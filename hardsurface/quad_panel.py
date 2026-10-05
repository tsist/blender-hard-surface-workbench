"""Structured rounded panels with local holes, counterbores and integral lips."""
from __future__ import annotations
import math
from .quad_patches import MeshBuilder, tiled_face, align_artificial_patch_events, quarter_arc_segments
from .quad_common import finish_mesh
from .io import RuntimeFailure


def _role(feature_id, surface, *, smooth=False, support=False):
    return {'feature_id':feature_id,'surface_id':surface,'surface_role':surface,
            'smooth':smooth,'curved':smooth,'support_band':support}


def _rect_cap(builder, loop, z, provenance):
    points=[builder.vertices[i] for i in loop]
    xs=sorted(set(p[0] for p in points));ys=sorted(set(p[1] for p in points))
    grid=[[builder.vertex((x,y,z)) for x in xs] for y in ys]
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):builder.quad(grid[j][i],grid[j][i+1],grid[j+1][i+1],grid[j+1][i],provenance)
    return grid


def _translate_loop(builder, loop, z):
    return builder.loop([(builder.vertices[i][0],builder.vertices[i][1],z) for i in loop])


def _bridge(builder, a, b, role, max_length=4):
    if len(a)!=len(b):raise RuntimeFailure('QUAD_SEAM_MISMATCH','Loft boundary sampling differs',first=len(a),second=len(b))
    count=max(1,math.ceil(max(math.dist(builder.vertices[i],builder.vertices[j]) for i,j in zip(a,b))/max_length))
    if count>4096:raise RuntimeFailure('QUAD_SAMPLING_BUDGET','Loft requires excessive longitudinal sampling')
    previous=a
    pa=[builder.vertices[i] for i in a];pb=[builder.vertices[i] for i in b]
    for row in range(1,count+1):
        current=b if row==count else builder.loop([tuple(x+(y-x)*row/count for x,y in zip(p,q)) for p,q in zip(pa,pb)])
        builder.loft(previous,current,provenance=role);previous=current


def _features(p, side):
    features=[];target=p.get('target_edge_length',4.)
    for hole in p.get('holes',[]):
        cx,cy=hole['center'];hid=hole['id']
        if hole['kind']=='circle':
            cb=hole.get('counterbore');maximum=cb['radius'] if cb else hole['radius']
            radius=cb['radius'] if cb and cb['side']==side else hole['radius']
            half=maximum+max(target*.625,maximum*.75)
            features.append({'id':hid,'kind':'circle','center':(cx,cy),'radius':radius,
                'sampling_radius':maximum,'patch':(cx-half,cy-half,cx+half,cy+half)})
        else:
            w,h=hole['size'];r=hole['radius'];margin=max(target*.25,r*.5)
            features.append({'id':hid,'kind':'rounded_rectangle','bounds':(cx-w/2,cy-h/2,cx+w/2,cy+h/2),
                'radius':r,'patch':(cx-w/2-margin,cy-h/2-margin,cx+w/2+margin,cy+h/2+margin)})
    return features


def _bevel_loop(builder, loop, size, center, radius, width, theta, z, side):
    cx,cy=center;w,h=size;ccx=w/2-radius;ccy=h/2-radius
    result=[]
    for index in loop:
        x,y,_=builder.vertices[index];rx,ry=x-cx,y-cy;ax,ay=abs(rx),abs(ry)
        if ax>ccx+1e-8 and ay>ccy+1e-8:
            dx,dy=ax-ccx,ay-ccy;length=math.hypot(dx,dy)
            nx,ny=math.copysign(dx/length,rx),math.copysign(dy/length,ry)
        elif abs(ax-(w/2-width))<1e-7:nx,ny=math.copysign(1.,rx),0.
        elif abs(ay-(h/2-width))<1e-7:nx,ny=0.,math.copysign(1.,ry)
        else:raise RuntimeFailure('QUAD_OUTLINE_MAPPING','Rounded perimeter lacks a known outward normal',point=[x,y])
        result.append((x+width*math.sin(theta)*nx,y+width*math.sin(theta)*ny,z+side*width*(1-math.cos(theta))))
    return builder.loop(result)


def _analytic_normals(mesh,p):
    """Per-corner normals preserve flat datum faces and tangent outline blends."""
    cx,cy=p.get('center',[0,0]);w,h=p['size'];r=p['corner_radius'];b=p.get('edge_bevel',0.)
    lo,hi=p['z_min'],p['z_max'];ccx=w/2-r;ccy=h/2-r
    holes={x['id']:x for x in p.get('holes',[])}
    out=[]
    for face,source in zip(mesh['faces'],mesh['face_provenance']):
        a,c,d=(mesh['vertices_mm'][face[i]] for i in (0,1,2))
        u=[c[i]-a[i] for i in range(3)];v=[d[i]-a[i] for i in range(3)]
        raw=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);length=math.sqrt(sum(x*x for x in raw));flat=tuple(x/length for x in raw)
        role=source['surface_role'];row=[]
        for index in face:
            x,y,z=mesh['vertices_mm'][index];normal=flat
            if role in ('outline_wall','outline_bevel'):
                dx=max(0.,abs(x-cx)-ccx);dy=max(0.,abs(y-cy)-ccy)
                if dx and dy:
                    length=math.hypot(dx,dy);nx,ny=math.copysign(dx/length,x-cx),math.copysign(dy/length,y-cy)
                elif dx:nx,ny=math.copysign(1.,x-cx),0.
                else:nx,ny=0.,math.copysign(1.,y-cy)
                if role=='outline_wall':normal=(nx,ny,0.)
                else:
                    bottom=z<=(lo+hi)/2
                    nz=max(0.,min(1.,(lo+b-z)/b if bottom else (z-hi+b)/b))
                    radial=math.sqrt(max(0.,1-nz*nz));normal=(nx*radial,ny*radial,-nz if bottom else nz)
            elif role.endswith(':wall') and role[:-5] in holes:
                hole=holes[role[:-5]];hx,hy=hole['center']
                if hole['kind']=='circle':
                    dx,dy=x-hx,y-hy;length=math.hypot(dx,dy);normal=(-dx/length,-dy/length,0.)
                else:
                    hw,hh=hole['size'];hr=hole['radius']
                    qx=max(hx-hw/2+hr,min(x,hx+hw/2-hr));qy=max(hy-hh/2+hr,min(y,hy+hh/2-hr))
                    dx,dy=x-qx,y-qy;length=math.hypot(dx,dy);normal=(-dx/length,-dy/length,0.)
            row.append(normal)
        out.append(row)
    mesh['corner_normals']=out
    mesh['metadata']['shading']='analytic curved-surface corner normals with flat datum faces and hard true corners'
    return mesh


def build_quad_panel(p, *, feature_id):
    width,depth=p['size'];cx,cy=p.get('center',[0,0]);lo,hi=p['z_min'],p['z_max'];radius=p['corner_radius']
    bevel=p.get('edge_bevel',0.);target=p.get('target_edge_length',4.);tolerance=p.get('chord_tolerance',.025)
    if not 0<=bevel<min(radius,(hi-lo)/2) or not 0<radius<min(width,depth)/2 or not hi>lo:
        raise RuntimeFailure('QUAD_PANEL_DIMENSIONS','Panel outline, thickness or bevel is geometrically impossible')
    holes=p.get('holes',[]);lips=p.get('lips',[])
    ids=[h['id'] for h in holes]+[l['id'] for l in lips]
    if len(set(ids))!=len(ids):raise RuntimeFailure('QUAD_FEATURE_ID','Hole and lip identities must be unique')
    for h in holes:
        cb=h.get('counterbore')
        if cb and not (h['radius']<cb['radius'] and 0<cb['depth']<hi-lo):
            raise RuntimeFailure('QUAD_COUNTERBORE_DIMENSIONS','Counterbore must be wider and shallower than the plate')
    for lip in lips:
        if lip['z_min']>=lo:raise RuntimeFailure('QUAD_LIP_DIMENSIONS','Integral bottom lip must extend below the plate')
    builder=MeshBuilder(max_vertices=200000,max_faces=200000)
    bounds=(cx-width/2+bevel,cy-depth/2+bevel,cx+width/2-bevel,cy+depth/2-bevel)
    bottom_features=_features(p,'bottom')+[{'id':x['id'],'kind':'rectangle','bounds':x['bounds']} for x in lips]
    top_features=_features(p,'top')
    patch_alignment=align_artificial_patch_events([
        {'axes':('x','y'),'bounds':bounds,'radius':radius-bevel,'holes':bottom_features},
        {'axes':('x','y'),'bounds':bounds,'radius':radius-bevel,'holes':top_features}],min(.5,target*.15))
    options={'chord_tolerance_mm':tolerance,'outline_radius':radius-bevel,
             'max_segments':p.get('max_segments',512),'target_edge_length_mm':target,
             'outline_chord_tolerance_mm':tolerance*(radius-bevel)/radius}
    bottom=tiled_face(builder,bounds,bottom_features,lo,provenance=_role(feature_id,'panel_bottom'),**options)
    top=tiled_face(builder,bounds,top_features,hi,
        x_breaks=bottom['x_breaks'],y_breaks=bottom['y_breaks'],
        provenance=_role(feature_id,'panel_top'),**options)
    for lip in lips:
        a=bottom['holes'][lip['id']];b=_translate_loop(builder,a,lip['z_min'])
        _bridge(builder,a,b,_role(feature_id,lip['id']+':wall'),target)
        _rect_cap(builder,b,lip['z_min'],_role(feature_id,lip['id']+':bottom'))
    for h in holes:
        a=bottom['holes'][h['id']];b=top['holes'][h['id']];cb=h.get('counterbore')
        role=_role(feature_id,h['id']+':wall',smooth=True)
        if cb:
            z=hi-cb['depth'] if cb['side']=='top' else lo+cb['depth']
            aa=_translate_loop(builder,a,z);bb=_translate_loop(builder,b,z)
            _bridge(builder,a,aa,role,target)
            _bridge(builder,aa,bb,_role(feature_id,h['id']+':counterbore_shoulder'),target)
            _bridge(builder,bb,b,role,target)
        else:_bridge(builder,a,b,role,target)
    low=bottom['outer'];high=top['outer']
    bevel_segments=quarter_arc_segments(bevel,tolerance,p.get('max_segments',512)) if bevel else 0
    if bevel:
        for side,original,z in ((1,low,lo),(-1,high,hi)):
            previous=original
            for segment in range(1,bevel_segments+1):
                current=_bevel_loop(builder,original,p['size'],(cx,cy),radius,bevel,math.pi/2*segment/bevel_segments,z,side)
                builder.loft(previous,current,provenance=_role(feature_id,'outline_bevel',smooth=True,support=True));previous=current
            if side==1:low=previous
            else:high=previous
    _bridge(builder,low,high,_role(feature_id,'outline_wall',smooth=True),target)
    mesh=builder.mesh();mesh['metadata']={'operation':'quad.panel','hole_ids':[h['id'] for h in holes],
        'lip_ids':[l['id'] for l in lips],'chord_tolerance_mm':tolerance,'bevel_width_mm':bevel,'bevel_segments':bevel_segments,
        'sampling':'conforming planar field with feature-local annuli and shared loft rings',
        'nominal_outline':{'size_mm':p['size'],'radius_mm':radius,'z_min_mm':lo,'z_max_mm':hi},
        'artificial_patch_alignment':patch_alignment}
    return _analytic_normals(finish_mesh(mesh,feature_id=feature_id),p)
