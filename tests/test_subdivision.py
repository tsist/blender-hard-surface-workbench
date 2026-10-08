"""Host-only contract and topology regression tests, no Blender needed."""
import copy
import json
import unittest
from pathlib import Path
from hardsurface import subdivision as s
from hardsurface.contract import ContractError
from hardsurface.host import parser

ROOT = Path(__file__).resolve().parents[1]

def request():
    return {'schema_version':'1.0','command':'hardsurface.subdivision.diagnose','params':{
        'request_id':'subd.fixture','source':{'file':'/tmp/source.blend','expected_sha256':'a'*64},
        'target':{'object_name':'Cube'},'stack_mode':'isolated_control_cage'}}

class SubdivisionContractTests(unittest.TestCase):
    def test_defaults_do_not_mutate_input(self):
        q=request(); old=copy.deepcopy(q); p=s.validate_request(q)['params']
        self.assertEqual(q,old);self.assertEqual(p['levels'],[0,1,2,3]);self.assertTrue(p['render']['enabled'])
    def test_source_target_stack_are_required(self):
        for field in ('source','target','stack_mode'):
            q=request();del q['params'][field]
            with self.assertRaises(ContractError):s.validate_request(q)
    def test_only_one_target_selector(self):
        q=request();q['params']['target']['object_id']='a'*36
        with self.assertRaises(ContractError):s.validate_request(q)
    def test_level_bounds_and_baseline(self):
        for levels in ([1,2],[0,4],[0,0],[],[False,1]):
            q=request();q['params']['levels']=levels
            with self.assertRaises(ContractError):s.validate_request(q)
    def test_resource_bounds(self):
        for key,value in [('cpu_threads',5),('wall_seconds',601),('max_evaluated_faces',250001),('max_aggregate_faces',500001)]:
            q=request();q['params'][key]=value
            with self.assertRaises(ContractError):s.validate_request(q)
        for dimensions in ({'width':2049},{'width':2048,'height':2048}):
            q=request();q['params']['render']=dimensions
            with self.assertRaises(ContractError):s.validate_request(q)
    def test_reject_unsupported_parameters(self):
        for key in ('save_blend','apply','turbo_smooth','output_file','script'):
            q=request();q['params'][key]=True
            with self.assertRaises(ContractError):s.validate_request(q)
    def test_forecast_counts_real_polygons(self):
        self.assertEqual(s.estimate_faces([4]*6,[0,1,2,3]),{'0':6,'1':24,'2':96,'3':384})
        self.assertEqual(s.estimate_faces([3,5],[0,1,2]),{'0':2,'1':8,'2':32})
    def test_schema_file(self):
        self.assertEqual(json.loads((ROOT/'schemas/hardsurface-subdivision-diagnose.schema.json').read_text()),s.schema())
    def test_public_parser(self):
        a=parser().parse_args(['hardsurface','subdivision','diagnose','--request','/tmp/request.json'])
        self.assertEqual(a.subdivision_action,'diagnose')



def cube_geometry():
    vertices=[(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]
    faces=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    edges=sorted({tuple(sorted((a,b))) for f in faces for a,b in zip(f,f[1:]+f[:1])})
    return vertices,edges,faces

class SubdivisionTopologyTests(unittest.TestCase):
    def test_cube_all_quad_does_not_claim_good_flow(self):
        r=s.analyze_cage(*cube_geometry())
        self.assertEqual(r['counts']['quads'],6);self.assertEqual(r['counts']['derived_triangle_count'],12)
        self.assertEqual(r['poles']['interior_count'],8);self.assertEqual(r['vertex_valence_histogram'],{'3':8})
        self.assertEqual(r['edge_loop_continuity']['closed_cycles'],0)
        self.assertEqual(r['quad_strip_continuity']['closed_cycles'],3)
        self.assertTrue(r['manifold']['closed_consistently_oriented'])
    def test_open_quad_is_a_boundary(self):
        r=s.analyze_cage([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[(0,1),(1,2),(2,3),(0,3)],[(0,1,2,3)])
        self.assertEqual(r['manifold']['boundary_edges'],4)
        self.assertFalse(r['manifold']['closed_consistently_oriented'])
        self.assertEqual(r['quad_strip_continuity']['closed_cycles'],0)
        self.assertEqual(r['quad_strip_continuity']['open_or_interrupted'],2)
    def test_periodic_quad_grid_has_both_loop_families(self):
        n,m=5,7
        vertices=[(i,j,0) for i in range(n) for j in range(m)]
        index=lambda i,j:(i%n)*m+j%m
        faces=[(index(i,j),index(i+1,j),index(i+1,j+1),index(i,j+1)) for i in range(n) for j in range(m)]
        edges=sorted({tuple(sorted((a,b))) for f in faces for a,b in zip(f,f[1:]+f[:1])})
        r=s.analyze_cage(vertices,edges,faces)
        self.assertEqual(r['poles']['interior_count'],0)
        self.assertEqual(r['edge_loop_continuity']['closed_cycles'],n+m)
        self.assertEqual(r['quad_strip_continuity']['closed_cycles'],n+m)
        self.assertEqual(r['vertex_valence_histogram'],{'4':n*m})
    def test_winding_conflict_reported(self):
        v,e,f=cube_geometry();f[0]=f[0][::-1]
        r=s.analyze_cage(v,e,f)
        self.assertEqual(r['manifold']['inconsistent_winding_edges'],4)
        self.assertFalse(r['manifold']['closed_consistently_oriented'])
    def test_disconnected_cages_counted(self):
        v,e,f=cube_geometry();v=v+[(x+4,y,z) for x,y,z in v];e=e+[(a+8,b+8) for a,b in e];f=f+[tuple(i+8 for i in q) for q in f]
        self.assertEqual(s.analyze_cage(v,e,f)['components'],2)

class SubdivisionFramingTests(unittest.TestCase):
    def test_landscape_horizontal_fit(self):
        self.assertAlmostEqual(s.orthographic_scale(4,3,640,480),4*1.18)
    def test_portrait_horizontal_fit(self):
        self.assertAlmostEqual(s.orthographic_scale(4,3,240,480),4*1.18)
    def test_vertical_extent_drives_horizontal_scale(self):
        self.assertAlmostEqual(s.orthographic_scale(2,6,640,480),8*1.18)




def panel_reference():
    return {'size':[96,62],'center':[0,0],'corner_radius':9,'edge_bevel':.7,
            'z_min':0,'z_max':5.5,'holes':[{'center':[11,1],'radius':8.5}],'tolerance_mm':.05}

class SubdivisionPanelReferenceTests(unittest.TestCase):
    def test_reference_is_optional_and_never_inferred(self):
        p=s.validate_request(request())['params'];self.assertNotIn('panel_reference',p)
        self.assertEqual(s.panel_reference_summary(None,[])['finite_sample_acceptance'],'not_run')
    def test_explicit_reference_normalized_without_mutating(self):
        q=request();q['params']['panel_reference']=panel_reference();original=copy.deepcopy(q)
        r=s.validate_request(q)['params']['panel_reference'];self.assertEqual(q,original)
        self.assertEqual(r['coordinate_space'],'world');self.assertEqual(r['length_unit'],'mm')
        self.assertEqual(r['kind'],'single_sharp_bore_rounded_panel')
    def test_all_nominal_fields_required(self):
        for key in panel_reference():
            q=request();ref=panel_reference();del ref[key];q['params']['panel_reference']=ref
            with self.subTest(key=key),self.assertRaises(ContractError):s.validate_request(q)
    def test_reference_domain_rejects_inconsistent_geometry(self):
        changes=[{'size':[0,62]},{'corner_radius':31},{'edge_bevel':3},{'z_max':0},
                 {'holes':[]},{'holes':[{'center':[46,0],'radius':3}]},
                 {'holes':[{'center':[39,22],'radius':9}]},
                 {'holes':[{'center':[0,0],'radius':1},{'center':[1,1],'radius':1}]},
                 {'center':[99999,0]},{'tolerance_mm':0},{'tolerance_mm':float('nan')},
                 {'length_unit':'m'},{'coordinate_space':'object_local'}]
        for delta in changes:
            q=request();ref=panel_reference();ref.update(delta);q['params']['panel_reference']=ref
            with self.subTest(delta=delta),self.assertRaises(ContractError):s.validate_request(q)
    def test_reference_failure_is_separate_from_execution(self):
        ref=panel_reference();rows=[{'level':0,'reference_check':{'status':'fail'}},{'level':2,'reference_check':{'status':'pass'}}]
        r=s.panel_reference_summary(ref,rows)
        self.assertEqual(r['status'],'caller_supplied');self.assertEqual(r['finite_sample_acceptance'],'fail')
        self.assertEqual(r['design_acceptance'],'not_assigned');self.assertFalse(r['continuous_hausdorff_certified'])
        self.assertEqual(r['sampled_levels'][1]['status'],'pass');self.assertEqual(len(r['reference_sha256']),64)
    def test_metadata_cannot_authorize_reference(self):
        r=s.panel_reference_summary(panel_reference(),[{'level':2,'reference_check':{'status':'pass'}}])
        self.assertEqual(r['reference_approval'],'not_asserted_caller_supplied_parameters_only')

class SubdivisionAuthoredMetadataTests(unittest.TestCase):
    def fake(self,values):
        from types import SimpleNamespace
        class Fake(dict):pass
        o=Fake(values);o.data=SimpleNamespace(vertices=[0,1,2,3]);o.modifiers=[];return o
    def test_ordinary_source_has_no_authored_requirement(self):
        self.assertEqual(s.inspect_authored_control_loops(self.fake({}))['status'],'not_applicable')
    def test_missing_or_malformed_authored_records_fail_without_abort(self):
        values=[{'hs_subdivision_settings':'{}'},{'hs_subd_control_loops':'[]'},
                {'hs_subdivision_settings':'null','hs_subd_control_loops':'[]'},
                {'hs_subdivision_settings':True,'hs_subd_control_loops':[]},
                {'hs_subdivision_settings':'{bad','hs_subd_control_loops':'[]'},
                {'hs_subdivision_settings':'{}','hs_subd_control_loops':'x'*300000}]
        for value in values:
            with self.subTest(value=str(value)[:60]):
                r=s.inspect_authored_control_loops(self.fake(value));self.assertEqual(r['status'],'fail')
                self.assertEqual(r['failure_kind'],'malformed_or_missing_authored_metadata')
    def test_calls_verifier_on_original_and_binds_settings(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        settings={'name':'HS_Subdivision_Cage','subdivision_type':'CATMULL_CLARK','levels':2,'render_levels':2,'quality':6,
                  'uv_smooth':'PRESERVE_BOUNDARIES','boundary_smooth':'ALL','use_creases':True,'use_limit_surface':True}
        loops=[{'role':'bore_rim_bottom','vertex_indices':[0,1,2],'crease':1,'expected_valence':4}]
        o=self.fake({'hs_subdivision_settings':json.dumps(settings),'hs_subd_control_loops':json.dumps(loops)})
        o.modifiers=[SimpleNamespace(type='SUBSURF',**settings)]
        with patch('hardsurface.subd_cage_validation.inspect_control_loops',return_value={'status':'pass','cycles':[]}) as verifier:
            r=s.inspect_authored_control_loops(o);verifier.assert_called_once_with(o)
            self.assertEqual(r['status'],'pass');self.assertEqual(r['source'],'original_object.data_before_clone')
        o.modifiers[0].levels=1
        with patch('hardsurface.subd_cage_validation.inspect_control_loops',return_value={'status':'pass','cycles':[]}):
            r=s.inspect_authored_control_loops(o);self.assertEqual(r['status'],'fail');self.assertEqual(r['authored_modifier_settings_match'],'fail')

if __name__=='__main__':unittest.main()
