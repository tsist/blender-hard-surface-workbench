# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned caller-attested reference freeze; this is not a chat authentication service."""
from __future__ import annotations
import re
from .io import RuntimeFailure, read_json, verify_descriptor

SHA=re.compile(r'^[0-9a-f]{64}$')

def fail(message,code='REFERENCE_APPROVAL_REQUIRED'):
    raise RuntimeFailure(code,message)

def verified_json(value):
    before=verify_descriptor(value)
    content=read_json(value['file'],max_bytes=1024*1024,_reference=before)
    if verify_descriptor(value)!=before:fail('Reference evidence changed while reading','REFERENCE_CONFLICT')
    return content,before

def approval_descriptor(path,sha):
    if path is None and sha is None:return None
    if path is None or not isinstance(sha,str) or not SHA.fullmatch(sha):
        fail('Both external approval file and independently verified SHA256 are required')
    return {'file':str(path),'expected_sha256':sha}

def verify_reference(params,approval):
    if params['purpose']!='production':
        if approval is not None:fail('Fixture requests cannot consume production approval evidence','REFERENCE_CONFLICT')
        return {'status':'not_applicable','reason':'contract_fixture_only'}
    if not isinstance(approval,dict) or set(approval)!={'file','expected_sha256'} or not isinstance(approval.get('expected_sha256'),str) or not SHA.fullmatch(approval['expected_sha256']):
        fail('Production requires a separately pinned external approval record descriptor')
    evidence,record=verified_json(approval)
    required={'receipt_version','approved','manifest_sha256','approval_reference','dimensions_sha256','checklist_sha256','unresolved_conflicts','source'}
    if not isinstance(evidence,dict) or set(evidence)!=required or evidence['receipt_version']!='1.0' or evidence['approved'] is not True:
        fail('External approval record is incomplete; an approved flag alone is not evidence')
    source=evidence['source']
    if not isinstance(source,dict) or set(source)!={'channel','author','room_id','message_id','quote'} or source['channel'] not in ('chatgpt','slack') or source['author']!='user' or any(not isinstance(source[k],str) or not source[k] or len(source[k])>16000 for k in source):
        fail('Approval requires the verified user source channel, role, exact message identity and quote')
    if source['message_id']!=evidence['approval_reference']:fail('Approval source identity differs','REFERENCE_CONFLICT')
    for key in ('manifest_sha256','dimensions_sha256','checklist_sha256'):
        if not isinstance(evidence[key],str) or not SHA.fullmatch(evidence[key]):fail('Invalid approval SHA256: '+key)
    if evidence['unresolved_conflicts']!=[]:fail('Reference conflicts are unresolved or unknown','REFERENCE_CONFLICT')
    descriptor=params['reference_package']['manifest']
    if evidence['manifest_sha256']!=descriptor['expected_sha256']:fail('Approval manifest differs from request','REFERENCE_CONFLICT')
    manifest,manifest_record=verified_json(descriptor)
    if not isinstance(manifest,dict) or manifest.get('unresolved_conflicts')!=[] or manifest.get('approval_reference')!=source['message_id'] or manifest.get('approval_quote')!=source['quote']:
        fail('Manifest approval source or conflict state differs','REFERENCE_CONFLICT')
    if manifest.get('version')!=params['reference_package']['version']:fail('Requested reference version differs from frozen manifest','REFERENCE_CONFLICT')
    references=manifest.get('references');checklist=manifest.get('checklist')
    if not isinstance(references,list) or not 1<=len(references)<=256 or not isinstance(checklist,dict):fail('Frozen manifest lacks bounded reference files and checklist','REFERENCE_CONFLICT')
    rows=[];seen=set()
    for ref in [*references,checklist]:
        if not isinstance(ref,dict) or set(ref)!={'file','sha256','bytes','role'} or not isinstance(ref['role'],str) or not ref['role'] or not isinstance(ref['sha256'],str) or not SHA.fullmatch(ref['sha256']) or type(ref['bytes']) is not int or not 0<=ref['bytes']<=256*1024*1024:
            fail('Invalid frozen reference descriptor','REFERENCE_CONFLICT')
        if ref['file'] in seen:fail('Duplicate frozen reference file','REFERENCE_CONFLICT')
        seen.add(ref['file'])
        rows.append({**verify_descriptor(ref,expected=False),'role':ref['role']})
    dimensions=[row for row in rows if row['role']=='authoritative_dimension_spec']
    if len(dimensions)!=1 or dimensions[0]['sha256']!=evidence['dimensions_sha256'] or checklist['sha256']!=evidence['checklist_sha256']:
        fail('Approval dimensions or checklist differs from real referenced bytes','REFERENCE_CONFLICT')
    checklist_data,_=verified_json({'file':checklist['file'],'expected_sha256':checklist['sha256'],'bytes':checklist['bytes']})
    if not isinstance(checklist_data,dict) or checklist_data.get('approval_message')!=source['message_id'] or checklist_data.get('before_modeling') is not True:
        fail('Checklist does not identify this pre-modeling approval','REFERENCE_CONFLICT')
    requirements={item.get('id') for item in checklist_data.get('items',[]) if isinstance(item,dict)}
    if not set(params['reference_package']['requirements'])<=requirements:fail('Requested requirements are absent from frozen checklist','REFERENCE_CONFLICT')
    return {'status':'pass','manifest_sha256':evidence['manifest_sha256'],'approval_reference':source['message_id'],'dimensions_sha256':evidence['dimensions_sha256'],'checklist_sha256':evidence['checklist_sha256'],'approval_record':{k:record[k] for k in ('sha256','bytes')},'source':source,'manifest':manifest_record,'reference_files':rows,'trust_boundary':'Caller verified the source user message and supplied the approval SHA out of band; local validation proves content binding, not remote message authentication'}
