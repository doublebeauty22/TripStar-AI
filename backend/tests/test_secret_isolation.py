import asyncio
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from backend.app import config
from backend.app.api.routes.settings import RuntimeSettingsPayload, get_settings
from backend.app.services import xhs_service
from backend.app.services.google_map_service import GoogleMapService
from backend.app.services.amap_service import AmapService


SERVER_SECRET_FIELDS = {
    "openai_api_key",
    "xhs_cookie",
    "google_maps_api_key",
    "google_maps_server_api_key",
    "amap_web_service_key",
}


class RuntimeSecretIsolationTests(unittest.TestCase):
    def test_runtime_file_cannot_override_openai_environment_setting(self):
        original = config.settings.openai_api_key
        try:
            config.settings.openai_api_key = "fake-env-openai-key"
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "runtime_settings.json"
                path.write_text(
                    json.dumps({
                        "openai_api_key": "fake-runtime-openai-key",
                        "openai_model": "safe-runtime-model",
                    }),
                    encoding="utf-8",
                )
                with patch.object(config, "_RUNTIME_SETTINGS_FILE", path):
                    overrides = config._load_runtime_overrides()
                    config._apply_runtime_overrides(overrides)

            self.assertEqual(config.settings.openai_api_key, "fake-env-openai-key")
            self.assertNotIn("openai_api_key", overrides)
        finally:
            config.settings.openai_api_key = original

    def test_runtime_persistence_drops_every_server_secret(self):
        payload = {
            "openai_api_key": "fake-openai-key",
            "xhs_cookie": "fake-cookie",
            "google_maps_api_key": "fake-google-key",
            "google_maps_server_api_key": "fake-google-server-key",
            "amap_web_service_key": "fake-amap-key",
            "openai_model": "safe-runtime-model",
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime_settings.json"
            with patch.object(config, "_RUNTIME_SETTINGS_FILE", path):
                config._persist_runtime_overrides(payload)
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(persisted, {"openai_model": "safe-runtime-model"})
        self.assertTrue(SERVER_SECRET_FIELDS.isdisjoint(config._RUNTIME_SETTING_KEYS))

    def test_settings_get_returns_presence_metadata_not_values(self):
        originals = {
            "openai_api_key": config.settings.openai_api_key,
            "xhs_cookie": config.settings.xhs_cookie,
            "google_maps_server_api_key": config.settings.google_maps_server_api_key,
            "amap_web_service_key": config.settings.amap_web_service_key,
        }
        fake_values = {
            "openai_api_key": "fake-openai-key",
            "xhs_cookie": "fake-cookie",
            "google_maps_server_api_key": "fake-google-server-key",
            "amap_web_service_key": "fake-amap-key",
        }
        try:
            for field, value in fake_values.items():
                setattr(config.settings, field, value)
            response = asyncio.run(get_settings())
            serialized = json.dumps(response)
            for field in SERVER_SECRET_FIELDS:
                self.assertNotIn(field, response["data"])
            for value in fake_values.values():
                self.assertNotIn(value, serialized)
            for field in (
                "openai_configured", "xhs_configured",
                "google_server_configured", "amap_server_configured",
            ):
                self.assertTrue(response["data"][field])
        finally:
            for field, value in originals.items():
                setattr(config.settings, field, value)

    def test_settings_payload_rejects_server_secret_mutation(self):
        for field in SERVER_SECRET_FIELDS:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                RuntimeSettingsPayload.model_validate({field: "fake-secret"})


class BuildContextIsolationTests(unittest.TestCase):
    def test_git_and_docker_ignores_cover_secret_files(self):
        root = Path(__file__).resolve().parents[2]
        dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
        for pattern in (
            ".env", "**/.env", "**/.env.*",
            "runtime_settings.json", "**/runtime_settings.json",
        ):
            self.assertIn(pattern, dockerignore.splitlines())
        self.assertIn("!**/.env.example", dockerignore.splitlines())

        for path in (
            ".env", "backend/.env", "backend/.env.production",
            "frontend/.env", "backend/runtime_settings.json",
            "runtime_settings.json", "logs/app.log", "debug.log",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", "--", path],
                cwd=root,
                check=False,
            )
            self.assertEqual(result.returncode, 0, path)

        example = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", "backend/.env.example"],
            cwd=root,
            check=False,
        )
        self.assertNotEqual(example.returncode, 0)


class SecretSafeHttpLoggingTests(unittest.TestCase):
    def test_google_query_key_is_absent_from_http_error_log(self):
        fake_key = "fake-google-query-key-for-test"
        request = httpx.Request(
            "GET", f"https://maps.invalid/geocode?key={fake_key}"
        )
        error = httpx.HTTPStatusError(
            "request failed",
            request=request,
            response=httpx.Response(403, request=request),
        )
        service = GoogleMapService(fake_key)
        service._client = SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
            close=lambda: None,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertIsNone(service.geocode("safe-address"))
        rendered = output.getvalue()
        self.assertNotIn(fake_key, rendered)
        self.assertNotIn("https://", rendered)
        self.assertIn("endpoint=geocoding", rendered)
        self.assertIn("status=403", rendered)

    def test_amap_query_key_is_absent_from_http_error_log(self):
        fake_key = "fake-amap-query-key-for-test"
        request = httpx.Request(
            "GET", f"https://amap.invalid/place?key={fake_key}"
        )
        error = httpx.HTTPStatusError(
            "request failed",
            request=request,
            response=httpx.Response(403, request=request),
        )
        service = AmapService(fake_key, SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
            close=lambda: None,
        ))
        output = io.StringIO()
        with redirect_stdout(output):
            result = service.geocode("safe-address", "safe-city")
            self.assertFalse(result.data_available)
        rendered = output.getvalue()
        self.assertNotIn(fake_key, rendered)
        self.assertNotIn("https://", rendered)
        self.assertIn("provider=amap", rendered)
        self.assertIn("status=403", rendered)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
