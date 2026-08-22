import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import requests

from backend.app.services import xhs_service


REQUEST_ID = "abcdef123456"


class _Client:
    def __init__(self, *, search=None, search_error=None, detail=None, detail_error=None):
        self.search = search if search is not None else {"data": {"items": []}}
        self.search_error = search_error
        self.detail = detail if detail is not None else {"data": {"items": []}}
        self.detail_error = detail_error

    def search_notes(self, **_kwargs):
        if self.search_error:
            raise self.search_error
        return self.search

    def get_note_detail(self, *_args):
        if self.detail_error:
            raise self.detail_error
        return self.detail


def _note_search():
    return {"data": {"items": [{"model_type": "note", "id": "note", "xsec_token": "token"}]}}


class XHSPhotoObservabilityTests(unittest.TestCase):
    def _run(self, client=None, *, client_error=None, ssr=None, ssr_error=None):
        output = io.StringIO()
        client_patch = patch.object(
            xhs_service, "get_xhs_client",
            side_effect=client_error if client_error else None,
            return_value=client,
        )
        ssr_response = SimpleNamespace(text=ssr if ssr is not None else "")
        ssr_patch = patch.object(
            xhs_service.httpx, "get",
            side_effect=ssr_error if ssr_error else None,
            return_value=ssr_response,
        )
        with redirect_stdout(output), client_patch, ssr_patch:
            try:
                result = xhs_service.get_xhs_photo_sync("private query", request_id=REQUEST_ID)
                error = None
            except Exception as exc:
                result, error = None, exc
        return result, error, output.getvalue()

    def assert_event(self, output, stage, category, retryable):
        expected = (
            f"XHS_EVENT request_id={REQUEST_ID} stage={stage} "
            f"category={category} retryable={str(retryable).lower()}"
        )
        self.assertIn(expected, output)

    def test_missing_config(self):
        _, error, output = self._run(client_error=xhs_service.XHSCookieExpiredError("secret"))
        self.assertIsNotNone(error)
        self.assert_event(output, "configuration", "missing_config", False)

    def test_search_http_categories(self):
        cases = [
            ("authentication_failed", False),
            ("permission_denied", False),
            ("rate_limited", True),
        ]
        for category, retryable in cases:
            with self.subTest(category=category):
                client = _Client(search_error=xhs_service.XHSRequestError(category, "secret"))
                _, error, output = self._run(client)
                self.assertIsNotNone(error)
                self.assert_event(output, "search", category, retryable)

    def test_risk_control(self):
        client = _Client(search_error=xhs_service.XHSCookieExpiredError("secret"))
        _, error, output = self._run(client)
        self.assertIsNotNone(error)
        self.assert_event(output, "search", "risk_control", False)

    def test_sign_failure(self):
        client = _Client(search_error=xhs_service.XHSRequestError("sign_error", "secret"))
        _, error, output = self._run(client)
        self.assertIsNotNone(error)
        self.assert_event(output, "sign", "sign_error", False)

    def test_search_transport_categories(self):
        cases = [(requests.Timeout("secret"), "timeout"),
                 (requests.ConnectionError("secret"), "network_error")]
        for error_value, category in cases:
            with self.subTest(category=category):
                _, error, output = self._run(_Client(search_error=error_value))
                self.assertIsNotNone(error)
                self.assert_event(output, "search", category, True)

    def test_malformed_search_and_no_result(self):
        _, error, output = self._run(_Client(search={"data": {"items": "bad"}}))
        self.assertIsNotNone(error)
        self.assert_event(output, "parse", "malformed_response", False)

        result, error, output = self._run(_Client())
        self.assertEqual(result, "")
        self.assertIsNone(error)
        self.assert_event(output, "search", "no_result", False)

    def test_detail_failure_then_ssr_success(self):
        state = {"note": {"noteDetailMap": {"note": {"note": {
            "imageList": [{"urlDefault": "https://image.example/safe"}]
        }}}}}
        html = f"window.__INITIAL_STATE__={json.dumps(state)}</script>"
        client = _Client(search=_note_search(), detail_error=requests.Timeout("secret"))
        result, error, output = self._run(client, ssr=html)
        self.assertIsNone(error)
        self.assertEqual(result, "https://image.example/safe")
        self.assert_event(output, "detail", "timeout", True)

    def test_ssr_timeout_and_malformed_response(self):
        client = _Client(search=_note_search())
        _, error, output = self._run(client, ssr_error=httpx.ReadTimeout("secret"))
        self.assertIsNotNone(error)
        self.assert_event(output, "ssr", "timeout", True)

        malformed = 'window.__INITIAL_STATE__={"note": []}</script>'
        _, error, output = self._run(client, ssr=malformed)
        self.assertIsNotNone(error)
        self.assert_event(output, "parse", "malformed_response", False)

    def test_logs_exclude_sensitive_data(self):
        sensitive = ["private query", "cookie-secret", "a1-secret", "raw-provider-body",
                     "https://private.example", "/private/local/path", "signature-secret"]
        client = _Client(search_error=RuntimeError(" ".join(sensitive)))
        _, error, output = self._run(client)
        self.assertIsNotNone(error)
        self.assert_event(output, "search", "unexpected_error", False)
        for value in sensitive:
            self.assertNotIn(value, output)


class _Response:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status_code = status
        self.payload = payload if payload is not None else {"success": True, "data": {"items": []}}
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("raw response")

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class XHSNativeClientClassificationTests(unittest.TestCase):
    def _photo_for_response(self, response):
        client = xhs_service.XhsNativeClient("not-logged")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=client
        ), patch.object(xhs_service, "_sign_request", return_value=({}, {}, "{}")), patch.object(
            xhs_service, "_new_search_id", return_value="safe"
        ), patch.object(xhs_service.requests, "post", return_value=response):
            with self.assertRaises(Exception):
                xhs_service.get_xhs_photo_sync("not-logged", request_id=REQUEST_ID)
        return output.getvalue()

    def test_http_statuses_are_typed(self):
        cases = [(401, "authentication_failed"), (403, "permission_denied"),
                 (429, "rate_limited")]
        for status, reason in cases:
            with self.subTest(status=status), patch.object(
                xhs_service, "_sign_request", return_value=({}, {}, "{}")
            ), patch.object(xhs_service, "_new_search_id", return_value="safe"), patch.object(
                xhs_service.requests, "post", return_value=_Response(status=status)
            ):
                with self.assertRaises(xhs_service.XHSRequestError) as raised:
                    xhs_service.XhsNativeClient("not-logged").search_notes("not-logged")
                self.assertEqual(raised.exception.reason, reason)

    def test_300011_is_risk_control_in_photo_path(self):
        response = _Response(payload={"success": False, "code": 300011, "msg": "not logged"})
        client = xhs_service.XhsNativeClient("not-logged")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=client
        ), patch.object(xhs_service, "_sign_request", return_value=({}, {}, "{}")), patch.object(
            xhs_service, "_new_search_id", return_value="safe"
        ), patch.object(xhs_service.requests, "post", return_value=response):
            with self.assertRaises(xhs_service.XHSCookieExpiredError):
                xhs_service.get_xhs_photo_sync("not-logged", request_id=REQUEST_ID)
        self.assertIn("stage=search category=risk_control retryable=false", output.getvalue())
        self.assertNotIn("not logged", output.getvalue())

    def test_string_300011_is_also_risk_control(self):
        response = _Response(payload={"success": False, "code": "300011", "msg": "safe"})
        output = self._photo_for_response(response)
        self.assertIn("stage=search category=risk_control retryable=false", output)
        self.assertNotIn("business_code=", output)

    def test_other_http_errors_are_classified_by_retryability(self):
        for status, retryable in [
            (400, False), (404, False), (422, False),
            (500, True), (502, True), (503, True),
        ]:
            with self.subTest(status=status):
                output = self._photo_for_response(_Response(status=status))
                self.assertIn(
                    f"stage=search category=http_error status={status} "
                    f"retryable={str(retryable).lower()}",
                    output,
                )

    def test_detail_http_error_logs_status_and_keeps_ssr_fallback(self):
        search_response = _Response(payload={"success": True, "data": {"items": [{
            "model_type": "note", "id": "safe-note", "xsec_token": "safe-token",
        }]}})
        detail_response = _Response(status=503)
        client = xhs_service.XhsNativeClient("not-logged")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=client,
        ), patch.object(
            xhs_service, "_sign_request", return_value=({}, {}, "{}"),
        ), patch.object(
            xhs_service, "_new_search_id", return_value="safe",
        ), patch.object(
            xhs_service.requests, "post",
            side_effect=[search_response, detail_response],
        ) as post, patch.object(
            xhs_service.httpx, "get", return_value=SimpleNamespace(text=""),
        ) as ssr:
            result = xhs_service.get_xhs_photo_sync("not-logged", request_id=REQUEST_ID)
        self.assertEqual(result, "")
        self.assertEqual(post.call_count, 2)
        ssr.assert_called_once()
        self.assertIn(
            "stage=detail category=http_error status=503 retryable=true",
            output.getvalue(),
        )

    def test_invalid_status_metadata_is_omitted_without_stringification(self):
        unsafe_statuses = [None, True, "404", {"status": "secret"}, 99, 600]
        for status in unsafe_statuses:
            with self.subTest(status_type=type(status).__name__):
                output = io.StringIO()
                with redirect_stdout(output):
                    xhs_service._log_xhs_event(
                        request_id=REQUEST_ID, stage="search", category="http_error",
                        http_status=status,
                    )
                self.assertNotIn("status=", output.getvalue())
                self.assertNotIn("secret", output.getvalue())

                error = xhs_service.XHSRequestError(
                    "http_error", "safe", http_status=status,
                )
                self.assertIsNone(error.http_status)

        output = io.StringIO()
        with redirect_stdout(output):
            xhs_service._log_xhs_event(
                request_id=REQUEST_ID, stage="search",
                category="authentication_failed", http_status=401,
            )
        self.assertNotIn("status=", output.getvalue())
        self.assertIsNone(xhs_service.XHSRequestError(
            "authentication_failed", "safe", http_status=401,
        ).http_status)

    def test_unknown_business_failures_are_business_rejected(self):
        cases = [
            ({"success": False, "code": 399999, "msg": "ordinary rejection"}, "399999"),
            ({"success": False, "code": "SAFE_CODE-1.2", "msg": "ordinary message"}, "SAFE_CODE-1.2"),
            ({"success": False, "msg": "missing code"}, "missing"),
            ({"success": False, "code": None, "msg": "null code"}, "missing"),
        ]
        for payload, safe_code in cases:
            with self.subTest(payload_keys=sorted(payload)):
                output = self._photo_for_response(_Response(payload=payload))
                self.assertIn(
                    "stage=search category=business_rejected "
                    f"business_code={safe_code} retryable=false", output
                )
                self.assertNotIn(payload["msg"], output)

    def test_unsafe_business_codes_are_redacted(self):
        unsafe_codes = [
            {"secret": "value"}, ["secret"], "A" * 33, "has space",
            "has\nnewline", "has=value", "control\x01value",
        ]
        for code in unsafe_codes:
            with self.subTest(code_type=type(code).__name__):
                payload = {
                    "success": False, "code": code,
                    "msg": "provider message must not be logged",
                    "message": "alternate message must not be logged",
                    "body_secret": "response body must not be logged",
                }
                output = self._photo_for_response(_Response(payload=payload))
                self.assertIn(
                    "stage=search category=business_rejected "
                    "business_code=redacted retryable=false", output
                )
                for sensitive in payload.values():
                    if isinstance(sensitive, str) and sensitive != "redacted":
                        self.assertNotIn(sensitive, output)

    def test_other_request_exceptions_are_request_error(self):
        for request_error in [
            requests.TooManyRedirects("redirect target must not be logged"),
            requests.RequestException("raw request failure must not be logged"),
        ]:
            with self.subTest(error_type=type(request_error).__name__):
                client = _Client(search_error=request_error)
                output = io.StringIO()
                with redirect_stdout(output), patch.object(
                    xhs_service, "get_xhs_client", return_value=client
                ):
                    with self.assertRaises(requests.RequestException):
                        xhs_service.get_xhs_photo_sync("not-logged", request_id=REQUEST_ID)
                self.assertIn(
                    "stage=search category=request_error retryable=false", output.getvalue()
                )
                self.assertNotIn(str(request_error), output.getvalue())

    def test_signature_exception_is_typed(self):
        with patch.dict("sys.modules", {"backend.app.services.xhs_sign.sign_util": None}):
            with self.assertRaises(xhs_service.XHSRequestError) as raised:
                xhs_service._sign_request("not-logged", "/safe", {})
        self.assertEqual(raised.exception.reason, "sign_error")


if __name__ == "__main__":
    unittest.main()
