# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST-only tests; no project dimensions, native or limit-surface claims."""
from copy import deepcopy
import unittest
from unittest import mock

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
from hardsurface.sparse_patch_graph import (
    AXIS_STRIP_POLICY, AXIS_STRIP_SCHEMA, _axis_strip_plan, _apply_axis_strip_insertion,
    make_axis_strip_reference, plan_strip_insertion, apply_strip_insertion,
    semantic_mesh, fingerprint, validate_sparse_mesh, edge_id)
from tests.test_sparse_balanced_layout import parameters as neutral_parameters


def parameters(support=False, insertions=None):
    p = neutral_parameters(support)
    p['sparse_cage']['insertion_policy'] = AXIS_STRIP_POLICY
    p['sparse_cage']['insertions'] = [] if insertions is None else deepcopy(insertions)
    return p


def private_base(support=False):
    mesh = build_sparse_panel(neutral_parameters(support))
    mesh['sparse_graph']['axis_strip_reference'] = make_axis_strip_reference(mesh, .42)
    return mesh


class AxisStripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bases = {s: private_base(s) for s in (False, True)}

    def assert_source_exact(self, before, after):
        old, new = semantic_mesh(before)['coordinates'], semantic_mesh(after)['coordinates']
        self.assertTrue(set(old) <= set(new))
        self.assertEqual(old, {sid: new[sid] for sid in old})

    def assert_current_loops(self, mesh, count):
        graph = mesh['sparse_graph']; loops = graph['semantic_inserted_loops']
        self.assertEqual(len(loops), count)
        port_roles = {p['role'] for p in graph['ports']}
        for row in loops:
            self.assertNotIn(row['role'], port_roles)
            self.assertEqual(row['point_order'], 'positive_axis_projected_cycle')
            self.assertEqual(row['expected_valence'], 4)
            self.assertEqual(len(row['edge_ids']), len(row['vertex_ids']))
            self.assertTrue(row['proof']['connected_closed_cycle'])
            self.assertTrue(row['proof']['opposite_edge_continuation'])
            self.assertEqual(row['proof']['native_extraction'], 'not_run')
            for sid in row['vertex_ids']:
                self.assertEqual(mesh['vertices_mm'][graph['vertex_map'][sid]][row['axis_index']], row['cut_mm'])
            self.assertEqual(row['edge_ids'], [edge_id(a, b) for a, b in zip(row['vertex_ids'], row['vertex_ids'][1:]+row['vertex_ids'][:1])])
        self.assertEqual(validate_sparse_mesh(mesh)['status'], 'pass')

    def test_common_base_interval_is_derived_from_every_crossed_edge(self):
        base = self.bases[False]; graph = base['sparse_graph']; ref = graph['axis_strip_reference']
        self.assertEqual(ref['minimum_spacing_mm'], .42)
        for corridor in ('east', 'south'):
            declaration = ref['corridors'][corridor]; axis = declaration['axis_index']
            ranges = [sorted(base['vertices_mm'][i][axis] for i in graph['edges'][graph['edge_map'][eid]])
                      for eid in declaration['base_split_edge_ids']]
            expected = [max(x[0] for x in ranges), min(x[1] for x in ranges)]
            self.assertEqual(declaration['common_interval_mm'], expected)
            for fraction in (.2, .5, .8):
                plan = _axis_strip_plan(base, corridor, fraction)
                self.assertEqual(plan['cut_mm'], expected[0]+fraction*(expected[1]-expected[0]))
                self.assertEqual(plan['source_graph_sha256'], fingerprint(graph))
                self.assertEqual(plan['axis_reference_sha256'], ref['sha256'])
                self.assertEqual(plan['schema_version'], AXIS_STRIP_SCHEMA)

    def test_ab_initial_east_then_all_distinct_east_or_south_fractions(self):
        for support, base in self.bases.items():
            frozen = deepcopy(base)
            for first_fraction in (.2, .5, .8):
                first_plan = _axis_strip_plan(base, 'east', first_fraction)
                first = _apply_axis_strip_insertion(base, first_plan)
                self.assertEqual(first_plan['added_faces'], 44)
                self.assert_source_exact(base, first); self.assert_current_loops(first, 1)
                for corridor in ('east', 'south'):
                    for fraction in (.2, .5, .8):
                        with self.subTest(support=support, first=first_fraction, corridor=corridor, fraction=fraction):
                            if corridor == 'east' and fraction == first_fraction:
                                with self.assertRaises(RuntimeFailure) as caught: _axis_strip_plan(first, corridor, fraction)
                                self.assertEqual(caught.exception.code, 'SPARSE_AXIS_SPACING')
                                continue
                            second_plan = _axis_strip_plan(first, corridor, fraction)
                            second = _apply_axis_strip_insertion(first, second_plan)
                            self.assertEqual(second_plan['added_faces'], 44 if corridor == 'east' else 50)
                            self.assertEqual(second_plan['source_topology_epoch'], 1)
                            self.assertEqual(second['sparse_graph']['topology_epoch'], 2)
                            self.assertEqual(second['sparse_graph']['axis_strip_reference'], base['sparse_graph']['axis_strip_reference'])
                            self.assert_source_exact(base, second); self.assert_source_exact(first, second)
                            self.assert_current_loops(second, 2)
                            for port in second['sparse_graph']['ports']:
                                self.assertEqual(len(port['vertex_ids']), 16 if '.hole_' in port['role'] else 36)
                            history = second['sparse_graph']['insertion_transactions']
                            self.assertEqual(history[0], first['sparse_graph']['insertion_transaction'])
                            self.assertEqual(history[-1], second['sparse_graph']['insertion_transaction'])
                            self.assertEqual(set(history[-1]['face_mapping']), set(first['sparse_graph']['face_map']))
                            self.assertEqual(set(history[-1]['edge_mapping']), set(first['sparse_graph']['edge_map']))
                            self.assertEqual(history[-1]['plan']['source_graph_sha256'], fingerprint(first['sparse_graph']))
                            self.assertEqual(history[-1]['result_semantic_sha256'], fingerprint(semantic_mesh(second)))
                            self.assertEqual(len(second['sparse_graph']['semantic_inserted_loops'][0]['vertex_ids']), 44 if corridor == 'east' else 46)
                            with self.assertRaises(RuntimeFailure): _axis_strip_plan(second, 'south', .5)
            self.assertEqual(base, frozen)

    def test_all_new_vertices_share_one_axis_even_when_edge_fractions_differ(self):
        base = self.bases[False]; plan = _axis_strip_plan(base, 'east', .5)
        self.assertGreater(len(set(plan['edge_fractions'].values())), 1)
        inserted = _apply_axis_strip_insertion(base, plan)
        self.assertEqual({semantic_mesh(inserted)['coordinates'][s][0] for s in inserted['sparse_graph']['insertion_transaction']['new_vertex_ids']}, {plan['cut_mm']})

    def test_tampered_plan_reference_or_old_vertex_rejects_without_mutating_source(self):
        base = self.bases[False]; frozen = deepcopy(base); plan = _axis_strip_plan(base, 'east', .5)
        changes = {'cut_mm': plan['cut_mm']+.01, 'source_topology_epoch': 1,
                   'policy': 'linear', 'source_graph_sha256': 'bad', 'axis_reference_sha256': 'bad',
                   'steps': [], 'edge_fractions': {}, 'added_faces': 45}
        for key, value in changes.items():
            bad = deepcopy(plan); bad[key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeFailure): _apply_axis_strip_insertion(base, bad)
        bad = deepcopy(base); bad['sparse_graph']['axis_strip_reference']['minimum_spacing_mm'] /= 2
        with self.assertRaises(RuntimeFailure): _axis_strip_plan(bad, 'east', .5)
        bad = deepcopy(base); sid = next(iter(bad['sparse_graph']['axis_strip_reference']['base_vertex_coordinates_mm']))
        bad['vertices_mm'][bad['sparse_graph']['vertex_map'][sid]][2] += .0001
        with self.assertRaises(RuntimeFailure): _apply_axis_strip_insertion(bad, plan)
        self.assertEqual(base, frozen)

    def test_nearby_cut_and_endpoint_coincidence_are_rejected(self):
        base = self.bases[False]; first = _apply_axis_strip_insertion(base, _axis_strip_plan(base, 'east', .5))
        for fraction in (.5, .50001, .51):
            with self.subTest(fraction=fraction), self.assertRaises(RuntimeFailure) as caught:
                _axis_strip_plan(first, 'east', fraction)
            self.assertEqual(caught.exception.code, 'SPARSE_AXIS_SPACING')
        for corridor, fraction in (('west', .5), ('east', .19), ('east', float('nan')), ('east', True)):
            with self.subTest(corridor=corridor, fraction=fraction), self.assertRaises(RuntimeFailure):
                _axis_strip_plan(base, corridor, fraction)

    def test_nonstraddling_route_and_wrong_original_anchor_subchain_reject(self):
        base = self.bases[False]
        changed = deepcopy(base); ref = changed['sparse_graph']['axis_strip_reference']
        # A forged wider intersection still straddles the outer seed, but not
        # every core edge; the route must reject it rather than extrapolate.
        row = ref['corridors']['east']; row['common_interval_mm'][1] += 6
        ref['sha256'] = fingerprint({k: v for k, v in ref.items() if k != 'sha256'})
        with self.assertRaises(RuntimeFailure) as caught: _axis_strip_plan(changed, 'east', .8)
        self.assertEqual(caught.exception.code, 'SPARSE_AXIS_NONSTRADDLING')
        changed = deepcopy(base); ref = changed['sparse_graph']['axis_strip_reference']
        row = ref['corridors']['east']; port = next(p for p in changed['sparse_graph']['ports'] if p['role'] == row['seed_port_role'])
        row['seed_anchor_vertex_ids'][1] = port['vertex_ids'][(port['vertex_ids'].index(row['seed_anchor_vertex_ids'][1])+1) % len(port['vertex_ids'])]
        ref['sha256'] = fingerprint({k: v for k, v in ref.items() if k != 'sha256'})
        with self.assertRaises(RuntimeFailure) as caught: _axis_strip_plan(changed, 'east', .5)
        self.assertEqual(caught.exception.code, 'SPARSE_INSERTION_SEED')

    def test_existing_generated_identity_collision_fails_closed(self):
        base = self.bases[False]; first = _apply_axis_strip_insertion(base, _axis_strip_plan(base, 'east', .5))
        plan = _axis_strip_plan(first, 'east', .2)
        existing = first['sparse_graph']['insertion_transaction']['new_vertex_ids'][0]
        original = fingerprint
        def collision(value):
            if isinstance(value, list) and value[:3] == [AXIS_STRIP_POLICY, 1, 'east']:
                return existing.rsplit(':', 1)[-1]+'0'*24
            return original(value)
        with mock.patch('hardsurface.sparse_patch_graph.fingerprint', side_effect=collision):
            with self.assertRaises(RuntimeFailure) as caught: _apply_axis_strip_insertion(first, plan)
        self.assertEqual(caught.exception.code, 'SPARSE_AXIS_ID_COLLISION')

    def test_loop_metadata_tampering_is_checked_against_actual_mesh(self):
        base = self.bases[False]; first = _apply_axis_strip_insertion(base, _axis_strip_plan(base, 'east', .5))
        for key, value in (('vertex_indices', []), ('expected_valence', 3), ('closed', False), ('cut_mm', 0)):
            changed = deepcopy(first); changed['sparse_graph']['semantic_inserted_loops'][0][key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeFailure): validate_sparse_mesh(changed)

    def test_public_constructor_replays_two_transactions_and_binds_identity(self):
        base = build_sparse_panel(parameters())
        first = apply_strip_insertion(base, plan_strip_insertion(base, 'east', fraction=.5))
        second = apply_strip_insertion(first, plan_strip_insertion(first, 'south', fraction=.8))
        self.assertEqual(second['authored_structure']['parameter_binding']['parameters']['sparse_cage']['insertions'],
                         [{'corridor': 'east', 'fraction': .5}, {'corridor': 'south', 'fraction': .8}])
        self.assertEqual(validate_sparse_authored_identity(second)['status'], 'pass')
        self.assertEqual(second, build_sparse_panel(second['authored_structure']['parameter_binding']['parameters']))
        self.assert_source_exact(base, second)
        changed = deepcopy(first); changed['sparse_graph']['insertion_transactions'][0]['plan']['cut_mm'] += .01
        with self.assertRaises(RuntimeFailure): plan_strip_insertion(changed, 'south')

    def test_axis_host_physical_graph_reordering_rejects_without_mutation(self):
        base = build_sparse_panel(parameters()); changed = deepcopy(base)
        graph = changed['sparse_graph']; count = len(graph['edges'])
        graph['edges'].reverse()
        graph['edge_map'] = {sid: count-1-i for sid, i in graph['edge_map'].items()}
        frozen = deepcopy(changed)
        self.assertEqual(validate_sparse_mesh(changed)['status'], 'pass')
        self.assertEqual(validate_sparse_authored_identity(changed)['status'], 'pass')
        with self.assertRaises(RuntimeFailure) as caught: plan_strip_insertion(changed, 'east')
        self.assertEqual(caught.exception.code, 'SPARSE_INSERTION_SOURCE_LAYOUT')
        with self.assertRaises(RuntimeFailure): apply_strip_insertion(changed, plan_strip_insertion(base, 'east'))
        self.assertEqual(changed, frozen)
        # The old policy still operates semantically on valid HOST storage.
        legacy = build_sparse_panel(neutral_parameters()); original = plan_strip_insertion(legacy, 'east')
        graph = legacy['sparse_graph']; count = len(graph['edges']); graph['edges'].reverse()
        graph['edge_map'] = {sid: count-1-i for sid, i in graph['edge_map'].items()}
        self.assertEqual(plan_strip_insertion(legacy, 'east'), original)
        self.assertEqual(apply_strip_insertion(legacy, original)['sparse_graph']['topology_epoch'], 1)

    def test_absent_policy_keeps_legacy_plan_and_one_insert_only(self):
        base = build_sparse_panel(neutral_parameters())
        plan = plan_strip_insertion(base, 'east')
        self.assertEqual(plan['schema_version'], 'sparse-strip-insertion/1.0')
        self.assertNotIn('policy', plan)
        result = apply_strip_insertion(base, plan)
        self.assertNotIn('axis_strip_reference', result['sparse_graph'])
        self.assertNotIn('semantic_inserted_loops', result['sparse_graph'])
        with self.assertRaises(RuntimeFailure): plan_strip_insertion(result, 'south')


if __name__ == '__main__': unittest.main()
