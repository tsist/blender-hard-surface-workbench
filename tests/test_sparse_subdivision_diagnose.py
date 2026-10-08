"""Neutral HOST-only sparse diagnose contract and artifact boundary tests.

No Blender execution, saved-file qualification, shape fitter or visual evidence.
Synthetic subdivision fixtures exercise indexed lineage and export only.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from hardsurface import subdivision as s
from hardsurface import host
from hardsurface.contract import ContractError
from hardsurface.io import RuntimeFailure
from hardsurface.subdivision_source import evaluated_bore_domain
from structure_workunit_fixtures import EXPECTED_SOURCE_MODIFIER
from test_sparse_subdivision_source import native_sparse, capture, reorder
from test_subdivision_source_binding import native_panel, refine_mock_quad_mesh
from test_sparse_axis_geometry import parameters, EAST
from hardsurface.sparse_panel_geometry import BALANCED_FIXED_FRAME_SCHEMA


def request():
    nominal=parameters(False,BALANCED_FIXED_FRAME_SCHEMA,EAST)
    reference={key:copy.deepcopy(nominal[key]) for key in ('size','center','corner_radius','edge_bevel','z_min','z_max')}
    reference['holes']=[{key:copy.deepcopy(nominal['holes'][0][key]) for key in ('center','radius')}]
    reference['tolerance_mm']=.05
    expected={**copy.deepcopy(EXPECTED_SOURCE_MODIFIER),'levels':0,'render_levels':0}
    return {'schema_version':'1.0','command':'hardsurface.subdivision.diagnose','params':{
        'request_id':'neutral.sparse.diagnose','source':{'file':'/tmp/neutral-source.blend','expected_sha256':'a'*64},
        'target':{'object_id':'71111111-1111-4111-8111-111111111111'},'stack_mode':'isolated_control_cage',
        'evaluation_profile':{'mode':s.SPARSE_DIAGNOSTIC_PROFILE,'expected_source_modifier':expected},
        'levels':[0,1,2,3],'render':{'enabled':False},'export_geometry':True,
        'panel_reference':reference,'max_tree_rss_bytes':3*1024**3,'cpu_threads':1}}


class SparseDiagnosticContractTests(unittest.TestCase):
    def rejected(self,q):
        with self.assertRaises(ContractError):s.validate_request(q)

    def test_explicit_bound_profile_has_no_generic_settings_or_render(self):
        q=request();before=copy.deepcopy(q);p=s.validate_request(q)['params']
        self.assertEqual(q,before);self.assertNotIn('settings',p)
        self.assertEqual(p['levels'],[0,1,2,3]);self.assertFalse(p['render']['enabled'])
        self.assertTrue(p['export_geometry']);self.assertEqual(p['cpu_threads'],1)

    def test_object_uuid_reference_and_complete_modifier_are_required(self):
        for key in ('panel_reference','target'):
            q=request();del q['params'][key];self.rejected(q)
        q=request();q['params']['target']={'object_name':'Neutral'};self.rejected(q)
        for key in s._SOURCE_MODIFIER['required']:
            q=request();del q['params']['evaluation_profile']['expected_source_modifier'][key]
            with self.subTest(key=key):self.rejected(q)

    def test_no_generic_settings_render_partial_levels_or_missing_export(self):
        for key,value in (('settings',{'quality':3}),('render',{'enabled':True}),('levels',[0,2]),
                          ('levels',[0,1,3]),('export_geometry',False),('surface_export',{})):
            q=request();q['params'][key]=value
            with self.subTest(key=key,value=value):self.rejected(q)
        for key in ('levels','render','export_geometry'):
            q=request();del q['params'][key]
            with self.subTest(missing=key):self.rejected(q)

    def test_sparse_authored_baseline_cannot_be_silently_changed(self):
        for key,value in (('levels',2),('render_levels',2),('quality',3),('use_limit_surface',False),
                          ('use_creases',False),('show_viewport',False),('show_render',False)):
            q=request();q['params']['evaluation_profile']['expected_source_modifier'][key]=value
            with self.subTest(key=key):self.rejected(q)

    def test_rss_is_sparse_only_integer_one_to_three_gib(self):
        for value in (True,1024**3-1,3*1024**3+1,2.*1024**3):
            q=request();q['params']['max_tree_rss_bytes']=value;self.rejected(q)
            with self.assertRaises(ContractError):
                host._read_only_budget('subdivision.diagnose',q['params'],time.monotonic())
        for mode in ('legacy_geometry_diagnostic_v0','source_bound_qualification_v1'):
            q=request();q['params']['evaluation_profile']['mode']=mode
            if mode.startswith('legacy'):q['params']['evaluation_profile'].pop('expected_source_modifier')
            self.rejected(q)
            with self.assertRaises(ContractError):host._read_only_budget('subdivision.diagnose',q['params'],time.monotonic())
        for value in (1024**3,3*1024**3):
            q=request();q['params']['max_tree_rss_bytes']=value
            p=s.validate_request(q)['params'];budget=host._read_only_budget('subdivision.diagnose',p,time.monotonic())
            self.assertEqual(budget.limits.max_tree_rss_bytes,value)
        p=s.validate_request(request())['params'];del p['max_tree_rss_bytes']
        self.assertEqual(host._read_only_budget('subdivision.diagnose',p,time.monotonic()).limits.max_tree_rss_bytes,1024**3)

    def test_schema_snapshot_has_both_old_and_new_profiles(self):
        schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/hardsurface-subdivision-diagnose.schema.json').read_text())
        self.assertEqual(schema,s.schema())
        modes=[r['properties']['mode']['const'] for r in schema['properties']['params']['properties']['evaluation_profile']['oneOf']]
        self.assertEqual(modes,['legacy_geometry_diagnostic_v0','source_bound_qualification_v1',s.SPARSE_DIAGNOSTIC_PROFILE])


class SparseDiagnosticSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original,cls.binding,cls.constructor=native_sparse()
        cls.source=capture(cls.original,cls.binding)

    def test_source_prepare_and_loop_inspection_never_call_legacy_verifier(self):
        obj=copy.deepcopy(self.original);profile=request()['params']['evaluation_profile']
        before=(s._mesh_arrays(obj.data),copy.deepcopy(dict(obj)),copy.deepcopy(dict(obj.data)),copy.deepcopy(vars(obj.modifiers[0])))
        with patch('hardsurface.subd_cage_validation.inspect_control_loops',side_effect=AssertionError('legacy path')):
            report,source=s.prepare_source_evaluation(obj,profile,registry={obj['hs_object_id']:self.binding},unit_scale=1.)
            loops=s.inspect_sparse_authored_control_loops(obj,source)
        self.assertEqual(report['qualification'],'source_bound_diagnostic_only')
        self.assertEqual(loops['status'],'pass',loops)
        self.assertEqual(before,(s._mesh_arrays(obj.data),dict(obj),dict(obj.data),vars(obj.modifiers[0])))
        self.assertEqual({row['classification'] for row in loops['cycles']},{'regular_edge_loop','pole_crossing_edge_cycle'})
        self.assertTrue(all(row['actual_edges_and_creases']=='pass' for row in loops['cycles']))

    def test_sparse_rejects_legacy_source_and_missing_registry(self):
        profile=request()['params']['evaluation_profile']
        with self.assertRaises(RuntimeFailure):s.prepare_source_evaluation(self.original,profile,registry={},unit_scale=1.)
        obj,binding=native_panel();profile['expected_source_modifier']=copy.deepcopy(EXPECTED_SOURCE_MODIFIER)
        with self.assertRaises(RuntimeFailure):
            s.prepare_source_evaluation(obj,profile,registry={obj['hs_object_id']:binding},unit_scale=1.)

    def test_complete_profile_tamper_and_unknown_writable_field_fail(self):
        for key,value in (('show_only_control_edges',False),('quality',3),('use_limit_surface',False)):
            profile=request()['params']['evaluation_profile'];profile['expected_source_modifier'][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeFailure):
                s.prepare_source_evaluation(self.original,profile,registry={self.original['hs_object_id']:self.binding},unit_scale=1.)
        obj=copy.deepcopy(self.original);obj.modifiers[0].foreign_evaluation_switch=True
        with self.assertRaises(RuntimeFailure):
            s.prepare_source_evaluation(obj,request()['params']['evaluation_profile'],registry={obj['hs_object_id']:self.binding},unit_scale=1.)

    def test_persisted_loop_declarations_cannot_override_authorship(self):
        for mutation in ('role','index','null','crease','duplicate','missing','extra'):
            obj=copy.deepcopy(self.original);loops=json.loads(obj['hs_subd_control_loops'])
            if mutation=='role':loops[0]['role']='invented_cycle'
            elif mutation=='index':loops[0]['vertex_indices'][0]=(loops[0]['vertex_indices'][0]+1)%len(obj.data.vertices)
            elif mutation=='null':next(row for row in loops if row['expected_valence']==4)['expected_valence']=None
            elif mutation=='crease':loops[0]['crease']=.5
            elif mutation=='duplicate':loops.append(copy.deepcopy(loops[0]))
            elif mutation=='missing':loops.pop()
            else:loops[0]['pass']=True
            obj['hs_subd_control_loops']=json.dumps(loops)
            with self.subTest(mutation=mutation):self.assertEqual(s.inspect_sparse_authored_control_loops(obj,self.source)['status'],'fail')

    def test_actual_crease_and_valence_are_read_again(self):
        for mutation in ('crease','edge','valence','settings'):
            obj=copy.deepcopy(self.original);cycle=self.source['control_loops'][0]['vertex_indices']
            edge=next(e for e in obj.data.edges if set(e.vertices)==set(cycle[:2]))
            if mutation=='crease':obj.data.attributes['crease_edge'].data[edge.index].value=.5
            elif mutation=='edge':edge.vertices=[cycle[0],(cycle[1]+3)%len(obj.data.vertices)]
            elif mutation=='valence':
                obj.data.edges.append(NS(index=len(obj.data.edges),vertices=[cycle[0],next(i for i in range(len(obj.data.vertices)) if i not in cycle)]))
                obj.data.attributes['crease_edge'].data.append(NS(value=0.))
            else:obj['hs_subdivision_settings']=json.dumps({**self.constructor['subdivision_modifier'],'quality':3})
            with self.subTest(mutation=mutation):self.assertEqual(s.inspect_sparse_authored_control_loops(obj,self.source)['status'],'fail')

    def test_index_reorder_carries_native_ids_but_keeps_original_declarations(self):
        obj=copy.deepcopy(self.original);reorder(obj);source=capture(obj,self.binding)
        self.assertNotEqual(source['control_loops'],source['authored_control_loops'])
        report=s.inspect_sparse_authored_control_loops(obj,source)
        self.assertEqual(report['status'],'pass',report)

    def test_level_zero_is_raw_baseline_and_all_writable_fields_copy(self):
        actual=self.source['evidence']['actual_source_modifier'];clone=NS(**copy.deepcopy(actual))
        for level in range(4):
            result=s.configure_source_clone_modifier(clone,actual,level)
            self.assertEqual(clone.levels,level);self.assertEqual(clone.render_levels,level)
            self.assertEqual(clone.show_viewport,bool(level));self.assertEqual(clone.show_render,bool(level))
            self.assertTrue(clone.use_limit_surface);self.assertEqual(clone.quality,6)
            self.assertTrue(set(result['intentional_overrides'])<={'levels','render_levels','show_viewport','show_render'})


class SparseDiagnosticReportTests(unittest.TestCase):
    def rows(self,statuses):
        return [{'level':i,'reference_check':{'status':status,'proximity_status':status,
                'feature_witnesses':{'bbox_status':status,'volume_screen_status':status},
                'hole_slices':{'bottom_rim':{'status':status}}}} for i,status in enumerate(statuses)]

    def test_l0_failure_does_not_decide_l2_and_l3_success_does_not_rescue_it(self):
        reference=s.validate_request(request())['params']['panel_reference']
        for statuses,l2 in ((['fail','fail','pass','fail'],'pass'),(['pass','pass','fail','pass'],'fail')):
            report=s.sparse_panel_reference_summary(reference,self.rows(statuses))
            self.assertEqual(report['l2_limited_diagnostics']['finite_proximity'],l2)
            self.assertEqual(report['l2_limited_diagnostics']['bbox'],l2)
            self.assertEqual(report['finite_sample_acceptance'],'limited_diagnostic_only')
            self.assertEqual(report['shape_status'],'shape_not_qualified')
            self.assertTrue(all(row['status']=='not_run' for row in report['formal_measurements'].values()))
            rays=report['formal_measurements']['bore_radial_ray_sections']
            self.assertEqual(rays['numeric_obligations'],'caller_frozen_contract_not_bound')
            self.assertNotIn('rays_per_station',rays);self.assertNotIn('nominal_z_stations_mm',rays)
            self.assertNotIn('tolerance_mm',report['formal_measurements']['flatness_with_exclusion_mask'])
            self.assertNotIn('tolerance_px',report['formal_measurements']['silhouette_deviation'])

    def test_measured_failures_and_not_sampled_are_retained(self):
        rows=self.rows(['fail']*4);rows[2]['reference_check']['hole_slices']['middle_wall']={'status':'not_sampled'}
        report=s.sparse_panel_reference_summary(s.validate_request(request())['params']['panel_reference'],rows)
        self.assertEqual(report['l2_limited_diagnostics']['vertex_station_screen']['middle_wall'],'not_sampled')
        self.assertEqual(report['design_acceptance'],'not_assigned')

    def test_missing_l2_cannot_generate_formal_target_summary(self):
        with self.assertRaises(RuntimeFailure):
            s.sparse_panel_reference_summary(s.validate_request(request())['params']['panel_reference'],self.rows(['pass','pass']))


class IdentityMatrix(list):
    def __init__(self):super().__init__([[int(i==j) for j in range(4)] for i in range(4)])
    def __matmul__(self,vector):return list(vector)


class SparseDiagnosticExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj,cls.binding,_=native_sparse();cls.source=capture(cls.obj,cls.binding)
        cls.mesh=refine_mock_quad_mesh(cls.obj.data)

    def test_sparse_export_real_edges_polygons_native_triangles_and_bound_regions(self):
        mesh=copy.deepcopy(self.mesh)
        # Explicit mock native tessellation is supplied, not generated by export.
        mesh.loop_triangles=[NS(vertices=(0,1,2),polygon_index=7)]
        domain=evaluated_bore_domain(mesh,self.source,level=1)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'mathutils':NS(Vector=list)}):
            out=s._export_geometry(mesh,IdentityMatrix(),Path(tmp),1,{'sha256':'a'*64},'b'*64,[0],semantic_source=self.source,semantic_domain=domain)
            data=json.loads(Path(out['file']).read_text())
        self.assertEqual(data['edges'],[list(e.vertices) for e in mesh.edges])
        self.assertEqual(data['polygons'],[list(p.vertices) for p in mesh.polygons])
        self.assertEqual(data['loop_triangles'],[{'vertices':[0,1,2],'polygon_index':7}])
        regions=data['bound_semantic_regions']
        self.assertEqual(regions['evaluated_face_parent_slots'],domain['parent_slots'])
        self.assertEqual(regions['bore_evaluated_face_indices'],domain['bore_face_indices'])
        self.assertEqual(len(regions['source_face_ids_by_parent_slot']),666)
        self.assertEqual(len(regions['evaluated_face_parent_slots']),666*4)
        self.assertEqual(regions['flat_exclusion_mask_qualification'],'not_run')

    def test_export_rejects_missing_unbound_and_stale_maps(self):
        domain=evaluated_bore_domain(self.mesh,self.source,level=1)
        cases=[(None,domain),(self.source,None)]
        bad=copy.deepcopy(domain);bad['parent_slots'][0]+=1;cases.append((self.source,bad))
        bad=copy.deepcopy(self.source);bad['evidence']['external_registry_verified']=False;cases.append((bad,domain))
        for source,row in cases:
            with self.assertRaises(RuntimeFailure):s.sparse_geometry_regions(self.mesh,source,row,level=1)

    def test_legacy_export_does_not_gain_sparse_fields(self):
        mesh=copy.deepcopy(self.mesh);mesh.loop_triangles=[]
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'mathutils':NS(Vector=list)}):
            out=s._export_geometry(mesh,IdentityMatrix(),Path(tmp),1,{'sha256':'a'*64},'b'*64,[0])
            data=json.loads(Path(out['file']).read_text())
        self.assertNotIn('bound_semantic_regions',data)


if __name__=='__main__':unittest.main()
