"""Opt-in, read-only attestation of native geometry-generated shading normals.

No normal is authored, transferred, normalized, quantized or repaired here.
Blender's actual corner-normal array and native face/edge state are required.
These helpers also accept explicit host mocks; they never import Blender.
"""
from __future__ import annotations

import hashlib
import math
import struct

from . import contract as c
from .io import RuntimeFailure
from .subdivision_source import GEOMETRY_PROPERTIES, DISPLAY_PROPERTIES, REQUIRED_PROPERTIES

POLICY = 'geometry_normals_v1'
NORMAL_LENGTH_TOLERANCE = 1e-5


def _fail(message, **details):
    raise RuntimeFailure('OBSERVATION_NORMAL_POLICY', message, policy=POLICY, **details)


def _required(owner, name):
    if not hasattr(owner, name):
        _fail('Actual native normal/state API is unavailable; no fallback or recomputation is permitted', property=name)
    return getattr(owner, name)


def _boolean(value, name):
    if type(value) is not bool:
        _fail('Native shading flags must be actual booleans', property=name)
    return value


def _bool_attribute(mesh, name, domain, expected):
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return {'present': False, 'domain': domain, 'count': len(expected)}
    if (attribute.domain != domain or attribute.data_type != 'BOOLEAN'
            or len(attribute.data) != len(expected)):
        _fail('Native shading attribute has an invalid type, domain or count', attribute=name)
    values = [_boolean(item.value, name) for item in attribute.data]
    if values != expected:
        _fail('Native shading attribute disagrees with actual mesh flags', attribute=name)
    return {'present': True, 'domain': attribute.domain, 'data_type': attribute.data_type,
            'count': len(values)}


def mesh_record(mesh, geometry_record):
    """Hash ordered normals/flags and their actual mesh-domain correspondence.

    Values are packed as their exact Python float64 representations, including
    signed zero. Native float32 values widen exactly; no tolerance is used for
    identity comparisons. The unit-length tolerance only rejects invalid input.
    """
    geometry = geometry_record(mesh)
    custom = _boolean(_required(mesh, 'has_custom_normals'), 'has_custom_normals')
    attributes = _required(mesh, 'attributes')
    if custom or attributes.get('custom_normal') is not None:
        _fail('Custom normals or the custom_normal attribute are not supported')
    normals = _required(mesh, 'corner_normals')
    edges, polygons, loops = mesh.edges, mesh.polygons, mesh.loops
    if len(edges) > 4000000:
        _fail('Native edge domain exceeds the observation budget')
    if len(normals) != len(loops) or not len(loops):
        _fail('Actual corner normals must cover every mesh loop', normals=len(normals), loops=len(loops))
    domain = _required(mesh, 'normals_domain')
    if domain not in ('POINT', 'FACE', 'CORNER'):
        _fail('Native normals domain is unsupported', normals_domain=domain)
    digest = hashlib.sha256(b'HS_OBSERVATION_NATIVE_NORMALS_V1\0')
    digest.update(struct.pack('>QQQQ', len(mesh.vertices), len(edges), len(polygons), len(loops)))
    digest.update(domain.encode('ascii') + b'\0')
    sharp, smooth = [], []
    for edge in edges:
        indices = tuple(edge.vertices)
        if (len(indices) != 2 or any(type(v) is not int or not 0 <= v < len(mesh.vertices) for v in indices)
                or indices[0] == indices[1]):
            _fail('Invalid native edge domain')
        flag = _boolean(_required(edge, 'use_edge_sharp'), 'use_edge_sharp')
        sharp.append(flag)
        digest.update(struct.pack('>QQ?', *indices, flag))
    cursor = 0
    for polygon in polygons:
        indices = tuple(polygon.vertices)
        start = _required(polygon, 'loop_start')
        count = _required(polygon, 'loop_total')
        if type(start) is not int or type(count) is not int or start != cursor or count != len(indices) or count < 3:
            _fail('Polygon loops must exactly partition the native corner domain')
        flag = _boolean(_required(polygon, 'use_smooth'), 'use_smooth')
        smooth.append(flag)
        digest.update(struct.pack('>QQ?', start, count, flag))
        for offset, vertex_index in enumerate(indices):
            if cursor + offset >= len(loops):
                _fail('Polygon loop range exceeds native loop count')
            loop = loops[cursor + offset]
            edge_index = _required(loop, 'edge_index')
            if (_required(loop, 'vertex_index') != vertex_index or type(edge_index) is not int
                    or not 0 <= edge_index < len(edges)):
                _fail('Loop vertices/edges disagree with the native mesh domains')
            if set(edges[edge_index].vertices) != {vertex_index, indices[(offset + 1) % count]}:
                _fail('Loop edge does not join adjacent face corners')
            digest.update(struct.pack('>QQ', vertex_index, edge_index))
        cursor += count
    if cursor != len(loops):
        _fail('Polygon loops do not cover the complete native corner domain')
    normals_read = 0
    for item in normals:
        if normals_read >= len(loops):
            _fail('Native corner-normal iteration exceeds the loop count', normals_read=normals_read + 1, loops=len(loops))
        try:
            vector = tuple(float(value) for value in _required(item, 'vector'))
        except (ValueError, TypeError, OverflowError) as error:
            _fail('Corner normal cannot be read as a native finite vector', reason=str(error))
        if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
            _fail('Corner normals must be finite three-component vectors')
        length = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(length) or abs(length - 1.0) > NORMAL_LENGTH_TOLERANCE:
            _fail('Corner normal is not unit length; no normalization is permitted', observed_length=length)
        digest.update(struct.pack('>ddd', *vector))
        normals_read += 1
    if normals_read != len(loops):
        _fail('Native corner-normal iteration did not cover every mesh loop',
              reported_normals=len(normals), normals_read=normals_read, loops=len(loops))
    attribute_state = {
        'sharp_edge': _bool_attribute(mesh, 'sharp_edge', 'EDGE', sharp),
        'sharp_face': _bool_attribute(mesh, 'sharp_face', 'FACE', [not value for value in smooth]),
    }
    digest.update(c.canonical_bytes(attribute_state))
    shading = {'hash_semantics': 'HS_OBSERVATION_NATIVE_NORMALS_V1',
               'native_normals_sharp_smooth_sha256': digest.hexdigest(),
               'identity_comparison': 'exact_float64_bytes_no_quantization',
               'normal_length_validation_tolerance': NORMAL_LENGTH_TOLERANCE,
               'corner_normals': normals_read, 'normals_domain': domain,
               'edges': len(edges), 'sharp_edges': sum(sharp),
               'smooth_faces': sum(smooth), 'flat_faces': len(smooth) - sum(smooth),
               'native_shading_attributes': attribute_state, 'has_custom_normals': False}
    return {**geometry, 'native_shading': shading}


def modifier_record(obj):
    """Capture the same complete native SUBSURF profile as source qualification.

    Intentional observation visibility changes are excluded; no geometry or
    modifier setting is excluded. Unknown writable RNA settings fail closed.
    """
    if len(obj.modifiers) != 1 or obj.modifiers[0].type != 'SUBSURF':
        _fail('Geometry-normal isolation requires exactly one SUBSURF; normal-transfer and other modifiers are refused',
              modifier_types=[mod.type for mod in obj.modifiers])
    mod = obj.modifiers[0]
    properties = getattr(getattr(mod, 'bl_rna', None), 'properties', None)
    if properties is None:
        names = [key for key in (*GEOMETRY_PROPERTIES, *DISPLAY_PROPERTIES) if hasattr(mod, key)]
        unknown = set(vars(mod)) - set(names) - {'name', 'type', 'bl_rna'}
    else:
        names = [p.identifier for p in properties if p.identifier not in ('rna_type', 'name', 'type') and not p.is_readonly]
        unknown = set(names) - set(GEOMETRY_PROPERTIES) - set(DISPLAY_PROPERTIES)
    if unknown or not REQUIRED_PROPERTIES <= set(names):
        _fail('Complete classified writable SUBSURF settings are required',
              unknown=sorted(unknown), missing=sorted(REQUIRED_PROPERTIES - set(names)))
    actual = {'name': mod.name, 'type': mod.type}
    for name in names:
        value = getattr(mod, name)
        if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
            _fail('SUBSURF settings must be finite scalars', property=name)
        actual[name] = value
    if (actual['subdivision_type'] != 'CATMULL_CLARK' or actual['use_custom_normals'] is not False
            or actual['show_viewport'] is not True or actual['show_render'] is not True
            or actual['levels'] != actual['render_levels'] or actual.get('use_adaptive_subdivision', False)
            or getattr(getattr(obj, 'cycles', None), 'use_adaptive_subdivision', False)):
        _fail('An enabled fixed-level Catmull-Clark SUBSURF with matching viewport/render levels and no custom normals is required')
    return {'settings': actual, 'settings_sha256': c.fingerprint(actual)}


def source_record(obj, mesh_state, depsgraph, geometry_record):
    if mesh_state not in ('control', 'evaluated'):
        _fail('Explicit control or evaluated source state required')
    if (obj.type != 'MESH' or obj.mode != 'OBJECT' or obj.parent or obj.constraints
            or obj.animation_data or obj.library or obj.override_library
            or _required(obj.data, 'library') or _required(obj.data, 'override_library')
            or _required(obj.data, 'animation_data') or obj.data.shape_keys):
        _fail('Geometry-normal isolation requires a local static unparented mesh with no animation or shape keys')
    modifier = modifier_record(obj)
    control = mesh_record(obj.data, geometry_record)
    # Never accept caller-supplied substitute arrays for a declared mesh state.
    state = control if mesh_state == 'control' else mesh_record(obj.evaluated_get(depsgraph).data, geometry_record)
    return {'policy': POLICY, 'mesh_state': mesh_state, 'source_modifier': modifier,
            'source_control_mesh': control, 'source_mesh': state}


def require_equal(expected, actual, *, role, object_id=None):
    if c.fingerprint(expected) != c.fingerprint(actual):
        _fail('Exact native geometry/normal/settings identity changed; no normal repair or fallback is permitted',
              role=role, object_id=object_id, expected=expected, observed=actual)


def _socket_value(socket):
    value = _required(socket, 'default_value')
    if type(value) in (float, int, bool, str):
        values = value
    else:
        try:
            values = [float(item) for item in value]
        except (TypeError, ValueError, OverflowError):
            _fail('Diagnostic socket has an unsupported default value', socket=socket.name)
    try:
        c.canonical_bytes(values)
    except (ValueError, TypeError, OverflowError, c.ContractError):
        _fail('Diagnostic socket value is nonfinite or unsupported', socket=socket.name)
    return values


def material_record(material, diagnostic):
    """Inspect the existing fixed two-node override; never build a new shader."""
    if not material.use_nodes or material.node_tree is None:
        _fail('Diagnostic material must use the fixed node tree')
    tree = material.node_tree
    nodes = list(tree.nodes)
    if sorted(node.bl_idname for node in nodes) != ['ShaderNodeBsdfPrincipled', 'ShaderNodeOutputMaterial']:
        _fail('Diagnostic material may contain only Principled BSDF and Material Output; textures, bump, normal maps, groups and displacement nodes are refused',
              node_types=[node.bl_idname for node in nodes])
    bsdf = next(node for node in nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled')
    output = next(node for node in nodes if node.bl_idname == 'ShaderNodeOutputMaterial')
    if any(node.mute for node in nodes) or not output.is_active_output:
        _fail('Diagnostic shader/output nodes must be active and unmuted')
    node_enums = {}
    for name in ('distribution', 'subsurface_method'):
        value = _required(bsdf, name)
        if type(value) is not str or not value:
            _fail('Actual Principled non-socket shading enums are required', property=name)
        node_enums[name] = value
    links = list(tree.links)
    if (len(links) != 1 or links[0].from_node != bsdf or links[0].to_node != output
            or links[0].from_socket != bsdf.outputs['BSDF'] or links[0].to_socket != output.inputs['Surface']
            or not links[0].is_valid):
        _fail('Diagnostic material requires exactly one valid BSDF-to-Surface link')
    inputs = {}
    for socket in bsdf.inputs:
        if socket.is_linked:
            _fail('All diagnostic BSDF inputs, including Normal and Coat Normal, must be unlinked', socket=socket.name)
        inputs[socket.name] = _socket_value(socket)
    normal_inputs = {name: value for name, value in inputs.items() if 'Normal' in name}
    if 'Normal' not in normal_inputs or any(value != [0.0, 0.0, 0.0] for value in normal_inputs.values()):
        _fail('Diagnostic normal socket defaults must be the native zero vector')
    for socket in output.inputs:
        if socket.name != 'Surface' and socket.is_linked:
            _fail('Diagnostic Volume and Displacement inputs must be unlinked', socket=socket.name)
    # Blender stores these prescribed material literals as float32. This is
    # solely expected-value construction, never normal/geometry quantization.
    f32 = lambda value: struct.unpack('>f', struct.pack('>f', value))[0]
    expected = diagnostic['material_override']
    for name, key in (('Base Color', 'base_color'), ('Roughness', 'roughness'), ('Metallic', 'metallic')):
        value = expected[key]
        fixed = [f32(item) for item in value] if isinstance(value, list) else f32(value)
        if inputs.get(name) != fixed:
            _fail('Diagnostic material differs from the existing fixed preset', socket=name, expected=fixed, observed=inputs.get(name))
    actual = {'node_types': [node.bl_idname for node in nodes],
              'bsdf_node_enums': node_enums,
              'bsdf_input_defaults': inputs, 'normal_inputs_unlinked': sorted(normal_inputs),
              'output_target': output.target,
              'output_inputs_linked': {socket.name: bool(socket.is_linked) for socket in output.inputs},
              'links': [{'from': 'Principled BSDF.BSDF', 'to': 'Material Output.Surface', 'valid': True}],
              'texture_nodes': 0, 'bump_normal_map_displacement_nodes': 0}
    return {'actual_node_checks': actual, 'actual_node_checks_sha256': c.fingerprint(actual)}


def attest(source_objects, proxies, expected_sources, expected_proxies, *, mesh_state,
           depsgraph, geometry_record, view_layer, material, diagnostic, expected_material,
           phase, view_name=None):
    """Verify each source and the one frozen proxy again at a render boundary."""
    for oid, obj in source_objects.items():
        actual = source_record(obj, mesh_state, depsgraph, geometry_record)
        require_equal(expected_sources[oid], actual, role='source_' + phase, object_id=oid)
        proxy = proxies[oid]
        if proxy.modifiers:
            _fail('Frozen normal-isolation proxy acquired modifiers', object_id=oid)
        actual = mesh_record(proxy.data, geometry_record)
        require_equal(expected_proxies[oid], actual, role='frozen_proxy_' + phase, object_id=oid)
    if view_layer.material_override != material:
        _fail('Fixed diagnostic material override was replaced')
    actual_material = material_record(material, diagnostic)
    require_equal(expected_material, actual_material, role='diagnostic_material_' + phase)
    return {'phase': phase, 'view': view_name, 'source_and_proxy_identity': 'pass',
            'actual_material_identity': actual_material['actual_node_checks_sha256']}
