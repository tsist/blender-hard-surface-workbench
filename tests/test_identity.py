import unittest
from hardsurface import identity as i
from hardsurface.contract import ContractError

class IdentityTests(unittest.TestCase):
    def test_stable_ids(self):
        a=i.stable_id('project','feature','step'); self.assertEqual(a,i.stable_id('project','feature','step')); self.assertNotEqual(a,i.stable_id('project','feature','step',kind='data')); i.validate_id(a)
    def test_duplicate_registry(self):
        a={'object_id':i.new_id(),'data_id':i.new_id()}
        with self.assertRaises(ContractError): i.validate_registry([a,a])
    def test_shared_data_explicit(self):
        data=i.new_id(); result=i.validate_registry([{'object_id':i.new_id(),'data_id':data},{'object_id':i.new_id(),'data_id':data}]); self.assertEqual(len(result['data_users'][data]),2)
    def test_topology_ignores_positions(self):
        self.assertEqual(i.topology_hash([[0,0,0]]*3,[(0,1)],[[0,1,2]]),i.topology_hash([[1,2,3]]*3,[(0,1)],[[0,1,2]]))
    def test_topology_same_count_different_connections(self):
        self.assertNotEqual(i.topology_hash(4,[(0,1)],[[0,1,2]]),i.topology_hash(4,[(0,2)],[[0,1,2]]))
    def test_winding_invalidates(self):
        self.assertNotEqual(i.topology_hash(3,[],[[0,1,2]]),i.topology_hash(3,[],[[0,2,1]]))
    def test_geometry_changes(self): self.assertNotEqual(i.geometry_hash([[0,0,0]]),i.geometry_hash([[1,0,0]]))
    def test_stale_selector(self):
        selector={'kind':'index_selection','object_id':i.new_id(),'data_id':i.new_id(),'topology_sha256':'a','domain':'EDGE','indices':[1]}; snap={**selector,'counts':{'EDGE':2}}
        self.assertEqual(i.validate_selection(selector,snap),[1]); snap['topology_sha256']='b'
        with self.assertRaises(ContractError): i.validate_selection(selector,snap)
    def test_position_dependent_requires_geometry(self):
        s={'kind':'index_selection','object_id':i.new_id(),'data_id':i.new_id(),'topology_sha256':'a','domain':'EDGE','indices':[1],'position_dependent':True}
        with self.assertRaises(ContractError): i.validate_selection(s,{**s,'counts':{'EDGE':2}})
    def test_port_uniqueness(self):
        p={'feature_id':'f','port':'body_object'}; self.assertEqual(i.resolve_feature_port([p],'f','body_object'),p)
        with self.assertRaises(ContractError): i.resolve_feature_port([p,p],'f','body_object')
    def test_uppercase_uuid_rejected(self):
        with self.assertRaises(ContractError): i.validate_id(i.new_id().upper())
