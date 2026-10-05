"""Host contract, numerical-domain and source-preserving worker mock tests."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
import uuid
from unittest.mock import patch

from hardsurface import contract as c
from hardsurface import topology as t
from hardsurface.io import RuntimeFailure


IDENTITY = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]


def request():
    return {'schema_version': '1.0', 'command': 'hardsurface.topology', 'params': {
        'request_id': 'topology.fixture.1', 'source': {'file': '/tmp/source.blend', 'expected_sha256': 'a' * 64}}}


def geometry(vertices, faces, edges=None):
    if edges is None:
        edges = sorted({tuple(sorted((a, b))) for f in faces for a, b in zip(f, (*f[1:], f[0]))})
    return {'vertices': vertices, 'edges': edges, 'polygons': faces}


def triangles(faces):
    return [{'vertices': (f[0], f[i], f[i + 1]), 'polygon_index': fi}
            for fi, f in enumerate(faces) for i in range(1, len(f) - 1)]


def analyze(g, matrix=None):
    return t._analyze(g, triangles(g['polygons']), matrix or IDENTITY)


def cube():
    return geometry([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
                    [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])


class SchemaTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaises(c.ContractError):
            t.validate_request(value)

    def test_import_does_not_load_blender(self):
        result = subprocess.run([sys.executable, '-B', '-c',
                                 'import sys; from hardsurface import topology; topology.schema(); '
                                 'assert "bpy" not in sys.modules; assert "mathutils" not in sys.modules'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_defaults_and_independent_schema(self):
        original = request()
        copy_original = copy.deepcopy(original)
        result = t.validate_request(original)['params']
        self.assertEqual(original, copy_original)
        self.assertEqual(result['mesh_states'], ['control', 'evaluated'])
        self.assertFalse(result['export_geometry'])
        self.assertEqual((result['cpu_threads'], result['wall_seconds']), (2, 600))
        self.assertNotIn('feature_ids', result)
        schema = t.schema()
        schema['properties']['command']['const'] = 'other'
        self.assertEqual(t.schema()['properties']['command']['const'], 'hardsurface.topology')

    def test_unknown_fields_and_fixed_command(self):
        for level in ('root', 'params', 'source'):
            r = request()
            node = {'root': r, 'params': r['params'], 'source': r['params']['source']}[level]
            node['output_path'] = '/tmp/replace.blend'
            self.invalid(r)
        r = request()
        r['command'] = 'hardsurface.retopology'
        self.invalid(r)

    def test_arrays_are_bounded_exact_unique(self):
        for field, bad_values in [('mesh_states', [[], ['control', 'control'], ['render'], ['control', 'evaluated', 'control']]),
                                  ('feature_ids', [[], ['sample_plate', 'sample_plate'], ['sample_plate/*'], ['a' * 97], ['x' + str(i) for i in range(129)]])]:
            for value in bad_values:
                r = request()
                r['params'][field] = value
                self.invalid(r)

    def test_object_ids_require_unique_uuids_and_exclude_feature_filter(self):
        oid = str(uuid.uuid5(uuid.NAMESPACE_DNS, 'topology.test'))
        for ids in ([], ['sample_plate'], [oid, oid], [oid.upper()], [str(uuid.uuid5(uuid.NAMESPACE_DNS, str(i))) for i in range(129)]):
            r = request()
            r['params']['object_ids'] = ids
            self.invalid(r)
        r = request()
        r['params']['object_ids'] = [oid]
        self.assertEqual(t.validate_request(r)['params']['object_ids'], [oid])
        r['params']['feature_ids'] = ['sample_plate']
        self.invalid(r)
        self.assertEqual(t.schema()['properties']['params']['not'], {'required': ['feature_ids', 'object_ids']})

    def test_strict_primitives_and_paths(self):
        for field, values in [('cpu_threads', [0, 5, True, 1.1]), ('wall_seconds', [0, 601, True, float('nan'), float('inf')]),
                              ('export_geometry', [1, 'true', None]), ('request_id', ['', '../escape', 'a' * 129])]:
            for value in values:
                r = request()
                r['params'][field] = value
                self.invalid(r)
        for path in ('relative.blend', '/tmp/../source.blend', '/tmp/source.png', '/tmp/back\\slash.blend'):
            r = request()
            r['params']['source']['file'] = path
            self.invalid(r)
        self.invalid(json.dumps(request()).replace('"schema_version": "1.0"', '"schema_version":"1.0","schema_version":"1.0"'))


class MeshMetricsTests(unittest.TestCase):
    def test_cube_actual_faces_and_oriented_volume(self):
        result = analyze(cube())
        m = result['metrics']
        self.assertEqual(m['counts'], {'vertices': 8, 'edges': 12, 'faces': 6, 'triangles': 0,
                                      'quads': 6, 'ngons': 0, 'maximum_face_sides': 4, 'polygon_corners': 24})
        self.assertEqual(m['loop_triangulation']['count'], 12)
        self.assertEqual(m['nonmanifold_edges'], 0)
        self.assertEqual(m['nonmanifold_vertices'], 0)
        self.assertEqual(m['valence_histogram'], {'3': 8})
        self.assertEqual(m['poles'], 8)  # Corners are diagnostic poles, not failures.
        self.assertAlmostEqual(m['oriented_signed_volume_m3'], 1)
        self.assertAlmostEqual(m['projected_polygon_area_m2'], 6)
        self.assertEqual(m['zero_area_faces'], 0)

    def test_inverted_winding_and_transform_volume(self):
        g = cube()
        g['polygons'] = [tuple(reversed(face)) for face in g['polygons']]
        self.assertAlmostEqual(analyze(g)['metrics']['oriented_signed_volume_m3'], -1)
        matrix = copy.deepcopy(IDENTITY)
        matrix[0][0], matrix[1][1], matrix[2][2] = 2, 3, 4
        matrix[0][3] = 200
        result = analyze(cube(), matrix)
        self.assertAlmostEqual(result['metrics']['oriented_signed_volume_m3'], 24)
        example = result['findings']['poles']['examples'][0]
        self.assertEqual(example['world_position_m'], [200, 0, 0])

    def test_ngon_tessellation_never_becomes_source_edges(self):
        g = geometry([(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0)], [(0, 1, 2, 3, 4)])
        m = analyze(g)['metrics']
        self.assertEqual((m['counts']['edges'], m['counts']['faces'], m['counts']['ngons']), (5, 1, 1))
        self.assertEqual(m['source_triangles']['count'], 0)
        self.assertEqual(m['loop_triangulation']['count'], 3)
        self.assertEqual(m['boundary_edges'], 5)

    def test_loose_duplicate_points_faces_edges_and_zero_area(self):
        g = geometry([(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 0, 0), (9, 9, 9), (8, 8, 8)],
                     [(0, 1, 2), (2, 1, 0)], [(0, 1), (1, 2), (0, 2), (0, 1), (3, 5)])
        result = analyze(g)
        m = result['metrics']
        self.assertEqual(m['duplicate_vertex_cells'], 1)
        self.assertEqual(m['duplicate_faces'], 1)
        self.assertEqual(m['duplicate_edges'], 1)
        self.assertEqual(m['zero_area_faces'], 2)
        self.assertEqual(m['loose_edges'], 1)
        self.assertEqual(m['loose_vertices'], 1)
        self.assertEqual(m['source_triangles']['degenerate_count'], 2)

    def test_vertex_bow_tie_even_with_closed_edge_fans(self):
        # Two tetrahedra sharing only vertex zero have disconnected vertex fans.
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0), (0, -1, 0), (0, 0, -1)]
        faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3),
                 (0, 4, 5), (0, 6, 4), (0, 5, 6), (4, 6, 5)]
        m = analyze(geometry(vertices, faces))['metrics']
        self.assertEqual(m['nonmanifold_edges'], 0)
        self.assertEqual(m['disconnected_vertex_fans'], 1)
        self.assertEqual(m['nonmanifold_vertices'], 1)

    def test_concavity_nonplanarity_quad_warp_separate(self):
        concave = geometry([(0, 0, 0), (2, 0, 0), (1, .5, 0), (2, 2, 0), (0, 2, 0)], [(0, 1, 2, 3, 4)])
        self.assertEqual(analyze(concave)['metrics']['concave_faces'], 1)
        warped = geometry([(0, 0, 0), (1, 0, 0), (1, 1, .25), (0, 1, 0)], [(0, 1, 2, 3)])
        m = analyze(warped)['metrics']
        self.assertEqual((m['nonplanar_faces'], m['warped_quads']), (1, 1))
        self.assertGreater(m['maximum_quad_warp_degrees'], 5)

    def test_long_thin_source_and_derived_triangle_separate(self):
        g = geometry([(0, 0, 0), (1, 0, 0), (1, .00001, 0)], [(0, 1, 2)])
        result = analyze(g)
        self.assertEqual(result['metrics']['source_triangles']['long_thin_count'], 1)
        self.assertEqual(result['metrics']['loop_triangulation']['long_thin_count'], 1)
        example = result['findings']['long_thin_loop_triangles']['examples'][0]
        self.assertEqual((example['face_index'], example['loop_triangle_index']), (0, 0))

    def test_findings_bounded_without_losing_counts(self):
        g = geometry([(i, 0, 0) for i in range(200)], [], [])
        result = analyze(g)
        found = result['findings']['loose_vertices']
        self.assertEqual(found['count'], 200)
        self.assertEqual(len(found['examples']), 32)
        self.assertTrue(found['examples_truncated'])

    def test_duplicate_face_canonicalization_repeated_indices(self):
        original = (0, 1, 0, 2, 3)
        for i in range(len(original)):
            rotated = original[i:] + original[:i]
            self.assertEqual(t._canonical_face(original), t._canonical_face(rotated))
            self.assertEqual(t._canonical_face(original), t._canonical_face(tuple(reversed(rotated))))

    def test_indexed_candidate_congruence_ignores_translation_only(self):
        g = cube()
        moved = copy.deepcopy(g)
        moved['vertices'] = [(x + 5, y + 2, z - 1) for x, y, z in g['vertices']]
        original, shifted = analyze(g)['metrics'], analyze(moved)['metrics']
        self.assertEqual(original['candidate_congruence_sha256'], shifted['candidate_congruence_sha256'])
        self.assertNotEqual(original['local_geometry_sha256'], shifted['local_geometry_sha256'])
        self.assertEqual(original['topology_sha256'], shifted['topology_sha256'])

    def test_analysis_deadline_and_invalid_loop_indices(self):
        def fail():
            raise RuntimeFailure('TEST_TIMEOUT', 'deadline')
        with self.assertRaises(RuntimeFailure):
            t._analyze(cube(), [], IDENTITY, deadline=fail)
        with self.assertRaises(RuntimeFailure):
            t._analyze(cube(), [{'vertices': [0, 1, 999], 'polygon_index': 0}], IDENTITY)


class FakeMesh:
    def __init__(self, g, name='mesh'):
        self.name, self.users = name, 1
        self.vertices = [NS(co=v) for v in g['vertices']]
        self.edges = [NS(vertices=e) for e in g['edges']]
        self.polygons, self.loops = [], []
        for face in g['polygons']:
            self.polygons.append(NS(vertices=face, use_smooth=False, loop_start=len(self.loops), normal=(0., 0., 1.)))
            self.loops.extend(NS(vertex_index=v) for v in face)
        self.loop_triangles = []
        self.attributes = {}
        self.tessellations = 0

    def get(self, key):
        return 'data-id' if key == 'hs_data_id' else None

    def as_pointer(self):
        return id(self)

    def copy(self):
        return copy.deepcopy(self)

    def calc_loop_triangles(self):
        self.tessellations += 1
        self.loop_triangles = [NS(**tri) for tri in triangles([p.vertices for p in self.polygons])]


class FakeObject:
    def __init__(self, fid, mesh=None, name=None, typ='MESH', hidden=False, generated=True):
        self.fid, self.name, self.type = fid, name or str(fid), typ
        self.hide_render, self.hide_viewport = hidden, False
        self.generated = generated
        self.object_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, 'topology.' + self.name))
        self.data = mesh or FakeMesh(cube())
        self.mode, self.modifiers, self.users_collection = 'OBJECT', [], []
        self.matrix_world = copy.deepcopy(IDENTITY)
        self.clear_calls = 0

    def get(self, key):
        return {'hs_feature_id': self.fid, 'hs_generated': self.generated, 'hs_object_id': self.object_id}.get(key)

    def evaluated_get(self, depsgraph):
        return self

    def to_mesh(self, **kwargs):
        return self.data.copy()

    def to_mesh_clear(self):
        self.clear_calls += 1


class BoundaryTests(unittest.TestCase):
    def test_selection_counts_each_exclusion_and_all_instances(self):
        objects = [FakeObject('sample_plate'), FakeObject('sample_cap'), FakeObject('cut', hidden=True),
                   FakeObject('camera', typ='CAMERA'), FakeObject('manual', generated=False), FakeObject(None)]
        selected, record = t._resolve_features(objects)
        self.assertEqual(set(selected), {objects[0].object_id, objects[1].object_id})
        self.assertEqual(record['feature_ids'], ['sample_cap', 'sample_plate'])
        self.assertEqual(record['object_ids'], sorted(selected))
        self.assertEqual(record['instances_count'], 2)
        self.assertEqual(record['excluded_counts'], {'non_mesh': 1, 'hidden_mesh': 1, 'non_generated_mesh': 1,
                                                    'missing_feature_id_mesh': 1, 'unrequested_final_mesh': 0})
        _, subset = t._resolve_features(objects, ['sample_plate'])
        self.assertEqual(subset['excluded_counts']['unrequested_final_mesh'], 1)

    def test_unknown_hidden_nonmesh_targets_fail(self):
        for objects in ([], [FakeObject('sample_cap')], [FakeObject('sample_plate', hidden=True)], [FakeObject('sample_plate', typ='CURVE')],
                        [FakeObject('sample_plate', generated=False)]):
            with self.assertRaises(RuntimeFailure):
                t._resolve_features(objects, ['sample_plate'])

    def test_pattern_feature_expands_shared_and_independent_instances(self):
        first = FakeObject('PATTERN', name='Screw 1')
        shared = FakeObject('PATTERN', first.data, name='Screw 2')
        independent = FakeObject('PATTERN', name='Screw 3')
        objects = [first, shared, independent, FakeObject('OTHER')]
        for selected, record in (t._resolve_features(objects[:3]), t._resolve_features(objects, ['PATTERN'])):
            self.assertEqual(set(selected), {first.object_id, shared.object_id, independent.object_id})
            self.assertEqual(record['feature_ids'], ['PATTERN'])
            self.assertEqual(record['instances_count'], 3)
            self.assertIs(selected[first.object_id].data, selected[shared.object_id].data)
            self.assertIsNot(selected[first.object_id].data, selected[independent.object_id].data)
        selected, record = t._resolve_features(objects, object_ids=[shared.object_id])
        self.assertEqual(list(selected), [shared.object_id])
        self.assertEqual(record['mode'], 'explicit_object_ids')
        self.assertEqual(record['excluded_counts']['unrequested_final_mesh'], 3)

    def test_object_identity_missing_invalid_duplicate_and_unknown_rejected(self):
        for bad_id in (None, '', 'name-based-id', 'A' * 36):
            obj = FakeObject('sample_plate')
            obj.object_id = bad_id
            with self.assertRaises(RuntimeFailure) as error:
                t._resolve_features([obj])
            self.assertEqual(error.exception.code, 'TOPOLOGY_OBJECT_ID')
        first, second = FakeObject('PATTERN', name='first'), FakeObject('PATTERN', name='second')
        second.object_id = first.object_id
        with self.assertRaises(RuntimeFailure) as error:
            t._resolve_features([first, second])
        self.assertEqual(error.exception.code, 'TOPOLOGY_OBJECT_ID')
        with self.assertRaises(RuntimeFailure):
            t._resolve_features([first], object_ids=[str(uuid.uuid4())])

    def test_modifier_parity_and_bevel_clamp_is_observation(self):
        obj = FakeObject('sample_plate')
        obj.modifiers = [NS(name='bevel', type='BEVEL', show_viewport=True, show_render=True,
                            width=.001, segments=3, use_clamp_overlap=True)]
        observed = t._modifiers(obj)
        self.assertTrue(observed[0]['use_clamp_overlap'])
        self.assertIn('not measured', observed[0]['clamp_semantics'])
        obj.modifiers[0].show_render = False
        with self.assertRaises(RuntimeFailure):
            t._modifiers(obj)

    def test_budget_fails_before_polygon_iteration_and_aggregate(self):
        class Large:
            def __len__(self):
                return 200001
        mesh = NS(vertices=Large(), edges=[], polygons=[], loops=[])
        with self.assertRaises(RuntimeFailure):
            t._counts(mesh)
        total = {'vertices': 199999}
        with self.assertRaises(RuntimeFailure):
            t._consume_budget(total, {'vertices': 2})

    def test_output_immutable_byte_bounded_and_cannot_traverse(self):
        root = Path(tempfile.mkdtemp(prefix='hs-topology-boundary-', dir='/tmp'))
        ref = t._write_output(root, 'topology-000-control-stats.json', {'a': 1}, [0])
        original = Path(ref['file']).read_bytes()
        self.assertEqual(ref['sha256'], hashlib.sha256(original).hexdigest())
        for name in ('topology-000-control-stats.json', '../source.blend', 'topology-/bad.json'):
            with self.assertRaises(RuntimeFailure):
                t._write_output(root, name, {'bad': 1}, [0])
        with patch.dict(t.LIMITS, {'json_file_bytes': 5}):
            with self.assertRaises(RuntimeFailure):
                t._write_output(root, 'topology-001-control-stats.json', {'large': 'abc'}, [0])
        self.assertEqual(Path(ref['file']).read_bytes(), original)
        alias = root / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(RuntimeFailure):
            t._output_path(alias, 'topology-002-control-stats.json')

    def test_sharp_flags_and_corner_normals_preserve_domain(self):
        mesh = FakeMesh(geometry([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2), (0, 2, 3)]))
        mesh.attributes['sharp_edge'] = NS(data=[NS(value=(i == 0)) for i in range(len(mesh.edges))])
        mesh.polygons[0].use_smooth = True
        mesh.corner_normals = [NS(vector=v) for v in [(0, 0, 1)] * 3 + [(0, 1, 0)] * 3]
        result = t._shading_summary(mesh)
        self.assertEqual((result['sharp_edge_count'], result['smooth_face_count']), (1, 1))
        self.assertEqual(result['corner_normals']['vertices_with_corner_angle_above_001_degree'], 2)
        self.assertAlmostEqual(result['corner_normals']['maximum_angle_from_first_corner_degrees'], 90)
        exported = t._shading_export(mesh)
        self.assertEqual(exported['polygon_loop_starts'], [0, 3])
        self.assertEqual(exported['loop_vertex_indices'], [0, 1, 2, 0, 2, 3])


class ExecuteMockTests(unittest.TestCase):
    def setup_scene(self):
        root = Path(tempfile.mkdtemp(prefix='hs-topology-execute-', dir='/tmp'))
        source = root / 'source.blend'
        source.write_bytes(b'saved blend fixture; never modified')
        job = root / 'job'
        job.mkdir()
        mesh = FakeMesh(cube())
        objects = [FakeObject('sample_plate', mesh), FakeObject('sample_cap', mesh)]
        removed = []
        scene = NS(name='test', objects=objects, collection=NS(objects=objects, children=[], hide_render=False))
        layer = NS(name='main', update=lambda: None)
        bpy = NS(data=NS(filepath=str(source), meshes=NS(remove=lambda value: removed.append(value))),
                 context=NS(scene=scene, view_layer=layer, evaluated_depsgraph_get=lambda: object()))
        geometry_module = NS(require_si_scene=lambda: {'scale_length': 1.0})
        r = request()
        r['params']['source'] = {'file': str(source), 'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        r['params']['export_geometry'] = True
        return root, source, job, objects, removed, bpy, geometry_module, r

    def test_end_to_end_each_instance_each_state_export_and_source_preservation(self):
        root, source, job, objects, removed, bpy, gm, r = self.setup_scene()
        before = source.read_bytes()
        with patch.dict(sys.modules, {'bpy': bpy, 'hardsurface.ops.geometry': gm}):
            result = t.execute(r, job)
        self.assertEqual(result['outcome'], 'topology_inspected')
        self.assertEqual(result['source'], result['source_after'])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(len(result['topology']['objects']), 2)
        self.assertEqual(len(result['topology']['shared_mesh_groups']), 1)
        self.assertEqual(len(result['topology']['shared_mesh_groups'][0]['objects']), 2)
        self.assertEqual(len(result['topology']['candidate_congruent_groups']), 2)
        self.assertEqual(result['topology']['aggregate_analyzed_domains']['vertices'], 32)
        self.assertEqual(len(result['outputs']), 8)
        self.assertEqual(len(removed), 2)
        self.assertEqual([obj.clear_calls for obj in objects], [1, 1])
        self.assertEqual(objects[0].data.tessellations, 0)
        for ref in result['outputs']:
            data = Path(ref['file']).read_bytes()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (ref['bytes'], ref['sha256']))
            exported = json.loads(data)
            if ref['kind'] == 'topology_geometry':
                self.assertEqual((len(exported['edges']), len(exported['polygons']), len(exported['loop_triangles'])), (12, 6, 12))
                self.assertEqual(exported['matrix_world'], IDENTITY)
                self.assertEqual(exported['coordinate_units'], 'm')
                self.assertEqual(len(exported['shading']['face_smooth']), 6)
        self.assertFalse(result['blend_save_performed'])

    def test_analysis_failure_releases_only_temporary_mesh_and_preserves_source(self):
        root, source, job, objects, removed, bpy, gm, r = self.setup_scene()
        before = source.read_bytes()
        with patch.dict(sys.modules, {'bpy': bpy, 'hardsurface.ops.geometry': gm}), \
                patch.object(t, '_analyze', side_effect=RuntimeFailure('TEST_FAIL', 'analysis failed')):
            with self.assertRaisesRegex(RuntimeFailure, 'analysis failed'):
                t.execute(r, job)
        self.assertEqual(len(removed), 1)
        self.assertIsNot(removed[0], objects[0].data)
        self.assertEqual(source.read_bytes(), before)

    def test_pattern_instances_remain_independent_through_execution(self):
        root, source, job, objects, removed, bpy, gm, r = self.setup_scene()
        # Two real-style pattern instances share feature/data identity but keep
        # distinct object IDs. A third shares the feature but owns copied data.
        objects[0].fid = objects[1].fid = 'PATTERN'
        objects.append(FakeObject('PATTERN', mesh=objects[0].data.copy(), name='third pattern instance'))
        r['params']['feature_ids'] = ['PATTERN']
        with patch.dict(sys.modules, {'bpy': bpy, 'hardsurface.ops.geometry': gm}):
            result = t.execute(r, job)
        topology = result['topology']
        self.assertEqual(topology['selection']['instances_count'], 3)
        self.assertEqual(topology['selection']['feature_ids'], ['PATTERN'])
        self.assertEqual(len(topology['objects']), 3)
        self.assertEqual(len({row['identity']['object_id'] for row in topology['objects']}), 3)
        self.assertEqual(len(topology['shared_mesh_groups']), 1)
        self.assertEqual(len(topology['shared_mesh_groups'][0]['objects']), 2)
        self.assertEqual(topology['aggregate_analyzed_domains']['vertices'], 48)
        self.assertEqual(len(result['outputs']), 12)
        self.assertEqual({ref['object_id'] for ref in result['outputs']}, {obj.object_id for obj in objects})

    def test_exact_object_filter_runs_only_requested_instance(self):
        root, source, job, objects, removed, bpy, gm, r = self.setup_scene()
        objects[0].fid = objects[1].fid = 'PATTERN'
        r['params']['object_ids'] = [objects[1].object_id]
        with patch.dict(sys.modules, {'bpy': bpy, 'hardsurface.ops.geometry': gm}):
            result = t.execute(r, job)
        self.assertEqual(result['topology']['selection']['object_ids'], [objects[1].object_id])
        self.assertEqual(result['topology']['selection']['instances_count'], 1)
        self.assertEqual(len(result['outputs']), 4)

    def test_source_identity_checked_before_blender(self):
        root, source, job, objects, removed, bpy, gm, r = self.setup_scene()
        r['params']['source']['expected_sha256'] = '0' * 64
        with self.assertRaises(RuntimeFailure) as error:
            t.execute(r, job)
        self.assertEqual(error.exception.code, 'SOURCE_CHANGED')
        self.assertEqual(list(job.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
