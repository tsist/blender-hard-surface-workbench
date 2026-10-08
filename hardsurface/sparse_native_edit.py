# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact, bounded source-graph insertion through the complete saved body.

The host preview predicts all affected ports and identities before rebuilding.
Only matching raw native before/after witnesses can verify the transaction.
Neither preview nor verification authorizes an edit or qualifies SubD shape.
"""
from copy import deepcopy
import struct

from . import structure_kernel as k
from .sparse_panel_geometry import build_sparse_panel, validate_sparse_authored_identity
from .subd_panel_identity import _canonical_cycle
from .sparse_parameter_edit import parameter_binding

VERSION='sparse-native-insertion/1.0'


def _fail(code,message,**details):
    raise k.StructureError(code,message,**details)


def is_insertion(before_authorship,new_parameters):
    if not isinstance(before_authorship,dict) or not isinstance(new_parameters,dict):return False
    if before_authorship.get('schedule_revision')!='sparse_sharp_panel_ids_v2':return False
    old=before_authorship['parameter_binding']['parameters']
    return old.get('sparse_cage',{}).get('insertions',[])!=new_parameters.get('sparse_cage',{}).get('insertions',[])


def _validate_change(old,new):
    a,b=deepcopy(old),deepcopy(new)
    a.pop('edit_datum',None);b.pop('edit_datum',None)
    ac=a.setdefault('sparse_cage',{});bc=b.setdefault('sparse_cage',{})
    before=ac.pop('insertions',[]);after=bc.pop('insertions',[])
    axis=(ac.get('insertion_policy')=='axis_plane_v1')
    valid=(isinstance(before,list) and isinstance(after,list) and a==b)
    if axis:
        layout=ac.get('layout',{})
        valid=(valid and layout.get('schema') in ('fixed-frame-axis-aligned/1.0','fixed-frame-axis-aligned/1.1')
               and len(after)==len(before)+1 and len(after)<=2 and after[:-1]==before)
    else:
        valid=(valid and not before and len(after)==1)
    if not valid:
        _fail('SPARSE_INSERTION_SCOPE','Insertion must append exactly one declaration within the existing policy; prior insertions, dimensions and policy must remain unchanged')
    return after[-1]


def _native_prediction(result):
    """Predict exactly the documented storage transform, without native claims."""
    from .structure_adapter import adapt_authored_mesh
    from .structure_native import _with_control_loops,DEFAULT_TOLERANCES
    actual=deepcopy(result)
    actual['vertices_mm']=[[struct.unpack('f',struct.pack('f',x*.001))[0]*1000.0 for x in v] for v in result['vertices_mm']]
    a=result['authored_structure']
    adapted=adapt_authored_mesh(actual,vertex_map=a['vertex_map'],face_map=a['face_map'],tolerances=deepcopy(DEFAULT_TOLERANCES))
    _with_control_loops(adapted,a)
    k.validate_structure(adapted['mesh'],adapted['structure'])
    return {'mesh':adapted['mesh'],'structure':adapted['structure']}


def _expanded_cycle(ids,midpoints):
    result=[]
    for a,b in zip(ids,ids[1:]+ids[:1]):
        result.append(a)
        mid=midpoints.get(tuple(sorted((a,b))))
        if mid:result.append(mid)
    return _canonical_cycle(result)


def _entity_mapping(before,after,transaction,base):
    """Use exact membership expansion; no coordinate/nearest-entity guessing."""
    inverse={i:s for s,i in base['sparse_graph']['vertex_map'].items()}
    mids={}
    axis=transaction['plan'].get('schema_version')=='sparse-axis-strip-insertion/1.0'
    for eid in transaction['plan']['split_edge_ids']:
        a,b=base['sparse_graph']['edges'][base['sparse_graph']['edge_map'][eid]]
        endpoints=[inverse[a],inverse[b]]
        if axis:
            if set(transaction.get('split_edge_endpoints',{}).get(eid,[]))!=set(endpoints):
                _fail('SPARSE_INSERTION_IDENTITY','Axis split ancestry differs from the immediate source edge',edge_id=eid)
            sid=transaction.get('split_edge_vertex_ids',{}).get(eid)
            if not sid or sid not in transaction['new_vertex_ids']:
                _fail('SPARSE_INSERTION_IDENTITY','Axis split requires an explicitly declared new vertex',edge_id=eid)
        else:sid='vertex:insert:'+k.fingerprint([transaction['plan']['corridor'],eid])[:40]
        mids[tuple(sorted(endpoints))]=sid
    mapping={};kinds={}
    for row in before['regions']:
        target_faces=sorted(c for sid in row['face_ids'] for c in transaction['face_mapping'][sid])
        choices=[r for r in after['regions'] if r['feature_id']==row['feature_id'] and r['role']==row['role'] and r['face_ids']==target_faces]
        if len(choices)!=1:_fail('SPARSE_INSERTION_IDENTITY','Region identity migration is ambiguous',region_id=row['id'])
        mapping[row['id']]=choices[0]['id'];kinds[row['id']]='regions'
    for row in before['loops']:
        target=_expanded_cycle(row['vertex_ids'],mids)
        choices=[r for r in after['loops'] if r['role']==row['role'] and _canonical_cycle(r['vertex_ids'])==target]
        if len(choices)!=1:_fail('SPARSE_INSERTION_IDENTITY','Loop identity migration is ambiguous',loop_id=row['id'])
        mapping[row['id']]=choices[0]['id'];kinds[row['id']]='loops'
    for row in before['boundary_ports']:
        choices=[r for r in after['boundary_ports'] if r['loop_id']==mapping[row['loop_id']] and r['region_id']==mapping[row['region_id']]]
        if len(choices)!=1:_fail('SPARSE_INSERTION_IDENTITY','Port identity migration is ambiguous',port_id=row['id'])
        mapping[row['id']]=choices[0]['id'];kinds[row['id']]='boundary_ports'
    target_ids={r['id'] for group in ('regions','loops','boundary_ports') for r in after[group]}
    created=[]
    if axis:
        loop=transaction.get('insertion_loop',{})
        choices=[r for r in after['loops'] if r['role']==loop.get('role')
                 and _canonical_cycle(r['vertex_ids'])==_canonical_cycle(loop.get('vertex_ids',[]))]
        if len(choices)!=1 or choices[0]['id'] in mapping.values():
            _fail('SPARSE_INSERTION_IDENTITY','Axis transaction must create exactly its declared semantic control loop')
        created=[choices[0]['id']]
    if len(set(mapping.values()))!=len(mapping) or set(mapping.values())|set(created)!=target_ids:
        _fail('SPARSE_INSERTION_IDENTITY','Structural identity migration must cover every old and new entity exactly')
    return mapping,kinds,mids,created


def plan_insertion(before_witness,before_authorship,new_parameters,*,datum_policy):
    from .structure_edit import _mesh,_verify_geometric_records
    if datum_policy not in ('not_requested','fixed_bottom','fixed_midplane'):
        _fail('AUTHORED_EDIT_DATUM_REQUIRED','Explicit datum policy required')
    if new_parameters.get('edit_datum',datum_policy)!=datum_policy:
        _fail('AUTHORED_EDIT_DATUM_MISMATCH','Parameter datum differs from requested policy')
    before,before_report=_mesh(before_witness,before_authorship)
    _verify_geometric_records(before_witness)
    old=before_authorship['parameter_binding']['parameters']
    declaration=_validate_change(old,new_parameters)
    owners={row['feature_id'] for row in before_witness['structure']['regions']}
    if len(owners)!=1:_fail('SPARSE_INSERTION_SCOPE','Sparse body must have one exact feature owner')
    owner=next(iter(owners))
    base=build_sparse_panel(old,feature_id=owner)
    desired=build_sparse_panel(new_parameters,feature_id=owner)
    transaction=desired['sparse_graph'].get('insertion_transaction')
    if (transaction is None or transaction['plan']['corridor']!=declaration['corridor']
            or transaction['plan']['fraction']!=declaration['fraction']):
        _fail('SPARSE_INSERTION_SCOPE','Constructor did not supply the declared whole-body transaction')
    if old.get('sparse_cage',{}).get('insertion_policy')=='axis_plane_v1':
        from .sparse_patch_graph import semantic_mesh
        if (transaction['plan'].get('schema_version')!='sparse-axis-strip-insertion/1.0'
                or transaction['plan'].get('source_topology_epoch')!=base['sparse_graph']['topology_epoch']
                or transaction['plan'].get('source_sha256')!=k.fingerprint(semantic_mesh(base))):
            _fail('SPARSE_INSERTION_STALE','Axis transaction must bind the exact immediate prefix graph')
    predicted=_native_prediction(desired)
    sa,sb=before_witness['structure'],predicted['structure']
    mapping,kinds,mids,created=_entity_mapping(sa,sb,transaction,base)
    before_records={r['id']:r for group in ('regions','loops','boundary_ports') for r in sa[group]}
    after_records={r['id']:r for group in ('regions','loops','boundary_ports') for r in sb[group]}
    affected_entities=sorted(key for key,target in mapping.items() if key!=target or before_records[key]!=after_records[target])
    affected_vertices=sorted({s for edge in mids for s in edge})
    affected_faces=sorted(transaction['retired_face_ids'])
    lineage=[]
    for sid in sorted(sa['vertex_map']):lineage.append({'relation':'continue','old':[sid],'new':[sid]})
    for sid in transaction['new_vertex_ids']:lineage.append({'relation':'create','old':[],'new':[sid]})
    for sid,children in sorted(transaction['face_mapping'].items()):
        lineage.append({'relation':'continue' if children==[sid] else 'split','old':[sid],'new':children})
    for sid,target in sorted(mapping.items()):
        if sid==target:lineage.append({'relation':'continue','old':[sid],'new':[sid]})
        else:
            lineage.extend([{'relation':'delete','old':[sid],'new':[]},{'relation':'create','old':[],'new':[target]}])
    lineage.extend({'relation':'create','old':[],'new':[sid]} for sid in created)
    contract={'topology_policy':'explicit_change',**{'before_'+key:before_report[key] for key in ('geometry_signature','topology_signature','attribute_signature','structure_signature')},
              'affected_vertex_ids':affected_vertices,'affected_face_ids':affected_faces,
              'affected_entity_ids':affected_entities,'lineage':lineage}
    k.verify_edit(before_witness['mesh'],sa,predicted['mesh'],sb,contract)
    predicted_report=k.validate_structure(predicted['mesh'],predicted['structure'])
    result={'schema_version':VERSION,'schedule_revision':before_authorship['schedule_revision'],
            'datum_policy':datum_policy,'expected_parameter_binding':parameter_binding(new_parameters),
            'before_authorship_sha256':before_authorship['authorship_sha256'],
            'kernel_contract':contract,'host_transaction':transaction,
            'structural_identity_migration':[{'kind':kinds[s],'old_id':s,'new_id':target,
                'relation':'continue' if s==target else 'explicit_reidentification_with_stale_selection_invalidation'} for s,target in sorted(mapping.items())],
            'expected_native_witness_sha256':k.fingerprint(predicted),
            'expected_native_signatures':{key:predicted_report[key] for key in ('geometry_signature','topology_signature','attribute_signature','structure_signature')},
            'port_changes':deepcopy(transaction['plan']['port_changes']),
            'whole_body_face_delta':transaction['plan']['added_faces'],
            'source_original_vertices':'must_remain_exactly_unchanged',
            'dependent_selections':'invalidate_and_re_resolve','qualification':'not_run'}
    if created:
        result['created_structural_entities']=[{'kind':'loops','new_id':sid,'relation':'create'} for sid in created]
    result['plan_sha256']=k.fingerprint(result)
    return result


def verify_insertion(before_report,after_report,edit_plan):
    from .structure_edit import _report,_verify_geometric_records
    before,bk=_report(before_report,'Before');after,ak=_report(after_report,'After')
    if not isinstance(edit_plan,dict) or edit_plan.get('schema_version')!=VERSION:
        _fail('SPARSE_INSERTION_PLAN','Exact versioned insertion plan required')
    expected=plan_insertion(before_report['witness'],before_report['authorship'],edit_plan['expected_parameter_binding']['parameters'],datum_policy=edit_plan['datum_policy'])
    if expected!=edit_plan:_fail('SPARSE_INSERTION_STALE','Predeclared insertion plan differs from current source')
    if after_report['authorship']['parameter_binding']!=edit_plan['expected_parameter_binding']:
        _fail('SPARSE_INSERTION_PARAMETERS','After state does not bind the requested insertion')
    ba,bb=before_report['binding'],after_report['binding']
    for key in ('schema_version','object_id','data_id','mesh_state','schedule_revision','context_sha256'):
        if ba[key]!=bb[key]:_fail('SPARSE_INSERTION_CONTEXT','Native identity or evaluation context changed',field=key)
    if bb['topology_epoch']!=ba['topology_epoch']+1:
        _fail('SPARSE_INSERTION_EPOCH','A new complete topology requires exactly one epoch increment')
    if any(ak[key]!=value for key,value in edit_plan['expected_native_signatures'].items()):
        _fail('SPARSE_INSERTION_WITNESS','Actual complete native body differs from preflight native-storage prediction')
    va,vb=before_report['authorship']['vertex_map'],after_report['authorship']['vertex_map']
    if any(before['vertices_mm'][i]!=after['vertices_mm'][vb[s]] for s,i in va.items()):
        _fail('SPARSE_INSERTION_ORIGINAL_MOVED','Insertion changed an original control vertex')
    _verify_geometric_records(after_report['witness'])
    proof=k.verify_edit(before_report['witness']['mesh'],before_report['witness']['structure'],
                        after_report['witness']['mesh'],after_report['witness']['structure'],edit_plan['kernel_contract'])
    result={'schema_version':VERSION,'status':'pass','kernel_edit':proof,
            'before_binding':deepcopy(ba),'after_binding':deepcopy(bb),
            'plan_sha256':edit_plan['plan_sha256'],'port_changes':deepcopy(edit_plan['port_changes']),
            'whole_body_face_delta':edit_plan['whole_body_face_delta'],
            'structural_identity_migration':deepcopy(edit_plan['structural_identity_migration']),
            'original_control_vertices':'exactly_preserved','complete_body':'verified',
            'source_preservation':'host_required','saved_reopen':'host_required',
            'dependent_selections':'invalidate_and_re_resolve','qualification':'not_run'}
    if edit_plan.get('created_structural_entities'):
        result['created_structural_entities']=deepcopy(edit_plan['created_structural_entities'])
    return result
