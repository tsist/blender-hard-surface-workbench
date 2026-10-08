# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import unittest
from hardsurface.structure_adapter import adapt_authored_mesh
from hardsurface.structure_kernel import StructureError
from test_structure_kernel import annulus

class AdapterTests(unittest.TestCase):
    def data(self):
        m,s=annulus();a={'vertices_mm':m['vertices'],'faces':m['faces'],'face_provenance':[{'feature_id':'coupon','surface_id':'annular_top'} for f in m['faces']]}
        return a,{'vertex_map':s['vertex_map'],'face_map':s['face_map'],'tolerances':s['tolerances']}
    def test_adapter_checks_mesh_not_counts(self):
        a,k=self.data();original=copy.deepcopy((a,k));r=adapt_authored_mesh(a,**k)
        self.assertEqual(r['validation']['status'],'pass');self.assertEqual(r['validation']['counts']['boundary_ports'],2);self.assertEqual(r['qualification'],'not_run');self.assertEqual((a,k),original)
    def test_no_implicit_index_identity(self):
        a,k=self.data();k['vertex_map']={}
        with self.assertRaises(StructureError):adapt_authored_mesh(a,**k)
    def test_malformed_provenance(self):
        a,k=self.data();a['face_provenance'][0]={}
        with self.assertRaises(StructureError):adapt_authored_mesh(a,**k)
    def test_index_permutation_keeps_semantic_ids(self):
        a,k=self.data();r0=adapt_authored_mesh(a,**k)
        n=len(a['vertices_mm']);a['vertices_mm'].reverse();a['faces']=[[n-1-v for v in f] for f in a['faces']];k['vertex_map']={key:n-1-v for key,v in k['vertex_map'].items()}
        r1=adapt_authored_mesh(a,**k)
        self.assertEqual(r0['structure']['regions'],r1['structure']['regions']);self.assertEqual(r0['structure']['loops'],r1['structure']['loops']);self.assertEqual(r0['validation']['topology_signature'],r1['validation']['topology_signature'])
    def test_native_status_not_inherited(self):
        a,k=self.data();a['metadata']={'visual':'pass','native':'pass'}
        r=adapt_authored_mesh(a,**k);self.assertEqual(r['native_extraction'],'not_run');self.assertEqual(r['qualification'],'not_run')
    def test_missing_face_record(self):
        a,k=self.data();a['face_provenance'].pop()
        with self.assertRaises(StructureError):adapt_authored_mesh(a,**k)
    def test_degenerate_boundary(self):
        a,k=self.data();a['vertices_mm'][1]=a['vertices_mm'][0][:]
        with self.assertRaises(StructureError):adapt_authored_mesh(a,**k)

if __name__=='__main__':unittest.main()
