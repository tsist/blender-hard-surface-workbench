"""Fixed local hole O-grid, axial macroblocks and independent corner sectors.

Experimental bounded explicit-mesh domain. All subdivision schedules are planned
before allocation; unsupported layouts fail rather than falling back to another
strategy. The hole frame is an explicit caller input and is not moved or resized
by hole edits. No whole-panel hole-to-outline interpolation is constructed.
"""
from __future__ import annotations
import math
from .io import RuntimeFailure
from .quad_patches import _lerp, _planar_quad_metrics, circle_segments, quarter_arc_segments


def _fail(message, **details):
    raise RuntimeFailure('QUAD_LOCAL_BLOCKS_DOMAIN',message,**details)


def _ccw(faces):
    return [tuple(reversed(f)) if sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(f,f[1:]+f[:1]))<0 else tuple(f) for f in faces]


def _rect_loop(bounds, n):
    x0,y0,x1,y1=bounds;v=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    return [_lerp(v[s],v[(s+1)%4],j/n) for s in range(4) for j in range(n)]


def transition_four_to_two(fine,coarse):
    """Six convex quads for a 4-fine/2-coarse strip, with three interior points.

    This primitive only returns the candidate coordinates. The caller must test
    actual convexity and quality. In a rectangle loop, groups are side-aligned;
    no group crosses an unproved rectangle corner.
    """
    if len(fine)!=5 or len(coarse)!=3:_fail('4:2 transition requires 5 fine and 3 coarse boundary points')
    p0,p1,p2,p3,p4=fine;q0,q1,q2=coarse
    u=_lerp(p1,_lerp(q0,q1,.5),.5)
    v=_lerp(p2,q1,.7)
    w=_lerp(p3,_lerp(q1,q2,.5),.5)
    return [(p0,p1,u,q0),(p1,p2,v,u),(p2,p3,w,v),(p3,p4,q2,w),(u,v,q1,q0),(v,w,q2,q1)]


def _hole_patch(center,r,bounds,n):
    hole=[(center[0]+r*math.cos(-3*math.pi/4+math.tau*i/n),
           center[1]+r*math.sin(-3*math.pi/4+math.tau*i/n)) for i in range(n)]
    coarse=_rect_loop(bounds,n//8)
    fine=[_lerp(a,b,.5) for a,b in zip(hole,_rect_loop(bounds,n//4))]
    ogr=[(hole[i],hole[(i+1)%n],fine[(i+1)%n],fine[i]) for i in range(n)]
    transitions=[]
    for i in range(0,n,4):
        transitions+=transition_four_to_two([fine[(i+j)%n] for j in range(5)],
                                           [coarse[(i//2+j)%(n//2)] for j in range(3)])
    return hole,coarse,_ccw(ogr),_ccw(transitions)


def _corner(r,na):
    """Quarter disk: arc 6/12 -> 2/4 locally, then 1/2 interior quads.

    Only center, 0.55*r and r reach the two radial seams, independent of na.
    Thus curved sampling terminates here instead of entering axial blocks.
    """
    fine=[(r*math.cos(i*math.pi/2/na),r*math.sin(i*math.pi/2/na)) for i in range(na+1)]
    n=na//3;coarse=[(.55*r*math.cos(i*math.pi/2/n),.55*r*math.sin(i*math.pi/2/n)) for i in range(n+1)]
    reduction=[]
    for i in range(n):
        p0,p1,p2,p3=fine[3*i:3*i+4];q0,q1=coarse[i:i+2]
        reduction.extend(((p0,p1,p2,q0),(p2,p3,q1,q0)))
    cap=[((0.,0.),coarse[i],coarse[i+1],coarse[i+2]) for i in range(0,n,2)]
    return _ccw(reduction),_ccw(cap)


def _samples(a,b,n):return [a+(b-a)*i/n for i in range(n)]+[b]


def _grid(xs,ys):
    return [((x0,y0),(x1,y0),(x1,y1),(x0,y1))
            for y0,y1 in zip(ys,ys[1:]) for x0,x1 in zip(xs,xs[1:])]


def _quality(faces):return _planar_quad_metrics(faces)


def plan_local_patch_blocks(p):
    holes=p.get('holes',[])
    if len(holes)!=1 or holes[0]['kind']!='circle' or holes[0].get('counterbore') or p.get('lips'):
        _fail('Local patch blocks requires one circular through-hole, without counterbore or lips')
    frame=p.get('local_patch_bounds')
    if not isinstance(frame,(list,tuple)) or len(frame)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in frame):
        _fail('Explicit finite local_patch_bounds [xmin,ymin,xmax,ymax] is required')
    x0,y0,x1,y1=frame
    w,h=p['size'];r=p['corner_radius'];b=p.get('edge_bevel',0.);cx,cy=p.get('center',[0,0])
    hx,hy=holes[0]['center'];hr=holes[0]['radius'];target=p.get('target_edge_length',4.);tol=p.get('chord_tolerance',.025);maximum=p.get('max_segments',512)
    values=[w,h,r,b,cx,cy,hx,hy,hr,target,tol,p['z_min'],p['z_max']]
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):_fail('Geometry and sampling values must be finite numbers')
    if not (w>0 and h>0 and 0<r<min(w,h)/2 and 0<=b<min(r,(p['z_max']-p['z_min'])/2) and hr>0 and target>0 and tol>0):
        _fail('Panel geometry or sampling controls are outside the positive geometric domain')
    if type(maximum) is not int or not 12<=maximum<=512:_fail('max_segments must be an integer in 12..512')
    ax,ay=w/2-r,h/2-r;core=(cx-ax,cy-ay,cx+ax,cy+ay)
    if not (core[0]+1e-6<x0<x1<core[2]-1e-6 and core[1]+1e-6<y0<y1<core[3]-1e-6):
        _fail('Fixed hole frame must lie strictly inside the rectangle joining the four corner centers',frame=frame,core=core)
    if not (.75<=(x1-x0)/(y1-y0)<=1.5):_fail('Local frame aspect must be within the qualified 0.75..1.5 range')
    if min(hx-hr-x0,x1-hx-hr,hy-hr-y0,y1-hy-hr)<=1e-6:_fail('Hole must be strictly inside the fixed frame')
    minimum=circle_segments(hr,tol,maximum)
    n=max(32,16*math.ceil(minimum/16))
    if n>64 or n>maximum:_fail('Hole sampling needs more than the bounded 32/48/64 loop family',chosen=n,maximum=maximum)
    # Extra conservative sum budget is retained only as diagnostic. The user's
    # literal independent arc contract remains tol for each approved radius.
    outline_tol=tol/2 if b else tol
    required_arc=quarter_arc_segments(r,outline_tol,maximum)
    na=6*math.ceil(required_arc/6)
    if na not in (6,12):_fail('Corner sector supports 6 or 12 arc edges; finer curves need a different qualified pattern',required=required_arc)
    sag=r*(1-math.cos(math.pi/(4*na)));roundover_tol=tol-sag if b else 0.
    if b:quarter_arc_segments(b,roundover_tol,maximum)
    nx=[max(1,math.ceil((x0-core[0])/target)),n//8,max(1,math.ceil((core[2]-x1)/target))]
    ny=[max(1,math.ceil((y0-core[1])/target)),n//8,max(1,math.ceil((core[3]-y1)/target))]
    outer_count=2*(sum(nx)+sum(ny))+4*na
    estimated_faces=sum(nx)*sum(ny)-nx[1]*ny[1]+4*(sum(nx)+sum(ny))+10*na//3+n*5//2
    if max(nx+ny)>64 or outer_count>min(maximum,384) or estimated_faces>10000:
        raise RuntimeFailure('QUAD_SAMPLING_BUDGET','Local block schedule exceeds bounded allocation',outline=outer_count,estimated_planar_faces=estimated_faces)
    xv=[core[0],x0,x1,core[2]];yv=[core[1],y0,y1,core[3]]
    xblocks=[_samples(a,z,k) for a,z,k in zip(xv,xv[1:],nx)]
    yblocks=[_samples(a,z,k) for a,z,k in zip(yv,yv[1:],ny)]
    xs=[v for q in xblocks for v in q[:-1]]+[core[2]]
    ys=[v for q in yblocks for v in q[:-1]]+[core[3]]
    hole,frame_loop,ogr,transitions=_hole_patch((hx,hy),hr,frame,n)
    macro=[]
    for j in range(3):
        for i in range(3):
            if (i,j)!=(1,1):macro+=_grid(xblocks[i],yblocks[j])
    rf=r-b;ri=.55*rf
    bands=(_grid(xs,[core[1]-rf,core[1]-ri,core[1]])+
           _grid([core[2],core[2]+ri,core[2]+rf],ys)+
           _grid(xs,[core[3],core[3]+ri,core[3]+rf])+
           _grid([core[0]-rf,core[0]-ri,core[0]],ys))
    reduction,cap=_corner(rf,na);corner_reduction=[];corner_cap=[];outer=[]
    centers=[(core[2],core[1]),(core[2],core[3]),(core[0],core[3]),(core[0],core[1])]
    straight=[[(x,core[1]-rf) for x in xs[:-1]],[(core[2]+rf,y) for y in ys[:-1]],
              [(x,core[3]+rf) for x in reversed(xs[1:])],[(core[0]-rf,y) for y in reversed(ys[1:])]]
    for side,center in enumerate(centers):
        angle=(side-1)*math.pi/2;co,si=math.cos(angle),math.sin(angle)
        def tr(point):
            x,y=point;return(center[0]+co*x-si*y,center[1]+si*x+co*y)
        corner_reduction += [tuple(tr(v) for v in f) for f in reduction]
        corner_cap += [tuple(tr(v) for v in f) for f in cap]
        outer+=straight[side]
        outer += [tr((rf*math.cos(i*math.pi/2/na),rf*math.sin(i*math.pi/2/na))) for i in range(na)]
    roles=[('hole_local_ogrid',ogr),('hole_local_4_to_2',transitions),('axial_macroblocks',macro),
           ('outline_straight_bands',bands),('corner_local_3_to_1',corner_reduction),('corner_coarse_quads',corner_cap)]
    quality={name:_quality(ff) for name,ff in roles}
    all_faces=[f for _,ff in roles for f in ff];overall=_quality(all_faces)
    if not(overall['minimum_angle_degrees']>=8 and overall['maximum_angle_degrees']<=174.5 and overall['maximum_aspect_ratio']<=40):
        _fail('Local block geometry fails unchanged planning angle/aspect bounds; no fallback was substituted',quality=overall,regions=quality)
    return {'outer':outer,'hole':hole,'frame':frame_loop,'roles':roles,'metadata':{
        'strategy':'local_patch_blocks','version':1,'fixed_patch_bounds_mm':list(frame),'core_bounds_mm':list(core),
        'block_graph':'fixed hole O-grid + 8 rectangular macroblocks + 4 two-row straight bands + 4 independent quarter-disk corner sectors',
        'hole_sampling':{'minimum_chord_segments':minimum,'chosen_segments':n,'frame_segments':n//2,'segments_per_frame_side':n//8,'extra_samples_reason':'multiple of 16 for side-aligned 4:2 groups, minimum 32 for local O-grid family; independent of outer arc/straight count'},
        'outline_sampling':{'horizontal_straight_segments':sum(nx),'vertical_straight_segments':sum(ny),'quarter_arc_segments':na,'minimum_quarter_arc_segments':required_arc,'total_boundary_segments':outer_count,'corner_inner_arc_segments':na//3,'corner_radial_seam_segments':2},
        'macroblock_schedule':{'x':nx,'y':ny,'field_excludes_central_block':True,'hole_density_never_enters_corner_schedule':True},
        'layout_quality':overall,'region_quality':quality,'per_face_region_counts':{role:len(ff) for role,ff in roles},
        'local_planning_targets':{'minimum_angle_degrees':8,'maximum_angle_degrees':174.5,'maximum_aspect_ratio':40},
        'transition_counts':{'hole_4_to_2_blocks':n//4,'quads_per_hole_block':6,'corner_3_to_1_blocks':4*na//3},
        'error_budget_mm':{'requested_chord_tolerance':tol,'scope':'literal per-arc design tolerance retained; sum budget is extra conservative compound-surface diagnostic only','outline_direction':outline_tol,'roundover_direction':roundover_tol,'hole_direction':tol,'outline_actual_sag':sag,'coupled_corner_bound':sag+roundover_tol},
        'edit_invariants':{'hole_position':'for valid moves within the same explicit frame, all changed XY coordinates are strictly inside the frame; all frame/exterior coordinates and polygon indices remain unchanged','thickness':'XY and planar block schedule are thickness independent; longitudinal wall counts can change across target-length thresholds'},
        'parameter_roles':{'local_patch_bounds':'caller-owned fixed planar routing region; keep identical for local hole motion','target_edge_length':'coarse outer straight/macroblock and longitudinal wall targets only; does not determine bore or corner curvature samples','chord_tolerance':'literal independent curve bound, plus additional compound diagnostic budget','max_segments':'hard preallocation boundary count ceiling'},
        'limitations':'bounded one circular hole with fixed rectangular frame and 6/12-segment corner sectors; explicit mesh only; no SubD, optimality or visual approval claim'}}


def emit_local_blocks_face(builder,plan,z,provenance):
    first=len(builder.faces)
    for role,faces in plan['roles']:
        source={**provenance,'patch_role':role}
        for face in faces:builder.quad(*(builder.vertex((*point,z)) for point in face),provenance=source)
    return {'outer':builder.loop([(*q,z) for q in plan['outer']]),
            'hole':builder.loop([(*q,z) for q in plan['hole']]),'face_range':(first,len(builder.faces))}
