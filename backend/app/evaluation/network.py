"""Eval-only fail-closed network isolation."""

import socket
from contextlib import contextmanager
from unittest.mock import patch


class NetworkAccessBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkAccessBlocked("network_access_blocked")


@contextmanager
def deny_network():
    """Block socket connection primitives only inside this context."""
    with patch.object(socket, "create_connection", _blocked), patch.object(
        socket.socket, "connect", _blocked
    ), patch.object(socket.socket, "connect_ex", _blocked):
        yield
