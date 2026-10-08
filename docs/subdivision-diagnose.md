# Read-only subdivision diagnosis (independent development candidate)

This operation samples an existing saved control cage with actual Blender 5.2.x
Catmull–Clark at requested levels 0–3. It is implemented in the plugin;
`blenderctl` supervises the complete work unit. Nothing is installed, registered,
or saved back to a `.blend`. Source geometry, transforms, shading attributes,
creases and modifiers remain unchanged. This is a development candidate, not a
claim that arbitrary models become clean SubD models or that TurboSmooth passed.

## Public entry point

Select this reviewed candidate using `BLENDERCTL_HARDSURFACE_ROOT`, then use the
existing opt-in public CLI dispatch. No change to the frozen CLI is required.
Global flags precede the command. The jobs directory must be on a qualified
local filesystem (for this Linux environment, `/tmp` is tmpfs; the workspace
OverlayFS is correctly refused by the existing source-guard policy).

```
python /path/to/blenderctl/cli.py --blender /absolute/blender \
  --jobs-dir /tmp/unique-subdivision-jobs \
  hardsurface describe --section subdivision.diagnose

python /path/to/blenderctl/cli.py --blender /absolute/blender \
  --jobs-dir /tmp/unique-subdivision-jobs \
  hardsurface subdivision diagnose --request /absolute/diagnose.json
```

Minimal request:

```json
{
  "schema_version": "1.0",
  "command": "hardsurface.subdivision.diagnose",
  "params": {
    "request_id": "part-a.subdivision.v1",
    "source": {"file": "/absolute/part-a.blend", "expected_sha256": "<64 hex characters>"},
    "target": {"object_name": "PartA"},
    "stack_mode": "isolated_control_cage"
  }
}
```

`target.object_id` (one UUID) can replace `target.object_name`. They are mutually
exclusive, and the selector must resolve to exactly one current-scene mesh.

- `levels`: defaults to `[0,1,2,3]`, unique, level 0 mandatory, maximum 3
- `settings`: `boundary_smooth` (ALL/PRESERVE_CORNERS), native `uv_smooth`, quality
  1–6, and `use_limit_surface` (default false). Actual modifier properties are
  recorded per sample, not merely the requested subset
- `render`: defaults to enabled, 512×512, three-quarter view; front/back/top/
  bottom/side also supported. Paired neutral and reflection-strip views are
  always generated together when enabled
- `panel_reference`: optional explicit caller-owned sharp-bore rounded-panel
  nominal geometry and tolerance, described below; never inferred from source
  metadata and never treated as user-approved design authority
- `export_geometry`: defaults false; true emits one bounded geometry JSON per
  sampled level, with a file/SHA256/bytes descriptor in `levels[i].geometry`
- `max_evaluated_faces`: default 200,000, hard cap 250,000
- `max_aggregate_faces`: default 400,000, hard cap 500,000
- `cpu_threads`: 1–4, default 2; `wall_seconds`: greater than 0 and at most 600

Every request is bound to source bytes before admission and after consumption.
The host copies the input into its guarded job directory. The original input's
before/after observations are reported; this is not a claim of continuous
immutability of the original on an unqualified filesystem. Never reuse the same
request ID for a changed request or assume a previous failed job was rerun.

## Stack and shading semantics

Only a local static mesh in Object mode is supported. Parenting, constraints,
instances, shape keys and animation are rejected, as are non-SUBSURF modifiers
or multiple modifiers. An empty stack or one SUBSURF is accepted. Its complete
settings are recorded, and the original remains untouched; the isolated clone
gets a newly declared Catmull–Clark modifier for the bounded level sweep. Source
render/viewport level differences are reported, not used for the sweep.

The clone retains geometric crease attributes. Its custom-normal attribute is
removed, all face normals use smooth shading, and sharp-edge shading flags are
cleared. This is verified and separately reported against source shading. Level
0 has the diagnostic subdivision modifier disabled. Its unified smooth
diagnostic shading is explicitly not the original delivered normal appearance;
a sharp hole edge can look visually rounded by that display override. It receives the same
explicit clone-only normal reset as levels 1–3. This avoids inherited weighted
or custom normals disguising cage behavior at the baseline.

The renderer uses a separate temporary scene and one fixed camera/light frame
normalized from level-0 world bounds. The orthographic camera explicitly uses
horizontal sensor fit; its horizontal scale accounts for image aspect ratio.
All eight baseline bounding-box corners must project inside a five-percent safe
frame before rendering, including landscape and portrait outputs. It does not alter source visibility,
camera, world, material assignments, or source scene settings. Neutral uses 32
samples; reflection strips use 128 with denoising off. Adaptive sampling is off,
seed is fixed, and actual camera/light/material settings are in every view.
Reflection strips show localized specular behavior; they do not illuminate or
prove quality over the entire surface. Review neutral views for silhouette.

## What the report means

`control_cage` uses real original polygons and edges:

- face-arity histogram, true quad/triangle/n-gon counts, valence histogram
- connected components, boundary/nonmanifold/wire edges, disconnected vertex
  fans, winding conflicts and interior poles
- edge-loop continuity through interior manifold valence-4 all-quad vertices
- opposite-edge quad-strip/ring continuity, clearly separate from edge loops

A cube can be all quads yet have eight valence-3 poles, zero uninterrupted
regular edge loops, and three closed quad strips. Those counts are observations,
not an automatic surface-quality or support-loop-placement verdict.

Each real evaluated level reports vertices/edges/polygons, separate Blender
loop-triangle count, world bounds, triangle-surface area, oriented volume
integral, edge-manifold counts, components, and a geometry identity. Shrinkage
is relative to level 0: dimensions, bounds insets, center shift, area and volume
integral ratios. An open surface's volume integral is explicitly not enclosed
volume. Even a closed result is not a self-intersection or solid-validity proof.

Without `panel_reference`, all shape changes remain relative to level 0 and no
nominal-reference acceptance is assigned. The optional bounded panel check below
adds finite reference-relative witnesses; it still does not certify complete
dimensional accuracy, reference approval, a Hausdorff bound, thickness clearance,
UV distortion, full surface intersections, or aesthetic acceptance. Consumers
can independently measure the bounded geometry export.

The backend is recorded as actual Blender Catmull–Clark, including Blender
version/build and OpenSubdiv build flag. Autodesk TurboSmooth is not run, and
backend equivalence is not claimed.

## Optional explicit panel reference and authored-loop checks

`params.panel_reference` is a caller-supplied, axis-aligned world-mm reference for
one rounded rectangular plate with uniform outline/Z edge roundover and one
sharp circular through-bore. It must explicitly supply all nominal dimensions:

```json
"panel_reference": {
  "size": [96, 62], "center": [0, 0],
  "corner_radius": 9, "edge_bevel": 0.7,
  "z_min": 0, "z_max": 5.5,
  "holes": [{"center": [11, 1], "radius": 8.5}],
  "tolerance_mm": 0.05
}
```

These are numeric-fixture dimensions, not a default design. The normalized
kind is `single_sharp_bore_rounded_panel`, coordinate space `world`, and units
`mm`; other domains/spaces/units are rejected. Coordinates are bounded to
±100,000 mm, positive lengths to 100,000 mm, tolerance to (0,100] mm. Z thickness
must be positive; corner radius must be below half the smaller XY size; edge
roundover must be smaller than both the corner radius and half thickness. The
entire circular bore must fit strictly inside the planar cap, with no contact
or overlap with the outline roundover. This domain constraint is essential for
the sharp-bore/flat-cap corner-distance calculation.

The plugin calls its bounded reference measurement helper on actual evaluated
world-space geometry at each requested level. `levels[i].reference_check` keeps
the full finite measurement result; `reference_shape` records normalized caller
parameters, their SHA256 digest, domain validation, and each level's status.
Metadata is never substituted for an omitted caller reference. The report
explicitly says caller-supplied parameters do not establish user-approved design
authority. The checks are finite sampled shape checks, not certified continuous
Hausdorff distance or complete dimensional metrology.

A failed reference sample/domain check is reported for that level while the
remaining sweep continues. `acceptance.execution` remains separate from
`acceptance.reference_shape`; source-cage level 0 may fail a tolerance that
levels 2–3 pass. Overall finite-sample acceptance covers every requested level,
so consumers must examine the per-level results rather than hiding level-0/1
failures. A `not_sampled` witness is never silently promoted to a pass.

If the original source declares `hs_subd_control_loops`, the plugin validates
bounded JSON structure and calls its authored-loop helper on original actual
edges, crease values and vertex valence before making the clone. Authored
settings must also be present and match the original native SUBSURF modifier.
Results are under `authored_control_loops`. An ordinary source with neither
metadata field is `not_applicable`; missing/malformed authored records or drift
produce an explicit failed check. Metadata itself cannot establish a valid
cycle, reference approval, or aesthetic quality. The source's metadata signature
is verified again after execution, alongside geometry and saved-file identity.

## Optional geometry export

Sidecar keys:

- `vertices`: local-space points in metres
- `world_vertices_mm`: world-space points in millimetres
- `matrix_world`: exact sampled object transform
- `edges` and `polygons`: real indexed mesh domains for this level
- `loop_triangles`: actual `mesh.calc_loop_triangles()` records, each with
  `vertices` and `polygon_index`; this derived rendering/analysis tessellation
  does not change or inflate source polygon/quad counts
- `level`, `source_sha256`, `local_geometry_sha256`, explicit space/unit and
  index-domain descriptions

Evaluated indices must not be assumed to correspond across levels. Separate
triangle records matter because evaluated quads can be nonplanar: arbitrary fan
triangulation need not reproduce Blender's real diagonals. Reports contain file
identities, never the large geometry arrays inline. Limits are 48 MiB per JSON
and 128 MiB aggregate, in addition to geometry and host disk/RSS caps.

## Resource and preservation boundaries

Source hard bounds: 200,000 faces, 200,000 vertices, 600,000 edges, 1,200,000
corners, 8,192 corners per face. The Catmull–Clark face forecast is checked before
allocation; actual evaluated faces/vertices and aggregate faces are checked too.
Longest render edge ≤2,048, each PNG ≤2 MiB, and all paired views share a
320-million pixel-sample bound. The host has a whole-process-tree wall watchdog,
1 GiB RSS and 512 MiB job-disk budget. These are operational bounds, not
performance guarantees.

Temporary objects, meshes, scene, camera, lights, world and diagnostic material
are owned by the operation and removed in `finally`. No source cleanup or
persistent deletion is performed. Source file identities and target in-memory
geometry/modifier/attribute signature are compared afterward. External source
resources and unsupported scene dependencies fail closed through the existing
worker dependency audit rather than being silently skipped.

## Reproducible qualification

Run host tests with `python -m unittest discover -s tests -p test_subdivision.py`.
Run `scripts/qualify_subdivision_diagnose.py` with absolute `--blender`,
`--blenderctl`, `--jobs-dir` and a new `--out` directory. The script creates only
new authored numeric fixtures, then invokes diagnosis through public
`blenderctl`. It checks real level counts, source preservation, paired image
identities, geometry sidecars, custom-normal reset, an existing recorded SUBSURF,
an open-quad boundary, and fail-closed unsupported-stack/face-budget/target cases.
Numeric harness success never implies visual or user aesthetic acceptance.
