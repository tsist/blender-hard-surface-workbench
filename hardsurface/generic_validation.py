# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only validation of caller-declared geometry in millimetres.

Blender is imported only by run()/Mesh. Pure helpers remain independently
exercisable. Contacts are denied unless one bounded, qualified region contains
the complete intersection witness set. No design identifiers or defaults exist.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import defaultdict

from .io import RuntimeFailure
from .robust_geometry_audit import add, cross, dot, mul, norm, sub

EPS = .002  # numerical classification tolerance; never a design tolerance
AXIS = {'X': 0, 'Y': 1, 'Z': 2}
MAX_VERTICES = 200000
MAX_TRIANGLES = 400000
MAX_WITNESSES = 1000000
MAX_RAY_HITS = 1000
MAX_FALLBACKS = 64
MAX_COORDINATE_MM = 1e9


def xyz(point):
    return [float(x) for x in point]


def bbox(points):
    return [min(p[i] for p in points) for i in range(3)] + [max(p[i] for p in points) for i in range(3)]


def within(point, bounds, pad=0):
    return all(bounds[i] - pad <= point[i] <= bounds[i + 3] + pad for i in range(3))


def ranges_overlap(a, b, pad=0):
    return all(max(a[i], b[i]) <= min(a[i + 3], b[i + 3]) + pad for i in range(3))


def plane_coordinates(point, axis):
    """Map an XYZ-axis plane to qualifier UVZ; U/V preserve world-axis order."""
    k = AXIS[axis]
    order = [i for i in range(3) if i != k] + [k]
    return tuple(float(point[i]) for i in order)


def axis_ray(bounds, axis, fixed_mm):
    """Bounded positive-axis ray enclosing the actual mesh, at any translation."""
    k = AXIS[axis]
    extent = bounds[k + 3] - bounds[k]
    margin = max(EPS * 8, extent * 1e-6)
    origin = [0.0, 0.0, 0.0]
    direction = [0.0, 0.0, 0.0]
    origin[k] = bounds[k] - margin
    direction[k] = 1.0
    for i, value in zip((i for i in range(3) if i != k), fixed_mm):
        origin[i] = value
    return tuple(origin), tuple(direction), extent + 2 * margin


def ray_limit(origin, bounds):
    return math.sqrt(math.fsum(max(abs(bounds[i] - origin[i]), abs(bounds[i + 3] - origin[i])) ** 2 for i in range(3))) + EPS * 8


def bvh_precision(bounds):
    """Reject a local span whose binary32 spacing cannot resolve ray progress.

    Translation is removed before construction. The guard uses a stricter
    EPS/16 spacing than classification needs so the EPS/10 forward ray step
    remains representable. It does not expand any dimensional/contact tolerance.
    """
    extent = max(bounds[i + 3] - bounds[i] for i in range(3))
    maximum = extent / 2 + max(EPS * 8, extent * 1e-6)
    if not math.isfinite(maximum) or maximum < 0:
        raise RuntimeFailure('VALIDATION_PRECISION_UNSUPPORTED', 'Invalid local BVH coordinate span')
    spacing = max(2.0 ** -149, math.ldexp(1.0, math.frexp(maximum)[1] - 24)) if maximum else 2.0 ** -149
    if spacing > EPS / 16:
        raise RuntimeFailure('VALIDATION_PRECISION_UNSUPPORTED', 'Local binary32 BVH spacing exceeds the supported numerical domain',
                             maximum_local_coordinate_mm=maximum, binary32_spacing_mm=spacing,
                             maximum_spacing_mm=EPS / 16, numeric_epsilon_mm=EPS)
    return {'maximum_local_coordinate_mm': maximum, 'binary32_spacing_mm': spacing,
            'maximum_spacing_mm': EPS / 16, 'numeric_epsilon_mm': EPS}


def evaluated_topology(vertices, triangles):
    """Closed, single-component, outward evaluated triangle topology, not quads."""
    counts = defaultdict(list)
    adjacency = defaultdict(set)
    degenerate = 0
    for tri in triangles:
        points = [vertices[i] for i in tri]
        if norm(cross(sub(points[1], points[0]), sub(points[2], points[0]))) <= 1e-10:
            degenerate += 1
        for i, j in zip(tri, tri[1:] + tri[:1]):
            counts[tuple(sorted((i, j)))].append((i, j))
            adjacency[i].add(j)
            adjacency[j].add(i)
    unseen = set(range(len(vertices)))
    components = []
    membership = {}
    while unseen:
        stack = [unseen.pop()]
        component = set(stack)
        while stack:
            for i in adjacency[stack.pop()]:
                if i in unseen:
                    unseen.remove(i)
                    component.add(i)
                    stack.append(i)
        for i in component:
            membership[i] = len(components)
        components.append(component)
    # Translation-invariant summation avoids cancellation at world offsets.
    anchors = [vertices[min(component)] for component in components]
    volumes = [[] for _ in components]
    for tri in triangles:
        component = membership[tri[0]]
        a, b, c = [sub(vertices[i], anchors[component]) for i in tri]
        volumes[component].append(dot(a, cross(b, c)) / 6)
    signed = [math.fsum(values) for values in volumes]
    nonmanifold = sum(len(rows) != 2 for rows in counts.values())
    winding = sum(len(rows) == 2 and rows[0] != tuple(reversed(rows[1])) for rows in counts.values())
    finite = all(math.isfinite(float(x)) for p in vertices for x in p)
    passed = bool(vertices and triangles) and finite and not degenerate and not nonmanifold and not winding and len(components) == 1 and all(volume > 0 for volume in signed)
    return {'status': 'pass' if passed else 'fail', 'vertices': len(vertices),
            'triangles': len(triangles), 'components': len(components),
            'component_signed_volume_mm3': signed, 'nonmanifold_edges': nonmanifold,
            'inconsistent_winding_edges': winding, 'degenerate_triangles': degenerate,
            'finite': finite, 'scope': 'evaluated closed outward triangle topology; no source-quad or self-intersection certification'}


class Mesh:
    def __init__(self, label, obj, depsgraph):
        from mathutils.bvhtree import BVHTree
        self.label = label
        evaluated = obj.evaluated_get(depsgraph)
        data = evaluated.to_mesh()
        try:
            if len(data.vertices) > MAX_VERTICES or len(data.loops) > MAX_TRIANGLES * 3:
                raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Input evaluated mesh cardinality exceeds bound')
            data.calc_loop_triangles()
            if len(data.loop_triangles) > MAX_TRIANGLES:
                raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Evaluated triangle cardinality exceeds bound')
            matrix = [[float(x) for x in row] for row in evaluated.matrix_world]
            self.vertices = [tuple(math.fsum(matrix[i][j] * float(v.co[j]) for j in range(3)) * 1000.0 + matrix[i][3] * 1000.0 for i in range(3)) for v in data.vertices]
            self.tris = [tuple(t.vertices) for t in data.loop_triangles]
        finally:
            evaluated.to_mesh_clear()
        if not self.vertices or not self.tris:
            raise RuntimeFailure('VALIDATION_GEOMETRY', 'Empty evaluated mesh', part=label)
        if any(not math.isfinite(x) or abs(x) > MAX_COORDINATE_MM for p in self.vertices for x in p):
            raise RuntimeFailure('VALIDATION_GEOMETRY', 'Nonfinite/out-of-domain evaluated world coordinate', part=label)
        self.bounds = bbox(self.vertices)
        self.precision = bvh_precision(self.bounds)
        self.anchor = tuple((self.bounds[i] + self.bounds[i + 3]) / 2 for i in range(3))
        # BVH uses local coordinates, retaining its numerical precision at offsets.
        self.tree = BVHTree.FromPolygons([sub(v, self.anchor) for v in self.vertices], self.tris, all_triangles=True, epsilon=0.0)
        self.triangle_points = [tuple(self.vertices[i] for i in tri) for tri in self.tris]
        self.normals = []
        for a, b, c in self.triangle_points:
            normal = cross(sub(b, a), sub(c, a))
            length = norm(normal)
            self.normals.append(mul(normal, 1 / length) if length else (0.0, 0.0, 0.0))
        self.closed_outward_topology_pass = False
        self.fallback_budget = [MAX_FALLBACKS]
        self.fallback_records = []

    def ray(self, origin, direction, limit=None):
        from mathutils import Vector
        if limit is None:
            limit = ray_limit(origin, self.bounds)
        point = Vector(sub(origin, self.anchor))
        direction = Vector(direction).normalized()
        records = []
        distance = 0.0
        for _ in range(MAX_RAY_HITS):
            location, normal, index, _ = self.tree.ray_cast(point, direction, max(0, limit - distance))
            if location is None:
                return records
            distance += (location - point).length
            world = add(tuple(location), self.anchor)
            if not records or norm(sub(world, records[-1][0])) > EPS:
                records.append((world, tuple(normal), index))
            following = location + direction * (EPS * .1)
            advance = (following - location).dot(direction)
            if advance <= 0:
                raise RuntimeFailure('VALIDATION_GEOMETRY', 'Ray advance lost numerical precision', part=self.label)
            point = following
            distance += advance
            if distance >= limit:
                return records
        raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Ray hit bound exceeded', part=self.label)

    def classify(self, point):
        if not within(point, self.bounds, EPS):
            return 'outside'
        nearest = self.tree.find_nearest(sub(point, self.anchor))
        if nearest[0] is None:
            raise RuntimeFailure('VALIDATION_GEOMETRY', 'Empty nearest-surface query', part=self.label)
        if nearest[3] <= EPS:
            return 'boundary'
        votes = []
        for direction in ((1, .237, .413), (.311, 1, .527), (.419, .293, 1)):
            hits = self.ray(point, direction)
            unit = mul(direction, 1 / norm(direction))
            if any(abs(dot(normal, unit)) < 1e-6 for _, normal, _ in hits):
                continue
            votes.append(len(hits) % 2)
        if len(votes) >= 2 and len(set(votes)) == 1:
            return 'inside' if votes[0] else 'outside'
        if not self.closed_outward_topology_pass:
            return 'indeterminate'
        if self.fallback_budget[0] <= 0:
            raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Float64 fallback witness bound exceeded')
        self.fallback_budget[0] -= 1
        from .robust_geometry_audit import resolve_indeterminate
        result = resolve_indeterminate(point, self.triangle_points, parity_status='indeterminate', closed_outward_topology_pass=True, epsilon_mm=EPS)
        self.fallback_records.append({'point_mm': xyz(point), 'status': result['status'],
                                      'boundary': result.get('boundary'), 'winding': result.get('winding'),
                                      'angle_sum_radians': result.get('angle_sum_radians'),
                                      'method': result.get('method', result.get('reason')),
                                      'closed_outward_topology_pass': True, 'epsilon_mm': EPS})
        return result['status']


def _outer_contains(point, outer):
    if outer['kind'] == 'rectangle':
        return all(a - EPS <= value <= b + EPS for value, a, b in zip(point, outer['min_mm'], outer['max_mm']))
    return math.hypot(point[0] - outer['center_mm'][0], point[1] - outer['center_mm'][1]) <= outer['radius_mm'] + EPS


def _hull(points):
    points = sorted(set(tuple(p[:2]) for p in points))
    if len(points) < 3:
        return points
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _avoids_bore(points, boundary):
    """Fail closed on a contact hull crossing the strict actual bore interior.

    Every point first passes the qualified finite-segment boundary test. A
    separating-axis test then prevents endpoints on opposite sides of a bore
    from licensing a segment/polygon across its interior. Positive interior
    overlap is rejected conservatively; the numerical epsilon is never widened.
    """
    from .contact_boundary import outside_or_boundary
    if not all(outside_or_boundary(point, boundary, epsilon_mm=EPS) for point in points):
        return False
    hull = _hull(points)
    if len(hull) <= 1:
        return True
    loop = [tuple(point[:2]) for point in boundary['loop_mm']]
    # Include hull edges and bore edges: this also detects a filled contact patch
    # containing the entire bore, even when all original vertices avoid the bore.
    for polygon in (loop, hull):
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            axis = (-(b[1] - a[1]), b[0] - a[0])
            if axis == (0, 0):
                continue
            # Translate before projection to avoid cancellation at world offsets.
            hp = [(point[0] - a[0]) * axis[0] + (point[1] - a[1]) * axis[1] for point in hull]
            bp = [(point[0] - a[0]) * axis[0] + (point[1] - a[1]) * axis[1] for point in loop]
            # Binary64 arithmetic can place an exact shared-edge interpolation
            # a few ulps across its own supporting line. Use an operation-scale
            # roundoff bound in projection units, never the contact/design EPS.
            coordinate_ulp=max(math.ulp(max(1.,abs(value))) for point in hull+loop for value in point)
            roundoff=128*coordinate_ulp*math.hypot(*axis)
            if max(hp) <= min(bp)+roundoff or max(bp) <= min(hp)+roundoff:
                return True
    return False


def qualify_contacts(specs, meshes):
    from .contact_boundary import qualified_hole_boundary
    contacts = {}
    evidence = []
    checks = []
    for i, contact in enumerate(specs):
        prepared = []
        for j, region in enumerate(contact['regions']):
            boundaries = []
            for k, bore in enumerate(region['bores']):
                mesh = meshes[bore['part']]
                boundary = qualified_hole_boundary(
                    [plane_coordinates(point, region['axis']) for point in mesh.vertices], mesh.tris,
                    center_mm=bore['center_mm'], radius_mm=bore['radius_mm'], z_mm=region['plane_mm'],
                    epsilon_mm=EPS, position_tolerance_mm=bore['position_tolerance_mm'],
                    chord_tolerance_mm=bore['chord_tolerance_mm'])
                boundaries.append(boundary)
                identity = f'@contact/{i}/region/{j}/bore/{k}'
                checks.append({'id': identity, 'status': boundary['status'], 'kind': 'actual_bore_qualification', 'part': bore['part']})
                row = {key: value for key, value in boundary.items() if key not in ('loop_mm', 'loop_vertex_indices')}
                encoded = json.dumps({'loop_mm': boundary['loop_mm'], 'loop_vertex_indices': boundary['loop_vertex_indices']}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                evidence.append({'id': identity, 'part': bore['part'], 'world_axis': region['axis'],
                                 'coordinate_order': [i for i in range(3) if i != AXIS[region['axis']]] + [AXIS[region['axis']]],
                                 **row, 'actual_loop_sha256': hashlib.sha256(encoded).hexdigest()})
            prepared.append({'spec': region, 'boundaries': boundaries})
        contacts[frozenset(contact['pair'])] = prepared
    return contacts, evidence, checks


def contact_allowed(part_a, part_b, points, contacts):
    if not points:
        return False
    for prepared in contacts.get(frozenset((part_a, part_b)), []):
        region = prepared['spec']
        projected = [plane_coordinates(point, region['axis']) for point in points]
        if not all(abs(point[2] - region['plane_mm']) <= EPS and _outer_contains(point, region['outer']) for point in projected):
            continue
        if all(_avoids_bore(projected, boundary) for boundary in prepared['boundaries']):
            return True
    return False


def pair_check(a, b, contacts):
    if not ranges_overlap(a.bounds, b.bounds, EPS):
        return {'pair': [a.label, b.label], 'status': 'pass', 'method': 'disjoint evaluated AABBs'}
    from .contact_candidates import cross_candidates
    from .robust_geometry_audit import triangle_intersection
    start = {mesh.label: len(mesh.fallback_records) for mesh in (a, b)}
    candidates, broad_phase = cross_candidates(a.vertices, a.tris, b.vertices, b.tris, epsilon_mm=EPS)
    forbidden, allowed = [], []
    forbidden_count = allowed_count = 0
    for i, j in candidates:
        points = triangle_intersection(a.triangle_points[i], b.triangle_points[j], epsilon_mm=EPS)['points_mm']
        if not points:
            continue
        valid = contact_allowed(a.label, b.label, points, contacts)
        rows = allowed if valid else forbidden
        if valid:
            allowed_count += 1
        else:
            forbidden_count += 1
        if len(rows) < 20:
            row = {'triangles': [i, j], 'points_mm': [xyz(point) for point in points]}
            if not valid:
                row['source_triangles_mm'] = [[xyz(point) for point in a.triangle_points[i]], [xyz(point) for point in b.triangle_points[j]]]
            rows.append(row)
    contained, uncertain = [], []
    inside_count = uncertain_count = tested = 0
    rejected_inward = {'outside':0,'boundary':0,'indeterminate':0}
    verified_inward = 0
    for source, target in ((a, b), (b, a)):
        inward = (sub(mul(add(add(p, q), r), 1 / 3), mul(n, EPS * 4)) for (p, q, r), n in zip(source.triangle_points, source.normals))
        for point, witness_kind in itertools.chain(((point,'surface_vertex') for point in source.vertices),((point,'inward_centroid') for point in inward)):
            if not within(point, target.bounds, EPS):
                continue
            tested += 1
            if tested > MAX_WITNESSES:
                raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Containment witness bound exceeded')
            row = {'source': source.label, 'target': target.label, 'point_mm': xyz(point),'witness_kind':witness_kind}
            if witness_kind=='inward_centroid':
                origin_state=source.classify(point)
                if origin_state!='inside':
                    rejected_inward[origin_state]+=1
                    if origin_state=='indeterminate':
                        uncertain_count+=1
                        if len(uncertain)<20:uncertain.append(dict(row,reason='Source interior witness could not be established'))
                    continue
                verified_inward+=1
            state = target.classify(point)
            if state == 'inside':
                inside_count += 1
                if len(contained) < 20:
                    contained.append(row)
            elif state == 'indeterminate':
                uncertain_count += 1
                if len(uncertain) < 20:
                    uncertain.append(row)
    failed = forbidden_count or inside_count
    return {'pair': [a.label, b.label], 'status': 'fail' if failed else 'not_run' if uncertain_count else 'pass',
            'method': 'Complete cross-object spatial AABB candidates, float64 triangle intersection, all source vertices and source-qualified inward-centroid containment witnesses; topology-qualified float64 fallback for uncertain parity',
            'candidate_triangle_pairs': len(candidates), 'broad_phase': broad_phase,
            'float64_fallbacks': {mesh.label: mesh.fallback_records[start[mesh.label]:] for mesh in (a, b)},
            'allowed_contact_examples': allowed, 'allowed_contact_count': allowed_count,
            'forbidden_intersection_examples': forbidden, 'forbidden_intersection_count': forbidden_count,
            'strict_containment_examples': contained, 'strict_containment_count': inside_count,
            'indeterminate_examples': uncertain, 'indeterminate_count': uncertain_count,
            'containment_points_tested': tested,'source_interior_points_verified':verified_inward,'rejected_inward_witnesses':rejected_inward, 'numeric_epsilon_mm': EPS,
            'coverage': 'Static triangulated surface intersections and sampled containment; no exact B-rep or continuous-motion proof'}


def bounds_result(identity, actual, specification):
    error = max(abs(a - b) for a, b in zip(actual, specification['expected']))
    return {'id': identity, 'kind': 'bounds', 'status': 'pass' if error <= specification['tolerance_mm'] else 'fail',
            'actual_mm': actual, 'expected_mm': specification['expected'], 'max_error_mm': error,
            'tolerance_mm': specification['tolerance_mm']}


def probe_result(probe, hits):
    expected = probe['expected_hits_mm']
    error = max((abs(a - b) for a, b in zip(hits, expected)), default=0) if len(hits) == len(expected) else None
    return {'id': probe['id'], 'kind': 'axis_ray', 'status': 'pass' if error is not None and error <= probe['tolerance_mm'] else 'fail',
            'actual_hits_mm': hits, 'expected_hits_mm': expected, 'max_error_mm': error, 'tolerance_mm': probe['tolerance_mm']}


def derived_results(params, rays):
    results = []
    for spec in params['difference_checks']:
        a, b = rays[spec['a']['probe']], rays[spec['b']['probe']]
        try:
            actual = a['actual_hits_mm'][spec['a']['index']] - b['actual_hits_mm'][spec['b']['index']]
            error = abs(actual - spec['expected_mm'])
            status = 'pass' if error <= spec['tolerance_mm'] else 'fail'
        except IndexError:
            actual = error = None
            status = 'fail'
        results.append({'id': spec['id'], 'kind': 'hit_difference', 'status': status, 'actual_mm': actual,
                        'expected_mm': spec['expected_mm'], 'error_mm': error, 'tolerance_mm': spec['tolerance_mm']})
    for spec in params['axis_checks']:
        rows = [rays[key] for key in spec['probes']]
        if any(len(row['actual_hits_mm']) != 2 for row in rows):
            results.append({'id': spec['id'], 'kind': 'axial_angle', 'status': 'fail', 'reason': 'Two boundary hits required per transverse ray'})
            continue
        centers = [sum(row['actual_hits_mm']) / 2 for row in rows]
        lateral = math.hypot(centers[2] - centers[0], centers[3] - centers[1])
        angle = math.degrees(math.atan2(lateral, spec['separation_mm']))
        results.append({'id': spec['id'], 'kind': 'axial_angle', 'status': 'pass' if angle <= spec['maximum_degrees'] else 'fail',
                        'actual_degrees': angle, 'maximum_degrees': spec['maximum_degrees'],
                        'method': 'Two orthogonal transverse ray midpoint witnesses at two declared axial stations'})
    return results


def validate_scene_scope(scene, depsgraph):
    """Fail closed when renderable geometry is outside the real-mesh contract."""
    non_geometry={'CAMERA','LIGHT','EMPTY','ARMATURE','LATTICE','SPEAKER','LIGHT_PROBE'}
    for obj in scene.objects:
        if getattr(obj,'hide_render',False):continue
        if getattr(obj,'instance_type','NONE') not in ('NONE',None) or bool(getattr(obj,'particle_systems',())):
            raise RuntimeFailure('VALIDATION_UNSUPPORTED_SCENE','Renderable instances or particles require a separate qualified adapter',object=getattr(obj,'name',''),type=obj.type)
        if obj.type!='MESH' and obj.type not in non_geometry:
            raise RuntimeFailure('VALIDATION_UNSUPPORTED_SCENE','Renderable non-mesh geometry is outside the real-mesh validation contract',object=getattr(obj,'name',''),type=obj.type)
    if any(getattr(instance,'is_instance',False) for instance in getattr(depsgraph,'object_instances',())):
        raise RuntimeFailure('VALIDATION_UNSUPPORTED_SCENE','Evaluated instances are outside the real-mesh validation contract')


def run(params):
    """Read the open Blender scene using already schema-validated parameters."""
    import bpy
    scene = bpy.context.scene
    if scene.unit_settings.system != 'METRIC' or abs(scene.unit_settings.scale_length - 1) > 1e-9:
        raise RuntimeFailure('VALIDATION_UNITS', 'Validation requires scene SI metres with scale_length=1')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    validate_scene_scope(scene,depsgraph)
    visible = [obj for obj in scene.objects if obj.type == 'MESH' and not obj.hide_render]
    selected, meshes = {}, {}
    for part in params['parts']:
        matches = [obj for obj in visible if obj.get('hs_feature_id') == part['feature_id']]
        if len(matches) != 1:
            raise RuntimeFailure('VALIDATION_SELECTION', 'Expected exactly one render-visible mesh with the declared hs_feature_id', part=part['id'], feature_id=part['feature_id'], matches=len(matches))
        obj = matches[0]
        if obj.hide_viewport or (callable(getattr(obj,'visible_get',None)) and not obj.visible_get()) or any(mod.show_render != mod.show_viewport for mod in obj.modifiers):
            raise RuntimeFailure('VALIDATION_SELECTION', 'Viewport/render evaluation mismatch', part=part['id'])
        selected[part['id']] = obj
        meshes[part['id']] = Mesh(part['id'], obj, depsgraph)
        if sum(len(mesh.vertices) for mesh in meshes.values()) > MAX_VERTICES or sum(len(mesh.tris) for mesh in meshes.values()) > MAX_TRIANGLES:
            raise RuntimeFailure('VALIDATION_BUDGET_EXCEEDED', 'Aggregate evaluated geometry exceeds bound')
    topology = {part: evaluated_topology(mesh.vertices, mesh.tris) for part, mesh in meshes.items()}
    fallback_budget = [MAX_FALLBACKS]
    for part, mesh in meshes.items():
        mesh.closed_outward_topology_pass = topology[part]['status'] == 'pass'
        mesh.fallback_budget = fallback_budget
    checks = []
    if params['require_all_visible']:
        matched = set(selected.values())
        extra = [obj.name for obj in visible if obj not in matched]
        checks.append({'id': '@assembly/selected_meshes', 'kind': 'selected_visible_meshes', 'status': 'fail' if extra else 'pass',
                       'expected_count': len(meshes), 'actual_visible_count': len(visible), 'unselected_visible_meshes': extra})
    for part in params['parts']:
        if 'bounds_mm' in part:
            checks.append(bounds_result('@bounds/' + part['id'], meshes[part['id']].bounds, part['bounds_mm']))
    if 'assembly_bounds_mm' in params:
        actual = bbox([tuple(mesh.bounds[:3]) for mesh in meshes.values()] + [tuple(mesh.bounds[3:]) for mesh in meshes.values()])
        checks.append(bounds_result('@assembly/bounds', actual, params['assembly_bounds_mm']))
    rays = {}
    for probe in params['probes']:
        mesh = meshes[probe['part']]
        origin, direction, limit = axis_ray(mesh.bounds, probe['axis'], probe['fixed_mm'])
        hits = [float(point[AXIS[probe['axis']]]) for point, _, _ in mesh.ray(origin, direction, limit)]
        row = probe_result(probe, hits)
        checks.append(row)
        rays[probe['id']] = row
    checks.extend(derived_results(params, rays))
    contacts, evidence = {}, []
    if params['include_pairs']:
        contacts, evidence, qualifications = qualify_contacts(params['contacts'], meshes)
        checks.extend(qualifications)
    pairs = []
    for a, b in itertools.combinations(meshes, 2):
        if not params['include_pairs']:
            pairs.append({'pair': [a, b], 'status': 'not_run', 'reason': 'Pair validation explicitly disabled'})
        elif topology[a]['status'] != 'pass' or topology[b]['status'] != 'pass':
            pairs.append({'pair': [a, b], 'status': 'not_run', 'reason': 'Closed outward evaluated topology required for both parts'})
        else:
            pairs.append(pair_check(meshes[a], meshes[b], contacts))
    def status(rows):
        statuses = [row['status'] for row in rows]
        return 'fail' if 'fail' in statuses else 'not_run' if 'not_run' in statuses else 'pass'
    pair_status = status(pairs) if params['include_pairs'] else 'not_run'
    overall = status(list(topology.values()) + checks + pairs + [{'status': pair_status}])
    return {'version': 'generic-declarative-validation-2.0', 'candidate': bpy.data.filepath,
            'blender_version': bpy.app.version_string, 'evaluation': 'Evaluated dependency graph world geometry in mm from SI metres; local-coordinate BVH',
            'parts': list(meshes), 'status': overall, 'pair_status': pair_status,
            'status_scope': 'Provided specifications and selected evaluated closed outward triangle topology; requested static interpart checks only',
            'require_all_visible': params['require_all_visible'], 'include_pairs': params['include_pairs'],
            'topology': topology, 'checks': checks, 'pairs': pairs, 'contact_boundaries': evidence,
            'bvh_precision': {part: mesh.precision for part, mesh in meshes.items()},
            'visual': 'not_run', 'user_feedback': 'not_run',
            'limitations': ['The caller supplies all design values; passing does not certify arbitrary-design correctness',
                            'Ray and containment witnesses are discrete samples',
                            'This verifier does not certify source quads or single-part self-intersection freedom',
                            'Only real mesh bodies are supported; instances, particles, renderable curves/volumes and viewport/render visibility mismatches reject',
                            'Partial selection does not validate unselected parts',
                            'Contacts must fit one bounded declared region; declared bore loops must qualify from actual evaluated facets',
                            'Contact witness hulls crossing actual bore interiors are rejected conservatively',
                            'Local BVH binary32 spacing must be at most numerical epsilon/16; unsupported extents fail explicitly',
                            'Numerical classification epsilon is 0.002 mm; dimensional tolerances are caller-supplied']}
