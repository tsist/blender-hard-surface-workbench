# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete bounded cross-object AABB candidates, including coplanar surfaces."""
import math
import statistics
from collections import defaultdict
from .io import RuntimeFailure

MAX_TRIANGLES = 400000
MAX_INSERTIONS = 4000000
MAX_COMPARISONS = 20000000
MAX_CANDIDATES = 1000000

def cross_candidates(vertices_a, triangles_a, vertices_b, triangles_b, *, epsilon_mm=.002):
    if len(triangles_a)+len(triangles_b)>MAX_TRIANGLES or not 0<=epsilon_mm<=.002:
        raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh input domain exceeds bound')
    def bounds(vertices,triangles):
        result=[]
        for tri in triangles:
            pts=[vertices[i] for i in tri]
            b=tuple(min(p[k] for p in pts) for k in range(3))+tuple(max(p[k] for p in pts) for k in range(3))
            if not all(math.isfinite(x) for x in b):raise RuntimeFailure('VALIDATION_GEOMETRY','Nonfinite triangle bounds')
            result.append(b)
        return result
    aa,bb=bounds(vertices_a,triangles_a),bounds(vertices_b,triangles_b)
    if not aa or not bb:return [],{'method':'complete_cross_spatial_AABB','comparisons':0,'insertions':0}
    all_bounds=aa+bb
    extent=max(max(b[k+3] for b in all_bounds)-min(b[k] for b in all_bounds) for k in range(3))
    diagonal=[math.sqrt(sum((b[k+3]-b[k])**2 for k in range(3))) for b in all_bounds]
    cell=max(extent/64,statistics.median(diagonal)*1.25,1e-6)
    buckets=defaultdict(list);insertions=comparisons=0;pairs=set()
    def cells(b):
        lo=[math.floor((b[k]-epsilon_mm)/cell) for k in range(3)]
        hi=[math.floor((b[k+3]+epsilon_mm)/cell) for k in range(3)]
        count=math.prod(hi[k]-lo[k]+1 for k in range(3))
        if count>4096:raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh triangle spatial span exceeds bound')
        for x in range(lo[0],hi[0]+1):
            for y in range(lo[1],hi[1]+1):
                for z in range(lo[2],hi[2]+1):yield (x,y,z)
    for i,b in enumerate(aa):
        for key in cells(b):
            insertions+=1
            if insertions>MAX_INSERTIONS:raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh spatial insertion bound exceeded')
            buckets[key].append(i)
    for j,b in enumerate(bb):
        for key in cells(b):
            insertions+=1
            if insertions>MAX_INSERTIONS:raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh spatial insertion bound exceeded')
            for i in buckets.get(key,()):
                comparisons+=1
                if comparisons>MAX_COMPARISONS:raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh spatial comparison bound exceeded')
                a=aa[i]
                if all(max(a[k],b[k])<=min(a[k+3],b[k+3])+epsilon_mm for k in range(3)):
                    pairs.add((i,j))
                    if len(pairs)>MAX_CANDIDATES:raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED','Cross-mesh candidate pair bound exceeded')
    return sorted(pairs),{'method':'complete_cross_spatial_AABB','cell_size_mm':cell,'insertions':insertions,'comparisons':comparisons,'occupied_cells':len(buckets),'a_triangles':len(aa),'b_triangles':len(bb),'overlap_epsilon_mm':epsilon_mm,'same_index_or_adjacency_exclusions':False}
