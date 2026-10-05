# Third-party notices and attribution

The Linux read-guard module `hardsurface/linux_file_guard.py` derives from [tsist/blenderctl](https://github.com/tsist/blenderctl), CLI 0.55.0, baseline commit `722d94afcff6964bee11639e72c2454c6dba112e`. It is licensed GPL-3.0-or-later, copyright © 2026 blenderctl contributors. Existing SPDX notices are preserved. Its original source is retained except that the `Failure` import is adapted to this package's compatible `RuntimeFailure` exception. The standalone plugin does not bundle the full blenderctl CLI.

The hard-surface implementation, schemas, independent numerical fixtures and release tooling are distributed under GPL-3.0-or-later. The full license is in LICENSE. Modified public-source distribution prepared 2026-10-05: generic declarative validation, explicit constructor inputs, runtime-path portability, qualification cases and public packaging/documentation. These changes are not a representation that every supported input is production-qualified.

Blender, Python and their supplied libraries are separate runtimes, not bundled. Blender may provide additional libraries; consult its installed distribution and licenses. No implementation of those libraries is vendored here.

Legacy `sketch.solve` supports a separately configured `slvs` 3.2 dependency. The examined wheel's COPYING contained GPLv3 while package metadata classified MIT. This discrepancy is not treated as clearance to redistribute that binary. No solver wheel, extracted runtime or automatic installer is included. Review the dependency's exact source and terms before any deployment.

No user projects, textures, reference images, review reports, approval records or user configuration are included. Source distribution does not establish extension installation, registration or GUI qualification. No warranty.
