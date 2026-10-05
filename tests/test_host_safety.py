"""Host integration safety with mocked work execution; no Blender/native runs.

All test directories and lock files are retained in evidence/host-safety-tests.
The implementation identity test changes a copied fixture root only.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from hardsurface import host
from hardsurface.budgets import BudgetExceeded, BudgetLimits, JobBudget
from hardsurface.io import RuntimeFailure
from hardsurface.jobs import JobStore

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "host-safety-tests"
EVIDENCE.mkdir(parents=True, exist_ok=True)
DIGEST = hashlib.sha256(b"host safety numerical fixture").hexdigest()
REOPEN_CHECKS = {name: "pass" for name in
                 ("technical", "preservation", "dependency_reproduction", "state_consistency")}


class HostSupervisorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="supervisor-", dir=EVIDENCE))
        self.store = JobStore(self.base / "store", allowed_roots=[self.base])
        self.job = self.store.submit("owned-supervisor", DIGEST)

    def test_only_one_supervisor_can_enter_queued_job(self):
        entered, release = threading.Event(), threading.Event()
        expected = {"status": "succeeded", "job_id": self.job["job_id"]}

        def owned_work(root, job_id, blender, started, checkpoint, budget, parent):
            self.assertEqual(self.store.status(job_id)["status"], "queued")
            self.store.update(job_id, status="running")
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release owned supervisor")
            self.store.update(job_id, status="succeeded")
            return expected

        with mock.patch.object(host, "_run_job_impl", side_effect=owned_work) as implementation:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(host.run_job, self.store.root, self.job["job_id"], "not-executed")
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    self.assertEqual(self.store.status(self.job["job_id"])["status"], "running")
                    with self.assertRaises(RuntimeFailure) as caught:
                        host.run_job(self.store.root, self.job["job_id"], "not-executed")
                    self.assertEqual(caught.exception.code, "JOB_ALREADY_RUNNING")
                    self.assertEqual(implementation.call_count, 1)
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=5), expected)
            with self.assertRaises(RuntimeFailure) as caught:
                host.run_job(self.store.root, self.job["job_id"], "not-executed")
            self.assertEqual(caught.exception.code, "JOB_NOT_QUEUED")
            self.assertEqual(implementation.call_count, 1)
        self.assertTrue((self.store.job_dir(self.job["job_id"]) / "execution.lock").exists())

    def test_all_nonqueued_states_refuse_reentry(self):
        for state in ("running", "succeeded", "failed", "timed_out", "cancelled"):
            with self.subTest(state=state):
                job = self.store.submit("state-" + state, DIGEST)
                self.store.update(job["job_id"], status=state)
                with mock.patch.object(host, "_run_job_impl") as implementation:
                    with self.assertRaises(RuntimeFailure) as caught:
                        host.run_job(self.store.root, job["job_id"], "not-executed")
                    self.assertEqual(caught.exception.code, "JOB_NOT_QUEUED")
                    implementation.assert_not_called()

    def test_supervisor_error_releases_own_lock_without_resetting_state(self):
        def failed_work(root, job_id, *args):
            self.store.update(job_id, status="running")
            raise RuntimeFailure("INJECTED_FAILURE", "Authored fixture failure")

        with mock.patch.object(host, "_run_job_impl", side_effect=failed_work):
            with self.assertRaises(RuntimeFailure) as caught:
                host.run_job(self.store.root, self.job["job_id"], "not-executed")
            self.assertEqual(caught.exception.code, "INJECTED_FAILURE")
        lock = self.store.job_dir(self.job["job_id"]) / "execution.lock"
        inode = lock.stat().st_ino
        with mock.patch.object(host, "_run_job_impl") as implementation:
            with self.assertRaises(RuntimeFailure) as caught:
                host.run_job(self.store.root, self.job["job_id"], "not-executed")
            self.assertEqual(caught.exception.code, "JOB_NOT_QUEUED")
            implementation.assert_not_called()
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(self.store.status(self.job["job_id"])["status"], "running")

    def test_execution_lock_symlink_cannot_be_followed(self):
        target = self.base / "untouched-target.txt"
        target.write_text("preserved", encoding="utf-8")
        (self.store.job_dir(self.job["job_id"]) / "execution.lock").symlink_to(target)
        with mock.patch.object(host, "_run_job_impl") as implementation:
            with self.assertRaises(OSError):
                host.run_job(self.store.root, self.job["job_id"], "not-executed")
            implementation.assert_not_called()
        self.assertEqual(target.read_text(), "preserved")

    def test_budget_checkpoint_and_parent_context_are_forwarded(self):
        shared = object()
        checkpoint = {"fixture_checkpoint": "exact"}
        started = time.monotonic()
        with mock.patch.object(host, "_run_job_impl", return_value={"mocked": True}) as implementation:
            result = host.run_job(self.store.root, self.job["job_id"], "not-executed", started,
                                  checkpoint, shared, "parent-fixture-id")
        self.assertEqual(result, {"mocked": True})
        implementation.assert_called_once_with(self.store.root, self.job["job_id"], "not-executed",
                                                started, checkpoint, shared, "parent-fixture-id")

    def test_parent_and_case_cancel_flags_propagate(self):
        parent = self.store.submit("parent-study", DIGEST)
        child = self.store.submit("child-case", DIGEST)
        self.store.parent_cancel_id = parent["job_id"]
        self.assertFalse(host.is_cancelled(self.store, child["job_id"]))
        self.store.cancel(parent["job_id"])
        self.assertTrue(host.is_cancelled(self.store, child["job_id"]))
        self.assertFalse(self.store.status(child["job_id"])["cancel_requested"])
        local = self.store.submit("local-case", DIGEST)
        self.store.parent_cancel_id = None
        self.store.cancel(local["job_id"])
        self.assertTrue(host.is_cancelled(self.store, local["job_id"]))

    def test_worker_callback_observes_parent_cancel_without_running_blender(self):
        parent = self.store.submit("callback-parent", DIGEST)
        self.store.parent_cancel_id = parent["job_id"]
        self.store.cancel(parent["job_id"])
        fake_process = mock.Mock()
        fake_process.run.return_value = {"status": "cancelled", "returncode": -15}
        budget = JobBudget(BudgetLimits(wall_seconds=10), started_at=time.monotonic())
        with mock.patch("hardsurface.jobs.OwnedProcess", return_value=fake_process) as process:
            with self.assertRaises(RuntimeFailure) as caught:
                host.worker({"job_dir": str(self.store.job_dir(self.job["job_id"]))},
                            self.store.job_dir(self.job["job_id"]), budget, self.store,
                            "never-executed-blender", "cancel-fixture")
            self.assertEqual(caught.exception.code, "CANCELLED")
            callback = process.call_args.kwargs["cancel_check"]
            self.assertTrue(callable(callback))
            self.assertTrue(callback())
            fake_process.run.assert_called_once_with()


class HostAggregateBudgetTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="aggregate-", dir=EVIDENCE))
        self.first, self.second = self.base / "case-one", self.base / "case-two"
        self.first.mkdir()
        self.second.mkdir()

    def test_case_disk_usage_is_cumulative_not_last_case_only(self):
        (self.first / "retained-one.bin").write_bytes(b"1" * 600)
        (self.second / "retained-two.bin").write_bytes(b"2" * 600)
        budget = JobBudget(BudgetLimits(wall_seconds=10, max_disk_bytes=1000), started_at=time.monotonic())
        aggregate = host.AggregateBudget(budget, [self.first])
        aggregate.check(disk_bytes=0)
        self.assertEqual(budget.peak_disk_bytes, 600)
        aggregate.add_root(self.second)
        with self.assertRaises(BudgetExceeded) as caught:
            aggregate.check(disk_bytes=0)
        self.assertEqual(caught.exception.resource, "disk_bytes")
        self.assertEqual(caught.exception.observed, 1200)
        self.assertEqual(aggregate.report()["aggregate_owned_job_roots"], [str(self.first), str(self.second)])
        self.assertTrue((self.first / "retained-one.bin").exists())
        self.assertTrue((self.second / "retained-two.bin").exists())

    def test_identical_case_root_is_not_double_counted(self):
        (self.first / "one.bin").write_bytes(b"1" * 600)
        budget = JobBudget(BudgetLimits(wall_seconds=10, max_disk_bytes=1000), started_at=time.monotonic())
        aggregate = host.AggregateBudget(budget, [self.first])
        aggregate.add_root(self.first)
        aggregate.check()
        self.assertEqual(budget.peak_disk_bytes, 600)
        self.assertEqual(len(aggregate.roots), 1)

    def test_aggregate_keeps_original_wall_start_and_rss_observation(self):
        now = [3.0]
        budget = JobBudget(BudgetLimits(wall_seconds=5, max_tree_rss_bytes=100), started_at=0, clock=lambda: now[0])
        aggregate = host.AggregateBudget(budget, [self.first, self.second])
        self.assertEqual(aggregate.remaining_seconds, 2)
        aggregate.check(tree_rss_bytes=80)
        self.assertEqual(aggregate.report()["peak_tree_rss_bytes_observed"], 80)
        now[0] = 5
        with self.assertRaises(BudgetExceeded) as caught:
            aggregate.check()
        self.assertEqual(caught.exception.resource, "wall_seconds")


class HostRequiredAcceptanceTests(unittest.TestCase):
    def plan(self, name, producer="core"):
        return {"required_checks": [{"id": name, "producer": producer, "applicable": True}], "steps": []}

    def test_missing_registry_cannot_accept(self):
        with self.assertRaises(RuntimeFailure) as caught:
            host.evaluate_required({"steps": []}, {}, {}, {}, {})
        self.assertEqual(caught.exception.code, "MISSING_REQUIRED_CHECKS")

    def test_missing_core_check_cannot_accept(self):
        with self.assertRaises(RuntimeFailure) as caught:
            host.evaluate_required(self.plan("dimensions"), {"checks": {}}, {}, {}, {})
        self.assertEqual(caught.exception.code, "REQUIRED_CHECK_FAILED")
        self.assertEqual(caught.exception.details["evidence"]["status"], "not_run")

    def test_any_nonpass_core_result_cannot_accept(self):
        for status in ("fail", "not_run", "not_applicable", "partial"):
            with self.subTest(status=status):
                with self.assertRaises(RuntimeFailure) as caught:
                    host.evaluate_required(self.plan("dimensions"), {"checks": {"dimensions": {"status": status}}}, {}, {}, {})
                self.assertEqual(caught.exception.code, "REQUIRED_CHECK_FAILED")

    def test_explicit_core_pass_is_retained_as_evidence(self):
        check = {"status": "pass", "measured": 20.0, "tolerance": .01}
        result = host.evaluate_required(self.plan("dimensions"), {"checks": {"dimensions": check}}, {}, {}, {})
        self.assertEqual(result, {"dimensions": check})

    def test_reopen_missing_or_partial_check_evidence_cannot_accept(self):
        missing_sets = [{}, *[{k: v for k, v in REOPEN_CHECKS.items() if k != missing}
                              for missing in REOPEN_CHECKS]]
        for checks in missing_sets:
            with self.subTest(checks=checks):
                with self.assertRaises(RuntimeFailure) as caught:
                    host.evaluate_required(self.plan("reopen", "host"), {}, {},
                                           {"independent_reopen": True, "checks": checks}, {})
                self.assertEqual(caught.exception.code, "REQUIRED_CHECK_FAILED")

    def test_reopen_explicit_failure_cannot_accept(self):
        for failed in REOPEN_CHECKS:
            checks = {**REOPEN_CHECKS, failed: "fail"}
            with self.subTest(failed=failed):
                with self.assertRaises(RuntimeFailure):
                    host.evaluate_required(self.plan("reopen", "host"), {}, {},
                                           {"independent_reopen": True, "checks": checks}, {})

    def test_complete_independent_reopen_can_pass(self):
        result = host.evaluate_required(self.plan("reopen", "host"), {}, {},
                                        {"independent_reopen": True, "checks": REOPEN_CHECKS}, {})
        self.assertEqual(result["reopen"]["status"], "pass")

    def test_visual_or_user_evidence_is_not_fabricated(self):
        for name in ("visual", "user_feedback"):
            with self.subTest(name=name):
                with self.assertRaises(RuntimeFailure) as caught:
                    host.evaluate_required(self.plan(name, "external"), {"checks": {name: "pass"}}, {}, {}, {})
                self.assertEqual(caught.exception.code, "REQUIRED_CHECK_FAILED")

    def test_unqualified_not_applicable_cannot_bypass_required_check(self):
        plan = self.plan("dimensions")
        plan["required_checks"][0].update(applicable=False, reason="fixture would prefer to skip")
        with self.assertRaises(RuntimeFailure) as caught:
            host.evaluate_required(plan, {}, {}, {}, {})
        self.assertEqual(caught.exception.code, "CHECK_NOT_APPLICABLE")


class HostImplementationIdentityTests(unittest.TestCase):
    def test_root_worker_bytes_are_in_implementation_fingerprint(self):
        base = Path(tempfile.mkdtemp(prefix="identity-", dir=EVIDENCE))
        copied = base / "copied-implementation"
        (copied / "hardsurface").mkdir(parents=True)
        (copied / "hardsurface" / "fixture.py").write_text("# authored hash fixture only; never executed\n")
        worker = copied / "blender_worker.py"
        shutil.copyfile(ROOT / "blender_worker.py", worker)
        (copied / "blender_manifest.toml").write_text('version = "fixture"\n')
        binary = base / "not-executed-blender.bin"
        binary.write_bytes(b"Numerical descriptor fixture, not an executable")
        original_worker = worker.read_bytes()
        with mock.patch.object(host, "ROOT", copied):
            before = host.impl_identity(binary)
            worker.write_bytes(original_worker + b"\n# changed only in retained test copy\n")
            after = host.impl_identity(binary)
            (copied / "unrelated.txt").write_text("Nonimplementation note")
            unchanged = host.impl_identity(binary)
        self.assertNotEqual(before["source_sha256"], after["source_sha256"])
        self.assertEqual(after["source_sha256"], unchanged["source_sha256"])
        self.assertEqual(before["python"], after["python"])
        self.assertEqual(before["blender"], after["blender"])
        self.assertNotEqual(worker.resolve(), (ROOT / "blender_worker.py").resolve())
        self.assertTrue(worker.exists())


if __name__ == "__main__":
    unittest.main()
