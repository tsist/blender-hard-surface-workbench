"""Read-only Catmull–Clark diagnostics on an isolated, disposable control cage.

Host-safe contract and topology analysis. Blender-only execution is lazy. This
operation never saves a blend or changes the loaded source object. It is not a
TurboSmooth implementation or a reference-shape/artistic acceptance test.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from . import contract as c
from .io import RuntimeFailure, checked_path, descriptor, verify_descriptor
from .surface_export import OPTION as _SURFACE_EXPORT

LIMITS = {'source_faces': 200000, 'source_vertices': 200000, 'source_edges': 600000, 'source_corners': 1200000,
          'evaluated_faces': 250000, 'evaluated_vertices': 300000,
          'aggregate_faces': 500000, 'face_corners': 8192,
          'longest_render_edge': 2048, 'render_pixel_samples': 320000000,
          'finding_examples': 32, 'geometry_file_bytes': 48*1024*1024, 'geometry_aggregate_bytes': 128*1024*1024}
from .source_modifier_contract import _SETTINGS, _SOURCE_MODIFIER, _AUTHORED_SETTINGS, _SOURCE_MODIFIER_PROPERTIES
_REFERENCE_COORD = c.number(minimum=-100000, maximum=100000)
_REFERENCE_LENGTH = c.number(exclusiveMinimum=0, maximum=100000)
_PANEL_REFERENCE = c.obj({
    'kind': c.optional_default(c.const('single_sharp_bore_rounded_panel'), 'single_sharp_bore_rounded_panel'),
    'coordinate_space': c.optional_default(c.const('world'), 'world'),
    'length_unit': c.optional_default(c.const('mm'), 'mm'),
    'size': c.array(_REFERENCE_LENGTH, 2, 2),
    'center': c.array(_REFERENCE_COORD, 2, 2),
    'corner_radius': _REFERENCE_LENGTH,
    'edge_bevel': _REFERENCE_LENGTH,
    'z_min': _REFERENCE_COORD, 'z_max': _REFERENCE_COORD,
    'holes': c.array(c.obj({'center': c.array(_REFERENCE_COORD, 2, 2), 'radius': _REFERENCE_LENGTH}), 1, 1),
    'tolerance_mm': c.number(exclusiveMinimum=0, maximum=100),
}, ['size','center','corner_radius','edge_bevel','z_min','z_max','holes','tolerance_mm'])
SPARSE_DIAGNOSTIC_PROFILE = 'source_bound_sparse_diagnostic_v1'
SPARSE_SOURCE_SCHEMA = 'sparse_sharp_panel_ids_v2'
_EVALUATION_PROFILE = c.union(
    c.obj({'mode': c.const('legacy_geometry_diagnostic_v0')}),
    c.obj({'mode': c.const('source_bound_qualification_v1'), 'expected_source_modifier': _SOURCE_MODIFIER}),
    c.obj({'mode': c.const(SPARSE_DIAGNOSTIC_PROFILE), 'expected_source_modifier': _SOURCE_MODIFIER}),
)
_LOOP_RECORD = c.obj({
    'role': c.string(minLength=1,maxLength=64),
    'vertex_indices': c.array(c.integer(minimum=0,maximum=LIMITS['source_vertices']-1),3,128,uniqueItems=True),
    'crease': c.number(minimum=0,maximum=1),
    'expected_valence': c.integer(minimum=1,maximum=32),
})
REQUEST = c.obj({
    'schema_version': c.const('1.0'), 'command': c.const('hardsurface.subdivision.diagnose'),
    'params': c.obj({
        'request_id': c.string(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', minLength=1, maxLength=128),
        'source': c.FILE,
        'target': c.union(c.obj({'object_id': c.UUID}), c.obj({'object_name': c.string(minLength=1, maxLength=128)})),
        'stack_mode': c.const('isolated_control_cage'),
        'export_geometry': c.optional_default(c.BOOL, False),
        'surface_export': _SURFACE_EXPORT,
        'panel_reference': _PANEL_REFERENCE,
        'levels': c.optional_default(c.array(c.integer(minimum=0, maximum=3), 1, 4, uniqueItems=True, contains={'const':0}, minContains=1), [0, 1, 2, 3]),
        'settings': _SETTINGS,
        'evaluation_profile': c.optional_default(_EVALUATION_PROFILE, {'mode': 'legacy_geometry_diagnostic_v0'}),
        'render': c.optional_default(c.obj({
            'enabled': c.optional_default(c.BOOL, True),
            'direction': c.optional_default(c.enum('front', 'back', 'top', 'bottom', 'side', 'three_quarter'), 'three_quarter'),
            'width': c.optional_default(c.integer(minimum=64, maximum=2048), 512),
            'height': c.optional_default(c.integer(minimum=64, maximum=2048), 512),
        }, []), {}),
        'max_evaluated_faces': c.optional_default(c.integer(minimum=1, maximum=LIMITS['evaluated_faces']), 200000),
        'max_aggregate_faces': c.optional_default(c.integer(minimum=1, maximum=LIMITS['aggregate_faces']), 400000),
        'cpu_threads': c.optional_default(c.integer(minimum=1, maximum=4), 2),
        'wall_seconds': c.optional_default(c.number(exclusiveMinimum=0, maximum=600), 600),
        'max_tree_rss_bytes': c.integer(minimum=1024**3, maximum=3*1024**3),
    }, ['request_id', 'source', 'target', 'stack_mode']),
})


def schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Hard Surface Workbench read-only subdivision diagnosis 1.0', **copy.deepcopy(REQUEST)}


def validate_request(request):
    if isinstance(request, (str, bytes, bytearray)): request = c.strict_loads(request)
    c._walk_limits(request)
    if len(c.canonical_bytes(request)) > c.MAX_BYTES: raise c.ContractError('LIMIT_EXCEEDED', 'Request exceeds 2 MiB')
    result = c._validate(request, REQUEST); c._paths(result)
    p = result['params']
    mode = p['evaluation_profile']['mode']
    if mode in ('source_bound_qualification_v1', SPARSE_DIAGNOSTIC_PROFILE):
        if 'settings' in p:
            raise c.ContractError('INVALID_REQUEST', 'Source-bound qualification uses only the complete expected_source_modifier profile; generic diagnostic settings cannot be mixed in')
        if 'object_id' not in p['target'] or 'panel_reference' not in p:
            raise c.ContractError('INVALID_REQUEST', 'Source-bound qualification requires an explicit native object_id and panel_reference')
    else:
        p.setdefault('settings', c._validate({}, _SETTINGS))
    if mode == SPARSE_DIAGNOSTIC_PROFILE:
        if 'levels' not in request['params'] or sorted(p['levels']) != [0, 1, 2, 3] or p['render']['enabled'] or not p['export_geometry']:
            raise c.ContractError('INVALID_REQUEST', 'Sparse source-bound diagnosis requires levels [0,1,2,3], render.enabled=false and export_geometry=true')
        required_baseline = {'levels': 0, 'render_levels': 0, 'quality': 6, 'use_limit_surface': True,
                             'use_creases': True, 'show_viewport': True, 'show_render': True}
        expected = p['evaluation_profile']['expected_source_modifier']
        if any(expected[key] != value for key, value in required_baseline.items()):
            raise c.ContractError('INVALID_REQUEST', 'Sparse diagnosis requires the authored enabled level-zero quality-6 limit-surface crease baseline')
        if 'max_tree_rss_bytes' in p and type(request['params']['max_tree_rss_bytes']) is not int:
            raise c.ContractError('INVALID_REQUEST', 'Sparse diagnosis memory budget must be an actual integer', '$.params.max_tree_rss_bytes')
    elif 'max_tree_rss_bytes' in p:
        raise c.ContractError('INVALID_REQUEST', 'Explicit subdivision memory budget is supported only by the sparse source-bound diagnostic profile')
    if Path(p['source']['file']).suffix.lower() != '.blend': raise c.ContractError('INVALID_REQUEST', 'Source must be a saved .blend file')
    # Level zero is always an explicit baseline; sorting does not add a sample.
    if 0 not in p['levels']: raise c.ContractError('INVALID_REQUEST', 'levels must include level 0 as the control baseline')
    p['levels'] = sorted(p['levels'])
    from .surface_export import validate_option
    validate_option(p)
    samples = len(p['levels']) * p['render']['width'] * p['render']['height'] * (32 + 128)
    if p['render']['enabled'] and samples > LIMITS['render_pixel_samples']:
        raise c.ContractError('LIMIT_EXCEEDED', 'Paired level renders exceed the aggregate pixel-sample budget')
    if 'panel_reference' in p:validate_panel_reference_domain(p['panel_reference'])
    return result


normalize_request = validate_request


def estimate_faces(face_arities, levels):
    """CC splits each n-gon to n quads once, then each quad to four."""
    arities = list(face_arities)
    if not arities or len(arities) > LIMITS['source_faces'] or any(type(n) is not int or not 3 <= n <= LIMITS['face_corners'] for n in arities):
        raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT', 'Control face count/arity outside bounded supported domain')
    return {str(level): len(arities) if level == 0 else sum(arities) * 4 ** (level - 1) for level in levels}




def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _components(adjacency, selected=None):
    remaining = set(range(len(adjacency))) if selected is None else set(selected)
    components = []
    while remaining:
        first = remaining.pop(); pending = [first]; members = []
        while pending:
            current = pending.pop(); members.append(current)
            for neighbour in adjacency[current]:
                if neighbour in remaining: remaining.remove(neighbour); pending.append(neighbour)
        components.append(members)
    return components


def _continuity_summary(adjacency, eligible, all_count):
    groups = _components(adjacency, eligible)
    closed = sum(all(len(adjacency[e]) == 2 for e in group) for group in groups)
    return {'components': len(groups), 'closed_cycles': closed, 'open_or_interrupted': len(groups)-closed,
            'participating_edges': len(eligible), 'nonparticipating_edges': all_count-len(eligible),
            'examples': [{'edge_count':len(g),'closed':all(len(adjacency[e]) == 2 for e in g),
                          'edge_indices':sorted(g)[:32],'indices_truncated':len(g)>32} for g in sorted(groups,key=lambda g:(-len(g),min(g)))[:LIMITS['finding_examples']]]}


def analyze_cage(vertices, edges, polygons):
    """Original indexed topology: edge loops and opposite-edge quad strips differ.

    This intentionally does not infer useful design flow merely from all-quads.
    Edge loops terminate at poles/boundaries/non-quad corners; quad strips cross
    each real quad between opposite edges and terminate at non-quad barriers.
    """
    if len(vertices)>LIMITS['source_vertices'] or len(polygons)>LIMITS['source_faces']:
        raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT','Control cage exceeds source vertex/face bound')
    if not vertices or not polygons: raise RuntimeFailure('SUBDIVISION_EMPTY','Target needs vertices and real faces')
    edge_map={tuple(sorted(e)):i for i,e in enumerate(edges)}
    if len(edge_map)!=len(edges):raise RuntimeFailure('SUBDIVISION_TOPOLOGY','Duplicate indexed edges are unsupported')
    incident=[[] for _ in edges]; neighbours=[set() for _ in vertices]; vertex_edges=[[] for _ in vertices]; vertex_faces=[set() for _ in vertices]
    for i,(a,b) in enumerate(edges):
        if a==b or not 0<=a<len(vertices) or not 0<=b<len(vertices):raise RuntimeFailure('SUBDIVISION_TOPOLOGY','Invalid indexed source edge')
        neighbours[a].add(b);neighbours[b].add(a);vertex_edges[a].append(i);vertex_edges[b].append(i)
    face_edges=[]
    for fi,face in enumerate(polygons):
        if not 3<=len(face)<=LIMITS['face_corners'] or len(set(face))!=len(face):raise RuntimeFailure('SUBDIVISION_TOPOLOGY','Unsupported degenerate polygon')
        ring=[]
        for a,b in zip(face,face[1:]+face[:1]):
            edge=edge_map.get(tuple(sorted((a,b))))
            if edge is None:raise RuntimeFailure('SUBDIVISION_TOPOLOGY','Polygon boundary has no real mesh edge')
            incident[edge].append((fi,a,b));ring.append(edge);vertex_faces[a].add(fi)
        face_edges.append(ring)
    boundary={i for i,uses in enumerate(incident) if len(uses)==1}
    nonmanifold={i for i,uses in enumerate(incident) if len(uses)>2}
    wire={i for i,uses in enumerate(incident) if len(uses)==0}
    boundary_vertices={v for i in boundary for v in edges[i]}
    winding=[i for i,uses in enumerate(incident) if len(uses)==2 and uses[0][1:]==uses[1][1:]]
    valences=Counter(len(n) for n in neighbours)
    poles=[i for i,n in enumerate(neighbours) if i not in boundary_vertices and len(n)!=4]
    regular_boundary=[i for i in boundary_vertices if len(neighbours[i])==3]
    quad_vertices={v for fi,f in enumerate(polygons) if len(f)==4 for v in f}
    loop_graph=[set() for _ in edges]
    eligible_loop=set()
    for v,around in enumerate(vertex_edges):
        if len(around)!=4 or v in boundary_vertices or any(len(incident[e])!=2 for e in around) or any(len(polygons[f])!=4 for f in vertex_faces[v]):continue
        for edge in around:
            adjacent=set()
            for fi,_,_ in incident[edge]:
                adjacent.update(e for e in face_edges[fi] if e!=edge and v in edges[e])
            opposite=set(around)-adjacent-{edge}
            if len(opposite)==1:
                other=opposite.pop();loop_graph[edge].add(other);loop_graph[other].add(edge);eligible_loop.update((edge,other))
    strip_graph=[set() for _ in edges];eligible_strip=set()
    for ring in face_edges:
        if len(ring)!=4:continue
        for a,b in ((ring[0],ring[2]),(ring[1],ring[3])):
            if a in nonmanifold or b in nonmanifold:continue
            strip_graph[a].add(b);strip_graph[b].add(a);eligible_strip.update((a,b))
    # Manifoldness also needs each vertex's incident face fan to be connected.
    nonmanifold_vertices=[]
    for v,faces in enumerate(vertex_faces):
        if not faces:continue
        pending=[next(iter(faces))];seen=set(pending)
        while pending:
            f=pending.pop()
            for e in face_edges[f]:
                if v not in edges[e]:continue
                for nf,_,_ in incident[e]:
                    if nf not in seen:seen.add(nf);pending.append(nf)
        if seen!=faces or any(len(incident[e])>2 for e in vertex_edges[v]):nonmanifold_vertices.append(v)
    arities=Counter(map(len,polygons));connected=_components(neighbours)
    return {'counts':{'vertices':len(vertices),'edges':len(edges),'faces':len(polygons),'quads':arities.get(4,0),'triangles':arities.get(3,0),'ngons':sum(n for k,n in arities.items() if k>4),
                      'derived_triangle_count':sum(n-2 for n in map(len,polygons))},
            'face_arity_histogram':{str(k):v for k,v in sorted(arities.items())},
            'quad_ratio':arities.get(4,0)/len(polygons),
            'vertex_valence_histogram':{str(k):v for k,v in sorted(valences.items())},
            'components':len(connected),'component_vertex_counts':sorted(map(len,connected),reverse=True)[:32],
            'manifold':{'boundary_edges':len(boundary),'nonmanifold_edges':len(nonmanifold),'wire_edges':len(wire),
                        'nonmanifold_vertices':len(nonmanifold_vertices),'inconsistent_winding_edges':len(winding),
                        'isolated_vertices':sum(not n for n in neighbours),
                        'closed_consistently_oriented':not (boundary or nonmanifold or wire or nonmanifold_vertices or winding) and all(neighbours),
                        'self_intersections':'not_tested'},
            'poles':{'definition':'Interior edge-neighbour valence differs from 4; boundary corners reported separately',
                     'interior_count':len(poles),'interior_examples':[{'vertex_index':v,'valence':len(neighbours[v]),'local_position':list(vertices[v])} for v in poles[:32]],
                     'boundary_vertices':len(boundary_vertices),'regular_boundary_valence3_count':len(regular_boundary),
                     'boundary_other_valence_count':len(boundary_vertices)-len(regular_boundary)},
            'edge_loop_continuity':{**_continuity_summary(loop_graph,eligible_loop,len(edges)),
                'definition':'Opposite continuation at interior, manifold, valence-4 vertices surrounded only by real quads; terminates at every unsupported junction'},
            'quad_strip_continuity':{**_continuity_summary(strip_graph,eligible_strip,len(edges)),
                'definition':'Opposite-edge traversal across real quad faces (edge rings); distinct from vertex-following edge loops'},
            'topology_sha256':_hash({'edges':edges,'polygons':polygons}),
            'limitations':['All-quad status and closed loops do not prove useful support placement or absence of pinching',
                           'Self-intersection, reference-shape error, and aesthetic surface quality are not assigned',
                           'Derived triangles are tessellation/analysis data; source quad counts use real polygons only']}


def _mesh_arrays(mesh):
    return ([tuple(float(x) for x in v.co) for v in mesh.vertices],
            [tuple(e.vertices) for e in mesh.edges], [tuple(p.vertices) for p in mesh.polygons])


def _mesh_signature(obj):
    from .core import modifiers_snapshot
    arrays=_mesh_arrays(obj.data)
    attributes=[]
    for a in obj.data.attributes:
        values=[]
        for item in a.data:
            for field in ('value','vector','color','byte_color'):
                if hasattr(item,field):
                    value=getattr(item,field)
                    values.append(value if isinstance(value,(int,float,bool,str)) else list(value));break
        attributes.append({'name':a.name,'domain':a.domain,'type':a.data_type,'sha256':_hash(values)})
    return _hash({'arrays':arrays,'matrix':[[float(x) for x in row] for row in obj.matrix_world],
                  'modifiers':modifiers_snapshot(obj),'attributes':attributes,
                  'face_smooth':[p.use_smooth for p in obj.data.polygons],
                  'face_materials':[p.material_index for p in obj.data.polygons],
                  'materials':[m.name if m else None for m in obj.data.materials]})


def prepare_source_evaluation(obj, profile, *, registry, unit_scale):
    """Actual modifier/native/registry checks run before allocating a clone."""
    from .subdivision_source import capture_semantic_source, require_modifier_profile, snapshot_source_modifier
    if profile['mode'] not in ('source_bound_qualification_v1', SPARSE_DIAGNOSTIC_PROFILE):
        return {'mode': 'legacy_geometry_diagnostic_v0', 'qualification': 'not_qualified',
                'reason': 'Independent generic diagnostic settings are not a source-bound evaluation'}, None
    actual = snapshot_source_modifier(obj)
    modifier = require_modifier_profile(actual, profile['expected_source_modifier'])
    oid = obj.get('hs_object_id')
    if oid not in registry:
        raise RuntimeFailure('SUBDIVISION_SOURCE_BINDING', 'Actual native object has no independent scene-registry entry', object_id=oid)
    sparse = profile['mode'] == SPARSE_DIAGNOSTIC_PROFILE
    source = capture_semantic_source(obj, unit_scale=unit_scale, expected_binding=registry[oid], require_bound=True,
                                     **({'source_schema': SPARSE_SOURCE_SCHEMA} if sparse else {}))
    report = {'mode': profile['mode'], 'qualification': 'source_binding_verified',
            'modifier_profile': modifier, 'semantic_source': copy.deepcopy(source['evidence']),
            'registry_entry_sha256': _hash(registry[oid]),
            'intentional_sample_overrides': ['levels', 'render_levels', 'show_viewport', 'show_render'],
            'sample_override_policy': 'levels/render_levels equal the sampled level; level 0 disables the otherwise enabled modifier as an explicit raw-control baseline'}
    if sparse:
        report.update(qualification='source_bound_diagnostic_only', formal_target_level=2,
                      baseline_level=0, diagnostic_comparison_levels=[1, 3], g3_qualification='not_run')
    return report, source


def configure_source_clone_modifier(mod, source_settings, level):
    """Copy and verify every actual writable source field, with stated samples."""
    expected = copy.deepcopy(source_settings)
    expected.update(levels=level, render_levels=level, show_viewport=bool(level), show_render=bool(level))
    for key, value in expected.items():
        if key != 'type':
            try: setattr(mod, key, value)
            except (AttributeError, TypeError, ValueError) as exc:
                raise RuntimeFailure('SUBDIVISION_CLONE_PROFILE', 'Source setting cannot be copied to isolated clone', property=key, reason=str(exc))
    # Direct property readback also works for disabled L0, unlike source preflight.
    actual = {'type': mod.type}
    for key in expected:
        if key != 'type': actual[key] = getattr(mod, key)
    if _hash(actual) != _hash(expected):
        raise RuntimeFailure('SUBDIVISION_CLONE_PROFILE', 'Actual clone modifier settings differ from the source-bound per-level profile', expected=expected, actual=actual)
    overrides = {key: {'source': source_settings[key], 'sample': expected[key]} for key in expected if expected[key] != source_settings[key]}
    return {'status': 'pass', 'actual_clone_modifier': actual, 'actual_source_modifier_sha256': _hash(source_settings),
            'actual_clone_modifier_sha256': _hash(actual), 'intentional_overrides': overrides}


def source_scene_evaluation(scene):
    """Reject scene-wide geometry overrides that a new scene could hide."""
    if not hasattr(scene.render, 'use_simplify') or scene.render.use_simplify:
        raise RuntimeFailure('SUBDIVISION_SCENE_PROFILE_UNSUPPORTED', 'Source-bound fixed-level sampling requires source scene render.use_simplify=False')
    return {'use_simplify': False, 'frame_current': int(scene.frame_current),
            'scale_length': float(scene.unit_settings.scale_length),
            'policy': 'Scene-wide subdivision simplification is unsupported; explicit levels remain the only geometry sampling override'}


def _shape_metrics(mesh, matrix):
    from mathutils import Vector
    vertices,edges,polygons=_mesh_arrays(mesh)
    # Evaluated mesh has a higher budget than source. Pure topology analyzer has
    # source limits; shape manifold analysis needs only linear adjacency counts.
    points=[matrix@Vector(v) for v in vertices]
    if not points or any(not math.isfinite(x) or abs(x)>1e6 for p in points for x in p):raise RuntimeFailure('SUBDIVISION_GEOMETRY','Finite bounded sampled coordinates required')
    low=[min(p[i] for p in points) for i in range(3)];high=[max(p[i] for p in points) for i in range(3)]
    center=Vector([(a+b)/2 for a,b in zip(low,high)])
    mesh.calc_loop_triangles(); area=0.;volume=0.
    for tri in mesh.loop_triangles:
        a,b,c0=[points[i] for i in tri.vertices]
        area+=(b-a).cross(c0-a).length*.5
        volume+=(a-center).dot((b-center).cross(c0-center))/6
    uses=defaultdict(list);neighbors=[set() for _ in vertices]
    for a,b in edges:neighbors[a].add(b);neighbors[b].add(a)
    for face in polygons:
        for a,b in zip(face,face[1:]+face[:1]):uses[tuple(sorted((a,b)))].append((a,b))
    boundaries=sum(len(v)==1 for v in uses.values());nonmanifold=sum(len(v)>2 for v in uses.values());winding=sum(len(v)==2 and v[0]==v[1] for v in uses.values());wire=len(edges)-len(uses)
    closed=not(boundaries or nonmanifold or winding or wire) and all(neighbors)
    return {'vertices':len(vertices),'edges':len(edges),'faces':len(polygons),'quads':sum(len(p)==4 for p in polygons),
            'blender_loop_triangles':len(mesh.loop_triangles),'triangulation_domain':'Derived analysis tessellation, not source topology',
            'bbox_m':{'min':low,'max':high,'dimensions':[b-a for a,b in zip(low,high)],'center':list(center)},
            'surface_area_m2':area,'oriented_volume_integral_m3':volume,
            'volume_interpretation':'Closed oriented surface integral; not a self-intersection or watertight-solid proof' if closed else 'Open/nonmanifold surface integral; not enclosed volume',
            'manifold':{'boundary_edges':boundaries,'nonmanifold_edges':nonmanifold,'wire_edges':wire,'inconsistent_winding_edges':winding,'closed_consistent_edges':closed},
            'components':len(_components(neighbors)), 'local_geometry_sha256':_hash({'vertices':vertices,'edges':edges,'polygons':polygons})}


def _shrinkage(baseline, sample):
    base=baseline['bbox_m'];now=sample['bbox_m']
    ratios=[n/b if b>1e-15 else None for b,n in zip(base['dimensions'],now['dimensions'])]
    return {'baseline':'level_0_original_control_cage','dimension_ratio_xyz':ratios,
            'dimension_change_m_xyz':[n-b for b,n in zip(base['dimensions'],now['dimensions'])],
            'bbox_inset_m_min_xyz':[n-b for b,n in zip(base['min'],now['min'])],
            'bbox_inset_m_max_xyz':[b-n for b,n in zip(base['max'],now['max'])],
            'bbox_center_shift_m_xyz':[n-b for b,n in zip(base['center'],now['center'])],
            'surface_area_ratio':sample['surface_area_m2']/baseline['surface_area_m2'] if baseline['surface_area_m2']>1e-30 else None,
            'oriented_volume_integral_ratio':sample['oriented_volume_integral_m3']/baseline['oriented_volume_integral_m3'] if abs(baseline['oriented_volume_integral_m3'])>1e-30 else None,
            'reference_shape_error':'not_available_no_reference_shape_supplied'}


def orthographic_scale(horizontal_span, vertical_span, width, height):
    """Blender HORIZONTAL sensor fit: ortho_scale denotes horizontal span."""
    if not all(math.isfinite(x) and x>=0 for x in (horizontal_span,vertical_span)) or width<=0 or height<=0:
        raise RuntimeFailure('SUBDIVISION_RENDER_BOUNDS','Invalid projected framing inputs')
    return max(horizontal_span,vertical_span*width/height)*1.18


def _create_render_rig(scene, baseline, config, created):
    """One camera/light frame fixed from L0; same framing for every sample."""
    import bpy
    from mathutils import Vector, Matrix
    from .observation import DIRECTIONS
    bbox=baseline['bbox_m'];low=Vector(bbox['min']);high=Vector(bbox['max']);center=(low+high)*.5
    extent=max(high-low)
    if extent<1e-7:raise RuntimeFailure('SUBDIVISION_RENDER_BOUNDS','Control bounds have no usable render extent')
    camera_data=bpy.data.cameras.new('HS SubD diagnosis camera');created.append((bpy.data.cameras,camera_data))
    camera=bpy.data.objects.new(camera_data.name,camera_data);created.append((bpy.data.objects,camera));scene.collection.objects.link(camera);scene.camera=camera
    camera_data.type='ORTHO';camera_data.sensor_fit='HORIZONTAL';camera_data.clip_start=max(extent*1e-4,1e-6);camera_data.clip_end=max(extent*100,1)
    camera.location=center+Vector(DIRECTIONS[config['direction']])*extent
    outward=(camera.location-center).normalized();up=Vector((0,1,0) if config['direction'] in ('top','bottom') else (0,0,1))
    right=(-outward).cross(up).normalized();camera_up=right.cross(-outward).normalized()
    camera.rotation_euler=Matrix((right,camera_up,outward)).transposed().to_euler()
    points=[Vector((x,y,z)) for x in (low.x,high.x) for y in (low.y,high.y) for z in (low.z,high.z)]
    horizontal=[p.dot(right) for p in points];vertical=[p.dot(camera_up) for p in points]
    camera_data.ortho_scale=orthographic_scale(max(horizontal)-min(horizontal),max(vertical)-min(vertical),config['width'],config['height'])
    world=bpy.data.worlds.new('HS SubD diagnosis world');created.append((bpy.data.worlds,world));world.use_nodes=True;scene.world=world
    material=bpy.data.materials.new('HS SubD diagnosis temporary override');created.append((bpy.data.materials,material));material.use_nodes=True
    scene.view_layers[0].material_override=material
    lights=[]
    for i in range(3):
        data=bpy.data.lights.new('HS SubD diagnosis light '+str(i),'AREA');created.append((bpy.data.lights,data))
        light=bpy.data.objects.new(data.name,data);created.append((bpy.data.objects,light));scene.collection.objects.link(light);lights.append(light)
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.shading_system=False
    scene.cycles.use_adaptive_sampling=False;scene.cycles.seed=0;scene.cycles.use_animated_seed=False
    scene.render.use_compositing=False;scene.render.use_sequencer=False
    scene.render.threads_mode='FIXED';scene.render.threads=config['cpu_threads']
    scene.render.resolution_x=config['width'];scene.render.resolution_y=config['height'];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.render.film_transparent=False
    scene.view_settings.view_transform='AgX';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
    from bpy_extras.object_utils import world_to_camera_view
    with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
        bpy.context.view_layer.update()
        projected=[world_to_camera_view(scene,camera,point) for point in points]
    if any(not all(math.isfinite(x) for x in point) or not (.05<=point.x<=.95 and .05<=point.y<=.95 and point.z>0) for point in projected):
        raise RuntimeFailure('SUBDIVISION_CAMERA_FRAME','All eight baseline bbox corners must fit within the five-percent safe frame',projected_corners=[list(v) for v in projected])
    frame_check={'status':'pass','required_margin_normalized':.05,'projected_bbox_corners':[list(v) for v in projected],
                 'tested_points':8,'method':'bpy_extras.object_utils.world_to_camera_view','sensor_fit':'HORIZONTAL'}
    return {'center':center,'extent':extent,'right':right,'up':camera_up,'outward':outward,'world':world,'material':material,'lights':lights,
            'evidence':{'frame':'Fixed from original level-0 world-space bounds for every level and preset',
                'bbox_framing_check':frame_check,'camera':{'type':'ORTHO','sensor_fit':'HORIZONTAL','position_m':list(camera.location),'target_m':list(center),'ortho_scale_m':camera_data.ortho_scale,
                    'rotation_euler':list(camera.rotation_euler),'clip_start_m':camera_data.clip_start,'clip_end_m':camera_data.clip_end},
                'baseline_center_m':list(center),'baseline_extent_m':extent,'display_transform':'AgX','engine':'CYCLES','device':'CPU','adaptive_sampling':False,'seed':0,'animated_seed':False}}


def _render_pair(scene, rig, level, config, job, deadline):
    import bpy
    from mathutils import Matrix
    from .observation import diagnostic_settings, _png_record
    results=[]
    for preset in ('neutral','reflection_strips'):
        deadline();s=diagnostic_settings(preset);scene.cycles.samples=s['samples'];scene.cycles.use_denoising=s['denoising']
        if s['denoising']:scene.cycles.denoiser='OPENIMAGEDENOISE'
        bg=rig['world'].node_tree.nodes.get('Background');bg.inputs['Color'].default_value=s['world_color'];bg.inputs['Strength'].default_value=s['world_strength']
        bsdf=rig['material'].node_tree.nodes.get('Principled BSDF')
        for key,slot in (('base_color','Base Color'),('roughness','Roughness'),('metallic','Metallic')):bsdf.inputs[slot].default_value=s['material_override'][key]
        lighting=[]
        for light,(x,y,z,power) in zip(rig['lights'],s['lights_camera_basis']):
            light.location=rig['center']+(rig['right']*x+rig['up']*y+rig['outward']*z)*rig['extent']
            target=light.location-rig['outward']*rig['extent'] if preset=='reflection_strips' else rig['center']
            forward=(target-light.location).normalized();right=forward.cross(rig['up']).normalized();up=right.cross(forward).normalized()
            light.rotation_euler=Matrix((right,up,-forward)).transposed().to_euler();light.data.energy=power*rig['extent']**2
            light.data.shape=s['light_shape'];light.data.size=rig['extent']*s['light_size_extent']
            if preset=='reflection_strips':light.data.size_y=rig['extent']*s['light_size_y_extent']
            lighting.append({'position_m':list(light.location),'rotation_euler':list(light.rotation_euler),'shape':light.data.shape,'energy_w':light.data.energy,'size_m':light.data.size,'size_y_m':light.data.size_y if preset=='reflection_strips' else None})
        path=checked_path(job/('subdivision-l%d-%s.png'%(level,preset)),exists=False)
        if path.exists():raise RuntimeFailure('OUTPUT_COLLISION','Diagnosis image already exists',file=str(path))
        scene.render.filepath=str(path)
        bpy.ops.render.render(write_still=True,scene=scene.name)
        image=_png_record(path,config['width'],config['height'])
        results.append({**image,'level':level,'preset':preset,'view':config['direction'],
                        'settings':s,'lighting':lighting,'frame':rig['evidence'],'visual_acceptance':'not_run'})
        deadline()
    return results


def execute(request, job_dir):
    """Source clone → real Catmull–Clark levels → metrics and paired views."""
    request=validate_request(request);p=request['params'];source_before=verify_descriptor(p['source'])
    job=checked_path(job_dir,exists=False)
    if not job.is_dir():raise RuntimeFailure('SUBDIVISION_OUTPUT','Existing owned job directory required')
    import bpy
    from .ops.geometry import require_si_scene
    from .core import modifiers_snapshot, loaded_structure_registry
    opened=descriptor(checked_path(bpy.data.filepath))
    if opened['sha256']!=source_before['sha256']:raise RuntimeFailure('SUBDIVISION_SOURCE','Opened source SHA differs from declared input')
    units=require_si_scene();started=time.monotonic()
    if len(bpy.context.scene.objects)>4096:raise RuntimeFailure('SUBDIVISION_SCENE_LIMIT','Scene exceeds 4096 objects')
    target=p['target'];matches=[o for o in bpy.context.scene.objects if (o.get('hs_object_id')==target['object_id'] if 'object_id' in target else o.name==target['object_name'])]
    if len(matches)!=1:raise RuntimeFailure('SUBDIVISION_TARGET','Target selector must match exactly one scene object',matches=len(matches),target=target)
    original=matches[0]
    if original.type!='MESH' or original.library or original.override_library or original.data.library or original.mode!='OBJECT':raise RuntimeFailure('SUBDIVISION_TARGET_UNSUPPORTED','Only local Mesh objects in Object mode are supported')
    if original.parent or original.constraints or original.animation_data or original.data.shape_keys or original.instance_type!='NONE':raise RuntimeFailure('SUBDIVISION_TARGET_UNSUPPORTED','Parenting, constraints, animation, shape keys and instances are unsupported')
    unsupported=[{'name':m.name,'type':m.type} for m in original.modifiers if m.type!='SUBSURF']
    if unsupported or len(original.modifiers)>1:raise RuntimeFailure('SUBDIVISION_STACK_UNSUPPORTED','Only an empty stack or one recorded SUBSURF is supported; other modifiers are never silently removed',modifiers=modifiers_snapshot(original),unsupported=unsupported)
    if (len(original.data.vertices)>LIMITS['source_vertices'] or len(original.data.polygons)>LIMITS['source_faces'] or len(original.data.edges)>LIMITS['source_edges'] or len(original.data.loops)>LIMITS['source_corners']):raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT','Source exceeds control geometry bounds')
    original_stack=modifiers_snapshot(original);signature=_mesh_signature(original)
    source_metadata_signature=_authored_metadata_signature(original)
    sparse_diagnostic=p['evaluation_profile']['mode']==SPARSE_DIAGNOSTIC_PROFILE
    source_bound=p['evaluation_profile']['mode'] in ('source_bound_qualification_v1',SPARSE_DIAGNOSTIC_PROFILE)
    registry=loaded_structure_registry() if source_bound else {}
    evaluation_profile,semantic_source=prepare_source_evaluation(original,p['evaluation_profile'],registry=registry,unit_scale=units['scale_length'])
    loop_inspection=(inspect_sparse_authored_control_loops(original,semantic_source) if sparse_diagnostic
                     else inspect_authored_control_loops(original))
    if source_bound:evaluation_profile['source_scene_evaluation']=source_scene_evaluation(bpy.context.scene)
    if source_bound and loop_inspection['status']!='pass':
        raise RuntimeFailure('SUBDIVISION_SOURCE_BINDING','Source-bound qualification requires valid actual authored control loops')
    surface_enabled='surface_export' in p
    if surface_enabled:
        from . import surface_export as surfaces
        from .surface_projection import calibrate_views
        surface_binding=surfaces.bind_observation(p['surface_export'],source_before,original.get('hs_object_id'),p['evaluation_profile']['expected_source_modifier'], sparse=sparse_diagnostic)
        if sparse_diagnostic:
            surfaces.require_equal(semantic_source['evidence'], surface_binding['sparse_evaluation']['source_binding']['semantic_source'], role='sparse_surface_source_vs_observation')
        source_normal_record=surfaces.mesh_record(original.data,surfaces._mesh_record)
        surfaces.require_equal(surface_binding['source_record']['source_control_mesh'],source_normal_record,role='original_control_vs_pinned_observation')
        source_matrix=surfaces.matrix_values(original.matrix_world)
        if any(view['original_matrix_world']!=source_matrix for view in surface_binding['views']):
            raise RuntimeFailure('SUBDIVISION_SURFACE_EXPORT','Actual source matrix differs from pinned observation')
        source_surface_maps=surfaces.capture_source_maps(original,semantic_source,unit_scale=units['scale_length'],registry_entry=registry[original.get('hs_object_id')])
        surface_bytes=[0];surface_levels=[];surface_cameras=[]
    vertices,edges,faces=_mesh_arrays(original.data)
    estimates=estimate_faces([len(face) for face in faces],p['levels'])
    if max(estimates.values())>p['max_evaluated_faces'] or sum(estimates.values())>p['max_aggregate_faces']:
        raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT','Predicted real Catmull–Clark faces exceed budget before allocation',predicted_faces=estimates,max_evaluated_faces=p['max_evaluated_faces'],max_aggregate_faces=p['max_aggregate_faces'])
    cage=analyze_cage(vertices,edges,faces)
    source_shading={'smooth_faces':sum(f.use_smooth for f in original.data.polygons),'flat_faces':sum(not f.use_smooth for f in original.data.polygons),'has_custom_normals':bool(original.data.has_custom_normals),'sharp_edges':sum(e.use_edge_sharp for e in original.data.edges)}
    crease_attributes=[]
    for name in ('crease_edge','crease_vert'):
        attr=original.data.attributes.get(name)
        if attr:
            values=[float(v.value) for v in attr.data]
            crease_attributes.append({'name':name,'domain':attr.domain,'data_type':attr.data_type,'count':len(values),'nonzero_count':sum(v!=0 for v in values),'minimum':min(values,default=None),'maximum':max(values,default=None),'values_sha256':_hash(values)})
    created=[];diag_scene=None;rows=[];previews=[];outputs=[];exported_bytes=[0];baseline=None;restored=False
    def deadline():
        if time.monotonic()-started>p['wall_seconds']:raise RuntimeFailure('SUBDIVISION_DEADLINE','Subdivision diagnostic wall budget exhausted')
    try:
        deadline()
        diag_scene=bpy.data.scenes.new('HS Subdivision Diagnostic '+uuid.uuid4().hex[:8]);created.append((bpy.data.scenes,diag_scene))
        diag_scene.unit_settings.system='METRIC';diag_scene.unit_settings.scale_length=1.
        if source_bound:diag_scene.render.use_simplify=False;diag_scene.frame_set(evaluation_profile['source_scene_evaluation']['frame_current'])
        mesh=original.data.copy();created.append((bpy.data.meshes,mesh))
        clone=bpy.data.objects.new('HS SubD isolated cage',mesh);created.append((bpy.data.objects,clone));diag_scene.collection.objects.link(clone);clone.matrix_world=original.matrix_world.copy()
        # Generic diagnosis retains its historical display override. Qualification
        # preserves the source's actual smooth/sharp data and rejects unsupported
        # custom normals before cloning, rather than changing their semantics.
        if not source_bound:
            for polygon in mesh.polygons:polygon.use_smooth=True
            for edge in mesh.edges:edge.use_edge_sharp=False
            custom=mesh.attributes.get('custom_normal')
            if custom is not None:mesh.attributes.remove(custom)
            if mesh.has_custom_normals:raise RuntimeFailure('SUBDIVISION_NORMALS_UNSUPPORTED','Temporary custom normals could not be removed; source unchanged')
        clone_shading={'smooth_faces':sum(f.use_smooth for f in mesh.polygons),'flat_faces':sum(not f.use_smooth for f in mesh.polygons),
                       'has_custom_normals':bool(mesh.has_custom_normals),'sharp_edges':sum(e.use_edge_sharp for e in mesh.edges)}
        mod=clone.modifiers.new('HS diagnostic Catmull-Clark','SUBSURF')
        if not source_bound:
            mod.subdivision_type='CATMULL_CLARK';mod.show_only_control_edges=False
            for key,value in p['settings'].items():setattr(mod,key,value)
            if hasattr(mod,'use_custom_normals'):mod.use_custom_normals=False
            if hasattr(mod,'use_creases'):mod.use_creases=True
        backend={'name':'Blender Subdivision Surface','algorithm':'CATMULL_CLARK','blender_version':bpy.app.version_string,
                 'blender_build_hash':bpy.app.build_hash.decode(),'opensubdiv_build_enabled':bool(getattr(bpy.app.build_options,'opensubdiv',False)),
                 'turbo_smooth_tested':False,'turbo_smooth_equivalence':'not_claimed','requested_settings':p.get('settings'),
                 'evaluation_profile':evaluation_profile,
                 'crease_attributes':crease_attributes,'source_modifier_stack':original_stack,
                 'stack_mode':'isolated_control_cage','source_stack_evaluation':('Exact actual source SUBSURF copied and verified on independent clone; only reported level/baseline-enable overrides' if source_bound else 'Generic diagnostic SUBSURF replaces the recorded source stack on an independent clone; not source-bound qualification'),
                 'diagnostic_shading':{'clone_face_smooth':not bool(clone_shading['flat_faces']),'source_face_shading':source_shading,'custom_normals':False,'verified_clone_mesh':clone_shading,'normal_override_scope':('No mesh smooth/sharp/normal override; source data preserved on clone' if source_bound else 'Temporary clone at all levels including 0; source unchanged; level-0 smooth diagnostic shading is not the original delivered normal appearance')}}
        if surface_enabled:
            source_maps_ref=surfaces.write_json_new(job/'subdivision-source-surface-maps.json',source_surface_maps,surface_bytes,deadline=deadline)
            outputs.append({**source_maps_ref,'kind':'native_surface_source_maps'})
            surface_cameras=calibrate_views([view['calibration_input'] for view in surface_binding['views']],bpy,diag_scene,created)
            calibration_ref=surfaces.write_json_new(job/'subdivision-observation-calibration.json',{'schema_version':'1.0','format':'HS_NATIVE_OBSERVATION_CALIBRATION_V1','source':source_before,'observation':surface_binding['report'],'views':surface_cameras,'images':[view['image'] for view in surface_binding['views']],'authority':surface_binding['authority']},surface_bytes,deadline=deadline)
            outputs.append({**calibration_ref,'kind':'native_observation_calibration'})
        rig=None;actual_aggregate=0
        for level in p['levels']:
            deadline();sample_profile=None
            if source_bound:
                sample_profile=configure_source_clone_modifier(mod,evaluation_profile['modifier_profile']['actual_source_modifier'],level)
            else:
                mod.levels=level;mod.render_levels=level;mod.show_viewport=bool(level);mod.show_render=bool(level)
            with bpy.context.temp_override(scene=diag_scene,view_layer=diag_scene.view_layers[0]):
                bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();evaluated=clone.evaluated_get(dg)
                temporary=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=dg)
                try:
                    if len(temporary.polygons)>p['max_evaluated_faces'] or len(temporary.vertices)>LIMITS['evaluated_vertices']:raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT','Actual evaluated sample exceeds face/vertex cap')
                    actual_aggregate+=len(temporary.polygons)
                    if actual_aggregate>p['max_aggregate_faces']:raise RuntimeFailure('SUBDIVISION_GEOMETRY_LIMIT','Actual evaluated samples exceed aggregate face cap')
                    metrics=_shape_metrics(temporary,evaluated.matrix_world)
                    reference_check=None;semantic_domain=None
                    if source_bound:
                        from .subdivision_source import evaluated_bore_domain
                        semantic_domain=evaluated_bore_domain(temporary,semantic_source,level=level)
                    if 'panel_reference' in p:
                        reference_check=measure_against_panel(temporary,evaluated.matrix_world,p['panel_reference'],semantic_domain=semantic_domain)
                        if sparse_diagnostic:
                            reference_check.update(diagnostic_only=True,formal_shape_qualification='not_run',
                                finite_vertex_station_policy='Existing minimum-24 vertex coincidence/coverage screen at reference z_min/mid/z_max; not a frozen ray-section protocol or an independent diameter/position measurement')
                    geometry=None;surface_geometry=None
                    if p['export_geometry']:
                        geometry=_export_geometry(temporary,evaluated.matrix_world,job,level,source_before,metrics['local_geometry_sha256'],exported_bytes,
                                                  **({'semantic_source':semantic_source,'semantic_domain':semantic_domain} if sparse_diagnostic else {}))
                        outputs.append({**geometry,'level':level,'kind':'subdivision_evaluated_geometry'})
                    if surface_enabled and level in p['surface_export']['levels']:
                        surface_geometry=surfaces.export_level(temporary,evaluated.matrix_world,job,level,source_before,source_maps_ref,semantic_domain,surface_binding,surface_bytes,deadline=deadline)
                        outputs.append(surface_geometry);surface_levels.append(surface_geometry)
                    if metrics['faces']!=estimates[str(level)]:raise RuntimeFailure('SUBDIVISION_EVALUATION','Actual subdivision face count differs from conservative contract estimate',level=level,expected=estimates[str(level)],actual=metrics['faces'])
                finally:evaluated.to_mesh_clear()
            if level==0:baseline=metrics
            row={'level':level,'metrics':metrics,'shrinkage':_shrinkage(baseline,metrics),'actual_modifier_stack':modifiers_snapshot(clone),'views':[]}
            if sparse_diagnostic:
                row['sample_role']='raw_control_baseline' if level==0 else 'formal_target_with_limited_diagnostics' if level==2 else 'diagnostic_comparison_only'
                row['formal_shape_qualification']='not_run'
            if sample_profile is not None:row['source_bound_evaluation']=sample_profile
            if semantic_domain is not None:row['semantic_face_transport']=semantic_domain['evidence']
            if reference_check is not None:row['reference_check']=reference_check
            if geometry is not None:row['geometry']=geometry
            if surface_geometry is not None:row['surface_geometry']=surface_geometry
            if p['render']['enabled']:
                if rig is None:rig=_create_render_rig(diag_scene,baseline,{**p['render'],'cpu_threads':p['cpu_threads']},created)
                row['views']=_render_pair(diag_scene,rig,level,p['render'],job,deadline);previews.extend(row['views'])
            rows.append(row)
            deadline()
    finally:
        # Only temporary datablocks owned by this invocation are removed.
        # Source objects, meshes, transforms, materials and settings are untouched.
        for collection,data in reversed(created):collection.remove(data,do_unlink=True)
        source_after=verify_descriptor(p['source']);opened_after=descriptor(checked_path(bpy.data.filepath))
        if source_before!=source_after or opened!=opened_after or _mesh_signature(original)!=signature or _authored_metadata_signature(original)!=source_metadata_signature:
            raise RuntimeFailure('SUBDIVISION_SOURCE_CHANGED','Source bytes or target in-memory geometry/settings changed')
        if source_bound:
            after_profile,after_semantic=prepare_source_evaluation(original,p['evaluation_profile'],registry=loaded_structure_registry(),unit_scale=units['scale_length'])
            after_profile['source_scene_evaluation']=source_scene_evaluation(bpy.context.scene)
            if after_profile!=evaluation_profile or after_semantic!=semantic_source:
                raise RuntimeFailure('SUBDIVISION_SOURCE_CHANGED','Actual source native binding, semantic provenance or scene registry changed')
        if surface_enabled:
            surfaces.require_equal(source_normal_record,surfaces.mesh_record(original.data,surfaces._mesh_record),role='original_control_after_surface_export')
            after_maps=surfaces.capture_source_maps(original,after_semantic,unit_scale=units['scale_length'],registry_entry=loaded_structure_registry()[original.get('hs_object_id')])
            if after_maps!=source_surface_maps or surfaces.matrix_values(original.matrix_world)!=source_matrix:
                raise RuntimeFailure('SUBDIVISION_SURFACE_EXPORT','Original native source maps or matrix changed')
        restored=True
    reference_report=(sparse_panel_reference_summary(p['panel_reference'],rows) if sparse_diagnostic
                      else panel_reference_summary(p.get('panel_reference'),rows))
    return {'outcome':'subdivision_diagnosed','operation':'hardsurface.subdivision.diagnose','schema_version':'1.0','request_id':p['request_id'],
            'source':source_before,'source_after':source_after,'opened_source':opened,'source_semantics':'SAVED_DISK_V1',
            'target':{'name':original.name,'object_id':original.get('hs_object_id'),'mesh_name':original.data.name,'shared_mesh_users':original.data.users},
            'scene_units':units,'backend':backend,'control_cage':cage,'authored_control_loops':loop_inspection,'levels':rows,'previews':previews,'outputs':outputs,'exported_geometry_bytes':exported_bytes[0],
            'forecast_faces':estimates,'actual_aggregate_faces':actual_aggregate,'limits':LIMITS,
            'reference_shape':reference_report,
            'source_signature_sha256':signature,'source_signature_after_sha256':_mesh_signature(original),
            'blend_save_performed':False,'saved_candidate_modified':False,'source_geometry_modified':False,'source_transforms_modified':False,'temporary_state_restored':restored,
            'elapsed_seconds':time.monotonic()-started,'cpu_threads':p['cpu_threads'],'wall_seconds':p['wall_seconds'],
            **({'surface_export':{'format':surfaces.FORMAT,'status':'pass','levels':surface_levels,'source_maps':source_maps_ref,'calibration':calibration_ref,'exported_bytes':surface_bytes[0],'limits':surfaces.LIMITS,'pinned_old_L2_identity':'pass','original_native_state_preserved':True,'baseline_level':0,'surface_levels':[2,3],'render_performed':False,'authority':surface_binding['authority'],'projection_conditions':('Native render-time projection conditions, recalibrated by calc_matrix_camera' if sparse_diagnostic else 'Recorded old extrinsics; declared unrecorded aspect/shift, calibrated with native calc_matrix_camera; no historical aspect/shift attestation')}} if surface_enabled else {}),
            'acceptance':{'execution':'pass','preservation':'pass','subdivision_sampling':'pass','reference_shape':reference_report['finite_sample_acceptance'],'authored_control_loops':loop_inspection['status'],'visual':'not_run','user_feedback':'not_run'},
            'evaluation_qualification':{'profile':evaluation_profile['mode'],'source_binding':'pass' if source_bound else 'not_qualified','semantic_transport':'pass' if source_bound else 'not_qualified','reference_shape':reference_report['finite_sample_acceptance'] if source_bound else 'diagnostic_only','production_qualification':'not_asserted',
                **({'shape_status':'shape_not_qualified','formal_target_level':2,'g3':'not_run','formal_measurements':reference_report['formal_measurements']} if sparse_diagnostic else {})},
            'limitations':['This tests actual Blender Catmull-Clark only; Autodesk TurboSmooth is not run and equivalence is not claimed',
                           'No automatic surface quality score: inspect paired neutral and reflection views',
                           ('No reference shape was supplied; dimensional change is relative to the control cage, not design tolerance' if 'panel_reference' not in p else 'Reference checks are finite one-sided samples against caller-declared sharp-bore rounded-panel geometry; no certified Hausdorff or approved-design claim'),
                           'Self-intersections, thickness clearance, UV distortion and export-backend equivalence are not tested']}


def _export_geometry(mesh, matrix, job, level, source, local_hash, written, *, semantic_source=None, semantic_domain=None):
    """Bounded indexed evidence, never a saved/modified source or tessellation."""
    from mathutils import Vector
    vertices,edges,polygons=_mesh_arrays(mesh)
    data={'schema_version':'1.0','kind':'subdivision_evaluated_geometry','level':level,
          'source_sha256':source['sha256'],'local_geometry_sha256':local_hash,
          'vertices_coordinate_space':'object_local','vertices_coordinate_units':'m',
          'world_vertices_mm_coordinate_space':'world','world_vertices_mm_coordinate_units':'mm',
          'matrix_world':[[float(x) for x in row] for row in matrix],
          'vertices':vertices,'world_vertices_mm':[[float(x)*1000 for x in matrix@Vector(v)] for v in vertices],
          'edges':edges,'polygons':polygons,
          'loop_triangles':[{'vertices':list(t.vertices),'polygon_index':int(t.polygon_index)} for t in mesh.loop_triangles],
          'loop_triangles_semantics':'Actual Blender analysis/render tessellation of this sampled mesh, separate from real polygons and edges',
          'domain_semantics':'Real mesh vertices, edges and polygons at this native Blender Catmull-Clark sample; no added triangulation edges',
          'index_semantics':'Array position is the index in this level; evaluated indices are not presumed to correspond across levels',
          'derived_triangles_included':True}
    if semantic_source is not None or semantic_domain is not None:
        data['bound_semantic_regions']=sparse_geometry_regions(mesh,semantic_source,semantic_domain,level=level)
    raw=json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
    if len(raw)>LIMITS['geometry_file_bytes'] or written[0]+len(raw)>LIMITS['geometry_aggregate_bytes']:
        raise RuntimeFailure('SUBDIVISION_OUTPUT_LIMIT','Geometry export exceeds independent per-file/aggregate byte bound',bytes=len(raw),already_written=written[0])
    path=checked_path(job/('subdivision-l%d-geometry.json'%level),exists=False)
    with path.open('xb') as stream:stream.write(raw)
    written[0]+=len(raw)
    return descriptor(path)


def sparse_geometry_regions(mesh, source, domain, *, level):
    """Export actual face lineage and source masks, never geometric rematching."""
    from .subdivision_source import evaluated_bore_domain
    if not isinstance(source,dict) or source.get('source_schema')!=SPARSE_SOURCE_SCHEMA or not isinstance(domain,dict):
        raise RuntimeFailure('SUBDIVISION_SPARSE_EXPORT','Sparse geometry export requires bound source and actual evaluated semantic evidence')
    # Revalidate before serialization so detached/tampered caller maps cannot
    # become exported evidence merely because a prior check passed.
    actual=evaluated_bore_domain(mesh,source,level=level)
    if _hash(actual)!=_hash(domain):
        raise RuntimeFailure('SUBDIVISION_SPARSE_EXPORT','Evaluated semantic domain changed before export')
    evidence=source['evidence']
    if evidence['identity_binding_status']!='bound' or not evidence['external_registry_verified']:
        raise RuntimeFailure('SUBDIVISION_SPARSE_EXPORT','Sparse geometry export requires independent registry-bound source authentication')
    return {'schema_version':'sparse-source-regions/1.0','source_schema':SPARSE_SOURCE_SCHEMA,
            'source_binding':copy.deepcopy(evidence['native_binding']),
            'source_evidence_sha256':_hash(evidence),'source_face_count':source['source_face_count'],
            'source_face_ids_by_parent_slot':copy.deepcopy(source['parent_face_ids_by_slot']),
            'source_provenance_by_parent_slot':copy.deepcopy(source['parent_provenance_by_slot']),
            'source_surface_labels_by_parent_slot':list(source['parent_surface_by_slot']),
            'evaluated_face_parent_slots':list(domain['parent_slots']),
            'evaluated_face_surface_labels':list(domain['surface_labels']),
            'bore_source_parent_slots':list(source['bore_parent_slots']),
            'bore_evaluated_face_indices':list(domain['bore_face_indices']),
            'source_control_loops':copy.deepcopy(source['control_loops']),
            'transport':copy.deepcopy(domain['evidence']),
            'mask_semantics':'Bore membership is authenticated authored-parent provenance; other regions are the complete parent provenance rows. No geometric fitting or proximity rematching.',
            'flat_exclusion_mask_qualification':'not_run','formal_shape_qualification':'not_run'}


def validate_panel_reference_domain(reference):
    """Keep distance probes inside the declared sharp-bore/flat-cap domain."""
    w,h=reference['size'];cx,cy=reference['center'];r=reference['corner_radius'];b=reference['edge_bevel']
    lo,hi=reference['z_min'],reference['z_max'];hole=reference['holes'][0];hx,hy=hole['center'];hr=hole['radius']
    if hi<=lo or r>=min(w,h)/2 or b>=min(r,(hi-lo)/2):
        raise c.ContractError('INVALID_REQUEST','panel_reference requires positive thickness, corner radius below half size, and edge roundover below corner radius and half thickness')
    if max(abs(cx)+w/2,abs(cy)+h/2)>100000:
        raise c.ContractError('INVALID_REQUEST','panel_reference outline exceeds the bounded world domain')
    qx=abs(hx-cx)-(w/2-r);qy=abs(hy-cy)-(h/2-r)
    outline_distance=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r
    clearance=-(outline_distance+b+hr)
    if clearance<=1e-6:
        raise c.ContractError('INVALID_REQUEST','The single circular hole must lie wholly inside the flat cap, without touching the outline roundover')
    return {'flat_cap_bore_clearance_mm':clearance,'measurement_domain':'single_sharp_circular_bore_inside_flat_caps_of_axis_aligned_rounded_plate'}


def _authored_metadata_signature(obj):
    return _hash({key:None if obj.get(key) is None else {'type':type(obj.get(key)).__name__,
                  'sha256':hashlib.sha256(str(obj.get(key)).encode()).hexdigest()}
                  for key in ('hs_subdivision_settings','hs_subd_control_loops')})


def inspect_authored_control_loops(obj):
    """Metadata is untrusted evidence to verify, never reference authorization."""
    raw_loops=obj.get('hs_subd_control_loops');raw_settings=obj.get('hs_subdivision_settings')
    if raw_loops is None and raw_settings is None:
        return {'status':'not_applicable','reason':'Source does not declare authored subdivision control-loop metadata'}
    base={'source':'original_object.data_before_clone','metadata_signature_sha256':_authored_metadata_signature(obj),
          'scope':'Original actual edge/crease/valence verification of explicitly authored named loops; no reference approval inferred'}
    try:
        if raw_loops is None or raw_settings is None:raise ValueError('Authored cage requires both hs_subd_control_loops and hs_subdivision_settings')
        if not isinstance(raw_loops,str) or not isinstance(raw_settings,str) or len(raw_loops.encode())>256*1024 or len(raw_settings.encode())>16*1024:
            raise ValueError('Authored metadata must be bounded JSON text')
        declared=c._validate(c.strict_loads(raw_loops),c.array(_LOOP_RECORD,1,32),'$.hs_subd_control_loops')
        settings=c._validate(c.strict_loads(raw_settings),_AUTHORED_SETTINGS,'$.hs_subdivision_settings')
        if any(i>=len(obj.data.vertices) for row in declared for i in row['vertex_indices']):raise ValueError('Control-loop vertex index exceeds source vertex count')
        matches=(len(obj.modifiers)==1 and obj.modifiers[0].type=='SUBSURF' and
                 all(hasattr(obj.modifiers[0],key) and getattr(obj.modifiers[0],key)==value for key,value in settings.items()))
        from .subd_cage_validation import inspect_control_loops
        result=inspect_control_loops(obj)
        result.update(base)
        result['authored_modifier_settings_match']='pass' if matches else 'fail'
        result['declared_settings_sha256']=_hash(settings)
        if not matches:result['status']='fail';result['modifier_failure']='Actual original modifier differs from authored settings metadata'
        return result
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,c.ContractError) as exc:
        return {**base,'status':'fail','failure_kind':'malformed_or_missing_authored_metadata','message':str(exc)}


def inspect_sparse_authored_control_loops(obj, source):
    """Bind persisted declarations to authenticated author cycles and raw edges.

    Null valence is permitted only where the authenticated sparse author declares
    it. Actual edge/crease/valence and opposite-quad continuation are still read;
    a cycle through poles is never relabelled as a regular edge loop.
    """
    base={'source':'original_object.data_before_clone','metadata_signature_sha256':_authored_metadata_signature(obj),
          'scope':'Persisted declarations bound to sparse native authorship, actual edges, creases and valence; no design approval inferred'}
    try:
        evidence=source['evidence'];native_loops=evidence['semantic_control_loops']
        if (source.get('source_schema')!=SPARSE_SOURCE_SCHEMA or evidence['identity_binding_status']!='bound'
                or not evidence['external_registry_verified'] or native_loops['status']!='pass'):
            raise ValueError('Sparse loop inspection requires actual registry-bound sparse native authentication')
        raw_loops=obj.get('hs_subd_control_loops');raw_settings=obj.get('hs_subdivision_settings')
        if not isinstance(raw_loops,str) or not isinstance(raw_settings,str) or len(raw_loops.encode())>256*1024 or len(raw_settings.encode())>16*1024:
            raise ValueError('Sparse authored metadata must be bounded JSON text')
        sparse_record=copy.deepcopy(_LOOP_RECORD)
        sparse_record['properties']['expected_valence']=c.union(c.integer(minimum=1,maximum=32),c.const(None))
        declared=c._validate(c.strict_loads(raw_loops),c.array(sparse_record,1,128),'$.hs_subd_control_loops')
        settings=c._validate(c.strict_loads(raw_settings),_AUTHORED_SETTINGS,'$.hs_subdivision_settings')
        def by_role(rows):
            result={row['role']:row for row in rows}
            if len(result)!=len(rows):raise ValueError('Duplicate sparse loop roles')
            return result
        if _hash(by_role(declared))!=_hash(by_role(source['authored_control_loops'])):
            raise ValueError('Persisted sparse loops differ from authenticated original author declarations')
        if (len(obj.modifiers)!=1 or obj.modifiers[0].type!='SUBSURF'
                or any(not hasattr(obj.modifiers[0],key) or _hash(getattr(obj.modifiers[0],key))!=_hash(value) for key,value in settings.items())):
            raise ValueError('Actual source modifier differs from persisted authored settings')
        _,edges,faces=_mesh_arrays(obj.data)
        edge_lookup={tuple(sorted(edge)):i for i,edge in enumerate(edges)}
        adjacency=defaultdict(set);incident=defaultdict(list)
        for a,b in edges:adjacency[a].add(b);adjacency[b].add(a)
        for face in faces:
            for vertex in face:incident[vertex].append(set(face))
        crease=obj.data.attributes.get('crease_edge')
        if crease is None or crease.domain!='EDGE' or crease.data_type!='FLOAT' or len(crease.data)!=len(edges):
            raise ValueError('Actual complete EDGE FLOAT crease_edge required')
        native_by_role=by_role(native_loops['cycles']);rows=[]
        for loop in source['control_loops']:
            ids=loop['vertex_indices'];role=loop['role'];expected=loop['expected_valence']
            if len(ids)<3 or len(ids)!=len(set(ids)) or any(type(i)is not int or not 0<=i<len(obj.data.vertices) for i in ids):
                raise ValueError('Authenticated sparse cycle has invalid actual vertex indices')
            for a,b in zip(ids,ids[1:]+ids[:1]):
                edge=edge_lookup.get(tuple(sorted((a,b))))
                if edge is None or not math.isclose(float(crease.data[edge].value),float(loop['crease']),rel_tol=0.,abs_tol=1e-7):
                    raise ValueError('Actual sparse cycle edge or crease disagrees with authored cycle')
            valences=Counter(len(adjacency[i]) for i in ids)
            if expected is not None and set(valences)!={expected}:
                raise ValueError('Actual sparse cycle valence disagrees with authored expectation')
            continuation=all(len(incident[v])==4 and all(len(face)==4 and not {ids[(j-1)%len(ids)],ids[(j+1)%len(ids)]}<=face for face in incident[v]) for j,v in enumerate(ids))
            classification='regular_edge_loop' if continuation else 'pole_crossing_edge_cycle'
            if expected is not None and not continuation:
                raise ValueError('Regular sparse control loop lacks opposite-quad continuation')
            native=native_by_role[role];histogram={str(k):v for k,v in valences.items()}
            if (native['status']!='pass' or native['classification']!=classification
                    or native['actual_valence_histogram']!=histogram or native['opposite_quad_edge_continuation']!=continuation):
                raise ValueError('Actual sparse cycle differs from native semantic loop attestation')
            rows.append({**copy.deepcopy(native),'actual_vertex_indices':list(ids),'actual_edges_and_creases':'pass'})
        if set(native_by_role)!={row['role'] for row in rows}:
            raise ValueError('Sparse actual cycles do not fully cover native author roles')
        return {**base,'status':'pass','cycles':rows,'declared_settings_sha256':_hash(settings),
                'authored_modifier_settings_match':'pass','persisted_declarations_match':'pass',
                'native_authorship_bound':True,'source_binding_sha256':_hash(evidence['native_binding'])}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,c.ContractError) as exc:
        return {**base,'status':'fail','failure_kind':'malformed_or_unbound_sparse_authored_metadata','message':str(exc)}


def measure_against_panel(mesh, matrix, reference, *, semantic_domain=None):
    from mathutils import Vector
    from .subd_panel_measure import measure_panel
    vertices,_,faces=_mesh_arrays(mesh)
    world_mm=[[float(x)*1000 for x in matrix@Vector(v)] for v in vertices]
    try:
        arguments=({'bore_face_indices':semantic_domain['bore_face_indices'],'measurement_profile':'semantic_bore_v1'} if semantic_domain is not None else {})
        result=measure_panel(world_mm,faces,reference,tolerance_mm=reference['tolerance_mm'],**arguments)
        return {**result,'reference_sha256':_hash(reference),'coordinate_space':'world','length_unit':'mm',
                'source_bound_semantics':semantic_domain['evidence'] if semantic_domain is not None else {'status':'not_qualified','reason':'legacy geometry-only diagnostic'},
                'direction':'finite_mesh_samples_to_declared_reference','continuous_hausdorff_certified':False,
                'reference_approval':'not_asserted_caller_supplied_parameters_only'}
    except RuntimeFailure as exc:
        return {'status':'fail','failure_kind':'unsupported_measurement_domain','error_code':exc.code,'message':str(exc),
                'reference_sha256':_hash(reference),'continuous_hausdorff_certified':False,
                'reference_approval':'not_asserted_caller_supplied_parameters_only'}


def panel_reference_summary(reference, rows):
    if reference is None:
        return {'status':'not_supplied','shape_error':'not_computed','design_acceptance':'not_assigned','finite_sample_acceptance':'not_run'}
    results=[{'level':row['level'],'status':row['reference_check']['status']} for row in rows]
    accepted=bool(results) and all(row['status']=='pass' for row in results)
    return {'status':'caller_supplied','parameters':copy.deepcopy(reference),'reference_sha256':_hash(reference),
            'domain_validation':validate_panel_reference_domain(reference),'sampled_levels':results,
            'finite_sample_acceptance':'pass' if accepted else 'fail','design_acceptance':'not_assigned',
            'reference_approval':'not_asserted_caller_supplied_parameters_only','continuous_hausdorff_certified':False,
            'scope':'Finite one-sided mesh samples; a failed level does not fail execution or hide successful level measurements'}


def sparse_panel_reference_summary(reference, rows):
    """Keep L2 limited diagnostics separate from unexecuted formal G3 tests."""
    samples=[]
    for row in rows:
        result=row['reference_check'];witness=result.get('feature_witnesses',{})
        samples.append({'level':row['level'],
            'sample_role':'raw_control_baseline' if row['level']==0 else 'formal_target_with_limited_diagnostics' if row['level']==2 else 'diagnostic_comparison_only',
            'finite_diagnostic_status':result['status'],
            'finite_proximity':result.get('proximity_status','not_run'),
            'bbox':witness.get('bbox_status','not_run'),
            'volume_screen':witness.get('volume_screen_status','not_run'),
            'vertex_station_screen':{name:item['status'] for name,item in result.get('hole_slices',{}).items()},
            'formal_shape_qualification':'not_run'})
    l2=next((row for row in samples if row['level']==2),None)
    if l2 is None:
        raise RuntimeFailure('SUBDIVISION_SPARSE_REPORT','Sparse diagnostic report requires its explicit L2 target sample')
    formal={
        'bore_radial_ray_sections':{'status':'not_run','numeric_obligations':'caller_frozen_contract_not_bound',
                                   'reason':'Independent approved ray counts and section heights are not supplied or bound by this API'},
        'independent_bore_diameter':{'status':'not_run','nominal_diameter_mm':2*reference['holes'][0]['radius']},
        'independent_bore_position':{'status':'not_run','nominal_center_mm':list(reference['holes'][0]['center'])},
        'outer_corner_radius':{'status':'not_run','nominal_radius_mm':reference['corner_radius']},
        'edge_roundover_radius':{'status':'not_run','nominal_radius_mm':reference['edge_bevel']},
        'flatness_with_exclusion_mask':{'status':'not_run','numeric_obligations':'caller_frozen_contract_not_bound',
                                        'mask_status':'not_run','reason':'Independent flatness tolerance and exclusion distances are not bound by this API'},
        'silhouette_deviation':{'status':'not_run','numeric_obligations':'caller_frozen_contract_not_bound',
                                'frozen_view_mask_status':'not_run','reason':'Approved views, masks and pixel tolerance are not bound by this API'},
        'g3_overall':{'status':'not_run','shape_status':'shape_not_qualified'},
    }
    for name,row in formal.items():
        row.setdefault('numeric_obligations','caller_frozen_contract_not_bound')
        row.setdefault('reason','Independent formal measurement and its frozen acceptance contract are not implemented or bound by this API')
        if name in ('independent_bore_diameter','independent_bore_position','outer_corner_radius','edge_roundover_radius'):
            row['nominal_parameter_source']='caller_panel_reference_only'
    return {'status':'caller_supplied','parameters':copy.deepcopy(reference),'reference_sha256':_hash(reference),
            'domain_validation':validate_panel_reference_domain(reference),'sampled_levels':samples,
            'formal_target_level':2,'control_baseline_level':0,'diagnostic_comparison_levels':[1,3],
            'l2_limited_diagnostics':l2,'finite_sample_acceptance':'limited_diagnostic_only',
            'formal_measurements':formal,'shape_status':'shape_not_qualified','design_acceptance':'not_assigned',
            'reference_approval':'not_asserted_caller_supplied_parameters_only','continuous_hausdorff_certified':False,
            'scope':'Finite one-sided proximity, bounding box, volume and vertex-station screens only. No all-level acceptance conjunction; L0/L1/L3 do not determine L2 qualification. Failed measurements do not fail execution.'}
