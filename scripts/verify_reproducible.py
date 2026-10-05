#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare two independently built directories without changing either one."""
import argparse
import hashlib
from pathlib import Path


def files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    args = parser.parse_args()
    first, second = files(args.first), files(args.second)
    if not first or first != second:
        raise SystemExit('Independent package builds differ or are empty')
    print('Reproducible package files:', len(first))


if __name__ == '__main__':
    main()
