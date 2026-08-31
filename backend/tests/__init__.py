"""Backend test package bootstrap."""

from .network_guard import (
    ExternalNetworkBlocked,
    assert_network_guard_active,
    install_network_guard,
)


# Package-aware discovery and focused ``python -m unittest`` execution import this
# package before loading test modules.
install_network_guard()


__all__ = (
    "ExternalNetworkBlocked",
    "assert_network_guard_active",
    "install_network_guard",
)
