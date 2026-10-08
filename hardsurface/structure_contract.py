"""Host-only structure.plan v1 contract. No scene or filesystem writes.

Shape validation and caller declarations are not approval, native qualification,
mesh evidence or production authority. This first batch deliberately cannot grant
any of those. The existing dependency-free JSON boundary is reused read-only.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path, PurePosixPath

from . import contract as _c

ContractError = _c.ContractError
SCHEMA_VERSION = 'structure-plan/1.0'
CONTRACT_VERSION = '1.0.0'
OPERATION_VERSION = '0.1.0'
EVIDENCE_VERSION = '1.0.0'
DOMAIN_ID = 'planar.rounded_panel.single_sharp_circle.dev3'
FIRST_BATCH_STATUS = {
    'm0': 'pending', 'native': 'not_run', 'visual': 'not_run',
    'reopen': 'not_run', 'holdout': 'not_run', 'production_qualified': False,
}

obj, array, enum, const = _c.obj, _c.array, _c.enum, _c.const
number, integer, string, union = _c.number, _c.integer, _c.string, _c.union
ID, SHA = _c.IDENT, _c.SHA
# One regex avoids oneOf ambiguity for UUIDs that also satisfy symbolic IDENT.
OBJECT_ID = string(pattern=r'^(?:[A-Za-z][A-Za-z0-9_.:-]{0,95}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$',maxLength=96)
TEXT = string(minLength=1, maxLength=2048)
FILE_PATH = string(minLength=1, maxLength=4096)
POS = number(exclusiveMinimum=0)
NONNEG = number(minimum=0)
V2, V3 = array(number(), 2, 2), array(number(), 3, 3)
FILE = obj({'file': FILE_PATH, 'sha256': SHA, 'bytes': integer(minimum=1)})
NULL_FILE = union(FILE, const(None))
IDENTITY = obj({
    'request_id': ID, 'project_id': ID, 'contract_version': const(CONTRACT_VERSION),
    'operation_version': const(OPERATION_VERSION), 'evidence_version': const(EVIDENCE_VERSION),
    'implementation_id': ID, 'implementation_sha256': SHA,
})
REFERENCE_FILE = obj({'id': ID, 'role': enum('appearance', 'dimensions', 'detail', 'checklist'), 'artifact': FILE})
APPROVAL = obj({
    'authority': const('user'), 'source_message_id': TEXT, 'evidence': FILE,
    'reference_package_sha256': SHA, 'design_sha256': SHA,
    'project_id': ID,
    'verification': const('external_verification_required'),
})
REFERENCE = obj({
    'state': enum('proposed', 'approval_evidence_supplied'),
    'package_id': ID, 'version': TEXT, 'package_sha256': SHA,
    'artifacts': array(REFERENCE_FILE, 0, 32),
    'conflicts': array(obj({'id': ID, 'description': TEXT}), 0, 32),
    'approval_evidence': union(APPROVAL, const(None)),
})
HOLE = obj({'id': ID, 'kind': const('circle'), 'rim': const('sharp'), 'center_mm': V2, 'radius_mm': POS})
DESIGN = obj({
    'asset_id': ID, 'parent_surface': const('plane'), 'length_unit': const('mm'),
    'outline': obj({'kind': const('rounded_rectangle'), 'center_mm': V2,
                    'size_mm': array(POS, 2, 2), 'corner_radius_mm': POS}),
    'z_range_mm': V2, 'edge_roundover_mm': POS, 'holes': array(HOLE, 1, 1),
})
TOPOLOGY = obj({
    'strategy': const('subd_control_cage'),
    'hole_control_count': integer(minimum=24, maximum=24),
    'outline_control_count': integer(minimum=48, maximum=48),
    'local_patch_bounds_mm': array(number(), 4, 4),
    'master_face_policy': const('quad_preferred_zero_ngons'),
    'triangle_exceptions': array(obj({'id': ID, 'reason': TEXT}), 0, 0),
})
TOPOLOGY['properties']['bore_support_width_mm'] = POS
TOPOLOGY['properties']['outer_join_policy'] = enum('joined_arc_v1','joined_arc_v2')
TOPOLOGY['if'] = {'required':['outer_join_policy']}
TOPOLOGY['then'] = {'required':['bore_support_width_mm']}

EVALUATION = obj({
    'method': const('CATMULL_CLARK'), 'levels': array(integer(minimum=0, maximum=3), 1, 4, uniqueItems=True),
    'blender_version': string(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$', maxLength=32),
    'settings_status': const('proposed'),
})
REGION = obj({'id': ID, 'role': ID, 'surface_id': ID, 'feature_id': ID,
              'loop_ids': array(ID, 0, 32, uniqueItems=True)})
LOOP = obj({'id': ID, 'role': ID, 'region_id': ID, 'closed': const(True),
            'orientation': enum('cw', 'ccw'), 'expected_vertex_count': integer(minimum=3, maximum=1000000)})
PORT = obj({'id': ID, 'loop_id': ID, 'region_id': ID, 'origin_mm': V3, 'normal': V3,
            'seam_policy': const('exact_ordered_match'),
            'allowed_transforms': array(enum('identity', 'reverse_order'), 1, 2, uniqueItems=True)})
INTERFACE = obj({'id': ID, 'port_a': ID, 'port_b': ID,
                 'relation': enum('shared_boundary', 'thickness_pair'),
                 'minimum_gap_mm': NONNEG})
STRUCTURE = obj({
    'declaration_status': const('unverified'), 'regions': array(REGION, 0, 128),
    'loops': array(LOOP, 0, 256), 'boundary_ports': array(PORT, 0, 256),
    'interfaces': array(INTERFACE, 0, 128),
})
PARAMETERS = ('hole.center_x_mm', 'hole.center_y_mm', 'hole.radius_mm', 'panel.thickness_mm', 'panel.edge_roundover_mm')
EDIT = obj({
    'target_feature_ids': array(ID, 1, 16, uniqueItems=True),
    'allowed_parameters': array(enum(*PARAMETERS), 0, len(PARAMETERS), uniqueItems=True),
    'parameter_ranges': array(obj({'parameter': enum(*PARAMETERS), 'minimum': number(), 'maximum': number()}), 0, len(PARAMETERS)),
    'affected_region_ids': array(ID, 0, 128, uniqueItems=True),
    'protected_object_ids': array(OBJECT_ID, 0, 128, uniqueItems=True),
    'invariants': array(enum('non_target_unchanged', 'outside_patch_unchanged', 'semantic_identity'), 1, 3, uniqueItems=True),
    'ambiguity_policy': const('stop'), 'selection_invalidation': const('invalidate_dependents'),
})
WORK_UNIT = obj({'id': ID, 'operation': const('structure.plan'),
                 'depends_on': array(ID, 0, 128, uniqueItems=True),
                 'input_ports': array(ID, 0, 256, uniqueItems=True),
                 'output_ports': array(ID, 0, 256, uniqueItems=True),
                 'checkpoint': const('none_read_only')})


def _tolerance(unit):
    return obj({'status': const('proposed'), 'value': union(NONNEG, const(None)),
                'unit': const(unit), 'basis': TEXT})


QUALITY = obj({
    'tolerances': obj({
        'nominal_dimensions': _tolerance('mm'), 'explicit_arc_chord': _tolerance('mm'),
        'subd_shape': _tolerance('mm'), 'screen_observation': _tolerance('px'),
    }),
    'budget_limits': obj({
        'wall_seconds': POS, 'peak_ram_bytes': integer(minimum=1),
        'control_faces': integer(minimum=1, maximum=1000000000), 'evaluated_faces': integer(minimum=1, maximum=1000000000),
        'cpu_threads': integer(minimum=1, maximum=1024),
    }),
    'estimates': obj({
        'status': const('unmeasured_estimate'), 'basis': TEXT,
        'wall_seconds': union(POS, const(None)), 'peak_ram_bytes': union(integer(minimum=1), const(None)),
        'control_faces': union(integer(minimum=1, maximum=1000000000), const(None)),
    }),
    'required_evidence': array(enum('source_mesh', 'evaluated_mesh', 'dimensions', 'visual', 'edit', 'reopen', 'holdout'), 7, 7, uniqueItems=True),
})
REQUEST = obj({
    'schema_version': const(SCHEMA_VERSION), 'operation': const('structure.plan'),
    'execution_mode': const('dry_run'), 'requested_usage': enum('development', 'production'),
    'identity': IDENTITY,
    'source': obj({'kind': const('saved_snapshot'), 'snapshot': FILE,
                   'save_state': const('saved'), 'object_ids': array(OBJECT_ID, 1, 128, uniqueItems=True)}),
    'reference_contract': REFERENCE,
    'asset_profile': obj({
        'route': enum('film_product', 'realtime'), 'length_unit': const('mm'),
        'delivery_layers': array(enum('editable_master', 'evaluated_high'), 1, 2, uniqueItems=True),
        'observation': obj({'status': const('proposed'), 'camera_reference': NULL_FILE,
                            'resolution_px': union(array(integer(minimum=1, maximum=65536), 2, 2), const(None)),
                            'view_ids': array(ID, 0, 32, uniqueItems=True)}),
    }),
    'design': DESIGN, 'topology': TOPOLOGY, 'evaluation': EVALUATION,
    'structure': STRUCTURE, 'edit_contract': EDIT,
    'work_units': array(WORK_UNIT, 1, 128), 'quality_and_budget': QUALITY,
    'output': obj({'candidate_file': FILE_PATH, 'evidence_directory': FILE_PATH,
                   'publication': const('candidate_only'), 'overwrite': const(False)}),
})


def schema():
    """Return an isolated JSON Schema; no implementation or approval side effects."""
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            '$id': 'urn:hardsurface:structure-plan:1.0',
            'title': 'Hard Surface host-only structure.plan 1.0',
            'description': 'Declarations only. Host validation never grants reference approval or production qualification.',
            **copy.deepcopy(REQUEST)}


def fingerprint(value):
    """SHA-256 of bounded canonical JSON, preserving array order; not approval."""
    return _c.fingerprint(value)


def _fail(message, path='$', code='INVALID_STRUCTURE_REQUEST'):
    raise ContractError(code, message, path)


def _path(value, location):
    p = PurePosixPath(value)
    if (not p.is_absolute() or value.startswith('//') or p.as_posix() != value or '..' in p.parts
            or '\\' in value or '\x00' in value or '://' in value or value == '/'):
        _fail('Use a canonical absolute POSIX path without traversal', location, 'INVALID_PATH')
    return Path(value)


def _aliases(a, b):
    """Resolve existing symlinks and hardlinks without writing/creating paths."""
    try:
        if a.resolve(strict=False) == b.resolve(strict=False):
            return True
        if a.exists() and b.exists():
            return a.samefile(b)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail('Cannot safely resolve path identity: ' + str(exc), code='PATH_IDENTITY_UNVERIFIED')
    return False


def _all_files(value, location='$'):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = location + '.' + key
            if key == 'file':
                found.append((_path(item, path), path))
            else:
                found.extend(_all_files(item, path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(_all_files(item, f'{location}[{i}]'))
    return found


def _domain_check(r):
    d, t = r['design'], r['topology']
    w, h = d['outline']['size_mm']
    cx, cy = d['outline']['center_mm']
    radius = d['outline']['corner_radius_mm']
    lo, hi = d['z_range_mm']
    bevel = d['edge_roundover_mm']
    derived = [hi - lo, cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2]
    if not all(math.isfinite(x) for x in derived):
        _fail('Derived dimensions must remain finite', '$.design', 'UNSUPPORTED_DOMAIN')
    if not lo < hi or not radius < min(w, h) / 2 or not bevel < min(radius / 4, (hi - lo) / 3):
        _fail('Rounded panel/roundover dimensions are outside the existing candidate domain', '$.design', 'UNSUPPORTED_DOMAIN')
    x0, y0, x1, y1 = t['local_patch_bounds_mm']
    if not (cx - w / 2 + radius < x0 < x1 < cx + w / 2 - radius
            and cy - h / 2 + radius < y0 < y1 < cy + h / 2 - radius):
        _fail('Fixed patch must be strictly inside the outline corner-center rectangle', '$.topology.local_patch_bounds_mm', 'UNSUPPORTED_DOMAIN')
    if not .75 <= (x1 - x0) / (y1 - y0) <= 1.5:
        _fail('Existing candidate patch aspect domain is 0.75..1.5', '$.topology.local_patch_bounds_mm', 'UNSUPPORTED_DOMAIN')
    hole = d['holes'][0]
    hx, hy = hole['center_mm']; hr = hole['radius_mm']
    clearance = [hx - hr - x0, x1 - hx - hr, hy - hr - y0, y1 - hy - hr]
    support=t.get('bore_support_width_mm')
    if support is not None:
        compensated=hr*3/(2+math.cos(math.tau/t['hole_control_count']))
        if support>hr or compensated+support>=min(hx-x0,x1-hx,hy-y0,y1-hy):
            _fail('Independent bore support exceeds compensated radius/frame clearance', '$.topology.bore_support_width_mm', 'UNSUPPORTED_DOMAIN')
    if not all(math.isfinite(x) for x in clearance) or min(clearance) < max(.8, hr * .15):
        _fail('Hole lacks the current candidate transition clearance', '$.design.holes', 'UNSUPPORTED_DOMAIN')


def validate_request(value):
    """Validate and copy a request; do not supply design values or authenticate claims.

    This accepts dry-run production *intent*, never execution authority. File
    identities are declarations: no file content, approval or native run is read.
    """
    if isinstance(value, (str, bytes, bytearray)):
        value = _c.strict_loads(value)
    _c._walk_limits(value)
    if len(_c.canonical_bytes(value)) > _c.MAX_BYTES:
        _fail('Request exceeds 2 MiB', code='LIMIT_EXCEEDED')
    if isinstance(value, dict):
        versions = [('schema_version', value.get('schema_version'), SCHEMA_VERSION)]
        identity = value.get('identity')
        if isinstance(identity, dict):
            versions.extend(('identity.' + key, identity.get(key), expected) for key, expected in (
                ('contract_version', CONTRACT_VERSION), ('operation_version', OPERATION_VERSION), ('evidence_version', EVIDENCE_VERSION)))
        for path, actual, expected in versions:
            if actual is not None and actual != expected:
                _fail(f'Unsupported version {actual!r}; explicit migration required', '$.' + path, 'UNSUPPORTED_VERSION')
    r = _c._validate(value, REQUEST)
    protected = _all_files(r)
    candidate = _path(r['output']['candidate_file'], '$.output.candidate_file')
    evidence = _path(r['output']['evidence_directory'], '$.output.evidence_directory')
    for p, location in protected:
        if _aliases(p, candidate) or _aliases(p, evidence):
            _fail('Output aliases a protected source/reference file', location, 'SOURCE_OUTPUT_ALIAS')
        try:
            contained = p.resolve(strict=False).is_relative_to(evidence.resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            _fail('Cannot resolve evidence path protection', location, 'PATH_IDENTITY_UNVERIFIED')
        if contained:
            _fail('Evidence output directory contains a protected input', location, 'SOURCE_OUTPUT_ALIAS')
    if _aliases(candidate, evidence):
        _fail('Candidate file and evidence directory alias', '$.output', 'OUTPUT_ALIAS')
    ref = r['reference_contract']; approval = ref['approval_evidence']
    _c._unique(ref['artifacts'], '$.reference_contract.artifacts')
    _c._unique(ref['conflicts'], '$.reference_contract.conflicts')
    if ref['state'] == 'proposed' and approval is not None:
        _fail('Proposed reference cannot carry an approval claim', '$.reference_contract')
    if ref['state'] == 'approval_evidence_supplied':
        if approval is None:
            _fail('Approval evidence is missing', '$.reference_contract.approval_evidence')
        if ref['conflicts']:
            _fail('Unresolved reference conflicts forbid an approval claim', '$.reference_contract.conflicts')
        if set(a['role'] for a in ref['artifacts']) != {'appearance', 'dimensions', 'detail', 'checklist'}:
            _fail('Approval evidence requires the complete reference package', '$.reference_contract.artifacts')
        if (approval['reference_package_sha256'] != ref['package_sha256']
                or approval['design_sha256'] != fingerprint(r['design'])
                or approval['project_id'] != r['identity']['project_id']):
            _fail('Approval evidence does not bind this exact reference/design/project', '$.reference_contract.approval_evidence', 'REFERENCE_BINDING_MISMATCH')
    _domain_check(r)
    s = r['structure']
    registries = {name: _c._unique(s[name], '$.structure.' + name) for name in ('regions', 'loops', 'boundary_ports', 'interfaces')}
    regions, loops, ports = (registries[k] for k in ('regions', 'loops', 'boundary_ports'))
    all_ids = [item['id'] for entries in s.values() if isinstance(entries, list) for item in entries]
    if len(all_ids) != len(set(all_ids)):
        _fail('Semantic IDs must be unique across entity kinds', '$.structure', 'DUPLICATE_ID')
    feature_ids = {r['design']['asset_id'], *[h['id'] for h in r['design']['holes']]}
    for region in regions.values():
        if region['feature_id'] not in feature_ids:
            _fail('Region references an unknown feature', '$.structure.regions')
        for loop_id in region['loop_ids']:
            if loop_id not in loops or loops[loop_id]['region_id'] != region['id']:
                _fail('Region/loop ownership mismatch', '$.structure.regions')
    for loop in loops.values():
        if loop['region_id'] not in regions or loop['id'] not in regions[loop['region_id']]['loop_ids']:
            _fail('Loop requires reciprocal region ownership', '$.structure.loops')
    for port in ports.values():
        if port['loop_id'] not in loops or loops[port['loop_id']]['region_id'] != port['region_id']:
            _fail('Port loop/region ownership mismatch', '$.structure.boundary_ports')
        if not math.isclose(sum(v * v for v in port['normal']), 1.0, rel_tol=0, abs_tol=1e-9):
            _fail('Boundary port normal must be unit length', '$.structure.boundary_ports')
    for interface in registries['interfaces'].values():
        if interface['port_a'] == interface['port_b'] or any(interface[k] not in ports for k in ('port_a', 'port_b')):
            _fail('Interface must refer to two distinct declared ports', '$.structure.interfaces')
    edit = r['edit_contract']
    if not set(edit['target_feature_ids']) <= feature_ids or not set(edit['affected_region_ids']) <= set(regions):
        _fail('Edit target/region is undeclared', '$.edit_contract')
    if not set(edit['protected_object_ids']) <= set(r['source']['object_ids']):
        _fail('Protected object is outside the saved source scope', '$.edit_contract.protected_object_ids')
    ranges = edit['parameter_ranges']
    if len({x['parameter'] for x in ranges}) != len(ranges) or {x['parameter'] for x in ranges} != set(edit['allowed_parameters']):
        _fail('Each allowed parameter needs exactly one explicit range', '$.edit_contract.parameter_ranges')
    hole = r['design']['holes'][0]
    current = dict(zip(PARAMETERS, [*hole['center_mm'], hole['radius_mm'], r['design']['z_range_mm'][1] - r['design']['z_range_mm'][0],r['design']['edge_roundover_mm']]))
    if 'panel.edge_roundover_mm' in edit['allowed_parameters'] and 'bore_support_width_mm' not in r['topology']:
        _fail('Outer-roundover editing requires explicit fixed bore support', '$.edit_contract.allowed_parameters', 'UNSUPPORTED_DOMAIN')
    for entry in ranges:
        if not entry['minimum'] <= current[entry['parameter']] <= entry['maximum']:
            _fail('Range is inverted or excludes current design value', '$.edit_contract.parameter_ranges')
        if entry['parameter'] in ('hole.radius_mm', 'panel.thickness_mm', 'panel.edge_roundover_mm') and entry['minimum'] <= 0:
            _fail('Radius/thickness edit range must stay positive', '$.edit_contract.parameter_ranges')
    units = _c._unique(r['work_units'], '$.work_units')
    _c._dag({key: u['depends_on'] for key, u in units.items()}, '$.work_units')
    writers = set()
    for unit in units.values():
        if not set(unit['input_ports'] + unit['output_ports']) <= set(ports):
            _fail('Work unit uses an undeclared boundary port', '$.work_units')
        if writers.intersection(unit['output_ports']):
            _fail('Multiple work units declare the same output port', '$.work_units', 'OUTPUT_CONFLICT')
        writers.update(unit['output_ports'])
    return r


def plan_request(value):
    """Return an honest no-write plan; even a valid fixture cannot become runnable."""
    try:
        r = validate_request(value)
    except ContractError as exc:
        return {'operation': 'structure.plan', 'status': 'rejected', 'request_sha256': None,
                'blockers': [exc.as_dict()], 'qualification': copy.deepcopy(FIRST_BATCH_STATUS),
                'construction_authorized': False, 'side_effects': [], 'next_action': 'Correct the rejected input'}
    blockers = [
        {'code': 'M0_PENDING', 'message': 'An independently verified exact runtime/baseline receipt is required for this request; this read-only planner does not infer it from installed files'},
        {'code': 'SOURCE_IDENTITY_UNVERIFIED', 'message': 'Source SHA/size/save-state declarations require independent file verification'},
        {'code': 'IMPLEMENTATION_IDENTITY_UNVERIFIED', 'message': 'Caller-supplied implementation identity requires an independent source manifest check'},
        {'code': 'STRUCTURE_DECLARATIONS_UNVERIFIED', 'message': 'Region, loop, port and edit-scope declarations require actual indexed-mesh verification'},
        {'code': 'REFERENCE_APPROVAL_REQUIRED' if r['reference_contract']['state'] == 'proposed' else 'REFERENCE_APPROVAL_UNVERIFIED',
         'message': 'Separate user approval must be independently verified against the frozen package and design'},
        {'code': 'NATIVE_QUALIFICATION_NOT_RUN', 'message': 'Mesh, evaluated geometry, edit, reopen and holdout checks have not run'},
        {'code': 'VISUAL_QUALIFICATION_NOT_RUN', 'message': 'Actual fixed-view visual inspection and stage approval are pending'},
    ]
    if {a['role'] for a in r['reference_contract']['artifacts']} != {'appearance', 'dimensions', 'detail', 'checklist'}:
        blockers.append({'code': 'REFERENCE_PACKAGE_INCOMPLETE', 'message': 'Appearance, dimension, detail and checklist references are all required before approval'})
    if r['reference_contract']['conflicts']:
        blockers.append({'code': 'REFERENCE_CONFLICTS', 'message': 'Resolve all listed reference conflicts before reference approval'})
    if r['asset_profile']['route'] == 'realtime':
        blockers.append({'code': 'REALTIME_CHAIN_NOT_IMPLEMENTED', 'message': 'Low mesh, UV, bake, LOD and interchange are not implemented by this first-batch planner'})
    if r['requested_usage'] == 'production':
        blockers.append({'code': 'PRODUCTION_UNQUALIFIED', 'message': 'This implementation/domain/route has no native, visual or user qualification'})
    q = r['quality_and_budget']; estimate = q['estimates']; limits = q['budget_limits']
    # Arithmetic projection only. Catmull-Clark quads split four ways per level;
    # this assumes the caller's face-count estimate and is not a mesh count.
    faces = estimate['control_faces']
    projected = None if faces is None else faces * (4 ** max(r['evaluation']['levels']))
    for key in ('wall_seconds', 'peak_ram_bytes', 'control_faces'):
        if estimate[key] is not None and estimate[key] > limits[key]:
            blockers.append({'code': 'BUDGET_ESTIMATE_EXCEEDS_LIMIT', 'message': key + ' estimate exceeds the explicit limit'})
    if projected is not None and projected > limits['evaluated_faces']:
        blockers.append({'code': 'BUDGET_ESTIMATE_EXCEEDS_LIMIT', 'message': 'Projected evaluated face estimate exceeds the explicit limit'})
    if any(x['value'] is None for x in q['tolerances'].values()):
        blockers.append({'code': 'TOLERANCES_UNRESOLVED', 'message': 'Every applicable tolerance group needs separately approved values'})
    observation = r['asset_profile']['observation']
    if observation['camera_reference'] is None or observation['resolution_px'] is None or not observation['view_ids']:
        blockers.append({'code': 'OBSERVATION_UNRESOLVED', 'message': 'Camera, resolution and fixed views still need proposal and approval'})
    return {
        'operation': 'structure.plan', 'status': 'planned_with_blockers',
        'identity': copy.deepcopy(r['identity']), 'request_sha256': fingerprint(r),
        'domain': DOMAIN_ID, 'execution_mode': 'dry_run', 'construction_authorized': False,
        'declared_change_scope': {
            'target_feature_ids': r['edit_contract']['target_feature_ids'],
            'allowed_parameters': r['edit_contract']['allowed_parameters'],
            'affected_region_ids': r['edit_contract']['affected_region_ids'],
            'protected_object_ids': r['edit_contract']['protected_object_ids'],
            'invariants': r['edit_contract']['invariants'], 'verification': 'not_run',
        },
        'estimates': {'status': 'unmeasured_estimate', 'basis': estimate['basis'],
                      'control_faces': faces, 'evaluated_faces': projected,
                      'projection_method': 'caller_control_face_estimate_times_4_power_max_level',
                      'wall_seconds': estimate['wall_seconds'], 'peak_ram_bytes': estimate['peak_ram_bytes']},
        'qualification': copy.deepcopy(FIRST_BATCH_STATUS), 'blockers': blockers,
        'side_effects': [], 'next_action': 'Resolve M0 and reference approval before any native construction',
    }


def migrate_legacy_panel(params, identity):
    """Map only explicit dev.3 fields to an incomplete, proposed planning skeleton.

    No defaults, legacy approvals, qualification, measured budgets or ambiguous
    chord/SubD tolerance interpretation are inherited. The return is deliberately
    NOT a valid executable/planning request until its listed additions are made.
    """
    _c._walk_limits(params); _c._walk_limits(identity)
    if not isinstance(params, dict):
        _fail('Legacy panel params must be an object')
    checked_identity = _c._validate(identity, IDENTITY, '$.identity')
    design = {'length_unit': 'mm'}
    topology, evaluation, losses, additions = {}, {}, [], []
    skeleton = {'schema_version': SCHEMA_VERSION, 'operation': 'structure.plan',
                'execution_mode': 'dry_run', 'requested_usage': 'development',
                'identity': checked_identity, 'design': design, 'topology': topology,
                'evaluation': evaluation,
                'reference_contract': {'state': 'proposed', 'approval_evidence': None}}
    mapping = {'id': 'asset_id', 'size': 'size_mm', 'center': 'center_mm', 'corner_radius': 'corner_radius_mm'}
    outline = {}
    consumed = {'op', 'id', 'size', 'center', 'corner_radius', 'z_min', 'z_max', 'holes', 'topology_strategy', 'edge_bevel', 'local_patch_bounds', 'subdivision_cage', 'chord_tolerance'}
    for old, new in mapping.items():
        if old in params:
            (design if old == 'id' else outline)[new] = copy.deepcopy(params[old])
        else:
            additions.append('Supply explicit legacy/design value: ' + old)
    if outline:
        design['outline'] = {'kind': 'rounded_rectangle', **outline}
    design['parent_surface'] = 'plane'
    if 'z_min' in params and 'z_max' in params:
        design['z_range_mm'] = [copy.deepcopy(params['z_min']), copy.deepcopy(params['z_max'])]
    else:
        additions.append('Supply both explicit z_min and z_max')
        for key in ('z_min', 'z_max'):
            if key in params:
                losses.append({'field': key, 'reason': 'An incomplete z pair is not interpreted as a design range'})
    if 'holes' in params:
        holes = params['holes']
        if isinstance(holes, list) and len(holes) == 1 and isinstance(holes[0], dict) and holes[0].get('kind') == 'circle' and not holes[0].get('counterbore'):
            h = holes[0]
            design['holes'] = [{new: copy.deepcopy(h[old]) for old, new in [('id', 'id'), ('kind', 'kind'), ('center', 'center_mm'), ('radius', 'radius_mm')] if old in h}]
            design['holes'][0]['rim'] = 'sharp'
            for key in set(h) - {'id', 'kind', 'center', 'radius'}:
                losses.append({'field': 'holes[0].' + key, 'reason': 'Not transferred to the bounded sharp-circle design'})
        else:
            losses.append({'field': 'holes', 'reason': 'Only one explicit circle without counterbore can migrate; no shape conversion is attempted'})
    else:
        additions.append('Supply the single sharp circular hole explicitly')
    if params.get('topology_strategy') == 'subd_control_cage':
        topology['strategy'] = 'subd_control_cage'
        if 'edge_bevel' in params:
            design['edge_roundover_mm'] = copy.deepcopy(params['edge_bevel'])
        else:
            additions.append('Supply explicit edge roundover; no default adopted')
    else:
        losses.append({'field': 'topology_strategy', 'reason': 'A different or missing legacy strategy cannot be silently converted to SubD'})
        additions.append('Resolve the unsupported legacy topology strategy')
        if 'edge_bevel' in params:
            losses.append({'field': 'edge_bevel', 'reason': 'Roundover meaning is not inferred for an unsupported legacy strategy'})
    if 'local_patch_bounds' in params:
        topology['local_patch_bounds_mm'] = copy.deepcopy(params['local_patch_bounds'])
    else:
        additions.append('Supply an explicit fixed local patch frame')
    cfg = params.get('subdivision_cage')
    if isinstance(cfg, dict):
        for old, new in [('method', 'method'), ('preview_levels', 'levels')]:
            if old in cfg:
                evaluation[new] = [copy.deepcopy(cfg[old])] if old == 'preview_levels' else copy.deepcopy(cfg[old])
        if 'hole_segments' in cfg:
            topology['hole_control_count'] = copy.deepcopy(cfg['hole_segments'])
        for key in set(cfg) - {'method', 'preview_levels', 'hole_segments'}:
            losses.append({'field': 'subdivision_cage.' + key, 'reason': 'Unknown legacy setting not transferred'})
    elif cfg is not None:
        losses.append({'field': 'subdivision_cage', 'reason': 'Malformed legacy evaluation settings not transferred'})
    if 'chord_tolerance' in params:
        losses.append({'field': 'chord_tolerance', 'reason': 'Legacy field mixed explicit chord and evaluated shape meaning; not copied to any tolerance group'})
    if params.get('op') != 'quad.panel':
        losses.append({'field': 'op', 'reason': 'Legacy operation is absent or not quad.panel; validate source intent before completing migration'})
    for key in sorted(set(params) - consumed):
        losses.append({'field': key, 'reason': 'No approved mapping; value is not inherited'})
    additions.extend([
        'Supply exact saved-source file SHA, size, object scope and protected new output paths',
        'Reconfirm every mapped design value; migration is not reference approval',
        'Supply complete proposed reference artifacts and resolve conflicts; legacy approval is never inherited',
        'Supply explicit topology counts/face policy and exact proposed evaluation version/settings',
        'Supply independently scoped edit ranges, semantic declarations and complete read-only work units',
        'Supply asset route, observation conditions and separate nominal/chord/SubD/screen tolerances',
        'Supply explicit resource limits and clearly unmeasured estimates; do not manufacture design values',
        'Complete M0 and independent native/visual/holdout qualification before any production claim',
    ])
    return {'migration_version': '1.0.0', 'source_format': 'dev3.quad.panel',
            'source_parameters_sha256': fingerprint(params), 'status': 'proposed_incomplete',
            'production_runnable': False, 'request_skeleton': skeleton,
            'losses': losses, 'required_additions': additions,
            'qualification': copy.deepcopy(FIRST_BATCH_STATUS)}
