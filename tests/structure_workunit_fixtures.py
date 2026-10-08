# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral M1 requests for the real host; these functions never launch Blender.

These dimensions are numerical contract fixtures, not a PQ design/reference.
The two datum modes must be fixed at creation: patch_if_revision cannot replace
feature programs and therefore must never smuggle an edit_datum change.
"""
from copy import deepcopy

from hardsurface import contract, planner, subdivision

FEATURE_ID = 'neutral_panel'
CHECKPOINT_ID = 'attempt-01-step-01'
STEP_KEYS = [FEATURE_ID + '/panel', FEATURE_ID + '/checkpoint']
DIMENSIONS = {'hole_x': 11, 'hole_y': 1, 'hole_radius': 8.5, 'z_min': 0, 'z_max': 5.5}
BASE = {'id': 'panel', 'op': 'quad.panel', 'topology_strategy': 'subd_control_cage',
        'size': [96, 62], 'corner_radius': 9, 'edge_bevel': .7, 'center': [0, 0],
        'local_patch_bounds': [-7, -15, 25, 17], 'chord_tolerance': .05,
        'subdivision_cage': {'method': 'CATMULL_CLARK', 'hole_segments': 24, 'preview_levels': 2}}
CASES = (
    {'name': 'baseline_bottom', 'action': 'run', 'datum': 'fixed_bottom', 'source': None, 'values': {}, 'native_processes': 4},
    {'name': 'hole_update', 'action': 'run', 'datum': 'fixed_bottom', 'source': 'baseline_bottom', 'values': {'hole_x': 10, 'hole_y': 2, 'hole_radius': 8}, 'native_processes': 6},
    {'name': 'thickness_bottom', 'action': 'run', 'datum': 'fixed_bottom', 'source': 'hole_update', 'values': {'z_max': 6.5}, 'native_processes': 6},
    {'name': 'baseline_midplane', 'action': 'run', 'datum': 'fixed_midplane', 'source': None, 'values': {}, 'native_processes': 4},
    {'name': 'thickness_midplane', 'action': 'run', 'datum': 'fixed_midplane', 'source': 'baseline_midplane', 'values': {'z_min': -.5, 'z_max': 6}, 'native_processes': 6},
    {'name': 'resume_bottom', 'action': 'resume', 'datum': 'fixed_bottom', 'source': 'baseline_bottom', 'values': {}, 'native_processes': 3},
)
WORKUNIT_COUNT = 12  # Six model/resume jobs, each followed by one read-only diagnosis.
NATIVE_PROCESS_COUNT = 35  # 29 model/resume workers + six diagnosis workers.
WALL_SECONDS = 600
EXPECTED_SOURCE_MODIFIER = {
    'name': 'HS_Subdivision_Cage', 'type': 'SUBSURF', 'subdivision_type': 'CATMULL_CLARK',
    'levels': 2, 'render_levels': 2, 'quality': 6, 'uv_smooth': 'PRESERVE_BOUNDARIES',
    'boundary_smooth': 'ALL', 'use_creases': True, 'use_limit_surface': True,
    'use_custom_normals': False, 'show_viewport': True, 'show_render': True,
    'show_in_editmode': True, 'show_on_cage': False, 'show_only_control_edges': True,
    'use_apply_on_spline': False, 'use_pin_to_last': False,
    'use_adaptive_subdivision': False, 'adaptive_space': 'PIXEL', 'adaptive_pixel_size': 1.0,
    'adaptive_object_edge_length': 0.009999999776482582,
}


def dimension_ref(name):
    return {'kind': 'dimension_ref', 'id': name}


def base_state(datum):
    if datum not in ('fixed_bottom', 'fixed_midplane'):
        raise ValueError('A separately predetermined thickness datum is required')
    panel = deepcopy(BASE)
    panel.update(edit_datum=datum, z_min=dimension_ref('z_min'), z_max=dimension_ref('z_max'),
                 holes=[{'id': 'bore', 'kind': 'circle', 'center': [dimension_ref('hole_x'), dimension_ref('hole_y')],
                         'radius': dimension_ref('hole_radius')}])
    return contract.validate_state({'revision': 1,
        'dimensions': [{'id': key, 'role': 'driving', 'quantity': 'length', 'value': value, 'unit': 'mm'} for key, value in DIMENSIONS.items()],
        'sketches': [], 'features': [{'id': FEATURE_ID, 'program': {'kind': 'steps', 'steps': [panel,
            {'id': 'checkpoint', 'op': 'checkpoint', 'depends_on': ['panel'], 'label': 'M1 native structural checkpoint'}]}}]})


def initial_request(name, datum):
    checks = ['design_dimensions', 'closed_mesh', 'source_preserved', 'reopen', 'preservation', 'quad_topology', 'structure_identity']
    return contract.validate_request({'schema_version': '1.0', 'command': 'hardsurface.run', 'params': {
        'manifest_version': '1.0', 'request_id': 'structure.workunit.' + name, 'purpose': 'contract_fixture',
        'source': {'kind': 'new_scene', 'project_id': 'm1_fixture_' + datum, 'length_unit': 'mm', 'up_axis': 'Z'},
        'context': {'scene': 'Fixture', 'view_layer': 'ViewLayer', 'frame': 1, 'evaluation': 'RENDER'},
        'resources': [], 'design': {'mode': 'create_if_absent', 'expected_state': 'absent', 'state': base_state(datum)},
        'protection': {'source_write': 'forbidden', 'shared_data': 'reject_shared', 'manual_edits': 'preserve_supported_nonconflicting', 'on_conflict': 'pause'},
        'work_units': [{'id': 'build', 'feature_ids': [FEATURE_ID], 'checks': checks, 'failure_policy': 'stop_job'}],
        'quality': {'profile': 'static_closed_mechanical_fixture', 'required': checks,
                    'visual': 'not_applicable_fixture_only', 'user_feedback': 'not_applicable_fixture_only', 'length_tolerance': .01},
        'budgets': {'max_total_steps': 2, 'max_geometry_vertices': 200000, 'max_geometry_loops': 1000000,
                    'wall_seconds': WALL_SECONDS, 'cpu_threads': 2, 'max_total_attempts': 1, 'max_artifact_bytes': 512 * 1024**2},
        'execution': {'max_attempts': 1, 'technical_strategies': ['declared']},
        'wire': {'enabled': False}, 'output': {'publication': 'candidate_only', 'report': 'compact'}}})


def patch_request(name, datum, source, saved_state, changes):
    if not changes or set(changes) - set(DIMENSIONS):
        raise ValueError('Only the five declared driving dimensions may change')
    state = contract.validate_state(saved_state)
    panel = state['features'][0]['program']['steps'][0]
    if state['features'][0]['id'] != FEATURE_ID or panel.get('edit_datum') != datum:
        raise ValueError('Datum and feature scope must already match the saved source')
    request = initial_request(name, datum)
    request['params']['source'] = {'kind': 'saved_blend', 'file': source['file'], 'expected_sha256': source['sha256'], 'bytes': source['bytes'], 'length_unit': 'mm'}
    request['params']['design'] = {'mode': 'patch_if_revision', 'expected_revision': state['revision'],
        'state_sha256': contract.fingerprint(state),
        'patches': [{'op': 'set_dimension', 'id': key, 'value': value, 'unit': 'mm'} for key, value in changes.items()]}
    return contract.validate_request(request)


def panel_parameters(saved_state):
    state = contract.validate_state(saved_state)
    lookup = {x['id']: x for x in state['dimensions']}
    panel = state['features'][0]['program']['steps'][0]
    return planner._resolve_dimensions(panel, lookup, .001)


def diagnosis_request(name, candidate, object_id, saved_state):
    parameters = panel_parameters(saved_state)
    reference = {key: deepcopy(parameters[key]) for key in ('size', 'center', 'corner_radius', 'edge_bevel', 'z_min', 'z_max')}
    reference['holes'] = [{key: deepcopy(parameters['holes'][0][key]) for key in ('center', 'radius')}]
    reference['tolerance_mm'] = .05
    return subdivision.validate_request({'schema_version': '1.0', 'command': 'hardsurface.subdivision.diagnose', 'params': {
        'request_id': 'structure.workunit.' + name + '.levels',
        'source': {'file': candidate['file'], 'expected_sha256': candidate['sha256'], 'bytes': candidate['bytes']},
        'target': {'object_id': object_id}, 'stack_mode': 'isolated_control_cage',
        'evaluation_profile': {'mode': 'source_bound_qualification_v1',
            'expected_source_modifier': deepcopy(EXPECTED_SOURCE_MODIFIER)},
        'levels': [0, 1, 2, 3], 'panel_reference': reference, 'render': {'enabled': False},
        'export_geometry': False, 'cpu_threads': 2, 'wall_seconds': WALL_SECONDS}})


def preparation_summary():
    return {'qualification': 'not_run', 'scope': 'neutral numerical contract fixtures; no PQ or visual approval',
        'cases': deepcopy(list(CASES)), 'jobs': WORKUNIT_COUNT, 'host_cli_calls': WORKUNIT_COUNT,
        'expected_blender_invocations_on_full_success': NATIVE_PROCESS_COUNT,
        'expected_existing_watchdog_invocations_on_full_success': NATIVE_PROCESS_COUNT,
        'process_count_basis': 'run: checkpoint build + checkpoint reopen + tail build + final reopen; saved-source adds stage + inspect; resume adds resume-stage + tail build + reopen; each diagnosis adds one worker',
        'not_an_os_process_or_thread_bound': True,
        'failure_policy': 'stop at first failure; no retries; unchanged geometry gates',
        'required_reference_levels': [2, 3], 'diagnostic_reference_levels': [0, 1], 'reference_tolerance_mm': .05,
        'evaluation_profile': 'source_bound_qualification_v1', 'measurement_profile': 'semantic_bore_v1',
        'expected_source_modifier': deepcopy(EXPECTED_SOURCE_MODIFIER),
        'job_wall_seconds_each': WALL_SECONDS, 'job_disk_limit_bytes_each': 512 * 1024**2,
        'total_reviewed_job_wall_budget_seconds': WALL_SECONDS * WORKUNIT_COUNT,
        'total_cli_timeout_budget_seconds': (WALL_SECONDS + 30) * WORKUNIT_COUNT,
        'job_disk_budget_sum_bytes': WORKUNIT_COUNT * 512 * 1024**2,
        'budget_limits_scope': 'Existing per-job budgets and serial CLI timeouts; inventory/copy/log overhead additional; not a hard OS resource sandbox',
        'wire_and_beauty_renders': False,
        'execution_layout': 'New approved root / execution-copy / jobs-store (inside the copied host.ROOT); TMPDIR and HOME also under the approved root'}
