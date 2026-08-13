import unittest

from backend.app.models.schemas import PreferenceProfile, TripRequest


class PreferenceSchemaTests(unittest.TestCase):
    def test_legacy_trip_request_remains_valid(self):
        request = TripRequest(
            city="东京",
            start_date="2026-09-01",
            end_date="2026-09-03",
            travel_days=3,
            transportation="公共交通",
            accommodation="经济型酒店",
            preferences=["美食"],
        )
        self.assertIsNone(request.preference_profile)
        self.assertEqual(request.cities[0].city, "东京")

    def test_profile_accepts_unknown_earliest_start(self):
        profile = PreferenceProfile(
            party_type="with_parents",
            party_size=3,
            budget_cny=5000,
            pace="balanced",
            interests=["美食"],
            special_requirements="不想早起",
            constraints={"avoid_early_start": True, "earliest_start_time": None},
        )
        self.assertTrue(profile.constraints.avoid_early_start)
        self.assertIsNone(profile.constraints.earliest_start_time)


if __name__ == "__main__":
    unittest.main()
