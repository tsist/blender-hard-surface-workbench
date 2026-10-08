# Experimental sparse panel strategy

Explicit opt-in: `quad.panel.topology_strategy: "sparse_annulus"`. Omission or
`"tiled"` uses the unchanged legacy constructor. This candidate is independent
of the reflection-observer development snapshot. No install or publication.

## Domain and parameter roles

Exactly one circular through-hole; no counterbore, lip, extra hole, open profile,
or SubD modifier. Width/depth 1..2.2, outline-radius/depth .08.. .22,
hole-radius/depth .08.. .18; hole offset at most .18 width and .08 depth.
Round-over fits radius and half thickness, and is at most .04 depth. Fine
quarter-arc count must be <=15: the deliberate three-arc-vertex reduction quad
would otherwise approach the unchanged 175-degree gate. Invalid or unqualified
layouts explicitly reject before MeshBuilder allocation; no automatic fallback.
These are candidate engineering boundaries, not dimensions to design a part to.

`target_edge_length` bounds straight-outline and wall-height segment lengths.
Unlike tiled, it does not impose a global flat-face grid. Broad planar spans use
the first passing count among one through four. `chord_tolerance` still bounds
individual arcs. Internally the candidate conservatively allocates half to XY
sampling, then uses the actual unused sag budget for the edge round-over. The
reported compound-surface distance bound is an additional diagnostic, not a new
approved design constraint or a claim that legacy's independent arcs failed.

## Why these edges exist

The circular rim is a continuous O-loop without reduction poles. Its minimum
chord count and actual count are recorded separately. Extra hole samples match
the coarse outline routing loop and eightfold alignment for exact bore axes.
They are not obtained by unioning corner angle rays. Thus arbitrary hole-radius
changes can alter both boundary schedules; only hole-position and thickness
isolation are claimed and tested.

A sparse planar annulus joins the hole to an inset, silhouette-based routing
loop. Only genuinely curved corner arcs use count reduction: three outer edges
join one routing edge with two convex four-vertex polygons. One polygon has
three distinct consecutive arc samples; no such polygon is used on collinear
straight edges. This is not a triangle/NGon construction or post-hoc repair.
There is no interior star pair at each hole segment. Straight outline portions
use one-to-one quads. Every region has face provenance and count metadata.

A fixed, bounded search chooses among four silhouette-based inset fractions.
It is independent of hole position and thickness. The final production gate is
unchanged: source/evaluated polygon type, incidence, angle, aspect, warpage,
actual planar normals and Blender self-intersections still decide acceptance.
Local planning preferences (8..174.5 degrees and aspect40) are stricter than the
final 5..175/50 ordinary-face gate. The CCW helper only establishes authored
polygon winding; it cannot establish non-overlap. Native self-intersection
inspection remains mandatory.

## Qualification and limits

Neutral host fixtures cover hole movement, thickness, target spacing, a wider
outline, smaller bore, translation, and zero bevel. They inspect real generated
vertices/faces, dimensions, requested independent arc errors, both quad-diagonal
triangle interiors, unchanged exterior coordinate sets, and deterministic builds.
Native fixtures inspect actual control/evaluated polygons and self-intersections.
The source metadata reports ring counts/roles, surface/region counts, measured
angular spans, conservative compound bound, and actual valence histogram.

All comparisons are explicit polygon meshes. Fewer faces do not prove visual
quality, a good subdivision cage, ideal edge flow, or a universal minimum. Hole
movement may change the minimal planar band count near a qualification boundary;
fixed exterior coordinates still remain independent. Production visual review,
reference checks, saved-file reopen and task-specific editing are separate.

## Rejected prototype and separate preflight fix

An early dual 3:1 annulus prototype passed numerical shape checks but duplicated
star transitions and exceeded legacy face counts. It is not shipped as an API;
its evidence remains in the development overlay, outside this runtime.

Requesting `reference_consistency` as a machine work-unit/required-quality check
now fails early with `CHECK_UNSUPPORTED`, after valid reference approval checking.
The approved reference package, visual-required field and external review remain
required. The unsupported check is never silently dropped or reported as pass.
