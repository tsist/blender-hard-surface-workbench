# Native surface-field export candidate

The historical contract below remains unchanged. An explicit HOST-only sparse
L0-source/L2-clone adapter is documented in [sparse surface observation](sparse-surface-observation.md); it has no new native qualification.

This opt-in, read-only addition supplies the data needed to relate existing
rendered evidence to an actual evaluated mesh. It does not modify geometry,
author normals, transfer normals, render another image or save a Blender file.
The API is opt-in through the generated diagnosis schema. Native execution of
this candidate had not been performed when this source copy was prepared.

## Why positions and hashes are insufficient

An indexed mesh can locate an image ray on an evaluated triangle and supply a
geometric triangle normal. Blender's smooth CORNER normal can differ. A recorded
normal hash attests identity but does not expose the vector. A face label or a
known subdivision count likewise cannot reconstruct evaluated face ancestry.

A straight-to-circular nominal outline is tangent-continuous and can have a
legitimate one-sided curvature jump. Reflected-band bending alone is not proof
of excess geometry error. Compare the same protected geometry, camera,
reference and actual normal fields before changing a constructor.

## Required scope and evidence

The extension reuses the complete `hardsurface.subdivision.diagnose` work unit.
It requires the source-bound modifier profile, real native/registry validation,
explicit geometry output and rendering disabled. The existing raw L0 baseline
remains part of diagnosis; detailed new field output is limited to L2/L3 in the
planned first probe. Omission preserves legacy behavior.

The optional `params.surface_export` object has the following required fields:

- `format`: `HS_NATIVE_SURFACE_FIELDS_V1`
- `levels`: exactly `[2,3]`; diagnosis itself uses `[0,2,3]`
- `expected_l2`: the pinned `positions_topology_sha256` and
  `native_normals_sharp_smooth_sha256` from the completed old observation
- `observation.report`: a current absolute `file`, exact `sha256` and `bytes`
- `observation.views`: one or two distinct view names, each with a current
  pinned PNG `image` descriptor and explicit `projection_inputs`

Projection inputs comprise pixel aspect X/Y, resolution percentage 100 and
camera shift X/Y. The old report did not record all of these fields. They remain
declared conditions, not retroactive native readings. The recorded old scale,
clipping range, sensor fit and camera matrix are preserved; native
`calc_matrix_camera` supplies new intrinsics under the stated conditions.
Relocated, byte-identical reports and images are supported without treating
their old temporary paths as current input locations.

The option also requires an L2/L2 source modifier, two CPU threads,
`export_geometry=true`, `render.enabled=false` and the existing complete
source-bound profile/nominal reference. Generic diagnostic profiles are refused.

The intended field contract contains:

- Exact native `corner_normals`, `normals_domain`, their coordinate space and
  direction units, with no normalization or quantization
- Loop vertex/edge correspondence; polygon loop ranges and smooth flags;
  actual Blender loop triangles including loop indices
- Native sharp flags and supported crease attributes, retaining domain and
  attribute presence rather than inventing missing values
- Actual evaluated FACE parent-slot and surface-label arrays, authenticated
  source maps and named control loops
- Full source/evaluation modifier settings, transforms and file identity,
  plus the exact position/topology and native-normal hashes used by observation
- Protected prior observation references and Blender-native camera projection
  calibration; previously recorded camera transforms remain explicitly
  distinguished from newly calculated intrinsics

The exported vectors and domain arrays must reproduce the existing rendered
L2 geometry/normal identity before consumers use them to explain that image.
L3 is a separate stability sample; it was not the surface rendered in an L2
image. Ancestor face labels do not represent complete subdivision influence
weights. Missing native APIs, unsupported semantic data, drift or mismatched
hashes fail closed. No indexed-order, nearest-point or Z-threshold substitute
is an authenticated ancestry map.

## Output and completion bounds

In addition to the existing L0/L2/L3 geometry JSONs, the job writes
`subdivision-source-surface-maps.json`,
`subdivision-observation-calibration.json`,
`subdivision-l2-surface.json` and `subdivision-l3-surface.json`.
`surface_export.recompute_identity(data)` reconstructs both observation hashes
from the emitted arrays with lightweight views; it does not invent native data.

New surface files are limited to 64 MiB each and 160 MiB combined. Existing
geometry output retains its separate 48 MiB/file and 128 MiB total limits.
The complete diagnosis job retains its existing sampled 1 GiB worker-tree RSS,
512 MiB disk and at most 600-second bounds. These are monitored limits, not
hard OS isolation. The first native probe must measure its actual use.

The source report is bounded to 8 MiB and each image to 2 MiB. All external
evidence binds the exact bytes read. Streamed output refuses existing files;
partial output is retained if collection, identity or budget checks fail.
Only a completed successful whole-job report, matching descriptors and hashes,
and successful final source-preservation checks make an export usable. An L2
file retained after a later L3 failure is incomplete job evidence.

## Consumer limits

Pixel reprojection round-trips test numerical consistency of the chosen HOST
projection. Native camera fields and projection calibration provide separate
evidence for renderer correspondence. Orthographic calibration does not add
perspective support or a pixel-silhouette acceptance method.

An ideal mirror-direction or light-plane intersection is only a geometric
lighting proxy. It does not reproduce roughness, area-light integration,
multiple scattering, sampling, filtering or the display transform. Native
normal exports do not, by themselves, turn that proxy into a reference render.

Report observed residuals and unresolved evidence separately. Do not invent a
new curvature tolerance, interpret all reflection turns as defects, change
reference radii to fit an image, or promote numerical output to visual/user
acceptance. The first probe is a bounded evidence acquisition job, not another
modeling or parameter-search batch.
