"""Host-only development regression tests for optional reflected-strip observation."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from hardsurface import observation as o
from hardsurface.contract import ContractError
from hardsurface.io import RuntimeFailure, descriptor

ROOT = Path(__file__).resolve().parents[1]


def request():
    return {'schema_version': '1.0', 'command': 'hardsurface.observe', 'params': {
        'request_id': 'reflection.host.fixture',
        'source': {'file': '/tmp/numeric.blend', 'expected_sha256': 'a' * 64},
        'wire': {'enabled': False}, 'diagnostic_preset': 'reflection_strips',
        'views': [{'name': 'front', 'direction': 'front', 'visible_feature_ids': ['block']}],
    }}


class ReflectionDiagnosticTests(unittest.TestCase):
    def test_legacy_default_unchanged(self):
        q = request(); del q['params']['diagnostic_preset']; del q['params']['wire']
        p = o.validate_request(q)['params']
        self.assertEqual(p['diagnostic_preset'], 'neutral')
        self.assertTrue(p['wire']['enabled'])
        settings = o.diagnostic_settings('neutral')
        self.assertEqual(settings['samples'], 32)
        self.assertTrue(settings['denoising'])
        self.assertEqual(settings['material_override']['roughness'], .5)
        self.assertEqual(settings['material_override']['metallic'], 0)
        self.assertEqual(settings['light_shape'], 'DISK')
        self.assertEqual(settings['lights_camera_basis'], [[-3, 4, 6, 500], [4, 1, 3, 300], [0, -3, -4, 350]])
        self.assertEqual(settings['light_size_extent'], 4)
        self.assertEqual(settings['world_strength'], .4)

    def test_explicit_reflection_normalizes_without_mutation(self):
        q = request(); before = copy.deepcopy(q)
        p = o.validate_request(q)['params']
        self.assertEqual(q, before)
        self.assertEqual(p['diagnostic_preset'], 'reflection_strips')
        self.assertFalse(p['wire']['enabled'])
        self.assertEqual(p['views'][0]['mesh_state'], 'evaluated')

    def test_reflection_wire_must_be_explicitly_disabled(self):
        for wire in (None, {}, {'enabled': True}):
            q = request()
            if wire is None: del q['params']['wire']
            else: q['params']['wire'] = wire
            with self.assertRaises(ContractError): o.validate_request(q)

    def test_only_known_presets(self):
        for preset in ('mirror', 'reflection_strips_v2', '', '../neutral', False, None, {}, ['neutral']):
            q = request(); q['params']['diagnostic_preset'] = preset
            with self.assertRaises(ContractError): o.validate_request(q)

    def test_unknown_diagnostic_knobs_rejected(self):
        for key in ('roughness', 'metallic', 'lights', 'texture', 'material', 'output_file', 'save_blend', 'score'):
            q = request(); q['params'][key] = .2
            with self.assertRaises(ContractError): o.validate_request(q)

    def test_view_level_preset_is_not_silently_ignored(self):
        q = request(); q['params']['views'][0]['diagnostic_preset'] = 'reflection_strips'
        with self.assertRaises(ContractError): o.validate_request(q)

    def test_fixed_diagnostic_values_are_bounded(self):
        s = o.diagnostic_settings('reflection_strips')
        self.assertGreater(s['material_override']['roughness'], 0)
        self.assertLess(s['material_override']['roughness'], .5)
        self.assertEqual(s['material_override']['metallic'], 1)
        self.assertEqual(s['samples'], 128)
        self.assertFalse(s['denoising'])
        self.assertFalse(s['textures_created'])
        self.assertEqual(s['light_shape'], 'RECTANGLE')
        self.assertEqual(s['light_size_extent'], .12)
        self.assertEqual(s['light_size_y_extent'], 4)
        self.assertEqual(len(s['lights_camera_basis']), 3)
        self.assertEqual(s['lights_camera_basis'], [[-.75, 0, 2.5, 60], [0, 0, 2.5, 60], [.75, 0, 2.5, 60]])
        self.assertEqual(s['surface_quality_inference'], 'none')

    def test_settings_return_independent_copies(self):
        s = o.diagnostic_settings('reflection_strips')
        s['material_override']['roughness'] = 1
        s['lights_camera_basis'][0][0] = 100
        self.assertEqual(o.diagnostic_settings('reflection_strips')['material_override']['roughness'], .08)
        self.assertEqual(o.diagnostic_settings('reflection_strips')['lights_camera_basis'][0][0], -.75)

    def test_schema_file_matches_authority(self):
        self.assertEqual(json.loads((ROOT / 'schemas/hardsurface-observe.schema.json').read_text()), o.schema())

    def test_exported_schema_expresses_cross_field_wire_requirement(self):
        rule=o.schema()['properties']['params']['allOf'][0]
        self.assertEqual(rule['if']['properties']['diagnostic_preset']['const'], 'reflection_strips')
        self.assertEqual(rule['then']['required'], ['wire'])
        self.assertEqual(rule['then']['properties']['wire']['properties']['enabled']['const'], False)

    def test_resource_bounds_still_apply(self):
        for key, value in [('cpu_threads', 5), ('wall_seconds', 601)]:
            q=request(); q['params'][key] = value
            with self.assertRaises(ContractError): o.validate_request(q)
        for key,value in [('width', 2049), ('height', 15)]:
            q=request(); q['params']['views'][0][key] = value
            with self.assertRaises(ContractError): o.validate_request(q)
        q=request(); q['params']['views'] *= 17
        with self.assertRaises(ContractError): o.validate_request(q)

    def test_rejects_bad_source_sha_before_blender_import(self):
        root=Path(tempfile.mkdtemp(prefix='hs-reflect-source-',dir='/tmp'))
        source=root/'fixture.blend'; source.write_bytes(b'Host-only identity fixture, not a Blender file')
        before=descriptor(source)
        q=request(); q['params']['source']['file']=str(source)
        with self.assertRaises(RuntimeFailure): o.execute(q,str(root))
        self.assertEqual(before,descriptor(source))
        self.assertEqual(list(root.iterdir()),[source])

    def test_execute_does_not_save_blend_or_mutate_source_materials(self):
        tree=ast.parse((ROOT/'hardsurface/observation.py').read_text())
        calls=[ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node,ast.Call)]
        self.assertFalse(any('save_as_mainfile' in name or 'save_mainfile' in name for name in calls))
        self.assertFalse(any('materials.append' in name or 'materials.clear' in name for name in calls))
        source=(ROOT/'hardsurface/observation.py').read_text()
        self.assertIn("assign(view_layer, 'material_override', material)",source)
        self.assertIn('finally:\n        restore_objects()',source)

    def test_report_does_not_assert_aesthetic_acceptance(self):
        source=(ROOT/'hardsurface/observation.py').read_text()
        self.assertIn("'visual': 'not_run'",source)
        self.assertIn("'visual_acceptance': 'not_run'",source)
        self.assertIn("'surface_quality_inference': 'none'",source)


class ReflectionRotationTests(unittest.TestCase):
    def test_default_zero_and_request_immutability(self):
        q=request(); before=copy.deepcopy(q)
        p=o.validate_request(q)['params']
        self.assertEqual(p['reflection_rotation_degrees'],0)
        self.assertEqual(q,before)
        self.assertEqual(o.diagnostic_settings('reflection_strips'),o.diagnostic_settings('reflection_strips',0))

    def test_angle_boundary_and_type_rejection(self):
        for angle in (-180, -90, -0.25, 0, 22.5, 90, 180):
            q=request();q['params']['reflection_rotation_degrees']=angle
            self.assertEqual(o.validate_request(q)['params']['reflection_rotation_degrees'],angle)
            self.assertEqual(o.diagnostic_settings('reflection_strips',angle)['reflection_rotation_degrees'],angle)
        for angle in (-180.0001, 180.0001, True, False, None, '90', [], {}, float('nan'), float('inf'), -float('inf'), 10**400):
            q=request();q['params']['reflection_rotation_degrees']=angle
            with self.subTest(angle=repr(angle)):
                with self.assertRaises(ContractError):o.validate_request(q)
                with self.assertRaises(ContractError):o.diagnostic_settings('reflection_strips',angle)

    def test_neutral_nonzero_rejected_including_omitted_preset(self):
        for explicit in (True,False):
            for angle in (-180,-90,0,90,180):
                q=request();q['params']['reflection_rotation_degrees']=angle
                if explicit:q['params']['diagnostic_preset']='neutral'
                else:del q['params']['diagnostic_preset']
                if angle:
                    with self.assertRaises(ContractError):o.validate_request(q)
                    with self.assertRaises(ContractError):o.diagnostic_settings('neutral',angle)
                else:self.assertEqual(o.validate_request(q)['params']['reflection_rotation_degrees'],0)

    def test_rotation_is_not_a_per_view_field(self):
        q=request();q['params']['views'][0]['reflection_rotation_degrees']=90
        with self.assertRaises(ContractError):o.validate_request(q)

    def test_zero_and_ninety_semantics(self):
        right=(1,0,0);up=(0,1,0)
        a,b=o._rotated_camera_plane_basis(right,up,0)
        self.assertIs(a,right);self.assertIs(b,up)
        a,b=o._rotated_camera_plane_basis(right,up,90)
        for got,want in zip(a,(0,1,0)):self.assertAlmostEqual(got,want)
        for got,want in zip(b,(-1,0,0)):self.assertAlmostEqual(got,want)

    def test_basis_remains_orthonormal_and_right_handed(self):
        import math
        def dot(a,b):return sum(x*y for x,y in zip(a,b))
        def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
        basis_pairs=[((1,0,0),(0,1,0)),((1,0,0),(0,0,1)),
                     ((2/math.sqrt(5),1/math.sqrt(5),0),(-1/math.sqrt(30),2/math.sqrt(30),5/math.sqrt(30)))]
        for right,up in basis_pairs:
            outward=cross(right,up)
            for angle in (-180,-90,-45,0,22.5,90,180):
                a,b=o._rotated_camera_plane_basis(right,up,angle)
                self.assertAlmostEqual(dot(a,a),1);self.assertAlmostEqual(dot(b,b),1)
                self.assertAlmostEqual(dot(a,b),0)
                for got,want in zip(cross(a,b),outward):self.assertAlmostEqual(got,want)
                self.assertAlmostEqual(dot(a,outward),0);self.assertAlmostEqual(dot(b,outward),0)

    def test_exported_schema_runtime_parity_for_json_values(self):
        try:from jsonschema import Draft202012Validator
        except ImportError:self.skipTest('Optional external jsonschema validator is unavailable')
        validator=Draft202012Validator(o.schema())
        cases=[]
        for preset in ('reflection_strips','neutral',None):
            for angle in (-181,-180,-90,-0.25,0,0.1,90,180,181,True,False,None,'90',[],{}):
                for wire in (None,{}, {'enabled':False},{'enabled':True}):
                    q=request()
                    if preset is None:del q['params']['diagnostic_preset']
                    else:q['params']['diagnostic_preset']=preset
                    if wire is None:del q['params']['wire']
                    else:q['params']['wire']=wire
                    q['params']['reflection_rotation_degrees']=angle
                    cases.append(q)
        for q in cases:
            # JSON Schema applies to JSON data. Nonfinite pseudo-JSON is tested
            # separately with the strict parser, before either validator.
            q=json.loads(json.dumps(q,allow_nan=False))
            schema_ok=validator.is_valid(q)
            try:o.validate_request(q);runtime_ok=True
            except ContractError:runtime_ok=False
            with self.subTest(params=q['params']):self.assertEqual(schema_ok,runtime_ok)
        self.assertEqual(len(cases),180)

    def test_nonfinite_json_tokens_are_strictly_rejected(self):
        q=request();q['params']['reflection_rotation_degrees']=0
        raw=json.dumps(q)
        for token in ('NaN','Infinity','-Infinity','1e9999'):
            with self.assertRaises(ContractError):
                o.validate_request(raw.replace('"reflection_rotation_degrees": 0','"reflection_rotation_degrees": '+token))


if __name__ == '__main__': unittest.main()
