# Opt-in source-bound reflection strips

`hardsurface.observe` supports an optional per-view `reflection_anchor` object.
Legacy requests, light placement and normalized fingerprints are unchanged when
it is absent. This mode changes only temporary diagnostic emitters. Material,
roughness, source geometry/normals and camera remain fixed.

An anchored view requires `diagnostic_preset=reflection_strips`,
`normal_policy=geometry_normals_v1`, `wire.enabled=false`, evaluated mesh state,
a single explicit visible object, an explicit camera, and no display explosion.
See the strict observation schema for numeric bounds and required pins.

The request pins source SHA, evaluated geometry SHA, native-normal SHA, object
world-matrix SHA and normalized view SHA. It selects an actual native triangle
index, exact vertex/loop/face correspondence and barycentric location. Normals
are read from that native triangle's corners, not accepted from callers. The
selected point must be visible, within camera clips and the named image ROI.
Grazing/back-facing anchors fail. Hash pins establish local content identity,
not permission or proof of user authorization.

Compute view SHA with `reflection_anchor.view_fingerprint(normalized_view)`
before adding its anchor object. ROI uses [left,bottom,right,top], image-normalized.
Three stripe offsets, width and length are specified in camera-image millimetres
at the object projection plane. The planner reflects the camera direction about
the anchor normal and maps the camera-space stripe orientation onto that plane.
Energy is deliberately unchanged from the legacy rig; new physical emitter
area and actual energy are reported. No equal-radiance equivalence is claimed.

Predicted diagnostics trace at most 32x32 ROI grid samples using camera and
emitter visibility against the one bound object. Predicted mirror hits are
separate from observed PNG samples. A mirror hit does not model roughness or
Cycles-specific shading and is not an observed bright pixel. A finite grid can
miss small features or narrow bands. Actual native emitter values, rather than
ideal planner values, are used for the predictions.

The saved wire-free PNG is sampled at those visible positions. Uniform sampled
values fail the numerical contrast diagnostic; nonuniform values remain
inconclusive until actual stripe traversal/coverage is reviewed. Neither stage
automatically passes visual or surface-quality acceptance. A flat-face anchor
can leave curved regions unobserved; qualify a separate local anchor only when
needed. Reports retain geometry/normal preservation independently.

HOST tests cover binding/schema rejection, planar reflection math, image-space
orientation/width, backward/parallel/off-rectangle rays, grazing and uniform-
image behavior. Native Blender/render behavior still requires a separately
authorized small validation batch. No automatic render retries or broad search.
