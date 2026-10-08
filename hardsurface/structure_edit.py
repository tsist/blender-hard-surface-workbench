# SPDX-License-Identifier: GPL-3.0-or-later
"""Predeclared, bounded semantic edits for the authored single-circle panel.

Planning inspects only the old witness and new parameters, before regeneration.
Verification consumes actual native witnesses. It does not infer identity, widen
scope from an observed diff, authorize an edit, or certify source preservation.
"""
from copy import deepcopy
import math
import struct

from . import structure_kernel as kernel
from . import subd_panel_identity as author
from .io import RuntimeFailure

PLAN_VERSION = '1.0'
SIGNATURES = ('geometry_signature', 'topology_signature', 'attribute_signature', 'structure_signature')
GROUPS = ('regions', 'loops', 'boundary_ports')
NATIVE_INVARIANTS = ('schema_version', 'object_id', 'data_id', 'mesh_state',
                     'schedule_revision', 'topology_epoch', 'context_sha256')


def _fail(code, message, **details):
    raise kernel.StructureError(code, message, **details)


def _author_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except RuntimeFailure as exc:
        _fail(exc.code, str(exc), **getattr(exc, 'details', {}))


def _mesh(witness, authorship):
    kernel._keys(witness, ('mesh', 'structure'), label='edit witness')
    mesh, structure = witness['mesh'], witness['structure']
    report = kernel.validate_structure(mesh, structure)
    if not isinstance(authorship, dict):
        _fail('STRUCTURE_IDENTITY_MISSING', 'An authored parameter and identity manifest is required')
    if any(authorship.get(name) != structure[name] for name in ('vertex_map', 'face_map')):
        _fail('AUTHORED_MAP_INVALID', 'Authorship maps must resolve the actual witness indices')
    supplied = {'vertices_mm': deepcopy(mesh['vertices']), 'faces': deepcopy(mesh['faces']),
                'edge_creases': deepcopy(mesh.get('edge_creases', [])),
                'authored_structure': deepcopy(authorship)}
    _author_call(author.validate_authored_identity, supplied)
    _parameter_domain(authorship['parameter_binding']['parameters'])
    return supplied, report


def _parameter_domain(p):
    """Bounded parameter checks without generating any vertices or faces."""
    if isinstance(p,dict) and p.get('topology_strategy')=='sparse_control_cage':
        from .sparse_panel_geometry import validate_parameter_domain
        return _author_call(validate_parameter_domain,p)
    try:
        if not isinstance(p, dict) or p.get('op') != 'quad.panel' or p.get('topology_strategy') != 'subd_control_cage':
            raise ValueError('Only quad.panel/subd_control_cage is supported')
        holes, cfg = p['holes'], p.get('subdivision_cage', {})
        if not isinstance(cfg,dict):raise ValueError('subdivision_cage must be an object')
        if 'outer_join_policy' in cfg and (cfg['outer_join_policy'] not in ('joined_arc_v1','joined_arc_v2') or 'bore_support_width' not in cfg):
            raise ValueError('Only explicit joined_arc_v1 or joined_arc_v2 with fixed bore support is supported')
        if not isinstance(holes, list) or len(holes) != 1 or holes[0]['kind'] != 'circle' or holes[0].get('counterbore') or p.get('lips'):
            raise ValueError('Exactly one sharp circular through-hole is required')
        if type(cfg.get('hole_segments', 24)) is not int or cfg.get('hole_segments', 24) != 24 or cfg.get('method', 'CATMULL_CLARK') != 'CATMULL_CLARK':
            raise ValueError('The circle24/outer48 Catmull-Clark schedule is fixed')
        if type(cfg.get('preview_levels', 2)) is not int or not 0 <= cfg.get('preview_levels', 2) <= 3:
            raise ValueError('Preview levels must remain in the supported range')
        w, h = p['size']; cx, cy = p.get('center', [0, 0])
        lo, hi, r, b = p['z_min'], p['z_max'], p['corner_radius'], p['edge_bevel']
        hx, hy = holes[0]['center']; hr = holes[0]['radius']
        x0, y0, x1, y1 = p['local_patch_bounds']
        for value in (w,h,cx,cy,lo,hi,r,b,hx,hy,hr,x0,y0,x1,y1):
            kernel._number(value, 'panel parameter')
        if not (w > 0 and h > 0 and 0 < b < min(r/4, (hi-lo)/3) and r < min(w,h)/2 and hr > 0):
            raise ValueError('Outline, hole, and thickness must remain in the bounded domain')
        if not (cx-w/2+r < x0 < x1 < cx+w/2-r and cy-h/2+r < y0 < y1 < cy+h/2-r):
            raise ValueError('The fixed hole frame must remain strictly inside the outline')
        if not .75 <= (x1-x0)/(y1-y0) <= 1.5:
            raise ValueError('The fixed frame aspect ratio is unsupported')
        if min(hx-hr-x0, x1-hx-hr, hy-hr-y0, y1-hy-hr) < max(.8, hr*.15):
            raise ValueError('Hole changes must retain the supported transition clearance')
        if 'bore_support_width' in cfg:
            width=kernel._number(cfg['bore_support_width'],'bore_support_width',True)
            if width>hr or hr*(3/(2+math.cos(math.tau/24)))+width>=min(hx-x0,x1-hx,hy-y0,y1-hy):
                raise ValueError('Explicit bore support exceeds the radius or fixed-frame clearance bound')
        # Reject NaN/non-JSON additions even in fields this version does not use.
        kernel.fingerprint(p)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        if isinstance(exc, kernel.StructureError):
            raise
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED', str(exc))


def _changes(old, new):
    rows = []
    for path, before, after in (
        ('holes[0].center', old['holes'][0]['center'], new['holes'][0]['center']),
        ('holes[0].radius', old['holes'][0]['radius'], new['holes'][0]['radius']),
        ('z_min', old['z_min'], new['z_min']),
        ('z_max', old['z_max'], new['z_max']),
        ('edge_bevel',old['edge_bevel'],new['edge_bevel'])):
        if before != after:
            rows.append({'parameter': path, 'before': deepcopy(before), 'after': deepcopy(after)})
    return rows


def _entity_closure(structure, vertex_ids, face_ids):
    """Resolve authored dependencies against the BEFORE semantic graph only."""
    vertices, faces = set(vertex_ids), set(face_ids)
    loops = {r['id'] for r in structure['loops'] if vertices.intersection(r['vertex_ids'])}
    regions = {r['id'] for r in structure['regions'] if faces.intersection(r['face_ids'])}
    ports = {r['id'] for r in structure['boundary_ports'] if r['loop_id'] in loops}
    return sorted(loops | regions | ports)


def _types(structure):
    result = {key: 'vertex' for key in structure['vertex_map']}
    result.update({key: 'face' for key in structure['face_map']})
    for group in GROUPS:
        result.update({r['id']: group for r in structure[group]})
    return result


def _hole_region_signatures(witness):
    """Actual nine-region hole witness, including each fixed-frame boundary.

    Membership is pinned to authored face addresses and independently checked
    against the adapter regions. Hashes use actual coordinates, oriented faces
    and actual per-edge crease values, never metadata's claimed geometry.
    """
    mesh,structure=witness['mesh'],witness['structure']
    vm,fm=structure['vertex_map'],structure['face_map'];inverse={i:key for key,i in vm.items()}
    if any(key.startswith('vertex:') for key in vm):
        expected={'holewall/bore_wall':['face:holewall/sector:%d'%i for i in range(16)]}
        for side in ('bottom','top'):
            expected[side+'/hole_to_field']=['face:%s/hole_to_field/sector:%d'%(side,i) for i in range(16)]
            ids=['face:%s/hole_planar_support/sector:%d'%(side,i) for i in range(16)]
            if any(key in fm for key in ids):expected[side+'/hole_planar_support']=ids
    else:
        expected={'bore_wall':['bore_wall/sector:%d'%i for i in range(24)]}
        for side in ('bottom','top'):
            prefix='plane/'+side+'/'
            expected[side+':bore_planar_support']=[prefix+'bore_support/sector:%d'%i for i in range(24)]
            expected[side+':bore_planar_buffer']=[prefix+'bore_buffer/sector:%d'%i for i in range(24)]
            expected[side+':planar_3_to_1']=[prefix+'reduce3/block:%d/quad:%s'%(i,q) for i in range(8) for q in ('a','b')]
            expected[side+':fixed_frame_buffer']=[prefix+'fixed_frame_buffer/sector:%d'%i for i in range(8)]
    crease={tuple(sorted((inverse[a],inverse[b]))):value for a,b,value in mesh.get('edge_creases',[])}
    rows=[]
    for role,face_ids in sorted(expected.items()):
        regions=[row for row in structure['regions'] if row['role']==role]
        if len(regions)!=1 or regions[0]['face_ids']!=sorted(face_ids):
            _fail('AUTHORED_HOLE_REGION_MISMATCH','Actual protected hole-region membership differs from the authored schedule',role=role)
        faces={key:author._canonical_cycle([inverse[i] for i in mesh['faces'][fm[key]]]) for key in sorted(face_ids)}
        vertices=sorted({key for face in faces.values() for key in face})
        edges=sorted({tuple(sorted((a,b))) for face in faces.values() for a,b in zip(face,face[1:]+face[:1])})
        rows.append({'region_id':regions[0]['id'],'role':role,'face_ids':sorted(face_ids),
                     'vertex_ids':vertices,'face_count':len(face_ids),'vertex_count':len(vertices),'edge_count':len(edges),
                     'geometry_sha256':kernel.fingerprint({key:list(mesh['vertices'][vm[key]]) for key in vertices}),
                     'connectivity_sha256':kernel.fingerprint(faces),
                     'crease_sha256':kernel.fingerprint([[*edge,crease.get(edge,0.)] for edge in edges])})
    return rows


def plan_authored_edit(before_witness, before_authorship, new_parameters, *, datum_policy):
    """Return a frozen-scope kernel contract BEFORE any new mesh is built.

    ``not_requested`` permits unchanged Z and hole-only edits. Thickness needs
    an explicit supported datum. This receipt records scope, not user approval.
    """
    from .sparse_native_edit import is_insertion,plan_insertion
    if is_insertion(before_authorship,new_parameters):
        return plan_insertion(before_witness,before_authorship,new_parameters,datum_policy=datum_policy)
    if datum_policy not in ('not_requested', 'fixed_bottom', 'fixed_midplane'):
        _fail('AUTHORED_EDIT_DATUM_REQUIRED', 'An explicit supported edit datum policy is required')
    if not isinstance(new_parameters, dict):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED', 'Complete new panel parameters are required')
    if new_parameters.get('edit_datum', datum_policy) != datum_policy:
        _fail('AUTHORED_EDIT_DATUM_MISMATCH', 'Parameter edit_datum contradicts the explicit edit policy')
    before, report = _mesh(before_witness, before_authorship)
    _verify_parameter_coordinates(before)
    _verify_geometric_records(before_witness)
    _parameter_domain(new_parameters)
    scope = _author_call(author.plan_parameter_edit, before, new_parameters,
                         datum=None if datum_policy == 'not_requested' else datum_policy)
    old = before_authorship['parameter_binding']['parameters']
    changed = _changes(old, new_parameters)
    vertices = sorted(scope['allowed_vertex_axes'])
    faces = sorted(scope['affected_face_ids'])
    entities = _entity_closure(before_witness['structure'], vertices, faces)
    dependencies = []
    active = ['holes[0].center[%d]' % i for i in (0,1)
              if old['holes'][0]['center'][i] != new_parameters['holes'][0]['center'][i]]
    if old['holes'][0]['radius'] != new_parameters['holes'][0]['radius']:
        active.append('holes[0].radius')
    if old['z_min'] != new_parameters['z_min'] or old['z_max'] != new_parameters['z_max']:
        active.append('z_range.'+datum_policy)
    if old['edge_bevel'] != new_parameters['edge_bevel']:
        active.append('edge_bevel')
    for path in active:
        dependency = deepcopy(before_authorship['edit_dependencies'][path])
        dependency['entity_ids'] = _entity_closure(before_witness['structure'], dependency['vertex_ids'], dependency['face_ids'])
        dependencies.append({'parameter': path, **dependency})
    types = _types(before_witness['structure'])
    lineage = [{'relation': 'continue', 'old': [key], 'new': [key]} for key in sorted(types)]
    contract = {'topology_policy': 'preserve',
                **{'before_'+key: report[key] for key in SIGNATURES},
                'affected_vertex_ids': vertices, 'affected_face_ids': faces,
                'affected_entity_ids': entities, 'lineage': lineage}
    result = {'schema_version': PLAN_VERSION, 'schedule_revision': before_authorship['schedule_revision'],
              'coordinate_space': 'object_local_mm', 'datum_policy': datum_policy,
              'before_authorship_sha256': before_authorship['authorship_sha256'],
              'expected_parameter_binding': author.parameter_binding(new_parameters),
              'parameter_changes': changed, 'parameter_dependencies': dependencies, 'authored_scope': scope,
              'allowed_vertex_axes': deepcopy(scope['allowed_vertex_axes']),
              'typed_continuations': [{'id': key, 'kind': types[key], 'relation': 'continue'} for key in sorted(types)],
              'kernel_contract': contract, 'dependency_resolution': 'authored_schedule_and_before_witness_only',
              'dependent_selections': 'invalidate_and_re_resolve',
              'source_preservation': 'not_checked', 'qualification': 'not_run'}
    if active==['edge_bevel']:
        result['protected_hole_regions']=_hole_region_signatures(before_witness)
    result['plan_sha256'] = kernel.fingerprint(result)
    return result


def _report(report, label):
    if not isinstance(report, dict) or report.get('status') != 'pass':
        _fail('NATIVE_EDIT_WITNESS_REQUIRED', label+' native structure report must pass')
    extraction = report.get('native_extraction')
    required = {'status': 'pass', 'source': 'raw object.data', 'coordinate_space': 'object_local_mm',
                'actual_edges_verified': True, 'identity_transport': 'validated_POINT_FACE_INT_slots',
                'crease_source': 'actual_EDGE_FLOAT_endpoint_values', 'signature_quantization': 'none'}
    if not isinstance(extraction, dict) or any(extraction.get(k) != v for k,v in required.items()):
        _fail('NATIVE_EDIT_WITNESS_REQUIRED', label+' requires actual raw native extraction evidence')
    if 'witness' not in report or 'authorship' not in report:
        _fail('NATIVE_EDIT_WITNESS_REQUIRED', label+' is missing raw witness or authorship')
    supplied, current = _mesh(report['witness'], report['authorship'])
    claimed = report.get('kernel_report', {})
    binding = report.get('binding', {})
    if not isinstance(claimed, dict) or not isinstance(binding, dict):
        _fail('NATIVE_EDIT_BINDING_INVALID', label+' has malformed evidence bindings')
    if claimed.get('status') != 'pass' or any(claimed.get(k) != current[k] or binding.get(k) != current[k] for k in SIGNATURES):
        _fail('SELECTION_STALE', label+' evidence signatures do not describe its raw witness')
    if any(k not in binding for k in NATIVE_INVARIANTS):
        _fail('NATIVE_EDIT_BINDING_INVALID', label+' is missing native identity/context bindings')
    if (binding['mesh_state'] != 'control' or binding['schedule_revision'] != report['authorship']['schedule_revision'] or
        binding.get('authorship_sha256') != report['authorship']['authorship_sha256'] or
        any(not isinstance(binding[k], str) or not binding[k] for k in ('object_id', 'data_id', 'context_sha256')) or
        type(binding['topology_epoch']) is not int or binding['topology_epoch'] < 0):
        _fail('NATIVE_EDIT_BINDING_INVALID', label+' native identity or authorship binding is invalid')
    return supplied, current


def _static_records(structure):
    """Geometric frames may change; meaning/membership never may in this edit."""
    result = {group: {} for group in GROUPS}
    for group in GROUPS:
        for record in structure[group]:
            row = deepcopy(record)
            if group == 'loops':
                row.pop('normal', None)
                row['anchor'].pop('position_mm', None)
            elif group == 'boundary_ports':
                row.pop('frame', None)
            result[group][row['id']] = row
    return result


def _verify_geometric_records(witness):
    """The bounded adapter has an exact anchored-frame convention."""
    mesh, structure = witness['mesh'], witness['structure']
    vm = structure['vertex_map']; loops = {r['id']: r for r in structure['loops']}
    for row in loops.values():
        points = [mesh['vertices'][vm[key]] for key in row['vertex_ids']]
        normal = kernel._normal(points); length = kernel._norm(normal)
        expected = [x/length for x in normal]
        if list(row['normal']) != expected or list(row['anchor']['position_mm']) != list(points[0]):
            _fail('AUTHORED_EDIT_WITNESS_MISMATCH', 'Loop geometry is not the exact current authored adapter witness', loop_id=row['id'])
    for row in structure['boundary_ports']:
        loop = loops[row['loop_id']]
        points = [mesh['vertices'][vm[key]] for key in loop['vertex_ids']]
        tangent = kernel._sub(points[1], points[0]); length = kernel._norm(tangent)
        tangent = [x/length for x in tangent]
        expected = {'origin_mm': list(points[0]), 'x_axis': tangent,
                    'y_axis': list(kernel._cross(loop['normal'], tangent)), 'normal': loop['normal']}
        if row['frame'] != expected:
            _fail('AUTHORED_EDIT_WITNESS_MISMATCH', 'Port frame is not the exact current authored adapter frame', port_id=row['id'])


def _lerp(a, b, t):
    return tuple(x+(y-x)*t for x,y in zip(a,b))


def _bore_coordinates(p):
    """Evaluate bounded parameter bindings, without constructing mesh topology."""
    hx, hy = p['holes'][0]['center']; radius = p['holes'][0]['radius']; bevel = p['edge_bevel']
    control = radius*(3/(2+math.cos(math.tau/24)))
    def ring(r, n):
        return [(hx+r*math.cos(-3*math.pi/4+math.tau*i/n), hy+r*math.sin(-3*math.pi/4+math.tau*i/n)) for i in range(n)]
    def frame(n):
        x0,y0,x1,y1 = p['local_patch_bounds']; corners = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
        return [_lerp(corners[i], corners[(i+1)%4], j/n) for i in range(4) for j in range(n)]
    width=p.get('subdivision_cage',{}).get('bore_support_width',max(bevel*2,radius*.12))
    rim = ring(control,24); support = ring(control+width,24)
    coarse = [_lerp(q,z,.9) for q,z in zip(ring(control,8),frame(2))]
    fine = [_lerp(q,z,.35) for q,z in zip(support,frame(6))]; transition = fine[:]
    for i in range(8):
        j=3*i+2; q=_lerp(coarse[i],coarse[(i+1)%8],2/3)
        transition[j]=tuple(fine[j][axis]+.18*(fine[j][axis]-q[axis]) for axis in (0,1))
        mid=_lerp(fine[3*i],transition[j],.5); q=_lerp(coarse[i],coarse[(i+1)%8],1/3)
        transition[3*i+1]=tuple(mid[axis]+.30*(mid[axis]-q[axis]) for axis in (0,1))
    return {'bore/%s/slot:%d'%(name,i): xy for name,points in
            (('rim',rim),('support',support),('transition',transition),('coarse',coarse)) for i,xy in enumerate(points)}


def _profile_z(p):
    lo,hi,b=p['z_min'],p['z_max'],p['edge_bevel']; near=b*math.sin(math.pi/6); factor=3/(2+math.cos(math.pi/6))
    out={'bottom_flat_guard':lo,'lower_wall_guard':lo+b+near,'upper_wall_guard':hi-b-near,'top_flat_guard':hi}
    for j in range(4):
        radius=b*factor if j in (1,2) else b
        out['lower_roundover/j%d'%j]=lo+b-radius*math.cos(j*math.pi/6)
        out['upper_roundover/j%d'%j]=hi-b+radius*math.cos(j*math.pi/6)
    if p.get('subdivision_cage',{}).get('outer_join_policy')=='joined_arc_v1':
        a=b*((48/math.sqrt(2)-1)/23-19/20);c=19*b/20
        out.update({'lower_roundover/j1':lo+b-c,'lower_roundover/j2':lo+b-a,
                    'upper_roundover/j1':hi-b+c,'upper_roundover/j2':hi-b+a})
    elif p.get('subdivision_cage',{}).get('outer_join_policy')=='joined_arc_v2':
        a=b*((48/math.sqrt(2)-1)/23-1);c=b
        out.update({'lower_roundover/j1':lo+b-c,'lower_roundover/j2':lo+b-a,
                    'upper_roundover/j1':hi-b+c,'upper_roundover/j2':hi-b+a})
    return out


def _outer_coordinates(p):
    """Independent schedule-coordinate attestation, without mesh construction.

    Fixed frame/grid, planar bands/corners and every profile slot are derived
    from the bound dimensions. No coordinate, metadata or after-edit diff is
    used to choose membership or recover intended dimensions.
    """
    w,h=p['size'];cx,cy=p.get('center',[0,0]);r=p['corner_radius'];b=p['edge_bevel']
    x0,y0,x1,y1=p['local_patch_bounds']
    core=(cx-w/2+r,cy-h/2+r,cx+w/2-r,cy+h/2-r)
    def axis(a,u,v,z):
        split=2 if 'bore_support_width' in p.get('subdivision_cage',{}) else 4
        da=min(r*math.pi/12,(u-a)/split);dz=min(r*math.pi/12,(z-v)/split)
        return [a,a+da,u,u+(v-u)/2,v,z-dz,z]
    xs=axis(core[0],x0,x1,core[2]);ys=axis(core[1],y0,y1,core[3])
    centers=[(core[2],core[1]),(core[2],core[3]),(core[0],core[3]),(core[0],core[1])]
    def outline(inset,joined=False):
        radius=r-inset
        sides=[[(x,core[1]-radius) for x in xs[:-1]],[(core[2]+radius,y) for y in ys[:-1]],
               [(x,core[3]+radius) for x in reversed(xs[1:])],[(core[0]-radius,y) for y in reversed(ys[1:])]]
        raw=[]
        for side,(x,y) in enumerate(centers):
            raw.extend(sides[side])
            raw.extend((x+radius*math.cos((side-1)*math.pi/2+i*math.pi/12),
                        y+radius*math.sin((side-1)*math.pi/2+i*math.pi/12)) for i in range(6))
        center_x=(core[0]+core[2])/2;center_y=(core[1]+core[3])/2
        ax=(core[2]-core[0])/2;ay=(core[3]-core[1])/2
        factor=3/(2+math.cos(math.pi/12));endpoint=(5-factor*math.cos(math.pi/12))/4
        if joined:
            endpoint=1. if joined=='joined_arc_v2' else (2+factor)/3
            mid_normal=(1+23*endpoint+23*factor*math.cos(math.pi/12)+factor*math.cos(math.pi/6))/48
            if not (radius>0 and 0<mid_normal<1):
                _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Joined arc midpoint domain is invalid')
        result=[]
        for x,y in raw:
            qx=center_x+max(-ax,min(ax,x-center_x));qy=center_y+max(-ay,min(ay,y-center_y))
            dx,dy=x-qx,y-qy
            curved=abs(dx)>1e-8 and abs(dy)>1e-8
            tangent=(abs(dx)>1e-8 and abs(abs(y-center_y)-ay)<1e-8) or (abs(dy)>1e-8 and abs(abs(x-center_x)-ax)<1e-8)
            scale=factor if curved else endpoint if tangent else 1.
            point=[qx+dx*scale,qy+dy*scale]
            if joined and tangent:
                if abs(dx)>1e-8:
                    axis=1;sign=1 if y>center_y else -1
                    distance=core[3]-ys[-2] if sign>0 else ys[1]-core[1]
                else:
                    axis=0;sign=1 if x>center_x else -1
                    distance=core[2]-xs[-2] if sign>0 else xs[1]-core[0]
                advance=(48*math.sqrt(1-mid_normal**2)+distance/radius-23*factor*math.sin(math.pi/12)-factor*math.sin(math.pi/6))/23
                if not (distance>0 and math.isfinite(advance) and 0<=advance<factor*math.sin(math.pi/12)):
                    _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Joined arc tangent controls leave the ordered domain')
                point[axis]+=sign*radius*advance
            result.append(tuple(point))
        if joined and any(math.dist(a,b)<=1e-8 for a,b in zip(result,result[1:]+result[:1])):
            _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Joined arc spans must remain nonzero')
        return result
    planar={author.grid_id(i,j):(x,y) for i,x in enumerate(xs) for j,y in enumerate(ys)}
    inset=b+max(b*2,r*.10);rf=r-inset;ri=.55*rf
    aliases=author.planar_aliases()
    for side in range(4):
        for sample in range(7):
            for layer,offset in enumerate((0,ri,rf)):
                key=author.band_id(side,sample,layer)
                if key in aliases:continue
                planar[key]=((xs[sample],core[1]-offset),(core[2]+offset,ys[sample]),
                             (xs[6-sample],core[3]+offset),(core[0]-offset,ys[6-sample]))[side]
        angle=(side-1)*math.pi/2;co,si=math.cos(angle),math.sin(angle)
        for name,count,radius in (('fine',6,rf),('coarse',2,ri)):
            for j in range(1,count):
                x,y=radius*math.cos(j*math.pi/2/count),radius*math.sin(j*math.pi/2/count)
                planar['corner/side:%d/%s:%d'%(side,name,j)]=(centers[side][0]+co*x-si*y,centers[side][1]+si*x+co*y)
    for token,xy in zip(author.outline_tokens(),outline(inset)):
        planar[author.resolve_alias(token,aliases)]=xy
    factor=3/(2+math.cos(math.pi/6));near=b*math.sin(math.pi/6)
    insets={'bottom_flat_guard':b+near,'lower_wall_guard':0.,'upper_wall_guard':0.,'top_flat_guard':b+near}
    for j in range(4):
        value=b-(b*factor if j in (1,2) else b)*math.sin(j*math.pi/6)
        insets['lower_roundover/j%d'%j]=value;insets['upper_roundover/j%d'%j]=value
    joined=p.get('subdivision_cage',{}).get('outer_join_policy')
    if joined=='joined_arc_v1':
        a=b*((48/math.sqrt(2)-1)/23-19/20);c=19*b/20
        for side in ('lower','upper'):
            insets[side+'_roundover/j1']=b-a;insets[side+'_roundover/j2']=b-c
    elif joined=='joined_arc_v2':
        a=b*((48/math.sqrt(2)-1)/23-1);c=b
        for side in ('lower','upper'):
            insets[side+'_roundover/j1']=b-a;insets[side+'_roundover/j2']=b-c
    profile={name:outline(value,joined) for name,value in insets.items()}
    return planar,profile


def _numeric_coordinate(actual, expected):
    # Source may be exact host doubles or native float32 SI materialization.
    # This is a numeric representation allowance, never an artistic tolerance;
    # protected coordinates are compared exactly elsewhere.
    try:
        native = struct.unpack('!f', struct.pack('!f', expected*.001))[0]*1000
    except (OverflowError, struct.error):
        _fail('AUTHORED_PARAMETER_GEOMETRY_MISMATCH', 'Parameter coordinate exceeds native precision domain')
    epsilon=max(math.ulp(float(expected)), math.ulp(float(actual)), math.ulp(float(native)))*64
    return min(abs(actual-expected), abs(actual-native)) <= epsilon


def _verify_parameter_coordinates(supplied):
    if supplied.get('authored_structure',{}).get('schedule_revision')=='sparse_sharp_panel_ids_v2':
        from .sparse_panel_geometry import validate_sparse_authored_identity
        return _author_call(validate_sparse_authored_identity,supplied)
    authored=supplied['authored_structure']; p=authored['parameter_binding']['parameters']
    bore, profile=_bore_coordinates(p),_profile_z(p)
    planar,outline=_outer_coordinates(p)
    for key,index in authored['vertex_map'].items():
        coordinates=supplied['vertices_mm'][index]
        expected={}
        if key.startswith('plane/'):
            pieces=key.split('/',2)
            if len(pieces)!=3 or pieces[1] not in ('bottom','top'):
                _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Unexpected planar logical address',vertex_id=key)
            expected[2]=p['z_min'] if pieces[1]=='bottom' else p['z_max']
            if pieces[2] in bore:
                expected.update(enumerate(bore[pieces[2]]))
            elif pieces[2] in planar:
                expected.update(enumerate(planar[pieces[2]]))
            else:
                _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Unexpected planar coordinate address',vertex_id=key)
        elif key.startswith('profile/'):
            name=key[len('profile/'):].rsplit('/slot:',1)[0]
            if name not in profile:
                _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Unexpected profile logical address',vertex_id=key)
            expected[2]=profile[name]
            try:expected.update(enumerate(outline[name][int(key.rsplit('/slot:',1)[1])]))
            except (KeyError,IndexError,ValueError):
                _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Unexpected outline coordinate address',vertex_id=key)
        else:
            _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Unexpected vertex logical address',vertex_id=key)
        for axis,value in expected.items():
            if not _numeric_coordinate(coordinates[axis],value):
                _fail('AUTHORED_PARAMETER_GEOMETRY_MISMATCH','Actual target coordinate contradicts its declared parameter binding',vertex_id=key,axis=axis)


def verify_authored_edit(before_report, after_report, edit_plan):
    """Prove the predeclared edit on raw actual before/after native witnesses."""
    if isinstance(edit_plan,dict) and edit_plan.get('schema_version')=='sparse-native-insertion/1.0':
        from .sparse_native_edit import verify_insertion
        return verify_insertion(before_report,after_report,edit_plan)
    before, before_kernel = _report(before_report, 'Before')
    after, after_kernel = _report(after_report, 'After')
    if not isinstance(edit_plan, dict) or not isinstance(edit_plan.get('expected_parameter_binding'), dict):
        _fail('AUTHORED_EDIT_CONTRACT_MISMATCH','A complete predeclared edit plan is required')
    expected_binding=edit_plan['expected_parameter_binding']
    if not isinstance(expected_binding.get('parameters'), dict):
        _fail('AUTHORED_EDIT_CONTRACT_MISMATCH','The edit plan must bind complete expected parameters')
    expected=plan_authored_edit(before_report['witness'],before_report['authorship'],
                               expected_binding['parameters'],datum_policy=edit_plan.get('datum_policy'))
    if edit_plan != expected:
        _fail('AUTHORED_EDIT_CONTRACT_MISMATCH','Edit plan is stale or its predeclared scope/lineage was altered')
    if after_report['authorship']['parameter_binding'] != expected_binding:
        _fail('AUTHORED_EDIT_PARAMETER_MISMATCH','After state does not bind the exact requested parameters')
    a,b=before_report['binding'],after_report['binding']
    if any(a[k]!=b[k] for k in NATIVE_INVARIANTS):
        _fail('NATIVE_EDIT_BINDING_CHANGED','Object/data identity, matrix, evaluation, schedule or topology epoch changed')
    aa,bb=before_report['authorship'],after_report['authorship']
    for key in ('schema_version','schedule_revision','supported_edit_datum','alias_manifest','semantic_control_loops','edit_dependencies',
                'semantic_connectivity_sha256','semantic_crease_sha256'):
        if aa.get(key)!=bb.get(key):
            _fail('AUTHORED_EDIT_TOPOLOGY_CHANGED','Authored meaning or schedule changed',field=key)
    sa,sb=before_report['witness']['structure'],after_report['witness']['structure']
    if any(sa[k]!=sb[k] for k in ('schema_version','length_unit','tolerances')) or _static_records(sa)!=_static_records(sb):
        _fail('AUTHORED_EDIT_SEMANTICS_CHANGED','Semantic roles, membership, loop order or witness policy changed')
    if before_report.get('losses',[])!=after_report.get('losses',[]):
        _fail('AUTHORED_EDIT_SEMANTICS_CHANGED','Adapter loss/required-interface conditions changed')
    for key,index in aa['vertex_map'].items():
        if key not in bb['vertex_map']:
            _fail('AUTHORED_EDIT_TOPOLOGY_CHANGED','Semantic vertex identity was removed',vertex_id=key)
        old,new=before['vertices_mm'][index],after['vertices_mm'][bb['vertex_map'][key]]
        allowed=edit_plan['allowed_vertex_axes'].get(key,[])
        if any(old[k]!=new[k] for k in range(3) if k not in allowed):
            _fail('AUTHORED_EDIT_OUTSIDE_SCOPE','A protected local coordinate axis changed',vertex_id=key)
    _verify_parameter_coordinates(before)
    _verify_parameter_coordinates(after)
    _verify_geometric_records(before_report['witness'])
    _verify_geometric_records(after_report['witness'])
    proof=kernel.verify_edit(before_report['witness']['mesh'],sa,after_report['witness']['mesh'],sb,edit_plan['kernel_contract'])
    protected={}
    if 'protected_hole_regions' in edit_plan:
        observed=_hole_region_signatures(after_report['witness'])
        if edit_plan['protected_hole_regions']!=observed:
            _fail('AUTHORED_HOLE_REGION_CHANGED','Actual hole-region coordinates, connectivity or creases changed')
        keys=('geometry_sha256','connectivity_sha256','crease_sha256')
        protected={'protected_hole_regions':{'status':'pass','source':'actual_before_and_after_semantic_region_witnesses',
                   'regions':[{**row,'status':'pass','coordinates':'exactly_preserved','connectivity':'exactly_preserved','creases':'exactly_preserved',
                               'before_signatures':{key:old[key] for key in keys},'after_signatures':{key:row[key] for key in keys}}
                              for old,row in zip(edit_plan['protected_hole_regions'],observed)]}}
    # Evidence only: construction and the predeclared write-set remain unchanged.
    changed = sorted(key for key, index in aa['vertex_map'].items()
                     if before['vertices_mm'][index] != after['vertices_mm'][bb['vertex_map'][key]])
    unchanged = sorted(set(aa['vertex_map']) - set(changed))
    protected_vertices = sorted(set(aa['vertex_map']) - set(edit_plan['allowed_vertex_axes']))
    protected_faces = sorted(set(aa['face_map']) - set(edit_plan['kernel_contract']['affected_face_ids']))
    impact = {'status':'pass','scope':'raw_source_cage_only',
              'affected_vertex_ids':sorted(edit_plan['allowed_vertex_axes']),
              'allowed_vertex_axes':deepcopy(edit_plan['allowed_vertex_axes']),
              'affected_face_ids':deepcopy(edit_plan['kernel_contract']['affected_face_ids']),
              'changed_vertex_ids':changed,'unchanged_vertex_ids':unchanged,
              'protected_vertex_ids':protected_vertices,'protected_face_ids':protected_faces,
              'protected_coordinates':'exactly_preserved','topology':'preserved',
              'evaluated_surface_preservation':'not_asserted'}
    return {'schema_version':PLAN_VERSION,'status':'pass','kernel_edit':proof,**protected,
            'before_binding':deepcopy(a),'after_binding':deepcopy(b),'impact':impact,
            'parameter_changes':deepcopy(edit_plan['parameter_changes']),
            'parameter_binding':'pass','parameter_coordinates':'pass','protected_coordinate_axes':'pass',
            'native_identity_context':'pass','typed_total_continuations':'pass',
            'datum_policy':edit_plan['datum_policy'],'plan_sha256':edit_plan['plan_sha256'],
            'before':before_kernel,'after':after_kernel,
            'dependent_selections':'invalidate_and_re_resolve',
            'source_preservation':'not_checked','non_target_objects':'not_checked',
            'saved_reopen':'not_checked','qualification':'not_run'}
