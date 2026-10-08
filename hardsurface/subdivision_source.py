"""Fail-closed source and evaluated FACE-INT transport for fixed Catmull-Clark.

Host-safe protocol helpers. These functions never evaluate Blender, write IDs,
repair labels or infer correspondence from face ordering or nearby geometry.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math

from . import contract
from .io import RuntimeFailure
from .structure_kernel import fingerprint
from .structure_native import FACE_SLOT, MANIFEST, validate_native_structure

SURFACE_ATTRIBUTE = 'hs_quad_surface_id'
SPARSE_SOURCE_SCHEMA = 'sparse_sharp_panel_ids_v2'
GEOMETRY_PROPERTIES = (
    'subdivision_type', 'levels', 'render_levels', 'quality', 'uv_smooth',
    'boundary_smooth', 'use_creases', 'use_limit_surface', 'use_custom_normals',
    'show_viewport', 'show_render', 'show_in_editmode', 'show_on_cage',
    'show_only_control_edges', 'use_apply_on_spline', 'use_pin_to_last',
    'use_adaptive_subdivision', 'adaptive_space', 'adaptive_pixel_size',
    'adaptive_object_edge_length',
)
DISPLAY_PROPERTIES = ('is_active', 'show_expanded', 'open_adaptive_subdivision_panel', 'open_advanced_panel')
REQUIRED_PROPERTIES = frozenset(GEOMETRY_PROPERTIES[:16])


def _fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def snapshot_source_modifier(obj):
    """Read all writable settings and reject unsupported evaluation semantics.

RNA is authoritative in Blender. The explicit attribute fallback is only the
host-mock protocol. New unclassified writable RNA fields fail closed rather
than being silently omitted by a permissive snapshot.
    """
    if len(obj.modifiers) != 1 or obj.modifiers[0].type != 'SUBSURF':
        _fail('SUBDIVISION_PROFILE_STACK', 'Source-bound evaluation needs exactly one actual SUBSURF modifier')
    mod = obj.modifiers[0]
    properties = getattr(getattr(mod, 'bl_rna', None), 'properties', None)
    if properties is None:
        names = [key for key in (*GEOMETRY_PROPERTIES, *DISPLAY_PROPERTIES) if hasattr(mod, key)]
        unknown = set(vars(mod)) - set(names) - {'name', 'type', 'bl_rna'}
    else:
        names = [p.identifier for p in properties if p.identifier not in ('rna_type', 'name', 'type') and not p.is_readonly]
        unknown = set(names) - set(GEOMETRY_PROPERTIES) - set(DISPLAY_PROPERTIES)
    if unknown:
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Unclassified writable modifier settings cannot be omitted', properties=sorted(unknown))
    if not REQUIRED_PROPERTIES <= set(names):
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Source modifier lacks required evaluation settings', missing=sorted(REQUIRED_PROPERTIES-set(names)))
    actual = {'name': mod.name, 'type': mod.type}
    for key in names:
        value = getattr(mod, key)
        if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
            _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Modifier settings must be finite scalar values', property=key)
        actual[key] = value
    if actual['subdivision_type'] != 'CATMULL_CLARK' or not actual['show_viewport'] or not actual['show_render']:
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Only enabled viewport/render Catmull-Clark source evaluation is supported')
    if actual.get('use_adaptive_subdivision', False) or getattr(getattr(obj, 'cycles', None), 'use_adaptive_subdivision', False):
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Adaptive subdivision is unsupported by fixed-level descendant transport')
    if actual['use_custom_normals'] or getattr(obj.data, 'has_custom_normals', False) or obj.data.attributes.get('custom_normal') is not None:
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Custom-normal subdivision requires a separately qualified evaluation route')
    if getattr(obj, 'hide_viewport', False) or getattr(obj, 'hide_render', False):
        _fail('SUBDIVISION_PROFILE_UNSUPPORTED', 'Hidden source evaluation cannot be silently enabled on a qualification clone')
    return actual


def declared_modifier_settings(actual):
    """Expected settings cover every supported non-cosmetic writable property."""
    return {key: deepcopy(value) for key, value in actual.items() if key not in DISPLAY_PROPERTIES}


def require_modifier_profile(actual, expected):
    declared = declared_modifier_settings(actual)
    # Bool/int equality must not weaken this identity check.
    if fingerprint(declared) != fingerprint(expected):
        _fail('SUBDIVISION_PROFILE_MISMATCH', 'Actual source modifier differs from the explicitly requested complete evaluation profile',
              expected=expected, actual=declared)
    return {'status': 'pass', 'expected_settings_sha256': fingerprint(expected),
            'actual_settings_sha256': fingerprint(actual), 'actual_source_modifier': deepcopy(actual),
            'expected_source_modifier': deepcopy(expected), 'preservation_scope': 'all writable source modifier settings; only declared per-level sample overrides'}


def _face_int_values(mesh, name):
    attr = mesh.attributes.get(name)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT' or len(attr.data) != len(mesh.polygons):
        _fail('SUBDIVISION_SEMANTIC_ATTRIBUTE', 'Complete actual FACE INT attribute required', attribute=name)
    values = [item.value for item in attr.data]
    if any(type(value) is not int for value in values):
        _fail('SUBDIVISION_SEMANTIC_ATTRIBUTE', 'FACE INT values must be actual integers', attribute=name)
    return values


def _edge_uses(mesh):
    uses = defaultdict(list)
    for index, polygon in enumerate(mesh.polygons):
        face = list(polygon.vertices)
        if len(face) != 4 or len(set(face)) != 4:
            _fail('SUBDIVISION_SEMANTIC_TOPOLOGY', 'Nondegenerate indexed quad faces required')
        for a, b in zip(face, face[1:]+face[:1]): uses[tuple(sorted((a, b)))].append((index, a, b))
    if any(len(rows) != 2 or rows[0][1:] != rows[1][1:][::-1] for rows in uses.values()):
        _fail('SUBDIVISION_SEMANTIC_TOPOLOGY', 'Semantic transport requires a closed consistently oriented actual mesh')
    return uses


def _source_neighbour_cycles(mesh, slots):
    uses = _edge_uses(mesh); by_face = {}
    for rows in uses.values():
        left, right = rows
        by_face[(left[0], left[1], left[2])] = slots[right[0]]
        by_face[(right[0], right[1], right[2])] = slots[left[0]]
    cycles = [None] * len(slots)
    for fi, polygon in enumerate(mesh.polygons):
        face = list(polygon.vertices)
        cycles[slots[fi]] = [by_face[(fi, a, b)] for a, b in zip(face, face[1:]+face[:1])]
    return cycles


def _cyclic_key(values):
    return min(tuple(values[i:]+values[:i]) for i in range(len(values)))


def _validate_patch_topology(mesh, parents, source_cycles, level):
    """Verify connected descendant disks and oriented source quotient topology.

This is a transport consistency witness, not a reusable global evaluated-ID
proof. A graph automorphism of an entire symmetric source can preserve this
topology; geometric bore membership is checked separately by the measurement.
    """
    uses = _edge_uses(mesh); groups = defaultdict(set); neighbours = defaultdict(set)
    boundaries = defaultdict(dict); patch_vertices = defaultdict(set); patch_edges = defaultdict(set)
    for fi, (polygon, parent) in enumerate(zip(mesh.polygons, parents)):
        groups[parent].add(fi); patch_vertices[parent].update(polygon.vertices)
    for edge, rows in uses.items():
        left, right = rows; a, b = parents[left[0]], parents[right[0]]
        patch_edges[a].add(edge); patch_edges[b].add(edge)
        if a == b:
            neighbours[left[0]].add(right[0]); neighbours[right[0]].add(left[0])
        else:
            for row, parent, other in ((left, a, b), (right, b, a)):
                if row[1] in boundaries[parent]:
                    _fail('SUBDIVISION_SEMANTIC_PATCH', 'Parent region boundary branches or touches itself', parent_slot=parent)
                boundaries[parent][row[1]] = (row[2], other)
    subdivisions = 2 ** level; actual_cycles = []
    for parent in range(len(source_cycles)):
        region = groups[parent]; seen = {next(iter(region))}; queue = list(seen)
        while queue:
            for neighbour in neighbours[queue.pop()]:
                if neighbour not in seen: seen.add(neighbour); queue.append(neighbour)
        if seen != region or len(patch_vertices[parent])-len(patch_edges[parent])+len(region) != 1:
            _fail('SUBDIVISION_SEMANTIC_PATCH', 'Every parent region must be a connected topological disk', parent_slot=parent)
        boundary = boundaries[parent]
        if len(boundary) != 4*subdivisions or {end for end, _ in boundary.values()} != set(boundary):
            _fail('SUBDIVISION_SEMANTIC_PATCH', 'Parent patch must have one quad-derived oriented boundary', parent_slot=parent)
        first = min(boundary); current = first; walked = set(); neighbours_around = []
        while current not in walked:
            walked.add(current); current, neighbour = boundary[current]; neighbours_around.append(neighbour)
        if current != first or len(walked) != len(boundary):
            _fail('SUBDIVISION_SEMANTIC_PATCH', 'Parent patch has multiple boundary cycles', parent_slot=parent)
        compressed = []
        for neighbour in neighbours_around:
            if not compressed or compressed[-1] != neighbour: compressed.append(neighbour)
        if len(compressed) > 1 and compressed[0] == compressed[-1]: compressed.pop()
        expected = source_cycles[parent]
        if (len(compressed) != 4 or _cyclic_key(compressed) != _cyclic_key(expected)
                or Counter(neighbours_around) != Counter({value: expected.count(value)*subdivisions for value in expected})):
            _fail('SUBDIVISION_SEMANTIC_PATCH', 'Evaluated parent boundary adjacency/orientation differs from authenticated original quad', parent_slot=parent)
        actual_cycles.append(list(_cyclic_key(compressed)))
    return {'status': 'pass', 'connected_disk_regions': len(groups), 'oriented_source_neighbour_cycles_match': True,
            'boundary_edges_per_source_quad': 4*subdivisions,
            'quotient_topology_sha256': fingerprint(actual_cycles),
            'scope': 'counts, connected disk regions and oriented source quotient adjacency; not globally unique per-face ancestry under symmetric graph automorphisms'}


def _sparse_bore_annulus(mesh, members, *, vertex_map, declared_cycles):
    """Check the actual oriented holewall region, including its vertex links."""
    uses = _edge_uses(mesh)
    neighbours = defaultdict(set); edges = set(); vertices = set()
    outgoing = {}; incoming = Counter(); links = defaultdict(lambda: defaultdict(set))
    for fi in members:
        face = list(mesh.polygons[fi].vertices); vertices.update(face)
        for j, vertex in enumerate(face):
            a, b = face[j-1], face[(j+1) % len(face)]
            links[vertex][a].add(b); links[vertex][b].add(a)
    for edge, rows in uses.items():
        selected = [row for row in rows if row[0] in members]
        if not selected: continue
        edges.add(edge)
        if len(selected) == 2:
            a, b = selected[0][0], selected[1][0]
            neighbours[a].add(b); neighbours[b].add(a)
        else:
            _, a, b = selected[0]
            if a in outgoing:
                _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Holewall boundary branches or touches itself')
            outgoing[a] = b; incoming[b] += 1
    if (not members or set(outgoing) != set(incoming) or any(n != 1 for n in incoming.values())
            or len(vertices)-len(edges)+len(members) != 0):
        _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Holewall must have annular Euler characteristic and directed boundary cycles')
    seen = set(); pending = [next(iter(members))]
    while pending:
        face = pending.pop()
        if face in seen: continue
        seen.add(face); pending.extend(neighbours[face]-seen)
    if seen != members:
        _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Holewall must be one connected region')
    for vertex, link in links.items():
        seen = set(); pending = [next(iter(link))]
        while pending:
            neighbour = pending.pop()
            if neighbour in seen: continue
            seen.add(neighbour); pending.extend(link[neighbour]-seen)
        degrees = [len(row) for row in link.values()]
        if (seen != set(link) or any(n not in (1, 2) for n in degrees)
                or degrees.count(1) != (2 if vertex in outgoing else 0)):
            _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Holewall vertex links must be single interior cycles or boundary paths')
    inverse = {index: key for key, index in vertex_map.items()}
    unused = set(outgoing); cycles = []
    while unused:
        first = min(unused); current = first; cycle = []
        while current in unused:
            unused.remove(current); cycle.append(inverse[current]); current = outgoing[current]
        if current != first or len(cycle) < 3:
            _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Holewall boundary must consist of disjoint oriented cycles')
        cycles.append(list(_cyclic_key(cycle)))
    cycles.sort()
    expected = sorted(list(_cyclic_key(list(cycle))) for cycle in declared_cycles)
    if len(cycles) != 2 or cycles != expected:
        _fail('SUBDIVISION_SEMANTIC_ANNULUS', 'Actual holewall must have exactly the two authenticated oriented region boundaries')
    return {'status': 'pass', 'source_mesh_closed_oriented': True, 'connected_regions': 1,
            'manifold_vertex_links': True, 'euler_characteristic': 0,
            'boundary_cycle_count': len(cycles), 'boundary_vertex_cycles': cycles,
            'boundary_cycle_lengths': [len(cycle) for cycle in cycles],
            'oriented_boundary_cycles_sha256': fingerprint(cycles)}


def _capture_sparse_source(obj, native, expected_binding):
    """Bind resolved per-face sparse provenance to authenticated author identities.

    Native materialization deduplicates equal provenance rows. Surface labels
    address that actual table; FACE_SLOT and the native face map provide face
    identity. Neither label numbers nor current storage order imply ancestry.
    """
    if expected_binding is None or native.get('identity_binding_status') != 'bound':
        _fail('SUBDIVISION_SOURCE_BINDING', 'Sparse diagnostic source requires an actual external registry binding')
    author = native.get('authorship', {})
    if author.get('schedule_revision') != SPARSE_SOURCE_SCHEMA:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Explicit sparse source schema does not match the authenticated author schedule')
    actual_modifier = snapshot_source_modifier(obj)
    count = len(obj.data.polygons)
    if not count or any(len(face.vertices) != 4 for face in obj.data.polygons):
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Fixed all-quad source required for exact per-face descendant transport')
    try:
        table = contract.strict_loads(obj.get('hs_quad_surface_table'))
        envelope = contract.strict_loads(obj.data.get(MANIFEST))
    except (contract.ContractError, TypeError, ValueError) as exc:
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Sparse semantic table and authenticated manifest must be strict bounded JSON', reason=str(exc))
    required = {'feature_id', 'surface_id', 'surface_role', 'region_group', 'smooth', 'curved', 'support_band'}
    if (not isinstance(table, list) or not 0 < len(table) <= count
            or any(not isinstance(row, dict) or not required <= set(row)
                   or any(not isinstance(row[key], str) or not row[key] for key in ('feature_id', 'surface_id', 'surface_role', 'region_group'))
                   or any(type(row[key]) is not bool for key in ('smooth', 'curved', 'support_band')) for row in table)):
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Sparse source requires a complete typed provenance table resolved for every source face')
    features = {row['feature_id'] for row in table}
    if len(features) != 1 or (obj.get('hs_feature_id') is not None and features != {obj.get('hs_feature_id')}):
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Foreign or missing sparse source feature in semantic provenance')
    feature = next(iter(features)); labels = _face_int_values(obj.data, SURFACE_ATTRIBUTE)
    slots = _face_int_values(obj.data, FACE_SLOT)
    if set(slots) != set(range(count)) or len(set(slots)) != count:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Source parent slots must uniquely cover every actual source face')
    if set(labels) != set(range(len(table))):
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Sparse surface labels must completely cover the actual authored provenance table')
    face_map = author['face_map']; original = envelope['authored_structure']
    face_ids = envelope['face_ids']; by_slot = [None] * count; provenance = [None] * count
    for face_id, face_index in face_map.items():
        parent, label = slots[face_index], labels[face_index]
        if face_ids[parent] != face_id:
            _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Actual sparse parent slot is not its authenticated original author face', face_id=face_id)
        by_slot[parent] = label; provenance[parent] = deepcopy(table[label])
    resolved_provenance = {face_id: table[labels[face_index]] for face_id, face_index in face_map.items()}
    if fingerprint(resolved_provenance) != envelope['semantic_baseline']['provenance']:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Resolved source labels differ from the authenticated per-face provenance')
    structure = native['witness']['structure']
    regions = [row for row in structure['regions'] if row['role'] == 'holewall/bore_wall']
    if len(regions) != 1 or regions[0]['feature_id'] != feature:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Exactly one authenticated typed holewall region is required')
    region = regions[0]; authored_bore = set(region['face_ids'])
    actual_bore = set()
    for face_id, face_index in face_map.items():
        row = table[labels[face_index]]
        is_wall = row['region_group'] == 'holewall'
        is_bore = row['surface_role'] == 'holewall/bore_wall' and row['surface_id'] == 'holewall/bore_wall'
        if is_wall != is_bore or is_bore != (face_id in authored_bore):
            _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Sparse holewall provenance differs from the complete authenticated typed region', face_id=face_id)
        if is_bore: actual_bore.add(face_index)
    loops = {row['id']: row for row in structure['loops']}
    annulus = _sparse_bore_annulus(obj.data, actual_bore, vertex_map=author['vertex_map'],
        declared_cycles=[loops[key]['vertex_ids'] for key in region['boundary_loop_ids']])
    bore_labels = sorted({labels[index] for index in actual_bore})
    bore_parents = sorted(slots[index] for index in actual_bore)
    neighbour_cycles = _source_neighbour_cycles(obj.data, slots)
    def control_loops(manifest):
        return [{'role': row['role'], 'vertex_indices': [manifest['vertex_map'][key] for key in row['vertex_ids']],
                 'crease': row['crease'], 'expected_valence': row['expected_valence']}
                for row in manifest['semantic_control_loops']]
    evidence = {'status': 'validated', 'source': 'actual original object.data before evaluation',
        'source_schema': SPARSE_SOURCE_SCHEMA, 'identity_binding_status': native['identity_binding_status'],
        'native_binding': deepcopy(native['binding']), 'external_registry_verified': True,
        'source_face_count': count, 'source_bore_face_count': len(actual_bore),
        'source_surface_table_sha256': fingerprint(table), 'source_parent_slots_sha256': fingerprint(slots),
        'source_surface_labels_sha256': fingerprint(labels), 'parent_surface_by_slot_sha256': fingerprint(by_slot),
        'parent_face_ids_by_slot_sha256': fingerprint(face_ids), 'parent_provenance_by_slot_sha256': fingerprint(provenance),
        'source_neighbour_cycles_sha256': fingerprint(neighbour_cycles),
        'full_per_face_provenance_bound': True, 'surface_labels_are_face_identity': False,
        'resolved_face_provenance_sha256': fingerprint(resolved_provenance), 'authored_bore_region_identity_matches': True,
        'source_bore_labels': bore_labels, 'source_bore_parent_slots': bore_parents,
        'source_bore_face_ids': sorted(authored_bore), 'source_bore_region_id': region['id'],
        'source_bore_annulus': annulus, 'semantic_control_loops': deepcopy(native['semantic_control_loops']),
        'actual_source_modifier': actual_modifier}
    return {'source_schema': SPARSE_SOURCE_SCHEMA, 'source_face_count': count,
            'parent_surface_by_slot': by_slot, 'parent_face_ids_by_slot': list(face_ids),
            'parent_provenance_by_slot': provenance, 'bore_labels': bore_labels, 'bore_parent_slots': bore_parents,
            'source_neighbour_cycles': neighbour_cycles, 'surface_table_count': len(table),
            'authorship': deepcopy(author), 'control_loops': control_loops(author),
            'authored_control_loops': control_loops(original), 'evidence': evidence}


def capture_semantic_source(obj, *, unit_scale, expected_binding=None, require_bound=False, source_schema=None):
    """Authenticate raw source before any evaluated labels can be consumed.

Saved-source qualification supplies its independently stored scene-registry
entry and requires a bound identity. A newly authored object can be inspected
before core assigns IDs, but still undergoes full actual native attestation.
    """
    if require_bound and expected_binding is None:
        _fail('SUBDIVISION_SOURCE_BINDING', 'Source-bound qualification requires an actual external registry entry')
    native = validate_native_structure(obj, unit_scale=unit_scale, expected_binding=expected_binding)
    if native.get('status') != 'pass' or native.get('native_extraction', {}).get('status') != 'pass':
        _fail('SUBDIVISION_SOURCE_BINDING', 'Actual native source attestation failed')
    if require_bound and native.get('identity_binding_status') != 'bound':
        _fail('SUBDIVISION_SOURCE_BINDING', 'Saved source has not been bound to object/data identity and registry')
    if source_schema is not None:
        if source_schema != SPARSE_SOURCE_SCHEMA:
            _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Unsupported explicit source semantic schema')
        return _capture_sparse_source(obj, native, expected_binding)
    actual_modifier = snapshot_source_modifier(obj)
    count = len(obj.data.polygons)
    if not count or any(len(face.vertices) != 4 for face in obj.data.polygons):
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Fixed all-quad source required for exact per-face descendant transport')
    try:
        table = contract.strict_loads(obj.get('hs_quad_surface_table'))
    except (contract.ContractError, TypeError, ValueError) as exc:
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Source semantic table must be strict bounded JSON', reason=str(exc))
    if not isinstance(table, list) or not table or len(table) > count or any(not isinstance(row, dict) for row in table):
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Nonempty bounded semantic provenance table required')
    if len({fingerprint(row) for row in table}) != len(table):
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Duplicate semantic provenance table entries are unsupported')
    bore_labels = [i for i, row in enumerate(table) if row.get('surface_role') == 'bore_wall']
    if len(bore_labels) != 1:
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Exactly one authored bore_wall domain required', matches=len(bore_labels))
    features = {row.get('feature_id') for row in table if isinstance(row.get('feature_id'), str)}
    if len(features) != 1 or any(row.get('feature_id') not in features for row in table) or (obj.get('hs_feature_id') is not None and features != {obj.get('hs_feature_id')}):
        _fail('SUBDIVISION_SEMANTIC_TABLE', 'Foreign or missing source feature in semantic provenance')
    labels = _face_int_values(obj.data, SURFACE_ATTRIBUTE)
    slots = _face_int_values(obj.data, FACE_SLOT)
    if set(slots) != set(range(count)) or len(set(slots)) != count:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Source parent slots must uniquely cover every actual source face')
    if any(not 0 <= value < len(table) for value in labels) or set(labels) != set(range(len(table))):
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Source semantic labels must completely cover the authored table')
    by_slot = [None] * count
    for parent, label in zip(slots, labels): by_slot[parent] = label
    neighbour_cycles = _source_neighbour_cycles(obj.data, slots)
    bore_label = bore_labels[0]
    if labels.count(bore_label) == 0:
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Actual source bore_wall has no faces')
    author_faces = native.get('authorship', {}).get('face_map', {})
    bore_ids = {'bore_wall/sector:%d' % i for i in range(24)}
    author_bore = {author_faces[key] for key in bore_ids if key in author_faces}
    actual_bore = {i for i, label in enumerate(labels) if label == bore_label}
    if (set(key for key in author_faces if key.startswith('bore_wall/')) != bore_ids
            or len(author_bore) != 24 or author_bore != actual_bore):
        _fail('SUBDIVISION_SEMANTIC_SOURCE', 'Actual bore_wall provenance must equal the authenticated 24 author-named bore sectors')
    evidence = {'status': 'validated', 'source': 'actual original object.data before evaluation',
        'identity_binding_status': native['identity_binding_status'], 'native_binding': deepcopy(native['binding']),
        'external_registry_verified': expected_binding is not None, 'source_face_count': count,
        'source_bore_face_count': labels.count(bore_label), 'source_surface_table_sha256': fingerprint(table),
        'authored_bore_sector_identity_matches': True,
        'source_parent_slots_sha256': fingerprint(slots), 'source_surface_labels_sha256': fingerprint(labels),
        'parent_surface_by_slot_sha256': fingerprint(by_slot), 'source_neighbour_cycles_sha256': fingerprint(neighbour_cycles),
        'actual_source_modifier': actual_modifier}
    return {'source_face_count': count, 'parent_surface_by_slot': by_slot, 'bore_label': bore_label,
            'source_neighbour_cycles': neighbour_cycles, 'surface_table_count': len(table), 'evidence': evidence}


def evaluated_bore_domain(mesh, source, *, level):
    """Validate native labels without presuming evaluated-index correspondence."""
    if type(level) is not int or not 0 <= level <= 3:
        _fail('SUBDIVISION_SEMANTIC_LEVEL', 'A supported exact fixed subdivision level is required')
    count = source['source_face_count']; factor = 4 ** level
    if len(mesh.polygons) != count * factor or any(len(face.vertices) != 4 for face in mesh.polygons):
        _fail('SUBDIVISION_SEMANTIC_DESCENDANTS', 'Evaluated all-quad cardinality differs from fixed-level source descendants')
    labels = _face_int_values(mesh, SURFACE_ATTRIBUTE); parents = _face_int_values(mesh, FACE_SLOT)
    if any(not 0 <= value < count for value in parents) or Counter(parents) != Counter({i: factor for i in range(count)}):
        _fail('SUBDIVISION_SEMANTIC_DESCENDANTS', 'Each original face must have exactly 4^level evaluated descendants; missing, duplicate or unknown parent labels rejected')
    if any(not 0 <= label < source['surface_table_count'] or label != source['parent_surface_by_slot'][parent] for parent, label in zip(parents, labels)):
        _fail('SUBDIVISION_SEMANTIC_DESCENDANTS', 'Evaluated surface provenance disagrees with its authenticated original parent face')
    topology = _validate_patch_topology(mesh, parents, source['source_neighbour_cycles'], level)
    if source.get('source_schema') == SPARSE_SOURCE_SCHEMA:
        bore_labels = set(source['bore_labels']); bore_parents = set(source['bore_parent_slots'])
        if any((label in bore_labels) != (parent in bore_parents) for parent, label in zip(parents, labels)):
            _fail('SUBDIVISION_SEMANTIC_DESCENDANTS', 'Evaluated bore labels disagree with authenticated original bore parents')
        bore = [i for i, (parent, label) in enumerate(zip(parents, labels)) if parent in bore_parents and label in bore_labels]
    else:
        bore = [i for i, label in enumerate(labels) if label == source['bore_label']]
    expected_bore = source['evidence']['source_bore_face_count'] * factor
    if not bore or len(bore) != expected_bore:
        _fail('SUBDIVISION_SEMANTIC_DESCENDANTS', 'Evaluated bore domain must contain every and only authored bore descendant')
    evidence = {'status': 'validated', 'measurement_profile': 'semantic_bore_v1', 'level': level,
        'source': deepcopy(source['evidence']), 'evaluated_faces': len(labels), 'evaluated_bore_faces': len(bore),
        'expected_descendants_per_source_face': factor, 'all_source_face_descendant_counts_match': True,
        'parent_surface_labels_match': True, 'parent_patch_topology': topology, 'bore_face_indices_sha256': fingerprint(bore),
        'evaluated_parent_slots_sha256': fingerprint(parents), 'evaluated_surface_labels_sha256': fingerprint(labels),
        'transport': 'actual evaluated FACE INT source slots and surface labels with per-parent counts, connected disk regions and oriented source quotient adjacency; no index-order inference',
        'evidence_limit': 'Native source authentication plus fixed modifier/profile and topology-consistent labels; not an arbitrary reusable evaluated semantic-ID guarantee. Bore union geometry and boundary membership require semantic_bore_v1 measurement.'}
    result = {'bore_face_indices': bore, 'evidence': evidence}
    if source.get('source_schema') == SPARSE_SOURCE_SCHEMA:
        evidence.update(source_schema=SPARSE_SOURCE_SCHEMA, bore_parent_membership_matches=True)
        result.update(parent_slots=parents, surface_labels=labels)
    return result
