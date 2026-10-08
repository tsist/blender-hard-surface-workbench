"""Bounded HOST observations of existing, semantic native SubD exports.

No Blender, third-party numerical packages, filesystem writes, index pairing,
or shape approval. Every source vertex and triangle centroid is measured to
the complete target union of *closed triangles* (including their edges).
Finite samples are not a continuous Hausdorff distance or a SubD limit proof.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import heapq
import json
import math
import re
import time

from .io import RuntimeFailure
from .structure_kernel import fingerprint
from .subdivision_source import (DISPLAY_PROPERTIES, GEOMETRY_PROPERTIES,
                                 REQUIRED_PROPERTIES, SPARSE_SOURCE_SCHEMA)

SCHEMA_VERSION = 'subdivision-comparison/1.0'
DEFAULT_LIMITS = {
    'max_vertices': 300000, 'max_polygons': 250000, 'max_triangles': 500000,
    'max_parents': 10000, 'max_samples': 1600000,
    'max_triangle_tests': 50000000, 'max_bvh_nodes': 2000000,
    'max_domain_memberships': 2000000, 'max_report_regions': 30000,
    'max_wall_seconds': 300.0, 'max_abs_coordinate_mm': 1000000000.0,
}
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _fail(message, **details):
    raise RuntimeFailure('SUBDIVISION_COMPARE_INVALID', message, **details)


def _export_hash(value):
    # Match subdivision._hash, including its ASCII/signed-zero serialization.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _sha(value, field):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail('Expected SHA-256 evidence', field=field)
    return value


def _integer(value, field, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        _fail('Expected bounded actual integer', field=field)
    return value


def _number(value, field):
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail('Expected finite actual number', field=field)
    return float(value)


def _label(value, field):
    if not isinstance(value, str) or not value or len(value) > 1024:
        _fail('Expected nonempty bounded semantic label', field=field)
    return value


def _list(value, field, minimum=0, maximum=None):
    if not isinstance(value, list) or len(value) < minimum or (maximum is not None and len(value) > maximum):
        _fail('Array cardinality is invalid', field=field)
    return value


def _mapping(value, field):
    if not isinstance(value, dict):
        _fail('Expected object', field=field)
    return value


class _Budget:
    def __init__(self, limits, check_cancel):
        self.limits = dict(DEFAULT_LIMITS)
        if limits is not None:
            if not isinstance(limits, dict) or set(limits) - set(self.limits):
                _fail('Unknown comparison resource limits')
            self.limits.update(limits)
        for key, value in self.limits.items():
            if key in ('max_wall_seconds', 'max_abs_coordinate_mm'):
                if _number(value, key) <= 0:
                    _fail('Resource limits must be positive', field=key)
            else:
                _integer(value, key, 1)
        if self.limits['max_abs_coordinate_mm'] > 1e30:
            _fail('Coordinate bound is outside safe floating-point arithmetic')
        if check_cancel is not None and not callable(check_cancel):
            _fail('check_cancel must be a callable or None')
        self.check_cancel = check_cancel
        self.started = time.monotonic()
        self.counts = Counter()
        self.ticks = 0
        self.check()

    def check(self):
        if self.check_cancel is not None and self.check_cancel():
            raise RuntimeFailure('SUBDIVISION_COMPARE_CANCELLED', 'Comparison cancelled; no completed comparison report')
        if time.monotonic() - self.started > self.limits['max_wall_seconds']:
            raise RuntimeFailure('SUBDIVISION_COMPARE_LIMIT', 'Comparison wall-time budget exhausted', resource='max_wall_seconds')

    def tick(self, count=1):
        self.ticks += count
        if self.ticks >= 128:
            self.ticks = 0
            self.check()

    def charge(self, resource, count=1):
        self.counts[resource] += count
        if self.counts[resource] > self.limits['max_' + resource]:
            raise RuntimeFailure('SUBDIVISION_COMPARE_LIMIT', 'Comparison resource budget exhausted',
                                 resource='max_' + resource, observed=self.counts[resource])
        self.tick(count)

    def size(self, resource, count):
        if count > self.limits['max_' + resource]:
            raise RuntimeFailure('SUBDIVISION_COMPARE_LIMIT', 'Geometry exceeds comparison budget',
                                 resource='max_' + resource, observed=count)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _point_triangle_d2(p, triangle, *, with_point=False):
    """Closest point on a closed triangle, never on its unbounded plane."""
    a, b, c = triangle
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    normal = _cross(ab, ac)
    nn = _dot(normal, normal)
    # Cross-product barycentrics avoid cancellation in uu*vv-uv*uv.
    beta = _dot(_cross(ap, ac), normal) / nn
    gamma = _dot(_cross(ab, ap), normal) / nn
    best = (_dot(ap, normal) ** 2 / nn
            if beta >= 0 and gamma >= 0 and beta + gamma <= 1 else math.inf)
    closest = (tuple(p[i] - normal[i] * (_dot(ap, normal) / nn) for i in range(3))
               if with_point and math.isfinite(best) else None)
    for x, y in ((a, b), (b, c), (c, a)):
        edge, offset = _sub(y, x), _sub(p, x)
        fraction = min(1.0, max(0.0, _dot(offset, edge) / _dot(edge, edge)))
        delta = tuple(offset[i] - fraction * edge[i] for i in range(3))
        candidate = _dot(delta, delta)
        if candidate < best:
            best = candidate
            if with_point:
                closest = tuple(x[i] + fraction * edge[i] for i in range(3))
    return (max(0.0, best), closest) if with_point else max(0.0, best)


def _profile(source):
    profile = _mapping(source.get('actual_source_modifier'), 'actual_source_modifier')
    allowed = set(GEOMETRY_PROPERTIES) | set(DISPLAY_PROPERTIES) | {'name', 'type'}
    if set(profile) - allowed or not REQUIRED_PROPERTIES <= set(profile):
        _fail('Incomplete or unrecognized source modifier profile')
    for key, value in profile.items():
        if type(value) not in (str, bool, int, float) or (type(value) is float and not math.isfinite(value)):
            _fail('Modifier settings must be finite scalars', field=key)
    if (profile.get('type') != 'SUBSURF' or profile.get('subdivision_type') != 'CATMULL_CLARK'
            or profile.get('show_viewport') is not True or profile.get('show_render') is not True
            or profile.get('use_adaptive_subdivision', False) is not False
            or profile.get('use_custom_normals') is not False):
        _fail('Only exported fixed, enabled Catmull-Clark profiles are supported')
    for key in ('levels', 'render_levels', 'quality'):
        _integer(profile.get(key), 'modifier.' + key, 0)
    for key, value in profile.items():
        if (key.startswith(('use_', 'show_')) or key in DISPLAY_PROPERTIES) and type(value) is not bool:
            _fail('Modifier flags must be actual booleans', field=key)
    return {key: value for key, value in profile.items() if key not in DISPLAY_PROPERTIES}


class _Mesh:
    def __init__(self, data, budget):
        self.data = _mapping(data, 'geometry')
        if (data.get('schema_version') != '1.0' or data.get('kind') != 'subdivision_evaluated_geometry'
                or data.get('derived_triangles_included') is not True
                or data.get('world_vertices_mm_coordinate_space') != 'world'
                or data.get('world_vertices_mm_coordinate_units') != 'mm'
                or data.get('vertices_coordinate_space') != 'object_local'
                or data.get('vertices_coordinate_units') != 'm'):
            _fail('Expected the existing complete native world-mm geometry export schema')
        self.level = _integer(data.get('level'), 'level', 0, 3)
        self.source_sha256 = _sha(data.get('source_sha256'), 'source_sha256')
        vertices = _list(data.get('world_vertices_mm'), 'world_vertices_mm', 3)
        polygons = _list(data.get('polygons'), 'polygons', 1)
        triangles = _list(data.get('loop_triangles'), 'loop_triangles', 1)
        for name, rows in (('vertices', vertices), ('polygons', polygons), ('triangles', triangles)):
            budget.size(name, len(rows))
        limit = budget.limits['max_abs_coordinate_mm']
        self.vertices = []
        for row in vertices:
            row = _list(row, 'world vertex', 3, 3)
            value = tuple(_number(x, 'world vertex') for x in row)
            if any(abs(x) > limit for x in value):
                _fail('Coordinate exceeds safe configured bound')
            self.vertices.append(value)
            budget.tick()
        n = len(vertices)
        matrix = _list(data.get('matrix_world'), 'matrix_world', 4, 4)
        matrix = [tuple(_number(x, 'matrix_world') for x in _list(row, 'matrix row', 4, 4)) for row in matrix]
        if matrix[3] != (0., 0., 0., 1.):
            _fail('Expected affine matrix_world')
        local = _list(data.get('vertices'), 'vertices', n, n)
        for index, row in enumerate(local):
            xyz = tuple(_number(x, 'local vertex') for x in _list(row, 'local vertex', 3, 3))
            expected = tuple((sum(matrix[i][j] * xyz[j] for j in range(3)) + matrix[i][3]) * 1000 for i in range(3))
            # Blender matrix multiplication uses float32 in supported exports.
            if any(not math.isfinite(x) or not math.isclose(x, y, rel_tol=2e-6, abs_tol=1e-6)
                   for x, y in zip(expected, self.vertices[index])):
                _fail('World-mm coordinates disagree with local coordinates and matrix', vertex=index)
            budget.tick()
        self.polygons = []
        edge_faces = defaultdict(list)
        used = set()
        for fi, face in enumerate(polygons):
            # sparse-source-regions/1.0 describes fixed all-quad descendants.
            face = _list(face, 'polygon', 4, 4)
            face = tuple(_integer(v, 'polygon vertex', 0, n - 1) for v in face)
            if len(set(face)) != 4:
                _fail('Repeated polygon vertex', polygon=fi)
            used.update(face)
            self.polygons.append(face)
            for a, b in zip(face, face[1:] + face[:1]):
                edge_faces[tuple(sorted((a, b)))].append(fi)
            budget.tick()
        if used != set(range(n)):
            _fail('Whole-geometry export contains isolated or uncovered vertices')
        self.edge_faces = edge_faces
        directed_edges = Counter((a, b) for face in self.polygons
                                 for a, b in zip(face, face[1:] + face[:1]))
        boundary_count = sum(len(faces) == 1 for faces in edge_faces.values())
        nonmanifold_count = sum(len(faces) > 2 for faces in edge_faces.values())
        orientation_conflicts = sum(len(faces) == 2 and
                                    (directed_edges[(a, b)] != 1 or directed_edges[(b, a)] != 1)
                                    for (a, b), faces in edge_faces.items())
        if nonmanifold_count or orientation_conflicts:
            _fail('Semantic source descendants require manifold, consistently oriented polygon edges',
                  nonmanifold_edges=nonmanifold_count, orientation_conflicts=orientation_conflicts)
        self.topology = {'boundary_edge_count': boundary_count,
                         'nonmanifold_edge_count': nonmanifold_count,
                         'orientation_conflict_count': orientation_conflicts,
                         'closed_by_edge_incidence': boundary_count == 0,
                         'scope': 'Actual polygon edge incidence; not a self-intersection or solid-volume proof'}
        edge_rows = _list(data.get('edges'), 'edges', 1, 4 * len(polygons))
        edges = set()
        for row in edge_rows:
            row = _list(row, 'edge', 2, 2)
            edge = tuple(sorted(_integer(v, 'edge vertex', 0, n - 1) for v in row))
            if edge[0] == edge[1] or edge in edges:
                _fail('Degenerate or duplicate real edge')
            edges.add(edge)
            budget.tick()
        if edges != set(edge_faces):
            _fail('Real edges must exactly cover polygon boundaries')
        if _sha(data.get('local_geometry_sha256'), 'local_geometry_sha256') != _export_hash(
                {key: data[key] for key in ('vertices', 'edges', 'polygons')}):
            _fail('Local geometry digest does not match exported arrays')
        self.triangles, self.triangle_faces, self.areas, self.centroids = [], [], [], []
        by_face = defaultdict(list)
        for row in triangles:
            row = _mapping(row, 'loop triangle')
            if set(row) != {'vertices', 'polygon_index'}:
                _fail('Loop triangle must use the native vertices/polygon_index schema')
            indices = tuple(_integer(v, 'triangle vertex', 0, n - 1)
                            for v in _list(row['vertices'], 'triangle vertices', 3, 3))
            fi = _integer(row['polygon_index'], 'triangle polygon_index', 0, len(polygons) - 1)
            if len(set(indices)) != 3 or not set(indices) <= set(self.polygons[fi]):
                _fail('Triangle must contain three distinct vertices from its real polygon')
            tri = tuple(self.vertices[v] for v in indices)
            ab, ac = _sub(tri[1], tri[0]), _sub(tri[2], tri[0])
            cross = _cross(ab, ac)
            nn = _dot(cross, cross)
            longest2 = max(_dot(ab, ab), _dot(ac, ac), _dot(_sub(tri[2], tri[1]), _sub(tri[2], tri[1])))
            if not math.isfinite(nn) or nn <= (1e-14 * longest2) ** 2:
                _fail('Degenerate or numerically unresolved triangle')
            self.triangles.append(tri)
            self.triangle_faces.append(fi)
            self.areas.append(math.sqrt(nn) / 2)
            self.centroids.append(tuple(sum(v[i] / 3 for v in tri) for i in range(3)))
            by_face[fi].append(indices)
            budget.tick()
        for fi, polygon in enumerate(self.polygons):
            rows = by_face[fi]
            if len(rows) != 2 or len({tuple(sorted(row)) for row in rows}) != 2:
                _fail('Every real quad requires exactly two distinct covering triangles', polygon=fi)
            oriented = Counter((a, b) for tri in rows for a, b in zip(tri, tri[1:] + tri[:1]))
            boundary = Counter(zip(polygon, polygon[1:] + polygon[:1]))
            remainder = oriented - boundary
            if not boundary <= oriented or len(remainder) != 2 or any(
                    value != 1 or remainder[(b, a)] != 1 for (a, b), value in remainder.items()):
                _fail('Triangles must tessellate the complete oriented parent polygon', polygon=fi)
            budget.tick()
        self._semantics(data.get('bound_semantic_regions'), budget)

    def _semantics(self, semantics, budget):
        s = self.semantics = _mapping(semantics, 'bound_semantic_regions')
        if s.get('schema_version') != 'sparse-source-regions/1.0' or s.get('source_schema') != SPARSE_SOURCE_SCHEMA:
            _fail('Unsupported bound semantic export schema')
        count = self.parent_count = _integer(s.get('source_face_count'), 'source_face_count', 1)
        budget.size('parents', count)
        self.parent_ids = _list(s.get('source_face_ids_by_parent_slot'), 'source face IDs', count, count)
        if len(set(_label(v, 'source face ID') for v in self.parent_ids)) != count:
            _fail('Source face IDs must be unique within this model')
        provenance = _list(s.get('source_provenance_by_parent_slot'), 'parent provenance', count, count)
        self.roles = [_label(_mapping(row, 'parent provenance').get('surface_role'), 'surface_role') for row in provenance]
        source_labels = _list(s.get('source_surface_labels_by_parent_slot'), 'source surface labels', count, count)
        for label in source_labels:
            _integer(label, 'source surface label', 0)
        self.parents = _list(s.get('evaluated_face_parent_slots'), 'parent slots', len(self.polygons), len(self.polygons))
        for parent in self.parents:
            _integer(parent, 'parent slot', 0, count - 1)
        factor = 4 ** self.level
        if Counter(self.parents) != Counter({i: factor for i in range(count)}):
            _fail('Every source parent must have exactly 4^level actual descendants')
        self._parent_patches(budget)
        labels = _list(s.get('evaluated_face_surface_labels'), 'evaluated surface labels', len(self.parents), len(self.parents))
        for parent, label in zip(self.parents, labels):
            if _integer(label, 'evaluated surface label', 0) != source_labels[parent]:
                _fail('Evaluated semantic label disagrees with its own source parent')
        transport = _mapping(s.get('transport'), 'transport')
        source = _mapping(transport.get('source'), 'transport.source')
        for key in ('level', 'evaluated_faces', 'expected_descendants_per_source_face', 'evaluated_bore_faces'):
            _integer(transport.get(key), 'transport.' + key, 0)
        _integer(source.get('source_face_count'), 'transport.source.source_face_count', 1)
        if (transport.get('status') != 'validated' or type(transport.get('level')) is not int
                or transport['level'] != self.level or transport.get('source_schema') != SPARSE_SOURCE_SCHEMA
                or transport.get('evaluated_faces') != len(self.polygons)
                or transport.get('expected_descendants_per_source_face') != factor
                or transport.get('all_source_face_descendant_counts_match') is not True
                or transport.get('parent_surface_labels_match') is not True
                or source.get('identity_binding_status') != 'bound'
                or source.get('external_registry_verified') is not True
                or source.get('full_per_face_provenance_bound') is not True
                or source.get('source_face_count') != count or source.get('source_schema') != SPARSE_SOURCE_SCHEMA):
            _fail('Incomplete or inconsistent actual parent transport evidence')
        topology = _mapping(transport.get('parent_patch_topology'), 'parent_patch_topology')
        for key in ('connected_disk_regions', 'boundary_edges_per_source_quad'):
            _integer(topology.get(key), 'parent_patch_topology.' + key, 1)
        if (topology.get('status') != 'pass' or topology.get('connected_disk_regions') != count
                or topology.get('oriented_source_neighbour_cycles_match') is not True
                or topology.get('boundary_edges_per_source_quad') != 4 * 2 ** self.level):
            _fail('Missing complete source-parent topology transport witness')
        expected = [('evaluated_parent_slots_sha256', self.parents), ('evaluated_surface_labels_sha256', labels)]
        for key, value in expected:
            if _sha(transport.get(key), key) != fingerprint(value):
                _fail('Semantic transport digest mismatch', field=key)
        for key, value in [('parent_face_ids_by_slot_sha256', self.parent_ids),
                           ('parent_provenance_by_slot_sha256', provenance),
                           ('parent_surface_by_slot_sha256', source_labels)]:
            if _sha(source.get(key), key) != fingerprint(value):
                _fail('Source parent evidence digest mismatch', field=key)
        binding = _mapping(s.get('source_binding'), 'source_binding')
        if not binding or fingerprint(binding) != fingerprint(source.get('native_binding')):
            _fail('Semantic export and transport have different source bindings')
        if _sha(s.get('source_evidence_sha256'), 'source_evidence_sha256') != _export_hash(source):
            _fail('Source evidence digest differs from exported transport source')
        bore_parents = _list(s.get('bore_source_parent_slots'), 'bore parents')
        for parent in bore_parents:
            _integer(parent, 'bore parent', 0, count - 1)
        if len(set(bore_parents)) != len(bore_parents):
            _fail('Duplicate bore parent')
        bore_set = set(bore_parents)
        bore_faces = _list(s.get('bore_evaluated_face_indices'), 'bore faces')
        if any(type(index) is not int for index in bore_faces) or bore_faces != [i for i, parent in enumerate(self.parents) if parent in bore_set]:
            _fail('Bore domain must contain exactly every declared bore-parent descendant')
        if (transport.get('evaluated_bore_faces') != len(bore_faces)
                or transport.get('bore_parent_membership_matches') is not True
                or _sha(transport.get('bore_face_indices_sha256'), 'bore_face_indices_sha256') != fingerprint(bore_faces)):
            _fail('Bore transport evidence differs from complete exported domain')
        self.profile = _profile(source)
        self.triangle_parents = [self.parents[fi] for fi in self.triangle_faces]
        self.loops = _list(s.get('source_control_loops'), 'source_control_loops', 0, budget.limits['max_report_regions'])
        loop_roles = set()
        for row in self.loops:
            row = _mapping(row, 'control loop')
            role = _label(row.get('role'), 'control loop role')
            if role in loop_roles:
                _fail('Duplicate source control loop role')
            loop_roles.add(role)
            indices = _list(row.get('vertex_indices'), 'control loop vertices', 3, budget.limits['max_vertices'])
            if any(type(v) is not int or v < 0 for v in indices) or len(set(indices)) != len(indices):
                _fail('Source control loop must contain distinct actual vertex indices')
            budget.tick(len(indices))
        budget.check()

    def _parent_patches(self, budget):
        """Verify complete connected disk domains instead of trusting flags."""
        groups = defaultdict(set)
        vertices = defaultdict(set)
        edges = defaultdict(set)
        boundary = Counter()
        adjacency = defaultdict(set)
        for fi, parent in enumerate(self.parents):
            groups[parent].add(fi)
            vertices[parent].update(self.polygons[fi])
        for edge, faces in self.edge_faces.items():
            parents = {self.parents[fi] for fi in faces}
            for parent in parents:
                edges[parent].add(edge)
                if len(faces) == 1 or len(parents) == 2:
                    boundary[parent] += 1
            if len(faces) == 2 and len(parents) == 1:
                a, b = faces
                adjacency[a].add(b)
                adjacency[b].add(a)
            budget.tick()
        for parent, members in groups.items():
            seen = set()
            pending = [next(iter(members))]
            while pending:
                face = pending.pop()
                if face in seen:
                    continue
                seen.add(face)
                pending.extend(adjacency[face] - seen)
                budget.tick()
            if (seen != members or len(vertices[parent]) - len(edges[parent]) + len(members) != 1
                    or boundary[parent] != 4 * 2 ** self.level):
                _fail('Each own-parent region must be a complete connected quad-derived disk', parent_slot=parent)


class _TriangleIndex:
    """Median-split AABB BVH with conservative floating-point box bounds."""
    def __init__(self, mesh, budget):
        self.mesh, self.budget, self.nodes = mesh, budget, []
        self.bounds = [(tuple(min(v[i] for v in tri) for i in range(3)),
                        tuple(max(v[i] for v in tri) for i in range(3))) for tri in mesh.triangles]
        self.root = self._build(list(range(len(mesh.triangles))))

    def _build(self, indices):
        self.budget.charge('bvh_nodes')
        low = tuple(min(self.bounds[j][0][i] for j in indices) for i in range(3))
        high = tuple(max(self.bounds[j][1][i] for j in indices) for i in range(3))
        node = len(self.nodes)
        self.nodes.append(None)
        if len(indices) <= 8:
            self.nodes[node] = (low, high, tuple(indices), None)
        else:
            axis = max(range(3), key=lambda i: high[i] - low[i])
            indices.sort(key=lambda j: self.mesh.centroids[j][axis])
            split = len(indices) // 2
            children = (self._build(indices[:split]), self._build(indices[split:]))
            self.nodes[node] = (low, high, None, children)
        self.budget.check()
        return node

    @staticmethod
    def _bound(point, low, high):
        # Round outwards: preserve candidates on boundaries under roundoff.
        total = 0.0
        for i in range(3):
            delta = max(low[i] - point[i], point[i] - high[i], 0.0)
            delta = max(0.0, math.nextafter(delta, -math.inf))
            term = math.nextafter(delta * delta, -math.inf) if delta else 0.0
            total = max(0.0, math.nextafter(total + term, -math.inf))
        return total

    def distance(self, point, *, with_witness=False):
        best = math.inf
        best_triangle = None
        pending = [(0.0, self.root)]
        while pending:
            bound, index = heapq.heappop(pending)
            if bound > best:
                continue
            low, high, triangles, children = self.nodes[index]
            if triangles is not None:
                for ti in triangles:
                    self.budget.charge('triangle_tests')
                    candidate = _point_triangle_d2(point, self.mesh.triangles[ti])
                    if candidate < best:
                        best, best_triangle = candidate, ti
            else:
                for child in children:
                    lo, hi, _, _ = self.nodes[child]
                    bound = self._bound(point, lo, hi)
                    if bound <= best:
                        heapq.heappush(pending, (bound, child))
            self.budget.tick()
        if not math.isfinite(best):
            _fail('No finite whole-target triangle distance was obtained')
        if with_witness:
            self.budget.charge('triangle_tests')
            _, closest = _point_triangle_d2(point, self.mesh.triangles[best_triangle], with_point=True)
            polygon = self.mesh.triangle_faces[best_triangle]
            parent = self.mesh.parents[polygon]
            return {'distance_mm': math.sqrt(best), 'target_triangle_index': best_triangle,
                    'target_polygon_index': polygon, 'target_parent_slot': parent,
                    'target_source_face_id': self.mesh.parent_ids[parent],
                    'target_triangle_world_mm': [list(v) for v in self.mesh.triangles[best_triangle]],
                    'closest_target_point_world_mm': list(closest),
                    'nearest_separation_vector_mm': [closest[i] - point[i] for i in range(3)],
                    'semantics': 'Nearest surface pair only; the separation vector is not a corresponding material-point displacement'}
        return math.sqrt(best)


class _Stats:
    def __init__(self, tolerance):
        self.count = self.above = 0
        self.weight = self.total = self.squared = self.maximum = 0.0
        self.tolerance = tolerance

    def add(self, distance, weight=1.0):
        self.count += 1
        self.weight += weight
        self.total += distance * weight
        self.squared += distance * distance * weight
        self.maximum = max(self.maximum, distance)
        self.above += self.tolerance is not None and distance > self.tolerance

    def merge(self, other):
        self.count += other.count
        self.weight += other.weight
        self.total += other.total
        self.squared += other.squared
        self.maximum = max(self.maximum, other.maximum)
        self.above += other.above

    def report(self, weighting):
        result = {'status': 'observed_finite_samples' if self.count else 'empty_domain',
                  'samples': self.count, 'weighting': weighting,
                  'maximum_mm': self.maximum if self.count else None,
                  'mean_mm': self.total / self.weight if self.weight else None,
                  'rms_mm': math.sqrt(self.squared / self.weight) if self.weight else None}
        if weighting == 'triangle_area':
            result['sampled_triangle_area_mm2'] = self.weight
        if self.tolerance is not None:
            result['samples_above_reference_tolerance'] = self.above
        return result


def _domains(mesh, source, budget):
    domains = defaultdict(set)
    for parent, role in enumerate(mesh.roles):
        domains['surface:' + role].add(parent)
    budget.charge('domain_memberships', mesh.parent_count)
    budget.charge('report_regions', mesh.parent_count + len(domains))

    def add_domain(label, selected):
        budget.charge('domain_memberships', len(selected))
        budget.charge('report_regions')
        domains[label] = selected

    status = {'status': 'unavailable', 'reason': 'Own-model L0 geometry was not supplied',
              'pole_neighborhoods_complete': False}
    if source is not None:
        if source.level != 0 or source.source_sha256 != mesh.source_sha256:
            _fail('Neighborhood source must be this geometry source at L0')
        for key in ('source_binding', 'source_face_ids_by_parent_slot', 'source_provenance_by_parent_slot',
                    'source_surface_labels_by_parent_slot', 'source_control_loops'):
            if fingerprint(source.semantics[key]) != fingerprint(mesh.semantics[key]):
                _fail('Neighborhood L0 semantic evidence differs from its evaluated export', field=key)
        if fingerprint(source.profile) != fingerprint(mesh.profile):
            _fail('Neighborhood L0 source modifier profile differs')
        incident = defaultdict(set)
        adjacent = defaultdict(set)
        valence = Counter()
        for fi, face in enumerate(source.polygons):
            for vi in face:
                incident[vi].add(fi)
        for (a, b), faces in source.edge_faces.items():
            valence[a] += 1
            valence[b] += 1
            for fi in faces:
                adjacent[fi].update(other for other in faces if other != fi)

        def ring(vertices):
            faces = set()
            for vertex in vertices:
                faces.update(incident[vertex])
                budget.tick()
            grown = set(faces)
            for fi in faces:
                grown.update(adjacent[fi])
                budget.tick()
            return {source.parents[fi] for fi in grown}

        poles = []
        all_poles = set()
        for vi in range(len(source.vertices)):
            if valence[vi] != 4:
                label = 'pole:source_vertex:' + str(vi)
                add_domain(label, ring((vi,)))
                all_poles.update(domains[label])
                poles.append({'label': label, 'source_vertex_index': vi, 'valence': valence[vi],
                              'world_mm': list(source.vertices[vi])})
            budget.tick()
        if poles:
            add_domain('all_poles_incident_plus_one_face_ring', all_poles)
        for row in source.loops:
            indices = row['vertex_indices']
            if any(v >= len(source.vertices) for v in indices) or any(
                    tuple(sorted((a, b))) not in source.edge_faces
                    for a, b in zip(indices, indices[1:] + indices[:1])):
                _fail('Control-loop label is not a real closed source edge cycle')
            add_domain('loop:' + row['role'], ring(indices))
        status = {'status': 'derived_from_own_L0', 'pole_neighborhoods_complete': True,
                  'poles': poles, 'source_level': 0,
                  'pole_selection': 'Actual source vertex edge valence differs from four; on open surfaces this also includes boundary vertices',
                  'semantics': 'Incident source faces plus one edge-adjacent face ring; all own-parent descendants retained; no cross-model pole-index pairing'}
    return domains, status


def _direction(source, target_index, domains, neighborhoods, budget, tolerance):
    vertex_stats, centroid_stats = _Stats(tolerance), _Stats(tolerance)
    parents = [_Stats(tolerance) for _ in range(source.parent_count)]
    maximum_vertex = maximum_centroid = None
    for vi, point in enumerate(source.vertices):
        distance = target_index.distance(point)
        if maximum_vertex is None or distance > vertex_stats.maximum:
            maximum_vertex = vi
        vertex_stats.add(distance)
        budget.tick()
    for ti, point in enumerate(source.centroids):
        distance = target_index.distance(point)
        if maximum_centroid is None or distance > centroid_stats.maximum:
            maximum_centroid = ti
        area = source.areas[ti]
        centroid_stats.add(distance, area)
        parents[source.triangle_parents[ti]].add(distance, area)
        budget.tick()
    parent_rows = [{'parent_slot': parent, 'source_face_id': source.parent_ids[parent],
                    'surface_role': source.roles[parent], 'evaluated_faces': 4 ** source.level,
                    'triangle_centroids': stats.report('triangle_area')}
                   for parent, stats in enumerate(parents)]
    domain_rows = {}
    for label, selected in sorted(domains.items()):
        stats = _Stats(tolerance)
        for parent in sorted(selected):
            stats.merge(parents[parent])
            budget.tick()
        domain_rows[label] = {'own_parent_slots': sorted(selected), 'parent_count': len(selected),
                              'triangle_centroids': stats.report('triangle_area'),
                              'target_domain': 'whole_target_geometry'}
    def witness(index, points, kind):
        point = points[index]
        result = {'source_sample_kind': kind, 'source_sample_index': index,
                  'source_world_mm': list(point), **target_index.distance(point, with_witness=True)}
        if kind == 'triangle_centroid':
            parent = source.triangle_parents[index]
            result.update(source_parent_slot=parent, source_face_id=source.parent_ids[parent])
        return result
    return {'vertices': {**vertex_stats.report('equal_samples'),
                         'maximum_witness': witness(maximum_vertex, source.vertices, 'vertex')},
            'triangle_centroids': {**centroid_stats.report('triangle_area'),
                                   'maximum_witness': witness(maximum_centroid, source.centroids, 'triangle_centroid')},
            'coverage': {'all_vertices_sampled': True, 'all_triangle_centroids_sampled': True,
                         'all_polygons_covered_by_triangles': True, 'all_source_parents_sampled': True,
                         'source_parents': source.parent_count, 'sampled_source_parents': len(parents),
                         'target_triangles': len(target_index.mesh.triangles), 'target_scope': 'whole_geometry',
                         'source_topology': dict(source.topology),
                         'target_topology': dict(target_index.mesh.topology)},
            'parent_regions': parent_rows, 'semantic_domains': domain_rows,
            'pole_and_loop_neighborhoods': neighborhoods}


def compare_exports(before_geometry, after_geometry, *, before_source_geometry=None,
                    after_source_geometry=None, limits=None, reference_tolerance_mm=None,
                    check_cancel=None):
    """Compare parsed existing same-level native exports, without side effects.

    Optional source geometries must be the corresponding own-model L0 exports.
    All samples run, or RuntimeFailure is raised: budget exhaustion/cancellation
    never returns partial coverage as a completed comparison. ``check_cancel``
    is called throughout validation/indexing/sampling; a truthy return cancels,
    and an exception propagates so an owning job can enforce its own deadline.
    The reference tolerance only counts observations; it is never a gate.
    """
    budget = _Budget(limits, check_cancel)
    tolerance = None
    if reference_tolerance_mm is not None:
        tolerance = _number(reference_tolerance_mm, 'reference_tolerance_mm')
        if tolerance < 0:
            _fail('Reference tolerance must be nonnegative')
    try:
        before, after = _Mesh(before_geometry, budget), _Mesh(after_geometry, budget)
        if before.level != after.level:
            _fail('Before and after must be evaluated at the same level')
        if fingerprint(before.profile) != fingerprint(after.profile):
            _fail('Before and after require the same complete source modifier profile')
        sample_count = sum(len(mesh.vertices) + len(mesh.triangles) for mesh in (before, after))
        budget.charge('samples', sample_count)
        before_l0 = (_Mesh(before_source_geometry, budget) if before_source_geometry is not None
                     else before if before.level == 0 else None)
        after_l0 = (_Mesh(after_source_geometry, budget) if after_source_geometry is not None
                    else after if after.level == 0 else None)
        before_domains, before_neighborhoods = _domains(before, before_l0, budget)
        after_domains, after_neighborhoods = _domains(after, after_l0, budget)
        before_index, after_index = _TriangleIndex(before, budget), _TriangleIndex(after, budget)
        directions = {
            'before_to_after': _direction(before, after_index, before_domains, before_neighborhoods, budget, tolerance),
            'after_to_before': _direction(after, before_index, after_domains, after_neighborhoods, budget, tolerance),
        }
        budget.check()
        maximum = max(row[kind]['maximum_mm'] for row in directions.values()
                      for kind in ('vertices', 'triangle_centroids'))
        return {'schema_version': SCHEMA_VERSION, 'operation': 'hardsurface.subdivision.compare',
                'execution': {'status': 'succeeded', 'coverage': 'complete_for_declared_finite_samples'},
                'shape_review': {'status': 'pending', 'shape_preservation': 'not_asserted',
                                 'reference_tolerance_is_acceptance_gate': False},
                'level': before.level, 'coordinate_space': 'world', 'coordinate_units': 'mm',
                'evaluation_profile_sha256': fingerprint(before.profile),
                'source_sha256': {'before': before.source_sha256, 'after': after.source_sha256},
                'method': {'samples': 'all_vertices_and_all_native_triangle_centroids',
                           'distance_target': 'complete_union_of_closed_native_triangles',
                           'acceleration': 'dependency_free_AABB_BVH', 'index_correspondence_used': False,
                           'continuous_hausdorff': False, 'subdivision_limit_surface_certified': False,
                           'parent_slots_correspond_across_models': False},
                'reference_tolerance_mm': tolerance, 'sampled_bidirectional_maximum_mm': maximum,
                'directions': directions, 'limits': dict(budget.limits),
                'resources': {**dict(budget.counts), 'elapsed_seconds': time.monotonic() - budget.started},
                'limitations': ['Finite vertex/centroid samples do not bound unsampled surface interiors or the continuous Hausdorff distance.',
                                'Semantic lineage is validated for internal consistency against supplied export evidence; this HOST comparison does not independently reopen or authenticate native Blender state.',
                                'Distances target the whole mesh; semantic regions and pole neighborhoods identify each side\'s own sample domain, not paired faces or poles.',
                                'Execution success and any reference tolerance do not approve shape preservation, dimensions, visual quality, self-intersections, or production readiness.']}
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        _fail('Malformed or unsupported geometry/semantic evidence', reason=str(exc))
