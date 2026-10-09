"""Strict, dependency-free authority for Hard Surface Workbench manifest 1.0.

JSON schema, describe and runtime checking consume the same schema objects.
Semantic checks are additional to structural JSON Schema checks. Never imports bpy.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath, Path

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 16
MAX_NODES = 100000
ID_PATTERN = r'^[A-Za-z][A-Za-z0-9_.:-]{0,95}$'
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
SHA_PATTERN = r'^[0-9a-f]{64}$'
LENGTH_SCALES = {'m': 1.0, 'mm': .001, 'cm': .01, 'in': .0254}
ANGLE_SCALES = {'rad': 1.0, 'deg': math.pi / 180}
CANONICAL_VERSION = 'HS_CANONICAL_JSON_V1'

class ContractError(ValueError):
    def __init__(self, code, message, path='$', details=None):
        super().__init__(f'{code} at {path}: {message}')
        self.code, self.message, self.path, self.details = code, message, path, details or {}
    def as_dict(self):
        return {'code':self.code,'message':self.message,'path':self.path,'details':self.details}

def _fail(message, path='$', code='INVALID_REQUEST', details=None):
    raise ContractError(code, message, path, details)

def string(**kw): return {'type':'string','maxLength':4096,**kw}
def number(**kw): return {'type':'number',**kw}
def integer(**kw): return {'type':'integer',**kw}
def array(items, minimum=0, maximum=256, **kw): return {'type':'array','items':items,'minItems':minimum,'maxItems':maximum,**kw}
def obj(properties, required=None, **kw):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False,**kw}
def enum(*values, **kw): return {'type':'string','enum':list(values),**kw}
def const(value): return {'const':value}
def union(*variants): return {'oneOf':list(variants)}
def optional_default(node,value): return {**node,'default':value}
IDENT = string(pattern=ID_PATTERN,maxLength=96)
UUID = string(pattern=UUID_PATTERN,maxLength=36)
SHA = string(pattern=SHA_PATTERN,maxLength=64)
BOOL = {'type':'boolean'}
V2 = array(number(),2,2)
V3 = array(number(),3,3)
DIMREF = obj({'kind':const('dimension_ref'),'id':IDENT})
LENGTH = union(number(),DIMREF)
POS_LENGTH = union(number(exclusiveMinimum=0),DIMREF)
ANGLE = union(number(),DIMREF)
FILE = obj({'file':string(minLength=1),'expected_sha256':SHA,'bytes':integer(minimum=0)},['file','expected_sha256'])
OBJECT_REF = obj({'kind':const('object_ref'),'object_id':UUID,'data_id':UUID,'revision':integer(minimum=1),'type':const('MESH')},['kind','object_id'])
STEP_REF = obj({'kind':const('step_output'),'step_id':IDENT,'port':IDENT})
FEATURE_REF = obj({'kind':const('feature_ref'),'feature_id':IDENT,'port':IDENT})
PROFILE_REF = obj({'kind':const('sketch_profile'),'sketch_id':IDENT,'profile_id':IDENT})
TARGET_REF = union(OBJECT_REF,STEP_REF,FEATURE_REF)
ANY_PROFILE = union(PROFILE_REF,STEP_REF,FEATURE_REF)
SELECTION = union(
    obj({'kind':const('feature_role'),'feature_step':IDENT,'role':enum('outer_boundary_edges','all_edges','all_faces')}),
    obj({'kind':const('index_selection'),'object_id':UUID,'data_id':UUID,'domain':enum('VERTEX','EDGE','FACE'),'indices':array(integer(minimum=0),1,200000,uniqueItems=True),'topology_sha256':SHA,'geometry_sha256':SHA,'evaluation_sha256':SHA,'position_dependent':optional_default(BOOL,False)},['kind','object_id','data_id','domain','indices','topology_sha256']),
    obj({'kind':const('attribute_selection'),'object_id':UUID,'data_id':UUID,'name':string(minLength=1,maxLength=63),'domain':enum('VERTEX','EDGE','FACE'),'type':enum('INT','BOOLEAN'),'value':union(integer(),BOOL),'expected_count':integer(minimum=1,maximum=200000),'topology_sha256':SHA}),
)

def entity(kind,fields):
    return obj({'id':IDENT,'kind':const(kind),**fields,'construction':optional_default(BOOL,False)},['id','kind',*fields])
ENTITY = union(
    entity('point2d',{'seed':V2}),
    entity('line_segment2d',{'start':IDENT,'end':IDENT}),
    entity('circle2d',{'center':IDENT,'radius_seed':number(exclusiveMinimum=0)}),
    entity('arc2d',{'center':IDENT,'radius_seed':number(exclusiveMinimum=0),'start_angle_seed':number(),'sweep_seed':number(exclusiveMinimum=-360,exclusiveMaximum=360)}),
)

def constraint(kind, fields, required=None):
    return obj({'id':IDENT,'type':const(kind),**fields,'mode':optional_default(enum('driving','reference'),'driving'),'requirement_id':IDENT}, ['id','type',*(fields if required is None else required)])
CONSTRAINT = union(
    constraint('point_fixed2d',{'point':IDENT,'at':V2}),
    constraint('coincident',{'a':IDENT,'b':IDENT}),
    constraint('arc_endpoint_coincident',{'arc':IDENT,'endpoint':enum('start','end'),'point':IDENT}),
    *(constraint(k,{'line':IDENT}) for k in ('horizontal','vertical')),
    *(constraint(k,{'a':IDENT,'b':IDENT}) for k in ('parallel','perpendicular','equal_length','concentric','equal_radius')),
    constraint('axis_distance2d',{'from':IDENT,'to':IDENT,'axis':enum('X','Y'),'dimension':IDENT}),
    constraint('distance',{'from':IDENT,'to':IDENT,'dimension':IDENT}),
    *(constraint(k,{'circle':IDENT,'dimension':IDENT}) for k in ('radius','diameter')),
    constraint('point_line_distance',{'point':IDENT,'line':IDENT,'dimension':IDENT,'side':enum('left','right')}),
    constraint('angle',{'a':IDENT,'b':IDENT,'dimension':IDENT,'directed':optional_default(BOOL,True)},['a','b','dimension']),
    *(constraint(k,{'arc':IDENT,'dimension':IDENT}) for k in ('arc_start_angle','arc_sweep')),
    constraint('equal_spacing',{'points':array(IDENT,3,128,uniqueItems=True),'axis':enum('X','Y'),'signed':const(True)}),
    constraint('tangent_line_circle',{'line':IDENT,'circle':IDENT,'domain':enum('segment','infinite'),'side':enum('left','right')}),
    constraint('tangent_line_arc',{'line':IDENT,'arc':IDENT,'domain':enum('segment','infinite'),'side':enum('left','right')}),
    *(constraint(k,{'a':IDENT,'b':IDENT,'branch':enum('external','internal'),'internal_outer':IDENT},['a','b','branch']) for k in ('tangent_circle_circle','tangent_arc_arc')),
)
DIMENSION = obj({'id':IDENT,'role':enum('driving','reference'),'quantity':enum('length','angle','scalar'),'value':number(),'unit':enum('m','mm','cm','in','deg','rad','1'),'minimum':number(),'maximum':number(),'locked':optional_default(BOOL,False),'requirement_id':IDENT},['id','role','quantity','value','unit'])
WORKPLANE = union(
    obj({'kind':const('datum'),'id':enum('world_xy','world_xz','world_yz')}),
    obj({'kind':const('frame'),'id':IDENT,'origin':V3,'x_axis':V3,'y_axis':V3}),
)
SOLVE = obj({
    'backend':optional_default(enum('slvs_3_2'),'slvs_3_2'),
    'underconstrained':optional_default(enum('reject'),'reject'),
    'redundant_solved':optional_default(enum('report_and_verify','reject'),'report_and_verify'),
    'branch_policy':optional_default(enum('preserve_declared'),'preserve_declared'),
    'verification':optional_default(obj({'length_tolerance_mm':number(exclusiveMinimum=0,maximum=1),'angle_tolerance_deg':number(exclusiveMinimum=0,maximum=1)}),{'length_tolerance_mm':.001,'angle_tolerance_deg':.001}),
    'max_attempts':optional_default(integer(minimum=1,maximum=3),1),
    'allowed_strategies':optional_default(array(enum('declared_seed','last_good_seed'),1,2,uniqueItems=True),['declared_seed']),
    'diagnostics':optional_default(obj({'when':enum('failure_or_redundancy','never'),'max_seconds':number(exclusiveMinimum=0,maximum=30)}),{'when':'failure_or_redundancy','max_seconds':5.0}),
},[])
PROFILE = obj({'id':IDENT,'outer':array(IDENT,1,128,uniqueItems=True),'holes':optional_default(array(array(IDENT,1,128,uniqueItems=True),0,32),[])},['id','outer'])
SKETCH = obj({'id':IDENT,'workplane':WORKPLANE,'entities':array(ENTITY,1,128),'constraints':array(CONSTRAINT,0,256),'profiles':array(PROFILE,0,32),'solve':optional_default(SOLVE,{}),'depends_on':optional_default(array(IDENT,0,32,uniqueItems=True),[])},['id','workplane','entities','constraints','profiles'])

# Operation registry is both contract authority and discoverable capability list.
def operation(name, fields, required, *, ports=('body_object',), reads=(), writes=(), topology=False):
    return {'schema':obj({'id':IDENT,'op':const(name),**fields,'depends_on':optional_default(array(IDENT,0,128,uniqueItems=True),[])},['id','op',*required]),'ports':list(ports),'reads':list(reads),'writes':list(writes),'changes_topology':topology}
MODE = optional_default(enum('modifier','mesh_apply'),'modifier')
OPERATIONS = {
    'sketch.solve':operation('sketch.solve',{'sketch_id':IDENT},['sketch_id'],ports=()),
    'profile.extrude':operation('profile.extrude',{'profile':ANY_PROFILE,'depth':POS_LENGTH,'mode':optional_default(enum('mesh_apply'),'mesh_apply'),'chord_tolerance':optional_default(number(exclusiveMinimum=0),.01)},['profile','depth'],topology=True),
    'profile.revolve':operation('profile.revolve',{'profile':ANY_PROFILE,'axis':enum('X','Y','Z'),'angle':optional_default(ANGLE,360.0),'segments':optional_default(integer(minimum=3,maximum=4096),64),'chord_tolerance':optional_default(number(exclusiveMinimum=0),.01)},['profile','axis'],topology=True),
    'primitive.box':operation('primitive.box',{'size':array(POS_LENGTH,3,3),'center':optional_default(V3,[0,0,0])},['size'],topology=True),
    'primitive.cylinder':operation('primitive.cylinder',{'radius':POS_LENGTH,'depth':POS_LENGTH,'segments':optional_default(integer(minimum=3,maximum=4096),64),'center':optional_default(V3,[0,0,0])},['radius','depth'],topology=True),
    'boolean.apply_or_stack':operation('boolean.apply_or_stack',{'target':TARGET_REF,'cutter':TARGET_REF,'operation':enum('DIFFERENCE','UNION','INTERSECT'),'solver':optional_default(enum('EXACT','MANIFOLD'),'EXACT'),'mode':MODE},['target','cutter','operation'],reads=('target','cutter'),writes=('target',),topology=True),
    'pattern.linear':operation('pattern.linear',{'target':TARGET_REF,'count':integer(minimum=2,maximum=128),'offset':array(LENGTH,3,3)},['target','count','offset'],ports=('body_object','instances'),reads=('target',),topology=True),
    'pattern.radial':operation('pattern.radial',{'target':TARGET_REF,'count':integer(minimum=2,maximum=128),'axis':enum('X','Y','Z'),'angle':optional_default(ANGLE,360.0),'center':optional_default(V3,[0,0,0])},['target','count','axis'],ports=('body_object','instances'),reads=('target',),topology=True),
    'shell.solidify':operation('shell.solidify',{'target':TARGET_REF,'thickness':POS_LENGTH,'offset':optional_default(number(minimum=-1,maximum=1),-1.0),'mode':MODE},['target','thickness'],reads=('target',),writes=('target',),topology=True),
    'edge.bevel':operation('edge.bevel',{'target':TARGET_REF,'width':POS_LENGTH,'segments':optional_default(integer(minimum=1,maximum=32),3),'mode':MODE,'selection':SELECTION,'clamp_overlap':optional_default(const(False),False)},['target','width'],reads=('target',),writes=('target',),topology=True),
    'normal.finish':operation('normal.finish',{'target':TARGET_REF,'method':enum('smooth_by_angle','flat'),'angle':optional_default(number(minimum=0,maximum=180),30.0)},['target','method'],reads=('target',),writes=('target',)),
    'object.transform':operation('object.transform',{'target':TARGET_REF,'translation':optional_default(V3,[0,0,0]),'rotation':optional_default(V3,[0,0,0]),'scale':optional_default(V3,[1,1,1]),'space':optional_default(enum('WORLD','LOCAL'),'WORLD')},['target'],reads=('target',),writes=('target',)),
    'measure':operation('measure',{'target':TARGET_REF,'checks':array(enum('dimensions','closed_mesh','volume','bounds','normals'),1,5,uniqueItems=True)},['target','checks'],ports=('measurement',),reads=('target',)),
    'checkpoint':operation('checkpoint',{'label':string(maxLength=128)},[],ports=('checkpoint',)),
}
# Structured operations construct topology from parameter domains, never by
# triangulating Boolean output. Physical values use the declared mm source unit.
QUAD_SAMPLING = {
    'chord_tolerance':optional_default(number(exclusiveMinimum=0,maximum=.05),.025),
    'target_edge_length':optional_default(number(exclusiveMinimum=0,maximum=10),4.0),
    'max_segments':optional_default(integer(minimum=12,maximum=512),512),
}
QUAD_COUNTERBORE = obj({'radius':POS_LENGTH,'depth':POS_LENGTH,'side':enum('top','bottom')})
QUAD_HOLE = union(
    obj({'id':IDENT,'kind':const('circle'),'center':array(LENGTH,2,2),'radius':POS_LENGTH,'counterbore':QUAD_COUNTERBORE},['id','kind','center','radius']),
    obj({'id':IDENT,'kind':const('rounded_rectangle'),'center':array(LENGTH,2,2),'size':array(POS_LENGTH,2,2),'radius':POS_LENGTH},['id','kind','center','size','radius']),
)
QUAD_LIP = obj({'id':IDENT,'bounds':array(LENGTH,4,4),'z_min':LENGTH})
QUAD_SEAT = obj({'id':IDENT,'bounds':array(LENGTH,4,4),'z_top':LENGTH,'hole_center':array(LENGTH,2,2),'hole_radius':POS_LENGTH,'hole_bottom':LENGTH})
QUAD_POST = obj({'id':IDENT,'center':array(LENGTH,2,2),'radius':POS_LENGTH,'z_top':LENGTH,'hole_radius':POS_LENGTH,'hole_top':LENGTH})
QUAD_OPENING = obj({'id':IDENT,'side':enum('left','right','back','front'),'center':array(LENGTH,2,2),'size':array(POS_LENGTH,2,2),'radius':POS_LENGTH})
OPERATIONS.update({
    'quad.panel':operation('quad.panel',{
        'size':array(POS_LENGTH,2,2),'center':optional_default(array(LENGTH,2,2),[0,0]),
        'corner_radius':POS_LENGTH,'z_min':LENGTH,'z_max':LENGTH,
        'topology_strategy':optional_default(enum('tiled','sparse_annulus','local_patch_blocks','subd_control_cage','sparse_control_cage',description='Legacy routes remain explicit compatibility paths. sparse_annulus target_edge_length controls straight-outline and wall-height sampling; radial spans use bounded minimal angle/aspect-qualified layouts. local_patch_blocks keeps its explicit fixed local_patch_bounds frame. sparse_control_cage is the versioned low-density single-hole closed-body route; source-cage review is separate from evaluated shape qualification.'),'tiled'),
        'edit_datum':enum('not_requested','fixed_bottom','fixed_midplane',description='Explicit semantic-rebuild thickness datum. Omitted means no thickness edit; this field never authorizes changing approved design.'),
        'subdivision_cage':obj({'method':optional_default(enum('CATMULL_CLARK'),'CATMULL_CLARK'),'hole_segments':optional_default(integer(minimum=24,maximum=24),24),'preview_levels':optional_default(integer(minimum=0,maximum=3),2),'bore_support_width':number(exclusiveMinimum=0),'outer_join_policy':enum('joined_arc_v1','joined_arc_v2')},[]),
        'sparse_cage':obj({'hole_segments':integer(minimum=4,maximum=128),'outer_segments':integer(minimum=8,maximum=256),'hole_planar_support':BOOL,'profile_arc_segments':integer(minimum=2,maximum=8),'hole_cage_radius_factor':number(exclusiveMinimum=0),'outer_support_fraction':number(exclusiveMinimum=0,exclusiveMaximum=1),'wall_support_fraction':number(exclusiveMinimum=0,exclusiveMaximum=1),'collar_radius_factor':number(exclusiveMinimum=1),'layout':obj({'schema':enum('fixed-frame-axis-aligned/1.0','fixed-frame-axis-aligned/1.1'),'feature_frame_mm':array(number(),4,4,description='Frozen local-mm collar frame [xmin,ymin,xmax,ymax]; hole edits do not move this frame or the macrogrid.'),'corner_guard_mm':number(exclusiveMinimum=0,description='Frozen spacing from core corner to the adjacent macrogrid guard slot.')}),'preview_levels':integer(minimum=0,maximum=3),'insertion_policy':enum('axis_plane_v1',description='Explicit bounded whole-body axis-plane insertion policy; requires a frozen fixed-frame layout and allows at most two append-only insertion declarations.'),'insertions':array(obj({'corridor':enum('east','south'),'fraction':number(minimum=.2,maximum=.8)}),0,2)},[]),
        'local_patch_bounds':array(LENGTH,4,4,description='Fixed caller-owned [xmin,ymin,xmax,ymax] in mm, required by local_patch_blocks; preserve across hole-position edits.'),
        'holes':optional_default(array(QUAD_HOLE,0,32),[]),
        'lips':optional_default(array(QUAD_LIP,0,16),[]),
        'edge_bevel':optional_default(union(number(minimum=0,maximum=2),DIMREF),0.0),**QUAD_SAMPLING,
    },['size','corner_radius','z_min','z_max'],topology=True),
    'quad.shell':operation('quad.shell',{
        'size':array(POS_LENGTH,3,3),'center':optional_default(array(LENGTH,2,2),[0,0]),
        'z_min':optional_default(LENGTH,0),'corner_radius':POS_LENGTH,'wall_thickness':POS_LENGTH,'base_thickness':POS_LENGTH,
        'corner_seats':array(QUAD_SEAT,4,4),'posts':optional_default(array(QUAD_POST,0,16),[]),
        'openings':optional_default(array(QUAD_OPENING,0,32),[]),
        'edge_bevel':optional_default(number(minimum=0,maximum=1),0.0),**QUAD_SAMPLING,
    },['size','corner_radius','wall_thickness','base_thickness','corner_seats'],topology=True),
    'quad.fastener':operation('quad.fastener',{
        'center':array(LENGTH,2,2),'shaft_radius':POS_LENGTH,'head_radius':POS_LENGTH,
        'head_height':POS_LENGTH,'shaft_length':POS_LENGTH,'shoulder_z':LENGTH,
        'direction':{'type':'integer','enum':[-1,1]},
        'socket_flat_width':optional_default(number(minimum=0),0.0),
        'socket_depth':optional_default(number(minimum=0),0.0),**QUAD_SAMPLING,
    },['center','shaft_radius','head_radius','head_height','shaft_length','shoulder_z','direction'],topology=True),
})
_subd_cfg=OPERATIONS['quad.panel']['schema']['properties']['subdivision_cage']
_subd_cfg['if']={'required':['outer_join_policy']}
_subd_cfg['then']={'required':['bore_support_width']}

# Opt-in only: an absent insertion policy retains the one-insertion legacy
# envelope. Exported schemas and runtime validation use this same conditional.
_sparse_cfg=OPERATIONS['quad.panel']['schema']['properties']['sparse_cage']
_sparse_cfg['properties']['corner_columns']=obj({
    'schema':const('corner-columns-midpoint/1.0'),
},description='Explicit midpoint corner-column constructor; requires a fixed-frame layout and axis_plane_v1. No fitting or optimization controls are accepted.')
_sparse_cfg['if']={'properties':{'insertion_policy':const('axis_plane_v1')},'required':['insertion_policy']}
_sparse_cfg['then']={'required':['layout']}
_sparse_cfg['else']={'properties':{'insertions':array(obj({'corridor':enum('east','south'),'fraction':number(minimum=.2,maximum=.8)}),0,1)}}
_sparse_cfg['allOf']=[{
    'if':{'required':['corner_columns']},
    'then':{'required':['layout','insertion_policy'],'properties':{
        'profile_arc_segments':integer(minimum=4,maximum=4),
    }},
}]

# Conditional field requirement is part of the exported schema and runtime
# contract; legacy panel modes retain their prior required fields.
OPERATIONS['quad.panel']['schema']['if']={'properties':{'topology_strategy':enum('local_patch_blocks','subd_control_cage')},'required':['topology_strategy']}
OPERATIONS['quad.panel']['schema']['then']={'required':['local_patch_bounds']}
# Property constraints are a second bounded conditional; non-SubD routes retain
# the previous <=1 mm envelope and cannot accept the new independent bore band.
OPERATIONS['quad.panel']['schema']['allOf']=[{
    'if':{'properties':{'topology_strategy':enum('subd_control_cage','sparse_control_cage')},'required':['topology_strategy']},
    'then':{'properties':{'edge_bevel':union(number(minimum=0,maximum=2),DIMREF)}},
    'else':{'properties':{
        'edge_bevel':union(number(minimum=0,maximum=1),DIMREF),
        'subdivision_cage':obj({'method':optional_default(enum('CATMULL_CLARK'),'CATMULL_CLARK'),'hole_segments':optional_default(integer(minimum=24,maximum=24),24),'preview_levels':optional_default(integer(minimum=0,maximum=3),2)},[]),
    }},
}, {
    'if':{'required':['sparse_cage'],'properties':{
        'sparse_cage':{'required':['corner_columns']},
    }},
    'then':{'required':['topology_strategy'],'properties':{
        'topology_strategy':const('sparse_control_cage'),
    }},
}]

STEP = union(*(v['schema'] for v in OPERATIONS.values()))
RECIPE_PARAMETERS = union(
    obj({'depth':POS_LENGTH,'bevel_width':POS_LENGTH,'editable':optional_default(BOOL,True)},['depth','bevel_width']),
    obj({'size':array(POS_LENGTH,3,3),'bevel_width':POS_LENGTH},['size']),
    obj({'radius':POS_LENGTH,'depth':POS_LENGTH,'inner_radius':POS_LENGTH,'segments':optional_default(integer(minimum=3,maximum=4096),64)},['radius','depth']),
    obj({'size':array(POS_LENGTH,3,3),'hole_radius':POS_LENGTH,'hole_count':integer(minimum=1,maximum=32),'hole_spacing':POS_LENGTH,'hole_origin':V3,'segments':optional_default(integer(minimum=3,maximum=4096),32)},['size','hole_radius','hole_count','hole_spacing','hole_origin']),
    obj({'target':TARGET_REF,'hole_radius':POS_LENGTH,'hole_depth':POS_LENGTH,'hole_count':integer(minimum=1,maximum=32),'hole_spacing':POS_LENGTH,'hole_origin':V3,'segments':optional_default(integer(minimum=3,maximum=4096),32)},['target','hole_radius','hole_depth','hole_count','hole_spacing','hole_origin']),
)
PROGRAM = union(
    obj({'kind':const('steps'),'steps':array(STEP,1,64)}),
    obj({'kind':const('recipe'),'recipe_id':enum('profile_extrude_edge_finish','plate_box','cylindrical_spacer','perforated_panel','linear_hole_pattern'),'recipe_version':const('1.0.0'),'inputs':obj({'profile':ANY_PROFILE},[]),'parameters':RECIPE_PARAMETERS}),
)
FEATURE = obj({'id':IDENT,'program':PROGRAM,'depends_on':optional_default(array(IDENT,0,128,uniqueItems=True),[]),'requirement_id':IDENT},['id','program'])
STATE = obj({'revision':integer(minimum=1),'dimensions':array(DIMENSION,0,512),'sketches':array(SKETCH,0,32),'features':array(FEATURE,1,128)})
PATCH = union(
    obj({'op':const('set_dimension'),'id':IDENT,'value':number(),'unit':enum('m','mm','cm','in','deg','rad','1')},['op','id','value']),
    obj({'op':const('replace_constraint'),'sketch_id':IDENT,'constraint':CONSTRAINT}),
    obj({'op':const('add_entity'),'sketch_id':IDENT,'entity':ENTITY}),
    obj({'op':const('add_feature'),'feature':FEATURE}),
    obj({'op':const('insert_sparse_strip'),'feature_id':IDENT,'step_id':IDENT,'expected_feature_sha256':SHA,'corridor':enum('east','south'),'fraction':number(minimum=.2,maximum=.8)}),
    obj({'op':const('add_sketch'),'sketch':SKETCH}),
)
DESIGN = union(
    obj({'mode':const('create_if_absent'),'expected_state':const('absent'),'state':STATE}),
    obj({'mode':const('use_saved'),'expected_revision':integer(minimum=1)}),
    obj({'mode':const('patch_if_revision'),'expected_revision':integer(minimum=1),'state_sha256':SHA,'patches':array(PATCH,1,128)}),
)
WIRE_STYLE = obj({'enabled':optional_default(BOOL,True),'mesh_state':optional_default(enum('control','evaluated'),'evaluated'),'line_width_px':optional_default(number(minimum=1,maximum=2),1.25),'max_edges':optional_default(integer(minimum=1,maximum=100000),100000)},[])
RUN_WIRE = obj({**WIRE_STYLE['properties'],'component_views':optional_default(BOOL,True),'width':optional_default(integer(minimum=64,maximum=2048),480),'height':optional_default(integer(minimum=64,maximum=2048),360)},[])
CHECKS = enum('constraint_residuals','profile_validity','design_dimensions','closed_mesh','source_preserved','reopen','preservation','dependencies','normals','volume','reference_consistency','quad_topology','structure_identity')
REQUEST = obj({'schema_version':const('1.0'),'command':const('hardsurface.run'),'params':obj({
    'manifest_version':const('1.0'),'request_id':string(minLength=1,maxLength=128,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),'purpose':enum('contract_fixture','production'),
    'source':union(obj({'kind':const('new_scene'),'project_id':IDENT,'length_unit':enum(*LENGTH_SCALES),'up_axis':const('Z')}),obj({'kind':const('saved_blend'),'file':string(minLength=1),'expected_sha256':SHA,'bytes':integer(minimum=1),'length_unit':enum(*LENGTH_SCALES)})),
    'context':obj({'scene':string(minLength=1,maxLength=63),'view_layer':string(minLength=1,maxLength=63),'frame':integer(minimum=-1048574,maximum=1048574),'evaluation':enum('RENDER','VIEWPORT')}),
    'resources':array(obj({'id':IDENT,'kind':enum('image','library','font','cache','other'),'file':string(minLength=1),'expected_sha256':SHA,'bytes':integer(minimum=0)}),0,128),
    'design':DESIGN,
    'protection':obj({'source_write':const('forbidden'),'shared_data':enum('reject_shared','make_single_user'),'manual_edits':const('preserve_supported_nonconflicting'),'on_conflict':const('pause'),'non_target_object_ids':optional_default(array(UUID,0,128,uniqueItems=True),[])},['source_write','shared_data','manual_edits','on_conflict']),
    'work_units':array(obj({'id':IDENT,'feature_ids':array(IDENT,1,128,uniqueItems=True),'checks':array(CHECKS,1,16,uniqueItems=True),'failure_policy':enum('stop_job'),'depends_on':optional_default(array(IDENT,0,8,uniqueItems=True),[])},['id','feature_ids','checks','failure_policy']),1,8),
    'quality':obj({'profile':IDENT,'stage':enum('source_cage','full'),'required':array(CHECKS,1,16,uniqueItems=True),'visual':enum('not_applicable_fixture_only','required'),'user_feedback':enum('not_applicable_fixture_only','required','not_run'),'length_tolerance':optional_default(number(exclusiveMinimum=0),.01),'angle_tolerance_deg':optional_default(number(exclusiveMinimum=0,maximum=1),.001)},['profile','required','visual','user_feedback']),
    'budgets':obj({'max_total_steps':integer(minimum=1,maximum=128),'max_geometry_vertices':integer(minimum=8,maximum=2000000),'max_geometry_loops':integer(minimum=24,maximum=10000000),'wall_seconds':number(exclusiveMinimum=0,maximum=3600),'cpu_threads':integer(minimum=1,maximum=8),'max_instances':optional_default(integer(minimum=1,maximum=128),128),'max_total_attempts':optional_default(integer(minimum=1,maximum=96),32),'max_tessellation_vertices':optional_default(integer(minimum=8,maximum=200000),200000),'max_preview_samples':optional_default(integer(minimum=1,maximum=1000000000),100000000),'max_observed_rss_bytes':optional_default(integer(minimum=67108864),2147483648),'max_artifact_bytes':optional_default(integer(minimum=1048576),1073741824)},['max_total_steps','max_geometry_vertices','max_geometry_loops','wall_seconds','cpu_threads']),
    'output':obj({'publication':const('candidate_only'),'report':enum('compact','full'),'editability':optional_default(enum('preserve_modifiers','apply_declared'),'preserve_modifiers')},['publication','report']),
    'wire':optional_default(RUN_WIRE,{}),
    'preview':obj({'engine':const('CYCLES'),'device':const('CPU'),'width':integer(minimum=64,maximum=2048),'height':integer(minimum=64,maximum=2048),'samples':integer(minimum=1,maximum=256),'views':array(enum('front','top','right','three_quarter','back','bottom'),1,6,uniqueItems=True)}),
    'reference_package':obj({'version':string(minLength=1,maxLength=32),'manifest':FILE,'requirements':array(IDENT,1,256,uniqueItems=True),'review_views':array(IDENT,1,16,uniqueItems=True)}),
    'recovery':obj({'checkpoint':FILE,'compatibility':const('exact')}),
    'execution':optional_default(obj({'max_attempts':optional_default(integer(minimum=1,maximum=3),1),'technical_strategies':optional_default(array(enum('declared','boolean_exact','boolean_manifold','tessellation_refine'),1,3,uniqueItems=True),['declared'])},[]),{}),
},['manifest_version','request_id','purpose','source','context','resources','design','protection','work_units','quality','budgets','output'])})


def _walk_limits(value, depth=0, counter=None):
    if counter is None: counter=[0]
    counter[0]+=1
    if depth>MAX_DEPTH or (isinstance(value,(dict,list)) and depth>=MAX_DEPTH): _fail('JSON nesting exceeds limit',code='LIMIT_EXCEEDED')
    if counter[0]>MAX_NODES: _fail('JSON node count exceeds limit',code='LIMIT_EXCEEDED')
    if isinstance(value,str):
        try: value.encode('utf-8',errors='strict')
        except UnicodeError: _fail('Unpaired Unicode surrogate')
    elif isinstance(value,(int,float)) and not isinstance(value,bool):
        try: finite=math.isfinite(value)
        except (OverflowError,ValueError): finite=False
        if not finite: _fail('Non-finite or unrepresentable number')
    elif isinstance(value,dict):
        for key,val in value.items():
            if not isinstance(key,str): _fail('Object keys must be strings')
            _walk_limits(key,depth,counter); _walk_limits(val,depth+1,counter)
    elif isinstance(value,list):
        for item in value: _walk_limits(item,depth+1,counter)
    elif value is not None and not isinstance(value,(int,float,bool)): _fail(f'Non-JSON type {type(value).__name__}')

def strict_loads(data):
    if not isinstance(data,(str,bytes,bytearray)): _fail('JSON input must be UTF-8 bytes or text')
    try:
        if not isinstance(data,str): data=bytes(data).decode('utf-8',errors='strict')
        raw=data.encode('utf-8',errors='strict')
    except UnicodeError: _fail('Invalid UTF-8 or Unicode')
    if len(raw)>MAX_BYTES: _fail('Request exceeds 2 MiB',code='LIMIT_EXCEEDED')
    # Check nesting before json.loads to avoid unbounded parser recursion.
    depth=0; in_string=False; escaped=False
    for ch in data:
        if in_string:
            if escaped: escaped=False
            elif ch=='\\': escaped=True
            elif ch=='"': in_string=False
        elif ch=='"': in_string=True
        elif ch in '[{':
            depth+=1
            if depth>MAX_DEPTH: _fail('JSON nesting exceeds limit',code='LIMIT_EXCEEDED')
        elif ch in ']}': depth-=1
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: _fail(f'Duplicate JSON key: {key}')
            result[key]=value
        return result
    def reject(token): _fail(f'Non-finite JSON token: {token}')
    try: value=json.loads(data,object_pairs_hook=pairs,parse_constant=reject)
    except (json.JSONDecodeError,RecursionError,ValueError) as exc: _fail(f'Malformed JSON: {exc}')
    _walk_limits(value)
    return value


def _matches_condition(value,node):
    """Match the registered bounded schema predicates, including nested presence."""
    if 'const' in node and value!=node['const']:return False
    if 'enum' in node and value not in node['enum']:return False
    # JSON Schema required/properties predicates apply only to object values.
    if not isinstance(value,dict):return True
    return (all(k in value for k in node.get('required',())) and
            all(k not in value or _matches_condition(value[k],spec)
                for k,spec in node.get('properties',{}).items()))


def _validate(value,node,path='$'):
    if 'oneOf' in node:
        # Fast discrimination and useful nested paths rather than hiding field errors.
        variants=node['oneOf']
        if isinstance(value,dict):
            for discriminator in ('op','kind','mode','type'):
                if discriminator in value:
                    selected=[v for v in variants if v.get('properties',{}).get(discriminator,{}).get('const',object())==value[discriminator]]
                    if len(selected)==1: return _validate(value,selected[0],path)
        valid=[]
        for choice in variants:
            try: valid.append(_validate(value,choice,path))
            except ContractError: pass
        if len(valid)!=1: _fail('Value must match exactly one registered variant',path)
        return valid[0]
    if 'const' in node:
        c=node['const']
        if type(value) is not type(c) or value!=c: _fail(f'Expected constant {c!r}',path)
        return value
    typ=node.get('type')
    if typ=='object':
        if not isinstance(value,dict): _fail('Expected object',path)
        properties=node['properties']; extra=set(value)-set(properties)
        if extra: _fail(f'Unknown fields: {sorted(extra)}',path)
        missing=set(node.get('required',()))-set(value)
        if missing: _fail(f'Missing fields: {sorted(missing)}',path)
        # Registered if/then/else/allOf conditionals apply required fields and
        # property subcontracts identically to the exported JSON Schema.
        conditionals=([node] if 'if' in node else [])+node.get('allOf',[])
        for conditional in conditionals:
            condition=conditional.get('if')
            if not isinstance(condition,dict):_fail('Invalid registered conditional',path,code='INTERNAL_ERROR')
            matched=_matches_condition(value,condition)
            branch=conditional.get('then' if matched else 'else',{})
            conditional_missing=set(branch.get('required',()))-set(value)
            if conditional_missing:_fail(f'Missing conditional fields: {sorted(conditional_missing)}',path)
            for key,spec in branch.get('properties',{}).items():
                if key in value:_validate(value[key],spec,path+'.'+key)
        result={}
        for key,spec in properties.items():
            if key in value: result[key]=_validate(value[key],spec,path+'.'+key)
            elif 'default' in spec: result[key]=_validate(copy.deepcopy(spec['default']),spec,path+'.'+key)
        return result
    if typ=='array':
        if not isinstance(value,list): _fail('Expected array',path)
        if not node.get('minItems',0)<=len(value)<=node.get('maxItems',100000): _fail('Array length outside bounds',path)
        if node.get('uniqueItems') and len({canonical_bytes(v) for v in value})!=len(value): _fail('Duplicate array elements',path)
        return [_validate(v,node['items'],f'{path}[{i}]') for i,v in enumerate(value)]
    if typ=='string':
        if not isinstance(value,str): _fail('Expected string',path)
        if not node.get('minLength',0)<=len(value)<=node.get('maxLength',4096): _fail('String length outside bounds',path)
        if 'pattern' in node and not re.fullmatch(node['pattern'],value): _fail('String does not match required format',path)
        if 'enum' in node and value not in node['enum']: _fail(f'Expected one of {node["enum"]}',path)
        return value
    if typ in ('integer','number'):
        if isinstance(value,bool) or not isinstance(value,(int,float)): _fail('Expected number, not boolean',path)
        try: finite=math.isfinite(value)
        except (OverflowError,ValueError): finite=False
        if not finite: _fail('Non-finite or unrepresentable number',path)
        if typ=='integer' and (isinstance(value,float) and not value.is_integer()): _fail('Expected an integral number',path)
        for key,check in [('minimum',lambda x,y:x>=y),('maximum',lambda x,y:x<=y),('exclusiveMinimum',lambda x,y:x>y),('exclusiveMaximum',lambda x,y:x<y)]:
            if key in node and not check(value,node[key]): _fail(f'Number violates {key}={node[key]}',path)
        return int(value) if typ=='integer' else (0.0 if value==0 else float(value))
    if typ=='boolean':
        if type(value) is not bool: _fail('Expected boolean',path)
        return value
    _fail('Invalid internal schema',path,code='INTERNAL_ERROR')


def canonical_bytes(value):
    _walk_limits(value)
    def clean(v):
        if isinstance(v,float): return 0.0 if v==0 else v
        if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
        if isinstance(v,list): return [clean(x) for x in v]
        return v
    return json.dumps(clean(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def fingerprint(value): return hashlib.sha256(canonical_bytes(value)).hexdigest()
def content_fingerprint(request):
    r=normalize_request(request); del r['params']['request_id']; return fingerprint(r)


def _unique(items,path):
    result={}
    for item in items:
        if item['id'] in result: _fail(f'Duplicate ID: {item["id"]}',path,code='DUPLICATE_ID')
        result[item['id']]=item
    return result

def _dag(graph,path):
    visiting=set(); visited=set(); order=[]
    def visit(key):
        if key in visiting: _fail(f'Dependency cycle at {key}',path,code='DEPENDENCY_CYCLE')
        if key in visited: return
        if key not in graph: _fail(f'Unknown dependency {key}',path)
        visiting.add(key)
        for dep in sorted(graph[key]): visit(dep)
        visiting.remove(key); visited.add(key); order.append(key)
    for key in graph: visit(key)
    return order


def validate_state(state):
    _walk_limits(state); result=_validate(state,STATE,'$.design.state')
    dims=_unique(result['dimensions'],'$.design.state.dimensions')
    for d in dims.values():
        units={'length':LENGTH_SCALES,'angle':ANGLE_SCALES,'scalar':{'1':1}}
        if d['unit'] not in units[d['quantity']]: _fail('Dimension unit and quantity disagree')
        if 'minimum' in d and d['value']<d['minimum']: _fail(f'Dimension {d["id"]} below minimum')
        if 'maximum' in d and d['value']>d['maximum']: _fail(f'Dimension {d["id"]} above maximum')
        if d.get('minimum',-math.inf)>d.get('maximum',math.inf): _fail('Dimension minimum exceeds maximum')
    sketches=_unique(result['sketches'],'$.design.state.sketches')
    _dag({k:s['depends_on'] for k,s in sketches.items()},'$.design.state.sketches')
    for s in sketches.values():
        entities=_unique(s['entities'],f'$.sketches.{s["id"]}.entities')
        _unique(s['constraints'],f'$.sketches.{s["id"]}.constraints'); _unique(s['profiles'],f'$.sketches.{s["id"]}.profiles')
        def ref(eid,kinds):
            if eid not in entities or entities[eid]['kind'] not in kinds: _fail(f'Sketch {s["id"]}: reference {eid} must be {kinds}')
        points=('point2d',); lines=('line_segment2d',); curves=('circle2d','arc2d')
        for e in entities.values():
            if e['kind']=='line_segment2d':
                ref(e['start'],points); ref(e['end'],points)
                if e['start']==e['end']: _fail('Line endpoints must differ')
            if e['kind'] in curves: ref(e['center'],points)
            if e['kind']=='arc2d' and e['sweep_seed']==0: _fail('Arc sweep cannot be zero')
        for c in s['constraints']:
            typ=c['type']
            fields={}
            if typ=='point_fixed2d': fields={'point':points}
            elif typ=='coincident': fields={'a':points,'b':points}
            elif typ=='arc_endpoint_coincident': fields={'arc':('arc2d',),'point':points}
            elif typ in ('horizontal','vertical'): fields={'line':lines}
            elif typ in ('parallel','perpendicular','equal_length','angle'): fields={'a':lines,'b':lines}
            elif typ in ('concentric','equal_radius'): fields={'a':curves,'b':curves}
            elif typ in ('axis_distance2d','distance'): fields={'from':points,'to':points}
            elif typ in ('radius','diameter'): fields={'circle':curves}
            elif typ=='point_line_distance': fields={'point':points,'line':lines}
            elif typ in ('tangent_line_circle','tangent_line_arc'): fields={'line':lines, 'circle' if typ.endswith('circle') else 'arc':('circle2d',) if typ.endswith('circle') else ('arc2d',)}
            elif typ in ('tangent_circle_circle','tangent_arc_arc'):
                kinds=('circle2d',) if typ.endswith('circle') else ('arc2d',); fields={'a':kinds,'b':kinds}
                if c['branch']=='internal' and c.get('internal_outer') not in (c['a'],c['b']): _fail('Internal tangency needs explicit outer curve ID')
                if c['branch']=='external' and 'internal_outer' in c: _fail('External tangency forbids internal_outer')
            elif typ in ('arc_start_angle','arc_sweep'): fields={'arc':('arc2d',)}
            elif typ=='equal_spacing':
                for eid in c['points']: ref(eid,points)
            for field,kinds in fields.items(): ref(c[field],kinds)
            if 'a' in c and c['a']==c['b']: _fail('Constraint operands must differ')
            if 'dimension' in c:
                d=dims.get(c['dimension'])
                if d is None: _fail(f'Unknown dimension {c["dimension"]}')
                if d['quantity']!=('angle' if typ in ('angle','arc_start_angle','arc_sweep') else 'length'): _fail('Constraint dimension quantity mismatch')
                if c['mode']=='driving' and d['role']!='driving': _fail('Reference dimension cannot drive a constraint')
        for p in s['profiles']:
            used=[]
            for loop in [p['outer'],*p['holes']]:
                for eid in loop:
                    ref(eid,('line_segment2d','circle2d','arc2d'))
                    if entities[eid].get('construction'): _fail('Construction entity cannot form profile')
                if any(entities[e]['kind']=='circle2d' for e in loop) and len(loop)!=1: _fail('A circular loop must contain only that circle')
                used.extend(loop)
            if len(set(used))!=len(used): _fail('Profile loops reuse an entity')
        if s['workplane']['kind']=='frame':
            x,y=s['workplane']['x_axis'],s['workplane']['y_axis']
            if abs(sum(v*v for v in x)-1)>1e-9 or abs(sum(v*v for v in y)-1)>1e-9 or abs(sum(a*b for a,b in zip(x,y)))>1e-9: _fail('Frame axes must be orthonormal and right-handed by x cross y')
        if s['solve']['max_attempts']>len(s['solve']['allowed_strategies']): _fail('max_attempts exceeds declared solver strategies')
    feats=_unique(result['features'],'$.design.state.features')
    _dag({k:f['depends_on'] for k,f in feats.items()},'$.design.state.features')
    for f in feats.values():
        if f['program']['kind']=='steps': _unique(f['program']['steps'],f'$.features.{f["id"]}.steps')
    return result


def _paths(value,path='$'):
    if isinstance(value,dict):
        for key,item in value.items():
            if key=='file':
                p=PurePosixPath(item)
                if not p.is_absolute() or '..' in p.parts or '\\' in item or '\x00' in item or '://' in item: _fail('File paths must be absolute POSIX paths without traversal',path+'.file')
            _paths(item,path+'.'+key)
    elif isinstance(value,list):
        for i,item in enumerate(value): _paths(item,f'{path}[{i}]')

def validate_request(request):
    if isinstance(request,(str,bytes,bytearray)): request=strict_loads(request)
    _walk_limits(request)
    if len(canonical_bytes(request))>MAX_BYTES: _fail('Request exceeds 2 MiB',code='LIMIT_EXCEEDED')
    result=_validate(request,REQUEST); p=result['params']; _paths(result)
    _unique(p['resources'],'$.params.resources'); _unique(p['work_units'],'$.params.work_units')
    _dag({u['id']:u['depends_on'] for u in p['work_units']},'$.params.work_units')
    if p['purpose']=='production':
        if 'reference_package' not in p: _fail('Production requires a frozen reference package',code='REFERENCE_REQUIRED')
        if p['quality']['visual']!='required' or p['quality']['user_feedback']=='not_applicable_fixture_only': _fail('Production cannot waive reference/visual acceptance as fixture')
    else:
        if p['quality']['visual']!='not_applicable_fixture_only' or p['quality']['user_feedback']!='not_applicable_fixture_only': _fail('Contract fixtures cannot claim production visual/user acceptance')
    if p['execution']['max_attempts']>len(p['execution']['technical_strategies']): _fail('max_attempts exceeds declared technical strategies')
    if p['source']['kind']=='new_scene' and p['design']['mode']!='create_if_absent': _fail('A new scene requires create_if_absent design')
    if p['design']['mode']=='create_if_absent':
        p['design']['state']=validate_state(p['design']['state'])
        if p['design']['state']['revision']!=1: _fail('New design initial revision must equal 1')
    return result
normalize_request=validate_request


def resolve_design(design_command,saved_state=None):
    command=_validate(design_command,DESIGN,'$.design')
    if command['mode']=='create_if_absent':
        if saved_state is not None: _fail('Design state already exists',code='DESIGN_STATE_EXISTS')
        state=validate_state(command['state'])
        if state['revision']!=1: _fail('New design revision must equal 1')
        return state
    if saved_state is None: _fail('Saved design state is missing',code='DESIGN_STATE_MISSING')
    state=validate_state(saved_state)
    if command['expected_revision']!=state['revision']: _fail('Saved design revision differs',code='REVISION_CONFLICT')
    if command['mode']=='use_saved': return state
    if command['state_sha256']!=fingerprint(state): _fail('Saved design fingerprint differs',code='REVISION_CONFLICT')
    seen=set()
    for patch in command['patches']:
        op=patch['op']
        key=(op,patch.get('id',patch.get('sketch_id')),patch.get('constraint',{}).get('id',patch.get('entity',{}).get('id',patch.get('feature',{}).get('id',patch.get('sketch',{}).get('id')))))
        if key in seen: _fail('Duplicate patch target')
        seen.add(key)
        if op=='set_dimension':
            d=next((d for d in state['dimensions'] if d['id']==patch['id']),None)
            if d is None: _fail('Unknown dimension patch target')
            if d.get('locked'): _fail('Locked dimension cannot be patched',code='DESIGN_LOCKED')
            if d['role']!='driving': _fail('Reference dimensions are measured, not patched')
            d['value']=patch['value']
            if 'unit' in patch and patch['unit']!=d['unit']:
                scales=LENGTH_SCALES if d['quantity']=='length' else ANGLE_SCALES if d['quantity']=='angle' else {'1':1.0}
                if patch['unit'] not in scales: _fail('Dimension patch unit and quantity disagree')
                ratio=scales[d['unit']]/scales[patch['unit']]
                for bound in ('minimum','maximum'):
                    if bound in d: d[bound]*=ratio
                d['unit']=patch['unit']
        elif op in ('replace_constraint','add_entity'):
            sketch=next((s for s in state['sketches'] if s['id']==patch['sketch_id']),None)
            if sketch is None: _fail('Unknown sketch patch target')
            if op=='add_entity': sketch['entities'].append(patch['entity'])
            else:
                idx=next((i for i,c in enumerate(sketch['constraints']) if c['id']==patch['constraint']['id']),None)
                if idx is None: _fail('replace_constraint target does not exist')
                sketch['constraints'][idx]=patch['constraint']
        elif op=='add_feature': state['features'].append(patch['feature'])
        elif op=='insert_sparse_strip':
            feature=next((f for f in state['features'] if f['id']==patch['feature_id']),None)
            if feature is None or fingerprint(feature)!=patch['expected_feature_sha256']:
                _fail('Sparse insertion feature identity is stale',code='REVISION_CONFLICT')
            program=feature['program']
            if program['kind']!='steps':_fail('Sparse insertion requires an explicit existing step program')
            step=next((s for s in program['steps'] if s['id']==patch['step_id']),None)
            if step is None or step.get('op')!='quad.panel' or step.get('topology_strategy')!='sparse_control_cage':
                _fail('Sparse insertion cannot migrate a different constructor',code='SPARSE_MIGRATION_REQUIRED')
            cfg=step.setdefault('sparse_cage',{})
            previous=cfg.get('insertions',[])
            declaration={'corridor':patch['corridor'],'fraction':patch['fraction']}
            if cfg.get('insertion_policy')=='axis_plane_v1':
                if len(previous)>=2:
                    _fail('The axis-plane policy supports at most two append-only insertions',code='SPARSE_INSERTION_DOMAIN')
                if declaration in previous:
                    _fail('An axis-plane declaration cannot duplicate an existing insertion',code='SPARSE_INSERTION_DOMAIN')
                cfg['insertions']=[*previous,declaration]
            else:
                if previous:_fail('Only one insertion from the base sparse schedule is supported',code='SPARSE_INSERTION_DOMAIN')
                cfg['insertions']=[declaration]
        elif op=='add_sketch': state['sketches'].append(patch['sketch'])
    state['revision']+=1
    return validate_state(state)


def dimension_values(state,normalized=True):
    result={}
    for d in state['dimensions']:
        scale=(LENGTH_SCALES if d['quantity']=='length' else ANGLE_SCALES if d['quantity']=='angle' else {'1':1})[d['unit']]
        result[d['id']]=float(d['value'])*(scale if normalized else 1)
    return result

def schema(section='request'):
    if section=='research-evidence':
        from .research_evidence import schema as research_schema
        return research_schema()
    registry={'report':REPORT,'case':obj({'id':IDENT,'strategy':enum('declared','boolean_exact','boolean_manifold','tessellation_refine')}),'request':REQUEST,'state':STATE,'step':STEP,'sketch':SKETCH,'constraint':CONSTRAINT,'selection':SELECTION,'design':DESIGN}
    if section in OPERATIONS: selected=OPERATIONS[section]['schema']
    elif section in registry: selected=registry[section]
    else: _fail(f'Unknown schema section {section}')
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','title':f'Hard Surface Workbench {section} 1.0',**copy.deepcopy(selected)}

def describe(section=None):
    if section: return schema(section)
    return {'manifest_version':'1.0','canonical_version':CANONICAL_VERSION,'operations':{name:{k:v for k,v in spec.items() if k!='schema'} for name,spec in OPERATIONS.items()},'limits':{'request_bytes':MAX_BYTES,'json_depth':MAX_DEPTH,'json_nodes':MAX_NODES},'units':{'length':list(LENGTH_SCALES),'angle':list(ANGLE_SCALES)},'capability_boundary':'Schema support is not runtime qualification; production requires externally verified reference approval.'}

def export_schemas(directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True); paths=[]
    for name in ('report','case','request','state','step','sketch','constraint','selection','design','research-evidence',*OPERATIONS):
        path=directory/(name+'.schema.json'); path.write_text(json.dumps(schema(name),ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8'); paths.append(str(path))
    return paths


def validate_sketch(sketch,dimensions=None):
    """Validate one standalone sketch before solver import.

    Optional dimensions are full Dimension records. Without them, temporary typed
    dimensions are inferred solely to validate reference kinds; their zero values
    are never solver input or design state. Cross-sketch dependencies require
    validate_state and are deliberately rejected by this standalone boundary.
    """
    s=_validate(sketch,SKETCH,'$.sketch')
    _walk_limits(s)
    if dimensions is None:
        inferred={}
        for con in s['constraints']:
            if 'dimension' not in con: continue
            q='angle' if con['type'] in ('angle','arc_start_angle','arc_sweep') else 'length'
            old=inferred.get(con['dimension'])
            if old and old['quantity']!=q: _fail('One dimension used with incompatible quantities')
            inferred[con['dimension']]={'id':con['dimension'],'role':'driving','quantity':q,'value':0,'unit':'rad' if q=='angle' else 'm'}
        dimensions=list(inferred.values())
    state={'revision':1,'dimensions':dimensions,'sketches':[s],'features':[{'id':'validation_only','program':{'kind':'steps','steps':[{'id':'solve','op':'sketch.solve','sketch_id':s['id']}]}}]}
    return validate_state(state)['sketches'][0]

# Compact receipts are references to bounded complete evidence, not arbitrary JSON.
OUTPUT_FILE = obj({'file':string(minLength=1),'sha256':SHA,'bytes':integer(minimum=0)})
ACCEPTANCE_RESULT = obj({'status':enum('pass','fail','not_run','not_applicable'),'reason':string(),'evidence':OUTPUT_FILE},['status'])
GUARD_SUMMARY = obj({'status':enum('pass','fail','unknown','not_applicable'),'accepted':union(BOOL,const(None)),'files_count':integer(minimum=0,maximum=256),'guard_type':string(maxLength=128)},['status','accepted','files_count'])
REPORT = obj({
    'report_version':const('1.0'),'job_id':string(minLength=1,maxLength=128),'request_id':string(minLength=1,maxLength=128),
    'status':enum('succeeded','failed','cancelled','timed_out'),'domain_outcome':enum('pass','partial','failed'),
    'candidate':union(obj({'file':string(minLength=1),'sha256':SHA,'bytes':integer(minimum=0),'state':enum('verified_candidate','usable_delivery')}),const(None)),
    'acceptance':obj({k:ACCEPTANCE_RESULT for k in ('technical','preservation','dependency_reproduction','performance','visual','user_feedback','method_acceptance')}),
    'counts':obj({k:integer(minimum=0,maximum=10000000) for k in ('steps','units_accepted','units_failed','units_blocked','steps_reused')},[]),
    'error':union(obj({'code':string(minLength=1,maxLength=128),'detail_code':string(maxLength=128),'message':string(),'retry_class':enum('never','new_input_required','retriable'),'details_reference':OUTPUT_FILE},['code','message','retry_class']),const(None)),
    'source_protection':obj({'original_source_observed':obj({'status':enum('observed','changed','unknown','not_applicable'),'written_by_this_tool':const(False),'continuous_immutability_proven':const(False)}),'staged_snapshot_guarded':GUARD_SUMMARY,'resources_guarded':GUARD_SUMMARY,'evidence':OUTPUT_FILE}),
    'checkpoints':array(obj({'checkpoint_id':IDENT,'state':const('accepted_checkpoint'),'receipt':OUTPUT_FILE}),0,16),
    'topology_diagnostics':obj({'status':enum('pass','disabled_by_request','failed','not_run'),'mesh_state':enum('control','evaluated'),'components':integer(minimum=0,maximum=128),'statistics':OUTPUT_FILE,'views':array(OUTPUT_FILE,0,16)},['status','mesh_state','components','views']),
    'qualification_scope':obj({'stage':const('source_cage'),'source_structure':const('pass'),'evaluated_shape':const('not_run'),'surface_observation':const('not_run'),'production_qualification':const('not_run')}),
    'report':OUTPUT_FILE,'next_step':string(),'warnings':array(obj({'code':string(maxLength=128),'message':string()}),0,16),
    'details_omitted':BOOL,'details_reference':OUTPUT_FILE,
},['report_version','job_id','request_id','status','domain_outcome','candidate','acceptance','counts','error','source_protection','checkpoints','report','next_step','warnings'])

def validate_report(report):
    _walk_limits(report)
    result=_validate(report,REPORT,'$.report')
    _paths(result)
    if result['status']=='succeeded' and result['domain_outcome']=='failed': _fail('Succeeded receipt cannot claim failed domain',code='REPORT_INCONSISTENT')
    if 'qualification_scope' in result:
        if (result['status']!='succeeded' or result['domain_outcome']!='partial'
                or result['candidate'] is None or result['candidate']['state']!='verified_candidate'
                or result['acceptance']['performance']['status']!='pass'
                or any(result['acceptance'][key]['status']=='pass' for key in ('visual','user_feedback','method_acceptance'))):
            _fail('Source-cage receipts must remain partial verified candidates without visual, user or production qualification',code='REPORT_INCONSISTENT')
    if result['domain_outcome']=='pass' or 'qualification_scope' in result:
        if result['status']!='succeeded' or result['candidate'] is None or result['error'] is not None: _fail('Passing report requires successful candidate and no error',code='REPORT_INCONSISTENT')
        for key in ('technical','preservation','dependency_reproduction'):
            if result['acceptance'][key]['status']!='pass': _fail('Passing report lacks required acceptance',code='REPORT_INCONSISTENT')
    if result['domain_outcome']=='pass' or 'qualification_scope' in result:
        source=result['source_protection']
        if source['original_source_observed']['status'] not in ('observed','not_applicable'): _fail('Passing report has unknown or changed original source',code='REPORT_INCONSISTENT')
        for key in ('staged_snapshot_guarded','resources_guarded'):
            if source[key]['status'] not in ('pass','not_applicable'): _fail('Passing report has unaccepted source/resource guard',code='REPORT_INCONSISTENT')
            if 'qualification_scope' in result and source[key]['accepted'] is not True:
                _fail('Source-cage scope requires explicitly accepted guards',code='REPORT_INCONSISTENT')
    for key,entry in result['acceptance'].items():
        if entry['status']=='pass' and not entry.get('evidence'): _fail(f'Pass needs evidence: {key}',code='REPORT_INCONSISTENT')
        if entry['status']=='not_applicable' and not entry.get('reason'): _fail(f'Not applicable needs rationale: {key}',code='REPORT_INCONSISTENT')
    return result


def compact_report(report):
    """Make exact receipt; detail omission is explicit, errors never silently vanish.

    Full report must already have been saved and hashed. All nested detailed
    evidence remains available through that immutable descriptor.
    """
    evidence=_validate(report.get('report'),OUTPUT_FILE,'$.report')
    def cut(text,limit=4096):
        text=str(text)
        return text if len(text)<=limit else text[:limit-40]+' [truncated; see full report]'
    accepted={}
    for name in REPORT['properties']['acceptance']['properties']:
        raw=report.get('acceptance',{}).get(name,{'status':'not_run'})
        if isinstance(raw,str): raw={'status':raw}
        value={'status':raw.get('status','not_run')}
        if raw.get('reason'): value['reason']=cut(raw['reason'])
        if raw.get('evidence'):
            source=raw['evidence']
            if isinstance(source,dict) and set(source)=={'file','sha256','bytes'}: value['evidence']=source
            else:
                value['evidence']=evidence
                if not value.get('reason'): value['reason']=cut(source)
        if value['status']=='pass' and 'evidence' not in value: _fail(f'Full report pass lacks evidence: {name}',code='REPORT_INCONSISTENT')
        accepted[name]=value
    source=report.get('source_protection',{})
    original=source.get('original_source_observed',{})
    os_status='not_applicable' if original.get('status')=='not_applicable' else 'observed' if original else 'unknown'
    def guard(raw):
        if not isinstance(raw,dict): raw={}
        files=raw.get('files',[]); n=len(files)
        accepted=raw.get('accepted')
        status=('pass' if n else 'not_applicable') if accepted is True else 'fail' if accepted is False else 'unknown'
        value={'status':status,'accepted':accepted if type(accepted) is bool else None,'files_count':n}
        if raw.get('guard_type'): value['guard_type']=cut(raw['guard_type'],128)
        return value
    err=report.get('error')
    if err:
        err={'code':cut(err.get('code','UNKNOWN'),128),'detail_code':cut(err.get('detail_code',err.get('code','UNKNOWN')),128),'message':cut(err.get('message','Unknown error')),'retry_class':err.get('retry_class','new_input_required'),'details_reference':evidence}
    else: err=None
    warnings=[]
    for warning in report.get('warnings',[])[:16]:
        if isinstance(warning,dict): warnings.append({'code':cut(warning.get('code','WARNING'),128),'message':cut(warning.get('message',warning))})
        else: warnings.append({'code':'WARNING','message':cut(warning)})
    checkpoints=[{'checkpoint_id':c['checkpoint_id'],'state':c['state'],'receipt':c['receipt']} for c in report.get('checkpoints',[])[-16:] if c.get('state')=='accepted_checkpoint' and c.get('receipt')]
    result={'report_version':'1.0','job_id':report['job_id'],'request_id':report['request_id'],'status':report['status'],'domain_outcome':report.get('domain_outcome','failed'),'candidate':report.get('candidate'),'acceptance':accepted,'counts':{k:v for k,v in report.get('counts',{}).items() if k in REPORT['properties']['counts']['properties']},'error':err,'source_protection':{'original_source_observed':{'status':os_status,'written_by_this_tool':False,'continuous_immutability_proven':False},'staged_snapshot_guarded':guard(source.get('staged_snapshot_guarded')),'resources_guarded':guard(source.get('resources_guarded')),'evidence':evidence},'checkpoints':checkpoints,'report':evidence,'next_step':cut(report.get('next_step','Inspect full report')),'warnings':warnings,'details_omitted':True,'details_reference':evidence}
    if 'topology_diagnostics' in report:result['topology_diagnostics']=report['topology_diagnostics']
    if 'qualification_scope' in report:result['qualification_scope']=report['qualification_scope']
    return validate_report(result)
