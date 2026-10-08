"""Mocked bpy lifecycle only: no native evaluation, images or production claims."""
import copy
from contextlib import contextmanager, ExitStack
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hardsurface import sparse_observation as a, observation as o, subdivision as s
from hardsurface import observation_normals as n
from hardsurface import subdivision_source as semantic
from hardsurface.io import RuntimeFailure, descriptor
from test_sparse_surface_observation import request
from test_observation_geometry_normals import Source


class OwnedCollection:
    def __init__(self,kind,events,fail=False,new=None):
        self.kind,self.events,self.fail,self.new=kind,events,fail,new
    def remove(self,item,**kwargs):
        self.events.append(('remove',self.kind,kwargs))
        if self.fail: raise RuntimeError('injected '+self.kind+' cleanup failure')


class LifecycleTests(unittest.TestCase):
    def run_case(self, *, render_error=False, cleanup_failure=False, original_drift=False, scene_count=1):
        raw=o.validate_request(request());p=raw['params'];option=p['sparse_evaluation']
        events=[];oid=option['object_id'];original=Source()
        original.modifiers=[NS(**copy.deepcopy(option['expected_source_modifier']))]
        original.get=lambda key:oid if key=='hs_object_id' else None
        original.instance_type='NONE';original.matrix_world=[[float(i==j) for j in range(4)] for i in range(4)]
        mesh=copy.deepcopy(original.data);original.data.copy=lambda:mesh
        clone=Source();clone.modifiers=[NS(**copy.deepcopy(option['expected_source_modifier']))]
        original.copy=lambda:clone
        evaluated=NS(to_mesh=lambda **kw:mesh,to_mesh_clear=lambda:events.append(('to_mesh_clear',)))
        clone.evaluated_get=lambda graph:evaluated
        source_scene=NS(name='original',objects=[original]*scene_count,view_layers=[NS()],render=NS(use_simplify=False))
        temporary=NS(name='owned',unit_settings=NS(),render=NS(),view_layers=[NS()],
                     frame_set=lambda value:None,collection=NS(objects=NS(link=lambda obj:events.append(('link',)))))
        context=NS(scene=source_scene,view_layer=source_scene.view_layers[0],evaluated_depsgraph_get=lambda:object())
        temporary.view_layers[0].update=lambda:None
        @contextmanager
        def override(**kwargs):
            old={key:getattr(context,key) for key in kwargs}
            for key,value in kwargs.items():setattr(context,key,value)
            try:yield
            finally:
                for key,value in old.items():setattr(context,key,value)
        context.temp_override=override
        scenes=OwnedCollection('SCENE',events,new=lambda name:temporary)
        meshes=OwnedCollection('MESH',events)
        objects=OwnedCollection('OBJECT',events,fail=cleanup_failure)
        record={'native_mock':'not_a_native_result'}
        source_record={'source_control_mesh':record,'source_modifier':{'settings':option['expected_source_modifier']}}
        evaluation={'mode':s.SPARSE_DIAGNOSTIC_PROFILE,'modifier_profile':{'actual_source_modifier':copy.deepcopy(option['expected_source_modifier'])},
                    'semantic_source':{'status':'mock_bound'}}
        bound={'evidence':{'status':'mock_bound'}}
        registry={oid:{'mock':True}}
        drift={'enabled':False}
        def observe(inner,job,*,_source_guard):
            self.assertIs(context.scene,temporary)
            self.assertNotIn('sparse_evaluation',inner['params'])
            self.assertEqual(original.modifiers[0].levels,0)
            self.assertEqual(clone.modifiers[0].levels,2)
            drift['enabled']=original_drift
            _source_guard('before_render')
            if render_error:raise ValueError('injected rendering failure')
            _source_guard('after_render')
            return {'previews':[{'temporary_transformations':[{'frozen_geometry':{'normal_isolation':{'source':{
                'source_mesh':record,'source_modifier':{'settings':copy.deepcopy(vars(clone.modifiers[0]))}}}}}]}]}
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            path=Path(directory)/'source.blend';path.write_bytes(b'HOST fake source; not Blender')
            d=descriptor(path);p['source']={'file':d['file'],'expected_sha256':d['sha256'],'bytes':d['bytes']}
            bpy=NS(context=context,data=NS(filepath=str(path),scenes=scenes,meshes=meshes,objects=objects))
            stack.enter_context(patch.dict(sys.modules,{'bpy':bpy,
                'hardsurface.core':NS(loaded_structure_registry=lambda:registry),
                'hardsurface.ops.geometry':NS(require_si_scene=lambda:{'scale_length':1.})}))
            for module,name,value in [
                (s,'prepare_source_evaluation',lambda *args,**kw:(evaluation,bound)),
                (s,'inspect_sparse_authored_control_loops',lambda *args,**kw:{'status':'pass'}),
                (s,'source_scene_evaluation',lambda scene:{'frame_current':1}),
                (s,'_mesh_signature',lambda obj:'drift' if drift['enabled'] else 'same'),
                (s,'_authored_metadata_signature',lambda obj:'same'),
                (n,'source_record',lambda *args,**kw:source_record),
                (n,'mesh_record',lambda *args,**kw:record),
                (semantic,'evaluated_bore_domain',lambda *args,**kw:{'evidence':{'status':'validated','level':2}})]:
                stack.enter_context(patch.object(module,name,value))
            result=error=None
            try:result=a.execute_sparse(raw,directory,observe=observe)
            except Exception as caught:error=caught
            self.assertIs(context.scene,source_scene)
            self.assertEqual(path.read_bytes(),b'HOST fake source; not Blender')
            self.assertEqual(original.modifiers[0].levels,0)
        return result,error,events

    def test_original_scene_limit_precedes_clone_allocation(self):
        result,error,events=self.run_case(scene_count=4097)
        self.assertIsNone(result);self.assertIsInstance(error,RuntimeFailure)
        self.assertEqual(error.code,'OBSERVATION_SCENE_LIMIT')
        self.assertEqual(events,[])

    def test_success_has_full_guard_phases_and_owned_cleanup(self):
        result,error,events=self.run_case()
        self.assertIsNone(error)
        witness=result['sparse_evaluation']
        self.assertTrue(witness['original_source_preserved'])
        self.assertEqual(witness['source_modifier']['levels'],0)
        self.assertEqual(witness['effective_modifier']['levels'],2)
        self.assertEqual([r['phase'] for r in witness['original_source_checks']],
                         ['before_clone','before_render','after_render','after_observation','after_clone_cleanup'])
        self.assertEqual([e[1] for e in events if e[0]=='remove'],['OBJECT','MESH','SCENE'])

    def test_render_failure_preserved_and_all_owned_cleanup_attempted(self):
        result,error,events=self.run_case(render_error=True)
        self.assertIsNone(result);self.assertIsInstance(error,ValueError)
        self.assertEqual(str(error),'injected rendering failure')
        self.assertEqual([e[1] for e in events if e[0]=='remove'],['OBJECT','MESH','SCENE'])

    def test_cleanup_failure_does_not_skip_remaining_releases(self):
        result,error,events=self.run_case(render_error=True,cleanup_failure=True)
        self.assertIsNone(result);self.assertIsInstance(error,RuntimeFailure)
        self.assertEqual(error.code,'OBSERVATION_SPARSE_CLEANUP')
        self.assertIsInstance(error.__cause__,ValueError)
        self.assertEqual([e[1] for e in events if e[0]=='remove'],['OBJECT','MESH','SCENE'])

    def test_original_drift_stops_render_and_cannot_return_success(self):
        result,error,events=self.run_case(original_drift=True)
        self.assertIsNone(result);self.assertIsInstance(error,RuntimeFailure)
        self.assertEqual(error.code,'OBSERVATION_SPARSE_CLEANUP')
        self.assertEqual(error.__cause__.code,'OBSERVATION_SOURCE_CHANGED')
        self.assertEqual([e[1] for e in events if e[0]=='remove'],['OBJECT','MESH','SCENE'])


if __name__=='__main__':unittest.main()
