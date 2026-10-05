"""Synthetic content-binding tests; no remote approval authentication is claimed.

Temporary evidence is deliberately retained, following the project's no-cleanup rule.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from hardsurface.io import RuntimeFailure, descriptor
from hardsurface.planner import plan_request
from hardsurface.reference import approval_descriptor, verify_reference
from tests.test_contract import minimal_request


class ReferenceApprovalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hs-reference-test-', dir='/tmp'))
        self.source = {
            'channel': 'chatgpt', 'author': 'user', 'room_id': 'synthetic-test-room',
            'message_id': 'synthetic-test-message',
            'quote': 'Synthetic test approval for fixture revision 1.',
        }
        self.dimensions_path = self.root / 'dimensions.json'
        self.write_json(self.dimensions_path, {'width': 20, 'height': 30, 'depth': 10, 'unit': 'mm'})
        self.hero_path = self.root / 'hero.svg'
        self.hero_path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="30"/></svg>')
        self.detail_path = self.root / 'detail.svg'
        self.detail_path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>')
        self.checklist_path = self.root / 'checklist.json'
        self.checklist = {
            'approval_message': self.source['message_id'], 'before_modeling': True,
            'items': [{'id': 'outline'}, {'id': 'dimensions'}, {'id': 'detail'}],
        }
        self.write_json(self.checklist_path, self.checklist)
        self.manifest_path = self.root / 'manifest.json'
        self.manifest = {
            'version': '1',
            'approval_reference': self.source['message_id'],
            'approval_quote': self.source['quote'], 'unresolved_conflicts': [],
            'references': [
                self.reference(self.dimensions_path, 'authoritative_dimension_spec'),
                self.reference(self.hero_path, 'concept_render'),
                self.reference(self.detail_path, 'detail_reference'),
            ],
            'checklist': self.reference(self.checklist_path, 'reference_consistency_checklist'),
        }
        self.write_json(self.manifest_path, self.manifest)
        self.request = minimal_request()
        self.params = self.request['params']
        self.params['purpose'] = 'production'
        self.params['quality']['visual'] = 'required'
        self.params['quality']['user_feedback'] = 'required'
        self.params['reference_package'] = {
            'version': '1', 'manifest': self.pin(self.manifest_path),
            'requirements': ['outline', 'dimensions'], 'review_views': ['hero'],
        }
        self.receipt_path = self.root / 'approval.json'
        self.receipt = {
            'receipt_version': '1.0', 'approved': True,
            'manifest_sha256': descriptor(self.manifest_path)['sha256'],
            'approval_reference': self.source['message_id'],
            'dimensions_sha256': descriptor(self.dimensions_path)['sha256'],
            'checklist_sha256': descriptor(self.checklist_path)['sha256'],
            'unresolved_conflicts': [], 'source': copy.deepcopy(self.source),
        }
        self.repin_receipt()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')

    @staticmethod
    def reference(path, role):
        return {**descriptor(path), 'role': role}

    @staticmethod
    def pin(path):
        return {'file': str(path), 'expected_sha256': descriptor(path)['sha256']}

    def repin_receipt(self):
        self.write_json(self.receipt_path, self.receipt)
        self.approval = self.pin(self.receipt_path)

    def repin_manifest(self):
        self.write_json(self.manifest_path, self.manifest)
        self.params['reference_package']['manifest'] = self.pin(self.manifest_path)
        self.receipt['manifest_sha256'] = descriptor(self.manifest_path)['sha256']
        self.repin_receipt()

    def repin_checklist(self):
        self.write_json(self.checklist_path, self.checklist)
        self.manifest['checklist'] = self.reference(self.checklist_path, 'reference_consistency_checklist')
        self.receipt['checklist_sha256'] = descriptor(self.checklist_path)['sha256']
        self.repin_manifest()

    def assert_denied(self, code='REFERENCE_CONFLICT', approval=None):
        with self.assertRaises(RuntimeFailure) as caught:
            verify_reference(self.params, self.approval if approval is None else approval)
        self.assertEqual(caught.exception.code, code)

    def test_pinned_files_and_source_are_bound(self):
        result = verify_reference(self.params, self.approval)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['source'], self.source)
        self.assertEqual(result['manifest'], descriptor(self.manifest_path))
        self.assertEqual(result['reference_files'], self.manifest['references'] + [self.manifest['checklist']])
        self.assertEqual(result['approval_record'], {
            key: descriptor(self.receipt_path)[key] for key in ('sha256', 'bytes')
        })
        self.assertIn('not remote message authentication', result['trust_boundary'])

    def test_planner_accepts_separately_pinned_approval(self):
        plan = plan_request(self.request, reference_approval=self.approval)
        self.assertEqual(plan['reference_gate']['status'], 'pass')
        self.assertEqual(plan['reference_gate']['approval_reference'], self.source['message_id'])

    def test_slack_source_contract_is_supported(self):
        self.receipt['source']['channel'] = 'slack'
        self.repin_receipt()
        self.assertEqual(verify_reference(self.params, self.approval)['source']['channel'], 'slack')

    def test_production_missing_approval_is_denied(self):
        with self.assertRaises(RuntimeFailure) as caught:
            verify_reference(self.params, None)
        self.assertEqual(caught.exception.code, 'REFERENCE_APPROVAL_REQUIRED')

    def test_boolean_and_legacy_inline_approval_are_denied(self):
        legacy = {key: value for key, value in self.receipt.items() if key not in ('source', 'receipt_version')}
        for approval in (True, False, {'approved': True}, legacy, self.receipt):
            with self.subTest(approval=approval):
                self.assert_denied('REFERENCE_APPROVAL_REQUIRED', approval)

    def test_pinning_a_legacy_receipt_does_not_make_it_evidence(self):
        self.receipt.pop('source')
        self.receipt.pop('receipt_version')
        self.repin_receipt()
        self.assert_denied('REFERENCE_APPROVAL_REQUIRED')

    def test_receipt_requires_source(self):
        self.receipt.pop('source')
        self.repin_receipt()
        self.assert_denied('REFERENCE_APPROVAL_REQUIRED')

    def test_receipt_requires_exact_true_and_version(self):
        original = copy.deepcopy(self.receipt)
        for field, value in (('approved', False), ('approved', 1), ('approved', 'true'), ('receipt_version', '0.9')):
            with self.subTest(field=field, value=value):
                self.receipt = copy.deepcopy(original)
                self.receipt[field] = value
                self.repin_receipt()
                self.assert_denied('REFERENCE_APPROVAL_REQUIRED')

    def test_source_requires_identity_quote_and_user_role(self):
        original = copy.deepcopy(self.receipt)
        cases = [(key, '') for key in self.source]
        cases += [('author', 'assistant'), ('channel', 'website'), ('quote', 12)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.receipt = copy.deepcopy(original)
                self.receipt['source'][field] = value
                self.repin_receipt()
                self.assert_denied('REFERENCE_APPROVAL_REQUIRED')

    def test_source_missing_field_is_denied(self):
        for field in self.source:
            with self.subTest(field=field):
                self.receipt['source'] = copy.deepcopy(self.source)
                self.receipt['source'].pop(field)
                self.repin_receipt()
                self.assert_denied('REFERENCE_APPROVAL_REQUIRED')

    def test_wrong_receipt_pin_is_denied(self):
        self.approval['expected_sha256'] = '0' * 64
        self.assert_denied('SOURCE_CHANGED')

    def test_changed_receipt_bytes_are_denied(self):
        self.receipt_path.write_bytes(self.receipt_path.read_bytes() + b'\n')
        self.assert_denied('SOURCE_CHANGED')

    def test_changed_manifest_bytes_are_denied(self):
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b'\n')
        self.assert_denied('SOURCE_CHANGED')

    def test_changed_each_reference_and_checklist_is_denied(self):
        for path in (self.dimensions_path, self.hero_path, self.detail_path, self.checklist_path):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                self.assert_denied('SOURCE_CHANGED')
                path.write_bytes(original)

    def test_receipt_cannot_target_another_manifest(self):
        self.receipt['manifest_sha256'] = '0' * 64
        self.repin_receipt()
        self.assert_denied()

    def test_source_and_receipt_message_identity_must_match(self):
        self.receipt['source']['message_id'] = 'another-synthetic-message'
        self.repin_receipt()
        self.assert_denied()

    def test_manifest_must_match_approval_message(self):
        self.manifest['approval_reference'] = 'another-synthetic-message'
        self.repin_manifest()
        self.assert_denied()

    def test_manifest_must_match_exact_approval_quote(self):
        self.manifest['approval_quote'] += ' Altered.'
        self.repin_manifest()
        self.assert_denied()

    def test_manifest_version_must_match_request(self):
        self.params['reference_package']['version'] = '2'
        self.assert_denied()

    def test_receipt_conflicts_must_be_known_and_empty(self):
        for value in (None, False, '', {}, ['synthetic-unresolved-dimension']):
            with self.subTest(value=value):
                self.receipt['unresolved_conflicts'] = value
                self.repin_receipt()
                self.assert_denied()

    def test_manifest_conflicts_must_be_known_and_empty(self):
        for value in (None, False, '', {}, ['synthetic-unresolved-detail']):
            with self.subTest(value=value):
                self.manifest['unresolved_conflicts'] = value
                self.repin_manifest()
                self.assert_denied()
        self.manifest.pop('unresolved_conflicts')
        self.repin_manifest()
        self.assert_denied()

    def test_referenced_byte_count_is_checked(self):
        self.manifest['references'][1]['bytes'] += 1
        self.repin_manifest()
        self.assert_denied('SOURCE_CHANGED')

    def test_manifest_requires_reference_files_and_checklist(self):
        original = copy.deepcopy(self.manifest)
        for field, value in (('references', []), ('references', None), ('checklist', None)):
            with self.subTest(field=field, value=value):
                self.manifest = copy.deepcopy(original)
                self.manifest[field] = value
                self.repin_manifest()
                self.assert_denied()

    def test_duplicate_reference_files_are_denied(self):
        self.manifest['references'].append(copy.deepcopy(self.manifest['references'][0]))
        self.repin_manifest()
        self.assert_denied()

    def test_exactly_one_authoritative_dimension_file_is_required(self):
        self.manifest['references'][0]['role'] = 'dimension_preview'
        self.repin_manifest()
        self.assert_denied()
        self.manifest['references'][0]['role'] = 'authoritative_dimension_spec'
        self.manifest['references'][1]['role'] = 'authoritative_dimension_spec'
        self.repin_manifest()
        self.assert_denied()

    def test_dimension_and_checklist_hashes_must_match_real_files(self):
        original = copy.deepcopy(self.receipt)
        for field in ('dimensions_sha256', 'checklist_sha256'):
            with self.subTest(field=field):
                self.receipt = copy.deepcopy(original)
                self.receipt[field] = '0' * 64
                self.repin_receipt()
                self.assert_denied()

    def test_checklist_requires_matching_pre_modeling_approval(self):
        original = copy.deepcopy(self.checklist)
        for field, value in (('approval_message', 'another-synthetic-message'), ('before_modeling', False), ('before_modeling', 1)):
            with self.subTest(field=field, value=value):
                self.checklist = copy.deepcopy(original)
                self.checklist[field] = value
                self.repin_checklist()
                self.assert_denied()

    def test_request_requirements_must_exist_in_frozen_checklist(self):
        self.params['reference_package']['requirements'].append('absent-requirement')
        self.assert_denied()

    def test_fixture_needs_no_production_evidence(self):
        params = minimal_request()['params']
        self.assertEqual(verify_reference(params, None)['status'], 'not_applicable')
        with self.assertRaises(RuntimeFailure) as caught:
            verify_reference(params, self.approval)
        self.assertEqual(caught.exception.code, 'REFERENCE_CONFLICT')

    def test_descriptor_requires_file_and_independent_lowercase_sha(self):
        self.assertIsNone(approval_descriptor(None, None))
        self.assertEqual(approval_descriptor(self.receipt_path, self.approval['expected_sha256']), self.approval)
        for path, sha in ((None, 'a' * 64), (self.receipt_path, None), (self.receipt_path, ''), (self.receipt_path, 'A' * 64), (self.receipt_path, True)):
            with self.subTest(path=path, sha=sha), self.assertRaises(RuntimeFailure) as caught:
                approval_descriptor(path, sha)
            self.assertEqual(caught.exception.code, 'REFERENCE_APPROVAL_REQUIRED')

    def test_cli_plan_run_study_accept_separate_approval_flags(self):
        from hardsurface.host import parser
        for action in ('plan', 'run', 'study'):
            argv = ['hardsurface', action, '--request', str(self.root / 'request.json'),
                    '--reference-approval', str(self.receipt_path),
                    '--reference-approval-sha256', self.approval['expected_sha256']]
            if action == 'study':
                argv += ['--cases', str(self.root / 'cases.json')]
            with self.subTest(action=action):
                parsed = parser().parse_args(argv)
                self.assertEqual(approval_descriptor(parsed.reference_approval, parsed.reference_approval_sha256), self.approval)


if __name__ == '__main__':
    unittest.main()
