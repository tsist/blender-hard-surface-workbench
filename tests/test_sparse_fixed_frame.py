# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST-only fixed-frame fixtures; no production design or approval."""
from copy import deepcopy
import math
import unittest
from unittest import mock

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import (build_sparse_panel, sparse_config,
    validate_parameter_domain, validate_sparse_authored_identity, SCHEDULE_REVISION,
    FIXED_FRAME_SCHEMA, FIXED_FRAME_MIN_ANGLE_DEGREES, FIXED_FRAME_MAX_ANGLE_DEGREES)
from hardsurface.sparse_parameter_edit import plan_parameter_edit
from hardsurface.sparse_patch_graph import (semantic_mesh, validate_sparse_mesh,
    plan_strip_insertion, apply_strip_insertion)
from tests.test_sparse_patch_graph import host_quality, host_intersections


BASE = {'op': 'quad.panel', 'id': 'neutral_fixed_frame', 'topology_strategy': 'sparse_control_cage',
        'size': [120, 84], 'center': [0, 0], 'corner_radius': 11, 'edge_bevel': 1.5,
        'z_min': -4, 'z_max': 4,
        'holes': [{'id': 'bore', 'kind': 'circle', 'center': [-10, 3], 'radius': 12}],
        'sparse_cage': {'layout': {'schema': FIXED_FRAME_SCHEMA,
            'feature_frame_mm': [-30, -18, 12, 24], 'corner_guard_mm': 5}}}


def parameters(support=False):
    p = deepcopy(BASE)
    p['sparse_cage']['hole_planar_support'] = support
    return p


def moved_parameters(p):
    result = deepcopy(p)
    result['holes'][0].update(center=[-7, 2], radius=12.6)
    return result


class FixedFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bodies = {support: build_sparse_panel(parameters(support)) for support in (False, True)}

    def assert_failure(self, p, code):
        with self.assertRaises(RuntimeFailure) as caught:
            validate_parameter_domain(p)
        self.assertEqual(caught.exception.code, code)
        return caught.exception.details

    def test_explicit_axis_formula_uses_frozen_frame_not_hole(self):
        p = parameters()
        domain = validate_parameter_domain(p)['geometry_domain']['fixed_frame']
        self.assertEqual(domain['grid_x_mm'], [-49, -44, -30, -19.5, -9, 1.5, 12, 28, 44, 49])
        self.assertEqual(domain['grid_y_mm'], [-31, -26, -18, -7.5, 3, 13.5, 24, 31])
        moved = validate_parameter_domain(moved_parameters(p))['geometry_domain']['fixed_frame']
        self.assertEqual(domain['grid_x_mm'], moved['grid_x_mm'])
        self.assertEqual(domain['grid_y_mm'], moved['grid_y_mm'])
        self.assertEqual(parameters(), p)

    def test_complete_ab_graph_retains_schedule_and_host_quality(self):
        for support, body in self.bodies.items():
            with self.subTest(support=support):
                audit = validate_sparse_mesh(body)
                count = 654 if support else 622
                self.assertEqual((audit['vertices'], audit['edges'], audit['faces']), (count, 2*count, count))
                self.assertEqual((audit['components'], audit['boundary_edges'], audit['euler']), (1, 0, 0))
                self.assertGreater(audit['signed_volume_mm3'], 0)
                self.assertEqual(body['structure_schedule_revision'], SCHEDULE_REVISION)
                legacy = parameters(support); legacy['sparse_cage'].pop('layout')
                self.assertEqual(semantic_mesh(body)['connectivity'], semantic_mesh(build_sparse_panel(legacy))['connectivity'])
                self.assertTrue(host_quality(body)['passed'])
                self.assertEqual(validate_sparse_authored_identity(body)['status'], 'pass')

    def test_complete_ab_has_no_detected_host_triangle_intersections(self):
        for support, body in self.bodies.items():
            with self.subTest(support=support):
                report = host_intersections(body)
                self.assertEqual(report['status'], 'pass', report['findings'])

    def test_macrogrid_edges_are_axis_aligned(self):
        body = self.bodies[False]
        inverse = {i: sid for sid, i in body['sparse_graph']['vertex_map'].items()}
        checked = 0
        for a, b in body['sparse_graph']['edges']:
            if '/grid/' in inverse[a] and '/grid/' in inverse[b]:
                x, y = body['vertices_mm'][a], body['vertices_mm'][b]
                self.assertEqual(x[2], y[2])
                self.assertNotEqual(x, y)
                self.assertTrue((x[0] == y[0]) ^ (x[1] == y[1]))
                checked += 1
        self.assertGreater(checked, 150)

    def test_all_outer_rings_share_actual_core_tangent_correspondence(self):
        body = self.bodies[False]
        coords = semantic_mesh(body)['coordinates']
        d = validate_parameter_domain(parameters())['geometry_domain']
        xs, ys = d['fixed_frame']['grid_x_mm'], d['fixed_frame']['grid_y_mm']
        rings = [p for p in body['sparse_graph']['ports']
                 if p['role'].startswith('profile.') or '.outer_' in p['role'] and not p['role'].endswith('outer_core')]
        self.assertEqual(len(rings), 14)
        for ring in rings:
            for sid in ring['vertex_ids']:
                side = int(sid.split('/side:')[1].split('/')[0])
                i = int(sid.split('/sample:')[1])
                x, y, _ = coords[sid]
                u = (x, y, -x, -y)[side]
                core_half = (d['w']/2-d['r']) if side % 2 == 0 else (d['h']/2-d['r'])
                axis = xs if side % 2 == 0 else ys
                n = len(axis)-1
                with self.subTest(ring=ring['role'], side=side, sample=i):
                    if i == 0:
                        other = (y, -x, -y, x)[side]
                        other_half = (d['h']/2-d['r']) if side % 2 == 0 else (d['w']/2-d['r'])
                        self.assertAlmostEqual(u+core_half, other+other_half)
                        self.assertLess(u, -core_half)
                    elif i == 1:
                        self.assertEqual(u, -core_half)
                    elif i == n-1:
                        self.assertEqual(u, core_half)
                    else:
                        self.assertEqual(u, axis[i] if side < 2 else -axis[n-i])

    def test_hole_edits_change_only_ring_vertices_and_preserve_every_exterior(self):
        for support, before in self.bodies.items():
            p = moved_parameters(parameters(support))
            after = build_sparse_panel(p)
            a, b = semantic_mesh(before), semantic_mesh(after)
            ring_ids = {sid for sid in a['coordinates'] if '/hole_rim/' in sid or '/hole_support/' in sid}
            changed = {sid for sid, xyz in a['coordinates'].items() if xyz != b['coordinates'][sid]}
            self.assertEqual(changed, ring_ids)
            self.assertEqual(len(changed), 64 if support else 32)
            self.assertEqual(a['connectivity'], b['connectivity'])
            self.assertEqual(a['creases'], b['creases'])
            plan = plan_parameter_edit(before, p)
            self.assertEqual(set(plan['allowed_vertex_axes']), ring_ids)
            for dependency in before['authored_structure']['edit_dependencies'].values():
                self.assertTrue(set(dependency['vertex_ids']).issubset(a['coordinates']))
            for name in ('holes[0].center[0]', 'holes[0].center[1]', 'holes[0].radius'):
                self.assertEqual(set(before['authored_structure']['edit_dependencies'][name]['vertex_ids']), ring_ids)

    def test_schema_and_bound_layout_cannot_be_changed_as_hole_edit(self):
        for key, value in [('corner_guard_mm', 6), ('feature_frame_mm', [-31, -18, 12, 24])]:
            p = parameters(); p['sparse_cage']['layout'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeFailure) as caught:
                plan_parameter_edit(self.bodies[False], p)
            self.assertEqual(caught.exception.code, 'AUTHORED_EDIT_SCOPE_UNSUPPORTED')
        body = deepcopy(self.bodies[False])
        body['authored_structure']['parameter_binding']['parameters']['sparse_cage']['layout']['corner_guard_mm'] = 6
        with self.assertRaises(RuntimeFailure) as caught:
            validate_sparse_authored_identity(body)
        self.assertEqual(caught.exception.code, 'SPARSE_PARAMETER_BINDING')

    def test_metadata_separates_new_layout_from_unchanged_nominal_design(self):
        body = self.bodies[False]
        info = body['metadata']['subdivision_cage']
        self.assertEqual(info['candidate_version'], 3)
        self.assertEqual(info['layout_policy']['schema'], FIXED_FRAME_SCHEMA)
        self.assertEqual(info['nominal_design']['size_mm'], BASE['size'])
        self.assertEqual(info['nominal_design']['corner_radius_mm'], BASE['corner_radius'])
        self.assertEqual(info['nominal_design']['hole_center_mm'], BASE['holes'][0]['center'])
        self.assertEqual(info['nominal_design']['hole_radius_mm'], BASE['holes'][0]['radius'])
        self.assertEqual(body['authored_structure']['supported_edit_datum']['hole_center_radius'],
                         'frozen_macrogrid_local_hole_rings_only')
        self.assertEqual(info['fixed_frame_domain'], validate_parameter_domain(parameters())['geometry_domain']['fixed_frame'])
        self.assertTrue(all(value == 'not_run' for key, value in info['evidence'].items() if key != 'host_graph'))

    def test_malformed_layout_fails_before_allocation(self):
        variants = [None, {}, [], {'schema': 'unknown'}, {**BASE['sparse_cage']['layout'], 'extra': 1}]
        for frame in ([0, 1, 2], [-30, -18, -31, 24], [-30, 25, 12, 24], [-30, -18, float('nan'), 24], [-30, -18, True, 24]):
            variants.append({**BASE['sparse_cage']['layout'], 'feature_frame_mm': frame})
        for guard in (0, -1, True, None, float('inf')):
            variants.append({**BASE['sparse_cage']['layout'], 'corner_guard_mm': guard})
        with mock.patch('hardsurface.sparse_panel_geometry.SparseMeshBuilder', side_effect=AssertionError('allocated before domain validation')):
            for value in variants:
                p = parameters(); p['sparse_cage']['layout'] = value
                with self.subTest(layout=value), self.assertRaises(RuntimeFailure):
                    build_sparse_panel(p)

    def test_frame_guard_spacing_is_scale_aware_and_strict(self):
        for guard in (.01, 19, 90):
            p = parameters(); p['sparse_cage']['layout']['corner_guard_mm'] = guard
            self.assert_failure(p, 'SPARSE_FRAME_GRID_SPACING')
        for frame in ([-50, -18, 12, 24], [-44, -18, 12, 24], [-30, -18, 44, 24], [-30, -18, 12, 31], [-30, -27, 12, 24]):
            p = parameters(); p['sparse_cage']['layout']['feature_frame_mm'] = frame
            self.assert_failure(p, 'SPARSE_FRAME_GRID_SPACING')
        p = parameters(); p['sparse_cage']['layout']['corner_guard_mm'] = .43
        domain = validate_parameter_domain(p)['geometry_domain']['fixed_frame']
        self.assertEqual(domain['minimum_grid_spacing_mm'], .42)
        self.assertGreater(domain['observed_grid_spacing_mm'], domain['minimum_grid_spacing_mm'])
        p['sparse_cage']['layout']['corner_guard_mm'] = .41
        self.assert_failure(p, 'SPARSE_FRAME_GRID_SPACING')

    def test_too_close_hole_or_support_fails_radial_margin(self):
        for support, x in ((False, -1), (True, -2)):
            p = parameters(support); p['holes'][0]['center'] = [x, 3]
            details = self.assert_failure(p, 'SPARSE_FRAME_RADIAL_CLEARANCE')
            self.assertGreater(details['required_minimum_mm'], 0)
            self.assertLess(details['observed_minimum_mm'], details['required_minimum_mm'])

    def test_annulus_acute_boundary_is_not_silently_relaxed(self):
        self.assertEqual(FIXED_FRAME_MIN_ANGLE_DEGREES, 30)
        self.assertEqual(FIXED_FRAME_MAX_ANGLE_DEGREES, 150)
        p = parameters(True); p['holes'][0]['center'] = [-4.1, 3]
        q = validate_parameter_domain(p)['geometry_domain']['fixed_frame']['annulus_quality']
        self.assertGreater(q['observed_minimum_angle_degrees'], 30)
        self.assertLess(q['observed_minimum_angle_degrees'], 30.2)
        self.assertLess(q['observed_maximum_angle_degrees'], 150)
        self.assertGreater(q['minimum_normalized_turn'], .5)
        p['holes'][0]['center'] = [-4, 3]
        details = self.assert_failure(p, 'SPARSE_FRAME_ANNULUS_ANGLE')
        self.assertTrue(details['annulus_quality']['strictly_convex'])
        self.assertLess(details['annulus_quality']['observed_minimum_angle_degrees'], 30)
        self.assertGreater(details['observed_radial_margin_mm'], details['required_radial_margin_mm'])

    def test_inside_frame_is_not_sufficient_for_convexity(self):
        p = parameters(); p['holes'][0].update(center=[-24, -12], radius=4)
        details = self.assert_failure(p, 'SPARSE_FRAME_ANNULUS_CONVEXITY')
        self.assertGreater(details['observed_radial_margin_mm'], 0)
        self.assertFalse(details['annulus_quality']['strictly_convex'])
        self.assertLess(details['annulus_quality']['minimum_normalized_turn'], 0)

    def test_translation_preserves_axis_layout_and_local_quality(self):
        p = parameters(); shift = [17, -29]
        p['center'] = shift
        p['holes'][0]['center'] = [x+s for x, s in zip(p['holes'][0]['center'], shift)]
        p['sparse_cage']['layout']['feature_frame_mm'] = [x+shift[i % 2] for i, x in enumerate(p['sparse_cage']['layout']['feature_frame_mm'])]
        after = semantic_mesh(build_sparse_panel(p))
        before = semantic_mesh(self.bodies[False])
        self.assertEqual(before['connectivity'], after['connectivity'])
        for sid, xyz in before['coordinates'].items():
            for axis in (0, 1):
                self.assertAlmostEqual(xyz[axis]+shift[axis], after['coordinates'][sid][axis])
            self.assertEqual(xyz[2], after['coordinates'][sid][2])

    def test_absent_layout_remains_legacy_not_implicit_fixed_frame(self):
        p = parameters(); p['sparse_cage'].pop('layout')
        body = build_sparse_panel(p)
        self.assertNotIn('layout', sparse_config(p))
        self.assertNotIn('fixed_frame', validate_parameter_domain(p)['geometry_domain'])
        self.assertEqual(body['metadata']['subdivision_cage']['candidate_version'], 2)
        self.assertNotIn('layout_policy', body['metadata']['subdivision_cage'])
        self.assertEqual(body['authored_structure']['supported_edit_datum']['hole_center_radius'], 'whole_authored_sparse_layout_rebuild')
        self.assertTrue(any('/grid/' in s for s in body['authored_structure']['edit_dependencies']['holes[0].radius']['vertex_ids']))

    def test_full_body_insertion_preserves_fixed_frame_hole_edit_scope(self):
        for support in (False, True):
            for corridor, added in (('east', 44), ('south', 48)):
                with self.subTest(support=support, corridor=corridor):
                    before = self.bodies[support]
                    plan = plan_strip_insertion(before, corridor)
                    self.assertEqual((plan['added_vertices'], plan['added_faces']), (added, added))
                    inserted = apply_strip_insertion(before, plan)
                    count = (654 if support else 622)+added
                    audit = validate_sparse_mesh(inserted)
                    self.assertEqual((audit['vertices'], audit['faces'], audit['boundary_edges']), (count, count, 0))
                    p = moved_parameters(inserted['authored_structure']['parameter_binding']['parameters'])
                    after = build_sparse_panel(p)
                    a, b = semantic_mesh(inserted), semantic_mesh(after)
                    new_ids = inserted['sparse_graph']['insertion_transaction']['new_vertex_ids']
                    self.assertTrue(all(a['coordinates'][sid] == b['coordinates'][sid] for sid in new_ids))
                    changed = {sid for sid, xyz in a['coordinates'].items() if xyz != b['coordinates'][sid]}
                    edit = plan_parameter_edit(inserted, p)
                    self.assertEqual(changed, set(edit['allowed_vertex_axes']))
                    self.assertFalse(set(new_ids).intersection(edit['allowed_vertex_axes']))
                    self.assertTrue(host_quality(inserted)['passed'])
                    self.assertEqual(a['connectivity'], b['connectivity'])


if __name__ == '__main__':
    unittest.main()
