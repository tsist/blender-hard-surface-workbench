# Tangent-plane joined-arc v2

This optional constructor policy is `subdivision_cage.outer_join_policy=joined_arc_v2`. It requires explicit fixed bore support. Existing v1 and omitted-policy outputs remain independently supported; switching a persisted object's policy in place is rejected.

Let b denote nominal outer roundover radius. V2 sets the outline endpoint factor E=1 and the quarter-profile coordinate c=b. The other profile coordinate is a=b((48/sqrt(2)-1)/23-1). Existing angular sampling and the circle midpoint equation determine the first-arc tangent advance from the real neighbouring guard spacing. No new vertex, face, authored ID, crease, region role or planar transition ring is introduced.

The rationale is geometric: the previous endpoint compensation could step away from the straight tangent plane and generate a shallow reverse turn before the circular span. Real parent-version strip images showed bilateral waviness. V2 constrains the corresponding controls to their tangent planes; it does not hide the defect with normal transfer, materials or a wider distance threshold.

Independent HOST evidence has convex ordered control polygons at all eight joins across twelve profile rings, monotone convex quarter profiles, no height or wall-bound overshoot, and no sampled curvature reversal beyond numerical roundoff. The tested baseline's refined outward error reaches about 0.047476 mm, only 0.002524 mm below the inherited 0.05 mm gate. The tested profile curvature peak is about 1.513 times nominal circular curvature. These are finite/proxy results, not certified all-points or native visual acceptance.

Source-mesh quality remains an independent gate. The tested project's support-band aspect is about 65.77 and its smaller-roundover edit about 87.70, below the unchanged limit 100. A separate small neutral input fails at about 105.52 and remains unsupported. No roles are relabelled and no quality limits are raised to admit it.

Native evaluation, independent save/reopen, actual multi-direction highlights, and coverage of the upper fillet remain required for this exact version. The completed parent observations are retained failures, not reclassified successes. Complete bore sections, the approved flat-region mask, perspective views and pixel silhouette acceptance remain outside this repair's implemented qualification claims.
