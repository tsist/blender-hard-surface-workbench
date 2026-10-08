"""Host-only synthetic measurement fixtures, never native SubD qualification."""
import copy
import math
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface.semantic_bore_measure import semantic_bore_samples
from hardsurface.subd_panel_measure import measure_panel


def fixture(*, jitter=0., n=96, zrows=(0., 2.75, 5.5)):
    # Sharp square annular solid is an exact measure_panel distance-function
    # domain fixture. It is not an authored SubD cage or production design.
    p = {'size': [40., 40.], 'corner_radius': 0., 'edge_bevel': 0., 'center': [0., 0.],
         'z_min': 0., 'z_max': 5.5, 'holes': [{'kind': 'circle', 'center': [0., 0.], 'radius': 8.5}]}
    vertices = []; index = {}
    for k, z in enumerate(zrows):
        for ring in ((0, 1, 2) if k in (0, len(zrows)-1) else (0, 2)):
            for i in range(n):
                co, si = math.cos(math.tau*i/n), math.sin(math.tau*i/n)
                radius = (8.5, 9.2, 20./max(abs(co), abs(si)))[ring]
                index[k, ring, i] = len(vertices)
                vertices.append([radius*co, radius*si,
                                 z+(jitter if k == len(zrows)-1 and ring == 1 and i % 2 == 0 else 0.)])
    faces = []; bore = []; support_top = []; support_bottom = []
    for i in range(n):
        j = (i+1) % n
        for ring in range(2):
            if ring == 0: support_bottom.append(len(faces))
            faces.append([index[0, ring, i], index[0, ring, j], index[0, ring+1, j], index[0, ring+1, i]])
            if ring == 0: support_top.append(len(faces))
            top = len(zrows)-1
            faces.append([index[top, ring, i], index[top, ring+1, i], index[top, ring+1, j], index[top, ring, j]])
        for k in range(len(zrows)-1):
            bore.append(len(faces))
            faces.append([index[k, 0, i], index[k+1, 0, i], index[k+1, 0, j], index[k, 0, j]])
            faces.append([index[k, 2, i], index[k, 2, j], index[k+1, 2, j], index[k+1, 2, i]])
    return vertices, faces, p, bore, {'index': index, 'support_top': support_top, 'support_bottom': support_bottom}


def semantic(v, f, p, bore, tolerance=.05):
    return measure_panel(v, f, p, tolerance_mm=tolerance, bore_face_indices=bore, measurement_profile='semantic_bore_v1')


class SemanticBoreMeasurementTests(unittest.TestCase):
    def test_complete_exact_host_fixture_passes_both_requested_tolerances(self):
        v, f, p, bore, _ = fixture()
        for tolerance in (.05, .10):
            r = semantic(v, f, p, bore, tolerance)
            self.assertEqual(r['status'], 'pass')
            self.assertEqual(r['measurement_profile'], 'semantic_bore_v1')
            self.assertEqual(r['qualification_status'], 'not_established_by_measurement_alone')
            e = r['semantic_bore_membership']
            self.assertEqual(e['boundary_cycle_count'], 2)
            self.assertEqual(e['euler_characteristic'], 0)
            self.assertEqual(e['face_count'], 192)
            self.assertEqual(e['source_binding'], 'caller_supplied_membership_requires_external_source_binding')
            self.assertEqual(r['hole_slices']['top_rim']['selection_method'], 'topological_boundary_cycle')
            self.assertEqual(r['hole_slices']['middle_wall']['selection_method'], 'semantic_wall_vertices_at_station')

    def test_flat_support_jitter_cannot_contaminate_semantic_rim(self):
        for jitter in (0., .5e-6, 1.1e-6, 3.23e-6):
            v, f, p, bore, _ = fixture(jitter=jitter)
            r = semantic(v, f, p, bore)
            self.assertEqual(r['status'], 'pass')
            for row in r['hole_slices'].values():
                self.assertEqual(row['vertices'], 96)
                self.assertLess(row['max_radial_error_mm'], 1e-12)
            legacy = measure_panel(v, f, p, tolerance_mm=.05)
            self.assertEqual(legacy['measurement_profile'], 'legacy_geometry_diagnostic_v0')
            self.assertEqual(legacy['semantic_bore_membership']['status'], 'legacy_diagnostic_only')
            self.assertEqual(legacy['hole_slices']['top_rim']['vertices'], 192 if jitter >= 1.1e-6 else 96)
            self.assertEqual(legacy['hole_slices']['top_rim']['status'], 'fail' if jitter >= 1.1e-6 else 'pass')
            # The repair changes membership, never geometry or existing probes.
            for key in ('sample_count', 'maximum_sampled_surface_distance_mm', 'proximity_status'):
                self.assertEqual(r[key], legacy[key])
            for key in ('actual_volume_mm3', 'nominal_volume_mm3', 'volume_screen_limit_mm3', 'actual_bounds_mm', 'bbox_status'):
                self.assertEqual(r['feature_witnesses'][key], legacy['feature_witnesses'][key])

    def test_profiles_and_membership_fail_closed(self):
        v, f, p, bore, _ = fixture()
        bad = [None, [], [bore[0]]*2, [True], [1.0], [-1], [len(f)], set(bore), {'faces': bore}]
        for indices in bad:
            with self.subTest(indices=str(indices)[:40]), self.assertRaises(RuntimeFailure):
                semantic(v, f, p, indices)
        for profile in (None, 'typo', 'legacy_geometry_diagnostic_v0'):
            with self.assertRaises(RuntimeFailure):
                measure_panel(v, f, p, tolerance_mm=.05, bore_face_indices=bore, measurement_profile=profile)
        with self.assertRaises(RuntimeFailure):
            measure_panel(v, f, p, tolerance_mm=.05, measurement_profile='semantic_bore_v2')

    def test_input_order_does_not_change_membership_or_result(self):
        v, f, p, bore, _ = fixture()
        self.assertEqual(semantic(v, f, p, bore), semantic(v, f, p, list(reversed(bore))))

    def test_missing_one_wall_face_rejected(self):
        v, f, p, bore, _ = fixture()
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore[1:])

    def test_missing_internal_face_rejected(self):
        v, f, p, bore, _ = fixture(zrows=(0., 1., 2.75, 4., 5.5))
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore[:1]+bore[2:])

    def test_only_half_height_wall_rejected(self):
        v, f, p, bore, _ = fixture()
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore[::2])

    def test_flat_neighbor_and_jittered_flat_neighbor_rejected(self):
        for jitter in (0., 1.1e-6):
            v, f, p, bore, extra = fixture(jitter=jitter)
            for indices in (bore+[extra['support_top'][0]], bore+extra['support_top'], extra['support_top']):
                with self.assertRaises(RuntimeFailure): semantic(v, f, p, indices)

    def test_balanced_wall_cap_membership_swap_rejected(self):
        v, f, p, bore, extra = fixture(jitter=1.1e-6)
        swapped = list(bore); swapped[0] = extra['support_top'][0]
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, swapped)

    def test_one_reversed_or_globally_reversed_wall_rejected(self):
        for all_faces in (False, True):
            v, f, p, bore, _ = fixture()
            for fi in (bore if all_faces else bore[:1]): f[fi].reverse()
            with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_degenerate_quad_geometry_rejected(self):
        v, f, p, bore, _ = fixture()
        v[f[bore[0]][1]] = list(v[f[bore[0]][0]])
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_actual_mesh_ring_nonclosure_rejected(self):
        v, f, p, bore, extra = fixture()
        # Remove one actual adjoining cap face without modifying selected faces.
        bad = extra['support_top'][0]
        f = f[:bad]+f[bad+1:]
        bore = [i-(i > bad) for i in bore]
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_actual_mesh_nonmanifold_wall_edge_rejected(self):
        v, f, p, bore, _ = fixture(); f.append(list(f[bore[0]]))
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_actual_mesh_reversed_adjacent_cap_rejected(self):
        v, f, p, bore, extra = fixture(); f[extra['support_top'][0]].reverse()
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_disconnected_foreign_wall_region_rejected(self):
        v, f, p, bore, _ = fixture(); offset = len(v); count = len(f)
        v += copy.deepcopy(v); f += [[i+offset for i in face] for face in f]
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore+[i+count for i in bore])

    def test_warped_actual_boundary_is_not_averaged_into_nominal_plane(self):
        v, f, p, bore, extra = fixture()
        v[extra['index'][2, 0, 0]][2] += .01
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_true_enlargement_still_fails_without_near_radius_filter(self):
        for delta in (.2, 1.5):
            v, f, p, bore, extra = fixture()
            for (k, ring, i), vi in extra['index'].items():
                if ring == 0:
                    v[vi][0] *= (8.5+delta)/8.5; v[vi][1] *= (8.5+delta)/8.5
            for tolerance in (.05, .10):
                r = semantic(v, f, p, bore, tolerance)
                self.assertEqual(r['status'], 'fail')
                self.assertEqual(r['hole_slices']['top_rim']['vertices'], 96)
                self.assertAlmostEqual(r['hole_slices']['top_rim']['max_radial_error_mm'], delta)
                self.assertEqual(r['feature_witnesses']['semantic_wall_radial_witness']['status'], 'fail')

    def test_declared_axis_shift_and_radius_change_still_fail(self):
        v, f, p, bore, _ = fixture()
        for changes in ({'center': [.2, 0.]}, {'radius': 8.7}):
            q = copy.deepcopy(p); q['holes'][0].update(changes)
            self.assertEqual(semantic(v, f, q, bore)['status'], 'fail')

    def test_between_station_wall_deformation_is_measured(self):
        v, f, p, bore, extra = fixture(zrows=(0., .001, 2.75, 4., 5.5))
        # An outward bump is absent from all three finite station rows.
        # The added semantic all-wall radial witness independently catches it.
        for (k, ring, i), vi in extra['index'].items():
            if k == 3 and ring == 0:
                v[vi][0] *= 8.7/8.5; v[vi][1] *= 8.7/8.5
        r = semantic(v, f, p, bore)
        self.assertEqual(r['status'], 'fail')
        self.assertTrue(all(row['status'] == 'pass' for row in r['hole_slices'].values()))
        self.assertEqual(r['feature_witnesses']['semantic_wall_radial_witness']['status'], 'fail')

    def test_sparse_finite_stations_are_not_replaced_with_a_72_ray_claim(self):
        v, f, p, bore, _ = fixture(zrows=(0., 1., 4., 5.5))
        r = semantic(v, f, p, bore)
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['hole_slices']['middle_wall']['status'], 'not_sampled')
        self.assertEqual(r['qualification_status'], 'not_established_by_measurement_alone')

    def test_invalid_tolerance_rejected(self):
        v, f, p, bore, _ = fixture()
        for tolerance in (True, False, None, '0.05', 0., -1., float('nan'), float('inf')):
            with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore, tolerance)

    def test_finite_but_overflowing_coordinates_rejected(self):
        v, f, p, bore, _ = fixture()
        v[f[bore[0]][0]][0] = 1e308
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_nominal_plane_ordering_rejected(self):
        v, f, p, bore, _ = fixture(); p['z_min'], p['z_max'] = p['z_max'], p['z_min']
        with self.assertRaises(RuntimeFailure): semantic(v, f, p, bore)

    def test_finite_proximity_and_bbox_screen_are_still_required(self):
        v, f, p, bore, extra = fixture()
        for (k, ring, i), vi in extra['index'].items():
            if ring == 2: v[vi][0] *= 1.02
        r = semantic(v, f, p, bore)
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['proximity_status'], 'fail')
        self.assertEqual(r['feature_witnesses']['bbox_status'], 'fail')
        self.assertTrue(all(row['status'] == 'pass' for row in r['hole_slices'].values()))


if __name__ == '__main__': unittest.main()
