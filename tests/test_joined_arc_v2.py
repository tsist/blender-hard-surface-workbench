# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral complete-source HOST fixtures for the explicit joined_arc_v2 policy.

Protocol objects below are Python mocks. The finite regular-curve checks do
not evaluate Blender, certify an all-points shape bound, or qualify highlights.
"""
import copy
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
from hardsurface.measurement_binding import bind_constructor_measurement
from hardsurface.quad_panel_subd import plan_subd_cage, _outline_control
from hardsurface.quad_quality import validate_mesh, DEFAULT_POLICY
from hardsurface.structure_native import validate_native_structure
from hardsurface.structure_plan_binding import _parameters
from hardsurface.subd_cage_validation import inspect_control_loops
from test_joined_arc_policy import neutral as v1_neutral, protocol_mock
from test_structure_edit import authored, report

POLICY = 'joined_arc_v2'
ROOT = Path(__file__).resolve().parents[1]


def neutral(name='baseline', policy=POLICY):
    p = v1_neutral(False)
    p['edge_bevel'] = .8
    if policy is not None:
        p['subdivision_cage']['outer_join_policy'] = policy
    if name == 'E1':
        p['holes'][0]['radius'] = 8.8
    elif name == 'E2':
        p['holes'][0]['center'] = [11.5, .5]
    elif name == 'E3':
        p['z_max'] = 6.5
    elif name == 'E4':
        p['edge_bevel'] = .7
    elif name != 'baseline':
        raise ValueError(name)
    return p


def profile_points(mesh, knot):
    vm = mesh['authored_structure']['vertex_map']
    return [mesh['vertices_mm'][vm['profile/%s/slot:%d' % (knot, i)]] for i in range(48)]


class JoinedArcV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meshes = {name: authored(neutral(name)) for name in ('baseline', 'E1', 'E2', 'E3', 'E4')}
        cls.before = report(cls.meshes['baseline'])

    def test_explicit_schema_and_fixed_support_domain(self):
        for policy in ('joined_arc_v1', POLICY):
            p = neutral(policy=policy)
            self.assertEqual(contract._validate(p, contract.STEP)['subdivision_cage']['outer_join_policy'], policy)
            edit._parameter_domain(p)
        omitted = contract._validate(neutral(policy=None), contract.STEP)
        self.assertNotIn('outer_join_policy', omitted['subdivision_cage'])
        for value in ('joined_arc_v3', 'joined_arc', None, True, {}, []):
            p = neutral(); p['subdivision_cage']['outer_join_policy'] = value
            with self.subTest(value=value):
                with self.assertRaises(contract.ContractError): contract._validate(p, contract.STEP)
                with self.assertRaises(RuntimeFailure): plan_subd_cage(p)
                with self.assertRaises(kernel.StructureError): edit._parameter_domain(p)
        p = neutral(); p['subdivision_cage'].pop('bore_support_width')
        with self.assertRaises(contract.ContractError): contract._validate(p, contract.STEP)
        with self.assertRaises(RuntimeFailure): plan_subd_cage(p)
        with self.assertRaises(kernel.StructureError): edit._parameter_domain(p)

    def test_all_five_full_meshes_preserve_topology_roles_and_planar_coordinates(self):
        for name, mesh in self.meshes.items():
            old = authored(neutral(name, policy=None))
            self.assertEqual((len(mesh['vertices_mm']), len(mesh['faces'])), (992, 992))
            for key in ('faces', 'edge_creases', 'control_loops'):
                self.assertEqual(mesh[key], old[key])
            for key in ('vertex_map', 'face_map', 'alias_manifest', 'semantic_control_loops',
                        'semantic_connectivity_sha256', 'semantic_crease_sha256'):
                self.assertEqual(mesh['authored_structure'][key], old['authored_structure'][key])
            changed = []
            for key, index in mesh['authored_structure']['vertex_map'].items():
                if key.startswith('plane/'):
                    self.assertEqual(mesh['vertices_mm'][index], old['vertices_mm'][index])
                if math.dist(mesh['vertices_mm'][index], old['vertices_mm'][index]) > 1e-10:
                    changed.append(key)
            self.assertEqual(len(changed), 256)
            self.assertEqual(len({key.rsplit('/slot:', 1)[0] for key in changed}), 12)
            self.assertTrue(all(key.startswith('profile/') for key in changed))
            for before, after in zip(old['face_provenance'], mesh['face_provenance']):
                self.assertEqual(before, {key: value for key, value in after.items() if key != 'outer_join_policy'})
                if before['curved'] and before['surface_role'] != 'bore_wall':
                    self.assertEqual(after['outer_join_policy'], POLICY)
                else:
                    self.assertNotIn('outer_join_policy', after)
            self.assertEqual(author.validate_authored_identity(mesh)['status'], 'pass')

    def test_all_five_full_source_quality_gates_are_unchanged(self):
        self.assertEqual(DEFAULT_POLICY['max_aspect_ratio'], 50)
        self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'], 100)
        for name, mesh in self.meshes.items():
            q = validate_mesh([[x*.001 for x in v] for v in mesh['vertices_mm']], mesh['faces'],
                              face_provenance=mesh['face_provenance'], include_face_metrics=True)
            with self.subTest(name=name):
                self.assertTrue(q['passed'], q['findings'])
                for row in q['face_metrics']:
                    role = mesh['face_provenance'][row['face_index']]
                    self.assertLessEqual(row['aspect_ratio'], 100 if role['support_band'] else 50)
                for row in mesh['metadata']['subdivision_cage']['quality'].values():
                    self.assertGreaterEqual(row['minimum_angle_degrees'], 5)
                    self.assertLessEqual(row['maximum_angle_degrees'], 175)
                    self.assertLessEqual(row['maximum_aspect_ratio'], 50)

    def test_actual_profile_arrays_have_tangent_pair_and_exact_diagonal_midpoint(self):
        for name in ('baseline', 'E3', 'E4'):
            p = neutral(name); mesh = self.meshes[name]
            b = p['edge_bevel']; a = b*((48/math.sqrt(2)-1)/23-1)
            lo, hi = p['z_min'], p['z_max']; side_y = p['center'][1]-p['size'][1]/2
            # Slot 3 is an ordinary bottom straight control. Read the finished
            # arrays directly; neither constructor nor attestation helpers derive expectations.
            expected = {'lower_roundover/j0': (b, lo), 'lower_roundover/j1': (b-a, lo),
                        'lower_roundover/j2': (0, lo+b-a), 'lower_roundover/j3': (0, lo+b),
                        'upper_roundover/j0': (b, hi), 'upper_roundover/j1': (b-a, hi),
                        'upper_roundover/j2': (0, hi-b+a), 'upper_roundover/j3': (0, hi-b)}
            for knot, (inset, z) in expected.items():
                point = profile_points(mesh, knot)[3]
                self.assertAlmostEqual(point[1], side_y+inset, places=13)
                self.assertAlmostEqual(point[2], z, places=13)
            section = [profile_points(mesh, 'lower_roundover/j%d' % j)[3] for j in range(4)]
            midpoint = [math.fsum(w*q[axis] for w, q in zip((1,23,23,1), section))/48 for axis in (1,2)]
            self.assertAlmostEqual(midpoint[0], side_y+b-b/math.sqrt(2), places=13)
            self.assertAlmostEqual(midpoint[1], lo+b-b/math.sqrt(2), places=13)
            comp = mesh['metadata']['subdivision_cage']['compensation']
            self.assertEqual(comp['roundover_interior_pair']['c_over_b'], 1.)
            self.assertEqual(comp['roundover_interior_pair']['a_over_b'], (48/math.sqrt(2)-1)/23-1)

    def test_actual_eight_joins_on_twelve_loops_satisfy_endpoint_and_midpoint_equations(self):
        for name in ('baseline', 'E4'):
            for shift in ((0, 0), (125, -81), (-140, 93)):
                p = neutral(name); p['center'] = list(shift)
                p['holes'][0]['center'] = [x+d for x,d in zip(p['holes'][0]['center'], shift)]
                p['local_patch_bounds'] = [x+shift[i%2] for i,x in enumerate(p['local_patch_bounds'])]
                mesh = authored(p); w,h = p['size']; r = p['corner_radius']; cx,cy = shift
                corners = ((cx+w/2-r,cy-h/2+r),(cx+w/2-r,cy+h/2-r),
                           (cx-w/2+r,cy+h/2-r),(cx-w/2+r,cy-h/2+r))
                for knot in author.PROFILE_KNOTS:
                    points = profile_points(mesh, knot); rho = corners[0][1]-points[3][1]
                    for side, center in enumerate(corners):
                        for endpoint, direction in ((side*12+6,1),(((side+1)*12)%48,-1)):
                            controls = [points[(endpoint+j*direction)%48] for j in (-1,0,1,2)]
                            guard, tangent = controls[:2]
                            normal_axis = 1-side%2 if direction==1 else side%2
                            tangent_axis = 1-normal_axis
                            # E=1: the tangent endpoint stays on the adjacent
                            # actual straight guard plane, with zero normal offset.
                            self.assertAlmostEqual(tangent[normal_axis], guard[normal_axis], delta=1e-12)
                            self.assertAlmostEqual(abs(tangent[normal_axis]-center[normal_axis]), rho, delta=1e-12)
                            midpoint = [math.fsum(w*q[axis] for w,q in zip((1,23,23,1),controls))/48 for axis in (0,1)]
                            self.assertAlmostEqual(math.dist(midpoint,center), rho, delta=1e-12)
                            # Solve the circle from the observed normal midpoint
                            # and the other three source controls, independently
                            # of the production tau helper and its guard lookup.
                            normal = midpoint[normal_axis]-center[normal_axis]
                            sign = 1 if controls[2][tangent_axis]>center[tangent_axis] else -1
                            target = center[tangent_axis]+sign*math.sqrt(rho*rho-normal*normal)
                            solved = (48*target-controls[0][tangent_axis]-23*controls[2][tangent_axis]-controls[3][tangent_axis])/23
                            self.assertAlmostEqual(tangent[tangent_axis], solved, delta=2e-12)

    def test_actual_loops_are_convex_and_sampled_join_curvature_has_no_reversal(self):
        for name in ('baseline', 'E4'):
            for knot in author.PROFILE_KNOTS:
                points = profile_points(self.meshes[name], knot)
                edges = [(points[(i+1)%48][0]-q[0],points[(i+1)%48][1]-q[1]) for i,q in enumerate(points)]
                for i,a in enumerate(edges):
                    b = edges[(i+1)%48]
                    self.assertGreaterEqual(a[0]*b[1]-a[1]*b[0], -1e-10)
                for join in range(0,48,6):
                    for step in range(49):
                        u = join-1.5+step/16; span = math.floor(u); t = u-span
                        local = [points[(span+j)%48] for j in (-1,0,1,2)]
                        d = (-(1-t)**2/2, (9*t*t-12*t)/6, (-9*t*t+6*t+3)/6, t*t/2)
                        dd = (1-t,3*t-2,1-3*t,t)
                        v = [math.fsum(w*q[axis] for w,q in zip(d,local)) for axis in (0,1)]
                        acc = [math.fsum(w*q[axis] for w,q in zip(dd,local)) for axis in (0,1)]
                        speed = math.hypot(*v)
                        self.assertGreater(speed, 1e-8)
                        self.assertGreaterEqual((v[0]*acc[1]-v[1]*acc[0])/speed**3, -1e-10)

    def test_independent_attestation_rejects_v1_coordinates_and_mutation(self):
        mesh = self.meshes['baseline']
        with patch('hardsurface.quad_panel_subd.build_subd_panel', side_effect=AssertionError('No constructor')):
            edit._verify_parameter_coordinates(mesh)
        for mode in ('v1_arrays','endpoint','profile'):
            changed = copy.deepcopy(mesh)
            if mode == 'v1_arrays':
                changed['vertices_mm'] = authored(neutral(policy='joined_arc_v1'))['vertices_mm']
            else:
                knot, slot, axis = ('bottom_flat_guard',6,1) if mode=='endpoint' else ('lower_roundover/j1',3,2)
                index = changed['authored_structure']['vertex_map']['profile/%s/slot:%d'%(knot,slot)]
                changed['vertices_mm'][index] = list(changed['vertices_mm'][index]); changed['vertices_mm'][index][axis] += .001
            with self.subTest(mode=mode), self.assertRaises(RuntimeFailure): author.validate_authored_identity(changed)

    def test_predeclared_edits_and_e4_exact_nine_region_168_face_protection(self):
        for name in ('E1','E2','E3','E4'):
            p = neutral(name)
            plan = edit.plan_authored_edit(self.before['witness'],self.before['authorship'],p,
                                           datum_policy='fixed_bottom' if name=='E3' else 'not_requested')
            proof = edit.verify_authored_edit(self.before,report(self.meshes[name]),plan)
            self.assertEqual(proof['status'],'pass'); self.assertEqual(proof['qualification'],'not_run')
            if name=='E4':
                rows = proof['protected_hole_regions']['regions']
                self.assertEqual(len(rows),9); self.assertEqual(sum(row['face_count'] for row in rows),168)
                before,after = self.meshes['baseline'],self.meshes['E4']
                roles = {'bore_wall'}|{side+':'+role for side in ('bottom','top') for role in
                    ('bore_planar_support','bore_planar_buffer','planar_3_to_1','fixed_frame_buffer')}
                ids = [i for i,row in enumerate(before['face_provenance']) if row['surface_role'] in roles]
                self.assertEqual(len(ids),168)
                self.assertEqual({before['face_provenance'][i]['surface_role'] for i in ids},roles)
                for i in ids:
                    self.assertEqual(before['faces'][i],after['faces'][i])
                    self.assertEqual(before['face_provenance'][i],after['face_provenance'][i])
                    for vertex in before['faces'][i]: self.assertEqual(before['vertices_mm'][vertex],after['vertices_mm'][vertex])
                self.assertEqual(before['edge_creases'],after['edge_creases'])
                self.assertEqual(edit._hole_region_signatures(self.before['witness']),edit._hole_region_signatures(report(after)['witness']))

    def test_policy_migration_is_rejected_without_mutating_the_source(self):
        for source,target in ((None,POLICY),(POLICY,None),('joined_arc_v1',POLICY),(POLICY,'joined_arc_v1')):
            mesh = authored(neutral(policy=source)); saved = copy.deepcopy(mesh)
            p = neutral(policy=target)
            with self.assertRaises(RuntimeFailure): author.plan_parameter_edit(mesh,p)
            before = report(mesh)
            with self.assertRaises(kernel.StructureError): edit.plan_authored_edit(before['witness'],before['authorship'],p,datum_policy='not_requested')
            self.assertEqual(mesh,saved)

    def test_native_protocol_mock_binds_v2_and_rejects_wrong_or_missing_policy(self):
        obj,mesh = protocol_mock(neutral())
        self.assertEqual(inspect_control_loops(obj)['status'],'pass')
        bound = validate_native_structure(obj,unit_scale=1.)
        self.assertEqual(bind_constructor_measurement(mesh['metadata']['subdivision_cage'],bound)['technical_tolerance_mm'],.05)
        for policy in (None,'joined_arc_v1'):
            obj,_ = protocol_mock(neutral()); metadata=json.loads(obj['hs_quad_metadata'])
            if policy is None: metadata['subdivision_cage']['control_policy'].pop('outer_join_policy')
            else: metadata['subdivision_cage']['control_policy']['outer_join_policy']=policy
            obj['hs_quad_metadata']=json.dumps(metadata)
            self.assertEqual(inspect_control_loops(obj)['status'],'fail')
        obj,_=protocol_mock(neutral(policy='joined_arc_v1')); metadata=json.loads(obj['hs_quad_metadata'])
        metadata['subdivision_cage']['control_policy']['outer_join_policy']=POLICY; obj['hs_quad_metadata']=json.dumps(metadata)
        self.assertEqual(inspect_control_loops(obj)['status'],'fail')

    def test_structure_request_binds_exact_version_and_requires_support_width(self):
        request=json.loads((ROOT/'fixtures/structure-plan.valid.json').read_text())
        request['design']['edge_roundover_mm']=.8
        request['evaluation']['levels']=[2]; request['topology']['bore_support_width_mm']=1.4
        request['topology']['outer_join_policy']=POLICY
        structure_contract.validate_request(request)
        result=_parameters(request,self.meshes['baseline']['authored_structure'])
        self.assertIn('topology.outer_join_policy',result['checked_fields'])
        for policy in (None,'joined_arc_v1'):
            q=copy.deepcopy(request)
            if policy is None:q['topology'].pop('outer_join_policy')
            else:q['topology']['outer_join_policy']=policy
            with self.assertRaises(kernel.StructureError):_parameters(q,self.meshes['baseline']['authored_structure'])
        request['topology'].pop('bore_support_width_mm')
        with self.assertRaises(structure_contract.ContractError):structure_contract.validate_request(request)

    def test_unsupported_guard_domains_fail_closed_and_narrow_roundover_fails_quality(self):
        layout=plan_subd_cage(neutral())['outline_layout']
        for mode in ('zero_radius','zero_guard','collapsed_guard','excess_guard'):
            q=copy.deepcopy(layout); inset=0
            if mode=='zero_radius':inset=q['radius']
            elif mode=='zero_guard':q['ys'][-2]=q['core'][3]
            elif mode=='collapsed_guard':q['ys'][-2]=q['core'][3]-1e-10
            else:q['ys'][-2]=q['core'][3]-100*q['radius']
            with self.subTest(mode=mode),self.assertRaises(RuntimeFailure):_outline_control(q,inset,POLICY)
        p=neutral();p['edge_bevel']=.65;mesh=authored(p)
        q=validate_mesh([[x*.001 for x in v] for v in mesh['vertices_mm']],mesh['faces'],face_provenance=mesh['face_provenance'])
        self.assertFalse(q['passed'])
        self.assertTrue(any(row['code']=='ASPECT_RATIO_EXCEEDED' and row['maximum']==100 for row in q['findings']))
        # This same neutral case passed v1. V2's source-quality domain is
        # narrower here; version opt-in must not imply universal replacement.
        p['subdivision_cage']['outer_join_policy']='joined_arc_v1'; old=authored(p)
        old_quality=validate_mesh([[x*.001 for x in v] for v in old['vertices_mm']],old['faces'],face_provenance=old['face_provenance'])
        self.assertTrue(old_quality['passed'],old_quality['findings'])


if __name__=='__main__':unittest.main()
