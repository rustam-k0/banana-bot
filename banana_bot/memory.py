from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import re


@dataclass
class Conversation:
    messages: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=8))
    summary: str = ""
    facts: list[str] = field(default_factory=list)


class ConversationMemory:
    """Bounded process memory; the interface can later be backed by Redis unchanged."""

    def __init__(self, message_limit: int = 8, summary_chars: int = 1200, fact_limit: int = 20):
        self._message_limit = message_limit
        self._summary_chars = summary_chars
        self._fact_limit = fact_limit
        self._items: defaultdict[int, Conversation] = defaultdict(self._new_conversation)

    def _new_conversation(self) -> Conversation:
        return Conversation(messages=deque(maxlen=self._message_limit))

    def clear(self, user_id: int) -> None:
        self._items.pop(user_id, None)

    def add(self, user_id: int, role: str, content: str) -> None:
        conversation = self._items[user_id]
        if len(conversation.messages) == conversation.messages.maxlen:
            evicted = conversation.messages[0]
            fragment = f"{evicted['role']}: {evicted['content'][:240]}"
            conversation.summary = (conversation.summary + "\n" + fragment).strip()[-self._summary_chars :]
        conversation.messages.append({"role": role, "content": content})
        if role == "user":
            self._capture_fact(conversation, content)

    def _capture_fact(self, conversation: Conversation, content: str) -> None:
        match = re.match(r"\s*(?:remember|запомни)\s*[:,-]?\s*(.+)", content, re.IGNORECASE)
        if match:
            fact = match.group(1).strip()[:300]
            if fact and fact not in conversation.facts:
                conversation.facts = (conversation.facts + [fact])[-self._fact_limit :]

    def context(self, user_id: int) -> list[dict[str, str]]:
        conversation = self._items[user_id]
        prefix: list[dict[str, str]] = []
        if conversation.summary:
            prefix.append({"role": "system", "content": f"Conversation summary: {conversation.summary}"})
        if conversation.facts:
            prefix.append({"role": "system", "content": "Saved user facts:\n- " + "\n- ".join(conversation.facts)})
        return prefix + list(conversation.messages)
