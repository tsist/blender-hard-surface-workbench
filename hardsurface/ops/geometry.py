"""Mesh construction and independent geometry inspection (Blender process only)."""
from __future__ import annotations
import hashlib, json, math
import bpy, bmesh
from mathutils import Vector
from mathutils.geometry import tessellate_polygon
from ..identity import topology_hash,geometry_hash

class GeometryError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message); self.code=code; self.details=details or {}

def digest(value):
    # Same numeric/UTF-8 canonical semantics as Contract, without applying
    # request byte/node limits to independently bounded generated mesh evidence.
    def clean(v):
        if isinstance(v,float):return 0.0 if v==0 else v
        if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(clean(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

def area(loop):
    return sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(loop,loop[1:]+loop[:1]))/2

def orient(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

def on_segment(a,b,p,eps):
    return abs(orient(a,b,p))<=eps and min(a[0],b[0])-eps<=p[0]<=max(a[0],b[0])+eps and min(a[1],b[1])-eps<=p[1]<=max(a[1],b[1])+eps

def intersects(a,b,c,d,eps):
    o=[orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)]
    if o[0]*o[1]<-eps*eps and o[2]*o[3]<-eps*eps:return True
    return any((abs(v)<=eps and on_segment(x,y,p,eps)) for v,x,y,p in [(o[0],a,b,c),(o[1],a,b,d),(o[2],c,d,a),(o[3],c,d,b)])

def inside(p,loop):
    x,y=p; hit=False
    for a,b in zip(loop,loop[1:]+loop[:1]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:hit=not hit
    return hit

def validate_profile(loops):
    """Accept one outer boundary and disjoint one-level holes; no guessed nesting."""
    if not isinstance(loops,list) or not loops:raise GeometryError('PROFILE_EMPTY','Profile needs an outer loop')
    if sum(len(l) for l in loops)>100000:raise GeometryError('PROFILE_LIMIT','Profile exceeds bounded vertex limit')
    result=[]
    scale=max([abs(float(v)) for loop in loops for p in loop for v in p]+[1e-3])
    eps=max(1e-18,scale*scale*1e-12)
    for li,raw in enumerate(loops):
        loop=[[float(p[0]),float(p[1])] for p in raw]
        if len(loop)<3 or any(len(p)!=2 or not all(math.isfinite(x) for x in p) for p in raw):raise GeometryError('PROFILE_INVALID','Finite 2D loops of >=3 vertices required')
        if len(loop)>2000:raise GeometryError('PROFILE_LIMIT','Quadratic independent boundary validator supports <=2000 vertices per loop')
        edges=list(zip(loop,loop[1:]+loop[:1]))
        if any(math.dist(a,b)<=math.sqrt(eps)*1e-3 for a,b in edges):raise GeometryError('PROFILE_ZERO_EDGE','Zero-length profile edge')
        for i,(a,b) in enumerate(edges):
            for j,(c,d) in enumerate(edges):
                if j<=i or (j-i)==1 or (i==0 and j==len(edges)-1):continue
                if intersects(a,b,c,d,eps):raise GeometryError('PROFILE_SELF_INTERSECTION','Profile loop self-intersects',{'loop':li})
        ar=area(loop)
        if abs(ar)<=eps:raise GeometryError('PROFILE_ZERO_AREA','Profile area is zero')
        if (ar>0)!=(li==0):loop.reverse()
        result.append(loop)
    for i in range(1,len(result)):
        if not inside(result[i][0],result[0]):raise GeometryError('PROFILE_HOLE_OUTSIDE','Hole lies outside outer loop')
        for j in range(i):
            for a,b in zip(result[i],result[i][1:]+result[i][:1]):
                for c,d in zip(result[j],result[j][1:]+result[j][:1]):
                    if intersects(a,b,c,d,eps):raise GeometryError('PROFILE_BOUNDARY_INTERSECTION','Profile boundaries intersect or touch')
            if j>0 and (inside(result[i][0],result[j]) or inside(result[j][0],result[i])):raise GeometryError('PROFILE_NESTED_HOLES','Nested holes/islands need explicitly separate profiles')
    return result

def caps(loops):
    pts=[Vector((x,y,0)) for loop in loops for x,y in loop]
    vectors=[]; i=0
    for loop in loops:vectors.append(pts[i:i+len(loop)]);i+=len(loop)
    raw=tessellate_polygon(vectors)
    if raw and not isinstance(raw[0][0],int):
        lookup={tuple(p):i for i,p in enumerate(pts)}; tris=[tuple(lookup[tuple(v)] for v in t) for t in raw]
    else:tris=[tuple(t) for t in raw]
    actual=sum(abs(orient(pts[a],pts[b],pts[c]))/2 for a,b,c in tris)
    expected=sum(area(l) for l in loops)
    if not math.isclose(actual,expected,rel_tol=2e-5,abs_tol=1e-12):raise GeometryError('PROFILE_TRIANGULATION','Triangulation area mismatches outer minus holes',{'expected':expected,'actual':actual})
    corrected=[]
    for a,b,c in tris:
        center=[(pts[a][k]+pts[b][k]+pts[c][k])/3 for k in (0,1)]
        if not inside(center,loops[0]) or any(inside(center,h) for h in loops[1:]):raise GeometryError('PROFILE_TRIANGULATION','Cap triangle enters excluded hole')
        corrected.append((a,b,c) if orient(pts[a],pts[b],pts[c])>0 else (c,b,a))
    return pts,corrected

def new_mesh(name,verts,faces):
    mesh=bpy.data.meshes.new(name+'.mesh'); mesh.from_pydata(verts,[],faces);mesh.update(calc_edges=True)
    bm=bmesh.new();bm.from_mesh(mesh);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
    ob=bpy.data.objects.new(name,mesh);bpy.context.scene.collection.objects.link(ob);return ob

def create_extrusion(name,loops,depth):
    if not math.isfinite(depth) or depth<=0:raise GeometryError('DEPTH_INVALID','Depth must be positive')
    loops=validate_profile(loops);pts,tris=caps(loops);n=len(pts)
    verts=[(p.x,p.y,z) for z in (0,depth) for p in pts]
    # Preserve one planar face for a simple cap after independent triangulation
    # proof. Materialized skinny interior diagonals can spuriously constrain
    # Blender bevel overlap even though they are not design edges.
    if len(loops)==1:
        faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
        cap_representation='validated_simple_ngon'
    else:
        faces=[tuple(reversed(t)) for t in tris]+[tuple(v+n for v in t) for t in tris]
        cap_representation='validated_hole_triangulation'
    start=0
    for loop in loops:
        for j in range(len(loop)):
            a=start+j;b=start+(j+1)%len(loop);faces.append((a,b,b+n,a+n))
        start+=len(loop)
    obj=new_mesh(name,verts,faces)
    return obj,{'profile_area_m2':sum(area(l) for l in loops),'expected_volume_m3':sum(area(l) for l in loops)*depth,'depth_m':depth,'profile_vertices':n,'holes':len(loops)-1,'cap_representation':cap_representation,'cap_triangulation_proof_triangles':len(tris)}

def create_revolution(name,loops,axis,angle_deg,segments):
    """Profile x is radius and y is axial coordinate, explicitly mapped onto axis."""
    loops=validate_profile(loops);pts,tris=caps(loops)
    if axis not in ('X','Y','Z') or not (0<angle_deg<=360) or segments<3:raise GeometryError('REVOLVE_INVALID','Invalid axis, angle or sampling')
    if any(p.x<0 for p in pts):raise GeometryError('REVOLVE_AXIS_CROSSING','Profile crosses radius zero')
    if max(p.x for p in pts)<=1e-10:raise GeometryError('REVOLVE_ZERO_RADIUS','Degenerate radius')
    full=abs(angle_deg-360)<1e-8; rings=segments if full else segments+1; verts=[]; mapping={};axisverts={}
    for ri in range(rings):
        theta=math.radians(angle_deg)*ri/segments
        for j,p in enumerate(pts):
            if abs(p.x)<=1e-12 and j in axisverts:mapping[ri,j]=axisverts[j];continue
            r,z=p.x,p.y; a,b=r*math.cos(theta),r*math.sin(theta)
            v=(z,a,b) if axis=='X' else (b,z,a) if axis=='Y' else (a,b,z)
            mapping[ri,j]=len(verts);verts.append(v)
            if abs(p.x)<=1e-12:axisverts[j]=mapping[ri,j]
    faces=[];start=0
    for loop in loops:
        for j in range(len(loop)):
            a=start+j;b=start+(j+1)%len(loop)
            for ri in range(segments):
                rj=(ri+1)%rings;face=[mapping[ri,a],mapping[ri,b],mapping[rj,b],mapping[rj,a]]
                face=list(dict.fromkeys(face))
                if len(face)>=3:faces.append(face)
        start+=len(loop)
    if not full:
        faces.extend(tuple(mapping[0,j] for j in reversed(t)) for t in tris)
        faces.extend(tuple(mapping[rings-1,j] for j in t) for t in tris)
    # Polygonal sweep exact volume is first area moment times polygonal angular factor.
    moment=0.0
    for loop in loops:
        moment+=sum((a[0]+b[0])*(a[0]*b[1]-b[0]*a[1]) for a,b in zip(loop,loop[1:]+loop[:1]))/6
    expected=abs(moment*segments*math.sin(math.radians(angle_deg)/segments))
    obj=new_mesh(name,verts,faces)
    return obj,{'expected_volume_m3':expected,'axis':axis,'angle_deg':angle_deg,'segments':segments,'radial_chord_error_m':max(p.x for p in pts)*(1-math.cos(math.radians(angle_deg)/segments/2)),'coordinate_semantics':'profile x = nonnegative radius; profile y = axis coordinate'}

def require_si_scene():
    units=bpy.context.scene.unit_settings
    if float(units.scale_length)!=1.0:
        raise GeometryError('SOURCE_UNIT_SCALE_UNSUPPORTED','Saved scene unit scale must be exactly 1 metre per coordinate unit; no implicit rescale is permitted',{'actual_scale_length':float(units.scale_length),'required_scale_length':1.0})
    return {'scale_length':float(units.scale_length),'system':units.system,'length_unit':units.length_unit,'system_rotation':units.system_rotation,'use_separate':units.use_separate}

def mesh_metrics(obj,evaluated=True):
    require_si_scene()
    bpy.context.view_layer.update(); dg=bpy.context.evaluated_depsgraph_get()
    eo=obj.evaluated_get(dg) if evaluated else obj
    mesh=eo.to_mesh(preserve_all_data_layers=True,depsgraph=dg) if evaluated else obj.data
    try:
        matrix=eo.matrix_world; verts=[tuple(matrix@v.co) for v in mesh.vertices]
        faces=[list(p.vertices) for p in mesh.polygons];edges=[list(e.vertices) for e in mesh.edges]
        bm=bmesh.new();bm.from_mesh(mesh);bm.transform(matrix)
        bounds=[min(v[k] for v in verts) for k in range(3)]+[max(v[k] for v in verts) for k in range(3)] if verts else [0]*6
        volume=bm.calc_volume(signed=True) if bm.faces else 0.0
        metrics={'vertices':len(verts),'edges':len(edges),'faces':len(faces),'loops':len(mesh.loops),'bounds_m':bounds,'dimensions_m':[bounds[k+3]-bounds[k] for k in range(3)],'signed_volume_m3':volume,'volume_m3':abs(volume),'area_m2':sum(f.calc_area() for f in bm.faces),'boundary_edges':sum(e.is_boundary for e in bm.edges),'nonmanifold_edges':sum(not e.is_manifold for e in bm.edges),'wire_edges':sum(e.is_wire for e in bm.edges),'inconsistent_winding_edges':sum(e.is_manifold and not e.is_contiguous for e in bm.edges),'degenerate_faces':sum(f.calc_area()<1e-16 for f in bm.faces),'finite':all(math.isfinite(c) for v in verts for c in v),'topology_sha256':topology_hash(len(verts),edges,faces),'geometry_sha256':geometry_hash([[round(c,10) for c in v] for v in verts])}
        bm.free();return metrics
    finally:
        if evaluated:eo.to_mesh_clear()

def require_closed(obj):
    m=mesh_metrics(obj)
    if not m['finite'] or m['boundary_edges'] or m['nonmanifold_edges'] or m['degenerate_faces'] or m['volume_m3']<=1e-16:raise GeometryError('GEOMETRY_INVALID','Evaluated result is not a finite, nondegenerate, closed volumetric mesh',m)
    return m
