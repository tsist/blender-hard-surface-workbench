# Subdivision control cage candidate

This is an independently developed candidate over the published 0.2.0 protocol
baseline. It is not installed, enabled globally, published, or asserted to work
in 3ds Max. Blender Catmull–Clark is the only implemented backend. The protocol
version stays 0.2.0 because the existing opt-in CLI dispatcher requires it;
`CANDIDATE.json` identifies the unreleased overlay.

## Domain and explicit route

`quad.panel` accepts `topology_strategy: "subd_control_cage"` and an explicit
`local_patch_bounds: [xmin,ymin,xmax,ymax]` in millimetres. Exactly one sharp
circular through-hole, a rounded-rectangle outline, and positive outer edge
roundover are supported. Lips, counterbores, multiple holes, zero roundover and
unqualified control counts reject without a silent fallback. This is a bounded
family, not a general automatic retopologizer.

The optional `subdivision_cage` contains:

- `method`: `CATMULL_CLARK` only
- `hole_segments`: integer 24 only
- `preview_levels`: integer 0–3, default 2

The saved modifier uses matching viewport and render levels, satisfying the
existing generic topology contract. Default is 2/2. Level 0 and level 1 are useful
for editing/diagnosis but are not assumed to satisfy final geometric tolerances.
Final levels 2 and 3 must be tested separately. Level 3 is not secretly applied.
The actual modifier uses quality 6, limit-surface positioning, UV preserve
boundaries, boundary smoothing ALL, and geometric creases enabled.

## Authored topology

A compensated 24-control circular rim is followed by a regular support loop and
an intermediate planar ring. Explicit 3:1 two-quad transition blocks reduce the
24 ring to eight coarse vertices. A separate buffer reaches the caller's fixed
rectangular frame. The surrounding field is axial rectangular blocks. Corner
sampling terminates in flat corner sectors; near-tangent straight guards avoid
letting long straight spans control the curved-outline transition.

The outer roundover has three control spans per quarter, planar-side guards and
wall-side guards. Interior roundover controls are compensated to counter
Catmull–Clark shrinkage. These controls and the circular compensation are an
approximation to the approved shape, not a claim of exact CAD circles or a
universal radius formula. The sparse cage is judged by its evaluated surface,
not by whether its source vertices lie on the final nominal skin.

The specific recipe emits valence 3/4/5 only. Poles remain in the flat routing
patches; this is recipe-specific and does not make arbitrary 3/5 poles safe.
Their actual location, nearby curvature, highlight response and edit behavior
must still be reviewed. Quad count alone is not an acceptance criterion.

Exactly two circular rim cycles have full geometric crease weight 1. They keep
the approved sharp opening tangent discontinuity. They do not lock the radius;
the bore is separately compensated and measured at both rims and in the wall.
All other authored control loops are uncreased. This mode is crease-dependent
Blender geometry and is not claimed to be a support-only portable TurboSmooth
cage. No small hole bevel is silently introduced.

## Actual checks

The source mesh still uses the original strict explicit polygon quality gate.
The bridge separately verifies the actual indexed semantic cycles, closure,
true opposite-edge continuation through valence-4 quad vertices, role/cardinality and bore geometry anchors, finite actual edge crease values and the exact set of
nonzero crease edges. Declaring a loop in metadata is insufficient.

The evaluated Catmull–Clark policy retains source face arity, zero unapproved
triangles/n-gons, degeneracy, concavity, angle/aspect, manifold and native
self-intersection checks. The existing global explicit-mesh policy is unchanged.
Evaluated curved quads are not required to be perfectly coplanar: their warpage
and plane-distance metrics are retained under a named applicability report.
The datum patches are separately measured against their nominal planes.

For this bounded shape, `subd_panel_measure` checks actual mesh vertices, unique
edge midpoints, polygon centroids and both diagonal interpretations' triangle
centroids against a nominal rounded-extrusion/roundover/sharp-bore implicit
surface. `chord_tolerance` supplies the required millimetre tolerance for this
route; it is not widened after a failure. The report contains the worst sample,
probe count and top/middle/bottom bore radii. Feature witnesses additionally require the declared extents/bounds, bore angular coverage and a conservative nominal-volume sanity screen. These reject missing-shape counterexamples but remain non-exhaustive. This is finite sampled evidence,
not a certified Hausdorff bound or proof about an uncomputed analytic limit.

`hardsurface subdivision diagnose` supplies independent levels 0–3, actual native
geometry sidecars and fixed-frame views. Match the candidate's actual settings
explicitly; command defaults are not inherited from the source modifier. Use
three-quarter neutral and top reflection for shallow panels. Inspect the actual
images; a black or clipped reflection view has not completed the visual check.
No execution, metric, or successful render sets artistic/user acceptance.

## Editing and protection

Keeping the fixed frame identical isolates valid hole-position edits to its
interior. Thickness edits preserve XY coordinates and polygon connectivity.
Pure and actual native regression fixtures cover these properties separately. The saved-source work unit permits regeneration only for the exact authored single SubD stack and unchanged managed baseline. Modifier drift fails closed. Transform/pattern operations remain unsupported for this new route.
Unsupported edits fail the domain or actual mesh/shape gate. The old constructor
routes remain available and unchanged. New work is saved as a new file; no
frozen original or global configuration is overwritten.

## Remaining boundaries

The candidate has no UV, displacement, animation/deformation, export round-trip,
film-studio approval, AAA engine budget, or cross-backend qualification. Neutral
parameter fixtures are regression evidence, not independent held-out production
assets. Production acceptance also requires approved references, actual images,
representative edits, saved-file reopening and an independent review.
