# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert authored patch provenance to independently checked structure data.

The existing constructor can supply its raw vertices_mm/faces/face_provenance
output. This adapter does not call it, enable an add-on, infer nearest-point
identity, construct geometry, or inherit its old visual/technical status.
Semantic vertex and face maps must come from a caller-owned authored identity
schedule; absent maps are a hard failure, not raw-index IDs in disguise.
"""
from collections import defaultdict, Counter
from copy import deepcopy
from .structure_kernel import (StructureError, _id, _keys, _list, _vector, _index_map,
                               _edge, _normal, _norm, _dot, _sub, _cross, fingerprint,
                               validate_structure)


def adapt_authored_mesh(authored, *, vertex_map, face_map, tolerances):
    """Read-only extraction for supplied mesh arrays, with explicit loss report.

    The output is a data witness, not a native Blender extraction claim. Curved
    region boundaries keep cycles but receive no planar boundary_port. An
    application needing those ports must reject this loss and implement a new
    port family; it must never flatten the geometry silently.
    """
    if not isinstance(authored, dict) or not {'vertices_mm', 'faces', 'face_provenance'} <= set(authored):
        raise StructureError('AUTHORED_MESH_INVALID', 'Expected explicit vertices_mm/faces/face_provenance arrays')
    mesh = {'vertices': deepcopy(authored['vertices_mm']), 'faces': deepcopy(authored['faces']), 'edge_creases': deepcopy(authored.get('edge_creases', []))}
    vertices = [_vector(v, 'vertex') for v in _list(mesh['vertices'], 'vertices')]
    faces = _list(mesh['faces'], 'faces')
    vm = _index_map(vertex_map, len(vertices), 'vertex_map')
    fm = _index_map(face_map, len(faces), 'face_map')
    inverse_v, inverse_f = {i:k for k,i in vm.items()}, {i:k for k,i in fm.items()}
    provenance = _list(authored['face_provenance'], 'face provenance')
    if len(provenance) != len(faces):
        raise StructureError('AUTHORED_PROVENANCE_INVALID', 'Every face requires one provenance row')
    for i, face in enumerate(faces):
        if not isinstance(face, (list, tuple)) or len(face) not in (3, 4) or any(type(v) is not int or v < 0 or v >= len(vertices) for v in face) or len(set(face)) != len(face):
            raise StructureError('AUTHORED_MESH_INVALID', 'Face indices or degree are invalid', face_index=i)
    _keys(tolerances, ('position_mm', 'unit_vector', 'area_mm2'), label='tolerances')
    # Validate numeric tolerance shapes before using them in projection checks.
    from .structure_kernel import _number
    eps = _number(tolerances['position_mm'], 'position_mm', True)
    area_eps = _number(tolerances['area_mm2'], 'area_mm2', True)
    groups = defaultdict(list)
    for i, row in enumerate(provenance):
        if not isinstance(row, dict): raise StructureError('AUTHORED_PROVENANCE_INVALID', 'Provenance must be an object')
        feature, surface = _id(row.get('feature_id')), _id(row.get('surface_id'))
        groups[(feature, surface)].append(i)
    structure = {'schema_version':'1.0','length_unit':'mm','vertex_map':vm,'face_map':fm,'regions':[],'loops':[],'boundary_ports':[],'tolerances':deepcopy(tolerances)}
    losses = []
    for (feature, surface), members in sorted(groups.items()):
        edges = defaultdict(list)
        graph = defaultdict(set)
        for i in members:
            seq = list(faces[i])
            for a,b in zip(seq,seq[1:]+seq[:1]): edges[_edge(a,b)].append((i,a,b))
        for rows in edges.values():
            if len(rows)>2:raise StructureError('AUTHORED_REGION_INVALID','Authored region has nonmanifold edges')
            if len(rows)==2:
                a,b=rows[0][0],rows[1][0];graph[a].add(b);graph[b].add(a)
        remaining=set(members)
        for start in sorted(members,key=lambda i:inverse_f[i]):
            if start not in remaining: continue
            if len(structure['regions']) >= 10000:
                raise StructureError('STRUCTURE_BUDGET_EXCEEDED', 'Too many authored connected regions')
            todo=[start];component=set()
            while todo:
                i=todo.pop()
                if i in component:continue
                component.add(i);todo.extend(graph[i]-component)
            remaining-=component
            # Component identity uses authored semantic face IDs, not vertex coordinates.
            rid='region:'+fingerprint([feature,surface,min(inverse_f[i] for i in component)])[:24]
            component_edges=defaultdict(list)
            for i in component:
                seq=list(faces[i])
                for a,b in zip(seq,seq[1:]+seq[:1]):component_edges[_edge(a,b)].append((a,b))
            boundary=[rows[0] for rows in component_edges.values() if len(rows)==1]
            outgoing, incoming={},Counter()
            for a,b in boundary:
                if a in outgoing:raise StructureError('AUTHORED_BOUNDARY_AMBIGUOUS','Boundary branches; no implicit repair')
                outgoing[a]=b;incoming[b]+=1
            if set(outgoing)!=set(incoming) or any(v!=1 for v in incoming.values()):
                raise StructureError('AUTHORED_BOUNDARY_AMBIGUOUS','Boundary must be disjoint directed cycles')
            loops=[];unused=set(outgoing)
            for anchor in sorted(unused,key=lambda i:inverse_v[i]):
                if anchor not in unused: continue
                if len(structure['loops']) >= 10000:
                    raise StructureError('STRUCTURE_BUDGET_EXCEEDED', 'Too many authored boundary loops')
                seq=[];v=anchor
                while v in unused:
                    unused.remove(v);seq.append(v);v=outgoing[v]
                if v!=anchor or len(seq)<3:
                    raise StructureError('AUTHORED_BOUNDARY_AMBIGUOUS','Boundary enters another cycle')
                ids=[inverse_v[i] for i in seq]
                lid='loop:'+fingerprint([rid,ids[0]])[:24]
                points=[vertices[i] for i in seq];normal=_normal(points);length=_norm(normal)
                if length<=2*area_eps:
                    raise StructureError('AUTHORED_BOUNDARY_UNSUPPORTED','Cycle has no usable projected orientation')
                normal=[x/length for x in normal]
                structure['loops'].append({'id':lid,'role':surface,'vertex_ids':ids,'closed':True,'normal':normal,'anchor':{'vertex_id':ids[0],'position_mm':list(points[0])}})
                loops.append(lid)
                if all(abs(_dot(_sub(p,points[0]),normal))<=eps for p in points):
                    tangent=_sub(points[1],points[0]);length=_norm(tangent)
                    if length<=eps:raise StructureError('AUTHORED_BOUNDARY_UNSUPPORTED','Boundary starts with a degenerate edge')
                    tangent=[x/length for x in tangent];y=list(_cross(normal,tangent))
                    structure['boundary_ports'].append({'id':'port:'+fingerprint(lid)[:24],'loop_id':lid,'region_id':rid,'point_order':ids[:],'frame':{'origin_mm':list(points[0]),'x_axis':tangent,'y_axis':y,'normal':normal},'allowed_transitions':['identity']})
                else:
                    losses.append({'code':'NONPLANAR_PORT_NOT_IMPLEMENTED','loop_id':lid,'impact':'No port created; operations requiring this interface must stop'})
            structure['regions'].append({'id':rid,'role':surface,'feature_id':feature,'face_ids':sorted(inverse_f[i] for i in component),'boundary_loop_ids':loops})
    report=validate_structure(mesh,structure)
    return {'mesh':mesh,'structure':structure,'validation':report,'losses':losses,
            'identity_provenance':'caller_supplied_authored_semantic_maps',
            'legacy_control_loop_checks':'not_inherited; original control_loops remain separate evidence',
            'native_extraction':'not_run','qualification':'not_run'}
