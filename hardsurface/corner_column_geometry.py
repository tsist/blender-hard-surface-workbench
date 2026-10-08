# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded midpoint corner columns for the explicit sparse constructor.

The two regular cycles are constructor topology, before any user axis cut.
Placement is solely the arithmetic midpoint of declared semantic parent edges;
there is no fitting, proposal replay, old-body migration or native acceptance.
"""
from collections import defaultdict
from copy import deepcopy

from .sparse_patch_graph import (SparseMeshBuilder, _axis_trace, edge_id,
    enrich_ports, fail, fingerprint, semantic_mesh, validate_sparse_mesh)

SCHEMA = 'corner-columns-midpoint/1.0'
CONSTRUCTOR_SCHEMA = 'quad.panel/sparse_control_cage-corner-columns-midpoint/1.0'
SEEDS = {
    'west': ['vertex:top.outer_flat_boundary/side:0/sample:0',
             'vertex:top.outer_flat_boundary/side:0/sample:1'],
    'east': ['vertex:top.outer_flat_boundary/side:0/sample:8',
             'vertex:top.outer_flat_boundary/side:1/sample:0'],
}


def structural_loop(mesh, role, ids):
    """Prove an actual closed regular opposite-edge cycle, without a plane claim."""
    graph = mesh['sparse_graph']; vm = graph['vertex_map']
    if (not isinstance(ids, list) or len(ids) < 4 or
            any(not isinstance(s, str) for s in ids) or
            len(set(ids)) != len(ids) or any(s not in vm for s in ids)):
        fail('CORNER_LOOP', 'Corner cycle requires distinct actual semantic vertices')
    indices = [vm[s] for s in ids]
    adjacency, faces_at = defaultdict(set), defaultdict(list)
    for fi, face in enumerate(mesh['faces']):
        for a, b in zip(face, face[1:]+face[:1]):
            adjacency[a].add(b); adjacency[b].add(a); faces_at[a].append(fi)
    for j, index in enumerate(indices):
        previous, following = indices[j-1], indices[(j+1) % len(indices)]
        if (len(adjacency[index]) != 4 or previous not in adjacency[index] or
                following not in adjacency[index] or
                any(previous in mesh['faces'][fi] and following in mesh['faces'][fi]
                    for fi in faces_at[index])):
            fail('CORNER_LOOP', 'Corner column must be a closed valence-four opposite-edge loop')
    edges = [edge_id(ids[j], ids[(j+1) % len(ids)]) for j in range(len(ids))]
    if any(eid not in graph['edge_map'] for eid in edges):
        fail('CORNER_LOOP', 'Corner cycle edges must exist in the actual mesh')
    return {'role': role, 'vertex_ids': list(ids), 'vertex_indices': indices,
        'edge_ids': edges, 'crease': 0.0, 'expected_valence': 4, 'closed': True,
        'point_order': 'semantic_closed_cycle',
        'proof': {'status': 'pass', 'connected_closed_cycle': True,
                  'all_vertex_valences': 4, 'opposite_edge_continuation': True,
                  'native_extraction': 'not_run'}}


def _expand_cycle(ids, mids):
    result = []
    for a, b in zip(ids, ids[1:]+ids[:1]):
        result.append(a)
        if edge_id(a, b) in mids:
            result.append(mids[edge_id(a, b)])
    return result


def apply_corner_columns(mesh):
    """Construct two complete 44-node midpoint loops on the raw outer32 base."""
    before = validate_sparse_mesh(mesh)
    old = mesh['sparse_graph']
    if (old.get('topology_epoch') != 0 or 'axis_strip_reference' in old or
            'constructor_topology' in old or 'authored_structure' in mesh or
            any(len(p['vertex_ids']) != (16 if '.hole_' in p['role'] else 32)
                for p in old['ports'])):
        fail('CORNER_BASE_DOMAIN', 'Corner construction requires an unbound epoch-zero outer32 raw body')
    vm, fm = old['vertex_map'], old['face_map']
    inverse = {index: sid for sid, index in vm.items()}
    inverse_faces = {index: sid for sid, index in fm.items()}
    top = next(p for p in old['ports'] if p['role'] == 'top.outer_flat_boundary')
    routes, split, edge_routes = {}, {}, {}
    for name, seed in SEEDS.items():
        if not all(s in top['vertex_ids'] for s in seed) or edge_id(*seed) not in top['edge_ids']:
            fail('CORNER_SEED', 'The semantic corner seed must belong to the top outer port')
        steps = _axis_trace(mesh, seed)
        route_faces = {row['face_id'] for row in steps}
        route_edges = {row['entry_edge_id'] for row in steps}
        if len(steps) != 44 or len(route_edges) != 44:
            fail('CORNER_ROUTE_DOMAIN', 'Each declared structural corner route must contain 44 faces and edges')
        if set(split) & route_faces or set(edge_routes) & route_edges:
            fail('CORNER_ROUTE_OVERLAP', 'Structural corner routes must be mutually disjoint')
        if any(mesh['face_provenance'][fm[s]]['region_group'] == 'holewall' or
               'hole' in mesh['face_provenance'][fm[s]]['surface_role'] for s in route_faces):
            fail('CORNER_HOLE_DOMAIN', 'Structural corner routes may not enter the hole domain')
        routes[name] = steps
        for row in steps:
            split[row['face_id']] = (name, row)
        edge_routes.update((eid, name) for eid in route_edges)

    builder = SparseMeshBuilder(mesh['face_provenance'][0]['feature_id'])
    for sid, index in sorted(vm.items(), key=lambda row: row[1]):
        builder.vertex(sid, mesh['vertices_mm'][index])
    mids, vertex_lineage = {}, {}
    for eid in sorted(edge_routes):
        a, b = old['edges'][old['edge_map'][eid]]
        parents = sorted((inverse[a], inverse[b]))
        sid = 'vertex:corner_column:'+fingerprint([SCHEMA, edge_routes[eid], eid])[:40]
        if sid in vm:
            fail('CORNER_ID_COLLISION', 'Generated corner vertex identity already exists')
        builder.vertex(sid, [(x+y)/2 for x, y in zip(mesh['vertices_mm'][a], mesh['vertices_mm'][b])])
        mids[eid] = sid
        vertex_lineage[sid] = {'source_edge_id': eid, 'source_endpoint_vertex_ids': parents,
                              'placement': 'arithmetic_edge_midpoint', 'fraction': .5}

    face_lineage, deferred = {}, []
    def add_face(sid, ids, provenance):
        builder.face(sid, ids, region_group=provenance['region_group'],
            surface_role=provenance['surface_role'], curved=provenance['curved'],
            support=provenance['support_band'])
        builder.provenance[-1] = deepcopy(provenance)
    for fi, face in enumerate(mesh['faces']):
        sid = inverse_faces[fi]; ids = [inverse[v] for v in face]
        provenance = mesh['face_provenance'][fi]
        if sid not in split:
            add_face(sid, ids, provenance); continue
        name, step = split[sid]
        j = next(j for j in range(4) if edge_id(ids[j], ids[(j+1) % 4]) == step['entry_edge_id'])
        a, b, c, d = ids[j:]+ids[:j]
        m, n = mids[edge_id(a, b)], mids[edge_id(c, d)]
        children = ['face:corner_column:'+fingerprint([SCHEMA, name, sid, k])[:40] for k in (0, 1)]
        if any(child in fm for child in children):
            fail('CORNER_ID_COLLISION', 'Generated corner face identity already exists')
        add_face(children[0], [a, m, n, d], provenance)
        deferred.append((children[1], [m, b, c, n], provenance))
        for k, child in enumerate(children):
            face_lineage[child] = {'source_face_id': sid, 'route': name, 'child_index': k}
    for args in deferred:
        add_face(*args)
    for port in old['ports']:
        builder.port(port['role'], _expand_cycle(port['vertex_ids'], mids), crease=port['crease'])
    result = builder.mesh(); graph = result['sparse_graph']
    for a, b, weight in mesh['edge_creases']:
        sa, sb = inverse[a], inverse[b]
        if edge_id(sa, sb) in mids:
            fail('CORNER_CREASE', 'Structural corner routes may not reach a creased edge')
        result['edge_creases'].append([builder.vertex_map[sa], builder.vertex_map[sb], weight])
    graph['corridor_seeds'] = deepcopy(old['corridor_seeds'])
    graph['constructor_topology'] = {'schema': SCHEMA, 'base_outer_segments': 36}
    graph['structural_vertex_lineage'] = vertex_lineage
    graph['structural_face_lineage'] = face_lineage
    graph['semantic_structural_loops'] = [structural_loop(result, 'structural.corner_column.'+name,
        [mids[step['entry_edge_id']] for step in steps]) for name, steps in routes.items()]
    enrich_ports(result)
    after = validate_sparse_mesh(result)
    if (after['vertices']-before['vertices'] != 88 or after['faces']-before['faces'] != 88 or
            any(len(p['vertex_ids']) != (16 if '.hole_' in p['role'] else 36) for p in graph['ports'])):
        fail('CORNER_COUNT', 'Actual complete corner topology differs from the declared bounded family')
    original, current = semantic_mesh(mesh), semantic_mesh(result)
    if any(current['coordinates'].get(sid) != point for sid, point in original['coordinates'].items()):
        fail('CORNER_SOURCE_MUTATION', 'Corner construction must preserve every original coordinate')
    return result
