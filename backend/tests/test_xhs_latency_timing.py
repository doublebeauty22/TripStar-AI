import io
import re
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services import xhs_service


PRIVATE_MARKERS = (
    "Private City", "Private Query", "private-note", "private-token",
    "https://private.invalid", "private-provider-body",
)


def _timing_lines(output: str) -> list[str]:
    return [
        line for line in output.splitlines()
        if line.startswith("event=xhs_stage_timing ")
    ]


def _stage_lines(output: str, stage: str) -> list[str]:
    return [line for line in _timing_lines(output) if f"stage={stage} " in line]


class _Client:
    def __init__(self, *, detail=None, search_error=None, detail_error=None):
        self.detail = detail or {}
        self.search_error = search_error
        self.detail_error = detail_error

    def search_notes(self, **_kwargs):
        if self.search_error:
            raise self.search_error
        return {
            "success": True,
            "data": {"items": [{
                "model_type": "note",
                "id": "private-note",
                "xsec_token": "private-token",
                "note_card": {"display_title": "Private Title"},
            }]},
        }

    def get_note_detail(self, *_args):
        if self.detail_error:
            raise self.detail_error
        return self.detail


def _completion():
    content = (
        '[{"name":"Private Title","identity_text":"Private Title",'
        '"name_zh":"Private Title","name_en":"Private Title",'
        '"recommendation":"Private evidence text","duration":60,'
        '"reservation_required":false,"reservation_tips":"",'
        '"evidence_ids":["private-note"],"evidence_support":['
        '{"evidence_id":"private-note","identity_quote":"Private Title",'
        '"recommendation_quote":"Private evidence text"}]}]'
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class XHSResearchTimingTests(unittest.TestCase):
    def test_success_emits_search_detail_extraction_and_geocoding(self):
        detail = {"data": {"items": [{"note_card": {
            "desc": "Private evidence text",
        }}]}}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=_Client(detail=detail)
        ), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            return_value=_completion(),
        ), patch.object(xhs_service, "geocode_amap", return_value=None):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(result.status, "available")
        rendered = output.getvalue()
        for stage in (
            "research_search", "research_detail", "research_llm_extraction",
            "research_geocoding",
        ):
            lines = _stage_lines(rendered, stage)
            self.assertEqual(len(lines), 1)
            self.assertIn("success=true", lines[0])
            self.assertRegex(lines[0], r"duration_ms=\d+")
        self.assertFalse(_stage_lines(rendered, "research_ssr"))
        self._assert_timing_is_private(rendered)

    def test_detail_failure_and_ssr_success_are_separate(self):
        state = {
            "note": {"noteDetailMap": {"private-note": {
                "note": {"desc": "Private evidence text"},
            }}},
        }
        response = SimpleNamespace(
            text=f"window.__INITIAL_STATE__={__import__('json').dumps(state)}</script>"
        )
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client",
            return_value=_Client(detail_error=RuntimeError("private-provider-body")),
        ), patch.object(xhs_service.httpx, "get", return_value=response), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            return_value=_completion(),
        ), patch.object(xhs_service, "geocode_amap", return_value=None):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(result.status, "available")
        self.assertIn("success=false", _stage_lines(output.getvalue(), "research_detail")[0])
        self.assertIn("success=true", _stage_lines(output.getvalue(), "research_ssr")[0])
        self._assert_timing_is_private(output.getvalue())

    def test_ssr_handled_failure_is_timed_false_and_flow_remains_fail_open(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client",
            return_value=_Client(detail_error=RuntimeError("private-provider-body")),
        ), patch.object(
            xhs_service.httpx, "get", side_effect=RuntimeError("private-provider-body")
        ), patch("backend.app.services.llm_service.create_chat_completion") as completion:
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(result.reason, "detail_unavailable")
        completion.assert_not_called()
        self.assertIn("success=false", _stage_lines(output.getvalue(), "research_detail")[0])
        self.assertIn("success=false", _stage_lines(output.getvalue(), "research_ssr")[0])
        self.assertFalse(_stage_lines(output.getvalue(), "research_llm_extraction"))
        self.assertFalse(_stage_lines(output.getvalue(), "research_geocoding"))
        self._assert_timing_is_private(output.getvalue())

    def test_llm_failure_is_timed_false_and_existing_result_is_preserved(self):
        detail = {"data": {"items": [{"note_card": {"desc": "Private evidence text"}}]}}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=_Client(detail=detail)
        ), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            side_effect=RuntimeError("private-provider-body"),
        ):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(result.reason, "extraction_failed")
        self.assertIn(
            "success=false",
            _stage_lines(output.getvalue(), "research_llm_extraction")[0],
        )
        self.assertFalse(_stage_lines(output.getvalue(), "research_geocoding"))
        self._assert_timing_is_private(output.getvalue())

    def _assert_timing_is_private(self, output: str) -> None:
        timing = "\n".join(_timing_lines(output))
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, timing)
        for line in _timing_lines(output):
            self.assertRegex(
                line,
                r"^event=xhs_stage_timing stage=[a-z_]+ duration_ms=\d+ success=(true|false)$",
            )


class XHSImageTimingTests(unittest.TestCase):
    def test_search_and_detail_success_skip_ssr(self):
        detail = {"data": {"items": [{"note_card": {"image_list": [{
            "info_list": [{"url": "https://private.invalid/image"}],
        }]}}]}}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=_Client(detail=detail)
        ):
            result = xhs_service.get_xhs_photo_sync("Private Query")

        self.assertEqual(result, "https://private.invalid/image")
        self.assertIn("success=true", _stage_lines(output.getvalue(), "image_search")[0])
        self.assertIn("success=true", _stage_lines(output.getvalue(), "image_detail")[0])
        self.assertFalse(_stage_lines(output.getvalue(), "image_ssr"))
        self._assert_timing_is_private(output.getvalue())

    def test_detail_failure_and_ssr_success_are_separate(self):
        state = {"note": {"noteDetailMap": {"private-note": {"note": {
            "imageList": [{"url": "https://private.invalid/image"}],
        }}}}}
        response = SimpleNamespace(
            text=f"window.__INITIAL_STATE__={__import__('json').dumps(state)}</script>"
        )
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client",
            return_value=_Client(detail_error=RuntimeError("private-provider-body")),
        ), patch.object(xhs_service.httpx, "get", return_value=response):
            result = xhs_service.get_xhs_photo_sync("Private Query")

        self.assertEqual(result, "https://private.invalid/image")
        self.assertIn("success=false", _stage_lines(output.getvalue(), "image_detail")[0])
        self.assertIn("success=true", _stage_lines(output.getvalue(), "image_ssr")[0])
        self._assert_timing_is_private(output.getvalue())

    def test_search_exception_is_unchanged_and_skips_later_stages(self):
        marker = RuntimeError("private-provider-body")
        output = io.StringIO()
        with self.assertRaises(RuntimeError) as raised:
            with redirect_stdout(output), patch.object(
                xhs_service, "get_xhs_client", return_value=_Client(search_error=marker)
            ):
                xhs_service.get_xhs_photo_sync("Private Query")

        self.assertIs(raised.exception, marker)
        self.assertIn("success=false", _stage_lines(output.getvalue(), "image_search")[0])
        self.assertFalse(_stage_lines(output.getvalue(), "image_detail"))
        self.assertFalse(_stage_lines(output.getvalue(), "image_ssr"))
        self._assert_timing_is_private(output.getvalue())

    def _assert_timing_is_private(self, output: str) -> None:
        timing = "\n".join(_timing_lines(output))
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, timing)
        for line in _timing_lines(output):
            self.assertRegex(
                line,
                r"^event=xhs_stage_timing stage=[a-z_]+ duration_ms=\d+ success=(true|false)$",
            )


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
