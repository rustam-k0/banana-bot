from __future__ import annotations

import time
from typing import Awaitable, Callable, TypeVar

from banana_bot.adapters.base import AIAdapter
from banana_bot.config import AppConfig, ModelTarget
from banana_bot.domain import ImageResult, TextResult
from banana_bot.http import ProviderError
from banana_bot.memory import ConversationMemory
from banana_bot.observability import Metrics, log_event


T = TypeVar("T")


class AIService:
    def __init__(self, config: AppConfig, adapters: dict[str, AIAdapter], memory: ConversationMemory, metrics: Metrics):
        self.config, self.adapters, self.memory, self.metrics = config, adapters, memory, metrics

    async def _fallback(self, operation: str, chain: tuple[ModelTarget, ...], call: Callable[[AIAdapter, ModelTarget], Awaitable[T]]) -> T:
        last_error: Exception | None = None
        for target in self.config.enabled_chain(chain):
            started = time.perf_counter()
            try:
                result = await call(self.adapters[target.provider], target)
                usage = getattr(result, "usage", None)
                self.metrics.record(target.provider, target.model, (time.perf_counter() - started) * 1000, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
                log_event("provider_success", operation=operation, provider=target.provider, model=target.model)
                return result
            except ProviderError as exc:
                self.metrics.record(target.provider, target.model, (time.perf_counter() - started) * 1000, error=True)
                log_event("provider_failure", operation=operation, provider=target.provider, model=target.model, status=exc.status, code=exc.code)
                if exc.safety_related:
                    raise
                last_error = exc
            except Exception as exc:
                self.metrics.record(target.provider, target.model, (time.perf_counter() - started) * 1000, error=True)
                log_event("provider_failure", operation=operation, provider=target.provider, model=target.model, error_type=type(exc).__name__)
                last_error = exc
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(503, f"No provider completed {operation}") from last_error

    async def chat(self, user_id: int, text: str, mode: str = "fast", detailed: bool = False) -> TextResult:
        chains = {"fast": self.config.chat_fast_chain, "balanced": self.config.chat_balanced_chain, "complex": self.config.chat_complex_chain}
        self.memory.add(user_id, "user", text)
        messages = [{"role": "system", "content": "Answer concisely by default. Be accurate and use the user's language."}, *self.memory.context(user_id)]
        tokens = self.config.detailed_output_tokens if detailed else self.config.max_output_tokens
        result = await self._fallback("chat", chains.get(mode, chains["fast"]), lambda adapter, target: adapter.chat(target.model, messages, tokens))
        self.memory.add(user_id, "assistant", result.text)
        return result

    async def generate_image(self, prompt: str, mode: str) -> ImageResult:
        chain = self.config.image_pro_chain if mode == "PRO" else self.config.image_fast_chain
        return await self._fallback("generate_image", chain, lambda adapter, target: adapter.image(target.model, prompt, dict(target.options)))

    async def edit_image(self, image: bytes, prompt: str, mode: str) -> ImageResult:
        chain = self.config.image_pro_chain if mode == "PRO" else self.config.image_fast_chain
        return await self._fallback("edit_image", chain, lambda adapter, target: adapter.edit_image(target.model, image, prompt, dict(target.options)))

    async def transcribe(self, audio: bytes) -> TextResult:
        return await self._fallback("transcribe", self.config.transcription_chain, lambda adapter, target: adapter.transcribe(target.model, audio))

    async def analyze_file(self, content: bytes, mime_type: str, prompt: str) -> TextResult:
        return await self._fallback("analyze_file", self.config.file_analysis_chain, lambda adapter, target: adapter.analyze_file(target.model, content, mime_type, prompt, self.config.max_output_tokens))
