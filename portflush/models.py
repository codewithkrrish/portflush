"""Data model for a listening socket."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Listener:
    """One process listening on one port.

    ``pid`` is ``None`` when the discovery backend (e.g. macOS ``netstat``)
    cannot report the owning process.
    """

    port: int
    pid: int | None
    process: str | None = None
    user: str | None = None
    protocol: str = "tcp"
    address: str | None = None

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation."""
        return {
            "port": self.port,
            "pid": self.pid,
            "process": self.process,
            "user": self.user,
            "protocol": self.protocol,
            "address": self.address,
        }
