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

from neutron_lib.plugins.ml2 import api
from neutron_lib.api.definitions import portbindings
from neutron_lib import constants as p_constants
from networking_f5_ml2._i18n import _LI


from neutron.plugins.ml2.drivers import mech_agent
from oslo_log import log
from oslo_config import cfg
from networking_f5_ml2.plugins.ml2.drivers.mech_f5 import constants as f5_constants

LOG = log.getLogger(__name__)

cfg.CONF.import_group('ml2_f5',
                      'networking_f5_ml2.plugins.ml2.drivers.mech_f5.config')


class F5MechanismDriver(mech_agent.SimpleAgentMechanismDriverBase):
    """Binds ports used by the F5 driver.
    """

    def __init__(self):
        LOG.info(_LI("F5 ML2 mechanism driver initializing..."))
        self.agent_type = f5_constants.F5_AGENT_TYPE
        self.vif_type = f5_constants.VIF_TYPE_F5
        self.vif_details = {portbindings.CAP_PORT_FILTER: False}
        self.physical_networks = cfg.CONF.ml2_f5.physical_networks

        super(F5MechanismDriver, self).__init__(
                self.agent_type,
                self.vif_type,
                self.vif_details)

        LOG.info(_LI("F5 ML2 mechanism driver initialized..."))

    def get_allowed_network_types(self, agent):
        return ([p_constants.TYPE_VLAN])

    def get_mappings(self, agent):
        return agent['configurations'].get('network_maps', {'default': 'default'})

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        LOG.info(_LI("try_to_bind_segment_for_agent"))

        if segment[api.PHYSICAL_NETWORK] in self.physical_networks:

            if self.check_segment_for_agent(segment, agent):
                context.set_binding(segment[api.ID],
                                    self.vif_type,
                                    self.vif_details)
                return True

        return False

    def check_segment_for_agent(self, segment, agent):
        LOG.info(_LI("Checking segment for agent " + str(agent) + " " + str(agent['agent_type'])))
        return agent['agent_type'] == f5_constants.F5_AGENT_TYPE
