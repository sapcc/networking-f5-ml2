# Copyright 2015 Cloudbase Solutions Srl
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
from neutron.i18n import _LI,_LW
import oslo_messaging
from oslo_service import loopingcall

from neutron.agent.common import polling
from neutron.common import config
from neutron.agent import rpc as agent_rpc
from neutron.agent import securitygroups_rpc as sg_rpc
from neutron.common import constants as n_const
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron import context
from neutron.i18n import _LE
from neutron.db import db_base_plugin_v2 as db_base
from neutron.plugins.ml2 import db as db_ml2

from networking_f5_ml2.plugins.ml2.drivers.mech_f5 import config as f5_config
from networking_f5_ml2.plugins.ml2.drivers.mech_f5 import constants as f5_constants
from networking_f5_ml2.plugins.ml2.drivers.mech_f5.agent import f5_firewall

from oslo_utils import importutils
import f5.oslbaasv1agent.drivers.bigip.constants as lbaasconstants

LOG = logging.getLogger(__name__)
CONF = cfg.CONF





class F5NeutronAgent(sg_rpc.SecurityGroupAgentRpcCallbackMixin):

    target = oslo_messaging.Target(version='1.4')

    def __init__(self,
                 minimize_polling=False,
                 quitting_rpc_timeout=None,
                 conf=None,
                 f5_monitor_respawn_interval=(
                    f5_constants.DEFAULT_F5_RESPAWN)):

        super(F5NeutronAgent, self).__init__()

        self.f5_monitor_respawn_interval = f5_monitor_respawn_interval

        self.conf = conf or cfg.CONF

        LOG.debug("***** conf")
        if conf:
            conf.log_opt_values(LOG,logging.DEBUG)

        LOG.debug("***** cfg.conf")
        if cfg.CONF:
            cfg.CONF.log_opt_values(LOG,logging.DEBUG)


        self.f5_config = f5_config.CONF

        self.agent_conf = self.conf.get('AGENT', {})
        self.security_conf = self.conf.get('SECURITYGROUP', {})

        self.minimize_polling=minimize_polling,
        self.polling_interval=10
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


        self.enable_security_groups = self.security_conf.get('enable_security_group', False)

        self.local_vlan_map = {}

        self.f5_driver = importutils.import_object(self.conf.f5_bigip_lbaas_device_driver, self.conf)


        host = self.conf.host

        self.agent_host = host + ":" + self.f5_driver.agent_id
        self.f5_driver.agent_host = self.agent_host

        self.agent_id = 'f5-agent-%s' % host

        self.setup_rpc()
        self.db = db_base.NeutronDbPluginV2()

        # Security group agent support
        if self.enable_security_groups:
            self.sg_agent = sg_rpc.SecurityGroupAgentRpc(self.context,
                    self.sg_plugin_rpc, self.local_vlan_map,
                    defer_refresh_firewall=True)

        self.agent_state = {
        'binary': 'neutron-f5-agent',
        'host': self.agent_host,
        'topic': n_const.L2_AGENT_TOPIC,
        'configurations': {},
        'agent_type': f5_constants.F5_AGENT_TYPE,
        'start_flag': True}




        self.connection.consume_in_threads()




    def port_update(self, context,  **kwargs):
        port = kwargs.get('port')
        self.updated_ports.add(port['id'])

        if self.enable_security_groups:
            if 'security_groups' in port:
                self.sg_agent.refresh_firewall()

        LOG.info(_LI("Agent port_update for port %s", port['id']))

    def port_delete(self, context, **kwargs):
        port_id = kwargs.get('port_id')
        self.deleted_ports.add(port_id)
        self.updated_ports.discard(port_id)

        LOG.info(_LI("Agent port_delete for port {}".format(port_id)))

    def network_create(self, context, **kwargs):
        LOG.info(_LI("Agent network_create"))

    def network_update(self, context, **kwargs):
        network_id = kwargs['network']['id']
        for port_id in self.network_ports[network_id]:
            # notifications could arrive out of order, if the port is deleted
            # we don't want to update it anymore
            if port_id not in self.deleted_ports:
                self.updated_ports.add(port_id)
        LOG.debug("Agent network_update for network "
                  "%(network_id)s, with ports: %(ports)s",
                  {'network_id': network_id,
                   'ports': self.network_ports[network_id]})

    def network_delete(self, context, **kwargs):
        LOG.info(_LI("Agent network_delete"))

    def _clean_network_ports(self, port_id):
        for port_set in self.network_ports.values():
            if port_id in port_set:
                port_set.remove(port_id)
                break


    def setup_rpc(self):

        LOG.info(_LI("RPC agent_id: %s"), self.agent_id)

        self.plugin_rpc = agent_rpc.PluginApi(topics.PLUGIN)
        self.sg_plugin_rpc = sg_rpc.SecurityGroupServerRpcApi(topics.PLUGIN)
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
                     [topics.NETWORK, topics.DELETE],
                     [topics.SECURITY_GROUP, topics.UPDATE]]

        self.connection = agent_rpc.create_consumers([self],
                                                     topics.AGENT,
                                                     consumers,
                                                     start_listening=False)


        report_interval = 30 #self.conf.AGENT.report_interval
        heartbeat = loopingcall.FixedIntervalLoopingCall(self._report_state)
        heartbeat.start(interval=report_interval)

    def _report_state(self):
        LOG.info(_LI("******** Reporting state via rpc"))


        try:
            self.state_rpc.report_state(self.context,
                                        self.agent_state)

            self.agent_state.pop('start_flag', None)
            LOG.info(_LI("******** Reporting state completed"))
        except Exception:
            LOG.info(_LI("******** Reporting state via rpc failed "))
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
            LOG.debug('Full set of CONF:')
            self.conf.log_opt_values(LOG, logging.DEBUG)
            self.catch_sighup = False
        return self.run_daemon_loop

    def _handle_sigterm(self, signum, frame):
        self.catch_sigterm = True
        if self.quitting_rpc_timeout:
            self.set_rpc_timeout(self.quitting_rpc_timeout)

    def _handle_sighup(self, signum, frame):
        self.catch_sighup = True

    @staticmethod
    def _to_list_of_neutron_ports(ports):
        neutron_ports = set()

        for mac,port in ports.iteritems():
            neutron_ports.add(port['neutron_info']['port_id'])

        return neutron_ports

    @staticmethod
    def _to_list_of_macs(ports):

        return ports.keys()

    @staticmethod
    def _unbound_ports(ports):
       unbound_ports = {}

       for port in ports:
            LOG.info("******** unbound")
            LOG.info(port)

            #if "neutron_info" in port and port["connected_vlan"] != port['neutron_info']['segmentation_id']:
            #    unbound_ports[mac] = port

       return unbound_ports

    def _scan_ports(self):
        start = time.clock()

        # For now just get ports assigned to this host, we will then check for the corresponding VLAN config on the device
        # May not scale but should prove concept works, we are also using dirct DB calls rather than RPC, I suspect this
        # is an anti pattern and done properly we should extend the RPC API to allow us to scan the LB ports
        ports = {'bound' : [],'unbound' : []}

        all_ports = self.db.get_ports(self.context_with_session,filters={'host' : self.agent_host})

        for bigip in self.f5_driver.get_config_bigips():
            vlans = bigip.vlan.get_vlans(folder='/')
            LOG.info("******** VLANs from F5")
            LOG.info(vlans)
            for vlan in vlans:
                tag = bigip.vlan.get_id(vlan,folder='/')
                LOG.info("******** VLAN tag ")
                LOG.info(vlan)
                LOG.info(tag)



        for port in all_ports:

            binding_levels = db_ml2.get_binding_levels(self.context_with_session.session,port['id'],self.agent_host)



            for binding_level in binding_levels:
                # if segment bound with ml2f5 driver
                if binding_level.driver == 'f5ml2':
                    segment = db_ml2.get_segment_by_id(self.context_with_session.session,binding_level.segment_id)
                    if segment['network_type'] == 'vlan':
                        LOG.info("******** The VLAN in F5 for this segment needs checking")
                        LOG.info(segment)

                    # and type is VLAN
                    # Get VLAN from iControl for port network and check its bound on this host to the correct VLAN



            ports['bound'].append(port)

        LOG.info(_LI("Scan ports completed in {} seconds".format(time.clock()-start)))

        return ports


    def _bind_ports(self, added_ports):

        devices_up = []
        devices_down = []

        for mac,port in added_ports.iteritems():
            if port["connected_vlan"] != port['neutron_info']['segmentation_id']:

                LOG.info("Preparing to bind port {} to VLAN {}".format(port['neutron_info']['port_id'],port['neutron_info']['segmentation_id']))

                try:


                    #TODO call to F5 to set port vlan


                    devices_up.append(port['neutron_info']['port_id'])
                except Exception:
                    devices_down.append(port['neutron_info']['port_id'])

        LOG.info("Updating ports up {} down {} agent {} host {}".format(devices_up,devices_down, self.agent_id,cfg.CONF.host))

        result = self.plugin_rpc.update_device_list(self.context, devices_up, devices_down, self.agent_id,cfg.CONF.host)

        LOG.info("Updated ports result".format(result))

        # update firewall agent if we have addded or updated ports
        if self.updated_ports or added_ports:
            self.sg_agent.setup_port_filters(F5NeutronAgent._to_list_of_neutron_ports(added_ports), self.updated_ports)

        # clear updated ports
        self.updated_ports.clear()


    def _unbind_ports(self, ports):
        # Nothing really to do on the VCenter - we let the vcenter unplug - so all we need to do is
        # trigger the firewall update and clear the deleted ports list
        self.sg_agent.remove_devices_filter(ports)
        self.deleted_ports.clear()

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

    def rpc_loop(self, polling_manager=None):
        if not polling_manager: polling_manager = polling.get_polling_manager(minimize_polling=False)

        while self._check_and_handle_signal():
            start = time.time()
            port_stats = {}
            try:


                # Get current ports known on the VMWare intergration bridge
                ports = self._scan_ports()

                added_ports = F5NeutronAgent._unbound_ports(ports)

                port_stats = {"added":len(added_ports), "updated":len(self.updated_ports), "deleted":len(self.deleted_ports)}

                self._bind_ports(added_ports)

                # Remove deleted ports
                #TODO : updated/deleted ports can be updated via the callback

                self._unbind_ports(self.deleted_ports)

            except Exception:
                LOG.exception(_LE("Error while processing ports"))

            polling_manager.polling_completed()
            self.loop_count_and_wait(start,port_stats)

    def daemon_loop(self):
        # Start everything.
        LOG.info(_LI("Agent initialized successfully, now running... "))
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, self._handle_sighup)
        with polling.get_polling_manager(
            self.minimize_polling,
            self.f5_monitor_respawn_interval) as pm:

            self.rpc_loop(polling_manager=pm)
