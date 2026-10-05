"""Fail-closed B/C/N/M semantic preservation. No arbitrary topology merge.

Input projections are maps {object_id: {field: JSON value}}. Geometry fingerprints,
modifiers and dependency descriptors must be obtained from real scene inspection.
B is the previous pure generator projection, C current scene, N newly generated.
"""
from __future__ import annotations
import copy
from .contract import ContractError, fingerprint

ALLOWLIST = frozenset({'name','display','visibility','material_slots','manual_modifiers'})
OWNED_FIELDS = frozenset({'geometry_sha256','topology_sha256','matrix','managed_modifiers','design_revision','data_id','feature_id','provenance'})
MODIFIER_ALLOWLIST = frozenset({'WEIGHTED_NORMAL','TRIANGULATE','NORMAL_EDIT'})
MISSING = object()


def _equivalent(a,b): return a is b if a is MISSING or b is MISSING else a==b

def _summary(value): return {'missing':True} if value is MISSING else {'sha256':fingerprint(value),'value':copy.deepcopy(value)}

def merge_projections(baseline,current,desired,*,write_set=None,context=None):
    """Return paused report on any conflict; never publish a partial merged state.

    context:
      shared_data_users {data_id:[object IDs]}, verified_manual_objects [IDs],
      stable_datum_ids [IDs], material_face_assignments_stable [object IDs],
      verified_postprocessors [object IDs]. Manual objects require dependency
      descriptors and positive inspection evidence, not just absence from B.
    """
    for value in (baseline,current,desired):
        if not isinstance(value,dict): raise ContractError('INVALID_PRESERVATION','Projections must be object maps')
    context=context or {}; writes=set(desired if write_set is None else write_set); merged=copy.deepcopy(desired); overlay={}; preserved=[]; conflicts=[]
    def conflict(oid,field,b,c,n,reason):
        conflicts.append({'object_id':oid,'field':field,'reason':reason,'B':_summary(b),'C':_summary(c),'N':_summary(n),'downstream':context.get('consumers',{}).get(oid,[]),'options':['keep_current_branch','generate_separate_branch','rebuild_interface_manually']})
    # Shared write data crossing a protected/manual domain cannot be proven independent.
    for did,users in context.get('shared_data_users',{}).items():
        if writes.intersection(users) and any(u not in writes or u not in baseline for u in users):
            conflict(next(iter(writes.intersection(users))),'data_id',did,did,did,'SHARED_DATA_CROSS_PARTITION')
    for oid in sorted(set(baseline)|set(current)|set(desired)):
        b=baseline.get(oid,MISSING); c=current.get(oid,MISSING); n=desired.get(oid,MISSING)
        if b is MISSING:
            if c is not MISSING and n is not MISSING:
                conflict(oid,'object',b,c,n,'NEW_GENERATED_ID_COLLIDES_WITH_CURRENT_OBJECT'); continue
            if c is not MISSING:
                if oid not in context.get('verified_manual_objects',[]): conflict(oid,'object',b,c,n,'MANUAL_DEPENDENCIES_NOT_VERIFIED'); continue
                deps=c.get('datum_dependencies',[])
                if any(d not in context.get('stable_datum_ids',[]) for d in deps): conflict(oid,'datum_dependencies',b,c,n,'MANUAL_DATUM_CHANGED'); continue
                merged[oid]=copy.deepcopy(c); overlay[oid]={'kind':'manual_object','value':copy.deepcopy(c)}; preserved.append({'object_id':oid,'fields':['*'],'basis':'independent_verified_manual_object'})
            continue
        if c is MISSING:
            if n is not MISSING: conflict(oid,'object',b,c,n,'GENERATED_OBJECT_REMOVED_BY_USER')
            continue
        if n is MISSING:
            if c!=b: conflict(oid,'object',b,c,n,'GENERATOR_DELETE_OVERLAPS_MANUAL_EDITS')
            continue
        if oid not in writes:
            if n!=b: conflict(oid,'object',b,c,n,'UNDECLARED_WRITE_SET')
            else:
                merged[oid]=copy.deepcopy(c)
                if c!=b: overlay[oid]={'kind':'protected_object','value':copy.deepcopy(c)}; preserved.append({'object_id':oid,'fields':['*'],'basis':'outside_write_set'})
            continue
        edits={}
        for field in sorted(set(b)|set(c)|set(n)):
            bv=b.get(field,MISSING); cv=c.get(field,MISSING); nv=n.get(field,MISSING)
            manual_changed=not _equivalent(bv,cv); generator_changed=not _equivalent(bv,nv)
            if not manual_changed: continue
            if generator_changed:
                # Explicit exact-equivalence rule only; ownership is still logged.
                if _equivalent(cv,nv):
                    edits[field]={'kind':'equivalent_both_changed','value':copy.deepcopy(cv)}; continue
                conflict(oid,field,bv,cv,nv,'OVERLAPPING_MANUAL_AND_GENERATED_CHANGE'); continue
            if field not in ALLOWLIST:
                conflict(oid,field,bv,cv,nv,'UNSUPPORTED_MANUAL_FIELD'); continue
            if field=='material_slots' and oid not in context.get('material_face_assignments_stable',[]):
                conflict(oid,field,bv,cv,nv,'FACE_MATERIAL_MAPPING_NOT_VERIFIED'); continue
            if field=='manual_modifiers':
                if cv is MISSING or not isinstance(cv,list) or oid not in context.get('verified_postprocessors',[]) or any(not isinstance(m,dict) or m.get('type') not in MODIFIER_ALLOWLIST for m in cv):
                    conflict(oid,field,bv,cv,nv,'POSTPROCESSOR_NOT_VERIFIED_OR_UNSUPPORTED'); continue
            if cv is MISSING:
                merged[oid].pop(field,None); edits[field]={'kind':'delete'}
            else:
                merged[oid][field]=copy.deepcopy(cv); edits[field]={'kind':'set','value':copy.deepcopy(cv)}
        if edits:
            overlay[oid]={'kind':'field_overlay','fields':edits}; preserved.append({'object_id':oid,'fields':sorted(edits),'basis':'allowlisted_disjoint_or_explicit_exact_equivalence'})
    return {'status':'pause' if conflicts else 'pass','conflicts':conflicts,'preserved':preserved,'merged':None if conflicts else merged,'baseline_new':None if conflicts else copy.deepcopy(desired),'manual_overlay_new':None if conflicts else overlay,'baseline_sha256':fingerprint(baseline),'current_sha256':fingerprint(current),'desired_sha256':fingerprint(desired),'merged_sha256':None if conflicts else fingerprint(merged),'acceptance':{'preservation':'fail' if conflicts else 'pass','geometry':'not_run','dependencies':'not_run','reopen':'not_run'}}


def require_merge(baseline,current,desired,**kwargs):
    report=merge_projections(baseline,current,desired,**kwargs)
    if report['status']!='pass': raise ContractError('PRESERVATION_CONFLICT','Manual edits could not be proven nonconflicting',details=report)
    return report


def verify_overlay_reopen(merged,actual):
    expected=fingerprint(merged); observed=fingerprint(actual)
    if expected!=observed: raise ContractError('PRESERVATION_CONFLICT','Reopened semantic projection differs',details={'expected_sha256':expected,'actual_sha256':observed})
    return {'status':'pass','expected_sha256':expected,'actual_sha256':observed}


def preservation_matrix():
    return {'supported':{'name':'outside generator changes','display':'allowlisted object display values','visibility':'allowlisted object visibility values','material_slots':'only independently verified face mapping','manual_modifiers':sorted(MODIFIER_ALLOWLIST),'manual_objects':'verified independent data and stable datum dependencies'},'unsupported':['generated mesh manual edits','unknown modifier stack changes','cross-partition shared writable data','unverified face or edge index consumers','automatic overwrite or rollback of later edits'],'baseline_rule':'B_new=N; M is never relabeled pure generated baseline'}
