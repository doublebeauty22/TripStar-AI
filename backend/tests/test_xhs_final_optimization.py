import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services import xhs_service


class _BoundedExtractionClient:
    def search_notes(self, **_kwargs):
        return {"data": {"items": [
            {
                "model_type": "note",
                "id": f"note-{index}",
                "xsec_token": f"token-{index}",
                "note_card": {"display_title": f"Place {index}"},
            }
            for index in range(6)
        ]}}

    def get_note_detail(self, note_id, _token):
        index = int(note_id.rsplit("-", 1)[1])
        desc = f"Place {index} quote {index} " + (f"tail-{index}" * 120)
        return {"data": {"items": [{"note_card": {"desc": desc}}]}}


def _four_item_completion():
    items = []
    for index in range(4):
        name = f"Place {index}"
        items.append({
            "name": name,
            "identity_text": name,
            "name_zh": name,
            "name_en": name,
            "evidence_ids": [f"note-{index}"],
            "evidence_support": [{
                "evidence_id": f"note-{index}",
                "identity_quote": name,
                "recommendation_quote": f"quote {index}",
            }],
        })
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(items)),
    )])


class XHSExtractionWorkloadTests(unittest.TestCase):
    def test_four_notes_are_ordered_bounded_and_fit_explicit_output_limit(self):
        captured = {}

        def complete(**kwargs):
            captured.update(kwargs)
            return _four_item_completion()

        with patch.object(
            xhs_service, "get_xhs_client", return_value=_BoundedExtractionClient()
        ), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake-model")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            side_effect=complete,
        ), patch.object(xhs_service, "geocode_amap", return_value=None):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        prompt = captured["messages"][0]["content"]
        self.assertEqual([item.note_id for item in result.evidence], [
            "note-0", "note-1", "note-2", "note-3",
        ])
        self.assertEqual(len(result.extracted_items), 4)
        self.assertNotIn("note-4", prompt)
        self.assertNotIn("note-5", prompt)
        for index in range(4):
            section = prompt.split(f"note_id: note-{index}", 1)[1]
            if index < 3:
                section = section.split(f"note_id: note-{index + 1}", 1)[0]
            extraction_text = section.split("正文内容: ", 1)[1].split("\n来源:", 1)[0]
            self.assertLessEqual(len(extraction_text), xhs_service._XHS_EXTRACTION_NOTE_CHARS)
        self.assertNotIn('"recommendation":', prompt)
        self.assertNotIn('"duration":', prompt)
        self.assertNotIn('"reservation_required":', prompt)
        self.assertNotIn('"reservation_tips":', prompt)
        for required in (
            '"name"', '"identity_text"', '"name_zh"', '"name_en"',
            '"evidence_ids"', '"evidence_support"', '"identity_quote"',
            '"recommendation_quote"',
        ):
            self.assertIn(required, prompt)
        self.assertEqual(captured["max_tokens"], xhs_service._XHS_EXTRACTION_MAX_TOKENS)
        self.assertEqual(
            captured["stage_max_token_exposure"],
            xhs_service._XHS_EXTRACTION_MAX_TOKENS,
        )

    def test_invalid_or_truncated_json_still_fails_open(self):
        completion = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='[{"name":"incomplete"'),
        )])
        with patch.object(
            xhs_service, "get_xhs_client", return_value=_BoundedExtractionClient()
        ), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake-model")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            return_value=completion,
        ):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "extraction_failed")


class XHSCoverSelectionTests(unittest.TestCase):
    @staticmethod
    def detail(url, width=None, height=None):
        image = {"info_list": [{"url": url}]}
        if width is not None:
            image["width"] = width
        if height is not None:
            image["height"] = height
        return image

    @staticmethod
    def ssr(url, width=None, height=None):
        image = {"urlDefault": url}
        if width is not None:
            image["width"] = width
        if height is not None:
            image["height"] = height
        return image

    def test_later_landscape_is_preferred_over_first_portrait(self):
        images = [self.detail("portrait", 600, 1200), self.detail("landscape", 1200, 800)]
        self.assertEqual(
            xhs_service._select_cover_image(images, xhs_service._detail_image_url),
            "landscape",
        )

    def test_first_landscape_remains_selected(self):
        images = [self.detail("first", 1200, 800), self.detail("second", 1400, 900)]
        self.assertEqual(
            xhs_service._select_cover_image(images, xhs_service._detail_image_url),
            "first",
        )

    def test_missing_dimensions_preserve_provider_order(self):
        images = [self.detail("unknown"), self.detail("portrait", 600, 1200)]
        self.assertEqual(
            xhs_service._select_cover_image(images, xhs_service._detail_image_url),
            "unknown",
        )

    def test_all_reliably_portrait_candidates_return_empty(self):
        images = [self.detail("one", 600, 1200), self.detail("two", 700, 1000)]
        self.assertEqual(
            xhs_service._select_cover_image(images, xhs_service._detail_image_url),
            "",
        )

    def test_detail_url_precedence_is_preserved(self):
        image = {
            "width": 1200, "height": 800,
            "info_list": [{"url": "standard"}, {"url": "high"}],
            "url_default": "fallback",
        }
        self.assertEqual(xhs_service._detail_image_url(image), "high")

    def test_ssr_uses_the_same_cover_rule(self):
        images = [self.ssr("portrait", 600, 1200), self.ssr("landscape", 1200, 800)]
        self.assertEqual(
            xhs_service._select_cover_image(images, xhs_service._ssr_image_url),
            "landscape",
        )


if __name__ == "__main__":
    unittest.main()
