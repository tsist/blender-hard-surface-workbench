# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral host protocol tests, explicitly not native Blender evidence."""
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace as NS

from test_structure_native_contract import Mesh, Object
from hardsurface.sparse_panel_geometry import build_sparse_panel
from hardsurface.structure_native import write_authored_identity, bind_native_structure, validate_native_structure
from hardsurface.structure_edit import plan_authored_edit, verify_authored_edit
from hardsurface.io import RuntimeFailure


def parameters():
    return {'id':'panel','op':'quad.panel','topology_strategy':'sparse_control_cage',
            'size':[120.,84.],'center':[0.,0.],'corner_radius':11.,'edge_bevel':1.5,
            'z_min':-4.,'z_max':4.,'holes':[{'id':'bore','kind':'circle','center':[-10.,3.],'radius':12.}],
            'lips':[],'sparse_cage':{'preview_levels':0},'edit_datum':'fixed_midplane'}


def materialized(p,epoch=None):
    result=build_sparse_panel(p,feature_id='neutral')
    obj=Object(Mesh(result['vertices_mm'],result['faces']))
    obj['hs_quad_surface_table']=json.dumps(result['face_provenance'])
    obj['hs_quad_construction_sha256']=result['construction_sha256']
    surface=obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
    for i,row in enumerate(surface.data):row.value=i
    values={tuple(sorted((a,b))):v for a,b,v in result['edge_creases']}
    crease=obj.data.attributes.new('crease_edge','FLOAT','EDGE')
    for edge,row in zip(obj.data.edges,crease.data):row.value=values.get(tuple(sorted(edge.vertices)),0.)
    obj.modifiers=[NS(type='SUBSURF',**result['subdivision_modifier'])]
    write_authored_identity(obj,result,unit_scale=1.,topology_epoch=result['sparse_graph']['topology_epoch'] if epoch is None else epoch)
    obj['hs_object_id']='7581aa09-49b0-5d92-a182-823282e74000';obj.data['hs_data_id']='7581aa09-49b0-5d92-a182-823282e74001';obj['hs_feature_id']='neutral'
    binding=bind_native_structure(obj,source_binding={'fixture':'host_protocol_only'},unit_scale=1.)
    return obj,validate_native_structure(obj,unit_scale=1.,expected_binding=binding)


class SparsePipelineIntegrationTests(unittest.TestCase):
    def test_source_manifest_actual_float32_transport(self):
        obj,report=materialized(parameters())
        self.assertEqual(report['kernel_report']['counts']['faces'],622)
        cycles=report['semantic_control_loops']['cycles']
        self.assertTrue(any(row['classification']=='pole_crossing_edge_cycle' for row in cycles))
        self.assertTrue(any(row['classification']=='regular_edge_loop' for row in cycles))
        self.assertEqual(report['qualification'],'not_run')

    def test_independent_four_parameter_edits(self):
        p=parameters();_,before=materialized(p)
        cases=[]
        q=deepcopy(p);q['holes'][0]['radius']=14.;cases.append(q)
        q=deepcopy(p);q['holes'][0]['center']=[-3.,-2.];cases.append(q)
        q=deepcopy(p);q['z_min']=-5.;q['z_max']=5.;cases.append(q)
        q=deepcopy(p);q['edge_bevel']=1.2;cases.append(q)
        for q in cases:
            with self.subTest(parameters=q):
                plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
                _,after=materialized(q)
                proof=verify_authored_edit(before,after,plan)
                self.assertEqual(proof['status'],'pass')
                self.assertEqual(proof['qualification'],'not_run')
                if q['edge_bevel']!=p['edge_bevel']:
                    self.assertEqual(proof['protected_hole_regions']['status'],'pass')

    def test_unknown_policy_change_is_not_parameter_edit(self):
        p=parameters();_,before=materialized(p)
        p['sparse_cage']['hole_planar_support']=True
        with self.assertRaises(Exception):
            plan_authored_edit(before['witness'],before['authorship'],p,datum_policy='fixed_midplane')

    def test_full_body_insertions_migrate_actual_native_protocol(self):
        from hardsurface.identity import register_structure_binding
        p=parameters();_,before=materialized(p)
        for corridor,delta in [('east',44),('south',48)]:
            with self.subTest(corridor=corridor):
                q=deepcopy(p);q['sparse_cage']['insertions']=[{'corridor':corridor,'fraction':.5}]
                plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
                _,after=materialized(q)
                proof=verify_authored_edit(before,after,plan)
                self.assertEqual(proof['status'],'pass')
                self.assertEqual(proof['whole_body_face_delta'],delta)
                self.assertTrue(all(row['after']==34 for row in proof['port_changes'] if row['role'].endswith('outer_flat_boundary')))
                with self.assertRaises(Exception):
                    register_structure_binding({before['binding']['object_id']:before['binding']},after['binding'],previous_binding=before['binding'])
                registry=register_structure_binding({before['binding']['object_id']:before['binding']},after['binding'],previous_binding=before['binding'],topology_migration=proof)
                self.assertEqual(registry[after['binding']['object_id']]['topology_epoch'],1)

    def test_insertion_requires_real_epoch_and_exact_scope(self):
        p=parameters();_,before=materialized(p)
        q=deepcopy(p);q['sparse_cage']['insertions']=[{'corridor':'east','fraction':.5}]
        plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
        _,after=materialized(q,epoch=0)
        with self.assertRaises(Exception):verify_authored_edit(before,after,plan)
        q['edge_bevel']=1.2
        with self.assertRaises(Exception):plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')

    def test_actual_id_relabel_and_geometry_drift_rejected(self):
        obj,report=materialized(parameters())
        obj.data.vertices[0].co[0]+=1e-6
        with self.assertRaises(RuntimeFailure):validate_native_structure(obj,unit_scale=1.,expected_binding=report['binding'])


if __name__=='__main__':unittest.main()
