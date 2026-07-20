import html
import re


def telegram_html(value: str) -> str:
    value = html.escape(value, quote=False)
    value = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", value, flags=re.DOTALL)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", value)


def chunks(value: str, size: int = 3900) -> list[str]:
    return [value[index:index + size] for index in range(0, len(value), size)] or [""]
