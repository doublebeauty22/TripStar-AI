"""Cookie validation contracts; signing JS and HTTP are always replaced here."""

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from backend.tests import assert_network_guard_active
from backend.app.services import xhs_service
from backend.app.services.xhs_sign import sign_util


class XHSSigningCookieValidationTests(unittest.TestCase):
    _PRIVATE_MARKER = "synthetic-private-cookie-marker"
    _VALIDATION_MESSAGE = "XHS signing requires a non-empty a1 cookie"
    _SIGN_ERROR_MESSAGE = "XHS request signing failed"

    def setUp(self):
        assert_network_guard_active()
        # These tests exercise parsing, validation, and the real client error
        # boundary. The separate signer integration tests exercise actual JS.
        self.xs_call = self.enterContext(patch.object(
            sign_util._xs_js,
            "call",
            return_value={
                "xs": "synthetic-sign-result",
                "xt": 123,
                "xs_common": "synthetic-common-result",
            },
        ))
        self.xray_call = self.enterContext(patch.object(
            sign_util._xray_js, "call", return_value="synthetic-trace-result",
        ))
        self.post = self.enterContext(patch.object(
            xhs_service.requests,
            "post",
            return_value=SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"success": True, "data": {"items": []}},
            ),
        ))

    def _invalid_cookie_cases(self):
        return (
            ("empty", ""),
            ("whitespace", " \t "),
            ("unparseable", self._PRIVATE_MARKER),
            ("missing_a1", f"other={self._PRIVATE_MARKER}"),
            ("empty_a1", f"a1=; other={self._PRIVATE_MARKER}"),
            ("blank_a1", f"a1= \t ; other={self._PRIVATE_MARKER}"),
        )

    def _assert_private_message(self, error, expected):
        message = str(error)
        self.assertTrue(message == expected, "error message must be fixed")
        self.assertTrue(
            self._PRIVATE_MARKER not in message,
            "error message must exclude private input",
        )

    def test_empty_or_missing_a1_is_rejected_before_any_js_call(self):
        for label, raw_cookie in self._invalid_cookie_cases():
            with self.subTest(case=label):
                self.xs_call.reset_mock()
                self.xray_call.reset_mock()
                with self.assertRaises(ValueError) as raised:
                    sign_util.generate_request_params(
                        raw_cookie, "/api/sns/web/v2/search/notes", {"page": 1},
                    )

                self._assert_private_message(
                    raised.exception, self._VALIDATION_MESSAGE,
                )
                self.xs_call.assert_not_called()
                self.xray_call.assert_not_called()
                self.post.assert_not_called()

    def test_real_search_rejects_invalid_cookie_as_sign_error_before_http(self):
        self._assert_client_rejects_invalid_cookie("search")

    def test_real_detail_rejects_invalid_cookie_as_sign_error_before_http(self):
        self._assert_client_rejects_invalid_cookie("detail")

    def _assert_client_rejects_invalid_cookie(self, operation):
        for label, raw_cookie in self._invalid_cookie_cases():
            with self.subTest(case=label):
                self.xs_call.reset_mock()
                self.xray_call.reset_mock()
                self.post.reset_mock()
                client = xhs_service.XhsNativeClient(raw_cookie)
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(
                    xhs_service.XHSRequestError,
                ) as raised:
                    if operation == "search":
                        client.search_notes("synthetic-query")
                    else:
                        client.get_note_detail("synthetic-note")

                self.assertEqual(raised.exception.reason, "sign_error")
                self._assert_private_message(
                    raised.exception, self._SIGN_ERROR_MESSAGE,
                )
                self.assertTrue(
                    self._PRIVATE_MARKER not in output.getvalue(),
                    "client output must exclude private input",
                )
                self.xs_call.assert_not_called()
                self.xray_call.assert_not_called()
                self.post.assert_not_called()

    def test_mixed_cookie_separators_preserve_values_containing_equals(self):
        _, cookies, payload = sign_util.generate_request_params(
            f" a1 = {self._PRIVATE_MARKER};session=left=right; webId=synthetic-id ",
            "/api/sns/web/v2/search/notes",
            {"keyword": "合成景点"},
        )

        self.assertTrue(cookies == {
            "a1": self._PRIVATE_MARKER,
            "session": "left=right",
            "webId": "synthetic-id",
        }, "mixed whitespace must not combine distinct cookie pairs")
        self.assertEqual(payload, '{"keyword":"合成景点"}')
        self.post.assert_not_called()

    def test_json_cookie_export_reaches_signer_through_real_client_factory(self):
        raw_cookie = json.dumps([
            {"name": "a1", "value": self._PRIVATE_MARKER},
            {"name": "session", "value": "left=right"},
        ])
        self._assert_normalized_factory_input_reaches_signer(raw_cookie)

    def test_quoted_cookie_header_reaches_signer_through_real_client_factory(self):
        raw_cookie = f" ' a1={self._PRIVATE_MARKER}; session=left=right ' "
        self._assert_normalized_factory_input_reaches_signer(raw_cookie)

    def _assert_normalized_factory_input_reaches_signer(self, raw_cookie):
        with patch.object(
            xhs_service,
            "get_settings",
            return_value=SimpleNamespace(xhs_cookie=raw_cookie),
        ), redirect_stdout(io.StringIO()):
            client = xhs_service.get_xhs_client()
            _, cookies, payload = sign_util.generate_request_params(
                client.cookies_str, "/api/sns/web/v1/feed", {"page": 1},
            )

        self.assertTrue(cookies == {
            "a1": self._PRIVATE_MARKER,
            "session": "left=right",
        }, "normalized configuration must reach the real cookie parser")
        self.assertEqual(payload, '{"page":1}')
        self.post.assert_not_called()


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
