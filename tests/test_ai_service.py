import unittest

from banana_bot.config import load_config
from banana_bot.domain import TextResult, Usage
from banana_bot.http import ProviderError
from banana_bot.memory import ConversationMemory
from banana_bot.observability import Metrics
from banana_bot.services.ai import AIService


class FakeAdapter:
    def __init__(self, provider, result=None, error=None):
        self.provider, self.result, self.error = provider, result, error
        self.calls = []

    async def chat(self, model, messages, max_tokens):
        self.calls.append((model, messages, max_tokens))
        if self.error:
            raise self.error
        return self.result or TextResult("ok", self.provider, model, Usage(10, 5))


class AIServiceTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return load_config({
            "TELEGRAM_BOT_TOKEN": "t", "OPENAI_API_KEY": "o", "GOOGLE_API_KEY": "g",
            "XAI_API_KEY": "x", "HTTP_RETRIES": "0",
        }, validate=True)

    async def test_fast_chat_uses_luna_and_short_limit(self):
        openai = FakeAdapter("openai")
        service = AIService(self.config(), {"openai": openai, "google": FakeAdapter("google"), "xai": FakeAdapter("xai")}, ConversationMemory(), Metrics())
        result = await service.chat(7, "hello", "fast")
        self.assertEqual(result.text, "ok")
        self.assertEqual(openai.calls[0][0], "gpt-5.6-luna")
        self.assertEqual(openai.calls[0][2], self.config().max_output_tokens)

    async def test_provider_error_falls_back_without_prompt_logging(self):
        openai = FakeAdapter("openai", error=ProviderError(503, "down"))
        google = FakeAdapter("google", result=TextResult("backup", "google", "gemini"))
        service = AIService(self.config(), {"openai": openai, "google": google, "xai": FakeAdapter("xai")}, ConversationMemory(), Metrics())
        result = await service.chat(7, "secret request", "balanced")
        self.assertEqual(result.provider, "google")
        self.assertEqual(len(google.calls), 1)

    async def test_safety_error_does_not_fallback(self):
        openai = FakeAdapter("openai", error=ProviderError(400, "blocked", "content_policy_violation"))
        google = FakeAdapter("google")
        service = AIService(self.config(), {"openai": openai, "google": google, "xai": FakeAdapter("xai")}, ConversationMemory(), Metrics())
        with self.assertRaises(ProviderError):
            await service.chat(7, "text", "complex")
        self.assertFalse(google.calls)


if __name__ == "__main__":
    unittest.main()
