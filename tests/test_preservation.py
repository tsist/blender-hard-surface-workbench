import copy
import unittest
from hardsurface.preservation import merge_projections,require_merge,verify_overlay_reopen
from hardsurface.contract import ContractError

class PreservationTests(unittest.TestCase):
    def baseline(self): return {'object':{'name':'Generated','geometry_sha256':'old','topology_sha256':'old','data_id':'data','material_slots':['material']}}
    def test_name_preserved_baseline_is_pure_N(self):
        b=self.baseline(); c=copy.deepcopy(b); n=copy.deepcopy(b); c['object']['name']='Hand Name'; n['object']['geometry_sha256']='new'; result=merge_projections(b,c,n)
        self.assertEqual(result['status'],'pass'); self.assertEqual(result['merged']['object']['name'],'Hand Name'); self.assertEqual(result['baseline_new']['object']['name'],'Generated'); self.assertIn('name',result['manual_overlay_new']['object']['fields'])
    def test_mesh_edits_stop(self):
        b=self.baseline(); c=copy.deepcopy(b); n=copy.deepcopy(b); c['object']['geometry_sha256']='hand'; n['object']['geometry_sha256']='new'
        r=merge_projections(b,c,n); self.assertEqual(r['status'],'pause'); self.assertIsNone(r['merged'])
    def test_unknown_field_stop(self):
        b=self.baseline(); c=copy.deepcopy(b); c['object']['driver']='unknown'; self.assertEqual(merge_projections(b,c,b)['status'],'pause')
    def test_material_mapping_must_be_verified(self):
        b=self.baseline(); c=copy.deepcopy(b); c['object']['material_slots']=['new']; self.assertEqual(merge_projections(b,c,b)['status'],'pause'); self.assertEqual(merge_projections(b,c,b,context={'material_face_assignments_stable':['object']})['status'],'pass')
    def test_manual_object_provenance(self):
        b=self.baseline(); c=copy.deepcopy(b); c['manual']={'name':'manual','datum_dependencies':['axis']}
        self.assertEqual(merge_projections(b,c,b)['status'],'pause'); r=merge_projections(b,c,b,context={'verified_manual_objects':['manual'],'stable_datum_ids':['axis']}); self.assertEqual(r['status'],'pass'); self.assertNotIn('manual',r['baseline_new']); self.assertIn('manual',r['merged'])
    def test_cross_partition_shared_mesh(self):
        b=self.baseline(); r=merge_projections(b,b,b,context={'shared_data_users':{'data':['object','manual']}}); self.assertEqual(r['status'],'pause')
    def test_deleted_generated_object_stops(self): self.assertEqual(merge_projections(self.baseline(),{},self.baseline())['status'],'pause')
    def test_reopen(self):
        self.assertEqual(verify_overlay_reopen({'a':1},{'a':1})['status'],'pass')
        with self.assertRaises(ContractError): verify_overlay_reopen({'a':1},{'a':2})
    def test_require_merge(self):
        with self.assertRaises(ContractError): require_merge(self.baseline(),{},self.baseline())
    def test_outside_write_set_preserved(self):
        b=self.baseline(); c=copy.deepcopy(b); c['object']['geometry_sha256']='hand'; r=merge_projections(b,c,b,write_set=[]); self.assertEqual(r['merged'],c)
