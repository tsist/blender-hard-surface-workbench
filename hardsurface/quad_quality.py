"""Bounded, pure-Python acceptance gate for actual polygon topology.

Coordinates are metres. This module neither imports Blender nor tessellates,
repairs, saves or mutates geometry. Run it independently on control and evaluated
polygons; Blender loop triangles are NOT faces. Provenance must survive the
bridge, not be reconstructed by guessing after a topology-changing modifier.

Defaults are developer technical qualification thresholds, not approved design
tolerances. Passing proves the checks enumerated in the report, not visual
quality, dimensional compliance, outward winding or absence of intersections
between distinct faces. Every triangle requires a face-specific reason record;
the default triangle budget is zero and percentages never grant a waiver.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from .io import RuntimeFailure


LIMITS = {'vertices': 200000, 'faces': 400000, 'corners': 1200000,
          'face_corners': 8192, 'coordinate_m': 1000000.0}
DEFAULT_POLICY = {
    'require_closed': True,
    'min_edge_m': 1e-6,
    'min_face_area_m2': 1e-12,
    'min_edge_ratio': 1e-5,
    'min_corner_angle_degrees': 5.0,
    'max_quad_corner_angle_degrees': 175.0,
    'max_aspect_ratio': 50.0,
    'max_support_band_aspect_ratio': 100.0,
    'max_quad_warpage_degrees': 5.0,
    'max_quad_plane_distance_m': 1e-7,
    'max_quad_plane_distance_relative': 1e-3,
    'max_triangles': 0,
    'max_connected_triangle_faces': 4,
    'max_triangle_faces_at_vertex': 4,
    'max_finding_examples': 64,
}
# Quality overrides may only tighten the engineering defaults. The separate
# explicit open-surface and justified-triangle options never waive other gates.
_BOUNDS = {
    'min_edge_m': (1e-6, 1.0),
    'min_face_area_m2': (1e-12, 1.0),
    'min_edge_ratio': (1e-5, 0.1),
    'min_corner_angle_degrees': (5.0, 60.0),
    'max_quad_corner_angle_degrees': (120.0, 175.0),
    'max_aspect_ratio': (1.0, 50.0),
    'max_support_band_aspect_ratio': (1.0, 100.0),
    'max_quad_warpage_degrees': (0.0, 5.0),
    'max_quad_plane_distance_m': (0.0, 1e-7),
    'max_quad_plane_distance_relative': (0.0, 1e-3),
    'max_triangles': (0, 32),
    'max_connected_triangle_faces': (1, 4),
    'max_triangle_faces_at_vertex': (1, 4),
    'max_finding_examples': (1, 4096),
}
TRIANGLE_REASON_CODES = frozenset({
    'corner_transition', 'odd_loop_termination', 'constrained_patch_transition',
})
_BANNED_CONSTRUCTIONS = frozenset({
    'triangulate', 'triangulation', 'ngon_triangulation', 'triangle_fan',
    'fan_triangulation', 'generic_triangulation',
})


def quality_policy(overrides=None):
    """Return an independent, validated policy; unknown/disablement keys fail."""
    if overrides is not None and not isinstance(overrides, Mapping):
        raise RuntimeFailure('QUAD_QUALITY_POLICY', 'Policy must be a mapping')
    result = dict(DEFAULT_POLICY)
    for name, value in (overrides or {}).items():
        if name not in result:
            raise RuntimeFailure('QUAD_QUALITY_POLICY', 'Unknown policy field', field=name)
        if name == 'require_closed':
            valid = type(value) is bool
        else:
            lo, hi = _BOUNDS[name]
            integer = isinstance(DEFAULT_POLICY[name], int)
            valid = (type(value) is int if integer else type(value) in (int, float))
            valid = valid and lo <= value <= hi and math.isfinite(value)
        if not valid:
            raise RuntimeFailure('QUAD_QUALITY_POLICY', 'Policy value outside supported bounds', field=name)
        result[name] = value
    if result['min_corner_angle_degrees'] >= result['max_quad_corner_angle_degrees']:
        raise RuntimeFailure('QUAD_QUALITY_POLICY', 'Corner-angle interval is empty')
    if result['max_support_band_aspect_ratio'] < result['max_aspect_ratio']:
        raise RuntimeFailure('QUAD_QUALITY_POLICY', 'Support-band aspect ceiling cannot be smaller than ordinary ceiling')
    return result


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _dot(a, b):
    return math.fsum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.hypot(*a)


def _angle(a, b):
    if _length(a) == 0.0 or _length(b) == 0.0:
        return None
    # atan2 is stable at nearly parallel/antiparallel directions.
    return math.degrees(math.atan2(_length(_cross(a, b)), _dot(a, b)))


def _canonical(face):
    # Only 3/4-corner faces reach this helper.
    forward, backward = tuple(face), tuple(reversed(face))
    return min(row[i:] + row[:i] for row in (forward, backward) for i in range(len(row)))


def _orient2(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersect_segments(a, b, c, d):
    # Roundoff-only classification; no design-tolerance expansion.
    scale = max(abs(x) for p in (a, b, c, d) for x in p)
    length = max(math.dist(a, b), math.dist(c, d))
    eps = 64 * math.ulp(scale if scale else 1.0) * length
    values = (_orient2(a, b, c), _orient2(a, b, d),
              _orient2(c, d, a), _orient2(c, d, b))
    signs = [1 if x > eps else -1 if x < -eps else 0 for x in values]
    if signs[0] * signs[1] < 0 and signs[2] * signs[3] < 0:
        return True
    def on(p, u, v):
        return all(min(u[i], v[i]) <= p[i] <= max(u[i], v[i]) for i in range(2))
    return ((signs[0] == 0 and on(c, a, b)) or (signs[1] == 0 and on(d, a, b)) or
            (signs[2] == 0 and on(a, c, d)) or (signs[3] == 0 and on(b, c, d)))


def _face_metrics(points):
    """Diagonal calculations measure a quad; they never replace its polygon."""
    n = len(points)
    # Work near the origin so projection and Newell normal are translation-stable.
    p = [_sub(v, points[0]) for v in points]
    edge_lengths = [_length(_sub(p[(i + 1) % n], p[i])) for i in range(n)]
    normals = [_cross(p[i], p[(i + 1) % n]) for i in range(n)]
    normal = tuple(math.fsum(v[i] for v in normals) for i in range(3))
    projected_area = _length(normal) / 2
    corners = [_angle(_sub(p[i - 1], p[i]), _sub(p[(i + 1) % n], p[i])) for i in range(n)]
    diagonal = max(_length(_sub(a, b)) for a in p for b in p)
    longest, shortest = max(edge_lengths), min(edge_lengths)
    result = {'edge_lengths_m': edge_lengths, 'minimum_edge_m': shortest,
              'maximum_edge_m': longest, 'minimum_edge_ratio': shortest / longest if longest else 0.0,
              'area_m2': projected_area, 'corner_angles_degrees': corners,
              'minimum_corner_angle_degrees': min((x for x in corners if x is not None), default=None),
              'maximum_corner_angle_degrees': max((x for x in corners if x is not None), default=None),
              'aspect_ratio': None, 'quad_warpage_degrees': None, 'quad_plane_distance_m': None,
              'face_diagonal_m': diagonal, 'self_crossing': False, 'concave': False}
    if n == 4:
        triangle_normals = [_cross(_sub(p[b], p[a]), _sub(p[c], p[a]))
                            for a, b, c in ((0, 1, 2), (0, 2, 3), (0, 1, 3), (1, 2, 3))]
        # Max disagreement across BOTH diagonal choices, not the most convenient one.
        warps = [_angle(triangle_normals[0], triangle_normals[1]),
                 _angle(triangle_normals[2], triangle_normals[3])]
        result['quad_warpage_degrees'] = max((x for x in warps if x is not None), default=None)
        result['mean_diagonal_area_m2'] = math.fsum(_length(v) for v in triangle_normals) / 4
        strongest = max(triangle_normals, key=_length)
        if _length(strongest):
            drop = max(range(3), key=lambda i: abs(strongest[i]))
            xy = [tuple(q[i] for i in range(3) if i != drop) for q in p]
            result['self_crossing'] = (_intersect_segments(xy[0], xy[1], xy[2], xy[3]) or
                                       _intersect_segments(xy[1], xy[2], xy[3], xy[0]))
            turns = [_orient2(xy[i - 1], xy[i], xy[(i + 1) % 4]) for i in range(4)]
            result['concave'] = min(turns) < 0 < max(turns) and not result['self_crossing']
        if _length(normal):
            center = tuple(math.fsum(q[i] for q in p) / n for i in range(3))
            result['quad_plane_distance_m'] = max(abs(_dot(_sub(q, center), normal)) / _length(normal) for q in p)
    if shortest > 0 and projected_area > 0:
        area_denominator = projected_area * (2 if n == 3 else 1)
        result['aspect_ratio'] = max(longest / shortest, longest * longest / area_denominator)
    return result


def _sequence(value):
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _label(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def validate_mesh(vertices, faces, *, face_provenance, triangle_reasons=None,
                  policy=None, identity=None, mesh_state='control',
                  include_face_metrics=False, finding_sink=None):
    """Inspect real vertex/face arrays without mutation and return JSON-safe evidence.

    face_provenance is aligned one-to-one with faces. Every entry requires
    feature_id and surface_id strings; optional support_band/curved are bools.
    Optional source_face_corners > 4 or triangulation construction is forbidden.

    A triangle reason is exactly {face_index, feature_id, surface_id, reason_code,
    explanation, quad_alternative_rejected, approval_id}. The last three are
    nonempty strings. approval_id identifies an independently verified approval;
    this validator verifies coverage/identity, not the authority of that record.
    Extra, stale, duplicated or non-triangle records fail. Optional finding_sink
    receives EVERY located finding, even after compact examples are exhausted.
    The caller owns any file-writing callback. include_face_metrics adds complete
    per-face metrics and may produce a large report; it is off by default.
    """
    p = quality_policy(policy)
    if mesh_state not in ('control', 'evaluated'):
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'mesh_state must be control or evaluated')
    if type(include_face_metrics) is not bool or (finding_sink is not None and not callable(finding_sink)):
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Invalid reporting options')
    if not _sequence(vertices) or not _sequence(faces):
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Vertices and faces must be bounded sequences')
    if len(vertices) > LIMITS['vertices'] or len(faces) > LIMITS['faces']:
        raise RuntimeFailure('QUAD_QUALITY_LIMIT', 'Mesh exceeds vertex or face budget')
    if not _sequence(face_provenance) or len(face_provenance) != len(faces):
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Provenance must have one entry per actual polygon')
    if triangle_reasons is None:
        triangle_reasons = []
    if not _sequence(triangle_reasons) or len(triangle_reasons) > 1024:
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Triangle reasons must be a bounded sequence')
    identity = {} if identity is None else identity
    if not isinstance(identity, Mapping) or any(not _label(k) or not _label(v) for k, v in identity.items()):
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Identity must map bounded names to bounded strings')
    if len(identity) > 8:
        raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Identity has too many fields')
    total_corners = 0
    for i, face in enumerate(faces):
        if not _sequence(face):
            raise RuntimeFailure('QUAD_QUALITY_INPUT', 'Each face must be an index sequence', face_index=i)
        total_corners += len(face)
        if len(face) > LIMITS['face_corners'] or total_corners > LIMITS['corners']:
            raise RuntimeFailure('QUAD_QUALITY_LIMIT', 'Mesh exceeds corner budget', face_index=i)

    examples, finding_counts, all_metrics = [], Counter(), []
    who = dict(identity)

    def finding(code, *, face_index=None, face_indices=None, vertex_indices=None, **details):
        row = {**who, 'code': code, 'mesh_state': mesh_state}
        if face_index is not None:
            row['face_index'] = face_index
            provenance = face_provenance[face_index]
            if isinstance(provenance, Mapping):
                for key in ('feature_id', 'surface_id'):
                    if _label(provenance.get(key)):
                        row[key] = provenance[key]
        if face_indices is not None:
            row['face_indices'] = list(face_indices)
        if vertex_indices is not None:
            row['vertex_indices'] = list(vertex_indices)
        row.update(details)
        finding_counts[code] += 1
        if len(examples) < p['max_finding_examples']:
            examples.append(row)
        if finding_sink is not None:
            finding_sink(row)

    if not vertices or not faces:
        finding('EMPTY_MESH', vertices=len(vertices), faces=len(faces))
    points, coordinate_first = [], {}
    for i, vertex in enumerate(vertices):
        valid = (_sequence(vertex) and len(vertex) == 3 and
                 all(type(x) in (int, float) and abs(x) <= LIMITS['coordinate_m'] and math.isfinite(x) for x in vertex))
        if not valid:
            points.append(None)
            finding('INVALID_VERTEX_COORDINATE', vertex_indices=[i], coordinate_bound_m=LIMITS['coordinate_m'])
            continue
        point = tuple(float(x) for x in vertex)
        points.append(point)
        if point in coordinate_first:
            finding('DUPLICATE_VERTEX_COORDINATE', vertex_indices=[coordinate_first[point], i])
        else:
            coordinate_first[point] = i

    edge_faces, vertex_faces, links = defaultdict(list), defaultdict(set), defaultdict(list)
    canonical_faces, triangle_ids, valid_face_ids = {}, set(), set()
    counts = Counter({'vertices': len(vertices), 'faces': len(faces), 'corners': total_corners,
                      'quads': 0, 'triangles': 0, 'ngons': 0, 'invalid_faces': 0})
    extrema = {'minimum_edge_m': None, 'minimum_face_area_m2': None,
               'minimum_corner_angle_degrees': None, 'maximum_corner_angle_degrees': None,
               'maximum_aspect_ratio': None, 'maximum_quad_warpage_degrees': None,
               'maximum_quad_plane_distance_m': None}
    extrema_locations = {}

    for i, face in enumerate(faces):
        n, provenance = len(face), face_provenance[i]
        if n == 3:
            counts['triangles'] += 1
            triangle_ids.add(i)
        elif n == 4:
            counts['quads'] += 1
        elif n > 4:
            counts['ngons'] += 1
            finding('NGON_FORBIDDEN', face_index=i, corners=n)
        else:
            counts['invalid_faces'] += 1
            finding('INVALID_FACE_SIZE', face_index=i, corners=n)
        good_provenance = isinstance(provenance, Mapping) and all(_label(provenance.get(k)) for k in ('feature_id', 'surface_id'))
        if not good_provenance:
            finding('MISSING_FACE_PROVENANCE', face_index=i)
            provenance = {}
        else:
            for flag in ('support_band', 'curved'):
                if flag in provenance and type(provenance[flag]) is not bool:
                    finding('INVALID_FACE_PROVENANCE', face_index=i, field=flag)
            construction = provenance.get('construction', '')
            source_corners = provenance.get('source_face_corners', n)
            if (not isinstance(construction, str) or len(construction) > 128 or
                    type(source_corners) is not int or source_corners < 3):
                finding('INVALID_FACE_PROVENANCE', face_index=i, field='construction_or_source_face_corners')
            elif construction.lower() in _BANNED_CONSTRUCTIONS or source_corners > 4:
                finding('TRIANGULATION_SUBSTITUTE_FORBIDDEN', face_index=i,
                        construction=construction, source_face_corners=source_corners)
        indices_valid = all(type(v) is int and 0 <= v < len(points) for v in face)
        if not indices_valid:
            finding('INVALID_VERTEX_INDEX', face_index=i)
            continue
        if len(set(face)) != n:
            finding('REPEATED_FACE_VERTEX', face_index=i, vertex_indices=face[:16])
            continue
        if n < 3:
            continue
        # Topology uses original boundaries, including a forbidden n-gon's edges.
        for j, v in enumerate(face):
            nxt = face[(j + 1) % n]
            edge_faces[tuple(sorted((v, nxt)))].append((i, v, nxt))
            vertex_faces[v].add(i)
            links[v].append((face[j - 1], nxt))
        if n > 4 or any(points[v] is None for v in face):
            continue
        key = _canonical(face)
        if key in canonical_faces:
            finding('DUPLICATE_FACE', face_index=i, first_face_index=canonical_faces[key])
        else:
            canonical_faces[key] = i
        valid_face_ids.add(i)
        metric = _face_metrics([points[v] for v in face])
        if include_face_metrics:
            all_metrics.append({'face_index': i, 'feature_id': provenance.get('feature_id'),
                                'surface_id': provenance.get('surface_id'), **metric})
        # A missing provenance is already a failure; metrics still remain useful.
        for name, field, is_min in (
            ('minimum_edge_m', 'minimum_edge_m', True),
            ('minimum_face_area_m2', 'area_m2', True),
            ('minimum_corner_angle_degrees', 'minimum_corner_angle_degrees', True),
            ('maximum_corner_angle_degrees', 'maximum_corner_angle_degrees', False),
            ('maximum_aspect_ratio', 'aspect_ratio', False),
            ('maximum_quad_warpage_degrees', 'quad_warpage_degrees', False),
            ('maximum_quad_plane_distance_m', 'quad_plane_distance_m', False),
        ):
            if metric[field] is not None:
                old = extrema[name]
                if old is None or (metric[field] < old if is_min else metric[field] > old):
                    extrema[name] = metric[field]
                    extrema_locations[name] = {'face_index': i, 'feature_id': provenance.get('feature_id'),
                                               'surface_id': provenance.get('surface_id')}
        for j, length in enumerate(metric['edge_lengths_m']):
            if length < p['min_edge_m']:
                finding('EDGE_TOO_SHORT', face_index=i, vertex_indices=[face[j], face[(j + 1) % n]],
                        measured_m=length, minimum_m=p['min_edge_m'])
        if metric['minimum_edge_ratio'] < p['min_edge_ratio']:
            finding('AVOIDABLE_TINY_EDGE', face_index=i, measured=metric['minimum_edge_ratio'], minimum=p['min_edge_ratio'])
        if metric['area_m2'] == 0:
            finding('ZERO_AREA_FACE', face_index=i)
        elif metric['area_m2'] < p['min_face_area_m2']:
            finding('FACE_AREA_TOO_SMALL', face_index=i, measured_m2=metric['area_m2'], minimum_m2=p['min_face_area_m2'])
        if metric['minimum_corner_angle_degrees'] is None or metric['minimum_corner_angle_degrees'] < p['min_corner_angle_degrees']:
            finding('CORNER_ANGLE_TOO_SMALL', face_index=i, measured_degrees=metric['minimum_corner_angle_degrees'],
                    minimum_degrees=p['min_corner_angle_degrees'])
        aspect_limit = p['max_support_band_aspect_ratio'] if provenance.get('support_band') is True else p['max_aspect_ratio']
        if metric['aspect_ratio'] is not None and metric['aspect_ratio'] > aspect_limit:
            finding('ASPECT_RATIO_EXCEEDED', face_index=i, measured=metric['aspect_ratio'], maximum=aspect_limit,
                    support_band=provenance.get('support_band') is True)
        if n == 4:
            if metric['self_crossing']:
                finding('SELF_CROSSING_QUAD', face_index=i)
            if metric['concave']:
                finding('CONCAVE_QUAD', face_index=i)
            if metric['maximum_corner_angle_degrees'] is not None and metric['maximum_corner_angle_degrees'] > p['max_quad_corner_angle_degrees']:
                finding('QUAD_CORNER_ANGLE_TOO_LARGE', face_index=i, measured_degrees=metric['maximum_corner_angle_degrees'],
                        maximum_degrees=p['max_quad_corner_angle_degrees'])
            if metric['quad_warpage_degrees'] is not None and metric['quad_warpage_degrees'] > p['max_quad_warpage_degrees']:
                finding('QUAD_WARPAGE_EXCEEDED', face_index=i, measured_degrees=metric['quad_warpage_degrees'],
                        maximum_degrees=p['max_quad_warpage_degrees'])
            plane_limit = max(p['max_quad_plane_distance_m'], p['max_quad_plane_distance_relative'] * metric['face_diagonal_m'])
            if metric['quad_plane_distance_m'] is not None and metric['quad_plane_distance_m'] > plane_limit:
                finding('QUAD_NONPLANAR', face_index=i, measured_m=metric['quad_plane_distance_m'], maximum_m=plane_limit)

    boundary_count = nonmanifold_edge_count = 0
    for edge, incident in edge_faces.items():
        ids = [row[0] for row in incident]
        if len(incident) == 1:
            boundary_count += 1
            if p['require_closed']:
                finding('BOUNDARY_EDGE', face_index=ids[0], vertex_indices=edge)
        elif len(incident) > 2:
            nonmanifold_edge_count += 1
            finding('NONMANIFOLD_EDGE', face_index=ids[0], face_indices=ids, vertex_indices=edge)
        elif incident[0][1:] == incident[1][1:]:
            finding('INCONSISTENT_EDGE_ORIENTATION', face_index=ids[0], face_indices=ids, vertex_indices=edge)
    for v in range(len(vertices)):
        if not vertex_faces[v]:
            finding('ISOLATED_VERTEX', vertex_indices=[v])
            continue
        graph, degree = defaultdict(set), Counter()
        for a, b in links[v]:
            graph[a].add(b)
            graph[b].add(a)
            degree[a] += 1
            degree[b] += 1
        seen, pending = set(), [next(iter(graph))]
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(graph[node] - seen)
        degree_counts = Counter(degree.values())
        valid_link = (len(seen) == len(graph) and set(degree) and
                      (set(degree.values()) == {2} or
                       (not p['require_closed'] and degree_counts[1] == 2 and set(degree.values()) <= {1, 2})))
        if not valid_link:
            finding('NONMANIFOLD_VERTEX', face_index=min(vertex_faces[v]), vertex_indices=[v],
                    incident_face_indices=sorted(vertex_faces[v]), link_components_connected=len(seen) == len(graph),
                    link_degree_counts={str(k): val for k, val in sorted(degree_counts.items())})

    reason_fields = {'face_index', 'feature_id', 'surface_id', 'reason_code', 'explanation',
                     'quad_alternative_rejected', 'approval_id'}
    face_graph={i:set() for i in valid_face_ids}
    for incident in edge_faces.values():
        ids=[row[0] for row in incident if row[0] in valid_face_ids]
        if ids:
            for other in ids[1:]:face_graph[ids[0]].add(other);face_graph[other].add(ids[0])
    remaining=set(valid_face_ids);face_components=0
    while remaining:
        face_components+=1;pending=[next(iter(remaining))];component=set()
        while pending:
            current=pending.pop()
            if current in component:continue
            component.add(current);pending.extend(face_graph[current]-component)
        remaining-=component
    if p['require_closed'] and face_components!=1:
        finding('DISCONNECTED_COMPONENT_SHELLS',measured=face_components,maximum=1)
    covered, seen_reasons = set(), set()
    for record_index, reason in enumerate(triangle_reasons):
        if not isinstance(reason, Mapping) or set(reason) != reason_fields:
            finding('INVALID_TRIANGLE_REASON', reason_record_index=record_index, reason='record_fields')
            continue
        fi = reason['face_index']
        if type(fi) is not int or fi not in triangle_ids:
            finding('STALE_TRIANGLE_REASON', reason_record_index=record_index)
            continue
        if fi in seen_reasons:
            finding('DUPLICATE_TRIANGLE_REASON', face_index=fi, reason_record_index=record_index)
            continue
        seen_reasons.add(fi)
        provenance = face_provenance[fi]
        valid = (isinstance(provenance, Mapping) and fi in valid_face_ids and
                 reason['feature_id'] == provenance.get('feature_id') and
                 reason['surface_id'] == provenance.get('surface_id') and
                 isinstance(reason['reason_code'], str) and
                 reason['reason_code'] in TRIANGLE_REASON_CODES)
        valid = valid and all(isinstance(reason[k], str) and 8 <= len(reason[k].strip()) <= 2048
                              for k in ('explanation', 'quad_alternative_rejected')) and _label(reason['approval_id'])
        if not valid:
            finding('INVALID_TRIANGLE_REASON', face_index=fi, reason_record_index=record_index, reason='identity_or_justification')
        else:
            covered.add(fi)
    for fi in sorted(triangle_ids - covered):
        finding('UNJUSTIFIED_TRIANGLE', face_index=fi)
    if len(triangle_ids) > p['max_triangles']:
        finding('TRIANGLE_BUDGET_EXCEEDED', face_index=min(triangle_ids), face_indices=sorted(triangle_ids),
                measured=len(triangle_ids), maximum=p['max_triangles'])
    # Connected triangle patches may not disguise a giant face or triangle fan.
    triangle_graph = {i: set() for i in triangle_ids}
    triangles_bordering_quads = set()
    for incident in edge_faces.values():
        ids = [row[0] for row in incident if row[0] in triangle_ids]
        if any(row[0] in valid_face_ids and len(faces[row[0]]) == 4 for row in incident):
            triangles_bordering_quads.update(ids)
        # A star preserves component membership without a quadratic clique on
        # a malicious/nonmanifold edge with many incident triangles.
        if ids:
            for fi in ids[1:]:
                triangle_graph[ids[0]].add(fi)
                triangle_graph[fi].add(ids[0])
    remaining, largest_patch = set(triangle_ids), 0
    while remaining:
        root, patch = min(remaining), set()
        pending = [root]
        while pending:
            fi = pending.pop()
            if fi not in patch:
                patch.add(fi)
                pending.extend(triangle_graph[fi] - patch)
        remaining -= patch
        largest_patch = max(largest_patch, len(patch))
        if not patch & triangles_bordering_quads:
            finding('TRIANGLE_PATCH_WITHOUT_QUAD_NEIGHBOR', face_index=root, face_indices=sorted(patch))
        if len(patch) > p['max_connected_triangle_faces']:
            finding('TRIANGLE_PATCH_TOO_LARGE', face_index=root, face_indices=sorted(patch),
                    measured=len(patch), maximum=p['max_connected_triangle_faces'])
    for v, ids in vertex_faces.items():
        tris = sorted(ids & triangle_ids)
        if len(tris) > p['max_triangle_faces_at_vertex']:
            finding('TRIANGLE_FAN_FORBIDDEN', face_index=tris[0], face_indices=tris, vertex_indices=[v],
                    measured=len(tris), maximum=p['max_triangle_faces_at_vertex'])

    total_findings = sum(finding_counts.values())
    report = {
        'schema_version': '1.0', 'gate': 'quad_first_actual_polygons', 'passed': not total_findings,
        'mesh_state': mesh_state, 'identity': who, 'coordinate_units': 'm',
        'policy': p, 'policy_status': 'developer technical qualification values; not user-approved design tolerances',
        'counts': {**counts, 'edges': len(edge_faces), 'boundary_edges': boundary_count,
                   'connected_face_components':face_components,
                   'nonmanifold_edges': nonmanifold_edge_count, 'justified_triangles': len(covered),
                   'largest_connected_triangle_patch': largest_patch},
        'metrics': extrema, 'metric_locations': extrema_locations,
        'finding_counts': dict(sorted(finding_counts.items())), 'finding_count': total_findings,
        'findings': examples, 'findings_truncated': max(0, total_findings - len(examples)),
        'all_findings_sent_to_sink': finding_sink is not None,
        'semantics': {
            'topology_domain': 'actual polygons only; no loop triangles or implicit triangulation',
            'area': 'magnitude of translation-stable polygon area vector; quad diagonal mean also available per face',
            'aspect': 'max(longest edge / shortest edge, longest edge squared / polygon area); triangle area denominator is doubled',
            'warpage': 'maximum normal disagreement over both quad diagonals',
            'planarity': 'maximum distance from centroid plane with polygon-area normal; limit max(absolute, relative * diagonal)',
            'triangle_authority': 'reason coverage and identities checked; caller must independently verify approval_id authority',
            'not_checked': ['intersections between distinct faces', 'outward orientation', 'design dimensions', 'visual quality'],
        },
    }
    if include_face_metrics:
        report['face_metrics'] = all_metrics
    return report


def assert_mesh_quality(*args, **kwargs):
    """Use at candidate acceptance; preserve the full compact report on failure."""
    report = validate_mesh(*args, **kwargs)
    if not report['passed']:
        raise RuntimeFailure('QUAD_QUALITY_FAILED', 'Actual polygon topology failed quad-first quality gate', report=report)
    return report
