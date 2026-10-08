"""Actual in-memory neutral fixtures only; no asset source opened or saved."""
import sys,json
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.test_sparse_panel import cases
from tests.test_quad_public import dimension_witnesses
from hardsurface.ops.quad_bridge import create,inspect_object
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
rows=[]
for label,p in cases():
    obj,receipt=create(p,'neutral_fixture',label)
    obj['hs_feature_id']='neutral_fixture';obj['hs_object_id']='neutral-'+label
    reports={};cache={}
    for state in ('control','evaluated'):
        report=inspect_object(obj,state,cache)
        assert report['passed'],(label,state,report['findings'])
        reports[state]=report
    mesh={'vertices_mm':[[float(x)*1000 for x in v.co] for v in obj.data.vertices],
          'faces':[list(f.vertices) for f in obj.data.polygons]}
    table=json.loads(obj['hs_quad_surface_table']);attr=obj.data.attributes['hs_quad_surface_id']
    mesh['face_provenance']=[table[x.value] for x in attr.data]
    dimensions=dimension_witnesses(mesh,p,nominal_tolerance_mm=.00002)
    rows.append({'case':label,'reports':reports,'dimensions':dimensions,'constructor':receipt})
result={'status':'pass','scope':'eight in-memory neutral fixtures, actual control/evaluated polygons; no saved model or visual approval','blender_version':bpy.app.version_string,'build':bpy.app.build_hash.decode(),'cases':rows}
(OUT/'native-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'status':'pass','cases':len(rows),'checks':len(rows)*2,'report':str(OUT/'native-result.json')}))
