# Experimental public source: 0.2.0

Prepared 2026-10-05. This release exposes a bounded parameter-driven modeling implementation and its numerical tests. It does not certify arbitrary mechanical designs.

## API changes

`hardsurface validate` uses schema 2.0. Callers supply stable feature/part identities, expected bounds, axis-aligned ray probes, differences, axial checks, and explicitly allowed contact regions in the request. These specifications participate in request identity. There is no fixed profile selector or implicit part name.

Fastener dimensions and placement are explicit inputs. Optional sockets and edge bevels are disabled unless requested. Origin conventions and technical sampling/resource defaults are not project design presets.

Projected-interval contact comparisons account for binary64 representable rounding. This numerical handling does not modify nominal geometry, caller design tolerances, or the separately documented classification epsilon.

## Distribution

This is a standalone plugin source repository. It contains the domain implementation, schemas, numerical fixtures, standalone source launcher and tests. The separate blenderctl branch contains only an explicit dispatch adapter, its tests and documentation; it does not embed this repository. Only the small attributed Linux read-guard module is retained from blenderctl, with its exception import adapted to this package.

`PUBLIC_FILES.txt` is an exact source allowlist. The packaging script validates UTF-8/Python syntax, source/manifest versions, required notices, safe relative paths, archive members and ZIP bytes. Two independent builds can be compared with `scripts/verify_reproducible.py`.

Only source, public schemas, newly documented examples, reusable numerical tests and release tooling are published. Model files, images, user approval records, conversations, local execution output, historical reviews/patches, native wheels, runtime binaries, bytecode and user configuration are excluded.

## Quality limits

Face counts and edge flow remain areas for improvement. Passing topology and finite numerical checks does not prove low-density meshes, good subdivision, manufacturing validity, or visual quality. Some parameter layouts are intentionally rejected when the constructor cannot meet its bounds. There is no silent tolerance relaxation or fallback to arbitrary remeshing.

The Blender sidebar is a thin adapter. Installation, enablement and interactive use remain separate from host-only CI and static distribution checks.

## Repository split verification

The repository split changes launch paths, implementation fingerprint inputs and the location of the Linux read guard. Constructor, geometry, scene validation, Blender worker and observation algorithms are unchanged from the documented baseline. Host/import/package and selected actual Blender integration checks are rerun for the split; historic geometry qualification is not represented as a new full reconstruction run.
