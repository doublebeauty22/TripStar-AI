import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.models.schemas import PreferenceParseRequest
from backend.app.services.preference_service import parse_preference_profile


FAKE_CONTENT = """{
      "avoid_early_start": true,
      "earliest_start_time": null,
      "mobility_notes": ["减少长距离步行"],
      "food_notes": [],
      "other_notes": [],
      "inferred_interests": ["拍照", "美食"],
      "parsing_notes": []
    }"""


class _FakeLlm:
    model = "test-model"

    @staticmethod
    def _create(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=FAKE_CONTENT))]
        )

    _client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


class PreferenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.request = PreferenceParseRequest(
            party_type="with_parents",
            party_size=3,
            budget_cny=5000,
            pace="balanced",
            interests=["美食"],
            special_requirements="不想早起，妈妈膝盖不好，喜欢拍照",
        )

    @patch("backend.app.services.preference_service._get_llm", return_value=_FakeLlm())
    def test_explicit_fields_win_and_missing_time_stays_null(self, _mock_llm):
        profile, used_llm, _message = parse_preference_profile(self.request)

        self.assertTrue(used_llm)
        self.assertEqual(profile.party_type, "with_parents")
        self.assertEqual(profile.party_size, 3)
        self.assertEqual(profile.budget_cny, 5000)
        self.assertEqual(profile.interests, ["美食"])
        self.assertEqual(profile.inferred_interests, ["拍照"])
        self.assertTrue(profile.constraints.avoid_early_start)
        self.assertIsNone(profile.constraints.earliest_start_time)
        self.assertTrue(any("选择一个具体" in note for note in profile.parsing_notes))

    @patch("backend.app.services.preference_service._get_llm", side_effect=RuntimeError("offline"))
    def test_parser_failure_returns_non_blocking_fallback(self, _mock_llm):
        profile, used_llm, message = parse_preference_profile(self.request)

        self.assertFalse(used_llm)
        self.assertEqual(profile.party_type, "with_parents")
        self.assertEqual(profile.special_requirements, self.request.special_requirements)
        self.assertIn("显式字段", message)
        self.assertTrue(profile.parsing_notes)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
