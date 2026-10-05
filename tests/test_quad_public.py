"""Independent synthetic cases for the public parameter-driven constructors.

These are pure-Python geometry checks, not Blender or visual qualification.
Nominal dimensions use a numerical roundoff bound. Curve approximation uses
the separately requested chord bound; it must never relax nominal dimensions.
The JSON inputs are deliberately varied illustrative parts, not mesh goldens.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import inspect
import json
import math
from pathlib import Path
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface.quad_fastener import FastenerError, build_quad_fastener
from hardsurface.quad_geometry import build
from hardsurface.quad_patches import QuadPatchError
from hardsurface.quad_quality import DEFAULT_POLICY, validate_mesh


FIXTURE_PATH = Path(__file__).resolve().parents[1] / 'fixtures' / 'public_quad_cases.json'


def load_cases():
    """Load fresh public parameter records, also usable by CLI qualification."""
    return json.loads(FIXTURE_PATH.read_text())['cases']


NOMINAL_TOLERANCE_MM = json.loads(FIXTURE_PATH.read_text())['nominal_tolerance_mm']


@lru_cache(maxsize=16)
def _mesh(case_id):
    case = next(case for case in load_cases() if case['id'] == case_id)
    return build(case['parameters'], case_id)


def _role_faces(mesh, role):
    return [face for face, source in zip(mesh['faces'], mesh['face_provenance'])
            if source['surface_role'] == role]


def _role_points(mesh, role):
    indices = {index for face in _role_faces(mesh, role) for index in face}
    assert indices, 'Missing actual surface: ' + role
    return [mesh['vertices_mm'][index] for index in sorted(indices)]


def _axis_bounds(points, axis):
    return min(p[axis] for p in points), max(p[axis] for p in points)


def _round_box_distance(point, bounds, radius):
    """Exact signed distance to an axis-aligned rounded rectangle."""
    x0, y0, x1, y1 = bounds
    qx = abs(point[0] - (x0 + x1) / 2) - (x1 - x0) / 2 + radius
    qy = abs(point[1] - (y0 + y1) / 2) - (y1 - y0) / 2 + radius
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius


def _segment_deviation(a, b, distance):
    """Minimize the analytic convex distance field along the authored edge."""
    def evaluate(t):
        return distance(tuple(x + (y - x) * t for x, y in zip(a, b)))

    lo, hi = 0.0, 1.0
    # Ternary interval reduction is independent of the production witness.
    for _ in range(64):
        left, right = (2 * lo + hi) / 3, (lo + 2 * hi) / 3
        if evaluate(left) <= evaluate(right):
            hi = right
        else:
            lo = left
    return max(0.0, -min(evaluate(0), evaluate(1), evaluate((lo + hi) / 2)))


def dimension_witnesses(mesh, parameters, nominal_tolerance_mm=NOMINAL_TOLERANCE_MM):
    """Check actual mesh coordinates against inputs, return compact evidence.

    This helper reads constructor-format mesh arrays and can therefore also be
    used on actual Blender control/evaluated polygon arrays exported in mm.
    It does not accept metadata as evidence for the geometric measurements.
    """
    p = parameters
    measurements, curves = [], []

    def nominal(name, measured, expected):
        error = abs(measured - expected)
        assert error <= nominal_tolerance_mm, (name, measured, expected, error)
        measurements.append({'dimension': name, 'measured_mm': measured,
                             'expected_mm': expected, 'error_mm': error})

    def extent(role, axis, expected):
        points = mesh['vertices_mm'] if role is None else _role_points(mesh, role)
        for end, actual, requested in zip(('min', 'max'), _axis_bounds(points, axis), expected):
            nominal(f'{role or "object"}:axis_{axis}:{end}', actual, requested)

    def planar(role, axis, expected):
        extent(role, axis, (expected, expected))

    def curve(role, axes, distance, axial_axis=None):
        faces = _role_faces(mesh, role)
        assert faces, 'Missing actual curve surface: ' + role
        points = _role_points(mesh, role)
        vertex_error = max(abs(distance(tuple(point[a] for a in axes))) for point in points)
        nominal(role + ':profile_vertex_error', vertex_error, 0.0)
        edges = set()
        for face in faces:
            for ia, ib in zip(face, face[1:] + face[:1]):
                a, b = mesh['vertices_mm'][ia], mesh['vertices_mm'][ib]
                if axial_axis is not None and abs(a[axial_axis] - b[axial_axis]) > nominal_tolerance_mm:
                    continue
                pa, pb = tuple(a[k] for k in axes), tuple(b[k] for k in axes)
                if math.dist(pa, pb) > nominal_tolerance_mm:
                    edges.add(tuple(sorted((pa, pb))))
        assert edges, 'No actual profile edges: ' + role
        deviation = max(_segment_deviation(a, b, distance) for a, b in edges)
        assert deviation <= p['chord_tolerance'] + nominal_tolerance_mm, (role, deviation, p['chord_tolerance'])
        curves.append({'surface': role, 'authored_profile_edges': len(edges),
                       'nominal_vertex_error_mm': vertex_error,
                       'maximum_chord_deviation_mm': deviation,
                       'chord_tolerance_mm': p['chord_tolerance']})

    def circle(role, center, radius):
        curve(role, (0, 1), lambda point: math.dist(point, center) - radius, 2)

    def rounded(role, axes, bounds, radius, axial_axis):
        curve(role, axes, lambda point: _round_box_distance(point, bounds, radius), axial_axis)

    def roundover(roles, bounds, outline_radius, bevel, lo, hi):
        if not bevel:
            return
        for role in roles:
            maximum_deviation, maximum_vertex_error, count = 0.0, 0.0, 0
            for face in _role_faces(mesh, role):
                for ia, ib in zip(face, face[1:] + face[:1]):
                    a, b = mesh['vertices_mm'][ia], mesh['vertices_mm'][ib]
                    if abs(a[2] - b[2]) <= nominal_tolerance_mm:
                        continue
                    zcenter = lo + bevel if (a[2] + b[2]) / 2 < (lo + hi) / 2 else hi - bevel
                    center = (bevel, zcenter)
                    # The profile signed distance is the exact inward offset
                    # from the nominal silhouette along this meridian.
                    qa, qb = [(-_round_box_distance(point[:2], bounds, outline_radius), point[2]) for point in (a, b)]
                    maximum_vertex_error = max(maximum_vertex_error,
                                               abs(math.dist(qa, center) - bevel),
                                               abs(math.dist(qb, center) - bevel))
                    midpoint = tuple((x + y) / 2 for x, y in zip(qa, qb))
                    maximum_deviation = max(maximum_deviation, bevel - math.dist(midpoint, center))
                    count += 1
            assert count, 'Missing actual round-over arcs: ' + role
            nominal(role + ':bevel_radius_constraint', maximum_vertex_error, 0.0)
            assert maximum_deviation <= p['chord_tolerance'] + nominal_tolerance_mm, (role, maximum_deviation, p['chord_tolerance'])
            curves.append({'surface': role, 'authored_profile_edges': count,
                           'nominal_vertex_error_mm': maximum_vertex_error,
                           'maximum_chord_deviation_mm': maximum_deviation,
                           'chord_tolerance_mm': p['chord_tolerance']})

    def circle_band(role, center, radius, lower, upper):
        # Counterbores share their wall role; separate the two exact radii by
        # selecting the actual polygons whose four corners lie on each radius.
        selected = []
        for face, source in zip(mesh['faces'], mesh['face_provenance']):
            if source['surface_role'] != role:
                continue
            if all(abs(math.dist(mesh['vertices_mm'][i][:2], center) - radius) <= nominal_tolerance_mm for i in face):
                selected.append(face)
        assert selected, 'Missing circular band: ' + role
        selected_points = [mesh['vertices_mm'][i] for face in selected for i in face]
        for label, actual, wanted in zip(('min', 'max'), _axis_bounds(selected_points, 2), (lower, upper)):
            nominal(role + ':radius_' + str(radius) + ':z_' + label, actual, wanted)
        radius_error = max(abs(math.dist(point[:2], center) - radius) for point in selected_points)
        nominal(role + ':radius_' + str(radius), radius_error, 0.0)
        edges = set()
        for face in selected:
            for ia, ib in zip(face, face[1:] + face[:1]):
                a, b = mesh['vertices_mm'][ia], mesh['vertices_mm'][ib]
                if abs(a[2] - b[2]) <= nominal_tolerance_mm:
                    edges.add(tuple(sorted((tuple(a[:2]), tuple(b[:2])))))
        assert edges
        deviation = max(radius - math.dist(tuple((a[k] + b[k]) / 2 for k in range(2)), center) for a, b in edges)
        assert deviation <= p['chord_tolerance'] + nominal_tolerance_mm, (role, deviation)
        curves.append({'surface': role, 'radius_mm': radius, 'authored_profile_edges': len(edges),
                       'nominal_vertex_error_mm': radius_error,
                       'maximum_chord_deviation_mm': deviation,
                       'chord_tolerance_mm': p['chord_tolerance']})

    if p['op'] == 'quad.fastener':
        cx, cy = p['center']
        sr, hr = p['shaft_radius'], p['head_radius']
        z, direction = p['shoulder_z'], p['direction']
        tip, top = z - direction * p['shaft_length'], z + direction * p['head_height']
        extent(None, 0, (cx - hr, cx + hr))
        extent(None, 1, (cy - hr, cy + hr))
        extent(None, 2, tuple(sorted((tip, top))))
        extent('shaft_wall', 2, tuple(sorted((tip, z))))
        extent('head_wall', 2, tuple(sorted((z, top))))
        planar('head_shoulder', 2, z)
        planar('shaft_tip', 2, tip)
        circle('shaft_wall', p['center'], sr)
        circle('head_wall', p['center'], hr)
        if p['socket_flat_width']:
            floor = top - direction * p['socket_depth']
            extent('socket_wall', 2, tuple(sorted((floor, top))))
            planar('socket_floor', 2, floor)
            apothem = p['socket_flat_width'] / 2
            normals = [(math.cos(math.pi / 6 + i * math.pi / 3),
                        math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
            for point in _role_points(mesh, 'socket_wall'):
                distance = max((point[0] - cx) * a + (point[1] - cy) * b for a, b in normals) - apothem
                nominal('socket:hexagon_flat_constraint', distance, 0.0)
        else:
            assert not any(source['surface_role'].startswith('socket_') for source in mesh['face_provenance'])

    elif p['op'] == 'quad.panel':
        cx, cy = p['center']
        w, d = p['size']
        lo, hi = p['z_min'], p['z_max']
        box = (cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)
        extent(None, 0, (box[0], box[2]))
        extent(None, 1, (box[1], box[3]))
        extent(None, 2, (min([lo] + [lip['z_min'] for lip in p['lips']]), hi))
        planar('panel_bottom', 2, lo)
        planar('panel_top', 2, hi)
        rounded('outline_wall', (0, 1), box, p['corner_radius'], 2)
        roundover(('outline_bevel',), box, p['corner_radius'], p['edge_bevel'], lo, hi)
        for hole in p['holes']:
            role = hole['id'] + ':wall'
            hx, hy = hole['center']
            if hole['kind'] == 'circle':
                cb = hole.get('counterbore')
                if cb:
                    shoulder = hi - cb['depth'] if cb['side'] == 'top' else lo + cb['depth']
                    small_range = (lo, shoulder) if cb['side'] == 'top' else (shoulder, hi)
                    large_range = (shoulder, hi) if cb['side'] == 'top' else (lo, shoulder)
                    circle_band(role, hole['center'], hole['radius'], *small_range)
                    circle_band(role, hole['center'], cb['radius'], *large_range)
                    planar(hole['id'] + ':counterbore_shoulder', 2, shoulder)
                else:
                    circle_band(role, hole['center'], hole['radius'], lo, hi)
            else:
                width, height = hole['size']
                rounded(role, (0, 1), (hx - width / 2, hy - height / 2, hx + width / 2, hy + height / 2), hole['radius'], 2)
                extent(role, 2, (lo, hi))
            assert _role_faces(mesh, role)
        for lip in p['lips']:
            role = lip['id'] + ':wall'
            a, b, c, d = lip['bounds']
            extent(role, 0, (a, c))
            extent(role, 1, (b, d))
            extent(role, 2, (lip['z_min'], lo))
            planar(lip['id'] + ':bottom', 2, lip['z_min'])

    elif p['op'] == 'quad.shell':
        cx, cy = p['center']
        w, d, h = p['size']
        z0, wall = p['z_min'], p['wall_thickness']
        zt, floor = z0 + h, z0 + p['base_thickness']
        box = (cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)
        extent(None, 0, (box[0], box[2]))
        extent(None, 1, (box[1], box[3]))
        extent(None, 2, (z0, max([zt] + [seat['z_top'] for seat in p['corner_seats']] + [post['z_top'] for post in p['posts']])))
        planar('bottom', 2, z0)
        planar('floor', 2, floor)
        planar('rim', 2, zt)
        for side, axis, outer, inner in (
                ('left', 0, box[0], box[0] + wall), ('right', 0, box[2], box[2] - wall),
                ('front', 1, box[1], box[1] + wall), ('back', 1, box[3], box[3] - wall)):
            planar(side + ':outer', axis, outer)
            planar(side + ':inner', axis, inner)
        rounded('outer_corner', (0, 1), box, p['corner_radius'], 2)
        roundover(('bottom_roundover', 'rim_roundover'), box, p['corner_radius'], p['edge_bevel'], z0, zt)
        for seat in p['corner_seats']:
            role = seat['id']
            a, b, c, d = seat['bounds']
            extent(role + ':top', 0, (a, c))
            extent(role + ':top', 1, (b, d))
            planar(role + ':top', 2, seat['z_top'])
            circle(role + ':blind_bore', seat['hole_center'], seat['hole_radius'])
            extent(role + ':blind_bore', 2, (seat['hole_bottom'], seat['z_top']))
            planar(role + ':blind_bottom', 2, seat['hole_bottom'])
        for post in p['posts']:
            role = post['id']
            circle(role + ':outer', post['center'], post['radius'])
            circle(role + ':blind_bore', post['center'], post['hole_radius'])
            extent(role + ':outer', 2, (floor, post['z_top']))
            extent(role + ':blind_bore', 2, (z0, post['hole_top']))
            planar(role + ':top', 2, post['z_top'])
            planar(role + ':blind_bottom', 2, post['hole_top'])
        for opening in p['openings']:
            horizontal, z = opening['center']
            width, height = opening['size']
            axis = 0 if opening['side'] in ('left', 'right') else 1
            rounded(opening['id'] + ':tunnel', (1 - axis, 2),
                    (horizontal - width / 2, z - height / 2, horizontal + width / 2, z + height / 2), opening['radius'], axis)
            actual = _axis_bounds(_role_points(mesh, opening['id'] + ':tunnel'), axis)
            nominal(opening['id'] + ':wall_thickness', actual[1] - actual[0], wall)
    else:
        raise AssertionError('Unsupported fixture operation: ' + str(p['op']))

    return {'nominal_tolerance_mm': nominal_tolerance_mm,
            'chord_tolerance_mm': p['chord_tolerance'],
            'nominal_max_error_mm': max(row['error_mm'] for row in measurements),
            'dimensions': measurements, 'curves': curves}


def _signed_volume(mesh):
    origin = mesh['vertices_mm'][0]
    vertices = [tuple(p[k] - origin[k] for k in range(3)) for p in mesh['vertices_mm']]
    def determinant(a, b, c):
        return (a[0] * (b[1] * c[2] - b[2] * c[1])
                + a[1] * (b[2] * c[0] - b[0] * c[2])
                + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return math.fsum(determinant(vertices[f[0]], vertices[f[i]], vertices[f[i + 1]]) / 6
                     for f in mesh['faces'] for i in range(1, len(f) - 1))


class PublicConstructorTests(unittest.TestCase):
    def test_fixture_matrix_contains_independent_varied_parts(self):
        cases = load_cases()
        self.assertEqual(len({case['id'] for case in cases}), len(cases))
        groups = {op: [case['parameters'] for case in cases if case['parameters']['op'] == op]
                  for op in ('quad.panel', 'quad.shell', 'quad.fastener')}
        self.assertGreaterEqual(len(groups['quad.panel']), 3)
        self.assertGreaterEqual(len(groups['quad.shell']), 2)
        self.assertGreaterEqual(len(groups['quad.fastener']), 3)
        self.assertEqual(len({tuple(p['size']) for p in groups['quad.panel']}), len(groups['quad.panel']))
        self.assertEqual(len({tuple(p['size']) for p in groups['quad.shell']}), len(groups['quad.shell']))
        self.assertGreater(len({len(p['posts']) for p in groups['quad.shell']}), 1)
        self.assertGreater(len({len(p['openings']) for p in groups['quad.shell']}), 1)
        self.assertEqual({p['direction'] for p in groups['quad.fastener']}, {-1, 1})
        self.assertEqual({bool(p['socket_flat_width']) for p in groups['quad.fastener']}, {False, True})

    def test_actual_polygons_all_quad_connected_manifold_and_quality(self):
        for case in load_cases():
            with self.subTest(case=case['id']):
                mesh = _mesh(case['id'])
                self.assertTrue(mesh['faces'])
                self.assertTrue(all(len(face) == len(set(face)) == 4 for face in mesh['faces']))
                self.assertEqual(len(mesh['face_provenance']), len(mesh['faces']))
                self.assertEqual({row['feature_id'] for row in mesh['face_provenance']}, {case['id']})
                report = validate_mesh([[value * 0.001 for value in point] for point in mesh['vertices_mm']],
                                       mesh['faces'], face_provenance=mesh['face_provenance'])
                self.assertEqual(report['policy'], DEFAULT_POLICY)
                self.assertTrue(report['passed'], report['finding_counts'])
                self.assertEqual(report['counts']['quads'], len(mesh['faces']))
                for key in ('triangles', 'ngons', 'boundary_edges', 'nonmanifold_edges'):
                    self.assertEqual(report['counts'][key], 0)
                self.assertEqual(report['counts']['connected_face_components'], 1)
                self.assertGreater(_signed_volume(mesh), 0)

    def test_actual_dimensions_and_chords_have_separate_tolerances(self):
        for case in load_cases():
            with self.subTest(case=case['id']):
                witness = dimension_witnesses(_mesh(case['id']), case['parameters'])
                self.assertTrue(witness['dimensions'])
                self.assertTrue(witness['curves'])
                self.assertLess(witness['nominal_tolerance_mm'], witness['chord_tolerance_mm'] * 1e-4)
                self.assertLessEqual(witness['nominal_max_error_mm'], NOMINAL_TOLERANCE_MM)

    def test_chord_tolerance_cannot_hide_shifted_nominal_dimensions(self):
        case = next(case for case in load_cases() if case['id'] == 'fastener_short_socket')
        mesh = copy.deepcopy(_mesh(case['id']))
        shift = case['parameters']['chord_tolerance'] / 4
        mesh['vertices_mm'] = [(x + shift, y, z) for x, y, z in mesh['vertices_mm']]
        with self.assertRaises(AssertionError):
            dimension_witnesses(mesh, case['parameters'])

    def test_fastener_has_no_placement_or_solid_dimension_defaults(self):
        signature = inspect.signature(build_quad_fastener)
        for name in ('center_mm', 'shaft_radius_mm', 'head_radius_mm', 'head_height_mm',
                     'shaft_length_mm', 'shoulder_z_mm'):
            self.assertIs(signature.parameters[name].default, inspect.Parameter.empty, name)
        self.assertIsNone(signature.parameters['socket_flat_width_mm'].default)
        self.assertEqual(signature.parameters['socket_depth_mm'].default, 0)
        with self.assertRaises(TypeError):
            build_quad_fastener()

    def test_fastener_socket_is_disabled_without_an_explicit_socket_request(self):
        p = next(case['parameters'] for case in load_cases() if case['parameters']['op'] == 'quad.fastener')
        mesh = build_quad_fastener(center_mm=p['center'], shaft_radius_mm=p['shaft_radius'],
                                   head_radius_mm=p['head_radius'], head_height_mm=p['head_height'],
                                   shaft_length_mm=p['shaft_length'], shoulder_z_mm=p['shoulder_z'])
        self.assertFalse(mesh['metadata']['socket_enabled'])
        self.assertFalse(any(row['surface_role'].startswith('socket_') for row in mesh['face_provenance']))

    def test_fastener_shaft_sampling_respects_requested_axial_bound(self):
        for case in load_cases():
            p = case['parameters']
            if p['op'] != 'quad.fastener':
                continue
            with self.subTest(case=case['id']):
                mesh = _mesh(case['id'])
                lengths = []
                for face in _role_faces(mesh, 'shaft_wall'):
                    for ia, ib in zip(face, face[1:] + face[:1]):
                        a, b = mesh['vertices_mm'][ia], mesh['vertices_mm'][ib]
                        if math.dist(a[:2], b[:2]) <= NOMINAL_TOLERANCE_MM:
                            lengths.append(abs(a[2] - b[2]))
                self.assertTrue(lengths)
                self.assertLessEqual(max(lengths), p['target_edge_length'] + NOMINAL_TOLERANCE_MM)

    def test_fastener_positive_volume_matches_authored_circle_and_hexagon(self):
        for case in load_cases():
            p = case['parameters']
            if p['op'] != 'quad.fastener':
                continue
            with self.subTest(case=case['id']):
                mesh = _mesh(case['id'])
                ring = {tuple(point[:2]) for point in _role_points(mesh, 'head_wall')
                        if abs(point[2] - p['shoulder_z']) < NOMINAL_TOLERANCE_MM}
                count = len(ring)
                factor = count * math.sin(math.tau / count) / 2
                expected = factor * (p['shaft_radius'] ** 2 * p['shaft_length'] + p['head_radius'] ** 2 * p['head_height'])
                expected -= math.sqrt(3) * p['socket_flat_width'] ** 2 * p['socket_depth'] / 2
                self.assertAlmostEqual(_signed_volume(mesh), expected, delta=max(1, expected) * 1e-11)

    def test_parameters_are_unchanged_by_construction(self):
        for case in load_cases():
            with self.subTest(case=case['id']):
                before = copy.deepcopy(case['parameters'])
                build(case['parameters'], case['id'])
                self.assertEqual(case['parameters'], before)


class RejectedPublicRequests(unittest.TestCase):
    def case(self, operation):
        return copy.deepcopy(next(case['parameters'] for case in load_cases() if case['parameters']['op'] == operation))

    def test_overlapping_panel_holes_fail_before_returning_geometry(self):
        p = self.case('quad.panel')
        p['holes'][1] = dict(p['holes'][0], id='second_overlapping_hole')
        with self.assertRaises(QuadPatchError) as caught:
            build(p, 'reject_overlap')
        self.assertEqual(caught.exception.code, 'overlapping_patches')

    def test_impossible_panel_counterbore_is_rejected(self):
        p = self.case('quad.panel')
        p['holes'][0]['counterbore']['depth'] = p['z_max'] - p['z_min']
        with self.assertRaises(RuntimeFailure) as caught:
            build(p, 'reject_counterbore')
        self.assertEqual(caught.exception.code, 'QUAD_COUNTERBORE_DIMENSIONS')

    def test_shell_requires_four_mirrored_seats(self):
        for variation in ('missing_seat', 'different_width', 'outside_post', 'outside_opening'):
            p = self.case('quad.shell')
            if variation == 'missing_seat':
                p['corner_seats'].pop()
            elif variation == 'different_width':
                p['corner_seats'][0]['bounds'][2] += 0.7
            elif variation == 'outside_post':
                p['posts'][0]['center'][0] += p['size'][0]
            else:
                p['openings'][0]['center'][1] = p['z_min']
            with self.subTest(variation=variation), self.assertRaises(RuntimeFailure) as caught:
                build(p, 'reject_shell_layout')
            self.assertEqual(caught.exception.code, 'QUAD_SHELL_DIMENSIONS')

    def test_fastener_impossible_radii_and_socket_are_rejected(self):
        for variation in ('head_radius', 'socket_depth', 'socket_width', 'direction'):
            p = self.case('quad.fastener')
            if variation == 'head_radius':
                p['head_radius'] = p['shaft_radius']
            elif variation == 'socket_depth':
                p['socket_depth'] = p['head_height']
            elif variation == 'socket_width':
                p['socket_flat_width'] = 2 * p['head_radius']
            else:
                p['direction'] = 0
            with self.subTest(variation=variation), self.assertRaises(FastenerError):
                build(p, 'reject_fastener')

    def test_extremely_fine_tolerance_fails_bounded_sampling_budget(self):
        for op in ('quad.panel', 'quad.shell', 'quad.fastener'):
            p = self.case(op)
            p['chord_tolerance'] = 1e-15
            p['max_segments'] = 24
            with self.subTest(op=op), self.assertRaises((QuadPatchError, FastenerError, RuntimeFailure)) as caught:
                build(p, 'reject_unbounded_sampling')
            self.assertIn('budget', str(caught.exception).lower())


if __name__ == '__main__':
    unittest.main()
