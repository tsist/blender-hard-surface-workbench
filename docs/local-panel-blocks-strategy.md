# Experimental local panel blocks

`quad.panel` accepts the optional `topology_strategy: "local_patch_blocks"`.
The default `tiled` and comparison `sparse_annulus` constructors retain their
original geometry logic. This candidate does not claim SubD suitability,
globally minimal counts, artistic approval, or broader asset qualification.

## Caller contract

Provide `local_patch_bounds: [xmin, ymin, xmax, ymax]` in the panel's world XY
millimetres. These bounds are a technical routing frame, not a new asset shape.
They must remain identical across edits whose requested scope is local hole
motion. They are required by both the exported JSON Schema and runtime contract
only when choosing `local_patch_blocks`; other modes do not require them.

The frame must be strictly inside the rectangle joining the four rounded-outline
corner centers. It must wholly enclose the circular through-hole. The supported
frame aspect is 0.75 to 1.5. Only one circular through-hole is accepted; no lips,
counterbores or automatic fallback. Invalid geometry, sampling, seams or quality
causes rejection, never silent changes to geometry or the final quality policy.

## Explicit block graph

1. Sample the hole from its own radius and chord bound. Round upward to a multiple
   of 16, minimum 32, maximum 64, for four side-aligned transition schedules
2. One local O-grid ring maps toward the fixed rectangular frame. Eight, twelve,
   or sixteen 4-fine:2-coarse blocks terminate its density before that frame
3. Each 4:2 block has five fine boundary vertices, three coarse boundary vertices,
   three interior points and six convex quads. Groups never straddle a frame
   corner. Actual coordinates must pass convexity/angle/aspect planning checks
4. The outer planar core uses eight rectangular macroblocks surrounding the
   excluded frame, with conforming coarse samples. It never uses a bore-to-outline
   interpolation or a global fine Cartesian sample union
5. Four straight-outline bands have two radial rows. Four independent corner
   sectors use 6 or 12 outline arc edges, reduced locally 3:1 to 2 or 4 inner arc
   edges, then one or two inner quads. Their radial seams always have two spans,
   so corner arc density cannot propagate into the rectangular field

The hole count is independent of outline curvature and target-driven outer spans.
The coarse frame count is half the hole count and imposes only the necessary
conforming schedule on adjacent coarse blocks. This is a bounded family, not an
arbitrary independent boundary-count mesher.

`target_edge_length` chooses left/right and top/bottom exterior block counts,
straight-outline samples and wall-height segments. The frame-center intervals
follow the coarse frame schedule rather than an additional global sample grid.

## Geometry and limits

All original nominal geometry and the literal chord bound on each approved arc
are retained. An extra conservative sum budget for compound curved corners is
recorded separately; it does not change the user's original tolerance contract.
Corners requiring more than 12 samples per quarter, holes requiring more than 64
samples, outer boundaries above the declared `max_segments` or 384, an exterior
interval above 64 spans, or an estimated planar face count above 10,000 reject
before mesh allocation. The shared MeshBuilder also retains its allocation caps.

Planar local targets remain 8° minimum angle, 174.5° maximum, aspect <=40. The
existing final source/evaluated quad gate is unchanged (175°/50 or existing
support-band policy) and includes actual native self-intersection checking.
Passing the local planner never substitutes for that gate or actual image review.
The six-quad transition has some valence-6 frame-side anchors and valence-7
frame-corner anchors, in addition to 3/4/5 vertices. This candidate is an explicit
mesh, and does not claim a 3/5-only pole pattern or SubD suitability. These anchors
must be inspected visually, not hidden behind an all-quad count.

## Edit invariants and scope

For valid hole-position edits within the same fixed frame, polygon connectivity
and all frame/exterior coordinates are identical; only strictly interior
coordinates move. No intermediate interpolation ring spans the plate.
Thickness is independent of XY and planar routing. Wall-height subdivision can
change at target-length thresholds; connectivity preservation is claimed only
for the tested edits that stay in the same longitudinal schedule.

The neutral qualification includes 32/48/64 hole samples, 6/12 corner samples,
translation, no bevel, target 4, a -4 mm hole move, and a thickness increase.
Actual control and evaluated Blender 5.2.2 LTS meshes are inspected separately.
Technical fixture evidence and schematic previews do not approve B01 production
or imply completed reflection/visual acceptance.
