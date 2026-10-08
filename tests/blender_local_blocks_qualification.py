"""Actual neutral meshes only; no production model is opened, saved or changed."""
import sys,json,hashlib
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.test_local_panel_blocks import cases
from tests.test_quad_public import dimension_witnesses
from hardsurface.ops.quad_bridge import create,inspect_object
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
assert bpy.app.version_string=='5.2.2 LTS',bpy.app.version_string
assert bpy.app.build_hash.decode()=='d13f752e3b9c',bpy.app.build_hash
rows=[];actual={}
for label,p in cases():
    obj,receipt=create(p,'neutral_fixture',label)
    obj['hs_feature_id']='neutral_fixture';obj['hs_object_id']='neutral-'+label
    reports={};cache={};dimensions={};meshes={}
    for state in ('control','evaluated'):
        report=inspect_object(obj,state,cache)
        assert report['passed'],(label,state,report['findings'])
        assert report['self_intersections']['status']=='pass'
        reports[state]=report
        eo=None
        if state=='evaluated':
            dg=bpy.context.evaluated_depsgraph_get();eo=obj.evaluated_get(dg);bm=eo.to_mesh(preserve_all_data_layers=True,depsgraph=dg)
        else:bm=obj.data
        mesh={'vertices_mm':[[float(x)*1000 for x in v.co] for v in bm.vertices],
              'faces':[list(f.vertices) for f in bm.polygons]}
        table=json.loads(obj['hs_quad_surface_table']);attr=bm.attributes['hs_quad_surface_id']
        mesh['face_provenance']=[table[x.value] for x in attr.data]
        dimensions[state]=dimension_witnesses(mesh,p,nominal_tolerance_mm=.00003)
        meshes[state]=mesh
        if eo:eo.to_mesh_clear()
    assert meshes['control']==meshes['evaluated']
    actual[label]=meshes['control']
    path=OUT/(label+'-actual-mesh.json');path.write_text(json.dumps(meshes,separators=(',',':')))
    rows.append({'case':label,'reports':reports,'dimensions':dimensions,'constructor':receipt,'actual_mesh':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
base=actual['neutral'];move=actual['hole_move'];thick=actual['thickness'];frame=cases()[0][1]['local_patch_bounds']
assert base['faces']==move['faces']==thick['faces']
changed=[i for i,(a,b) in enumerate(zip(base['vertices_mm'],move['vertices_mm'])) if a!=b]
outside=[i for i in changed if any(not(frame[0]<m['vertices_mm'][i][0]<frame[2] and frame[1]<m['vertices_mm'][i][1]<frame[3]) for m in (base,move))]
assert not outside
assert [v[:2] for v in base['vertices_mm']]==[v[:2] for v in thick['vertices_mm']]
assert all(v==thick['vertices_mm'][i] for i,v in enumerate(base['vertices_mm']) if v[2]==0)
def region_points(mesh,roles):
    return {tuple(mesh['vertices_mm'][i]) for f,s in zip(mesh['faces'],mesh['face_provenance']) if s.get('patch_role') in roles for i in f}
assert region_points(actual['corner6'],{'hole_local_ogrid','hole_local_4_to_2','axial_macroblocks'})==region_points(actual['corner12_same_shape'],{'hole_local_ogrid','hole_local_4_to_2','axial_macroblocks'})
result={'status':'pass','scope':'ten independently authored in-memory neutral parameter fixtures; actual control/evaluated polygons, analytic dimensions, native self-intersection gate and actual E1/E2-style edits; no B01 asset or visual approval','blender_version':bpy.app.version_string,'build':bpy.app.build_hash.decode(),'cases':rows,'actual_corner_sampling_edit':{'quarter_arc_segments_before_after':[6,12],'hole_and_frame_and_macroblock_point_sets_identical':True},'actual_edits':{'hole_move':{'changed_vertices':len(changed),'changed_outside_fixed_frame':outside,'polygon_connectivity_identical':True},'thickness':{'xy_identical':True,'polygon_connectivity_identical':True,'bottom_vertices_identical':True}}}
(OUT/'native-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'status':'pass','cases':len(rows),'checks':len(rows)*2,'report':str(OUT/'native-result.json')}))
