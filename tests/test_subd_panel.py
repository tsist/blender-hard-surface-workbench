"""Neutral SubD-cage fixtures; no private production dimensions."""
import copy,unittest,math,json
from pathlib import Path
from hardsurface import contract
from hardsurface.quad_geometry import build
from hardsurface.quad_quality import validate_mesh,DEFAULT_POLICY
from hardsurface.subd_panel_measure import nominal_distance,measure_panel
from hardsurface.io import RuntimeFailure

BASE={'op':'quad.panel','id':'panel','topology_strategy':'subd_control_cage','size':[96,62],'corner_radius':9,'edge_bevel':.7,'center':[0,0],'z_min':0,'z_max':5.5,'holes':[{'id':'bore','kind':'circle','center':[11,1],'radius':8.5}],'local_patch_bounds':[-7,-15,25,17],'chord_tolerance':.05,'subdivision_cage':{'method':'CATMULL_CLARK','hole_segments':24,'preview_levels':2}}
def cases():
    rows=[]
    for name,delta in [('neutral',{}),('hole_move',{'holes':[{'id':'bore','kind':'circle','center':[9,1],'radius':8.5}]}),('thickness',{'z_max':6.5}),('translated',{'center':[7,-4],'holes':[{'id':'bore','kind':'circle','center':[18,-3],'radius':8.5}],'local_patch_bounds':[0,-19,32,13]}),('alternate',{'size':[104,68],'corner_radius':10,'edge_bevel':.8,'z_max':6.4,'holes':[{'id':'bore','kind':'circle','center':[8,0],'radius':7.5}],'local_patch_bounds':[-10,-17,26,17]})]:
        p=copy.deepcopy(BASE);p.update(delta);rows.append((name,p))
    return rows

class SubDCageTests(unittest.TestCase):
    def test_neutral_source_topology(self):
        for name,p in cases():
            with self.subTest(name=name):
                m=build(p,'neutral');q=validate_mesh([[x*.001 for x in v] for v in m['vertices_mm']],m['faces'],face_provenance=m['face_provenance'])
                self.assertTrue(q['passed'],q['findings']);self.assertEqual(set(m['metadata']['subdivision_cage']['source_valence_histogram']),{'3','4','5'})
                self.assertEqual(len(m['edge_creases']),48);self.assertTrue(all(x[2]==1 for x in m['edge_creases']));self.assertEqual(m['subdivision_modifier']['subdivision_type'],'CATMULL_CLARK')
                self.assertEqual(m,build(p,'neutral'))
    def test_local_edit_invariants(self):
        fixtures=dict(cases());a=build(fixtures['neutral'],'n');b=build(fixtures['hole_move'],'n');self.assertEqual(a['faces'],b['faces']);frame=BASE['local_patch_bounds']
        changed=[i for i,(u,v) in enumerate(zip(a['vertices_mm'],b['vertices_mm'])) if u!=v];self.assertTrue(changed)
        for i in changed:
            for m in(a,b):x,y,z=m['vertices_mm'][i];self.assertTrue(frame[0]<x<frame[2] and frame[1]<y<frame[3])
        c=build(fixtures['thickness'],'n');self.assertEqual(a['faces'],c['faces']);self.assertEqual([v[:2] for v in a['vertices_mm']],[v[:2] for v in c['vertices_mm']])
    def test_schema_integer_and_required_frame(self):
        p=contract._validate(BASE,contract.STEP,'$');self.assertEqual(p['subdivision_cage']['hole_segments'],24)
        s=json.loads((Path(__file__).resolve().parents[1]/'schemas/quad.panel.schema.json').read_text());self.assertEqual(s,contract.schema('quad.panel'))
        q=copy.deepcopy(BASE);q.pop('local_patch_bounds')
        with self.assertRaises(contract.ContractError):contract._validate(q,contract.STEP,'$')
    def test_unsupported_domain_rejects(self):
        for delta in [{'holes':[]},{'edge_bevel':0},{'subdivision_cage':{'hole_segments':48}},{'local_patch_bounds':[-1,-1,1,1]},{'z_max':1.},{'lips':[{'id':'extra'}]}]:
            p=copy.deepcopy(BASE);p.update(delta)
            with self.subTest(delta=delta),self.assertRaises((RuntimeFailure,ValueError)):build(p,'n')
    def test_nominal_analytic_points_and_bad_distance(self):
        for p in [(0,0,0),(48,0,2.75),(11+8.5,1,2.75),(39+9/math.sqrt(2),22+9/math.sqrt(2),2.75)]:self.assertAlmostEqual(nominal_distance(p,BASE),0,places=10)
        self.assertGreater(nominal_distance((49,0,2.75),BASE),.99)
        self.assertAlmostEqual(nominal_distance((11+8.5-.04,1,5.54),BASE),math.sqrt(2)*.04,places=10)
    def test_measurement_rejects_nonfinite_and_empty(self):
        for vertices,faces in [([],[]),([[float('nan'),0,0]]*4,[[0,1,2,3]]),([[0,0,0]]*4,[[0,1,2,9]])]:
            with self.assertRaises(RuntimeFailure):measure_panel(vertices,faces,BASE,tolerance_mm=.05)
    def test_legacy_policy_not_relaxed(self):
        self.assertEqual(DEFAULT_POLICY['max_quad_warpage_degrees'],5.);self.assertEqual(DEFAULT_POLICY['max_quad_plane_distance_m'],1e-7);self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'],100)
if __name__=='__main__':unittest.main()
