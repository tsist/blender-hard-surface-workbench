"""Opt-in exact native surface fields; no rendering, normal repair or ancestry inference.

HOST-safe contracts, evidence binding and streaming serialization. Native data
are supplied only by the existing source-bound diagnose evaluator. A completed
job report is the usability gate; partial files are retained on every failure.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import struct

from . import contract as c
from .io import RuntimeFailure, checked_path, read_json_reference
from .observation import _mesh_record
from .observation_normals import mesh_record, require_equal
from .structure_native import FACE_SLOT, validate_native_structure
from .structure_kernel import fingerprint as native_fingerprint
from .subdivision_source import SURFACE_ATTRIBUTE, _face_int_values, declared_modifier_settings

FORMAT = 'HS_NATIVE_SURFACE_FIELDS_V1'
LIMITS = {'file_bytes': 64 * 1024**2, 'aggregate_bytes': 160 * 1024**2,
          'report_bytes': 8 * 1024**2, 'image_bytes': 2 * 1024**2,
          'vertices': 300000, 'faces': 250000, 'edges': 500000,
          'loops': 1000000, 'triangles': 500000, 'views': 2}
REFERENCE = c.obj({'file': c.string(minLength=1, maxLength=4096),
                   'sha256': c.SHA, 'bytes': c.integer(minimum=1, maximum=LIMITS['report_bytes'])})
PROJECTION_INPUTS = c.obj({
    'pixel_aspect_x': c.number(exclusiveMinimum=0, maximum=100),
    'pixel_aspect_y': c.number(exclusiveMinimum=0, maximum=100),
    'resolution_percentage': c.const(100),
    'shift_x': c.number(minimum=-10, maximum=10),
    'shift_y': c.number(minimum=-10, maximum=10)})
OPTION = c.obj({
    'format': c.const(FORMAT),
    'binding_mode': c.const('source_bound_sparse_observation_v1'),
    'levels': c.const([2, 3]),
    'expected_l2': c.obj({'positions_topology_sha256': c.SHA,
                         'native_normals_sharp_smooth_sha256': c.SHA}),
    'observation': c.obj({'report': REFERENCE, 'views': c.array(c.obj({
        'view': c.string(minLength=1, maxLength=128),
        'image': c.obj({'file': c.string(minLength=1, maxLength=4096), 'sha256': c.SHA,
                        'bytes': c.integer(minimum=1, maximum=LIMITS['image_bytes'])}),
        'projection_inputs': PROJECTION_INPUTS}), 1, 2)})}, ['format', 'levels', 'expected_l2', 'observation'])


def fail(message, **details):
    raise RuntimeFailure('SUBDIVISION_SURFACE_EXPORT', message, **details)


def validate_option(params):
    if 'surface_export' not in params:
        return
    sparse = params['evaluation_profile']['mode'] == 'source_bound_sparse_diagnostic_v1'
    expected_levels = [0, 1, 2, 3] if sparse else [0, 2, 3]
    if (params['evaluation_profile']['mode'] not in ('source_bound_qualification_v1', 'source_bound_sparse_diagnostic_v1')
            or params['render']['enabled'] is not False or params['export_geometry'] is not True
            or params['levels'] != expected_levels or params['cpu_threads'] != 2):
        raise c.ContractError('INVALID_REQUEST', 'Native surface export requires its exact source-bound profile/levels, render.enabled=false, export_geometry=true and cpu_threads=2')
    option = params['surface_export']
    if sparse != (option.get('binding_mode') == 'source_bound_sparse_observation_v1'):
        raise c.ContractError('INVALID_REQUEST', 'Sparse export requires explicit sparse observation binding; legacy binding is unchanged')
    profile = params['evaluation_profile']['expected_source_modifier']
    level = 0 if sparse else 2
    if profile['levels'] != level or profile['render_levels'] != level:
        raise c.ContractError('INVALID_REQUEST', 'Pinned source level differs from the observation binding mode')
    if any(type(level) is not int for level in params['surface_export']['levels']):
        raise c.ContractError('INVALID_REQUEST', 'Surface levels must be actual integer levels')
    views = params['surface_export']['observation']['views']
    if len({row['view'] for row in views}) != len(views):
        raise c.ContractError('INVALID_REQUEST', 'Pinned observation view names must be unique')


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        fail('Actual finite native numbers are required; booleans are not numeric evidence', field=name)
    return float(value)


def _vector(value, count, name):
    try:
        result = [_number(item, name) for item in value]
    except TypeError as error:
        fail('Native vector is unavailable', field=name, reason=str(error))
    if len(result) != count:
        fail('Native vector length differs', field=name, expected=count, observed=len(result))
    return result


def matrix_values(matrix):
    result = [_vector(row, 4, 'matrix_world') for row in matrix]
    if len(result) != 4 or result[3] != [0., 0., 0., 1.]:
        fail('Actual affine four-by-four object matrix required')
    return result


def _int(value, count, name):
    if type(value) is not int or not 0 <= value < count:
        fail('Actual native index is invalid', field=name, value=value, domain_count=count)
    return value


def _image_bytes(reference, width, height):
    """Hash exactly the bounded bytes read, allowing relocated identical images."""
    path = checked_path(reference['file'])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(LIMITS['image_bytes'] + 1)
    if (len(raw) != reference['bytes'] or len(raw) > LIMITS['image_bytes']
            or hashlib.sha256(raw).hexdigest() != reference['sha256']):
        fail('Pinned observation image bytes differ', file=str(path))
    if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR' or struct.unpack('>II', raw[16:24]) != (width, height):
        fail('Pinned observation must be a PNG with its recorded resolution')
    return dict(reference)


def _bind_observation(option, source, object_id, expected_modifier, *, sparse=False):
    """Pinned content is caller evidence, never remote approval authentication."""
    reference = option['observation']['report']
    report = read_json_reference(reference)
    if (report.get('operation') != 'hardsurface.observe' or report.get('status') != 'succeeded'
            or report.get('outcome') != 'pass' or report.get('mesh_state') != 'evaluated'
            or report.get('blend_save_performed') is not False
            or report.get('saved_candidate_modified') is not False
            or report.get('temporary_state_restored') is not True):
        fail('Pinned report is not a completed preserving native observation')
    for key in ('source', 'source_after', 'opened_source'):
        row = report.get(key, {})
        if row.get('sha256') != source['sha256'] or row.get('bytes') != source['bytes']:
            fail('Pinned observation source bytes differ from the current saved source', field=key)
    if report.get('source_sha256') != source['sha256']:
        fail('Pinned observation top-level source hash differs')
    sparse_evaluation = report.get('sparse_evaluation')
    if sparse:
        from .sparse_observation import MODE, effective_modifier
        if (option.get('binding_mode') != MODE or not isinstance(sparse_evaluation, dict)
                or sparse_evaluation.get('mode') != MODE
                or sparse_evaluation.get('source_object_id') != object_id
                or type(sparse_evaluation.get('source_level')) is not int or sparse_evaluation.get('source_level') != 0
                or type(sparse_evaluation.get('level')) is not int or sparse_evaluation.get('level') != 2
                or sparse_evaluation.get('original_source_preserved') is not True
                or sparse_evaluation.get('evaluation_scope') != 'owned_separate_scene_clone_only'
                or c.fingerprint(sparse_evaluation.get('source_modifier')) != c.fingerprint(expected_modifier)
                or c.fingerprint(sparse_evaluation.get('effective_modifier')) != c.fingerprint(effective_modifier(expected_modifier))):
            fail('Pinned sparse observation lacks exact original-L0/effective-L2 identity')
        phases = sparse_evaluation.get('original_source_checks', [])
        for phase in ('before_clone', 'before_render', 'after_render', 'after_observation', 'after_clone_cleanup'):
            if not any(row.get('phase') == phase and row.get('original_source_identity') == 'pass' for row in phases):
                fail('Pinned sparse observation lacks original-source preservation checks', phase=phase)
        if (sparse_evaluation.get('source_control_loops', {}).get('status') != 'pass'
                or sparse_evaluation.get('semantic_transport', {}).get('status') != 'validated'
                or sparse_evaluation.get('semantic_transport', {}).get('level') != 2
                or sparse_evaluation.get('source_binding', {}).get('mode') != 'source_bound_sparse_diagnostic_v1'):
            fail('Pinned sparse observation lacks authenticated loops/semantic transport')
        effective = effective_modifier(expected_modifier)
    else:
        if sparse_evaluation is not None or 'binding_mode' in option:
            fail('Sparse observation cannot be consumed through legacy qualification binding')
        effective = expected_modifier
    previews = report.get('previews')
    if not isinstance(previews, list) or not 1 <= len(previews) <= 16:
        fail('Pinned report previews must be bounded')
    views, source_record = [], None
    from .surface_projection import validate_camera_record
    for requested in option['observation']['views']:
        matches = [row for row in previews if row.get('view') == requested['view']]
        if len(matches) != 1:
            fail('Pinned view must select exactly one recorded preview', view=requested['view'])
        view = matches[0]
        if (view.get('mesh_state') != 'evaluated' or view.get('wire', {}).get('enabled') is not False
                or view.get('diagnostic', {}).get('normal_isolation', {}).get('policy') != 'geometry_normals_v1'):
            fail('Pinned view needs an evaluated unoverlaid native-normal witness')
        image = requested['image']
        if any(image[key] != view.get(key) for key in ('sha256', 'bytes')):
            fail('Pinned current image descriptor differs from the recorded image content')
        for phase in ('before_render', 'after_render'):
            checks = [row for row in view.get('normal_isolation_checks', []) if row.get('phase') == phase and row.get('view') == requested['view']]
            if len(checks) != 1 or checks[0].get('source_and_proxy_identity') != 'pass':
                fail('Pinned view lacks preserving native normal render-boundary checks', phase=phase)
        transforms = view.get('temporary_transformations', [])
        if len(transforms) != 1 or transforms[0].get('object_id') != object_id:
            fail('Pinned view must contain exactly the requested native object')
        transform = transforms[0]
        if (transform.get('original_matrix_world') != transform.get('observation_matrix_world')
                or transform.get('translation_world_mm') != [0., 0., 0.]):
            fail('Exploded/transformed old observation cannot bind an unchanged source export')
        frozen = transform.get('frozen_geometry', {})
        normals = frozen.get('normal_isolation', {})
        actual_source = normals.get('source', {})
        state = actual_source.get('source_mesh', {})
        modifier = actual_source.get('source_modifier', {}).get('settings', {})
        if (actual_source.get('mesh_state') != 'evaluated'
                or actual_source.get('policy') != 'geometry_normals_v1'
                or normals.get('normal_sharp_smooth_unchanged') is not True
                or normals.get('normal_recomputed_transferred_or_authored_by_observer') is not False
                or frozen.get('geometry_unchanged') is not True
                or c.fingerprint(declared_modifier_settings(modifier)) != c.fingerprint(effective)):
            fail('Pinned source modifier/native-normal preservation witness differs')
        if (state.get('hash_semantics') != 'HS_OBSERVATION_POSITIONS_TOPOLOGY_V1'
                or state.get('native_shading', {}).get('hash_semantics') != 'HS_OBSERVATION_NATIVE_NORMALS_V1'
                or state.get('positions_topology_sha256') != option['expected_l2']['positions_topology_sha256']
                or state.get('native_shading', {}).get('native_normals_sharp_smooth_sha256') != option['expected_l2']['native_normals_sharp_smooth_sha256']
                or state != normals.get('frozen_proxy')):
            fail('Pinned L2 geometry/native-normal hashes or source/proxy records differ')
        if source_record is not None and source_record != actual_source:
            fail('Pinned views disagree about the actual source normal state')
        if sparse:
            require_equal(sparse_evaluation['original_control_identity'], actual_source['source_control_mesh'],
                          role='sparse_original_vs_observed_clone_control')
            if c.fingerprint(view.get('native_projection_inputs')) != c.fingerprint(requested['projection_inputs']):
                fail('Sparse projection inputs differ from their native render-time values')
        source_record = deepcopy(actual_source)
        image = _image_bytes(image, view['width'], view['height'])
        calibrated_input = validate_camera_record({
            'name': view['view'], 'camera': view['camera'], 'width': view['width'], 'height': view['height'],
            'report': reference, 'producer_identity': {'source_sha256': report['implementation']['source_sha256'],
                'blender': report['implementation']['blender']},
            'projection_inputs': requested['projection_inputs'],
            'projection_inputs_origin': ('native_render_time_recorded' if sparse else 'caller_declared_unrecorded_intrinsics')})
        views.append({'calibration_input': calibrated_input, 'image': image,
                      'recorded_image': {key: view[key] for key in ('file', 'sha256', 'bytes')},
                      'original_matrix_world': matrix_values(transform['original_matrix_world'])})
    return {**({'sparse_evaluation': deepcopy(sparse_evaluation)} if sparse else {}),
            'report': deepcopy(reference), 'views': views, 'source_record': source_record,
            'expected_l2': deepcopy(option['expected_l2']),
            'authority': 'Caller-pinned local content identity only; no user or remote approval authentication',
            'path_policy': 'Current descriptors bind the exact archived bytes; historical paths are informational'}


def capture_source_maps(obj, source, *, unit_scale, registry_entry):
    """Fresh native validation, never arrays loaded from a historical witness."""
    native = validate_native_structure(obj, unit_scale=unit_scale, expected_binding=registry_entry)
    if native.get('status') != 'pass' or native.get('identity_binding_status') != 'bound':
        fail('Actual native authored source identity is not bound')
    author = native['authorship']
    slots = _face_int_values(obj.data, FACE_SLOT)
    labels = _face_int_values(obj.data, SURFACE_ATTRIBUTE)
    if (native_fingerprint(slots) != source['evidence']['source_parent_slots_sha256']
            or native_fingerprint(labels) != source['evidence']['source_surface_labels_sha256']):
        fail('Source semantic arrays changed after their native validation')
    face_ids = [None] * len(slots)
    for name, index in author['face_map'].items():
        _int(index, len(slots), 'actual source face map')
        if face_ids[index] is not None:
            fail('Authored source face map is not one-to-one')
        face_ids[index] = name
    if any(value is None for value in face_ids):
        fail('Authored source face map does not cover every actual polygon')
    slot_to_polygon = [None] * len(slots)
    for polygon, slot in enumerate(slots):
        _int(slot, len(slots), FACE_SLOT)
        if slot_to_polygon[slot] is not None:
            fail('Actual source parent slots are not unique')
        slot_to_polygon[slot] = polygon
    loops = []
    for row in author['semantic_control_loops']:
        loops.append({**deepcopy(row), 'vertex_indices': [author['vertex_map'][key] for key in row['vertex_ids']]})
    return {'schema_version': '1.0', 'format': 'HS_NATIVE_SURFACE_SOURCE_MAPS_V1',
        'source': 'fresh original object.data and validate_native_structure',
        'native_binding': deepcopy(native['binding']), 'source_vertex_map': deepcopy(author['vertex_map']),
        'source_face_map': deepcopy(author['face_map']), 'source_polygon_authored_face_ids': face_ids,
        'source_parent_slots': slots, 'source_surface_labels': labels,
        'source_slot_to_polygon': slot_to_polygon,
        'source_slot_to_authored_face_id': [face_ids[index] for index in slot_to_polygon],
        'surface_table': c.strict_loads(obj.get('hs_quad_surface_table')), 'named_control_loops': loops,
        'native_control_loop_checks': deepcopy(native['semantic_control_loops']),
        'semantic_source_evidence': deepcopy(source['evidence']),
        'parent_semantics': 'Native FACE INT parent slots identify validated source patches, not complete subdivision influence weights'}


def validate_mesh(mesh, matrix):
    """Require native numeric fields and exact loop/triangle correspondence."""
    try:
        for field in ('vertices', 'edges', 'polygons', 'loops', 'corner_normals', 'loop_triangles', 'attributes', 'normals_domain', 'has_custom_normals'):
            if not hasattr(mesh, field):
                fail('Required actual native surface API is unavailable; no fallback permitted', field=field)
        counts = {key: len(getattr(mesh, field)) for key, field in (
            ('vertices', 'vertices'), ('edges', 'edges'), ('faces', 'polygons'), ('loops', 'loops'), ('triangles', 'loop_triangles'))}
        if any(count < 1 or count > LIMITS[key] for key, count in counts.items()):
            fail('Native surface domain exceeds explicit bounds', counts=counts)
        if mesh.normals_domain != 'CORNER':
            fail('This surface export requires actual CORNER-domain native normals', normals_domain=mesh.normals_domain)
        matrix_values(matrix)
        for vertex in mesh.vertices:
            _vector(vertex.co, 3, 'vertices')
        for normal in mesh.corner_normals:
            _vector(normal.vector, 3, 'corner_normals')
        for polygon in mesh.polygons:
            if len(polygon.vertices) != 4 or type(polygon.loop_start) is not int or type(polygon.loop_total) is not int or polygon.loop_total != 4:
                fail('Surface export requires the validated actual all-quad corner domain')
            for index in polygon.vertices:
                _int(index, counts['vertices'], 'polygon.vertices')
        for loop in mesh.loops:
            _int(loop.vertex_index, counts['vertices'], 'loop.vertex_index')
            _int(loop.edge_index, counts['edges'], 'loop.edge_index')
        triangle_masks = bytearray(counts['faces'])
        for triangle in mesh.loop_triangles:
            fi = _int(triangle.polygon_index, counts['faces'], 'triangle.polygon_index')
            vertices, loops = list(triangle.vertices), list(triangle.loops)
            if len(vertices) != 3 or len(loops) != 3 or len(set(vertices)) != 3 or len(set(loops)) != 3:
                fail('Native loop triangle requires three distinct vertices and loops')
            polygon = mesh.polygons[fi]
            for vertex, loop_index in zip(vertices, loops):
                _int(vertex, counts['vertices'], 'triangle.vertices')
                _int(loop_index, counts['loops'], 'triangle.loops')
                if (mesh.loops[loop_index].vertex_index != vertex
                        or not polygon.loop_start <= loop_index < polygon.loop_start + polygon.loop_total):
                    fail('Native triangle loops must belong to its polygon and actual vertices')
            local = [index - polygon.loop_start for index in loops]
            ordered = sorted(local)
            if local not in [ordered[i:] + ordered[:i] for i in range(3)]:
                fail('Native triangle winding differs from its polygon corner order')
            bit = 1 << next(index for index in range(4) if index not in local)
            if triangle_masks[fi] & bit:
                fail('Native tessellation contains a duplicate polygon triangle')
            triangle_masks[fi] |= bit
        if any(value not in (5, 10) for value in triangle_masks):
            fail('Native tessellation must cover every quad by one of its two oriented diagonals')
        for name, domain, count in (('crease_edge', 'EDGE', counts['edges']), ('crease_vert', 'POINT', counts['vertices'])):
            attr = mesh.attributes.get(name)
            if attr is not None:
                if attr.domain != domain or attr.data_type != 'FLOAT' or len(attr.data) != count:
                    fail('Actual crease attribute domain/type/count differs', attribute=name)
                if any(not 0 <= _number(item.value, name) <= 1 for item in attr.data):
                    fail('Actual crease value is outside [0,1]', attribute=name)
        return mesh_record(mesh, _mesh_record)
    except (AttributeError, TypeError, IndexError, KeyError, ValueError, OverflowError) as error:
        fail('Required actual native surface field is missing or corrupt', reason=str(error))


class Array:
    """A one-use row iterator; serialized without retaining native domain copies."""
    def __init__(self, values):
        self.values = values


def write_json_new(path, fields, written, *, deadline=lambda: None):
    """Immutable bounded streaming file, leaving partial bytes on failure."""
    path = checked_path(path, exists=False)
    if path.exists():
        raise RuntimeFailure('OUTPUT_COLLISION', 'Surface output already exists', file=str(path))
    digest, count, pieces = hashlib.sha256(), 0, 0
    with path.open('xb') as stream:
        def emit(raw):
            nonlocal count, pieces
            if count + len(raw) > LIMITS['file_bytes'] or written[0] + len(raw) > LIMITS['aggregate_bytes']:
                raise RuntimeFailure('SUBDIVISION_SURFACE_OUTPUT_LIMIT', 'Surface export exceeds explicit per-file/aggregate byte cap; partial files retained', file=str(path), bytes=count, aggregate_bytes=written[0])
            stream.write(raw); digest.update(raw); count += len(raw); written[0] += len(raw); pieces += 1
            if pieces % 4096 == 0:
                deadline()
        def encode(value):
            if isinstance(value, Array):
                emit(b'[')
                for index, row in enumerate(value.values):
                    if index: emit(b',')
                    emit(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
                emit(b']')
            elif isinstance(value, dict):
                emit(b'{')
                for index, (key, item) in enumerate(value.items()):
                    if index: emit(b',')
                    emit(json.dumps(key, ensure_ascii=False).encode() + b':'); encode(item)
                emit(b'}')
            else:
                emit(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
        encode(fields); emit(b'\n'); stream.flush(); os.fsync(stream.fileno())
    deadline()
    return {'file': str(path), 'bytes': count, 'sha256': digest.hexdigest()}


def export_level(mesh, matrix, job, level, source, source_maps_ref, semantic_domain, binding, written, *, deadline=lambda: None):
    if type(level) is not int or level not in (2, 3):
        fail('Only explicitly requested L2/L3 native surface fields may be exported')
    record = validate_mesh(mesh, matrix)
    if level == 2:
        require_equal(binding['source_record']['source_mesh'], record, role='surface_export_vs_pinned_old_L2')
    parents = _face_int_values(mesh, FACE_SLOT)
    labels = _face_int_values(mesh, SURFACE_ATTRIBUTE)
    evidence = semantic_domain['evidence']
    if (evidence.get('status') != 'validated' or evidence.get('level') != level
            or native_fingerprint(parents) != evidence.get('evaluated_parent_slots_sha256')
            or native_fingerprint(labels) != evidence.get('evaluated_surface_labels_sha256')):
        fail('Actual evaluated semantic arrays differ from validated native transport')
    attributes = {}
    for name in ('sharp_edge', 'sharp_face'):
        attr = mesh.attributes.get(name)
        attributes[name] = {**record['native_shading']['native_shading_attributes'][name]}
        if attr is not None:
            attributes[name]['values'] = Array(item.value for item in attr.data)
    creases = {}
    for name, domain, count in (('crease_edge', 'EDGE', len(mesh.edges)), ('crease_vert', 'POINT', len(mesh.vertices))):
        attr = mesh.attributes.get(name)
        creases[name] = {'present': attr is not None, 'domain': domain, 'count': count}
        if attr is not None:
            creases[name].update(data_type=attr.data_type, values=Array(float(item.value) for item in attr.data))
    fields = {'schema_version': '1.0', 'format': FORMAT, 'level': level,
        'source': source, 'source_maps': source_maps_ref, 'observation_report': binding['report'],
        'vertices_coordinate_space': 'object_local', 'vertices_coordinate_units': 'm',
        'normals_coordinate_space': 'object_local', 'normals_units': 'unit_direction_dimensionless',
        'normals_domain': mesh.normals_domain, 'has_custom_normals': mesh.has_custom_normals,
        'matrix_world': matrix_values(matrix), 'observation_identity': record,
        'vertices': Array(_vector(v.co, 3, 'vertices') for v in mesh.vertices),
        'edges': Array(list(edge.vertices) for edge in mesh.edges),
        'edge_use_edge_sharp': Array(edge.use_edge_sharp for edge in mesh.edges),
        'polygons': Array(list(polygon.vertices) for polygon in mesh.polygons),
        'polygon_state_columns': ['loop_start', 'loop_total', 'use_smooth'],
        'polygon_state': Array([p.loop_start, p.loop_total, p.use_smooth] for p in mesh.polygons),
        'loop_columns': ['vertex_index', 'edge_index'],
        'loops': Array([loop.vertex_index, loop.edge_index] for loop in mesh.loops),
        'corner_normals': Array(_vector(item.vector, 3, 'corner_normals') for item in mesh.corner_normals),
        'loop_triangle_columns': ['vertex_0', 'vertex_1', 'vertex_2', 'loop_0', 'loop_1', 'loop_2', 'polygon_index'],
        'loop_triangles': Array([*triangle.vertices, *triangle.loops, triangle.polygon_index] for triangle in mesh.loop_triangles),
        'native_shading_attributes': attributes, 'crease_attributes': creases,
        'parent_slot_attribute': {'name': FACE_SLOT, 'domain': 'FACE', 'data_type': 'INT', 'values': Array(iter(parents))},
        'surface_label_attribute': {'name': SURFACE_ATTRIBUTE, 'domain': 'FACE', 'data_type': 'INT', 'values': Array(iter(labels))},
        'semantic_transport': evidence,
        'identity_semantics': 'Exact HS_OBSERVATION float64 byte hashes including signed zero; no quantization, normalization or repaired values',
        'ancestry_limit': 'Native FACE INT source patch labels with validated topology; not complete subdivision influence weights',
        'usability': 'Requires a successful enclosing job report including unchanged L2 observation identity and source preservation'}
    result = write_json_new(Path(job) / ('subdivision-l%d-surface.json' % level), fields, written, deadline=deadline)
    require_equal(record, validate_mesh(mesh, matrix), role='surface_export_after_serialization')
    if (_face_int_values(mesh, FACE_SLOT) != parents or _face_int_values(mesh, SURFACE_ATTRIBUTE) != labels):
        fail('Native semantic attributes changed during surface serialization')
    return {**result, 'level': level, 'kind': 'native_surface_fields', 'format': FORMAT,
            'observation_identity': record, 'pinned_old_L2_identity': 'pass' if level == 2 else 'not_applicable_new_L3_sample'}


def recompute_identity(data):
    """HOST rehash exported raw arrays through the exact existing collectors.

    Lightweight views create one temporary Python record per access; they do
    not build a second retained mesh. This verifies normal/geometry identity,
    not historical authorization or full semantic transport.
    """
    from types import SimpleNamespace as NS
    required = {'schema_version': '1.0', 'format': FORMAT, 'vertices_coordinate_space': 'object_local',
        'vertices_coordinate_units': 'm', 'normals_coordinate_space': 'object_local',
        'normals_units': 'unit_direction_dimensionless',
        'polygon_state_columns': ['loop_start', 'loop_total', 'use_smooth'],
        'loop_columns': ['vertex_index', 'edge_index'],
        'loop_triangle_columns': ['vertex_0', 'vertex_1', 'vertex_2', 'loop_0', 'loop_1', 'loop_2', 'polygon_index']}
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in required.items()):
        fail('Export format, units, coordinate spaces or native array columns differ')
    class Rows:
        def __init__(self, rows, make): self.rows, self.make = rows, make
        def __len__(self): return len(self.rows)
        def __iter__(self):
            for index, row in enumerate(self.rows): yield self.make(index, row)
        def __getitem__(self, index): return self.make(index, self.rows[index])
    try:
        if type(data['level']) is not int or data['level'] not in (2, 3):
            fail('Exported level must be an actual L2 or L3 integer')
        for field, width, domain in (('vertices', 3, 'vertices'), ('edges', 2, 'edges'), ('polygons', 4, 'faces'), ('polygon_state', 3, 'faces'), ('loops', 2, 'loops'), ('corner_normals', 3, 'loops'), ('loop_triangles', 7, 'triangles')):
            rows = data[field]
            if not isinstance(rows, list) or not 1 <= len(rows) <= LIMITS[domain] or any(not isinstance(row, list) or len(row) != width for row in rows):
                fail('Exported native array rows have an invalid shape or count', field=field)
        for group, specs in (('native_shading_attributes', [('sharp_edge', 'EDGE', 'BOOLEAN', len(data['edges'])), ('sharp_face', 'FACE', 'BOOLEAN', len(data['polygons']))]), ('crease_attributes', [('crease_edge', 'EDGE', 'FLOAT', len(data['edges'])), ('crease_vert', 'POINT', 'FLOAT', len(data['vertices']))])):
            if set(data[group]) != {name for name, _, _, _ in specs}:
                fail('Exported attribute names differ', group=group)
            for name, domain, kind, count in specs:
                attr = data[group][name]
                keys = {'present', 'domain', 'count'} | ({'data_type', 'values'} if attr.get('present') is True else set())
                if (set(attr) != keys or type(attr['present']) is not bool or attr['domain'] != domain or type(attr['count']) is not int or attr['count'] != count
                        or (attr['present'] and (attr['data_type'] != kind or not isinstance(attr['values'], list) or len(attr['values']) != count))):
                    fail('Exported attribute metadata or domain count differs', attribute=name)
        if len(data['edges']) != len(data['edge_use_edge_sharp']) or len(data['polygons']) != len(data['polygon_state']):
            fail('Exported state arrays must cover their complete domains')
        attributes = {}
        for name, value in data['native_shading_attributes'].items():
            if type(value['present']) is not bool:
                fail('Native attribute presence must be boolean')
            if value['present']:
                attributes[name] = NS(domain=value['domain'], data_type=value['data_type'],
                    data=Rows(value['values'], lambda index, row: NS(value=row)))
        for name, value in data['crease_attributes'].items():
            if type(value['present']) is not bool:
                fail('Native attribute presence must be boolean')
            if value['present']:
                attributes[name] = NS(domain=value['domain'], data_type=value['data_type'],
                    data=Rows(value['values'], lambda index, row: NS(value=row)))
        mesh = NS(vertices=Rows(data['vertices'], lambda i, row: NS(co=row)),
            edges=Rows(data['edges'], lambda i, row: NS(vertices=row, use_edge_sharp=data['edge_use_edge_sharp'][i])),
            polygons=Rows(data['polygons'], lambda i, row: NS(vertices=row, loop_start=data['polygon_state'][i][0],
                loop_total=data['polygon_state'][i][1], use_smooth=data['polygon_state'][i][2])),
            loops=Rows(data['loops'], lambda i, row: NS(vertex_index=row[0], edge_index=row[1])),
            corner_normals=Rows(data['corner_normals'], lambda i, row: NS(vector=row)),
            loop_triangles=Rows(data['loop_triangles'], lambda i, row: NS(vertices=row[:3], loops=row[3:6], polygon_index=row[6])),
            has_custom_normals=data['has_custom_normals'], normals_domain=data['normals_domain'], attributes=attributes)
        return validate_mesh(mesh, data['matrix_world'])
    except (AttributeError, TypeError, KeyError, IndexError, ValueError, OverflowError) as error:
        fail('Exported native arrays are incomplete or invalid', reason=str(error))


def bind_observation(option, source, object_id, expected_modifier, *, sparse=False):
    try:
        return _bind_observation(option, source, object_id, expected_modifier, sparse=sparse)
    except (AttributeError, TypeError, KeyError, IndexError, ValueError, OverflowError) as error:
        fail('Pinned observation fields are missing or malformed', reason=str(error))
