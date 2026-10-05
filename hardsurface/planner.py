"""Deterministic, host-only work-unit planner. Estimates are not geometry proof."""
from __future__ import annotations
import copy
import math
from .contract import (ContractError, OPERATIONS, LENGTH_SCALES, normalize_request,
    resolve_design, dimension_values, fingerprint, _validate, STEP, _dag)

RECIPE_VERSIONS = {k:'1.0.0' for k in ('profile_extrude_edge_finish','plate_box','cylindrical_spacer','perforated_panel','linear_hole_pattern')}
OBJECT_PORTS = {'body_object'}

def _error(code,message,**details): raise ContractError(code,message,details=details)
def ref(step,port='body_object'): return {'kind':'step_output','step_id':step,'port':port}

def expand_recipe(program):
    """Only finite registered recipes; limits checked before materializing steps."""
    name=program['recipe_id']; p=program['parameters']; inputs=program['inputs']
    if RECIPE_VERSIONS.get(name)!=program['recipe_version']: _error('RECIPE_UNSUPPORTED','Unknown exact recipe version')
    if name=='profile_extrude_edge_finish':
        if set(p)-{'depth','bevel_width','editable'} or not {'depth','bevel_width'}<=set(p) or set(inputs)!={'profile'}: _error('INVALID_REQUEST','Recipe parameter/input mismatch')
        if inputs['profile']['kind']=='sketch_profile':
            sk=inputs['profile']; steps=[{'id':'solve','op':'sketch.solve','sketch_id':sk['sketch_id']}]; profile=ref('solve',sk['profile_id'])
        else: steps=[]; profile=inputs['profile']
        steps.extend([{'id':'body','op':'profile.extrude','profile':profile,'depth':p['depth']}, {'id':'finish','op':'edge.bevel','target':ref('body'),'width':p['bevel_width'],'mode':'modifier' if p.get('editable',True) else 'mesh_apply'}])
        return steps
    if name=='plate_box':
        if set(p)-{'size','bevel_width'} or 'size' not in p or inputs: _error('INVALID_REQUEST','Recipe parameter/input mismatch')
        steps=[{'id':'body','op':'primitive.box','size':p['size']}]
        if 'bevel_width' in p: steps.append({'id':'finish','op':'edge.bevel','target':ref('body'),'width':p['bevel_width']})
        return steps
    if name=='cylindrical_spacer':
        if set(p)-{'radius','depth','inner_radius','segments'} or not {'radius','depth'}<=set(p) or inputs: _error('INVALID_REQUEST','Recipe parameter/input mismatch')
        steps=[{'id':'body','op':'primitive.cylinder','radius':p['radius'],'depth':p['depth'],'segments':p.get('segments',64)}]
        if 'inner_radius' in p and p['inner_radius']>=p['radius']: _error('INVALID_GEOMETRY','Spacer bore radius must be smaller than outer radius')
        if 'inner_radius' in p:
            # Equality of depth deliberately rejected by geometry if caps are ambiguous;
            # explicit overshoot is calculated only after resolving design dimensions.
            steps.extend([{'id':'bore','op':'primitive.cylinder','radius':p['inner_radius'],'depth':p['depth'],'segments':p.get('segments',64)}, {'id':'cut','op':'boolean.apply_or_stack','target':ref('body'),'cutter':ref('bore'),'operation':'DIFFERENCE'}])
        return steps
    if name in ('perforated_panel','linear_hole_pattern'):
        required={'hole_radius','hole_count','hole_spacing','hole_origin'}|({'size'} if name=='perforated_panel' else {'target','hole_depth'})
        if set(p)-required-{'segments'} or not required<=set(p) or inputs: _error('INVALID_REQUEST','Recipe parameter/input mismatch')
        count=p['hole_count']
        if count*2+(1 if name=='perforated_panel' else 0)>64: _error('BUDGET_EXCEEDED','Recipe step count exceeds 64 before expansion')
        # Numeric/dimension values must already be resolved before arithmetic below.
        steps=[{'id':'body','op':'primitive.box','size':p['size']}] if name=='perforated_panel' else []
        target=ref('body') if steps else p['target']
        depth=p['size'][2]*1.1 if name=='perforated_panel' else p['hole_depth']
        if isinstance(depth,dict) or isinstance(p['hole_spacing'],dict): _error('INTERNAL_ERROR','Expand recipes only after resolving dimension refs')
        for i in range(count):
            center=list(p['hole_origin']); center[0]+=i*p['hole_spacing']
            cutter=f'hole{i}'; cut=f'cut{i}'
            steps.extend([{'id':cutter,'op':'primitive.cylinder','radius':p['hole_radius'],'depth':depth,'center':center,'segments':p.get('segments',32)}, {'id':cut,'op':'boolean.apply_or_stack','target':target,'cutter':ref(cutter),'operation':'DIFFERENCE'}])
            target=ref(cut)
        return steps
    _error('RECIPE_UNSUPPORTED','Unregistered recipe')


def _refs(value):
    if isinstance(value,dict):
        if value.get('kind') in ('step_output','feature_ref','object_ref','sketch_profile'): yield value
        else:
            for v in value.values(): yield from _refs(v)
    elif isinstance(value,list):
        for v in value: yield from _refs(v)

def _resolve_dimensions(value,dimensions,scale,context_key=None):
    if isinstance(value,dict):
        if value.get('kind')=='dimension_ref':
            d=dimensions.get(value['id'])
            if d is None: _error('INVALID_REFERENCE','Unknown dimension',dimension_id=value['id'])
            if d['role']!='driving': _error('INVALID_REFERENCE','Reference-only dimension cannot drive geometry',dimension_id=d['id'])
            expected='angle' if context_key=='angle' else 'length'
            if d['quantity']!=expected: _error('INVALID_REFERENCE','Dimension has incompatible physical quantity',dimension_id=d['id'],expected=expected)
            if expected=='length': return d['value']*LENGTH_SCALES[d['unit']]/scale
            return d['value'] if d['unit']=='deg' else math.degrees(d['value'])
        return {k:_resolve_dimensions(v,dimensions,scale,k) for k,v in value.items()}
    if isinstance(value,list): return [_resolve_dimensions(v,dimensions,scale,context_key) for v in value]
    return value


def curve_segment_count(radius,sweep_radians,chord_tolerance,max_segments):
    """Stable small-angle estimate; reject before allocating vertices."""
    if not (radius>0 and chord_tolerance>0 and 0<abs(sweep_radians)<=2*math.pi): _error('INVALID_GEOMETRY','Invalid curve sampling arguments')
    ratio=chord_tolerance/radius
    if ratio<=0: _error('BUDGET_EXCEEDED','Curve tolerance is below representable sampling scale')
    if ratio>=1: step=math.pi
    else: step=4*math.asin(math.sqrt(ratio/2))
    needed=max(3 if abs(sweep_radians)>=2*math.pi-1e-12 else 1,math.ceil(abs(sweep_radians)/step))
    if needed>max_segments: _error('BUDGET_EXCEEDED','Curve tessellation exceeds budget before allocation',estimated_segments=needed,limit=max_segments)
    return needed


def _reference_gate(params,approval):
    from .reference import verify_reference
    return verify_reference(params,approval)


def plan_request(request,saved_state=None,reference_approval=None):
    req=normalize_request(request); p=req['params']; gate=_reference_gate(p,reference_approval)
    state=resolve_design(p['design'],saved_state); scale=LENGTH_SCALES[p['source']['length_unit']]
    dimensions={d['id']:d for d in state['dimensions']}; sketches={s['id']:s for s in state['sketches']}; features={f['id']:f for f in state['features']}
    feature_units={}; units={u['id']:u for u in p['work_units']}
    for unit in units.values():
        for fid in unit['feature_ids']:
            if fid not in features: _error('INVALID_REFERENCE','Work unit references missing feature',feature_id=fid)
            if fid in feature_units: _error('WRITE_CONFLICT','Feature belongs to more than one work unit',feature_id=fid)
            feature_units[fid]=unit['id']
    # Closure must be explicit; silently expanding target scope is forbidden.
    selected=set(feature_units); feature_graph={fid:set(f['depends_on']) for fid,f in features.items() if fid in selected}; expanded={}
    total_count=0
    for fid in features:
        if fid not in selected: continue
        program=_resolve_dimensions(features[fid]['program'],dimensions,scale)
        steps=expand_recipe(program) if program['kind']=='recipe' else program['steps']
        total_count+=len(steps)
        if len(steps)>64 or total_count>p['budgets']['max_total_steps']: _error('BUDGET_EXCEEDED','Expanded step budget exceeded before execution',steps=total_count)
        expanded[fid]=[_validate(s,STEP,f'$.features.{fid}.steps') for s in steps]
        for reference in _refs(steps):
            if reference['kind']=='feature_ref': feature_graph[fid].add(reference['feature_id'])
        missing=feature_graph[fid]-selected
        if missing: _error('INVALID_REFERENCE','Dependency feature must be explicitly included in work units',feature_ids=sorted(missing))
    # Explicit unit dependencies also order their feature graphs.
    for fid in feature_graph:
        for unit_dep in units[feature_units[fid]]['depends_on']:
            feature_graph[fid].update(units[unit_dep]['feature_ids'])
    feature_order=_dag(feature_graph,'$.features')
    # Work-unit dependency graph receives feature cross-links; cycles rejected.
    unit_graph={k:set(v['depends_on']) for k,v in units.items()}
    for fid,deps in feature_graph.items():
        for dep in deps:
            if feature_units[fid]!=feature_units[dep]: unit_graph[feature_units[fid]].add(feature_units[dep])
    unit_order=_dag(unit_graph,'$.work_units')
    graph={}; planned={}; output_alias={}; feature_output={}; feature_completion={}; feature_alias={}; vertex_est={}; loop_est={}; instances=0; attempts=sum(s['solve']['max_attempts'] for s in sketches.values()); curve_points=0; warnings=[]
    def target_key(reference,fid):
        kind=reference['kind']
        if kind=='object_ref': return 'object:'+reference['object_id']
        if kind=='step_output': return output_alias.get(fid+'/'+reference['step_id'],fid+'/'+reference['step_id'])
        if kind=='feature_ref': return feature_alias[reference['feature_id']]
        return None
    for fid in feature_order:
        steps=expanded[fid]; local={s['id']:s for s in steps}
        if len(local)!=len(steps): _error('DUPLICATE_ID','Duplicate expanded step IDs')
        seen=set(); last_object=None; last_key=None
        for raw in steps:
            key=fid+'/'+raw['id']; deps={fid+'/'+x for x in raw['depends_on']}
            for dep in raw['depends_on']:
                if dep not in local: _error('INVALID_REFERENCE','Missing step depends_on target',step=key,dependency=dep)
            for refv in _refs(raw):
                kind=refv['kind']
                if kind=='step_output':
                    sid=refv['step_id']
                    if sid not in seen: _error('INVALID_REFERENCE','step_output must reference an earlier local step',step=key,reference=sid)
                    producer=local[sid]
                    ports=OPERATIONS[producer['op']]['ports']
                    if producer['op']=='sketch.solve':
                        if producer['sketch_id'] not in sketches: _error('INVALID_REFERENCE','Unknown sketch solve target')
                        ports=[x['id'] for x in sketches[producer['sketch_id']]['profiles']]
                    if refv['port'] not in ports: _error('INVALID_REFERENCE','Undeclared output port',step=key,port=refv['port'])
                    if 'profile' in raw and refv is raw['profile'] and producer['op']!='sketch.solve': _error('INVALID_REFERENCE','Profile input requires solved sketch profile output')
                    if any(field in raw and refv is raw[field] for field in ('target','cutter')) and refv['port'] not in OBJECT_PORTS: _error('INVALID_REFERENCE','Object input requires body_object port')
                    deps.add(fid+'/'+sid)
                elif kind=='feature_ref':
                    if refv['port']!='body_object' or refv['feature_id'] not in feature_output: _error('INVALID_REFERENCE','Cross-feature port is missing or not an object')
                    deps.add(feature_completion[refv['feature_id']])
                elif kind=='sketch_profile':
                    sk=sketches.get(refv['sketch_id'])
                    if sk is None or refv['profile_id'] not in {p['id'] for p in sk['profiles']}: _error('INVALID_REFERENCE','Unknown sketch profile')
                    warnings.append({'code':'SOLVE_REQUIRED','sketch_id':sk['id'],'message':'Direct profile reference requires accepted independent solve before geometry.'})
            for feature_dep in feature_graph[fid]:
                if not seen: deps.add(feature_completion[feature_dep])
            if raw['op']=='checkpoint': deps.update(fid+'/'+prior for prior in seen)
            if raw['op']=='sketch.solve':
                if raw['sketch_id'] not in sketches: _error('INVALID_REFERENCE','Unknown sketch solve target')
                solve=sketches[raw['sketch_id']]['solve']
            # Revalidate resolved lengths (dimension_ref positivity is semantic).
            for parameter in ('radius','depth','width','thickness','chord_tolerance'):
                if parameter in raw and raw[parameter]<=0: _error('INVALID_REQUEST','Positive physical parameter required',parameter=parameter)
            if raw['op']=='primitive.box' and any(v<=0 for v in raw['size']): _error('INVALID_REQUEST','Box dimensions must be positive')
            if raw['op']=='object.transform' and (any(v<=0 for v in raw['scale']) or max(raw['scale'])-min(raw['scale'])>1e-12): _error('UNSUPPORTED_TRANSFORM','Only positive uniform scale supported')
            if raw['op']=='pattern.linear' and not any(raw['offset']): _error('INVALID_GEOMETRY','Linear pattern offset must be nonzero')
            if raw['op']=='pattern.radial' and not 0<abs(raw['angle'])<=360: _error('INVALID_GEOMETRY','Radial pattern angle must be nonzero and at most one turn')
            if raw['op']=='profile.revolve' and not 0<abs(raw['angle'])<=360: _error('INVALID_REQUEST','Revolve angle must be nonzero and at most one turn')
            if raw['op']=='normal.finish' and raw['method']=='flat' and raw.get('angle')!=30.0: _error('INACTIVE_PARAMETER','Flat shading does not consume angle')
            if raw.get('selection',{}).get('kind')=='feature_role':
                sel=raw['selection']; producer=local.get(sel['feature_step'])
                if producer is None or producer['op'] not in ('profile.extrude','primitive.box','primitive.cylinder'): _error('SELECTION_STALE','Requested feature role has no supported provenance')
                deps.add(fid+'/'+sel['feature_step'])
            alias=target_key(raw['target'],fid) if 'target' in raw else key
            # Pattern body_object is the original object; instances are separate outputs.
            output_alias[key]=alias
            spec=OPERATIONS[raw['op']]; reads=[target_key(raw[field],fid) for field in spec['reads']]; writes=[target_key(raw[field],fid) for field in spec['writes']]
            for field in spec['reads']:
                if raw[field].get('kind')=='object_ref' and raw[field].get('data_id'): reads.append('data:'+raw[field]['data_id'])
            for field in spec['writes']:
                if raw[field].get('kind')=='object_ref' and raw[field].get('data_id'): writes.append('data:'+raw[field]['data_id'])
            if 'body_object' in spec['ports']:
                last_object=key
                if not writes: writes=[key+'#instances'] if raw['op'].startswith('pattern.') else [key]
            if raw['op']=='boolean.apply_or_stack' and reads[0]==reads[1]: _error('WRITE_CONFLICT','Boolean target cannot be its own cutter')
            graph[key]=deps; planned[key]={**copy.deepcopy(raw),'feature_id':fid,'unit_id':feature_units[fid],'step_key':key,'effective_params':copy.deepcopy(raw),'dependencies':sorted(deps),'read_set':reads,'write_set':writes,'changes_topology':spec['changes_topology']}
            op=raw['op']; v=l=0
            if op.startswith('quad.'):
                if p['source']['length_unit']!='mm': _error('QUAD_UNITS_UNSUPPORTED','Structured operations require explicit mm source units')
                from .quad_geometry import planning_evidence
                evidence=planning_evidence(raw,fid)
                planned[key]['geometry_estimate']=evidence
                v,l=evidence['vertices'],evidence['loops']
                curve_points+=v
            elif op=='primitive.box': v,l=8,24
            elif op=='primitive.cylinder': v,l=2*raw['segments'],6*raw['segments']
            elif op.startswith('profile.'):
                profile=raw['profile']
                sk=sketches[profile['sketch_id']] if profile['kind']=='sketch_profile' else sketches[local[profile['step_id']]['sketch_id']]
                nv=0
                for e in sk['entities']:
                    if e.get('construction'): continue
                    if e['kind']=='line_segment2d': nv+=2
                    elif e['kind'] in ('circle2d','arc2d'):
                        sweep=2*math.pi if e['kind']=='circle2d' else math.radians(e['sweep_seed'])
                        nv+=curve_segment_count(e['radius_seed'],sweep,raw['chord_tolerance'],p['budgets']['max_tessellation_vertices'])
                curve_points+=nv
                v=nv*(2 if op=='profile.extrude' else raw['segments']+1); l=v*6
            elif 'target' in raw:
                target=target_key(raw['target'],fid); v=vertex_est.get(target,0); l=loop_est.get(target,0)
                if target.startswith('object:'): warnings.append({'code':'RUNTIME_COST_REQUIRED','object_id':target[7:]})
                if op.startswith('pattern.'):
                    instances+=raw['count']; v*=raw['count']-1; l*=raw['count']-1
                elif op=='shell.solidify': v*=2; l*=4
                elif op=='edge.bevel': v*=1+raw['segments']*4; l*=1+raw['segments']*4
                elif op=='boolean.apply_or_stack':
                    cutter=target_key(raw['cutter'],fid)
                    # Track input-size work, not a claimed bound on intersections.
                    # Multiplying the prior heuristic at every Boolean invents
                    # exponential growth even for a sequence of simple holes.
                    v+=vertex_est.get(cutter,0); l+=loop_est.get(cutter,0)
                    planned[key]['geometry_estimate']={'method':'additive_operand_size_heuristic','vertices':v,'loops':l,'output_upper_bound':None,'runtime_evaluated_counts_required':True}
                    warnings.append({'code':'BOOLEAN_ESTIMATE_ONLY','step_key':key,'message':'Additive operand-size heuristic is not an output bound. Boolean output size is unknown until evaluated; actual pre/post geometry checks and process resource sampling are required.'})
            # Current evaluated alias replaces prior estimate; retained cutters still count.
            cost_key=key+'#instances' if op.startswith('pattern.') else alias
            vertex_est[cost_key]=v; loop_est[cost_key]=l
            seen.add(raw['id']); last_key=key
        feature_output[fid]=last_object or last_key
        feature_completion[fid]=last_key
        feature_alias[fid]=output_alias[last_object or last_key]
    order=_dag(graph,'$.steps')
    ancestors={}
    for key in order:
        ancestors[key]=set(graph[key])
        for dep in graph[key]: ancestors[key].update(ancestors[dep])
    for i,key in enumerate(order):
        a=planned[key]
        for prior in order[:i]:
            b=planned[prior]
            conflict=(set(a['write_set']) & (set(b['write_set'])|set(b['read_set']))) | (set(b['write_set']) & set(a['read_set']))
            if conflict and prior not in ancestors[key] and key not in ancestors[prior]: _error('WRITE_CONFLICT','Read/write domains overlap without dependency order',first=prior,second=key,domains=sorted(conflict))
    budget=p['budgets']; execution_attempts=p['execution']['max_attempts']
    diagnostic_calls=sum(s['solve']['max_attempts'] for s in sketches.values() if s['solve']['diagnostics']['when']!='never')
    diagnostic_seconds=sum(s['solve']['max_attempts']*s['solve']['diagnostics']['max_seconds'] for s in sketches.values() if s['solve']['diagnostics']['when']!='never')
    estimates={'diagnostic_calls':diagnostic_calls,'diagnostic_wall_seconds_reserved':diagnostic_seconds,'primary_solve_calls':attempts,'total_solve_calls':attempts+diagnostic_calls,'diagnostic_internal_work':'Native diagnosis may re-solve per constraint; bounded by separate diagnostic timeout and aggregate wall budget, not assumed one ordinary solve.','steps':len(order),'vertices':sum(vertex_est.values()),'loops':sum(loop_est.values()),'instances':instances,'solve_attempts':attempts*execution_attempts,'tessellation_vertices':curve_points,'execution_attempts':execution_attempts,'preview_samples':0,'confidence':'heuristic_not_hard_bound','hard_memory_enforcement':False}
    if 'preview' in p:
        prev=p['preview']; estimates['preview_samples']=prev['width']*prev['height']*prev['samples']*len(prev['views'])*execution_attempts
    wire=p['wire'];wire_count=(len(state['features'])+instances if wire['component_views'] else 1) if wire['enabled'] else 0
    if wire_count>16:_error('BUDGET_EXCEEDED','Default component wire diagnostics exceed 16 views; explicitly narrow/disable diagnostic output before execution',views=wire_count,limit=16)
    components_upper=len(state['features'])+instances
    checkpoint_count=sum(step['op']=='checkpoint' for step in planned.values())
    quad_required=p['purpose']=='production' or any(step['op'].startswith('quad.') for step in planned.values())
    quality_artifacts=2*components_upper*(checkpoint_count+1) if quad_required else 0
    artifact_upper=3+len(p['resources'])+2*checkpoint_count+quality_artifacts+((2*components_upper+2*wire_count) if wire['enabled'] else 0)
    if artifact_upper>256:_error('BUDGET_EXCEEDED','Required quality/diagnostic evidence exceeds checkpoint artifact capacity before execution',artifacts=artifact_upper,limit=256)
    estimates['wire_artifact_upper_bound']=artifact_upper
    estimates['wire_views_upper_bound']=wire_count
    estimates['wire_preview_samples']=wire_count*wire['width']*wire['height']*32
    estimates['preview_samples']+=estimates['wire_preview_samples']
    estimates['geometry_policy']={'static_geometry':'heuristic_admission_check_not_output_bound','boolean_output_upper_bound':None,'provable_expansion_counts':{'steps':len(order),'requested_pattern_instances':instances,'preview_samples':estimates['preview_samples']},'runtime_geometry_limits':{'vertices':budget['max_geometry_vertices'],'loops':budget['max_geometry_loops'],'scope':'all evaluated scene meshes, including retained cutters','checks':'before each Boolean, after Boolean evaluation, after each step and before checkpoint persistence'},'process_limits':{'wall_seconds':budget['wall_seconds'],'max_observed_rss_bytes':budget['max_observed_rss_bytes'],'cpu_threads':budget['cpu_threads'],'enforcement':'host watchdog checks elapsed wall time and sampled process-family RSS; Blender/OMP receive requested thread counts, not a CPU-time limit','limitation':'geometry checks happen after evaluation; RSS sampling can miss transient peaks and is not hard memory isolation'}}
    for field,limit in [('vertices','max_geometry_vertices'),('loops','max_geometry_loops'),('instances','max_instances'),('total_solve_calls','max_total_attempts'),('tessellation_vertices','max_tessellation_vertices'),('preview_samples','max_preview_samples')]:
        if estimates[field]>budget[limit]: _error('BUDGET_EXCEEDED',f'{field} estimate exceeds budget before geometry allocation',estimate=estimates[field],limit=budget[limit])
    checks=required_checks(p,state,[planned[k] for k in order])
    result={'required_checks':checks,'plan_version':'1.0','request':req,'resolved_design_state':state,'design_sha256':fingerprint(state),'dimension_values':dimension_values(state),'length_scale':scale,'steps':[planned[k] for k in order],'work_units':[units[k] for k in unit_order],'estimates':estimates,'reference_gate':gate,'warnings':warnings,'checkpoints_after':list(feature_completion.values())}
    result['plan_sha256']=fingerprint(result)
    return result

plan=plan_request


CHECK_PRODUCERS = {
    'constraint_residuals':'solver', 'profile_validity':'solver',
    'design_dimensions':'core','closed_mesh':'core','normals':'core','volume':'core',
    'preservation':'core','source_preserved':'host','reopen':'host','dependencies':'host',
    'reference_consistency':'external_visual','quad_topology':'core',
}

def required_checks(params,state,steps):
    """Demand explicit evidence for every requested check; scopes are discoverable.

    This schedules checks, never produces a pass. Downstream acceptance must fail
    closed when a scoped result is absent or not_run.
    """
    declared=set(params['quality']['required'])
    if params['purpose']=='production' or any(s['op'].startswith('quad.') for s in steps):
        declared.add('quad_topology')
    by_unit={}
    for unit in params['work_units']:
        for check in unit['checks']:
            declared.add(check); by_unit.setdefault(check,[]).append(unit['id'])
    geometry=[s for s in steps if s['op'] not in ('sketch.solve','checkpoint','measure')]
    profiles=[s for s in steps if s['op'].startswith('profile.')]
    result=[]
    for check in sorted(declared):
        producer=CHECK_PRODUCERS.get(check)
        if producer is None: _error('CHECK_UNSUPPORTED','Required check has no registered evidence producer',check=check)
        applicable=True; reason=None
        if check=='reference_consistency' and params['purpose']=='contract_fixture': _error('CHECK_UNSUPPORTED','Fixture cannot establish production reference consistency',check=check)
        if check=='constraint_residuals' and not state['sketches']: _error('CHECK_NOT_APPLICABLE','Required constraint check has no sketch scope',check=check)
        if check=='profile_validity' and not profiles: _error('CHECK_NOT_APPLICABLE','Required profile validity has no consumed profile scope',check=check)
        if check in ('design_dimensions','closed_mesh','normals','volume') and not geometry and not any(s['op']=='measure' for s in steps): _error('CHECK_NOT_APPLICABLE','Required geometry check has no geometry or measure scope',check=check)
        if check=='source_preserved' and params['source']['kind']=='new_scene': applicable=False; reason='Explicit new_scene has no original source file; resources remain independently protected.'
        scope=profiles if check=='profile_validity' else geometry if check in ('design_dimensions','closed_mesh','normals','volume') else steps
        result.append({'id':check,'producer':producer,'applicable':applicable,'reason':reason,'unit_ids':sorted(by_unit.get(check,[])),'step_keys':[s['step_key'] for s in scope],'scope':'Executed feature parameters and evaluated geometry; not manufacturing certification.' if check=='design_dimensions' else 'Declared work-unit targets and consumed dependencies.'})
    return result

def validate_check_results(plan,results):
    """Pure evidence gate used by host and tests; no status inferred from exit code."""
    for spec in plan['required_checks']:
        check=spec['id']; result=results.get(check)
        if not isinstance(result,dict): _error('REQUIRED_CHECK_MISSING','Required acceptance evidence is absent',check=check)
        expected='pass' if spec['applicable'] else 'not_applicable'
        if result.get('status')!=expected: _error('REQUIRED_CHECK_FAILED','Required acceptance status is not satisfied',check=check,status=result.get('status'),expected=expected)
        if expected=='pass' and not result.get('evidence'): _error('REQUIRED_CHECK_MISSING','A pass requires reviewable evidence',check=check)
        if expected=='not_applicable' and not result.get('reason'): _error('REQUIRED_CHECK_MISSING','Not applicable requires explicit rationale',check=check)
    return {'status':'pass','checks':sorted(spec['id'] for spec in plan['required_checks'])}
