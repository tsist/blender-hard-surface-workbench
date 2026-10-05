"""Independent two-dimensional geometry checks; never an alternate solver.

Internal metric is mm / degrees. Seeds in a solved sketch are the backend's
candidate coordinates, not proof that the driving equations were satisfied.
"""
from __future__ import annotations
import copy
import math

VERSION = '1.0.0'
LENGTH_TO_MM = {'mm': 1., 'cm': 10., 'm': 1000., 'in': 25.4}


class SketchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sub(a, b): return (a[0]-b[0], a[1]-b[1])
def add(a, b): return (a[0]+b[0], a[1]+b[1])
def mul(a, s): return (a[0]*s, a[1]*s)
def dot(a, b): return a[0]*b[0]+a[1]*b[1]
def cross(a, b): return a[0]*b[1]-a[1]*b[0]
def norm(a): return math.hypot(*a)
def dist(a, b): return norm(sub(a, b))
def wrap(a): return (a+180.) % 360.-180.


def metric_sketch(sketch, length_unit='mm'):
    if length_unit not in LENGTH_TO_MM:
        raise SketchError('unsupported_unit', str(length_unit))
    out = copy.deepcopy(sketch)
    scale = LENGTH_TO_MM[length_unit]
    for e in out['entities']:
        if e['kind'] == 'point2d': e['seed'] = [x*scale for x in e['seed']]
        elif e['kind'] in ('circle2d', 'arc2d'): e['radius_seed'] *= scale
    for c in out.get('constraints', []):
        if c['type'] == 'point_fixed2d': c['at'] = [x*scale for x in c['at']]
    return out


def entity_map(sketch):
    entities = {e['id']: e for e in sketch['entities']}
    if len(entities) != len(sketch['entities']):
        raise SketchError('duplicate_entity', 'Entity IDs must be unique')
    return entities


def point(es, name):
    e = es[name]
    if e['kind'] != 'point2d': raise SketchError('invalid_reference', name+' is not a point')
    p = e['seed']
    if len(p) != 2 or not all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) for x in p):
        raise SketchError('invalid_geometry','Nonfinite point')
    return tuple(p)


def line(es, name):
    e = es[name]
    if e['kind'] != 'line_segment2d': raise SketchError('invalid_reference', name+' is not a line')
    a, b = point(es,e['start']), point(es,e['end'])
    if dist(a,b) <= 1e-12: raise SketchError('degenerate_geometry',name+' is zero length')
    return a,b


def curve(es,name):
    e=es[name]
    if e['kind'] not in ('circle2d','arc2d'): raise SketchError('invalid_reference',name+' is not circular')
    r=e['radius_seed']
    if not math.isfinite(r) or r <= 0: raise SketchError('degenerate_geometry','Radius must be positive')
    if e['kind']=='arc2d' and (not math.isfinite(e['sweep_seed']) or not 0 < abs(e['sweep_seed']) < 360):
        raise SketchError('degenerate_geometry','Arc sweep must be nonzero and less than 360 degrees')
    return point(es,e['center']),r


def arc_point(es,name,angle):
    c,r=curve(es,name); t=math.radians(angle)
    return (c[0]+r*math.cos(t),c[1]+r*math.sin(t))


def in_sweep(es,name,p,tol=1e-8):
    e=es[name]
    if e['kind']=='circle2d': return True
    c,r=curve(es,name); angle=math.degrees(math.atan2(p[1]-c[1],p[0]-c[0]))
    start,sweep=e['start_angle_seed'],e['sweep_seed']
    delta=((angle-start) if sweep>0 else (start-angle))%360
    return delta <= abs(sweep)+math.degrees(tol/r) or 360-delta <= math.degrees(tol/r)


def foot(p,a,b):
    v=sub(b,a); t=dot(sub(p,a),v)/dot(v,v)
    return add(a,mul(v,t)),t,cross(v,sub(p,a))/norm(v)


def verify_constraints(sketch, dimensions, length_tolerance_mm=.001, angle_tolerance_deg=.001):
    """dimensions are SI values (metres/radians); sketch coordinates are mm."""
    es=entity_map(sketch); rows=[]
    if not (math.isfinite(length_tolerance_mm) and length_tolerance_mm>0 and math.isfinite(angle_tolerance_deg) and angle_tolerance_deg>0):
        raise SketchError('invalid_tolerance','Positive finite verification tolerances required')
    for e in es.values():
        if e['kind']=='point2d': point(es,e['id'])
        elif e['kind']=='line_segment2d': line(es,e['id'])
        elif e['kind'] in ('circle2d','arc2d'): curve(es,e['id'])
        else: raise SketchError('unsupported_entity',e['kind'])
    for c in sketch.get('constraints',[]):
        t=c['type']; angle=t in ('angle','arc_start_angle','arc_sweep','parallel','perpendicular')
        unit='deg' if angle else 'mm'; limit=angle_tolerance_deg if angle else length_tolerance_mm
        value=dimensions.get(c.get('dimension'))
        target=None if value is None else (math.degrees(value) if angle else value*1000)
        if 'dimension' in c and target is None: raise SketchError('unresolved_dimension',c['dimension'])
        measured=None; domain_ok=True
        if t=='point_fixed2d': residual=dist(point(es,c['point']),c['at'])
        elif t=='coincident': residual=dist(point(es,c['a']),point(es,c['b']))
        elif t=='arc_endpoint_coincident':
            e=es[c['arc']];angle=e['start_angle_seed']+(e['sweep_seed'] if c['endpoint']=='end' else 0)
            residual=dist(arc_point(es,c['arc'],angle),point(es,c['point']))
        elif t in ('horizontal','vertical'):
            a,b=line(es,c['line']); residual=abs(b[1]-a[1]) if t=='horizontal' else abs(b[0]-a[0])
        elif t in ('axis_distance2d','distance'):
            a,b=point(es,c['from']),point(es,c['to'])
            measured=(b[0]-a[0] if c['axis']=='X' else b[1]-a[1]) if t=='axis_distance2d' else dist(a,b)
            residual=abs(measured-target)
        elif t in ('radius','diameter'):
            _,r=curve(es,c['circle']); measured=r*(2 if t=='diameter' else 1); residual=abs(measured-target)
        elif t in ('arc_start_angle','arc_sweep'):
            e=es[c['arc']]; measured=e['start_angle_seed'] if t=='arc_start_angle' else e['sweep_seed']
            residual=abs(wrap(measured-target)) if t=='arc_start_angle' else abs(measured-target)
        elif t in ('parallel','perpendicular','angle','equal_length'):
            a,b=line(es,c['a']); p,q=line(es,c['b']); u,v=sub(b,a),sub(q,p)
            theta=math.degrees(math.atan2(cross(u,v),dot(u,v)))
            if t=='parallel': residual=min(abs(theta),180-abs(theta))
            elif t=='perpendicular': residual=abs(abs(theta)-90)
            elif t=='equal_length': residual=abs(norm(u)-norm(v))
            else:
                measured=theta if c.get('directed',True) else abs(theta)
                residual=abs(wrap(measured-target)) if c.get('directed',True) else abs(measured-target)
        elif t in ('concentric','equal_radius'):
            a,r=curve(es,c['a']); b,s=curve(es,c['b']); residual=dist(a,b) if t=='concentric' else abs(r-s)
        elif t=='point_line_distance':
            a,b=line(es,c['line']); _,_,signed=foot(point(es,c['point']),a,b)
            measured=signed*(1 if c['side']=='left' else -1); residual=abs(measured-target)
        elif t=='equal_spacing':
            axis=0 if c['axis']=='X' else 1
            p=[point(es,n)[axis] for n in c['points']]
            d=[b-a for a,b in zip(p,p[1:])]
            residual=max(abs(x-d[0]) for x in d)
        elif t in ('tangent_line_circle','tangent_line_arc'):
            name=c.get('circle',c.get('arc')); ctr,r=curve(es,name); a,b=line(es,c['line']); f,u,signed=foot(ctr,a,b)
            residual=abs(signed*(1 if c['side']=='left' else -1)-r)
            domain_ok=(c['domain']=='infinite' or -length_tolerance_mm/dist(a,b)<=u<=1+length_tolerance_mm/dist(a,b)) and in_sweep(es,name,f,length_tolerance_mm)
        elif t in ('tangent_circle_circle','tangent_arc_arc'):
            a,r=curve(es,c['a']); b,s=curve(es,c['b']); d=dist(a,b)
            if d<=1e-12: raise SketchError('degenerate_geometry','Concentric curves have no unique tangency')
            external=c['branch']=='external'; residual=abs(d-(r+s if external else abs(r-s)))
            if external: contact=add(a,mul(sub(b,a),r/d))
            else:
                outer=c.get('internal_outer')
                domain_ok=outer in (c['a'],c['b']) and ((r>s) if outer==c['a'] else (s>r))
                contact=add(a,mul(sub(b,a),r/d*(1 if r>s else -1)))
            domain_ok=domain_ok and in_sweep(es,c['a'],contact,length_tolerance_mm) and in_sweep(es,c['b'],contact,length_tolerance_mm)
        else: raise SketchError('unsupported_constraint',t)
        reference=c.get('mode','driving')=='reference'
        rows.append({'constraint_id':c['id'],'type':t,'mode':c.get('mode','driving'),
                     'verification_residual':residual,'unit':unit,'tolerance':limit,'domain_valid':domain_ok,
                     'measured_value':measured,'target_value':target,
                     'status':'measurement_only' if reference else ('pass' if residual<=limit and domain_ok else 'fail')})
    return {'status':'pass' if all(r['status']!='fail' for r in rows) else 'fail','constraints':rows,
            'implementation':VERSION,'native_solver_residual':None}


def signed_area(points):
    return sum(cross(a,b) for a,b in zip(points,points[1:]+points[:1]))/2


def segments_intersect(a,b,c,d,tol=1e-9):
    def orient(p,q,r): return cross(sub(q,p),sub(r,p))
    def on(p,q,r): return min(p[0],q[0])-tol<=r[0]<=max(p[0],q[0])+tol and min(p[1],q[1])-tol<=r[1]<=max(p[1],q[1])+tol
    o1,o2,o3,o4=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
    if o1*o2<0 and o3*o4<0: return True
    return any(abs(o)<=tol and on(p,q,r) for o,p,q,r in ((o1,a,b,c),(o2,a,b,d),(o3,c,d,a),(o4,c,d,b)))


def inside(p,ring):
    hit=False
    for a,b in zip(ring,ring[1:]+ring[:1]):
        if (a[1]>p[1]) != (b[1]>p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]: hit=not hit
    return hit


def sample_entity(es,name,chord_tolerance_mm,max_vertices):
    e=es[name]
    if e['kind']=='line_segment2d': return list(line(es,name)),0.
    c,r=curve(es,name)
    sweep=360. if e['kind']=='circle2d' else e['sweep_seed']
    start=0. if e['kind']=='circle2d' else e['start_angle_seed']
    theta=4*math.asin(math.sqrt(min(1.,chord_tolerance_mm/r/2)))
    if theta<=0: raise SketchError('tessellation_budget','Tolerance below floating-point resolution')
    n=max(3 if e['kind']=='circle2d' else 1,math.ceil(abs(math.radians(sweep))/min(theta,math.pi/2)))
    if n+1>max_vertices: raise SketchError('tessellation_budget','Chord tolerance requires too many vertices')
    pts=[arc_point(es,name,start+sweep*i/n) for i in range(n+1)]
    if e['kind']=='circle2d': pts[-1]=pts[0]
    return pts,2*r*math.sin(abs(math.radians(sweep))/n/4)**2


def entity_intersections(es,na,nb,tol=1e-9):
    """Analytic intersections on finite lines/arcs, overlap is rejected."""
    ea,eb=es[na],es[nb]; la=ea['kind']=='line_segment2d'; lb=eb['kind']=='line_segment2d'
    if la and lb:
        a,b=line(es,na); c,d=line(es,nb); u,v=sub(b,a),sub(d,c); den=cross(u,v)
        if abs(den)<=tol:
            if abs(cross(sub(c,a),u))>tol: return []
            ts=sorted([dot(sub(c,a),u)/dot(u,u),dot(sub(d,a),u)/dot(u,u)])
            lo,hi=max(0.,ts[0]),min(1.,ts[1])
            if hi-lo>tol: raise SketchError('self_intersection','Overlapping collinear segments')
            return [add(a,mul(u,(lo+hi)/2))] if hi>=lo-tol else []
        t=cross(sub(c,a),v)/den; s=cross(sub(c,a),u)/den
        return [add(a,mul(u,t))] if -tol<=t<=1+tol and -tol<=s<=1+tol else []
    if not la and lb: return entity_intersections(es,nb,na,tol)
    if la:
        a,b=line(es,na); c,r=curve(es,nb); f,t,_=foot(c,a,b); h2=r*r-dist(f,c)**2
        if h2 < -tol: return []
        delta=math.sqrt(max(0.,h2))/dist(a,b)
        return [p for x in {t-delta,t+delta} if -tol<=x<=1+tol for p in [add(a,mul(sub(b,a),x))] if in_sweep(es,nb,p,tol)]
    a,r=curve(es,na); b,s=curve(es,nb); d=dist(a,b)
    if d<=tol:
        if abs(r-s)<=tol:
            if ea['kind']=='circle2d' or eb['kind']=='circle2d':raise SketchError('self_intersection','Coincident circular boundaries overlap')
            def intervals(e):
                lo=(e['start_angle_seed']+min(0.,e['sweep_seed']))%360
                hi=lo+abs(e['sweep_seed'])
                return [(lo,min(360.,hi))]+([(0.,hi-360)] if hi>360 else [])
            for x,y in intervals(ea):
                for z,w in intervals(eb):
                    if min(y,w)-max(x,z)>math.degrees(tol/r):raise SketchError('self_intersection','Coincident arcs overlap')
            endpoints=[arc_point(es,na,ea['start_angle_seed']),arc_point(es,na,ea['start_angle_seed']+ea['sweep_seed'])]
            return [p for p in endpoints if in_sweep(es,nb,p,tol)]
        return []
    if d>r+s+tol or d<abs(r-s)-tol: return []
    x=(r*r-s*s+d*d)/(2*d); h=math.sqrt(max(0.,r*r-x*x)); u=mul(sub(b,a),1/d); f=add(a,mul(u,x)); v=(-u[1],u[0])
    return [p for sign in ({1} if h<=tol else {-1,1}) for p in [add(f,mul(v,h*sign))] if in_sweep(es,na,p,tol) and in_sweep(es,nb,p,tol)]


def check_polygon_intersections(loops,max_comparisons=8_000_000,budget_check=None):
    """AABB sweep reduces ordinary curve checks without an unbounded worst case."""
    events=[]
    for li,ring in enumerate(loops):
        for i,(a,b) in enumerate(zip(ring,ring[1:]+ring[:1])):
            events.append((min(a[0],b[0]),max(a[0],b[0]),min(a[1],b[1]),max(a[1],b[1]),li,i,a,b))
    events.sort(key=lambda e:(e[0],e[2],e[4],e[5]));active=[];comparisons=0
    for current in events:
        xmin,xmax,ymin,ymax,li,i,a,b=current
        active=[e for e in active if e[1]>=xmin-1e-9]
        for other in active:
            comparisons+=1
            if budget_check and comparisons%1024==0:budget_check()
            if comparisons>max_comparisons:raise SketchError('verification_budget','Polygon candidate-comparison budget exceeded')
            _,_,y0,y1,lj,j,c,d=other
            if y1<ymin-1e-9 or ymax<y0-1e-9:continue
            if li==lj and (abs(i-j)==1 or {i,j}=={0,len(loops[li])-1}):continue
            if segments_intersect(a,b,c,d):raise SketchError('self_intersection' if li==lj else 'boundary_touch','Sampled boundaries intersect or touch')
        active.append(current)
    return comparisons


def tessellate_profile(sketch,profile,chord_tolerance_mm=.01,max_vertices=200000,closure_tolerance_mm=.001,budget_check=None):
    if chord_tolerance_mm<=0 or max_vertices<3: raise SketchError('invalid_budget','Positive chord budget required')
    es=entity_map(sketch); paths=[profile['outer']]+profile.get('holes',[]); loops=[]; observed=0.; total=0
    if any(not p for p in paths): raise SketchError('open_profile','Empty boundary')
    used=[name for path in paths for name in path]
    if len(used)!=len(set(used)): raise SketchError('ambiguous_profile','Repeated boundary entity')
    for path in paths:
        if budget_check:budget_check()
        pts=[]
        for idx,name in enumerate(path):
            if budget_check:budget_check()
            if es[name]['kind']=='circle2d' and len(path)!=1: raise SketchError('ambiguous_profile','A circle must occupy its own loop')
            arr,error=sample_entity(es,name,chord_tolerance_mm,max_vertices-total)
            observed=max(observed,error)
            if pts:
                if dist(pts[-1],arr[0])<=closure_tolerance_mm: pass
                elif dist(pts[-1],arr[-1])<=closure_tolerance_mm: arr.reverse()
                else: raise SketchError('open_profile','Successive boundary entities do not join')
                pts.extend(arr[1:])
            else: pts.extend(arr)
        if dist(pts[0],pts[-1])>closure_tolerance_mm: raise SketchError('open_profile','Boundary does not close')
        pts=pts[:-1]
        if len(pts)<3 or abs(signed_area(pts))<=closure_tolerance_mm**2: raise SketchError('degenerate_profile','Boundary has no area')
        total+=len(pts)
        if total>max_vertices: raise SketchError('tessellation_budget','Profile exceeds vertex budget')
        loops.append(pts)
    # Analytic boundary check independent of sampling density.
    flat=[(li,ei,name) for li,p in enumerate(paths) for ei,name in enumerate(p)]
    for ix,(li,ei,a) in enumerate(flat):
        if budget_check:budget_check()
        for lj,ej,b in flat[ix+1:]:
            intersections=entity_intersections(es,a,b,closure_tolerance_mm*1e-3)
            adjacent=li==lj and (abs(ei-ej)==1 or {ei,ej}=={0,len(paths[li])-1})
            if intersections:
                if not adjacent: raise SketchError('self_intersection','Nonadjacent boundaries intersect or touch')
                endpoints=[]
                for n in (a,b):
                    e=es[n]
                    if e['kind']=='line_segment2d': endpoints.append(line(es,n))
                    elif e['kind']=='arc2d': endpoints.append((arc_point(es,n,e['start_angle_seed']),arc_point(es,n,e['start_angle_seed']+e['sweep_seed'])))
                    else: endpoints.append(())
                if any(not (any(dist(p,q)<=closure_tolerance_mm for q in endpoints[0]) and any(dist(p,q)<=closure_tolerance_mm for q in endpoints[1])) for p in intersections):
                    raise SketchError('self_intersection','Adjacent curves intersect away from their shared endpoints')
    # The combined polygon sweep catches chord approximation artifacts between
    # otherwise disjoint analytic boundaries. Candidate work is explicitly capped.
    comparisons=check_polygon_intersections(loops,budget_check=budget_check)
    for li,ring in enumerate(loops):
        if li and not inside(ring[0],loops[0]):raise SketchError('hole_outside','Hole not inside outer boundary')
        for lj,other in enumerate(loops[:li]):
            if lj and (inside(ring[0],other) or inside(other[0],ring)):raise SketchError('nested_holes','Nested holes require distinct profiles')
    oriented=[ring if (signed_area(ring)>0)==(i==0) else list(reversed(ring)) for i,ring in enumerate(loops)]
    return {'status':'pass','loops':oriented,'coordinate_unit':'mm','chord_tolerance_mm':chord_tolerance_mm,
            'max_chord_error_mm':observed,'vertex_count':total,'nesting_depths':[0]+[1]*(len(loops)-1),
            'analytic_intersections':'pass','polygon_candidate_comparisons':comparisons,'profile_id':profile['id']}


def branch_signature(sketch,profiles=None):
    es=entity_map(sketch); signs={}
    for p in sketch.get('profiles',[]):
        integral=0.;first=None;previous=None
        for name in p['outer']:
            e=es[name];kind=e['kind']
            if kind=='line_segment2d':
                start,end=line(es,name);term=cross(start,end)/2
            else:
                ctr,r=curve(es,name);start_angle=0. if kind=='circle2d' else e['start_angle_seed'];sweep=360. if kind=='circle2d' else e['sweep_seed']
                start=arc_point(es,name,start_angle);end=arc_point(es,name,start_angle+sweep)
                a,b=math.radians(start_angle),math.radians(start_angle+sweep)
                term=(r*ctr[0]*(math.sin(b)-math.sin(a))-r*ctr[1]*(math.cos(b)-math.cos(a))+r*r*math.radians(sweep))/2
            if previous is not None and dist(previous,end)<dist(previous,start):start,end,term=end,start,-term
            if first is None:first=start
            if previous is not None:integral+=cross(previous,start)/2
            integral+=term;previous=end
        if first is not None:
            integral+=cross(previous,first)/2
            signs[p['id']]=1 if integral>1e-10 else -1 if integral< -1e-10 else 0
    arcs={n:(1 if e['sweep_seed']>0 else -1) for n,e in es.items() if e['kind']=='arc2d'}
    # Every ordered point triple provides an orientation signature up to bounded IR size.
    triples={}
    declared=sketch.get('branch_checks',[])
    for check in declared:
        a,b,c=[point(es,x) for x in check['points']]; value=cross(sub(b,a),sub(c,a))
        triples[check['id']]=1 if value>1e-10 else -1 if value< -1e-10 else 0
    # Distance triangles have mirror branches. Do not label every arbitrary
    # seed-side relation as design intent: a perturbed collinear seed may
    # legitimately converge to its declared horizontal/vertical constraint.
    edges={frozenset((c['from'],c['to'])) for c in sketch.get('constraints',[]) if c['type']=='distance' and c.get('mode','driving')=='driving'}
    automatic={};points=sorted(n for n,e in es.items() if e['kind']=='point2d')
    for i,a in enumerate(points):
        for j,b in enumerate(points[i+1:],i+1):
            if frozenset((a,b)) not in edges:continue
            for c in points[j+1:]:
                if frozenset((a,c)) not in edges or frozenset((b,c)) not in edges:continue
                value=cross(sub(point(es,b),point(es,a)),sub(point(es,c),point(es,a)))
                automatic[a+'|'+b+'|'+c]=1 if value>1e-10 else -1 if value< -1e-10 else 0
    return {'profile_winding':signs,'arc_sweep':arcs,'oriented_triples':triples,'point_sides':automatic}


def check_branch(before,after):
    changed=[]
    for category,items in before.items():
        for key,value in items.items():
            new=after.get(category,{}).get(key)
            if value==0 or new==0 or value!=new: changed.append(category+':'+key)
    return {'status':'pass' if not changed else 'branch_changed','changed':changed}
