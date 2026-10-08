# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST checks of versioned corner construction and axis ancestry."""
from copy import deepcopy
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
from hardsurface.sparse_patch_graph import (
    AXIS_STRIP_POLICY, CORNER_AXIS_REFERENCE_SCHEMA, CORNER_TOPOLOGY_SCHEMA,
    _axis_strip_plan, _apply_axis_strip_insertion, edge_id, fingerprint,
    make_axis_strip_reference, plan_strip_insertion, apply_strip_insertion,
    semantic_mesh, validate_sparse_mesh)
from tests.test_sparse_balanced_layout import parameters as neutral_parameters


def parameters(support=False, route=()):
    result = neutral_parameters(support)
    result['sparse_cage'].update({
        'corner_columns': {'schema': CORNER_TOPOLOGY_SCHEMA},
        'insertion_policy': AXIS_STRIP_POLICY,
        'insertions': [{'corridor': corridor, 'fraction': fraction} for corridor, fraction in route]})
    return result


def rehash(reference):
    reference['sha256'] = fingerprint({key: value for key, value in reference.items() if key != 'sha256'})


class CornerAxisLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bases = {support: build_sparse_panel(parameters(support)) for support in (False, True)}

    def assert_coordinates_retained(self, before, after):
        old = semantic_mesh(before)['coordinates']; new = semantic_mesh(after)['coordinates']
        self.assertEqual(old, {sid: new[sid] for sid in old})

    def assert_structural_cycles(self, mesh):
        graph = mesh['sparse_graph']
        loops = graph['semantic_structural_loops']
        self.assertEqual([row['role'] for row in loops], ['structural.corner_column.west', 'structural.corner_column.east'])
        for row in loops:
            self.assertNotIn('axis_index', row)
            self.assertNotIn('cut_mm', row)
            self.assertEqual(row['expected_valence'], 4)
            self.assertTrue(row['proof']['opposite_edge_continuation'])
            self.assertEqual(row['proof']['native_extraction'], 'not_run')
            self.assertEqual(row['edge_ids'], [edge_id(a, b) for a, b in zip(row['vertex_ids'], row['vertex_ids'][1:]+row['vertex_ids'][:1])])
        self.assertEqual(validate_sparse_mesh(mesh)['status'], 'pass')

    def test_reference_freezes_actual_constructor_before_user_epoch(self):
        for support, base in self.bases.items():
            with self.subTest(support=support):
                graph = base['sparse_graph']; reference = graph['axis_strip_reference']
                self.assertEqual(graph['topology_epoch'], 0)
                self.assertEqual(graph.get('insertion_transactions', []), [])
                self.assertEqual(reference['schema_version'], CORNER_AXIS_REFERENCE_SCHEMA)
                self.assertEqual(reference['constructor_topology'], graph['constructor_topology'])
                self.assertEqual(reference['base_outer_port_counts'],
                                 {p['role']: len(p['vertex_ids']) for p in graph['ports'] if '.hole_' not in p['role']})
                self.assertEqual(set(reference['base_outer_port_counts'].values()), {36})
                self.assertEqual(reference['base_structural_loops'], graph['semantic_structural_loops'])
                self.assertEqual(reference['base_structural_loops_sha256'], fingerprint(graph['semantic_structural_loops']))
                self.assertEqual([len(row['vertex_ids']) for row in reference['base_structural_loops']], [44, 44])
                self.assertEqual(reference, make_axis_strip_reference(base, reference['minimum_spacing_mm']))
                self.assert_structural_cycles(base)

    def test_crossing_and_parallel_routes_propagate_actual_loops_and_all_ports(self):
        routes = ((('east', .5), ('south', .5)), (('south', .5), ('east', .5)),
                  (('east', .2), ('east', .8)), (('south', .2), ('south', .8)))
        for support, base in self.bases.items():
            original = deepcopy(base)
            for route in routes:
                mesh = base
                for epoch, (corridor, fraction) in enumerate(route, 1):
                    with self.subTest(support=support, route=route, epoch=epoch):
                        before = mesh; old_graph = before['sparse_graph']
                        plan = _axis_strip_plan(before, corridor, fraction)
                        mesh = _apply_axis_strip_insertion(before, plan); graph = mesh['sparse_graph']
                        self.assertEqual(graph['topology_epoch'], epoch)
                        self.assertEqual(len(graph['semantic_inserted_loops']), epoch)
                        self.assertEqual(graph['axis_strip_reference'], base['sparse_graph']['axis_strip_reference'])
                        for key in ('constructor_topology', 'structural_vertex_lineage', 'structural_face_lineage'):
                            self.assertEqual(graph[key], old_graph[key])
                        mids = graph['insertion_transaction']['split_edge_vertex_ids']
                        for old, current in zip(old_graph['semantic_structural_loops'], graph['semantic_structural_loops']):
                            expected = []
                            for a, b in zip(old['vertex_ids'], old['vertex_ids'][1:]+old['vertex_ids'][:1]):
                                expected.append(a)
                                if edge_id(a, b) in mids:
                                    expected.append(mids[edge_id(a, b)])
                            self.assertEqual(current['vertex_ids'], expected)
                        old_holes = {p['role']: p['vertex_ids'] for p in old_graph['ports'] if '.hole_' in p['role']}
                        self.assertEqual(old_holes, {p['role']: p['vertex_ids'] for p in graph['ports'] if '.hole_' in p['role']})
                        self.assertTrue(all(len(p['vertex_ids']) == 36+2*epoch for p in graph['ports'] if '.hole_' not in p['role']))
                        self.assert_coordinates_retained(before, mesh)
                        self.assert_structural_cycles(mesh)
                with self.assertRaises(RuntimeFailure):
                    _axis_strip_plan(mesh, 'east', .6)
            self.assertEqual(base, original)

    def test_crossing_south_expands_both_constructor_cycles_without_axis_claim(self):
        base = self.bases[False]
        first = _apply_axis_strip_insertion(base, _axis_strip_plan(base, 'east', .5))
        self.assertEqual([len(row['vertex_ids']) for row in first['sparse_graph']['semantic_structural_loops']], [44, 44])
        second = _apply_axis_strip_insertion(first, _axis_strip_plan(first, 'south', .5))
        self.assertEqual([len(row['vertex_ids']) for row in second['sparse_graph']['semantic_structural_loops']], [46, 46])

    def test_public_commit_is_exact_replay_and_keeps_constructor_distinct_from_axis_epochs(self):
        for support, base in self.bases.items():
            first = apply_strip_insertion(base, plan_strip_insertion(base, 'east', fraction=.5))
            second = apply_strip_insertion(first, plan_strip_insertion(first, 'south', fraction=.5))
            self.assertEqual(second, build_sparse_panel(parameters(support, (('east', .5), ('south', .5)))))
            self.assertEqual(validate_sparse_authored_identity(second)['status'], 'pass')
            self.assertEqual(second['sparse_graph']['insertion_transactions'][0], first['sparse_graph']['insertion_transaction'])
            self.assert_coordinates_retained(base, second)

    def test_reference_counts_version_and_full_loop_fields_reject_even_when_rehashed(self):
        base = self.bases[False]
        mutations = (
            lambda ref: ref['base_outer_port_counts'].update({next(iter(ref['base_outer_port_counts'])): 38}),
            lambda ref: ref['base_structural_loops'][0].update({'axis_index': 0}),
            lambda ref: ref['base_structural_loops'][0].update({'expected_valence': 3}),
            lambda ref: ref['base_structural_loops'][0].update({'vertex_indices': []}),
            lambda ref: ref['constructor_topology'].update({'base_outer_segments': 38}),
            lambda ref: ref.update({'schema_version': 'sparse-axis-strip-reference/1.0'}),
            lambda ref: ref['corridors']['east']['common_interval_mm'].__setitem__(0, ref['corridors']['east']['common_interval_mm'][0]+.01),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                changed = deepcopy(base); reference = changed['sparse_graph']['axis_strip_reference']
                mutate(reference)
                reference['base_structural_loops_sha256'] = fingerprint(reference['base_structural_loops'])
                rehash(reference)
                frozen = deepcopy(changed)
                with self.assertRaises(RuntimeFailure):
                    _axis_strip_plan(changed, 'east', .5)
                self.assertEqual(changed, frozen)

    def test_structural_metadata_or_missing_constructor_cannot_be_silently_dropped(self):
        base = self.bases[False]
        for key in ('constructor_topology', 'structural_vertex_lineage', 'structural_face_lineage', 'semantic_structural_loops'):
            changed = deepcopy(base); del changed['sparse_graph'][key]
            with self.subTest(field=key), self.assertRaises(RuntimeFailure):
                validate_sparse_mesh(changed)
        changed = deepcopy(base)
        changed['sparse_graph']['semantic_structural_loops'][0]['proof']['opposite_edge_continuation'] = False
        with self.assertRaises(RuntimeFailure):
            validate_sparse_mesh(changed)

    def test_structural_ancestry_fields_cannot_be_replaced_by_unbound_metadata(self):
        for domain, field, value in (('structural_vertex_lineage', 'fraction', .25),
                                     ('structural_vertex_lineage', 'placement', 'fitted'),
                                     ('structural_face_lineage', 'child_index', 2)):
            changed = deepcopy(self.bases[False])
            lineage = changed['sparse_graph'][domain]
            lineage[next(iter(lineage))][field] = value
            with self.subTest(domain=domain, field=field), self.assertRaises(RuntimeFailure):
                validate_sparse_mesh(changed)

    def test_crossed_corner_cycle_requires_the_actual_history_split_mapping(self):
        base = self.bases[False]
        first = _apply_axis_strip_insertion(base, _axis_strip_plan(base, 'south', .5))
        changed = deepcopy(first); graph = changed['sparse_graph']
        tx = graph['insertion_transactions'][0]
        corner_edges = set(base['sparse_graph']['semantic_structural_loops'][0]['edge_ids'])
        crossed = corner_edges & set(tx['split_edge_vertex_ids'])
        self.assertEqual(len(crossed), 2)
        del tx['split_edge_vertex_ids'][sorted(crossed)[0]]
        graph['insertion_transaction'] = deepcopy(tx)
        frozen = deepcopy(changed)
        with self.assertRaises(RuntimeFailure):
            _axis_strip_plan(changed, 'east', .5)
        self.assertEqual(changed, frozen)

    def test_legacy_reference_stays_version_one_without_constructor_fields(self):
        parameters_legacy = neutral_parameters()
        parameters_legacy['sparse_cage']['insertion_policy'] = AXIS_STRIP_POLICY
        base = build_sparse_panel(parameters_legacy)
        reference = base['sparse_graph']['axis_strip_reference']
        self.assertEqual(reference['schema_version'], 'sparse-axis-strip-reference/1.0')
        self.assertEqual(set(reference), {'schema_version', 'policy', 'minimum_spacing_mm', 'base_topology_epoch',
            'base_connectivity_sha256', 'base_vertex_ids', 'base_vertex_coordinates_mm', 'corridors', 'sha256'})
        plan = _axis_strip_plan(base, 'east', .5)
        self.assertEqual({(p['before'], p['after']) for p in plan['port_changes']}, {(32, 34)})


if __name__ == '__main__':
    unittest.main()
