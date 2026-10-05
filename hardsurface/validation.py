# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict declarative validation requests; no profiles, scripts, or design defaults.

All geometric specifications travel inside the request and therefore participate
in the host request fingerprint. Source/dependency isolation remains owned by the
host adapter. This module additionally verifies the opened source and scene state.
"""
from __future__ import annotations

import copy
import math

from .contract import (
    BOOL, FILE, IDENT, _fail, _validate, array, canonical_bytes, const, enum,
    integer, number, obj, optional_default, strict_loads, string, union,
)
from .io import RuntimeFailure, verify_descriptor

MAX_PARTS = 32
MAX_PROBES = 1000
MAX_PAIRS = 496
MAX_CONTACT_REGIONS = 256
MAX_BORE_SPECS = 256
COORDINATE_LIMIT_MM = 1e9
COORD = number(minimum=-COORDINATE_LIMIT_MM, maximum=COORDINATE_LIMIT_MM)
POSITIVE = number(exclusiveMinimum=0, maximum=COORDINATE_LIMIT_MM)
TOLERANCE = number(minimum=0, maximum=COORDINATE_LIMIT_MM)
V2 = array(COORD, 2, 2)
BOUNDS = obj({'expected': array(COORD, 6, 6), 'tolerance_mm': TOLERANCE})
PART = obj({'id': IDENT, 'feature_id': IDENT, 'bounds_mm': BOUNDS}, ['id', 'feature_id'])
PROBE = obj({'id': IDENT, 'part': IDENT, 'axis': enum('X', 'Y', 'Z'),
             'fixed_mm': V2, 'expected_hits_mm': array(COORD, 0, 1000),
             'tolerance_mm': TOLERANCE})
HIT = obj({'probe': IDENT, 'index': integer(minimum=0, maximum=999)})
DIFFERENCE = obj({'id': IDENT, 'a': HIT, 'b': HIT, 'expected_mm': COORD,
                  'tolerance_mm': TOLERANCE})
AXIAL = obj({'id': IDENT, 'probes': array(IDENT, 4, 4, uniqueItems=True),
             'separation_mm': POSITIVE, 'maximum_degrees': number(minimum=0, maximum=90)})
OUTER = union(
    obj({'kind': const('rectangle'), 'min_mm': V2, 'max_mm': V2}),
    obj({'kind': const('disk'), 'center_mm': V2, 'radius_mm': POSITIVE}),
)
BORE = obj({'part': IDENT, 'center_mm': V2, 'radius_mm': POSITIVE,
            'position_tolerance_mm': number(exclusiveMinimum=0, maximum=.05),
            'chord_tolerance_mm': number(exclusiveMinimum=0, maximum=.05)})
REGION = obj({'axis': enum('X', 'Y', 'Z'), 'plane_mm': COORD, 'outer': OUTER,
              'bores': optional_default(array(BORE, 0, 32), [])},
             ['axis', 'plane_mm', 'outer'])
CONTACT = obj({'pair': array(IDENT, 2, 2, uniqueItems=True),
               'regions': array(REGION, 1, 16)})
SCHEMA = obj({
    'schema_version': const('2.0'),
    'command': const('hardsurface.validate'),
    'params': obj({
        'request_id': string(minLength=1, maxLength=128,
                             pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
        'source': FILE,
        'parts': array(PART, 1, MAX_PARTS),
        'assembly_bounds_mm': BOUNDS,
        'probes': optional_default(array(PROBE, 0, MAX_PROBES), []),
        'difference_checks': optional_default(array(DIFFERENCE, 0, 1000), []),
        'axis_checks': optional_default(array(AXIAL, 0, 1000), []),
        'contacts': optional_default(array(CONTACT, 0, MAX_PAIRS), []),
        'require_all_visible': optional_default(BOOL, True),
        'include_pairs': optional_default(BOOL, True),
        'cpu_threads': optional_default(integer(minimum=1, maximum=4), 2),
        'wall_seconds': optional_default(number(exclusiveMinimum=0, maximum=600), 600),
    }, ['request_id', 'source', 'parts']),
})
AXIS = {'X': 0, 'Y': 1, 'Z': 2}


def schema():
    return copy.deepcopy(SCHEMA)


def _ordered_bounds(spec, path):
    values = spec['expected']
    if any(values[i] >= values[i + 3] for i in range(3)):
        _fail('Bounds require strictly increasing minima/maxima on all axes', path)


def _unique(rows, key, path):
    result = {}
    for i, row in enumerate(rows):
        value = row[key]
        if value in result:
            _fail('Duplicate ' + key, f'{path}[{i}].{key}')
        result[value] = row
    return result


def _probe_position(probe):
    return dict(zip((i for i in range(3) if i != AXIS[probe['axis']]), probe['fixed_mm']))


def _axis_semantics(check, probes, path):
    rows = [probes[key] for key in check['probes']]
    if len({row['part'] for row in rows}) != 1:
        _fail('An axial check requires four probes on the same part', path)
    if any(len(row['expected_hits_mm']) != 2 for row in rows):
        _fail('An axial check requires two expected hits for every probe', path)
    a, b, c, d = [AXIS[row['axis']] for row in rows]
    if a == b or a != c or b != d:
        _fail('Axial probes must be ordered u-low, v-low, u-high, v-high', path)
    axial = next(i for i in range(3) if i not in (a, b))
    positions = [_probe_position(row) for row in rows]
    # Roundoff only: declared design tolerances cannot loosen the check geometry.
    close = lambda x, y: abs(x - y) <= max(1e-10, 8 * max(math.ulp(x), math.ulp(y)))
    if not close(positions[0][axial], positions[1][axial]) or not close(positions[2][axial], positions[3][axial]):
        _fail('Each pair of transverse probes must share one axial station', path)
    if not close(positions[0][b], positions[2][b]) or not close(positions[1][a], positions[3][a]):
        _fail('Low and high probes must retain their transverse fixed coordinates', path)
    separation = abs(positions[2][axial] - positions[0][axial])
    if not close(separation, check['separation_mm']):
        _fail('separation_mm must equal the distance between the two axial stations', path)


def validate_request(value):
    if isinstance(value, (str, bytes)):
        value = strict_loads(value)
    # Apply the same JSON node/depth/finite-domain limits to Python callers.
    canonical_bytes(value)
    value = _validate(copy.deepcopy(value), SCHEMA)
    p = value['params']
    parts = _unique(p['parts'], 'id', '$.params.parts')
    _unique(p['parts'], 'feature_id', '$.params.parts')
    for i, part in enumerate(p['parts']):
        if 'bounds_mm' in part:
            _ordered_bounds(part['bounds_mm'], f'$.params.parts[{i}].bounds_mm')
    if 'assembly_bounds_mm' in p:
        _ordered_bounds(p['assembly_bounds_mm'], '$.params.assembly_bounds_mm')
    probes = _unique(p['probes'], 'id', '$.params.probes')
    check_ids = set(probes)
    if sum(len(row['expected_hits_mm']) for row in p['probes']) > 10000:
        _fail('Total expected ray hits exceed 10000', '$.params.probes')
    for i, probe in enumerate(p['probes']):
        if probe['part'] not in parts:
            _fail('Unknown selected part', f'$.params.probes[{i}].part')
        hits = probe['expected_hits_mm']
        if any(a >= b for a, b in zip(hits, hits[1:])):
            _fail('Expected ray hits must be strictly increasing', f'$.params.probes[{i}].expected_hits_mm')
    for collection in ('difference_checks', 'axis_checks'):
        for i, check in enumerate(p[collection]):
            path = f'$.params.{collection}[{i}]'
            if check['id'] in check_ids:
                _fail('Duplicate check or probe id', path + '.id')
            check_ids.add(check['id'])
            refs = [check['a']['probe'], check['b']['probe']] if collection == 'difference_checks' else check['probes']
            if any(ref not in probes for ref in refs):
                _fail('Unknown probe reference', path)
            if collection == 'difference_checks':
                for key in ('a', 'b'):
                    ref = check[key]
                    if ref['index'] >= len(probes[ref['probe']]['expected_hits_mm']):
                        _fail('Hit index is outside the referenced expected hits', path + '.' + key)
            else:
                _axis_semantics(check, probes, path)
    seen_pairs = set()
    region_count = bore_count = 0
    for i, contact in enumerate(p['contacts']):
        path = f'$.params.contacts[{i}]'
        pair = frozenset(contact['pair'])
        if not pair.issubset(parts):
            _fail('Contact pair refers to an unselected part', path + '.pair')
        if pair in seen_pairs:
            _fail('Duplicate unordered contact pair; combine its regions', path + '.pair')
        seen_pairs.add(pair)
        for j, region in enumerate(contact['regions']):
            region_count += 1
            outer = region['outer']
            if outer['kind'] == 'rectangle' and any(a >= b for a, b in zip(outer['min_mm'], outer['max_mm'])):
                _fail('Rectangle minima must precede maxima', f'{path}.regions[{j}].outer')
            for k, bore in enumerate(region['bores']):
                bore_count += 1
                if bore['part'] not in pair:
                    _fail('Bore part must belong to its contact pair', f'{path}.regions[{j}].bores[{k}].part')
    if region_count > MAX_CONTACT_REGIONS or bore_count > MAX_BORE_SPECS:
        _fail('Aggregate contact region/bore budget exceeded', '$.params.contacts')
    verify_descriptor(p['source'])
    return value


def execute(request, job_dir=None):
    request = validate_request(request)
    p = request['params']
    import bpy
    from .core import inspect_scene
    from .generic_validation import run
    from .validation_report import project_validation

    if not bpy.data.filepath:
        raise RuntimeFailure('SOURCE_CHANGED', 'No candidate file is open')
    verify_descriptor({'file': bpy.data.filepath, 'expected_sha256': p['source']['expected_sha256']})
    before = inspect_scene()
    try:
        result = run(p)
    finally:
        if inspect_scene() != before:
            raise RuntimeFailure('READ_ONLY_VIOLATION', 'Validation modified Blender scene data')
        verify_descriptor(p['source'])
    pair_status = result['pair_status']
    full_status = result['status'] if p['require_all_visible'] and p['include_pairs'] else 'not_run'
    result, outputs = project_validation(result, job_dir)
    return {
        'outcome': 'validation_complete', 'domain_outcome': result['status'],
        'validation': result, 'outputs': outputs,
        'acceptance': {'geometry': {'status': result['status']},
                       'interpart_pairs': {'status': pair_status},
                       'visual': {'status': 'not_run'}, 'user_feedback': {'status': 'not_run'}},
        'covered_parts': [part['id'] for part in p['parts']],
        'full_assembly_acceptance': full_status,
        'acceptance_scope': 'Only caller-provided specifications, selected evaluated topology, and requested static pair checks; no arbitrary-design certification',
        'read_only': True, 'source': verify_descriptor(p['source']),
        'visual': 'not_run', 'user_feedback': 'not_run',
        'budgets': {'parts': MAX_PARTS, 'probes': MAX_PROBES, 'pairs': MAX_PAIRS,
                    'vertices': 200000, 'triangles': 400000, 'pair_candidates': 1000000,
                    'containment_witnesses_per_pair': 1000000, 'float64_fallback_witnesses': 64,
                    'wall_seconds': p['wall_seconds'],
                    'memory': 'Host sampled RSS; evaluated mesh/BVH creation may allocate before cardinality rejection'},
    }
