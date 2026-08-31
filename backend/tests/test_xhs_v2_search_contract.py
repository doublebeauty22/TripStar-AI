import json
import io
import re
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

from backend.app.services import xhs_service


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"success": True, "data": {"items": []}}


class _BusinessRejectedResponse(_Response):
    def json(self):
        return {"success": False, "code": -100, "msg": "not-logged"}


class XHSSearchV2ContractTests(unittest.TestCase):
    def _run_search(self, client, keyword="private-query"):
        signed = []
        posted = []

        def fake_sign(cookie, api, data):
            signed.append((api, dict(data)))
            return (
                {
                    "authority": "edith.xiaohongshu.com",
                    "origin": "https://www.xiaohongshu.com",
                    "referer": "https://www.xiaohongshu.com/",
                },
                {"private-cookie": "not-logged"},
                json.dumps(data, separators=(",", ":"), ensure_ascii=False),
            )

        def fake_post(url, **kwargs):
            posted.append((url, kwargs))
            return _Response()

        with patch.object(xhs_service, "_sign_request", side_effect=fake_sign), patch.object(
            xhs_service.requests, "post", side_effect=fake_post
        ):
            client.search_notes(keyword)
        return signed[0], posted[0]

    def test_search_uses_exact_v2_contract_and_signs_transmitted_payload(self):
        client = xhs_service.XhsNativeClient("private-cookie")

        (signed_path, signed_payload), (url, request) = self._run_search(client)
        transmitted_payload = json.loads(request["data"].decode("utf-8"))

        self.assertEqual(url, "https://so.xiaohongshu.com/api/sns/web/v2/search/notes")
        self.assertEqual(signed_path, "/api/sns/web/v2/search/notes")
        self.assertEqual(signed_payload, transmitted_payload)
        self.assertEqual(
            set(transmitted_payload),
            {
                "keyword", "page", "page_size", "search_id", "sort",
                "note_type", "ext_flags", "geo", "image_formats", "session_id",
            },
        )
        self.assertNotIn("filters", transmitted_payload)
        self.assertEqual(transmitted_payload["image_formats"], ["jpg"])
        self.assertEqual(request["headers"]["authority"], "so.xiaohongshu.com")
        self.assertEqual(request["headers"]["origin"], "https://www.xiaohongshu.com")
        self.assertEqual(request["headers"]["referer"], "https://www.xiaohongshu.com/")
        self.assertEqual(request["timeout"], 15)
        self.assertNotIn("params", request)

    def test_session_is_uuid4_per_client_and_search_id_changes_per_call(self):
        first = xhs_service.XhsNativeClient("private-cookie")
        second = xhs_service.XhsNativeClient("private-cookie")

        (_, first_payload), _ = self._run_search(first)
        (_, repeated_payload), _ = self._run_search(first)
        (_, second_payload), _ = self._run_search(second)

        session_id = first_payload["session_id"]
        self.assertRegex(
            session_id,
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )
        self.assertEqual(uuid.UUID(session_id).version, 4)
        self.assertEqual(session_id, repeated_payload["session_id"])
        self.assertNotEqual(session_id, second_payload["session_id"])
        self.assertNotEqual(first_payload["search_id"], repeated_payload["search_id"])
        self.assertNotEqual(first_payload["search_id"], session_id)

    def test_search_makes_one_request_without_v1_fallback(self):
        client = xhs_service.XhsNativeClient("private-cookie")
        with patch.object(xhs_service, "_sign_request") as sign, patch.object(
            xhs_service.requests, "post", return_value=_Response()
        ) as post:
            sign.return_value = (
                {"authority": "edith.xiaohongshu.com"}, {},
                '{"success":"not-provider-data"}',
            )
            client.search_notes("private-query")

        self.assertEqual(sign.call_count, 1)
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("/v1/", post.call_args.args[0])

    def test_detail_retains_edith_host_and_authority(self):
        client = xhs_service.XhsNativeClient("private-cookie")
        with patch.object(xhs_service, "_sign_request") as sign, patch.object(
            xhs_service.requests, "post", return_value=_Response()
        ) as post:
            sign.return_value = (
                {"authority": "edith.xiaohongshu.com"}, {}, "{}",
            )
            client.get_note_detail("private-note-id")

        self.assertEqual(sign.call_args.args[1], "/api/sns/web/v1/feed")
        self.assertEqual(post.call_args.args[0], "https://edith.xiaohongshu.com/api/sns/web/v1/feed")
        self.assertEqual(post.call_args.kwargs["headers"]["authority"], "edith.xiaohongshu.com")

    def test_session_id_never_enters_business_rejection_output(self):
        client = xhs_service.XhsNativeClient("private-cookie")
        private_session_id = client._search_session_id
        output = io.StringIO()
        with patch.object(xhs_service, "_sign_request") as sign, patch.object(
            xhs_service.requests, "post", return_value=_BusinessRejectedResponse()
        ), redirect_stdout(output):
            sign.return_value = (
                {"authority": "edith.xiaohongshu.com"}, {}, "{}",
            )
            with self.assertRaises(xhs_service.XHSRequestError) as raised:
                client.search_notes("private-query")

        self.assertEqual(raised.exception.reason, "business_rejected")
        self.assertEqual(raised.exception.business_code, "-100")
        self.assertNotIn(private_session_id, output.getvalue())
        self.assertNotIn(private_session_id, str(raised.exception))


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
