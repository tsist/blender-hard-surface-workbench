# SPDX-License-Identifier: GPL-3.0-or-later
"""r2 single-through-hole sparse control body, explicitly bounded and unfitted.

The initial 16/32 schedule is a supported topology family, not a universal
point-count claim. Nominal design, technical sampling and evaluated acceptance
remain separate. All coordinates here are initial L0 fitting candidates.
"""
from collections import Counter
from copy import deepcopy
import math
import struct

from .sparse_patch_graph import (SparseMeshBuilder, fail, fingerprint, edge_id,
    enrich_ports, semantic_mesh, validate_sparse_mesh, _strip_plan, _apply_strip_insertion,
    make_axis_strip_reference, _axis_strip_plan, _apply_axis_strip_insertion)
from .subd_panel_identity import authorship_payload
from .corner_column_geometry import (SCHEMA as CORNER_COLUMNS_SCHEMA,
    CONSTRUCTOR_SCHEMA as CORNER_CONSTRUCTOR_SCHEMA, apply_corner_columns)

SCHEDULE_REVISION = 'sparse_sharp_panel_ids_v2'
FIXED_FRAME_SCHEMA = 'fixed-frame-axis-aligned/1.0'
BALANCED_FIXED_FRAME_SCHEMA = 'fixed-frame-axis-aligned/1.1'
FIXED_FRAME_SCHEMAS = (FIXED_FRAME_SCHEMA, BALANCED_FIXED_FRAME_SCHEMA)
AXIS_INSERTION_POLICY = 'axis_plane_v1'
AXIS_CONSTRUCTOR_SCHEMA = 'quad.panel/sparse_control_cage-axis-plane/1.0'
# These constrain the supported HOST construction, not production tolerances or
# user-approved design. 30 degrees excludes acute local fan/sliver quads while
# retaining the bounded circle16-to-rectangle16 topology; it is never relaxed
# to accommodate an edit. Strict convexity is checked independently.
FIXED_FRAME_MIN_ANGLE_DEGREES = 30.0
FIXED_FRAME_MAX_ANGLE_DEGREES = 150.0
FIXED_FRAME_GRID_WIDTH_FRACTION = .005
FIXED_FRAME_RADIAL_MARGIN_FRACTION = .10
SUPPORTED_EDIT_DATUM = {'coordinate_space': 'object_local_mm',
    'hole_center_radius': 'whole_authored_sparse_layout_rebuild',
    'z_range': ['fixed_bottom', 'fixed_midplane'],
    'selection': 'explicit_required_for_thickness_edit', 'reference_approval': 'not_implied'}
CONFIG_DEFAULTS = {'hole_segments': 16, 'outer_segments': 32,
    'hole_planar_support': False, 'profile_arc_segments': 4, 'preview_levels': 0,
    'hole_cage_radius_factor': 3/(2+math.cos(math.tau/16)),
    'outer_support_fraction': 0.12, 'wall_support_fraction': 0.15,
    'collar_radius_factor': 1.375, 'insertions': []}


def _number(value, label, *, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value) or (positive and value <= 0):
        fail('SPARSE_PARAMETER_INVALID', 'Finite numeric parameter required', parameter=label)
    return float(value)


def _pair(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        fail('SPARSE_PARAMETER_INVALID', 'Two-coordinate parameter required', parameter=label)
    return tuple(_number(x, label) for x in value)


def _layout(value):
    required = {'schema', 'feature_frame_mm', 'corner_guard_mm'}
    if not isinstance(value, dict) or set(value) != required or value.get('schema') not in FIXED_FRAME_SCHEMAS:
        fail('SPARSE_LAYOUT_UNSUPPORTED', 'An exact, explicit fixed-frame layout declaration is required')
    frame = value['feature_frame_mm']
    if not isinstance(frame, (list, tuple)) or len(frame) != 4:
        fail('SPARSE_LAYOUT_INVALID', 'feature_frame_mm must be [xmin, ymin, xmax, ymax]')
    frame = [_number(v, 'feature_frame_mm') for v in frame]
    guard = _number(value['corner_guard_mm'], 'corner_guard_mm', positive=True)
    if not (frame[0] < frame[2] and frame[1] < frame[3]):
        fail('SPARSE_LAYOUT_INVALID', 'The fixed feature frame must have positive width and height')
    return {'schema': value['schema'], 'feature_frame_mm': frame, 'corner_guard_mm': guard}


def sparse_config(parameters):
    if not isinstance(parameters, dict):
        fail('SPARSE_PARAMETER_INVALID', 'Parameter object required')
    cfg = parameters.get('sparse_cage', {})
    allowed = set(CONFIG_DEFAULTS) | {'layout', 'insertion_policy', 'corner_columns'}
    if not isinstance(cfg, dict) or set(cfg)-allowed:
        fail('SPARSE_CONFIG_UNSUPPORTED', 'Unknown sparse topology or fitting controls', fields=sorted(set(cfg)-allowed) if isinstance(cfg, dict) else [])
    result = {**deepcopy(CONFIG_DEFAULTS), **deepcopy(cfg)}
    # Absence deliberately preserves the complete legacy config/geometry path.
    # Explicit null/partial declarations cannot silently select legacy layout.
    if 'layout' in result:
        result['layout'] = _layout(result['layout'])
    # This selector is intentionally absent from CONFIG_DEFAULTS: old saved
    # declarations retain their exact serialized config and linear constructor.
    if 'insertion_policy' in result:
        if result['insertion_policy'] != AXIS_INSERTION_POLICY:
            fail('SPARSE_INSERTION_POLICY', 'Only an explicit axis_plane_v1 insertion policy is supported')
        if 'layout' not in result:
            fail('SPARSE_INSERTION_POLICY', 'Axis-plane insertions require an explicit fixed-frame layout')
    if 'corner_columns' in result:
        if parameters.get('topology_strategy') != 'sparse_control_cage':
            fail('SPARSE_CORNER_COLUMNS', 'Corner columns require the explicit sparse_control_cage topology strategy')
        if result['corner_columns'] != {'schema': CORNER_COLUMNS_SCHEMA}:
            fail('SPARSE_CORNER_COLUMNS', 'Corner columns require the exact explicit midpoint schema without fitting controls')
        if 'layout' not in result or result.get('insertion_policy') != AXIS_INSERTION_POLICY:
            fail('SPARSE_CORNER_COLUMNS', 'Midpoint corner columns require fixed-frame layout and axis_plane_v1')
        if type(result['profile_arc_segments']) is not int or result['profile_arc_segments'] != 4:
            fail('SPARSE_CORNER_COLUMNS', 'The two 44-node midpoint corner loops require profile_arc_segments 4')
    if type(result['hole_segments']) is not int or result['hole_segments'] != 16 or type(result['outer_segments']) is not int or result['outer_segments'] != 32:
        fail('SPARSE_SCHEDULE_UNSUPPORTED', 'Current bounded r2 family supports initial hole16/outer32 only; other families require implementation')
    if type(result['hole_planar_support']) is not bool:
        fail('SPARSE_CONFIG_UNSUPPORTED', 'hole_planar_support must be a boolean A/B choice')
    for name, lo, hi in [('profile_arc_segments', 2, 8), ('preview_levels', 0, 3)]:
        if type(result[name]) is not int or not lo <= result[name] <= hi:
            fail('SPARSE_CONFIG_UNSUPPORTED', 'Bounded integer sparse control required', field=name)
    for name, lo, hi in [('hole_cage_radius_factor', 1.0, 1.1), ('outer_support_fraction', .04, .3),
                         ('wall_support_fraction', .03, .3), ('collar_radius_factor', 1.25, 1.7)]:
        value = _number(result[name], name)
        if not lo <= value <= hi:
            fail('SPARSE_CONFIG_UNSUPPORTED', 'Sparse fitting control is outside its declared candidate domain', field=name)
    insertions = result['insertions']
    max_insertions = 2 if result.get('insertion_policy') == AXIS_INSERTION_POLICY else 1
    if not isinstance(insertions, list) or len(insertions) > max_insertions:
        fail('SPARSE_INSERTION_DOMAIN', 'Insertion count exceeds the explicit bounded policy', maximum=max_insertions)
    for insertion in insertions:
        if not isinstance(insertion, dict) or set(insertion)-{'corridor', 'fraction'} or insertion.get('corridor') not in ('east', 'south'):
            fail('SPARSE_INSERTION_DOMAIN', 'One east/south corridor declaration required')
        t = _number(insertion.get('fraction', .5), 'insertion fraction')
        if not .2 <= t <= .8:
            fail('SPARSE_INSERTION_DOMAIN', 'Insertion fraction must lie within 0.2..0.8')
        insertion['fraction'] = t
    if max_insertions == 2 and len(insertions) == 2 and insertions[0] == insertions[1]:
        fail('SPARSE_AXIS_SPACING', 'Duplicate axis-plane declarations are not supported')
    return result


def _fixed_frame_domain(d, cfg):
    """Validate the frozen macrogrid and all local annulus quads before allocation."""
    layout = cfg['layout']
    fx0, fy0, fx1, fy1 = layout['feature_frame_mm']
    guard = layout['corner_guard_mm']
    frame_x = [fx0+(fx1-fx0)*i/4 for i in range(5)]
    frame_y = [fy0+(fy1-fy0)*i/4 for i in range(5)]
    # v1.0 remains byte-exact for saved reconstruction. v1.1 balances the
    # frame-to-core span; the guard still bounds a separate tangent buffer.
    east_corridor = ((fx1+d['x1'])/2 if layout['schema'] == BALANCED_FIXED_FRAME_SCHEMA
                     else (fx1+d['x1']-guard)/2)
    xs = [d['x0'], d['x0']+guard]+frame_x+[east_corridor, d['x1']-guard, d['x1']]
    ys = [d['y0'], d['y0']+guard]+frame_y+[d['y1']]
    min_width = max(1e-5, FIXED_FRAME_GRID_WIDTH_FRACTION*min(d['w'], d['h']))
    widths = [b-a for values in (xs, ys) for a, b in zip(values, values[1:])]
    if any(not math.isfinite(v) for v in xs+ys+widths) or min(widths) < min_width:
        fail('SPARSE_FRAME_GRID_SPACING', 'The frozen frame and corner guards must form a strictly ordered, sufficiently wide core grid',
             required_minimum_mm=min_width, observed_minimum_mm=min(widths))
    radius = d['hr']*cfg['hole_cage_radius_factor']
    outer_radius = radius+(d['hole_support_width'] if cfg['hole_planar_support'] else 0)
    radial_margin = min(d['hx']-fx0, fx1-d['hx'], d['hy']-fy0, fy1-d['hy'])-outer_radius
    min_margin = max(min_width, FIXED_FRAME_RADIAL_MARGIN_FRACTION*d['hr'])
    if not math.isfinite(radial_margin) or radial_margin < min_margin:
        fail('SPARSE_FRAME_RADIAL_CLEARANCE', 'The hole control ring and optional support require positive radial margin inside the frozen frame',
             required_minimum_mm=min_margin, observed_minimum_mm=radial_margin)
    collar = ([(frame_x[i], fy0) for i in range(4)]+[(fx1, frame_y[i]) for i in range(4)]+
              [(frame_x[i], fy1) for i in range(4, 0, -1)]+[(fx0, frame_y[i]) for i in range(4, 0, -1)])
    def circle(r):
        return [(d['hx']+r*math.cos(-3*math.pi/4+i*math.tau/16),
                 d['hy']+r*math.sin(-3*math.pi/4+i*math.tau/16)) for i in range(16)]
    rings = [circle(radius)]
    if cfg['hole_planar_support']:
        rings.append(circle(outer_radius))
    rings.append(collar)
    min_angle, max_angle, min_turn = 180.0, 0.0, 1.0
    minimum_at, maximum_at, nonconvex_at = None, None, None
    for band, (inner, outer) in enumerate(zip(rings, rings[1:])):
        for sector in range(16):
            j = (sector+1) % 16
            quad = [inner[sector], outer[sector], outer[j], inner[j]]
            for k, point in enumerate(quad):
                before, after = quad[(k-1) % 4], quad[(k+1) % 4]
                a = (before[0]-point[0], before[1]-point[1])
                b = (after[0]-point[0], after[1]-point[1])
                norm = math.hypot(*a)*math.hypot(*b)
                turn = (b[0]*a[1]-b[1]*a[0])/norm if norm else -1.0
                if not math.isfinite(turn) or turn <= 1e-12:
                    nonconvex_at = {'band': band, 'sector': sector, 'corner': k, 'normalized_turn': turn}
                angle = (math.degrees(math.acos(max(-1.0, min(1.0, (a[0]*b[0]+a[1]*b[1])/norm))))
                         if norm else 0.0)
                min_turn = min(min_turn, turn)
                if angle < min_angle:
                    min_angle, minimum_at = angle, {'band': band, 'sector': sector, 'corner': k}
                if angle > max_angle:
                    max_angle, maximum_at = angle, {'band': band, 'sector': sector, 'corner': k}
    quality = {'strictly_convex': nonconvex_at is None, 'minimum_normalized_turn': min_turn,
               'required_minimum_angle_degrees': FIXED_FRAME_MIN_ANGLE_DEGREES,
               'required_maximum_angle_degrees': FIXED_FRAME_MAX_ANGLE_DEGREES,
               'observed_minimum_angle_degrees': min_angle, 'observed_maximum_angle_degrees': max_angle,
               'minimum_at': minimum_at, 'maximum_at': maximum_at,
               'quads_checked': 16*(len(rings)-1),
               'scope': 'host_supported_construction_only; no_production_or_reference_acceptance'}
    if nonconvex_at is not None:
        fail('SPARSE_FRAME_ANNULUS_CONVEXITY', 'Every local hole-to-frame quad must be strictly convex',
             offending_corner=nonconvex_at, annulus_quality=quality, observed_radial_margin_mm=radial_margin)
    if min_angle < FIXED_FRAME_MIN_ANGLE_DEGREES or max_angle > FIXED_FRAME_MAX_ANGLE_DEGREES:
        fail('SPARSE_FRAME_ANNULUS_ANGLE', 'The fixed-frame local annulus lies outside its supported 30..150 degree bounds',
             annulus_quality=quality, observed_radial_margin_mm=radial_margin,
             required_radial_margin_mm=min_margin)
    return {'schema': layout['schema'], 'feature_frame_mm': list(layout['feature_frame_mm']),
            'corner_guard_mm': guard, 'grid_x_mm': xs, 'grid_y_mm': ys,
            'minimum_grid_spacing_mm': min_width, 'observed_grid_spacing_mm': min(widths),
            'minimum_radial_margin_mm': min_margin, 'observed_radial_margin_mm': radial_margin,
            'annulus_quality': quality}


def _domain(p, cfg):
    if not isinstance(p, dict):
        fail('SPARSE_PARAMETER_INVALID', 'Parameter object required')
    if p.get('topology_strategy') not in (None, 'sparse_control_cage') or p.get('op') not in (None, 'quad.panel'):
        fail('SPARSE_SCHEDULE_UNSUPPORTED', 'No implicit migration from another operation or topology strategy')
    if 'chord_tolerance' in p:
        _number(p['chord_tolerance'], 'technical chord tolerance', positive=True)
    try:
        w, h = _pair(p['size'], 'size'); cx, cy = _pair(p.get('center', [0, 0]), 'center')
        r = _number(p['corner_radius'], 'corner_radius', positive=True)
        b = _number(p['edge_bevel'], 'edge_bevel', positive=True)
        lo, hi = _number(p['z_min'], 'z_min'), _number(p['z_max'], 'z_max')
        holes = p['holes']
        if not isinstance(holes, list) or len(holes) != 1 or holes[0].get('kind') != 'circle' or holes[0].get('counterbore') or p.get('lips'):
            fail('SPARSE_DOMAIN', 'Exactly one sharp circular through-hole without lips or counterbore is supported')
        hx, hy = _pair(holes[0]['center'], 'hole center')
        hr = _number(holes[0]['radius'], 'hole radius', positive=True)
    except (KeyError, IndexError, TypeError) as exc:
        fail('SPARSE_PARAMETER_INVALID', 'Incomplete single-hole plate parameters', reason=str(exc))
    if not (1 <= w <= 10000 and 1 <= h <= 10000 and .75 <= w/h <= 3 and .05*h <= r <= .3*min(w, h)
            and .01*h <= hi-lo <= .4*h and 0 < b < min(r*.7, (hi-lo)/2)
            and .04*h <= hr <= .25*min(w, h)):
        fail('SPARSE_DOMAIN', 'Plate dimensions exceed the bounded geometric candidate domain')
    half = cfg['collar_radius_factor']*hr
    x0, x1, y0, y1 = cx-w/2+r, cx+w/2-r, cy-h/2+r, cy+h/2-r
    gap = min(hx-half-x0, x1-hx-half, hy-half-y0, y1-hy-half)
    if 'layout' not in cfg and gap <= max(r*.04, 1e-5):
        fail('SPARSE_COLLAR_CLEARANCE', 'The complete square hole collar must remain strictly inside the rounded-outline core', clearance_mm=gap)
    support_width = .07*hr
    if 'layout' not in cfg and cfg['hole_cage_radius_factor']*hr + (support_width if cfg['hole_planar_support'] else 0) >= half:
        fail('SPARSE_COLLAR_CLEARANCE', 'Hole control ring and its optional support must fit within the collar')
    result = {'w': w, 'h': h, 'r': r, 'b': b, 'cx': cx, 'cy': cy,
            'hx': hx, 'hy': hy, 'hr': hr, 'lo': lo, 'hi': hi,
            'half': half, 'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1,
            'hole_support_width': support_width}
    if 'layout' in cfg:
        result['fixed_frame'] = _fixed_frame_domain(result, cfg)
    return result


def validate_parameter_domain(parameters):
    """Pure, bounded parameter validation; no files, mesh edits or qualification."""
    cfg = sparse_config(parameters)
    return {'config': cfg, 'geometry_domain': _domain(parameters, cfg),
            'status': 'supported_candidate_domain', 'qualification': 'not_run'}


def _raw_body(p, cfg, feature_id):
    d = _domain(p, cfg)
    x0, x1, y0, y1 = (d[k] for k in ('x0', 'x1', 'y0', 'y1'))
    hx, hy, half = d['hx'], d['hy'], d['half']
    if 'layout' in cfg:
        xs, ys = d['fixed_frame']['grid_x_mm'], d['fixed_frame']['grid_y_mm']
    else:
        hole_x = [hx-half+half*i/2 for i in range(5)]
        hole_y = [hy-half+half*i/2 for i in range(5)]
        east_buffer = x1-(x1-hole_x[-1])/3
        xs = [x0, x0+(hole_x[0]-x0)/3]+hole_x+[(hole_x[-1]+east_buffer)/2, east_buffer, x1]
        ys = [y0, y0+(hole_y[0]-y0)/3]+hole_y+[y1]
    xt = ['west_core', 'west_buffer']+['hole_x%d'%i for i in range(5)]+['east_corridor', 'east_buffer', 'east_core']
    yt = ['south_core', 'south_buffer']+['hole_y%d'%i for i in range(5)]+['north_core']
    builder = SparseMeshBuilder(feature_id)
    side_data, seeds = {}, {}

    # The CCW perimeter slots are authored side/sample addresses, not recovered
    # by sorting generated coordinates. Corner/tangent correspondence is shared
    # unchanged by every outer profile ring.
    slots = ([(0, i, len(xs)-1, (i, 0)) for i in range(len(xs)-1)] +
             [(1, j, len(ys)-1, (len(xs)-1, j)) for j in range(len(ys)-1)] +
             [(2, i, len(xs)-1, (len(xs)-1-i, len(ys)-1)) for i in range(len(xs)-1)] +
             [(3, j, len(ys)-1, (0, len(ys)-1-j)) for j in range(len(ys)-1)])

    def outline_xy(slot, rho):
        side, i, n, ij = slot
        core_half = (d['w']/2-d['r']) if side % 2 == 0 else (d['h']/2-d['r'])
        other_half = (d['h']/2-d['r']) if side % 2 == 0 else (d['w']/2-d['r'])
        if i == 0:
            u, v = -core_half-rho/math.sqrt(2), -other_half-rho/math.sqrt(2)
        else:
            # Fixed exterior scheduling is independent of hole position/radius.
            # The intervening planar transition absorbs collar edits; neither
            # existing outer geometry nor its semantic addresses are re-fitted.
            if 'layout' in cfg:
                # Two tangent anchors bound each rounded corner. All remaining
                # straight slots inherit their actual core tangential coordinate,
                # so every outer ring shares axis-aligned field correspondence.
                if i == 1:
                    u = -core_half
                elif i == n-1:
                    u = core_half
                else:
                    u = ((xs[ij[0]]-d['cx']) if side % 2 == 0 else (ys[ij[1]]-d['cy']))
                    if side >= 2:
                        u = -u
            else:
                u = -core_half+(i-1)/(n-2)*2*core_half
            v = -other_half-rho
        x, y = ((u, v), (-v, u), (-u, -v), (v, -u))[side]
        return [d['cx']+x, d['cy']+y]

    def outline_ring(layer, rho, z):
        ids = []
        for slot in slots:
            sid = 'vertex:%s/side:%d/sample:%d'%(layer, slot[0], slot[1])
            builder.vertex(sid, [*outline_xy(slot, rho), z]); ids.append(sid)
        builder.port(layer, ids)
        return ids

    def planar_bridge(a, b, layer, role, *, flip=False):
        if len(a) != len(b):
            fail('SPARSE_PORT_ARITY', 'Planar bridge requires equal ordered port arity')
        for i in range(len(a)):
            j = (i+1) % len(a)
            ids = [a[i], b[i], b[j], a[j]]
            builder.face('face:%s/%s/sector:%d'%(layer, role, i), ids[::-1] if flip else ids,
                         region_group=layer, surface_role=layer+'/'+role, support=('support' in role))

    for layer, z, flip in [('top', d['hi'], False), ('bottom', d['lo'], True)]:
        def grid(i, j):
            sid = 'vertex:%s/grid/%s/%s'%(layer, xt[i], yt[j])
            builder.vertex(sid, [xs[i], ys[j], z])
            return sid
        for j in range(len(ys)-1):
            for i in range(len(xs)-1):
                if 2 <= i < 6 and 2 <= j < 6:
                    continue
                ids = [grid(i, j), grid(i+1, j), grid(i+1, j+1), grid(i, j+1)]
                builder.face('face:%s/field/%s/%s'%(layer, xt[i], yt[j]), ids[::-1] if flip else ids,
                             region_group=layer, surface_role=layer+'/planar_field')
        collar_ij = ([(i, 2) for i in range(2, 6)]+[(6, j) for j in range(2, 6)]+
                     [(i, 6) for i in range(6, 2, -1)]+[(2, j) for j in range(6, 2, -1)])
        collar = [grid(i, j) for i, j in collar_ij]
        builder.port(layer+'.hole_collar', collar)
        hole_rings = []
        for name, radius, crease in [('hole_rim', d['hr']*cfg['hole_cage_radius_factor'], 1.0)]+([
                ('hole_support', d['hr']*cfg['hole_cage_radius_factor']+d['hole_support_width'], 0.0)] if cfg['hole_planar_support'] else []):
            ids = []
            for i in range(16):
                angle = -3*math.pi/4+i*math.tau/16
                sid = 'vertex:%s/%s/sector:%d'%(layer, name, i)
                builder.vertex(sid, [hx+radius*math.cos(angle), hy+radius*math.sin(angle), z]); ids.append(sid)
            builder.port(layer+'.'+name, ids, crease=crease)
            hole_rings.append(ids)
        if cfg['hole_planar_support']:
            planar_bridge(hole_rings[0], hole_rings[1], layer, 'hole_planar_support', flip=flip)
        planar_bridge(hole_rings[-1], collar, layer, 'hole_to_field', flip=flip)
        core = [grid(*slot[3]) for slot in slots]
        builder.port(layer+'.outer_core', core)
        flat_rho = d['r']-d['b']
        support = outline_ring(layer+'.outer_support', flat_rho*(1-cfg['outer_support_fraction']), z)
        flat = outline_ring(layer+'.outer_flat_boundary', flat_rho, z)
        planar_bridge(core, support, layer, 'outer_to_field', flip=flip)
        planar_bridge(support, flat, layer, 'outer_support', flip=flip)
        side_data[layer] = {'rim': hole_rings[0], 'flat': flat}
        if layer == 'top':
            seeds['east'] = [flat[7], flat[8]]
            seeds['south'] = [flat[len(xs)], flat[len(xs)+1]]

    def outer_bridge(a, b, label, group, *, support=False):
        if len(a) != len(b):
            fail('SPARSE_PORT_ARITY', 'Outer body bridge requires equal ordered port arity')
        for i in range(len(a)):
            j = (i+1) % len(a)
            builder.face('face:outer/%s/sector:%d'%(label, i), [a[i], b[i], b[j], a[j]],
                         region_group=group, surface_role=label, curved=group == 'outer_roundover', support=support)

    last = side_data['top']['flat']
    n, r, b = cfg['profile_arc_segments'], d['r'], d['b']
    for i in range(1, n+1):
        theta = math.pi*i/(2*n)
        current = outline_ring('profile.top_arc.%d'%i, r-b+b*math.sin(theta), d['hi']-b+b*math.cos(theta))
        outer_bridge(last, current, 'outer_roundover/top_arc.%d'%i, 'outer_roundover'); last = current
    guard = min(b*cfg['wall_support_fraction'], (d['hi']-d['lo']-2*b)/4)
    for name, z in [('wall_support_top', d['hi']-b-guard), ('wall_body', d['lo']+b+guard), ('wall_support_bottom', d['lo']+b)]:
        current = outline_ring('profile.'+name, r, z)
        outer_bridge(last, current, 'side/'+name, 'side', support='support' in name); last = current
    for i in range(n-1, -1, -1):
        theta = math.pi*i/(2*n)
        current = (side_data['bottom']['flat'] if i == 0 else
                   outline_ring('profile.bottom_arc.%d'%i, r-b+b*math.sin(theta), d['lo']+b-b*math.cos(theta)))
        outer_bridge(last, current, 'outer_roundover/bottom_arc.%d'%i, 'outer_roundover'); last = current
    for i in range(16):
        j = (i+1) % 16
        builder.face('face:holewall/sector:%d'%i,
                     [side_data['top']['rim'][i], side_data['top']['rim'][j], side_data['bottom']['rim'][j], side_data['bottom']['rim'][i]],
                     region_group='holewall', surface_role='holewall/bore_wall', curved=True)
    mesh = builder.mesh()
    mesh['sparse_graph']['corridor_seeds'] = seeds
    for layer in ('top', 'bottom'):
        ids = side_data[layer]['rim']
        mesh['edge_creases'].extend([builder.vertex_map[a], builder.vertex_map[b], 1.0] for a, b in zip(ids, ids[1:]+ids[:1]))
    enrich_ports(mesh)
    if 'corner_columns' in cfg:
        mesh = apply_corner_columns(mesh)
        d['corner_base_audit'] = validate_sparse_mesh(mesh)
    if cfg.get('insertion_policy') == AXIS_INSERTION_POLICY:
        # Reference planes are established on the validated base schedule, not
        # by trusting metadata attached to a caller-supplied edited graph.
        mesh['sparse_graph']['axis_strip_reference'] = make_axis_strip_reference(
            mesh, minimum_spacing_mm=d['fixed_frame']['minimum_grid_spacing_mm'])
        for insertion in cfg['insertions']:
            mesh = _apply_axis_strip_insertion(mesh,
                _axis_strip_plan(mesh, insertion['corridor'], insertion['fraction']))
    else:
        for insertion in cfg['insertions']:
            mesh = _apply_strip_insertion(mesh, _strip_plan(mesh, insertion['corridor'], insertion['fraction']),
                                         preserve_equal_components='layout' in cfg)
    return mesh, d


def _authored_loops(graph):
    """Persist ordinary ports and axis-plane cycles without conflating frames."""
    loops = [{'role': row['role'], 'vertex_ids': list(row['vertex_ids']), 'crease': row['crease'],
              'expected_valence': 4 if row['kind'] == 'regular_edge_loop' else None}
             for row in graph['ports']]
    loops.extend({'role': row['role'], 'vertex_ids': list(row['vertex_ids']), 'crease': row['crease'],
                  'expected_valence': 4} for row in graph.get('semantic_inserted_loops', []))
    loops.extend({'role': row['role'], 'vertex_ids': list(row['vertex_ids']), 'crease': row['crease'],
                  'expected_valence': 4} for row in graph.get('semantic_structural_loops', []))
    return loops


def _manifest(mesh, p):
    graph = mesh['sparse_graph']; sem = semantic_mesh(mesh)
    loops = _authored_loops(graph)
    all_ids = sorted(graph['vertex_map'])
    fixed_frame = 'layout' in p.get('sparse_cage', {})
    planar = ([sid for sid in all_ids if '/hole_rim/' in sid or '/hole_support/' in sid] if fixed_frame else
              [sid for sid in all_ids if '/grid/' in sid or '/hole_' in sid])
    outer = [sid for sid in all_ids if sid.startswith('vertex:profile.') or '.outer_' in sid]
    # Hashed constructor vertices inherit their parameter scopes from semantic
    # parent endpoints, never from the generated address's spelling.
    for sid, row in graph.get('structural_vertex_lineage', {}).items():
        parents = row['source_endpoint_vertex_ids']
        if any(parent in planar for parent in parents): planar.append(sid)
        if any(parent in outer for parent in parents): outer.append(sid)
    # After a strip insertion, generated midpoints have explicit graph ancestry;
    # dependency expansion follows each split edge's two authored endpoints.
    transaction = graph.get('insertion_transaction')
    if transaction:
        cfg = p.get('sparse_cage', {})
        axis_policy = cfg.get('insertion_policy') == AXIS_INSERTION_POLICY
        # The latest transaction is relative to its immediate prefix. Reusing
        # the zero-cut body here would lose earlier inserted outer descendants
        # and cannot resolve edges created by the first cut.
        prefix = cfg.get('insertions', [])[:-1] if axis_policy else []
        base = build_sparse_panel({**p, 'sparse_cage': {**cfg, 'insertions': prefix}}, _validate=False)
        if axis_policy:
            planar = list(base['authored_structure']['edit_dependencies']['holes[0].radius']['vertex_ids'])
            outer = list(base['authored_structure']['edit_dependencies']['edge_bevel']['vertex_ids'])
        inv = {i: sid for sid, i in base['sparse_graph']['vertex_map'].items()}
        for eid in transaction['plan']['split_edge_ids']:
            a, b = base['sparse_graph']['edges'][base['sparse_graph']['edge_map'][eid]]
            sid = (transaction['split_edge_vertex_ids'][eid] if axis_policy else
                   'vertex:insert:'+fingerprint([transaction['plan']['corridor'], eid])[:40])
            if inv[a] in planar or inv[b] in planar: planar.append(sid)
            if inv[a] in outer or inv[b] in outer: outer.append(sid)
    def dependency(vertices, axes):
        chosen = set(vertices)
        return {'vertex_ids': sorted(chosen), 'allowed_axes': axes,
                'face_ids': sorted(sid for sid, ids in sem['connectivity'].items() if chosen.intersection(ids)),
                'semantic_control_loop_roles': sorted(row['role'] for row in loops if chosen.intersection(row['vertex_ids']))}
    deps = {'holes[0].center[0]': dependency(planar, [0, 1]), 'holes[0].center[1]': dependency(planar, [0, 1]),
            'holes[0].radius': dependency(planar, [0, 1]), 'edge_bevel': dependency(outer, [0, 1, 2]),
            'z_range.fixed_bottom': dependency(all_ids, [2]), 'z_range.fixed_midplane': dependency(all_ids, [2])}
    edit_datum = deepcopy(SUPPORTED_EDIT_DATUM)
    if fixed_frame:
        edit_datum.update({'hole_center_radius': 'frozen_macrogrid_local_hole_rings_only',
                           'layout_schema': p['sparse_cage']['layout']['schema'],
                           'feature_frame_edit': 'distinct_new_plan_required'})
    if p.get('sparse_cage', {}).get('insertion_policy') == AXIS_INSERTION_POLICY:
        edit_datum.update({'insertion_policy': AXIS_INSERTION_POLICY,
                           'insertion_policy_edit': 'distinct_new_plan_required'})
    if 'corner_columns' in p.get('sparse_cage', {}):
        edit_datum.update({'corner_columns_schema': CORNER_COLUMNS_SCHEMA,
                           'corner_columns_edit': 'distinct_new_construction_required'})
    result = {'schema_version': '1.0', 'schedule_revision': SCHEDULE_REVISION,
        'vertex_map': dict(graph['vertex_map']), 'face_map': dict(graph['face_map']),
        'semantic_control_loops': loops,
        'parameter_binding': {'constructor': 'quad.panel/sparse_control_cage', 'parameters': deepcopy(p),
                              'design_sha256': fingerprint(p), 'coordinate_space': 'object_local_mm'},
        'supported_edit_datum': edit_datum, 'alias_manifest': {},
        'semantic_connectivity_sha256': fingerprint(sem['connectivity']),
        'semantic_crease_sha256': fingerprint(sem['creases']), 'edit_dependencies': deps}
    result['authorship_sha256'] = fingerprint(authorship_payload(result))
    return result


def build_sparse_panel(parameters, feature_id='panel', *, topology=None, _validate=True):
    """Build complete L0 data without Blender, file I/O, repair or source mutation."""
    p = deepcopy(parameters)
    if not isinstance(p, dict):
        fail('SPARSE_PARAMETER_INVALID', 'Parameter object required')
    if topology is not None:
        if 'sparse_cage' in p and p['sparse_cage'] != topology:
            fail('SPARSE_CONFIG_CONFLICT', 'Conflicting topology declarations')
        p['sparse_cage'] = deepcopy(topology)
    cfg = sparse_config(p)
    mesh, d = _raw_body(p, cfg, feature_id)
    audit = validate_sparse_mesh(mesh)
    graph = mesh['sparse_graph']
    mesh['control_loops'] = [{'role': row['role'],
                             'vertex_indices': [graph['vertex_map'][sid] for sid in row['vertex_ids']],
                             'crease': row['crease'], 'expected_valence': row['expected_valence']}
                            for row in _authored_loops(graph)]
    mesh['sharp_edges'] = [row[:2] for row in mesh['edge_creases']]
    mesh['subdivision_modifier'] = {'name': 'HS_Subdivision_Cage', 'subdivision_type': 'CATMULL_CLARK',
        'levels': cfg['preview_levels'], 'render_levels': cfg['preview_levels'], 'quality': 6,
        'uv_smooth': 'PRESERVE_BOUNDARIES', 'boundary_smooth': 'ALL', 'use_creases': True, 'use_limit_surface': True}
    nominal = {'center_mm': [d['cx'], d['cy']], 'size_mm': [d['w'], d['h']], 'corner_radius_mm': d['r'],
        'edge_roundover_radius_mm': d['b'], 'hole_center_mm': [d['hx'], d['hy']], 'hole_radius_mm': d['hr'],
        'z_range_mm': [d['lo'], d['hi']]}
    mesh['metadata'] = {'operation': 'quad.panel', 'construction': 'authored_r2_shared_index_complete_body',
        'signed_volume_mm3': audit['signed_volume_mm3'], 'source_faces': {'quads': len(mesh['faces']), 'triangles': 0, 'ngons': 0},
        'bounds_mm': [min(v[k] for v in mesh['vertices_mm']) for k in range(3)]+[max(v[k] for v in mesh['vertices_mm']) for k in range(3)],
        'subdivision_cage': {'strategy': 'sparse_control_cage',
            'candidate_version': (4 if cfg['layout']['schema'] == BALANCED_FIXED_FRAME_SCHEMA else 3) if 'layout' in cfg else 2,
            'nominal_design': nominal,
            'config': deepcopy(cfg), 'initial_topology': {'hole_segments': 16, 'outer_segments': 32},
            'top_plane_candidate_quads': 143 if cfg['hole_planar_support'] else 127,
            'region_face_counts': audit['region_face_counts'],
            'patch_face_counts': dict(sorted(Counter(row['surface_role'] for row in mesh['face_provenance']).items())),
            'source_valence_histogram': {str(k): v for k, v in audit['vertex_valence_histogram'].items()},
            'outer_profile_band_count': 2*cfg['profile_arc_segments']+3,
            'hole_wall_band_count': 1, 'hole_planar_support': 'retained_AB' if cfg['hole_planar_support'] else 'conditional_A',
            'technical_chord_tolerance_mm': p.get('chord_tolerance'), 'acceptance_tolerance_mm': None,
            'source_control_placement': 'initial_unfitted_candidate',
            'hole_radius_seed': 'ideal_periodic_cubic_curve_only_not_full_surface_fit',
            'outer_profile': 'nominal_circular_meridian_with_tangent_guard_rings_not_limit_fit',
            'support_roles': {'outer_planar': 'separate flat-support ring retained', 'outer_wall': 'two tangent guards retained',
                              'hole_planar': 'conditional omission requires complete-body evaluated A/B comparison'},
            'evidence': {'host_graph': 'pass', 'self_intersection': 'not_run', 'native': 'not_run',
                         'subdivision_L1_L2_L3': 'not_run', 'visual': 'not_run', 'user_approval': 'not_run'},
            'limitations': ['Single circular through-hole only', 'Initial hole16/outer32 family only',
                'No universal density/minimality or evaluated dimensional guarantee',
                'One east/south full-body insertion only; inserted curved-edge midpoint is an unfitted control candidate']}}
    if 'layout' in cfg:
        mesh['metadata']['subdivision_cage']['layout_policy'] = {
            'schema': cfg['layout']['schema'], 'macrogrid': 'frozen_axis_aligned',
            'exterior_correspondence': 'core_tangent_coordinates_with_corner_tangent_anchors',
            'hole_edit_scope': 'hole_rim_and_optional_support_only_with_ancestral_descendants',
            'frame_edit': 'distinct_new_plan_required'}
        mesh['metadata']['subdivision_cage']['fixed_frame_domain'] = deepcopy(d['fixed_frame'])
    if cfg.get('insertion_policy') == AXIS_INSERTION_POLICY:
        info = mesh['metadata']['subdivision_cage']
        info.update({'candidate_version': 5, 'constructor_schema': AXIS_CONSTRUCTOR_SCHEMA,
                     'constructor_version': 5,
                     'initial_top_plane_quads': 143 if cfg['hole_planar_support'] else 127,
                     'top_plane_candidate_quads': audit['region_face_counts']['top'],
                     'inserted_axis_loop_count': len(graph.get('semantic_inserted_loops', [])),
                     'insertion_policy': {'name': AXIS_INSERTION_POLICY,
                         'schema': 'axis-plane-strip-policy/1.0', 'maximum_insertions': 2,
                         'fraction_reference': 'immutable_base_common_crossed_edge_interval',
                         'minimum_spacing_mm': d['fixed_frame']['minimum_grid_spacing_mm'],
                         'whole_domain_evaluated_acceptance': 'not_run'},
                     'insertion_transactions': deepcopy(graph.get('insertion_transactions', []))})
        info['limitations'][-1] = ('At most two east/south complete-body axis-plane insertions; '
                                 'curved-edge placement remains an unfitted control candidate')
    if 'corner_columns' in cfg:
        info = mesh['metadata']['subdivision_cage']
        info.update({'candidate_version': 6, 'constructor_version': 6,
                     'constructor_schema': CORNER_CONSTRUCTOR_SCHEMA,
                     'initial_topology': {'hole_segments': 16, 'outer_segments': 36},
                     'initial_top_plane_quads': d['corner_base_audit']['region_face_counts']['top'],
                     'structural_corner_loop_count': len(graph['semantic_structural_loops']),
                     'structural_corner_placement': 'arithmetic_edge_midpoint',
                     'structural_corner_added_faces': 88,
                     'structural_corner_base_faces': d['corner_base_audit']['faces']})
        info['limitations'][1] = 'Explicit midpoint corner-column hole16/outer36 constructor family only'
    mesh['authored_structure'] = _manifest(mesh, p)
    mesh['authorship_sha256'] = mesh['authored_structure']['authorship_sha256']
    mesh['structure_schedule_revision'] = SCHEDULE_REVISION
    mesh['construction_sha256'] = fingerprint({'vertices_mm': mesh['vertices_mm'], 'faces': mesh['faces'],
        'face_provenance': mesh['face_provenance'], 'corner_normals': None, 'edge_creases': mesh['edge_creases'],
        'subdivision_modifier': mesh['subdivision_modifier'], 'control_loops': mesh['control_loops']})
    if _validate:
        validate_sparse_authored_identity(mesh)
    return mesh


def validate_sparse_authored_identity(mesh, manifest=None):
    """Rebuild the declarative schedule and verify actual semantic geometry.

    Reordered storage is accepted; relabelled vertices/faces or revised winding
    are not. Native arrays permit only exact float32 metre materialization, which
    does not replace the native adapter's exact saved-source binding checks.
    """
    manifest = mesh.get('authored_structure') if manifest is None else manifest
    required = {'schema_version', 'schedule_revision', 'vertex_map', 'face_map', 'semantic_control_loops',
        'parameter_binding', 'supported_edit_datum', 'alias_manifest', 'semantic_connectivity_sha256',
        'semantic_crease_sha256', 'edit_dependencies', 'authorship_sha256'}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest['schema_version'] != '1.0' or manifest['schedule_revision'] != SCHEDULE_REVISION:
        fail('SPARSE_MANIFEST_INVALID', 'Exact sparse schedule manifest required')
    binding = manifest['parameter_binding']
    if not isinstance(binding, dict) or set(binding) != {'constructor', 'parameters', 'design_sha256', 'coordinate_space'} or not isinstance(binding['parameters'], dict) or binding['constructor'] != 'quad.panel/sparse_control_cage' or binding['coordinate_space'] != 'object_local_mm' or binding['design_sha256'] != fingerprint(binding['parameters']):
        fail('SPARSE_PARAMETER_BINDING', 'Exact bound design parameters required')
    vertices = mesh.get('vertices_mm', mesh.get('vertices'))
    faces = mesh.get('faces')
    if not isinstance(vertices, (list, tuple)) or not isinstance(faces, (list, tuple)):
        fail('SPARSE_MESH_INVALID', 'Actual arrays required')
    for face in faces:
        if not isinstance(face, (list, tuple)) or len(face) != 4 or any(type(i) is not int or not 0 <= i < len(vertices) for i in face) or len(set(face)) != 4:
            fail('SPARSE_MESH_INVALID', 'Actual faces must contain four distinct integer indices')
    for row in mesh.get('edge_creases', []):
        if not isinstance(row, (list, tuple)) or len(row) != 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in row[:2]) or type(row[2]) not in (float, int) or not math.isfinite(row[2]) or not 0 <= row[2] <= 1:
            fail('SPARSE_CREASE', 'Actual crease must contain integer endpoints and a finite numeric weight')
    for name, count in [('vertex_map', len(vertices)), ('face_map', len(faces))]:
        mapping = manifest[name]
        if not isinstance(mapping, dict) or len(mapping) != count or any(type(i) is not int for i in mapping.values()) or set(mapping.values()) != set(range(count)):
            fail('SPARSE_IDENTITY_COVERAGE', 'Actual semantic maps must be bijections')
    # Feature ownership is caller-selected, while authored semantic addresses
    # are constructor-selected. Axis lineage binds the owner's port records.
    owner = (mesh['face_provenance'][0].get('feature_id', 'panel')
             if mesh.get('face_provenance') and 'sparse_graph' in mesh else 'panel')
    expected = build_sparse_panel(binding['parameters'], feature_id=owner, _validate=False)
    em = expected['authored_structure']
    if set(manifest['vertex_map']) != set(em['vertex_map']) or set(manifest['face_map']) != set(em['face_map']):
        fail('SPARSE_SEMANTIC_SCHEDULE', 'Authored addresses differ from the declarative sparse schedule')
    for key in required-{'vertex_map', 'face_map'}:
        if manifest[key] != em[key]:
            fail('SPARSE_SEMANTIC_SCHEDULE', 'Authored schedule metadata differs from reconstruction', field=key)
    graph = {'vertex_map': manifest['vertex_map'], 'face_map': manifest['face_map']}
    try:
        actual = semantic_mesh({'vertices_mm': vertices, 'faces': faces, 'edge_creases': mesh.get('edge_creases', [])}, graph)
    except (IndexError, KeyError, ValueError, TypeError):
        fail('SPARSE_MESH_INVALID', 'Actual connectivity cannot resolve to the authored schedule')
    wanted = semantic_mesh(expected)
    if actual['connectivity'] != wanted['connectivity'] or actual['creases'] != wanted['creases']:
        fail('SPARSE_SEMANTIC_CONNECTIVITY', 'Actual oriented connectivity or crease edges differ from bound construction')
    exact_host = 'sparse_graph' in mesh
    if exact_host:
        if mesh['sparse_graph']['vertex_map'] != manifest['vertex_map'] or mesh['sparse_graph']['face_map'] != manifest['face_map']:
            fail('SPARSE_SEMANTIC_SCHEDULE', 'Graph and persisted manifest identity maps differ')
        validate_sparse_mesh(mesh)
        ports = {row['role']: row['vertex_ids'] for row in _authored_loops(mesh['sparse_graph'])}
        if ports != {row['role']: row['vertex_ids'] for row in manifest['semantic_control_loops']}:
            fail('SPARSE_SEMANTIC_SCHEDULE', 'Graph ports differ from persisted authored rings')
        if binding['parameters'].get('sparse_cage', {}).get('insertion_policy') == AXIS_INSERTION_POLICY:
            keys = ['axis_strip_reference', 'semantic_inserted_loops', 'insertion_transactions',
                    'insertion_transaction', 'topology_epoch']
            if 'corner_columns' in binding['parameters']['sparse_cage']:
                keys.extend(['constructor_topology', 'semantic_structural_loops',
                             'structural_vertex_lineage', 'structural_face_lineage'])
            for key in keys:
                observed, wanted_metadata = mesh['sparse_graph'].get(key), expected['sparse_graph'].get(key)
                if key in ('semantic_inserted_loops', 'semantic_structural_loops'):
                    # Index permutations may change the current physical loop
                    # indices; their semantic order and plane proof must not.
                    observed = [{k: v for k, v in row.items() if k != 'vertex_indices'} for row in observed or []]
                    wanted_metadata = [{k: v for k, v in row.items() if k != 'vertex_indices'} for row in wanted_metadata or []]
                if observed != wanted_metadata:
                    fail('SPARSE_SEMANTIC_SCHEDULE', 'Axis policy graph metadata differs from bound reconstruction', field=key)
    if 'face_provenance' in mesh:
        if len(mesh['face_provenance']) != len(faces):
            fail('SPARSE_PROVENANCE', 'Actual provenance must cover every face')
        for sid, i in manifest['face_map'].items():
            row = mesh['face_provenance'][i]
            want = expected['face_provenance'][em['face_map'][sid]]
            if not isinstance(row, dict) or any(row.get(k) != value for k, value in want.items() if k != 'feature_id'):
                fail('SPARSE_PROVENANCE', 'Semantic face role differs from authored construction', face_id=sid)
    for sid, point in actual['coordinates'].items():
        expected_point = wanted['coordinates'][sid]
        if len(point) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in point):
            fail('SPARSE_COORDINATE_INVALID', 'Actual coordinates must be finite')
        for x, y in zip(point, expected_point):
            native = struct.unpack('f', struct.pack('f', y*.001))[0]*1000.0
            if x != y and (exact_host or x != native):
                fail('SPARSE_BOUND_COORDINATES', 'Actual semantic coordinates differ from the bound construction', vertex_id=sid)
    return {'status': 'pass', 'vertices': len(vertices), 'faces': len(faces),
            'control_loops': len(manifest['semantic_control_loops']), 'authorship_sha256': manifest['authorship_sha256'],
            'coordinate_policy': 'host_exact' if exact_host else 'exact_host_or_float32_metre_materialization',
            'native_extraction': 'not_run', 'qualification': 'not_run'}
