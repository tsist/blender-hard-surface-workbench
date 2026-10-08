# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify source-inspection files against separately captured native witnesses."""
from collections import defaultdict
import hashlib
import struct
from pathlib import Path

from .io import RuntimeFailure,read_json_reference,verify_descriptor
from .source_mesh_inspection import analyze_mesh,render_svg,fingerprint,DEFAULT_VIEWS,VIEW
from .contract import _validate
from .contract import fingerprint as domain_fingerprint


def _fail(message):
    raise RuntimeFailure('SOURCE_INSPECTION_EVIDENCE',message)


def verify_construction_inspection(report,*,root,native_reports,control_quality):
    inspection=report.get('source_mesh_inspection')
    if not isinstance(inspection,dict):_fail('Construction inspection is required')
    candidate=report.get('candidate',{});sha=candidate.get('sha256')
    if (inspection.get('operation')!='hardsurface.mesh.inspect'
            or inspection.get('source_sha256')!=sha
            or inspection.get('source')!=candidate or inspection.get('source_after')!=candidate
            or inspection.get('opened_source')!=candidate
            or inspection.get('source_semantics')!='SAVED_DISK_V1'
            or any(inspection.get('acceptance',{}).get(key)!='pass' for key in ('inspection_execution','preservation','polygon_quality'))
            or any(inspection.get(key) is not False for key in ('saved_candidate_modified','source_geometry_modified','source_transforms_modified','blend_save_performed','modifiers_evaluated'))
            or inspection.get('render_engine_used') is not None):
        _fail('Inspection must be read-only and bound to the exact saved candidate')
    objects=inspection.get('objects');outputs=inspection.get('outputs')
    if not isinstance(objects,list) or not 1<=len(objects)<=32 or not isinstance(outputs,list) or not 1<=len(outputs)<=128:
        _fail('Inspection object/output scope is missing or oversized')
    objects_by_id={row.get('identity',{}).get('object_id'):row for row in objects if isinstance(row,dict)}
    expected_ids={row['object_id'] for row in report.get('checks',{}).get('structure_identity',{}).get('evidence',[])}
    if len(objects_by_id)!=len(objects) or not expected_ids or set(objects_by_id)!=expected_ids or not expected_ids<=set(native_reports):
        _fail('Inspection must cover exactly all construction structure-check targets')
    grouped=defaultdict(list);refs={};files=set()
    for index,item in enumerate(outputs):
        if not isinstance(item,dict) or item.get('object_id') not in expected_ids or not {'file','sha256','bytes'}<=set(item):
            _fail('Output has unknown object or missing file identity')
        ref={k:item[k] for k in ('file','sha256','bytes')}
        path=Path(ref['file'])
        if root is not None and not path.resolve().is_relative_to(Path(root).resolve()):_fail('Inspection output escaped the owned job')
        if ref['file'] in files:_fail('Duplicate inspection output')
        files.add(ref['file']);verify_descriptor(ref,expected=False)
        refs['source_inspection_%03d'%index]=ref;grouped[item['object_id']].append(item)
    required_views=[_validate(v,VIEW) for v in DEFAULT_VIEWS]
    for oid,summary in objects_by_id.items():
        native=native_reports[oid];binding=native['binding']
        from .structure_edit import _report,_verify_geometric_records
        _report(native,'Checkpoint source')
        _verify_geometric_records(native['witness'])
        if (control_quality.get(oid,{}).get('self_intersections',{}).get('status')!='pass'
                or control_quality.get(oid,{}).get('passed') is not True):
            _fail('Construction requires actual control polygon and distinct-face intersection checks')
        parts=grouped[oid];details=[x for x in parts if x.get('kind')=='source_mesh_inspection'];views=[x for x in parts if x.get('kind')=='actual_source_wire_svg']
        if len(details)!=1 or len(views)!=len(required_views) or len(parts)!=1+len(views):
            _fail('Each object requires exactly one native JSON and the complete declared source views')
        ref={k:details[0][k] for k in ('file','sha256','bytes')};detail=read_json_reference(ref,root=root)
        if summary.get('evidence')!=ref or summary.get('views')!=views:_fail('Output summary does not bind the exact object files')
        preservation=detail.get('native_preservation',{})
        if (detail.get('source_sha256')!=sha or detail.get('operation')!='hardsurface.mesh.inspect'
                or detail.get('mesh_state')!='L0_control' or detail.get('evidence_origin')!='blender_raw_object_data'
                or detail.get('native_binding')!=binding or summary.get('native_binding')!=binding
                or detail.get('native_binding_status')!='bound' or detail.get('external_registry_compared') is not True
                or detail.get('identity')!=summary.get('identity')
                or detail.get('identity',{}).get('object_id')!=oid
                or preservation.get('status')!='pass' or not preservation.get('before_sha256')
                or preservation.get('before_sha256')!=preservation.get('after_sha256')
                or preservation.get('source_mesh_modified') is not False or preservation.get('blend_save_performed') is not False
                or summary.get('native_preservation')!=preservation
                or any(detail.get('checks',{}).get(key)!='pass' for key in ('input_binding','actual_edge_face_coverage','native_extraction','polygon_quality','declared_regular_loops'))
                or detail.get('quality',{}).get('passed') is not True):
            _fail('Actual JSON detail contains failed, absent or inconsistent native source evidence')
        raw=detail.get('actual_mesh',{});sem=detail.get('semantics',{});witness=native['witness'];maps=witness['structure']
        if raw.get('vertices_mm')!=witness['mesh']['vertices'] or raw.get('faces')!=witness['mesh']['faces']:
            _fail('Inspection arrays differ from separately captured actual native structure')
        quality=control_quality[oid]
        matrix=detail.get('matrix_world')
        context={'coordinate_space':'object_local_mm','scale_length':1.0,'matrix_world':matrix,
                 'modifiers':detail.get('modifiers_observed_not_evaluated')}
        from .structure_kernel import fingerprint as structural_fingerprint
        if structural_fingerprint(context)!=binding['context_sha256']:
            _fail('Inspection transform/modifier context differs from native source binding')
        identity_matrix=[[float(i==j) for j in range(4)] for i in range(4)]
        if matrix!=identity_matrix:
            _fail('This source-stage acceptance requires the constructor identity object transform; standalone inspection remains read-only')
        metres=[[struct.unpack('f',struct.pack('f',x*.001))[0] for x in v] for v in raw['vertices_mm']]
        if (quality.get('identity',{}).get('object_id')!=oid
                or quality.get('identity',{}).get('feature_id')!=detail['identity']['feature_id']
                or quality.get('mesh_state')!='control'
                or quality.get('actual_structure',{}).get('binding')!=binding
                or quality.get('topology_sha256')!=domain_fingerprint(raw['faces'])
                or quality.get('geometry_sha256')!=domain_fingerprint(metres)
                or quality.get('self_intersections',{}).get('triangles')!=2*len(raw['faces'])
                or quality.get('self_intersections',{}).get('findings')!=[]):
            _fail('Actual control/intersection audit is absent, failed, or bound to a different object or mesh')
        if (sem.get('vertex_ids')!=[k for k,i in sorted(maps['vertex_map'].items(),key=lambda x:x[1])]
                or sem.get('face_ids')!=[k for k,i in sorted(maps['face_map'].items(),key=lambda x:x[1])]):
            _fail('Inspection semantic slots differ from actual native structure')
        from .sparse_panel_geometry import build_sparse_panel
        if native['authorship'].get('schedule_revision')!='sparse_sharp_panel_ids_v2':
            _fail('Source-stage inspection cannot silently migrate a legacy author schedule')
        expected_mesh=build_sparse_panel(native['authorship']['parameter_binding']['parameters'],feature_id=detail['identity']['feature_id'])
        expected_faces=expected_mesh['authored_structure']['face_map']
        if sem.get('face_provenance')!=[expected_mesh['face_provenance'][expected_faces[s]] for s in sem['face_ids']]:
            _fail('Inspection surface roles differ from the bound constructor and actual semantic face identities')
        rebuilt=analyze_mesh(raw['vertices_mm'],raw['edges'],raw['faces'],vertex_map=maps['vertex_map'],face_map=maps['face_map'],
                face_provenance=sem['face_provenance'],semantic_control_loops=native['authorship']['semantic_control_loops'],
                identity=detail['identity'],evidence_origin='blender_raw_object_data')
        # Every analyzer-owned field can affect the drawing or a downstream
        # decision. A handpicked summary is insufficient: poles, regular paths,
        # metrics and all actual index rows must reproduce as well.
        for key,value in rebuilt.items():
            if key=='checks':
                for name,status in value.items():
                    if name=='native_extraction':continue  # independently checked above
                    if fingerprint(detail.get('checks',{}).get(name))!=fingerprint(status):
                        _fail('Inspection check does not reproduce actual arrays: '+name)
            elif fingerprint(detail.get(key))!=fingerprint(value):
                _fail('Inspection analysis does not reproduce actual arrays: '+key)
        for key in ('counts','mesh_sha256','semantic_sha256'):
            if fingerprint(summary.get(key))!=fingerprint(detail[key]):_fail('Inspection summary differs from full evidence')
        if [x.get('view') for x in views]!=required_views:_fail('Required whole/top/bottom/wall/roundover source-view coverage is incomplete')
        for item in views:
            if item.get('source_sha256')!=sha or item.get('mesh_sha256')!=detail['mesh_sha256']:_fail('SVG source identity differs')
            expected=render_svg(detail,item['view']).encode('utf-8')
            if len(expected)!=item['bytes'] or hashlib.sha256(expected).hexdigest()!=item['sha256']:
                _fail('SVG bytes do not reproduce the declared actual-data view')
    return refs
