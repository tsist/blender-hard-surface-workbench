"""Bounds and schema parity for the explicit fixed-support SubD-only branch."""
import copy
import json
from pathlib import Path
import unittest
from hardsurface import contract as c
from hardsurface import structure_contract as sc
from hardsurface.planner import plan_request
from test_contract import minimal_request
from test_subd_panel import BASE


class RoundoverSchemaTests(unittest.TestCase):
    def step(self, strategy='subd_control_cage', edge=2):
        p=copy.deepcopy(BASE);p['topology_strategy']=strategy;p['edge_bevel']=edge
        return p
    def test_subd_accepts_explicit_two_mm_schema_only(self):
        p=self.step();self.assertEqual(c._validate(p,c.STEP)['edge_bevel'],2)
    def test_subd_above_two_refused(self):
        with self.assertRaises(c.ContractError):c._validate(self.step(edge=2.00001),c.STEP)
    def test_other_routes_keep_one_mm_bound(self):
        for strategy in ('tiled','sparse_annulus','local_patch_blocks'):
            with self.subTest(strategy=strategy),self.assertRaises(c.ContractError):c._validate(self.step(strategy),c.STEP)
    def test_missing_strategy_keeps_legacy_bound(self):
        p=self.step();p.pop('topology_strategy')
        with self.assertRaises(c.ContractError):c._validate(p,c.STEP)
    def test_explicit_bore_width_positive_and_no_default(self):
        p=self.step(edge=.7);self.assertNotIn('bore_support_width',c._validate(p,c.STEP)['subdivision_cage'])
        p['subdivision_cage']['bore_support_width']=1
        self.assertEqual(c._validate(p,c.STEP)['subdivision_cage']['bore_support_width'],1)
        for value in (0,-1,True,float('nan'),float('inf')):
            q=copy.deepcopy(p);q['subdivision_cage']['bore_support_width']=value
            with self.subTest(value=value),self.assertRaises(c.ContractError):c._validate(q,c.STEP)
    def test_other_routes_refuse_new_bore_width(self):
        for strategy in ('tiled','sparse_annulus','local_patch_blocks'):
            p=self.step(strategy,edge=.7);p['subdivision_cage']['bore_support_width']=1
            with self.subTest(strategy=strategy),self.assertRaises(c.ContractError):c._validate(p,c.STEP)
    def test_edge_radius_can_be_driven_but_resolved_bound_still_applies(self):
        p=self.step(edge={'kind':'dimension_ref','id':'edge'});c._validate(p,c.STEP)
        request=minimal_request();state=request['params']['design']['state'];state['dimensions']=[{'id':'edge','role':'driving','quantity':'length','value':2.01,'unit':'mm'}];state['features'][0]['program']['steps']=[p]
        with self.assertRaises(c.ContractError):plan_request(request)
    def test_exported_jsonschema_matches_conditionals(self):
        try:from jsonschema import Draft202012Validator
        except ImportError:self.skipTest('optional jsonschema unavailable')
        validator=Draft202012Validator(c.schema('quad.panel'))
        for strategy in ('subd_control_cage','tiled','sparse_annulus','local_patch_blocks'):
            for edge in (.7,1,1.5,2,2.01):
                for support in (None,1,-1):
                    p=self.step(strategy,edge=edge)
                    if support is not None:p['subdivision_cage']['bore_support_width']=support
                    try:c._validate(p,c.STEP);accepted=True
                    except c.ContractError:accepted=False
                    with self.subTest(strategy=strategy,edge=edge,support=support):self.assertEqual(validator.is_valid(p),accepted)
    def test_readonly_plan_outer_edit_needs_independent_support(self):
        path=Path(__file__).parents[1]/'fixtures/structure-plan.valid.json';p=json.loads(path.read_text())
        p['edit_contract']['allowed_parameters'].append('panel.edge_roundover_mm');p['edit_contract']['parameter_ranges'].append({'parameter':'panel.edge_roundover_mm','minimum':.5,'maximum':1.})
        with self.assertRaises(sc.ContractError):sc.validate_request(p)
        p['topology']['bore_support_width_mm']=1.;self.assertEqual(sc.validate_request(p)['topology']['bore_support_width_mm'],1.)
    def test_readonly_plan_width_guard_cannot_hide_frame_overlap(self):
        p=json.loads((Path(__file__).parents[1]/'fixtures/structure-plan.valid.json').read_text())
        p['topology']['bore_support_width_mm']=8.5
        with self.assertRaises(sc.ContractError):sc.validate_request(p)

if __name__=='__main__':unittest.main()
