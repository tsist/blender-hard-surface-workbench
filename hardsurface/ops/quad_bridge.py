"""Materialize authored structured geometry and inspect actual mesh polygons."""
from __future__ import annotations
import json
import math
import bpy
from ..io import RuntimeFailure
from ..quad_geometry import build
from ..quad_quality import validate_mesh
from .geometry import new_mesh, digest, require_closed


def create(parameters, feature_id, name):
    result = build(parameters, feature_id)
    obj = new_mesh(name, [[x*.001 for x in v] for v in result['vertices_mm']], result['faces'])
    if len(obj.data.polygons) != len(result['faces']):
        raise RuntimeFailure('QUAD_MATERIALIZATION_FAILED', 'Blender changed source polygon cardinality')
    table = []
    table_lookup = {}
    attr = obj.data.attributes.new('hs_quad_surface_id', 'INT', 'FACE')
    for poly, provenance in zip(obj.data.polygons, result['face_provenance']):
        key = json.dumps(provenance, sort_keys=True, separators=(',',':'))
        if key not in table_lookup:
            table_lookup[key] = len(table)
            table.append(provenance)
        attr.data[poly.index].value = table_lookup[key]
        poly.use_smooth = bool(provenance.get('smooth', False))
    obj['hs_quad_surface_table'] = json.dumps(table, sort_keys=True, separators=(',',':'))
    obj['hs_quad_construction_sha256'] = result['construction_sha256']
    obj['hs_quad_constructor'] = parameters['op']
    obj['hs_quad_metadata'] = json.dumps(result.get('metadata',{}), sort_keys=True, separators=(',',':'))
    sharp = {tuple(sorted(edge)) for edge in result.get('sharp_edges',[])}
    for edge in obj.data.edges:
        if tuple(sorted(edge.vertices)) in sharp:
            edge.use_edge_sharp = True
    if 'corner_normals' in result:
        normals=result['corner_normals']
        if len(normals)!=len(obj.data.polygons) or any(len(row)!=len(poly.vertices) for row,poly in zip(normals,obj.data.polygons)):
            raise RuntimeFailure('QUAD_SHADING_INVALID','Authored corner normal cardinality differs')
        # new_mesh only reorients faces. Match semantic vertex IDs if Blender
        # reversed a face instead of assuming loop order remains unchanged.
        flattened=[]
        for poly,face,row in zip(obj.data.polygons,result['faces'],normals):
            lookup=dict(zip(face,row))
            flattened.extend(lookup[index] for index in poly.vertices)
        obj.data.normals_split_custom_set(flattened)
        obj.data.update()
    position_error = max((math.dist(v.co, [x*.001 for x in ideal]) for v,ideal in zip(obj.data.vertices,result['vertices_mm'])), default=0)
    if position_error > 1e-7:
        raise RuntimeFailure('QUAD_FLOAT32_PRECISION', 'Constructed vertices exceed materialization precision bound', maximum_error_m=position_error)
    metrics = require_closed(obj)
    return obj, {'constructor':parameters['op'],'construction_sha256':result['construction_sha256'],
                 'metadata':result.get('metadata',{}),'actual':metrics,
                 'dimensional_witness':{'max_materialization_error_m':position_error,
                    'scope':'declared parameter-domain samples; independent actual feature probes remain required'}}


def inspect_object(obj, mesh_state, self_intersection_cache=None):
    if mesh_state not in ('control','evaluated'):
        raise RuntimeFailure('QUAD_STATE_INVALID','Unknown actual mesh state')
    if not obj.get('hs_quad_surface_table'):
        raise RuntimeFailure('QUAD_PROVENANCE_MISSING','Final body lacks authored structured surface provenance',object_id=obj.get('hs_object_id'),feature_id=obj.get('hs_feature_id'))
    if obj.modifiers:
        raise RuntimeFailure('QUAD_PROVENANCE_UNSUPPORTED','Final structured body has an unqualified modifier that can change topology or shading',object_id=obj.get('hs_object_id'))
    table = json.loads(obj['hs_quad_surface_table'])
    eo = None
    try:
        if mesh_state == 'evaluated':
            eo = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = eo.to_mesh(preserve_all_data_layers=True,depsgraph=bpy.context.evaluated_depsgraph_get())
        else:
            mesh = obj.data
        attr = mesh.attributes.get('hs_quad_surface_id')
        if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT' or len(attr.data)!=len(mesh.polygons):
            raise RuntimeFailure('QUAD_PROVENANCE_MISSING','Surface provenance did not survive actual topology',mesh_state=mesh_state)
        provenance=[]
        for item in attr.data:
            if not 0<=item.value<len(table):
                raise RuntimeFailure('QUAD_PROVENANCE_INVALID','Actual polygon has unknown surface identity')
            provenance.append(table[item.value])
        vertices=[list(obj.matrix_world@v.co) for v in mesh.vertices]
        faces=[list(p.vertices) for p in mesh.polygons]
        report=validate_mesh(vertices,faces,face_provenance=provenance,
            identity={'object_id':obj.get('hs_object_id'),'feature_id':obj.get('hs_feature_id'),'name':obj.name},
            mesh_state=mesh_state)
        report['topology_sha256']=digest(faces)
        report['geometry_sha256']=digest(vertices)
        shading=[]
        maximum=0.0
        for poly,source in zip(mesh.polygons,provenance):
            if source.get('curved',False):continue
            deviations=[]
            for li in poly.loop_indices:
                normal=mesh.corner_normals[li].vector
                dot=max(-1.0,min(1.0,float(normal.dot(poly.normal))))
                deviations.append(math.degrees(math.acos(dot)))
            measured=max(deviations,default=0.0);maximum=max(maximum,measured)
            if measured>1.0:
                shading.append({'code':'PLANAR_SHADING_DEVIATION','face_index':poly.index,
                    'feature_id':source['feature_id'],'surface_id':source['surface_id'],
                    'mesh_state':mesh_state,'measured_degrees':measured,'maximum_degrees':1.0})
        report['shading']={'planar_maximum_normal_deviation_degrees':maximum,
            'technical_gate_degrees':1.0,'affected_faces':len(shading),
            'scope':'actual corner normals on authored planar surface roles; artistic shading remains visual review'}
        if shading:
            report['passed']=False
            report['finding_counts']['PLANAR_SHADING_DEVIATION']=len(shading)
            report['finding_count']+=len(shading)
            remaining=max(0,report['policy']['max_finding_examples']-len(report['findings']))
            report['findings'].extend(shading[:remaining])
            report['findings_truncated']+=max(0,len(shading)-remaining)
        if report['passed']:
            from ..self_intersection import audit_blender_mesh
            cache=self_intersection_cache if self_intersection_cache is not None else {}
            key=(report['topology_sha256'],report['geometry_sha256'])
            if key in cache:
                import copy
                intersection=copy.deepcopy(cache[key]);intersection['reuse']='identical actual indexed topology and world vertex SHA'
            else:
                intersection=audit_blender_mesh(mesh,obj.matrix_world);cache[key]=intersection
            report['self_intersections']=intersection
            if intersection['status']!='pass':
                report['passed']=False;report['finding_counts']['SELF_INTERSECTION']=len(intersection['findings'])
                report['finding_count']+=len(intersection['findings'])
                report['findings'].extend({'code':'SELF_INTERSECTION','mesh_state':mesh_state,**x} for x in intersection['findings'][:16])
        return report
    finally:
        if eo is not None: eo.to_mesh_clear()
