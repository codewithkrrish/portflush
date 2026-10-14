"""Specific exception types for portflush.

The CLI catches these and prints a human-readable message on stderr with
the correct exit code — raw tracebacks never reach the user.
"""


class PortFlushError(Exception):
    """Base class for all portflush errors."""

    exit_code: int = 1


class InvalidPortError(PortFlushError):
    """A supplied port is not a valid TCP/UDP port number."""

    exit_code = 2


class DiscoveryError(PortFlushError):
    """No usable method could inspect listening sockets on this system."""

    exit_code = 1


class NoListenerError(PortFlushError):
    """Nothing is listening on the requested port(s)."""

    exit_code = 3


class KillError(PortFlushError):
    """One or more processes could not be terminated."""

    exit_code = 4


class NotInteractiveError(PortFlushError):
    """An interactive prompt was requested but stdin is not a TTY."""

    exit_code = 2
