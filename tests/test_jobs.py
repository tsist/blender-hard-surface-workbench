"""Numerical/authored host fixtures; generated job directories are retained."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

from hardsurface.budgets import BudgetLimits, JobBudget
from hardsurface.jobs import JobStore, OwnedProcess, ReliabilityError

ROOT = Path(__file__).resolve().parents[1] / "evidence" / "reliability-tests"
ROOT.mkdir(parents=True, exist_ok=True)
DIGEST = hashlib.sha256(b"canonical request fixture").hexdigest()


class JobTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="jobs-", dir=ROOT))
        self.store = JobStore(self.base / "store", allowed_roots=[self.base])

    def test_same_request_is_one_job_and_lost_response_recoverable(self):
        first = self.store.submit("request1", DIGEST)
        second = self.store.submit("request1", DIGEST)
        recovered = JobStore(self.store.root, allowed_roots=[self.base]).recover("request1", DIGEST)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["job_id"], recovered["job_id"])
        self.assertEqual(len(list(self.store.jobs_root.iterdir())), 1)

    def test_same_id_different_digest_conflicts(self):
        self.store.submit("request1", DIGEST)
        with self.assertRaises(ReliabilityError) as caught:
            self.store.submit("request1", "a" * 64)
        self.assertEqual(caught.exception.detail_code, "REQUEST_ID_CONFLICT")

    def test_concurrent_claim_never_creates_duplicate(self):
        def submit(_):
            try:
                return self.store.submit("concurrent", DIGEST)
            except ReliabilityError as exc:
                self.assertEqual(exc.detail_code, "SUBMISSION_UNKNOWN")
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(submit, range(24)))
        successful = [item for item in results if item]
        self.assertEqual(sum(not item["reused"] for item in successful), 1)
        self.assertEqual(len({item["job_id"] for item in successful}), 1)
        self.assertEqual(len(list(self.store.jobs_root.iterdir())), 1)
        self.assertEqual(self.store.recover("concurrent")["job_id"], successful[0]["job_id"])

    def test_incomplete_claim_is_not_reclaimed(self):
        claim = self.store._claim_dir("half")
        claim.mkdir()
        (claim / "claim.json").write_text('{"version":', encoding="utf-8")
        for _ in range(2):
            with self.assertRaises(ReliabilityError) as caught:
                self.store.submit("half", DIGEST)
            self.assertEqual(caught.exception.detail_code, "SUBMISSION_UNKNOWN")
        self.assertTrue(claim.exists())
        self.assertEqual(list(self.store.jobs_root.iterdir()), [])

    def test_duplicate_job_identities_fail_closed(self):
        job = self.store.submit("duplicate", DIGEST)
        alternate = uuid.uuid4().hex
        path = self.store.job_dir(alternate)
        path.mkdir()
        identity = json.loads((self.store.job_dir(job["job_id"]) / "job.json").read_text())
        identity["job_id"] = alternate
        (path / "job.json").write_text(json.dumps(identity))
        with self.assertRaises(ReliabilityError) as caught:
            self.store.recover("duplicate")
        self.assertEqual(caught.exception.detail_code, "SUBMISSION_UNKNOWN")
        self.assertEqual(caught.exception.details["match_count"], 2)

    def test_orphan_without_index_cannot_be_submitted_again(self):
        job = self.store.submit("before", DIGEST)
        identity = json.loads((self.store.job_dir(job["job_id"]) / "job.json").read_text())
        identity["request_id"] = "orphan"
        (self.store.job_dir(job["job_id"]) / "job.json").write_text(json.dumps(identity))
        with self.assertRaises(ReliabilityError) as caught:
            self.store.submit("orphan", DIGEST)
        self.assertEqual(caught.exception.detail_code, "SUBMISSION_UNKNOWN")
        self.assertEqual(len(list(self.store.jobs_root.iterdir())), 1)

    def test_cancel_is_durable_request_not_pid_kill(self):
        job = self.store.submit("cancel", DIGEST)
        self.store.update(job["job_id"], status="running", process_pid=1, heartbeat="1900-01-01")
        with mock.patch("os.kill") as kill, mock.patch("os.killpg") as killpg:
            result = self.store.cancel(job["job_id"])
            kill.assert_not_called()
            killpg.assert_not_called()
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["liveness_inferred_from_metadata"])
        self.assertEqual(self.store.cancel(job["job_id"])["cancel_requested_at"], result["cancel_requested_at"])

    def test_terminal_transition_cannot_restart(self):
        job = self.store.submit("terminal", DIGEST)
        self.store.update(job["job_id"], status="succeeded")
        with self.assertRaises(ReliabilityError):
            self.store.update(job["job_id"], status="running")
        self.assertTrue((self.store.job_dir(job["job_id"]) / "status.lock").exists())

    def test_boundary_and_symlink_rejection(self):
        with self.assertRaises(ReliabilityError):
            JobStore(self.base / "other", allowed_roots=[self.base / "different"])
        link = self.base / "link"
        link.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaises(ReliabilityError):
            JobStore(link, allowed_roots=[self.base])
        with self.assertRaises(ReliabilityError):
            self.store.status("../elsewhere")

    def test_new_jobs_limit_checked_before_claim(self):
        store = JobStore(self.base / "bounded", allowed_roots=[self.base], max_jobs=1)
        first = store.submit("first", DIGEST)
        with self.assertRaises(ReliabilityError) as caught:
            store.submit("second", DIGEST)
        self.assertEqual(caught.exception.detail_code, "JOB_LIMIT_EXCEEDED")
        self.assertFalse(store._claim_dir("second").exists())
        self.assertEqual(store.recover("first")["job_id"], first["job_id"])

    def test_nonempty_unowned_root_rejected(self):
        root = self.base / "unowned"
        root.mkdir()
        (root / "existing-user-file.txt").write_text("keep")
        with self.assertRaises(ReliabilityError):
            JobStore(root, allowed_roots=[self.base])
        self.assertEqual((root / "existing-user-file.txt").read_text(), "keep")


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux observed process family")
class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="process-", dir=ROOT))

    def process(self, code, **kwargs):
        budget = kwargs.pop("budget", None) or JobBudget(BudgetLimits(wall_seconds=5), started_at=time.monotonic())
        return OwnedProcess([sys.executable, "-c", code], cwd=self.base, budget=budget,
                            stdout_path=self.base / "stdout.log", stderr_path=self.base / "stderr.log",
                            poll_seconds=.02, **kwargs)

    def test_success_outputs_and_nonzero_exit(self):
        result = self.process("print('owned fixture')").run()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual((self.base / "stdout.log").read_text().strip(), "owned fixture")
        self.assertFalse(result["hard_memory_enforcement"])

    def test_fast_child_disk_write_cannot_skip_final_budget(self):
        budget = JobBudget(BudgetLimits(wall_seconds=5, max_disk_bytes=1024), started_at=time.monotonic())
        result = self.process("from pathlib import Path; Path('big.bin').write_bytes(b'x'*4096)",
                              budget=budget, disk_root=self.base).run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["resource"], "disk_bytes")
        self.assertGreaterEqual(result["budget"]["peak_disk_bytes_observed"], 4096)
        self.assertTrue((self.base / "big.bin").exists())

    def test_nonzero_native_style_exit_is_failure(self):
        result = self.process("import os; os._exit(19)").run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["returncode"], 19)
        self.assertTrue(result["worker_exit_observed"])

    def test_timeout_terminates_observed_child_tree(self):
        code = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; x=bytearray(12000000); time.sleep(20)']); time.sleep(20)"
        budget = JobBudget(BudgetLimits(wall_seconds=.4), started_at=time.monotonic())
        result = self.process(code, budget=budget, terminate_grace_seconds=.1).run()
        self.assertEqual(result["status"], "timed_out")
        self.assertTrue(result["worker_exit_observed"])
        self.assertFalse(result["cleanup_review_required"])
        self.assertTrue(result["observed_descendants_terminal"])
        self.assertGreaterEqual(max(len(sample["processes"]) for sample in result["process_evidence"]), 2)
        self.assertGreater(result["observed_peak_tree_rss_bytes"], 12_000_000)
        self.assertGreaterEqual(len(result["signals"]), 2)

    def test_cancel_keeps_outputs_and_confirms_exit(self):
        start = time.monotonic()
        result = self.process("import time; print('fixture',flush=True); time.sleep(20)",
                              cancel_check=lambda: time.monotonic() - start > .15).run()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["worker_exit_observed"])
        self.assertTrue((self.base / "stdout.log").exists())

    def test_stage_timeout_does_not_reset_aggregate_budget(self):
        budget = JobBudget(BudgetLimits(wall_seconds=5), started_at=time.monotonic() - 1)
        result = self.process("import time; time.sleep(10)", budget=budget, timeout_seconds=.12).run()
        self.assertEqual(result["error"]["resource"], "stage_wall_seconds")
        self.assertGreater(result["budget"]["wall_seconds_observed"], 1)

    def test_memory_observation_aborts(self):
        budget = JobBudget(BudgetLimits(wall_seconds=5, max_tree_rss_bytes=5_000_000), started_at=time.monotonic())
        result = self.process("import time; x=bytearray(20000000); time.sleep(20)", budget=budget).run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["resource"], "tree_rss_bytes")
        self.assertTrue(result["worker_exit_observed"])

    def test_no_spawn_when_already_cancelled(self):
        result = self.process("raise Exception('must not run')", cancel_check=lambda: True).run()
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["started"])
        self.assertFalse((self.base / "stdout.log").exists())

    def test_instance_cannot_run_twice(self):
        owned = self.process("pass")
        owned.run()
        with self.assertRaises(ReliabilityError):
            owned.run()


if __name__ == "__main__":
    unittest.main()
