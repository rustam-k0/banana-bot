from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TextResult:
    text: str
    provider: str
    model: str
    usage: Usage = Usage()


@dataclass(frozen=True)
class ImageResult:
    content: bytes
    provider: str
    model: str
