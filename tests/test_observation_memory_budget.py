"""HOST-only budget contract/wiring tests; no Blender, retained temporary evidence."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hardsurface import contract as c
from hardsurface import host
from hardsurface import observation as o
from hardsurface import subdivision, topology, validation
from hardsurface.budgets import BudgetExceeded, BudgetLimits, JobBudget
from hardsurface.io import RuntimeFailure
from hardsurface.jobs import JobStore, ReliabilityError


ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
LEGACY_FINGERPRINT = '7bd1bff14d8c8e14220ebaa7ec9103c6bf46f9f612e9e6d65abba0be32667940'


def request():
    return {'schema_version': '1.0', 'command': 'hardsurface.observe', 'params': {
        'request_id': 'observation-memory-host',
        'source': {'file': '/tmp/source.blend', 'expected_sha256': 'a' * 64},
        'views': [{'name': 'corner', 'direction': 'three_quarter', 'visible_feature_ids': ['panel']}],
    }}


def other_requests():
    common = {'request_id': 'other-read-only-memory-host', 'source': request()['params']['source']}
    return (
        ('validate', validation, {'schema_version': '2.0', 'command': 'hardsurface.validate',
            'params': {**common, 'parts': [{'id': 'panel', 'feature_id': 'panel'}]}}),
        ('topology', topology, {'schema_version': '1.0', 'command': 'hardsurface.topology',
            'params': {**common, 'feature_ids': ['panel']}}),
        ('subdivision.diagnose', subdivision, {'schema_version': '1.0',
            'command': 'hardsurface.subdivision.diagnose', 'params': {**common,
                'target': {'object_name': 'panel'}, 'stack_mode': 'isolated_control_cage'}}),
    )


class ObservationMemoryContractTests(unittest.TestCase):
    def test_omitted_option_preserves_exact_legacy_normalization_and_fingerprint(self):
        raw = request()
        before = copy.deepcopy(raw)
        expected = copy.deepcopy(raw)
        expected['params'].update(cpu_threads=2, wall_seconds=600.0, diagnostic_preset='neutral',
            reflection_rotation_degrees=0.0,
            wire={'enabled': True, 'line_width_px': 1.25, 'max_edges': 100000, 'mesh_state': 'evaluated'})
        expected['params']['views'][0].update(width=1024, height=1024, explode_z_mm={}, mesh_state='evaluated')
        normalized = o.validate_request(raw)
        self.assertEqual(raw, before)
        self.assertEqual(normalized, expected)
        self.assertEqual(c.fingerprint(normalized), LEGACY_FINGERPRINT)
        self.assertNotIn('max_tree_rss_bytes', normalized['params'])

    def test_explicit_integer_endpoints_and_three_gib_do_not_mutate_request(self):
        for value in (GIB, 3 * GIB, 4 * GIB):
            with self.subTest(value=value):
                raw = request()
                raw['params']['max_tree_rss_bytes'] = value
                before = copy.deepcopy(raw)
                self.assertEqual(o.validate_request(raw)['params']['max_tree_rss_bytes'], value)
                self.assertEqual(raw, before)

    def test_invalid_bounds_types_and_unknown_fields_are_rejected(self):
        for value in (0, GIB - 1, 4 * GIB + 1, -GIB, True, False, float(GIB),
                      float(3 * GIB), 3 * GIB + .5, str(3 * GIB), None, {}, []):
            with self.subTest(value=value):
                raw = request()
                raw['params']['max_tree_rss_bytes'] = value
                with self.assertRaises(c.ContractError):
                    o.validate_request(raw)
                with self.assertRaises(c.ContractError):
                    o.validate_request(json.dumps(raw))
                with self.assertRaises(c.ContractError):
                    host._read_only_budget('observe', {'wall_seconds': 600,
                        'max_tree_rss_bytes': value}, time.monotonic())
        for key in ('max_tree_rss', 'max_observed_rss_bytes', 'max_tree_rss_bytes_limit'):
            raw = request()
            raw['params'][key] = 3 * GIB
            with self.subTest(key=key), self.assertRaises(c.ContractError):
                o.validate_request(raw)

    def test_exact_exported_schema_keeps_the_budget_optional_without_a_default(self):
        schema = o.schema()
        self.assertEqual(json.loads((ROOT / 'schemas/hardsurface-observe.schema.json').read_text()), schema)
        params = schema['properties']['params']
        self.assertEqual(params['properties']['max_tree_rss_bytes'],
            {'type': 'integer', 'minimum': GIB, 'maximum': 4 * GIB})
        self.assertNotIn('max_tree_rss_bytes', params['required'])
        self.assertFalse(params['additionalProperties'])

    def test_other_read_only_commands_keep_one_gib_and_refuse_the_field(self):
        root = Path(tempfile.mkdtemp(prefix='hs-observation-memory-other-'))
        source = root / 'host-only-source.blend'
        source.write_text('Host-only fixture; not a Blender data file.')
        source_record = host.descriptor(source)
        for action, adapter, raw in other_requests():
            with self.subTest(action=action):
                raw['params']['source'] = {'file': source_record['file'], 'expected_sha256': source_record['sha256']}
                params = adapter.validate_request(raw)['params']
                budget = host._read_only_budget(action, params, time.monotonic())
                self.assertEqual(budget.limits.max_tree_rss_bytes, GIB)
                schema_params = adapter.schema()['properties']['params']
                if action == 'subdivision.diagnose':
                    # New sparse-only profile exposes this optional field, while
                    # this legacy request must still reject it below.
                    self.assertEqual(schema_params['properties']['max_tree_rss_bytes'],
                        {'type': 'integer', 'minimum': GIB, 'maximum': 3 * GIB})
                    self.assertNotIn('max_tree_rss_bytes', schema_params['required'])
                else:
                    self.assertNotIn('max_tree_rss_bytes', schema_params['properties'])
                self.assertFalse(schema_params['additionalProperties'])
                raw['params']['max_tree_rss_bytes'] = 3 * GIB
                with self.assertRaises(c.ContractError):
                    adapter.validate_request(raw)
                with self.assertRaises(c.ContractError):
                    host._read_only_budget(action, {**params, 'max_tree_rss_bytes': 3 * GIB}, time.monotonic())

    def test_explicit_budget_changes_fingerprint_and_same_request_id_conflicts(self):
        normalized = o.validate_request(request())
        changed = copy.deepcopy(normalized)
        changed['params']['max_tree_rss_bytes'] = 3 * GIB
        explicit_legacy = copy.deepcopy(normalized)
        explicit_legacy['params']['max_tree_rss_bytes'] = GIB
        self.assertEqual(len({c.fingerprint(value) for value in (normalized, changed, explicit_legacy)}), 3)
        root = Path(tempfile.mkdtemp(prefix='hs-observation-memory-identity-'))
        store = JobStore(root / 'jobs', allowed_roots=[root])
        first = store.submit(changed['params']['request_id'], c.fingerprint(changed))
        second = store.submit(changed['params']['request_id'], c.fingerprint(changed))
        self.assertTrue(second['reused'])
        self.assertEqual(second['job_id'], first['job_id'])
        different_explicit = copy.deepcopy(changed)
        different_explicit['params']['max_tree_rss_bytes'] = 4 * GIB
        for different in (normalized, explicit_legacy, different_explicit):
            with self.subTest(params=different['params']), self.assertRaises(ReliabilityError) as caught:
                store.submit(different['params']['request_id'], c.fingerprint(different))
            self.assertEqual(caught.exception.detail_code, 'REQUEST_ID_CONFLICT')


class ObservationMemoryRuntimeTests(unittest.TestCase):
    def test_real_job_budget_uses_requested_limit_and_preserves_wall_disk_limits(self):
        params = o.validate_request(request())['params']
        legacy = host._read_only_budget('observe', params, time.monotonic())
        self.assertIsInstance(legacy, JobBudget)
        self.assertEqual(legacy.limits, BudgetLimits(wall_seconds=600,
            max_tree_rss_bytes=GIB, max_disk_bytes=512 * 1024**2))
        with self.assertRaises(BudgetExceeded):
            legacy.check(tree_rss_bytes=GIB + 1)
        params['max_tree_rss_bytes'] = 3 * GIB
        budget = host._read_only_budget('observe', params, time.monotonic())
        self.assertEqual(params['cpu_threads'], 2)
        self.assertEqual(budget.limits, BudgetLimits(wall_seconds=600,
            max_tree_rss_bytes=3 * GIB, max_disk_bytes=512 * 1024**2))
        budget.check(tree_rss_bytes=3 * GIB)
        with self.assertRaises(BudgetExceeded) as caught:
            budget.check(tree_rss_bytes=3 * GIB + 1)
        self.assertEqual(caught.exception.resource, 'tree_rss_bytes')
        self.assertEqual(caught.exception.limit, 3 * GIB)
        with self.assertRaises(BudgetExceeded) as caught:
            budget.check(disk_bytes=512 * 1024**2 + 1)
        self.assertEqual(caught.exception.resource, 'disk_bytes')

    def run_host_fixture(self, value=None, fail_worker=False):
        """Exercise public admission/reporting with mocked Blender and file guards only."""
        root = Path(tempfile.mkdtemp(prefix='hs-observation-memory-runtime-'))
        raw = request()
        raw['params']['views'].append({**copy.deepcopy(raw['params']['views'][0]), 'name': 'second'})
        if value is not None:
            raw['params']['max_tree_rss_bytes'] = value
        path = root / 'request.json'
        path.write_text(json.dumps(raw))
        budgets = []

        def fake_worker(payload, job, budget, store, blender, label):
            budgets.append(budget)
            self.assertEqual(payload['request']['params']['cpu_threads'], 2)
            if value is not None:
                self.assertEqual(payload['request']['params']['max_tree_rss_bytes'], value)
                budget.check(tree_rss_bytes=2 * GIB)
            if fail_worker:
                raise RuntimeFailure('HOST_FIXTURE_FAILURE', 'Synthetic host-only failure')
            data = {'previews': [], 'elapsed_seconds': 0.01}
            host.atomic_json(job / (label + '-result.json'), data)
            return data

        source = {'file': '/tmp/host-only-no-blender-source.blend', 'sha256': 'a' * 64, 'bytes': 1}
        with patch('hardsurface.io.copy_verified', return_value=(source, {'host_fixture': True})), \
             patch.object(host, 'ProtectedInputs') as guards, \
             patch.object(host, 'impl_identity', return_value={'host_fixture': True}), \
             patch.object(host, 'verify_descriptor', return_value=source), \
             patch.object(host, 'worker', side_effect=fake_worker):
            guards.return_value.__enter__.return_value.report.return_value = {'host_fixture': True}
            result = host.read_only_action('observe', path, root / 'jobs', 'unused-host-fixture', time.monotonic())
        report = host.load(result['report']['file'])
        return result, report, budgets

    def test_actual_host_worker_wiring_shares_three_gib_budget_and_reports_it(self):
        result, report, budgets = self.run_host_fixture(3 * GIB)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(budgets), 2)
        self.assertIs(budgets[0], budgets[1])
        self.assertEqual(budgets[0].limits.max_tree_rss_bytes, 3 * GIB)
        policy = {'max_tree_rss_bytes': 3 * GIB, 'max_disk_bytes': 512 * 1024**2,
            'wall_seconds': 600.0, 'cpu_threads': 2,
            'budget_scope': 'all_isolated_view_workers', 'hard_memory_enforcement': False}
        self.assertEqual(result['resource_policy'], policy)
        self.assertEqual(report['resource_policy'], policy)
        self.assertTrue(report['view_process_isolation'])

    def test_legacy_host_report_omits_policy_and_keeps_one_gib_budget(self):
        result, report, budgets = self.run_host_fixture()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(budgets), 2)
        self.assertEqual(budgets[0].limits.max_tree_rss_bytes, GIB)
        self.assertNotIn('resource_policy', result)
        self.assertNotIn('resource_policy', report)

    def test_explicit_policy_is_bound_even_when_worker_fails(self):
        result, report, budgets = self.run_host_fixture(3 * GIB, fail_worker=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'HOST_FIXTURE_FAILURE')
        self.assertEqual(len(budgets), 1)
        self.assertEqual(report['resource_policy']['max_tree_rss_bytes'], 3 * GIB)
        self.assertEqual(result['resource_policy'], report['resource_policy'])


if __name__ == '__main__':
    unittest.main()
