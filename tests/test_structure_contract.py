"""Host-only boundary tests; synthetic fixtures are not approved sample designs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from hardsurface import structure_contract as c

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / 'fixtures' / 'structure-plan.valid.json').read_text())


def claimed_approval(r):
    ref = r['reference_contract']
    ref['state'] = 'approval_evidence_supplied'
    ref['approval_evidence'] = {
        'authority': 'user', 'source_message_id': 'fixture:message:not-a-real-approval',
        'evidence': {'file': '/fixtures/approval/evidence.json', 'sha256': 'e' * 64, 'bytes': 64},
        'reference_package_sha256': ref['package_sha256'], 'design_sha256': c.fingerprint(r['design']),
        'project_id': r['identity']['project_id'], 'verification': 'external_verification_required',
    }
    return r


class StructureContractTests(unittest.TestCase):
    def test_valid_roundtrip_isolated_copy(self):
        original = fixture(); snapshot = copy.deepcopy(original)
        result = c.validate_request(original)
        self.assertEqual(result, original)
        result['design']['holes'][0]['center_mm'][0] = -123
        self.assertEqual(original, snapshot)
        self.assertEqual(c.validate_request(json.dumps(original)), original)
        self.assertEqual(c.validate_request(json.dumps(original).encode()), original)

    def test_schema_export_matches_authority(self):
        self.assertEqual(json.loads((ROOT / 'schemas' / 'structure-plan.schema.json').read_text()), c.schema())
        a = c.schema(); a['properties'].clear()
        self.assertTrue(c.schema()['properties'])

    def test_all_object_shapes_are_closed(self):
        def check(value):
            if isinstance(value, dict):
                if value.get('type') == 'object':
                    self.assertIs(value['additionalProperties'], False)
                for item in value.values(): check(item)
            elif isinstance(value, list):
                for item in value: check(item)
        check(c.schema())

    def test_missing_top_level_fields(self):
        for field in fixture():
            with self.subTest(field=field):
                r = fixture(); del r[field]
                with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_unknown_fields_at_every_object(self):
        def object_paths(value, path=()):
            if isinstance(value, dict):
                yield path
                for key, item in value.items(): yield from object_paths(item, path + (key,))
            elif isinstance(value, list):
                for i, item in enumerate(value): yield from object_paths(item, path + (i,))
        for path in object_paths(fixture()):
            with self.subTest(path=path):
                r = fixture(); target = r
                for key in path: target = target[key]
                target['unexpected_authorization'] = True
                with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_versions_fail_explicitly(self):
        for key in ('schema_version', 'contract_version', 'operation_version', 'evidence_version'):
            with self.subTest(key=key):
                r = fixture(); (r if key == 'schema_version' else r['identity'])[key] = '999.0'
                with self.assertRaises(c.ContractError) as ctx: c.validate_request(r)
                self.assertEqual(ctx.exception.code, 'UNSUPPORTED_VERSION')

    def test_implementation_sha_required(self):
        for bad in (None, '', 'a' * 63, 'A' * 64, '0.2.0', True):
            r = fixture(); r['identity']['implementation_sha256'] = bad
            with self.subTest(value=bad), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_nonfinite_and_bool_geometry_rejected(self):
        for bad in (float('nan'), float('inf'), -float('inf'), True, False, 10 ** 1000):
            r = fixture(); r['design']['holes'][0]['radius_mm'] = bad
            with self.subTest(value=str(bad)[:30]), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_duplicate_json_and_non_json_types(self):
        for bad in ('{"schema_version":"a","schema_version":"b"}', 'NaN', 'Infinity', '1e999', {'bad': object()}, {1: 'x'}):
            with self.subTest(value=repr(bad)), self.assertRaises(c.ContractError): c.validate_request(bad)

    def test_bool_numeric_and_count_boundaries(self):
        for bad in (True, False, 23, 25, 24.5):
            r = fixture(); r['topology']['hole_control_count'] = bad
            with self.subTest(value=bad), self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['topology']['hole_control_count'] = 24.0
        self.assertIs(type(c.validate_request(r)['topology']['hole_control_count']), int)

    def test_no_unit_inference_or_design_defaults(self):
        r = fixture(); r['design']['length_unit'] = 'm'
        with self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); del r['design']['edge_roundover_mm']
        with self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['design']['hole_control_count'] = 24
        with self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['topology']['preview_levels'] = 2
        with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_execution_cannot_be_requested(self):
        for field, bad in [('operation', 'construct.run'), ('execution_mode', 'production'), ('execution_mode', 'execute')]:
            r = fixture(); r[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_bounded_single_hole_domain(self):
        mutators = [
            lambda r: r['design'].update(holes=[]),
            lambda r: r['design']['holes'].append(copy.deepcopy(r['design']['holes'][0])),
            lambda r: r['design']['holes'][0].update(kind='slot'),
            lambda r: r['design']['holes'][0].update(counterbore={'radius': 12}),
            lambda r: r['design'].update(edge_roundover_mm=0),
            lambda r: r['design'].update(z_range_mm=[1, 1]),
            lambda r: r['design']['outline'].update(corner_radius_mm=40),
            lambda r: r['topology'].update(local_patch_bounds_mm=[-1, -1, 1, 1]),
            lambda r: r['topology'].update(local_patch_bounds_mm=[-7, -15, 45, 17]),
            lambda r: r['design'].update(z_range_mm=[-1e308, 1e308]),
        ]
        for i, mutate in enumerate(mutators):
            r = fixture(); mutate(r)
            with self.subTest(case=i), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_reference_state_is_not_approval_boolean(self):
        for field, bad in [('state', 'approved'), ('approval_evidence', True)]:
            r = fixture(); r['reference_contract'][field] = bad
            with self.subTest(field=field), self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['reference_contract']['approved'] = True
        with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_approval_claim_requires_exact_bindings(self):
        r = claimed_approval(fixture()); self.assertEqual(c.validate_request(r), r)
        for field in ('reference_package_sha256', 'design_sha256', 'project_id'):
            bad = copy.deepcopy(r); bad['reference_contract']['approval_evidence'][field] = 'f' * 64
            with self.subTest(field=field), self.assertRaises(c.ContractError) as ctx: c.validate_request(bad)
            self.assertEqual(ctx.exception.code, 'REFERENCE_BINDING_MISMATCH')

    def test_approval_claim_needs_complete_conflict_free_package(self):
        for change in ('missing_evidence', 'conflict', 'incomplete', 'claimed_verified', 'proposed_with_claim'):
            r = claimed_approval(fixture()); ref = r['reference_contract']
            if change == 'missing_evidence': ref['approval_evidence'] = None
            if change == 'conflict': ref['conflicts'] = [{'id': 'conflict', 'description': 'Unresolved dimension'}]
            if change == 'incomplete': ref['artifacts'].pop()
            if change == 'claimed_verified': ref['approval_evidence']['verification'] = 'verified'
            if change == 'proposed_with_claim': ref['state'] = 'proposed'
            with self.subTest(case=change), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_declared_approval_never_unlocks_production(self):
        r = claimed_approval(fixture()); r['requested_usage'] = 'production'
        plan = c.plan_request(r)
        self.assertFalse(plan['construction_authorized'])
        self.assertFalse(plan['qualification']['production_qualified'])
        codes = {b['code'] for b in plan['blockers']}
        self.assertTrue({'REFERENCE_APPROVAL_UNVERIFIED', 'M0_PENDING', 'PRODUCTION_UNQUALIFIED'} <= codes)
        for key in ('native', 'visual', 'reopen', 'holdout'):
            self.assertEqual(plan['qualification'][key], 'not_run')

    def test_proposed_conflicts_remain_visible_blockers(self):
        r = fixture(); r['reference_contract']['conflicts'] = [{'id': 'hole_size', 'description': 'Radius is still undecided'}]
        self.assertIn('REFERENCE_CONFLICTS', {b['code'] for b in c.plan_request(r)['blockers']})

    def test_tolerance_groups_distinct_and_nullable(self):
        r = fixture(); q = r['quality_and_budget']['tolerances']
        self.assertEqual(set(q), {'nominal_dimensions', 'explicit_arc_chord', 'subd_shape', 'screen_observation'})
        self.assertTrue(all(v['value'] is None for v in c.validate_request(r)['quality_and_budget']['tolerances'].values()))
        q['screen_observation']['unit'] = 'mm'
        with self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); del r['quality_and_budget']['tolerances']['subd_shape']
        with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_budget_no_measured_claim_no_bool_zero_negative(self):
        for key in ('wall_seconds', 'peak_ram_bytes', 'control_faces', 'evaluated_faces', 'cpu_threads'):
            for bad in (True, 0, -1, float('inf')):
                r = fixture(); r['quality_and_budget']['budget_limits'][key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['quality_and_budget']['estimates']['status'] = 'measured'
        with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_over_budget_is_explicit_blocker_not_quality_downgrade(self):
        r = fixture(); before = copy.deepcopy(r)
        r['quality_and_budget']['budget_limits']['evaluated_faces'] = 1
        plan = c.plan_request(r)
        self.assertEqual(plan['estimates']['evaluated_faces'], 20000 * 4 ** 3)
        self.assertIn('BUDGET_ESTIMATE_EXCEEDS_LIMIT', {b['code'] for b in plan['blockers']})
        self.assertEqual(r['evaluation'], before['evaluation'])
        self.assertEqual(r['quality_and_budget']['tolerances'], before['quality_and_budget']['tolerances'])

    def test_unknown_estimate_stays_unknown(self):
        r = fixture(); r['quality_and_budget']['estimates']['control_faces'] = None
        p = c.plan_request(r)
        self.assertIsNone(p['estimates']['control_faces'])
        self.assertIsNone(p['estimates']['evaluated_faces'])
        self.assertEqual(p['estimates']['status'], 'unmeasured_estimate')

    def test_fingerprint_stable_key_order_sensitive_array_order(self):
        r = fixture(); reordered = dict(reversed(list(r.items())))
        self.assertEqual(c.fingerprint(r), c.fingerprint(reordered))
        self.assertNotEqual(c.fingerprint([1, 2]), c.fingerprint([2, 1]))
        self.assertEqual(c.plan_request(r)['request_sha256'], c.fingerprint(c.validate_request(r)))

    def test_source_and_reference_output_aliases(self):
        for source_path in (fixture()['source']['snapshot']['file'], fixture()['reference_contract']['artifacts'][0]['artifact']['file']):
            r = fixture(); r['output']['candidate_file'] = source_path
            with self.subTest(path=source_path), self.assertRaises(c.ContractError) as ctx: c.validate_request(r)
            self.assertEqual(ctx.exception.code, 'SOURCE_OUTPUT_ALIAS')
        r = fixture(); r['output']['evidence_directory'] = '/fixtures'
        with self.assertRaises(c.ContractError) as ctx: c.validate_request(r)
        self.assertEqual(ctx.exception.code, 'SOURCE_OUTPUT_ALIAS')

    def test_existing_symlink_and_hardlink_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'original.blend'; source.write_bytes(b'fixture')
            symlink = Path(tmp) / 'link.blend'; symlink.symlink_to(source)
            hardlink = Path(tmp) / 'hard.blend'; os.link(source, hardlink)
            for output in (symlink, hardlink):
                r = fixture(); r['source']['snapshot']['file'] = str(source)
                r['output']['candidate_file'] = str(output)
                with self.subTest(path=str(output)), self.assertRaises(c.ContractError) as ctx: c.validate_request(r)
                self.assertEqual(ctx.exception.code, 'SOURCE_OUTPUT_ALIAS')
            self.assertEqual(source.read_bytes(), b'fixture')

    def test_path_rules(self):
        for value in ('relative.blend', '/tmp/../original.blend', '/tmp//double.blend', '//tmp/double-root.blend', '/tmp/./dot.blend', '/tmp/bad\\file.blend', 'https://site/file.blend', '/', '/tmp/a\x00.blend'):
            r = fixture(); r['output']['candidate_file'] = value
            with self.subTest(path=value), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_semantic_references_and_uniqueness(self):
        mutators = [
            lambda r: r['structure']['loops'][0].update(region_id='missing'),
            lambda r: r['structure']['regions'][0].update(loop_ids=[]),
            lambda r: r['structure']['boundary_ports'][0].update(loop_id='missing'),
            lambda r: r['structure']['boundary_ports'][0].update(normal=[0, 0, 0]),
            lambda r: r['structure']['boundary_ports'][0].update(id='bore_top'),
            lambda r: r['edit_contract'].update(protected_object_ids=['unknown']),
            lambda r: r['edit_contract'].update(target_feature_ids=['unknown']),
            lambda r: r['work_units'][0].update(input_ports=['missing']),
        ]
        for i, mutate in enumerate(mutators):
            r = fixture(); mutate(r)
            with self.subTest(case=i), self.assertRaises(c.ContractError): c.validate_request(r)

    def test_edit_ranges_and_dag(self):
        for ranges in ([], [{'parameter': 'hole.center_x_mm', 'minimum': 12, 'maximum': 10}], [{'parameter': 'hole.center_x_mm', 'minimum': 0, 'maximum': 10}]):
            r = fixture(); r['edit_contract']['parameter_ranges'] = ranges
            with self.subTest(ranges=ranges), self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['work_units'][0]['depends_on'] = ['plan']
        with self.assertRaises(c.ContractError): c.validate_request(r)
        r = fixture(); r['work_units'][0]['depends_on'] = ['missing']
        with self.assertRaises(c.ContractError): c.validate_request(r)

    def test_rejected_plan_does_not_disguise_invalid_input(self):
        r = fixture(); r['design']['holes'] = []
        plan = c.plan_request(r)
        self.assertEqual(plan['status'], 'rejected')
        self.assertIsNone(plan['request_sha256'])
        self.assertFalse(plan['construction_authorized'])
        self.assertTrue(plan['blockers'])

    def test_realtime_is_not_qualified_by_film_plan(self):
        r = fixture(); r['asset_profile']['route'] = 'realtime'
        self.assertIn('REALTIME_CHAIN_NOT_IMPLEMENTED', {b['code'] for b in c.plan_request(r)['blockers']})

    def test_plan_does_not_write_outputs_or_import_blender(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            r = fixture(); r['output']['candidate_file'] = str(Path(tmp) / 'new.blend')
            r['output']['evidence_directory'] = str(Path(tmp) / 'evidence')
            p = c.plan_request(r)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(p['side_effects'], [])
        self.assertNotIn('bpy', sys.modules)

    def test_optional_jsonschema_conformance(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('Optional jsonschema is not installed')
        jsonschema.Draft202012Validator.check_schema(c.schema())
        validator = jsonschema.Draft202012Validator(c.schema())
        validator.validate(fixture())
        validator.validate(claimed_approval(fixture()))
        r = fixture(); r['unknown'] = True
        self.assertTrue(list(validator.iter_errors(r)))


class LegacyMigrationTests(unittest.TestCase):
    def legacy(self):
        return {'id': 'panel', 'op': 'quad.panel', 'topology_strategy': 'subd_control_cage',
                'size': [96, 62], 'center': [0, 0], 'corner_radius': 9, 'edge_bevel': .7,
                'z_min': 0, 'z_max': 5.5, 'holes': [{'id': 'bore', 'kind': 'circle', 'center': [11, 1], 'radius': 8.5}],
                'local_patch_bounds': [-7, -15, 25, 17], 'chord_tolerance': .05,
                'subdivision_cage': {'method': 'CATMULL_CLARK', 'hole_segments': 24, 'preview_levels': 2}}

    def test_explicit_values_separated_no_approval_inheritance(self):
        p = self.legacy(); p['approved'] = True; p['qualification'] = {'visual': 'pass'}
        result = c.migrate_legacy_panel(p, fixture()['identity'])
        skeleton = result['request_skeleton']
        self.assertEqual(result['status'], 'proposed_incomplete')
        self.assertFalse(result['production_runnable'])
        self.assertEqual(skeleton['reference_contract'], {'state': 'proposed', 'approval_evidence': None})
        self.assertEqual(skeleton['design']['edge_roundover_mm'], .7)
        self.assertEqual(skeleton['topology']['hole_control_count'], 24)
        self.assertEqual(skeleton['evaluation']['levels'], [2])
        self.assertNotIn('quality_and_budget', skeleton)
        losses = {entry['field'] for entry in result['losses']}
        self.assertTrue({'approved', 'qualification', 'chord_tolerance'} <= losses)
        self.assertTrue(result['required_additions'])
        with self.assertRaises(c.ContractError): c.validate_request(skeleton)

    def test_no_missing_design_defaults_added(self):
        r = c.migrate_legacy_panel({'op': 'quad.panel'}, fixture()['identity'])
        self.assertNotIn('outline', r['request_skeleton']['design'])
        self.assertNotIn('edge_roundover_mm', r['request_skeleton']['design'])
        self.assertNotIn('holes', r['request_skeleton']['design'])
        self.assertTrue(any('center' in x for x in r['required_additions']))

    def test_unknown_fields_losses_and_copy_isolation(self):
        p = self.legacy(); p['custom_value'] = {'x': 42}; before = copy.deepcopy(p)
        r = c.migrate_legacy_panel(p, fixture()['identity'])
        self.assertIn('custom_value', {x['field'] for x in r['losses']})
        r['request_skeleton']['design']['outline']['size_mm'][0] = 1
        self.assertEqual(p, before)

    def test_unsupported_hole_and_strategy_not_coerced(self):
        p = self.legacy(); p['holes'][0]['kind'] = 'slot'; p['topology_strategy'] = 'tiled'
        r = c.migrate_legacy_panel(p, fixture()['identity'])
        self.assertNotIn('holes', r['request_skeleton']['design'])
        self.assertNotIn('strategy', r['request_skeleton']['topology'])
        self.assertTrue({'holes', 'topology_strategy'} <= {x['field'] for x in r['losses']})

    def test_unmapped_partial_values_have_explicit_losses(self):
        p = {'op': 'quad.panel', 'topology_strategy': 'tiled', 'edge_bevel': .5, 'z_min': 0}
        r = c.migrate_legacy_panel(p, fixture()['identity'])
        losses = {x['field'] for x in r['losses']}
        self.assertTrue({'edge_bevel', 'z_min', 'topology_strategy'} <= losses)

    def test_invalid_identity_and_nonfinite_legacy_reject(self):
        i = fixture()['identity']; i['implementation_sha256'] = 'unknown'
        with self.assertRaises(c.ContractError): c.migrate_legacy_panel(self.legacy(), i)
        p = self.legacy(); p['corner_radius'] = float('nan')
        with self.assertRaises(c.ContractError): c.migrate_legacy_panel(p, fixture()['identity'])


if __name__ == '__main__':
    unittest.main()
