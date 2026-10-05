# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, source-preserving I/O. No implicit cleanup or overwrite of user files."""
from __future__ import annotations
import hashlib,json,os,stat,time,uuid,math,re
from pathlib import Path

class RuntimeFailure(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message); self.code=code; self.details=details

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def descriptor(path):
    p=Path(path); return {'file':str(p.absolute()),'sha256':digest(p),'bytes':p.stat().st_size}

def atomic_json(path,value,*,replace=True):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    if len(data)>8*1024*1024:raise RuntimeFailure('REPORT_LIMIT','JSON exceeds 8 MiB')
    temp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(temp,'xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    if not replace and p.exists():raise RuntimeFailure('PATH_CONFLICT','Output already exists',file=str(p))
    if replace:os.replace(temp,p)
    else:
        # Atomic no-replace hardlink: temp is retained as diagnostic evidence.
        os.link(temp,p)
    return descriptor(p)

def checked_path(path,*,exists=True):
    p=Path(path)
    if not p.is_absolute() or '..' in p.parts:raise RuntimeFailure('INVALID_PATH','Absolute non-traversing path required',file=str(p))
    for part in [p,*p.parents]:
        if part.is_symlink():raise RuntimeFailure('SYMLINK_REFUSED','Symbolic links are not accepted',file=str(part))
    if exists and (not p.is_file() or not stat.S_ISREG(p.stat().st_mode)):raise RuntimeFailure('NOT_FOUND','Regular file required',file=str(p))
    return p

def verify_descriptor(value,*,expected=True):
    p=checked_path(value['file']); sha=value['expected_sha256'] if expected else value['sha256']; actual=descriptor(p)
    if sha!=actual['sha256'] or ('bytes' in value and value['bytes']!=actual['bytes']):raise RuntimeFailure('SOURCE_CHANGED','File hash or size differs',expected=value,observed=actual)
    return actual

def copy_verified(source,target,*,limit_bytes):
    before=verify_descriptor(source); p=checked_path(target,exists=False)
    if p.exists():raise RuntimeFailure('PATH_CONFLICT','Copy target already exists',file=str(p))
    if before['bytes']>limit_bytes:raise RuntimeFailure('BUDGET_EXCEEDED','File copy budget exceeded')
    p.parent.mkdir(parents=True,exist_ok=True)
    total=0
    with open(before['file'],'rb') as src,open(p,'xb') as dst:
        for b in iter(lambda:src.read(1024*1024),b''):
            total+=len(b)
            if total>limit_bytes:raise RuntimeFailure('BUDGET_EXCEEDED','Input grew beyond copy budget')
            dst.write(b)
        dst.flush();os.fsync(dst.fileno())
    copied=descriptor(p);after=verify_descriptor(source)
    if before!=after or copied['sha256']!=before['sha256']:raise RuntimeFailure('SOURCE_CHANGED','Input changed while copying')
    return copied,{'before':before,'after':after,'observed_at':time.time(),'written_by_this_tool':False,'continuous_immutability_proven':False}

def tree_manifest(root,max_files=10000):
    root=Path(root);result=[]
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise RuntimeFailure('SYMLINK_REFUSED','Archive contains symbolic link',file=str(p))
        if p.is_file():
            if len(result)>=max_files:raise RuntimeFailure('BUDGET_EXCEEDED','Archive file count exceeded')
            result.append({'relative_path':str(p.relative_to(root)),'sha256':digest(p),'bytes':p.stat().st_size})
    return result

def read_json_reference(value,*,root=None):
    """Read bounded JSON whose exact bytes, not just a prior stat, match its ref."""
    if (not isinstance(value,dict) or set(value)!={'file','sha256','bytes'} or
        not isinstance(value['file'],str) or len(value['file'])>4096 or
        not isinstance(value['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['sha256']) or
        type(value['bytes']) is not int or not 0<=value['bytes']<=8*1024*1024):
        raise RuntimeFailure('EVIDENCE_REFERENCE_INVALID','Expected bounded file/SHA-256/bytes JSON reference')
    path=checked_path(value['file'])
    if root is not None and not path.is_relative_to(Path(root)):
        raise RuntimeFailure('EVIDENCE_REFERENCE_INVALID','Evidence is outside the owned job',file=str(path))
    return read_json(path,_reference=value)

def compact_step_records(records):
    """Checkpoint history is semantic metadata, never an embedded report graph.

    No file refs are persisted in the blend: receipts own artifact dependencies,
    and a copied/resumed candidate must not retain references to an older job.
    Ordinary operation witnesses remain intact for check_registry on resume.
    """
    result=[]
    for record in records:
        if record.get('op')=='checkpoint':
            evidence=record.get('evidence',{})
            record={k:v for k,v in record.items() if k in ('step_key','op','status','seconds','geometry_budget','reused_checkpoint')}
            record['evidence']={k:evidence[k] for k in ('state','design_state_sha256','completed_step_keys') if k in evidence}
        result.append(record)
    return result

def compact_checkpoint_record(record):
    """Project the fixed checkpoint envelope; bulky snapshots live in sidecar."""
    fields=('file','sha256','bytes','candidate','producer_identity','state',
            'design_state_sha256','completed_step_keys','sidecar','design_state','report','quad_quality')
    return {key:record[key] for key in fields if key in record}

def checkpoint_evidence_artifacts(report,*,root=None):
    """Validate only the fixed checkpoint sidecar/report refs, without recursion."""
    checkpoints=report.get('checkpoints',[])
    if not isinstance(checkpoints,list) or len(checkpoints)>128:
        raise RuntimeFailure('EVIDENCE_REFERENCE_INVALID','Checkpoint evidence list must be bounded')
    artifacts={}
    for index,checkpoint in enumerate(checkpoints):
        if not isinstance(checkpoint,dict) or 'scene_snapshot' in checkpoint:
            raise RuntimeFailure('EVIDENCE_REFERENCE_INVALID','Checkpoint must contain compact sidecar references')
        side=read_json_reference(checkpoint.get('sidecar'),root=root)
        saved=read_json_reference(checkpoint.get('report'),root=root)
        if (saved!={k:v for k,v in checkpoint.items() if k!='report'} or
            not isinstance(side,dict) or set(side)!={'scene_snapshot','design_state_sha256','completed_step_keys'} or
            side['design_state_sha256']!=checkpoint.get('design_state_sha256') or
            side['completed_step_keys']!=checkpoint.get('completed_step_keys')):
            raise RuntimeFailure('EVIDENCE_REFERENCE_MISMATCH','Checkpoint references do not bind the reported checkpoint')
        if report.get('partial_checkpoint') and index==len(checkpoints)-1:
            if (side['scene_snapshot']!=report.get('scene_snapshot') or
                checkpoint.get('candidate')!=report.get('candidate') or
                checkpoint.get('completed_step_keys')!=report.get('completed_step_keys')):
                raise RuntimeFailure('EVIDENCE_REFERENCE_MISMATCH','Checkpoint sidecar differs from the exact stop result')
        artifacts[f'checkpoint_sidecar_{index:03d}']=checkpoint['sidecar']
        artifacts[f'checkpoint_report_{index:03d}']=checkpoint['report']
    quality_records=[]
    for checkpoint in checkpoints:
        if checkpoint.get('quad_quality') is not None:quality_records.append(checkpoint['quad_quality'])
    if report.get('checks',{}).get('quad_topology') is not None:quality_records.append(report['checks']['quad_topology'])
    seen=set()
    for quality in quality_records:
        if quality.get('status')!='pass' or not isinstance(quality.get('evidence'),list):
            raise RuntimeFailure('QUAD_EVIDENCE_INVALID','Mandatory quad check lacks complete passed evidence')
        for item in quality['evidence']:
            detail=item.get('details');value=read_json_reference(detail,root=root)
            if item.get('passed') is not True or value.get('passed') is not True or value.get('mesh_state')!=item.get('mesh_state'):
                raise RuntimeFailure('QUAD_EVIDENCE_INVALID','Actual quad report does not support gate summary')
            if detail['file'] in seen:continue
            seen.add(detail['file']);artifacts[f'quad_quality_{len(seen):03d}']=detail
    return artifacts

def read_json(path,*,max_bytes=8*1024*1024,_reference=None):
    p=checked_path(path)
    if p.stat().st_size>max_bytes:raise RuntimeFailure('JSON_LIMIT','JSON file exceeds byte limit')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise RuntimeFailure('INVALID_JSON','Regular JSON file required')
        raw=stream.read(max_bytes+1)
    if len(raw)>max_bytes:raise RuntimeFailure('JSON_LIMIT','JSON file grew beyond byte limit')
    if _reference is not None and (len(raw)!=_reference['bytes'] or hashlib.sha256(raw).hexdigest()!=_reference['sha256']):
        raise RuntimeFailure('EVIDENCE_REFERENCE_MISMATCH','Referenced JSON bytes or SHA-256 differ',file=str(p))
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result:raise RuntimeFailure('INVALID_JSON','Duplicate key',key=key)
            result[key]=value
        return result
    def invalid(value):raise RuntimeFailure('INVALID_JSON','Nonfinite JSON number')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    count=[0]
    def walk(item,depth=0):
        count[0]+=1
        if depth>64 or count[0]>1000000:raise RuntimeFailure('JSON_LIMIT','JSON structure limit exceeded')
        if isinstance(item,float) and not math.isfinite(item):raise RuntimeFailure('INVALID_JSON','Nonfinite JSON float')
        if isinstance(item,str):item.encode('utf-8',errors='strict')
        elif isinstance(item,dict):
            for k,v in item.items():walk(k,depth+1);walk(v,depth+1)
        elif isinstance(item,list):
            for v in item:walk(v,depth+1)
    walk(value);return value
