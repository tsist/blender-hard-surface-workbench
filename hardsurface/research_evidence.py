# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded HOST evidence over explicit linear models; never solves or uses Blender."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import time

MAX_BYTES = 8 * 1024 * 1024
MAX_VERTICES = 10000
MAX_FACES = 20000
MAX_VARIABLES = 256
MAX_COEFFICIENTS = 500000


def schema():
    """Public JSON Schema; cross-field identities and dimensions are runtime gates."""
    def obj(properties, required=None):
        return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
                'additionalProperties': False}
    def array(item, lower=0, upper=None, **extra):
        result = {'type': 'array', 'items': item, 'minItems': lower, **extra}
        if upper is not None:
            result['maxItems'] = upper
        return result
    finite = {'type': 'number'}
    point = array(finite, 3, 3)
    identity = {'type': 'string', 'minLength': 1, 'maxLength': 128}
    digest = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    row = array(finite, 1, MAX_VARIABLES)
    constraint = obj({'a': row, 'b': finite})
    properties = {
        'schema_version': {'const': 'research-evidence/1.0'},
        'source': obj({'vertices': array(point, 4, MAX_VERTICES),
                       'faces': array(array({'type': 'integer', 'minimum': 0}, 4, 4, uniqueItems=True), 1, MAX_FACES),
                       'vertex_ids': array(identity, 4, MAX_VERTICES, uniqueItems=True)}),
        'source_sha256': digest,
        'model': obj({'variable_count': {'type': 'integer', 'minimum': 1, 'maximum': MAX_VARIABLES},
                      'basis': array(row, 12, MAX_VERTICES*3),
                      'inequalities': array(constraint, 0, MAX_COEFFICIENTS),
                      'equalities': array(constraint, 0, MAX_COEFFICIENTS),
                      'bounds': array(array(finite, 2, 2), 1, MAX_VARIABLES)}),
        'external_lp': obj({'source_sha256': digest, 'model_sha256': digest,
                            'status': {'enum': ['optimal', 'feasible', 'infeasible', 'not_run', 'failed', 'timeout']},
                            'q': row}, ['source_sha256', 'model_sha256', 'status']),
        'protected_vertex_ids': array(identity, 0, MAX_VERTICES, uniqueItems=True),
        'coverage': obj({'origin': point, 'cell_size': {'type': 'number', 'exclusiveMinimum': 0},
                         'required_bins': array(array({'type': 'integer'}, 3, 3), 1, MAX_VERTICES, uniqueItems=True)}),
        'quality': obj({'max_warp_degrees': {'type': 'number', 'minimum': 0, 'maximum': 180},
                        'min_corner_degrees': {'type': 'number', 'minimum': 0, 'maximum': 180},
                        'max_edge_ratio': {'type': 'number', 'minimum': 1}}),
        'budgets': obj({'wall_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 120},
                        'operations': {'type': 'integer', 'minimum': 1, 'maximum': 5000000}}),
        'residual_tolerance': {'type': 'number', 'minimum': 0, 'maximum': 0.001},
    }
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Generic HOST research evidence 1.0', **obj(properties)}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    need(type(value) in (int, float) and math.isfinite(value), 'Expected finite number')
    return float(value)


def vector(value, size):
    need(isinstance(value, list) and len(value) == size, 'Vector dimension mismatch')
    return [number(x) for x in value]


def exact_keys(value, required, optional=()):
    need(isinstance(value, dict), 'Expected object')
    need(set(required) <= set(value) <= set(required) | set(optional), 'Missing or unknown fields')


class Budget:
    def __init__(self, seconds, operations, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.seconds = number(seconds)
        need(0 < self.seconds <= 120, 'wall_seconds must be in (0, 120]')
        need(type(operations) is int and 0 < operations <= 5000000, 'Invalid operation budget')
        self.limit = operations
        self.used = 0

    def check(self, cost=1):
        self.used += cost
        if self.used > self.limit or self.clock() - self.started >= self.seconds:
            raise TimeoutError('Research evidence budget exhausted')

    def receipt(self):
        return {'wall_seconds_limit': self.seconds, 'elapsed_seconds': self.clock() - self.started,
                'operation_limit': self.limit, 'operations_used': self.used,
                'enforcement': 'cooperative checks plus bounded input; no subprocess watchdog'}


def dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def norm(a):
    return math.hypot(*a)


def angle(a, b):
    d = norm(a) * norm(b)
    need(d > 0 and math.isfinite(d), 'Degenerate or overflowing geometry')
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(a, b) / d))))


def parse_source(source, budget):
    exact_keys(source, ('vertices', 'faces', 'vertex_ids'))
    vertices = source['vertices']
    need(isinstance(vertices, list) and 4 <= len(vertices) <= MAX_VERTICES, 'Invalid vertex count')
    vertices = [vector(p, 3) for p in vertices]
    ids = source['vertex_ids']
    need(isinstance(ids, list) and len(ids) == len(vertices), 'Vertex identity count mismatch')
    need(all(isinstance(x, str) and 0 < len(x) <= 128 for x in ids) and len(set(ids)) == len(ids), 'Invalid vertex identities')
    faces = source['faces']
    need(isinstance(faces, list) and 1 <= len(faces) <= MAX_FACES, 'Invalid face count')
    for face in faces:
        budget.check(4)
        need(isinstance(face, list) and len(face) == 4 and len(set(face)) == 4, 'Require distinct quad corners')
        need(all(type(i) is int and 0 <= i < len(vertices) for i in face), 'Invalid face index')
    need(set(i for f in faces for i in f) == set(range(len(vertices))), 'Unused vertices')
    return vertices, faces, ids


def topology(faces, vertex_count, budget):
    edges = defaultdict(list)
    links = [defaultdict(list) for _ in range(vertex_count)]
    face_keys = Counter()
    for face in faces:
        budget.check(4)
        face_keys[tuple(sorted(face))] += 1
        for j, a in enumerate(face):
            b = face[(j+1) % 4]
            edges[tuple(sorted((a, b)))].append((a, b))
            previous, following = face[(j-1) % 4], b
            links[a][previous].append(following)
            links[a][following].append(previous)
    boundary = sum(len(v) == 1 for v in edges.values())
    nonmanifold = sum(len(v) > 2 for v in edges.values())
    winding = sum(len(v) == 2 and v[0] == v[1] for v in edges.values())
    bad_links = 0
    for link in links:
        budget.check(len(link))
        seen = set()
        stack = [next(iter(link))] if link else []
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(x for x in link[node] if x not in seen)
        if len(seen) != len(link) or not link or any(len(v) != 2 for v in link.values()):
            bad_links += 1
    adjacency = [set() for _ in range(vertex_count)]
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    unseen = set(range(vertex_count))
    components = 0
    while unseen:
        components += 1
        pending = [unseen.pop()]
        while pending:
            vertex = pending.pop()
            budget.check()
            neighbours = adjacency[vertex] & unseen
            unseen.difference_update(neighbours)
            pending.extend(neighbours)
    duplicates = sum(v-1 for v in face_keys.values())
    passed = not (boundary or nonmanifold or winding or bad_links or duplicates)
    return {'status': 'pass' if passed else 'fail', 'boundary_edges': boundary,
            'nonmanifold_edges': nonmanifold, 'winding_errors': winding,
            'noncycle_vertex_links': bad_links, 'duplicate_faces': duplicates,
            'connected_components': components, 'vertices': vertex_count, 'edges': len(edges),
            'faces': len(faces), 'euler_characteristic': vertex_count-len(edges)+len(faces),
            'scope': 'closed oriented local 2-manifold; not global outwardness or intersection'}


def quality_thresholds(thresholds):
    exact_keys(thresholds, ('max_warp_degrees', 'min_corner_degrees', 'max_edge_ratio'))
    warp_limit = number(thresholds['max_warp_degrees'])
    corner_limit = number(thresholds['min_corner_degrees'])
    ratio_limit = number(thresholds['max_edge_ratio'])
    need(0 <= warp_limit <= 180 and 0 <= corner_limit <= 180 and ratio_limit >= 1, 'Invalid quality thresholds')
    return warp_limit, corner_limit, ratio_limit


def quality(vertices, faces, thresholds, budget):
    warp_limit, corner_limit, ratio_limit = quality_thresholds(thresholds)
    warps, corners, ratios = [], [], []
    invalid = 0
    diagonal_invalid = [0, 0]
    worst_warp = worst_ratio = worst_corner = None
    for face_index, face in enumerate(faces):
        budget.check(32)
        p = [vertices[i] for i in face]
        face_invalid = False
        normals = []
        for diagonal, triangles in enumerate((((0, 1, 2), (0, 2, 3)), ((0, 1, 3), (1, 2, 3)))):
            try:
                n = [cross(sub(p[b], p[a]), sub(p[c], p[a])) for a, b, c in triangles]
                need(all(norm(x) > 0 for x in n), 'Zero area triangle')
                warp = number(angle(*n))
                warps.append(warp)
                if worst_warp is None or warp > worst_warp['value']:
                    worst_warp = {'face_index': face_index, 'diagonal': diagonal, 'value': warp}
                normals.extend(n)
                need(number(dot(n[0], n[1])) > 0, 'Incompatible diagonal triangles')
            except (ValueError, OverflowError):
                diagonal_invalid[diagonal] += 1
                face_invalid = True
        try:
            lengths = [number(norm(sub(p[(i+1) % 4], p[i]))) for i in range(4)]
            need(min(lengths) > 0, 'Zero edge')
            ratio = number(max(lengths) / min(lengths))
            ratios.append(ratio)
            if worst_ratio is None or ratio > worst_ratio['value']:
                worst_ratio = {'face_index': face_index, 'value': ratio}
            area = [number(math.fsum(n[a] for n in normals)) for a in range(3)]
            for i in range(4):
                forward, backward = sub(p[(i+1) % 4], p[i]), sub(p[(i-1) % 4], p[i])
                corner = number(angle(forward, backward))
                corners.append(corner)
                if worst_corner is None or corner < worst_corner['value']:
                    worst_corner = {'face_index': face_index, 'corner': i, 'value': corner}
                if number(dot(cross(forward, backward), area)) <= 0:
                    face_invalid = True
        except (ValueError, OverflowError):
            face_invalid = True
        invalid += face_invalid
    metrics = {'max_warp_both_diagonals_degrees': max(warps, default=None),
               'rms_warp_both_diagonals_degrees': math.sqrt(math.fsum(w*w for w in warps)/len(warps)) if warps else None,
               'warp_sample_count': len(warps), 'expected_warp_sample_count': 2*len(faces),
               'invalid_diagonal_counts': diagonal_invalid,
               'worst_warp': worst_warp, 'worst_edge_ratio': worst_ratio, 'worst_corner': worst_corner,
               'min_corner_degrees': min(corners, default=None), 'max_edge_ratio': max(ratios, default=None)}
    passed = (not invalid and len(warps) == 2*len(faces) and max(warps) <= warp_limit
              and min(corners) >= corner_limit and max(ratios) <= ratio_limit)
    return {'status': 'pass' if passed else 'fail', 'invalid_faces': invalid,
            'thresholds': thresholds, **metrics}


def coverage_specification(specification):
    exact_keys(specification, ('origin', 'cell_size', 'required_bins'))
    origin = vector(specification['origin'], 3)
    cell = number(specification['cell_size'])
    need(cell > 0, 'cell_size must be positive')
    required = specification['required_bins']
    need(isinstance(required, list) and 0 < len(required) <= MAX_VERTICES, 'Empty or excessive coverage domain')
    need(all(isinstance(k, list) and len(k) == 3 and all(type(i) is int for i in k) for k in required), 'Invalid bin key')
    need(len({tuple(k) for k in required}) == len(required), 'Duplicate required coverage bins')
    return origin, cell, {tuple(k) for k in required}


def coverage(vertices, specification, budget):
    origin, cell, required = coverage_specification(specification)
    occupied = set()
    for p in vertices:
        budget.check(3)
        occupied.add(tuple(math.floor((p[i]-origin[i])/cell) for i in range(3)))
    missing = required - occupied
    return {'status': 'pass' if not missing else 'fail', 'required_count': len(required),
            'covered_required_count': len(required & occupied), 'missing_bins': sorted(missing),
            'extra_bin_count': len(occupied-required),
            'scope': 'candidate vertex samples only; not curvature, continuous surface, or CC coverage'}


def parse_model(model, vertex_count, budget):
    exact_keys(model, ('variable_count', 'basis', 'inequalities', 'equalities', 'bounds'))
    n = model['variable_count']
    need(type(n) is int and 1 <= n <= MAX_VARIABLES, 'Invalid variable count')
    basis = model['basis']
    need(isinstance(basis, list) and len(basis) == vertex_count*3, 'Basis row mismatch')
    rows = len(basis)
    for group in ('inequalities', 'equalities'):
        need(isinstance(model[group], list), 'Expected constraint list')
        rows += len(model[group])
    need(rows*n <= MAX_COEFFICIENTS, 'Coefficient limit exceeded')
    for row in basis:
        budget.check(n)
        vector(row, n)
    for group in ('inequalities', 'equalities'):
        for row in model[group]:
            budget.check(n)
            exact_keys(row, ('a', 'b'))
            vector(row['a'], n)
            number(row['b'])
    need(isinstance(model['bounds'], list) and len(model['bounds']) == n, 'Bounds dimension mismatch')
    for pair in model['bounds']:
        lo, hi = vector(pair, 2)
        need(lo <= hi, 'Reversed bounds')
    return n


def verify_external(external, model, source_sha, tolerance, budget):
    exact_keys(external, ('source_sha256', 'model_sha256', 'status'), ('q',))
    need(external['source_sha256'] == source_sha and external['model_sha256'] == canonical_sha(model), 'External evidence identity mismatch')
    status = external['status']
    need(status in ('optimal', 'feasible', 'infeasible', 'not_run', 'failed', 'timeout'), 'Unknown external status')
    if 'q' not in external:
        return {'status': 'not_run', 'external_status': status,
                'reason': 'No candidate vector; external infeasibility is unverified, not a proof'}, None
    need(status in ('optimal', 'feasible'), 'Candidate vector contradicts external status')
    q = vector(external['q'], model['variable_count'])
    inequality = equality = bounds = 0.0
    for row in model['inequalities']:
        budget.check(len(q))
        inequality = max(inequality, number(dot(row['a'], q)-row['b']))
    for row in model['equalities']:
        budget.check(len(q))
        equality = max(equality, number(abs(dot(row['a'], q)-row['b'])))
    for x, (lo, hi) in zip(q, model['bounds']):
        budget.check()
        bounds = max(bounds, number(lo-x), number(x-hi))
    need(all(math.isfinite(x) for x in (inequality, equality, bounds)), 'Nonfinite constraint residual')
    passed = max(inequality, equality, bounds) <= tolerance
    return {'status': 'pass' if passed else 'fail', 'external_status': status,
            'max_inequality_violation': inequality, 'max_equality_residual': equality,
            'max_bound_violation': bounds, 'absolute_tolerance': tolerance,
            'optimality': 'not_run', 'infeasibility_proof': 'not_run'}, q if passed else None


def evaluate(request, *, clock=time.monotonic):
    """Validate one complete evidence unit. No writes, optimization, or native run."""
    exact_keys(request, ('schema_version', 'source', 'source_sha256', 'model', 'external_lp',
                         'protected_vertex_ids', 'coverage', 'quality', 'budgets', 'residual_tolerance'))
    need(request['schema_version'] == 'research-evidence/1.0', 'Unsupported evidence schema')
    exact_keys(request['budgets'], ('wall_seconds', 'operations'))
    budget = Budget(request['budgets']['wall_seconds'], request['budgets']['operations'], clock)
    report = {'schema_version': 'research-evidence-report/1.0', 'protocol_version': '0.2.0',
              'status': 'incomplete', 'production': 'not_run', 'native': 'not_run',
              'exact_cc_junction_solver': 'not_implemented',
              'self_intersection': {'status': 'not_run', 'reason': 'No intersection implementation in this unit; shared-vertex pairs are not exempted'},
              'geometry': {'status': 'not_run'}, 'external_lp': {'status': 'not_run'},
              'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        budget.check()
        raw = json.dumps(request, allow_nan=False, separators=(',', ':')).encode()
        need(len(raw) <= MAX_BYTES, 'Request byte limit exceeded')
        source_sha = canonical_sha(request['source'])
        need(request['source_sha256'] == source_sha, 'Source identity mismatch')
        report['source_sha256'] = source_sha
        report['request_sha256'] = canonical_sha(request)
        vertices, faces, ids = parse_source(request['source'], budget)
        model = request['model']
        parse_model(model, len(vertices), budget)
        quality_thresholds(request['quality'])
        coverage_specification(request['coverage'])
        protected = request['protected_vertex_ids']
        need(isinstance(protected, list) and all(isinstance(i, str) for i in protected), 'Invalid protected identities')
        need(len(set(protected)) == len(protected) and set(protected) <= set(ids), 'Unknown or duplicate protected identities')
        tolerance = number(request['residual_tolerance'])
        need(0 <= tolerance <= 0.001, 'Invalid residual tolerance')
        report['external_lp'], q = verify_external(request['external_lp'], model, source_sha, tolerance, budget)
        if q is not None:
            candidate = []
            for i, point in enumerate(vertices):
                budget.check(3*len(q))
                candidate.append([number(point[a]+dot(model['basis'][3*i+a], q)) for a in range(3)])
            protected_set = set(protected)
            unchanged = all(p == c for p, c, identity in zip(vertices, candidate, ids) if identity in protected_set)
            report['geometry'] = {'status': 'evaluated', 'candidate_sha256': canonical_sha({'vertices': candidate, 'faces': faces, 'vertex_ids': ids}),
                                  'topology': topology(faces, len(vertices), budget),
                                  'quality': quality(candidate, faces, request['quality'], budget),
                                  'coverage': coverage(candidate, request['coverage'], budget),
                                  'protected_identity': {'status': ('pass' if unchanged else 'fail') if protected else 'not_run', 'count': len(protected), 'comparison': 'exact coordinate and retained vertex identity'}}
            gates = [report['geometry'][key]['status'] for key in ('topology', 'quality', 'coverage', 'protected_identity')]
            report['status'] = 'failed' if 'fail' in gates else 'incomplete'
        elif report['external_lp']['status'] == 'fail':
            report['status'] = 'failed'
        budget.check()
    except TimeoutError as error:
        report['status'] = 'timed_out'
        report['error'] = str(error)
    except (ValueError, TypeError, OverflowError, KeyError) as error:
        report['status'] = 'failed'
        report['error'] = str(error)
    report['budget'] = budget.receipt()
    return report


def read_and_evaluate(path):
    """Use the existing regular-file, no-symlink, bounded strict JSON reader."""
    from .io import read_json
    return evaluate(read_json(Path(path).absolute(), max_bytes=MAX_BYTES))
