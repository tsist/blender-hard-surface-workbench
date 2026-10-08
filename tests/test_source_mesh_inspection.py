# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent neutral host fixtures. These do not qualify any Blender asset."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from hardsurface import source_mesh_inspection as m
from hardsurface.io import RuntimeFailure
from hardsurface.source_mesh_inspection_native import build_request_for_construct, _output


def edges_for(faces):
    return [list(e) for e in sorted({tuple(sorted((a,b))) for face in faces for a,b in zip(face,face[1:]+face[:1])})]


def fixture(vertices, faces, groups, loops=()):
    return {'vertices_mm':vertices, 'edges':edges_for(faces), 'faces':faces,
            'vertex_map':{'vertex:%d'%i:i for i in range(len(vertices))},
            'face_map':{'face:%d'%i:i for i in range(len(faces))},
            'face_provenance':[{'feature_id':'neutral_fixture','surface_id':group+'/patch',
                                'region_group':group,'support_band':False,'curved':False} for group in groups],
            'semantic_control_loops':list(loops)}


def cube():
    # Explicit outward winding, not constructor-generated production parameters.
    v=[[-2,-2,-2],[2,-2,-2],[2,2,-2],[-2,2,-2],[-2,-2,2],[2,-2,2],[2,2,2],[-2,2,2]]
    f=[[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    return fixture(v,f,['bottom','top','side','side','side','side'])


def square_tube():
    # Four square rings, flat outward top/bottom and inward aperture wall.
    v=[[-3,-3,0],[3,-3,0],[3,3,0],[-3,3,0],
       [-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0],
       [-3,-3,2],[3,-3,2],[3,3,2],[-3,3,2],
       [-1,-1,2],[1,-1,2],[1,1,2],[-1,1,2]]
    f=[[0,1,9,8],[1,2,10,9],[2,3,11,10],[3,0,8,11],
       [5,4,12,13],[6,5,13,14],[7,6,14,15],[4,7,15,12],
       [1,0,4,5],[2,1,5,6],[3,2,6,7],[0,3,7,4],
       [8,9,13,12],[9,10,14,13],[10,11,15,14],[11,8,12,15]]
    loops=[{'role':'top.aperture','vertex_ids':['vertex:%d'%i for i in (12,13,14,15)],'expected_valence':4}]
    return fixture(v,f,['side']*4+['holewall']*4+['bottom']*4+['top']*4,loops)


def grid():
    # Independent 3 x 3 planar quads; topology is read rather than repaired.
    v=[[x,y,0] for y in range(4) for x in range(4)]
    f=[[0,1,5,4],[1,2,6,5],[2,3,7,6], [4,5,9,8],[5,6,10,9],[6,7,11,10], [8,9,13,12],[9,10,14,13],[10,11,15,14]]
    return fixture(v,f,['top']*9)


def inspect(data, **kwargs):
    return m.analyze_mesh(data['vertices_mm'],data['edges'],data['faces'],
        vertex_map=data['vertex_map'],face_map=data['face_map'],face_provenance=data['face_provenance'],
        semantic_control_loops=data['semantic_control_loops'], **kwargs)


class ActualGraphTests(unittest.TestCase):
    def test_cube_true_winding_valences_and_roles(self):
        d=cube(); before=copy.deepcopy(d); r=inspect(d)
        self.assertEqual(d,before)
        self.assertEqual(r['counts'],{'vertices':8,'edges':12,'faces':6,'face_sides':{4:6}})
        self.assertEqual(r['valence_histogram'],{3:8})
        self.assertTrue(r['quality']['passed'],r['quality']['findings'])
        self.assertEqual(r['checks']['self_intersections']['status'],'not_run')
        self.assertEqual(r['checks']['outward_orientation'],'not_run')
        self.assertEqual(r['checks']['native_extraction'],'not_run')
        self.assertEqual(r['regular_paths'],[])
        self.assertEqual(r['regions']['whole']['boundary']['ordered_loops'],[])
        self.assertEqual(r['regions']['top']['face_indices'],[1])
        self.assertEqual(r['regions']['holewall']['status'],'absent')
        self.assertEqual(r['regions']['whole']['face_count'],sum(r['regions'][k]['face_count'] for k in m.REGIONS[1:]))
        self.assertAlmostEqual(r['regions']['whole']['area_mm2'],96)
        self.assertEqual(len(r['face_metrics']),6)
        self.assertEqual(r['preservation']['before_sha256'],r['preservation']['after_sha256'])
        json.dumps(r,allow_nan=False)

    def test_ordered_boundary_retains_source_winding(self):
        r=inspect(square_tube()); loops=r['regions']['top']['boundary']['ordered_loops']
        self.assertEqual(len(loops),2)
        vertex_sets={tuple(row['vertex_indices']) for row in loops}
        self.assertIn((8,9,10,11),vertex_sets)
        self.assertIn((13,12,15,14),vertex_sets)
        self.assertTrue(all(row['kind']=='ordered_boundary_loop' for row in loops))

    def test_regular_loop_requires_opposites_not_merely_closed(self):
        d=square_tube(); r=inspect(d)
        self.assertEqual(r['valence_histogram'],{4:16})
        self.assertTrue(r['quality']['passed'],r['quality']['findings'])
        loop=r['declared_chains'][0]
        self.assertEqual(loop['kind'],'regular_edge_loop')
        self.assertEqual(loop['nonregular_continuation_vertex_indices'],[])
        self.assertTrue(r['regular_paths'])
        # A face boundary turns at regular valence-four vertices: closed but not a loop.
        d['semantic_control_loops']=[{'role':'turning','vertex_ids':['vertex:%d'%i for i in (8,9,13,12)],'expected_valence':None}]
        turning=inspect(d)['declared_chains'][0]
        self.assertEqual(turning['kind'],'ordered_closed_edge_chain')
        self.assertEqual(len(turning['nonregular_continuation_vertex_indices']),4)
        self.assertEqual(turning['pole_vertex_indices'],[])

    def test_pole_crossing_chain_never_advertised_slideable(self):
        d=cube(); d['semantic_control_loops']=[{'role':'top_boundary','vertex_ids':['vertex:%d'%i for i in (4,5,6,7)],'expected_valence':None}]
        r=inspect(d); chain=r['declared_chains'][0]
        self.assertEqual(chain['kind'],'pole_crossing_closed_chain')
        self.assertEqual(chain['pole_vertex_indices'],[4,5,6,7])
        self.assertIsNone(chain['declared_regular_loop_verified'])
        self.assertEqual(r['checks']['declared_regular_loops'],'pass')
        d['semantic_control_loops'][0]['expected_valence']=4
        self.assertEqual(inspect(d)['checks']['declared_regular_loops'],'fail')

    def test_quad_strips_reference_real_opposite_edges_and_faces(self):
        d=square_tube(); r=inspect(d)
        for row in r['quad_strips']:
            self.assertEqual(row['status'],'pass')
            pairs=list(zip(row['cross_edge_indices'],row['cross_edge_indices'][1:]+(row['cross_edge_indices'][:1] if row['closed'] else [])))
            self.assertEqual(len(pairs),len(row['face_indices']))
            for (a,b),fi in zip(pairs,row['face_indices']):
                face=d['faces'][fi]
                local=[tuple(sorted((x,y))) for x,y in zip(face,face[1:]+face[:1])]
                ai=local.index(tuple(sorted(d['edges'][a]))); bi=local.index(tuple(sorted(d['edges'][b])))
                self.assertEqual((ai-bi)%4,2)

    def test_open_regular_chains_stop_at_boundary(self):
        r=inspect(grid(),require_closed=False)
        self.assertTrue(r['quality']['passed'])
        paths=r['regular_paths']; self.assertTrue(paths)
        self.assertTrue(all(row['kind']=='regular_edge_chain' for row in paths))
        self.assertTrue(all(not row['closed'] for row in paths))
        self.assertEqual(len(r['regions']['whole']['boundary']['ordered_loops'][0]['edge_indices']),12)

    def test_reverse_one_face_is_not_silently_repaired(self):
        d=cube(); d['faces'][1].reverse(); r=inspect(d)
        self.assertFalse(r['quality']['passed'])
        self.assertIn('INCONSISTENT_EDGE_ORIENTATION',r['quality']['finding_counts'])
        self.assertEqual(r['actual_mesh']['faces'][1],d['faces'][1])

    def test_all_five_partition_roles_are_ledgered_without_coordinate_guessing(self):
        d=cube()
        # Neutral fixture semantic partitions deliberately test mapping, not shape.
        d['face_provenance'][2].update(region_group='holewall',surface_id='holewall/fixture')
        d['face_provenance'][3].update(region_group='outer_roundover',surface_id='outer_roundover/fixture')
        r=inspect(d)
        self.assertTrue(all(r['regions'][g]['status']=='present' for g in m.REGIONS))
        self.assertEqual(sum(r['regions'][g]['face_count'] for g in m.REGIONS[1:]),6)

    def test_reordered_actual_edge_array_retains_original_index_identity(self):
        d=square_tube();d['edges'].reverse();r=inspect(d)
        self.assertEqual(r['actual_mesh']['edges'],d['edges'])
        for chain in r['declared_chains']:
            vids=chain['vertex_indices']
            self.assertEqual([set(d['edges'][ei]) for ei in chain['edge_indices']],
                             [set(pair) for pair in zip(vids,vids[1:]+vids[:1])])

    def test_distortion_reports_actual_stretched_and_bowtie_faces(self):
        d=grid(); d['vertices_mm'][5][2]=.5; r=inspect(d,require_closed=False)
        self.assertGreater(max(row['quad_warpage_degrees'] or 0 for row in r['face_metrics']),0)
        v=[[0,0,0],[2,2,0],[0,2,0],[2,0,0]]; d=fixture(v,[[0,1,2,3]],['top'])
        r=inspect(d,require_closed=False)
        self.assertFalse(r['quality']['passed'])
        self.assertTrue(r['face_metrics'][0]['self_crossing'])


class InvalidInputTests(unittest.TestCase):
    def test_invalid_indices_numbers_and_ids_fail_closed(self):
        mutations=[lambda d:d['faces'][0].__setitem__(0,-1), lambda d:d['faces'][0].__setitem__(0,True),
                   lambda d:d['edges'][0].__setitem__(1,500),lambda d:d['vertices_mm'][0].__setitem__(0,float('nan')),
                   lambda d:d['vertices_mm'][0].__setitem__(0,float('inf')),
                   lambda d:d['vertex_map'].__setitem__('vertex:0',1),
                   lambda d:d['face_map'].__setitem__('face:0',True),
                   lambda d:d['face_provenance'][0].pop('region_group'),
                   lambda d:d['face_provenance'][0].__setitem__('feature_id',''),
                   lambda d:d['face_provenance'][0].__setitem__('surface_id','bad\nrole'),
                   lambda d:d['face_provenance'][0].__setitem__('support_band',1),
                   lambda d:d['edges'].append(d['edges'][0]),lambda d:d['edges'].pop()]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                d=cube(); mutate(d)
                with self.assertRaises(RuntimeFailure):inspect(d)

    def test_missing_semantic_edge_or_vertex_rejected(self):
        for ids in ([0,2,5],[0,1,99]):
            d=cube();d['semantic_control_loops']=[{'role':'broken','vertex_ids':['vertex:%d'%i for i in ids]}]
            with self.assertRaises(RuntimeFailure):inspect(d)

    def test_input_mutation_detected(self):
        d=cube(); actual=m.validate_mesh
        def mutate(*args,**kwargs):
            d['vertices_mm'][0][0] += 1
            return actual(*args,**kwargs)
        with patch.object(m,'validate_mesh',mutate):
            with self.assertRaises(RuntimeFailure):inspect(d)


class SvgAndRequestTests(unittest.TestCase):
    def test_svg_contains_actual_ids_hash_and_no_fabricated_hidden_surface(self):
        r=inspect(square_tube()); svg=m.render_svg(r,{'name':'isolated-top','region':'top','direction':'top'})
        root=ET.fromstring(svg); ns={'s':'http://www.w3.org/2000/svg'}
        self.assertEqual(len(root.findall('.//s:line',ns)),len(r['regions']['top']['edge_indices']))
        self.assertEqual(len(root.findall('.//s:g[@data-face-index]',ns)),4)
        metadata=json.loads(root.find('s:metadata',ns).text)
        self.assertEqual(metadata['mesh_sha256'],r['mesh_sha256'])
        self.assertEqual(metadata['render_engine'],'none; direct SVG projection')
        self.assertEqual(metadata['evidence_origin'],'host_fixture')
        self.assertNotIn('<image',svg)
        self.assertNotIn('<polygon',svg)
        self.assertIn('actual array indices',svg)
        self.assertIn('E',svg);self.assertIn('F',svg)

    def test_svg_rejects_hash_mismatch(self):
        r=inspect(cube());r['actual_mesh']['vertices_mm'][0][0]=99
        with self.assertRaises(RuntimeFailure):m.render_svg(r,m.DEFAULT_VIEWS[0])

    def test_svg_refuses_tampered_region_membership_and_finite_but_invalid_arrays(self):
        r=inspect(square_tube());r['regions']['top']['face_indices']=[0]
        with self.assertRaises(RuntimeFailure):m.render_svg(r,{'name':'bad','region':'top','direction':'top'})
        r=inspect(cube());r['actual_mesh']['faces'][0][0]=-1
        r['mesh_sha256']=m.fingerprint(r['actual_mesh'])
        with self.assertRaises(RuntimeFailure):m.render_svg(r,m.DEFAULT_VIEWS[0])

    def test_absent_region_svg_is_honest(self):
        svg=m.render_svg(inspect(cube()),{'name':'absent','region':'outer_roundover','direction':'three_quarter'})
        self.assertIn('Region absent',svg)

    def test_request_schema_defaults_and_unknown_fields(self):
        req={'schema_version':'1.0','command':'hardsurface.mesh.inspect','params':{'request_id':'host.fixture','source':{'file':'/tmp/neutral.blend','expected_sha256':'0'*64}}}
        out=m.validate_request(req)
        self.assertEqual(out['params']['max_tree_rss_bytes'],1024**3)
        self.assertEqual(len(out['params']['views']),7)
        self.assertFalse(out['params']['self_intersections'])
        self.assertTrue(all(v['width']<=2000 and v['height']<=2000 for v in out['params']['views']))
        self.assertEqual(json.loads((Path(__file__).parents[1]/'schemas/hardsurface-mesh-inspect.schema.json').read_text()),m.schema())
        req['params']['beauty_render']=True
        with self.assertRaises(c.ContractError):m.validate_request(req)

    def test_construct_helper_uses_only_work_unit_features_and_exact_saved_sha(self):
        req={'params':{'request_id':'construct.fixture','budgets':{'cpu_threads':2,'wall_seconds':400},'work_units':[{'feature_ids':['wanted']}],
                       'design':{'state':{'features':[{'id':'wanted'},{'id':'unrelated'}]}}}}
        candidate={'file':'/tmp/saved.blend','sha256':'a'*64,'bytes':100}
        result=build_request_for_construct(req,candidate)
        self.assertEqual(result['params']['feature_ids'],['wanted'])
        self.assertEqual(result['params']['source']['expected_sha256'],'a'*64)
        self.assertFalse(result['params']['self_intersections'])

    def test_output_exclusive_creation_and_path_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            written=[0]; ref=_output(tmp,'mesh-inspect-fixture.json',b'{}\n',written)
            self.assertEqual(ref['bytes'],3)
            with self.assertRaises(RuntimeFailure):_output(tmp,'mesh-inspect-fixture.json',b'changed',written)
            with self.assertRaises(RuntimeFailure):_output(tmp,'../anything.json',b'{}',written)
            self.assertEqual(Path(ref['file']).read_bytes(),b'{}\n')


class NativeBridgeProtocolTests(unittest.TestCase):
    """Real semantic transport on a neutral mock, never a Blender qualification."""
    def setUp(self):
        self.mock_schedule=patch('hardsurface.structure_native._validate_schedule',return_value={'status':'host_fixture_only'})
        self.mock_schedule.start();self.addCleanup(self.mock_schedule.stop)

    def prepared(self):
        from test_structure_native_contract import fixture
        from hardsurface.structure_native import write_authored_identity, bind_native_structure
        obj,result=fixture()
        for i,row in enumerate(result['face_provenance']):
            row['region_group']='bottom' if i==0 else 'top' if i==1 else 'side'
            row['feature_id']='neutral_fixture'
        obj['hs_quad_surface_table']=json.dumps(result['face_provenance'])
        obj['hs_feature_id']='neutral_fixture'
        obj['hs_object_id']='11111111-1111-4111-8111-111111111111'
        obj.data['hs_data_id']='22222222-2222-4222-8222-222222222222'
        write_authored_identity(obj,result,unit_scale=1.0)
        binding=bind_native_structure(obj,source_binding={'source_sha256':'a'*64,'fixture':'neutral host'},unit_scale=1.0)
        return obj,binding

    def test_native_bridge_uses_raw_domains_and_preserves_every_hash(self):
        from hardsurface.source_mesh_inspection_native import inspect_object
        obj,binding=self.prepared()
        r=inspect_object(obj,expected_binding=binding,evidence_origin='host_fixture')
        self.assertEqual(r['checks']['native_extraction'],'protocol_mock_pass')
        self.assertEqual(r['evidence_origin'],'host_fixture')
        self.assertEqual(r['actual_mesh']['faces'],[list(p.vertices) for p in obj.data.polygons])
        self.assertEqual(r['actual_mesh']['edges'],[list(e.vertices) for e in obj.data.edges])
        self.assertEqual(r['actual_mesh']['vertices_mm'],[[float(x)*1000 for x in v.co] for v in obj.data.vertices])
        self.assertNotEqual(r['actual_mesh']['vertices_mm'][0][0],-2.)
        self.assertEqual(r['native_preservation']['before_sha256'],r['native_preservation']['after_sha256'])
        self.assertTrue(r['external_registry_compared'])
        self.assertEqual(len(r['actual_edge_creases']),12)

    def test_native_corrupt_slot_provenance_identity_and_geometry_rejected(self):
        from hardsurface.source_mesh_inspection_native import inspect_object
        from hardsurface.structure_native import VERTEX_SLOT
        mutations=[lambda o:o.data.attributes[VERTEX_SLOT].data[0].__setattr__('value',999),
                   lambda o:o.data.attributes['hs_quad_surface_id'].data[0].__setattr__('value',999),
                   lambda o:o.data.vertices[0].co.__setitem__(0,float('nan')),
                   lambda o:o.__setitem__('hs_object_id','invalid-id'),
                   lambda o:o.data.polygons[0].vertices.__setitem__(0,-1),
                   lambda o:o.data.edges.pop()]
        for mutate in mutations:
            obj,binding=self.prepared();mutate(obj)
            with self.assertRaises(RuntimeFailure):inspect_object(obj,expected_binding=binding,evidence_origin='host_fixture')

    def test_external_binding_drift_rejected(self):
        from hardsurface.source_mesh_inspection_native import inspect_object
        obj,binding=self.prepared();binding['authorship_sha256']='f'*64
        with self.assertRaises(RuntimeFailure):inspect_object(obj,expected_binding=binding,evidence_origin='host_fixture')

    def test_native_mutation_during_analysis_rejected(self):
        from hardsurface import source_mesh_inspection_native as n
        obj,binding=self.prepared();original=n.analyze_mesh
        def mutate(*args,**kwargs):
            report=original(*args,**kwargs)
            obj.data.vertices[0].co[0]+=.001
            return report
        with patch.object(n,'analyze_mesh',mutate):
            with self.assertRaises(RuntimeFailure):n.inspect_object(obj,expected_binding=binding,evidence_origin='host_fixture')


# Import last so tests establish the public module independently of bpy.
from hardsurface import contract as c
if __name__=='__main__':unittest.main()
