"""Tests for graceful kill escalation (fake kill/alive functions)."""

import os
import signal

from portflush.killing import kill_listeners
from portflush.models import Listener


def _listener(pid=1111, port=3000):
    return Listener(port=port, pid=pid, process="node")


def test_graceful_kill_no_escalation():
    sent: list[tuple[int, int]] = []
    alive_state = {1111: True}

    def fake_send(pid, sig):
        sent.append((pid, sig))
        if sig == signal.SIGTERM:
            alive_state[pid] = False

    summary = kill_listeners(
        [_listener()], kill_func=fake_send,
        is_alive=lambda pid: alive_state[pid], sleep_func=lambda _: None,
    )
    assert len(summary.killed) == 1
    assert summary.killed[0].escalated is False
    assert sent == [(1111, signal.SIGTERM)]


def test_escalates_to_sigkill_after_timeout():
    sent: list[tuple[int, int]] = []
    alive_state = {1111: True}

    def fake_send(pid, sig):
        sent.append((pid, sig))
        if sig == signal.SIGKILL:
            alive_state[pid] = False  # SIGKILL finally ends it

    # timeout=0: the deadline is already past on the first check.
    summary = kill_listeners(
        [_listener()], timeout=0.0, kill_func=fake_send,
        is_alive=lambda pid: alive_state[pid], sleep_func=lambda _: None,
    )
    assert sent[0][1] == signal.SIGTERM
    assert sent[-1][1] == signal.SIGKILL
    assert summary.killed[0].escalated is True


def test_force_goes_straight_to_sigkill():
    sent: list[tuple[int, int]] = []
    summary = kill_listeners(
        [_listener()], force=True,
        kill_func=lambda pid, sig: sent.append((pid, sig)),
        is_alive=lambda pid: False, sleep_func=lambda _: None,
    )
    assert sent == [(1111, signal.SIGKILL)]
    assert summary.killed[0].escalated is True


def test_dry_run_kills_nothing():
    sent: list = []
    summary = kill_listeners(
        [_listener()], dry_run=True,
        kill_func=lambda pid, sig: sent.append((pid, sig)),
        is_alive=lambda pid: True, sleep_func=lambda _: None,
    )
    assert sent == []
    assert len(summary.killed) == 1


def test_refuses_to_kill_own_pid():
    summary = kill_listeners(
        [_listener(pid=os.getpid())],
        kill_func=lambda pid, sig: (_ for _ in ()).throw(AssertionError("must not kill self")),
        is_alive=lambda pid: True, sleep_func=lambda _: None,
    )
    assert len(summary.killed) == 0
    assert summary.results[0].skipped is True


def test_unknown_pid_skipped():
    summary = kill_listeners(
        [Listener(port=3000, pid=None)],
        kill_func=lambda pid, sig: None,
        is_alive=lambda pid: True, sleep_func=lambda _: None,
    )
    assert summary.results[0].skipped is True


def test_permission_denied_reported_not_raised():
    def fake_send(pid, sig):
        raise PermissionError("denied")

    summary = kill_listeners(
        [_listener()], kill_func=fake_send,
        is_alive=lambda pid: True, sleep_func=lambda _: None,
    )
    assert len(summary.failed) == 1
    assert "permission denied" in summary.failed[0].reason
