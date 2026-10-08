"""HOST-only opt-in reflection anchor schema/math/integrity regressions."""
import copy
import json
import math
import sys
import types
import unittest
from unittest import mock
from hardsurface import observation as o, reflection_anchor as a, reflection_coverage as adapter
from hardsurface import contract as c
from hardsurface.io import RuntimeFailure

OID='12345678-1234-4234-8234-123456789abc'
M=[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]
def request():
    r={'schema_version':'1.0','command':'hardsurface.observe','params':{
        'request_id':'anchor.host.fixture','source':{'file':'/tmp/anchor.blend','expected_sha256':'a'*64},
        'wire':{'enabled':False},'diagnostic_preset':'reflection_strips','normal_policy':'geometry_normals_v1',
        'views':[{'name':'whole','visible_object_ids':[OID],'camera':{'position_mm':[-210,260,210],'target_mm':[0,0,0],'up_axis':'Z','ortho_scale_mm':220}}]}}
    r=o.validate_request(r);v=r['params']['views'][0]
    v['reflection_anchor']={'mode':'surface_anchor_v1','source_sha256':'a'*64,'positions_topology_sha256':'b'*64,
        'native_normals_sharp_smooth_sha256':'c'*64,'matrix_world_sha256':c.fingerprint(M),'view_sha256':a.view_fingerprint(v),
        'object_id':OID,'triangle_index':0,'triangle_vertices':[0,1,2],'triangle_loops':[0,1,2],'polygon_index':0,
        'barycentric':[.3,.3,.4],'distance_mm':400.,'stripe_width_screen_mm':10.,'stripe_length_screen_mm':100.,
        'offsets_screen_mm':[-20.,0.,20.],'roi':[.1,.1,.9,.9]}
    return r

def record():return {'positions_topology_sha256':'b'*64,'native_shading':{'native_normals_sharp_smooth_sha256':'c'*64}}
def basis():
    v=a.unit((-.53202927,.65870303,.53202933));x=a.unit((-.77794123,-.62833703,0));return v,x,a.unit(a.cross(v,x))

class AnchorTests(unittest.TestCase):
    def test_valid_normalized_without_mutation(self):
        r=request();prior=copy.deepcopy(r);self.assertEqual(o.validate_request(r),prior);self.assertEqual(r,prior)
    def test_identity_binding_all_fields(self):
        r=request();v=r['params']['views'][0];q=v['reflection_anchor'];a.require_identity(q,'a'*64,record(),M,v)
        for key in ('source_sha256','positions_topology_sha256','native_normals_sharp_smooth_sha256','matrix_world_sha256','view_sha256'):
            bad=copy.deepcopy(q);bad[key]='d'*64
            with self.subTest(key=key),self.assertRaises(RuntimeFailure):a.require_identity(bad,'a'*64,record(),M,v)
    def test_stale_camera_or_roi_binding(self):
        r=request();r['params']['views'][0]['camera']['ortho_scale_mm']=221
        with self.assertRaises(c.ContractError):o.validate_request(r)
    def test_schema_rejects_arbitrary_normal_and_missing_pins(self):
        for key in ('source_sha256','positions_topology_sha256','native_normals_sharp_smooth_sha256','triangle_loops','polygon_index'):
            r=request();del r['params']['views'][0]['reflection_anchor'][key]
            with self.subTest(key=key),self.assertRaises(c.ContractError):o.validate_request(r)
        r=request();r['params']['views'][0]['reflection_anchor']['normal_world']=[0,0,1]
        with self.assertRaises(c.ContractError):o.validate_request(r)
    def test_invalid_limits(self):
        for key,value in [('distance_mm',0),('distance_mm',10001),('stripe_width_screen_mm',float('inf')),('barycentric',[0,0,0]),('roi',[.5,.1,.4,.9]),('offsets_screen_mm',[0,0,1]),('triangle_index',-1)]:
            r=request();r['params']['views'][0]['reflection_anchor'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(c.ContractError):o.validate_request(r)
    def test_incompatible_modes(self):
        for key,value in [('normal_policy',None),('diagnostic_preset','neutral')]:
            r=request()
            if value is None:del r['params'][key]
            else:r['params'][key]=value
            with self.assertRaises(c.ContractError):o.validate_request(r)
        for key,value in [('mesh_state','control'),('visible_object_ids',[OID,'f'*8+'-ffff-ffff-ffff-'+'f'*12]),('explode_z_mm',{OID:1})]:
            r=request();v=r['params']['views'][0];v[key]=value;v['reflection_anchor']['view_sha256']=a.view_fingerprint(v)
            with self.assertRaises(c.ContractError):o.validate_request(r)
    def test_plan_both_orientations(self):
        q=request()['params']['views'][0]['reflection_anchor'];v,x,y=basis();p=(0,0,5);n=(0,0,1)
        for angle in (0,90,-45):
            plan=a.plan(p,n,v,x,y,q,angle);theta=math.radians(angle);sx=a.add(a.mul(x,math.cos(theta)),a.mul(y,math.sin(theta)))
            for light,offset in zip(plan['lights'],q['offsets_screen_mm']):
                tangent=a.sub(sx,a.mul(v,a.dot(n,sx)/a.dot(n,v)));point=a.add(p,a.mul(tangent,offset))
                self.assertIsNotNone(a.ray_rectangle(point,a.reflect(v,n),light))
                long=light['long_axis_world'];r=light['outward_axis_world'];short=light['short_axis_world']
                image_long=a.sub(long,a.mul(r,a.dot(n,long)/a.dot(n,r)))
                self.assertAlmostEqual(a.dot(image_long,sx),0,places=6)
                image_short=a.sub(short,a.mul(r,a.dot(n,short)/a.dot(n,r)))
                self.assertAlmostEqual(abs(a.dot(image_short,sx))*light['size_mm'],10,places=6)
                self.assertAlmostEqual(a.dot(a.cross(short,long),r),1,places=8)
    def test_wrong_hemisphere_negative_control(self):
        v,x,y=basis();n=(0,0,1);self.assertLess(a.dot(a.reflect(v,n),v),0)
        light={'position_mm':a.mul(v,400),'outward_axis_world':v,'short_axis_world':x,'long_axis_world':y,'size_mm':1000,'size_y_mm':1000}
        self.assertIsNone(a.ray_rectangle((0,0,5),a.reflect(v,n),light))
    def test_ray_rectangle_bounds_and_emitting_side(self):
        l={'position_mm':(0,0,10),'outward_axis_world':(0,0,1),'short_axis_world':(1,0,0),'long_axis_world':(0,1,0),'size_mm':2,'size_y_mm':4}
        self.assertEqual(a.ray_rectangle((0,0,0),(0,0,1),l),10)
        for p,d in [((2,0,0),(0,0,1)),((0,3,0),(0,0,1)),((0,0,20),(0,0,-1)),((0,0,0),(1,0,0))]:self.assertIsNone(a.ray_rectangle(p,d,l))
    def test_grazing_degenerate_rejected(self):
        q=request()['params']['views'][0]['reflection_anchor']
        for n,v,x,y in [((0,0,1),(1,0,0),(0,1,0),(0,0,1)),((0,0,1),(0,0,-1),(1,0,0),(0,1,0)),((0,0,1),(0,0,1),(0,0,1),(0,1,0))]:
            with self.assertRaises(RuntimeFailure):a.plan((0,0,0),n,v,x,y,q,0)
    def test_barycentric(self):
        self.assertEqual(a.barycentric((.2,.3,0),(0,0,0),(1,0,0),(0,1,0)),(.5,.2,.3))
        with self.assertRaises(RuntimeFailure):a.barycentric((0,0,0),(0,0,0),(0,0,0),(0,0,0))
    def test_observed_never_visual_pass(self):
        self.assertEqual(a.observed_summary([],0)['status'],'inconclusive')
        self.assertEqual(a.observed_summary([(0,0,0)]*4,4)['status'],'fail')
        r=a.observed_summary([(0,0,0),(1,1,1)],2);self.assertEqual(r['status'],'inconclusive');self.assertEqual(r['surface_quality_inference'],'none')
    def test_png_adapter_removes_own_temporary_image(self):
        im=types.SimpleNamespace(size=(2,2),pixels=[0.,0.,0.,1.,1.,1.,1.,1.,0.,0.,0.,1.,1.,1.,1.,1.])
        images=types.SimpleNamespace(load=mock.Mock(return_value=im),remove=mock.Mock())
        with mock.patch.dict(sys.modules,{'bpy':types.SimpleNamespace(data=types.SimpleNamespace(images=images))}):
            r=adapter.observe_png('/tmp/x.png',{'width':2,'height':2},[(0,0),(1,0)])
        self.assertEqual(r['status'],'inconclusive');images.remove.assert_called_once_with(im)
    def test_schema_matches_runtime_json(self):
        from pathlib import Path
        self.assertEqual(json.loads((Path(__file__).parents[1]/'schemas/hardsurface-observe.schema.json').read_text()),o.schema())
    def test_json_schema_optional_mode(self):
        import jsonschema
        jsonschema.Draft202012Validator(o.schema()).validate(request())
        r=request();del r['params']['normal_policy']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.Draft202012Validator(o.schema()).validate(r)


class _Vec(tuple):
    def __new__(cls,value):return tuple.__new__(cls,value)
    def __mul__(self,value):return _Vec(v*value for v in self)
class _Identity:
    def __iter__(self):return iter(M)
    def __matmul__(self,v):return _Vec(v)
    def to_3x3(self):return self
    def inverted(self):return self
    def transposed(self):return self

class NativeAdapterHostMocks(unittest.TestCase):
    def setUp(self):
        self.r=request();self.view=self.r['params']['views'][0];self.q=self.view['reflection_anchor']
        self.q['roi']=[0,0,1,1];self.q['view_sha256']=a.view_fingerprint(self.view)
        self.mesh=types.SimpleNamespace(vertices=[types.SimpleNamespace(co=_Vec(p)) for p in [(0,0,0),(.001,0,0),(0,.001,0)]],
            loop_triangles=[types.SimpleNamespace(vertices=(0,1,2),loops=(0,1,2),polygon_index=0)],
            corner_normals=[types.SimpleNamespace(vector=_Vec((0,0,1))) for _ in range(3)],calc_loop_triangles=mock.Mock())
        self.proxy=types.SimpleNamespace(data=self.mesh,matrix_world=_Identity())
        self.point=(.3,.4,0)
        self.tree=types.SimpleNamespace(ray_cast=mock.Mock(return_value=(_Vec(self.point),None,0,10)))
        self.factory=types.SimpleNamespace(FromPolygons=mock.Mock(return_value=self.tree))
        self.modules={'mathutils':types.SimpleNamespace(Vector=_Vec),'mathutils.bvhtree':types.SimpleNamespace(BVHTree=self.factory)}
        self.camera={'position_mm':[0,0,10],'ortho_scale_mm':10,'clip_start_m':.000001,'clip_end_m':100}
    def prepare(self):
        return adapter.prepare(self.proxy,self.view,self.q,'a'*64,record(),self.camera,(1,0,0),(0,1,0),(0,0,1))
    def test_prepare_reads_actual_corner_normals_and_world_mm(self):
        before=[tuple(x.vector) for x in self.mesh.corner_normals]
        with mock.patch.dict(sys.modules,self.modules):ctx=self.prepare()
        self.assertEqual(ctx['normal'],(0,0,1));self.assertEqual(ctx['point'],self.point)
        self.assertEqual(before,[tuple(x.vector) for x in self.mesh.corner_normals]);self.mesh.calc_loop_triangles.assert_called_once()
        self.assertEqual(self.factory.FromPolygons.call_args.args[0],[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)])
        self.assertEqual(self.factory.FromPolygons.call_args.kwargs,{'all_triangles':True,'epsilon':0.0})
    def test_prepare_rejects_actual_triangle_correspondence(self):
        self.mesh.loop_triangles[0].loops=(2,1,0)
        with mock.patch.dict(sys.modules,self.modules),self.assertRaises(RuntimeFailure):self.prepare()
    def test_prepare_rejects_occluded_or_missing_anchor(self):
        for hit in (None,_Vec((.3,.4,1))):
            self.tree.ray_cast.return_value=(hit,None,0,9)
            with mock.patch.dict(sys.modules,self.modules),self.assertRaises(RuntimeFailure):self.prepare()
    def test_prepare_rejects_coincident_other_triangle_and_uses_near_clip(self):
        with mock.patch.dict(sys.modules,self.modules):
            self.prepare()
            origin,direction,distance=self.tree.ray_cast.call_args.args
            self.assertAlmostEqual(origin[2],9.999);self.assertAlmostEqual(distance,99999.999)
            self.tree.ray_cast.return_value=(_Vec(self.point),None,1,10)
            with self.assertRaises(RuntimeFailure):self.prepare()
    def test_prepare_rejects_anchor_outside_roi(self):
        self.q['roi']=[0,0,.1,.1]
        with mock.patch.dict(sys.modules,self.modules),self.assertRaises(RuntimeFailure):self.prepare()
    def test_prepare_rejects_native_normal_hash_mismatch_before_bvh(self):
        self.q['native_normals_sharp_smooth_sha256']='d'*64
        with mock.patch.dict(sys.modules,self.modules),self.assertRaises(RuntimeFailure):self.prepare()
        self.factory.FromPolygons.assert_not_called()
    def test_predict_separates_visible_mirror_hits_and_occlusion(self):
        with mock.patch.dict(sys.modules,self.modules):
            ctx=self.prepare();v=dict(self.view,width=1,height=1)
            plan={'lights':[{'position_mm':[0,0,10],'outward_axis_world':[0,0,1],'short_axis_world':[1,0,0],
                'long_axis_world':[0,1,0],'size_mm':10,'size_y_mm':10}]}
            def visible(origin,direction,distance):
                return (_Vec(self.point),None,0,10) if direction[2]<0 else (None,None,None,None)
            self.tree.ray_cast.side_effect=visible
            prediction,samples=adapter.predict(ctx,v,self.q,plan)
            self.assertEqual(samples,[(0,0)]);self.assertEqual(prediction['mirror_hit_samples'],1)
            self.assertEqual(prediction['status'],'informative');self.assertEqual(prediction['surface_quality_inference'],'none')
            self.tree.ray_cast.side_effect=lambda *args:(_Vec(self.point),None,0,1)
            blocked,_=adapter.predict(ctx,v,self.q,plan)
            self.assertEqual(blocked['mirror_hit_samples'],0);self.assertEqual(blocked['blocked_emitter_candidates'],1)
    def test_subpixel_roi_does_not_sample_outside_bounds(self):
        with mock.patch.dict(sys.modules,self.modules):
            ctx=self.prepare();q=dict(self.q,roi=[.5,.5,.50001,.50001])
            prediction,samples=adapter.predict(ctx,self.view,q,{'lights':[]})
            self.assertEqual(samples,[]);self.assertEqual(prediction['status'],'inconclusive')
            self.assertEqual(prediction['pixel_centers_outside_roi_skipped'],1)
    def test_png_bottom_left_indexing_and_cleanup_on_error(self):
        # Row zero is bottom; select bottom-right and top-left with unequal RGB.
        im=types.SimpleNamespace(size=(2,2),pixels=[0.,0.,0.,1., .2,.2,.2,1., .8,.8,.8,1., 1.,1.,1.,1.])
        images=types.SimpleNamespace(load=mock.Mock(return_value=im),remove=mock.Mock())
        with mock.patch.dict(sys.modules,{'bpy':types.SimpleNamespace(data=types.SimpleNamespace(images=images))}):
            result=adapter.observe_png('/tmp/x.png',{'width':2,'height':2},[(1,0),(0,1)])
            self.assertAlmostEqual(result['sample_luminance_min'],.2);self.assertAlmostEqual(result['sample_luminance_max'],.8)
            im.size=(1,1)
            with self.assertRaises(RuntimeFailure):adapter.observe_png('/tmp/x.png',{'width':2,'height':2},[(0,0)])
        self.assertEqual(images.remove.call_count,2)

if __name__=='__main__':unittest.main()
