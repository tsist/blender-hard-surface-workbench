"""HOST-only scopes, raw-array evidence and orchestration. No native Blender."""
import copy
import unittest
from hardsurface import edit_review as w, contract as c
from hardsurface.structure_edit import plan_authored_edit, verify_authored_edit
from test_edit_review import request
from test_sparse_pipeline_integration import parameters, materialized


def scope_fixture(category):
    old=c._validate(parameters(),c.STEP);new=copy.deepcopy(old)
    if category=='hole_diameter':new['holes'][0]['radius']=14.
    elif category=='hole_position':new['holes'][0]['center']=[-3.,-2.]
    elif category=='thickness':new.update(z_min=-5.,z_max=5.)
    elif category=='outer_roundover':new['edge_bevel']=1.2
    state={'dimensions':[],'sketches':[],'features':[{'id':'neutral','program':{'kind':'steps','steps':[old]}}]}
    plan={'length_scale':.001,'steps':[{'feature_id':'neutral','op':'quad.panel','effective_params':new}]}
    return plan,state


class ParameterScopeTests(unittest.TestCase):
    def test_all_four_single_categories(self):
        for category in ('hole_diameter','hole_position','thickness','outer_roundover'):
            with self.subTest(category=category):self.assertEqual(w.parameter_edit_scope(*scope_fixture(category)),category)

    def test_dimension_names_do_not_define_permission(self):
        plan,state=scope_fixture('hole_diameter')
        state['dimensions']=[{'id':'arbitrary_label','quantity':'length','role':'driving','unit':'mm','value':12}]
        state['features'][0]['program']['steps'][0]['holes'][0]['radius']={'kind':'dimension_ref','id':'arbitrary_label'}
        self.assertEqual(w.parameter_edit_scope(plan,state),'hole_diameter')
        state['features'][0]['program']['steps'][0]['size'][0]={'kind':'dimension_ref','id':'arbitrary_label'}
        with self.assertRaises(Exception):w.parameter_edit_scope(plan,state)

    def test_combinations_noops_technical_policy_and_extra_feature_rejected(self):
        for mutation in ('combination','noop','policy','datum','feature','step','sketch'):
            plan,state=scope_fixture('hole_diameter');new=plan['steps'][0]['effective_params']
            if mutation=='combination':new['edge_bevel']=1.2
            if mutation=='noop':new['holes'][0]['radius']=12.
            if mutation=='policy':new['sparse_cage']['preview_levels']=2
            if mutation=='datum':new['edit_datum']='fixed_bottom'
            if mutation=='feature':state['features'].append(copy.deepcopy(state['features'][0]))
            if mutation=='step':state['features'][0]['program']['steps'].append(copy.deepcopy(new))
            if mutation=='sketch':state['sketches']=[{}]
            with self.subTest(mutation=mutation),self.assertRaises(Exception):w.parameter_edit_scope(plan,state)

    def test_thickness_requires_unchanged_saved_explicit_datum(self):
        for datum in ('not_requested','fixed_bottom'):
            plan,state=scope_fixture('thickness')
            state['features'][0]['program']['steps'][0]['edit_datum']=datum
            plan['steps'][0]['effective_params']['edit_datum']=datum
            with self.assertRaises(Exception):w.parameter_edit_scope(plan,state)
        plan,state=scope_fixture('thickness');old=state['features'][0]['program']['steps'][0]
        old['edit_datum']='fixed_bottom';new=plan['steps'][0]['effective_params'];new['edit_datum']='fixed_bottom';new['z_min']=old['z_min']
        self.assertEqual(w.parameter_edit_scope(plan,state),'thickness')

    def test_request_allows_at_most_two_dimensions_one_feature(self):
        q=request({'file':'/tmp/neutral.blend','sha256':'a'*64,'bytes':10})
        q['params']['after_panel_reference']=copy.deepcopy(q['params']['before_diagnosis']['params']['panel_reference'])
        q['params']['after_panel_reference']['holes'][0]['radius']+=1
        e=q['params']['edit_request']['params']
        for count in (1,2):
            e['design']['patches']=[{'op':'set_dimension','id':'d'+str(i),'value':i+1} for i in range(count)]
            self.assertEqual(len(w.validate_request(q)['params']['edit_request']['params']['design']['patches']),count)
        e['design']['patches'].append({'op':'set_dimension','id':'d2','value':3})
        with self.assertRaises(Exception):w.validate_request(q)
        e['design']['patches']=e['design']['patches'][:1];e['work_units'][0]['feature_ids']=['block','other']
        with self.assertRaises(Exception):w.validate_request(q)


class ParameterImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=parameters();_,cls.before=materialized(cls.base)

    def test_actual_array_impact_and_identity_bindings_all_categories(self):
        for category in ('hole_diameter','hole_position','thickness','outer_roundover'):
            plan,_=scope_fixture(category);q=plan['steps'][0]['effective_params']
            # Keep the exact complete author parameters rather than unrelated schema defaults.
            target=copy.deepcopy(self.base)
            for key in ('holes','z_min','z_max','edge_bevel'):target[key]=q[key]
            ep=plan_authored_edit(self.before['witness'],self.before['authorship'],target,datum_policy='fixed_midplane')
            _,after=materialized(target);proof=verify_authored_edit(self.before,after,ep)
            with self.subTest(category=category):
                self.assertEqual(proof['before_binding'],self.before['binding']);self.assertEqual(proof['after_binding'],after['binding'])
                imp=proof['impact'];self.assertTrue(imp['changed_vertex_ids'])
                self.assertFalse(set(imp['changed_vertex_ids']) & set(imp['protected_vertex_ids']))
                self.assertEqual(set(imp['changed_vertex_ids']) | set(imp['unchanged_vertex_ids']),set(self.before['authorship']['vertex_map']))
                self.assertEqual(imp['affected_face_ids'],ep['kernel_contract']['affected_face_ids'])
                self.assertEqual(imp['protected_coordinates'],'exactly_preserved')
                self.assertEqual(imp['evaluated_surface_preservation'],'not_asserted')
                self.assertEqual(proof['before_binding']['topology_epoch'],proof['after_binding']['topology_epoch'])
                if category=='outer_roundover':self.assertEqual(proof['protected_hole_regions']['status'],'pass')


if __name__=='__main__':unittest.main()

class PlanEvidenceTests(unittest.TestCase):
    def test_plan_content_and_checkpoint_hash_both_required(self):
        plan={'request':{'neutral':True},'steps':[{'category':'hole_diameter'}]}
        sha=c.fingerprint(plan);plan['plan_sha256']=sha;fp={'plan':{'base':sha}}
        w.verify_edit_plan_binding(plan,fp,plan['request'])
        wrong=copy.deepcopy(plan);wrong['steps'][0]['category']='thickness'
        with self.assertRaises(Exception):w.verify_edit_plan_binding(wrong,fp,plan['request'])
        wrong['plan_sha256']=c.fingerprint({k:v for k,v in wrong.items() if k!='plan_sha256'})
        with self.assertRaises(Exception):w.verify_edit_plan_binding(wrong,fp,plan['request'])
        with self.assertRaises(Exception):w.verify_edit_plan_binding(plan,fp,{'neutral':False})

class AfterReferenceTests(unittest.TestCase):
    def test_explicit_references_propagate_only_matching_category(self):
        for category in ('hole_diameter','hole_position','thickness','outer_roundover'):
            q=request({'file':'/tmp/neutral.blend','sha256':'a'*64,'bytes':10})
            p=q['params'];before=p['before_diagnosis']['params'];after=copy.deepcopy(before['panel_reference'])
            if category=='hole_diameter':after['holes'][0]['radius']+=1
            if category=='hole_position':after['holes'][0]['center'][0]+=1
            if category=='thickness':after['z_max']+=1
            if category=='outer_roundover':after['edge_bevel']+=.1
            p['after_panel_reference']=after
            binding={'neutral':True}
            accepted={'before':{'accepted':{'target':before['target'],'native_binding':binding,'evaluation_profile':before['evaluation_profile']}},
                      'edit':{'accepted':{'target':before['target'],'before_binding':binding,'evaluation_profile':before['evaluation_profile'],
                                         'source':{'file':'/tmp/candidate.blend','sha256':'b'*64,'bytes':10},'edit_category':category}}}
            self.assertEqual(w._child_request('after',q,accepted)['params']['panel_reference'],after)
            self.assertNotEqual(before['panel_reference'],after)
            accepted['edit']['accepted']['edit_category']='wrong'
            with self.assertRaises(Exception):w._child_request('after',q,accepted)

    def test_tolerance_frame_and_mixed_reference_edits_fail(self):
        q=request({'file':'/tmp/neutral.blend','sha256':'a'*64,'bytes':10});before=q['params']['before_diagnosis']['params']['panel_reference']
        for field in ('tolerance_mm','corner_radius','size'):
            after=copy.deepcopy(before);after['holes'][0]['radius']+=1
            if field=='size':after[field][0]+=1
            else:after[field]+=.1
            with self.assertRaises(Exception):w.reference_edit_category(before,after)

class EditWitnessCheckpointTests(unittest.TestCase):
    def test_edit_proof_frozen_before_parent_adoption(self):
        import tempfile,json
        from pathlib import Path
        from hardsurface.io import checkpoint_evidence_artifacts
        from hardsurface.recovery import artifact_descriptor
        with tempfile.TemporaryDirectory(prefix='host-proof-pin-') as directory:
            root=Path(directory);path=root/'proof.json'
            path.write_text(json.dumps({'status':'pass','impact':{'changed_vertex_ids':['vertex:a']}}))
            ref=artifact_descriptor(path)
            report={'steps':[{'evidence':{'structure_edit':{'status':'pass','evidence':ref}}}]}
            artifacts=checkpoint_evidence_artifacts(report,root=root)
            self.assertEqual(artifacts['structure_edit_0000'],ref)
            path.write_text(json.dumps({'status':'pass','impact':{'changed_vertex_ids':[]}}))
            with self.assertRaises(Exception):checkpoint_evidence_artifacts(report,root=root)
