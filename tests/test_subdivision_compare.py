"""Neutral HOST evidence fixtures; no Blender execution or native qualification."""
from copy import deepcopy
import hashlib
import json
import math
import random
import unittest
from unittest.mock import patch

from hardsurface import subdivision_compare as comparison
from hardsurface.io import RuntimeFailure
from hardsurface.structure_kernel import fingerprint


PROFILE = {
    'name': 'neutral_source', 'type': 'SUBSURF', 'subdivision_type': 'CATMULL_CLARK',
    'levels': 2, 'render_levels': 2, 'quality': 3, 'uv_smooth': 'PRESERVE_BOUNDARIES',
    'boundary_smooth': 'ALL', 'use_creases': True, 'use_limit_surface': True,
    'use_custom_normals': False, 'show_viewport': True, 'show_render': True,
    'show_in_editmode': True, 'show_on_cage': False, 'show_only_control_edges': True,
    'use_apply_on_spline': False, 'use_pin_to_last': False,
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def seal(data):
    """Recompute declared fixture hashes so structural rejection is exercised."""
    semantic = data['bound_semantic_regions']
    transport = semantic['transport']
    source = transport['source']
    data['local_geometry_sha256'] = digest({key: data[key] for key in ('vertices', 'edges', 'polygons')})
    source['parent_face_ids_by_slot_sha256'] = fingerprint(semantic['source_face_ids_by_parent_slot'])
    source['parent_provenance_by_slot_sha256'] = fingerprint(semantic['source_provenance_by_parent_slot'])
    source['parent_surface_by_slot_sha256'] = fingerprint(semantic['source_surface_labels_by_parent_slot'])
    transport['evaluated_parent_slots_sha256'] = fingerprint(semantic['evaluated_face_parent_slots'])
    transport['evaluated_surface_labels_sha256'] = fingerprint(semantic['evaluated_face_surface_labels'])
    transport['bore_face_indices_sha256'] = fingerprint(semantic['bore_evaluated_face_indices'])
    semantic['source_evidence_sha256'] = digest(source)
    return data


def geometry(vertices, faces, *, level=0, parents=None, source_tag='neutral', loops=None, alternate=False):
    parents = list(range(len(faces))) if parents is None else parents
    count = max(parents) + 1
    edges = sorted({tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])})
    triangles = []
    for fi, (a, b, c, d) in enumerate(faces):
        rows = ([a, b, d], [b, c, d]) if alternate else ([a, b, c], [a, c, d])
        triangles.extend({'vertices': row, 'polygon_index': fi} for row in rows)
    binding = {'schema_version': 'native-control-structure/1.0', 'mesh_state': 'control',
               'source_binding_sha256': digest(source_tag)}
    source = {'identity_binding_status': 'bound', 'external_registry_verified': True,
              'full_per_face_provenance_bound': True, 'source_face_count': count,
              'source_schema': comparison.SPARSE_SOURCE_SCHEMA, 'native_binding': deepcopy(binding),
              'actual_source_modifier': deepcopy(PROFILE)}
    semantic = {
        'schema_version': 'sparse-source-regions/1.0', 'source_schema': comparison.SPARSE_SOURCE_SCHEMA,
        'source_binding': binding, 'source_face_count': count,
        'source_face_ids_by_parent_slot': ['neutral-face:' + str(i) for i in range(count)],
        'source_provenance_by_parent_slot': [{'surface_role': 'neutral_plane', 'feature_id': 'fixture'} for _ in range(count)],
        'source_surface_labels_by_parent_slot': [0] * count,
        'evaluated_face_parent_slots': list(parents), 'evaluated_face_surface_labels': [0] * len(faces),
        'bore_source_parent_slots': [], 'bore_evaluated_face_indices': [],
        'source_control_loops': deepcopy(loops or []),
        'transport': {'status': 'validated', 'level': level, 'source_schema': comparison.SPARSE_SOURCE_SCHEMA,
                      'source': source, 'evaluated_faces': len(faces), 'evaluated_bore_faces': 0,
                      'expected_descendants_per_source_face': 4 ** level,
                      'all_source_face_descendant_counts_match': True, 'parent_surface_labels_match': True,
                      'bore_parent_membership_matches': True,
                      'parent_patch_topology': {'status': 'pass', 'connected_disk_regions': count,
                                               'oriented_source_neighbour_cycles_match': True,
                                               'boundary_edges_per_source_quad': 4 * 2 ** level}},
    }
    data = {
        'schema_version': '1.0', 'kind': 'subdivision_evaluated_geometry', 'level': level,
        'source_sha256': digest(source_tag), 'derived_triangles_included': True,
        'vertices_coordinate_space': 'object_local', 'vertices_coordinate_units': 'm',
        'world_vertices_mm_coordinate_space': 'world', 'world_vertices_mm_coordinate_units': 'mm',
        'matrix_world': [[float(i == j) for j in range(4)] for i in range(4)],
        'vertices': [[float(v) / 1000 for v in row] for row in vertices],
        'world_vertices_mm': [[float(v) for v in row] for row in vertices],
        'edges': [list(row) for row in edges], 'polygons': [list(row) for row in faces],
        'loop_triangles': triangles, 'bound_semantic_regions': semantic,
    }
    return seal(data)


def grid(xs=(0., 1.), ys=(0., 1.), *, z=0., level=0, source_tag='neutral', alternate=False):
    nx, ny = len(xs) - 1, len(ys) - 1
    vertices = [(x, y, z) for y in ys for x in xs]
    faces = []
    parents = []
    stride = 2 ** level
    if nx % stride or ny % stride:
        raise ValueError('Fixture must contain complete parent patches')
    for j in range(ny):
        for i in range(nx):
            v = j * (nx + 1) + i
            faces.append([v, v + 1, v + nx + 2, v + nx + 1])
            parents.append((j // stride) * (nx // stride) + i // stride)
    return geometry(vertices, faces, level=level, parents=parents, source_tag=source_tag, alternate=alternate)


def cube():
    return geometry([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
                    [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
                     [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]])


class MetricTests(unittest.TestCase):
    def test_identical_surface_with_changed_triangulation_is_zero(self):
        before, after = grid(), grid(alternate=True)
        report = comparison.compare_exports(before, after)
        self.assertAlmostEqual(report['sampled_bidirectional_maximum_mm'], 0.)
        self.assertEqual(report['execution']['status'], 'succeeded')
        self.assertEqual(report['shape_review']['status'], 'pending')
        self.assertEqual(report['shape_review']['shape_preservation'], 'not_asserted')
        self.assertFalse(report['method']['continuous_hausdorff'])
        self.assertFalse(report['method']['index_correspondence_used'])
        self.assertIsNone(report['reference_tolerance_mm'])

    def test_normal_offset_detected_both_directions_and_reference_never_passes_shape(self):
        report = comparison.compare_exports(grid(), grid(z=.25), reference_tolerance_mm=.1)
        self.assertAlmostEqual(report['sampled_bidirectional_maximum_mm'], .25)
        for row in report['directions'].values():
            for kind in ('vertices', 'triangle_centroids'):
                self.assertAlmostEqual(row[kind]['mean_mm'], .25)
                self.assertAlmostEqual(row[kind]['rms_mm'], .25)
                self.assertEqual(row[kind]['samples_above_reference_tolerance'], row[kind]['samples'])
        self.assertFalse(report['shape_review']['reference_tolerance_is_acceptance_gate'])
        self.assertEqual(report['shape_review']['status'], 'pending')

    def test_maximum_witness_locates_source_and_closest_target_without_correspondence_claim(self):
        before, after = grid(), grid(z=.25)
        report = comparison.compare_exports(before, after)
        for direction, sign in (('before_to_after', 1), ('after_to_before', -1)):
            for kind in ('vertices', 'triangle_centroids'):
                stats = report['directions'][direction][kind]
                witness = stats['maximum_witness']
                self.assertEqual(witness['distance_mm'], stats['maximum_mm'])
                self.assertEqual(len(witness['source_world_mm']), 3)
                self.assertEqual(len(witness['target_triangle_world_mm']), 3)
                self.assertAlmostEqual(witness['nearest_separation_vector_mm'][2], sign * .25)
                self.assertLess(abs(witness['nearest_separation_vector_mm'][0]), 1e-14)
                self.assertIn('not a corresponding material-point displacement', witness['semantics'])
                point = witness['closest_target_point_world_mm']
                self.assertLess(comparison._point_triangle_d2(point, witness['target_triangle_world_mm']), 1e-28)

    def test_tangential_planar_resampling_and_nonuniform_changed_topology_are_zero(self):
        before = grid((0., .3, 1.), (0., .6, 1.))
        after = grid((0., .1, .8, 1.), (0., .4, .9, 1.), alternate=True)
        report = comparison.compare_exports(before, after)
        self.assertLess(report['sampled_bidirectional_maximum_mm'], 1e-14)
        left, right = report['directions'].values()
        self.assertEqual(left['vertices']['samples'], 9)
        self.assertEqual(right['vertices']['samples'], 16)
        self.assertEqual(left['coverage']['source_parents'], 4)
        self.assertEqual(right['coverage']['source_parents'], 9)
        self.assertFalse(report['method']['parent_slots_correspond_across_models'])

    def test_closed_triangle_distance_respects_edges_and_corners(self):
        tri = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
        distance = comparison._point_triangle_d2
        self.assertEqual(distance((.2, .2, 3.), tri), 9.)
        self.assertEqual(distance((2., 0., 0.), tri), 1.)
        self.assertEqual(distance((-1., -1., 0.), tri), 2.)
        self.assertAlmostEqual(distance((2., 2., 0.), tri), 4.5)
        self.assertEqual(distance((.5, .5, 0.), tri), 0.)

    def test_whole_target_search_does_not_restrict_to_parent_or_surface_role(self):
        before, after = grid(), grid((0., .5, 1.), (0., .5, 1.))
        for i, row in enumerate(after['bound_semantic_regions']['source_provenance_by_parent_slot']):
            row['surface_role'] = 'different_role_' + str(i)
        seal(after)
        report = comparison.compare_exports(before, after)
        self.assertLess(report['sampled_bidirectional_maximum_mm'], 1e-14)
        self.assertEqual(report['directions']['before_to_after']['coverage']['target_scope'], 'whole_geometry')

    def test_planar_extension_is_detected_bidirectionally(self):
        report = comparison.compare_exports(grid(), grid((0., 2.), (0., 1.)))
        self.assertEqual(report['directions']['before_to_after']['vertices']['maximum_mm'], 0.)
        self.assertEqual(report['directions']['after_to_before']['vertices']['maximum_mm'], 1.)

    def test_complete_parent_coverage_with_own_l0_poles_and_loop(self):
        base = grid()
        base['bound_semantic_regions']['source_control_loops'] = [
            {'role': 'neutral_boundary', 'vertex_indices': [0, 1, 3, 2]}]
        evaluated = grid((0., .5, 1.), (0., .5, 1.), level=1)
        evaluated['bound_semantic_regions']['source_control_loops'] = deepcopy(base['bound_semantic_regions']['source_control_loops'])
        report = comparison.compare_exports(evaluated, evaluated,
                                            before_source_geometry=base, after_source_geometry=base)
        for row in report['directions'].values():
            self.assertEqual(row['coverage']['sampled_source_parents'], 1)
            self.assertEqual(row['parent_regions'][0]['triangle_centroids']['samples'], 8)
            self.assertEqual(row['pole_and_loop_neighborhoods']['status'], 'derived_from_own_L0')
            self.assertEqual(len(row['pole_and_loop_neighborhoods']['poles']), 4)
            self.assertEqual(row['semantic_domains']['loop:neutral_boundary']['triangle_centroids']['samples'], 8)
            self.assertEqual(row['semantic_domains']['pole:source_vertex:0']['own_parent_slots'], [0])

    def test_absent_l0_does_not_claim_pole_coverage(self):
        data = grid((0., .5, 1.), (0., .5, 1.), level=1)
        report = comparison.compare_exports(data, data)
        self.assertFalse(report['directions']['before_to_after']['pole_and_loop_neighborhoods']['pole_neighborhoods_complete'])
        self.assertEqual(report['shape_review']['status'], 'pending')

    def test_boundary_status_is_measured_and_open_fixture_is_not_closed(self):
        for data, count, closed in ((grid(), 4, False), (cube(), 0, True)):
            report = comparison.compare_exports(data, data)
            topology = report['directions']['before_to_after']['coverage']['source_topology']
            self.assertEqual(topology['boundary_edge_count'], count)
            self.assertIs(topology['closed_by_edge_incidence'], closed)

    def test_bvh_matches_exhaustive_true_triangle_distances(self):
        data = grid(tuple(i / 6 for i in range(7)), tuple(i / 5 for i in range(6)))
        budget = comparison._Budget(None, None)
        mesh = comparison._Mesh(data, budget)
        index = comparison._TriangleIndex(mesh, budget)
        generator = random.Random(714)
        for _ in range(50):
            point = tuple(generator.uniform(-.5, 1.5) for _ in range(3))
            expected = math.sqrt(min(comparison._point_triangle_d2(point, tri) for tri in mesh.triangles))
            self.assertAlmostEqual(index.distance(point), expected, places=13)

    def test_input_exports_remain_unchanged(self):
        before, after = grid(), grid(alternate=True)
        snapshots = deepcopy((before, after))
        comparison.compare_exports(before, after)
        self.assertEqual((before, after), snapshots)


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, data, other=None, **kwargs):
        with self.assertRaises(RuntimeFailure) as context:
            comparison.compare_exports(data, other or grid(), **kwargs)
        self.assertEqual(context.exception.code, 'SUBDIVISION_COMPARE_INVALID')

    def test_schema_level_units_and_profile_mismatch_fail(self):
        for field, value in [('schema_version', '9.0'), ('kind', 'generic_mesh'),
                             ('level', True), ('world_vertices_mm_coordinate_units', 'm'),
                             ('world_vertices_mm_coordinate_space', 'local'),
                             ('derived_triangles_included', False)]:
            data = grid(); data[field] = value
            with self.subTest(field=field): self.assert_invalid(data)
        high = grid((0., .5, 1.), (0., .5, 1.), level=1)
        self.assert_invalid(high)
        data = grid()
        data['bound_semantic_regions']['transport']['source']['actual_source_modifier']['quality'] = 4
        seal(data); self.assert_invalid(data)

    def test_incomplete_profile_and_unknown_resource_limits_fail(self):
        data = grid()
        del data['bound_semantic_regions']['transport']['source']['actual_source_modifier']['use_creases']
        seal(data); self.assert_invalid(data)
        self.assert_invalid(grid(), limits={'unknown': 4})
        self.assert_invalid(grid(), limits={'max_samples': True})
        self.assert_invalid(grid(), reference_tolerance_mm=float('nan'))
        self.assert_invalid(grid(), reference_tolerance_mm=-1)
        data = grid()
        data['bound_semantic_regions']['transport']['source']['actual_source_modifier']['use_creases'] = 1
        seal(data); self.assert_invalid(data)

    def test_nonfinite_boolean_or_malformed_vertices_fail(self):
        for value in (float('nan'), float('inf'), True, '1'):
            data = grid(); data['world_vertices_mm'][0][0] = value
            with self.subTest(value=value): self.assert_invalid(data)
        data = grid(); data['world_vertices_mm'][0].append(1); self.assert_invalid(data)
        data = grid(); data['world_vertices_mm'][0][0] = .2; self.assert_invalid(data)

    def test_degenerate_triangle_and_invalid_indices_fail(self):
        data = grid()
        data['world_vertices_mm'][1] = list(data['world_vertices_mm'][0])
        data['vertices'][1] = list(data['vertices'][0]); seal(data); self.assert_invalid(data)
        for index in (True, -1, 10, 0.0):
            data = grid(); data['loop_triangles'][0]['vertices'][0] = index
            with self.subTest(index=index): self.assert_invalid(data)

    def test_missing_duplicate_misassigned_or_reversed_triangles_fail(self):
        data = grid(); data['loop_triangles'].pop(); self.assert_invalid(data)
        data = grid(); data['loop_triangles'][1] = deepcopy(data['loop_triangles'][0]); self.assert_invalid(data)
        data = grid((0., .5, 1.)); data['loop_triangles'][0]['polygon_index'] = 1; self.assert_invalid(data)
        data = grid(); data['loop_triangles'][0]['vertices'].reverse(); self.assert_invalid(data)
        data = grid(); data['loop_triangles'][0]['extra'] = 0; self.assert_invalid(data)

    def test_missing_edges_isolated_vertices_and_geometry_digest_fail(self):
        data = grid(); data['edges'].pop(); seal(data); self.assert_invalid(data)
        data = grid(); data['vertices'].append([2., 2., 2.]); data['world_vertices_mm'].append([2000., 2000., 2000.])
        seal(data); self.assert_invalid(data)
        data = grid(); data['local_geometry_sha256'] = '0' * 64; self.assert_invalid(data)

    def test_semantic_schema_nonfinite_and_parent_digest_fail(self):
        data = grid(); data['bound_semantic_regions']['schema_version'] = 'unknown'; self.assert_invalid(data)
        data = grid(); data['bound_semantic_regions']['transport']['status'] = 'not_run'; self.assert_invalid(data)
        data = grid(); data['bound_semantic_regions']['transport']['evaluated_parent_slots_sha256'] = '0' * 64
        self.assert_invalid(data)
        data = grid(); data['bound_semantic_regions']['source_provenance_by_parent_slot'][0]['bad'] = float('inf')
        self.assert_invalid(data)

    def test_semantic_parent_coverage_and_labels_fail_even_when_rehashed(self):
        for replacement in ([True], [1], [], [0, 0]):
            data = grid(); data['bound_semantic_regions']['evaluated_face_parent_slots'] = replacement
            seal(data)
            with self.subTest(parents=replacement): self.assert_invalid(data)
        data = grid(); data['bound_semantic_regions']['evaluated_face_surface_labels'] = [1]
        seal(data); self.assert_invalid(data)
        data = grid((0., .5, 1.))
        data['bound_semantic_regions']['source_face_ids_by_parent_slot'] = ['duplicate', 'duplicate']
        seal(data); self.assert_invalid(data)

    def test_disconnected_or_wrong_shaped_parent_patch_fails_even_with_equal_counts(self):
        data = grid((0., .25, .5, .75, 1.), (0., .5, 1.), level=1)
        # Four descendants each, but two disconnected column pairs per parent.
        data['bound_semantic_regions']['evaluated_face_parent_slots'] = [0, 1, 0, 1, 0, 1, 0, 1]
        seal(data); self.assert_invalid(data, data)

    def test_invalid_bore_membership_fails(self):
        data = grid(); data['bound_semantic_regions']['bore_source_parent_slots'] = [0]
        seal(data); self.assert_invalid(data)

    def test_wrong_l0_and_false_control_loop_are_rejected(self):
        evaluated = grid((0., .5, 1.), (0., .5, 1.), level=1)
        self.assert_invalid(evaluated, evaluated, before_source_geometry=grid(source_tag='foreign'))
        data = grid()
        data['bound_semantic_regions']['source_control_loops'] = [{'role': 'fake', 'vertex_indices': [0, 1, 2]}]
        self.assert_invalid(data)

    def test_nonmanifold_or_reversed_shared_polygon_edges_fail(self):
        data = grid((0., .5, 1.))
        data['polygons'][1].reverse()
        data['loop_triangles'][2]['vertices'].reverse()
        data['loop_triangles'][3]['vertices'].reverse()
        seal(data); self.assert_invalid(data)


class ResourceTests(unittest.TestCase):
    def assert_limited(self, limits):
        with self.assertRaises(RuntimeFailure) as context:
            comparison.compare_exports(grid(), grid(), limits=limits)
        self.assertEqual(context.exception.code, 'SUBDIVISION_COMPARE_LIMIT')

    def test_counts_work_samples_and_report_limits_fail_without_partial_success(self):
        for limits in ({'max_vertices': 3}, {'max_triangles': 1}, {'max_samples': 11},
                       {'max_triangle_tests': 1}, {'max_bvh_nodes': 1},
                       {'max_domain_memberships': 1}, {'max_report_regions': 1}):
            with self.subTest(limits=limits): self.assert_limited(limits)

    def test_deadline_checked_during_work(self):
        clock = iter([0., 0., 2.] + [2.] * 100)
        with patch.object(comparison.time, 'monotonic', side_effect=lambda: next(clock)):
            self.assert_limited({'max_wall_seconds': 1.})

    def test_cancel_callback_truthy_or_job_exception_propagates(self):
        with self.assertRaises(RuntimeFailure) as context:
            comparison.compare_exports(grid(), grid(), check_cancel=lambda: True)
        self.assertEqual(context.exception.code, 'SUBDIVISION_COMPARE_CANCELLED')
        calls = []
        def cancelled():
            calls.append(1)
            return len(calls) > 4
        with self.assertRaises(RuntimeFailure) as context:
            comparison.compare_exports(grid(), grid(), check_cancel=cancelled)
        self.assertEqual(context.exception.code, 'SUBDIVISION_COMPARE_CANCELLED')
        class JobCancelled(Exception): pass
        def owning_job(): raise JobCancelled()
        with self.assertRaises(JobCancelled):
            comparison.compare_exports(grid(), grid(), check_cancel=owning_job)

    def test_moderate_grid_uses_bvh_within_explicit_bounded_work(self):
        coordinates = tuple(i / 12 for i in range(13))
        data = grid(coordinates, coordinates)
        samples = 2 * (len(data['world_vertices_mm']) + len(data['loop_triangles']))
        report = comparison.compare_exports(data, data, limits={'max_samples': samples,
                                                                'max_triangle_tests': 20000,
                                                                'max_wall_seconds': 10.})
        self.assertEqual(report['resources']['samples'], samples)
        self.assertLess(report['resources']['triangle_tests'], samples * len(data['loop_triangles']) / 4)
        self.assertLess(report['sampled_bidirectional_maximum_mm'], 1e-14)


if __name__ == '__main__':
    unittest.main()
