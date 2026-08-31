"""Test-only protection against accidental real network access."""

from __future__ import annotations

import socket
import unittest
from typing import Any, NoReturn


class ExternalNetworkBlocked(AssertionError):
    """Raised when a backend test attempts any real socket operation."""


def _blocked_network(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise ExternalNetworkBlocked(
        "backend tests must mock all network access; real socket egress is blocked"
    )


_blocked_network._tripstar_network_guard = True  # type: ignore[attr-defined]


def install_network_guard() -> None:
    """Install the process-wide test guard idempotently."""
    if all(
        getattr(target, "_tripstar_network_guard", False)
        for target in (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.create_connection,
            socket.getaddrinfo,
        )
    ):
        return
    socket.socket.connect = _blocked_network  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked_network  # type: ignore[method-assign]
    socket.create_connection = _blocked_network  # type: ignore[assignment]
    socket.getaddrinfo = _blocked_network  # type: ignore[assignment]


def assert_network_guard_active() -> None:
    """Fail if the current test process has not installed the guard."""
    guarded = (
        getattr(socket.socket.connect, "_tripstar_network_guard", False)
        and getattr(socket.socket.connect_ex, "_tripstar_network_guard", False)
        and getattr(socket.create_connection, "_tripstar_network_guard", False)
        and getattr(socket.getaddrinfo, "_tripstar_network_guard", False)
    )
    if not guarded:
        raise AssertionError("backend network guard is not active")


def guarded_unittest_main() -> None:
    """Run a directly executed unittest module with egress blocked first."""
    install_network_guard()
    unittest.main()
