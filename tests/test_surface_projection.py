"""HOST mocks verify native intrinsic calls; these are not native qualifications."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface import surface_projection as p


def record(name='join_right_strips090'):
    return {
        'name': name, 'width': 1600, 'height': 1200,
        'report': {'file': '/tmp/pinned/report.json', 'sha256': 'a' * 64, 'bytes': 321},
        'producer_identity': {'source_sha256': 'b' * 64,
                              'blender': {'file': '/opt/blender', 'sha256': 'c' * 64, 'bytes': 100}},
        'camera': {'type': 'ORTHO', 'sensor_fit': 'VERTICAL',
                   'ortho_scale_mm': 24.000000208616257,
                   'clip_start_m': 9.999999974752427e-07, 'clip_end_m': 1000.0,
                   'matrix_world': [[-0.05143188685178757, -0.17193108797073364, 0.9837654232978821, 0.2800000011920929],
                                    [0.9986764788627625, -0.008854459039866924, 0.050663966685533524, 0.04729999974370003],
                                    [0.0, 0.9850691556930542, 0.17215894162654877, 0.03500000014901161],
                                    [0.0, 0.0, 0.0, 1.0]],
                   'position_mm': [280.0000011920929, 47.29999974370003, 35.00000014901161],
                   'target_mm': [80.00000566244125, 37.00000047683716, 0.0], 'up_axis': 'Z'},
        'projection_inputs': {'pixel_aspect_x': 1.0, 'pixel_aspect_y': 1.0,
                              'resolution_percentage': 100, 'shift_x': 0.0, 'shift_y': 0.0},
        'projection_inputs_origin': p.INPUT_ORIGIN,
    }


class NativeMock:
    """Only expose the native operations this helper is allowed to perform."""
    def __init__(self):
        self.events = []
        self.camera_data = []
        self.objects = []
        self.depsgraph = object()
        # Deliberately arbitrary native result; tests forbid a replacement formula.
        self.matrix = [[i * 4.0 + j + .125 for j in range(4)] for i in range(4)]
        self.scene = SimpleNamespace(
            camera=object(), render=object(),
            collection=SimpleNamespace(objects=SimpleNamespace(link=self.link)),
            view_layers=[SimpleNamespace(update=self.update)])
        self.bpy = SimpleNamespace(
            app=SimpleNamespace(version_string='5.2.2 mock', build_hash=b'mock-build'),
            data=SimpleNamespace(cameras=SimpleNamespace(new=self.new_camera),
                                 objects=SimpleNamespace(new=self.new_object)),
            context=SimpleNamespace(temp_override=self.override,
                                    evaluated_depsgraph_get=lambda: self.depsgraph))

    def new_camera(self, name):
        data = SimpleNamespace(name=name, type='PERSP', sensor_fit='AUTO',
                               ortho_scale=6.0, clip_start=.1, clip_end=1000.0,
                               shift_x=0.0, shift_y=0.0)
        self.camera_data.append(data)
        self.events.append(('new_camera', data))
        return data

    def new_object(self, name, data):
        outer = self

        class Camera:
            __slots__ = ('name', 'data')

            def __init__(self):
                self.name, self.data = name, data

            def calc_matrix_camera(self, depsgraph, **kwargs):
                outer.events.append(('calc', depsgraph, kwargs, self.data))
                return copy.deepcopy(outer.matrix)

        obj = Camera()
        self.objects.append(obj)
        self.events.append(('new_object', obj))
        return obj

    def link(self, camera):
        self.events.append(('link', camera))

    def update(self):
        self.events.append(('update',))

    @contextmanager
    def override(self, **kwargs):
        self.events.append(('override_enter', kwargs))
        try:
            yield
        finally:
            self.events.append(('override_exit',))


class CameraValidationTests(unittest.TestCase):
    def test_import_is_host_safe(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; from hardsurface import surface_projection; '
            'assert "bpy" not in sys.modules; assert "mathutils" not in sys.modules; '
            'assert "hardsurface.core" not in sys.modules'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_copy_preserves_exact_recorded_camera_json(self):
        original = record()
        normalized = p.validate_camera_record(original)
        self.assertEqual(json.dumps(normalized), json.dumps(original))
        normalized['camera']['matrix_world'][0][0] = 0
        self.assertNotEqual(normalized, original)

    def test_missing_unknown_and_false_provenance_are_rejected(self):
        variations = []
        for key in record():
            value = record(); del value[key]; variations.append(value)
        value = record(); value['extra'] = 1; variations.append(value)
        value = record(); value['projection_inputs_origin'] = 'old_native_attested'; variations.append(value)
        value = record(); del value['projection_inputs']['shift_x']; variations.append(value)
        value = record(); value['camera']['lens'] = 50; variations.append(value)
        value = record(); value['producer_identity']['source_sha256'] = 'x' * 64; variations.append(value)
        value = record(); value['report']['file'] = '/tmp/../unbound'; variations.append(value)
        for value in variations:
            with self.subTest(value=value), self.assertRaises(RuntimeFailure):
                p.validate_camera_record(value)

    def test_scalar_and_matrix_nonfinite_bools_and_big_integers_are_rejected(self):
        for bad in (True, False, float('nan'), float('inf'), -float('inf'), 10**400, '1', None):
            for location in ('width', 'height', 'aspect', 'shift', 'percentage', 'scale', 'clip', 'matrix', 'position', 'bytes'):
                value = record()
                if location in ('width', 'height'):
                    value[location] = bad
                elif location in ('aspect', 'shift', 'percentage'):
                    value['projection_inputs'][{'aspect': 'pixel_aspect_x', 'shift': 'shift_y', 'percentage': 'resolution_percentage'}[location]] = bad
                elif location in ('scale', 'clip'):
                    value['camera'][{'scale': 'ortho_scale_mm', 'clip': 'clip_end_m'}[location]] = bad
                elif location == 'matrix':
                    value['camera']['matrix_world'][0][0] = bad
                elif location == 'position':
                    value['camera']['position_mm'][0] = bad
                else:
                    value['report']['bytes'] = bad
                with self.subTest(bad=bad, location=location), self.assertRaises(RuntimeFailure):
                    p.validate_camera_record(value)

    def test_unsupported_and_inconsistent_camera_data_rejected(self):
        mutations = [lambda v: v['camera'].update(type='PERSP'),
                     lambda v: v['camera'].update(sensor_fit='AUTO'),
                     lambda v: v['camera'].update(clip_start_m=1000),
                     lambda v: v['camera'].update(clip_start_m=0),
                     lambda v: v['camera'].update(ortho_scale_mm=0),
                     lambda v: v['projection_inputs'].update(resolution_percentage=50),
                     lambda v: v['projection_inputs'].update(pixel_aspect_y=0),
                     lambda v: v['projection_inputs'].update(shift_x=11),
                     lambda v: v['camera']['matrix_world'][3].__setitem__(0, 1),
                     lambda v: v['camera']['matrix_world'].__setitem__(1, [0, 0, 0, 0]),
                     lambda v: v.update(width=2049)]
        for mutate in mutations:
            value = record(); mutate(value)
            with self.subTest(value=value), self.assertRaises(RuntimeFailure):
                p.validate_camera_record(value)


class NativeCalibrationTests(unittest.TestCase):
    def test_native_arguments_readback_provenance_and_pose_preservation(self):
        native, value, created = NativeMock(), record(), []
        value['projection_inputs'].update(pixel_aspect_x=1.2, pixel_aspect_y=1.6,
                                           shift_x=.25, shift_y=-.375)
        before = copy.deepcopy(value)
        active, render = native.scene.camera, native.scene.render
        result = p.calibrate_views([value], native.bpy, native.scene, created)[0]
        self.assertEqual(value, before)
        self.assertEqual(result['projection_matrix'], native.matrix)
        self.assertEqual(json.dumps(result['camera_to_world_matrix']), json.dumps(value['camera']['matrix_world']))
        self.assertEqual(result['recorded_camera'], value['camera'])
        self.assertEqual(result['producer_identity'], value['producer_identity'])
        self.assertFalse(result['provenance']['old_unrecorded_intrinsics_attested'])
        self.assertFalse(result['provenance']['original_pose_recreated'])
        self.assertIs(native.scene.camera, active)
        self.assertIs(native.scene.render, render)
        calls = [event for event in native.events if event[0] == 'calc']
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1], native.depsgraph)
        self.assertEqual(calls[0][2], {'x': 1600, 'y': 1200, 'scale_x': 1.2, 'scale_y': 1.6})
        self.assertEqual(calls[0][3].ortho_scale, value['camera']['ortho_scale_mm'] * .001)
        self.assertEqual(calls[0][3].clip_start, value['camera']['clip_start_m'])
        self.assertEqual(calls[0][3].shift_x, .25)
        self.assertEqual(calls[0][3].shift_y, -.375)
        self.assertEqual(created, [(native.bpy.data.cameras, native.camera_data[0]),
                                   (native.bpy.data.objects, native.objects[0])])
        self.assertEqual([e[0] for e in native.events][-4:], ['override_enter', 'update', 'calc', 'override_exit'])
        self.assertEqual(result['matrix_convention']['vectors'], 'column_vectors')
        json.dumps(result, allow_nan=False)

    def test_two_views_remain_independent(self):
        native, created = NativeMock(), []
        values = [record(), record('join_left_strips090')]
        values[1]['width'] = 800
        values[1]['camera']['matrix_world'][0][3] = -.28
        result = p.calibrate_views(values, native.bpy, native.scene, created)
        self.assertEqual(len(created), 4)
        self.assertEqual(result[1]['native_call']['x'], 800)
        self.assertEqual(result[1]['camera_to_world_matrix'][0][3], -.28)
        result[0]['recorded_camera']['matrix_world'][0][3] = 999
        self.assertNotEqual(result[0]['recorded_camera'], values[0]['camera'])
        self.assertEqual(result[1]['recorded_camera'], values[1]['camera'])

    def test_whole_batch_validation_precedes_native_allocation(self):
        bad = record('bad'); bad['camera']['clip_end_m'] = float('inf')
        for values in ([], [record(), record()], [record(), record('two'), record('three')], [record(), bad], None):
            native = NativeMock()
            with self.subTest(values=values), self.assertRaises(RuntimeFailure):
                p.calibrate_views(values, native.bpy, native.scene, [])
            self.assertEqual(native.events, [])

    def test_missing_native_api_fails_without_formula_fallback(self):
        native = NativeMock()
        native.bpy.data.objects.new = lambda name, data: SimpleNamespace(name=name)
        created = []
        with self.assertRaises(RuntimeFailure) as error:
            p.calibrate_views([record()], native.bpy, native.scene, created)
        self.assertEqual(error.exception.code, 'SURFACE_PROJECTION_API')
        self.assertEqual(len(created), 2)
        self.assertFalse(any(e[0] == 'calc' for e in native.events))

    def test_invalid_native_projection_fails_and_retains_owned_cleanup(self):
        for matrix in ([[1, 0], [0, 1]], [[False] * 4 for _ in range(4)],
                       [[float('nan')] * 4 for _ in range(4)], [[float('inf')] * 4 for _ in range(4)]):
            native, created = NativeMock(), []
            native.matrix = matrix
            with self.subTest(matrix=matrix), self.assertRaises(RuntimeFailure):
                p.calibrate_views([record()], native.bpy, native.scene, created)
            self.assertEqual(len(created), 2)
            self.assertEqual(native.events[-1][0], 'override_exit')

    def test_creation_and_native_call_failures_are_runtime_failures(self):
        for failing_stage in ('new_object', 'link', 'update'):
            native, created = NativeMock(), []

            def fail(*args, **kwargs):
                raise ValueError('native mock failure')

            if failing_stage == 'new_object':
                native.bpy.data.objects.new = fail
            elif failing_stage == 'link':
                native.scene.collection.objects.link = fail
            else:
                native.scene.view_layers[0].update = fail
            with self.subTest(stage=failing_stage), self.assertRaises(RuntimeFailure):
                p.calibrate_views([record()], native.bpy, native.scene, created)
            self.assertEqual(len(created), 1 if failing_stage == 'new_object' else 2)

    def test_native_clamping_is_rejected(self):
        native = NativeMock()

        class Clamped(SimpleNamespace):
            def __setattr__(self, key, value):
                if key == 'shift_x':
                    value = 0
                super().__setattr__(key, value)

        native.bpy.data.cameras.new = lambda name: Clamped(name=name, type='PERSP', sensor_fit='AUTO',
            ortho_scale=6.0, clip_start=.1, clip_end=1000.0, shift_x=0.0, shift_y=0.0)
        value = record(); value['projection_inputs']['shift_x'] = .25
        with self.assertRaises(RuntimeFailure) as error:
            p.calibrate_views([value], native.bpy, native.scene, [])
        self.assertEqual(error.exception.code, 'SURFACE_PROJECTION_NATIVE_MISMATCH')


if __name__ == '__main__':
    unittest.main()
