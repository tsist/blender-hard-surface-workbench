"""Host-only source binding and synthetic topology transport; never Blender.

Synthetic refinement is linear face splitting, not Catmull-Clark evaluation and
not native qualification. Real panel source materialization uses float32 mocks.
"""
import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from hardsurface import subdivision as s
from hardsurface.io import RuntimeFailure
from hardsurface.quad_geometry import build
from hardsurface.structure_native import FACE_SLOT, write_authored_identity, bind_native_structure
from hardsurface.subdivision_source import (capture_semantic_source, evaluated_bore_domain,
    snapshot_source_modifier, require_modifier_profile, declared_modifier_settings)
from structure_workunit_fixtures import EXPECTED_SOURCE_MODIFIER, base_state, panel_parameters, diagnosis_request
from test_structure_native_contract import Object, Mesh


def native_panel():
    result = build(panel_parameters(base_state('fixed_bottom')), 'neutral_panel')
    obj = Object(Mesh(result['vertices_mm'], result['faces']))
    obj.data.has_custom_normals = False
    obj.modifiers = [NS(**copy.deepcopy(EXPECTED_SOURCE_MODIFIER), show_expanded=True, is_active=True,
                        open_adaptive_subdivision_panel=False, open_advanced_panel=False)]
    obj['hs_quad_construction_sha256'] = result['construction_sha256']
    obj['hs_quad_constructor'] = 'quad.panel'; obj['hs_feature_id'] = 'neutral_panel'
    table=[]; lookup={}; attr=obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
    for item, row in zip(attr.data, result['face_provenance']):
        key=json.dumps(row,sort_keys=True)
        if key not in lookup: lookup[key]=len(table); table.append(row)
        item.value=lookup[key]
    obj['hs_quad_surface_table']=json.dumps(table)
    crease=obj.data.attributes.new('crease_edge','FLOAT','EDGE')
    crease_by_edge={tuple(sorted((a,b))):value for a,b,value in result['edge_creases']}
    for edge, item in zip(obj.data.edges,crease.data): item.value=crease_by_edge.get(tuple(edge.vertices),0.)
    write_authored_identity(obj,result,unit_scale=1.)
    obj['hs_object_id']='11111111-1111-4111-8111-111111111111'
    obj.data['hs_data_id']='22222222-2222-4222-8222-222222222222'
    binding=bind_native_structure(obj,source_binding={'source':{'sha256':'a'*64},'job_id':'host-only'},unit_scale=1.)
    return obj,binding


def refine_mock_quad_mesh(mesh):
    """One oriented topology-only split with exact attribute lineage."""
    vertices=[list(v.co) for v in mesh.vertices]; edges={}; faces=[]; parents=[]; labels=[]
    for fi,polygon in enumerate(mesh.polygons):
        face=list(polygon.vertices); mid=[]
        for a,b in zip(face,face[1:]+face[:1]):
            edge=tuple(sorted((a,b)))
            if edge not in edges:
                edges[edge]=len(vertices);vertices.append([(vertices[a][j]+vertices[b][j])/2 for j in range(3)])
            mid.append(edges[edge])
        center=len(vertices);vertices.append([sum(vertices[i][j] for i in face)/4 for j in range(3)])
        for i in range(4):
            faces.append([face[i],mid[i],center,mid[(i-1)%4]])
            parents.append(mesh.attributes[FACE_SLOT].data[fi].value)
            labels.append(mesh.attributes['hs_quad_surface_id'].data[fi].value)
    out=Mesh([[x*1000 for x in v] for v in vertices],faces)
    for key,values in ((FACE_SLOT,parents),('hs_quad_surface_id',labels)):
        attr=out.attributes.new(key,'INT','FACE')
        for item,value in zip(attr.data,values):item.value=value
    return out


class SourceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original,cls.binding=native_panel()
        cls.source=capture_semantic_source(cls.original,unit_scale=1.,expected_binding=cls.binding,require_bound=True)
        cls.refined=refine_mock_quad_mesh(cls.original.data)

    def setUp(self): self.obj=copy.deepcopy(self.original)

    def fail(self,code,call):
        with self.assertRaises(RuntimeFailure) as cm:call()
        self.assertEqual(cm.exception.code,code)

    def test_actual_panel_native_registry_source_authentication(self):
        self.assertTrue(self.source['evidence']['external_registry_verified'])
        self.assertTrue(self.source['evidence']['authored_bore_sector_identity_matches'])
        self.assertEqual(self.source['evidence']['source_bore_face_count'],24)
        self.assertEqual(self.source['source_face_count'],992)

    def test_complete_profile_and_copied_display_fields(self):
        actual=snapshot_source_modifier(self.obj)
        self.assertEqual(declared_modifier_settings(actual),EXPECTED_SOURCE_MODIFIER)
        self.assertEqual(require_modifier_profile(actual,EXPECTED_SOURCE_MODIFIER)['status'],'pass')
        clone=copy.deepcopy(self.obj.modifiers[0]); report=s.configure_source_clone_modifier(clone,actual,3)
        self.assertEqual(clone.quality,6);self.assertTrue(clone.use_limit_surface);self.assertTrue(clone.show_only_control_edges)
        self.assertEqual(set(report['intentional_overrides']),{'levels','render_levels'})
        report=s.configure_source_clone_modifier(clone,actual,0)
        self.assertFalse(clone.show_viewport);self.assertFalse(clone.show_render)
        self.assertEqual(set(report['intentional_overrides']),{'levels','render_levels','show_viewport','show_render'})

    def test_old_quality_and_limit_profile_fails(self):
        actual=snapshot_source_modifier(self.obj);wrong=copy.deepcopy(EXPECTED_SOURCE_MODIFIER)
        wrong.update(quality=3,use_limit_surface=False)
        self.fail('SUBDIVISION_PROFILE_MISMATCH',lambda:require_modifier_profile(actual,wrong))

    def test_each_setting_is_bound_no_bool_int_equivalence(self):
        actual=snapshot_source_modifier(self.obj)
        for key,value in [('use_creases',False),('show_only_control_edges',False),('show_in_editmode',False),('use_limit_surface',1),('render_levels',3),('adaptive_pixel_size',2.)]:
            expected=copy.deepcopy(EXPECTED_SOURCE_MODIFIER);expected[key]=value
            with self.subTest(key=key):self.fail('SUBDIVISION_PROFILE_MISMATCH',lambda:require_modifier_profile(actual,expected))

    def test_missing_runtime_property_in_request_fails(self):
        expected=copy.deepcopy(EXPECTED_SOURCE_MODIFIER);expected.pop('adaptive_space')
        self.fail('SUBDIVISION_PROFILE_MISMATCH',lambda:require_modifier_profile(snapshot_source_modifier(self.obj),expected))

    def test_unknown_writable_geometry_property_fails(self):
        self.obj.modifiers[0].new_geometry_switch=False
        self.fail('SUBDIVISION_PROFILE_UNSUPPORTED',lambda:snapshot_source_modifier(self.obj))

    def test_adaptive_custom_normals_and_disabled_flags_rejected(self):
        for key in ('use_adaptive_subdivision','use_custom_normals'):
            obj=copy.deepcopy(self.original);setattr(obj.modifiers[0],key,True)
            with self.subTest(key=key):self.fail('SUBDIVISION_PROFILE_UNSUPPORTED',lambda:snapshot_source_modifier(obj))
        for key in ('show_viewport','show_render'):
            obj=copy.deepcopy(self.original);setattr(obj.modifiers[0],key,False)
            with self.subTest(key=key):self.fail('SUBDIVISION_PROFILE_UNSUPPORTED',lambda:snapshot_source_modifier(obj))
        self.obj.data.has_custom_normals=True
        self.fail('SUBDIVISION_PROFILE_UNSUPPORTED',lambda:snapshot_source_modifier(self.obj))

    def test_scene_simplification_rejected(self):
        scene=NS(render=NS(use_simplify=True),frame_current=1,unit_settings=NS(scale_length=1.))
        self.fail('SUBDIVISION_SCENE_PROFILE_UNSUPPORTED',lambda:s.source_scene_evaluation(scene))
        scene.render.use_simplify=False;self.assertFalse(s.source_scene_evaluation(scene)['use_simplify'])

    def test_missing_external_registry_not_metadata_authentication(self):
        self.fail('SUBDIVISION_SOURCE_BINDING',lambda:capture_semantic_source(self.obj,unit_scale=1.,require_bound=True))
        wrong=copy.deepcopy(self.binding);wrong['geometry_signature']='f'*64
        self.fail('STRUCTURE_REGISTRY_DRIFT',lambda:capture_semantic_source(self.obj,unit_scale=1.,expected_binding=wrong,require_bound=True))

    def test_actual_source_modifier_drift_cannot_use_old_native_metadata(self):
        self.obj.modifiers[0].quality=3
        self.fail('STRUCTURE_CONTEXT_DRIFT',lambda:capture_semantic_source(self.obj,unit_scale=1.,expected_binding=self.binding,require_bound=True))

    def test_legacy_route_is_explicitly_unqualified(self):
        profile,source=s.prepare_source_evaluation(None,{'mode':'legacy_geometry_diagnostic_v0'},registry={},unit_scale=1.)
        self.assertIsNone(source);self.assertEqual(profile['qualification'],'not_qualified')

    def test_source_bound_request_does_not_use_old_generic_defaults(self):
        request=diagnosis_request('host',{'file':'/tmp/none.blend','sha256':'a'*64,'bytes':1},self.obj['hs_object_id'],base_state('fixed_bottom'))
        p=request['params'];self.assertNotIn('settings',p)
        self.assertEqual(p['evaluation_profile']['expected_source_modifier']['quality'],6)
        self.assertTrue(p['evaluation_profile']['expected_source_modifier']['use_limit_surface'])
        self.assertEqual(s.validate_request(request),request)
        request['params']['settings']={'quality':3,'use_limit_surface':False}
        with self.assertRaises(s.c.ContractError):s.validate_request(request)

    def test_valid_l0_and_l1_actual_attribute_transport(self):
        for level,mesh in ((0,self.obj.data),(1,self.refined)):
            row=evaluated_bore_domain(mesh,self.source,level=level)
            self.assertEqual(len(row['bore_face_indices']),24*4**level)
            self.assertEqual(row['evidence']['parent_patch_topology']['connected_disk_regions'],992)

    def test_evaluated_face_reorder_preserves_identity_without_index_guess(self):
        mesh=copy.deepcopy(self.refined);mesh.polygons.reverse()
        for key in (FACE_SLOT,'hs_quad_surface_id'):mesh.attributes[key].data.reverse()
        row=evaluated_bore_domain(mesh,self.source,level=1)
        self.assertEqual(len(row['bore_face_indices']),96)

    def test_evaluated_missing_wrong_domain_and_noninteger_labels_reject(self):
        for mutation in ('missing','wrong_domain','noninteger'):
            mesh=copy.deepcopy(self.refined)
            if mutation=='missing':del mesh.attributes['hs_quad_surface_id']
            elif mutation=='wrong_domain':mesh.attributes['hs_quad_surface_id'].domain='POINT'
            else:mesh.attributes['hs_quad_surface_id'].data[0].value=True
            with self.subTest(mutation=mutation):self.fail('SUBDIVISION_SEMANTIC_ATTRIBUTE',lambda:evaluated_bore_domain(mesh,self.source,level=1))

    def test_missing_duplicate_and_unknown_parent_labels_reject(self):
        for value in (1,992):
            mesh=copy.deepcopy(self.refined);mesh.attributes[FACE_SLOT].data[0].value=value
            self.fail('SUBDIVISION_SEMANTIC_DESCENDANTS',lambda:evaluated_bore_domain(mesh,self.source,level=1))

    def test_foreign_surface_label_cannot_alias_valid_parent(self):
        mesh=copy.deepcopy(self.refined);old=mesh.attributes['hs_quad_surface_id'].data[0].value
        mesh.attributes['hs_quad_surface_id'].data[0].value=(old+1)%self.source['surface_table_count']
        self.fail('SUBDIVISION_SEMANTIC_DESCENDANTS',lambda:evaluated_bore_domain(mesh,self.source,level=1))

    def test_balanced_paired_label_swap_fails_region_topology(self):
        mesh=copy.deepcopy(self.refined)
        for key in (FACE_SLOT,'hs_quad_surface_id'):
            rows=mesh.attributes[key].data;rows[0].value,rows[80].value=rows[80].value,rows[0].value
        self.fail('SUBDIVISION_SEMANTIC_PATCH',lambda:evaluated_bore_domain(mesh,self.source,level=1))

    def test_balanced_whole_parent_swap_fails_quotient_topology(self):
        mesh=copy.deepcopy(self.refined);parents=mesh.attributes[FACE_SLOT].data;labels=mesh.attributes['hs_quad_surface_id'].data
        a,b=parents[0].value,parents[80].value
        for parent,label in zip(parents,labels):
            if parent.value==a:parent.value=b;label.value=self.source['parent_surface_by_slot'][b]
            elif parent.value==b:parent.value=a;label.value=self.source['parent_surface_by_slot'][a]
        self.fail('SUBDIVISION_SEMANTIC_PATCH',lambda:evaluated_bore_domain(mesh,self.source,level=1))

    def test_source_semantic_attribute_drift_cannot_use_metadata(self):
        self.obj.data.attributes['hs_quad_surface_id'].data[0].value=999
        self.fail('STRUCTURE_SOURCE_DRIFT',lambda:capture_semantic_source(self.obj,unit_scale=1.,expected_binding=self.binding,require_bound=True))


if __name__=='__main__':unittest.main()
