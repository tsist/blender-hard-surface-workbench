# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-only structural witnesses checked against actual indexed mesh data.

No Blender import or construction occurs here. Semantic names do not certify
geometry: loops must follow actual edges; region boundaries are reconstructed
from faces; ports are checked against their points and frame. These checks do
not establish surface quality, absence of self intersection, or qualification.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import math

STRUCTURE_VERSION = '1.0'
MAX_VERTICES = 200000
MAX_FACES = 200000


class StructureError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def _fail(code, message, **details):
    raise StructureError(code, message, **details)


def _keys(value, required, optional=(), label='entity'):
    if not isinstance(value, dict):
        _fail('STRUCTURE_INPUT', f'{label} must be an object')
    if any(not isinstance(k, str) for k in value):
        _fail('STRUCTURE_INPUT', f'{label} requires string field names')
    missing, unknown = set(required)-set(value), set(value)-set(required)-set(optional)
    if missing or unknown:
        _fail('STRUCTURE_INPUT', f'{label} fields differ', missing=sorted(missing), unknown=sorted(unknown))


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail('STRUCTURE_INPUT', f'{label} must be a finite number')
    try:
        good = math.isfinite(value)
    except OverflowError:
        good = False
    if not good or (positive and value <= 0):
        _fail('STRUCTURE_INPUT', f'{label} must be finite' + (' and positive' if positive else ''))
    converted = float(value)
    if isinstance(value, int) and int(converted) != value:
        _fail('STRUCTURE_INPUT', f'{label} integer cannot be represented exactly as float')
    return converted


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _fail('STRUCTURE_INPUT', f'{label} must be a 3-vector')
    return tuple(_number(x, label) for x in value)


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        _fail('STRUCTURE_INPUT', 'Semantic ID must be a bounded nonempty string')
    return value


def _list(value, label, maximum=MAX_VERTICES):
    if not isinstance(value, list) or len(value) > maximum:
        _fail('STRUCTURE_INPUT', f'{label} must be a bounded list')
    return value


def _sub(a, b): return tuple(x-y for x, y in zip(a, b))
def _dot(a, b):
    value = sum(x*y for x, y in zip(a, b))
    if not math.isfinite(value): _fail('GEOMETRY_ARITHMETIC_INVALID', 'Nonfinite geometric dot product')
    return value
def _cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _norm(a):
    value = math.hypot(*a)
    if not math.isfinite(value): _fail('GEOMETRY_ARITHMETIC_INVALID', 'Geometry exceeds finite arithmetic range')
    return value
def _edge(a, b): return tuple(sorted((a, b)))
def _cycle_edges(v): return [_edge(a, b) for a, b in zip(v, v[1:]+v[:1])]


def _normal(points):
    # Newell sum, translation invariant for a closed polygon.
    n = [0., 0., 0.]
    for a, b in zip(points, points[1:]+points[:1]):
        n[0] += (a[1]-b[1])*(a[2]+b[2])
        n[1] += (a[2]-b[2])*(a[0]+b[0])
        n[2] += (a[0]-b[0])*(a[1]+b[1])
    if any(not math.isfinite(x) for x in n):
        _fail('GEOMETRY_ARITHMETIC_INVALID', 'Nonfinite geometric area normal')
    return tuple(n)


def _unit(value, label, eps):
    vec = _vector(value, label)
    if abs(_norm(vec)-1.) > eps:
        _fail('FRAME_INVALID', f'{label} must be unit length')
    return vec


def _canonical(value):
    # No quantization: a declared coordinate change cannot vanish in a hash.
    if isinstance(value, dict): return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [_canonical(v) for v in value]
    if isinstance(value, float) and value == 0: return 0.0
    return value


def fingerprint(value):
    try:
        payload = json.dumps(_canonical(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, OverflowError) as exc:
        _fail('STRUCTURE_INPUT', 'Cannot fingerprint non-finite or non-JSON data', reason=str(exc))
    return hashlib.sha256(payload).hexdigest()


def _rotated(ids):
    start = min(range(len(ids)), key=lambda i: ids[i])
    return ids[start:]+ids[:start]


def _index_map(value, size, label):
    if not isinstance(value, dict) or len(value) != size:
        _fail('IDENTITY_MAP_INVALID', f'{label} must cover every actual element exactly once')
    for key, idx in value.items():
        _id(key)
        if type(idx) is not int or idx < 0 or idx >= size:
            _fail('IDENTITY_MAP_INVALID', f'{label} has invalid actual index', semantic_id=key)
    if set(value.values()) != set(range(size)):
        _fail('IDENTITY_MAP_INVALID', f'{label} aliases actual elements')
    return dict(value)


def validate_structure(mesh, structure):
    """Return evidence for a supplied mesh; reject contradictory witnesses.

    Mesh coordinates must be millimetres. All indexed elements have stable
    semantic IDs supplied by the constructor. Names are never inferred using
    nearest points. The caller is responsible for obtaining authentic raw mesh
    arrays (a native extraction adapter is a separate, unqualified boundary).
    """
    _keys(mesh, ('vertices', 'faces'), ('edge_creases',), 'mesh')
    vertices = [_vector(v, 'vertex') for v in _list(mesh['vertices'], 'vertices')]
    faces = _list(mesh['faces'], 'faces', MAX_FACES)
    if not vertices or not faces:
        _fail('STRUCTURE_INPUT', 'An actual nonempty mesh is required')
    _keys(structure, ('schema_version', 'length_unit', 'vertex_map', 'face_map', 'regions', 'loops', 'boundary_ports', 'tolerances'), label='structure')
    if structure['schema_version'] != STRUCTURE_VERSION:
        _fail('STRUCTURE_VERSION_UNSUPPORTED', 'No implicit forward or backward schema conversion')
    if structure['length_unit'] != 'mm':
        _fail('STRUCTURE_INPUT', 'Coordinates require explicit millimetres')
    tolerances = structure['tolerances']
    _keys(tolerances, ('position_mm', 'unit_vector', 'area_mm2'), label='tolerances')
    eps = _number(tolerances['position_mm'], 'position_mm', True)
    unit_eps = _number(tolerances['unit_vector'], 'unit_vector', True)
    area_eps = _number(tolerances['area_mm2'], 'area_mm2', True)
    if unit_eps >= .01:
        _fail('STRUCTURE_INPUT', 'Unit-vector numeric tolerance must be below 0.01')
    vm = _index_map(structure['vertex_map'], len(vertices), 'vertex_map')
    fm = _index_map(structure['face_map'], len(faces), 'face_map')
    inverse_vm = {index: key for key, index in vm.items()}
    edges, oriented, adjacency = Counter(), defaultdict(list), defaultdict(set)
    vertex_links = defaultdict(lambda: defaultdict(set))
    semantic_faces = {}
    seen_faces = set()
    for i, face in enumerate(faces):
        if not isinstance(face, (tuple, list)) or len(face) not in (3, 4) or any(type(v) is not int or v < 0 or v >= len(vertices) for v in face) or len(set(face)) != len(face):
            _fail('MESH_FACE_INVALID', 'Faces require distinct actual indices and three or four sides', face_index=i)
        canonical_face = tuple(sorted(face))
        if canonical_face in seen_faces:
            _fail('MESH_DUPLICATE_FACE', 'Duplicate face detected', face_index=i)
        seen_faces.add(canonical_face)
        if _norm(_normal([vertices[v] for v in face]))*.5 <= area_eps:
            _fail('MESH_DEGENERATE', 'Face projected area is at or below the declared tolerance', face_index=i)
        face = list(face)
        for j, v in enumerate(face):
            a, b = face[j-1], face[(j+1)%len(face)]
            vertex_links[v][a].add(b); vertex_links[v][b].add(a)
        for a, b in zip(face, face[1:]+face[:1]):
            edge = _edge(a, b)
            if _norm(_sub(vertices[a], vertices[b])) <= eps:
                _fail('MESH_DEGENERATE', 'Edge length is at or below tolerance', edge=list(edge))
            edges[edge] += 1
            oriented[edge].append((a, b))
            adjacency[a].add(b); adjacency[b].add(a)
    for edge, count in edges.items():
        if count > 2:
            _fail('MESH_NONMANIFOLD', 'More than two faces use an actual edge', edge=list(edge))
        if count == 2 and oriented[edge][0] == oriented[edge][1]:
            _fail('MESH_ORIENTATION', 'Adjacent faces traverse a shared edge in the same direction', edge=list(edge))
    for v, link in vertex_links.items():
        if any(len(neighbors) > 2 for neighbors in link.values()):
            _fail('MESH_NONMANIFOLD_VERTEX', 'Vertex link branches', vertex_index=v)
        reached, pending = set(), [next(iter(link))]
        while pending:
            n = pending.pop()
            if n in reached: continue
            reached.add(n); pending.extend(link[n]-reached)
        if len(reached) != len(link) or sum(len(n)==1 for n in link.values()) not in (0, 2):
            _fail('MESH_NONMANIFOLD_VERTEX', 'Vertex link is not one cycle or one boundary path', vertex_index=v)
    if set(adjacency) != set(range(len(vertices))):
        _fail('MESH_LOOSE_VERTEX', 'Semantic map includes unused vertices')
    for key, idx in fm.items(): semantic_faces[key] = _rotated([inverse_vm[v] for v in faces[idx]])
    crease_map = {}
    for crease in _list(mesh.get('edge_creases', []), 'edge_creases', len(edges)):
        if not isinstance(crease, (list, tuple)) or len(crease) != 3:
            _fail('MESH_CREASE_INVALID', 'Creases require two actual indices and a value')
        a, b, value = crease
        if type(a) is not int or type(b) is not int:
            _fail('MESH_CREASE_INVALID', 'Crease endpoints must be actual integer indices')
        edge = _edge(a, b); value = _number(value, 'crease')
        if edge not in edges or edge in crease_map or not 0 <= value <= 1:
            _fail('MESH_CREASE_INVALID', 'Invalid, duplicate, or absent crease edge')
        crease_map[edge] = value
    if set(vm).intersection(fm):
        _fail('IDENTITY_MAP_INVALID', 'Vertex and face semantic IDs cannot collide')
    for group, field, limit in (('loops', 'vertex_ids', 1000000), ('regions', 'face_ids', MAX_FACES), ('boundary_ports', 'point_order', 1000000)):
        records = _list(structure[group], group, 10000)
        total = 0
        for row in records:
            if not isinstance(row, dict): _fail('STRUCTURE_INPUT', 'Entity must be an object')
            total += len(_list(row.get(field), field))
            if total > limit: _fail('STRUCTURE_BUDGET_EXCEEDED', 'Cumulative witness membership exceeds host budget', group=group, limit=limit)
    all_ids, loops, regions, ports = set(vm)|set(fm), {}, {}, {}
    def entity_id(record, kind):
        key = _id(record['id'])
        if key in all_ids: _fail('IDENTITY_MAP_INVALID', 'Entity IDs must be globally unique', semantic_id=key)
        all_ids.add(key)
        return key
    for record in _list(structure['loops'], 'loops', 10000):
        _keys(record, ('id', 'role', 'vertex_ids', 'closed', 'normal', 'anchor'), ('crease', 'expected_valence'), 'loop')
        key = entity_id(record, 'loop'); _id(record['role'])
        ids = [_id(x) for x in _list(record['vertex_ids'], 'loop vertices')]
        if record['closed'] is not True or len(ids) < 3 or len(set(ids)) != len(ids) or any(v not in vm for v in ids):
            _fail('LOOP_INVALID', 'Only distinct mapped closed cycles are supported', loop_id=key)
        indices = [vm[v] for v in ids]; actual_edges = _cycle_edges(indices)
        if any(e not in edges for e in actual_edges):
            _fail('LOOP_EDGE_MISSING', 'Declared loop does not follow actual mesh edges', loop_id=key)
        normal = _unit(record['normal'], 'loop normal', unit_eps)
        area_normal = _normal([vertices[i] for i in indices])
        if _dot(area_normal, normal) <= 2*area_eps:
            _fail('LOOP_ORDER_INVALID', 'Loop winding conflicts with declared normal or is degenerate', loop_id=key)
        anchor = record['anchor']
        _keys(anchor, ('vertex_id', 'position_mm'), label='loop anchor')
        if anchor['vertex_id'] != ids[0]:
            _fail('LOOP_ANCHOR_INVALID', 'Loop starts at its declared stable anchor', loop_id=key)
        position = _vector(anchor['position_mm'], 'anchor position')
        if _norm(_sub(vertices[indices[0]], position)) > eps:
            _fail('LOOP_ANCHOR_INVALID', 'Actual anchor moved beyond declared tolerance', loop_id=key)
        if 'crease' in record:
            value = _number(record['crease'], 'loop crease')
            if not 0 <= value <= 1 or any(abs(crease_map.get(e, 0.)-value) > unit_eps for e in actual_edges):
                _fail('LOOP_CREASE_MISMATCH', 'Declared crease differs from actual edges', loop_id=key)
        if 'expected_valence' in record:
            valence = record['expected_valence']
            if type(valence) is not int or valence < 2 or any(len(adjacency[v]) != valence for v in indices):
                _fail('LOOP_VALENCE_MISMATCH', 'Declared valence differs from actual adjacency', loop_id=key)
        loops[key] = {'indices': indices, 'edges': set(actual_edges), 'record': record}
    for record in _list(structure['regions'], 'regions', 10000):
        _keys(record, ('id', 'role', 'feature_id', 'face_ids', 'boundary_loop_ids'), label='region')
        key = entity_id(record, 'region'); _id(record['role']); _id(record['feature_id'])
        ids = [_id(x) for x in _list(record['face_ids'], 'region faces', MAX_FACES)]
        bounds = [_id(x) for x in _list(record['boundary_loop_ids'], 'region boundaries', 10000)]
        if not ids or len(ids) != len(set(ids)) or any(f not in fm for f in ids) or len(bounds) != len(set(bounds)) or any(b not in loops for b in bounds):
            _fail('REGION_INVALID', 'Region must reference distinct actual faces and existing loops', region_id=key)
        face_indices = [fm[f] for f in ids]
        region_edges = Counter(e for i in face_indices for e in _cycle_edges(list(faces[i])))
        boundary = {e for e, count in region_edges.items() if count == 1}
        claimed = Counter(e for b in bounds for e in loops[b]['edges'])
        if set(claimed) != boundary or any(count != 1 for count in claimed.values()):
            _fail('REGION_BOUNDARY_MISMATCH', 'Declared loops do not equal actual region boundary', region_id=key, missing_edges=len(boundary-set(claimed)), unexpected_edges=len(set(claimed)-boundary))
        directed_boundary = {}
        for i in face_indices:
            seq = list(faces[i])
            for a, b in zip(seq, seq[1:]+seq[:1]):
                if _edge(a, b) in boundary: directed_boundary[_edge(a, b)] = (a, b)
        for bound in bounds:
            seq = loops[bound]['indices']
            for a, b in zip(seq, seq[1:]+seq[:1]):
                if directed_boundary[_edge(a, b)] != (a, b):
                    _fail('REGION_BOUNDARY_ORDER', 'Loop winding disagrees with actual region boundary', region_id=key, loop_id=bound)
        # Regions are edge-connected; touching at only one vertex is not a patch.
        face_graph = defaultdict(set); edge_faces = defaultdict(list)
        for i in face_indices:
            for e in _cycle_edges(list(faces[i])): edge_faces[e].append(i)
        for ids2 in edge_faces.values():
            if len(ids2) == 2:
                a, b = ids2; face_graph[a].add(b); face_graph[b].add(a)
        reached, pending = set(), [face_indices[0]]
        while pending:
            i = pending.pop()
            if i in reached: continue
            reached.add(i); pending.extend(face_graph[i]-reached)
        if reached != set(face_indices):
            _fail('REGION_DISCONNECTED', 'Region is not edge-connected', region_id=key)
        regions[key] = {'faces': set(face_indices), 'record': record}
    ownership = Counter(i for region in regions.values() for i in region['faces'])
    if set(ownership) != set(range(len(faces))) or any(n != 1 for n in ownership.values()):
        _fail('REGION_COVERAGE_INVALID', 'Regions must partition all actual faces without overlap')
    for record in _list(structure['boundary_ports'], 'boundary_ports', 10000):
        _keys(record, ('id', 'loop_id', 'region_id', 'point_order', 'frame', 'allowed_transitions'), label='boundary port')
        key = entity_id(record, 'boundary port')
        loop_id, region_id = _id(record['loop_id']), _id(record['region_id'])
        if loop_id not in loops or region_id not in regions or loop_id not in regions[region_id]['record']['boundary_loop_ids']:
            _fail('PORT_BOUNDARY_INVALID', 'Port must use an actual boundary loop of its region', port_id=key)
        if record['point_order'] != loops[loop_id]['record']['vertex_ids']:
            _fail('PORT_ORDER_MISMATCH', 'Port order must match its anchored semantic loop', port_id=key)
        frame = record['frame']
        _keys(frame, ('origin_mm', 'x_axis', 'y_axis', 'normal'), label='port frame')
        origin = _vector(frame['origin_mm'], 'frame origin')
        x = _unit(frame['x_axis'], 'frame x', unit_eps)
        y = _unit(frame['y_axis'], 'frame y', unit_eps)
        z = _unit(frame['normal'], 'frame normal', unit_eps)
        if abs(_dot(x, y)) > unit_eps or _norm(_sub(_cross(x, y), z)) > unit_eps:
            _fail('FRAME_INVALID', 'Port frame must be right-handed and orthonormal', port_id=key)
        if _norm(_sub(z, loops[loop_id]['record']['normal'])) > unit_eps:
            _fail('FRAME_INVALID', 'Port normal must match ordered loop normal', port_id=key)
        if any(abs(_dot(_sub(vertices[v], origin), z)) > eps for v in loops[loop_id]['indices']):
            _fail('PORT_NOT_PLANAR', 'This boundary-port version only supports planar loops', port_id=key)
        transitions = [_id(x) for x in _list(record['allowed_transitions'], 'port transitions', 8)]
        if not transitions or len(set(transitions)) != len(transitions) or any(t not in ('identity', 'reverse', 'resample_explicit') for t in transitions):
            _fail('PORT_TRANSITION_UNSUPPORTED', 'Unknown or duplicate transition family', port_id=key)
        ports[key] = {'record': record}
    return {
        'schema_version': STRUCTURE_VERSION, 'status': 'pass',
        'geometry_signature': fingerprint({k: vertices[i] for k, i in vm.items()}),
        'topology_signature': fingerprint(semantic_faces),
        'attribute_signature': fingerprint(sorted([sorted([inverse_vm[a], inverse_vm[b]])+[float(value)] for (a,b),value in crease_map.items() if value != 0.])),
        'structure_signature': fingerprint({k: structure[k] for k in ('schema_version', 'length_unit', 'regions', 'loops', 'boundary_ports', 'tolerances')}),
        'counts': {'vertices': len(vertices), 'faces': len(faces), 'triangles': sum(len(f) == 3 for f in faces), 'quads': sum(len(f) == 4 for f in faces), 'actual_boundary_edges': sum(n == 1 for n in edges.values()), 'regions': len(regions), 'loops': len(loops), 'boundary_ports': len(ports)},
        'checked': ['indexed_mesh', 'connected_vertex_fans', 'identity_bijections', 'actual_edges', 'loop_order_anchors', 'actual_region_boundaries', 'port_frames'],
        'not_checked': ['self_intersection', 'global_shape', 'subdivision_surface', 'visual_quality', 'native_extraction', 'reference_approval'],
        'qualification': 'not_run',
    }


def verify_selection(mesh, structure, selection):
    """Resolve only exact semantic selections tied to both current signatures."""
    report = validate_structure(mesh, structure)
    _keys(selection, ('topology_signature', 'geometry_signature', 'attribute_signature', 'structure_signature', 'entity_ids'), label='selection')
    if any(selection[k] != report[k] for k in ('topology_signature', 'geometry_signature', 'attribute_signature', 'structure_signature')):
        _fail('SELECTION_STALE', 'Selection does not bind the current actual geometry and topology')
    ids = [_id(x) for x in _list(selection['entity_ids'], 'selection IDs', 10000)]
    available = {r['id'] for group in ('regions', 'loops', 'boundary_ports') for r in structure[group]}
    if not ids or len(ids) != len(set(ids)) or any(v not in available for v in ids):
        _fail('SELECTION_INVALID', 'Selection contains missing or repeated semantic entities')
    return {'status': 'pass', 'entity_ids': list(ids), 'topology_signature': report['topology_signature'], 'geometry_signature': report['geometry_signature']}


def verify_edit(before_mesh, before_structure, after_mesh, after_structure, contract):
    """Prove declared coordinate/topology invariants with explicit ID lineage.

    Topology changes require total old/new entity coverage. One-to-many or
    many-to-one lineage is accepted only as a declaration and invalidates all
    dependent selections. Nearest-point mapping is deliberately absent.
    """
    before = validate_structure(before_mesh, before_structure)
    after = validate_structure(after_mesh, after_structure)
    _keys(contract, ('topology_policy', 'before_topology_signature', 'before_geometry_signature', 'before_attribute_signature', 'before_structure_signature', 'affected_vertex_ids', 'affected_face_ids', 'affected_entity_ids', 'lineage'), label='edit contract')
    for kind in ('topology', 'geometry', 'attribute', 'structure'):
        if contract['before_'+kind+'_signature'] != before[kind+'_signature']:
            _fail('SELECTION_STALE', 'Edit input fingerprints are stale')
    if contract['topology_policy'] not in ('preserve', 'explicit_change'):
        _fail('EDIT_CONTRACT_INVALID', 'Unknown topology policy')
    affected_v = [_id(x) for x in _list(contract['affected_vertex_ids'], 'affected vertices')]
    affected_f = [_id(x) for x in _list(contract['affected_face_ids'], 'affected faces', MAX_FACES)]
    if len(affected_v) != len(set(affected_v)) or any(x not in before_structure['vertex_map'] for x in affected_v) or len(affected_f) != len(set(affected_f)) or any(x not in before_structure['face_map'] for x in affected_f):
        _fail('EDIT_CONTRACT_INVALID', 'Affected scope must identify distinct existing elements')
    affected_e = [_id(x) for x in _list(contract['affected_entity_ids'], 'affected entities', 30000)]
    records0 = {r['id']: r for group in ('regions', 'loops', 'boundary_ports') for r in before_structure[group]}
    records1 = {r['id']: r for group in ('regions', 'loops', 'boundary_ports') for r in after_structure[group]}
    if len(affected_e) != len(set(affected_e)) or any(x not in records0 for x in affected_e):
        _fail('EDIT_CONTRACT_INVALID', 'Affected entities must identify distinct existing records')
    for key in set(records0)-set(affected_e):
        if key not in records1 or records0[key] != records1[key]:
            _fail('NON_TARGET_CHANGED', 'Semantic witness outside affected scope changed', entity_id=key)
    vm0, vm1 = before_structure['vertex_map'], after_structure['vertex_map']
    fm0, fm1 = before_structure['face_map'], after_structure['face_map']
    iv0, iv1 = {v:k for k,v in vm0.items()}, {v:k for k,v in vm1.items()}
    for key in set(vm0)-set(affected_v):
        if key not in vm1 or before_mesh['vertices'][vm0[key]] != after_mesh['vertices'][vm1[key]]:
            _fail('NON_TARGET_CHANGED', 'Coordinate or identity outside affected scope changed', vertex_id=key)
    def incidence(mesh, inverse):
        found = defaultdict(set)
        for face in mesh['faces']:
            ids = [inverse[v] for v in face]
            for a, b in zip(ids, ids[1:]+ids[:1]):
                found[a].add(b); found[b].add(a)
        return found
    adjacent0, adjacent1 = incidence(before_mesh, iv0), incidence(after_mesh, iv1)
    for key in set(vm0)-set(affected_v):
        if adjacent0[key] != adjacent1[key]:
            _fail('NON_TARGET_CHANGED', 'Adjacency outside affected scope changed', vertex_id=key)
    for key in set(fm0)-set(affected_f):
        if key not in fm1 or _rotated([iv0[v] for v in before_mesh['faces'][fm0[key]]]) != _rotated([iv1[v] for v in after_mesh['faces'][fm1[key]]]):
            _fail('NON_TARGET_CHANGED', 'Connectivity outside affected scope changed', face_id=key)
    if before['attribute_signature'] != after['attribute_signature']:
        _fail('ATTRIBUTE_INVARIANT_FAILED', 'This edit proof does not authorize changing crease attributes')
    topology_changed = before['topology_signature'] != after['topology_signature']
    if contract['topology_policy'] == 'preserve' and (topology_changed or set(vm0) != set(vm1)):
        _fail('TOPOLOGY_INVARIANT_FAILED', 'A topology-preserving edit changed connectivity or identity')
    lineage = _list(contract['lineage'], 'lineage', MAX_VERTICES+MAX_FACES)
    old_entities = set(vm0)|set(fm0)|{r['id'] for group in ('regions', 'loops', 'boundary_ports') for r in before_structure[group]}
    new_entities = set(vm1)|set(fm1)|{r['id'] for group in ('regions', 'loops', 'boundary_ports') for r in after_structure[group]}
    def kinds(s):
        out = {k: 'vertex' for k in s['vertex_map']} | {k: 'face' for k in s['face_map']}
        for group in ('regions', 'loops', 'boundary_ports'):
            out.update({r['id']: group for r in s[group]})
        return out
    kinds0, kinds1 = kinds(before_structure), kinds(after_structure)
    used_old, used_new = set(), set()
    for row in lineage:
        _keys(row, ('relation', 'old', 'new'), label='lineage row')
        old = [_id(x) for x in _list(row['old'], 'lineage old', len(old_entities))]; new = [_id(x) for x in _list(row['new'], 'lineage new', len(new_entities))]
        if len(set(old)) != len(old) or len(set(new)) != len(new) or not set(old) <= old_entities or not set(new) <= new_entities or used_old.intersection(old) or used_new.intersection(new):
            _fail('IDENTITY_LINEAGE_INVALID', 'Lineage is unknown, duplicated, or ambiguous')
        if len({kinds0[x] for x in old}|{kinds1[x] for x in new}) != 1:
            _fail('IDENTITY_LINEAGE_INVALID', 'Lineage cannot change entity kind')
        relation = row['relation']
        valid = ((relation == 'continue' and len(old) == len(new) == 1 and old == new) or
                 (relation == 'split' and len(old) == 1 and len(new) > 1) or
                 (relation == 'merge' and len(old) > 1 and len(new) == 1) or
                 (relation == 'delete' and len(old) == 1 and not new and old[0] not in new_entities) or
                 (relation == 'create' and not old and len(new) == 1 and new[0] not in old_entities))
        if not valid: _fail('IDENTITY_LINEAGE_INVALID', 'Lineage relation/cardinality is not explicit')
        used_old.update(old); used_new.update(new)
    if used_old != old_entities or used_new != new_entities:
        _fail('IDENTITY_LINEAGE_INCOMPLETE', 'Every old/new semantic entity requires explicit lineage')
    if contract['topology_policy'] == 'preserve' and any(row['relation'] != 'continue' for row in lineage):
        _fail('IDENTITY_LINEAGE_INVALID', 'Topology-preserving edits require continued semantic identities')
    return {'status': 'pass', 'topology_changed': topology_changed, 'non_target_coordinates': 'pass', 'non_target_connectivity': 'pass', 'lineage_coverage': 'pass', 'dependent_selections': 'invalidate_and_re_resolve', 'before': before, 'after': after, 'qualification': 'not_run'}
