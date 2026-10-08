# SPDX-License-Identifier: GPL-3.0-or-later
"""PREPARED ONLY: native M1 positive/fault/save/reopen qualification driver.

This file was authored and syntax-checked on the host. It has NOT been run in
Blender and grants no execution permission. A later user-approved, exact-hash
plan and isolated new job are required. Never run it on a source or candidate
whose mutation/creation has not been approved. No addon registration/install,
network access, original replacement, cleanup or subprocess launch occurs.

One producer invocation creates one new owned candidate and a producer receipt.
A DIFFERENT Blender process must independently open that exact candidate and
run mode=reopen. The script refuses a verifier process matching its producer.
Producer receipts alone do not establish reopen, visual, reference or production
qualification. The host must protect source/dependencies and approve the exact
script/plugin hashes and all output paths; an approval_reference string in JSON
is a scope label only and cannot authorize itself.

Plan schema (all paths absolute, all outputs under an existing new job_dir):
  schema_version: native-structure-qualification-plan/1.0
  approval_reference: exact external approval message reference
  script_sha256: SHA256 of THIS file
  plugin_files: {relative hardsurface/**/*.py path: SHA256}; exact complete set
  source: {file, sha256, bytes}; source .blend is opened by producer's caller
  job_dir: new existing job directory
  case: one name from CASES
  parameters: approved quad.panel/subd_control_cage parameters
  object_id, data_id, feature_id: approved stable identity strings
  outputs: {candidate, producer_receipt, verifier_receipt}

Invocation prepared for later authorization, not executed here:
  Blender SOURCE --background --python THIS -- --mode produce --plan PLAN
  Blender CANDIDATE --background --python THIS -- --mode reopen --plan PLAN
The caller must additionally arrange --disable-autoexec and the existing source
protection/timeout/isolated config layer; this script does not substitute for it.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

CASES = {
    'unchanged': None,
    'reorder_with_ids': None,
    'missing_vertex_slots': 'STRUCTURE_IDENTITY_MISSING',
    'missing_face_slots': 'STRUCTURE_IDENTITY_MISSING',
    'duplicate_vertex_slots': 'STRUCTURE_IDENTITY_INVALID',
    'duplicate_face_slots': 'STRUCTURE_IDENTITY_INVALID',
    'unknown_vertex_slot': 'STRUCTURE_IDENTITY_INVALID',
    'reordered_vertex_slots_only': 'STRUCTURE_GEOMETRY_DRIFT',
    'reordered_face_slots_only': 'STRUCTURE_TOPOLOGY_DRIFT',
    'vertex_wrong_domain': 'STRUCTURE_ATTRIBUTE_INVALID',
    'face_wrong_type': 'STRUCTURE_ATTRIBUTE_INVALID',
    'topology_same_count': 'STRUCTURE_TOPOLOGY_DRIFT',
    'reverse_face': 'STRUCTURE_TOPOLOGY_DRIFT',
    'loose_edge': 'STRUCTURE_ACTUAL_EDGES_INVALID',
    'duplicate_edge': 'STRUCTURE_ACTUAL_EDGES_INVALID',
    'loose_vertex': 'STRUCTURE_AUTHORSHIP_INVALID',
    'missing_crease': 'STRUCTURE_ATTRIBUTE_MISSING',
    'crease_wrong_domain': 'STRUCTURE_ATTRIBUTE_INVALID',
    'crease_nan': 'STRUCTURE_CREASE_INVALID',
    'crease_out_of_range': 'STRUCTURE_CREASE_INVALID',
    'crease_changed': 'STRUCTURE_CREASE_MISMATCH',
    'matrix_drift': 'STRUCTURE_CONTEXT_DRIFT',
    'modifier_drift': 'STRUCTURE_CONTEXT_DRIFT',
    'source_receipt_drift': 'STRUCTURE_SOURCE_DRIFT',
    'source_table_drift': 'STRUCTURE_SOURCE_DRIFT',
    'manifest_sha_drift': 'STRUCTURE_METADATA_SHA_MISMATCH',
    'manifest_source_and_hash_drift': 'STRUCTURE_REGISTRY_DRIFT',
}


def require(condition, message):
    if not condition: raise RuntimeError(message)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def safe_path(value, *, exists):
    p=Path(value)
    require(p.is_absolute() and '..' not in p.parts, 'Absolute nontraversing paths required')
    require(not any(item.is_symlink() for item in (p,*p.parents)), 'Symlink paths are forbidden')
    require(p.exists() == exists, 'Expected path existence differs: '+str(p))
    return p


def record(path):
    p=Path(path);return {'file':str(p),'sha256':sha(p),'bytes':p.stat().st_size}


def check_record(value):
    path=safe_path(value['file'],exists=True)
    require(record(path)==value,'Pinned file descriptor differs: '+str(path))
    return path


def load_plan(path):
    path=safe_path(path,exists=True);raw=path.read_bytes()
    require(len(raw)<=4*1024*1024,'Plan exceeds 4 MiB')
    plan=json.loads(raw)
    keys={'schema_version','approval_reference','script_sha256','plugin_files','source','job_dir',
          'case','parameters','object_id','data_id','feature_id','outputs'}
    require(set(plan)==keys and plan['schema_version']=='native-structure-qualification-plan/1.0','Unexpected plan fields/version')
    require(isinstance(plan['approval_reference'],str) and plan['approval_reference'],'External approval reference must be supplied')
    require(plan['script_sha256']==sha(__file__),'Qualification script SHA is not the approved SHA')
    root=Path(__file__).resolve().parents[1]
    files={str(p.relative_to(root)):sha(p) for p in sorted((root/'hardsurface').rglob('*.py'))}
    require(plan['plugin_files']==files,'Approved plugin implementation file set or SHA differs')
    require(plan['case'] in CASES,'Unknown native fault case')
    require(plan['parameters'].get('op')=='quad.panel' and plan['parameters'].get('topology_strategy')=='subd_control_cage','Only the bounded authored panel constructor is allowed')
    job=safe_path(plan['job_dir'],exists=True);require(job.is_dir(),'Job directory required')
    require(set(plan['outputs'])=={'candidate','producer_receipt','verifier_receipt'},'All three outputs must be declared')
    paths=[]
    for value in plan['outputs'].values():
        p=Path(value);require(p.is_absolute() and '..' not in p.parts and p.parent==job,'Every output must be a direct owned job path')
        require(not any(item.is_symlink() for item in (p,*p.parents)),'Output aliases forbidden');paths.append(p)
    require(len(set(paths))==3 and Path(plan['source']['file']) not in paths,'Output collision/alias')
    require(paths[0].suffix=='.blend','Candidate output must be .blend')
    return plan,hashlib.sha256(raw).hexdigest(),root


def _copy_reindexed_mesh(obj, *, reorder=False, reverse_face=False, topology_drift=False):
    """Fault fixture utility. Never used by production identity transport."""
    import bpy
    from hardsurface.structure_native import VERTEX_SLOT,FACE_SLOT
    old=obj.data;vp=list(reversed(range(len(old.vertices)))) if reorder else list(range(len(old.vertices)))
    fp=list(reversed(range(len(old.polygons)))) if reorder else list(range(len(old.polygons)))
    lookup={previous:new for new,previous in enumerate(vp)}
    vertices=[list(old.vertices[i].co) for i in vp]
    faces=[[lookup[v] for v in old.polygons[i].vertices] for i in fp]
    if reverse_face:faces[0].reverse()
    if topology_drift:
        # Same counts; choose a distinct nonmember to change connectivity.
        faces[0][0]=next(i for i in range(len(vertices)) if i not in faces[0])
    native=bpy.data.meshes.new(old.name+'.fault-fixture')
    native.from_pydata(vertices,[],faces);native.update(calc_edges=True)
    for key,value in old.items():native[key]=copy.deepcopy(value)
    for name,domain,kind,order in ((VERTEX_SLOT,'POINT','INT',vp),(FACE_SLOT,'FACE','INT',fp),('hs_quad_surface_id','FACE','INT',fp)):
        attr=native.attributes.new(name,kind,domain)
        for index,previous in enumerate(order):attr.data[index].value=old.attributes[name].data[previous].value
    old_crease={tuple(sorted(lookup[v] for v in edge.vertices)):old.attributes['crease_edge'].data[index].value for index,edge in enumerate(old.edges)}
    attr=native.attributes.new('crease_edge','FLOAT','EDGE')
    for index,edge in enumerate(native.edges):attr.data[index].value=old_crease.get(tuple(sorted(edge.vertices)),0.0)
    obj.data=native
    # No remove/cleanup: old mesh remains an unused diagnostic data block.


def mutate(obj, case):
    from hardsurface.structure_native import VERTEX_SLOT,FACE_SLOT,MANIFEST,MANIFEST_SHA
    from hardsurface.structure_kernel import fingerprint
    mesh=obj.data
    if case=='unchanged':return
    if case=='reorder_with_ids':_copy_reindexed_mesh(obj,reorder=True);return
    if case in ('topology_same_count','reverse_face'):
        _copy_reindexed_mesh(obj,topology_drift=case=='topology_same_count',reverse_face=case=='reverse_face');return
    if case.startswith('missing_'):
        name={'missing_vertex_slots':VERTEX_SLOT,'missing_face_slots':FACE_SLOT,'missing_crease':'crease_edge'}[case]
        mesh.attributes.remove(mesh.attributes[name]);return
    if case in ('duplicate_vertex_slots','duplicate_face_slots'):
        name=VERTEX_SLOT if case=='duplicate_vertex_slots' else FACE_SLOT
        mesh.attributes[name].data[0].value=mesh.attributes[name].data[1].value;return
    if case=='unknown_vertex_slot':mesh.attributes[VERTEX_SLOT].data[0].value=2147483647;return
    if case in ('reordered_vertex_slots_only','reordered_face_slots_only'):
        name=VERTEX_SLOT if case=='reordered_vertex_slots_only' else FACE_SLOT
        data=mesh.attributes[name].data;values=[x.value for x in data]
        for row,value in zip(data,reversed(values)):row.value=value
        return
    if case in ('vertex_wrong_domain','face_wrong_type','crease_wrong_domain'):
        name,domain,kind={
            'vertex_wrong_domain':(VERTEX_SLOT,'EDGE','INT'),
            'face_wrong_type':(FACE_SLOT,'FACE','FLOAT'),
            'crease_wrong_domain':('crease_edge','POINT','FLOAT')}[case]
        mesh.attributes.remove(mesh.attributes[name]);mesh.attributes.new(name,kind,domain);return
    if case in ('loose_edge','duplicate_edge'):
        existing={tuple(sorted(e.vertices)) for e in mesh.edges}
        pair=list(mesh.edges[0].vertices) if case=='duplicate_edge' else next([0,b] for b in range(1,len(mesh.vertices)) if (0,b) not in existing)
        mesh.edges.add(1);mesh.edges[-1].vertices=pair;mesh.update();return
    if case=='loose_vertex':mesh.vertices.add(1);mesh.vertices[-1].co=(123.0,456.0,789.0);mesh.update();return
    if case.startswith('crease_'):
        value={'crease_nan':float('nan'),'crease_out_of_range':1.5,'crease_changed':.5}[case]
        mesh.attributes['crease_edge'].data[0].value=value;return
    if case=='matrix_drift':obj.location.x+=.001;return
    if case=='modifier_drift':obj.modifiers[0].levels+=1;return
    if case=='source_receipt_drift':obj['hs_quad_construction_sha256']='f'*64;return
    if case=='source_table_drift':
        table=json.loads(obj['hs_quad_surface_table']);table[0]['surface_id']='fault-source'
        obj['hs_quad_surface_table']=json.dumps(table);return
    if case=='manifest_sha_drift':mesh[MANIFEST_SHA]='f'*64;return
    if case=='manifest_source_and_hash_drift':
        value=json.loads(mesh[MANIFEST]);value['identity_binding']['source_binding']['source']['sha256']='f'*64
        mesh[MANIFEST]=json.dumps(value,sort_keys=True,separators=(',',':'));mesh[MANIFEST_SHA]=fingerprint(value);return
    raise AssertionError(case)


def check_native(obj, case, binding, unit_scale):
    from hardsurface.structure_native import validate_native_structure,compact_native_report
    from hardsurface.io import RuntimeFailure
    expected=CASES[case]
    try:
        report=validate_native_structure(obj,unit_scale=unit_scale,expected_binding=binding)
    except RuntimeFailure as error:
        require(expected is not None and error.code==expected,
                'Wrong fault result: expected %r, received %s: %s'%(expected,error.code,error))
        return {'status':'expected_rejection','code':error.code,'details':error.details}
    require(expected is None,'Injected fault was not rejected: '+case)
    return {'status':'pass','report':compact_native_report(report)}


def produce(plan, plan_sha):
    import bpy
    from hardsurface.core import DomainExecutor,object_snapshot,save_json_new,PROCESS_IDENTITY
    from hardsurface.ops.quad_bridge import create
    from hardsurface.ops.geometry import require_si_scene
    from hardsurface.structure_native import bind_native_structure
    source=check_record(plan['source'])
    require(Path(bpy.data.filepath).resolve()==source,'Producer must open the pinned saved source')
    for path in plan['outputs'].values():safe_path(path,exists=False)
    units=require_si_scene();before=[(obj,object_snapshot(obj)) for obj in bpy.context.scene.objects]
    obj,creation=create(plan['parameters'],plan['feature_id'],'NativeStructureQualification_'+plan['case'])
    obj['hs_object_id']=plan['object_id'];obj.data['hs_data_id']=plan['data_id'];obj['hs_feature_id']=plan['feature_id']
    binding=bind_native_structure(obj,source_binding={'source':plan['source'],'approval_reference':plan['approval_reference'],'plan_sha256':plan_sha},unit_scale=units['scale_length'])
    mutate(obj,plan['case']);bpy.context.view_layer.update()
    observed=check_native(obj,plan['case'],binding,units['scale_length'])
    require(all(object_snapshot(old)==snapshot for old,snapshot in before),'Non-target source object changed')
    check_record(plan['source'])
    # Reuse the production new-job/no-overwrite save method without running a job.
    DomainExecutor._save_new(SimpleNamespace(job=Path(plan['job_dir'])),Path(plan['outputs']['candidate']))
    check_record(plan['source'])
    report={'schema_version':'native-structure-qualification-receipt/1.0','mode':'producer',
        'case':plan['case'],'plan_sha256':plan_sha,'script_sha256':plan['script_sha256'],
        'plugin_files':plan['plugin_files'],'source':plan['source'],'candidate':record(plan['outputs']['candidate']),
        'producer_identity':PROCESS_IDENTITY,'producer_pid':os.getpid(),
        'object_name':obj.name,'binding':binding,'observed':observed,'creation':creation,
        'non_target_preservation':{'status':'pass','snapshots':[snapshot for _,snapshot in before]},
        'source_preservation':'file_descriptor_rechecked','independent_reopen':'not_run',
        'visual_quality':'not_run','reference_approval':'not_run','production_qualification':'not_run'}
    save_json_new(plan['outputs']['producer_receipt'],report)
    print(json.dumps({'status':'producer_complete_reopen_required','receipt':record(plan['outputs']['producer_receipt'])}),flush=True)


def reopen(plan, plan_sha):
    import bpy
    from hardsurface.core import PROCESS_IDENTITY,object_snapshot,save_json_new
    from hardsurface.ops.geometry import require_si_scene
    receipt_path=safe_path(plan['outputs']['producer_receipt'],exists=True)
    raw=receipt_path.read_bytes();require(len(raw)<=16*1024*1024,'Producer receipt oversized');producer=json.loads(raw)
    require(producer['mode']=='producer' and producer['plan_sha256']==plan_sha and producer['script_sha256']==plan['script_sha256'] and producer['plugin_files']==plan['plugin_files'],'Producer scope/hashes differ')
    require(producer['producer_identity']!=PROCESS_IDENTITY and producer['producer_pid']!=os.getpid(),'Verifier must use a genuinely independent Blender process')
    require(producer['case']==plan['case'],'Producer case differs')
    path=check_record(producer['candidate']);require(path==Path(plan['outputs']['candidate']),'Producer output path differs')
    require(Path(bpy.data.filepath).resolve()==path,'Verifier must independently open exact producer candidate')
    check_record(plan['source']);safe_path(plan['outputs']['verifier_receipt'],exists=False)
    obj=bpy.data.objects.get(producer['object_name']);require(obj is not None,'Saved target missing')
    observed=check_native(obj,plan['case'],producer['binding'],require_si_scene()['scale_length'])
    # Every source object's actual state is reconstructed after independent load.
    for expected in producer['non_target_preservation']['snapshots']:
        old=bpy.data.objects.get(expected['name']);require(old is not None and object_snapshot(old)==expected,'Saved non-target source object drift')
    result={'schema_version':'native-structure-qualification-receipt/1.0','mode':'verifier',
        'case':plan['case'],'plan_sha256':plan_sha,'candidate':producer['candidate'],
        'producer_receipt':record(receipt_path),'producer_identity':producer['producer_identity'],
        'verifier_identity':PROCESS_IDENTITY,'verifier_pid':os.getpid(),'observed':observed,
        'independent_reopen':'pass','non_target_preservation':'pass','source_preservation':'file_descriptor_rechecked',
        'blender_version':bpy.app.version_string,'blender_build_hash':bpy.app.build_hash.decode(),
        'visual_quality':'not_run','reference_approval':'not_run','production_qualification':'not_run'}
    save_json_new(plan['outputs']['verifier_receipt'],result)
    print(json.dumps({'status':'independent_reopen_complete','receipt':record(plan['outputs']['verifier_receipt'])}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',required=True,choices=('produce','reopen'));parser.add_argument('--plan',required=True)
    require('--' in sys.argv,'Explicit qualification arguments required')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    plan,plan_sha,root=load_plan(args.plan);sys.path.insert(0,str(root))
    if args.mode=='produce':produce(plan,plan_sha)
    else:reopen(plan,plan_sha)


if __name__=='__main__':main()
