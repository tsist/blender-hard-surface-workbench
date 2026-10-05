"""Closed all-quad enclosure authored from conforming surface patches.

The supported corner layout is four mirrored rectangular, integral seats which
cover the inner corner radii.  This restriction is checked before allocating any
mesh.  The exposed cavity is consequently a cross, not a rounded rectangle with
hidden internal walls.  Every cap, opening tunnel and support is explicit.
"""
from __future__ import annotations

import math

from .quad_patches import MeshBuilder, tiled_face, _profile_loop, _rectangle_loop, align_artificial_patch_events, quarter_arc_segments
from .quad_common import finish_mesh
from .io import RuntimeFailure


def _role(fid, name, smooth=False, support=False):
    return {'feature_id': fid, 'surface_id': name, 'surface_role': name,
            'smooth': smooth, 'curved': smooth, 'support_band': support}


def _fail(message, **details):
    raise RuntimeFailure('QUAD_SHELL_DIMENSIONS', message, **details)


def _validate(p):
    w,d,h = p['size']; cx,cy = p.get('center',(0.,0.))
    z0=p.get('z_min',0.); r=p['corner_radius'];wall=p['wall_thickness'];base=p['base_thickness']
    bevel=p.get('edge_bevel',0.);zt=z0+h;zf=z0+base
    if not (0<wall<r<min(w,d)/2 and 0<base<h and 0<=bevel<min(wall,base,h/2,r)):
        _fail('Wall, base, outline radius and bevel must define a positive shell')
    inside=(cx-w/2+wall,cy-d/2+wall,cx+w/2-wall,cy+d/2-wall)
    seats=p['corner_seats']
    if len(seats)!=4:_fail('Exactly four corner seats are required')
    ids=[x['id'] for key in ('corner_seats','posts','openings') for x in p.get(key,[])]
    if len(set(ids))!=len(ids):_fail('All shell feature identities must be unique')
    indexed={};widths=[];depths=[];heights=[]
    for seat in seats:
        a,b,c,e=seat['bounds'];x,y=seat['hole_center'];sr=seat['hole_radius']
        sx=-1 if (a+c)/2<cx else 1;sy=-1 if (b+e)/2<cy else 1
        if (sx,sy) in indexed:_fail('Corner seats must occupy distinct corners')
        if not (inside[0]<=a<c<=inside[2] and inside[1]<=b<e<=inside[3]):_fail('Seat lies outside the inner rectangular footprint')
        if abs((a if sx<0 else c)-inside[0 if sx<0 else 2])>1e-9 or abs((b if sy<0 else e)-inside[1 if sy<0 else 3])>1e-9:
            _fail('Corner seats must meet both adjacent inner wall planes')
        if min(c-a,e-b)<r-wall or not (zt<seat['z_top'] and zf<seat['hole_bottom']<seat['z_top']):
            _fail('Seats must cover the inner fillets, rise above the rim and retain blind-hole bottoms')
        if not (a+sr<x<c-sr and b+sr<y<e-sr):_fail('Seat blind hole must lie strictly inside its seat')
        indexed[sx,sy]=seat;widths.append(c-a);depths.append(e-b);heights.append(seat['z_top'])
    if max(widths)-min(widths)>1e-9 or max(depths)-min(depths)>1e-9 or max(heights)-min(heights)>1e-9:
        _fail('Only mirrored seat footprints with a common top elevation are supported')
    sw,sd=widths[0],depths[0]
    if 2*sw>=inside[2]-inside[0] or 2*sd>=inside[3]-inside[1]:_fail('Corner seats close the cavity')
    # The elevated seat rectangle must fit within the actual exterior outline.
    if math.hypot(max(0,r-wall),max(0,r-wall))>=r-bevel:
        _fail('Seat outer corner enters the exterior round-over')
    cross=(inside[0]+sw,inside[1]+sd,inside[2]-sw,inside[3]-sd)
    for post in p.get('posts',[]):
        x,y=post['center'];pr=post['radius'];hr=post['hole_radius']
        if not (0<hr<pr and zf<post['z_top'] and z0<post['hole_top']<post['z_top']):_fail('Post radii or blind-hole elevations are invalid')
        if not (cross[0]<x-pr and x+pr<cross[2] and cross[1]<y-pr and y+pr<cross[3]):
            _fail('Supported posts must lie in the central unobstructed cavity')
    for opening in p.get('openings',[]):
        a,z=opening['center'];ow,oh=opening['size'];rr=opening['radius'];side=opening['side']
        if side not in ('left','right','back','front') or not (0<rr<=min(ow,oh)/2):_fail('Unsupported opening profile')
        lo,hi=(cross[1],cross[3]) if side in ('left','right') else (cross[0],cross[2])
        margin=max(p.get('target_edge_length',4.)*.375,rr)
        if not (lo<a-ow/2-margin and a+ow/2+margin<hi and zf<z-oh/2-margin and z+oh/2+margin<zt-bevel):
            _fail('Opening local patch must clear the base, rim and corner seats', opening=opening['id'])
    return inside,cross,indexed


def _clip(values,lo,hi):
    return sorted(set([lo,hi]+[v for v in values if lo<=v<=hi]))


def _samples(axis,counts):
    return [a+(b-a)*j/counts[(a,b)] for a,b in zip(axis,axis[1:]) for j in range(counts[(a,b)])]+[axis[-1]]


def _translate(builder, loop, z):
    return builder.loop([(builder.vertices[i][0],builder.vertices[i][1],z) for i in loop])


def _bridge(builder,a,b,role,target):
    if len(a)!=len(b):_fail('Shared ring counts differ')
    pa=[builder.vertices[i] for i in a];pb=[builder.vertices[i] for i in b]
    n=max(1,math.ceil(max(math.dist(x,y) for x,y in zip(pa,pb))/target))
    if n>4096:_fail('Axial sampling budget exceeded')
    previous=a
    for j in range(1,n+1):
        current=b if j==n else builder.loop([tuple(x+(y-x)*j/n for x,y in zip(p,q)) for p,q in zip(pa,pb)])
        builder.loft(previous,current,provenance=role);previous=current


def _circle_cap(builder,loop,center,radius,role):
    """Circular boundary -> square core -> rectangular Coons grid, all quads.

    Unlike a direct circle Coons cap, the four grid corner cells have genuine
    square corners.  Unequal quadrant counts are allowed and retained exactly.
    """
    cx,cy=center;points=[builder.vertices[i] for i in loop];z=points[0][2]
    anchors=[]
    for sx,sy in ((-1,-1),(1,-1),(1,1),(-1,1)):
        wanted=(cx+sx*radius/math.sqrt(2),cy+sy*radius/math.sqrt(2))
        candidates=[i for i,p in enumerate(points) if math.hypot(p[0]-wanted[0],p[1]-wanted[1])<1e-8]
        if len(candidates)!=1:_fail('Circle cap lacks a unique planned quadrant anchor')
        anchors.append(candidates[0])
    if anchors[0]!=0:_fail('Circle cap boundary must begin at its BL quadrant anchor')
    nx=anchors[1];ny=anchors[2]-nx
    if anchors[3]!=2*nx+ny or len(loop)!=2*(nx+ny):_fail('Circle cap opposite quadrant counts differ')
    half=radius*.45
    square=[]
    for x,y,_ in points:
        dx,dy=x-cx,y-cy;scale=half/max(abs(dx),abs(dy))
        square.append((cx+dx*scale,cy+dy*scale,z))
    core=builder.loop(square);_bridge(builder,loop,core,role,max(radius*.5,.1))
    # The boundary begins at BL diagonal, then BR, TR, TL; sides have nx,ny.
    cut=(0,nx,nx+ny,2*nx+ny);ps=[builder.vertices[i] for i in core]
    bottom=ps[:nx+1];right=ps[nx:nx+ny+1]
    top=list(reversed(ps[nx+ny:2*nx+ny+1]));left=list(reversed(ps[2*nx+ny:]+[ps[0]]))
    corners=ps[cut[0]],ps[cut[1]],ps[cut[2]],ps[cut[3]]
    grid=[]
    for j in range(ny+1):
        row=[];v=j/ny
        for i in range(nx+1):
            u=i/nx
            if j==0:q=bottom[i]
            elif j==ny:q=top[i]
            elif i==0:q=left[j]
            elif i==nx:q=right[j]
            else:
                q=tuple((1-v)*bottom[i][k]+v*top[i][k]+(1-u)*left[j][k]+u*right[j][k]
                    -((1-u)*(1-v)*corners[0][k]+u*(1-v)*corners[1][k]+u*v*corners[2][k]+(1-u)*v*corners[3][k]) for k in range(3))
            row.append(builder.vertex(q))
        grid.append(row)
    for j in range(ny):
        for i in range(nx):builder.quad(grid[j][i],grid[j][i+1],grid[j+1][i+1],grid[j+1][i],role)


def _bevel(builder,loop,p,z,sign,role):
    width=p.get('edge_bevel',0.)
    if not width:return loop
    cx,cy=p.get('center',(0,0));w,d,_=p['size'];r=p['corner_radius'];previous=loop
    segments=quarter_arc_segments(width,p.get('chord_tolerance',.025),p.get('max_segments',512))
    for j in range(1,segments+1):
        theta=math.pi/2*j/segments;out=[]
        for i in loop:
            x,y,_=builder.vertices[i];dx,dy=x-cx,y-cy;ax,ay=abs(dx),abs(dy)
            if ax>w/2-r+1e-9 and ay>d/2-r+1e-9:
                vx,vy=ax-(w/2-r),ay-(d/2-r);length=math.hypot(vx,vy)
                nx,ny=math.copysign(vx/length,dx),math.copysign(vy/length,dy)
            elif abs(ax-(w/2-width))<1e-8:nx,ny=math.copysign(1,dx),0
            elif abs(ay-(d/2-width))<1e-8:nx,ny=0,math.copysign(1,dy)
            else:_fail('Perimeter round-over has an unknown normal',point=(x,y))
            out.append((x+width*math.sin(theta)*nx,y+width*math.sin(theta)*ny,z+sign*width*(1-math.cos(theta))))
        current=builder.loop(out);builder.loft(previous,current,provenance=role);previous=current
    return previous


def _curve_witness(points, bounds, radius, name, tolerance):
    """Measure chord deviation against the exact rounded-box distance field.

    The distance field on each chord is convex; golden-section minimization
    finds its deepest inward point, including arc/straight tangency crossings.
    No mesh interpolation or bounding-box measurement substitutes for the curve.
    """
    x0,y0,x1,y1=bounds;cx=(x0+x1)/2;cy=(y0+y1)/2
    def distance(x,y):
        qx=abs(x-cx)-(x1-x0)/2+radius;qy=abs(y-cy)-(y1-y0)/2+radius
        return math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-radius
    radial=max(abs(distance(*point)) for point in points)
    error=0.;ratio=(math.sqrt(5)-1)/2
    for a,b in zip(points,points[1:]+points[:1]):
        def f(t):return distance(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
        lo,hi=0.,1.;t1=hi-ratio*(hi-lo);t2=lo+ratio*(hi-lo);v1=f(t1);v2=f(t2)
        for _ in range(48):
            if v1<v2:hi,t2,v2=t2,t1,v1;t1=hi-ratio*(hi-lo);v1=f(t1)
            else:lo,t1,v1=t1,t2,v2;t2=lo+ratio*(hi-lo);v2=f(t2)
        error=max(error,-min(v1,v2))
    if radial>1e-8 or error>tolerance*(1+1e-8):
        _fail('Authored curve exceeds its exact profile or chord tolerance',profile=name,radial_error_mm=radial,chord_error_mm=error)
    return {'profile':name,'radius_mm':radius,'sample_count':len(points),
            'profile_vertex_error_mm':radial,'maximum_chord_deviation_mm':error,
            'profile_bounds_mm':list(bounds),'witness':'analytic_rounded_box_signed_distance_on_each_authored_chord'}


def _analytic_normals(mesh,p):
    """Exact geometric normals at each corner; preserve real sharp interfaces."""
    cx,cy=p.get('center',(0,0));w,d,h=p['size'];r=p['corner_radius'];bv=p.get('edge_bevel',0.)
    lo=p.get('z_min',0.);hi=lo+h;ccx=w/2-r;ccy=d/2-r
    posts={x['id']:x for x in p.get('posts',[])};seats={x['id']:x for x in p['corner_seats']}
    openings={x['id']:x for x in p.get('openings',[])};out=[]
    for face,source in zip(mesh['faces'],mesh['face_provenance']):
        a,b,c=(mesh['vertices_mm'][face[i]] for i in (0,1,2))
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        raw=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        length=math.sqrt(sum(n*n for n in raw));flat=tuple(n/length for n in raw)
        role=source['surface_role'];row=[]
        for index in face:
            x,y,z=mesh['vertices_mm'][index];normal=flat
            if role in ('outer_corner','bottom_roundover','rim_roundover'):
                dx=max(0.,abs(x-cx)-ccx);dy=max(0.,abs(y-cy)-ccy);length=math.hypot(dx,dy)
                nx,ny=math.copysign(dx/length,x-cx),math.copysign(dy/length,y-cy)
                if role=='outer_corner':normal=(nx,ny,0.)
                else:
                    bottom=role=='bottom_roundover';nz=max(0.,min(1.,(lo+bv-z)/bv if bottom else (z-hi+bv)/bv))
                    radial=math.sqrt(max(0.,1-nz*nz));normal=(nx*radial,ny*radial,-nz if bottom else nz)
            elif role.endswith(':outer') and role[:-6] in posts:
                post=posts[role[:-6]];dx,dy=x-post['center'][0],y-post['center'][1];length=math.hypot(dx,dy)
                normal=(dx/length,dy/length,0.)
            elif role.endswith(':blind_bore') and role[:-11] in posts|seats:
                item=(posts|seats)[role[:-11]];center=item.get('hole_center',item.get('center'))
                dx,dy=x-center[0],y-center[1];length=math.hypot(dx,dy);normal=(-dx/length,-dy/length,0.)
            elif role.endswith(':tunnel') and role[:-7] in openings:
                opening=openings[role[:-7]];hc,zc=opening['center'];ow,oh=opening['size'];rr=opening['radius']
                horizontal=y if opening['side'] in ('left','right') else x
                qh=max(hc-ow/2+rr,min(horizontal,hc+ow/2-rr));qz=max(zc-oh/2+rr,min(z,zc+oh/2-rr))
                dh,dz=horizontal-qh,z-qz;length=math.hypot(dh,dz)
                normal=(0.,-dh/length,-dz/length) if opening['side'] in ('left','right') else (-dh/length,0.,-dz/length)
            row.append(normal)
        out.append(row)
    mesh['corner_normals']=out
    mesh['metadata']['shading']='analytic outline, roundover, post, bore and opening normals; exact flat datum normals'
    return mesh


def _coarse_axis(fine, lower, upper, protected, target):
    """Choose 1/3 edge groups while placing the inner grid independently.

    Every feature-patch endpoint is protected.  Remaining spans use the
    smallest bounded grid count with the same parity as the shared boundary.
    Dynamic programming distributes the 3:1 groups to minimize seam skew.
    """
    mandatory={0,len(fine)-1}|{i for i,x in enumerate(fine) if any(abs(x-a)<1e-9 or abs(x-b)<1e-9 for a,b,_ in protected)}
    anchors=sorted(mandatory);indices=[0];coarse=[lower]
    for start,end in zip(anchors,anchors[1:]):
        a=lower if start==0 else fine[start];b=upper if end==len(fine)-1 else fine[end]
        if b<=a:return None
        n=end-start;limit=min([target]+[pitch for low,high,pitch in protected if low-1e-9<=a and b<=high+1e-9])
        count=max(math.ceil((b-a)/limit),math.ceil(n/3))
        if (n-count)%2:count+=1
        if count>n:return None
        # Unchanged single edges retain their original physical coordinates.
        table={(0,0):(0.,None)}
        for j in range(count):
            for used in range(n+1):
                state=table.get((j,used))
                if state is None:continue
                for step in (1,3):
                    nextused=used+step;remaining=n-nextused;left=count-j-1
                    if nextused>n or not left<=remaining<=3*left or (remaining-left)%2:continue
                    ideal=a+(b-a)*(j+1)/count
                    delta=fine[start+nextused]-ideal
                    cost=state[0]+delta*delta
                    key=(j+1,nextused)
                    if key not in table or cost<table[key][0]:table[key]=(cost,used)
        if (count,n) not in table:return None
        chosen=[];used=n
        for j in range(count,0,-1):chosen.append(start+used);used=table[j,used][1]
        chosen.reverse()
        indices.extend(chosen);coarse.extend(b if j==count else a+(b-a)*j/count for j in range(1,count+1))
    if any(x<=fine[0] or x>=fine[-1] for x in coarse):return None
    return indices,coarse


def _local_rectangular_face(spec, xs, ys, target, tolerance, maximum, fid, required_hole_loops):
    """Keep exact shared edge samples; confine density changes to local bands."""
    from .quad_quality import validate_mesh
    if spec['radius'] or any(f['kind']=='rectangle' for f in spec['holes']):return None
    x0,y0,x1,y1=spec['bounds']
    if min(x1-x0,y1-y0)<2*target:return None
    protected=[[],[]]
    for f in spec['holes']:
        p=f['patch']
        radius=f.get('sampling_radius',f['radius']);step=min(math.sqrt(8*radius*tolerance),math.pi*radius/2)
        for dim in range(2):
            width=2*radius if f['kind']=='circle' else f['bounds'][dim+2]-f['bounds'][dim]
            length=width-2*radius+math.pi*radius/2
            protected[dim].append((p[dim],p[dim+2],min(target,(p[dim+2]-p[dim])*step*3/length)*(1-1e-12)))
    clearance=min([min(x1-x0,y1-y0)/4]+[min(f['patch'][0]-x0,f['patch'][1]-y0,x1-f['patch'][2],y1-f['patch'][3])*.6 for f in spec['holes']])
    original_cells=(len(xs)-1)*(len(ys)-1)
    for requested in (target*.6,target*.45,target*.3,target*.2):
        band=min(requested,clearance)
        if band<.05:continue
        xp=_coarse_axis(xs,x0+band,x1-band,protected[0],target)
        yp=_coarse_axis(ys,y0+band,y1-band,protected[1],target)
        if xp is None or yp is None:continue
        xi,xc=xp;yi,yc=yp
        if (len(xc)-1)*(len(yc)-1)>=original_cells*.9:continue
        candidate=MeshBuilder();role=_role(fid,spec['role']);inner=(xc[0],yc[0],xc[-1],yc[-1])
        try:
            result=tiled_face(candidate,inner,spec['holes'],chord_tolerance_mm=tolerance,max_segments=maximum,
                x_breaks=xc,y_breaks=yc,target_edge_length_mm=target,provenance=role)
            # Paired tunnel faces must retain identical actual profile samples.
            if any(len(result['holes'][key])!=len(expected) or any(math.dist(candidate.vertices[index],point)>1e-8 for index,point in zip(result['holes'][key],expected)) for key,expected in required_hole_loops.items()):continue
            # Paired surfaces share the same independent feature-patch plan.
            # Require the inner field to retain the planned coarse grid itself.
            actualx=_samples(result['x_breaks'],dict(zip(zip(result['x_breaks'],result['x_breaks'][1:]),result['x_subdivisions'])))
            actualy=_samples(result['y_breaks'],dict(zip(zip(result['y_breaks'],result['y_breaks'][1:]),result['y_subdivisions'])))
            if len(actualx)!=len(xc) or len(actualy)!=len(yc):continue
            fine_sides=[[(x,y0) for x in xs],[(x1,y) for y in ys],[(x,y1) for x in reversed(xs)],[(x0,y) for y in reversed(ys)]]
            inner_sides=[[(x,yc[0]) for x in xc],[(xc[-1],y) for y in yc],[(x,yc[-1]) for x in reversed(xc)],[(xc[0],y) for y in reversed(yc)]]
            mappings=[xi,yi,[len(xs)-1-i for i in reversed(xi)],[len(ys)-1-i for i in reversed(yi)]]
            for fine,coarse,mapping in zip(fine_sides,inner_sides,mappings):
                fi=[candidate.vertex((*point,0.)) for point in fine];ci=[candidate.vertex((*point,0.)) for point in coarse]
                for j,(first,last) in enumerate(zip(mapping,mapping[1:])):
                    q0,q1=ci[j:j+2];segment=fi[first:last+1]
                    if last-first==1:candidate.quad(segment[0],q0,q1,segment[1],role,flip=True)
                    elif last-first==3:
                        p0,p1,p2,p3=segment
                        def mix(a,b,t):return tuple(x+(y-x)*t for x,y in zip(a,b))
                        u=candidate.vertex(mix(candidate.vertices[p1],mix(candidate.vertices[q0],candidate.vertices[q1],1/3),.4))
                        v=candidate.vertex(mix(candidate.vertices[p2],mix(candidate.vertices[q0],candidate.vertices[q1],2/3),.4))
                        for face in ((p0,q0,u,p1),(p1,u,v,p2),(p2,v,q1,p3),(u,q0,q1,v)):candidate.quad(*face,provenance=role,flip=True)
                    else:_fail('Local boundary transition must consume one or three edges')
            report=validate_mesh([[v*.001 for v in point] for point in candidate.vertices],candidate.faces,
                face_provenance=candidate.face_provenance,policy={'require_closed':False,'max_finding_examples':1})
            if not report['passed']:continue
        except ValueError:
            continue
        result['outer']=candidate.loop([(*point,0.) for point in _rectangle_loop(xs,ys)])
        return candidate,result,{'band_width_mm':band,'old_field_cells':original_cells,
            'interior_field_cells':(len(xc)-1)*(len(yc)-1),'boundary_vertices':2*(len(xs)+len(ys)-2),
            'interior_columns':len(xc)-1,'interior_rows':len(yc)-1,
            'method':'independent_rectangular_field_with_mixed_one_to_one_and_three_to_one_quad_bands'}
    return None


def build_quad_shell(p, *, feature_id):
    inside,cross,seats=_validate(p)
    w,d,h=p['size'];cx,cy=p.get('center',(0,0));z0=p.get('z_min',0.);zt=z0+h;zf=z0+p['base_thickness']
    r=p['corner_radius'];wall=p['wall_thickness'];bv=p.get('edge_bevel',0.)
    target=p.get('target_edge_length',4.);tol=p.get('chord_tolerance',.025);maximum=p.get('max_segments',512)
    if not (0<target and 0<tol and isinstance(maximum,int) and 4<=maximum<=4096):_fail('Invalid sampling budget')
    outer=(cx-w/2+bv,cy-d/2+bv,cx+w/2-bv,cy+d/2-bv)
    specs=[]
    def add(name,bounds,holes,axes,mapper,*,radius=0.,smooth=False,role=None):
        spec={'name':name,'bounds':tuple(bounds),'holes':holes,'axes':axes,'mapper':mapper,'radius':radius,'smooth':smooth,'role':role or name}
        specs.append(spec)
    def post_features(hole):
        features=[]
        for post in p.get('posts',[]):
            x,y=post['center'];pr=post['hole_radius'] if hole else post['radius'];half=pr+max(target*.625,pr*.6)
            features.append({'id':post['id'],'kind':'circle','center':(x,y),'radius':post['hole_radius'] if hole else pr,
                'sampling_radius':pr,'patch':(x-half,y-half,x+half,y+half)})
        return features
    bottom_field=(cx-w/2+r,cy-d/2+r,cx+w/2-r,cy+d/2-r)
    add('bottom',outer,[{'id':'bottom_field','kind':'rectangle','bounds':bottom_field}],('x','y'),lambda a,b:(a,b,z0),radius=r-bv)
    add('bottom:field',bottom_field,post_features(True),('x','y'),lambda a,b:(a,b,z0),role='bottom')
    add('floor',(cross[0],inside[1],cross[2],inside[3]),post_features(False),('x','y'),lambda a,b:(a,b,zf))
    add('floor:left',(inside[0],cross[1],cross[0],cross[3]),[],('x','y'),lambda a,b:(a,b,zf),role='floor')
    add('floor:right',(cross[2],cross[1],inside[2],cross[3]),[],('x','y'),lambda a,b:(a,b,zf),role='floor')
    add('rim',outer,[{'id':'cavity','kind':'rectangle','bounds':inside}],('x','y'),lambda a,b:(a,b,zt),radius=r-bv)
    sideinfo={
        'left':('y',cy-d/2+r,cy+d/2-r,cross[1],cross[3],cx-w/2,inside[0]),
        'right':('y',cy-d/2+r,cy+d/2-r,cross[1],cross[3],cx+w/2,inside[2]),
        'front':('x',cx-w/2+r,cx+w/2-r,cross[0],cross[2],cy-d/2,inside[1]),
        'back':('x',cx-w/2+r,cx+w/2-r,cross[0],cross[2],cy+d/2,inside[3]),
    }
    for side,(axis,a,b,ia,ib,plane,iplane) in sideinfo.items():
        holes=[]
        for opening in p.get('openings',[]):
            if opening['side']!=side:continue
            c,z=opening['center'];ow,oh=opening['size'];rr=opening['radius'];m=max(target*.375,rr)
            holes.append({'id':opening['id'],'kind':'rounded_rectangle','radius':rr,
                'bounds':(c-ow/2,z-oh/2,c+ow/2,z+oh/2),'patch':(c-ow/2-m,z-oh/2-m,c+ow/2+m,z+oh/2+m)})
        mapper=(lambda a,z,v=plane:(v,a,z)) if axis=='y' else (lambda a,z,v=plane:(a,v,z))
        imapper=(lambda a,z,v=iplane:(v,a,z)) if axis=='y' else (lambda a,z,v=iplane:(a,v,z))
        add(side+':outer',(a,z0+bv,b,zt-bv),holes,(axis,'z'),mapper)
        add(side+':inner',(ia,zf,ib,zt),holes,(axis,'z'),imapper)
    for (sx,sy),seat in seats.items():
        a,b,c,e=seat['bounds'];st=seat['z_top'];name=seat['id'];ix=c if sx<0 else a;iy=e if sy<0 else b;ox=a if sx<0 else c;oy=b if sy<0 else e
        add(name+':inner_x',(b,zf,e,st),[],('y','z'),lambda v,z,x=ix:(x,v,z))
        add(name+':inner_y',(a,zf,c,st),[],('x','z'),lambda v,z,y=iy:(v,y,z))
        add(name+':outer_x',(b,zt,e,st),[],('y','z'),lambda v,z,x=ox:(x,v,z))
        add(name+':outer_y',(a,zt,c,st),[],('x','z'),lambda v,z,y=oy:(v,y,z))
    patch_alignment=align_artificial_patch_events(specs,min(.5,target*.15))
    # One coordinate event schedule per physical axis.  Planning only adds
    # subdivisions; dimensions and nominal profile coordinates never move.
    events={a:set() for a in ('x','y','z')}
    for s in specs:
        for dim,axis in enumerate(s['axes']):
            low,high=s['bounds'][dim],s['bounds'][dim+2];events[axis].update((low,high))
            if s['radius']:events[axis].update((low+s['radius'],high-s['radius']))
            for f in s['holes']:
                box=f.get('patch',f.get('bounds'));events[axis].update((box[dim],box[dim+2]))
    for seat in seats.values():
        a,b,c,e=seat['bounds'];events['x'].update((a,c));events['y'].update((b,e))
    events={axis:sorted(values) for axis,values in events.items()}
    if any(any(b-a<1e-6 for a,b in zip(values,values[1:])) for values in events.values()):_fail('Distinct design events create micro-edge intervals')
    counts={axis:{(a,b):max(1,math.ceil((b-a)/target)) for a,b in zip(values,values[1:])} for axis,values in events.items()}
    for s in specs:
        axes=[_clip(events[a],s['bounds'][dim],s['bounds'][dim+2]) for dim,a in enumerate(s['axes'])]
        temp=tiled_face(MeshBuilder(),s['bounds'],s['holes'],outline_radius=s['radius'],chord_tolerance_mm=tol,max_segments=maximum,
            x_breaks=axes[0],y_breaks=axes[1],target_edge_length_mm=target,outline_chord_tolerance_mm=tol*(r-bv)/r)
        for dim,axis in enumerate(s['axes']):
            breaks=temp['xy'[dim]+'_breaks'];ns=temp['xy'[dim]+'_subdivisions']
            for a,b,n in zip(breaks,breaks[1:],ns):counts[axis][a,b]=max(counts[axis][a,b],n)
    # Seat annuli have no artificial surrounding patch: their true rectangular
    # boundary is shared with the walls and floor.  Sample each quarter circle.
    for seat in seats.values():
        rr=seat['hole_radius'];step=min(math.sqrt(8*rr*tol),math.pi*rr/2)
        for dim,axis in enumerate(('x','y')):
            a,b=seat['bounds'][dim],seat['bounds'][dim+2]
            for lo,hi in counts[axis]:
                if a<=lo and hi<=b:counts[axis][lo,hi]=max(counts[axis][lo,hi],math.ceil(math.pi*rr/2*(hi-lo)/(b-a)/step))
    samples={axis:_samples(values,counts[axis]) for axis,values in events.items()}
    builder=MeshBuilder(max_vertices=200000,max_faces=200000);results={};witnesses=[];local_plans={}
    for s in specs:
        axes=[_clip(events[a],s['bounds'][dim],s['bounds'][dim+2]) for dim,a in enumerate(s['axes'])]
        tempbuilder=MeshBuilder();tmp=tiled_face(tempbuilder,s['bounds'],s['holes'],outline_radius=s['radius'],chord_tolerance_mm=tol,max_segments=maximum,
            x_breaks=axes[0],y_breaks=axes[1],x_subdivisions=[counts[s['axes'][0]][a,b] for a,b in zip(axes[0],axes[0][1:])],
            y_subdivisions=[counts[s['axes'][1]][a,b] for a,b in zip(axes[1],axes[1][1:])],target_edge_length_mm=target,outline_chord_tolerance_mm=tol*(r-bv)/r,
            provenance=_role(feature_id,s['role'],s['smooth']))
        xs=_samples(tmp['x_breaks'],dict(zip(zip(tmp['x_breaks'],tmp['x_breaks'][1:]),tmp['x_subdivisions'])))
        ys=_samples(tmp['y_breaks'],dict(zip(zip(tmp['y_breaks'],tmp['y_breaks'][1:]),tmp['y_subdivisions'])))
        required_hole_loops={key:[tempbuilder.vertices[i] for i in loop] for key,loop in tmp['holes'].items()}
        independent=_local_rectangular_face(s,xs,ys,target,tol,maximum,feature_id,required_hole_loops)
        if independent is not None:
            candidate,candidate_result,plan=independent
            if len(candidate.faces)<len(tempbuilder.faces):
                plan['original_faces']=len(tempbuilder.faces);plan['final_faces']=len(candidate.faces)
                tempbuilder,tmp=candidate,candidate_result;local_plans[s['name']]=plan
        remap=[builder.vertex(s['mapper'](a,b)) for a,b,_ in tempbuilder.vertices]
        for face,prov in zip(tempbuilder.faces,tempbuilder.face_provenance):builder.quad(*(remap[i] for i in face),provenance=prov)
        results[s['name']]={'outer':[remap[i] for i in tmp['outer']], 'holes':{key:[remap[i] for i in val] for key,val in tmp['holes'].items()},'plan':tmp}
    # Straight side panels omit the outer corner arcs.  These retain the same
    # XY ring samples and shared Z schedule as their adjacent wall panels.
    bottom=results['bottom'];rim=results['rim']
    low=_bevel(builder,bottom['outer'],p,z0,1,_role(feature_id,'bottom_roundover',True,True))
    high=_bevel(builder,rim['outer'],p,zt,-1,_role(feature_id,'rim_roundover',True,True))
    zs=_clip(samples['z'],z0+bv,zt-bv)
    for i,ai in enumerate(low):
        j=(i+1)%len(low);pa=builder.vertices[ai];pb=builder.vertices[low[j]]
        if abs(pa[0]-pb[0])<1e-9 or abs(pa[1]-pb[1])<1e-9:continue
        previous=(ai,low[j])
        for z in zs[1:]:
            current=(builder.vertex((pa[0],pa[1],z)),builder.vertex((pb[0],pb[1],z)))
            builder.quad(previous[0],previous[1],current[1],current[0],_role(feature_id,'outer_corner',True));previous=current
        # Chord sagitta uses the requested unchanged outward profile.
        chord=math.dist(pa,pb);sagitta=r-math.sqrt(max(0,r*r-chord*chord/4))
        if sagitta>tol*(1+1e-8):_fail('Expanded outer profile exceeds requested chord tolerance',sagitta_mm=sagitta,chord_tolerance_mm=tol)
        witnesses.append({'feature_id':feature_id,'profile':'outer_corner','radius_mm':r,'sagitta_mm':sagitta})
    for opening in p.get('openings',[]):
        side=opening['side'];key=opening['id'];a=results[side+':outer']['holes'][key];b=results[side+':inner']['holes'][key]
        _bridge(builder,a,b,_role(feature_id,key+':tunnel',True),target)
        c,z=opening['center'];ow,oh=opening['size']
        coords=[(builder.vertices[i][1 if side in ('left','right') else 0],builder.vertices[i][2]) for i in a]
        witnesses.append(_curve_witness(coords,(c-ow/2,z-oh/2,c+ow/2,z+oh/2),opening['radius'],key,tol))
    for post in p.get('posts',[]):
        name=post['id'];a=results['floor']['holes'][name];b=_translate(builder,a,post['z_top'])
        _bridge(builder,a,b,_role(feature_id,name+':outer',True),target)
        x,y=post['center'];rr=post['radius']
        witnesses.append(_curve_witness([builder.vertices[i][:2] for i in a],(x-rr,y-rr,x+rr,y+rr),rr,name+':outer',tol))
        _circle_cap(builder,b,post['center'],post['radius'],_role(feature_id,name+':top'))
        a=results['bottom:field']['holes'][name];b=_translate(builder,a,post['hole_top'])
        _bridge(builder,a,b,_role(feature_id,name+':blind_bore',True),target)
        rr=post['hole_radius']
        witnesses.append(_curve_witness([builder.vertices[i][:2] for i in a],(x-rr,y-rr,x+rr,y+rr),rr,name+':blind_bore',tol))
        _circle_cap(builder,b,post['center'],post['hole_radius'],_role(feature_id,name+':blind_bottom'))
    for seat in seats.values():
        a,b,c,e=seat['bounds'];name=seat['id'];z=seat['z_top'];x,y=seat['hole_center'];rr=seat['hole_radius']
        fx=[v for v in samples['x'] if a<=v<=c];fy=[v for v in samples['y'] if b<=v<=e]
        rect=builder.loop([(*q,z) for q in _rectangle_loop(fx,fy)])
        hole=builder.loop([(*q,z) for q in _profile_loop((x-rr,y-rr,x+rr,y+rr),rr,fx,fy)])
        builder.loft(rect,hole,provenance=_role(feature_id,name+':top'))
        witnesses.append(_curve_witness([builder.vertices[i][:2] for i in hole],(x-rr,y-rr,x+rr,y+rr),rr,name+':blind_bore',tol))
        end=_translate(builder,hole,seat['hole_bottom']);_bridge(builder,hole,end,_role(feature_id,name+':blind_bore',True),target)
        _circle_cap(builder,end,seat['hole_center'],rr,_role(feature_id,name+':blind_bottom'))
    mesh=builder.mesh();mesh['metadata']={'operation':'quad.shell','construction':'shared_boundary_quad_patches',
        'chord_tolerance_mm':tol,'bevel_width_mm':bv,'bevel_segments':quarter_arc_segments(bv,tol,maximum) if bv else 0,
        'seat_ids':[s['id'] for s in p['corner_seats']],'post_ids':[s['id'] for s in p.get('posts',[])],
        'opening_ids':[s['id'] for s in p.get('openings',[])],'cavity_outline':'cross_with_fully_integral_rectangular_corner_seats',
        'nominal_outline':{'size_mm':p['size'],'radius_mm':r,'wall_mm':wall,'base_mm':p['base_thickness'],'z_min_mm':z0},
        'curve_witnesses':witnesses,'local_surface_plans':local_plans,'max_stitch_error_mm':builder.max_stitch_error_mm,
        'artificial_patch_alignment':patch_alignment}
    return _analytic_normals(finish_mesh(mesh,feature_id=feature_id),p)
