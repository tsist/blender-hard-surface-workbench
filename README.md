# Hard Surface Workbench — reflected-anchor diagnostic candidate (dev.21)

A development plugin and bundled CLI for protected, complete Blender work units.
This source copy is not installed or enabled. No production or visual approval
is conveyed by a source archive or passing HOST tests.

## Development status

See the [development plan](docs/development-plan.md) for the exact evidence
boundary through dev.21 and pending native, visual and acceptance gates.
All-quad meshes may still have poor edge flow and excessive face counts.

## Source-bound reflected-strip placement

The optional per-view reflection anchor fixes diagnostic light placement for
oblique flat faces. It authenticates native geometry/corner normals and camera
binding, preserves source shape/shading, and reports predicted mirror hits
separately from sampled PNG contrast. See [the contract](docs/reflection-anchor.md).
This increment has HOST-only validation; native rendering and visual acceptance
remain pending. Package protocol stays 0.2.0 for dispatcher compatibility;
`CANDIDATE.json` and exact source identity distinguish dev.21-r1.

## Optional complete corner columns

The explicit `sparse_cage.corner_columns` selector adds two complete midpoint
columns inside the existing sparse constructor. Semantic reconstruction and the
bounded user axis-edit route remain explicit; experimental fitting coefficients
are not enabled. See the [versioned constructor](docs/corner-midpoint-constructor.md).

## Sparse evaluated-mesh diagnostic

The explicit `source_bound_sparse_diagnostic_v1` profile samples a preserved
source at L0–L3 and exports real indexed geometry with authenticated face domains.
L2 is the formal target; available finite-distance/bbox screens and missing
required checks are reported separately. No complete G3 or production pass is
assigned. See [the bounded diagnostic contract](docs/sparse-subdivision-diagnostic.md).

## Current sparse route

`quad.panel` with `topology_strategy: sparse_control_cage` constructs a bounded,
closed, single sharp circular through-hole plate. Default A/B variants contain
622/654 source quads respectively; top-plane counts are127/143, not whole-body
counts. The default outer profile uses four bands at each roundover.

For new straight-structure work, declare the explicit versioned
`sparse_cage.layout` fixed-frame policy. Its macrogrid is axis aligned and
independent of live hole position or radius. The same ordered exterior slots
continue through the upper roundover, side and lower roundover. Corner/tangent
anchors are retained; local corner transitions remain visible.

The layout frame is caller-owned technical placement, chosen for the intended
edit envelope. It is not a nominal design change or a universal range of legal
hole positions. Insufficient spacing, clearance, convexity or local annulus
angles outside30–150 degrees reject without moving protected geometry or
relaxing tolerances. These are HOST construction gates, not shape acceptance.

See [the exact source-stage contract](docs/sparse-source-stage.md),
[candidate identity](CANDIDATE.json), and the generated `schemas/` authority.
The actual request step belongs in
`params.design.state.features[].program.steps[]`.

## Explicit axis-plane insertion

Opt in with `sparse_cage.insertion_policy: axis_plane_v1` and a declared fixed
frame. Each insertion specifies east/south and fraction0.2–0.8 of the frozen
BASE strip's common coordinate interval. It does not apply the same percentage
to every unequal edge. All new east points share exactly one X coordinate, or
south points one Y coordinate; existing vertices are preserved exactly.

At most two insertions are supported. One additional east or south cut can
follow an initial east cut; duplicate, crowded, non-straddling, ambiguous and
third cuts reject. Legacy top/bottom interfaces propagate32→34→36 through
every outer profile and side. Inserted edge loops have separate semantic
identities and verified closed valence-four continuation. They are not mislabeled
as positive-Z boundary ports. A crossing cut explicitly expands the old loop
and creates one new loop with complete lineage.

The saved-design patch appends exactly one declaration while preserving the
entire old prefix. Policy, layout and nominal edits cannot be bundled into that
transaction. Native epoch increments once and stale selections are invalidated.
Public HOST axis transactions require canonical authored storage and reject
unsupported rearranged HOST graph storage before mutation; validated native
storage permutations remain supported through semantic reconstruction.

Default uninserted counts above are not the count after construction cuts.
Always read actual whole-body and region counts from the current report. No
unlimited repeat-edit or arbitrary existing-mesh repair capability is claimed.

## Mandatory complete-source preflight

A fixed-frame request is admitted only after fresh whole-body HOST checks pass
for authored metre coordinates and independently derived float32-metre storage
predictions. Required gates include finite closed structure, exact authored
identity, all five body regions, unchanged-default polygon quality, and
conservative distinct-face intersection predictions in both coordinate domains.
Every face is checked, including ordinary roundover faces and wall support bands.
No absent, failed or `not_run` predicate can be replaced by a generic success bit.

New-scene source-cage submissions perform this plan before creating a queue
record or launching any process. Saved-source edits first obtain their guarded
saved state, then run the same planner before construction. Native verification
still runs independently: a HOST prediction is not native evidence.

A multi-state project preflight must declare exact state names and independently
computed expected source bindings. Aggregate acceptance requires every state,
all its gates, and its exact binding; repeated reports cannot stand in for edits.
Use a dedicated fresh manifest-exact source copy for the project matrix,
separate from unit-test execution copies and their generated evidence folders.
Retain raw per-state reports and before/after implementation fingerprints. Neutral unit
test counts cannot substitute for the current project's complete fixture matrix.

## Complete work units and evidence

- Bundled `./hardsurface-cli describe --section quad.panel` exposes current fields
- `hardsurface run --request` owns construction, required checks, guarded save
  and independent reopen as one scheduled unit
- `quality.stage: source_cage`, preview level0 and disabled rendered wire keep
  source construction separate from evaluated geometry and rendering
- Source receipts remain partial verified candidates; evaluated shape,
  surface observation and production qualification stay `not_run`
- `hardsurface mesh inspect --request` extracts saved raw mesh data and writes
  lightweight JSON/SVG whole/region views without modifier evaluation
- Exact semantic identities, parameter bindings, ordered ports and edit scopes
  guard independent nominal edits and bounded whole-body strip insertion
- Fixed-frame insertion fractions0.2–0.8 preserve equal coordinate components;
  floating interpolation is not allowed to shift a bound constant plane

HOST geometry and storage-protocol mocks do not count as Blender execution.
The sparse full-qualification route currently fails closed. Actual source
review, native edits, evaluated shape, surface observation and holdouts require
separate evidence in their intended order.

## Compatibility and boundaries

Layout `fixed-frame-axis-aligned/1.1` balances the two eastern exterior spans;
`1.0` retains its earlier guard-relative corridor formula exactly. Both leave
frame selection and support spacing as explicit caller-owned technical values.
The semantic v2 connectivity graph is retained, while explicit layout schema
and geometry candidate version identify the revised placement policy. Omitting
`layout` preserves the earlier sparse mapping for compatibility; existing
saved models are never silently remapped. Legacy fractional interpolation is
retained for exact HOST reconstruction; certain non-midpoint legacy fractions
can still reject at the exact port-plane guard. The new fixed-frame policy
fixes that behavior without changing historical coordinates. Older construction and diagnostic
routes remain explicit compatibility interfaces. Their historical results do
not qualify this candidate.

The bundled CLI is the executable entry point. No installed global blenderctl
wrapper or global plugin configuration is assumed. Qualified source guards and
bounded job resource policies remain mandatory. Game export and other asset
families are later development stages.

## HOST tests

Run `python3 -B -m unittest discover -s tests -v` from an isolated copy of this
source tree with a fresh cache prefix. Preserve executable modes when copying.
Generated schemas must match their Python authorities. Tests are neutral HOST
fixtures, not a claim of native or user visual qualification.

SPDX-License-Identifier: GPL-3.0-or-later
