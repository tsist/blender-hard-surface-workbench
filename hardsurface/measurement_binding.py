"""Bind duplicate constructor measurement metadata to freshly attested parameters.

This is an integrity check, not reference/user approval. Native caller must pass
its freshly computed native structure report; a caller-made dict authenticates
nothing by itself. It never changes the inherited technical error threshold.
"""
from copy import deepcopy
import math
from .io import RuntimeFailure
from .structure_kernel import fingerprint


def bind_constructor_measurement(declared, native_report):
    def fail(message):
        raise RuntimeFailure('SUBD_MEASUREMENT_BINDING_MISMATCH', message)
    if not isinstance(native_report, dict) or native_report.get('status') != 'pass':
        fail('Fresh successful native structure evidence is required')
    extraction=native_report.get('native_extraction',{})
    if extraction.get('status') != 'pass' or extraction.get('source') != 'raw object.data':
        fail('Host-only or metadata-only evidence cannot bind native measurement')
    try:
        author=native_report['authorship'];binding=author['parameter_binding'];p=binding['parameters']
        if binding['constructor']!='quad.panel/subd_control_cage' or binding['coordinate_space']!='object_local_mm':
            fail('Unsupported authored measurement coordinate/constructor domain')
        if len(p['holes'])!=1 or p['holes'][0]['kind']!='circle':
            fail('Exactly one authenticated circular bore required')
        expected={'center_mm':deepcopy(p.get('center',[0,0])), 'size_mm':deepcopy(p['size']),
                  'corner_radius_mm':p['corner_radius'], 'edge_roundover_radius_mm':p['edge_bevel'],
                  'hole_center_mm':deepcopy(p['holes'][0]['center']), 'hole_radius_mm':p['holes'][0]['radius'],
                  'z_range_mm':[p['z_min'],p['z_max']]}
        tolerance=p.get('chord_tolerance',.025)
        if type(tolerance) not in (int,float) or not math.isfinite(tolerance) or tolerance<=0:
            fail('Finite positive authenticated technical tolerance required')
        if not isinstance(declared,dict) or fingerprint(declared['nominal_design'])!=fingerprint(expected):
            fail('Duplicate nominal metadata differs from authenticated authored parameters')
        if fingerprint(declared['error_tolerance_mm'])!=fingerprint(tolerance):
            fail('Duplicate measurement threshold differs from authenticated constructor input')
        domain={'size':deepcopy(p['size']), 'center':deepcopy(p.get('center',[0,0])),
                'corner_radius':p['corner_radius'], 'edge_bevel':p['edge_bevel'],
                'z_min':p['z_min'],'z_max':p['z_max'],
                'holes':[{'center':deepcopy(p['holes'][0]['center']),'radius':p['holes'][0]['radius']}]}
    except (KeyError,TypeError,IndexError,ValueError) as error:
        fail('Incomplete or invalid authenticated measurement binding: '+str(error))
    return {'parameters':domain,'technical_tolerance_mm':tolerance,'evidence':{
        'status':'pass','method':'authored_parameter_measurement_binding_v1',
        'author_parameter_binding_sha256':fingerprint(binding),'nominal_metadata_sha256':fingerprint(expected),
        'technical_tolerance_mm':tolerance,'technical_tolerance_source':'authenticated constructor chord_tolerance (inherited technical gate; unchanged)',
        'reference_numeric_acceptance':'not_established_by_this_integrity_check',
        'nominal_source':'fresh native attestation of authored parameters; metadata equality independently required'}}
