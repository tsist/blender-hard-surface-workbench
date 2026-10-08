# SPDX-License-Identifier: GPL-3.0-or-later
"""One resumable source-bound edit/review work unit; host-only orchestration.

Only existing public edit and diagnosis entry points start native processes.
An accepted stage is immutable evidence, never an inference from a running PID.
The workflow deliberately has no render, fitting, cleanup or production-pass path.
"""
from __future__ import annotations
import copy
import fcntl
import json
import os
import math
import stat
from pathlib import Path
import time

from . import contract as c, subdivision
from .budgets import BudgetExceeded, BudgetLimits, JobBudget, tree_file_bytes, available_ram_bytes
from .io import RuntimeFailure, checked_path, read_json, read_json_reference
from .jobs import _atomic_json, _fsync_dir, _no_symlinks
from .recovery import artifact_descriptor, verify_descriptor, CheckpointStore
from .subdivision_compare import DEFAULT_LIMITS as COMPARE_LIMITS
import hashlib

def evidence_digest(value):
    # Generated scene evidence has independent bounds larger than input contracts.
    def clean(item):
        if isinstance(item,float): return 0.0 if item == 0 else item
        if isinstance(item,dict): return {k:clean(v) for k,v in item.items()}
        if isinstance(item,list): return [clean(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(clean(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

VERSION = 'hardsurface-edit-review/1.1'
STAGES = ('before', 'edit', 'after', 'compare')
# Standalone P2 surface acquisition is not a stage in this bounded P1 workflow.
_WORKFLOW_DIAGNOSIS = copy.deepcopy(subdivision.REQUEST)
_WORKFLOW_DIAGNOSIS['properties']['params']['properties'].pop('surface_export', None)
REQUEST = c.obj({
    'schema_version': c.const('1.0'), 'command': c.const('hardsurface.edit-review'),
    'params': c.obj({
        'request_id': copy.deepcopy(subdivision.REQUEST['properties']['params']['properties']['request_id']),
        'edit_request': copy.deepcopy(c.REQUEST),
        'reference_approval': c.union(c.const(None), c.obj({'file': c.string(minLength=1), 'expected_sha256': c.SHA})),
        'before_diagnosis': copy.deepcopy(_WORKFLOW_DIAGNOSIS),
        'after_panel_reference': c.optional_default(c.union(c.const(None), copy.deepcopy(subdivision.REQUEST['properties']['params']['properties']['panel_reference'])), None),
        'comparison_limits': c.optional_default(c.obj({key: (c.integer(minimum=1) if type(value) is int else c.number(exclusiveMinimum=0)) for key,value in COMPARE_LIMITS.items()}), COMPARE_LIMITS),
        'budgets': c.obj({
            'wall_seconds': c.number(exclusiveMinimum=0, maximum=10800),
            'max_disk_bytes': c.integer(minimum=1048576),
            'max_tree_rss_bytes': c.integer(minimum=1024**3, maximum=3*1024**3),
            'cpu_threads': c.integer(minimum=1, maximum=4),
            'max_native_processes': c.const(6),
            'min_system_available_bytes': c.optional_default(c.integer(minimum=0), 2*1024**3),
        }, ['wall_seconds','max_disk_bytes','max_tree_rss_bytes','cpu_threads','max_native_processes']),
    }, ['request_id','edit_request','reference_approval','before_diagnosis','budgets']),
})


def schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Hard Surface Workbench resumable bounded edit and actual SubD review 1.1', **copy.deepcopy(REQUEST)}


def fail(message, code='EDIT_REVIEW_BINDING', **details):
    raise RuntimeFailure(code, message, **details)


def require(condition, message, code='EDIT_REVIEW_BINDING'):
    if not condition:
        fail(message, code)


def validate_request(value):
    if isinstance(value, (str, bytes, bytearray)):
        value = c.strict_loads(value)
    c._walk_limits(value)
    if len(c.canonical_bytes(value)) > c.MAX_BYTES:
        fail('Workflow request exceeds 2 MiB', 'LIMIT_EXCEEDED')
    result = c._validate(value, REQUEST)
    p = result['params']
    require('surface_export' not in p['before_diagnosis']['params'],
            'Surface export is standalone P2 work; not an edit-review stage', 'INVALID_REQUEST')
    p['edit_request'] = c.validate_request(p['edit_request'])
    p['before_diagnosis'] = subdivision.validate_request(p['before_diagnosis'])
    e, d, b = p['edit_request']['params'], p['before_diagnosis']['params'], p['budgets']
    require(e['source']['kind'] == 'saved_blend', 'A saved source edit is required', 'INVALID_REQUEST')
    require(e['design']['mode'] == 'patch_if_revision', 'An exact revision-bound edit is required', 'INVALID_REQUEST')
    patches = e['design']['patches']
    insertion = len(patches) == 1 and patches[0]['op'] == 'insert_sparse_strip'
    parameters = 1 <= len(patches) <= 2 and all(x['op'] == 'set_dimension' for x in patches)
    require(insertion or parameters,
            'One insertion or one parameter category (at most two dimensions) is required', 'INVALID_REQUEST')
    require(len(e['work_units']) == 1 and len(e['work_units'][0]['feature_ids']) == 1,
            'The sole work unit must select exactly one feature', 'INVALID_REQUEST')
    if insertion:
        require(e['work_units'][0]['feature_ids'] == [patches[0]['feature_id']],
                'The sole work unit must select exactly the insertion feature', 'INVALID_REQUEST')
    if parameters:
        require(p['after_panel_reference'] is not None, 'Parameter edits require an explicit after-panel reference', 'INVALID_REQUEST')
        reference_edit_category(d['panel_reference'], p['after_panel_reference'])
    else:
        require(p['after_panel_reference'] is None, 'Insertion retains the unchanged nominal reference', 'INVALID_REQUEST')
    require(e.get('quality', {}).get('stage') == 'source_cage', 'Edit must retain source-cage-only qualification', 'INVALID_REQUEST')
    require(not e['wire']['enabled'] and 'preview' not in e and 'recovery' not in e,
            'No rendering, wire workers or nested recovery is supported', 'INVALID_REQUEST')
    require(e['execution'] == {'max_attempts': 1, 'technical_strategies': ['declared']},
            'Only one declared edit attempt is supported', 'INVALID_REQUEST')
    require(d['evaluation_profile']['mode'] == subdivision.SPARSE_DIAGNOSTIC_PROFILE,
            'Complete source-bound sparse diagnosis is required', 'INVALID_REQUEST')
    require(d['levels'] == [0, 1, 2, 3], 'Ordered levels 0 through 3 are required', 'INVALID_REQUEST')
    for key in ('file', 'expected_sha256'):
        require(d['source'][key] == e['source'][key], 'Before diagnosis must bind the exact edit source', 'INVALID_REQUEST')
    if 'bytes' in d['source']:
        require(d['source']['bytes'] == e['source']['bytes'], 'Source sizes differ', 'INVALID_REQUEST')
    for settings in (e['budgets'], d):
        require(settings['cpu_threads'] <= b['cpu_threads'], 'A child exceeds the aggregate thread cap', 'INVALID_REQUEST')
    require(e['budgets']['max_observed_rss_bytes'] <= b['max_tree_rss_bytes'] and
            d.get('max_tree_rss_bytes', 1024**3) <= b['max_tree_rss_bytes'],
            'A child exceeds the aggregate RSS cap', 'INVALID_REQUEST')
    return result


def _input_file(value):
    from .io import verify_descriptor as input_verify
    return input_verify(value)


def _binding(request, blender):
    from . import host
    from .reference import verify_reference
    p = request['params']; e = p['edit_request']['params']
    files = [_input_file(e['source']), *[_input_file(x) for x in e['resources']]]
    gate = verify_reference(e, p['reference_approval'])
    if p['reference_approval'] is not None:
        files.append(_input_file(p['reference_approval']))
        files.extend([gate['manifest'], *[{k: row[k] for k in ('file','sha256','bytes')} for row in gate['reference_files']]])
    return {'workflow_version': VERSION, 'request': request, 'source_and_references': files,
            'reference_gate': gate, 'implementation': host.impl_identity(blender)}


def plan(request, blender):
    request = validate_request(request)
    bound = _binding(request, blender)
    return {'workflow_version': VERSION, 'status': 'planned', 'binding_sha256': c.fingerprint(bound),
            'stages': list(STAGES), 'native_processes': {'before': 1, 'edit': 4, 'after': 1, 'compare': 0, 'total': 6},
            'scope': 'One exact saved-source sparse insertion or independent hole diameter/position, explicit-datum thickness or outer roundover edit; independent saved-candidate reopen; source-bound actual Catmull-Clark L0..3',
            'saved_source_plan_validation': 'Required during existing guarded source inspect before build; unsupported extra steps fail closed',
            'budgets': request['params']['budgets'], 'execution': {'status': 'not_run'},
            'shape_review': {'status': 'pending'}, 'production_qualification': 'not_asserted'}


def reference_edit_category(before, after):
    a,b=copy.deepcopy(before),copy.deepcopy(after)
    require(len(a['holes']) == len(b['holes']) == 1, 'Exactly one reference hole is required', 'EDIT_REVIEW_SCOPE')
    categories=[]
    for key,category in (('radius','hole_diameter'),('center','hole_position')):
        if a['holes'][0][key] != b['holes'][0][key]: categories.append(category)
        b['holes'][0][key]=a['holes'][0][key]
    if any(a[k] != b[k] for k in ('z_min','z_max')): categories.append('thickness')
    for key in ('z_min','z_max'): b[key]=a[key]
    if a['edge_bevel'] != b['edge_bevel']: categories.append('outer_roundover')
    b['edge_bevel']=a['edge_bevel']
    require(a == b and len(categories) == 1, 'After reference must change exactly one edit category; tolerance and frame stay fixed', 'EDIT_REVIEW_SCOPE')
    return categories[0]


def parameter_edit_scope(plan, saved_state):
    """Check semantic parameter paths, never dimension names, before native build."""
    from .planner import _resolve_dimensions
    require(isinstance(saved_state, dict) and not saved_state.get('sketches'),
            'Parameter edit requires the exact inspected saved state without sketches', 'EDIT_REVIEW_SCOPE')
    steps = plan.get('steps', [])
    require(len(steps) == 1, 'Parameter edit requires exactly one planned step', 'EDIT_REVIEW_SCOPE')
    step = steps[0]; fid = step['feature_id']
    features = [f for f in saved_state['features'] if f['id'] == fid]
    require(len(features) == 1 and len(saved_state['features']) == 1,
            'Parameter workflow currently requires one saved feature; shared driving dimensions are unsupported', 'EDIT_REVIEW_SCOPE')
    patches = plan.get('request', {}).get('params', {}).get('design', {}).get('patches', [])
    if patches:
        consumed = set()
        def refs(value):
            if isinstance(value, dict):
                if value.get('kind') == 'dimension_ref': consumed.add(value['id'])
                for child in value.values(): refs(child)
            elif isinstance(value, list):
                for child in value: refs(child)
        refs(features[0]['program'])
        require(all(p.get('op') == 'set_dimension' and p['id'] in consumed for p in patches),
                'Every dimension patch must drive the selected saved panel', 'EDIT_REVIEW_SCOPE')
    program = _resolve_dimensions(features[0]['program'], {d['id']: d for d in saved_state['dimensions']}, plan['length_scale'])
    require(program.get('kind') == 'steps' and len(program['steps']) == 1,
            'Parameter workflow requires one explicit saved panel step', 'EDIT_REVIEW_SCOPE')
    old = c._validate(program['steps'][0], c.STEP)
    new = copy.deepcopy(step.get('effective_params', step))
    require(old.get('op') == new.get('op') == 'quad.panel' and
            old.get('topology_strategy') == new.get('topology_strategy') == 'sparse_control_cage',
            'Only existing sparse panels are supported', 'EDIT_REVIEW_SCOPE')
    a, b = copy.deepcopy(old), copy.deepcopy(new)
    require(len(a.get('holes', [])) == len(b.get('holes', [])) == 1, 'Exactly one hole is required', 'EDIT_REVIEW_SCOPE')
    categories = []
    for key, category in (('radius','hole_diameter'), ('center','hole_position')):
        if a['holes'][0][key] != b['holes'][0][key]: categories.append(category)
        b['holes'][0][key] = a['holes'][0][key]
    if any(a[k] != b[k] for k in ('z_min','z_max')):
        categories.append('thickness')
        datum = a.get('edit_datum')
        require(datum in ('fixed_bottom','fixed_midplane'), 'Thickness requires a saved explicit datum', 'EDIT_REVIEW_SCOPE')
        require((datum == 'fixed_bottom' and a['z_min'] == b['z_min']) or
                (datum == 'fixed_midplane' and a['z_min']+a['z_max'] == b['z_min']+b['z_max']),
                'Thickness violates the saved datum', 'EDIT_REVIEW_SCOPE')
    for key in ('z_min','z_max'): b[key] = a[key]
    if a['edge_bevel'] != b['edge_bevel']: categories.append('outer_roundover')
    b['edge_bevel'] = a['edge_bevel']
    require(a == b and len(categories) == 1,
            'Exactly one supported parameter category may change; no no-op, combined edits or technical-policy changes', 'EDIT_REVIEW_SCOPE')
    return categories[0]


class WorkflowBudget:
    """One cumulative serial budget, sampled inside every owned native process."""
    def __init__(self, job, limits, *, usage=None, started=None, cancel_check=None):
        self.job, self.policy = Path(job), limits
        self.cancel_requested = cancel_check or (lambda: False)
        self.usage_path = self.job / 'usage.json'
        previous = usage or {}
        elapsed = previous.get('wall_seconds_observed', 0.0)
        require(type(elapsed) in (int, float) and elapsed >= 0, 'Invalid accumulated time')
        self.processes = previous.get('native_processes_started', 0)
        require(type(self.processes) is int and 0 <= self.processes <= limits['max_native_processes'], 'Invalid accumulated process count')
        self.budget = JobBudget(BudgetLimits(wall_seconds=limits['wall_seconds'], max_disk_bytes=limits['max_disk_bytes'],
                                            max_tree_rss_bytes=limits['max_tree_rss_bytes'],
                                            minimum_available_ram_bytes=limits.get('min_system_available_bytes',2*1024**3)),
                                started_at=(started if started is not None else time.monotonic()) - elapsed)
        self.budget.peak_tree_rss_bytes = previous.get('peak_tree_rss_bytes_observed', 0)
        self.budget.peak_disk_bytes = previous.get('peak_disk_bytes_observed', 0)

    def __getattr__(self, name):
        return getattr(self.budget, name)

    def add_root(self, root):
        require(Path(root).is_relative_to(self.job), 'Child is outside the owned workflow')

    def save(self):
        _atomic_json(self.usage_path, self.report())

    def check(self, **observations):
        # OwnedProcess supplies its child family, excluding this Python host.
        try:
            rss_rows=Path('/proc/self/status').read_text().splitlines()
            own_rss=next(int(row.split()[1])*1024 for row in rss_rows if row.startswith('VmRSS:'))
        except (OSError,ValueError,StopIteration):
            fail('Current host RSS observation unavailable','EDIT_REVIEW_RESOURCE_OBSERVATION')
        observations['tree_rss_bytes'] = (observations.get('tree_rss_bytes') or 0) + own_rss
        if observations.get('available_ram') is None: observations['available_ram']=available_ram_bytes()
        if observations['available_ram'] is None: fail('System available memory observation unavailable','EDIT_REVIEW_RESOURCE_OBSERVATION')
        observations['disk_bytes'] = tree_file_bytes(self.job)
        try:
            return self.budget.check(**observations)
        finally:
            self.save()

    def claim_process(self):
        self.check()
        available=available_ram_bytes()
        required=self.policy['max_tree_rss_bytes']+self.policy.get('min_system_available_bytes',2*1024**3)
        if available is None or available < required:
            raise BudgetExceeded('native_admission_available_memory',required,available or 0)
        if self.processes >= self.policy['max_native_processes']:
            raise BudgetExceeded('native_processes', self.processes + 1, self.policy['max_native_processes'])
        self.processes += 1
        self.save()  # reserve before Popen; an interrupted reservation is not reused

    def validate_edit_plan(self, plan, saved_state=None):
        steps = plan.get('steps', [])
        require(len(steps) == 1 and steps[0]['op'] == 'quad.panel' and
                steps[0].get('effective_params', steps[0]).get('topology_strategy') == 'sparse_control_cage' and
                not plan.get('resolved_design_state', {}).get('sketches'),
                'Workflow requires one sparse panel build and no extra checkpoint/solver/operation workers', 'EDIT_REVIEW_SCOPE')
        if plan.get('request', {}).get('params', {}).get('design', {}).get('patches', [{}])[0].get('op') == 'set_dimension':
            parameter_edit_scope(plan, saved_state)

    def report(self):
        return {**self.budget.report(), 'native_processes_started': self.processes,
                'native_process_limit': self.policy['max_native_processes'],
                'cpu_threads_limit': self.policy['cpu_threads'], 'concurrent_native_process_limit': 1,
                'rss_scope': 'Observed Python host RSS plus active native descendant family; not hard isolation',
                'min_system_available_bytes': self.policy.get('min_system_available_bytes',2*1024**3),
                'budget_scope': 'Entire serial workflow including retained parent and child artifacts; resumed time is cumulative'}


class JointBudget:
    """Keep each child's established limits while also enforcing the parent."""
    def __init__(self, local, shared):
        self.local, self.shared = local, shared

    def __getattr__(self, name):
        return getattr(self.local, name)

    @property
    def remaining_seconds(self):
        return min(self.local.remaining_seconds, self.shared.remaining_seconds)

    def check(self, **observations):
        self.shared.check(**observations)
        return self.local.check(**observations)

    def preflight(self, estimates, **kwargs):
        self.shared.check()
        return self.local.preflight(estimates, **kwargs)

    def cancel_requested(self):
        return self.shared.cancel_requested()

    def claim_process(self):
        self.shared.claim_process()

    def validate_edit_plan(self, plan, saved_state=None):
        self.shared.validate_edit_plan(plan, saved_state=saved_state)

    def add_root(self, root):
        self.shared.add_root(root)

    def report(self):
        return {**self.local.report(), 'aggregate_workflow': self.shared.report()}


def _ref(value):
    return {k: value[k] for k in ('file', 'sha256', 'bytes')}


def _read(ref, root=None, *, max_bytes=8*1024*1024):
    verify_descriptor(_ref(ref), root=root)
    return read_json(checked_path(ref['file']), max_bytes=max_bytes, _reference=_ref(ref))


def _geometry(ref, root, budget=None):
    """Dedicated bounded geometry parser; verify the exact parsed bytes, including ABA."""
    if budget: budget.check()
    ref=_ref(ref); verify_descriptor(ref,root=root)
    require(ref['bytes'] <= subdivision.LIMITS['geometry_file_bytes'],'Geometry file exceeds 48 MiB','EDIT_REVIEW_GEOMETRY_LIMIT')
    fd=os.open(checked_path(ref['file']),os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),'Geometry is not a regular file')
        raw=stream.read(subdivision.LIMITS['geometry_file_bytes']+1)
    require(len(raw)==ref['bytes'] and hashlib.sha256(raw).hexdigest()==ref['sha256'],
            'Geometry parsed bytes differ from pinned descriptor','EVIDENCE_REFERENCE_MISMATCH')
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'Duplicate geometry JSON key','INVALID_JSON'); result[key]=value
        return result
    def constant(value): fail('Nonfinite geometry JSON constant','INVALID_JSON')
    try: value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,RecursionError) as exc: fail('Invalid geometry JSON','INVALID_JSON',reason=str(exc))
    del raw
    stack=[(value,0)]; count=0
    while stack:
        item,depth=stack.pop();count+=1
        require(depth<=64 and count<=8_000_000,'Geometry JSON exceeds dedicated depth/node bound','EDIT_REVIEW_GEOMETRY_LIMIT')
        if isinstance(item,float):require(math.isfinite(item),'Nonfinite geometry number','INVALID_JSON')
        elif isinstance(item,str):item.encode('utf-8',errors='strict')
        elif isinstance(item,dict):
            stack.extend((v,depth+1) for v in item.values());stack.extend((k,depth+1) for k in item)
        elif isinstance(item,list):stack.extend((v,depth+1) for v in item)
    if budget:budget.check()
    return value


def _refs(value, *, root):
    """Pin every owned receipt artifact, including nested native edit witnesses."""
    result = {}
    def visit(item):
        if isinstance(item, dict):
            if {'file','sha256','bytes'} <= set(item):
                ref = _ref(item)
                if Path(ref['file']).is_relative_to(root):
                    verify_descriptor(ref, root=root); result[ref['file']] = ref
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    require(len(result) <= 512, 'Too many child artifacts')
    return list(result.values())


def _diagnosis(report, request, job, budget=None):
    p = request['params']; source = _input_file(p['source'])
    require(report.get('status') == 'succeeded' and report.get('source_original') == source,
            'Diagnosis is not successful and bound to the exact requested source')
    require(report.get('source_guard', {}).get('accepted') is True, 'Diagnosis lacks accepted source protection')
    require(all(report.get(key) is False for key in ('blend_save_performed','saved_candidate_modified','source_geometry_modified','source_transforms_modified')),
            'Diagnosis mutation boundary is not verified')
    require(report.get('previews') == [] and report.get('target', {}).get('object_id') == p['target']['object_id'],
            'Diagnosis target differs or rendering occurred')
    require(report.get('source_signature_sha256') == report.get('source_signature_after_sha256') and
            bool(report.get('source_signature_sha256')), 'Diagnosis source signatures differ')
    profile = report.get('backend', {}).get('evaluation_profile', {})
    modifier = profile.get('modifier_profile', {})
    from .subdivision_source import declared_modifier_settings
    actual = declared_modifier_settings(modifier.get('actual_source_modifier', {}))
    require(profile.get('mode') == subdivision.SPARSE_DIAGNOSTIC_PROFILE and modifier.get('status') == 'pass' and
            actual == p['evaluation_profile']['expected_source_modifier'] and modifier.get('expected_source_modifier') == actual,
            'Actual diagnostic profile differs from the complete requested profile')
    require(profile.get('semantic_source', {}).get('actual_source_modifier') == modifier['actual_source_modifier'],
            'Semantic source and actual modifier differ')
    levels = report.get('levels', [])
    require([row.get('level') for row in levels] == [0,1,2,3], 'Diagnosis must contain all four actual levels')
    geometries = {}
    for row in levels:
        level = row['level']; expected = copy.deepcopy(modifier['actual_source_modifier'])
        expected.update(levels=level, render_levels=level, show_viewport=bool(level), show_render=bool(level))
        require(row.get('source_bound_evaluation', {}).get('status') == 'pass' and
                row['source_bound_evaluation'].get('actual_clone_modifier') == expected and
                row.get('actual_modifier_stack') == [expected], 'Actual per-level settings differ')
        ref = row.get('geometry', {})
        geometry = _geometry(ref, job, budget)
        require(geometry.get('level') == level and geometry.get('source_sha256') == source['sha256'],
                'Geometry export does not bind its level and actual source')
        transport = geometry.get('bound_semantic_regions', {}).get('transport', {})
        require(transport.get('source') == profile['semantic_source'] and transport.get('level') == level and
                transport == row.get('semantic_face_transport'), 'Geometry export semantic transport differs from report')
        geometries[str(level)] = _ref(ref)
    return {'source': source, 'target': p['target'], 'evaluation_profile': p['evaluation_profile'],
            'native_binding': profile['semantic_source']['native_binding'], 'geometries': geometries}


def verify_edit_plan_binding(plan, fingerprints, request):
    payload = copy.deepcopy(plan)
    recorded = payload.pop('plan_sha256', None)
    require(recorded == fingerprints.get('plan', {}).get('base') == c.fingerprint(payload),
            'Saved edit plan differs from the accepted checkpoint fingerprint')
    require(plan.get('request') == request, 'Accepted plan request differs from the edit request')


def _edit(report, request, job):
    require(report.get('status') == 'succeeded', 'Edit did not succeed')
    checkpoints = [x for x in report.get('checkpoints', []) if x.get('checkpoint_id') == 'final']
    require(len(checkpoints) == 1, 'Exactly one final accepted edit checkpoint is required')
    cp = CheckpointStore(job).resume(report['fingerprints'], 'final', expected_receipt=checkpoints[0]['receipt'])
    candidate = cp['artifacts']['candidate']
    require(_ref(report['candidate']) == candidate, 'Edit candidate differs from accepted saved checkpoint')
    core = _read(report['core_report'], job)
    reopened = _read(cp['verification']['evidence'], job)
    require(_ref(core['candidate']) == candidate and reopened.get('candidate') == candidate and
            reopened.get('independent_reopen') is True and
            reopened.get('scene_snapshot_sha256') == evidence_digest(core['scene_snapshot']),
            'Saved candidate is not bound to the independently reopened scene snapshot')
    patches = request['params']['design']['patches']
    insertion = patches[0]['op'] == 'insert_sparse_strip'
    feature_id = request['params']['work_units'][0]['feature_ids'][0]
    rows = [x for x in core['scene_snapshot']['objects'] if x.get('feature_id') == feature_id and
            (not insertion or x.get('step_key') == feature_id+'/'+patches[0]['step_id']) and x.get('type') == 'MESH']
    require(len(rows) == 1, 'Actual edit receipt must resolve exactly one target object')
    row = rows[0]
    require(len(row.get('modifiers', [])) == 1, 'Edited object must have exactly one actual modifier')
    from .subdivision_source import declared_modifier_settings
    profile = declared_modifier_settings(row['modifiers'][0])
    c._validate(profile, subdivision._SOURCE_MODIFIER)
    edits = [x['evidence']['structure_edit']['evidence'] for x in core['steps']
             if x.get('evidence', {}).get('structure_edit', {}).get('status') == 'pass']
    require(len(edits) == 1, 'The edit receipt must contain one native edit witness')
    require(_ref(edits[0]) in [_ref(value) for key,value in cp['artifacts'].items() if key.startswith('structure_edit_')],
            'Native edit witness is not pinned by the immutable child checkpoint')
    witness = _read(edits[0], job)
    expected_version = 'sparse-native-insertion/1.0' if insertion else '1.0'
    require(witness.get('schema_version') == expected_version and witness.get('status') == 'pass' and
            witness.get('before_binding', {}).get('object_id') == row['object_id'] and
            witness.get('after_binding', {}).get('object_id') == row['object_id'], 'Native edit witness differs from actual target')
    if not insertion:
        require(all(witness.get(k) == 'pass' for k in ('parameter_binding','parameter_coordinates',
                'protected_coordinate_axes','native_identity_context','typed_total_continuations')),
                'Parameter edit invariants are not verified')
        require(witness.get('impact', {}).get('status') == 'pass' and
                witness['impact'].get('scope') == 'raw_source_cage_only', 'Parameter impact evidence is missing')
        from .structure_edit import NATIVE_INVARIANTS
        require(all(witness['before_binding'][k] == witness['after_binding'][k] for k in NATIVE_INVARIANTS),
                'Parameter edit changed native identity or topology epoch')
        # Recheck the exact request against the source state pinned in the accepted edit fingerprints.
        plan_ref = artifact_descriptor(job/'plan.json', root=job)
        planned = _read(plan_ref, job)
        verify_edit_plan_binding(planned, report['fingerprints'], request)
        category = parameter_edit_scope(planned, report['fingerprints']['targets']['source_state'])
    else:
        category = 'insert_sparse_strip'
    return {'source': candidate, 'target': {'object_id': row['object_id']},
            'evaluation_profile': {'mode': subdivision.SPARSE_DIAGNOSTIC_PROFILE, 'expected_source_modifier': profile},
            'before_binding': witness['before_binding'], 'after_binding': witness['after_binding'],
            'edit_category': category, 'impact': copy.deepcopy(witness.get('impact', {'status':'not_applicable_insertion'})),
            'native_edit_witness': _ref(edits[0]), 'independent_reopen': cp['verification']['evidence'],
            'accepted_checkpoint': cp['receipt'], 'evidence_artifacts': _refs(core,root=job) + _refs(cp,root=job) + ([] if insertion else [plan_ref])}


def _accept_child(stage, child, request, bound, store, budget=None):
    job = store.job_dir(child['job_id'])
    require(child['status'] == 'succeeded', 'Nonterminal or unsuccessful child cannot be accepted')
    require(read_json(job/'request.json') == request, 'Child saved request differs from exact launched request')
    report_ref = artifact_descriptor(job/'report.json', root=job)
    report = _read(report_ref, job)
    result_ref = artifact_descriptor(job/'result.json', root=job)
    _read(result_ref, job)
    expected_impl = bound['implementation']
    actual_impl = report.get('implementation') if stage != 'edit' else report.get('fingerprints', {}).get('implementation')
    require(actual_impl == expected_impl, 'Child plugin/runtime identity differs from workflow')
    accepted = _edit(report, request, job) if stage == 'edit' else _diagnosis(report, request, job, budget)
    processes = sorted(job.rglob('*-process.json'))
    require(len(processes) == (4 if stage == 'edit' else 1), 'Native process count differs from bounded work unit')
    for path in processes:
        evidence = _read(artifact_descriptor(path), job)
        require(evidence.get('status') == 'succeeded' and evidence.get('returncode') == 0 and
                evidence.get('worker_exit_observed') is True and evidence.get('observed_descendants_terminal') is True and
                evidence.get('cleanup_review_required') is False and evidence.get('parent_death_watchdog', {}).get('ready') is True, 'Native worker terminal/cleanup evidence is incomplete')
    artifacts = _refs(report, root=job) + _refs(accepted,root=job) + [report_ref, result_ref, artifact_descriptor(job/'request.json', root=job), artifact_descriptor(job/'job.json', root=job)]
    artifacts += [artifact_descriptor(path, root=job) for path in processes]
    return {'stage': stage, 'child_job_id': child['job_id'], 'accepted': accepted, 'artifacts': artifacts}


def _checkpoint(job, stage, record):
    path = job/('accepted-'+stage+'.json'); commit = job/('committed-'+stage+'.json')
    _atomic_json(path, record, exclusive=True)
    _atomic_json(commit, {'version': VERSION, 'receipt': artifact_descriptor(path, root=job)}, exclusive=True)
    _fsync_dir(job)


def _accepted(job, stage):
    path = job/('accepted-'+stage+'.json'); commit = job/('committed-'+stage+'.json')
    if not path.exists() and not commit.exists(): return None
    require(path.exists() and commit.exists(), 'Partially committed stage requires reconciliation', 'EDIT_REVIEW_RECONCILE')
    record = _read(read_json(commit)['receipt'], job)
    require(record['stage'] == stage, 'Stage checkpoint identity differs')
    for artifact in record['artifacts']:
        verify_descriptor(artifact, root=job)
    return record


def _child_request(stage, request, accepted):
    from .host import derived_request_id
    p = request['params']
    result = copy.deepcopy(p['edit_request'] if stage == 'edit' else p['before_diagnosis'])
    result['params']['request_id'] = derived_request_id(p['request_id'], 'edit-review:'+stage)
    if stage == 'after':
        actual = accepted['edit']['accepted']
        before = accepted['before']['accepted']
        require(actual['target'] == before['target'] and actual['before_binding'] == before['native_binding'],
                'Before diagnosis does not identify the actual edit source object and native binding')
        require(actual['evaluation_profile'] == before['evaluation_profile'], 'Actual edited profile changed; paired measurement is invalid')
        if p['after_panel_reference'] is not None:
            require(reference_edit_category(p['before_diagnosis']['params']['panel_reference'], p['after_panel_reference']) == actual.get('edit_category'),
                    'After nominal reference category differs from the actual parameter edit')
            result['params']['panel_reference'] = copy.deepcopy(p['after_panel_reference'])
        result['params'].update(source={'file': actual['source']['file'], 'expected_sha256': actual['source']['sha256'], 'bytes': actual['source']['bytes']},
                                target=copy.deepcopy(actual['target']), evaluation_profile=copy.deepcopy(actual['evaluation_profile']))
    return result


def _compare(accepted, job, budget, request):
    from .subdivision_compare import compare_exports
    before, after = accepted['before']['accepted'], accepted['after']['accepted']
    require(before['evaluation_profile'] == after['evaluation_profile'], 'Paired profiles differ')
    require(after['native_binding'] == accepted['edit']['accepted']['after_binding'],
            'After diagnosis differs from the independently saved edit native binding')
    baselines = [_geometry(x['geometries']['0'], job, budget) for x in (before, after)]
    reports = []
    last_sample=[0.0]
    def check_cancel():
        if budget.cancel_requested(): return True
        if budget.remaining_seconds <= 0: raise BudgetExceeded('wall_seconds',budget.elapsed_seconds,budget.limits.wall_seconds)
        now=time.monotonic()
        if now-last_sample[0]>=.2:
            budget.check();last_sample[0]=now
        return False
    for level in range(4):
        budget.check()
        geometries = [_geometry(x['geometries'][str(level)], job, budget) for x in (before, after)]
        report = compare_exports(*geometries, before_source_geometry=baselines[0], after_source_geometry=baselines[1],
                                 limits=request['params']['comparison_limits'],
                                 reference_tolerance_mm=request['params']['before_diagnosis']['params']['panel_reference']['tolerance_mm'],
                                 check_cancel=check_cancel)
        reports.append({'level': level, 'comparison': report})
    path = job/'comparison.json'
    _atomic_json(path, {'workflow_version': VERSION, 'execution': {'status': 'succeeded'},
                        'shape_review': {'status': 'pending', 'visual':'not_run','highlight':'not_run',
                                         'continuous_limit_surface':'not_run','calibrated_silhouette':'not_run',
                                         'reference_tolerance_role':'finite_sample_reference_only'}, 'production_qualification': 'not_asserted',
                        'edit_category': accepted['edit']['accepted'].get('edit_category'),
                        'source_cage_impact': accepted['edit']['accepted'].get('impact'),
                        'source_pair': {'before': before['source'], 'after': after['source']}, 'levels': reports}, exclusive=True)
    return {'stage': 'compare', 'accepted': {'comparison': artifact_descriptor(path, root=job)},
            'artifacts': [artifact_descriptor(path, root=job)]}


def run(request_path, root, blender, *, resume=False, started=None):
    """Repeated run/resume adopts only exact terminal child evidence; never retries."""
    from . import host
    request = validate_request(read_json(Path(request_path).absolute(), max_bytes=c.MAX_BYTES))
    bound = _binding(request, blender); binding_sha = c.fingerprint(bound)
    store = host.store_for(root)
    rid = host.derived_request_id(request['params']['request_id'], 'edit-review')
    record = store.recover(rid, binding_sha) if resume else store.submit(rid, binding_sha)
    job = store.job_dir(record['job_id']); path = job/'workflow-binding.json'
    fd = os.open(job/'workflow.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return _result(job, 'reconcile_required', reason='Another supervisor owns this workflow')
        if path.exists():
            require(c.fingerprint(read_json(path)) == binding_sha, 'Workflow binding snapshot changed')
        else:
            require(not record.get('reused') and not resume, 'Uncommitted workflow admission needs reconciliation', 'EDIT_REVIEW_RECONCILE')
            _atomic_json(path, bound, exclusive=True)
        try:
            return _run_locked(request, bound, job, store, blender, started)
        except Exception as exc:
            result=_result(job, 'reconcile_required' if getattr(exc,'code','') == 'EDIT_REVIEW_RECONCILE' else 'failed',error=host.error_dict(exc))
            _record_outcome(job, store, result)
            return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def _result(job, status, **extra):
    return {'workflow_version': VERSION, 'job_id': job.name, 'status': status,
            'execution': {'status': status}, 'shape_review': {'status': 'pending'},
            'production_qualification': 'not_asserted', **extra}


def _run_locked(request, bound, job, store, blender, started):
    result = _execute_locked(request, bound, job, store, blender, started)
    _record_outcome(job, store, result)
    return result


def _record_outcome(job, store, result):
    """Persist current validation without rewriting an earlier terminal receipt."""
    _atomic_json(job/'workflow-report.json', result)
    state = store.status(job.name)['status']
    if result['status'] in ('failed','cancelled') and state not in store.TERMINAL:
        _atomic_json(job/'result.json', result)
        store.update(job.name,status=result['status'])


def _execute_locked(request, bound, job, store, blender, started):
    from . import host
    previous_status = store.status(job.name)['status']
    if previous_status in ('failed', 'cancelled', 'timed_out'):
        # In particular, a comparison commit cannot promote a later budget
        # failure to success. A failed workflow is not an automatic retry.
        path = job/'result.json'
        require(path.is_file(), 'Terminal workflow lacks its final receipt', 'EDIT_REVIEW_RECONCILE')
        previous = read_json(path)
        require(previous.get('status') == previous_status,
                'Terminal workflow and final receipt disagree', 'EDIT_REVIEW_RECONCILE')
        return previous
    accepted = {}
    for stage in STAGES:
        value = _accepted(job, stage)
        if value is not None:
            require(len(accepted) == STAGES.index(stage), 'Stage checkpoint sequence has a gap')
            require(value.get('binding_sha256') == c.fingerprint(bound), 'Stage input binding differs')
            accepted[stage] = value
    usage = read_json(job/'usage.json') if (job/'usage.json').exists() else None
    if accepted or any((job/('launch-'+stage+'.json')).exists() for stage in STAGES):
        require(usage is not None,'Cumulative usage record is missing')
    for checkpoint in accepted.values():
        prior=checkpoint.get('resource_usage')
        require(isinstance(prior,dict),'Checkpoint cumulative usage is missing')
        for key in ('wall_seconds_observed','native_processes_started','peak_tree_rss_bytes_observed','peak_disk_bytes_observed'):
            require(usage.get(key,-1)>=prior[key],'Cumulative usage went backwards', 'EDIT_REVIEW_BINDING')
    if 'compare' in accepted:
        result=_result(job, 'succeeded', comparison=accepted['compare']['accepted']['comparison'],
                       candidate=accepted['edit']['accepted']['source'], resumed=True)
        _atomic_json(job/'result.json',result)
        _atomic_json(job/'workflow-report.json',result)
        if store.status(job.name)['status'] != 'succeeded': store.update(job.name,status='succeeded')
        return result
    budget = WorkflowBudget(job, request['params']['budgets'], usage=usage, started=started,
                            cancel_check=lambda: store.status(job.name)['cancel_requested'])
    child_root = job/'children'; children = host.store_for(child_root)
    if store.status(job.name)['status'] == 'queued': store.update(job.name, status='running')
    try:
        for stage in STAGES:
            if stage in accepted: continue
            if budget.cancel_requested(): fail('Workflow cancellation requested','CANCELLED')
            budget.check()
            require(_binding(request, blender) == bound, 'Source/reference/plugin/runtime binding changed')
            if stage == 'compare':
                value = _compare(accepted, job, budget, request)
            else:
                child_request = _child_request(stage, request, accepted)
                launch = job/('launch-'+stage+'.json'); child_path = job/(stage+'-request.json')
                if launch.exists():
                    entry = read_json(launch)
                    require(entry['request_sha256'] == c.fingerprint(child_request), 'Launched child request differs')
                    verify_descriptor(entry['request_file'], root=job)
                    try: child = children.recover(child_request['params']['request_id'], entry['admission_sha256'])
                    except Exception as exc:
                        return _result(job, 'reconcile_required', stage=stage, reason=str(exc), metrics=budget.report())
                else:
                    _atomic_json(child_path, child_request, exclusive=True)
                    if stage == 'edit' and request['params']['reference_approval'] is not None:
                        admission = c.fingerprint({'request': child_request, 'reference_approval': bound['reference_gate']})
                    else: admission = c.fingerprint(child_request)
                    entry = {'request_sha256': c.fingerprint(child_request), 'admission_sha256': admission,
                             'request_file': artifact_descriptor(child_path, root=job)}
                    _atomic_json(launch, entry, exclusive=True)
                    try:
                        if stage == 'edit':
                            host.submit(child_path, child_root, blender, background=False, started=time.monotonic(),
                                        shared_budget=budget, reference_approval=request['params']['reference_approval'])
                        else:
                            host.read_only_action('subdivision.diagnose', child_path, child_root, blender,
                                                  time.monotonic(), shared_budget=budget)
                    except Exception:
                        # A missing response is not evidence that submission failed.
                        # Inspect this exact identity once; never submit it again.
                        pass
                    try: child = children.recover(child_request['params']['request_id'], entry['admission_sha256'])
                    except Exception as exc:
                        return _result(job,'reconcile_required',stage=stage,reason=str(exc),metrics=budget.report())
                if child['status'] not in host.TERMINAL:
                    return _result(job, 'reconcile_required', stage=stage, child_job_id=child['job_id'], metrics=budget.report())
                if child['status'] != 'succeeded':
                    return _result(job, 'cancelled' if child['status']=='cancelled' else 'failed', stage=stage, child_job_id=child['job_id'],
                                   reason='Child failed; existing request is never automatically resubmitted', metrics=budget.report())
                value = _accept_child(stage, child, child_request, bound, children, budget)
            budget.check()
            require(_binding(request, blender) == bound, 'Binding changed before stage checkpoint')
            value['binding_sha256'] = c.fingerprint(bound)
            budget.save()
            value['resource_usage'] = read_json(job/'usage.json')
            _checkpoint(job, stage, value); accepted[stage] = value
        result = _result(job, 'succeeded', comparison=accepted['compare']['accepted']['comparison'],
                         candidate=accepted['edit']['accepted']['source'], metrics=budget.report())
        _atomic_json(job/'result.json', result)
        _atomic_json(job/'workflow-report.json', result)
        budget.check()
        store.update(job.name, status='succeeded')
        return result
    except Exception as exc:
        return _result(job, 'reconcile_required' if getattr(exc, 'code', '') == 'EDIT_REVIEW_RECONCILE' else 'cancelled' if getattr(exc,'code','') in ('CANCELLED','SUBDIVISION_COMPARE_CANCELLED') else 'failed',
                       error=host.error_dict(exc), completed_stages=list(accepted), metrics=budget.report())
    finally:
        budget.save()
