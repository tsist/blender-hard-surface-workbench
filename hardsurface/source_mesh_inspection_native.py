# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Blender L0 bridge. No render, evaluation, scene repair or blend save.

Blender is imported only by runtime entry points. Domain extraction verifies
persisted semantic slots and the external scene registry, then rechecks source
and in-memory hashes after analysis. SVG output comes from actual indexed data.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import time

from . import contract as c
from .io import RuntimeFailure, checked_path, descriptor, verify_descriptor
from .source_mesh_inspection import (LIMITS, analyze_mesh, fingerprint, json_bytes,
                                     render_svg, validate_request)
from .structure_native import extract_control_mesh, validate_native_structure


def _fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def _preflight(obj):
    if getattr(obj, 'type', None) != 'MESH' or getattr(obj, 'mode', 'OBJECT') != 'OBJECT':
        _fail('MESH_INSPECTION_TARGET', 'Source inspection requires an object-mode mesh')
    counts = {'vertices': len(obj.data.vertices), 'edges': len(obj.data.edges), 'faces': len(obj.data.polygons)}
    if any(not 0 < count <= LIMITS[key] for key, count in counts.items()):
        _fail('MESH_INSPECTION_LIMIT', 'Actual source domains exceed inspection limits', counts=counts)
    if sum(len(face.vertices) for face in obj.data.polygons) > LIMITS['corners']:
        _fail('MESH_INSPECTION_LIMIT', 'Actual polygon corners exceed inspection limit')
    return counts


def _snapshot(obj, unit_scale):
    extracted = extract_control_mesh(obj, unit_scale=unit_scale)
    edges = [[int(x) for x in edge.vertices] for edge in obj.data.edges]
    data = {'authored': extracted['authored'], 'vertex_map': extracted['vertex_map'],
            'face_map': extracted['face_map'], 'actual_edges': edges,
            'envelope': extracted['envelope'], 'context': extracted['context'],
            'object_id': obj.get('hs_object_id'), 'data_id': obj.data.get('hs_data_id'),
            'feature_id': obj.get('hs_feature_id')}
    return extracted, edges, fingerprint(data)


def inspect_object(obj, *, unit_scale=1.0, expected_binding=None,
                   self_intersections=False, deadline=lambda: None, evidence_origin='blender_raw_object_data'):
    """Reuse after construction or on saved raw source; no process is spawned.

    expected_binding must be supplied for saved-source inspection. Omission is
    permitted for callers inspecting immediately after materialization; the
    report explicitly distinguishes pending registry binding. Semantic transport
    still has to verify all actual POINT/FACE slots, edges and provenance.
    """
    _preflight(obj)
    for key, value, spec in (('feature_id', obj.get('hs_feature_id'), c.IDENT),
                             ('object_id', obj.get('hs_object_id'), c.UUID),
                             ('data_id', obj.data.get('hs_data_id'), c.UUID)):
        try: c._validate(value, spec)
        except c.ContractError as exc:
            _fail('MESH_INSPECTION_IDENTITY', 'Actual object requires valid persistent identities', field=key)
    if type(self_intersections) is not bool:
        _fail('MESH_INSPECTION_INPUT', 'self_intersections must be boolean')
    native = validate_native_structure(obj, unit_scale=unit_scale, expected_binding=expected_binding)
    extracted, edges, before = _snapshot(obj, unit_scale)
    actual = extracted['authored']; envelope = extracted['envelope']
    if any(row.get('feature_id') != obj.get('hs_feature_id') for row in actual['face_provenance']):
        _fail('MESH_INSPECTION_BINDING', 'Actual polygon provenance feature differs from object feature identity')
    identity = {'feature_id': obj.get('hs_feature_id'), 'object_id': obj.get('hs_object_id'),
                'data_id': obj.data.get('hs_data_id'), 'object_name': obj.name}
    try:
        deadline()
        report = analyze_mesh(actual['vertices_mm'], edges, actual['faces'],
            vertex_map=extracted['vertex_map'], face_map=extracted['face_map'],
            face_provenance=actual['face_provenance'],
            semantic_control_loops=envelope['authored_structure']['semantic_control_loops'],
            semantic_chains=envelope['authored_structure'].get('inspection_chains', []),
            identity=identity, evidence_origin=evidence_origin)
        report['checks']['native_extraction'] = 'pass' if evidence_origin=='blender_raw_object_data' else 'protocol_mock_pass'
        report['native_binding'] = deepcopy(native['binding'])
        report['native_binding_status'] = native['identity_binding_status']
        report['external_registry_compared'] = expected_binding is not None
        report['actual_edge_creases'] = [{'edge_index':i, 'crease':float(item.value)} for i,item in enumerate(obj.data.attributes['crease_edge'].data)]
        report['matrix_world'] = deepcopy(extracted['context']['matrix_world'])
        report['modifiers_observed_not_evaluated'] = deepcopy(extracted['context']['modifiers'])
        report['index_semantics'] = 'Original positions in obj.data vertices/edges/polygons; winding unchanged; no tessellation edges'
        if self_intersections:
            if not report['quality']['passed']:
                report['checks']['self_intersections'] = {'status': 'not_run', 'reason': 'Polygon quality failed; intersection audit preconditions were not met'}
            else:
                import bpy
                from .self_intersection import audit_blender_mesh
                temporary = obj.data.copy()
                try:
                    report['checks']['self_intersections'] = audit_blender_mesh(temporary, obj.matrix_world, deadline=deadline)
                    report['checks']['self_intersections']['tessellation_scope'] = 'Temporary copy only; original polygon and edge arrays unchanged'
                finally:
                    bpy.data.meshes.remove(temporary)
        deadline()
    finally:
        _, _, after = _snapshot(obj, unit_scale)
        if before != after:
            _fail('MESH_INSPECTION_SOURCE_CHANGED', 'Actual source geometry, semantic data, identity or context changed during inspection')
    report['native_preservation'] = {'status': 'pass', 'before_sha256': before, 'after_sha256': after,
                                     'scope': 'Raw mesh, original edge order, semantic envelope, object/data identities, matrix and modifier settings',
                                     'source_mesh_modified': False, 'blend_save_performed': False}
    return report


def _output(job_dir, name, encoded, written):
    job = Path(job_dir)
    if not job.is_absolute() or '..' in job.parts or not job.is_dir():
        _fail('MESH_INSPECTION_OUTPUT', 'Existing absolute owned job directory required')
    if not re.fullmatch(r'mesh-inspect-[a-z0-9-]+\.(json|svg)', name):
        _fail('MESH_INSPECTION_OUTPUT', 'Output name must be internally generated')
    for part in (job, *job.parents):
        if part.is_symlink(): _fail('MESH_INSPECTION_OUTPUT', 'Symlink output paths are refused')
    path = checked_path(job/name, exists=False)
    if len(encoded) > LIMITS['json_bytes'] or written[0]+len(encoded) > 128*1024*1024:
        _fail('MESH_INSPECTION_OUTPUT_LIMIT', 'Evidence exceeds bounded output budget')
    if path.exists(): _fail('OUTPUT_COLLISION', 'Inspection evidence already exists', file=str(path))
    with open(path, 'xb') as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    written[0] += len(encoded)
    return descriptor(path)


def execute(request, job_dir):
    """Inspect the already-opened guarded source and emit JSON plus actual SVGs."""
    request = validate_request(request); p = request['params']
    source_before = verify_descriptor(p['source'])
    import bpy
    from .ops.geometry import require_si_scene
    from .core import loaded_structure_registry
    from .topology import _resolve_features, _render_visible_names
    started = time.monotonic()
    def deadline():
        if time.monotonic()-started > p['wall_seconds']:
            _fail('MESH_INSPECTION_DEADLINE', 'Source inspection wall budget exhausted')
    opened_path = checked_path(bpy.data.filepath); opened_before = descriptor(opened_path)
    if opened_before['sha256'] != source_before['sha256']:
        _fail('MESH_INSPECTION_SOURCE', 'Opened source does not match declared file bytes')
    units = require_si_scene(); scene = bpy.context.scene
    if len(scene.objects) > 4096: _fail('MESH_INSPECTION_LIMIT', 'Scene exceeds object bound')
    selected, selection = _resolve_features(list(scene.objects), p.get('feature_ids'), bpy.context.view_layer,
        _render_visible_names(scene.collection), p.get('object_ids'))
    if len(selected) > 32: _fail('MESH_INSPECTION_LIMIT', 'Inspection selects at most 32 actual objects')
    registry = loaded_structure_registry()
    total = {key: 0 for key in ('vertices', 'edges', 'faces')}
    for oid, obj in selected.items():
        for key, count in _preflight(obj).items(): total[key] += count
        if oid not in registry: _fail('MESH_INSPECTION_BINDING', 'Saved source requires external structure registry binding', object_id=oid)
    if any(total[key] > LIMITS[key] for key in total):
        _fail('MESH_INSPECTION_LIMIT', 'Aggregate selected domains exceed limits', counts=total)
    outputs=[]; objects=[]; written=[0]
    original = {oid: _snapshot(obj, units['scale_length'])[2] for oid,obj in selected.items()}
    try:
        for index, (oid, obj) in enumerate(selected.items()):
            report = inspect_object(obj, unit_scale=units['scale_length'], expected_binding=registry[oid],
                                    self_intersections=p['self_intersections'], deadline=deadline)
            report.update(source_sha256=source_before['sha256'], source_semantics='SAVED_DISK_V1')
            detail = _output(job_dir, 'mesh-inspect-%03d-evidence.json'%index, json_bytes(report), written)
            outputs.append({**detail, 'kind': 'source_mesh_inspection', 'object_id': oid, 'mesh_state': 'control'})
            views=[]
            for view in p['views']:
                deadline(); svg=render_svg(report, view)
                ref=_output(job_dir, 'mesh-inspect-%03d-%s.svg'%(index,view['name']), svg.encode('utf-8'), written)
                row={**ref, 'kind': 'actual_source_wire_svg', 'object_id': oid,
                     'region': view['region'], 'direction': view['direction'], 'width': view['width'], 'height': view['height'],
                     'view':deepcopy(view),
                     'mesh_sha256': report['mesh_sha256'], 'source_sha256': source_before['sha256']}
                outputs.append(row); views.append(row)
            objects.append({'identity': report['identity'], 'counts': report['counts'],
                            'mesh_sha256': report['mesh_sha256'], 'semantic_sha256': report['semantic_sha256'],
                            'native_binding': report['native_binding'], 'native_preservation': report['native_preservation'],
                            'checks': report['checks'], 'evidence': detail, 'views': views,
                            'region_face_counts': {k:v['face_count'] for k,v in report['regions'].items()},
                            'valence_histogram': report['valence_histogram'],
                            'regular_closed_loop_count': sum(row['closed'] for row in report['regular_paths']),
                            'quad_strip_count': len(report['quad_strips'])})
        deadline()
    finally:
        source_after = verify_descriptor(p['source']); opened_after = descriptor(opened_path)
        if source_before != source_after or opened_before != opened_after:
            _fail('MESH_INSPECTION_SOURCE_CHANGED', 'Saved source bytes changed during inspection')
        if loaded_structure_registry() != registry or require_si_scene() != units:
            _fail('MESH_INSPECTION_SOURCE_CHANGED', 'Scene structure registry or unit context changed in memory')
        for oid,obj in selected.items():
            if original[oid] != _snapshot(obj, units['scale_length'])[2]:
                _fail('MESH_INSPECTION_SOURCE_CHANGED', 'Selected source mesh or context changed during output generation', object_id=oid)
    return {'schema_version':'1.0', 'operation':'hardsurface.mesh.inspect', 'outcome':'source_mesh_inspected',
            'request_id':p['request_id'], 'source':source_before, 'source_after':source_after,
            'opened_source':opened_before, 'source_sha256':source_before['sha256'], 'source_semantics':'SAVED_DISK_V1',
            'selection':selection, 'objects':objects, 'outputs':outputs, 'aggregate_counts':total,
            'elapsed_seconds':time.monotonic()-started, 'cpu_threads':p['cpu_threads'], 'wall_seconds':p['wall_seconds'],
            'max_tree_rss_bytes':p['max_tree_rss_bytes'], 'output_bytes':written[0],
            'saved_candidate_modified':False, 'source_geometry_modified':False, 'source_transforms_modified':False,
            'blend_save_performed':False, 'render_engine_used':None, 'modifiers_evaluated':False,
            'acceptance':{'inspection_execution':'pass', 'preservation':'pass',
                          'polygon_quality':'pass' if all(row['checks']['polygon_quality']=='pass' for row in objects) else 'fail',
                          'modeling_quality':'not_assigned', 'visual':'not_run', 'user_feedback':'not_run'}}


def build_request_for_construct(run_request, candidate_descriptor):
    """Bind a same-process read to exact just-saved candidate bytes and targets."""
    p=run_request['params']; budgets=p['budgets']
    feature_ids=sorted({fid for unit in p['work_units'] for fid in unit['feature_ids']})
    if not feature_ids: _fail('MESH_INSPECTION_SELECTION', 'Construction work units must identify inspected features')
    request_id='inspect:'+hashlib_request(p['request_id'])
    return validate_request({'schema_version':'1.0','command':'hardsurface.mesh.inspect','params':{
        'request_id':request_id,
        'source':{'file':candidate_descriptor['file'], 'expected_sha256':candidate_descriptor['sha256'], 'bytes':candidate_descriptor['bytes']},
        'feature_ids':feature_ids, 'cpu_threads':min(4,budgets['cpu_threads']),
        'wall_seconds':min(600,budgets['wall_seconds']),
        'max_tree_rss_bytes':max(1024**3,min(4*1024**3,budgets.get('max_observed_rss_bytes',1024**3))),
        'self_intersections':False}})


def hashlib_request(request_id):
    # Keep the follow-on request ID within the same bounded public syntax.
    return fingerprint({'construction_request_id':request_id})[:40]


def execute_construct_inspection(run_request, candidate_descriptor, job_dir):
    return execute(build_request_for_construct(run_request,candidate_descriptor),job_dir)
