"""Pure HOST synthetic geometry checks; these are not native qualification."""
import copy
import json
import math
import random
import struct
import unittest

from hardsurface.bore_section_measure import (_check_simple, _ray, _section,
                                            measure_bore_sections)
from hardsurface.io import RuntimeFailure


def fixture(*, n=72, radius=8.0, rings=(0.0, 1.0, 3.0), phase=0.0):
    vertices = [[radius * math.cos(math.tau * i / n + phase),
                 radius * math.sin(math.tau * i / n + phase), z]
                for z in rings for i in range(n)]
    faces = []
    for k in range(len(rings) - 1):
        for i in range(n):
            j = (i + 1) % n
            faces.append([k * n + i, (k + 1) * n + i,
                          (k + 1) * n + j, k * n + j])
    return vertices, faces


def measure(vertices, faces, **changes):
    options = {'bore_face_indices': list(range(len(faces))),
               'membership_token': 'synthetic-host-fixture-external-binding-unestablished',
               'axis_center_xy_mm': [0.0, 0.0],
               'stations_z_mm': [0.25, 1.4, 2.6],
               'nominal_radius_mm': 8.0, 'radial_tolerance_mm': 0.02}
    options.update(changes)
    return measure_bore_sections(vertices, faces, **options)


def radii(report):
    return [ray['radius_mm'] for variant in report['interpretations']
            for station in variant['stations'] for ray in station['rays']]


class BoreSectionMeasureTests(unittest.TestCase):
    def test_actual_sections_between_vertex_rings_have_all_72_rays(self):
        vertices, faces = fixture()
        report = measure(vertices, faces)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['sample_count'], 2 * 3 * 72)
        self.assertEqual(report['angles_per_station'], 72)
        self.assertEqual(report['angular_step_degrees'], 5)
        self.assertEqual(report['qualification_status'], 'not_established_by_host_measurement')
        self.assertIn('not_verified', report['source_authentication'])
        self.assertIn('external_required', report['native_evaluation_attestation'])
        self.assertTrue(report['numerical_policy']['independent_of_design_radial_tolerance'])
        for variant in report['interpretations']:
            for station in variant['stations']:
                self.assertEqual(station['closed_section_count'], 1)
                self.assertEqual(len(station['rays']), 72)
                for i, ray in enumerate(station['rays']):
                    self.assertEqual(ray['angle_degrees'], 5 * i)
                    self.assertEqual(ray['positive_crossing_count'], 1)
                    self.assertAlmostEqual(ray['radius_mm'], 8, places=11)
                    self.assertAlmostEqual(ray['diameter_mm'], 16, places=11)
        json.dumps(report, allow_nan=False)

    def test_polygon_mid_edge_is_actual_chord_not_nominal_circle(self):
        vertices, faces = fixture()
        expected = 8 * math.cos(math.radians(2.5))
        report = measure(vertices, faces, start_angle_degrees=2.5)
        self.assertTrue(all(abs(value - expected) < 1e-11 for value in radii(report)))
        self.assertGreater(report['maximum_radial_error_mm'], 0.007)
        self.assertEqual(measure(vertices, faces, start_angle_degrees=2.5,
                                 radial_tolerance_mm=0.005)['status'], 'fail')

    def test_sections_through_vertices_and_coplanar_ring_edges_deduplicate(self):
        vertices, faces = fixture()
        report = measure(vertices, faces, stations_z_mm=[0.0, 1.0, 3.0])
        self.assertEqual(report['status'], 'pass')
        for variant in report['interpretations']:
            for station in variant['stations']:
                self.assertEqual(station['section_segment_count'], 72)
                self.assertTrue(all(ray['positive_crossing_count'] == 1 for ray in station['rays']))

    def test_actual_triangles_policy(self):
        vertices, quads = fixture()
        faces = [tri for f in quads for tri in ([f[0], f[1], f[2]], [f[0], f[2], f[3]])]
        report = measure(vertices, faces, triangulation_policy='actual_triangles', stations_z_mm=[0, 0.4, 1, 2, 3])
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['sample_count'], 5 * 72)
        self.assertEqual(len(report['interpretations']), 1)
        with self.assertRaises(RuntimeFailure):
            measure(vertices, quads, triangulation_policy='actual_triangles')

    def test_shifted_actual_wall_fails_radial_even_when_diameter_is_correct(self):
        vertices, faces = fixture(n=144)
        for vertex in vertices:
            vertex[0] += 0.2
        report = measure(vertices, faces)
        self.assertEqual(report['status'], 'fail')
        zero = report['interpretations'][0]['stations'][0]['rays'][0]
        self.assertAlmostEqual(zero['diameter_mm'], 16)
        self.assertAlmostEqual(zero['absolute_radial_error_mm'], 0.2)

    def test_declared_axis_shift_is_not_fitted_away(self):
        vertices, faces = fixture()
        self.assertEqual(measure(vertices, faces, axis_center_xy_mm=[0.25, 0])['status'], 'fail')

    def test_enlargement_wrong_nominal_radius_and_ellipse_fail(self):
        vertices, faces = fixture(radius=8.25)
        enlarged = measure(vertices, faces)
        self.assertEqual(enlarged['status'], 'fail')
        self.assertAlmostEqual(enlarged['maximum_radial_error_mm'], 0.25)
        self.assertAlmostEqual(enlarged['interpretations'][0]['stations'][0]['rays'][0]['diameter_mm'], 16.5)
        vertices, faces = fixture()
        self.assertEqual(measure(vertices, faces, nominal_radius_mm=8.25)['status'], 'fail')
        for vertex in vertices:
            vertex[0] *= 1.03
            vertex[1] *= 0.97
        self.assertEqual(measure(vertices, faces)['status'], 'fail')

    def test_axial_and_angular_waves_use_actual_sections(self):
        vertices, faces = fixture(rings=(0, 1, 2, 3))
        for i in range(72, 144):
            vertices[i][0] *= 8.3 / 8
            vertices[i][1] *= 8.3 / 8
        axial = measure(vertices, faces, stations_z_mm=[0.5, 1.5, 2.5])
        self.assertEqual(axial['status'], 'fail')
        self.assertAlmostEqual(axial['maximum_radial_error_mm'], 0.15)
        vertices, faces = fixture()
        for vertex in vertices:
            ratio = (8 + 0.15 * math.cos(3 * math.atan2(vertex[1], vertex[0]))) / 8
            vertex[0] *= ratio
            vertex[1] *= ratio
        angular = measure(vertices, faces)
        self.assertEqual(angular['status'], 'fail')
        self.assertAlmostEqual(angular['maximum_radial_error_mm'], 0.15)

    def test_different_quad_interpretations_are_both_reported(self):
        vertices, faces = fixture()
        # A tapered ring shifted in XY creates nonplanar actual quads.
        for i in range(72, len(vertices)):
            vertices[i][0] += 0.08
            vertices[i][1] *= 1.01
        report = measure(vertices, faces, stations_z_mm=[0.5], start_angle_degrees=1.3)
        a, b = report['interpretations']
        differences = [abs(x['radius_mm'] - y['radius_mm']) for x, y in zip(a['stations'][0]['rays'], b['stations'][0]['rays'])]
        self.assertGreater(max(differences), 1e-5)
        self.assertIn('no claim for arbitrary mixtures', report['quad_interpretation_scope'])

    def test_lost_face_and_lost_membership_rejected(self):
        vertices, faces = fixture()
        for actual, selected in ((faces[:-1], list(range(len(faces) - 1))),
                                 (faces, list(range(1, len(faces))))):
            with self.assertRaises(RuntimeFailure):
                measure(vertices, actual, bore_face_indices=selected)

    def test_duplicate_membership_faces_and_unselected_actual_duplicate_rejected(self):
        vertices, faces = fixture()
        with self.assertRaises(RuntimeFailure):
            measure(vertices, faces, bore_face_indices=list(range(len(faces))) + [0])
        duplicate = faces + [list(faces[0])]
        for membership in (list(range(len(duplicate))), list(range(len(faces)))):
            with self.assertRaises(RuntimeFailure):
                measure(vertices, duplicate, bore_face_indices=membership)

    def test_one_or_all_reversed_faces_rejected(self):
        vertices, faces = fixture()
        for indices in ([0], list(range(len(faces)))):
            altered = copy.deepcopy(faces)
            for i in indices:
                altered[i].reverse()
            with self.assertRaises(RuntimeFailure):
                measure(vertices, altered)

    def test_missing_stations_do_not_snap_to_nearest_ring(self):
        vertices, faces = fixture()
        for stations in ([-0.01], [3.01], [0.5, 8]):
            with self.assertRaises(RuntimeFailure):
                measure(vertices, faces, stations_z_mm=stations)

    def test_unresolved_near_ring_station_is_not_silently_snapped(self):
        vertices, faces = fixture()
        with self.assertRaises(RuntimeFailure) as caught:
            measure(vertices, faces, stations_z_mm=[1 + 1e-10])
        self.assertEqual(caught.exception.code, 'BORE_SECTION_GEOMETRY')

    def test_missing_authentication_token_and_invalid_membership_rejected(self):
        vertices, faces = fixture()
        for token in (None, '', ' ', {}, 4):
            with self.subTest(token=token), self.assertRaises(RuntimeFailure):
                measure(vertices, faces, membership_token=token)
        for membership in (None, [], [True], [-1], [len(faces)], [0.0], {0}):
            with self.subTest(membership=membership), self.assertRaises(RuntimeFailure):
                measure(vertices, faces, bore_face_indices=membership)

    def test_invalid_numeric_inputs_rejected(self):
        vertices, faces = fixture()
        for key in ('nominal_radius_mm', 'radial_tolerance_mm', 'geometry_epsilon_mm'):
            for value in (None, True, 0, -1, float('nan'), float('inf'), '0.02'):
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeFailure):
                    measure(vertices, faces, **{key: value})
        for key, values in (('stations_z_mm', ([], [True], [1, 1], [float('nan')], [1, 1 + 1e-10])),
                            ('axis_center_xy_mm', ([0], [0, float('inf')], [0, True])),
                            ('angle_count', (True, 71, 72.0)),
                            ('start_angle_degrees', (float('nan'), float('inf'), 1e30)),
                            ('triangulation_policy', (None, 'assume_native', 'nearest'))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeFailure):
                    measure(vertices, faces, **{key: value})
        for bad in (float('nan'), float('inf'), 1e308, 10 ** 1000):
            altered = copy.deepcopy(vertices)
            altered[0][0] = bad
            with self.assertRaises(RuntimeFailure):
                measure(altered, faces)
        with self.assertRaises(RuntimeFailure):
            measure(vertices, faces, nominal_radius_mm=1e308)

    def test_geometry_epsilon_cannot_be_used_as_design_tolerance(self):
        vertices, faces = fixture(radius=8.1)
        small = measure(vertices, faces, radial_tolerance_mm=0.01)
        large = measure(vertices, faces, radial_tolerance_mm=0.2)
        self.assertEqual(small['status'], 'fail')
        self.assertEqual(large['status'], 'pass')
        self.assertEqual(small['numerical_policy'], large['numerical_policy'])
        self.assertEqual(radii(small), radii(large))
        with self.assertRaises(RuntimeFailure):
            measure(vertices, faces, geometry_epsilon_mm=0.1)

    def test_storage_permutation_preserves_geometry_and_results(self):
        vertices, faces = fixture()
        original = measure(vertices, faces, start_angle_degrees=2.1)
        rng = random.Random(184)
        order = list(range(len(vertices)))
        rng.shuffle(order)
        reverse = {old: new for new, old in enumerate(order)}
        new_vertices = [vertices[i] for i in order]
        new_faces = [[reverse[i] for i in face] for face in faces]
        rng.shuffle(new_faces)
        reordered = measure(new_vertices, new_faces, start_angle_degrees=2.1)
        self.assertEqual(original['status'], reordered['status'])
        self.assertEqual(original['input_signatures']['selected_geometry_sha256'],
                         reordered['input_signatures']['selected_geometry_sha256'])
        self.assertNotEqual(original['input_signatures']['mesh_sha256'], reordered['input_signatures']['mesh_sha256'])
        for a, b in zip(radii(original), radii(reordered)):
            self.assertAlmostEqual(a, b, places=11)
        membership_reversed = measure(vertices, faces, start_angle_degrees=2.1,
                                      bore_face_indices=list(reversed(range(len(faces)))))
        self.assertEqual(original, membership_reversed)

    def test_scale_and_float32_input_preserve_finite_measurements(self):
        vertices, faces = fixture()
        for scale in (0.001, 1, 10000):
            converted = [[struct.unpack('f', struct.pack('f', x * scale))[0] for x in v] for v in vertices]
            report = measure(converted, faces, nominal_radius_mm=8 * scale,
                             radial_tolerance_mm=0.02 * scale,
                             stations_z_mm=[0.25 * scale, 1.4 * scale, 2.6 * scale],
                             geometry_epsilon_mm=1e-9 * scale)
            self.assertEqual(report['status'], 'pass')
            self.assertLess(report['maximum_radial_error_mm'], 1e-5 * scale)
            json.dumps(report, allow_nan=False)

    def test_translation_preserves_radial_result(self):
        vertices, faces = fixture()
        shifted = [[x + 1200, y - 2400, z + 900] for x, y, z in vertices]
        report = measure(shifted, faces, axis_center_xy_mm=[1200, -2400],
                         stations_z_mm=[900.25, 901.4, 902.6])
        self.assertEqual(report['status'], 'pass')
        self.assertLess(report['maximum_radial_error_mm'], 1e-10)

    def test_multiple_sections_or_foreign_region_rejected(self):
        vertices, faces = fixture()
        offset = len(vertices)
        extra = [[x + 25, y, z] for x, y, z in vertices]
        with self.assertRaises(RuntimeFailure):
            measure(vertices + extra, faces + [[i + offset for i in f] for f in faces])

    def test_axis_outside_wall_is_not_nearest_hit_measurement(self):
        vertices, faces = fixture()
        with self.assertRaises(RuntimeFailure):
            measure(vertices, faces, axis_center_xy_mm=[9, 0])

    def test_no_nominal_proximity_classifier(self):
        vertices, faces = fixture(radius=20)
        report = measure(vertices, faces)
        self.assertEqual(report['status'], 'fail')
        self.assertAlmostEqual(report['maximum_radial_error_mm'], 12)
        self.assertEqual(report['sample_count'], 432)

    def test_inputs_are_not_mutated(self):
        vertices, faces = fixture()
        before = copy.deepcopy((vertices, faces))
        measure(vertices, faces)
        self.assertEqual((vertices, faces), before)

    def test_ray_kernel_rejects_multiple_positive_crossings_without_nearest_choice(self):
        # A simple but non-star-shaped polygon has three positive crossings at
        # zero degrees. Test the actual intersection kernel directly so the
        # public inward-wall domain precheck cannot mask a ray-kernel defect.
        xy = [(-4, -4), (6, -4), (6, 4), (3, 4), (3, -1),
              (1, -1), (1, 4), (-4, 4)]
        loop = [('v', i) for i in range(len(xy))]
        points = dict(zip(loop, xy))
        graph = {key: {loop[i - 1], loop[(i + 1) % len(loop)]} for i, key in enumerate(loop)}
        _check_simple(loop, points, 1e-9, 1.5)
        with self.assertRaises(RuntimeFailure) as caught:
            _ray(loop, points, graph, 0, 1e-9, 1.5)
        self.assertEqual(caught.exception.code, 'BORE_SECTION_RAY_COVERAGE')
        self.assertEqual(caught.exception.details['positive_crossing_count'], 3)

    def test_ray_kernel_rejects_collinear_edge_and_tangent_vertex(self):
        for xy in ([(-4, -4), (4, -4), (4, 0), (2, 0), (2, 4), (-4, 4)],
                   [(-4, -4), (4, -4), (4, 4), (3, 0), (2, 4), (-4, 4)]):
            loop = [('v', i) for i in range(len(xy))]
            points = dict(zip(loop, xy))
            graph = {key: {loop[i - 1], loop[(i + 1) % len(loop)]} for i, key in enumerate(loop)}
            with self.assertRaises(RuntimeFailure) as caught:
                _ray(loop, points, graph, 0, 1e-9, 1.5)
            self.assertEqual(caught.exception.code, 'BORE_SECTION_RAY_AMBIGUOUS')

    def test_section_kernel_rejects_coplanar_triangle_folded_edge_and_open_loop(self):
        coplanar = [(1, 0, 1), (2, 0, 1), (1, 1, 1)]
        with self.assertRaises(RuntimeFailure) as caught:
            _section(coplanar, [(0, (0, 1, 2))], 1, 1e-9)
        self.assertEqual(caught.exception.code, 'BORE_SECTION_COPLANAR')
        folded = [(1, -1, 1), (1, 1, 1), (2, 0, 2), (0, 0, 2)]
        with self.assertRaises(RuntimeFailure) as caught:
            _section(folded, [(0, (0, 1, 2)), (1, (1, 0, 3))], 1, 1e-9)
        self.assertEqual(caught.exception.code, 'BORE_SECTION_COPLANAR')
        with self.assertRaises(RuntimeFailure) as caught:
            _section([(1, -1, 0), (1, 1, 0), (1, 0, 2)], [(0, (0, 1, 2))], 1, 1e-9)
        self.assertEqual(caught.exception.code, 'BORE_SECTION_COVERAGE')

    def test_section_kernel_rejects_nonadjacent_crossing_and_touch(self):
        for xy in ([(-1, -1), (1, 1), (-1, 1), (1, -1)],
                   [(-2, -2), (2, -2), (2, 2), (0, -2), (-2, 2)]):
            loop = [('v', i) for i in range(len(xy))]
            points = dict(zip(loop, xy))
            with self.assertRaises(RuntimeFailure) as caught:
                _check_simple(loop, points, 1e-9, 1.5)
            self.assertEqual(caught.exception.code, 'BORE_SECTION_GEOMETRY')


if __name__ == '__main__':
    unittest.main()
