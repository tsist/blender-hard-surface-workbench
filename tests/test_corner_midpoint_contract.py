# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral HOST contract fixtures; no production dimensions or native evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from hardsurface import contract as c
from tests.test_contract import minimal_request


SELECTOR = {'schema': 'corner-columns-midpoint/1.0'}


def parameters(layout='fixed-frame-axis-aligned/1.0', selected=True):
    p = {'id': 'neutral_corner_contract', 'op': 'quad.panel',
         'topology_strategy': 'sparse_control_cage', 'size': [88, 58],
         'corner_radius': 8, 'z_min': -3, 'z_max': 3,
         'holes': [{'id': 'bore', 'kind': 'circle', 'center': [-3, 1], 'radius': 7}],
         'sparse_cage': {'layout': {'schema': layout,
             'feature_frame_mm': [-18, -15, 16, 15], 'corner_guard_mm': 4},
             'insertion_policy': 'axis_plane_v1'}}
    if selected:
        p['sparse_cage']['corner_columns'] = deepcopy(SELECTOR)
    return p


def payloads(p):
    request = minimal_request()
    state = request['params']['design']['state']
    state['features'][0]['program']['steps'] = [deepcopy(p)]
    return {'quad.panel': p, 'step': p, 'state': state,
            'design': request['params']['design'], 'request': request}


def invalid_parameters():
    for value in (None, False, '', [], {}, {'schema': None},
                  {'schema': 'corner-columns-midpoint/1.1'},
                  {'schema': 'corner-columns-midpoint/1.0\n'},
                  {'schema': 'corner-columns-midpoint/1.0', 'coefficients': [1, 1, 1, 1]},
                  {'schema': 'corner-columns-midpoint/1.0', 'optimization': True}):
        p = parameters(); p['sparse_cage']['corner_columns'] = value
        yield 'selector:' + repr(value), p
    for field in ('layout', 'insertion_policy'):
        p = parameters(); p['sparse_cage'].pop(field)
        yield 'missing:' + field, p
        p = parameters(); p['sparse_cage'][field] = None
        yield 'null:' + field, p
    p = parameters(); p['sparse_cage']['layout']['schema'] = 'fixed-frame-axis-aligned/999'
    yield 'unknown-layout', p
    p = parameters(); p['sparse_cage']['insertion_policy'] = 'axis_plane_v2'
    yield 'unknown-policy', p
    for alias in ('corner_column', 'corner_columns_policy', 'midpoint_corners'):
        p = parameters(); p['sparse_cage'][alias] = p['sparse_cage'].pop('corner_columns')
        yield 'alias:' + alias, p
    for field in ('corner_coefficients', 'optimization', 'production_tolerance_mm'):
        p = parameters(); p['sparse_cage'][field] = .1
        yield 'unsupported-control:' + field, p
    for strategy in (None, 'tiled', 'subd_control_cage', 'sparse_annulus', 'local_patch_blocks'):
        p = parameters(); p['local_patch_bounds'] = [-18, -15, 16, 15]
        if strategy is None:
            p.pop('topology_strategy')
        else:
            p['topology_strategy'] = strategy
        yield 'wrong-route:' + repr(strategy), p
    for value in (None, True, 2, 3, 5, 8):
        p = parameters(); p['sparse_cage']['profile_arc_segments'] = value
        yield 'unsupported-profile:' + repr(value), p


class CornerMidpointContractTests(unittest.TestCase):
    def test_selector_is_exact_optional_and_has_no_defaults(self):
        cfg = c.schema('quad.panel')['properties']['sparse_cage']
        selector = cfg['properties']['corner_columns']
        self.assertNotIn('corner_columns', cfg['required'])
        self.assertNotIn('default', selector)
        self.assertEqual(selector['required'], ['schema'])
        self.assertFalse(selector['additionalProperties'])
        self.assertEqual(selector['properties'], {'schema': {'const': SELECTOR['schema']}})

    def test_valid_selector_survives_every_public_wrapper_without_mutation(self):
        for layout in ('fixed-frame-axis-aligned/1.0', 'fixed-frame-axis-aligned/1.1'):
            p = parameters(layout)
            for section, value in payloads(p).items():
                original = deepcopy(value)
                with self.subTest(layout=layout, section=section):
                    result = c._validate(value, c.schema(section))
                    self.assertEqual(value, original)
                    self.assertIn(SELECTOR['schema'].encode(), c.canonical_bytes(result))
            values = payloads(p)
            self.assertEqual(c.validate_state(values['state']),
                             c.resolve_design(values['design']))
            self.assertEqual(c.validate_request(values['request'])['params']['design']['state'],
                             c.validate_state(values['state']))

    def test_invalid_selectors_and_dependencies_reject_every_public_wrapper(self):
        for case, p in invalid_parameters():
            for section, value in payloads(p).items():
                original = deepcopy(value)
                with self.subTest(case=case, section=section), self.assertRaises(c.ContractError):
                    c._validate(value, c.schema(section))
                self.assertEqual(value, original)
            values = payloads(p)
            for validator, value in ((c.validate_state, values['state']),
                                      (c.resolve_design, values['design']),
                                      (c.validate_request, values['request'])):
                with self.subTest(case=case, validator=validator.__name__), self.assertRaises(c.ContractError):
                    validator(value)

    def test_absence_preserves_legacy_normalized_payload_goldens(self):
        # Pinned from the unchanged dev.15 authority, before this opt-in existed.
        expected = {
            'fixed-frame-axis-aligned/1.0': '56ff2b05bb261e0d9890adb3c05fcf023440cfbecfcc84f31082e37dd2c7823f',
            'fixed-frame-axis-aligned/1.1': 'e9c44e886ba131942acd7f4377d97dfdf694678ad3b45026b96071bdf7730cba',
        }
        for layout, golden in expected.items():
            request = payloads(parameters(layout, selected=False))['request']
            result = c.validate_request(request)
            self.assertNotIn(b'corner_columns', c.canonical_bytes(result))
            self.assertEqual(c.fingerprint(result), golden)
        p = parameters(selected=False); p.pop('sparse_cage')
        self.assertNotIn('sparse_cage', c._validate(p, c.schema('quad.panel')))

    def test_corner_profile_is_four_only_while_legacy_bounds_remain(self):
        for selected in (False, True):
            for value in (2, 3, 4, 4.0, 5, 8):
                p = parameters(selected=selected); p['sparse_cage']['profile_arc_segments'] = value
                with self.subTest(selected=selected, value=value):
                    if selected and value != 4:
                        with self.assertRaises(c.ContractError): c._validate(p, c.schema('quad.panel'))
                    else:
                        self.assertEqual(c._validate(p, c.schema('quad.panel'))['sparse_cage']['profile_arc_segments'], value)

    def test_runtime_identifier_keeps_fullmatch_behavior(self):
        p = parameters(); p['id'] += '\n'
        with self.assertRaises(c.ContractError): c._validate(p, c.schema('quad.panel'))

    def test_exported_schema_parity_for_valid_and_invalid_payloads(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('optional jsonschema unavailable')
        validators = {name: Draft202012Validator(c.schema(name))
                      for name in payloads(parameters())}
        cases = [('valid:' + version, parameters(version)) for version in
                 ('fixed-frame-axis-aligned/1.0', 'fixed-frame-axis-aligned/1.1')]
        cases += [('legacy-absent', parameters(selected=False)), *invalid_parameters()]
        for name, p in cases:
            for section, value in payloads(p).items():
                try:
                    c._validate(value, c.schema(section)); accepted = True
                except c.ContractError:
                    accepted = False
                with self.subTest(case=name, section=section):
                    self.assertEqual(validators[section].is_valid(value), accepted)

    def test_public_export_matches_checked_in_schema_bytes(self):
        source = Path(__file__).resolve().parents[1] / 'schemas'
        with tempfile.TemporaryDirectory() as destination:
            for generated in c.export_schemas(destination):
                generated = Path(generated)
                with self.subTest(schema=generated.name):
                    self.assertEqual(generated.read_bytes(), (source / generated.name).read_bytes())


if __name__ == '__main__':
    unittest.main()
