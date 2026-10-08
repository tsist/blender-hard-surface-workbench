# First-batch qualification: contracts before planar production

Status: 2026-10-06 `structural-native-dev.6` candidate documentation. Domain/structure schema identity is independently versioned as 1.0. The checks below are planned acceptance requirements, not a test-result report. No Blender run, sample geometry, visual pass, or family qualification is established by this file.

## Current evidence boundary

- Public 0.2.0 and dev.3 are different source identities despite the shared manifest version. Bind every result to the exact new source/package SHA, schema versions, constructor version and Blender build
- Recovered dev.3 completed 566 total host tests (564 passed, 2 optional skips); dev.4 completed 676 total (674 passed, 2 skips). Five legacy native numerical fixtures per tree were reproduced with matching pairwise signatures. These are legacy-baseline evidence, not qualification of the new candidate or artistic success
- Blender 5.2.2 LTS build d13f752e3b9c has been restored and the old dev.3/dev.4 baseline reproduced. The dev.5 parent passed 27 native identity/save-reader cases and the first complete CLI build, then stopped at L3 diagnostic acceptance. The dev.6 source-profile/semantic-measurement repair remains natively untested and requires new exact-source execution approval. Do not fall back to system 4.3.2
- M1 completion, M2 qualification and first-batch completion retain their native, visual and independent-reopen dependencies; passing host tests is insufficient
- Asset-specific G0 approval is held in a separate project receipt binding the exact user-visible reference. It is not embedded as a universal authorization in this package. Native candidate execution, test qualification and each user visual stage remain distinct approvals

## Identity and state model

Evidence records carry at least `check_id`, `status`, `required`, `applicability_basis`, source/model/reference/implementation SHA, runtime/build, complete evaluation settings, observation and measurement scope, worst value/view, output reference with SHA/bytes, and reason for fail/not_run/not_applicable. These are semantic requirements; use actual implemented schema fields rather than treating this document as an executable request.

Use pass/fail/not_run/not_applicable. A required fail or not_run blocks its declared qualification scope. Unsupported required checks are not_run, never not_applicable. User acceptance is never synthesized from an execution receipt. Operation execution, technical geometry, actual visual review, user approval and transferability remain distinct.

A qualification key includes asset family, application route, design domain, constructor version/source SHA, Blender version/build, full evaluation settings and delivery chain. Protocol number or a test count is not a qualification key.

## Planned test inventory

All FB-* IDs below are planned test cases. A log must explicitly establish which have actually run.

| ID | Layer / scenario | Required assertion |
|---|---|---|
| FB-C01 | Schema / separated design-topology-evaluation | Units, identity, reference state and design relations validate; missing approvals cannot be satisfied by metadata alone |
| FB-C02 | Region membership | Claimed faces and boundaries resolve against the actual mesh; missing, foreign or stale indices rejected |
| FB-C03 | Ordered loops | Real edge adjacency, closure, direction, uniqueness, owner and geometric anchors verified; metadata-only rings fail |
| FB-C04 | Boundary ports | Orientation, point correspondence, coordinate frame, permitted conversion and actual seam connectivity agree |
| FB-C05 | Legacy adapter boundary | Existing 24-point/48-point restrictions apply only to that adapter; generic entities do not equate arbitrary loop cardinality with qualification |
| FB-C06 | Input integrity | Zero/negative sizes, invalid relationships, non-finite values, unsupported family and incompatible schema/runtime reject before scene writes |
| FB-C07 | Approval binding | Proposal, missing approval, mismatched reference SHA or conflicted drawings deny production construction |
| FB-C08 | Dry-run | Reports complete target/write scope, non-target invariants, invalidations, destination and limits; source/scene/output unchanged |
| FB-C09 | Idempotency | Same request ID + same fingerprint resolves original job; changed fingerprint rejects; timeout does not duplicate jobs |
| FB-C10 | Stale selection / mapping | Stale managed selection fails; remapped entities carry explicit continuation/split/merge/delete; ambiguity fails closed |
| FB-C11 | Source and non-target protection | Exact protected input SHA unchanged; non-target object/dependency invariants checked before/after and after recovery |
| FB-C12 | Route gating | film_product and realtime keys are independent; unqualified geometry cannot enter a successful game qualification |
| FB-C13 | Result aggregation | Required missing/native/visual/holdout checks remain not_run; n/a requires contract basis; no score averaging clears a failure |
| FB-N01 | Actual source mesh | Source arity, triangles by approved exception, zero >4-gons, degeneracy, orientation, manifold/boundary and self-intersection checks include all hidden faces |
| FB-N02 | Evaluation | Full stack and L0/L1/L2/L3 geometry/settings retained; final-level required pass cannot erase other-level failures |
| FB-N03 | Shape | Nominal dimensions, hole diameter/position/circularity, outer silhouette, thickness, plane/roundover relation measured with explicit coverage |
| FB-N04 | Hole completeness | Entrance, wall, exit, bottom face and tightest transition inspected in same model/version; section copies supplement, not replace, intact evidence |
| FB-N05 | Rebuild repeatability | Same frozen inputs yield the same canonical geometry/connectivity/semantic signature; .blend byte equality is not required |
| FB-E01 | Diameter / position edit | Declared affected regions versus real changes checked; unaffected connection/coordinate invariants hold or the edit rejects |
| FB-E02 | Thickness / rim edit | Expected global/local effects explicit; independent dimensions and distant regions protected |
| FB-E03 | Boundary / invalid variants | Support clearance, near-edge hole and bevel/thickness conflict reject or satisfy the declared domain without silent clamping |
| FB-E04 | Topology-changing edit | Explicit semantic identity maps and dependent selection invalidation; prior measurement/visual cache invalidated |
| FB-V01 | Native-wire pairing | L0 native mesh edges plus same-camera no-overlay evaluated views; no drawn ideal wire, render tessellation or edit-cage ambiguity |
| FB-V02 | Multi-angle review | Front/top/side/rear/underside, full outline and hole/rim detail; no hidden/backface exemption |
| FB-V03 | Highlight sweep | Multiple approved light/stripe directions and camera conditions; preserve worst frame and inspect ripples, pinching and seams |
| FB-V04 | Fair route comparison | Same target, camera, resolution and required level; separate geometric changes from normal/shading changes |
| FB-D01 | Save and independent reopen | Reopened new candidate matches object/dependencies, stack, semantic maps, dimensions and representative native/evaluated/highlight views |
| FB-D02 | Failure/recovery | Interrupted/partial job stays failed or pending; valid checkpoint provenance restored without overwriting protected sources or later edits |
| FB-R01 | Resource accounting | Timings for open/construct/evaluate/check/save/reopen/render separately plus end-to-end; peak process RAM/method and system availability recorded |
| FB-H01 | Independent holdout | Select after implementation freeze, no tuning exposure; exact reference approval and full native/visual/edit/reopen evidence |
| FB-H02 | Near-boundary holdout | At least one separate holdout near the proposed domain boundary; failure restricts qualification, not silently adjusts tolerance |

Native mesh-health and self-intersection checks must state their actual implementation and limitations. If the current native backend lacks a required check, the gate stays not_run even when manifoldness, Euler statistics or image appearance pass.

## Native observation package

1. Save native L0 source-wire images with true source mesh edges, modifier visibility and selection/overlay state recorded. Include full front/back, side and local hole/rim views; retain geometry sidecars for mesh audits
2. Pair the same cameras with neutral, unselected, no-overlay evaluated images for L1/L2/L3. Explicitly disable wire, vertices, edit cage, outline, construction and measurement overlays in these images
3. Use separate geometric measurement/section outputs for dimensions. Mark each section plane and show the intact same-version model; do not modify the production master to make evidence
4. Review highlights across multiple directions under frozen neutral settings. Capture low glancing views of both main planes, hole entrance/exit, interior wall and outer corner transitions; preserve the worst frame and its camera/light settings
5. After independent reopen, regenerate representative evidence. Existing files copied into a folder are not proof that the reopened file reproduces them

Reference illustrations are design aids only, not generated-model or native-wire evidence. A generated reference must never depict fictional topology as if extracted from Blender. All generated images and review previews follow the methodology's <=2048-pixel longest-edge limit, verified locally before viewing/transmission; preview bytes <=2 MiB, with original/preview identity distinguished.

## Measurement classes

Keep dimensional deviation, explicit arc chord error, sampled SubD surface deviation and projected screen error separate. State sampling planes, angles, density, unsampled regions and worst value. Circle chord-error math does not bound Catmull-Clark error. Source compensation vertices need not lie on the nominal final skin; preserve both L0 and required final-level results with their roles. No manufacturing, analytic limit-surface or global Hausdorff claim follows from finite samples.

Numerical tolerances, final evaluation level and pixel error are proposed by the caller-approved reference contract, not constants silently adopted from historical private cases. Budgets require current small-sample measurements; don't infer peak memory from the Blender title bar or sum modifier timings as end-to-end time.

## Application-specific continuation

### film_product

The first deliverable is a managed editable master plus specified evaluated high model. Favor useful support-loop structure; validated crease/Bevel combinations require explicit scope, stack and matched visual evidence. The first coupon qualifies only its bounded planar single-hole subfamily and tested parameter/evaluation conditions, never the entire flat-plate/window/slot family.

### realtime: planning only in this batch

Until qualified geometry exists, all actual low-poly, UV, bake, LOD and exchange-readback results remain not_run. Prepare tests for:

- High/low object-feature correspondence, projection sources and declared nearest-camera silhouette error
- UV seam/padding/texel-density/overlap/mirror contracts
- Normals, smooth boundaries, tangent basis, normal-map channel convention and cage/ray settings
- One frozen triangulation signature used by both bake and export; connectivity/UV/normal/tangent changes invalidate existing bake results
- Projection misses, leaks, wrong-island hits, occlusion, seams and mirrored areas
- Per-LOD geometry/UV/normal/tangent signatures and stated rebake or reuse; each LOD visibly tested with its own applied normal map
- Save/export and target-reader reopen comparison of geometry, UV, normals, material slots and dependency availability, followed by actual shading inspection

These are engine-independent qualification tests. Unreal/Unity import, runtime performance, compression, collision and engine LOD transitions remain outside this batch.

## Gates A / B and stopping rules

Current development process A requires stage-by-stage user visual approval after required internal evidence. The first-batch end condition includes a runnable correctly identified candidate, bounded constructor/rejections, actual editable model and images, edit/reopen/holdout evidence, an explicit status for historical visual failure, and current user approval. Static code/schema/docs or host test counts cannot satisfy it.

Request process B only for an exact approved family/route/version/domain after representative work and at least two independent holdouts pass, including one near the valid-domain boundary; repeat builds, edits, invalid inputs and reopen must be reproducible and serious failures closed. Obtain separate user approval for that exact scope. Two holdouts are a project minimum, not proof over a continuous domain.

If a holdout is used to tune implementation, reclassify it as a development/regression case and obtain a fresh holdout. New family/route/domain, materially changed construction/evaluation/export, serious failure or user visual rejection returns the affected scope to A. Approval for a development plan never authorizes installation, overwriting, deletion, external sharing or publication.
