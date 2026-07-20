from __future__ import annotations

import base64
from typing import Any

from banana_bot.domain import AudioResult, ImageResult, TextResult, Usage
from banana_bot.http import AsyncHTTPClient, ProviderError


class GoogleAdapter:
    provider = "google"

    def __init__(self, client: AsyncHTTPClient, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1"):
        self.client, self.api_key, self.base_url = client, api_key, base_url.rstrip("/")

    async def _generate(self, model: str, contents: list[dict[str, Any]], max_tokens: int | None = None) -> dict:
        payload: dict[str, Any] = {"contents": contents}
        if max_tokens:
            payload["generationConfig"] = {"maxOutputTokens": max_tokens}
        return await self.client.request_json("POST", f"{self.base_url}/models/{model}:generateContent", headers={"x-goog-api-key": self.api_key}, json=payload)

    @staticmethod
    def _parts(payload: dict) -> list[dict]:
        return payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])

    async def chat(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> TextResult:
        contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": str(item["content"])}]} for item in messages]
        payload = await self._generate(model, contents, max_tokens)
        text = "".join(part.get("text", "") for part in self._parts(payload))
        if not text:
            raise ProviderError(502, "Provider returned no text")
        usage = payload.get("usageMetadata", {})
        return TextResult(text, self.provider, model, Usage(usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)))

    async def image(self, model: str, prompt: str, options: dict[str, str]) -> ImageResult:
        payload = await self._generate(model, [{"parts": [{"text": prompt}]}])
        return ImageResult(self._image(payload), self.provider, model)

    async def edit_image(self, model: str, image: bytes, prompt: str, options: dict[str, str]) -> ImageResult:
        payload = await self._generate(model, [{"parts": [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(image).decode("ascii")}}, {"text": prompt}]}])
        return ImageResult(self._image(payload), self.provider, model)

    def _image(self, payload: dict) -> bytes:
        for part in self._parts(payload):
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return base64.b64decode(data["data"])
        raise ProviderError(502, "Provider returned no image")

    async def transcribe(self, model: str, audio: bytes) -> TextResult:
        payload = await self._generate(model, [{"parts": [{"inlineData": {"mimeType": "audio/ogg", "data": base64.b64encode(audio).decode("ascii")}}, {"text": "Transcribe this voice message. Return only the recognized text."}]}])
        return TextResult("".join(part.get("text", "") for part in self._parts(payload)).strip(), self.provider, model)

    async def analyze_file(self, model: str, content: bytes, mime_type: str, prompt: str, max_tokens: int) -> TextResult:
        payload = await self._generate(model, [{"parts": [{"inlineData": {"mimeType": mime_type, "data": base64.b64encode(content).decode("ascii")}}, {"text": prompt}]}], max_tokens)
        text = "".join(part.get("text", "") for part in self._parts(payload))
        return TextResult(text, self.provider, model)

    async def synthesize(self, model: str, text: str, voice: str) -> AudioResult:
        raise ProviderError(400, "Google speech synthesis is not configured")
