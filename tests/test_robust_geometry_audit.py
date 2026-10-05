import json,math,unittest
from hardsurface.robust_geometry_audit import *

def octahedron(scale=1,shift=(0,0,0)):
    v=[(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
    v=[add(mul(p,scale),shift) for p in v]
    ids=[(0,2,4),(2,1,4),(1,3,4),(3,0,4),(2,0,5),(1,2,5),(3,1,5),(0,3,5)]
    return [[v[i] for i in t] for t in ids]

def legacy_clip(subject,clip,eps=.002):
    poly=subject;trace=[]
    for u,v in zip(clip,clip[1:]+clip[:1]):
        edge=sub(v,u)
        def side(p):return cross2(edge,sub(p,u))
        out=[];prev=poly[-1];sp=side(prev)
        for cur in poly:
            sc=side(cur);pin=sp>=-eps*norm(edge);cin=sc>=-eps*norm(edge)
            if pin!=cin:
                t=sp/(sp-sc);point=add(prev,mul(sub(cur,prev),t));out.append(point);trace.append({'t':t,'point':point})
            if cin:out.append(cur)
            prev,sp=cur,sc
        poly=out
        if not poly:break
    return poly,trace

class GeometryAuditTests(unittest.TestCase):
    def test_legacy_specific_triangle_extrapolation(self):
        a=[(-.001,0),(-.003,.5),(.1,.25)];b=[(0,-1),(1,-1),(0,2)]
        old,trace=legacy_clip(a,b);self.assertTrue(any(abs(t['t']+.5)<1e-12 for t in trace))
        self.assertTrue(any(not point_in_convex_2d(p,a) for p in old))
        new=clip_convex_2d(a,b);self.assertTrue(new['points'])
        self.assertTrue(all(point_in_convex_2d(p,a) and point_in_convex_2d(p,b) for p in new['points']))
        self.assertTrue(all(0<=t['t']<=1 for t in new['crossings']))
    def test_reversed_triangle_winding_clipping(self):
        a=[(0,0),(2,0),(0,2)];b=[(0,1),(1,0),(1,1)]
        p=clip_convex_2d(a,b)['points'];q=clip_convex_2d(a,list(reversed(b)))['points']
        self.assertAlmostEqual(abs(area2(p)),abs(area2(q)))
    def test_disjoint_triangles_remain_empty(self):
        self.assertFalse(clip_convex_2d([(0,0),(1,0),(0,1)],[(2,2),(3,2),(2,3)])['points'])
    def test_non_coplanar_true_crossing_preserved(self):
        a=[(-1,-1,0),(1,-1,0),(0,1,0)];b=[(0,-.5,-1),(0,-.5,1),(0,.5,0)]
        r=triangle_intersection(a,b);self.assertEqual(r['kind'],'noncoplanar')
        self.assertEqual(len(r['points_mm']),2)
        self.assertTrue(all(abs(p[0])<1e-12 and abs(p[2])<1e-12 for p in r['points_mm']))
    def test_non_coplanar_not_silently_projected(self):
        with self.assertRaises(ValueError):coplanar_triangle_intersection([(0,0,0),(1,0,0),(0,1,0)],[(0,0,.01),(1,0,.01),(0,1,.01)])
    def test_full_containment(self):
        outer=octahedron(2);inner=octahedron(.2)
        for point in inner[0]:self.assertEqual(resolve_indeterminate(point,outer,parity_status='indeterminate',closed_outward_topology_pass=True)['status'],'inside')
    def test_interpenetration_has_inside_and_outside_witnesses(self):
        target=octahedron(1);crossing=octahedron(.5,(.8,0,0));points={p for t in crossing for p in t}
        statuses={resolve_indeterminate(p,target,parity_status='indeterminate',closed_outward_topology_pass=True)['status'] for p in points}
        self.assertIn('inside',statuses);self.assertIn('outside',statuses)
    def test_external_point(self):
        r=resolve_indeterminate((2,.1,.1),octahedron(),parity_status='indeterminate',closed_outward_topology_pass=True)
        self.assertEqual(r['status'],'outside');self.assertLess(abs(r['winding']),1e-12)
    def test_boundary_separate_from_winding(self):
        r=resolve_indeterminate((1/3,1/3,1/3),octahedron(),parity_status='indeterminate',closed_outward_topology_pass=True)
        self.assertEqual(r['status'],'boundary');self.assertNotIn('winding',r)
    def test_flipped_normals_do_not_gain_inside(self):
        flipped=[list(reversed(t)) for t in octahedron()]
        raw=raw_solid_angle_winding((0,0,0),flipped);self.assertAlmostEqual(raw['winding'],-1);self.assertEqual(raw['status'],'indeterminate')
        gated=resolve_indeterminate((0,0,0),flipped,parity_status='indeterminate',closed_outward_topology_pass=False)
        self.assertFalse(gated['fallback_used']);self.assertEqual(gated['status'],'indeterminate')
    def test_open_mesh_does_not_use_winding(self):
        r=resolve_indeterminate((0,0,0),octahedron()[:-1],parity_status='indeterminate',closed_outward_topology_pass=False)
        self.assertFalse(r['fallback_used']);self.assertEqual(r['status'],'indeterminate')
    def test_known_parity_not_overridden(self):
        r=resolve_indeterminate((0,0,0),[],parity_status='outside',closed_outward_topology_pass=True)
        self.assertEqual(r['status'],'outside');self.assertFalse(r['fallback_used'])
    def test_contact_epsilon_unchanged(self):
        t=octahedron();p=(1+.001,0,0);r=resolve_indeterminate(p,t,parity_status='indeterminate',closed_outward_topology_pass=True)
        self.assertEqual(r['status'],'boundary');self.assertEqual(r['epsilon_mm'],.002)
        r=resolve_indeterminate((1+.003,0,0),t,parity_status='indeterminate',closed_outward_topology_pass=True);self.assertEqual(r['status'],'outside')

if __name__=='__main__':unittest.main(verbosity=2)
