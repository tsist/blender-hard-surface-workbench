"""Native adapter for source-bound anchors and bounded diagnostic sampling.

Only derived triangulation/BVH, temporary light placement and image reads.
No mesh/shading/source authoring. HOST import safe; Blender imports are lazy.
"""
from . import reflection_anchor as a


def prepare(proxy,view,q,source_sha,normal_record,camera_record,right,up,outward):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    matrix=[[float(x) for x in row] for row in proxy.matrix_world]
    identity=a.require_identity(q,source_sha,normal_record,matrix,view)
    mesh=proxy.data
    mesh.calc_loop_triangles()
    tris=list(mesh.loop_triangles)
    if q['triangle_index']>=len(tris):a.fail('Anchor triangle index out of bounds')
    verts=[tuple((proxy.matrix_world@v.co)*1000) for v in mesh.vertices]
    topology=[tuple(t.vertices) for t in tris]
    # All distances in mm. zero BVH epsilon avoids inflating diagnostic surfaces.
    tree=BVHTree.FromPolygons(verts,topology,all_triangles=True,epsilon=0.0)
    normal_matrix=proxy.matrix_world.to_3x3().inverted().transposed()
    def normal_at(index,weights):
        normals=[tuple(normal_matrix@mesh.corner_normals[j].vector) for j in tris[index].loops]
        return a.unit(a.weighted(normals,weights))
    tri=tris[q['triangle_index']]
    if list(tri.vertices)!=q['triangle_vertices'] or list(tri.loops)!=q['triangle_loops'] or tri.polygon_index!=q['polygon_index']:
        a.fail('Anchor native triangle/corner/face correspondence mismatch')
    p=a.weighted([verts[i] for i in tri.vertices],q['barycentric']);n=normal_at(q['triangle_index'],q['barycentric'])
    c=tuple(camera_record['position_mm']);v=a.unit(outward);right=a.unit(right);up=a.unit(up)
    scale=camera_record['ortho_scale_mm'];aspect=view['width']/view['height'];rel=a.sub(p,c)
    px=.5+a.dot(rel,right)/(scale*aspect);py=.5+a.dot(rel,up)/scale
    x0,y0,x1,y1=q['roi']
    if not(x0<=px<=x1 and y0<=py<=y1) or not(-camera_record['clip_end_m']*1000<a.dot(rel,v)<-camera_record['clip_start_m']*1000):
        a.fail('Anchor outside ROI or camera clip interval')
    origin=a.sub(p,a.mul(v,a.dot(rel,v)))
    near=camera_record['clip_start_m']*1000;far=camera_record['clip_end_m']*1000
    origin=a.sub(origin,a.mul(v,near))
    hit,_,idx,_=tree.ray_cast(Vector(origin),Vector(a.mul(v,-1)),far-near)
    # Fail closed at shared-edge/coincident ties: the selected triangle must be
    # the actual first hit, not merely a coincident point on another face.
    if idx!=q['triangle_index'] or hit is None or a.dot(a.sub(tuple(hit),p),a.sub(tuple(hit),p))>1e-8:
        a.fail('Selected anchor is not first-hit visible from bound camera')
    return {'identity':identity,'point':p,'normal':n,'tree':tree,'triangles':tris,'verts':verts,'normal_at':normal_at,
            'camera':camera_record,'right':right,'up':up,'outward':v,'anchor_pixel_normalized':[px,py]}


def predict(context,view,q,plan):
    from mathutils import Vector
    tree=context['tree'];camera=context['camera'];v=context['outward'];right=context['right'];up=context['up'];c=camera['position_mm']
    width,height=view['width'],view['height'];scale=camera['ortho_scale_mm'];aspect=width/height
    x0,y0,x1,y1=q['roi'];samples=[];hits=0;occluded=0;normal_back=0;outside_roi=0
    nx=min(32,max(1,int((x1-x0)*width)));ny=min(32,max(1,int((y1-y0)*height)))
    near=camera['clip_start_m']*1000;far=camera['clip_end_m']*1000
    for gy in range(ny):
        for gx in range(nx):
            ix=min(width-1,int((x0+(gx+.5)*(x1-x0)/nx)*width));iy=min(height-1,int((y0+(gy+.5)*(y1-y0)/ny)*height))
            sx=(ix+.5)/width;sy=(iy+.5)/height
            if not(x0<=sx<=x1 and y0<=sy<=y1):outside_roi+=1;continue
            origin=a.add(a.add(c,a.mul(right,(sx-.5)*scale*aspect)),a.mul(up,(sy-.5)*scale));origin=a.sub(origin,a.mul(v,near))
            p,_,ti,distance=tree.ray_cast(Vector(origin),Vector(a.mul(v,-1)),far-near)
            if p is None:continue
            p=tuple(p);tri=context['triangles'][ti];weights=a.barycentric(p,*[context['verts'][j] for j in tri.vertices]);n=context['normal_at'](ti,weights)
            if a.dot(n,v)<=0:normal_back+=1;samples.append((ix,iy));continue
            d=a.unit(a.reflect(v,n));sample_hit=False
            for light in plan['lights']:
                t=a.ray_rectangle(p,d,light)
                if t is None:continue
                # Offset is diagnostic ray self-hit avoidance only; no geometry
                # inflation or change. Explicitly reported, not silently tuned.
                epsilon=1e-4
                obstacle,_,_,obstacle_distance=tree.ray_cast(Vector(a.add(p,a.mul(d,epsilon))),Vector(d),max(0,t-epsilon))
                if obstacle is not None and obstacle_distance<t-epsilon:occluded+=1;continue
                sample_hit=True;break
            hits+=int(sample_hit);samples.append((ix,iy))
    result={'status':'informative' if hits else 'fail','test':'sampled_native_mirror_ray_rectangle_hits',
            'grid':[nx,ny],'grid_rays':nx*ny,'pixel_centers_outside_roi_skipped':outside_roi,'visible_surface_samples':len(samples),'mirror_hit_samples':hits,
            'mirror_hit_fraction_of_visible_samples':hits/len(samples) if samples else None,
            'blocked_emitter_candidates':occluded,'back_facing_shading_normal_samples':normal_back,
            'self_ray_offset_mm':1e-4,'roi_image_normalized_bottom_left':q['roi'],
            'limits':['Finite first-hit camera and light-ray visibility on sole bound object','Native corner normals interpolated for ray calculation; not Cycles radiance prediction','Fixed grid can miss narrow bands and small features','Informative means mirror hits found, never full coverage or surface-quality pass'],
            'surface_quality_inference':'none'}
    if not samples:result.update(status='inconclusive',reason='No camera-visible samples in ROI')
    return result,samples


def observe_png(path,view,samples):
    import bpy
    image=None
    try:
        image=bpy.data.images.load(str(path),check_existing=False)
        if tuple(image.size)!=(view['width'],view['height']):a.fail('Observed PNG dimensions changed')
        stride=len(image.pixels)//(view['width']*view['height'])
        if stride not in (3,4) or len(image.pixels)!=view['width']*view['height']*stride:
            a.fail('Expected Blender-decoded RGB or RGBA pixel interface')
        width=view['width'];values=[]
        for x,y in samples:
            start=(y*width+x)*stride;values.append(tuple(image.pixels[start:start+3]))
        return a.observed_summary(values,len(samples))
    finally:
        if image is not None:bpy.data.images.remove(image)
