"""Pure host checks for separately held actual native registry bindings."""
import copy
import unittest
from hardsurface.identity import STRUCTURE_BINDING_FIELDS,validate_structure_registry,register_structure_binding
from hardsurface.contract import ContractError


def binding():
    b={k:'a'*64 for k in STRUCTURE_BINDING_FIELDS}
    b.update(schema_version='native-control-structure/1.0',object_id='00000000-0000-4000-8000-000000000001',data_id='00000000-0000-4000-8000-000000000002',mesh_state='control',schedule_revision='single_sharp_panel_ids_v1',topology_epoch=0)
    return b


class RegistryTests(unittest.TestCase):
    def test_initial_register_is_copy(self):
        b=binding();r=register_structure_binding({},b);self.assertEqual(r,{b['object_id']:b});r[b['object_id']]['geometry_signature']='b'*64;self.assertEqual(b['geometry_signature'],'a'*64)
    def test_replace_requires_exact_old(self):
        b=binding();r={b['object_id']:b};c=copy.deepcopy(b);c['geometry_signature']='b'*64
        with self.assertRaises(ContractError):register_structure_binding(r,c)
        self.assertEqual(register_structure_binding(r,c,previous_binding=b)[b['object_id']],c)
    def test_stale_old_refused(self):
        b=binding();c=copy.deepcopy(b);c['geometry_signature']='b'*64
        with self.assertRaises(ContractError):register_structure_binding({b['object_id']:b},c,previous_binding=c)
    def test_new_object_cannot_claim_old(self):
        b=binding()
        with self.assertRaises(ContractError):register_structure_binding({},b,previous_binding=b)
    def test_missing_or_extra_binding_field(self):
        b=binding()
        for mode in ('missing','extra'):
            c=copy.deepcopy(b)
            if mode=='missing':c.pop('attribute_signature')
            else:c['claim']='approved'
            with self.assertRaises(ContractError):validate_structure_registry({b['object_id']:c})
    def test_missing_source_sha(self):
        b=binding();b['source_binding_sha256']=None
        with self.assertRaises(ContractError):validate_structure_registry({b['object_id']:b})
    def test_bad_epoch(self):
        for value in (True,-1,1.5):
            b=binding();b['topology_epoch']=value
            with self.assertRaises(ContractError):validate_structure_registry({b['object_id']:b})
    def test_key_is_exact_object_identity(self):
        b=binding()
        with self.assertRaises(ContractError):validate_structure_registry({b['data_id']:b})
    def test_semantic_scope_change_rejected(self):
        b=binding()
        for key,value in [('data_id','00000000-0000-4000-8000-000000000003'),('schedule_revision','other_schedule'),('topology_epoch',1)]:
            c=copy.deepcopy(b);c[key]=value
            with self.assertRaises(ContractError):register_structure_binding({b['object_id']:b},c,previous_binding=b)
    def test_unsupported_state_and_schema(self):
        for key,value in [('mesh_state','evaluated'),('schema_version','native-control-structure/2.0')]:
            b=binding();b[key]=value
            with self.assertRaises(ContractError):validate_structure_registry({b['object_id']:b})

if __name__=='__main__':unittest.main()
