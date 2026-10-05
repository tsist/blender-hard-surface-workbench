import json
import os
import unittest
from pathlib import Path
from hardsurface.solver import *


class ProtocolTests(unittest.TestCase):
    def test_status_and_dof(self):
        r=normalize_result({'result':0,'dof':0,'nbad':0});self.assertEqual(r['dof_status'],'known')
        for code in (1,2,3):
            r=normalize_result({'result':code,'dof':0,'nbad':0});self.assertIsNone(r['dof']);self.assertEqual(r['dof_status'],'unknown')
        self.assertEqual(normalize_result({'result':4,'dof':0})['backend_status'],'redundant_solved')
    def test_unknown_dof(self):
        for dof in (-1,None):self.assertEqual(normalize_result({'result':0,'dof':dof})['dof_status'],'unknown')
    def test_diagnostic_mapping_not_minimum_conflict(self):
        r=normalize_result(({'result':1,'dof':0,'nbad':3},[10,11,99]),{10:'width',11:'width'})
        self.assertEqual(r['diagnostic_constraint_ids'],['width']);self.assertEqual(r['unmapped_diagnostic_handle_count'],1)
        self.assertEqual(r['diagnostic_kind'],'related_or_suspected_constraints')
    def test_result_protocol_fail_closed(self):
        for raw in ({'result':99},[],{'result':'0'},{'result':True}):
            with self.assertRaises(SolverError):normalize_result(raw)
    def test_json_duplicate_nonfinite_and_budget(self):
        for raw in (b'{"a":1,"a":2}',b'{"x":NaN}',b'{"x":1e999}'):
            with self.assertRaises(SolverError):strict_json(raw)
        with self.assertRaises(SolverError):strict_json(b'{}',1)
    def test_missing_backend_not_seed_success(self):
        r=solve_sketch({'entities':[],'constraints':[],'profiles':[]},{})
        self.assertFalse(r['accepted']);self.assertEqual(r['status'],'backend_unavailable')
    def test_invalid_deployment_hash(self):
        with self.assertRaises(SolverError):validate_deployment({'python_executable':'python','package_dir':'relative','wheel_path':'x'})


@unittest.skipUnless(os.environ.get('HS_SLVS_CONFIG'),'Approved pinned slvs deployment is not configured')
class NativeIntegrationTests(unittest.TestCase):
    """Real backend tests; no synthetic return is solver qualification."""
    def setUp(self):self.config=json.loads(Path(os.environ['HS_SLVS_CONFIG']).read_text())
    def test_fixture_matrix(self):
        fixtures=sorted((Path(__file__).resolve().parent.parent/'fixtures').glob('solver_*.json'))
        self.assertGreaterEqual(len(fixtures),17,'Native matrix must not pass vacuously')
        for path in fixtures:
            fixture=json.loads(path.read_text())
            with self.subTest(fixture=path.name):
                result=solve_sketch(fixture['sketch'],fixture['dimension_values'],config=self.config,request_id=path.stem)
                self.assertEqual(result['status'],fixture['expected_status'],result)
                if fixture['expected_status']=='verified_solved':self.assertTrue(result['accepted']);self.assertEqual(result['verification']['status'],'pass')

    def test_last_good_mirror_rejected(self):
        f=json.loads((Path(__file__).resolve().parent.parent/'fixtures'/'solver_triangle_distance.json').read_text())
        good=solve_sketch(f['sketch'],f['dimension_values'],config=self.config)
        self.assertTrue(good['accepted'],good)
        f['sketch']['entities'][2]['seed'][1]*=-1
        mirrored=solve_sketch(f['sketch'],f['dimension_values'],config=self.config,last_good=good['solved_sketch'])
        self.assertEqual(mirrored['status'],'branch_changed',mirrored)


if __name__=='__main__':unittest.main()

class CandidateBoundaryTests(unittest.TestCase):
    def test_only_seed_changes_allowed(self):
        before={'entities':[{'id':'p','kind':'point2d','seed':[0,0]}],'constraints':[],'profiles':[]}
        after={'entities':[{'id':'p','kind':'point2d','seed':[1,2]}],'constraints':[],'profiles':[]}
        verify_candidate_identity(before,after)
        after['entities'][0]['id']='different'
        with self.assertRaises(SolverError):verify_candidate_identity(before,after)
    def test_changed_constraints_rejected(self):
        before={'entities':[],'constraints':[{'id':'fixed','type':'point_fixed2d'}]}
        with self.assertRaises(SolverError):verify_candidate_identity(before,{'entities':[],'constraints':[]})


class ExpansionBudgetTests(unittest.TestCase):
    def test_contract_valid_helper_expansion_rejected_before_backend(self):
        from hardsurface.contract import validate_sketch
        points=[{'id':'p'+str(i),'kind':'point2d','seed':[i,0]} for i in range(128)]
        sketch={'id':'expansion','workplane':{'kind':'datum','id':'world_xy'},'entities':points,
                'constraints':[{'id':'spacing'+str(i),'type':'equal_spacing','points':[p['id'] for p in points],'axis':'X','signed':True} for i in range(6)],'profiles':[]}
        validate_sketch(sketch)
        # No backend configuration: this proves the public preflight rejects
        # before deployment validation, native import, or helper allocation.
        result=solve_sketch(sketch,{})
        self.assertEqual(result['status'],'solver_expansion_budget',result)
        self.assertFalse(result['accepted'])
        self.assertIn('equations=2292',result['error']['message'])
    def test_reference_constraints_do_not_expand(self):
        sketch={'entities':[{'id':'p','kind':'point2d','seed':[0,0]}],
                'constraints':[{'id':'r','type':'point_fixed2d','point':'p','at':[0,0],'mode':'reference'}]}
        estimate=estimate_native_expansion(sketch)
        self.assertEqual(estimate['native_parameters_upper_bound'],2)
        self.assertEqual(estimate['native_equations_upper_bound'],0)
    def test_all_authored_native_fixtures_within_expansion_budget(self):
        fixtures=list((Path(__file__).resolve().parent.parent/'fixtures').glob('solver_*.json'))
        self.assertGreaterEqual(len(fixtures),19)
        for path in fixtures:
            with self.subTest(path=path.name):estimate_native_expansion(json.loads(path.read_text())['sketch'])
