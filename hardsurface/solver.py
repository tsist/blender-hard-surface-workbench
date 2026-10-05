"""Pinned SolveSpace 3.2 process adapter and fail-closed result contract.

This module does not import slvs. Only solver_worker imports native code, in a
new owned process after explicit deployment configuration has been supplied.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import uuid
import time

from .sketch import (SketchError,metric_sketch,verify_constraints,tessellate_profile,
                     branch_signature,check_branch,LENGTH_TO_MM)

PROTOCOL_VERSION='HS_SOLVER_V1'
ADAPTER_VERSION='1.0.0'
WHEEL_NAME='slvs-3.2-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
WHEEL_SHA256='9ee04ff5a00482cef1751eed71658f4b01f605de42c56786373c7da9e5b25f80'
MAX_MESSAGE_BYTES=2*1024*1024
MAX_RESULT_BYTES=8*1024*1024


class SolverError(ValueError):
    def __init__(self,code,message):
        super().__init__(message); self.code=code


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(raw,limit=MAX_RESULT_BYTES):
    if len(raw)>limit: raise SolverError('protocol_error','JSON message exceeds budget')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise SolverError('protocol_error','Duplicate JSON key: '+k)
            result[k]=v
        return result
    def bad(value): raise SolverError('protocol_error','Nonfinite JSON number '+value)
    try: result=json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)
    except (ValueError,UnicodeError,RecursionError) as e:
        if isinstance(e,SolverError): raise
        raise SolverError('protocol_error',str(e)) from e
    def check(x,depth=0):
        if depth>32: raise SolverError('protocol_error','JSON nesting budget exceeded')
        if isinstance(x,float) and not math.isfinite(x): raise SolverError('protocol_error','Nonfinite number')
        if isinstance(x,dict):
            for v in x.values(): check(v,depth+1)
        elif isinstance(x,list):
            for v in x: check(v,depth+1)
    check(result)
    return result


def normalize_result(raw,handles=None):
    """Only actual upstream fields are normalized. Failed-result DOF is unknown."""
    handles=handles or {}; failed=[]
    if isinstance(raw,tuple) and len(raw)==2: raw,failed=raw
    if not isinstance(raw,dict) or type(raw.get('result')) is not int:
        raise SolverError('protocol_error','Unexpected slvs result shape')
    if not isinstance(failed,(list,tuple)) or any(type(x) is not int for x in failed):
        raise SolverError('protocol_error','Unexpected slvs diagnostic handles')
    code=raw['result']; statuses={0:'solved',1:'inconsistent_or_redundant_nonconvergent',2:'did_not_converge',3:'too_many_unknowns',4:'redundant_solved'}
    if code not in statuses: raise SolverError('protocol_error','Unknown slvs result flag '+str(code))
    native_dof=raw.get('dof'); known=code in (0,4) and type(native_dof) is int and native_dof>=0
    return {'backend_status':statuses[code],'backend_result_code':code,'dof':native_dof if known else None,
            'dof_status':'known' if known else 'unknown','diagnostic_constraint_ids':sorted({handles[h] for h in failed if h in handles}),
            'unmapped_diagnostic_handle_count':sum(h not in handles for h in failed),
            'diagnostic_kind':'related_or_suspected_constraints' if failed else 'none_reported',
            'native_fields_present':sorted(raw)}


NATIVE_EXPANSION_LIMIT = 2047
EXPANSION_ESTIMATOR_VERSION = 'HS_SLVS32_EXPANSION_V1'


def estimate_native_expansion(sketch,limit=NATIVE_EXPANSION_LIMIT):
    """Conservative pre-substitution counts, without allocating helper entities.

    Upstream v3.2 MAX_UNKNOWNS=2048 checks equation count in WriteJacobian.
    This adapter also caps parameter expansion for bounded allocation. Native
    substitutions may reduce counts; rejection is our budget, never native code3.
    """
    if type(limit) is not int or not 1<=limit<=NATIVE_EXPANSION_LIMIT:
        raise SolverError('invalid_budget','Native expansion cap must be in [1,2047]')
    parameters=equations=helper_points=0
    for entity in sketch['entities']:
        kind=entity['kind']
        if kind=='point2d':parameters+=2
        elif kind=='circle2d':parameters+=1
        elif kind=='arc2d':parameters+=4;equations+=1;helper_points+=2
        elif kind!='line_segment2d':raise SolverError('unsupported_entity',kind)
    two_equations={'point_fixed2d','coincident','concentric','arc_endpoint_coincident'}
    one_equation={'horizontal','vertical','parallel','perpendicular','equal_length','equal_radius','distance','radius','diameter','angle','arc_start_angle','arc_sweep'}
    for constraint in sketch.get('constraints',[]):
        if constraint.get('mode','driving')=='reference':continue
        kind=constraint['type']
        if kind in two_equations:equations+=2
        elif kind in one_equation:equations+=1
        elif kind=='equal_spacing':
            n=len(constraint['points']);equations+=3*n-2;parameters+=2*n;helper_points+=n
        elif kind in {'axis_distance2d','point_line_distance','tangent_line_circle','tangent_line_arc','tangent_circle_circle','tangent_arc_arc'}:
            equations+=3;parameters+=2;helper_points+=1
        else:raise SolverError('unsupported_constraint',kind)
    result={'estimator':EXPANSION_ESTIMATOR_VERSION,'native_equations_upper_bound':equations,
            'native_parameters_upper_bound':parameters,'auxiliary_point_count_upper_bound':helper_points,
            'equation_limit':limit,'parameter_limit':limit,'native_code3_exercised':False}
    if equations>limit or parameters>limit:
        raise SolverError('solver_expansion_budget',f'Pre-expansion budget exceeded: equations={equations}, parameters={parameters}, per-count limit={limit}; native code3 was not invoked')
    return result


def verify_candidate_identity(declared,candidate):
    """Only seed coordinates may differ across the solver boundary."""
    def authority(sketch):
        out=copy.deepcopy(sketch)
        for e in out['entities']:
            for key in ('seed','radius_seed','start_angle_seed','sweep_seed'):e.pop(key,None)
        return out
    if authority(declared)!=authority(candidate):raise SolverError('protocol_error','Worker changed design identities or constraint authority')


def failure(code,message,**extra):
    return {'accepted':False,'status':code,'dof':None,'dof_status':'unknown','diagnostic_constraint_ids':[],
            'verification':{'status':'not_run'},'profiles':{},'error':{'code':code,'message':message},**extra}


def validate_deployment(config):
    required={'python_executable','package_dir','wheel_path'}
    missing=required-set(config)
    if missing: raise SolverError('backend_unavailable','Missing isolated slvs deployment: '+','.join(sorted(missing)))
    for key in required:
        if not Path(config[key]).is_absolute(): raise SolverError('invalid_deployment',key+' must be absolute')
    wheel=Path(config['wheel_path'])
    if not wheel.is_file(): raise SolverError('backend_unavailable','Pinned wheel missing')
    if wheel.stat().st_size>10*1024*1024:raise SolverError('backend_identity_mismatch','Wheel exceeds expected bounded size')
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=WHEEL_SHA256:
        raise SolverError('backend_identity_mismatch','Pinned wheel SHA256 differs')
    if not Path(config['python_executable']).is_file() or not Path(config['package_dir']).is_dir():
        raise SolverError('backend_unavailable','Isolated interpreter or package directory missing')


class _StageBudget:
    """Intersect a stage wall cap with the one cumulative job budget."""
    def __init__(self,shared,seconds):
        self.shared=shared;self.started=time.monotonic();self.seconds=seconds
    @property
    def remaining_seconds(self):return max(0.,min(self.shared.remaining_seconds,self.seconds-(time.monotonic()-self.started)))
    def check(self,**kw):
        from .budgets import BudgetExceeded
        self.shared.check(**kw)
        elapsed=time.monotonic()-self.started
        if elapsed>=self.seconds:raise BudgetExceeded('wall_seconds',elapsed,self.seconds)
        return self.report()
    def report(self):return {**self.shared.report(),'stage_wall_seconds':time.monotonic()-self.started,'stage_limit_seconds':self.seconds}


def run_worker(request,config,timeout_seconds=30,*,job_dir=None,budget=None,cancel_check=None):
    """One request per owned process. Request, response, and logs are retained."""
    from .budgets import JobBudget,BudgetLimits
    from .jobs import OwnedProcess
    if not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=600:raise SolverError('invalid_budget','Worker timeout out of bounds')
    if budget is None:budget=JobBudget(BudgetLimits(wall_seconds=timeout_seconds),started_at=time.monotonic())
    stage=_StageBudget(budget,timeout_seconds);stage.check()
    validate_deployment(config)
    payload=canonical(request)
    if len(payload)>MAX_MESSAGE_BYTES:raise SolverError('protocol_error','Request too large')
    root=Path(__file__).resolve().parent.parent
    retained=Path(job_dir) if job_dir else root/'evidence'/'solver-jobs'
    retained.mkdir(parents=True,exist_ok=True)
    tmp=retained/('solver-'+uuid.uuid4().hex);tmp.mkdir(mode=0o700)
    output=tmp/'response.json';req=tmp/'request.json';req.write_bytes(payload)
    launch=[config['python_executable'],'-I','-c',
            'import sys;sys.dont_write_bytecode=True;sys.pycache_prefix=sys.argv[2]+".unused-bytecode";sys.path.insert(0,sys.argv[1]);from hardsurface.solver_worker import main;raise SystemExit(main(sys.argv[2:]))',
            str(root),str(req),str(output),str(config['package_dir']),str(config['wheel_path'])]
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT')}
    env.update({'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'})
    disk_root=retained
    for ancestor in [retained,*list(retained.parents)[:8]]:
        if (ancestor/'job.json').is_file():disk_root=ancestor;break
    process=OwnedProcess(launch,cwd=tmp,budget=stage,cancel_check=cancel_check,env=env,
                         stdout_path=tmp/'stdout.log',stderr_path=tmp/'stderr.log',disk_root=disk_root).run()
    process['artifacts_directory']=str(tmp)
    if process.get('cleanup_review_required'):return failure('cleanup_review_required','Worker process family termination is unconfirmed',process=process)
    if process['status']=='cancelled':return failure('cancelled','Solver cancelled',process=process)
    if process['status']=='timed_out':return failure('solver_timeout','Solver exceeded remaining stage/job wall budget',process=process)
    returncode=process['returncode']
    if returncode is not None and returncode<0:return failure('native_crash','Solver worker terminated by signal '+str(-returncode),process=process)
    if returncode!=0:return failure('worker_failure','Solver worker did not finish successfully',process=process)
    if not output.is_file():return failure('protocol_error','Worker did not atomically commit a result',process=process)
    if output.stat().st_size>MAX_RESULT_BYTES:return failure('protocol_error','Worker output exceeds bound',process=process)
    result=strict_json(output.read_bytes())
    required={'protocol_version','request_sha256','request_id','result'}
    if not isinstance(result,dict) or set(result)!=required or result['protocol_version']!=PROTOCOL_VERSION or result['request_sha256']!=digest(request) or result['request_id']!=request['request_id']:
        return failure('protocol_error','Worker response identity or envelope mismatch',process=process)
    if not isinstance(result['result'],dict):return failure('protocol_error','Worker result is not an object',process=process)
    stage.check()
    return {**result['result'],'process':process}


def solve_sketch(sketch,dimension_values,*,length_unit='mm',config=None,last_good=None,request_id='sketch-solve',job_id='standalone',job_dir=None,budget=None,cancel_check=None):
    """Solve with supplied SI dimension values; return mesh-ready mm profiles.

    No installed backend means backend_unavailable, never a seed-as-solution
    fallback. last_good must be a previously accepted metric solved sketch.
    """
    options=sketch.get('solve',{}); config=config or {}
    from .budgets import JobBudget,BudgetLimits,BudgetExceeded
    if budget is None:budget=JobBudget(BudgetLimits(wall_seconds=config.get('timeout_seconds',30)),started_at=time.monotonic())
    try:
        expansion=estimate_native_expansion(sketch)
        metric=metric_sketch(sketch,length_unit)
        before=branch_signature(last_good or metric)
        strategies=options.get('allowed_strategies',['declared_seed'])
        attempts=[]; current=None
        for strategy in strategies[:options.get('max_attempts',1)]:
            budget.check()
            if cancel_check and cancel_check():return failure('cancelled','Solver cancelled before next attempt')
            if strategy=='last_good_seed' and last_good is None:
                attempts.append({'strategy':strategy,'status':'not_available'});continue
            if strategy not in ('declared_seed','last_good_seed'):
                raise SolverError('unsupported_strategy',strategy)
            seeded=copy.deepcopy(metric)
            if strategy=='last_good_seed':
                previous={e['id']:e for e in last_good['entities']}
                for e in seeded['entities']:
                    if e['id'] in previous:
                        for key in ('seed','radius_seed','start_angle_seed','sweep_seed'):
                            if key in e and key in previous[e['id']]:e[key]=copy.deepcopy(previous[e['id']][key])
            request={'protocol_version':PROTOCOL_VERSION,'request_id':request_id,'job_id':job_id,
                     'sketch':seeded,'dimension_values':dimension_values,
                     'backend':{'name':'slvs','version':'3.2','wheel_sha256':WHEEL_SHA256},
                     'diagnostics':options.get('diagnostics',{'when':'failure_or_redundancy','max_seconds':5}),'phase':'primary'}
            current=run_worker(request,config,config.get('timeout_seconds',30),job_dir=job_dir,budget=budget,cancel_check=cancel_check)
            if current.get('backend_result_code') in (1,2,4) and request['diagnostics'].get('when')=='failure_or_redundancy':
                detailed=copy.deepcopy(request);detailed['phase']='diagnostics'
                detail=run_worker(detailed,config,request['diagnostics'].get('max_seconds',5),job_dir=job_dir,budget=budget,cancel_check=cancel_check)
                current['diagnostic_process']=detail.get('process')
                current['diagnostic_status']=detail.get('backend_status',detail.get('status'))
                if detail.get('backend_result_code')==current.get('backend_result_code'):
                    for key in ('diagnostic_constraint_ids','diagnostic_kind','unmapped_diagnostic_handle_count'):current[key]=detail[key]
                elif detail.get('status') in ('cancelled','cleanup_review_required'):
                    return detail
                else:current['diagnostic_kind']='incomplete_or_nonreproduced'

            attempts.append({'strategy':strategy,'status':current.get('backend_status',current.get('status'))})
            if current.get('backend_result_code') in (0,4):break
            if current.get('status') in ('native_crash','solver_timeout','protocol_error','abi_import_failure'):break
        if current is None:return failure('no_available_strategy','No requested seed strategy is available',attempts=attempts)
        current['attempts']=attempts
        current['expansion_estimate']=expansion
        if current.get('backend_result_code') not in (0,4):
            current.update({'accepted':False,'status':current.get('status',current.get('backend_status','backend_failure'))});return current
        solved=current['solved_sketch']; verify_candidate_identity(metric,solved); tolerances=options.get('verification',{})
        verify=verify_constraints(solved,dimension_values,**tolerances)
        after=branch_signature(solved); branch=check_branch(before,after)
        def profile_guard():
            budget.check()
            if cancel_check and cancel_check():raise SolverError('cancelled','Solver cancelled during profile verification')
        profiles={};remaining_vertices=config.get('max_vertices',200000)
        for p in solved.get('profiles',[]):
            budget.check()
            if cancel_check and cancel_check():return failure('cancelled','Solver cancelled before profile verification')
            profiles[p['id']]=tessellate_profile(solved,p,config.get('chord_tolerance_mm',.01),remaining_vertices,tolerances.get('length_tolerance_mm',.001),budget_check=profile_guard)
            remaining_vertices-=profiles[p['id']]['vertex_count']
        status='verified_solved'
        if current['dof_status']!='known':status='dof_unknown'
        elif current['dof']>0 and options.get('underconstrained','reject')=='reject':status='underconstrained'
        elif current['backend_result_code']==4 and options.get('redundant_solved','report_and_verify')=='reject':status='redundant_rejected'
        elif verify['status']!='pass':status='residual_verification_failed'
        elif branch['status']!='pass':status='branch_changed'
        budget.check()
        if cancel_check and cancel_check():return failure('cancelled','Solver cancelled before candidate acceptance')
        current.update({'accepted':status=='verified_solved','status':status,'verification':verify,'branch':branch,
                        'branch_signature':after,'profiles':profiles,'coordinate_unit':'mm'})
        return current
    except (SketchError,SolverError) as e:return failure(e.code,str(e))
    except BudgetExceeded as e:return failure('resource_limit',str(e),budget=e.as_dict())
    except (KeyError,TypeError,ValueError) as e:return failure('invalid_input',str(e))
    except OSError as e:return failure('worker_start_or_io_failure',str(e))
