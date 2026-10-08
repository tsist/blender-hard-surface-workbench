# SPDX-License-Identifier: GPL-3.0-or-later
"""Host mock protocol tests only. No bpy, Blender, legacy model or native run.

A tiny explicitly authored closed cube exercises transport faults independently
of the panel constructor. Passing these tests does not qualify Blender APIs,
float storage, save/reopen, the 992-element cage, or visual output.
"""
import copy
import json
import math
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

from hardsurface.io import RuntimeFailure
from hardsurface.structure_kernel import fingerprint
from hardsurface.subd_panel_identity import (authorship_payload, fingerprint as author_fingerprint,
                                            semantic_connectivity, semantic_creases)
from hardsurface.structure_native import (
    VERTEX_SLOT, FACE_SLOT, MANIFEST, MANIFEST_SHA, AUTHORED_SHA, SIGNATURES,
    verify_materialization_order, write_authored_identity, extract_control_mesh,
    validate_native_structure, bind_native_structure, compact_native_report,
    has_native_identity,
)


def f32(value):
    return struct.unpack('!f', struct.pack('!f', value))[0]


class Attributes(dict):
    def __init__(self, mesh):
        super().__init__(); self.mesh = mesh
    def new(self, name, kind, domain):
        if name in self: raise AssertionError('Protocol attempted duplicate attribute')
        count = {'POINT': len(self.mesh.vertices), 'FACE': len(self.mesh.polygons), 'EDGE': len(self.mesh.edges)}[domain]
        attr = NS(name=name, data_type=kind, domain=domain, data=[NS(value=0) for _ in range(count)])
        self[name] = attr; return attr


class Mesh(dict):
    def __init__(self, vertices, faces):
        super().__init__()
        self.vertices = [NS(index=i, co=[f32(x*.001) for x in v]) for i,v in enumerate(vertices)]
        self.polygons = [NS(index=i, vertices=list(face)) for i,face in enumerate(faces)]
        pairs = sorted({tuple(sorted((a,b))) for face in faces for a,b in zip(face,face[1:]+face[:1])})
        self.edges = [NS(index=i, vertices=list(pair)) for i,pair in enumerate(pairs)]
        self.attributes = Attributes(self)
        self.users = 1; self.shape_keys = None; self.animation_data = None


class Object(dict):
    def __init__(self, mesh):
        super().__init__(); self.data = mesh; self.name = 'HostProtocolFixture'; self.type = 'MESH'
        self.mode = 'OBJECT'; self.parent = None; self.constraints = []; self.animation_data = None
        self.modifiers = []; self.matrix_world = [[float(i == j) for j in range(4)] for i in range(4)]
    def evaluated_get(self, *args, **kwargs):
        raise AssertionError('Native identity MUST NOT read evaluated data')


def fixture():
    vertices = [[-2.,-2.,-2.],[2.,-2.,-2.],[2.,2.,-2.],[-2.,2.,-2.],[-2.,-2.,2.],[2.,-2.,2.],[2.,2.,2.],[-2.,2.,2.]]
    faces = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    ids = ['corner/---','corner/+--','corner/++-','corner/-+-','corner/--+','corner/+-+','corner/+++','corner/-++']
    face_ids = ['side/bottom','side/top','side/front','side/right','side/back','side/left']
    source = [{'feature_id': 'host-fixture', 'surface_id': name, 'surface_role': name} for name in face_ids]
    result = {'vertices_mm': vertices, 'faces': faces, 'face_provenance': source,
              'edge_creases': [], 'construction_sha256': fingerprint({'host_mock_fixture': [vertices,faces,source]})}
    authored = {'schema_version': '1.0', 'schedule_revision': 'host_mock_cube_identity_v1',
                'vertex_map': dict(zip(ids, range(8))), 'face_map': dict(zip(face_ids, range(6))),
                'semantic_control_loops': [], 'parameter_binding': {'fixture': 'host-only closed cube'},
                'supported_edit_datum': {'coordinate_space':'object_local_mm'}, 'alias_manifest': {},
                'edit_dependencies': {}}
    authored['semantic_connectivity_sha256'] = author_fingerprint(semantic_connectivity(result, authored))
    authored['semantic_crease_sha256'] = author_fingerprint(semantic_creases(result, authored))
    authored['authorship_sha256'] = author_fingerprint(authorship_payload(authored))
    result['authored_structure'] = authored; result['authorship_sha256'] = authored['authorship_sha256']
    obj = Object(Mesh(vertices,faces))
    obj['hs_quad_surface_table'] = json.dumps(source); obj['hs_quad_construction_sha256'] = result['construction_sha256']
    a = obj.data.attributes.new('hs_quad_surface_id','INT','FACE')
    for i,row in enumerate(a.data): row.value = i
    obj.data.attributes.new('crease_edge','FLOAT','EDGE')
    return obj,result


def written():
    obj,result = fixture(); report = write_authored_identity(obj,result,unit_scale=1.0)
    return obj,result,report


def bound():
    obj,result,_ = written(); obj['hs_object_id']='object-fixture'; obj.data['hs_data_id']='data-fixture'
    binding = bind_native_structure(obj, source_binding={'source': {'sha256':'a'*64}, 'job_id':'host-mock'}, unit_scale=1.0)
    report = validate_native_structure(obj,unit_scale=1.0,expected_binding=binding)
    return obj,result,report


def permute(obj, *, carry_vertex_slots=True, carry_face_slots=True):
    mesh = obj.data; permutation = [6,2,4,0,7,5,1,3]
    lookup = {old:new for new,old in enumerate(permutation)}
    mesh.vertices = [mesh.vertices[i] for i in permutation]
    if carry_vertex_slots:
        attr=mesh.attributes[VERTEX_SLOT]; attr.data = [attr.data[i] for i in permutation]
    for i,v in enumerate(mesh.vertices): v.index=i
    for p in mesh.polygons: p.vertices=[lookup[i] for i in p.vertices]
    for e in mesh.edges: e.vertices=[lookup[i] for i in e.vertices]
    mesh.polygons.reverse()
    if carry_face_slots: mesh.attributes[FACE_SLOT].data.reverse()
    mesh.attributes['hs_quad_surface_id'].data.reverse()
    for i,p in enumerate(mesh.polygons): p.index=i
    mesh.edges.reverse(); mesh.attributes['crease_edge'].data.reverse()
    for i,e in enumerate(mesh.edges): e.index=i


class NativeProtocolTests(unittest.TestCase):
    def setUp(self):
        # Only the author-schedule verifier is substituted: these are transport
        # protocol mocks, not a false claim that a cube follows the panel schedule.
        # Production always calls the pinned panel schedule validator twice.
        self.schedule_patch = patch('hardsurface.structure_native._validate_schedule', return_value={'status':'host_mock_only'})
        self.schedule_mock = self.schedule_patch.start()
        self.addCleanup(self.schedule_patch.stop)

    def test_production_requires_author_schedule_before_mutation(self):
        from hardsurface.subd_panel_identity import validate_authored_identity
        self.schedule_mock.side_effect=validate_authored_identity
        obj,result=fixture()
        with self.assertRaises(RuntimeFailure) as error:write_authored_identity(obj,result,unit_scale=1.0)
        self.assertEqual(error.exception.code,'AUTHORED_SCHEDULE_UNSUPPORTED')
        self.assertNotIn(VERTEX_SLOT,obj.data.attributes)

    def test_pinned_schedule_validator_called_initial_and_actual(self):
        obj,result,report=written()
        self.assertGreaterEqual(self.schedule_mock.call_count,2)
        actual_call=self.schedule_mock.call_args
        self.assertEqual(actual_call.args[0]['vertices_mm'],report['witness']['mesh']['vertices'])
        self.assertEqual(actual_call.args[1]['vertex_map'],report['authorship']['vertex_map'])

    def fails(self, code, mutate, *, use_bound=False):
        obj,_,report = bound() if use_bound else written(); mutate(obj)
        with self.assertRaises(RuntimeFailure) as error:
            validate_native_structure(obj,unit_scale=1.0)
        self.assertEqual(error.exception.code,code)

    def test_closed_primitive_raw_witness_and_no_native_qualification(self):
        obj,_,report=written()
        self.assertEqual(report['status'],'pass')
        self.assertEqual(report['kernel_report']['counts']['actual_boundary_edges'],0)
        self.assertEqual(report['kernel_report']['counts']['quads'],6)
        self.assertIn('native_extraction', report['kernel_report']['not_checked'])
        self.assertEqual(report['kernel_report']['qualification'],'not_run')
        self.assertEqual(report['qualification'],'not_run')
        self.assertEqual(report['native_extraction']['signature_quantization'],'none')
        self.assertEqual(report['identity_binding_status'],'pending_registry')
        self.assertEqual(report['witness']['mesh']['vertices'][0][0],f32(-.002)*1000)
        self.assertNotEqual(report['witness']['mesh']['vertices'][0][0],-2.0)
        self.assertEqual(report['materialization']['policy'],'exact_ieee754_float32_authored_metres_v1')

    def test_legacy_missing_identity_never_auto_assigned(self):
        obj,_=fixture()
        with self.assertRaises(RuntimeFailure) as error: validate_native_structure(obj,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_IDENTITY_MISSING')
        self.assertFalse(has_native_identity(obj)); self.assertNotIn(MANIFEST,obj.data)

    def test_materialization_order_verified_before_slot_writes(self):
        for mutation in ('vertices','faces','winding','cardinality'):
            with self.subTest(mutation=mutation):
                obj,r=fixture()
                if mutation=='vertices': obj.data.vertices.reverse()
                elif mutation=='faces': obj.data.polygons.reverse()
                elif mutation=='winding': obj.data.polygons[0].vertices.reverse()
                else: obj.data.vertices.pop()
                with self.assertRaises(RuntimeFailure) as error: write_authored_identity(obj,r,unit_scale=1.0)
                self.assertEqual(error.exception.code,'STRUCTURE_MATERIALIZATION_ORDER')
                self.assertNotIn(VERTEX_SLOT,obj.data.attributes)

    def test_materialization_float32_exact_not_artistic_tolerance(self):
        obj,r=fixture(); obj.data.vertices[0].co[0] += 1e-12
        with self.assertRaises(RuntimeFailure) as error: write_authored_identity(obj,r,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_MATERIALIZATION_ORDER')

    def test_actual_reorder_carries_identity_and_source(self):
        obj,_,before=bound(); permute(obj)
        after=validate_native_structure(obj,unit_scale=1.0,expected_binding=before['binding'])
        self.assertEqual(before['binding'],after['binding'])
        for key in SIGNATURES: self.assertEqual(before['kernel_report'][key],after['kernel_report'][key])
        self.assertNotEqual(before['authorship']['vertex_map'],after['authorship']['vertex_map'])
        self.assertEqual(after['authorship']['vertex_map'],after['witness']['structure']['vertex_map'])
        self.assertEqual(after['authorship']['face_map'],after['witness']['structure']['face_map'])

    def test_reorder_without_vertex_identity_is_rejected(self):
        self.fails('STRUCTURE_GEOMETRY_DRIFT',lambda o:permute(o,carry_vertex_slots=False))

    def test_reorder_without_face_identity_is_rejected(self):
        self.fails('STRUCTURE_TOPOLOGY_DRIFT',lambda o:permute(o,carry_face_slots=False))

    def test_missing_id_attributes(self):
        for name in (VERTEX_SLOT,FACE_SLOT):
            with self.subTest(name=name): self.fails('STRUCTURE_IDENTITY_MISSING',lambda o:o.data.attributes.pop(name))

    def test_malformed_id_attribute_domains(self):
        for name in (VERTEX_SLOT,FACE_SLOT):
            for field,value in (('domain','EDGE'),('data_type','FLOAT')):
                with self.subTest(name=name,field=field): self.fails('STRUCTURE_ATTRIBUTE_INVALID',lambda o:setattr(o.data.attributes[name],field,value))

    def test_short_attribute_data(self):
        for name in (VERTEX_SLOT,FACE_SLOT,'crease_edge','hs_quad_surface_id'):
            with self.subTest(name=name): self.fails('STRUCTURE_ATTRIBUTE_INVALID',lambda o:o.data.attributes[name].data.pop())

    def test_duplicate_unknown_negative_boolean_slots(self):
        for name in (VERTEX_SLOT,FACE_SLOT):
            for value in ('duplicate',999,-1,True):
                with self.subTest(name=name,value=value):
                    self.fails('STRUCTURE_IDENTITY_INVALID',lambda o:setattr(o.data.attributes[name].data[0],'value',o.data.attributes[name].data[1].value if value=='duplicate' else value))

    def test_actual_loose_edge_is_not_silently_dropped(self):
        def mutate(o):
            o.data.edges.append(NS(index=len(o.data.edges),vertices=[0,6]))
            o.data.attributes['crease_edge'].data.append(NS(value=0.0))
        self.fails('STRUCTURE_ACTUAL_EDGES_INVALID',mutate)

    def test_actual_duplicate_or_missing_edge(self):
        def duplicate(o):
            o.data.edges.append(copy.deepcopy(o.data.edges[0]));o.data.attributes['crease_edge'].data.append(NS(value=0.0))
        def missing(o): o.data.edges.pop();o.data.attributes['crease_edge'].data.pop()
        self.fails('STRUCTURE_ACTUAL_EDGES_INVALID',duplicate);self.fails('STRUCTURE_ACTUAL_EDGES_INVALID',missing)

    def test_face_count_equal_connectivity_drift(self):
        self.fails('STRUCTURE_ACTUAL_EDGES_INVALID',lambda o:setattr(o.data.polygons[0],'vertices',[0,3,2,5]))

    def test_face_winding_drift(self):
        self.fails('STRUCTURE_TOPOLOGY_DRIFT',lambda o:o.data.polygons[0].vertices.reverse())

    def test_geometry_change_too_small_for_rounded_legacy_digest_still_fails(self):
        self.fails('STRUCTURE_GEOMETRY_DRIFT',lambda o:o.data.vertices[0].co.__setitem__(0,o.data.vertices[0].co[0]+1e-14))

    def test_missing_crease_attribute(self):
        self.fails('STRUCTURE_ATTRIBUTE_MISSING',lambda o:o.data.attributes.pop('crease_edge'))

    def test_crease_must_be_edge_float_and_finite_bounded(self):
        for field,value in (('domain','POINT'),('data_type','INT')):
            with self.subTest(field=field):self.fails('STRUCTURE_ATTRIBUTE_INVALID',lambda o:setattr(o.data.attributes['crease_edge'],field,value))
        for value in (float('nan'),float('inf'),-.001,1.001):
            with self.subTest(value=value):self.fails('STRUCTURE_CREASE_INVALID',lambda o:setattr(o.data.attributes['crease_edge'].data[0],'value',value))

    def test_crease_endpoint_weight_mismatch(self):
        self.fails('STRUCTURE_CREASE_MISMATCH',lambda o:setattr(o.data.attributes['crease_edge'].data[0],'value',.5))

    def test_initial_crease_uses_real_endpoints(self):
        obj,r=fixture();r['edge_creases']=[[0,1,1.0]]
        # Deliberately set a different actual edge rather than authored endpoints.
        obj.data.attributes['crease_edge'].data[-1].value=1.0
        with self.assertRaises(RuntimeFailure) as error:write_authored_identity(obj,r,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_CREASE_MISMATCH')

    def test_source_table_and_face_source_attribute_drift(self):
        self.fails('STRUCTURE_SOURCE_DRIFT',lambda o:o.__setitem__('hs_quad_surface_table',json.dumps(json.loads(o['hs_quad_surface_table'])[::-1])))
        self.fails('STRUCTURE_SOURCE_DRIFT',lambda o:setattr(o.data.attributes['hs_quad_surface_id'].data[0],'value',1))
        self.fails('STRUCTURE_SOURCE_DRIFT',lambda o:o.__setitem__('hs_quad_construction_sha256','b'*64))

    def test_matrix_or_modifier_drift(self):
        self.fails('STRUCTURE_CONTEXT_DRIFT',lambda o:o.matrix_world[0].__setitem__(3,.01))
        self.fails('STRUCTURE_EVALUATION_UNSUPPORTED',lambda o:o.modifiers.append(NS(type='BEVEL',name='unknown')))
        self.fails('STRUCTURE_CONTEXT_DRIFT',lambda o:o.modifiers.append(NS(type='SUBSURF',name='late',levels=2)))

    def test_subsurf_writable_fields_bound(self):
        obj,r=fixture();obj.modifiers=[NS(type='SUBSURF',name='mock',levels=2,render_levels=2,show_viewport=True)]
        write_authored_identity(obj,r,unit_scale=1.0);obj.modifiers[0].show_viewport=False
        with self.assertRaises(RuntimeFailure) as error:validate_native_structure(obj,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_CONTEXT_DRIFT')

    def test_shared_data_parent_keys_animation_editmode_rejected(self):
        for mutate in (lambda o:setattr(o.data,'users',2),lambda o:setattr(o,'parent',object()),lambda o:setattr(o.data,'shape_keys',object()),lambda o:setattr(o,'animation_data',object()),lambda o:setattr(o,'mode','EDIT')):
            self.fails('STRUCTURE_NATIVE_UNSUPPORTED',mutate)

    def test_units_explicit(self):
        obj,_,_=written()
        for scale in (.001,0,True,float('nan')):
            with self.subTest(scale=scale),self.assertRaises(RuntimeFailure) as error:validate_native_structure(obj,unit_scale=scale)
            self.assertEqual(error.exception.code,'STRUCTURE_UNITS_UNSUPPORTED')

    def test_table_sha_epoch_and_malformed_json_rejected(self):
        self.fails('STRUCTURE_METADATA_SHA_MISMATCH',lambda o:o.data.__setitem__(MANIFEST_SHA,'0'*64))
        self.fails('STRUCTURE_METADATA_INVALID',lambda o:o.data.__setitem__(MANIFEST,'{"same":1,"same":2}'))
        self.fails('STRUCTURE_METADATA_INVALID',lambda o:o.data.__setitem__(MANIFEST,'{"same":NaN}'))
        def mutate(o):
            value=json.loads(o.data[MANIFEST]);value['topology_epoch']=-1
            o.data[MANIFEST]=json.dumps(value);o.data[MANIFEST_SHA]=fingerprint(value)
        self.fails('STRUCTURE_METADATA_INVALID',mutate)

    def test_authored_hash_drift_rejected(self):
        self.fails('STRUCTURE_AUTHORSHIP_SHA_MISMATCH',lambda o:o.__setitem__(AUTHORED_SHA,'c'*64))
        obj,r=fixture();r['authorship_sha256']='d'*64
        with self.assertRaises(RuntimeFailure) as error:write_authored_identity(obj,r,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_AUTHORSHIP_SHA_MISMATCH')

    def test_refuse_repair_or_rebinding(self):
        obj,r,_=written()
        with self.assertRaises(RuntimeFailure) as error:write_authored_identity(obj,r,unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_IDENTITY_EXISTS')
        obj,_,report=bound()
        with self.assertRaises(RuntimeFailure) as error:bind_native_structure(obj,source_binding={'job_id':'different'},unit_scale=1.0)
        self.assertEqual(error.exception.code,'STRUCTURE_IDENTITY_REBIND_REFUSED')

    def test_bound_identity_and_expected_source_receipt(self):
        obj,_,report=bound()
        self.assertEqual(report['identity_binding_status'],'bound')
        self.assertEqual(validate_native_structure(obj,unit_scale=1.0,expected_binding=report['binding'])['status'],'pass')
        self.fails('STRUCTURE_IDENTITY_BINDING_DRIFT',lambda o:o.__setitem__('hs_object_id','different'),use_bound=True)
        self.fails('STRUCTURE_IDENTITY_BINDING_DRIFT',lambda o:o.data.__setitem__('hs_data_id','different'),use_bound=True)
        value=json.loads(obj.data[MANIFEST]);value['identity_binding']['source_binding']['source']['sha256']='f'*64
        obj.data[MANIFEST]=json.dumps(value);obj.data[MANIFEST_SHA]=fingerprint(value)
        with self.assertRaises(RuntimeFailure) as error:validate_native_structure(obj,unit_scale=1.0,expected_binding=report['binding'])
        self.assertEqual(error.exception.code,'STRUCTURE_REGISTRY_DRIFT')

    def test_changed_envelope_and_hash_together_cannot_match_external_registry(self):
        obj,_,report=bound();value=json.loads(obj.data[MANIFEST]);value['topology_epoch']+=1
        obj.data[MANIFEST]=json.dumps(value);obj.data[MANIFEST_SHA]=fingerprint(value)
        with self.assertRaises(RuntimeFailure) as error:validate_native_structure(obj,unit_scale=1.0,expected_binding=report['binding'])
        self.assertEqual(error.exception.code,'STRUCTURE_REGISTRY_DRIFT')

    def test_compact_report_excludes_large_arrays_and_declarations_distinct(self):
        _,_,report=written(); compact=compact_native_report(report)
        self.assertNotIn('witness',compact);self.assertNotIn('authorship',compact)
        self.assertIn('authored',compact);self.assertIn('native_extraction',compact)
        self.assertIn('source_preservation',compact['not_checked'])

    def test_roundtrip_mock_serialization_is_not_native_reopen_claim(self):
        obj,_,report=bound(); clone=copy.deepcopy(obj)
        reread=validate_native_structure(clone,unit_scale=1.0,expected_binding=report['binding'])
        self.assertEqual(reread['binding'],report['binding'])
        self.assertIn('saved_reopen',reread['not_checked'])


if __name__ == '__main__': unittest.main()
