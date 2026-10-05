"""Fail-closed local job identity and live-owned process supervision.

Persistent job metadata never authorizes signaling a PID. Only an OwnedProcess
instance that started a child in this process can signal its observed process
family. All files/lock files are retained; there is no cleanup or stale-lock API.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Mapping, Sequence

from .budgets import BudgetExceeded, JobBudget, available_ram_bytes, tree_file_bytes


class ReliabilityError(RuntimeError):
    def __init__(self, detail_code: str, message: str, **details):
        self.code = "CONFLICT" if detail_code in {"REQUEST_ID_CONFLICT", "SUBMISSION_UNKNOWN"} else "VALIDATION_FAILED"
        self.detail_code, self.details = detail_code, details
        super().__init__(message)

    def as_dict(self):
        return {"code": self.code, "detail_code": self.detail_code, "message": str(self), **self.details}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _plain_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reject_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path, *, max_bytes=1024 * 1024):
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("metadata is not a regular file")
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("metadata exceeds bounded size")
        value = json.loads(raw, object_pairs_hook=_reject_pairs,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return value
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ReliabilityError("SUBMISSION_UNKNOWN", f"Unreadable or incomplete metadata: {path}", path=str(path)) from exc


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Mapping, *, exclusive=False):
    """One-file atomic publication; not a multi-file/directory transaction.

    Unpublished .pending files are intentionally preserved after every outcome.
    Exclusive publication uses a hard link, so no existing record is overwritten.
    """
    path = Path(path)
    raw = _plain_json(value)
    pending = path.parent / (".pending-" + uuid.uuid4().hex + ".json")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if exclusive:
        os.link(pending, path, follow_symlinks=False)
    else:
        if path.is_symlink():
            raise ReliabilityError("PATH_NOT_OWNED", "Metadata destination is a symlink", path=str(path))
        os.replace(pending, path)
    _fsync_dir(path.parent)


def _no_symlinks(path: Path):
    if not path.is_absolute() or ".." in path.parts:
        raise ReliabilityError("PATH_NOT_OWNED", "Absolute, non-traversing path required", path=str(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ReliabilityError("PATH_NOT_OWNED", "Symlink path component rejected", path=str(current))
        except FileNotFoundError:
            pass
    return path


def _bounded_root(root, allowed_roots):
    root = _no_symlinks(Path(root))
    if not allowed_roots:
        raise ReliabilityError("PATH_NOT_OWNED", "Explicit trusted allowed_roots are required")
    allowed = [_no_symlinks(Path(item)) for item in allowed_roots]
    if any(item == Path("/") for item in allowed):
        raise ReliabilityError("PATH_NOT_OWNED", "Filesystem root is not a bounded authorization")
    if not any(root == item or item in root.parents for item in allowed):
        raise ReliabilityError("PATH_NOT_OWNED", "Job root is outside authorized roots", path=str(root))
    if root == Path("/"):
        raise ReliabilityError("PATH_NOT_OWNED", "Filesystem root cannot be a job root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_symlinks(root)
    if not root.is_dir() or root.stat().st_uid != os.geteuid():
        raise ReliabilityError("PATH_NOT_OWNED", "Job root must be a directory owned by this user")
    return root


class JobStore:
    TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
    STATES = TERMINAL | {"queued", "running"}

    def __init__(self, root, *, allowed_roots: Sequence[os.PathLike | str], max_jobs=10_000):
        self.root = _bounded_root(root, allowed_roots)
        if not isinstance(max_jobs, int) or isinstance(max_jobs, bool) or max_jobs < 1:
            raise ValueError("max_jobs must be a positive integer")
        self.max_jobs = max_jobs
        marker = self.root / "owner.json"
        expected = {"kind": "hardsurface_job_store", "version": 1, "root": str(self.root), "uid": os.geteuid()}
        if not marker.exists():
            # An unmarked nonempty directory must never be silently adopted.
            if any(self.root.iterdir()):
                raise ReliabilityError("PATH_NOT_OWNED", "Nonempty unmarked jobs root cannot be adopted")
            try:
                _atomic_json(marker, expected, exclusive=True)
            except FileExistsError:
                pass
        if _read_json(marker) != expected:
            raise ReliabilityError("PATH_NOT_OWNED", "Jobs root owner marker mismatch")
        self.index_root, self.jobs_root = self.root / "requests", self.root / "jobs"
        for directory in (self.index_root, self.jobs_root):
            _no_symlinks(directory)
            directory.mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def _validate_request(request_id, request_digest=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id):
            raise ValueError("request_id must be a bounded ASCII identifier")
        if request_digest is not None and (not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest)):
            raise ValueError("request_digest must be lowercase SHA-256")

    def _claim_dir(self, request_id):
        self._validate_request(request_id)
        return self.index_root / hashlib.sha256(request_id.encode("utf-8")).hexdigest()

    def job_dir(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ReliabilityError("PATH_NOT_OWNED", "Invalid job ID")
        path = self.jobs_root / job_id
        _no_symlinks(path)
        return path

    def _identity(self, job_id):
        value = _read_json(self.job_dir(job_id) / "job.json")
        required = {"job_id", "request_id", "request_digest", "created_at", "metadata", "version"}
        if set(value) != required or value.get("version") != 1 or value.get("job_id") != job_id or not isinstance(value.get("metadata"), dict) or not isinstance(value.get("created_at"), str):
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Job identity metadata mismatch", job_id=job_id)
        try:
            self._validate_request(value["request_id"], value["request_digest"])
        except ValueError as exc:
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Malformed job identity", job_id=job_id) from exc
        return value

    def _matching_identities(self, request_id):
        matches = []
        count = 0
        for path in self.jobs_root.iterdir():
            if path.name.startswith("."):
                continue
            count += 1
            if count > self.max_jobs:
                raise ReliabilityError("SUBMISSION_UNKNOWN", "Job identity scan bound exceeded")
            identity = self._identity(path.name)
            if identity["request_id"] == request_id:
                matches.append(identity)
        return matches

    @contextmanager
    def _submission_lock(self):
        path = _no_symlinks(self.root / "submission.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def submit(self, request_id: str, request_digest: str, metadata: Mapping | None = None):
        self._validate_request(request_id, request_digest)
        # A persistent advisory file serializes index bookkeeping and max_jobs.
        # The request's exclusive directory remains the authoritative claim;
        # automatic release of this process's flock never removes either file.
        with self._submission_lock():
            return self._submit(request_id, request_digest, metadata)

    def _submit(self, request_id, request_digest, metadata):
        self._validate_request(request_id, request_digest)
        metadata = dict(metadata or {})
        if len(_plain_json(metadata)) > 512 * 1024:
            raise ValueError("Submission metadata exceeds 512 KiB")
        claim_dir = self._claim_dir(request_id)
        if not claim_dir.exists():
            if self._matching_identities(request_id):
                raise ReliabilityError("SUBMISSION_UNKNOWN", "Unindexed existing request identity; refusing resubmission")
            if sum(1 for item in self.jobs_root.iterdir() if not item.name.startswith(".")) >= self.max_jobs:
                raise ReliabilityError("JOB_LIMIT_EXCEEDED", "Bounded jobs root is full; no automatic cleanup is permitted", max_jobs=self.max_jobs)
        _no_symlinks(claim_dir)
        try:
            claim_dir.mkdir(mode=0o700)
        except FileExistsError:
            result = self.recover(request_id, request_digest)
            return {**result, "reused": True}
        _fsync_dir(self.index_root)
        # mkdir is the exclusive claim. An interrupted claim is never reclaimed.
        job_id = uuid.uuid4().hex
        path = self.job_dir(job_id)
        path.mkdir(mode=0o700)
        identity = {"version": 1, "job_id": job_id, "request_id": request_id,
                    "request_digest": request_digest, "created_at": utc_now(), "metadata": dict(metadata or {})}
        _atomic_json(path / "job.json", identity, exclusive=True)
        _atomic_json(path / "status.json", {"version": 1, "job_id": job_id, "status": "queued",
                     "updated_at": utc_now(), "revision": 0}, exclusive=True)
        _atomic_json(claim_dir / "claim.json", {"version": 1, "request_id": request_id,
                     "request_digest": request_digest, "job_id": job_id}, exclusive=True)
        _fsync_dir(self.jobs_root)
        return {**self.status(job_id), "reused": False}

    def recover(self, request_id: str, request_digest: str | None = None):
        self._validate_request(request_id, request_digest)
        claim_dir = self._claim_dir(request_id)
        _no_symlinks(claim_dir)
        claim = _read_json(claim_dir / "claim.json")
        if set(claim) != {"version", "request_id", "request_digest", "job_id"} or claim.get("version") != 1 or claim.get("request_id") != request_id:
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Request claim is incomplete or inconsistent")
        try:
            self._validate_request(claim["request_id"], claim["request_digest"])
        except ValueError as exc:
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Malformed request claim") from exc
        if request_digest is not None and claim["request_digest"] != request_digest:
            raise ReliabilityError("REQUEST_ID_CONFLICT", "Request ID is already bound to a different digest", request_id=request_id)
        matches = self._matching_identities(request_id)
        if len(matches) != 1 or matches[0]["job_id"] != claim["job_id"] or matches[0]["request_digest"] != claim["request_digest"]:
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Zero/multiple/inconsistent job identities", request_id=request_id, match_count=len(matches))
        return self.status(claim["job_id"])

    def status(self, job_id: str):
        identity = self._identity(job_id)
        value = _read_json(self.job_dir(job_id) / "status.json")
        if (value.get("version") != 1 or value.get("job_id") != job_id or value.get("status") not in self.STATES or
            not isinstance(value.get("revision"), int) or isinstance(value.get("revision"), bool) or value["revision"] < 0 or
            not isinstance(value.get("updated_at"), str) or
            set(value) & {"request_id", "request_digest", "metadata", "created_at", "cancel_requested", "cancel_requested_at"}):
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Job status metadata mismatch", job_id=job_id)
        cancelled = _no_symlinks(self.job_dir(job_id) / "cancel.json")
        cancel = _read_json(cancelled) if cancelled.exists() else None
        if cancel and (set(cancel) != {"version", "job_id", "requested_at"} or cancel.get("job_id") != job_id or cancel.get("version") != 1):
            raise ReliabilityError("SUBMISSION_UNKNOWN", "Invalid cancellation metadata")
        return {**identity, **value, "cancel_requested": cancel is not None,
                "cancel_requested_at": cancel["requested_at"] if cancel else None,
                "liveness_inferred_from_metadata": False}

    @contextmanager
    def _status_lock(self, job_id):
        path = self.job_dir(job_id) / "status.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def update(self, job_id, **changes):
        immutable = {"version", "job_id", "request_id", "request_digest", "created_at", "metadata",
                     "revision", "cancel_requested", "cancel_requested_at", "liveness_inferred_from_metadata"}
        if immutable & set(changes):
            raise ValueError("Cannot mutate job identity or cancellation through update")
        with self._status_lock(job_id):
            old = self.status(job_id)
            new_state = changes.get("status", old["status"])
            if new_state not in self.STATES or (old["status"] in self.TERMINAL and new_state != old["status"]) or (old["status"] == "running" and new_state == "queued"):
                raise ReliabilityError("INVALID_JOB_TRANSITION", f"Invalid transition {old['status']} -> {new_state}")
            value = _read_json(self.job_dir(job_id) / "status.json")
            value.update(changes)
            value.update(updated_at=utc_now(), revision=value["revision"] + 1)
            _atomic_json(self.job_dir(job_id) / "status.json", value)
        return self.status(job_id)

    def cancel(self, job_id):
        current = self.status(job_id)
        if current["status"] in self.TERMINAL or current["cancel_requested"]:
            return current
        path = self.job_dir(job_id) / "cancel.json"
        try:
            _atomic_json(path, {"version": 1, "job_id": job_id, "requested_at": utc_now()}, exclusive=True)
        except FileExistsError:
            pass
        return self.status(job_id)


def _proc_table():
    """Linux process identity snapshots; unavailable observations fail supervision."""
    result = {}
    try:
        names = os.listdir("/proc")
    except OSError as exc:
        raise ReliabilityError("PROCESS_OBSERVATION_UNAVAILABLE", "Linux /proc observation is required") from exc
    page = os.sysconf("SC_PAGE_SIZE")
    for name in names:
        if not name.isdigit():
            continue
        try:
            text = Path("/proc", name, "stat").read_text(encoding="ascii")
            end = text.rfind(")")
            fields = text[end + 2:].split()
            pid = int(name)
            result[pid] = {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
                           "pgid": int(fields[2]), "session_id": int(fields[3]),
                           "start_ticks": int(fields[19]), "rss_bytes": max(0, int(fields[21])) * page}
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            # An unreadable process might be a child. Report an incomplete scan.
            raise ReliabilityError("PROCESS_OBSERVATION_UNAVAILABLE", "Incomplete /proc process observation", pid=int(name))
    return result


class OwnedProcess:
    """Synchronous serial supervisor for one newly spawned POSIX process family.

    A standalone Linux parent-pidfd watchdog is the new session/group leader.
    Supervisor death fail-stops that group, including descendants missed by RSS
    sampling. Processes that deliberately escape the group remain outside this
    trusted-process contract. This is not a sandbox or hard memory isolation.
    """
    def __init__(self, argv: Sequence[str], *, cwd, budget: JobBudget, cancel_check=None,
                 poll_seconds=0.05, terminate_grace_seconds=0.5, stdout_path=None,
                 stderr_path=None, env=None, disk_root=None, timeout_seconds=None):
        if not argv or isinstance(argv, (str, bytes)) or any(not isinstance(item, str) for item in argv):
            raise ValueError("argv must be a nonempty list of strings; shell execution is unsupported")
        if not 0 < poll_seconds <= 1 or not 0 <= terminate_grace_seconds <= 10:
            raise ValueError("Invalid bounded sampling/grace interval")
        self.argv, self.cwd, self.budget = list(argv), _no_symlinks(Path(cwd)), budget
        if not self.cwd.is_dir():
            raise ValueError("cwd must exist")
        self.cancel_check = cancel_check or (lambda: False)
        self.poll_seconds, self.grace = poll_seconds, terminate_grace_seconds
        self.stdout_path, self.stderr_path, self.env, self.disk_root = stdout_path, stderr_path, env, disk_root
        self.process = None
        self._owned = {}
        self._samples = []
        self._sample_count = 0
        self._peak = 0
        self._ran = False
        if timeout_seconds is not None and (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and positive")
        self.timeout_seconds = timeout_seconds

    def _observe(self):
        table = _proc_table()
        if self.process is None:
            return [], 0
        pid = self.process.pid
        live_owned = {key for key, start in self._owned.items()
                      if key in table and table[key]["start_ticks"] == start}
        if pid in table and (pid not in self._owned or self._owned[pid] == table[pid]["start_ticks"]):
            live_owned.add(pid)
        # Descendants and members of the session created by this Popen are owned.
        changed = True
        while changed:
            before = len(live_owned)
            for key, item in table.items():
                if item["ppid"] in live_owned or (item["session_id"] == pid and item["pgid"] == pid and live_owned):
                    live_owned.add(key)
            changed = len(live_owned) != before
        for key in live_owned:
            self._owned[key] = table[key]["start_ticks"]
        observations = [table[key] for key in sorted(live_owned)]
        alive = [item for item in observations if item["state"] not in {"Z", "X"}]
        rss = sum(item["rss_bytes"] for item in alive)
        self._peak = max(self._peak, rss)
        sample = {"monotonic": time.monotonic(), "tree_rss_bytes": rss, "processes": observations}
        # Keep bounded detailed samples plus an accurate sampled peak.
        self._sample_count += 1
        if len(self._samples) < 2048:
            self._samples.append(sample)
        return alive, rss

    def _signal_owned(self, sig):
        """pidfds bind identity across signaling; never use a PID from disk."""
        alive, _ = self._observe()
        signaled = []
        for item in alive:
            pidfd = None
            try:
                if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                    raise ReliabilityError("PROCESS_SIGNAL_UNSUPPORTED", "Identity-bound pidfd signaling is required")
                pidfd = os.pidfd_open(item["pid"])
                # PID could have exited/reused between observation and pidfd_open.
                now = _proc_table().get(item["pid"])
                if now is None or now["start_ticks"] != item["start_ticks"]:
                    continue
                signal.pidfd_send_signal(pidfd, sig)
                signaled.append({"pid": item["pid"], "start_ticks": item["start_ticks"], "signal": int(sig)})
            except ProcessLookupError:
                continue
            finally:
                if pidfd is not None:
                    os.close(pidfd)
        return signaled

    def run(self):
        if self._ran:
            raise ReliabilityError("PROCESS_ALREADY_RUN", "An OwnedProcess instance cannot be run twice")
        self._ran = True
        self.budget.check()
        if self.cancel_check():
            return {"status": "cancelled", "started": False, "returncode": None,
                    "cancel_requested": True, "worker_exit_observed": True, "cleanup_review_required": False,
                    "process_evidence": [], "budget": self.budget.report()}
        if os.name != "posix" or not Path("/proc").is_dir() or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise ReliabilityError("PROCESS_SIGNAL_UNSUPPORTED", "Linux procfs and pidfd support are required")
        handles = []
        def output(path):
            if path is None:
                return subprocess.DEVNULL
            path = _no_symlinks(Path(path))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            handle = os.fdopen(fd, "wb")
            handles.append(handle)
            return handle
        reason, error, signals, cleanup = None, None, [], False
        stage_started_at = time.monotonic()
        ready_buffer, watchdog_ready = b"", None
        parent_identity = _proc_table().get(os.getpid())
        if parent_identity is None:
            raise ReliabilityError("PROCESS_OBSERVATION_UNAVAILABLE", "Cannot bind supervisor identity")
        wrapper = Path(__file__).with_name("process_watchdog.py")
        if not wrapper.is_file():
            raise ReliabilityError("PROCESS_WATCHDOG_UNAVAILABLE", "Owned parent-death watchdog is missing")
        ready_read, ready_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        launch = [sys.executable, "-I", "-S", str(wrapper),
                  "--parent-pid", str(os.getpid()),
                  "--parent-start-ticks", str(parent_identity["start_ticks"]),
                  "--ready-fd", str(ready_write), "--", *self.argv]
        try:
            self.process = subprocess.Popen(launch, cwd=self.cwd, env=self.env,
                                            stdout=output(self.stdout_path), stderr=output(self.stderr_path),
                                            stdin=subprocess.DEVNULL, shell=False, start_new_session=True,
                                            pass_fds=(ready_write,))
            os.close(ready_write)
            ready_write = None
            self._observe()
            while True:
                if watchdog_ready is None:
                    try:
                        ready_buffer += os.read(ready_read, 4096)
                    except BlockingIOError:
                        pass
                    if len(ready_buffer) > 4096:
                        raise ReliabilityError("PROCESS_WATCHDOG_UNAVAILABLE", "Unbounded watchdog readiness reply")
                    if b"\n" in ready_buffer:
                        try:
                            watchdog_ready = json.loads(ready_buffer)
                        except (ValueError, TypeError) as exc:
                            raise ReliabilityError("PROCESS_WATCHDOG_UNAVAILABLE", "Invalid watchdog readiness reply") from exc
                        expected_ready = {"version": 1, "status": "ready", "watchdog_pid": self.process.pid,
                                          "process_group_id": self.process.pid, "parent_pid": os.getpid(),
                                          "parent_start_ticks": parent_identity["start_ticks"], "mechanism": "linux_parent_pidfd"}
                        if watchdog_ready != expected_ready:
                            raise ReliabilityError("PROCESS_WATCHDOG_UNAVAILABLE", "Watchdog readiness identity mismatch")
                alive, rss = self._observe()
                returncode = self.process.poll()
                if self.cancel_check():
                    reason = "cancelled"
                    break
                if self.timeout_seconds is not None and time.monotonic() - stage_started_at >= self.timeout_seconds:
                    reason = "timed_out"
                    error = BudgetExceeded("stage_wall_seconds", time.monotonic() - stage_started_at, self.timeout_seconds).as_dict()
                    break
                try:
                    disk = tree_file_bytes(self.disk_root) if self.disk_root is not None else None
                    self.budget.check(tree_rss_bytes=rss, disk_bytes=disk, available_ram=available_ram_bytes())
                except BudgetExceeded as exc:
                    reason = "timed_out" if exc.resource == "wall_seconds" else "failed"
                    error = exc.as_dict()
                    break
                if not alive and returncode is not None:
                    if watchdog_ready is None:
                        reason = "failed"
                        error = {"code": "VALIDATION_FAILED", "detail_code": "PROCESS_WATCHDOG_UNAVAILABLE",
                                 "message": "Parent-death watchdog exited without verified readiness"}
                    break
                time.sleep(min(self.poll_seconds, self.budget.remaining_seconds))
            if reason is not None:
                signals.extend(self._signal_owned(signal.SIGTERM))
                deadline = time.monotonic() + self.grace
                while time.monotonic() < deadline:
                    alive, _ = self._observe()
                    if not alive:
                        break
                    time.sleep(self.poll_seconds)
                alive, _ = self._observe()
                if alive:
                    signals.extend(self._signal_owned(signal.SIGKILL))
                try:
                    self.process.wait(timeout=max(1.0, self.grace))
                except subprocess.TimeoutExpired:
                    cleanup = True
                deadline = time.monotonic() + max(1.0, self.grace)
                while time.monotonic() < deadline:
                    alive, _ = self._observe()
                    if not alive:
                        break
                    time.sleep(self.poll_seconds)
                cleanup = cleanup or bool(alive)
            else:
                self.process.wait()
                reason = "succeeded" if self.process.returncode == 0 else "failed"
            return {"status": reason, "started": True, "returncode": self.process.returncode,
                    "cancel_requested": reason == "cancelled", "cancelled_at": utc_now() if reason == "cancelled" else None,
                    "error": error, "worker_exit_observed": self.process.returncode is not None,
                    "observed_descendants_terminal": not cleanup,
                    "cleanup_review_required": cleanup, "signals": signals,
                    "observed_peak_tree_rss_bytes": self._peak, "process_evidence": self._samples,
                    "process_sampling_interval_seconds": self.poll_seconds,
                    "process_sample_count": self._sample_count,
                    "process_samples_omitted": max(0, self._sample_count - len(self._samples)),
                    "unobserved_escaped_descendants_cannot_be_excluded": True,
                    "parent_death_watchdog": {"ready": watchdog_ready is not None, "identity": watchdog_ready,
                                               "scope": "trusted_processes_remaining_in_owned_session_group",
                                               "watchdog_sigkill_or_group_escape_is_not_contained": True},
                    "hard_memory_enforcement": False, "budget": self.budget.report()}
        except BaseException as exc:
            if self.process is not None:
                # Supervisor errors still make a best effort to stop only owned
                # children; inability to prove termination is surfaced to caller.
                cleanup_evidence = {"cleanup_review_required": True, "worker_exit_observed": False}
                try:
                    cleanup_evidence["signals"] = self._signal_owned(signal.SIGKILL)
                    self.process.wait(timeout=1)
                    remaining, _ = self._observe()
                    cleanup_evidence.update(worker_exit_observed=True,
                                            cleanup_review_required=bool(remaining),
                                            observed_remaining=remaining)
                except Exception as cleanup_error:
                    cleanup_evidence["cleanup_error"] = str(cleanup_error)
                details = dict(getattr(exc, "details", {}))
                details["process_cleanup"] = cleanup_evidence
                exc.details = details
            raise
        finally:
            os.close(ready_read)
            if ready_write is not None:
                os.close(ready_write)
            for handle in handles:
                handle.close()
