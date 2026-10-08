# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-only plan binding tests. No bpy, native run, save/reopen or approval.

The integration fixture materializes the authored 992-element panel into Python
protocol mocks. All plan adaptations below are synthetic test data, not changes
to or approval of the user reference proposal.
"""
import copy
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from hardsurface import structure_kernel as kernel
from hardsurface import structure_native as native
from hardsurface import structure_contract as contract
from hardsurface import subd_panel_identity as author
from hardsurface.structure_plan_binding import bind_structure_plan, BINDING_VERSION
from test_structure_integration_review import panel, permute_panel

ROOT = Path(__file__).resolve().parents[1]


def fixture(report, result):
    request = json.loads((ROOT/'fixtures/structure-plan.valid.json').read_text())
    request['source']['object_ids'] = [report['binding']['object_id'], 'non_target']
    request['evaluation']['levels'] = [2]
    structure = report['witness']['structure']
    region = next(row for row in structure['regions'] if row['role'] == 'bore_wall')
    loops = {row['id']: row for row in structure['loops']}
    top = next(loops[key] for key in region['boundary_loop_ids'] if loops[key]['normal'][2] > 0)
    bottom = next(loops[key] for key in region['boundary_loop_ids'] if loops[key]['normal'][2] < 0)
    port = next(row for row in structure['boundary_ports'] if row['loop_id'] == top['id'])
    # Keep the plan normal nominal +Z and origin in exact authored mm. Native
    # coordinates undergo float32 conversion to metres and then back to mm.
    anchor = top['vertex_ids'][0]
    request['structure']['boundary_ports'][0]['origin_mm'] = list(result['vertices_mm'][result['authored_structure']['vertex_map'][anchor]])
    mapping = {'schema_version': BINDING_VERSION, 'coordinate_space': 'object_local_mm',
        'feature_ownership': {'bore': region['feature_id']},
        'regions': {'bore_region': {'actual_region_id': region['id'], 'actual_surface_id': region['role'], 'face_ids': list(region['face_ids'])}},
        'loops': {'bore_top': {'actual_loop_id': top['id'], 'orientation_normal': 'local_positive_z'},
                  'bore_bottom': {'actual_loop_id': bottom['id'], 'orientation_normal': 'local_positive_z'}},
        'boundary_ports': {'bore_top_port': {'actual_port_id': port['id'], 'transform': 'identity', 'point_order': list(port['point_order'])}}}
    return request, mapping


def reseal_witness(report):
    """Synthetic adversarial test receipt; never an authentication helper."""
    actual = kernel.validate_structure(report['witness']['mesh'], report['witness']['structure'])
    report['kernel_report'] = actual
    for key in native.SIGNATURES: report['binding'][key] = actual[key]


def reseal_author(report):
    a = report['authorship']; a['parameter_binding']['design_sha256'] = author.fingerprint(a['parameter_binding']['parameters'])
    a['authorship_sha256'] = author.fingerprint(author.authorship_payload(a))
    report['binding']['authorship_sha256'] = a['authorship_sha256']


class StructurePlanBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.object, cls.result, cls.report = panel()
        cls.request, cls.mapping = fixture(cls.report, cls.result)

    def setUp(self):
        self.r, self.m, self.n = copy.deepcopy(self.request), copy.deepcopy(self.mapping), copy.deepcopy(self.report)

    def bind(self, *, expected=None):
        return bind_structure_plan(self.r, self.n, self.m, expected_binding=self.n['binding'] if expected is None else expected)

    def rejected(self, code):
        with self.assertRaises(kernel.StructureError) as ctx: self.bind()
        self.assertEqual(ctx.exception.code, code)

    def test_full_mock_binds_without_approval_or_side_effects(self):
        snapshot = copy.deepcopy((self.r, self.n, self.m))
        with patch('builtins.open', side_effect=AssertionError('No reference/source file reads')):
            result = self.bind()
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['evidence_kind'], 'host_only_declaration_binding')
        self.assertFalse(result['construction_authorized'])
        self.assertEqual(result['qualification'], 'not_run')
        self.assertEqual(result['reference_approval'], 'required')
        self.assertEqual(result['side_effects'], [])
        self.assertEqual(len(result['regions']), 1)
        self.assertEqual(len(result['loops']), 2)
        self.assertEqual(len(result['boundary_ports']), 1)
        self.assertEqual(result['regions'][0]['declared_feature_id'], 'bore')
        self.assertEqual(result['regions'][0]['feature_id'], 'review_panel')
        self.assertIn('evaluation.blender_version', result['parameter_binding']['unavailable_parameter_fields'])
        self.assertEqual((self.r, self.n, self.m), snapshot)
        result['regions'][0]['face_ids'].clear()
        self.assertTrue(self.m['regions']['bore_region']['face_ids'])

    def test_request_still_uses_strict_closed_schema(self):
        self.r['approved'] = True
        with self.assertRaises(contract.ContractError): self.bind()

    def test_reference_claim_does_not_authorize(self):
        reference = self.r['reference_contract']; reference['state'] = 'approval_evidence_supplied'
        reference['approval_evidence'] = {'authority':'user', 'source_message_id':'fixture:not-approval',
            'evidence':{'file':'/fixtures/not-real-approval.json','sha256':'e'*64,'bytes':1},
            'reference_package_sha256':reference['package_sha256'], 'design_sha256':contract.fingerprint(self.r['design']),
            'project_id':self.r['identity']['project_id'], 'verification':'external_verification_required'}
        result = self.bind()
        self.assertFalse(result['construction_authorized'])
        self.assertEqual(result['qualification'], 'not_run')
        self.assertEqual(result['reference_approval'], 'external_verification_required')

    def test_actual_object_must_be_in_explicit_source_scope(self):
        self.r['source']['object_ids'] = ['different_object', 'non_target']
        self.rejected('PLAN_OBJECT_SCOPE_MISMATCH')

    def test_external_binding_is_required_and_exact(self):
        for expected in ({}, {'object_id':'other'}, dict(self.n['binding'], context_sha256='e'*64)):
            with self.subTest(expected=expected), self.assertRaises(kernel.StructureError) as ctx:
                bind_structure_plan(self.r, self.n, self.m, expected_binding=expected)
            self.assertEqual(ctx.exception.code, 'PLAN_NATIVE_BINDING_MISMATCH')

    def test_pending_registry_or_evaluated_data_is_rejected(self):
        self.n['identity_binding_status'] = 'pending_registry'
        self.rejected('PLAN_NATIVE_BINDING_INVALID')
        self.n['identity_binding_status'] = 'bound'; self.n['binding']['mesh_state'] = 'evaluated'
        self.rejected('PLAN_NATIVE_BINDING_INVALID')

    def test_native_descriptor_is_not_truthy_flag(self):
        self.n['native_extraction']['actual_edges_verified'] = 1
        self.rejected('PLAN_NATIVE_EXTRACTION_UNSUPPORTED')

    def test_raw_witness_is_revalidated_not_report_flags(self):
        self.n['witness']['mesh']['faces'][0][0] = self.n['witness']['mesh']['faces'][0][1]
        with self.assertRaises(kernel.StructureError): self.bind()

    def test_report_signature_and_external_signature_must_equal_fresh_raw_mesh(self):
        self.n['kernel_report']['geometry_signature'] = 'e'*64
        self.rejected('PLAN_WITNESS_SIGNATURE_MISMATCH')

    def test_actual_geometry_change_cannot_hide_behind_unchanged_report(self):
        mesh = self.n['witness']['mesh']
        # A uniform X translation preserves witness checks after translating
        # geometric records; its old external geometry signature is still stale.
        for v in mesh['vertices']: v[0] += .25
        for row in self.n['witness']['structure']['loops']: row['anchor']['position_mm'][0] += .25
        for row in self.n['witness']['structure']['boundary_ports']: row['frame']['origin_mm'][0] += .25
        self.rejected('PLAN_WITNESS_SIGNATURE_MISMATCH')

    def test_authored_identity_and_parameters_must_match_external_author_hash(self):
        self.n['authorship']['parameter_binding']['parameters']['holes'][0]['radius'] += .001
        self.rejected('PLAN_AUTHORSHIP_MISMATCH')

    def test_mapping_exact_coverage_and_no_approval_fields(self):
        for change in ('missing', 'extra', 'approval'):
            self.m = copy.deepcopy(self.mapping)
            if change == 'missing': self.m['loops'].pop('bore_bottom')
            if change == 'extra': self.m['loops']['other'] = copy.deepcopy(self.m['loops']['bore_top'])
            if change == 'approval': self.m['approved'] = True
            with self.subTest(change=change), self.assertRaises(kernel.StructureError): self.bind()

    def test_duplicate_actual_entities_rejected(self):
        self.m['loops']['bore_bottom']['actual_loop_id'] = self.m['loops']['bore_top']['actual_loop_id']
        self.rejected('PLAN_MAPPING_AMBIGUOUS')

    def test_unknown_id_never_gets_nearest_match(self):
        self.m['loops']['bore_top']['actual_loop_id'] = 'almost-the-same-ring'
        self.rejected('PLAN_ENTITY_MISSING')

    def test_face_ownership_exact_and_nonempty(self):
        self.m['regions']['bore_region']['face_ids'].pop()
        self.rejected('PLAN_REGION_OWNERSHIP')

    def test_feature_owner_and_surface_alias_are_explicit(self):
        self.m['feature_ownership']['bore'] = 'other_feature'
        self.rejected('PLAN_FEATURE_OWNERSHIP')
        self.m['feature_ownership']['bore'] = 'review_panel'
        self.m['regions']['bore_region']['actual_surface_id'] = 'wall'
        self.rejected('PLAN_REGION_OWNERSHIP')

    def test_hole_cannot_claim_a_shell_region(self):
        shell = next(row for row in self.n['witness']['structure']['regions'] if row['role'] == 'bottom_flat_guard')
        self.m['regions']['bore_region'] = {'actual_region_id':shell['id'],'actual_surface_id':shell['role'],'face_ids':shell['face_ids']}
        self.rejected('PLAN_FEATURE_OWNERSHIP')

    def test_region_cannot_omit_a_real_boundary(self):
        self.r['structure']['regions'][0]['loop_ids'] = ['bore_top']
        self.r['structure']['loops'] = self.r['structure']['loops'][:1]
        self.m['loops'].pop('bore_bottom')
        self.rejected('PLAN_REGION_BOUNDARY_MISMATCH')

    def test_loop_count_and_named_orientation(self):
        self.r['structure']['loops'][0]['expected_vertex_count'] = 25
        self.rejected('PLAN_LOOP_COUNT_MISMATCH')
        self.r['structure']['loops'][0]['expected_vertex_count'] = 24
        self.r['structure']['loops'][0]['orientation'] = 'cw'
        self.rejected('PLAN_LOOP_ORIENTATION_MISMATCH')

    def test_known_author_role_cannot_name_another_cycle(self):
        self.r['structure']['loops'][0]['role'] = 'bore_rim_bottom'
        self.rejected('PLAN_LOOP_ROLE_MISMATCH')

    def test_orientation_normal_must_be_explicit_named_axis(self):
        self.m['loops']['bore_top']['orientation_normal'] = 'auto'
        self.rejected('PLAN_LOOP_NORMAL_UNSUPPORTED')
        self.m['loops']['bore_top']['orientation_normal'] = 'local_negative_z'
        self.r['structure']['loops'][0]['orientation'] = 'cw'
        self.assertEqual(self.bind()['status'], 'pass')

    def test_author_loop_alias_is_proven_by_cycle_not_raw_boundary_id(self):
        control = next(row for row in self.n['witness']['structure']['loops'] if row['role'] == 'bore_rim_top')
        self.m['loops']['bore_top']['actual_loop_id'] = control['id']
        result = self.bind()
        entry = next(row for row in result['loops'] if row['declared_id'] == 'bore_top')
        self.assertNotEqual(entry['actual_id'], entry['boundary_cycle_id'])

    def test_same_count_nonboundary_control_loop_rejected(self):
        control = next(row for row in self.n['witness']['structure']['loops'] if row['role'] == 'bore_support_top')
        self.m['loops']['bore_top']['actual_loop_id'] = control['id']
        self.rejected('PLAN_LOOP_OWNERSHIP')

    def test_explicit_port_anchor_order_cannot_rotate_or_reverse_silently(self):
        for mode in ('rotate', 'reverse'):
            self.m = copy.deepcopy(self.mapping); order = self.m['boundary_ports']['bore_top_port']['point_order']
            self.m['boundary_ports']['bore_top_port']['point_order'] = order[1:]+order[:1] if mode == 'rotate' else list(reversed(order))
            with self.subTest(mode=mode): self.rejected('PLAN_PORT_ORDER_MISMATCH')

    def test_reverse_transform_requires_both_declared_and_actual_permission(self):
        self.r['structure']['boundary_ports'][0]['allowed_transforms'].append('reverse_order')
        self.m['boundary_ports']['bore_top_port']['transform'] = 'reverse_order'
        self.rejected('PLAN_PORT_TRANSFORM_UNSUPPORTED')

    def test_absent_port_never_flattens_or_synthesizes(self):
        self.m['boundary_ports']['bore_top_port']['actual_port_id'] = 'missing-planar-port'
        self.rejected('PLAN_ENTITY_MISSING')

    def test_port_center_is_not_silently_reinterpreted_as_anchor(self):
        self.r['structure']['boundary_ports'][0]['origin_mm'] = [11, 1, 5.5]
        self.rejected('PLAN_PORT_FRAME_MISMATCH')

    def test_native_float32_position_is_separate_from_design_tolerance(self):
        original = self.r['structure']['boundary_ports'][0]['origin_mm']
        actual = self.n['witness']['structure']['boundary_ports']
        port = next(row for row in actual if row['id'] == self.m['boundary_ports']['bore_top_port']['actual_port_id'])
        self.assertNotEqual(original, port['frame']['origin_mm'])
        result = self.bind()
        self.assertFalse(result['transport_precision']['uses_design_tolerance'])
        self.r['quality_and_budget']['tolerances']['nominal_dimensions']['value'] = 1000
        self.r['structure']['boundary_ports'][0]['origin_mm'][0] += 1e-5
        self.rejected('PLAN_PORT_FRAME_MISMATCH')

    def test_port_normal_cannot_change_beyond_computational_roundoff(self):
        angle = 1e-5
        self.r['structure']['boundary_ports'][0]['normal'] = [math.sin(angle),0,math.cos(angle)]
        self.rejected('PLAN_PORT_FRAME_MISMATCH')

    def test_nonplanar_port_rejected_even_with_kernel_tolerance(self):
        loop_id = self.m['loops']['bore_top']['actual_loop_id']
        structure = self.n['witness']['structure']; loop = next(row for row in structure['loops'] if row['id']==loop_id)
        vertex = structure['vertex_map'][loop['vertex_ids'][2]]
        self.n['witness']['mesh']['vertices'][vertex][2] += 2e-8
        reseal_witness(self.n)
        self.rejected('PLAN_PORT_NOT_PLANAR')

    def test_nominal_design_uses_no_epsilon_even_when_tolerances_are_large(self):
        self.r['quality_and_budget']['tolerances']['nominal_dimensions']['value'] = 1000
        self.r['design']['holes'][0]['radius_mm'] = math.nextafter(8.5, 9.)
        self.rejected('PLAN_PARAMETER_MISMATCH')

    def test_evaluation_batch_is_not_inferred_from_one_native_level(self):
        self.r['evaluation']['levels'] = [1,2,3]
        self.rejected('PLAN_PARAMETER_MISMATCH')

    def test_available_exact_blender_version_is_checked(self):
        self.n['authorship']['parameter_binding']['parameters']['blender_version'] = '5.2.1'
        reseal_author(self.n)
        self.rejected('PLAN_PARAMETER_MISMATCH')

    def test_missing_authored_design_value_not_filled_from_plan(self):
        self.n['authorship']['parameter_binding']['parameters'].pop('edge_bevel')
        reseal_author(self.n)
        self.rejected('PLAN_PARAMETERS_MISSING')

    def test_interfaces_and_min_gap_are_never_certified(self):
        self.r['structure']['boundary_ports'].append(dict(self.r['structure']['boundary_ports'][0], id='second_port'))
        self.r['structure']['interfaces'] = [{'id':'seam','port_a':'bore_top_port','port_b':'second_port','relation':'thickness_pair','minimum_gap_mm':0}]
        self.rejected('PLAN_INTERFACES_UNSUPPORTED')

    def test_different_schedule_cannot_bind_even_with_resealed_author_hash(self):
        self.n['authorship']['schedule_revision'] = 'another_schedule'
        self.n['binding']['schedule_revision'] = 'another_schedule'
        reseal_author(self.n)
        self.rejected('PLAN_AUTHORED_SCHEDULE_REJECTED')

    def test_datum_metadata_cannot_be_forged_into_supported_schedule(self):
        self.n['authorship']['supported_edit_datum'] = {'approved': True}
        reseal_author(self.n)
        self.rejected('PLAN_AUTHORED_SCHEDULE_REJECTED')

    def test_cyclic_control_anchor_difference_needs_explicit_port_seam(self):
        # A separate author/control witness may have another stable cycle
        # anchor; the exact port point_order remains the explicit seam anchor.
        control = next(row for row in self.n['witness']['structure']['loops'] if row['role'] == 'bore_rim_top')
        control['vertex_ids'] = control['vertex_ids'][3:]+control['vertex_ids'][:3]
        first = control['vertex_ids'][0]
        control['anchor'] = {'vertex_id': first, 'position_mm': self.n['witness']['mesh']['vertices'][self.n['witness']['structure']['vertex_map'][first]]}
        self.m['loops']['bore_top']['actual_loop_id'] = control['id']
        reseal_witness(self.n)
        self.assertEqual(self.bind()['status'], 'pass')

    def test_wrong_region_port_does_not_bind_same_kind_of_loop(self):
        bottom = next(row for row in self.n['witness']['structure']['boundary_ports']
                      if row['loop_id'] == self.m['loops']['bore_bottom']['actual_loop_id'])
        self.m['boundary_ports']['bore_top_port']['actual_port_id'] = bottom['id']
        self.m['boundary_ports']['bore_top_port']['point_order'] = bottom['point_order']
        self.rejected('PLAN_PORT_ORDER_MISMATCH')

    def test_transport_policy_required(self):
        self.n['materialization']['policy'] = 'nearest_within_tolerance'
        self.rejected('PLAN_TRANSPORT_UNSUPPORTED')

    def test_legal_storage_permutation_keeps_binding(self):
        obj = copy.deepcopy(self.object); permute_panel(obj)
        self.n = native.validate_native_structure(obj, unit_scale=1., expected_binding=self.report['binding'])
        self.assertEqual(self.bind()['expected_binding'], self.report['binding'])

    def test_nonfinite_and_non_json_mapping_fail_closed(self):
        for bad in (float('nan'), float('inf'), object(), {'bad': (1,2)}):
            self.m = copy.deepcopy(self.mapping); self.m['extra'] = bad
            with self.subTest(bad=repr(bad)), self.assertRaises(kernel.StructureError): self.bind()


if __name__ == '__main__': unittest.main()
