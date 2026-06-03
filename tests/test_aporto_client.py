"""
Tests for the optional Aporto wrapper (aporto_client.py).

Run from the repository root:
    python3 -m unittest discover -s tests -v

These tests do NOT require an APORTO_API_KEY — they mock the HTTP layer
and verify that the wrapper sends the correct skill IDs, params, and
attribution header.
"""
import asyncio
import base64
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# Make sure aporto_client can be imported when running tests from repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeStatusMsg:
    """Stand-in for aiogram's Message; records the last edit_text call."""
    def __init__(self):
        self.last_text: str | None = None
    async def edit_text(self, text: str):
        self.last_text = text


def _runner(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAportoClientBasics(unittest.TestCase):
    def test_integration_id_is_placeholder(self):
        from aporto_client import INTEGRATION_ID
        self.assertEqual(INTEGRATION_ID, "YOUR_APORTO_INTEGRATION_ID")

    def test_pinned_skill_ids_are_present(self):
        from aporto_client import APORTO_SKILLS
        for k in ("image_gen_flash", "image_gen_pro", "image_edit", "transcribe"):
            self.assertIn(k, APORTO_SKILLS, f"missing skill key: {k}")
            self.assertIsInstance(APORTO_SKILLS[k], int)
            self.assertGreater(APORTO_SKILLS[k], 0)

    def test_build_headers_contain_integration_id(self):
        from aporto_client import _build_headers, INTEGRATION_ID
        h = _build_headers("abc")
        self.assertEqual(h["Authorization"], "Bearer abc")
        self.assertEqual(h["X-Aporto-Integration-Id"], INTEGRATION_ID)
        self.assertIn("User-Agent", h)  # required to pass Cloudflare WAF
        self.assertEqual(h["Content-Type"], "application/json")

    def test_get_aporto_key_raises_when_missing(self):
        from aporto_client import _get_aporto_key, AportoConfigError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AportoConfigError):
                _get_aporto_key()

    def test_get_aporto_key_returns_value(self):
        from aporto_client import _get_aporto_key
        with patch.dict(os.environ, {"APORTO_API_KEY": "test-key"}):
            self.assertEqual(_get_aporto_key(), "test-key")


class TestExtractText(unittest.TestCase):
    def test_data_content_real_aporto_shape(self):
        """Real Aporto Gemini 2.5 Flash responses put text in result.data.content."""
        from aporto_client import _extract_text
        result = {
            "success": True,
            "data": {"content": "Hi, this is a test.", "model": "gemini-2.5-flash"},
            "status": "succeeded",
        }
        self.assertEqual(_extract_text(result), "Hi, this is a test.")

    def test_openai_compatible_choices(self):
        from aporto_client import _extract_text
        result = {"data": {"choices": [{"message": {"content": "openai style"}}]}}
        self.assertEqual(_extract_text(result), "openai style")

    def test_candidates_text(self):
        from aporto_client import _extract_text
        result = {
            "data": {
                "candidates": [
                    {"content": {"parts": [{"text": "first"}, {"text": "second"}]}}
                ]
            }
        }
        self.assertEqual(_extract_text(result), "first")

    def test_raises_when_empty(self):
        from aporto_client import _extract_text, AportoEmptyResultError
        with self.assertRaises(AportoEmptyResultError):
            _extract_text({})


class TestExtractImageBytes(unittest.TestCase):
    def test_artifacts_data_base64(self):
        from aporto_client import _extract_image_bytes
        png_bytes = b"\x89PNG fake"
        result = {
            "artifacts": [{"data": base64.b64encode(png_bytes).decode("ascii")}],
        }
        self.assertEqual(_extract_image_bytes(result), png_bytes)

    def test_raises_when_no_image(self):
        from aporto_client import _extract_image_bytes, AportoEmptyResultError
        with self.assertRaises(AportoEmptyResultError):
            _extract_image_bytes({"artifacts": []})


class TestHighLevelWrappers(unittest.TestCase):
    def _set_env(self):
        os.environ["APORTO_API_KEY"] = "test-key"

    def test_generate_image_missing_key(self):
        from aporto_client import generate_image_from_text
        with patch.dict(os.environ, {}, clear=True):
            status = FakeStatusMsg()
            result = _runner(generate_image_from_text("a cat", "FLASH", status, "EN"))
            self.assertIsNone(result)
            self.assertIsNotNone(status.last_text)

    def test_generate_image_pro_uses_pro_skill(self):
        from aporto_client import generate_image_from_text
        self._set_env()
        run_skill_mock = AsyncMock(return_value={
            "artifacts": [{"data": base64.b64encode(b"\x89PNG real").decode("ascii")}]
        })
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            result = _runner(generate_image_from_text("a cat", "PRO", status, "EN"))
            self.assertEqual(result, b"\x89PNG real")
            args, _ = run_skill_mock.call_args
            self.assertEqual(args[0], 146)  # PRO skill
            self.assertEqual(args[1], {"prompt": "a cat"})

    def test_generate_image_flash_uses_flash_skill(self):
        from aporto_client import generate_image_from_text
        self._set_env()
        run_skill_mock = AsyncMock(return_value={
            "artifacts": [{"data": base64.b64encode(b"\x89PNG f").decode("ascii")}]
        })
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            _runner(generate_image_from_text("hi", "FLASH", status, "EN"))
            args, _ = run_skill_mock.call_args
            self.assertEqual(args[0], 96)  # FLASH skill

    def test_generate_image_api_error_returns_none(self):
        from aporto_client import generate_image_from_text, AportoAPIError
        self._set_env()
        run_skill_mock = AsyncMock(side_effect=AportoAPIError("rate limit", status=429))
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            result = _runner(generate_image_from_text("x", "FLASH", status, "EN"))
            self.assertIsNone(result)
            self.assertIsNotNone(status.last_text)

    def test_edit_image_sends_base64_image(self):
        from aporto_client import edit_image_with_prompt
        self._set_env()
        img = b"\xff\xd8\xff jpeg-bytes"
        run_skill_mock = AsyncMock(return_value={
            "artifacts": [{"data": base64.b64encode(b"\x89PNG edited").decode("ascii")}]
        })
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            result = _runner(edit_image_with_prompt(img, "make it blue", "PRO", status, "EN"))
            self.assertEqual(result, b"\x89PNG edited")
            args, kwargs = run_skill_mock.call_args
            self.assertEqual(args[0], 249)  # image_edit skill
            self.assertEqual(kwargs.get("intent"), "edit image according to text prompt")
            self.assertIn("image", args[1])
            self.assertEqual(args[1]["image"], base64.b64encode(img).decode("ascii"))
            self.assertEqual(args[1]["prompt"], "make it blue")
            self.assertEqual(args[1]["image_mime"], "image/jpeg")

    def test_transcribe_audio_sends_multimodal_payload(self):
        from aporto_client import transcribe_audio
        self._set_env()
        audio = b"OggS fake"
        run_skill_mock = AsyncMock(return_value={"data": {"content": "hello world"}})
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            result = _runner(transcribe_audio(audio, "FLASH", status, "EN"))
            self.assertEqual(result, "hello world")
            args, kwargs = run_skill_mock.call_args
            self.assertEqual(args[0], 294)  # transcribe skill
            self.assertEqual(kwargs.get("intent"), "transcribe voice message to text")
            params = args[1]
            self.assertIn("messages", params)
            content = params["messages"][0]["content"]
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "input_audio")
            self.assertEqual(content[1]["input_audio"]["format"], "ogg")
            self.assertEqual(
                content[1]["input_audio"]["data"],
                base64.b64encode(audio).decode("ascii"),
            )
            # No "model" key — Aporto selects the model from the skillId.
            self.assertNotIn("model", params)

    def test_transcribe_audio_uses_russian_prompt(self):
        from aporto_client import transcribe_audio
        self._set_env()
        run_skill_mock = AsyncMock(return_value={"data": {"content": "привет"}})
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            _runner(transcribe_audio(b"OggS", "FLASH", status, "RU"))
            args, _ = run_skill_mock.call_args
            self.assertIn("Транскрибируй", args[1]["messages"][0]["content"][0]["text"])

    def test_transcribe_audio_empty_result_returns_none(self):
        from aporto_client import transcribe_audio
        self._set_env()
        run_skill_mock = AsyncMock(return_value={"data": {"content": ""}})
        with patch("aporto_client.run_skill_async", run_skill_mock):
            status = FakeStatusMsg()
            result = _runner(transcribe_audio(b"OggS", "FLASH", status, "EN"))
            self.assertIsNone(result)
            self.assertIsNotNone(status.last_text)


class TestConfigAcceptsAporto(unittest.TestCase):
    """Verify config.py accepts APORTO_API_KEY as an optional field."""

    def test_aporto_field_optional(self):
        from config import AppConfig
        # No APORTO_API_KEY in env -> field stays None
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x", "GOOGLE_API_KEY": "y"}, clear=True):
            from config import load_config
            c = load_config()
            self.assertIsNone(c.aporto_api_key)
            self.assertEqual(c.google_api_key, "y")

    def test_aporto_field_reads(self):
        with patch.dict(os.environ, {"APORTO_API_KEY": "aportok", "TELEGRAM_BOT_TOKEN": "t"}, clear=True):
            from config import load_config
            c = load_config()
            self.assertEqual(c.aporto_api_key, "aportok")
            self.assertIsNone(c.google_api_key)


if __name__ == "__main__":
    unittest.main()
