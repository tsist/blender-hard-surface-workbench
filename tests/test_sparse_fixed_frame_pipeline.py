# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed-frame host protocol tests; these are not Blender execution evidence."""
from copy import deepcopy
import unittest

from test_sparse_pipeline_integration import parameters, materialized
from test_sparse_source_stage import source_request
from hardsurface import contract as c
from hardsurface.planner import plan_request
from hardsurface.sparse_panel_geometry import build_sparse_panel
from hardsurface.sparse_parameter_edit import plan_parameter_edit
from hardsurface.structure_edit import plan_authored_edit, verify_authored_edit


def fixed_parameters(support=False):
    p=parameters()
    p['sparse_cage'].update(hole_planar_support=support,layout={
        'schema':'fixed-frame-axis-aligned/1.0',
        'feature_frame_mm':[-31.,-18.,12.,24.], 'corner_guard_mm':6.})
    return p


class FixedFramePipelineTests(unittest.TestCase):
    def test_complete_request_admits_explicit_layout(self):
        r=source_request()
        r['params']['design']['state']['features'][0]['program']['steps']=[fixed_parameters()]
        plan=plan_request(r)
        step=plan['request']['params']['design']['state']['features'][0]['program']['steps'][0]
        self.assertEqual(step['sparse_cage']['layout'],fixed_parameters()['sparse_cage']['layout'])
        self.assertEqual(plan['steps'][0]['geometry_estimate']['faces'],622)

    def test_layout_contract_rejects_incomplete_and_unknown_controls(self):
        for change in ('missing','wrong_version','extra','bool_guard'):
            r=source_request();p=fixed_parameters();layout=p['sparse_cage']['layout']
            if change=='missing':layout.pop('feature_frame_mm')
            elif change=='wrong_version':layout['schema']='fixed-frame-axis-aligned/999'
            elif change=='extra':layout['relax_angles']=True
            else:layout['corner_guard_mm']=True
            r['params']['design']['state']['features'][0]['program']['steps']=[p]
            with self.subTest(change=change),self.assertRaises(Exception):plan_request(r)

    def test_independent_edits_verify_exact_host_storage_protocol(self):
        for support in (False,True):
            p=fixed_parameters(support);_,before=materialized(p)
            edits=[]
            q=deepcopy(p);q['holes'][0]['radius']=14.;edits.append(q)
            q=deepcopy(p);q['holes'][0]['center']=[-6.,1.];edits.append(q)
            q=deepcopy(p);q.update(z_min=-5.,z_max=5.);edits.append(q)
            q=deepcopy(p);q['edge_bevel']=1.2;edits.append(q)
            for q in edits:
                with self.subTest(support=support,parameters=q):
                    plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
                    _,after=materialized(q)
                    proof=verify_authored_edit(before,after,plan)
                    self.assertEqual(proof['status'],'pass')
                    self.assertEqual(proof['qualification'],'not_run')
                    if q['holes']!=p['holes']:
                        scope=plan_parameter_edit(build_sparse_panel(p),q,datum='fixed_midplane')
                        self.assertTrue(scope['allowed_vertex_axes'])
                        self.assertTrue(all('/hole_rim/' in s or '/hole_support/' in s for s in scope['allowed_vertex_axes']))

    def test_frame_or_guard_change_is_not_a_nominal_edit(self):
        p=fixed_parameters();before=build_sparse_panel(p)
        for key in ('feature_frame_mm','corner_guard_mm'):
            q=deepcopy(p)
            if key=='feature_frame_mm':q['sparse_cage']['layout'][key][0]-=1.
            else:q['sparse_cage']['layout'][key]+=1.
            with self.subTest(key=key),self.assertRaises(Exception):
                plan_parameter_edit(before,q,datum='fixed_midplane')

    def test_whole_body_insertion_protocol_and_later_local_edit(self):
        for corridor,delta in (('east',44),('south',48)):
            p=fixed_parameters();_,before=materialized(p)
            q=deepcopy(p);q['sparse_cage']['insertions']=[{'corridor':corridor,'fraction':.5}]
            plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
            _,after=materialized(q)
            proof=verify_authored_edit(before,after,plan)
            self.assertEqual(proof['whole_body_face_delta'],delta)
            self.assertEqual(proof['status'],'pass')
            r=deepcopy(q);r['holes'][0]['center']=[-8.,2.]
            edit=plan_authored_edit(after['witness'],after['authorship'],r,datum_policy='fixed_midplane')
            _,final=materialized(r)
            self.assertEqual(verify_authored_edit(after,final,edit)['status'],'pass')

    def test_insertion_fraction_endpoints_preserve_exact_port_planes(self):
        from hardsurface.sparse_patch_graph import validate_sparse_mesh
        for layout in (True,):
            for support in (False,True):
                p=fixed_parameters(support) if layout else parameters()
                p['sparse_cage']['hole_planar_support']=support
                for corridor in ('east','south'):
                    for fraction in (.2,.3,.7,.8):
                        q=deepcopy(p);q['sparse_cage']['insertions']=[{'corridor':corridor,'fraction':fraction}]
                        with self.subTest(layout=layout,support=support,corridor=corridor,fraction=fraction):
                            mesh=build_sparse_panel(q)
                            self.assertEqual(validate_sparse_mesh(mesh)['status'],'pass')


if __name__=='__main__':unittest.main()
