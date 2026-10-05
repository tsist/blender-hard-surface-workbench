"""Bounded, source-preserving inspection of real control/evaluated topology.

No bpy import at host/schema time. The worker owns dependency auditing, CPU
isolation and the hard wall watchdog. This module never saves a blend, changes
source geometry, welds vertices, applies modifiers, or judges artistic quality.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import contract as c
from .io import RuntimeFailure, checked_path, descriptor, verify_descriptor

LIMITS = {'scene_objects': 4096, 'features': 128, 'selected_objects': 128, 'aggregate_vertices': 200000,
          'aggregate_edges': 600000, 'aggregate_polygons': 400000,
          'aggregate_polygon_corners': 1200000, 'aggregate_loop_triangles': 400000,
          'face_corners': 8192, 'finding_examples_per_category': 32,
          'json_file_bytes': 8 * 1024 * 1024, 'aggregate_json_bytes': 128 * 1024 * 1024}
THRESHOLDS = {
    'zero_area_m2': 1e-16, 'duplicate_vertex_quantization_m': 1e-9,
    'nonplanarity_absolute_m': 1e-7, 'nonplanarity_relative_to_face_diagonal': 1e-5,
    'concavity_relative_cross_tolerance': 1e-10,
    'long_thin_triangle_longest_edge_over_altitude': 20.0,
    'quad_warp_degrees': 5.0, 'pole_definition': 'edge-neighbour valence differs from 4',
    'purpose': 'Diagnostic thresholds only; no automatic modeling-quality pass/fail',
}
REQUEST = c.obj({
    'schema_version': c.const('1.0'), 'command': c.const('hardsurface.topology'),
    'params': c.obj({
        'request_id': c.string(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', minLength=1, maxLength=128),
        'source': c.FILE,
        'feature_ids': c.array(c.IDENT, 1, LIMITS['features'], uniqueItems=True),
        'object_ids': c.array(c.UUID, 1, LIMITS['selected_objects'], uniqueItems=True),
        'mesh_states': c.optional_default(c.array(c.enum('control', 'evaluated'), 1, 2, uniqueItems=True), ['control', 'evaluated']),
        'export_geometry': c.optional_default(c.BOOL, False),
        'self_intersections': c.optional_default(c.BOOL, False),
        'cpu_threads': c.optional_default(c.integer(minimum=1, maximum=4), 2),
        'wall_seconds': c.optional_default(c.number(exclusiveMinimum=0, maximum=600), 600),
    }, ['request_id', 'source'], **{'not': {'required': ['feature_ids', 'object_ids']}}),
})


def schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Hard Surface Workbench read-only topology 1.0', **copy.deepcopy(REQUEST)}


def validate_request(request):
    if isinstance(request, (str, bytes, bytearray)):
        request = c.strict_loads(request)
    c._walk_limits(request)
    if len(c.canonical_bytes(request)) > c.MAX_BYTES:
        raise c.ContractError('LIMIT_EXCEEDED', 'Request exceeds 2 MiB')
    result = c._validate(request, REQUEST)
    c._paths(result)
    if 'feature_ids' in result['params'] and 'object_ids' in result['params']:
        raise c.ContractError('INVALID_REQUEST', 'feature_ids and object_ids are mutually exclusive')
    if Path(result['params']['source']['file']).suffix.lower() != '.blend':
        raise c.ContractError('INVALID_REQUEST', 'Source must be a saved .blend file')
    return result


normalize_request = validate_request


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _mean(points):
    return tuple(sum(p[i] for p in points) / len(points) for i in range(3))


def _world(point, matrix):
    return tuple(sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3))


def _matrix_values(matrix):
    result = [[float(v) for v in row] for row in matrix]
    if len(result) != 4 or any(len(r) != 4 for r in result) or not all(math.isfinite(v) for r in result for v in r):
        raise RuntimeFailure('TOPOLOGY_TRANSFORM', 'Expected a finite 4 by 4 world matrix')
    if result[3] != [0.0, 0.0, 0.0, 1.0]:
        raise RuntimeFailure('TOPOLOGY_TRANSFORM', 'Projective mesh transforms are unsupported')
    return result


def _json_bytes(value):
    # Mesh evidence has independent size limits, not the request node budget.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _counts(mesh):
    result = {'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'polygons': len(mesh.polygons),
              'polygon_corners': len(mesh.loops)}
    for key, count in result.items():
        if count > LIMITS['aggregate_' + key]:
            raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT', 'Mesh exceeds topology budget before polygon traversal', domain=key, count=count)
    # Blender tessellates a valid n-gon into n-2 triangles; no tessellation is
    # triggered before this cheap preflight and the hard worker watchdog applies.
    triangle_upper = 0
    for polygon in mesh.polygons:
        corners = len(polygon.vertices)
        if not 3 <= corners <= LIMITS['face_corners']:
            raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT', 'Polygon corner count outside supported bounds', corners=corners)
        triangle_upper += corners - 2
    result['loop_triangles'] = triangle_upper
    for key, count in result.items():
        if count > LIMITS['aggregate_' + key]:
            raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT', 'Mesh exceeds topology budget before analysis', domain=key, count=count)
    return result


def _consume_budget(total, counts):
    for key, count in counts.items():
        total[key] = total.get(key, 0) + count
        if total[key] > LIMITS['aggregate_' + key]:
            raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT', 'Selected instances and states exceed aggregate budget',
                                 domain=key, count=total[key], limit=LIMITS['aggregate_' + key])


def _extract(mesh):
    vertices = [tuple(float(v) for v in vertex.co) for vertex in mesh.vertices]
    if any(len(v) != 3 or not all(math.isfinite(x) and abs(x) <= 1000000 for x in v) for v in vertices):
        raise RuntimeFailure('TOPOLOGY_GEOMETRY', 'Coordinates must be finite and within +/-1000000 metres')
    edges = [tuple(int(v) for v in edge.vertices) for edge in mesh.edges]
    polygons = [tuple(int(v) for v in polygon.vertices) for polygon in mesh.polygons]
    for indices in edges + polygons:
        if any(i < 0 or i >= len(vertices) for i in indices):
            raise RuntimeFailure('TOPOLOGY_GEOMETRY', 'Mesh contains out-of-range vertex indices')
    return {'vertices': vertices, 'edges': edges, 'polygons': polygons}


def _visible(obj, view_layer=None, render_visible=None):
    if obj.hide_render or getattr(obj, 'hide_viewport', False):
        return False
    if render_visible is not None and obj.name not in render_visible:
        return False
    if hasattr(obj, 'hide_get') and obj.hide_get(view_layer=view_layer):
        return False
    if hasattr(obj, 'visible_get') and not obj.visible_get(view_layer=view_layer):
        return False
    return True


def _resolve_features(objects, requested=None, view_layer=None, render_visible=None, object_ids=None):
    """Expand features to all instances, keyed only by verified persistent UUID.

    Pattern instances legitimately share feature IDs and mesh data; a feature
    ID is a grouping/filter dimension, never a substitute for object identity.
    """
    if requested is not None and object_ids is not None:
        raise RuntimeFailure('TOPOLOGY_SELECTION', 'feature_ids and object_ids are mutually exclusive')
    eligible = {}
    feature_objects = defaultdict(list)
    excluded = Counter()
    for obj in objects:
        if obj.type != 'MESH':
            excluded['non_mesh'] += 1
        elif not _visible(obj, view_layer, render_visible):
            excluded['hidden_mesh'] += 1
        elif not obj.get('hs_generated'):
            excluded['non_generated_mesh'] += 1
        elif not isinstance(obj.get('hs_feature_id'), str):
            excluded['missing_feature_id_mesh'] += 1
        else:
            fid, oid = obj.get('hs_feature_id'), obj.get('hs_object_id')
            try:
                c._validate(fid, c.IDENT)
                c._validate(oid, c.UUID)
            except c.ContractError as error:
                raise RuntimeFailure('TOPOLOGY_OBJECT_ID', 'Final generated mesh requires valid feature and object identities',
                                     object_name=obj.name, feature_id=fid, object_id=oid) from error
            if oid in eligible:
                raise RuntimeFailure('TOPOLOGY_OBJECT_ID', 'Persistent object ID is duplicated across final visible meshes',
                                     object_id=oid, object_names=[eligible[oid].name, obj.name])
            eligible[oid] = obj
            feature_objects[fid].append(oid)
    if object_ids is not None:
        ids = list(object_ids)
        for oid in ids:
            c._validate(oid, c.UUID)
            if oid not in eligible:
                raise RuntimeFailure('TOPOLOGY_OBJECT_ID', 'Object ID does not identify a final visible generated mesh', object_id=oid)
        mode = 'explicit_object_ids'
    elif requested is not None:
        ids = []
        for fid in requested:
            c._validate(fid, c.IDENT)
            if fid not in feature_objects:
                raise RuntimeFailure('TOPOLOGY_FEATURE_ID', 'Feature has no final visible generated mesh instances', feature_id=fid)
            ids.extend(sorted(feature_objects[fid]))
        mode = 'explicit_feature_ids_expanded_to_instances'
    else:
        ids = sorted(eligible)
        mode = 'all_final_visible_generated_meshes'
    if not ids or len(ids) > LIMITS['selected_objects']:
        raise RuntimeFailure('TOPOLOGY_SELECTION', 'Selection requires 1 to 128 final generated mesh object instances', count=len(ids))
    selected = {oid: eligible[oid] for oid in ids}
    excluded['unrequested_final_mesh'] = len(eligible) - len(selected)
    return selected, {'mode': mode, 'object_ids': list(selected),
                      'feature_ids': sorted({obj.get('hs_feature_id') for obj in selected.values()}),
                      'selected_objects': len(selected), 'instances_count': len(selected), 'scene_objects': len(objects),
                      'excluded_counts': {key: excluded[key] for key in ('non_mesh', 'hidden_mesh', 'non_generated_mesh',
                                                                        'missing_feature_id_mesh', 'unrequested_final_mesh')},
                      'visibility': 'render-enabled and visible in active view layer; hidden cutters excluded'}


def _render_visible_names(collection, parent_visible=True, visited=None):
    # A multiply linked object is render-visible if at least one collection path is.
    visited = set() if visited is None else visited
    token = (id(collection), parent_visible)
    if token in visited:
        return set()
    visited.add(token)
    visible = parent_visible and not collection.hide_render
    names = {obj.name for obj in collection.objects} if visible else set()
    for child in collection.children:
        names.update(_render_visible_names(child, visible, visited))
    return names


def _modifiers(obj):
    observed = []
    for modifier in obj.modifiers:
        if (modifier.show_viewport != modifier.show_render or
                (modifier.type in ('SUBSURF', 'MULTIRES') and modifier.levels != modifier.render_levels)):
            raise RuntimeFailure('TOPOLOGY_EVALUATION', 'Viewport and render modifier evaluation must match',
                                 object_name=obj.name, modifier=modifier.name)
        row = {'name': modifier.name, 'type': modifier.type, 'show_viewport': modifier.show_viewport,
               'show_render': modifier.show_render}
        if modifier.type == 'BEVEL':
            for name in ('width', 'segments', 'profile', 'limit_method', 'angle_limit', 'offset_type',
                         'affect', 'use_clamp_overlap', 'harden_normals', 'loop_slide', 'miter_outer', 'miter_inner'):
                if hasattr(modifier, name):
                    row[name] = getattr(modifier, name)
            row['clamp_semantics'] = 'Configuration observed; local effective bevel width/clamping is not measured'
        observed.append(row)
    return observed


def _triangle_metrics(points):
    edge_lengths = [_length(_sub(points[(i + 1) % 3], points[i])) for i in range(3)]
    double_area = _length(_cross(_sub(points[1], points[0]), _sub(points[2], points[0])))
    longest = max(edge_lengths)
    # longest / altitude == longest squared / twice area. Null denotes a
    # degenerate triangle, independently counted instead of emitting Infinity.
    return double_area / 2, longest * longest / double_area if double_area > 0 else None


def _shading_summary(mesh):
    """Observe existing flags/normals without recalculation or edits to source."""
    sharp = getattr(mesh, 'attributes', {}).get('sharp_edge')
    sharp_count = (sum(bool(item.value) for item in sharp.data) if sharp is not None
                   else sum(bool(getattr(edge, 'use_edge_sharp', False)) for edge in mesh.edges))
    smooth_count = sum(bool(getattr(face, 'use_smooth', False)) for face in mesh.polygons)
    normals = getattr(mesh, 'corner_normals', None)
    normal_info = {'available': normals is not None, 'coordinate_space': 'object_local',
                   'source': 'Blender mesh.corner_normals', 'count': 0}
    if normals is not None:
        first = {}
        split = set()
        max_angle = 0.0
        invalid = 0
        for index, item in enumerate(normals):
            normal = tuple(float(x) for x in item.vector)
            norm = _length(normal)
            if not all(math.isfinite(x) for x in normal) or norm <= 0:
                invalid += 1
                continue
            normal = tuple(x / norm for x in normal)
            vertex = mesh.loops[index].vertex_index
            if vertex in first:
                angle = math.degrees(math.acos(max(-1., min(1., _dot(normal, first[vertex])))))
                max_angle = max(max_angle, angle)
                if angle > .001:
                    split.add(vertex)
            else:
                first[vertex] = normal
        normal_info.update(count=len(normals), invalid_or_zero_count=invalid,
                           vertices_with_corner_angle_above_001_degree=len(split),
                           maximum_angle_from_first_corner_degrees=max_angle,
                           angle_semantics='Each corner compared with first valid corner at the same vertex; not all-pairs maximum')
    return {'sharp_edge_count': sharp_count, 'smooth_face_count': smooth_count,
            'flat_face_count': len(mesh.polygons) - smooth_count,
            'has_custom_normals': bool(getattr(mesh, 'has_custom_normals', False)),
            'normals_domain': getattr(mesh, 'normals_domain', None), 'corner_normals': normal_info}


def _shading_export(mesh):
    sharp = getattr(mesh, 'attributes', {}).get('sharp_edge')
    normals = getattr(mesh, 'corner_normals', None)
    return {'edge_sharp': ([bool(item.value) for item in sharp.data] if sharp is not None
                           else [bool(getattr(edge, 'use_edge_sharp', False)) for edge in mesh.edges]),
            'face_smooth': [bool(getattr(face, 'use_smooth', False)) for face in mesh.polygons],
            'polygon_normals': [list(float(x) for x in face.normal) if hasattr(face, 'normal') else None for face in mesh.polygons],
            'corner_normals': [list(float(x) for x in item.vector) for item in normals] if normals is not None else None,
            'loop_vertex_indices': [int(loop.vertex_index) for loop in mesh.loops],
            'polygon_loop_starts': [int(face.loop_start) for face in mesh.polygons],
            'normal_coordinate_space': 'object_local; compare corner normals to polygon normals separately from plane-distance geometry tests'}


def _canonical_face(face):
    # Booth's least-rotation algorithm is linear even for repeated vertices.
    def rotation(values):
        doubled = tuple(values) * 2
        n, i, j, offset = len(values), 0, 1, 0
        while i < n and j < n and offset < n:
            left, right = doubled[i + offset], doubled[j + offset]
            if left == right:
                offset += 1
                continue
            if left > right:
                i += offset + 1
                if i == j:
                    i += 1
            else:
                j += offset + 1
                if i == j:
                    j += 1
            offset = 0
        start = min(i, j)
        return doubled[start:start + n]
    return min(rotation(face), rotation(tuple(reversed(face))))


def _analyze(geometry, triangles, matrix, identity=None, state='control', deadline=lambda: None):
    """Analyze real mesh domains. Tessellation remains a separately named domain.

    Coordinates/metrics are in metres; findings retain original domain indices.
    Duplicate points are equal quantized local coordinate cells, not an all-pairs
    proximity test, and duplicate faces use vertex indices rather than geometry.
    """
    vertices, edges, faces = geometry['vertices'], geometry['edges'], geometry['polygons']
    world = [_world(v, matrix) for v in vertices]
    if not all(math.isfinite(x) and abs(x) <= 1000000 for point in world for x in point):
        raise RuntimeFailure('TOPOLOGY_TRANSFORM', 'World coordinates must be finite and within +/-1000000 metres')
    findings = {}
    who = {key: identity.get(key) for key in ('feature_id', 'object_id', 'name')} if identity else {}

    def finding(kind, *, vertex_indices=(), face_index=None, edge_index=None, point=None, **details):
        entry = findings.setdefault(kind, {'count': 0, 'examples': []})
        entry['count'] += 1
        if len(entry['examples']) >= LIMITS['finding_examples_per_category']:
            return
        indices = list(vertex_indices)
        local = point if point is not None else (_mean([vertices[i] for i in indices]) if indices else (0., 0., 0.))
        example = {**who, 'mesh_state': state, 'vertex_indices': indices[:16],
                   'vertex_indices_total': len(indices), 'local_position_m': list(local),
                   'world_position_m': list(_world(local, matrix)), **details}
        if face_index is not None:
            example['face_index'] = face_index
        if edge_index is not None:
            example['edge_index'] = edge_index
        entry['examples'].append(example)

    neighbours = [set() for _ in vertices]
    incident_faces = [set() for _ in vertices]
    edge_faces = defaultdict(list)
    edge_indices = defaultdict(list)
    duplicate_edges = 0
    for index, edge in enumerate(edges):
        if index % 4096 == 0:
            deadline()
        a, b = edge
        key = tuple(sorted(edge))
        if edge_indices[key]:
            duplicate_edges += 1
            finding('duplicate_edges', vertex_indices=edge, edge_index=index, first_edge_index=edge_indices[key][0])
        edge_indices[key].append(index)
        neighbours[a].add(b)
        neighbours[b].add(a)
        if a == b or world[a] == world[b]:
            finding('zero_length_edges', vertex_indices=edge, edge_index=index)

    areas = []
    nonplanarity_max = 0.0
    quad_warp_max = 0.0
    side_counts = Counter()
    seen_faces = {}
    source_triangle_ratios = []
    source_degenerate_triangles = 0
    for index, face in enumerate(faces):
        if index % 1024 == 0:
            deadline()
        side_counts[len(face)] += 1
        for vertex in face:
            incident_faces[vertex].add(index)
        for a, b in zip(face, (*face[1:], face[0])):
            edge_faces[tuple(sorted((a, b)))].append((index, 1 if a < b else -1))
        if len(set(face)) != len(face):
            finding('repeated_face_vertices', vertex_indices=face, face_index=index)
        canonical = _canonical_face(face)
        if canonical in seen_faces:
            finding('duplicate_faces', vertex_indices=face, face_index=index, first_face_index=seen_faces[canonical])
        else:
            seen_faces[canonical] = index
        points = [world[i] for i in face]
        center = _mean(points)
        # Newell's area vector with centered coordinates avoids translation loss.
        centered = [_sub(p, center) for p in points]
        cross_vectors = [_cross(a, b) for a, b in zip(centered, (*centered[1:], centered[0]))]
        area_vector = tuple(sum(v[i] for v in cross_vectors) for i in range(3))
        norm = _length(area_vector)
        area = norm / 2
        areas.append(area)
        if area <= THRESHOLDS['zero_area_m2']:
            finding('zero_area_faces', vertex_indices=face, face_index=index, projected_area_m2=area)
        diagonal = _length(tuple(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)))
        if norm > 0:
            normal = tuple(v / norm for v in area_vector)
            deviation = max(abs(_dot(p, normal)) for p in centered)
            nonplanarity_max = max(nonplanarity_max, deviation)
            if deviation > max(THRESHOLDS['nonplanarity_absolute_m'], diagonal * THRESHOLDS['nonplanarity_relative_to_face_diagonal']):
                finding('nonplanar_faces', vertex_indices=face, face_index=index, maximum_plane_distance_m=deviation)
            # Reflex corners relative to the oriented Newell normal diagnose
            # concavity; self-intersection is explicitly outside this test.
            tolerance = diagonal * diagonal * THRESHOLDS['concavity_relative_cross_tolerance']
            reflex = [i for i in range(len(face)) if _dot(_cross(_sub(points[i], points[i - 1]),
                        _sub(points[(i + 1) % len(face)], points[i])), normal) < -tolerance]
            if reflex:
                finding('concave_faces', vertex_indices=face, face_index=index, reflex_corner_offsets=reflex[:16])
        if len(face) == 3:
            tri_area, ratio = _triangle_metrics(points)
            if tri_area <= THRESHOLDS['zero_area_m2']:
                source_degenerate_triangles += 1
            if ratio is not None:
                source_triangle_ratios.append(ratio)
                if ratio > THRESHOLDS['long_thin_triangle_longest_edge_over_altitude']:
                    finding('long_thin_source_triangles', vertex_indices=face, face_index=index, longest_edge_over_altitude=ratio)
        elif len(face) == 4:
            # Maximum bend between triangle normals for the two possible
            # diagonals. This is a geometric diagnostic, not a tessellation edit.
            angles = []
            for a, b, d, e in ((0, 1, 2, 3), (1, 2, 3, 0)):
                n1 = _cross(_sub(points[b], points[a]), _sub(points[d], points[a]))
                n2 = _cross(_sub(points[d], points[a]), _sub(points[e], points[a]))
                size = _length(n1) * _length(n2)
                if size > 0:
                    angles.append(math.degrees(math.acos(max(-1.0, min(1.0, _dot(n1, n2) / size)))))
            warp = max(angles, default=0.0)
            quad_warp_max = max(quad_warp_max, warp)
            if warp > THRESHOLDS['quad_warp_degrees']:
                finding('warped_quads', vertex_indices=face, face_index=index, maximum_diagonal_bend_degrees=warp)

    boundary = nonmanifold = wire_edges = winding = 0
    nonmanifold_vertices = set()
    for key, actual_indices in edge_indices.items():
        uses = edge_faces.get(key, [])
        if not uses:
            wire_edges += len(actual_indices)
            for index in actual_indices:
                finding('loose_edges', vertex_indices=key, edge_index=index)
        if len(uses) == 1:
            boundary += len(actual_indices)
            for index in actual_indices:
                finding('boundary_edges', vertex_indices=key, edge_index=index)
        if len(uses) != 2:
            nonmanifold += len(actual_indices)
            nonmanifold_vertices.update(key)
            for index in actual_indices:
                finding('nonmanifold_edges', vertex_indices=key, edge_index=index, incident_face_count=len(uses))
        if len(uses) == 2 and uses[0][1] == uses[1][1]:
            winding += len(actual_indices)
            finding('inconsistent_winding_edges', vertex_indices=key, edge_index=actual_indices[0], face_indices=[u[0] for u in uses])
    missing_edges = set(edge_faces) - set(edge_indices)
    for edge in missing_edges:
        finding('face_edges_missing_from_edge_domain', vertex_indices=edge)

    valence = Counter(len(adjacent) for adjacent in neighbours)
    point_cells = {}
    duplicate_vertices = 0
    quantization = THRESHOLDS['duplicate_vertex_quantization_m']
    for index, point in enumerate(vertices):
        if index % 4096 == 0:
            deadline()
        degree = len(neighbours[index])
        if degree != 4:
            finding('poles', vertex_indices=[index], valence=degree)
        if not incident_faces[index] and not neighbours[index]:
            finding('loose_vertices', vertex_indices=[index])
        cell = tuple(round(x / quantization) for x in point)
        if cell in point_cells:
            duplicate_vertices += 1
            finding('duplicate_vertex_cells', vertex_indices=[index], first_vertex_index=point_cells[cell])
        else:
            point_cells[cell] = index
        # Vertex fan components detect bow-tie vertices even when all incident
        # edges individually have two faces. Each adjacency is visited linearly.
        incident = incident_faces[index]
        if incident:
            graph = {face: set() for face in incident}
            for adjacent in neighbours[index]:
                uses = [f for f, _ in edge_faces.get(tuple(sorted((index, adjacent))), [])]
                if uses:
                    # A star suffices for connectivity and stays linear on
                    # pathological high-incidence edges.
                    graph[uses[0]].update(uses[1:])
                    for f in uses[1:]:
                        graph[f].add(uses[0])
            unseen = set(incident)
            components = 0
            while unseen:
                components += 1
                pending = [unseen.pop()]
                while pending:
                    current = pending.pop()
                    more = graph[current] & unseen
                    unseen.difference_update(more)
                    pending.extend(more)
            if components > 1:
                nonmanifold_vertices.add(index)
                finding('disconnected_vertex_fans', vertex_indices=[index], fan_components=components)
        elif not neighbours[index]:
            nonmanifold_vertices.add(index)

    for index in sorted(nonmanifold_vertices):
        finding('nonmanifold_vertices', vertex_indices=[index])

    render_ratios = []
    render_degenerate = 0
    signed_terms = []
    # Recentering improves cancellation for translated closed objects. For an
    # open mesh the reported signed volume is only a reference-dependent sum.
    reference = _mean(world) if world else (0., 0., 0.)
    for index, triangle in enumerate(triangles):
        if index % 4096 == 0:
            deadline()
        indices, polygon_index = triangle['vertices'], triangle['polygon_index']
        if len(indices) != 3 or any(i < 0 or i >= len(vertices) for i in indices) or not 0 <= polygon_index < len(faces):
            raise RuntimeFailure('TOPOLOGY_GEOMETRY', 'Invalid loop-triangle mapping')
        points = [world[i] for i in indices]
        tri_area, ratio = _triangle_metrics(points)
        signed_terms.append(_dot(_sub(points[0], reference), _cross(_sub(points[1], reference), _sub(points[2], reference))) / 6)
        if tri_area <= THRESHOLDS['zero_area_m2']:
            render_degenerate += 1
        if ratio is not None:
            render_ratios.append(ratio)
            if ratio > THRESHOLDS['long_thin_triangle_longest_edge_over_altitude']:
                finding('long_thin_loop_triangles', vertex_indices=indices, face_index=polygon_index,
                        loop_triangle_index=index, longest_edge_over_altitude=ratio)
    for entry in findings.values():
        entry['examples_truncated'] = entry['count'] > len(entry['examples'])
    topology_hash = _hash({'version': 'HS_TOPOLOGY_DOMAINS_V1', 'vertices': len(vertices), 'edges': edges, 'polygons': faces})
    geometry_hash = _hash({'version': 'HS_LOCAL_GEOMETRY_V1', **geometry})
    anchor = vertices[0] if vertices else (0., 0., 0.)
    candidate_signature = _hash({'version': 'HS_INDEXED_TRANSLATION_QUANTIZED_SHAPE_V1', 'edges': edges, 'polygons': faces,
                                 'relative_vertices': [tuple(round((v[i] - anchor[i]) / quantization) for i in range(3)) for v in vertices]})
    counts = {'vertices': len(vertices), 'edges': len(edges), 'faces': len(faces),
              'triangles': side_counts[3], 'quads': side_counts[4], 'ngons': sum(n for sides, n in side_counts.items() if sides > 4),
              'maximum_face_sides': max(side_counts, default=0), 'polygon_corners': sum(len(f) for f in faces)}
    world_bounds = [[min(v[i] for v in world) for i in range(3)], [max(v[i] for v in world) for i in range(3)]] if world else None
    metrics = {
        'counts': counts, 'face_side_histogram': {str(k): v for k, v in sorted(side_counts.items())},
        'valence_histogram': {str(k): v for k, v in sorted(valence.items())},
        'poles': sum(n for val, n in valence.items() if val != 4), 'boundary_edges': boundary,
        'nonmanifold_edges': nonmanifold, 'loose_edges': wire_edges, 'inconsistent_winding_edges': winding,
        'nonmanifold_vertices': len(nonmanifold_vertices),
        'nonmanifold_vertex_semantics': 'Incident to a non-2-face edge, disconnected face fan, or isolated vertex; includes boundary vertices',
        'duplicate_edges': duplicate_edges, 'duplicate_vertex_cells': duplicate_vertices,
        'duplicate_faces': findings.get('duplicate_faces', {}).get('count', 0),
        'loose_vertices': findings.get('loose_vertices', {}).get('count', 0),
        'disconnected_vertex_fans': findings.get('disconnected_vertex_fans', {}).get('count', 0),
        'zero_area_faces': findings.get('zero_area_faces', {}).get('count', 0),
        'nonplanar_faces': findings.get('nonplanar_faces', {}).get('count', 0),
        'maximum_plane_distance_m': nonplanarity_max,
        'concave_faces': findings.get('concave_faces', {}).get('count', 0),
        'warped_quads': findings.get('warped_quads', {}).get('count', 0), 'maximum_quad_warp_degrees': quad_warp_max,
        'oriented_signed_volume_m3': math.fsum(signed_terms),
        'signed_volume_semantics': 'Oriented Blender loop-triangle sum about vertex centroid; physical volume requires closed consistently oriented surface',
        'projected_polygon_area_m2': math.fsum(areas), 'world_bounds_m': world_bounds,
        'source_triangles': {'count': side_counts[3], 'long_thin_count': findings.get('long_thin_source_triangles', {}).get('count', 0),
                             'degenerate_count': source_degenerate_triangles, 'maximum_longest_edge_over_altitude': max(source_triangle_ratios, default=None)},
        'loop_triangulation': {'count': len(triangles), 'long_thin_count': findings.get('long_thin_loop_triangles', {}).get('count', 0),
                               'degenerate_count': render_degenerate, 'maximum_longest_edge_over_altitude': max(render_ratios, default=None),
                               'domain': 'Blender loop_triangles derived for analysis; not source polygons or wire edges'},
        'topology_sha256': topology_hash, 'local_geometry_sha256': geometry_hash,
        'candidate_congruence_sha256': candidate_signature,
    }
    return {'mesh_state': state, 'metrics': metrics, 'findings': findings,
            'thresholds': copy.deepcopy(THRESHOLDS),
            'limitations': ['Diagnostic counts do not imply artistic or manufacturing acceptance',
                            'Duplicate vertices mean identical quantized local cells, not all-pairs proximity',
                            'Face duplicates compare cyclic vertex-index sequences, ignoring winding',
                            'Concavity uses reflex corners; polygon self-intersection and mesh self-intersection are not tested',
                            'Candidate congruence requires equal indexed translation-normalized quantized local geometry; it is not a geometric or quality proof',
                            'Quad warp uses maximum normal bend across both diagonals; concave quads can also flag']}


def _output_path(job_dir, name):
    job = Path(job_dir)
    if not job.is_absolute() or '..' in job.parts or not job.is_dir():
        raise RuntimeFailure('TOPOLOGY_OUTPUT', 'Existing absolute owned job directory required')
    for part in (job, *job.parents):
        if part.is_symlink():
            raise RuntimeFailure('TOPOLOGY_OUTPUT', 'Symbolic-link output directories are refused')
    if not isinstance(name, str) or not name.startswith('topology-') or not name.endswith('.json') or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789-.' for ch in name):
        raise RuntimeFailure('TOPOLOGY_OUTPUT', 'Only internally generated topology JSON names are accepted')
    output = checked_path(job / name, exists=False)
    if output.exists():
        raise RuntimeFailure('OUTPUT_COLLISION', 'Topology output already exists', file=str(output))
    return output


def _write_output(job_dir, name, data, written):
    path = _output_path(job_dir, name)
    encoded = _json_bytes(data)
    if len(encoded) > LIMITS['json_file_bytes'] or written[0] + len(encoded) > LIMITS['aggregate_json_bytes']:
        raise RuntimeFailure('TOPOLOGY_OUTPUT_LIMIT', 'Topology JSON output exceeds per-file or aggregate byte budget', bytes=len(encoded))
    # Exclusive creation prevents replacing existing evidence even under races.
    with open(path, 'xb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    written[0] += len(encoded)
    return descriptor(path)


def _write_geometry(job_dir, stem, data, written):
    """Preserve indexed arrays with bounded, SHA-addressed shards if required.

    The 8 MiB file and 128 MiB aggregate limits remain unchanged. Array indices
    are global in one mesh state; shards only transport consecutive slices.
    """
    encoded=_json_bytes(data)
    if len(encoded)<=LIMITS['json_file_bytes']:
        ref=_write_output(job_dir,stem+'.json',data,written)
        return ref,[{**ref,'kind':'topology_geometry'}]
    metadata=copy.deepcopy({k:v for k,v in data.items() if k not in ('vertices','edges','polygons','loop_triangles','shading','construction')})
    shading=data.get('shading',{})
    metadata['shading']={k:v for k,v in shading.items() if not isinstance(v,list)}
    fields={k:data[k] for k in ('vertices','edges','polygons','loop_triangles')}
    fields.update({'shading.'+k:v for k,v in shading.items() if isinstance(v,list)})
    if data.get('construction') is not None:
        metadata['construction']={k:v for k,v in data['construction'].items() if k!='face_surface_ids'}
        fields['construction.face_surface_ids']=data['construction']['face_surface_ids']
    arrays={};artifacts=[]
    for name,values in fields.items():
        chunks=[];start=0
        while start<len(values):
            count=min(25000,len(values)-start)
            while True:
                value={'schema_version':'1.0','field':name,'start':start,'count':count,'values':values[start:start+count]}
                if len(_json_bytes(value))<=min(2*1024*1024,LIMITS['json_file_bytes']):break
                if count==1:raise RuntimeFailure('TOPOLOGY_OUTPUT_LIMIT','One indexed array record exceeds the shard bound')
                count=max(1,count//2)
            if len(artifacts)>=128:raise RuntimeFailure('TOPOLOGY_OUTPUT_LIMIT','Geometry shard count exceeds 128')
            filename=stem+'-'+name.replace('_','-').replace('.','-')+'-'+str(len(chunks)).zfill(3)+'.json'
            ref=_write_output(job_dir,filename,value,written)
            chunks.append({**ref,'relative_file':filename,'start':start,'count':count})
            artifacts.append({**ref,'kind':'topology_geometry_shard','field':name,'start':start,'count':count})
            start+=count
        arrays[name]={'length':len(values),'chunks':chunks}
    manifest={'schema_version':'1.1','storage':'sharded_indexed_json','metadata':metadata,'arrays':arrays,
              'reconstruction':'Concatenate each field values by start; exact length and SHA required; dotted shading fields belong to metadata.shading',
              'unsharded_sha256':hashlib.sha256(encoded).hexdigest(),'unsharded_bytes':len(encoded)}
    ref=_write_output(job_dir,stem+'.json',manifest,written)
    artifacts.append({**ref,'kind':'topology_geometry_manifest'})
    return {**ref,'storage':'sharded_indexed_json','unsharded_sha256':manifest['unsharded_sha256'],
            'unsharded_bytes':len(encoded),'shards':len(artifacts)-1},artifacts


def read_geometry_export(path):
    """Read a portable bounded geometry export and verify every shard identity."""
    from .io import read_json,verify_descriptor
    path=checked_path(path);manifest=read_json(path)
    if manifest.get('storage')!='sharded_indexed_json':return manifest
    if manifest.get('schema_version')!='1.1' or not isinstance(manifest.get('arrays'),dict):
        raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Unrecognized geometry manifest')
    result=copy.deepcopy(manifest['metadata']);seen=set();total=0;chunk_count=0
    allowed={'vertices','edges','polygons','loop_triangles','construction.face_surface_ids',
        'shading.edge_sharp','shading.face_smooth','shading.polygon_normals','shading.corner_normals',
        'shading.loop_vertex_indices','shading.polygon_loop_starts'}
    for field,entry in manifest['arrays'].items():
        if field not in allowed or type(entry.get('length')) is not int or not 0<=entry['length']<=1200000:
            raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Unknown or unbounded geometry field')
        values=[]
        for chunk in entry['chunks']:
            name=chunk.get('relative_file');chunk_count+=1
            if (not isinstance(name,str) or Path(name).name!=name or not name.startswith('topology-')
                    or not name.endswith('.json') or name in seen or chunk_count>128):
                raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Invalid, duplicate or excessive geometry shard')
            seen.add(name);ref={'file':str(path.parent/name),'sha256':chunk['sha256'],'bytes':chunk['bytes']}
            verify_descriptor(ref,expected=False);total+=ref['bytes']
            if total>LIMITS['aggregate_json_bytes']:raise RuntimeFailure('TOPOLOGY_OUTPUT_LIMIT','Geometry shards exceed aggregate bound')
            row=read_json(path.parent/name)
            if (row.get('field')!=field or row.get('start')!=len(values) or row.get('count')!=len(row.get('values',[]))
                    or row['start']!=chunk.get('start') or row['count']!=chunk.get('count')):
                raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Geometry shard field/index range differs')
            values.extend(row['values'])
        if len(values)!=entry['length']:raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Geometry array has gaps or missing records')
        target=result;parts=field.split('.')
        for part in parts[:-1]:target=target.setdefault(part,{})
        target[parts[-1]]=values
    raw=_json_bytes(result)
    if len(raw)!=manifest['unsharded_bytes'] or hashlib.sha256(raw).hexdigest()!=manifest['unsharded_sha256']:
        raise RuntimeFailure('TOPOLOGY_SHARD_INVALID','Reconstructed geometry identity differs')
    return result


def _identity(obj):
    return {'feature_id': obj.get('hs_feature_id'), 'object_id': obj.get('hs_object_id'),
            'data_id': obj.data.get('hs_data_id'), 'name': obj.name, 'type': obj.type,
            'data_name': obj.data.name, 'data_users': obj.data.users,
            'collections': sorted(collection.name for collection in obj.users_collection)}


def execute(request, job_dir):
    """Inspect an already-opened guarded worker scene; never open/save a blend."""
    request = validate_request(request)
    p = request['params']
    source_before = verify_descriptor(p['source'])
    # Validate destination before importing Blender or examining the scene.
    _output_path(job_dir, 'topology-000-control-stats.json')
    import bpy
    from .ops.geometry import require_si_scene

    started = time.monotonic()
    intersection_cache={}
    source_path = checked_path(bpy.data.filepath)
    opened_before = descriptor(source_path)
    if opened_before['sha256'] != source_before['sha256']:
        raise RuntimeFailure('TOPOLOGY_SOURCE', 'Opened scene does not match the declared source SHA', declared=source_before, opened=opened_before)
    units = require_si_scene()
    scene, layer = bpy.context.scene, bpy.context.view_layer
    objects = list(scene.objects)
    if len(objects) > LIMITS['scene_objects']:
        raise RuntimeFailure('TOPOLOGY_SCENE_LIMIT', 'Scene exceeds topology object budget')
    selected, selection = _resolve_features(objects, p.get('feature_ids'), layer, _render_visible_names(scene.collection), p.get('object_ids'))
    shared = defaultdict(list)
    congruent = defaultdict(list)
    results, outputs, total, written = [], [], {}, [0]
    original_snapshots = {}

    def deadline():
        if time.monotonic() - started > p['wall_seconds']:
            raise RuntimeFailure('TOPOLOGY_DEADLINE', 'Topology wall budget exhausted')

    # Preflight all control domains before expensive evaluation/tessellation.
    control_preflight = {}
    all_control_counts = {}
    for oid, obj in selected.items():
        if obj.mode != 'OBJECT':
            raise RuntimeFailure('TOPOLOGY_TARGET_UNSUPPORTED', 'Topology inspection requires Object mode', object_id=oid)
        control_preflight[oid] = _counts(obj.data)
        _consume_budget(all_control_counts, control_preflight[oid])
        _modifiers(obj)
    for oid, obj in selected.items():
        original_snapshots[oid] = (_hash(_extract(obj.data)), _matrix_values(obj.matrix_world))
    layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    try:
        for object_index, (oid, obj) in enumerate(selected.items()):
            deadline()
            identity = _identity(obj)
            fid = identity['feature_id']
            pointer = obj.data.as_pointer() if hasattr(obj.data, 'as_pointer') else id(obj.data)
            shared[pointer].append({'feature_id': fid, 'object_id': identity['object_id'], 'name': obj.name})
            row = {'identity': identity, 'modifiers': _modifiers(obj), 'states': {}}
            for state in p['mesh_states']:
                evaluated = None
                temporary = None
                try:
                    # Complete the independent qualifier before acquiring this
                    # function's evaluated temporary mesh. Nested to_mesh_clear
                    # on the same evaluated object would invalidate it.
                    quality=None
                    if p['self_intersections']:
                        from .ops.quad_bridge import inspect_object
                        quality=inspect_object(obj,state,intersection_cache)
                    if state == 'control':
                        counts = control_preflight[oid]
                        _consume_budget(total, counts)
                        geometry = _extract(obj.data)  # Original real polygons and edges.
                        matrix = _matrix_values(obj.matrix_world)
                        temporary = obj.data.copy()
                        mesh = temporary  # Tessellation cache never touches source data.
                    else:
                        evaluated = obj.evaluated_get(depsgraph)
                        _counts(evaluated.data)
                        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
                        counts = _counts(mesh)
                        _consume_budget(total, counts)
                        geometry = _extract(mesh)
                        matrix = _matrix_values(evaluated.matrix_world)
                    deadline()
                    mesh.calc_loop_triangles()
                    if len(mesh.loop_triangles) > counts['loop_triangles']:
                        raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT', 'Tessellation exceeded its preflight bound')
                    triangles = [{'vertices': tuple(t.vertices), 'polygon_index': t.polygon_index} for t in mesh.loop_triangles]
                    detail = _analyze(geometry, triangles, matrix, identity, state, deadline)
                    detail['shading'] = _shading_summary(mesh)
                    if p['self_intersections']:
                        detail['quad_quality']=quality
                        detail['self_intersections']=quality.get('self_intersections',{'status':'not_run','reason':'Actual quad/normal gate failed before intersection audit'})
                    detail.update(identity=identity, matrix_world=matrix,
                                  source_sha256=source_before['sha256'], schema_version='1.0',
                                  evaluation='DIRECT_OBJECT_DATA' if state == 'control' else 'VIEWPORT_WITH_RENDER_MATCHING_MODIFIERS')
                    name = 'topology-%03d-%s-stats.json' % (object_index, state)
                    stats_ref = _write_output(job_dir, name, detail, written)
                    outputs.append({**stats_ref, 'kind': 'topology_statistics', 'feature_id': fid, 'object_id': oid, 'mesh_state': state})
                    summary = {'metrics': detail['metrics'], 'statistics': stats_ref, 'matrix_world': matrix,
                               'shading': detail['shading'],
                               'evaluation': detail['evaluation'], 'diagnostic_quality_verdict': 'not_assigned'}
                    if p['self_intersections']:summary['self_intersections']=detail['self_intersections']
                    if p['export_geometry']:
                        export = {'schema_version': '1.0', 'identity': identity, 'mesh_state': state,
                                  'source_sha256': source_before['sha256'], 'coordinate_units': 'm',
                                  'vertices_coordinate_space': 'object_local', 'matrix_world': matrix,
                                  'index_semantics': 'Array positions are original indices in this mesh state; evaluated indices need not map to control',
                                  'domain_semantics': 'Real mesh vertices, edges and polygons; no derived tessellation edges', **geometry,
                                  'loop_triangles': triangles,
                                  'loop_triangles_semantics': 'Derived Blender tessellation for rendering/analysis only; vertices map to this mesh state and polygon_index maps to polygons',
                                  'shading': _shading_export(mesh)}
                        surface=mesh.attributes.get('hs_quad_surface_id')
                        if obj.get('hs_quad_surface_table') and surface is not None:
                            table=json.loads(obj['hs_quad_surface_table'])
                            if len(table)>4096:raise RuntimeFailure('TOPOLOGY_GEOMETRY_LIMIT','Surface provenance table exceeds 4096 roles')
                            export['construction']={'constructor':obj.get('hs_quad_constructor'),
                                'surface_table':table,'face_surface_ids':[int(item.value) for item in surface.data],
                                'construction_sha256':obj.get('hs_quad_construction_sha256')}
                        geom_ref,geometry_outputs = _write_geometry(job_dir, 'topology-%03d-%s-geometry' % (object_index, state), export, written)
                        summary['geometry'] = geom_ref
                        if len(outputs)+len(geometry_outputs)>256:
                            raise RuntimeFailure('TOPOLOGY_OUTPUT_LIMIT','Topology output count exceeds 256 bounded files')
                        outputs.extend({**item,'feature_id':fid,'object_id':oid,'mesh_state':state} for item in geometry_outputs)
                    row['states'][state] = summary
                    congruent[(state, detail['metrics']['candidate_congruence_sha256'])].append({'feature_id': fid, 'object_id': identity['object_id'], 'name': obj.name})
                finally:
                    if temporary is not None:
                        bpy.data.meshes.remove(temporary)
                    if evaluated is not None:
                        evaluated.to_mesh_clear()
            results.append(row)
        deadline()
    finally:
        source_after = verify_descriptor(p['source'])
        opened_after = descriptor(source_path)
        if source_before != source_after or opened_before != opened_after:
            raise RuntimeFailure('TOPOLOGY_SOURCE_CHANGED', 'Saved source identity changed during inspection')
        for oid, obj in selected.items():
            if original_snapshots[oid] != (_hash(_extract(obj.data)), _matrix_values(obj.matrix_world)):
                raise RuntimeFailure('TOPOLOGY_SOURCE_CHANGED', 'Source geometry or transform changed in memory', object_id=oid)

    topology = {'scene': scene.name, 'scene_units': units, 'view_layer': layer.name, 'selection': selection,
                'objects': results, 'limits': copy.deepcopy(LIMITS), 'thresholds': copy.deepcopy(THRESHOLDS),
                'aggregate_analyzed_domains': total, 'output_bytes': written[0],
                'shared_mesh_groups': [{'objects': group, 'basis': 'Actual source mesh datablock identity in this Blender process'} for group in shared.values() if len(group) > 1],
                'candidate_congruent_groups': [{'mesh_state': state, 'signature': sig, 'objects': group,
                                                'basis': 'Indexed translation-normalized quantized local geometry; candidate only, not geometric or artistic proof'}
                                               for (state, sig), group in congruent.items() if len(group) > 1],
                'instances_counted_separately': True}
    return {'outcome': 'topology_inspected', 'operation': 'hardsurface.topology', 'schema_version': '1.0', 'request_id': p['request_id'],
            'source': source_before, 'source_after': source_after, 'opened_source': opened_before,
            'source_sha256': source_before['sha256'], 'source_semantics': 'SAVED_DISK_V1',
            'topology': topology, 'outputs': outputs, 'cpu_threads': p['cpu_threads'], 'wall_seconds': p['wall_seconds'],
            'elapsed_seconds': time.monotonic() - started, 'saved_candidate_modified': False,
            'source_geometry_modified': False, 'source_transforms_modified': False, 'blend_save_performed': False,
            'acceptance': {'inspection_execution': 'pass', 'preservation': 'pass', 'modeling_quality': 'not_assigned', 'visual': 'not_run'}}
