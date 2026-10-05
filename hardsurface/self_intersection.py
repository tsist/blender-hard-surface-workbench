"""Bounded actual-triangle intra-component intersection audit.

The broad phase enumerates all triangle AABB spatial-cell overlaps. Exact pair checks
use the task-authored float64 clipper; adjacent faces only permit their actual
shared indexed edge/vertex. No whole adjacent polygon is exempted.
"""
from __future__ import annotations
import math
import statistics
from collections import defaultdict
from .io import RuntimeFailure
from .robust_geometry_audit import triangle_intersection,cross,sub,dot,norm

MAX_TRIANGLES=400000
MAX_CANDIDATE_PAIRS=1000000
CLASSIFICATION_EPSILON_MM=1e-7
SHARED_BOUNDARY_EPSILON_MM=1e-6
BVH_PADDING_MM=1e-4
MAX_GRID_INSERTIONS=4000000
MAX_GRID_COMPARISONS=20000000


def _on_shared(point,shared,vertices):
    if not shared:return False
    if len(shared)==1:return math.dist(point,vertices[next(iter(shared))])<=SHARED_BOUNDARY_EPSILON_MM
    if len(shared)!=2:return False
    a,b=[vertices[i] for i in sorted(shared)];ab=sub(b,a);den=dot(ab,ab)
    if den<=0:return False
    t=dot(sub(point,a),ab)/den
    if not 0<=t<=1:return min(math.dist(point,a),math.dist(point,b))<=SHARED_BOUNDARY_EPSILON_MM
    q=[a[i]+t*ab[i] for i in range(3)]
    return math.dist(point,q)<=SHARED_BOUNDARY_EPSILON_MM


def audit_pairs(vertices_mm,triangles,polygon_indices,candidates,*,deadline=lambda:None):
    if len(triangles)>MAX_TRIANGLES or len(polygon_indices)!=len(triangles):
        raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Actual triangle domain exceeds budget')
    findings=[];count=checked=same_face=shared_edge=boundary_only=0
    for a,b in candidates:
        if b<=a:continue
        count+=1
        if count>MAX_CANDIDATE_PAIRS:raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Broad-phase pair budget exceeded')
        if count%256==0:deadline()
        if polygon_indices[a]==polygon_indices[b]:same_face+=1;continue
        ia,ib=triangles[a],triangles[b];ta=[vertices_mm[i] for i in ia];tb=[vertices_mm[i] for i in ib]
        shared=set(ia)&set(ib)
        if len(shared)==2:
            na=cross(sub(ta[1],ta[0]),sub(ta[2],ta[0]));nb=cross(sub(tb[1],tb[0]),sub(tb[2],tb[0]))
            la,lb=norm(na),norm(nb)
            if la>0 and lb>0 and norm(cross(na,nb))>1e-10*la*lb:
                # Two distinct triangle planes sharing an edge intersect only
                # along that edge. Coplanar/folded neighbors still get clipped.
                shared_edge+=1;continue
        checked+=1
        try:result=triangle_intersection(ta,tb,epsilon_mm=CLASSIFICATION_EPSILON_MM)
        except (ArithmeticError,ValueError,ZeroDivisionError) as exc:
            raise RuntimeFailure('SELF_INTERSECTION_INDETERMINATE','Exact triangle test was indeterminate',triangles=[a,b],polygons=[polygon_indices[a],polygon_indices[b]],reason=str(exc)) from exc
        points=result['points_mm']
        if not points:continue
        if all(_on_shared(point,shared,vertices_mm) for point in points):boundary_only+=1;continue
        findings.append({'triangles':[a,b],'polygons':[polygon_indices[a],polygon_indices[b]],
                         'shared_vertex_ids':sorted(shared),'points_mm':points[:8]})
        if len(findings)>=64:break
    return {'status':'fail' if findings else 'pass','triangles':len(triangles),
        'broad_phase_pairs_checked':count,'exact_pairs_checked':checked,'same_polygon_pairs_skipped':same_face,
        'shared_non_coplanar_edge_pairs_proved':shared_edge,'shared_boundary_only_pairs':boundary_only,
        'findings':findings,'finding_limit_reached':len(findings)>=64,
        'spatial_cell_padding_mm':BVH_PADDING_MM,'plane_classification_epsilon_mm':CLASSIFICATION_EPSILON_MM,
        'shared_boundary_epsilon_mm':SHARED_BOUNDARY_EPSILON_MM,
        'scope':'All bounded BVH candidate triangle pairs within one actual mesh; same source polygon excluded after quad validity; only actual shared indexed edge/point contact allowed'}


def spatial_candidates(vertices,triangles,*,deadline=lambda:None):
    """Complete AABB candidates without same-tree or adjacency exclusions."""
    bounds=[]
    for tri in triangles:
        pts=[vertices[i] for i in tri]
        bounds.append(tuple(min(p[k] for p in pts) for k in range(3))+tuple(max(p[k] for p in pts) for k in range(3)))
    if not bounds:return [],{'cell_size_mm':0,'insertions':0,'comparisons':0}
    extent=max(max(b[k+3] for b in bounds)-min(b[k] for b in bounds) for k in range(3))
    diagonals=[math.sqrt(sum((b[k+3]-b[k])**2 for k in range(3))) for b in bounds]
    cell=max(extent/64,statistics.median(diagonals)*1.25,1e-6)
    buckets=defaultdict(list);pairs=set();insertions=comparisons=0
    for i,b in enumerate(bounds):
        if i%256==0:deadline()
        lo=[math.floor((b[k]-BVH_PADDING_MM)/cell) for k in range(3)]
        hi=[math.floor((b[k+3]+BVH_PADDING_MM)/cell) for k in range(3)]
        cells=math.prod(hi[k]-lo[k]+1 for k in range(3))
        if cells>4096 or insertions+cells>MAX_GRID_INSERTIONS:
            raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Spatial broad-phase cell budget exceeded',triangle=i,insertions=insertions,cells=cells)
        for x in range(lo[0],hi[0]+1):
            for y in range(lo[1],hi[1]+1):
                for z in range(lo[2],hi[2]+1):
                    bucket=buckets[x,y,z]
                    for j in bucket:
                        comparisons+=1
                        if comparisons>MAX_GRID_COMPARISONS:
                            raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Spatial broad-phase comparison budget exceeded')
                        q=bounds[j]
                        if all(b[k]<=q[k+3] and q[k]<=b[k+3] for k in range(3)):
                            pairs.add((j,i))
                            if len(pairs)>MAX_CANDIDATE_PAIRS:
                                raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Spatial candidate pair budget exceeded')
                    bucket.append(i);insertions+=1
    return sorted(pairs),{'method':'all triangle AABBs in intersected spatial cells; no adjacency filtering',
                         'cell_size_mm':cell,'insertions':insertions,'comparisons':comparisons,'occupied_cells':len(buckets)}


def audit_blender_mesh(mesh,matrix_world,*,deadline=lambda:None):
    mesh.calc_loop_triangles()
    if len(mesh.loop_triangles)>MAX_TRIANGLES:raise RuntimeFailure('SELF_INTERSECTION_BUDGET','Actual tessellation exceeds triangle budget')
    vertices=[[float(v)*1000 for v in matrix_world@point.co] for point in mesh.vertices]
    triangles=[tuple(t.vertices) for t in mesh.loop_triangles]
    polygon_indices=[t.polygon_index for t in mesh.loop_triangles]
    pairs,phase=spatial_candidates(vertices,triangles,deadline=deadline)
    result=audit_pairs(vertices,triangles,polygon_indices,pairs,deadline=deadline)
    result['broad_phase']=phase
    result['scope']=result['scope'].replace('BVH candidate','spatial-AABB candidate')
    return result
