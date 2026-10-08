#!/usr/bin/env python3
"""Run a deterministic, exhaustive module shard of the HOST unittest suite."""
import argparse
import json
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--list-only', action='store_true')
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error('shard-index must be in [0, shard-count)')
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    names = sorted(path.name for path in (root / 'tests').glob('test_*.py'))
    selected = names[args.shard_index::args.shard_count]
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in selected:
        suite.addTests(loader.discover(str(root / 'tests'), pattern=name))
    print(json.dumps({'shard_index': args.shard_index, 'shard_count': args.shard_count,
                      'modules': selected, 'test_count': suite.countTestCases()}), flush=True)
    if args.list_only:
        return 0
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
