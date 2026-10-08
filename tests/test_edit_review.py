"""Neutral host-only scheduler/evidence tests. No Blender or plugin loading."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hardsurface import contract as c, edit_review as w, host
from hardsurface.budgets import BudgetExceeded, BudgetLimits, JobBudget
from hardsurface.io import RuntimeFailure, atomic_json, read_json
from hardsurface.jobs import _atomic_json
from hardsurface.recovery import artifact_descriptor
from test_contract import minimal_request
from test_sparse_subdivision_diagnose import request as diagnosis_request


def request(source):
    edit = minimal_request(); e = edit['params']
    e['source'] = {'kind':'saved_blend','file':source['file'],'expected_sha256':source['sha256'],'bytes':source['bytes'],'length_unit':'mm'}
    e['design'] = {'mode':'patch_if_revision','expected_revision':1,'state_sha256':'a'*64,
                   'patches':[{'op':'insert_sparse_strip','feature_id':'block','step_id':'body','expected_feature_sha256':'b'*64,'corridor':'east','fraction':.5}]}
    e['quality']['stage']='source_cage'; e['budgets']['max_observed_rss_bytes']=2*1024**3
    d=diagnosis_request(); d['params']['source']={k:v for k,v in e['source'].items() if k in ('file','expected_sha256','bytes')}
    return {'schema_version':'1.0','command':'hardsurface.edit-review','params':{
        'request_id':'neutral.edit-review','edit_request':edit,'reference_approval':None,'before_diagnosis':d,
        'budgets':{'wall_seconds':900,'max_disk_bytes':1024**3,'max_tree_rss_bytes':3*1024**3,'cpu_threads':2,'max_native_processes':6}}}


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.q=request({'file':'/tmp/neutral.blend','sha256':'a'*64,'bytes':10})

    def test_closed_versioned_schema_matches_snapshot(self):
        actual=json.loads((Path(__file__).resolve().parents[1]/'schemas/hardsurface-edit-review.schema.json').read_text())
        self.assertEqual(actual,w.schema())
        self.assertEqual(w.validate_request(self.q)['params']['comparison_limits'],w.COMPARE_LIMITS)
        for mutation in ('version','unknown','source','target','generic','render','attempt','patch'):
            q=copy.deepcopy(self.q)
            if mutation=='version':q['schema_version']='2.0'
            if mutation=='unknown':q['params']['hidden_cache']={}
            if mutation=='source':q['params']['before_diagnosis']['params']['source']['expected_sha256']='f'*64
            if mutation=='target':q['params']['before_diagnosis']['params']['target']={'object_name':'guess'}
            if mutation=='generic':q['params']['before_diagnosis']['params']['evaluation_profile']={'mode':'legacy_geometry_diagnostic_v0'}
            if mutation=='render':q['params']['edit_request']['params']['wire']['enabled']=True
            if mutation=='attempt':q['params']['edit_request']['params']['execution']={'max_attempts':2,'technical_strategies':['declared','boolean_exact']}
            if mutation=='patch':q['params']['edit_request']['params']['design']['patches'][0]={'op':'add_sketch','sketch':{}}
            with self.subTest(mutation=mutation), self.assertRaises((c.ContractError,RuntimeFailure)):
                w.validate_request(q)

    def test_threads_and_rss_cannot_escape_aggregate(self):
        for target,key,value in (('edit_request','cpu_threads',4),('before_diagnosis','cpu_threads',4),('edit_request','max_observed_rss_bytes',4*1024**3)):
            q=copy.deepcopy(self.q); e=q['params'][target]['params']; (e['budgets'] if target=='edit_request' else e)[key]=value
            with self.assertRaises(RuntimeFailure):w.validate_request(q)

    def test_cli_commands_and_no_async(self):
        for action in ('plan','run','resume'):
            p=host.parser().parse_args(['hardsurface','edit-review',action,'--request','/tmp/x.json'])
            self.assertEqual(p.review_action,action)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='review-budget-'); self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name); self.limits=request({'file':'/tmp/x','sha256':'a'*64,'bytes':1})['params']['budgets']

    def test_live_disk_rss_and_process_budget(self):
        b=w.WorkflowBudget(self.job,self.limits)
        with self.assertRaises(BudgetExceeded):b.check(tree_rss_bytes=4*1024**3)
        for _ in range(6):b.claim_process()
        with self.assertRaises(BudgetExceeded):b.claim_process()
        self.assertEqual(read_json(b.usage_path)['native_processes_started'],6)
        (self.job/'large').write_bytes(b'a'*100)
        b=w.WorkflowBudget(self.job,{**self.limits,'max_disk_bytes':50})
        with self.assertRaises(BudgetExceeded):b.check()

    def test_cumulative_wall_and_bounded_repeated_usage_files(self):
        b=w.WorkflowBudget(self.job,self.limits,usage={'wall_seconds_observed':899.99,'native_processes_started':1},started=time.monotonic()-.1)
        with self.assertRaises(BudgetExceeded):b.check()
        b=w.WorkflowBudget(self.job,self.limits)
        for _ in range(25):b.check()
        self.assertEqual([p.name for p in self.job.iterdir()],['usage.json'])

    def test_joint_keeps_both_active_child_and_parent_caps(self):
        shared=w.WorkflowBudget(self.job,self.limits)
        local=JobBudget(BudgetLimits(wall_seconds=100,max_tree_rss_bytes=12,max_disk_bytes=20),started_at=time.monotonic())
        joint=w.JointBudget(local,shared)
        with self.assertRaises(BudgetExceeded):joint.check(tree_rss_bytes=13)
        with self.assertRaises(BudgetExceeded):joint.check(disk_bytes=21)
        self.assertIn('aggregate_workflow',joint.report())

    def test_extra_native_edit_steps_rejected_before_build(self):
        b=w.WorkflowBudget(self.job,self.limits)
        step={'op':'quad.panel','effective_params':{'topology_strategy':'sparse_control_cage'}}
        b.validate_edit_plan({'steps':[step],'resolved_design_state':{'sketches':[]}})
        with self.assertRaises(RuntimeFailure):b.validate_edit_plan({'steps':[step,{'op':'checkpoint'}]})


class GeometryReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='review-geometry-'); self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_more_than_one_million_json_nodes_is_supported(self):
        path=self.root/'large.json';path.write_text('{"values":['+','.join(['0']*1_000_010)+']}')
        actual=w._geometry(artifact_descriptor(path),self.root)
        self.assertEqual(len(actual['values']),1_000_010)

    def test_duplicate_nonfinite_depth_and_wrong_hash_fail(self):
        for raw in ('{"x":1,"x":2}','{"x":NaN}','{"x":1e999}','['*66+'0'+']'*66):
            path=self.root/'bad.json';path.write_text(raw)
            with self.assertRaises(RuntimeFailure):w._geometry(artifact_descriptor(path),self.root)
        path=self.root/'bad.json';path.write_text('{}');ref=artifact_descriptor(path);ref['sha256']='f'*64
        with self.assertRaises(Exception):w._geometry(ref,self.root)

    def test_read_after_descriptor_aba_change_is_rejected(self):
        path=self.root/'aba.json';path.write_text('{"x":1}');ref=artifact_descriptor(path)
        original=w.verify_descriptor
        def swapped(*args,**kwargs):
            result=original(*args,**kwargs);path.write_text('{"x":2}');return result
        with patch.object(w,'verify_descriptor',side_effect=swapped),self.assertRaises(RuntimeFailure):w._geometry(ref,self.root)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='review-workflow-'); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.source=self.root/'source.blend'; self.source.write_bytes(b'neutral-source')
        self.q=request(artifact_descriptor(self.source)); self.path=self.root/'request.json'; atomic_json(self.path,self.q)
        self.jobs=self.root/'jobs'; self.calls=[]; self.fault=None; self.native_binding={'object_id':self.q['params']['before_diagnosis']['params']['target']['object_id'],'topology_epoch':0}
        self.identity={'source_sha256':'a'*64,'runtime':'neutral'}
        self.patches=[patch.object(host,'impl_identity',return_value=self.identity),patch.object(host,'read_only_action',side_effect=self.diagnose),
                      patch.object(host,'submit',side_effect=self.edit),patch.object(w,'_accept_child',side_effect=self.accept),patch.object(w,'_compare',side_effect=self.compare)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def child(self,path,root,budget,stage):
        q=read_json(path); st=host.store_for(root); row=st.submit(q['params']['request_id'],c.fingerprint(q)); job=st.job_dir(row['job_id'])
        atomic_json(job/'request.json',q);self.calls.append(stage)
        for _ in range(4 if stage=='edit' else 1):budget.claim_process()
        if self.fault==stage+':unknown':raise KeyboardInterrupt('response loss after reservation')
        if self.fault==stage+':failed':st.update(job.name,status='failed');return row
        st.update(job.name,status='succeeded');atomic_json(job/'report.json',{'status':'succeeded'})
        atomic_json(job/'result.json',{'status':'succeeded'});return row

    def diagnose(self,action,path,root,blender,started,shared_budget=None):
        return self.child(path,root,shared_budget,'after' if ':after' in read_json(path)['params']['request_id'] else 'before')

    def edit(self,path,root,blender,**kwargs):
        return self.child(path,root,kwargs['shared_budget'],'edit')

    def accept(self,stage,child,request,bound,store,budget=None):
        job=store.job_dir(child['job_id']); p=request['params']
        if stage=='edit':
            candidate=job/'candidate.blend';candidate.write_bytes(b'neutral-candidate'); source=artifact_descriptor(candidate)
            profile=bound['request']['params']['before_diagnosis']['params']['evaluation_profile']
            accepted={'source':source,'target':{'object_id':self.native_binding['object_id']},'evaluation_profile':profile,
                      'before_binding':self.native_binding,'after_binding':{**self.native_binding,'topology_epoch':1}}
        else:
            source=artifact_descriptor(p['source']['file']); accepted={'source':source,'target':p['target'],'evaluation_profile':p['evaluation_profile'],
                    'native_binding':{**self.native_binding,'topology_epoch':1 if stage=='after' else 0}}
        return {'stage':stage,'child_job_id':job.name,'accepted':accepted,'artifacts':[artifact_descriptor(job/'report.json')]+([source] if stage=='edit' else [])}

    def compare(self,accepted,job,budget,request):
        self.calls.append('compare');p=job/'comparison.json';atomic_json(p,{'execution':{'status':'succeeded'},'shape_review':{'status':'pending'}})
        return {'stage':'compare','accepted':{'comparison':artifact_descriptor(p)},'artifacts':[artifact_descriptor(p)]}

    def run_work(self,**kwargs):return w.run(self.path,self.jobs,'/tmp/neutral-blender',**kwargs)

    def test_one_unit_success_resumes_without_any_child_calls(self):
        result=self.run_work();self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(self.calls,['before','edit','after','compare'])
        self.assertEqual(result['metrics']['native_processes_started'],6)
        self.assertEqual(result['shape_review']['status'],'pending')
        again=self.run_work(resume=True);self.assertEqual(again['status'],'succeeded',again)
        self.assertEqual(len(self.calls),4)
        job=host.store_for(self.jobs).job_dir(result['job_id'])
        after=read_json(job/'after-request.json')['params']
        self.assertEqual(after['source']['expected_sha256'],result['candidate']['sha256'])
        self.assertNotEqual(after['source']['file'],str(self.source))

    def test_successful_stage_checkpointed_before_interruption(self):
        original=w._checkpoint
        def interrupted(job,stage,record):
            original(job,stage,record)
            if stage=='before':raise KeyboardInterrupt('supervisor gone')
        with patch.object(w,'_checkpoint',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):self.run_work()
        result=self.run_work(resume=True);self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(self.calls,['before','edit','after','compare'])

    def test_successful_child_response_loss_reconciles_without_resubmit(self):
        original=self.accept
        first=[True]
        def lost(*args):
            if first[0]:first[0]=False;raise KeyboardInterrupt('lost response before checkpoint')
            return original(*args)
        with patch.object(w,'_accept_child',side_effect=lost),self.assertRaises(KeyboardInterrupt):self.run_work()
        result=self.run_work(resume=True);self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(self.calls,['before','edit','after','compare'])

    def test_unknown_or_running_child_never_resubmitted(self):
        self.fault='edit:unknown'
        with self.assertRaises(KeyboardInterrupt):self.run_work()
        result=self.run_work(resume=True);self.assertEqual(result['status'],'reconcile_required',result)
        self.assertEqual(self.calls,['before','edit'])
        self.assertEqual(host.store_for(self.jobs).status(result['job_id'])['status'],'running')

    def test_terminal_child_failure_persisted_never_retried(self):
        self.fault='edit:failed';result=self.run_work();self.assertEqual(result['status'],'failed')
        again=self.run_work(resume=True);self.assertEqual(again['status'],'failed')
        self.assertEqual(self.calls,['before','edit'])
        st=host.store_for(self.jobs);self.assertEqual(st.status(result['job_id'])['status'],'failed')
        self.assertEqual(read_json(st.job_dir(result['job_id'])/'result.json')['status'],'failed')

    def test_accepted_artifact_tamper_fails_without_relaunch(self):
        result=self.run_work();Path(result['candidate']['file']).write_bytes(b'changed')
        again=self.run_work(resume=True);self.assertEqual(again['status'],'failed',again)
        self.assertEqual(len(self.calls),4)
        st=host.store_for(self.jobs); job=st.job_dir(result['job_id'])
        self.assertEqual(st.status(result['job_id'])['status'],'succeeded')
        self.assertEqual(read_json(job/'result.json')['status'],'succeeded')
        self.assertEqual(read_json(job/'workflow-report.json')['status'],'failed')

    def test_nonterminal_checkpoint_validation_failure_persists_failed_receipt(self):
        original=w._checkpoint
        def lost(job,stage,record):
            original(job,stage,record)
            if stage=='before':raise KeyboardInterrupt('lost before edit')
        with patch.object(w,'_checkpoint',side_effect=lost),self.assertRaises(KeyboardInterrupt):self.run_work()
        row=host.store_for(self.jobs).recover('neutral.edit-review:edit-review')
        job=host.store_for(self.jobs).job_dir(row['job_id'])
        value=read_json(job/'usage.json');value['native_processes_started']=0;atomic_json(job/'usage.json',value)
        result=self.run_work(resume=True)
        self.assertEqual(result['status'],'failed',result)
        self.assertEqual(host.store_for(self.jobs).status(row['job_id'])['status'],'failed')
        self.assertEqual(read_json(job/'result.json')['status'],'failed')
        self.assertEqual(self.calls,['before'])

    def test_failed_parent_with_committed_comparison_never_promoted(self):
        original=w._checkpoint
        def failed(job,stage,record):
            original(job,stage,record)
            if stage=='compare':raise RuntimeFailure('RESOURCE_LIMIT','late budget failure')
        with patch.object(w,'_checkpoint',side_effect=failed):result=self.run_work()
        self.assertEqual(result['status'],'failed',result)
        again=self.run_work(resume=True)
        self.assertEqual(again['status'],'failed',again)
        st=host.store_for(self.jobs); self.assertEqual(st.status(result['job_id'])['status'],'failed')
        self.assertEqual(read_json(st.job_dir(result['job_id'])/'result.json')['status'],'failed')
        self.assertEqual(len(self.calls),4)

    def test_source_request_and_runtime_changes_reject_reuse(self):
        self.run_work();self.source.write_bytes(b'source-mutated')
        with self.assertRaises(RuntimeFailure):self.run_work(resume=True)
        self.source.write_bytes(b'neutral-source');self.identity['runtime']='different'
        with self.assertRaises(Exception):self.run_work(resume=True)
        self.assertEqual(len(self.calls),4)

    def test_target_from_another_object_rejected_before_after(self):
        original=self.accept
        def wrong(*args):
            result=original(*args)
            if args[0]=='edit':result['accepted']['before_binding']={**self.native_binding,'object_id':'different'}
            return result
        with patch.object(w,'_accept_child',side_effect=wrong):result=self.run_work()
        self.assertEqual(result['status'],'failed',result);self.assertEqual(self.calls,['before','edit'])

    def test_parent_cancel_stops_before_next_native_stage(self):
        original=w._checkpoint
        def cancel(job,stage,record):
            original(job,stage,record)
            if stage=='before':host.store_for(self.jobs).cancel(job.name)
        with patch.object(w,'_checkpoint',side_effect=cancel):result=self.run_work()
        self.assertEqual(result['status'],'cancelled',result);self.assertEqual(self.calls,['before'])
        self.assertEqual(host.store_for(self.jobs).status(result['job_id'])['status'],'cancelled')

    def test_compare_commit_response_loss_finishes_parent_state_on_resume(self):
        original=w._checkpoint
        def lost(job,stage,record):
            original(job,stage,record)
            if stage=='compare':raise KeyboardInterrupt('lost final response')
        with patch.object(w,'_checkpoint',side_effect=lost),self.assertRaises(KeyboardInterrupt):self.run_work()
        result=self.run_work(resume=True);st=host.store_for(self.jobs)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(st.status(result['job_id'])['status'],'succeeded')
        self.assertEqual(read_json(st.job_dir(result['job_id'])/'result.json')['status'],'succeeded')
        self.assertEqual(len(self.calls),4)

    def test_usage_cannot_go_backwards_on_resume(self):
        result=self.run_work();job=host.store_for(self.jobs).job_dir(result['job_id']);usage=read_json(job/'usage.json')
        usage['native_processes_started']=0;atomic_json(job/'usage.json',usage)
        again=self.run_work(resume=True);self.assertEqual(again['status'],'failed',again);self.assertEqual(len(self.calls),4)

    def test_exception_after_successful_submission_reads_existing_terminal_child(self):
        original=self.diagnose
        def uncertain(*args,**kwargs):
            original(*args,**kwargs);raise RuntimeError('response not returned')
        with patch.object(host,'read_only_action',side_effect=uncertain):result=self.run_work()
        self.assertEqual(result['status'],'succeeded',result);self.assertEqual(self.calls,['before','edit','after','compare'])

    def test_compare_cancellation_uses_cancelled_state(self):
        with patch.object(w,'_compare',side_effect=RuntimeFailure('SUBDIVISION_COMPARE_CANCELLED','owner cancelled')):result=self.run_work()
        self.assertEqual(result['status'],'cancelled',result)
        self.assertEqual(host.store_for(self.jobs).status(result['job_id'])['status'],'cancelled')


if __name__=='__main__':unittest.main()
