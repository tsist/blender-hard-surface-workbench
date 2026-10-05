#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent, opt-in public CLI qualification using synthetic dimensions.

Without --run this only checks construction plans and prints the native test
inventory. Native execution requires explicit BLENDER_PATH and
BLENDERCTL_PYTHON environment variables. It creates unique evidence/jobs,
retains failures, never deletes files, and passes no scripts through the CLI.

The dimension oracles below are analytic consequences of the public fixture
inputs, not values learned from generated meshes. A technical pass does not
claim artistic quality, production approval, or arbitrary-design certification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "public_quad_cases.json"
CPU_THREADS = 2
WALL_SECONDS = 270
PROCESS_SECONDS = 300
POSITION_TOLERANCE_MM = 0.002
sys.path.insert(0, str(ROOT))


def require(condition, message, **details):
    if not condition:
        raise AssertionError(message + (": " + json.dumps(details, sort_keys=True) if details else ""))


def sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def descriptor(path):
    path = Path(path).resolve()
    return {"file": str(path), "expected_sha256": sha256(path), "bytes": path.stat().st_size}


def verified_json(ref):
    path = Path(ref["file"])
    require(path.stat().st_size == ref["bytes"], "Evidence byte count changed", file=str(path))
    require(sha256(path) == ref.get("sha256", ref.get("expected_sha256")),
            "Evidence SHA changed", file=str(path))
    return load_json(path)


def feature(identifier, parameters):
    return {"id": identifier, "program": {"kind": "steps", "steps": [
        {"id": "body", **copy.deepcopy(parameters)}]}}


def build_request(name, features):
    """A whole registered work unit, with all default wire settings intact."""
    checks = ["design_dimensions", "closed_mesh", "normals", "quad_topology",
              "source_preserved", "reopen", "preservation"]
    return {"schema_version": "1.0", "command": "hardsurface.run", "params": {
        "manifest_version": "1.0", "request_id": name, "purpose": "contract_fixture",
        "source": {"kind": "new_scene", "project_id": "public_cli_qualification",
                   "length_unit": "mm", "up_axis": "Z"},
        "context": {"scene": "SyntheticQualification", "view_layer": "ViewLayer",
                    "frame": 1, "evaluation": "RENDER"},
        "resources": [],
        "design": {"mode": "create_if_absent", "expected_state": "absent", "state": {
            "revision": 1, "dimensions": [], "sketches": [], "features": features}},
        "protection": {"source_write": "forbidden", "shared_data": "reject_shared",
                       "manual_edits": "preserve_supported_nonconflicting", "on_conflict": "pause"},
        "work_units": [{"id": "build_all", "feature_ids": [row["id"] for row in features],
                        "checks": checks, "failure_policy": "stop_job"}],
        "quality": {"profile": "synthetic_public_parts", "required": checks,
                    "visual": "not_applicable_fixture_only", "user_feedback": "not_applicable_fixture_only",
                    "length_tolerance": POSITION_TOLERANCE_MM},
        "budgets": {"max_total_steps": 16, "max_geometry_vertices": 200000,
                    "max_geometry_loops": 1000000, "wall_seconds": WALL_SECONDS,
                    "cpu_threads": CPU_THREADS, "max_artifact_bytes": 512 * 1024 * 1024},
        "output": {"publication": "candidate_only", "report": "compact"}}}


def bounds(parameters):
    op = parameters["op"]
    if op == "primitive.box":
        center, size = parameters["center"], parameters["size"]
        return [center[i] - size[i] / 2 for i in range(3)] + [center[i] + size[i] / 2 for i in range(3)]
    cx, cy = parameters["center"]
    if op == "quad.fastener":
        z = parameters["shoulder_z"]
        direction = parameters["direction"]
        ends = [z - direction * parameters["shaft_length"], z + direction * parameters["head_height"]]
        radius = parameters["head_radius"]
        return [cx - radius, cy - radius, min(ends), cx + radius, cy + radius, max(ends)]
    width, height = parameters["size"][:2]
    z_min = parameters["z_min"]
    if op == "quad.panel":
        z_min = min([z_min] + [row["z_min"] for row in parameters.get("lips", [])])
        z_max = parameters["z_max"]
    else:
        z_max = max([z_min + parameters["size"][2]] + [row["z_top"] for row in parameters["corner_seats"]])
    return [cx - width / 2, cy - height / 2, z_min, cx + width / 2, cy + height / 2, z_max]


def validation_request(name, source, features):
    part_rows = [{"id": row["id"], "feature_id": row["id"], "bounds_mm": {
        "expected": bounds(row["program"]["steps"][0]), "tolerance_mm": POSITION_TOLERANCE_MM}}
        for row in features]
    all_bounds = [row["bounds_mm"]["expected"] for row in part_rows]
    assembly = [min(row[i] for row in all_bounds) for i in range(3)] + [
        max(row[i + 3] for row in all_bounds) for i in range(3)]
    return {"schema_version": "2.0", "command": "hardsurface.validate", "params": {
        "request_id": name, "source": source, "parts": part_rows,
        "assembly_bounds_mm": {"expected": assembly, "tolerance_mm": POSITION_TOLERANCE_MM},
        "probes": [], "difference_checks": [], "axis_checks": [], "contacts": [],
        "require_all_visible": True, "include_pairs": True,
        "cpu_threads": CPU_THREADS, "wall_seconds": WALL_SECONDS}}


def add_probe(spec, identifier, part, axis, fixed, hits, tolerance=POSITION_TOLERANCE_MM):
    row = {"id": identifier, "part": part, "axis": axis, "fixed_mm": fixed,
           "expected_hits_mm": sorted(hits), "tolerance_mm": tolerance}
    spec["probes"].append(row)
    return row


def add_difference(spec, identifier, probe, first, last, tolerance=POSITION_TOLERANCE_MM):
    spec["difference_checks"].append({"id": identifier,
        "a": {"probe": probe["id"], "index": last},
        "b": {"probe": probe["id"], "index": first},
        "expected_mm": probe["expected_hits_mm"][last] - probe["expected_hits_mm"][first],
        "tolerance_mm": tolerance})


def rounded_half_span(size, radius, transverse_offset):
    """Analytic line intersection through a rounded rectangular boundary."""
    along, transverse = size
    offset = abs(transverse_offset)
    if offset >= transverse / 2:
        return None
    curve = max(0.0, offset - (transverse / 2 - radius))
    return along / 2 - radius + math.sqrt(max(0.0, radius * radius - curve * curve))


def panel_hits(parameters, axis, fixed_coordinate, z):
    along = 0 if axis == "X" else 1
    cross = 1 - along
    center = parameters["center"]
    half = rounded_half_span([parameters["size"][along], parameters["size"][cross]],
                             parameters["corner_radius"], fixed_coordinate - center[cross])
    require(half is not None, "Panel oracle line must intersect the outline")
    hits = [center[along] - half, center[along] + half]
    for hole in parameters["holes"]:
        distance = fixed_coordinate - hole["center"][cross]
        if hole["kind"] == "circle":
            radius = hole["radius"]
            recess = hole.get("counterbore")
            if recess and ((recess["side"] == "top" and z > parameters["z_max"] - recess["depth"])
                           or (recess["side"] == "bottom" and z < parameters["z_min"] + recess["depth"])):
                radius = recess["radius"]
            if abs(distance) >= radius:
                continue
            hole_half = math.sqrt(radius * radius - distance * distance)
        else:
            hole_half = rounded_half_span([hole["size"][along], hole["size"][cross]], hole["radius"], distance)
            if hole_half is None:
                continue
        hits.extend([hole["center"][along] - hole_half, hole["center"][along] + hole_half])
    return sorted(hits)


def dimension_oracles(request, identifier, parameters):
    spec = request["params"]
    op = parameters["op"]
    cx, cy = parameters["center"][:2]
    if op == "quad.panel":
        width, height = parameters["size"]
        y = cy - height / 2 + parameters["corner_radius"] + parameters["edge_bevel"] + 1
        thickness = add_probe(spec, "panel_thickness", identifier, "Z", [cx, y],
                              [parameters["z_min"], parameters["z_max"]])
        add_difference(spec, "panel_thickness_difference", thickness, 0, 1)
        for hole in parameters["holes"]:
            add_probe(spec, hole["id"] + "_through", identifier, "Z", hole["center"], [])
            stations = [("bore", (parameters["z_min"] + parameters["z_max"]) / 2)]
            recess = hole.get("counterbore")
            if recess:
                z = parameters["z_max"] - recess["depth"] / 2 if recess["side"] == "top" else parameters["z_min"] + recess["depth"] / 2
                bore_z = ((parameters["z_min"] + parameters["z_max"] - recess["depth"]) / 2
                          if recess["side"] == "top" else
                          (parameters["z_min"] + recess["depth"] + parameters["z_max"]) / 2)
                stations[0] = ("bore", bore_z)
                stations.append(("counterbore", z))
            for label, z in stations:
                for axis, cross in (("X", 1), ("Y", 0)):
                    fixed = hole["center"][cross]
                    # Curves intersected off their cardinal samples have bounded chord error.
                    add_probe(spec, hole["id"] + "_" + label + "_" + axis.lower(), identifier,
                              axis, [fixed, z], panel_hits(parameters, axis, fixed, z),
                              parameters["chord_tolerance"] + POSITION_TOLERANCE_MM)
        for lip in parameters["lips"]:
            a, b, c, d = lip["bounds"]
            row = add_probe(spec, lip["id"] + "_height", identifier, "Z", [(a + c) / 2, (b + d) / 2],
                            [lip["z_min"], parameters["z_max"]])
            add_difference(spec, lip["id"] + "_difference", row, 0, 1)
    elif op == "quad.shell":
        width, depth, height = parameters["size"]
        z_min = parameters["z_min"]
        floor = z_min + parameters["base_thickness"]
        row = add_probe(spec, "floor_thickness", identifier, "Z", [cx + width * .2, cy + depth * .2], [z_min, floor])
        add_difference(spec, "floor_thickness_difference", row, 0, 1)
        wall = parameters["wall_thickness"]
        add_probe(spec, "side_wall_thickness", identifier, "X", [cy, z_min + height - wall],
                  [cx - width / 2, cx - width / 2 + wall, cx + width / 2 - wall, cx + width / 2])
        for post in parameters["posts"]:
            add_probe(spec, post["id"] + "_blind_depth", identifier, "Z", post["center"], [post["hole_top"], post["z_top"]])
        for seat in parameters["corner_seats"]:
            add_probe(spec, seat["id"] + "_blind_depth", identifier, "Z", seat["hole_center"], [z_min, seat["hole_bottom"]])
        for opening in parameters["openings"]:
            axis = "X" if opening["side"] in ("left", "right") else "Y"
            sides = ("left", "right") if axis == "X" else ("front", "back")
            transverse, z = opening["center"]
            z += opening["radius"] / 8  # Avoid the opposite opening's exact tangent elevation.
            extent, center = (width, cx) if axis == "X" else (depth, cy)
            hits = []
            for side, endpoints in zip(sides, ((center - extent / 2, center - extent / 2 + wall),
                                               (center + extent / 2 - wall, center + extent / 2))):
                open_here = False
                for other in parameters["openings"]:
                    if other["side"] != side:
                        continue
                    half = rounded_half_span(other["size"], other["radius"], z - other["center"][1])
                    open_here |= half is not None and abs(transverse - other["center"][0]) < half
                if not open_here:
                    hits.extend(endpoints)
            add_probe(spec, opening["id"] + "_passage", identifier, axis, [transverse, z], hits)
    elif op == "quad.fastener":
        shoulder, direction = parameters["shoulder_z"], parameters["direction"]
        length, radius = parameters["shaft_length"], parameters["shaft_radius"]
        probe_ids = []
        for label, fraction in (("low", .75), ("high", .25)):
            z = shoulder - direction * length * fraction
            for axis, center, transverse in (("X", cx, cy), ("Y", cy, cx)):
                name = "shaft_" + label + "_" + axis.lower()
                row = add_probe(spec, name, identifier, axis, [transverse, z], [center - radius, center + radius])
                add_difference(spec, name + "_diameter", row, 0, 1)
                probe_ids.append(name)
        spec["axis_checks"].append({"id": "shaft_coaxiality", "probes": probe_ids,
                                    "separation_mm": length / 2, "maximum_degrees": .01})
        height, socket_depth = parameters["head_height"], parameters["socket_depth"]
        head_radius = parameters["head_radius"]
        head_z = shoulder + direction * (height - socket_depth) / 2
        add_probe(spec, "head_diameter", identifier, "X", [cy, head_z], [cx - head_radius, cx + head_radius])
        end = shoulder - direction * length
        cap = shoulder + direction * (height - socket_depth)
        add_probe(spec, "center_axial_depth", identifier, "Z", [cx, cy], [min(end, cap), max(end, cap)])
        if socket_depth:
            socket_z = shoulder + direction * (height - socket_depth / 2)
            flat = parameters["socket_flat_width"]
            add_probe(spec, "socket_flat_width", identifier, "Y", [cx, socket_z],
                      [cy - head_radius, cy - flat / 2, cy + flat / 2, cy + head_radius])
            add_probe(spec, "socket_corner_width", identifier, "X", [cy, socket_z],
                      [cx - head_radius, cx - flat / math.sqrt(3), cx + flat / math.sqrt(3), cx + head_radius])
    return request


def assembly_features(kind, contact_axis="Z"):
    rows = [feature("base", {"op": "primitive.box", "size": [26, 18, 4], "center": [3, -7, 0]}),
            feature("upper", {"op": "primitive.box", "size": [12, 10, 3], "center": [3, -7, 3.5]}),
            feature("separate", {"op": "primitive.box", "size": [5, 7, 6], "center": [43, 9, 0]})]
    if kind == "intrusion":
        rows[1]["program"]["steps"][0]["center"][2] = 3.0
    elif kind == "containment":
        rows[1]["program"]["steps"][0].update(size=[3, 2, 1], center=[3, -7, 0])
    # Map the original XY contact rectangle to the other world-axis planes.
    # Its ordered local coordinates remain original X/Y on both new planes.
    order = {"X": (2, 0, 1), "Y": (0, 2, 1), "Z": (0, 1, 2)}[contact_axis]
    for row in rows:
        parameters = row["program"]["steps"][0]
        for key in ("center", "size"):
            parameters[key] = [parameters[key][index] for index in order]
    return rows


def declare_contact(request, axis="Z"):
    request["params"]["contacts"] = [{"pair": ["base", "upper"], "regions": [{
        "axis": axis, "plane_mm": 2, "outer": {"kind": "rectangle", "min_mm": [-3, -12], "max_mm": [9, -2]}}]}]
    return request


def bore_contact_features(cases, intrusion=False):
    """Derive a small mating pair entirely from the first public panel case."""
    panel = copy.deepcopy(next(row["parameters"] for row in cases if row["parameters"]["op"] == "quad.panel"))
    hole = next(row for row in panel["holes"] if row["kind"] == "circle"
                and row.get("counterbore", {}).get("side") == "top")
    recess = hole["counterbore"]
    fastener = {"op": "quad.fastener", "center": list(hole["center"]),
                "shaft_radius": hole["radius"] * .7, "head_radius": recess["radius"] * .8,
                "head_height": recess["depth"] * .8,
                "shaft_length": (panel["z_max"] - panel["z_min"]) * 1.5,
                "shoulder_z": panel["z_max"] - recess["depth"] - (.04 if intrusion else 0),
                "direction": 1, "socket_flat_width": 0, "socket_depth": 0,
                "chord_tolerance": panel["chord_tolerance"],
                "target_edge_length": panel["target_edge_length"], "max_segments": panel["max_segments"]}
    return [feature("contact_panel", panel), feature("contact_fastener", fastener)]


def bore_contact_request(name, source, features):
    request = validation_request(name, source, features)
    for row in features:
        dimension_oracles(request, row["id"], row["program"]["steps"][0])
    panel, fastener = [row["program"]["steps"][0] for row in features]
    hole = next(row for row in panel["holes"] if row["kind"] == "circle"
                and row.get("counterbore", {}).get("side") == "top")
    # The actual panel seat remains fixed even in the intrusion case. Moving
    # the declared plane with the intruding head would test a missing plane,
    # rather than qualification of the real bore followed by pair rejection.
    plane = panel["z_max"] - hole["counterbore"]["depth"]
    request["params"]["contacts"] = [{"pair": ["contact_panel", "contact_fastener"], "regions": [{
        "axis": "Z", "plane_mm": plane,
        "outer": {"kind": "disk", "center_mm": list(hole["center"]), "radius_mm": fastener["head_radius"]},
        "bores": [{"part": "contact_panel", "center_mm": list(hole["center"]), "radius_mm": hole["radius"],
                   "position_tolerance_mm": POSITION_TOLERANCE_MM,
                   "chord_tolerance_mm": panel["chord_tolerance"]}]}]}]
    return request


class Qualification:
    def __init__(self, cases):
        self.cases = cases
        self.tag = uuid.uuid4().hex
        self.out = ROOT / "evidence" / ("generic-cli-" + self.tag)
        self.out.mkdir(parents=True, exist_ok=False)
        # The host's immutable source guards require a fresh /tmp job store;
        # the checkout/evidence mount is intentionally not a guard substitute.
        self.jobs = Path(tempfile.mkdtemp(prefix="hs-public-cli-", dir="/tmp")) / "jobs"
        self.rows = []
        self.sources = {str(FIXTURES): sha256(FIXTURES)}
        self.built = {}
        self.started = time.monotonic()
        self.runtime = {name: descriptor(os.environ[name]) for name in ("BLENDER_PATH", "BLENDERCTL_PYTHON")}
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="2",
                        OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2")

    def protect(self, ref):
        self.sources[ref["file"]] = ref["expected_sha256"]

    def check_sources(self):
        for path, expected in self.sources.items():
            require(sha256(path) == expected, "Original input SHA changed", file=path)

    def record(self, name, function):
        started = time.monotonic()
        try:
            evidence = function()
            self.rows.append({"name": name, "status": "pass", "evidence": evidence})
        except Exception as error:
            self.rows.append({"name": name, "status": "fail", "error_type": type(error).__name__, "error": str(error)})
        self.rows[-1]["seconds"] = round(time.monotonic() - started, 3)
        self.report()
        print(json.dumps({"name": name, "status": self.rows[-1]["status"], "seconds": self.rows[-1]["seconds"]}), flush=True)

    def report(self):
        value = {"schema_version": "1.0", "scope": "Public CLI; authored synthetic numerical parts only",
                 "native_execution": True, "production_model_created": False,
                 "visual_acceptance": "not_claimed", "evidence": str(self.out),
                 "retained_job_root": str(self.jobs),
                 "cpu_threads": CPU_THREADS, "per_command_wall_seconds": WALL_SECONDS,
                 "runtime_executables": self.runtime,
                 "elapsed_seconds": round(time.monotonic() - self.started, 3),
                 "passed": sum(row["status"] == "pass" for row in self.rows),
                 "failed": sum(row["status"] != "pass" for row in self.rows),
                 "protected_input_sha256": self.sources, "tests": self.rows}
        # This is the new run's own progress file, never an input or an existing run.
        (self.out / "result.json").write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return value

    def call(self, action, name, request, *, returncode=0):
        request = copy.deepcopy(request)
        request["params"]["request_id"] = name + ":" + self.tag
        path = self.out / (name + ".request.json")
        write_new(path, request)
        self.protect(descriptor(path))
        self.check_sources()
        started = time.monotonic()
        try:
            result = subprocess.run([str(ROOT / "hardsurface-cli"), "--jobs-dir", str(self.jobs),
                                     "hardsurface", action, "--request", str(path)],
                                    cwd=ROOT, env=self.env, capture_output=True, text=True,
                                    timeout=PROCESS_SECONDS, check=False)
        finally:
            self.check_sources()
        (self.out / (name + ".stdout.txt")).write_text(result.stdout, encoding="utf-8")
        (self.out / (name + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        reply = json.loads(result.stdout)
        write_new(self.out / (name + ".response.json"), {"returncode": result.returncode,
                  "seconds": time.monotonic() - started, "response": reply})
        require(result.returncode == returncode, "Unexpected CLI exit code",
                expected=returncode, actual=result.returncode, response=reply)
        return reply.get("data", reply)

    def build(self, name, features):
        request = build_request(name, features)
        require("wire" not in request["params"], "Default wire coverage must not override defaults")
        result = self.call("run", name, request)
        require(result.get("status") == "succeeded", "Complete work unit failed", response=result)
        full = verified_json(result["report"])
        core = verified_json(full["core_report"])
        required = full["required_checks"]
        for check in ("quad_topology", "reopen", "design_dimensions", "closed_mesh", "normals", "preservation"):
            require(required[check]["status"] == "pass", "Missing passing work-unit check", check=check)
        rows = core["checks"]["quad_topology"]["evidence"]
        require(len(rows) == 2 * len(features), "Actual mesh states do not cover every part")
        actual_states = {(row["feature_id"], row["mesh_state"]) for row in rows}
        require(actual_states == set(itertools.product([row["id"] for row in features], ["control", "evaluated"])),
                "Control/evaluated coverage differs")
        topology = []
        for row in rows:
            detail = verified_json(row["details"])
            require(detail["passed"] and detail["counts"].get("ngons", 0) == 0
                    and detail["counts"].get("triangles", 0) == 0, "Actual polygon gate failed", detail=detail)
            require(detail["counts"]["quads"] == detail["counts"]["faces"], "A polygon is not a quad")
            require(detail["self_intersections"]["status"] == "pass", "Actual self-intersection gate did not pass")
            topology.append({"feature_id": row["feature_id"], "mesh_state": row["mesh_state"],
                             "counts": detail["counts"], "self_intersections": "pass", "details": row["details"]})
        diagnostics = full["topology_diagnostics"]
        require(diagnostics["status"] == "pass" and diagnostics["components"] == len(features),
                "Default diagnostics do not cover every part")
        require(len(diagnostics["views"]) == len(features), "Missing default wire views")
        wire_rows = []
        for index, ref in enumerate(diagnostics["views"], 1):
            data = Path(ref["file"]).read_bytes()
            require(sha256(ref["file"]) == ref["sha256"] and len(data) == ref["bytes"], "Wire image identity differs")
            require(data[:8] == b"\x89PNG\r\n\x1a\n", "Wire output is not PNG")
            width, height = struct.unpack(">II", data[16:24])
            require(64 <= width <= 480 and 64 <= height <= 360, "Wire image exceeds native qualification budget")
            wire_report = load_json(Path(diagnostics["statistics"]["file"]).parent / ("wire-default-%03d-result.json" % index))
            for preview in wire_report["previews"]:
                wire = preview["wire"]
                require(wire["enabled"] and wire["actual_edges"] > 0 and wire["added_triangulation_edges"] == 0
                        and wire["source_meshes_modified"] is False, "Wire does not show actual mesh edges", wire=wire)
                wire_rows.append({"image": ref, "size": [width, height], "actual_edges": wire["actual_edges"], "method": wire["method"]})
        require(full["source_protection"]["staged_snapshot_guarded"]["accepted"]
                and full["source_protection"]["resources_guarded"]["accepted"], "Build guards were not finalized")
        source = descriptor(result["candidate"]["file"])
        require(source["expected_sha256"] == result["candidate"]["sha256"], "Accepted candidate SHA differs")
        self.protect(source)
        self.built[name] = {"source": source, "features": features, "result": result}
        return {"job_id": result["job_id"], "source": source, "report": result["report"],
                "actual_topology": topology, "default_wire": wire_rows, "independent_reopen": "pass"}

    def spec(self, name):
        require(name in self.built, "Required source build did not succeed", source=name)
        row = self.built[name]
        request = validation_request("validate_" + name, row["source"], row["features"])
        if len(row["features"]) == 1:
            item = row["features"][0]
            dimension_oracles(request, item["id"], item["program"]["steps"][0])
        return request

    def validate(self, name, request, expected="pass", failed_layer=None):
        result = self.call("validate", name, request)
        require(result["status"] == "succeeded", "Validation transport failed", response=result)
        require(result["domain_outcome"] == expected, "Wrong geometric domain outcome", response=result)
        require(result["source_saved"] is False and result["read_only"] is True, "Validation was not read-only")
        require(result["source_guard"]["accepted"], "Read-only source guard did not close")
        value = result["validation"]
        if value.get("details_omitted"):
            from hardsurface.validation_report import read_validation_export
            value = read_validation_export(value["details_reference"]["file"])
        parts = [row["id"] for row in request["params"]["parts"]]
        wanted_pairs = {frozenset(pair) for pair in itertools.combinations(parts, 2)}
        actual_pairs = {frozenset(row["pair"]) for row in value["pairs"]}
        require(actual_pairs == wanted_pairs and len(value["pairs"]) == len(wanted_pairs),
                "Complete unordered pair coverage missing", expected=len(wanted_pairs), actual=len(value["pairs"]))
        if expected == "pass":
            require(all(row["status"] == "pass" for row in value["checks"]), "A caller specification did not pass")
            require(all(row["status"] == "pass" for row in value["pairs"]), "A pair did not pass")
            require(result["full_assembly_acceptance"] == "pass", "Complete caller-spec scope did not pass")
        if failed_layer == "checks":
            require(any(row["status"] == "fail" for row in value["checks"]), "Wrong dimensions escaped caller-spec checks")
            require(all(row["status"] == "pass" for row in value["pairs"]), "Dimension failure incorrectly changed pair results")
        if failed_layer == "pairs":
            require(all(row["status"] == "pass" for row in value["checks"]), "Pair fixture unexpectedly failed dimensions")
            require(any(row["status"] == "fail" for row in value["pairs"]), "Actual interpart problem escaped pair checks")
        return {"job_id": result["job_id"], "domain_outcome": result["domain_outcome"],
                "full_assembly_acceptance": result["full_assembly_acceptance"],
                "checks": value["checks"], "pairs": value["pairs"],
                "contact_boundaries": value.get("contact_boundaries", []), "source_guard": result["source_guard"]}

    def rejected(self, name, request, code, *, before_worker):
        before = {path.name for path in (self.jobs / "jobs").glob("*")}
        result = self.call("validate", name, request, returncode=2 if before_worker else 5)
        require(result.get("error", {}).get("code") == code, "Rejected at the wrong layer", response=result)
        after = {path.name for path in (self.jobs / "jobs").glob("*")}
        require(before == after if before_worker else len(after - before) == 1,
                "Invalid request reached the wrong execution layer")
        return {"expected_code": code, "error": result["error"], "new_jobs": sorted(after - before),
                "layer": "request_contract" if before_worker else "actual_scene_selection"}

    def export_topology(self, source_name):
        source = self.built[source_name]["source"]
        request = {"schema_version": "1.0", "command": "hardsurface.topology", "params": {
            "request_id": "public_geometry_export", "source": source,
            "mesh_states": ["control", "evaluated"], "export_geometry": True,
            "self_intersections": True, "cpu_threads": CPU_THREADS, "wall_seconds": WALL_SECONDS}}
        result = self.call("topology", "export_actual_control_evaluated", request)
        require(result["status"] == "succeeded", "Topology export failed", response=result)
        full = verified_json(result["report"])
        require(full["source_saved"] is False and full["source_guard"]["accepted"], "Topology export did not preserve source")
        from hardsurface.topology import read_geometry_export
        exports = []
        require(len(full["topology"]["objects"]) == 1, "Unexpected exported object count")
        for item in full["topology"]["objects"]:
            require(set(item["states"]) == {"control", "evaluated"}, "Missing actual mesh state")
            for state, row in item["states"].items():
                ref = row["geometry"]
                require(ref["bytes"] <= 8 * 1024 * 1024, "Geometry export exceeds per-file bound")
                exported = read_geometry_export(ref["file"])
                require(exported["polygons"] and all(len(face) == 4 for face in exported["polygons"]), "Export contains non-quad polygons")
                require(row["self_intersections"]["status"] == "pass", "Export self-intersection audit failed")
                exports.append({"mesh_state": state, "geometry": ref, "vertices": len(exported["vertices"]),
                                "polygons": len(exported["polygons"]), "self_intersections": "pass"})
        require(sum(item["bytes"] for item in full["outputs"]) <= 128 * 1024 * 1024, "Aggregate export exceeds bound")
        return {"report": result["report"], "exports": exports}

    def run(self):
        for row in self.cases:
            identifier = row["id"]
            self.record("build_" + identifier, lambda row=row: self.build(row["id"], [feature(row["id"], row["parameters"])]))
            self.record("validate_" + identifier, lambda identifier=identifier: self.validate("validate_" + identifier, self.spec(identifier)))
        fastener = next(row["id"] for row in self.cases if row["parameters"]["op"] == "quad.fastener")
        self.record("export_actual_control_evaluated", lambda: self.export_topology(fastener))

        def pair_case(kind, name, axis="Z"):
            result = self.validate("validate_" + name, declare_contact(self.spec(name), axis),
                                   "pass" if kind == "contact" else "fail", None if kind == "contact" else "pairs")
            pair = next(row for row in result["pairs"] if set(row["pair"]) == {"base", "upper"})
            if kind == "contact":
                require(pair["allowed_contact_count"] > 0 and pair["forbidden_intersection_count"] == 0
                        and pair["strict_containment_count"] == 0, "Declared bounded contact was not actually exercised")
            elif kind == "intrusion":
                # Intersections can lie wholly inside the declared contact
                # plane/region; that never licenses strict volume intrusion.
                require(pair["allowed_contact_count"] + pair["forbidden_intersection_count"] > 0
                        and pair["strict_containment_count"] > 0,
                        "Intrusion requires actual intersections and source-qualified strict inside witnesses")
            else:
                require(pair["strict_containment_count"] > 0 and pair["forbidden_intersection_count"] == 0,
                        "Containment must be found without surface intersections")
            return result

        for kind in ("contact", "intrusion", "containment"):
            source_name = "pair_" + kind
            self.record("build_" + source_name, lambda kind=kind, name=source_name: self.build(name, assembly_features(kind)))
            self.record("validate_" + source_name, lambda kind=kind, name=source_name: pair_case(kind, name))

        for axis in ("X", "Y"):
            source_name = "pair_contact_" + axis.lower()
            self.record("build_" + source_name, lambda axis=axis, name=source_name: self.build(name, assembly_features("contact", axis)))
            self.record("validate_" + source_name, lambda axis=axis, name=source_name: pair_case("contact", name, axis))

        def bore_spec(name):
            require(name in self.built, "Required bore-contact source did not succeed", source=name)
            row = self.built[name]
            return bore_contact_request("validate_" + name, row["source"], row["features"])

        def bore_case(name, intrusion):
            result = self.validate("validate_" + name, bore_spec(name),
                                   "fail" if intrusion else "pass", "pairs" if intrusion else None)
            boundaries = result["contact_boundaries"]
            require(boundaries and all(row["status"] == "pass" and row["candidate_edge_count"] > 0 for row in boundaries),
                    "Actual evaluated bore boundary was not qualified", boundaries=boundaries)
            pair = result["pairs"][0]
            if intrusion:
                require(pair["strict_containment_count"] > 0,
                        "Lowered head must produce strict volume intrusion despite a qualified bore")
            else:
                require(pair["allowed_contact_count"] > 0 and pair["forbidden_intersection_count"] == 0
                        and pair["strict_containment_count"] == 0,
                        "The real head/seat contact and bore exclusion were not exercised")
            return result

        for intrusion in (False, True):
            source_name = "bore_contact_intrusion" if intrusion else "bore_contact_seated"
            self.record("build_" + source_name, lambda intrusion=intrusion, name=source_name:
                        self.build(name, bore_contact_features(self.cases, intrusion)))
            self.record("validate_" + source_name, lambda intrusion=intrusion, name=source_name: bore_case(name, intrusion))

        def wrong_bore_radius():
            request = bore_spec("bore_contact_seated")
            bore = request["params"]["contacts"][0]["regions"][0]["bores"][0]
            bore["radius_mm"] += 10 * bore["position_tolerance_mm"]
            result = self.validate("wrong_actual_bore_radius", request, "fail")
            checks = [row for row in result["checks"] if row.get("kind") == "actual_bore_qualification"]
            require(checks and all(row["status"] == "fail" for row in checks),
                    "Incorrect bore specification escaped eager actual-loop qualification")
            require(all(row["status"] == "pass" for row in result["checks"] if row.get("kind") != "actual_bore_qualification"),
                    "Incorrect bore metadata must not change geometric dimension results")
            require(result["contact_boundaries"] and all(row["status"] == "fail" for row in result["contact_boundaries"]),
                    "Incorrect bore specification lacks explicit failed boundary evidence")
            return result
        self.record("wrong_actual_bore_radius", wrong_bore_radius)

        def dimension_negative(kind):
            request = self.spec(fastener)
            if kind == "bounds":
                request["params"]["parts"][0]["bounds_mm"]["expected"][3] += 1
            elif kind == "ray":
                request["params"]["probes"][0]["expected_hits_mm"][0] -= .5
            else:
                request["params"]["difference_checks"][0]["expected_mm"] += .5
            result = self.validate("wrong_" + kind, request, "fail", "checks")
            target = ("@bounds/" + fastener if kind == "bounds" else
                      request["params"]["probes"][0]["id"] if kind == "ray" else
                      request["params"]["difference_checks"][0]["id"])
            require(next(row for row in result["checks"] if row["id"] == target)["status"] == "fail",
                    "Wrong dimension did not fail its own caller-specified check", check=target)
            return result

        for kind in ("bounds", "ray", "difference"):
            self.record("wrong_" + kind, lambda kind=kind: dimension_negative(kind))
        self.record("undeclared_contact", lambda: self.validate("undeclared_contact", self.spec("pair_contact"), "fail", "pairs"))

        def too_small_region():
            request = declare_contact(self.spec("pair_contact"))
            request["params"]["contacts"][0]["regions"][0]["outer"]["max_mm"][0] -= 1
            return self.validate("contact_region_too_small", request, "fail", "pairs")
        self.record("contact_region_too_small", too_small_region)

        def invalid(kind):
            request = self.spec(fastener)
            if kind == "missing_parts":
                del request["params"]["parts"]
            elif kind == "missing_spec_reference":
                request["params"]["difference_checks"][0]["a"]["probe"] = "unknown_probe"
            elif kind == "missing_scene_part":
                request["params"]["parts"][0]["feature_id"] = "unknown_feature"
            return self.rejected(kind, request, "INVALID_REQUEST" if kind in ("missing_parts", "missing_spec_reference")
                                 else "VALIDATION_SELECTION", before_worker=kind in ("missing_parts", "missing_spec_reference"))
        for kind in ("missing_parts", "missing_spec_reference", "missing_scene_part"):
            self.record(kind, lambda kind=kind: invalid(kind))

        def omitted_part():
            request = declare_contact(self.spec("pair_contact"))
            # Omit only the separate third box. The declared base/upper contact
            # still refers to selected parts and must continue to pass.
            request["params"]["parts"] = [part for part in request["params"]["parts"] if part["id"] != "separate"]
            del request["params"]["assembly_bounds_mm"]
            result = self.validate("omitted_visible_part", request, "fail", "checks")
            selected = next(row for row in result["checks"] if row["id"] == "@assembly/selected_meshes")
            require(selected["status"] == "fail" and selected["unselected_visible_meshes"],
                    "Visible-part omission did not fail assembly completeness")
            require(all(row["status"] == "pass" for row in result["checks"] if row["id"] != "@assembly/selected_meshes"),
                    "Visible-part omission must fail only the completeness check")
            return result
        self.record("omitted_visible_part", omitted_part)
        self.record("all_input_sources_unchanged", lambda: (self.check_sources() or {"count": len(self.sources), "sha256_unchanged": True}))
        report = self.report()
        print(json.dumps({"report": str(self.out / "result.json"), "passed": report["passed"], "failed": report["failed"]}), flush=True)
        return 0 if report["failed"] == 0 else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run native CLI qualification with explicitly selected runtime")
    args = parser.parse_args()
    fixture = load_json(FIXTURES)
    cases = fixture["cases"]
    require(len({row["id"] for row in cases}) == len(cases), "Duplicate public fixture ID")
    require({row["parameters"]["op"] for row in cases} == {"quad.panel", "quad.shell", "quad.fastener"}, "Public domain coverage is incomplete")
    require({row["parameters"]["direction"] for row in cases if row["parameters"]["op"] == "quad.fastener"} == {-1, 1}, "Both fastener directions are required")
    if not args.run:
        from hardsurface.planner import plan_request
        rows = []
        for row in cases:
            try:
                planned = plan_request(build_request(row["id"], [feature(row["id"], row["parameters"])]))
                require(planned["request"]["params"]["wire"]["enabled"], "Default wire disabled")
                estimate = planned["steps"][0].get("geometry_estimate", {})
                rows.append({"id": row["id"], "status": "pass", "geometry_estimate": {
                    key: estimate[key] for key in ("vertices", "faces", "loops", "construction_sha256") if key in estimate}})
            except Exception as error:
                rows.append({"id": row["id"], "status": "fail", "error": str(error)})
        for kind in ("contact", "intrusion", "containment"):
            planned = plan_request(build_request("pair_" + kind, assembly_features(kind)))
            rows.append({"id": "pair_" + kind, "status": "pass", "steps": len(planned["steps"])})
        for axis in ("X", "Y"):
            name = "pair_contact_" + axis.lower()
            planned = plan_request(build_request(name, assembly_features("contact", axis)))
            rows.append({"id": name, "status": "pass", "steps": len(planned["steps"]), "contact_plane_axis": axis})
        for intrusion in (False, True):
            name = "bore_contact_intrusion" if intrusion else "bore_contact_seated"
            planned = plan_request(build_request(name, bore_contact_features(cases, intrusion)))
            rows.append({"id": name, "status": "pass", "steps": len(planned["steps"]), "actual_bore_qualification": True})
        print(json.dumps({"mode": "static_plan_only", "native_execution": "not_run", "plans": rows,
                          "native_build_work_units": len(cases) + 7, "default_wire": [480, 360],
                          "cpu_threads": CPU_THREADS, "per_command_wall_seconds": WALL_SECONDS,
                          "required_environment": ["BLENDER_PATH", "BLENDERCTL_PYTHON"],
                          "negative_semantics": ["wrong_bounds", "wrong_ray", "wrong_difference", "intrusion", "containment",
                                                 "undeclared_contact", "undersized_contact_region", "missing_parts", "missing_probe_reference",
                                                 "missing_scene_part", "omitted_visible_part", "bore_contact_intrusion",
                                                 "wrong_actual_bore_radius"]}, indent=2))
        return 0 if all(row["status"] == "pass" for row in rows) else 2
    for name in ("BLENDER_PATH", "BLENDERCTL_PYTHON"):
        value = os.environ.get(name)
        require(value and Path(value).is_absolute() and Path(value).is_file() and os.access(value, os.X_OK),
                "Native qualification requires an explicit existing executable", environment=name)
    return Qualification(cases).run()


if __name__ == "__main__":
    raise SystemExit(main())
