# Validation and qualification boundaries

## Supported construction domain

The public constructors consume caller-provided dimensions. Panels support the documented rounded outline and local feature layout. Shells currently require four mirrored integral rectangular corner seats; this is a supported construction domain, not a promise of arbitrary enclosure geometry. Fasteners are unthreaded, with a declared head/shaft and optional hex socket.

The production save/checkpoint gate inspects actual control and evaluated polygons. The general `topology` command can also request bounded self-intersection auditing. A technical pass must be read together with the supported-domain and resource limits.

## Declarative geometry validation

Whole-scene coverage supports real mesh bodies. Rendered instances, particle geometry, curves and other unsupported visible geometry are rejected rather than silently excluded from acceptance. It is not a general Blender scene/render equivalence checker.

Schema 2.0 binds expected values and tolerances to the same request as the saved-source descriptor. It supports explicit part bounds, axis-aligned ray hits, differences between declared ray witnesses, four-probe axial checks, and bounded pair checks.

Contact is denied unless explicitly covered by a declared qualifying region. The supported regions are XYZ-axis-aligned planes containing a rectangle or disk, with optional exclusions verified against actual convex circular-bore boundaries. Arbitrary curved contact, flexible/deforming contact and a general manufacturing-solid proof are outside the supported domain.

Numerical classification thresholds remain distinct from caller-supplied design tolerances. Bounded ray/containment samples and finite intersection witnesses have documented coverage limits. A successful operation means its report was written; `domain_outcome` and individual checks determine its geometric result. Visual and user acceptance are separate.

The declarative validator does not replace the production control/evaluated quad gate or certify single-body self-intersection freedom. The `run` save/checkpoint gate and explicitly requested `topology` self-intersection audit own those checks.

## Tests and honest claims

The default unit suite checks host contracts and independently generated numerical fixtures without installing Blender or native solver packages. Native solver tests require a separately prepared configuration and otherwise skip explicitly.

The optional generic CLI qualification script uses an existing Blender runtime and creates new numerical fixture outputs. Such runs and actual image review must be recorded separately from host CI. Static ZIP integrity, AST parsing and a green host-only workflow do not establish extension registration, GUI operation or visual quality.

Precise final test counts, tested Blender build and inspection results should be taken from the release/PR's verified results, not inferred from historical runs or source version numbers.

## Release qualification recorded on 2026-10-05

The pre-split qualification host suite ran 487 tests: 485 passed and the two optional native solver tests skipped because no solver deployment was configured. Python 3.12 and 3.13 were checked separately. The standalone repository removes three CLI-dispatch tests (now owned by blenderctl) and adds six standalone-distribution checks: 490 tests, 488 passed and two optional solver skips.

The actual Blender baseline is 5.2.2 LTS, build `d13f752e3b9c`. Native checks cover 40 core fixture cases and 12 bad-mesh, scene-coverage and thin-wall rejection cases. The final declarative checker passes 42 assertions over independent synthetic CLI scenarios, including allowed contact, forbidden penetration, containment, axis-plane variants and actual bore boundaries.

After the final projected-interval rounding fix, 14 already-generated numerical fixture sets were re-opened and revalidated without changing their geometry bytes; a seated-bore positive was newly built, independently reopened and observed. Constructor, core, self-audit and observation modules were unchanged from the build baseline. This is explicitly reused-geometry validation, not a claim that every fixture was reconstructed by the final checker revision.

Default-wire views for all eight independent constructor cases and two new seated-bore views were inspected. An additional lower-side view confirmed the reverse fastener socket without modifying its source. These checks do not establish artistic acceptance of every possible result. Source/evaluated topology and numerical qualification are distinct from visual quality and extension GUI qualification.

## Two-repository integration check

The standalone split also completes one newly generated independent fastener fixture through the separate blenderctl adapter: asynchronous submission, detached supervisor, owned job wait/result, actual control/evaluated quad gate, independent reopen, positive declarative validation and intentional wrong-bounds rejection. The saved candidate hash remains unchanged after validation. This checks repository boundaries and source protection; it does not repeat the full constructor matrix or imply new GUI/visual qualification.
