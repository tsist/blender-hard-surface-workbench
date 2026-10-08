"""Neutral HOST-only sparse source authentication and lineage checks.

These fixtures use the actual constructor and float32 native protocol mocks.
Synthetic quad splitting tests transport only, not Blender, Catmull-Clark,
saved-file execution, measured shape, project geometry or visual qualification.
"""
import copy
import json
import unittest
from types import SimpleNamespace as NS

from hardsurface.io import RuntimeFailure
from hardsurface.sparse_panel_geometry import build_sparse_panel, BALANCED_FIXED_FRAME_SCHEMA
from hardsurface.structure_native import (FACE_SLOT, VERTEX_SLOT,
    bind_native_structure, write_authored_identity)
from hardsurface.subdivision_source import (SPARSE_SOURCE_SCHEMA, SURFACE_ATTRIBUTE,
    _sparse_bore_annulus, capture_semantic_source, evaluated_bore_domain)
from structure_workunit_fixtures import EXPECTED_SOURCE_MODIFIER
from test_sparse_axis_geometry import parameters, EAST, EAST_SOUTH
from test_structure_native_contract import Mesh, Object
from test_subdivision_source_binding import native_panel, refine_mock_quad_mesh


def native_sparse(support=False, route=EAST, *, deduplicate=True):
    """Use production quad_bridge table materialization; native checks unmocked."""
    p = parameters(support, BALANCED_FIXED_FRAME_SCHEMA, route)
    result = build_sparse_panel(p, feature_id='neutral_sparse')
    obj = Object(Mesh(result['vertices_mm'], result['faces']))
    obj.data.has_custom_normals = False
    obj.modifiers = [NS(**{**copy.deepcopy(EXPECTED_SOURCE_MODIFIER), **result['subdivision_modifier']})]
    obj['hs_quad_construction_sha256'] = result['construction_sha256']
    obj['hs_quad_constructor'] = 'quad.panel'; obj['hs_feature_id'] = 'neutral_sparse'
    obj['hs_subd_control_loops'] = json.dumps(result['control_loops'])
    obj['hs_subdivision_settings'] = json.dumps(result['subdivision_modifier'])
    labels = obj.data.attributes.new(SURFACE_ATTRIBUTE, 'INT', 'FACE')
    table = []; lookup = {}
    for item, row in zip(labels.data, result['face_provenance']):
        key = json.dumps(row, sort_keys=True, separators=(',', ':'))
        if not deduplicate or key not in lookup:
            lookup[key] = len(table); table.append(row)
        item.value = lookup[key]
    obj['hs_quad_surface_table'] = json.dumps(table, sort_keys=True, separators=(',', ':'))
    crease = obj.data.attributes.new('crease_edge', 'FLOAT', 'EDGE')
    values = {tuple(sorted((a, b))): weight for a, b, weight in result['edge_creases']}
    for edge, item in zip(obj.data.edges, crease.data): item.value = values.get(tuple(edge.vertices), 0.)
    write_authored_identity(obj, result, unit_scale=1., topology_epoch=result['sparse_graph']['topology_epoch'])
    obj['hs_object_id'] = '71111111-1111-4111-8111-111111111111'
    obj.data['hs_data_id'] = '72222222-2222-4222-8222-222222222222'
    binding = bind_native_structure(obj, source_binding={'fixture': 'neutral-host-only'}, unit_scale=1.)
    return obj, binding, result


def capture(obj, binding):
    return capture_semantic_source(obj, unit_scale=1., expected_binding=binding,
                                   require_bound=True, source_schema=SPARSE_SOURCE_SCHEMA)


def reorder(obj):
    """Carry all attributes through a real vertex/face/edge storage permutation."""
    mesh = obj.data; order = list(reversed(range(len(mesh.vertices))))
    lookup = {old: new for new, old in enumerate(order)}
    mesh.vertices = [mesh.vertices[i] for i in order]
    mesh.attributes[VERTEX_SLOT].data = [mesh.attributes[VERTEX_SLOT].data[i] for i in order]
    for i, vertex in enumerate(mesh.vertices): vertex.index = i
    for polygon in mesh.polygons: polygon.vertices = [lookup[i] for i in polygon.vertices]
    for edge in mesh.edges: edge.vertices = [lookup[i] for i in edge.vertices]
    mesh.polygons.reverse()
    for name in (FACE_SLOT, SURFACE_ATTRIBUTE): mesh.attributes[name].data.reverse()
    for i, polygon in enumerate(mesh.polygons): polygon.index = i
    mesh.edges.reverse(); mesh.attributes['crease_edge'].data.reverse()
    for i, edge in enumerate(mesh.edges): edge.index = i


class SparseSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.binding, cls.result = native_sparse()
        cls.source = capture(cls.a, cls.binding)
        cls.refined = refine_mock_quad_mesh(cls.a.data)

    def assert_rejected(self, fn, code=None):
        with self.assertRaises(RuntimeFailure) as caught: fn()
        if code is not None: self.assertEqual(caught.exception.code, code)

    def test_complete_actual_a_source_and_typed_annulus(self):
        source = self.source; evidence = source['evidence']
        self.assertEqual(source['source_face_count'], 666)
        self.assertEqual(source['surface_table_count'], 20)
        self.assertEqual(len(source['bore_labels']), 1)
        self.assertNotIn('bore_label', source)
        self.assertEqual(evidence['native_binding']['topology_epoch'], 1)
        self.assertTrue(evidence['full_per_face_provenance_bound'])
        self.assertEqual(evidence['source_bore_annulus']['boundary_cycle_lengths'], [16, 16])
        self.assertEqual(evidence['source_bore_annulus']['euler_characteristic'], 0)
        self.assertEqual(len(source['parent_face_ids_by_slot']), 666)
        table = json.loads(self.a['hs_quad_surface_table'])
        for parent, label in enumerate(source['parent_surface_by_slot']):
            self.assertEqual(source['parent_provenance_by_slot'][parent], table[label])
        self.assertFalse(evidence['surface_labels_are_face_identity'])

    def test_supported_b_source_and_outer_ports_are_not_hardcoded(self):
        obj, binding, _ = native_sparse(True)
        source = capture(obj, binding)
        self.assertEqual(source['source_face_count'], 698)
        self.assertEqual(source['surface_table_count'], 22)
        self.assertEqual(source['evidence']['source_bore_face_count'], 16)
        self.assertTrue(all(len(row['vertex_indices']) == 34 for row in source['control_loops']
                            if row['role'].endswith('outer_flat_boundary')))

    def test_zero_and_two_insertions_use_authenticated_count_and_epoch(self):
        for route, faces in (((), 622), (EAST_SOUTH, 716)):
            with self.subTest(route=route):
                obj, binding, _ = native_sparse(route=route)
                source = capture(obj, binding)
                self.assertEqual(source['source_face_count'], faces)
                self.assertEqual(source['evidence']['native_binding']['topology_epoch'], len(route))
                self.assertEqual(source['evidence']['source_bore_face_count'], 16)

    def test_sparse_requires_explicit_schema_and_external_registry(self):
        self.assert_rejected(lambda: capture_semantic_source(self.a, unit_scale=1., expected_binding=self.binding,
                                                             require_bound=True))
        self.assert_rejected(lambda: capture_semantic_source(self.a, unit_scale=1., source_schema=SPARSE_SOURCE_SCHEMA),
                             'SUBDIVISION_SOURCE_BINDING')
        self.assert_rejected(lambda: capture_semantic_source(self.a, unit_scale=1., expected_binding=self.binding,
                                                             source_schema='sparse_fuzzy'), 'SUBDIVISION_SEMANTIC_SOURCE')
        wrong = copy.deepcopy(self.binding); wrong['topology_epoch'] += 1
        self.assert_rejected(lambda: capture(self.a, wrong), 'STRUCTURE_REGISTRY_DRIFT')

    def test_legacy_source_does_not_enter_sparse_profile(self):
        obj, binding = native_panel()
        self.assert_rejected(lambda: capture(obj, binding), 'SUBDIVISION_SEMANTIC_SOURCE')
        source = capture_semantic_source(obj, unit_scale=1., expected_binding=binding, require_bound=True)
        self.assertEqual(source['evidence']['source_bore_face_count'], 24)
        self.assertNotIn('source_schema', source)

    def test_all_native_source_domains_still_authenticated(self):
        for mutation in ('geometry', 'crease', 'modifier', 'registry_id'):
            obj = copy.deepcopy(self.a)
            if mutation == 'geometry': obj.data.vertices[0].co[0] += 1e-8
            elif mutation == 'crease': obj.data.attributes['crease_edge'].data[0].value = .5
            elif mutation == 'modifier': obj.modifiers[0].quality = 3
            else: obj['hs_object_id'] = 'foreign'
            with self.subTest(mutation=mutation): self.assert_rejected(lambda: capture(obj, self.binding))

    def test_wrong_role_missing_wall_cap_and_malformed_table_reject(self):
        wall = self.source['bore_labels'][0]
        cap = next(i for i, row in enumerate(json.loads(self.a['hs_quad_surface_table'])) if row['region_group'] == 'top')
        for mutation in ('wrong_role', 'missing_wall', 'cap_is_wall', 'malformed_row', 'foreign_feature', 'nonbool_flag'):
            obj = copy.deepcopy(self.a); table = json.loads(obj['hs_quad_surface_table'])
            if mutation == 'wrong_role': table[wall]['surface_role'] = 'bore_wall'
            elif mutation == 'missing_wall': table[wall] = copy.deepcopy(table[cap])
            elif mutation == 'cap_is_wall': table[cap] = copy.deepcopy(table[wall])
            elif mutation == 'malformed_row': table[wall] = ['holewall/bore_wall']
            elif mutation == 'foreign_feature': table[wall]['feature_id'] = 'foreign'
            else: table[wall]['smooth'] = 1
            obj['hs_quad_surface_table'] = json.dumps(table)
            with self.subTest(mutation=mutation): self.assert_rejected(lambda: capture(obj, self.binding))

    def test_forged_unknown_and_wrong_surface_labels_reject(self):
        for label in (len(self.a.data.polygons), self.source['bore_labels'][0], True):
            obj = copy.deepcopy(self.a); obj.data.attributes[SURFACE_ATTRIBUTE].data[0].value = label
            with self.subTest(label=label): self.assert_rejected(lambda: capture(obj, self.binding))

    def test_full_table_and_equivalent_row_aliases_do_not_conflate_face_identity(self):
        obj, binding, result = native_sparse(deduplicate=False)
        before = capture(obj, binding); a, b = before['bore_labels'][:2]
        labels = obj.data.attributes[SURFACE_ATTRIBUTE].data
        self.assertEqual(result['face_provenance'][a], result['face_provenance'][b])
        labels[a].value, labels[b].value = labels[b].value, labels[a].value
        after = capture(obj, binding)
        self.assertEqual(after['surface_table_count'], 666)
        self.assertEqual(len(after['bore_labels']), 16)
        self.assertEqual(before['parent_face_ids_by_slot'], after['parent_face_ids_by_slot'])
        self.assertEqual(before['parent_provenance_by_slot'], after['parent_provenance_by_slot'])
        self.assertNotEqual(before['parent_surface_by_slot'], after['parent_surface_by_slot'])
        self.assertEqual(len(evaluated_bore_domain(obj.data, after, level=0)['bore_face_indices']), 16)

    def test_parent_swap_and_combined_label_parent_swap_reject(self):
        for carry_label in (False, True):
            obj = copy.deepcopy(self.a)
            names = (FACE_SLOT, SURFACE_ATTRIBUTE) if carry_label else (FACE_SLOT,)
            for name in names:
                rows = obj.data.attributes[name].data
                rows[0].value, rows[80].value = rows[80].value, rows[0].value
            with self.subTest(carry_label=carry_label): self.assert_rejected(lambda: capture(obj, self.binding))

    def test_reordered_source_preserves_authorship_and_exact_label_binding(self):
        obj = copy.deepcopy(self.a); reorder(obj)
        source = capture(obj, self.binding)
        for key in ('parent_surface_by_slot', 'parent_face_ids_by_slot', 'parent_provenance_by_slot', 'bore_labels', 'bore_parent_slots'):
            self.assertEqual(source[key], self.source[key])
        self.assertEqual(source['evidence']['source_bore_annulus'], self.source['evidence']['source_bore_annulus'])
        self.assertEqual(source['authored_control_loops'], self.source['authored_control_loops'])
        self.assertNotEqual(source['control_loops'], self.source['control_loops'])
        self.assertEqual(len(evaluated_bore_domain(obj.data, source, level=0)['bore_face_indices']), 16)

    def test_actual_region_topology_rejects_missing_wall_and_cap_addition(self):
        members = {i for i, row in enumerate(self.a.data.attributes[SURFACE_ATTRIBUTE].data)
                   if row.value in self.source['bore_labels']}
        author = self.source['authorship']
        cycles = self.source['evidence']['source_bore_annulus']['boundary_vertex_cycles']
        for corrupted in (members-{min(members)}, members|{0}):
            self.assert_rejected(lambda: _sparse_bore_annulus(self.a.data, corrupted,
                vertex_map=author['vertex_map'], declared_cycles=cycles), 'SUBDIVISION_SEMANTIC_ANNULUS')
        wrong = copy.deepcopy(cycles); wrong[0].reverse()
        self.assert_rejected(lambda: _sparse_bore_annulus(self.a.data, members,
            vertex_map=author['vertex_map'], declared_cycles=wrong), 'SUBDIVISION_SEMANTIC_ANNULUS')

    def test_every_sparse_label_transports_through_l0_l1_l2_l3(self):
        mesh = self.a.data
        for level in range(4):
            if level: mesh = refine_mock_quad_mesh(mesh)
            with self.subTest(level=level):
                domain = evaluated_bore_domain(mesh, self.source, level=level)
                self.assertEqual(len(domain['bore_face_indices']), 16 * 4**level)
                self.assertEqual(set(domain['surface_labels'][i] for i in domain['bore_face_indices']), set(self.source['bore_labels']))
                self.assertEqual(set(domain['parent_slots'][i] for i in domain['bore_face_indices']), set(self.source['bore_parent_slots']))
                self.assertEqual(domain['evidence']['parent_patch_topology']['connected_disk_regions'], 666)

    def test_reordered_evaluation_preserves_all_labels(self):
        mesh = copy.deepcopy(self.refined); mesh.polygons.reverse()
        for name in (FACE_SLOT, SURFACE_ATTRIBUTE): mesh.attributes[name].data.reverse()
        domain = evaluated_bore_domain(mesh, self.source, level=1)
        self.assertEqual(len(domain['bore_face_indices']), 64)

    def test_missing_duplicate_unknown_parent_and_wrong_surface_reject(self):
        for attribute, value in ((FACE_SLOT, 666), (FACE_SLOT, self.refined.attributes[FACE_SLOT].data[4].value),
                                 (SURFACE_ATTRIBUTE, self.source['bore_labels'][0]), (SURFACE_ATTRIBUTE, True)):
            mesh = copy.deepcopy(self.refined); mesh.attributes[attribute].data[0].value = value
            with self.subTest(attribute=attribute, value=value):
                self.assert_rejected(lambda: evaluated_bore_domain(mesh, self.source, level=1))

    def test_balanced_descendant_and_whole_parent_swaps_reject_topology(self):
        for whole_parent in (False, True):
            mesh = copy.deepcopy(self.refined)
            parents, labels = mesh.attributes[FACE_SLOT].data, mesh.attributes[SURFACE_ATTRIBUTE].data
            if whole_parent:
                a, b = parents[0].value, parents[80].value
                for parent, label in zip(parents, labels):
                    if parent.value == a: parent.value = b; label.value = self.source['parent_surface_by_slot'][b]
                    elif parent.value == b: parent.value = a; label.value = self.source['parent_surface_by_slot'][a]
            else:
                for rows in (parents, labels): rows[0].value, rows[80].value = rows[80].value, rows[0].value
            with self.subTest(whole_parent=whole_parent):
                self.assert_rejected(lambda: evaluated_bore_domain(mesh, self.source, level=1), 'SUBDIVISION_SEMANTIC_PATCH')

    def test_evaluated_counts_and_attribute_domains_remain_fail_closed(self):
        for mutation in ('face_count', 'attribute_missing', 'attribute_domain', 'parent_short', 'winding'):
            mesh = copy.deepcopy(self.refined)
            if mutation == 'face_count': mesh.polygons.pop()
            elif mutation == 'attribute_missing': del mesh.attributes[SURFACE_ATTRIBUTE]
            elif mutation == 'attribute_domain': mesh.attributes[SURFACE_ATTRIBUTE].domain = 'POINT'
            elif mutation == 'parent_short': mesh.attributes[FACE_SLOT].data.pop()
            else: mesh.polygons[0].vertices.reverse()
            with self.subTest(mutation=mutation):
                self.assert_rejected(lambda: evaluated_bore_domain(mesh, self.source, level=1))


if __name__ == '__main__': unittest.main()
