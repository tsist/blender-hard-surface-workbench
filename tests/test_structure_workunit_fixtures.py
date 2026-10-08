# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-only preparation tests: no Blender/bpy, no .blend I/O, no native launch."""
import ast
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hardsurface import contract, host, planner
from hardsurface.quad_quality import DEFAULT_POLICY
from structure_workunit_fixtures import (BASE, CASES, CHECKPOINT_ID, DIMENSIONS, STEP_KEYS,
    NATIVE_PROCESS_COUNT, WORKUNIT_COUNT, EXPECTED_SOURCE_MODIFIER, base_state, initial_request, patch_request,
    panel_parameters, diagnosis_request, preparation_summary)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('qualify_structure_workunits', ROOT / 'scripts/qualify_structure_workunits.py')
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)
FAKE_SOURCE = {'file': '/tmp/not-created-m1-fixture.blend', 'sha256': 'a' * 64, 'bytes': 42}
OID = '11111111-1111-4111-8111-111111111111'


class WorkunitFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = {}
        cls.plans = {}
        for case in CASES:
            if case['action'] == 'resume':
                continue
            previous = cls.plans.get(case['source'])
            request = (patch_request(case['name'], case['datum'], FAKE_SOURCE, previous['resolved_design_state'], case['values'])
                       if previous else initial_request(case['name'], case['datum']))
            cls.requests[case['name']] = request
            cls.plans[case['name']] = planner.plan_request(request, saved_state=previous['resolved_design_state'] if previous else None)

    def test_all_five_model_requests_are_schema_valid(self):
        for request in self.requests.values():
            self.assertEqual(contract.validate_request(request), request)

    def test_all_five_plans_use_panel_and_real_checkpoint(self):
        for plan in self.plans.values():
            self.assertEqual([step['step_key'] for step in plan['steps']], STEP_KEYS)
            self.assertEqual([step['op'] for step in plan['steps']], ['quad.panel', 'checkpoint'])

    def test_all_five_dimensions_drive_refs(self):
        state = base_state('fixed_bottom')
        self.assertEqual({item['id'] for item in state['dimensions']}, set(DIMENSIONS))
        self.assertTrue(all(item['role'] == 'driving' and item['unit'] == 'mm' for item in state['dimensions']))
        encoded = json.dumps(state['features'])
        for key in DIMENSIONS:
            self.assertIn(json.dumps({'kind': 'dimension_ref', 'id': key}), encoded)

    def test_old_neutral_dimensions_and_frame_are_unchanged(self):
        panel = panel_parameters(base_state('fixed_bottom'))
        for key in ('size', 'corner_radius', 'edge_bevel', 'center', 'local_patch_bounds', 'chord_tolerance', 'subdivision_cage'):
            self.assertEqual(panel[key], BASE[key])
        self.assertEqual(panel['holes'][0], {'id': 'bore', 'kind': 'circle', 'center': [11, 1], 'radius': 8.5})
        self.assertEqual((panel['z_min'], panel['z_max']), (0, 5.5))

    def test_all_plans_have_authorship_and_required_native_checks(self):
        for plan in self.plans.values():
            self.assertRegex(plan['steps'][0]['geometry_estimate']['authorship_sha256'], '^[a-f0-9]{64}$')
            checks = {row['id']: row for row in plan['required_checks']}
            for key in ('structure_identity', 'quad_topology', 'reopen', 'source_preserved'):
                self.assertIn(key, checks)
            self.assertEqual(checks['structure_identity']['producer'], 'core')

    def test_patch_only_dimension_changes_and_exact_saved_state(self):
        for name in ('hole_update', 'thickness_bottom', 'thickness_midplane'):
            case = next(row for row in CASES if row['name'] == name)
            request = self.requests[name]['params']
            old = self.plans[case['source']]['resolved_design_state']
            self.assertEqual(request['source']['expected_sha256'], FAKE_SOURCE['sha256'])
            self.assertEqual(request['source']['bytes'], FAKE_SOURCE['bytes'])
            self.assertEqual(request['design']['mode'], 'patch_if_revision')
            self.assertEqual(request['design']['state_sha256'], contract.fingerprint(old))
            self.assertEqual(request['design']['expected_revision'], old['revision'])
            self.assertEqual({row['op'] for row in request['design']['patches']}, {'set_dimension'})

    def test_hole_edit_changes_xy_and_radius(self):
        panel = self.plans['hole_update']['steps'][0]['effective_params']
        self.assertEqual(panel['holes'][0]['center'], [10, 2])
        self.assertEqual(panel['holes'][0]['radius'], 8)
        self.assertEqual(panel['edit_datum'], 'fixed_bottom')

    def test_bottom_edit_retains_original_bottom(self):
        panel = self.plans['thickness_bottom']['steps'][0]['effective_params']
        self.assertEqual((panel['z_min'], panel['z_max']), (0, 6.5))
        self.assertEqual(panel['edit_datum'], 'fixed_bottom')
        self.assertEqual(self.plans['thickness_bottom']['resolved_design_state']['revision'], 3)

    def test_midplane_has_separate_correctly_declared_baseline(self):
        old = self.plans['baseline_midplane']['steps'][0]['effective_params']
        new = self.plans['thickness_midplane']['steps'][0]['effective_params']
        self.assertEqual(old['edit_datum'], 'fixed_midplane')
        self.assertEqual(new['edit_datum'], 'fixed_midplane')
        self.assertEqual((new['z_min'], new['z_max']), (-.5, 6))
        self.assertEqual(old['z_min'] + old['z_max'], new['z_min'] + new['z_max'])
        self.assertEqual(self.plans['thickness_midplane']['resolved_design_state']['revision'], 2)

    def test_patch_cannot_silently_change_datum(self):
        with self.assertRaises(ValueError):
            patch_request('wrong', 'fixed_midplane', FAKE_SOURCE, base_state('fixed_bottom'), {'z_max': 6})

    def test_non_dimension_patch_is_rejected(self):
        with self.assertRaises(ValueError):
            patch_request('wrong', 'fixed_bottom', FAKE_SOURCE, base_state('fixed_bottom'), {'edge_bevel': .8})

    def test_all_runs_are_bounded_numerical_no_render(self):
        for request in self.requests.values():
            params = request['params']
            self.assertEqual(params['purpose'], 'contract_fixture')
            self.assertFalse(params['wire']['enabled'])
            self.assertNotIn('preview', params)
            self.assertEqual(params['execution'], {'max_attempts': 1, 'technical_strategies': ['declared']})
            self.assertEqual(params['budgets']['max_total_steps'], 2)
            self.assertEqual(params['protection']['source_write'], 'forbidden')

    def test_diagnosis_schema_levels_tolerance_and_render(self):
        for name, plan in self.plans.items():
            request = diagnosis_request(name, FAKE_SOURCE, OID, plan['resolved_design_state'])
            params = request['params']
            self.assertEqual(params['levels'], [0, 1, 2, 3])
            self.assertEqual(params['panel_reference']['tolerance_mm'], .05)
            self.assertEqual(params['panel_reference']['holes'][0], {key: plan['steps'][0]['effective_params']['holes'][0][key] for key in ('center', 'radius')})
            self.assertFalse(params['render']['enabled'])
            self.assertFalse(params['export_geometry'])

    def test_old_quality_policy_not_relaxed(self):
        self.assertEqual(DEFAULT_POLICY['max_quad_warpage_degrees'], 5.)
        self.assertEqual(DEFAULT_POLICY['max_quad_plane_distance_m'], 1e-7)
        self.assertEqual(DEFAULT_POLICY['max_support_band_aspect_ratio'], 100)
        summary = preparation_summary()
        self.assertEqual(summary['required_reference_levels'], [2, 3])
        self.assertEqual(summary['diagnostic_reference_levels'], [0, 1])

    def test_job_and_native_invocation_accounting(self):
        self.assertEqual(len(CASES), 6)
        self.assertEqual(sum(case['native_processes'] for case in CASES), 29)
        self.assertEqual(NATIVE_PROCESS_COUNT, 29 + 6)
        self.assertEqual(WORKUNIT_COUNT, 6 + 6)
        self.assertTrue(preparation_summary()['not_an_os_process_or_thread_bound'])


class GateAndDriverTests(unittest.TestCase):
    def setUp(self):
        # Text-only test fixtures; no Blender binary or project is used.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'plain.txt').write_text('inert source bytes')
        self.runtime = self.root / 'inert-runtime.txt'
        self.runtime.write_text('This is never executed')
        self.runtime.chmod(0o700)
        self.output = self.root / 'approved-new-output'
        self.frozen = {'format': 'm1-workunit-freeze/1', 'code_files': driver.inventory(self.source),
                       'blender': {'file': str(self.runtime), 'sha256': driver.file_sha(self.runtime)}}
        self.freeze = self.root / 'freeze.json'
        driver.write_new(self.freeze, self.frozen)
        self.reference = 'test-only-external-user-decision-never-native-authority'
        self.approved = {'format': 'm1-workunit-authorization/1', 'decision': 'approved',
            'approval_reference': self.reference, 'freeze_sha256': driver.file_sha(self.freeze),
            'blender_sha256': driver.file_sha(self.runtime), 'output_root': str(self.output),
            'scope': driver.SCOPE, 'job_limit': WORKUNIT_COUNT, 'blender_invocation_limit': NATIVE_PROCESS_COUNT}
        self.approval = self.root / 'approval.json'
        driver.write_new(self.approval, self.approved)

    def gate(self, **overrides):
        params = dict(source_root=self.source, freeze_path=self.freeze, freeze_sha256=driver.file_sha(self.freeze),
            approval_path=self.approval, approval_sha256=driver.file_sha(self.approval),
            approval_reference=self.reference, blender=self.runtime,
            blender_sha256=self.frozen['blender']['sha256'], output_root=self.output)
        params.update(overrides)
        with patch.dict('os.environ', {}, clear=True):
            return driver.gate(**params)

    def test_gate_pass_is_read_only_not_native_execution(self):
        with patch.object(driver.subprocess, 'run', side_effect=AssertionError('No execution')):
            result = self.gate()
        self.assertFalse(self.output.exists())
        self.assertEqual(result['authorization'], self.approved)

    def test_gate_rejects_changed_code(self):
        (self.source / 'plain.txt').write_text('changed')
        with self.assertRaisesRegex(driver.QualificationFailure, 'inventory mismatch'):
            self.gate()

    def test_gate_rejects_unlisted_code(self):
        (self.source / 'extra.py').write_text('pass')
        with self.assertRaises(driver.QualificationFailure):
            self.gate()

    def test_gate_rejects_changed_runtime(self):
        self.runtime.write_text('changed runtime')
        with self.assertRaisesRegex(driver.QualificationFailure, 'Runtime binary SHA'):
            self.gate()

    def test_gate_rejects_wrong_freeze_sha(self):
        with self.assertRaisesRegex(driver.QualificationFailure, 'inventory SHA'):
            self.gate(freeze_sha256='f' * 64)

    def test_gate_rejects_wrong_approval_sha(self):
        with self.assertRaisesRegex(driver.QualificationFailure, 'approval record SHA'):
            self.gate(approval_sha256='f' * 64)

    def test_gate_rejects_missing_or_mismatched_external_reference(self):
        for ref in ('', 'a different approval'):
            with self.subTest(ref=ref), self.assertRaises(driver.QualificationFailure):
                self.gate(approval_reference=ref)

    def test_gate_rejects_existing_output(self):
        self.output.mkdir()
        with self.assertRaisesRegex(driver.QualificationFailure, 'new root'):
            self.gate()

    def test_gate_rejects_changed_output_scope(self):
        with self.assertRaisesRegex(driver.QualificationFailure, 'approval does not match'):
            self.gate(output_root=self.root / 'unapproved-output')

    def test_gate_rejects_output_within_source(self):
        with self.assertRaisesRegex(driver.QualificationFailure, 'frozen source'):
            self.gate(output_root=self.source / 'unapproved-child')

    def test_gate_rejects_source_symlink(self):
        (self.source / 'alias').symlink_to(self.source / 'plain.txt')
        with self.assertRaisesRegex(driver.QualificationFailure, 'symbolic links'):
            self.gate()

    def test_copy_is_byte_identical_new_files_only(self):
        self.output.mkdir()
        copied = driver.copy_execution_source(self.source, self.output, self.frozen['code_files'])
        self.assertEqual(driver.inventory(copied), self.frozen['code_files'])
        self.assertEqual(driver.inventory(self.source), self.frozen['code_files'])
        with self.assertRaises(FileExistsError):
            driver.copy_execution_source(self.source, self.output, self.frozen['code_files'])

    def test_cli_global_arguments_precede_subcommands(self):
        request = self.root / 'not-created.json'
        for action in ('run', 'diagnose', 'resume'):
            cmd = driver.cli_command(self.source, self.runtime, action, request=request, resume_job='a' * 32)
            parsed = host.parser().parse_args(cmd[1:])
            self.assertEqual(parsed.jobs_dir, str(self.source / 'jobs-store'))
            self.assertEqual(parsed.blender, str(self.runtime))
            self.assertFalse(parsed.background)
            self.assertEqual(parsed.command, 'hardsurface')
            if action == 'resume':
                self.assertEqual(parsed.checkpoint, CHECKPOINT_ID)

    def test_description_never_calls_process_or_creates_output(self):
        with patch.object(driver.subprocess, 'run', side_effect=AssertionError('Forbidden')), patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(driver.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())['qualification'], 'not_run')
        self.assertFalse(self.output.exists())

    def test_execute_missing_approval_is_rejected_before_launch(self):
        with patch.object(driver, 'execute_run', side_effect=AssertionError('Forbidden')):
            with self.assertRaises(driver.QualificationFailure):
                driver.main(['--execute'])

    def test_mock_cli_invokes_exactly_once_and_records_it(self):
        calls = []
        def fake(command, **kwargs):
            calls.append((command, kwargs))
            kwargs['stdout'].write(json.dumps({'ok': True, 'data': {'status': 'succeeded'}}).encode())
            return SimpleNamespace(returncode=0)
        result = driver.invoke_cli(['inert-host-command'], cwd=self.root, env={'TMPDIR': str(self.root)}, log_root=self.root, name='mock-pass', runner=fake)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['timeout'], 630)
        self.assertTrue((self.root / 'mock-pass-process.json').is_file())

    def test_mock_cli_failure_does_not_retry(self):
        calls = []
        def fake(command, **kwargs):
            calls.append(command)
            kwargs['stdout'].write(b'{"ok":false}')
            return SimpleNamespace(returncode=5)
        with self.assertRaisesRegex(driver.QualificationFailure, 'no retry'):
            driver.invoke_cli(['inert-host-command'], cwd=self.root, env={}, log_root=self.root, name='mock-fail', runner=fake)
        self.assertEqual(len(calls), 1)

    def test_mock_cli_timeout_does_not_retry(self):
        calls = []
        def fake(command, **kwargs):
            calls.append(command)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with self.assertRaises(subprocess.TimeoutExpired):
            driver.invoke_cli(['inert-host-command'], cwd=self.root, env={}, log_root=self.root, name='mock-timeout', runner=fake)
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.root / 'mock-timeout-failure.json').is_file())

    def test_no_bpy_import_or_direct_blender_launch(self):
        tree = ast.parse((ROOT / 'scripts/qualify_structure_workunits.py').read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any(isinstance(node, ast.Import) and any(alias.name == 'bpy' for alias in node.names) for node in imports))
        text = (ROOT / 'scripts/qualify_structure_workunits.py').read_text()
        self.assertNotIn("'--background'", text)
        self.assertNotIn("'--python'", text)


class EvidenceGateTests(unittest.TestCase):
    def native(self):
        binding = {'object_id': OID, 'data_id': '22222222-2222-4222-8222-222222222222', **{key: 'a' * 64 for key in driver.SIGNATURES}}
        row = {'status': 'pass', 'binding': binding, 'kernel_report': {'status': 'pass', **{key: binding[key] for key in driver.SIGNATURES}}}
        return row, {OID: binding}

    def test_native_kernel_must_match_external_registry(self):
        row, registry = self.native()
        driver.verify_native_rows([row], registry)
        row['kernel_report']['geometry_signature'] = 'f' * 64
        with self.assertRaises(driver.QualificationFailure):
            driver.verify_native_rows([row], registry)

    def test_native_metadata_without_actual_kernel_is_rejected(self):
        row, registry = self.native()
        row.pop('kernel_report')
        with self.assertRaises(driver.QualificationFailure):
            driver.verify_native_rows([row], registry)

    def test_independent_reopen_requires_distinct_native_process(self):
        row, registry = self.native()
        data = {'independent_reopen': True, 'producer_identity': 'blender:1:first', 'verifier_identity': 'blender:2:second',
                'checks': {key: 'pass' for key in ('technical', 'preservation', 'dependency_reproduction', 'state_consistency')},
                'structure_identity': {'evidence': [row]}}
        driver.verify_reopen(data, registry)
        data['verifier_identity'] = data['producer_identity']
        with self.assertRaises(driver.QualificationFailure):
            driver.verify_reopen(data, registry)

    def diagnosis(self):
        actual = deepcopy(EXPECTED_SOURCE_MODIFIER)
        source = {'status': 'validated', 'identity_binding_status': 'bound', 'external_registry_verified': True,
                  'authored_bore_sector_identity_matches': True, 'actual_source_modifier': actual}
        profile = {'mode': 'source_bound_qualification_v1', 'qualification': 'source_binding_verified',
                   'source_scene_evaluation': {'use_simplify': False}, 'semantic_source': source,
                   'modifier_profile': {'status': 'pass', 'expected_source_modifier': actual, 'actual_source_modifier': actual}}
        report = {'status': 'succeeded', 'source_original': FAKE_SOURCE, 'source_guard': {'accepted': True},
            'blend_save_performed': False, 'saved_candidate_modified': False, 'source_geometry_modified': False,
            'source_transforms_modified': False, 'previews': [], 'outputs': [],
            'source_signature_sha256': 'a' * 64, 'source_signature_after_sha256': 'a' * 64,
            'authored_control_loops': {'status': 'pass'}, 'backend': {'evaluation_profile': profile},
            'levels': [{'level': level, 'reference_check': {'status': 'pass' if level >= 2 else 'fail', 'tolerance_mm': .05}} for level in range(4)]}
        for row in report['levels']:
            level = row['level']; clone = deepcopy(actual)
            clone.update(levels=level, render_levels=level, show_viewport=bool(level), show_render=bool(level))
            row['source_bound_evaluation'] = {'status': 'pass', 'actual_clone_modifier': clone}
            row['actual_modifier_stack'] = [clone]
            transport = {'status': 'validated', 'source': source, 'level': level,
                         'all_source_face_descendant_counts_match': True, 'parent_surface_labels_match': True,
                         'expected_descendants_per_source_face': 4**level,
                         'parent_patch_topology': {'status': 'pass', 'oriented_source_neighbour_cycles_match': True}}
            row['semantic_face_transport'] = transport
            row['reference_check'].update(measurement_profile='semantic_bore_v1',
                semantic_bore_membership={'status': 'validated'}, source_bound_semantics=transport)
        return report

    def test_legacy_diagnostic_cannot_qualify_even_if_numbers_pass(self):
        report = self.diagnosis(); report['backend']['evaluation_profile']['mode'] = 'legacy_geometry_diagnostic_v0'
        with self.assertRaisesRegex(driver.QualificationFailure, 'Source-bound'):
            self.check_diagnosis(report)

    def test_wrong_modifier_profile_fails_before_geometric_result(self):
        report = self.diagnosis(); report['backend']['evaluation_profile']['modifier_profile']['actual_source_modifier'] = {'quality': 3}
        with self.assertRaisesRegex(driver.QualificationFailure, 'Actual source modifier'):
            self.check_diagnosis(report)

    def test_missing_native_semantics_is_not_qualification(self):
        report = self.diagnosis(); report['levels'][2].pop('semantic_face_transport')
        with self.assertRaisesRegex(driver.QualificationFailure, 'semantic face transport'):
            self.check_diagnosis(report)

    def test_geometry_fallback_is_not_qualification(self):
        report = self.diagnosis(); report['levels'][2]['reference_check']['measurement_profile'] = 'legacy_geometry_diagnostic_v0'
        with self.assertRaisesRegex(driver.QualificationFailure, 'authenticated actual semantic bore'):
            self.check_diagnosis(report)

    def check_diagnosis(self, report):
        with patch.object(driver, 'owned_job', return_value=Path('/nonexistent-mock')), patch.object(driver, 'read_json', return_value=report), \
             patch.object(driver, 'verify_descriptor'), patch.object(driver, 'descriptor', return_value={'mock': True}), \
             patch.object(driver, 'process_evidence', return_value=[]):
            return driver.diagnosis_evidence(Path('/nonexistent-mock'), {'job_id': 'mock'}, FAKE_SOURCE)

    def test_level_one_is_reported_diagnostic_without_hiding_failure(self):
        result = self.check_diagnosis(self.diagnosis())
        self.assertEqual(result['levels'][1]['role'], 'diagnostic')
        self.assertEqual(result['levels'][1]['reference_check']['status'], 'fail')

    def test_level_two_or_three_failure_is_required_stop(self):
        for level in (2, 3):
            report = self.diagnosis()
            report['levels'][level]['reference_check']['status'] = 'fail'
            with self.subTest(level=level), self.assertRaisesRegex(driver.QualificationFailure, 'L2/L3'):
                self.check_diagnosis(report)

    def test_tolerance_cannot_be_relaxed(self):
        report = self.diagnosis()
        report['levels'][2]['reference_check']['tolerance_mm'] = .06
        with self.assertRaisesRegex(driver.QualificationFailure, 'tolerance changed'):
            self.check_diagnosis(report)


if __name__ == '__main__':
    unittest.main()
