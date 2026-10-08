# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST receipt-adoption tests; no Blender, installation or real files.

The native envelopes are explicit Python protocol mocks. Geometry construction,
edit verification, JobStore and immutable checkpoint checks are real HOST code.
Text candidate fixtures do not establish native save/reopen qualification.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from hardsurface import contract as c, edit_review as w, planner
from hardsurface.io import RuntimeFailure, checkpoint_evidence_artifacts
from hardsurface.jobs import JobStore, ReliabilityError
from hardsurface.recovery import (
    CheckpointStore, REQUIRED_CHECKS, REQUIRED_FINGERPRINTS, artifact_descriptor,
)
from hardsurface.structure_edit import plan_authored_edit, verify_authored_edit
from test_edit_review import request
from test_sparse_pipeline_integration import materialized, parameters


class ParameterReceiptBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        panel = parameters()
        panel['holes'][0]['radius'] = {'kind': 'dimension_ref', 'id': 'radius'}
        cls.state = c.validate_state({
            'revision': 1,
            'dimensions': [{'id': 'radius', 'role': 'driving', 'quantity': 'length',
                            'value': 12, 'unit': 'mm'}],
            'sketches': [],
            'features': [{'id': 'block', 'program': {'kind': 'steps', 'steps': [panel]}}],
        })
        cls.workflow = request({'file': '/tmp/neutral-receipt-source.blend',
                                'sha256': 'a' * 64, 'bytes': 10})
        edit = cls.workflow['params']['edit_request']['params']
        checks = ['closed_mesh', 'preservation', 'reopen', 'structure_identity']
        edit['quality']['required'] = list(checks)
        edit['work_units'][0]['checks'] = list(checks)
        edit['design'] = {
            'mode': 'patch_if_revision', 'expected_revision': 1,
            'state_sha256': c.fingerprint(cls.state),
            'patches': [{'op': 'set_dimension', 'id': 'radius', 'value': 14}],
        }
        cls.edit_request = c.validate_request(cls.workflow['params']['edit_request'])
        cls.plan = planner.plan_request(cls.edit_request, saved_state=cls.state)
        old = c._validate(planner._resolve_dimensions(
            cls.state['features'][0]['program']['steps'][0],
            {'radius': cls.state['dimensions'][0]}, .001), c.STEP)
        new = cls.plan['steps'][0]['effective_params']
        _, cls.before = materialized(old)
        _, cls.after = materialized(new)
        edit_plan = plan_authored_edit(cls.before['witness'], cls.before['authorship'],
                                      new, datum_policy='fixed_midplane')
        cls.proof = verify_authored_edit(cls.before, cls.after, edit_plan)
        cls.modifier = deepcopy(cls.workflow['params']['before_diagnosis']['params']
                               ['evaluation_profile']['expected_source_modifier'])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='host-parameter-receipt-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = JobStore(self.root / 'store', allowed_roots=[self.root])
        row = self.store.submit('neutral.parameter-receipt', c.fingerprint(self.edit_request))
        self.job = self.store.job_dir(row['job_id'])

    def save(self, name, value):
        path = self.job / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return artifact_descriptor(path, root=self.job)

    def fixture(self, *, pin_witness=True, mutate_proof=None):
        """Construct one immutable checkpoint with explicitly mocked reopen."""
        candidate_path = self.job / 'candidate.txt'
        candidate_path.write_text('HOST protocol fixture; not a Blender file.\n',
                                  encoding='utf-8')
        candidate = artifact_descriptor(candidate_path, root=self.job)
        self.plan_ref = self.save('plan.json', self.plan)
        proof = deepcopy(self.proof)
        if mutate_proof is not None:
            mutate_proof(proof)
        self.witness_ref = self.save('witness.json', proof)
        self.core = {
            'candidate': candidate,
            'scene_snapshot': {
                'objects': [{'object_id': self.after['binding']['object_id'],
                             'feature_id': 'block', 'step_key': 'block/panel',
                             'type': 'MESH', 'modifiers': [deepcopy(self.modifier)]}],
                'structure_registry': {self.after['binding']['object_id']:
                                       deepcopy(self.after['binding'])},
            },
            'steps': [{'evidence': {'structure_edit': {
                'status': 'pass', 'evidence': self.witness_ref,
            }}}],
        }
        core_ref = self.save('core.json', self.core)
        checks = {key: 'pass' for key in REQUIRED_CHECKS}
        reopen_ref = self.save('reopened.json', {
            'candidate': candidate, 'independent_reopen': True, 'checks': checks,
            'scene_snapshot_sha256': w.evidence_digest(self.core['scene_snapshot']),
            'producer_identity': 'explicit-host-mock-writer',
            'verifier_identity': 'explicit-host-mock-reader',
        })
        fingerprints = {key: {} for key in REQUIRED_FINGERPRINTS}
        fingerprints.update(plan={'base': self.plan['plan_sha256']},
                            targets={'source_state': self.state},
                            request=c.fingerprint(self.edit_request))
        artifacts = {'candidate': candidate}
        if pin_witness:
            artifacts.update(checkpoint_evidence_artifacts(self.core, root=self.job))
        self.checkpoint = CheckpointStore(self.job).accept(
            'final', artifacts, fingerprints,
            {'candidate': candidate, 'independent_reopen': True, 'checks': checks,
             'evidence': reopen_ref}, completed_steps=['block/panel'])
        self.report = {'status': 'succeeded', 'checkpoints': [self.checkpoint],
                       'fingerprints': fingerprints, 'candidate': candidate,
                       'core_report': core_ref}
        return self.report

    def adopt(self):
        return w._edit(self.report, self.edit_request, self.job)

    def test_real_host_proof_and_pinned_receipt_accept(self):
        self.fixture()
        accepted = self.adopt()
        self.assertEqual(accepted['edit_category'], 'hole_diameter')
        self.assertEqual(accepted['before_binding'], self.before['binding'])
        self.assertEqual(accepted['after_binding'], self.after['binding'])
        self.assertGreater(len(accepted['impact']['changed_vertex_ids']), 0)
        self.assertEqual(accepted['native_edit_witness'], self.witness_ref)
        self.assertIn(self.witness_ref, self.checkpoint['artifacts'].values())
        self.assertIn(self.plan_ref, accepted['evidence_artifacts'])

    def test_replaced_impact_with_rewritten_mutable_reports_rejects(self):
        self.fixture()
        accepted_receipt = artifact_descriptor(self.checkpoint['receipt']['file'])
        changed = deepcopy(self.proof)
        changed['impact']['changed_vertex_ids'] = []
        changed['impact']['affected_vertex_ids'] = []
        replacement = self.save('replacement-witness.json', changed)
        self.core['steps'][0]['evidence']['structure_edit']['evidence'] = replacement
        self.report['core_report'] = self.save('core.json', self.core)
        with self.assertRaisesRegex(RuntimeFailure, 'not pinned'):
            self.adopt()
        self.assertEqual(artifact_descriptor(self.checkpoint['receipt']['file']),
                         accepted_receipt)

    def test_in_place_impact_mutation_rejects_at_checkpoint(self):
        self.fixture()
        changed = deepcopy(self.proof)
        changed['impact']['changed_vertex_ids'] = []
        self.core['steps'][0]['evidence']['structure_edit']['evidence'] = self.save(
            'witness.json', changed)
        self.report['core_report'] = self.save('core.json', self.core)
        with self.assertRaises(ReliabilityError):
            self.adopt()

    def test_missing_immutable_witness_rejects(self):
        self.fixture(pin_witness=False)
        with self.assertRaisesRegex(RuntimeFailure, 'not pinned'):
            self.adopt()

    def test_changed_plan_content_rejects(self):
        self.fixture()
        changed = deepcopy(self.plan)
        changed['steps'][0]['effective_params']['holes'][0]['radius'] = 12
        changed['steps'][0]['effective_params']['edge_bevel'] = 1.2
        self.save('plan.json', changed)
        with self.assertRaisesRegex(RuntimeFailure, 'plan differs'):
            self.adopt()

    def test_resealed_plan_still_rejects_against_checkpoint(self):
        self.fixture()
        changed = deepcopy(self.plan)
        changed['steps'][0]['effective_params']['holes'][0]['radius'] = 12
        changed['steps'][0]['effective_params']['edge_bevel'] = 1.2
        changed['plan_sha256'] = c.fingerprint({k: v for k, v in changed.items()
                                               if k != 'plan_sha256'})
        self.save('plan.json', changed)
        with self.assertRaisesRegex(RuntimeFailure, 'plan differs'):
            self.adopt()

    def test_before_native_target_mismatch_rejects(self):
        self.fixture(mutate_proof=lambda p: p['before_binding'].update(
            object_id='71111111-1111-4111-8111-111111111111'))
        with self.assertRaisesRegex(RuntimeFailure, 'actual target'):
            self.adopt()

    def test_after_native_target_mismatch_rejects(self):
        self.fixture(mutate_proof=lambda p: p['after_binding'].update(
            object_id='71111111-1111-4111-8111-111111111111'))
        with self.assertRaisesRegex(RuntimeFailure, 'actual target'):
            self.adopt()

    def test_native_identity_change_rejects_even_with_same_object(self):
        self.fixture(mutate_proof=lambda p: p['after_binding'].update(
            topology_epoch=p['before_binding']['topology_epoch'] + 1))
        with self.assertRaisesRegex(RuntimeFailure, 'native identity'):
            self.adopt()

    def test_after_diagnosis_binding_mismatch_rejects_before_geometry_read(self):
        self.fixture()
        accepted = self.adopt()
        after_binding = deepcopy(accepted['after_binding'])
        after_binding['data_id'] = '71111111-1111-4111-8111-111111111111'
        stages = {
            'before': {'accepted': {'evaluation_profile': accepted['evaluation_profile']}},
            'edit': {'accepted': accepted},
            'after': {'accepted': {'evaluation_profile': accepted['evaluation_profile'],
                                    'native_binding': after_binding}},
        }
        with self.assertRaisesRegex(RuntimeFailure, 'After diagnosis differs'):
            w._compare(stages, self.job, None, self.workflow)


if __name__ == '__main__':
    unittest.main()
