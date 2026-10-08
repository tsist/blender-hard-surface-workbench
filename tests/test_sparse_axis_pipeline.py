# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral axis-insertion HOST protocol fixtures; never Blender evidence.

The materialized helper simulates float32 metre storage only. Source preflight,
semantic lineage and saved-design admission remain independent assertions.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_sparse_fixed_frame import parameters as fixed_parameters
from test_sparse_pipeline_integration import materialized
from test_sparse_source_stage import source_request
from hardsurface import contract as c, host
from hardsurface.identity import register_structure_binding
from hardsurface.io import RuntimeFailure
from hardsurface.planner import plan_request
from hardsurface.sparse_native_edit import _validate_change
from hardsurface.sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
from hardsurface.sparse_patch_graph import semantic_mesh
from hardsurface.sparse_source_preflight import audit_sparse_source, require_sparse_source_preflight
from hardsurface.structure_edit import plan_authored_edit, verify_authored_edit


def parameters(support=False, layout='fixed-frame-axis-aligned/1.0'):
    p=fixed_parameters(support)
    p['edit_datum']='fixed_midplane'
    p['sparse_cage'].update(preview_levels=0,insertion_policy='axis_plane_v1')
    p['sparse_cage']['layout']['schema']=layout
    return p


def inserted(p, corridor='east', fraction=.5):
    q=deepcopy(p)
    q['sparse_cage']['insertions']=[*q['sparse_cage'].get('insertions',[]),
                                  {'corridor':corridor,'fraction':fraction}]
    return q


def request_for(p):
    r=source_request()
    r['params']['design']['state']['features'][0]['program']['steps']=[deepcopy(p)]
    return r


def patch_for(state, corridor='east', fraction=.5):
    feature=state['features'][0]
    return {'mode':'patch_if_revision','expected_revision':state['revision'],
            'state_sha256':c.fingerprint(state),'patches':[{'op':'insert_sparse_strip',
            'feature_id':feature['id'],'step_id':feature['program']['steps'][0]['id'],
            'expected_feature_sha256':c.fingerprint(feature),'corridor':corridor,'fraction':fraction}]}


class AxisContractTests(unittest.TestCase):
    def test_policy_is_explicit_requires_fixed_layout_and_bounds_two(self):
        for version in ('fixed-frame-axis-aligned/1.0','fixed-frame-axis-aligned/1.1'):
            p=inserted(inserted(parameters(layout=version)),'south')
            self.assertEqual(c._validate(p,c.schema('quad.panel'))['sparse_cage']['insertions'],
                             p['sparse_cage']['insertions'])
        for change in ('no_layout','unknown_policy','wrong_name','third','nonaxis_repeat'):
            p=inserted(inserted(parameters()),'south')
            if change=='no_layout':p['sparse_cage'].pop('layout')
            elif change=='unknown_policy':p['sparse_cage']['insertion_policy']='axis_plane_v999'
            elif change=='wrong_name':p['sparse_cage']['strip_policy']=p['sparse_cage'].pop('insertion_policy')
            elif change=='third':p=inserted(p,'east',.2)
            else:p['sparse_cage'].pop('insertion_policy')
            with self.subTest(change=change),self.assertRaises(c.ContractError):
                c._validate(p,c.schema('quad.panel'))

    def test_saved_design_appends_one_preserving_prefix_and_source(self):
        state=c.validate_state(request_for(parameters())['params']['design']['state'])
        original=deepcopy(state)
        once=c.resolve_design(patch_for(state),state)
        twice=c.resolve_design(patch_for(once,'south',.35),once)
        cfg=twice['features'][0]['program']['steps'][0]['sparse_cage']
        self.assertEqual(state,original)
        self.assertEqual(once['revision'],2);self.assertEqual(twice['revision'],3)
        self.assertEqual(cfg['insertions'],[{'corridor':'east','fraction':.5},
                                            {'corridor':'south','fraction':.35}])
        for command,source in ((patch_for(twice,'east',.8),twice),
                               (patch_for(once),once),
                               (patch_for(state),once)):
            with self.subTest(command=command),self.assertRaises(c.ContractError):
                c.resolve_design(command,source)
        command=patch_for(once,'south')
        command['patches'][0]['expected_feature_sha256']='0'*64
        with self.assertRaises(c.ContractError):c.resolve_design(command,once)
        command=patch_for(once,'south');command['state_sha256']='0'*64
        with self.assertRaises(c.ContractError):c.resolve_design(command,once)

    def test_native_change_is_exact_prefix_plus_one_and_no_other_edits(self):
        p=inserted(parameters());good=inserted(p,'south')
        self.assertEqual(_validate_change(p,good),good['sparse_cage']['insertions'][-1])
        for change in ('prefix','replace','remove','third','policy','layout','nominal'):
            q=deepcopy(good)
            if change=='prefix':q['sparse_cage']['insertions'][0]['fraction']=.35
            elif change=='replace':q['sparse_cage']['insertions']=[{'corridor':'south','fraction':.5}]
            elif change=='remove':q['sparse_cage']['insertions']=[]
            elif change=='third':q=inserted(q,'east',.2)
            elif change=='policy':q['sparse_cage'].pop('insertion_policy')
            elif change=='layout':q['sparse_cage']['layout']['corner_guard_mm']+=1
            else:q['edge_bevel']=1.2
            with self.subTest(change=change),self.assertRaises(Exception):_validate_change(p,q)
        legacy=parameters();legacy['sparse_cage'].pop('insertion_policy')
        self.assertEqual(_validate_change(legacy,inserted(legacy)),{'corridor':'east','fraction':.5})
        with self.assertRaises(Exception):_validate_change(inserted(legacy),inserted(inserted(legacy),'south'))
        with self.assertRaises(Exception):_validate_change(legacy,inserted(parameters()))

    def test_duplicate_close_and_third_rejected_by_constructor(self):
        p=inserted(parameters())
        for q in (inserted(p),inserted(p,'east',.500001),inserted(inserted(p,'south'),'east',.2)):
            with self.subTest(insertions=q['sparse_cage']['insertions']),self.assertRaises(RuntimeFailure):
                build_sparse_panel(q)


class AxisNativeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.states={}
        for support in (False,True):
            p=parameters(support);_,base=materialized(p)
            q=inserted(p);_,first=materialized(q)
            cls.states[support]=(p,base,q,first)

    def assert_edit(self, before, after, q, expected_delta):
        plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
        proof=verify_authored_edit(before,after,plan)
        self.assertEqual(proof['status'],'pass')
        self.assertEqual(proof['whole_body_face_delta'],expected_delta)
        self.assertEqual(proof['qualification'],'not_run')
        self.assertEqual(proof['after_binding']['topology_epoch'],proof['before_binding']['topology_epoch']+1)
        vm=after['witness']['structure']['vertex_map']
        for sid,i in before['witness']['structure']['vertex_map'].items():
            self.assertEqual(before['witness']['mesh']['vertices'][i],after['witness']['mesh']['vertices'][vm[sid]])
        self.assertEqual(len(plan['created_structural_entities']),1)
        self.assertEqual(proof['kernel_edit']['lineage_coverage'],'pass')
        return plan,proof

    def test_initial_east_protocol_ab_counts_and_whole_port_arity(self):
        for support,(p,before,q,after) in self.states.items():
            with self.subTest(support=support):
                plan,proof=self.assert_edit(before,after,q,44)
                self.assertEqual(len(after['witness']['mesh']['faces']),698 if support else 666)
                self.assertEqual({(x['before'],x['after']) for x in plan['port_changes']
                                  if x['role'].endswith('outer_flat_boundary')},{(32,34)})
                self.assertEqual(plan['host_transaction']['plan']['source_topology_epoch'],0)

    def test_continuing_east_and_crossing_south_matrix(self):
        for support,(_,base,p,before) in self.states.items():
            for corridor, fractions,delta in (('east',(.2,.35,.8),44),('south',(.2,.5,.8),50)):
                for fraction in fractions:
                    with self.subTest(support=support,corridor=corridor,fraction=fraction):
                        q=inserted(p,corridor,fraction);_,after=materialized(q)
                        plan,proof=self.assert_edit(before,after,q,delta)
                        self.assertEqual(plan['host_transaction']['plan']['source_topology_epoch'],1)
                        self.assertEqual({(x['before'],x['after']) for x in plan['port_changes']
                                         if x['role'].endswith('outer_flat_boundary')},{(34,36)})
                        oldloops={x['role']:x for x in before['witness']['structure']['loops']}
                        newloops={x['role']:x for x in after['witness']['structure']['loops']}
                        first=[role for role in oldloops if role.startswith('inserted.axis_strip.')]
                        self.assertEqual(len(first),1)
                        role=first[0]
                        self.assertEqual(len(newloops[role]['vertex_ids'])-len(oldloops[role]['vertex_ids']),
                                         2 if corridor=='south' else 0)
                        self.assertIn(oldloops[role]['id'],{x['old'][0] for x in plan['kernel_contract']['lineage'] if x['old']})
                        if corridor=='south':self.assertIn(oldloops[role]['id'],plan['kernel_contract']['affected_entity_ids'])

    def test_registry_requires_exact_migration_at_both_epochs(self):
        p,before,q,after=self.states[False]
        registry={before['binding']['object_id']:before['binding']}
        for source,target,params in ((before,after,q),):
            plan,proof=self.assert_edit(source,target,params,44)
            with self.assertRaises(c.ContractError):register_structure_binding(registry,target['binding'],previous_binding=source['binding'])
            registry=register_structure_binding(registry,target['binding'],previous_binding=source['binding'],topology_migration=proof)
        r=inserted(q,'south');_,final=materialized(r)
        plan,proof=self.assert_edit(after,final,r,50)
        with self.assertRaises(c.ContractError):register_structure_binding(registry,final['binding'],previous_binding=after['binding'])
        registry=register_structure_binding(registry,final['binding'],previous_binding=after['binding'],topology_migration=proof)
        self.assertEqual(registry[final['binding']['object_id']]['topology_epoch'],2)

    def test_stale_epoch_and_original_coordinate_drift_rejected(self):
        p,before,q,after=self.states[False]
        plan=plan_authored_edit(before['witness'],before['authorship'],q,datum_policy='fixed_midplane')
        _,wrong=materialized(q,epoch=0)
        with self.assertRaises(Exception):verify_authored_edit(before,wrong,plan)
        drift=deepcopy(after);drift['witness']['mesh']['vertices'][0][0]+=.001
        with self.assertRaises(Exception):verify_authored_edit(before,drift,plan)
        for key in ('object_id','data_id','context_sha256'):
            forged=deepcopy(after);forged['binding'][key]='foreign'
            with self.subTest(key=key),self.assertRaises(Exception):verify_authored_edit(before,forged,plan)

    def test_axis_loops_are_real_yz_xz_planes_and_previous_loop_expands(self):
        _,_,p,_=self.states[False]
        q=inserted(p,'south');body=build_sparse_panel(q)
        loops=body['sparse_graph']['semantic_inserted_loops']
        self.assertEqual(len(loops),2)
        for loop in loops:
            self.assertEqual({body['vertices_mm'][i][loop['axis_index']] for i in loop['vertex_indices']},{loop['cut_mm']})
        _,report=materialized(q)
        declared={x['role']:x for x in report['witness']['structure']['loops']}
        for row in loops:
            self.assertEqual(abs(declared[row['role']]['normal'][row['axis_index']]),1.)
            # Newell accumulation can leave roundoff even on exact common
            # coordinate planes; exact plane membership was asserted above.
            self.assertAlmostEqual(declared[row['role']]['normal'][2],0.,delta=1e-14)
        before=deepcopy(body);before['sparse_graph']['semantic_inserted_loops'][0]['cut_mm']+=.01
        with self.assertRaises(RuntimeFailure):validate_sparse_authored_identity(before)

    def test_later_nominal_edit_preserves_axis_semantic_schedule(self):
        _,_,p,_=self.states[False]
        q=inserted(p,'south');_,before=materialized(q)
        r=deepcopy(q);r['holes'][0]['center']=[-7.,2.]
        plan=plan_authored_edit(before['witness'],before['authorship'],r,datum_policy='fixed_midplane')
        _,after=materialized(r)
        self.assertEqual(verify_authored_edit(before,after,plan)['status'],'pass')
        self.assertEqual(before['authorship']['semantic_control_loops'],after['authorship']['semantic_control_loops'])


class AxisSourceAdmissionTests(unittest.TestCase):
    def test_new_scene_and_second_state_have_fresh_whole_source_gates(self):
        for support in (False,True):
            p=inserted(parameters(support));q=inserted(p,'south')
            for stage,params in (('first',p),('second',q)):
                with self.subTest(support=support,stage=stage):
                    request=request_for(params);saved_state=None
                    if stage=='first':
                        self.assertEqual(request['params']['source']['kind'],'new_scene')
                    else:
                        saved_state=c.validate_state(request_for(p)['params']['design']['state'])
                        # Planning consumes this explicit mock descriptor and
                        # saved state only. No .blend is read or materialized.
                        request['params']['source']={'kind':'saved_blend',
                            'file':'/tmp/neutral-axis-host-only-not-a-native-file.blend',
                            'expected_sha256':'0'*64,'bytes':1,'length_unit':'mm'}
                        request['params']['design']=patch_for(saved_state,'south')
                    plan=plan_request(request,saved_state=saved_state)
                    if saved_state is not None:
                        self.assertEqual(plan['resolved_design_state']['revision'],saved_state['revision']+1)
                        self.assertEqual(plan['resolved_design_state']['features'][0]['program']['steps'][0]['sparse_cage']['insertions'],
                                         params['sparse_cage']['insertions'])
                    # Actual host run envelope must fit the unchanged canonical
                    # depth/node budget and the worker's 2 MiB JSON input bound.
                    payload={'action':'run','request':plan['request'],'plan':plan,
                             'solutions':{},'job_dir':'/tmp/neutral-axis-host-only',
                             'source':request['params']['source'].get('file'),
                             'base_design_state':saved_state,'reference_approval':None}
                    encoded=c.canonical_bytes(payload)
                    self.assertLessEqual(len(encoded),c.MAX_BYTES)
                    self.assertEqual(c.strict_loads(encoded),payload)
                    self.assertEqual(plan['steps'][0]['geometry_estimate']['faces'],
                                     (698 if support else 666)+(50 if stage=='second' else 0))
                    mesh=build_sparse_panel(params,feature_id='neutral-feature')
                    report=audit_sparse_source(mesh,feature_id='neutral-feature')
                    self.assertIs(require_sparse_source_preflight(report,mesh=mesh),report)
                    self.assertTrue(report['passed'])
                    for domain in report['coordinate_domains'].values():
                        self.assertEqual(domain['quality']['policy']['max_aspect_ratio'],50.)
                        self.assertEqual(domain['quality']['policy']['max_support_band_aspect_ratio'],100.)

    def test_failed_axis_source_gate_stops_before_spawn_or_enqueue(self):
        p=inserted(parameters());p['sparse_cage']['wall_support_fraction']=.03
        root=Path(tempfile.mkdtemp(prefix='neutral-axis-preflight-cli-'))
        path=root/'request.json';path.write_text(json.dumps(request_for(p)))
        with mock.patch.object(host.subprocess,'Popen',side_effect=AssertionError('Native spawn forbidden')) as spawn, \
             mock.patch.object(host,'store_for',side_effect=AssertionError('Enqueue forbidden')) as store, \
             mock.patch('sys.stdout',new_callable=io.StringIO) as output:
            status=host.main(['hardsurface','plan','--request',str(path)])
        self.assertEqual(status,2)
        error=json.loads(output.getvalue())['error']
        self.assertEqual(error['code'],'SPARSE_SOURCE_PREFLIGHT_FAILED')
        self.assertIs(error['details']['report']['passed'],False)
        spawn.assert_not_called();store.assert_not_called()


if __name__=='__main__':unittest.main()
