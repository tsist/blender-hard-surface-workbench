# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral host fixtures for explicit joined_arc_v1; no native qualification.

Tests use complete source meshes and actual arrays/protocol mocks. Uniform
B-spline midpoint identities are equations, not visual or all-points evidence.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from hardsurface import contract, structure_contract
from hardsurface import structure_edit as edit
from hardsurface import structure_kernel as kernel
from hardsurface import subd_panel_identity as author
from hardsurface.io import RuntimeFailure
from hardsurface.quad_geometry import build
from hardsurface.quad_panel_subd import plan_subd_cage, _outline_control
from hardsurface.quad_quality import validate_mesh, DEFAULT_POLICY
from hardsurface.subd_cage_validation import inspect_control_loops
from hardsurface.structure_native import write_authored_identity
from hardsurface.structure_plan_binding import _parameters
from test_structure_edit import BASE, authored, report
from test_structure_native_contract import Mesh, Object, f32
from test_subd_panel import cases

ROOT=Path(__file__).resolve().parents[1]
POLICY='joined_arc_v1'


def neutral(joined=True):
    p=copy.deepcopy(BASE);p['subdivision_cage']['bore_support_width']=1.4
    if joined:p['subdivision_cage']['outer_join_policy']=POLICY
    return p


def protocol_mock(p):
    result=authored(p);obj=Object(Mesh(result['vertices_mm'],result['faces']))
    obj['hs_quad_surface_table']=json.dumps(result['face_provenance'])
    obj['hs_quad_construction_sha256']=result['construction_sha256']
    obj['hs_quad_metadata']=json.dumps(result['metadata'])
    obj['hs_subd_control_loops']=json.dumps(result['control_loops'])
    provenance=obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
    for i,row in enumerate(provenance.data):row.value=i
    creases=obj.data.attributes.new('crease_edge','FLOAT','EDGE')
    weights={tuple(sorted((a,b))):value for a,b,value in result['edge_creases']}
    for edge,row in zip(obj.data.edges,creases.data):row.value=f32(weights.get(tuple(edge.vertices),0.))
    write_authored_identity(obj,result,unit_scale=1.)
    return obj,result


class JoinedArcPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=neutral();cls.mesh=authored(cls.p);cls.before=report(cls.mesh)

    def test_omission_retains_five_full_legacy_byte_goldens(self):
        goldens=json.loads((ROOT/'fixtures/subd-legacy-dev4-signatures.json').read_text())
        by_name={row['case']:row for row in goldens['cases']}
        for name,p in cases():
            with self.subTest(name=name):
                mesh=build(p,'test-feature');golden=by_name[name]
                projection={key:mesh[key] for key in golden['legacy_keys']}
                actual=hashlib.sha256(json.dumps(projection,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                self.assertEqual(actual,golden['legacy_complete_canonical_sha256'])
                self.assertEqual(mesh['construction_sha256'],golden['construction_sha256'])
                self.assertNotIn('outer_join_policy',mesh['metadata']['subdivision_cage']['control_policy'])

    def test_only_twelve_profile_rings_change_and_complete_schedule_is_fixed(self):
        old=authored(neutral(False));new=self.mesh
        self.assertEqual((len(new['vertices_mm']),len(new['faces'])),(992,992))
        for key in ('faces','edge_creases','control_loops'):self.assertEqual(old[key],new[key])
        for key in ('vertex_map','face_map','semantic_control_loops','alias_manifest','semantic_connectivity_sha256','semantic_crease_sha256'):
            self.assertEqual(old['authored_structure'][key],new['authored_structure'][key])
        changed=[]
        for key,index in new['authored_structure']['vertex_map'].items():
            if old['vertices_mm'][index]!=new['vertices_mm'][index]:
                self.assertTrue(key.startswith('profile/'));changed.append(key)
        self.assertEqual(len({key.rsplit('/slot:',1)[0] for key in changed}),12)
        self.assertNotEqual(old['construction_sha256'],new['construction_sha256'])
        self.assertNotEqual(old['authorship_sha256'],new['authorship_sha256'])
        self.assertEqual(author.validate_authored_identity(new)['status'],'pass')

    def test_profile_pair_and_exact_diagonal_midpoint(self):
        p=self.p;b=p['edge_bevel'];a=b*((48/math.sqrt(2)-1)/23-19/20);c=19*b/20
        self.assertAlmostEqual((b+23*(a+c))/48,b/math.sqrt(2),places=14)
        z=edit._profile_z(p);lo,hi=p['z_min'],p['z_max']
        self.assertEqual(z['lower_roundover/j1'],lo+b-c)
        self.assertEqual(z['lower_roundover/j2'],lo+b-a)
        self.assertEqual(z['upper_roundover/j1'],hi-b+c)
        self.assertEqual(z['upper_roundover/j2'],hi-b+a)
        old=edit._profile_z(neutral(False))
        for key in z:
            if key.endswith(('/j1','/j2')):continue
            self.assertEqual(z[key],old[key])
        # Convex control turns in the local positive radial/upward section.
        section=[(-b/2,0),(0,0),(a,b-c),(c,b-a),(b,b),(b,1.5*b)]
        edges=[(y[0]-x[0],y[1]-x[1]) for x,y in zip(section,section[1:])]
        self.assertTrue(all(u[0]*v[1]-u[1]*v[0]>=-1e-14 for u,v in zip(edges,edges[1:])))

    def test_eight_signed_tangents_use_actual_guard_and_exact_arc_midpoint(self):
        layout=plan_subd_cage(self.p)['outline_layout'];core=layout['core'];f=3/(2+math.cos(math.pi/12));e=(2+f)/3
        # Test translated layouts so absolute-origin sign shortcuts fail.
        for dx,dy in ((0,0),(125,-81)):
            translated=copy.deepcopy(layout)
            translated['core']=[core[0]+dx,core[1]+dy,core[2]+dx,core[3]+dy]
            translated['xs']=[x+dx for x in layout['xs']];translated['ys']=[y+dy for y in layout['ys']]
            cx=(translated['core'][0]+translated['core'][2])/2;cy=(translated['core'][1]+translated['core'][3])/2
            for inset in (0,self.p['edge_bevel'],1.5*self.p['edge_bevel']):
                rho=layout['radius']-inset;points=_outline_control(translated,inset,POLICY)
                baseline=_outline_control(layout,inset,POLICY)
                for point,base in zip(points,baseline):
                    self.assertAlmostEqual(point[0],base[0]+dx,places=12);self.assertAlmostEqual(point[1],base[1]+dy,places=12)
                for side in range(4):
                    for slot,xnormal in ((side*12,side%2==1),(side*12+6,side%2==1)):
                        point=points[slot];axis=1 if xnormal else 0
                        positive=(point[1]>cy) if xnormal else (point[0]>cx)
                        d=((core[3]-layout['ys'][-2] if positive else layout['ys'][1]-core[1]) if xnormal else
                           (core[2]-layout['xs'][-2] if positive else layout['xs'][1]-core[0]))
                        m=(1+23*e+23*f*math.cos(math.pi/12)+f*math.cos(math.pi/6))/48
                        tau=(48*math.sqrt(1-m*m)+d/rho-23*f*math.sin(math.pi/12)-f*math.sin(math.pi/6))/23
                        corner=translated['core'][(3 if positive else 1) if xnormal else (2 if positive else 0)]
                        self.assertAlmostEqual(point[axis],corner+(1 if positive else -1)*rho*tau,places=12)
                        tangential=(-d/rho+23*tau+23*f*math.sin(math.pi/12)+f*math.sin(math.pi/6))/48
                        self.assertAlmostEqual(m*m+tangential*tangential,1,places=14)

    def test_schema_is_opt_in_and_requires_explicit_width(self):
        p=neutral(False);self.assertNotIn('outer_join_policy',contract._validate(p,contract.STEP)['subdivision_cage'])
        self.assertEqual(contract._validate(self.p,contract.STEP)['subdivision_cage']['outer_join_policy'],POLICY)
        for value in ('convex','legacy',None,1,True,{},[]):
            p=neutral();p['subdivision_cage']['outer_join_policy']=value
            with self.subTest(value=value),self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP)
            with self.assertRaises(RuntimeFailure):plan_subd_cage(p)
            with self.assertRaises(kernel.StructureError):edit._parameter_domain(p)
        p=neutral();p['subdivision_cage'].pop('bore_support_width')
        with self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP)
        with self.assertRaises(RuntimeFailure):plan_subd_cage(p)
        with self.assertRaises(kernel.StructureError):edit._parameter_domain(p)
        for strategy in ('tiled','sparse_annulus','local_patch_blocks'):
            p=neutral();p['topology_strategy']=strategy
            with self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP)
        try:from jsonschema import Draft202012Validator
        except ImportError:return
        schema=Draft202012Validator(contract.schema('quad.panel'))
        self.assertTrue(schema.is_valid(neutral()))
        p=neutral();p['subdivision_cage'].pop('bore_support_width');self.assertFalse(schema.is_valid(p))

    def test_bad_radius_guard_or_tangent_order_is_rejected(self):
        layout=plan_subd_cage(self.p)['outline_layout']
        for mode in ('zero_radius','zero_guard','excess_guard'):
            q=copy.deepcopy(layout);inset=0
            if mode=='zero_radius':inset=q['radius']
            elif mode=='zero_guard':q['ys'][-2]=q['core'][3]
            else:q['ys'][-2]=q['core'][3]-100*q['radius']
            with self.subTest(mode=mode),self.assertRaises(RuntimeFailure):_outline_control(q,inset,POLICY)

    def test_policy_is_bound_to_source_and_provenance_without_role_reclassification(self):
        old=authored(neutral(False));m=self.mesh
        self.assertEqual(m['metadata']['subdivision_cage']['control_policy']['outer_join_policy'],POLICY)
        for before,after in zip(old['face_provenance'],m['face_provenance']):
            self.assertEqual(before,{key:value for key,value in after.items() if key!='outer_join_policy'})
            if after['surface_role']=='bore_wall' or not after['curved']:
                self.assertNotIn('outer_join_policy',after)
            else:self.assertEqual(after['outer_join_policy'],POLICY)
        q=validate_mesh([[x*.001 for x in v] for v in m['vertices_mm']],m['faces'],face_provenance=m['face_provenance'],include_face_metrics=True)
        self.assertTrue(q['passed'],q['findings'])
        self.assertEqual(DEFAULT_POLICY['max_aspect_ratio'],50);self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'],100)
        for row in q['face_metrics']:
            provenance=m['face_provenance'][row['face_index']]
            self.assertLessEqual(row['aspect_ratio'],100 if provenance['support_band'] else 50)

    def test_independent_attestation_detects_legacy_shape_and_tampered_in_scope_coordinate(self):
        with patch('hardsurface.quad_panel_subd.build_subd_panel',side_effect=AssertionError('No constructor in attestation')):
            edit._verify_parameter_coordinates(self.mesh)
        altered=copy.deepcopy(self.mesh);altered['vertices_mm']=authored(neutral(False))['vertices_mm']
        with self.assertRaises(RuntimeFailure):author.validate_authored_identity(altered)
        altered=copy.deepcopy(self.mesh);index=altered['authored_structure']['vertex_map']['profile/lower_roundover/j1/slot:6']
        altered['vertices_mm'][index]=list(altered['vertices_mm'][index]);altered['vertices_mm'][index][0]+=.001
        with self.assertRaises(RuntimeFailure):author.validate_authored_identity(altered)

    def test_mock_native_cycle_binding_rejects_copied_or_missing_policy_metadata(self):
        obj,_=protocol_mock(self.p);self.assertEqual(inspect_control_loops(obj)['status'],'pass')
        metadata=json.loads(obj['hs_quad_metadata']);metadata['subdivision_cage']['control_policy'].pop('outer_join_policy')
        obj['hs_quad_metadata']=json.dumps(metadata);self.assertEqual(inspect_control_loops(obj)['status'],'fail')
        obj,_=protocol_mock(neutral(False));metadata=json.loads(obj['hs_quad_metadata'])
        metadata['subdivision_cage']['control_policy']['outer_join_policy']=POLICY;obj['hs_quad_metadata']=json.dumps(metadata)
        self.assertEqual(inspect_control_loops(obj)['status'],'fail')

    def test_ordinary_edits_retain_lineage_and_roundover_protects_nine_hole_regions(self):
        for name in ('E1','E2','E3','E4'):
            p=neutral();datum='not_requested'
            if name=='E1':p['holes'][0]['radius']=8.8
            elif name=='E2':p['holes'][0]['center']=[11.5,.5]
            elif name=='E3':p['z_max']=6.5;datum='fixed_bottom'
            else:p['edge_bevel']=.65
            with self.subTest(name=name):
                plan=edit.plan_authored_edit(self.before['witness'],self.before['authorship'],p,datum_policy=datum)
                mesh=authored(p);after=report(mesh);proof=edit.verify_authored_edit(self.before,after,plan)
                self.assertEqual(proof['status'],'pass');self.assertEqual(proof['qualification'],'not_run')
                quality=validate_mesh([[x*.001 for x in v] for v in mesh['vertices_mm']],mesh['faces'],face_provenance=mesh['face_provenance'])
                self.assertTrue(quality['passed'],quality['findings'])
                if name=='E4':
                    self.assertEqual(edit._hole_region_signatures(self.before['witness']),edit._hole_region_signatures(after['witness']))
                    regions=proof['protected_hole_regions']['regions'];self.assertEqual(len(regions),9)
                    self.assertEqual(sum(row['face_count'] for row in regions),168)

    def test_narrow_neutral_roundover_still_fails_unchanged_full_source_gate(self):
        p=neutral();p['edge_bevel']=.6;mesh=authored(p)
        result=validate_mesh([[x*.001 for x in v] for v in mesh['vertices_mm']],mesh['faces'],face_provenance=mesh['face_provenance'])
        self.assertFalse(result['passed'])
        self.assertTrue(any(row['code']=='ASPECT_RATIO_EXCEEDED' and row['maximum']==100 for row in result['findings']))

    def test_policy_migration_is_rejected_in_both_directions(self):
        for before,p in ((self.mesh,neutral(False)),(authored(neutral(False)),neutral())):
            with self.assertRaises(RuntimeFailure):author.plan_parameter_edit(before,p)
            r=report(before)
            with self.assertRaises(kernel.StructureError):edit.plan_authored_edit(r['witness'],r['authorship'],p,datum_policy='not_requested')

    def test_structure_plan_binds_policy_exactly(self):
        request=json.loads((ROOT/'fixtures/structure-plan.valid.json').read_text())
        request['evaluation']['levels']=[2];request['topology']['bore_support_width_mm']=1.4
        request['topology']['outer_join_policy']=POLICY
        structure_contract.validate_request(request)
        result=_parameters(request,self.mesh['authored_structure'])
        self.assertIn('topology.outer_join_policy',result['checked_fields'])
        request['topology'].pop('outer_join_policy')
        with self.assertRaises(kernel.StructureError):_parameters(request,self.mesh['authored_structure'])
        request['topology']['outer_join_policy']=POLICY;request['topology'].pop('bore_support_width_mm')
        with self.assertRaises(structure_contract.ContractError):structure_contract.validate_request(request)


if __name__=='__main__':unittest.main()
