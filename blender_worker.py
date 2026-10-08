"""Owned Blender subprocess adapter. Run with --disable-autoexec, isolated user dirs.
Usage: blender ... --python blender_worker.py -- payload.json
"""
from __future__ import annotations
import json, os, sys, traceback, math, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
# Authored module source, not any mutable/reusable pyc, is the execution identity.
sys.dont_write_bytecode=True
_cache_prefix=os.environ.get('PYTHONPYCACHEPREFIX') or str(ROOT/'evidence'/('unused-pycache-'+str(uuid.uuid4())))
if Path(_cache_prefix).exists():raise RuntimeError('Worker requires a fresh, nonexistent Python cache prefix')
sys.pycache_prefix=_cache_prefix
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import bpy
from mathutils import Vector, Matrix
from hardsurface.core import (execute_request,inspect_scene,verify_saved_candidate,CoreError,
                             digest,sha_file,file_record,save_json_new,PROCESS_IDENTITY,require_si_scene)

def safe_output(path,job):
    p=Path(path).resolve();j=Path(job).resolve()
    if not p.is_relative_to(j) or p.exists():raise CoreError('OUTPUT_COLLISION','Only a new owned-job output path is accepted',{'path':str(p)})
    p.parent.mkdir(parents=True,exist_ok=True);return p

def open_source(path):
    p=Path(path).resolve()
    if not p.is_file() or p.suffix.lower()!='.blend':raise CoreError('SOURCE_INVALID','Expected existing saved .blend')
    bpy.ops.wm.open_mainfile(filepath=str(p),load_ui=False,use_scripts=False)

def dependency_audit(original_source_file=None, resource_mapping=None, dependency_root=None):
    require_si_scene()
    problems=[];resources=[]
    resource_mapping=resource_mapping or {}
    def resolved(filepath):
        if original_source_file and filepath.startswith('//'):return (Path(original_source_file).resolve().parent/filepath[2:]).resolve()
        return Path(bpy.path.abspath(filepath)).resolve()
    def external_record(kind,name,path):
        mapped=resource_mapping.get(str(path))
        checkpath=Path(mapped['snapshot']).resolve() if mapped else path
        if dependency_root and not checkpath.is_relative_to(Path(dependency_root).resolve()):problems.append('dependency outside declared closure '+str(checkpath))
        if not checkpath.is_file():problems.append('missing '+kind+' '+str(checkpath));return
        actual=sha_file(checkpath)
        if mapped and mapped.get('sha256')!=actual:problems.append('mapped dependency SHA mismatch '+str(checkpath));return
        resources.append({'kind':kind,'name':name,'source':str(path),'sha256':actual,'bytes':checkpath.stat().st_size})
    if bpy.data.libraries:problems.append('linked libraries')
    # OSL Script nodes are an executable-content boundary even when currently
    # unused or hidden by a preview material override. Never compile them here.
    node_trees=list(bpy.data.node_groups)
    for datablocks in (bpy.data.materials,bpy.data.worlds,bpy.data.lights):
        node_trees.extend(x.node_tree for x in datablocks if getattr(x,'node_tree',None) is not None)
    seen_trees=set();node_count=0
    while node_trees:
        tree=node_trees.pop()
        if tree in seen_trees:continue
        seen_trees.add(tree)
        if len(seen_trees)>1024:raise CoreError('DEPENDENCY_GRAPH_LIMIT','Shader tree audit exceeds 1024 unique trees')
        for node in tree.nodes:
            node_count+=1
            if node_count>50000:raise CoreError('DEPENDENCY_GRAPH_LIMIT','Shader audit exceeds 50000 nodes')
            if node.bl_idname=='ShaderNodeScript' or node.type=='SCRIPT':problems.append('unsupported external/embedded shader script in '+tree.name)
            if node.bl_idname=='ShaderNodeTexIES':problems.append('unsupported IES shader dependency in '+tree.name)
            nested=getattr(node,'node_tree',None)
            if nested is not None:node_trees.append(nested)
    for scene in bpy.data.scenes:
        if getattr(scene,'compositing_node_group',None) is not None or (getattr(scene,'node_tree',None) is not None and len(scene.node_tree.nodes)):problems.append('unsupported compositor node graph '+scene.name)
        if scene.sequence_editor and len(getattr(scene.sequence_editor,'strips',getattr(scene.sequence_editor,'sequences',[]))):problems.append('unsupported sequencer '+scene.name)
        if scene.animation_data and (scene.animation_data.action or scene.animation_data.drivers):problems.append('scene animation/drivers')
    for obj in bpy.data.objects:
        if obj.library or obj.override_library:problems.append('linked or overridden object '+obj.name)
        if obj.animation_data or obj.constraints or obj.particle_systems:problems.append('dynamic object '+obj.name)
        if obj.type=='MESH' and obj.data.shape_keys:problems.append('shape keys '+obj.name)
        if any(m.type in ('NODES','CLOTH','FLUID','SOFT_BODY','MESH_CACHE','MESH_SEQUENCE_CACHE') for m in obj.modifiers):problems.append('dynamic modifier '+obj.name)
    for image in bpy.data.images:
        if image.source in ('GENERATED','VIEWER') or image.packed_file:continue
        if image.source!='FILE':problems.append('unsupported image source '+image.name);continue
        path=resolved(image.filepath);external_record('image',image.name,path)
    for font in bpy.data.fonts:
        if font.filepath in ('<builtin>','') or font.packed_file:continue
        path=resolved(font.filepath);external_record('font',font.name,path)
    if bpy.data.sounds or bpy.data.movieclips or bpy.data.volumes or bpy.data.cache_files:problems.append('audio/movie/volume/cache dependencies not qualified')
    if problems:raise CoreError('DEPENDENCY_UNSUPPORTED','Static closure audit rejected dependencies',{'problems':problems})
    return resources

def stage(payload):
    mapping=payload.get('resources',payload.get('resource_mapping',[]))
    if isinstance(mapping,dict):mapping=[{'source':k,'snapshot':v} for k,v in mapping.items()]
    matches={str(Path(x['source']).resolve()):x for x in mapping}
    resources=dependency_audit(payload.get('original_source_file'),matches)
    path=safe_output(payload['candidate_path'],payload['job_dir'])
    for resource in resources:
        entry=matches.get(resource['source'])
        if not entry:raise CoreError('DEPENDENCY_UNLISTED','An external resource has no explicit snapshot mapping',resource)
        target=Path(entry.get('snapshot',entry.get('target',''))).resolve()
        if not target.is_file() or sha_file(target)!=resource['sha256'] or (entry.get('sha256') and entry['sha256']!=resource['sha256']):raise CoreError('DEPENDENCY_HASH','Resource snapshot identity differs',resource)
        if resource['kind']=='image':bpy.data.images[resource['name']].filepath=bpy.path.relpath(str(target),start=str(path.parent))
        else:bpy.data.fonts[resource['name']].filepath=bpy.path.relpath(str(target),start=str(path.parent))
    bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False,compress=False,relative_remap=False)
    return {'outcome':'persisted_unverified','producer_identity':PROCESS_IDENTITY,'candidate':file_record(path),'resources':dependency_audit(),'scene_snapshot':inspect_scene(),'source_semantics':'SAVED_DISK_V1'}

def preview(payload):
    dependency_audit()
    job=payload['job_dir'];cfg=payload.get('preview',payload.get('request',{}).get('params',{}).get('preview',{}))
    width=cfg.get('width',512);height=cfg.get('height',512);samples=cfg.get('samples',16)
    if not 16<=width<=2048 or not 16<=height<=2048 or not 1<=samples<=128:raise CoreError('PREVIEW_BUDGET','Preview dimensions/samples exceed bound')
    objects=[o for o in bpy.context.scene.objects if o.type=='MESH' and not o.hide_render]
    if not objects:raise CoreError('PREVIEW_EMPTY','No renderable mesh')
    points=[o.matrix_world@Vector(corner) for o in objects for corner in o.bound_box]
    lo=Vector(tuple(min(p[i] for p in points) for i in range(3)));hi=Vector(tuple(max(p[i] for p in points) for i in range(3)));center=(lo+hi)/2;extent=max(hi-lo)
    scene=bpy.context.scene;scene.render.use_compositing=False;scene.render.use_sequencer=False;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.shading_system=False;scene.cycles.samples=samples;scene.render.threads_mode='FIXED';threads=payload.get('request',{}).get('params',{}).get('budgets',{}).get('cpu_threads',payload.get('cpu_threads',2))
    if type(threads) is not int or not 1<=threads<=8:raise CoreError('PREVIEW_THREADS','Explicit preview thread budget must be 1..8')
    scene.render.threads=threads
    scene.render.resolution_x=width;scene.render.resolution_y=height;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
    scene.view_settings.view_transform='AgX';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
    scene.cycles.use_denoising=True;scene.cycles.denoiser='OPENIMAGEDENOISE'
    if not scene.world:scene.world=bpy.data.worlds.new('HSW preview world')
    scene.world.use_nodes=True;scene.world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.08,.08,.08,1);scene.world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.4
    mat=bpy.data.materials.new('HSW preview neutral');mat.diffuse_color=(.72,.72,.72,1);mat.use_nodes=True
    bsdf=mat.node_tree.nodes.get('Principled BSDF');bsdf.inputs['Base Color'].default_value=(.72,.72,.72,1);bsdf.inputs['Roughness'].default_value=.5
    scene.view_layers[0].material_override=mat
    camdata=bpy.data.cameras.new('HSW preview camera');camera=bpy.data.objects.new('HSW preview camera',camdata);scene.collection.objects.link(camera);scene.camera=camera;camdata.type='ORTHO';camdata.ortho_scale=extent*1.8;camdata.clip_start=max(extent*.0001,1e-6);camdata.clip_end=max(extent*100,1)
    lights=[]
    for i in range(3):
        data=bpy.data.lights.new('HSW preview light '+str(i),'AREA');ob=bpy.data.objects.new(data.name,data);scene.collection.objects.link(ob);data.shape='DISK';data.size=extent*4;lights.append(ob)
    outputs=[]
    for view in cfg.get('views',['three_quarter']):
        dirs={'front':(0,-4,0),'top':(0,0,4),'three_quarter':(3,-4,3),'side':(4,0,0),'right':(4,0,0),'back':(0,4,0),'bottom':(0,0,-4)}
        if view not in dirs:raise CoreError('PREVIEW_VIEW_UNSUPPORTED','Unknown preview view')
        camera.location=center+Vector(dirs[view])*extent
        outward=(camera.location-center).normalized();up=Vector((0,1,0) if view in ('top','bottom') else (0,0,1))
        right=(-outward).cross(up).normalized();camera_up=right.cross(-outward).normalized()
        camera.rotation_euler=Matrix((right,camera_up,outward)).transposed().to_euler()
        lighting=[]
        for light,(x,y,z,power) in zip(lights,((-3,4,6,500),(4,1,3,300),(0,-3,-4,350))):
            light.location=center+(right*x+camera_up*y+outward*z)*extent
            light.rotation_euler=(center-light.location).to_track_quat('-Z','Y').to_euler();light.data.energy=power*extent*extent
            lighting.append({'position_m':list(light.location),'energy_w':light.data.energy})
        path=safe_output(str(Path(job)/('preview-'+view+'.png')),job);scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
        if path.stat().st_size>2*1024*1024:raise CoreError('PREVIEW_BYTES','Preview exceeds session 2 MiB bound')
        outputs.append({**file_record(path),'view':view,'width':width,'height':height,'samples':samples,'lighting':lighting,'lighting_frame':'camera_relative','display_transform':'AgX','visual_acceptance':'not_run'})
    return {'outcome':'pass','previews':outputs,'engine':'CYCLES','device':'CPU','saved_candidate_modified':False,'cpu_threads':threads}

def main():
    args=sys.argv[sys.argv.index('--')+1:]
    if args and args[0]=='--payload':args=args[1:]
    if len(args)!=1:raise RuntimeError('Expected one payload JSON path')
    payload=json.loads(Path(args[0]).read_text());report_path=Path(payload['report_path']).resolve();job=Path(payload.get('job_dir',report_path.parent)).resolve();job.mkdir(parents=True,exist_ok=True)
    safe_output(report_path,job)
    try:
        if tuple(bpy.app.version[:2]) != (5,2):raise CoreError('BLENDER_VERSION_UNSUPPORTED','This adapter supports Blender 5.2.x; other runtime families require qualification')
        source=payload.get('source')
        if isinstance(source,dict):source=source.get('file',source.get('path'))
        if source:open_source(source)
        elif payload['action'] in ('run','build'):
            request=payload['request'];params=request.get('params',request)
            if params['source']['kind']!='new_scene':raise CoreError('SOURCE_REQUIRED','Saved source request requires explicit staged source')
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.context.scene.name=params.get('context',{}).get('scene','Scene')
        if payload['action'] in ('run','build'):
            dependency_audit()
            result=execute_request(payload['request'],job_dir=job,solved_sketches=payload.get('solutions',payload.get('solved_sketches')),plan=payload.get('plan'),start_after_step_keys=payload.get('start_after_step_keys'),candidate_path=payload.get('candidate_path'),stop_after_step_key=payload.get('stop_after_step_key'),base_design_state=payload.get('base_design_state'),reference_approval=payload.get('reference_approval'))
            if payload['request']['params']['quality'].get('stage','full')=='source_cage' and not result.get('partial_checkpoint'):
                from hardsurface.source_mesh_inspection_native import execute_construct_inspection
                result['source_mesh_inspection']=execute_construct_inspection(payload['request'],result['candidate'],job)
                if result['source_mesh_inspection']['acceptance']['polygon_quality']!='pass':
                    raise CoreError('SOURCE_INSPECTION_QUALITY','Actual source inspection cannot confirm the source-stage polygon gate')
                result['qualification_scope']={'stage':'source_cage','source_structure':'pass','evaluated_shape':'not_run','surface_observation':'not_run','production_qualification':'not_run'}
        elif payload['action'] in ('validate','observe','topology','subdivision.diagnose','mesh.inspect'):
            resources=dependency_audit()
            allowed={str(Path(d['file']).resolve()):d['sha256'] for d in payload.get('guarded_resources',[])}
            if any(allowed.get(str(Path(d['source']).resolve()))!=d['sha256'] for d in resources):raise CoreError('DEPENDENCY_UNSUPPORTED','Read-only diagnostics require every external resource to be pinned and guarded by the host')
            if payload['action']=='validate':
                from hardsurface.validation import execute
                result=execute(payload['request'],job_dir=job)
            elif payload['action']=='subdivision.diagnose':
                from hardsurface.core import diagnose_subdivision
                result=diagnose_subdivision(payload['request'],job)
            elif payload['action']=='topology':
                from hardsurface.topology import execute
                result=execute(payload['request'],job)
            elif payload['action']=='mesh.inspect':
                from hardsurface.source_mesh_inspection_native import execute
                result=execute(payload['request'],job)
            else:
                from hardsurface.observation import execute
                result=execute(payload['request'],job)
        elif payload['action']=='inspect':result={'outcome':'pass','scene_snapshot':inspect_scene(),'resources':dependency_audit()}
        elif payload['action']=='verify':
            expected=payload.get('expected',payload.get('expected_report'))
            if isinstance(expected,str):expected=json.loads(Path(expected).read_text())
            resources=dependency_audit(dependency_root=payload.get('dependency_root'));result=verify_saved_candidate(expected);result['resources']=resources
        elif payload['action']=='stage':result=stage(payload)
        elif payload['action']=='preview':result=preview(payload)
        else:raise CoreError('ACTION_UNSUPPORTED','Unknown worker action')
    except Exception as exc:
        result={'outcome':'failed','execution_state':'failed','error':{'code':getattr(exc,'code','INTERNAL_ERROR'),'message':str(exc),'details':getattr(exc,'details',{})},'traceback':traceback.format_exc(),**getattr(exc,'partial_report',{}),'acceptance':{'technical':'fail','preservation':'not_run','reopen':'not_run','visual':'not_run','user_feedback':'not_run'}}
    result['python_import_policy']={'dont_write_bytecode':sys.dont_write_bytecode,'pycache_prefix':sys.pycache_prefix,'fresh_prefix_existed_before_import':False,'worker_source_sha256':sha_file(__file__),'python_version':sys.version}
    save_json_new(report_path,result)
    if result.get('outcome')=='failed':raise SystemExit(2)

if __name__=='__main__':main()
