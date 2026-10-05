"""Authored numerical-only Blender fixtures; no practical model file is read/saved."""
import json
import sys
from pathlib import Path
import bpy
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hardsurface.ops.quad_bridge import inspect_object
from hardsurface.self_intersection import audit_blender_mesh
from hardsurface.io import RuntimeFailure

OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
vertices=[(-.005,-.005,-.005),(.005,-.005,-.005),(.005,.005,-.005),(-.005,.005,-.005),
          (-.005,-.005,.005),(.005,-.005,.005),(.005,.005,.005),(-.005,.005,.005)]
faces=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
rows=[]

def create(name,vs,fs):
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(vs,[],fs);mesh.update()
    ob=bpy.data.objects.new(name,mesh);bpy.context.scene.collection.objects.link(ob)
    attr=mesh.attributes.new('hs_quad_surface_id','INT','FACE')
    for item in attr.data:item.value=0
    ob['hs_quad_surface_table']=json.dumps([{'feature_id':'fixture','surface_id':'planar','curved':False,'support_band':False}])
    ob['hs_quad_constructor']='fixture';ob['hs_feature_id']='fixture';ob['hs_object_id']=name
    return ob

box=create('valid-box',vertices,faces)
before=[tuple(v.co) for v in box.data.vertices]
for state in ('control','evaluated'):
    report=inspect_object(box,state);assert report['passed'],report
    assert report['self_intersections']['status']=='pass'
    rows.append({'case':'valid-'+state,'status':'pass','self_intersections':report['self_intersections']})
assert before==[tuple(v.co) for v in box.data.vertices]

ngon=create('bad-ngon',vertices+[(0,-.005,.005)],[faces[0],(4,8,5,6,7),*faces[2:]])
report=inspect_object(ngon,'control');assert not report['passed'] and report['counts']['ngons']==1
rows.append({'case':'actual-ngon-rejected','status':'pass','codes':report['finding_counts']})

tri=create('bad-triangle',vertices,[faces[0],(4,5,6),(4,6,7),*faces[2:]])
report=inspect_object(tri,'control');assert not report['passed'] and report['counts']['triangles']==2
rows.append({'case':'triangle-fan-substitute-rejected','status':'pass','codes':report['finding_counts']})

smooth=create('bad-smooth-flat-datum',vertices,faces)
for poly in smooth.data.polygons:poly.use_smooth=True
smooth.data.update();report=inspect_object(smooth,'control')
assert not report['passed'] and report['shading']['affected_faces']>0
rows.append({'case':'planar-normal-distortion-rejected','status':'pass','shading':report['shading']})

mod=create('unqualified-modifier',vertices,faces);mod.modifiers.new('Triangle changer','TRIANGULATE')
try:inspect_object(mod,'evaluated');raise AssertionError('modifier accepted')
except RuntimeFailure as exc:assert exc.code=='QUAD_PROVENANCE_UNSUPPORTED'
rows.append({'case':'modifier-provenance-rejected','status':'pass'})

crossing=create('crossing-triangles',[(0,0,0),(.002,0,0),(0,.002,0),(.0005,.0005,-.001),(.0005,.0005,.001),(.0015,.0005,0)],[(0,1,2),(3,4,5)])
audit=audit_blender_mesh(crossing.data,Matrix.Identity(4));assert audit['status']=='fail' and audit['findings']
rows.append({'case':'actual-spatial-crossing-rejected','status':'pass','audit':audit})

folded=create('folded-adjacent',[(0,0,0),(.002,0,0),(0,.002,0),(.001,.001,0)],[(0,1,2),(1,0,3)])
audit=audit_blender_mesh(folded.data,Matrix.Identity(4));assert audit['status']=='fail' and audit['findings']
rows.append({'case':'actual-shared-edge-fold-rejected','status':'pass','audit':audit})

near_parallel=create('near-parallel-crossing',[(0,0,0),(3.5,0,0),(0,3.5,0),(0,0,-.000001),(3.5,0,.00000215),(0,3.5,-.000001)],[(0,1,2),(3,4,5)])
audit=audit_blender_mesh(near_parallel.data,Matrix.Identity(4));assert audit['status']=='fail' and audit['findings']
rows.append({'case':'actual-near-parallel-crossing-rejected','status':'pass','audit':audit})

# Native scope rejection uses authored temporary scenes, never a source file.
from hardsurface.generic_validation import validate_scene_scope
for kind in ('collection_instance','curve_geometry'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if kind=='collection_instance':
        collection=bpy.data.collections.new('numeric-instance-source')
        obj=bpy.data.objects.new('numeric-instancer',None);obj.instance_type='COLLECTION';obj.instance_collection=collection
    else:
        data=bpy.data.curves.new('numeric-curve','CURVE');data.dimensions='3D';data.bevel_depth=.001
        obj=bpy.data.objects.new('numeric-curve-object',data)
    bpy.context.scene.collection.objects.link(obj)
    try:validate_scene_scope(bpy.context.scene,bpy.context.evaluated_depsgraph_get())
    except RuntimeFailure as error:assert error.code=='VALIDATION_UNSUPPORTED_SCENE'
    else:raise AssertionError('Unsupported render geometry was silently omitted')
    rows.append({'case':'unsupported-'+kind+'-rejected','status':'pass'})

# Exercise the thin-wall source-witness regression with actual Blender BVHs.
bpy.ops.wm.read_factory_settings(use_empty=True)
sys.path.insert(0,str(ROOT/'tests'))
from test_generic_geometry_regressions import thin_tube, box as numeric_box
from hardsurface import generic_validation as generic
numeric=[thin_tube(),numeric_box('clear-inner-box',(.0035,.0035,1),(9.9965,9.9965,9))]
objects=[create(item.label,[[v*.001 for v in point] for point in item.vertices],item.tris) for item in numeric]
depsgraph=bpy.context.evaluated_depsgraph_get();meshes=[generic.Mesh(item.label,obj,depsgraph) for item,obj in zip(numeric,objects)]
for mesh in meshes:
    mesh.closed_outward_topology_pass=generic.evaluated_topology(mesh.vertices,mesh.tris)['status']=='pass'
    assert mesh.closed_outward_topology_pass
pair=generic.pair_check(*meshes,{})
assert pair['status']=='pass' and pair['strict_containment_count']==0 and pair['rejected_inward_witnesses']['outside']>0
rows.append({'case':'actual-thin-wall-outside-source-witness-rejected','status':'pass','pair':pair})

result={'blender_version':bpy.app.version_string,'build':bpy.app.build_hash.decode(),'cases':rows,'status':'pass',
        'scope':'In-memory authored numerical fixtures only; no source blend opened or saved'}
(OUT/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'status':'pass','cases':len(rows),'report':str(OUT/'result.json')}))
