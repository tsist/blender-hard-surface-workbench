"""Authored isolated numeric fixtures only, never a practical production model."""
import sys,json,math,traceback,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import bpy
from hardsurface.core import DomainExecutor,inspect_scene,save_json_new,PROCESS_IDENTITY
from hardsurface.ops.geometry import *
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
RESULT=[]
def clear():bpy.ops.wm.read_factory_settings(use_empty=True)
def executor():
    e=DomainExecutor.__new__(DomainExecutor);e.params={'budgets':{}};e.scale=.001;e.project='numeric-fixture';e.outputs={};e.features={};e.state={'sketches':[]};e.length_tolerance_m=.01*.001;e.angle_tolerance_rad=math.radians(.001);return e
def step(id,op,**kw):return {'id':id,'op':op,'feature_id':'fixture','step_key':'fixture/'+id,**kw}
def box(e,id,size=(20,20,20),center=(0,0,0)):
    s=step(id,'primitive.box',size=list(size),center=list(center));ports,ev=e._new_primitive(s,s);e.outputs[s['step_key']]=ports;return ports['body_object']
def ref(id):return {'kind':'step_output','step_id':id,'port':'body_object'}
def call(e,id,op,**params):
    s=step(id,op,**params);ports,ev=e._modify(s,s);e.outputs[s['step_key']]=ports;return ports,ev
def close(a,b,rel=1e-5):assert math.isclose(a,b,rel_tol=rel,abs_tol=1e-12),(a,b)
def case(name,fn):
    clear();t=time.monotonic()
    try:
        evidence=fn();RESULT.append({'test':name,'status':'pass','seconds':time.monotonic()-t,'evidence':evidence})
    except Exception as exc:RESULT.append({'test':name,'status':'fail','error':str(exc),'traceback':traceback.format_exc()})
def reject(code,fn):
    try:fn()
    except GeometryError as exc:assert exc.code==code,(exc.code,code);return {'rejected':code}
    raise AssertionError('Expected rejection '+code)
def primitive():
    e=executor();o=box(e,'box',(80,50,5));m=require_closed(o)
    for a,b in zip(m['dimensions_m'],(.08,.05,.005)):close(a,b)
    close(m['volume_m3'],.08*.05*.005);return m
case('primitive_box_evaluated_dimensions_volume',primitive)
def cylinder():
    e=executor();s=step('cyl','primitive.cylinder',radius=5,depth=8,segments=64);ports,ev=e._new_primitive(s,s);m=require_closed(ports['body_object']);close(m['dimensions_m'][2],.008);return ev
case('primitive_cylinder_chordal_volume',cylinder)
outer=[[0,0],[.08,0],[.08,.05],[0,.05]]
hole=[[.02+.005*math.cos(-i*2*math.pi/64),.025+.005*math.sin(-i*2*math.pi/64)] for i in range(64)]
def extrude():
    o,ev=create_extrusion('extruded-hole',[outer,hole],.005);m=require_closed(o);close(m['volume_m3'],ev['expected_volume_m3']);hit=o.ray_cast(Vector((.02,.025,-.01)),Vector((0,0,1)))[0];assert not hit;return {'metrics':m,'hole_center_ray_hit':hit,'profile':ev}
case('profile_extrude_hole_cap_area_and_void',extrude)
case('profile_nested_hole_fail_closed',lambda:reject('PROFILE_NESTED_HOLES',lambda:validate_profile([outer,[[.01,.01],[.01,.04],[.04,.04],[.04,.01]],[[.02,.02],[.02,.03],[.03,.03],[.03,.02]]])))
case('profile_self_intersection_rejected',lambda:reject('PROFILE_SELF_INTERSECTION',lambda:validate_profile([[[0,0],[1,1],[0,1],[1,0]]])))
def revolve(angle):
    o,ev=create_revolution('revolve',[[[.01,0],[.02,0],[.02,.03],[.01,.03]]],'Z',angle,64);m=require_closed(o);close(m['volume_m3'],ev['expected_volume_m3'],5e-5);return {'metrics':m,'integral':ev}
case('profile_revolve_full_annulus',lambda:revolve(360));case('profile_revolve_partial_capped',lambda:revolve(120))
case('revolve_axis_crossing_rejected',lambda:reject('REVOLVE_AXIS_CROSSING',lambda:create_revolution('bad',[[[-1,0],[1,0],[1,1],[-1,1]]],'Z',360,32)))
def boolean():
    e=executor();body=box(e,'body');cut=box(e,'cut',(10,10,30));ports,ev=call(e,'subtract','boolean.apply_or_stack',target=ref('body'),cutter=ref('cut'),operation='DIFFERENCE',solver='EXACT',mode='modifier');close(mesh_metrics(body)['volume_m3'],.02**3-.01*.01*.02);return ev
case('boolean_difference_actual_removed_volume',boolean)
def boolean_noop():
    e=executor();box(e,'body');box(e,'cut',(10,10,10),(100,0,0));return reject('BOOLEAN_NO_OP',lambda:call(e,'bad','boolean.apply_or_stack',target=ref('body'),cutter=ref('cut'),operation='DIFFERENCE',solver='EXACT'))
case('boolean_disjoint_noop_rejected',boolean_noop)
def boolean_budget(limit,field,phase):
    e=executor();body=box(e,'body');box(e,'cut',(10,10,30));e.params['budgets'][field]=limit
    try:call(e,'subtract','boolean.apply_or_stack',target=ref('body'),cutter=ref('cut'),operation='DIFFERENCE',solver='EXACT',mode='mesh_apply')
    except GeometryError as exc:
        assert exc.code=='GEOMETRY_BUDGET',exc.code
        assert exc.details['phase']==phase,exc.details
        assert not body.modifiers,'Over-limit Boolean modifier must not be retained or applied'
        assert len(body.data.vertices)==8,'Over-limit Boolean must not be applied to base mesh'
        return {'rejected':exc.code,**exc.details}
    raise AssertionError('Expected evaluated Boolean budget rejection')
case('boolean_actual_input_vertex_budget_before_modifier',lambda:boolean_budget(15,'max_geometry_vertices','before_boolean'))
case('boolean_actual_output_vertex_budget_before_apply',lambda:boolean_budget(20,'max_geometry_vertices','after_boolean'))
case('boolean_actual_output_loop_budget_before_apply',lambda:boolean_budget(72,'max_geometry_loops','after_boolean'))
def bevel():
    e=executor();o=box(e,'body');ports,ev=call(e,'bevel','edge.bevel',target=ref('body'),width=1,segments=3,mode='modifier');assert len(o.modifiers)==1;assert mesh_metrics(o)['volume_m3']<.02**3
    # Actual cube bevel offset must expose x=halfsize-width on unchanged y/z face planes.
    dg=bpy.context.evaluated_depsgraph_get();eo=o.evaluated_get(dg);me=eo.to_mesh();measured=min(abs(abs(v.co.x)-.009) for v in me.vertices);eo.to_mesh_clear();assert measured<1e-7;ev['cube_offset_residual_m']=measured;return ev
case('bevel_unclamped_actual_cube_offset',bevel)
def clamp():
    e=executor();box(e,'body');return reject('BEVEL_CLAMP',lambda:call(e,'bevel','edge.bevel',target=ref('body'),width=15,segments=3,mode='modifier'))
case('bevel_design_width_clamp_rejected',clamp)
def solidify():
    e=executor();o=new_mesh('plane',[(0,0,0),(.02,0,0),(.02,.03,0),(0,.03,0)],[(0,1,2,3)]);e._identity(o,step('plane','primitive.box'));e.outputs['fixture/plane']={'body_object':o};ports,ev=call(e,'shell','shell.solidify',target=ref('plane'),thickness=2,offset=-1,mode='modifier');m=require_closed(o);close(m['dimensions_m'][2],.002);close(m['volume_m3'],.02*.03*.002);ev['plane_actual_thickness_m']=m['dimensions_m'][2];return ev
case('solidify_actual_planar_thickness',solidify)
def pattern(op):
    e=executor();box(e,'body',center=(20,0,0));s=step('array',op,target=ref('body'),count=4,offset=[30,0,0],axis='Z',angle=360,center=[0,0,0]);ports,ev=e._pattern(s,s);assert len(ports['instances'])==4;ids=[o['hs_object_id'] for o in ports['instances']];assert len(set(ids))==4;return ev
case('linear_pattern_unique_editable_ids',lambda:pattern('pattern.linear'));case('radial_pattern_unique_editable_ids',lambda:pattern('pattern.radial'))
def transform():
    e=executor();o=box(e,'body',(20,10,5));ports,ev=call(e,'xform','object.transform',target=ref('body'),translation=[100,0,0],rotation=[0,0,90],scale=[1,1,1]);m=mesh_metrics(o);close(m['dimensions_m'][0],.01);close(m['dimensions_m'][1],.02);close((m['bounds_m'][0]+m['bounds_m'][3])/2,.1);return ev
case('explicit_world_transform_bounds',transform)
def normals():
    e=executor();o=box(e,'body');_,ev=call(e,'normal','normal.finish',target=ref('body'),method='smooth_by_angle',angle=30);assert all(p.use_smooth for p in o.data.polygons);assert o.data.attributes.get('sharp_edge');return ev
case('native_normal_finish_angle_marks',normals)
# Integration through the real planner/core, isolated fixtures only.
from hardsurface.core import execute_request,CoreError,digest,file_record
import copy
BASE=json.loads((ROOT/'fixtures/box.json').read_text())
def fresh_request(name):
    request=copy.deepcopy(BASE);request['params']['request_id']=name;return request
def initialize_request(request):
    bpy.context.scene.name=request['params']['context']['scene']
    return execute_request(request,job_dir=OUT/request['params']['request_id'])
def saved_request(original,report,name,features=None):
    request=copy.deepcopy(original);p=request['params'];p['request_id']=name;p['source']={'kind':'saved_blend','file':report['candidate']['file'],'expected_sha256':report['candidate']['sha256'],'bytes':report['candidate']['bytes'],'length_unit':'mm'};p['design']={'mode':'use_saved','expected_revision':1}
    if features:p['work_units'][0]['feature_ids']=features
    return request
def subset_preservation():
    r=fresh_request('subset-first');r['params']['design']['state']['features'].append({'id':'sibling','program':{'kind':'steps','steps':[{'id':'body','op':'primitive.box','size':[7,8,9],'center':[50,0,0]}]}});r['params']['work_units'][0]['feature_ids'].append('sibling');first=initialize_request(r)
    sibling=next(o for o in bpy.context.scene.objects if o.get('hs_feature_id')=='sibling');sibling.location.y=.123;sibling['user_note']='protected custom note';before=object_snapshot(sibling)
    manual=new_mesh('manual',[(0,0,0),(.1,0,0),(0,.1,0)],[(0,1,2)]);manual['note']='manual';manual_before=object_snapshot(manual)
    second=execute_request(saved_request(r,first,'subset-second',['block']),job_dir=OUT/'subset-second')
    assert object_snapshot(sibling)==before;assert object_snapshot(manual)==manual_before
    return {'unselected_generated_preserved':True,'independent_manual_preserved':True,'merge':second['preservation_merge']['status']}
from hardsurface.core import object_snapshot
case('selected_feature_only_rebuild_preserves_protected_sibling',subset_preservation)
def metadata_reject(data=False,key='user_note'):
    suffix=str(data)+'-'+key;r=fresh_request('meta-first-'+suffix);first=initialize_request(r);o=next(iter(bpy.context.scene.objects));(o.data if data else o)[key]='must not vanish'
    return reject('MANUAL_GENERATED_CONFLICT',lambda:execute_request(saved_request(r,first,'meta-next-'+suffix),job_dir=OUT/('meta-next-'+suffix)))
case('unknown_object_custom_edit_fail_closed',lambda:metadata_reject(False));case('unknown_mesh_custom_edit_fail_closed',lambda:metadata_reject(True))
def protected_reject():
    r=fresh_request('protected-first');first=initialize_request(r);o=next(iter(bpy.context.scene.objects));r2=saved_request(r,first,'protected-next');r2['params']['protection']['non_target_object_ids']=[o['hs_object_id']]
    return reject('PROTECTED_TARGET',lambda:execute_request(r2,job_dir=OUT/'protected-next'))
case('explicit_non_target_cannot_be_rebuilt',protected_reject)
def rename_preserve():
    r=fresh_request('rename-first');first=initialize_request(r);o=next(iter(bpy.context.scene.objects));o.name='Kept manual display name';mat=bpy.data.materials.new('Manual slot');o.data.materials.append(mat)
    second=execute_request(saved_request(r,first,'rename-second'),job_dir=OUT/'rename-second');o=next(iter(bpy.context.scene.objects));assert o.name=='Kept manual display name';assert o.data.materials[0]==mat;baseline=json.loads(bpy.context.scene['hs_generated_baseline']);assert baseline[o['hs_object_id']]['name']!='Kept manual display name';assert baseline[o['hs_object_id']]['materials']==[]
    return {'name_preserved':True,'material_preserved':True,'baseline_remains_pure_generator':True,'merge':second['preservation_merge']['status']}
case('name_slot_overlay_preserved_without_absorbing_baseline',rename_preserve)
def required_check_reject():
    r=fresh_request('required-profile');r['params']['quality']['required'].append('profile_validity')
    try:initialize_request(r)
    except Exception as exc:
        assert getattr(exc,'code','') in ('CHECK_NO_WITNESS','CHECK_NOT_APPLICABLE','REQUIRED_CHECK_UNSUPPORTED','CHECK_SCOPE_EMPTY','INVALID_REQUEST','CHECK_UNSUPPORTED'),getattr(exc,'code','');return {'code':getattr(exc,'code',''),'rejected':True}
    raise AssertionError('Inapplicable check accepted')
case('required_profile_check_without_profile_rejected',required_check_reject)
def checkpoint():
    r=fresh_request('checkpoint-build');steps=r['params']['design']['state']['features'][0]['program']['steps'];steps.extend([{'id':'checkpoint','op':'checkpoint','depends_on':['body'],'label':'numeric fixture'},{'id':'move','op':'object.transform','target':{'kind':'step_output','step_id':'body','port':'body_object'},'translation':[5,0,0],'depends_on':['checkpoint']}]);result=initialize_request(r);cp=result['checkpoints'][0];assert cp['state']=='persisted_unverified';assert cp['candidate']['file']!=result['candidate']['file'];return {'checkpoint':cp['candidate'],'sidecar':cp['report'],'completed_step_keys':cp['completed_step_keys']}
case('checkpoint_actual_new_file_and_reopen_evidence',checkpoint)
def local_transform():
    e=executor();o=box(e,'body',(20,10,5));call(e,'turn','object.transform',target=ref('body'),rotation=[0,0,90]);call(e,'localmove','object.transform',target=ref('body'),translation=[5,0,0],space='LOCAL');m=mesh_metrics(o)
    measured=[(m['bounds_m'][i]+m['bounds_m'][i+3])/2 for i in range(3)]
    # Blender mesh coordinates/matrices are float32. Bounding opposing faces can
    # differ by a few ulps even when an intended zero translation is exact.
    # This fixture bound is below the existing 0.01 mm contract tolerance.
    roundoff_bound=8*(2**-23)*max(m['dimensions_m']);declared_tolerance_m=.01*.001
    assert roundoff_bound<declared_tolerance_m
    residual=max(abs(measured[i]-[0,.005,0][i]) for i in range(3));assert residual<=roundoff_bound,(residual,roundoff_bound)
    return {'actual_local_translation_world_center_m':measured,'max_residual_m':residual,'float32_bbox_roundoff_bound_m':roundoff_bound,'unchanged_contract_tolerance_m':declared_tolerance_m}
case('local_transform_composes_in_object_basis',local_transform)
def role_mismatch():
    e=executor();box(e,'a');box(e,'b',center=(50,0,0));return reject('SELECTION_STALE',lambda:call(e,'bevel','edge.bevel',target=ref('a'),width=1,selection={'kind':'feature_role','feature_step':'b','role':'all_edges'}))
case('feature_role_wrong_producer_rejected',role_mismatch)
def stale_object_ref():
    e=executor();e.state['revision']=2;e.non_target_ids=set();e.selected_features={'fixture'};o=box(e,'a');return reject('SELECTION_STALE',lambda:call(e,'bad','normal.finish',target={'kind':'object_ref','object_id':o['hs_object_id'],'revision':1},method='flat'))
case('object_ref_stale_revision_rejected',stale_object_ref)
def shared_scene():
    e=executor();o=box(e,'a');other=bpy.data.scenes.new('Protected other scene');other.collection.objects.link(o);return reject('SHARED_SCENE_CONFLICT',lambda:call(e,'bad','edge.bevel',target=ref('a'),width=1))
case('multi_scene_target_rejected',shared_scene)
def wrong_context():
    r=fresh_request('wrong-context');bpy.context.scene.name='Actual source scene';return reject('CONTEXT_SCENE',lambda:execute_request(r,job_dir=OUT/'wrong-context'))
case('explicit_scene_mismatch_rejected_before_mutation',wrong_context)
def shader_script_reject_without_execution():
    from blender_worker import dependency_audit
    group=bpy.data.node_groups.new('Empty nonexecuting OSL boundary fixture','ShaderNodeTree')
    node=group.nodes.new('ShaderNodeScript')
    assert node.script is None and not node.filepath
    result=reject('DEPENDENCY_UNSUPPORTED',dependency_audit)
    result.update({'node_type':'ShaderNodeScript','script_text_attached':False,'external_file_attached':False,'render_or_shader_execution_attempted':False})
    return result
case('external_shader_node_type_rejected_without_execution',shader_script_reject_without_execution)
def ies_node_reject_without_file_read():
    from blender_worker import dependency_audit
    group=bpy.data.node_groups.new('Empty nonexecuting IES boundary fixture','ShaderNodeTree')
    node=group.nodes.new('ShaderNodeTexIES')
    assert getattr(node,'ies',None) is None and not getattr(node,'filepath','')
    result=reject('DEPENDENCY_UNSUPPORTED',dependency_audit)
    result.update({'node_type':'ShaderNodeTexIES','text_attached':False,'external_file_attached':False,'render_or_file_read_attempted':False})
    return result
case('ies_node_type_rejected_without_external_file_read',ies_node_reject_without_file_read)
def canonical_digest_golden():
    from hardsurface.contract import fingerprint
    value={'label':'中文黄金夹具','values':[-0.0,0.0,3.25],'nested':{'signed_zero':-0.0}}
    expected=fingerprint(value);assert digest(value)==expected
    tuple_variant={**value,'values':(-0.0,0.0,3.25)};assert digest(tuple_variant)==expected
    return {'core_sha256':digest(value),'contract_sha256':expected,'unicode_utf8':True,'signed_zero_normalized':True,'tuple_list_equivalent':True}
case('canonical_digest_unicode_signed_zero_contract_golden',canonical_digest_golden)
def impossible_requested_tolerance_rejected():
    r=fresh_request('float32-precision-refusal');r['params']['quality']['length_tolerance']=1e-15;bpy.context.scene.name='Fixture';before=len(bpy.context.scene.objects)
    result=reject('GEOMETRY_PRECISION_UNSUPPORTED',lambda:execute_request(r,job_dir=OUT/'float32-precision-refusal'))
    assert len(bpy.context.scene.objects)==before;result['scene_mutated']=False;result['requested_tolerance_mm']=1e-15;return result
case('sub_float32_requested_tolerance_rejected_before_mutation',impossible_requested_tolerance_rejected)
case('underscore_object_metadata_conflict_rejected',lambda:metadata_reject(False,'_asset_guid'))
case('underscore_mesh_metadata_conflict_rejected',lambda:metadata_reject(True,'_asset_guid'))
def saved_non_si_scale_rejected():
    r=fresh_request('unit-scale-baseline');first=initialize_request(r);bpy.context.scene.unit_settings.scale_length=.001
    path=OUT/'non-si-source.blend';assert not path.exists();bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False)
    source=file_record(path);r2=saved_request(r,first,'non-si-rejected');r2['params']['source'].update(file=source['file'],expected_sha256=source['sha256'],bytes=source['bytes'])
    before=[[tuple(v.co) for v in o.data.vertices] for o in bpy.context.scene.objects if o.type=='MESH'];scale=bpy.context.scene.unit_settings.scale_length
    result=reject('SOURCE_UNIT_SCALE_UNSUPPORTED',lambda:execute_request(r2,job_dir=OUT/'non-si-rejected'))
    assert bpy.context.scene.unit_settings.scale_length==scale
    assert [[tuple(v.co) for v in o.data.vertices] for o in bpy.context.scene.objects if o.type=='MESH']==before
    result.update({'original_scale_unchanged':scale,'geometry_changed':False,'implicit_rescale':False});return result
case('saved_non_si_scale_rejected_without_rescale',saved_non_si_scale_rejected)
def mm_m_physical_equivalence():
    mm=fresh_request('unit-mm');mm['params']['quality']['length_tolerance']=.01;first=initialize_request(mm)
    m1=next(o['evaluated'] for o in first['scene_snapshot']['objects'] if o['generated'])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    metres=fresh_request('unit-m');metres['params']['source']['length_unit']='m';metres['params']['quality']['length_tolerance']=.00001;metres['params']['design']['state']['features'][0]['program']['steps'][0]['size']=[.02,.03,.01]
    second=initialize_request(metres);m2=next(o['evaluated'] for o in second['scene_snapshot']['objects'] if o['generated'])
    assert m1['geometry_sha256']==m2['geometry_sha256'];assert first['scene_snapshot']['scene_units']['scale_length']==second['scene_snapshot']['scene_units']['scale_length']==1.0
    return {'geometry_sha256':m1['geometry_sha256'],'dimensions_m':m1['dimensions_m'],'mm_scene_units':first['scene_snapshot']['scene_units'],'m_scene_units':second['scene_snapshot']['scene_units'],'physical_tolerance_m_both':.00001}
case('mm_and_m_requests_identical_physical_geometry',mm_m_physical_equivalence)
# Persist final fixture for independent process reopen, not this process reloading itself.
clear();e=executor();o=box(e,'identity',(12,15,18));o.name='Renamed fixture';snap=inspect_scene();blend=OUT/'identity-fixture.blend';assert not blend.exists();bpy.ops.wm.save_as_mainfile(filepath=str(blend),check_existing=False)
save_json_new(OUT/'identity-expected.json',{'scene_snapshot':snap,'producer_identity':PROCESS_IDENTITY})
save_json_new(OUT/'suite-report.json',{'kind':'isolated_numeric_fixture','blender':bpy.app.version_string,'build':bpy.app.build_hash.decode(),'tests':RESULT,'passed':sum(x['status']=='pass' for x in RESULT),'failed':sum(x['status']=='fail' for x in RESULT),'visual_acceptance':'not_applicable','production_model_created':False,'plugin_registered':False})
print('HSW_FIXTURES',sum(x['status']=='pass' for x in RESULT),'PASS',sum(x['status']=='fail' for x in RESULT),'FAIL')
if any(x['status']=='fail' for x in RESULT):raise SystemExit(2)
