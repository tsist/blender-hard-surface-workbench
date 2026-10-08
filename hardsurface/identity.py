"""Stable object/data identities and explicit selection invalidation contracts."""
from __future__ import annotations
import math
import hashlib
import json
import re
import uuid
from .contract import ContractError, UUID_PATTERN, fingerprint, canonical_bytes

NAMESPACE = uuid.UUID('6089d77d-639a-598e-a2f6-f324c579fa87')

def _mesh_hash(value):
    # Mesh size is separately budgeted; request parser node limits do not apply.
    digest=hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
        digest.update(chunk.encode('utf-8'))
    return digest.hexdigest()

def new_id(): return str(uuid.uuid4())
def stable_id(project_id, feature_id, step_id, port='body_object', *, kind='object'):
    return str(uuid.uuid5(NAMESPACE, canonical_bytes([project_id,feature_id,step_id,port,kind]).decode('utf-8')))
def validate_id(value):
    if not isinstance(value,str) or not re.fullmatch(UUID_PATTERN,value): raise ContractError('INVALID_ID','Expected canonical lowercase UUID')
    return value

def validate_registry(records):
    """One record per object. Shared data IDs are valid, duplicate object IDs are not."""
    seen={}; data_users={}
    for record in records:
        oid=validate_id(record['object_id']); did=validate_id(record['data_id']) if record.get('data_id') else None
        if oid in seen: raise ContractError('DUPLICATE_ID','Multiple objects have one persistent ID',details={'object_id':oid})
        seen[oid]=record
        if did: data_users.setdefault(did,[]).append(oid)
    return {'objects':seen,'data_users':data_users}

def _index(value,size):
    if type(value) is not int or not 0<=value<size: raise ContractError('INVALID_TOPOLOGY','Mesh index out of range')
    return value

def topology_hash(vertices,edges,faces):
    """Ordered indices, counts and domains; coordinate changes alone do not affect it.

    Array ordering deliberately participates: selectors address that ordering.
    """
    count=vertices if type(vertices) is int else len(vertices)
    if type(count) is not int or count<0: raise ContractError('INVALID_TOPOLOGY','Invalid vertex count')
    ee=[]; ff=[]
    for edge in edges:
        if len(edge)!=2: raise ContractError('INVALID_TOPOLOGY','Edges require two indices')
        pair=[_index(i,count) for i in edge]
        if pair[0]==pair[1]: raise ContractError('INVALID_TOPOLOGY','Self edge')
        ee.append(pair)
    for face in faces:
        if len(face)<3: raise ContractError('INVALID_TOPOLOGY','Faces require at least 3 indices')
        indices=[_index(i,count) for i in face]
        if len(set(indices))!=len(indices): raise ContractError('INVALID_TOPOLOGY','Repeated vertex in face')
        ff.append(indices)
    return _mesh_hash({'version':'HS_TOPOLOGY_V1','domains':['VERTEX','EDGE','FACE','CORNER'],'vertices':count,'edges':ee,'faces':ff})

def geometry_hash(vertices,matrix=None,evaluation=None):
    coords=[]
    for vertex in vertices:
        if len(vertex)!=3: raise ContractError('INVALID_GEOMETRY','Expected xyz coordinates')
        vals=[]
        for value in vertex:
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value): raise ContractError('INVALID_GEOMETRY','Nonfinite coordinate')
            vals.append(0.0 if value==0 else float(value))
        coords.append(vals)
    return _mesh_hash({'version':'HS_GEOMETRY_V1','vertices':coords,'matrix':matrix,'evaluation':evaluation})

def evaluation_hash(frame,view_layer,evaluation,modifiers):
    return fingerprint({'frame':frame,'view_layer':view_layer,'evaluation':evaluation,'modifiers':modifiers})

def validate_selection(selection,snapshot):
    """Validate immediately before consumption. Snapshot must come from actual mesh.

    snapshot: object_id,data_id,topology_sha256,geometry_sha256,evaluation_sha256,
    counts {VERTEX,EDGE,FACE}, optional attributes [{name,domain,type,values}].
    """
    for field in ('object_id','data_id','topology_sha256'):
        if selection.get(field)!=snapshot.get(field): raise ContractError('SELECTION_STALE',f'{field} changed',details={'expected':selection.get(field),'actual':snapshot.get(field)})
    if selection.get('position_dependent') and not selection.get('geometry_sha256'): raise ContractError('SELECTION_STALE','Position-dependent selection lacks geometry fingerprint')
    for field in ('geometry_sha256','evaluation_sha256'):
        if field in selection and selection[field]!=snapshot.get(field): raise ContractError('SELECTION_STALE',f'{field} changed')
    domain=selection.get('domain')
    counts=snapshot.get('counts',{})
    if domain not in counts: raise ContractError('SELECTION_UNSUPPORTED','Unknown mesh domain')
    if selection['kind']=='index_selection':
        indices=selection['indices']
        if len(set(indices))!=len(indices): raise ContractError('INVALID_SELECTION','Duplicate indices')
        for idx in indices: _index(idx,counts[domain])
        return list(indices)
    if selection['kind']=='attribute_selection':
        matches=[a for a in snapshot.get('attributes',[]) if a['name']==selection['name'] and a['domain']==domain and a['type']==selection['type']]
        if len(matches)!=1: raise ContractError('SELECTION_AMBIGUOUS','Attribute source is missing or ambiguous')
        values=matches[0]['values']
        if len(values)!=counts[domain]: raise ContractError('SELECTION_STALE','Attribute domain cardinality changed')
        indices=[i for i,x in enumerate(values) if type(x) is type(selection['value']) and x==selection['value']]
        if len(indices)!=selection['expected_count']: raise ContractError('SELECTION_AMBIGUOUS','Attribute cardinality differs',details={'actual_count':len(indices)})
        return indices
    raise ContractError('SELECTION_UNSUPPORTED','Selector is not an index or supported attribute selection')

def resolve_feature_port(registry,feature_id,port):
    """No name/index fallback and no assumption provenance survives Boolean."""
    matches=[r for r in registry if r.get('feature_id')==feature_id and r.get('port')==port and r.get('valid',True)]
    if len(matches)!=1: raise ContractError('SELECTION_AMBIGUOUS' if len(matches)>1 else 'SELECTION_STALE','Feature port did not resolve uniquely',details={'feature_id':feature_id,'port':port,'candidate_count':len(matches)})
    return matches[0]


STRUCTURE_BINDING_FIELDS = {
    'schema_version','object_id','data_id','mesh_state','schedule_revision',
    'topology_epoch','authorship_sha256','construction_sha256','manifest_sha256',
    'context_sha256','source_binding_sha256','geometry_signature',
    'topology_signature','attribute_signature','structure_signature',
}


def validate_structure_registry(registry):
    """Validate separately held native binding receipts, never grant approval."""
    if not isinstance(registry,dict) or len(registry)>128:
        raise ContractError('STRUCTURE_REGISTRY_INVALID','Bounded object-to-binding registry required')
    import copy
    for key,row in registry.items():
        validate_id(key)
        if not isinstance(row,dict) or set(row)!=STRUCTURE_BINDING_FIELDS:
            raise ContractError('STRUCTURE_REGISTRY_INVALID','Native binding fields differ')
        if row['schema_version']!='native-control-structure/1.0' or row['mesh_state']!='control':
            raise ContractError('STRUCTURE_REGISTRY_INVALID','Unsupported native binding version/state')
        if row['object_id']!=key:raise ContractError('STRUCTURE_REGISTRY_INVALID','Registry key differs from actual object identity')
        validate_id(row['data_id'])
        if not isinstance(row['schedule_revision'],str) or not row['schedule_revision'] or len(row['schedule_revision'])>200:
            raise ContractError('STRUCTURE_REGISTRY_INVALID','Explicit authored schedule identity required')
        if type(row['topology_epoch']) is not int or row['topology_epoch']<0:
            raise ContractError('STRUCTURE_REGISTRY_INVALID','Nonnegative topology epoch required')
        for field in STRUCTURE_BINDING_FIELDS-{'schema_version','object_id','data_id','mesh_state','schedule_revision','topology_epoch'}:
            if not isinstance(row[field],str) or not re.fullmatch('[0-9a-f]{64}',row[field]):
                raise ContractError('STRUCTURE_REGISTRY_INVALID','Complete SHA-256 binding required',details={'field':field})
    return copy.deepcopy(registry)


def register_structure_binding(registry,binding,*,previous_binding=None,topology_migration=None):
    """Create or explicitly continue one object receipt; no blind rebinding."""
    import copy
    result=validate_structure_registry(registry)
    if not isinstance(binding,dict):raise ContractError('STRUCTURE_REGISTRY_INVALID','Binding must be an object')
    oid=binding.get('object_id');validated=validate_structure_registry({oid:binding})[oid]
    if oid in result:
        if previous_binding!=result[oid]:raise ContractError('STRUCTURE_REGISTRY_STALE','Explicit matching previous binding required to replace receipt')
        if validated['data_id']!=result[oid]['data_id'] or validated['schedule_revision']!=result[oid]['schedule_revision']:
            raise ContractError('STRUCTURE_REGISTRY_SCOPE','This native edit path only continues object/data/schedule/epoch')
        if validated['topology_epoch']!=result[oid]['topology_epoch']:
            migration=topology_migration
            if (validated['schedule_revision']!='sparse_sharp_panel_ids_v2' or validated['topology_epoch']!=result[oid]['topology_epoch']+1
                    or not isinstance(migration,dict) or migration.get('schema_version')!='sparse-native-insertion/1.0'
                    or migration.get('status')!='pass' or migration.get('kernel_edit',{}).get('status')!='pass'
                    or migration.get('before_binding')!=result[oid] or migration.get('after_binding')!=validated
                    or migration.get('complete_body')!='verified' or migration.get('original_control_vertices')!='exactly_preserved'):
                raise ContractError('STRUCTURE_REGISTRY_SCOPE','Topology epoch changes require the exact verified sparse full-body migration')
        elif topology_migration is not None:
            raise ContractError('STRUCTURE_REGISTRY_SCOPE','A topology migration must increment its epoch')
    elif previous_binding is not None:
        raise ContractError('STRUCTURE_REGISTRY_STALE','Previous binding supplied for an absent object')
    elif topology_migration is not None:
        raise ContractError('STRUCTURE_REGISTRY_SCOPE','Topology migration requires an existing exact source registry')
    result[oid]=copy.deepcopy(validated)
    return result
