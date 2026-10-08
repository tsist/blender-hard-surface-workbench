"""Neutral HOST protocol fixtures; never Blender/native qualification."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hardsurface import contract as c, subdivision as s, surface_export as x
from hardsurface.io import RuntimeFailure, descriptor
from hardsurface.observation import _mesh_record
from hardsurface.observation_normals import mesh_record
from hardsurface.subdivision_source import evaluated_bore_domain, _source_neighbour_cycles
from hardsurface.structure_native import FACE_SLOT
from test_observation_geometry_normals import mesh as basic_mesh
from test_subdivision_source_binding import native_panel, refine_mock_quad_mesh
from structure_workunit_fixtures import base_state, diagnosis_request, EXPECTED_SOURCE_MODIFIER

ROOT = Path(__file__).resolve().parents[1]
MATRIX = [[float(i == j) for j in range(4)] for i in range(4)]
OID = '11111111-1111-4111-8111-111111111111'
PROJECTION = {'pixel_aspect_x': 1.0, 'pixel_aspect_y': 1.0, 'resolution_percentage': 100, 'shift_x': 0.0, 'shift_y': 0.0}


def mesh():
    value = basic_mesh(); value.normals_domain = 'CORNER'
    value.corner_normals[0].vector = [-0., .6, .8]
    value.loop_triangles = [NS(vertices=(0, 1, 2), loops=(0, 1, 2), polygon_index=0),
                            NS(vertices=(0, 2, 3), loops=(0, 2, 3), polygon_index=0)]
    for name, domain, values in ((FACE_SLOT, 'FACE', [0]), ('hs_quad_surface_id', 'FACE', [0]),
        ('sharp_edge', 'EDGE', [False] * 4), ('sharp_face', 'FACE', [False]), ('crease_edge', 'EDGE', [0., .5, 1., -0.])):
        value.attributes[name] = NS(domain=domain, data_type='BOOLEAN' if name.startswith('sharp') else 'FLOAT' if name.startswith('crease') else 'INT',
                                    data=[NS(value=v) for v in values])
    return value


def record(value): return mesh_record(value, _mesh_record)


def request():
    result = diagnosis_request('surface', {'file': '/tmp/source.blend', 'sha256': 'a' * 64, 'bytes': 64}, OID, base_state('fixed_bottom'))
    result['params'].update(levels=[0, 2, 3], export_geometry=True, surface_export={
        'format': x.FORMAT, 'levels': [2, 3], 'expected_l2': {'positions_topology_sha256': 'b' * 64, 'native_normals_sharp_smooth_sha256': 'c' * 64},
        'observation': {'report': {'file': '/tmp/old-report.json', 'sha256': 'd' * 64, 'bytes': 64},
                        'views': [{'view': 'pinned', 'image': {'file': '/tmp/old-image.png', 'sha256': 'e' * 64, 'bytes': 64}, 'projection_inputs': dict(PROJECTION)}]}})
    return result


def semantic(value, level=2):
    return {'evidence': {'status': 'validated', 'level': level,
        'evaluated_parent_slots_sha256': c.fingerprint([v.value for v in value.attributes[FACE_SLOT].data]),
        'evaluated_surface_labels_sha256': c.fingerprint([v.value for v in value.attributes['hs_quad_surface_id'].data])}}


def binding(value):
    return {'source_record': {'source_mesh': record(value)}, 'report': {'file': '/tmp/old.json', 'sha256': 'a' * 64, 'bytes': 1}}


def export(value, directory, *, level=2, witness=None, bound=None):
    return x.export_level(value, MATRIX, directory, level,
        {'file': '/tmp/source.blend', 'sha256': 'a' * 64, 'bytes': 1},
        {'file': '/tmp/maps.json', 'sha256': 'b' * 64, 'bytes': 1},
        witness or semantic(value, level), bound or binding(value), [0])


class ContractTests(unittest.TestCase):
    def test_opt_in_is_idempotent_and_preserves_caller_request(self):
        raw = request(); old = copy.deepcopy(raw); result = s.validate_request(raw)
        self.assertEqual(raw, old); self.assertEqual(result, s.validate_request(result))
        self.assertEqual(c.fingerprint(result), c.fingerprint(s.validate_request(result)))

    def test_legacy_normalized_request_and_fingerprint_unchanged(self):
        raw = request(); del raw['params']['surface_export']
        current = s.validate_request(raw)
        legacy = copy.deepcopy(s.REQUEST); del legacy['properties']['params']['properties']['surface_export']
        with patch.object(s, 'REQUEST', legacy): old = s.validate_request(raw)
        self.assertEqual(current, old); self.assertEqual(c.fingerprint(current), c.fingerprint(old))
        self.assertNotIn('surface_export', current['params'])

    def test_forbidden_execution_modes_and_missing_binding_rejected(self):
        changes = [('evaluation_profile', {'mode': 'legacy_geometry_diagnostic_v0'}), ('render', {'enabled': True}),
                   ('export_geometry', False), ('levels', [0, 1, 2, 3]), ('cpu_threads', 1)]
        for name, value in changes:
            raw = request(); raw['params'][name] = value
            with self.subTest(name=name), self.assertRaises(c.ContractError): s.validate_request(raw)
        raw = request(); del raw['params']['surface_export']['expected_l2']
        with self.assertRaises(c.ContractError): s.validate_request(raw)
        raw = request(); del raw['params']['export_geometry']
        with self.assertRaises(c.ContractError): s.validate_request(raw)
        raw = request(); raw['params']['surface_export']['levels'] = [2., 3.]
        with self.assertRaises(c.ContractError): s.validate_request(raw)
        raw = request(); raw['params']['surface_export']['observation']['views'][0]['projection_inputs']['pixel_aspect_x'] = True
        with self.assertRaises(c.ContractError): s.validate_request(raw)

    def test_schema_matches_generated(self):
        self.assertEqual(json.loads((ROOT / 'schemas/hardsurface-subdivision-diagnose.schema.json').read_text()), s.schema())


class NativeArrayTests(unittest.TestCase):
    def test_roundtrip_preserves_exact_observation_hashes_and_signed_zero(self):
        value = mesh(); before = copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as directory:
            output = export(value, directory)
            data = json.loads(Path(output['file']).read_text())
            self.assertEqual(x.recompute_identity(data), record(value))
            self.assertEqual(struct.pack('>d', data['corner_normals'][0][0]), struct.pack('>d', -0.))
            self.assertEqual(data['loop_triangles'][1], [0, 2, 3, 0, 2, 3, 0])
            self.assertEqual(data['parent_slot_attribute']['domain'], 'FACE')
            self.assertEqual(data['crease_attributes']['crease_edge']['values'], [0., .5, 1., -0.])
            self.assertEqual(descriptor(output['file']), {k: output[k] for k in ('file', 'sha256', 'bytes')})
            with self.assertRaises(RuntimeFailure): export(value, directory)
            self.assertEqual(descriptor(output['file'])['sha256'], output['sha256'])
        self.assertEqual(vars(value), vars(before))

    def test_changed_normal_vector_or_signed_zero_changes_hash(self):
        value = mesh()
        with tempfile.TemporaryDirectory() as directory:
            output = export(value, directory); data = json.loads(Path(output['file']).read_text())
            data['corner_normals'][0][0] = 0.
            self.assertNotEqual(x.recompute_identity(data), record(value))
            data['normals_units'] = 'mm'
            with self.assertRaises(RuntimeFailure): x.recompute_identity(data)

    def test_rehash_rejects_extra_columns_and_inconsistent_attribute_presence(self):
        value = mesh()
        with tempfile.TemporaryDirectory() as directory:
            output = export(value, directory); original = json.loads(Path(output['file']).read_text())
        for field in ('loops', 'loop_triangles', 'polygon_state', 'corner_normals', 'vertices'):
            data = copy.deepcopy(original); data[field][0].append(0)
            with self.subTest(field=field), self.assertRaises(RuntimeFailure): x.recompute_identity(data)
        data = copy.deepcopy(original); data['native_shading_attributes']['sharp_edge']['count'] += 1
        with self.assertRaises(RuntimeFailure): x.recompute_identity(data)
        data = copy.deepcopy(original); data['crease_attributes']['crease_vert']['values'] = []
        with self.assertRaises(RuntimeFailure): x.recompute_identity(data)

    def test_missing_corrupt_and_nonfinite_native_fields_fail_closed(self):
        mutations = [lambda m: delattr(m, 'corner_normals'), lambda m: delattr(m.loop_triangles[0], 'loops'),
            lambda m: setattr(m, 'normals_domain', 'POINT'), lambda m: setattr(m.corner_normals[0], 'vector', [False, .6, .8]),
            lambda m: setattr(m.corner_normals[0], 'vector', [0., 0., 2.]),
            lambda m: setattr(m.corner_normals[0], 'vector', [float('nan'), 0., 1.]),
            lambda m: setattr(m.vertices[0], 'co', [True, 0., 0.]),
            lambda m: setattr(m.loops[0], 'vertex_index', False), lambda m: setattr(m.loops[0], 'edge_index', 2),
            lambda m: setattr(m.polygons[0], 'loop_start', 1), lambda m: setattr(m.edges[0], 'use_edge_sharp', 1),
            lambda m: setattr(m.loop_triangles[0], 'loops', [0, 1, 3]),
            lambda m: setattr(m.loop_triangles[0], 'polygon_index', True),
            lambda m: m.loop_triangles.__setitem__(1, copy.deepcopy(m.loop_triangles[0])),
            lambda m: m.loop_triangles.__setitem__(0, NS(vertices=(0, 2, 1), loops=(0, 2, 1), polygon_index=0)),
            lambda m: setattr(m.attributes['crease_edge'], 'domain', 'POINT'),
            lambda m: setattr(m.attributes['crease_edge'].data[0], 'value', True)]
        for index, mutation in enumerate(mutations):
            value = mesh(); mutation(value)
            with self.subTest(index=index), self.assertRaises(RuntimeFailure): x.validate_mesh(value, MATRIX)

    def test_changed_old_l2_identity_blocks_export(self):
        value = mesh(); bound = binding(value); value.corner_normals[0].vector = [0., 0., 1.]
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeFailure): export(value, directory, bound=bound)
        self.assertEqual(bound['source_record']['source_mesh']['positions_topology_sha256'], _mesh_record(value)['positions_topology_sha256'])

    def test_changed_semantics_after_validation_cannot_be_written(self):
        for name in (FACE_SLOT, 'hs_quad_surface_id'):
            value = mesh(); witness = semantic(value); value.attributes[name].data[0].value = 1
            with tempfile.TemporaryDirectory() as directory, self.subTest(name=name), self.assertRaises(RuntimeFailure):
                export(value, directory, witness=witness)

    def test_sharp_attribute_presence_is_part_of_exact_hash(self):
        value = mesh(); initial = record(value)
        del value.attributes['sharp_edge']
        self.assertNotEqual(record(value), initial)
        with tempfile.TemporaryDirectory() as directory:
            output = export(value, directory); data = json.loads(Path(output['file']).read_text())
            self.assertFalse(data['native_shading_attributes']['sharp_edge']['present'])
            self.assertEqual(x.recompute_identity(data), record(value))

    def test_streamed_byte_limits_leave_partial_files_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.json'; written = [0]
            limits = {**x.LIMITS, 'file_bytes': 20, 'aggregate_bytes': 20}
            with patch.dict(x.LIMITS, limits), self.assertRaises(RuntimeFailure) as caught:
                x.write_json_new(path, {'values': x.Array(range(100))}, written)
            self.assertEqual(caught.exception.code, 'SUBDIVISION_SURFACE_OUTPUT_LIMIT')
            self.assertTrue(path.exists()); self.assertEqual(path.stat().st_size, written[0]); self.assertLessEqual(written[0], 20)
            with self.assertRaises(RuntimeFailure): x.write_json_new(path, {}, [0])
            other = Path(directory) / 'aggregate.json'
            with patch.dict(x.LIMITS, {'aggregate_bytes': 4}), self.assertRaises(RuntimeFailure):
                x.write_json_new(other, {'a': 1}, [3])
            self.assertTrue(other.exists())


class SourceMapAndTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hardsurface.subdivision_source import capture_semantic_source
        cls.obj, cls.registry = native_panel()
        cls.source = capture_semantic_source(cls.obj, unit_scale=1., expected_binding=cls.registry, require_bound=True)

    def test_fresh_source_maps_bind_actual_slots_authored_ids_and_loops(self):
        maps = x.capture_source_maps(self.obj, self.source, unit_scale=1., registry_entry=self.registry)
        self.assertEqual(len(maps['source_slot_to_polygon']), 992)
        self.assertEqual(len(maps['named_control_loops']), 16)
        for slot, polygon in enumerate(maps['source_slot_to_polygon']):
            self.assertEqual(maps['source_parent_slots'][polygon], slot)
            self.assertEqual(maps['source_face_map'][maps['source_slot_to_authored_face_id'][slot]], polygon)
        for loop in maps['named_control_loops']:
            self.assertEqual(loop['vertex_indices'], [maps['source_vertex_map'][name] for name in loop['vertex_ids']])

    def test_reordered_source_polygon_maps_are_read_from_native_slots(self):
        from hardsurface.subdivision_source import capture_semantic_source
        obj = copy.deepcopy(self.obj)
        obj.data.polygons.reverse()
        for name in (FACE_SLOT, 'hs_quad_surface_id'):
            obj.data.attributes[name].data.reverse()
        for index, face in enumerate(obj.data.polygons): face.index = index
        source = capture_semantic_source(obj, unit_scale=1., expected_binding=self.registry, require_bound=True)
        maps = x.capture_source_maps(obj, source, unit_scale=1., registry_entry=self.registry)
        old_slots = [row.value for row in self.obj.data.attributes[FACE_SLOT].data]
        expected = [old_slots[::-1].index(slot) for slot in range(len(old_slots))]
        self.assertEqual(maps['source_slot_to_polygon'], expected)
        for slot, polygon in enumerate(maps['source_slot_to_polygon']):
            self.assertEqual(maps['source_face_map'][maps['source_slot_to_authored_face_id'][slot]], polygon)

    def test_actual_parent_transport_rejects_wrong_surface_and_parent_assignments(self):
        refined = refine_mock_quad_mesh(self.obj.data)
        witness = evaluated_bore_domain(refined, self.source, level=1)
        self.assertEqual(witness['evidence']['status'], 'validated')
        for name, replacement in ((FACE_SLOT, 99999), ('hs_quad_surface_id', 99999)):
            changed = copy.deepcopy(refined); changed.attributes[name].data[0].value = replacement
            with self.subTest(name=name), self.assertRaises(RuntimeFailure): evaluated_bore_domain(changed, self.source, level=1)


class ObservationBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        value = mesh(); state = record(value)
        self.source = {'file': str(self.directory / 'source.blend'), 'sha256': 'a' * 64, 'bytes': 16}
        self.image = self.directory / 'relocated.png'
        self.image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0\0\0\rIHDR' + struct.pack('>II', 64, 64))
        self.option = request()['params']['surface_export']
        self.option['expected_l2'] = {'positions_topology_sha256': state['positions_topology_sha256'],
            'native_normals_sharp_smooth_sha256': state['native_shading']['native_normals_sharp_smooth_sha256']}
        self.option['observation']['views'][0]['image'] = descriptor(self.image)
        source_record = {'mesh_state': 'evaluated', 'policy': 'geometry_normals_v1', 'source_mesh': state, 'source_control_mesh': state,
                         'source_modifier': {'settings': EXPECTED_SOURCE_MODIFIER}}
        self.report = {'operation': 'hardsurface.observe', 'status': 'succeeded', 'outcome': 'pass', 'mesh_state': 'evaluated',
            'blend_save_performed': False, 'saved_candidate_modified': False, 'temporary_state_restored': True,
            'source_sha256': self.source['sha256'], **{key: {**self.source, 'file': '/old/stale/source.blend'} for key in ('source', 'source_after', 'opened_source')},
            'implementation': {'source_sha256': 'd' * 64, 'blender': {'file': '/old/blender', 'sha256': 'e' * 64, 'bytes': 1}},
            'previews': [{'view': 'pinned', 'mesh_state': 'evaluated', 'file': '/old/stale/image.png', 'sha256': descriptor(self.image)['sha256'], 'bytes': self.image.stat().st_size,
                'width': 64, 'height': 64, 'wire': {'enabled': False},
                'camera': {'type': 'ORTHO', 'position_mm': [0., 0., 1.], 'target_mm': [0., 0., 0.], 'up_axis': 'Z',
                    'ortho_scale_mm': 24.000000208616257, 'sensor_fit': 'VERTICAL', 'matrix_world': MATRIX,
                    'clip_start_m': 9.999999974752427e-7, 'clip_end_m': 1000.},
                'diagnostic': {'normal_isolation': {'policy': 'geometry_normals_v1'}},
                'normal_isolation_checks': [{'phase': phase, 'view': 'pinned', 'source_and_proxy_identity': 'pass'} for phase in ('before_render', 'after_render')],
                'temporary_transformations': [{'object_id': OID, 'original_matrix_world': MATRIX, 'observation_matrix_world': MATRIX, 'translation_world_mm': [0., 0., 0.],
                    'frozen_geometry': {'geometry_unchanged': True, 'normal_isolation': {'source': source_record, 'frozen_proxy': state,
                        'normal_sharp_smooth_unchanged': True, 'normal_recomputed_transferred_or_authored_by_observer': False}}}]}]}
        self.save_report()

    def save_report(self):
        path = self.directory / 'old-report.json'; path.write_text(json.dumps(self.report))
        self.option['observation']['report'] = descriptor(path)

    def bind(self): return x.bind_observation(self.option, self.source, OID, EXPECTED_SOURCE_MODIFIER)

    def test_relocated_same_bytes_bind_without_stale_path_equality(self):
        result = self.bind()
        self.assertEqual(result['views'][0]['image']['file'], str(self.image))
        self.assertEqual(result['views'][0]['recorded_image']['file'], '/old/stale/image.png')
        self.assertIn('no user or remote approval', result['authority'])

    def test_changed_pinned_report_bytes_are_rejected(self):
        Path(self.option['observation']['report']['file']).write_text(json.dumps(self.report) + '\n')
        with self.assertRaises(RuntimeFailure) as caught: self.bind()
        self.assertEqual(caught.exception.code, 'EVIDENCE_REFERENCE_MISMATCH')

    def test_wrong_source_hash_image_hash_normal_hash_and_profile_rejected(self):
        mutations = [lambda r: r['source'].update(sha256='b' * 64),
            lambda r: r['previews'][0].update(sha256='b' * 64),
            lambda r: r['previews'][0]['temporary_transformations'][0]['frozen_geometry']['normal_isolation']['source']['source_mesh']['native_shading'].update(native_normals_sharp_smooth_sha256='b' * 64),
            lambda r: r['previews'][0]['temporary_transformations'][0]['frozen_geometry']['normal_isolation']['source']['source_modifier']['settings'].update(quality=3),
            lambda r: r['previews'][0]['normal_isolation_checks'].pop(),
            lambda r: r['previews'][0].pop('camera')]
        original = copy.deepcopy(self.report)
        for index, mutate in enumerate(mutations):
            self.report = copy.deepcopy(original); mutate(self.report); self.save_report()
            with self.subTest(index=index), self.assertRaises(RuntimeFailure): self.bind()


if __name__ == '__main__': unittest.main()
