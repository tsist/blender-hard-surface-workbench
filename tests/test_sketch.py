import copy
import math
import unittest
from hardsurface.sketch import *


def rectangle():
    return {'id':'plate','entities':[{'id':n,'kind':'point2d','seed':p} for n,p in [('p0',[0,0]),('p1',[80,0]),('p2',[80,50]),('p3',[0,50]),('hc',[20,25])]]+
            [{'id':'l'+str(i),'kind':'line_segment2d','start':'p'+str(i),'end':'p'+str((i+1)%4)} for i in range(4)]+
            [{'id':'hole','kind':'circle2d','center':'hc','radius_seed':5}],
            'constraints':[{'id':'fixed','type':'point_fixed2d','point':'p0','at':[0,0]},
                           {'id':'width','type':'axis_distance2d','from':'p0','to':'p1','axis':'X','dimension':'w'},
                           {'id':'height','type':'axis_distance2d','from':'p0','to':'p3','axis':'Y','dimension':'h'},
                           {'id':'diameter','type':'diameter','circle':'hole','dimension':'d'}],
            'profiles':[{'id':'plate','outer':['l0','l1','l2','l3'],'holes':[['hole']]}]}


class IndependentGeometryTests(unittest.TestCase):
    def test_residual_units_and_bad_dimension(self):
        s=rectangle();r=verify_constraints(s,{'w':.08,'h':.05,'d':.01});self.assertEqual(r['status'],'pass')
        r=verify_constraints(s,{'w':.081,'h':.05,'d':.01});self.assertEqual(r['status'],'fail');self.assertAlmostEqual(r['constraints'][1]['verification_residual'],1)
    def test_reference_is_measurement_only(self):
        s=rectangle();s['constraints'][1]['mode']='reference'
        r=verify_constraints(s,{'w':.1,'h':.05,'d':.01});self.assertEqual(r['status'],'pass');self.assertEqual(r['constraints'][1]['measured_value'],80)
    def test_profile_hole_and_error_bound(self):
        s=rectangle();r=tessellate_profile(s,s['profiles'][0],.02)
        self.assertEqual(r['nesting_depths'],[0,1]);self.assertGreater(signed_area(r['loops'][0]),0);self.assertLess(signed_area(r['loops'][1]),0)
        self.assertLessEqual(r['max_chord_error_mm'],.02)
    def test_outside_hole(self):
        s=rectangle();s['entities'][4]['seed']=[90,25]
        with self.assertRaises(SketchError) as cm:tessellate_profile(s,s['profiles'][0])
        self.assertEqual(cm.exception.code,'hole_outside')
    def test_touching_hole_analytic(self):
        s=rectangle();s['entities'][4]['seed']=[5,25]
        with self.assertRaises(SketchError):tessellate_profile(s,s['profiles'][0],1)
    def test_self_crossing(self):
        s=rectangle();s['entities'][1]['seed'],s['entities'][2]['seed']=[80,50],[80,0]
        with self.assertRaises(SketchError):tessellate_profile(s,s['profiles'][0])
    def test_open_profile(self):
        s=rectangle();s['profiles'][0]['outer'].pop()
        with self.assertRaises(SketchError) as cm:tessellate_profile(s,s['profiles'][0])
        self.assertEqual(cm.exception.code,'open_profile')
    def test_segment_budget(self):
        s=rectangle()
        with self.assertRaises(SketchError):tessellate_profile(s,s['profiles'][0],1e-12,30)
    def test_arc_two_halves(self):
        s={'entities':[{'id':'c','kind':'point2d','seed':[0,0]}]+[{'id':n,'kind':'arc2d','center':'c','radius_seed':5,'start_angle_seed':a,'sweep_seed':180} for n,a in [('a',0),('b',180)]]}
        r=tessellate_profile(s,{'id':'disk','outer':['a','b']});self.assertGreater(signed_area(r['loops'][0]),0)
    def test_overlapping_arcs(self):
        s={'entities':[{'id':'c','kind':'point2d','seed':[0,0]}]+[{'id':n,'kind':'arc2d','center':'c','radius_seed':5,'start_angle_seed':a,'sweep_seed':270} for n,a in [('a',0),('b',180)]]}
        with self.assertRaises(SketchError):entity_intersections(entity_map(s),'a','b')
    def test_tangent_side_domain(self):
        s={'entities':[{'id':n,'kind':'point2d','seed':p} for n,p in [('a',[0,0]),('b',[10,0]),('c',[5,2])]]+[{'id':'line','kind':'line_segment2d','start':'a','end':'b'},{'id':'circle','kind':'circle2d','center':'c','radius_seed':2}],
           'constraints':[{'id':'tan','type':'tangent_line_circle','line':'line','circle':'circle','domain':'segment','side':'left'}]}
        self.assertEqual(verify_constraints(s,{})['status'],'pass');s['constraints'][0]['side']='right';self.assertEqual(verify_constraints(s,{})['status'],'fail')
        s['constraints'][0]['side']='left';s['entities'][2]['seed']=[15,2];self.assertEqual(verify_constraints(s,{})['status'],'fail')
        s['constraints'][0]['domain']='infinite';self.assertEqual(verify_constraints(s,{})['status'],'pass')
    def test_tangent_arc_sweep_domain(self):
        s={'entities':[{'id':n,'kind':'point2d','seed':p} for n,p in [('a',[0,0]),('b',[10,0]),('c',[5,2])]]+[{'id':'line','kind':'line_segment2d','start':'a','end':'b'},{'id':'arc','kind':'arc2d','center':'c','radius_seed':2,'start_angle_seed':0,'sweep_seed':180}],
           'constraints':[{'id':'tan','type':'tangent_line_arc','line':'line','arc':'arc','domain':'segment','side':'left'}]}
        self.assertEqual(verify_constraints(s,{})['status'],'fail');s['entities'][-1]['sweep_seed']=-180;self.assertEqual(verify_constraints(s,{})['status'],'pass')
    def test_circle_internal_external(self):
        s={'entities':[{'id':n,'kind':'point2d','seed':p} for n,p in [('a',[0,0]),('b',[3,0])]]+[{'id':'ca','kind':'circle2d','center':'a','radius_seed':5},{'id':'cb','kind':'circle2d','center':'b','radius_seed':2}],
           'constraints':[{'id':'tan','type':'tangent_circle_circle','a':'ca','b':'cb','branch':'internal','internal_outer':'ca'}]}
        self.assertEqual(verify_constraints(s,{})['status'],'pass');s['constraints'][0]['internal_outer']='cb';self.assertEqual(verify_constraints(s,{})['status'],'fail')
        s['constraints'][0].update({'branch':'external'});s['entities'][1]['seed']=[7,0];self.assertEqual(verify_constraints(s,{})['status'],'pass')
    def test_angles_and_equal_signed_spacing(self):
        s={'entities':[{'id':n,'kind':'point2d','seed':p} for n,p in [('a',[0,0]),('b',[2,0]),('c',[4,2])]]+[{'id':'l','kind':'line_segment2d','start':'a','end':'b'},{'id':'v','kind':'line_segment2d','start':'b','end':'c'}],
           'constraints':[{'id':'angle','type':'angle','a':'l','b':'v','dimension':'angle','directed':True},{'id':'equal','type':'equal_spacing','points':['a','b','c'],'axis':'X','signed':True}]}
        self.assertEqual(verify_constraints(s,{'angle':math.pi/4})['status'],'pass')
        s['entities'][2]['seed']=[0,2];self.assertEqual(verify_constraints(s,{'angle':math.pi/4})['status'],'fail')
    def test_mirror_branch(self):
        s=rectangle();a=branch_signature(s);s['entities'][2]['seed']=[80,-50];s['entities'][3]['seed']=[0,-50]
        self.assertEqual(check_branch(a,branch_signature(s))['status'],'branch_changed')
    def test_derived_arc_endpoint_link(self):
        s={'entities':[{'id':'c','kind':'point2d','seed':[0,0]},{'id':'p','kind':'point2d','seed':[0,5]},{'id':'a','kind':'arc2d','center':'c','radius_seed':5,'start_angle_seed':0,'sweep_seed':90}],
           'constraints':[{'id':'join','type':'arc_endpoint_coincident','arc':'a','endpoint':'end','point':'p'}]}
        self.assertEqual(verify_constraints(s,{})['status'],'pass')
        s['entities'][1]['seed']=[0,4]
        self.assertEqual(verify_constraints(s,{})['status'],'fail')
    def test_semicircular_profile_branch_area(self):
        s={'entities':[{'id':'c','kind':'point2d','seed':[0,0]},{'id':'p0','kind':'point2d','seed':[5,0]},{'id':'p1','kind':'point2d','seed':[-5,0]},{'id':'a','kind':'arc2d','center':'c','radius_seed':5,'start_angle_seed':0,'sweep_seed':180},{'id':'base','kind':'line_segment2d','start':'p1','end':'p0'}],
           'profiles':[{'id':'half','outer':['a','base']}]}
        self.assertEqual(branch_signature(s)['profile_winding']['half'],1)
        self.assertEqual(tessellate_profile(s,s['profiles'][0])['status'],'pass')
        s['entities'][3]['sweep_seed']=-180
        self.assertEqual(branch_signature(s)['profile_winding']['half'],-1)
    def test_dense_curve_bounded_sweep(self):
        s={'entities':[{'id':'c','kind':'point2d','seed':[0,0]},{'id':'circle','kind':'circle2d','center':'c','radius_seed':100}]}
        r=tessellate_profile(s,{'id':'dense','outer':['circle']},.00001,20000)
        self.assertGreater(r['vertex_count'],7000)
        self.assertLess(r['polygon_candidate_comparisons'],100000)
        self.assertGreater(r['max_chord_error_mm'],0)
        self.assertLessEqual(r['max_chord_error_mm'],.00001)
    def test_profile_callback_cancellation(self):
        s=rectangle()
        def cancel():raise SketchError('cancelled','authored cancellation probe')
        with self.assertRaises(SketchError) as cm:tessellate_profile(s,s['profiles'][0],budget_check=cancel)
        self.assertEqual(cm.exception.code,'cancelled')
    def test_unit_normalization(self):
        s=metric_sketch(rectangle(),'m');self.assertEqual(s['entities'][1]['seed'],[80000,0])
    def test_degenerate_line_rejected_even_reference(self):
        s=rectangle();s['entities'][1]['seed']=[0,0]
        with self.assertRaises(SketchError):verify_constraints(s,{'w':0,'h':.05,'d':.01})


if __name__=='__main__':unittest.main()
