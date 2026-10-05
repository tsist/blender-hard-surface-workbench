# Hard Surface Workbench 0.2.0

Experimental parameter-driven Blender hard-surface work units, with a standalone source CLI, JSON Schemas, and a minimal Blender sidebar.

**The meshes can still have poor edge flow and excessive face counts.** All-quad output is a structural property, not proof of good topology, good shading, or production readiness. Independent visual review is necessary. This is not a general CAD, subdivision, deformation, or manufacturing system.

## What it does

- `quad.panel`: bounded rounded plates, holes, counterbores, slots and integral lips
- `quad.shell`: rounded enclosures in the supported four-mirrored-corner-seat layout, with declared posts and openings
- `quad.fastener`: unthreaded shafts and heads, with an optional declared hex socket
- Explicit work units, candidate-only saves, source hashes, checkpoints, bounded jobs and recovery
- Actual control/evaluated topology, bounded triangle-intersection checks and real-edge wire observations
- Declarative `hardsurface validate` schema 2.0: caller-supplied part identities, dimensions, probes and bounded contact regions

Scene validation supports real mesh bodies and fails closed on unsupported rendered instances, particle geometry, and curves; it does not silently omit them.

There are no embedded project profiles or preset design dimensions. Public numerical fixtures are independent illustrative test inputs. Validation never loads an arbitrary script, and a successful validation command means a report was produced; check its geometry outcome and individual results.

## Runtime and start

Use Linux, host Python 3.12+ and a separately installed Blender 5.2.x. Actual Blender qualification and its precise build are described in [validation](docs/validation.md); accepting a version is not evidence that every build has been tested. Saved-file protection requires a supported local Linux filesystem and rejects unsupported filesystems.

From this directory:

```sh
export BLENDER_PATH=/absolute/path/to/your/blender
./hardsurface-cli hardsurface describe --section quad.panel
./hardsurface-cli hardsurface describe --section validate
./hardsurface-cli hardsurface plan --request fixtures/box.json
```

Global options precede the command:

```sh
./hardsurface-cli --blender /absolute/path/to/your/blender --jobs-dir /tmp/your-new-jobs hardsurface run --request request.json
```

`BLENDER_PATH` selects the default Blender executable; otherwise the host searches `PATH`. `--blender` overrides it. `BLENDERCTL_PYTHON` selects the host Python interpreter. The launcher does not download or install software. Production work units require a source-bound reference approval input; inspect the planner/schema before running.

## Test and build

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
python3 -B scripts/build_distribution.py --output /tmp/your-new-build
```

The manifest-based builder creates a source ZIP, a Blender extension ZIP, checksums and a member manifest. It refuses existing output directories. Unpack source archives into a new directory. The extension archive is the sidebar/domain package. Use the source package for the standalone backend; it has no full blenderctl copy or external Python dependency.

The package is source-distributed for review and development. **Extension installation, enablement, restart behavior and interactive end-to-end GUI use are not qualified by host tests or static packaging.** The sidebar validates request text; it is not a full interactive modeling application.

Structured quad constructors do not need `slvs`. Legacy constraint solving requires a separately reviewed deployment; the native binary is not redistributed and the optional native tests skip when no deployment is configured. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Optional blenderctl integration

This repository owns the generic modeling and validation logic. The separate [blenderctl](https://github.com/tsist/blenderctl) integration branch `hard-surface-workbench-linux-0.2.0` only dispatches explicit work units to this checkout.

Use reviewed source checkouts in separate directories, then explicitly select this checkout:

```sh
export BLENDERCTL_HARDSURFACE_ROOT=/absolute/path/to/blender-hard-surface-workbench
python3 /absolute/path/to/blenderctl/tools/blenderctl/cli.py hardsurface describe --section validate
```

The environment variable trusts the source code at that local path for execution. Review it before use. Neither repository automatically clones, downloads, installs, enables an add-on, or changes global configuration. Record the commit SHA of each checkout for reproducibility. The standalone source launcher also works without blenderctl.

## 中文说明

这是参数驱动的实验性插件，所有设计尺寸与验证规则由调用者明确提供，不包含项目专属参数或模型。当前仍可能存在布线不佳、面数过多的问题；全四边面不等于高质量拓扑或生产就绪。圆角壳体目前限定为四镜像角座结构，接触验证限定为轴对齐平面上的显式矩形/圆盘区域及合格实际孔界，不支持任意机械结构或任意曲面接触。

## License

GPL-3.0-or-later. No warranty. This modified public-source distribution was prepared on 2026-10-05; see [LICENSE](LICENSE) and [release notes](docs/public-release.md).
