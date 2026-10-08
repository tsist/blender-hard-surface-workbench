"""Bounded, read-only observations of an already opened, guarded .blend.

Schema and validation are usable in the host without Blender. The worker must
perform its static dependency audit and enforce the wall deadline externally;
an in-process deadline cannot interrupt a blocked Blender render. No blend is
saved and all temporary scene changes are restored, including on render failure.
"""
from __future__ import annotations

import copy
import hashlib
import math
import struct
import time
from pathlib import Path

from . import contract as c
from .io import RuntimeFailure, checked_path, descriptor, verify_descriptor
from .sparse_observation import OPTION as SPARSE_OBSERVATION_OPTION
from . import reflection_anchor

SAMPLES = 32
# Development-only observation preset: fixed, texture-free display diagnostics.
# These values are neither an asset material nor a surface-quality score.
DIAGNOSTIC_PRESETS = ('neutral', 'reflection_strips')
REFLECTION_SAMPLES = 128
REFLECTION_LIGHTS = ((-.75, 0, 2.5, 60), (0, 0, 2.5, 60), (.75, 0, 2.5, 60))
LEGACY_LIGHTS = ((-3, 4, 6, 500), (4, 1, 3, 300), (0, -3, -4, 350))
_REFLECTION_ROTATION = c.optional_default(c.number(minimum=-180, maximum=180), 0)


def diagnostic_settings(preset, reflection_rotation_degrees=0):
    """Independent fixed values; host tests need no Blender import."""
    if preset not in DIAGNOSTIC_PRESETS:
        raise c.ContractError('INVALID_REQUEST', 'Unknown observation diagnostic preset')
    angle = c._validate(reflection_rotation_degrees, _REFLECTION_ROTATION,
                        '$.params.reflection_rotation_degrees')
    reflective = preset == 'reflection_strips'
    if not reflective and angle != 0:
        raise c.ContractError('INVALID_REQUEST',
                              'Nonzero reflection rotation requires diagnostic_preset=reflection_strips',
                              '$.params.reflection_rotation_degrees')
    return {
        'preset': preset,
        'reflection_rotation_degrees': angle,
        'reflection_rotation_semantics': 'counterclockwise_in_camera_image; camera_unchanged',
        'contract': 'HS_REFLECTION_STRIPS_V1' if reflective else 'HS_NEUTRAL_LEGACY_V1',
        'material_override': {'base_color': [.72, .72, .72, 1],
                              'roughness': .08 if reflective else .5,
                              'metallic': 1.0 if reflective else 0.0},
        'world_color': [.08, .08, .08, 1], 'world_strength': .025 if reflective else .4,
        'samples': REFLECTION_SAMPLES if reflective else SAMPLES,
        'denoising': not reflective,
        'light_shape': 'RECTANGLE' if reflective else 'DISK',
        'light_size_extent': .12 if reflective else 4.0,
        'light_size_y_extent': 4.0 if reflective else None,
        'lights_camera_basis': [list(v) for v in (REFLECTION_LIGHTS if reflective else LEGACY_LIGHTS)],
        'light_orientation': 'parallel_to_camera_plane' if reflective else 'aim_at_center',
        'scope': 'temporary_view_layer_override_on_frozen_proxies',
        'textures_created': False, 'surface_quality_inference': 'none',
    }


def _rotated_camera_plane_basis(right, camera_up, angle):
    """Rotate a right-handed camera XY basis about outward (+camera Z).

    The long rectangle axis is local Y: 0 degrees is camera-up (vertical),
    +90 degrees is negative camera-right (horizontal). Spacing is local X.
    Return original values at zero so legacy floating-point placement/aiming
    remains exactly unchanged. Inputs are the orthonormal camera axes.
    """
    if angle == 0:
        return right, camera_up
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    return (tuple(r * cosine + u * sine for r, u in zip(right, camera_up)),
            tuple(-r * sine + u * cosine for r, u in zip(right, camera_up)))

MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_COORDINATE_MM = 100000
DIRECTIONS = {
    'front': (0, -4, 0), 'top': (0, 0, 4),
    'three_quarter': (3, -4, 3), 'side': (4, 0, 0),
    'right': (4, 0, 0), 'back': (0, 4, 0), 'bottom': (0, 0, -4),
}
_NAME = c.string(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,47}$', maxLength=48)
_COORD = c.number(minimum=-MAX_COORDINATE_MM, maximum=MAX_COORDINATE_MM)
_V3 = c.array(_COORD, 3, 3)
_CAMERA = c.obj({
    'position_mm': _V3, 'target_mm': _V3, 'up_axis': c.const('Z'),
    'ortho_scale_mm': c.number(minimum=.1, maximum=MAX_COORDINATE_MM),
})
_SELECTION_ID = c.string(
    pattern='(?:' + c.ID_PATTERN + '|' + c.UUID_PATTERN + ')', maxLength=96)
_EXPLOSION = {
    'type': 'object', 'properties': {}, 'maxProperties': 128,
    'propertyNames': copy.deepcopy(_SELECTION_ID),
    'additionalProperties': c.number(minimum=-500, maximum=500), 'default': {},
}
_COMMON_VIEW = {
    'reflection_anchor': reflection_anchor.OPTION,
    'name': _NAME,
    'mesh_state': c.enum('control','evaluated'),
    'explode_z_mm': _EXPLOSION,
    'width': c.optional_default(c.integer(minimum=16, maximum=2048), 1024),
    'height': c.optional_default(c.integer(minimum=16, maximum=2048), 1024),
}
_VIEW = c.union(
    *(c.obj({**_COMMON_VIEW, selector: c.array(identity, 1, 128, uniqueItems=True),
             projection: configuration}, ['name', selector, projection])
      for selector, identity in (('visible_feature_ids', c.IDENT), ('visible_object_ids', c.UUID))
      for projection, configuration in (('direction', c.enum(*DIRECTIONS)), ('camera', _CAMERA)))
)
REQUEST = c.obj({
    'schema_version': c.const('1.0'), 'command': c.const('hardsurface.observe'),
    'params': c.obj({
        'request_id': c.string(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', minLength=1, maxLength=128),
        'source': c.FILE, 'views': c.array(_VIEW, 1, 16),
        'wire': c.optional_default(c.WIRE_STYLE,{}),
        'diagnostic_preset': c.optional_default(c.enum(*DIAGNOSTIC_PRESETS), 'neutral'),
        'reflection_rotation_degrees': _REFLECTION_ROTATION,
        # No default: old normalized requests and their fingerprints are stable.
        'normal_policy': c.const('geometry_normals_v1'),
        'sparse_evaluation': SPARSE_OBSERVATION_OPTION,
        'max_tree_rss_bytes': c.integer(minimum=1024**3, maximum=4*1024**3),
        'cpu_threads': c.optional_default(c.integer(minimum=1, maximum=4), 2),
        'wall_seconds': c.optional_default(c.number(exclusiveMinimum=0, maximum=600), 600),
    }, ['request_id', 'source', 'views']),
})


def schema():
    """Return an independent draft-2020-12 schema for the observation request."""
    result = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
              'title': 'Hard Surface Workbench read-only observation 1.0',
              **copy.deepcopy(REQUEST)}
    # Mirror the runtime cross-field rule for external JSON Schema clients.
    result['properties']['params']['allOf'] = [{
        'if': {'required': ['diagnostic_preset'],
               'properties': {'diagnostic_preset': {'const': 'reflection_strips'}}},
        'then': {'required': ['wire'], 'properties': {
            'wire': {'required': ['enabled'], 'properties': {'enabled': {'const': False}}}}},
        'else': {'properties': {'reflection_rotation_degrees': {'const': 0}}},
    }]
    result['properties']['params']['allOf'].append({
        'if': {'properties': {'views': {'contains': {'required': ['reflection_anchor']}}}},
        'then': {'required': ['normal_policy', 'diagnostic_preset'], 'properties': {
            'normal_policy': {'const': 'geometry_normals_v1'},
            'diagnostic_preset': {'const': 'reflection_strips'}}}})
    for variant in result['properties']['params']['properties']['views']['items']['oneOf']:
        variant['allOf'] = [{'if': {'required': ['reflection_anchor']}, 'then': {
            'required': ['camera', 'visible_object_ids'], 'properties': {
                'mesh_state': {'const': 'evaluated'}, 'explode_z_mm': {'maxProperties': 0},
                'visible_object_ids': {'minItems': 1, 'maxItems': 1}}}}]
    return result


def _check(value, node, path='$'):
    """Contract checking plus bounded, named map support for explosion offsets."""
    if 'oneOf' in node:
        matches = []
        for variant in node['oneOf']:
            try:
                matches.append(_check(value, variant, path))
            except c.ContractError:
                pass
        if len(matches) != 1:
            raise c.ContractError('INVALID_REQUEST', 'Expected exactly one view configuration', path)
        return matches[0]
    if node.get('type') == 'object':
        if not isinstance(value, dict):
            raise c.ContractError('INVALID_REQUEST', 'Expected object', path)
        if len(value) > node.get('maxProperties', 100000):
            raise c.ContractError('INVALID_REQUEST', 'Too many map entries', path)
        properties = node['properties']
        missing = set(node.get('required', [])) - set(value)
        if missing:
            raise c.ContractError('INVALID_REQUEST', 'Missing fields: ' + str(sorted(missing)), path)
        result = {}
        for key, item in value.items():
            if 'propertyNames' in node:
                c._validate(key, node['propertyNames'], path)
            spec = properties.get(key, node.get('additionalProperties', False))
            if spec is False:
                raise c.ContractError('INVALID_REQUEST', 'Unknown field: ' + key, path)
            result[key] = _check(item, spec, path + '.' + key)
        for key, spec in properties.items():
            if key not in result and 'default' in spec:
                result[key] = _check(copy.deepcopy(spec['default']), spec, path + '.' + key)
        return result
    if node.get('type') == 'array':
        if not isinstance(value, list) or not node.get('minItems', 0) <= len(value) <= node.get('maxItems', 100000):
            raise c.ContractError('INVALID_REQUEST', 'Array length/type outside bounds', path)
        if node.get('uniqueItems') and len({c.canonical_bytes(v) for v in value}) != len(value):
            raise c.ContractError('INVALID_REQUEST', 'Duplicate array elements', path)
        return [_check(item, node['items'], f'{path}[{i}]') for i, item in enumerate(value)]
    return c._validate(value, node, path)


def validate_request(request):
    """Normalize a strict JSON request without opening files or importing bpy."""
    if isinstance(request, (str, bytes, bytearray)):
        request = c.strict_loads(request)
    c._walk_limits(request)
    if len(c.canonical_bytes(request)) > c.MAX_BYTES:
        raise c.ContractError('LIMIT_EXCEEDED', 'Request exceeds 2 MiB')
    result = _check(request, REQUEST)
    c._paths(result)
    p = result['params']
    if 'max_tree_rss_bytes' in p and type(request['params']['max_tree_rss_bytes']) is not int:
        raise c.ContractError('INVALID_REQUEST', 'Memory budget must be an integer byte count',
                              '$.params.max_tree_rss_bytes')
    if p['diagnostic_preset'] != 'reflection_strips' and p['reflection_rotation_degrees'] != 0:
        raise c.ContractError('INVALID_REQUEST',
                              'Nonzero reflection rotation requires diagnostic_preset=reflection_strips',
                              '$.params.reflection_rotation_degrees')
    if p['diagnostic_preset'] == 'reflection_strips' and p['wire']['enabled']:
        raise c.ContractError('INVALID_REQUEST',
                              'Reflection strips require explicit wire.enabled=false; mesh edges obscure reflected highlights',
                              '$.params.wire.enabled')
    if Path(p['source']['file']).suffix.lower() != '.blend':
        raise c.ContractError('INVALID_REQUEST', 'Source must be a saved .blend file')
    names = [view['name'] for view in p['views']]
    if len(names) != len(set(names)):
        raise c.ContractError('INVALID_REQUEST', 'View names must be unique')
    for index, view in enumerate(p['views']):
        view.setdefault('mesh_state',p['wire']['mesh_state'])
        path = f'$.params.views[{index}]'
        selector = 'visible_object_ids' if 'visible_object_ids' in view else 'visible_feature_ids'
        if set(view['explode_z_mm']) - set(view[selector]):
            raise c.ContractError('INVALID_REQUEST', 'Explosion IDs must be explicitly visible in this view', path)
        if 'camera' in view:
            camera = view['camera']
            ray = [t - a for a, t in zip(camera['position_mm'], camera['target_mm'])]
            if math.sqrt(sum(x * x for x in ray)) < .001:
                raise c.ContractError('INVALID_REQUEST', 'Camera position and target must differ by at least .001 mm', path)
            # The specified Z up axis cannot define roll for a vertical ray.
            if math.hypot(ray[0], ray[1]) <= math.sqrt(sum(x * x for x in ray)) * 1e-8:
                raise c.ContractError('INVALID_REQUEST', 'Camera ray is parallel to its Z up axis; use a fixed top/bottom view', path)
    for view in p['views']:
        reflection_anchor.validate_option(p, view)
    from .sparse_observation import validate_option
    validate_option(p)
    return result


normalize_request = validate_request


def _visible(obj, view_layer=None, render_visible=None):
    if obj.hide_render or getattr(obj, 'hide_viewport', False):
        return False
    if render_visible is not None and obj.name not in render_visible:
        return False
    if hasattr(obj, 'hide_get') and obj.hide_get(view_layer=view_layer):
        return False
    if hasattr(obj, 'visible_get') and not obj.visible_get(view_layer=view_layer):
        return False
    return True


def _render_visible_names(collection, parent_visible=True, visited=None):
    """An object can be visible through any one of its collection paths."""
    visited = set() if visited is None else visited
    token = (id(collection), parent_visible)
    if token in visited:
        return set()
    visited.add(token)
    visible = parent_visible and not collection.hide_render
    names = {obj.name for obj in collection.objects} if visible else set()
    for child in collection.children:
        names.update(_render_visible_names(child, visible, visited))
    return names


def _view_objects(objects, view):
    """Return each selected object once, preserving explicit selector order."""
    if 'visible_object_ids' in view:
        return {oid: objects[oid] for oid in view['visible_object_ids']}
    return {oid: objects[oid] for fid in view['visible_feature_ids']
            for oid in sorted(objects) if objects[oid].get('hs_feature_id') == fid}


def _selection_key(view, obj):
    return obj.get('hs_object_id' if 'visible_object_ids' in view else 'hs_feature_id')


def _resolve_features(objects, views, view_layer=None, render_visible=None):
    """Expand feature groups to instances keyed by unambiguous source UUID.

    Shared feature IDs and shared mesh data are valid. Each selected object
    must still have a distinct persistent identity across the source scene.
    """
    required_features = {fid for view in views for fid in view.get('visible_feature_ids', [])}
    required_objects = {oid for view in views for oid in view.get('visible_object_ids', [])}
    feature_matches = {fid: [] for fid in required_features}
    object_matches = {oid: [] for oid in required_objects}
    identities = {}
    for obj in objects:
        fid, oid = obj.get('hs_feature_id'), obj.get('hs_object_id')
        if isinstance(oid, str):
            identities.setdefault(oid, []).append(obj)
        if obj.type != 'MESH' or not _visible(obj, view_layer, render_visible):
            continue
        if isinstance(fid, str) and fid in feature_matches:
            feature_matches[fid].append(obj)
        if isinstance(oid, str) and oid in object_matches:
            object_matches[oid].append(obj)
    selected = {}
    for fid, found in feature_matches.items():
        if not found:
            raise RuntimeFailure('OBSERVATION_FEATURE_ID',
                                 'Feature has no final visible mesh instances',
                                 feature_id=fid, matches=[obj.name for obj in found])
    for oid, found in object_matches.items():
        if not found:
            raise RuntimeFailure('OBSERVATION_OBJECT_ID',
                                 'Object ID does not identify a final visible mesh', object_id=oid)
    for found in (*feature_matches.values(), *object_matches.values()):
        for obj in found:
            oid = obj.get('hs_object_id')
            try:
                c._validate(oid, c.UUID)
            except c.ContractError as error:
                raise RuntimeFailure('OBSERVATION_OBJECT_ID',
                                     'Selected mesh requires a valid persistent object UUID',
                                     object_name=obj.name, object_id=oid,
                                     feature_id=obj.get('hs_feature_id')) from error
            if len(identities[oid]) != 1:
                raise RuntimeFailure('OBSERVATION_OBJECT_ID',
                                     'Persistent object ID is ambiguous in the source scene', object_id=oid,
                                     object_names=[match.name for match in identities[oid]])
            selected[oid] = obj
    for view in views:
        count = len(_view_objects(selected, view))
        if not 1 <= count <= 128:
            raise RuntimeFailure('OBSERVATION_SELECTION',
                                 'Each view requires 1 to 128 mesh object instances', count=count)
    return {oid: selected[oid] for oid in sorted(selected)}


def _output_path(job_dir, name):
    job = Path(job_dir)
    if not job.is_absolute() or '..' in job.parts or not job.is_dir():
        raise RuntimeFailure('OBSERVATION_OUTPUT', 'Existing absolute owned job directory required')
    for part in (job, *job.parents):
        if part.is_symlink():
            raise RuntimeFailure('OBSERVATION_OUTPUT', 'Symbolic-link output directories are refused')
    c._validate(name, _NAME)
    output = checked_path(job / ('observation-' + name + '.png'), exists=False)
    if output.exists():
        raise RuntimeFailure('OUTPUT_COLLISION', 'Observation output already exists', file=str(output))
    return output


def _png_record(path, expected_width, expected_height):
    record = descriptor(path)
    if record['bytes'] > MAX_PREVIEW_BYTES:
        raise RuntimeFailure('OBSERVATION_BYTES', 'Preview exceeds the 2 MiB limit', **record)
    with open(path, 'rb') as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise RuntimeFailure('OBSERVATION_IMAGE', 'Render did not produce a PNG image', file=str(path))
    width, height = struct.unpack('>II', header[16:24])
    if (width, height) != (expected_width, expected_height):
        raise RuntimeFailure('OBSERVATION_IMAGE', 'Rendered PNG dimensions differ from the request',
                             expected=[expected_width, expected_height], observed=[width, height])
    return {**record, 'width': width, 'height': height}


def _matrix_values(matrix):
    return [[float(value) for value in row] for row in matrix]


def _identity(obj):
    return {'feature_id': obj.get('hs_feature_id'), 'object_id': obj.get('hs_object_id'),
            'data_id': obj.data.get('hs_data_id'), 'name': obj.name, 'type': obj.type,
            'data_name': obj.data.name, 'data_users': obj.data.users,
            'collections': sorted(collection.name for collection in obj.users_collection)}


def _mesh_record(mesh):
    """Content identity for evaluated local-space positions and face topology."""
    vertices, polygons, loops = len(mesh.vertices), len(mesh.polygons), len(mesh.loops)
    if vertices > 1000000 or loops > 4000000:
        raise RuntimeFailure('OBSERVATION_GEOMETRY_LIMIT', 'Evaluated mesh exceeds observation geometry budget')
    digest = hashlib.sha256(b'HS_OBSERVATION_POSITIONS_TOPOLOGY_V1\0')
    digest.update(struct.pack('>QQQ', vertices, polygons, loops))
    for vertex in mesh.vertices:
        coordinate = tuple(float(v) for v in vertex.co)
        if not all(math.isfinite(v) for v in coordinate):
            raise RuntimeFailure('OBSERVATION_GEOMETRY', 'Evaluated mesh has nonfinite coordinates')
        digest.update(struct.pack('>ddd', *coordinate))
    for polygon in mesh.polygons:
        indices = tuple(polygon.vertices)
        digest.update(struct.pack('>Q', len(indices)))
        for index in indices:
            digest.update(struct.pack('>Q', index))
    return {'vertices': vertices, 'polygons': polygons, 'loops': loops,
            'positions_topology_sha256': digest.hexdigest(),
            'hash_semantics': 'HS_OBSERVATION_POSITIONS_TOPOLOGY_V1'}


def execute(request, job_dir, *, _source_guard=None):
    """Render a finite observation set in an already opened audited worker scene.

    The host owns source guards, worker startup, dependency audit, resource
    isolation and the hard wall watchdog. This function never saves a .blend.
    """
    request = validate_request(request)
    p = request['params']
    if 'sparse_evaluation' in p:
        from .sparse_observation import execute_sparse
        return execute_sparse(request, job_dir, observe=execute)
    diagnostic = diagnostic_settings(p['diagnostic_preset'], p['reflection_rotation_degrees'])
    anchored = any('reflection_anchor' in view for view in p['views'])
    if anchored:
        diagnostic['placement_contract'] = 'HS_REFLECTION_ANCHOR_V1_per_opt_in_view'
        diagnostic['placement_note'] = 'Legacy layout values apply only to unanchored views; actual anchored layout is reported per preview'
    normal_isolation = p.get('normal_policy') == 'geometry_normals_v1'
    if normal_isolation:
        from . import observation_normals as normal_checks
    reflective = p['diagnostic_preset'] == 'reflection_strips'
    states={v['mesh_state'] for v in p['views']}
    if len(states)!=1:raise RuntimeFailure('OBSERVATION_STATE_BATCH','Each worker must observe exactly one mesh state')
    mesh_state=next(iter(states))
    source_before = verify_descriptor(p['source'])
    def output_name(view):
        base=('wire-'+view['mesh_state']+'-'+view['name']) if p['wire']['enabled'] else view['name']
        return base if len(base)<=48 else base[:39]+'-'+hashlib.sha256(base.encode()).hexdigest()[:8]
    paths = {view['name']: _output_path(job_dir, output_name(view)) for view in p['views']}
    import bpy
    from mathutils import Matrix, Vector
    from .ops.geometry import require_si_scene

    started = time.monotonic()
    source_path = checked_path(bpy.data.filepath)
    opened_before = descriptor(source_path)
    if opened_before['sha256'] != source_before['sha256']:
        raise RuntimeFailure('OBSERVATION_SOURCE', 'Opened scene file does not match the declared source SHA',
                             declared=source_before, opened=opened_before)
    units = require_si_scene()
    scene = bpy.context.scene
    if len(scene.objects) > 4096:
        raise RuntimeFailure('OBSERVATION_SCENE_LIMIT', 'Observation scene exceeds 4096 objects')
    objects = list(scene.objects)
    view_layer = bpy.context.view_layer
    source_objects = _resolve_features(objects, p['views'], view_layer,
                                       _render_visible_names(scene.collection))
    for oid, obj in source_objects.items():
        identity = {'object_id': oid, 'feature_id': obj.get('hs_feature_id')}
        if (obj.mode != 'OBJECT' or obj.parent or obj.constraints or obj.animation_data
                or obj.library or obj.override_library or obj.data.shape_keys):
            raise RuntimeFailure('OBSERVATION_TARGET_UNSUPPORTED',
                                 'Observation requires local static unparented mesh objects in Object mode', **identity)
        if not all(math.isfinite(value) for row in obj.matrix_world for value in row):
            raise RuntimeFailure('OBSERVATION_TRANSFORM', 'Source matrix has nonfinite values', **identity)
        for modifier in obj.modifiers:
            if (modifier.show_viewport != modifier.show_render
                    or (modifier.type in ('SUBSURF', 'MULTIRES') and modifier.levels != modifier.render_levels)):
                raise RuntimeFailure('OBSERVATION_EVALUATION',
                                     'Frozen observation requires matching viewport/render modifier evaluation', **identity)
    old_objects = {obj: (obj.matrix_world.copy(), obj.location.copy(), obj.hide_render, obj.hide_viewport) for obj in objects}
    original_camera, original_world = scene.camera, scene.world
    created_objects, created_data = [], []
    proxies, geometry = {}, {}
    normal_sources, normal_proxies = {}, {}
    changed = []

    def assign(owner, attr, value):
        changed.append((owner, attr, getattr(owner, attr)))
        setattr(owner, attr, value)

    def deadline():
        if time.monotonic() - started > p['wall_seconds']:
            raise RuntimeFailure('OBSERVATION_DEADLINE', 'Observation wall budget exhausted')

    def attest_normals(phase, view_name=None):
        """Re-read real source state and frozen copies, never repair shading."""
        if _source_guard is not None:
            _source_guard(phase)
        return normal_checks.attest(source_objects, proxies, normal_sources, normal_proxies,
            mesh_state=mesh_state, depsgraph=bpy.context.evaluated_depsgraph_get(), geometry_record=_mesh_record,
            view_layer=view_layer, material=material, diagnostic=diagnostic, expected_material=normal_material,
            phase=phase, view_name=view_name)

    def restore_objects():
        for obj, (matrix, location, hidden, viewport_hidden) in old_objects.items():
            # Source transforms and geometry are never written, even in memory.
            obj.hide_render = hidden
            obj.hide_viewport = viewport_hidden
        for oid, proxy in proxies.items():
            proxy.location = old_objects[source_objects[oid]][1]
            proxy.hide_render = True
        view_layer.update()

    def aim(obj, target, up):
        forward = (target - obj.location).normalized()
        right = forward.cross(up).normalized()
        camera_up = right.cross(forward).normalized()
        obj.rotation_euler = Matrix((right, camera_up, -forward)).transposed().to_euler()
        return right, camera_up

    outputs = []
    try:
        # Freeze fully evaluated bodies before hiding any source object or
        # moving any display copy. In particular, a Boolean cutter must never
        # be reevaluated against a translated target during display explosion.
        view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        total_vertices, total_loops = 0, 0
        for oid, source_object in source_objects.items():
            evaluated = source_object.evaluated_get(depsgraph)
            state_mesh=source_object.data if mesh_state=='control' else evaluated.data
            original_geometry = _mesh_record(state_mesh)
            if normal_isolation:
                normal_sources[oid] = normal_checks.source_record(
                    source_object, mesh_state, depsgraph, _mesh_record)
            total_vertices += original_geometry['vertices']
            total_loops += original_geometry['loops']
            if total_vertices > 1000000 or total_loops > 4000000:
                raise RuntimeFailure('OBSERVATION_GEOMETRY_LIMIT', 'Combined frozen bodies exceed geometry budget')
            mesh = source_object.data.copy() if mesh_state=='control' else bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            created_data.append((bpy.data.meshes, mesh))
            proxy_geometry = _mesh_record(mesh)
            if original_geometry != proxy_geometry:
                raise RuntimeFailure('OBSERVATION_GEOMETRY', 'Frozen proxy differs from source evaluated geometry',
                                     object_id=oid, feature_id=source_object.get('hs_feature_id'))
            if normal_isolation:
                normal_proxies[oid] = normal_checks.mesh_record(mesh, _mesh_record)
                normal_checks.require_equal(normal_sources[oid]['source_mesh'], normal_proxies[oid],
                                            role='initial_frozen_proxy', object_id=oid)
            proxy = source_object.copy()
            created_objects.append(proxy)
            proxy.name = 'HS observation proxy ' + oid
            proxy.data = mesh
            proxy['hs_observation_proxy']=True
            for modifier in list(proxy.modifiers):
                proxy.modifiers.remove(modifier)
            proxy.hide_render, proxy.hide_viewport = True, False
            scene.collection.objects.link(proxy)
            proxies[oid] = proxy
            geometry[oid] = {'mesh_state':mesh_state,'source_mesh': original_geometry, 'frozen_proxy': proxy_geometry,
                             'geometry_unchanged': True, 'evaluation': 'SOURCE_CONTROL_MESH' if mesh_state=='control' else 'VIEWPORT_WITH_RENDER_MATCHING_MODIFIERS'}
            if normal_isolation:
                geometry[oid]['normal_isolation'] = {'policy': normal_checks.POLICY,
                    'source': normal_sources[oid], 'frozen_proxy': normal_proxies[oid],
                    'normal_sharp_smooth_unchanged': True,
                    'normal_origin': 'native_geometry_generated_preserving_source_smooth_faces_and_sharp_edges',
                    'normal_recomputed_transferred_or_authored_by_observer': False}
        assign(scene.render, 'engine', 'CYCLES')
        assign(scene.cycles, 'device', 'CPU')
        assign(scene.cycles, 'shading_system', False)
        assign(scene.cycles, 'samples', diagnostic['samples'])
        assign(scene.cycles, 'use_adaptive_sampling', False)
        assign(scene.cycles, 'use_denoising', diagnostic['denoising'])
        assign(scene.cycles, 'denoiser', 'OPENIMAGEDENOISE')
        assign(scene.cycles, 'seed', 0)
        assign(scene.cycles, 'use_animated_seed', False)
        assign(scene.render, 'threads_mode', 'FIXED')
        assign(scene.render, 'threads', p['cpu_threads'])
        assign(scene.render, 'use_compositing', False)
        assign(scene.render, 'use_sequencer', False)
        assign(scene.render, 'use_file_extension', True)
        assign(scene.render, 'film_transparent', False)
        assign(scene.render, 'use_border', False)
        assign(scene.render, 'use_crop_to_border', False)
        assign(scene.render, 'use_multiview', False)
        assign(scene.render, 'pixel_aspect_x', 1.0)
        assign(scene.render, 'pixel_aspect_y', 1.0)
        assign(scene.render, 'resolution_percentage', 100)
        assign(scene.render.image_settings, 'file_format', 'PNG')
        assign(scene.render.image_settings, 'color_mode', 'RGB')
        assign(scene.render.image_settings, 'color_depth', '8')
        assign(scene.render.image_settings, 'compression', 100)
        # Record settings modified repeatedly only once for final restoration.
        for attr in ('resolution_x', 'resolution_y', 'filepath'):
            changed.append((scene.render, attr, getattr(scene.render, attr)))
        if hasattr(scene.render, 'use_motion_blur'):
            assign(scene.render, 'use_motion_blur', False)
        assign(scene.view_settings, 'view_transform', 'AgX')
        assign(scene.view_settings, 'look', 'None')
        assign(scene.view_settings, 'exposure', 0.0)
        assign(scene.view_settings, 'gamma', 1.0)
        world = bpy.data.worlds.new('HS observation temporary world')
        created_data.append((bpy.data.worlds, world))
        world.use_nodes = True
        background = world.node_tree.nodes.get('Background')
        background.inputs['Color'].default_value = diagnostic['world_color']
        background.inputs['Strength'].default_value = diagnostic['world_strength']
        scene.world = world
        material = bpy.data.materials.new('HS observation temporary neutral white')
        created_data.append((bpy.data.materials, material))
        material.use_nodes = True
        material.diffuse_color = (.72, .72, .72, 1)
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = (.72, .72, .72, 1)
        bsdf.inputs['Roughness'].default_value = diagnostic['material_override']['roughness']
        bsdf.inputs['Metallic'].default_value = diagnostic['material_override']['metallic']
        for layer in scene.view_layers:
            assign(layer, 'use', layer == view_layer)
        assign(view_layer, 'material_override', material)
        if normal_isolation:
            normal_material = normal_checks.material_record(material, diagnostic)
            diagnostic['normal_isolation'] = {'policy': normal_checks.POLICY,
                'actual_material': normal_material,
                'source_shading_override': False, 'normal_repair_or_quantization': False}

        def collections(root):
            yield root
            for child in root.children:
                yield from collections(child)

        for collection in collections(scene.collection):
            assign(collection, 'hide_render', False)
            assign(collection, 'hide_viewport', False)
        for layer_collection in collections(view_layer.layer_collection):
            assign(layer_collection, 'exclude', False)
            assign(layer_collection, 'holdout', False)
            assign(layer_collection, 'indirect_only', False)

        camera_data = bpy.data.cameras.new('HS observation temporary camera')
        created_data.append((bpy.data.cameras, camera_data))
        camera = bpy.data.objects.new(camera_data.name, camera_data)
        created_objects.append(camera)
        scene.collection.objects.link(camera)
        camera_data.type = 'ORTHO'
        camera_data.sensor_fit = 'VERTICAL'
        camera_data.clip_start = .000001
        camera_data.clip_end = 1000
        scene.camera = camera
        lights = []
        for index in range(3):
            data = bpy.data.lights.new('HS observation temporary light ' + str(index), 'AREA')
            created_data.append((bpy.data.lights, data))
            light = bpy.data.objects.new(data.name, data)
            created_objects.append(light)
            scene.collection.objects.link(light)
            data.shape = diagnostic['light_shape']
            lights.append(light)

        for view in p['views']:
            deadline()
            restore_objects()
            for obj in objects:
                obj.hide_render = True
            transformations = []
            visible_objects = _view_objects(source_objects, view)
            for oid, source_object in visible_objects.items():
                obj = proxies[oid]
                selection_key = _selection_key(view, source_object)
                original = old_objects[source_object][0]
                delta_mm = view['explode_z_mm'].get(selection_key, 0.0)
                obj.location.z = old_objects[source_object][1].z + delta_mm * .001
                obj.hide_render = False
                obj.hide_viewport = False
                transformations.append({**_identity(source_object), 'display_proxy_name': obj.name,
                                        'selection_key': selection_key,
                                        'selection_kind': 'object_id' if 'visible_object_ids' in view else 'feature_id',
                                        'frozen_geometry': geometry[oid], 'translation_world_mm': [0.0, 0.0, delta_mm],
                                        'original_matrix_world': _matrix_values(original),
                                        'requested_translation_world_mm': [0.0, 0.0, delta_mm],
                                        'xy_unchanged': True, 'rotation_scale_unchanged': True})
            view_layer.update()
            for transformation in transformations:
                current = proxies[transformation['object_id']].matrix_world
                original_values = transformation['original_matrix_world']
                transformation['observation_matrix_world'] = _matrix_values(current)
                transformation['translation_world_mm'] = [
                    (float(current[i][3]) - original_values[i][3]) * 1000 for i in range(3)]
                if (any(current[i][3] != original_values[i][3] for i in (0, 1))
                        or any(current[i][j] != original_values[i][j] for i in range(3) for j in range(3))):
                    raise RuntimeFailure('OBSERVATION_TRANSFORM', 'Display explosion changed XY, rotation or scale')
            points = []
            for oid in visible_objects:
                proxy = proxies[oid]
                points.extend(proxy.matrix_world @ Vector(corner) for corner in proxy.bound_box)
            if not points or not all(math.isfinite(v) and abs(v) <= 100 for point in points for v in point):
                raise RuntimeFailure('OBSERVATION_BOUNDS', 'Visible bounds must be finite and within +/-100 metres')
            low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
            high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
            center = (low + high) / 2
            extent = max(high - low)
            if extent < 1e-7:
                raise RuntimeFailure('OBSERVATION_BOUNDS', 'Visible geometry has no usable extent')
            if 'camera' in view:
                cfg = view['camera']
                camera.location = Vector(cfg['position_mm']) * .001
                target = Vector(cfg['target_mm']) * .001
                up = Vector((0, 0, 1))
                right, camera_up = aim(camera, target, up)
                camera_data.ortho_scale = cfg['ortho_scale_mm'] * .001
            else:
                camera.location = center + Vector(DIRECTIONS[view['direction']]) * extent
                target = center
                up = Vector((0, 1, 0) if view['direction'] in ('top', 'bottom') else (0, 0, 1))
                right, camera_up = aim(camera, target, up)
                horizontal = [point.dot(right) for point in points]
                vertical = [point.dot(camera_up) for point in points]
                camera_data.ortho_scale = max(max(vertical) - min(vertical),
                                             (max(horizontal) - min(horizontal)) * view['height'] / view['width']) * 1.18
            # Camera-relative key/fill/rim also illuminate bottom and cover
            # undersides; fixed +Z lights would leave these views unreadable.
            outward = (camera.location - target).normalized()
            light_right, light_up = right, camera_up
            if reflective and p['reflection_rotation_degrees'] != 0:
                light_right, light_up = map(Vector, _rotated_camera_plane_basis(
                    right, camera_up, p['reflection_rotation_degrees']))
            lighting = []
            for light, (x, y, z, power) in zip(lights, diagnostic['lights_camera_basis']):
                light.location = center + (light_right * x + light_up * y + outward * z) * extent
                # Parallel rectangular emitters keep straight strips in the
                # same camera-relative frame for every view, including bottom.
                aim(light, light.location - outward * extent if reflective else center, light_up)
                light.data.energy = power * extent * extent
                light.data.size = extent * diagnostic['light_size_extent']
                if reflective:
                    light.data.size_y = extent * diagnostic['light_size_y_extent']
                lighting.append({'type': 'AREA', 'role': ('strip_left', 'strip_center', 'strip_right')[len(lighting)] if reflective else ('key', 'fill', 'rim')[len(lighting)],
                                 'position_mm': [float(v) * 1000 for v in light.location],
                                 'energy_w': float(light.data.energy), 'size_mm': float(light.data.size) * 1000,
                                 'color': [float(v) for v in light.data.color],
                                 'shape': light.data.shape,
                                 'size_y_mm': float(light.data.size_y) * 1000 if reflective else None,
                                 'rotation_euler': [float(v) for v in light.rotation_euler],
                                 'short_axis_world': [float(v) for v in light.rotation_euler.to_matrix().col[0]],
                                 'long_axis_world': [float(v) for v in light.rotation_euler.to_matrix().col[1]],
                                 'outward_axis_world': [float(v) for v in light.rotation_euler.to_matrix().col[2]]})
            anchor_evidence = None
            coverage_samples = None
            if 'reflection_anchor' in view:
                from . import reflection_coverage
                q = view['reflection_anchor']
                proxy = proxies[q['object_id']]
                anchor_camera = {'position_mm': [float(v)*1000 for v in camera.location],
                    'ortho_scale_mm': float(camera_data.ortho_scale)*1000,
                    'clip_start_m': float(camera_data.clip_start), 'clip_end_m': float(camera_data.clip_end)}
                context = reflection_coverage.prepare(proxy, view, q, source_before['sha256'],
                    normal_proxies[q['object_id']], anchor_camera, right, camera_up, outward)
                planned = reflection_anchor.plan(context['point'], context['normal'], outward,
                    right, camera_up, q, p['reflection_rotation_degrees'])
                for light, spec, record in zip(lights, planned['lights'], lighting):
                    light.location = Vector(spec['position_mm'])*.001
                    light.rotation_euler = Matrix((spec['short_axis_world'], spec['long_axis_world'],
                        spec['outward_axis_world'])).transposed().to_euler()
                    light.data.size = spec['size_mm']*.001
                    light.data.size_y = spec['size_y_mm']*.001
                    record.update(position_mm=[float(v)*1000 for v in light.location],
                        size_mm=float(light.data.size)*1000, size_y_mm=float(light.data.size_y)*1000,
                        rotation_euler=[float(v) for v in light.rotation_euler],
                        short_axis_world=[float(v) for v in light.rotation_euler.to_matrix().col[0]],
                        long_axis_world=[float(v) for v in light.rotation_euler.to_matrix().col[1]],
                        outward_axis_world=[float(v) for v in light.rotation_euler.to_matrix().col[2]])
                    record['energy_policy'] = 'unchanged legacy power; emitter area changed and recorded'
                    record['normalize'] = bool(light.data.normalize) if hasattr(light.data, 'normalize') else None
                    record['spread_radians'] = float(light.data.spread) if hasattr(light.data, 'spread') else None
                # Predict against actual native emitter values after conversion.
                planned['lights'] = copy.deepcopy(lighting)
                prediction, coverage_samples = reflection_coverage.predict(context, view, q, planned)
                anchor_evidence = {'binding': context['identity'], 'request': copy.deepcopy(q),
                    'anchor_pixel_normalized': context['anchor_pixel_normalized'],
                    'plan': planned, 'predicted': prediction,
                    'diagnostic_coverage': 'inconclusive', 'visual_acceptance': 'not_run'}
                deadline()
            scene.render.resolution_x, scene.render.resolution_y = view['width'], view['height']
            output = paths[view['name']]
            # Recheck immediately before writing; only this worker owns the job.
            _output_path(job_dir, output_name(view))
            scene.render.filepath = str(output)
            view_layer.update()
            camera_record = {'type': 'ORTHO', 'position_mm': [float(v) * 1000 for v in camera.location],
                             'target_mm': [float(v) * 1000 for v in target],
                             'up_axis': 'Y' if view.get('direction') in ('top', 'bottom') else 'Z',
                             'ortho_scale_mm': float(camera_data.ortho_scale) * 1000,
                             'sensor_fit': camera_data.sensor_fit, 'matrix_world': _matrix_values(camera.matrix_world),
                             'clip_start_m': camera_data.clip_start, 'clip_end_m': camera_data.clip_end}
            from .wire_overlay import prepare as prepare_wire
            wire=prepare_wire(scene,view_layer,[proxies[oid] for oid in visible_objects],camera,{**p['wire'],'mesh_state':mesh_state},view)
            view_normal_checks = []
            try:
                if normal_isolation:
                    view_normal_checks.append(attest_normals('before_render', view['name']))
                bpy.ops.render.render(write_still=True, layer=view_layer.name)
            finally:wire.cleanup()
            if normal_isolation:
                view_normal_checks.append(attest_normals('after_render', view['name']))
            for oid, source_object in visible_objects.items():
                if _mesh_record(proxies[oid].data) != geometry[oid]['frozen_proxy']:
                    raise RuntimeFailure('OBSERVATION_GEOMETRY', 'Display proxy geometry changed while rendering',
                                         object_id=oid, feature_id=source_object.get('hs_feature_id'))
            image = _png_record(output, view['width'], view['height'])
            outputs.append({**image, 'view': view['name'], 'configuration': copy.deepcopy(view),
                            'camera': camera_record, 'lighting': lighting,
                            **({'native_projection_inputs': {
                                'pixel_aspect_x': float(scene.render.pixel_aspect_x),
                                'pixel_aspect_y': float(scene.render.pixel_aspect_y),
                                'resolution_percentage': int(scene.render.resolution_percentage),
                                'shift_x': float(camera_data.shift_x), 'shift_y': float(camera_data.shift_y)}} if _source_guard is not None else {}),
                            'temporary_transformations': transformations,
                            'samples': diagnostic['samples'], 'diagnostic': copy.deepcopy(diagnostic), 'mesh_state':mesh_state,'wire':wire.evidence,'visual_acceptance': 'not_run'})
            if anchor_evidence is not None:
                anchor_evidence['observed'] = reflection_coverage.observe_png(output, view, coverage_samples)
                if anchor_evidence['observed']['status'] == 'fail':
                    anchor_evidence['diagnostic_coverage'] = 'fail'
                outputs[-1]['reflection_anchor'] = anchor_evidence
                outputs[-1]['diagnostic']['light_orientation'] = 'source_bound_reflection_plane'
                outputs[-1]['diagnostic']['lights_camera_basis'] = None
                outputs[-1]['diagnostic']['light_size_extent'] = None
                outputs[-1]['diagnostic']['light_size_y_extent'] = None
                outputs[-1]['diagnostic']['contract'] = 'HS_REFLECTION_ANCHOR_V1'
            if normal_isolation:
                outputs[-1]['normal_isolation_checks'] = view_normal_checks
            restore_objects()
            deadline()
    finally:
        restore_objects()
        scene.camera, scene.world = original_camera, original_world
        for owner, attr, previous in reversed(changed):
            setattr(owner, attr, previous)
        # These are only in-memory datablocks created by this invocation.
        for obj in reversed(created_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for datablocks, data in reversed(created_data):
            datablocks.remove(data)
        view_layer.update()

    source_after = verify_descriptor(p['source'])
    opened_after = descriptor(source_path)
    if source_before != source_after or opened_before != opened_after:
        raise RuntimeFailure('OBSERVATION_SOURCE_CHANGED', 'Source identity changed during observation')
    for obj, (matrix, location, hidden, viewport_hidden) in old_objects.items():
        if (_matrix_values(obj.matrix_world) != _matrix_values(matrix)
                or obj.hide_render != hidden or obj.hide_viewport != viewport_hidden):
            raise RuntimeFailure('OBSERVATION_RESTORE', 'In-memory object state was not restored', object_name=obj.name)
    if normal_isolation:
        graph = bpy.context.evaluated_depsgraph_get()
        for oid, obj in source_objects.items():
            normal_checks.require_equal(normal_sources[oid], normal_checks.source_record(obj, mesh_state, graph, _mesh_record),
                                        role='source_after_restoration', object_id=oid)
        diagnostic['normal_isolation']['source_after_restoration_unchanged'] = True
    return {'outcome': 'pass', 'operation': 'hardsurface.observe', 'schema_version': '1.0',
            'request_id': p['request_id'],
            'source': source_before, 'source_after': source_after, 'opened_source': opened_before,
            'source_sha256': source_before['sha256'], 'source_semantics': 'SAVED_DISK_V1',
            'scene': scene.name, 'scene_units': units, 'view_layer': view_layer.name,
            'previews': outputs, 'engine': 'CYCLES', 'device': 'CPU', 'samples': diagnostic['samples'],
            'display_transform':'AgX','denoising':'OPENIMAGEDENOISE' if diagnostic['denoising'] else 'disabled',
            'material_override': copy.deepcopy(diagnostic['material_override']),
            'diagnostic': copy.deepcopy(diagnostic),
            'cpu_threads': p['cpu_threads'], 'wall_seconds': p['wall_seconds'],
            'elapsed_seconds': time.monotonic() - started, 'saved_candidate_modified': False,
            'temporary_state_restored': True, 'blend_save_performed': False,
            'mesh_state':mesh_state,'wire_default_enabled':True,'display_method': 'frozen_'+mesh_state+'_mesh_proxies', 'source_transforms_modified': False,
            'acceptance': {'technical': 'pass', 'preservation': 'pass',
                           'visual': 'not_run', 'user_feedback': 'not_run'}}
