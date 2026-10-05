# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-only checks for the standalone launcher and package boundaries."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hardsurface import host
from hardsurface.io import RuntimeFailure
from hardsurface.protection import guard_class

ROOT = Path(__file__).resolve().parents[1]


class StandaloneDistributionTests(unittest.TestCase):
    def call(self, *args, cwd=None):
        env = dict(os.environ, BLENDERCTL_PYTHON=sys.executable)
        return subprocess.run([str(ROOT / 'hardsurface-cli'), *args], cwd=cwd or ROOT,
                              env=env, text=True, capture_output=True, timeout=20)

    def test_launcher_exposes_generic_validation_without_blenderctl(self):
        result = self.call('hardsurface', 'describe', '--section', 'validate')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        reply = json.loads(result.stdout)
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['data']['type'], 'object')
        self.assertFalse((ROOT / 'cli_dev').exists())

    def test_launcher_preserves_callers_relative_request_paths(self):
        temporary = Path(tempfile.mkdtemp(prefix='hs-relative-launch-', dir='/tmp'))
        (temporary / 'request.json').write_text('{"unrecognized": true}')
        result = self.call('hardsurface', 'plan', '--request', 'request.json', cwd=temporary)
        self.assertEqual(result.returncode, 2)
        reply = json.loads(result.stdout)
        self.assertNotEqual(reply['error']['detail_code'], 'FileNotFoundError')
        self.assertNotIn('No such file', reply['error']['message'])

    def test_guard_uses_relative_compatible_exception(self):
        from hardsurface import linux_file_guard
        self.assertIs(linux_file_guard.Failure, RuntimeFailure)
        self.assertIs(guard_class(), linux_file_guard.LinuxFileGuard)
        self.assertEqual(linux_file_guard.LinuxFileGuard.__module__, 'hardsurface.linux_file_guard')

    def test_implementation_hash_binds_launcher_and_guard(self):
        temporary = Path(tempfile.mkdtemp(prefix='hs-split-identity-', dir='/tmp'))
        root = temporary / 'package'
        (root / 'hardsurface').mkdir(parents=True)
        (root / 'hardsurface' / 'linux_file_guard.py').write_text('# numerical identity fixture\n')
        (root / 'hardsurface-cli').write_text('# launcher identity fixture\n')
        binary = temporary / 'not-executed.bin'
        binary.write_bytes(b'not executable')
        with mock.patch.object(host, 'ROOT', root):
            first = host.impl_identity(binary)['source_sha256']
            (root / 'hardsurface-cli').write_text('# changed fixture launcher\n')
            second = host.impl_identity(binary)['source_sha256']
            (root / 'hardsurface' / 'linux_file_guard.py').write_text('# changed fixture guard\n')
            third = host.impl_identity(binary)['source_sha256']
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_async_relaunch_keeps_source_only_interpreter_flags(self):
        tree = ast.parse((ROOT / 'hardsurface/host.py').read_text())
        submit = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'submit')
        command = next(n.value for n in ast.walk(submit) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'cmd' for t in n.targets))
        values = [n.value for n in command.elts if isinstance(n, ast.Constant)]
        for flag in ('-B', '-S', '-P', '-X', '-m'):
            self.assertIn(flag, values)
        self.assertIn('hardsurface.host', values)

    def test_public_manifest_excludes_embedded_cli_and_execution_output(self):
        names = (ROOT / 'PUBLIC_FILES.txt').read_text().splitlines()
        self.assertIn('hardsurface-cli', names)
        self.assertIn('hardsurface/linux_file_guard.py', names)
        for name in names:
            self.assertFalse(name.startswith(('cli_dev/', 'evidence/', 'reports/')), name)
            self.assertNotIn('__pycache__', Path(name).parts)


if __name__ == '__main__':
    unittest.main()
