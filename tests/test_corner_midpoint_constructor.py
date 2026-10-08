# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST regression for explicit midpoint structural corner columns."""
from copy import deepcopy
from functools import lru_cache
import struct
import unittest
from unittest import mock

from hardsurface.corner_column_geometry import SCHEMA, CONSTRUCTOR_SCHEMA, structural_loop
from hardsurface.io import RuntimeFailure
from hardsurface.quad_quality import validate_mesh
from hardsurface.sparse_panel_geometry import (build_sparse_panel, sparse_config,
    validate_sparse_authored_identity, FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA)
from hardsurface.sparse_parameter_edit import plan_parameter_edit
from hardsurface.sparse_patch_graph import (edge_id, fingerprint, semantic_mesh, validate_sparse_mesh)
from tests.test_sparse_axis_geometry import parameters as axis_parameters


EAST = (('east', .5),)
EAST_EAST = EAST + (('east', .75),)
EAST_SOUTH = EAST + (('south', .5),)
CASES = ((False, FIXED_FRAME_SCHEMA), (True, BALANCED_FIXED_FRAME_SCHEMA))


def parameters(support=False, schema=FIXED_FRAME_SCHEMA, route=()):
    result = axis_parameters(support, schema, route)
    result['sparse_cage']['corner_columns'] = {'schema': SCHEMA}
    return result


@lru_cache(maxsize=None)
def body(support=False, schema=FIXED_FRAME_SCHEMA, route=()):
    return build_sparse_panel(parameters(support, schema, route))


class MidpointConstructorTests(unittest.TestCase):
    def test_selector_is_explicit_exact_and_has_no_fit_controls(self):
        self.assertNotIn('corner_columns', sparse_config(axis_parameters()))
        self.assertEqual(sparse_config(parameters())['corner_columns'], {'schema': SCHEMA})
        for value in (None, {}, [], True, {'schema': 'unknown'},
                      {'schema': SCHEMA, 'coefficients': [.5]*4}):
            p = parameters(); p['sparse_cage']['corner_columns'] = value
            with self.subTest(value=value), mock.patch(
                    'hardsurface.sparse_panel_geometry.SparseMeshBuilder', side_effect=AssertionError('allocated')):
                with self.assertRaises(RuntimeFailure):
                    build_sparse_panel(p)
        for key in ('layout', 'insertion_policy'):
            p = parameters(); p['sparse_cage'].pop(key)
            with self.subTest(missing=key), self.assertRaises(RuntimeFailure):
                sparse_config(p)
        p = parameters(); p.pop('topology_strategy')
        with self.assertRaises(RuntimeFailure):
            sparse_config(p)
        for profile in (2, 3, 5, 8):
            p = parameters(); p['sparse_cage']['profile_arc_segments'] = profile
            with self.subTest(profile=profile), self.assertRaises(RuntimeFailure):
                sparse_config(p)

    def test_two_baseline_routes_are_real_complete_regular_loops(self):
        for support, schema in CASES:
            source = body(support, schema); graph = source['sparse_graph']
            audit = validate_sparse_mesh(source)
            count = 742 if support else 710
            self.assertEqual((audit['vertices'], audit['faces'], audit['edges']), (count, count, count*2))
            self.assertEqual((audit['components'], audit['boundary_edges'], audit['euler']), (1, 0, 0))
            self.assertEqual(graph['topology_epoch'], 0)
            self.assertNotIn('insertion_transaction', graph)
            self.assertEqual(graph['constructor_topology'], {'schema': SCHEMA, 'base_outer_segments': 36})
            self.assertEqual(len(graph['structural_vertex_lineage']), 88)
            self.assertEqual(len(graph['structural_face_lineage']), 176)
            for port in graph['ports']:
                self.assertEqual(len(port['vertex_ids']), 16 if '.hole_' in port['role'] else 36)
            loops = graph['semantic_structural_loops']
            self.assertEqual({row['role'] for row in loops},
                             {'structural.corner_column.west', 'structural.corner_column.east'})
            for row in loops:
                self.assertEqual(len(row['vertex_ids']), 44)
                self.assertEqual(row, structural_loop(source, row['role'], row['vertex_ids']))
                self.assertNotIn('axis_index', row)
                self.assertNotIn('cut_mm', row)
            self.assertEqual(set().union(*(set(row['vertex_ids']) for row in loops)), set(graph['structural_vertex_lineage']))

    def test_midpoints_preserve_all_old_coordinates_and_hole_topology(self):
        for support, schema in CASES:
            legacy = build_sparse_panel(axis_parameters(support, schema))
            result = body(support, schema)
            a, b = semantic_mesh(legacy), semantic_mesh(result)
            self.assertEqual({s: b['coordinates'][s] for s in a['coordinates']}, a['coordinates'])
            for sid, row in result['sparse_graph']['structural_vertex_lineage'].items():
                first, second = row['source_endpoint_vertex_ids']
                self.assertEqual(row['source_edge_id'], edge_id(first, second))
                self.assertEqual(b['coordinates'][sid], [(x+y)/2 for x, y in zip(a['coordinates'][first], a['coordinates'][second])])
                self.assertEqual((row['placement'], row['fraction']), ('arithmetic_edge_midpoint', .5))
            self.assertEqual(a['creases'], b['creases'])
            for sid, index in legacy['sparse_graph']['face_map'].items():
                role = legacy['face_provenance'][index]['surface_role']
                if 'hole' in role:
                    self.assertEqual(a['connectivity'][sid], b['connectivity'][sid])

    def test_user_axis_epochs_and_actual_ports_are_separate_from_structural_loops(self):
        for support, schema in CASES:
            baseline = body(support, schema)
            reference = baseline['sparse_graph']['axis_strip_reference']
            for route in (EAST, EAST_EAST, EAST_SOUTH):
                with self.subTest(support=support, route=route):
                    source = body(support, schema, route); graph = source['sparse_graph']
                    self.assertEqual(graph['topology_epoch'], len(route))
                    self.assertEqual(graph['axis_strip_reference'], reference)
                    self.assertEqual(len(graph['insertion_transactions']), len(route))
                    self.assertEqual(len(graph['semantic_inserted_loops']), len(route))
                    self.assertEqual(graph['structural_vertex_lineage'], baseline['sparse_graph']['structural_vertex_lineage'])
                    self.assertEqual(graph['structural_face_lineage'], baseline['sparse_graph']['structural_face_lineage'])
                    for port in graph['ports']:
                        self.assertEqual(len(port['vertex_ids']), 16 if '.hole_' in port['role'] else 36+2*len(route))
                    self.assertEqual(len(source['faces'])-len(baseline['faces']),
                                     sum(tx['plan']['added_faces'] for tx in graph['insertion_transactions']))
                    if len(route) == 1:
                        self.assertEqual(len(source['faces']), 786 if support else 754)
                    authored = {row['role']: row for row in source['authored_structure']['semantic_control_loops']}
                    for row in graph['semantic_structural_loops']:
                        self.assertEqual(authored[row['role']]['vertex_ids'], row['vertex_ids'])
                        self.assertEqual(row, structural_loop(source, row['role'], row['vertex_ids']))

    def test_hole_edits_keep_every_old_and_new_exterior_identity_and_coordinate(self):
        for support, schema in CASES:
            for route in (EAST, EAST_SOUTH):
                source = body(support, schema, route)
                p = parameters(support, schema, route)
                p['holes'][0].update(center=[-7, 2], radius=12.6)
                result = build_sparse_panel(p)
                a, b = semantic_mesh(source), semantic_mesh(result)
                self.assertEqual(a['connectivity'], b['connectivity'])
                self.assertEqual(a['creases'], b['creases'])
                self.assertEqual(set(a['coordinates']), set(b['coordinates']))
                ring_ids = {s for s in a['coordinates'] if '/hole_rim/' in s or '/hole_support/' in s}
                self.assertEqual({s for s in a['coordinates'] if a['coordinates'][s] != b['coordinates'][s]}, ring_ids)
                for key in ('vertex_map', 'face_map', 'constructor_topology', 'semantic_structural_loops',
                            'structural_vertex_lineage', 'structural_face_lineage', 'axis_strip_reference'):
                    self.assertEqual(source['sparse_graph'][key], result['sparse_graph'][key])
                self.assertEqual(set(plan_parameter_edit(source, p)['allowed_vertex_axes']), ring_ids)

    def test_bevel_dependencies_include_all_affected_corner_and_axis_descendants(self):
        source = body(True, BALANCED_FIXED_FRAME_SCHEMA, EAST_SOUTH)
        p = parameters(True, BALANCED_FIXED_FRAME_SCHEMA, EAST_SOUTH); p['edge_bevel'] = 2.0
        result = build_sparse_panel(p)
        a, b = semantic_mesh(source), semantic_mesh(result)
        dep = set(source['authored_structure']['edit_dependencies']['edge_bevel']['vertex_ids'])
        changed = {s for s in a['coordinates'] if a['coordinates'][s] != b['coordinates'][s]}
        corner_ids = set(source['sparse_graph']['structural_vertex_lineage'])
        self.assertTrue(changed & corner_ids)
        self.assertTrue(changed <= dep)
        self.assertFalse(any('/hole_' in s for s in dep))
        self.assertEqual(a['connectivity'], b['connectivity'])
        self.assertEqual(set(plan_parameter_edit(source, p)['allowed_vertex_axes']), dep)

    def test_both_thickness_datums_change_only_z(self):
        source = body(route=EAST)
        a = semantic_mesh(source)
        for datum, limits in (('fixed_bottom', (-4, 5)), ('fixed_midplane', (-5, 5))):
            p = parameters(route=EAST); p['z_min'], p['z_max'] = limits
            result = build_sparse_panel(p); b = semantic_mesh(result)
            self.assertEqual(a['connectivity'], b['connectivity'])
            self.assertEqual({s: xyz[:2] for s, xyz in a['coordinates'].items()},
                             {s: xyz[:2] for s, xyz in b['coordinates'].items()})
            self.assertEqual(set(tuple(axes) for axes in plan_parameter_edit(source, p, datum=datum)['allowed_vertex_axes'].values()), {(2,)})

    def test_constructor_metadata_and_authorship_are_real_replay_without_proposal_flags(self):
        for support, schema in CASES:
            source = body(support, schema, EAST)
            self.assertEqual(fingerprint(source), fingerprint(build_sparse_panel(parameters(support, schema, EAST))))
            self.assertEqual(validate_sparse_authored_identity(source)['status'], 'pass')
            self.assertNotIn('corner_column_proposal', source)
            info = source['metadata']['subdivision_cage']
            self.assertEqual((info['constructor_schema'], info['constructor_version']), (CONSTRUCTOR_SCHEMA, 6))
            self.assertEqual(info['initial_topology']['outer_segments'], 36)
            self.assertEqual(info['initial_top_plane_quads'], 165 if support else 149)
            self.assertEqual(info['top_plane_candidate_quads'], info['region_face_counts']['top'])
            self.assertEqual(info['structural_corner_loop_count'], 2)
            self.assertTrue(all(v == 'not_run' for k, v in info['evidence'].items() if k != 'host_graph'))

    def test_forged_structural_lineage_or_loop_proof_fails_authorship(self):
        source = body(route=EAST)
        for key in ('structural_vertex_lineage', 'structural_face_lineage', 'semantic_structural_loops'):
            bad = deepcopy(source)
            if key == 'semantic_structural_loops':
                bad['sparse_graph'][key][0]['proof']['native_extraction'] = 'pass'
            else:
                first = next(iter(bad['sparse_graph'][key]))
                bad['sparse_graph'][key][first]['forged'] = True
            with self.subTest(key=key), self.assertRaises(RuntimeFailure):
                validate_sparse_authored_identity(bad)

    def test_selector_cannot_migrate_an_old_body_or_change_via_nominal_edit(self):
        before = build_sparse_panel(axis_parameters())
        with self.assertRaises(RuntimeFailure) as caught:
            plan_parameter_edit(before, parameters())
        self.assertEqual(caught.exception.code, 'AUTHORED_EDIT_SCOPE_UNSUPPORTED')
        p = parameters(); p['corner_radius'] += .5
        with self.assertRaises(RuntimeFailure) as caught:
            plan_parameter_edit(body(), p)
        self.assertEqual(caught.exception.code, 'AUTHORED_EDIT_SCOPE_UNSUPPORTED')

    def test_complete_control_cages_pass_authored_and_native_float32_quality(self):
        for support, schema in CASES:
            for route in ((), EAST):
                source = body(support, schema, route)
                for storage in ('authored', 'float32_metres'):
                    points = [[x*.001 for x in p] for p in source['vertices_mm']]
                    if storage == 'float32_metres':
                        points = [[struct.unpack('f', struct.pack('f', x))[0] for x in p] for p in points]
                    report = validate_mesh(points, source['faces'], face_provenance=source['face_provenance'],
                        identity={'evidence_origin': 'neutral_host_test'}, mesh_state='control')
                    self.assertTrue(report['passed'], (support, route, storage, report))


if __name__ == '__main__':
    unittest.main()
