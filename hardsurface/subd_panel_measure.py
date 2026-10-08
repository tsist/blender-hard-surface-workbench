"""Reference-relative, bounded probes for the declared rounded panel domain.

No arbitrary shape fitting. Nominal parameters must come from the frozen caller
contract. The outer shell is a rounded rectangle extrusion with a uniform XY/Z
edge roundover. The through-hole is a sharp circular cylinder. These are finite
mesh samples, not a Hausdorff bound or proof about an uncomputed limit surface.
"""
import math
from .io import RuntimeFailure
from .semantic_bore_measure import (semantic_bore_samples, SEMANTIC_BORE_PROFILE,
                                    LEGACY_MEASUREMENT_PROFILE, STATION_EPSILON_MM)


def nominal_distance(point,p):
    x,y,z=point;cx,cy=p.get('center',[0,0]);w,h=p['size'];r=p['corner_radius'];b=p['edge_bevel'];lo,hi=p['z_min'],p['z_max']
    qx=abs(x-cx)-(w/2-r);qy=abs(y-cy)-(h/2-r)
    dxy=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r+b
    dz=max(lo+b-z,z-hi+b)
    outer=math.hypot(max(dxy,0),max(dz,0))+min(max(dxy,dz),0)-b
    hole=p['holes'][0];hx,hy=hole['center'];bore=hole['radius']-math.hypot(x-hx,y-hy)
    # The sharp bore/cap corner is an intersection of orthogonal surfaces.
    # max alone is only a CSG implicit field and underestimates distance outside
    # both half-spaces; use the corner distance in that bounded domain.
    return math.hypot(outer,bore) if outer>0 and bore>0 else max(outer,bore)


def measure_panel(vertices,faces,p,*,tolerance_mm,bore_face_indices=None,measurement_profile=None):
    if len(vertices)>300000 or len(faces)>250000:raise RuntimeFailure('SUBD_MEASUREMENT_BUDGET','Actual mesh exceeds bounded reference probe domain')
    if not vertices or not faces or any(len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vertices):raise RuntimeFailure('SUBD_MEASUREMENT_DOMAIN','Nonempty finite 3D mesh required')
    if any(len(f)!=4 or len(set(f))!=4 or any(type(i)is not int or not 0<=i<len(vertices) for i in f) for f in faces):raise RuntimeFailure('SUBD_MEASUREMENT_DOMAIN','Valid indexed real quads required')
    if type(tolerance_mm) not in (int,float) or not math.isfinite(tolerance_mm) or tolerance_mm<=0:raise RuntimeFailure('SUBD_MEASUREMENT_DOMAIN','Positive finite tolerance required')
    semantic=None
    if measurement_profile==SEMANTIC_BORE_PROFILE:
        semantic=semantic_bore_samples(vertices,faces,p,bore_face_indices)
    elif measurement_profile not in (None,LEGACY_MEASUREMENT_PROFILE) or bore_face_indices is not None:
        raise RuntimeFailure('SUBD_MEASUREMENT_PROFILE','Explicit supported semantic profile required with bore membership')
    actual_profile=SEMANTIC_BORE_PROFILE if semantic is not None else LEGACY_MEASUREMENT_PROFILE
    maximum=-1.;worst=None;count=0;edges=set()
    def probe(q,kind,index):
        nonlocal maximum,worst,count
        d=abs(nominal_distance(q,p));count+=1
        if d>maximum:maximum=d;worst={'point_mm':list(q),'sample_kind':kind,'index':index,'absolute_distance_mm':d}
    for i,q in enumerate(vertices):probe(q,'vertex',i)
    for fi,f in enumerate(faces):
        for a,b in zip(f,f[1:]+f[:1]):edges.add(tuple(sorted((a,b))))
        probe([sum(vertices[i][k] for i in f)/len(f) for k in range(3)],'polygon_centroid',fi)
        # Match both diagonal interpretations of nonplanar quads conservatively.
        if len(f)==4:
            for j,tri in enumerate(((0,1,2),(0,2,3),(0,1,3),(1,2,3))):
                probe([sum(vertices[f[i]][k] for i in tri)/3 for k in range(3)],'quad_triangle_centroid',4*fi+j)
    for i,(a,b) in enumerate(sorted(edges)):probe([(vertices[a][k]+vertices[b][k])/2 for k in range(3)],'edge_midpoint',i)
    lo,hi=p['z_min'],p['z_max'];h=p['holes'][0];hx,hy=h['center'];hr=h['radius'];near=max(tolerance_mm*4,hr*.1)
    if semantic is None:
        # Frozen legacy diagnostic behavior; not usable as source-bound qualification.
        wall_ids=set()
        for f in faces:
            q=[vertices[i] for i in f]
            if max(v[2] for v in q)-min(v[2] for v in q)<1e-6:continue
            if max(abs(math.hypot(v[0]-hx,v[1]-hy)-hr) for v in q)<near:wall_ids.update(f)
    else:wall_ids=set(semantic['wall_vertex_indices'])
    bore=[vertices[i] for i in wall_ids]
    wall_radial_error=max((abs(math.hypot(v[0]-hx,v[1]-hy)-hr) for v in bore),default=None)
    wall_witness={'status':'pass' if wall_radial_error is not None and wall_radial_error<=tolerance_mm else 'fail',
                  'vertices':len(bore),'maximum_radial_error_mm':wall_radial_error,
                  'scope':'Every explicitly selected wall vertex against the declared bore axis and radius; finite vertices only'} if semantic is not None else None
    slices={}
    for label,z in [('bottom_rim',lo),('middle_wall',(lo+hi)/2),('top_rim',hi)]:
        rows=([vertices[i] for i in semantic['slice_vertex_indices'][label]] if semantic is not None
              else [v for v in bore if abs(v[2]-z)<STATION_EPSILON_MM])
        radii=[math.hypot(v[0]-hx,v[1]-hy) for v in rows]
        angles=sorted(math.atan2(v[1]-hy,v[0]-hx)%math.tau for v in rows)
        angular_gap=max((b-a for a,b in zip(angles,angles[1:]+[angles[0]+math.tau])),default=math.tau) if angles else math.tau
        radial_error=max((abs(v-hr) for v in radii),default=None)
        witnessed=len(rows)>=24 and angular_gap<=math.tau/24+1e-4 and radial_error is not None and radial_error<=tolerance_mm
        slices[label]={'vertices':len(rows),'z_mm':z,'radius_range_mm':[min(radii),max(radii)] if radii else None,'max_radial_error_mm':radial_error,'radial_peak_to_valley_mm':max(radii)-min(radii) if radii else None,'maximum_angular_gap_degrees':math.degrees(angular_gap),'status':'pass' if witnessed else 'fail' if rows else 'not_sampled'}
        if semantic is not None:
            slices[label]['selection_method']='semantic_wall_vertices_at_station' if label=='middle_wall' else 'topological_boundary_cycle'
    cx,cy=p.get('center',[0,0]);w,h=p['size'];r=p['corner_radius'];b=p['edge_bevel'];t=hi-lo
    bounds=[min(v[k] for v in vertices) for k in range(3)]+[max(v[k] for v in vertices) for k in range(3)]
    target_bounds=[cx-w/2,cy-h/2,lo,cx+w/2,cy+h/2,hi];extents=[bounds[k+3]-bounds[k] for k in range(3)]
    extent_errors=[abs(x-y) for x,y in zip(extents,[w,h,t])];bound_errors=[abs(x-y) for x,y in zip(bounds,target_bounds)]
    bounds_ok=max(extent_errors+bound_errors)<=tolerance_mm
    # Exact nominal volume for a parallel rounded-rectangle inset across both
    # quarter-circle edge roundovers, minus the sharp cylindrical through-hole.
    area=w*h-(4-math.pi)*r*r;perimeter=2*(w+h-4*r)+2*math.pi*r
    i1=b*b*(1-math.pi/4);i2=b**3*(5/3-math.pi/2)
    nominal_volume=area*t-2*perimeter*i1+2*math.pi*i2-math.pi*hr*hr*t
    nominal_surface=2*(area-perimeter*b+math.pi*b*b-math.pi*hr*hr)+perimeter*(t-2*b)+2*math.pi*hr*t+math.pi*b*perimeter+(4*math.pi-2*math.pi**2)*b*b
    origin=[sum(v[k] for v in vertices)/len(vertices) for k in range(3)]
    def det(a,b,c):return a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0])
    centered=[[v[k]-origin[k] for k in range(3)] for v in vertices]
    volume=abs(math.fsum(det(centered[f[0]],centered[f[j]],centered[f[j+1]])/6 for f in faces for j in range(1,len(f)-1)))
    volume_limit=2*nominal_surface*tolerance_mm;volume_error=abs(volume-nominal_volume)
    completeness={'status':'pass' if bounds_ok and volume_error<=volume_limit and all(row['status']=='pass' for row in slices.values()) and (wall_witness is None or wall_witness['status']=='pass') else 'fail',
        'actual_bounds_mm':bounds,'nominal_bounds_mm':target_bounds,'extent_errors_mm':extent_errors,'bound_errors_mm':bound_errors,
        'bbox_status':'pass' if bounds_ok else 'fail','actual_volume_mm3':volume,'nominal_volume_mm3':nominal_volume,'absolute_volume_difference_mm3':volume_error,
        'volume_screen_limit_mm3':volume_limit,'volume_screen_status':'pass' if volume_error<=volume_limit else 'fail',
        'volume_screen_policy':'2 × nominal surface area × requested tolerance; conservative missing-shape sanity screen, not a certified distance or volume tolerance',
        'scope':'bbox/extent, full angular coverage at three bore vertex slices, and closed-volume sanity are necessary feature witnesses; not exhaustive nominal-to-mesh distance coverage'}
    if wall_witness is not None:completeness['semantic_wall_radial_witness']=wall_witness
    return {'measurement_profile':actual_profile,
        'qualification_status':'not_established_by_measurement_alone',
        'semantic_bore_membership':semantic['evidence'] if semantic is not None else {'status':'legacy_diagnostic_only','selection_method':'legacy_dz_and_radius_proximity','source_binding':'not_established'},
        'status':'pass' if maximum<=tolerance_mm and completeness['status']=='pass' else 'fail','tolerance_mm':tolerance_mm,'sample_count':count,'maximum_sampled_surface_distance_mm':maximum,'worst_sample':worst,'hole_slices':slices,'feature_witnesses':completeness,'proximity_status':'pass' if maximum<=tolerance_mm else 'fail','scope':'finite actual mesh vertices, unique edge midpoints, polygon and both-quad-triangulation centroids against declared sharp-bore rounded-panel distance function; not an analytic continuous-surface or all-points bound'}
