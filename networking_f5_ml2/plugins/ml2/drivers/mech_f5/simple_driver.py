# Copyright 2016 SAP SE
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

from oslo_config import cfg
from oslo_log import log

import constants

LOG = log.getLogger(__name__)


cfg.CONF.import_group('ml2_f5',
                      'networking_f5_ml2.plugins.ml2.drivers.mech_f5.config')



class F5SimpleMechanismDriver(api.MechanismDriver):
    def __init__(self):
        LOG.info(_LI("F5 Simple mechanism driver initializing..."))
        self.agent_type = constants.F5_AGENT_TYPE
        self.vif_type = constants.VIF_TYPE_F5
        self.vif_details = {portbindings.CAP_PORT_FILTER: False}
        self.physical_networks = cfg.CONF.ml2_f5.physical_networks

        super(F5SimpleMechanismDriver, self).__init__()

        LOG.info(_LI("F5 simple mechanism driver initialized."))

    def initialize(self):
        pass

    def bind_port(self, context):
        device_owner = context.current['device_owner']
        if device_owner and device_owner == 'network:f5lbaasv2':
            # bind to first segment if no physical networks are configured
            if self.physical_networks is None:
                self._set_binding(context, context.segments_to_bind[0])
                return True

            # bind to first segment present in list of physical networks
            for segment in context.segments_to_bind:
                if segment[api.PHYSICAL_NETWORK] in self.physical_networks:
                    self._set_binding(context, segment)
                    return True

            LOG.error("No segment matches the configured physical networks "
                      "%(physical_networks)s",
                      {'physical_networks': self.physical_networks})
        return False

    def _set_binding(self, context, segment):
        context.set_binding(segment[api.ID],
                            self.vif_type,
                            self.vif_details,
                            p_constants.ACTIVE)
