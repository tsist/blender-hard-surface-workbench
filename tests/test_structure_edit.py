# SPDX-License-Identifier: GPL-3.0-or-later
"""New host-only edit fixtures. Native report envelopes below are explicit mocks.

No bpy, saved Blender file, native worker, or qualification is run by this suite.
"""
import copy
import json
import struct
import unittest
from unittest.mock import patch

from hardsurface.quad_geometry import build
from hardsurface.structure_adapter import adapt_authored_mesh
from hardsurface import structure_edit as edit
from hardsurface import structure_kernel as kernel
from hardsurface import subd_panel_identity as author

BASE = {'op':'quad.panel','id':'panel','topology_strategy':'subd_control_cage',
        'size':[96,62],'corner_radius':9,'edge_bevel':.7,'center':[0,0],
        'z_min':0,'z_max':5.5,
        'holes':[{'id':'bore','kind':'circle','center':[11,1],'radius':8.5}],
        'local_patch_bounds':[-7,-15,25,17],'chord_tolerance':.05,
        'subdivision_cage':{'method':'CATMULL_CLARK','hole_segments':24,'preview_levels':2}}
TOLERANCES={'position_mm':1e-4,'unit_vector':1e-6,'area_mm2':1e-10}


def authored(parameters=None, native_precision=False):
    mesh=build(copy.deepcopy(BASE if parameters is None else parameters),'fixture_panel')
    if native_precision:
        mesh['vertices_mm']=[[struct.unpack('!f',struct.pack('!f',x*.001))[0]*1000 for x in v] for v in mesh['vertices_mm']]
    return mesh


def report(mesh):
    """Mock native envelope, while arrays and all kernel checks are real host data."""
    a=mesh['authored_structure']
    adapted=adapt_authored_mesh(mesh,vertex_map=a['vertex_map'],face_map=a['face_map'],tolerances=TOLERANCES)
    checked=adapted['validation']
    binding={'schema_version':'1.0','object_id':'fixture-object','data_id':'fixture-data',
             'mesh_state':'control','schedule_revision':a['schedule_revision'],'topology_epoch':0,
             'context_sha256':kernel.fingerprint({'matrix':'identity','modifier':'fixed'}),
             'authorship_sha256':a['authorship_sha256'],
             'construction_sha256':mesh['construction_sha256'],
             'source_binding_sha256':kernel.fingerprint({'source':'host fixture only'}),
             **{key:checked[key] for key in edit.SIGNATURES}}
    return {'status':'pass','native_extraction':{'status':'pass','source':'raw object.data',
                'coordinate_space':'object_local_mm','actual_edges_verified':True,
                'identity_transport':'validated_POINT_FACE_INT_slots',
                'crease_source':'actual_EDGE_FLOAT_endpoint_values','signature_quantization':'none'},
            'kernel_report':checked,'witness':{'mesh':adapted['mesh'],'structure':adapted['structure']},
            'authorship':copy.deepcopy(a),'binding':binding,'losses':adapted['losses']}


def reseal_authorship(mesh):
    a=mesh['authored_structure']
    a['semantic_connectivity_sha256']=author.fingerprint(author.semantic_connectivity(mesh,a))
    a['semantic_crease_sha256']=author.fingerprint(author.semantic_creases(mesh,a))
    a['authorship_sha256']=author.fingerprint(author.authorship_payload(a))


class AuthoredEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before_mesh=authored()
        cls.before=report(cls.before_mesh)

    def plan(self, parameters=None, datum='not_requested'):
        return edit.plan_authored_edit(self.before['witness'],self.before['authorship'],
                                       copy.deepcopy(BASE if parameters is None else parameters),datum_policy=datum)

    def check_edit(self, parameters, datum='not_requested', native_precision=False):
        before=report(authored(native_precision=native_precision)) if native_precision else self.before
        # The contract must exist before the after constructor is called.
        plan=edit.plan_authored_edit(before['witness'],before['authorship'],parameters,datum_policy=datum)
        after=report(authored(parameters,native_precision=native_precision))
        proof=edit.verify_authored_edit(before,after,plan)
        self.assertEqual(proof['status'],'pass')
        self.assertEqual(proof['dependent_selections'],'invalidate_and_re_resolve')
        self.assertEqual(proof['source_preservation'],'not_checked')
        self.assertEqual(proof['saved_reopen'],'not_checked')
        return plan,proof

    def test_noop_is_valid_without_datums_or_geometry_write_set(self):
        original=copy.deepcopy(self.before)
        plan=self.plan()
        self.assertEqual(plan['parameter_changes'],[])
        self.assertEqual(plan['kernel_contract']['affected_vertex_ids'],[])
        self.assertEqual(edit.verify_authored_edit(self.before,self.before,plan)['status'],'pass')
        self.assertEqual(self.before,original)

    def test_plan_never_constructs_new_mesh_or_uses_after_witness(self):
        p=copy.deepcopy(BASE);p['holes'][0]['center']=[10,2]
        with patch('hardsurface.quad_panel_subd.build_subd_panel',side_effect=AssertionError('Must not build before contract')):
            plan=self.plan(p)
        self.assertEqual(len(plan['allowed_vertex_axes']),160)
        self.assertEqual(plan['dependency_resolution'],'authored_schedule_and_before_witness_only')
        self.assertEqual(plan['parameter_changes'][0]['parameter'],'holes[0].center')
        self.assertEqual(plan['parameter_changes'][0]['after'],[10,2])

    def test_x_y_radius_and_combined_hole_changes(self):
        for center,radius in (([10,1],8.5),([11,2],8.5),([11,1],8),([10,2],8)):
            with self.subTest(center=center,radius=radius):
                p=copy.deepcopy(BASE);p['holes'][0].update(center=center,radius=radius)
                plan,_=self.check_edit(p)
                self.assertEqual(len(plan['allowed_vertex_axes']),160)
                self.assertTrue(all(axes==[0,1] for axes in plan['allowed_vertex_axes'].values()))

    def test_fixed_bottom_increase_and_decrease(self):
        for z in (4.5,6.5):
            with self.subTest(z=z):
                p=copy.deepcopy(BASE);p['z_max']=z;p['edit_datum']='fixed_bottom'
                plan,_=self.check_edit(p,'fixed_bottom')
                self.assertEqual(len(plan['allowed_vertex_axes']),496)
                self.assertTrue(all(axes==[2] for axes in plan['allowed_vertex_axes'].values()))
                self.assertTrue(all(not key.startswith('plane/bottom/') for key in plan['allowed_vertex_axes']))

    def test_fixed_midplane_requires_exact_midpoint_and_preserves_xy(self):
        for low,high in ((-.5,6),(.5,5)):
            p=copy.deepcopy(BASE);p.update(z_min=low,z_max=high,edit_datum='fixed_midplane')
            plan,_=self.check_edit(p,'fixed_midplane')
            self.assertEqual(len(plan['allowed_vertex_axes']),992)
            self.assertTrue(all(axes==[2] for axes in plan['allowed_vertex_axes'].values()))
        p=copy.deepcopy(BASE);p['z_max']=6
        with self.assertRaises(kernel.StructureError):self.plan(p,'fixed_midplane')

    def test_combined_hole_thickness_uses_union_with_axis_protection(self):
        p=copy.deepcopy(BASE);p['holes'][0].update(center=[10,2],radius=8);p['z_max']=6.5
        plan,_=self.check_edit(p,'fixed_bottom')
        self.assertEqual(len(plan['allowed_vertex_axes']),576)
        self.assertEqual(plan['allowed_vertex_axes']['plane/top/bore/rim/slot:0'],[0,1,2])
        self.assertEqual(plan['allowed_vertex_axes']['plane/bottom/bore/rim/slot:0'],[0,1])

    def test_native_float32_coordinates_are_not_host_double_signatures(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8;p['z_max']=6.5
        plan,proof=self.check_edit(p,'fixed_bottom',native_precision=True)
        self.assertNotEqual(proof['before']['geometry_signature'],self.before['kernel_report']['geometry_signature'])
        self.assertEqual(proof['parameter_coordinates'],'pass')

    def test_missing_datum_and_fixed_bottom_zmin_change_fail(self):
        p=copy.deepcopy(BASE);p['z_max']=6.5
        with self.assertRaises(kernel.StructureError):self.plan(p)
        with self.assertRaises(kernel.StructureError):self.plan(p,None)
        p['z_min']=-1
        with self.assertRaises(kernel.StructureError):self.plan(p,'fixed_bottom')

    def test_edit_datum_is_administrative_but_must_match_explicit_policy(self):
        p=copy.deepcopy(BASE);p['edit_datum']='fixed_bottom'
        plan,_=self.check_edit(p,'fixed_bottom')
        self.assertEqual(plan['parameter_changes'],[])
        self.assertEqual(plan['expected_parameter_binding']['parameters']['edit_datum'],'fixed_bottom')
        with self.assertRaises(kernel.StructureError):self.plan(p,'not_requested')

    def test_unsupported_parameters_and_topologies_fail(self):
        mutations=[lambda p:p['size'].__setitem__(0,100),lambda p:p.update(edge_bevel=.8),
            lambda p:p.update(corner_radius=10),lambda p:p.update(center=[1,0]),
            lambda p:p.update(local_patch_bounds=[-8,-15,25,17]),lambda p:p['holes'][0].update(id='other'),
            lambda p:p['holes'][0].update(kind='slot'),lambda p:p['holes'].append(copy.deepcopy(p['holes'][0])),
            lambda p:p['subdivision_cage'].update(hole_segments=48),
            lambda p:p['subdivision_cage'].update(method='SIMPLE'),
            lambda p:p['subdivision_cage'].update(preview_levels=3),lambda p:p.update(unknown_option=1)]
        for mutate in mutations:
            p=copy.deepcopy(BASE);mutate(p)
            with self.subTest(p=p),self.assertRaises(kernel.StructureError):self.plan(p)

    def test_nonfinite_and_unsupported_parameter_domain_fail(self):
        for radius in (-1,0,20,float('nan'),float('inf'),True):
            p=copy.deepcopy(BASE);p['holes'][0]['radius']=radius
            with self.subTest(radius=radius),self.assertRaises(kernel.StructureError):self.plan(p)

    def test_contract_scope_and_lineage_cannot_be_widened_or_omitted(self):
        plan=self.plan()
        for mutate in (lambda p:p['kernel_contract']['affected_vertex_ids'].append('plane/top/grid/x:0/y:0'),
                       lambda p:p['kernel_contract']['lineage'].pop(),
                       lambda p:p['typed_continuations'].pop(),
                       lambda p:p.update(plan_sha256='0'*64)):
            altered=copy.deepcopy(plan);mutate(altered)
            with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,self.before,altered)

    def test_stale_before_and_declared_after_binding_fail(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        plan=self.plan(p);after=report(authored(p))
        altered=copy.deepcopy(self.before);altered['kernel_report']['geometry_signature']='0'*64
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(altered,after,plan)
        wrong=copy.deepcopy(BASE);wrong['holes'][0]['radius']=7.5
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,report(authored(wrong)),plan)

    def test_raw_actual_witness_is_recomputed_despite_passing_report(self):
        after=copy.deepcopy(self.before)
        index=after['witness']['structure']['vertex_map']['plane/top/grid/x:0/y:0']
        after['witness']['mesh']['vertices'][index]=list(after['witness']['mesh']['vertices'][index])
        after['witness']['mesh']['vertices'][index][0]+=.01
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,after,self.plan())

    def test_protected_outside_patch_and_coordinate_axes_fail(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        plan=self.plan(p)
        for key,axis in (('plane/top/grid/x:0/y:0',0),('plane/top/bore/support/slot:0',2)):
            mesh=authored(p);index=mesh['authored_structure']['vertex_map'][key]
            mesh['vertices_mm'][index]=list(mesh['vertices_mm'][index]);mesh['vertices_mm'][index][axis]+=.01
            with self.subTest(key=key,axis=axis),self.assertRaises(kernel.StructureError):
                edit.verify_authored_edit(self.before,report(mesh),plan)

    def test_wrong_in_scope_coordinate_rejects_even_with_correct_declared_parameters(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        mesh=authored(p);key='plane/top/bore/support/slot:0';index=mesh['authored_structure']['vertex_map'][key]
        mesh['vertices_mm'][index]=list(mesh['vertices_mm'][index]);mesh['vertices_mm'][index][0]+=.001
        with self.assertRaises(kernel.StructureError) as cm:edit.verify_authored_edit(self.before,report(mesh),self.plan(p))
        self.assertEqual(cm.exception.code,'AUTHORED_PARAMETER_GEOMETRY_MISMATCH')

    def test_semantic_meaning_is_protected_inside_affected_closure(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        after=report(authored(p));plan=self.plan(p)
        target=next(row for row in after['witness']['structure']['regions'] if row['id'] in plan['kernel_contract']['affected_entity_ids'])
        target['role']='repurposed_region'
        checked=kernel.validate_structure(**after['witness'])
        after['kernel_report']=checked;after['binding'].update({k:checked[k] for k in edit.SIGNATURES})
        with self.assertRaises(kernel.StructureError) as cm:edit.verify_authored_edit(self.before,after,plan)
        self.assertEqual(cm.exception.code,'AUTHORED_EDIT_SEMANTICS_CHANGED')

    def test_native_context_identity_epoch_and_extraction_must_remain_bound(self):
        for field,value in (('object_id','different'),('data_id','different'),('context_sha256','different'),
                            ('topology_epoch',1),('mesh_state','evaluated')):
            after=copy.deepcopy(self.before);after['binding'][field]=value
            with self.subTest(field=field),self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,after,self.plan())
        after=copy.deepcopy(self.before);after['native_extraction']['status']='not_run'
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,after,self.plan())

    def test_index_and_polygon_storage_permutation_preserves_proof(self):
        mesh=copy.deepcopy(self.before_mesh);a=mesh['authored_structure'];n=len(mesh['vertices_mm']);f=len(mesh['faces'])
        mesh['vertices_mm'].reverse();mesh['faces']=[[n-1-v for v in face] for face in reversed(mesh['faces'])]
        mesh['face_provenance'].reverse()
        mesh['edge_creases']=[[n-1-x,n-1-y,value] for x,y,value in mesh['edge_creases']]
        a['vertex_map']={key:n-1-index for key,index in a['vertex_map'].items()}
        a['face_map']={key:f-1-index for key,index in a['face_map'].items()}
        after=report(mesh)
        self.assertEqual(edit.verify_authored_edit(self.before,after,self.plan())['status'],'pass')

    def test_alias_and_dependency_manifest_cannot_expand_scope(self):
        for field in ('alias_manifest','edit_dependencies'):
            before=copy.deepcopy(self.before)
            if field=='alias_manifest':before['authorship'][field].pop(next(iter(before['authorship'][field])))
            else:before['authorship'][field]['holes[0].radius']['vertex_ids'].append('plane/top/grid/x:0/y:0')
            before['authorship']['authorship_sha256']=author.fingerprint(author.authorship_payload(before['authorship']))
            with self.subTest(field=field),self.assertRaises(kernel.StructureError):
                edit.plan_authored_edit(before['witness'],before['authorship'],BASE,datum_policy='not_requested')

    def test_loop_and_crease_witnesses_cannot_change(self):
        mesh=copy.deepcopy(self.before_mesh);mesh['edge_creases'][0][2]=.5
        reseal_authorship(mesh)
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,report(mesh),self.plan())


    def test_support_width_branch_change_keeps_identity_and_declared_scope(self):
        initial=copy.deepcopy(BASE);initial['edge_bevel']=.5;initial['holes'][0]['radius']=8
        requested=copy.deepcopy(initial);requested['holes'][0]['radius']=8.5
        before=report(authored(initial))
        plan=edit.plan_authored_edit(before['witness'],before['authorship'],requested,datum_policy='not_requested')
        after=report(authored(requested))
        self.assertEqual(edit.verify_authored_edit(before,after,plan)['status'],'pass')
        self.assertEqual(set(before['authorship']['vertex_map']),set(after['authorship']['vertex_map']))

    def test_each_parameter_has_predeclared_vertex_face_and_entity_dependencies(self):
        p=copy.deepcopy(BASE);p['holes'][0].update(center=[10,2],radius=8);p['z_max']=6.5
        plan=self.plan(p,'fixed_bottom')
        self.assertEqual([row['parameter'] for row in plan['parameter_dependencies']],
                         ['holes[0].center[0]','holes[0].center[1]','holes[0].radius','z_range.fixed_bottom'])
        for row in plan['parameter_dependencies']:
            self.assertTrue(row['vertex_ids']);self.assertTrue(row['face_ids']);self.assertTrue(row['entity_ids'])
            self.assertTrue(set(row['entity_ids'])<=set(plan['kernel_contract']['affected_entity_ids']))

    def test_affected_port_frame_cannot_be_arbitrarily_reoriented(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        after=report(authored(p));plan=self.plan(p)
        port=next(row for row in after['witness']['structure']['boundary_ports'] if row['id'] in plan['kernel_contract']['affected_entity_ids'])
        for axis in ('x_axis','y_axis'):port['frame'][axis]=[-x for x in port['frame'][axis]]
        checked=kernel.validate_structure(**after['witness'])
        after['kernel_report']=checked;after['binding'].update({k:checked[k] for k in edit.SIGNATURES})
        with self.assertRaises(kernel.StructureError) as cm:edit.verify_authored_edit(self.before,after,plan)
        self.assertEqual(cm.exception.code,'AUTHORED_EDIT_WITNESS_MISMATCH')

    def test_old_selection_is_stale_after_a_legal_coordinate_edit(self):
        p=copy.deepcopy(BASE);p['holes'][0]['radius']=8
        after=report(authored(p));selection={key:self.before['kernel_report'][key] for key in edit.SIGNATURES}
        selection['entity_ids']=[self.before['witness']['structure']['regions'][0]['id']]
        self.assertEqual(edit.verify_authored_edit(self.before,after,self.plan(p))['status'],'pass')
        with self.assertRaises(kernel.StructureError) as cm:kernel.verify_selection(**after['witness'],selection=selection)
        self.assertEqual(cm.exception.code,'SELECTION_STALE')

    def test_face_winding_and_missing_semantic_id_fail(self):
        after=copy.deepcopy(self.before);after['witness']['mesh']['faces'][0].reverse()
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,after,self.plan())
        after=copy.deepcopy(self.before);after['authorship']['vertex_map'].pop(next(iter(after['authorship']['vertex_map'])))
        with self.assertRaises(kernel.StructureError):edit.verify_authored_edit(self.before,after,self.plan())

    def test_malformed_subdivision_configuration_fails_as_domain_error(self):
        for malformed in (None,[],False):
            p=copy.deepcopy(BASE);p['subdivision_cage']=malformed
            with self.subTest(config=malformed),self.assertRaises(kernel.StructureError):self.plan(p)

    def test_full_native_protocol_mock_panel_and_edit_integrate(self):
        # These are plain Python stand-ins, not Blender objects or native evidence.
        from test_structure_native_contract import Mesh,Object,f32
        from hardsurface.structure_native import write_authored_identity,bind_native_structure,validate_native_structure
        def actual_report(parameters):
            result=authored(parameters);obj=Object(Mesh(result['vertices_mm'],result['faces']))
            obj['hs_quad_surface_table']=json.dumps(result['face_provenance'])
            obj['hs_quad_construction_sha256']=result['construction_sha256']
            provenance=obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
            for i,row in enumerate(provenance.data):row.value=i
            creases=obj.data.attributes.new('crease_edge','FLOAT','EDGE')
            weights={tuple(sorted((a,b))):value for a,b,value in result['edge_creases']}
            for edge,row in zip(obj.data.edges,creases.data):row.value=f32(weights.get(tuple(edge.vertices),0.))
            write_authored_identity(obj,result,unit_scale=1.0)
            obj['hs_object_id']='panel-protocol-fixture';obj.data['hs_data_id']='panel-protocol-data'
            binding=bind_native_structure(obj,source_binding={'job':'host-fixture-only'},unit_scale=1.0)
            return validate_native_structure(obj,unit_scale=1.0,expected_binding=binding)
        before=actual_report(BASE)
        p=copy.deepcopy(BASE);p['holes'][0].update(center=[10,2],radius=8);p['z_max']=6.5
        plan=edit.plan_authored_edit(before['witness'],before['authorship'],p,datum_policy='fixed_bottom')
        after=actual_report(p)
        self.assertEqual(edit.verify_authored_edit(before,after,plan)['status'],'pass')
        self.assertEqual(after['kernel_report']['counts']['vertices'],992)
        self.assertEqual(len(after['semantic_control_loops']['cycles']),16)


if __name__=='__main__':unittest.main()
