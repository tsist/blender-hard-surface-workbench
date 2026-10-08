"""Actual source-edge validation of authored semantic cage cycles."""
import json,math
from collections import defaultdict,Counter


def inspect_control_loops(obj, *, require_fixed_support=False):
    if type(require_fixed_support) is not bool:raise ValueError('require_fixed_support must be a boolean')
    mesh=obj.data;declared=json.loads(obj.get('hs_subd_control_loops','[]'));edge_map={tuple(sorted(e.vertices)):e.index for e in mesh.edges};adj=defaultdict(set)
    for a,b in edge_map:adj[a].add(b);adj[b].add(a)
    crease=mesh.attributes.get('crease_edge');rows=[];expected_creased=set()
    incident=defaultdict(list)
    for poly in mesh.polygons:
        for vertex in poly.vertices:incident[vertex].append(set(poly.vertices))
    metadata=json.loads(obj.get('hs_quad_metadata','{}')).get('subdivision_cage',{})
    design=metadata.get('nominal_design',{})
    policy=metadata.get('control_policy',{});fixed_width=None;fixed_binding_valid=True;fixed_cycle_binding='not_applicable'
    fixed_required=require_fixed_support or any(key in policy for key in ('bore_support_width_mm','bore_support_width_mode','technical_layout_revision','outer_join_policy'))
    # A fixed-width declaration requires actual identity transport and complete
    # independent coordinate attestation. A JSON width alone cannot certify it.
    try:
        from .structure_native import has_native_identity,validate_native_structure
        from .structure_edit import _verify_parameter_coordinates,_parameter_domain
        from .io import RuntimeFailure
        from .structure_kernel import StructureError
        if has_native_identity(obj):
            bound=validate_native_structure(obj,unit_scale=1.0)
            parameters=bound['authorship']['parameter_binding']['parameters']
            cfg=parameters.get('subdivision_cage',{})
            if 'bore_support_width' in cfg:
                fixed_required=True
                fixed_width=cfg['bore_support_width'];_parameter_domain(parameters)
                _verify_parameter_coordinates({'vertices_mm':bound['witness']['mesh']['vertices'],'authored_structure':bound['authorship']})
                vm=bound['authorship']['vertex_map']
                def cycle(role,indices):
                    return role,tuple(sorted(tuple(sorted((a,b))) for a,b in zip(indices,indices[1:]+indices[:1])))
                expected_cycles=Counter(cycle(row['role'].split('/j')[0],[vm[key] for key in row['vertex_ids']])
                                        for row in bound['authorship']['semantic_control_loops'])
                actual_cycles=Counter(cycle(row['role'],row['vertex_indices']) for row in declared)
                fixed_cycle_binding=actual_cycles==expected_cycles
                expected={'center_mm':parameters.get('center',[0,0]),'size_mm':parameters['size'],
                          'corner_radius_mm':parameters['corner_radius'],'edge_roundover_radius_mm':parameters['edge_bevel'],
                          'hole_center_mm':parameters['holes'][0]['center'],'hole_radius_mm':parameters['holes'][0]['radius'],
                          'z_range_mm':[parameters['z_min'],parameters['z_max']]}
                fixed_binding_valid=(fixed_cycle_binding and design==expected and policy.get('bore_support_width_mode')=='fixed_explicit'
                                     and policy.get('bore_support_width_mm')==fixed_width
                                     and policy.get('technical_layout_revision')=='fixed_support_half_gap_guards_v1'
                                     and policy.get('outer_join_policy')==cfg.get('outer_join_policy'))
        if fixed_required and fixed_width is None:
            fixed_binding_valid=False
    except (RuntimeFailure,StructureError,KeyError,TypeError,ValueError,AttributeError):
        fixed_binding_valid=False
    expected_roles=Counter({'bore_rim_bottom':1,'bore_support_bottom':1,'bore_rim_top':1,'bore_support_top':1,'bottom_flat_guard':1,'lower_roundover':4,'lower_wall_guard':1,'upper_wall_guard':1,'upper_roundover':4,'top_flat_guard':1})
    role_contract=Counter(x.get('role') for x in declared)==expected_roles
    seen_cycles=set()
    for i,record in enumerate(declared):
        v=record['vertex_indices'];edges=[tuple(sorted((a,b))) for a,b in zip(v,v[1:]+v[:1])]
        indices=[edge_map.get(e) for e in edges];valid=len(v)==(24 if record['role'].startswith('bore_') else 48) and len(set(v))==len(v) and all(x is not None for x in indices)
        fingerprint=tuple(sorted(edges));valid=valid and fingerprint not in seen_cycles;seen_cycles.add(fingerprint)
        expected_crease=1. if record['role'].startswith('bore_rim_') else 0.
        valid=valid and record['crease']==expected_crease and record['expected_valence']==4
        continuation=all(len(incident[vj])==4 and all(len(face)==4 and not {v[(j-1)%len(v)],v[(j+1)%len(v)]}<=face for face in incident[vj]) for j,vj in enumerate(v))
        valid=valid and continuation
        anchor=True
        if record['role'].startswith('bore_'):
            try:
                hx,hy=design['hole_center_mm'];r=design['hole_radius_mm'];b=design['edge_roundover_radius_mm'];lo,hi=design['z_range_mm']
                expected_radius=r*3/(2+math.cos(math.tau/24))+((fixed_width if fixed_width is not None else max(2*b,.12*r)) if record['role'].startswith('bore_support_') else 0.)
                z=lo if record['role'].endswith('bottom') else hi
                anchor=all(abs(mesh.vertices[index].co[2]*1000-z)<.00011 and abs(math.hypot(mesh.vertices[index].co[0]*1000-hx,mesh.vertices[index].co[1]*1000-hy)-expected_radius)<.00011 for index in v)
            except (KeyError,TypeError,ValueError):anchor=False
        valid=valid and anchor
        actual=[crease.data[x].value if crease and x is not None else None for x in indices]
        valid=valid and all(x is not None and abs(x-record['crease'])<1e-7 for x in actual) and all(len(adj[x])==record['expected_valence'] for x in v)
        if record['crease']>0:expected_creased.update(x for x in indices if x is not None)
        rows.append({'cycle_id':i,'role':record['role'],'vertices':len(v),'edges':len(edges),'status':'pass' if valid else 'fail','opposite_quad_edge_continuation':continuation,'anchor_tolerance_mm':.00011,'bore_role_geometry_anchor':anchor if record['role'].startswith('bore_') else 'not_applicable','closed_actual_edge_cycle':all(x is not None for x in indices) and len(set(v))==len(v),'valence_histogram':{str(k):sum(len(adj[x])==k for x in v) for k in sorted(set(len(adj[x]) for x in v))},'expected_crease':record['crease'],'actual_crease_range':[min(actual),max(actual)] if actual and all(x is not None for x in actual) else None})
    crease_domain_valid=bool(crease and crease.domain=='EDGE' and crease.data_type=='FLOAT' and len(crease.data)==len(mesh.edges) and all(math.isfinite(x.value) and 0<=x.value<=1 for x in crease.data))
    actual_creased={e.index for e in mesh.edges if crease and crease.data[e.index].value>0}
    return {'status':'pass' if fixed_binding_valid and crease_domain_valid and role_contract and declared and all(x['status']=='pass' for x in rows) and actual_creased==expected_creased else 'fail','fixed_support_parameter_geometry_binding':fixed_binding_valid if fixed_required or not fixed_binding_valid else 'not_applicable','fixed_support_cycle_binding':fixed_cycle_binding,'crease_domain_values_valid':crease_domain_valid,'semantic_role_cardinality_matches':role_contract,'source':'actual object.data edges and crease_edge values','cycles':rows,'actual_nonzero_crease_edges':len(actual_creased),'expected_nonzero_crease_edges':len(expected_creased),'unexpected_crease_edges':sorted(actual_creased-expected_creased),'scope':'authored semantic closed cycles, expected source valence and crease values; no automatic artistic or SubD surface suitability inference'}
