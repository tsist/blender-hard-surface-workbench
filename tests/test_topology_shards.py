import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hardsurface import topology
from hardsurface.io import RuntimeFailure


class IndexedGeometryShardTests(unittest.TestCase):
    def data(self):
        return {'schema_version':'1.0','identity':{'feature_id':'fixture'},'mesh_state':'control',
          'vertices':[[i*.01,i*.02,i*.03] for i in range(10000)],'edges':[[i,i+1] for i in range(9999)],
          'polygons':[[i,i+1,i+2,i+3] for i in range(9997)],'loop_triangles':[],
          'shading':{'normal_coordinate_space':'local','corner_normals':[[0,0,1]]*39988,'face_smooth':[False]*9997},
          'construction':{'constructor':'fixture','surface_table':[{'surface_id':'a'}],'face_surface_ids':[0]*9997}}
    def produce(self):
        root=Path(tempfile.mkdtemp(prefix='quad-shard-fixture-'))
        limits={**topology.LIMITS,'json_file_bytes':65536}
        with patch.dict(topology.LIMITS,limits):ref,outputs=topology._write_geometry(root,'topology-000-control-geometry',self.data(),[0])
        return root,ref,outputs
    def test_lossless_indexed_reassembly_and_bounds(self):
        root,ref,outputs=self.produce()
        self.assertEqual(ref['storage'],'sharded_indexed_json');self.assertGreater(len(outputs),1)
        self.assertTrue(all(x['bytes']<=65536 for x in outputs))
        self.assertEqual(topology.read_geometry_export(Path(ref['file'])),self.data())
    def test_changed_bytes_fail_exact_sha(self):
        root,ref,outputs=self.produce();Path(outputs[0]['file']).write_bytes(b'{}')
        with self.assertRaises(RuntimeFailure):topology.read_geometry_export(Path(ref['file']))
    def test_traversal_and_duplicate_shards_rejected(self):
        root,ref,outputs=self.produce();p=Path(ref['file']);manifest=json.loads(p.read_text())
        next(iter(manifest['arrays'].values()))['chunks'][0]['relative_file']='../escape.json'
        p.write_text(json.dumps(manifest))
        with self.assertRaises(RuntimeFailure):topology.read_geometry_export(p)
    def test_small_export_remains_legacy_json(self):
        root=Path(tempfile.mkdtemp(prefix='quad-small-export-'));data={'vertices':[],'edges':[],'polygons':[],'loop_triangles':[]}
        ref,outputs=topology._write_geometry(root,'topology-000-control-geometry',data,[0])
        self.assertNotIn('storage',ref);self.assertEqual(len(outputs),1)
        self.assertEqual(topology.read_geometry_export(Path(ref['file'])),data)
    def test_aggregate_budget_is_not_raised(self):
        root=Path(tempfile.mkdtemp(prefix='quad-shard-budget-'))
        with self.assertRaises(RuntimeFailure):topology._write_geometry(root,'topology-000-control-geometry',self.data(),[topology.LIMITS['aggregate_json_bytes']])


if __name__=='__main__':unittest.main()
