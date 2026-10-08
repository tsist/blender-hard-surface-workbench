# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent, newly authored host fixtures. No Blender construction or old scripts."""
import builtins
import copy
import math
import unittest
from unittest.mock import patch
from collections import defaultdict

from hardsurface import structure_kernel as kernel
from hardsurface import structure_adapter as adapter
from hardsurface.structure_kernel import StructureError, validate_structure, verify_edit, verify_selection


def strip(count=1, with_port=True):
    """Open rectangular patch with arbitrary count, rather than the original annulus."""
    vertices = [[float(x), float(y), 0.0] for y in (0, 1) for x in range(count+1)]
    faces = [[i, i+1, i+count+2, i+count+1] for i in range(count)]
    order = list(range(count+1)) + list(range(2*count+1, count, -1))
    loop = {'id': 'rim', 'role': 'outer_boundary', 'vertex_ids': [f'v{i}' for i in order], 'closed': True,
            'normal': [0.0, 0.0, 1.0], 'anchor': {'vertex_id': 'v0', 'position_mm': vertices[0][:]}}
    structure = {'schema_version': '1.0', 'length_unit': 'mm',
                 'vertex_map': {f'v{i}': i for i in range(len(vertices))},
                 'face_map': {f'f{i}': i for i in range(len(faces))},
                 'loops': [loop],
                 'regions': [{'id': 'patch', 'role': 'planar_patch', 'feature_id': 'fixture',
                              'face_ids': [f'f{i}' for i in range(count)], 'boundary_loop_ids': ['rim']}],
                 'boundary_ports': [],
                 'tolerances': {'position_mm': 1e-8, 'unit_vector': 1e-8, 'area_mm2': 1e-12}}
    if with_port:
        structure['boundary_ports'].append({'id': 'port', 'loop_id': 'rim', 'region_id': 'patch',
            'point_order': loop['vertex_ids'][:],
            'frame': {'origin_mm': [0.,0.,0.], 'x_axis': [1.,0.,0.], 'y_axis': [0.,1.,0.], 'normal': [0.,0.,1.]},
            'allowed_transitions': ['identity']})
    return {'vertices': vertices, 'faces': faces}, structure


def tetrahedron():
    mesh = {'vertices': [[0.,0.,0.], [1.,0.,0.], [0.,1.,0.], [0.,0.,1.]],
            'faces': [[0,2,1], [0,1,3], [0,3,2], [1,2,3]]}
    structure = {'schema_version': '1.0', 'length_unit': 'mm',
        'vertex_map': {f'v{i}': i for i in range(4)}, 'face_map': {f'f{i}': i for i in range(4)},
        'loops': [], 'boundary_ports': [],
        'regions': [{'id': 'shell', 'role': 'closed_shell', 'feature_id': 'fixture',
                     'face_ids': [f'f{i}' for i in range(4)], 'boundary_loop_ids': []}],
        'tolerances': {'position_mm': 1e-8, 'unit_vector': 1e-8, 'area_mm2': 1e-12}}
    return mesh, structure


def entities(structure):
    return set(structure['vertex_map']) | set(structure['face_map']) | {
        r['id'] for group in ('loops', 'regions', 'boundary_ports') for r in structure[group]}


def edit_contract(mesh, structure, policy='preserve'):
    report = validate_structure(mesh, structure)
    return {'topology_policy': policy, 'before_topology_signature': report['topology_signature'],
            'before_geometry_signature': report['geometry_signature'],
            'before_attribute_signature': report['attribute_signature'],
            'before_structure_signature': report['structure_signature'],
            'affected_vertex_ids': [], 'affected_face_ids': [], 'affected_entity_ids': [],
            'lineage': [{'relation': 'continue', 'old': [key], 'new': [key]} for key in sorted(entities(structure))]}


def selection(mesh, structure, ids):
    report = validate_structure(mesh, structure)
    return {key: report[key] for key in ('geometry_signature', 'topology_signature', 'attribute_signature', 'structure_signature')} | {'entity_ids': ids}


class IndependentStructureReviewTests(unittest.TestCase):
    def test_rectangular_strip_and_closed_shell_are_supported(self):
        for mesh, structure in (strip(7), tetrahedron()):
            with self.subTest(faces=len(mesh['faces'])):
                report = validate_structure(mesh, structure)
                self.assertEqual(report['status'], 'pass')
                self.assertEqual(report['qualification'], 'not_run')

    def test_strip_reverse_face_orientation_is_rejected(self):
        mesh, structure = strip(2)
        mesh['faces'][1].reverse()
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'MESH_ORIENTATION')

    def test_closed_shell_omitted_face_is_not_certified_closed(self):
        mesh, structure = tetrahedron()
        mesh['faces'].pop()
        structure['face_map'].pop('f3')
        structure['regions'][0]['face_ids'].pop()
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'REGION_BOUNDARY_MISMATCH')

    def test_global_entity_collision_in_port_is_rejected(self):
        mesh, structure = strip()
        structure['boundary_ports'][0]['id'] = 'f0'
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'IDENTITY_MAP_INVALID')

    def test_port_unhashable_references_fail_as_structure_errors(self):
        for field in ('loop_id', 'region_id'):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    mesh, structure = strip()
                    structure['boundary_ports'][0][field] = value
                    with self.assertRaises(StructureError):
                        validate_structure(mesh, structure)

    def test_mixed_unknown_field_keys_fail_as_structure_errors(self):
        mesh, structure = strip()
        structure.update({0: True, 'unknown': True})
        with self.assertRaises(StructureError):
            validate_structure(mesh, structure)

    def test_invalid_unicode_id_fails_as_structure_error(self):
        mesh, structure = strip()
        structure['regions'][0]['role'] = '\ud800'
        with self.assertRaises(StructureError):
            validate_structure(mesh, structure)

    def test_semantic_kind_cannot_change_under_continuing_identity(self):
        mesh, structure = strip()
        contract = edit_contract(mesh, structure)
        contract['affected_entity_ids'] = ['rim', 'patch', 'port']
        after = copy.deepcopy(structure)
        after['loops'][0]['id'] = 'patch'
        after['regions'][0]['id'] = 'rim'
        after['regions'][0]['boundary_loop_ids'] = ['patch']
        after['boundary_ports'][0].update(loop_id='patch', region_id='rim')
        with self.assertRaises(StructureError):
            verify_edit(mesh, structure, mesh, after, contract)

    def test_selection_stale_after_same_ids_change_semantic_targets(self):
        mesh, structure = strip()
        bound = selection(mesh, structure, ['rim'])
        after = copy.deepcopy(structure)
        after['loops'][0]['id'] = 'patch'
        after['regions'][0]['id'] = 'rim'
        after['regions'][0]['boundary_loop_ids'] = ['patch']
        after['boundary_ports'][0].update(loop_id='patch', region_id='rim')
        with self.assertRaises(StructureError):
            verify_selection(mesh, after, bound)

    def test_actual_crease_change_cannot_pass_zero_scope_edit(self):
        mesh, structure = strip()
        contract = edit_contract(mesh, structure)
        after_mesh = copy.deepcopy(mesh)
        after_mesh['edge_creases'] = [[0, 1, 0.8]]
        with self.assertRaises(StructureError):
            verify_edit(mesh, structure, after_mesh, structure, contract)

    def test_actual_crease_change_changes_an_evidence_signature(self):
        mesh, structure = strip()
        before = validate_structure(mesh, structure)
        mesh['edge_creases'] = [[0, 1, 0.8]]
        after = validate_structure(mesh, structure)
        names = [key for key in before if key.endswith('_signature')]
        self.assertTrue(any(before[key] != after[key] for key in names), 'Actual crease edits disappeared from all evidence signatures')

    def test_split_merge_lineage_cannot_cross_entity_domains(self):
        mesh, structure = strip()
        contract = edit_contract(mesh, structure, policy='explicit_change')
        contract['lineage'] = [row for row in contract['lineage'] if row['old'][0] not in {'v0', 'v1', 'patch'}]
        contract['lineage'].extend([
            {'relation': 'split', 'old': ['v0'], 'new': ['v0', 'patch']},
            {'relation': 'merge', 'old': ['patch', 'v1'], 'new': ['v1']}])
        with self.assertRaises(StructureError):
            verify_edit(mesh, structure, mesh, structure, contract)

    def test_same_identity_cannot_be_deleted_and_recreated(self):
        mesh, structure = strip()
        contract = edit_contract(mesh, structure, policy='explicit_change')
        contract['lineage'] = [row for row in contract['lineage'] if row['old'] != ['v0']]
        contract['lineage'].extend([
            {'relation': 'delete', 'old': ['v0'], 'new': []},
            {'relation': 'create', 'old': [], 'new': ['v0']}])
        with self.assertRaises(StructureError):
            verify_edit(mesh, structure, mesh, structure, contract)

    def test_exact_integer_coordinates_do_not_alias_in_signature(self):
        mesh, structure = tetrahedron()
        # A valid triangle in the YZ plane keeps area/edge checks finite. Its
        # second integer X coordinate cannot be represented exactly as float.
        large = 2**53
        mesh['vertices'] = [[large,0,0], [large,1,0], [large,0,1]]
        mesh['faces'] = [[0,1,2]]
        structure['vertex_map'] = {'v0':0, 'v1':1, 'v2':2}
        structure['face_map'] = {'f0':0}
        structure['regions'][0]['face_ids'] = ['f0']
        structure['regions'][0]['boundary_loop_ids'] = ['triangle']
        structure['loops'] = [{'id':'triangle','role':'rim','vertex_ids':['v0','v1','v2'], 'closed':True,
            'normal':[1,0,0], 'anchor':{'vertex_id':'v0','position_mm':[large,0,0]}}]
        before = validate_structure(mesh, structure)
        mesh['vertices'][1][0] = large + 1
        try:
            after = validate_structure(mesh, structure)
        except StructureError:
            return  # Explicit unsupported precision rejection is acceptable.
        self.assertNotEqual(before['geometry_signature'], after['geometry_signature'])

    def test_rotated_translated_port_frame_matches_actual_geometry(self):
        mesh, structure = strip(3)
        sine, cosine = math.sin(0.7), math.cos(0.7)
        mesh['vertices'] = [[10+x*cosine, 20+y, 30-x*sine] for x, y, z in mesh['vertices']]
        structure['loops'][0]['normal'] = [sine, 0., cosine]
        structure['loops'][0]['anchor']['position_mm'] = mesh['vertices'][0][:]
        structure['boundary_ports'][0]['frame'] = {'origin_mm': [10.,20.,30.],
            'x_axis': [cosine,0.,-sine], 'y_axis': [0.,1.,0.], 'normal': [sine,0.,cosine]}
        self.assertEqual(validate_structure(mesh, structure)['status'], 'pass')
        structure['boundary_ports'][0]['frame']['normal'] = [0.,0.,1.]
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'FRAME_INVALID')

    def test_closed_region_cannot_merge_disconnected_shells(self):
        mesh, structure = tetrahedron()
        other_vertices = [[x+3, y, z] for x, y, z in mesh['vertices']]
        other_faces = [[v+4 for v in face] for face in mesh['faces']]
        mesh['vertices'].extend(other_vertices)
        mesh['faces'].extend(other_faces)
        structure['vertex_map'] = {f'v{i}': i for i in range(8)}
        structure['face_map'] = {f'f{i}': i for i in range(8)}
        structure['regions'][0]['face_ids'] = [f'f{i}' for i in range(8)]
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'REGION_DISCONNECTED')

    def test_nonfinite_loop_area_cannot_bypass_orientation_check(self):
        extent = 1e154
        mesh, structure = strip(with_port=False)
        mesh['vertices'] = [[-extent/2,-extent/2,extent/2], [-extent/2,extent/2,extent/2],
                            [extent/2,extent/2,-extent/2], [extent/2,-extent/2,-extent/2]]
        mesh['faces'] = [[0,2,1], [0,3,2]]
        structure['face_map'] = {'f0': 0, 'f1': 1}
        structure['regions'][0]['face_ids'] = ['f0', 'f1']
        structure['loops'][0].update(vertex_ids=['v0','v3','v2','v1'], normal=[-.8,0.,.6],
            anchor={'vertex_id':'v0', 'position_mm':mesh['vertices'][0][:]})
        with self.assertRaises(StructureError):
            validate_structure(mesh, structure)

    def test_current_selection_and_noop_edit_are_valid(self):
        mesh, structure = strip(2)
        self.assertEqual(verify_selection(mesh, structure, selection(mesh, structure, ['rim']))['status'], 'pass')
        self.assertEqual(verify_edit(mesh, structure, mesh, structure, edit_contract(mesh, structure))['status'], 'pass')

    def test_new_diagonal_cannot_change_undeclared_vertex_adjacency(self):
        mesh, structure = strip()
        contract = edit_contract(mesh, structure, policy='explicit_change')
        contract['affected_face_ids'] = ['f0']
        contract['affected_entity_ids'] = ['patch']
        for row in contract['lineage']:
            if row['old'] == ['f0']:
                row.update(relation='split', new=['f0', 'f1'])
        after_mesh, after_structure = copy.deepcopy((mesh, structure))
        after_mesh['faces'] = [[0,1,3], [0,3,2]]
        after_structure['face_map']['f1'] = 1
        after_structure['regions'][0]['face_ids'].append('f1')
        with self.assertRaises(StructureError) as cm:
            verify_edit(mesh, structure, after_mesh, after_structure, contract)
        self.assertEqual(cm.exception.code, 'NON_TARGET_CHANGED')
        contract['affected_vertex_ids'] = ['v0', 'v3']
        self.assertEqual(verify_edit(mesh, structure, after_mesh, after_structure, contract)['status'], 'pass')

    def test_repeated_semantic_membership_has_preflight_budget(self):
        mesh, structure = strip(100)
        loop = structure['loops'][0]
        structure['loops'] = [dict(loop, id=f'rim:{i}') for i in range(5000)]
        original_normal = kernel._normal
        with patch.object(kernel, '_normal', wraps=original_normal) as calls:
            with self.assertRaises(StructureError) as cm:
                validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'STRUCTURE_BUDGET_EXCEEDED')
        self.assertLessEqual(calls.call_count, len(mesh['faces']), 'Membership budget ran after expensive loop work')

    def test_closed_shells_sharing_one_vertex_reject_disconnected_fan(self):
        mesh, structure = tetrahedron()
        other_faces = [[0 if v == 0 else v+3 for v in face] for face in mesh['faces']]
        mesh['vertices'].extend([[-1.,0.,0.], [0.,-1.,0.], [0.,0.,-1.]])
        mesh['faces'].extend(other_faces)
        structure['vertex_map'] = {f'v{i}': i for i in range(7)}
        structure['face_map'] = {f'f{i}': i for i in range(8)}
        structure['regions'].append({'id':'other_shell', 'role':'closed_shell', 'feature_id':'fixture',
            'face_ids':[f'f{i}' for i in range(4,8)], 'boundary_loop_ids':[]})
        with self.assertRaises(StructureError) as cm:
            validate_structure(mesh, structure)
        self.assertEqual(cm.exception.code, 'MESH_NONMANIFOLD_VERTEX')

    def test_adapter_disconnected_component_work_is_linear(self):
        count = 120
        vertices = [[float(4*i+x),float(y),0.] for i in range(count) for x,y in ((0,0),(1,0),(0,1))]
        faces = [[3*i,3*i+1,3*i+2] for i in range(count)]
        authored = {'vertices_mm':vertices, 'faces':faces,
            'face_provenance':[{'feature_id':'fixture', 'surface_id':'island'} for _ in faces]}
        kwargs = {'vertex_map':{f'v{i:04d}':i for i in range(len(vertices))},
            'face_map':{f'f{i:04d}':i for i in range(len(faces))},
            'tolerances':{'position_mm':1e-8, 'unit_vector':1e-8, 'area_mm2':1e-12}}
        visits = {'edge_rows':0, 'minimum_items':0}
        class CountingDefaultDict(defaultdict):
            def values(self):
                for value in super().values():
                    visits['edge_rows'] += 1
                    yield value
        def counted_minimum(iterable, *args, **options):
            def counted():
                for value in iterable:
                    visits['minimum_items'] += 1
                    yield value
            return builtins.min(counted(), *args, **options)
        with patch.object(adapter, 'defaultdict', CountingDefaultDict), patch.object(adapter, 'min', counted_minimum, create=True):
            result = adapter.adapt_authored_mesh(authored, **kwargs)
        self.assertEqual(result['validation']['counts']['regions'], count)
        self.assertEqual(result['validation']['counts']['boundary_ports'], count)
        self.assertEqual(result['qualification'], 'not_run')
        self.assertLess(visits['edge_rows'], 20*count, 'Adapter rescans all group edges for each component')
        self.assertLess(visits['minimum_items'], 10*count, 'Adapter repeatedly scans remaining faces/anchors')

    def test_boundary_reconstruction_edge_work_is_linear(self):
        mesh, structure = strip(80)
        original_edge = kernel._edge
        with patch.object(kernel, '_edge', wraps=original_edge) as calls:
            validate_structure(mesh, structure)
        self.assertLess(calls.call_count, 100*len(mesh['faces']), 'Boundary reconstruction rescans every face per boundary edge')


if __name__ == '__main__':
    unittest.main()
