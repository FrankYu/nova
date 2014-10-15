# Copyright (c) 2014 IBM Corp.
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

import mock
import mox
from oslo.config import cfg
from oslo.serialization import jsonutils
from webob import exc

from nova.api.openstack.compute import extensions
from nova.api.openstack.compute import plugins
from nova.api.openstack.compute.plugins.v3 import block_device_mapping_v1 as \
    block_device_mapping
from nova.api.openstack.compute.plugins.v3 import servers as servers_v3
from nova.api.openstack.compute import servers as servers_v2
from nova.compute import api as compute_api
from nova import exception
from nova import test
from nova.tests.api.openstack import fakes
from nova.tests.image import fake

CONF = cfg.CONF


class BlockDeviceMappingTestV21(test.TestCase):

    def _setup_controller(self):
        ext_info = plugins.LoadedExtensionInfo()
        CONF.set_override('extensions_blacklist', 'os-block-device-mapping',
                          'osapi_v3')
        self.controller = servers_v3.ServersController(extension_info=ext_info)
        CONF.set_override('extensions_blacklist',
                          ['os-block-device-mapping-v1',
                           'os-block-device-mapping'],
                          'osapi_v3')
        self.no_volumes_controller = servers_v3.ServersController(
                extension_info=ext_info)
        CONF.set_override('extensions_blacklist', '', 'osapi_v3')

    def setUp(self):
        super(BlockDeviceMappingTestV21, self).setUp()
        self._setup_controller()
        fake.stub_out_image_service(self.stubs)
        self.volume_id = fakes.FAKE_UUID
        self.bdm = [{
            'id': 1,
            'no_device': None,
            'virtual_name': None,
            'snapshot_id': None,
            'volume_id': self.volume_id,
            'status': 'active',
            'device_name': 'vda',
            'delete_on_termination': False,
            'volume_image_metadata':
                {'test_key': 'test_value'}
        }]

    def _get_servers_body(self, no_image=False):
        body = {
            'server': {
                'name': 'server_test',
                'imageRef': '76fa36fc-c930-4bf3-8c8a-ea2a2420deb6',
                'flavorRef': 'http://localhost/123/flavors/3',
                'metadata': {
                    'hello': 'world',
                    'open': 'stack',
                },
            },
        }
        if no_image:
            del body['server']['imageRef']
        return body

    def _test_create(self, params, no_image=False, override_controller=None):
        body = self._get_servers_body(no_image)
        body['server'].update(params)

        req = fakes.HTTPRequest.blank('/v2/fake/servers')
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        req.body = jsonutils.dumps(body)

        if override_controller:
            override_controller.create(req, body=body).obj['server']
        else:
            self.controller.create(req, body=body).obj['server']

    def test_create_instance_with_volumes_enabled(self):
        params = {'block_device_mapping': self.bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            return old_create(*args, **kwargs)

        def _validate_bdm(*args, **kwargs):
            pass

        self.stubs.Set(compute_api.API, 'create', create)
        self.stubs.Set(compute_api.API, '_validate_bdm', _validate_bdm)
        self._test_create(params)

    def test_create_instance_with_volumes_enabled_and_bdms_no_image(self):
        """Test that the create works if there is no image supplied but
        os-volumes extension is enabled and bdms are supplied
        """
        self.mox.StubOutWithMock(compute_api.API, '_validate_bdm')
        self.mox.StubOutWithMock(compute_api.API, '_get_bdm_image_metadata')
        volume = self.bdm[0]
        compute_api.API._validate_bdm(mox.IgnoreArg(),
                mox.IgnoreArg(), mox.IgnoreArg(),
                mox.IgnoreArg()).AndReturn(True)
        compute_api.API._get_bdm_image_metadata(mox.IgnoreArg(),
                                                self.bdm,
                                                True).AndReturn(volume)
        params = {'block_device_mapping': self.bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            self.assertNotIn('imageRef', kwargs)
            return old_create(*args, **kwargs)

        def _validate_bdm(*args, **kwargs):
            pass

        self.stubs.Set(compute_api.API, 'create', create)
        self.mox.ReplayAll()
        self._test_create(params, no_image=True)

    def test_create_instance_with_volumes_disabled(self):
        bdm = [{'device_name': 'foo'}]
        params = {'block_device_mapping': bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertNotIn(block_device_mapping, kwargs)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self._test_create(params,
                          override_controller=self.no_volumes_controller)

    @mock.patch('nova.compute.api.API._get_bdm_image_metadata')
    def test_create_instance_non_bootable_volume_fails(self, fake_bdm_meta):
        bdm = [{
            'id': 1,
            'bootable': False,
            'volume_id': self.volume_id,
            'status': 'active',
            'device_name': 'vda',
        }]
        params = {'block_device_mapping': bdm}
        fake_bdm_meta.side_effect = exception.InvalidBDMVolumeNotBootable(id=1)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, params, no_image=True)

    def test_create_instance_with_device_name_not_string(self):
        old_create = compute_api.API.create
        self.params = {'block_device_mapping': self.bdm}

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, self.params)

    def test_create_instance_with_device_name_empty(self):
        self.bdm[0]['device_name'] = ''
        params = {'block_device_mapping': self.bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, params)

    def test_create_instance_with_device_name_too_long(self):
        self.bdm[0]['device_name'] = 'a' * 256,
        params = {'block_device_mapping': self.bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, params)

    def test_create_instance_with_space_in_device_name(self):
        self.bdm[0]['device_name'] = 'vd a',
        params = {'block_device_mapping': self.bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertTrue(kwargs['legacy_bdm'])
            self.assertEqual(kwargs['block_device_mapping'], self.bdm)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, params)

    def test_create_instance_with_invalid_size(self):
        bdm = [{'delete_on_termination': 1,
                'device_name': 'vda',
                'volume_size': "hello world",
                'volume_id': '11111111-1111-1111-1111-111111111111'}]
        params = {'block_device_mapping': bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(kwargs['block_device_mapping'], bdm)
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self.assertRaises(exc.HTTPBadRequest,
                          self._test_create, params)

    def test_create_instance_with_bdm_delete_on_termination(self):
        bdm = [{'device_name': 'foo1', 'volume_id': 'fake_vol',
                'delete_on_termination': 1},
               {'device_name': 'foo2', 'volume_id': 'fake_vol',
                'delete_on_termination': True},
               {'device_name': 'foo3', 'volume_id': 'fake_vol',
                'delete_on_termination': 'invalid'},
               {'device_name': 'foo4', 'volume_id': 'fake_vol',
                'delete_on_termination': 0},
               {'device_name': 'foo5', 'volume_id': 'fake_vol',
                'delete_on_termination': False}]
        expected_bdm = [
            {'device_name': 'foo1', 'volume_id': 'fake_vol',
             'delete_on_termination': True},
            {'device_name': 'foo2', 'volume_id': 'fake_vol',
             'delete_on_termination': True},
            {'device_name': 'foo3', 'volume_id': 'fake_vol',
             'delete_on_termination': False},
            {'device_name': 'foo4', 'volume_id': 'fake_vol',
             'delete_on_termination': False},
            {'device_name': 'foo5', 'volume_id': 'fake_vol',
             'delete_on_termination': False}]
        params = {'block_device_mapping': bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertEqual(expected_bdm, kwargs['block_device_mapping'])
            return old_create(*args, **kwargs)

        def _validate_bdm(*args, **kwargs):
            pass

        self.stubs.Set(compute_api.API, 'create', create)
        self.stubs.Set(compute_api.API, '_validate_bdm', _validate_bdm)
        self._test_create(params)

    def test_create_instance_decide_format_legacy(self):
        ext_info = plugins.LoadedExtensionInfo()
        CONF.set_override('extensions_blacklist',
                          ['os-block-device-mapping',
                           'os-block-device-mapping-v1'],
                          'osapi_v3')
        controller = servers_v3.ServersController(extension_info=ext_info)
        bdm = [{'device_name': 'foo1',
                'volume_id': 'fake_vol',
                'delete_on_termination': 1}]

        expected_legacy_flag = True

        old_create = compute_api.API.create

        def create(*args, **kwargs):
            legacy_bdm = kwargs.get('legacy_bdm', True)
            self.assertEqual(legacy_bdm, expected_legacy_flag)
            return old_create(*args, **kwargs)

        def _validate_bdm(*args, **kwargs):
            pass

        self.stubs.Set(compute_api.API, 'create', create)
        self.stubs.Set(compute_api.API, '_validate_bdm',
                       _validate_bdm)

        self._test_create({}, override_controller=controller)

        params = {'block_device_mapping': bdm}
        self._test_create(params, override_controller=controller)

    def test_create_instance_both_bdm_formats(self):
        bdm = [{'device_name': 'foo'}]
        bdm_v2 = [{'source_type': 'volume',
                   'uuid': 'fake_vol'}]
        params = {'block_device_mapping': bdm,
                  'block_device_mapping_v2': bdm_v2}
        self.assertRaises(exc.HTTPBadRequest, self._test_create, params)


class BlockDeviceMappingTestV2(BlockDeviceMappingTestV21):

    def _setup_controller(self):
        self.ext_mgr = extensions.ExtensionManager()
        self.ext_mgr.extensions = {'os-volumes': 'fake'}
        self.controller = servers_v2.Controller(self.ext_mgr)
        self.ext_mgr_no_vols = extensions.ExtensionManager()
        self.ext_mgr_no_vols.extensions = {}
        self.no_volumes_controller = servers_v2.Controller(
            self.ext_mgr_no_vols)

    def test_create_instance_with_volumes_disabled(self):
        bdm = [{'device_name': 'foo'}]
        params = {'block_device_mapping': bdm}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertIsNone(kwargs['block_device_mapping'])
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        self._test_create(params,
                          override_controller=self.no_volumes_controller)

    def test_create_instance_decide_format_legacy(self):
        ext_mgr = extensions.ExtensionManager()
        ext_mgr.extensions = {'os-volumes': 'fake',
                              'os-block-device-mapping-v2-boot': 'fake'}
        controller = servers_v2.Controller(self.ext_mgr)
        bdm = [{'device_name': 'foo1',
                'volume_id': 'fake_vol',
                'delete_on_termination': 1}]

        expected_legacy_flag = True

        old_create = compute_api.API.create

        def create(*args, **kwargs):
            legacy_bdm = kwargs.get('legacy_bdm', True)
            self.assertEqual(legacy_bdm, expected_legacy_flag)
            return old_create(*args, **kwargs)

        def _validate_bdm(*args, **kwargs):
            pass

        self.stubs.Set(compute_api.API, 'create', create)
        self.stubs.Set(compute_api.API, '_validate_bdm',
                       _validate_bdm)

        self._test_create({}, override_controller=controller)

        params = {'block_device_mapping': bdm}
        self._test_create(params, override_controller=controller)


class TestServerCreateRequestXMLDeserializer(test.TestCase):

    def setUp(self):
        super(TestServerCreateRequestXMLDeserializer, self).setUp()
        self.deserializer = servers_v2.CreateDeserializer()

    def test_request_with_block_device_mapping(self):
        serial_request = """
    <server xmlns="http://docs.openstack.org/compute/api/v2"
     name="new-server-test" imageRef="1" flavorRef="1">
       <block_device_mapping>
         <mapping volume_id="7329b667-50c7-46a6-b913-cb2a09dfeee0"
          device_name="/dev/vda" virtual_name="root"
          delete_on_termination="False" />
         <mapping snapshot_id="f31efb24-34d2-43e1-8b44-316052956a39"
          device_name="/dev/vdb" virtual_name="ephemeral0"
          delete_on_termination="False" />
         <mapping device_name="/dev/vdc" no_device="True" />
       </block_device_mapping>
    </server>"""
        request = self.deserializer.deserialize(serial_request)
        expected = {"server": {
                "name": "new-server-test",
                "imageRef": "1",
                "flavorRef": "1",
                "block_device_mapping": [
                    {
                        "volume_id": "7329b667-50c7-46a6-b913-cb2a09dfeee0",
                        "device_name": "/dev/vda",
                        "virtual_name": "root",
                        "delete_on_termination": False,
                    },
                    {
                        "snapshot_id": "f31efb24-34d2-43e1-8b44-316052956a39",
                        "device_name": "/dev/vdb",
                        "virtual_name": "ephemeral0",
                        "delete_on_termination": False,
                    },
                    {
                        "device_name": "/dev/vdc",
                        "no_device": True,
                    },
                ]
                }}
        self.assertEqual(request['body'], expected)