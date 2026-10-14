"""Tests for the discovery parsers (pure functions, no live system)."""

from pathlib import Path

from portflush.discovery import (
    parse_lsof_output,
    parse_netstat_output,
    parse_proc_net_tcp,
    parse_ss_output,
    resolve_inodes_to_pids,
)

LSOF_SAMPLE = """COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
node    12345 krrish   21u  IPv4 0x1234      0t0  TCP *:3000 (LISTEN)
python   9999 krrish   3u   IPv4 0x5678      0t0  TCP 127.0.0.1:8000 (LISTEN)
"""

SS_SAMPLE = """Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process
tcp LISTEN 0 4096 [::]:3000 [::]:* users:(("node",pid=12345,fd=21))
tcp LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* users:(("python",pid=999,fd=3))
udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("avahi",pid=55,fd=1))
"""

NETSTAT_SAMPLE = """Proto Recv-Q Send-Q Local Address Foreign Address State PID/Program name
tcp 0 0 0.0.0.0:3000 0.0.0.0:* LISTEN 12345/node
tcp 0 0 127.0.0.1:8000 0.0.0.0:* LISTEN 999/python
tcp 0 0 127.0.0.1:9000 127.0.0.1:9001 ESTABLISHED 111/curl
"""

NETSTAT_MACOS_SAMPLE = """Proto Recv-Q Send-Q Local Address Foreign Address (state)
tcp4 0 0 *.3000 *.* LISTEN
"""

NETSTAT_WINDOWS_SAMPLE = """ Proto Local Address Foreign Address State PID
 TCP 0.0.0.0:3000 0.0.0.0:0 LISTENING 12345
 TCP 127.0.0.1:8000 127.0.0.1:9000 ESTABLISHED 999
"""

PROC_NET_SAMPLE = """ sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode
 0: 0100007F:0BB8 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 123456 1 0000000000000000 100 0 0 10 0
 1: 00000000:1F90 00000000:0000 01 00000000:00000000 00:00000000 00000000 1000 0 999999 1 0000000000000000 100 0 0 10 0
"""


def test_parse_lsof_happy_path():
    listeners = parse_lsof_output(LSOF_SAMPLE)
    assert len(listeners) == 2
    assert listeners[0].port == 3000
    assert listeners[0].pid == 12345
    assert listeners[0].process == "node"
    assert listeners[1].port == 8000
    assert listeners[1].address == "127.0.0.1:8000"


def test_parse_lsof_empty_and_corrupt():
    assert parse_lsof_output("") == []
    assert parse_lsof_output("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n") == []
    # A malformed row is skipped, not fatal.
    assert parse_lsof_output("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nGARBAGE\n") == []


def test_parse_ss_happy_path():
    listeners = parse_ss_output(SS_SAMPLE)
    assert len(listeners) == 2  # UNCONN row ignored
    assert listeners[0].port == 3000
    assert listeners[0].pid == 12345
    assert listeners[0].process == "node"
    assert listeners[1].port == 8000


def test_parse_ss_empty():
    assert parse_ss_output("") == []


def test_parse_netstat_happy_path():
    listeners = parse_netstat_output(NETSTAT_SAMPLE)
    assert len(listeners) == 2  # ESTABLISHED row ignored
    assert listeners[0].port == 3000
    assert listeners[0].pid == 12345
    assert listeners[0].process == "node"


def test_parse_netstat_macos_no_pid():
    listeners = parse_netstat_output(NETSTAT_MACOS_SAMPLE)
    assert len(listeners) == 1
    assert listeners[0].port == 3000
    assert listeners[0].pid is None


def test_parse_netstat_windows():
    listeners = parse_netstat_output(NETSTAT_WINDOWS_SAMPLE, windows=True)
    assert len(listeners) == 1
    assert listeners[0].port == 3000
    assert listeners[0].pid == 12345


def test_parse_proc_net_tcp_only_listen():
    found = parse_proc_net_tcp(PROC_NET_SAMPLE)
    assert found == [(3000, 123456)]  # state 01 row ignored


def test_resolve_inodes_with_fake_proc(tmp_path: Path):
    proc = tmp_path / "proc"
    proc.mkdir()
    pid_dir = proc / "4242" / "fd"
    pid_dir.mkdir(parents=True)
    (pid_dir / "7").symlink_to("socket:[123456]")
    (proc / "4242" / "cmdline").write_bytes(b"/usr/bin/node\x00server.js\x00")
    listeners = resolve_inodes_to_pids({123456: 3000, 999999: 8080}, proc_root=proc)
    assert len(listeners) == 1
    assert listeners[0].port == 3000
    assert listeners[0].pid == 4242
    assert listeners[0].process == "node"
