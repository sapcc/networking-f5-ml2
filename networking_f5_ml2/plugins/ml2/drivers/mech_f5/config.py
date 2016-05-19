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

from oslo_config import cfg

f5_opts = [

    cfg.StrOpt(
            'f5_bigip_lbaas_device_driver',
            default='f5.oslbaasv1agent.drivers.bigip.icontrol_driver.iControlDriver',
            help=_('LB driver')),
    cfg.BoolOpt(
            'f5_global_routed_mode',
            default=False,
            help=_('Global Routed Mode')),
    cfg.StrOpt(
            'environment_prefix',
            default='Project',
            help=_('Environment Prefix')),
]

f5_ml2_opts = [cfg.ListOpt('physical_networks', default=None,
                           help=_("List of pyhsical networks the driver should use to"
                                  "indentify the segment to use (if not specified"
                                  "driver will use first segment in the list)"))]

cfg.CONF.register_opts(f5_opts)
cfg.CONF.register_opts(f5_ml2_opts, 'ml2_f5')
CONF = cfg.CONF
CONF()
