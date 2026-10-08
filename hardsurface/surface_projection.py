"""Native intrinsic calibration for at most two pinned observation cameras.

This module imports no Blender modules and performs no file I/O. The caller
verifies the report descriptor and binds each camera to that report before
calling this helper. Old extrinsics are copied as JSON, never assigned to a
new object, decomposed, re-aimed, or regenerated. Unrecorded aspect/shift values
must be explicit caller declarations; they are not old native attestations.
"""
from __future__ import annotations

import copy
import math
from pathlib import PurePosixPath
import re

from .io import RuntimeFailure


INPUT_ORIGIN = 'caller_declared_unrecorded_intrinsics'
_SHA = re.compile(r'^[0-9a-f]{64}$')
_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,47}$')
_RECORD_KEYS = {'name', 'camera', 'width', 'height', 'report',
                'producer_identity', 'projection_inputs', 'projection_inputs_origin'}
_CAMERA_REQUIRED = {'type', 'sensor_fit', 'ortho_scale_mm', 'matrix_world',
                    'clip_start_m', 'clip_end_m'}
_CAMERA_OPTIONAL = {'position_mm', 'target_mm', 'up_axis'}
_INPUT_KEYS = {'pixel_aspect_x', 'pixel_aspect_y', 'resolution_percentage',
               'shift_x', 'shift_y'}


def _invalid(message, path):
    raise RuntimeFailure('SURFACE_PROJECTION_INPUT', message, path=path)


def _number(value, path, *, minimum=-1e9, maximum=1e9, positive=False):
    if type(value) not in (int, float):
        _invalid('A finite numeric scalar, excluding booleans, is required', path)
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid or (positive and value <= 0):
        _invalid('Numeric scalar is nonfinite or outside the supported bounds', path)
    return value


def _integer(value, path, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid('An integer within the supported bounds is required', path)
    return value


def _fields(value, path, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        _invalid('Object has missing or unsupported fields', path)


def _descriptor(value, path):
    _fields(value, path, {'file', 'sha256', 'bytes'})
    file = value['file']
    if (not isinstance(file, str) or not 1 <= len(file) <= 4096 or '\x00' in file
            or not PurePosixPath(file).is_absolute() or '..' in PurePosixPath(file).parts):
        _invalid('An absolute non-traversing descriptor path is required', path + '.file')
    if not isinstance(value['sha256'], str) or not _SHA.fullmatch(value['sha256']):
        _invalid('A lowercase SHA-256 descriptor is required', path + '.sha256')
    _integer(value['bytes'], path + '.bytes', 1, 2**63 - 1)


def _matrix(value, path, *, recorded=False):
    if not isinstance(value, list) or len(value) != 4:
        _invalid('A four-by-four row-major JSON matrix is required', path)
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 4:
            _invalid('A four-by-four row-major JSON matrix is required', path)
        for column, item in enumerate(row):
            _number(item, f'{path}[{row_index}][{column}]')
    if recorded:
        if value[3] != [0, 0, 0, 1]:
            _invalid('Recorded camera matrix must be affine', path)
        a, b, c = (row[:3] for row in value[:3])
        determinant = (a[0] * (b[1] * c[2] - b[2] * c[1])
                       - a[1] * (b[0] * c[2] - b[2] * c[0])
                       + a[2] * (b[0] * c[1] - b[1] * c[0]))
        if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
            _invalid('Recorded camera matrix must be invertible', path)


def validate_camera_record(view):
    """Validate the binder's pinned record without opening files or using bpy.

    Required keys are ``name, camera, width, height, report, producer_identity,
    projection_inputs, projection_inputs_origin``. ``camera`` is the original
    report camera object; ``report`` is its verified file/SHA-256/bytes reference.
    ``producer_identity`` is the report implementation's source SHA-256 and
    Blender descriptor (optional Python descriptor/version may be retained).
    ``projection_inputs`` requires both pixel aspects, both shifts, and exactly
    100 percent resolution. The percentage restriction makes the report's final
    pixel dimensions the exact native call dimensions without reverse scaling.
    This validates structure; binding content to a verified report is the
    caller's responsibility. The returned copy preserves recorded numeric JSON.
    """
    _fields(view, '$', _RECORD_KEYS)
    if not isinstance(view['name'], str) or not _NAME.fullmatch(view['name']):
        _invalid('A bounded observation view name is required', '$.name')
    for key in ('width', 'height'):
        _integer(view[key], '$.' + key, 16, 2048)
    _descriptor(view['report'], '$.report')
    producer = view['producer_identity']
    _fields(producer, '$.producer_identity', {'source_sha256', 'blender'},
            {'python', 'python_version'})
    if not isinstance(producer['source_sha256'], str) or not _SHA.fullmatch(producer['source_sha256']):
        _invalid('The original producer source SHA-256 is required', '$.producer_identity.source_sha256')
    _descriptor(producer['blender'], '$.producer_identity.blender')
    if 'python' in producer:
        _descriptor(producer['python'], '$.producer_identity.python')
    if 'python_version' in producer and (not isinstance(producer['python_version'], str)
                                       or not 1 <= len(producer['python_version']) <= 512):
        _invalid('A bounded Python version string is required', '$.producer_identity.python_version')
    if view['projection_inputs_origin'] not in (INPUT_ORIGIN, 'native_render_time_recorded'):
        _invalid('Unrecorded scalars must be identified as caller declarations', '$.projection_inputs_origin')
    inputs = view['projection_inputs']
    _fields(inputs, '$.projection_inputs', _INPUT_KEYS)
    for key in ('pixel_aspect_x', 'pixel_aspect_y'):
        _number(inputs[key], '$.projection_inputs.' + key, minimum=0, maximum=200, positive=True)
    for key in ('shift_x', 'shift_y'):
        _number(inputs[key], '$.projection_inputs.' + key, minimum=-10, maximum=10)
    _integer(inputs['resolution_percentage'], '$.projection_inputs.resolution_percentage', 100, 100)
    camera = view['camera']
    _fields(camera, '$.camera', _CAMERA_REQUIRED, _CAMERA_OPTIONAL)
    if camera['type'] != 'ORTHO' or camera['sensor_fit'] != 'VERTICAL':
        _invalid('Only recorded ORTHO/VERTICAL observation cameras are supported', '$.camera')
    _number(camera['ortho_scale_mm'], '$.camera.ortho_scale_mm', minimum=.1, maximum=100000)
    for key in ('clip_start_m', 'clip_end_m'):
        _number(camera[key], '$.camera.' + key, minimum=0, maximum=1e9, positive=True)
    if camera['clip_start_m'] >= camera['clip_end_m']:
        _invalid('Camera far clip must exceed near clip', '$.camera')
    _matrix(camera['matrix_world'], '$.camera.matrix_world', recorded=True)
    for key in ('position_mm', 'target_mm'):
        if key in camera:
            if not isinstance(camera[key], list) or len(camera[key]) != 3:
                _invalid('A recorded three-coordinate vector is required', '$.camera.' + key)
            for index, value in enumerate(camera[key]):
                _number(value, f'$.camera.{key}[{index}]', minimum=-100000, maximum=100000)
    if 'up_axis' in camera and camera['up_axis'] not in ('Y', 'Z'):
        _invalid('Unsupported recorded camera up axis', '$.camera.up_axis')
    return copy.deepcopy(view)


def _native_attr(owner, name):
    try:
        return getattr(owner, name)
    except Exception as exc:
        raise RuntimeFailure('SURFACE_PROJECTION_API', 'Required Blender API is unavailable',
                             attribute=name) from exc


def _native_callable(owner, name):
    method = _native_attr(owner, name)
    if not callable(method):
        raise RuntimeFailure('SURFACE_PROJECTION_API', 'Required Blender API is not callable',
                             attribute=name)
    return method


def _readback(data, expected):
    actual = {}
    for key, value in expected.items():
        observed = _native_attr(data, key)
        if isinstance(value, str):
            matches = observed == value
        else:
            _number(observed, '$.native_camera_attributes.' + key)
            matches = math.isclose(observed, value, rel_tol=2e-6, abs_tol=1e-14)
        if not matches:
            raise RuntimeFailure('SURFACE_PROJECTION_NATIVE_MISMATCH',
                                 'Blender camera scalar readback differs from the declared input',
                                 property=key, expected=value, observed=observed)
        actual[key] = observed
    return actual


def calibrate_views(records, bpy, scene, created):
    """Call Blender's intrinsic API, preserving the original extrinsics verbatim.

    ``scene`` must be a caller-owned temporary diagnosis scene. Every new camera
    datablock/object is immediately appended as ``(bpy.data.collection, value)``
    to the caller's ``created`` list. The caller must reverse-clean that list in
    its existing finally block, including after failure. This helper never sets
    a camera pose, active camera, render settings, lights, or source data, never
    renders, and never saves a blend. No hand-built projection matrix is used.
    """
    if not isinstance(records, list) or not 1 <= len(records) <= 2:
        _invalid('One or two pinned observation camera records are required', '$.views')
    if type(created) is not list:
        _invalid('The caller-owned temporary datablock cleanup list is required', '$.created')
    normalized = [validate_camera_record(record) for record in records]
    if len({record['name'] for record in normalized}) != len(normalized):
        _invalid('Pinned observation view names must be unique', '$.views')

    try:
        cameras = _native_attr(_native_attr(bpy, 'data'), 'cameras')
        objects = _native_attr(bpy.data, 'objects')
        new_camera = _native_callable(cameras, 'new')
        new_object = _native_callable(objects, 'new')
        link = _native_callable(_native_attr(_native_attr(scene, 'collection'), 'objects'), 'link')
        layer = _native_attr(scene, 'view_layers')[0]
        update = _native_callable(layer, 'update')
        context = _native_attr(bpy, 'context')
        override = _native_callable(context, 'temp_override')
        get_depsgraph = _native_callable(context, 'evaluated_depsgraph_get')
        app = _native_attr(bpy, 'app')
        version = _native_attr(app, 'version_string')
        build_hash = _native_attr(app, 'build_hash')
        if isinstance(build_hash, bytes):
            build_hash = build_hash.decode('ascii')
        if not isinstance(version, str) or not version or not isinstance(build_hash, str) or not build_hash:
            raise RuntimeFailure('SURFACE_PROJECTION_API', 'Blender version/build identity is unavailable')

        result = []
        for view in normalized:
            old, inputs = view['camera'], view['projection_inputs']
            camera_data = new_camera('HS Surface intrinsic calibration ' + view['name'])
            created.append((cameras, camera_data))
            camera = new_object(camera_data.name, camera_data)
            created.append((objects, camera))
            native_call = _native_callable(camera, 'calc_matrix_camera')
            link(camera)
            settings = {'type': old['type'], 'sensor_fit': old['sensor_fit'],
                        'ortho_scale': old['ortho_scale_mm'] * .001,
                        'clip_start': old['clip_start_m'], 'clip_end': old['clip_end_m'],
                        'shift_x': inputs['shift_x'], 'shift_y': inputs['shift_y']}
            for key, value in settings.items():
                _native_attr(camera_data, key)
                setattr(camera_data, key, value)
            actual = _readback(camera_data, settings)
            arguments = {'x': view['width'], 'y': view['height'],
                         'scale_x': inputs['pixel_aspect_x'], 'scale_y': inputs['pixel_aspect_y']}
            with override(scene=scene, view_layer=layer):
                update()
                depsgraph = get_depsgraph()
                if depsgraph is None:
                    raise RuntimeFailure('SURFACE_PROJECTION_API', 'Blender dependency graph is unavailable')
                native_matrix = native_call(depsgraph, **arguments)
                matrix = [list(row) for row in native_matrix]
            _matrix(matrix, '$.projection_matrix')
            result.append({
                'schema_version': '1.0', 'kind': 'blender_native_camera_projection',
                'view': view['name'], 'report': copy.deepcopy(view['report']),
                'producer_identity': copy.deepcopy(view['producer_identity']),
                'recorded_camera': copy.deepcopy(old),
                'camera_to_world_matrix': copy.deepcopy(old['matrix_world']),
                'projection_matrix': matrix,
                'resolution': {'width': view['width'], 'height': view['height'],
                               'percentage': inputs['resolution_percentage']},
                'projection_inputs': copy.deepcopy(inputs),
                'projection_inputs_origin': view['projection_inputs_origin'],
                'native_camera_attributes': actual,
                'native_call': {'api': 'bpy.types.Object.calc_matrix_camera', **arguments},
                'calibration_runtime': {'blender_version': version, 'blender_build_hash': build_hash},
                'matrix_convention': {
                    'storage': 'row_major_4x4', 'vectors': 'column_vectors',
                    'camera_to_world': 'recorded camera.matrix_world; translation in metres',
                    'camera_axes': '+X right; +Y up; -Z forward',
                    'world_to_clip': 'projection_matrix @ inverse(camera_to_world_matrix) @ world_position_m_homogeneous',
                    'clip_to_ndc': 'divide clip xyz by clip w',
                    'ndc_axes': '+X right; +Y up; depth from -1 near to +1 far',
                    'pixel_origin': 'top_left',
                    'ndc_to_pixel_edges': 'x=(ndc_x+1)*width/2; y=(1-ndc_y)*height/2',
                },
                'provenance': {
                    'extrinsics': 'exact original report matrix JSON; not recreated or assigned to a camera',
                    'recorded_intrinsics': ['type', 'sensor_fit', 'ortho_scale_mm', 'clip_start_m', 'clip_end_m'],
                    'declared_unrecorded_inputs': sorted(_INPUT_KEYS) if view['projection_inputs_origin'] == INPUT_ORIGIN else [],
                    **({'native_render_time_inputs': sorted(_INPUT_KEYS)} if view['projection_inputs_origin'] == 'native_render_time_recorded' else {}),
                    'projection': 'returned by Blender native calc_matrix_camera on an owned temporary camera',
                    'old_unrecorded_intrinsics_attested': False,
                    'original_pose_recreated': False, 'render_performed': False,
                    'blend_save_performed': False,
                },
                'limitations': ([
                    'Old pixel aspect, shifts and resolution percentage were not recorded; these are caller declarations, not old native attestations.',
                    'The observation producer sets square pixel aspect and 100 percent resolution and creates a fresh camera; zero shifts are a default assumption, not a directly recorded value.',
                    'Projection is calculated by the current recorded Blender runtime; original report producer identity is retained separately.',
                ] if view['projection_inputs_origin'] == INPUT_ORIGIN else [
                    'Projection is recalculated by the recorded Blender runtime from native render-time inputs; it does not render an image or establish visual acceptance.',
                ]),
            })
        return result
    except RuntimeFailure:
        raise
    except Exception as exc:
        raise RuntimeFailure('SURFACE_PROJECTION_NATIVE', 'Native camera calibration failed',
                             reason=str(exc)) from exc
