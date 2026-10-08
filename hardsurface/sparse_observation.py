"""Opt-in authenticated L0 source -> isolated L2 observation; no saved edits.

The legacy observer owns diagnostic shading, frozen proxies and render checks.
This adapter owns source authentication, the evaluation clone and preservation.
HOST tests establish contracts only. Native execution remains separately gated.
"""
from copy import deepcopy
import time
import sys

from . import contract as c
from .io import RuntimeFailure, descriptor, checked_path, verify_descriptor
from .source_modifier_contract import _SOURCE_MODIFIER

MODE = 'source_bound_sparse_observation_v1'
OPTION = c.obj({'mode': c.const(MODE), 'object_id': c.UUID,
                'expected_source_modifier': _SOURCE_MODIFIER, 'level': c.const(2)})
BASELINE = {'levels': 0, 'render_levels': 0, 'quality': 6,
            'use_limit_surface': True, 'use_creases': True,
            'show_viewport': True, 'show_render': True, 'use_custom_normals': False}


def effective_modifier(source):
    """Declared L2 delta; all other settings must stay byte-identical."""
    result = deepcopy(source)
    result.update(levels=2, render_levels=2, show_viewport=True, show_render=True)
    return result


def validate_option(params):
    option = params.get('sparse_evaluation')
    if option is None:
        return
    if (params.get('normal_policy') != 'geometry_normals_v1'
            or params['wire']['enabled'] is not False
            or type(option['level']) is not int
            or any(type(option['expected_source_modifier'][k]) is not type(v)
                   or option['expected_source_modifier'][k] != v for k, v in BASELINE.items())):
        raise c.ContractError('INVALID_REQUEST', 'Sparse observation requires an authenticated enabled L0 quality-6 baseline, explicit L2, geometry_normals_v1 and wire.enabled=false')
    for view in params['views']:
        if (view.get('visible_object_ids') != [option['object_id']]
                or view['mesh_state'] != 'evaluated' or view['explode_z_mm']):
            raise c.ContractError('INVALID_REQUEST', 'Sparse observation requires one exact native object, evaluated views and no display explosion')


def cleanup_owned(created, guard, *, primary_error=None):
    """Attempt every owned release, preserve primary failure as chained cause."""
    failures = []
    for collection, item, kind in reversed(created):
        try:
            if kind == 'OBJECT':
                collection.remove(item, do_unlink=True)
            else:
                collection.remove(item)
        except Exception as error:
            failures.append({'stage': 'owned_cleanup', 'kind': kind,
                             'error_type': type(error).__name__, 'message': str(error)})
    try:
        guard('after_clone_cleanup')
    except Exception as error:
        failures.append({'stage': 'original_preservation', 'error_type': type(error).__name__,
                         'message': str(error)})
    if failures:
        raise RuntimeFailure('OBSERVATION_SPARSE_CLEANUP',
            'Owned cleanup or final original-source preservation did not complete',
            failures=failures, primary_error=(None if primary_error is None else
                {'error_type': type(primary_error).__name__, 'message': str(primary_error)})) from primary_error


def execute_sparse(request, job_dir, *, observe):
    """Authenticate original, observe only owned copy, recheck original throughout."""
    import bpy
    from . import observation_normals as normals
    from .observation import _mesh_record, _matrix_values
    from .ops.geometry import require_si_scene
    from .core import loaded_structure_registry
    from .subdivision import (prepare_source_evaluation, configure_source_clone_modifier,
        inspect_sparse_authored_control_loops, source_scene_evaluation,
        _mesh_signature, _authored_metadata_signature, estimate_faces, LIMITS,
        SPARSE_DIAGNOSTIC_PROFILE)
    from .subdivision_source import evaluated_bore_domain, declared_modifier_settings

    started = time.monotonic()
    p = request['params']; option = p['sparse_evaluation']; oid = option['object_id']
    source_before = verify_descriptor(p['source'])
    if descriptor(checked_path(bpy.data.filepath))['sha256'] != source_before['sha256']:
        raise RuntimeFailure('OBSERVATION_SOURCE', 'Opened file differs from pinned sparse source')
    scene = bpy.context.scene; units = require_si_scene()
    if len(scene.objects) > 4096:
        raise RuntimeFailure('OBSERVATION_SCENE_LIMIT', 'Original sparse source scene exceeds 4096 objects')
    matches = [obj for obj in scene.objects if obj.get('hs_object_id') == oid]
    if len(matches) != 1:
        raise RuntimeFailure('OBSERVATION_OBJECT_ID', 'Sparse observation requires a unique source UUID')
    original = matches[0]
    if (original.type != 'MESH' or original.mode != 'OBJECT' or original.library
            or original.override_library or original.parent or original.constraints
            or original.animation_data or original.data.shape_keys or original.instance_type != 'NONE'):
        raise RuntimeFailure('OBSERVATION_TARGET_UNSUPPORTED', 'Sparse observation requires a local static unparented authored mesh')
    if (len(original.data.vertices) > LIMITS['source_vertices']
            or len(original.data.polygons) > LIMITS['source_faces']
            or len(original.data.edges) > LIMITS['source_edges']
            or len(original.data.loops) > LIMITS['source_corners']):
        raise RuntimeFailure('OBSERVATION_GEOMETRY_LIMIT', 'Sparse source exceeds diagnosis source limits')
    if estimate_faces([len(face.vertices) for face in original.data.polygons], [2])['2'] > 250000:
        raise RuntimeFailure('OBSERVATION_GEOMETRY_LIMIT', 'Sparse L2 exceeds surface-field face limit')
    registry = loaded_structure_registry()
    profile = {'mode': SPARSE_DIAGNOSTIC_PROFILE,
               'expected_source_modifier': option['expected_source_modifier']}
    evaluation, semantic = prepare_source_evaluation(original, profile, registry=registry,
                                                      unit_scale=units['scale_length'])
    loop_checks = inspect_sparse_authored_control_loops(original, semantic)
    if loop_checks['status'] != 'pass':
        raise RuntimeFailure('SUBDIVISION_SOURCE_BINDING', 'Sparse authored control-loop validation failed')
    scene_profile = source_scene_evaluation(scene)
    graph = bpy.context.evaluated_depsgraph_get()
    source_normals = normals.source_record(original, 'control', graph, _mesh_record)
    original_signature = _mesh_signature(original)
    metadata = _authored_metadata_signature(original)
    matrix = _matrix_values(original.matrix_world)
    registry_hash = c.fingerprint(registry)
    checks = []

    def guard(phase):
        if time.monotonic() - started > p['wall_seconds']:
            raise RuntimeFailure('OBSERVATION_DEADLINE', 'Sparse observation total wall budget exhausted')
        if (verify_descriptor(p['source']) != source_before
                or _mesh_signature(original) != original_signature
                or _authored_metadata_signature(original) != metadata
                or _matrix_values(original.matrix_world) != matrix
                or c.fingerprint(source_scene_evaluation(scene)) != c.fingerprint(scene_profile)):
            raise RuntimeFailure('OBSERVATION_SOURCE_CHANGED', 'Original sparse source changed during clone observation', phase=phase)
        # Registry is scene-bound; never read the temporary scene as the source.
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            current_registry = loaded_structure_registry()
            if c.fingerprint(current_registry) != registry_hash:
                raise RuntimeFailure('OBSERVATION_SOURCE_CHANGED', 'Original source registry changed', phase=phase)
            current_evaluation, current_semantic = prepare_source_evaluation(
                original, profile, registry=current_registry, unit_scale=units['scale_length'])
            normals.require_equal(evaluation, current_evaluation, role='sparse_source_evaluation_' + phase)
            normals.require_equal(semantic['evidence'], current_semantic['evidence'], role='sparse_source_semantics_' + phase)
            normals.require_equal(source_normals, normals.source_record(original, 'control',
                bpy.context.evaluated_depsgraph_get(), _mesh_record), role='sparse_original_control_' + phase)
        checks.append({'phase': phase, 'original_source_identity': 'pass'})

    created = []
    try:
        guard('before_clone')
        temporary_scene = bpy.data.scenes.new('HS sparse L2 observation'); created.append((bpy.data.scenes, temporary_scene, 'SCENE'))
        temporary_scene.unit_settings.system = 'METRIC'; temporary_scene.unit_settings.scale_length = 1.
        temporary_scene.render.use_simplify = False
        temporary_scene.frame_set(scene_profile['frame_current'])
        mesh = original.data.copy(); created.append((bpy.data.meshes, mesh, 'MESH'))
        clone = original.copy(); created.append((bpy.data.objects, clone, 'OBJECT')); clone.data = mesh
        temporary_scene.collection.objects.link(clone)
        clone.matrix_world = original.matrix_world.copy()
        clone_profile = configure_source_clone_modifier(clone.modifiers[0],
            evaluation['modifier_profile']['actual_source_modifier'], 2)
        normals.require_equal(source_normals['source_control_mesh'], normals.mesh_record(mesh, _mesh_record),
                              role='sparse_clone_control')
        inner_request = deepcopy(request); del inner_request['params']['sparse_evaluation']
        with bpy.context.temp_override(scene=temporary_scene, view_layer=temporary_scene.view_layers[0]):
            bpy.context.view_layer.update()
            evaluated = clone.evaluated_get(bpy.context.evaluated_depsgraph_get())
            sample = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get())
            try:
                transport = evaluated_bore_domain(sample, semantic, level=2)['evidence']
                expected_l2 = normals.mesh_record(sample, _mesh_record)
            finally:
                evaluated.to_mesh_clear()
            report = observe(inner_request, job_dir, _source_guard=guard)
        for view in report['previews']:
            source_record = view['temporary_transformations'][0]['frozen_geometry']['normal_isolation']['source']
            normals.require_equal(expected_l2, source_record['source_mesh'], role='sparse_L2_vs_render')
            normals.require_equal(declared_modifier_settings(clone_profile['actual_clone_modifier']),
                declared_modifier_settings(source_record['source_modifier']['settings']), role='sparse_effective_modifier_vs_render')
        guard('after_observation')
    finally:
        # Only invocation-owned, unsaved datablocks. No filesystem cleanup.
        cleanup_owned(created, guard, primary_error=sys.exc_info()[1])
    report['sparse_evaluation'] = {
        'mode': MODE, 'source_object_id': oid, 'source_scene': scene.name,
        'source_modifier': deepcopy(option['expected_source_modifier']),
        'effective_modifier': declared_modifier_settings(clone_profile['actual_clone_modifier']),
        'level': 2, 'source_level': 0, 'source_binding': evaluation,
        'source_control_loops': loop_checks, 'semantic_transport': transport,
        'original_control_identity': source_normals['source_control_mesh'],
        'original_source_checks': checks, 'original_source_preserved': True,
        'evaluation_scope': 'owned_separate_scene_clone_only',
        'material_scope': 'temporary_diagnostic_material_not_production_material',
        'native_qualification': 'not_granted'}
    report['elapsed_seconds'] = time.monotonic() - started
    return report
