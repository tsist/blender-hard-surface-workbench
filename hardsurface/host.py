# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict public CLI supervisor for isolated saved-file hard-surface work units."""
from __future__ import annotations
import argparse,contextlib,copy,json,os,shutil,subprocess,sys,time,traceback,uuid,fcntl,math,hashlib
from pathlib import Path
from .io import RuntimeFailure,atomic_json,descriptor,digest,verify_descriptor,tree_manifest,checked_path,checkpoint_evidence_artifacts
from .protection import ProtectedInputs,stage_inputs,recheck_originals
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_BLENDER=os.environ.get('BLENDER_PATH') or shutil.which('blender') or 'blender'
DEFAULT_JOBS='/tmp/hardsurface-dev-jobs'
TERMINAL={'succeeded','failed','timed_out','cancelled'}

def load(path):
    from .io import read_json
    return read_json(Path(path).absolute())

def impl_identity(blender):
    code={str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'hardsurface').rglob('*.py'))}
    for extra in ('blender_worker.py','blender_manifest.toml','hardsurface-cli'):
        if (ROOT/extra).is_file():code[extra]=digest(ROOT/extra)
    from .contract import fingerprint
    return {'source_sha256':fingerprint(code),'python':descriptor(sys.executable),'python_version':sys.version,'blender':descriptor(blender)}

def saved_reference(job):
    path=job/'reference-input.json'
    if not path.is_file():return None
    value=load(path)
    verify_descriptor(value['origin']);verify_descriptor(value['snapshot'])
    return value['snapshot']

def persist_reference(job,approval):
    if approval is None:return
    from .io import copy_verified
    copied,_=copy_verified(approval,job/'reference-approval.json',limit_bytes=1024*1024)
    atomic_json(job/'reference-input.json',{'origin':approval,'snapshot':{'file':copied['file'],'expected_sha256':copied['sha256']}})


def derived_request_id(original,suffix):
    value=original+':'+suffix
    return value if len(value)<=128 else original[:60]+':derived:'+hashlib.sha256(value.encode('ascii')).hexdigest()[:48]


def store_for(root):
    from .jobs import JobStore
    return JobStore(Path(root),allowed_roots=[Path('/tmp'),ROOT])

def error_dict(exc):
    return {'code':getattr(exc,'code','WORKER_FAILED'),'detail_code':getattr(exc,'detail_code',getattr(exc,'code',type(exc).__name__)),'message':str(exc),'details':getattr(exc,'details',{}),'retry_class':'never' if 'GUARD' in str(getattr(exc,'code','')) else 'new_input_required'}

def compact(report):
    from .contract import compact_report
    return compact_report(report)


def worker(payload,job,budget,store,blender,label,*,check_cancel=True):
    from .jobs import OwnedProcess
    from .budgets import tree_file_bytes
    path=job/(label+'-payload.json');out=Path(payload.get('job_dir',job))/(label+'-result.json');payload={**payload,'report_path':str(out)}
    atomic_json(path,payload)
    home=job/'isolated';home.mkdir(exist_ok=True)
    env=os.environ.copy();env.update({'HOME':str(home),'XDG_CONFIG_HOME':str(home/'config'),'XDG_CACHE_HOME':str(home/'cache'),'BLENDER_USER_CONFIG':str(home/'blender/config'),'BLENDER_USER_SCRIPTS':str(home/'blender/scripts'),'BLENDER_USER_EXTENSIONS':str(home/'blender/extensions'),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPYCACHEPREFIX':str(home/('unused-bytecode-'+uuid.uuid4().hex)),'OMP_NUM_THREADS':'2'})
    threads=int(payload.get('request',{}).get('params',{}).get('budgets',{}).get('cpu_threads',payload.get('request',{}).get('params',{}).get('cpu_threads',2)))
    env['OMP_NUM_THREADS']=str(threads)
    cmd=[str(blender),'--background','--factory-startup','--disable-autoexec','--threads',str(threads),'--python',str(ROOT/'blender_worker.py'),'--','--payload',str(path)]
    process=OwnedProcess(cmd,cwd=ROOT,budget=budget,cancel_check=(lambda:is_cancelled(store,job.name)) if check_cancel else None,env=env,stdout_path=job/(label+'.log'),stderr_path=job/(label+'-stderr.log'),disk_root=job)
    result=process.run();atomic_json(job/(label+'-process.json'),result)
    if result.get('status')!='succeeded' or result.get('returncode')!=0:
        failed=load(out) if out.is_file() else {}
        e=failed.get('error') or result.get('error') or {}
        raise RuntimeFailure('CANCELLED' if result.get('status')=='cancelled' else 'TIMEOUT' if result.get('status')=='timed_out' else e.get('code','WORKER_FAILED'),e.get('message',f'Blender {label} failed'),process=descriptor(job/(label+'-process.json')),worker=failed)
    if not out.is_file():raise RuntimeFailure('WORKER_FAILED',f'Blender {label} did not write report',process=descriptor(job/(label+'-process.json')))
    data=load(out)
    if data.get('ok') is False or data.get('status') in ('failed','fail') or data.get('error'):
        e=data.get('error') or {};raise RuntimeFailure(e.get('code','VALIDATION_FAILED'),e.get('message',f'{label} failed'),worker=data)
    return data

def prepare_resume_inputs(cp,job,budget,store,blender,guards):
    from .io import copy_verified
    destination=job/'resume-inputs';destination.mkdir()
    artifacts=cp['artifacts'];copied=[];mapping=[]
    for role,value in artifacts.items():
        if role!='candidate' and not role.startswith('resource_'):continue
        source={'file':value['file'],'expected_sha256':value['sha256'],'bytes':value['bytes']}
        target=destination/('checkpoint.blend' if role=='candidate' else role+'_'+Path(value['file']).name)
        item,obs=copy_verified(source,target,limit_bytes=budget.limits.max_disk_bytes);copied.append(item)
        if role=='candidate':candidate=item
        else:mapping.append({'source':value['file'],'snapshot':item['file'],'sha256':item['sha256']})
    guards.enter_context(ProtectedInputs(copied))
    rebased=destination/'checkpoint-rebased.blend'
    worker({'action':'stage','source':candidate['file'],'original_source_file':artifacts['candidate']['file'],'candidate_path':str(rebased),'resources':mapping,'job_dir':str(job)},job,budget,store,blender,'resume-stage')
    rebased_record=descriptor(rebased);guards.enter_context(ProtectedInputs([rebased_record]))
    return {'candidate':rebased_record,'resources':[r for r in copied if r['file']!=candidate['file']],'copied_accepted_checkpoint':cp['receipt'],'guarded_during_consumption':True}


def technical_plan(plan,strategy,*,quality=None,budgets=None,solutions=None):
    """Whitelist execution-only changes; design state and acceptance unchanged."""
    result=copy.deepcopy(plan)
    if strategy=='declared':return result
    if strategy=='tessellation_refine':
        if quality is None or budgets is None:raise RuntimeFailure('INVALID_TECHNICAL_STRATEGY','Refinement requires unchanged explicit quality and budgets')
        tolerance=quality['length_tolerance']*.5
        maximum=budgets['max_geometry_vertices'];estimated=0;changed=0
        for step in result['steps']:
            op=step['op'];p=step['effective_params']
            if op=='primitive.cylinder':radius=p['radius'];factor=2
            elif op=='profile.revolve':
                ref=p['profile']
                if ref['kind']=='sketch_profile':sid,pid=ref['sketch_id'],ref['profile_id']
                else:
                    producer=next(x for x in result['steps'] if x['feature_id']==step['feature_id'] and x['id']==ref['step_id']);sid,pid=producer['sketch_id'],ref['port']
                profile=(solutions or {}).get(sid,{}).get('profiles',{}).get(pid)
                if not profile:raise RuntimeFailure('SKETCH_NOT_ACCEPTED','Refinement requires accepted analytic profiles')
                radius=max(abs(point[0]) for loop in profile['loops'] for point in loop)*.001/result['length_scale'];factor=profile['vertex_count']
            else:continue
            from .budgets import estimate_arc_segments
            sweep=math.tau if op=='primitive.cylinder' else math.radians(abs(p.get('angle',360)))
            count=max(p.get('segments',64),estimate_arc_segments(radius,sweep,tolerance,max_segments=4096))
            p['segments']=count;estimated+=(count+1)*factor;changed+=1
        profile_steps=[step for step in result['steps'] if step['op'] in ('profile.extrude','profile.revolve')]
        if profile_steps:
            chord=min([step['effective_params'].get('chord_tolerance',.01) for step in profile_steps]+[tolerance])
            result['execution_chord_tolerance_mm']=chord*result['length_scale']*1000;changed+=1
        if not changed:raise RuntimeFailure('INEFFECTIVE_STRATEGY','No qualified curve sampling parameter can be refined')
        if estimated>maximum:raise RuntimeFailure('BUDGET_EXCEEDED','Refined primitive/profile count exceeds geometry budget before allocation',vertices=estimated,limit=maximum)
        result['execution_strategy']=strategy;result['refined_geometry_estimate']=estimated;return result
    if strategy not in ('boolean_exact','boolean_manifold'):
        raise RuntimeFailure('UNSUPPORTED_TECHNICAL_STRATEGY',f'Strategy {strategy} is not runtime-qualified')
    count=0
    for step in result['steps']:
        if step['op']=='boolean.apply_or_stack':
            step['effective_params']['solver']='EXACT' if strategy=='boolean_exact' else 'MANIFOLD';count+=1
    if not count:raise RuntimeFailure('INEFFECTIVE_STRATEGY','Requested Boolean strategy has no Boolean operation')
    result['execution_strategy']=strategy
    return result

def refine_solution_profiles(solutions,chord_tolerance_mm,max_vertices,budget):
    from .sketch import tessellate_profile
    refined=copy.deepcopy(solutions);remaining=max_vertices
    for result in refined.values():
        if not result.get('accepted'):raise RuntimeFailure('SKETCH_NOT_ACCEPTED','Only accepted analytic solutions can be re-tessellated')
        for profile in result['solved_sketch'].get('profiles',[]):
            budget.check()
            data=tessellate_profile(result['solved_sketch'],profile,chord_tolerance_mm,remaining,result['solved_sketch'].get('solve',{}).get('verification',{}).get('length_tolerance_mm',.001),budget_check=budget.check)
            remaining-=data['vertex_count'];result['profiles'][profile['id']]=data
    return refined


def test_fault(request,phase):
    """Closed, fixture-only fault injection; cannot run code or change design."""
    allowed={'after_checkpoint','after_build_save','after_reopen','before_accept','after_accept','before_receipt','after_receipt'}
    selected=os.environ.get('HS_TEST_FAULT')
    if selected and selected not in allowed:raise RuntimeFailure('INVALID_TEST_FAULT','Unknown fixture fault phase')
    if selected==phase:
        if request['params']['purpose']!='contract_fixture':raise RuntimeFailure('TEST_FAULT_PRODUCTION_REFUSED','Fault injection is fixture-only')
        if phase=='after_receipt':os._exit(72)
        raise RuntimeFailure('TEST_INJECTED_FAILURE','Controlled fixture fault at '+phase,phase=phase)


def execute_attempt(payload,job,budget,store,blender,number,fp,resources,held_guards,source_guard,staged_guard,report):
    """Explicit checkpoints split Blender execution into verified serial segments."""
    from .recovery import CheckpointStore
    base=Path(payload['job_dir']);skip=set(payload.get('start_after_step_keys',[]))
    boundaries=[step['step_key'] for step in payload['plan']['steps'] if step['op']=='checkpoint' and step['step_key'] not in skip]+[None]
    current_source=payload.get('source');completed=list(payload.get('start_after_step_keys',[]))
    for segment,stop in enumerate(boundaries,1):
        if is_cancelled(store,job.name):raise RuntimeFailure('CANCELLED','Job cancelled at verified checkpoint boundary')
        directory=base/f'segment-{segment:02d}';directory.mkdir()
        part={**payload,'job_dir':str(directory),'source':current_source,'start_after_step_keys':completed}
        if stop:part['stop_after_step_key']=stop
        label=f'build-{number:02d}-{segment:02d}'
        core=worker(part,job,budget,store,blender,label)
        if stop is None:return core
        candidate=core['candidate'];state=core['design_state']
        checkpoint_artifacts=checkpoint_evidence_artifacts(core,root=job)
        held_guards.enter_context(ProtectedInputs([candidate,state,*checkpoint_artifacts.values()]))
        verification_label=f'checkpoint-{number:02d}-{segment:02d}-reopen'
        verification=worker({'action':'verify','source':candidate['file'],'expected':core,'job_dir':str(job)},job,budget,store,blender,verification_label)
        source_guard.check();staged_guard.check();budget.check()
        if impl_identity(blender)!=fp['implementation']:raise RuntimeFailure('IMPLEMENTATION_CHANGED','Runtime identity changed before intermediate checkpoint acceptance')
        from .reference import verify_reference
        if verify_reference(payload['request']['params'],saved_reference(job))!=fp['reference']['approval']:raise RuntimeFailure('REFERENCE_CONFLICT','Reference changed before intermediate checkpoint acceptance')
        artifacts={'candidate':candidate,'design_state':state,**checkpoint_artifacts,**{f'resource_{i:03d}':r for i,r in enumerate(resources)}}
        proof={'independent_reopen':verification.get('independent_reopen'),'candidate':candidate,'checks':verification.get('checks',{}),'evidence':descriptor(job/(verification_label+'-result.json'))}
        accepted=CheckpointStore(job).accept(f'attempt-{number:02d}-step-{segment:02d}',artifacts,fp,proof,completed_steps=core['completed_step_keys'])
        report['checkpoints'].append(accepted)
        atomic_json(job/'progress.json',{'phase':'checkpoint_accepted','checkpoint_id':accepted['checkpoint_id'],'completed_steps':core['completed_step_keys'],'time':time.time()})
        test_fault(payload['request'],'after_checkpoint')
        current_source=candidate['file'];completed=core['completed_step_keys']
    raise RuntimeFailure('INTERNAL_ERROR','Segmented attempt had no final segment')


def default_wire_diagnostics(request,core,candidate,job,budget,store,blender,resources,report,available_artifact_slots):
    """Default saved-candidate topology diagnostics share the modeling job budget."""
    p=request['params'];cfg=p['wire']
    if not cfg['enabled']:
        report['topology_diagnostics']={'status':'disabled_by_request','mesh_state':cfg['mesh_state'],'components':0,'views':[]}
        return {}
    from .contract import WIRE_STYLE
    from .budgets import tree_file_bytes
    objects=sorted([o for o in core['scene_snapshot']['objects'] if o.get('generated') and o.get('feature_id') and o['type']=='MESH' and not o['hide_render']],key=lambda o:(o['feature_id'],o['object_id']))
    if not objects:raise RuntimeFailure('TOPOLOGY_FEATURE_ID','No final generated components available for default wire diagnostics')
    object_ids=[o['object_id'] for o in objects]
    if any(not oid for oid in object_ids) or len(set(object_ids))!=len(object_ids):raise RuntimeFailure('TOPOLOGY_OBJECT_ID','Every diagnostic instance needs a unique stable object ID')
    batches=[[o] for o in objects] if cfg['component_views'] else [objects]
    if len(batches)>16:raise RuntimeFailure('BUDGET_EXCEEDED','Default wire diagnostics exceed 16 component views',views=len(batches))
    other=p.get('preview');beauty_samples=other['width']*other['height']*other['samples']*len(other['views']) if other else 0
    samples=cfg['width']*cfg['height']*32*len(batches)+beauty_samples
    if samples>p['budgets']['max_preview_samples']:raise RuntimeFailure('BUDGET_EXCEEDED','Actual diagnostic and preview sample count exceeds shared request budget',samples=samples,limit=p['budgets']['max_preview_samples'])
    required_artifacts=1+len(objects)*2+len(batches)*2
    if required_artifacts>available_artifact_slots:raise RuntimeFailure('BUDGET_EXCEEDED','Topology diagnostics exceed accepted-checkpoint artifact capacity',required=required_artifacts,available=available_artifact_slots)
    directory=job/'topology-diagnostics';directory.mkdir()
    source={'file':candidate['file'],'expected_sha256':candidate['sha256'],'bytes':candidate['bytes']}
    threads=min(4,p['budgets']['cpu_threads']);wall=min(600,p['budgets']['wall_seconds'])
    topology_request={'schema_version':'1.0','command':'hardsurface.topology','params':{'request_id':derived_request_id(p['request_id'],'topology'),'source':source,'object_ids':object_ids,'mesh_states':['control','evaluated'],'export_geometry':False,'cpu_threads':threads,'wall_seconds':wall}}
    data=worker({'action':'topology','source':candidate['file'],'request':topology_request,'job_dir':str(directory),'guarded_resources':resources},job,budget,store,blender,'topology-default')
    statistics=descriptor(directory/'topology-default-result.json')
    entry={'status':'not_run','mesh_state':cfg['mesh_state'],'components':len(objects),'statistics':statistics,'views':[]}
    report['topology_diagnostics']=entry
    artifacts={'topology_statistics':statistics}
    for index,item in enumerate(data.get('outputs',[])):artifacts['topology_detail_'+str(index)]={k:item[k] for k in ('file','sha256','bytes')}
    for index,selected in enumerate(batches,1):
        label='wire-default-'+str(index).zfill(3)
        safe_name=('assembly' if len(selected)>1 else ''.join(ch if ch.isalnum() or ch in '_-' else '_' for ch in selected[0]['feature_id']))[:30]
        view={'name':safe_name+'-'+str(index),'direction':'three_quarter','visible_object_ids':[o['object_id'] for o in selected],'mesh_state':cfg['mesh_state'],'width':cfg['width'],'height':cfg['height']}
        observation={'schema_version':'1.0','command':'hardsurface.observe','params':{'request_id':derived_request_id(p['request_id'],label),'source':source,'wire':{k:cfg[k] for k in WIRE_STYLE['properties']},'views':[view],'cpu_threads':threads,'wall_seconds':wall}}
        rendered=worker({'action':'observe','source':candidate['file'],'request':observation,'job_dir':str(directory),'guarded_resources':resources},job,budget,store,blender,label)
        for image in rendered['previews']:
            item={k:image[k] for k in ('file','sha256','bytes')};entry['views'].append(item);artifacts['topology_wire_'+str(index)]=item
        artifacts['topology_wire_report_'+str(index)]=descriptor(directory/(label+'-result.json'))
        budget.check(disk_bytes=tree_file_bytes(job))
    entry['status']='pass';return artifacts


def evaluate_required(plan,core,solved,verification,report):
    """Required acceptance is explicit evidence, never a generic success bit."""
    if 'required_checks' not in plan:raise RuntimeFailure('MISSING_REQUIRED_CHECKS','Planner omitted required acceptance registry')
    evidence={};core_checks=core.get('checks',{})
    for item in plan['required_checks']:
        name=item['id'];producer=item['producer']
        if not item['applicable']:
            if name!='source_preserved':raise RuntimeFailure('CHECK_NOT_APPLICABLE','Unqualified not-applicable required check',check=name)
            evidence[name]={'status':'not_applicable','reason':item['reason']};continue
        if producer=='core':value=core_checks.get(name,{'status':'not_run'})
        elif producer=='solver':
            relevant={step.get('sketch_id') for step in plan['steps'] if step['op']=='sketch.solve'}
            relevant|={step['profile']['sketch_id'] for step in plan['steps'] if isinstance(step.get('profile'),dict) and step['profile'].get('kind')=='sketch_profile'}
            rows=[solved.get(sid,{}) for sid in sorted(relevant)]
            good=bool(rows) and all(row.get('accepted') and row.get('verification',{}).get('status')=='pass' for row in rows)
            if name=='profile_validity':good=good and all(row.get('profiles') for row in rows)
            value={'status':'pass' if good else 'fail','sketch_ids':sorted(relevant),'evidence':'accepted native solver and independent residual/profile reports'}
        elif producer=='host':
            good=(name=='reopen' and verification.get('independent_reopen') is True and all(verification.get('checks',{}).get(k)=='pass' for k in ('technical','preservation','dependency_reproduction','state_consistency'))) or (name=='dependencies' and verification.get('checks',{}).get('dependency_reproduction')=='pass') or (name=='source_preserved' and bool(report.get('original_final_observations')) and all(report['source_protection'][k].get('accepted') for k in ('staged_snapshot_guarded','resources_guarded')))
            value={'status':'pass' if good else 'fail','evidence':'independent reopen, dependency closure, original observations and completed guard interval'}
        else:value={'status':'not_run','reason':'External visual/user evidence is not generated by worker'}
        if isinstance(value,str):value={'status':value}
        evidence[name]=value
        if value.get('status')!='pass':raise RuntimeFailure('REQUIRED_CHECK_FAILED','A required acceptance check has no passing evidence',check=name,evidence=value)
    return evidence


def make_budget(params,started):
    from .budgets import BudgetLimits,JobBudget
    b=params['budgets'];return JobBudget(BudgetLimits(wall_seconds=b.get('wall_seconds',300),max_disk_bytes=b.get('max_artifact_bytes',1024**3),max_tree_rss_bytes=b.get('max_observed_rss_bytes',2*1024**3),max_steps=b.get('max_total_steps',128),max_vertices=b.get('max_geometry_vertices',200000),max_loops=b.get('max_geometry_loops',1000000),max_instances=b.get('max_instances',128),max_curve_segments=b.get('max_tessellation_vertices',200000)),started_at=started)

def is_cancelled(store,job_id):
    parent=getattr(store,'parent_cancel_id',None)
    return store.status(job_id).get('cancel_requested',False) or bool(parent and store.status(parent).get('cancel_requested',False))

class AggregateBudget:
    """One study budget across serial owned case jobs, including all retained artifacts."""
    def __init__(self,budget,roots):self.budget=budget;self.roots=list(roots)
    def add_root(self,root):
        if root not in self.roots:self.roots.append(root)
    def __getattr__(self,name):return getattr(self.budget,name)
    def check(self,**observations):
        from .budgets import tree_file_bytes
        observations['disk_bytes']=sum(tree_file_bytes(root) for root in self.roots)
        return self.budget.check(**observations)
    def report(self):return {**self.budget.report(),'aggregate_owned_job_roots':[str(x) for x in self.roots]}

def run_job(root,job_id,blender,started=None,resume_checkpoint=None,shared_budget=None,parent_cancel_id=None):
    store=store_for(root);job=store.job_dir(job_id)
    fd=os.open(job/'execution.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeFailure('JOB_ALREADY_RUNNING','Another supervisor owns this exact job') from exc
        if store.status(job_id)['status']!='queued':raise RuntimeFailure('JOB_NOT_QUEUED','Only a queued job may start; recovery requires a new exact-checkpoint job')
        return _run_job_impl(root,job_id,blender,started,resume_checkpoint,shared_budget,parent_cancel_id)
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def _run_job_impl(root,job_id,blender,started=None,resume_checkpoint=None,shared_budget=None,parent_cancel_id=None):
    from .contract import validate_request,fingerprint
    from .planner import plan_request
    store=store_for(root);store.parent_cancel_id=parent_cancel_id;job=store.job_dir(job_id);request=validate_request(load(job/'request.json'));p=request['params']
    started=store.status(job_id).get('metadata',{}).get('admitted_monotonic',started or time.monotonic());budget=shared_budget or make_budget(p,started)
    report={'report_version':'1.0','job_id':job_id,'request_id':store.status(job_id)['request_id'],'original_request_id':p['request_id'],'status':'running','domain_outcome':'failed','candidate':None,'checkpoints':[],'warnings':[],'acceptance':{k:{'status':'not_run'} for k in ('technical','preservation','dependency_reproduction','performance','visual','user_feedback','method_acceptance')}}
    store.update(job_id,status='running',supervisor_pid=os.getpid());atomic_json(job/'progress.json',{'phase':'preflight','time':time.time()})
    try:
        budget.check()
        identity=impl_identity(blender);atomic_json(job/'implementation.json',identity)
        approval=saved_reference(job)
        stage=stage_inputs(p,job/'stage',disk_budget=p['budgets'].get('max_artifact_bytes',1024**3))
        guarded=[*stage['resources']]+([stage['source']] if stage['source'] else [])
        if approval:guarded.append(verify_descriptor(approval))
        source_path=None;saved_state=None;saved_solutions={}
        with ProtectedInputs(guarded) as outer:
            if stage['source']:
                staged=job/'stage'/'source_rebased.blend'
                worker({'action':'stage','source':stage['source']['file'],'candidate_path':str(staged),'original_source_file':p['source']['file'],'job_dir':str(job),'resources':[{'source':m['original'],'snapshot':m['staged'],'sha256':r['sha256']} for m,r in zip(stage['mapping'],stage['resources'])]},job,budget,store,blender,'stage')
                source_path=str(staged)
            consumed=[descriptor(source_path)] if source_path else []
            with ProtectedInputs(consumed) as inner, contextlib.ExitStack() as recovery_guards:
                if source_path:
                    inspection=worker({'action':'inspect','source':source_path,'job_dir':str(job)},job,budget,store,blender,'inspect')
                    saved_state=inspection.get('design_state',inspection.get('scene_snapshot',{}).get('design_state'))
                    saved_solutions=inspection.get('saved_solutions',inspection.get('scene_snapshot',{}).get('saved_solutions',{}))
                plan=plan_request(request,saved_state=saved_state,reference_approval=approval);atomic_json(job/'plan.json',plan)
                fp={'request':fingerprint(request),'plan':{'base':plan['plan_sha256'],'strategy':(resume_checkpoint['fingerprints']['plan']['strategy'] if resume_checkpoint else p.get('execution',{}).get('technical_strategies',['declared'])[0])},'implementation':identity,'source':p['source'],'resources':p['resources'],'design':plan['design_sha256'],'reference':{'package':p.get('reference_package',{'status':'not_applicable_fixture'}),'approval':plan['reference_gate']},'context':p['context']}
                solved={}
                state=plan['resolved_design_state']
                fp.update({'operation_versions':{x['op']:'1.0' for x in plan['steps']},'solver':{'status':'not_applicable_no_sketches'},'targets':{'source_state':saved_state},'protection':p['protection']})
                if state.get('sketches'):
                    from .solver import solve_sketch
                    atomic_json(job/'progress.json',{'phase':'solve','time':time.time()})
                    for sketch in state['sketches']:
                        budget.check()
                        deployment=load(ROOT/'dependencies/deployment.json') if (ROOT/'dependencies/deployment.json').is_file() else {}
                        deployment=dict(deployment)
                        remaining_points=p['budgets']['max_tessellation_vertices']-sum(profile.get('vertex_count',0) for answer in solved.values() for profile in answer.get('profiles',{}).values())
                        if remaining_points<3:raise RuntimeFailure('BUDGET_EXCEEDED','Aggregate solved profile tessellation budget exhausted before allocation')
                        deployment['max_vertices']=min(deployment.get('max_vertices',200000),remaining_points,p['budgets']['max_geometry_vertices'])
                        chord=[step['effective_params'].get('chord_tolerance',.01)*plan['length_scale']*1000 for step in plan['steps'] if step['op'] in ('profile.extrude','profile.revolve')]
                        if chord:deployment['chord_tolerance_mm']=min(chord)
                        solved[sketch['id']]=solve_sketch(sketch,plan['dimension_values'],length_unit=p['source']['length_unit'],config=deployment,last_good=(saved_solutions.get(sketch['id'],{}).get('solved_sketch') if saved_solutions.get(sketch['id'],{}).get('accepted') else None),request_id=p['request_id']+':'+sketch['id'],job_id=job_id,job_dir=job/'solver'/sketch['id'],budget=budget,cancel_check=lambda:is_cancelled(store,job_id))
                        if not solved[sketch['id']].get('accepted'):raise RuntimeFailure('SOLVER_REJECTED','Sketch solve not accepted',sketch=sketch['id'],result=solved[sketch['id']])
                        fp['solver']={'deployment':deployment,'solution_identity':{k:v.get('backend_identity',v.get('backend',{})) for k,v in solved.items()}}
                    atomic_json(job/'solutions.json',solved)
                payload={'action':'run','request':request,'plan':plan,'solutions':solved,'job_dir':str(job),'source':source_path,'base_design_state':saved_state,'reference_approval':approval}
                if resume_checkpoint:
                    from .recovery import fingerprint_digest
                    if fingerprint_digest(fp)!=resume_checkpoint['fingerprints_sha256']:raise RuntimeFailure('RESUME_FINGERPRINT_MISMATCH','Resume input, plan, implementation or context changed')
                    recovered=prepare_resume_inputs(resume_checkpoint,job,budget,store,blender,recovery_guards)
                    payload['source']=recovered['candidate']['file'];payload['start_after_step_keys']=resume_checkpoint.get('completed_steps',[])
                    report['resume_inputs']=recovered
                    stage['resources'].extend(recovered['resources'])
                    report['resumed_from']={'job_id':resume_checkpoint['job_id'],'checkpoint_id':resume_checkpoint['checkpoint_id']}
                atomic_json(job/'progress.json',{'phase':'build','time':time.time()})
                attempts=[];core=None
                strategies=[resume_checkpoint['fingerprints']['plan']['strategy']] if resume_checkpoint else p.get('execution',{}).get('technical_strategies',['declared'])
                allowed_attempts=1 if resume_checkpoint else p.get('execution',{}).get('max_attempts',1)
                for attempt_index,strategy in enumerate(strategies[:allowed_attempts]):
                    attempt_dir=job/f'attempt-{attempt_index+1:02d}';attempt_dir.mkdir()
                    attempt_payload=copy.deepcopy(payload);attempt_payload['job_dir']=str(attempt_dir)
                    attempt_payload['plan']=technical_plan(plan,strategy,quality=p['quality'],budgets=p['budgets'],solutions=solved)
                    if 'execution_chord_tolerance_mm' in attempt_payload['plan']:
                        attempt_payload['solutions']=refine_solution_profiles(solved,attempt_payload['plan']['execution_chord_tolerance_mm'],p['budgets']['max_tessellation_vertices'],budget)
                    fp['plan']={'base':plan['plan_sha256'],'strategy':strategy}
                    invariant={'design':fingerprint(plan['resolved_design_state']),'reference':fingerprint(p.get('reference_package')),'quality':fingerprint(p['quality'])}
                    try:
                        core=execute_attempt(attempt_payload,job,budget,store,blender,attempt_index+1,fp,stage['resources'],recovery_guards,outer,inner,report)
                        attempts.append({'strategy':strategy,'status':'pass','invariants':invariant});report['attempts']=attempts;break
                    except RuntimeFailure as exc:
                        attempts.append({'strategy':strategy,'status':'fail','error':error_dict(exc),'invariants':invariant})
                        atomic_json(job/'attempts.json',attempts);report['attempts']=attempts
                        if exc.code not in {'BOOLEAN_EMPTY_RESULT','BOOLEAN_NO_EFFECT','BOOLEAN_FAILED','BOOLEAN_NO_OP','BOOLEAN_DIRECTION','PROFILE_TRIANGULATION','BEVEL_CLAMPED','BEVEL_CLAMP','BEVEL_NO_OP','GEOMETRY_INVALID','TESSELLATION_TOLERANCE','CURVE_TOLERANCE','CURVE_TOLERANCE_FAILED'}:raise
                        if attempt_index+1>=allowed_attempts:raise
                if core is None:raise RuntimeFailure('WORKER_FAILED','No successful attempt')
                report['attempts']=attempts;atomic_json(job/'core-report.json',core)
                test_fault(request,'after_build_save')
                cand=core.get('candidate')
                if isinstance(cand,str):cand=descriptor(cand)
                if not cand:cand=descriptor(attempt_dir/'candidate.blend')
                verify_descriptor(cand,expected=False)
                checkpoint_artifacts=checkpoint_evidence_artifacts(core,root=job)
                recovery_guards.enter_context(ProtectedInputs([cand,core['design_state'],*checkpoint_artifacts.values()]))
                verification=worker({'action':'verify','source':cand['file'],'expected':core,'job_dir':str(job)},job,budget,store,blender,'reopen')
                test_fault(request,'after_reopen')
                # Independent saved candidate verification is required before checkpoint acceptance.
                from .recovery import CheckpointStore
                artifacts={'candidate':cand,'design_state':core['design_state'],**checkpoint_artifacts}
                artifacts.update({f'resource_{index:03d}':item for index,item in enumerate(stage['resources'])})
                proof={'independent_reopen':verification.get('independent_reopen'),'candidate':cand,'checks':verification.get('checks',{}),'evidence':descriptor(job/'reopen-result.json')}
                # Acceptance is deferred until source/resource guards have closed successfully.
                pending_checkpoint=(artifacts,fp,proof)
                if p.get('preview'):
                    report['preview']=worker({'action':'preview','source':cand['file'],'request':request,'job_dir':str(job)},job,budget,store,blender,'preview')
                artifacts.update(default_wire_diagnostics(request,core,cand,job,budget,store,blender,stage['resources'],report,256-len(artifacts)))
                outer.check();inner.check();
                from .budgets import tree_file_bytes
                budget.check(disk_bytes=tree_file_bytes(job))
                report['original_final_observations']=recheck_originals(p,stage['original_observations'])
                report['candidate']={**cand,'state':'verified_candidate'}
                report['acceptance'].update({k:{'status':'pass','evidence':str(job/'reopen-result.json')} for k in ('technical','preservation','dependency_reproduction')})
                report['counts']=core.get('counts',{'steps':len(plan['steps'])})
                report['core_report']=descriptor(job/'core-report.json')
                report['fingerprints']=fp
            report['source_protection']={'original_source_observed':stage['original_source_observed'],'staged_snapshot_guarded':inner.report(),'resources_guarded':outer.report()}
        # final guard.close has now validated both scopes
        report['source_protection']['resources_guarded']=outer.report()
        if impl_identity(blender)!=identity:raise RuntimeFailure('IMPLEMENTATION_CHANGED','Runtime source or executable identity changed during execution')
        from .reference import verify_reference
        if verify_reference(p,saved_reference(job))!=plan['reference_gate']:raise RuntimeFailure('REFERENCE_CONFLICT','Reference approval changed during execution')
        report['reference_gate']=plan['reference_gate']
        report['required_checks']=evaluate_required(plan,core,solved,verification,report)
        from .budgets import tree_file_bytes
        budget.check(disk_bytes=tree_file_bytes(job))
        test_fault(request,'before_accept')
        checkpoint=CheckpointStore(job).accept('final',*pending_checkpoint,completed_steps=[step['step_key'] for step in plan['steps']])
        report['checkpoints'].append(checkpoint)
        test_fault(request,'after_accept')
        report['status']='succeeded';report['domain_outcome']='pass'
        report['acceptance']['performance']={'status':'pass','evidence':'Aggregate observed budget not exceeded; memory sampling is not hard isolation'}
        if p['purpose']=='contract_fixture':
            report['acceptance']['visual']={'status':'not_applicable','reason':'Numerical contract fixture; no practical visual acceptance'}
            report['acceptance']['user_feedback']={'status':'not_applicable','reason':'Numerical contract fixture'}
            report['candidate']['state']='usable_delivery'
        report['next_step']=('Review numerical fixture evidence; this fixture has no production visual acceptance' if p['purpose']=='contract_fixture' else 'Review the verified production candidate against the approved reference; visual, user feedback and method acceptance remain pending')
        test_fault(request,'before_receipt')
    except BaseException as exc:
        e=error_dict(exc);report['error']=e;report['traceback']=traceback.format_exc()
        if report.get('topology_diagnostics',{}).get('status')=='not_run':report['topology_diagnostics']['status']='failed'
        code=e['code'];wall_expired=e.get('details',{}).get('resource')=='wall_seconds'
        report['status']='cancelled' if 'CANCEL' in code else 'timed_out' if 'TIME' in code or wall_expired else 'failed'
        report['domain_outcome']='failed';report['candidate']=None
        report['unaccepted_leftovers']=[str(x.relative_to(job)) for x in job.rglob('*.blend')]
        report['next_step']='Inspect failure evidence; do not consume unaccepted candidates or retry unchanged requests'
    report['metrics']=budget.report()
    from .budgets import tree_file_bytes
    observed_bytes=tree_file_bytes(job);estimated_final=observed_bytes+len(json.dumps(report,ensure_ascii=False,allow_nan=False).encode())+16384
    if report['status']=='succeeded' and estimated_final>budget.limits.max_disk_bytes:
        report.update(status='failed',domain_outcome='failed',candidate=None,error={'code':'RESOURCE_LIMIT','detail_code':'BUDGET_EXCEEDED','message':'Final report and receipt reservation exceeds aggregate artifact budget','details':{'observed_bytes':observed_bytes,'estimated_final_bytes':estimated_final,'limit':budget.limits.max_disk_bytes},'retry_class':'new_budget_required'})
        report['acceptance']['performance']={'status':'fail','reason':'Final artifact reservation exceeds budget'}
    atomic_json(job/'report.json',report);report['report']=descriptor(job/'report.json')
    store.update(job_id,status=report['status'],domain_outcome=report['domain_outcome'],result=report['report'],finished_at=time.time())
    atomic_json(job/'result.json',compact(report))
    test_fault(request,'after_receipt')
    return report

def submit(request_path,root,blender,background=False,started=None,shared_budget=None,parent_cancel_id=None,reference_approval=None):
    from .contract import validate_request,fingerprint
    request=validate_request(load(request_path));store=store_for(root)
    from .reference import verify_reference
    gate=verify_reference(request['params'],reference_approval)
    admission=fingerprint(request) if reference_approval is None else fingerprint({'request':request,'reference_approval':gate})
    record=store.submit(request['params']['request_id'],admission,metadata={'admitted_monotonic':started or time.monotonic()});job=store.job_dir(record['job_id'])
    if shared_budget is not None:shared_budget.add_root(job)
    if record.get('reused'):
        if (job/'result.json').exists():return load(job/'result.json')
        return record
    atomic_json(job/'request.json',request)
    persist_reference(job,reference_approval)
    if background:
        cache_prefix=str(job/('unused-bytecode-'+uuid.uuid4().hex))
        cmd=[sys.executable,'-B','-S','-P','-X','pycache_prefix='+cache_prefix,'-m','hardsurface.host','--jobs-dir',str(root),'--blender',str(blender),'_supervise',record['job_id']]
        with open(job/'supervisor.log','xb') as log:
            process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPYCACHEPREFIX':cache_prefix})
        store.update(record['job_id'],supervisor_pid=process.pid)
        return {'job_id':record['job_id'],'request_id':request['params']['request_id'],'status':'queued','accepted':True}
    return compact(run_job(root,record['job_id'],blender,started=started,shared_budget=shared_budget,parent_cancel_id=parent_cancel_id))

def archive(root,job_id,destination,blender):
    store=store_for(root);status=store.status(job_id)
    if status.get('status')!='succeeded':raise RuntimeFailure('ARCHIVE_NOT_ACCEPTED','Only a successful verified job may be archived')
    job=store.job_dir(job_id);report=load(job/'report.json')
    if report.get('domain_outcome')!='pass' or not report.get('candidate'):raise RuntimeFailure('ARCHIVE_NOT_ACCEPTED','No accepted candidate')
    dest=checked_path(destination,exists=False)
    if dest.exists():raise RuntimeFailure('PATH_CONFLICT','Archive destination must be new')
    manifest=tree_manifest(job);needed=sum(x['bytes'] for x in manifest)
    if needed>shutil.disk_usage(dest.parent).free:raise RuntimeFailure('BUDGET_EXCEEDED','Insufficient archive space')
    dest.mkdir();
    for row in manifest:
        target=dest/row['relative_path'];target.parent.mkdir(parents=True,exist_ok=True)
        with open(job/row['relative_path'],'rb') as a,open(target,'xb') as b:shutil.copyfileobj(a,b)
        if digest(target)!=row['sha256']:raise RuntimeFailure('ARCHIVE_COPY_MISMATCH','Copy changed',file=str(target))
    atomic_json(dest/'archive-manifest.json',{'origin_job':str(job),'files':manifest,'historical_absolute_metadata_preserved':True})
    # Source layout must already use relative native dependency paths from stage.
    budget=make_budget(load(job/'request.json')['params'],time.monotonic())
    candidate=dest/Path(report['candidate']['file']).relative_to(job)
    result=worker({'action':'verify','source':str(candidate),'expected':load(dest/'core-report.json'),'dependency_root':str(dest),'job_dir':str(dest)},dest,budget,store,blender,'archive-reopen',check_cancel=False)
    atomic_json(dest/'archive-verification.json',result)
    return {'status':'pass','archive':str(dest),'candidate':descriptor(candidate),'manifest':descriptor(dest/'archive-manifest.json'),'independent_reopen':descriptor(dest/'archive-verification.json'),'source_job_retained':True}

def stage_only(request_path,root,blender,started):
    from .contract import validate_request,fingerprint
    request=validate_request(load(request_path));p=request['params'];store=store_for(root)
    record=store.submit(derived_request_id(p['request_id'],'stage'),fingerprint({'stage':request}),metadata={'admitted_monotonic':started})
    job=store.job_dir(record['job_id'])
    if record.get('reused'):
        return load(job/'result.json') if (job/'result.json').exists() else record
    atomic_json(job/'request.json',request);store.update(job.name,status='running');budget=make_budget(p,started)
    try:
        stage=stage_inputs(p,job/'stage',disk_budget=p['budgets']['max_artifact_bytes'])
        if stage['source']:
            consumed=stage['resources']+[stage['source']]
            with ProtectedInputs(consumed) as guards:
                target=job/'stage'/'source_rebased.blend'
                worker({'action':'stage','source':stage['source']['file'],'candidate_path':str(target),'original_source_file':p['source']['file'],'job_dir':str(job),'resources':[{'source':m['original'],'snapshot':m['staged'],'sha256':r['sha256']} for m,r in zip(stage['mapping'],stage['resources'])]},job,budget,store,blender,'stage')
                guards.check();output=copy.deepcopy(request);actual=descriptor(target)
                output['params']['source'].update(file=actual['file'],expected_sha256=actual['sha256'],bytes=actual['bytes'])
                for item,staged in zip(output['params']['resources'],stage['resources']):item.update(file=staged['file'],expected_sha256=staged['sha256'],bytes=staged['bytes'])
                recheck_originals(p,stage['original_observations'])
        else:output=copy.deepcopy(request)
        ref=atomic_json(job/'staged-request.json',output)
        result={'status':'succeeded','job_id':job.name,'request':ref,'source_protection':stage,'purpose':'Staging only, no geometric candidate acceptance'}
    except Exception as exc:result={'status':'failed','job_id':job.name,'error':error_dict(exc)}
    atomic_json(job/'result.json',result);store.update(job.name,status=result['status']);return result

def inspect_only(file,sha,root,blender,started):
    from .contract import fingerprint
    from .budgets import BudgetLimits,JobBudget
    from .io import copy_verified
    source={'file':file,'expected_sha256':sha};verify_descriptor(source)
    store=store_for(root);record=store.submit('inspect:'+uuid.uuid4().hex,fingerprint(source));job=store.job_dir(record['job_id'])
    store.update(job.name,status='running')
    try:
        staged,observation=copy_verified(source,job/'inspect.blend',limit_bytes=512*1024*1024)
        with ProtectedInputs([staged]):
            report=worker({'action':'inspect','source':staged['file'],'job_dir':str(job)},job,JobBudget(BudgetLimits(),started_at=started),store,blender,'inspect')
        result={'status':'succeeded','job_id':job.name,'inspection':report,'source_observation':observation,'accepted_geometry':False};store.update(job.name,status='succeeded')
    except Exception as exc:result={'status':'failed','job_id':job.name,'error':error_dict(exc)};store.update(job.name,status='failed')
    atomic_json(job/'result.json',result);return result

def study(request_path,cases_path,root,blender,started,reference_approval=None):
    from .contract import validate_request,fingerprint,schema
    base=validate_request(load(request_path));cases=load(cases_path)
    from .reference import verify_reference
    reference_gate=verify_reference(base['params'],reference_approval)
    if not isinstance(cases,list) or not 1<=len(cases)<=4:raise RuntimeFailure('INVALID_REQUEST','Study requires 1..4 named technical cases')
    names=set()
    for case in cases:
        if not isinstance(case,dict) or set(case)!={'id','strategy'} or not isinstance(case['id'],str) or not case['id'] or len(case['id'])>48 or not all(c.isalnum() or c in '_-' for c in case['id']) or case['id'] in names or case['strategy'] not in ('declared','boolean_exact','boolean_manifold','tessellation_refine'):
            raise RuntimeFailure('INVALID_REQUEST','Invalid/duplicate technical study case; dimensional changes are forbidden')
        names.add(case['id'])
    invariant={'design':fingerprint(base['params']['design']),'quality':fingerprint(base['params']['quality']),'reference':fingerprint(base['params'].get('reference_package'))}
    store=store_for(root);record=store.submit(derived_request_id(base['params']['request_id'],'study'),fingerprint({'base':base,'cases':cases,'reference_approval':reference_gate}),metadata={'admitted_monotonic':started});job=store.job_dir(record['job_id'])
    if record.get('reused'):return load(job/'result.json') if (job/'result.json').exists() else record
    store.update(job.name,status='running');results=[]
    shared=AggregateBudget(make_budget(base['params'],started),[job])
    for case in cases:
        request=copy.deepcopy(base);request['params']['request_id']=derived_request_id(request['params']['request_id'],'case:'+case['id']);request['params']['execution']={'max_attempts':1,'technical_strategies':[case['strategy']]}
        file=job/('case-'+case['id']+'.json');atomic_json(file,request)
        result=submit(file,root,blender,False,started,shared_budget=shared,parent_cancel_id=job.name,reference_approval=reference_approval);results.append({'case':case,'result':result,'immutable_design_reference_acceptance':invariant})
        if store.status(job.name).get('cancel_requested'):break
    outcome='cancelled' if store.status(job.name).get('cancel_requested') else 'succeeded' if len(results)==len(cases) and all(x['result'].get('status')=='succeeded' for x in results) else 'failed'
    result={'status':outcome,'job_id':job.name,'cases':results,'invariants':invariant,'automatic_visual_ranking':False,'wall_budget_shared_from_study_admission':True,'metrics':shared.report()}
    atomic_json(job/'result.json',result);store.update(job.name,status=outcome);return result

def resume(root,job_id,checkpoint_id,blender,started):
    from .recovery import CheckpointStore
    from .contract import fingerprint
    store=store_for(root);original=store.job_dir(job_id)
    if store.status(job_id)['status'] not in TERMINAL:raise RuntimeFailure('RESUME_SOURCE_NOT_TERMINAL','Original job termination is unconfirmed; do not duplicate a running or unknown execution')
    receipts=CheckpointStore(original)._receipts()
    matching=[r for r,_ in receipts if r['checkpoint_id']==checkpoint_id]
    if len(matching)!=1:raise RuntimeFailure('CHECKPOINT_INCOMPLETE','Exactly one accepted checkpoint required')
    cp=CheckpointStore(original).resume(matching[0]['fingerprints'],checkpoint_id=checkpoint_id)
    request=load(original/'request.json')
    approval=saved_reference(original)
    from .reference import verify_reference
    gate=verify_reference(request['params'],approval)
    if cp['fingerprints']['reference']!={'package':request['params'].get('reference_package',{'status':'not_applicable_fixture'}),'approval':gate}:raise RuntimeFailure('RESUME_FINGERPRINT_MISMATCH','Reference approval differs from accepted checkpoint')
    # Every current source input is rechecked; implementation is compared in run_job.
    p=request['params']
    if p['source']['kind']=='saved_blend':verify_descriptor(p['source'])
    for resource in p['resources']:verify_descriptor(resource)
    key=derived_request_id(store.status(job_id)['request_id'],'resume:'+cp['fingerprints_sha256'][:12]+':'+checkpoint_id)
    record=store.submit(key,fingerprint({'request':request,'checkpoint':cp['receipt']}),metadata={'admitted_monotonic':started,'resumed_from':job_id})
    job=store.job_dir(record['job_id'])
    if record.get('reused'):return load(job/'result.json') if (job/'result.json').exists() else record
    atomic_json(job/'request.json',request);atomic_json(job/'resume-checkpoint.json',cp)
    persist_reference(job,approval)
    return compact(run_job(root,job.name,blender,started,resume_checkpoint=cp))


def cancel_job(store,job_id):
    result=store.cancel(job_id)
    if result['status']!='queued':return result
    job=store.job_dir(job_id);fd=os.open(job/'execution.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return store.status(job_id)
        if store.status(job_id)['status']=='queued':
            evidence='Queued execution lock acquired; any later supervisor must reject nonqueued state'
            store.update(job_id,status='cancelled',finished_at=time.time(),cancellation_evidence=evidence)
            atomic_json(job/'result.json',{'status':'cancelled','job_id':job_id,'candidate':None,'execution_started':False,'cancellation_evidence':evidence})
        return store.status(job_id)
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def read_only_action(action,request_path,root,blender,started):
    from .contract import fingerprint
    from .io import copy_verified
    from .budgets import BudgetLimits,JobBudget,tree_file_bytes
    if action=='observe':from . import observation as adapter
    elif action=='validate':from . import validation as adapter
    elif action=='topology':from . import topology as adapter
    else:raise RuntimeFailure('UNSUPPORTED','No fixed read-only action')
    request=adapter.validate_request(load(request_path));p=request['params'];store=store_for(root)
    record=store.submit(p['request_id'],fingerprint(request),metadata={'admitted_monotonic':started,'action':action});job=store.job_dir(record['job_id'])
    if record.get('reused'):return load(job/'result.json') if (job/'result.json').exists() else record
    fd=os.open(job/'execution.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeFailure('JOB_ALREADY_RUNNING','Another supervisor owns this read-only job') from exc
        if store.status(job.name)['status']!='queued':raise RuntimeFailure('JOB_NOT_QUEUED','Only queued read-only work may start')
        return _read_only_impl(action,request,job,store,blender,started)
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def _read_only_impl(action,request,job,store,blender,started):
    from .io import copy_verified
    from .budgets import BudgetLimits,JobBudget,tree_file_bytes
    p=request['params']
    atomic_json(job/'request.json',request);store.update(job.name,status='running')
    budget=JobBudget(BudgetLimits(wall_seconds=p['wall_seconds'],max_tree_rss_bytes=1024**3,max_disk_bytes=512*1024**2),started_at=started)
    result={'status':'failed','job_id':job.name,'action':action,'source_saved':False}
    try:
        budget.check();identity=impl_identity(blender)
        snapshot,observation=copy_verified(p['source'],job/'source.blend',limit_bytes=256*1024**2)
        with ProtectedInputs([snapshot]) as guard:
            staged=copy.deepcopy(request);staged['params']['source']={'file':snapshot['file'],'expected_sha256':snapshot['sha256'],'bytes':snapshot['bytes']}
            if action=='observe':
                previews=[];view_reports=[];elapsed=0.0
                for index,view in enumerate(staged['params']['views'],1):
                    part=copy.deepcopy(staged);part['params']['views']=[view]
                    label='observe-view-'+str(index).zfill(2)
                    data=worker({'action':action,'source':snapshot['file'],'request':part,'job_dir':str(job)},job,budget,store,blender,label)
                    previews.extend(data['previews']);elapsed+=data['elapsed_seconds'];view_reports.append(descriptor(job/(label+'-result.json')))
                    guard.check();budget.check(disk_bytes=tree_file_bytes(job))
                data.update(previews=previews,elapsed_seconds=elapsed,view_reports=view_reports,view_process_isolation=True,budget_scope='All isolated view workers share one host wall/RSS/disk budget')
            else:
                data=worker({'action':action,'source':snapshot['file'],'request':staged,'job_dir':str(job)},job,budget,store,blender,action)
            guard.check();budget.check(disk_bytes=tree_file_bytes(job))
            if impl_identity(blender)!=identity:raise RuntimeFailure('IMPLEMENTATION_CHANGED','Read-only action implementation changed')
            final_source=verify_descriptor(p['source'])
        result.update(status='succeeded',**data,source_original=final_source,source_observation=observation,source_guard=guard.report(),implementation=identity,metrics=budget.report())
    except Exception as exc:
        e=error_dict(exc);result.update(error=e,status='cancelled' if 'CANCEL' in e['code'] else 'timed_out' if 'TIME' in e['code'] else 'failed',metrics=budget.report())
    full=atomic_json(job/'report.json',result)
    if action in ('observe','topology'):
        compact_result={k:result[k] for k in ('status','job_id','action','domain_outcome','source_original','source_saved','metrics','error') if k in result}
        compact_result.update(report=full,details_reference=full,details_omitted=True)
        if 'previews' in result:
            compact_result['previews']=[]
            for view in result['previews']:
                row={k:view[k] for k in ('file','sha256','bytes','view','width','height','mesh_state') if k in view}
                if 'wire' in view:row['wire']={k:view['wire'][k] for k in ('enabled','method','coverage','actual_edges','line_width_px','mesh_state','added_triangulation_edges','source_meshes_modified','visibility_method','geometric_visibility_completeness_proven','visibility_limitations') if k in view['wire']}
                compact_result['previews'].append(row)
        if 'topology' in result:
            topo=result['topology'];objects=[]
            for item in topo['objects'][:16]:
                states={}
                for state,value in item['states'].items():
                    summary={'counts':value['metrics']['counts'],'statistics':value['statistics']}
                    if 'geometry' in value:summary['geometry']=value['geometry']
                    if 'self_intersections' in value:
                        audit=value['self_intersections'];summary['self_intersections']={k:audit[k] for k in ('status','triangles','broad_phase_pairs_checked','exact_pairs_checked','finding_limit_reached') if k in audit}
                    states[state]=summary
                objects.append({'identity':{k:item['identity'][k] for k in ('object_id','feature_id','name','data_id','data_users')},'states':states})
            compact_result['topology']={'total_objects':len(topo['objects']),'returned_objects':len(objects),'has_more_objects':len(topo['objects'])>len(objects),'objects':objects,'excluded_counts':topo['selection']['excluded_counts'],'instances_counted_separately':True,'details_reference':full}
        if 'outputs' in result:compact_result['output_file_count']=len(result['outputs'])
    else:compact_result=result
    atomic_json(job/'result.json',compact_result);store.update(job.name,status=result['status'],finished_at=time.time());return compact_result


def parser():
    p=argparse.ArgumentParser(description='Hard Surface Workbench 0.2.0 development CLI; no source overwrite')
    p.add_argument('--jobs-dir',default=DEFAULT_JOBS);p.add_argument('--blender',default=DEFAULT_BLENDER);p.add_argument('--compact',action='store_true');p.add_argument('--async',dest='background',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    hs=sub.add_parser('hardsurface');actions=hs.add_subparsers(dest='action',required=True)
    d=actions.add_parser('describe');d.add_argument('--section',default='request')
    for name in ('plan','run','study','stage'):
        a=actions.add_parser(name);a.add_argument('--request',type=Path,required=True)
        if name=='study':a.add_argument('--cases',type=Path,required=True)
        if name in ('plan','run','study'):
            a.add_argument('--reference-approval',type=Path);a.add_argument('--reference-approval-sha256')
    for name in ('validate','observe','topology'):
        a=actions.add_parser(name);a.add_argument('--request',type=Path,required=True)
    a=actions.add_parser('inspect');a.add_argument('--file',required=True);a.add_argument('--expected-sha256',required=True)
    a=actions.add_parser('report');a.add_argument('job_id');a.add_argument('--section');a.add_argument('--offset',type=int,default=0);a.add_argument('--limit',type=int,default=50)
    a=actions.add_parser('archive');a.add_argument('job_id');a.add_argument('--destination',required=True)
    a=actions.add_parser('resume');a.add_argument('job_id');a.add_argument('--checkpoint',default='final')
    j=sub.add_parser('job').add_subparsers(dest='action',required=True)
    for name in ('status','result','cancel','wait'):
        a=j.add_parser(name);a.add_argument('job_id')
        if name=='wait':a.add_argument('--wait-seconds',type=float,default=30)
    a=j.add_parser('recover');a.add_argument('request_id')
    sub.add_parser('_supervise').add_argument('job_id')
    return p

def main(argv=None):
    started=time.monotonic()
    try:
        a=parser().parse_args(argv);root=Path(a.jobs_dir)
        if a.background and not (a.command=='hardsurface' and a.action=='run'):raise RuntimeFailure('INVALID_REQUEST','--async is supported only for hardsurface run')
        if a.command=='_supervise':result=compact(run_job(root,a.job_id,a.blender,started))
        elif a.command=='job':
            store=store_for(root)
            if a.action=='recover':result=store.recover(a.request_id)
            elif a.action=='cancel':result=cancel_job(store,a.job_id)
            elif a.action=='result':
                f=store.job_dir(a.job_id)/'result.json'
                if not f.is_file():raise RuntimeFailure('RESULT_PENDING','Result has not reached terminal state')
                result=load(f)
            elif a.action=='wait':
                if not 0<=a.wait_seconds<=55:raise RuntimeFailure('INVALID_REQUEST','wait_seconds must be 0..55')
                deadline=time.monotonic()+a.wait_seconds;result=store.status(a.job_id)
                while result['status'] not in TERMINAL and time.monotonic()<deadline:time.sleep(min(.2,max(0,deadline-time.monotonic())));result=store.status(a.job_id)
            else:result=store.status(a.job_id)
        elif a.action=='describe':
            from .contract import schema
            if a.section in ('validate','observe','topology'):
                from . import validation,observation,topology
                result={'validate':validation,'observe':observation,'topology':topology}[a.section].schema()
            else:result=schema(a.section)
        elif a.action=='plan':
            from .planner import plan_request
            from .reference import approval_descriptor
            result=plan_request(load(a.request),reference_approval=approval_descriptor(a.reference_approval,a.reference_approval_sha256))
        elif a.action=='run':
            from .reference import approval_descriptor
            result=submit(a.request,root,a.blender,a.background,started,reference_approval=approval_descriptor(a.reference_approval,a.reference_approval_sha256))
        elif a.action=='study':
            from .reference import approval_descriptor
            result=study(a.request,a.cases,root,a.blender,started,reference_approval=approval_descriptor(a.reference_approval,a.reference_approval_sha256))
        elif a.action=='stage':result=stage_only(a.request,root,a.blender,started)
        elif a.action in ('validate','observe','topology'):result=read_only_action(a.action,a.request,root,a.blender,started)
        elif a.action=='inspect':result=inspect_only(a.file,a.expected_sha256,root,a.blender,started)
        elif a.action=='resume':result=resume(root,a.job_id,a.checkpoint,a.blender,started)
        elif a.action=='report':
            result=load(store_for(root).job_dir(a.job_id)/'report.json')
            if a.section:
                if a.section not in result:raise RuntimeFailure('INVALID_REQUEST','Unknown report section')
                result=result[a.section]
                if isinstance(result,list):
                    if not 0<=a.offset or not 1<=a.limit<=100:raise RuntimeFailure('INVALID_REQUEST','Invalid page bounds')
                    total=len(result);result={'items':result[a.offset:a.offset+a.limit],'total':total,'next_offset':a.offset+a.limit if a.offset+a.limit<total else None}
        elif a.action=='archive':result=archive(root,a.job_id,a.destination,a.blender)
        else:raise RuntimeFailure('UNSUPPORTED',f'Action {a.action} integration is not yet qualified')
        print(json.dumps({'ok':True,'data':result},ensure_ascii=False,allow_nan=False));return 0 if not isinstance(result,dict) or result.get('status') not in ('failed','timed_out','cancelled') else 5
    except Exception as exc:
        print(json.dumps({'ok':False,'error':error_dict(exc)},ensure_ascii=False,allow_nan=False));return 2

if __name__=='__main__':raise SystemExit(main())
