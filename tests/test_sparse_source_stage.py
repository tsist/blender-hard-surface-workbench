# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-stage admission and synthetic receipt faults; no Blender is invoked."""
from copy import deepcopy
import json
import struct
from pathlib import Path
import tempfile
import unittest

from test_contract import minimal_request
from test_sparse_pipeline_integration import parameters,materialized
from hardsurface import contract as c
from hardsurface.planner import plan_request
from hardsurface.io import descriptor,RuntimeFailure,checkpoint_evidence_artifacts
from hardsurface.source_mesh_inspection import render_svg,DEFAULT_VIEWS,VIEW
from hardsurface.source_mesh_inspection_native import inspect_object
from hardsurface.source_inspection_evidence import verify_construction_inspection


def source_request():
    r=minimal_request();p=r['params']
    p['quality'].update(stage='source_cage',required=['closed_mesh','preservation','reopen','structure_identity'])
    p['work_units'][0]['checks']=['closed_mesh','preservation','reopen','structure_identity']
    p['design']['state']['features'][0]['program']['steps']=[parameters()]
    return r


class SparseSourceStageContractTests(unittest.TestCase):
    def test_source_stage_is_explicit_narrow_and_readable(self):
        plan=plan_request(source_request())
        self.assertEqual(plan['request']['params']['quality']['stage'],'source_cage')
        self.assertIn('quad_topology',{r['id'] for r in plan['required_checks']})
        self.assertEqual(plan['steps'][0]['geometry_estimate']['faces'],622)

    def test_no_implicit_full_or_early_evaluation(self):
        for mutation in ('full','level','design_check','rendered_wire','legacy_config','legacy_route'):
            r=source_request();p=r['params'];step=p['design']['state']['features'][0]['program']['steps'][0]
            if mutation=='full':p['quality']['stage']='full'
            elif mutation=='level':step['sparse_cage']['preview_levels']=2
            elif mutation=='design_check':p['quality']['required'].append('design_dimensions')
            elif mutation=='rendered_wire':p['wire']['enabled']=True
            elif mutation=='legacy_config':step['subdivision_cage']={}
            else:step['topology_strategy']='tiled'
            with self.subTest(mutation=mutation),self.assertRaises(Exception):plan_request(r)

    def test_bound_insertion_patch_only_changes_declared_existing_step(self):
        state=c.validate_state(source_request()['params']['design']['state']);f=state['features'][0]
        command={'mode':'patch_if_revision','expected_revision':1,'state_sha256':c.fingerprint(state),
                 'patches':[{'op':'insert_sparse_strip','feature_id':f['id'],'step_id':'panel',
                             'expected_feature_sha256':c.fingerprint(f),'corridor':'east','fraction':.5}]}
        changed=c.resolve_design(command,state)
        self.assertEqual(changed['revision'],2)
        self.assertNotIn('insertions',state['features'][0]['program']['steps'][0]['sparse_cage'])
        self.assertEqual(changed['features'][0]['program']['steps'][0]['sparse_cage']['insertions'],[{'corridor':'east','fraction':.5}])
        command['patches'][0]['expected_feature_sha256']='0'*64
        with self.assertRaises(c.ContractError):c.resolve_design(command,state)

    def test_source_receipt_cannot_claim_full_delivery(self):
        from test_contract import ReceiptTests
        full=ReceiptTests().sample()
        full['qualification_scope']={'stage':'source_cage','source_structure':'pass','evaluated_shape':'not_run','surface_observation':'not_run','production_qualification':'not_run'}
        full['acceptance']['performance']={'status':'pass','evidence':full['report']}
        with self.assertRaises(c.ContractError):c.compact_report(full)
        full['domain_outcome']='partial';full['candidate']['state']='verified_candidate'
        self.assertEqual(c.compact_report(full)['domain_outcome'],'partial')
        full['acceptance']['visual']={'status':'pass','evidence':full['report']}
        with self.assertRaises(c.ContractError):c.compact_report(full)

    def test_source_success_still_requires_technical_protection_and_no_error(self):
        from test_contract import ReceiptTests
        for mode in ('technical','guard','error','performance'):
            full=ReceiptTests().sample();full['domain_outcome']='partial';full['candidate']['state']='verified_candidate'
            full['acceptance']['performance']={'status':'pass','evidence':full['report']}
            full['qualification_scope']={'stage':'source_cage','source_structure':'pass','evaluated_shape':'not_run','surface_observation':'not_run','production_qualification':'not_run'}
            if mode=='technical':full['acceptance']['technical']={'status':'not_run'}
            elif mode=='guard':full['source_protection']['staged_snapshot_guarded']['accepted']=False
            elif mode=='performance':full['acceptance']['performance']={'status':'not_run'}
            else:full['error']={'code':'SOURCE_CHANGED','message':'Synthetic failure','retry_class':'never'}
            with self.subTest(mode=mode),self.assertRaises(c.ContractError):c.compact_report(full)

    def test_late_source_failure_keeps_original_serializable_error(self):
        from test_contract import ReceiptTests
        from hardsurface.host import close_qualification_scope
        full=ReceiptTests().sample();full.update(status='failed',domain_outcome='failed',candidate=None)
        full['error']={'code':'SOURCE_CHANGED','message':'Synthetic late guard failure','retry_class':'never'}
        full['qualification_scope']={'stage':'source_cage','source_structure':'pass','evaluated_shape':'not_run','surface_observation':'not_run','production_qualification':'not_run'}
        result=c.compact_report(close_qualification_scope(full))
        self.assertEqual(result['error']['code'],'SOURCE_CHANGED')
        self.assertNotIn('qualification_scope',result)


class SourceInspectionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This is a protocol mock of trusted native producers. It tests receipt
        # comparisons only; statuses below never count as actual native evidence.
        cls.obj,cls.native=materialized(parameters())
        cls.detail=inspect_object(cls.obj,expected_binding=cls.native['binding'],evidence_origin='blender_raw_object_data')

    def fixture(self):
        root=Path(tempfile.mkdtemp(prefix='source-evidence-host-fixture-'))
        candidate=root/'candidate.blend';candidate.write_bytes(b'host-protocol-fixture-not-a-real-blend')
        ref=descriptor(candidate);oid=self.native['binding']['object_id'];detail=deepcopy(self.detail);detail['source_sha256']=ref['sha256'];detail['source_semantics']='SAVED_DISK_V1'
        path=root/'evidence.json';path.write_text(json.dumps(detail));dref=descriptor(path)
        outputs=[{**dref,'kind':'source_mesh_inspection','object_id':oid,'mesh_state':'control'}];views=[]
        for i,v in enumerate(DEFAULT_VIEWS):
            view=c._validate(v,VIEW);path=root/('%02d.svg'%i);path.write_text(render_svg(detail,view))
            row={**descriptor(path),'kind':'actual_source_wire_svg','object_id':oid,'view':view,
                 'source_sha256':ref['sha256'],'mesh_sha256':detail['mesh_sha256']};views.append(row);outputs.append(row)
        summary={'identity':detail['identity'],'evidence':dref,'views':views,'native_binding':self.native['binding'],
                 'native_preservation':detail['native_preservation'],**{k:detail[k] for k in ('counts','mesh_sha256','semantic_sha256')}}
        inspection={'operation':'hardsurface.mesh.inspect','source_sha256':ref['sha256'],'source':ref,'source_after':ref,'opened_source':ref,
                    'source_semantics':'SAVED_DISK_V1','acceptance':{'inspection_execution':'pass','preservation':'pass','polygon_quality':'pass'},
                    'objects':[summary],'outputs':outputs,'render_engine_used':None,
                    **{k:False for k in ('saved_candidate_modified','source_geometry_modified','source_transforms_modified','blend_save_performed','modifiers_evaluated')}}
        report={'candidate':ref,'checks':{'structure_identity':{'evidence':[{'object_id':oid}]}},'source_mesh_inspection':inspection}
        metres=[[struct.unpack('f',struct.pack('f',x*.001))[0] for x in v] for v in detail['actual_mesh']['vertices_mm']]
        quality={'passed':True,'identity':{'object_id':oid,'feature_id':'neutral'},'mesh_state':'control',
                 'actual_structure':{'binding':self.native['binding']},'topology_sha256':c.fingerprint(detail['actual_mesh']['faces']),
                 'geometry_sha256':c.fingerprint(metres),
                 'self_intersections':{'status':'pass','triangles':2*detail['counts']['faces'],'findings':[]}}
        return root,report,{oid:self.native},{oid:quality}

    def test_complete_reproducible_evidence_passes_protocol(self):
        root,report,native,quality=self.fixture()
        self.assertEqual(len(verify_construction_inspection(report,root=root,native_reports=native,control_quality=quality)),8)

    def test_missing_detail_wrong_source_unknown_object_and_incomplete_views_reject(self):
        for mode in ('svg_only','wrong_sha','unknown_object','missing_view','no_intersections'):
            root,report,native,quality=self.fixture();inspection=report['source_mesh_inspection']
            if mode=='svg_only':inspection['outputs']=inspection['outputs'][1:]
            elif mode=='wrong_sha':inspection['outputs'][1]['source_sha256']='0'*64
            elif mode=='unknown_object':inspection['outputs'][0]['object_id']='foreign'
            elif mode=='missing_view':inspection['outputs'].pop();inspection['objects'][0]['views'].pop()
            else:quality={}
            with self.subTest(mode=mode),self.assertRaises(RuntimeFailure):verify_construction_inspection(report,root=root,native_reports=native,control_quality=quality)

    def test_failed_native_detail_cannot_be_relabelled_by_summary(self):
        for mode in ('native','quality','preservation','mesh','poles','regular_paths','svg'):
            root,report,native,quality=self.fixture();inspection=report['source_mesh_inspection'];item=inspection['outputs'][0]
            value=json.loads(Path(item['file']).read_text())
            if mode=='native':value['checks']['native_extraction']='not_run'
            elif mode=='quality':value['quality']['passed']=False
            elif mode=='preservation':value['native_preservation']['status']='fail'
            elif mode=='mesh':value['actual_mesh']['vertices_mm'][0][0]+=.1
            elif mode=='poles':
                for row in value['vertices']:row.update(pole=False,edge_valence=4)
            elif mode=='regular_paths':value['regular_paths']=[]
            else:
                row=inspection['outputs'][1];p=root/'wrong.svg';p.write_text('<svg/>');row.update(descriptor(p))
            if mode!='svg':
                p=root/'changed.json';p.write_text(json.dumps(value));ref=descriptor(p);item.update(ref);inspection['objects'][0]['evidence']=ref
            with self.subTest(mode=mode),self.assertRaises(RuntimeFailure):verify_construction_inspection(report,root=root,native_reports=native,control_quality=quality)

    def test_unbound_svg_only_checkpoint_rejected(self):
        with self.assertRaises(RuntimeFailure):checkpoint_evidence_artifacts({'candidate':{'sha256':'a'*64},'source_mesh_inspection':{'operation':'hardsurface.mesh.inspect'}},root=Path('/tmp'))

    def test_foreign_audit_and_stale_native_signatures_reject(self):
        for mode in ('foreign_object','foreign_geometry','foreign_topology','stale_native'):
            root,report,native,quality=self.fixture();native=deepcopy(native);quality=deepcopy(quality);oid=next(iter(native))
            if mode=='foreign_object':quality[oid]['identity']['object_id']='foreign'
            elif mode=='foreign_geometry':quality[oid]['geometry_sha256']='0'*64
            elif mode=='foreign_topology':quality[oid]['topology_sha256']='0'*64
            else:native[oid]['witness']['mesh']['vertices'][0][0]+=.1
            with self.subTest(mode=mode),self.assertRaises(Exception):verify_construction_inspection(report,root=root,native_reports=native,control_quality=quality)


if __name__=='__main__':unittest.main()
