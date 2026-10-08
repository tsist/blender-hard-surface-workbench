# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only host entry point for structural planning preparation.

This is not yet a blenderctl domain integration. It emits a JSON dry-run and
cannot start Blender, register add-ons, write a scene, or grant qualification.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .structure_contract import plan_request

MAX_REQUEST_BYTES = 2 * 1024 * 1024


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path, help='Existing local JSON request, read only')
    parser.add_argument('--pretty', action='store_true')
    args = parser.parse_args(argv)
    try:
        with args.request.open('rb') as handle:
            data = handle.read(MAX_REQUEST_BYTES+1)
        if len(data) > MAX_REQUEST_BYTES:
            result = {'operation':'structure.plan','status':'rejected','construction_authorized':False,'side_effects':[], 'blockers':[{'code':'LIMIT_EXCEEDED','message':'Request exceeds 2 MiB'}]}
        else:
            result = plan_request(data)
    except OSError as exc:
        result = {'operation':'structure.plan','status':'rejected','construction_authorized':False,'side_effects':[], 'blockers':[{'code':'INPUT_READ_FAILED','message':str(exc)}]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2 if args.pretty else None))
    return 2 if result['status'] == 'rejected' else 0


if __name__ == '__main__':
    sys.exit(main())
