"""HOST contract/identity tests only; no Blender, renderer or saved-file claims."""
import ast
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hardsurface import observation as o, subdivision as s, surface_export as x
from hardsurface import sparse_observation as a, contract as c
from hardsurface.io import RuntimeFailure
from test_surface_export import ObservationBindingTests as OldBinding, request as old_export, PROJECTION
from test_observation_geometry_normals import request as old_observe
from test_sparse_subdivision_diagnose import request as sparse_diagnose
from test_sparse_subdivision_source import native_sparse, capture, reorder

ROOT = Path(__file__).resolve().parents[1]


def request():
    raw = old_observe(); diagnostic = sparse_diagnose()['params']
    raw['params'].update(normal_policy='geometry_normals_v1', wire={'enabled':False, 'mesh_state':'evaluated'},
        sparse_evaluation={'mode':a.MODE, 'object_id':diagnostic['target']['object_id'], 'level':2,
                           'expected_source_modifier':diagnostic['evaluation_profile']['expected_source_modifier']})
    raw['params']['views']=[{'name':'whole','direction':'three_quarter',
                           'visible_object_ids':[diagnostic['target']['object_id']]}]
    return raw


class SparseObservationContractTests(unittest.TestCase):
    def test_explicit_normalized_and_legacy_fingerprint_unchanged(self):
        raw=request(); before=copy.deepcopy(raw); q=o.validate_request(raw)
        self.assertEqual(before,raw);self.assertEqual(q,o.validate_request(q))
        raw=old_observe();current=o.validate_request(raw)
        legacy=copy.deepcopy(o.REQUEST);del legacy['properties']['params']['properties']['sparse_evaluation']
        with patch.object(o,'REQUEST',legacy):old=o.validate_request(raw)
        self.assertEqual(current,old);self.assertEqual(c.fingerprint(current),c.fingerprint(old))

    def test_rejects_wrong_level_profile_wire_normals_explosion_selection(self):
        changes=[lambda p:p['sparse_evaluation'].update(level=3),
            lambda p:p['sparse_evaluation'].update(level=2.),
            lambda p:p['sparse_evaluation']['expected_source_modifier'].update(levels=2),
            lambda p:p['sparse_evaluation']['expected_source_modifier'].update(quality=3),
            lambda p:p['sparse_evaluation']['expected_source_modifier'].update(use_custom_normals=True),
            lambda p:p['wire'].update(enabled=True), lambda p:p.pop('normal_policy'),
            lambda p:p['views'][0].update(mesh_state='control'),
            lambda p:p['views'][0].update(explode_z_mm={p['sparse_evaluation']['object_id']:0}),
            lambda p:p['views'][0].update(visible_object_ids=['81111111-1111-4111-8111-111111111111'])]
        for index,mutate in enumerate(changes):
            raw=request();mutate(raw['params'])
            with self.subTest(index=index),self.assertRaises(c.ContractError):o.validate_request(raw)

    def test_effective_delta_is_levels_only_and_preserves_caller(self):
        original=request()['params']['sparse_evaluation']['expected_source_modifier'];before=copy.deepcopy(original)
        effective=a.effective_modifier(original)
        self.assertEqual(original,before)
        self.assertEqual({k for k in original if original[k]!=effective[k]}, {'levels','render_levels'})
        self.assertEqual(effective['levels'],2)

    def test_sparse_surface_export_requires_own_binding_preserves_legacy(self):
        q=sparse_diagnose();q['params']['cpu_threads']=2
        q['params']['surface_export']=copy.deepcopy(old_export()['params']['surface_export'])
        with self.assertRaises(c.ContractError):s.validate_request(q)
        q['params']['surface_export']['binding_mode']=a.MODE
        self.assertEqual(s.validate_request(q)['params']['levels'],[0,1,2,3])
        legacy=old_export();legacy['params']['surface_export']['binding_mode']=a.MODE
        with self.assertRaises(c.ContractError):s.validate_request(legacy)
        self.assertEqual(s.validate_request(old_export())['params']['levels'],[0,2,3])

    def test_schemas_exact_and_shared_modifier_contract(self):
        for name,module in [('observe',o),('subdivision-diagnose',s)]:
            self.assertEqual(json.loads((ROOT/('schemas/hardsurface-'+name+'.schema.json')).read_text()),module.schema())
        self.assertEqual(a.OPTION['properties']['expected_source_modifier'],s._SOURCE_MODIFIER)

    def test_dispatch_routes_opt_in_without_loading_blender(self):
        sentinel={'host_dispatch':'pass'}
        with patch.object(a,'execute_sparse',return_value=sentinel) as adapter:
            self.assertEqual(o.execute(request(),'/tmp/owned-host-job'),sentinel)
        args=adapter.call_args
        self.assertEqual(args.args[0],o.validate_request(request()))
        self.assertIs(args.kwargs['observe'],o.execute)

    def test_clone_configuration_only_changes_owned_modifier(self):
        from types import SimpleNamespace
        original=request()['params']['sparse_evaluation']['expected_source_modifier']
        frozen=copy.deepcopy(original)
        clone=SimpleNamespace(**copy.deepcopy(original))
        witness=s.configure_source_clone_modifier(clone,original,2)
        self.assertEqual(original,frozen)
        self.assertEqual(vars(clone),a.effective_modifier(original))
        self.assertEqual(set(witness['intentional_overrides']),{'levels','render_levels'})

    def test_native_adapter_source_writes_and_render_are_not_in_adapter(self):
        tree=ast.parse((ROOT/'hardsurface/sparse_observation.py').read_text())
        calls=[ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertFalse(any('save' in n or 'render.render' in n or 'normals_split' in n for n in calls))
        writes=[ast.unparse(t) for n in ast.walk(tree) if isinstance(n,ast.Assign) for t in n.targets]
        self.assertFalse(any(t.startswith('original.') for t in writes))
        self.assertIn('configure_source_clone_modifier',calls)
        self.assertIn('prepare_source_evaluation',calls)
        self.assertIn('evaluated_bore_domain',calls)
        self.assertIn('observe',calls)


class SparseObservationBindingTests(OldBinding):
    def setUp(self):
        super().setUp()
        self.expected=copy.deepcopy(request()['params']['sparse_evaluation']['expected_source_modifier'])
        effective=a.effective_modifier(self.expected)
        self.option['binding_mode']=a.MODE
        view=self.report['previews'][0]
        view['native_projection_inputs']=copy.deepcopy(PROJECTION)
        source=view['temporary_transformations'][0]['frozen_geometry']['normal_isolation']['source']
        source['source_modifier']['settings']=effective
        self.report['sparse_evaluation']={'mode':a.MODE,'source_object_id':view['temporary_transformations'][0]['object_id'],
            'source_level':0,'level':2,'original_source_preserved':True,'evaluation_scope':'owned_separate_scene_clone_only',
            'source_modifier':self.expected,'effective_modifier':effective,
            'original_control_identity':source['source_control_mesh'],
            'source_control_loops':{'status':'pass'},'semantic_transport':{'status':'validated','level':2},
            'source_binding':{'mode':s.SPARSE_DIAGNOSTIC_PROFILE},
            'original_source_checks':[{'phase':p,'original_source_identity':'pass'} for p in
                ('before_clone','before_render','after_render','after_observation','after_clone_cleanup')]}
        self.save_report()

    def bind(self):
        return x.bind_observation(self.option,self.source,
            self.report['previews'][0]['temporary_transformations'][0]['object_id'],self.expected,sparse=True)

    def test_new_projection_is_native_and_legacy_cannot_consume(self):
        result=self.bind()
        self.assertEqual(result['views'][0]['calibration_input']['projection_inputs_origin'],'native_render_time_recorded')
        with self.assertRaises(RuntimeFailure):
            x.bind_observation(self.option,self.source,self.report['sparse_evaluation']['source_object_id'],self.expected)

    def test_sparse_witness_mutations_fail_closed(self):
        original=copy.deepcopy(self.report)
        changes=[lambda r:r['sparse_evaluation'].update(level=3),
            lambda r:r['sparse_evaluation']['source_modifier'].update(levels=2),
            lambda r:r['sparse_evaluation']['effective_modifier'].update(quality=3),
            lambda r:r['sparse_evaluation'].update(original_source_preserved=False),
            lambda r:r['sparse_evaluation']['original_source_checks'].pop(),
            lambda r:r['sparse_evaluation']['semantic_transport'].update(status='not_run'),
            lambda r:r['previews'][0]['native_projection_inputs'].update(shift_x=.1)]
        for index,mutate in enumerate(changes):
            self.report=copy.deepcopy(original);mutate(self.report);self.save_report()
            with self.subTest(index=index),self.assertRaises(RuntimeFailure):self.bind()


class NativeProjectionOriginTests(unittest.TestCase):
    def test_native_recorded_calibration_origin_and_provenance(self):
        from test_surface_projection import NativeMock, record
        from hardsurface import surface_projection as projection
        native=NativeMock();value=record();value['projection_inputs_origin']='native_render_time_recorded'
        result=projection.calibrate_views([value],native.bpy,native.scene,[])[0]
        self.assertEqual(result['projection_inputs_origin'],'native_render_time_recorded')
        self.assertEqual(result['provenance']['declared_unrecorded_inputs'],[])
        self.assertEqual(result['provenance']['native_render_time_inputs'],sorted(value['projection_inputs']))
        self.assertNotIn('caller declarations',' '.join(result['limitations']))
        native=NativeMock();legacy=projection.calibrate_views([record()],native.bpy,native.scene,[])[0]
        self.assertNotIn('native_render_time_inputs',legacy['provenance'])


class SparseSourceMapsTests(unittest.TestCase):
    def test_actual_authored_sparse_map_survives_storage_permutation(self):
        obj,binding,_=native_sparse();source=capture(obj,binding)
        first=x.capture_source_maps(obj,source,unit_scale=1.,registry_entry=binding)
        reorder(obj);source=capture(obj,binding)
        second=x.capture_source_maps(obj,source,unit_scale=1.,registry_entry=binding)
        self.assertEqual(first['source_slot_to_authored_face_id'],second['source_slot_to_authored_face_id'])
        self.assertEqual(len(second['source_parent_slots']),len(obj.data.polygons))
        self.assertTrue(second['named_control_loops'])


if __name__=='__main__':unittest.main()
