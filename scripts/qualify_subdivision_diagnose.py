#!/usr/bin/env python3
"""Reproducible authored-fixture qualification through public blenderctl.

Creates a new evidence directory and source fixtures, then only calls the public
read-only plugin operation. No installation, register(), global changes, source
replacement, deletion, or third-party model is involved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def record(path):return {'file':str(path.resolve()),'sha256':sha(path),'bytes':path.stat().st_size}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--blender',required=True,type=Path)
    parser.add_argument('--blenderctl',required=True,type=Path)
    parser.add_argument('--jobs-dir',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    a=parser.parse_args()
    if not all(p.is_absolute() for p in (a.blender,a.blenderctl,a.jobs_dir,a.out)):raise ValueError('Absolute paths required')
    if a.out.exists():raise ValueError('New evidence output directory required')
    a.out.mkdir(parents=True)
    fixture_command=[str(a.blender),'--background','--factory-startup','--disable-autoexec','--threads','2','--python',str(ROOT/'tests/blender_subdivision_fixtures.py'),'--',str(a.out/'fixtures')]
    fixture=subprocess.run(fixture_command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
    (a.out/'fixture-process.log').write_text(fixture.stdout)
    if fixture.returncode:raise RuntimeError('Fixture generation failed')
    sources=json.loads((a.out/'fixtures/sources.json').read_text());before={key:sha(value['file']) for key,value in sources.items()}
    cases=[('cube','cube',None),('landscape-frame','cube',None),('portrait-frame','cube',None),('existing-subd','existing-subd',None),('custom-normals','custom-normals',None),('open-quad','open-quad',None),('unsupported-bevel','unsupported-bevel','SUBDIVISION_STACK_UNSUPPORTED'),('face-budget','cube','SUBDIVISION_GEOMETRY_LIMIT'),('missing-target','cube','SUBDIVISION_TARGET')]
    rows=[];env={**os.environ,'BLENDERCTL_HARDSURFACE_ROOT':str(ROOT)}
    for name,source_name,expected_error in cases:
        source=sources[source_name]
        params={'request_id':'subd.qualify.'+a.out.name+'.'+name,'source':{'file':source['file'],'expected_sha256':source['sha256']},'target':{'object_name':'Cube'},'stack_mode':'isolated_control_cage','export_geometry':expected_error is None,'render':{'enabled':name=='cube','width':256,'height':256},'wall_seconds':300}
        if name in ('landscape-frame','portrait-frame'):
            params['levels']=[0];params['render']={'enabled':True,'width':640 if name=='landscape-frame' else 240,'height':480}
        if name=='face-budget':params['max_evaluated_faces']=1
        if name=='missing-target':params['target']={'object_name':'Absent mesh'}
        request=a.out/(name+'-request.json');request.write_text(json.dumps({'schema_version':'1.0','command':'hardsurface.subdivision.diagnose','params':params},indent=2)+'\n')
        command=[sys.executable,str(a.blenderctl),'--blender',str(a.blender),'--jobs-dir',str(a.jobs_dir),'hardsurface','subdivision','diagnose','--request',str(request)]
        t=time.monotonic();process=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=330)
        result_path=a.out/(name+'-result.json');result_path.write_text(process.stdout);(a.out/(name+'-stderr.log')).write_text(process.stderr)
        envelope=json.loads(process.stdout);d=envelope.get('data',envelope);error=d.get('error',{}).get('code')
        checks={}
        if expected_error:
            checks['fail_closed_expected_error']=process.returncode!=0 and d.get('status')=='failed' and error==expected_error
        else:
            checks['public_cli_success']=process.returncode==0 and d.get('status')=='succeeded'
            if checks['public_cli_success']:
                checks['requested_levels']=[r['level'] for r in d['levels']]==params.get('levels',[0,1,2,3])
                checks['no_save']=d['blend_save_performed'] is False and d['saved_candidate_modified'] is False
                checks['source_signature_unchanged']=d['source_signature_sha256']==d['source_signature_after_sha256']
                checks['backend_explicit']=d['backend']['algorithm']=='CATMULL_CLARK' and not d['backend']['turbo_smooth_tested']
                checks['geometry_exports']=len(d['outputs'])==len(params.get('levels',[0,1,2,3]))
                for sample in d['levels']:
                    ref=sample['geometry'];data=json.loads(Path(ref['file']).read_text());level=sample['level'];metrics=sample['metrics']
                    checks['export_identity_l'+str(level)]=sha(ref['file'])==ref['sha256'] and Path(ref['file']).stat().st_size==ref['bytes']
                    checks['native_domains_l'+str(level)]=len(data['vertices'])==metrics['vertices'] and len(data['polygons'])==metrics['faces'] and len(data['loop_triangles'])==metrics['blender_loop_triangles']
                    checks['derived_triangle_mapping_l'+str(level)]=all(set(t['vertices']).issubset(data['polygons'][t['polygon_index']]) for t in data['loop_triangles'])
                if name=='cube':
                    checks['actual_quad_sequence']=[r['metrics']['faces'] for r in d['levels']]==[6,24,96,384]
                    checks['cage_poles_not_good_flow']=d['control_cage']['poles']['interior_count']==8 and d['control_cage']['edge_loop_continuity']['closed_cycles']==0
                    checks['eight_paired_views']=len(d['previews'])==8
                    for i,image in enumerate(d['previews']):
                        path=Path(image['file']);raw=path.read_bytes();width,height=struct.unpack('>II',raw[16:24])
                        checks['bounded_png_'+str(i)]=raw[:8]==b'\x89PNG\r\n\x1a\n' and (width,height)==(256,256) and len(raw)<=2*1024*1024 and sha(path)==image['sha256']
                    frames=[json.dumps(r['frame'],sort_keys=True) for r in d['previews']]
                    checks['identical_baseline_frame']=len(set(frames))==1
                if name in ('landscape-frame','portrait-frame'):
                    checks['two_views']=len(d['previews'])==2
                    checks['all_eight_corners_safely_framed']=all(v['frame']['bbox_framing_check']['status']=='pass' and v['frame']['bbox_framing_check']['tested_points']==8 and all(.05<=p[0]<=.95 and .05<=p[1]<=.95 and p[2]>0 for p in v['frame']['bbox_framing_check']['projected_bbox_corners']) for v in d['previews'])
                if name=='custom-normals':
                    shading=d['backend']['diagnostic_shading'];checks['original_normals_present']=shading['source_face_shading']['has_custom_normals'] and shading['source_face_shading']['sharp_edges']==12
                    checks['all_level_clone_normals_reset']=not shading['verified_clone_mesh']['has_custom_normals'] and shading['verified_clone_mesh']['sharp_edges']==0
                if name=='open-quad':checks['open_boundary_not_solid']=d['control_cage']['manifold']['boundary_edges']==4 and not d['control_cage']['manifold']['closed_consistently_oriented']
                if name=='existing-subd':checks['source_stack_recorded']=len(d['backend']['source_modifier_stack'])==1 and d['backend']['source_modifier_stack'][0]['levels']==1 and d['backend']['source_modifier_stack'][0]['render_levels']==2
        checks['fixture_source_bytes_unchanged']=sha(source['file'])==before[source_name]
        rows.append({'case':name,'status':'pass' if all(checks.values()) else 'fail','checks':checks,'result':record(result_path),'error':error,'seconds':time.monotonic()-t})
        print(json.dumps(rows[-1]),flush=True)
    summary={'status':'pass' if all(r['status']=='pass' for r in rows) else 'fail','public_blenderctl':record(a.blenderctl),'plugin_root':str(ROOT),'cases':rows,'all_fixture_sources_unchanged':all(sha(v['file'])==before[k] for k,v in sources.items()),'visual_acceptance':'not_assigned_by_numeric_harness'}
    (a.out/'qualification.json').write_text(json.dumps(summary,indent=2)+'\n')
    return 0 if summary['status']=='pass' and summary['all_fixture_sources_unchanged'] else 1

if __name__=='__main__':raise SystemExit(main())
