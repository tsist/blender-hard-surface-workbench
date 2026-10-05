"""Task-authored binary64 geometry audit. No Blender, I/O, or state changes.

Independent helpers for a reviewed fixed CLI adapter. EPS is used only for
coplanarity and physical boundary classification, never clipping half-plane
expansion. Closure/outward-topology gating is mandatory before winding fallback.
"""
import math

EPS_MM=.002

def add(a,b):return tuple(x+y for x,y in zip(a,b))
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def mul(a,s):return tuple(x*s for x in a)
def dot(a,b):return math.fsum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def norm2(a):return dot(a,a)
def norm(a):return math.sqrt(norm2(a))
def cross2(a,b):return a[0]*b[1]-a[1]*b[0]
def area2(p):
    anchor=p[0]
    return math.fsum(cross2(sub(p[i],anchor),sub(p[i+1],anchor)) for i in range(1,len(p)-1))
def finite_points(points):return all(math.isfinite(float(x)) for p in points for x in p)

def point_in_convex_2d(point,polygon):
    """Roundoff-only tolerance, not geometric/contact EPS."""
    area=area2(polygon)
    if abs(area)<1e-15:return False
    sign=1 if area>0 else -1
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        e=sub(b,a);p=sub(point,a)
        coordinate_ulp=max(math.ulp(max(1.,abs(x))) for x in (*a,*b,*point))
        error=max(1e-13,64*coordinate_ulp*norm(e))
        if sign*cross2(e,p)<-error:return False
    return True

def clip_convex_2d(subject,clip):
    """Sutherland-Hodgman against exact zero half-planes in float64."""
    if not finite_points(subject+clip):raise ValueError('Nonfinite coordinate')
    area=area2(clip)
    if abs(area)<1e-15 or abs(area2(subject))<1e-15:raise ValueError('Degenerate polygon')
    sign=1 if area>0 else -1
    polygon=[tuple(map(float,p)) for p in subject];audit=[]
    for edge_index,(u,v) in enumerate(zip(clip,clip[1:]+clip[:1])):
        if not polygon:break
        edge=sub(v,u)
        def side(p):return sign*cross2(edge,sub(p,u))
        output=[];previous=polygon[-1];sp=side(previous)
        for current in polygon:
            sc=side(current);pin=sp>=0.;cin=sc>=0.
            if pin!=cin:
                denominator=sp-sc
                if denominator==0.:raise ArithmeticError('Crossing with zero denominator')
                t=sp/denominator
                if not 0.<=t<=1.:raise ArithmeticError('Intersection parameter outside source segment')
                q=add(previous,mul(sub(current,previous),t))
                audit.append({'edge':edge_index,'sp':sp,'sc':sc,'t':t,'segment_start':previous,'segment_end':current,'intersection':q})
                output.append(q)
            if cin:output.append(current)
            previous,sp=current,sc
        polygon=output
    # Fail closed if numerical error violates either original triangle.
    for p in polygon:
        if not point_in_convex_2d(p,subject) or not point_in_convex_2d(p,clip):
            raise ArithmeticError('Computed intersection outside an input polygon')
    return {'points':polygon,'crossings':audit,'method':'float64 zero-half-plane clipping with source-segment and both-input containment checks'}

def coplanar_triangle_intersection(ta,tb,epsilon_mm=EPS_MM):
    ta=[tuple(map(float,p)) for p in ta];tb=[tuple(map(float,p)) for p in tb]
    if len(ta)!=3 or len(tb)!=3 or not finite_points(ta+tb):raise ValueError('Require finite triangles')
    normal=cross(sub(ta[1],ta[0]),sub(ta[2],ta[0]));length=norm(normal)
    if length<1e-15:raise ValueError('Degenerate source triangle')
    normal_b=cross(sub(tb[1],tb[0]),sub(tb[2],tb[0]));length_b=norm(normal_b)
    if length_b<1e-15:raise ValueError('Degenerate target triangle')
    normal_error=norm(cross(mul(normal,1/length),mul(normal_b,1/length_b)))
    distances=[abs(dot(sub(p,ta[0]),normal))/length for p in tb]
    if normal_error>1e-6 or max(distances)>epsilon_mm:raise ValueError('Not coplanar within unchanged classification tolerance')
    axis=max(range(3),key=lambda i:abs(normal[i]));keep=[i for i in range(3) if i!=axis]
    def project(p):return tuple(p[i] for i in keep)
    raw=clip_convex_2d([project(p) for p in ta],[project(p) for p in tb]);points=[]
    for p in raw['points']:
        q=[0.,0.,0.]
        for i,x in zip(keep,p):q[i]=x
        q[axis]=ta[0][axis]-math.fsum(normal[i]*(q[i]-ta[0][i]) for i in keep)/normal[axis]
        points.append(q)
    return {'points_mm':points,'projection_drop_axis':axis,'maximum_plane_distance_mm':max(distances),'normal_cross_length':normal_error,'crossings':raw['crossings'],'epsilon_mm':epsilon_mm,'clipping_half_plane_expansion_mm':0.,'method':raw['method']}

def closest_point_triangle(p,triangle):
    a,b,c=triangle;ab=sub(b,a);ac=sub(c,a);ap=sub(p,a);d1=dot(ab,ap);d2=dot(ac,ap)
    if d1<=0 and d2<=0:return a
    bp=sub(p,b);d3=dot(ab,bp);d4=dot(ac,bp)
    if d3>=0 and d4<=d3:return b
    vc=d1*d4-d3*d2
    if vc<=0 and d1>=0 and d3<=0:return add(a,mul(ab,d1/(d1-d3)))
    cp=sub(p,c);d5=dot(ab,cp);d6=dot(ac,cp)
    if d6>=0 and d5<=d6:return c
    vb=d5*d2-d1*d6
    if vb<=0 and d2>=0 and d6<=0:return add(a,mul(ac,d2/(d2-d6)))
    va=d3*d6-d5*d4
    if va<=0 and (d4-d3)>=0 and (d5-d6)>=0:
        return add(b,mul(sub(c,b),(d4-d3)/((d4-d3)+(d5-d6))))
    denominator=va+vb+vc
    if denominator==0:raise ValueError('Degenerate triangle in boundary test')
    return add(a,add(mul(ab,vb/denominator),mul(ac,vc/denominator)))

def boundary_distance(point,triangles):
    best=None
    for i,triangle in enumerate(triangles):
        q=closest_point_triangle(point,triangle);d2=norm2(sub(point,q))
        if best is None or d2<best[0]:best=(d2,i,q)
    if best is None:raise ValueError('Empty triangle mesh')
    return {'distance_mm':math.sqrt(best[0]),'triangle_index':best[1],'nearest_point_mm':best[2]}

def raw_solid_angle_winding(point,triangles):
    angles=[]
    for triangle in triangles:
        a,b,c=[sub(v,point) for v in triangle];la,lb,lc=map(norm,(a,b,c))
        if min(la,lb,lc)<1e-12:return {'status':'boundary_or_degenerate','winding':None}
        numerator=dot(a,cross(b,c));denominator=la*lb*lc+dot(a,b)*lc+dot(b,c)*la+dot(c,a)*lb
        angles.append(2*math.atan2(numerator,denominator))
    total=math.fsum(angles);w=total/(4*math.pi)
    if abs(w)<1e-5:status='outside'
    elif abs(w-1)<1e-5:status='inside'
    else:status='indeterminate'
    return {'status':status,'winding':w,'angle_sum_radians':total,'triangles':len(angles),'threshold':1e-5}

def resolve_indeterminate(point,triangles,*,parity_status,closed_outward_topology_pass,epsilon_mm=EPS_MM):
    """Only fallback for parity-indeterminate + previously verified outward closed mesh."""
    if parity_status!='indeterminate':
        return {'status':parity_status,'fallback_used':False,'reason':'Existing parity result retained'}
    if not closed_outward_topology_pass:
        return {'status':'indeterminate','fallback_used':False,'reason':'Closed, consistently outward topology not established'}
    if len(triangles)>400000:raise ValueError('Fixed triangle budget exceeded')
    if not finite_points([point]) or not finite_points([v for t in triangles for v in t]):raise ValueError('Nonfinite coordinate')
    distance=boundary_distance(point,triangles)
    if distance['distance_mm']<=epsilon_mm:
        return {'status':'boundary','fallback_used':True,'boundary':distance,'epsilon_mm':epsilon_mm,'reason':'Independent float64 boundary distance; winding not used'}
    result=raw_solid_angle_winding(point,triangles)
    return {**result,'fallback_used':True,'boundary':distance,'epsilon_mm':epsilon_mm,'method':'full oriented triangle solid-angle sum in float64; closed-topology gate required'}

def point_in_triangle_3d_roundoff(point,triangle):
    n=cross(sub(triangle[1],triangle[0]),sub(triangle[2],triangle[0]));length=norm(n)
    if length<1e-15:return False
    coordinate_ulp=max(math.ulp(max(1.,abs(x))) for v in [point,*triangle] for x in v)
    if abs(dot(sub(point,triangle[0]),n))/length>128*coordinate_ulp:return False
    drop=max(range(3),key=lambda i:abs(n[i]));keep=[i for i in range(3) if i!=drop]
    return point_in_convex_2d(tuple(point[i] for i in keep),[tuple(p[i] for i in keep) for p in triangle])

def triangle_intersection(ta,tb,epsilon_mm=EPS_MM):
    """True noncoplanar edge-plane intersections; no EPS-thick triangle test."""
    ta=[tuple(map(float,p)) for p in ta];tb=[tuple(map(float,p)) for p in tb]
    if len(ta)!=3 or len(tb)!=3 or not finite_points(ta+tb):raise ValueError('Require finite triangles')
    normals=[]
    for t in [ta,tb]:
        n=cross(sub(t[1],t[0]),sub(t[2],t[0]));l=norm(n)
        if l<1e-15:raise ValueError('Degenerate triangle')
        normals.append(mul(n,1/l))
    ulp=max(math.ulp(max(1.,abs(x))) for p in ta+tb for x in p);zero_tol=128*ulp
    parallel=norm(cross(*normals))<1e-6
    if parallel:
        signed=[dot(sub(p,ta[0]),normals[0]) for p in tb]
        separation=max(map(abs,signed))
        if separation<=epsilon_mm:
            return {**coplanar_triangle_intersection(ta,tb,epsilon_mm),'kind':'coplanar_within_classification_epsilon'}
        # An angular threshold does not prove disjoint planes. A shallowly
        # tilted triangle can cross a large source triangle. Reject only when
        # every vertex is strictly on one side; touching/straddling continues
        # through the actual edge-plane intersection path below.
        if min(signed)>zero_tol or max(signed)<-zero_tol:
            return {'points_mm':[],'kind':'parallel_disjoint','plane_distance_mm':separation,'proof':'all target vertices strictly on one source-plane side'}
    points=[];audit=[]
    for source_index,(source,target,n) in enumerate([(ta,tb,normals[1]),(tb,ta,normals[0])]):
        for edge_index,(u,v) in enumerate(zip(source,source[1:]+source[:1])):
            du=dot(sub(u,target[0]),n);dv=dot(sub(v,target[0]),n)
            candidates=[]
            if abs(du)<=zero_tol:candidates.append((u,0.,'endpoint_on_plane_roundoff'))
            if du*dv<0:
                t=du/(du-dv)
                if not 0<=t<=1:raise ArithmeticError('Noncoplanar crossing outside source segment')
                candidates.append((add(u,mul(sub(v,u),t)),t,'strict_plane_crossing'))
            for p,t,reason in candidates:
                inside=point_in_triangle_3d_roundoff(p,target) and point_in_triangle_3d_roundoff(p,source)
                audit.append({'source':source_index,'edge':edge_index,'du_mm':du,'dv_mm':dv,'t':t,'reason':reason,'point_mm':p,'inside_both':inside})
                if inside and not any(norm(sub(p,q))<=zero_tol for q in points):points.append(p)
    return {'points_mm':points,'kind':'noncoplanar','roundoff_distance_mm':zero_tol,'geometric_triangle_thickening_mm':0.,'candidates':audit,'epsilon_mm':epsilon_mm}
