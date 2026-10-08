# Structural preparation API, dev.4

This is host-only M1 preparation while M0 and G0 remain open. It is not a new qualified constructor, not a production entry point and not a complete first-batch work product.

## Implemented surface

- `structure_contract.schema()/validate_request()/plan_request()`: strict `structure-plan/1.0` read-only declaration schema, explicit compatibility/domain rejection, reference evidence binding without accepting caller-declared approval, source/output alias checks, unmeasured estimates and explicit blockers
- `structure_contract.migrate_legacy_panel()`: incomplete proposal plus explicit losses/required additions; no migration of approval, native evidence, ambiguous tolerances or defaults
- `python -B -m hardsurface.structure_cli --request fixtures/structure-plan.valid.json --pretty`: local read-only entry point. It emits JSON on stdout and does not create a job, run Blender, mutate a scene or write files. `planned_with_blockers` is a valid plan response, never construction permission
- `structure_kernel.validate_structure(mesh, structure)`: actual indexed mesh witnesses in millimetres; semantic vertex/face bijections; face edges, winding, connected vertex fans, loop order/anchors/crease/valence, exact region partitions/boundaries and planar port frames
- `structure_kernel.verify_selection()`: current topology, geometry, crease attribute and structure signatures must all match
- `structure_kernel.verify_edit()`: exact before-state signatures, protected coordinates/connectivity/adjacency and semantic witnesses, total typed identity lineage, dependent selection invalidation. Crease changes are deliberately unsupported by this edit proof
- `structure_adapter.adapt_authored_mesh()`: reads the legacy authored array/provenance shape, requires caller-supplied semantic maps, partitions connected actual face regions, extracts oriented real boundaries and planar ports, and sends them through the independent kernel. It does not call the old constructor or create geometry

There are two separate data layers: proposed design declarations in `structure-plan/1.0`, and actual mesh witnesses in the kernel's `schema_version: 1.0`. They are not interchangeable JSON, and there is no implicit conversion that turns a proposal into actual native evidence. Binding the declared structure/authoring schedule, actual native extraction and job receipt is still pending M0/native integration.

## Kernel mesh witness shape

`mesh` contains `vertices` as 3D millimetre coordinates, `faces` as actual index cycles, and optional `edge_creases` as `[actual_vertex_a, actual_vertex_b, weight]`. Arrays have implementation resource limits. Nonfinite or inexact integer-to-float coordinates reject instead of colliding in signatures.

`structure` contains exact `schema_version`, `length_unit`, bijective `vertex_map` and `face_map`, `regions`, `loops`, `boundary_ports`, and explicit numeric tolerances (`position_mm`, `unit_vector`, `area_mm2`). Semantic IDs must be unique across all entity kinds. IDs are authored identities, not raw vertex index strings. Missing identity maps stop adaptation; no nearest-point guess is made.

- Loop: `id`, `role`, ordered `vertex_ids`, `closed: true`, unit `normal`, anchor `{vertex_id, position_mm}`; optional actual `crease` and `expected_valence`
- Region: `id`, `role`, `feature_id`, `face_ids`, `boundary_loop_ids`; all regions exactly partition all faces. Each region is edge-connected and every boundary cycle matches the actual face winding
- Port: `id`, `loop_id`, `region_id`, exact `point_order`, right-handed frame `{origin_mm, x_axis, y_axis, normal}` and `allowed_transitions`. Only planar ports are implemented. Declaring `resample_explicit` as a permissible future family does not implement a resampler or permit silent nearest matching

The adapter creates bounded connected-component/loop IDs from caller-owned semantic identities and role provenance. A changed authoring schedule may change those IDs. Such a change must be expressed in explicit lineage; the adapter does not certify long-term rebuild stability. The old dev.3 control-loop evidence remains independent, not automatically inherited.

## Evidence semantics

`status: pass` from the kernel means the listed raw-data checks passed. It does not mean approved reference, native extraction, valid SubD surface, acceptable triangle exceptions, no self-intersection, correct dimensions, visual quality, object protection or production qualification. Every report lists these `not_checked` fields and keeps `qualification: not_run`.

No quantization is used for the geometry signature. Loop/face semantic IDs keep geometric and topological signatures stable under actual-array renumbering. Array/order changes in the structure declaration conservatively invalidate its signature. All nonzero actual crease values contribute separately to the attribute signature.

Edit contracts explicitly name affected old vertex, face and semantic entity IDs. Undeclared coordinates, adjacency, connectivity and witness records must be unchanged. Every old/new entity has a typed continue/split/merge/delete/create relation. Reusing an existing ID through a delete+create pair rejects. Topology-preserving edits allow only continuation. Topology change cannot use an affected-face declaration to attach new neighbors to protected vertices.

Implementation limits are safety bounds for the host checker, not measured commercial asset budgets: 200,000 vertices/faces, 10,000 entity records per group, 1,000,000 cumulative loop/port point references, and one total face partition. Production resource limits remain unset until measured and approved.

## Pending native and workflow integration

1. Restore and independently verify the exact authorized Blender/CLI environment
2. Reproduce the unchanged dev.3 baseline and known visual failure, under separate first-run permission
3. Add an authored stable-ID schedule to the actual constructor, bind declarations to actual extraction, and run native witnesses against generated single-hole output
4. Reuse existing job/protection/checkpoint/reopen machinery; integrate the new checks into complete domain work units and fresh full regression tests
5. Approve visual reference G0 before any new practical sample; implement the minimal production geometry change only afterward
6. Native source/evaluated checks, dimensions, self-intersection, actual multi-angle/native-wire/highlight observation, edit, reopen, holdout and G8 remain required

The sample JSON in `fixtures/structure-plan.valid.json` is deliberately synthetic: placeholder file hashes, unresolved tolerances/observations and false production authority. It must not be submitted as an approved sample or used to materialize its fictional source paths.
