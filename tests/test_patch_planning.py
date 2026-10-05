"""Geometry-independent contracts for local sampling and patch alignment."""
import copy
import math
import unittest
from hardsurface.quad_patches import align_artificial_patch_events, quarter_arc_segments, QuadPatchError


class PatchPlanningTests(unittest.TestCase):
    def surfaces(self):
        return [{'axes':('x','y'),'bounds':(-20,-12,20,12),'radius':0,
                 'holes':[{'id':'round','kind':'circle','center':(-8,0),'radius':1.3,'patch':(-11,-3, -5,3)},
                          {'id':'oblong','kind':'rounded_rectangle','bounds':(4,-1.4,11,1.4),'radius':.9,'patch':(2,-3.15,13,3.15)}]}]

    def test_only_artificial_edges_align_and_real_design_is_unchanged(self):
        surfaces=self.surfaces();old=copy.deepcopy(surfaces)
        changes=align_artificial_patch_events(surfaces,.3)
        self.assertTrue(changes)
        self.assertEqual(surfaces[0]['bounds'],old[0]['bounds'])
        for a,b in zip(surfaces[0]['holes'],old[0]['holes']):
            self.assertEqual({k:v for k,v in a.items() if k!='patch'}, {k:v for k,v in b.items() if k!='patch'})
        for row in changes:self.assertLessEqual(abs(row['old_patch_coordinate']-row['new_patch_coordinate']),.3)
        self.assertEqual(surfaces[0]['holes'][0]['patch'][1],surfaces[0]['holes'][1]['patch'][1])

    def test_actual_exclusion_is_never_snapped(self):
        surfaces=self.surfaces();surfaces[0]['holes'].append({'id':'lip','kind':'rectangle','bounds':(-17,-2.95,-15,1)})
        old=copy.deepcopy(surfaces[0]['holes'][-1]);align_artificial_patch_events(surfaces,.3)
        self.assertEqual(surfaces[0]['holes'][-1],old)

    def test_far_patch_coordinates_are_not_moved(self):
        surfaces=self.surfaces();old=copy.deepcopy(surfaces)
        self.assertEqual(align_artificial_patch_events(surfaces,.01),[])
        self.assertEqual(surfaces,old)

    def test_quarter_arc_counts_satisfy_independent_chord_bound(self):
        for radius,tolerance in ((.6,.018),(.4,.007),(1.2,.004),(.17,.02)):
            count=quarter_arc_segments(radius,tolerance)
            self.assertLessEqual(radius*(1-math.cos(math.pi/(4*count))),tolerance+1e-14)
            if count>1:self.assertGreater(radius*(1-math.cos(math.pi/(4*(count-1)))),tolerance)

    def test_impossible_curve_budget_fails_before_allocation(self):
        with self.assertRaises(QuadPatchError):quarter_arc_segments(.6,1e-14,16)

if __name__=='__main__':unittest.main()
