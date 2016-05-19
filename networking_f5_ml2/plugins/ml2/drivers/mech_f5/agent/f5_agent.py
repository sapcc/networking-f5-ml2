# Copyright 2016 SAP SE
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import signal
import time

import eventlet

eventlet.monkey_patch()

from oslo_config import cfg
from oslo_log import log as logging
from neutron.i18n import _LI, _LW
import oslo_messaging
from oslo_service import loopingcall

from neutron.agent.common import polling
from neutron.common import config
from neutron.agent import rpc as agent_rpc
from neutron.common import constants as n_const
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron import context
from neutron.i18n import _LE
from neutron.db import db_base_plugin_v2 as db_base
from neutron.plugins.ml2 import db as db_ml2


from networking_f5_ml2.plugins.ml2.drivers.mech_f5 import constants as f5_constants

from oslo_utils import importutils

LOG = logging.getLogger(__name__)

cfg.CONF.import_group('ml2_f5',
                      'networking_f5_ml2.plugins.ml2.drivers.mech_f5.config')



class F5NeutronAgent():
    target = oslo_messaging.Target(version='1.4')

    def __init__(self,
                 quitting_rpc_timeout=None,
                 conf=None, ):

        self.conf = cfg.CONF

        cfg.CONF.log_opt_values(LOG, logging.DEBUG)

        self.agent_conf = self.conf.get('AGENT', {})
        self.polling_interval = 10
        self.iter_num = 0
        self.run_daemon_loop = True
        self.quitting_rpc_timeout = quitting_rpc_timeout
        self.catch_sigterm = False
        self.catch_sighup = False

        # Stores port update notifications for processing in main rpc loop
        self.updated_ports = set()
        # Stores port delete notifications
        self.deleted_ports = set()

        self.network_ports = collections.defaultdict(set)

        self.local_vlan_map = {}

        self.f5_driver = importutils.import_object(cfg.CONF.f5_bigip_lbaas_device_driver, cfg.CONF)

        host = self.conf.host

        self.agent_host = host + ":" + self.f5_driver.agent_id

        self.f5_driver.agent_host = self.agent_host

        self.agent_id = 'f5-agent-%s' % host

        self.setup_rpc()
        self.db = db_base.NeutronDbPluginV2()

        self.agent_state = {
            'binary': 'neutron-f5-agent',
            'host': self.agent_host,
            'topic': n_const.L2_AGENT_TOPIC,
            'configurations': {},
            'agent_type': f5_constants.F5_AGENT_TYPE,
            'start_flag': True}

        self.connection.consume_in_threads()

    def port_update(self, context, **kwargs):
        port = kwargs.get('port')
        self.updated_ports.add(port['id'])

    def port_delete(self, context, **kwargs):
        port_id = kwargs.get('port_id')
        self.deleted_ports.add(port_id)
        self.updated_ports.discard(port_id)

    def network_create(self, context, **kwargs):
        pass

    def network_update(self, context, **kwargs):
        network_id = kwargs['network']['id']
        for port_id in self.network_ports[network_id]:
            # notifications could arrive out of order, if the port is deleted
            # we don't want to update it anymore
            if port_id not in self.deleted_ports:
                self.updated_ports.add(port_id)

    def network_delete(self, context, **kwargs):
        pass

    def _clean_network_ports(self, port_id):
        for port_set in self.network_ports.values():
            if port_id in port_set:
                port_set.remove(port_id)
                break

    def setup_rpc(self):

        LOG.info(_LI("RPC agent_id: %s"), self.agent_id)

        self.plugin_rpc = agent_rpc.PluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        # RPC network init
        self.context = context.get_admin_context_without_session()
        self.context_with_session = context.get_admin_context()

        # Define the listening consumers for the agent
        consumers = [[topics.PORT, topics.CREATE],
                     [topics.PORT, topics.UPDATE],
                     [topics.PORT, topics.DELETE],
                     [topics.NETWORK, topics.CREATE],
                     [topics.NETWORK, topics.UPDATE],
                     [topics.NETWORK, topics.DELETE]]

        self.connection = agent_rpc.create_consumers([self],
                                                     topics.AGENT,
                                                     consumers,
                                                     start_listening=False)

        report_interval = 30  # self.conf.AGENT.report_interval
        heartbeat = loopingcall.FixedIntervalLoopingCall(self._report_state)
        heartbeat.start(interval=report_interval)

    def _report_state(self):
        LOG.info(_LI("******** Reporting state via rpc"))
        try:
            self.state_rpc.report_state(self.context,
                                        self.agent_state)

            self.agent_state.pop('start_flag', None)

        except Exception:

            LOG.exception(_LE("Failed reporting state!"))

    def _check_and_handle_signal(self):
        if self.catch_sigterm:
            LOG.info(_LI("Agent caught SIGTERM, quitting daemon loop."))
            self.run_daemon_loop = False
            self.catch_sigterm = False
        if self.catch_sighup:
            LOG.info(_LI("Agent caught SIGHUP, resetting."))
            self.conf.reload_config_files()
            config.setup_logging()
            self.conf.log_opt_values(LOG, logging.DEBUG)
            self.catch_sighup = False
        return self.run_daemon_loop

    def _handle_sigterm(self, signum, frame):
        self.catch_sigterm = True
        if self.quitting_rpc_timeout:
            self.set_rpc_timeout(self.quitting_rpc_timeout)

    def _handle_sighup(self, signum, frame):
        self.catch_sighup = True

    def _scan_ports(self):
        start = time.clock()

        # For now get all ports, we will then check for the corresponding VLAN config on the device
        # Will not scale but should prove concept works, we are also using direct DB calls rather than RPC, I suspect this
        # is an anti pattern and done properly we should extend the RPC API to allow us to scan the LB ports


        all_ports = self.db.get_ports(self.context_with_session, filters={})

        for port in all_ports:
            LOG.info( port)
            LOG.info(_LI("Agent port scan for port {}".format(port['id'])))

            network = self.db.get_network(self.context_with_session, port['network_id'])
            binding_levels = db_ml2.get_binding_levels(self.context_with_session.session, port['id'], self.agent_host)
            for binding_level in binding_levels:
                LOG.info(_LI("Binding level {}".format(binding_level)))

                # if segment bound with ml2f5 driver
                if binding_level.driver == 'f5ml2':
                    segment = db_ml2.get_segment_by_id(self.context_with_session.session, binding_level.segment_id)
                    if segment['network_type'] == 'vlan':
                        # and type is VLAN
                        # Get VLANs from iControl for port network and check they are bound to the correct VLAN
                        for bigip in self.f5_driver.get_config_bigips():
                            folder = 'Project_' + network['tenant_id']
                            name = 'vlan-' + network['id'][0:10]

                            v = bigip.net.vlans.vlan
                            if v.exists(name=name, partition=folder):
                                v.load(name=name, partition=folder)
                                tag = v.tag
                                if tag != segment['segmentation_id']:
                                    # Update VLAN tag in case of mismatch
                                    LOG.info("Updating VLAN tag was %s needs to be %s", tag, segment['segmentation_id'])
                                    v.tag = segment['segmentation_id']
                                    v.update()

        LOG.info(_LI("Scan ports completed in {} seconds".format(time.clock() - start)))

    def loop_count_and_wait(self, start_time, port_stats):
        # sleep till end of polling interval
        elapsed = time.time() - start_time
        LOG.debug("F5 Agent rpc_loop - iteration:%(iter_num)d "
                  "completed. Processed ports statistics: "
                  "%(port_stats)s. Elapsed:%(elapsed).3f",
                  {'iter_num': self.iter_num,
                   'port_stats': port_stats,
                   'elapsed': elapsed})

        if elapsed < self.polling_interval:
            time.sleep(self.polling_interval - elapsed)
        else:
            LOG.debug("Loop iteration exceeded interval "
                      "(%(polling_interval)s vs. %(elapsed)s)!",
                      {'polling_interval': self.polling_interval,
                       'elapsed': elapsed})
        self.iter_num = self.iter_num + 1

    def rpc_loop(self, ):

        while self._check_and_handle_signal():
            start = time.time()
            port_stats = {}
            try:

                self._scan_ports()

            except Exception:
                LOG.exception(_LE("Error while processing ports"))

            self.loop_count_and_wait(start, port_stats)

    def daemon_loop(self):
        # Start everything.
        LOG.info(_LI("Agent initialized successfully, now running... "))
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, self._handle_sighup)
        self.rpc_loop()
