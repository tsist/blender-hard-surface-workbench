# SPDX-License-Identifier: GPL-3.0-or-later
"""Only deliberately authored synthetic HOST fixtures; no native solver inputs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hardsurface import research_evidence as r


def fixture():
    vertices = [[-1,-1,-1], [1,-1,-1], [1,1,-1], [-1,1,-1],
                [-1,-1,1], [1,-1,1], [1,1,1], [-1,1,1]]
    source = {'vertices': vertices, 'faces': [[0,3,2,1], [4,5,6,7], [0,1,5,4],
                                             [1,2,6,5], [2,3,7,6], [3,0,4,7]],
              'vertex_ids': ['synthetic-v'+str(i) for i in range(8)]}
    model = {'variable_count': 1, 'basis': [[0] for _ in range(24)],
             'inequalities': [{'a': [1], 'b': 1}], 'equalities': [{'a': [1], 'b': 0}],
             'bounds': [[-1,1]]}
    request = {'schema_version': 'research-evidence/1.0', 'source': source,
               'source_sha256': r.canonical_sha(source), 'model': model,
               'external_lp': {'source_sha256': r.canonical_sha(source),
                               'model_sha256': r.canonical_sha(model), 'status': 'feasible', 'q': [0]},
               'protected_vertex_ids': list(source['vertex_ids']),
               'coverage': {'origin': [0,0,0], 'cell_size': 1, 'required_bins': vertices},
               'quality': {'max_warp_degrees': 5, 'min_corner_degrees': 30, 'max_edge_ratio': 2},
               'budgets': {'wall_seconds': 10, 'operations': 100000}, 'residual_tolerance': 1e-9}
    return request


def rebind(request):
    request['source_sha256'] = r.canonical_sha(request['source'])
    request['external_lp']['source_sha256'] = request['source_sha256']
    request['external_lp']['model_sha256'] = r.canonical_sha(request['model'])
    return request


class ResearchEvidenceTests(unittest.TestCase):
    def test_closed_cube_remains_incomplete_without_intersection(self):
        result = r.evaluate(fixture())
        self.assertEqual(result['status'], 'incomplete', result)
        self.assertEqual(result['self_intersection']['status'], 'not_run')
        for gate in ('topology', 'quality', 'coverage', 'protected_identity'):
            self.assertEqual(result['geometry'][gate]['status'], 'pass', result)
        self.assertEqual(result['geometry']['quality']['rms_warp_both_diagonals_degrees'], 0)
        self.assertGreater(result['budget']['operations_used'], 0)
        self.assertEqual(result['external_lp']['optimality'], 'not_run')

    def test_no_source_identity(self):
        request = fixture()
        del request['source_sha256']
        with self.assertRaises(ValueError):
            r.evaluate(request)

    def test_wrong_source_identity(self):
        request = fixture()
        request['source']['vertices'][0][0] = -2
        self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_wrong_external_model_identity(self):
        request = fixture()
        request['model']['basis'][0] = [1]
        self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_infeasible_is_not_invented_geometry_failure(self):
        request = fixture()
        request['external_lp']['status'] = 'infeasible'
        del request['external_lp']['q']
        with patch.object(r, 'topology', side_effect=AssertionError('must not run')):
            result = r.evaluate(request)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['geometry']['status'], 'not_run')
        self.assertEqual(result['external_lp']['status'], 'not_run')
        self.assertEqual(result['external_lp']['external_status'], 'infeasible')

    def test_all_no_vector_states(self):
        for status in ('optimal', 'feasible', 'not_run', 'failed', 'timeout'):
            request = fixture()
            request['external_lp']['status'] = status
            del request['external_lp']['q']
            result = r.evaluate(request)
            self.assertEqual(result['geometry']['status'], 'not_run')
            self.assertEqual(result['status'], 'incomplete')

    def test_infeasible_with_vector_rejects(self):
        request = fixture()
        request['external_lp']['status'] = 'infeasible'
        self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_bad_vector_dimensions(self):
        request = fixture()
        request['external_lp']['q'] = [0,0]
        self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_nonfinite_rejected_everywhere(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            for target in ('vector', 'source', 'coefficient', 'threshold'):
                request = fixture()
                if target == 'vector': request['external_lp']['q'][0] = value
                if target == 'source': request['source']['vertices'][0][0] = value
                if target == 'coefficient': request['model']['basis'][0][0] = value
                if target == 'threshold': request['quality']['max_warp_degrees'] = value
                self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_constraint_violations_prevent_geometry(self):
        for group in ('inequalities', 'equalities', 'bounds'):
            request = fixture()
            request['external_lp']['q'] = [2]
            request['model']['equalities'] = []
            request['model']['inequalities'] = []
            request['model']['bounds'] = [[-3,3]]
            if group == 'inequalities': request['model'][group] = [{'a': [1], 'b': 1}]
            if group == 'equalities': request['model'][group] = [{'a': [1], 'b': 0}]
            if group == 'bounds': request['model'][group] = [[-1,1]]
            result = r.evaluate(rebind(request))
            self.assertEqual(result['external_lp']['status'], 'fail')
            self.assertEqual(result['geometry']['status'], 'not_run')
            self.assertEqual(result['status'], 'failed')

    def test_protected_identity_is_exact(self):
        request = fixture()
        request['model']['basis'][0] = [1]
        request['external_lp']['q'] = [1e-10]
        result = r.evaluate(rebind(request))
        self.assertEqual(result['geometry']['protected_identity']['status'], 'fail')

    def test_open_surface(self):
        request = fixture()
        request['source']['faces'].pop()
        result = r.evaluate(rebind(request))
        self.assertEqual(result['geometry']['topology']['boundary_edges'], 4)
        self.assertEqual(result['status'], 'failed')

    def test_duplicate_face(self):
        request = fixture()
        request['source']['faces'].append(request['source']['faces'][0][:])
        result = r.evaluate(rebind(request))
        self.assertEqual(result['geometry']['topology']['duplicate_faces'], 1)
        self.assertEqual(result['status'], 'failed')

    def test_disconnected_vertex_link(self):
        faces = fixture()['source']['faces']
        # Two closed cubes share only vertex zero: all edge degrees remain two.
        second = [[0 if i == 0 else i+7 for i in f] for f in faces]
        result = r.topology(faces + second, 15, r.Budget(10,10000))
        self.assertEqual(result['boundary_edges'], 0)
        self.assertEqual(result['nonmanifold_edges'], 0)
        self.assertEqual(result['noncycle_vertex_links'], 1)
        self.assertEqual(result['status'], 'fail')

    def test_winding_failure(self):
        request = fixture()
        request['source']['faces'][0].reverse()
        result = r.evaluate(rebind(request))
        self.assertEqual(result['geometry']['topology']['winding_errors'], 4)

    def test_missing_bin_cannot_be_replaced_by_extra(self):
        request = fixture()
        request['coverage']['required_bins'] = [[10,10,10]]
        result = r.evaluate(request)['geometry']['coverage']
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['extra_bin_count'], 8)
        self.assertEqual(result['covered_required_count'], 0)

    def test_shared_vertex_intersection_not_silently_passed(self):
        # Even an otherwise successful topology never authenticates intersections.
        result = r.evaluate(fixture())
        self.assertEqual(result['self_intersection']['status'], 'not_run')
        self.assertNotEqual(result['status'], 'pass')

    def test_both_diagonal_warp_and_concavity(self):
        request = fixture()
        request['source']['vertices'][6][2] = 2
        result = r.evaluate(rebind(request))
        self.assertGreater(result['geometry']['quality']['max_warp_both_diagonals_degrees'], 5)
        self.assertEqual(result['geometry']['quality']['status'], 'fail')
        thresholds = request['quality']
        concave = [[0,0,0],[2,0,0],[0.5,0.5,0],[0,2,0]]
        self.assertEqual(r.quality(concave, [[0,1,2,3]], thresholds, r.Budget(10,1000))['status'], 'fail')

    def test_operation_budget(self):
        request = fixture()
        request['budgets']['operations'] = 1
        result = r.evaluate(request)
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['geometry']['status'], 'not_run')

    def test_wall_budget(self):
        ticks = iter([0,20,21])
        result = r.evaluate(fixture(), clock=lambda: next(ticks))
        self.assertEqual(result['status'], 'timed_out')

    def test_dimension_and_index_rejection(self):
        for mutation in ('basis', 'bound', 'vertex', 'face'):
            request = fixture()
            if mutation == 'basis': request['model']['basis'][0] = [1,2]
            if mutation == 'bound': request['model']['bounds'][0] = [2,-2]
            if mutation == 'vertex': request['source']['vertices'][0] = [1,2]
            if mutation == 'face': request['source']['faces'][0][0] = True
            self.assertEqual(r.evaluate(rebind(request))['status'], 'failed')

    def test_input_unchanged(self):
        request = fixture()
        before = copy.deepcopy(request)
        r.evaluate(request)
        self.assertEqual(before, request)

    def test_cli_complete_unit_and_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.json'
            path.write_text(json.dumps(fixture()))
            result = subprocess.run([sys.executable, '-B', '-m', 'hardsurface.host',
                                     'hardsurface', 'research-evidence', '--request', str(path)],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)['data']
        self.assertEqual(report['protocol_version'], '0.2.0')
        self.assertEqual(report['status'], 'incomplete')

    def test_schema_matches_generated_file_and_fixture(self):
        import jsonschema
        schema = r.schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(fixture(), schema)
        path = Path(__file__).resolve().parents[1]/'schemas/research-evidence.schema.json'
        self.assertEqual(json.loads(path.read_text()), schema)
        request = fixture()
        request['external_lp']['pass'] = True
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(request, schema)

    def test_schema_discovery(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'hardsurface.host',
                                 'hardsurface', 'describe', '--section', 'research-evidence'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['data'], r.schema())

    def test_empty_protected_is_not_run(self):
        request = fixture()
        request['protected_vertex_ids'] = []
        result = r.evaluate(request)
        self.assertEqual(result['geometry']['protected_identity']['status'], 'not_run')
        self.assertEqual(result['status'], 'incomplete')

    def test_disconnected_components_are_reported(self):
        faces = fixture()['source']['faces']
        second = [[i+8 for i in f] for f in faces]
        result = r.topology(faces+second, 16, r.Budget(10,10000))
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['connected_components'], 2)
        self.assertEqual(result['euler_characteristic'], 4)

    def test_duplicate_coverage_domain_rejects(self):
        request = fixture()
        request['coverage']['required_bins'].append(request['coverage']['required_bins'][0])
        self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_no_vector_still_validates_stage_contracts(self):
        for field in ('coverage', 'quality'):
            request = fixture()
            request['external_lp']['status'] = 'infeasible'
            del request['external_lp']['q']
            request[field] = {'garbage': True}
            self.assertEqual(r.evaluate(request)['status'], 'failed')

    def test_overflow_bound_residual_rejects(self):
        request = fixture()
        request['model']['bounds'] = [[-1e308,1e308]]
        request['model']['equalities'] = []
        request['model']['inequalities'] = []
        request['external_lp']['q'] = [1e308]
        self.assertEqual(r.evaluate(rebind(request))['status'], 'failed')

    def test_negative_overflow_residual_rejects(self):
        request = fixture()
        request['model']['bounds'] = [[-1e308,1e308]]
        request['model']['equalities'] = []
        request['model']['inequalities'] = [{'a': [-1e308], 'b': 0}]
        request['external_lp']['q'] = [1e308]
        self.assertEqual(r.evaluate(rebind(request))['status'], 'failed')

    def test_symlink_request_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.json'
            path.write_text(json.dumps(fixture()))
            link = Path(directory)/'link.json'
            link.symlink_to(path)
            with self.assertRaises(Exception):
                r.read_and_evaluate(link)

    def test_failure_metrics_do_not_depend_on_diagonal_order(self):
        vertices = [[0,0,0],[4,0,0],[1,1,0.1],[0,2,0]]
        a = r.quality(vertices, [[0,1,2,3]], fixture()['quality'], r.Budget(10,10000))
        b = r.quality(vertices, [[1,2,3,0]], fixture()['quality'], r.Budget(10,10000))
        self.assertEqual(a['warp_sample_count'], 2)
        self.assertAlmostEqual(a['max_warp_both_diagonals_degrees'], b['max_warp_both_diagonals_degrees'])
        self.assertEqual(a['worst_warp']['face_index'], 0)

    def test_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.json'
            path.write_text('{"source": {}, "source": {}}')
            with self.assertRaises(Exception): r.read_and_evaluate(path)


if __name__ == '__main__':
    unittest.main()
