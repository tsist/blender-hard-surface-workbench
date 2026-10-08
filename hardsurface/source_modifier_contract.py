"""Shared HOST-only fixed Subdivision modifier schema; no Blender import."""
import copy
from . import contract as c

_SETTINGS = c.obj({
    'boundary_smooth': c.optional_default(c.enum('ALL', 'PRESERVE_CORNERS'), 'ALL'),
    'uv_smooth': c.optional_default(c.enum('NONE', 'PRESERVE_CORNERS', 'PRESERVE_CORNERS_AND_JUNCTIONS', 'PRESERVE_CORNERS_JUNCTIONS_AND_CONCAVE', 'PRESERVE_BOUNDARIES', 'SMOOTH_ALL'), 'PRESERVE_BOUNDARIES'),
    'quality': c.optional_default(c.integer(minimum=1, maximum=6), 3),
    'use_limit_surface': c.optional_default(c.BOOL, False),
}, [])
_AUTHORED_SETTINGS = c.obj({
    'name': c.string(minLength=1,maxLength=128), 'subdivision_type': c.const('CATMULL_CLARK'),
    'levels': c.integer(minimum=0,maximum=3), 'render_levels': c.integer(minimum=0,maximum=3),
    'quality': c.integer(minimum=1,maximum=6),
    'uv_smooth': _SETTINGS['properties']['uv_smooth'],
    'boundary_smooth': _SETTINGS['properties']['boundary_smooth'],
    'use_creases': c.BOOL, 'use_limit_surface': c.BOOL,
})
_SOURCE_MODIFIER_PROPERTIES = {
    **copy.deepcopy(_AUTHORED_SETTINGS['properties']), 'type': c.const('SUBSURF'),
    'use_custom_normals': c.BOOL, 'show_viewport': c.BOOL, 'show_render': c.BOOL,
    'show_in_editmode': c.BOOL, 'show_on_cage': c.BOOL, 'show_only_control_edges': c.BOOL,
    'use_apply_on_spline': c.BOOL, 'use_pin_to_last': c.BOOL,
    'use_adaptive_subdivision': c.BOOL, 'adaptive_space': c.enum('PIXEL', 'OBJECT'),
    'adaptive_pixel_size': c.number(exclusiveMinimum=0),
    'adaptive_object_edge_length': c.number(exclusiveMinimum=0),
}
# The four version-dependent adaptive properties are optional in the schema,
# but runtime demands exact coverage if the actual RNA exposes them.
_SOURCE_MODIFIER = c.obj(_SOURCE_MODIFIER_PROPERTIES,
    [key for key in _SOURCE_MODIFIER_PROPERTIES if key not in ('use_adaptive_subdivision', 'adaptive_space', 'adaptive_pixel_size', 'adaptive_object_edge_length')])
