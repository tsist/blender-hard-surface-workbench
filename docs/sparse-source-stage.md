# Sparse source-cage stage

This candidate adds a bounded low-density closed single-hole plate route and
actual source-mesh inspection. It is a development candidate, not a production
shape qualification. No installation or execution permission is conveyed here.

## Actual command boundary

The existing `hardsurface run --request` complete work unit accepts a panel step
with `topology_strategy: sparse_control_cage` and optional `sparse_cage` controls.
The step is inside `params.design.state.features[].program.steps[]`, not a new
top-level `parameters` object. Use `describe --section quad.panel` and the
request schema for exact fields. Schema admission and the narrower geometric
domain are both checked; neither silently changes a design to make it fit.

Set `params.quality.stage: source_cage`, `sparse_cage.preview_levels: 0`,
`wire.enabled: false`, and omit rendered preview. The complete unit constructs
the body, verifies native semantic storage, audits actual control polygons and
distinct-face intersections, writes actual-data JSON/SVG views in that same
worker, saves a new candidate and independently reopens it. Original saved
sources and non-target objects remain protected by the existing host pipeline.

The receipt is `domain_outcome: partial`, `candidate.state: verified_candidate`.
Its scope explicitly leaves evaluated shape, surface observation and production
qualification `not_run`. Source inspection does not grant user visual approval.
The sparse `full` route rejects with `SPARSE_FULL_QUALIFICATION_PENDING` until
its separate evaluated-shape acceptance path is implemented and verified.

## Supported initial topology

- One sharp circular through-hole in one closed rounded rectangular plate
- Initial hole16/outer32 schedule; other counts reject rather than resample
- Conditional hole-planar-support A/B: top127 versus top143, not body totals
- Default four quarter-roundover bands each end, two wall support bands and a
  side-body band: complete622Q /654Q, respectively
- Authored shared indices, full top/bottom/wall connections and outward winding
- New schedule `sparse_sharp_panel_ids_v2`; old24/48 identities are never renamed
  or silently migrated into it

Controls are initial, unfitted L0 positions. The ideal periodic hole-radius seed
is not a measured full-body Catmull-Clark result. Nominal dimensions, technical
chord targets and acceptance tolerances remain separate. The complete source
reports counts by region, actual loops/chains/strips, poles, density and skew.
No face-count or graph-validity result proves artistic quality.

## Fixed-frame straight-structure layout

For a new straight-structure candidate, declare `sparse_cage.layout` with schema
`fixed-frame-axis-aligned/1.1` for balanced eastern exterior spans (or explicit
`1.0` for byte-exact earlier scheduling), a frozen local-mm `feature_frame_mm`
`[xmin,ymin,xmax,ymax]`, and positive `corner_guard_mm`. The frame is technical
control placement, not a replacement nominal hole/outline design. It must be
chosen for the intended, separately authorized edit envelope and reviewed.

The frame defines a four-by-four collar schedule. The surrounding macrogrid
is axis aligned and does not depend on the live hole position or radius.
Non-corner exterior slots correspond directly to that grid's tangential axis,
except the two explicit tangent anchors per side. Corner diagonal slots and
short corner-guard transitions remain visible; this is not a claim that every
edge is orthogonal. The same ordered slots connect every outer profile, side
and bottom ring. No extra grid rows or source faces are added.

The constructor rejects inadequate grid spacing, insufficient ring clearance,
non-convex local hole transitions, and local corner angles outside its declared
30–150 degree construction domain. These are technical candidate bounds, not
user visual acceptance or evaluated shape tolerances. A frame that admits two
independent edits may reject their combination. Always validate the actual
requested state; never infer an arbitrary rectangular edit range.

Changing the frame, guard or policy is a new layout decision, not a nominal
hole edit. The exact immutable layout is bound into the saved parameter and
semantic identity manifest. Absence of `layout` explicitly retains the legacy
dev.11 mapping for compatibility; it does not silently repair existing files.
The v2 semantic graph remains unchanged, while layout schema and geometry
candidate version distinguish new control placement.

## Whole-body predictive admission

The fixed-frame planner computes all required source gates afresh. Both authored
metres and exact float32-metre predictions use the same unchanged polygon
quality policy as actual native control inspection. No region is omitted: in
particular, long ordinary arc faces and narrow wall support bands have distinct
existing limits. Both-diagonal distinct-face intersection prediction is also
required, and is labeled conservative HOST coverage rather than native mesh
triangulation. Native checks remain mandatory after admission.

Each report binds exact source, parameter, provenance and coordinate-domain
hashes. Source-available report checking recomputes structural and polygon
metrics; a self-rehashed JSON file is not authenticated native evidence. A
multi-case aggregate requires predeclared states and their independent expected
bindings, rejects duplicate report reuse and takes a strict AND of every gate.

New-scene source-cage `run` rejects failed preflight before queue or process
creation. A failed project fixture must block source freezing/native requests,
even when neutral package tests pass. For edits, validate the complete intended
state set, not only the baseline or a top-plane angle summary. Expanding a wall
support may solve a wall-aspect failure while leaving ordinary arc faces invalid.
Do not relax policies, relabel faces as support or remove necessary curvature
controls to conceal that failure.

## Editing

The existing saved-design dimension transaction supports independent hole
radius, hole center, explicit-datum thickness and outer-roundover edits. The
before witness declares coordinate axes and full affected face scope before
rebuilding. For the explicit fixed-frame layout, hole edits change only local hole-rim and
optional support-ring coordinates. The macrogrid, collar and exterior remain
exactly fixed. The compatibility layout retains its earlier wider dependency
scope. Outer-roundover
editing preserves the actual hole and collar regions. Native coordinate
binding accepts exact authored values or the exact float32-metre transport,
never an arbitrary expanded positional tolerance.

`insert_sparse_strip` is a narrowly scoped design patch, with explicit feature
ID, step ID, expected feature SHA, east/south corridor and fraction0.2..0.8.
For the compatibility linear-fraction path with an explicit fixed-frame layout,
equal endpoint coordinate components are
preserved exactly during interpolation, including non-midpoint fractions. The
legacy formula remains byte-compatible and can reject some fractional inputs
at its exact port-plane guard; it is not silently upgraded.
Without the explicit axis-plane policy, only one linear-fraction insertion from
the base sparse graph is supported. That compatibility route propagates both
outer interfaces32→34 through the top, roundovers, side and bottom; hole16 is
unchanged. Default east/south trials add44/48 quads to the whole body. The
preflight emits every affected port and old/new vertex, edge and face mapping.
A verified native transaction increments topology epoch exactly once and
invalidates dependent selections. No partial seam can be saved as success.

## Lightweight inspection

`hardsurface mesh inspect --request` is a read-only saved-source work unit.
It extracts raw `object.data`, verifies semantic slots and the external scene
registry, and writes source JSON plus SVGs without rendering or evaluating a
modifier. Whole front/back, top/bottom, hole wall, roundover and side views are
available. Edge/face labels are actual array indices, with full IDs in data.
Regular loops are distinguished from closed chains that turn or cross poles.
Source-stage acceptance currently binds the constructor's identity object
transform; translated design coordinates remain geometry parameters. Arbitrary
object transforms remain readable by standalone inspection, without inheriting
the source-stage acceptance path.

Standalone inspection's optional intersection audit is separate from source
construction's mandatory actual control audit. The construction checkpoint
requires both artifacts. It rechecks exact object coverage, native detail
status, actual mesh arrays/IDs, analysis hashes and reproducible SVG bytes.

## Evidence states

Host geometry and storage-protocol tests are not Blender runs. Source-stage
runtime, user source-cage review, evaluated dimensions, real normals/highlights,
independent holdouts and mature-scope approval each need their own evidence.
Game low-poly/UV/bake/LOD and additional asset families remain separate stages.

## Axis-plane continuation policy

`insertion_policy: axis_plane_v1` is explicit and requires fixed-frame layout.
Its fraction refers to the immutable BASE common interval of the strip's actual
crossed edges; per-edge interpolation is solved for one constant axis coordinate.
All old vertices stay exact and every adjacent body port is updated together.

The policy supports at most two cuts. Each continuation appends one declaration
to the exact previous prefix; coincident, too-close and third cuts fail. Added
faces are derived from the traced strip, not a fixed increment: a crossing cut
traverses the earlier subdivision and can add more faces. Actual top/bottom and
whole-body counts must replace uninserted127/143 shorthand in delivery labels.

Inserted cycles are stored separately from horizontal interface ports, verified
against real graph edges and opposite-face continuation, and persisted as
semantic control loops. When another cut crosses a loop, that loop's identity
expansion and the new loop's creation are both explicit. Old linear-policy and
fixed-frame scheduling outputs remain compatibility paths. Neither source
construction nor HOST storage mocks establish actual native edit success.

Run full unit regressions and each private project matrix in separate execution
copies. A matrix must start from an exact source allowlist and keep both code
and driver fingerprints unchanged; no concurrent test writer may share it.
Preserve failed records rather than converting an integrity failure to a pass.
