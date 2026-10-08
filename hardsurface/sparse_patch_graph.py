# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored sparse quad graph and bounded, whole-body strip transactions.

Logical addresses choose shared vertices. Coordinates only check a declared
identity; no coordinate weld, nearest-neighbour mapping or index-zipped IDs are
used. These host checks do not establish Blender or limit-surface quality.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math

from .io import RuntimeFailure
from .structure_kernel import fingerprint as _canonical_fingerprint


def fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def fingerprint(value):
    return _canonical_fingerprint(value)


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        fail('SPARSE_ID_INVALID', 'A bounded, nonempty semantic address is required')
    return value


def edge_id(a, b):
    return 'edge:' + fingerprint(sorted((_id(a), _id(b))))[:40]


def edge_key(a, b):
    return tuple(sorted((a, b)))


def _cycle(seq):
    seq = list(seq)
    i = seq.index(min(seq))
    return seq[i:] + seq[:i]


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


class SparseMeshBuilder:
    """Small author-time builder. A failed builder must be discarded."""
    def __init__(self, feature_id='panel'):
        self.feature_id = _id(feature_id)
        self.vertices, self.faces, self.provenance, self.ports = [], [], [], []
        self.vertex_map, self.face_map, self.edge_map, self.edges = {}, {}, {}, []
        self._edge_keys = {}

    def vertex(self, semantic_id, coordinates):
        semantic_id = _id(semantic_id)
        if len(coordinates) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in coordinates):
            fail('SPARSE_COORDINATE_INVALID', 'Three finite coordinates are required')
        point = [float(v) for v in coordinates]
        if semantic_id in self.vertex_map:
            i = self.vertex_map[semantic_id]
            if math.dist(self.vertices[i], point) > 1e-9:
                fail('SPARSE_SEAM_MISMATCH', 'One semantic vertex has inconsistent positions', vertex_id=semantic_id)
            return i
        if len(self.vertices) >= 10000:
            fail('SPARSE_BUDGET', 'Sparse candidate vertex budget exceeded')
        i = len(self.vertices)
        self.vertices.append(point)
        self.vertex_map[semantic_id] = i
        return i

    def face(self, semantic_id, vertex_ids, *, region_group, surface_role, curved=False, support=False):
        semantic_id = _id(semantic_id)
        if semantic_id in self.face_map or len(vertex_ids) != 4 or len(set(vertex_ids)) != 4:
            fail('SPARSE_FACE_INVALID', 'Unique face identity and four distinct authored corners required')
        if any(v not in self.vertex_map for v in vertex_ids):
            fail('SPARSE_FACE_INVALID', 'Face references an unauthored vertex')
        ids = [self.vertex_map[v] for v in vertex_ids]
        self.face_map[semantic_id] = len(self.faces)
        self.faces.append(ids)
        self.provenance.append({'feature_id': self.feature_id, 'surface_id': surface_role,
                                'surface_role': surface_role, 'region_group': region_group,
                                'smooth': True, 'curved': curved, 'support_band': support})
        for a, b in zip(vertex_ids, vertex_ids[1:]+vertex_ids[:1]):
            eid = edge_id(a, b)
            key = edge_key(self.vertex_map[a], self.vertex_map[b])
            if eid not in self.edge_map:
                if key in self._edge_keys:
                    fail('SPARSE_EDGE_IDENTITY', 'Conflicting edge identities')
                self.edge_map[eid] = len(self.edges)
                self._edge_keys[key] = eid
                self.edges.append(list(key))

    def port(self, role, vertex_ids, *, crease=0.0):
        if any(row['role'] == role for row in self.ports):
            fail('SPARSE_PORT_DUPLICATE', 'Port roles must be unique')
        if len(vertex_ids) < 4 or len(set(vertex_ids)) != len(vertex_ids) or any(v not in self.vertex_map for v in vertex_ids):
            fail('SPARSE_PORT_INVALID', 'An ordered port requires distinct authored vertex identities')
        self.ports.append({'role': _id(role), 'vertex_ids': list(vertex_ids),
                           'closed': True, 'point_order': 'ccw_positive_z', 'crease': float(crease)})

    def mesh(self):
        return {'vertices_mm': deepcopy(self.vertices), 'faces': deepcopy(self.faces),
                'face_provenance': deepcopy(self.provenance), 'edge_creases': [],
                'sparse_graph': {'schema_version': 'sparse-patch-graph/1.0',
                    'vertex_map': dict(self.vertex_map), 'face_map': dict(self.face_map),
                    'edge_map': dict(self.edge_map), 'edges': deepcopy(self.edges),
                    'ports': deepcopy(self.ports), 'topology_epoch': 0}}


def semantic_mesh(mesh, graph=None):
    graph = mesh['sparse_graph'] if graph is None else graph
    inverse = {i: s for s, i in graph['vertex_map'].items()}
    vertices = mesh.get('vertices_mm', mesh.get('vertices'))
    return {'coordinates': {s: vertices[i] for s, i in graph['vertex_map'].items()},
            'connectivity': {s: _cycle([inverse[v] for v in mesh['faces'][i]]) for s, i in graph['face_map'].items()},
            'creases': sorted([*sorted((inverse[a], inverse[b])), value] for a, b, value in mesh.get('edge_creases', []) if value)}


def validate_sparse_mesh(mesh, *, _allow_unenriched=False):
    """Check actual indices, opposite winding, links, ports and component counts.

    Vertex links must be connected circles, in addition to edge incidence two.
    Nonadjacent triangle intersection remains a separate required geometric
    audit; this routine reports that scope as not_run.
    """
    graph = mesh.get('sparse_graph')
    vertices, faces = mesh.get('vertices_mm'), mesh.get('faces')
    if not isinstance(graph, dict) or not isinstance(vertices, list) or not isinstance(faces, list) or not faces:
        fail('SPARSE_MESH_INVALID', 'Explicit authored mesh and graph required')
    for v in vertices:
        if not isinstance(v, (list, tuple)) or len(v) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in v):
            fail('SPARSE_MESH_INVALID', 'Finite xyz vertices required')
    for key, count in [('vertex_map', len(vertices)), ('face_map', len(faces))]:
        mapping = graph.get(key)
        if not isinstance(mapping, dict) or len(mapping) != count or any(type(i) is not int for i in mapping.values()) or set(mapping.values()) != set(range(count)):
            fail('SPARSE_IDENTITY_COVERAGE', 'Semantic maps must bijectively cover actual arrays', domain=key)
        for sid in mapping:
            _id(sid)
    if set(graph['vertex_map']) & set(graph['face_map']):
        fail('SPARSE_IDENTITY_COVERAGE', 'Vertex and face semantic namespaces must be disjoint')
    if len(mesh.get('face_provenance', [])) != len(faces):
        fail('SPARSE_PROVENANCE', 'Every actual face needs provenance')
    # Duplicate coordinates are rejected, not silently welded. Quantized buckets
    # are only a rejection accelerator; logical identity is already established.
    buckets = defaultdict(list)
    for i, point in enumerate(vertices):
        key = tuple(math.floor(x/1e-8) for x in point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in buckets.get((key[0]+dx, key[1]+dy, key[2]+dz), ()):
                        if math.dist(point, vertices[j]) < 1e-9:
                            fail('SPARSE_UNDECLARED_COINCIDENCE', 'Distinct logical vertices coincide', vertices=[j, i])
        buckets[key].append(i)
    incidence, adjacency, around = defaultdict(list), defaultdict(set), defaultdict(list)
    face_signatures = set()
    volume = 0.0
    for fi, face in enumerate(faces):
        if not isinstance(face, (list, tuple)) or len(face) != 4 or len(set(face)) != 4 or any(type(v) is not int or not 0 <= v < len(vertices) for v in face):
            fail('SPARSE_FACE_INVALID', 'Every face must be a nonrepeating indexed quad')
        key = tuple(sorted(face))
        if key in face_signatures:
            fail('SPARSE_FACE_DUPLICATE', 'Duplicate geometric face')
        face_signatures.add(key)
        pp = [vertices[i] for i in face]
        normals = [_cross(_sub(pp[1], pp[0]), _sub(pp[2], pp[0])),
                   _cross(_sub(pp[2], pp[0]), _sub(pp[3], pp[0]))]
        if min(_dot(n, n) for n in normals) <= 1e-18 or _dot(*normals) <= 0:
            fail('SPARSE_FACE_FOLDED', 'Degenerate or flipped quad triangles', face=fi)
        volume += (_dot(pp[0], _cross(pp[1], pp[2])) + _dot(pp[0], _cross(pp[2], pp[3])))/6
        for j, a in enumerate(face):
            b = face[(j+1) % 4]
            incidence[edge_key(a, b)].append((fi, a, b))
            adjacency[a].add(b); adjacency[b].add(a)
            around[a].append((face[(j-1) % 4], b))
    if set(adjacency) != set(range(len(vertices))):
        fail('SPARSE_LOOSE_VERTEX', 'Loose vertices are not permitted')
    neighbors = defaultdict(set)
    for edge, rows in incidence.items():
        if len(rows) != 2:
            fail('SPARSE_NONMANIFOLD', 'Every closed-body edge requires exactly two faces', edge=list(edge), incidence=len(rows))
        if rows[0][1:] != tuple(reversed(rows[1][1:])):
            fail('SPARSE_WINDING', 'Shared edge face directions must be opposite', edge=list(edge))
        neighbors[rows[0][0]].add(rows[1][0]); neighbors[rows[1][0]].add(rows[0][0])
    def reached(start, network):
        seen, todo = set(), [start]
        while todo:
            a = todo.pop()
            if a not in seen:
                seen.add(a); todo.extend(network[a]-seen)
        return seen
    if len(reached(0, neighbors)) != len(faces):
        fail('SPARSE_DISCONNECTED', 'Exactly one connected shell required')
    for vertex, pairs in around.items():
        link = defaultdict(set)
        for a, b in pairs:
            link[a].add(b); link[b].add(a)
        if any(len(v) != 2 for v in link.values()) or len(reached(next(iter(link)), link)) != len(link):
            fail('SPARSE_VERTEX_LINK', 'Each vertex link must be one circle', vertex=vertex)
    if len(vertices)-len(incidence)+len(faces) != 0:
        fail('SPARSE_EULER', 'One through-hole closed body requires Euler characteristic zero')
    if not math.isfinite(volume) or volume <= 1e-9:
        fail('SPARSE_VOLUME', 'Outward winding and positive volume required')
    inverse = {i: s for s, i in graph['vertex_map'].items()}
    expected_edges = {edge_id(inverse[a], inverse[b]): list((a, b)) for a, b in incidence}
    em, ee = graph.get('edge_map', {}), graph.get('edges', [])
    if set(em) != set(expected_edges) or len(ee) != len(incidence) or set(em.values()) != set(range(len(ee))):
        fail('SPARSE_EDGE_IDENTITY', 'Authored edge identities must cover actual mesh edges')
    if any(ee[i] != expected_edges[s] for s, i in em.items()):
        fail('SPARSE_EDGE_IDENTITY', 'Edge identity endpoints differ from actual connectivity')
    creases = {}
    for row in mesh.get('edge_creases', []):
        if len(row) != 3 or any(type(v) is not int for v in row[:2]) or type(row[2]) not in (int, float) or not math.isfinite(row[2]) or not 0 <= row[2] <= 1:
            fail('SPARSE_CREASE', 'Valid indexed crease required')
        key = edge_key(*row[:2])
        if key not in incidence or key in creases:
            fail('SPARSE_CREASE', 'Crease must identify one unique real edge')
        creases[key] = row[2]
    port_reports, roles = [], set()
    for port in graph.get('ports', []):
        role, ids = port.get('role'), port.get('vertex_ids')
        if role in roles or not isinstance(ids, list) or len(ids) < 4 or len(set(ids)) != len(ids) or any(s not in graph['vertex_map'] for s in ids):
            fail('SPARSE_PORT_INVALID', 'Port identity/order is invalid')
        roles.add(role)
        loop = [graph['vertex_map'][s] for s in ids]
        edge_ids = []
        for a, b in zip(loop, loop[1:]+loop[:1]):
            if edge_key(a, b) not in incidence:
                fail('SPARSE_PORT_DISCONNECTED', 'Port must follow actual connected edges', role=role)
            if creases.get(edge_key(a, b), 0.0) != port.get('crease', 0.0):
                fail('SPARSE_PORT_CREASE', 'Whole ordered port crease differs', role=role)
            edge_ids.append(edge_id(inverse[a], inverse[b]))
        area = sum(vertices[a][0]*vertices[b][1]-vertices[b][0]*vertices[a][1] for a, b in zip(loop, loop[1:]+loop[:1]))
        if port.get('closed') is not True or port.get('point_order') != 'ccw_positive_z' or area <= 0:
            fail('SPARSE_PORT_ORDER', 'Ports require genuine positive-Z ordered cycles', role=role)
        regular = all(len(adjacency[v]) == 4 and not any({loop[(j-1) % len(loop)], loop[(j+1) % len(loop)]} <= set(faces[fi]) for fi, _, _ in incidence[edge_key(v, loop[(j+1) % len(loop)])]) for j, v in enumerate(loop))
        if 'vertex_indices' in port and port['vertex_indices'] != loop:
            fail('SPARSE_PORT_IDENTITY', 'Stored port indices disagree with semantic IDs')
        if 'edge_ids' in port and port['edge_ids'] != edge_ids:
            fail('SPARSE_PORT_IDENTITY', 'Stored port edge identities disagree')
        port_reports.append({'role': role, 'vertices': len(loop), 'regular_edge_loop': regular,
                             'vertex_indices': loop, 'edge_ids': edge_ids})
        if not _allow_unenriched:
            expected=_port_contract(mesh,port)
            if any(port.get(key)!=value for key,value in expected.items()):
                fail('SPARSE_PORT_CONTRACT','Ordered frame, owner or directed adjacent-face side differs from actual port',role=role)
    if 'semantic_inserted_loops' in graph:
        loops = graph['semantic_inserted_loops']
        if not isinstance(loops, list) or len(loops) != graph.get('topology_epoch'):
            fail('SPARSE_AXIS_LOOP', 'Every committed axis epoch requires one current semantic loop')
        loop_roles = set()
        for row in loops:
            if not isinstance(row, dict) or not isinstance(row.get('role'), str) or row.get('role') in loop_roles or row.get('role') in roles or type(row.get('axis_index')) is not int or row.get('axis_index') not in (0, 1):
                fail('SPARSE_AXIS_LOOP', 'Axis cycles need unique non-port roles and explicit X or Y planes')
            loop_roles.add(row['role'])
            actual = _axis_loop(mesh, row['role'], row.get('vertex_ids', []), row['axis_index'], row.get('cut_mm'))
            if row != actual:
                fail('SPARSE_AXIS_LOOP', 'Inserted cycle identity, ordering or regularity proof differs from actual mesh')
    if any(key in graph for key in ('constructor_topology', 'semantic_structural_loops',
                                    'structural_vertex_lineage', 'structural_face_lineage')):
        _validate_corner_structure(mesh)
    groups = Counter(row.get('region_group') for row in mesh['face_provenance'])
    if set(groups) != {'top', 'bottom', 'holewall', 'outer_roundover', 'side'}:
        fail('SPARSE_COMPONENTS', 'Complete top/bottom/holewall/roundover/side provenance required')
    return {'status': 'pass', 'vertices': len(vertices), 'edges': len(incidence), 'faces': len(faces),
            'components': 1, 'boundary_edges': 0, 'euler': 0, 'signed_volume_mm3': volume,
            'region_face_counts': dict(sorted(groups.items())), 'ports': port_reports,
            'vertex_valence_histogram': dict(sorted(Counter(len(v) for v in adjacency.values()).items())),
            'self_intersection': 'not_run', 'native_extraction': 'not_run', 'subdivision_quality': 'not_run'}


def enrich_ports(mesh):
    report = validate_sparse_mesh(mesh,_allow_unenriched=True)
    for port, actual in zip(mesh['sparse_graph']['ports'], report['ports']):
        port.update({key: actual[key] for key in ('vertex_indices', 'edge_ids')})
        port['kind'] = 'regular_edge_loop' if actual['regular_edge_loop'] else 'closed_edge_chain'
        port.update(_port_contract(mesh,port))
    return report


def _port_contract(mesh,port):
    """Attach a verifiable local frame and both directed face sides to a seam."""
    graph=mesh['sparse_graph'];ids=port['vertex_ids'];vm=graph['vertex_map']
    inverse_faces={i:s for s,i in graph['face_map'].items()}
    points=[mesh['vertices_mm'][vm[s]] for s in ids]
    origin=list(points[0]);delta=[b-a for a,b in zip(points[0],points[1])]
    length=math.sqrt(sum(x*x for x in delta))
    if length<=0 or any(p[2]!=origin[2] for p in points):
        fail('SPARSE_PORT_FRAME','This seam family requires a nondegenerate constant-Z ordered cycle',role=port['role'])
    x=[q/length for q in delta];normal=[0.,0.,1.];y=[-x[1],x[0],0.]
    owners={row['feature_id'] for row in mesh['face_provenance']}
    if len(owners)!=1:fail('SPARSE_PORT_OWNER','Sparse seam needs one explicit feature owner')
    incidence=defaultdict(list)
    for fi,face in enumerate(mesh['faces']):
        for a,b in zip(face,face[1:]+face[:1]):incidence[edge_key(a,b)].append((fi,a,b))
    sides=[]
    for sa,sb in zip(ids,ids[1:]+ids[:1]):
        a,b=vm[sa],vm[sb];rows=incidence[edge_key(a,b)]
        if len(rows)!=2:fail('SPARSE_PORT_ARITY','A complete-body seam requires two opposite directed face sides')
        forward=[inverse_faces[fi] for fi,u,v in rows if (u,v)==(a,b)]
        backward=[inverse_faces[fi] for fi,u,v in rows if (u,v)==(b,a)]
        if len(forward)!=1 or len(backward)!=1:fail('SPARSE_PORT_ORDER','Seam face sides do not oppose each other')
        sides.append({'edge_id':edge_id(sa,sb),'with_port_direction_face_id':forward[0],'against_port_direction_face_id':backward[0]})
    return {'port_schema_version':'sparse-port/1.0','owner_feature_id':next(iter(owners)),
            'edge_count':len(ids),'anchor_vertex_id':ids[0],
            'local_frame':{'origin_mm':origin,'x_axis':x,'y_axis':y,'normal':normal},
            'allowed_connections':['shared_index_identity','explicit_reversed_order'],
            'directed_face_sides':sides,
            'frame_scope':'positive_Z cycle frame; surface outward normals remain face-specific'}


def _strip_plan(mesh, corridor, fraction):
    if corridor not in ('east', 'south') or type(fraction) not in (int, float) or not 0.2 <= fraction <= 0.8:
        fail('SPARSE_INSERTION_DOMAIN', 'Only east/south bounded strip insertion at fraction 0.2..0.8 is supported')
    graph = mesh['sparse_graph']
    if graph.get('topology_epoch', 0) != 0:
        fail('SPARSE_INSERTION_DOMAIN', 'Only one insertion from the unedited r2 schedule is supported')
    seed_ids = graph.get('corridor_seeds', {}).get(corridor)
    if not seed_ids:
        fail('SPARSE_INSERTION_SEED', 'No authored corridor seed for this graph')
    inverse = {i: s for s, i in graph['vertex_map'].items()}
    ef = defaultdict(list)
    for fi, face in enumerate(mesh['faces']):
        for a, b in zip(face, face[1:]+face[:1]):
            ef[edge_key(a, b)].append(fi)
    seed = edge_key(*(graph['vertex_map'][s] for s in seed_ids))
    current, previous, visited, steps = seed, None, set(), []
    while True:
        choices = [i for i in ef[current] if i != previous]
        if previous is None:
            choices = sorted(choices, key=lambda i: next(s for s, n in graph['face_map'].items() if n == i))[:1]
        if len(choices) != 1:
            fail('SPARSE_INSERTION_ROUTE', 'Opposite-edge strip route is ambiguous')
        fi = choices[0]
        if fi in visited:
            if current != seed:
                fail('SPARSE_INSERTION_ROUTE', 'Strip re-enters before closing its seed')
            break
        visited.add(fi)
        face = mesh['faces'][fi]
        j = next(j for j in range(4) if edge_key(face[j], face[(j+1) % 4]) == current)
        opposite = edge_key(face[(j+2) % 4], face[(j+3) % 4])
        steps.append({'face_id': next(s for s, n in graph['face_map'].items() if n == fi),
                      'entry_edge_id': edge_id(*(inverse[v] for v in current)),
                      'exit_edge_id': edge_id(*(inverse[v] for v in opposite))})
        previous, current = fi, opposite
        if current == seed:
            break
    split_edges = sorted({row[k] for row in steps for k in ('entry_edge_id', 'exit_edge_id')})
    port_changes = []
    for port in graph['ports']:
        n = sum(edge_id(a, b) in split_edges for a, b in zip(port['vertex_ids'], port['vertex_ids'][1:]+port['vertex_ids'][:1]))
        if n:
            if '.hole_' in port['role']:
                fail('SPARSE_INSERTION_ROUTE', 'This bounded insertion cannot alter hole ports')
            port_changes.append({'role': port['role'], 'before': len(port['vertex_ids']), 'after': len(port['vertex_ids'])+n})
    outer = [row for row in port_changes if row['role'].endswith('outer_flat_boundary')]
    if len(outer) != 2 or any(row['before'] != 32 or row['after'] != 34 for row in outer):
        fail('SPARSE_INSERTION_PROPAGATION', 'Expected both top/bottom outer interfaces to change 32 to 34')
    return {'schema_version': 'sparse-strip-insertion/1.0', 'corridor': corridor, 'fraction': float(fraction),
            'source_sha256': fingerprint(semantic_mesh(mesh)), 'source_topology_epoch': 0,
            'steps': steps, 'split_edge_ids': split_edges, 'port_changes': port_changes,
            'added_vertices': len(split_edges), 'added_faces': len(steps),
            'qualification': 'host_graph_only_not_native_edit_approval'}



AXIS_STRIP_POLICY = 'axis_plane_v1'
AXIS_STRIP_SCHEMA = 'sparse-axis-strip-insertion/1.0'
CORNER_TOPOLOGY_SCHEMA = 'corner-columns-midpoint/1.0'
CORNER_AXIS_REFERENCE_SCHEMA = 'sparse-axis-strip-reference/1.1'
CORNER_LOOP_ROLES = ('structural.corner_column.west', 'structural.corner_column.east')


def _validate_corner_structure(mesh):
    """Validate the one opt-in constructor's actual, non-planar edge cycles."""
    from .corner_column_geometry import structural_loop
    graph = mesh['sparse_graph']
    topology = graph.get('constructor_topology')
    if topology != {'schema': CORNER_TOPOLOGY_SCHEMA, 'base_outer_segments': 36} or type(topology.get('base_outer_segments')) is not int:
        fail('SPARSE_CORNER_STRUCTURE', 'The structural constructor declaration is incomplete or unsupported')
    loops = graph.get('semantic_structural_loops')
    if not isinstance(loops, list) or len(loops) != 2 or any(not isinstance(row, dict) for row in loops) or tuple(row.get('role') for row in loops) != CORNER_LOOP_ROLES:
        fail('SPARSE_CORNER_STRUCTURE', 'The midpoint constructor requires both ordered structural corner cycles')
    occupied = {row['role'] for row in graph['ports']} | {row['role'] for row in graph.get('semantic_inserted_loops', [])}
    for row in loops:
        if row['role'] in occupied or row != structural_loop(mesh, row['role'], row.get('vertex_ids')):
            fail('SPARSE_CORNER_STRUCTURE', 'Structural corner identity or actual regular-cycle proof differs')
    epoch = graph.get('topology_epoch')
    if type(epoch) is not int or not 0 <= epoch <= 2:
        fail('SPARSE_CORNER_STRUCTURE', 'Structural construction does not consume a user axis epoch')
    for port in graph['ports']:
        if '.hole_' not in port['role'] and len(port['vertex_ids']) != 36+2*epoch:
            fail('SPARSE_CORNER_STRUCTURE', 'Midpoint constructor body interfaces require their exact derived cardinality')
    for key in ('structural_vertex_lineage', 'structural_face_lineage'):
        if not isinstance(graph.get(key), dict) or not graph[key]:
            fail('SPARSE_CORNER_STRUCTURE', 'The structural constructor must retain its explicit ancestry', domain=key)
    lineage = graph['structural_vertex_lineage']; vm = graph['vertex_map']
    if len(lineage) != 88:
        fail('SPARSE_CORNER_STRUCTURE', 'Exactly 88 midpoint vertices belong to the two constructor routes')
    memberships = {row['role'].rsplit('.', 1)[-1]: set(row['vertex_ids']) for row in loops}
    for sid, row in lineage.items():
        if not isinstance(row, dict) or set(row) != {'source_edge_id', 'source_endpoint_vertex_ids', 'placement', 'fraction'}:
            fail('SPARSE_CORNER_STRUCTURE', 'Structural midpoint ancestry is incomplete')
        parents = row['source_endpoint_vertex_ids']
        if not isinstance(parents, list) or len(parents) != 2 or any(not isinstance(s, str) or s not in vm or s in lineage for s in parents) or parents != sorted(set(parents)) or row['source_edge_id'] != edge_id(*parents) or row['placement'] != 'arithmetic_edge_midpoint' or row['fraction'] != .5:
            fail('SPARSE_CORNER_STRUCTURE', 'Structural midpoint ancestry must bind two original semantic endpoints')
        routes = [name for name, ids in memberships.items() if sid in ids]
        if len(routes) != 1 or sid != 'vertex:corner_column:'+fingerprint([CORNER_TOPOLOGY_SCHEMA, routes[0], row['source_edge_id']])[:40]:
            fail('SPARSE_CORNER_STRUCTURE', 'Structural vertex identity must follow its stable route and parent edge')
    lineage = graph['structural_face_lineage']; families = defaultdict(set)
    if len(lineage) != 176:
        fail('SPARSE_CORNER_STRUCTURE', 'Both children of every structural route face must retain ancestry')
    for sid, row in lineage.items():
        if not isinstance(row, dict) or set(row) != {'source_face_id', 'route', 'child_index'} or not isinstance(row['source_face_id'], str) or row['route'] not in ('west', 'east') or type(row['child_index']) is not int or row['child_index'] not in (0, 1):
            fail('SPARSE_CORNER_STRUCTURE', 'Structural face ancestry is incomplete or malformed')
        if sid != 'face:corner_column:'+fingerprint([CORNER_TOPOLOGY_SCHEMA, row['route'], row['source_face_id'], row['child_index']])[:40]:
            fail('SPARSE_CORNER_STRUCTURE', 'Structural face identity must follow its stable route and parent face')
        families[(row['route'], row['source_face_id'])].add(row['child_index'])
    if len(families) != 88 or any(children != {0, 1} for children in families.values()) or Counter(route for route, _ in families) != {'west': 44, 'east': 44}:
        fail('SPARSE_CORNER_STRUCTURE', 'Structural lineage must cover two complete 44-face route families')
    return loops


def _axis_domain(corridor, fraction):
    if corridor not in ('east', 'south') or type(fraction) not in (int, float) or not math.isfinite(fraction) or not .2 <= fraction <= .8:
        fail('SPARSE_INSERTION_DOMAIN', 'Axis insertion requires east/south and a finite fraction in 0.2..0.8')


def _axis_trace(mesh, seed_ids):
    """Trace the unique opposite-edge quad cycle from an exact semantic edge."""
    graph = mesh['sparse_graph']
    inverse = {i: s for s, i in graph['vertex_map'].items()}
    inverse_faces = {i: s for s, i in graph['face_map'].items()}
    if len(seed_ids) != 2 or any(s not in graph['vertex_map'] for s in seed_ids):
        fail('SPARSE_INSERTION_SEED', 'Axis seed endpoints must have authored identities')
    ef = defaultdict(list)
    for fi, face in enumerate(mesh['faces']):
        for a, b in zip(face, face[1:]+face[:1]):
            ef[edge_key(a, b)].append(fi)
    seed = edge_key(*(graph['vertex_map'][s] for s in seed_ids))
    if seed not in ef:
        fail('SPARSE_INSERTION_SEED', 'Axis seed must be an actual current edge')
    current, previous, visited, crossed, steps = seed, None, set(), set(), []
    while True:
        if len(ef[current]) != 2 or current in crossed:
            fail('SPARSE_INSERTION_ROUTE', 'Axis strip requires a simple manifold opposite-edge cycle')
        crossed.add(current)
        choices = [i for i in ef[current] if i != previous]
        if previous is None:
            choices = sorted(choices, key=lambda i: inverse_faces[i])[:1]
        if len(choices) != 1 or choices[0] in visited:
            fail('SPARSE_INSERTION_ROUTE', 'Axis strip re-enters or has an ambiguous continuation')
        fi = choices[0]; visited.add(fi); face = mesh['faces'][fi]
        j = next(j for j in range(4) if edge_key(face[j], face[(j+1) % 4]) == current)
        opposite = edge_key(face[(j+2) % 4], face[(j+3) % 4])
        steps.append({'face_id': inverse_faces[fi],
                      'entry_edge_id': edge_id(*(inverse[v] for v in current)),
                      'exit_edge_id': edge_id(*(inverse[v] for v in opposite))})
        previous, current = fi, opposite
        if current == seed:
            if len(steps) < 4:
                fail('SPARSE_INSERTION_ROUTE', 'Axis strip cycle is too short')
            return steps
        if len(steps) >= len(mesh['faces']):
            fail('SPARSE_INSERTION_ROUTE', 'Axis strip did not close within the complete body')


def make_axis_strip_reference(mesh, minimum_spacing_mm):
    """Freeze the authored base intervals before any axis insertion is applied.

    Geometry supplies its already-validated minimum grid spacing. No production
    frame, nominal dimensions, nearest vertex search or coordinate weld is used.
    """
    validate_sparse_mesh(mesh)
    graph = mesh['sparse_graph']
    if graph.get('topology_epoch') != 0 or graph.get('insertion_transaction'):
        fail('SPARSE_AXIS_REFERENCE', 'An axis reference can only be made from the unedited authored base')
    if type(minimum_spacing_mm) not in (int, float) or not math.isfinite(minimum_spacing_mm) or minimum_spacing_mm <= 0:
        fail('SPARSE_AXIS_REFERENCE', 'A positive bound geometry minimum spacing is required')
    corridors, reference_vertex_ids = {}, set()
    for corridor, axis in (('east', 0), ('south', 1)):
        seed = graph.get('corridor_seeds', {}).get(corridor)
        if not isinstance(seed, list) or len(seed) != 2:
            fail('SPARSE_INSERTION_SEED', 'An original authored corridor seed is required')
        matches = []
        for port in graph['ports']:
            if not port['role'].endswith('outer_flat_boundary'):
                continue
            for a, b in zip(port['vertex_ids'], port['vertex_ids'][1:]+port['vertex_ids'][:1]):
                if {a, b} == set(seed): matches.append((port['role'], [a, b]))
        if len(matches) != 1:
            fail('SPARSE_AXIS_REFERENCE', 'Base seed must occur on one explicitly authored outer port')
        steps = _axis_trace(mesh, seed)
        edge_ids = sorted({row['entry_edge_id'] for row in steps})
        intervals = []
        for eid in edge_ids:
            a, b = graph['edges'][graph['edge_map'][eid]]
            intervals.append(sorted((mesh['vertices_mm'][a][axis], mesh['vertices_mm'][b][axis])))
            inverse = {i: sid for sid, i in graph['vertex_map'].items()}
            reference_vertex_ids.update((inverse[a], inverse[b]))
        lo, hi = max(v[0] for v in intervals), min(v[1] for v in intervals)
        if hi-lo <= 2*minimum_spacing_mm:
            fail('SPARSE_AXIS_REFERENCE', 'Authored crossed edges have no sufficiently wide common axis interval', corridor=corridor)
        corridors[corridor] = {'axis_index': axis, 'axis': 'x' if axis == 0 else 'y',
            'common_interval_mm': [lo, hi], 'seed_port_role': matches[0][0],
            'seed_anchor_vertex_ids': matches[0][1], 'base_split_edge_ids': edge_ids}
    result = {'schema_version': 'sparse-axis-strip-reference/1.0', 'policy': AXIS_STRIP_POLICY,
        'minimum_spacing_mm': float(minimum_spacing_mm), 'base_topology_epoch': 0,
        'base_connectivity_sha256': fingerprint(semantic_mesh(mesh)['connectivity']),
        'base_vertex_ids': sorted(graph['vertex_map']),
        'base_vertex_coordinates_mm': {sid: deepcopy(mesh['vertices_mm'][graph['vertex_map'][sid]]) for sid in sorted(reference_vertex_ids)},
        'corridors': corridors}
    if 'constructor_topology' in graph:
        loops = _validate_corner_structure(mesh)
        if any(len(row['vertex_ids']) != 44 for row in loops):
            fail('SPARSE_AXIS_REFERENCE', 'The midpoint constructor base requires two actual 44-vertex structural cycles')
        result.update({'schema_version': CORNER_AXIS_REFERENCE_SCHEMA,
            'constructor_topology': deepcopy(graph['constructor_topology']),
            'base_outer_port_counts': {p['role']: len(p['vertex_ids']) for p in graph['ports'] if '.hole_' not in p['role']},
            'base_structural_loops': deepcopy(loops),
            'base_structural_loops_sha256': fingerprint(loops)})
        reference_vertex_ids.update(sid for row in loops for sid in row['vertex_ids'])
        result['base_vertex_coordinates_mm'] = {sid: deepcopy(mesh['vertices_mm'][graph['vertex_map'][sid]]) for sid in sorted(reference_vertex_ids)}
    result['sha256'] = fingerprint(result)
    return result


def _axis_reference(mesh, axis_reference=None, minimum_spacing_mm=None):
    graph = mesh['sparse_graph']; reference = graph.get('axis_strip_reference')
    if not isinstance(reference, dict) or reference.get('policy') != AXIS_STRIP_POLICY:
        fail('SPARSE_AXIS_REFERENCE', 'The complete graph must carry an explicit frozen axis policy reference')
    required = {'schema_version', 'policy', 'minimum_spacing_mm', 'base_topology_epoch',
                'base_connectivity_sha256', 'base_vertex_ids', 'base_vertex_coordinates_mm', 'corridors', 'sha256'}
    corner_reference = reference.get('schema_version') == CORNER_AXIS_REFERENCE_SCHEMA
    if corner_reference:
        required |= {'constructor_topology', 'base_outer_port_counts', 'base_structural_loops', 'base_structural_loops_sha256'}
    if set(reference) != required or reference['schema_version'] not in ('sparse-axis-strip-reference/1.0', CORNER_AXIS_REFERENCE_SCHEMA) or reference['base_topology_epoch'] != 0:
        fail('SPARSE_AXIS_REFERENCE', 'The frozen axis reference schema is incomplete or unsupported')
    if corner_reference != ('constructor_topology' in graph):
        fail('SPARSE_AXIS_REFERENCE', 'The frozen reference version must match its explicit constructor')
    minimum = reference['minimum_spacing_mm']
    if type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum <= 0:
        fail('SPARSE_AXIS_REFERENCE', 'Frozen minimum spacing must be finite and positive')
    base_ids = reference['base_vertex_ids']; base_points = reference['base_vertex_coordinates_mm']
    if not isinstance(base_ids, list) or any(not isinstance(s, str) for s in base_ids) or len(set(base_ids)) != len(base_ids) or not isinstance(base_points, dict) or not set(base_points) <= set(base_ids):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen base identities or coordinates are malformed')
    if not isinstance(reference['corridors'], dict) or set(reference['corridors']) != {'east', 'south'}:
        fail('SPARSE_AXIS_REFERENCE', 'Both bounded original corridor declarations are required')
    for corridor, axis in (('east', 0), ('south', 1)):
        row = reference['corridors'][corridor]
        fields = {'axis_index', 'axis', 'common_interval_mm', 'seed_port_role', 'seed_anchor_vertex_ids', 'base_split_edge_ids'}
        if not isinstance(row, dict) or set(row) != fields or row['axis_index'] != axis or row['axis'] != ('x' if axis == 0 else 'y'):
            fail('SPARSE_AXIS_REFERENCE', 'Frozen corridor axis declaration is malformed')
        bounds = row['common_interval_mm']; anchors = row['seed_anchor_vertex_ids']
        if not isinstance(bounds, list) or len(bounds) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds) or bounds[1]-bounds[0] <= 2*minimum:
            fail('SPARSE_AXIS_REFERENCE', 'Frozen corridor interval is malformed or too narrow')
        if not isinstance(anchors, list) or len(anchors) != 2 or any(not isinstance(s, str) or s not in base_ids for s in anchors) or anchors[0] == anchors[1] or not isinstance(row['seed_port_role'], str) or not isinstance(row['base_split_edge_ids'], list):
            fail('SPARSE_AXIS_REFERENCE', 'Frozen corridor anchors are malformed')
    payload = {k: v for k, v in reference.items() if k != 'sha256'}
    if reference.get('sha256') != fingerprint(payload):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen axis reference digest differs')
    if axis_reference is not None and axis_reference != reference:
        fail('SPARSE_AXIS_REFERENCE', 'The supplied axis reference differs from the frozen graph reference')
    if minimum_spacing_mm is not None and minimum_spacing_mm != reference['minimum_spacing_mm']:
        fail('SPARSE_AXIS_REFERENCE', 'Minimum spacing cannot change during an insertion sequence')
    coordinates = semantic_mesh(mesh)['coordinates']
    if not set(base_ids) <= set(coordinates) or any(s not in coordinates or coordinates[s] != p for s, p in reference['base_vertex_coordinates_mm'].items()):
        fail('SPARSE_AXIS_SOURCE_CHANGED', 'An original authored vertex moved or lost its exact semantic identity')
    epoch = graph.get('topology_epoch'); history = graph.get('insertion_transactions', [])
    if type(epoch) is not int or epoch < 0 or epoch > 2 or not isinstance(history, list) or len(history) != epoch:
        fail('SPARSE_AXIS_HISTORY', 'Axis history must cover every committed topology epoch')
    if any(not isinstance(tx, dict) for tx in history):
        fail('SPARSE_AXIS_HISTORY', 'Axis history entries must be transaction objects')
    if epoch:
        if graph.get('insertion_transaction') != history[-1] or history[-1].get('result_semantic_sha256') != fingerprint(semantic_mesh(mesh)):
            fail('SPARSE_AXIS_HISTORY', 'Latest transaction must identify this exact current semantic body')
        for index, tx in enumerate(history):
            if not isinstance(tx, dict) or not isinstance(tx.get('plan'), dict) or tx['plan'].get('source_topology_epoch') != index or tx['plan'].get('axis_reference_sha256') != reference['sha256'] or tx['plan'].get('policy') != AXIS_STRIP_POLICY:
                fail('SPARSE_AXIS_HISTORY', 'Transaction lineage differs from the original bound axis reference')
            if index and tx['plan'].get('source_sha256') != history[index-1].get('result_semantic_sha256'):
                fail('SPARSE_AXIS_HISTORY', 'Every transaction must continue the immediate prior semantic result')
    if corner_reference:
        _validate_corner_axis_reference(mesh, reference, history)
    return reference


def _validate_corner_axis_reference(mesh, reference, history):
    """Bind the versioned base cardinality and exact structural-loop ancestry.

    Public source trust still requires canonical parameter-bound reconstruction;
    this narrow branch additionally rejects locally inconsistent frozen data.
    """
    from .corner_column_geometry import structural_loop
    graph = mesh['sparse_graph']; current = _validate_corner_structure(mesh)
    base_ids = reference['base_vertex_ids']; base_points = reference['base_vertex_coordinates_mm']
    digest = reference['base_connectivity_sha256']
    if base_ids != sorted(base_ids) or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen structural base identities and connectivity digest must be canonical')
    if any(not isinstance(p, list) or len(p) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in p) for p in base_points.values()):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen structural base coordinates must be finite xyz triples')
    for declaration in reference['corridors'].values():
        edges = declaration['base_split_edge_ids']
        if not edges or any(not isinstance(eid, str) or not eid.startswith('edge:') or len(eid) != 45 or any(c not in '0123456789abcdef' for c in eid[5:]) for eid in edges) or edges != sorted(set(edges)):
            fail('SPARSE_AXIS_REFERENCE', 'Frozen structural corridor edges must retain their complete canonical identities')
    if reference['constructor_topology'] != graph['constructor_topology']:
        fail('SPARSE_AXIS_REFERENCE', 'Frozen structural constructor differs from the current graph')
    counts = reference['base_outer_port_counts']
    expected_counts = {p['role']: 36 for p in graph['ports'] if '.hole_' not in p['role']}
    if not isinstance(counts, dict) or counts != expected_counts or any(type(n) is not int for n in counts.values()):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen outer cardinalities must be the exact verified midpoint-constructor counts')
    loops = reference['base_structural_loops']
    if not isinstance(loops, list) or len(loops) != 2 or any(not isinstance(row, dict) for row in loops) or tuple(row.get('role') for row in loops) != CORNER_LOOP_ROLES or reference['base_structural_loops_sha256'] != fingerprint(loops):
        fail('SPARSE_AXIS_REFERENCE', 'Frozen structural corner cycles or their digest differ')
    fields = {'role', 'vertex_ids', 'vertex_indices', 'edge_ids', 'crease', 'expected_valence', 'closed', 'point_order', 'proof'}
    proof = {'status': 'pass', 'connected_closed_cycle': True, 'all_vertex_valences': 4,
             'opposite_edge_continuation': True, 'native_extraction': 'not_run'}
    base_ids = set(base_ids)
    for row, actual in zip(loops, current):
        ids = row.get('vertex_ids')
        if set(row) != fields or not isinstance(ids, list) or len(ids) != 44 or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids) or not set(ids) <= base_ids:
            fail('SPARSE_AXIS_REFERENCE', 'Frozen corner loop must retain exactly its 44 distinct base vertices')
        if row['vertex_indices'] != [graph['vertex_map'][s] for s in ids] or row['edge_ids'] != [edge_id(a, b) for a, b in zip(ids, ids[1:]+ids[:1])] or row['crease'] != 0.0 or row['expected_valence'] != 4 or row['closed'] is not True or row['point_order'] != 'semantic_closed_cycle' or row['proof'] != proof:
            fail('SPARSE_AXIS_REFERENCE', 'Frozen corner loop has malformed full identity or proof fields')
        if not set(ids) <= set(reference['base_vertex_coordinates_mm']):
            fail('SPARSE_AXIS_REFERENCE', 'Frozen structural loop coordinates must be bound to the base')
        expanded = list(ids)
        for tx in history:
            mids = tx.get('split_edge_vertex_ids')
            if not isinstance(mids, dict):
                fail('SPARSE_AXIS_HISTORY', 'Structural loop propagation requires every exact split-edge mapping')
            next_ids = []
            for a, b in zip(expanded, expanded[1:]+expanded[:1]):
                next_ids.append(a)
                if edge_id(a, b) in mids:
                    next_ids.append(mids[edge_id(a, b)])
            expanded = next_ids
        if actual != structural_loop(mesh, row['role'], expanded):
            fail('SPARSE_AXIS_REFERENCE', 'Current structural loops must be exact descendants of the frozen base cycles')
    if not history and reference != make_axis_strip_reference(mesh, reference['minimum_spacing_mm']):
        fail('SPARSE_AXIS_REFERENCE', 'Epoch-zero structural reference differs from the actual complete constructor')


def _axis_seed(mesh, declaration, cut, minimum):
    graph = mesh['sparse_graph']; roles = [p for p in graph['ports'] if p['role'] == declaration['seed_port_role']]
    if len(roles) != 1:
        fail('SPARSE_INSERTION_SEED', 'Current graph lost the original seed port')
    ids = roles[0]['vertex_ids']; first, last = declaration['seed_anchor_vertex_ids']
    if first not in ids or last not in ids:
        fail('SPARSE_INSERTION_SEED', 'Original seed anchor identities must remain present')
    start = ids.index(first); chain = [first]
    for offset in range(1, len(ids)):
        sid = ids[(start+offset) % len(ids)]; chain.append(sid)
        if sid == last: break
        if sid in graph['axis_strip_reference']['base_vertex_ids']:
            fail('SPARSE_INSERTION_SEED', 'Original seed anchors no longer delimit the same port subchain')
    if chain[-1] != last:
        fail('SPARSE_INSERTION_SEED', 'Original seed anchor subchain does not close')
    axis = declaration['axis_index']; vm = graph['vertex_map']; matches = []
    for a, b in zip(chain, chain[1:]):
        lo, hi = sorted((mesh['vertices_mm'][vm[a]][axis], mesh['vertices_mm'][vm[b]][axis]))
        if lo < cut < hi:
            if min(cut-lo, hi-cut) < minimum:
                fail('SPARSE_AXIS_SPACING', 'Axis cut is too close to an existing seed endpoint', required_minimum_mm=minimum)
            matches.append([a, b])
    if len(matches) != 1:
        fail('SPARSE_INSERTION_SEED', 'Exactly one current edge in the original anchor subchain must straddle the axis plane')
    return matches[0]


def _axis_strip_plan(mesh, corridor, fraction, *, axis_reference=None, minimum_spacing_mm=None):
    _axis_domain(corridor, fraction)
    validate_sparse_mesh(mesh)
    graph = mesh['sparse_graph']; reference = _axis_reference(mesh, axis_reference, minimum_spacing_mm)
    epoch = graph['topology_epoch']
    if epoch >= 2:
        fail('SPARSE_INSERTION_DOMAIN', 'Axis policy supports two total complete-body insertions')
    declaration = reference['corridors'][corridor]; axis = declaration['axis_index']
    lo, hi = declaration['common_interval_mm']; cut = lo+float(fraction)*(hi-lo)
    minimum = reference['minimum_spacing_mm']
    for tx in graph.get('insertion_transactions', []):
        previous = tx['plan']
        if previous['axis_index'] == axis and abs(previous['cut_mm']-cut) < minimum:
            fail('SPARSE_AXIS_SPACING', 'Duplicate or too-close axis planes are not supported', required_minimum_mm=minimum)
    seed = _axis_seed(mesh, declaration, cut, minimum)
    steps = _axis_trace(mesh, seed)
    split_edges = sorted({row['entry_edge_id'] for row in steps})
    edge_parameters = {}
    inverse = {i: s for s, i in graph['vertex_map'].items()}
    for eid in split_edges:
        a, b = graph['edges'][graph['edge_map'][eid]]
        if inverse[a] > inverse[b]: a, b = b, a
        va, vb = mesh['vertices_mm'][a][axis], mesh['vertices_mm'][b][axis]
        low, high = sorted((va, vb))
        if not low < cut < high:
            fail('SPARSE_AXIS_NONSTRADDLING', 'Every crossed edge must strictly straddle the same axis plane', edge_id=eid)
        if min(cut-low, high-cut) < minimum:
            fail('SPARSE_AXIS_SPACING', 'Axis plane crowds a crossed-edge endpoint', edge_id=eid, required_minimum_mm=minimum)
        edge_parameters[eid] = (cut-va)/(vb-va)
    port_changes = []
    for port in graph['ports']:
        count = sum(edge_id(a, b) in edge_parameters for a, b in zip(port['vertex_ids'], port['vertex_ids'][1:]+port['vertex_ids'][:1]))
        if count:
            base_count = (reference['base_outer_port_counts'].get(port['role'])
                          if reference['schema_version'] == CORNER_AXIS_REFERENCE_SCHEMA else 32)
            if '.hole_' in port['role'] or count != 2 or base_count is None or len(port['vertex_ids']) != base_count+2*epoch:
                fail('SPARSE_INSERTION_PROPAGATION', 'Axis strip must preserve hole ports and add two vertices to each affected body interface')
            port_changes.append({'role': port['role'], 'before': len(port['vertex_ids']), 'after': len(port['vertex_ids'])+count})
    outer = [row for row in port_changes if row['role'].endswith('outer_flat_boundary')]
    if len(outer) != 2 or len(port_changes) != len([p for p in graph['ports'] if '.hole_' not in p['role']]):
        fail('SPARSE_INSERTION_PROPAGATION', 'Axis strip must propagate atomically across every outer body interface')
    return {'schema_version': AXIS_STRIP_SCHEMA, 'policy': AXIS_STRIP_POLICY,
        'corridor': corridor, 'fraction': float(fraction), 'axis_index': axis,
        'axis': declaration['axis'], 'cut_mm': cut,
        'base_common_interval_mm': list(declaration['common_interval_mm']),
        'minimum_spacing_mm': minimum, 'axis_reference_sha256': reference['sha256'],
        'source_sha256': fingerprint(semantic_mesh(mesh)), 'source_graph_sha256': fingerprint(graph),
        'source_topology_epoch': epoch, 'seed_vertex_ids': seed,
        'steps': steps, 'split_edge_ids': split_edges, 'edge_fractions': edge_parameters,
        'port_changes': port_changes, 'added_vertices': len(split_edges), 'added_faces': len(steps),
        'qualification': 'host_graph_only_not_native_edit_approval'}


def _axis_loop(mesh, role, ids, axis, cut):
    """Prove a planar closed cycle with opposite quad edges at regular vertices."""
    graph = mesh['sparse_graph']; vm = graph['vertex_map']
    if not isinstance(ids, list) or len(ids) < 4 or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids) or any(s not in vm for s in ids):
        fail('SPARSE_AXIS_LOOP', 'Inserted loop needs distinct actual semantic vertices')
    loop = [vm[s] for s in ids]
    if any(mesh['vertices_mm'][i][axis] != cut for i in loop):
        fail('SPARSE_AXIS_LOOP', 'Every inserted loop vertex must lie exactly on its declared axis plane')
    u, v = ((1, 2) if axis == 0 else (2, 0))
    points = mesh['vertices_mm']
    area = sum(points[a][u]*points[b][v]-points[b][u]*points[a][v] for a, b in zip(loop, loop[1:]+loop[:1]))
    if not math.isfinite(area) or area == 0:
        fail('SPARSE_AXIS_LOOP', 'Inserted axis cycle must enclose nonzero projected area')
    if area < 0:
        ids = [ids[0]]+list(reversed(ids[1:])); loop = [vm[s] for s in ids]; area = -area
    adjacency, faces_at = defaultdict(set), defaultdict(list)
    for fi, face in enumerate(mesh['faces']):
        for a, b in zip(face, face[1:]+face[:1]):
            adjacency[a].add(b); adjacency[b].add(a)
            faces_at[a].append(fi)
    edge_ids = []
    for j, vertex in enumerate(loop):
        previous, following = loop[j-1], loop[(j+1) % len(loop)]
        if len(adjacency[vertex]) != 4 or previous not in adjacency[vertex] or following not in adjacency[vertex]:
            fail('SPARSE_AXIS_LOOP', 'Inserted cycle vertices require exactly valence four and connected loop edges')
        if any(previous in mesh['faces'][fi] and following in mesh['faces'][fi] for fi in faces_at[vertex]):
            fail('SPARSE_AXIS_LOOP', 'Inserted cycle must traverse opposite, not adjacent, quad edges')
        edge_ids.append(edge_id(ids[j], ids[(j+1) % len(ids)]))
    return {'role': role, 'vertex_ids': list(ids), 'vertex_indices': loop, 'edge_ids': edge_ids,
        'crease': 0.0, 'expected_valence': 4, 'axis_index': axis, 'axis': 'x' if axis == 0 else 'y',
        'cut_mm': cut, 'closed': True, 'point_order': 'positive_axis_projected_cycle',
        'proof': {'status': 'pass', 'connected_closed_cycle': True, 'all_vertex_valences': 4,
                  'opposite_edge_continuation': True, 'exact_axis_plane': True,
                  'positive_projected_twice_area_mm2': area, 'native_extraction': 'not_run'}}


def _apply_axis_strip_insertion(mesh, plan):
    """Build a complete candidate without modifying even one source coordinate."""
    if not isinstance(plan, dict): fail('SPARSE_INSERTION_STALE', 'An exact bound axis plan is required')
    expected = _axis_strip_plan(mesh, plan.get('corridor'), plan.get('fraction'))
    if plan != expected:
        fail('SPARSE_INSERTION_STALE', 'Axis insertion plan, source graph, epoch or frozen reference changed')
    old = mesh['sparse_graph']; inverse = {i: s for s, i in old['vertex_map'].items()}
    inverse_faces = {i: s for s, i in old['face_map'].items()}
    builder = SparseMeshBuilder(mesh['face_provenance'][0]['feature_id'])
    for sid, i in old['vertex_map'].items(): builder.vertex(sid, mesh['vertices_mm'][i])
    mids, endpoints = {}, {}
    token = [AXIS_STRIP_POLICY, plan['source_topology_epoch'], plan['corridor']]
    for eid in plan['split_edge_ids']:
        a, b = old['edges'][old['edge_map'][eid]]
        if inverse[a] > inverse[b]: a, b = b, a
        sid = 'vertex:axis_insert:'+fingerprint(token+[eid])[:40]
        if sid in old['vertex_map']: fail('SPARSE_AXIS_ID_COLLISION', 'Generated axis vertex identity already exists')
        t = plan['edge_fractions'][eid]
        xyz = [x if x == y else (1-t)*x+t*y for x, y in zip(mesh['vertices_mm'][a], mesh['vertices_mm'][b])]
        xyz[plan['axis_index']] = plan['cut_mm']
        builder.vertex(sid, xyz); mids[eid] = sid; endpoints[eid] = [inverse[a], inverse[b]]
    split = {row['face_id']: row for row in plan['steps']}; replacements = {}
    for fi, face in enumerate(mesh['faces']):
        sid = inverse_faces[fi]; ids = [inverse[i] for i in face]; row = mesh['face_provenance'][fi]
        kwargs = {'region_group': row['region_group'], 'surface_role': row['surface_role'], 'curved': row['curved'], 'support': row['support_band']}
        if sid not in split:
            builder.face(sid, ids, **kwargs); continue
        j = next(j for j in range(4) if edge_id(ids[j], ids[(j+1) % 4]) == split[sid]['entry_edge_id'])
        a, b, c, d = ids[j:]+ids[:j]; m, n = mids[edge_id(a, b)], mids[edge_id(c, d)]
        children = ['face:axis_insert:'+fingerprint(token+[sid, k])[:40] for k in (0, 1)]
        if any(child in old['face_map'] for child in children): fail('SPARSE_AXIS_ID_COLLISION', 'Generated axis face identity already exists')
        builder.face(children[0], [a, m, n, d], **kwargs); builder.face(children[1], [m, b, c, n], **kwargs)
        replacements[sid] = children
    def expanded(ids):
        result = []
        for a, b in zip(ids, ids[1:]+ids[:1]):
            result.append(a)
            if edge_id(a, b) in mids: result.append(mids[edge_id(a, b)])
        return result
    for port in old['ports']: builder.port(port['role'], expanded(port['vertex_ids']), crease=port['crease'])
    result = builder.mesh()
    for a, b, weight in mesh['edge_creases']:
        sa, sb = inverse[a], inverse[b]
        if edge_id(sa, sb) in mids: fail('SPARSE_INSERTION_CREASE', 'Axis corridor unexpectedly reaches a creased edge')
        result['edge_creases'].append([builder.vertex_map[sa], builder.vertex_map[sb], weight])
    graph = result['sparse_graph']; graph['topology_epoch'] = old['topology_epoch']+1
    graph['corridor_seeds'] = deepcopy(old['corridor_seeds'])
    graph['axis_strip_reference'] = deepcopy(old['axis_strip_reference'])
    if 'constructor_topology' in old:
        from .corner_column_geometry import structural_loop
        for key in ('constructor_topology', 'structural_vertex_lineage', 'structural_face_lineage'):
            graph[key] = deepcopy(old[key])
        graph['semantic_structural_loops'] = [structural_loop(result, row['role'], expanded(row['vertex_ids']))
                                            for row in old['semantic_structural_loops']]
    enrich_ports(result)
    loops = [_axis_loop(result, row['role'], expanded(row['vertex_ids']), row['axis_index'], row['cut_mm'])
             for row in old.get('semantic_inserted_loops', [])]
    loop = _axis_loop(result, 'inserted.axis_strip.%d.%s'%(graph['topology_epoch'], plan['axis']),
                      [mids[row['entry_edge_id']] for row in plan['steps']], plan['axis_index'], plan['cut_mm'])
    loops.append(loop); graph['semantic_inserted_loops'] = loops
    tx = {'plan': deepcopy(plan), 'vertex_mapping': {s: [s] for s in old['vertex_map']},
        'new_vertex_ids': sorted(mids.values()), 'split_edge_vertex_ids': mids,
        'split_edge_endpoints': endpoints, 'insertion_loop': deepcopy(loop),
        'face_mapping': {s: replacements.get(s, [s]) for s in old['face_map']},
        'edge_mapping': {eid: ([edge_id(inverse[old['edges'][i][0]], mids[eid]), edge_id(mids[eid], inverse[old['edges'][i][1]])] if eid in mids else [eid]) for eid, i in old['edge_map'].items()},
        'new_edge_ids': sorted(set(builder.edge_map)-set(old['edge_map'])),
        'new_face_ids': sorted(set(builder.face_map)-set(old['face_map'])),
        'retired_edge_ids': sorted(set(old['edge_map'])-set(builder.edge_map)),
        'retired_face_ids': sorted(set(old['face_map'])-set(builder.face_map)),
        'result_semantic_sha256': fingerprint(semantic_mesh(result)),
        'commit': 'whole_candidate_only_input_unchanged', 'native_edit': 'not_run'}
    graph['insertion_transactions'] = deepcopy(old.get('insertion_transactions', []))+[tx]
    graph['insertion_transaction'] = deepcopy(tx)
    return result


def plan_strip_insertion(mesh, corridor, *, fraction=0.5):
    parameters = mesh.get('authored_structure', {}).get('parameter_binding', {}).get('parameters', {})
    if 'insertion_policy' in parameters.get('sparse_cage', {}):
        from .sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
        validate_sparse_authored_identity(mesh)
        if parameters['sparse_cage']['insertion_policy'] != AXIS_STRIP_POLICY:
            fail('SPARSE_INSERTION_POLICY', 'The bound insertion policy is unsupported')
        # This bounded HOST transaction deliberately binds physical graph
        # storage, because constructor replay must preserve the submitted plan.
        # Native extraction may use arbitrary Blender storage order: its adapter
        # validates semantic maps, then reconstructs this canonical HOST graph.
        canonical = build_sparse_panel(parameters, feature_id=mesh['face_provenance'][0]['feature_id'], _validate=False)
        if mesh['sparse_graph'] != canonical['sparse_graph']:
            fail('SPARSE_INSERTION_SOURCE_LAYOUT', 'Axis HOST transactions require the exact reconstructed graph storage; submitted source layout cannot be silently rebound')
        return _axis_strip_plan(mesh, corridor, fraction)
    validate_sparse_mesh(mesh)
    return _strip_plan(mesh, corridor, fraction)


def _apply_strip_insertion(mesh, plan, *, preserve_equal_components=False):
    """Pure candidate transaction. Input arrays are never mutated."""
    expected = _strip_plan(mesh, plan.get('corridor'), plan.get('fraction'))
    if plan != expected:
        fail('SPARSE_INSERTION_STALE', 'Insertion plan or source identity changed')
    old = mesh['sparse_graph']; inverse = {i: s for s, i in old['vertex_map'].items()}
    inverse_faces = {i: s for s, i in old['face_map'].items()}
    builder = SparseMeshBuilder(mesh['face_provenance'][0]['feature_id'])
    for sid, i in old['vertex_map'].items():
        builder.vertex(sid, mesh['vertices_mm'][i])
    mids = {}
    for eid in plan['split_edge_ids']:
        a, b = old['edges'][old['edge_map'][eid]]
        # Fraction follows stable semantic endpoint order, never array order.
        if inverse[a] > inverse[b]:
            a, b = b, a
        sid = 'vertex:insert:' + fingerprint([plan['corridor'], eid])[:40]
        t = plan['fraction']
        # The explicit fixed-frame policy keeps shared coordinates exact. The
        # legacy weighted formula remains unchanged for exact HOST reconstruction
        # of earlier saved declarations. The algebraically
        # equivalent weighted sum can round a constant Z and break a bound port
        # plane at non-dyadic insertion fractions; no tolerance widening is used.
        builder.vertex(sid, [x if preserve_equal_components and x == y else (1-t)*x+t*y
                             for x, y in zip(mesh['vertices_mm'][a], mesh['vertices_mm'][b])])
        mids[eid] = sid
    split = {row['face_id']: row for row in plan['steps']}
    replacements = {}
    for fi, face in enumerate(mesh['faces']):
        sid = inverse_faces[fi]; ids = [inverse[i] for i in face]; row = mesh['face_provenance'][fi]
        kwargs = {'region_group': row['region_group'], 'surface_role': row['surface_role'], 'curved': row['curved'], 'support': row['support_band']}
        if sid not in split:
            builder.face(sid, ids, **kwargs)
            continue
        j = next(j for j in range(4) if edge_id(ids[j], ids[(j+1) % 4]) == split[sid]['entry_edge_id'])
        a, b, c, d = ids[j:]+ids[:j]
        m, n = mids[edge_id(a, b)], mids[edge_id(c, d)]
        children = ['face:insert:'+fingerprint([sid, plan['corridor'], k])[:40] for k in (0, 1)]
        builder.face(children[0], [a, m, n, d], **kwargs)
        builder.face(children[1], [m, b, c, n], **kwargs)
        replacements[sid] = children
    for port in old['ports']:
        ids = []
        for a, b in zip(port['vertex_ids'], port['vertex_ids'][1:]+port['vertex_ids'][:1]):
            ids.append(a)
            if edge_id(a, b) in mids:
                ids.append(mids[edge_id(a, b)])
        builder.port(port['role'], ids, crease=port['crease'])
    result = builder.mesh()
    for a, b, weight in mesh['edge_creases']:
        sa, sb = inverse[a], inverse[b]
        if edge_id(sa, sb) in mids:
            fail('SPARSE_INSERTION_CREASE', 'Bounded corridor unexpectedly reaches a creased edge')
        result['edge_creases'].append([builder.vertex_map[sa], builder.vertex_map[sb], weight])
    result['sparse_graph']['topology_epoch'] = 1
    result['sparse_graph']['corridor_seeds'] = deepcopy(old['corridor_seeds'])
    result['sparse_graph']['insertion_transaction'] = {'plan': deepcopy(plan),
        'vertex_mapping': {s: [s] for s in old['vertex_map']},
        'new_vertex_ids': sorted(mids.values()),
        'face_mapping': {s: replacements.get(s, [s]) for s in old['face_map']},
        'edge_mapping': {eid: ([edge_id(inverse[old['edges'][i][0]], mids[eid]), edge_id(mids[eid], inverse[old['edges'][i][1]])] if eid in mids else [eid]) for eid, i in old['edge_map'].items()},
        'new_edge_ids': sorted(set(builder.edge_map)-set(old['edge_map'])),
        'new_face_ids': sorted(set(builder.face_map)-set(old['face_map'])),
        'retired_edge_ids': sorted(set(old['edge_map'])-set(builder.edge_map)),
        'retired_face_ids': sorted(set(old['face_map'])-set(builder.face_map)),
        'commit': 'whole_candidate_only_input_unchanged', 'native_edit': 'not_run'}
    enrich_ports(result)
    return result


def apply_strip_insertion(mesh, plan):
    """Reconstruct the complete candidate with the insertion bound to parameters."""
    from .sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
    validate_sparse_authored_identity(mesh)
    if plan != plan_strip_insertion(mesh, plan.get('corridor'), fraction=plan.get('fraction')):
        fail('SPARSE_INSERTION_STALE', 'Only the exact current dry-run plan may commit')
    parameters = deepcopy(mesh['authored_structure']['parameter_binding']['parameters'])
    configuration = parameters.setdefault('sparse_cage', {})
    if configuration.get('insertion_policy') == AXIS_STRIP_POLICY:
        configuration['insertions'] = configuration.get('insertions', [])+[{'corridor': plan['corridor'], 'fraction': plan['fraction']}]
    else:
        configuration['insertions'] = [{'corridor': plan['corridor'], 'fraction': plan['fraction']}]
    result = build_sparse_panel(parameters, feature_id=mesh['face_provenance'][0]['feature_id'])
    if configuration.get('insertion_policy') == AXIS_STRIP_POLICY and result['sparse_graph']['insertion_transaction']['plan'] != plan:
        fail('SPARSE_INSERTION_SOURCE_LAYOUT', 'Reconstruction cannot replace the exact submitted axis transaction plan')
    return result
