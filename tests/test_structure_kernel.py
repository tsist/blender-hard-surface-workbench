# SPDX-License-Identifier: GPL-3.0-or-later
"""New, host-authored fixtures only. No native Blender or legacy construction."""
import copy
import unittest
from hardsurface.structure_kernel import StructureError, validate_structure, verify_selection, verify_edit


def annulus():
    mesh = {'vertices': [[-2.,-2.,0.],[2.,-2.,0.],[2.,2.,0.],[-2.,2.,0.],[-1.,-1.,0.],[1.,-1.,0.],[1.,1.,0.],[-1.,1.,0.]], 'faces': [[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]}
    def loop(name, ids, normal):
        return {'id':name, 'role':'fixture_boundary', 'vertex_ids':ids, 'closed':True, 'normal':normal, 'anchor':{'vertex_id':ids[0], 'position_mm':mesh['vertices'][int(ids[0][1:])]}, 'crease':0., 'expected_valence':3}
    outer=loop('outer',['v0','v1','v2','v3'],[0.,0.,1.]); inner=loop('inner',['v4','v7','v6','v5'],[0.,0.,-1.])
    structure = {'schema_version':'1.0','length_unit':'mm','vertex_map':{f'v{i}':i for i in range(8)},'face_map':{f'f{i}':i for i in range(4)},'regions':[{'id':'annulus','role':'host_fixture','feature_id':'coupon','face_ids':[f'f{i}' for i in range(4)],'boundary_loop_ids':['outer','inner']}],'loops':[outer,inner],'boundary_ports':[{'id':'outer_port','loop_id':'outer','region_id':'annulus','point_order':outer['vertex_ids'][:],'frame':{'origin_mm':[0.,0.,0.],'x_axis':[1.,0.,0.],'y_axis':[0.,1.,0.],'normal':[0.,0.,1.]},'allowed_transitions':['identity']}], 'tolerances':{'position_mm':1e-8,'unit_vector':1e-8,'area_mm2':1e-12}}
    return mesh, structure


def contract(mesh, structure, vertices=(), faces=()):
    report=validate_structure(mesh,structure)
    ids=set(structure['vertex_map'])|set(structure['face_map'])|{r['id'] for group in ('regions','loops','boundary_ports') for r in structure[group]}
    return {'topology_policy':'preserve','before_topology_signature':report['topology_signature'],'before_geometry_signature':report['geometry_signature'],'before_attribute_signature':report['attribute_signature'],'before_structure_signature':report['structure_signature'],'affected_vertex_ids':list(vertices),'affected_face_ids':list(faces),'affected_entity_ids':[],'lineage':[{'relation':'continue','old':[x],'new':[x]} for x in sorted(ids)]}


class KernelTests(unittest.TestCase):
    def check_failure(self, code, mutate):
        mesh, structure=annulus(); mutate(mesh, structure)
        with self.assertRaises(StructureError) as cm: validate_structure(mesh,structure)
        self.assertEqual(cm.exception.code,code)
    def test_valid_arbitrary_four_point_annulus(self):
        mesh,structure=annulus();before=copy.deepcopy((mesh,structure));r=validate_structure(mesh,structure)
        self.assertEqual(r['counts']['quads'],4);self.assertEqual(r['qualification'],'not_run');self.assertEqual(before,(mesh,structure))
    def test_permuted_indices_same_signatures(self):
        mesh,s=annulus();before=validate_structure(mesh,s)
        permutation=[5,3,6,0,7,2,4,1]; lookup={old:new for new,old in enumerate(permutation)}
        mesh['vertices']=[mesh['vertices'][i] for i in permutation];mesh['faces']=[[lookup[v] for v in f] for f in reversed(mesh['faces'])]
        s['vertex_map']={k:lookup[v] for k,v in s['vertex_map'].items()};s['face_map']={k:3-v for k,v in s['face_map'].items()}
        after=validate_structure(mesh,s)
        for k in ('geometry_signature','topology_signature','structure_signature'):self.assertEqual(before[k],after[k])
    def test_forward_schema_rejected(self):self.check_failure('STRUCTURE_VERSION_UNSUPPORTED',lambda m,s:s.update(schema_version='2.0'))
    def test_unknown_structure_field(self):self.check_failure('STRUCTURE_INPUT',lambda m,s:s.update(approved=True))
    def test_unit_explicit(self):self.check_failure('STRUCTURE_INPUT',lambda m,s:s.update(length_unit='m'))
    def test_nonfinite(self):self.check_failure('STRUCTURE_INPUT',lambda m,s:m['vertices'][0].__setitem__(0,float('nan')))
    def test_bool_coordinate(self):self.check_failure('STRUCTURE_INPUT',lambda m,s:m['vertices'][0].__setitem__(0,False))
    def test_bad_face_index(self):self.check_failure('MESH_FACE_INVALID',lambda m,s:m['faces'][0].__setitem__(0,99))
    def test_ngon(self):self.check_failure('MESH_FACE_INVALID',lambda m,s:m['faces'][0].append(2))
    def test_duplicate_face(self):self.check_failure('MESH_DUPLICATE_FACE',lambda m,s:m['faces'].__setitem__(1,m['faces'][0][:]))
    def test_shared_edge_orientation(self):self.check_failure('MESH_ORIENTATION',lambda m,s:m['faces'][1].reverse())
    def test_degenerate_face(self):self.check_failure('MESH_DEGENERATE',lambda m,s:m['vertices'].__setitem__(5,m['vertices'][1][:]))
    def test_nonbijective_map(self):self.check_failure('IDENTITY_MAP_INVALID',lambda m,s:s['vertex_map'].__setitem__('v0',1))
    def test_missing_map_entry(self):self.check_failure('IDENTITY_MAP_INVALID',lambda m,s:s['vertex_map'].pop('v0'))
    def test_semantic_cross_kind_collision(self):self.check_failure('IDENTITY_MAP_INVALID',lambda m,s:s['regions'][0].update(id='v0'))
    def test_missing_edge(self):self.check_failure('LOOP_EDGE_MISSING',lambda m,s:s['loops'][0].update(vertex_ids=['v0','v2','v1','v3']))
    def test_reversed_loop_normal(self):self.check_failure('LOOP_ORDER_INVALID',lambda m,s:s['loops'][0].update(normal=[0,0,-1]))
    def test_anchor_false(self):self.check_failure('LOOP_ANCHOR_INVALID',lambda m,s:s['loops'][0]['anchor'].update(position_mm=[0,0,0]))
    def test_anchor_order(self):self.check_failure('LOOP_ANCHOR_INVALID',lambda m,s:s['loops'][0]['anchor'].update(vertex_id='v1'))
    def test_valence_false(self):self.check_failure('LOOP_VALENCE_MISMATCH',lambda m,s:s['loops'][0].update(expected_valence=4))
    def test_crease_false(self):self.check_failure('LOOP_CREASE_MISMATCH',lambda m,s:s['loops'][0].update(crease=1))
    def test_actual_crease_match(self):
        mesh,s=annulus();mesh['edge_creases']=[[0,1,.5],[1,2,.5],[2,3,.5],[3,0,.5]];s['loops'][0]['crease']=.5
        self.assertEqual(validate_structure(mesh,s)['status'],'pass')
    def test_region_hole_omitted(self):self.check_failure('REGION_BOUNDARY_MISMATCH',lambda m,s:s['regions'][0].update(boundary_loop_ids=['outer']))
    def test_region_actual_boundary_order(self):
        def mutate(m,s):
            s['loops'][1].update(vertex_ids=['v4','v5','v6','v7'],normal=[0,0,1])
        self.check_failure('REGION_BOUNDARY_ORDER',mutate)
    def test_port_bad_order(self):self.check_failure('PORT_ORDER_MISMATCH',lambda m,s:s['boundary_ports'][0]['point_order'].reverse())
    def test_port_left_handed(self):self.check_failure('FRAME_INVALID',lambda m,s:s['boundary_ports'][0]['frame'].update(y_axis=[0,-1,0]))
    def test_port_bad_origin(self):self.check_failure('PORT_NOT_PLANAR',lambda m,s:s['boundary_ports'][0]['frame'].update(origin_mm=[0,0,1]))
    def test_port_bad_transition(self):self.check_failure('PORT_TRANSITION_UNSUPPORTED',lambda m,s:s['boundary_ports'][0].update(allowed_transitions=['automatic_nearest']))
    def test_zero_tolerance_rejected(self):self.check_failure('STRUCTURE_INPUT',lambda m,s:s['tolerances'].update(position_mm=0))
    def test_absent_region_rejected(self):self.check_failure('REGION_COVERAGE_INVALID',lambda m,s:s.update(regions=[]))
    def test_selection_current(self):
        mesh,s=annulus();r=validate_structure(mesh,s)
        self.assertEqual(verify_selection(mesh,s,{k:r[k] for k in ('topology_signature','geometry_signature','attribute_signature','structure_signature')}|{'entity_ids':['outer']})['status'],'pass')
    def test_selection_stale_geometry(self):
        mesh,s=annulus();r=validate_structure(mesh,s);mesh['vertices'][1][0]=3
        with self.assertRaises(StructureError) as cm:verify_selection(mesh,s,{k:r[k] for k in ('topology_signature','geometry_signature','attribute_signature','structure_signature')}|{'entity_ids':['outer']})
        self.assertEqual(cm.exception.code,'SELECTION_STALE')
    def test_declared_local_edit(self):
        mesh,s=annulus();c=contract(mesh,s,['v1']);after=copy.deepcopy(mesh);after['vertices'][1][0]=3.
        self.assertEqual(verify_edit(mesh,s,after,s,c)['status'],'pass')
    def test_non_target_coordinate_edit_denied(self):
        mesh,s=annulus();c=contract(mesh,s);after=copy.deepcopy(mesh);after['vertices'][1][0]=3.
        with self.assertRaises(StructureError) as cm:verify_edit(mesh,s,after,s,c)
        self.assertEqual(cm.exception.code,'NON_TARGET_CHANGED')
    def test_lineage_not_total(self):
        mesh,s=annulus();c=contract(mesh,s);c['lineage'].pop()
        with self.assertRaises(StructureError) as cm:verify_edit(mesh,s,mesh,s,c)
        self.assertEqual(cm.exception.code,'IDENTITY_LINEAGE_INCOMPLETE')
    def test_lineage_alias_denied(self):
        mesh,s=annulus();c=contract(mesh,s);c['lineage'].append(c['lineage'][0])
        with self.assertRaises(StructureError) as cm:verify_edit(mesh,s,mesh,s,c)
        self.assertEqual(cm.exception.code,'IDENTITY_LINEAGE_INVALID')
    def test_stale_edit_binding(self):
        mesh,s=annulus();c=contract(mesh,s);c['before_geometry_signature']='0'*64
        with self.assertRaises(StructureError) as cm:verify_edit(mesh,s,mesh,s,c)
        self.assertEqual(cm.exception.code,'SELECTION_STALE')

if __name__=='__main__':unittest.main()
