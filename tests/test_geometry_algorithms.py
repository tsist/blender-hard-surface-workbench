"""Neutral exact-triangle and containment cases, with no production witnesses."""
import unittest
from hardsurface.robust_geometry_audit import triangle_intersection, resolve_indeterminate

class GeometryAlgorithmsTests(unittest.TestCase):
    def test_coplanar_clip_stays_inside_original_triangle(self):
        a=[(0.,0.,2.),(3.,0.,2.),(0.,3.,2.)]
        b=[(1.,-1.,2.),(4.,2.,2.),(1.,4.,2.)]
        points=triangle_intersection(a,b)['points_mm']
        self.assertTrue(points)
        for x,y,z in points:
            self.assertGreaterEqual(x,-1e-12);self.assertGreaterEqual(y,-1e-12)
            self.assertLessEqual(x+y,3+1e-12);self.assertAlmostEqual(z,2)

    def test_disjoint_and_crossing_triangle_pairs(self):
        a=[(0.,0.,0.),(2.,0.,0.),(0.,2.,0.)]
        b=[(.5,.5,-1.),(.5,.5,1.),(1.,.5,0.)]
        self.assertTrue(triangle_intersection(a,b)['points_mm'])
        self.assertFalse(triangle_intersection(a,[(x+8,y,z) for x,y,z in b])['points_mm'])

    def test_nearly_parallel_crossing_is_not_skipped(self):
        a=[(0.,0.,0.),(3500.,0.,0.),(0.,3500.,0.)]
        b=[(0.,0.,-.001),(3500.,0.,.00215),(0.,3500.,-.001)]
        for swap in (False,True):
            for order in ((0,1,2),(2,0,1),(1,2,0)):
                transform=lambda t:[tuple(p[i] for i in order) for p in t]
                first,second=(b,a) if swap else (a,b)
                result=triangle_intersection(transform(first),transform(second))
                self.assertEqual(result['kind'],'noncoplanar')
                self.assertEqual(len(result['points_mm']),2)
                for point in result['points_mm']:
                    self.assertAlmostEqual(point[order.index(0)],3500*.001/.00315,places=6)
                    self.assertAlmostEqual(point[order.index(2)],0,places=9)

    def test_nearly_parallel_strict_same_side_stays_disjoint(self):
        a=[(0.,0.,0.),(3500.,0.,0.),(0.,3500.,0.)]
        b=[(0.,0.,.003),(3500.,0.,.005),(0.,3500.,.003)]
        self.assertEqual(triangle_intersection(a,b)['points_mm'],[])

    def test_nearly_parallel_reverse_normals_and_large_translation(self):
        offset=(73000.,-21000.,54000.)
        a=[tuple(p[i]+offset[i] for i in range(3)) for p in [(0.,0.,0.),(3500.,0.,0.),(0.,3500.,0.)]]
        b=[tuple(p[i]+offset[i] for i in range(3)) for p in [(0.,0.,-.001),(3500.,0.,.00215),(0.,3500.,-.001)]]
        for first,second in ((a,b),(a,list(reversed(b))),(list(reversed(a)),b)):
            result=triangle_intersection(first,second)
            self.assertEqual(len(result['points_mm']),2)
            for point in result['points_mm']:
                self.assertAlmostEqual(point[2],offset[2],places=7)
                self.assertLess(abs(point[0]-offset[0]-3500*.001/.00315),.0001)

    def test_open_mesh_cannot_use_closed_winding_fallback(self):
        result=resolve_indeterminate((.2,.2,0),[[(0.,0.,0.),(2.,0.,0.),(0.,2.,0.)]],parity_status='indeterminate',closed_outward_topology_pass=False)
        self.assertEqual(result['status'],'indeterminate')

if __name__=='__main__':unittest.main()
