import copy
import json
import unittest
from hardsurface import contract as c


def minimal_request():
    return {'schema_version':'1.0','command':'hardsurface.run','params':{
        'manifest_version':'1.0','request_id':'fixture.box.1','purpose':'contract_fixture',
        'wire':{'enabled':False}, # Numerical fixture opts out; dedicated tests cover new default wire.
        'source':{'kind':'new_scene','project_id':'contract_fixture','length_unit':'mm','up_axis':'Z'},
        'context':{'scene':'Fixture','view_layer':'ViewLayer','frame':1,'evaluation':'RENDER'},
        'resources':[], 'design':{'mode':'create_if_absent','expected_state':'absent','state':{'revision':1,'dimensions':[],'sketches':[],'features':[{'id':'block','program':{'kind':'steps','steps':[{'id':'body','op':'primitive.box','size':[20,30,10]}]}}]}},
        'protection':{'source_write':'forbidden','shared_data':'reject_shared','manual_edits':'preserve_supported_nonconflicting','on_conflict':'pause'},
        'work_units':[{'id':'build','feature_ids':['block'],'checks':['design_dimensions','closed_mesh','source_preserved','reopen'],'failure_policy':'stop_job'}],
        'quality':{'profile':'static_closed_mechanical_fixture','required':['design_dimensions','preservation','reopen'],'visual':'not_applicable_fixture_only','user_feedback':'not_applicable_fixture_only'},
        'budgets':{'max_total_steps':32,'max_geometry_vertices':200000,'max_geometry_loops':1000000,'wall_seconds':180,'cpu_threads':2},
        'output':{'publication':'candidate_only','report':'compact'}}}


def sketch_state():
    state=minimal_request()['params']['design']['state']
    state['dimensions']=[{'id':'width','role':'driving','quantity':'length','value':20,'unit':'mm'}]
    state['sketches']=[{'id':'profile','workplane':{'kind':'datum','id':'world_xy'},'entities':[
        {'id':'p0','kind':'point2d','seed':[0,0]}, {'id':'p1','kind':'point2d','seed':[20,0]},
        {'id':'line','kind':'line_segment2d','start':'p0','end':'p1'},
        {'id':'circle','kind':'circle2d','center':'p0','radius_seed':10}],
        'constraints':[{'id':'width_constraint','type':'axis_distance2d','from':'p0','to':'p1','axis':'X','dimension':'width'}],
        'profiles':[{'id':'circle_profile','outer':['circle']}]}]
    return state


class ParserTests(unittest.TestCase):
    def test_valid_defaults(self):
        source=minimal_request(); result=c.validate_request(source)
        self.assertEqual(result['params']['execution']['max_attempts'],1)
        self.assertNotIn('execution',source['params'])
        self.assertEqual(result['params']['design']['state']['features'][0]['program']['steps'][0]['center'],[0.,0.,0.])
    def test_duplicate_keys(self):
        with self.assertRaises(c.ContractError): c.strict_loads('{"x":1,"x":2}')
    def test_nonfinite(self):
        for text in ('NaN','Infinity','-Infinity','1e999'):
            with self.subTest(text=text),self.assertRaises(c.ContractError): c.strict_loads(text)
    def test_bad_unicode(self):
        for text in (b'"\xff"','"\\ud800"'):
            with self.assertRaises(c.ContractError): c.strict_loads(text)
    def test_depth(self):
        with self.assertRaises(c.ContractError): c.strict_loads('['*17+'0'+']'*17)
    def test_size(self):
        with self.assertRaises(c.ContractError): c.strict_loads(' '*c.MAX_BYTES+'{}')
    def test_bool_numeric(self):
        r=minimal_request(); r['params']['budgets']['wall_seconds']=True
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_integral_number_normalized(self):
        r=minimal_request(); r['params']['context']['frame']=1.0
        self.assertIs(type(c.validate_request(r)['params']['context']['frame']),int)
        r['params']['context']['frame']=1.5
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_unknown_nested_field(self):
        r=minimal_request(); r['params']['design']['state']['features'][0]['program']['steps'][0]['python']='print(1)'
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_request_fingerprint_ignores_id(self):
        a=minimal_request(); b=copy.deepcopy(a); b['params']['request_id']='other'
        self.assertEqual(c.content_fingerprint(a),c.content_fingerprint(b))
    def test_canonical_negative_zero_and_order(self):
        self.assertEqual(c.canonical_bytes({'z':-0.0,'a':'中文'}),' {"a":"中文","z":0.0}'.strip().encode())
    def test_path_traversal(self):
        r=minimal_request(); r['params']['resources']=[{'id':'res','kind':'image','file':'/tmp/../bad','bytes':0,'expected_sha256':'0'*64}]
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_duplicate_resource(self):
        r=minimal_request(); entry={'id':'res','kind':'image','file':'/tmp/a','bytes':0,'expected_sha256':'0'*64}; r['params']['resources']=[entry,entry]
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_fixture_acceptance_cannot_claim_visual(self):
        r=minimal_request(); r['params']['quality']['visual']='required'
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_production_reference_missing(self):
        r=minimal_request(); r['params']['purpose']='production'
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_single_authority(self):
        r=minimal_request(); r['params']['sketches']=[]
        with self.assertRaises(c.ContractError): c.validate_request(r)
    def test_schema_closed(self):
        def walk(node):
            if isinstance(node,dict):
                if node.get('type')=='object': self.assertIs(node['additionalProperties'],False)
                for v in node.values(): walk(v)
            elif isinstance(node,list):
                for v in node: walk(v)
        walk(c.schema())
    def test_array_order_preserved(self):
        self.assertNotEqual(c.fingerprint([1,2]),c.fingerprint([2,1]))

class StateTests(unittest.TestCase):
    def test_use_saved_is_copy(self):
        s=c.validate_state(sketch_state()); x=c.resolve_design({'mode':'use_saved','expected_revision':1},s)
        x['revision']=2; self.assertEqual(s['revision'],1)
    def test_create_conflict(self):
        r=minimal_request()['params']['design']
        with self.assertRaises(c.ContractError): c.resolve_design(r,r['state'])
    def test_revision_conflict(self):
        with self.assertRaises(c.ContractError): c.resolve_design({'mode':'use_saved','expected_revision':2},sketch_state())
    def test_patch_exact_hash_and_revision(self):
        s=c.validate_state(sketch_state()); x=c.resolve_design({'mode':'patch_if_revision','expected_revision':1,'state_sha256':c.fingerprint(s),'patches':[{'op':'set_dimension','id':'width','value':30}]},s)
        self.assertEqual(x['revision'],2); self.assertEqual(x['dimensions'][0]['value'],30); self.assertEqual(s['dimensions'][0]['value'],20)
    def test_patch_stale_hash(self):
        s=sketch_state()
        with self.assertRaises(c.ContractError): c.resolve_design({'mode':'patch_if_revision','expected_revision':1,'state_sha256':'0'*64,'patches':[{'op':'set_dimension','id':'width','value':30}]},s)
    def test_locked_dimension(self):
        s=sketch_state(); s['dimensions'][0]['locked']=True; s=c.validate_state(s)
        with self.assertRaises(c.ContractError): c.resolve_design({'mode':'patch_if_revision','expected_revision':1,'state_sha256':c.fingerprint(s),'patches':[{'op':'set_dimension','id':'width','value':30}]},s)
    def test_units(self):
        self.assertAlmostEqual(c.dimension_values(sketch_state())['width'],.02)
        s=sketch_state(); s['dimensions'][0]['unit']='deg'
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_typed_reference(self):
        s=sketch_state(); s['sketches'][0]['constraints']=[{'id':'bad','type':'horizontal','line':'p0'}]
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_arc_sweep_nonzero(self):
        s=sketch_state(); s['sketches'][0]['entities'].append({'id':'arc','kind':'arc2d','center':'p0','radius_seed':10,'start_angle_seed':0,'sweep_seed':0})
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_internal_tangent_needs_branch(self):
        s=sketch_state(); s['sketches'][0]['entities'].append({'id':'circle2','kind':'circle2d','center':'p1','radius_seed':5}); s['sketches'][0]['constraints'].append({'id':'tangent','type':'tangent_circle_circle','a':'circle','b':'circle2','branch':'internal'})
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_sketch_cycle(self):
        s=sketch_state(); s['sketches'][0]['depends_on']=['profile']
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_frame_not_orthogonal(self):
        s=sketch_state(); s['sketches'][0]['workplane']={'kind':'frame','id':'frame','origin':[0,0,0],'x_axis':[1,0,0],'y_axis':[1,0,0]}
        with self.assertRaises(c.ContractError): c.validate_state(s)
    def test_duplicate_entity(self):
        s=sketch_state(); s['sketches'][0]['entities'].append(copy.deepcopy(s['sketches'][0]['entities'][0]))
        with self.assertRaises(c.ContractError): c.validate_state(s)

if __name__=='__main__': unittest.main()

class BoundaryRegressionTests(unittest.TestCase):
    def test_finite_floats_can_normalize_twice(self):
        r=minimal_request(); r['params']['design']['state']['features'][0]['program']['steps'][0]['size']=[20.5,30.2,10.1]
        a=c.normalize_request(r); self.assertEqual(c.normalize_request(a),a)
    def test_huge_integer_rejected_consistently(self):
        for value in ('1'+'0'*1000, 10**1000):
            with self.assertRaises(c.ContractError):
                if isinstance(value,str): c.strict_loads(value)
                else: c.canonical_bytes(value)
    def test_standalone_sketch_references(self):
        s=sketch_state()['sketches'][0]; self.assertEqual(c.validate_sketch(s)['id'],'profile')
        s['entities'][2]['start']='missing'
        with self.assertRaises(c.ContractError): c.validate_sketch(s)
    def test_arc_dimension_type(self):
        s=sketch_state(); s['sketches'][0]['entities'].append({'id':'arc','kind':'arc2d','center':'p0','radius_seed':10,'start_angle_seed':0,'sweep_seed':90}); s['dimensions'].append({'id':'sweep','role':'driving','quantity':'angle','value':90,'unit':'deg'}); s['sketches'][0]['constraints'].append({'id':'arc_sweep','type':'arc_sweep','arc':'arc','dimension':'sweep'}); c.validate_state(s)
    def test_generated_schema_files_match_authority(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/'schemas'
        if not root.exists(): return
        for path in root.glob('*.schema.json'):
            section=path.name.removesuffix('.schema.json')
            if section in ('hardsurface-validate','hardsurface-observe','hardsurface-topology'):
                from hardsurface import validation,observation,topology
                authority={'hardsurface-validate':validation,'hardsurface-observe':observation,'hardsurface-topology':topology}[section].schema()
            else:authority=c.schema(section)
            self.assertEqual(json.loads(path.read_text()),authority)

class ReceiptTests(unittest.TestCase):
    def sample(self):
        descriptor={'file':'/tmp/job/report.json','sha256':'a'*64,'bytes':123}
        return {'report_version':'1.0','job_id':'job','request_id':'request','status':'succeeded','domain_outcome':'pass','report':descriptor,'candidate':{'file':'/tmp/job/candidate.blend','sha256':'b'*64,'bytes':100,'state':'usable_delivery'},'acceptance':{key:{'status':'pass','evidence':descriptor} for key in ('technical','preservation','dependency_reproduction')},'source_protection':{'original_source_observed':{'status':'not_applicable'},'staged_snapshot_guarded':{'files':[],'accepted':True},'resources_guarded':{'files':[],'accepted':True}},'next_step':'Review evidence'}
    def test_compact_has_bounded_descriptors(self):
        r=c.compact_report(self.sample()); self.assertTrue(r['details_omitted']); self.assertEqual(r['report_version'],'1.0'); self.assertEqual(c.validate_report(r),r)
    def test_success_without_candidate_denied(self):
        r=self.sample(); r['candidate']=None
        with self.assertRaises(c.ContractError): c.compact_report(r)
    def test_success_without_acceptance_evidence_denied(self):
        r=self.sample(); r['acceptance']['technical']={'status':'pass'}
        with self.assertRaises(c.ContractError): c.compact_report(r)
    def test_success_unknown_guard_denied(self):
        r=self.sample(); del r['source_protection']
        with self.assertRaises(c.ContractError): c.compact_report(r)
    def test_failure_keeps_error(self):
        r=self.sample(); r.update(status='failed',domain_outcome='failed',candidate=None,error={'code':'ERROR','message':'details','retry_class':'never','details':{'secret_blob':'not included'}}); receipt=c.compact_report(r); self.assertEqual(receipt['error']['code'],'ERROR'); self.assertNotIn('details',receipt['error']); self.assertEqual(receipt['error']['details_reference'],receipt['report'])
    def test_unknown_receipt_field_denied(self):
        r=c.compact_report(self.sample()); r['hidden']='value'
        with self.assertRaises(c.ContractError): c.validate_report(r)
