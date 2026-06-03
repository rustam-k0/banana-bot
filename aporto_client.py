"""
Optional Aporto integration for banana-bot.

When the user sets `APORTO_API_KEY` in their .env, `bot.py` re-routes the
three AI call-sites (image generation, image editing, audio transcription)
through this module. When `APORTO_API_KEY` is unset, the original Google
Gemini code in `bot.py` is used unchanged.

Users get:
  - one API key for many AI models/skills (image, video, audio, search, etc.)
  - discounted routed pricing on eligible Aporto skills, where available
  - no need to maintain separate provider accounts

Aporto: https://aporto.tech
API key: https://app.aporto.tech/settings

This module is intentionally minimal: it implements only the three
async functions the bot needs, with the SAME signatures and return shapes
as the Google Gemini versions, so the monkey-patch in bot.py is the only
integration point.

Skill IDs were discovered once via `aporto discover "..."` and pinned
below. End users never run discovery at runtime.

Maintainer attribution:
  `INTEGRATION_ID` is a code-level constant. The original repository author
  replaces it with their own value from the Aporto Dashboard to enable
  referral attribution under Aporto's program terms. It is NOT a secret
  and NOT an env var.
"""

import os
import json
import logging
import asyncio
import base64
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger(__name__)


# --- Maintainer attribution --------------------------------------------
# Replace with your own value from https://app.aporto.tech (Integrations).
# Not a secret. Not an env var. Do not move this to .env.
INTEGRATION_ID = "YOUR_APORTO_INTEGRATION_ID"


# --- Pinned Aporto skill IDs -------------------------------------------
# These were chosen via integration-time discovery. See the PR description
# for the scoring rationale. Do not call `aporto discover` at runtime.
APORTO_SKILLS: dict[str, int] = {
    # Image generation: text -> image
    "image_gen_flash": 96,    # Image Generation Nano Banana 2 1K   (~$0.04)
    "image_gen_pro":   146,   # Image Generation Nano Banana Pro 2K (~$0.09)
    # Image editing: image + text -> image
    "image_edit":      249,   # Image Generation Nano Banana Image-to-Image (~$0.02)
    # Audio transcription (no dedicated STT skill in Aporto; use Gemini
    # multimodal chat as a pragmatic substitute)
    "transcribe":      294,   # Gemini 2.5 Flash Chat (multimodal)  (~$0.0001/call)
}


APORTO_BASE_URL = os.getenv("APORTO_BASE_URL", "https://app.aporto.tech")


# --- Errors -------------------------------------------------------------
class AportoError(Exception):
    """Base error for Aporto wrapper."""


class AportoConfigError(AportoError):
    """APORTO_API_KEY is missing or invalid."""


class AportoAPIError(AportoError):
    """Aporto returned an error response (4xx/5xx) or a malformed payload."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AportoEmptyResultError(AportoError):
    """Aporto succeeded but returned no usable artifact."""


# --- Helpers ------------------------------------------------------------
def _get_aporto_key() -> str:
    key = os.getenv("APORTO_API_KEY")
    if not key:
        raise AportoConfigError("APORTO_API_KEY is not set.")
    return key


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Cloudflare WAF rejects POST requests without a User-Agent
        # (returns 1010 / generic 403). Any non-empty value works.
        "User-Agent": "banana-bot-aporto-client/0.1",
        "X-Aporto-Integration-Id": INTEGRATION_ID,
    }


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 120.0) -> dict:
    """Blocking POST -> JSON. Used for non-streaming calls. Wrapped by run_skill_async."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise AportoAPIError(
            f"Aporto HTTP {e.code}: {e.reason}",
            status=e.code,
            body=raw,
        ) from e
    except urllib.error.URLError as e:
        raise AportoAPIError(f"Aporto network error: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise AportoAPIError(f"Aporto returned non-JSON: {e}") from e


async def run_skill_async(
    skill_id: int, params: dict, *, intent: str, timeout: float = 120.0
) -> dict:
    """
    Call Aporto routing.runSkill once and return the parsed JSON response.

    Endpoint and payload shape verified against the Aporto SDK source
    (@aporto-tech/sdk/dist/chunk-QCTYL5DU.mjs, function runSkill).
    If the Aporto API changes in a future SDK version, this is the only
    place to change.
    """
    api_key = _get_aporto_key()
    url = f"{APORTO_BASE_URL.rstrip('/')}/api/routing/run"
    payload = {
        "skillId": skill_id,
        "params": params,
        "waitForResult": True,
        "integrationId": INTEGRATION_ID,
        "intent": intent,  # server-required, even if SDK types mark it optional
    }
    headers = _build_headers(api_key)
    return await asyncio.to_thread(_post_json, url, payload, headers, timeout)


# --- Artifact extraction -----------------------------------------------
def _extract_image_bytes(result: dict) -> bytes:
    """
    Find image bytes in an Aporto run result.

    Tries:
      - result.artifacts[i].data (base64)
      - result.images[i].data (base64)
      - result.data.artifacts[i].data (base64)
      - result.artifact.data / result.artifact.url (singular)
      - any URL in artifacts -> download
    """
    candidates: list[list[dict]] = [
        result.get("artifacts") or [],
        result.get("images") or [],
        (result.get("data") or {}).get("artifacts") or [],
        (result.get("data") or {}).get("images") or [],
        [result["artifact"]] if isinstance(result.get("artifact"), dict) else [],
    ]
    for items in candidates:
        for art in items:
            if not isinstance(art, dict):
                continue
            data = art.get("data")
            if isinstance(data, str) and data:
                try:
                    return base64.b64decode(data)
                except Exception:
                    pass
            url = art.get("url")
            if url:
                return _download_bytes(url)
    raise AportoEmptyResultError("Aporto returned no image bytes or URL.")


def _extract_text(result: dict) -> str:
    """
    Find the assistant text in an Aporto result.

    Tries, in order:
      - result.data.content           (most common; Gemini 2.5 Flash)
      - result.data.choices[0].message.content (OpenAI-compatible)
      - result.data.candidates[0].content.parts[*].text
      - result.text (rare)
    Returns the text, stripped.
    """
    data = result.get("data") or {}

    if isinstance(data.get("content"), str) and data["content"].strip():
        return data["content"].strip()

    for choice in data.get("choices") or []:
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            for piece in content:
                if isinstance(piece, dict) and isinstance(piece.get("text"), str):
                    return piece["text"].strip()

    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                return t.strip()

    if isinstance(result.get("text"), str) and result["text"].strip():
        return result["text"].strip()

    raise AportoEmptyResultError("Aporto returned no text content.")


def _download_bytes(url: str, timeout: float = 60.0) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        raise AportoAPIError(f"Failed to download Aporto artifact from {url}: {e}") from e


# --- High-level wrappers (preserve return shape of original Gemini calls) ----
async def generate_image_from_text(
    prompt: str, mode: str, status_msg: Any, lang: str
) -> bytes | None:
    """
    Generates an image from a text prompt.

    Preserves the original Gemini-based signature and return type
    (bytes | None) so call-sites in bot.py do not need to change.

    Routing:
      PRO   -> APORTO_SKILLS["image_gen_pro"]   (146 — Nano Banana Pro 2K)
      FLASH -> APORTO_SKILLS["image_gen_flash"] (96  — Nano Banana 2 1K)
    """
    from texts import TEXTS
    from config import IMAGE_GEN_MODELS  # late import to avoid cycles

    model_name = IMAGE_GEN_MODELS.get(mode, IMAGE_GEN_MODELS["FLASH"])[0]
    skill_id = APORTO_SKILLS["image_gen_pro"] if mode == "PRO" else APORTO_SKILLS["image_gen_flash"]
    logger.info(
        f"Action: api_call | Type: generate_image | Provider: Aporto "
        f"| Model: {model_name} | skillId: {skill_id}"
    )
    t = TEXTS[lang]
    try:
        result = await run_skill_async(
            skill_id, {"prompt": prompt}, intent="generate image from text prompt"
        )
        return _extract_image_bytes(result)
    except AportoConfigError as e:
        logger.error(f"Action: api_error | Type: generate_image | Config: {e}")
        await status_msg.edit_text(str(e))
        return None
    except AportoAPIError as e:
        logger.error(
            f"Action: api_error | Type: generate_image | Status: {e.status} | Error: {e}"
        )
        if e.status == 429:
            await status_msg.edit_text(t.get("ERR_RATELIMIT", "Rate limit hit."))
        elif e.status is not None and e.status >= 500:
            await status_msg.edit_text(t.get("ERR_SERVER", "Upstream server error."))
        else:
            await status_msg.edit_text(
                t.get("ERR_UNKNOWN", "Aporto error: {error}").format(error=str(e))
            )
        return None
    except AportoEmptyResultError as e:
        logger.error(f"Action: api_error | Type: generate_image | Empty: {e}")
        await status_msg.edit_text(t.get("ERR_GEN_INTERNAL", "Aporto returned no image."))
        return None
    except Exception as e:
        logger.error(
            f"Action: system_error | Type: generate_image | Model: {model_name} | Error: {e}"
        )
        await status_msg.edit_text(t.get("ERR_GEN_INTERNAL", "Internal error."))
        return None


async def edit_image_with_prompt(
    image_bytes: bytes, prompt: str, mode: str, status_msg: Any, lang: str
) -> bytes | None:
    """
    Edits an existing image with a text prompt.

    Preserves the original signature and return type (bytes | None).
    """
    from texts import TEXTS
    from config import IMAGE_EDIT_MODELS

    model_name = IMAGE_EDIT_MODELS.get(mode, IMAGE_EDIT_MODELS["FLASH"])[0]
    skill_id = APORTO_SKILLS["image_edit"]
    logger.info(
        f"Action: api_call | Type: edit_image | Provider: Aporto "
        f"| Model: {model_name} | skillId: {skill_id}"
    )
    t = TEXTS[lang]
    try:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        result = await run_skill_async(
            skill_id,
            {"prompt": prompt, "image": image_b64, "image_mime": "image/jpeg"},
            intent="edit image according to text prompt",
        )
        return _extract_image_bytes(result)
    except AportoConfigError as e:
        logger.error(f"Action: api_error | Type: edit_image | Config: {e}")
        await status_msg.edit_text(str(e))
        return None
    except AportoAPIError as e:
        logger.error(
            f"Action: api_error | Type: edit_image | Status: {e.status} | Error: {e}"
        )
        if e.status == 429:
            await status_msg.edit_text(t.get("ERR_RATELIMIT", "Rate limit hit."))
        elif e.status is not None and e.status >= 500:
            await status_msg.edit_text(t.get("ERR_SERVER", "Upstream server error."))
        else:
            await status_msg.edit_text(
                t.get("ERR_UNKNOWN", "Aporto error: {error}").format(error=str(e))
            )
        return None
    except AportoEmptyResultError as e:
        logger.error(f"Action: api_error | Type: edit_image | Empty: {e}")
        await status_msg.edit_text(t.get("ERR_EDIT_INTERNAL", "Aporto returned no image."))
        return None
    except Exception as e:
        logger.error(
            f"Action: system_error | Type: edit_image | Model: {model_name} | Error: {e}"
        )
        await status_msg.edit_text(t.get("ERR_EDIT_INTERNAL", "Internal error."))
        return None


async def transcribe_audio(
    audio_bytes: bytes, mode: str, status_msg: Any, lang: str
) -> str | None:
    """
    Transcribes a voice message (ogg) into text.

    Aporto has no dedicated STT skill, so we route through Gemini 2.5 Flash
    Chat (skill 294) using a multimodal `input_audio` content part that
    Gemini-family chat models understand.
    """
    from texts import TEXTS
    from config import TEXT_AUDIO_MODELS

    model_name = TEXT_AUDIO_MODELS.get(mode, TEXT_AUDIO_MODELS["FLASH"])[0]
    skill_id = APORTO_SKILLS["transcribe"]
    logger.info(
        f"Action: api_call | Type: transcribe_audio | Provider: Aporto "
        f"| Model: {model_name} | skillId: {skill_id}"
    )
    t = TEXTS[lang]
    prompt_lang = (
        "Транскрибируй это голосовое сообщение в текст. "
        "Выведи только распознанный текст без лишних слов."
        if lang == "RU"
        else "Transcribe this voice message to text. "
             "Only return the recognized text without any extra words."
    )
    try:
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        # Aporto Gemini 2.5 Flash Chat accepts OpenAI-style multimodal
        # messages at the top level of `params`. Verified end-to-end.
        result = await run_skill_async(
            skill_id,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_lang},
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_b64, "format": "ogg"},
                            },
                        ],
                    }
                ],
            },
            intent="transcribe voice message to text",
        )
        return _extract_text(result).strip()
    except AportoConfigError as e:
        logger.error(f"Action: api_error | Type: transcribe_audio | Config: {e}")
        await status_msg.edit_text(str(e))
        return None
    except AportoAPIError as e:
        logger.error(
            f"Action: api_error | Type: transcribe_audio | Status: {e.status} | Error: {e}"
        )
        if e.status == 429:
            await status_msg.edit_text(t.get("ERR_RATELIMIT", "Rate limit hit."))
        elif e.status is not None and e.status >= 500:
            await status_msg.edit_text(t.get("ERR_SERVER", "Upstream server error."))
        else:
            await status_msg.edit_text(
                t.get("ERR_UNKNOWN", "Aporto error: {error}").format(error=str(e))
            )
        return None
    except AportoEmptyResultError as e:
        logger.error(f"Action: api_error | Type: transcribe_audio | Empty: {e}")
        await status_msg.edit_text(t.get("ERR_AUDIO_TRANS", "Aporto returned no text."))
        return None
    except Exception as e:
        logger.error(
            f"Action: system_error | Type: transcribe_audio | Model: {model_name} | Error: {e}"
        )
        await status_msg.edit_text(t.get("ERR_AUDIO_TRANS", "Internal error."))
        return None
