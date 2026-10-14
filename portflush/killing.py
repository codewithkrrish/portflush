"""Graceful process termination.

Default flow per process: SIGTERM, wait up to ``timeout`` seconds for the
process to exit, then SIGKILL if it is still alive. ``--force`` skips
straight to SIGKILL. On Windows the equivalents are ``taskkill`` without
and with ``/F``. ``kill_func`` / ``is_alive`` are injectable so tests can
drive the escalation logic without touching real processes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

from .errors import KillError
from .models import Listener

#: Default seconds to wait after SIGTERM before escalating to SIGKILL.
DEFAULT_TIMEOUT_SECONDS = 3.0
#: Poll interval while waiting for a process to exit.
POLL_INTERVAL_SECONDS = 0.1


@dataclass
class KillResult:
    """Outcome for one listener."""

    listener: Listener
    killed: bool = False
    escalated: bool = False  # SIGKILL was needed after SIGTERM
    skipped: bool = False  # e.g. own PID, or no PID known
    reason: str = ""


@dataclass
class KillSummary:
    """Aggregate outcome for a kill run."""

    results: list[KillResult] = field(default_factory=list)

    @property
    def killed(self) -> list[KillResult]:
        return [r for r in self.results if r.killed]

    @property
    def failed(self) -> list[KillResult]:
        return [r for r in self.results if not r.killed and not r.skipped]


def _default_is_alive(pid: int) -> bool:
    """True while the process still exists (zombies count as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _default_send(pid: int, sig: int) -> None:
    if os.name == "nt":
        args = ["taskkill", "/PID", str(pid)]
        if sig in (signal.SIGKILL, signal.SIGTERM):
            # taskkill without /F is the graceful ask; /F forces it.
            if sig == signal.SIGKILL:
                args.append("/F")
        subprocess.run(args, capture_output=True, check=False)
        return
    os.kill(pid, sig)


def _graceful_signal() -> int:
    return signal.SIGTERM


def _force_signal() -> int:
    return signal.SIGKILL


def kill_listeners(
    listeners: list[Listener],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    force: bool = False,
    dry_run: bool = False,
    kill_func=None,
    is_alive=None,
    sleep_func=time.sleep,
) -> KillSummary:
    """Terminate every listener, gracefully unless ``force`` is set.

    Own-PID listeners are never killed: they are reported as skipped.
    With ``dry_run`` nothing is signalled and every killable listener is
    reported as killed (what *would* happen).
    """
    send = kill_func or _default_send
    alive = is_alive or _default_is_alive
    summary = KillSummary()
    own_pid = os.getpid()

    for listener in listeners:
        if listener.pid is None:
            summary.results.append(
                KillResult(
                    listener=listener, skipped=True,
                    reason="unknown PID — cannot kill safely",
                )
            )
            continue
        if listener.pid == own_pid:
            summary.results.append(
                KillResult(
                    listener=listener, skipped=True,
                    reason="refusing to kill portflush itself",
                )
            )
            continue
        if dry_run:
            summary.results.append(KillResult(listener=listener, killed=True))
            continue
        try:
            if force:
                send(listener.pid, _force_signal())
                summary.results.append(
                    KillResult(listener=listener, killed=True, escalated=True)
                )
                continue
            send(listener.pid, _graceful_signal())
            deadline = time.monotonic() + timeout
            escalated = False
            while alive(listener.pid):
                if time.monotonic() >= deadline:
                    send(listener.pid, _force_signal())
                    escalated = True
                    break
                sleep_func(POLL_INTERVAL_SECONDS)
            if alive(listener.pid) and escalated:
                # Give SIGKILL a brief moment to land.
                sleep_func(POLL_INTERVAL_SECONDS)
                if alive(listener.pid):
                    raise KillError(
                        f"PID {listener.pid} did not exit after SIGKILL"
                    )
            summary.results.append(
                KillResult(listener=listener, killed=True, escalated=escalated)
            )
        except PermissionError:
            summary.results.append(
                KillResult(
                    listener=listener,
                    reason=f"permission denied killing PID {listener.pid} "
                    "(try running as admin / with sudo)",
                )
            )
        except ProcessLookupError:
            # Process vanished between discovery and kill: port is free.
            summary.results.append(KillResult(listener=listener, killed=True))
        except KillError as exc:
            summary.results.append(
                KillResult(listener=listener, reason=str(exc))
            )
        except OSError as exc:
            summary.results.append(
                KillResult(listener=listener, reason=str(exc))
            )
    return summary
