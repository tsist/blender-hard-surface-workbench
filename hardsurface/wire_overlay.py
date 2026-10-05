"""Actual mesh-edge Freestyle overlays on isolated observation proxies.

Importing this module never imports Blender. The caller freezes either the
control mesh or the evaluated modifier result, marks that new object with
``hs_observation_proxy = True``, and passes only the proxies visible in this
view. No triangulation, bevel curves, mesh edges, or source geometry are added.
"""
from __future__ import annotations

from array import array
import math

from .io import RuntimeFailure


MAX_EDGES = 100000
DEFAULT_LINE_WIDTH_PX = 1.25
PROXY_MARKER = 'hs_observation_proxy'


def validate_config(config=None):
    """Bound overlay work without Blender or optional dependencies."""
    config = {} if config is None else config
    if not isinstance(config, dict):
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'Wire overlay configuration must be an object')
    allowed = {'enabled', 'mesh_state', 'line_width_px', 'max_edges'}
    if set(config) - allowed:
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'Unknown wire overlay configuration field',
                             fields=sorted(set(config) - allowed))
    normalized = {'enabled': True, 'mesh_state': 'evaluated',
                  'line_width_px': DEFAULT_LINE_WIDTH_PX, 'max_edges': MAX_EDGES,
                  **config}
    if type(normalized['enabled']) is not bool:
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'enabled must be a boolean')
    if normalized['mesh_state'] not in ('control', 'evaluated'):
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'mesh_state must be control or evaluated')
    width = normalized['line_width_px']
    if (type(width) not in (int, float) or not math.isfinite(width)
            or not 1.0 <= width <= 2.0):
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'Wire width must be finite and between 1 and 2 pixels')
    budget = normalized['max_edges']
    if type(budget) is not int or not 1 <= budget <= MAX_EDGES:
        raise RuntimeFailure('OBSERVATION_WIRE_CONFIG', 'Wire edge budget must be between 1 and 100000')
    return normalized


class OverlayHandle:
    """Temporary renderer state and JSON-serializable observation evidence."""

    def __init__(self, evidence):
        self.evidence = evidence
        self._undo = []
        self._cleaned = False

    def _assign(self, owner, attr, value):
        old = getattr(owner, attr)
        self._undo.append(lambda: setattr(owner, attr, old))
        setattr(owner, attr, value)

    def cleanup(self):
        """Restore all temporary state; repeat calls are harmless."""
        if self._cleaned:
            return
        failures = []
        while self._undo:
            undo = self._undo.pop()
            try:
                undo()
            except Exception as error:
                failures.append(str(error))
        self._cleaned = True
        self.evidence['temporary_state_restored'] = not failures
        if failures:
            raise RuntimeFailure('OBSERVATION_WIRE_RESTORE', 'Wire overlay state restoration failed',
                                 failures=failures)


def _proxy_items(proxies):
    if isinstance(proxies, dict):
        return list(proxies.items())
    return [(obj.get('hs_feature_id', obj.name), obj) for obj in proxies]


def _check_proxies(items, budget):
    seen = set()
    counts = {}
    total = 0
    for fid, obj in items:
        if (getattr(obj, 'type', None) != 'MESH' or not obj.get(PROXY_MARKER, False)
                or obj.library or obj.override_library or obj.data.library
                or obj.data.users != 1 or obj.modifiers or obj.hide_render):
            raise RuntimeFailure('OBSERVATION_WIRE_PROXY',
                                 'Wire overlay requires visible, marked, local, single-user frozen mesh proxies',
                                 feature_id=str(fid))
        if id(obj) in seen:
            raise RuntimeFailure('OBSERVATION_WIRE_PROXY', 'Duplicate wire overlay proxy', feature_id=str(fid))
        seen.add(id(obj))
        count = len(obj.data.edges)
        total += count
        if total > budget:
            raise RuntimeFailure('OBSERVATION_WIRE_EDGE_LIMIT',
                                 'Actual mesh edges exceed the per-view wire overlay budget',
                                 actual_edges=total, max_edges=budget)
        counts[str(fid)] = counts.get(str(fid),0)+count
    return total, counts


def _mark_mesh(mesh, handle):
    """Mark only the EDGE-domain entries corresponding to mesh.edges."""
    attr = mesh.attributes.get('freestyle_edge')
    if attr is not None:
        if attr.domain != 'EDGE' or attr.data_type != 'BOOLEAN':
            raise RuntimeFailure('OBSERVATION_WIRE_ATTRIBUTE', 'Existing Freestyle edge attribute has an invalid type')
        old = array('b', [False]) * len(mesh.edges)
        attr.data.foreach_get('value', old)
        handle._undo.append(lambda: attr.data.foreach_set('value', old))
    else:
        attr = mesh.attributes.new('freestyle_edge', 'BOOLEAN', 'EDGE')
        handle._undo.append(lambda: mesh.attributes.remove(attr))
    attr.data.foreach_set('value', array('b', [True]) * len(mesh.edges))
    mesh.update()


def prepare(scene, view_layer, proxies, camera, config=None, view=None):
    """Prepare a visible-real-edge-only overlay after camera placement.

    Return an :class:`OverlayHandle`; call ``handle.cleanup()`` in ``finally``
    immediately after the render. Include ``handle.evidence`` in its report.
    The view mapping is optional and used only for its name. Disabled config
    temporarily suppresses pre-existing Freestyle but never imports bpy.
    """
    cfg = validate_config(config)
    evidence = {
        'enabled': cfg['enabled'], 'mesh_state': cfg['mesh_state'],
        'method': 'FREESTYLE_MARKED_REAL_MESH_EDGES' if cfg['enabled'] else 'OFF',
        'topology_semantics': 'CONTROL_MESH_EDGES' if cfg['mesh_state'] == 'control' else 'EVALUATED_MESH_EDGES',
        'line_width_px': float(cfg['line_width_px']) if cfg['enabled'] else 0.0,
        'line_width_mode': 'ABSOLUTE_PIXELS', 'max_edges': cfg['max_edges'],
        'actual_edges': 0, 'actual_edges_by_feature': {},
        'coverage': 'VISIBLE_FACE_CONNECTED_TRUE_EDGES' if cfg['enabled'] else 'OFF',
        'edge_count_semantics': 'ALL_FACE_CONNECTED_PROXY_EDGES_BEFORE_VISIBILITY',
        'polygons_count': 0, 'polygons_count_by_feature': {},
        'added_triangulation_edges': 0, 'added_geometry_edges': 0,
        'requested_visibility': 'VISIBLE', 'source_meshes_modified': False,
        'visibility_method': 'FREESTYLE_ESTIMATED_VIEW_MAP',
        'geometric_visibility_completeness_proven': False,
        'visibility_limitations': ['Exact-axis or coplanar views may omit marked edges or expose back edges; compare oblique views and indexed mesh export before inferring topology defects'],
        'temporary_state_restored': not cfg['enabled'],
        'view': (view or {}).get('name'),
    }
    handle = OverlayHandle(evidence)
    if not cfg['enabled']:
        if scene is not None:
            handle._assign(scene.render, 'use_freestyle', False)
            evidence['temporary_state_restored'] = False
        return handle
    items = _proxy_items(proxies)
    total, counts = _check_proxies(items, cfg['max_edges'])
    evidence.update(actual_edges=total, actual_edges_by_feature=counts)
    # Freestyle silently drops edges with no incident face. A seemingly
    # complete wire image would hide that defect, so refuse this unsupported
    # input explicitly. The topology report remains available to diagnose it.
    for fid, proxy in items:
        loose = sum(bool(getattr(edge, 'is_loose', False)) for edge in proxy.data.edges)
        if loose:
            raise RuntimeFailure('OBSERVATION_WIRE_LOOSE_EDGES_UNSUPPORTED',
                                 'Freestyle cannot render loose mesh edges; refusing an incomplete wire view',
                                 feature_id=str(fid), loose_edges=loose)
    evidence['loose_edge_policy'] = 'REFUSE_UNSUPPORTED_LOOSE_EDGES'
    face_counts = {}
    for fid,proxy in items:face_counts[str(fid)]=face_counts.get(str(fid),0)+len(proxy.data.polygons)
    evidence['object_instances']=[{'object_id':proxy.get('hs_object_id'),'feature_id':proxy.get('hs_feature_id'),'edges':len(proxy.data.edges),'polygons':len(proxy.data.polygons)} for fid,proxy in items]
    evidence.update(polygons_count=sum(face_counts.values()), polygons_count_by_feature=face_counts)
    if camera is None or camera.data.type != 'ORTHO':
        raise RuntimeFailure('OBSERVATION_WIRE_CAMERA', 'Wire observation requires an orthographic camera')
    import bpy
    if not bpy.app.build_options.freestyle:
        raise RuntimeFailure('OBSERVATION_WIRE_UNAVAILABLE', 'This Blender build does not include Freestyle')
    settings = view_layer.freestyle_settings
    try:
        handle._assign(scene.render, 'use_freestyle', True)
        handle._assign(scene.render, 'line_thickness_mode', 'ABSOLUTE')
        handle._assign(scene.render, 'line_thickness', 1.0)
        handle._assign(settings, 'mode', 'EDITOR')
        handle._assign(settings, 'as_render_pass', False)
        handle._assign(settings, 'use_view_map_cache', False)
        handle._assign(settings, 'use_culling', True)
        handle._assign(settings, 'use_suggestive_contours', False)
        handle._assign(settings, 'use_ridges_and_valleys', False)
        handle._assign(settings, 'use_material_boundaries', False)
        handle._assign(settings, 'use_smoothness', False)
        for existing in settings.linesets:
            handle._assign(existing, 'show_render', False)
        # new/remove changes the active editor selection even though all
        # existing line sets survive. Restore it after temporary removal.
        handle._assign(settings.linesets, 'active_index', settings.linesets.active_index)
        collection = bpy.data.collections.new('HS observation real-edge proxies')
        handle._undo.append(lambda: bpy.data.collections.remove(collection))
        scene.collection.children.link(collection)
        for _, proxy in items:
            collection.objects.link(proxy)
            _mark_mesh(proxy.data, handle)
        lineset = settings.linesets.new('HS observation real mesh edges')
        style = lineset.linestyle
        # Register style before lineset so reverse cleanup removes its user first.
        handle._undo.append(lambda: bpy.data.linestyles.remove(style))
        handle._undo.append(lambda: settings.linesets.remove(lineset))
        lineset.show_render = True
        lineset.select_by_visibility = True
        lineset.visibility = 'VISIBLE'
        lineset.select_by_edge_types = True
        lineset.edge_type_negation = 'INCLUSIVE'
        lineset.edge_type_combination = 'OR'
        lineset.select_by_collection = True
        lineset.collection = collection
        # Blender 5.2 does not increase collection.users for this pointer, but
        # linesets.remove still decrements it unless the pointer is detached.
        handle._undo.append(lambda: setattr(lineset, 'collection', None))
        lineset.collection_negation = 'INCLUSIVE'
        lineset.select_by_image_border = False
        lineset.select_by_face_marks = False
        for name in ('silhouette', 'border', 'crease', 'ridge_valley', 'suggestive_contour',
                     'material_boundary', 'contour', 'external_contour', 'edge_mark'):
            setattr(lineset, 'select_' + name, name == 'edge_mark')
            setattr(lineset, 'exclude_' + name, False)
        style.color = (0.0, 0.0, 0.0)
        style.alpha = 1.0
        style.thickness = float(cfg['line_width_px'])
        style.thickness_position = 'CENTER'
        style.use_chaining = True
        style.chaining = 'PLAIN'
        style.use_same_object = True
        style.use_dashed_line = False
        style.caps = 'ROUND'
        for modifiers in (style.color_modifiers, style.alpha_modifiers,
                          style.thickness_modifiers, style.geometry_modifiers):
            for modifier in list(modifiers):
                modifiers.remove(modifier)
        evidence['blender_version'] = bpy.app.version_string
        evidence['line_selection'] = 'VISIBLE_EDGE_MARK_ONLY'
        evidence['pixel_aspect'] = [float(scene.render.pixel_aspect_x), float(scene.render.pixel_aspect_y)]
        evidence['resolution'] = [scene.render.resolution_x, scene.render.resolution_y]
        view_layer.update()
        return handle
    except BaseException:
        handle.cleanup()
        raise
