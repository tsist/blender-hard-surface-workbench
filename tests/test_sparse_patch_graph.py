# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral host fixtures. No Blender, project design or approval assertions."""
import copy
import math
import random
import struct
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface.quad_quality import validate_mesh
from hardsurface.self_intersection import audit_pairs, spatial_candidates
from hardsurface.sparse_panel_geometry import (build_sparse_panel, validate_parameter_domain,
    validate_sparse_authored_identity, SCHEDULE_REVISION)
from hardsurface.sparse_patch_graph import (SparseMeshBuilder, validate_sparse_mesh,
    plan_strip_insertion, apply_strip_insertion, semantic_mesh, fingerprint, edge_id)


BASE = {'op': 'quad.panel', 'id': 'neutral_plate', 'topology_strategy': 'sparse_control_cage',
    'size': [120, 84], 'center': [0, 0], 'corner_radius': 11, 'edge_bevel': 1.5,
    'z_min': -4, 'z_max': 4, 'holes': [{'id': 'bore', 'kind': 'circle', 'center': [-10, 3], 'radius': 12}]}


def parameters(**changes):
    result = copy.deepcopy(BASE); result.update(changes); return result


def host_quality(mesh):
    return validate_mesh([[v*.001 for v in p] for p in mesh['vertices_mm']], mesh['faces'],
                         face_provenance=mesh['face_provenance'])


def host_intersections(mesh):
    triangles, polygons = [], []
    for i, (a, b, c, d) in enumerate(mesh['faces']):
        triangles.extend(([a, b, c], [a, c, d])); polygons.extend((i, i))
    return audit_pairs(mesh['vertices_mm'], triangles, polygons,
                       spatial_candidates(mesh['vertices_mm'], triangles)[0])


class SparseBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_sparse_panel(BASE, 'neutral-feature')

    def test_whole_body_counts_are_not_top_only(self):
        r = validate_sparse_mesh(self.base)
        self.assertEqual((r['vertices'], r['edges'], r['faces']), (622, 1244, 622))
        self.assertEqual(r['region_face_counts'], {'top': 127, 'bottom': 127, 'holewall': 16, 'outer_roundover': 256, 'side': 96})
        self.assertEqual((r['components'], r['boundary_edges'], r['euler']), (1, 0, 0))
        self.assertGreater(r['signed_volume_mm3'], 0)

    def test_ports_bind_frames_owners_and_opposite_face_sides(self):
        for port in self.base['sparse_graph']['ports']:
            self.assertEqual(port['port_schema_version'],'sparse-port/1.0')
            self.assertEqual(port['owner_feature_id'],'neutral-feature')
            self.assertEqual(port['edge_count'],len(port['vertex_ids']))
            self.assertEqual(len(port['directed_face_sides']),port['edge_count'])
            self.assertEqual(port['anchor_vertex_id'],port['vertex_ids'][0])
        for key,value in [('owner_feature_id','foreign'),('edge_count',31),('anchor_vertex_id','unknown'),('local_frame',{}),('directed_face_sides',[])]:
            m=copy.deepcopy(self.base);m['sparse_graph']['ports'][0][key]=value
            with self.subTest(field=key),self.assertRaises(RuntimeFailure):validate_sparse_mesh(m)

    def test_conditional_ab_retains_necessary_hole_ring(self):
        m = build_sparse_panel(parameters(sparse_cage={'hole_planar_support': True}))
        self.assertEqual(len(m['faces']), 654)
        self.assertEqual(m['metadata']['subdivision_cage']['region_face_counts']['top'], 143)
        self.assertTrue(host_quality(m)['passed'])
        self.assertEqual(len([x for x in m['sparse_graph']['ports'] if x['role'].endswith('hole_support')]), 2)

    def test_nonminimal_profile_density_is_supported(self):
        for n in (2, 3, 5, 8):
            with self.subTest(n=n):
                m = build_sparse_panel(parameters(sparse_cage={'profile_arc_segments': n}))
                self.assertEqual(len(m['faces']), 270+32*(2*n+3))

    def test_native_and_evaluated_status_remain_not_run(self):
        evidence = self.base['metadata']['subdivision_cage']['evidence']
        self.assertEqual(evidence['host_graph'], 'pass')
        self.assertTrue(all(evidence[k] == 'not_run' for k in evidence if k != 'host_graph'))
        self.assertEqual(self.base['subdivision_modifier']['levels'], 0)
        self.assertIsNone(self.base['metadata']['subdivision_cage']['acceptance_tolerance_mm'])

    def test_sharp_whole_rims_and_smooth_outer_profiles(self):
        graph = self.base['sparse_graph']
        self.assertEqual(len(self.base['edge_creases']), 32)
        for p in graph['ports']:
            self.assertEqual(p['crease'], 1.0 if p['role'].endswith('hole_rim') else 0.0)
            self.assertEqual(len(p['vertex_ids']), 16 if '.hole_' in p['role'] else 32)

    def test_pole_chains_are_not_mislabeled_regular_loops(self):
        loops = {row['role']: row for row in self.base['authored_structure']['semantic_control_loops']}
        for layer in ('top', 'bottom'):
            self.assertIsNone(loops[layer+'.hole_collar']['expected_valence'])
            self.assertIsNone(loops[layer+'.outer_core']['expected_valence'])
            self.assertEqual(loops[layer+'.hole_rim']['expected_valence'], 4)

    def test_legacy_quad_quality_policy_passes_unmodified(self):
        report = host_quality(self.base)
        self.assertTrue(report['passed'], report['finding_counts'])
        self.assertLess(report['metrics']['maximum_quad_warpage_degrees'], 1e-8)

    def test_neutral_body_has_no_detected_host_triangle_intersections(self):
        report = host_intersections(self.base)
        self.assertEqual(report['status'], 'pass', report['findings'])
        self.assertGreater(report['exact_pairs_checked'], 0)

    def test_repeatable_construction_and_hash(self):
        self.assertEqual(self.base, build_sparse_panel(BASE, 'neutral-feature'))

    def test_hole_position_and_radius_preserve_all_exterior_geometry(self):
        p = parameters(); p['holes'][0]['center'] = [-6, 1]; p['holes'][0]['radius'] = 13
        result = build_sparse_panel(p)
        before, after = semantic_mesh(self.base), semantic_mesh(result)
        self.assertEqual(before['connectivity'], after['connectivity'])
        exterior = [s for s in before['coordinates'] if s.startswith('vertex:profile.') or '.outer_' in s]
        self.assertTrue(exterior)
        self.assertTrue(all(before['coordinates'][s] == after['coordinates'][s] for s in exterior))

    def test_roundover_edit_protects_actual_hole_region(self):
        result = build_sparse_panel(parameters(edge_bevel=1.1))
        before, after = semantic_mesh(self.base), semantic_mesh(result)
        protected = [s for s in before['coordinates'] if '/hole_' in s or '/grid/' in s]
        self.assertTrue(all(before['coordinates'][s] == after['coordinates'][s] for s in protected))
        self.assertEqual(before['connectivity'], after['connectivity'])
        self.assertTrue(host_quality(result)['passed'])

    def test_centered_thickness_edit_preserves_xy(self):
        result = build_sparse_panel(parameters(z_min=-5, z_max=5))
        a, b = semantic_mesh(self.base), semantic_mesh(result)
        self.assertTrue(all(a['coordinates'][s][:2] == b['coordinates'][s][:2] for s in a['coordinates']))
        self.assertEqual(a['connectivity'], b['connectivity'])

    def test_global_translation_keeps_connectivity(self):
        p = parameters(center=[7, -9], z_min=16, z_max=24)
        p['holes'][0]['center'] = [-3, -6]
        result = build_sparse_panel(p)
        a, b = semantic_mesh(self.base), semantic_mesh(result)
        self.assertEqual(a['connectivity'], b['connectivity'])
        for sid, xyz in a['coordinates'].items():
            for actual, expected in zip(b['coordinates'][sid], [xyz[0]+7, xyz[1]-9, xyz[2]+20]):
                self.assertAlmostEqual(actual, expected, places=12)

    def test_arbitrary_counts_are_rejected_not_silently_substituted(self):
        for cfg in ({'hole_segments': 24}, {'outer_segments': 48}, {'profile_arc_segments': 1}, {'preview_levels': 4}):
            with self.subTest(cfg=cfg), self.assertRaises(RuntimeFailure):
                build_sparse_panel(parameters(sparse_cage=cfg))

    def test_domain_conflicts_fail_closed(self):
        bad = [parameters(edge_bevel=4), parameters(z_max=-5), parameters(size=[0, 84]),
               parameters(holes=[]), parameters(sparse_cage={'invented_option': True})]
        p = parameters(); p['holes'][0]['center'] = [40, 25]; bad.append(p)
        p = parameters(); p['holes'][0]['radius'] = math.nan; bad.append(p)
        for p in bad:
            with self.subTest(p=p), self.assertRaises(RuntimeFailure): build_sparse_panel(p)

    def test_domain_validator_does_not_change_request(self):
        p = parameters(); old = copy.deepcopy(p)
        self.assertEqual(validate_parameter_domain(p)['status'], 'supported_candidate_domain')
        self.assertEqual(p, old)

    def test_legacy_strategy_does_not_silently_migrate(self):
        with self.assertRaises(RuntimeFailure):
            build_sparse_panel(parameters(topology_strategy='subd_control_cage'))


class SparseIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.base = build_sparse_panel(BASE)

    def test_builder_uses_same_identity_not_coordinate_welding(self):
        b = SparseMeshBuilder(); i = b.vertex('vertex:a', [0, 0, 0])
        self.assertEqual(i, b.vertex('vertex:a', [0, 0, 0]))
        j = b.vertex('vertex:b', [0, 0, 0]); self.assertNotEqual(i, j)
        with self.assertRaises(RuntimeFailure): b.vertex('vertex:a', [0, .01, 0])

    def test_complete_native_compatible_manifest(self):
        m = self.base['authored_structure']
        self.assertEqual(m['schedule_revision'], SCHEDULE_REVISION)
        self.assertEqual(m['parameter_binding']['parameters'], BASE)
        self.assertEqual(validate_sparse_authored_identity(self.base)['status'], 'pass')

    def test_native_float32_transport_exact_membership(self):
        m = copy.deepcopy(self.base); m.pop('sparse_graph')
        m['vertices_mm'] = [[struct.unpack('f', struct.pack('f', x*.001))[0]*1000 for x in p] for p in m['vertices_mm']]
        self.assertEqual(validate_sparse_authored_identity(m)['status'], 'pass')
        m['vertices_mm'][0][0] = math.nextafter(m['vertices_mm'][0][0], math.inf)
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)

    def test_storage_permutation_preserves_logical_identity(self):
        m = copy.deepcopy(self.base); m.pop('sparse_graph')
        rng = random.Random(23); vv = list(range(len(m['vertices_mm']))); ff = list(range(len(m['faces'])))
        rng.shuffle(vv); rng.shuffle(ff); vm = {old: new for new, old in enumerate(vv)}; fm = {old: new for new, old in enumerate(ff)}
        m['vertices_mm'] = [m['vertices_mm'][i] for i in vv]
        m['faces'] = [[vm[v] for v in m['faces'][i]] for i in ff]
        m['face_provenance'] = [m['face_provenance'][i] for i in ff]
        m['edge_creases'] = [[vm[a], vm[b], w] for a, b, w in m['edge_creases']]
        m['authored_structure']['vertex_map'] = {s: vm[i] for s, i in m['authored_structure']['vertex_map'].items()}
        m['authored_structure']['face_map'] = {s: fm[i] for s, i in m['authored_structure']['face_map'].items()}
        self.assertEqual(validate_sparse_authored_identity(m)['status'], 'pass')

    def test_arbitrary_id_index_zip_is_rejected(self):
        m = copy.deepcopy(self.base); m.pop('sparse_graph'); vm = m['authored_structure']['vertex_map']
        a, b = list(vm)[:2]; vm[a], vm[b] = vm[b], vm[a]
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)

    def test_changed_bound_coordinates_rejected(self):
        m = copy.deepcopy(self.base); m['vertices_mm'][0][0] += .0001
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)

    def test_winding_and_open_seam_rejected(self):
        m = copy.deepcopy(self.base); m['faces'][0].reverse()
        with self.assertRaises(RuntimeFailure): validate_sparse_mesh(m)
        m = copy.deepcopy(self.base); m['faces'][0][0] = m['faces'][1][2]
        with self.assertRaises(RuntimeFailure): validate_sparse_mesh(m)

    def test_port_order_and_arity_tampering_rejected(self):
        m = copy.deepcopy(self.base); m['sparse_graph']['ports'][0]['vertex_ids'].reverse()
        with self.assertRaises(RuntimeFailure): validate_sparse_mesh(m)
        m = copy.deepcopy(self.base); m['sparse_graph']['ports'][0]['vertex_ids'].pop()
        with self.assertRaises(RuntimeFailure): validate_sparse_mesh(m)

    def test_crease_and_provenance_tampering_rejected(self):
        m = copy.deepcopy(self.base); m['edge_creases'][0][2] = .8
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)

    def test_native_boolean_indices_and_weights_rejected(self):
        m = copy.deepcopy(self.base); m.pop('sparse_graph'); m['faces'][0][0] = False
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)
        m = copy.deepcopy(self.base); m.pop('sparse_graph'); m['edge_creases'][0][2] = True
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)
        m = copy.deepcopy(self.base); m['face_provenance'][0]['support_band'] = True
        with self.assertRaises(RuntimeFailure): validate_sparse_authored_identity(m)


class SparseInsertionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.base = build_sparse_panel(BASE)

    def test_whole_body_propagation_counts_and_quality(self):
        for corridor, delta, top in [('east', 44, 138), ('south', 48, 140)]:
            with self.subTest(corridor=corridor):
                plan = plan_strip_insertion(self.base, corridor)
                result = apply_strip_insertion(self.base, plan)
                self.assertEqual(plan['added_faces'], delta)
                self.assertEqual(len(result['faces']), 622+delta)
                self.assertEqual(result['metadata']['subdivision_cage']['region_face_counts']['top'], top)
                self.assertTrue(all(row['before'] == 32 and row['after'] == 34 for row in plan['port_changes']))
                self.assertEqual(len(plan['port_changes']), 16)
                self.assertTrue(all(len(p['vertex_ids']) == 16 for p in result['sparse_graph']['ports'] if '.hole_' in p['role']))
                self.assertTrue(host_quality(result)['passed'])
                self.assertEqual(host_intersections(result)['status'], 'pass')

    def test_old_vertices_preserved_and_mapping_is_explicit(self):
        old = copy.deepcopy(self.base); plan = plan_strip_insertion(old, 'east'); result = apply_strip_insertion(old, plan)
        self.assertEqual(old, self.base)
        tx = result['sparse_graph']['insertion_transaction']; a, b = semantic_mesh(old), semantic_mesh(result)
        self.assertTrue(all(a['coordinates'][sid] == b['coordinates'][sid] for sid in a['coordinates']))
        self.assertEqual(len(tx['new_vertex_ids']), 44)
        self.assertEqual(len(tx['retired_face_ids']), 44)
        self.assertEqual(len(tx['new_face_ids']), 88)
        self.assertEqual(set(tx['face_mapping']), set(old['sparse_graph']['face_map']))
        self.assertEqual(set(tx['edge_mapping']), set(old['sparse_graph']['edge_map']))
        self.assertTrue(all(set(ids) <= set(result['sparse_graph']['edge_map']) for ids in tx['edge_mapping'].values()))

    def test_stale_or_modified_plan_cannot_commit(self):
        plan = plan_strip_insertion(self.base, 'east'); plan['added_faces'] += 1
        with self.assertRaises(RuntimeFailure): apply_strip_insertion(self.base, plan)
        plan = plan_strip_insertion(self.base, 'east'); altered = copy.deepcopy(self.base); altered['vertices_mm'][0][0] += .01
        with self.assertRaises(RuntimeFailure): apply_strip_insertion(altered, plan)

    def test_domain_rejects_unknown_corridor_repeat_or_unsafe_fraction(self):
        for c, t in [('west', .5), ('east', .1), ('south', .9)]:
            with self.subTest(c=c, t=t), self.assertRaises(RuntimeFailure): plan_strip_insertion(self.base, c, fraction=t)
        result = apply_strip_insertion(self.base, plan_strip_insertion(self.base, 'east'))
        with self.assertRaises(RuntimeFailure): plan_strip_insertion(result, 'south')


if __name__ == '__main__':
    unittest.main()
