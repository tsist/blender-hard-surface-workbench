"""Deterministic feature-local all-quad patches, in millimetres.

These primitives plan topology before emission; they never triangulate, dissolve,
beautify, Boolean, or repair a mesh after the fact.  Rectangular tiles share an
explicit edge subdivision schedule, so a hole ring cannot leave T junctions.
A failed operation can have appended geometry: discard the builder on failure.
"""
from __future__ import annotations

import math
from collections import defaultdict

TAU = 2.0 * math.pi


class QuadPatchError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise QuadPatchError('invalid_parameter', name + ' must be positive and finite')
    return float(value)


def _integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise QuadPatchError('invalid_budget', name + ' must be an integer >= ' + str(minimum))
    return value


def _point(value, dimensions):
    if len(value) != dimensions or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise QuadPatchError('invalid_geometry', 'Expected finite coordinates')
    return tuple(float(x) for x in value)


def _bounds(value):
    x0, y0, x1, y1 = _point(value, 4)
    if x1 <= x0 or y1 <= y0:
        raise QuadPatchError('invalid_bounds', 'Bounds are (xmin, ymin, xmax, ymax) with positive extents')
    return x0, y0, x1, y1


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _lerp(a, b, t):
    if t == 0:
        return tuple(a)
    if t == 1:
        return tuple(b)
    return tuple(x+(y-x)*t for x, y in zip(a, b))


class MeshBuilder:
    """Indexed mesh with exact/roundoff seam reuse and bounded allocation.

    Existing coordinates are retained, never averaged. Nonzero distances below
    ``near_seam_mm`` are rejected except machine-roundoff coincidences bounded
    by ``stitch_tolerance_mm``. This prevents a design gap being welded away.
    ``seam`` explicitly identifies a planned common vertex and permits the stated
    stitch tolerance; its mismatch fails instead of changing nominal geometry.
    """
    def __init__(self, stitch_tolerance_mm=1e-9, near_seam_mm=1e-7,
                 max_vertices=500000, max_faces=500000):
        self.stitch_tolerance_mm = _positive(stitch_tolerance_mm, 'stitch tolerance')
        self.near_seam_mm = _positive(near_seam_mm, 'near seam distance')
        if self.near_seam_mm < self.stitch_tolerance_mm:
            raise QuadPatchError('invalid_tolerance', 'Near seam guard must be >= stitch tolerance')
        self.max_vertices = _integer(max_vertices, 'max vertices', 4)
        self.max_faces = _integer(max_faces, 'max faces')
        self.vertices = []
        self.faces = []
        self.face_provenance = []
        self._buckets = defaultdict(list)
        self._exact = {}
        self._seams = {}
        self._face_keys = set()
        self.max_stitch_error_mm = 0.0

    def _bucket(self, p):
        return tuple(math.floor(x/self.near_seam_mm) for x in p)

    def vertex(self, point, seam=None):
        p = _point(point, 3)
        if seam is not None and seam in self._seams:
            index = self._seams[seam]
            distance = math.dist(p, self.vertices[index])
            if distance > self.stitch_tolerance_mm:
                raise QuadPatchError('seam_mismatch', 'Planned seam differs by %.12g mm' % distance)
            self.max_stitch_error_mm = max(distance, self.max_stitch_error_mm)
            return index
        if p in self._exact:
            index = self._exact[p]
            if seam is not None:
                self._seams[seam] = index
            return index
        key = self._bucket(p)
        nearby = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for index in self._buckets.get((key[0]+dx, key[1]+dy, key[2]+dz), ()):
                        d = math.dist(p, self.vertices[index])
                        if d <= self.near_seam_mm:
                            nearby.append((d, index))
        if nearby:
            distance, index = min(nearby)
            roundoff = min(self.stitch_tolerance_mm, 32*math.ulp(max(1.0, *(abs(x) for x in p))))
            if distance > roundoff or len(nearby) > 1:
                raise QuadPatchError('near_coincident_seam', 'Distinct/ambiguous vertices are only %.12g mm apart' % distance)
            self.max_stitch_error_mm = max(distance, self.max_stitch_error_mm)
            self._exact[p] = index
            if seam is not None:
                self._seams[seam] = index
            return index
        if len(self.vertices) >= self.max_vertices:
            raise QuadPatchError('vertex_budget', 'Vertex budget exceeded')
        index = len(self.vertices)
        self.vertices.append(p)
        self._exact[p] = index
        self._buckets[key].append(index)
        if seam is not None:
            self._seams[seam] = index
        return index

    def loop(self, points, seam_prefix=None):
        out = [self.vertex(p, None if seam_prefix is None else (seam_prefix, i)) for i, p in enumerate(points)]
        if len(out) < 4 or len(set(out)) != len(out):
            raise QuadPatchError('invalid_loop', 'Loops need >=4 distinct vertices, without a repeated endpoint')
        return out

    def quad(self, a, b, c, d, provenance=None, flip=False):
        face = (a, d, c, b) if flip else (a, b, c, d)
        if any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(self.vertices) for i in face) or len(set(face)) != 4:
            raise QuadPatchError('invalid_quad', 'A quad needs four distinct existing indices')
        p = [self.vertices[i] for i in face]
        normal = tuple(sum(_cross(_sub(p[i], p[0]), _sub(p[(i+1) % 4], p[0]))[k] for i in range(4)) for k in range(3))
        normal2 = _dot(normal, normal)
        if normal2 <= self.stitch_tolerance_mm**4:
            raise QuadPatchError('degenerate_quad', 'Quad has zero area or crossing edges')
        for i in range(4):
            turn = _cross(_sub(p[(i+1) % 4], p[i]), _sub(p[(i+2) % 4], p[(i+1) % 4]))
            if _dot(turn, normal) <= normal2*1e-12:
                raise QuadPatchError('folded_quad', 'Quad is degenerate, concave, or folded')
        canonical = frozenset(face)
        if canonical in self._face_keys:
            raise QuadPatchError('duplicate_face', 'Duplicate face refused')
        if len(self.faces) >= self.max_faces:
            raise QuadPatchError('face_budget', 'Face budget exceeded')
        self._face_keys.add(canonical)
        self.faces.append(face)
        self.face_provenance.append(dict(provenance or {}))
        return len(self.faces)-1

    def loft(self, loop_a, loop_b, provenance=None, flip=False):
        return loft(self, loop_a, loop_b, provenance=provenance, flip=flip)

    def mesh(self):
        return {'vertices': list(self.vertices), 'faces': list(self.faces),
                'face_provenance': [dict(p) for p in self.face_provenance],
                'coordinate_unit': 'mm', 'max_stitch_error_mm': self.max_stitch_error_mm}


def circle_segments(radius, chord_tolerance_mm=.025, max_segments=512, multiple=4):
    radius = _positive(radius, 'radius')
    tolerance = _positive(chord_tolerance_mm, 'chord tolerance')
    maximum = _integer(max_segments, 'max segments', 4)
    multiple = _integer(multiple, 'segment multiple', 4)
    angle = 4*math.asin(math.sqrt(min(1.0, tolerance/(2*radius))))
    if angle <= 0:
        raise QuadPatchError('sampling_budget', 'Tolerance is below floating point resolution')
    count = max(multiple, math.ceil(TAU/min(angle, math.pi/2)))
    count = ((count+multiple-1)//multiple)*multiple
    if count > maximum:
        raise QuadPatchError('sampling_budget', 'Chord tolerance exceeds maximum segment count')
    return count


def quarter_arc_segments(radius, chord_tolerance_mm, max_segments=512):
    """Quarter-round samples satisfy the same explicit curve error as profiles."""
    return circle_segments(radius,chord_tolerance_mm,max_segments,multiple=4)//4


def sample_circle(center, radius, chord_tolerance_mm=.025, max_segments=512,
                  segments=None, start_angle=-3*math.pi/4):
    center = _point(center, 2)
    radius = _positive(radius, 'radius')
    required = circle_segments(radius, chord_tolerance_mm, max_segments)
    count = required if segments is None else _integer(segments, 'segments', 4)
    if count % 4 or count > max_segments or radius*2*math.sin(math.pi/count/2)**2 > chord_tolerance_mm*(1+1e-12):
        raise QuadPatchError('sampling_budget', 'Circle count must be a multiple of 4 within the chord/count budgets')
    if not math.isfinite(start_angle):
        raise QuadPatchError('invalid_parameter', 'Start angle must be finite')
    return [(center[0]+radius*math.cos(start_angle+TAU*i/count),
             center[1]+radius*math.sin(start_angle+TAU*i/count)) for i in range(count)]


def _rounded_side(bounds, radius, side, t):
    """CCW side: starts at BL/BR/TR/TL diagonal fillet points respectively."""
    x0, y0, x1, y1 = bounds
    hx, hy = (x1-x0)/2, (y1-y0)/2
    cx, cy = (x0+x1)/2, (y0+y1)/2
    if radius == 0:
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return _lerp(corners[side], corners[(side+1) % 4], t)
    horizontal = side % 2 == 0
    half = hx if horizontal else hy
    straight = 2*(half-radius)
    half_arc = math.pi*radius/4
    distance = t*(straight+2*half_arc)
    if distance <= half_arc:
        angle = -3*math.pi/4 + distance/radius
        px = -half+radius + radius*math.cos(angle)
        py = -(hy if horizontal else hx)+radius + radius*math.sin(angle)
    elif distance <= half_arc+straight:
        px = -half+radius + distance-half_arc
        py = -(hy if horizontal else hx)
    else:
        angle = -math.pi/2 + (distance-half_arc-straight)/radius
        px = half-radius + radius*math.cos(angle)
        py = -(hy if horizontal else hx)+radius + radius*math.sin(angle)
    if side == 0:
        return cx+px, cy+py
    if side == 1:
        return cx-py, cy+px
    if side == 2:
        return cx-px, cy-py
    return cx+py, cy-px


def _radius(bounds, radius):
    radius = float(radius)
    if not math.isfinite(radius) or radius < 0 or radius > min(bounds[2]-bounds[0], bounds[3]-bounds[1])/2:
        raise QuadPatchError('invalid_radius', 'Corner radius must fit within the bounds')
    return radius


def sample_rounded_rectangle(bounds, radius, chord_tolerance_mm=.025,
                             max_segments=512, side_segments=None):
    bounds = _bounds(bounds)
    radius = _radius(bounds, radius)
    tolerance = _positive(chord_tolerance_mm, 'chord tolerance')
    maximum = _integer(max_segments, 'max segments', 4)
    if side_segments is None:
        if radius == 0:
            nx = ny = 1
        else:
            # Curvature <= 1/r and segment arclength <= sqrt(8*r*tol)
            # imply the whole segment's chord deviation <= tol, including
            # a segment that crosses an arc/straight tangency.
            step = min(math.sqrt(8*radius*tolerance), math.pi*radius/2)
            nx = max(1, math.ceil((bounds[2]-bounds[0]-2*radius+math.pi*radius/2)/step))
            ny = max(1, math.ceil((bounds[3]-bounds[1]-2*radius+math.pi*radius/2)/step))
    else:
        nx, ny = side_segments
        _integer(nx, 'x side segments')
        _integer(ny, 'y side segments')
        if radius:
            step = math.sqrt(8*radius*tolerance)
            if max((bounds[2]-bounds[0]-2*radius+math.pi*radius/2)/nx,
                   (bounds[3]-bounds[1]-2*radius+math.pi*radius/2)/ny) > step*(1+1e-12):
                raise QuadPatchError('sampling_budget', 'Requested side counts fail conservative chord bound')
    if 2*(nx+ny) > maximum:
        raise QuadPatchError('sampling_budget', 'Rounded rectangle exceeds segment budget')
    return [_rounded_side(bounds, radius, side, j/count)
            for side, count in enumerate((nx, ny, nx, ny)) for j in range(count)]


def sample_rectangle(bounds, x_segments=1, y_segments=1):
    return sample_rounded_rectangle(bounds, 0, max_segments=2*(x_segments+y_segments), side_segments=(x_segments, y_segments))


def loft(builder, loop_a, loop_b, provenance=None, flip=False):
    """Join corresponding CCW rings; a lower->upper loft faces outward."""
    if len(loop_a) != len(loop_b) or len(loop_a) < 4 or len(set(loop_a)) != len(loop_a) or len(set(loop_b)) != len(loop_b):
        raise QuadPatchError('loop_count_mismatch', 'Loft loops must have equal distinct vertex counts >= 4')
    begin = len(builder.faces)
    for i, a in enumerate(loop_a):
        j = (i+1) % len(loop_a)
        builder.quad(a, loop_a[j], loop_b[j], loop_b[i], provenance, flip)
    return list(range(begin, len(builder.faces)))


def _quad_grid_cap(builder, boundary, flip=False, provenance=None):
    """Fill a convex planar 4*n loop with an n*n Coons grid; no pole/fan.

    Boundary starts at a logical square corner, and each fourth follows one
    logical side. Circle loops are supported; arbitrary polygon loops must have
    actual convex corners at those four anchors (flat anchors are refused).
    Rejects folded layouts rather than silently triangulating them.
    """
    count = len(boundary)
    if count < 4 or count % 4 or len(set(boundary)) != count:
        raise QuadPatchError('cap_loop_count', 'Quad grid cap requires 4*n distinct boundary vertices')
    n = count//4
    p = [builder.vertices[i] for i in boundary]
    normal = _cross(_sub(p[n], p[0]), _sub(p[-n], p[0]))
    length = math.sqrt(_dot(normal, normal))
    if length <= builder.stitch_tolerance_mm**2 or any(abs(_dot(_sub(q, p[0]), normal))/length > builder.stitch_tolerance_mm for q in p):
        raise QuadPatchError('nonplanar_cap', 'Disk cap boundary must lie in one plane')
    grid = []
    first = len(builder.faces)
    for j in range(n+1):
        row = []
        for i in range(n+1):
            if j == 0:
                index = boundary[i]
            elif i == n:
                index = boundary[n+j]
            elif j == n:
                index = boundary[(3*n-i) % count]
            elif i == 0:
                index = boundary[(count-j) % count]
            else:
                u, v = i/n, j/n
                bottom, top = p[i], p[3*n-i]
                left, right = p[count-j], p[n+j]
                corners = p[0], p[n], p[2*n], p[3*n]
                q = tuple((1-v)*bottom[k]+v*top[k]+(1-u)*left[k]+u*right[k]
                          - ((1-u)*(1-v)*corners[0][k]+u*(1-v)*corners[1][k]+u*v*corners[2][k]+(1-u)*v*corners[3][k]) for k in range(3))
                index = builder.vertex(q)
            row.append(index)
        grid.append(row)
    for j in range(n):
        for i in range(n):
            builder.quad(grid[j][i], grid[j][i+1], grid[j+1][i+1], grid[j+1][i], provenance, flip)
    return {'boundary': list(boundary), 'grid': grid, 'face_range': (first, len(builder.faces))}



def quad_disk_cap(builder, boundary, flip=False, provenance=None):
    """Planar cap with a square core and only quads, with no center fan.

    A circular boundary receives a local O-grid before the square-grid core.
    This avoids the nearly 180-degree corner cells of a direct circular Coons
    grid at fine chord budgets. Already rectangular/grid-compatible boundaries
    use the core directly. Arbitrary incompatible polygons fail closed.
    """
    count=len(boundary)
    if count < 4 or count % 4 or len(set(boundary)) != count:
        raise QuadPatchError('cap_loop_count','Quad grid cap requires 4*n distinct boundary vertices')
    if any(type(i) is not int or not 0<=i<len(builder.vertices) for i in boundary):
        raise QuadPatchError('invalid_loop','Cap boundary references missing vertices')
    p=[builder.vertices[i] for i in boundary]
    center=tuple(math.fsum(q[k] for q in p)/count for k in range(3))
    radii=[math.dist(q,center) for q in p]
    radius=math.fsum(radii)/count
    is_circle=count>4 and max(radii)-min(radii)<=builder.stitch_tolerance_mm
    if not is_circle:
        return _quad_grid_cap(builder,boundary,flip,provenance)
    n=count//4
    u=_sub(p[n],p[0]); v=_sub(p[3*n],p[0])
    un,vn=math.sqrt(_dot(u,u)),math.sqrt(_dot(v,v))
    if min(un,vn)<=builder.near_seam_mm:
        raise QuadPatchError('degenerate_cap','Circle anchors are too close')
    u=tuple(c/un for c in u);v=tuple(c/vn for c in v)
    normal=_cross(u,v)
    if abs(_dot(u,v))>1e-8 or any(abs(_dot(_sub(q,center),normal))>builder.stitch_tolerance_mm for q in p):
        raise QuadPatchError('nonplanar_cap','Circle anchors must form a planar orthogonal frame')
    half=radius*.5
    local=sample_rectangle((-half,-half,half,half),n,n)
    core=[tuple(center[k]+q[0]*u[k]+q[1]*v[k] for k in range(3)) for q in local]
    shortest=min(math.dist(a,b) for a,b in zip(p,p[1:]+p[:1]))
    bands=max(1,math.ceil(max(math.dist(a,b) for a,b in zip(p,core))/(4*shortest)))
    if bands*count+n*n+len(builder.faces)>builder.max_faces:
        raise QuadPatchError('face_budget','Cap subdivision exceeds face budget before emission')
    first=len(builder.faces)
    previous=list(boundary)
    for band in range(1,bands+1):
        ring=builder.loop([_lerp(a,b,band/bands) for a,b in zip(p,core)])
        builder.loft(previous,ring,provenance=provenance,flip=flip)
        previous=ring
    result=_quad_grid_cap(builder,previous,flip,provenance)
    result.update({'boundary':list(boundary),'core_boundary':previous,
                   'radial_bands':bands,'face_range':(first,len(builder.faces))})
    return result


def _axis(values, guard):
    ordered = sorted(set(values))
    if any(b-a <= guard for a, b in zip(ordered, ordered[1:])):
        raise QuadPatchError('near_coincident_seam', 'Distinct planned grid coordinates are too close; dimensions were not snapped')
    return ordered


def align_artificial_patch_events(surfaces, max_shift):
    """Align nearby construction-only patch limits without moving profiles.

    Unrelated projections can otherwise leave a tiny strip at a local O-grid
    corner. Only generated ``patch`` limits may move; supplied silhouette,
    exclusions and feature coordinates are immutable anchors. All occurrences
    of an aligned patch must retain positive clearance from their true profiles
    and from their owning face. Unsupported near design events still reject.
    """
    if not 0 < max_shift <= 2:
        raise QuadPatchError('invalid_parameter', 'Patch alignment shift is bounded')
    by_axis = {}
    for surface in surfaces:
        for dim, axis in enumerate(surface['axes']):
            records = by_axis.setdefault(axis, {'fixed':set(), 'patches':[]})
            box = surface['bounds']
            records['fixed'].update((box[dim],box[dim+2]))
            if surface.get('radius',0):
                records['fixed'].update((box[dim]+surface['radius'],box[dim+2]-surface['radius']))
            for feature in surface['holes']:
                if 'patch' not in feature:
                    if 'bounds' in feature:records['fixed'].update((feature['bounds'][dim],feature['bounds'][dim+2]))
                    continue
                if feature['kind']=='circle':
                    radius=feature.get('sampling_radius',feature['radius'])
                    true=(feature['center'][dim]-radius,feature['center'][dim]+radius)
                else:true=(feature['bounds'][dim],feature['bounds'][dim+2])
                for offset in (dim,dim+2):
                    records['patches'].append((feature,offset,dim,true,box))
    changes=[]
    for axis,records in by_axis.items():
        occurrences={}
        for row in records['patches']:
            occurrences.setdefault(row[0]['patch'][row[1]],[]).append(row)
        values=sorted(set(occurrences)|records['fixed']);clusters=[]
        for value in values:
            if not clusters or value-clusters[-1][0]>max_shift:clusters.append([value])
            else:clusters[-1].append(value)
        for cluster in clusters:
            if len(cluster)<2:continue
            movable=[v for v in cluster if v in occurrences and v not in records['fixed']]
            fixed=[v for v in cluster if v in records['fixed']]
            anchors=fixed or cluster
            center=(cluster[0]+cluster[-1])/2
            anchors=sorted(anchors,key=lambda v:(abs(v-center),v))
            for old in movable:
                rows=occurrences[old]
                for new in anchors:
                    if abs(new-old)>max_shift:continue
                    valid=True
                    for feature,offset,dim,true,box in rows:
                        if offset==dim:
                            valid &= box[dim]+1e-6<new<true[0]-.05
                        else:valid &= true[1]+.05<new<box[dim+2]-1e-6
                    if not valid:continue
                    for feature,offset,dim,true,box in rows:
                        patch=list(feature['patch']);patch[offset]=new;feature['patch']=tuple(patch)
                    if new!=old:changes.append({'axis':axis,'old_patch_coordinate':old,'new_patch_coordinate':new,'actual_profiles_unchanged':True})
                    break
    return changes


def _samples(axis, subdivisions):
    return [axis[i]+(axis[i+1]-axis[i])*j/subdivisions[i]
            for i in range(len(axis)-1) for j in range(subdivisions[i])] + [axis[-1]]


def _rectangle_loop(xs, ys):
    return ([(x, ys[0]) for x in xs[:-1]] + [(xs[-1], y) for y in ys[:-1]]
            + [(x, ys[-1]) for x in reversed(xs[1:])] + [(xs[0], y) for y in reversed(ys[1:])])


def _profile_loop(bounds, radius, xs, ys):
    x0, y0, x1, y1 = xs[0], ys[0], xs[-1], ys[-1]
    fractions = [[(x-x0)/(x1-x0) for x in xs[:-1]], [(y-y0)/(y1-y0) for y in ys[:-1]],
                 [(x1-x)/(x1-x0) for x in reversed(xs[1:])], [(y1-y)/(y1-y0) for y in reversed(ys[1:])]]
    return [_rounded_side(bounds, radius, side, t) for side, ts in enumerate(fractions) for t in ts]


def _inside_round_rect(p, bounds, radius):
    x0, y0, x1, y1 = bounds
    if not x0 <= p[0] <= x1 or not y0 <= p[1] <= y1:
        return False
    if radius == 0:
        return True
    dx = max(x0+radius-p[0], 0, p[0]-(x1-radius))
    dy = max(y0+radius-p[1], 0, p[1]-(y1-radius))
    return dx*dx+dy*dy < radius*radius


def _outline_point(p, bounds, radius):
    """Keep straight silhouette coordinates; map each corner half to its arc."""
    if not radius:
        return p
    x0, y0, x1, y1 = bounds
    x, y = p
    if y in (y0, y1):
        sign = -1 if y == y0 else 1
        if x < x0+radius:
            a = (x0+radius-x)/radius*math.pi/4
            return x0+radius-radius*math.sin(a), y-sign*radius+sign*radius*math.cos(a)
        if x > x1-radius:
            a = (x-x1+radius)/radius*math.pi/4
            return x1-radius+radius*math.sin(a), y-sign*radius+sign*radius*math.cos(a)
    if x in (x0, x1):
        sign = -1 if x == x0 else 1
        if y < y0+radius:
            a = (y0+radius-y)/radius*math.pi/4
            return x-sign*radius+sign*radius*math.cos(a), y0+radius-radius*math.sin(a)
        if y > y1-radius:
            a = (y-y1+radius)/radius*math.pi/4
            return x-sign*radius+sign*radius*math.cos(a), y1-radius+radius*math.sin(a)
    return p


def _face_tile(builder, xs, ys, z, outline_bounds, outline_radius, flip, provenance):
    """Conforming Cartesian tile; only exterior edges follow the outline."""
    x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
    curve = lambda p: _outline_point(p, outline_bounds, outline_radius)
    corners = [curve(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    grid = []
    for y in ys:
        v = (y-y0)/(y1-y0)
        row = []
        for x in xs:
            u = (x-x0)/(x1-x0)
            # Interior shared edges stay Cartesian unless their endpoint moves
            # on the outline, in which case both adjoining tiles use one lerp.
            bottom = curve((x, y0)) if y0 == outline_bounds[1] else _lerp(corners[0], corners[1], u)
            top = curve((x, y1)) if y1 == outline_bounds[3] else _lerp(corners[3], corners[2], u)
            left = curve((x0, y)) if x0 == outline_bounds[0] else _lerp(corners[0], corners[3], v)
            right = curve((x1, y)) if x1 == outline_bounds[2] else _lerp(corners[1], corners[2], v)
            p = tuple((1-v)*bottom[k]+v*top[k]+(1-u)*left[k]+u*right[k]
                      - ((1-u)*(1-v)*corners[0][k]+u*(1-v)*corners[1][k]+u*v*corners[2][k]+(1-u)*v*corners[3][k]) for k in range(2))
            row.append(builder.vertex((*p, z)))
        grid.append(row)
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):
            builder.quad(grid[j][i], grid[j][i+1], grid[j+1][i+1], grid[j+1][i], provenance, flip)


def _feature(spec, guard):
    item = dict(spec)
    if 'id' not in item:
        raise QuadPatchError('invalid_feature', 'Every feature needs a unique id')
    kind = item.get('kind', 'circle')
    if kind == 'circle':
        cx, cy = _point(item['center'], 2)
        r = _positive(item['radius'], 'hole radius')
        bounds = (cx-r, cy-r, cx+r, cy+r)
    elif kind in ('rounded_rectangle', 'slot', 'rectangle'):
        bounds = _bounds(item['bounds'])
        r = _radius(bounds, 0 if kind == 'rectangle' else item['radius'])
    else:
        raise QuadPatchError('invalid_feature', 'Unsupported hole type '+str(kind))
    patch = _bounds(item.get('patch', bounds))
    exclusion = kind == 'rectangle' and 'patch' not in item
    if not exclusion and not (patch[0]+guard < bounds[0] and patch[1]+guard < bounds[1] and patch[2]-guard > bounds[2] and patch[3]-guard > bounds[3]):
        raise QuadPatchError('invalid_patch', 'Feature patch must strictly surround the true feature bounds')
    sampling_radius = _positive(item.get('sampling_radius', r), 'sampling radius') if r else 0.0
    if kind != 'circle' and sampling_radius != r:
        raise QuadPatchError('invalid_parameter', 'Alternate sampling radius is supported only for circles')
    if kind == 'circle' and sampling_radius < r:
        raise QuadPatchError('invalid_parameter', 'Sampling radius cannot understate the actual circle radius')
    return {'id': item['id'], 'kind': kind, 'bounds': bounds, 'radius': r, 'patch': patch, 'exclusion': exclusion, 'sampling_radius': sampling_radius}



def _planar_quad_metrics(faces):
    """Small, independent planning metric; final mesh acceptance is separate."""
    minimum_angle, maximum_angle, maximum_aspect = 180., 0., 0.
    for face in faces:
        origin=face[0]
        p=[_sub(q,origin) for q in face]
        edges=[_sub(p[(i+1)%4],p[i]) for i in range(4)]
        lengths=[math.hypot(*v) for v in edges]
        area=sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(p,p[1:]+p[:1]))/2
        if min(lengths)<=0 or area<=0:
            return {'minimum_angle_degrees':0.,'maximum_angle_degrees':180.,'maximum_aspect_ratio':math.inf}
        maximum_aspect=max(maximum_aspect,max(lengths)/min(lengths),max(lengths)**2/area)
        for i in range(4):
            a=_sub(p[i-1],p[i]);b=_sub(p[(i+1)%4],p[i])
            turn=b[0]*a[1]-b[1]*a[0]
            if turn<=0:
                return {'minimum_angle_degrees':0.,'maximum_angle_degrees':180.,'maximum_aspect_ratio':math.inf}
            angle=math.degrees(math.atan2(turn,_dot(a,b)))
            minimum_angle=min(minimum_angle,angle)
            maximum_angle=max(maximum_angle,angle)
    return {'minimum_angle_degrees':minimum_angle,'maximum_angle_degrees':maximum_angle,
            'maximum_aspect_ratio':maximum_aspect}


def _local_feature_layout(feature, fx, fy):
    """Bounded local grading choice; never changes profile or coarse seams.

    Keep the established half-clearance layout if it meets the local readability
    target. Otherwise inspect seven fractions and at most two radial bands.
    The target is a developer layout preference (10 degrees, aspect 40), not a
    relaxed/replacement final quality policy or a design dimension tolerance.
    """
    ffx=_samples(fx,[3]*(len(fx)-1)); ffy=_samples(fy,[3]*(len(fy)-1))
    inner=_profile_loop(feature['bounds'],feature['radius'],ffx,ffy)
    coarse=_rectangle_loop(fx,fy)
    def candidate(fraction,bands):
        intermediate=tuple(a+(b-a)*fraction for a,b in zip(feature['bounds'],feature['patch']))
        fine=_profile_loop(intermediate,0,ffx,ffy)
        rings=[fine]+[[_lerp(a,b,j/bands) for a,b in zip(fine,inner)] for j in range(1,bands)]+[inner]
        faces=[]
        for outer_ring,inner_ring in zip(rings,rings[1:]):
            for i in range(len(fine)):
                j=(i+1)%len(fine)
                faces.append((outer_ring[i],outer_ring[j],inner_ring[j],inner_ring[i]))
        for i,q0 in enumerate(coarse):
            q1=coarse[(i+1)%len(coarse)]
            p0,p1,p2,p3=[fine[(3*i+j)%len(fine)] for j in range(4)]
            u=_lerp(p1,_lerp(q0,q1,1/3),.4)
            v=_lerp(p2,_lerp(q0,q1,2/3),.4)
            faces.extend(((p0,q0,u,p1),(p1,u,v,p2),(p2,v,q1,p3),(u,q0,q1,v)))
        quality=_planar_quad_metrics(faces)
        meets_target=(quality['minimum_angle_degrees']>=10. and quality['maximum_angle_degrees']<=175.
                      and quality['maximum_aspect_ratio']<=40.)
        return {'fine':fine,'inner':inner,'rings':rings,'fraction':fraction,'radial_bands':bands,
                'quality':quality,'layout_target_met':meets_target}
    baseline=candidate(.5,1)
    if baseline['layout_target_met']:
        baseline['candidates_checked']=1
        return baseline
    candidates=[baseline]
    for bands in (1,2):
        for fraction in (.5,.55,.6,.65,.7,.75,.8):
            if bands==1 and fraction==.5:
                continue
            candidates.append(candidate(fraction,bands))
    eligible=[c for c in candidates if c['layout_target_met']]
    if eligible:
        # Spend extra local quads only where the one-band candidates cannot
        # meet the layout target; among equal-cost candidates maximize angles.
        chosen=min(eligible,key=lambda c:(c['radial_bands'],-c['quality']['minimum_angle_degrees'],c['quality']['maximum_aspect_ratio'],c['fraction']))
    else:
        eligible=[c for c in candidates if c['quality']['minimum_angle_degrees']>=max(5.,baseline['quality']['minimum_angle_degrees'])
                  and c['quality']['maximum_angle_degrees']<=175.
                  and c['quality']['maximum_aspect_ratio']<=min(50.,max(40.,baseline['quality']['maximum_aspect_ratio']))]
        if not eligible:
            raise QuadPatchError('local_patch_quality','No bounded local annulus layout passes convexity/angle/aspect requirements')
        chosen=min(eligible,key=lambda c:(-c['quality']['minimum_angle_degrees'],c['radial_bands'],c['quality']['maximum_aspect_ratio']))
    chosen['candidates_checked']=len(candidates)
    return chosen


def tiled_face(builder, bounds, holes=(), z=0.0, chord_tolerance_mm=.025,
               outline_radius=0.0, max_segments=512, max_cells=200000,
               flip=False, provenance=None, x_breaks=(), y_breaks=(),
               x_subdivisions=None, y_subdivisions=None, target_edge_length_mm=4.0, outline_chord_tolerance_mm=None):
    """Rectangle/rounded-rectangle face with feature-local O-grid holes.

    Features: {id, kind:'circle', center:(x,y), radius, patch:(xmin,ymin,xmax,ymax)};
    {id, kind:'rounded_rectangle' or 'slot', bounds, radius, patch}; or
    {id, kind:'rectangle', bounds} for an exact rectangular exclusion (e.g. lip).
    O-grid patches must be disjoint and strictly interior. Rectangle exclusions
    may touch the face bounds (returning open paths under exclusions[id]).
    Set sampling_radius to a common largest radius for coaxial circles, and use
    the same patch/breaks to retain identical loop correspondence. Extra x/y
    breaks add seam events; optional subdivisions replace the computed schedule
    only when they meet or exceed every chord requirement. All distinct coordinates
    separated beyond the seam guard. The exterior is CCW; returned hole loops
    are also CCW for convenient matching/lofting, NOT boundary-face winding.
    Returns index loops outer, holes[id], patches[id], plus emitted face_range.
    Curved holes have dense rings three times their coarse patch perimeter;
    feature-local all-quad 3:1 transition blocks stop that density spreading
    across the planar field. Rectangle exclusions use the coarse schedule.
    feature_layouts records bounded local clearance/grading choices and their
    measured planning quality; it does not replace final mesh/visual acceptance.
    """
    bounds = _bounds(bounds)
    outline_radius = _radius(bounds, outline_radius)
    tolerance = _positive(chord_tolerance_mm, 'chord tolerance')
    target = _positive(target_edge_length_mm, 'target edge length')
    maximum = _integer(max_segments, 'max segments', 4)
    _integer(max_cells, 'max cells')
    z = _point((z,), 1)[0]
    features = [_feature(s, builder.near_seam_mm) for s in holes]
    if len({s['id'] for s in features}) != len(features):
        raise QuadPatchError('duplicate_feature', 'Feature identifiers must be unique')
    guard = builder.near_seam_mm
    for i, f in enumerate(features):
        patch = f['patch']
        interior = bounds[0]+guard < patch[0] and bounds[1]+guard < patch[1] and bounds[2]-guard > patch[2] and bounds[3]-guard > patch[3]
        within = bounds[0] <= patch[0] and bounds[1] <= patch[1] and bounds[2] >= patch[2] and bounds[3] >= patch[3]
        f['boundary_exclusion'] = f['exclusion'] and not interior
        if not within or (not interior and not f['exclusion']):
            raise QuadPatchError('patch_outside', 'O-grid patch must be strictly inside face; exclusion must be within bounds')
        if not f['boundary_exclusion'] and any(not _inside_round_rect(p, bounds, outline_radius) for p in ((patch[0],patch[1]),(patch[2],patch[1]),(patch[2],patch[3]),(patch[0],patch[3]))):
            raise QuadPatchError('patch_outside', 'Feature patch enters the rounded silhouette')
        for previous in features[:i]:
            q = previous['patch']
            if min(patch[2], q[2])+guard >= max(patch[0], q[0]) and min(patch[3], q[3])+guard >= max(patch[1], q[1]):
                raise QuadPatchError('overlapping_patches', 'Feature patches overlap, touch, or have an unsafe near seam')
    xv = [bounds[0], bounds[2]]+[x for f in features for x in (f['patch'][0], f['patch'][2])]
    yv = [bounds[1], bounds[3]]+[y for f in features for y in (f['patch'][1], f['patch'][3])]
    for axis_extra, axis_values, low, high in ((x_breaks,xv,bounds[0],bounds[2]),(y_breaks,yv,bounds[1],bounds[3])):
        for value in axis_extra:
            value = _point((value,),1)[0]
            if not low <= value <= high:
                raise QuadPatchError('invalid_bounds','Explicit grid break lies outside face')
            axis_values.append(value)
    if outline_radius:
        xv += [bounds[0]+outline_radius, bounds[2]-outline_radius]
        yv += [bounds[1]+outline_radius, bounds[3]-outline_radius]
    xaxis, yaxis = _axis(xv, guard), _axis(yv, guard)
    nx = [max(1,math.ceil((b-a)/target)) for a,b in zip(xaxis,xaxis[1:])]
    ny = [max(1,math.ceil((b-a)/target)) for a,b in zip(yaxis,yaxis[1:])]
    for f in features:
        r = f['sampling_radius']
        if not r:
            continue
        step = min(math.sqrt(8*r*tolerance), math.pi*r/2)
        if step <= 0:
            raise QuadPatchError('sampling_budget','Chord tolerance is below numeric resolution')
        for axis, ns, dim in ((xaxis,nx,0), (yaxis,ny,1)):
            width = 2*r if f['kind'] == 'circle' else f['bounds'][dim+2]-f['bounds'][dim]
            plen = f['patch'][dim+2]-f['patch'][dim]
            length = width-2*r+math.pi*r/2
            for i, (a,b) in enumerate(zip(axis,axis[1:])):
                if a >= f['patch'][dim] and b <= f['patch'][dim+2]:
                    ns[i] = max(ns[i], math.ceil(length*(b-a)/plen/step/3))
    if outline_radius:
        outline_tolerance=tolerance if outline_chord_tolerance_mm is None else _positive(outline_chord_tolerance_mm,'outline chord tolerance')
        if outline_tolerance>tolerance:raise QuadPatchError('invalid_parameter','Outline sampling cannot weaken requested curve tolerance')
        angle = 4*math.asin(math.sqrt(min(1.0,outline_tolerance/(2*outline_radius))))
        if angle <= 0:
            raise QuadPatchError('sampling_budget','Outline tolerance is below numeric resolution')
        for axis, ns, dim in ((xaxis,nx,0),(yaxis,ny,1)):
            for i,(a,b) in enumerate(zip(axis,axis[1:])):
                if b <= bounds[dim]+outline_radius or a >= bounds[dim+2]-outline_radius:
                    ns[i] = max(ns[i], math.ceil((b-a)/outline_radius*(math.pi/4)/angle))
    for requested, planned in ((x_subdivisions,nx),(y_subdivisions,ny)):
        if requested is not None:
            if len(requested) != len(planned):
                raise QuadPatchError('invalid_budget','Explicit subdivision schedule must match planned axis intervals')
            for i,count in enumerate(requested):
                _integer(count,'interval subdivisions')
                if count < planned[i]:
                    raise QuadPatchError('sampling_budget','Explicit subdivision count fails chord bound')
                planned[i] = count
    if sum(nx)*sum(ny) > max_cells:
        raise QuadPatchError('cell_budget', 'Planned tile subdivision exceeds cell budget')
    if 2*(sum(nx)+sum(ny)) > maximum*8:
        raise QuadPatchError('sampling_budget', 'Outer boundary exceeds bounded face sampling budget')
    for f in features:
        local_count = 2*sum(n for axis,ns,dim in ((xaxis,nx,0),(yaxis,ny,1)) for a,b,n in zip(axis,axis[1:],ns) if a>=f['patch'][dim] and b<=f['patch'][dim+2])
        if local_count*(1 if f['exclusion'] else 3) > maximum:
            raise QuadPatchError('sampling_budget','A local feature exceeds maximum segments before allocation')
        if not f['exclusion'] and 3*local_count*local_count>1000000:
            raise QuadPatchError('transition_budget','Local transition planning exceeds bounded containment work')
    xs, ys = _samples(xaxis,nx), _samples(yaxis,ny)
    first = len(builder.faces)
    hole_loops, patch_loops, transition_loops, feature_layouts = {}, {}, {}, {}
    for f in features:
        patch = f['patch']
        if f['boundary_exclusion']:
            continue
        fx = [x for x in xs if patch[0] <= x <= patch[2]]
        fy = [y for y in ys if patch[1] <= y <= patch[3]]
        if 2*(len(fx)+len(fy)-2) > maximum:
            raise QuadPatchError('sampling_budget', 'A local feature loop exceeds maximum segments')
        outer = builder.loop([(*p,z) for p in _rectangle_loop(fx,fy)])
        patch_loops[f['id']] = outer
        if f['exclusion']:
            hole_loops[f['id']] = outer
            continue
        # Dense vertices remain wholly inside this local feature patch.
        # Every coarse edge receives three matching fine edges, including at
        # existing macro events from neighboring holes and exclusions.
        layout = _local_feature_layout(f,fx,fy)
        fine = builder.loop([(*p,z) for p in layout['fine']])
        inner = builder.loop([(*p,z) for p in layout['inner']])
        hole_loops[f['id']] = inner
        transition_loops[f['id']] = fine
        feature_layouts[f['id']] = {key:layout[key] for key in ('fraction','radial_bands','quality','layout_target_met','candidates_checked')}
        fp = dict(provenance or {})
        fp['subfeature_id'] = f['id']
        fp['patch_role'] = 'feature_ogrid'
        rings = [fine]+[builder.loop([(*p,z) for p in ring]) for ring in layout['rings'][1:-1]]+[inner]
        for outer_ring,inner_ring in zip(rings,rings[1:]):
            for i in range(len(fine)):
                j=(i+1)%len(fine)
                builder.quad(outer_ring[i],outer_ring[j],inner_ring[j],inner_ring[i],fp,flip)
        fp['patch_role'] = 'feature_transition_3_to_1'
        quad_transition_3_to_1(builder,fine,outer,flip=flip,provenance=fp)
    for iy in range(len(yaxis)-1):
        y0,y1=yaxis[iy:iy+2]
        ty=[y for y in ys if y0<=y<=y1]
        for ix in range(len(xaxis)-1):
            x0,x1=xaxis[ix:ix+2]
            if any(f['patch'][0] <= (x0+x1)/2 <= f['patch'][2] and f['patch'][1] <= (y0+y1)/2 <= f['patch'][3] for f in features):
                continue
            tx=[x for x in xs if x0<=x<=x1]
            _face_tile(builder,tx,ty,z,bounds,outline_radius,flip,provenance)
    loops, directed_edges = _boundary_loops(builder,first,len(builder.faces))
    if not loops:
        raise QuadPatchError('empty_face','No face survives the exclusions')
    outline = max(loops,key=lambda loop: abs(_loop_area(builder,loop)))
    if _loop_area(builder,outline) < 0:
        outline = outline[:1] + list(reversed(outline[1:]))
    exclusions = {}
    for f in features:
        if not f['boundary_exclusion']:
            continue
        p = f['patch']
        es = []
        for a,b in directed_edges:
            va,vb = builder.vertices[a],builder.vertices[b]
            if any(abs(va[axis]-value)<=builder.stitch_tolerance_mm and abs(vb[axis]-value)<=builder.stitch_tolerance_mm and p[1-axis]-guard<=min(va[1-axis],vb[1-axis]) and max(va[1-axis],vb[1-axis])<=p[3-axis]+guard for axis,value in ((0,p[0]),(0,p[2]),(1,p[1]),(1,p[3]))):
                es.append((a,b))
        exclusions[f['id']] = _edge_paths(es)
    return {'outer':outline,'holes':hole_loops,'patches':patch_loops,
            'boundary_loops':loops,'exclusions':exclusions,'transition_loops':transition_loops,
            'transition_ratio':3,'feature_layouts':feature_layouts,
            'feature_profiles':features,'outline_parameters':_rectangle_loop(xs,ys),
            'face_range':(first,len(builder.faces)), 'x_breaks':xaxis,'y_breaks':yaxis,
            'x_subdivisions':nx,'y_subdivisions':ny,'chord_tolerance_mm':tolerance}




def quad_transition_3_to_1(builder, fine, coarse, flip=False, provenance=None):
    """Join nested CCW loops using exact four-quad 3-fine:1-coarse blocks.

    Fine[3*i] and coarse[i] are corresponding radial anchors. Each block has
    six boundary vertices, two planned interior vertices, and four strictly
    convex quads. Shared block/corner edges are reused by index; no triangle
    pairing, vertex collapse, or post-hoc repair is involved.
    """
    if len(coarse)<4 or len(fine)!=3*len(coarse) or len(set(fine))!=len(fine) or len(set(coarse))!=len(coarse):
        raise QuadPatchError('transition_loop_count','Transition requires a distinct fine loop exactly 3 times the coarse loop')
    if any(type(i) is not int or not 0<=i<len(builder.vertices) for i in list(fine)+list(coarse)):
        raise QuadPatchError('invalid_loop','Transition references missing vertices')
    if len(fine)*len(coarse)>1000000:
        raise QuadPatchError('transition_budget','Transition containment check exceeds bounded comparison budget')
    outer_points=[builder.vertices[i] for i in coarse]
    inner_points=[builder.vertices[i] for i in fine]
    origin=outer_points[0]
    normals=[_cross(_sub(a,origin),_sub(b,origin)) for a,b in zip(outer_points,outer_points[1:]+outer_points[:1])]
    normal=tuple(math.fsum(q[k] for q in normals) for k in range(3))
    length=math.sqrt(_dot(normal,normal))
    if length<=builder.stitch_tolerance_mm**2:
        raise QuadPatchError('invalid_transition','Coarse boundary has no oriented area')
    normal=tuple(x/length for x in normal)
    if any(abs(_dot(_sub(p,origin),normal))>builder.stitch_tolerance_mm for p in inner_points+outer_points):
        raise QuadPatchError('invalid_transition','Local transition must be planar')
    for a,b in zip(outer_points,outer_points[1:]+outer_points[:1]):
        edge=_sub(b,a);edge_length=math.sqrt(_dot(edge,edge))
        if edge_length<=builder.near_seam_mm:
            raise QuadPatchError('invalid_transition','Coarse edge is too short')
        if any(_dot(_cross(edge,_sub(p,a)),normal)/edge_length < -builder.stitch_tolerance_mm for p in outer_points):
            raise QuadPatchError('invalid_transition','Coarse transition boundary must be convex')
        if any(_dot(_cross(edge,_sub(p,a)),normal)/edge_length<=builder.near_seam_mm for p in inner_points):
            raise QuadPatchError('invalid_transition','Fine loop must lie strictly inside the convex coarse boundary')
    if len(builder.faces)+4*len(coarse)>builder.max_faces or len(builder.vertices)+2*len(coarse)>builder.max_vertices:
        raise QuadPatchError('transition_budget','Transition exceeds mesh budget before emission')
    first=len(builder.faces)
    for i,q0 in enumerate(coarse):
        q1=coarse[(i+1)%len(coarse)]
        p0,p1,p2,p3=[fine[(3*i+j)%len(fine)] for j in range(4)]
        u=builder.vertex(_lerp(builder.vertices[p1],_lerp(builder.vertices[q0],builder.vertices[q1],1/3),.4))
        v=builder.vertex(_lerp(builder.vertices[p2],_lerp(builder.vertices[q0],builder.vertices[q1],2/3),.4))
        for face in ((p0,q0,u,p1),(p1,u,v,p2),(p2,v,q1,p3),(u,q0,q1,v)):
            builder.quad(*face,provenance=provenance,flip=flip)
    return {'fine':list(fine),'coarse':list(coarse),'face_range':(first,len(builder.faces))}


def _loop_area(builder,loop):
    return sum(builder.vertices[a][0]*builder.vertices[b][1]-builder.vertices[b][0]*builder.vertices[a][1]
               for a,b in zip(loop,loop[1:]+loop[:1]))/2


def _edge_paths(edges):
    outgoing = {a:b for a,b in edges}
    if len(outgoing) != len(edges):
        raise QuadPatchError('nonmanifold_boundary','Boundary paths branch')
    remaining = set(outgoing)
    incoming = {b for a,b in edges}
    paths=[]
    while remaining:
        first=min(remaining-incoming) if remaining-incoming else min(remaining)
        path=[first]; current=first
        while current in remaining:
            remaining.remove(current)
            current=outgoing[current]
            if current==first:
                break
            path.append(current)
        paths.append(path)
    return paths


def _boundary_loops(builder,first,last):
    incidences=defaultdict(list)
    for face in builder.faces[first:last]:
        for a,b in zip(face,face[1:]+face[:1]):
            incidences[tuple(sorted((a,b)))].append((a,b))
    if any(len(es)>2 or (len(es)==2 and es[0]==es[1]) for es in incidences.values()):
        raise QuadPatchError('nonmanifold_boundary','Patch face incidence is inconsistent')
    edges=[es[0] for es in incidences.values() if len(es)==1]
    if {a for a,b in edges}!={b for a,b in edges}:
        raise QuadPatchError('open_boundary','Face boundary did not close')
    return _edge_paths(edges),edges


def circle_rect_annulus(builder, center, radius, bounds, z=0.0,
                        chord_tolerance_mm=.025, max_segments=512,
                        flip=False, provenance=None):
    """One local circle-to-rectangle O-grid, without a pole or triangulation."""
    bounds = _bounds(bounds)
    center = _point(center,2)
    radius = _positive(radius,'radius')
    guard=builder.near_seam_mm
    if not (bounds[0]+guard<center[0]-radius and bounds[1]+guard<center[1]-radius and bounds[2]-guard>center[0]+radius and bounds[3]-guard>center[1]+radius):
        raise QuadPatchError('invalid_patch','Rectangle must strictly enclose the circle')
    n=circle_segments(radius,chord_tolerance_mm,max_segments)//4
    xs=[bounds[0]+(bounds[2]-bounds[0])*i/n for i in range(n+1)]
    ys=[bounds[1]+(bounds[3]-bounds[1])*i/n for i in range(n+1)]
    outer=builder.loop([(*p,z) for p in _rectangle_loop(xs,ys)])
    circle_bounds=(center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius)
    inner=builder.loop([(*p,z) for p in _profile_loop(circle_bounds,radius,xs,ys)])
    first=len(builder.faces)
    for i in range(len(outer)):
        j=(i+1)%len(outer)
        builder.quad(outer[i],outer[j],inner[j],inner[i],provenance,flip)
    return {'outer':outer,'inner':inner,'face_range':(first,len(builder.faces))}
