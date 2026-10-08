"""Bounded planning and sampled runtime budgets (standard library only).

These are observation/abort budgets, not OS memory isolation. The caller supplies
its monotonic CLI-entry timestamp so staging, queueing and validation count too.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, fields
import math
import os
from pathlib import Path
import shutil
import stat
import time
from typing import Callable, Mapping, Sequence


class BudgetExceeded(RuntimeError):
    code = "RESOURCE_LIMIT"
    detail_code = "BUDGET_EXCEEDED"

    def __init__(self, resource: str, observed, limit):
        self.resource, self.observed, self.limit = resource, observed, limit
        self.details = {"resource": resource, "observed": observed, "limit": limit,
                        "retry_class": "new_budget_required"}
        super().__init__(f"{resource} budget exceeded: {observed!r} > {limit!r}")

    def as_dict(self):
        return {"code": self.code, "detail_code": self.detail_code, **self.details}


@dataclass(frozen=True)
class BudgetLimits:
    wall_seconds: float = 300.0
    max_work_units: int = 8
    max_steps: int = 128
    max_steps_per_unit: int = 64
    max_cases: int = 4
    max_attempts: int = 3
    max_instances: int = 1024
    max_vertices: int = 200_000
    max_loops: int = 1_000_000
    max_views: int = 16
    max_render_pixels: int = 16 * 2048 * 2048
    max_curve_segments: int = 100_000
    max_disk_bytes: int = 1024 * 1024 * 1024
    max_tree_rss_bytes: int | None = None
    minimum_available_ram_bytes: int | None = None
    disk_reserve_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None and field.name in {"max_tree_rss_bytes", "minimum_available_ram_bytes"}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{field.name} must be finite and nonnegative")
            if field.name != "wall_seconds" and not isinstance(value, int):
                raise ValueError(f"{field.name} must be an integer")
        if self.wall_seconds <= 0:
            raise ValueError("wall_seconds must be positive")

    @classmethod
    def from_mapping(cls, values: Mapping):
        names = {item.name for item in fields(cls)}
        unknown = set(values) - names
        if unknown:
            raise ValueError(f"Unknown budget fields: {sorted(unknown)}")
        return cls(**values)


def checked_product(factors: Sequence[int], limit: int, resource="expanded_count") -> int:
    """Check expansion without constructing it; integer arithmetic cannot overflow."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    result = 1
    for value in factors:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("expansion factors must be nonnegative integers")
        result *= value
        if result > limit:
            raise BudgetExceeded(resource, result, limit)
    if result > limit:
        raise BudgetExceeded(resource, result, limit)
    return result


def estimate_arc_segments(radius: float, sweep_radians: float, chord_error: float,
                          max_segments: int = 100_000) -> int:
    """Sagitta-bounded segment count, before point allocation, for at most one turn."""
    for name, value in (("radius", radius), ("sweep", sweep_radians), ("chord_error", chord_error)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if radius <= 0 or chord_error <= 0 or not 0 < abs(sweep_radians) <= math.tau:
        raise ValueError("Require radius/error > 0 and 0 < abs(sweep) <= 2*pi")
    if not isinstance(max_segments, int) or isinstance(max_segments, bool) or max_segments < 1:
        raise ValueError("max_segments must be a positive integer")
    # asin form avoids cancellation from acos(1 - error/radius) for tiny errors.
    ratio = (chord_error / radius) / 2
    if ratio <= 0:
        raise BudgetExceeded("curve_segments", "beyond_float_resolution", max_segments)
    angle = 4.0 * math.asin(math.sqrt(min(1.0, ratio)))
    if angle == 0 or abs(sweep_radians) / angle > max_segments:
        raise BudgetExceeded("curve_segments", "greater_than_limit", max_segments)
    count = max(1, math.ceil(abs(sweep_radians) / angle))
    if count > max_segments:
        raise BudgetExceeded("curve_segments", count, max_segments)
    return count


def available_ram_bytes() -> int | None:
    """Linux MemAvailable is a system observation, not a process entitlement."""
    try:
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def tree_file_bytes(root: os.PathLike | str, *, max_files=20_000,
                    max_entries=40_000) -> int:
    """Sample regular-file lengths without following links or pathname swaps.

    Regular files may vanish or be atomically replaced during a live sample.
    Such samples are not snapshots; a quiescent tree has an exact byte total.
    Directory identity/traversal failures and non-regular files fail closed.
    Both enumerated entries (including vanished names) and files are bounded.
    """
    for name, limit in (("max_files", max_files), ("max_entries", max_entries)):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise NotImplementedError("Disk accounting requires no-follow directory handles")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    total = files = entries = 0

    def identity(info):
        return info.st_dev, info.st_ino

    def directory_stat(name, parent_fd):
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Non-directory or symlink in disk accounting path: {name}")
        return info

    def verify_directory(name, parent_fd, initial):
        if identity(directory_stat(name, parent_fd)) != identity(initial):
            raise ValueError(f"Directory changed during disk accounting: {name}")

    with ExitStack() as handles:
        # Pin each root component, including its ancestors. O_NOFOLLOW applies
        # to one component at a time, not just the final pathname component.
        parent_fd = None
        anchors = []
        for name in Path(root).absolute().parts:
            initial = directory_stat(name, parent_fd)
            fd = os.open(name, flags, dir_fd=parent_fd)
            handles.callback(os.close, fd)
            if identity(os.fstat(fd)) != identity(initial):
                raise ValueError(f"Directory changed during disk accounting: {name}")
            anchors.append((name, parent_fd, initial))
            parent_fd = fd

        # Iterative traversal avoids recursion and unbounded directory lists.
        stack = [(parent_fd, os.scandir(parent_fd), None)]
        try:
            while stack:
                fd, iterator, guard = stack[-1]
                entry = next(iterator, None)
                if entry is None:
                    if guard is not None:
                        verify_directory(*guard)
                    iterator.close()
                    if guard is not None:
                        os.close(fd)
                    stack.pop()
                    continue
                entries += 1
                if entries > max_entries:
                    raise BudgetExceeded("disk_accounting_entries", entries, max_entries)
                was_regular = entry.is_file(follow_symlinks=False)
                was_directory = entry.is_dir(follow_symlinks=False)
                if not (was_regular or was_directory):
                    raise ValueError(f"Symlink, special or unclassified file in disk accounting root: {entry.name}")
                try:
                    info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    # Atomic report replacement can remove an enumerated temp
                    # filename. Directory disappearance still fails closed.
                    if not was_regular:
                        raise
                    files += 1
                else:
                    if was_directory and (not stat.S_ISDIR(info.st_mode) or
                                          info.st_ino != entry.inode()):
                        raise ValueError(f"Directory changed during disk accounting: {entry.name}")
                    if stat.S_ISDIR(info.st_mode):
                        if not was_directory:
                            raise ValueError(f"File became directory during disk accounting: {entry.name}")
                        child = os.open(entry.name, flags, dir_fd=fd)
                        try:
                            if identity(os.fstat(child)) != identity(info):
                                raise ValueError(f"Directory changed during disk accounting: {entry.name}")
                            child_iterator = os.scandir(child)
                        except BaseException:
                            os.close(child)
                            raise
                        stack.append((child, child_iterator, (entry.name, fd, info)))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError(f"Symlink or special file in disk accounting root: {entry.name}")
                    files += 1
                    total += info.st_size
                if files > max_files:
                    raise BudgetExceeded("disk_accounting_files", files, max_files)
            for anchor in reversed(anchors):
                verify_directory(*anchor)
        finally:
            for fd, iterator, guard in reversed(stack):
                iterator.close()
                if guard is not None:
                    os.close(fd)
    return total


class JobBudget:
    def __init__(self, limits: BudgetLimits | Mapping, *, started_at: float,
                 clock: Callable[[], float] = time.monotonic):
        self.limits = limits if isinstance(limits, BudgetLimits) else BudgetLimits.from_mapping(limits)
        now = clock()
        if isinstance(started_at, bool) or not isinstance(started_at, (int, float)) or not math.isfinite(started_at) or started_at > now:
            raise ValueError("started_at must be a past/current monotonic CLI-entry timestamp")
        self.started_at, self.clock = started_at, clock
        self.peak_tree_rss_bytes = 0
        self.peak_disk_bytes = 0
        self.samples = 0

    @property
    def elapsed_seconds(self):
        return max(0.0, self.clock() - self.started_at)

    @property
    def remaining_seconds(self):
        return max(0.0, self.limits.wall_seconds - self.elapsed_seconds)

    def check(self, *, tree_rss_bytes: int | None = None, disk_bytes: int | None = None,
              available_ram: int | None = None):
        elapsed = self.elapsed_seconds
        if elapsed >= self.limits.wall_seconds:
            raise BudgetExceeded("wall_seconds", elapsed, self.limits.wall_seconds)
        for name, observed in (("tree_rss_bytes", tree_rss_bytes), ("disk_bytes", disk_bytes)):
            if observed is None:
                continue
            if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
                raise ValueError(f"{name} observation must be a nonnegative integer")
            setattr(self, "peak_" + name, max(getattr(self, "peak_" + name), observed))
            limit = getattr(self.limits, "max_" + name)
            if limit is not None and observed > limit:
                raise BudgetExceeded(name, observed, limit)
        if available_ram is not None and self.limits.minimum_available_ram_bytes is not None:
            if available_ram < self.limits.minimum_available_ram_bytes:
                raise BudgetExceeded("available_ram_shortfall_bytes", self.limits.minimum_available_ram_bytes - available_ram, 0)
        self.samples += 1
        return self.report()

    def preflight(self, estimates: Mapping, *, disk_root: os.PathLike | str | None = None):
        """Caller must count fully expanded cases/attempts/diagnosis and dependencies."""
        self.check()
        mapping = {"work_units": "max_work_units", "steps": "max_steps", "cases": "max_cases",
                   "attempts": "max_attempts", "instances": "max_instances", "vertices": "max_vertices",
                   "loops": "max_loops", "views": "max_views", "render_pixels": "max_render_pixels",
                   "curve_segments": "max_curve_segments"}
        permitted = set(mapping) | {"steps_per_unit", "input_bytes", "output_bytes", "checkpoint_bytes"}
        if set(estimates) - permitted:
            raise ValueError(f"Unknown estimates: {sorted(set(estimates) - permitted)}")
        for key, value in estimates.items():
            values = value if key == "steps_per_unit" else [value]
            if not isinstance(values, (list, tuple)):
                raise ValueError("steps_per_unit must be an array of counts")
            for amount in values:
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise ValueError(f"{key} must be nonnegative integer counts")
                attr = "max_steps_per_unit" if key == "steps_per_unit" else mapping.get(key)
                if attr and amount > getattr(self.limits, attr):
                    raise BudgetExceeded(key, amount, getattr(self.limits, attr))
        planned = sum(estimates.get(key, 0) for key in ("input_bytes", "output_bytes", "checkpoint_bytes"))
        if planned > self.limits.max_disk_bytes:
            raise BudgetExceeded("planned_disk_bytes", planned, self.limits.max_disk_bytes)
        free = None
        if disk_root is not None:
            free = shutil.disk_usage(disk_root).free
            required = planned + self.limits.disk_reserve_bytes
            if required > free:
                raise BudgetExceeded("disk_space_required_bytes", required, free)
        return {"estimated": dict(estimates), "planned_disk_bytes": planned,
                "disk_free_bytes_observed": free, "estimate_is_worst_case_guarantee": False,
                **self.report()}

    def report(self):
        return {"wall_seconds_observed": self.elapsed_seconds,
                "wall_seconds_limit": self.limits.wall_seconds,
                "wall_includes_cli_preflight_and_queue": True,
                "peak_tree_rss_bytes_observed": self.peak_tree_rss_bytes,
                "peak_disk_bytes_observed": self.peak_disk_bytes,
                "samples": self.samples, "hard_memory_enforcement": False,
                "rss_shared_pages_may_be_counted_more_than_once": True,
                "sampling_may_miss_transient_peaks": True,
                "tmpfs_is_system_ram_not_additive_exact_rss": True}
