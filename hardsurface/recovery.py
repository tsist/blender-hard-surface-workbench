"""Exact-match accepted checkpoint receipts; in-memory success is never resumable.

The geometry host is responsible for actually performing independent reopen and
checks. This module validates and preserves its explicit evidence, all artifact
SHA/byte identities and every required fingerprint group before any reuse.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping

from .jobs import ReliabilityError, _atomic_json, _no_symlinks, _plain_json, _read_json, utc_now

REQUIRED_FINGERPRINTS = frozenset({
    "source", "resources", "request", "plan", "operation_versions", "implementation",
    "solver", "reference", "design", "context", "targets", "protection",
})
REQUIRED_CHECKS = frozenset({"technical", "preservation", "dependency_reproduction", "state_consistency"})


def fingerprint_digest(fingerprints: Mapping) -> str:
    if not isinstance(fingerprints, Mapping) or set(fingerprints) != REQUIRED_FINGERPRINTS:
        missing = REQUIRED_FINGERPRINTS - set(fingerprints) if isinstance(fingerprints, Mapping) else REQUIRED_FINGERPRINTS
        extra = set(fingerprints) - REQUIRED_FINGERPRINTS if isinstance(fingerprints, Mapping) else set()
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "All exact fingerprint groups must be explicit", missing=sorted(missing), extra=sorted(extra))
    if any(value is None for value in fingerprints.values()):
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Unknown fingerprints cannot authorize reuse")
    try:
        return hashlib.sha256(_plain_json(fingerprints)).hexdigest()
    except (ValueError, TypeError) as exc:
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Fingerprint groups must be finite JSON") from exc


def artifact_descriptor(path, *, root=None) -> dict:
    path = _no_symlinks(Path(path))
    if root is not None and path != Path(root) and Path(root) not in path.parents:
        raise ReliabilityError("PATH_NOT_OWNED", "Checkpoint artifact is outside the owned job", path=str(path))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("artifact is not a regular file")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno())
        final = path.stat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(final) or size != after.st_size:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Artifact changed while fingerprinting", path=str(path))
        return {"file": str(path), "sha256": digest.hexdigest(), "bytes": size}
    except (OSError, ValueError) as exc:
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Cannot verify checkpoint artifact", path=str(path)) from exc


def verify_descriptor(descriptor: Mapping, *, root=None) -> dict:
    if not isinstance(descriptor, Mapping) or set(descriptor) != {"file", "sha256", "bytes"}:
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Artifact descriptor must contain exact file/SHA/bytes")
    if (not isinstance(descriptor["file"], str) or not descriptor["file"] or
        not isinstance(descriptor["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"])):
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Invalid artifact path or SHA-256")
    if not isinstance(descriptor["bytes"], int) or isinstance(descriptor["bytes"], bool) or descriptor["bytes"] < 0:
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Invalid artifact byte length")
    actual = artifact_descriptor(descriptor["file"], root=root)
    if actual != descriptor:
        raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Artifact SHA/bytes/path mismatch", expected=dict(descriptor), observed=actual)
    return actual


class CheckpointStore:
    def __init__(self, job_dir):
        self.job_dir = _no_symlinks(Path(job_dir))
        identity = _read_json(self.job_dir / "job.json")
        owner = _read_json(self.job_dir.parent.parent / "owner.json")
        if (self.job_dir.parent.name != "jobs" or identity.get("job_id") != self.job_dir.name or
            not re.fullmatch(r"[0-9a-f]{32}", self.job_dir.name) or
            owner != {"kind": "hardsurface_job_store", "version": 1,
                      "root": str(self.job_dir.parent.parent), "uid": os.geteuid()}):
            raise ReliabilityError("PATH_NOT_OWNED", "Checkpoint root is not an owned JobStore job")
        self.job_id = identity["job_id"]
        self.root = self.job_dir / "checkpoints"
        _no_symlinks(self.root)
        self.root.mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
            raise ValueError("checkpoint_id must be a bounded ASCII identifier")
        return value

    @contextmanager
    def _lock(self):
        fd = os.open(self.root / "accept.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _receipts(self):
        paths = sorted(self.root.glob("accepted-*.json"))
        if len(paths) > 256:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Checkpoint receipt scan bound exceeded")
        result = []
        sequences = set()
        for path in paths:
            try:
                receipt = _read_json(path)
            except ReliabilityError as exc:
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Malformed accepted checkpoint receipt", path=str(path)) from exc
            required = {"version", "job_id", "checkpoint_id", "state", "sequence", "accepted_at",
                        "fingerprints", "fingerprints_sha256", "artifacts", "verification", "completed_steps"}
            sequence = receipt.get("sequence")
            if (set(receipt) != required or receipt.get("version") != 1 or receipt.get("job_id") != self.job_id or
                receipt.get("state") != "accepted_checkpoint" or not isinstance(sequence, int) or isinstance(sequence, bool) or
                sequence < 1 or sequence in sequences or path.name != "accepted-" + self._id(receipt.get("checkpoint_id")) + ".json"):
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Accepted checkpoint metadata is inconsistent", path=str(path))
            commit_path = self.root / ("committed-" + receipt["checkpoint_id"] + ".json")
            try:
                commit = _read_json(commit_path)
            except ReliabilityError as exc:
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Checkpoint receipt lacks completed acceptance marker", path=str(path)) from exc
            if (set(commit) != {"version", "job_id", "checkpoint_id", "sequence", "receipt"} or
                commit.get("version") != 1 or commit.get("job_id") != self.job_id or
                commit.get("checkpoint_id") != receipt["checkpoint_id"] or commit.get("sequence") != sequence):
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Checkpoint acceptance marker mismatch")
            verify_descriptor(commit["receipt"], root=self.job_dir)
            if commit["receipt"]["file"] != str(path):
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Acceptance marker binds a different receipt")
            sequences.add(sequence)
            result.append((receipt, path))
        commits = list(self.root.glob("committed-*.json"))
        if len(commits) != len(result):
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Missing accepted receipt or unmatched acceptance marker")
        result.sort(key=lambda pair: pair[0]["sequence"])
        if sequences != set(range(1, len(result) + 1)):
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Accepted checkpoint sequence has missing records")
        return result

    def _verification(self, verification, candidate):
        required = {"independent_reopen", "candidate", "checks", "evidence"}
        if not isinstance(verification, Mapping) or set(verification) != required:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Independent reopen verification is required")
        checks = verification["checks"]
        if verification["independent_reopen"] is not True or verification["candidate"] != candidate:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Reopen evidence does not bind this candidate")
        if not isinstance(checks, Mapping) or not REQUIRED_CHECKS <= set(checks) or any(checks[key] != "pass" for key in REQUIRED_CHECKS):
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Required reopen checks did not all pass")
        verify_descriptor(verification["evidence"], root=self.job_dir)
        try:
            evidence = _read_json(verification["evidence"]["file"])
        except ReliabilityError as exc:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Unreadable independent verification evidence") from exc
        # An evidence file binds the independent reader's observations, not just
        # a caller-set accepted flag. Producer/verifier are explicit identities.
        if (evidence.get("independent_reopen") is not True or evidence.get("candidate") != candidate or
            evidence.get("checks") != checks or not isinstance(evidence.get("producer_identity"), str) or
            not evidence["producer_identity"] or not isinstance(evidence.get("verifier_identity"), str) or
            not evidence["verifier_identity"] or evidence["producer_identity"] == evidence["verifier_identity"]):
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Independent verification evidence is incomplete or inconsistent")
        return dict(verification)

    def accept(self, checkpoint_id: str, artifacts: Mapping, fingerprints: Mapping,
               verification: Mapping, *, completed_steps=()):
        checkpoint_id = self._id(checkpoint_id)
        fingerprint_sha = fingerprint_digest(fingerprints)
        if not isinstance(artifacts, Mapping) or "candidate" not in artifacts or not 1 <= len(artifacts) <= 256:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Checkpoint requires bounded artifacts including candidate")
        descriptors = {}
        for role, value in artifacts.items():
            if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}", role):
                raise ValueError("Invalid artifact role")
            descriptors[role] = verify_descriptor(value, root=self.job_dir) if isinstance(value, Mapping) else artifact_descriptor(value, root=self.job_dir)
        verified = self._verification(verification, descriptors["candidate"])
        if (not isinstance(completed_steps, (list, tuple)) or len(completed_steps) > 128 or
            any(not isinstance(step, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}(?:/[A-Za-z][A-Za-z0-9_.:-]{0,95})?", step) for step in completed_steps) or
            len(set(completed_steps)) != len(completed_steps)):
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Completed steps must be bounded unique IDs")
        receipt = {"version": 1, "job_id": self.job_id, "checkpoint_id": checkpoint_id,
                   "state": "accepted_checkpoint", "accepted_at": utc_now(),
                   "fingerprints": dict(fingerprints), "fingerprints_sha256": fingerprint_sha,
                   "artifacts": descriptors, "verification": verified, "completed_steps": list(completed_steps)}
        with self._lock():
            receipts = self._receipts()
            receipt["sequence"] = len(receipts) + 1
            path = self.root / ("accepted-" + checkpoint_id + ".json")
            if path.exists():
                raise ReliabilityError("CHECKPOINT_ALREADY_EXISTS", "Accepted checkpoints are immutable", checkpoint_id=checkpoint_id)
            # Recheck artifacts at the commit boundary after any wait for a lock.
            for descriptor in descriptors.values():
                verify_descriptor(descriptor, root=self.job_dir)
            self._verification(verified, descriptors["candidate"])
            _atomic_json(path, receipt, exclusive=True)
            # Acceptance is complete only after this second immutable record binds
            # the complete receipt SHA/bytes. A crash between records fails closed.
            _atomic_json(self.root / ("committed-" + checkpoint_id + ".json"),
                         {"version": 1, "job_id": self.job_id, "checkpoint_id": checkpoint_id,
                          "sequence": receipt["sequence"], "receipt": artifact_descriptor(path, root=self.job_dir)}, exclusive=True)
        return {**receipt, "receipt": artifact_descriptor(path, root=self.job_dir)}

    def resume(self, fingerprints: Mapping, checkpoint_id: str | None = None, *, expected_receipt=None):
        expected = fingerprint_digest(fingerprints)
        receipts = self._receipts()
        if checkpoint_id is not None:
            self._id(checkpoint_id)
            receipts = [item for item in receipts if item[0]["checkpoint_id"] == checkpoint_id]
        if not receipts:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "No accepted checkpoint available")
        receipt, path = receipts[-1]
        if expected_receipt is not None:
            verify_descriptor(expected_receipt, root=self.job_dir)
            if expected_receipt["file"] != str(path):
                raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Receipt identity refers to a different checkpoint")
        stored = fingerprint_digest(receipt["fingerprints"])
        if stored != receipt["fingerprints_sha256"]:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Checkpoint fingerprint digest is inconsistent")
        if stored != expected:
            changed = sorted(key for key in REQUIRED_FINGERPRINTS if _plain_json(receipt["fingerprints"][key]) != _plain_json(fingerprints[key]))
            code = "RESUME_IMPLEMENTATION_MISMATCH" if set(changed) & {"implementation", "solver", "operation_versions"} else "RESUME_FINGERPRINT_MISMATCH"
            raise ReliabilityError(code, "Exact-match checkpoint reuse denied", changed_fingerprint_groups=changed, retry_class="new_input_required")
        artifacts = receipt["artifacts"]
        if not isinstance(artifacts, dict) or "candidate" not in artifacts or not 1 <= len(artifacts) <= 256:
            raise ReliabilityError("CHECKPOINT_INCOMPLETE", "Missing or unbounded checkpoint artifacts")
        for descriptor in artifacts.values():
            verify_descriptor(descriptor, root=self.job_dir)
        self._verification(receipt["verification"], artifacts["candidate"])
        return {**receipt, "receipt": artifact_descriptor(path, root=self.job_dir),
                "reused": True, "resume_kind": "exact_accepted_checkpoint",
                "step_status": {step: "reused_from_checkpoint" for step in receipt["completed_steps"]}}
