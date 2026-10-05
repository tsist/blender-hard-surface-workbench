#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build deterministic source and Blender extension archives; never install them."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {'evidence', 'reports', 'runtime', 'dist', '__pycache__', '.git', '.aws', '.codex', '.agents'}
BLOCKED_SUFFIXES = {'.blend', '.blend1', '.whl', '.so', '.dll', '.exe', '.pyc', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.bmp', '.tif', '.tiff', '.exr', '.hdr', '.avif', '.obj', '.fbx', '.stl', '.gltf', '.glb', '.usd', '.usda', '.patch'}


def read_public_files():
    names = (ROOT / 'PUBLIC_FILES.txt').read_text(encoding='utf-8').splitlines()
    if len(names) != len(set(names)) or names != sorted(names):
        raise ValueError('PUBLIC_FILES must contain unique sorted paths')
    files = {}
    for name in names:
        relative = Path(name)
        if not name or relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name:
            raise ValueError('Unsafe public manifest path')
        if set(relative.parts) & BLOCKED_PARTS or relative.suffix.lower() in BLOCKED_SUFFIXES:
            raise ValueError('Runtime, asset, or historical artifact in public manifest')
        path = ROOT / relative
        if any(part.is_symlink() for part in [path, *path.parents] if part != ROOT.parent):
            raise ValueError('Symlink in public manifest path')
        data = path.read_bytes()
        text = data.decode('utf-8')
        if relative.suffix == '.py':
            ast.parse(text, filename=name)
        files[name] = data
    for name in ('LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'blender_manifest.toml', 'PUBLIC_FILES.txt', 'hardsurface-cli', 'hardsurface/host.py', 'hardsurface/linux_file_guard.py'):
        if name not in files:
            raise ValueError('Required public source file is absent: ' + name)
    return files


def write_archive(path, files):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 5, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name == 'hardsurface-cli' else 0o100644) << 16
            archive.writestr(info, data, compresslevel=9)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise ValueError('ZIP integrity failed')
        for name, data in files.items():
            if archive.read(name) != data:
                raise ValueError('ZIP content mismatch: ' + name)
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'members': sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Output directory must be new; existing artifacts are never replaced')
    files = read_public_files()
    manifest = tomllib.loads(files['blender_manifest.toml'].decode())
    version = manifest['version']
    if not isinstance(version, str) or len(version.split('.')) != 3 or not all(part.isdigit() for part in version.split('.')):
        raise ValueError('Extension version must be three numeric components')
    module = ast.parse(files['hardsurface/__init__.py'].decode())
    constants = {}
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in ('__version__', 'bl_info'):
                    constants[target.id] = ast.literal_eval(statement.value)
    if constants.get('__version__') != version or constants.get('bl_info', {}).get('version') != tuple(int(part) for part in version.split('.')):
        raise ValueError('Manifest, Python module, and bl_info versions differ')
    if 'SPDX:GPL-3.0-or-later' not in manifest.get('license', []):
        raise ValueError('GPL license declaration is required')
    extension = {name: data for name, data in files.items()
                 if name.startswith(('hardsurface/', 'schemas/', 'docs/'))
                 or name in ('LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'blender_manifest.toml')}
    extension['__init__.py'] = b'from .hardsurface import register, unregister\n'
    args.output.mkdir(parents=True)
    receipt = {'version': version, 'distribution': 'experimental-public-source',
               'license': 'GPL-3.0-or-later', 'install_enable_gui_qualification': 'not_run',
               'extension': write_archive(args.output / f'hard-surface-workbench-{version}.zip', extension),
               'source': write_archive(args.output / f'hard-surface-source-{version}.zip', files)}
    manifest = args.output / 'manifest.json'
    manifest.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    artifacts = [args.output / receipt[key]['file'] for key in ('extension', 'source')] + [manifest]
    (args.output / 'SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
        for path in sorted(artifacts)), encoding='utf-8')
    print(json.dumps({'version': version, 'source_files': len(files), 'extension_files': len(extension),
                      'artifacts': [{key: item[key] for key in ('file', 'sha256', 'bytes')}
                                    for item in (receipt['source'], receipt['extension'])]}))


if __name__ == '__main__':
    main()
