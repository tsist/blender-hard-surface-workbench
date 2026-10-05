"""Bounded checkpoint reports and exact receipt references; retains fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hardsurface.io import (RuntimeFailure, atomic_json, checkpoint_evidence_artifacts,
                            compact_checkpoint_record, compact_step_records,
                            descriptor, read_json, read_json_reference)
from hardsurface.jobs import JobStore, ReliabilityError
from hardsurface.recovery import CheckpointStore, REQUIRED_CHECKS, REQUIRED_FINGERPRINTS



class CheckpointProjectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hs-checkpoint-projection-', dir='/tmp'))

    def fixture(self, root=None, snapshot=None):
        root = root or self.root
        snapshot = snapshot or {'snapshot_version': '1.0', 'objects': [], 'design_state_sha256': 'd' * 64}
        candidate = root / 'candidate.fixture.txt'
        candidate.write_text('Numerical receipt fixture, not a Blender file')
        state = atomic_json(root / 'design-state.json', {'revision': 1})
        sidecar = atomic_json(root / 'checkpoint.json', {'scene_snapshot': snapshot,
            'design_state_sha256': 'd' * 64, 'completed_step_keys': ['Feature/body', 'Feature/checkpoint']})
        record = {**descriptor(candidate), 'candidate': descriptor(candidate),
            'producer_identity': 'fixture:producer', 'state': 'persisted_unverified',
            'design_state': state, 'design_state_sha256': 'd' * 64,
            'completed_step_keys': ['Feature/body', 'Feature/checkpoint'],
            'sidecar': sidecar, 'scene_snapshot': snapshot}
        record['report'] = atomic_json(root / 'checkpoint-report.json', compact_checkpoint_record(record))
        return {'scene_snapshot': snapshot, 'candidate': descriptor(candidate), 'design_state': state,
            'completed_step_keys': record['completed_step_keys'], 'partial_checkpoint': True,
            'checkpoints': [record], 'steps': [{'step_key': 'Feature/body', 'op': 'primitive.box',
                'status': 'pass', 'evidence': {'volume_m3': 0.001}},
                {'step_key': 'Feature/checkpoint', 'op': 'checkpoint', 'status': 'pass', 'evidence': record}]}

    def project(self, report):
        result = copy.deepcopy(report)
        result['checkpoints'] = [compact_checkpoint_record(item) for item in result['checkpoints']]
        for step in result['steps']:
            if step['op'] == 'checkpoint':
                step['evidence'] = compact_checkpoint_record(step['evidence'])
        return result

    def test_report_over_nine_megabytes_projects_under_unchanged_limit(self):
        snapshot = {'objects': [{'name': 'numeric-padding', 'custom': {'padding': 'x' * (3 * 1024 * 1024)}}]}
        old = self.fixture(snapshot=snapshot)
        old_path = self.root / 'legacy-report.json'
        old_path.write_text(json.dumps(old, indent=2))
        self.assertGreater(old_path.stat().st_size, 9_000_000)
        with self.assertRaisesRegex(RuntimeFailure, 'byte limit'):
            read_json(old_path)
        projected = self.project(old)
        new = atomic_json(self.root / 'compact-result.json', projected)
        self.assertLess(new['bytes'], 4 * 1024 * 1024)
        self.assertEqual(read_json(new['file'])['scene_snapshot'], snapshot)
        self.assertEqual(len(checkpoint_evidence_artifacts(projected, root=self.root)), 2)
        self.assertNotIn('scene_snapshot', projected['checkpoints'][0])
        self.assertNotIn('scene_snapshot', projected['steps'][-1]['evidence'])


    def test_saved_step_history_never_contains_snapshot_or_file_refs(self):
        report = self.fixture(snapshot={'objects': [{'padding': 'x' * 100_000}]})
        records = compact_step_records(report['steps'])
        self.assertEqual(records[0], report['steps'][0])
        self.assertEqual(set(records[1]['evidence']), {'state', 'design_state_sha256', 'completed_step_keys'})
        self.assertLess(len(json.dumps(records)), 1024)
        self.assertEqual(compact_step_records(records), records)

    def test_hash_checked_against_exact_parsed_bytes(self):
        reference = atomic_json(self.root / 'evidence.json', {'value': 1})
        Path(reference['file']).write_text('{"value": 2}\n')
        with self.assertRaises(RuntimeFailure) as caught:
            read_json_reference(reference)
        self.assertEqual(caught.exception.code, 'EVIDENCE_REFERENCE_MISMATCH')

    def test_missing_reference_fails_closed(self):
        report = self.project(self.fixture())
        path = Path(report['checkpoints'][0]['sidecar']['file'])
        path.rename(path.with_suffix('.unavailable'))
        with self.assertRaises(RuntimeFailure):
            checkpoint_evidence_artifacts(report, root=self.root)

    def test_descriptor_rejects_extra_keys_unbounded_size_and_symlinks(self):
        ref = atomic_json(self.root / 'data.json', {'ok': True})
        for wrong in ({**ref, 'fallback': {}}, {**ref, 'bytes': 8 * 1024 * 1024 + 1},
                      {**ref, 'bytes': True}, {**ref, 'sha256': 'bad'}):
            with self.subTest(wrong=wrong), self.assertRaises(RuntimeFailure):
                read_json_reference(wrong)
        link = self.root / 'symlink.json'
        link.symlink_to(ref['file'])
        with self.assertRaises(RuntimeFailure):
            read_json_reference({**ref, 'file': str(link)})

    def test_sidecar_must_bind_exact_top_level_snapshot(self):
        report = self.project(self.fixture())
        report['scene_snapshot'] = {'different': True}
        with self.assertRaises(RuntimeFailure) as caught:
            checkpoint_evidence_artifacts(report, root=self.root)
        self.assertEqual(caught.exception.code, 'EVIDENCE_REFERENCE_MISMATCH')

    def test_resume_receipt_binds_both_sidecar_and_compact_report(self):
        for mutate in ('sidecar', 'report', 'missing'):
            with self.subTest(mutate=mutate):
                store = JobStore(self.root / mutate, allowed_roots=[self.root])
                job = store.submit('projection-' + mutate, hashlib.sha256(mutate.encode()).hexdigest())
                path = store.job_dir(job['job_id'])
                report = self.project(self.fixture(path))
                artifacts = {'candidate': report['candidate'], 'design_state': report['design_state'],
                             **checkpoint_evidence_artifacts(report, root=path)}
                checks = {key: 'pass' for key in REQUIRED_CHECKS}
                evidence = atomic_json(path / 'verification.json', {'independent_reopen': True,
                    'candidate': artifacts['candidate'], 'checks': checks,
                    'producer_identity': 'fixture:producer', 'verifier_identity': 'fixture:independent-reader'})
                proof = {'independent_reopen': True, 'candidate': artifacts['candidate'],
                         'checks': checks, 'evidence': evidence}
                fingerprints = {key: {'fixture': key} for key in REQUIRED_FINGERPRINTS}
                receipt = CheckpointStore(path).accept('checkpoint', artifacts, fingerprints,
                    proof, completed_steps=report['completed_step_keys'])
                resumed = CheckpointStore(path).resume(fingerprints, 'checkpoint')
                self.assertEqual(resumed['receipt'], receipt['receipt'])
                self.assertEqual(set(resumed['step_status'].values()), {'reused_from_checkpoint'})
                target = Path(report['checkpoints'][0]['report' if mutate == 'report' else 'sidecar']['file'])
                if mutate == 'missing':
                    target.rename(target.with_suffix('.unavailable'))
                else:
                    target.write_text('{}\n')
                with self.assertRaises(ReliabilityError):
                    CheckpointStore(path).resume(fingerprints, 'checkpoint')

    def test_projection_failure_never_selects_another_geometry_attempt(self):
        from hardsurface import host
        for code in ('JSON_LIMIT', 'REPORT_LIMIT', 'EVIDENCE_REFERENCE_MISMATCH'):
            with self.subTest(code=code):
                request = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/box-cut-bevel.json').read_text())
                request['params'].pop('preview', None)
                request['params']['request_id'] = 'projection-no-retry-' + code
                request['params']['execution'] = {'max_attempts': 2, 'technical_strategies': ['declared', 'boolean_exact']}
                store = JobStore(self.root / ('no-retry-' + code), allowed_roots=[self.root])
                job = store.submit(request['params']['request_id'], hashlib.sha256(code.encode()).hexdigest())
                path = store.job_dir(job['job_id'])
                atomic_json(path / 'request.json', request)
                # Model a completed producer followed by a projection failure.
                # The real supervisor and retry policy execute; Blender does not.
                with mock.patch.object(host, 'impl_identity', return_value={'fixture': 'unchanged'}), \
                     mock.patch.object(host, 'execute_attempt', side_effect=RuntimeFailure(code, 'fixture projection failed')) as attempt:
                    report = host.run_job(store.root, job['job_id'], 'never-launched')
                self.assertEqual(attempt.call_count, 1)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['error']['code'], code)
                self.assertEqual(len(report['attempts']), 1)
                self.assertFalse((path / 'attempt-02').exists())


if __name__ == '__main__':
    unittest.main()
