"""Host-side guards for the bounded real mesh-edge observation overlay."""
import copy
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import unittest

from hardsurface import wire_overlay as wire
from hardsurface.io import RuntimeFailure


class Proxy(dict):
    def __init__(self, count=4, **overrides):
        super().__init__({wire.PROXY_MARKER: True, 'hs_feature_id': 'sample_plate'})
        self.name = 'temporary proxy'
        self.type = 'MESH'
        self.library = self.override_library = None
        self.data = SimpleNamespace(library=None, users=1, edges=range(count))
        self.modifiers = []
        self.hide_render = False
        for name, value in overrides.items():
            setattr(self, name, value)


class WireOverlayTests(unittest.TestCase):
    def test_import_does_not_load_blender(self):
        result = subprocess.run([sys.executable, '-B', '-c',
                                 'import sys; from hardsurface import wire_overlay; '
                                 'assert "bpy" not in sys.modules; assert "mathutils" not in sys.modules'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_strict_bounds_and_original_config_preservation(self):
        original = {'mesh_state': 'control', 'line_width_px': 2.0, 'max_edges': 100000}
        before = copy.deepcopy(original)
        config = wire.validate_config(original)
        self.assertTrue(config['enabled'])
        self.assertEqual(original, before)
        for bad in (None, 'false', 0, 1):
            with self.assertRaises(RuntimeFailure):
                wire.validate_config({'enabled': bad})
        for bad in (True, 0, .99, 2.01, float('nan'), float('inf'), '1.25'):
            with self.assertRaises(RuntimeFailure):
                wire.validate_config({'line_width_px': bad})
        for bad in (True, 0, 100001, 2.1, '1000'):
            with self.assertRaises(RuntimeFailure):
                wire.validate_config({'max_edges': bad})
        for bad in ({'mesh_state': 'triangles'}, {'shader': 'wireframe'}, []):
            with self.assertRaises(RuntimeFailure):
                wire.validate_config(bad)

    def test_explicit_off_suppresses_existing_style_and_restores(self):
        scene = SimpleNamespace(render=SimpleNamespace(use_freestyle=True))
        handle = wire.prepare(scene, None, None, None, {'enabled': False})
        self.assertFalse(scene.render.use_freestyle)
        self.assertEqual(handle.evidence['method'], 'OFF')
        self.assertEqual(handle.evidence['actual_edges'], 0)
        handle.cleanup()
        self.assertTrue(scene.render.use_freestyle)
        self.assertTrue(handle.evidence['temporary_state_restored'])
        handle.cleanup()
        self.assertTrue(scene.render.use_freestyle)

    def test_budget_counts_actual_mesh_edges_and_fails_before_bpy_import(self):
        self.assertEqual(wire._check_proxies([('sample_plate', Proxy(4))], 4), (4, {'sample_plate': 4}))
        with self.assertRaises(RuntimeFailure) as raised:
            wire.prepare(None, None, {'sample_plate': Proxy(50001), 'sample_cap': Proxy(50000)}, None)
        self.assertEqual(raised.exception.code, 'OBSERVATION_WIRE_EDGE_LIMIT')
        self.assertNotIn('bpy', sys.modules)

    def test_source_mesh_and_unfrozen_proxy_are_refused(self):
        source = Proxy()
        del source[wire.PROXY_MARKER]
        candidates = [source, Proxy(type='CAMERA'), Proxy(hide_render=True),
                      Proxy(modifiers=[object()]), Proxy(library=object()),
                      Proxy(data=SimpleNamespace(library=None, users=2, edges=range(4)))]
        for candidate in candidates:
            with self.assertRaises(RuntimeFailure) as raised:
                wire._check_proxies([('sample_plate', candidate)], 100000)
            self.assertEqual(raised.exception.code, 'OBSERVATION_WIRE_PROXY')

    def test_duplicate_object_refused(self):
        obj = Proxy()
        with self.assertRaises(RuntimeFailure):
            wire._check_proxies([('sample_plate', obj), ('sample_cap', obj)], 100000)

    def test_loose_edges_refused_instead_of_silently_omitted(self):
        obj = Proxy(data=SimpleNamespace(library=None, users=1,
                                        edges=[SimpleNamespace(is_loose=True)]))
        with self.assertRaises(RuntimeFailure) as raised:
            wire.prepare(None, None, {'sample_plate': obj}, None)
        self.assertEqual(raised.exception.code, 'OBSERVATION_WIRE_LOOSE_EDGES_UNSUPPORTED')
        self.assertEqual(raised.exception.details['loose_edges'], 1)

    def test_cleanup_continues_after_failure_and_reports(self):
        handle = wire.OverlayHandle({})
        restored = []
        handle._undo.append(lambda: restored.append(True))
        handle._undo.append(lambda: (_ for _ in ()).throw(ValueError('test failure')))
        with self.assertRaises(RuntimeFailure) as raised:
            handle.cleanup()
        self.assertEqual(raised.exception.code, 'OBSERVATION_WIRE_RESTORE')
        self.assertEqual(restored, [True])
        self.assertFalse(handle.evidence['temporary_state_restored'])


if __name__ == '__main__':
    unittest.main()
