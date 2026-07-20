from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import logging
import time
from typing import Any


logger = logging.getLogger("banana_bot")


def log_event(event: str, **fields: Any) -> None:
    safe = {key: value for key, value in fields.items() if key not in {"prompt", "content", "token", "api_key"}}
    logger.info(json.dumps({"event": event, **safe}, ensure_ascii=False, default=str))


@dataclass
class ModelTotals:
    calls: int = 0
    errors: int = 0
    latency_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0


class Metrics:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.users: Counter[int] = Counter()
        self.models: defaultdict[str, ModelTotals] = defaultdict(ModelTotals)

    def user_activity(self, user_id: int) -> None:
        self.users[user_id] += 1

    def record(self, provider: str, model: str, latency_ms: float, input_tokens: int = 0, output_tokens: int = 0, error: bool = False) -> None:
        totals = self.models[f"{provider}/{model}"]
        totals.calls += 1
        totals.errors += int(error)
        totals.latency_ms += latency_ms
        totals.input_tokens += input_tokens
        totals.output_tokens += output_tokens
        # Prices vary by contract and model; this conservative estimate is explicitly approximate.
        totals.estimated_cost_usd += input_tokens / 1_000_000 * 2.0 + output_tokens / 1_000_000 * 8.0

    def render(self) -> str:
        lines = [
            "📊 Banana Bot",
            f"Uptime: {int(time.time() - self.started_at)}s",
            f"Active users: {len(self.users)}",
            f"Requests: {sum(item.calls for item in self.models.values())}",
        ]
        for name, item in sorted(self.models.items()):
            average = item.latency_ms / item.calls if item.calls else 0
            lines.append(
                f"• {name}: {item.calls} calls, {item.errors} errors, {average:.0f} ms, "
                f"{item.input_tokens}/{item.output_tokens} tokens, ≈${item.estimated_cost_usd:.4f}"
            )
        return "\n".join(lines)
