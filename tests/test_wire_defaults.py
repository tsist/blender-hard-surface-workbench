import copy,unittest
from hardsurface import contract,planner,observation,topology
from test_contract import minimal_request

class WireDefaults(unittest.TestCase):
    def request(self):
        q=minimal_request();q['params'].pop('wire',None);return q
    def test_run_default_on_and_off_explicit(self):
        q=self.request();p=contract.normalize_request(q)['params'];self.assertTrue(p['wire']['enabled']);self.assertTrue(p['wire']['component_views']);self.assertEqual(p['wire']['mesh_state'],'evaluated')
        q['params']['wire']={'enabled':False};self.assertFalse(contract.normalize_request(q)['params']['wire']['enabled'])
    def test_observe_default_on(self):
        q={'schema_version':'1.0','command':'hardsurface.observe','params':{'request_id':'001','source':{'file':'/tmp/fixture.blend','expected_sha256':'a'*64},'views':[{'name':'wire','visible_feature_ids':['body'],'direction':'top'}]}}
        p=observation.validate_request(q)['params'];self.assertTrue(p['wire']['enabled']);self.assertEqual(p['views'][0]['mesh_state'],'evaluated')
    def test_numeric_ids_valid_invalid_prefix_early(self):
        q=self.request();q['params']['request_id']='001';planner.plan_request(q)
        q['params']['request_id']='_run'
        with self.assertRaises(contract.ContractError):contract.validate_request(q)
    def test_sample_budget_includes_default_wire(self):
        q=self.request();q['params']['budgets']['max_preview_samples']=1
        with self.assertRaises(contract.ContractError) as caught:planner.plan_request(q)
        self.assertEqual(caught.exception.code,'BUDGET_EXCEEDED')
    def test_artifact_capacity_rejects_before_geometry(self):
        q=self.request();p=q['params'];p['wire']={'component_views':False};p['budgets']['max_total_steps']=128
        p['design']['state']['features']=[{'id':'part'+str(i),'program':{'kind':'steps','steps':[{'id':'body','op':'primitive.box','size':[1,1,1]}]}} for i in range(128)]
        p['work_units'][0]['feature_ids']=[f['id'] for f in p['design']['state']['features']]
        with self.assertRaises(contract.ContractError) as caught:planner.plan_request(q)
        self.assertEqual(caught.exception.code,'BUDGET_EXCEEDED');self.assertIn('artifact capacity',str(caught.exception))
    def test_width_and_unknown_options_rejected(self):
        for val in (0,0.5,2.1,float('nan'),True):
            q=self.request();q['params']['wire']={'line_width_px':val}
            with self.subTest(value=val),self.assertRaises(contract.ContractError):contract.validate_request(q)
        q=self.request();q['params']['wire']={'triangulate_and_claim_original':True}
        with self.assertRaises(contract.ContractError):contract.validate_request(q)
