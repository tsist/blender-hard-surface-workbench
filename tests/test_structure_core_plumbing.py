"""Host integration contracts; native core behavior must still run in Blender."""
from pathlib import Path
import ast
import copy
import json
import tempfile
import unittest
from hardsurface import contract
from hardsurface import planner
from hardsurface.io import atomic_json, checkpoint_evidence_artifacts, compact_checkpoint_record, RuntimeFailure
from test_contract import minimal_request


class StructuralRegistryPlanningTests(unittest.TestCase):
    def params(self):
        from test_subd_panel import BASE
        return copy.deepcopy(BASE)
    def test_edit_datum_has_no_hidden_default(self):
        p=contract._validate(self.params(),contract.STEP,'$')
        self.assertNotIn('edit_datum',p)
    def test_explicit_datums_schema(self):
        for datum in ('not_requested','fixed_bottom','fixed_midplane'):
            p=self.params();p['edit_datum']=datum
            self.assertEqual(contract._validate(p,contract.STEP,'$')['edit_datum'],datum)
    def test_unknown_datum_rejected(self):
        p=self.params();p['edit_datum']='guess_closest'
        with self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP,'$')
    def test_structure_gate_auto_required_for_subd(self):
        p=self.params();p['step_key']='panel/body'
        params=minimal_request()['params'];state=params['design']['state']
        checks=planner.required_checks(params,state,[p]);row=next(x for x in checks if x['id']=='structure_identity')
        self.assertEqual(row['producer'],'core');self.assertEqual(row['step_keys'],['panel/body'])
    def test_structure_gate_rejects_no_authored_scope(self):
        params=minimal_request()['params'];params['quality']['required'].append('structure_identity')
        with self.assertRaises(contract.ContractError):planner.required_checks(params,params['design']['state'],[{'op':'primitive.box','step_key':'block/body'}])
    def test_non_subd_edit_datum_rejects(self):
        params=minimal_request()['params']
        with self.assertRaises(contract.ContractError):planner.required_checks(params,params['design']['state'],[{'op':'quad.panel','topology_strategy':'tiled','edit_datum':'fixed_bottom','step_key':'p/body'}])
    def test_declared_gate_has_runtime_schema_path(self):
        r=minimal_request();r['params']['quality']['required'].append('structure_identity')
        contract.validate_request(r)
        self.assertEqual(planner.CHECK_PRODUCERS['structure_identity'],'core')
    def test_source_structure_checks_all_target_visibility(self):
        p=Path(__file__).parents[1]/'hardsurface/core.py';tree=ast.parse(p.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DomainExecutor')
        gate=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_structure_identity_check')
        attrs={n.attr for n in ast.walk(gate) if isinstance(n,ast.Attribute)}
        self.assertNotIn('hide_render',attrs);self.assertNotIn('hide_viewport',attrs)
    def test_core_plumbing_is_present_not_native_proof(self):
        text=(Path(__file__).parents[1]/'hardsurface/core.py').read_text();tree=ast.parse(text)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DomainExecutor')
        prep=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_prepare_existing')
        preptext=ast.get_source_segment(text,prep)
        self.assertLess(preptext.index('plan_authored_edit('),preptext.index('bpy.data.objects.remove'))
        reopen=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_saved_candidate')
        self.assertIn('validate_native_structure(',ast.get_source_segment(text,reopen))


class StructuralEvidenceReferenceTests(unittest.TestCase):
    def test_structure_checkpoint_projection_retained(self):
        value={'structure_identity':{'status':'pass','evidence':[]},'unrelated':'not kept'}
        self.assertIn('structure_identity',compact_checkpoint_record(value));self.assertNotIn('unrelated',compact_checkpoint_record(value))
    def sample(self,path):
        from hardsurface.identity import STRUCTURE_BINDING_FIELDS
        binding={k:'a'*64 for k in STRUCTURE_BINDING_FIELDS}
        binding.update(schema_version='native-control-structure/1.0',object_id='00000000-0000-4000-8000-000000000001',data_id='00000000-0000-4000-8000-000000000002',mesh_state='control',schedule_revision='single_sharp_panel_ids_v1',topology_epoch=0)
        kernel={'status':'pass',**{k:binding[k] for k in ('geometry_signature','topology_signature','attribute_signature','structure_signature')}}
        report={'status':'pass','native_extraction':{'status':'pass','source':'raw object.data'},'identity_binding_status':'bound','kernel_report':kernel,'binding':binding}
        ref=atomic_json(path/'structure.json',report)
        summary={'status':'pass','evidence':[{'status':'pass','object_id':binding['object_id'],'data_id':binding['data_id'],'details':ref}]}
        return {'checks':{'structure_identity':summary},'scene_snapshot':{'structure_registry':{binding['object_id']:binding}}},report
    def test_native_evidence_registered(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);r,_=self.sample(p);a=checkpoint_evidence_artifacts(r,root=p)
            self.assertEqual(list(a),['structure_identity_001'])
    def test_host_kernel_alone_not_native_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);r,value=self.sample(p);value['native_extraction']='not_checked';ref=atomic_json(p/'kernel-only.json',value);r['checks']['structure_identity']['evidence'][0]['details']=ref
            with self.assertRaises(RuntimeFailure):checkpoint_evidence_artifacts(r,root=p)
    def test_bound_object_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);r,_=self.sample(p);r['checks']['structure_identity']['evidence'][0]['object_id']='other'
            with self.assertRaises(RuntimeFailure):checkpoint_evidence_artifacts(r,root=p)
    def test_missing_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeFailure):checkpoint_evidence_artifacts({'checks':{'structure_identity':{'status':'pass','evidence':[]}}},root=Path(d))

if __name__=='__main__':unittest.main()
