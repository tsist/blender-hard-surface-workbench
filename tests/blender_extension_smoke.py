"""Run in separate build and reopen processes after installing the extension ZIP."""
import importlib
import json
from pathlib import Path
import sys

import addon_utils
import bpy


MODE, OUTPUT = sys.argv[sys.argv.index("--") + 1:]
OUTPUT = Path(OUTPUT).resolve()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MODULE = "bl_ext.user_default.hard_surface_workbench"
assert addon_utils.check(MODULE) == (True, True)
workbench = importlib.import_module(MODULE + ".hardsurface")
bridge = importlib.import_module(workbench.__name__ + ".ops.quad_bridge")
scene = bpy.context.scene
profile = Path(bpy.utils.user_resource("EXTENSIONS")).resolve()
assert Path(workbench.__file__).resolve().is_relative_to(profile)


def inspect_bodies():
    results = {}
    for obj in scene.objects:
        if "hs_quad_constructor" not in obj:
            continue
        cache = {}
        results[obj.name] = {}
        for state in ("control", "evaluated"):
            report = bridge.inspect_object(obj, state, cache)
            assert report["passed"] and report["self_intersections"]["status"] == "pass", (obj.name, state)
            results[obj.name][state] = {key: report[key] for key in ("geometry_sha256", "topology_sha256", "counts")}
    return results


expected = OUTPUT / "geometry.json"
blend = OUTPUT / "parameterized-fixtures.blend"
if MODE == "build":
    OUTPUT.mkdir(exist_ok=False)
    text = bpy.data.texts.new("Workbench fixture request")
    text.write((FIXTURES / "box.json").read_text(encoding="utf-8"))
    scene.hsw_request_text = text.name
    before = tuple(scene.objects)
    assert bpy.ops.hsw.validate_request() == {"FINISHED"}
    assert tuple(scene.objects) == before
    parameters = json.loads((FIXTURES / "public_quad_cases.json").read_text(encoding="utf-8"))
    constructors = {}
    for row in parameters["cases"]:
        constructors.setdefault(row["parameters"]["op"], row)
    assert set(constructors) == {"quad.panel", "quad.shell", "quad.fastener"}
    for row in constructors.values():
        obj, _ = bridge.create(row["parameters"], row["id"], row["id"])
        obj["hs_object_id"] = row["id"]
        obj["hs_feature_id"] = row["id"]
    bpy.context.view_layer.update()
    results = inspect_bodies()
    assert len(results) == 3
    expected.write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {"FINISHED"}
elif MODE == "reopen":
    assert Path(bpy.data.filepath).resolve() == blend
    assert bpy.ops.hsw.validate_request() == {"FINISHED"}
    assert inspect_bodies() == json.loads(expected.read_text(encoding="utf-8"))
else:
    raise ValueError(MODE)

print(json.dumps({"mode": MODE, "status": "pass", "build": bpy.app.build_hash.decode(),
                  "blender": bpy.app.version_string, "workbench": workbench.__version__}))
