from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import random
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout


class ProviderError(RuntimeError):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.status in {408, 409, 425, 429} or self.status >= 500

    @property
    def safety_related(self) -> bool:
        return self.code in {"moderation_blocked", "content_policy_violation", "SAFETY"}


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int):
        self.limit = requests_per_minute
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.limit:
                await asyncio.sleep(max(0, 60 - (now - self._calls[0])))
            self._calls.append(time.monotonic())


class AsyncHTTPClient:
    def __init__(self, timeout: float, connect_timeout: float, retries: int, rate_limit: int):
        self._timeout = ClientTimeout(total=timeout, connect=connect_timeout)
        self._retries = retries
        self._limiter = SlidingWindowRateLimiter(rate_limit)
        self._session: ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        data_factory: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        await self.start()
        assert self._session is not None
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            await self._limiter.acquire()
            try:
                data = data_factory() if data_factory else None
                async with self._session.request(method, url, headers=headers, json=json, data=data) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except Exception:
                        payload = {"error": {"message": (await response.text())[:300]}}
                    if response.status >= 400:
                        error = payload.get("error", payload)
                        if isinstance(error, dict):
                            raise ProviderError(
                                response.status,
                                str(error.get("message", "Provider request failed")),
                                error.get("code") or error.get("type") or error.get("status"),
                            )
                        raise ProviderError(response.status, str(error))
                    return payload
            except ProviderError as exc:
                last_error = exc
                if exc.safety_related or not exc.retryable or attempt == self._retries:
                    raise
            except (ClientError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self._retries:
                    raise ProviderError(503, "Provider connection failed") from exc
            await asyncio.sleep((2**attempt) * 0.25 + random.random() * 0.1)
        raise ProviderError(503, "Provider request failed") from last_error

    async def download(self, url: str) -> bytes:
        await self.start()
        assert self._session is not None
        await self._limiter.acquire()
        async with self._session.get(url) as response:
            if response.status >= 400:
                raise ProviderError(response.status, "Could not download provider media")
            return await response.read()
