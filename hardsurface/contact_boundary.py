"""Qualify an actual mesh bore at a contact plane, without Blender or file I/O.

The nominal circle selects and qualifies the boundary; it never replaces it for
contact classification. All coordinates and tolerances are millimetres. Reports
are JSON-compatible and failed qualification never authorizes a contact.
"""

import math
from collections import defaultdict


MAX_LOOP_VERTICES = 512
MAX_VERTICES = 200000
MAX_TRIANGLES = 400000
_ROUND_OFF = 1e-10


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _convex_ccw(loop):
    """Global supporting-line test also rejects self-crossing star polygons."""
    if not 3 <= len(loop) <= MAX_LOOP_VERTICES:
        return False
    for i, a in enumerate(loop):
        b = loop[(i + 1) % len(loop)]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= _ROUND_OFF:
            return False
        for j, p in enumerate(loop):
            if j not in (i, (i + 1) % len(loop)) and _cross(a, b, p) <= _ROUND_OFF * length:
                return False
    return True


def _fit_circle(loop, expected_center):
    # Shift twice before forming the normal equations, avoiding cancellation at
    # large world coordinates. Python float arithmetic is IEEE binary64.
    relative = [(p[0] - expected_center[0], p[1] - expected_center[1]) for p in loop]
    mx = math.fsum(p[0] for p in relative) / len(relative)
    my = math.fsum(p[1] for p in relative) / len(relative)
    centered = [(x - mx, y - my) for x, y in relative]
    xx = math.fsum(x * x for x, y in centered)
    xy = math.fsum(x * y for x, y in centered)
    yy = math.fsum(y * y for x, y in centered)
    determinant = xx * yy - xy * xy
    if determinant <= _ROUND_OFF * (xx + yy) ** 2:
        raise ValueError('circle_fit_singular')
    bx = math.fsum(x * (x * x + y * y) for x, y in centered)
    by = math.fsum(y * (x * x + y * y) for x, y in centered)
    ax = (bx * yy - by * xy) / (2.0 * determinant)
    ay = (by * xx - bx * xy) / (2.0 * determinant)
    center = (expected_center[0] + mx + ax, expected_center[1] + my + ay)
    radius = math.sqrt((xx + yy) / len(loop) + ax * ax + ay * ay)
    return center, radius


def qualified_hole_boundary(vertices, triangles, *, center_mm, radius_mm,
                            z_mm, epsilon_mm=.002,
                            position_tolerance_mm=.05,
                            chord_tolerance_mm=.05):
    """Extract one actual convex bore loop from a horizontal triangle submesh.

    Only triangles whose three vertices lie within ``epsilon_mm`` of ``z_mm``
    participate. Candidate edges have incidence one in that entire submesh and
    both endpoints within the nominal radial position tolerance. Missing edges,
    multiple loops, filled disks, malformed topology, and unqualified circle or
    chord geometry fail closed. The caller must retain its own contact-plane,
    outer-region, whole-mesh topology, and independent containment checks.
    """
    report = {'status': 'fail', 'errors': [], 'reason': None, 'loop_mm': [],
              'loop_vertex_indices': [], 'method': 'actual_plane_submesh_bore_boundary',
              'plane_triangle_count': 0, 'candidate_edge_count': 0}

    def fail(reason):
        report['reason'] = reason
        report['errors'].append(reason)
        return report

    try:
        cx, cy = (float(x) for x in center_mm)
        radius, z = float(radius_mm), float(z_mm)
        epsilon = float(epsilon_mm)
        position = float(position_tolerance_mm)
        chord = float(chord_tolerance_mm)
        if not all(math.isfinite(x) for x in (cx, cy, radius, z, epsilon, position, chord)):
            return fail('nonfinite_specification')
        if radius <= 0 or not 0 < epsilon <= .002 or not 0 < position <= .05 or not 0 < chord <= .05:
            return fail('invalid_or_widened_tolerance')
        report.update(expected_center_mm=[cx, cy], expected_radius_mm=radius,
                      z_mm=z, epsilon_mm=epsilon, position_tolerance_mm=position,
                      chord_tolerance_mm=chord)
        if not 3 <= len(vertices) <= MAX_VERTICES or not 1 <= len(triangles) <= MAX_TRIANGLES:
            return fail('mesh_cardinality_out_of_bounds')
        points = [tuple(float(x) for x in p) for p in vertices]
        if any(len(p) != 3 or not all(math.isfinite(x) for x in p) for p in points):
            return fail('invalid_mesh_vertex')
        plane = [abs(p[2] - z) <= epsilon for p in points]
        near = [abs(math.hypot(p[0] - cx, p[1] - cy) - radius) <= position for p in points]
        edges = defaultdict(list)
        for raw in triangles:
            tri = tuple(raw)
            if len(tri) != 3 or any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(points) for i in tri):
                return fail('invalid_triangle_indices')
            if not all(plane[i] for i in tri):
                continue
            if len(set(tri)) != 3 or abs(_cross(*(points[i] for i in tri))) <= _ROUND_OFF ** 2:
                return fail('degenerate_plane_triangle')
            report['plane_triangle_count'] += 1
            for a, b, other in ((tri[0], tri[1], tri[2]), (tri[1], tri[2], tri[0]), (tri[2], tri[0], tri[1])):
                edges[tuple(sorted((a, b)))].append(other)
        if not report['plane_triangle_count']:
            return fail('missing_contact_plane_submesh')
        candidates = {edge: incident[0] for edge, incident in edges.items()
                      if len(incident) == 1 and all(near[i] for i in edge)}
        report['candidate_edge_count'] = len(candidates)
        if any(len(incident) > 2 and all(near[i] for i in edge) for edge, incident in edges.items()):
            return fail('nonmanifold_boundary_neighborhood')
        if not candidates:
            return fail('missing_bore_boundary')
        adjacency = defaultdict(list)
        for a, b in candidates:
            adjacency[a].append(b)
            adjacency[b].append(a)
        if len(adjacency) > MAX_LOOP_VERTICES:
            return fail('boundary_vertex_budget_exceeded')
        if any(len(neighbors) != 2 for neighbors in adjacency.values()):
            return fail('boundary_not_degree_two')
        start = min(adjacency)
        indices, previous, current = [], None, start
        while current not in indices:
            indices.append(current)
            following = next(i for i in adjacency[current] if i != previous)
            previous, current = current, following
        if current != start or len(indices) != len(adjacency):
            return fail('boundary_not_one_closed_loop')
        loop = [points[i] for i in indices]
        # Translate the area calculation too: only orientation is needed.
        area2 = math.fsum(_cross(loop[0], loop[i], loop[i + 1]) for i in range(1, len(loop) - 1))
        if area2 < 0:
            indices.reverse()
            loop.reverse()
        if not _convex_ccw(loop):
            return fail('boundary_not_nondegenerate_convex')
        for k, a in enumerate(indices):
            b = indices[(k + 1) % len(indices)]
            other = candidates[tuple(sorted((a, b)))]
            if _cross(points[a], points[b], points[other]) >= 0:
                return fail('boundary_is_not_a_bore')
        fitted_center, fitted_radius = _fit_circle(loop, (cx, cy))
        center_error = math.hypot(fitted_center[0] - cx, fitted_center[1] - cy)
        radius_error = abs(fitted_radius - radius)
        residual = max(abs(math.hypot(p[0] - fitted_center[0], p[1] - fitted_center[1]) - fitted_radius) for p in loop)
        sagitta = max(max(0.0, fitted_radius - _segment_distance(fitted_center, a, loop[(i + 1) % len(loop)])) for i, a in enumerate(loop))
        report.update(fitted_center_mm=list(fitted_center), fitted_radius_mm=fitted_radius,
                      center_error_mm=center_error, radius_error_mm=radius_error,
                      max_fit_residual_mm=residual, max_chord_sagitta_mm=sagitta,
                      plane_error_mm=max(abs(p[2] - z) for p in loop))
        if center_error > position:
            return fail('fitted_center_out_of_tolerance')
        if radius_error > position:
            return fail('fitted_radius_out_of_tolerance')
        if residual > position:
            return fail('circle_fit_residual_out_of_tolerance')
        if sagitta > chord:
            return fail('chord_sagitta_out_of_tolerance')
        # The fitted center must be strictly within the complete actual loop.
        if any(_cross(a, loop[(i + 1) % len(loop)], fitted_center) <= 0 for i, a in enumerate(loop)):
            return fail('fitted_center_not_inside_boundary')
        report.update(status='pass', reason=None, loop_mm=[list(p) for p in loop],
                      loop_vertex_indices=indices, boundary_vertex_count=len(loop))
        return report
    except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        return fail('invalid_boundary_input: ' + str(exc))


def outside_or_boundary(point, report, epsilon_mm=.002):
    """Whether a point on the qualified plane avoids the actual bore interior.

    Boundary inclusion uses Euclidean distance to actual finite segments. The
    numerical epsilon cannot exceed either 0.002 mm or qualification's epsilon.
    A status-only/synthetic report without qualified geometry fails closed.
    """
    try:
        epsilon = float(epsilon_mm)
        if report.get('status') != 'pass' or report.get('method') != 'actual_plane_submesh_bore_boundary':
            return False
        if not math.isfinite(epsilon) or not 0 < epsilon <= min(.002, report['epsilon_mm']):
            return False
        p = tuple(float(x) for x in point)
        if len(p) != 3 or not all(math.isfinite(x) for x in p) or abs(p[2] - report['z_mm']) > epsilon:
            return False
        loop = report['loop_mm']
        if not 3 <= len(loop) <= MAX_LOOP_VERTICES or len(report['loop_vertex_indices']) != len(loop):
            return False
        if any(len(v) != 3 or not all(math.isfinite(x) for x in v) or abs(v[2] - report['z_mm']) > report['epsilon_mm'] for v in loop):
            return False
        # Qualification already performed the O(n^2) global convexity check.
        # Local checks remain O(n), rejecting absent/repeated/reversed geometry
        # without turning every intersection witness into a fresh mesh audit.
        for i, a in enumerate(loop):
            b, c = loop[(i + 1) % len(loop)], loop[(i + 2) % len(loop)]
            if _cross(a, b, c) <= _ROUND_OFF * math.hypot(b[0] - a[0], b[1] - a[1]):
                return False
        if any(_segment_distance(p, a, loop[(i + 1) % len(loop)]) <= epsilon for i, a in enumerate(loop)):
            return True
        return any(_cross(a, loop[(i + 1) % len(loop)], p) < 0 for i, a in enumerate(loop))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
