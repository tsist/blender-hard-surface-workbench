"""Blender-only, schema-driven domain core. Authored code; never executes request code.

The host owns OS process isolation, source guards, final acceptance and resume receipts.
This module owns only an in-memory candidate and new files in its job directory.
"""
from __future__ import annotations
import hashlib, json, math, os, time, uuid, copy
from pathlib import Path
import bpy, bmesh
from mathutils import Matrix, Vector, Euler
from .ops.geometry import (GeometryError, digest, mesh_metrics, require_closed, new_mesh,
                           create_extrusion, create_revolution, require_si_scene)
from .io import compact_checkpoint_record, compact_step_records, read_json_reference

VERSION='0.2.0'
PROCESS_IDENTITY='blender:'+str(os.getpid())+':'+str(uuid.uuid4())
NAMESPACE=uuid.UUID('a05525d5-56ae-43b3-b650-91f8da01fb0e')

class CoreError(GeometryError): pass

def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def file_record(path):
    p=Path(path).resolve();return {'file':str(p),'sha256':sha_file(p),'bytes':p.stat().st_size}

def save_json_new(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
    return file_record(p)

def stable_id(project,key):return str(uuid.uuid5(NAMESPACE,project+'/'+key))

def plain(v):
    if isinstance(v,bpy.types.ID):return {'id_type':v.__class__.__name__,'name':v.name,'object_id':v.get('hs_object_id')}
    if isinstance(v,(str,int,float,bool)) or v is None:return v
    if hasattr(v,'to_list'):return plain(v.to_list())
    if hasattr(v,'to_dict'):return {str(k):plain(x) for k,x in v.to_dict().items()}
    try:return [plain(x) for x in v]
    except TypeError:return str(v)

def modifiers_snapshot(obj):
    records=[]
    for m in obj.modifiers:
        row={'name':m.name,'type':m.type}
        for p in m.bl_rna.properties:
            if p.identifier in ('rna_type','name','type') or p.type=='COLLECTION' or p.is_readonly:continue
            try:row[p.identifier]=plain(getattr(m,p.identifier))
            except (AttributeError,TypeError):continue
        records.append(row)
    return records

def mesh_content(obj):
    mesh=obj.data
    attrs=[]
    for a in mesh.attributes:
        if a.name.startswith('.select') or a.name.startswith('.hide'):continue
        values=[]
        for item in a.data:
            for key in ('value','vector','color','byte_color'):
                if hasattr(item,key):values.append(plain(getattr(item,key)));break
        attrs.append({'name':a.name,'domain':a.domain,'type':a.data_type,'values_sha256':digest(values)})
    return {'mesh_name':mesh.name,'mesh_custom':{k:plain(v) for k,v in mesh.items()},'base_metrics':mesh_metrics(obj,False),'attributes':attrs,
            'material_indices':[p.material_index for p in mesh.polygons],
            'smooth':[p.use_smooth for p in mesh.polygons],
            'uv_layers':[{'name':uv.name,'data':digest([list(d.uv) for d in uv.data])} for uv in mesh.uv_layers]}

def object_snapshot(obj):
    bpy.context.view_layer.update()
    row={'object_id':obj.get('hs_object_id'),'data_id':obj.data.get('hs_data_id') if obj.data else None,'name':obj.name,'type':obj.type,
         'feature_id':obj.get('hs_feature_id'),'step_key':obj.get('hs_step_key'),'generated':bool(obj.get('hs_generated',False)),
         'matrix_world':[[float(v) for v in r] for r in obj.matrix_world],
         'parent':obj.parent.get('hs_object_id',obj.parent.name) if obj.parent else None,
         'collections':sorted(c.name for c in obj.users_collection),'hide_render':obj.hide_render,'hide_viewport':obj.hide_viewport,
         'modifiers':modifiers_snapshot(obj),'constraints':[{'type':c.type,'name':c.name} for c in obj.constraints],
         'custom':{k:plain(v) for k,v in obj.items()},
         'materials':[{'name':s.material.name if s.material else None,'link':s.link} for s in obj.material_slots]}
    if obj.type=='MESH':row.update(mesh_content(obj));row['evaluated']=mesh_metrics(obj)
    elif obj.data:
        row['data_name']=obj.data.name
        if obj.type=='CAMERA':row['camera']={'lens':obj.data.lens,'type':obj.data.type,'ortho_scale':obj.data.ortho_scale}
        if obj.type=='LIGHT':row['light']={'type':obj.data.type,'energy':obj.data.energy,'color':list(obj.data.color)}
    return row

def loaded_design():
    data=bpy.context.scene.get('hs_design_state')
    if not data:return None
    try:state=json.loads(data)
    except (ValueError,TypeError) as exc:raise CoreError('DESIGN_STATE_INVALID','Saved design state is not JSON') from exc
    if digest(state)!=bpy.context.scene.get('hs_design_sha256'):raise CoreError('DESIGN_STATE_CONFLICT','Saved design SHA does not match state')
    return state

def loaded_solutions():
    raw=bpy.context.scene.get('hs_solutions')
    expected=bpy.context.scene.get('hs_solutions_sha256')
    if raw is None:
        if expected is not None:raise CoreError('SOLUTION_STATE_CONFLICT','Saved solutions SHA exists without solutions')
        return {}
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>2*1024*1024:raise CoreError('SOLUTION_STATE_LIMIT','Saved solutions must be bounded JSON text <=2 MiB')
    from .contract import strict_loads
    try:solutions=strict_loads(raw)
    except Exception as exc:raise CoreError('SOLUTION_STATE_INVALID','Saved solutions are not strict bounded JSON') from exc
    if not isinstance(solutions,dict) or len(solutions)>32:raise CoreError('SOLUTION_STATE_LIMIT','Saved solution mapping exceeds 32 sketches')
    if expected!=digest(solutions):raise CoreError('SOLUTION_STATE_CONFLICT','Saved solutions SHA does not match content')
    return solutions

def inspect_scene():
    scene_units=require_si_scene()
    ids={};data_ids={};objects=[]
    for obj in sorted(bpy.context.scene.objects,key=lambda x:x.name):
        oid=obj.get('hs_object_id')
        if oid and oid in ids:raise CoreError('DUPLICATE_ID','Duplicate persistent object ID',{'id':oid})
        if oid:ids[oid]=obj
        if obj.data and obj.data.get('hs_data_id'):
            did=obj.data['hs_data_id']
            if did in data_ids and data_ids[did]!=obj.data:raise CoreError('DUPLICATE_DATA_ID','Different data blocks share stable ID')
            data_ids[did]=obj.data
        objects.append(object_snapshot(obj))
    state=loaded_design();solutions=loaded_solutions()
    return {'snapshot_version':'1.0','scene':bpy.context.scene.name,'scene_units':scene_units,'frame':bpy.context.scene.frame_current,
            'objects':objects,'saved_solutions':solutions,'saved_solutions_sha256':digest(solutions),'design_state':state,'design_state_sha256':digest(state) if state else None,
            'generated_baseline':json.loads(bpy.context.scene.get('hs_generated_baseline','{}')),
            'manual_overlay':json.loads(bpy.context.scene.get('hs_manual_overlay','{}')),
            'blender_version':bpy.app.version_string,'blender_build_hash':bpy.app.build_hash.decode()}

def _projection(row):
    # Names and all-object material bindings can be reapplied; face assignments are protected.
    return {k:v for k,v in row.items() if k not in ('name','materials')}

def _preflight_target(obj):
    if obj.type!='MESH' or obj.library or obj.override_library:raise CoreError('TARGET_UNSUPPORTED','Only local Mesh targets supported')
    if obj.mode!='OBJECT' or obj.data.shape_keys or obj.animation_data or obj.constraints or obj.parent or obj.vertex_groups:raise CoreError('TARGET_DEFORMATION_UNSUPPORTED','Mode, parent, animation, deformation or constraints are unsupported')
    if len(obj.users_scene)!=1 or len(obj.users_collection)!=1 or any(c.users>1 for c in obj.users_collection):raise CoreError('SHARED_SCENE_CONFLICT','Target belongs to multiple scene or collection consumers')
    if obj.instance_type!='NONE':raise CoreError('INSTANCE_CONTEXT_UNSUPPORTED','Instance-producing target is unsupported')
    if obj.data.users>1:raise CoreError('SHARED_DATA_CONFLICT','Shared target mesh is rejected; no implicit write expansion')
    if any(m.show_render!=m.show_viewport for m in obj.modifiers):raise CoreError('EVALUATION_MISMATCH','Modifier viewport and render flags must agree')
    if any(m.type not in ('BOOLEAN','BEVEL','SOLIDIFY') or not m.name.startswith('HSW:') for m in obj.modifiers):raise CoreError('UNKNOWN_MODIFIER','Unmanaged modifier stack requires explicit preservation support')

def _apply_modifier(obj,modifier):
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
    if not bpy.ops.object.modifier_apply.poll():raise CoreError('CONTEXT_INVALID','modifier_apply poll failed')
    bpy.ops.object.modifier_apply(modifier=modifier.name)

def matrix_product(a,b):
    return [[sum(float(a[i][k])*float(b[k][j]) for k in range(4)) for j in range(4)] for i in range(4)]

def exact_delta(translation=(0,0,0),rotation_deg=(0,0,0),scale=(1,1,1)):
    x,y,z=[math.radians(float(v)) for v in rotation_deg];cx,sx=math.cos(x),math.sin(x);cy,sy=math.cos(y),math.sin(y);cz,sz=math.cos(z),math.sin(z)
    rx=[[1,0,0,0],[0,cx,-sx,0],[0,sx,cx,0],[0,0,0,1]];ry=[[cy,0,sy,0],[0,1,0,0],[-sy,0,cy,0],[0,0,0,1]];rz=[[cz,-sz,0,0],[sz,cz,0,0],[0,0,1,0],[0,0,0,1]]
    m=matrix_product(matrix_product(rz,ry),rx)
    for i in range(3):
        for j in range(3):m[i][j]*=float(scale[j])
        m[i][3]=float(translation[i])
    return m

def point_by_matrix(m,p):return [sum(float(m[i][j])*float(p[j]) for j in range(3))+float(m[i][3]) for i in range(3)]

class DomainExecutor:
    def __init__(self,request,job_dir,solved_sketches=None,plan=None,start_after_step_keys=None,base_design_state=None,reference_approval=None):
        self.request=request;self.params=request.get('params',request);self.job=Path(job_dir).resolve()
        self.job.mkdir(parents=True,exist_ok=True);self.solutions=solved_sketches or {};self.start=time.monotonic()
        if self.params['source']['kind']=='new_scene' and not bpy.data.filepath:
            # Only a fresh new scene receives display-unit configuration. A
            # loaded checkpoint/source keeps its settings and must be SI-scaled.
            unit=self.params['source']['length_unit'];settings=bpy.context.scene.unit_settings;settings.scale_length=1.0;settings.system='IMPERIAL' if unit=='in' else 'METRIC';settings.length_unit={'m':'METERS','cm':'CENTIMETERS','mm':'MILLIMETERS','in':'INCHES'}[unit]
        self.before=inspect_scene();self.saved_state=self.before['design_state']
        ctx=self.params['context']
        if bpy.context.scene.name!=ctx['scene']:raise CoreError('CONTEXT_SCENE','Loaded scene does not match declared scene')
        if bpy.context.view_layer.name!=ctx['view_layer']:raise CoreError('CONTEXT_VIEW_LAYER','Loaded view layer does not match declared view layer')
        bpy.context.scene.frame_set(ctx['frame'])
        from .planner import plan_request
        # The authoritative planner is called against saved state again inside Blender.
        planning_state=self.saved_state
        if start_after_step_keys:
            if not plan or not self.saved_state or digest(self.saved_state)!=digest(plan.get('resolved_design_state')):raise CoreError('RESUME_STATE_MISMATCH','Resume checkpoint design differs from exact plan')
            if self.params['design']['mode']=='create_if_absent':planning_state=None
            elif base_design_state is not None:planning_state=base_design_state
            elif self.params['design']['mode']=='use_saved':planning_state=self.saved_state
            else:raise CoreError('RESUME_BASE_STATE_REQUIRED','Patched design resume needs immutable original base_design_state')
        self.plan=plan_request(request,saved_state=planning_state,reference_approval=reference_approval)
        if plan:
            if digest(plan.get('request'))!=digest(self.plan.get('request')):raise CoreError('PLAN_MISMATCH','Host and Blender normalized requests differ')
            if digest(plan.get('reference_gate'))!=digest(self.plan.get('reference_gate')):raise CoreError('REFERENCE_CONFLICT','Host and Blender reference approvals differ')
            if digest(plan.get('resolved_design_state'))!=digest(self.plan.get('resolved_design_state')):raise CoreError('PLAN_MISMATCH','Host and Blender resolved design differ')
            if len(plan['steps'])!=len(self.plan['steps']):raise CoreError('PLAN_MISMATCH','Host and Blender step counts differ')
            for host_step,local_step in zip(plan['steps'],self.plan['steps']):
                hp=dict(host_step.get('effective_params',host_step));lp=dict(local_step.get('effective_params',local_step))
                if hp.get('op')=='boolean.apply_or_stack' and lp.get('op')==hp['op']:
                    new_solver=hp.get('solver','EXACT');old_solver=lp.get('solver','EXACT')
                    if new_solver!=old_solver:
                        allowed=self.params.get('execution',{}).get('technical_strategies',[])
                        allowed_text=json.dumps(allowed)
                        if new_solver not in ('EXACT','MANIFOLD') or ('boolean_'+new_solver.lower()) not in allowed_text:raise CoreError('STRATEGY_UNDECLARED','Boolean technical strategy was not predeclared')
                    hp.pop('solver',None);lp.pop('solver',None)
                if hp.get('op')==lp.get('op') and hp.get('op') in ('primitive.cylinder','profile.extrude','profile.revolve'):
                    changed=[k for k in ('segments','chord_tolerance') if hp.get(k)!=lp.get(k)]
                    if changed and 'tessellation_refine' not in self.params.get('execution',{}).get('technical_strategies',[]):raise CoreError('STRATEGY_UNDECLARED','Tessellation refinement was not predeclared')
                    for key in changed:
                        if key=='segments' and (type(hp[key]) is not int or not lp[key]<=hp[key]<=4096):raise CoreError('PLAN_MISMATCH','Refined segment count must increase within registered bound')
                        if key=='chord_tolerance' and (not isinstance(hp[key],(int,float)) or isinstance(hp[key],bool) or not 0<hp[key]<=lp[key]):raise CoreError('PLAN_MISMATCH','Refined chord tolerance must decrease while positive')
                    for key in ('segments','chord_tolerance'):
                        if key in hp and key in lp:hp.pop(key);lp.pop(key)
                if hp!=lp:raise CoreError('PLAN_MISMATCH','Host changed nontechnical effective parameters')
            self.plan=plan
        self.params=self.plan['request']['params'];self.state=self.plan['resolved_design_state'];self.scale=self.plan.get('length_scale',{'mm':.001,'cm':.01,'m':1,'in':.0254}.get(self.params['source'].get('length_unit','mm'),.001))
        self.project=self.params['source'].get('project_id',bpy.context.scene.get('hs_project_id','project'))
        self.outputs={};self.features={};self.steps=[];self.checkpoints=[];self.skip=set(start_after_step_keys or []);self.completed=[]
        if self.skip:
            keys=[s['step_key'] for s in self.plan['steps']]
            if set(keys[:len(self.skip)])!=self.skip:raise CoreError('RESUME_PREFIX','Only an exact completed prefix can be resumed')
            if set(json.loads(bpy.context.scene.get('hs_completed_steps','[]')))!=self.skip:raise CoreError('RESUME_PREFIX','Checkpoint completed steps differ from requested prefix')
        self.saved_step_records={r['step_key']:r for r in compact_step_records(json.loads(bpy.context.scene.get('hs_step_records','[]')))}
        self.selected_features={s['feature_id'] for s in self.plan['steps']}
        self.non_target_ids=set(self.params['protection'].get('non_target_object_ids',[]))
        self.protected_ids={r['object_id'] for r in self.before['objects'] if r['generated'] and (r['feature_id'] not in self.selected_features or r['object_id'] in self.non_target_ids)}
        self.before_manual={r['object_id'] or r['name']:r for r in self.before['objects'] if not r['generated'] or r['object_id'] in self.protected_ids} 
        self.overlay={};self.rebuild_objects=[]
        self.length_tolerance_m=float(self.params['quality']['length_tolerance'])*self.scale
        self.angle_tolerance_rad=math.radians(float(self.params['quality']['angle_tolerance_deg']))
        self.precision_report=self._qualify_precision()
        self._prepare_existing()
        bpy.context.scene['hs_project_id']=self.project
        self.persist_state()
    def _qualify_precision(self):
        # Conservative registered float32 envelope, checked before any removal or build.
        envelope=1e-6;angular=False
        for row in self.before['objects']:
            if row.get('feature_id') in self.selected_features and row.get('evaluated'):
                envelope=max(envelope,max(abs(v) for v in row['evaluated']['bounds_m']))
        fields={'primitive.box':('size','center'),'primitive.cylinder':('radius','depth','center'),'profile.extrude':('depth',),'edge.bevel':('width',),'shell.solidify':('thickness',),'pattern.radial':('center',),'pattern.linear':('offset',),'object.transform':('translation',)}
        for item in self.plan['steps']:
            p=item.get('effective_params',item);op=p['op'];values=[]
            for key in fields.get(op,()):
                v=p.get(key,0);values.extend(v if isinstance(v,list) else [v])
            extent=max([abs(float(v))*self.scale for v in values]+[0])
            if op=='pattern.linear':envelope+=extent*p['count']
            elif op=='object.transform':envelope=envelope*max(p.get('scale',[1,1,1]))+extent
            else:envelope=max(envelope,2*extent)
            if op.startswith('quad.'):
                bounds=item.get('geometry_estimate',{}).get('metadata',{}).get('bounds_mm',[])
                envelope=max(envelope,max([abs(float(v))*.001 for v in bounds]+[0]))
            if op in ('pattern.radial','profile.revolve','normal.finish') or (op=='object.transform' and any(p.get('rotation',[0,0,0]))):angular=True
        for solution in self.solutions.values():
            for profile in solution.get('profiles',{}).values():
                envelope=max(envelope,max([abs(float(v))*.001 for loop in profile.get('loops',[]) for point in loop for v in point]+[0]))
        minimum_length=max(1e-12,8*(2**-23)*envelope);minimum_angle=8*(2**-23)
        if self.length_tolerance_m<minimum_length:raise CoreError('GEOMETRY_PRECISION_UNSUPPORTED','Requested length tolerance is below the qualified float32 bound; no tolerance was widened',{'requested_m':self.length_tolerance_m,'qualified_minimum_m':minimum_length,'coordinate_envelope_m':envelope})
        if angular and self.angle_tolerance_rad<minimum_angle:raise CoreError('GEOMETRY_PRECISION_UNSUPPORTED','Requested angle tolerance is below the qualified float32 bound; no tolerance was widened',{'requested_rad':self.angle_tolerance_rad,'qualified_minimum_rad':minimum_angle})
        return {'requested_length_tolerance_m':self.length_tolerance_m,'requested_angle_tolerance_rad':self.angle_tolerance_rad,'qualified_minimum_length_m':minimum_length,'qualified_minimum_angle_rad':minimum_angle if angular else None,'coordinate_envelope_m':envelope,'scope':'registered positional/parameter witnesses, not exhaustive manufacturing wall/clearance certification'}
    def _length_check(self,actual,expected,label):
        residual=abs(float(actual)-float(expected))
        if residual>self.length_tolerance_m:raise CoreError('DIMENSION_TOLERANCE_FAILED',label,{'actual_m':actual,'expected_m':expected,'residual_m':residual,'tolerance_m':self.length_tolerance_m})
        return residual
    def _matrix_witness(self,obj,expected):
        bpy.context.view_layer.update();actual=[[float(v) for v in row] for row in obj.matrix_world]
        positional=max([math.dist(point_by_matrix(actual,v.co),point_by_matrix(expected,v.co)) for v in obj.data.vertices]+[0])
        if positional>self.length_tolerance_m:raise CoreError('TRANSFORM_TOLERANCE_FAILED','Actual transformed position exceeds declared tolerance',{'residual_m':positional,'tolerance_m':self.length_tolerance_m})
        angles=[]
        for col in range(3):
            a=[actual[i][col] for i in range(3)];b=[expected[i][col] for i in range(3)];den=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b));cosine=max(-1,min(1,sum(x*y for x,y in zip(a,b))/den));angles.append(math.acos(cosine))
        if max(angles)>self.angle_tolerance_rad:raise CoreError('ANGLE_TOLERANCE_FAILED','Actual transform basis exceeds declared angle tolerance',{'residual_rad':max(angles),'tolerance_rad':self.angle_tolerance_rad})
        return {'position_residual_m':positional,'angle_residual_rad':max(angles),'length_tolerance_m':self.length_tolerance_m,'angle_tolerance_rad':self.angle_tolerance_rad,'method':'independent float64 matrix and all base vertex positions'}
    def persist_state(self):
        bpy.context.scene['hs_design_state']=json.dumps(self.state,sort_keys=True,separators=(',',':'))
        bpy.context.scene['hs_design_sha256']=digest(self.state)
        encoded=json.dumps(self.solutions,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(encoded.encode('utf-8'))>2*1024*1024:raise CoreError('SOLUTION_STATE_LIMIT','Saved solver state exceeds 2 MiB')
        bpy.context.scene['hs_solutions']=encoded;bpy.context.scene['hs_solutions_sha256']=digest(self.solutions)
        loaded_solutions()
    def _prepare_existing(self):
        managed=[r for r in self.before['objects'] if r['generated'] and r['feature_id'] in self.selected_features]
        forbidden=[r['object_id'] for r in managed if r['object_id'] in self.non_target_ids]
        if forbidden:raise CoreError('PROTECTED_TARGET','Selected feature intersects explicitly protected object IDs',{'object_ids':forbidden})
        if self.skip:
            for row in managed:
                obj=next(o for o in bpy.context.scene.objects if o.get('hs_object_id')==row['object_id'])
                self.overlay[row['object_id']]={'name':obj.name,'materials':[slot.material for slot in obj.material_slots]}
            # Resume does not rebuild existing generated objects; restore output ports from provenance.
            for obj in bpy.context.scene.objects:
                if obj.get('hs_generated'):
                    raw=json.loads(obj.get('hs_provenance','{}'));key=raw.get('step_key')
                    if key:self.outputs.setdefault(key,{})[raw.get('port','body_object')]=obj
            for s in self.plan['steps']:
                key=s.get('step_key',s.get('feature_id','')+'/'+s['id'])
                if key in self.skip:
                    if s['op']=='sketch.solve':
                        sid=s.get('effective_params',s)['sketch_id'];solution=self.solutions.get(sid,{})
                        if not solution.get('accepted'):raise CoreError('RESUME_SKETCH_MISSING','Checkpoint continuation needs accepted saved sketch solution')
                        self.outputs[key]={pid:self.profile(sid,pid) for pid in solution.get('profiles',{})}
                    else:
                        for obj in bpy.context.scene.objects:
                            history=json.loads(obj.get('hs_output_history','[]'))
                            if key in history and not (obj.get('hs_step_key')==key and json.loads(obj.get('hs_provenance','{}')).get('port')!='body_object'):self.outputs[key]={'body_object':obj}
                    if key in self.outputs and 'body_object' in self.outputs[key]:self.features[s.get('feature_id','')]=self.outputs[key]
            return
        if not managed:return
        baseline=self.before['generated_baseline']
        for row in managed:
            oid=row['object_id'];obj=next(o for o in bpy.context.scene.objects if o.get('hs_object_id')==oid)
            _preflight_target(obj)
            old=baseline.get(oid)
            if old is None or _projection(old)!=_projection(row):raise CoreError('MANUAL_GENERATED_CONFLICT','Generated geometry, topology, transforms, stack or bindings differ from baseline',{'object_id':oid})
            self.overlay[oid]={'name':obj.name,'materials':[s.material for s in obj.material_slots]}
            self.rebuild_objects.append(obj)
        # Remove only managed candidate objects after all protection checks, never source file or manual objects.
        for obj in self.rebuild_objects:bpy.data.objects.remove(obj,do_unlink=True)
    def _identity(self,obj,step,port='body_object',suffix=''):
        key=step['step_key']+'/'+port+suffix
        obj['hs_object_id']=stable_id(self.project,'object/'+key);obj.data['hs_data_id']=stable_id(self.project,'mesh/'+key)
        obj['hs_generated']=True;obj['hs_feature_id']=step['feature_id'];obj['hs_step_key']=step['step_key']
        obj['hs_provenance']=json.dumps({'version':VERSION,'feature_id':step['feature_id'],'step_key':step['step_key'],'port':port,'parameters_sha256':digest(step.get('effective_params',step))},sort_keys=True)
        obj['hs_output_history']=json.dumps([step['step_key']]);return obj
    def resolve(self,ref,step):
        kind=ref['kind']
        if kind=='step_output':
            key=step['feature_id']+'/'+ref['step_id'];ports=self.outputs.get(key,{})
            if ref['port'] not in ports:raise CoreError('PORT_NOT_FOUND','Step port is absent',{'key':key,'port':ref['port']})
            return ports[ref['port']]
        if kind=='feature_ref':
            ports=self.features.get(ref['feature_id'],{})
            if ref['port'] not in ports:raise CoreError('PORT_NOT_FOUND','Feature port absent')
            return ports[ref['port']]
        if kind=='object_ref':
            found=[o for o in bpy.context.scene.objects if o.get('hs_object_id')==ref['object_id']]
            if len(found)!=1:raise CoreError('SELECTION_AMBIGUOUS','Object reference must resolve exactly once')
            o=found[0]
            if ref.get('data_id') and o.data.get('hs_data_id')!=ref['data_id']:raise CoreError('SELECTION_STALE','Data ID differs')
            if ref.get('type') and o.type!=ref['type']:raise CoreError('SELECTION_STALE','Object type differs')
            if ref.get('revision') is not None and ref['revision']!=self.state['revision']:raise CoreError('SELECTION_STALE','Design revision differs')
            if o.get('hs_object_id') in self.non_target_ids or o.get('hs_feature_id') not in self.selected_features:raise CoreError('PROTECTED_TARGET','Object reference is outside the declared selected feature write set')
            if not o.get('hs_generated'):raise CoreError('UNMANAGED_TARGET','Explicit adoption workflow is not supported; cannot mutate manual object')
            return o
        if kind=='sketch_profile':return self.profile(ref['sketch_id'],ref['profile_id'])
        raise CoreError('REFERENCE_UNSUPPORTED','Unsupported reference kind')
    def profile(self,sid,pid):
        solved=self.solutions.get(sid)
        if not solved or not solved.get('accepted'):raise CoreError('SKETCH_NOT_ACCEPTED','Profile requires accepted independent solver result',{'sketch_id':sid})
        profiles=solved.get('profiles',{})
        if isinstance(profiles,list):profiles={p['id']:p for p in profiles}
        p=profiles.get(pid)
        if not p or p.get('status')!='pass':raise CoreError('PROFILE_NOT_ACCEPTED','Profile missing or not verified')
        if p.get('coordinate_unit')!='mm':raise CoreError('PROFILE_UNITS','Solver output must explicitly use mm')
        if 'max_chord_error_mm' not in p or not isinstance(p['max_chord_error_mm'],(int,float)) or not math.isfinite(p['max_chord_error_mm']) or p['max_chord_error_mm']<0:raise CoreError('PROFILE_NOT_ACCEPTED','Profile lacks finite measured chord error')
        verify=solved.get('verification',{})
        if verify.get('status')!='pass':raise CoreError('SKETCH_NOT_ACCEPTED','Independent constraint verification is absent')
        for row in verify.get('constraints',[]):
            if row.get('mode')=='reference':continue
            residual=abs(float(row['verification_residual']))
            limit=self.length_tolerance_m*1000 if row['unit']=='mm' else math.degrees(self.angle_tolerance_rad) if row['unit']=='deg' else None
            if limit is None or residual>limit:raise CoreError('DESIGN_TOLERANCE_FAILED','Solved sketch residual exceeds requested geometry quality tolerance',{'constraint_id':row.get('constraint_id'),'residual':residual,'unit':row.get('unit'),'quality_tolerance':limit})
        return {'loops':[[[float(x)*.001,float(y)*.001] for x,y in loop] for loop in p['loops']], 'evidence':p,'sketch_id':sid,'profile_id':pid}
    def length(self,v):return float(v)*self.scale
    def vec(self,v):return [self.length(x) for x in v]
    def _new_primitive(self,step,p):
        if p['op']=='primitive.box':
            s=self.vec(p['size']);c=self.vec(p.get('center',[0,0,0]));verts=[(c[0]+sx*s[0]/2,c[1]+sy*s[1]/2,c[2]+sz*s[2]/2) for sx,sy,sz in [(-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),(1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1)]]
            obj=new_mesh(step['id'],verts,[(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)]);expected=math.prod(s)
        else:
            n=p.get('segments',64);r=self.length(p['radius']);d=self.length(p['depth']);c=self.vec(p.get('center',[0,0,0]))
            chord=r*(1-math.cos(math.pi/n))
            if chord>self.length_tolerance_m:raise CoreError('CURVE_TOLERANCE_FAILED','Cylinder chord error exceeds declared quality positional tolerance',{'chord_error_m':chord,'tolerance_m':self.length_tolerance_m,'segments':n})
            verts=[(c[0]+r*math.cos(2*math.pi*i/n),c[1]+r*math.sin(2*math.pi*i/n),c[2]+z*d/2) for z in (-1,1) for i in range(n)]
            faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
            obj=new_mesh(step['id'],verts,faces);expected=n*.5*r*r*math.sin(2*math.pi/n)*d
        self._identity(obj,step);metrics=require_closed(obj)
        if p['op']=='primitive.box' and any(check['id']=='quad_topology' for check in getattr(self,'plan',{}).get('required_checks',[])):
            attribute=obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
            surfaces=[]
            for poly in obj.data.polygons:
                attribute.data[poly.index].value=poly.index
                surfaces.append({'feature_id':step['feature_id'],'surface_id':'box_face_'+str(poly.index),
                    'surface_role':'box_face','construction':'primitive_box','source_face_corners':4,
                    'smooth':False,'curved':False,'support_band':False})
            obj['hs_quad_surface_table']=json.dumps(surfaces,sort_keys=True,separators=(',',':'))
            obj['hs_quad_constructor']='primitive.box'
        if not math.isclose(metrics['volume_m3'],expected,rel_tol=2e-5,abs_tol=1e-14):raise CoreError('DIMENSION_MISMATCH','Primitive volume differs from registered dimensions')
        if p['op']=='primitive.box':
            ideal=[c[k]-s[k]/2 for k in range(3)]+[c[k]+s[k]/2 for k in range(3)]
            residual=max(self._length_check(a,b,'Primitive box actual bound') for a,b in zip(metrics['bounds_m'],ideal))
            dimensional={'bounds_residual_m':residual,'tolerance_m':self.length_tolerance_m}
        else:
            radial=max(self._length_check(math.hypot(v.co.x-c[0],v.co.y-c[1]),r,'Cylinder sampled radius') for v in obj.data.vertices)
            axial=max(self._length_check(metrics['bounds_m'][2],c[2]-d/2,'Cylinder lower cap'),self._length_check(metrics['bounds_m'][5],c[2]+d/2,'Cylinder upper cap'))
            dimensional={'sampled_radius_residual_m':radial,'cap_position_residual_m':axial,'radial_chord_error_m':chord,'tolerance_m':self.length_tolerance_m}
        return {'body_object':obj},{'expected_volume_m3':expected,'actual':metrics,'dimensional_witness':dimensional}
    def _profile(self,step,p):
        profile=self.resolve(p['profile'],step)
        chord_limit=min(self.length_tolerance_m,self.length(p.get('chord_tolerance',.01)))
        source_chord=float(profile['evidence'].get('max_chord_error_mm',0))*.001
        if source_chord>chord_limit:raise CoreError('CURVE_TOLERANCE_FAILED','Solved profile chord error exceeds declared tolerance',{'chord_error_m':source_chord,'tolerance_m':chord_limit})
        if p['op']=='profile.revolve':
            maximum_radius=max(x for loop in profile['loops'] for x,y in loop);sweep_chord=maximum_radius*(1-math.cos(math.radians(p.get('angle',360))/p.get('segments',64)/2))
            if sweep_chord>chord_limit:raise CoreError('CURVE_TOLERANCE_FAILED','Revolve chord error exceeds declared tolerance',{'chord_error_m':sweep_chord,'tolerance_m':chord_limit})
        if p['op']=='profile.extrude':obj,evidence=create_extrusion(step['id'],profile['loops'],self.length(p['depth']))
        else:obj,evidence=create_revolution(step['id'],profile['loops'],p.get('axis','Z'),p.get('angle',360),p.get('segments',64))
        # Only registered datum frames; no arbitrary matrix guessing.
        sk=next((s for s in self.state.get('sketches',[]) if s['id']==profile['sketch_id']),{})
        wp=sk.get('workplane',{'kind':'datum','id':'world_xy'})
        if p['op']=='profile.extrude':
            if wp=={'kind':'datum','id':'world_xz'}:obj.matrix_world=Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
            elif wp=={'kind':'datum','id':'world_yz'}:obj.matrix_world=Matrix(((0,0,1,0),(1,0,0,0),(0,1,0,0),(0,0,0,1)))
            elif wp!={'kind':'datum','id':'world_xy'}:raise CoreError('WORKPLANE_UNSUPPORTED','Only world_xy, world_xz and world_yz extrusion datums implemented')
        self._identity(obj,step);m=require_closed(obj)
        if not math.isclose(m['volume_m3'],evidence['expected_volume_m3'],rel_tol=5e-5,abs_tol=1e-14):raise CoreError('PROFILE_MESH_VOLUME','Evaluated profile volume does not match independent polygon integral',{'actual':m['volume_m3'],'expected':evidence['expected_volume_m3']})
        if p['op']=='profile.extrude':
            depth=self.length(p['depth']);residual=max(self._length_check(min(v.co.z for v in obj.data.vertices),0,'Extrusion lower cap'),self._length_check(max(v.co.z for v in obj.data.vertices),depth,'Extrusion upper cap'))
            exact_points=[(x,y,z) for z in (0,depth) for loop in profile['loops'] for x,y in loop]
            if len(exact_points)!=len(obj.data.vertices):raise CoreError('PROFILE_VERTEX_MISMATCH','Profile construction vertex cardinality differs')
            coordinate_error=max(math.dist(v.co,point) for v,point in zip(obj.data.vertices,exact_points))
            if coordinate_error>self.length_tolerance_m:raise CoreError('DIMENSION_TOLERANCE_FAILED','Extruded profile vertex positions exceed tolerance')
            evidence['dimensional_witness']={'depth_residual_m':residual,'profile_position_residual_m':coordinate_error,'source_chord_error_m':source_chord,'tolerance_m':self.length_tolerance_m}
        else:
            radii_axial=[(x,y) for loop in profile['loops'] for x,y in loop];ax={'X':0,'Y':1,'Z':2}[p['axis']];radial_axes=[i for i in range(3) if i!=ax]
            residual=max(min(math.dist((math.hypot(v.co[radial_axes[0]],v.co[radial_axes[1]]),v.co[ax]),point) for point in radii_axial) for v in obj.data.vertices)
            if residual>self.length_tolerance_m:raise CoreError('DIMENSION_TOLERANCE_FAILED','Revolved profile radius/axis positions exceed tolerance')
            evidence['dimensional_witness']={'radius_axis_position_residual_m':residual,'source_chord_error_m':source_chord,'sweep_chord_error_m':sweep_chord,'tolerance_m':self.length_tolerance_m}
        evidence.update({'actual':m,'profile_evidence':profile['evidence']});return {'body_object':obj},evidence
    def _selection(self,obj,selection,step):
        if not selection:return None
        if selection['kind']=='feature_role':
            producer_key=step['feature_id']+'/'+selection['feature_step']
            producer=self.outputs.get(producer_key,{}).get('body_object')
            if producer is not obj:raise CoreError('SELECTION_STALE','Feature role producer does not match target object')
            if json.loads(obj.get('hs_output_history','[]'))!=[producer_key]:raise CoreError('SELECTION_STALE','Feature topology role was invalidated by downstream operations')
            if selection.get('role') not in ('all_edges','outer_boundary_edges'):raise CoreError('SELECTION_UNSUPPORTED','Feature role unsupported')
            if selection.get('role')=='outer_boundary_edges':raise CoreError('SELECTION_UNSUPPORTED','Outer-only boundary role is not yet qualified; cannot widen to every edge')
            return None
        if selection['kind']=='index_selection':
            current=mesh_metrics(obj,False)
            from .identity import validate_selection
            indexes=validate_selection(selection,{'object_id':obj['hs_object_id'],'data_id':obj.data['hs_data_id'],**current,'counts':{'EDGE':len(obj.data.edges),'FACE':len(obj.data.polygons),'VERTEX':len(obj.data.vertices)}})
            if selection['domain']!='EDGE':raise CoreError('SELECTION_DOMAIN','Bevel requires EDGE domain')
            return indexes
        raise CoreError('SELECTION_UNSUPPORTED','Selection kind unsupported')
    def _geometry_budget(self,phase,step_key):
        """Gate observed evaluated cardinalities, not Boolean transient memory.

        Avoid allocating Python vertex/face arrays or a bmesh just to count.
        Blender evaluation itself remains subject to the host process watchdog.
        """
        budget=self.params.get('budgets',{});limits={'vertices':budget.get('max_geometry_vertices',200000),'loops':budget.get('max_geometry_loops',1000000)}
        total={'vertices':0,'loops':0};bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
        for obj in bpy.context.scene.objects:
            if obj.type!='MESH':continue
            eo=obj.evaluated_get(dg);mesh=eo.to_mesh(preserve_all_data_layers=False,depsgraph=dg)
            try:
                total['vertices']+=len(mesh.vertices);total['loops']+=len(mesh.loops)
            finally:eo.to_mesh_clear()
            if any(total[field]>limits[field] for field in limits):
                raise CoreError('GEOMETRY_BUDGET','Evaluated geometry exceeds request budget',{'phase':phase,'step_key':step_key,'observed_at_least':total,'limits':limits,'last_object':obj.name})
        return {'phase':phase,'step_key':step_key,'observed':total,'limits':limits,'scope':'all evaluated scene meshes, including retained cutters'}
    def _modify(self,step,p):
        obj=self.resolve(p['target'],step);_preflight_target(obj);op=p['op'];evidence={}
        if obj.get('hs_quad_constructor') and op in ('boolean.apply_or_stack','edge.bevel','shell.solidify'):
            raise CoreError('QUAD_PROVENANCE_UNSUPPORTED','A topology-changing legacy modifier cannot retain structured surface provenance; use the corresponding declared quad domain parameters',{'operation':op,'object_id':obj.get('hs_object_id')})
        if op=='boolean.apply_or_stack':evidence['input_geometry_budget']=self._geometry_budget('before_boolean',step['step_key'])
        before=mesh_metrics(obj);evidence['before']=before
        if op=='boolean.apply_or_stack':
            cutter=self.resolve(p['cutter'],step);_preflight_target(cutter)
            if cutter==obj:raise CoreError('BOOLEAN_SELF','Boolean target and cutter must differ')
            require_closed(obj);require_closed(cutter)
            pending=[cutter];seen=set()
            while pending:
                dependency=pending.pop()
                if dependency==obj:raise CoreError('BOOLEAN_CYCLE','Boolean modifier dependency graph reaches target')
                if dependency in seen:continue
                seen.add(dependency)
                if len(seen)>len(bpy.context.scene.objects):raise CoreError('BOOLEAN_GRAPH_LIMIT','Boolean dependency graph exceeds scene object bound')
                for existing in dependency.modifiers:
                    if existing.type=='BOOLEAN' and existing.object is not None:pending.append(existing.object)
            mod=obj.modifiers.new('HSW:'+step['step_key'],'BOOLEAN');mod.object=cutter;mod.operation=p.get('operation','DIFFERENCE');mod.solver=p.get('solver','EXACT')
            try:evidence['output_geometry_budget']=self._geometry_budget('after_boolean',step['step_key'])
            except CoreError:
                obj.modifiers.remove(mod)
                raise
            after=require_closed(obj)
            if before['geometry_sha256']==after['geometry_sha256'] or math.isclose(before['volume_m3'],after['volume_m3'],rel_tol=1e-7,abs_tol=1e-15):obj.modifiers.remove(mod);raise CoreError('BOOLEAN_NO_OP','Boolean did not change evaluated volume')
            if mod.operation=='DIFFERENCE' and after['volume_m3']>=before['volume_m3']:raise CoreError('BOOLEAN_DIRECTION','Difference did not remove material')
            if mod.operation=='UNION' and after['volume_m3']<=before['volume_m3']:raise CoreError('BOOLEAN_DIRECTION','Union did not add material')
            if p.get('mode','modifier')=='mesh_apply':_apply_modifier(obj,mod)
            cutter.hide_render=True;cutter.hide_set(True);evidence['cutter_id']=cutter['hs_object_id']
        elif op=='edge.bevel':
            indices=self._selection(obj,p.get('selection'),step)
            if indices is not None:raise CoreError('SELECTION_UNSUPPORTED','Partial-edge bevel not yet qualified; rejected rather than widened')
            mod=obj.modifiers.new('HSW:'+step['step_key'],'BEVEL');mod.width=self.length(p['width']);mod.segments=p.get('segments',3);mod.limit_method='ANGLE';mod.angle_limit=math.radians(1);mod.offset_type='OFFSET';mod.use_clamp_overlap=True
            evidence['width_parameter_residual_m']=self._length_check(mod.width,self.length(p['width']),'Bevel stored width differs from request')
            clamped=mesh_metrics(obj);mod.use_clamp_overlap=False;unclamped=mesh_metrics(obj);mod.use_clamp_overlap=False
            if clamped['geometry_sha256']!=unclamped['geometry_sha256']:obj.modifiers.remove(mod);raise CoreError('BEVEL_CLAMP','Requested bevel width would be clamped; design width is unchanged and candidate rejected')
            if clamped['geometry_sha256']==before['geometry_sha256']:obj.modifiers.remove(mod);raise CoreError('BEVEL_NO_OP','Bevel has no evaluated geometric effect')
            require_closed(obj);evidence.update({'requested_width_m':self.length(p['width']),'clamp_detected':False,'width_acceptance_method':'actual evaluated clamped/unclamped geometry equivalence','global_self_intersection':'not_run'})
            if p.get('mode','modifier')=='mesh_apply':_apply_modifier(obj,mod)
        elif op=='shell.solidify':
            mod=obj.modifiers.new('HSW:'+step['step_key'],'SOLIDIFY');mod.thickness=self.length(p['thickness']);mod.offset=p.get('offset',-1);mod.use_even_offset=True;mod.use_quality_normals=True
            evidence['thickness_parameter_residual_m']=self._length_check(mod.thickness,self.length(p['thickness']),'Solidify stored thickness differs from request')
            after=require_closed(obj)
            if after['geometry_sha256']==before['geometry_sha256']:obj.modifiers.remove(mod);raise CoreError('SOLIDIFY_NO_OP','Solidify has no geometry effect')
            evidence['requested_thickness_m']=mod.thickness;evidence['thickness_exhaustive_check']='not_run';evidence['acceptance_scope']='closed evaluated output and finite geometric effect'
            flat_axes=[i for i,d in enumerate(before['dimensions_m']) if d<1e-10]
            if len(flat_axes)==1:
                measured=after['dimensions_m'][flat_axes[0]]
                self._length_check(measured,self.length(p['thickness']),'Planar shell actual thickness differs')
                evidence['measured_planar_thickness_m']=measured
            if p.get('mode','modifier')=='mesh_apply':_apply_modifier(obj,mod)
        elif op=='normal.finish':
            smooth=p.get('method','flat')=='smooth_by_angle'
            for poly in obj.data.polygons:poly.use_smooth=smooth
            if smooth:
                if not hasattr(obj.data,'set_sharp_from_angle'):raise CoreError('NORMAL_API_UNSUPPORTED','Native sharp-angle API unavailable')
                threshold=math.radians(p.get('angle',30));obj.data.set_sharp_from_angle(angle=threshold)
                bm=bmesh.new();bm.from_mesh(obj.data);bm.edges.ensure_lookup_table();sharp=obj.data.attributes.get('sharp_edge');examined=0;ambiguous=0
                for edge in bm.edges:
                    if not edge.is_manifold:continue
                    measured=edge.calc_face_angle();observed=bool(sharp.data[edge.index].value) if sharp else False;examined+=1
                    if abs(measured-threshold)<=self.angle_tolerance_rad:ambiguous+=1;continue
                    if observed!=(measured>threshold):bm.free();raise CoreError('ANGLE_TOLERANCE_FAILED','Native sharp-edge classification contradicts declared angle threshold')
                bm.free();evidence['normal_angle_witness']={'examined_manifold_edges':examined,'within_tolerance_threshold_edges':ambiguous,'threshold_rad':threshold,'angle_tolerance_rad':self.angle_tolerance_rad}
            obj.data.update();evidence['normal_policy']=p.get('method','flat')
        elif op=='object.transform':
            before_matrix=[[float(v) for v in row] for row in obj.matrix_world]
            translation=Vector(self.vec(p.get('translation',[0,0,0])));rot=Euler([math.radians(x) for x in p.get('rotation',[0,0,0])],'XYZ').to_matrix().to_4x4();scale=p.get('scale',[1,1,1])
            if any(s<=0 for s in scale):raise CoreError('TRANSFORM_SCALE','Only positive scale is supported')
            delta=Matrix.Translation(translation)@rot@Matrix.Diagonal([*scale,1])
            obj.matrix_world=(obj.matrix_world@delta) if p.get('space','WORLD')=='LOCAL' else (delta@obj.matrix_world)
            ideal_delta=exact_delta(self.vec(p.get('translation',[0,0,0])),p.get('rotation',[0,0,0]),scale)
            expected_matrix=matrix_product(before_matrix,ideal_delta) if p.get('space','WORLD')=='LOCAL' else matrix_product(ideal_delta,before_matrix)
            evidence['transform_witness']=self._matrix_witness(obj,expected_matrix)
        else:raise CoreError('OP_UNSUPPORTED',op)
        history=json.loads(obj.get('hs_output_history','[]'));history.append(step['step_key']);obj['hs_output_history']=json.dumps(history)
        evidence['after']=mesh_metrics(obj);return {'body_object':obj},evidence
    def _pattern(self,step,p):
        target=self.resolve(p['target'],step);_preflight_target(target);count=p['count']
        if not 2<=count<=128:raise CoreError('PATTERN_LIMIT','Pattern count must be 2..128')
        instances=[target];witnesses=[];initial_matrix=[[float(v) for v in row] for row in target.matrix_world]
        for i in range(1,count):
            obj=target.copy();obj.data=target.data.copy();bpy.context.scene.collection.objects.link(obj);self._identity(obj,step,'instances','/'+str(i));obj['hs_derived_from']=target['hs_object_id']
            if p['op']=='pattern.linear':
                obj.matrix_world=Matrix.Translation(Vector(self.vec(p['offset']))*i)@target.matrix_world
                ideal_delta=exact_delta([v*i for v in self.vec(p['offset'])])
            else:
                c=Vector(self.vec(p.get('center',[0,0,0])));angle=float(p.get('angle',360));den=count if abs(angle)==360 else count-1
                obj.matrix_world=Matrix.Translation(c)@Matrix.Rotation(math.radians(angle)*i/den,4,p.get('axis','Z'))@Matrix.Translation(-c)@target.matrix_world
                ideal_rotation=[0,0,0];ideal_rotation[{'X':0,'Y':1,'Z':2}[p.get('axis','Z')]]=angle*i/den
                ideal_delta=matrix_product(matrix_product(exact_delta(self.vec(p.get('center',[0,0,0]))),exact_delta(rotation_deg=ideal_rotation)),exact_delta([-x for x in self.vec(p.get('center',[0,0,0]))]))
            witnesses.append(self._matrix_witness(obj,matrix_product(ideal_delta,initial_matrix)));instances.append(obj)
        history=json.loads(target.get('hs_output_history','[]'));history.append(step['step_key']);target['hs_output_history']=json.dumps(history)
        return {'body_object':target,'instances':instances},{'instance_ids':[o['hs_object_id'] for o in instances],'count':count,'transform_witnesses':witnesses,'distinct_data':len(set(o.data.as_pointer() for o in instances))==count}
    def _checkpoint(self,step):
        quad_quality=self._quad_topology_check() if self.params['purpose']=='production' or any(s['op'].startswith('quad.') for s in self.plan['steps']) else None
        completed=[*self.completed,step['step_key']]
        summary={'state':'persisted_unverified','design_state_sha256':digest(self.state),'completed_step_keys':completed}
        if quad_quality is not None:summary['quad_quality']=quad_quality
        # The current checkpoint's semantic witness is saved before the .blend;
        # artifact hashes cannot be embedded in the file whose hash they name.
        records=[*self.steps,{'step_key':step['step_key'],'op':'checkpoint','status':'pass','evidence':summary}]
        self._persist_baseline();self._apply_overlay();bpy.context.scene['hs_completed_steps']=json.dumps(completed);bpy.context.scene['hs_step_records']=json.dumps(compact_step_records(records),sort_keys=True);snap=inspect_scene();protected={r['object_id'] or r['name']:r for r in snap['objects'] if not r['generated'] or r['object_id'] in self.protected_ids}
        if protected!=self.before_manual:raise CoreError('NON_TARGET_CHANGED','Checkpoint changed protected scene content')
        path=self.job/('checkpoint-'+str(len(self.checkpoints)+1).zfill(3)+'.blend')
        self._save_new(path);design_side=save_json_new(path.with_name(path.stem+'-design-state.json'),self.state)
        side=save_json_new(path.with_suffix('.json'),{'scene_snapshot':snap,'design_state_sha256':digest(self.state),'completed_step_keys':completed})
        r=compact_checkpoint_record({**file_record(path),'candidate':file_record(path),'producer_identity':PROCESS_IDENTITY,**summary,'sidecar':side,'design_state':design_side});r['report']=save_json_new(self.job/('checkpoint-'+str(len(self.checkpoints)+1).zfill(3)+'-report.json'),r);self.checkpoints.append(r);return {},r
    def _save_new(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.job) or path.exists():raise CoreError('OUTPUT_COLLISION','Only new files inside the owned job directory can be saved',{'path':str(path)})
        bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False,compress=False)
    def _persist_baseline(self):
        generated=[object_snapshot(o) for o in bpy.context.scene.objects if o.get('hs_generated')]
        records={r['object_id']:r for r in generated}
        for oid in self.overlay:
            if oid in records and oid in self.before['generated_baseline']:
                # Name/material bindings are not generator-owned steps in this registry.
                records[oid]['name']=self.before['generated_baseline'][oid]['name']
                records[oid]['materials']=self.before['generated_baseline'][oid]['materials']
        for oid in self.protected_ids:
            if oid in self.before['generated_baseline']:records[oid]=self.before['generated_baseline'][oid]
        bpy.context.scene['hs_generated_baseline']=json.dumps(records,sort_keys=True,separators=(',',':'))
    def _apply_overlay(self):
        from .preservation import require_merge
        def projection(row):
            value=dict(row);value['material_slots']=value.pop('materials',[]);return value
        baseline={oid:projection(row) for oid,row in self.before['generated_baseline'].items()}
        current={row['object_id'] or row['name']:projection(row) for row in self.before['objects']}
        desired={oid:projection(row) for oid,row in json.loads(bpy.context.scene['hs_generated_baseline']).items()}
        material_stable=[]
        for oid in self.overlay:
            b=baseline.get(oid,{});n=desired.get(oid,{})
            if b.get('material_indices')==n.get('material_indices') or (set(b.get('material_indices',[]))<={0} and set(n.get('material_indices',[]))<={0}):material_stable.append(oid)
        self.merge_report=require_merge(baseline,current,desired,write_set=set(desired)-self.protected_ids,context={'verified_manual_objects':[r['object_id'] or r['name'] for r in self.before['objects'] if not r['generated']],'material_face_assignments_stable':material_stable})
        public={}
        for obj in bpy.context.scene.objects:
            overlay=self.overlay.get(obj.get('hs_object_id'))
            if not overlay:continue
            if obj['hs_object_id'] not in material_stable and overlay['materials']:raise CoreError('MATERIAL_MAPPING_UNVERIFIED','Cannot carry material slots across changed face assignment')
            obj.name=overlay['name'];obj.data.materials.clear()
            for mat in overlay['materials']:
                if mat is not None:obj.data.materials.append(mat)
            public[obj['hs_object_id']]={'name':obj.name,'materials':[s.material.name if s.material else None for s in obj.material_slots]}
        bpy.context.scene['hs_manual_overlay']=json.dumps(public,sort_keys=True,separators=(',',':'))
    def _quad_topology_check(self):
        from .ops.quad_bridge import inspect_object
        objects=[o for o in bpy.context.scene.objects if o.type=='MESH' and not o.hide_render and not o.hide_viewport]
        if not objects:raise CoreError('CHECK_NO_WITNESS','No visible mesh for mandatory quad topology check')
        evidence=[];intersection_cache={}
        for obj in objects:
            for state in ('control','evaluated'):
                report=inspect_object(obj,state,intersection_cache)
                index=len(list(self.job.glob('quad-quality-*.json')))
                detail=save_json_new(self.job/('quad-quality-'+str(index).zfill(4)+'.json'),report)
                evidence.append({'object_id':obj.get('hs_object_id'),'feature_id':obj.get('hs_feature_id'),'mesh_state':state,'passed':report['passed'],'details':detail})
                if not report['passed']:raise CoreError('QUAD_QUALITY_FAILED','Actual mesh failed mandatory topology quality gates',evidence[-1])
        return {'status':'pass','method':'actual control and evaluated polygons; zero ngons, zero unapproved triangles, bounded metric and manifold gates','evidence':evidence}
    def check_registry(self):
        requested=set(self.params['quality']['required'])
        if self.params['purpose']=='production' or any(s['op'].startswith('quad.') for s in self.plan['steps']):requested.add('quad_topology')
        for unit in self.params['work_units']:requested.update(unit['checks'])
        records={};body_objects=[o for o in bpy.context.scene.objects if o.type=='MESH' and o.get('hs_generated') and o.get('hs_feature_id') in self.selected_features and not o.hide_render]
        geometry_steps=[s for s in self.steps if s.get('op','').startswith(('primitive.','profile.','quad.'))]
        for name in sorted(requested):
            if name in ('source_preserved','reopen','dependencies'):
                records[name]={'status':'not_run','owner':'host','method':'source guards, declared closure and independent subprocess reopen'};continue
            if name=='preservation':records[name]={'status':'pass','method':'B/C/N/M merge and exact non-target snapshot comparison'};continue
            if name=='quad_topology':
                records[name]=self._quad_topology_check();continue
            if name=='reference_consistency':raise CoreError('CHECK_UNSUPPORTED','Reference visual consistency requires approved production review; numeric core cannot declare pass')
            if name in ('closed_mesh','volume','normals'):
                if not body_objects:raise CoreError('CHECK_NO_WITNESS','No generated body for required check '+name)
                evidence=[]
                for obj in body_objects:
                    m=require_closed(obj)
                    if name=='normals' and (m['signed_volume_m3']<=0 or m['inconsistent_winding_edges']):raise CoreError('NORMAL_ORIENTATION','Signed evaluated volume is not outward')
                    evidence.append({'object_id':obj['hs_object_id'],'metrics':m})
                records[name]={'status':'pass','method':'evaluated finite closed topology and signed volume','evidence':evidence};continue
            if name=='constraint_residuals':
                consumed={s.get('effective_params',s).get('sketch_id') for s in self.plan['steps'] if s['op']=='sketch.solve'}
                for s in self.plan['steps']:
                    p=s.get('effective_params',s)
                    if p.get('profile',{}).get('kind')=='sketch_profile':consumed.add(p['profile']['sketch_id'])
                if not consumed:raise CoreError('CHECK_NO_WITNESS','Constraint residuals requested but no sketch consumed')
                evidence={sid:self.solutions.get(sid,{}).get('verification') for sid in consumed}
                if any(not self.solutions.get(sid,{}).get('accepted') or not isinstance(v,dict) or v.get('status')!='pass' for sid,v in evidence.items()):raise CoreError('CONSTRAINT_VERIFICATION','Required independent residual verification is absent')
                records[name]={'status':'pass','method':'independent solver adapter residual verifier for every consumed sketch','evidence':evidence};continue
            if name=='profile_validity':
                profile_steps=[s for s in geometry_steps if s['op'].startswith('profile.')]
                if not profile_steps:raise CoreError('CHECK_NO_WITNESS','Profile check requested but no profile was consumed')
                records[name]={'status':'pass','method':'independent segment intersections, hole containment, cap area and evaluated volume integral','step_keys':[s['step_key'] for s in profile_steps]};continue
            if name=='design_dimensions':
                if not geometry_steps and not self.skip:raise CoreError('CHECK_NO_WITNESS','No dimensional construction witnesses')
                shell=[s for s in self.steps if s.get('op')=='shell.solidify']
                if any('measured_planar_thickness_m' not in s['evidence'] for s in shell):raise CoreError('CHECK_UNSUPPORTED','Exhaustive nonplanar solidify thickness dimension validation is not qualified')
                records[name]={'status':'pass','method':'primitive exact polygon volume, profile integration, actual depth/bounds, bevel no-clamp equivalence; planar shell thickness when present','step_keys':[s['step_key'] for s in geometry_steps+shell],'scope':'dimensions consumed by registered operations, not manufacture certification'};continue
            raise CoreError('CHECK_UNSUPPORTED','Required check is not implemented: '+name)
        return records
    def run(self,candidate_path=None,stop_after_step_key=None):
        if stop_after_step_key and not any(s['step_key']==stop_after_step_key and s['op']=='checkpoint' for s in self.plan['steps']):raise CoreError('CHECKPOINT_STOP_INVALID','Stop key must name a planned checkpoint operation')
        for step in self.plan['steps']:
            key=step.get('step_key',step['feature_id']+'/'+step['id']);step={**step,'step_key':key};p=step.get('effective_params',step)
            if key in self.skip:
                self.completed.append(key);self.steps.append({**self.saved_step_records.get(key,{'step_key':key,'op':p['op'],'status':'pass','evidence':{}}),'reused_checkpoint':True});continue
            if time.monotonic()-self.start>self.params.get('budgets',{}).get('wall_seconds',180):raise CoreError('WALL_BUDGET','Core work budget elapsed')
            t=time.monotonic();op=p['op']
            if op=='checkpoint':self._geometry_budget('before_checkpoint',key)
            if op=='sketch.solve':
                sid=p['sketch_id'];solution=self.solutions.get(sid)
                if not solution or not solution.get('accepted'):raise CoreError('SKETCH_NOT_ACCEPTED','No accepted solver result')
                ports={pid:self.profile(sid,pid) for pid in solution.get('profiles',{})};ev={k:solution.get(k) for k in ('status','dof','dof_status','verification','branch_signature','backend')}
            elif op.startswith('quad.'):
                from .ops.quad_bridge import create
                obj,ev=create(p,step['feature_id'],step['id'])
                self._identity(obj,step);ports={'body_object':obj}
                expected=step.get('geometry_estimate',{}).get('construction_sha256')
                if expected!=ev['construction_sha256']:raise CoreError('QUAD_PLAN_MISMATCH','Host and Blender structured constructions differ')
            elif op.startswith('primitive.'):ports,ev=self._new_primitive(step,p)
            elif op.startswith('profile.'):ports,ev=self._profile(step,p)
            elif op in ('boolean.apply_or_stack','edge.bevel','shell.solidify','normal.finish','object.transform'):ports,ev=self._modify(step,p)
            elif op.startswith('pattern.'):ports,ev=self._pattern(step,p)
            elif op=='checkpoint':ports,ev=self._checkpoint(step)
            elif op=='measure':
                obj=self.resolve(p['target'],step);ports={'measurement':mesh_metrics(obj)};ev=ports['measurement']
                for check in p.get('checks',[]):
                    if check=='closed_mesh':require_closed(obj)
                    elif check not in ('bounds','volume','dimensions','finite','manifold','design_dimensions','normals'):raise CoreError('CHECK_UNSUPPORTED','Measure check not implemented: '+check)
                    elif check=='finite' and not ev['finite']:raise CoreError('FINITE_FAILED','Nonfinite geometry')
            else:raise CoreError('OP_UNSUPPORTED','Operation unsupported: '+op)
            geometry_budget=self._geometry_budget('after_step',key)
            self.outputs[key]=ports
            if 'body_object' in ports:self.features[step['feature_id']]=ports
            self.completed.append(key);self.steps.append({'step_key':key,'op':op,'status':'pass','seconds':time.monotonic()-t,'evidence':ev,'geometry_budget':geometry_budget})
            if key==stop_after_step_key:
                cp=self.checkpoints[-1]
                return {'core_version':VERSION,'producer_identity':PROCESS_IDENTITY,'outcome':'checkpoint_persisted_unverified','execution_state':'checkpoint_persisted_unverified','candidate':cp['candidate'],'design_state':cp['design_state'],'design_state_sha256':digest(self.state),'scene_snapshot':read_json_reference(cp['sidecar'])['scene_snapshot'],'checkpoints':self.checkpoints,'completed_step_keys':self.completed,'steps':self.steps,'remaining_step_keys':[s['step_key'] for s in self.plan['steps'] if s['step_key'] not in self.completed],'preservation_merge':self.merge_report,'checks':{'completed_operations':{'status':'pass','scope':self.completed},'preservation':{'status':'pass','method':'checkpoint exact non-target snapshot and B/C/N/M merge'}},'acceptance':{'technical':'pass','preservation':'pass','reopen':'not_run','visual':'not_run','user_feedback':'not_run'},'partial_checkpoint':True}
        self._persist_baseline();self._apply_overlay();bpy.context.scene['hs_completed_steps']=json.dumps(self.completed);bpy.context.scene['hs_step_records']=json.dumps(compact_step_records(self.steps),sort_keys=True);after=inspect_scene()
        manual={r['object_id'] or r['name']:r for r in after['objects'] if not r['generated'] or r['object_id'] in self.protected_ids}
        if manual!=self.before_manual:raise CoreError('NON_TARGET_CHANGED','Non-target scene objects differ from source')
        self.checks=self.check_registry()
        candidate=Path(candidate_path) if candidate_path else self.job/'candidate.blend';self._save_new(candidate)
        state_file=save_json_new(self.job/'design-state.json',self.state)
        return {'core_version':VERSION,'producer_identity':PROCESS_IDENTITY,'outcome':'persisted_unverified','execution_state':'persisted_unverified','steps':self.steps,'candidate':file_record(candidate),'design_state':state_file,'design_state_sha256':digest(self.state),'scene_snapshot':after,'checkpoints':self.checkpoints,'completed_step_keys':self.completed,'preservation_merge':self.merge_report,'precision_policy':self.precision_report,'checks':self.checks,'acceptance':{'technical':'pass','preservation':'pass','reopen':'not_run','visual':'not_applicable' if self.params.get('purpose')=='contract_fixture' else 'not_run','user_feedback':'not_applicable' if self.params.get('purpose')=='contract_fixture' else 'not_run'},'elapsed_seconds':time.monotonic()-self.start,'evaluation':{'mode':'depsgraph_viewport','render_equivalence':'modifier show_viewport/show_render checked equal'},'limitations':['No continuous or exhaustive self-intersection proof','Generic solidify thickness is not exhaustively measured','Outer-only and partial-edge bevel selections reject until qualified']}

def execute_request(request,*,job_dir,solved_sketches=None,plan=None,start_after_step_keys=None,candidate_path=None,stop_after_step_key=None,base_design_state=None,reference_approval=None):
    executor=DomainExecutor(request,job_dir,solved_sketches,plan,start_after_step_keys,base_design_state,reference_approval)
    try:return executor.run(candidate_path,stop_after_step_key)
    except Exception as exc:
        exc.partial_report={'checkpoints':executor.checkpoints,'steps':executor.steps,'completed_step_keys':executor.completed,'producer_identity':PROCESS_IDENTITY}
        raise

def verify_saved_candidate(expected_report):
    # The host validates receipt sidecars; the independent reader compares the
    # complete top-level snapshot so archived verification has no origin paths.
    expected=expected_report.get('scene_snapshot',expected_report);actual=inspect_scene()
    # Build metadata is exact for this qualification; all object/domain projections are compared.
    if actual!=expected:raise CoreError('REOPEN_MISMATCH','Independent reopen scene snapshot differs',{'expected_sha256':digest(expected),'actual_sha256':digest(actual)})
    return {'outcome':'pass','independent_reopen':True,'candidate':file_record(bpy.data.filepath),'producer_identity':expected_report.get('producer_identity'),'verifier_identity':PROCESS_IDENTITY,'checks':{'technical':'pass','preservation':'pass','dependency_reproduction':'pass','state_consistency':'pass'},'acceptance':{'reopen':'pass','preservation':'pass'},'scene_snapshot_sha256':digest(actual),'design_state_sha256':actual['design_state_sha256'],'object_count':len(actual['objects']),'blender_version':bpy.app.version_string,'blender_build_hash':bpy.app.build_hash.decode()}
