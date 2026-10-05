"""Bounded, all-quad socket-head fastener construction in millimetres.

This module has no Blender dependency or side effects.  The shoulder is the
head/shaft contact plane, ``direction=1`` puts the head above it, and ``-1``
reflects the whole fastener below it.  Threads and chamfers are deliberately
outside this nominal, sharp-edged geometry contract.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .quad_patches import MeshBuilder, quad_disk_cap


MAX_CIRCLE_SEGMENTS = 512
MAX_VERTICES = 100000
MAX_SHAFT_STEPS = 4096
MAX_OTHER_AXIAL_SEGMENT_MM = 4.0
TARGET_WALL_ASPECT = 24.0
MAX_ABSOLUTE_MM = 1000000.0


class FastenerError(ValueError):
    """Invalid or unbounded geometry request, detected before construction."""


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FastenerError(f'{name} must be a finite number')
    value = float(value)
    if not math.isfinite(value) or abs(value) > MAX_ABSOLUTE_MM:
        raise FastenerError(f'{name} must be finite and within +/-1000000 mm')
    if positive and value <= 0:
        raise FastenerError(f'{name} must be positive')
    return value


def _segments(radius, tolerance, limit):
    """Stable sagitta inversion; never allocate an unbounded fine circle."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 12 <= limit <= MAX_CIRCLE_SEGMENTS:
        raise FastenerError(f'max_circle_segments must be an integer from 12 to {MAX_CIRCLE_SEGMENTS}')
    # asin(sqrt(error/(2*r))) remains well conditioned for very small error/r.
    half_angle = 2.0 * math.asin(math.sqrt(min(tolerance / (2.0 * radius), 1.0)))
    if half_angle == 0 or half_angle < math.pi / limit:
        raise FastenerError('chord_error_mm requires more circle segments than the budget permits')
    count = max(12, 12 * math.ceil((math.pi / half_angle) / 12))
    if count > limit:
        raise FastenerError('chord_error_mm requires more circle segments than the budget permits')
    return count


def _circle(radius, count, z):
    return [(radius * math.cos(2.0 * math.pi * i / count),
             radius * math.sin(2.0 * math.pi * i / count), z) for i in range(count)]


def _hexagon(flat_width, count, z):
    """Ray-aligned samples on an exact hexagon, including all six corners.

    Corners occur at 0, 60, ... degrees.  Horizontal flats therefore lie at
    y=+/-flat_width/2.  Straight edges need no approximation tolerance.
    """
    apothem = flat_width / 2.0
    points = []
    for i in range(count):
        angle = 2.0 * math.pi * i / count
        normal_angle = (math.floor(i * 6 / count) + 0.5) * math.pi / 3.0
        radius = apothem / math.cos(angle - normal_angle)
        points.append((radius * math.cos(angle), radius * math.sin(angle), z))
    return points


def _role(name, smooth=False):
    return {'surface_role': name, 'smooth': smooth,
            'curved': smooth, 'support_band': False}


def _loft_subdivided(builder, first, last, steps, provenance):
    a = [builder.vertices[index] for index in first]
    b = [builder.vertices[index] for index in last]
    previous = first
    for step in range(1, steps + 1):
        current = last if step == steps else builder.loop([
            tuple(x + (y - x) * step / steps for x, y in zip(p, q)) for p, q in zip(a, b)])
        builder.loft(previous, current, provenance=provenance)
        previous = current


def _quad_circle_cap(builder, boundary, *, flip=False, provenance):
    """Circular O-grid around a real square core, with bounded radial steps.

    A square-domain Coons patch directly on a fine circle leaves almost-flat
    corner cells.  The internal diamond has four actual right-angle corners;
    annular rings connect it to the exact requested circular boundary instead.
    """
    count = len(boundary)
    points = [builder.vertices[index] for index in boundary]
    radius = math.hypot(points[0][0], points[0][1])
    square = [(p[0] * (0.65 * radius / (abs(p[0]) + abs(p[1]))),
               p[1] * (0.65 * radius / (abs(p[0]) + abs(p[1]))), p[2]) for p in points]
    rings = max(1, math.ceil(count / 24))
    previous = boundary
    for ring in range(1, rings + 1):
        t = ring / rings
        current = builder.loop([(p[0] + (q[0] - p[0]) * t,
                                 p[1] + (q[1] - p[1]) * t, p[2])
                                for p, q in zip(points, square)])
        builder.loft(previous, current, flip=flip, provenance=provenance)
        previous = current
    quad_disk_cap(builder, previous, flip=flip, provenance=provenance)


def _quad_hex_cap(builder, boundary, provenance):
    """Six conforming kite grids with no collinear-corner pseudo-quads.

    A four-sided disk map would place two grid corners halfway along a hex
    flat.  Those corner cells would have three collinear vertices.  Instead,
    each actual hex corner owns one kite bounded by its two adjacent half
    edges and two shared midpoint-to-centre grid lines.
    """
    count = len(boundary)
    steps = count // 12
    points = [builder.vertices[index] for index in boundary]
    center = (sum(p[0] for p in points) / count,
              sum(p[1] for p in points) / count, points[0][2])
    center_index = builder.vertex(center)
    rays = {}
    for corner in range(6):
        midpoint = (2 * corner + 1) * steps
        endpoint = points[midpoint]
        rays[midpoint] = [center_index] + [
            builder.vertex(tuple(center[axis] + (endpoint[axis] - center[axis]) * step / steps
                                 for axis in range(3)))
            for step in range(1, steps)] + [boundary[midpoint]]
    for corner in range(6):
        anchor = 2 * corner * steps
        previous = (anchor - steps) % count
        following = (anchor + steps) % count
        p, v, q = points[previous], points[anchor], points[following]
        grid = []
        for row in range(steps + 1):
            line = []
            t = row / steps
            for column in range(steps + 1):
                s = column / steps
                if row == 0:
                    index = rays[previous][column]
                elif column == 0:
                    index = rays[following][row]
                elif column == steps:
                    index = boundary[(previous + row) % count]
                elif row == steps:
                    index = boundary[(following - column) % count]
                else:
                    bottom = tuple((1 - s) * center[a] + s * p[a] for a in range(3))
                    top = points[(following - column) % count]
                    left = tuple((1 - t) * center[a] + t * q[a] for a in range(3))
                    right = points[(previous + row) % count]
                    mapped = tuple(
                        (1 - t) * bottom[a] + t * top[a] + (1 - s) * left[a] + s * right[a]
                        - ((1 - s) * (1 - t) * center[a] + s * (1 - t) * p[a]
                           + s * t * v[a] + (1 - s) * t * q[a])
                        for a in range(3))
                    index = builder.vertex((mapped[0], mapped[1], center[2]))
                line.append(index)
            grid.append(line)
        for row in range(steps):
            for column in range(steps):
                builder.quad(grid[row][column], grid[row][column + 1],
                             grid[row + 1][column + 1], grid[row + 1][column], provenance=provenance)


def _sharp_edges(faces, provenance, socket_corner_edges):
    edge_roles = defaultdict(set)
    for face, source in zip(faces, provenance):
        for a, b in zip(face, face[1:] + face[:1]):
            edge_roles[tuple(sorted((a, b)))].add(source['surface_role'])
    sharp = {edge for edge, roles in edge_roles.items() if len(roles) > 1}
    sharp.update(tuple(sorted(edge)) for edge in socket_corner_edges)
    return sorted(sharp)


def build_quad_fastener(*, center_mm, shaft_radius_mm,
                        head_radius_mm, head_height_mm,
                        shaft_length_mm, shoulder_z_mm,
                        feature_id='fastener', direction=1,
                        socket_flat_width_mm=None, socket_depth_mm=0,
                        chord_error_mm=0.025, max_shaft_segment_mm=4.0,
                        max_circle_segments=MAX_CIRCLE_SEGMENTS):
    """Return a closed outward-oriented all-quad nominal fastener mesh.

    All placement and solid dimensions are required; the constructor contains
    no part-specific design preset. A socket is disabled by default, with
    ``socket_flat_width_mm=None`` and depth zero (or ``None``). An enabled
    socket requires both positive dimensions and must fit wholly within the
    head. The circle chord tolerance is at most 0.05 mm; subdivision is
    bounded and an impossible budget raises :class:`FastenerError`.

    Returned ``vertices_mm`` use the requested world millimetre coordinates.
    ``face_provenance`` is parallel to ``faces`` and carries ``surface_role``
    and ``smooth``.  Consumers should use ``sharp_edges`` as hard normal seams;
    only the circular shaft/head walls request smooth shading.
    """
    if not isinstance(feature_id, str) or not feature_id or len(feature_id) > 128:
        raise FastenerError('feature_id must be a nonempty string of at most 128 characters')
    if not isinstance(center_mm, (list, tuple)) or len(center_mm) != 2:
        raise FastenerError('center_mm must contain exactly two coordinates')
    center = tuple(_number(v, 'center_mm') for v in center_mm)
    shaft_radius = _number(shaft_radius_mm, 'shaft_radius_mm', positive=True)
    head_radius = _number(head_radius_mm, 'head_radius_mm', positive=True)
    head_height = _number(head_height_mm, 'head_height_mm', positive=True)
    shaft_length = _number(shaft_length_mm, 'shaft_length_mm', positive=True)
    shoulder_z = _number(shoulder_z_mm, 'shoulder_z_mm')
    tolerance = _number(chord_error_mm, 'chord_error_mm', positive=True)
    shaft_step = _number(max_shaft_segment_mm, 'max_shaft_segment_mm', positive=True)
    if isinstance(direction, bool) or direction not in (-1, 1):
        raise FastenerError('direction must be 1 or -1')
    if head_radius <= shaft_radius:
        raise FastenerError('head_radius_mm must be greater than shaft_radius_mm')
    if tolerance > 0.05:
        raise FastenerError('chord_error_mm must not exceed 0.05 mm')
    if shaft_step > 4.0:
        raise FastenerError('max_shaft_segment_mm must not exceed 4 mm')
    socket_enabled = socket_flat_width_mm is not None
    if socket_enabled:
        socket_width = _number(socket_flat_width_mm, 'socket_flat_width_mm', positive=True)
        socket_depth = _number(socket_depth_mm, 'socket_depth_mm', positive=True)
        if socket_depth >= head_height:
            raise FastenerError('socket_depth_mm must be less than head_height_mm')
        if socket_width / math.sqrt(3.0) >= head_radius:
            raise FastenerError('socket hexagon corners must lie strictly inside the head')
    else:
        if socket_depth_mm is not None and _number(socket_depth_mm, 'socket_depth_mm') != 0:
            raise FastenerError('a disabled socket requires socket_depth_mm=None or 0')
        socket_width = socket_depth = 0.0
    if any(abs(c) + head_radius > MAX_ABSOLUTE_MM for c in center):
        raise FastenerError('fastener XY extent exceeds the coordinate budget')
    if max(abs(shoulder_z - direction * shaft_length),
           abs(shoulder_z + direction * head_height)) > MAX_ABSOLUTE_MM:
        raise FastenerError('fastener Z extent exceeds the coordinate budget')

    count = _segments(head_radius, tolerance, max_circle_segments)
    shaft_chord = 2.0 * shaft_radius * math.sin(math.pi / count)
    head_chord = 2.0 * head_radius * math.sin(math.pi / count)
    if min(shaft_chord, head_chord, shaft_length, head_height, head_radius - shaft_radius) <= 1e-7:
        raise FastenerError('fastener dimensions are below the mesh seam precision bound')
    shaft_step = min(shaft_step, TARGET_WALL_ASPECT * shaft_chord)
    head_step = min(MAX_OTHER_AXIAL_SEGMENT_MM, TARGET_WALL_ASPECT * head_chord)
    socket_step = MAX_OTHER_AXIAL_SEGMENT_MM
    socket_edge = None
    if socket_enabled:
        socket_loop = _hexagon(socket_width, count, 0.0)
        socket_edge = min(math.dist(a, b) for a, b in zip(socket_loop, socket_loop[1:] + socket_loop[:1]))
        if min(socket_edge, socket_depth, head_height - socket_depth,
               head_radius - socket_width / math.sqrt(3.0)) <= 1e-7:
            raise FastenerError('socket dimensions are below the mesh seam precision bound')
        socket_step = min(socket_step, TARGET_WALL_ASPECT * socket_edge)
    if max(shaft_length / shaft_step, head_height / head_step,
           socket_depth / socket_step) > MAX_SHAFT_STEPS:
        raise FastenerError('subdivision exceeds the axial step budget')
    shaft_steps = max(1, math.ceil(shaft_length / shaft_step))
    head_steps = max(1, math.ceil(head_height / head_step))
    socket_steps = max(1, math.ceil(socket_depth / socket_step)) if socket_enabled else 0
    shoulder_steps = max(1, math.ceil((head_radius - shaft_radius) / (TARGET_WALL_ASPECT * shaft_chord)))
    top_steps = max(1, math.ceil((head_radius - socket_width / 2) / (TARGET_WALL_ASPECT * socket_edge))) if socket_enabled else 0
    # Each disk has (N/4-1)^2 internal vertices, with existing boundary reused.
    estimated_vertices = count * (shaft_steps + head_steps + 2 + (socket_steps + 1 if socket_enabled else 0)) + 2 * (count // 4 - 1) ** 2
    estimated_vertices += count * math.ceil(count / 24) * (1 if socket_enabled else 2)
    estimated_vertices += count * (shoulder_steps - 1 + (top_steps - 1 if socket_enabled else 0))
    if estimated_vertices > MAX_VERTICES:
        raise FastenerError('fastener exceeds the vertex budget')

    builder = MeshBuilder(max_vertices=MAX_VERTICES, max_faces=MAX_VERTICES)
    shaft_rings = [builder.loop(_circle(shaft_radius, count, -shaft_length + shaft_length * step / shaft_steps))
                   for step in range(shaft_steps + 1)]
    for lower, upper in zip(shaft_rings, shaft_rings[1:]):
        builder.loft(lower, upper, provenance=_role('shaft_wall', True))
    _quad_circle_cap(builder, shaft_rings[0], flip=True, provenance=_role('shaft_tip'))

    head_rings = [builder.loop(_circle(head_radius, count, head_height * step / head_steps))
                  for step in range(head_steps + 1)]
    head_base, head_top = head_rings[0], head_rings[-1]
    _loft_subdivided(builder, shaft_rings[-1], head_base, shoulder_steps, provenance=_role('head_shoulder'))
    for lower, upper in zip(head_rings, head_rings[1:]):
        builder.loft(lower, upper, provenance=_role('head_wall', True))
    socket_corner_edges = []
    if socket_enabled:
        socket_rings = [builder.loop(_hexagon(socket_width, count,
                                             head_height - socket_depth + socket_depth * step / socket_steps))
                        for step in range(socket_steps + 1)]
        socket_floor, socket_top = socket_rings[0], socket_rings[-1]
        _loft_subdivided(builder, head_top, socket_top, top_steps, provenance=_role('head_top'))
        for lower, upper in zip(socket_rings, socket_rings[1:]):
            builder.loft(lower, upper, flip=True, provenance=_role('socket_wall'))
            socket_corner_edges.extend((lower[i], upper[i]) for i in range(0, count, count // 6))
        _quad_hex_cap(builder, socket_floor, provenance=_role('socket_floor'))
    else:
        _quad_circle_cap(builder, head_top, provenance=_role('head_top'))

    raw = builder.mesh()
    faces = [list(reversed(face)) if direction == -1 else list(face) for face in raw['faces']]
    vertices = [(center[0] + v[0], center[1] + v[1], shoulder_z + direction * v[2]) for v in raw['vertices']]
    provenance = [dict(source, feature_id=feature_id,
                       surface_id=f'{feature_id}:{source["surface_role"]}')
                  for source in raw['face_provenance']]
    circle_area_factor = count / 2.0 * math.sin(2.0 * math.pi / count)
    socket_area = math.sqrt(3.0) / 2.0 * socket_width ** 2
    solid_factor = shaft_radius ** 2 * shaft_length + head_radius ** 2 * head_height
    return {
        'vertices_mm': vertices,
        'faces': faces,
        'face_provenance': provenance,
        'sharp_edges': _sharp_edges(faces, provenance, socket_corner_edges),
        'metadata': {
            'primitive': 'quad.fastener',
            'feature_id': feature_id,
            'coordinate_unit': 'mm',
            'topology': 'all_quad_structured_closed_surface',
            'cap_method': 'circular_o_grid_square_core_and_six_kite_socket_grid',
            'circle_segments': count,
            'circle_chord_error_mm': 2.0 * head_radius * math.sin(math.pi / (2.0 * count)) ** 2,
            'requested_chord_error_mm': tolerance,
            'shaft_steps': shaft_steps,
            'shaft_segment_length_mm': shaft_length / shaft_steps,
            'head_steps': head_steps,
            'head_segment_length_mm': head_height / head_steps,
            'socket_wall_steps': socket_steps,
            'head_shoulder_radial_steps': shoulder_steps,
            'head_top_radial_steps': top_steps,
            'socket_enabled': socket_enabled,
            'socket_flat_width_mm': socket_width if socket_enabled else None,
            'socket_depth_mm': socket_depth if socket_enabled else None,
            'head_radius_mm': head_radius,
            'shaft_radius_mm': shaft_radius,
            'head_height_mm': head_height,
            'shaft_length_mm': shaft_length,
            'shoulder_z_mm': shoulder_z,
            'center_mm': list(center),
            'direction': direction,
            'expected_faceted_volume_mm3': circle_area_factor * solid_factor - socket_area * socket_depth,
            'nominal_analytic_volume_mm3': math.pi * solid_factor - socket_area * socket_depth,
            'shading': 'smooth_circular_walls_flat_caps_and_socket_sharp_role_boundaries',
            'threads': False,
            'chamfers': False,
        },
    }


# The construction name mirrors the quad primitive family at call sites.
quad_fastener = build_quad_fastener
