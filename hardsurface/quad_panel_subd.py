"""Bounded, editable Catmull-Clark panel control cages.

The cage is not a tessellation of the final surface. Analytic dimensions belong
on the evaluated surface; deliberate curve compensation is reported. Only one
circular sharp-rim through-hole is currently supported. No fallback constructor.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import math
from .io import RuntimeFailure
from .quad_patches import _lerp, _planar_quad_metrics
from .subd_panel_identity import (AuthoredPoint, AuthoredMeshBuilder, ring_points, grid_id,
                                  band_id, planar_aliases, resolve_alias, outline_tokens, authored_manifest, PROFILE_KNOTS)
from .quad_common import finish_mesh


def _fail(message,**details):
    raise RuntimeFailure('SUBD_PANEL_DOMAIN',message,**details)


def _rectangle(bounds,n):
    x0,y0,x1,y1=bounds
    pts=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    return [_lerp(pts[s],pts[(s+1)%4],j/n) for s in range(4) for j in range(n)]


def _ring(center,r,n):
    return [(center[0]+r*math.cos(-3*math.pi/4+math.tau*j/n),
             center[1]+r*math.sin(-3*math.pi/4+math.tau*j/n)) for j in range(n)]


def _ccw(face):
    return tuple(reversed(face)) if sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(face,face[1:]+face[:1]))<0 else tuple(face)


def _annulus(a,b):
    assert len(a)==len(b)
    return [_ccw((a[i],a[(i+1)%len(a)],b[(i+1)%len(b)],b[i])) for i in range(len(a))]


def _transition_shaping(fine,coarse):
    shaped=list(fine)
    for i in range(len(coarse)):
        j=3*i+2; q=_lerp(coarse[i],coarse[(i+1)%len(coarse)],2/3)
        shaped[j]=(fine[j][0]+.18*(fine[j][0]-q[0]),fine[j][1]+.18*(fine[j][1]-q[1]))
        a,b=fine[3*i],shaped[j]
        mid=_lerp(a,b,.5); q=_lerp(coarse[i],coarse[(i+1)%len(coarse)],1/3)
        shaped[3*i+1]=(mid[0]+.30*(mid[0]-q[0]),mid[1]+.30*(mid[1]-q[1]))
    return shaped


def _reduce_three_to_one(fine,coarse):
    """Two quads per 3:1 block; needs regular buffer outside coarse ring.

    The geometric arrangement is checked independently. Count reduction alone
    does not promise good shape, and the pole region must be planar.
    """
    if len(fine)!=3*len(coarse):_fail('3:1 transition requires exactly corresponding ring counts')
    result=[]
    for i,q0 in enumerate(coarse):
        q1=coarse[(i+1)%len(coarse)];p=[fine[(3*i+j)%len(fine)] for j in range(4)]
        result.extend((_ccw((p[0],p[1],p[2],q0)),_ccw((p[2],p[3],q1,q0))))
    return result


def _outline_from_layout(layout,inset):
    core=layout['core'];xs=layout['xs'];ys=layout['ys'];rf=layout['radius']-inset
    if rf<=0:_fail('Inset consumes outline radius')
    centers=[(core[2],core[1]),(core[2],core[3]),(core[0],core[3]),(core[0],core[1])]
    straight=[[(x,core[1]-rf) for x in xs[:-1]],[(core[2]+rf,y) for y in ys[:-1]],
              [(x,core[3]+rf) for x in reversed(xs[1:])],[(core[0]-rf,y) for y in reversed(ys[1:])]]
    outer=[]
    for side,center in enumerate(centers):
        outer+=straight[side]
        outer += [(center[0]+rf*math.cos((side-1)*math.pi/2+i*math.pi/12),
                   center[1]+rf*math.sin((side-1)*math.pi/2+i*math.pi/12)) for i in range(6)]
    return outer


def _outline_control(layout,inset,outer_join_policy=None):
    raw=_outline_from_layout(layout,inset);core=layout['core'];r=layout['radius']-inset
    cx=(core[0]+core[2])/2;cy=(core[1]+core[3])/2;ax=(core[2]-core[0])/2;ay=(core[3]-core[1])/2
    factor=3/(2+math.cos(math.pi/12));endpoint=(5-factor*math.cos(math.pi/12))/4
    joined=outer_join_policy in ('joined_arc_v1','joined_arc_v2')
    if outer_join_policy is not None and not joined:_fail('Unsupported outer join policy')
    if joined:
        # Explicit line/arc join approximation; no global convexity claim.
        endpoint=1. if outer_join_policy=='joined_arc_v2' else (2+factor)/3
        m=(1+23*endpoint+23*factor*math.cos(math.pi/12)+factor*math.cos(math.pi/6))/48
        if not (r>0 and 0<m<1):_fail('Joined arc normal midpoint leaves the supported domain')
    out=[]
    for x,y in raw:
        qx=cx+max(-ax,min(ax,x-cx));qy=cy+max(-ay,min(ay,y-cy));dx=x-qx;dy=y-qy
        arc=abs(dx)>1e-8 and abs(dy)>1e-8
        tangent=(abs(dx)>1e-8 and abs(abs(y-cy)-ay)<1e-8) or (abs(dy)>1e-8 and abs(abs(x-cx)-ax)<1e-8)
        f=factor if arc else endpoint if tangent else 1.
        px,py=qx+dx*f,qy+dy*f
        if joined and tangent:
            xnormal=abs(dx)>1e-8
            positive=(y>cy) if xnormal else (x>cx)
            d=((core[3]-layout['ys'][-2] if positive else layout['ys'][1]-core[1]) if xnormal
               else (core[2]-layout['xs'][-2] if positive else layout['xs'][1]-core[0]))
            tau=(48*math.sqrt(1-m*m)+d/r-23*factor*math.sin(math.pi/12)-factor*math.sin(math.pi/6))/23
            if not (d>0 and math.isfinite(tau) and 0<=tau<factor*math.sin(math.pi/12)):
                _fail('Joined arc guard spacing leaves the ordered tangent-control domain',radius=r,guard_distance=d,tau=tau)
            if xnormal:py+=(1 if positive else -1)*r*tau
            else:px+=(1 if positive else -1)*r*tau
        out.append((px,py))
    if joined and any(math.dist(a,b)<=1e-8 for a,b in zip(out,out[1:]+out[:1])):
        _fail('Joined arc controls must retain nonzero ordered spans')
    return out


def _samples(a,b,n):return [a+(b-a)*i/n for i in range(n)]+[b]


def _grid(xs,ys):
    return [((x0,y0),(x1,y0),(x1,y1),(x0,y1)) for y0,y1 in zip(ys,ys[1:]) for x0,x1 in zip(xs,xs[1:])]


def plan_subd_cage(p):
    holes=p.get('holes',[]);frame=p.get('local_patch_bounds');cfg=p.get('subdivision_cage',{})
    if not isinstance(cfg,dict):_fail('subdivision_cage must be an object')
    if 'outer_join_policy' in cfg:
        if cfg['outer_join_policy'] not in ('joined_arc_v1','joined_arc_v2'):_fail('Unsupported outer join policy')
        if 'bore_support_width' not in cfg:_fail('Joined arc policy requires an explicit fixed bore support width')
    if len(holes)!=1 or holes[0]['kind']!='circle' or holes[0].get('counterbore') or p.get('lips'):
        _fail('SubD cage requires one circular sharp through-hole; counterbores/lips unsupported')
    if not isinstance(frame,(list,tuple)) or len(frame)!=4:_fail('An explicit fixed local_patch_bounds frame is required')
    w,h=p['size'];r=p['corner_radius'];b=p.get('edge_bevel',0);cx,cy=p.get('center',[0,0]);lo,hi=p['z_min'],p['z_max'];hole=holes[0];hx,hy=hole['center'];hr=hole['radius']
    vals=[w,h,r,b,cx,cy,lo,hi,hx,hy,hr,*frame]
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vals):_fail('All geometry inputs must be finite numbers')
    if not (w>0 and h>0 and 0<b<min(r/4,(hi-lo)/3) and r<min(w,h)/2 and hr>0):_fail('Requires a positive rounded outline and roundover with room for support bands')
    n=cfg.get('hole_segments',24)
    if type(n)is not int or n!=24:_fail('This candidate supports exactly 24 hole controls')
    if cfg.get('method','CATMULL_CLARK')!='CATMULL_CLARK':_fail('Only Blender CATMULL_CLARK is implemented')
    levels=cfg.get('preview_levels',2)
    if type(levels)is not int or not 0<=levels<=3:_fail('Preview level must be integer 0..3')
    tol=p.get('chord_tolerance',.025)
    x0,y0,x1,y1=frame;core=(cx-w/2+r,cy-h/2+r,cx+w/2-r,cy+h/2-r)
    if not(core[0]<x0<x1<core[2] and core[1]<y0<y1<core[3]):_fail('Frame must be strictly inside the outline corner-center rectangle')
    if not(.75<=(x1-x0)/(y1-y0)<=1.5):_fail('Frame aspect must be within 0.75..1.5')
    if min(hx-hr-x0,x1-hx-hr,hy-hr-y0,y1-hy-hr)<max(.8,hr*.15):_fail('Hole needs clearance for a local supported transition')
    factor=3/(2+math.cos(math.tau/n));control_hr=hr*factor
    support_width=max(b*2,hr*.12)
    if 'bore_support_width' in cfg:
        support_width=cfg['bore_support_width']
        if (type(support_width) not in (int,float) or not math.isfinite(support_width)
                or not 0<support_width<=hr):
            _fail('Explicit bore support width must be finite, positive and no greater than the hole radius')
        if control_hr+support_width>=min(hx-x0,x1-hx,hy-y0,y1-hy):
            _fail('Explicit compensated bore support must remain strictly inside the fixed frame')
    rim=ring_points(_ring((hx,hy),control_hr,n),'bore/rim')
    support=ring_points(_ring((hx,hy),control_hr+support_width,n),'bore/support')
    rawframe=ring_points(_rectangle(frame,n//12),'fixed_frame')
    # Coarse circular/square hybrid creates room for the density transition.
    fine_frame=_rectangle(frame,n//4)
    transition=[_lerp(q,z,.35) for q,z in zip(support,fine_frame)]
    coarse=ring_points([_lerp(q,z,.9) for q,z in zip(_ring((hx,hy),control_hr,n//3),rawframe)],'bore/coarse')
    transition=ring_points(_transition_shaping(transition,coarse),'bore/transition')
    hole_roles=[('bore_planar_support',_annulus(rim,support)),('bore_planar_buffer',_annulus(support,transition)),('planar_3_to_1',_reduce_three_to_one(transition,coarse)),('fixed_frame_buffer',_annulus(coarse,rawframe))]
    # 24->8 uses 2 intervals in the fixed frame, with one exterior interval.
    # 48->16 uses four central intervals, with two exterior intervals.
    k=n//24;counts=[k,2*k,k]
    def axis_blocks(a,u,v,z):
        split=2 if 'bore_support_width' in cfg else 4
        da=min(r*math.pi/12,(u-a)/split);dz=min(r*math.pi/12,(z-v)/split)
        return [[a,a+da,u],_samples(u,v,2),[v,z-dz,z]]
    xb=axis_blocks(core[0],x0,x1,core[2]);yb=axis_blocks(core[1],y0,y1,core[3])
    def authored_grid(ax,ay,token):
        return [tuple(AuthoredPoint(point,token(x,y)) for point,x,y in
                     (((ax[u],ay[v]),u,v),((ax[u+1],ay[v]),u+1,v),
                      ((ax[u+1],ay[v+1]),u+1,v+1),((ax[u],ay[v+1]),u,v+1)))
                for v in range(len(ay)-1) for u in range(len(ax)-1)]
    macro=[f for j in range(3) for i in range(3) if(i,j)!=(1,1)
           for f in authored_grid(xb[i],yb[j],lambda u,v,i=i,j=j:grid_id(2*i+u,2*j+v))]
    if n!=24:_fail('48-control bore needs a separately qualified outline reduction family; select 24 for this candidate')
    xs=[v for row in xb for v in row[:-1]]+[core[2]]
    ys=[v for row in yb for v in row[:-1]]+[core[3]]
    transition_inset=b+max(b*2,r*.10)
    rf=r-transition_inset;ri=.55*rf
    bands=(authored_grid(xs,[core[1]-rf,core[1]-ri,core[1]],lambda u,v:band_id(0,u,2-v))+
           authored_grid([core[2],core[2]+ri,core[2]+rf],ys,lambda u,v:band_id(1,v,u))+
           authored_grid(xs,[core[3],core[3]+ri,core[3]+rf],lambda u,v:band_id(2,6-u,v))+
           authored_grid([core[0]-rf,core[0]-ri,core[0]],ys,lambda u,v:band_id(3,6-v,2-u)))
    # Same quarter-disk formula as the legacy helper; tokens are assigned while
    # each logical point is authored and travel through winding and transforms.
    fine=[(rf*math.cos(i*math.pi/2/6),rf*math.sin(i*math.pi/2/6)) for i in range(7)]
    coarse_corner=[(.55*rf*math.cos(i*math.pi/2/2),.55*rf*math.sin(i*math.pi/2/2)) for i in range(3)]
    corner_faces=[]
    centers=[(core[2],core[1]),(core[2],core[3]),(core[0],core[3]),(core[0],core[1])]
    for side,center in enumerate(centers):
        ff=[AuthoredPoint(q,'corner/side:%d/fine:%d'%(side,j)) for j,q in enumerate(fine)]
        cc=[AuthoredPoint(q,'corner/side:%d/coarse:%d'%(side,j)) for j,q in enumerate(coarse_corner)]
        reduction=[]
        for i in range(2):
            p0,p1,p2,p3=ff[3*i:3*i+4];q0,q1=cc[i:i+2]
            reduction.extend((_ccw((p0,p1,p2,q0)),_ccw((p2,p3,q1,q0))))
        cap=[_ccw((AuthoredPoint((0.,0.),'corner/side:%d/center'%side),cc[0],cc[1],cc[2]))]
        angle=(side-1)*math.pi/2;co,si=math.cos(angle),math.sin(angle)
        def tr(q):return AuthoredPoint((center[0]+co*q[0]-si*q[1],center[1]+si*q[0]+co*q[1]),q.semantic_id)
        corner_faces.extend(tuple(tr(v) for v in face) for face in reduction+cap)
    outline_layout={'core':core,'xs':xs,'ys':ys,'radius':r}
    if cfg.get('outer_join_policy') in ('joined_arc_v1','joined_arc_v2'):
        # tau is affine in d/rho: checking both radius extremes bounds all twelve rings.
        for inset in (0.,b+b*math.sin(math.pi/6)):
            _outline_control(outline_layout,inset,cfg['outer_join_policy'])
    aliases=planar_aliases()
    flat_transition=[AuthoredPoint(q,token) for q,token in
                     zip(_outline_control(outline_layout,transition_inset),outline_tokens())]
    # Compensation is assigned to explicit outline slots, never recovered from
    # rounded coordinates. Alias resolution is wholly topological.
    replacement={resolve_alias(q.semantic_id,aliases):q for q in flat_transition}
    def remap(faces):
        return [tuple(AuthoredPoint(replacement.get(resolve_alias(q.semantic_id,aliases),q),q.semantic_id)
                      for q in face) for face in faces]
    outer_roles=[('axial_macroblocks',macro),('outline_straight_bands',remap(bands)),('outline_planar_corner_reduction',remap(corner_faces))]
    allroles=hole_roles+outer_roles
    face_tokens={
        'bore_planar_support':['bore_support/sector:%d'%i for i in range(24)],
        'bore_planar_buffer':['bore_buffer/sector:%d'%i for i in range(24)],
        'planar_3_to_1':['reduce3/block:%d/quad:%s'%(i,q) for i in range(8) for q in ('a','b')],
        'fixed_frame_buffer':['fixed_frame_buffer/sector:%d'%i for i in range(8)],
        'axial_macroblocks':['macro/block:%d,%d/cell:%d,%d'%(i,j,u,v) for j in range(3) for i in range(3)
                            if (i,j)!=(1,1) for v in range(2) for u in range(2)],
        'outline_straight_bands':(
            ['straight_band/side:0/cell:%d,%d'%(u,1-v) for v in range(2) for u in range(6)]+
            ['straight_band/side:1/cell:%d,%d'%(v,u) for v in range(6) for u in range(2)]+
            ['straight_band/side:2/cell:%d,%d'%(5-u,v) for v in range(2) for u in range(6)]+
            ['straight_band/side:3/cell:%d,%d'%(5-v,1-u) for v in range(6) for u in range(2)]),
        'outline_planar_corner_reduction':[token for side in range(4) for token in
            (['corner/side:%d/reduce/block:%d/quad:%s'%(side,i,q) for i in range(2) for q in ('a','b')]+
             ['corner/side:%d/cap'%side])],
    }
    quality={name:_planar_quad_metrics(faces) for name,faces in allroles}
    for name,q in quality.items():
        if not(q['minimum_angle_degrees']>=5 and q['maximum_angle_degrees']<=175 and q['maximum_aspect_ratio']<=50):_fail('A planar transition fails bounded shape checks',region=name,quality=q)
    plan={'outline_layout':outline_layout,'roles':allroles,'face_tokens':face_tokens,'aliases':aliases,'support':support,'rim':rim,'flat_transition':flat_transition,'transition_inset':transition_inset,'metadata':{
        'strategy':'subd_control_cage','candidate_version':1,'backend':'Blender Subdivision Surface','method':'CATMULL_CLARK',
        'nominal_design':{'center_mm':[cx,cy],'size_mm':p['size'],'corner_radius_mm':r,'edge_roundover_radius_mm':b,'hole_center_mm':[hx,hy],'hole_radius_mm':hr,'z_range_mm':[lo,hi]},
        'control_policy':{'hole_segments':n,'outline_segments':len(flat_transition),'roundover_quarter_segments':3,'bore_rim_crease':1.0,'crease_scope':'two circular sharp opening rims only','preview_levels':levels,'render_levels':levels,**({'bore_support_width_mm':support_width,'bore_support_width_mode':'fixed_explicit','technical_layout_revision':'fixed_support_half_gap_guards_v1'} if 'bore_support_width' in cfg else {})},
        'compensation':{'bore_radius_factor':factor,'roundover_interior_control_radius_factor':3/(2+math.cos(math.pi/6)),'bore_control_radius_mm':control_hr,'outline':'local periodic-circle compensation with explicit tangent controls and straight guards; actual evaluated between-knot measurement remains mandatory'},
        'quality':quality,'fixed_patch_bounds_mm':list(frame),'dimension_contract':'source cage may overshoot nominal; evaluated levels 2 and 3 must pass specified shape tolerance, including between-knot sampling',
        'error_tolerance_mm':tol,'limits':'one circle, no lips/counterbores; 24-to-8 local bore transition and six-control quarter-outline sectors with local straight guards; no TurboSmooth compatibility or visual approval claim'}}
    if cfg.get('outer_join_policy')=='joined_arc_v1':
        plan['metadata']['control_policy']['outer_join_policy']='joined_arc_v1'
        compensation=plan['metadata']['compensation']
        compensation.pop('roundover_interior_control_radius_factor')
        compensation.update({
            'roundover_interior_pair':{'a_over_b':(48/math.sqrt(2)-1)/23-19/20,'c_over_b':19/20,
                                      'equation':'c=19b/20; a=b*((48/sqrt(2)-1)/23-19/20)'},
            'outline':'joined_arc_v1: E=(2+F)/3; first arc-span midpoint lies on each inset circle using its actual adjacent straight guard',
            'joined_arc_domain':'rho>0; d>0; 0<M<1; finite 0<=tau<F*sin(pi/12); nonzero ordered spans; unchanged planar transition rings',
            'curvature_limitations':'bounded approximation with variable profile curvature; shallow outline reverse curvature remains; no global convexity or native/visual qualification claim'})
    elif cfg.get('outer_join_policy')=='joined_arc_v2':
        plan['metadata']['control_policy']['outer_join_policy']='joined_arc_v2'
        compensation=plan['metadata']['compensation']
        compensation.pop('roundover_interior_control_radius_factor')
        compensation.update({
            'roundover_interior_pair':{'a_over_b':(48/math.sqrt(2)-1)/23-1,'c_over_b':1.,
                                      'equation':'c=b; a=b*((48/sqrt(2)-1)/23-1)'},
            'outline':'joined_arc_v2: E=1; F=3/(2+cos(pi/12)); first arc-span midpoint lies on each inset circle using its actual adjacent straight guard',
            'joined_arc_domain':'rho>0; d>0; 0<M<1; finite 0<=tau<F*sin(pi/12); nonzero ordered spans; unchanged planar transition rings',
            'curvature_limitations':'fixed analytic HOST hypothesis with variable profile curvature; finite samples are not an all-points certificate; no native or visual qualification claim'})
    return plan


def build_subd_panel(p,*,feature_id):
    plan=plan_subd_cage(p);aliases={f'plane/{side}/{a}':f'plane/{side}/{z}' for side in ('bottom','top') for a,z in plan['aliases'].items()};builder=AuthoredMeshBuilder(aliases=aliases,max_vertices=20000,max_faces=20000);lo,hi=p['z_min'],p['z_max'];b=p['edge_bevel'];r=p['corner_radius'];cx,cy=p.get('center',[0,0])
    def role(name,planar=True):
        out={'feature_id':feature_id,'surface_id':name,'surface_role':name,'patch_role':name,'smooth':not planar,'curved':not planar,'support_band':not planar}
        if not planar and name!='bore_wall' and p.get('subdivision_cage',{}).get('outer_join_policy') in ('joined_arc_v1','joined_arc_v2'):
            out['outer_join_policy']=p['subdivision_cage']['outer_join_policy']
        return out
    sides=[];control_loops=[];semantic_control_loops=[]
    def plane_id(side,q):return 'plane/'+side+'/'+q.semantic_id
    def semantic_loop(role,indices,crease=0.):
        inverse={i:s for s,i in builder.vertex_map.items()}
        semantic_control_loops.append({'role':role,'vertex_ids':[inverse[i] for i in indices],'crease':crease,'expected_valence':4})
    for side,z in [('bottom',lo),('top',hi)]:
        for name,faces in plan['roles']:
            if len(faces)!=len(plan['face_tokens'][name]):_fail('Authored face schedule coverage is incomplete',region=name)
            for f,token in zip(faces,plan['face_tokens'][name]):
                builder.quad(*(builder.vertex((*q,z),semantic_id=plane_id(side,q)) for q in f),
                             provenance=role(side+':'+name),semantic_id='plane/'+side+'/'+token)
        rim=builder.loop([(*q,z) for q in plan['rim']],semantic_ids=[plane_id(side,q) for q in plan['rim']])
        outer=builder.loop([(*q,z) for q in plan['flat_transition']],semantic_ids=[plane_id(side,q) for q in plan['flat_transition']])
        sides.append({'rim':rim,'outer':outer})
        control_loops.append({'role':'bore_rim_'+side,'vertex_indices':rim,'crease':1.0,'expected_valence':4})
        semantic_loop('bore_rim_'+side,rim,1.)
        # Keep legacy raw loop order byte-for-byte while semantic order comes
        # directly from the authored angle slots, not an atan2 reconstruction.
        support=plan['support']
        ordered=sorted(support,key=lambda q:math.atan2(q[1]-p['holes'][0]['center'][1],q[0]-p['holes'][0]['center'][0]))
        old_support=builder.loop([(*q,z) for q in ordered],semantic_ids=[plane_id(side,q) for q in ordered])
        control_loops.append({'role':'bore_support_'+side,'vertex_indices':old_support,'crease':0.0,'expected_valence':4})
        authored_support=builder.loop([(*q,z) for q in support],semantic_ids=[plane_id(side,q) for q in support])
        semantic_loop('bore_support_'+side,authored_support)
    builder.loft(sides[0]['rim'],sides[1]['rim'],provenance=role('bore_wall',False),face_ids=['bore_wall/sector:%d'%i for i in range(24)])
    profile=[]
    profile_factor=3/(2+math.cos(math.pi/6))
    near=b*math.sin(math.pi/6)
    # Flat guard loops separate transition poles from the curved cross-section.
    profile.append((b+near,lo,'bottom_flat_guard'))
    profile.extend((b-(b*profile_factor if j in (1,2) else b)*math.sin(j*math.pi/6),lo+b-(b*profile_factor if j in (1,2) else b)*math.cos(j*math.pi/6),'lower_roundover') for j in range(4))
    profile.extend(((0.,lo+b+near,'lower_wall_guard'),(0.,hi-b-near,'upper_wall_guard')))
    profile.extend((b-(b*profile_factor if j in (1,2) else b)*math.sin(j*math.pi/6),hi-b+(b*profile_factor if j in (1,2) else b)*math.cos(j*math.pi/6),'upper_roundover') for j in range(3,-1,-1))
    profile.append((b+near,hi,'top_flat_guard'))
    outer_join_policy=p.get('subdivision_cage',{}).get('outer_join_policy')
    if outer_join_policy=='joined_arc_v1':
        c=19*b/20;a=b*((48/math.sqrt(2)-1)/23-19/20)
        profile[2]=(b-a,lo+b-c,'lower_roundover')
        profile[3]=(b-c,lo+b-a,'lower_roundover')
        profile[8]=(b-c,hi-b+a,'upper_roundover')
        profile[9]=(b-a,hi-b+c,'upper_roundover')
    elif outer_join_policy=='joined_arc_v2':
        c=b;a=b*((48/math.sqrt(2)-1)/23-1)
        profile[2]=(b-a,lo+b-c,'lower_roundover')
        profile[3]=(b-c,lo+b-a,'lower_roundover')
        profile[8]=(b-c,hi-b+a,'upper_roundover')
        profile[9]=(b-a,hi-b+c,'upper_roundover')
    profile_names=PROFILE_KNOTS
    previous=sides[0]['outer'];previous_name='bottom_plane'
    for (inset,z,name),knot in zip(profile,profile_names):
        points=_outline_control(plan['outline_layout'],inset,outer_join_policy)
        current=builder.loop([(*q,z) for q in points],semantic_ids=['profile/%s/slot:%d'%(knot,i) for i in range(48)])
        builder.loft(previous,current,provenance=role(name,False),face_ids=['outline/%s/%s/sector:%d'%(previous_name,knot,i) for i in range(48)]);previous=current;previous_name=knot
        semantic_loop(knot,current)
        control_loops.append({'role':name,'vertex_indices':current,'crease':0.0,'expected_valence':4})
    builder.loft(previous,sides[1]['outer'],provenance=role('top_planar_guard',False),face_ids=['outline/%s/top_plane/sector:%d'%(previous_name,i) for i in range(48)])
    mesh=builder.mesh();mesh['metadata']={'operation':'quad.panel','subdivision_cage':plan['metadata'],'sampling':'sparse planar macroblocks, supported local O-loops and explicit 3:1 density transitions; compensated Catmull-Clark control rings'}
    result=finish_mesh(mesh,feature_id=feature_id)
    creases=[]
    for side in sides:
        loop=side['rim'];creases.extend([a,z,1.] for a,z in zip(loop,loop[1:]+loop[:1]))
    result['edge_creases']=creases
    result['control_loops']=control_loops
    result['subdivision_modifier']={'name':'HS_Subdivision_Cage','subdivision_type':'CATMULL_CLARK','levels':p.get('subdivision_cage',{}).get('preview_levels',2),'render_levels':p.get('subdivision_cage',{}).get('preview_levels',2),'quality':6,'uv_smooth':'PRESERVE_BOUNDARIES','boundary_smooth':'ALL','use_creases':True,'use_limit_surface':True}
    adjacency=defaultdict(set)
    for f in result['faces']:
        for a,z in zip(f,f[1:]+f[:1]):adjacency[a].add(z);adjacency[z].add(a)
    valences=Counter(len(v) for v in adjacency.values())
    result['metadata']['subdivision_cage']['source_valence_histogram']={str(k):v for k,v in sorted(valences.items())}
    if any(k not in(3,4,5) for k in valences):_fail('Authored cage contains an unplanned valence outside 3/4/5',valences=dict(valences))
    result['sharp_edges']=[x[:2] for x in creases]
    result['authored_structure']=authored_manifest(result,builder,semantic_control_loops,p)
    result['authorship_sha256']=result['authored_structure']['authorship_sha256']
    return result
