"""Checkpoint protocol tests use explicit text fixtures, not real .blend models."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hardsurface.jobs import JobStore, ReliabilityError
from hardsurface.recovery import (CheckpointStore, REQUIRED_FINGERPRINTS, REQUIRED_CHECKS,
                                  artifact_descriptor, fingerprint_digest)

ROOT = Path(__file__).resolve().parents[1] / "evidence" / "reliability-tests"
ROOT.mkdir(parents=True, exist_ok=True)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="recovery-", dir=ROOT))
        self.jobs = JobStore(self.base / "store", allowed_roots=[self.base])
        job = self.jobs.submit("checkpoint-fixture", hashlib.sha256(b"fixture").hexdigest())
        self.job = self.jobs.job_dir(job["job_id"])
        self.store = CheckpointStore(self.job)
        self.fingerprints = {key: {"fixture_sha256": hashlib.sha256(key.encode()).hexdigest()} for key in REQUIRED_FINGERPRINTS}

    def fixture(self, name="one"):
        candidate = self.job / (name + ".candidate.txt")
        candidate.write_text("Numerical checkpoint protocol fixture; not a Blender file.\n" + name)
        state = self.job / (name + ".state.json")
        state.write_text(json.dumps({"revision": 1, "fixture": name}))
        descriptor = artifact_descriptor(candidate)
        checks = {key: "pass" for key in REQUIRED_CHECKS}
        evidence = self.job / (name + ".reopen.json")
        # This validates evidence shape only. Actual Blender reopening is the
        # integration host's independent responsibility and is not claimed here.
        evidence.write_text(json.dumps({"independent_reopen": True, "candidate": descriptor,
                           "checks": checks, "producer_identity": "contract-fixture-writer",
                           "verifier_identity": "contract-fixture-reader", "stage": "numerical-interface-test"}))
        verification = {"independent_reopen": True, "candidate": descriptor, "checks": checks,
                        "evidence": artifact_descriptor(evidence)}
        return {"candidate": descriptor, "design_state": artifact_descriptor(state)}, verification

    def accept(self, name="one"):
        artifacts, verification = self.fixture(name)
        return self.store.accept(name, artifacts, self.fingerprints, verification, completed_steps=["Feature/Step", "check"])

    def test_accepted_receipt_exact_resume(self):
        receipt = self.accept()
        resumed = self.store.resume(self.fingerprints, expected_receipt=receipt["receipt"])
        self.assertTrue(resumed["reused"])
        self.assertEqual(resumed["state"], "accepted_checkpoint")
        self.assertEqual(resumed["step_status"]["Feature/Step"], "reused_from_checkpoint")
        self.assertEqual(resumed["receipt"], receipt["receipt"])

    def test_each_fingerprint_group_mutation_rejects(self):
        self.accept()
        for key in REQUIRED_FINGERPRINTS:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.fingerprints)
                changed[key] = {"fixture_sha256": "changed"}
                with self.assertRaises(ReliabilityError) as caught:
                    self.store.resume(changed)
                self.assertIn(key, caught.exception.details["changed_fingerprint_groups"])

    def test_candidate_byte_change_rejects(self):
        receipt = self.accept()
        Path(receipt["artifacts"]["candidate"]["file"]).write_text("Changed by simulated external editor")
        with self.assertRaises(ReliabilityError) as caught:
            self.store.resume(self.fingerprints)
        self.assertEqual(caught.exception.detail_code, "CHECKPOINT_INCOMPLETE")

    def test_sidecar_change_rejects_even_candidate_unchanged(self):
        receipt = self.accept()
        Path(receipt["artifacts"]["design_state"]["file"]).write_text('{"revision":2}')
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)

    def test_verification_evidence_change_rejects(self):
        receipt = self.accept()
        Path(receipt["verification"]["evidence"]["file"]).write_text("{}")
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)

    def test_verified_in_memory_is_not_a_checkpoint(self):
        artifacts, verification = self.fixture()
        verification["independent_reopen"] = False
        with self.assertRaises(ReliabilityError):
            self.store.accept("in-memory", artifacts, self.fingerprints, verification)
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)
        self.assertTrue(Path(artifacts["candidate"]["file"]).exists())

    def test_unknown_fingerprint_rejected(self):
        for values in ({}, {**self.fingerprints, "solver": None}, {**self.fingerprints, "unexpected": "x"}):
            with self.assertRaises(ReliabilityError):
                fingerprint_digest(values)

    def test_independent_evidence_required(self):
        artifacts, verification = self.fixture()
        evidence_path = Path(verification["evidence"]["file"])
        evidence = json.loads(evidence_path.read_text())
        evidence["verifier_identity"] = evidence["producer_identity"]
        evidence_path.write_text(json.dumps(evidence))
        verification["evidence"] = artifact_descriptor(evidence_path)
        with self.assertRaises(ReliabilityError):
            self.store.accept("same-reader", artifacts, self.fingerprints, verification)

    def test_required_check_failure_rejects(self):
        artifacts, verification = self.fixture()
        verification["checks"]["state_consistency"] = "not_run"
        with self.assertRaises(ReliabilityError):
            self.store.accept("incomplete", artifacts, self.fingerprints, verification)

    def test_latest_accepted_only_and_immutable_receipts(self):
        one = self.accept("one")
        two = self.accept("two")
        self.assertEqual(self.store.resume(self.fingerprints)["checkpoint_id"], "two")
        self.assertEqual(self.store.resume(self.fingerprints, "one")["checkpoint_id"], "one")
        with self.assertRaises(ReliabilityError):
            self.store.accept("one", one["artifacts"], self.fingerprints, one["verification"])
        self.assertEqual(artifact_descriptor(one["receipt"]["file"]), one["receipt"])
        self.assertEqual(two["sequence"], 2)

    def test_partial_unaccepted_files_are_preserved_and_not_reused(self):
        artifacts, _ = self.fixture("partial")
        (self.store.root / "writing-partial.json").write_text('{"state":"persisted_unverified"}')
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)
        self.assertTrue(Path(artifacts["candidate"]["file"]).exists())
        self.assertTrue((self.store.root / "writing-partial.json").exists())

    def test_malformed_latest_receipt_does_not_fall_back(self):
        self.accept("one")
        two = self.accept("two")
        Path(two["receipt"]["file"]).write_text('{"state":')
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)

    def test_response_loss_after_receipt_before_commit_fails_closed(self):
        from unittest import mock
        from hardsurface import recovery
        original = recovery._atomic_json
        artifacts, verification = self.fixture()
        def interrupted(path, value, **kwargs):
            if Path(path).name.startswith("committed-"):
                raise OSError("Injected acceptance marker write failure")
            return original(path, value, **kwargs)
        with mock.patch("hardsurface.recovery._atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.store.accept("interrupted", artifacts, self.fingerprints, verification)
        self.assertTrue((self.store.root / "accepted-interrupted.json").exists())
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)

    def test_artifacts_cannot_escape_job_or_use_symlink(self):
        external = self.base / "external.txt"
        external.write_text("preserved")
        with self.assertRaises(ReliabilityError):
            artifact_descriptor(external, root=self.job)
        link = self.job / "linked.txt"
        link.symlink_to(external)
        with self.assertRaises(ReliabilityError):
            artifact_descriptor(link, root=self.job)

    def test_tampered_receipt_hash_detected(self):
        accepted = self.accept()
        path = Path(accepted["receipt"]["file"])
        raw = json.loads(path.read_text())
        raw["completed_steps"] = ["NotActuallyDone"]
        path.write_text(json.dumps(raw))
        with self.assertRaises(ReliabilityError):
            self.store.resume(self.fingerprints)


if __name__ == "__main__":
    unittest.main()
