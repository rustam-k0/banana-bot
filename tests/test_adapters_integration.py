import base64
import unittest

from banana_bot.adapters.google import GoogleAdapter
from banana_bot.adapters.openai import OpenAIAdapter


class MockHTTPClient:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    async def request_json(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.payload

    async def download(self, url):
        return b"downloaded"


class AdapterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_responses_contract_and_usage(self):
        http = MockHTTPClient({"output_text": "answer", "usage": {"input_tokens": 12, "output_tokens": 4}})
        adapter = OpenAIAdapter(http, "not-a-real-key")
        result = await adapter.chat("gpt-5.6-luna", [{"role": "user", "content": "hello"}], 700)
        self.assertEqual(result.text, "answer")
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertTrue(http.requests[0][1].endswith("/responses"))
        self.assertEqual(http.requests[0][2]["json"]["max_output_tokens"], 700)

    async def test_google_image_payload_is_decoded(self):
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        http = MockHTTPClient({"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]})
        adapter = GoogleAdapter(http, "not-a-real-key")
        result = await adapter.image("backup-image", "banana", {})
        self.assertEqual(result.content, b"image-bytes")
        self.assertIn("backup-image:generateContent", http.requests[0][1])


if __name__ == "__main__":
    unittest.main()
