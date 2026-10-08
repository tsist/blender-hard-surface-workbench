# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed transport from authored semantic IDs to actual control mesh data.

This module intentionally imports no Blender modules. Its protocol is exercised
with host mocks; only a Blender caller may claim that an object is native. It
never evaluates a modifier, repairs IDs, infers nearest-point identity or writes
an existing object's missing identity. Initial identity writes require the full
constructor output and exact, float32-aware materialization proof.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
import struct

from .io import RuntimeFailure
from .structure_adapter import adapt_authored_mesh
from .subd_panel_identity import validate_authored_identity as _validate_schedule
from .structure_kernel import (StructureError, _id, _index_map, _normal, _norm,
                               fingerprint, validate_structure)

VERTEX_SLOT = 'hs_structure_vertex_slot'
FACE_SLOT = 'hs_structure_face_slot'
MANIFEST = 'hs_structure_manifest'
MANIFEST_SHA = 'hs_structure_manifest_sha256'
AUTHORED_SHA = 'hs_structure_authorship_sha256'
NATIVE_VERSION = 'native-control-structure/1.0'
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 200000
SIGNATURES = ('geometry_signature', 'topology_signature', 'attribute_signature', 'structure_signature')
DEFAULT_TOLERANCES = {'position_mm': 1e-7, 'unit_vector': 1e-7, 'area_mm2': 1e-12}


def _fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def _json_text(value):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        _fail('STRUCTURE_METADATA_INVALID', 'Identity metadata must be finite JSON', reason=str(exc))
    if len(text.encode('utf-8')) > MAX_JSON_BYTES:
        _fail('STRUCTURE_METADATA_LIMIT', 'Identity metadata exceeds the 8 MiB bound')
    return text


def _json_read(text, label):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_JSON_BYTES:
        _fail('STRUCTURE_METADATA_INVALID', 'Missing or oversized identity JSON', field=label)
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _fail('STRUCTURE_METADATA_INVALID', 'Duplicate identity JSON key', field=label)
            result[key] = value
        return result
    def nonfinite(value):
        _fail('STRUCTURE_METADATA_INVALID', 'Nonfinite identity JSON value', field=label)
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, TypeError, RecursionError) as exc:
        _fail('STRUCTURE_METADATA_INVALID', 'Malformed identity JSON', field=label, reason=str(exc))
    # Bound decoded shape independently of byte size, before any traversal below.
    todo = [(value, 0)]; count = 0
    while todo:
        node, depth = todo.pop(); count += 1
        if depth > 32 or count > 800000:
            _fail('STRUCTURE_METADATA_LIMIT', 'Identity JSON nesting or node bound exceeded', field=label)
        if isinstance(node, dict): todo.extend((v, depth+1) for v in node.values())
        elif isinstance(node, list): todo.extend((v, depth+1) for v in node)
        elif isinstance(node, float) and not math.isfinite(node): nonfinite(node)
    return value


def _f32(value):
    try:
        result = struct.unpack('!f', struct.pack('!f', float(value)))[0]
    except (OverflowError, ValueError, TypeError):
        _fail('STRUCTURE_NUMERIC_PRECISION', 'Coordinate cannot be represented as finite float32')
    if not math.isfinite(result):
        _fail('STRUCTURE_NUMERIC_PRECISION', 'Coordinate cannot be represented as finite float32')
    return result


def verify_materialization_order(mesh, vertices_m, faces):
    """Prove from_pydata order/connectivity before any semantic slots are written.

    Coordinates must be exactly the IEEE float32 conversion of authored metres.
    This checks transport precision, not artistic tolerance. Subsequent native
    signatures use the unrounded actual float values converted to local mm.
    """
    if not 0 < len(vertices_m) <= MAX_ELEMENTS or not 0 < len(faces) <= MAX_ELEMENTS:
        _fail('STRUCTURE_MATERIALIZATION_INVALID', 'Nonempty bounded authored arrays required')
    if len(mesh.vertices) != len(vertices_m) or len(mesh.polygons) != len(faces):
        _fail('STRUCTURE_MATERIALIZATION_ORDER', 'Native vertex or face count differs from authored arrays')
    maximum = 0.0
    for i, (actual, expected) in enumerate(zip(mesh.vertices, vertices_m)):
        xyz = list(actual.co)
        if len(xyz) != 3 or len(expected) != 3 or not all(math.isfinite(float(x)) for x in expected):
            _fail('STRUCTURE_NUMERIC_PRECISION', 'Finite three-dimensional coordinates required', vertex_index=i)
        rounded = [_f32(x) for x in expected]
        if xyz != rounded:
            _fail('STRUCTURE_MATERIALIZATION_ORDER', 'Native coordinate order or float32 conversion differs', vertex_index=i)
        maximum = max(maximum, math.dist(xyz, expected))
    for i, (polygon, face) in enumerate(zip(mesh.polygons, faces)):
        if list(polygon.vertices) != list(face):
            _fail('STRUCTURE_MATERIALIZATION_ORDER', 'Native oriented connectivity differs before ID assignment', face_index=i)
    return {'status': 'pass', 'policy': 'exact_ieee754_float32_authored_metres_v1',
            'maximum_error_m': maximum, 'coordinate_space': 'object_local',
            'native_signature_quantization': 'none'}


def _attribute(mesh, name, domain, kind, length):
    attr = mesh.attributes.get(name)
    if attr is None:
        _fail('STRUCTURE_IDENTITY_MISSING' if name in (VERTEX_SLOT, FACE_SLOT) else 'STRUCTURE_ATTRIBUTE_MISSING', 'Required actual mesh attribute is absent', attribute=name)
    if attr.domain != domain or attr.data_type != kind or len(attr.data) != length:
        _fail('STRUCTURE_ATTRIBUTE_INVALID', 'Actual attribute domain, type or cardinality differs', attribute=name)
    return attr


def _authorship_hash(manifest):
    from .subd_panel_identity import authorship_payload, fingerprint as authored_fingerprint
    try:
        return authored_fingerprint(authorship_payload(manifest))
    except (KeyError, TypeError, ValueError) as exc:
        _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Incomplete canonical authored payload', reason=str(exc))


def _validate_authored(manifest, vertex_count, face_count):
    required = {'schema_version', 'schedule_revision', 'vertex_map', 'face_map',
                'semantic_control_loops', 'parameter_binding', 'supported_edit_datum'}
    if not isinstance(manifest, dict) or not required <= set(manifest) or manifest['schema_version'] != '1.0':
        _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Explicit versioned authored identity schedule required')
    try:
        _id(manifest['schedule_revision'])
        vm = _index_map(manifest['vertex_map'], vertex_count, 'vertex_map')
        fm = _index_map(manifest['face_map'], face_count, 'face_map')
    except StructureError as exc:
        _fail('STRUCTURE_AUTHORSHIP_INVALID', str(exc), kernel_code=exc.code)
    if set(vm) & set(fm):
        _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Vertex and face identities must have separate names')
    loops = manifest['semantic_control_loops']
    if not isinstance(loops, list) or len(loops) > 10000:
        _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Bounded semantic loop array required')
    roles = set()
    for row in loops:
        if not isinstance(row, dict) or not {'role', 'vertex_ids', 'crease', 'expected_valence'} <= set(row):
            _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Incomplete authored semantic loop')
        try: _id(row['role'])
        except StructureError as exc: _fail('STRUCTURE_AUTHORSHIP_INVALID', str(exc))
        ids = row['vertex_ids']
        if row['role'] in roles or not isinstance(ids, list) or not 3 <= len(ids) <= MAX_ELEMENTS or any(not isinstance(v, str) for v in ids) or len(ids) != len(set(ids)) or any(v not in vm for v in ids):
            _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Ambiguous semantic loop role or vertex references')
        value = row['crease']
        irregular_sparse=(manifest['schedule_revision']=='sparse_sharp_panel_ids_v2' and row['expected_valence'] is None)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 or (not irregular_sparse and (type(row['expected_valence']) is not int or row['expected_valence'] < 2)):
            _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Invalid semantic loop crease or valence')
        roles.add(row['role'])
    if not isinstance(manifest['parameter_binding'], dict):
        _fail('STRUCTURE_AUTHORSHIP_INVALID', 'Explicit parameter binding required')
    _json_read(_json_text(manifest), 'authored_structure')
    return vm, fm


def _slot_map(mesh, name, domain, table, count):
    if not isinstance(table, list) or len(table) != count or any(not isinstance(key, str) for key in table) or len(set(table)) != count:
        _fail('STRUCTURE_IDENTITY_INVALID', 'Semantic table must uniquely cover the domain', attribute=name)
    try:
        for key in table: _id(key)
    except StructureError as exc: _fail('STRUCTURE_IDENTITY_INVALID', str(exc))
    attr = _attribute(mesh, name, domain, 'INT', count)
    values = [item.value for item in attr.data]
    if any(type(value) is not int or not 0 <= value < count for value in values) or len(set(values)) != count:
        _fail('STRUCTURE_IDENTITY_INVALID', 'Missing, duplicate or unknown semantic slot', attribute=name)
    return {table[value]: index for index, value in enumerate(values)}


def _actual_arrays(obj, envelope):
    mesh = obj.data
    if not 0 < len(mesh.vertices) <= MAX_ELEMENTS or not 0 < len(mesh.polygons) <= MAX_ELEMENTS or len(mesh.edges) > MAX_ELEMENTS*4:
        _fail('STRUCTURE_NATIVE_LIMIT', 'Actual mesh exceeds bounded native domains')
    manifest = envelope['authored_structure']
    _validate_authored(manifest, len(mesh.vertices), len(mesh.polygons))
    vm = _slot_map(mesh, VERTEX_SLOT, 'POINT', envelope['vertex_ids'], len(mesh.vertices))
    fm = _slot_map(mesh, FACE_SLOT, 'FACE', envelope['face_ids'], len(mesh.polygons))
    if set(vm) != set(manifest['vertex_map']) or set(fm) != set(manifest['face_map']):
        _fail('STRUCTURE_IDENTITY_INVALID', 'Persisted slot tables differ from authored IDs')
    vertices = [[float(x)*1000.0 for x in v.co] for v in mesh.vertices]
    if any(len(v) != 3 or not all(math.isfinite(x) for x in v) for v in vertices):
        _fail('STRUCTURE_GEOMETRY_INVALID', 'Actual local coordinates must be finite')
    faces = [list(p.vertices) for p in mesh.polygons]
    for row in faces:
        if len(row) not in (3, 4) or any(type(x) is not int or not 0 <= x < len(vertices) for x in row) or len(set(row)) != len(row):
            _fail('STRUCTURE_TOPOLOGY_DRIFT', 'Actual face connectivity is invalid')
    derived = {tuple(sorted((a,b))) for row in faces for a,b in zip(row,row[1:]+row[:1])}
    edge_rows = [list(e.vertices) for e in mesh.edges]
    if any(len(e) != 2 or any(type(x) is not int or not 0 <= x < len(vertices) for x in e) or e[0] == e[1] for e in edge_rows):
        _fail('STRUCTURE_ACTUAL_EDGES_INVALID', 'Actual edge endpoints are invalid')
    actual_edges = [tuple(sorted(e)) for e in edge_rows]
    if len(set(actual_edges)) != len(actual_edges) or set(actual_edges) != derived:
        _fail('STRUCTURE_ACTUAL_EDGES_INVALID', 'Actual edges differ from face-derived edges; loose, duplicate or missing edge',
              unexpected_edges=len(set(actual_edges)-derived), missing_edges=len(derived-set(actual_edges)))
    attr = _attribute(mesh, 'crease_edge', 'EDGE', 'FLOAT', len(mesh.edges))
    values = [float(item.value) for item in attr.data]
    if any(not math.isfinite(x) or not 0 <= x <= 1 for x in values):
        _fail('STRUCTURE_CREASE_INVALID', 'Actual crease values must be finite in [0,1]')
    creases = [[a, b, value] for (a,b),value in zip(actual_edges,values)]
    table = _json_read(obj.get('hs_quad_surface_table'), 'hs_quad_surface_table')
    if not isinstance(table, list) or fingerprint(table) != envelope['surface_table_sha256']:
        _fail('STRUCTURE_SOURCE_DRIFT', 'Actual provenance table differs from the materialized source')
    source_attr = _attribute(mesh, 'hs_quad_surface_id', 'FACE', 'INT', len(faces))
    provenance = []
    for row in source_attr.data:
        if type(row.value) is not int or not 0 <= row.value < len(table):
            _fail('STRUCTURE_SOURCE_DRIFT', 'Actual polygon has an unknown provenance slot')
        provenance.append(deepcopy(table[row.value]))
    actual = {'vertices_mm': vertices, 'faces': faces, 'edge_creases': creases, 'face_provenance': provenance}
    return actual, vm, fm


def _plain(value):
    if value is None or isinstance(value, (str, bool, int, float)): return value
    if isinstance(value, (list, tuple)): return [_plain(x) for x in value]
    try: return [_plain(x) for x in value]
    except TypeError: _fail('STRUCTURE_EVALUATION_UNSUPPORTED', 'Unsupported modifier value cannot be omitted')


def _context(obj, unit_scale):
    if type(unit_scale) not in (int, float) or not math.isfinite(unit_scale) or unit_scale != 1.0:
        _fail('STRUCTURE_UNITS_UNSUPPORTED', 'Native coordinates require SI scale_length exactly 1')
    if getattr(obj, 'type', None) != 'MESH' or getattr(obj, 'mode', 'OBJECT') != 'OBJECT':
        _fail('STRUCTURE_NATIVE_UNSUPPORTED', 'Only object-mode mesh control data is supported')
    if getattr(obj, 'parent', None) is not None or getattr(obj.data, 'shape_keys', None) is not None or getattr(obj.data, 'users', 1) != 1 or len(getattr(obj, 'constraints', [])):
        _fail('STRUCTURE_NATIVE_UNSUPPORTED', 'Parented, constrained, shared or shape-key mesh is unsupported')
    if getattr(obj, 'animation_data', None) is not None or getattr(obj.data, 'animation_data', None) is not None:
        _fail('STRUCTURE_NATIVE_UNSUPPORTED', 'Animated control data is unsupported')
    matrix = [[float(x) for x in row] for row in obj.matrix_world]
    if len(matrix) != 4 or any(len(row) != 4 or not all(math.isfinite(x) for x in row) for row in matrix):
        _fail('STRUCTURE_MATRIX_INVALID', 'Actual world matrix must be finite 4x4')
    modifiers = []
    for mod in obj.modifiers:
        if mod.type != 'SUBSURF' or len(obj.modifiers) != 1:
            _fail('STRUCTURE_EVALUATION_UNSUPPORTED', 'Only the isolated authored Subdivision modifier is supported')
        row = {'name': mod.name, 'type': mod.type}
        properties = getattr(getattr(mod, 'bl_rna', None), 'properties', None)
        if properties is None:
            # Explicit protocol fallback for host mocks, not a native RNA fallback.
            names = ('subdivision_type', 'levels', 'render_levels', 'quality', 'uv_smooth',
                     'boundary_smooth', 'use_creases', 'use_limit_surface', 'use_custom_normals',
                     'show_viewport', 'show_render', 'show_in_editmode', 'show_on_cage')
        else:
            names = [p.identifier for p in properties if p.identifier not in ('rna_type', 'name', 'type') and not p.is_readonly and p.type != 'COLLECTION']
        for name in names:
            if hasattr(mod, name): row[name] = _plain(getattr(mod, name))
        modifiers.append(row)
    result = {'coordinate_space': 'object_local_mm', 'scale_length': float(unit_scale),
              'matrix_world': matrix, 'modifiers': modifiers}
    _json_text(result)
    return result


def _semantic_data(actual, vm, fm):
    inverse = {i: key for key, i in vm.items()}
    def rotated(seq):
        index = seq.index(min(seq)); return seq[index:]+seq[:index]
    return {
        'geometry': {key: actual['vertices_mm'][idx] for key, idx in vm.items()},
        'topology': {key: rotated([inverse[i] for i in actual['faces'][idx]]) for key, idx in fm.items()},
        'provenance': {key: actual['face_provenance'][idx] for key, idx in fm.items()},
        'creases': sorted([sorted((inverse[a], inverse[b]))+[float(value)] for a,b,value in actual['edge_creases'] if value != 0.0]),
    }


def _with_control_loops(adapted, manifest):
    """Add author-named loops using actual semantic maps, with independent checks."""
    mesh, structure = adapted['mesh'], adapted['structure']
    vm = structure['vertex_map']; incident = defaultdict(list); adjacency = defaultdict(set)
    for face in mesh['faces']:
        for vertex in face: incident[vertex].append(set(face))
        for a,b in zip(face,face[1:]+face[:1]): adjacency[a].add(b); adjacency[b].add(a)
    rows = []
    for loop in manifest['semantic_control_loops']:
        ids = loop['vertex_ids']; indices = [vm[key] for key in ids]
        points = [mesh['vertices'][i] for i in indices]; normal = _normal(points); length = _norm(normal)
        if length <= 0:
            _fail('STRUCTURE_CONTROL_LOOP_INVALID', 'Actual semantic loop has no orientation', role=loop['role'])
        normal = [x/length for x in normal]
        # Opposite-edge continuation is a constructor contract, beyond valence.
        continuation = all(len(incident[v]) == 4 and all(len(face) == 4 and not {indices[(j-1)%len(indices)], indices[(j+1)%len(indices)]} <= face for face in incident[v]) for j,v in enumerate(indices))
        irregular_sparse=(manifest['schedule_revision']=='sparse_sharp_panel_ids_v2' and loop['expected_valence'] is None)
        if not continuation and not irregular_sparse:
            _fail('STRUCTURE_CONTROL_LOOP_INVALID', 'Semantic cycle does not continue through opposite quad edges', role=loop['role'])
        record={'id': 'control-loop:'+fingerprint(loop['role'])[:32], 'role': loop['role'],
            'vertex_ids': list(ids), 'closed': True, 'normal': normal,
            'anchor': {'vertex_id': ids[0], 'position_mm': list(points[0])},
            'crease': loop['crease']}
        if not irregular_sparse:record['expected_valence']=loop['expected_valence']
        structure['loops'].append(record)
        row={'role': loop['role'], 'status': 'pass', 'vertices': len(ids),
             'opposite_quad_edge_continuation': continuation,
             'expected_valence': loop['expected_valence'], 'expected_crease': loop['crease']}
        if manifest['schedule_revision']=='sparse_sharp_panel_ids_v2':
            row.update(classification='regular_edge_loop' if continuation else 'pole_crossing_edge_cycle',
                       actual_valence_histogram={str(k):v for k,v in Counter(len(adjacency[i]) for i in indices).items()})
        rows.append(row)
    return {'status': 'pass', 'source': 'actual object.data semantic slots, edges and crease_edge', 'cycles': rows}


def _read_envelope(obj):
    raw = obj.data.get(MANIFEST)
    if raw is None:
        _fail('STRUCTURE_IDENTITY_MISSING', 'Legacy object has no persisted authored identity; no automatic migration')
    value = _json_read(raw, MANIFEST)
    required = {'schema_version', 'authored_structure', 'authorship_sha256', 'construction_sha256',
                'vertex_ids', 'face_ids', 'topology_epoch', 'surface_table_sha256',
                'semantic_baseline', 'context', 'materialization', 'identity_binding'}
    if not isinstance(value, dict) or set(value) != required or value['schema_version'] != NATIVE_VERSION:
        _fail('STRUCTURE_METADATA_INVALID', 'Native identity envelope fields or version differ')
    for field in ('authorship_sha256', 'construction_sha256', 'surface_table_sha256'):
        token = value[field]
        if not isinstance(token, str) or len(token) != 64 or any(x not in '0123456789abcdef' for x in token):
            _fail('STRUCTURE_METADATA_INVALID', 'Invalid persisted SHA256 field', field=field)
    baseline = value['semantic_baseline']
    if (not isinstance(baseline, dict) or set(baseline) != {'geometry', 'topology', 'provenance', 'creases'}
            or any(not isinstance(x, str) or len(x) != 64 or any(c not in '0123456789abcdef' for c in x) for x in baseline.values())):
        _fail('STRUCTURE_METADATA_INVALID', 'Invalid persisted semantic baseline fingerprints')
    bound = value['identity_binding']
    if bound is not None and (not isinstance(bound, dict) or set(bound) != {'object_id', 'data_id', 'source_binding'}
            or any(not isinstance(bound.get(k), str) or not bound[k] for k in ('object_id', 'data_id'))
            or not isinstance(bound.get('source_binding'), dict) or not bound['source_binding']):
        _fail('STRUCTURE_METADATA_INVALID', 'Invalid persisted source/identity binding')
    if fingerprint(value) != obj.data.get(MANIFEST_SHA):
        _fail('STRUCTURE_METADATA_SHA_MISMATCH', 'Persisted semantic metadata SHA differs')
    if (_authorship_hash(value['authored_structure']) != value['authorship_sha256']
            or value['authored_structure'].get('authorship_sha256') != value['authorship_sha256']
            or obj.get(AUTHORED_SHA) != value['authorship_sha256']):
        _fail('STRUCTURE_AUTHORSHIP_SHA_MISMATCH', 'Authored identity schedule SHA differs')
    if value['construction_sha256'] != obj.get('hs_quad_construction_sha256'):
        _fail('STRUCTURE_SOURCE_DRIFT', 'Constructor receipt SHA differs from persisted authored source')
    if type(value['topology_epoch']) is not int or value['topology_epoch'] < 0:
        _fail('STRUCTURE_METADATA_INVALID', 'Topology epoch must be a nonnegative integer')
    return value


def _persist(obj, envelope):
    obj.data[MANIFEST] = _json_text(envelope)
    obj.data[MANIFEST_SHA] = fingerprint(envelope)
    obj[AUTHORED_SHA] = envelope['authorship_sha256']


def write_authored_identity(obj, result, *, unit_scale, topology_epoch=0):
    """Initial, one-time identity assignment after proven constructor materialization."""
    if obj.data.get(MANIFEST) is not None or obj.data.attributes.get(VERTEX_SLOT) is not None or obj.data.attributes.get(FACE_SLOT) is not None:
        _fail('STRUCTURE_IDENTITY_EXISTS', 'Refuse to replace or repair existing semantic identity')
    manifest = deepcopy(result.get('authored_structure'))
    vm, fm = _validate_authored(manifest, len(result['vertices_mm']), len(result['faces']))
    _validate_schedule(result, manifest)
    authored_sha = _authorship_hash(manifest)
    if result.get('authorship_sha256') != authored_sha or manifest.get('authorship_sha256') != authored_sha:
        _fail('STRUCTURE_AUTHORSHIP_SHA_MISMATCH', 'Constructor must supply the exact authored schedule SHA')
    if type(topology_epoch) is not int or topology_epoch < 0:
        _fail('STRUCTURE_METADATA_INVALID', 'Topology epoch must be a nonnegative integer')
    precision = verify_materialization_order(obj.data, [[x*.001 for x in v] for v in result['vertices_mm']], result['faces'])
    context = _context(obj, unit_scale)
    table = _json_read(obj.get('hs_quad_surface_table'), 'hs_quad_surface_table')
    envelope = {'schema_version': NATIVE_VERSION, 'authored_structure': manifest,
        'authorship_sha256': authored_sha, 'construction_sha256': result['construction_sha256'],
        'vertex_ids': sorted(vm), 'face_ids': sorted(fm), 'topology_epoch': topology_epoch,
        'surface_table_sha256': fingerprint(table), 'semantic_baseline': {}, 'context': context,
        'materialization': precision, 'identity_binding': None}
    # Validate JSON before mutating mesh attributes. Failed candidates are retained.
    _json_text(envelope)
    for name, domain, mapping, table_ids in ((VERTEX_SLOT, 'POINT', vm, envelope['vertex_ids']), (FACE_SLOT, 'FACE', fm, envelope['face_ids'])):
        attr = obj.data.attributes.new(name, 'INT', domain)
        slots = {key: index for index, key in enumerate(table_ids)}
        for key, index in mapping.items(): attr.data[index].value = slots[key]
    actual, actual_vm, actual_fm = _actual_arrays(obj, envelope)
    semantic = _semantic_data(actual, actual_vm, actual_fm)
    authored_semantic = _semantic_data({'vertices_mm': result['vertices_mm'], 'faces': result['faces'],
        'face_provenance': result['face_provenance'], 'edge_creases': result.get('edge_creases', [])}, vm, fm)
    for key in ('topology', 'provenance'):
        if semantic[key] != authored_semantic[key]:
            _fail('STRUCTURE_MATERIALIZATION_ORDER', 'Actual semantic data differs from constructor', domain=key)
    # Crease storage is float32 too; never substitute authored values when reading.
    expected_creases = [row[:-1]+[_f32(row[-1])] for row in authored_semantic['creases']]
    if semantic['creases'] != expected_creases:
        _fail('STRUCTURE_CREASE_MISMATCH', 'Actual edge endpoint crease values differ from authored values')
    envelope['semantic_baseline'] = {key: fingerprint(value) for key, value in semantic.items()}
    _persist(obj, envelope)
    return validate_native_structure(obj, unit_scale=unit_scale)


def extract_control_mesh(obj, *, unit_scale):
    """Read actual local raw arrays only; never read evaluated interpolated IDs."""
    envelope = _read_envelope(obj)
    context = _context(obj, unit_scale)
    if context != envelope['context']:
        _fail('STRUCTURE_CONTEXT_DRIFT', 'Actual matrix or evaluation binding changed')
    actual, vm, fm = _actual_arrays(obj, envelope)
    semantic = _semantic_data(actual, vm, fm)
    for key, value in semantic.items():
        if fingerprint(value) != envelope['semantic_baseline'].get(key):
            code = {'geometry': 'STRUCTURE_GEOMETRY_DRIFT', 'topology': 'STRUCTURE_TOPOLOGY_DRIFT',
                    'provenance': 'STRUCTURE_SOURCE_DRIFT', 'creases': 'STRUCTURE_CREASE_MISMATCH'}[key]
            _fail(code, 'Actual semantic domain differs from its materialized baseline', domain=key)
    binding = envelope['identity_binding']
    if binding is not None and (binding.get('object_id') != obj.get('hs_object_id') or binding.get('data_id') != obj.data.get('hs_data_id')):
        _fail('STRUCTURE_IDENTITY_BINDING_DRIFT', 'Actual object/data identity differs from registry binding')
    return {'authored': actual, 'vertex_map': vm, 'face_map': fm,
            'envelope': envelope, 'context': context, 'semantic': semantic}


def validate_native_structure(obj, *, unit_scale, expected_binding=None):
    """Recompute actual evidence; an external expected binding protects saved receipts."""
    extracted = extract_control_mesh(obj, unit_scale=unit_scale)
    envelope = extracted['envelope']
    actual_authorship = deepcopy(envelope['authored_structure'])
    actual_authorship['vertex_map'] = dict(extracted['vertex_map'])
    actual_authorship['face_map'] = dict(extracted['face_map'])
    _validate_schedule(extracted['authored'], actual_authorship)
    try:
        adapted = adapt_authored_mesh(extracted['authored'], vertex_map=extracted['vertex_map'],
            face_map=extracted['face_map'], tolerances=deepcopy(DEFAULT_TOLERANCES))
        loop_report = _with_control_loops(adapted, envelope['authored_structure'])
        kernel = validate_structure(adapted['mesh'], adapted['structure'])
    except StructureError as exc:
        _fail('STRUCTURE_KERNEL_REJECTED', str(exc), kernel_code=exc.code, kernel_details=exc.details)
    bound = envelope['identity_binding']
    binding = {'schema_version': NATIVE_VERSION, 'object_id': obj.get('hs_object_id'),
        'data_id': obj.data.get('hs_data_id'), 'mesh_state': 'control',
        'schedule_revision': envelope['authored_structure']['schedule_revision'],
        'topology_epoch': envelope['topology_epoch'], 'authorship_sha256': envelope['authorship_sha256'],
        'construction_sha256': envelope['construction_sha256'], 'manifest_sha256': obj.data[MANIFEST_SHA],
        'context_sha256': fingerprint(extracted['context']),
        'source_binding_sha256': fingerprint(bound['source_binding']) if bound else None,
        **{key: kernel[key] for key in SIGNATURES}}
    if expected_binding is not None and binding != expected_binding:
        _fail('STRUCTURE_REGISTRY_DRIFT', 'Actual extracted structure differs from expected external registry binding')
    return {'schema_version': NATIVE_VERSION, 'status': 'pass',
        'native_extraction': {'status': 'pass', 'source': 'raw object.data', 'coordinate_space': 'object_local_mm',
            'actual_edges_verified': True, 'identity_transport': 'validated_POINT_FACE_INT_slots',
            'crease_source': 'actual_EDGE_FLOAT_endpoint_values', 'signature_quantization': 'none'},
        'kernel_report': kernel, 'binding': binding, 'identity_binding_status': 'bound' if bound else 'pending_registry',
        'authorship': actual_authorship,
        'authored': {'authorship_sha256': envelope['authorship_sha256'], 'schedule_revision': envelope['authored_structure']['schedule_revision'],
            'parameter_binding': deepcopy(envelope['authored_structure']['parameter_binding']),
            'supported_edit_datum': deepcopy(envelope['authored_structure']['supported_edit_datum'])},
        'materialization': deepcopy(envelope['materialization']), 'semantic_control_loops': loop_report,
        'witness': {'mesh': adapted['mesh'], 'structure': adapted['structure']},
        'losses': adapted['losses'], 'qualification': 'not_run',
        'not_checked': ['saved_reopen', 'source_preservation', 'non_target_preservation', 'visual_quality', 'reference_approval']}


def bind_native_structure(obj, *, source_binding, unit_scale, expected_authorship_sha256=None):
    """Bind a newly materialized object after core assigns its object/data UUIDs.

    Source/job receipt data is caller supplied and remains declared metadata.
    Returns a compact binding dictionary. Callers must verify source bytes
    independently and compare it against an externally held registry at checkpoint/reopen time.
    """
    report = validate_native_structure(obj, unit_scale=unit_scale)
    envelope = _read_envelope(obj)
    if expected_authorship_sha256 is not None and expected_authorship_sha256 != envelope['authorship_sha256']:
        _fail('STRUCTURE_AUTHORSHIP_SHA_MISMATCH', 'Core expected a different authored identity schedule')
    if not isinstance(source_binding, dict) or not source_binding:
        _fail('STRUCTURE_SOURCE_BINDING_INVALID', 'Explicit nonempty job/source receipt binding required')
    oid, did = obj.get('hs_object_id'), obj.data.get('hs_data_id')
    if not isinstance(oid, str) or not oid or not isinstance(did, str) or not did:
        _fail('STRUCTURE_IDENTITY_BINDING_MISSING', 'Core must assign object and data identities before binding')
    binding = {'object_id': oid, 'data_id': did, 'source_binding': deepcopy(source_binding)}
    _json_text(binding)
    if envelope['identity_binding'] is not None:
        if envelope['identity_binding'] != binding:
            _fail('STRUCTURE_IDENTITY_REBIND_REFUSED', 'Existing native source/identity binding cannot be replaced')
        return report['binding']
    envelope['identity_binding'] = binding
    _persist(obj, envelope)
    return validate_native_structure(obj, unit_scale=unit_scale)['binding']


def compact_native_report(report):
    """Report actual gates without embedding raw mesh/identity witness arrays."""
    return {key: deepcopy(value) for key, value in report.items() if key not in ('witness', 'authorship')}


def has_native_identity(obj):
    """Detect partial identity too, so losing only the manifest cannot downgrade."""
    return (obj.data.get(MANIFEST) is not None or obj.get(AUTHORED_SHA) is not None
            or obj.data.attributes.get(VERTEX_SLOT) is not None or obj.data.attributes.get(FACE_SLOT) is not None)
