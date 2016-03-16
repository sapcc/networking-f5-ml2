# Copyright 2014 IBM Corp.
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
        'icontrol_hostname',
        default='localhost',
        help=_('The address or hostname of vcenter device.')),
    cfg.StrOpt(
        'icontrol_username',
        default='administrator',
        help=_('The login username of f5 device.')),
    cfg.StrOpt(
        'icontrol_password',
        default='password',
        secret=True,
        help=_('The login password of f5 device.')),
    cfg.FloatOpt(
        'task_poll_interval',
        default=2,
        help=_('The interval of task polling.')),
    cfg.IntOpt(
        'api_retry_count',
        default=10,
        help=_('The retry count if api call fails.')),
    cfg.IntOpt(
        'f5_device_type',
        default='external',
        help=_('LB Device Type')),
    cfg.IntOpt(
        'f5_bigip_lbaas_device_driver',
        default='f5.oslbaasv1agent.drivers.bigip.icontrol_driver.iControlDriver',
        help=_('LB driver')),


]

cfg.CONF.register_opts(f5_opts, group='DEFAULT')
CONF = cfg.CONF
CONF()
