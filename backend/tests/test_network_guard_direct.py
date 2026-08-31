"""Activation probe shared by supported backend unittest entrypoints."""

import socket
import unittest

try:
    from backend.tests.network_guard import (
        ExternalNetworkBlocked,
        assert_network_guard_active,
    )
except ModuleNotFoundError:
    from network_guard import ExternalNetworkBlocked, assert_network_guard_active


class DirectNetworkGuardActivationTests(unittest.TestCase):
    def test_guard_is_active_before_test_execution(self):
        assert_network_guard_active()
        with self.assertRaises(ExternalNetworkBlocked):
            socket.getaddrinfo("example.invalid", 443)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
