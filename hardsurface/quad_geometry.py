"""Pure parameter-domain construction entry point for structured quad bodies.

Coordinates are millimetres. This module cannot open/save Blender files and
does not accept executable callbacks or arbitrary caller-provided mesh data.
"""
from __future__ import annotations
import hashlib
import json
from .io import RuntimeFailure


def build(parameters, feature_id):
    op = parameters['op']
    p = parameters
    if op == 'quad.fastener':
        from .quad_fastener import build_quad_fastener
        mesh = build_quad_fastener(
            feature_id=feature_id, center_mm=p['center'],
            shaft_radius_mm=p['shaft_radius'], head_radius_mm=p['head_radius'],
            head_height_mm=p['head_height'], shaft_length_mm=p['shaft_length'],
            shoulder_z_mm=p['shoulder_z'], direction=p['direction'],
            socket_flat_width_mm=p['socket_flat_width'] or None,
            socket_depth_mm=p['socket_depth'],
            chord_error_mm=p['chord_tolerance'],
            max_shaft_segment_mm=p['target_edge_length'],
            max_circle_segments=p['max_segments'])
    elif op == 'quad.panel':
        from .quad_panel import build_quad_panel
        mesh = build_quad_panel(p, feature_id=feature_id)
    elif op == 'quad.shell':
        from .quad_shell import build_quad_shell
        mesh = build_quad_shell(p, feature_id=feature_id)
    else:
        raise RuntimeFailure('QUAD_OPERATION_UNSUPPORTED', 'Unregistered structured operation')
    vertices = mesh['vertices_mm']
    faces = mesh['faces']
    if len(vertices) > 200000 or len(faces) > 200000:
        raise RuntimeFailure('QUAD_ALLOCATION_BUDGET', 'Structured body exceeds local mesh allocation limit')
    if any(len(face) != 4 for face in faces):
        raise RuntimeFailure('QUAD_CONSTRUCTION_FAILED', 'Structured constructor emitted a non-quad polygon')
    if len(mesh['face_provenance']) != len(faces):
        raise RuntimeFailure('QUAD_PROVENANCE_FAILED', 'Every constructed polygon requires provenance')
    mesh['construction_sha256'] = hashlib.sha256(json.dumps(
        {'vertices_mm':vertices, 'faces':faces, 'face_provenance':mesh['face_provenance'],
         'corner_normals':mesh.get('corner_normals')},
        sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return mesh


def planning_evidence(parameters, feature_id):
    mesh = build(parameters, feature_id)
    return {'method':'deterministic_structured_patch_construction',
            'vertices':len(mesh['vertices_mm']),
            'loops':sum(map(len,mesh['faces'])), 'faces':len(mesh['faces']),
            'construction_sha256':mesh['construction_sha256'],
            'metadata':mesh.get('metadata',{}),
            'output_upper_bound':'exact_for_this_constructor_before_later_operations'}
