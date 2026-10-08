# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral complete-source HOST regressions; no Blender or design qualification."""
from copy import deepcopy
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from hardsurface import host
from hardsurface.io import RuntimeFailure
from hardsurface.quad_geometry import build, planning_evidence
from hardsurface.quad_quality import DEFAULT_POLICY, validate_mesh
from hardsurface.sparse_panel_geometry import build_sparse_panel
from hardsurface.sparse_patch_graph import validate_sparse_mesh
from hardsurface.sparse_source_preflight import (DOMAINS, REGIONS, REQUIRED_GATES,
    audit_sparse_source, require_sparse_source_preflight, aggregate_sparse_source_preflights,
    sparse_source_binding, _report_sha, _sha)
from tests.test_sparse_fixed_frame import parameters
from test_sparse_source_stage import source_request


def request_for(p):
    request = source_request()
    request['params']['design']['state']['features'][0]['program']['steps'] = [deepcopy(p)]
    return request


class SparseSourcePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters, cls.meshes, cls.reports = {}, {}, {}
        for state, fraction in (('valid', .15), ('valid_b', .15), ('high_aspect', .12), ('float32_boundary', .14)):
            p = parameters()
            if state == 'valid_b':
                p['sparse_cage']['hole_planar_support'] = True
            p['sparse_cage']['wall_support_fraction'] = fraction
            mesh = build_sparse_panel(p, feature_id='neutral-feature')
            cls.parameters[state], cls.meshes[state] = p, mesh
            cls.reports[state] = audit_sparse_source(mesh, feature_id='neutral-feature')

    def reject(self, report, **kwargs):
        with self.assertRaises(RuntimeFailure) as caught:
            require_sparse_source_preflight(report, **kwargs)
        self.assertEqual(caught.exception.code, 'SPARSE_SOURCE_PREFLIGHT_FAILED')
        self.assertIs(caught.exception.details['report'], report)
        return caught.exception.details

    def test_valid_complete_fixed_frame_passes_strict_and_without_mutation(self):
        mesh, report = self.meshes['valid'], self.reports['valid']
        self.assertIs(require_sparse_source_preflight(report, mesh=mesh), report)
        self.assertIs(report['passed'], True)
        self.assertEqual(set(report['gates']), set(REQUIRED_GATES))
        self.assertTrue(all(g['passed'] is True and g['status'] == 'pass' for g in report['gates'].values()))
        self.assertEqual(mesh, build_sparse_panel(self.parameters['valid'], feature_id='neutral-feature'))
        self.assertEqual(report['report_sha256'], _report_sha(report))
        from hardsurface.contract import fingerprint
        self.assertEqual(len(fingerprint(report)), 64)

    def test_all_regions_all_polygons_same_provenance_and_default_policy(self):
        mesh, report = self.meshes['valid'], self.reports['valid']
        for name, domain in report['coordinate_domains'].items():
            with self.subTest(domain=name):
                self.assertEqual(domain['quality']['policy'], DEFAULT_POLICY)
                self.assertEqual(domain['quality']['counts']['faces'], len(mesh['faces']))
                self.assertEqual(domain['quality']['finding_count'], 0)
                self.assertEqual(domain['provenance_sha256'], _sha(mesh['face_provenance']))
                self.assertEqual(set(domain['regions']), set(REGIONS))
                self.assertEqual(sorted(i for r in domain['regions'].values() for i in r['face_indices']),
                                 list(range(len(mesh['faces']))))
                self.assertEqual(sum(r['measured_faces'] for r in domain['regions'].values()), len(mesh['faces']))
                self.assertGreater(domain['regions']['side']['maximum_support_band_aspect_ratio'], 90.)
                self.assertEqual(domain['self_intersections']['triangles'], 4*len(mesh['faces']))
                self.assertEqual(domain['self_intersections']['status'], 'pass')
                self.assertGreater(domain['self_intersections']['exact_pairs_checked'], 0)

    def test_graph_pass_cannot_waive_side_support_aspect_over_100(self):
        mesh, report = self.meshes['high_aspect'], self.reports['high_aspect']
        self.assertEqual(validate_sparse_mesh(mesh)['status'], 'pass')
        self.assertIs(report['gates']['authored_identity']['passed'], True)
        self.assertIs(report['passed'], False)
        for name in DOMAINS:
            quality = report['coordinate_domains'][name]['quality']
            self.assertEqual(quality['policy']['max_support_band_aspect_ratio'], 100.)
            self.assertEqual(quality['finding_counts'], {'ASPECT_RATIO_EXCEEDED': 8})
            finding = report['gates'][name+'.quad_quality']['findings'][0]
            self.assertGreater(finding['measured'], 100.)
            self.assertEqual(finding['maximum'], 100.)
            self.assertIs(finding['support_band'], True)
            self.assertTrue(finding['semantic_face_ids'])
            self.assertTrue(all('side/' in sid for sid in finding['semantic_face_ids']))
            self.assertIs(report['gates'][name+'.self_intersections']['passed'], True)
        details = self.reject(report, mesh=mesh)
        self.assertEqual({x['gate'] for x in details['failed_gates']},
                         {name+'.quad_quality' for name in DOMAINS})

    def test_exact_float32_metre_storage_can_fail_when_authored_metres_pass(self):
        mesh, report = self.meshes['float32_boundary'], self.reports['float32_boundary']
        self.assertIs(report['gates']['authored_metres.quad_quality']['passed'], True)
        self.assertIs(report['gates']['float32_metres.quad_quality']['passed'], False)
        exact = [[struct.unpack('f', struct.pack('f', x*.001))[0] for x in v] for v in mesh['vertices_mm']]
        native_policy_prediction = validate_mesh(exact, mesh['faces'], face_provenance=mesh['face_provenance'])
        domain = report['coordinate_domains']['float32_metres']
        self.assertEqual(domain['vertices_sha256'], _sha(exact))
        self.assertEqual(domain['quality']['metrics'], native_policy_prediction['metrics'])
        self.assertEqual(domain['quality']['finding_counts'], {'ASPECT_RATIO_EXCEEDED': 4})
        self.assertGreater(domain['quality']['metrics']['maximum_aspect_ratio'], 100.)
        self.assertLess(report['coordinate_domains']['authored_metres']['quality']['metrics']['maximum_aspect_ratio'], 100.)
        self.reject(report)

    def test_required_gate_false_missing_not_run_or_truthy_never_passes(self):
        for name in REQUIRED_GATES:
            for mutation in ('missing', 'false', 'not_run', 'truthy'):
                report = deepcopy(self.reports['valid'])
                if mutation == 'missing':
                    report['gates'].pop(name)
                elif mutation == 'false':
                    report['gates'][name]['passed'] = False
                elif mutation == 'not_run':
                    report['gates'][name]['status'] = 'not_run'
                else:
                    report['gates'][name]['passed'] = 1
                report['report_sha256'] = _report_sha(report)
                with self.subTest(gate=name, mutation=mutation):
                    self.reject(report)

    def test_complete_regions_and_coverage_cannot_be_omitted(self):
        for group in REGIONS:
            report = deepcopy(self.reports['valid'])
            report['coordinate_domains']['float32_metres']['regions'].pop(group)
            report['report_sha256'] = _report_sha(report)
            with self.subTest(region=group):
                self.reject(report)
        report = deepcopy(self.reports['valid'])
        report['coordinate_domains']['authored_metres']['regions']['side']['face_indices'][0] = 0
        report['report_sha256'] = _report_sha(report)
        self.reject(report)

    def test_report_schema_source_binding_and_metric_tamper_fail_closed(self):
        for mutation in ('schema', 'extra', 'digest', 'binding', 'policy', 'whole_metrics', 'region_metrics',
                         'false_zero', 'gate_findings', 'intersection_evidence'):
            report = deepcopy(self.reports['valid'])
            if mutation == 'schema':
                report['schema_version'] = 'unsupported'
            elif mutation == 'extra':
                report['native_passed'] = True
            elif mutation == 'digest':
                report['report_sha256'] = '0'*64
            elif mutation == 'binding':
                report['source_binding']['faces_sha256'] = '0'*64
            elif mutation == 'policy':
                report['coordinate_domains']['float32_metres']['quality']['policy']['max_support_band_aspect_ratio'] = 101.
            elif mutation == 'whole_metrics':
                report['coordinate_domains']['authored_metres']['quality']['metrics']['maximum_aspect_ratio'] = 1.
            elif mutation == 'region_metrics':
                report['coordinate_domains']['authored_metres']['regions']['side']['metrics']['maximum_aspect_ratio'] = 1.
            elif mutation == 'false_zero':
                report['coordinate_domains']['authored_metres']['quality']['finding_count'] = False
            elif mutation == 'gate_findings':
                report['coordinate_domains']['authored_metres']['gates']['quad_quality']['finding_count'] = 1
            else:
                report['coordinate_domains']['authored_metres']['self_intersections'] = {
                    **report['coordinate_domains']['authored_metres']['self_intersections'], 'exact_pairs_checked': -1}
            if mutation != 'digest':
                report['report_sha256'] = _report_sha(report)
            with self.subTest(mutation=mutation):
                self.reject(report)

    def test_audit_cannot_be_reused_for_another_mesh_or_authorship(self):
        for name in ('vertices_mm', 'face_provenance', 'authorship_sha256'):
            mesh = deepcopy(self.meshes['valid'])
            if name == 'vertices_mm':
                mesh[name][0][0] += .001
            elif name == 'face_provenance':
                mesh[name][0]['support_band'] = not mesh[name][0]['support_band']
            else:
                mesh[name] = '0'*64
            with self.subTest(field=name):
                self.reject(self.reports['valid'], mesh=mesh)

    def test_exact_float32_domain_cannot_be_substituted_or_rebound(self):
        for mutation in ('vertices_sha', 'copy_authored_domain', 'storage_transform', 'rebind_both'):
            report = deepcopy(self.reports['valid'])
            domain = report['coordinate_domains']['float32_metres']
            if mutation == 'vertices_sha':
                domain['vertices_sha256'] = '0'*64
            elif mutation == 'copy_authored_domain':
                domain = deepcopy(report['coordinate_domains']['authored_metres'])
                report['coordinate_domains']['float32_metres'] = domain
                for check, gate in domain['gates'].items():
                    report['gates']['float32_metres.'+check] = gate
            elif mutation == 'storage_transform':
                domain['storage_transform'] = 'authored coordinates reused without float32 rounding'
            else:
                domain['vertices_sha256'] = '0'*64
                report['source_binding']['float32_metres_vertices_sha256'] = '0'*64
            report['report_sha256'] = _report_sha(report)
            with self.subTest(mutation=mutation):
                self.reject(report, mesh=self.meshes['valid'])
                aggregate = aggregate_sparse_source_preflights({'A': report}, required_states=['A'],
                    expected_bindings={'A': sparse_source_binding(self.meshes['valid'])})
                self.assertIs(aggregate['passed'], False)

    def test_source_available_recomputes_consistently_falsified_metrics(self):
        report = deepcopy(self.reports['valid'])
        domain = report['coordinate_domains']['authored_metres']
        domain['quality']['metrics']['maximum_quad_plane_distance_m'] = 1e-9
        for region in domain['regions'].values():
            region['metrics']['maximum_quad_plane_distance_m'] = 1e-9
        report['report_sha256'] = _report_sha(report)
        details = self.reject(report, mesh=self.meshes['valid'])
        self.assertIn('measurements differ', details['reason'])

    def test_nonfinite_source_has_serializable_explicit_failures(self):
        mesh = deepcopy(self.meshes['valid'])
        mesh['vertices_mm'][0][0] = float('nan')
        report = audit_sparse_source(mesh)
        self.assertIs(report['passed'], False)
        self.assertIs(report['gates']['finite_structure']['passed'], False)
        self.assertIsNone(report['source_binding'])
        json.dumps(report, allow_nan=False)
        self.reject(report)

    def test_missing_source_groups_fail_even_when_summary_claims_complete(self):
        mesh = deepcopy(self.meshes['valid'])
        for row in mesh['face_provenance']:
            if row['region_group'] == 'side':
                row['region_group'] = 'top'
        report = audit_sparse_source(mesh)
        self.assertIs(report['gates']['surface_coverage']['passed'], False)
        self.assertIs(report['gates']['authored_identity']['passed'], False)
        self.assertIs(report['passed'], False)

    def test_strict_aggregate_requires_exact_nonempty_unique_state_coverage(self):
        valid = self.reports['valid']
        bindings = {'A': sparse_source_binding(self.meshes['valid']),
                    'B': sparse_source_binding(self.meshes['valid_b'])}
        for states in ([], ['A', 'A'], [''], ['A']*257, None):
            with self.subTest(states=states):
                self.assertIs(aggregate_sparse_source_preflights({}, required_states=states)['passed'], False)
        for reports in ({}, {'A': valid}, {'A': valid, 'B': valid, 'extra': valid}, {'A': valid, 'B': None}):
            with self.subTest(observed=list(reports)):
                self.assertIs(aggregate_sparse_source_preflights(reports, required_states=['A', 'B'])['passed'], False)
        result = aggregate_sparse_source_preflights({'A': valid, 'B': self.reports['valid_b']},
            required_states=['A', 'B'], expected_bindings=bindings, require_pass=True)
        self.assertIs(result['passed'], True)
        self.assertEqual(set(result['state_report_sha256']), {'A', 'B'})

    def test_aggregate_never_accepts_summary_counts_or_failed_individual_state(self):
        bindings = {'A': sparse_source_binding(self.meshes['valid']),
                    'B': sparse_source_binding(self.meshes['valid_b'])}
        for body in (self.reports['high_aspect'], self.reports['float32_boundary'],
                     {'passed': True, 'tests': 999, 'failures': []}, {'status': 'not_run'}):
            result = aggregate_sparse_source_preflights({'A': self.reports['valid'], 'B': body},
                required_states=['A', 'B'], expected_bindings=bindings)
            self.assertIs(result['passed'], False)
            self.assertEqual(result['failures'][0]['state'], 'B')
            with self.assertRaises(RuntimeFailure) as caught:
                aggregate_sparse_source_preflights({'A': self.reports['valid'], 'B': body},
                    required_states=['A', 'B'], expected_bindings=bindings, require_pass=True)
            self.assertEqual(caught.exception.code, 'SPARSE_SOURCE_PREFLIGHT_FAILED')

    def test_aggregate_rejects_missing_swapped_or_replayed_expected_state_bindings(self):
        reports = {'A': self.reports['valid'], 'B': self.reports['valid_b']}
        a, b = sparse_source_binding(self.meshes['valid']), sparse_source_binding(self.meshes['valid_b'])
        for bindings in (None, {}, {'A': a}, {'A': a, 'B': b, 'extra': a}, {'A': b, 'B': a}):
            with self.subTest(bindings=bindings):
                result = aggregate_sparse_source_preflights(reports, required_states=['A', 'B'], expected_bindings=bindings)
                self.assertIs(result['passed'], False)
        for bindings in ({'A': a, 'B': b}, {'A': a, 'B': a}):
            result = aggregate_sparse_source_preflights({'A': self.reports['valid'], 'B': self.reports['valid']},
                required_states=['A', 'B'], expected_bindings=bindings)
            self.assertIs(result['passed'], False)
        self.assertEqual(a, self.reports['valid']['source_binding'])

    def test_no_host_pass_claims_native_or_subdivision_qualification(self):
        for report in self.reports.values():
            for field in ('native_verification', 'subdivision_qualification', 'shape_acceptance', 'visual_acceptance'):
                self.assertEqual(report[field], 'not_run')

    def test_planning_gate_is_fresh_and_rejects_before_cli_spawn_or_enqueue(self):
        root = Path(tempfile.mkdtemp(prefix='neutral-sparse-preflight-cli-'))
        path = root/'request.json'
        path.write_text(json.dumps(request_for(self.parameters['high_aspect'])))
        with mock.patch.object(host.subprocess, 'Popen', side_effect=AssertionError('Native/supervisor spawn forbidden')) as spawn, \
             mock.patch.object(host, 'store_for', side_effect=AssertionError('Enqueue forbidden')) as store, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            result = host.main(['hardsurface', 'plan', '--request', str(path)])
        self.assertEqual(result, 2)
        error = json.loads(output.getvalue())['error']
        self.assertEqual(error['code'], 'SPARSE_SOURCE_PREFLIGHT_FAILED')
        self.assertFalse(error['details']['report']['passed'])
        self.assertTrue(error['details']['report']['coordinate_domains'])
        spawn.assert_not_called()
        store.assert_not_called()

    def test_raw_constructor_and_legacy_planning_are_compatibility_only_unchanged(self):
        p = deepcopy(self.parameters['high_aspect'])
        mesh = build(p, 'neutral-feature')
        self.assertEqual(mesh['faces'], self.meshes['high_aspect']['faces'])
        self.assertEqual(mesh['vertices_mm'], self.meshes['high_aspect']['vertices_mm'])
        p['sparse_cage'].pop('layout')
        with mock.patch('hardsurface.sparse_source_preflight.audit_sparse_source', side_effect=AssertionError('Legacy read/build must stay unchanged')):
            evidence = planning_evidence(p, 'neutral-feature')
        self.assertNotIn('source_preflight', evidence)


class SparseConstructionDigestTests(unittest.TestCase):
    def test_signed_zero_and_unicode_preserve_both_existing_constructor_hashes(self):
        p = parameters()
        p.update(z_min=-8.0, z_max=-0.0)
        feature = 'neutral-构件'
        direct = build_sparse_panel(p, feature_id=feature)
        entry = build(p, feature)
        self.assertNotEqual(direct['construction_sha256'], entry['construction_sha256'])
        self.assertEqual(direct['vertices_mm'], entry['vertices_mm'])
        for mesh in (direct, entry):
            before = deepcopy(mesh)
            report = audit_sparse_source(mesh, feature_id=feature, require_pass=True)
            self.assertIs(report['passed'], True)
            self.assertEqual(report['source_binding']['construction_sha256'], mesh['construction_sha256'])
            self.assertEqual(mesh, before)


if __name__ == '__main__':
    unittest.main()
