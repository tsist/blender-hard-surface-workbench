# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only binding of proposed structure.plan declarations to mesh witnesses.

This is an evidence boundary, not an execution or reference-approval gate. The
caller supplies every semantic correspondence and an independently held native
binding. No file is opened, no geometry is built and no nearest/positional match
is used. Native provenance and source-file authenticity remain external duties.
"""
from __future__ import annotations
from copy import deepcopy
import json
import math
import struct

from .structure_contract import validate_request, fingerprint as request_fingerprint
from . import structure_kernel as k
from . import subd_panel_identity as author
from .io import RuntimeFailure

BINDING_VERSION = 'structure-plan-binding/1.0'
NATIVE_VERSION = 'native-control-structure/1.0'
SIGNATURES = ('geometry_signature', 'topology_signature', 'attribute_signature', 'structure_signature')
GROUPS = ('regions', 'loops', 'boundary_ports')
NORMAL_AXES = {'local_positive_z': (0., 0., 1.), 'local_negative_z': (0., 0., -1.)}
TRANSPORT_POLICY = 'exact_ieee754_float32_authored_metres_v1'
FRAME_ROUNDOFF = 64 * math.ulp(1.0)


def _fail(code, message, **details):
    raise k.StructureError(code, message, **details)


def _bounded_json(value):
    # Reports may contain a full 992-element witness and its author manifest.
    todo = [(value, 0)]; count = 0
    while todo:
        node, depth = todo.pop(); count += 1
        if depth > 32 or count > 800000:
            _fail('PLAN_BINDING_LIMIT', 'Binding input exceeds host nesting/node limits')
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                _fail('PLAN_BINDING_INPUT', 'All binding object keys must be strings')
            todo.extend((v, depth+1) for v in node.values())
        elif isinstance(node, list):
            todo.extend((v, depth+1) for v in node)
        elif node is None or type(node) in (bool, int, float, str):
            if type(node) in (int, float): k._number(node, 'binding number')
        else:
            _fail('PLAN_BINDING_INPUT', 'Binding inputs must contain only finite JSON values')
    if len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')) > 8 * 1024 * 1024:
        _fail('PLAN_BINDING_LIMIT', 'Binding input exceeds the 8 MiB JSON limit')


def _exact(a, b):
    """Numeric JSON equality without letting true/false stand in for 1/0."""
    if type(a) is bool or type(b) is bool: return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return a == b
    if type(a) is not type(b): return False
    if isinstance(a, dict): return set(a) == set(b) and all(_exact(a[x], b[x]) for x in a)
    if isinstance(a, list): return len(a) == len(b) and all(_exact(x, y) for x, y in zip(a, b))
    return a == b


def _same_nominal(actual, expected, path):
    if not _exact(actual, expected):
        _fail('PLAN_PARAMETER_MISMATCH', 'Plan must equal the exact authored nominal value; design tolerance is not used', field=path)


def _parameters(request, authorship):
    binding = authorship.get('parameter_binding')
    if not isinstance(binding, dict) or set(binding) != {'constructor', 'parameters', 'design_sha256', 'coordinate_space'}:
        _fail('PLAN_PARAMETERS_MISSING', 'Complete authored parameter binding is required')
    p = binding['parameters']
    if (not isinstance(p, dict) or binding['constructor'] != 'quad.panel/subd_control_cage'
            or binding['coordinate_space'] != 'object_local_mm' or binding['design_sha256'] != author.fingerprint(p)):
        _fail('PLAN_PARAMETERS_INVALID', 'Authored parameter binding or fingerprint differs')
    d, t, evaluation = request['design'], request['topology'], request['evaluation']
    try:
        pairs = [
            (p['op'], 'quad.panel', 'operation'), (p['id'], d['asset_id'], 'design.asset_id'),
            (p['size'], d['outline']['size_mm'], 'design.outline.size_mm'),
            (p['center'], d['outline']['center_mm'], 'design.outline.center_mm'),
            (p['corner_radius'], d['outline']['corner_radius_mm'], 'design.outline.corner_radius_mm'),
            (p['edge_bevel'], d['edge_roundover_mm'], 'design.edge_roundover_mm'),
            ([p['z_min'], p['z_max']], d['z_range_mm'], 'design.z_range_mm'),
            (p['topology_strategy'], t['strategy'], 'topology.strategy'),
            (p['local_patch_bounds'], t['local_patch_bounds_mm'], 'topology.local_patch_bounds_mm'),
            (p['subdivision_cage']['method'], evaluation['method'], 'evaluation.method'),
            (p['subdivision_cage']['hole_segments'], t['hole_control_count'], 'topology.hole_control_count'),
            # A control report binds one actual preview level. It cannot prove
            # an unrun multi-level evaluation batch by mere set membership.
            ([p['subdivision_cage']['preview_levels']], evaluation['levels'], 'evaluation.levels'),
        ]
        if not isinstance(p['holes'], list) or len(p['holes']) != 1 or not isinstance(p['holes'][0], dict):
            _fail('PLAN_PARAMETER_MISMATCH', 'Exactly one authored circular hole is required')
        hole, declared = p['holes'][0], d['holes'][0]
        pairs += [(hole['id'], declared['id'], 'design.holes.id'),
                  (hole['kind'], declared['kind'], 'design.holes.kind'),
                  (hole['center'], declared['center_mm'], 'design.holes.center_mm'),
                  (hole['radius'], declared['radius_mm'], 'design.holes.radius_mm')]
        if hole.get('counterbore') or p.get('lips'):
            _fail('PLAN_PARAMETER_MISMATCH', 'Counterbores and additional lips are outside this binding domain')
    except (KeyError, TypeError) as exc:
        _fail('PLAN_PARAMETERS_MISSING', 'An exact plan value is absent from authored parameters', field=str(exc))
    optional = [('outline_control_count', t['outline_control_count'], 'topology.outline_control_count'),
                ('master_face_policy', t['master_face_policy'], 'topology.master_face_policy'),
                ('triangle_exceptions', t['triangle_exceptions'], 'topology.triangle_exceptions'),
                ('blender_version', evaluation['blender_version'], 'evaluation.blender_version')]
    missing = []
    for key, value, path in optional:
        if key in p: pairs.append((p[key], value, path))
        else: missing.append(path)
    declared_support=t.get('bore_support_width_mm')
    actual_support=p.get('subdivision_cage',{}).get('bore_support_width')
    if declared_support is not None or actual_support is not None:
        pairs.append((actual_support,declared_support,'topology.bore_support_width_mm'))
    declared_join=t.get('outer_join_policy')
    actual_join=p.get('subdivision_cage',{}).get('outer_join_policy')
    if declared_join is not None or actual_join is not None:
        pairs.append((actual_join,declared_join,'topology.outer_join_policy'))
    for actual, expected, path in pairs: _same_nominal(actual, expected, path)
    return {'status': 'pass', 'comparison': 'exact_nominal_no_design_tolerance',
            'checked_fields': [path for _, _, path in pairs], 'unavailable_parameter_fields': missing}


def _cycle_edges(ids):
    return frozenset(tuple(sorted((a, b))) for a, b in zip(ids, ids[1:]+ids[:1]))


def _cycle_equal(a, b, reverse=False):
    if len(a) != len(b) or set(a) != set(b): return False
    candidate = list(reversed(a)) if reverse else list(a)
    start = candidate.index(b[0]); candidate = candidate[start:]+candidate[:start]
    return candidate == list(b)


def _position(actual, nominal):
    # This is a discrete, documented representation transform, not a distance
    # tolerance. Native local millimetres come from float32 stored metres.
    try:
        transported = struct.unpack('!f', struct.pack('!f', float(nominal)*.001))[0]*1000.
    except (OverflowError, ValueError, struct.error):
        _fail('PLAN_TRANSPORT_UNSUPPORTED', 'Plan position exceeds native float32 transport range')
    return math.isfinite(transported) and (actual == nominal or actual == transported)


def _mapping(request, mapping):
    k._keys(mapping, ('schema_version', 'coordinate_space', 'feature_ownership', *GROUPS), label='plan mapping')
    if mapping['schema_version'] != BINDING_VERSION or mapping['coordinate_space'] != 'object_local_mm':
        _fail('PLAN_MAPPING_UNSUPPORTED', 'Explicit current-version object-local millimetre mapping required')
    owners = mapping['feature_ownership']
    features = {row['feature_id'] for row in request['structure']['regions']}
    if not isinstance(owners, dict) or set(owners) != features:
        _fail('PLAN_FEATURE_OWNERSHIP', 'Explicit ownership mapping must cover exactly declared region features')
    for value in owners.values(): k._id(value)
    for group in GROUPS:
        entries = mapping[group]
        declared = {row['id'] for row in request['structure'][group]}
        if not isinstance(entries, dict) or set(entries) != declared:
            _fail('PLAN_MAPPING_COVERAGE', 'Mapping must cover exactly the declared entities', group=group)
        targets = []
        for plan_id, row in entries.items():
            required = {'regions': ('actual_region_id', 'actual_surface_id', 'face_ids'),
                        'loops': ('actual_loop_id', 'orientation_normal'),
                        'boundary_ports': ('actual_port_id', 'transform', 'point_order')}[group]
            k._keys(row, required, label='mapping '+plan_id)
            target = row[required[0]]; k._id(target); targets.append(target)
            if group == 'regions':
                k._id(row['actual_surface_id'])
                ids = [k._id(x) for x in k._list(row['face_ids'], 'mapped face IDs', k.MAX_FACES)]
                if not ids or len(ids) != len(set(ids)):
                    _fail('PLAN_REGION_OWNERSHIP', 'Mapped region faces must be explicit, nonempty and unique')
            elif group == 'loops':
                if not isinstance(row['orientation_normal'], str) or row['orientation_normal'] not in NORMAL_AXES:
                    _fail('PLAN_LOOP_NORMAL_UNSUPPORTED', 'Name local_positive_z or local_negative_z explicitly')
            else:
                if row['transform'] not in ('identity', 'reverse_order'):
                    _fail('PLAN_PORT_TRANSFORM_UNSUPPORTED', 'Only explicit identity/reverse_order seam transport is supported')
                ids = [k._id(x) for x in k._list(row['point_order'], 'mapped point order')]
                if len(ids) < 3 or len(ids) != len(set(ids)):
                    _fail('PLAN_PORT_ORDER_MISMATCH', 'Port mapping requires distinct ordered semantic points')
        if len(targets) != len(set(targets)):
            _fail('PLAN_MAPPING_AMBIGUOUS', 'Multiple declarations cannot alias one actual entity', group=group)


def bind_structure_plan(request, native_report, mapping, *, expected_binding):
    """Bind all declarations or raise ContractError/StructureError, with no writes.

    Mapping shape (all dictionaries keyed by the *declared* entity ID):
    feature_ownership: {declared feature ID: actual constructor feature ID}
    regions: {id: {actual_region_id, actual_surface_id,
                   face_ids: [all actual semantic face IDs]}}
    loops: {id: {actual_loop_id, orientation_normal: local_positive_z|local_negative_z}}
    boundary_ports: {id: {actual_port_id, transform: identity|reverse_order,
                         point_order: [actual anchored semantic vertex IDs]}}

    Actual entities may have different IDs. Loop ownership is proved using
    complete real boundary edge cycles, including author-loop aliases whose IDs
    differ from the adapter's boundary-loop IDs. Port seam start is explicit in
    point_order. Interfaces/minimum-gap claims are unsupported and rejected.
    """
    r = validate_request(request)
    for value in (native_report, mapping, expected_binding): _bounded_json(value)
    if not isinstance(native_report, dict) or native_report.get('schema_version') != NATIVE_VERSION or native_report.get('status') != 'pass':
        _fail('PLAN_NATIVE_REPORT_INVALID', 'A full current native-control report is required')
    binding = native_report.get('binding')
    if not isinstance(expected_binding, dict) or not expected_binding or not isinstance(binding, dict) or not _exact(binding, expected_binding):
        _fail('PLAN_NATIVE_BINDING_MISMATCH', 'Report differs from the independently held expected binding')
    required_binding = {'schema_version', 'object_id', 'data_id', 'mesh_state', 'schedule_revision',
                        'topology_epoch', 'authorship_sha256', 'construction_sha256', 'manifest_sha256',
                        'context_sha256', 'source_binding_sha256', *SIGNATURES}
    if (set(binding) != required_binding or binding['schema_version'] != NATIVE_VERSION or binding['mesh_state'] != 'control'
            or native_report.get('identity_binding_status') != 'bound'):
        _fail('PLAN_NATIVE_BINDING_INVALID', 'A complete externally bound control-mesh receipt is required')
    for key in ('object_id', 'data_id', 'schedule_revision'): k._id(binding[key])
    if binding['object_id'] not in r['source']['object_ids']:
        _fail('PLAN_OBJECT_SCOPE_MISMATCH', 'Actual object is outside the declared source object scope')
    extraction = native_report.get('native_extraction')
    required_extraction = {'status': 'pass', 'source': 'raw object.data', 'coordinate_space': 'object_local_mm',
                          'actual_edges_verified': True, 'identity_transport': 'validated_POINT_FACE_INT_slots',
                          'crease_source': 'actual_EDGE_FLOAT_endpoint_values', 'signature_quantization': 'none'}
    if not _exact(extraction, required_extraction):
        _fail('PLAN_NATIVE_EXTRACTION_UNSUPPORTED', 'Unsupported extraction coordinate/identity/edge/crease descriptor')
    if type(binding['topology_epoch']) is not int or binding['topology_epoch'] < 0:
        _fail('PLAN_NATIVE_BINDING_INVALID', 'Topology epoch must be a nonnegative integer')
    for key in required_binding - {'schema_version', 'object_id', 'data_id', 'mesh_state', 'schedule_revision', 'topology_epoch'}:
        value = binding[key]
        if not isinstance(value, str) or len(value) != 64 or any(x not in '0123456789abcdef' for x in value):
            _fail('PLAN_NATIVE_BINDING_INVALID', 'An external binding fingerprint is absent or invalid', field=key)
    witness = native_report.get('witness'); k._keys(witness, ('mesh', 'structure'), label='native witness')
    mesh, structure = witness['mesh'], witness['structure']
    actual = k.validate_structure(mesh, structure)
    reported = native_report.get('kernel_report')
    if not isinstance(reported, dict) or reported.get('status') != 'pass' or any(actual[key] != reported.get(key) or actual[key] != binding[key] for key in SIGNATURES):
        _fail('PLAN_WITNESS_SIGNATURE_MISMATCH', 'Fresh raw-witness signatures differ from the report or external binding')
    authored = native_report.get('authorship')
    if not isinstance(authored, dict) or any(authored.get(key) != structure[key] for key in ('vertex_map', 'face_map')):
        _fail('PLAN_AUTHORSHIP_MISMATCH', 'Authored identities must resolve every actual witness index')
    try:
        digest = author.fingerprint(author.authorship_payload(authored))
    except (KeyError, TypeError, ValueError) as exc:
        _fail('PLAN_AUTHORSHIP_MISMATCH', 'Full authored manifest is required', reason=str(exc))
    if digest != binding['authorship_sha256'] or authored.get('authorship_sha256') != digest or authored.get('schedule_revision') != binding['schedule_revision']:
        _fail('PLAN_AUTHORSHIP_MISMATCH', 'Authored payload differs from external author identity')
    precision = native_report.get('materialization')
    if not isinstance(precision, dict) or precision.get('status') != 'pass' or precision.get('policy') != TRANSPORT_POLICY or precision.get('coordinate_space') != 'object_local' or precision.get('native_signature_quantization') != 'none':
        _fail('PLAN_TRANSPORT_UNSUPPORTED', 'Explicit unquantized float32-metre transport evidence required')
    parameter_report = _parameters(r, authored)
    try:
        author_report = author.validate_authored_identity({'vertices_mm': mesh['vertices'], 'faces': mesh['faces'],
            'edge_creases': mesh.get('edge_creases', [])}, authored)
    except RuntimeFailure as exc:
        _fail('PLAN_AUTHORED_SCHEDULE_REJECTED', str(exc), author_code=exc.code)
    if actual['counts']['triangles']:
        _fail('PLAN_FACE_POLICY_MISMATCH', 'The plan permits no triangle exceptions')
    if r['structure']['interfaces']:
        _fail('PLAN_INTERFACES_UNSUPPORTED', 'Shared-boundary/thickness/minimum-gap certification is outside this helper')
    _mapping(r, mapping)
    declared = {group: {row['id']: row for row in r['structure'][group]} for group in GROUPS}
    registry = {group: {row['id']: row for row in structure[group]} for group in GROUPS}
    allowed_features = set(mapping['feature_ownership'].values())
    if any(row['feature_id'] not in allowed_features for row in registry['regions'].values()):
        _fail('PLAN_FEATURE_OWNERSHIP', 'Actual mesh contains feature ownership outside the bounded plan')
    resolved = {group: {} for group in GROUPS}
    for group in GROUPS:
        key = {'regions': 'actual_region_id', 'loops': 'actual_loop_id', 'boundary_ports': 'actual_port_id'}[group]
        for plan_id, row in mapping[group].items():
            if row[key] not in registry[group]:
                _fail('PLAN_ENTITY_MISSING', 'Mapped actual entity does not exist; no implicit match is attempted', declared_id=plan_id, actual_id=row[key])
            resolved[group][plan_id] = registry[group][row[key]]
    region_evidence, loop_evidence, port_evidence = [], [], []
    for plan_id, region in resolved['regions'].items():
        plan = declared['regions'][plan_id]; supplied = mapping['regions'][plan_id]
        if region['feature_id'] != mapping['feature_ownership'][plan['feature_id']] or region['role'] != supplied['actual_surface_id'] or set(region['face_ids']) != set(supplied['face_ids']):
            _fail('PLAN_REGION_OWNERSHIP', 'Actual region faces, feature or surface ownership contradict the plan', region_id=plan_id)
        # The legacy constructor owns bore faces under its step feature, not
        # the design hole ID. Only the pinned bore-wall semantic domain may be
        # reassociated to that declared hole; a caller cannot relabel the shell.
        if plan['feature_id'] == r['design']['holes'][0]['id'] and (
                region['role'] != 'bore_wall' or len(region['face_ids']) != r['topology']['hole_control_count']
                or any(not key.startswith('bore_wall/sector:') for key in region['face_ids'])):
            _fail('PLAN_FEATURE_OWNERSHIP', 'Only proven bore-wall faces can bind the declared hole feature', region_id=plan_id)
        boundaries = [registry['loops'][key] for key in region['boundary_loop_ids']]
        unmatched = {_cycle_edges(row['vertex_ids']): row['id'] for row in boundaries}
        if len(unmatched) != len(boundaries):
            _fail('PLAN_REGION_BOUNDARY_MISMATCH', 'Actual region boundary cycles are ambiguous')
        for loop_id in plan['loop_ids']:
            loop = resolved['loops'][loop_id]; edges = _cycle_edges(loop['vertex_ids'])
            boundary_id = unmatched.pop(edges, None)
            if boundary_id is None:
                _fail('PLAN_LOOP_OWNERSHIP', 'Mapped loop must be one complete actual boundary cycle of its region', loop_id=loop_id)
            boundary = registry['loops'][boundary_id]
            if not (_cycle_equal(loop['vertex_ids'], boundary['vertex_ids']) or _cycle_equal(loop['vertex_ids'], boundary['vertex_ids'], reverse=True)):
                _fail('PLAN_LOOP_OWNERSHIP', 'Boundary edge membership did not prove ordered cycle correspondence', loop_id=loop_id)
            lp = declared['loops'][loop_id]
            named = next((row for row in authored['semantic_control_loops'] if row['role'] == lp['role']), None)
            if named is not None and _cycle_edges(named['vertex_ids']) != edges:
                _fail('PLAN_LOOP_ROLE_MISMATCH', 'A known author role cannot name a different actual cycle', loop_id=loop_id)
            if loop['closed'] is not True or len(loop['vertex_ids']) != lp['expected_vertex_count']:
                _fail('PLAN_LOOP_COUNT_MISMATCH', 'Actual closed-cycle cardinality differs', loop_id=loop_id)
            normal_name = mapping['loops'][loop_id]['orientation_normal']; normal = NORMAL_AXES[normal_name]
            points = [mesh['vertices'][structure['vertex_map'][key]] for key in loop['vertex_ids']]
            orientation = k._dot(k._normal(points), normal)
            threshold = 2*structure['tolerances']['area_mm2']
            if abs(orientation) <= threshold or ('ccw' if orientation > 0 else 'cw') != lp['orientation']:
                _fail('PLAN_LOOP_ORIENTATION_MISMATCH', 'Actual winding differs relative to the explicitly named normal', loop_id=loop_id)
            loop_evidence.append({'declared_id': loop_id, 'actual_id': loop['id'], 'boundary_cycle_id': boundary_id,
                                  'declared_role': lp['role'], 'actual_role': loop['role'],
                                  'orientation_normal': normal_name, 'orientation': lp['orientation'], 'vertex_count': len(points)})
        if unmatched:
            _fail('PLAN_REGION_BOUNDARY_MISMATCH', 'Declared loops omit actual region boundaries', region_id=plan_id)
        region_evidence.append({'declared_id': plan_id, 'actual_id': region['id'], 'feature_id': region['feature_id'],
                                'declared_feature_id': plan['feature_id'], 'declared_surface_id': plan['surface_id'],
                                'surface_id': region['role'], 'face_ids': sorted(region['face_ids'])})
    for plan_id, port in resolved['boundary_ports'].items():
        plan, mp = declared['boundary_ports'][plan_id], mapping['boundary_ports'][plan_id]
        loop = resolved['loops'][plan['loop_id']]; region = resolved['regions'][plan['region_id']]
        allowed = {'identity' if x == 'identity' else 'reverse_order' for x in port['allowed_transitions'] if x in ('identity', 'reverse')}
        if not set(plan['allowed_transforms']) <= allowed or mp['transform'] not in plan['allowed_transforms']:
            _fail('PLAN_PORT_TRANSFORM_UNSUPPORTED', 'Declared seam transforms are not supported by the actual port', port_id=plan_id)
        if (port['region_id'] != region['id'] or mp['point_order'] != port['point_order']
                or not _cycle_equal(loop['vertex_ids'], port['point_order'], reverse=mp['transform'] == 'reverse_order')):
            _fail('PLAN_PORT_ORDER_MISMATCH', 'Actual port owner, anchored order or explicit seam transform differs', port_id=plan_id)
        frame = port['frame']
        if (not all(_position(a, b) for a, b in zip(frame['origin_mm'], plan['origin_mm']))
                or k._norm(k._sub(frame['normal'], plan['normal'])) > FRAME_ROUNDOFF):
            _fail('PLAN_PORT_FRAME_MISMATCH', 'Plan port frame differs from actual frame; only explicit coordinate transport is allowed', port_id=plan_id)
        points = [mesh['vertices'][structure['vertex_map'][key]] for key in port['point_order']]
        scale = max(1., *(abs(v) for point in points for v in point), *(abs(v) for v in frame['origin_mm']))
        numeric_plane_error = 128 * math.ulp(scale)
        if any(abs(k._dot(k._sub(point, frame['origin_mm']), frame['normal'])) > numeric_plane_error for point in points):
            _fail('PLAN_PORT_NOT_PLANAR', 'Actual port points are nonplanar beyond computational roundoff', port_id=plan_id)
        port_evidence.append({'declared_id': plan_id, 'actual_id': port['id'], 'transform': mp['transform'],
                              'point_order': list(port['point_order']), 'frame': deepcopy(frame), 'seam_policy': plan['seam_policy']})
    return {'schema_version': BINDING_VERSION, 'status': 'pass', 'evidence_kind': 'host_only_declaration_binding',
            'request_sha256': request_fingerprint(r), 'mapping_sha256': k.fingerprint(mapping),
            'expected_binding': deepcopy(binding), 'kernel_report': actual, 'authored_schedule': author_report, 'parameter_binding': parameter_report,
            'regions': region_evidence, 'loops': loop_evidence, 'boundary_ports': port_evidence,
            'transport_precision': {'position_policy': 'exact_or_float32_metres_then_mm', 'native_policy': TRANSPORT_POLICY,
                                    'uses_design_tolerance': False, 'normal_roundoff': FRAME_ROUNDOFF,
                                    'planarity_roundoff_policy': '128_ulp_coordinate_scale', 'signature_quantization': 'none'},
            'reference_approval': 'required' if r['reference_contract']['state'] == 'proposed' else 'external_verification_required',
            'construction_authorized': False, 'qualification': 'not_run', 'side_effects': [],
            'not_checked': ['native_execution_authenticity', 'source_file_authenticity', 'reference_file_authenticity',
                            'reference_approval', 'implementation_file_authenticity', 'nominal_geometric_dimensions',
                            'subdivision_evaluation', 'interfaces', 'minimum_gap', 'edit_execution', 'saved_reopen',
                            'visual_quality', 'production_qualification']}
