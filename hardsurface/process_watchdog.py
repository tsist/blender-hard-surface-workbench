# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-stop parent-death guard for a trusted Linux owned process group.

This file is launched by OwnedProcess using isolated Python in a new session.
It is deliberately standalone: no package imports, preexec_fn, prctl, global
configuration, or external dependencies. It does not contain escaping processes
or survive its own SIGKILL; those remain outside this trusted-process contract.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

VERSION = 1
POLL_MILLISECONDS = 20
FAILURE_EXIT = 125


class WatchdogError(RuntimeError):
    pass


def process_start_ticks(pid):
    raw = Path("/proc", str(pid), "stat").read_text(encoding="ascii")
    fields = raw[raw.rfind(")") + 2:].split()
    return int(fields[19])


def parent_pidfd(expected_parent, expected_start):
    """Bind the actual parent, rejecting death/reparenting/reuse startup races."""
    if sys.platform != "linux" or not hasattr(os, "pidfd_open") or not hasattr(select, "poll"):
        raise WatchdogError("Linux pidfd and poll support are required")
    if expected_parent <= 1 or expected_start <= 0 or os.getppid() != expected_parent:
        raise WatchdogError("Expected supervisor is no longer this watchdog's parent")
    if process_start_ticks(expected_parent) != expected_start:
        raise WatchdogError("Supervisor process identity changed before pidfd open")
    fd = os.pidfd_open(expected_parent, 0)
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        if (os.getppid() != expected_parent or poller.poll(0) or
            process_start_ticks(expected_parent) != expected_start):
            raise WatchdogError("Supervisor died or changed while binding parent pidfd")
        return fd, poller
    except BaseException:
        os.close(fd)
        raise


def own_group_members(group_id):
    """Read only this new group's live members; zombies are already terminated."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii")
            fields = raw[raw.rfind(")") + 2:].split()
            pid, pgid, sid = int(entry.name), int(fields[2]), int(fields[3])
            if pid != group_id and pgid == group_id and sid == group_id and fields[0] not in {"Z", "X"}:
                members.append(pid)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError) as exc:
            raise WatchdogError("Cannot establish terminal state of owned process group") from exc
    return members


def kill_own_group():
    """The wrapper remains group leader, so this ID cannot refer to another group."""
    own = os.getpid()
    if os.getpgrp() != own or os.getsid(0) != own:
        raise WatchdogError("Refusing to signal a group not created for this watchdog")
    os.killpg(own, signal.SIGKILL)
    # SIGKILL should terminate this process before this statement can execute.
    os._exit(FAILURE_EXIT)


def mirror_returncode(returncode):
    if returncode >= 0:
        return returncode
    sig = -returncode
    if sig not in {signal.SIGKILL, signal.SIGSTOP}:
        signal.signal(sig, signal.SIG_DFL)
    os.kill(os.getpid(), sig)
    os._exit(128 + sig)


def run(argv, *, expected_parent, expected_parent_start, ready_fd):
    if not argv or any(not isinstance(arg, str) for arg in argv):
        raise WatchdogError("Nonempty argv is required; shell execution is unsupported")
    own = os.getpid()
    if os.getpgrp() != own or os.getsid(0) != own:
        raise WatchdogError("Watchdog must be the leader of its own new session and group")
    fd, poller = parent_pidfd(expected_parent, expected_parent_start)
    child = None
    try:
        ready = {"version": VERSION, "status": "ready", "watchdog_pid": own,
                 "process_group_id": own, "parent_pid": expected_parent,
                 "parent_start_ticks": expected_parent_start, "mechanism": "linux_parent_pidfd"}
        raw = json.dumps(ready, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        if os.write(ready_fd, raw) != len(raw):
            raise WatchdogError("Incomplete readiness handshake")
        os.close(ready_fd)
        ready_fd = None
        # If death precedes launch, no requested process is started. If it races
        # Popen, the already-bound pidfd becomes ready and the new group is killed.
        if poller.poll(0) or os.getppid() != expected_parent:
            kill_own_group()
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, shell=False,
                                 close_fds=True, start_new_session=False)
        while True:
            if poller.poll(POLL_MILLISECONDS):
                kill_own_group()
            result = child.poll()
            if result is not None and not own_group_members(own):
                # Final death check closes the normal-completion acceptance race.
                if poller.poll(0):
                    kill_own_group()
                return mirror_returncode(result)
    except BaseException:
        if child is not None:
            # Any watchdog logic failure after launch is fail-stop for this own
            # group, including wrapper and still-running descendants.
            kill_own_group()
        raise
    finally:
        if ready_fd is not None:
            os.close(ready_fd)
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Internal owned process parent-death watchdog")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--parent-start-ticks", type=int, required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return run(command, expected_parent=args.parent_pid,
                   expected_parent_start=args.parent_start_ticks, ready_fd=args.ready_fd)
    except (OSError, ValueError, WatchdogError) as exc:
        print("hardsurface watchdog refused: " + str(exc), file=sys.stderr, flush=True)
        return FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
