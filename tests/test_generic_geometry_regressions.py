# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral, independent geometry regressions for public pair validation.

The mesh classifier below is test-only binary64 triangle distance plus oriented
solid angle. It does not reuse the production BVH, ray, boundary, or winding
classifiers. Production pair enumeration and triangle intersections remain live.
"""
import itertools
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hardsurface import generic_validation as geometry
from hardsurface.io import RuntimeFailure
from hardsurface.robust_geometry_audit import triangle_intersection


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _edge_distance(point, a, b):
    edge = _subtract(b, a)
    t = min(1.0, max(0.0, _dot(_subtract(point, a), edge) / _dot(edge, edge)))
    return math.dist(point, tuple(x + t * v for x, v in zip(a, edge)))


def _triangle_distance(point, triangle):
    a, b, c = triangle
    normal = _cross(_subtract(b, a), _subtract(c, a))
    length = _length(normal)
    normal = tuple(v / length for v in normal)
    signed = _dot(_subtract(point, a), normal)
    projected = tuple(v - signed * n for v, n in zip(point, normal))
    # The oriented half-plane signs test the projection's actual triangle.
    if all(_dot(_cross(_subtract(y, x), _subtract(projected, x)), normal) >= -1e-12
           for x, y in ((a, b), (b, c), (c, a))):
        return abs(signed)
    return min(_edge_distance(point, x, y) for x, y in ((a, b), (b, c), (c, a)))


class IndependentMesh:
    """Closed triangle mesh with an independently implemented test classifier."""
    def __init__(self, label, vertices, quads):
        self.label = label
        self.vertices = [tuple(map(float, point)) for point in vertices]
        self.tris = [(face[0], face[i], face[i + 1]) for face in quads for i in (1, 2)]
        self.triangle_points = [tuple(self.vertices[i] for i in face) for face in self.tris]
        self.bounds = [min(p[k] for p in self.vertices) for k in range(3)] + [max(p[k] for p in self.vertices) for k in range(3)]
        self.normals = []
        for a, b, c in self.triangle_points:
            normal = _cross(_subtract(b, a), _subtract(c, a))
            length = _length(normal)
            self.normals.append(tuple(v / length for v in normal))
        self.fallback_records = []

    def classify(self, point):
        if min(_triangle_distance(point, triangle) for triangle in self.triangle_points) <= geometry.EPS:
            return 'boundary'
        angles = []
        for triangle in self.triangle_points:
            a, b, c = [_subtract(vertex, point) for vertex in triangle]
            la, lb, lc = map(_length, (a, b, c))
            numerator = _dot(a, _cross(b, c))
            denominator = la * lb * lc + _dot(a, b) * lc + _dot(b, c) * la + _dot(c, a) * lb
            angles.append(2 * math.atan2(numerator, denominator))
        winding = math.fsum(angles) / (4 * math.pi)
        if abs(winding) < 1e-6:
            return 'outside'
        if abs(winding - 1) < 1e-6:
            return 'inside'
        return 'indeterminate'


def box(label, lower, upper):
    x0, y0, z0 = lower
    x1, y1, z1 = upper
    vertices = [(x, y, z) for z in (z0, z1)
                for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7)]
    quads += [(i, (i + 1) % 4, (i + 1) % 4 + 4, i + 4) for i in range(4)]
    return IndependentMesh(label, vertices, quads)


def thin_tube():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    inner = [(.001, .001), (9.999, .001), (9.999, 9.999), (.001, 9.999)]
    vertices = [(x, y, z) for ring in (outer, inner) for z in (0, 10) for x, y in ring]
    quads = []
    for i in range(4):
        j = (i + 1) % 4
        quads.extend([(i, j, j + 4, i + 4),
                      (i + 8, i + 12, j + 12, j + 8),
                      (i, i + 8, j + 8, j),
                      (i + 4, j + 4, j + 12, i + 12)])
    return IndependentMesh('thin_square_tube', vertices, quads)


class GenericPairGeometryRegressions(unittest.TestCase):
    def assert_closed_outward(self, *meshes):
        for mesh in meshes:
            report = geometry.evaluated_topology(mesh.vertices, mesh.tris)
            self.assertEqual(report['status'], 'pass', report)
            geometry.bvh_precision(mesh.bounds)

    def test_inward_sample_outside_thin_source_cannot_prove_containment(self):
        tube = thin_tube()
        inner = box('clear_inner_box', (.0035, .0035, 1), (9.9965, 9.9965, 9))
        self.assert_closed_outward(tube, inner)
        self.assertGreater(.0035 - .001, geometry.EPS)
        witness = (20 / 3, .008, 10 / 3)
        self.assertEqual(tube.classify(witness), 'outside')
        self.assertEqual(inner.classify(witness), 'inside')
        for a, b in ((tube, inner), (inner, tube)):
            result = geometry.pair_check(a, b, {})
            self.assertEqual(result['forbidden_intersection_count'], 0, result)
            self.assertEqual(result['strict_containment_count'], 0, result)
            self.assertEqual(result['status'], 'pass', result)

    def test_actual_complete_containment_remains_a_failure(self):
        outer = box('outer_solid', (0, 0, 0), (10, 10, 10))
        inner = box('contained_solid', (2, 3, 4), (6, 7, 8))
        self.assert_closed_outward(outer, inner)
        self.assertEqual(outer.classify((4, 5, 6)), 'inside')
        for a, b in ((outer, inner), (inner, outer)):
            result = geometry.pair_check(a, b, {})
            self.assertEqual(result['status'], 'fail', result)
            self.assertEqual(result['forbidden_intersection_count'], 0, result)
            self.assertGreater(result['strict_containment_count'], 0, result)

    def test_uncertain_source_interior_does_not_become_a_pass(self):
        tube = thin_tube()
        inner = box('clear_inner_box', (.0035, .0035, 1), (9.9965, 9.9965, 9))
        self.assert_closed_outward(tube, inner)
        tube.classify = lambda point: 'indeterminate'
        result = geometry.pair_check(tube, inner, {})
        self.assertEqual(result['status'], 'not_run', result)
        self.assertEqual(result['strict_containment_count'], 0, result)
        self.assertGreater(result['indeterminate_count'], 0, result)

    def test_real_surface_crossing_remains_a_failure(self):
        a = box('first_solid', (0, 0, 0), (4, 4, 4))
        b = box('crossing_solid', (2, 1, 1), (6, 3, 3))
        self.assert_closed_outward(a, b)
        for first, second in ((a, b), (b, a)):
            result = geometry.pair_check(first, second, {})
            self.assertEqual(result['status'], 'fail', result)
            self.assertGreater(result['forbidden_intersection_count'], 0, result)
            self.assertGreater(result['strict_containment_count'], 0, result)

    def test_near_parallel_real_crossing_is_never_empty_within_supported_span(self):
        first = [(0, 0, 0), (3500, 0, 0), (0, 3500, 0)]
        second = [(0, 0, -.001), (3500, 0, .00215), (0, 3500, -.001)]
        expected_x = .001 / .0000009
        expected = [(expected_x, 0, 0), (expected_x, 3500 - expected_x, 0)]
        for order, reverse, permutation, shift in itertools.product(
                (False, True), (False, True), ((0, 1, 2), (1, 2, 0), (2, 0, 1)),
                ((0, 0, 0), (120000, -75000, 410000))):
            with self.subTest(order=order, reverse=reverse, permutation=permutation, shift=shift):
                def transform(point):
                    return tuple(point[axis] + offset for axis, offset in zip(permutation, shift))
                a, b = [[transform(p) for p in triangle] for triangle in (first, second)]
                if reverse:
                    b.reverse()
                if order:
                    a, b = b, a
                points = a + b
                bounds = [min(p[k] for p in points) for k in range(3)] + [max(p[k] for p in points) for k in range(3)]
                geometry.bvh_precision(bounds)
                result = triangle_intersection(a, b)
                self.assertEqual(result['kind'], 'noncoplanar', result)
                self.assertGreaterEqual(len(result['points_mm']), 2, result)
                # At a large translation, tiny stored slope differences can
                # move a shallow intersection by several micrometres.
                for point in expected:
                    self.assertLess(min(math.dist(transform(point), hit) for hit in result['points_mm']), .0001, result)


class SceneCoverageRegressions(unittest.TestCase):
    class Object:
        def __init__(self, kind='EMPTY', instance_type='NONE', feature_id=None):
            self.name = 'neutral_' + kind + '_' + instance_type
            self.type = kind
            self.instance_type = instance_type
            self.feature_id = feature_id
            self.hide_render = False
            self.hide_viewport = False
            self.modifiers = []
            self.particle_systems = []

        def get(self, key):
            return self.feature_id if key == 'hs_feature_id' else None

    def run_scene(self, additional=(), instances=(), require_all_visible=True):
        selected = self.Object('MESH', feature_id='feature.single')
        depsgraph = SimpleNamespace(object_instances=list(instances))
        scene = SimpleNamespace(objects=[selected, *additional],
                                unit_settings=SimpleNamespace(system='METRIC', scale_length=1))
        bpy = SimpleNamespace(context=SimpleNamespace(scene=scene,
                              evaluated_depsgraph_get=lambda: depsgraph),
                              data=SimpleNamespace(filepath='neutral-test-only.blend'),
                              app=SimpleNamespace(version_string='mock-only'))
        params = {'parts': [{'id': 'single', 'feature_id': 'feature.single'}],
                  'require_all_visible': require_all_visible, 'include_pairs': True,
                  'probes': [], 'difference_checks': [], 'axis_checks': [], 'contacts': []}
        mesh = box('single', (0, 0, 0), (2, 3, 4))
        mesh.precision = geometry.bvh_precision(mesh.bounds)
        with patch.dict('sys.modules', bpy=bpy), patch.object(geometry, 'Mesh', return_value=mesh):
            return geometry.run(params)

    def test_collection_and_vertex_or_face_instances_are_explicitly_unsupported(self):
        for kind, instance in (('EMPTY', 'COLLECTION'), ('MESH', 'VERTS'), ('MESH', 'FACES')):
            for complete in (True, False):
                with self.subTest(kind=kind, instance=instance, complete=complete):
                    with self.assertRaises(RuntimeFailure):
                        self.run_scene([self.Object(kind, instance)], require_all_visible=complete)

    def test_visible_nonmesh_surface_cannot_be_silently_omitted(self):
        for kind in ('CURVE', 'SURFACE', 'FONT', 'META'):
            for complete in (True, False):
                with self.subTest(kind=kind, complete=complete):
                    with self.assertRaises(RuntimeFailure):
                        self.run_scene([self.Object(kind)], require_all_visible=complete)

    def test_depsgraph_instances_cannot_be_silently_omitted(self):
        instance = SimpleNamespace(is_instance=True, object=self.Object('MESH'))
        for complete in (True, False):
            with self.subTest(complete=complete), self.assertRaises(RuntimeFailure):
                self.run_scene(instances=[instance], require_all_visible=complete)

    def test_nonrenderable_helpers_remain_supported(self):
        helpers = [self.Object(kind) for kind in ('EMPTY', 'LIGHT', 'CAMERA', 'ARMATURE', 'LATTICE')]
        self.assertEqual(self.run_scene(helpers)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
