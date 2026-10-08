"""HOST-only integrity checks; no native result or user approval is fabricated."""
import copy,unittest
from hardsurface.measurement_binding import bind_constructor_measurement
from hardsurface.io import RuntimeFailure
P={'size':[96.,62.],'center':[0.,0.],'corner_radius':9.,'edge_bevel':.7,'z_min':0.,'z_max':5.5,'chord_tolerance':.05,'holes':[{'kind':'circle','center':[11.,1.],'radius':8.5}]}
def fixture():
 d={'nominal_design':{'size_mm':[96.,62.],'center_mm':[0.,0.],'corner_radius_mm':9.,'edge_roundover_radius_mm':.7,'z_range_mm':[0.,5.5],'hole_center_mm':[11.,1.],'hole_radius_mm':8.5},'error_tolerance_mm':.05}
 n={'status':'pass','native_extraction':{'status':'pass','source':'raw object.data'},'authorship':{'parameter_binding':{'constructor':'quad.panel/subd_control_cage','coordinate_space':'object_local_mm','parameters':copy.deepcopy(P)}}}
 return d,n
class BindingTests(unittest.TestCase):
 def test_unchanged_input_and_separate_authority(self):
  d,n=fixture();before=copy.deepcopy((d,n));r=bind_constructor_measurement(d,n);self.assertEqual(before,(d,n));self.assertEqual(r['technical_tolerance_mm'],.05);self.assertEqual(r['parameters']['size'],P['size']);self.assertEqual(r['evidence']['reference_numeric_acceptance'],'not_established_by_this_integrity_check')
 def test_every_nominal_field_is_bound(self):
  for key in fixture()[0]['nominal_design']:
   d,n=fixture();v=d['nominal_design'][key];d['nominal_design'][key]=[x+1 for x in v] if isinstance(v,list) else v+1
   with self.subTest(key=key),self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_threshold_cannot_be_relaxed_in_metadata(self):
  for x in (.1,.5,True,0,None,'0.05',float('nan'),float('inf')):
   d,n=fixture();d['error_tolerance_mm']=x
   with self.subTest(x=x),self.assertRaises((RuntimeFailure,ValueError)):bind_constructor_measurement(d,n)
 def test_stale_nominal_metadata_after_parameter_edit_rejected(self):
  d,n=fixture();n['authorship']['parameter_binding']['parameters']['holes'][0]['radius']=8.
  with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_host_report_cannot_be_promoted(self):
  for field,val in [('status','fail'),('native_extraction',{'status':'pass','source':'host fixture'})]:
   d,n=fixture();n[field]=val
   with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_missing_fields_fail(self):
  for key in ['nominal_design','error_tolerance_mm']:
   d,n=fixture();d.pop(key)
   with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_extra_nominal_fields_fail_forward(self):
  d,n=fixture();d['nominal_design']['unknown_radius']=1
  with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_unsupported_coordinate_space_rejected(self):
  d,n=fixture();n['authorship']['parameter_binding']['coordinate_space']='world_mm'
  with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
 def test_boolean_nominal_alias_rejected(self):
  d,n=fixture();d['nominal_design']['center_mm'][0]=False
  with self.assertRaises(RuntimeFailure):bind_constructor_measurement(d,n)
if __name__=='__main__':unittest.main()
