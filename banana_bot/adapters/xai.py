from __future__ import annotations

import base64

from banana_bot.domain import ImageResult, TextResult, Usage
from banana_bot.http import AsyncHTTPClient, ProviderError


class XAIAdapter:
    provider = "xai"

    def __init__(self, client: AsyncHTTPClient, api_key: str, base_url: str = "https://api.x.ai/v1"):
        self.client, self.base_url = client, base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def chat(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> TextResult:
        payload = await self.client.request_json("POST", f"{self.base_url}/chat/completions", headers=self.headers, json={"model": model, "messages": messages, "max_tokens": max_tokens})
        text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise ProviderError(502, "Provider returned no text")
        usage = payload.get("usage", {})
        return TextResult(text, self.provider, model, Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))

    async def _image_bytes(self, payload: dict) -> bytes:
        data = payload.get("data") or []
        if not data:
            raise ProviderError(502, "Provider returned no image")
        if data[0].get("b64_json"):
            return base64.b64decode(data[0]["b64_json"])
        if data[0].get("url"):
            return await self.client.download(data[0]["url"])
        raise ProviderError(502, "Provider returned unsupported image data")

    async def image(self, model: str, prompt: str, options: dict[str, str]) -> ImageResult:
        payload = await self.client.request_json("POST", f"{self.base_url}/images/generations", headers=self.headers, json={"model": model, "prompt": prompt, "response_format": "b64_json", **options})
        return ImageResult(await self._image_bytes(payload), self.provider, model)

    async def edit_image(self, model: str, image: bytes, prompt: str, options: dict[str, str]) -> ImageResult:
        encoded = base64.b64encode(image).decode("ascii")
        payload = await self.client.request_json("POST", f"{self.base_url}/images/edits", headers=self.headers, json={"model": model, "prompt": prompt, "image": {"url": f"data:image/jpeg;base64,{encoded}", "type": "image_url"}, **options})
        return ImageResult(await self._image_bytes(payload), self.provider, model)

    async def transcribe(self, model: str, audio: bytes) -> TextResult:
        raise ProviderError(400, "xAI transcription is not configured")

    async def analyze_file(self, model: str, content: bytes, mime_type: str, prompt: str, max_tokens: int) -> TextResult:
        raise ProviderError(400, "xAI file analysis is not configured")
