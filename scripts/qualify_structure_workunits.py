#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PREPARED, NOT QUALIFIED: gated M1 qualification through the existing host CLI.

Default --describe is host-only and creates nothing. Native execution requires
--execute, an externally frozen exact inventory, its SHA, the exact Blender SHA,
a never-created output root, and a separate externally authorized scope record.
This program does NOT manufacture approval. The caller must verify that the
referenced user decision really authorizes the exact bytes/runtime/output scope;
checking a JSON approval record is an integrity check, not authentication.

Freeze format: {"format":"m1-workunit-freeze/1", "code_files": {relative: sha256},
               "blender": {"file": absolute_binary_path, "sha256": sha256}}
The inventory contains every regular package file except __pycache__/*.pyc and
.git internals, including this driver and both fixture/test modules. No symlinks.
Authorization format: {"format":"m1-workunit-authorization/1", "decision":"approved",
 "approval_reference": exact_external_user_decision_reference,
 "freeze_sha256": sha256, "blender_sha256": sha256, "output_root": absolute_new_path,
 "scope":"neutral_m1_workunits_and_level_diagnosis", "job_limit":12,
 "blender_invocation_limit":35}

Correct existing CLI argument order:
  execution-copy/hardsurface-cli --jobs-dir execution-copy/jobs-store
    --blender /absolute/blender hardsurface run --request /absolute/request.json
  ... hardsurface resume JOB_ID --checkpoint attempt-01-step-01
  ... hardsurface subdivision diagnose --request /absolute/diagnosis.json

All native work uses existing host supervision, ProtectedInputs, JobStore,
CheckpointStore, native core, and independent-reader verification. No bpy import,
plugin enable/install, alternate modeling runner, overwrite, deletion or retry.
The byte-identical execution copy places jobs within the existing host ROOT
allowlist. All generated files, HOME and temporary files are in the approved root.
"""
from __future__ import annotations
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from hardsurface import contract
from hardsurface.io import checked_path, descriptor, read_json, read_json_reference, checkpoint_evidence_artifacts, verify_descriptor
from hardsurface.recovery import CheckpointStore
from structure_workunit_fixtures import (CASES, CHECKPOINT_ID, STEP_KEYS, WORKUNIT_COUNT, EXPECTED_SOURCE_MODIFIER,
    NATIVE_PROCESS_COUNT, WALL_SECONDS, initial_request, patch_request, diagnosis_request,
    preparation_summary)

SCOPE = 'neutral_m1_workunits_and_level_diagnosis'
SHA = re.compile(r'[0-9a-f]{64}')
SIGNATURES = ('geometry_signature', 'topology_signature', 'attribute_signature', 'structure_signature')


class QualificationFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise QualificationFailure(message)


def file_sha(path):
    return descriptor(checked_path(path))['sha256']


def inventory(root, *, owned_runtime=False):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.git' in relative.parts or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if owned_runtime and relative.parts[0] == 'jobs-store':
            continue
        require(not path.is_symlink(), 'Code inventory rejects symbolic links: ' + str(path))
        if path.is_file():
            result[relative.as_posix()] = file_sha(path)
    return result


def write_new(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    require(len(raw) <= 8 * 1024**2, 'Evidence exceeds the bounded JSON size')
    with open(path, 'xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return descriptor(path)


def gate(source_root, freeze_path, freeze_sha256, approval_path, approval_sha256,
         approval_reference, blender, blender_sha256, output_root):
    """Read-only preflight. No output directory, code copy or process is created."""
    for expected in (freeze_sha256, approval_sha256, blender_sha256):
        require(isinstance(expected, str) and SHA.fullmatch(expected), 'Explicit exact SHA-256 required')
    source_root = Path(source_root).absolute()
    output_root = checked_path(output_root, exists=False)
    blender = checked_path(blender)
    require(not output_root.exists() and output_root.parent.is_dir(), 'A new root with an existing parent is required')
    require(not output_root.is_relative_to(source_root), 'Output must not be nested inside the frozen source')
    require(file_sha(freeze_path) == freeze_sha256, 'Frozen inventory SHA mismatch')
    require(file_sha(approval_path) == approval_sha256, 'External approval record SHA mismatch')
    freeze = read_json(freeze_path)
    expected = freeze.get('code_files', {})
    require(set(freeze) == {'format', 'code_files', 'blender'} and freeze['format'] == 'm1-workunit-freeze/1', 'Unknown freeze format')
    require(isinstance(expected, dict) and expected and all(isinstance(k, str) and isinstance(v, str) and SHA.fullmatch(v) for k, v in expected.items()), 'Exact file hash map required')
    require(inventory(source_root) == expected, 'Frozen package inventory mismatch or unlisted files')
    require(freeze['blender'] == {'file': str(blender), 'sha256': blender_sha256}, 'Freeze names a different runtime')
    require(file_sha(blender) == blender_sha256, 'Runtime binary SHA mismatch')
    require(os.access(blender, os.X_OK), 'Runtime must already be executable; no installation is performed')
    require(isinstance(approval_reference, str) and 1 <= len(approval_reference) <= 4096, 'Verified external user-decision reference required')
    approval = read_json(approval_path)
    approved_scope = {'format': 'm1-workunit-authorization/1', 'decision': 'approved',
        'approval_reference': approval_reference, 'freeze_sha256': freeze_sha256,
        'blender_sha256': blender_sha256, 'output_root': str(output_root), 'scope': SCOPE,
        'job_limit': WORKUNIT_COUNT, 'blender_invocation_limit': NATIVE_PROCESS_COUNT}
    require(approval == approved_scope, 'External approval does not match this exact bounded run')
    require(not os.environ.get('HS_TEST_FAULT'), 'Unexpected host fault injection must be absent')
    return {'freeze': freeze, 'freeze_record': descriptor(freeze_path),
            'authorization_record': descriptor(approval_path), 'authorization': approval,
            'source_root': str(source_root), 'output_root': str(output_root), 'blender': descriptor(blender)}


def copy_execution_source(source_root, output_root, frozen):
    destination = output_root / 'execution-copy'
    destination.mkdir()
    for relative in frozen:
        source = source_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        require(not target.exists(), 'Execution-copy collision')
        with open(source, 'rb') as src, open(target, 'xb') as dst:
            shutil.copyfileobj(src, dst)
        target.chmod(source.stat().st_mode & 0o777)
        require(file_sha(target) == frozen[relative], 'Source changed during execution copy')
    require(inventory(destination) == frozen, 'Execution copy is not byte-identical')
    require(inventory(source_root) == frozen, 'Source changed while preparing execution copy')
    return destination


def cli_command(execution_root, blender, action, *, request=None, resume_job=None):
    command = [str(execution_root / 'hardsurface-cli'), '--jobs-dir', str(execution_root / 'jobs-store'),
               '--blender', str(blender), 'hardsurface']
    if action == 'resume':
        require(resume_job is not None and re.fullmatch('[0-9a-f]{32}', resume_job), 'Verified source job ID required')
        return command + ['resume', resume_job, '--checkpoint', CHECKPOINT_ID]
    require(request is not None, 'Request required')
    return command + (['subdivision', 'diagnose'] if action == 'diagnose' else ['run']) + ['--request', str(request)]


def invoke_cli(command, *, cwd, env, log_root, name, runner=None):
    """One foreground existing CLI call, no retries; mockable without any native run."""
    runner = subprocess.run if runner is None else runner
    record = {'command': command, 'cwd': str(cwd), 'timeout_seconds': WALL_SECONDS + 30}
    write_new(log_root / (name + '-invocation.json'), record)
    stdout = log_root / (name + '-stdout.json')
    stderr = log_root / (name + '-stderr.log')
    started = time.monotonic()
    try:
        with open(stdout, 'xb') as out, open(stderr, 'xb') as err:
            process = runner(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, timeout=WALL_SECONDS + 30, check=False)
    except BaseException as exc:
        write_new(log_root / (name + '-failure.json'), {'status': 'stopped', 'type': type(exc).__name__,
            'message': str(exc), 'seconds': time.monotonic() - started,
            'termination': 'Inspect host/watchdog evidence before any further work; no retry performed'})
        raise
    result = {'returncode': process.returncode, 'seconds': time.monotonic() - started,
              'stdout': descriptor(stdout), 'stderr': descriptor(stderr)}
    write_new(log_root / (name + '-process.json'), result)
    require(process.returncode == 0, 'Host CLI failed: ' + name + '; inspect retained logs, no retry')
    envelope = read_json(stdout)
    require(envelope.get('ok') is True and isinstance(envelope.get('data'), dict), 'Malformed host CLI response')
    require(envelope['data'].get('status') == 'succeeded', 'Host work unit did not succeed')
    return envelope['data']


def verify_native_rows(rows, registry):
    require(isinstance(rows, list) and rows and isinstance(registry, dict), 'Actual native structural evidence required')
    require(len(rows) == len(registry) and {row.get('binding', {}).get('object_id') for row in rows} == set(registry), 'Native evidence must cover each registry object exactly once')
    for row in rows:
        binding = row.get('binding', {})
        kernel = row.get('kernel_report', {})
        require(row.get('status') == 'pass' and registry.get(binding.get('object_id')) == binding,
                'Native report differs from the external registry')
        require(kernel.get('status') == 'pass' and all(isinstance(binding.get(k), str) and SHA.fullmatch(binding[k]) and kernel.get(k) == binding[k] for k in SIGNATURES),
                'Actual native kernel signatures do not bind the registry')


def verify_reopen(data, registry):
    require(data.get('independent_reopen') is True, 'Real independent reopen required')
    producer, verifier = data.get('producer_identity'), data.get('verifier_identity')
    require(isinstance(producer, str) and isinstance(verifier, str) and producer.startswith('blender:') and verifier.startswith('blender:') and producer != verifier,
            'Producer and saved-file verifier must be distinct native processes')
    require(all(data.get('checks', {}).get(key) == 'pass' for key in ('technical', 'preservation', 'dependency_reproduction', 'state_consistency')), 'Independent reopen checks incomplete')
    verify_native_rows(data.get('structure_identity', {}).get('evidence'), registry)


def owned_job(execution_root, data):
    job_id = data.get('job_id')
    require(isinstance(job_id, str) and re.fullmatch('[0-9a-f]{32}', job_id), 'Invalid returned job identity')
    path = execution_root / 'jobs-store' / 'jobs' / job_id
    checked_path(path / 'report.json')
    return path


def model_evidence(execution_root, data, case):
    job = owned_job(execution_root, data)
    report = read_json(job / 'report.json')
    require(report.get('status') == 'succeeded' and report.get('domain_outcome') == 'pass', 'Full model report failed')
    require(report.get('topology_diagnostics', {}).get('status') == 'disabled_by_request', 'Unexpected wire diagnostics')
    core = read_json_reference(report['core_report'], root=job)
    artifacts = checkpoint_evidence_artifacts(core, root=job)
    registry = core['scene_snapshot']['structure_registry']
    require(len(registry) == 1, 'Fixture must have exactly one registered authored panel')
    require(core.get('completed_step_keys') == STEP_KEYS, 'Complete panel + checkpoint steps required')
    required = report.get('required_checks', {})
    for check in ('structure_identity', 'quad_topology', 'preservation', 'reopen', 'design_dimensions'):
        require(required.get(check, {}).get('status') == 'pass', 'Missing required ' + check)
    require(all(report['source_protection'][key].get('accepted') is True for key in ('staged_snapshot_guarded', 'resources_guarded')), 'Completed source guard intervals required')
    if case['source'] and case['action'] != 'resume':
        require(required.get('source_preserved', {}).get('status') == 'pass' and report.get('original_final_observations'), 'Saved-source preservation evidence required')
    native = [read_json_reference(row['details'], root=job) for row in core['checks']['structure_identity']['evidence']]
    verify_native_rows(native, registry)
    for row in native:
        require(row.get('native_extraction', {}).get('status') == 'pass' and row['native_extraction'].get('source') == 'raw object.data' and row.get('identity_binding_status') == 'bound', 'Host-only or unbound evidence refused')
    reopen = read_json(job / 'reopen-result.json')
    verify_reopen(reopen, registry)
    require(reopen['candidate'] == core['candidate'], 'Reopen must bind the saved candidate bytes')
    checkpoints = []
    store = CheckpointStore(job)
    expected_checkpoints = ['final'] if case['action'] == 'resume' else [CHECKPOINT_ID, 'final']
    require([cp['checkpoint_id'] for cp in report['checkpoints']] == expected_checkpoints, 'Expected intermediate/final receipt sequence missing')
    for entry in report['checkpoints']:
        accepted = store.resume(report['fingerprints'], checkpoint_id=entry['checkpoint_id'], expected_receipt=entry['receipt'])
        cp_reopen = read_json_reference(accepted['verification']['evidence'], root=job)
        # The fixed fixture has one panel; an intermediate checkpoint has the
        # same bound registry as the final save. No reconstruction or migration.
        verify_reopen(cp_reopen, registry)
        checkpoints.append({'checkpoint_id': accepted['checkpoint_id'], 'receipt': accepted['receipt'],
                            'completed_steps': accepted['completed_steps'], 'independent_reopen': accepted['verification']})
    state = read_json_reference(core['design_state'], root=job)
    require(contract.fingerprint(state) == core['design_state_sha256'], 'Saved design sidecar hash mismatch')
    edits = [step['evidence']['structure_edit'] for step in core['steps'] if step.get('evidence', {}).get('structure_edit')]
    if case['source'] and case['action'] == 'run':
        require(len(edits) == 1 and edits[0].get('status') == 'pass', 'Native saved-source rebuild edit proof required')
        edit = read_json_reference(edits[0]['evidence'], root=job)
        require(edit.get('status') == 'pass' and edit.get('datum_policy') == case['datum'] and edit.get('kernel_edit', {}).get('status') == 'pass', 'Actual native edit contract failed')
        require(all(edit.get(key) == 'pass' for key in ('parameter_binding', 'parameter_coordinates', 'protected_coordinate_axes', 'native_identity_context', 'typed_total_continuations')), 'Incomplete native edit witnesses')
    if case['action'] == 'resume':
        require(report.get('resumed_from', {}).get('checkpoint_id') == CHECKPOINT_ID, 'Actual checkpoint continuation absent')
        require(all(step.get('reused_checkpoint') is True for step in core['steps']), 'Resume unexpectedly rebuilt a checkpoint step')
    processes = process_evidence(job, case['native_processes'])
    candidate = verify_descriptor(core['candidate'], expected=False)
    return {'job_id': data['job_id'], 'candidate': candidate, 'state': state, 'registry': registry,
        'report': descriptor(job / 'report.json'), 'core_report': report['core_report'],
        'actual_native_evidence': [row['details'] for row in core['checks']['structure_identity']['evidence']],
        'checkpoint_evidence_artifacts': artifacts, 'checkpoints': checkpoints,
        'reopen': descriptor(job / 'reopen-result.json'), 'source_protection': report['source_protection'],
        'original_final_observations': report.get('original_final_observations', []), 'edit_proofs': edits,
        'native_processes': processes, 'resumed_from': report.get('resumed_from')}


def process_evidence(job, expected_count):
    files = sorted(job.glob('*-process.json'))
    require(len(files) == expected_count, 'Native process invocation count differs from reviewed flow')
    result = []
    for path in files:
        item = read_json(path)
        require(item.get('status') == 'succeeded' and item.get('returncode') == 0 and item.get('worker_exit_observed') is True,
                'A native worker did not terminate successfully')
        require(item.get('cleanup_review_required') is False and item.get('observed_descendants_terminal') is True and item.get('parent_death_watchdog', {}).get('ready') is True,
                'Owned process cleanup/watchdog evidence incomplete')
        result.append(descriptor(path))
    return result


def diagnosis_evidence(execution_root, data, candidate):
    job = owned_job(execution_root, data)
    report = read_json(job / 'report.json')
    require(report.get('status') == 'succeeded', 'Diagnosis execution failed')
    require(report.get('source_original') == candidate and report.get('source_guard', {}).get('accepted') is True, 'Diagnosis did not guard the exact candidate')
    for key in ('blend_save_performed', 'saved_candidate_modified', 'source_geometry_modified', 'source_transforms_modified'):
        require(report.get(key) is False, 'Diagnosis modified its source')
    require(report.get('previews') == [] and report.get('outputs') == [], 'No rendering or geometry export was authorized')
    require(report.get('source_signature_sha256') == report.get('source_signature_after_sha256'), 'Diagnostic source signature changed')
    require(report.get('authored_control_loops', {}).get('status') == 'pass', 'Actual authored control loops failed')
    profile = report.get('backend', {}).get('evaluation_profile', {})
    require(profile.get('mode') == 'source_bound_qualification_v1' and profile.get('qualification') == 'source_binding_verified', 'Source-bound evaluation profile is required; generic/legacy diagnostics cannot qualify')
    require(profile.get('source_scene_evaluation', {}).get('use_simplify') is False, 'Source scene subdivision simplification must be rejected before isolated sampling')
    binding = profile.get('modifier_profile', {})
    require(binding.get('status') == 'pass' and binding.get('expected_source_modifier') == EXPECTED_SOURCE_MODIFIER, 'Explicit expected complete source modifier profile changed')
    from hardsurface.subdivision_source import declared_modifier_settings
    actual_source = binding.get('actual_source_modifier', {})
    require(declared_modifier_settings(actual_source) == EXPECTED_SOURCE_MODIFIER, 'Actual source modifier differs from reviewed complete profile')
    semantic_source = profile.get('semantic_source', {})
    require(semantic_source.get('status') == 'validated' and semantic_source.get('identity_binding_status') == 'bound' and semantic_source.get('external_registry_verified') is True, 'Actual semantic source must be authenticated against the saved scene registry')
    require(semantic_source.get('authored_bore_sector_identity_matches') is True, 'Actual bore domain must match authenticated authored bore sectors')
    require(semantic_source.get('actual_source_modifier') == actual_source, 'Semantic source and evaluation profile bindings differ')
    rows = report.get('levels', [])
    require([row['level'] for row in rows] == [0, 1, 2, 3], 'All reviewed levels must be sampled')
    for row in rows:
        require(row.get('reference_check', {}).get('tolerance_mm') == .05, 'Reference tolerance changed')
        expected_clone = deepcopy(actual_source)
        expected_clone.update(levels=row['level'], render_levels=row['level'], show_viewport=bool(row['level']), show_render=bool(row['level']))
        sampled_profile = row.get('source_bound_evaluation', {})
        require(sampled_profile.get('status') == 'pass' and sampled_profile.get('actual_clone_modifier') == expected_clone, 'Actual sampled modifier profile is not source-bound')
        require(row.get('actual_modifier_stack') == [expected_clone], 'Reported actual sampled modifier stack differs from verified source-bound profile')
        transport = row.get('semantic_face_transport', {})
        require(transport.get('status') == 'validated' and transport.get('source') == semantic_source and transport.get('level') == row['level'], 'Evaluated semantic face transport is not bound to actual source')
        require(transport.get('all_source_face_descendant_counts_match') is True and transport.get('parent_surface_labels_match') is True and transport.get('expected_descendants_per_source_face') == 4**row['level'], 'Evaluated semantic descendant counts or label correspondence failed')
        require(transport.get('parent_patch_topology', {}).get('status') == 'pass' and transport.get('parent_patch_topology', {}).get('oriented_source_neighbour_cycles_match') is True, 'Evaluated parent patch topology transport is unverified')
        reference = row['reference_check']
        require(reference.get('measurement_profile') == 'semantic_bore_v1' and reference.get('semantic_bore_membership', {}).get('status') == 'validated' and reference.get('source_bound_semantics') == transport, 'Reference measurement lacks authenticated actual semantic bore membership')
        if row['level'] in (2, 3):
            require(row['reference_check'].get('status') == 'pass', 'Required L2/L3 finite reference samples failed')
    verify_descriptor(candidate, expected=False)
    return {'job_id': data['job_id'], 'report': descriptor(job / 'report.json'),
        'levels': [{'level': row['level'], 'role': 'required' if row['level'] in (2, 3) else 'diagnostic',
                    'reference_check': row['reference_check']} for row in rows],
        'native_processes': process_evidence(job, 1), 'source_guard': report['source_guard'],
        'evaluation_profile': profile,
        'visual_acceptance': 'not_run'}


def execute_run(verified, *, runner=None):
    """Called only after external authorization preflight; never used by tests."""
    output = Path(verified['output_root'])
    source = Path(verified['source_root'])
    frozen = verified['freeze']['code_files']
    blender = Path(verified['blender']['file'])
    output.mkdir(mode=0o700)
    summary = {'status': 'running', 'qualification': 'not_run', 'authority': verified,
               'plan': preparation_summary(), 'cases': [], 'completed_cli_calls': 0}
    write_new(output / 'reviewed-scope.json', summary)
    sources = {}
    execution = None
    try:
        execution = copy_execution_source(source, output, frozen)
        for name in ('requests', 'logs', 'tmp', 'home'):
            (output / name).mkdir()
        env = {**os.environ, 'HOME': str(output / 'home'), 'TMPDIR': str(output / 'tmp'),
               'XDG_CONFIG_HOME': str(output / 'home' / 'config'), 'XDG_CACHE_HOME': str(output / 'home' / 'cache'),
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'BLENDERCTL_PYTHON': sys.executable}
        completed = {}
        def recheck():
            require(inventory(source) == frozen and inventory(execution, owned_runtime=True) == frozen, 'Frozen source/execution bytes changed')
            require(descriptor(blender) == verified['blender'], 'Runtime changed')
            for value in sources.values():
                verify_descriptor(value, expected=False)
        def call(name, action, request=None, resume_job=None):
            require(summary['completed_cli_calls'] < WORKUNIT_COUNT, 'Reviewed CLI call limit exceeded')
            recheck()
            path = output / 'requests' / (name + '.json') if request else None
            if request:
                write_new(path, request)
            response = invoke_cli(cli_command(execution, blender, action, request=path, resume_job=resume_job),
                cwd=execution, env=env, log_root=output / 'logs', name=name, runner=runner)
            summary['completed_cli_calls'] += 1
            recheck()
            return response
        for case in CASES:
            name = case['name']
            previous = completed.get(case['source'])
            if case['action'] == 'resume':
                response = call(name, 'resume', resume_job=previous['job_id'])
            else:
                request = (patch_request(name, case['datum'], previous['candidate'], previous['state'], case['values'])
                    if previous else initial_request(name, case['datum']))
                response = call(name, 'run', request)
            evidence = model_evidence(execution, response, case)
            if previous:
                old, new = previous['registry'], evidence['registry']
                require(set(old) == set(new), 'Stable structural object identity changed during edit/resume')
                oid = next(iter(old))
                require(old[oid]['data_id'] == new[oid]['data_id'], 'Stable structural data identity changed')
                if case['action'] == 'resume':
                    require(old == new and previous['state'] == evidence['state'], 'Resume changed accepted structural identity or design state')
            completed[name] = evidence
            sources[name] = evidence['candidate']
            oid = next(iter(evidence['registry']))
            diag_request = diagnosis_request(name, evidence['candidate'], oid, evidence['state'])
            diagnosis = diagnosis_evidence(execution, call(name + '-levels', 'diagnose', diag_request), evidence['candidate'])
            row = {'case': name, 'status': 'pass', 'workunit': evidence, 'diagnosis': diagnosis}
            summary['cases'].append(row)
            write_new(output / (name + '-evidence.json'), row)
            recheck()
        require(summary['completed_cli_calls'] == WORKUNIT_COUNT, 'Incomplete bounded job sequence')
        total = sum(len(row['workunit']['native_processes']) + len(row['diagnosis']['native_processes']) for row in summary['cases'])
        require(total == NATIVE_PROCESS_COUNT, 'Native process total mismatch')
        summary.update(status='pass', qualification='native_numerical_workunits_pass', native_processes=total,
                       source_candidates_after={key: descriptor(value['file']) for key, value in sources.items()},
                       frozen_source_unchanged=True, visual_acceptance='not_run', production_qualification='not_asserted')
    except BaseException as exc:
        observations = {}
        for key, value in sources.items():
            try:
                actual = descriptor(checked_path(value['file']))
                observations[key] = {'before': value, 'after': actual, 'bytes_unchanged': actual == value}
            except Exception as observation_error:
                observations[key] = {'before': value, 'observation_error': str(observation_error)}
        summary['source_observations_at_failure'] = observations
        try:
            summary['frozen_source_unchanged'] = inventory(source) == frozen
            summary['runtime_unchanged'] = descriptor(blender) == verified['blender']
        except Exception as observation_error:
            summary['identity_observation_error'] = str(observation_error)
        summary.update(status='stopped', qualification='not_passed', error={'type': type(exc).__name__, 'message': str(exc)},
                       next_step='Review retained evidence and actual failure; no retry, deletion or acceptance relaxation was performed')
        write_new(output / 'qualification.json', summary)
        raise
    write_new(output / 'qualification.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--describe', action='store_true', help='Host-only reviewed scope; the default, with no file creation')
    mode.add_argument('--execute', action='store_true', help='Requires an independently verified exact external user approval')
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--freeze-sha256')
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--approval-record-sha256')
    parser.add_argument('--approval-reference')
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--blender-sha256')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(preparation_summary(), indent=2))
        return 0
    require(all(value is not None for key, value in vars(args).items() if key not in ('execute', 'describe')), 'All exact freeze, external approval, runtime and new-root arguments are required')
    verified = gate(ROOT, args.freeze, args.freeze_sha256, args.approval_record,
        args.approval_record_sha256, args.approval_reference, args.blender, args.blender_sha256, args.out)
    summary = execute_run(verified)
    print(json.dumps({'status': summary['status'], 'evidence': str(args.out / 'qualification.json')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
