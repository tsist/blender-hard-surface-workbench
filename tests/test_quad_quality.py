"""Pure-Python topology gates; these are not Blender runtime qualification."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from hardsurface.io import RuntimeFailure
from hardsurface import quad_quality as q


def cube():
    vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return vertices, faces


def annulus(segments=16):
    vertices = []
    for z, radius in ((0, 2), (0, 1), (1, 2), (1, 1)):
        vertices.extend((radius * math.cos(i * math.tau / segments),
                         radius * math.sin(i * math.tau / segments), z) for i in range(segments))
    faces = []
    n = segments
    for i in range(n):
        j = (i + 1) % n
        faces.extend(((i, j, 2*n+j, 2*n+i), (n+j, n+i, 3*n+i, 3*n+j),
                      (j, i, n+i, n+j), (2*n+i, 2*n+j, 3*n+j, 3*n+i)))
    return vertices, faces


def provenance(faces, **extra):
    return [{'feature_id': 'body', 'surface_id': 'surface-%d' % i,
             'construction': 'structured_quad', **extra} for i in range(len(faces))]


def reason(fi, entries, **overrides):
    return {'face_index': fi, 'feature_id': entries[fi]['feature_id'],
            'surface_id': entries[fi]['surface_id'], 'reason_code': 'corner_transition',
            'explanation': 'The specifically reviewed corner closes an odd local transition.',
            'quad_alternative_rejected': 'The tested quad alternative crosses the frozen corner boundary.',
            'approval_id': 'test-only-case-approval', **overrides}


def inspect(vertices, faces, **kwargs):
    kwargs.setdefault('face_provenance', provenance(faces))
    return q.validate_mesh(vertices, faces, **kwargs)


class ValidMeshTests(unittest.TestCase):
    def test_closed_cube_all_quads_and_finite_json(self):
        v, f = cube()
        r = inspect(v, f, include_face_metrics=True, identity={'object_id': 'cube-object'})
        self.assertTrue(r['passed'], r['findings'])
        self.assertEqual(r['counts']['quads'], 6)
        self.assertEqual(r['counts']['triangles'], 0)
        self.assertEqual(r['counts']['edges'], 12)
        self.assertEqual(r['metrics']['minimum_face_area_m2'], 1)
        self.assertEqual(r['metrics']['minimum_corner_angle_degrees'], 90)
        self.assertEqual(len(r['face_metrics']), 6)
        self.assertIn('not user-approved', r['policy_status'])
        json.dumps(r, allow_nan=False)

    def test_structured_closed_hole_annulus(self):
        v, f = annulus()
        r = inspect(v, f)
        self.assertTrue(r['passed'], r['findings'])
        self.assertEqual(r['counts']['quads'], 64)
        self.assertEqual(r['counts']['boundary_edges'], 0)
        self.assertLess(r['metrics']['maximum_quad_warpage_degrees'], 1e-10)

    def test_open_patch_requires_explicit_open_policy(self):
        v, f = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2, 3)]
        self.assertFalse(inspect(v, f)['passed'])
        r = inspect(v, f, policy={'require_closed': False})
        self.assertTrue(r['passed'], r['findings'])
        self.assertEqual(r['counts']['boundary_edges'], 4)

    def test_translation_and_uniform_scale_preserve_validity(self):
        v, f = cube()
        moved = [tuple(x * .01 + shift for x, shift in zip(point, (100, -200, 300))) for point in v]
        r = inspect(moved, f)
        self.assertTrue(r['passed'], r['findings'])
        self.assertAlmostEqual(r['metrics']['minimum_edge_m'], .01)

    def test_moderate_aspect_not_rejected(self):
        v = [(0, 0, 0), (.01, 0, 0), (.01, .00025, 0), (0, .00025, 0)]
        r = inspect(v, [(0, 1, 2, 3)], policy={'require_closed': False})
        self.assertTrue(r['passed'], r['findings'])
        self.assertAlmostEqual(r['metrics']['maximum_aspect_ratio'], 40)

    def test_support_band_is_bounded_not_blanket_whitelist(self):
        f = [(0, 1, 2, 3)]
        v = [(0, 0, 0), (.02, 0, 0), (.02, .00025, 0), (0, .00025, 0)]
        ordinary = inspect(v, f, policy={'require_closed': False})
        self.assertIn('ASPECT_RATIO_EXCEEDED', ordinary['finding_counts'])
        tagged = inspect(v, f, face_provenance=provenance(f, support_band=True), policy={'require_closed': False})
        self.assertTrue(tagged['passed'], tagged['findings'])
        v[1], v[2] = (.1, 0, 0), (.1, .00025, 0)
        bad = inspect(v, f, face_provenance=provenance(f, support_band=True), policy={'require_closed': False})
        self.assertIn('ASPECT_RATIO_EXCEEDED', bad['finding_counts'])

    def test_input_arrays_and_metadata_not_mutated(self):
        v, f = cube()
        p, config = provenance(f), {'min_edge_m': .001}
        before = copy.deepcopy((v, f, p, config))
        inspect(v, f, face_provenance=p, policy=config)
        self.assertEqual((v, f, p, config), before)


class GeometryFailureTests(unittest.TestCase):
    def codes(self, vertices, faces, **kwargs):
        kwargs.setdefault('policy', {'require_closed': False})
        return inspect(vertices, faces, **kwargs)['finding_counts']

    def test_ngon_hard_failure_without_triangulation(self):
        v = [(math.cos(i * math.tau / 5), math.sin(i * math.tau / 5), 0) for i in range(5)]
        r = inspect(v, [tuple(range(5))], policy={'require_closed': False})
        self.assertIn('NGON_FORBIDDEN', r['finding_counts'])
        self.assertEqual(r['counts']['ngons'], 1)
        self.assertEqual(r['counts']['triangles'], 0)

    def test_invalid_indices_and_bool_indices(self):
        v, _ = cube()
        for f in [[(0, 1, 2, 999)], [(0, 1, 2, -1)], [(0, 1, 2, True)], [(0, 1, 2, 3.0)]]:
            with self.subTest(f=f):
                self.assertIn('INVALID_VERTEX_INDEX', self.codes(v, f))

    def test_repeated_vertex_and_duplicate_faces(self):
        v, f = cube()
        self.assertIn('REPEATED_FACE_VERTEX', self.codes(v, [(0, 1, 1, 3)]))
        f.append(tuple(reversed(f[0])))
        self.assertIn('DUPLICATE_FACE', self.codes(v, f))

    def test_nonfinite_malformed_and_out_of_bound_coordinates(self):
        for bad in ((float('nan'), 0, 0), (float('inf'), 0, 0), (0, 0),
                    (True, 0, 0), (10**400, 0, 0), ('0', 0, 0)):
            v, f = cube()
            v[0] = bad
            r = inspect(v, f)
            self.assertIn('INVALID_VERTEX_COORDINATE', r['finding_counts'])
            json.dumps(r, allow_nan=False)

    def test_coincident_vertex_and_zero_edge(self):
        v = [(0, 0, 0), (0, 0, 0), (1, 1, 0), (0, 1, 0)]
        codes = self.codes(v, [(0, 1, 2, 3)])
        self.assertIn('DUPLICATE_VERTEX_COORDINATE', codes)
        self.assertIn('EDGE_TOO_SHORT', codes)

    def test_zero_area_collinear_quad(self):
        codes = self.codes([(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)], [(0, 1, 2, 3)])
        self.assertIn('ZERO_AREA_FACE', codes)

    def test_sliver_quad_angle_gate(self):
        v = [(0, 0, 0), (1, 0, 0), (2, .01, 0), (1, .01, 0)]
        codes = self.codes(v, [(0, 1, 2, 3)])
        self.assertIn('CORNER_ANGLE_TOO_SMALL', codes)
        self.assertIn('QUAD_CORNER_ANGLE_TOO_LARGE', codes)

    def test_avoidable_tiny_edge_even_when_above_absolute_floor(self):
        v = [(0, 0, 0), (2e-6, 0, 0), (1, 1, 0), (0, 1, 0)]
        codes = self.codes(v, [(0, 1, 2, 3)])
        self.assertIn('AVOIDABLE_TINY_EDGE', codes)
        self.assertNotIn('EDGE_TOO_SHORT', codes)

    def test_submicron_edge(self):
        v = [(0, 0, 0), (1e-7, 0, 0), (1e-7, .001, 0), (0, .001, 0)]
        self.assertIn('EDGE_TOO_SHORT', self.codes(v, [(0, 1, 2, 3)]))

    def test_bowtie_with_zero_area_and_asymmetric_nonzero_area(self):
        for fourth in ((1, 0, 0), (2, 0, 0)):
            codes = self.codes([(0, 0, 0), (1, 1, 0), (0, 1, 0), fourth], [(0, 1, 2, 3)])
            self.assertIn('SELF_CROSSING_QUAD', codes)

    def test_concave_quad(self):
        v = [(0, 0, 0), (1, 0, 0), (.2, .2, 0), (0, 1, 0)]
        self.assertIn('CONCAVE_QUAD', self.codes(v, [(0, 1, 2, 3)]))

    def test_nonplanar_quad_and_warpage(self):
        v = [(0, 0, 0), (1, 0, 0), (1, 1, .3), (0, 1, 0)]
        codes = self.codes(v, [(0, 1, 2, 3)])
        self.assertIn('QUAD_NONPLANAR', codes)
        self.assertIn('QUAD_WARPAGE_EXCEEDED', codes)

    def test_area_threshold_separate_from_short_edges(self):
        v = [(0, 0, 0), (.001, 0, 0), (.001, .001, 0), (0, .001, 0)]
        codes = self.codes(v, [(0, 1, 2, 3)], policy={'require_closed': False, 'min_face_area_m2': 2e-6})
        self.assertIn('FACE_AREA_TOO_SMALL', codes)
        self.assertNotIn('EDGE_TOO_SHORT', codes)


class ManifoldFailureTests(unittest.TestCase):
    def test_missing_cap_is_closed_mesh_failure(self):
        v, f = cube()
        r = inspect(v, f[:-1])
        self.assertIn('BOUNDARY_EDGE', r['finding_counts'])
        self.assertEqual(r['counts']['boundary_edges'], 4)

    def test_reversed_face_orientation(self):
        v, f = cube()
        f[0] = tuple(reversed(f[0]))
        r = inspect(v, f)
        self.assertEqual(r['finding_counts']['INCONSISTENT_EDGE_ORIENTATION'], 4)

    def test_edge_has_three_incident_faces(self):
        v, f = cube()
        f.append(f[0])
        self.assertIn('NONMANIFOLD_EDGE', inspect(v, f)['finding_counts'])

    def test_two_closed_shells_touching_only_at_one_vertex(self):
        v, f = cube()
        v2, f2 = cube()
        v2 = [tuple(-x for x in p) for p in v2]
        mapping = {0: 0, **{i: len(v) + i - 1 for i in range(1, 8)}}
        v.extend(v2[1:])
        f.extend(tuple(mapping[i] for i in face) for face in f2)
        r = inspect(v, f)
        self.assertIn('NONMANIFOLD_VERTEX', r['finding_counts'])
        self.assertEqual(r['counts']['nonmanifold_edges'], 0)
        self.assertEqual(r['counts']['boundary_edges'], 0)

    def test_isolated_vertex(self):
        v, f = cube()
        v.append((3, 4, 5))
        r = inspect(v, f)
        self.assertEqual(r['finding_counts']['ISOLATED_VERTEX'], 1)


class TrianglePolicyTests(unittest.TestCase):
    def split_cube(self):
        v, f = cube()
        f = [(3, 2, 1), (3, 1, 0)] + f[1:]
        p = provenance(f)
        p[0]['construction'] = p[1]['construction'] = 'reviewed_corner_transition'
        return v, f, p, [reason(0, p), reason(1, p)]

    def test_default_denies_even_individually_justified_triangles(self):
        v, f, p, reasons = self.split_cube()
        r = inspect(v, f, face_provenance=p, triangle_reasons=reasons)
        self.assertIn('TRIANGLE_BUDGET_EXCEEDED', r['finding_counts'])

    def test_explicit_budget_and_each_reason_accept_sparse_transition(self):
        v, f, p, reasons = self.split_cube()
        r = inspect(v, f, face_provenance=p, triangle_reasons=reasons, policy={'max_triangles': 2})
        self.assertTrue(r['passed'], r['findings'])
        self.assertEqual(r['counts']['justified_triangles'], 2)

    def test_one_reason_cannot_cover_other_triangle(self):
        v, f, p, reasons = self.split_cube()
        r = inspect(v, f, face_provenance=p, triangle_reasons=reasons[:1], policy={'max_triangles': 2})
        self.assertEqual(r['finding_counts']['UNJUSTIFIED_TRIANGLE'], 1)
        self.assertTrue(any(row['code'] == 'UNJUSTIFIED_TRIANGLE' and row['face_index'] == 1 for row in r['findings']))

    def test_unknown_stale_duplicate_or_mismatched_reason_fails(self):
        mutations = [('reason_code', 'because_it_looks_fine'), ('surface_id', 'another-surface'),
                     ('feature_id', 'another-feature'), ('approval_id', ''),
                     ('explanation', ''), ('quad_alternative_rejected', '')]
        for key, value in mutations:
            v, f, p, reasons = self.split_cube()
            reasons[0][key] = value
            r = inspect(v, f, face_provenance=p, triangle_reasons=reasons, policy={'max_triangles': 2})
            self.assertIn('INVALID_TRIANGLE_REASON', r['finding_counts'], key)
        v, f, p, reasons = self.split_cube()
        for extra, code in ((reason(2, p), 'STALE_TRIANGLE_REASON'), (reason(0, p), 'DUPLICATE_TRIANGLE_REASON')):
            r = inspect(v, f, face_provenance=p, triangle_reasons=reasons + [extra], policy={'max_triangles': 2})
            self.assertIn(code, r['finding_counts'])

    def test_no_fraction_policy_or_global_reason_waiver(self):
        with self.assertRaises(RuntimeFailure):
            q.quality_policy({'max_triangle_fraction': .01})
        v, f, p, _ = self.split_cube()
        r = inspect(v, f, face_provenance=p, triangle_reasons=[{'reason': 'small fraction'}], policy={'max_triangles': 2})
        self.assertEqual(r['finding_counts']['UNJUSTIFIED_TRIANGLE'], 2)

    def test_source_ngon_and_generic_triangulation_are_prohibited(self):
        for extra in ({'source_face_corners': 100}, {'construction': 'ngon_triangulation'}, {'construction': 'triangle_fan'}):
            v, f, p, reasons = self.split_cube()
            p[0].update(extra)
            r = inspect(v, f, face_provenance=p, triangle_reasons=reasons, policy={'max_triangles': 2})
            self.assertIn('TRIANGULATION_SUBSTITUTE_FORBIDDEN', r['finding_counts'])

    def test_giant_triangle_fan_rejected_even_with_fabricated_individual_reasons(self):
        n = 8
        v = [(0, 0, 0)] + [(math.cos(i * math.tau / n), math.sin(i * math.tau / n), 0) for i in range(n)]
        f = [(0, i + 1, (i + 1) % n + 1) for i in range(n)]
        p = provenance(f)
        r = inspect(v, f, face_provenance=p, triangle_reasons=[reason(i, p) for i in range(n)],
                    policy={'require_closed': False, 'max_triangles': 8})
        self.assertIn('TRIANGLE_PATCH_TOO_LARGE', r['finding_counts'])
        self.assertIn('TRIANGLE_FAN_FORBIDDEN', r['finding_counts'])

    def test_all_triangle_shell_cannot_impersonate_quad_transition(self):
        v = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        f = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]
        p = provenance(f)
        r = inspect(v, f, face_provenance=p, triangle_reasons=[reason(i, p) for i in range(4)],
                    policy={'max_triangles': 4})
        self.assertEqual(r['finding_counts'], {'TRIANGLE_PATCH_WITHOUT_QUAD_NEIGHBOR': 1})


class ContractAndEvidenceTests(unittest.TestCase):
    def test_missing_or_misaligned_provenance_fails_closed(self):
        v, f = cube()
        for p in (None, [], [{}] * 6, [None] * 6):
            if p is None or len(p) != len(f):
                with self.assertRaises(RuntimeFailure):
                    inspect(v, f, face_provenance=p)
            else:
                self.assertFalse(inspect(v, f, face_provenance=p)['passed'])

    def test_malformed_flags_and_provenance_do_not_grant_exemption(self):
        v, f = cube()
        for extra in ({'support_band': 'yes'}, {'curved': 1}, {'source_face_corners': True}, {'construction': []}):
            r = inspect(v, f, face_provenance=provenance(f, **extra))
            self.assertIn('INVALID_FACE_PROVENANCE', r['finding_counts'])

    def test_control_evaluated_domains_are_separate(self):
        v, f = cube()
        r = inspect(v, f, mesh_state='evaluated')
        self.assertEqual(r['mesh_state'], 'evaluated')
        with self.assertRaises(RuntimeFailure):
            inspect(v, f, mesh_state='loop_triangles')

    def test_compact_summary_and_full_finding_sink(self):
        v, f = cube()
        v.extend((5 + i, 0, 0) for i in range(20))
        findings = []
        r = inspect(v, f, policy={'max_finding_examples': 2}, finding_sink=findings.append)
        self.assertEqual(r['finding_count'], 20)
        self.assertEqual(len(r['findings']), 2)
        self.assertEqual(r['findings_truncated'], 18)
        self.assertEqual(len(findings), 20)
        self.assertEqual({row['vertex_indices'][0] for row in findings}, set(range(8, 28)))
        json.dumps(r, allow_nan=False)

    def test_feature_face_and_edge_locations(self):
        v = [(0, 0, 0), (1e-7, 0, 0), (1e-7, .001, 0), (0, .001, 0)]
        r = inspect(v, [(0, 1, 2, 3)], policy={'require_closed': False}, identity={'object_id': 'body-001'})
        row = next(row for row in r['findings'] if row['code'] == 'EDGE_TOO_SHORT')
        self.assertEqual((row['object_id'], row['feature_id'], row['surface_id'], row['face_index']),
                         ('body-001', 'body', 'surface-0', 0))
        self.assertEqual(row['vertex_indices'], [0, 1])

    def test_identity_cannot_spoof_finding_code_or_state(self):
        v, f = cube()
        r = inspect(v, f[:-1], identity={'code': 'success', 'mesh_state': 'pretend'})
        self.assertTrue(all(row['code'] != 'success' and row['mesh_state'] == 'control' for row in r['findings']))

    def test_policy_rejects_nonsense_and_looser_quality_limits(self):
        for policy in ({'min_edge_m': 0}, {'max_aspect_ratio': 1000}, {'max_support_band_aspect_ratio': 1000},
                       {'min_corner_angle_degrees': .1}, {'max_quad_warpage_degrees': 90},
                       {'max_triangles': 33}, {'max_triangles': True}, {'max_finding_examples': 0},
                       {'min_edge_m': float('nan')}, {'min_edge_m': 10**400}, {'require_closed': 'no'}):
            with self.subTest(policy=policy), self.assertRaises(RuntimeFailure):
                q.quality_policy(policy)

    def test_input_limits_before_traversing_geometry(self):
        v, f = cube()
        with patch.dict(q.LIMITS, {'vertices': 7}):
            with self.assertRaises(RuntimeFailure) as error:
                inspect(v, f)
            self.assertEqual(error.exception.code, 'QUAD_QUALITY_LIMIT')
        with patch.dict(q.LIMITS, {'corners': 10}):
            with self.assertRaises(RuntimeFailure):
                inspect(v, f)

    def test_empty_mesh_and_short_faces_fail(self):
        self.assertFalse(inspect([], [])['passed'])
        self.assertIn('INVALID_FACE_SIZE', inspect([(0, 0, 0), (1, 0, 0)], [(0, 1)])['finding_counts'])

    def test_assertion_carries_structured_failure(self):
        v, f = cube()
        r = q.assert_mesh_quality(v, f, face_provenance=provenance(f))
        self.assertTrue(r['passed'])
        with self.assertRaises(RuntimeFailure) as error:
            q.assert_mesh_quality(v, f[:-1], face_provenance=provenance(f[:-1]))
        self.assertEqual(error.exception.code, 'QUAD_QUALITY_FAILED')
        self.assertFalse(error.exception.details['report']['passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
