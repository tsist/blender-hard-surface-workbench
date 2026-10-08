# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-authored pure-host tests. Never import Blender or a native test runner."""
import copy
import importlib.util
import math
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

from hardsurface.io import RuntimeFailure
from hardsurface.quad_geometry import build
from hardsurface import quad_panel_subd
from hardsurface.subd_panel_identity import (
    AuthoredPoint, AuthoredMeshBuilder, SCHEDULE_REVISION, fingerprint, authorship_payload,
    semantic_connectivity, validate_authored_identity, plan_parameter_edit,
    verify_parameter_edit, planar_aliases, resolve_alias, expected_control_loops,
)

BASE={'op':'quad.panel','id':'panel','topology_strategy':'subd_control_cage','size':[96,62],
      'corner_radius':9,'edge_bevel':.7,'center':[0,0],'z_min':0,'z_max':5.5,
      'holes':[{'id':'bore','kind':'circle','center':[11,1],'radius':8.5}],
      'local_patch_bounds':[-7,-15,25,17],'chord_tolerance':.05,
      'subdivision_cage':{'method':'CATMULL_CLARK','hole_segments':24,'preview_levels':2}}


def params(**changes):
    p=copy.deepcopy(BASE);p.update(changes);return p


def hole_params(x=11,y=1,radius=8.5):
    return params(holes=[{'id':'bore','kind':'circle','center':[x,y],'radius':radius}])


def reindex(mesh):
    """Storage permutation retains attached logical maps, never coordinate lookup."""
    result=copy.deepcopy(mesh);rng=random.Random(92117)
    vv=list(range(len(mesh['vertices_mm'])));ff=list(range(len(mesh['faces'])))
    rng.shuffle(vv);rng.shuffle(ff)
    vi={old:new for new,old in enumerate(vv)};fi={old:new for new,old in enumerate(ff)}
    result['vertices_mm']=[mesh['vertices_mm'][old] for old in vv]
    result['faces']=[[vi[v] for v in mesh['faces'][old]] for old in ff]
    result['face_provenance']=[mesh['face_provenance'][old] for old in ff]
    result['edge_creases']=[[vi[a],vi[b],v] for a,b,v in mesh['edge_creases']]
    result['authored_structure']['vertex_map']={s:vi[i] for s,i in mesh['authored_structure']['vertex_map'].items()}
    result['authored_structure']['face_map']={s:fi[i] for s,i in mesh['authored_structure']['face_map'].items()}
    return result


class AuthoredBuilderTests(unittest.TestCase):
    def test_authored_points_survive_copy_without_identity_recovery(self):
        point=AuthoredPoint((1.,2.),'grid/x:1/y:2');other=copy.deepcopy(point)
        self.assertEqual(point,other);self.assertEqual(point.semantic_id,other.semantic_id)

    def test_explicit_alias_reuses_logical_vertex(self):
        b=AuthoredMeshBuilder(aliases={'edge/end':'grid/corner'})
        self.assertEqual(b.vertex((0,0,0),semantic_id='grid/corner'),b.vertex((0,0,0),semantic_id='edge/end'))
        self.assertEqual(b.vertex_map,{'grid/corner':0})

    def test_alias_may_precede_canonical_vertex(self):
        b=AuthoredMeshBuilder(aliases={'end':'corner'})
        self.assertEqual(b.vertex((0,0,0),semantic_id='end'),b.vertex((0,0,0),semantic_id='corner'))
        self.assertEqual(set(b.vertex_map),{'corner'})

    def test_same_id_changed_coordinate_fails(self):
        b=AuthoredMeshBuilder();b.vertex((0,0,0),semantic_id='corner')
        with self.assertRaisesRegex(RuntimeFailure,'inconsistent'): b.vertex((.01,0,0),semantic_id='corner')

    def test_alias_mismatched_seam_fails(self):
        b=AuthoredMeshBuilder(aliases={'end':'corner'});b.vertex((0,0,0),semantic_id='corner')
        with self.assertRaises(RuntimeFailure):b.vertex((0,.01,0),semantic_id='end')

    def test_undeclared_exact_seam_fails(self):
        b=AuthoredMeshBuilder();b.vertex((0,0,0),semantic_id='one')
        with self.assertRaises(RuntimeFailure) as exc:b.vertex((0,0,0),semantic_id='two')
        self.assertEqual(exc.exception.code,'SEMANTIC_SEAM_UNDECLARED')

    def test_undeclared_near_seam_fails(self):
        b=AuthoredMeshBuilder();b.vertex((0,0,0),semantic_id='one')
        with self.assertRaises(RuntimeFailure):b.vertex((1e-10,0,0),semantic_id='two')

    def test_alias_cycles_fail(self):
        for aliases in ({'a':'a'},{'a':'b','b':'a'}):
            with self.subTest(aliases=aliases),self.assertRaises(RuntimeFailure):AuthoredMeshBuilder(aliases=aliases)

    def test_vertex_requires_author_id(self):
        with self.assertRaises(RuntimeFailure):AuthoredMeshBuilder().vertex((0,0,0))

    def test_duplicate_face_id_fails(self):
        b=AuthoredMeshBuilder()
        for i,p in enumerate(((0,0,0),(1,0,0),(1,1,0),(0,1,0),(2,0,0),(2,1,0))):b.vertex(p,semantic_id='logical:%d'%i)
        b.quad(0,1,2,3,semantic_id='cell:left')
        with self.assertRaises(RuntimeFailure):b.quad(1,4,5,2,semantic_id='cell:left')

    def test_loop_requires_exact_id_coverage(self):
        with self.assertRaises(RuntimeFailure):AuthoredMeshBuilder().loop([(0,0,0)]*4,semantic_ids=['a'])

    def test_loft_requires_exact_face_id_coverage(self):
        with self.assertRaises(RuntimeFailure):AuthoredMeshBuilder().loft([0,1,2,3],[4,5,6,7],face_ids=['f'])

    def test_logical_aliases_have_no_coordinates(self):
        aliases=planar_aliases()
        self.assertEqual(len(aliases),56)
        self.assertEqual(resolve_alias('fixed_frame/slot:0',aliases),'grid/x:2/y:2')
        self.assertEqual(resolve_alias('corner/side:0/center',aliases),'grid/x:6/y:0')


class AuthoredPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base=build(BASE,'test-feature')

    def test_cardinality_unique_ids_loops_and_repeatability(self):
        a=self.base['authored_structure']
        self.assertEqual((len(a['vertex_map']),len(a['face_map'])),(992,992))
        self.assertFalse(set(a['vertex_map'])&set(a['face_map']))
        self.assertEqual(a['semantic_control_loops'],expected_control_loops())
        self.assertEqual(len({r['role'] for r in a['semantic_control_loops']}),16)
        self.assertTrue(all(len(s)<=200 for s in [*a['vertex_map'],*a['face_map']]))
        self.assertEqual(self.base,build(BASE,'test-feature'))
        self.assertEqual(validate_authored_identity(self.base)['status'],'pass')

    def test_parameter_binding_is_exact_and_independently_hashed(self):
        a=self.base['authored_structure'];binding=a['parameter_binding']
        self.assertEqual(binding['parameters'],BASE)
        self.assertEqual(binding['design_sha256'],fingerprint(BASE))
        self.assertEqual(a['authorship_sha256'],fingerprint(authorship_payload(a)))
        p=params(edit_datum='fixed_bottom');b=build(p,'test-feature')
        self.assertEqual(b['construction_sha256'],self.base['construction_sha256'])
        self.assertNotEqual(b['authorship_sha256'],self.base['authorship_sha256'])

    def test_dependency_counts_and_explicit_datums(self):
        deps=self.base['authored_structure']['edit_dependencies']
        for key in ('holes[0].center[0]','holes[0].center[1]','holes[0].radius'):
            self.assertEqual(len(deps[key]['vertex_ids']),160)
        self.assertEqual(len(deps['z_range.fixed_bottom']['vertex_ids']),496)
        self.assertEqual(len(deps['z_range.fixed_midplane']['vertex_ids']),992)

    def test_array_reorder_preserves_authorship(self):
        b=reindex(self.base)
        self.assertNotEqual(b['vertices_mm'],self.base['vertices_mm'])
        self.assertEqual(validate_authored_identity(b)['status'],'pass')
        self.assertEqual(fingerprint(authorship_payload(b['authored_structure'])),self.base['authorship_sha256'])
        self.assertEqual(semantic_connectivity(b,b['authored_structure']),semantic_connectivity(self.base,self.base['authored_structure']))

    def test_construct_traversal_order_preserves_authorship(self):
        original=quad_panel_subd.plan_subd_cage
        def reverse_roles(p):
            plan=original(p)
            plan['roles']=[(name,list(reversed(faces))) for name,faces in reversed(plan['roles'])]
            plan['face_tokens']={name:list(reversed(tokens)) for name,tokens in plan['face_tokens'].items()}
            return plan
        with patch.object(quad_panel_subd,'plan_subd_cage',reverse_roles):b=build(BASE,'test-feature')
        self.assertNotEqual(self.base['vertices_mm'],b['vertices_mm'])
        self.assertEqual(self.base['authorship_sha256'],b['authorship_sha256'])
        self.assertEqual(validate_authored_identity(b)['status'],'pass')

    def test_alias_missing_even_with_new_hash_rejected(self):
        b=copy.deepcopy(self.base);a=b['authored_structure'];a['alias_manifest'].pop(next(iter(a['alias_manifest'])))
        a['authorship_sha256']=fingerprint(authorship_payload(a))
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_duplicate_map_index_rejected(self):
        for field in ('vertex_map','face_map'):
            b=copy.deepcopy(self.base);a=b['authored_structure'];keys=list(a[field]);a[field][keys[1]]=a[field][keys[0]]
            with self.subTest(field=field),self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_invalid_map_id_rejected(self):
        b=copy.deepcopy(self.base);a=b['authored_structure'];s=next(iter(a['vertex_map']));a['vertex_map']['x'*201]=a['vertex_map'].pop(s)
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_missing_id_rejected(self):
        b=copy.deepcopy(self.base);a=b['authored_structure'];a['vertex_map'].pop(next(iter(a['vertex_map'])))
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_changed_schedule_rejected(self):
        b=copy.deepcopy(self.base);b['authored_structure']['schedule_revision']=SCHEDULE_REVISION+'_other'
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_changed_connectivity_rejected(self):
        b=copy.deepcopy(self.base);b['faces'][0]=list(reversed(b['faces'][0]))
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_new_crease_rejected(self):
        b=copy.deepcopy(self.base);b['edge_creases'][0][2]=.5
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_removed_or_duplicate_loop_role_rejected(self):
        for mutate in ('remove','duplicate'):
            b=copy.deepcopy(self.base);a=b['authored_structure']
            if mutate=='remove':a['semantic_control_loops'].pop()
            else:a['semantic_control_loops'][1]['role']=a['semantic_control_loops'][0]['role']
            a['authorship_sha256']=fingerprint(authorship_payload(a))
            with self.subTest(mutate=mutate),self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_altered_dependency_set_with_new_hash_rejected(self):
        b=copy.deepcopy(self.base);a=b['authored_structure'];a['edit_dependencies']['holes[0].radius']['vertex_ids'].append('profile/upper_wall_guard/slot:0')
        a['authorship_sha256']=fingerprint(authorship_payload(a))
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_parameter_hash_mismatch_rejected(self):
        b=copy.deepcopy(self.base);b['authored_structure']['parameter_binding']['parameters']['z_max']=9
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_nonfinite_coordinate_rejected(self):
        b=copy.deepcopy(self.base);b['vertices_mm'][0]=(float('nan'),0,0)
        with self.assertRaises(RuntimeFailure):validate_authored_identity(b)

    def test_hole_xy_radius_and_combined_edits(self):
        for p in (hole_params(x=9),hole_params(y=2),hole_params(radius=7.8),hole_params(9,2,7.8),hole_params(12,0,9)):
            with self.subTest(hole=p['holes'][0]):
                contract=plan_parameter_edit(self.base,p);b=build(p,'test-feature')
                report=verify_parameter_edit(self.base,b,contract)
                self.assertEqual(report['status'],'pass');self.assertEqual(len(report['changed_vertex_ids']),160)
                self.assertEqual(self.base['faces'],b['faces'])
                self.assertEqual(len(report['lineage']['vertices']),992)

    def test_support_max_branch_changes_do_not_change_identity(self):
        p=hole_params(radius=7.8);p['edge_bevel']=.45
        a=build(p,'test-feature');q=copy.deepcopy(p);q['holes'][0]['radius']=7.0
        c=plan_parameter_edit(a,q);b=build(q,'test-feature')
        self.assertEqual(verify_parameter_edit(a,b,c)['status'],'pass')
        self.assertEqual(set(a['authored_structure']['vertex_map']),set(b['authored_structure']['vertex_map']))

    def test_fixed_bottom_growth_and_shrink_protect_all_xy_and_lower(self):
        for z in (6.5,4.5):
            p=params(z_max=z,edit_datum='fixed_bottom');c=plan_parameter_edit(self.base,p,datum='fixed_bottom');b=build(p,'test-feature')
            self.assertEqual(len(verify_parameter_edit(self.base,b,c)['changed_vertex_ids']),496)
            self.assertEqual([v[:2] for v in self.base['vertices_mm']],[v[:2] for v in b['vertices_mm']])

    def test_fixed_midplane_growth_and_shrink_protect_all_xy(self):
        for lo,hi in ((-.5,6.),(.5,5.)):
            p=params(z_min=lo,z_max=hi,edit_datum='fixed_midplane');c=plan_parameter_edit(self.base,p,datum='fixed_midplane');b=build(p,'test-feature')
            self.assertEqual(len(verify_parameter_edit(self.base,b,c)['changed_vertex_ids']),992)
            self.assertEqual([v[:2] for v in self.base['vertices_mm']],[v[:2] for v in b['vertices_mm']])
            self.assertEqual(lo+hi,BASE['z_min']+BASE['z_max'])

    def test_thickness_requires_explicit_and_matching_datum(self):
        for p,datum in ((params(z_max=6.5),None),(params(z_min=-1,z_max=6.5),'fixed_bottom'),(params(z_max=6.5),'fixed_midplane'),(params(z_max=6.5),'unknown')):
            with self.subTest(datum=datum),self.assertRaises(RuntimeFailure):plan_parameter_edit(self.base,p,datum=datum)

    def test_combined_hole_and_thickness(self):
        p=hole_params(9,2,7.8);p.update(z_max=6.5)
        c=plan_parameter_edit(self.base,p,datum='fixed_bottom');b=build(p,'test-feature')
        self.assertEqual(verify_parameter_edit(self.base,b,c)['status'],'pass')
        self.assertEqual(len(c['allowed_vertex_axes']),576)

    def test_edit_verification_accepts_reindexed_arrays(self):
        p=hole_params(x=9);c=plan_parameter_edit(self.base,p);b=reindex(build(p,'test-feature'))
        self.assertEqual(verify_parameter_edit(reindex(self.base),b,c)['status'],'pass')

    def test_protected_coordinate_change_fails(self):
        p=hole_params(x=9);c=plan_parameter_edit(self.base,p);b=build(p,'test-feature')
        s='profile/upper_wall_guard/slot:0';i=b['authored_structure']['vertex_map'][s];b['vertices_mm'][i]=tuple(x+.01 if k==0 else x for k,x in enumerate(b['vertices_mm'][i]))
        with self.assertRaises(RuntimeFailure) as exc:verify_parameter_edit(self.base,b,c)
        self.assertEqual(exc.exception.code,'AUTHORED_EDIT_OUTSIDE_SCOPE')

    def test_midplane_xy_change_fails(self):
        p=params(z_min=-.5,z_max=6.);c=plan_parameter_edit(self.base,p,datum='fixed_midplane');b=build(p,'test-feature')
        i=b['authored_structure']['vertex_map']['plane/bottom/bore/rim/slot:0'];v=b['vertices_mm'][i];b['vertices_mm'][i]=(v[0]+.01,v[1],v[2])
        with self.assertRaises(RuntimeFailure):verify_parameter_edit(self.base,b,c)

    def test_contract_write_set_expansion_fails(self):
        p=hole_params(x=9);c=plan_parameter_edit(self.base,p);b=build(p,'test-feature');c['allowed_vertex_axes']['profile/upper_wall_guard/slot:0']=[0,1,2]
        with self.assertRaises(RuntimeFailure):verify_parameter_edit(self.base,b,c)

    def test_invalid_new_parameter_values_rejected_before_contract(self):
        for p in (hole_params(radius=float('nan')),hole_params(radius=-1),hole_params(x=25),params(z_max=1)):
            with self.subTest(p=p),self.assertRaises((RuntimeFailure,ValueError)):plan_parameter_edit(self.base,p,datum='fixed_bottom')

    def test_nominal_scope_changes_rejected(self):
        changes=({'size':[100,62]},{'corner_radius':10},{'local_patch_bounds':[-8,-15,25,17]},
                 {'edge_bevel':.8},{'holes':[]},{'subdivision_cage':{'hole_segments':48}},
                 {'holes':[{'id':'bore','kind':'slot','center':[11,1],'radius':8.5}]},
                 {'holes':[BASE['holes'][0],BASE['holes'][0]]},{'center':[1,0]})
        for change in changes:
            with self.subTest(change=change),self.assertRaises(RuntimeFailure):plan_parameter_edit(self.base,params(**change))

    def test_legacy_five_fixture_outputs_match_frozen_source_exactly(self):
        import hashlib,json
        golden=json.loads((Path(__file__).resolve().parents[1]/'fixtures/subd-legacy-dev4-signatures.json').read_text())
        by_name={row['case']:row for row in golden['cases']}
        fixtures=[('neutral',BASE),('hole_move',hole_params(x=9)),('thickness',params(z_max=6.5)),
                  ('translated',params(center=[7,-4],holes=[{'id':'bore','kind':'circle','center':[18,-3],'radius':8.5}],local_patch_bounds=[0,-19,32,13])),
                  ('alternate',params(size=[104,68],corner_radius=10,edge_bevel=.8,z_max=6.4,holes=[{'id':'bore','kind':'circle','center':[8,0],'radius':7.5}],local_patch_bounds=[-10,-17,26,17]))]
        for name,p in fixtures:
            with self.subTest(name=name):
                after=build(p,'test-feature');expected=by_name[name]
                projection={key:after[key] for key in expected['legacy_keys']}
                observed=hashlib.sha256(json.dumps(projection,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                self.assertEqual(expected['legacy_complete_canonical_sha256'],observed)
                self.assertEqual(expected['construction_sha256'],after['construction_sha256'])


if __name__=='__main__':unittest.main()
