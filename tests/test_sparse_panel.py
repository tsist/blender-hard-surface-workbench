"""Neutral numerical qualification; no production dimensions or approvals."""
import copy,json,math,unittest
from pathlib import Path
from unittest import mock
from hardsurface import contract
from hardsurface.quad_geometry import build
from hardsurface.quad_quality import validate_mesh,DEFAULT_POLICY
from hardsurface.quad_panel_sparse import plan_sparse_annulus
from hardsurface.io import RuntimeFailure
from hardsurface.planner import plan_request
from tests.test_quad_public import dimension_witnesses
from tests import test_reference_approval as reference_tests

BASE={'id':'body','op':'quad.panel','center':[0,0],'lips':[],'size':[96,62],'corner_radius':9,'edge_bevel':.7,'z_min':0,'z_max':5.5,'holes':[{'id':'bore','kind':'circle','center':[9,-2],'radius':8.5}],'target_edge_length':10,'chord_tolerance':.045,'max_segments':512,'topology_strategy':'sparse_annulus'}

def cases():
    rows=[]
    for case,delta in [('neutral',{}),('hole_move',{'holes':[{'id':'bore','kind':'circle','center':[5,-2],'radius':8.5}]}),('thickness',{'z_max':6.6}),('target4',{'target_edge_length':4}),('wide',{'size':[106,56],'corner_radius':7,'holes':[{'id':'bore','kind':'circle','center':[13,1],'radius':7}]}),('small_hole',{'holes':[{'id':'bore','kind':'circle','center':[0,0],'radius':5.5}]}),('no_bevel',{'edge_bevel':0}),('translated',{'center':[20,-30],'holes':[{'id':'bore','kind':'circle','center':[29,-32],'radius':8.5}]})]:
        p=copy.deepcopy(BASE);p.update(delta);rows.append((case,p))
    return rows

def points_for(mesh,roles):
    return {tuple(mesh['vertices_mm'][i]) for f,s in zip(mesh['faces'],mesh['face_provenance']) if s['surface_role'] in roles for i in f}

class SparsePanelTests(unittest.TestCase):
    def test_eight_neutral_parameter_cases(self):
        for case,p in cases():
            with self.subTest(case=case):
                m=build(p,'neutral_fixture');g=validate_mesh([[x*.001 for x in v] for v in m['vertices_mm']],m['faces'],face_provenance=m['face_provenance'])
                self.assertTrue(g['passed'],g['findings']);dimension_witnesses(m,p)
                self.assertEqual(g['counts']['triangles'],0);self.assertEqual(g['counts']['ngons'],0)
                sm=m['metadata']['sparse_layout'];self.assertLessEqual(sm['actual_sampling']['coupled_corner_distance_bound_mm'],p['chord_tolerance']+1e-12)
                self.assertEqual(m,build(p,'neutral_fixture'))
    def test_both_quad_diagonals_respect_added_compound_bound(self):
        for name,p in cases():
            if not p['edge_bevel']:continue
            mesh=build(p,'neutral_fixture');bound=mesh['metadata']['sparse_layout']['actual_sampling']['coupled_corner_distance_bound_mm']
            cx,cy=p['center'];w,h=p['size'];r=p['corner_radius'];b=p['edge_bevel'];maximum=0.
            for face,source in zip(mesh['faces'],mesh['face_provenance']):
                if source['surface_role']!='outline_bevel':continue
                v=[mesh['vertices_mm'][i] for i in face]
                for ids in ((0,1,2),(0,2,3),(0,1,3),(1,2,3)):
                    for i in range(6):
                        for j in range(6-i):
                            weights=(i/5,j/5,1-(i+j)/5)
                            x,y,z=[sum(weights[k]*v[ids[k]][d] for k in range(3)) for d in range(3)]
                            qx=abs(x-cx)-w/2+r;qy=abs(y-cy)-h/2+r
                            sdf=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r
                            zc=p['z_min']+b if z<(p['z_min']+p['z_max'])/2 else p['z_max']-b
                            radial=sdf+b
                            phi=max(0.,min(math.pi/2,math.atan2(abs(z-zc),radial)))
                            measured=math.hypot(radial-b*math.cos(phi),abs(z-zc)-b*math.sin(phi));maximum=max(maximum,measured)
            self.assertLessEqual(maximum,bound+1e-9,(name,maximum,bound))
    def test_hole_motion_preserves_actual_outer_coordinate_sets(self):
        m=[build(p,'neutral_fixture') for _,p in cases()[:2]]
        for role in ('outline_bevel','outline_wall'):
            self.assertEqual(points_for(m[0],[role]),points_for(m[1],[role]))
        self.assertEqual(m[0]['faces'],m[1]['faces'])
        self.assertEqual(m[0]['metadata']['sparse_layout']['ring_counts'],m[1]['metadata']['sparse_layout']['ring_counts'])
    def test_thickness_edit_preserves_xy_and_polygon_connectivity(self):
        a=build(cases()[0][1],'neutral_fixture');b=build(cases()[2][1],'neutral_fixture')
        self.assertEqual(a['faces'],b['faces']);self.assertEqual([v[:2] for v in a['vertices_mm']],[v[:2] for v in b['vertices_mm']])
    def test_explicit_tiled_is_legacy_identical(self):
        p=copy.deepcopy(BASE);p.pop('topology_strategy');a=build(p,'neutral_fixture')
        p['topology_strategy']='tiled';self.assertEqual(a,build(p,'neutral_fixture'))
    def test_default_target_comparison_and_hole_provenance(self):
        p=cases()[3][1];a=build(p,'neutral_fixture');p=copy.deepcopy(p);p['topology_strategy']='tiled';b=build(p,'neutral_fixture')
        self.assertLess(len(a['faces']),len(b['faces']))
        self.assertFalse(any(x.get('patch_role')=='hole_density_transition' for x in a['face_provenance']))
        self.assertEqual(a['metadata']['sparse_layout']['transition_counts']['quads_per_reduction_block'],2)
    def test_domain_fails_before_vertex_allocation(self):
        bad=[]
        for changes in ({'holes':[]},{'holes':BASE['holes']*2},{'lips':[{'id':'lip','bounds':[-1,-1,1,1],'z_min':-1}]},{'size':[160,40]},{'chord_tolerance':.0001},{'max_segments':24},{'holes':[{'id':'bore','kind':'circle','center':[40,0],'radius':8.5}]}):
            p=copy.deepcopy(BASE);p.update(changes);bad.append(p)
        with mock.patch('hardsurface.quad_panel.MeshBuilder',side_effect=AssertionError('allocated before domain check')):
            for p in bad:
                with self.subTest(p=p),self.assertRaises((RuntimeFailure,ValueError)):build(p,'neutral_fixture')
    def test_schema_is_explicit_and_unknown_strategy_rejected(self):
        schema=contract.schema('quad.panel')['properties']['topology_strategy']
        self.assertEqual(schema['default'],'tiled');self.assertIn('wall-height',schema['description'])
        p=copy.deepcopy(BASE);p['topology_strategy']='automatic_fallback'
        with self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP,'$')
    def test_quality_policy_not_weakened(self):
        self.assertEqual(DEFAULT_POLICY['max_quad_corner_angle_degrees'],175)
        self.assertEqual(DEFAULT_POLICY['max_aspect_ratio'],50)
        self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'],100)
    def test_external_reference_check_fails_in_preflight_after_valid_reference_gate(self):
        # Existing synthetic test helper creates pinned, clearly synthetic records.
        t=reference_tests.ReferenceApprovalTests();t.setUp()
        for scope in ('unit','quality'):
            req=copy.deepcopy(t.request)
            if scope=='unit':req['params']['work_units'][0]['checks'].append('reference_consistency')
            else:req['params']['quality']['required'].append('reference_consistency')
            with self.assertRaisesRegex(contract.ContractError,'CHECK_UNSUPPORTED'):
                plan_request(req,reference_approval=t.approval)
            self.assertEqual(req['params']['quality']['visual'],'required')

if __name__=='__main__':unittest.main()
