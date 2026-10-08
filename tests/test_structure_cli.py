# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from hardsurface.structure_cli import main, MAX_REQUEST_BYTES

class CliTests(unittest.TestCase):
    def run_cli(self,path):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):code=main(['--request',str(path)])
        return code,json.loads(out.getvalue())
    def test_read_only_fixture_blocked_no_outputs(self):
        p=Path(__file__).parents[1]/'fixtures/structure-plan.valid.json';before=p.read_bytes()
        code,r=self.run_cli(p);self.assertEqual(code,0);self.assertEqual(r['status'],'planned_with_blockers');self.assertFalse(r['construction_authorized']);self.assertEqual(p.read_bytes(),before);self.assertEqual(r['side_effects'],[])
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            code,r=self.run_cli(Path(d)/'not_there.json')
        self.assertEqual(code,2);self.assertEqual(r['blockers'][0]['code'],'INPUT_READ_FAILED')
    def test_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.json';p.write_text('{"x":1,"x":2}')
            code,r=self.run_cli(p)
        self.assertEqual(code,2);self.assertEqual(r['status'],'rejected')
    def test_overbudget_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.json';p.write_bytes(b' '*(MAX_REQUEST_BYTES+1))
            code,r=self.run_cli(p)
        self.assertEqual(code,2);self.assertEqual(r['blockers'][0]['code'],'LIMIT_EXCEEDED')
    def test_invalid_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.json';p.write_bytes(b'\xff')
            code,r=self.run_cli(p)
        self.assertEqual(code,2);self.assertEqual(r['status'],'rejected')

if __name__=='__main__':unittest.main()
