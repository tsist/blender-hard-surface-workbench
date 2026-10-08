"""B3 neutral numerical fixtures, never production dimensions or asset approval."""
import copy,json,math,unittest
from pathlib import Path
from unittest import mock
from hardsurface import contract
from hardsurface.quad_geometry import build
from hardsurface.quad_quality import validate_mesh,DEFAULT_POLICY
from hardsurface.quad_panel_blocks import plan_local_patch_blocks,transition_four_to_two,_quality,_ccw
from hardsurface.io import RuntimeFailure
from tests.test_quad_public import dimension_witnesses

BASE={'lips':[],'op':'quad.panel','id':'neutral','size':[96,62],'center':[0,0],'corner_radius':9,'edge_bevel':.7,'z_min':0,'z_max':5.5,'holes':[{'id':'bore','kind':'circle','center':[11,1],'radius':8.5}],'target_edge_length':8,'chord_tolerance':.045,'max_segments':512,'topology_strategy':'local_patch_blocks','local_patch_bounds':[-7,-15,25,17]}

def cases():
    rows=[]
    for label,delta in [('neutral',{}),('hole_move',{'holes':[{'id':'bore','kind':'circle','center':[7,1],'radius':8.5}]}),('thickness',{'z_max':6.6}),('target4',{'target_edge_length':4}),('no_bevel',{'edge_bevel':0}),('corner6',{'corner_radius':2.8,'chord_tolerance':.05}),('corner12_same_shape',{'corner_radius':2.8}),('hole48',{'chord_tolerance':.04}),('hole64',{'size':[160,112],'corner_radius':6,'holes':[{'id':'bore','kind':'circle','center':[2,1],'radius':20}],'local_patch_bounds':[-37,-37,37,37],'chord_tolerance':.03}),('translated',{'center':[20,-30],'holes':[{'id':'bore','kind':'circle','center':[31,-29],'radius':8.5}],'local_patch_bounds':[13,-45,45,-13]})]:
        p=copy.deepcopy(BASE);p.update(delta);rows.append((label,p))
    return rows


def get_mesh(p):return build(p,'neutral_fixture')

class LocalBlocksTests(unittest.TestCase):
    def test_component_four_to_two_numeric_proof(self):
        ff=_ccw(transition_four_to_two([(x,0.) for x in range(5)],[(0.,2.),(2.,2.),(4.,2.)]))
        self.assertEqual(len(ff),6);q=_quality(ff);self.assertGreater(q['minimum_angle_degrees'],30);self.assertLess(q['maximum_angle_degrees'],150)
    def test_neutral_parameter_cases_actual_mesh_gate_and_dimensions(self):
        for label,p in cases():
            with self.subTest(label=label):
                m=get_mesh(p);g=validate_mesh([[x*.001 for x in v] for v in m['vertices_mm']],m['faces'],face_provenance=m['face_provenance'])
                self.assertTrue(g['passed'],g['findings']);self.assertEqual(g['counts']['triangles'],0);self.assertEqual(g['counts']['ngons'],0);dimension_witnesses(m,p)
                self.assertEqual(m,get_mesh(p));layout=m['metadata']['local_blocks_layout']
                self.assertLessEqual(layout['actual_sampling']['coupled_corner_distance_bound_mm'],p['chord_tolerance']+1e-12)
                self.assertEqual(layout['hole_sampling']['chosen_segments']%16,0)
    def test_exact_local_hole_motion(self):
        a=get_mesh(cases()[0][1]);b=get_mesh(cases()[1][1]);frame=BASE['local_patch_bounds']
        self.assertEqual(a['faces'],b['faces']);changed=[]
        for i,(v,w) in enumerate(zip(a['vertices_mm'],b['vertices_mm'])):
            if v==w:continue
            changed.append(i)
            for q in (v,w):self.assertTrue(frame[0]<q[0]<frame[2] and frame[1]<q[1]<frame[3])
        self.assertTrue(changed)
        for role in ('axial_macroblocks','outline_straight_bands','corner_local_3_to_1','corner_coarse_quads'):
            av=[a['vertices_mm'][i] for f,s in zip(a['faces'],a['face_provenance']) if s.get('patch_role')==role for i in f]
            bv=[b['vertices_mm'][i] for f,s in zip(b['faces'],b['face_provenance']) if s.get('patch_role')==role for i in f]
            self.assertEqual(av,bv)
    def test_thickness_xy_connectivity_bottom_preserved(self):
        a=get_mesh(cases()[0][1]);b=get_mesh(cases()[2][1]);self.assertEqual(a['faces'],b['faces'])
        self.assertEqual([v[:2] for v in a['vertices_mm']],[v[:2] for v in b['vertices_mm']])
        for i,v in enumerate(a['vertices_mm']):
            if v[2]==BASE['z_min']:self.assertEqual(v,b['vertices_mm'][i])
    def test_curve_schedules_are_independent(self):
        a=plan_local_patch_blocks(BASE);p=copy.deepcopy(BASE);p['target_edge_length']=4;b=plan_local_patch_blocks(p)
        self.assertEqual(a['hole'],b['hole']);self.assertEqual(a['metadata']['hole_sampling'],b['metadata']['hole_sampling'])
        self.assertEqual(a['metadata']['outline_sampling']['quarter_arc_segments'],b['metadata']['outline_sampling']['quarter_arc_segments'])
        self.assertNotEqual(a['metadata']['outline_sampling']['total_boundary_segments'],b['metadata']['outline_sampling']['total_boundary_segments'])
    def test_outline_count_changes_without_hole_or_frame_change(self):
        fixtures=dict(cases());a=plan_local_patch_blocks(fixtures['corner6']);b=plan_local_patch_blocks(fixtures['corner12_same_shape'])
        self.assertEqual(a['hole'],b['hole']);self.assertEqual(a['frame'],b['frame'])
        self.assertEqual(a['metadata']['macroblock_schedule'],b['metadata']['macroblock_schedule'])
        self.assertEqual(a['metadata']['outline_sampling']['quarter_arc_segments'],6)
        self.assertEqual(b['metadata']['outline_sampling']['quarter_arc_segments'],12)
    def test_macroblocks_are_rectangular_and_counts_conform(self):
        plan=plan_local_patch_blocks(BASE)
        for name,ff in plan['roles']:
            if name not in ('axial_macroblocks','outline_straight_bands'):continue
            for f in ff:
                for a,b in zip(f,f[1:]+f[:1]):self.assertTrue(a[0]==b[0] or a[1]==b[1])
        self.assertEqual(len(plan['hole']),32);self.assertEqual(len(plan['frame']),16)
        self.assertEqual(plan['metadata']['outline_sampling']['corner_radial_seam_segments'],2)
    def test_domain_rejections_precede_builder_allocation(self):
        changes=[{'holes':[]},{'holes':BASE['holes']*2},{'lips':[{'id':'lip','bounds':[-1,-1,1,1],'z_min':-1}]},{'local_patch_bounds':None},{'local_patch_bounds':[-40,-15,25,17]},{'local_patch_bounds':[-7,-3,25,3]},{'local_patch_bounds':[0,0,2,2]},{'chord_tolerance':.0001},{'max_segments':24},{'target_edge_length':.0001},{'holes':[{'id':'bore','kind':'circle','center':[23,1],'radius':8.5}]},{'holes':[{'id':'bore','kind':'circle','center':[11,1],'radius':8.5,'counterbore':{'radius':10,'depth':1,'side':'top'}}]}]
        with mock.patch('hardsurface.quad_panel.MeshBuilder',side_effect=AssertionError('allocated before domain rejection')):
            for delta in changes:
                p=copy.deepcopy(BASE);p.update(delta)
                with self.subTest(delta=delta),self.assertRaises((RuntimeFailure,ValueError)):get_mesh(p)
    def test_actual_schema_and_export(self):
        schema=contract.schema('quad.panel');self.assertIn('local_patch_blocks',schema['properties']['topology_strategy']['enum']);self.assertIn('local_patch_bounds',schema['properties'])
        validated=contract._validate(BASE,contract.STEP,'$');self.assertEqual(validated['local_patch_bounds'],BASE['local_patch_bounds'])
        export=json.loads((Path(__file__).resolve().parents[1]/'schemas'/'quad.panel.schema.json').read_text());self.assertEqual(export,schema)
    def test_frame_required_only_for_new_mode(self):
        p=copy.deepcopy(BASE);p.pop('local_patch_bounds')
        with self.assertRaises(contract.ContractError):contract._validate(p,contract.STEP,'$')
        for mode in ('tiled','sparse_annulus'):
            p['topology_strategy']=mode;self.assertEqual(contract._validate(p,contract.STEP,'$')['topology_strategy'],mode)
        p.pop('topology_strategy');self.assertEqual(contract._validate(p,contract.STEP,'$')['topology_strategy'],'tiled')
    def test_unchanged_quality_policy(self):
        self.assertEqual(DEFAULT_POLICY['max_quad_corner_angle_degrees'],175);self.assertEqual(DEFAULT_POLICY['max_aspect_ratio'],50);self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'],100)

if __name__=='__main__':unittest.main()
