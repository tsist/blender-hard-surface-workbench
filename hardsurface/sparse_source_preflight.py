# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed, complete-body HOST predictions for sparse source admission.

These checks neither import Blender nor edit a mesh. Both coordinate domains
use native control inspection's unchanged quad_quality policy and provenance.
The intersection prediction reuses native spatial_candidates/audit_pairs, with
both possible diagonals of every valid convex quad. That is conservative HOST
coverage, not an assertion about Blender's actual tessellation or qualification.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import struct

from .io import RuntimeFailure
from .quad_quality import DEFAULT_POLICY, LIMITS, validate_mesh
from .self_intersection import MAX_TRIANGLES, audit_pairs, spatial_candidates
from .sparse_panel_geometry import validate_sparse_authored_identity
from .sparse_patch_graph import validate_sparse_mesh
from .structure_kernel import fingerprint as sparse_construction_fingerprint


SCHEMA = 'sparse-source-preflight/1.0'
AGGREGATE_SCHEMA = 'sparse-source-preflight-set/1.0'
DOMAINS = ('authored_metres', 'float32_metres')
STORAGE_TRANSFORMS = {'authored_metres': 'float64 authored_mm * 0.001',
    'float32_metres': "struct.unpack('f', struct.pack('f', authored_mm * 0.001))[0]"}
REGIONS = ('bottom', 'holewall', 'outer_roundover', 'side', 'top')
REQUIRED_GATES = ('finite_structure', 'authored_identity', 'surface_coverage') + tuple(
    domain + '.' + check for domain in DOMAINS
    for check in ('quad_quality', 'self_intersections'))
_NOT_RUN = {'native_verification': 'not_run', 'subdivision_qualification': 'not_run',
            'shape_acceptance': 'not_run', 'visual_acceptance': 'not_run'}
_REPORT_FIELDS = {'schema_version', 'gate', 'status', 'passed', 'evidence_origin',
    'source_binding', 'required_gates', 'gates', 'coordinate_domains', 'failures',
    'report_sha256', 'scope', *_NOT_RUN}
_EXPECTED_ERRORS = (RuntimeFailure, ValueError, TypeError, KeyError, IndexError,
                    ArithmeticError, AttributeError)
_EXTREMA = (
    ('minimum_edge_m', 'minimum_edge_m', min),
    ('minimum_face_area_m2', 'area_m2', min),
    ('minimum_corner_angle_degrees', 'minimum_corner_angle_degrees', min),
    ('maximum_corner_angle_degrees', 'maximum_corner_angle_degrees', max),
    ('maximum_aspect_ratio', 'aspect_ratio', max),
    ('maximum_quad_warpage_degrees', 'quad_warpage_degrees', max),
    ('maximum_quad_plane_distance_m', 'quad_plane_distance_m', max),
)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _report_sha(report):
    return _sha({k: v for k, v in report.items() if k != 'report_sha256'})


def _error(exc):
    # Details are diagnostics only; invalid source coordinates must not prevent
    # a JSON-safe failure report from being retained by the caller.
    details = getattr(exc, 'details', {})
    try:
        _sha(details)
    except _EXPECTED_ERRORS:
        details = {'reason': 'Error details contained non-JSON or nonfinite data'}
    return {'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc),
            'details': details}


def _gate(passed, **evidence):
    return {'status': 'pass' if passed is True else 'fail', 'passed': passed is True,
            **evidence}


def _not_run(reason):
    return {'status': 'not_run', 'passed': False, 'reason': reason}


def _attempt(callback):
    try:
        return _gate(True, evidence=callback())
    except _EXPECTED_ERRORS as exc:
        return _gate(False, error=_error(exc))


def _binding(mesh):
    manifest = mesh.get('authored_structure', {})
    parameters = manifest.get('parameter_binding', {})
    provenance = mesh.get('face_provenance', [])
    features = {p.get('feature_id') for p in provenance if isinstance(p, dict)}
    authored_m = [[x*.001 for x in v] for v in mesh['vertices_mm']]
    float32_m = [[struct.unpack('f', struct.pack('f', x))[0] for x in v] for v in authored_m]
    return {'mesh_sha256': _sha(mesh),
            'construction_sha256': mesh.get('construction_sha256'),
            'authorship_sha256': mesh.get('authorship_sha256'),
            'parameters_sha256': parameters.get('design_sha256'),
            'faces_sha256': _sha(mesh['faces']),
            'provenance_sha256': _sha(provenance),
            'authored_metres_vertices_sha256': _sha(authored_m),
            'float32_metres_vertices_sha256': _sha(float32_m),
            'feature_id': next(iter(features)) if len(features) == 1 else None,
            'vertices': len(mesh['vertices_mm']), 'faces': len(mesh['faces'])}


def sparse_source_binding(mesh):
    """Bind a predeclared expected state independently of its audit result.

    This returns identity only, never a quality pass. A fixture driver supplies
    these records by state name when aggregating its separately generated audits.
    """
    return _binding(mesh)


def _authored_identity(mesh, feature_id):
    result = validate_sparse_authored_identity(mesh)
    binding = _binding(mesh)
    payload = {'vertices_mm': mesh['vertices_mm'], 'faces': mesh['faces'],
        'face_provenance': mesh['face_provenance'], 'corner_normals': mesh.get('corner_normals'),
        'edge_creases': mesh['edge_creases'], 'subdivision_modifier': mesh['subdivision_modifier'],
        'control_loops': mesh.get('control_loops', [])}
    # Both existing entry points are immutable compatibility interfaces:
    # build_sparse_panel uses the canonical sparse digest; quad_geometry.build
    # replaces it with ordinary JSON's digest. Signed zero and Unicode expose
    # the difference. Validate only those exact producer algorithms, without
    # rewriting the stored digest or changing source/native reconstruction.
    expected_digests = {_sha(payload), sparse_construction_fingerprint(payload)}
    observed = mesh.get('construction_sha256')
    actual_feature = binding['feature_id']
    if (not isinstance(actual_feature, str) or not actual_feature.strip()
            or len(actual_feature) > 128 or (feature_id is not None and actual_feature != feature_id)
            or observed not in expected_digests
            or mesh.get('authorship_sha256') != result['authorship_sha256']):
        raise RuntimeFailure('SPARSE_SOURCE_BINDING',
            'Exact construction, authorship and whole-body feature identity required',
            expected_feature_id=feature_id, observed_feature_id=actual_feature,
            expected_construction_sha256=sorted(expected_digests),
            observed_construction_sha256=observed)
    return {**result, 'construction_sha256': observed, 'feature_id': actual_feature,
            'construction_digest_policy': 'existing_sparse_canonical_or_quad_entry_json'}


def _surface_coverage(mesh):
    provenance = mesh['face_provenance']
    if len(provenance) != len(mesh['faces']):
        raise RuntimeFailure('SPARSE_SOURCE_COVERAGE', 'Provenance must cover every polygon')
    groups = Counter(p.get('region_group') for p in provenance)
    declared = mesh['metadata']['subdivision_cage']['region_face_counts']
    if set(groups) != set(REGIONS) or dict(groups) != declared or any(n <= 0 for n in groups.values()):
        raise RuntimeFailure('SPARSE_SOURCE_COVERAGE',
            'All top, bottom, holewall, outer_roundover and side faces are required',
            observed=dict(groups), declared=declared, required=list(REGIONS))
    return {'face_count': len(provenance), 'region_face_counts': dict(sorted(groups.items())),
            'surface_face_counts': dict(sorted(Counter(p['surface_id'] for p in provenance).items()))}


def _located(finding, inverse):
    result = deepcopy(finding)
    ids = set()
    if type(finding.get('face_index')) is int:
        ids.add(finding['face_index'])
    for key in ('face_indices', 'polygons', 'incident_face_indices'):
        ids.update(i for i in finding.get(key, []) if type(i) is int)
    result['semantic_face_ids'] = [inverse[i] for i in sorted(ids) if i in inverse]
    return result


def _region_metrics(mesh, metrics, findings):
    result = {}
    inverse = {i: sid for sid, i in mesh['authored_structure']['face_map'].items()}
    for group in REGIONS:
        ids = [i for i, p in enumerate(mesh['face_provenance']) if p.get('region_group') == group]
        id_set = set(ids)
        selected = [m for m in metrics if m['face_index'] in id_set]
        located = [f for f in findings if f.get('face_index') in ids or
                   set(f.get('face_indices', [])) & id_set]
        extrema, locations = {}, {}
        for name, field, reducer in _EXTREMA:
            values = [m[field] for m in selected if m.get(field) is not None]
            extrema[name] = reducer(values) if values else None
            locations[name] = [m['face_index'] for m in selected if
                               m.get(field) is not None and m[field] == extrema[name]]
        support_ids = [i for i in ids if mesh['face_provenance'][i].get('support_band') is True]
        result[group] = {'face_count': len(ids), 'face_indices': ids,
            'semantic_face_ids': [inverse[i] for i in ids], 'measured_faces': len(selected),
            'support_band_faces': len(support_ids), 'metrics': extrema,
            'metric_face_indices': locations, 'finding_count': len(located),
            'finding_counts': dict(sorted(Counter(f['code'] for f in located).items())),
            'maximum_support_band_aspect_ratio': max((m['aspect_ratio'] for m in selected
                if m['face_index'] in support_ids and m['aspect_ratio'] is not None), default=None),
            'maximum_ordinary_aspect_ratio': max((m['aspect_ratio'] for m in selected
                if m['face_index'] not in support_ids and m['aspect_ratio'] is not None), default=None)}
    return result


def _intersection_prediction(vertices_m, faces, metrics):
    # Native only audits distinct-face intersections after polygon validity.
    # Aspect/edge thresholds do not make valid convex quads untessellatable, so
    # independent intersection diagnostics still run for those failing bodies.
    if len(metrics) != len(faces) or any(m['self_crossing'] or m['concave'] or
            m['area_m2'] <= 0 or m['minimum_edge_m'] <= 0 for m in metrics):
        raise RuntimeFailure('SPARSE_INTERSECTION_PRECONDITION',
                             'Every polygon must be a nondegenerate convex quad')
    if 4*len(faces) > MAX_TRIANGLES:
        raise RuntimeFailure('SELF_INTERSECTION_BUDGET', 'Both-diagonal HOST coverage exceeds triangle budget')
    triangles, polygons = [], []
    for fi, (a, b, c, d) in enumerate(faces):
        triangles.extend(((a, b, c), (a, c, d), (a, b, d), (b, c, d)))
        polygons.extend((fi,)*4)
    vertices_mm = [[x*1000.0 for x in point] for point in vertices_m]
    candidates, phase = spatial_candidates(vertices_mm, triangles)
    result = audit_pairs(vertices_mm, triangles, polygons, candidates)
    result.update(broad_phase=phase,
        tessellation='HOST conservative both-diagonal coverage; four test triangles per source quad',
        evidence_origin='host_prediction_not_actual_blender_loop_triangles',
        source_polygons=len(faces), native_triangle_count='not_run')
    result['scope'] = result['scope'].replace('BVH candidate', 'spatial-AABB candidate')
    return result


def _domain(mesh, vertices_m, name):
    inverse = {i: sid for sid, i in mesh['authored_structure']['face_map'].items()}
    identity = {'feature_id': _binding(mesh)['feature_id']}
    findings = []
    quality = validate_mesh(vertices_m, mesh['faces'], face_provenance=mesh['face_provenance'],
        identity=identity, mesh_state='control', include_face_metrics=True, finding_sink=findings.append)
    metrics = quality.pop('face_metrics')
    all_findings = [_located(f, inverse) for f in findings]
    quality_gate = _gate(quality.get('passed') is True and quality.get('finding_count') == 0
        and quality.get('finding_counts') == {} and not all_findings,
        finding_count=quality['finding_count'], finding_counts=quality['finding_counts'],
        findings=all_findings)
    intersection_gate = _attempt(lambda: _intersection_prediction(vertices_m, mesh['faces'], metrics))
    if intersection_gate['passed']:
        intersections = intersection_gate['evidence']
        intersections['findings'] = [_located(f, inverse) for f in intersections['findings']]
        intersection_gate['passed'] = (intersections['status'] == 'pass' and
            intersections['findings'] == [] and intersections['finding_limit_reached'] is False)
        intersection_gate['status'] = 'pass' if intersection_gate['passed'] else 'fail'
    else:
        intersections = {'status': 'not_run', 'findings': [], 'error': intersection_gate['error']}
    return {'coordinate_units': 'm', 'evidence_origin': 'host_prediction',
        'storage_transform': STORAGE_TRANSFORMS[name],
        'vertices_sha256': _sha(vertices_m), 'faces_sha256': _sha(mesh['faces']),
        'provenance_sha256': _sha(mesh['face_provenance']),
        'quality': quality, 'regions': _region_metrics(mesh, metrics, all_findings),
        'self_intersections': intersections,
        'gates': {'quad_quality': quality_gate, 'self_intersections': intersection_gate}}


def audit_sparse_source(mesh, *, require_pass=False, feature_id=None):
    """Collect all feasible gates; optionally raise, preserving report on failure.

    The caller owns persistence. No report is loaded or reused, no policy can be
    relaxed and no native process is started. Input geometry is never changed.
    """
    if type(require_pass) is not bool:
        raise RuntimeFailure('SPARSE_SOURCE_PREFLIGHT_FAILED', 'require_pass must be boolean')
    gates = {name: _not_run('Prerequisite data unavailable') for name in REQUIRED_GATES}
    domains = {}
    binding = None
    gates['finite_structure'] = _attempt(lambda: validate_sparse_mesh(mesh))
    gates['authored_identity'] = _attempt(lambda: _authored_identity(mesh, feature_id))
    gates['surface_coverage'] = _attempt(lambda: _surface_coverage(mesh))
    try:
        binding = _binding(mesh)
        vertices = mesh['vertices_mm']
        if (not 0 < len(vertices) <= LIMITS['vertices'] or
                any(not isinstance(v, (list, tuple)) or len(v) != 3 or
                    any(type(x) not in (int, float) or not math.isfinite(x) or
                        abs(x*.001) > LIMITS['coordinate_m'] for x in v) for v in vertices)):
            raise RuntimeFailure('SPARSE_SOURCE_COORDINATES', 'Bounded finite authored xyz arrays required')
        for name in DOMAINS:
            try:
                vertices_m = [[x*.001 if name == DOMAINS[0] else
                    struct.unpack('f', struct.pack('f', x*.001))[0] for x in v] for v in vertices]
                domains[name] = _domain(mesh, vertices_m, name)
                for check, gate in domains[name]['gates'].items():
                    gates[name+'.'+check] = gate
            except _EXPECTED_ERRORS as exc:
                for check in ('quad_quality', 'self_intersections'):
                    gates[name+'.'+check] = _gate(False, error=_error(exc))
    except _EXPECTED_ERRORS as exc:
        for name in DOMAINS:
            for check in ('quad_quality', 'self_intersections'):
                gates[name+'.'+check] = _gate(False, error=_error(exc))
    failures = [{'gate': name, **deepcopy(gates[name])} for name in REQUIRED_GATES
                if gates[name].get('passed') is not True or gates[name].get('status') != 'pass']
    report = {'schema_version': SCHEMA, 'gate': 'whole_sparse_source_host_preflight',
        'status': 'fail' if failures else 'pass', 'passed': not failures,
        'evidence_origin': 'host_authored_arrays_and_float32_metre_prediction',
        'source_binding': binding, 'required_gates': list(REQUIRED_GATES),
        'gates': gates, 'coordinate_domains': domains, 'failures': failures, **_NOT_RUN,
        'scope': 'All source polygons in both coordinate domains. Unchanged native control quad policy '
            'and exact provenance; shared pure native intersection algorithm with conservative '
            'both-diagonal HOST tessellation. Actual Blender storage/extraction, normals, modifier '
            'state, reopen, evaluated SubD, dimensions and visual acceptance remain separate.'}
    # Graph evidence includes integer-keyed valence histograms. Normalize the
    # report itself to its persisted JSON form before hashing or embedding in a
    # plan; the strict planner requires string object keys at every depth.
    report = json.loads(json.dumps(report, allow_nan=False))
    report['report_sha256'] = _report_sha(report)
    if require_pass:
        require_sparse_source_preflight(report, mesh=mesh)
    return report


def _checked_report(report, mesh=None):
    """Check a trusted producer's report; its checksum is not authentication.

    When the source is available, recompute cheap structure and whole-polygon
    checks as well as both storage domains. Intersection evidence is still from
    the fresh audit producer, not authenticated by an externally rehashed JSON.
    """
    if not isinstance(report, dict) or set(report) != _REPORT_FIELDS:
        raise ValueError('Exact preflight report schema required')
    if (report['schema_version'] != SCHEMA or report['gate'] != 'whole_sparse_source_host_preflight'
            or report['report_sha256'] != _report_sha(report)
            or report['required_gates'] != list(REQUIRED_GATES)
            or set(report['gates']) != set(REQUIRED_GATES)
            or report['evidence_origin'] != 'host_authored_arrays_and_float32_metre_prediction'
            or any(report[k] != v for k, v in _NOT_RUN.items())):
        raise ValueError('Preflight source envelope, scope or content hash differs')
    if report['passed'] is not True or report['status'] != 'pass' or report['failures'] != []:
        raise ValueError('Whole source preflight contains failures')
    if any(g.get('passed') is not True or g.get('status') != 'pass' for g in report['gates'].values()):
        raise ValueError('Every required gate must be explicitly passed true')
    binding = report['source_binding']
    if (not isinstance(binding, dict) or set(binding) != {'mesh_sha256', 'construction_sha256',
            'authorship_sha256', 'parameters_sha256', 'faces_sha256', 'provenance_sha256',
            'authored_metres_vertices_sha256', 'float32_metres_vertices_sha256',
            'feature_id', 'vertices', 'faces'}
            or any(not isinstance(binding[k], str) or len(binding[k]) != 64 or
                   any(c not in '0123456789abcdef' for c in binding[k])
                   for k in ('mesh_sha256', 'construction_sha256', 'authorship_sha256', 'parameters_sha256',
                             'faces_sha256', 'provenance_sha256',
                             'authored_metres_vertices_sha256', 'float32_metres_vertices_sha256'))
            or not isinstance(binding['feature_id'], str) or not binding['feature_id']
            or any(type(binding[k]) is not int or binding[k] <= 0 for k in ('vertices', 'faces'))):
        raise ValueError('Complete exact source binding required')
    if mesh is not None and binding != _binding(mesh):
        raise ValueError('Preflight is stale or belongs to another source')
    if mesh is not None:
        validate_sparse_mesh(mesh)
        _authored_identity(mesh, binding['feature_id'])
        _surface_coverage(mesh)
    if set(report['coordinate_domains']) != set(DOMAINS):
        raise ValueError('Both authored and exact float32 coordinate domains are required')
    for name in ('finite_structure', 'authored_identity'):
        evidence = report['gates'][name]['evidence']
        if (evidence.get('status') != 'pass' or evidence.get('vertices') != binding['vertices']
                or evidence.get('faces') != binding['faces']):
            raise ValueError('Whole-body structure/identity gate lacks complete source evidence')
    identity = report['gates']['authored_identity']['evidence']
    if (identity.get('construction_sha256') != binding['construction_sha256']
            or identity.get('authorship_sha256') != binding['authorship_sha256']
            or identity.get('feature_id') != binding['feature_id']):
        raise ValueError('Authored identity evidence differs from exact source binding')
    coverage = report['gates']['surface_coverage']['evidence']
    if coverage.get('face_count') != binding['faces'] or set(coverage['region_face_counts']) != set(REGIONS):
        raise ValueError('Required region coverage differs from source binding')
    for name in DOMAINS:
        domain = report['coordinate_domains'][name]
        quality, intersections = domain['quality'], domain['self_intersections']
        if (set(domain) != {'coordinate_units', 'evidence_origin', 'storage_transform',
                           'vertices_sha256', 'faces_sha256', 'provenance_sha256',
                           'quality', 'regions', 'self_intersections', 'gates'}
                or domain['coordinate_units'] != 'm' or domain['evidence_origin'] != 'host_prediction'
                or domain['storage_transform'] != STORAGE_TRANSFORMS[name]
                or domain['vertices_sha256'] != binding[name+'_vertices_sha256']
                or domain['faces_sha256'] != binding['faces_sha256']
                or domain['provenance_sha256'] != binding['provenance_sha256']
                or quality.get('passed') is not True or type(quality.get('finding_count')) is not int
                or quality.get('finding_count') != 0
                or quality.get('finding_counts') != {} or quality.get('findings') != []
                or quality.get('findings_truncated') != 0 or quality.get('policy') != DEFAULT_POLICY
                or quality.get('mesh_state') != 'control' or quality.get('coordinate_units') != 'm'
                or quality.get('identity') != {'feature_id': binding['feature_id']}
                or quality['counts']['faces'] != binding['faces']
                or quality['counts']['quads'] != binding['faces']
                or quality['counts']['vertices'] != binding['vertices']
                or intersections.get('status') != 'pass' or intersections.get('findings') != []
                or intersections.get('finding_limit_reached') is not False
                or intersections.get('triangles') != 4*binding['faces']
                or intersections.get('source_polygons') != binding['faces']
                or set(domain['regions']) != set(REGIONS)):
            raise ValueError('Whole-body quality, unchanged policy, intersection or surface coverage differs')
        all_faces = []
        for group, region in domain['regions'].items():
            if (type(region['face_count']) is not int or region['face_count'] <= 0
                    or region['measured_faces'] != region['face_count']
                    or len(region['face_indices']) != region['face_count']
                    or len(region['semantic_face_ids']) != region['face_count']
                    or region['finding_count'] != 0 or region['finding_counts'] != {}
                    or region['face_count'] != coverage['region_face_counts'][group]
                    or set(region['metrics']) != {x[0] for x in _EXTREMA}
                    or any(type(v) not in (int, float) or not math.isfinite(v)
                           for v in region['metrics'].values())):
                raise ValueError('Per-region metrics or complete measured coverage differs')
            for field, limit in (('maximum_support_band_aspect_ratio', DEFAULT_POLICY['max_support_band_aspect_ratio']),
                                 ('maximum_ordinary_aspect_ratio', DEFAULT_POLICY['max_aspect_ratio'])):
                if region[field] is not None and (type(region[field]) not in (int, float)
                        or not math.isfinite(region[field]) or region[field] > limit):
                    raise ValueError('Per-region aspect metrics exceed unchanged native thresholds')
            all_faces.extend(region['face_indices'])
        if sorted(all_faces) != list(range(binding['faces'])):
            raise ValueError('Region metrics must cover every source face exactly once')
        for field, _, reducer in _EXTREMA:
            if quality['metrics'][field] != reducer(region['metrics'][field] for region in domain['regions'].values()):
                raise ValueError('Whole-body metrics differ from all-region extrema')
        for check in ('quad_quality', 'self_intersections'):
            if domain['gates'][check] != report['gates'][name+'.'+check]:
                raise ValueError('Domain gate differs from required gate evidence')
        qgate = domain['gates']['quad_quality']
        if (set(qgate) != {'status', 'passed', 'finding_count', 'finding_counts', 'findings'}
                or type(qgate['finding_count']) is not int or qgate['finding_count'] != 0
                or qgate['finding_counts'] != {} or qgate['findings'] != []
                or domain['gates']['self_intersections'].get('evidence') != intersections):
            raise ValueError('Underlying whole-domain gate evidence contains failures or differs')
        if mesh is not None:
            vertices_m = [[x*.001 if name == DOMAINS[0] else
                struct.unpack('f', struct.pack('f', x*.001))[0] for x in v] for v in mesh['vertices_mm']]
            observed_findings = []
            observed_quality = validate_mesh(vertices_m, mesh['faces'],
                face_provenance=mesh['face_provenance'], identity={'feature_id': binding['feature_id']},
                mesh_state='control', include_face_metrics=True, finding_sink=observed_findings.append)
            observed_metrics = observed_quality.pop('face_metrics')
            inverse = {i: sid for sid, i in mesh['authored_structure']['face_map'].items()}
            observed_regions = _region_metrics(mesh, observed_metrics,
                [_located(f, inverse) for f in observed_findings])
            if observed_quality != quality or observed_regions != domain['regions']:
                raise ValueError('Whole-polygon quality or region measurements differ from the exact source')
    return report


def require_sparse_source_preflight(report, *, mesh=None):
    """Enforce a freshly produced report; mesh additionally rejects stale binding."""
    try:
        return _checked_report(report, mesh)
    except _EXPECTED_ERRORS as exc:
        raise RuntimeFailure('SPARSE_SOURCE_PREFLIGHT_FAILED',
            'Complete HOST source preflight failed; no native execution is permitted',
            reason=str(exc), failed_gates=(list(report.get('failures', []))
                if isinstance(report, dict) else []), report=report) from exc


def aggregate_sparse_source_preflights(reports, *, required_states, expected_bindings=None,
                                      require_pass=False):
    """AND exact named state coverage for a private multi-case fixture driver.

    Empty, duplicate, unknown or omitted states fail; no test-count/summary or
    missing/not_run required gate can authorize native execution. Callers must
    independently bind each predeclared expected state with sparse_source_binding,
    then audit each generated state and retain its report. Missing expected
    bindings collect a diagnostic failure; they can never yield a passing set.
    Reports must come from this trusted audit producer. Rehashable JSON digests
    detect stale/mismatched contents; they do not authenticate arbitrary callers.
    """
    failures, checked = [], {}
    valid = (isinstance(required_states, (list, tuple)) and 0 < len(required_states) <= 256
        and all(isinstance(s, str) and 0 < len(s) <= 128 for s in required_states)
        and len(set(required_states)) == len(required_states))
    if not valid:
        failures.append({'gate': 'required_state_coverage', 'reason': 'Nonempty bounded unique state names required'})
    elif not isinstance(reports, dict) or set(reports) != set(required_states):
        failures.append({'gate': 'required_state_coverage', 'required_states': list(required_states),
                         'observed_states': sorted(map(str, reports)) if isinstance(reports, dict) else []})
    bindings_valid = (valid and isinstance(expected_bindings, dict)
                      and set(expected_bindings) == set(required_states))
    bindings_sha = None
    if bindings_valid:
        try:
            bindings_sha = _sha(expected_bindings)
        except _EXPECTED_ERRORS:
            bindings_valid = False
    if not bindings_valid:
        failures.append({'gate': 'expected_state_bindings',
                         'reason': 'Exact complete expected source bindings required for every state'})
    if valid and isinstance(reports, dict):
        seen_sources, seen_reports = {}, {}
        for state in required_states:
            if state not in reports:
                continue
            try:
                _checked_report(reports[state])
                binding = reports[state]['source_binding']
                if bindings_valid and expected_bindings[state] != binding:
                    raise ValueError('State report does not bind its predeclared expected source')
                source_hash, report_hash = binding['mesh_sha256'], reports[state]['report_sha256']
                if source_hash in seen_sources or report_hash in seen_reports:
                    raise ValueError('One source/report cannot substitute for distinct required states')
                seen_sources[source_hash], seen_reports[report_hash] = state, state
                checked[state] = reports[state]['report_sha256']
            except _EXPECTED_ERRORS as exc:
                failures.append({'state': state, 'gate': 'complete_state_preflight', 'reason': str(exc),
                    'failed_gates': deepcopy(reports[state].get('failures', []))
                        if isinstance(reports[state], dict) else []})
    report = {'schema_version': AGGREGATE_SCHEMA, 'status': 'fail' if failures else 'pass',
        'passed': not failures, 'required_states': list(required_states) if valid else [],
        'state_report_sha256': checked,
        'expected_bindings_sha256': bindings_sha,
        'failures': failures,
        'evidence_origin': 'host_prediction_only', **_NOT_RUN}
    report['report_sha256'] = _report_sha(report)
    if require_pass is not False and require_pass is not True:
        raise RuntimeFailure('SPARSE_SOURCE_PREFLIGHT_FAILED', 'require_pass must be boolean')
    if require_pass and failures:
        raise RuntimeFailure('SPARSE_SOURCE_PREFLIGHT_FAILED',
            'Exact complete-state HOST preflight AND failed; no native execution is permitted', report=report)
    return report
