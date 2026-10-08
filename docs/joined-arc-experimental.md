# Experimental joined-arc observation candidate

Implementation: structural-native-dev.7-experimental. This is a new independent candidate requiring exact-source native approval. It is not a qualified production default.

## Established problem

The dev.6 parent passed 54 identity/fault producer-reader processes, three source-profile/legacy-cause checks, and 35 complete native workunit processes. Its first PQ build then stopped at the unchanged 0.05 mm constructor gate: finite surface error 0.0768335202 mm and extent error about 0.05459 mm. No PQ blend or preview was saved. The total actual process count was 93, not the 137 upper budget.

A separate regular cubic B-spline HOST model reproduced that native surface error within 0.0000032 mm. Periodic-circle compensation was being reused at joined straight/arc and flat/fillet/wall boundaries. Correct mesh connectivity and quad counts could not repair the local shape discrepancy.

## Bounded implementation

An explicit subdivision_cage.outer_join_policy=joined_arc_v1 within fixed bore support replaces the outer profile control placement. Omission retains all five legacy goldens exactly. It changes 256 existing controls, keeps 992 vertices and 992 faces, preserves semantic identities, topology, creases, both planar transition rings and protected bore geometry. It does not move nominal design dimensions or relax any quality threshold.

The profile uses a symmetric interior pair constrained to place its central regular-span midpoint on the nominal circle. Endpoint and guard controls remain on their original bounding planes. Outline tangent placement is derived from the actual adjacent guard spacing and a first-arc-span midpoint circle equation. The equations and finite domains are implemented explicitly; invalid values and in-place policy migration fail closed.

HOST predictions for the tested project baseline/independent variants meet the existing 0.05 mm gate. These predictions are not native verification, all-points accuracy, or an expanded continuous parameter-domain guarantee. A narrow neutral input still fails the existing aspect gate and remains a negative case. Source role assignments and the regular/support aspect limits are unchanged.

## Known curvature risk: not passed

The cross-section is monotone/convex in the HOST model. The complete outline is not. A dense signed-curvature probe on the regular R12 wall-guard XY curve still finds -0.0189531 per mm at the two Y-positive short-guard joins, compared with -0.0405728 per mm in the source. These are HOST evaluated spline curves, not merely control-polygon turns and not measured native curvature. The residual reversal may produce a highlight ripple. Numeric distance passing cannot accept it.

A tested E=1 normal-endpoint alternative removes that reversal but fails the unchanged distance gate; it is not substituted. Earlier unconstrained coefficients that introduced reverse cross-section curvature and plane overshoot are also not production recommendations.

The next native observation boundary is baseline-only: actual construction, independent reopen, source-bound L0-L3 measurements and focused paired control wire/no-overlay/0,45,90,135-degree strip views at the two joins. All cameras remain orthographic. Any visible ripple or missing diagnostic coverage blocks further acceptance; no E1-E4 execution or commercial/family qualification follows automatically.

## Measurement and shading integrity

Duplicate nominal/tolerance metadata is checked against freshly attested authored parameters. This preserves the inherited technical 0.05 gate; it does not claim approved-reference numeric criteria are all implemented.

The opt-in geometry_normals_v1 observation policy is intended to reject transferred/custom source normals and verify actual normal/smooth/sharp data across source and frozen display proxies, with fixed texture-free diagnostic material. Its native behavior is still untested in this candidate.

A separate actual-section 72-ray HOST sampler is present as development preparation only. It is not integrated into production acceptance or the next observation scope. The approved flat mask, independent complete dimensional/center criteria, perspective lens controls and pixel silhouette comparator remain unfinished. No image or source-lineage claim is inferred from an opaque token.
