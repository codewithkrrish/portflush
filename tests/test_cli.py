"""CLI tests: ports, confirmation, dry-run, JSON, edge cases.

Discovery and killing are monkeypatched so no real process is touched.
"""

import json

import pytest

import portflush.cli as cli
from portflush.errors import InvalidPortError
from portflush.models import Listener

LISTENER = Listener(port=3000, pid=1111, process="node", address="*:3000")


def _patch(monkeypatch, listeners, killed=None):
    monkeypatch.setattr(cli, "find_listeners", lambda port: list(listeners))
    if killed is not None:
        monkeypatch.setattr(cli, "kill_listeners", lambda *a, **k: killed)


def test_parse_port_valid_and_invalid():
    assert cli.parse_port("3000") == 3000
    with pytest.raises(InvalidPortError):
        cli.parse_port("abc")
    with pytest.raises(InvalidPortError):
        cli.parse_port("0")
    with pytest.raises(InvalidPortError):
        cli.parse_port("70000")
    with pytest.raises(InvalidPortError):
        cli.parse_port("-1")


def test_list_mode_prints_listener(monkeypatch, capsys):
    _patch(monkeypatch, [LISTENER])
    assert cli.run(["3000"]) == 0
    out = capsys.readouterr().out
    assert "3000" in out
    assert "node" in out


def test_list_json_output(monkeypatch, capsys):
    _patch(monkeypatch, [LISTENER])
    assert cli.run(["3000", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["port"] == 3000
    assert data[0]["pid"] == 1111


def test_no_listener_exit_code_3(monkeypatch, capsys):
    _patch(monkeypatch, [])
    assert cli.run(["3000"]) == 3
    assert "nothing is listening" in capsys.readouterr().err


def test_no_listener_json_error(monkeypatch, capsys):
    _patch(monkeypatch, [])
    assert cli.run(["3000", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["error"]


def test_no_ports_usage_error(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        cli.run([])
    assert exc.value.code == 2


def test_dry_run_kills_nothing(monkeypatch, capsys):
    from portflush.killing import KillSummary, KillResult

    summary = KillSummary(results=[KillResult(listener=LISTENER, killed=True)])
    _patch(monkeypatch, [LISTENER], killed=summary)
    assert cli.run(["3000", "--kill", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would kill" in out
    assert "Dry run" in out


def test_kill_refuses_without_confirmation_when_not_tty(monkeypatch, capsys):
    _patch(monkeypatch, [LISTENER])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.run(["3000", "--kill"]) == 2
    assert "confirmation" in capsys.readouterr().err


def test_kill_with_yes_skips_prompt(monkeypatch, capsys):
    from portflush.killing import KillSummary, KillResult

    summary = KillSummary(results=[KillResult(listener=LISTENER, killed=True)])
    _patch(monkeypatch, [LISTENER], killed=summary)
    assert cli.run(["3000", "--kill", "--yes"]) == 0
    assert "killed" in capsys.readouterr().out


def test_kill_json_reports_failed(monkeypatch, capsys):
    from portflush.killing import KillSummary, KillResult

    summary = KillSummary(
        results=[KillResult(listener=LISTENER, reason="permission denied")]
    )
    _patch(monkeypatch, [LISTENER], killed=summary)
    assert cli.run(["3000", "--kill", "--yes", "--json"]) == 4
    data = json.loads(capsys.readouterr().out)
    assert data["failed"][0]["reason"] == "permission denied"


def test_duplicate_ports_deduplicated(monkeypatch, capsys):
    calls: list[int] = []
    monkeypatch.setattr(
        cli, "find_listeners",
        lambda port: calls.append(port) or [LISTENER],
    )
    assert cli.run(["3000", "--port", "3000"]) == 0
    assert calls == [3000]
