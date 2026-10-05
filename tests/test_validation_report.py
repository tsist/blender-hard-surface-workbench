import copy
import json
from pathlib import Path
import tempfile
import unittest
from hardsurface.validation_report import project_validation, read_validation_export, encoded, MAX_SHARD_BYTES
from hardsurface.io import RuntimeFailure

class ValidationReportTests(unittest.TestCase):
    def result(self):
        return {'status':'fail','topology':{},'checks':[{'id':str(i),'status':'pass','evidence':'x'*600} for i in range(257)],'pairs':[{'pair':['sample_plate',str(i)],'status':'fail','evidence':'y'*1500} for i in range(43)]}
    def test_small_legacy_result_unchanged(self):
        data={'status':'pass','checks':[],'pairs':[]}
        self.assertEqual(project_validation(data,None),(data,[]))
    def test_large_result_lossless_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            original=self.result();summary,outputs=project_validation(original,d)
            self.assertTrue(summary['details_omitted']);self.assertLessEqual(len(encoded(summary)),MAX_SHARD_BYTES)
            self.assertTrue(all(r['bytes']<=MAX_SHARD_BYTES for r in outputs))
            self.assertEqual(read_validation_export(Path(summary['details_reference']['file'])),original)
            self.assertEqual(len(summary['checks']),257);self.assertEqual(len(summary['pairs']),43)
    def test_source_detail_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            summary,outputs=project_validation(self.result(),d);p=Path(outputs[0]['file']);p.write_bytes(p.read_bytes().replace(b'pass',b'fail',1))
            with self.assertRaises(RuntimeFailure):read_validation_export(Path(summary['details_reference']['file']))
    def test_manifest_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            summary,_=project_validation(self.result(),d);p=Path(summary['details_reference']['file']);x=json.loads(p.read_text());x['shards'][0]['file']='../bad.json';p.write_text(json.dumps(x))
            with self.assertRaises(RuntimeFailure):read_validation_export(p)
    def test_manifest_duplicate_and_discontinuous_rejected(self):
        for mode in ('duplicate','start','complete_sha'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                summary,_=project_validation(self.result(),d);p=Path(summary['details_reference']['file']);x=json.loads(p.read_text())
                if mode=='duplicate':x['shards'].append(x['shards'][0])
                elif mode=='start':x['shards'][0]['start']=1
                else:x['complete_sha256']='0'*64
                p.write_text(json.dumps(x))
                with self.assertRaises(RuntimeFailure):read_validation_export(p)
    def test_row_and_total_bounds_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeFailure):project_validation({'checks':[{'text':'x'*(MAX_SHARD_BYTES+1)}],'pairs':[]},d)
            with self.assertRaises(RuntimeFailure):project_validation({'checks':[{'text':'x'*(4*1024*1024+1)}],'pairs':[]},d)
    def test_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            project_validation(self.result(),d)
            with self.assertRaises(FileExistsError):project_validation(self.result(),d)
    def test_python_tuples_preserve_canonical_json_identity(self):
        with tempfile.TemporaryDirectory() as d:
            data=self.result();data['checks'][0]['tuple_witness']=(1.,2.,3.)
            summary,_=project_validation(data,d)
            self.assertEqual(encoded(read_validation_export(summary['details_reference']['file'])),encoded(data))

if __name__=='__main__':unittest.main()
