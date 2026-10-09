# Generic HOST research evidence (dev.22-r1)

This is a new independently implemented, standard-library-only evidence adapter.
It does **not** port the exact rational Catmull–Clark/junction solver, run an LP,
launch Blender, change a saved model, or install an add-on. Protocol stays 0.2.0.

## Complete unit

`./hardsurface-cli hardsurface research-evidence --request request.json`

The CLI reads a bounded strict JSON file and returns the complete report on
stdout using the existing `ok`/`data` envelope. No job directory, source write,
child solver, Blender executable, or installed wrapper is required. `ok: true`
means a report was returned, never qualification. A failed or timed-out report
exits 5; malformed top-level requests exit 2; an incomplete report exits 0.

`tests/test_research_evidence.py::fixture()` is a wholly synthetic complete input.
The schema is `research-evidence/1.0`, discoverable through
`hardsurface describe --section research-evidence` and mirrored in
`schemas/research-evidence.schema.json`. Unknown fields are rejected. Runtime
validation also enforces cross-field dimensions, finiteness and SHA identities.

Required fields:

- `source`: vertices as finite length-three coordinate lists, ordered quad
  `faces` with integer vertex indices, and unique string `vertex_ids`. All
  vertices must participate. Coordinates, thresholds and model coefficients
  must use one consistent caller-declared-in-practice unit system; the adapter
  does not convert units or infer physical dimensions.
- `source_sha256`: `canonical_sha(source)` from the module, binding all source
  coordinates, face order and identity labels. This binds supplied bytes of a
  canonical representation; it does not establish origin from a native file.
- `model`: `variable_count`, flattened coordinate-row `basis` (three rows per
  source vertex), `inequalities`, `equalities`, and finite `[lower, upper]`
  `bounds` per variable. Constraint rows contain only `a` and `b`, for
  `a dot q <= b` or `a dot q == b`. No implicit inequalities or omitted bounds.
- `external_lp`: matching `source_sha256`, `model_sha256=canonical_sha(model)`,
  `status`, and optional finite `q`. Accepted status labels are `optimal`,
  `feasible`, `infeasible`, `not_run`, `failed`, and `timeout`.
- `protected_vertex_ids`: explicit unique subset of the source identities.
  Empty yields protected_identity `not_run`, not whole-source protection.
- `coverage`: explicit `origin`, positive scalar `cell_size`, and nonempty
  unique integer triplets `required_bins`. No inferred denominator.
- `quality`: explicit `max_warp_degrees`, `min_corner_degrees`, and
  `max_edge_ratio`; the caller owns these thresholds.
- `residual_tolerance`: finite absolute constraint tolerance in [0, 0.001].
  This does not relax exact protected coordinate comparisons.
- `budgets`: positive `wall_seconds` (at most 120) and integer `operations`
  (at most 5,000,000). These are enforced, recorded limits, not retrospective
  labels. Input caps are 8 MiB, 10,000 vertices, 20,000 quads, 256 variables,
  and 500,000 combined basis/constraint coefficients.

## Source, model and candidate binding

A candidate can only be reconstructed as `source + basis*q`. There is no
independent candidate geometry input that could be substituted after LP checks.
A valid external vector is checked against every supplied inequality, equality
and bound. The report includes actual maximum residuals. Reported `optimal`
is not independently established optimality. Source, complete request, module
implementation, and reconstructed candidate receive SHA-256 identities.
Inputs are not modified. Candidate topology and labels are retained exactly;
protected coordinates must remain numerically equal in binary64, without using
the residual tolerance. Positive and negative zero compare equal; this is not
a raw coordinate-byte comparison.
This is declared-set identity preservation, not a proof that the declared set
covers every support star, that the basis expresses intended constraints, or
that an evaluated surface preserves native subdivision correspondence.

## Evidence states and limits

Without `q`, external verification and candidate geometry are `not_run`.
A solver-reported `infeasible` remains a quoted external status; no independent
infeasibility certificate, IIS, global impossibility, or failed geometry audit
is manufactured. With a contradictory status and vector, the input fails.
Invalid residuals fail and block geometry. This adapter neither invokes nor
retries a solver.

With a verified vector, the implemented checks are:

- Closed edge incidence, consistent local orientation, duplicate faces and
  connected cycle links at each vertex, plus component and Euler counts. This does not certify global outward
  orientation, a single connected component, genus, or self-intersection.
- Nonzero edges/areas, positive convex corner alignment, both quad diagonals'
  triangle compatibility and warp, RMS over both diagonals, corner angles,
  and edge ratios, with worst face/corner/diagonal indices for localization.
  Invalid diagonals are counted and both sides are examined even if one fails.
  Limits are explicit finite numerical screens.
- Candidate **vertex-sample** grid coverage using floor((point-origin)/cell).
  Only required bins count toward coverage; additional bins cannot compensate
  for missing originals. This is neither continuous coverage nor the exact
  curvature/CC coverage procedure from a research case. The source baseline
  and caller's required-bin selection remain their responsibility.
- Exact declared protected vertex positions and retained identities.

Self-intersection is explicitly `not_run`, including shared-vertex face pairs;
none are silently exempted or passed. Exact CC/junction solver is
`not_implemented`; native and production qualification are `not_run`.
Consequently, even when all implemented gates pass, the overall status is
`incomplete`. There is no global `pass` or `evidence_complete` status.
A failed implemented check gives `failed`; resource exhaustion gives
`timed_out`. Earlier stage evidence may remain visible after a later timeout,
but never overrides the terminal nonpassing status.

Budgets cover the evaluate stage using frequent cooperative checks and hard
bounded inputs, not an OS watchdog. The CLI regular-file read and strict JSON
parse precede the evaluate timer and have byte/structure limits. Canonical
serialization and bounded allocation can occur between checks; no end-to-end
hard wall, CPU or peak-RSS guarantee is claimed. Operation counts
are instrumented work units, not processor instructions. Wall receipts use a
monotonic clock. The implementation is HOST-only and its tests use new synthetic
cubes, invalid quads, identity tampering and algebraic constraints, never actual
case geometry, private paths, stable IDs, or external trial payloads.
