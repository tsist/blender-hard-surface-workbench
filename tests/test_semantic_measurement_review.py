"""Independent dev6 host review: synthetic meshes, no Blender execution.

The annular fixture isolates membership behavior. Its outer shell deliberately
is not the declared rounded panel, so these tests never claim shape acceptance.
"""
import copy
import math
import unittest

from hardsurface.io import RuntimeFailure
from hardsurface.subd_panel_measure import measure_panel
from hardsurface.semantic_bore_measure import semantic_bore_samples


REFERENCE = {'size': [96, 62], 'corner_radius': 9, 'edge_bevel': .7,
             'center': [0, 0], 'z_min': 0., 'z_max': 5.5,
             'holes': [{'center': [11, 1], 'radius': 8.5}]}


def annular_fixture(jitter=0., radius=8.5, axis=(11., 1.), stations=(0., 2.75, 5.5)):
    """Authored indices define membership; no coordinate-derived selection."""
    n = 96
    vertices = []
    lookup = {}
    for k, z in enumerate(stations):
        for q, r in enumerate((radius, 9.2, 20.)):
            for i in range(n):
                lookup[k, q, i] = len(vertices)
                vertices.append([axis[0] + r*math.cos(math.tau*i/n),
                                 axis[1] + r*math.sin(math.tau*i/n),
                                 z + (jitter if k == len(stations)-1 and q == 1 and i % 2 == 0 else 0.)])
    faces = []
    bore = []
    support = []
    for i in range(n):
        j = (i + 1) % n
        for q in range(2):
            faces.append([lookup[0,q,i], lookup[0,q,j], lookup[0,q+1,j], lookup[0,q+1,i]])
            if q == 0:
                support.append(len(faces))
            faces.append([lookup[len(stations)-1,q,i], lookup[len(stations)-1,q+1,i], lookup[len(stations)-1,q+1,j], lookup[len(stations)-1,q,j]])
        for k in range(len(stations)-1):
            bore.append(len(faces))
            faces.append([lookup[k,0,i], lookup[k+1,0,i], lookup[k+1,0,j], lookup[k,0,j]])
            faces.append([lookup[k,2,i], lookup[k,2,j], lookup[k+1,2,j], lookup[k+1,2,i]])
    return vertices, faces, bore, support


class SemanticMeasurementIndependentReview(unittest.TestCase):
    def measured(self, vertices, faces, indices, reference=None):
        return measure_panel(vertices, faces, reference or REFERENCE,
                             tolerance_mm=.05, bore_face_indices=indices,
                             measurement_profile='semantic_bore_v1')

    def test_support_jitter_does_not_change_real_boundary_membership(self):
        stable = None
        for jitter in (0., .5e-6, 1.1e-6, 3.23e-6):
            with self.subTest(jitter_mm=jitter):
                v, f, bore, _ = annular_fixture(jitter)
                row = self.measured(v, f, bore)
                actual = row['semantic_bore_membership']['boundaries']
                if stable is None:
                    stable = actual
                self.assertEqual(actual, stable)
                self.assertEqual(row['hole_slices']['top_rim']['vertices'], 96)
                self.assertTrue(all(s['status'] == 'pass' for s in row['hole_slices'].values()))
                self.assertEqual(row['tolerance_mm'], .05)

    def test_legacy_jitter_bug_is_preserved_and_explicitly_unqualified(self):
        v, f, _, _ = annular_fixture(1.1e-6)
        row = measure_panel(v, f, REFERENCE, tolerance_mm=.05)
        self.assertEqual(row['hole_slices']['top_rim']['status'], 'fail')
        self.assertAlmostEqual(row['hole_slices']['top_rim']['max_radial_error_mm'], .7)
        self.assertEqual(row['measurement_profile'], 'legacy_geometry_diagnostic_v0')
        self.assertNotEqual(row['qualification_status'], 'pass')

    def test_balanced_wall_cap_contamination_is_rejected(self):
        v, f, bore, support = annular_fixture(1.1e-6)
        contaminated = bore[1:] + support[:1]
        self.assertEqual(len(contaminated), len(bore))
        with self.assertRaises(RuntimeFailure):
            self.measured(v, f, contaminated)

    def test_whole_support_added_is_rejected(self):
        v, f, bore, support = annular_fixture(1.1e-6)
        with self.assertRaises(RuntimeFailure):
            self.measured(v, f, bore + support)

    def test_reversed_wall_winding_is_rejected(self):
        v, f, bore, _ = annular_fixture()
        f[bore[0]].reverse()
        with self.assertRaises(RuntimeFailure):
            self.measured(v, f, bore)

    def test_nonmanifold_actual_closure_is_rejected(self):
        v, f, bore, support = annular_fixture()
        f.append(f[support[0]][:])
        with self.assertRaises(RuntimeFailure):
            self.measured(v, f, bore)

    def test_missing_actual_closure_is_rejected(self):
        v, f, bore, support = annular_fixture()
        missing = support[0]
        f.pop(missing)
        bore = [i - (i > missing) for i in bore]
        with self.assertRaises(RuntimeFailure):
            self.measured(v, f, bore)

    def test_wrong_radius_is_not_hidden_by_semantic_selection(self):
        v, f, bore, _ = annular_fixture(radius=8.6)
        row = self.measured(v, f, bore)
        self.assertEqual(row['status'], 'fail')
        self.assertTrue(all(s['status'] == 'fail' for s in row['hole_slices'].values()))
        self.assertGreater(row['feature_witnesses']['semantic_wall_radial_witness']['maximum_radial_error_mm'], .05)

    def test_wrong_axis_is_not_hidden_by_semantic_selection(self):
        v, f, bore, _ = annular_fixture(axis=(11.1, 1.))
        row = self.measured(v, f, bore)
        self.assertEqual(row['status'], 'fail')
        self.assertEqual(row['feature_witnesses']['semantic_wall_radial_witness']['status'], 'fail')

    def test_local_bulge_between_stations_remains_a_failure(self):
        v, f, bore, _ = annular_fixture(stations=(0., 1.375, 2.75, 4.125, 5.5))
        # The quarter-height displacement lies outside all three station rows.
        # All selected vertices must still be checked against the frozen axis.
        index = f[bore[0]][1]
        v[index][0] += .1
        row = self.measured(v, f, bore)
        self.assertEqual(row['status'], 'fail')
        self.assertEqual(row['feature_witnesses']['semantic_wall_radial_witness']['status'], 'fail')

    def test_boundary_membership_preserved_when_storage_is_permuted(self):
        v, f, bore, _ = annular_fixture()
        first = self.measured(v, f, bore)
        count = len(v)
        v.reverse()
        f = [[count - 1 - i for i in face] for face in f]
        f.reverse()
        bore = [len(f) - 1 - i for i in bore]
        second = self.measured(v, f, bore)
        self.assertEqual(first['hole_slices'], second['hole_slices'])

    def test_membership_alone_never_claims_source_qualification(self):
        v, f, bore, _ = annular_fixture()
        row = self.measured(v, f, bore)
        self.assertEqual(row['qualification_status'], 'not_established_by_measurement_alone')
        self.assertEqual(row['semantic_bore_membership']['source_binding'],
                         'caller_supplied_membership_requires_external_source_binding')

    def test_invalid_or_implicit_membership_fails_closed(self):
        v, f, bore, _ = annular_fixture()
        for indices in (None, [], [True], [len(f)], [bore[0], bore[0]]):
            with self.subTest(indices=indices), self.assertRaises(RuntimeFailure):
                self.measured(v, f, indices)
        with self.assertRaises(RuntimeFailure):
            measure_panel(v, f, REFERENCE, tolerance_mm=.05, bore_face_indices=bore)


class SemanticTransportIndependentReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_subdivision_source_binding import native_panel, refine_mock_quad_mesh
        from hardsurface.subdivision_source import capture_semantic_source
        cls.obj, cls.binding = native_panel()
        cls.source = capture_semantic_source(cls.obj, unit_scale=1., expected_binding=cls.binding,
                                             require_bound=True)
        cls.meshes = [cls.obj.data]
        for _ in range(3):
            cls.meshes.append(refine_mock_quad_mesh(cls.meshes[-1]))

    def test_all_reviewed_levels_have_complete_24_sector_transport(self):
        from hardsurface.subdivision_source import evaluated_bore_domain
        for level, mesh in enumerate(self.meshes):
            with self.subTest(level=level):
                result = evaluated_bore_domain(mesh, self.source, level=level)
                self.assertEqual(len(result['bore_face_indices']), 24 * 4**level)
                proof = result['evidence']['parent_patch_topology']
                self.assertEqual(proof['connected_disk_regions'], 992)
                self.assertEqual(proof['boundary_edges_per_source_quad'], 4 * 2**level)
                self.assertTrue(proof['oriented_source_neighbour_cycles_match'])

    def test_two_real_bore_sector_labels_cannot_be_swapped_wholesale(self):
        from hardsurface.structure_native import FACE_SLOT
        from hardsurface.subdivision_source import evaluated_bore_domain
        mesh = copy.deepcopy(self.meshes[2])
        rows = mesh.attributes[FACE_SLOT].data
        bore_parents = [i for i, role in enumerate(self.source['parent_surface_by_slot'])
                        if role == self.source['bore_label']]
        a, b = bore_parents[0], bore_parents[12]
        before = sorted(x.value for x in rows)
        for row in rows:
            if row.value == a:
                row.value = b
            elif row.value == b:
                row.value = a
        self.assertEqual(sorted(x.value for x in rows), before)
        with self.assertRaises(RuntimeFailure) as exc:
            evaluated_bore_domain(mesh, self.source, level=2)
        self.assertEqual(exc.exception.code, 'SUBDIVISION_SEMANTIC_PATCH')

    def test_two_real_bore_child_labels_cannot_be_swapped(self):
        from hardsurface.structure_native import FACE_SLOT
        from hardsurface.subdivision_source import evaluated_bore_domain
        mesh = copy.deepcopy(self.meshes[2])
        rows = mesh.attributes[FACE_SLOT].data
        bore_parents = [i for i, role in enumerate(self.source['parent_surface_by_slot'])
                        if role == self.source['bore_label']]
        a, b = bore_parents[0], bore_parents[12]
        ia = next(i for i, row in enumerate(rows) if row.value == a)
        ib = next(i for i, row in enumerate(rows) if row.value == b)
        rows[ia].value, rows[ib].value = rows[ib].value, rows[ia].value
        with self.assertRaises(RuntimeFailure) as exc:
            evaluated_bore_domain(mesh, self.source, level=2)
        self.assertEqual(exc.exception.code, 'SUBDIVISION_SEMANTIC_PATCH')

    def test_vertex_reordering_preserves_transport_without_index_assumption(self):
        from hardsurface.subdivision_source import evaluated_bore_domain
        mesh = copy.deepcopy(self.meshes[1])
        count = len(mesh.vertices)
        mesh.vertices.reverse()
        for polygon in mesh.polygons:
            polygon.vertices = [count - 1 - i for i in polygon.vertices]
        for edge in mesh.edges:
            edge.vertices = [count - 1 - i for i in edge.vertices]
        result = evaluated_bore_domain(mesh, self.source, level=1)
        self.assertEqual(len(result['bore_face_indices']), 96)

    def test_authenticated_source_bore_set_crosschecks_authored_sector_names(self):
        from unittest.mock import patch
        from hardsurface.structure_native import validate_native_structure
        from hardsurface.subdivision_source import capture_semantic_source
        report = validate_native_structure(self.obj, unit_scale=1., expected_binding=self.binding)
        mapping = report['authorship']['face_map']
        a = 'bore_wall/sector:0'
        b = 'plane/top/bore_support/sector:0'
        mapping[a], mapping[b] = mapping[b], mapping[a]
        with patch('hardsurface.subdivision_source.validate_native_structure', return_value=report):
            with self.assertRaises(RuntimeFailure) as exc:
                capture_semantic_source(self.obj, unit_scale=1., expected_binding=self.binding,
                                        require_bound=True)
        self.assertEqual(exc.exception.code, 'SUBDIVISION_SEMANTIC_SOURCE')


if __name__ == '__main__':
    unittest.main()
