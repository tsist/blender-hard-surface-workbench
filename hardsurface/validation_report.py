# SPDX-License-Identifier: GPL-3.0-or-later
"""Lossless bounded validation details, with a small public summary."""
import hashlib
import json
from pathlib import Path
from .io import RuntimeFailure, checked_path, descriptor
from .contract import strict_loads

MAX_SHARD_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_SHARDS = 64

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def project_validation(result, job_dir):
    raw = encoded(result)
    if len(raw) <= MAX_SHARD_BYTES:
        return result, []
    if len(raw) > MAX_TOTAL_BYTES:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation details exceed 4 MiB total bound')
    if job_dir is None:
        raise RuntimeFailure('REPORT_LIMIT', 'Large validation requires an owned job directory')
    root = Path(job_dir).resolve()
    sections = {key: result[key] for key in ('checks', 'pairs')}
    base = {key: value for key, value in result.items() if key not in sections}
    pending = []
    for section, rows in sections.items():
        start = 0
        while start < len(rows):
            batch = []
            for row in rows[start:]:
                candidate = {'schema_version': '1.0', 'section': section, 'start': start, 'rows': batch + [row]}
                if len(encoded(candidate)) > MAX_SHARD_BYTES:
                    break
                batch.append(row)
            if not batch:
                raise RuntimeFailure('REPORT_LIMIT', 'One validation detail row exceeds 128 KiB', section=section, row=start)
            pending.append((section, start, batch))
            start += len(batch)
    if len(pending) > MAX_SHARDS:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation detail shard count exceeds 64')
    records, outputs = [], []
    total = 0
    for index, (section, start, rows) in enumerate(pending):
        data = encoded({'schema_version': '1.0', 'section': section, 'start': start, 'rows': rows})
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise RuntimeFailure('REPORT_LIMIT', 'Validation encoded shards exceed 4 MiB')
        path = root / ('validation-detail-%03d.json' % index)
        with path.open('xb') as stream:
            stream.write(data)
        item = descriptor(path)
        outputs.append(item)
        records.append(dict(file=path.name, sha256=item['sha256'], bytes=item['bytes'], section=section, start=start, count=len(rows)))
    manifest = {'schema_version': '1.0', 'format': 'validation_sections', 'base': base,
                'counts': {key: len(rows) for key, rows in sections.items()}, 'shards': records,
                'complete_sha256': hashlib.sha256(raw).hexdigest(), 'complete_bytes': len(raw)}
    data = encoded(manifest)
    if len(data) > MAX_SHARD_BYTES:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation manifest exceeds 128 KiB')
    path = root / 'validation-details.json'
    with path.open('xb') as stream:
        stream.write(data)
    ref = descriptor(path)
    outputs.append(ref)
    summary = {**base, 'checks': [{k: row[k] for k in ('id', 'status') if k in row} for row in result['checks']],
               'pairs': [{k: row[k] for k in ('pair', 'status', 'candidate_triangle_pairs', 'containment_points_tested') if k in row} for row in result['pairs']],
               'details_omitted': True, 'details_reference': ref, 'complete_sha256': manifest['complete_sha256'],
               'details_format': 'validation_sections', 'report_budgets': {'summary_bytes': MAX_SHARD_BYTES, 'shard_bytes': MAX_SHARD_BYTES, 'total_detail_bytes': MAX_TOTAL_BYTES, 'shards': MAX_SHARDS}}
    if len(encoded(summary)) > MAX_SHARD_BYTES:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation summary exceeds 128 KiB')
    # Reconstruct and verify before publishing the projection.
    if encoded(read_validation_export(path)) != raw:
        raise RuntimeFailure('REPORT_INTEGRITY', 'Validation projection failed lossless verification')
    return summary, outputs

def read_validation_export(path):
    """Load a manifest (or a legacy complete result), verifying all bytes first."""
    path = checked_path(str(Path(path).absolute()))
    if path.stat().st_size > MAX_SHARD_BYTES:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation manifest exceeds 128 KiB')
    raw = path.read_bytes()
    if len(raw) > MAX_SHARD_BYTES:
        raise RuntimeFailure('REPORT_LIMIT', 'Validation manifest exceeds 128 KiB')
    value = strict_loads(raw)
    if value.get('format') != 'validation_sections':
        return value
    refs = value['shards']
    if value.get('schema_version') != '1.0' or len(refs) > MAX_SHARDS or set(value['counts']) != {'checks', 'pairs'} or any(type(n) is not int or not 0 <= n <= 10000 for n in value['counts'].values()) or type(value['complete_bytes']) is not int or not 0 <= value['complete_bytes'] <= MAX_TOTAL_BYTES:
        raise RuntimeFailure('REPORT_INTEGRITY', 'Invalid validation shard structure')
    result = dict(value['base']); result.update(checks=[], pairs=[])
    total = 0; seen = set()
    for ref in refs:
        name = ref['file']
        if not isinstance(name, str) or Path(name).name != name or name in seen:
            raise RuntimeFailure('REPORT_INTEGRITY', 'Unsafe or duplicate validation shard')
        seen.add(name)
        item = checked_path(str(path.parent / name))
        if type(ref['bytes']) is not int or not 0 <= ref['bytes'] <= MAX_SHARD_BYTES or item.stat().st_size != ref['bytes']:
            raise RuntimeFailure('REPORT_LIMIT', 'Validation shard exceeds bound')
        data = item.read_bytes(); total += len(data)
        if len(data) != ref['bytes'] or hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise RuntimeFailure('REPORT_INTEGRITY', 'Validation shard byte identity differs')
        if total > MAX_TOTAL_BYTES:
            raise RuntimeFailure('REPORT_LIMIT', 'Validation detail total exceeds bound')
        chunk = strict_loads(data); section = ref['section']
        if section not in ('checks', 'pairs') or chunk.get('schema_version') != '1.0' or chunk['section'] != section or chunk['start'] != ref['start'] or len(result[section]) != ref['start'] or len(chunk['rows']) != ref['count']:
            raise RuntimeFailure('REPORT_INTEGRITY', 'Validation shard coverage is discontinuous')
        result[section].extend(chunk['rows'])
    data = encoded(result)
    if any(len(result[key]) != value['counts'][key] for key in ('checks', 'pairs')) or len(data) != value['complete_bytes'] or hashlib.sha256(data).hexdigest() != value['complete_sha256']:
        raise RuntimeFailure('REPORT_INTEGRITY', 'Complete validation identity differs')
    return result
