"""Authored diagnostic-only fixtures; saves new files, never production models."""
import bpy
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hardsurface.io import descriptor
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=True)
if any(OUT.glob('*.blend')):raise RuntimeError('New empty fixture output required')
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1.
verts=[(-.01,-.01,-.01),(.01,-.01,-.01),(.01,.01,-.01),(-.01,.01,-.01),(-.01,-.01,.01),(.01,-.01,.01),(.01,.01,.01),(-.01,.01,.01)]
faces=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
mesh=bpy.data.meshes.new('Cube real quads');mesh.from_pydata(verts,[],faces);mesh.update()
o=bpy.data.objects.new('Cube',mesh);bpy.context.scene.collection.objects.link(o)
o['hs_object_id']='9354eb53-bffe-42b9-8aa3-0a95fe1b0991'
paths={}
for name,modifier in [('cube',None),('existing-subd','SUBSURF'),('unsupported-bevel','BEVEL')]:
    for m in list(o.modifiers):o.modifiers.remove(m)
    if modifier:
        m=o.modifiers.new('Original '+modifier,modifier)
        if modifier=='SUBSURF':m.levels=1;m.render_levels=2
    path=OUT/(name+'.blend');bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False)
    paths[name]=descriptor(path)
# A custom-normal source must be preserved, while the diagnostic clone resets it.
for m in list(o.modifiers):o.modifiers.remove(m)
for poly in mesh.polygons:poly.use_smooth=True
for edge in mesh.edges:edge.use_edge_sharp=True
mesh.normals_split_custom_set([(0.,0.,1.)]*len(mesh.loops))
assert mesh.has_custom_normals
path=OUT/'custom-normals.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False);paths['custom-normals']=descriptor(path)
# Deliberately open single quad: diagnostics must identify the boundary, not
# pretend that smooth sampling makes it a watertight manufactured solid.
for m in list(o.modifiers):o.modifiers.remove(m)
openmesh=bpy.data.meshes.new('Open real quad');openmesh.from_pydata(verts[:4],[],[(0,1,2,3)]);openmesh.update();o.data=openmesh
path=OUT/'open-quad.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False);paths['open-quad']=descriptor(path)
(OUT/'sources.json').write_text(json.dumps(paths,indent=2)+'\n')
print(json.dumps({'fixtures':paths}))
