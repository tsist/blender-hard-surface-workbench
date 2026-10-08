# SPDX-License-Identifier: GPL-3.0-or-later
"""Predeclared coordinate scopes for the sparse single-hole parameter family.

This is a pure planning boundary, not a native edit or user authorization. It
preserves the complete semantic topology and refuses technical-policy changes.
"""
from copy import deepcopy

from .io import RuntimeFailure
from .structure_kernel import fingerprint
from .sparse_panel_geometry import (SCHEDULE_REVISION, validate_parameter_domain,
                                    validate_sparse_authored_identity)
from .subd_panel_identity import semantic_connectivity


def _fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def parameter_binding(parameters):
    return {'constructor':'quad.panel/sparse_control_cage',
            'parameters':deepcopy(parameters),'design_sha256':fingerprint(parameters),
            'coordinate_space':'object_local_mm'}


def plan_parameter_edit(before, after_parameters, *, datum=None):
    """Bind supported dimensions and exact author dependencies before mutation."""
    validate_sparse_authored_identity(before)
    authored=before['authored_structure']
    old=authored['parameter_binding']['parameters'];new=deepcopy(after_parameters)
    if not isinstance(new,dict):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Complete new parameters required')
    validate_parameter_domain(new)
    a,b=deepcopy(old),deepcopy(new)
    a.pop('edit_datum',None);b.pop('edit_datum',None)
    try:
        old_hole,new_hole=a['holes'][0],b['holes'][0]
        hole_changed=old_hole['center']!=new_hole['center'] or old_hole['radius']!=new_hole['radius']
        z_changed=a['z_min']!=b['z_min'] or a['z_max']!=b['z_max']
        bevel_changed=a['edge_bevel']!=b['edge_bevel']
        new_hole['center']=deepcopy(old_hole['center']);new_hole['radius']=old_hole['radius']
        b['z_min']=a['z_min'];b['z_max']=a['z_max'];b['edge_bevel']=a['edge_bevel']
    except (KeyError,IndexError,TypeError) as exc:
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Edit must preserve one complete sparse panel contract',reason=str(exc))
    if a!=b:
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Only hole center/radius, explicit-datum thickness and outer roundover are parameter edits; topology and technical policy need their own transaction')
    if datum is not None and datum not in ('fixed_bottom','fixed_midplane'):
        _fail('AUTHORED_EDIT_DATUM_REQUIRED','Unsupported thickness datum')
    if z_changed:
        if datum is None:_fail('AUTHORED_EDIT_DATUM_REQUIRED','Explicit thickness datum required')
        if datum=='fixed_bottom' and old['z_min']!=new['z_min']:
            _fail('AUTHORED_EDIT_DATUM_MISMATCH','fixed_bottom requires unchanged z_min')
        if datum=='fixed_midplane' and old['z_min']+old['z_max']!=new['z_min']+new['z_max']:
            _fail('AUTHORED_EDIT_DATUM_MISMATCH','fixed_midplane requires exactly unchanged midpoint')
    active=[]
    for axis in (0,1):
        if old['holes'][0]['center'][axis]!=new['holes'][0]['center'][axis]:active.append('holes[0].center[%d]'%axis)
    if old['holes'][0]['radius']!=new['holes'][0]['radius']:active.append('holes[0].radius')
    if z_changed:active.append('z_range.'+datum)
    if bevel_changed:active.append('edge_bevel')
    allowed={}
    for path in active:
        dep=authored['edit_dependencies'][path]
        for sid in dep['vertex_ids']:
            allowed[sid]=sorted(set(allowed.get(sid,[]))|set(dep['allowed_axes']))
    chosen=set(allowed);connectivity=semantic_connectivity(before,authored)
    return {'schema_version':'1.0','schedule_revision':SCHEDULE_REVISION,
            'before_authorship_sha256':authored['authorship_sha256'],
            'after_parameter_binding':parameter_binding(new),'datum':datum,
            'hole_changed':hole_changed,'z_range_changed':z_changed,
            'allowed_vertex_axes':allowed,
            'affected_face_ids':sorted(s for s,ids in connectivity.items() if chosen.intersection(ids)),
            'semantic_control_loop_roles':[row['role'] for row in authored['semantic_control_loops'] if chosen.intersection(row['vertex_ids'])],
            'topology_policy':'preserve','downstream_selections':'invalidate_and_resolve_again',
            'qualification':'host_scope_only; no_reference_or_native_approval'}
