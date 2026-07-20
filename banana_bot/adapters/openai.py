from __future__ import annotations

import base64
from aiohttp import FormData

from banana_bot.domain import ImageResult, TextResult, Usage
from banana_bot.http import AsyncHTTPClient, ProviderError


class OpenAIAdapter:
    provider = "openai"

    def __init__(self, client: AsyncHTTPClient, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.client, self.base_url = client, base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def chat(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> TextResult:
        payload = await self.client.request_json("POST", f"{self.base_url}/responses", headers=self.headers, json={
            "model": model, "input": messages, "max_output_tokens": max_tokens,
        })
        text = payload.get("output_text")
        if not text:
            text = "".join(
                part.get("text", "") for item in payload.get("output", [])
                for part in item.get("content", []) if part.get("type") in {"output_text", "text"}
            )
        if not text:
            raise ProviderError(502, "Provider returned no text")
        usage = payload.get("usage", {})
        return TextResult(text, self.provider, model, Usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0)))

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
        payload = await self.client.request_json("POST", f"{self.base_url}/images/generations", headers=self.headers, json={"model": model, "prompt": prompt, **options})
        return ImageResult(await self._image_bytes(payload), self.provider, model)

    async def edit_image(self, model: str, image: bytes, prompt: str, options: dict[str, str]) -> ImageResult:
        def form() -> FormData:
            data = FormData()
            for key, value in {"model": model, "prompt": prompt, **options}.items():
                data.add_field(key, str(value))
            data.add_field("image[]", image, filename="source.jpg", content_type="image/jpeg")
            return data
        payload = await self.client.request_json("POST", f"{self.base_url}/images/edits", headers=self.headers, data_factory=form)
        return ImageResult(await self._image_bytes(payload), self.provider, model)

    async def transcribe(self, model: str, audio: bytes) -> TextResult:
        def form() -> FormData:
            data = FormData()
            data.add_field("model", model)
            data.add_field("response_format", "json")
            data.add_field("file", audio, filename="voice.ogg", content_type="audio/ogg")
            return data
        payload = await self.client.request_json("POST", f"{self.base_url}/audio/transcriptions", headers=self.headers, data_factory=form)
        return TextResult(payload.get("text", "").strip(), self.provider, model)

    async def analyze_file(self, model: str, content: bytes, mime_type: str, prompt: str, max_tokens: int) -> TextResult:
        encoded = base64.b64encode(content).decode("ascii")
        return await self.chat(model, [{"role": "user", "content": [
            {"type": "input_file", "filename": "document", "file_data": f"data:{mime_type};base64,{encoded}"},
            {"type": "input_text", "text": prompt},
        ]}], max_tokens)  # type: ignore[list-item]
