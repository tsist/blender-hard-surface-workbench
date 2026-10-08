"""Opt-in source-bound reflection rig. Domain math only; no Blender import.

Identity pins authenticate content, not user authority. Never writes normals,
geometry, material, camera, source files or approval state.
"""
from __future__ import annotations
import math
from . import contract as c
from .io import RuntimeFailure

HASH=c.string(pattern=r'^[a-f0-9]{64}$',minLength=64,maxLength=64)
OPTION=c.obj({
    'mode':c.const('surface_anchor_v1'),
    'source_sha256':HASH,'positions_topology_sha256':HASH,
    'native_normals_sharp_smooth_sha256':HASH,'matrix_world_sha256':HASH,
    'view_sha256':HASH,'object_id':c.UUID,
    'triangle_index':c.integer(minimum=0,maximum=7999999),
    'triangle_vertices':c.array(c.integer(minimum=0,maximum=999999),3,3),
    'triangle_loops':c.array(c.integer(minimum=0,maximum=3999999),3,3),
    'polygon_index':c.integer(minimum=0,maximum=3999999),
    'barycentric':c.array(c.number(minimum=0,maximum=1),3,3),
    'distance_mm':c.number(minimum=1,maximum=10000),
    'stripe_width_screen_mm':c.number(minimum=.01,maximum=1000),
    'stripe_length_screen_mm':c.number(minimum=.01,maximum=10000),
    'offsets_screen_mm':c.array(c.number(minimum=-1000,maximum=1000),3,3),
    'roi':c.array(c.number(minimum=0,maximum=1),4,4),
})

def fail(message,**details):raise RuntimeFailure('REFLECTION_ANCHOR',message,**details)
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def mul(a,k):return tuple(x*k for x in a)
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def unit(a):
    if len(a)!=3 or not all(math.isfinite(x) for x in a):fail('Nonfinite or invalid vector')
    length=math.sqrt(dot(a,a))
    if length<1e-12:fail('Degenerate vector')
    return mul(a,1/length)
def reflect(v,n):return sub(mul(n,2*dot(n,v)),v)
def view_fingerprint(view):return c.fingerprint({k:v for k,v in view.items() if k!='reflection_anchor'})
def validate_option(params,view):
    q=view.get('reflection_anchor')
    if q is None:return
    if params['diagnostic_preset']!='reflection_strips' or params.get('normal_policy')!='geometry_normals_v1':
        raise c.ContractError('INVALID_REQUEST','Reflection anchor requires reflection strips and geometry_normals_v1')
    if view.get('visible_object_ids')!=[q['object_id']] or 'camera' not in view or view['mesh_state']!='evaluated' or view['explode_z_mm']:
        raise c.ContractError('INVALID_REQUEST','Anchor requires one explicit object, evaluated state, explicit camera, and no explosion')
    if abs(sum(q['barycentric'])-1)>1e-10:raise c.ContractError('INVALID_REQUEST','Anchor barycentric weights must sum to one')
    x0,y0,x1,y1=q['roi']
    if x0>=x1 or y0>=y1:raise c.ContractError('INVALID_REQUEST','Anchor ROI must have positive image-normalized area')
    if len(set(q['offsets_screen_mm']))!=3:raise c.ContractError('INVALID_REQUEST','Anchor stripe offsets must be distinct')
    if q['source_sha256']!=params['source'].get('expected_sha256'):raise c.ContractError('INVALID_REQUEST','Anchor source pin must match request source pin')
    if q['view_sha256']!=view_fingerprint(view):raise c.ContractError('INVALID_REQUEST','Anchor view binding mismatch')

def require_identity(q,source_sha,record,matrix,view):
    actual={'source_sha256':source_sha,'positions_topology_sha256':record['positions_topology_sha256'],
            'native_normals_sharp_smooth_sha256':record['native_shading']['native_normals_sharp_smooth_sha256'],
            'matrix_world_sha256':c.fingerprint(matrix),'view_sha256':view_fingerprint(view)}
    for key,value in actual.items():
        if q[key]!=value:fail('Anchor identity mismatch',field=key,expected=q[key],actual=value)
    return actual

def plan(anchor,normal,v,right,up,q,angle):
    p=tuple(anchor);n=unit(normal);v=unit(v);right=unit(right);up=unit(up)
    if not all(math.isfinite(x) and abs(x)<=100000 for x in p):fail('Anchor point outside supported world bounds')
    if dot(n,v)<.05:fail('Anchor back-facing or grazing; choose a qualified location')
    if max(abs(dot(right,v)),abs(dot(up,v)),abs(dot(right,up)))>1e-5:fail('Camera basis is not orthonormal')
    t=math.radians(angle);short=add(mul(right,math.cos(t)),mul(up,math.sin(t)));long=add(mul(right,-math.sin(t)),mul(up,math.cos(t)))
    r=unit(reflect(v,n))
    def lift(e):return sub(e,mul(v,dot(n,e)/dot(n,v)))
    def onto(e):
        a=lift(e);return sub(a,mul(r,dot(r,a)))
    b=unit(onto(long));a=unit(cross(b,r))
    surface_a=sub(a,mul(r,dot(n,a)/dot(n,r)));scale=abs(dot(surface_a,short))
    if scale<1e-8:fail('Degenerate stripe projection')
    width=q['stripe_width_screen_mm']/scale
    length=math.sqrt(dot(onto(long),onto(long)))*(q['stripe_length_screen_mm']+abs(dot(surface_a,long))*width)
    if max(width,length)>20000:fail('Planned emitter dimensions exceed 20 metres')
    lights=[]
    for offset in q['offsets_screen_mm']:
        center=add(add(p,mul(r,q['distance_mm'])),mul(onto(short),offset))
        if not all(math.isfinite(x) and abs(x)<=100000 for x in center):fail('Planned emitter outside supported world bounds')
        lights.append({'position_mm':list(center),'outward_axis_world':list(r),'short_axis_world':list(a),'long_axis_world':list(b),
                       'size_mm':width,'size_y_mm':length,'offset_screen_mm':offset})
    return {'contract':'HS_REFLECTION_ANCHOR_V1','anchor_world_mm':list(p),'anchor_normal_world':list(n),
            'normal_semantics':'normalized HOST/native interpolation for ray math only; source corner normals untouched',
            'reflection_direction_world':list(r),'lights':lights,'surface_quality_inference':'none',
            'predicted_coverage_status':'not_assessed','observed_coverage_status':'not_run'}

def ray_rectangle(p,d,light):
    normal=light['outward_axis_world'];den=dot(d,normal)
    if den<=1e-10:return None
    t=dot(sub(light['position_mm'],p),normal)/den
    if t<=0:return None
    q=sub(add(p,mul(d,t)),light['position_mm'])
    if abs(dot(q,light['short_axis_world']))>light['size_mm']/2 or abs(dot(q,light['long_axis_world']))>light['size_y_mm']/2:return None
    return t

def barycentric(p,a,b,c_):
    u=sub(b,a);v=sub(c_,a);w=sub(p,a);uu=dot(u,u);uv=dot(u,v);vv=dot(v,v);wu=dot(w,u);wv=dot(w,v)
    den=uu*vv-uv*uv
    if den<=1e-30:fail('Degenerate native anchor triangle')
    s=(vv*wu-uv*wv)/den;t=(uu*wv-uv*wu)/den
    return (1-s-t,s,t)

def weighted(vectors,weights):return tuple(sum(v[i]*w for v,w in zip(vectors,weights)) for i in range(3))

def observed_summary(rgb_values,visible_count):
    """Bounded sampled PNG data; never a visual pass or full ROI certificate."""
    if not rgb_values or not visible_count:return {'status':'inconclusive','reason':'No visible ROI image samples','surface_quality_inference':'none'}
    values=sorted(.2126*v[0]+.7152*v[1]+.0722*v[2] for v in rgb_values)
    if len(values)!=visible_count or not all(math.isfinite(v) for v in values):fail('Invalid observed pixel samples')
    percentile=lambda f:values[min(len(values)-1,int((len(values)-1)*f))]
    span=max(values)-min(values)
    return {'status':'fail' if span<=1e-6 else 'inconclusive',
            'reason':'Numerically uniform sampled ROI cannot establish stripe contrast' if span<=1e-6 else 'Sampled contrast measured; spatial stripe traversals require image review',
            'samples':len(values),'sample_luminance_min':values[0],'sample_luminance_max':values[-1],
            'sample_luminance_p10':percentile(.1),'sample_luminance_p50':percentile(.5),'sample_luminance_p90':percentile(.9),
            'value_semantics':'Blender-decoded saved PNG RGB; display transform baked, then image decode; not raw radiance',
            'sampling_limit':'Fixed 32x32 ROI grid with native first-hit visibility; not complete pixel coverage',
            'surface_quality_inference':'none'}
