"""HOST-only exact normal-isolation checks; no Blender or rendering is run."""
import ast
import copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hardsurface import contract as c
from hardsurface import observation as o
from hardsurface import observation_normals as n
from hardsurface.io import RuntimeFailure

ROOT = Path(__file__).resolve().parents[1]


def request():
    return {'schema_version': '1.0', 'command': 'hardsurface.observe', 'params': {
        'request_id': 'normal-isolation-host',
        'source': {'file': '/tmp/source.blend', 'expected_sha256': 'a' * 64},
        'views': [{'name': 'corner', 'direction': 'three_quarter', 'visible_feature_ids': ['panel']}],
    }}


def mesh():
    vertices = [NS(co=point) for point in ((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.))]
    return NS(vertices=vertices, edges=[NS(vertices=(i, (i + 1) % 4), use_edge_sharp=False) for i in range(4)],
        polygons=[NS(vertices=(0, 1, 2, 3), loop_start=0, loop_total=4, use_smooth=True)],
        loops=[NS(vertex_index=i, edge_index=i) for i in range(4)],
        corner_normals=[NS(vector=[0., 0., 1.]) for _ in range(4)], normals_domain='POINT',
        has_custom_normals=False, attributes={}, library=None, override_library=None, animation_data=None, shape_keys=None)


def modifier():
    return NS(name='HS_Subdivision_Cage', type='SUBSURF', subdivision_type='CATMULL_CLARK',
        levels=2, render_levels=2, quality=6, uv_smooth='PRESERVE_BOUNDARIES', boundary_smooth='ALL',
        use_creases=True, use_limit_surface=True, use_custom_normals=False, show_viewport=True, show_render=True,
        show_in_editmode=True, show_on_cage=False, show_only_control_edges=True,
        use_apply_on_spline=False, use_pin_to_last=False, use_adaptive_subdivision=False,
        adaptive_space='PIXEL', adaptive_pixel_size=1., adaptive_object_edge_length=.01)


class Source:
    def __init__(self):
        self.data = mesh()
        self.evaluated = NS(data=copy.deepcopy(self.data))
        # Give evaluated geometry a deliberately different exact identity.
        self.evaluated.data.vertices[0].co = (-.125, 0., 0.)
        self.evaluated.data.corner_normals[0].vector = [0., .6, .8]
        self.modifiers = [modifier()]
        self.type, self.mode = 'MESH', 'OBJECT'
        self.parent = self.animation_data = self.library = self.override_library = None
        self.constraints = []

    def evaluated_get(self, depsgraph):
        return self.evaluated


class Sockets(list):
    def __getitem__(self, key):
        return next(item for item in self if item.name == key) if isinstance(key, str) else super().__getitem__(key)


class Material(NS):
    # RNA datablock equality compares identity, unlike SimpleNamespace values.
    def __eq__(self, other): return self is other
    def __ne__(self, other): return self is not other


def socket(name, value=0., linked=False):
    return NS(name=name, default_value=value, is_linked=linked)


def f32(value):
    return struct.unpack('>f', struct.pack('>f', value))[0]


def material(preset='neutral'):
    settings = o.diagnostic_settings(preset)
    spec = settings['material_override']
    bsdf = NS(bl_idname='ShaderNodeBsdfPrincipled', mute=False,
        distribution='MULTI_GGX', subsurface_method='RANDOM_WALK',
        inputs=Sockets([socket('Base Color', [f32(value) for value in spec['base_color']]),
            socket('Roughness', f32(spec['roughness'])), socket('Metallic', f32(spec['metallic'])),
            socket('Normal', [0., 0., 0.]), socket('Coat Normal', [0., 0., 0.]), socket('Alpha', 1.)]),
        outputs=Sockets([socket('BSDF')]))
    output = NS(bl_idname='ShaderNodeOutputMaterial', mute=False, is_active_output=True, target='ALL',
        inputs=Sockets([socket('Surface', linked=True), socket('Volume'), socket('Displacement')]))
    link = NS(from_node=bsdf, to_node=output, from_socket=bsdf.outputs['BSDF'],
              to_socket=output.inputs['Surface'], is_valid=True)
    return Material(use_nodes=True, node_tree=NS(nodes=[bsdf, output], links=[link]))


class NormalPolicyContractTests(unittest.TestCase):
    def test_omitted_option_does_not_change_legacy_normalization_or_fingerprint(self):
        raw = request()
        normalized = o.validate_request(raw)
        legacy = copy.deepcopy(o.REQUEST)
        del legacy['properties']['params']['properties']['normal_policy']
        with patch.object(o, 'REQUEST', legacy):
            old = o.validate_request(raw)
        self.assertEqual(normalized, old)
        self.assertEqual(c.fingerprint(normalized), c.fingerprint(old))
        self.assertNotIn('normal_policy', normalized['params'])

    def test_only_explicit_version_is_supported_without_request_mutation(self):
        raw = request(); raw['params']['normal_policy'] = n.POLICY
        before = copy.deepcopy(raw)
        self.assertEqual(o.validate_request(raw)['params']['normal_policy'], n.POLICY)
        self.assertEqual(raw, before)
        for bad in (None, False, {}, [], 'flat', 'geometry_normals_v2'):
            raw['params']['normal_policy'] = bad
            with self.subTest(bad=bad), self.assertRaises(c.ContractError): o.validate_request(raw)

    def test_exact_exported_schema(self):
        self.assertEqual(json.loads((ROOT / 'schemas/hardsurface-observe.schema.json').read_text()), o.schema())
        field = o.schema()['properties']['params']['properties']['normal_policy']
        self.assertEqual(field, {'const': n.POLICY})

    def test_native_boundaries_are_before_render_and_after_wire_cleanup(self):
        tree = ast.parse((ROOT / 'hardsurface/observation.py').read_text())
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
        loop = next(node for node in ast.walk(fn) if isinstance(node, ast.For) and ast.unparse(node.target) == 'view')
        render_try = next(node for node in loop.body if isinstance(node, ast.Try))
        self.assertIn("attest_normals('before_render'", ast.unparse(render_try.body[0]))
        self.assertIn('bpy.ops.render.render', ast.unparse(render_try.body[-1]))
        self.assertIn('wire.cleanup()', ast.unparse(render_try.finalbody[0]))
        after = loop.body[loop.body.index(render_try) + 1]
        self.assertIn("attest_normals('after_render'", ast.unparse(after))
        calls = [ast.unparse(node.func) for node in ast.walk(fn) if isinstance(node, ast.Call)]
        self.assertFalse(any('normals_split_custom_set' in call or 'shade_flat' in call or 'shade_smooth' in call for call in calls))


class NormalAssertions:
    def record(self, value): return n.mesh_record(value, o._mesh_record)

    def rejected(self, call):
        with self.assertRaises(RuntimeFailure) as caught: call()
        self.assertEqual(caught.exception.code, 'OBSERVATION_NORMAL_POLICY')


class NativeNormalRecordTests(NormalAssertions, unittest.TestCase):

    def test_exact_copy_passes_and_preserves_source(self):
        original = mesh(); before = copy.deepcopy(original)
        first = self.record(original); second = self.record(copy.deepcopy(original))
        self.assertEqual(first, second)
        self.assertEqual(vars(original), vars(before))
        self.assertEqual(first['native_shading']['corner_normals'], 4)
        self.assertEqual(first['native_shading']['smooth_faces'], 1)

    def test_normals_contents_not_only_counts_are_bound(self):
        a = mesh(); b = copy.deepcopy(a); b.corner_normals[0].vector = [0., .6, .8]
        self.assertEqual(o._mesh_record(a), o._mesh_record(b))
        self.assertNotEqual(self.record(a), self.record(b))
        self.rejected(lambda: n.require_equal(self.record(a), self.record(b), role='proxy'))

    def test_no_silent_quantization_or_signed_zero_canonicalization(self):
        a = mesh(); b = copy.deepcopy(a); b.corner_normals[0].vector[0] = -0.
        self.assertNotEqual(self.record(a), self.record(b))
        b.corner_normals[0].vector[0] = 2.0 ** -100
        self.assertNotEqual(self.record(a), self.record(b))

    def test_normal_domain_content_and_smooth_sharp_flags_are_bound(self):
        a = mesh(); baseline = self.record(a)
        mutations = [lambda value: setattr(value, 'normals_domain', 'CORNER'),
            lambda value: setattr(value.edges[0], 'use_edge_sharp', True),
            lambda value: setattr(value.polygons[0], 'use_smooth', False)]
        for mutation in mutations:
            b = copy.deepcopy(a); mutation(b)
            self.assertEqual(o._mesh_record(a), o._mesh_record(b))
            self.assertNotEqual(baseline, self.record(b))

    def test_edge_flags_order_is_bound_even_when_sharp_counts_match(self):
        a = mesh(); a.edges[0].use_edge_sharp = True
        b = copy.deepcopy(a); b.edges[0].use_edge_sharp = False; b.edges[1].use_edge_sharp = True
        self.assertEqual(self.record(a)['native_shading']['sharp_edges'], self.record(b)['native_shading']['sharp_edges'])
        self.assertNotEqual(self.record(a), self.record(b))

    def test_face_flags_order_is_bound_even_when_smooth_counts_match(self):
        a = mesh()
        a.polygons.append(NS(vertices=(0, 1, 2, 3), loop_start=4, loop_total=4, use_smooth=False))
        a.loops.extend(copy.deepcopy(a.loops)); a.corner_normals.extend(copy.deepcopy(a.corner_normals))
        b = copy.deepcopy(a); b.polygons[0].use_smooth = False; b.polygons[1].use_smooth = True
        self.assertEqual(self.record(a)['native_shading']['smooth_faces'], self.record(b)['native_shading']['smooth_faces'])
        self.assertNotEqual(self.record(a), self.record(b))

    def test_normal_nonfinite_zero_bad_length_and_bad_count_rejected(self):
        for vector in ([float('nan'), 0., 1.], [float('inf'), 0., 1.], [0., 0., 0.],
                       [0., 0., 2.], [0., 1.], [1., 0., 0., 0.], [1e300, 0., 0.]):
            value = mesh(); value.corner_normals[0].vector = vector
            with self.subTest(vector=vector): self.rejected(lambda: self.record(value))
        value = mesh(); value.corner_normals.pop()
        self.rejected(lambda: self.record(value))

    def test_reported_corner_count_cannot_hide_empty_truncated_or_excess_iteration(self):
        class ReportedFourNormals:
            def __init__(self, actual): self.actual = actual
            def __len__(self): return 4
            def __iter__(self): return iter(self.actual)
        for count in (0, 3, 5):
            value = mesh()
            value.corner_normals = ReportedFourNormals([NS(vector=[0., 0., 1.]) for _ in range(count)])
            self.assertEqual(len(value.corner_normals), len(value.loops))
            with self.subTest(actually_read=count): self.rejected(lambda: self.record(value))

    def test_missing_api_and_bad_domains_rejected(self):
        for field in ('corner_normals', 'normals_domain', 'has_custom_normals', 'attributes'):
            value = mesh(); delattr(value, field)
            with self.subTest(field=field): self.rejected(lambda: self.record(value))
        mutations = [lambda value: setattr(value, 'normals_domain', 'EDGE'),
            lambda value: setattr(value.polygons[0], 'loop_total', 3),
            lambda value: setattr(value.polygons[0], 'loop_start', 1),
            lambda value: setattr(value.loops[0], 'vertex_index', 2),
            lambda value: setattr(value.loops[0], 'edge_index', 2),
            lambda value: setattr(value.edges[0], 'use_edge_sharp', 1),
            lambda value: setattr(value.polygons[0], 'use_smooth', 1)]
        for mutation in mutations:
            value = mesh(); mutation(value)
            self.rejected(lambda: self.record(value))

    def test_custom_normals_and_custom_attribute_rejected(self):
        value = mesh(); value.has_custom_normals = True
        self.rejected(lambda: self.record(value))
        value = mesh(); value.attributes['custom_normal'] = object()
        self.rejected(lambda: self.record(value))

    def test_boolean_attributes_require_actual_domain_count_and_consistency(self):
        for name, domain, count in (('sharp_edge', 'EDGE', 4), ('sharp_face', 'FACE', 1)):
            for wrong in ('domain', 'type', 'count', 'value', 'boolean'):
                value = mesh()
                attr = NS(domain=domain, data_type='BOOLEAN', data=[NS(value=False) for _ in range(count)])
                value.attributes[name] = attr
                self.record(value)
                if wrong == 'domain': attr.domain = 'POINT'
                if wrong == 'type': attr.data_type = 'FLOAT'
                if wrong == 'count': attr.data.pop()
                if wrong == 'value': attr.data[0].value = True
                if wrong == 'boolean': attr.data[0].value = 0
                with self.subTest(name=name, wrong=wrong): self.rejected(lambda: self.record(value))


class SourceAndRenderBoundaryTests(NormalAssertions, unittest.TestCase):
    def source_record(self, obj, state='evaluated'):
        return n.source_record(obj, state, None, o._mesh_record)

    def test_unsupported_stacks_and_normal_customization_rejected(self):
        for typ in ('DATA_TRANSFER', 'NORMAL_EDIT', 'WEIGHTED_NORMAL', 'BEVEL', 'NODES'):
            value = Source(); value.modifiers.append(NS(type=typ))
            with self.subTest(typ=typ): self.rejected(lambda: self.source_record(value))
        for change in (lambda value: setattr(value.modifiers[0], 'use_custom_normals', True),
                       lambda value: setattr(value.data, 'has_custom_normals', True),
                       lambda value: setattr(value.evaluated.data, 'has_custom_normals', True),
                       lambda value: setattr(value.modifiers[0], 'render_levels', 3),
                       lambda value: setattr(value.modifiers[0], 'new_normal_option', True)):
            value = Source(); change(value)
            self.rejected(lambda: self.source_record(value))

    def test_static_local_object_and_mesh_data_required(self):
        for owner_name, fields in (('object', ('parent', 'animation_data', 'library', 'override_library')),
                                  ('data', ('animation_data', 'library', 'override_library', 'shape_keys'))):
            for field in fields:
                value = Source(); owner = value if owner_name == 'object' else value.data
                setattr(owner, field, object())
                with self.subTest(owner=owner_name, field=field): self.rejected(lambda: self.source_record(value))

    def test_requested_actual_control_or_evaluated_state_is_recorded(self):
        value = Source()
        control = self.source_record(value, 'control'); evaluated = self.source_record(value, 'evaluated')
        self.assertEqual(control['source_mesh'], self.record(value.data))
        self.assertEqual(evaluated['source_mesh'], self.record(value.evaluated.data))
        self.assertEqual(control['source_control_mesh'], evaluated['source_control_mesh'])
        self.assertNotEqual(control['source_mesh'], evaluated['source_mesh'])

    def test_complete_source_modifier_settings_identity(self):
        value = Source(); baseline = self.source_record(value)
        value.modifiers[0].quality = 5
        changed = self.source_record(value)
        self.assertEqual(baseline['source_mesh'], changed['source_mesh'])
        self.assertNotEqual(baseline['source_modifier']['settings_sha256'], changed['source_modifier']['settings_sha256'])
        self.rejected(lambda: n.require_equal(baseline, changed, role='source'))

    def boundary(self, state='evaluated'):
        source = Source(); selected = source.data if state == 'control' else source.evaluated.data
        proxy = NS(data=copy.deepcopy(selected), modifiers=[])
        shader = material(); settings = o.diagnostic_settings('neutral')
        args = dict(mesh_state=state, depsgraph=None, geometry_record=o._mesh_record,
            view_layer=NS(material_override=shader), material=shader, diagnostic=settings,
            expected_material=n.material_record(shader, settings), phase='before_render', view_name='corner')
        expected_sources = {'panel': self.source_record(source, state)}
        expected_proxies = {'panel': self.record(proxy.data)}
        def check():
            return n.attest({'panel': source}, {'panel': proxy}, expected_sources, expected_proxies, **args)
        return source, proxy, args, check

    def test_same_frozen_mesh_passes_every_view_boundary(self):
        source, proxy, args, check = self.boundary()
        for angle in (0, 45, 90, 135):
            for phase in ('before_render', 'after_render'):
                args.update(phase=phase, view_name='rotation_' + str(angle))
                self.assertEqual(check()['source_and_proxy_identity'], 'pass')
        self.assertEqual(args['view_name'], check()['view'])

    def test_geometry_unchanged_but_source_or_proxy_normals_drift_rejected(self):
        for state in ('control', 'evaluated'):
            for target in ('source_control', 'source_selected', 'proxy'):
                for phase in ('before_render', 'after_render'):
                    source, proxy, args, check = self.boundary(state)
                    selected = source.data if state == 'control' else source.evaluated.data
                    target_mesh = {'source_control': source.data, 'source_selected': selected, 'proxy': proxy.data}[target]
                    before = o._mesh_record(target_mesh)
                    target_mesh.corner_normals[1].vector = [0., .6, .8]
                    args['phase'] = phase
                    self.assertEqual(before, o._mesh_record(target_mesh))
                    with self.subTest(state=state, target=target, phase=phase): self.rejected(check)

    def test_source_smooth_sharp_geometry_and_proxy_modifiers_drift_rejected(self):
        changes = [lambda source, proxy: setattr(source.data.polygons[0], 'use_smooth', False),
            lambda source, proxy: setattr(source.data.edges[0], 'use_edge_sharp', True),
            lambda source, proxy: setattr(source.evaluated.data.vertices[0], 'co', (0., 0., .1)),
            lambda source, proxy: setattr(proxy.data.polygons[0], 'use_smooth', False),
            lambda source, proxy: setattr(proxy.data.edges[0], 'use_edge_sharp', True),
            lambda source, proxy: proxy.modifiers.append(NS(type='WEIGHTED_NORMAL'))]
        for change in changes:
            source, proxy, args, check = self.boundary(); change(source, proxy)
            self.rejected(check)

    def test_control_proxy_cannot_impersonate_evaluated_geometry(self):
        source, proxy, args, check = self.boundary()
        proxy.data = copy.deepcopy(source.data)
        self.rejected(check)

    def test_material_override_and_material_contents_drift_rejected(self):
        source, proxy, args, check = self.boundary()
        args['view_layer'].material_override = material()
        self.rejected(check)
        source, proxy, args, check = self.boundary()
        args['material'].node_tree.nodes[0].inputs['Alpha'].default_value = .25
        self.rejected(check)

    def test_principled_non_socket_enum_drift_is_rejected_at_render_boundaries(self):
        for name, changed in (('distribution', 'GGX'), ('subsurface_method', 'RANDOM_WALK_SKIN')):
            for phase in ('before_render', 'after_render'):
                source, proxy, args, check = self.boundary()
                before = args['expected_material']['actual_node_checks']['bsdf_node_enums'][name]
                self.assertNotEqual(before, changed)
                setattr(args['material'].node_tree.nodes[0], name, changed)
                args['phase'] = phase
                with self.subTest(property=name, phase=phase): self.rejected(check)


class MaterialAttestationTests(unittest.TestCase):
    def rejected(self, value, preset='neutral'):
        with self.assertRaises(RuntimeFailure) as caught: n.material_record(value, o.diagnostic_settings(preset))
        self.assertEqual(caught.exception.code, 'OBSERVATION_NORMAL_POLICY')

    def test_existing_neutral_and_reflection_presets_are_actually_inspected(self):
        for preset in ('neutral', 'reflection_strips'):
            value = material(preset); actual = n.material_record(value, o.diagnostic_settings(preset))
            self.assertEqual(actual['actual_node_checks']['texture_nodes'], 0)
            self.assertEqual(actual['actual_node_checks']['bsdf_node_enums'],
                             {'distribution': 'MULTI_GGX', 'subsurface_method': 'RANDOM_WALK'})
            self.assertEqual(actual['actual_node_checks']['normal_inputs_unlinked'], ['Coat Normal', 'Normal'])
            self.assertFalse(actual['actual_node_checks']['output_inputs_linked']['Displacement'])
            self.assertEqual(len(actual['actual_node_checks_sha256']), 64)

    def test_even_unconnected_texture_normal_bump_displacement_nodes_rejected(self):
        for typ in ('ShaderNodeBump', 'ShaderNodeNormalMap', 'ShaderNodeTexImage', 'ShaderNodeTexNoise',
                    'ShaderNodeDisplacement', 'ShaderNodeVectorDisplacement', 'ShaderNodeGroup'):
            value = material(); value.node_tree.nodes.append(NS(bl_idname=typ))
            with self.subTest(typ=typ): self.rejected(value)

    def test_normal_links_displacement_links_and_nonzero_normal_defaults_rejected(self):
        for name in ('Normal', 'Coat Normal', 'Base Color'):
            value = material(); value.node_tree.nodes[0].inputs[name].is_linked = True
            self.rejected(value)
        for name in ('Displacement', 'Volume'):
            value = material(); value.node_tree.nodes[1].inputs[name].is_linked = True
            self.rejected(value)
        value = material(); value.node_tree.nodes[0].inputs['Normal'].default_value = [0., 0., 1.]
        self.rejected(value)

    def test_invalid_muted_or_missing_shader_links_and_presets_rejected(self):
        for change in (lambda value: value.node_tree.links.clear(),
                       lambda value: setattr(value.node_tree.links[0], 'is_valid', False),
                       lambda value: setattr(value.node_tree.nodes[0], 'mute', True),
                       lambda value: setattr(value.node_tree.nodes[1], 'is_active_output', False),
                       lambda value: setattr(value.node_tree.nodes[0].inputs['Roughness'], 'default_value', .9),
                       lambda value: setattr(value.node_tree.nodes[0].inputs['Alpha'], 'default_value', float('nan'))):
            value = material(); change(value); self.rejected(value)


if __name__ == '__main__': unittest.main()
