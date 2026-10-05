"""Actual harmless fail-stop fixtures; no Blender or foreign native code.

The supervisor-death tests deliberately os._exit their own authored supervisor.
Every directory, log and marker is retained for inspection.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hardsurface.budgets import BudgetLimits, JobBudget
from hardsurface.jobs import OwnedProcess
from hardsurface import process_watchdog

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "watchdog-tests"
EVIDENCE.mkdir(parents=True, exist_ok=True)


def live_process(pid):
    try:
        raw = Path("/proc", str(pid), "stat").read_text()
        return raw[raw.rfind(")") + 2:].split()[0] not in {"Z", "X"}
    except FileNotFoundError:
        return False


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "pidfd_open"), "Linux pidfd fail-stop tests")
class ProcessWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="watchdog-", dir=EVIDENCE))

    def owned(self, code, *, wall=3):
        return OwnedProcess([sys.executable, "-c", code], cwd=self.base,
                            budget=JobBudget(BudgetLimits(wall_seconds=wall), started_at=time.monotonic()),
                            stdout_path=self.base / "stdout.log", stderr_path=self.base / "stderr.log",
                            poll_seconds=.01, terminate_grace_seconds=.05)

    def test_verified_readiness_and_normal_exit_are_preserved(self):
        result = self.owned("print('payload output'); raise SystemExit(17)").run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["returncode"], 17)
        guard = result["parent_death_watchdog"]
        self.assertTrue(guard["ready"])
        self.assertEqual(guard["identity"]["parent_pid"], os.getpid())
        self.assertEqual(guard["identity"]["mechanism"], "linux_parent_pidfd")
        self.assertEqual((self.base / "stdout.log").read_text().strip(), "payload output")

    def test_payload_signal_returncode_is_preserved(self):
        result = self.owned("import os,signal; os.kill(os.getpid(),signal.SIGTERM)").run()
        self.assertEqual(result["returncode"], -signal.SIGTERM)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["parent_death_watchdog"]["ready"])

    def test_early_root_exit_waits_for_samegroup_descendant(self):
        marker = self.base / "descendant-finished.txt"
        child = f"import time; from pathlib import Path; time.sleep(.2); Path({str(marker)!r}).write_text('done')"
        code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
        started = time.monotonic()
        result = self.owned(code).run()
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(marker.exists())
        self.assertGreaterEqual(time.monotonic() - started, .2)
        self.assertTrue(result["observed_descendants_terminal"])

    def test_early_root_exit_descendant_still_obeys_parent_wall_budget(self):
        marker = self.base / "must-not-finish.txt"
        child = f"import time; from pathlib import Path; time.sleep(.5); Path({str(marker)!r}).write_text('unexpected')"
        code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
        result = self.owned(code, wall=.2).run()
        self.assertEqual(result["status"], "timed_out")
        time.sleep(.55)
        self.assertFalse(marker.exists())
        self.assertFalse(result["cleanup_review_required"])

    def test_actual_supervisor_death_kills_launched_child_group(self):
        started_file = self.base / "child-started.json"
        forbidden = self.base / "child-finished-after-parent.txt"
        payload = ("import os,json,time; from pathlib import Path; "
                   f"Path({str(started_file)!r}).write_text(json.dumps({{'pid':os.getpid(),'watchdog_pid':os.getppid(),'pgid':os.getpgrp()}})); "
                   f"time.sleep(.7); Path({str(forbidden)!r}).write_text('must not exist')")
        supervisor = f'''
import os,sys,time
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from hardsurface.jobs import OwnedProcess
from hardsurface.budgets import BudgetLimits,JobBudget
started=Path({str(started_file)!r})
def crash_after_child_started():
    if started.exists(): os._exit(19)
    return False
owned=OwnedProcess([sys.executable,'-c',{payload!r}],cwd={str(self.base)!r},
    budget=JobBudget(BudgetLimits(wall_seconds=.2),started_at=time.monotonic()),
    cancel_check=crash_after_child_started,poll_seconds=.005,
    stdout_path={str(self.base / 'supervisor-stdout.log')!r},stderr_path={str(self.base / 'supervisor-stderr.log')!r})
owned.run()
raise SystemExit(97)
'''
        completed = subprocess.run([sys.executable, "-I", "-S", "-c", supervisor], timeout=5,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(completed.returncode, 19, completed.stderr.decode())
        self.assertTrue(started_file.exists(), "payload must actually launch before the supervisor crash")
        started = json.loads(started_file.read_text())
        time.sleep(.85)
        observed = {"supervisor_exit": completed.returncode, "job_wall_seconds": .2,
                    "child_started": started, "child_completed_after_supervisor_death": forbidden.exists(),
                    "child_live": live_process(started["pid"]),
                    "watchdog_live": live_process(started["watchdog_pid"]),
                    "scope": "authored harmless Python process; no Blender"}
        (self.base / "supervisor-death-evidence.json").write_text(json.dumps(observed, indent=2))
        self.assertFalse(observed["child_completed_after_supervisor_death"])
        self.assertFalse(observed["child_live"])
        self.assertFalse(observed["watchdog_live"])

    def test_actual_parent_exit_immediately_after_wrapper_popen_cannot_leak_payload(self):
        # Parent exit races watchdog startup/parent-pidfd binding. Even if the
        # wrapper is scheduled first, the payload must not outlive parent death.
        forbidden = self.base / "early-race-finished.txt"
        payload = f"import time; from pathlib import Path; time.sleep(.35); Path({str(forbidden)!r}).write_text('unexpected')"
        supervisor = f'''
import os,sys,time
sys.path.insert(0,{str(ROOT)!r})
import hardsurface.jobs as jobs
from hardsurface.budgets import BudgetLimits,JobBudget
real_popen=jobs.subprocess.Popen
def exit_immediately(*args,**kwargs):
    real_popen(*args,**kwargs)
    os._exit(23)
jobs.subprocess.Popen=exit_immediately
jobs.OwnedProcess([sys.executable,'-c',{payload!r}],cwd={str(self.base)!r},
    budget=JobBudget(BudgetLimits(wall_seconds=.2),started_at=time.monotonic()),
    stdout_path={str(self.base / 'early-stdout.log')!r},stderr_path={str(self.base / 'early-stderr.log')!r}).run()
'''
        completed = subprocess.run([sys.executable, "-I", "-S", "-c", supervisor], timeout=5,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(completed.returncode, 23, completed.stderr.decode())
        time.sleep(.5)
        self.assertFalse(forbidden.exists())
        (self.base / "early-parent-exit-evidence.json").write_text(json.dumps({
            "supervisor_exit": 23, "child_delayed_marker_exists": forbidden.exists(),
            "race": "supervisor exits immediately after wrapper Popen returns", "no_blender": True}, indent=2))

    def test_parent_identity_mismatch_refuses_before_launch(self):
        forbidden = self.base / "must-not-launch.txt"
        ready_read, ready_write = os.pipe()
        try:
            payload = f"from pathlib import Path; Path({str(forbidden)!r}).write_text('unexpected')"
            result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "hardsurface/process_watchdog.py"),
                                     "--parent-pid", str(os.getppid()), "--parent-start-ticks", "1",
                                     "--ready-fd", str(ready_write), "--", sys.executable, "-c", payload],
                                    start_new_session=True, pass_fds=(ready_write,), timeout=3,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, process_watchdog.FAILURE_EXIT)
            self.assertIn(b"Expected supervisor", result.stderr)
            self.assertFalse(forbidden.exists())
        finally:
            os.close(ready_read)
            os.close(ready_write)

    def test_unsupported_platform_refuses_without_pidfd_call(self):
        with mock.patch.object(process_watchdog.sys, "platform", "unsupported"), \
                mock.patch.object(process_watchdog.os, "pidfd_open") as opened:
            with self.assertRaises(process_watchdog.WatchdogError):
                process_watchdog.parent_pidfd(os.getpid(), 1)
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
