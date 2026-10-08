# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST tests for an explicit versioned balanced exterior policy.

Saved-source examples below are JSON/float32 protocol fixtures, not Blender
execution, production design, reference approval or visual qualification.
"""
from copy import deepcopy
import json
import struct
import unittest
from unittest import mock

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import (build_sparse_panel, sparse_config,
    validate_parameter_domain, validate_sparse_authored_identity, SCHEDULE_REVISION,
    FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA, FIXED_FRAME_SCHEMAS,
    FIXED_FRAME_MIN_ANGLE_DEGREES, FIXED_FRAME_MAX_ANGLE_DEGREES)
from hardsurface.sparse_parameter_edit import plan_parameter_edit
from hardsurface.sparse_patch_graph import (semantic_mesh, validate_sparse_mesh,
    fingerprint, plan_strip_insertion, apply_strip_insertion)


BASE = {'op': 'quad.panel', 'id': 'neutral_fixed_frame', 'topology_strategy': 'sparse_control_cage',
        'size': [120, 84], 'center': [0, 0], 'corner_radius': 11, 'edge_bevel': 1.5,
        'z_min': -4, 'z_max': 4,
        'holes': [{'id': 'bore', 'kind': 'circle', 'center': [-10, 3], 'radius': 12}],
        'sparse_cage': {'layout': {'schema': BALANCED_FIXED_FRAME_SCHEMA,
            'feature_frame_mm': [-31, -18, 12, 24], 'corner_guard_mm': 6}}}


def parameters(support=False, schema=BALANCED_FIXED_FRAME_SCHEMA):
    p = deepcopy(BASE)
    p['sparse_cage']['hole_planar_support'] = support
    if schema is None:
        p['sparse_cage'].pop('layout')
    else:
        p['sparse_cage']['layout']['schema'] = schema
    return p


# Captured from the unmodified v1.0 constructor before adding the v1.1 selector.
# These cover the whole serialized mesh, including geometry and bound metadata.
LEGACY_MESH_SHA256 = {
    (FIXED_FRAME_SCHEMA, False): '79023306c50ec34d63e4108a8277689723ec2020d8db706db2262634a7791d06',
    (FIXED_FRAME_SCHEMA, True): 'be31e662af0121a1feeaf373bbc3f9e992f79018d1a34895fd9ea113649bf4a7',
    (None, False): '44c0d4c3b915216aa7d0b1ca89249638ac30de0be497e5c70b52200dc3da90d8',
    (None, True): '018f0c6395188ebd2e937d6fd6765cbd1213bb2c9515e5ca00769af740b8a74b'}


class BalancedLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bodies = {(schema, support): build_sparse_panel(parameters(support, schema))
                      for schema in (None, FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA)
                      for support in (False, True)}

    def test_two_explicit_versions_normalize_without_changing_the_selector(self):
        self.assertEqual(FIXED_FRAME_SCHEMAS,
                         ('fixed-frame-axis-aligned/1.0', 'fixed-frame-axis-aligned/1.1'))
        for schema in FIXED_FRAME_SCHEMAS:
            p = parameters(schema=schema)
            normalized = sparse_config(p)
            self.assertEqual(normalized['layout']['schema'], schema)
            self.assertEqual(normalized['layout']['feature_frame_mm'], [-31., -18., 12., 24.])
            self.assertEqual(normalized['layout']['corner_guard_mm'], 6.)
            self.assertEqual(normalized['wall_support_fraction'], .15)
            self.assertEqual(p, parameters(schema=schema))
        self.assertNotIn('layout', sparse_config(parameters(schema=None)))

    def test_selector_changes_only_east_corridor_in_frozen_macrogrid(self):
        domains = {schema: validate_parameter_domain(parameters(schema=schema))['geometry_domain']['fixed_frame']
                   for schema in FIXED_FRAME_SCHEMAS}
        legacy, balanced = (domains[schema] for schema in FIXED_FRAME_SCHEMAS)
        self.assertEqual(legacy['grid_x_mm'], [-49, -43, -31, -20.25, -9.5, 1.25, 12, 27.5, 43, 49])
        self.assertEqual(balanced['grid_x_mm'], [-49, -43, -31, -20.25, -9.5, 1.25, 12, 30.5, 43, 49])
        self.assertEqual(balanced['grid_y_mm'], [-31, -25, -18, -7.5, 3, 13.5, 24, 31])
        self.assertEqual(legacy['grid_y_mm'], balanced['grid_y_mm'])
        self.assertEqual(legacy['annulus_quality'], balanced['annulus_quality'])
        for support in (False, True):
            a = semantic_mesh(self.bodies[FIXED_FRAME_SCHEMA, support])
            b = semantic_mesh(self.bodies[BALANCED_FIXED_FRAME_SCHEMA, support])
            self.assertEqual(a['connectivity'], b['connectivity'])
            self.assertEqual(a['creases'], b['creases'])
            changes = {sid: xyz for sid, xyz in a['coordinates'].items() if xyz != b['coordinates'][sid]}
            self.assertTrue(changes)
            for sid, xyz in changes.items():
                self.assertEqual(xyz[0], 27.5)
                self.assertEqual(b['coordinates'][sid], [30.5, xyz[1], xyz[2]])

    def test_ab_graph_counts_and_semantic_schedule_are_unchanged(self):
        for support in (False, True):
            body = self.bodies[BALANCED_FIXED_FRAME_SCHEMA, support]
            audit = validate_sparse_mesh(body)
            count = 654 if support else 622
            self.assertEqual((audit['vertices'], audit['edges'], audit['faces']), (count, 2*count, count))
            self.assertEqual((audit['components'], audit['boundary_edges'], audit['euler']), (1, 0, 0))
            self.assertGreater(audit['signed_volume_mm3'], 0)
            self.assertEqual(body['structure_schedule_revision'], SCHEDULE_REVISION)
            self.assertEqual(validate_sparse_authored_identity(body)['status'], 'pass')
            self.assertEqual(semantic_mesh(body)['connectivity'],
                             semantic_mesh(self.bodies[None, support])['connectivity'])

    def test_macrogrid_and_all_profile_ring_axes_share_correspondence(self):
        body = self.bodies[BALANCED_FIXED_FRAME_SCHEMA, False]
        coords = semantic_mesh(body)['coordinates']
        inverse = {i: sid for sid, i in body['sparse_graph']['vertex_map'].items()}
        checked = 0
        for a, b in body['sparse_graph']['edges']:
            if '/grid/' in inverse[a] and '/grid/' in inverse[b]:
                x, y = body['vertices_mm'][a], body['vertices_mm'][b]
                self.assertEqual(x[2], y[2])
                self.assertTrue((x[0] == y[0]) ^ (x[1] == y[1]))
                checked += 1
        self.assertGreater(checked, 150)
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

    def test_hole_edit_preserves_macrogrid_exterior_and_bound_graph(self):
        for support in (False, True):
            before = self.bodies[BALANCED_FIXED_FRAME_SCHEMA, support]
            p = parameters(support); p['holes'][0].update(center=[-7, 2], radius=12.6)
            after = build_sparse_panel(p)
            a, b = semantic_mesh(before), semantic_mesh(after)
            ring_ids = {sid for sid in a['coordinates'] if '/hole_rim/' in sid or '/hole_support/' in sid}
            changed = {sid for sid, xyz in a['coordinates'].items() if xyz != b['coordinates'][sid]}
            self.assertEqual(changed, ring_ids)
            self.assertEqual(len(changed), 64 if support else 32)
            self.assertEqual(a['connectivity'], b['connectivity'])
            self.assertEqual(a['creases'], b['creases'])
            self.assertEqual(set(plan_parameter_edit(before, p)['allowed_vertex_axes']), ring_ids)
            self.assertEqual(before['metadata']['subdivision_cage']['fixed_frame_domain']['grid_x_mm'],
                             after['metadata']['subdivision_cage']['fixed_frame_domain']['grid_x_mm'])
            self.assertEqual(before['metadata']['subdivision_cage']['fixed_frame_domain']['grid_y_mm'],
                             after['metadata']['subdivision_cage']['fixed_frame_domain']['grid_y_mm'])

    def test_legacy_constructors_remain_byte_exact_and_saved_reconstruction_passes(self):
        for key, expected_sha256 in LEGACY_MESH_SHA256.items():
            with self.subTest(schema=key[0], support=key[1]):
                body = self.bodies[key]
                self.assertEqual(fingerprint(body), expected_sha256)
                saved = json.loads(json.dumps(body))
                self.assertEqual(fingerprint(saved), expected_sha256)
                self.assertEqual(validate_sparse_authored_identity(saved)['status'], 'pass')
                rebuilt = build_sparse_panel(saved['authored_structure']['parameter_binding']['parameters'])
                self.assertEqual(fingerprint(rebuilt), expected_sha256)

    def test_float32_protocol_preserves_each_exact_version_binding(self):
        for schema in FIXED_FRAME_SCHEMAS:
            for support in (False, True):
                body = self.bodies[schema, support]
                stored = {name: deepcopy(body[name]) for name in
                          ('vertices_mm', 'faces', 'edge_creases', 'authored_structure', 'face_provenance')}
                stored['vertices_mm'] = [[struct.unpack('f', struct.pack('f', x*.001))[0]*1000
                                          for x in xyz] for xyz in stored['vertices_mm']]
                with self.subTest(schema=schema, support=support):
                    result = validate_sparse_authored_identity(stored)
                    self.assertEqual(result['status'], 'pass')
                    self.assertEqual(result['native_extraction'], 'not_run')

    def test_version_change_is_not_nominal_edit_or_saved_model_migration(self):
        for old, new in (FIXED_FRAME_SCHEMAS, tuple(reversed(FIXED_FRAME_SCHEMAS))):
            before = self.bodies[old, False]
            with self.subTest(old=old, new=new), self.assertRaises(RuntimeFailure) as caught:
                plan_parameter_edit(before, parameters(schema=new))
            self.assertEqual(caught.exception.code, 'AUTHORED_EDIT_SCOPE_UNSUPPORTED')
            relabelled = deepcopy(before)
            relabelled['authored_structure'] = deepcopy(self.bodies[new, False]['authored_structure'])
            with self.assertRaises(RuntimeFailure) as caught:
                validate_sparse_authored_identity(relabelled)
            self.assertEqual(caught.exception.code, 'SPARSE_BOUND_COORDINATES')

    def test_metadata_retains_actual_version_and_unchanged_nominal_design(self):
        for schema, version in ((None, 2), (FIXED_FRAME_SCHEMA, 3), (BALANCED_FIXED_FRAME_SCHEMA, 4)):
            body = self.bodies[schema, False]
            info = body['metadata']['subdivision_cage']
            self.assertEqual(info['candidate_version'], version)
            self.assertEqual(info['nominal_design'], self.bodies[None, False]['metadata']['subdivision_cage']['nominal_design'])
            datum = body['authored_structure']['supported_edit_datum']
            if schema is None:
                self.assertNotIn('layout_schema', datum)
                self.assertNotIn('layout_policy', info)
            else:
                self.assertEqual(info['layout_policy']['schema'], schema)
                self.assertEqual(info['fixed_frame_domain']['schema'], schema)
                self.assertEqual(datum['layout_schema'], schema)
                self.assertEqual(datum['feature_frame_edit'], 'distinct_new_plan_required')
            self.assertTrue(all(v == 'not_run' for k, v in info['evidence'].items() if k != 'host_graph'))

    def test_malformed_or_unknown_layout_rejects_before_allocation(self):
        layout = parameters()['sparse_cage']['layout']
        variants = [None, {}, [], {**layout, 'extra': 1}]
        variants.extend({**layout, 'schema': value} for value in (None, True, [], {}, '1.1',
                        'fixed-frame-axis-aligned/1.2', 'fixed-frame-axis-aligned/999'))
        variants.extend({**layout, 'feature_frame_mm': frame} for frame in
                        ([0, 1, 2], [-31, -18, -32, 24], [-31, 25, 12, 24], [-31, -18, True, 24],
                         [-31, -18, float('nan'), 24]))
        variants.extend({**layout, 'corner_guard_mm': guard} for guard in (0, -1, True, None, float('inf')))
        with mock.patch('hardsurface.sparse_panel_geometry.SparseMeshBuilder', side_effect=AssertionError('early allocation')):
            for value in variants:
                p = parameters(); p['sparse_cage']['layout'] = value
                with self.subTest(layout=value), self.assertRaises(RuntimeFailure):
                    build_sparse_panel(p)

    def test_balanced_corridor_cannot_cross_or_crowd_its_east_buffer(self):
        for xmax in (36.2, 37, 42):
            p = parameters(); p['sparse_cage']['layout']['feature_frame_mm'][2] = xmax
            # Other core/guard/frame intervals remain ordered; the balanced
            # east corridor is respectively too close, coincident, or crossed.
            with self.subTest(xmax=xmax), self.assertRaises(RuntimeFailure) as caught:
                validate_parameter_domain(p)
            self.assertEqual(caught.exception.code, 'SPARSE_FRAME_GRID_SPACING')
            self.assertEqual(caught.exception.details['required_minimum_mm'], .42)
            self.assertLess(caught.exception.details['observed_minimum_mm'], .42)
        self.assertEqual(FIXED_FRAME_MIN_ANGLE_DEGREES, 30.)
        self.assertEqual(FIXED_FRAME_MAX_ANGLE_DEGREES, 150.)

    def test_existing_full_body_insertion_policy_applies_to_both_versions(self):
        for schema in FIXED_FRAME_SCHEMAS:
            before = self.bodies[schema, False]
            for corridor, added in (('east', 44), ('south', 48)):
                for fraction in (.2, .8):
                    with self.subTest(schema=schema, corridor=corridor, fraction=fraction):
                        plan = plan_strip_insertion(before, corridor, fraction=fraction)
                        inserted = apply_strip_insertion(before, plan)
                        audit = validate_sparse_mesh(inserted)
                        self.assertEqual((audit['vertices'], audit['faces'], audit['boundary_edges']),
                                         (622+added, 622+added, 0))
                        p = deepcopy(inserted['authored_structure']['parameter_binding']['parameters'])
                        p['holes'][0].update(center=[-7, 2], radius=12.6)
                        a, b = semantic_mesh(inserted), semantic_mesh(build_sparse_panel(p))
                        self.assertEqual(a['connectivity'], b['connectivity'])
                        for sid in inserted['sparse_graph']['insertion_transaction']['new_vertex_ids']:
                            self.assertEqual(a['coordinates'][sid], b['coordinates'][sid])
                        edit = plan_parameter_edit(inserted, p)
                        self.assertTrue(all('/hole_rim/' in sid for sid in edit['allowed_vertex_axes']))


if __name__ == '__main__':
    unittest.main()
