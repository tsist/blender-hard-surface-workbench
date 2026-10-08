"""Bounded single-hole panel face layout with local density transitions.

This is an explicit polygon mesh, not a SubD cage optimizer. Curved boundary
samples terminate into two coarse routing rings on the planar datum. No Boolean,
triangle, ngon, dissolve, repair or normal operation chooses the face layout.
"""
from __future__ import annotations
import math
from collections import Counter
from .io import RuntimeFailure
from .quad_patches import (MeshBuilder, _lerp, _planar_quad_metrics,
                           circle_segments, quarter_arc_segments)


def _error(message, **details):
    raise RuntimeFailure('QUAD_SPARSE_DOMAIN', message, **details)


def _domain(p):
    holes=p.get('holes',[])
    if len(holes)!=1 or holes[0]['kind']!='circle' or holes[0].get('counterbore') or p.get('lips'):
        _error('Sparse annulus requires exactly one circular through-hole, without counterbore or lips')
    w,h=p['size'];r=p['corner_radius'];b=p.get('edge_bevel',0.)
    cx,cy=p.get('center',[0,0]);hx,hy=holes[0]['center'];hr=holes[0]['radius']
    # Ratio limits are candidate qualification bounds, never design defaults.
    if not (1<=w/h<=2.2 and .08<=r/h<=.22 and .08<=hr/h<=.18
            and abs(hx-cx)<=.18*w and abs(hy-cy)<=.08*h):
        _error('Sparse annulus is outside its bounded width/depth, radius or eccentricity domain',
               width_depth_ratio=w/h, outline_radius_depth_ratio=r/h,
               hole_radius_depth_ratio=hr/h, normalized_offset=[(hx-cx)/w,(hy-cy)/h])
    if not (0<=b<min(r,(p['z_max']-p['z_min'])/2) and b<=.04*h):
        _error('Sparse annulus round-over exceeds the bounded geometric domain')
    if min(w/2-abs(hx-cx)-hr,h/2-abs(hy-cy)-hr)<=b:
        _error('Hole must remain strictly inside the flat panel face')
    target=p.get('target_edge_length',4.);tol=p.get('chord_tolerance',.025)
    if not (math.isfinite(target) and target>0 and math.isfinite(tol) and tol>0):
        _error('Sampling controls must be finite and positive')
    return w,h,r,b,cx,cy,hx,hy,hr,target,tol


def _outline(w,h,r,nx,ny,na):
    out=[]
    data=[((-w/2+r,-h/2),(w/2-r,-h/2),(w/2-r,-h/2+r),nx),
          ((w/2,-h/2+r),(w/2,h/2-r),(w/2-r,h/2-r),ny),
          ((w/2-r,h/2),(-w/2+r,h/2),(-w/2+r,h/2-r),nx),
          ((-w/2,h/2-r),(-w/2,-h/2+r),(-w/2+r,-h/2+r),ny)]
    for side,(a,b,c,n) in enumerate(data):
        out.extend(_lerp(a,b,i/n) for i in range(n))
        out.extend((c[0]+r*math.cos(-math.pi/2+side*math.pi/2+i/na*math.pi/2),
                    c[1]+r*math.sin(-math.pi/2+side*math.pi/2+i/na*math.pi/2)) for i in range(na))
    return out


def _strip(a,b):
    return [(a[i],a[(i+1)%len(a)],b[(i+1)%len(a)],b[i]) for i in range(len(a))]


def _ccw(faces):
    return [tuple(reversed(f)) if sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(f,f[1:]+f[:1]))<0 else f for f in faces]


def _quality(faces):
    return _planar_quad_metrics(_ccw(faces))


def _eligible(m):
    # These are local planning preferences, stricter than the unchanged final
    # production gate. A planner pass never grants visual or Blender acceptance.
    return m['minimum_angle_degrees']>=8 and m['maximum_angle_degrees']<=174.5 and m['maximum_aspect_ratio']<=40


def _outline_transition(fine, coarse, schedule):
    faces=[];offset=0
    for i,count in enumerate(schedule):
        q0,q1=coarse[i],coarse[(i+1)%len(coarse)]
        pp=[fine[(offset+j)%len(fine)] for j in range(count+1)]
        if count==1:faces.append((pp[0],q0,q1,pp[1]))
        else:
            p0,p1,p2,p3=pp
            faces.extend(((p0,p1,p2,q0),(p2,p3,q1,q0)))
        offset+=count
    return faces


def plan_sparse_annulus(p):
    """Separate corner sampling from the flat routing and circular rim counts."""
    w,h,r,b,cx,cy,hx,hy,hr,target,tol=_domain(p)
    maximum=p.get('max_segments',512)
    # Two-direction sum budget protects the coupled toroidal corner round-over.
    outline_tol=tol/2 if b else tol
    nx=max(1,math.ceil((w-2*r)/target));ny=max(1,math.ceil((h-2*r)/target))
    na=3*math.ceil(quarter_arc_segments(r,outline_tol,maximum)/3)
    outline_actual_sag=r*(1-math.cos(math.pi/(4*na)))
    roundover_tol=tol-outline_actual_sag if b else 0.
    if na>15:
        raise RuntimeFailure('QUAD_SPARSE_LAYOUT','Corner count reduction requires quarter-arc segments <=15 to preserve corner-angle margin; finer chord requirements need another strategy')
    required=circle_segments(hr,tol,maximum)
    count=2*(nx+ny)+4*(na//3)
    while count<required or count%8:
        if (w-2*r)/nx >= (h-2*r)/ny:nx+=1
        else:ny+=1
        count+=2
    n=2*(nx+ny)+4*na
    if max(n,count)>maximum or n>384 or math.ceil(max(w,h)/target)>512:
        raise RuntimeFailure('QUAD_SAMPLING_BUDGET','Sparse annulus sampling exceeds bounded work before allocation',outline=n,hole=count,maximum=maximum)
    outer=[(x+cx,y+cy) for x,y in _outline(w-2*b,h-2*b,r-b,nx,ny,na)]
    schedule=[]
    for straight in (nx,ny,nx,ny):schedule.extend([1]*straight+[3]*(na//3))
    starts=[];offset=0
    for step in schedule:starts.append(offset);offset+=step
    hole=[(hx+hr*math.cos(-3*math.pi/4+i*math.tau/count),hy+hr*math.sin(-3*math.pi/4+i*math.tau/count)) for i in range(count)]
    # Only the curved silhouette has density-changing blocks. The hole retains
    # a continuous O-loop without any 3:1 poles or corner-ray union samples.
    outline_candidates=[]
    for scale in (.90,.86,.82,.94):
        coarse=[(cx+(outer[i][0]-cx)*scale,cy+(outer[i][1]-cy)*scale) for i in starts]
        transition=_outline_transition(outer,coarse,schedule)
        quality=_quality(transition)
        if _eligible(quality):outline_candidates.append((quality,scale,coarse,transition))
    if not outline_candidates:
        raise RuntimeFailure('QUAD_SPARSE_LAYOUT','No bounded local corner transition meets the planning targets')
    # Select silhouette construction independently of hole POSITION/thickness.
    # Hole radius can raise the correspondence count; no size-edit isolation is claimed.
    oq,scale,coarse_outer,transition=max(outline_candidates,key=lambda x:x[0]['minimum_angle_degrees'])
    eligible=[]
    for bands in range(1,5):
        bridge=[];previous=hole
        for j in range(1,bands+1):
            current=[_lerp(a,z,j/bands) for a,z in zip(hole,coarse_outer)]
            bridge.extend(_strip(previous,current));previous=current
        quality=_quality(bridge+transition)
        if _eligible(quality):
            eligible.append((bands,bridge,quality));break
    if not eligible:
        raise RuntimeFailure('QUAD_SPARSE_LAYOUT','Sparse routing does not meet local planning targets; default strategy was not substituted',quality=quality)
    bands,bridge,quality=eligible[0]
    roles=[('coarse_planar_spans',bridge),('outline_local_transition',transition)]
    return {'outer':outer,'hole':hole,'roles':[(role,_ccw(ff)) for role,ff in roles],
        'metadata':{'strategy':'sparse_annulus','version':1,'local_planning_targets':{'minimum_angle_degrees':8,'maximum_angle_degrees':174.5,'maximum_aspect_ratio':40},'layout_quality':quality,
            'outline_sampling':{'horizontal_straight_segments':nx,'vertical_straight_segments':ny,'quarter_arc_segments':na},
            'error_budget_mm':{'requested_chord_tolerance':tol,'scope':'additional conservative compound-surface sampling budget; original tolerance remains independent arc contract','outline_direction':outline_tol,'roundover_direction':roundover_tol,'hole_direction':tol,'outline_actual_sag':outline_actual_sag,'coupled_corner_bound':outline_actual_sag+roundover_tol},
            'hole_sampling':{'minimum_chord_segments':required,'chosen_segments':count,'extra_samples_reason':'matched coarse outline routing count, rounded to eight for exact bore-axis samples; no corner-angle ray union'},
            'ring_counts':{'hole_boundary':count,'outline_coarse_route':count,'outline_boundary':n},
            'ring_roles':{'hole_boundary':'circle chord tolerance; continuous editable O-loop with no rim reduction poles','outline_coarse_route':'coarse routing ring with corner-only 3:1 transitions','outline_boundary':'independent straight and quarter-circle sampling'},
            'ring_parameters':{'outline_coarse_scale':scale,'coarse_span_bands':bands},
            'transition_counts':{'curved_corner_reduction_blocks':4*(na//3),'quads_per_reduction_block':2,'one_to_one_straight_blocks':2*(nx+ny)},
            'per_face_region_counts':{role:len(ff) for role,ff in roles},
            'parameter_roles':{'chord_tolerance':'shared directional error budget for coupled corner round-over, separate hole chord bound','target_edge_length':'maximum straight outline span and wall height per segment; planar spans use the smallest passing count in 1..4, independent of global target length','max_segments':'upper count per authored curved boundary'},
            'limitations':'bounded one circular through-hole; explicit mesh only; no SubD, visual quality or globally minimum face count claim'}}


def emit_sparse_face(builder,plan,z,provenance):
    first=len(builder.faces)
    for role,faces in plan['roles']:
        source={**provenance,'patch_role':role}
        for face in faces:
            ids=[builder.vertex((*point,z)) for point in face]
            builder.quad(*ids,provenance=source)
    return {'outer':builder.loop([(*q,z) for q in plan['outer']]),
            'hole':builder.loop([(*q,z) for q in plan['hole']]),
            'face_range':(first,len(builder.faces))}


def actual_sampling_evidence(mesh,p):
    """Measure actual emitted angular spans, independently of planned counts.

    Each corner round-over vertex is on a tensor-product torus patch. For any
    triangle-interior convex combination, replacing the averaged XY direction
    by its normalized direction moves it at most R*(1-cos(dtheta/2)); replacing
    the averaged meridian by its circular arc moves it at most b*(1-cos(dphi/2)).
    Triangle inequality bounds distance to an ideal point in the same patch by
    the sum. This is an added conservative surface diagnostic, not a rewrite of
    the approved independent-arc design tolerance. Both quad diagonals obey it.
    """
    cx,cy=p.get('center',[0,0]);w,h=p['size'];r=p['corner_radius'];b=p.get('edge_bevel',0.)
    ccx,ccy=w/2-r,h/2-r;lo,hi=p['z_min'],p['z_max']
    maxima={'outline_angle_span_radians':0.,'roundover_angle_span_radians':0.,'coupled_corner_distance_bound_mm':0.}
    roles=Counter();neighbors={i:set() for i in range(len(mesh['vertices_mm']))}
    for face,source in zip(mesh['faces'],mesh['face_provenance']):
        roles[source.get('patch_role',source['surface_role'])]+=1
        for a,z in zip(face,face[1:]+face[:1]):neighbors[a].add(z);neighbors[z].add(a)
        if source['surface_role']!='outline_bevel':continue
        normals=[];phis=[]
        for index in face:
            x,y,z=mesh['vertices_mm'][index]
            dx=max(0.,abs(x-cx)-ccx);dy=max(0.,abs(y-cy)-ccy);rho=math.hypot(dx,dy)
            normals.append((math.copysign(dx/rho,x-cx),math.copysign(dy/rho,y-cy)))
            zc=lo+b if z<(lo+hi)/2 else hi-b
            phis.append(math.atan2(abs(z-zc),rho-(r-b)))
        dt=max(math.atan2(abs(a[0]*z[1]-a[1]*z[0]),a[0]*z[0]+a[1]*z[1]) for a in normals for z in normals)
        dp=max(phis)-min(phis)
        bound=r*(1-math.cos(dt/2))+b*(1-math.cos(dp/2))
        maxima['outline_angle_span_radians']=max(maxima['outline_angle_span_radians'],dt)
        maxima['roundover_angle_span_radians']=max(maxima['roundover_angle_span_radians'],dp)
        maxima['coupled_corner_distance_bound_mm']=max(maxima['coupled_corner_distance_bound_mm'],bound)
    maxima['per_region_faces']=dict(sorted(roles.items()))
    maxima['vertex_valence_histogram']={str(v):sum(len(n)==v for n in neighbors.values()) for v in sorted({len(n) for n in neighbors.values()})}
    maxima['non_valence_four_vertices']=sum(len(n)!=4 for n in neighbors.values())
    maxima['scope']='actual source polygon angular spans and connectivity; additional compound-error diagnostic, not visual approval'
    return maxima
