import itertools
import unittest
from hardsurface.self_intersection import audit_pairs,spatial_candidates


class ActualTriangleIntersectionTests(unittest.TestCase):
    def audit(self,v,t):return audit_pairs(v,t,list(range(len(t))),list(itertools.combinations(range(len(t)),2)))
    def test_disjoint_triangles(self):
        self.assertEqual(self.audit([(0,0,0),(1,0,0),(0,1,0),(2,0,0),(3,0,0),(2,1,0)],[(0,1,2),(3,4,5)])['status'],'pass')
    def test_true_nonadjacent_crossing_is_rejected(self):
        r=self.audit([(0,0,0),(2,0,0),(0,2,0),(.5,.5,-1),(.5,.5,1),(1.5,.5,0)],[(0,1,2),(3,4,5)])
        self.assertEqual(r['status'],'fail');self.assertTrue(r['findings'])
    def test_shared_edge_only_is_allowed(self):
        r=self.audit([(0,0,0),(1,0,0),(0,1,0),(1,-1,0)],[(0,1,2),(1,0,3)])
        self.assertEqual(r['status'],'pass');self.assertEqual(r['shared_boundary_only_pairs'],1)
    def test_folded_coplanar_adjacent_overlap_is_rejected(self):
        self.assertEqual(self.audit([(0,0,0),(2,0,0),(0,2,0),(1,1,0)],[(0,1,2),(1,0,3)])['status'],'fail')
    def test_sharing_vertex_does_not_exempt_crossing(self):
        r=self.audit([(0,0,0),(3,0,0),(0,3,0),(1,1,-1),(1,1,1)],[(0,1,2),(0,3,4)])
        self.assertEqual(r['status'],'fail')
    def test_exact_shared_vertex_contact_is_allowed(self):
        r=self.audit([(0,0,0),(1,0,0),(0,1,0),(-1,0,0),(0,-1,0)],[(0,1,2),(0,3,4)])
        self.assertEqual(r['status'],'pass')
    def test_spatial_candidates_equal_bruteforce_aabb_pairs(self):
        import random
        rng=random.Random(316)
        vertices=[tuple(rng.uniform(-2,2) for _ in range(3)) for _ in range(90)]
        triangles=[tuple(range(i,i+3)) for i in range(0,90,3)]
        boxes=[tuple(min(vertices[i][k] for i in t) for k in range(3))+tuple(max(vertices[i][k] for i in t) for k in range(3)) for t in triangles]
        brute={(i,j) for i,j in itertools.combinations(range(len(triangles)),2) if all(boxes[i][k]<=boxes[j][k+3] and boxes[j][k]<=boxes[i][k+3] for k in range(3))}
        actual,proof=spatial_candidates(vertices,triangles)
        self.assertEqual(set(actual),brute);self.assertGreater(proof['comparisons'],0)


if __name__=='__main__':unittest.main()
