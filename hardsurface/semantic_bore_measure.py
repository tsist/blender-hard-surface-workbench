"""Actual-topology validation for an explicitly supplied sharp-bore wall.

Indices select candidates; they do not establish authored/source provenance.
This bounded host checker never reconstructs membership from coordinate proximity.
Source/evaluated lineage must be independently verified by the native caller.
"""
from collections import defaultdict
import hashlib
import json
import math

from .io import RuntimeFailure

SEMANTIC_BORE_PROFILE = 'semantic_bore_v1'
LEGACY_MEASUREMENT_PROFILE = 'legacy_geometry_diagnostic_v0'
# Preserve the previous finite vertex-station coincidence epsilon. This is not
# the dimensional acceptance tolerance, nor a wall classification threshold.
STATION_EPSILON_MM = 1e-4


def _fail(message, **details):
    raise RuntimeFailure('SUBD_BORE_MEMBERSHIP_INVALID', message, **details)


def _sha(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _length(v):
    return math.sqrt(sum(x*x for x in v))


def semantic_bore_samples(vertices, faces, p, bore_face_indices):
    """Return actual wall IDs and two directed boundary cycles, or fail closed.

    The selected region must be an oriented manifold quad annulus embedded in a
    closed, consistently wound mesh. Every triangle interpretation must face
    inward within a 45-degree radial cone, excluding cap/support faces even if
    infinitesimally nonplanar. This cone is a domain/regularity condition, not a
    relaxation of the caller's unchanged radial/surface dimensional tolerance.
    """
    if (not isinstance(bore_face_indices, (list, tuple)) or not bore_face_indices
            or len(bore_face_indices) > len(faces)
            or any(type(i) is not int or not 0 <= i < len(faces) for i in bore_face_indices)
            or len(set(bore_face_indices)) != len(bore_face_indices)):
        _fail('Explicit nonempty unique in-range integer bore face indices required')
    try:
        lo, hi = p['z_min'], p['z_max']
        hx, hy = p['holes'][0]['center']
        radius = p['holes'][0]['radius']
    except (KeyError, IndexError, TypeError, ValueError):
        _fail('One declared sharp circular bore and nominal planes required')
    if (len(p.get('holes', [])) != 1 or p['holes'][0].get('counterbore')
            or p['holes'][0].get('kind', 'circle') != 'circle'
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in (lo, hi, hx, hy, radius))
            or not lo < hi or radius <= 0):
        _fail('One finite positive sharp circular bore with ordered nominal planes required')
    selected = sorted(bore_face_indices)
    edges = defaultdict(list)
    vertex_faces = defaultdict(set)
    face_neighbors = defaultdict(set)
    minimum_radial_normal_cosine = 1.0
    for fi in selected:
        f = faces[fi]
        for vi in f:
            vertex_faces[vi].add(fi)
        for a, b in zip(f, f[1:]+f[:1]):
            edges[tuple(sorted((a, b)))].append((fi, a, b))
        q = [vertices[i] for i in f]
        scale = max(sum(d*d for d in (a[k]-b[k] for k in range(3))) for a in q for b in q)
        if not math.isfinite(scale):
            _fail('Selected bore coordinates exceed the finite arithmetic domain', face_index=fi)
        for tri in ((0, 1, 2), (0, 2, 3), (0, 1, 3), (1, 2, 3)):
            a, b, c = (q[i] for i in tri)
            normal = _cross([b[k]-a[k] for k in range(3)], [c[k]-a[k] for k in range(3)])
            area = _length(normal)
            if not math.isfinite(area) or area <= max(scale*1e-12, 1e-18):
                _fail('Selected bore quad has a degenerate triangle interpretation', face_index=fi)
            dx = (a[0]+b[0]+c[0])/3-hx
            dy = (a[1]+b[1]+c[1])/3-hy
            radial = math.hypot(dx, dy)
            if not math.isfinite(radial) or radial <= 1e-12:
                _fail('Selected bore face crosses the declared axis', face_index=fi)
            cosine = -(normal[0]*dx+normal[1]*dy)/(area*radial)
            minimum_radial_normal_cosine = min(minimum_radial_normal_cosine, cosine)
            if not math.isfinite(cosine) or cosine <= math.sqrt(.5):
                _fail('Selected face is not an inward-oriented radial wall quad', face_index=fi,
                      radial_normal_cosine=cosine)
    for edge, users in edges.items():
        if len(users) not in (1, 2):
            _fail('Selected wall edge is nonmanifold', edge=list(edge))
        if len(users) == 2:
            u, v = users
            if u[1:] != (v[2], v[1]):
                _fail('Selected wall has inconsistent face winding', edge=list(edge))
            face_neighbors[u[0]].add(v[0]); face_neighbors[v[0]].add(u[0])
    reached = {selected[0]}; pending = [selected[0]]
    while pending:
        for fi in face_neighbors[pending.pop()]-reached:
            reached.add(fi); pending.append(fi)
    if len(reached) != len(selected):
        _fail('Selected wall is not one edge-connected region')
    # The parent mesh, rather than metadata alone, must support the selected
    # region and each boundary edge with exactly two oppositely oriented faces.
    full_users = defaultdict(list)
    for fi, f in enumerate(faces):
        for a, b in zip(f, f[1:]+f[:1]):
            edge = tuple(sorted((a, b)))
            if edge in edges:
                full_users[edge].append((fi, a, b))
    for edge, users in full_users.items():
        if len(users) != 2 or users[0][1:] != (users[1][2], users[1][1]):
            _fail('Actual mesh does not close the selected wall with consistent manifold adjacency',
                  edge=list(edge), adjacent_face_count=len(users))
    directed = [(users[0][1], users[0][2]) for users in edges.values() if len(users) == 1]
    outgoing = {}; incoming = {}
    for a, b in directed:
        if a in outgoing or b in incoming:
            _fail('Wall boundary branches or touches itself at a vertex')
        outgoing[a] = b; incoming[b] = a
    if not directed or set(outgoing) != set(incoming):
        _fail('Wall boundary is not closed')
    # Edge-manifoldness alone misses a pinched vertex. Verify each selected
    # vertex's incident-face link is one cycle (interior) or one path (boundary).
    for vi, incident in vertex_faces.items():
        adjacency = {fi: set() for fi in incident}
        for fi in incident:
            f = faces[fi]; j = f.index(vi)
            for other in (f[(j-1) % 4], f[(j+1) % 4]):
                for fj, _, _ in edges[tuple(sorted((vi, other)))]:
                    if fj != fi:
                        adjacency[fi].add(fj)
        seen = {next(iter(incident))}; todo = list(seen)
        while todo:
            for fj in adjacency[todo.pop()]-seen:
                seen.add(fj); todo.append(fj)
        degrees = sorted(len(v) for v in adjacency.values())
        if vi in outgoing:
            valid = (degrees == [0] or degrees == [1, 1]+[2]*(len(degrees)-2))
        else:
            valid = all(d == 2 for d in degrees)
        if seen != incident or not valid:
            _fail('Selected wall has a nonmanifold vertex link', vertex_index=vi)
    cycles = []; remaining = set(outgoing)
    while remaining:
        start = min(remaining); cycle = []; current = start
        while current in remaining:
            remaining.remove(current); cycle.append(current); current = outgoing[current]
        if current != start or len(cycle) < 3:
            _fail('Wall boundary does not form a simple closed cycle')
        cycles.append(cycle)
    euler = len(vertex_faces)-len(edges)+len(selected)
    if len(cycles) != 2 or euler != 0:
        _fail('Complete wall must be an annulus with exactly two closed boundary cycles',
              boundary_cycle_count=len(cycles), euler_characteristic=euler)
    result = {}; boundaries = {}
    for cycle in cycles:
        zvalues = [vertices[i][2] for i in cycle]
        errors = {'bottom_rim': max(abs(z-lo) for z in zvalues),
                  'top_rim': max(abs(z-hi) for z in zvalues)}
        choices = [label for label, error in errors.items() if error < STATION_EPSILON_MM]
        if len(choices) != 1 or choices[0] in result:
            _fail('Two complete boundary cycles must match distinct ordered nominal planes', plane_errors_mm=errors)
        label = choices[0]; sign = -1 if label == 'bottom_rim' else 1
        deltas = []
        for a, b in zip(cycle, cycle[1:]+cycle[:1]):
            ax, ay = vertices[a][0]-hx, vertices[a][1]-hy
            bx, by = vertices[b][0]-hx, vertices[b][1]-hy
            delta = math.atan2(ax*by-ay*bx, ax*bx+ay*by)
            if sign*delta <= 0 or abs(delta) >= math.pi:
                _fail('Directed boundary cycle must wind once without reversal around the declared axis', boundary=label)
            deltas.append(delta)
        winding = math.fsum(deltas)/math.tau
        if abs(winding-sign) > 1e-8:
            _fail('Boundary cycle does not have one correctly oriented winding', boundary=label, winding=winding)
        result[label] = cycle
        boundaries[label] = {'vertex_count': len(cycle), 'vertex_indices_sha256': _sha(cycle),
                             'nominal_plane_z_mm': lo if sign < 0 else hi,
                             'maximum_plane_error_mm': errors[label], 'directed_winding': winding}
    wall_ids = sorted(vertex_faces)
    if any(vertices[i][2] < lo-STATION_EPSILON_MM or vertices[i][2] > hi+STATION_EPSILON_MM for i in wall_ids):
        _fail('Selected wall extends beyond the actual nominal end planes')
    result['middle_wall'] = [i for i in wall_ids if abs(vertices[i][2]-(lo+hi)/2) < STATION_EPSILON_MM]
    return {'slice_vertex_indices': result, 'wall_vertex_indices': wall_ids, 'evidence': {
        'status': 'validated', 'selection_method': 'explicit_face_membership_with_actual_topology_validation',
        'face_count': len(selected), 'vertex_count': len(wall_ids), 'edge_count': len(edges),
        'membership_sha256': _sha(selected), 'boundary_cycle_count': 2, 'euler_characteristic': euler,
        'minimum_radial_normal_cosine': minimum_radial_normal_cosine,
        'boundaries': boundaries,
        'source_binding': 'caller_supplied_membership_requires_external_source_binding',
        'scope': 'Actual oriented closed-mesh quad annulus and nominal-plane boundary witnesses; does not authenticate source lineage or prove continuous surface accuracy'}}
