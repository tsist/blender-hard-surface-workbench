import hashlib,json,os,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from hardsurface.io import RuntimeFailure,copy_verified,descriptor,verify_descriptor,checked_path,atomic_json
from hardsurface.protection import ProtectedInputs,stage_inputs,recheck_originals
from hardsurface.host import technical_plan,derived_request_id
class IOTests(unittest.TestCase):
 def setUp(self):self.root=Path(tempfile.mkdtemp(prefix='hs-io-test-',dir='/tmp'));self.source=self.root/'source.bin';self.source.write_bytes(b'owned synthetic bytes')
 def desc(self):return {'file':str(self.source),'expected_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),'bytes':self.source.stat().st_size}
 def test_derived_ids_remain_bounded_deterministic(self):
  a=derived_request_id('x'*128,'resume:'+('y'*96));self.assertLessEqual(len(a),128);self.assertEqual(a,derived_request_id('x'*128,'resume:'+('y'*96)));self.assertNotEqual(a,derived_request_id('x'*128,'resume:'+('z'*96)))
 def test_copy_and_observations(self):
  copied,obs=copy_verified(self.desc(),self.root/'copy.bin',limit_bytes=1024);self.assertEqual(copied['sha256'],self.desc()['expected_sha256']);self.assertFalse(obs['continuous_immutability_proven']);self.assertFalse(obs['written_by_this_tool'])
 def test_no_overwrite(self):
  with self.assertRaises(RuntimeFailure):copy_verified(self.desc(),self.source,limit_bytes=1024)
 def test_wrong_hash(self):
  value=self.desc();value['expected_sha256']='0'*64
  with self.assertRaises(RuntimeFailure):verify_descriptor(value)
 def test_copy_budget(self):
  with self.assertRaises(RuntimeFailure):copy_verified(self.desc(),self.root/'oversized.bin',limit_bytes=1)
 def test_path_traversal(self):
  with self.assertRaises(RuntimeFailure):checked_path(str(self.root/'a'/'..'/'source.bin'))
 def test_symlink_refused(self):
  p=self.root/'link';p.symlink_to(self.source)
  with self.assertRaises(RuntimeFailure):checked_path(p)
 def test_guard_actual_tmpfs(self):
  with ProtectedInputs([descriptor(self.source)]) as guard:guard.check();self.assertFalse(guard.report()['accepted'])
  self.assertTrue(guard.report()['accepted'])
 def test_unsupported_filesystem_guard_refusal(self):
  class RefusedGuard:
   def __init__(self,path):raise RuntimeFailure('GUARD_UNSUPPORTED','Synthetic filesystem policy refusal')
  with patch('hardsurface.protection.guard_class',return_value=RefusedGuard):
   with self.assertRaises(RuntimeFailure):
    with ProtectedInputs([descriptor(self.source)]):pass
 def test_original_change_observed(self):
  original=self.desc();self.source.write_bytes(b'changed bytes')
  with self.assertRaises(RuntimeFailure):recheck_originals({'source':{'kind':'new_scene'},'resources':[original]},[])
 def test_repair_preserves_design(self):
  plan={'resolved_design_state':{'dimensions':[{'id':'width','value':40}]},'steps':[{'op':'boolean.apply_or_stack','effective_params':{'solver':'FAST','diameter':12}}]}
  revised=technical_plan(plan,'boolean_exact');self.assertEqual(plan['steps'][0]['effective_params']['solver'],'FAST');self.assertEqual(revised['resolved_design_state'],plan['resolved_design_state']);self.assertEqual(revised['steps'][0]['effective_params']['diameter'],12)
 def test_repair_unknown_rejected(self):
  with self.assertRaises(RuntimeFailure):technical_plan({'steps':[]},'change_hole_diameter')
if __name__=='__main__':unittest.main()
