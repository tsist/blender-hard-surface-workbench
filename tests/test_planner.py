import copy
import unittest
from hardsurface.planner import plan_request,curve_segment_count
from hardsurface.contract import ContractError
from hardsurface.io import RuntimeFailure
from tests.test_contract import minimal_request,sketch_state

class PlannerTests(unittest.TestCase):
    def test_basic_plan(self):
        p=plan_request(minimal_request()); self.assertEqual(p['steps'][0]['step_key'],'block/body'); self.assertEqual(p['estimates']['vertices'],8); self.assertEqual(p['length_scale'],.001)
    def test_deterministic(self): self.assertEqual(plan_request(minimal_request())['plan_sha256'],plan_request(minimal_request())['plan_sha256'])
    def test_dimension_link(self):
        r=minimal_request(); s=r['params']['design']['state']; s['dimensions']=[{'id':'width','role':'driving','quantity':'length','value':2,'unit':'cm'}]; s['features'][0]['program']['steps'][0]['size'][0]={'kind':'dimension_ref','id':'width'}; p=plan_request(r); self.assertEqual(p['steps'][0]['effective_params']['size'][0],20.)
    def test_negative_dimension_rejected(self):
        r=minimal_request(); s=r['params']['design']['state']; s['dimensions']=[{'id':'width','role':'driving','quantity':'length','value':-1,'unit':'mm'}]; s['features'][0]['program']['steps'][0]['size'][0]={'kind':'dimension_ref','id':'width'}
        with self.assertRaises(ContractError): plan_request(r)
    def test_write_conflict(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; target={'kind':'step_output','step_id':'body','port':'body_object'}; steps.extend([{'id':'a','op':'edge.bevel','target':target,'width':1},{'id':'b','op':'shell.solidify','target':target,'thickness':1}])
        with self.assertRaises(ContractError) as cm: plan_request(r)
        self.assertEqual(cm.exception.code,'WRITE_CONFLICT')
    def test_dependency_resolves_write_conflict(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; target={'kind':'step_output','step_id':'body','port':'body_object'}; steps.extend([{'id':'a','op':'edge.bevel','target':target,'width':1},{'id':'b','op':'shell.solidify','target':target,'thickness':1,'depends_on':['a']}]); self.assertEqual(len(plan_request(r)['steps']),3)
    def test_unknown_port(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; steps.append({'id':'a','op':'edge.bevel','target':{'kind':'step_output','step_id':'body','port':'__dict__'},'width':1})
        with self.assertRaises(ContractError): plan_request(r)
    def test_forward_reference(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; steps.insert(0,{'id':'a','op':'edge.bevel','target':{'kind':'step_output','step_id':'body','port':'body_object'},'width':1})
        with self.assertRaises(ContractError): plan_request(r)
    def test_bounded_recipe_expansion(self):
        r=minimal_request(); r['params']['design']['state']['features'][0]['program']={'kind':'recipe','recipe_id':'perforated_panel','recipe_version':'1.0.0','inputs':{},'parameters':{'size':[100,40,10],'hole_radius':2,'hole_count':32,'hole_spacing':3,'hole_origin':[-45,0,0]}}
        with self.assertRaises(ContractError) as cm: plan_request(r)
        self.assertEqual(cm.exception.code,'BUDGET_EXCEEDED')
    def test_tiny_chord_preallocation(self):
        with self.assertRaises(ContractError): curve_segment_count(100,6.283185307179586,1e-20,200000)
    def test_pattern_budget(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; steps.append({'id':'array','op':'pattern.linear','target':{'kind':'step_output','step_id':'body','port':'body_object'},'count':128,'offset':[30,0,0]}); r['params']['budgets']['max_instances']=64
        with self.assertRaises(ContractError): plan_request(r)
    def test_long_boolean_chain_uses_labeled_input_size_heuristic(self):
        r=minimal_request();r['params']['budgets'].update(max_total_steps=64,max_geometry_vertices=400000,max_geometry_loops=2000000)
        steps=r['params']['design']['state']['features'][0]['program']['steps']
        steps.append({'id':'cutter','op':'primitive.box','size':[1,1,20]})
        target='body'
        for i in range(22):
            key='cut'+str(i)
            steps.append({'id':key,'op':'boolean.apply_or_stack','target':{'kind':'step_output','step_id':target,'port':'body_object'},'cutter':{'kind':'step_output','step_id':'cutter','port':'body_object'},'operation':'DIFFERENCE'})
            target=key
        plan=plan_request(r);estimates=plan['estimates']
        self.assertEqual(estimates['vertices'],8+22*8+8)
        self.assertEqual(estimates['loops'],24+22*24+24)
        self.assertEqual(estimates['confidence'],'heuristic_not_hard_bound')
        self.assertIsNone(estimates['geometry_policy']['boolean_output_upper_bound'])
        booleans=[s for s in plan['steps'] if s['op']=='boolean.apply_or_stack']
        self.assertEqual(len(booleans),22)
        self.assertTrue(all(s['geometry_estimate']['output_upper_bound'] is None for s in booleans))
        self.assertTrue(all(s['geometry_estimate']['runtime_evaluated_counts_required'] for s in booleans))
    def test_analytic_pattern_vertex_expansion_still_rejected(self):
        r=minimal_request();r['params']['budgets']['max_geometry_vertices']=512
        r['params']['design']['state']['features'][0]['program']['steps'].append({'id':'array','op':'pattern.linear','target':{'kind':'step_output','step_id':'body','port':'body_object'},'count':128,'offset':[30,0,0]})
        with self.assertRaises(ContractError) as cm:plan_request(r)
        self.assertEqual(cm.exception.code,'BUDGET_EXCEEDED')
        self.assertEqual(cm.exception.details['estimate'],1024)
    def test_profile_tiny_chord_still_rejected_before_allocation(self):
        r=minimal_request();r['params']['design']['state']=sketch_state()
        r['params']['design']['state']['features'][0]['program']={'kind':'steps','steps':[{'id':'body','op':'profile.extrude','profile':{'kind':'sketch_profile','sketch_id':'profile','profile_id':'circle_profile'},'depth':5,'chord_tolerance':1e-20}]}
        with self.assertRaises(ContractError) as cm:plan_request(r)
        self.assertEqual(cm.exception.code,'BUDGET_EXCEEDED')
    def test_nonuniform_transform_denied(self):
        r=minimal_request(); steps=r['params']['design']['state']['features'][0]['program']['steps']; steps.append({'id':'scale','op':'object.transform','target':{'kind':'step_output','step_id':'body','port':'body_object'},'scale':[1,2,1]})
        with self.assertRaises(ContractError): plan_request(r)
    def test_reference_external_approval(self):
        r=minimal_request(); p=r['params']; p['purpose']='production'; p['quality']['visual']='required'; p['quality']['user_feedback']='required'; p['reference_package']={'version':'1','manifest':{'file':'/tmp/reference.json','expected_sha256':'a'*64},'requirements':['outline'],'review_views':['hero']}
        with self.assertRaises(RuntimeFailure) as cm: plan_request(r)
        self.assertEqual(cm.exception.code,'REFERENCE_APPROVAL_REQUIRED')
        evidence={'approved':True,'manifest_sha256':'a'*64,'approval_reference':'synthetic-test-message','dimensions_sha256':'b'*64,'checklist_sha256':'c'*64,'unresolved_conflicts':[]}
        with self.assertRaises(RuntimeFailure) as cm: plan_request(r,reference_approval=evidence)
        self.assertEqual(cm.exception.code,'REFERENCE_APPROVAL_REQUIRED')
    def test_recipe_plate(self):
        r=minimal_request(); r['params']['design']['state']['features'][0]['program']={'kind':'recipe','recipe_id':'plate_box','recipe_version':'1.0.0','inputs':{},'parameters':{'size':[30,20,5],'bevel_width':.5}}; self.assertEqual(len(plan_request(r)['steps']),2)
    def test_recipe_schema_mismatch(self):
        r=minimal_request(); r['params']['design']['state']['features'][0]['program']={'kind':'recipe','recipe_id':'plate_box','recipe_version':'1.0.0','inputs':{},'parameters':{'radius':30,'depth':20}}
        with self.assertRaises(ContractError): plan_request(r)
    def test_profile_estimate(self):
        r=minimal_request(); r['params']['design']['state']=sketch_state(); r['params']['design']['state']['features'][0]['program']={'kind':'recipe','recipe_id':'profile_extrude_edge_finish','recipe_version':'1.0.0','inputs':{'profile':{'kind':'sketch_profile','sketch_id':'profile','profile_id':'circle_profile'}},'parameters':{'depth':5,'bevel_width':.5}}; self.assertGreater(plan_request(r)['estimates']['tessellation_vertices'],3)

if __name__=='__main__': unittest.main()

class RequiredEvidenceTests(unittest.TestCase):
    def test_missing_evidence_denied(self):
        from hardsurface.planner import validate_check_results
        with self.assertRaises(ContractError): validate_check_results(plan_request(minimal_request()),{})
    def test_empty_pass_denied(self):
        from hardsurface.planner import validate_check_results
        p=plan_request(minimal_request()); results={s['id']:{'status':'pass'} for s in p['required_checks']}
        with self.assertRaises(ContractError): validate_check_results(p,results)
    def test_explicit_results_required(self):
        from hardsurface.planner import validate_check_results
        p=plan_request(minimal_request()); results={s['id']:({'status':'pass','evidence':'verified.json'} if s['applicable'] else {'status':'not_applicable','reason':s['reason']}) for s in p['required_checks']}; self.assertEqual(validate_check_results(p,results)['status'],'pass')
    def test_no_sketch_cannot_claim_residuals(self):
        r=minimal_request(); r['params']['quality']['required'].append('constraint_residuals')
        with self.assertRaises(ContractError): plan_request(r)
    def test_fixture_cannot_claim_reference_consistency(self):
        r=minimal_request(); r['params']['quality']['required'].append('reference_consistency')
        with self.assertRaises(ContractError): plan_request(r)
    def test_cross_feature_alias_conflict(self):
        r=minimal_request(); s=r['params']['design']['state']; s['features'] += [{'id':'second','program':{'kind':'steps','steps':[{'id':'finish','op':'edge.bevel','target':{'kind':'feature_ref','feature_id':'block','port':'body_object'},'width':1}]}},{'id':'third','program':{'kind':'steps','steps':[{'id':'finish','op':'edge.bevel','target':{'kind':'feature_ref','feature_id':'block','port':'body_object'},'width':1}]}}]; r['params']['work_units'][0]['feature_ids']=['block','second','third']
        with self.assertRaises(ContractError) as cm: plan_request(r)
        self.assertEqual(cm.exception.code,'WRITE_CONFLICT')
    def test_cross_unit_declared_order(self):
        r=minimal_request(); s=r['params']['design']['state']; s['features'].insert(0,{'id':'second','program':{'kind':'steps','steps':[{'id':'body2','op':'primitive.box','size':[1,1,1]}]}}); r['params']['work_units'].append({'id':'next','feature_ids':['second'],'checks':['closed_mesh'],'failure_policy':'stop_job','depends_on':['build']}); p=plan_request(r); self.assertEqual(p['steps'][0]['feature_id'],'block')
