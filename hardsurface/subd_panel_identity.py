# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure author-time identities for the bounded single-sharp-hole cage.

Logical addresses originate at construction branches. Coordinates only validate
explicit same-ID seams; they never select identity. This is not native evidence,
reference approval, or production qualification.
"""
from collections import defaultdict
from copy import deepcopy
import math

from .quad_patches import MeshBuilder, QuadPatchError, _point
from .io import RuntimeFailure
from .structure_kernel import fingerprint

SCHEMA_VERSION = '1.0'
SCHEDULE_REVISION = 'single_sharp_panel_ids_v1'
# Pinned oriented logical graph and authored crease schedule for this revision.
# Neither value encodes coordinates, raw indices, feature IDs or traversal order.
SCHEDULE_CONNECTIVITY_SHA256 = '7d81f299282121a00ebc0f3ddad1b6ae6c207511d5090e29dd4538d1f88e5b96'
SCHEDULE_CREASE_SHA256 = '5351c601fb4c7561df5b053401423fb18d1b83e01dc73f0f6aaebbc9226285af'
PROFILE_KNOTS = (['bottom_flat_guard'] + ['lower_roundover/j%d'%j for j in range(4)] +
                 ['lower_wall_guard','upper_wall_guard'] +
                 ['upper_roundover/j%d'%j for j in range(3,-1,-1)] + ['top_flat_guard'])
SUPPORTED_EDIT_DATUM = {
    'coordinate_space': 'object_local_mm',
    'hole_center_radius': 'fixed_local_patch_bounds',
    'z_range': ['fixed_bottom', 'fixed_midplane'],
    'selection': 'explicit_required_for_thickness_edit',
    'reference_approval': 'not_implied',
}


def _fail(code, message, **details):
    raise RuntimeFailure(code, message, **details)


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        _fail('AUTHORED_ID_INVALID', 'Semantic IDs must be nonempty strings of at most 200 characters')
    return value


class AuthoredPoint(tuple):
    """A coordinate with an explicit logical address, retained through winding."""
    def __new__(cls, coordinates, semantic_id):
        obj = super().__new__(cls, coordinates)
        obj.semantic_id = _id(semantic_id)
        return obj

    def __getnewargs__(self):
        return tuple(self),self.semantic_id


def ring_points(points, name):
    return [AuthoredPoint(p, '%s/slot:%d' % (name, i)) for i, p in enumerate(points)]


def grid_id(x, y):
    return 'grid/x:%d/y:%d' % (x, y)


def band_id(side, sample, layer):
    return 'band/side:%d/sample:%d/layer:%d' % (side, sample, layer)


def planar_aliases():
    aliases = {}
    # Fixed frame and macroblocks are one authored seam, not coincident vertices.
    for i, (x, y) in enumerate(((2,2),(3,2),(4,2),(4,3),(4,4),(3,4),(2,4),(2,3))):
        aliases['fixed_frame/slot:%d' % i] = grid_id(x, y)
    for side in range(4):
        for k in range(7):
            x, y = ((k,0),(6,k),(6-k,6),(0,6-k))[side]
            aliases[band_id(side,k,0)] = grid_id(x,y)
        for name, layer, last in [('fine',2,6),('coarse',1,2)]:
            aliases['corner/side:%d/%s:0' % (side,name)] = band_id(side,6,layer)
            aliases['corner/side:%d/%s:%d' % (side,name,last)] = band_id((side+1)%4,0,layer)
        x,y=((6,0),(6,6),(0,6),(0,0))[side]
        aliases['corner/side:%d/center' % side] = grid_id(x,y)
    return aliases


def resolve_alias(semantic_id, aliases):
    _id(semantic_id)
    seen = set()
    while semantic_id in aliases:
        if semantic_id in seen:
            _fail('SEMANTIC_ALIAS_CYCLE', 'Authored alias declarations contain a cycle')
        seen.add(semantic_id)
        semantic_id = _id(aliases[semantic_id])
    return semantic_id


def outline_tokens():
    return [token for side in range(4) for token in
            ([band_id(side,k,2) for k in range(6)] +
             ['corner/side:%d/fine:%d' % (side,j) for j in range(6)])]


class AuthoredMeshBuilder(MeshBuilder):
    """Opt-in fail-closed builder; legacy builders and their behavior are untouched."""
    def __init__(self, *, aliases=None, **kwargs):
        super().__init__(**kwargs)
        self.aliases = dict(aliases or {})
        for key in self.aliases:
            _id(key)
            if self.aliases[key] == key:
                _fail('SEMANTIC_ALIAS_CYCLE', 'Self aliases are not valid')
            resolve_alias(key, self.aliases)
        self.vertex_map = {}
        self.face_map = {}
        self._used_aliases = set()

    def vertex(self, point, seam=None, *, semantic_id=None):
        if seam is not None:
            _fail('SEMANTIC_SEAM_INVALID', 'Use declared logical aliases, not legacy seam arguments')
        semantic_id = _id(semantic_id)
        canonical = resolve_alias(semantic_id, self.aliases)
        if semantic_id in self.aliases:
            self._used_aliases.add(semantic_id)
        p = _point(point, 3)
        if canonical in self.vertex_map:
            index = self.vertex_map[canonical]
            distance = math.dist(p, self.vertices[index])
            if distance > self.stitch_tolerance_mm:
                _fail('SEMANTIC_SEAM_MISMATCH', 'One logical vertex has inconsistent positions', semantic_id=canonical, distance_mm=distance)
            self.max_stitch_error_mm = max(distance, self.max_stitch_error_mm)
            return index
        key = self._bucket(p)
        # Spatial bins here are a rejection guard only, never an identity lookup.
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for dz in (-1,0,1):
                    for i in self._buckets.get((key[0]+dx,key[1]+dy,key[2]+dz), ()):
                        if math.dist(p, self.vertices[i]) <= self.near_seam_mm:
                            _fail('SEMANTIC_SEAM_UNDECLARED', 'Distinct logical vertices touch without an explicit alias', semantic_id=canonical)
        if len(self.vertices) >= self.max_vertices:
            raise QuadPatchError('vertex_budget', 'Vertex budget exceeded')
        index = len(self.vertices)
        self.vertices.append(p)
        self._buckets[key].append(index)
        self._exact[p] = index
        self.vertex_map[canonical] = index
        return index

    def loop(self, points, seam_prefix=None, *, semantic_ids=None):
        if seam_prefix is not None or semantic_ids is None or len(points) != len(semantic_ids):
            _fail('AUTHORED_LOOP_INVALID', 'An authored loop needs exactly one logical ID per point')
        out = [self.vertex(p, semantic_id=s) for p,s in zip(points,semantic_ids)]
        if len(out)<4 or len(set(out)) != len(out):
            _fail('AUTHORED_LOOP_INVALID', 'An authored loop must have at least four distinct logical vertices')
        return out

    def quad(self, a,b,c,d, provenance=None, flip=False, *, semantic_id=None):
        semantic_id = _id(semantic_id)
        if semantic_id in self.face_map:
            _fail('SEMANTIC_FACE_DUPLICATE', 'Logical face ID was emitted more than once', semantic_id=semantic_id)
        index = super().quad(a,b,c,d,provenance=provenance,flip=flip)
        self.face_map[semantic_id] = index
        return index

    def loft(self, loop_a,loop_b,provenance=None,flip=False, *, face_ids=None):
        if len(loop_a)!=len(loop_b) or face_ids is None or len(face_ids)!=len(loop_a):
            _fail('AUTHORED_LOFT_INVALID', 'Authored loft requires equal rings and one face ID per sector')
        return [self.quad(loop_a[i],loop_a[(i+1)%len(loop_a)],loop_b[(i+1)%len(loop_b)],loop_b[i],
                          provenance=provenance,flip=flip,semantic_id=face_ids[i]) for i in range(len(loop_a))]


def parameter_binding(parameters):
    # Full caller parameters are retained exactly. This is independent of geometry
    # and permits fail-closed comparison of previously unknown future fields.
    if parameters.get('topology_strategy')=='sparse_control_cage':
        from .sparse_parameter_edit import parameter_binding as sparse_binding
        return sparse_binding(parameters)
    original = deepcopy(parameters)
    return {'constructor':'quad.panel/subd_control_cage', 'parameters':original,
            'design_sha256':fingerprint(original), 'coordinate_space':'object_local_mm'}


def _canonical_cycle(seq):
    # Cyclic start is storage detail; reversal remains a real topology difference.
    seq=list(seq)
    return min(seq[i:]+seq[:i] for i in range(len(seq)))


def semantic_connectivity(mesh, authored):
    inverse = {i:s for s,i in authored['vertex_map'].items()}
    return {s:_canonical_cycle([inverse[i] for i in mesh['faces'][index]])
            for s,index in authored['face_map'].items()}


def semantic_creases(mesh, authored):
    inverse = {i:s for s,i in authored['vertex_map'].items()}
    rows=[]
    for a,b,value in mesh.get('edge_creases',[]):
        if value: rows.append([*sorted((inverse[a],inverse[b])),value])
    return sorted(rows)


def authorship_payload(authored):
    """Index/order independent manifest; geometry is deliberately a separate hash."""
    keys=('schema_version','schedule_revision','semantic_control_loops','parameter_binding',
          'supported_edit_datum','alias_manifest','semantic_connectivity_sha256','semantic_crease_sha256','edit_dependencies')
    return {**{k:authored[k] for k in keys},'vertex_ids':sorted(authored['vertex_map']),
            'face_ids':sorted(authored['face_map'])}


def expected_control_loops():
    rows=[]
    for side in ('bottom','top'):
        for ring,crease in [('rim',1.),('support',0.)]:
            rows.append({'role':'bore_'+ring+'_'+side,
                         'vertex_ids':['plane/%s/bore/%s/slot:%d'%(side,ring,i) for i in range(24)],
                         'crease':crease,'expected_valence':4})
    for knot in PROFILE_KNOTS:
        rows.append({'role':knot,'vertex_ids':['profile/%s/slot:%d'%(knot,i) for i in range(48)],
                     'crease':0.,'expected_valence':4})
    return rows


def edit_dependencies(mesh, authored):
    """Exact author-schedule write sets; never derived from observed differences."""
    ids=set(authored['vertex_map']);connectivity=semantic_connectivity(mesh,authored)
    hole=sorted(s for s in ids if any(s.startswith('plane/'+side+'/bore/') for side in ('bottom','top')))
    upper=sorted(s for s in ids if s.startswith('plane/top/') or s.startswith('profile/upper_') or s.startswith('profile/top_flat_guard/'))
    def dependency(vertices,axes):
        selected=set(vertices)
        return {'vertex_ids':vertices,'allowed_axes':axes,
                'face_ids':sorted(s for s,vs in connectivity.items() if selected.intersection(vs)),
                'semantic_control_loop_roles':[row['role'] for row in expected_control_loops() if selected.intersection(row['vertex_ids'])]}
    result={'holes[0].center[0]':dependency(hole,[0,1]),
            'holes[0].center[1]':dependency(hole,[0,1]),
            'holes[0].radius':dependency(hole,[0,1]),
            'z_range.fixed_bottom':dependency(upper,[2]),
            'z_range.fixed_midplane':dependency(sorted(ids),[2])}
    parameters=authored['parameter_binding']['parameters']
    if 'bore_support_width' in parameters.get('subdivision_cage',{}):
        # Canonical planar band/corner addresses and profile slots are authored
        # before an edit. Hole, fixed-frame and grid addresses are never targets.
        outer=sorted(s for s in ids if s.startswith('profile/') or
                     any(s.startswith('plane/'+side+'/'+kind+'/')
                         for side in ('bottom','top') for kind in ('band','corner')))
        result['edge_bevel']=dependency(outer,[0,1,2])
    return result


def authored_manifest(mesh, builder, control_loops, parameters):
    result={'schema_version':SCHEMA_VERSION,'schedule_revision':SCHEDULE_REVISION,
            'vertex_map':dict(builder.vertex_map),'face_map':dict(builder.face_map),
            'semantic_control_loops':deepcopy(control_loops),'parameter_binding':parameter_binding(parameters),
            'supported_edit_datum':deepcopy(SUPPORTED_EDIT_DATUM),
            'alias_manifest':dict(builder.aliases)}
    result['semantic_connectivity_sha256']=fingerprint(semantic_connectivity(mesh,result))
    result['semantic_crease_sha256']=fingerprint(semantic_creases(mesh,result))
    result['edit_dependencies']=edit_dependencies(mesh,result)
    result['authorship_sha256']=fingerprint(authorship_payload(result))
    validate_authored_identity(mesh,result)
    return result


def validate_authored_identity(mesh, authored=None):
    """Validate maps/aliases, real loops, winding fingerprint and exact bindings.

    This operates on supplied arrays. It cannot establish that arrays came from
    Blender; the independent native adapter owns that claim.
    """
    authored=mesh.get('authored_structure') if authored is None else authored
    if not isinstance(authored,dict):
        _fail('STRUCTURE_IDENTITY_MISSING','Authored semantic identity is required')
    if authored.get('schedule_revision')=='sparse_sharp_panel_ids_v2':
        from .sparse_panel_geometry import validate_sparse_authored_identity
        return validate_sparse_authored_identity(mesh,authored)
    required={'schema_version','schedule_revision','vertex_map','face_map','semantic_control_loops',
              'parameter_binding','supported_edit_datum','alias_manifest','semantic_connectivity_sha256',
              'semantic_crease_sha256','edit_dependencies','authorship_sha256'}
    if set(authored)!=required:
        _fail('AUTHORED_MANIFEST_INVALID','Authored manifest fields differ from the exact schema')
    if authored.get('schema_version')!=SCHEMA_VERSION or authored.get('schedule_revision')!=SCHEDULE_REVISION:
        _fail('AUTHORED_SCHEDULE_UNSUPPORTED','Only the exact bounded author schedule is supported')
    vertices=mesh.get('vertices_mm',mesh.get('vertices'))
    faces=mesh.get('faces')
    if not isinstance(vertices,(list,tuple)) or not isinstance(faces,(list,tuple)):
        _fail('AUTHORED_MESH_INVALID','Expected vertex and face arrays')
    for name,count in [('vertex_map',len(vertices)),('face_map',len(faces))]:
        mapping=authored.get(name)
        if not isinstance(mapping,dict) or len(mapping)!=count:
            _fail('AUTHORED_MAP_INVALID','Semantic maps must cover arrays exactly', mapping=name)
        for key,value in mapping.items():
            _id(key)
            if type(value) is not int or not 0<=value<count:
                _fail('AUTHORED_MAP_INVALID','Semantic map index is invalid',mapping=name)
        if set(mapping.values())!=set(range(count)):
            _fail('AUTHORED_MAP_INVALID','Semantic maps must be bijective',mapping=name)
    if set(authored['vertex_map'])&set(authored['face_map']):
        _fail('AUTHORED_MAP_INVALID','Vertex and face logical namespaces must be disjoint')
    for v in vertices:
        if not isinstance(v,(tuple,list)) or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):
            _fail('AUTHORED_MESH_INVALID','Vertices must have three finite coordinates')
    aliases=authored.get('alias_manifest')
    expected_aliases={f'plane/{side}/{a}':f'plane/{side}/{b}' for side in ('bottom','top') for a,b in planar_aliases().items()}
    if aliases!=expected_aliases:
        _fail('AUTHORED_ALIAS_INVALID','Alias table differs from the exact author schedule')
    for key in aliases:
        if key in authored['vertex_map'] or resolve_alias(key,aliases) not in authored['vertex_map']:
            _fail('AUTHORED_ALIAS_INVALID','Aliases must resolve to existing canonical vertices only')
    adjacency=defaultdict(set);edges=set()
    for f in faces:
        if not isinstance(f,(tuple,list)) or len(f)!=4 or any(type(i)is not int or not 0<=i<len(vertices) for i in f) or len(set(f))!=4:
            _fail('AUTHORED_MESH_INVALID','All authored faces must be valid quads')
        for a,b in zip(f,list(f[1:])+[f[0]]):
            adjacency[a].add(b);adjacency[b].add(a);edges.add(tuple(sorted((a,b))))
    if set(adjacency)!=set(range(len(vertices))):
        _fail('AUTHORED_MESH_INVALID','Loose authored vertices are not allowed')
    creases={}
    for row in mesh.get('edge_creases',[]):
        if not isinstance(row,(tuple,list)) or len(row)!=3:
            _fail('AUTHORED_CREASE_INVALID','Crease rows need endpoints and weight')
        a,b,value=row
        if type(a)is not int or type(b)is not int or tuple(sorted((a,b))) not in edges or type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
            _fail('AUTHORED_CREASE_INVALID','Crease must bind a real edge with weight in [0,1]')
        edge=tuple(sorted((a,b)))
        if edge in creases: _fail('AUTHORED_CREASE_INVALID','Duplicate crease edge')
        creases[edge]=value
    roles=set();loops=authored.get('semantic_control_loops')
    if loops!=expected_control_loops(): _fail('AUTHORED_LOOP_INVALID','Semantic control loop schedule differs from the exact authored revision')
    for loop in loops:
        if not isinstance(loop,dict) or set(loop)!={'role','vertex_ids','crease','expected_valence'}:
            _fail('AUTHORED_LOOP_INVALID','Control loop fields are invalid')
        role=_id(loop['role']);ids=loop['vertex_ids']
        if role in roles or not isinstance(ids,list) or len(ids)<4 or len(set(ids))!=len(ids) or any(i not in authored['vertex_map'] for i in ids):
            _fail('AUTHORED_LOOP_INVALID','Control loop roles and vertex IDs must be unique')
        roles.add(role);indices=[authored['vertex_map'][s] for s in ids]
        for a,b in zip(indices,indices[1:]+indices[:1]):
            if b not in adjacency[a] or len(adjacency[a])!=loop['expected_valence'] or creases.get(tuple(sorted((a,b))),0)!=loop['crease']:
                _fail('AUTHORED_LOOP_INVALID','Control loop edge, valence or crease differs from actual mesh',role=role)
    binding=authored.get('parameter_binding',{})
    if not isinstance(binding,dict) or set(binding)!={'constructor','parameters','design_sha256','coordinate_space'}:
        _fail('AUTHORED_PARAMETER_BINDING_INVALID','Exact parameter binding fields are required')
    if (binding.get('constructor')!='quad.panel/subd_control_cage' or binding.get('coordinate_space')!='object_local_mm' or
        not isinstance(binding.get('parameters'),dict) or binding.get('design_sha256')!=fingerprint(binding['parameters'])):
        _fail('AUTHORED_PARAMETER_BINDING_INVALID','Exact original parameter fingerprint is invalid')
    if authored.get('supported_edit_datum')!=SUPPORTED_EDIT_DATUM:
        _fail('AUTHORED_DATUM_INVALID','Supported datum declaration differs from the author schedule')
    if authored.get('semantic_connectivity_sha256')!=SCHEDULE_CONNECTIVITY_SHA256 or authored.get('semantic_connectivity_sha256')!=fingerprint(semantic_connectivity(mesh,authored)):
        _fail('AUTHORED_CONNECTIVITY_CHANGED','Actual semantic oriented connectivity differs from the author manifest')
    if authored.get('semantic_crease_sha256')!=SCHEDULE_CREASE_SHA256 or authored.get('semantic_crease_sha256')!=fingerprint(semantic_creases(mesh,authored)):
        _fail('AUTHORED_CREASE_CHANGED','Actual semantic crease weights differ from the author manifest')
    if authored.get('edit_dependencies')!=edit_dependencies(mesh,authored):
        _fail('AUTHORED_DEPENDENCY_INVALID','Parameter dependency sets differ from the authored schedule')
    if authored.get('authorship_sha256')!=fingerprint(authorship_payload(authored)):
        _fail('AUTHORSHIP_SHA_MISMATCH','Authored manifest fingerprint is invalid')
    cfg=binding['parameters'].get('subdivision_cage',{})
    if isinstance(cfg,dict) and any(key in cfg for key in ('bore_support_width','outer_join_policy')):
        # Native adapters call this schedule validator as well. Fixed-width
        # radius/layout evidence therefore cannot be supplied by manifest JSON
        # alone, even outside the edit-verification entry point.
        from .structure_edit import _parameter_domain,_verify_parameter_coordinates
        from .structure_kernel import StructureError
        try:
            _parameter_domain(binding['parameters'])
            _verify_parameter_coordinates({'vertices_mm':vertices,'authored_structure':authored})
        except StructureError as exc:
            _fail(exc.code,str(exc),**getattr(exc,'details',{}))
    return {'status':'pass','vertices':len(vertices),'faces':len(faces),'control_loops':len(loops),
            'authorship_sha256':authored['authorship_sha256'],'native_extraction':'not_run'}


def plan_parameter_edit(before, after_parameters, *, datum=None):
    """Determine the complete authorized geometric write-set BEFORE rebuilding.

    The caller still owns user approval. Datum must explicitly be fixed_bottom
    or fixed_midplane for any z-range change; neither is inferred from thickness.
    """
    if before.get('authored_structure',{}).get('schedule_revision')=='sparse_sharp_panel_ids_v2':
        from .sparse_parameter_edit import plan_parameter_edit as sparse_plan
        return sparse_plan(before,after_parameters,datum=datum)
    validate_authored_identity(before)
    authored=before['authored_structure'];old=authored['parameter_binding']['parameters'];new=deepcopy(after_parameters)
    if not isinstance(new,dict):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','After parameters must be an object')
    old_cfg,new_cfg=old.get('subdivision_cage',{}),new.get('subdivision_cage',{})
    if not isinstance(old_cfg,dict) or not isinstance(new_cfg,dict):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','subdivision_cage must retain its parameter object')
    if old_cfg.get('outer_join_policy')!=new_cfg.get('outer_join_policy'):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Changing outer_join_policy requires a separately approved technical-policy migration')
    a,b=deepcopy(old),deepcopy(new)
    a.pop('edit_datum',None);b.pop('edit_datum',None)
    try:
        old_hole=a['holes'][0];new_hole=b['holes'][0]
        hole_changed=(old_hole['center']!=new_hole['center'] or old_hole['radius']!=new_hole['radius'])
        z_changed=(a['z_min']!=b['z_min'] or a['z_max']!=b['z_max'])
        bevel_changed=a['edge_bevel']!=b['edge_bevel']
        if bevel_changed and 'bore_support_width' in a.get('subdivision_cage',{}):
            b['edge_bevel']=a['edge_bevel']
        new_hole['center']=deepcopy(old_hole['center']);new_hole['radius']=old_hole['radius']
        b['z_min']=a['z_min'];b['z_max']=a['z_max']
    except (KeyError,IndexError,TypeError):
        _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Edit must retain the complete original single-hole parameter contract')
    if a!=b: _fail('AUTHORED_EDIT_SCOPE_UNSUPPORTED','Only hole X/Y/radius, explicit-datum z range and fixed-bore-support outer roundover are supported')
    from .quad_panel_subd import plan_subd_cage
    try:
        plan_subd_cage(new)  # Validate finite values and the unchanged bounded geometric domain.
    except (KeyError,IndexError,TypeError,ValueError,OverflowError) as exc:
        _fail('AUTHORED_EDIT_PARAMETER_INVALID','New parameters do not form a valid bounded cage contract',reason=str(exc))
    if datum is not None and datum not in ('fixed_bottom','fixed_midplane'):
        _fail('AUTHORED_EDIT_DATUM_REQUIRED','Unsupported thickness datum')
    if z_changed:
        if datum is None: _fail('AUTHORED_EDIT_DATUM_REQUIRED','Thickness editing requires an explicit datum')
        if datum=='fixed_bottom' and old['z_min']!=new['z_min']:
            _fail('AUTHORED_EDIT_DATUM_MISMATCH','fixed_bottom requires unchanged z_min')
        if datum=='fixed_midplane' and old['z_min']+old['z_max']!=new['z_min']+new['z_max']:
            _fail('AUTHORED_EDIT_DATUM_MISMATCH','fixed_midplane requires exactly unchanged midpoint')
    allowed_axes={}
    active=[]
    old_hole,new_hole=old['holes'][0],new['holes'][0]
    for axis in (0,1):
        if old_hole['center'][axis]!=new_hole['center'][axis]:active.append('holes[0].center[%d]'%axis)
    if old_hole['radius']!=new_hole['radius']:active.append('holes[0].radius')
    if z_changed:active.append('z_range.'+datum)
    if bevel_changed:active.append('edge_bevel')
    for path in active:
        dependency=authored['edit_dependencies'][path]
        for semantic_id in dependency['vertex_ids']:
            axes=([0,1] if path=='edge_bevel' and semantic_id.startswith('plane/') else dependency['allowed_axes'])
            allowed_axes[semantic_id]=sorted(set(allowed_axes.get(semantic_id,[]))|set(axes))
    connectivity=semantic_connectivity(before,authored);selected=set(allowed_axes)
    face_ids=sorted(s for s,vs in connectivity.items() if selected.intersection(vs))
    return {'schema_version':SCHEMA_VERSION,'schedule_revision':SCHEDULE_REVISION,
            'before_authorship_sha256':authored['authorship_sha256'],'after_parameter_binding':parameter_binding(new),
            'datum':datum,'hole_changed':hole_changed,'z_range_changed':z_changed,
            'allowed_vertex_axes':allowed_axes,'affected_face_ids':face_ids,
            'semantic_control_loop_roles':[row['role'] for row in authored['semantic_control_loops'] if selected.intersection(row['vertex_ids'])],
            'topology_policy':'preserve','downstream_selections':'invalidate_and_resolve_again',
            'qualification':'host_scope_only; no_reference_or_native_approval'}


def verify_parameter_edit(before, after, contract):
    """Check actual arrays against a predeclared edit, never widen its write-set."""
    validate_authored_identity(before);validate_authored_identity(after)
    aa=before['authored_structure'];bb=after['authored_structure']
    expected=plan_parameter_edit(before,bb['parameter_binding']['parameters'],datum=contract.get('datum'))
    if contract!=expected: _fail('AUTHORED_EDIT_CONTRACT_MISMATCH','Edit contract is stale or write-set was altered')
    if set(aa['vertex_map'])!=set(bb['vertex_map']) or set(aa['face_map'])!=set(bb['face_map']):
        _fail('AUTHORED_EDIT_TOPOLOGY_CHANGED','Edit changed the logical entity sets')
    if semantic_connectivity(before,aa)!=semantic_connectivity(after,bb) or semantic_creases(before,aa)!=semantic_creases(after,bb):
        _fail('AUTHORED_EDIT_TOPOLOGY_CHANGED','Edit changed oriented connectivity or crease attributes')
    if aa['semantic_control_loops']!=bb['semantic_control_loops']:
        _fail('AUTHORED_EDIT_TOPOLOGY_CHANGED','Edit changed semantic loop roles or order')
    changed=[]
    for s,index in aa['vertex_map'].items():
        old=before['vertices_mm'][index];new=after['vertices_mm'][bb['vertex_map'][s]]
        axes=contract['allowed_vertex_axes'].get(s,[])
        if any(old[k]!=new[k] for k in range(3) if k not in axes):
            _fail('AUTHORED_EDIT_OUTSIDE_SCOPE','A protected coordinate changed',semantic_id=s)
        if old!=new:changed.append(s)
    # A permitted address is not permission for arbitrary target coordinates.
    # Recompute the analytic binding independently of constructor output.
    from .structure_edit import _verify_parameter_coordinates
    from .structure_kernel import StructureError
    try:
        _verify_parameter_coordinates(before);_verify_parameter_coordinates(after)
    except StructureError as exc:
        _fail(exc.code,str(exc),**getattr(exc,'details',{}))
    return {'status':'pass','changed_vertex_ids':sorted(changed),
            'lineage':{'vertices':[{'old_id':s,'new_id':s,'relation':'continue'} for s in sorted(aa['vertex_map'])],
                       'faces':[{'old_id':s,'new_id':s,'relation':'continue'} for s in sorted(aa['face_map'])]},
            'downstream_selections':'invalidate_and_resolve_again','native_extraction':'not_run'}
