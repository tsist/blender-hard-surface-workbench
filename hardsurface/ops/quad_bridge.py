"""Materialize authored structured geometry and inspect actual mesh polygons."""
from __future__ import annotations
import json
import math
import bpy
from ..io import RuntimeFailure
from ..quad_geometry import build
from ..quad_quality import validate_mesh
from .geometry import new_mesh, digest, require_closed, require_si_scene
from ..structure_native import (write_authored_identity, validate_native_structure,
                               compact_native_report, has_native_identity)


def create(parameters, feature_id, name, *, topology_epoch=None):
    result = build(parameters, feature_id)
    structured = 'authored_structure' in result
    units = require_si_scene() if structured else None
    obj = new_mesh(name, [[x*.001 for x in v] for v in result['vertices_mm']], result['faces'],
                   already_oriented=structured)
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
    if 'subdivision_modifier' in result:
        obj['hs_subd_control_loops']=json.dumps(result['control_loops'],sort_keys=True,separators=(',',':'))
        crease=obj.data.attributes.new('crease_edge','FLOAT','EDGE')
        values={tuple(sorted((a,b))):v for a,b,v in result['edge_creases']}
        for edge in obj.data.edges:crease.data[edge.index].value=values.get(tuple(sorted(edge.vertices)),0.)
        for poly in obj.data.polygons:poly.use_smooth=True
        settings=result['subdivision_modifier'];modifier=obj.modifiers.new(settings['name'],'SUBSURF')
        for key,value in settings.items():
            if key!='name':setattr(modifier,key,value)
        obj['hs_subdivision_settings']=json.dumps(settings,sort_keys=True,separators=(',',':'))
    position_error = max((math.dist(v.co, [x*.001 for x in ideal]) for v,ideal in zip(obj.data.vertices,result['vertices_mm'])), default=0)
    if position_error > 1e-7:
        raise RuntimeFailure('QUAD_FLOAT32_PRECISION', 'Constructed vertices exceed materialization precision bound', maximum_error_m=position_error)
    if topology_epoch is None:topology_epoch=result.get('sparse_graph',{}).get('topology_epoch',0)
    actual_structure = write_authored_identity(obj, result, unit_scale=units['scale_length'],topology_epoch=topology_epoch) if structured else None
    metrics = require_closed(obj)
    return obj, {**({'actual_structure': compact_native_report(actual_structure)} if structured else {}), 'constructor':parameters['op'],'construction_sha256':result['construction_sha256'],
                 'metadata':result.get('metadata',{}),'actual':metrics,
                 'dimensional_witness':{'max_materialization_error_m':position_error,
                    'scope':'declared parameter-domain samples; independent actual feature probes remain required'}}


def inspect_object(obj, mesh_state, self_intersection_cache=None):
    if mesh_state not in ('control','evaluated'):
        raise RuntimeFailure('QUAD_STATE_INVALID','Unknown actual mesh state')
    if not obj.get('hs_quad_surface_table'):
        raise RuntimeFailure('QUAD_PROVENANCE_MISSING','Final body lacks authored structured surface provenance',object_id=obj.get('hs_object_id'),feature_id=obj.get('hs_feature_id'))
    actual_structure = None
    if has_native_identity(obj):
        units = require_si_scene()
        actual_structure = validate_native_structure(obj, unit_scale=units['scale_length'])
    cage_settings=json.loads(obj.get('hs_subdivision_settings','null'))
    is_subd=bool(cage_settings)
    if is_subd:
        if len(obj.modifiers)!=1 or obj.modifiers[0].type!='SUBSURF' or any(getattr(obj.modifiers[0],k)!=v for k,v in cage_settings.items()):
            raise RuntimeFailure('SUBD_MODIFIER_DRIFT','Authored SubD modifier differs from bound settings')
    if obj.modifiers and not is_subd:
        raise RuntimeFailure('QUAD_PROVENANCE_UNSUPPORTED','Final structured body has an unqualified modifier that can change topology or shading',object_id=obj.get('hs_object_id'))
    semantic_source = None
    if is_subd and mesh_state == 'evaluated' and actual_structure is not None:
        from ..subdivision_source import capture_semantic_source
        # Fresh source attestation before evaluation; direct evaluated calls
        # cannot rely on a previously run control inspection or cached labels.
        semantic_source = capture_semantic_source(obj, unit_scale=units['scale_length'])
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
        if is_subd and mesh_state=='evaluated':
            allowed={'QUAD_NONPLANAR','QUAD_WARPAGE_EXCEEDED'}
            deferred=[f for f in report['findings'] if f['code'] in allowed]
            report['subdivision_surface_diagnostics']={'policy':'CATMULL_CLARK_EVALUATED_V1','scope':'nonplanar evaluated surface quads measured but not treated as flat-polygon defects; actual shape and highlight review required','counts':{k:v for k,v in report['finding_counts'].items() if k in allowed},'examples':deferred,'artistic_approval':'not_run'}
            removed=sum(report['finding_counts'].pop(k,0) for k in allowed)
            report['finding_count']-=removed
            report['findings']=[f for f in report['findings'] if f['code'] not in allowed]
            report['passed']=report['finding_count']==0
        if actual_structure is not None:
            report['actual_structure']=compact_native_report(actual_structure)
        report['topology_sha256']=digest(faces)
        report['geometry_sha256']=digest(vertices)
        shading=[]
        maximum=0.0
        for poly,source in zip(mesh.polygons,provenance):
            if source.get('curved',False) or is_subd:continue
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
            'scope':'SubD shading is assessed by actual evaluated feature/normal and highlight diagnostics, not the explicit-mesh planar-normal test' if is_subd else 'actual corner normals on authored planar surface roles; artistic shading remains visual review'}
        if shading:
            report['passed']=False
            report['finding_counts']['PLANAR_SHADING_DEVIATION']=len(shading)
            report['finding_count']+=len(shading)
            remaining=max(0,report['policy']['max_finding_examples']-len(report['findings']))
            report['findings'].extend(shading[:remaining])
            report['findings_truncated']+=max(0,len(shading)-remaining)
        if is_subd:
            if mesh_state=='control':
                from ..subd_cage_validation import inspect_control_loops
                # Stable semantic slots replace legacy raw-index control loops
                # for the bounded authored route. Native validation retains
                # opposite-edge continuation and actual crease/valence checks.
                loops=actual_structure['semantic_control_loops'] if actual_structure is not None else inspect_control_loops(obj)
                report['semantic_control_loops']=loops
                if loops['status']!='pass':
                    report['passed']=False;report['finding_counts']['SUBD_CONTROL_LOOP_DRIFT']=1;report['finding_count']+=1
                    report['findings'].append({'code':'SUBD_CONTROL_LOOP_DRIFT','mesh_state':mesh_state})
            declared=json.loads(obj['hs_quad_metadata'])['subdivision_cage']
            if mesh_state=='evaluated':
                from ..subd_panel_measure import measure_panel
                d=declared['nominal_design']
                domain={'size':d['size_mm'],'center':d['center_mm'],'corner_radius':d['corner_radius_mm'],'edge_bevel':d['edge_roundover_radius_mm'],'z_min':d['z_range_mm'][0],'z_max':d['z_range_mm'][1],'holes':[{'center':d['hole_center_mm'],'radius':d['hole_radius_mm']}]}
                measurement_binding = None
                if actual_structure is not None:
                    from ..measurement_binding import bind_constructor_measurement
                    measurement_binding = bind_constructor_measurement(declared, actual_structure)
                    domain = measurement_binding['parameters']
                if semantic_source is not None:
                    from ..subdivision_source import evaluated_bore_domain
                    transported = evaluated_bore_domain(mesh, semantic_source, level=int(obj.modifiers[0].levels))
                    sampled=measure_panel([[x*1000 for x in v] for v in vertices],faces,domain,
                        tolerance_mm=declared['error_tolerance_mm'],
                        bore_face_indices=transported['bore_face_indices'], measurement_profile='semantic_bore_v1')
                    sampled['semantic_transport'] = transported['evidence']
                    sampled['source_evaluation_profile'] = {
                        'kind': 'actual_source_modifier_no_clone_override',
                        'modifier': semantic_source['evidence']['actual_source_modifier'],
                        'level': int(obj.modifiers[0].levels),
                        'scope': 'Actual source depsgraph evaluation; geometry check only, not user or visual qualification'}
                else:
                    sampled=measure_panel([[x*1000 for x in v] for v in vertices],faces,domain,
                        tolerance_mm=declared['error_tolerance_mm'])
                if measurement_binding is not None:
                    sampled['constructor_measurement_binding'] = measurement_binding['evidence']
                report['evaluated_reference_samples']=sampled
                if semantic_source is None:
                    # Legacy measurements remain available as diagnostics, but
                    # cannot satisfy the current authored production gate.
                    report['passed']=False
                    report['finding_counts']['SUBD_SEMANTIC_SOURCE_REQUIRED']=1
                    report['finding_count']+=1
                    report['findings'].append({'code':'SUBD_SEMANTIC_SOURCE_REQUIRED',
                        'mesh_state':mesh_state,
                        'reason':'Legacy numeric measurement lacks authenticated source-domain transport; use explicitly unqualified diagnosis or separately approved migration'})
                datum={}
                for prefix,z in [('bottom:',d['z_range_mm'][0]),('top:',d['z_range_mm'][1])]:
                    selected={i for f,source in zip(faces,provenance) if source['surface_role'].startswith(prefix) for i in f}
                    datum[prefix[:-1]]={'vertices':len(selected),'maximum_plane_deviation_mm':max((abs(vertices[i][2]*1000-z) for i in selected),default=None)}
                report['datum_planes']=datum
                if sampled['status']!='pass':
                    report['passed']=False;report['finding_counts']['SUBD_REFERENCE_SAMPLE_ERROR']=1;report['finding_count']+=1
                    report['findings'].append({'code':'SUBD_REFERENCE_SAMPLE_ERROR','mesh_state':mesh_state,'maximum_mm':sampled['maximum_sampled_surface_distance_mm'],'tolerance_mm':declared['error_tolerance_mm']})
            else:report['reference_shape_scope']='intentional compensated control cage; evaluated reference-shape gate is separate'
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
