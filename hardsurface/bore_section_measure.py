"""Finite HOST measurements of an explicitly selected actual bore wall.

This module performs no Blender evaluation, source authentication, reconstruction
from nominal coordinates, or native qualification. The caller must authenticate
the selected face IDs and supply the actual evaluated mesh plus its own binding
token. A token is evidence carried by the caller, never authentication here.
"""
from collections import defaultdict
import hashlib
import json
import math

from .io import RuntimeFailure


METHOD = 'semantic_bore_plane_sections_polar_rays'
VERSION = 1
MAX_VERTICES = 300000
MAX_FACES = 250000
MAX_STATIONS = 128
MAX_TRIANGLE_STATIONS = 4000000
MAX_SECTION_SEGMENTS = 16384
MAX_INTERSECTION_PAIRS = 2000000


def _fail(code, message, **details):
    raise RuntimeFailure('BORE_SECTION_' + code, message, **details)


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _canonical_cycle(sequence):
    sequence = tuple(sequence)
    return min(sequence[i:] + sequence[:i] for i in range(len(sequence)))


def _validate_topology(vertices, faces, selected):
    edges = defaultdict(list)
    incident = defaultdict(set)
    neighbors = {fi: set() for fi in selected}
    face_keys = set()
    for fi in selected:
        face = faces[fi]
        key = tuple(sorted(face))
        if key in face_keys:
            _fail('TOPOLOGY', 'Duplicate selected face', face_index=fi)
        face_keys.add(key)
        for vi in face:
            incident[vi].add(fi)
        for a, b in zip(face, face[1:] + face[:1]):
            edges[tuple(sorted((a, b)))].append((fi, a, b))
    boundary = []
    for edge, users in edges.items():
        if len(users) > 2:
            _fail('TOPOLOGY', 'Nonmanifold selected edge', edge=list(edge))
        if len(users) == 2:
            first, second = users
            if first[1:] != (second[2], second[1]):
                _fail('WINDING', 'Inconsistent selected face winding', edge=list(edge))
            neighbors[first[0]].add(second[0])
            neighbors[second[0]].add(first[0])
        else:
            boundary.append(users[0][1:])
    reached = {selected[0]}
    pending = list(reached)
    while pending:
        for fi in neighbors[pending.pop()] - reached:
            reached.add(fi)
            pending.append(fi)
    if len(reached) != len(selected):
        _fail('TOPOLOGY', 'Selected wall must be one edge-connected region')
    outgoing, incoming = {}, {}
    for a, b in boundary:
        if a in outgoing or b in incoming:
            _fail('TOPOLOGY', 'Selected boundary branches or touches itself')
        outgoing[a], incoming[b] = b, a
    if not outgoing or set(outgoing) != set(incoming):
        _fail('TOPOLOGY', 'Selected wall requires closed boundary cycles')
    cycles, remaining = [], set(outgoing)
    while remaining:
        start = min(remaining)
        current, cycle = start, []
        while current in remaining:
            remaining.remove(current)
            cycle.append(current)
            current = outgoing[current]
        if current != start or len(cycle) < 3:
            _fail('TOPOLOGY', 'Invalid selected boundary cycle')
        cycles.append(cycle)
    euler = len(incident) - len(edges) + len(selected)
    if len(cycles) != 2 or euler != 0:
        _fail('TOPOLOGY', 'Selected wall must be an annulus with two boundary cycles',
              boundary_cycle_count=len(cycles), euler_characteristic=euler)
    # Check actual vertex links, including the one-face boundary corner case.
    for vi, users in incident.items():
        links = {fi: set() for fi in users}
        for fi in users:
            face = faces[fi]
            j = face.index(vi)
            for vj in (face[j - 1], face[(j + 1) % len(face)]):
                for fj, _, _ in edges[tuple(sorted((vi, vj)))]:
                    if fj != fi:
                        links[fi].add(fj)
        seen = {next(iter(users))}
        todo = list(seen)
        while todo:
            for fj in links[todo.pop()] - seen:
                seen.add(fj)
                todo.append(fj)
        degrees = sorted(len(value) for value in links.values())
        valid = (degrees == [0] or degrees == [1, 1] + [2] * (len(degrees) - 2)) if vi in outgoing else all(d == 2 for d in degrees)
        if seen != users or not valid:
            _fail('TOPOLOGY', 'Nonmanifold selected vertex link', vertex_index=vi)
    # Other actual faces cannot secretly add a third user or a reversed neighbor.
    actual_users = defaultdict(list)
    for fi, face in enumerate(faces):
        for a, b in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((a, b)))
            if edge in edges:
                actual_users[edge].append((fi, a, b))
    for edge, users in actual_users.items():
        if len(users) > 2 or (len(users) == 2 and users[0][1:] != (users[1][2], users[1][1])):
            _fail('TOPOLOGY', 'Actual mesh adjacency at selected wall is nonmanifold or inconsistently wound',
                  edge=list(edge), actual_face_count=len(users))
    return {'face_count': len(selected), 'vertex_count': len(incident),
            'edge_count': len(edges), 'boundary_cycle_count': 2,
            'euler_characteristic': euler,
            'parent_mesh_closure': 'not_required_or_proven_by_this_selected_wall_measurement'}, sorted(incident)


def _triangulations(faces, selected, policy):
    if policy == 'actual_triangles':
        if any(len(faces[fi]) != 3 for fi in selected):
            _fail('DOMAIN', 'actual_triangles requires selected actual triangles')
        return [('actual_triangles', [(fi, tuple(faces[fi])) for fi in selected])]
    if policy != 'both_quad_diagonals':
        _fail('DOMAIN', 'Explicit supported triangulation policy required')
    results = []
    for name, patterns in (('quad_diagonal_02', ((0, 1, 2), (0, 2, 3))),
                           ('quad_diagonal_13', ((0, 1, 3), (1, 2, 3)))):
        triangles = []
        for fi in selected:
            face = faces[fi]
            if len(face) == 3:
                triangles.append((fi, tuple(face)))
            else:
                triangles.extend((fi, tuple(face[i] for i in triangle)) for triangle in patterns)
        results.append((name, triangles))
    return results


def _validate_triangles(vertices, triangles, epsilon):
    minimum = 1.0
    for fi, indices in triangles:
        a, b, c = (vertices[vi] for vi in indices)
        u, v = _sub(b, a), _sub(c, a)
        normal = (u[1] * v[2] - u[2] * v[1],
                  u[2] * v[0] - u[0] * v[2],
                  u[0] * v[1] - u[1] * v[0])
        area = math.hypot(*normal)
        scale = max(math.dist(a, b), math.dist(b, c), math.dist(c, a))
        if not math.isfinite(area) or area <= epsilon * scale:
            _fail('GEOMETRY', 'Degenerate or numerically unresolved actual triangle', face_index=fi)
        radial = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
        radius = math.hypot(*radial)
        cosine = -(normal[0] * radial[0] + normal[1] * radial[1]) / (area * radius) if radius > epsilon else -1
        if not math.isfinite(cosine) or cosine <= 1e-10:
            _fail('WINDING', 'Every actual triangle must be an inward-facing radial wall',
                  face_index=fi, radial_normal_cosine=cosine)
        minimum = min(minimum, cosine)
    return minimum


def _section(vertices, triangles, z, epsilon):
    points, segments = {}, {}
    isolated = set()

    def point(key, xy):
        if key in points and math.dist(points[key], xy) > epsilon:
            _fail('NUMERIC', 'One topological section key has inconsistent coordinates')
        points[key] = xy
        return key

    def vertex(vi):
        return point(('v', vi), vertices[vi][:2])

    def edge(a, b):
        a, b = sorted((a, b))
        av, bv = vertices[a], vertices[b]
        fraction = (z - av[2]) / (bv[2] - av[2])
        xy = tuple(av[k] + fraction * (bv[k] - av[k]) for k in range(2))
        return point(('e', a, b), xy)

    for fi, triangle in triangles:
        distances = [vertices[vi][2] - z for vi in triangle]
        # A declared Z plane is never moved to a nearby vertex ring. Exact
        # floating input equality owns vertex/coplanar cases; an unresolved
        # tiny resulting segment fails below instead of silently snapping Z.
        signs = [0 if d == 0 else (1 if d > 0 else -1) for d in distances]
        zeros = [i for i, sign in enumerate(signs) if sign == 0]
        if len(zeros) == 3:
            _fail('COPLANAR', 'A selected triangle is coplanar with the requested section',
                  z_mm=z, face_index=fi)
        coplanar_side = None
        if len(zeros) == 2:
            hits = [vertex(triangle[i]) for i in zeros]
            coplanar_side = next(sign for sign in signs if sign)
        elif len(zeros) == 1:
            i = zeros[0]
            other = [j for j in range(3) if j != i]
            key = vertex(triangle[i])
            if signs[other[0]] == signs[other[1]]:
                isolated.add(key)
                continue
            hits = [key, edge(triangle[other[0]], triangle[other[1]])]
        else:
            hits = [edge(triangle[i], triangle[j]) for i, j in ((0, 1), (1, 2), (2, 0))
                    if signs[i] != signs[j]]
        if not hits:
            continue
        if len(hits) != 2 or hits[0] == hits[1] or math.dist(points[hits[0]], points[hits[1]]) <= epsilon:
            _fail('GEOMETRY', 'Collapsed or ambiguous section segment', z_mm=z, face_index=fi)
        key = tuple(sorted(hits))
        if key in segments:
            previous = segments[key]
            if coplanar_side is None or previous['coplanar_side'] is None or previous['merged']:
                _fail('TOPOLOGY', 'Duplicate section segment cannot be merged', z_mm=z)
            if coplanar_side == previous['coplanar_side']:
                _fail('COPLANAR', 'Plane touches a folded wall along a coplanar edge', z_mm=z)
            previous['merged'] = True
        else:
            segments[key] = {'coplanar_side': coplanar_side, 'merged': False}
    if not segments:
        _fail('COVERAGE', 'No actual section exists at the requested station', z_mm=z)
    if len(segments) > MAX_SECTION_SEGMENTS:
        _fail('BUDGET', 'Section segment budget exceeded', z_mm=z)
    graph = defaultdict(set)
    for a, b in segments:
        graph[a].add(b)
        graph[b].add(a)
    if isolated - set(graph):
        _fail('COPLANAR', 'Section contains isolated vertex contact', z_mm=z)
    if any(len(adjacent) != 2 for adjacent in graph.values()):
        _fail('COVERAGE', 'Actual section is open, branching, or nonmanifold', z_mm=z)
    start = min(graph)
    loop, visited, previous, current = [], set(), None, start
    while current not in visited:
        loop.append(current)
        visited.add(current)
        choices = sorted(graph[current] - ({previous} if previous is not None else set()))
        previous, current = current, choices[0]
    if current != start or len(loop) != len(graph):
        _fail('COVERAGE', 'Station must contain exactly one closed section', z_mm=z)
    _check_simple(loop, points, epsilon, z)
    deltas = []
    for a, b in zip(loop, loop[1:] + loop[:1]):
        av, bv = points[a], points[b]
        if math.hypot(*av) <= epsilon or math.hypot(*bv) <= epsilon:
            _fail('COVERAGE', 'Section reaches the declared axis', z_mm=z)
        deltas.append(math.atan2(_cross2(av, bv), av[0] * bv[0] + av[1] * bv[1]))
    winding = math.fsum(deltas) / math.tau
    if abs(abs(winding) - 1) > 1e-8:
        _fail('COVERAGE', 'Closed section must surround the declared axis once', z_mm=z, winding=winding)
    return loop, points, graph, winding


def _check_simple(loop, points, epsilon, z):
    # Broad-phase X intervals keep normal circular sections inexpensive. A
    # bounded exact candidate count prevents adversarial quadratic geometry.
    rows = []
    for index, (a, b) in enumerate(zip(loop, loop[1:] + loop[:1])):
        av, bv = points[a], points[b]
        rows.append((min(av[0], bv[0]), max(av[0], bv[0]),
                     min(av[1], bv[1]), max(av[1], bv[1]), index, a, b))
    active, comparisons = [], 0
    for row in sorted(rows):
        active = [other for other in active if other[1] >= row[0] - epsilon]
        for other in active:
            if row[2] > other[3] + epsilon or other[2] > row[3] + epsilon:
                continue
            comparisons += 1
            if comparisons > MAX_INTERSECTION_PAIRS:
                _fail('BUDGET', 'Section intersection comparison budget exceeded', z_mm=z)
            if abs(row[4] - other[4]) in (1, len(loop) - 1):
                # Adjacent edges may share only their common endpoint; a
                # reversal along an overlapping ray is still a folded section.
                common = set(row[5:]) & set(other[5:])
                center = points[next(iter(common))]
                a = points[next(key for key in row[5:] if key not in common)]
                b = points[next(key for key in other[5:] if key not in common)]
                u, v = _sub(a, center), _sub(b, center)
                if abs(_cross2(u, v)) <= epsilon * max(math.hypot(*u), math.hypot(*v)) and u[0] * v[0] + u[1] * v[1] > 0:
                    _fail('GEOMETRY', 'Adjacent section segments overlap', z_mm=z)
                continue
            a, b, c, d = (points[key] for key in (row[5], row[6], other[5], other[6]))
            u, v = _sub(b, a), _sub(d, c)
            scale = max(math.hypot(*u), math.hypot(*v))
            tol = epsilon * scale
            s1, s2 = _cross2(u, _sub(c, a)), _cross2(u, _sub(d, a))
            s3, s4 = _cross2(v, _sub(a, c)), _cross2(v, _sub(b, c))
            if ((s1 <= tol and s2 >= -tol) or (s2 <= tol and s1 >= -tol)) and ((s3 <= tol and s4 >= -tol) or (s4 <= tol and s3 >= -tol)):
                _fail('GEOMETRY', 'Nonadjacent section segments intersect or touch', z_mm=z)
        active.append(row)


def _ray(loop, points, graph, angle, epsilon, z):
    direction = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    hits, vertex_hits = [], set()
    for a, b in zip(loop, loop[1:] + loop[:1]):
        av, bv = points[a], points[b]
        pa, pb = _cross2(direction, av), _cross2(direction, bv)
        ra = direction[0] * av[0] + direction[1] * av[1]
        rb = direction[0] * bv[0] + direction[1] * bv[1]
        za, zb = abs(pa) <= epsilon, abs(pb) <= epsilon
        if za and zb:
            if max(ra, rb) >= -epsilon:
                _fail('RAY_AMBIGUOUS', 'Polar ray overlaps a section segment', z_mm=z, angle_degrees=angle)
            continue
        if za or zb:
            key, radius = (a, ra) if za else (b, rb)
            if abs(radius) <= epsilon:
                _fail('RAY_AMBIGUOUS', 'Section reaches the polar origin', z_mm=z, angle_degrees=angle)
            if radius > epsilon:
                vertex_hits.add(key)
            continue
        if (pa > 0) == (pb > 0):
            continue
        fraction = pa / (pa - pb)
        xy = tuple(av[k] + fraction * (bv[k] - av[k]) for k in range(2))
        radius = direction[0] * xy[0] + direction[1] * xy[1]
        if abs(radius) <= epsilon:
            _fail('RAY_AMBIGUOUS', 'Section crosses the polar origin', z_mm=z, angle_degrees=angle)
        if radius > epsilon:
            hits.append((radius, xy, ['segment', list(a), list(b)]))
    for key in sorted(vertex_hits):
        sides = [_cross2(direction, points[neighbor]) for neighbor in graph[key]]
        if abs(sides[0]) <= epsilon or abs(sides[1]) <= epsilon or (sides[0] > 0) == (sides[1] > 0):
            _fail('RAY_AMBIGUOUS', 'Polar ray touches a section vertex without one transverse crossing',
                  z_mm=z, angle_degrees=angle)
        xy = points[key]
        hits.append((direction[0] * xy[0] + direction[1] * xy[1], xy, ['vertex', list(key)]))
    if len(hits) != 1:
        _fail('RAY_COVERAGE', 'Every polar ray requires exactly one positive crossing',
              z_mm=z, angle_degrees=angle, positive_crossing_count=len(hits))
    return hits[0]


def measure_bore_sections(vertices_mm, faces, *, bore_face_indices,
                          membership_token, axis_center_xy_mm, stations_z_mm,
                          nominal_radius_mm, radial_tolerance_mm, angle_count=72,
                          start_angle_degrees=0.0,
                          triangulation_policy='both_quad_diagonals',
                          geometry_epsilon_mm=1e-9):
    """Measure actual plane sections at all caller-declared stations and 72 rays.

    Invalid/ambiguous geometry raises RuntimeFailure; valid measured geometry
    outside radial tolerance returns status='fail' with every actual sample.
    Both-quad policy checks two complete interpretations (all 02, all 13), not
    every combinatorial mixture, a bilinear quad, or Blender's unstated choice.
    For native evaluated triangulation use actual_triangles explicitly.
    """
    if not isinstance(vertices_mm, (list, tuple)) or not 0 < len(vertices_mm) <= MAX_VERTICES or not isinstance(faces, (list, tuple)) or not 0 < len(faces) <= MAX_FACES:
        _fail('BUDGET', 'Nonempty bounded mesh arrays required')
    if any(not isinstance(v, (list, tuple)) or len(v) != 3 or any(not _finite(x) for x in v) for v in vertices_mm):
        _fail('DOMAIN', 'Finite actual 3D vertices required')
    if any(not isinstance(f, (list, tuple)) or len(f) not in (3, 4) or any(type(i) is not int or not 0 <= i < len(vertices_mm) for i in f) or len(set(f)) != len(f) for f in faces):
        _fail('DOMAIN', 'Actual indexed triangles or quads without repeated vertices required')
    if not isinstance(bore_face_indices, (list, tuple)) or not bore_face_indices or any(type(i) is not int or not 0 <= i < len(faces) for i in bore_face_indices) or len(set(bore_face_indices)) != len(bore_face_indices):
        _fail('MEMBERSHIP', 'Explicit nonempty unique actual semantic face indices required')
    if not isinstance(membership_token, str) or not membership_token.strip() or len(membership_token) > 4096:
        _fail('MEMBERSHIP', 'Caller-supplied external source-binding token required')
    if not isinstance(axis_center_xy_mm, (list, tuple)) or len(axis_center_xy_mm) != 2 or any(not _finite(x) for x in axis_center_xy_mm):
        _fail('DOMAIN', 'Finite declared axis center XY required')
    if not isinstance(stations_z_mm, (list, tuple)) or not 0 < len(stations_z_mm) <= MAX_STATIONS or any(not _finite(z) for z in stations_z_mm) or len(set(stations_z_mm)) != len(stations_z_mm):
        _fail('DOMAIN', 'Explicit finite unique bounded Z stations required')
    if any(not _finite(value) or value <= 0 for value in (nominal_radius_mm, radial_tolerance_mm, geometry_epsilon_mm)):
        _fail('DOMAIN', 'Positive finite nominal radius, radial tolerance, and numerical epsilon required')
    if not _finite(2 * nominal_radius_mm):
        _fail('NUMERIC', 'Nominal diameter exceeds the finite arithmetic domain')
    if type(angle_count) is not int or angle_count != 72 or not _finite(start_angle_degrees):
        _fail('DOMAIN', 'This measurement profile requires 72 angles spaced by 5 degrees')
    if abs(start_angle_degrees) > 3600000:
        _fail('DOMAIN', 'Start angle exceeds the bounded numeric domain')
    selected = sorted(bore_face_indices)
    topology, wall_ids = _validate_topology(vertices_mm, faces, selected)
    # Centering avoids global-coordinate cancellation in area and winding tests.
    hx, hy = axis_center_xy_mm
    vertices = [(v[0] - hx, v[1] - hy, v[2]) for v in vertices_mm]
    extent = max(max(vertices[i][k] for i in wall_ids) - min(vertices[i][k] for i in wall_ids) for k in range(3))
    coordinate_magnitude = max(abs(x) for i in wall_ids for x in vertices_mm[i])
    coordinate_magnitude = max(coordinate_magnitude, abs(hx), abs(hy), *(abs(z) for z in stations_z_mm))
    epsilon = max(float(geometry_epsilon_mm), extent * 1e-12, math.ulp(coordinate_magnitude) * 32)
    if not math.isfinite(extent) or not math.isfinite(epsilon) or extent <= 0 or epsilon >= extent * 1e-5 or any(not all(math.isfinite(x) for x in vertices[i]) for i in wall_ids):
        _fail('NUMERIC', 'Coordinates or geometry epsilon exceed the resolvable numeric domain')
    if any(abs(a - b) <= 2 * epsilon for i, a in enumerate(stations_z_mm) for b in stations_z_mm[i + 1:]):
        _fail('NUMERIC', 'Distinct stations must be separated beyond numerical resolution')
    variants = _triangulations(faces, selected, triangulation_policy)
    if sum(len(triangles) for _, triangles in variants) * len(stations_z_mm) > MAX_TRIANGLE_STATIONS:
        _fail('BUDGET', 'Triangle/station work budget exceeded')
    angles = [(float(start_angle_degrees) + 5 * i) % 360 for i in range(72)]
    interpretations, worst = [], None
    failed_sample_count = 0
    for name, triangles in variants:
        minimum_cosine = _validate_triangles(vertices, triangles, epsilon)
        stations = []
        for z in stations_z_mm:
            loop, points, graph, winding = _section(vertices, triangles, z, epsilon)
            rays = []
            for i, angle in enumerate(angles):
                radius, xy, key = _ray(loop, points, graph, angle, epsilon, z)
                signed_error = radius - nominal_radius_mm
                error = abs(signed_error)
                sample = {'ray_index': i, 'angle_degrees': angle,
                          'positive_crossing_count': 1,
                          'radius_mm': radius, 'signed_radial_error_mm': signed_error,
                          'absolute_radial_error_mm': error,
                          'point_mm': [xy[0] + hx, xy[1] + hy, z],
                          'intersection_key': key,
                          'status': 'pass' if error <= radial_tolerance_mm else 'fail'}
                rays.append(sample)
                failed_sample_count += sample['status'] == 'fail'
                if worst is None or error > worst['absolute_radial_error_mm']:
                    worst = dict(sample, z_mm=z, interpretation=name)
            for i, sample in enumerate(rays):
                sample['opposite_ray_index'] = (i + 36) % 72
                sample['diameter_mm'] = sample['radius_mm'] + rays[(i + 36) % 72]['radius_mm']
                sample['signed_diameter_error_mm'] = sample['diameter_mm'] - 2 * nominal_radius_mm
                if not _finite(sample['diameter_mm']) or not _finite(sample['signed_diameter_error_mm']):
                    _fail('NUMERIC', 'Measured diameter exceeds the finite arithmetic domain')
            if worst['interpretation'] == name and worst['z_mm'] == z:
                worst.update(rays[worst['ray_index']])
            stations.append({'z_mm': z, 'status': 'pass' if all(r['status'] == 'pass' for r in rays) else 'fail',
                             'closed_section_count': 1, 'section_segment_count': len(loop),
                             'absolute_winding': abs(winding), 'ray_count': 72,
                             'radius_range_mm': [min(r['radius_mm'] for r in rays), max(r['radius_mm'] for r in rays)],
                             'diameter_range_mm': [min(r['diameter_mm'] for r in rays), max(r['diameter_mm'] for r in rays)],
                             'maximum_radial_error_mm': max(r['absolute_radial_error_mm'] for r in rays),
                             'rays': rays})
        interpretations.append({'name': name, 'triangle_count': len(triangles),
                                'minimum_radial_normal_cosine': minimum_cosine, 'stations': stations})
    canonical_geometry = sorted(_canonical_cycle(tuple(tuple(float(x) for x in vertices_mm[vi]) for vi in faces[fi])) for fi in selected)
    configuration = {'axis_center_xy_mm': list(axis_center_xy_mm), 'stations_z_mm': list(stations_z_mm),
                     'nominal_radius_mm': nominal_radius_mm, 'radial_tolerance_mm': radial_tolerance_mm,
                     'angle_count': 72, 'start_angle_degrees': float(start_angle_degrees) % 360,
                     'triangulation_policy': triangulation_policy, 'geometry_epsilon_mm': geometry_epsilon_mm}
    return {'method': METHOD, 'version': VERSION, 'status': 'pass' if failed_sample_count == 0 else 'fail',
            'qualification_status': 'not_established_by_host_measurement',
            'source_authentication': 'external_caller_required_not_verified_by_this_module',
            'native_evaluation_attestation': 'external_required_not_established',
            'input_domain': 'explicit semantic manifold annulus of inward actual triangles or both diagonal interpretations of real quads',
            'membership_selection': 'only_explicit_actual_face_indices_no_coordinate_classifier',
            'input_signatures': {'mesh_sha256': _sha([vertices_mm, faces]),
                                 'membership_sha256': _sha(selected),
                                 'membership_token_sha256': _sha(membership_token),
                                 'selected_geometry_sha256': _sha(canonical_geometry),
                                 'configuration_sha256': _sha(configuration)},
            'configuration': configuration, 'topology': topology,
            'numerical_policy': {'requested_geometry_epsilon_mm': geometry_epsilon_mm,
                                 'effective_geometry_epsilon_mm': epsilon,
                                 'independent_of_design_radial_tolerance': True,
                                 'station_plane_policy': 'exact declared Z; no near-ring snapping; unresolved small segments fail',
                                 'dimensional_comparison': 'absolute radial error <= radial_tolerance_mm; no added numerical allowance'},
            'triangulation_policy': triangulation_policy,
            'quad_interpretation_scope': 'all 02 and all 13 complete triangulations; no claim for arbitrary mixtures, bilinear patches, or an unstated native tessellation' if triangulation_policy == 'both_quad_diagonals' else 'caller-supplied actual triangles only',
            'sample_count': 72 * len(stations_z_mm) * len(variants),
            'declared_station_count': len(stations_z_mm), 'angles_per_station': 72,
            'angular_step_degrees': 5, 'radial_tolerance_mm': radial_tolerance_mm,
            'nominal_radius_mm': nominal_radius_mm, 'nominal_diameter_mm': 2 * nominal_radius_mm,
            'diameter_policy': 'opposite-ray radius sum about the declared axis; reported independently, no diameter acceptance tolerance inferred',
            'maximum_radial_error_mm': worst['absolute_radial_error_mm'], 'worst_sample': worst,
            'failed_sample_count': failed_sample_count, 'interpretations': interpretations,
            'scope': 'finite actual plane-section intersections at exactly the declared stations and 72 rays; no all-points, between-station, continuous-surface, source-authenticity, or native-qualification claim'}
