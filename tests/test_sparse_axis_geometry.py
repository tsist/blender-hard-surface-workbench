# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST-only axis constructor checks; no project or Blender approval."""
from copy import deepcopy
from functools import lru_cache
import random
import struct
import unittest
from unittest import mock

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import (AXIS_INSERTION_POLICY,
    AXIS_CONSTRUCTOR_SCHEMA, FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA,
    build_sparse_panel, sparse_config, validate_sparse_authored_identity)
from hardsurface.sparse_parameter_edit import plan_parameter_edit
from hardsurface.sparse_patch_graph import (fingerprint, semantic_mesh,
    validate_sparse_mesh, plan_strip_insertion, apply_strip_insertion)
from tests.test_sparse_balanced_layout import parameters as fixed_parameters


def parameters(support=False, schema=FIXED_FRAME_SCHEMA, route=()):
    result = fixed_parameters(support, schema)
    result['sparse_cage']['insertion_policy'] = AXIS_INSERTION_POLICY
    result['sparse_cage']['insertions'] = [
        {'corridor': corridor, 'fraction': fraction} for corridor, fraction in route]
    return result


@lru_cache(maxsize=None)
def body(support=False, schema=FIXED_FRAME_SCHEMA, route=()):
    return build_sparse_panel(parameters(support, schema, route))


EAST = (('east', .5),)
EAST_EAST = EAST + (('east', .75),)
EAST_SOUTH = EAST + (('south', .5),)
CASES = ((False, FIXED_FRAME_SCHEMA), (True, BALANCED_FIXED_FRAME_SCHEMA))

# Captured from the unchanged constructor, including every serialized field.
LEGACY_INSERTED_SHA256 = {
    (FIXED_FRAME_SCHEMA, False, 'east'): 'ba496a0c0ffe377dcc3dd97fb7f2c592375e59ff9b2eeb6f9da495e21a229e2a',
    (FIXED_FRAME_SCHEMA, True, 'east'): '83549cf262c1ad7ae3ddb967de9f14ea8f57c9f0973f937810c71755730e9b6c',
    (FIXED_FRAME_SCHEMA, False, 'south'): '1f92ab98ac7c821f405e9631e5ce3f78d3dd4248bee97b0388ca2d0e95311880',
    (FIXED_FRAME_SCHEMA, True, 'south'): '7dd927bb76df1e96496190953435f1cc7bf890b3ed821ca92f7dbe595dcb38d1',
    (BALANCED_FIXED_FRAME_SCHEMA, False, 'east'): 'eaa8170a2ad509c8194e1a777e8fd32556683b54fdf7e6373b34725ff6d8faad',
    (BALANCED_FIXED_FRAME_SCHEMA, True, 'east'): 'a084aa1350066a20afb04aed25f6ee3513d12e39ea15c1de52cce75cb92c6556',
    (BALANCED_FIXED_FRAME_SCHEMA, False, 'south'): '47a7fc3b7c4c5b77725db5d2ef55a934e159fdc849cecdf6cf8605066a9039cd',
    (BALANCED_FIXED_FRAME_SCHEMA, True, 'south'): 'be5280ec38264a1d28f87bf18e40273999be221872a846cb69b0d518f592a5b9'}


class AxisConstructorTests(unittest.TestCase):
    def test_explicit_policy_and_layout_are_required_without_implicit_migration(self):
        for schema in (FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA):
            p = parameters(schema=schema)
            self.assertEqual(sparse_config(p)['insertion_policy'], AXIS_INSERTION_POLICY)
            self.assertEqual(p, parameters(schema=schema))
        for policy in (None, '', 'axis_plane_v2', True, {}):
            p = parameters(); p['sparse_cage']['insertion_policy'] = policy
            with self.subTest(policy=policy), self.assertRaises(RuntimeFailure) as caught:
                sparse_config(p)
            self.assertEqual(caught.exception.code, 'SPARSE_INSERTION_POLICY')
        p = parameters(schema=None)
        with self.assertRaises(RuntimeFailure) as caught:
            sparse_config(p)
        self.assertEqual(caught.exception.code, 'SPARSE_INSERTION_POLICY')
        legacy = fixed_parameters()
        self.assertNotIn('insertion_policy', sparse_config(legacy))
        legacy['sparse_cage']['insertions'] = [dict(corridor=c, fraction=f) for c, f in EAST_EAST]
        with self.assertRaises(RuntimeFailure) as caught:
            sparse_config(legacy)
        self.assertEqual(caught.exception.code, 'SPARSE_INSERTION_DOMAIN')

    def test_constructor_repeats_exactly_and_covers_every_body_interface(self):
        for support, schema in CASES:
            for route, extra in (((), 0), (EAST, 44), (EAST_EAST, 88), (EAST_SOUTH, 94)):
                with self.subTest(support=support, schema=schema, route=route):
                    source = body(support, schema, route)
                    self.assertEqual(fingerprint(source), fingerprint(build_sparse_panel(parameters(support, schema, route))))
                    report = validate_sparse_mesh(source)
                    count = (654 if support else 622) + extra
                    self.assertEqual((report['vertices'], report['edges'], report['faces']), (count, count*2, count))
                    self.assertEqual((report['components'], report['boundary_edges'], report['euler']), (1, 0, 0))
                    graph = source['sparse_graph']
                    self.assertEqual(graph['topology_epoch'], len(route))
                    for port in graph['ports']:
                        self.assertEqual(len(port['vertex_ids']), 16 if '.hole_' in port['role'] else 32+2*len(route))
                    inserted = graph.get('semantic_inserted_loops', [])
                    self.assertEqual(len(inserted), len(route))
                    authored = {row['role']: row for row in source['authored_structure']['semantic_control_loops']}
                    ordinary_roles = {row['role'] for row in graph['ports']}
                    for loop in inserted:
                        self.assertNotIn(loop['role'], ordinary_roles)
                        self.assertEqual(authored[loop['role']]['vertex_ids'], loop['vertex_ids'])
                        self.assertEqual(authored[loop['role']]['expected_valence'], 4)
                        self.assertTrue(loop['proof']['opposite_edge_continuation'])
                        self.assertEqual({source['vertices_mm'][i][loop['axis_index']] for i in loop['vertex_indices']}, {loop['cut_mm']})

    def test_fractions_keep_base_intervals_across_continuing_cuts(self):
        for support, schema in CASES:
            original = body(support, schema)
            reference = original['sparse_graph']['axis_strip_reference']
            first = body(support, schema, EAST)
            for route in (EAST_EAST, EAST_SOUTH):
                result = body(support, schema, route); graph = result['sparse_graph']
                self.assertEqual(graph['axis_strip_reference'], reference)
                self.assertEqual(graph['insertion_transactions'][0], first['sparse_graph']['insertion_transaction'])
                latest = graph['insertion_transaction']
                self.assertEqual(latest, graph['insertion_transactions'][-1])
                self.assertEqual(latest['plan']['source_topology_epoch'], 1)
                self.assertEqual(latest['plan']['source_sha256'], fingerprint(semantic_mesh(first)))
                self.assertEqual(set(latest['vertex_mapping']), set(first['sparse_graph']['vertex_map']))
                lo, hi = latest['plan']['base_common_interval_mm']
                self.assertEqual(latest['plan']['cut_mm'], lo+route[-1][1]*(hi-lo))

    def test_hole_edits_write_protect_all_inserted_vertices_after_two_cuts(self):
        for support, schema in CASES:
            for route in (EAST_EAST, EAST_SOUTH):
                before = body(support, schema, route)
                p = parameters(support, schema, route)
                p['holes'][0].update(center=[-7, 2], radius=12.6)
                after = build_sparse_panel(p)
                a, b = semantic_mesh(before), semantic_mesh(after)
                ring_ids = {sid for sid in a['coordinates'] if '/hole_rim/' in sid or '/hole_support/' in sid}
                changed = {sid for sid in a['coordinates'] if a['coordinates'][sid] != b['coordinates'][sid]}
                self.assertEqual(changed, ring_ids)
                self.assertEqual(a['connectivity'], b['connectivity'])
                self.assertEqual(a['creases'], b['creases'])
                self.assertEqual(before['sparse_graph']['axis_strip_reference'], after['sparse_graph']['axis_strip_reference'])
                edit = plan_parameter_edit(before, p)
                self.assertEqual(set(edit['allowed_vertex_axes']), ring_ids)
                generated = set().union(*(set(tx['new_vertex_ids']) for tx in before['sparse_graph']['insertion_transactions']))
                self.assertFalse(generated & set(edit['allowed_vertex_axes']))

    def test_prior_outer_descendants_survive_bevel_and_explicit_thickness_edits(self):
        before = body(True, BALANCED_FIXED_FRAME_SCHEMA, EAST_SOUTH)
        p = parameters(True, BALANCED_FIXED_FRAME_SCHEMA, EAST_SOUTH)
        p['edge_bevel'] = 2.0
        p['z_max'] = 5.0
        edit = plan_parameter_edit(before, p, datum='fixed_bottom')
        after = build_sparse_panel(p)
        a, b = semantic_mesh(before), semantic_mesh(after)
        self.assertEqual(a['connectivity'], b['connectivity'])
        self.assertEqual(set(a['coordinates']), set(b['coordinates']))
        bevel_ids = set(before['authored_structure']['edit_dependencies']['edge_bevel']['vertex_ids'])
        first = body(True, BALANCED_FIXED_FRAME_SCHEMA, EAST)
        first_outer = set(first['authored_structure']['edit_dependencies']['edge_bevel']['vertex_ids'])
        self.assertTrue(first_outer <= bevel_ids)
        for sid, xyz in a['coordinates'].items():
            for axis in range(3):
                if xyz[axis] != b['coordinates'][sid][axis]:
                    self.assertIn(axis, edit['allowed_vertex_axes'][sid])
                    if axis != 2:
                        self.assertIn(sid, bevel_ids)
        moved = deepcopy(p); moved['holes'][0].update(center=[-7, 2], radius=12.6)
        hole_edit = plan_parameter_edit(after, moved)
        final = semantic_mesh(build_sparse_panel(moved))
        self.assertEqual({sid for sid in b['coordinates'] if b['coordinates'][sid] != final['coordinates'][sid]}, set(hole_edit['allowed_vertex_axes']))
        self.assertTrue(all('/hole_rim/' in sid or '/hole_support/' in sid for sid in hole_edit['allowed_vertex_axes']))

    def test_policy_and_history_cannot_be_changed_as_coordinate_edits(self):
        source = body(route=EAST)
        for key, value in (('insertion_policy', None), ('insertions', [dict(corridor='south', fraction=.5)])):
            p = parameters(route=EAST)
            if value is None:
                p['sparse_cage'].pop(key)
            else:
                p['sparse_cage'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeFailure) as caught:
                plan_parameter_edit(source, p)
            self.assertEqual(caught.exception.code, 'AUTHORED_EDIT_SCOPE_UNSUPPORTED')

    def test_duplicate_nearby_and_third_insertions_fail_closed(self):
        original = parameters(route=EAST+EAST)
        with mock.patch('hardsurface.sparse_panel_geometry.SparseMeshBuilder', side_effect=AssertionError('allocated')):
            with self.assertRaises(RuntimeFailure) as caught:
                build_sparse_panel(original)
        self.assertEqual(caught.exception.code, 'SPARSE_AXIS_SPACING')
        for route, code in ((EAST+(('east', .50001),), 'SPARSE_AXIS_SPACING'),
                            (EAST_SOUTH+(('east', .75),), 'SPARSE_INSERTION_DOMAIN')):
            p = parameters(route=route); saved = deepcopy(p)
            with self.subTest(route=route), self.assertRaises(RuntimeFailure) as caught:
                build_sparse_panel(p)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(p, saved)

    def test_axis_metadata_states_actual_counts_and_unqualified_scope(self):
        for support, schema in CASES:
            for route in ((), EAST, EAST_EAST, EAST_SOUTH):
                source = body(support, schema, route); info = source['metadata']['subdivision_cage']
                self.assertEqual((info['constructor_schema'], info['constructor_version']), (AXIS_CONSTRUCTOR_SCHEMA, 5))
                self.assertEqual(info['initial_top_plane_quads'], 143 if support else 127)
                self.assertEqual(info['top_plane_candidate_quads'], info['region_face_counts']['top'])
                self.assertEqual(info['inserted_axis_loop_count'], len(route))
                self.assertEqual(info['insertion_transactions'], source['sparse_graph'].get('insertion_transactions', []))
                self.assertEqual(info['insertion_policy']['whole_domain_evaluated_acceptance'], 'not_run')
                self.assertTrue(all(value == 'not_run' for key, value in info['evidence'].items() if key != 'host_graph'))

    def test_forged_reference_lineage_or_inserted_loop_proof_is_rejected(self):
        original = body(route=EAST_SOUTH)
        for field in ('axis_strip_reference', 'insertion_transactions', 'semantic_inserted_loops'):
            bad = deepcopy(original)
            if field == 'axis_strip_reference':
                bad['sparse_graph'][field]['minimum_spacing_mm'] *= 2
                payload = {k: v for k, v in bad['sparse_graph'][field].items() if k != 'sha256'}
                bad['sparse_graph'][field]['sha256'] = fingerprint(payload)
            elif field == 'insertion_transactions':
                bad['sparse_graph'][field][0]['commit'] = 'forged'
            else:
                bad['sparse_graph'][field][0]['proof']['native_extraction'] = 'pass'
            with self.subTest(field=field), self.assertRaises(RuntimeFailure):
                validate_sparse_authored_identity(bad)

    def test_nondefault_feature_owner_is_reconstructible(self):
        source = build_sparse_panel(parameters(route=EAST_EAST), feature_id='neutral_axis_feature')
        self.assertEqual(validate_sparse_authored_identity(source)['status'], 'pass')
        self.assertTrue(all(port['owner_feature_id'] == 'neutral_axis_feature' for port in source['sparse_graph']['ports']))

    def test_two_cut_native_transport_uses_semantic_ids_after_storage_permutation(self):
        source = deepcopy(body(True, BALANCED_FIXED_FRAME_SCHEMA, EAST_SOUTH))
        source.pop('sparse_graph')
        rng = random.Random(51)
        vertices = list(range(len(source['vertices_mm']))); faces = list(range(len(source['faces'])))
        rng.shuffle(vertices); rng.shuffle(faces)
        vm = {old: new for new, old in enumerate(vertices)}
        fm = {old: new for new, old in enumerate(faces)}
        source['vertices_mm'] = [[struct.unpack('f', struct.pack('f', x*.001))[0]*1000.0
                                  for x in source['vertices_mm'][i]] for i in vertices]
        source['faces'] = [[vm[v] for v in source['faces'][i]] for i in faces]
        source['face_provenance'] = [source['face_provenance'][i] for i in faces]
        source['edge_creases'] = [[vm[a], vm[b], value] for a, b, value in source['edge_creases']]
        manifest = source['authored_structure']
        manifest['vertex_map'] = {sid: vm[i] for sid, i in manifest['vertex_map'].items()}
        manifest['face_map'] = {sid: fm[i] for sid, i in manifest['face_map'].items()}
        self.assertEqual(validate_sparse_authored_identity(source)['status'], 'pass')
        a, b = list(manifest['vertex_map'])[:2]
        manifest['vertex_map'][a], manifest['vertex_map'][b] = manifest['vertex_map'][b], manifest['vertex_map'][a]
        with self.assertRaises(RuntimeFailure):
            validate_sparse_authored_identity(source)

    def test_saved_legacy_linear_insertions_reconstruct_byte_exact(self):
        for (schema, support, corridor), digest in LEGACY_INSERTED_SHA256.items():
            p = fixed_parameters(support, schema)
            p['sparse_cage']['insertions'] = [{'corridor': corridor, 'fraction': .5}]
            with self.subTest(schema=schema, support=support, corridor=corridor):
                self.assertEqual(fingerprint(build_sparse_panel(p)), digest)

    def test_public_wrapper_appends_the_second_declaration(self):
        first = body(route=EAST)
        saved = fingerprint(first)
        plan = plan_strip_insertion(first, 'south', fraction=.5)
        result = apply_strip_insertion(first, plan)
        self.assertEqual(result['authored_structure']['parameter_binding']['parameters']['sparse_cage']['insertions'],
                         [{'corridor': 'east', 'fraction': .5}, {'corridor': 'south', 'fraction': .5}])
        self.assertEqual(fingerprint(result), fingerprint(body(route=EAST_SOUTH)))
        self.assertEqual(fingerprint(first), saved)


if __name__ == '__main__':
    unittest.main()
