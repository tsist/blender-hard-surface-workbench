import copy
import math
import unittest

from hardsurface.contact_boundary import qualified_hole_boundary, outside_or_boundary


def annulus(n=32, radius=2.3, center=(0., 0.), z=0., order=None):
    angles = [2 * math.pi * i / n for i in range(n)]
    vertices = [(center[0] + r * math.cos(a), center[1] + r * math.sin(a), z)
                for r in (radius, 4.6) for a in angles]
    order = list(range(n)) if order is None else order
    triangles = []
    for k, i in enumerate(order):
        j = order[(k + 1) % n]
        triangles.extend(((i, j, n + j), (i, n + j, n + i)))
    return vertices, triangles


class ContactBoundaryTests(unittest.TestCase):
    def qualify(self, vertices, triangles, **kwargs):
        spec = dict(center_mm=(0, 0), radius_mm=2.3, z_mm=0)
        spec.update(kwargs)
        return qualified_hole_boundary(vertices, triangles, **spec)

    def test_actual_inscribed_facet_contact_is_allowed(self):
        vertices, triangles = annulus()
        report = self.qualify(vertices, triangles)
        self.assertEqual(report['status'], 'pass', report)
        a, b = vertices[:2]
        p = tuple((x + y) / 2 for x, y in zip(a, b))
        self.assertLess(math.hypot(*p[:2]), 2.3 - .002)
        self.assertAlmostEqual(math.hypot(*p[:2]), 2.3 * math.cos(math.pi/32))
        self.assertTrue(outside_or_boundary(p, report))
        self.assertTrue(outside_or_boundary((3.8, 0, 0), report))
        self.assertFalse(outside_or_boundary((0, 0, 0), report))
        self.assertFalse(outside_or_boundary(tuple(x * .995 for x in p), report))

    def test_epsilon_is_not_design_tolerance(self):
        v, t = annulus()
        report = self.qualify(v, t)
        a, b = v[:2]
        p = [(x + y) / 2 for x, y in zip(a, b)]
        length = math.hypot(*p[:2])
        near = (p[0] * (1 - .001 / length), p[1] * (1 - .001 / length), 0)
        inside = (p[0] * (1 - .003 / length), p[1] * (1 - .003 / length), 0)
        self.assertTrue(outside_or_boundary(near, report))
        self.assertFalse(outside_or_boundary(inside, report))
        self.assertFalse(outside_or_boundary(inside, report, epsilon_mm=.05))


    def test_contact_plane_and_tiny_float_error(self):
        v, t = annulus(z=6.800001)
        report = self.qualify(v, t, z_mm=6.8)
        self.assertEqual(report['status'], 'pass', report)
        self.assertTrue(outside_or_boundary((3.8, 0, 6.8), report))
        self.assertFalse(outside_or_boundary((3.8, 0, 6.803), report))
        self.assertEqual(self.qualify(v, t, z_mm=6.81)['status'], 'fail')

    def test_circle_fit_handles_realistic_world_offset(self):
        v, t = annulus(center=(23, -31), z=6.8)
        report = self.qualify(v, t, center_mm=(23, -31), z_mm=6.8)
        self.assertEqual(report['status'], 'pass', report)
        self.assertAlmostEqual(report['fitted_radius_mm'], 2.3)
        self.assertLess(report['center_error_mm'], 1e-12)

    def test_small_qualified_position_variations(self):
        v, t = annulus(center=(.02, -.01), radius=2.31)
        report = self.qualify(v, t)
        self.assertEqual(report['status'], 'pass', report)
        self.assertAlmostEqual(report['fitted_radius_mm'], 2.31)

    def test_wrong_center_radius_and_coarse_chord_fail(self):
        for kwargs in ({'center': (.06, 0)}, {'radius': 2.36}, {'n': 8}):
            with self.subTest(kwargs=kwargs):
                v, t = annulus(**kwargs)
                report = self.qualify(v, t)
                self.assertEqual(report['status'], 'fail', report)
                self.assertFalse(outside_or_boundary((3.8, 0, 0), report))

    def test_self_crossing_loop_fails(self):
        v, t = annulus(n=35, order=[i * 2 % 35 for i in range(35)])
        report = self.qualify(v, t)
        self.assertEqual(report['status'], 'fail', report)
        self.assertEqual(report['reason'], 'boundary_not_nondegenerate_convex')

    def test_missing_boundary_edge_fails(self):
        v, t = annulus()
        report = self.qualify(v, t[2:])
        self.assertEqual(report['status'], 'fail', report)
        self.assertEqual(report['reason'], 'boundary_not_degree_two')

    def test_missing_plane_boundary_fails(self):
        v, t = annulus(z=1)
        self.assertEqual(self.qualify(v, t)['reason'], 'missing_contact_plane_submesh')

    def test_two_candidate_loops_fail(self):
        v, t = annulus()
        v2, t2 = annulus(radius=2.32)
        t2 = [tuple(i + len(v) for i in tri) for tri in t2]
        report = self.qualify(v + v2, t + t2)
        self.assertEqual(report['reason'], 'boundary_not_one_closed_loop')

    def test_disk_rim_cannot_be_used_as_bore(self):
        v, _ = annulus()
        v = v[:32] + [(0, 0, 0)]
        t = [(i, (i + 1) % 32, 32) for i in range(32)]
        self.assertEqual(self.qualify(v, t)['reason'], 'boundary_is_not_a_bore')

    def test_loop_vertex_budget(self):
        v, t = annulus(n=513)
        self.assertEqual(self.qualify(v, t)['reason'], 'boundary_vertex_budget_exceeded')

    def test_synthetic_pass_without_loop_never_authorizes_contact(self):
        for report in ({'status': 'pass'}, {'status': 'pass', 'loop_mm': []},
                       {'status': 'pass', 'center_mm': [0, 0], 'radius_mm': 2.3}):
            self.assertFalse(outside_or_boundary((3.8, 0, 0), report))
        v, t = annulus()
        report = self.qualify(v, t)
        for key in ('loop_mm', 'loop_vertex_indices'):
            damaged = copy.deepcopy(report)
            damaged[key] = []
            self.assertFalse(outside_or_boundary((3.8, 0, 0), damaged))

    def test_invalid_inputs_and_tolerance_widening_fail_closed(self):
        v, t = annulus()
        for kwargs in ({'epsilon_mm': .003}, {'position_tolerance_mm': .051},
                       {'chord_tolerance_mm': .051}, {'radius_mm': float('nan')}):
            self.assertEqual(self.qualify(v, t, **kwargs)['status'], 'fail')
        self.assertEqual(self.qualify(v, [(0, 1, len(v))])['status'], 'fail')
        self.assertEqual(self.qualify(v + [(0, 0, float('nan'))], t)['status'], 'fail')

    def test_source_arrays_are_not_mutated(self):
        v, t = annulus()
        before = copy.deepcopy((v, t))
        self.qualify(v, t)
        self.assertEqual((v, t), before)

    def test_degenerate_and_nonmanifold_boundary_fail(self):
        v, t = annulus()
        self.assertEqual(self.qualify(v, t + [(0, 0, 1)])['reason'], 'degenerate_plane_triangle')
        self.assertEqual(self.qualify(v, t + [t[0], t[0]])['reason'], 'nonmanifold_boundary_neighborhood')


if __name__ == '__main__':
    unittest.main()
