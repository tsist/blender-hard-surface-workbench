# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual L0 mesh inspection and transparent wire SVGs, without Blender imports.

All indices refer to the supplied actual arrays. Semantic labels are checked
against those arrays, never used to manufacture missing edges or faces. Host
fixtures remain labelled as fixtures. Geometry quality, self-intersection,
visual review and native extraction are deliberately separate evidence gates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import html
import json
import math
from pathlib import Path
import re

from . import contract as c
from .io import RuntimeFailure
from .quad_quality import validate_mesh

REGIONS = ('whole', 'top', 'bottom', 'holewall', 'outer_roundover', 'side')
DIRECTIONS = ('three_quarter', 'bottom_three_quarter', 'top', 'bottom', 'front', 'side')
LIMITS = {'vertices': 50000, 'edges': 100000, 'faces': 50000, 'corners': 200000,
          'semantic_chains': 10000, 'coordinate_mm': 1000000.0,
          'svg_bytes': 8 * 1024 * 1024, 'json_bytes': 8 * 1024 * 1024}
DEFAULT_VIEWS = [
    {'name': 'whole-three-quarter', 'region': 'whole', 'direction': 'three_quarter'},
    {'name': 'whole-bottom-three-quarter', 'region': 'whole', 'direction': 'bottom_three_quarter'},
    {'name': 'top-isolated', 'region': 'top', 'direction': 'top'},
    {'name': 'bottom-isolated', 'region': 'bottom', 'direction': 'bottom'},
    {'name': 'holewall-isolated', 'region': 'holewall', 'direction': 'three_quarter'},
    {'name': 'roundover-isolated', 'region': 'outer_roundover', 'direction': 'three_quarter'},
    {'name': 'side-isolated', 'region': 'side', 'direction': 'three_quarter'},
]
VIEW = c.obj({
    'name': c.string(pattern=r'^[a-z][a-z0-9-]{0,47}$', maxLength=48),
    'region': c.enum(*REGIONS), 'direction': c.enum(*DIRECTIONS),
    'width': c.optional_default(c.integer(minimum=320, maximum=2000), 1600),
    'height': c.optional_default(c.integer(minimum=240, maximum=2000), 1200),
    'labels': c.optional_default(c.enum('both', 'edges', 'faces', 'none'), 'both'),
    'label_budget': c.optional_default(c.integer(minimum=0, maximum=2000), 160),
}, ['name', 'region', 'direction'])
REQUEST = c.obj({
    'schema_version': c.const('1.0'), 'command': c.const('hardsurface.mesh.inspect'),
    'params': c.obj({
        'request_id': c.string(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', minLength=1, maxLength=128),
        'source': c.FILE,
        'feature_ids': c.array(c.IDENT, 1, 32, uniqueItems=True),
        'object_ids': c.array(c.UUID, 1, 32, uniqueItems=True),
        'views': c.optional_default(c.array(VIEW, 1, 16), DEFAULT_VIEWS),
        'self_intersections': c.optional_default(c.BOOL, False),
        'max_tree_rss_bytes': c.optional_default(c.integer(minimum=1024**3, maximum=4*1024**3), 1024**3),
        'cpu_threads': c.optional_default(c.integer(minimum=1, maximum=4), 2),
        'wall_seconds': c.optional_default(c.number(exclusiveMinimum=0, maximum=600), 300),
    }, ['request_id', 'source'], **{'not': {'required': ['feature_ids', 'object_ids']}}),
})


def schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Hard Surface Workbench actual L0 source mesh inspection 1.0',
            **deepcopy(REQUEST)}


def validate_request(request):
    if isinstance(request, (str, bytes, bytearray)):
        request = c.strict_loads(request)
    c._walk_limits(request)
    if len(c.canonical_bytes(request)) > c.MAX_BYTES:
        raise c.ContractError('LIMIT_EXCEEDED', 'Request exceeds 2 MiB')
    result = c._validate(request, REQUEST)
    c._paths(result)
    p = result['params']
    if 'feature_ids' in p and 'object_ids' in p:
        raise c.ContractError('INVALID_REQUEST', 'feature_ids and object_ids are mutually exclusive')
    if Path(p['source']['file']).suffix.lower() != '.blend':
        raise c.ContractError('INVALID_REQUEST', 'Source must be a saved .blend file')
    if len({v['name'] for v in p['views']}) != len(p['views']):
        raise c.ContractError('INVALID_REQUEST', 'View names must be unique')
    if type(p['max_tree_rss_bytes']) is not int:
        raise c.ContractError('INVALID_REQUEST', 'Memory budget requires integer bytes')
    return result


validate = normalize_request = validate_request


def _fail(message, **details):
    raise RuntimeFailure('MESH_INSPECTION_INPUT', message, **details)


def json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        _fail('Inspection evidence must be bounded finite JSON', reason=str(exc))


def fingerprint(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(x) < 32 or ord(x) == 127 for x in value):
        _fail('Semantic identity must be a bounded printable nonempty string')
    return value


def _map(value, count, label):
    if not isinstance(value, dict) or len(value) != count:
        _fail('Semantic map must cover its actual domain exactly', domain=label)
    for key, index in value.items():
        _id(key)
        if type(index) is not int or not 0 <= index < count:
            _fail('Semantic map contains invalid actual index', domain=label)
    if len(set(value.values())) != count:
        _fail('Semantic map contains duplicated or missing actual indices', domain=label)
    return {index: key for key, index in value.items()}


def _array(value, maximum, label, *, nonempty=True):
    if not isinstance(value, (list, tuple)) or not (1 if nonempty else 0) <= len(value) <= maximum:
        _fail('Actual array size or type outside inspection bound', domain=label)


def _validated(vertices_mm, edges, faces, vertex_map, face_map, face_provenance, region_groups):
    for value, name in ((vertices_mm, 'vertices'), (edges, 'edges'), (faces, 'faces')):
        _array(value, LIMITS[name], name)
    vertices = []
    for i, v in enumerate(vertices_mm):
        if (not isinstance(v, (list, tuple)) or len(v) != 3 or
                any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > LIMITS['coordinate_mm'] for x in v)):
            _fail('Actual coordinate is invalid or nonfinite', vertex_index=i)
        vertices.append([float(x) for x in v])
    inverse_v = _map(vertex_map, len(vertices), 'vertices')
    inverse_f = _map(face_map, len(faces), 'faces')
    if set(vertex_map) & set(face_map):
        _fail('Vertex and face semantic identities must not overlap')
    clean_edges, edge_index = [], {}
    for i, e in enumerate(edges):
        if (not isinstance(e, (list, tuple)) or len(e) != 2 or
                any(type(v) is not int or not 0 <= v < len(vertices) for v in e) or e[0] == e[1]):
            _fail('Actual edge has invalid endpoints', edge_index=i)
        key = tuple(sorted(e))
        if key in edge_index:
            _fail('Duplicated actual edge cannot have unambiguous identity', edge_index=i)
        clean_edges.append(list(e)); edge_index[key] = i
    clean_faces, face_edges, incident = [], [], defaultdict(list)
    corners = 0
    for fi, face in enumerate(faces):
        if (not isinstance(face, (list, tuple)) or not 3 <= len(face) <= 8192 or
                any(type(v) is not int or not 0 <= v < len(vertices) for v in face) or len(set(face)) != len(face)):
            _fail('Actual face has invalid or repeated indices', face_index=fi)
        corners += len(face)
        if corners > LIMITS['corners']:
            _fail('Polygon corner budget exceeded')
        seq = list(face); eids = []
        for a, b in zip(seq, seq[1:]+seq[:1]):
            ei = edge_index.get(tuple(sorted((a, b))))
            if ei is None:
                _fail('Actual edge is missing from a polygon boundary', face_index=fi, vertex_indices=[a, b])
            incident[ei].append((fi, a, b)); eids.append(ei)
        clean_faces.append(seq); face_edges.append(eids)
    if len(incident) != len(edges):
        _fail('Loose actual edges have no supported face or role', edge_indices=sorted(set(range(len(edges))) - set(incident))[:64])
    if not isinstance(face_provenance, (list, tuple)) or len(face_provenance) != len(faces):
        _fail('Face provenance must cover actual polygons one-to-one')
    if region_groups is not None:
        if not isinstance(region_groups, dict):
            _fail('Explicit surface-to-region map must be an object')
        for key, group in region_groups.items():
            _id(key)
            if group not in REGIONS[1:]: _fail('Unsupported region in explicit map')
    provenance = []
    for fi, row in enumerate(face_provenance):
        if not isinstance(row, dict): _fail('Face provenance is not an object', face_index=fi)
        _id(row.get('feature_id')); surface = _id(row.get('surface_id'))
        group = row.get('region_group', (region_groups or {}).get(surface))
        if group not in REGIONS[1:]:
            _fail('Face requires explicit supported region_group; geometry is not guessed', face_index=fi, surface_id=surface)
        if region_groups and surface in region_groups and group != region_groups[surface]:
            _fail('Explicit region mappings conflict', face_index=fi)
        for key in ('support_band', 'curved'):
            if key in row and type(row[key]) is not bool:
                _fail('Face role flag must be boolean', face_index=fi, field=key)
        provenance.append({**deepcopy(row), 'region_group': group})
    # Reject unsafe JSON in optional metadata before running graph algorithms.
    json_bytes(provenance)
    return vertices, clean_edges, clean_faces, inverse_v, inverse_f, provenance, edge_index, face_edges, incident


def _graph_paths(graph):
    """Ordered components of a degree-at-most-two graph, with explicit rejection."""
    unused = set(graph); rows = []
    while unused:
        seed = min(unused); component = set(); todo = [seed]
        while todo:
            n = todo.pop()
            if n in component: continue
            component.add(n); todo.extend(graph[n] - component)
        unused -= component
        if any(len(graph[n]) > 2 for n in component):
            rows.append({'status': 'ambiguous_branch', 'nodes': sorted(component), 'closed': False}); continue
        ends = [n for n in component if len(graph[n]) < 2]
        closed = not ends
        start = min(ends) if ends else min(component)
        order = []; seen = set(); previous = None; current = start
        while current not in seen:
            order.append(current); seen.add(current)
            choices = sorted(graph[current] - ({previous} if previous is not None else set()))
            if not choices: break
            previous, current = current, choices[0]
        if len(order) != len(component) or (closed and current != start):
            rows.append({'status': 'ambiguous_order', 'nodes': sorted(component), 'closed': False})
        else:
            rows.append({'status': 'pass', 'nodes': order, 'closed': closed})
    return rows


def _vertex_path(eids, edges, closed):
    if len(eids) == 1: return edges[eids[0]][:]
    shared = set(edges[eids[0]]) & set(edges[eids[-1 if closed else 1]])
    if len(shared) != 1: return None
    shared = next(iter(shared))
    start = shared if closed else next(v for v in edges[eids[0]] if v != shared)
    seq = [start]
    for ei in eids:
        a, b = edges[ei]
        if seq[-1] not in (a, b): return None
        seq.append(b if seq[-1] == a else a)
    if closed:
        if seq[-1] != start: return None
        seq.pop()
    return seq


def _boundary(face_ids, faces, face_edges, edges, inverse_v):
    use = defaultdict(list)
    for fi in face_ids:
        face = faces[fi]
        for ei, a, b in zip(face_edges[fi], face, face[1:]+face[:1]): use[ei].append((a, b))
    boundary = {ei: rows[0] for ei, rows in use.items() if len(rows) == 1}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for ei, (a, b) in boundary.items(): outgoing[a].append((b, ei)); incoming[b].append(a)
    bad = sorted(v for v in set(outgoing) | set(incoming) if len(outgoing[v]) != 1 or len(incoming[v]) != 1)
    if bad or any(len(rows) > 2 for rows in use.values()):
        return {'status': 'fail', 'boundary_edge_indices': sorted(boundary), 'ambiguous_vertex_indices': bad,
                'ordered_loops': [], 'reason': 'Boundary branches, inconsistent winding, or nonmanifold edges; no order invented'}
    unused = set(boundary); loops = []
    while unused:
        ei = min(unused); start = boundary[ei][0]; current = start; vids = []; eids = []
        while True:
            vids.append(current); nxt, edge = outgoing[current][0]
            if edge not in unused: break
            unused.remove(edge); eids.append(edge); current = nxt
            if current == start: break
        if current != start or len(eids) != len(vids):
            return {'status': 'fail', 'boundary_edge_indices': sorted(boundary), 'ordered_loops': [], 'reason': 'Boundary does not form disjoint oriented cycles'}
        loops.append({'kind': 'ordered_boundary_loop', 'closed': True, 'vertex_indices': vids,
                      'vertex_ids': [inverse_v[v] for v in vids], 'edge_indices': eids})
    return {'status': 'pass', 'boundary_edge_indices': sorted(boundary), 'ordered_loops': loops,
            'orientation': 'Original face winding, not screen clockwise order; no regular-edge-loop claim'}


def analyze_mesh(vertices_mm, edges, faces, *, vertex_map, face_map, face_provenance,
                 semantic_control_loops=(), semantic_chains=(), identity=None,
                 evidence_origin='host_fixture', region_groups=None, require_closed=True):
    """Analyze arrays without mutation. Native callers must supply actual raw data.

    Only region_group metadata or an explicit surface map partitions roles.
    Validity of the declared role is a binding fact, not a geometric shape proof.
    """
    if evidence_origin not in ('host_fixture', 'authored_constructor_arrays', 'blender_raw_object_data'):
        _fail('Unknown evidence origin')
    if type(require_closed) is not bool: _fail('require_closed must be boolean')
    identity = {} if identity is None else deepcopy(identity)
    if not isinstance(identity, dict): _fail('Identity must be a JSON object')
    json_bytes(identity)
    raw_before = fingerprint([vertices_mm, edges, faces, vertex_map, face_map, face_provenance, semantic_control_loops, semantic_chains])
    v, e, f, iv, iff, p, edge_index, fe, incident = _validated(vertices_mm, edges, faces, vertex_map, face_map, face_provenance, region_groups)
    ve, vf = defaultdict(set), defaultdict(set)
    for ei, (a, b) in enumerate(e): ve[a].add(ei); ve[b].add(ei)
    for fi, face in enumerate(f):
        for vi in face: vf[vi].add(fi)
    valences = [len(ve[i]) for i in range(len(v))]
    opposites = {}
    for vi in range(len(v)):
        if valences[vi] != 4 or len(vf[vi]) != 4 or any(len(f[fi]) != 4 for fi in vf[vi]) or any(len(incident[ei]) != 2 for ei in ve[vi]):
            continue
        for ei in ve[vi]:
            face_set = {row[0] for row in incident[ei]}
            opposite = [ej for ej in ve[vi] if ej != ei and not face_set.intersection(row[0] for row in incident[ej])]
            if len(opposite) == 1: opposites[(vi, ei)] = opposite[0]
    regular_graph = {ei: set() for ei in range(len(e))}
    for (vi, ei), ej in opposites.items():
        if opposites.get((vi, ej)) == ei: regular_graph[ei].add(ej)
    regular_paths = []
    for row in _graph_paths(regular_graph):
        if len(row['nodes']) < 2: continue
        vids = _vertex_path(row['nodes'], e, row['closed']) if row['status'] == 'pass' else None
        if vids is None: continue
        regular_paths.append({'kind': 'regular_edge_loop' if row['closed'] else 'regular_edge_chain',
                              'closed': row['closed'], 'edge_indices': row['nodes'], 'vertex_indices': vids,
                              'vertex_ids': [iv[i] for i in vids],
                              'end_vertex_indices': [] if row['closed'] else [vids[0], vids[-1]]})
    declared = []; roles = set(); chain_vertices = 0
    for domain, values in (('semantic_control_loops', semantic_control_loops), ('semantic_chains', semantic_chains)):
        _array(values, LIMITS['semantic_chains'], domain, nonempty=False)
        for raw in values:
            if not isinstance(raw, dict): _fail('Declared chain must be an object')
            role = _id(raw.get('role'))
            if raw.get('expected_valence', 4) not in (None, 4) or type(raw.get('expected_valence', 4)) is bool:
                _fail('Declared control cycle expected_valence must be 4 or null', role=role)
            if 'crease' in raw and (type(raw['crease']) not in (int, float) or not math.isfinite(raw['crease']) or not 0 <= raw['crease'] <= 1):
                _fail('Declared crease must be finite in [0,1]', role=role)
            if role in roles: _fail('Declared loop or chain role is duplicated', role=role)
            roles.add(role)
            ids = raw.get('vertex_ids'); _array(ids, LIMITS['vertices'], 'chain vertex IDs')
            chain_vertices += len(ids)
            if chain_vertices > LIMITS['corners']: _fail('Aggregate semantic chain references exceed bound')
            if any(not isinstance(s, str) or s not in vertex_map for s in ids) or len(ids) != len(set(ids)):
                _fail('Declared chain references missing or duplicate vertex identities', role=role)
            closed = raw.get('closed', True)
            if type(closed) is not bool or len(ids) < (3 if closed else 2): _fail('Invalid declared chain closure')
            vids = [vertex_map[s] for s in ids]; pairs = list(zip(vids, vids[1:]+(vids[:1] if closed else []))); eids = []
            for a, b in pairs:
                ei = edge_index.get(tuple(sorted((a, b))))
                if ei is None: _fail('Declared chain is not made of actual edges', role=role, vertex_indices=[a,b])
                eids.append(ei)
            checks = range(len(vids)) if closed else range(1, len(vids)-1)
            turns = [vids[i] for i in checks if opposites.get((vids[i], eids[i-1])) != eids[i]]
            poles = [i for i in vids if valences[i] != 4]
            regular = not turns and closed
            declared.append({'role': role, 'declared_domain': domain, 'closed': closed,
                             'kind': 'regular_edge_loop' if regular else 'pole_crossing_closed_chain' if poles and closed else 'ordered_closed_edge_chain' if closed else 'ordered_edge_chain',
                             'vertex_indices': vids, 'vertex_ids': list(ids), 'edge_indices': eids,
                             'pole_vertex_indices': poles, 'nonregular_continuation_vertex_indices': turns,
                             'declared_regular_loop_verified': regular if domain == 'semantic_control_loops' and raw.get('expected_valence', 4) == 4 else None})
    strip_graph = {ei: set() for ei in range(len(e))}; crossings = defaultdict(list)
    for fi, eids in enumerate(fe):
        if len(eids) != 4: continue
        for a, b in ((eids[0], eids[2]), (eids[1], eids[3])):
            strip_graph[a].add(b); strip_graph[b].add(a); crossings[tuple(sorted((a,b)))].append(fi)
    strips = []
    for row in _graph_paths(strip_graph):
        if len(row['nodes']) < 2: continue
        nodes = row['nodes']; pairs = list(zip(nodes, nodes[1:]+(nodes[:1] if row['closed'] else [])))
        crossed = [crossings[tuple(sorted(pair))] for pair in pairs]
        ambiguity = row['status'] != 'pass' or any(len(x) != 1 for x in crossed)
        fis = [x[0] for x in crossed if len(x) == 1]
        strips.append({'kind': 'quad_strip', 'status': 'ambiguous' if ambiguity else 'self_crossing' if len(set(fis)) != len(fis) else 'pass',
                       'closed': row['closed'], 'cross_edge_indices': nodes, 'face_indices': fis,
                       'face_ids': [iff[i] for i in fis], 'surface_roles': sorted({p[i]['surface_id'] for i in fis}),
                       'semantics': 'Opposite-edge traversal through actual quads; not a vertex edge-loop or edit authorization'})
    quality = validate_mesh([[x*.001 for x in q] for q in v], f, face_provenance=p,
                            identity=identity, mesh_state='control', include_face_metrics=True,
                            policy={'require_closed': require_closed})
    metrics = {row['face_index']: row for row in quality.pop('face_metrics')}
    regions = {}; ledger = []
    def ledger_row(name, ids):
        area = math.fsum(metrics[i]['area_m2']*1e6 for i in ids if i in metrics)
        return {'role': name, 'face_count': len(ids), 'face_indices': list(ids),
                'area_mm2': area, 'faces_per_mm2': len(ids)/area if area else None,
                'support_band_faces': sum(p[i].get('support_band', False) for i in ids),
                'curved_faces': sum(p[i].get('curved', False) for i in ids),
                'maximum_aspect_ratio': max((metrics[i]['aspect_ratio'] for i in ids if i in metrics and metrics[i]['aspect_ratio'] is not None), default=None),
                'maximum_quad_warpage_degrees': max((metrics[i]['quad_warpage_degrees'] for i in ids if i in metrics and metrics[i]['quad_warpage_degrees'] is not None), default=None)}
    for group in REGIONS:
        ids = [i for i in range(len(f)) if group == 'whole' or p[i]['region_group'] == group]
        regions[group] = {**ledger_row(group, ids), 'status': 'present' if ids else 'absent',
                          'edge_indices': sorted({ei for i in ids for ei in fe[i]}),
                          'boundary': _boundary(ids, f, fe, e, iv)}
    role_faces = defaultdict(list)
    for i, row in enumerate(p): role_faces[row['surface_id']].append(i)
    for role in sorted(role_faces): ledger.append(ledger_row(role, role_faces[role]))
    raw_after = fingerprint([vertices_mm, edges, faces, vertex_map, face_map, face_provenance, semantic_control_loops, semantic_chains])
    if raw_before != raw_after: _fail('Caller arrays changed during read-only inspection')
    actual = {'vertices_mm': v, 'edges': e, 'faces': f}
    semantics = {'vertex_ids': [iv[i] for i in range(len(v))], 'face_ids': [iff[i] for i in range(len(f))], 'face_provenance': p}
    return {'schema_version': '1.0', 'operation': 'hardsurface.mesh.inspect', 'mesh_state': 'L0_control',
            'coordinate_space': 'object_local_mm', 'evidence_origin': evidence_origin, 'identity': identity,
            'mesh_sha256': fingerprint(actual), 'semantic_sha256': fingerprint(semantics),
            'actual_mesh': actual, 'semantics': semantics,
            'counts': {'vertices': len(v), 'edges': len(e), 'faces': len(f), 'face_sides': dict(sorted(Counter(map(len, f)).items()))},
            'regions': regions, 'face_role_ledger': ledger,
            'valence_histogram': dict(sorted(Counter(valences).items())),
            'vertices': [{'vertex_index': i, 'vertex_id': iv[i], 'edge_valence': valences[i],
                          'incident_edge_indices': sorted(ve[i]), 'incident_face_indices': sorted(vf[i]),
                          'pole': valences[i] != 4, 'boundary': any(len(incident[j]) == 1 for j in ve[i])} for i in range(len(v))],
            'edges': [{'edge_index': i, 'vertex_indices': edge, 'vertex_ids': [iv[j] for j in edge],
                       'incident_face_indices': [r[0] for r in incident[i]],
                       'length_mm': math.dist(v[edge[0]], v[edge[1]])} for i, edge in enumerate(e)],
            'face_metrics': [{**metrics[i], 'face_id': iff[i]} for i in sorted(metrics)],
            'regular_paths': regular_paths, 'declared_chains': declared, 'quad_strips': strips,
            'quality': quality,
            'checks': {'input_binding': 'pass', 'actual_edge_face_coverage': 'pass',
                       'native_extraction': 'not_run' if evidence_origin != 'blender_raw_object_data' else 'caller_bridge_required',
                       'polygon_quality': 'pass' if quality['passed'] else 'fail',
                       'declared_regular_loops': 'fail' if any(r['declared_regular_loop_verified'] is False for r in declared) else 'pass',
                       'self_intersections': {'status': 'not_run', 'reason': 'No distinct-face intersection algorithm was run by the host analyzer'},
                       'outward_orientation': 'not_run', 'design_dimensions': 'not_run', 'visual_review': 'not_run'},
            'preservation': {'status': 'pass', 'before_sha256': raw_before, 'after_sha256': raw_after,
                             'scope': 'Caller arrays and semantic declarations; native source checks reported separately'},
            'limitations': ['L0 source topology only; no evaluated surface or beauty render',
                            'Pole means actual edge valence differs from four, including boundary vertices',
                            'Region and role names are verified metadata assignments, not proof of nominal shape',
                            'Density is per actual polygon area; necessity of support rings requires a separate ablation test',
                            'No editability, dimension, self-intersection, visual or qualification pass is inferred from graph validity']}


def render_svg(report, view):
    """Render actual source edges with no hidden-surface removal or beauty render."""
    view = c._validate(view, VIEW)
    mesh = report.get('actual_mesh'); semantics = report.get('semantics')
    if not isinstance(mesh, dict) or not isinstance(semantics, dict): _fail('SVG requires full verified actual mesh evidence')
    if fingerprint(mesh) != report.get('mesh_sha256') or fingerprint(semantics) != report.get('semantic_sha256'):
        _fail('SVG geometry or semantic evidence hash differs')
    region = report['regions'][view['region']]; selected_faces = region['face_indices']; selected_edges = region['edge_indices']
    vertices = mesh['vertices_mm']; edges = mesh['edges']; faces = mesh['faces']
    # Validate a deserialized report, not just its claimed hashes.
    _validated(vertices, edges, faces, {key:i for i,key in enumerate(semantics['vertex_ids'])},
               {key:i for i,key in enumerate(semantics['face_ids'])}, semantics['face_provenance'], None)
    expected_faces = [i for i,row in enumerate(semantics['face_provenance']) if view['region']=='whole' or row['region_group']==view['region']]
    edge_lookup = {tuple(sorted(edge)):i for i,edge in enumerate(edges)}
    expected_edges = sorted({edge_lookup[tuple(sorted((a,b)))] for fi in expected_faces for a,b in zip(faces[fi],faces[fi][1:]+faces[fi][:1])})
    if selected_faces != expected_faces or selected_edges != expected_edges:
        _fail('SVG region selection differs from verified face roles and actual edges')
    if any(type(i) is not int or not 0 <= i < len(faces) for i in selected_faces) or any(type(i) is not int or not 0 <= i < len(edges) for i in selected_edges):
        _fail('SVG selection indices are invalid')
    direction = view['direction']
    bases = {'top': ((1,0,0),(0,1,0)), 'bottom': ((-1,0,0),(0,1,0)),
             'front': ((1,0,0),(0,0,1)), 'side': ((0,1,0),(0,0,1)),
             'three_quarter': ((.8,.6,0),(-.36,.48,.8)),
             'bottom_three_quarter': ((.8,.6,0),(.36,-.48,-.8))}
    right, up = bases[direction]
    points = [(math.fsum(a*b for a,b in zip(v,right)), math.fsum(a*b for a,b in zip(v,up))) for v in vertices]
    used = sorted({vi for ei in selected_edges for vi in edges[ei]})
    width, height = view['width'], view['height']; margin = 62
    if used:
        xmin, xmax = min(points[i][0] for i in used), max(points[i][0] for i in used)
        ymin, ymax = min(points[i][1] for i in used), max(points[i][1] for i in used)
        scale = min((width-2*margin)/max(xmax-xmin, 1e-9), (height-180)/max(ymax-ymin, 1e-9))
    else: xmin=xmax=ymin=ymax=0.; scale=1.
    def xy(i):
        x,y=points[i]; return ((x-(xmin+xmax)/2)*scale+width/2, -(y-(ymin+ymax)/2)*scale+(height-70)/2+25)
    def escape(value): return html.escape(str(value), quote=True)
    metadata = {'view': view, 'identity': report['identity'], 'evidence_origin': report['evidence_origin'],
                'mesh_sha256': report['mesh_sha256'], 'semantic_sha256': report['semantic_sha256'],
                'source_sha256': report.get('source_sha256'), 'coordinate_space': report['coordinate_space'],
                'selected_face_indices': selected_faces, 'selected_edge_indices': selected_edges,
                'render_engine': 'none; direct SVG projection', 'occlusion': 'none; all selected actual source edges shown',
                'mesh_state': 'L0_control', 'new_geometry_created': False}
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<title>Actual L0 source wire inspection</title>', '<metadata>'+escape(json_bytes(metadata).decode())+'</metadata>',
            '<rect width="100%" height="100%" fill="#f6f8fc"/>',
            '<g font-family="sans-serif" fill="#182941">',
            f'<text x="24" y="30" font-size="19">{escape(view["name"])} | L0 actual edges | {escape(report["evidence_origin"])}</text>',
            f'<text x="24" y="53" font-size="13">{len(selected_faces)} faces / {len(selected_edges)} edges; transparent wire, no hidden-line removal</text>', '</g>']
    boundary = set(region['boundary']['boundary_edge_indices'])
    rows.append('<g fill="none" stroke-linecap="round">')
    for ei in selected_edges:
        a,b=edges[ei]; x1,y1=xy(a); x2,y2=xy(b)
        color = '#286dd4' if ei in boundary else '#47576d'; sw=1.4 if ei in boundary else .85
        label = 'E%d %s -- %s' % (ei, semantics['vertex_ids'][a], semantics['vertex_ids'][b])
        rows.append(f'<line data-edge-index="{ei}" x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" stroke="{color}" stroke-width="{sw}"><title>{escape(label)}</title></line>')
    rows.append('</g><g font-family="sans-serif" font-size="10">')
    budget=view['label_budget']; labels=view['labels']; printed=0
    def text_at(x,y,label,color):
        return f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="middle" fill="{color}" stroke="#f6f8fc" stroke-width="2.5" paint-order="stroke">{escape(label)}</text>'
    # Deterministic alternation distributes bounded visible labels over both domains.
    edge_budget=budget if labels=='edges' else budget//2 if labels=='both' else 0
    face_budget=budget if labels=='faces' else budget-edge_budget if labels=='both' else 0
    def subset(values,n):
        if n<=0:return []
        if len(values)<=n:return values
        return [values[(i*len(values))//n] for i in range(n)]
    for ei in subset(selected_edges,edge_budget):
        a,b=edges[ei]; x1,y1=xy(a); x2,y2=xy(b)
        rows.append(text_at((x1+x2)/2,(y1+y2)/2-3,'E%d'%ei,'#195ab1')); printed+=1
    visible_faces=set(subset(selected_faces,face_budget))
    for fi in selected_faces:
        coords=[xy(i) for i in faces[fi]]; x=sum(q[0] for q in coords)/len(coords); y=sum(q[1] for q in coords)/len(coords)
        rows.append(f'<g data-face-index="{fi}" data-face-id="{escape(semantics["face_ids"][fi])}"><title>{escape("F%d %s"%(fi,semantics["face_ids"][fi]))}</title>')
        if fi in visible_faces: rows.append(text_at(x,y,'F%d'%fi,'#647084')); printed+=1
        rows.append('</g>')
    used_set=set(used)
    poles=[row for row in report['vertices'] if row['pole'] and row['vertex_index'] in used_set]
    for row in poles:
        x,y=xy(row['vertex_index']); label='V%d valence=%d %s'%(row['vertex_index'],row['edge_valence'],row['vertex_id'])
        rows.append(f'<circle data-vertex-index="{row["vertex_index"]}" cx="{x:.3f}" cy="{y:.3f}" r="3.1" fill="#ce3f46"><title>{escape(label)}</title></circle>')
    if not used: rows.append(text_at(width/2,height/2,'Region absent in this source mesh','#647084'))
    rows.extend(['</g><g font-family="monospace" font-size="11" fill="#42536b">',
                 f'<text x="24" y="{height-58}">E/F = actual array indices; red = valence poles ({len(poles)}); visible labels={printed}, XML carries all selected IDs</text>',
                 f'<text x="24" y="{height-37}">mesh SHA256 {report["mesh_sha256"]}</text>',
                 f'<text x="24" y="{height-16}">self-intersections: {escape(report["checks"]["self_intersections"]["status"])}; visual/design acceptance: not_run</text>', '</g></svg>'])
    result='\n'.join(rows)+'\n'
    if len(result.encode())>LIMITS['svg_bytes']: _fail('SVG exceeds bounded output size')
    return result
