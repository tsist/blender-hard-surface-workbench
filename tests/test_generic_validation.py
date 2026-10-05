# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent neutral fixtures for the declarative public validator."""
import copy
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from hardsurface.contract import ContractError, fingerprint
from hardsurface.io import RuntimeFailure
from hardsurface import generic_validation as geometry
from hardsurface import validation


class GenericSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'neutral-source.blend'
        self.path.write_bytes(b'neutral test descriptor only')
        self.request = {'schema_version': '2.0', 'command': 'hardsurface.validate',
                        'params': {'request_id': 'neutral-check',
                                   'source': {'file': str(self.path), 'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()},
                                   'parts': [{'id': 'left', 'feature_id': 'feature.left'},
                                             {'id': 'right', 'feature_id': 'feature.right'}]}}

    def valid(self, request=None):
        return validation.validate_request(request or self.request)

    def reject(self, request=None):
        with self.assertRaises(ContractError):
            self.valid(request)

    def probe(self, identity='cross', part='left', axis='Z', fixed=None, hits=None):
        return {'id': identity, 'part': part, 'axis': axis, 'fixed_mm': fixed or [1, -3],
                'expected_hits_mm': [4, 9] if hits is None else hits, 'tolerance_mm': .01}

    def test_default_scope_is_complete_selected_visible_set_and_all_pairs(self):
        result = self.valid()
        self.assertTrue(result['params']['require_all_visible'])
        self.assertTrue(result['params']['include_pairs'])
        self.assertEqual(result['params']['contacts'], [])
        self.assertNotIn('profile', result['params'])
        self.assertNotIn('include_pairs', self.request['params'])
        self.assertEqual(validation.schema()['properties']['schema_version']['const'], '2.0')

    def test_inline_data_affects_request_fingerprint(self):
        self.request['params']['probes'] = [self.probe()]
        first = fingerprint(self.valid())
        self.request['params']['probes'][0]['expected_hits_mm'][1] += .2
        self.assertNotEqual(first, fingerprint(self.valid()))

    def test_unknown_fields_profiles_and_scripts_rejected(self):
        for key, value in [('profile', 'anything'), ('script', 'print(1)'), ('plan', {})]:
            request = copy.deepcopy(self.request)
            request['params'][key] = value
            self.reject(request)
        self.request['schema_version'] = '1.0'
        self.reject()

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        with self.assertRaises(ContractError):
            validation.validate_request('{"schema_version":"2.0","schema_version":"2.0"}')
        self.request['params']['probes'] = [self.probe(hits=[float('nan')])]
        self.reject()
        self.request['params']['probes'] = [self.probe(hits=[True])]
        self.reject()

    def test_schema_is_defensive_copy(self):
        value = validation.schema()
        value['properties']['schema_version']['const'] = 'bad'
        self.assertEqual(validation.schema()['properties']['schema_version']['const'], '2.0')

    def test_source_identity_guard(self):
        self.path.write_bytes(b'changed')
        with self.assertRaises(RuntimeFailure) as raised:
            self.valid()
        self.assertEqual(raised.exception.code, 'SOURCE_CHANGED')

    def test_part_and_feature_identity_uniqueness(self):
        for key in ('id', 'feature_id'):
            request = copy.deepcopy(self.request)
            request['params']['parts'][1][key] = request['params']['parts'][0][key]
            self.reject(request)

    def test_part_cap_allows_32_and_forbids_33(self):
        self.request['params']['parts'] = [{'id': f'part{i}', 'feature_id': f'feature{i}'} for i in range(32)]
        self.assertEqual(math.comb(len(self.valid()['params']['parts']), 2), 496)
        self.request['params']['parts'].append({'id': 'extra', 'feature_id': 'extra'})
        self.reject()

    def test_bounds_need_finite_ordered_minima(self):
        for expected in ([0, 0, 0, 0, 2, 3], [0, 1, 0, 3, -1, 4], [0, 0, 0, 2, 3, 1e10]):
            self.request['params']['parts'][0]['bounds_mm'] = {'expected': expected, 'tolerance_mm': .01}
            self.reject()
        self.request['params']['parts'][0]['bounds_mm'] = {'expected': [-2, -3, 5, 4, 8, 9], 'tolerance_mm': 0}
        self.valid()

    def test_missing_part_and_probe_refs_fail_no_silent_skips(self):
        self.request['params']['probes'] = [self.probe(part='absent')]
        self.reject()
        self.request['params']['probes'] = [self.probe()]
        self.request['params']['difference_checks'] = [{'id': 'gap', 'a': {'probe': 'cross', 'index': 0}, 'b': {'probe': 'absent', 'index': 1}, 'expected_mm': -5, 'tolerance_mm': .01}]
        self.reject()
        self.request['params']['difference_checks'][0]['b']['probe'] = 'cross'
        self.request['params']['difference_checks'][0]['b']['index'] = 2
        self.reject()

    def test_probe_order_and_global_check_id_uniqueness(self):
        self.request['params']['probes'] = [self.probe(hits=[9, 4])]
        self.reject()
        self.request['params']['probes'] = [self.probe()]
        self.request['params']['difference_checks'] = [{'id': 'cross', 'a': {'probe': 'cross', 'index': 0}, 'b': {'probe': 'cross', 'index': 1}, 'expected_mm': -5, 'tolerance_mm': .01}]
        self.reject()

    def test_probe_and_expected_hit_aggregate_budgets(self):
        self.request['params']['probes'] = [self.probe(identity=f'ray{i}') for i in range(1001)]
        self.reject()
        self.request['params']['probes'] = [self.probe(identity=f'ray{i}', hits=list(range(1000))) for i in range(11)]
        self.reject()

    def axial_request(self):
        self.request['params']['probes'] = [self.probe('uLow', axis='X', fixed=[2, 6], hits=[-2, 4]),
                                           self.probe('vLow', axis='Y', fixed=[1, 6], hits=[-1, 5]),
                                           self.probe('uHigh', axis='X', fixed=[2, 14], hits=[-2, 4]),
                                           self.probe('vHigh', axis='Y', fixed=[1, 14], hits=[-1, 5])]
        self.request['params']['axis_checks'] = [{'id': 'axis', 'probes': ['uLow', 'vLow', 'uHigh', 'vHigh'], 'separation_mm': 8, 'maximum_degrees': .2}]
        return self.request

    def test_axial_contract_checks_station_geometry(self):
        self.axial_request()
        self.valid()
        for mutation in ('station', 'separation', 'transverse', 'part', 'axis', 'hit_count'):
            request = copy.deepcopy(self.request)
            p = request['params']
            if mutation == 'station': p['probes'][1]['fixed_mm'][1] += 1
            if mutation == 'separation': p['axis_checks'][0]['separation_mm'] += 1
            if mutation == 'transverse': p['probes'][2]['fixed_mm'][0] += 1
            if mutation == 'part': p['probes'][3]['part'] = 'right'
            if mutation == 'axis': p['probes'][2]['axis'] = 'Y'
            if mutation == 'hit_count': p['probes'][2]['expected_hits_mm'] = [1]
            self.reject(request)

    def contact(self):
        return {'pair': ['left', 'right'], 'regions': [{'axis': 'Y', 'plane_mm': 7,
                'outer': {'kind': 'rectangle', 'min_mm': [-8, 3], 'max_mm': [2, 13]},
                'bores': [{'part': 'left', 'center_mm': [-3, 8], 'radius_mm': .75,
                           'position_tolerance_mm': .02, 'chord_tolerance_mm': .03}]}]}

    def test_contact_pairs_regions_and_bore_membership_are_strict(self):
        self.request['params']['contacts'] = [self.contact()]
        self.valid()
        for mutation in ('pair', 'duplicate', 'rectangle', 'bore', 'tolerance'):
            request = copy.deepcopy(self.request)
            contact = request['params']['contacts'][0]
            if mutation == 'pair': contact['pair'][0] = 'unknown'
            if mutation == 'duplicate': request['params']['contacts'].append({**copy.deepcopy(contact), 'pair': ['right', 'left']})
            if mutation == 'rectangle': contact['regions'][0]['outer']['min_mm'][0] = 2
            if mutation == 'bore': contact['regions'][0]['bores'][0]['part'] = 'unknown'
            if mutation == 'tolerance': contact['regions'][0]['bores'][0]['position_tolerance_mm'] = .051
            self.reject(request)

    def test_contact_aggregate_regions_and_bores_bounded(self):
        self.request['params']['parts'] = [{'id': f'p{i}', 'feature_id': f'f{i}'} for i in range(24)]
        region = {'axis': 'X', 'plane_mm': 3, 'outer': {'kind': 'disk', 'center_mm': [4, 5], 'radius_mm': 2}}
        self.request['params']['contacts'] = [{'pair': list(pair), 'regions': [region]} for pair in itertools.combinations([f'p{i}' for i in range(24)], 2)]
        self.reject()

    def fake_run(self, request=None, extra=False, duplicate=False, broken=False):
        params = self.valid(request)['params']

        class SceneObject:
            type = 'MESH'
            hide_render = False
            hide_viewport = False
            modifiers = []

            def __init__(self, feature):
                self.feature = self.name = feature

            def get(self, key):
                return self.feature if key == 'hs_feature_id' else None

        objects = [SceneObject(part['feature_id']) for part in params['parts']]
        if extra:
            objects.append(SceneObject('unselected.extra'))
        if duplicate:
            objects.append(SceneObject(params['parts'][0]['feature_id']))
        scene = SimpleNamespace(objects=objects, unit_settings=SimpleNamespace(system='METRIC', scale_length=1))
        bpy = SimpleNamespace(context=SimpleNamespace(scene=scene, evaluated_depsgraph_get=lambda: None),
                              data=SimpleNamespace(filepath=str(self.path)), app=SimpleNamespace(version_string='test-only'))

        def mesh(label, obj, graph):
            index = objects.index(obj)
            vertices, triangles = GeometryHelperTests().tetra(index * 20)
            if broken and index == 0:
                triangles = triangles[:-1]
            return SimpleNamespace(label=label, vertices=vertices, tris=triangles,
                                   bounds=geometry.bbox(vertices), precision={}, fallback_records=[])

        with patch.dict(sys.modules, bpy=bpy), patch.object(geometry, 'Mesh', side_effect=mesh):
            return geometry.run(params)

    def test_runtime_enumerates_every_selected_pair(self):
        self.request['params']['parts'].append({'id': 'third', 'feature_id': 'feature.third'})
        report = self.fake_run()
        self.assertEqual([row['pair'] for row in report['pairs']], [['left', 'right'], ['left', 'third'], ['right', 'third']])
        self.assertEqual(report['status'], 'pass')

    def test_runtime_requires_all_visible_unless_explicit_partial(self):
        self.assertEqual(self.fake_run(extra=True)['status'], 'fail')
        self.request['params']['require_all_visible'] = False
        self.assertEqual(self.fake_run(extra=True)['status'], 'pass')

    def test_runtime_pair_disable_and_topology_gating_are_explicit(self):
        self.request['params']['include_pairs'] = False
        report = self.fake_run()
        self.assertEqual(report['status'], 'not_run')
        self.assertEqual(report['pair_status'], 'not_run')
        self.assertTrue(all(row['status'] == 'not_run' for row in report['pairs']))
        self.request['params']['include_pairs'] = True
        report = self.fake_run(broken=True)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['pair_status'], 'not_run')

    def test_runtime_ambiguous_feature_selection_fails(self):
        with self.assertRaises(RuntimeFailure) as raised:
            self.fake_run(duplicate=True)
        self.assertEqual(raised.exception.code, 'VALIDATION_SELECTION')


class GeometryHelperTests(unittest.TestCase):
    def test_axis_rays_derive_span_from_actual_bounds_at_large_offset(self):
        bounds = [230000, -750000, 880000, 230009, -749987, 880017]
        for axis, index in geometry.AXIS.items():
            origin, direction, span = geometry.axis_ray(bounds, axis, [12, 34])
            self.assertLess(origin[index], bounds[index])
            self.assertGreater(origin[index] + span, bounds[index + 3])
            self.assertLess(span, 18)
            self.assertEqual(direction[index], 1)
            self.assertEqual([origin[k] for k in range(3) if k != index], [12, 34])

    def test_axis_plane_coordinate_permutations(self):
        self.assertEqual(geometry.plane_coordinates([2, 4, 7], 'X'), (4, 7, 2))
        self.assertEqual(geometry.plane_coordinates([2, 4, 7], 'Y'), (2, 7, 4))
        self.assertEqual(geometry.plane_coordinates([2, 4, 7], 'Z'), (2, 4, 7))

    def test_bvh_precision_allows_translated_small_mesh_and_rejects_large_extent(self):
        local = [-8, -6, -4, 12, 9, 7]
        shifted = [coordinate + 75000000 for coordinate in local]
        self.assertEqual(geometry.bvh_precision(local), geometry.bvh_precision(shifted))
        with self.assertRaises(RuntimeFailure) as raised:
            geometry.bvh_precision([-20000, -1, -1, 20000, 1, 1])
        self.assertEqual(raised.exception.code, 'VALIDATION_PRECISION_UNSUPPORTED')
        self.assertEqual(raised.exception.details['numeric_epsilon_mm'], .002)

    def test_bounds_and_ray_errors_keep_caller_tolerances(self):
        expected = {'expected': [-2, 3, 5, 7, 11, 13], 'tolerance_mm': .02}
        self.assertEqual(geometry.bounds_result('bounds', expected['expected'], expected)['status'], 'pass')
        self.assertEqual(geometry.bounds_result('bounds', [-2, 3, 5, 7, 11, 13.03], expected)['status'], 'fail')
        probe = {'id': 'ray', 'expected_hits_mm': [5, 9], 'tolerance_mm': .01}
        self.assertEqual(geometry.probe_result(probe, [5, 9])['status'], 'pass')
        self.assertEqual(geometry.probe_result(probe, [5])['status'], 'fail')
        self.assertEqual(geometry.probe_result({**probe, 'expected_hits_mm': []}, [])['status'], 'pass')

    def test_hit_difference_is_a_minus_b_and_missing_actual_hit_fails(self):
        params = {'difference_checks': [{'id': 'thickness', 'a': {'probe': 'ray', 'index': 1}, 'b': {'probe': 'ray', 'index': 0}, 'expected_mm': 6, 'tolerance_mm': .01}], 'axis_checks': []}
        result = geometry.derived_results(params, {'ray': {'actual_hits_mm': [4, 10]}})[0]
        self.assertEqual(result['actual_mm'], 6)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(geometry.derived_results(params, {'ray': {'actual_hits_mm': [4]}})[0]['status'], 'fail')

    def test_axial_angle_actual_displacement_and_missing_hits_fail(self):
        params = {'difference_checks': [], 'axis_checks': [{'id': 'axial', 'probes': ['a', 'b', 'c', 'd'], 'separation_mm': 10, 'maximum_degrees': 1}]}
        rays = {key: {'actual_hits_mm': [2, 6]} for key in 'abcd'}
        self.assertEqual(geometry.derived_results(params, rays)[0]['status'], 'pass')
        rays['c']['actual_hits_mm'] = [3, 7]
        result = geometry.derived_results(params, rays)[0]
        self.assertEqual(result['status'], 'fail')
        self.assertAlmostEqual(result['actual_degrees'], math.degrees(math.atan(.1)))
        rays['d']['actual_hits_mm'] = []
        self.assertEqual(geometry.derived_results(params, rays)[0]['status'], 'fail')

    def tetra(self, offset=0):
        v = [(offset, 0, 0), (offset + 3, 0, 0), (offset, 4, 0), (offset, 0, 5)]
        return v, [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]

    def test_actual_topology_is_closed_outward_and_translation_invariant(self):
        for offset in (0, 64000000):
            vertices, triangles = self.tetra(offset)
            report = geometry.evaluated_topology(vertices, triangles)
            self.assertEqual(report['status'], 'pass')
            self.assertAlmostEqual(report['component_signed_volume_mm3'][0], 10)
            self.assertIn('no source-quad', report['scope'])
        self.assertEqual(geometry.evaluated_topology(vertices, triangles[:-1])['status'], 'fail')
        self.assertEqual(geometry.evaluated_topology(vertices, [tuple(reversed(t)) for t in triangles])['status'], 'fail')
        self.assertEqual(geometry.evaluated_topology(vertices + [(0, 0, 8)], triangles)['status'], 'fail')

    def test_contacts_default_forbidden_and_bounded_on_all_axes(self):
        point = (2, 4, 7)
        self.assertFalse(geometry.contact_allowed('one', 'two', [point], {}))
        for axis, index in geometry.AXIS.items():
            uvz = geometry.plane_coordinates(point, axis)
            region = {'axis': axis, 'plane_mm': uvz[2], 'outer': {'kind': 'disk', 'center_mm': list(uvz[:2]), 'radius_mm': 2}}
            contacts = {frozenset(('one', 'two')): [{'spec': region, 'boundaries': []}]}
            self.assertTrue(geometry.contact_allowed('one', 'two', [point], contacts))
            shifted = list(point); shifted[index] += .003
            self.assertFalse(geometry.contact_allowed('one', 'two', [shifted], contacts))
            shifted = list(point); shifted[(index + 1) % 3] += 3
            self.assertFalse(geometry.contact_allowed('one', 'two', [shifted], contacts))

    def ring(self, axis):
        center, plane = (2.4, -3.1), 8.25
        vertices = []
        for radius in (.75, 2.2):
            for i in range(24):
                angle = 2 * math.pi * i / 24
                uvz = [center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle), plane]
                order = [k for k in range(3) if k != geometry.AXIS[axis]] + [geometry.AXIS[axis]]
                xyz = [0, 0, 0]
                for k, value in zip(order, uvz): xyz[k] = value
                vertices.append(tuple(xyz))
        triangles = []
        for i in range(24):
            j = (i + 1) % 24
            triangles.extend([(i, i + 24, j + 24), (i, j + 24, j)])
        region = {'axis': axis, 'plane_mm': plane, 'outer': {'kind': 'disk', 'center_mm': list(center), 'radius_mm': 2.2},
                  'bores': [{'part': 'one', 'center_mm': list(center), 'radius_mm': .75, 'position_tolerance_mm': .02, 'chord_tolerance_mm': .03}]}
        return SimpleNamespace(vertices=vertices, tris=triangles), region

    def test_bore_edge_hull_roundoff_is_not_physical_tolerance(self):
        mesh,region=self.ring('Z')
        contacts,_,_=geometry.qualify_contacts([{'pair':['one','two'],'regions':[region]}],{'one':mesh})
        boundary=contacts[frozenset(('one','two'))][0]['boundaries'][0]
        a,b=boundary['loop_mm'][:2]
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        inward=(-dy/length,dx/length)
        def points(offset):
            return [(a[0]+t*dx+offset*inward[0],a[1]+t*dy+offset*inward[1],a[2]) for t in (.27,.79)]
        self.assertTrue(geometry._avoids_bore(points(1e-14),boundary))
        for inward_distance in (1e-6,1e-4,.003):
            self.assertFalse(geometry._avoids_bore(points(inward_distance),boundary))

    def test_actual_bore_qualifies_in_xyz_planes_and_missing_bore_fails(self):
        for axis in 'XYZ':
            mesh, region = self.ring(axis)
            contacts, evidence, checks = geometry.qualify_contacts([{'pair': ['one', 'two'], 'regions': [region]}], {'one': mesh})
            self.assertEqual(checks[0]['status'], 'pass', evidence)
            self.assertEqual(evidence[0]['world_axis'], axis)
            self.assertTrue(geometry.contact_allowed('one', 'two', [mesh.vertices[0]], contacts))
            region['bores'][0]['radius_mm'] = 1.1
            _, evidence, checks = geometry.qualify_contacts([{'pair': ['one', 'two'], 'regions': [region]}], {'one': mesh})
            self.assertEqual(checks[0]['status'], 'fail')

    def test_bore_hole_is_not_licensed_by_endpoints_or_covering_polygon(self):
        mesh, region = self.ring('Z')
        contacts, _, _ = geometry.qualify_contacts([{'pair': ['one', 'two'], 'regions': [region]}], {'one': mesh})
        cx, cy = region['outer']['center_mm']; z = region['plane_mm']
        self.assertFalse(geometry.contact_allowed('one', 'two', [(cx, cy, z)], contacts))
        self.assertFalse(geometry.contact_allowed('one', 'two', [(cx - 1, cy, z), (cx + 1, cy, z)], contacts))
        self.assertFalse(geometry.contact_allowed('one', 'two', [(cx + x, cy + y, z) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))], contacts))
        self.assertTrue(geometry.contact_allowed('one', 'two', [(cx + 1, cy, z), (cx + 1.5, cy, z)], contacts))

    def test_entire_witness_set_must_fit_one_region(self):
        regions = [{'spec': {'axis': 'Z', 'plane_mm': 5, 'outer': {'kind': 'disk', 'center_mm': [x, 0], 'radius_mm': .5}}, 'boundaries': []} for x in (-2, 2)]
        contacts = {frozenset(('one', 'two')): regions}
        self.assertFalse(geometry.contact_allowed('one', 'two', [(-2, 0, 5), (2, 0, 5)], contacts))


if __name__ == '__main__':
    unittest.main()
