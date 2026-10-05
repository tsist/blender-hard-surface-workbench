import copy
import unittest
from hardsurface import contract
from hardsurface.planner import plan_request,required_checks
from hardsurface.io import RuntimeFailure
from tests.test_contract import minimal_request


def request():
    r=minimal_request()
    r['params']['design']['state']['features'][0]['program']['steps']=[{
      'id':'body','op':'quad.fastener','center':[-3,7],'shaft_radius':.9,
      'head_radius':2.4,'head_height':1.1,'shaft_length':11.3,'shoulder_z':6.4,'direction':1}]
    return r


class StructuredContractTests(unittest.TestCase):
    def test_public_schema_exposes_bounded_domain_operations(self):
        for op in ('quad.panel','quad.shell','quad.fastener'):
            self.assertEqual(contract.schema(op)['properties']['op']['const'],op)
            self.assertFalse(contract.schema(op)['additionalProperties'])
    def test_quad_gate_mandatory_even_wire_off_and_unrequested(self):
        p=plan_request(request())
        self.assertFalse(p['request']['params']['wire']['enabled'])
        self.assertIn('quad_topology',[x['id'] for x in p['required_checks']])
        e=p['steps'][0]['geometry_estimate']
        self.assertEqual(e['loops'],e['faces']*4)
        self.assertEqual(len(e['construction_sha256']),64)
    def test_production_gate_mandatory_for_legacy_programs(self):
        r=minimal_request();p=r['params'];p['purpose']='production'
        checks=required_checks(p,p['design']['state'],[{'op':'primitive.box','step_key':'block/body'}])
        self.assertIn('quad_topology',[x['id'] for x in checks])
    def test_dimension_reference_reconstructs_deterministically(self):
        r=request();s=r['params']['design']['state']
        s['dimensions']=[{'id':'shaft_length','role':'driving','quantity':'length','unit':'mm','value':11.3}]
        s['features'][0]['program']['steps'][0]['shaft_length']={'kind':'dimension_ref','id':'shaft_length'}
        a=plan_request(r);s['dimensions'][0]['value']=14.7;b=plan_request(r)
        self.assertNotEqual(a['steps'][0]['geometry_estimate']['construction_sha256'],b['steps'][0]['geometry_estimate']['construction_sha256'])
        self.assertEqual(b['steps'][0]['shaft_length'],14.7)
    def test_unknown_fields_and_non_mm_are_rejected(self):
        r=request();r['params']['design']['state']['features'][0]['program']['steps'][0]['python']='bad'
        with self.assertRaises(contract.ContractError):contract.validate_request(r)
        r=request();r['params']['source']['length_unit']='m'
        with self.assertRaisesRegex(contract.ContractError,'QUAD_UNITS_UNSUPPORTED'):plan_request(r)
    def test_extreme_chord_fails_before_mesh_allocation(self):
        r=request();r['params']['design']['state']['features'][0]['program']['steps'][0]['chord_tolerance']=1e-15
        with self.assertRaises(ValueError):plan_request(r)
    def test_declared_geometry_budget_is_enforced(self):
        r=request();r['params']['budgets']['max_geometry_vertices']=8
        with self.assertRaisesRegex(contract.ContractError,'BUDGET_EXCEEDED'):plan_request(r)
    def test_nominal_dimension_and_chord_limits_remain_separate(self):
        a=request();a['params']['quality']['length_tolerance']=.05
        b=copy.deepcopy(a);b['params']['quality']['length_tolerance']=.0001
        pa,pb=plan_request(a),plan_request(b)
        self.assertEqual(pa['steps'][0]['geometry_estimate']['construction_sha256'],pb['steps'][0]['geometry_estimate']['construction_sha256'])
        self.assertEqual(pb['steps'][0]['chord_tolerance'],.025)
        self.assertEqual(pb['request']['params']['quality']['length_tolerance'],.0001)
        self.assertNotEqual(pa['plan_sha256'],pb['plan_sha256'])


if __name__=='__main__':unittest.main()
