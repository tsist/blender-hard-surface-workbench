"""Actual Blender regression of the public domain bridge using neutral fixtures."""
import sys,json,hashlib,math
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from hardsurface.ops.quad_bridge import create,inspect_object
from hardsurface.ops.geometry import digest
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
from tests.test_subd_panel import cases
case=sys.argv[sys.argv.index('--case')+1] if '--case' in sys.argv else 'neutral'
p=dict(cases())[case]
obj,receipt=create(p,'neutral_fixture','NeutralSubDCage');obj['hs_feature_id']='neutral_fixture';obj['hs_object_id']='neutral-panel'
from hardsurface.subd_cage_validation import inspect_control_loops
loop_report=inspect_control_loops(obj);(OUT/'actual-control-loops.json').write_text(json.dumps(loop_report,indent=2));assert loop_report['status']=='pass',loop_report
rows=[]
for state in (('control','evaluated') if '--fast' not in sys.argv else ()):
 r=inspect_object(obj,state,{})
 (OUT/(state+'-quality.json')).write_text(json.dumps(r,indent=2));print('STATE',state,r['passed'],r['finding_counts'],flush=True);rows.append({'state':state,'passed':r['passed']})
 bpy.context.view_layer.objects.active=obj;obj.select_set(True)
from hardsurface.subd_panel_measure import measure_panel
measurements=[]
for level in (1,2,3):
    obj.modifiers[0].levels=level;bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();eo=obj.evaluated_get(dg);mesh=eo.to_mesh()
    v=[[float(q)*1000 for q in obj.matrix_world@x.co] for x in mesh.vertices];f=[list(x.vertices) for x in mesh.polygons]
    measured=measure_panel(v,f,p,tolerance_mm=.05);measured['qualification']='legacy_numeric_diagnostic_only_not_dev6_source_bound';measurements.append({'level':level,**measured});eo.to_mesh_clear();print('MEASURE',level,measured['status'],measured['maximum_sampled_surface_distance_mm'],flush=True)
obj.modifiers[0].levels=2
(OUT/'actual-measurements.json').write_text(json.dumps(measurements,indent=2))
path=OUT/'neutral-control-cage.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path))
(OUT/'neutral-request-parameters.json').write_text(json.dumps(p,indent=2))
assert all(row['passed'] for row in rows),rows
assert all(row['status']=='pass' for row in measurements if row['level'] in (2,3)),measurements
(OUT/'bridge-result.json').write_text(json.dumps({'measurement_qualification':'legacy_numeric_diagnostic_only_not_dev6_source_bound','blender':bpy.app.version_string,'build':bpy.app.build_hash.decode(),'case':case,'checks':rows,'control_geometry_sha256':digest([list(obj.matrix_world@v.co) for v in obj.data.vertices]),'control_topology_sha256':digest([list(f.vertices) for f in obj.data.polygons]),'constructor':receipt,'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},indent=2))
