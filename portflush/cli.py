"""Thin CLI layer for portflush.

All logic lives in :mod:`portflush.discovery` and :mod:`portflush.killing`;
this module only parses arguments, wires I/O, and maps errors to the
correct exit code and stderr message.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .discovery import discover_listeners, find_listeners
from .errors import (
    InvalidPortError,
    NoListenerError,
    NotInteractiveError,
    PortFlushError,
)
from .killing import DEFAULT_TIMEOUT_SECONDS, kill_listeners
from .models import Listener

MIN_PORT = 1
MAX_PORT = 65535
EXIT_OK = 0
EXIT_USAGE = 2


def parse_port(text: str) -> int:
    """Validate one port argument; raises InvalidPortError on bad input."""
    try:
        port = int(text)
    except ValueError:
        raise InvalidPortError(
            f"invalid port {text!r}: expected a number 1-{MAX_PORT}"
        ) from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise InvalidPortError(
            f"invalid port {port}: expected a number 1-{MAX_PORT}"
        )
    return port


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="portflush",
        description="Find and free whatever is listening on a port.",
    )
    parser.add_argument(
        "ports", nargs="*", metavar="PORT",
        help="port(s) to inspect, e.g. 3000 8080",
    )
    parser.add_argument(
        "--port", action="append", default=[], metavar="PORT",
        help="port to inspect (repeatable; same as positional)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list listeners instead of killing (default when --kill is absent)",
    )
    parser.add_argument(
        "--kill", action="store_true",
        help="terminate the processes listening on the given port(s)",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="skip confirmation and send SIGKILL immediately",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="skip the confirmation prompt (still kills gracefully)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show what would be killed without killing anything",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable JSON output",
    )
    parser.add_argument(
        "--pick", action="store_true",
        help="interactively pick which listeners to kill when several match",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds to wait after SIGTERM before SIGKILL "
        f"(default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--version", action="version", version=f"portflush {__version__}",
    )
    return parser


def _collect_ports(args: argparse.Namespace) -> list[int]:
    """Merge positional and --port values, deduplicated in order."""
    ports: list[int] = []
    for text in list(args.ports) + list(args.port):
        port = parse_port(text)
        if port not in ports:
            ports.append(port)
    return ports


def _confirm(prompt: str) -> bool:
    """Ask a y/N question; returns False on anything but yes."""
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _pick_listeners(listeners: list[Listener]) -> list[Listener]:
    """Let the user choose a subset of listeners by number."""
    if not sys.stdin.isatty():
        raise NotInteractiveError(
            "--pick needs an interactive terminal (stdin is not a TTY)"
        )
    print("Multiple listeners found:")
    for index, item in enumerate(listeners, start=1):
        print(f"  {index}. port {item.port}  PID {item.pid}  "
              f"{item.process or 'unknown'}")
    answer = input("Enter numbers to kill, comma-separated (blank = all): ")
    if not answer.strip():
        return list(listeners)
    chosen: list[Listener] = []
    for piece in answer.split(","):
        piece = piece.strip()
        try:
            number = int(piece)
        except ValueError:
            raise InvalidPortError(
                f"invalid selection {piece!r}: expected a number"
            ) from None
        if not 1 <= number <= len(listeners):
            raise InvalidPortError(
                f"selection {number} out of range (1-{len(listeners)})"
            )
        if listeners[number - 1] not in chosen:
            chosen.append(listeners[number - 1])
    return chosen


def _print_listeners(listeners: list[Listener], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([item.to_dict() for item in listeners], indent=2))
        return
    if not listeners:
        print("Nothing is listening.")
        return
    print(f"{'PORT':>6}  {'PID':>7}  {'PROCESS':<20}  {'ADDRESS'}")
    print("-" * 64)
    for item in listeners:
        print(f"{item.port:>6}  {str(item.pid or '-'):>7}  "
              f"{(item.process or '-'): <20}  {item.address or '-'}")


def _print_kill_report(
    listeners: list[Listener], summary, *, as_json: bool, dry_run: bool
) -> int:
    """Render the kill outcome; returns the process exit code."""
    if as_json:
        payload = {
            "dry_run": dry_run,
            "killed": [
                {**r.listener.to_dict(), "escalated": r.escalated}
                for r in summary.killed
            ],
            "skipped": [
                {"listener": r.listener.to_dict(), "reason": r.reason}
                for r in summary.results if r.skipped
            ],
            "failed": [
                {"listener": r.listener.to_dict(), "reason": r.reason}
                for r in summary.failed
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        for result in summary.results:
            item = result.listener
            label = f"port {item.port} PID {item.pid} ({item.process or 'unknown'})"
            if result.killed:
                verb = "would kill" if dry_run else "killed"
                extra = " (SIGKILL)" if result.escalated else ""
                print(f"{verb} {label}{extra}")
            elif result.skipped:
                print(f"skipped {label}: {result.reason}")
            else:
                print(f"failed {label}: {result.reason}", file=sys.stderr)
        if dry_run:
            print("Dry run: nothing was killed.")
    if summary.failed:
        return 4
    return EXIT_OK


def run(argv: list[str] | None = None) -> int:
    """Entry point used by ``__main__`` and tests. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ports = _collect_ports(args)
        if args.timeout < 0:
            parser.error("--timeout must not be negative")
        if not ports:
            if args.list:
                listeners = discover_listeners()
                _print_listeners(listeners, as_json=args.json)
                return EXIT_OK
            parser.error("give at least one PORT, or use --list")
            return EXIT_USAGE  # pragma: no cover (argparse exits first)
        listeners: list[Listener] = []
        for port in ports:
            listeners.extend(find_listeners(port))
        if not listeners:
            raise NoListenerError(
                "nothing is listening on "
                + ", ".join(str(p) for p in ports)
            )
        if not args.kill and not args.list:
            # Safe default: just show what is there.
            _print_listeners(listeners, as_json=args.json)
            return EXIT_OK
        if args.list:
            _print_listeners(listeners, as_json=args.json)
            return EXIT_OK
        # --kill path
        targets = listeners
        if args.pick and len(listeners) > 1:
            targets = _pick_listeners(listeners)
            if not targets:
                print("Nothing selected.")
                return EXIT_OK
        if not args.force and not args.yes and not args.dry_run:
            if not sys.stdin.isatty():
                print(
                    "refusing to kill without confirmation: pass --yes, "
                    "--force, or --dry-run",
                    file=sys.stderr,
                )
                return EXIT_USAGE
            names = ", ".join(
                f"{t.process or 'PID ' + str(t.pid)} (port {t.port})"
                for t in targets
            )
            if not _confirm(f"Kill {len(targets)} process(es): {names}?"):
                print("Aborted.")
                return EXIT_OK
        summary = kill_listeners(
            targets, timeout=args.timeout, force=args.force,
            dry_run=args.dry_run,
        )
        return _print_kill_report(
            targets, summary, as_json=args.json, dry_run=args.dry_run
        )
    except PortFlushError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"portflush: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nportflush: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    """Console-script entry point."""
    raise SystemExit(run())
