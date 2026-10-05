"""Small pure helpers for authored surface assemblies (millimetres)."""
from __future__ import annotations
from collections import defaultdict, deque
import math
from .io import RuntimeFailure


def finish_mesh(mesh, *, feature_id):
    """Orient a closed authored patch complex without adding/removing faces."""
    vertices=mesh.get('vertices_mm',mesh.get('vertices'))
    faces=[list(f) for f in mesh['faces']]
    provenance=mesh.get('face_provenance',[])
    if len(faces)!=len(provenance):
        raise RuntimeFailure('QUAD_PROVENANCE_INVALID','Patch assembly lost surface provenance')
    edges=defaultdict(list)
    for fi,face in enumerate(faces):
        if len(face)!=4 or len(set(face))!=4:
            raise RuntimeFailure('QUAD_INVALID_FACE','Patch assembly emitted a non-quad or repeated corner',face=fi)
        for a,b in zip(face,face[1:]+face[:1]):
            edges[tuple(sorted((a,b)))].append((fi,1 if a<b else -1))
    bad=[{'edge':list(e),'count':len(rows)} for e,rows in edges.items() if len(rows)!=2]
    if bad:
        raise RuntimeFailure('QUAD_SEAM_NOT_CLOSED','Planned patch boundaries failed to stitch exactly',count=len(bad),examples=bad[:32])
    neighbors=defaultdict(list)
    for rows in edges.values():
        (a,sa),(b,sb)=rows
        neighbors[a].append((b,sa==sb));neighbors[b].append((a,sa==sb))
    flips={0:False};queue=deque([0])
    while queue:
        a=queue.popleft()
        for b,opposite in neighbors[a]:
            wanted=flips[a]^opposite
            if b in flips:
                if flips[b]!=wanted:raise RuntimeFailure('QUAD_NONORIENTABLE','Surface complex is not orientable')
            else:flips[b]=wanted;queue.append(b)
    if len(flips)!=len(faces):
        raise RuntimeFailure('QUAD_DISCONNECTED_BODY','Component must have exactly one connected surface shell')
    faces=[list(reversed(f)) if flips[i] else f for i,f in enumerate(faces)]
    def det(a,b,c):
        return a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0])
    volume=sum(det(vertices[f[0]],vertices[f[i]],vertices[f[i+1]])/6 for f in faces for i in (1,2))
    if not math.isfinite(volume) or abs(volume)<1e-9:
        raise RuntimeFailure('QUAD_ZERO_VOLUME','Patch assembly has no finite positive volume')
    if volume<0:faces=[list(reversed(f)) for f in faces];volume=-volume
    for row in provenance:
        row.setdefault('feature_id',feature_id)
        row.setdefault('surface_id',row.get('surface_role','structured_surface'))
        row.setdefault('smooth',False)
        row.setdefault('curved',False)
        row.setdefault('support_band',False)
    sharp=[]
    for edge,rows in edges.items():
        a,b=(provenance[fi] for fi,_ in rows)
        if a['surface_id']!=b['surface_id']:sharp.append(list(edge))
    metadata=mesh.get('metadata',{})
    metadata.update({'signed_volume_mm3':volume,'construction':'shared_boundary_quad_patches',
                     'source_faces':{'quads':len(faces),'triangles':0,'ngons':0},
                     'bounds_mm':[min(v[k] for v in vertices) for k in range(3)]+[max(v[k] for v in vertices) for k in range(3)]})
    return {'vertices_mm':vertices,'faces':faces,'face_provenance':provenance,
            'sharp_edges':sharp,'metadata':metadata}


def boundary_loops(faces):
    counts=defaultdict(list)
    for face in faces:
        for a,b in zip(face,face[1:]+face[:1]):counts[tuple(sorted((a,b)))].append((a,b))
    outgoing=defaultdict(list)
    for rows in counts.values():
        if len(rows)==1:
            a,b=rows[0];outgoing[a].append(b);outgoing[b].append(a)
    if any(len(rows)!=2 for rows in outgoing.values()):
        raise RuntimeFailure('QUAD_BOUNDARY_BRANCH','Surface boundary is not a disjoint set of closed loops')
    result=[];seen=set()
    for start in sorted(outgoing):
        if start in seen:continue
        loop=[];previous=None;current=start
        while current not in seen:
            seen.add(current);loop.append(current)
            nxt=next(v for v in outgoing[current] if v!=previous)
            previous,current=current,nxt
        if current!=start:raise RuntimeFailure('QUAD_BOUNDARY_BRANCH','Boundary entered another loop')
        result.append(loop)
    return result
