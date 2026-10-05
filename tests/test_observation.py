"""Host-only observation boundary tests. Temporary evidence is retained."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from hardsurface import contract as c
from hardsurface import observation as o
from hardsurface.io import RuntimeFailure

OBJECT_A = '00000000-0000-4000-8000-000000000001'
OBJECT_B = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def request():
    return {'schema_version': '1.0', 'command': 'hardsurface.observe', 'params': {
        'request_id': 'observation.fixture.1',
        'source': {'file': '/tmp/source.blend', 'expected_sha256': 'a' * 64},
        'views': [{'name': 'assembled', 'direction': 'three_quarter',
                   'visible_feature_ids': ['sample_plate', 'sample_cap'], 'explode_z_mm': {'sample_cap': 37}}],
    }}


class ObservationSchemaTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaises(c.ContractError):
            o.validate_request(value)

    def test_import_has_no_blender_or_optional_dependency(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; from hardsurface import observation; '
            'assert "bpy" not in sys.modules; assert "mathutils" not in sys.modules; '
            'assert "hardsurface.core" not in sys.modules; observation.schema()'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_defaults_and_input_immutability(self):
        source = request()
        original = copy.deepcopy(source)
        validated = o.validate_request(source)
        self.assertEqual(source, original)
        self.assertEqual(validated['params']['cpu_threads'], 2)
        self.assertEqual(validated['params']['wall_seconds'], 600)
        self.assertEqual(validated['params']['views'][0]['width'], 1024)
        self.assertEqual(validated['params']['views'][0]['height'], 1024)
        self.assertEqual(validated['params']['views'][0]['explode_z_mm'], {'sample_cap': 37})
        self.assertTrue(validated['params']['wire']['enabled'])
        self.assertEqual(validated['params']['views'][0]['mesh_state'], 'evaluated')

    def test_schema_is_independent_and_exposes_bounded_map(self):
        schema = o.schema()
        view = schema['properties']['params']['properties']['views']['items']['oneOf'][0]
        mapping = view['properties']['explode_z_mm']
        self.assertEqual(mapping['maxProperties'], 128)
        self.assertEqual(mapping['additionalProperties']['maximum'], 500)
        schema['properties']['command']['const'] = 'bad'
        self.assertEqual(o.schema()['properties']['command']['const'], 'hardsurface.observe')

    def test_json_duplicate_keys_and_nonfinite(self):
        raw = json.dumps(request())
        self.invalid(raw.replace('"schema_version": "1.0"', '"schema_version":"1.0","schema_version":"1.0"'))
        for number in (float('nan'), float('inf'), -float('inf'), 10**400):
            value = request()
            value['params']['views'][0]['explode_z_mm']['sample_cap'] = number
            self.invalid(value)
        self.invalid(raw.replace('37', 'NaN'))

    def test_unknown_fields_at_all_levels(self):
        for target in ('root', 'params', 'source', 'view'):
            value = request()
            node = {'root': value, 'params': value['params'],
                    'source': value['params']['source'], 'view': value['params']['views'][0]}[target]
            node['output_path'] = '/tmp/overwrite.blend'
            self.invalid(value)

    def test_thread_wall_pixel_and_explosion_bounds(self):
        for key, samples in {'cpu_threads': [0, 5, True, 1.1],
                             'wall_seconds': [0, -1, 600.01, True]}.items():
            for sample in samples:
                value = request()
                value['params'][key] = sample
                self.invalid(value)
        for key in ('width', 'height'):
            for sample in (0, 15, 2049, 16.1, True):
                value = request()
                value['params']['views'][0][key] = sample
                self.invalid(value)
        for sample in (-500.01, 500.01, True):
            value = request()
            value['params']['views'][0]['explode_z_mm']['sample_cap'] = sample
            self.invalid(value)
        for sample in (-500, 0, 500):
            value = request()
            value['params']['views'][0]['explode_z_mm']['sample_cap'] = sample
            o.validate_request(value)

    def test_source_paths_and_identity(self):
        for path in ('relative.blend', '/tmp/../source.blend', '/tmp/back\\slash.blend',
                     '/tmp/source.blend\x00', 'https://example/source.blend', '/tmp/source.png'):
            value = request()
            value['params']['source']['file'] = path
            self.invalid(value)
        value = request()
        value['params']['source']['expected_sha256'] = 'ABC'
        self.invalid(value)

    def test_names_traversal_duplicates_and_view_limits(self):
        for name in ('../bad', '/bad', '.', 'with.dot', 'has space', 'a' * 49, '', '中'):
            value = request()
            value['params']['views'][0]['name'] = name
            self.invalid(value)
        value = request()
        value['params']['views'] *= 2
        self.invalid(value)
        for count in (0, 17):
            value = request()
            value['params']['views'] = [{**copy.deepcopy(value['params']['views'][0]), 'name': 'view' + str(i)} for i in range(count)]
            self.invalid(value)
        value = request()
        value['params']['views'] = [{**copy.deepcopy(value['params']['views'][0]), 'name': 'view' + str(i)} for i in range(16)]
        self.assertEqual(len(o.validate_request(value)['params']['views']), 16)

    def test_feature_ids_are_exact_and_explosion_requires_visible_target(self):
        for ids in ([], ['sample_plate', 'sample_plate'], ['sample_pin_*'], ['sample_plate/sample_cap']):
            value = request()
            value['params']['views'][0].update(visible_feature_ids=ids, explode_z_mm={})
            self.invalid(value)
        value = request()
        value['params']['views'][0]['explode_z_mm']['hidden'] = 100
        self.invalid(value)
        value['params']['views'][0]['explode_z_mm'] = {'bad/id': 100}
        self.invalid(value)

    def test_object_selector_uuid_keys_and_both_camera_modes(self):
        value = request()
        view = value['params']['views'][0]
        del view['visible_feature_ids']
        view.update(visible_object_ids=[OBJECT_A, OBJECT_B],
                    explode_z_mm={OBJECT_A: 25, OBJECT_B: -25})
        normalized = o.validate_request(value)['params']['views'][0]
        self.assertEqual(normalized['visible_object_ids'], [OBJECT_A, OBJECT_B])
        self.assertEqual(normalized['explode_z_mm'], {OBJECT_A: 25, OBJECT_B: -25})
        del view['direction']
        view['camera'] = {'position_mm': [100, -200, 300], 'target_mm': [0, 0, 0],
                          'up_axis': 'Z', 'ortho_scale_mm': 800}
        self.assertIn('camera', o.validate_request(value)['params']['views'][0])

    def test_object_selectors_are_exact_mutually_exclusive_and_bounded(self):
        value = request()
        view = value['params']['views'][0]
        view['visible_object_ids'] = [OBJECT_A]
        self.invalid(value)
        del view['visible_feature_ids']
        view['explode_z_mm'] = {}
        for ids in ([], [OBJECT_A, OBJECT_A], ['sample_plate'], ['*'], ['/tmp/source.blend'],
                    ['../escape'], ['00000000-0000-0000-0000-000000000001'], [OBJECT_B.upper()]):
            changed = copy.deepcopy(value)
            changed['params']['views'][0]['visible_object_ids'] = ids
            self.invalid(changed)
        for mapping in ({OBJECT_B: 10}, {'sample_plate': 10}, {'bad/id': 10}, {'../escape': 10}):
            changed = copy.deepcopy(value)
            changed['params']['views'][0]['explode_z_mm'] = mapping
            self.invalid(changed)
        del view['visible_object_ids']
        self.invalid(value)

    def test_mixed_selector_views_and_mesh_state_overrides(self):
        value = request()
        value['params']['wire'] = {'mesh_state': 'control'}
        value['params']['views'].append({'name': 'one_instance', 'direction': 'top',
                                       'visible_object_ids': [OBJECT_B], 'mesh_state': 'evaluated'})
        views = o.validate_request(value)['params']['views']
        self.assertEqual([view['mesh_state'] for view in views], ['control', 'evaluated'])
        self.assertEqual(views[1]['explode_z_mm'], {})

    def test_camera_modes_are_exclusive_and_explicit_camera_bounded(self):
        value = request()
        view = value['params']['views'][0]
        camera = {'position_mm': [100, -200, 300], 'target_mm': [0, 0, 0],
                  'up_axis': 'Z', 'ortho_scale_mm': 800}
        view['camera'] = camera
        self.invalid(value)
        del view['direction']
        self.assertEqual(o.validate_request(value)['params']['views'][0]['camera']['up_axis'], 'Z')
        for attr, sample in [('up_axis', 'Y'), ('ortho_scale_mm', 0), ('ortho_scale_mm', 100001),
                             ('position_mm', [0, 0, 0]), ('position_mm', [0, 0, 1]),
                             ('position_mm', [100001, 0, 0]), ('target_mm', [0, 0]),
                             ('target_mm', [float('nan'), 0, 0])]:
            changed = copy.deepcopy(value)
            changed['params']['views'][0]['camera'][attr] = sample
            self.invalid(changed)
        changed = copy.deepcopy(value)
        changed['params']['views'][0]['camera']['lens'] = 45
        self.invalid(changed)
        del view['camera']
        self.invalid(value)

    def test_request_identity_required_and_bounded(self):
        for rid in ('', '../escape', 'a' * 129, 1):
            value = request()
            value['params']['request_id'] = rid
            self.invalid(value)
        value = request()
        del value['params']['request_id']
        self.invalid(value)


class FakeObject:
    def __init__(self, fid, name='test', typ='MESH', hidden=False, oid=OBJECT_A, data=None):
        self.fid, self.name, self.type = fid, name, typ
        self.oid, self.hide_render, self.hide_viewport = oid, hidden, False
        self.data = data
        self.users_collection = []

    def get(self, key):
        return {'hs_feature_id': self.fid, 'hs_object_id': self.oid}.get(key)


class ObservationRuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hs-observe-test-', dir='/tmp'))

    def test_feature_resolution_rejects_unknown_and_nonmesh(self):
        views = [{'visible_feature_ids': ['sample_plate']}]
        obj = FakeObject('sample_plate')
        self.assertIs(o._resolve_features([obj, FakeObject(None, oid=OBJECT_B)], views)[OBJECT_A], obj)
        self.assertIs(o._resolve_features([obj, FakeObject('sample_plate', hidden=True, oid=OBJECT_B)], views)[OBJECT_A], obj)
        for objects in ([], [FakeObject('sample_plate.other')],
                        [FakeObject('sample_plate', typ='CAMERA')], [FakeObject('sample_plate', hidden=True)]):
            with self.assertRaises(RuntimeFailure):
                o._resolve_features(objects, views)

    def test_feature_expands_distinct_instances_and_shared_mesh_is_not_collapsed(self):
        data = SimpleNamespace(name='shared pattern mesh', users=2,
                               get=lambda key: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
        first = FakeObject('pattern', 'first', oid=OBJECT_A, data=data)
        second = FakeObject('pattern', 'second', oid=OBJECT_B, data=data)
        view = {'visible_feature_ids': ['pattern'], 'explode_z_mm': {'pattern': 160}}
        selected = o._resolve_features([second, first], [view])
        self.assertEqual(list(selected), [OBJECT_A, OBJECT_B])
        self.assertIs(selected[OBJECT_A].data, selected[OBJECT_B].data)
        visible = o._view_objects(selected, view)
        self.assertEqual(list(visible), [OBJECT_A, OBJECT_B])
        self.assertEqual([o._selection_key(view, obj) for obj in visible.values()], ['pattern', 'pattern'])
        self.assertEqual([view['explode_z_mm'][o._selection_key(view, obj)] for obj in visible.values()], [160, 160])
        identities = [o._identity(obj) for obj in visible.values()]
        self.assertEqual([identity['object_id'] for identity in identities], [OBJECT_A, OBJECT_B])
        self.assertEqual([identity['feature_id'] for identity in identities], ['pattern', 'pattern'])
        self.assertEqual([identity['data_users'] for identity in identities], [2, 2])
        second.data = SimpleNamespace(name='unique instance mesh', users=1, get=lambda key: None)
        self.assertEqual(len(o._resolve_features([first, second], [view])), 2)

    def test_object_selector_is_exact_even_when_feature_and_mesh_are_shared(self):
        shared = object()
        objects = [FakeObject('pattern', 'first', oid=OBJECT_A, data=shared),
                   FakeObject('pattern', 'second', oid=OBJECT_B, data=shared)]
        view = {'visible_object_ids': [OBJECT_B], 'explode_z_mm': {OBJECT_B: -40}}
        selected = o._resolve_features(objects, [view])
        self.assertEqual(selected, {OBJECT_B: objects[1]})
        self.assertEqual(o._view_objects(selected, view), selected)
        self.assertEqual(o._selection_key(view, objects[1]), OBJECT_B)
        self.assertEqual(view['explode_z_mm'][o._selection_key(view, objects[1])], -40)
        combined = o._resolve_features(objects, [view, {'visible_feature_ids': ['pattern']}])
        self.assertEqual(o._view_objects(combined, view), selected)

    def test_source_object_identity_is_required_valid_and_unambiguous(self):
        view = {'visible_feature_ids': ['sample_plate']}
        for oid in (None, '', 'sample_plate', '../object', 123, OBJECT_B.upper()):
            with self.subTest(oid=oid), self.assertRaises(RuntimeFailure) as caught:
                o._resolve_features([FakeObject('sample_plate', oid=oid)], [view])
            self.assertEqual(caught.exception.code, 'OBSERVATION_OBJECT_ID')
        for duplicate in (FakeObject('sample_plate', 'duplicate'),
                          FakeObject('different', 'duplicate'),
                          FakeObject('sample_plate', 'hidden duplicate', hidden=True),
                          FakeObject(None, 'camera duplicate', typ='CAMERA')):
            for selection in (view, {'visible_object_ids': [OBJECT_A]}):
                with self.subTest(duplicate=duplicate.name, selection=selection), self.assertRaises(RuntimeFailure) as caught:
                    o._resolve_features([FakeObject('sample_plate'), duplicate], [selection])
                self.assertEqual(caught.exception.code, 'OBSERVATION_OBJECT_ID')
                self.assertEqual(caught.exception.details['object_id'], OBJECT_A)

    def test_object_selector_rejects_unknown_hidden_and_nonmesh(self):
        view = {'visible_object_ids': [OBJECT_A]}
        for objects in ([], [FakeObject('sample_plate', oid=OBJECT_B)],
                        [FakeObject('sample_plate', typ='CAMERA')], [FakeObject('sample_plate', hidden=True)]):
            with self.assertRaises(RuntimeFailure) as caught:
                o._resolve_features(objects, [view])
            self.assertEqual(caught.exception.code, 'OBSERVATION_OBJECT_ID')

    def test_final_visibility_and_expanded_instance_budget(self):
        obj = FakeObject('sample_plate')
        obj.hide_viewport = True
        with self.assertRaises(RuntimeFailure):
            o._resolve_features([obj], [{'visible_feature_ids': ['sample_plate']}])
        obj.hide_viewport = False
        with self.assertRaises(RuntimeFailure):
            o._resolve_features([obj], [{'visible_object_ids': [OBJECT_A]}], render_visible=set())
        objects = [FakeObject('pattern', oid=f'00000000-0000-4000-8000-{i:012x}') for i in range(129)]
        with self.assertRaises(RuntimeFailure) as caught:
            o._resolve_features(objects, [{'visible_feature_ids': ['pattern']}])
        self.assertEqual(caught.exception.code, 'OBSERVATION_SELECTION')
        self.assertEqual(len(o._resolve_features(objects[:128], [{'visible_feature_ids': ['pattern']}])), 128)

    def test_output_is_new_owned_and_cannot_traverse(self):
        output = o._output_path(self.root, 'front')
        self.assertEqual(output.parent, self.root)
        output.write_bytes(b'previous evidence')
        with self.assertRaises(RuntimeFailure):
            o._output_path(self.root, 'front')
        self.assertEqual(output.read_bytes(), b'previous evidence')
        with self.assertRaises(c.ContractError):
            o._output_path(self.root, '../front')
        with self.assertRaises(RuntimeFailure):
            o._output_path('relative', 'front')

    def test_output_symlinks_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(RuntimeFailure):
            o._output_path(alias, 'front')
        file_alias = self.root / 'observation-front.png'
        file_alias.symlink_to(self.root / 'missing.png')
        with self.assertRaises(RuntimeFailure):
            o._output_path(self.root, 'front')

    def test_png_identity_dimensions_and_byte_budget(self):
        # Header fixtures test the report boundary, not image decode quality.
        png = self.root / 'image.png'
        header = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 64, 32)
        png.write_bytes(header)
        record = o._png_record(png, 64, 32)
        self.assertEqual(record['width'], 64)
        self.assertEqual(len(record['sha256']), 64)
        with self.assertRaises(RuntimeFailure):
            o._png_record(png, 32, 64)
        bad = self.root / 'bad.png'
        bad.write_bytes(b'not a png')
        with self.assertRaises(RuntimeFailure):
            o._png_record(bad, 64, 32)
        large = self.root / 'large.png'
        with large.open('xb') as stream:
            stream.write(header)
            stream.truncate(o.MAX_PREVIEW_BYTES + 1)
        with self.assertRaises(RuntimeFailure):
            o._png_record(large, 64, 32)


if __name__ == '__main__':
    unittest.main()
