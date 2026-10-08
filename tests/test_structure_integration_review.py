# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent dev5 host-only integration regressions.

The mesh protocol is a Python mock with float32 coordinates. No Blender, bpy,
.blend load/save, external runner, native qualification, or visual claim occurs.
"""
import ast
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from hardsurface import structure_native as native
from hardsurface import structure_edit as edit
from hardsurface.io import RuntimeFailure, atomic_json, checkpoint_evidence_artifacts
from hardsurface.quad_geometry import build, planning_evidence
from test_structure_native_contract import Mesh, Object, f32
from test_subd_panel import BASE

ROOT = Path(__file__).resolve().parents[1]


def panel(parameters=None):
    result = build(copy.deepcopy(BASE if parameters is None else parameters), 'review_panel')
    obj = Object(Mesh(result['vertices_mm'], result['faces']))
    table = []; lookup = {}; attr = obj.data.attributes.new('hs_quad_surface_id', 'INT', 'FACE')
    for i, provenance in enumerate(result['face_provenance']):
        key = json.dumps(provenance, sort_keys=True)
        if key not in lookup:
            lookup[key] = len(table); table.append(provenance)
        attr.data[i].value = lookup[key]
    obj['hs_quad_surface_table'] = json.dumps(table)
    obj['hs_quad_construction_sha256'] = result['construction_sha256']
    obj['hs_quad_constructor'] = 'quad.panel'
    obj['hs_quad_metadata'] = json.dumps(result['metadata'])
    obj['hs_subd_control_loops'] = json.dumps(result['control_loops'])
    obj['hs_subdivision_settings'] = json.dumps(result['subdivision_modifier'])
    obj.modifiers = [NS(type='SUBSURF', **result['subdivision_modifier'])]
    attr = obj.data.attributes.new('crease_edge', 'FLOAT', 'EDGE')
    crease = {tuple(sorted((a,b))): value for a,b,value in result['edge_creases']}
    for edge in obj.data.edges: attr.data[edge.index].value = f32(crease.get(tuple(edge.vertices), 0.))
    native.write_authored_identity(obj, result, unit_scale=1.0)
    obj['hs_object_id'] = '11111111-1111-4111-8111-111111111111'; obj.data['hs_data_id'] = '22222222-2222-4222-8222-222222222222'
    binding = native.bind_native_structure(obj, source_binding={'source': {'sha256': 'a'*64}, 'job_id': 'review-host'},
                                          expected_authorship_sha256=result['authorship_sha256'], unit_scale=1.0)
    report = native.validate_native_structure(obj, unit_scale=1.0, expected_binding=binding)
    return obj, result, report


def permute_panel(obj):
    mesh = obj.data; rng = random.Random(38110)
    vertices = list(range(len(mesh.vertices))); faces = list(range(len(mesh.polygons))); edges = list(range(len(mesh.edges)))
    rng.shuffle(vertices); rng.shuffle(faces); rng.shuffle(edges)
    inverse = {old: new for new, old in enumerate(vertices)}
    mesh.vertices = [mesh.vertices[i] for i in vertices]
    mesh.attributes[native.VERTEX_SLOT].data = [mesh.attributes[native.VERTEX_SLOT].data[i] for i in vertices]
    for i,v in enumerate(mesh.vertices): v.index = i
    for face in mesh.polygons: face.vertices = [inverse[i] for i in face.vertices]
    for edge in mesh.edges: edge.vertices = [inverse[i] for i in edge.vertices]
    mesh.polygons = [mesh.polygons[i] for i in faces]
    for name in (native.FACE_SLOT, 'hs_quad_surface_id'):
        mesh.attributes[name].data = [mesh.attributes[name].data[i] for i in faces]
    for i,face in enumerate(mesh.polygons): face.index = i
    mesh.edges = [mesh.edges[i] for i in edges]
    mesh.attributes['crease_edge'].data = [mesh.attributes['crease_edge'].data[i] for i in edges]
    for i,edge in enumerate(mesh.edges): edge.index = i


def core_node(name):
    tree = ast.parse((ROOT/'hardsurface/core.py').read_text())
    return next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)


class FullPanelProtocolIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj, cls.result, cls.report = panel()

    def test_host_planning_attests_authorship_independently_of_geometry(self):
        planned = planning_evidence(copy.deepcopy(BASE), 'review_panel')
        self.assertEqual(planned.get('authorship_sha256'), self.result['authorship_sha256'])
        self.assertEqual(planned.get('structure_schedule_revision'), self.result['authored_structure']['schedule_revision'])
        self.assertEqual(planned['construction_sha256'], self.result['construction_sha256'])

    def evidence_report(self, directory, detail):
        ref = atomic_json(directory/'structure.json', detail)
        binding = self.report['binding']
        return {'scene_snapshot': {'structure_registry': {binding['object_id']: binding}},
                'checks': {'structure_identity': {'status':'pass', 'evidence':[
                    {'status':'pass', 'object_id':binding['object_id'], 'data_id':binding['data_id'], 'details':ref}]}}}

    def test_complete_native_receipt_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.evidence_report(Path(directory), copy.deepcopy(self.report))
            self.assertEqual(len(checkpoint_evidence_artifacts(report, root=Path(directory))), 1)

    def test_native_receipt_kernel_signature_cannot_disagree_with_binding(self):
        bad = copy.deepcopy(self.report); bad['kernel_report']['geometry_signature'] = 'f'*64
        with tempfile.TemporaryDirectory() as directory:
            report = self.evidence_report(Path(directory), bad)
            with self.assertRaises(RuntimeFailure): checkpoint_evidence_artifacts(report, root=Path(directory))

    def test_native_receipt_cannot_disagree_with_snapshot_registry(self):
        bad = copy.deepcopy(self.report); bad['binding']['source_binding_sha256'] = 'f'*64
        with tempfile.TemporaryDirectory() as directory:
            report = self.evidence_report(Path(directory), bad)
            with self.assertRaises(RuntimeFailure): checkpoint_evidence_artifacts(report, root=Path(directory))

    def test_full_992_panel_passes_real_native_protocol_over_mock(self):
        report = self.report
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(len(report['witness']['mesh']['vertices']), 992)
        self.assertEqual(len(report['witness']['mesh']['faces']), 992)
        self.assertEqual(len(report['semantic_control_loops']['cycles']), 16)
        self.assertEqual(report['identity_binding_status'], 'bound')
        self.assertIn('saved_reopen', report['not_checked'])
        self.assertEqual(report['qualification'], 'not_run')

    def test_full_panel_native_storage_permutation(self):
        obj = copy.deepcopy(self.obj); permute_panel(obj)
        report = native.validate_native_structure(obj, unit_scale=1.0, expected_binding=self.report['binding'])
        self.assertEqual(report['binding'], self.report['binding'])
        self.assertNotEqual(report['authorship']['vertex_map'], self.report['authorship']['vertex_map'])
        self.assertEqual(report['semantic_control_loops'], self.report['semantic_control_loops'])

    def test_full_panel_unchanged_permuted_state_verifies_edit(self):
        obj = copy.deepcopy(self.obj); permute_panel(obj)
        before = native.validate_native_structure(obj, unit_scale=1.0, expected_binding=self.report['binding'])
        plan = edit.plan_authored_edit(before['witness'], before['authorship'], copy.deepcopy(BASE), datum_policy='not_requested')
        result = edit.verify_authored_edit(before, self.report, plan)
        self.assertEqual(result['status'], 'pass')

    def test_full_native_protocol_edit_hole_and_fixed_bottom(self):
        for change, datum in (({'center':[10,2], 'radius':8.0}, 'not_requested'), ({}, 'fixed_bottom')):
            p = copy.deepcopy(BASE)
            if change: p['holes'][0].update(change)
            else: p['z_max'] = 6.5; p['edit_datum'] = datum
            with self.subTest(datum=datum):
                plan = edit.plan_authored_edit(self.report['witness'], self.report['authorship'], p, datum_policy=datum)
                _,_,after = panel(p)
                self.assertEqual(edit.verify_authored_edit(self.report, after, plan)['status'], 'pass')

    def core_environment(self, obj):
        class Error(Exception):
            def __init__(self, code, *args): super().__init__(code, *args); self.code=code
        snapshot = {'design_state_sha256':'d'*64, 'objects':[],
                    'structure_registry':{self.report['binding']['object_id']:self.report['binding']}}
        fake_bpy = NS(context=NS(scene=NS(objects=[obj], unit_settings=NS(scale_length=1.0))),
                      data=NS(filepath='host-mock-no-file.blend'), app=NS(version_string='host mock',build_hash=b'host mock'))
        env = {'__package__':'hardsurface', 'bpy':fake_bpy, 'json':json, 'CoreError':Error,
               'inspect_scene':lambda:copy.deepcopy(snapshot),
               'loaded_structure_registry':lambda:copy.deepcopy(snapshot['structure_registry']),
               'digest':native.fingerprint, 'file_record':lambda path:{'host_mock':True},
               'PROCESS_IDENTITY':'host-mock-not-native'}
        exec(compile(ast.Module(body=[core_node('DomainExecutor'),core_node('verify_saved_candidate')],type_ignores=[]),
                     '<host-only-core-methods>', 'exec'), env)
        return env, snapshot

    def test_reopen_method_reextracts_actual_arrays_over_mock(self):
        obj = copy.deepcopy(self.obj); obj['hs_generated']=True
        env, snapshot = self.core_environment(obj)
        # Deliberately make the injected snapshot agree: the independent raw
        # extraction must still catch the fault, not merely compare two JSONs.
        obj.data.vertices[0].co[0] += 1e-12
        with self.assertRaises(RuntimeFailure) as error:
            env['verify_saved_candidate']({'scene_snapshot':snapshot})
        self.assertEqual(error.exception.code, 'STRUCTURE_GEOMETRY_DRIFT')

    def test_reopen_method_cannot_skip_missing_all_identity_markers(self):
        obj = copy.deepcopy(self.obj); obj['hs_generated']=True
        env, snapshot = self.core_environment(obj)
        for name in (native.MANIFEST,native.MANIFEST_SHA): obj.data.pop(name)
        obj.pop(native.AUTHORED_SHA); obj.pop('hs_subdivision_settings')
        for name in (native.VERTEX_SLOT,native.FACE_SLOT): obj.data.attributes.pop(name)
        with self.assertRaises(RuntimeFailure) as error:
            env['verify_saved_candidate']({'scene_snapshot':snapshot})
        self.assertEqual(error.exception.code, 'STRUCTURE_IDENTITY_MISSING')

    def test_final_gate_cannot_skip_registry_object_with_lost_markers(self):
        obj = copy.deepcopy(self.obj); obj['hs_generated']=True; obj['hs_feature_id']='review_panel'
        env, snapshot = self.core_environment(obj)
        for name in (native.MANIFEST,native.MANIFEST_SHA): obj.data.pop(name)
        obj.pop(native.AUTHORED_SHA); obj.pop('hs_subdivision_settings')
        for name in (native.VERTEX_SLOT,native.FACE_SLOT): obj.data.attributes.pop(name)
        executor = env['DomainExecutor'].__new__(env['DomainExecutor'])
        executor.structure_registry=snapshot['structure_registry']; executor.selected_features={'review_panel'}
        with self.assertRaises(RuntimeFailure) as error: executor._structure_identity_check()
        self.assertEqual(error.exception.code, 'STRUCTURE_IDENTITY_MISSING')

    def test_missing_slots_crease_or_source_never_downgrade(self):
        for name in (native.VERTEX_SLOT, native.FACE_SLOT, 'crease_edge', 'hs_quad_surface_id'):
            obj = copy.deepcopy(self.obj); obj.data.attributes.pop(name)
            with self.subTest(attribute=name), self.assertRaises(RuntimeFailure):
                native.validate_native_structure(obj, unit_scale=1.0, expected_binding=self.report['binding'])

    def test_full_panel_external_binding_rejects_source_reseal(self):
        obj = copy.deepcopy(self.obj); envelope = json.loads(obj.data[native.MANIFEST])
        envelope['identity_binding']['source_binding']['source']['sha256'] = 'b'*64
        obj.data[native.MANIFEST] = json.dumps(envelope)
        obj.data[native.MANIFEST_SHA] = native.fingerprint(envelope)
        with self.assertRaises(RuntimeFailure) as error:
            native.validate_native_structure(obj, unit_scale=1.0, expected_binding=self.report['binding'])
        self.assertEqual(error.exception.code, 'STRUCTURE_REGISTRY_DRIFT')


class CoreLifecycleHostReviewTests(unittest.TestCase):
    def test_distribution_contains_required_identity_runtime_modules(self):
        public = set((ROOT/'PUBLIC_FILES.txt').read_text().splitlines())
        required = {'hardsurface/subd_panel_identity.py', 'hardsurface/structure_native.py', 'hardsurface/structure_edit.py'}
        self.assertFalse(required-public, 'Standalone distributions must include the mandatory identity imports')

    def test_core_never_passes_length_conversion_as_scene_scale(self):
        node = core_node('DomainExecutor'); reopen = core_node('verify_saved_candidate')
        count = 0
        for call in (n for parent in (node, reopen) for n in ast.walk(parent) if isinstance(n, ast.Call)):
            for kw in call.keywords:
                if kw.arg == 'unit_scale':
                    count += 1
                    self.assertNotIsInstance(kw.value, ast.Constant, 'Scene scale must come from actual verified scene units')
        self.assertGreater(count, 3)

    def test_all_before_witnesses_and_plans_captured_before_any_removal(self):
        # Execute just the authored class definition with mocked dependencies;
        # there is no import of the Blender-only core or bpy.
        calls = []; objects = []
        for i in range(2):
            obj = Object(Mesh([[0,0,0]], [])); obj.name = 'mock%d'%i; obj.material_slots = []
            obj.update(hs_object_id='object%d'%i, hs_subdivision_settings='{}', hs_step_key='panel%d/body'%i)
            objects.append(obj)
        scene = NS(objects=objects, unit_settings=NS(scale_length=1.0))
        fake_bpy = NS(context=NS(scene=scene), data=NS(objects=NS(remove=lambda obj, **kw: calls.append(('remove',obj['hs_object_id'])))))
        class Error(Exception):
            def __init__(self, *args): super().__init__(*args)
        env = {'__package__':'hardsurface', 'bpy':fake_bpy, 'json':json, 'CoreError':Error,
               '_preflight_target':lambda obj,**kw:calls.append(('preflight',obj['hs_object_id'])), '_projection':lambda row:row}
        exec(compile(ast.Module(body=[core_node('DomainExecutor')],type_ignores=[]), '<host-only-core-methods>', 'exec'), env)
        executor = env['DomainExecutor'].__new__(env['DomainExecutor'])
        rows = [{'generated':True,'feature_id':'panel%d'%i,'object_id':'object%d'%i} for i in range(2)]
        executor.before = {'objects':rows,'generated_baseline':{r['object_id']:copy.deepcopy(r) for r in rows}}
        executor.selected_features = {'panel0','panel1'}; executor.non_target_ids = set(); executor.skip = set()
        executor.overlay = {}; executor.rebuild_objects = []; executor.structure_before = {}; executor.structure_edit_plans = {}
        executor.structure_registry = {r['object_id']: {'mock':'external binding'} for r in rows}
        executor.plan = {'steps':[{'step_key':'panel%d/body'%i,'op':'quad.panel','effective_params':{'marker':i}} for i in range(2)]}
        def validate(obj, **kwargs):
            calls.append(('extract',obj['hs_object_id'])); return {'witness':obj['hs_object_id'], 'authorship':{}}
        def make_plan(witness, authorship, parameters, **kwargs):
            calls.append(('plan',witness)); return {'scope':witness}
        with patch.object(native,'validate_native_structure',side_effect=validate), patch.object(edit,'plan_authored_edit',side_effect=make_plan):
            executor._prepare_existing()
        first_remove = next(i for i,row in enumerate(calls) if row[0]=='remove')
        self.assertEqual([row[0] for row in calls[:first_remove]].count('extract'), 2)
        self.assertEqual([row[0] for row in calls[:first_remove]].count('plan'), 2)
        self.assertEqual(set(executor.structure_before), {'object0','object1'})
        # A later target failing its predeclared edit leaves every old object in
        # place. Passing the first target must not begin eager deletion.
        calls.clear(); executor.rebuild_objects = []; executor.structure_before = {}; executor.structure_edit_plans = {}
        def stop_second(witness, authorship, parameters, **kwargs):
            if witness == 'object1': raise RuntimeFailure('REVIEW_STOP', 'Injected second target scope rejection')
            return make_plan(witness, authorship, parameters, **kwargs)
        with patch.object(native,'validate_native_structure',side_effect=validate), patch.object(edit,'plan_authored_edit',side_effect=stop_second):
            with self.assertRaises(RuntimeFailure): executor._prepare_existing()
        self.assertFalse(any(row[0] == 'remove' for row in calls))


if __name__ == '__main__': unittest.main()
