"""Cross-platform discovery of processes listening on ports.

Strategy: try the best available backend in order — ``lsof``, ``ss``,
``netstat`` — and fall back to parsing ``/proc/net/tcp*`` on Linux when
none of them exist. Every parser is a pure function over command output,
so it is unit-testable without touching the live system.

Only listening sockets are reported: established connections and
TIME_WAIT entries are ignored.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import DiscoveryError
from .models import Listener

#: TCP state value for LISTEN in /proc/net/tcp (hex, upper-case in file).
_PROC_LISTEN_STATE = "0A"

# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

_PORT_RE = re.compile(r"(?::|\.)(\d+)\s*(?:\(LISTEN\)|\(LISTENING\))?\s*$")
_BRACKET_PORT_RE = re.compile(r"\]:(\d+)\s*$")
_SS_USERS_RE = re.compile(r'\(\("(?P<name>[^"]+)",pid=(?P<pid>\d+)')


def _port_from_address(address: str) -> int | None:
    """Extract the port from an address such as ``127.0.0.1:3000``,
    ``[::1]:3000`` or ``*.3000``. Returns ``None`` when unparsable."""
    address = address.strip()
    bracket = _BRACKET_PORT_RE.search(address)
    if bracket:
        return int(bracket.group(1))
    match = _PORT_RE.search(address)
    if match:
        return int(match.group(1))
    return None


def _clean_lsof_name(name: str) -> str:
    """Strip the trailing ``(LISTEN)`` marker from an lsof NAME field."""
    return re.sub(r"\s*\(LISTEN\)\s*$", "", name).strip()


# ---------------------------------------------------------------------------
# Pure parsers (unit-tested)
# ---------------------------------------------------------------------------


def parse_lsof_output(output: str) -> list[Listener]:
    """Parse ``lsof -nP -iTCP -sTCP:LISTEN`` (or ``-iUDP``) output.

    The NAME column is ``*:3000 (LISTEN)`` — two whitespace-separated
    tokens — so it is rejoined from column 8 onwards.
    """
    listeners: list[Listener] = []
    lines = output.splitlines()
    for line in lines[1:]:  # skip the COMMAND PID USER ... header
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        command, pid_text, user = parts[0], parts[1], parts[2]
        node = parts[7].upper()  # TCP or UDP
        name = _clean_lsof_name(" ".join(parts[8:]))
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        port = _port_from_address(name)
        if port is None:
            continue
        protocol = "udp" if node == "UDP" else "tcp"
        listeners.append(
            Listener(
                port=port, pid=pid, process=command, user=user,
                protocol=protocol, address=name,
            )
        )
    return listeners


def parse_ss_output(output: str) -> list[Listener]:
    """Parse ``ss -tlnp`` / ``ss -ulnp`` output."""
    listeners: list[Listener] = []
    for line in output.splitlines():
        if not line.strip() or line.lstrip().startswith(("Netid", "State")):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        protocol = parts[0].lower()
        if protocol.endswith("6"):
            protocol = protocol[:-1]
        if parts[1] != "LISTEN":
            continue
        local = parts[4]
        port = _port_from_address(local)
        if port is None:
            continue
        users = _SS_USERS_RE.search(line)
        pid = int(users.group("pid")) if users else None
        process = users.group("name") if users else None
        listeners.append(
            Listener(
                port=port, pid=pid, process=process,
                protocol=protocol, address=local,
            )
        )
    return listeners


def parse_netstat_output(output: str, *, windows: bool = False) -> list[Listener]:
    """Parse ``netstat`` output.

    Handles both Unix (``netstat -tlnp``) and Windows (``netstat -ano``)
    layouts. Only LISTEN / LISTENING rows are kept.
    """
    listeners: list[Listener] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith(("PROTO", "ACTIVE", "PROTOCOL")):
            continue
        parts = stripped.split()
        if windows:
            # TCP    0.0.0.0:3000    0.0.0.0:0    LISTENING    12345
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            protocol = parts[0].lower()
            port = _port_from_address(parts[1])
            if port is None:
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                pid = None
            listeners.append(
                Listener(port=port, pid=pid, protocol=protocol, address=parts[1])
            )
            continue
        # Unix: tcp 0 0 0.0.0.0:3000 0.0.0.0:* LISTEN 12345/node
        # macOS: tcp4 0 0 *.3000 *.* LISTEN
        if len(parts) < 6 or parts[5] != "LISTEN":
            continue
        protocol = "udp" if parts[0].lower().startswith("udp") else "tcp"
        port = _port_from_address(parts[3])
        if port is None:
            continue
        pid: int | None = None
        process: str | None = None
        if len(parts) > 6 and "/" in parts[6]:
            pid_text, _, process = parts[6].partition("/")
            try:
                pid = int(pid_text)
            except ValueError:
                pid = None
        listeners.append(
            Listener(
                port=port, pid=pid, process=process or None,
                protocol=protocol, address=parts[3],
            )
        )
    return listeners


def parse_proc_net_tcp(text: str) -> list[tuple[int, int]]:
    """Parse ``/proc/net/tcp`` or ``/proc/net/tcp6`` content.

    Returns ``(port, inode)`` pairs for sockets in LISTEN state.
    """
    found: list[tuple[int, int]] = []
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 10 or parts[3] != _PROC_LISTEN_STATE:
            continue
        local = parts[1]
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1], 16)
            inode = int(parts[9])
        except ValueError:
            continue
        found.append((port, inode))
    return found


def resolve_inodes_to_pids(
    inode_to_port: dict[int, int], *, proc_root: Path = Path("/proc")
) -> list[Listener]:
    """Map socket inodes to owning PIDs by scanning ``/proc/<pid>/fd``.

    ``inode_to_port`` maps socket inode -> port. Processes whose command
    line cannot be read are reported with ``process=None``.
    """
    pid_for_inode: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        fd_dir = entry / "fd"
        if not fd_dir.is_dir():
            continue
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match:
                pid_for_inode[int(match.group(1))] = pid
    listeners: list[Listener] = []
    for inode, port in inode_to_port.items():
        pid = pid_for_inode.get(inode)
        if pid is None:
            continue
        process: str | None = None
        try:
            cmdline = (proc_root / str(pid) / "cmdline").read_bytes()
            process = cmdline.split(b"\0")[0].decode(errors="replace") or None
            if process:
                process = Path(process).name
        except OSError:
            process = None
        listeners.append(Listener(port=port, pid=pid, process=process))
    return listeners


# ---------------------------------------------------------------------------
# Live discovery
# ---------------------------------------------------------------------------


def _run(command: list[str]) -> str:
    """Run a discovery command, returning stdout (empty string on failure)."""
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _discover_via_proc() -> list[Listener]:
    """Linux fallback: parse /proc/net/tcp* and map inodes to PIDs."""
    inode_to_port: dict[int, int] = {}
    for name in ("tcp", "tcp6", "udp", "udp6"):
        path = Path("/proc/net") / name
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for port, inode in parse_proc_net_tcp(text):
            inode_to_port[inode] = port
    if not inode_to_port:
        return []
    return resolve_inodes_to_pids(inode_to_port)


def discover_listeners() -> list[Listener]:
    """Return every listening socket on this machine.

    Raises :class:`DiscoveryError` when no backend could produce data.
    """
    if shutil.which("lsof"):
        listeners: list[Listener] = []
        listeners += parse_lsof_output(
            _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        )
        listeners += parse_lsof_output(
            _run(["lsof", "-nP", "-iUDP", "-sUDP:LISTEN"])
        )
        if listeners:
            return listeners
    if shutil.which("ss"):
        output = _run(["ss", "-tlnp"]) + _run(["ss", "-ulnp"])
        if output.strip():
            return parse_ss_output(output)
    if shutil.which("netstat"):
        windows = os.name == "nt"
        args = ["netstat", "-ano"] if windows else ["netstat", "-tlnp"]
        output = _run(args)
        if output.strip():
            return parse_netstat_output(output, windows=windows)
    if Path("/proc/net/tcp").exists():
        return _discover_via_proc()
    raise DiscoveryError(
        "Could not inspect listening ports: no lsof, ss, netstat, "
        "or /proc/net/tcp found on this system."
    )


def find_listeners(port: int) -> list[Listener]:
    """Return the listeners on one specific port (empty list if none)."""
    return [item for item in discover_listeners() if item.port == port]
