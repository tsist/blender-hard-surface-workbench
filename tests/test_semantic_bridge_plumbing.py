"""Host-only call-path assertions; actual Blender transport remains unqualified."""
import ast
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class SemanticBridgePlumbing(unittest.TestCase):
 def test_evaluated_bridge_attests_source_independently(self):
  source=(ROOT/'hardsurface/ops/quad_bridge.py').read_text();ast.parse(source)
  self.assertIn('if has_native_identity(obj):',source)
  self.assertNotIn("if mesh_state == 'control' and has_native_identity(obj):",source)
  self.assertIn("capture_semantic_source(obj, unit_scale=units['scale_length'])",source)
  self.assertLess(source.index('capture_semantic_source(obj'),source.index('eo = obj.evaluated_get'))
 def test_bridge_uses_actual_evaluated_face_domain(self):
  source=(ROOT/'hardsurface/ops/quad_bridge.py').read_text()
  self.assertIn("evaluated_bore_domain(mesh, semantic_source, level=int(obj.modifiers[0].levels))",source)
  self.assertIn("bore_face_indices=transported['bore_face_indices'], measurement_profile='semantic_bore_v1'",source)
  self.assertIn("sampled['semantic_transport'] = transported['evidence']",source)
  self.assertIn("semantic_source['evidence']['actual_source_modifier']",source)
 def test_missing_semantic_source_cannot_promote_production_gate(self):
  source=(ROOT/'hardsurface/ops/quad_bridge.py').read_text()
  self.assertIn("if semantic_source is None:",source)
  self.assertIn("report['finding_counts']['SUBD_SEMANTIC_SOURCE_REQUIRED']=1",source)
  self.assertIn("report['passed']=False",source)
 def test_legacy_harness_cannot_claim_source_bound_qualification(self):
  source=(ROOT/'tests/blender_subd_panel_qualification.py').read_text()
  self.assertIn('legacy_numeric_diagnostic_only_not_dev6_source_bound',source)
if __name__=='__main__':unittest.main()
