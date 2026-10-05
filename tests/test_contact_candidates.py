import random
import unittest
from unittest.mock import patch
from hardsurface.contact_candidates import cross_candidates
from hardsurface.io import RuntimeFailure

class CompleteCrossCandidatesTests(unittest.TestCase):
    def brute(self,aa,ta,bb,tb,eps):
        def bounds(v,t):return [([min(v[i][k] for i in tri) for k in range(3)],[max(v[i][k] for i in tri) for k in range(3)]) for tri in t]
        a,b=bounds(aa,ta),bounds(bb,tb)
        return [(i,j) for i,(lo,hi) in enumerate(a) for j,(bl,bh) in enumerate(b) if all(max(lo[k],bl[k])<=min(hi[k],bh[k])+eps for k in range(3))]
    def test_coplanar_identical_indices_are_not_filtered(self):
        v=[(0,0,0),(1,0,0),(1,1,0),(0,1,0)];t=[(0,1,2),(0,2,3)]
        self.assertEqual(cross_candidates(v,t,v,t)[0],[(0,0),(0,1),(1,0),(1,1)])
    def test_random_matches_all_bruteforce_aabbs(self):
        rng=random.Random(7301)
        for planar in (False,True):
            a=[(rng.uniform(-4,4),rng.uniform(-4,4),0 if planar else rng.uniform(-4,4)) for _ in range(120)]
            b=[(rng.uniform(-4,4),rng.uniform(-4,4),0 if planar else rng.uniform(-4,4)) for _ in range(120)]
            ts=[tuple(range(i,i+3)) for i in range(0,120,3)]
            self.assertEqual(cross_candidates(a,ts,b,ts)[0],self.brute(a,ts,b,ts,.002))
    def test_numerical_contact_planes_are_included_but_larger_gap_excluded(self):
        a=[(0,0,0),(1,0,0),(0,1,0)];t=[(0,1,2)]
        self.assertEqual(cross_candidates(a,t,[(x,y,.001) for x,y,z in a],t)[0],[(0,0)])
        self.assertEqual(cross_candidates(a,t,[(x,y,.003) for x,y,z in a],t)[0],[])
    def test_budgets_fail_closed(self):
        a=[(0,0,0),(1,0,0),(0,1,0)];t=[(0,1,2)]
        for constant in ('MAX_INSERTIONS','MAX_COMPARISONS','MAX_CANDIDATES'):
            with self.subTest(constant=constant),patch('hardsurface.contact_candidates.'+constant,0):
                with self.assertRaises(RuntimeFailure):cross_candidates(a,t,a,t)
    def test_epsilon_cannot_be_widened(self):
        with self.assertRaises(RuntimeFailure):cross_candidates([],[],[],[],epsilon_mm=.01)

if __name__=='__main__':unittest.main()
