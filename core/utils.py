from __future__ import annotations

from html import unescape
import re


PHONE_PATTERN = re.compile(r"^\+91[6-9]\d{9}$")
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", str(phone or ""))

    if cleaned.startswith("+91"):
        normalized = cleaned
    elif cleaned.startswith("91") and len(cleaned) == 12:
        normalized = f"+{cleaned}"
    elif len(cleaned) == 10:
        normalized = f"+91{cleaned}"
    else:
        raise ValueError("phone number is not recognizable")

    if not PHONE_PATTERN.fullmatch(normalized):
        raise ValueError("phone number must normalize to +91XXXXXXXXXX")

    return normalized


def strip_html(text: str) -> str:
    cleaned = TAG_PATTERN.sub(" ", str(text or ""))
    cleaned = unescape(cleaned)
    return WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def truncate_utf8(text: str, max_chars: int) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")

    if isinstance(text, bytes):
        cleaned = text.decode("utf-8", errors="ignore")
    else:
        cleaned = str(text or "").encode("utf-8", errors="ignore").decode("utf-8")

    return cleaned[:max_chars]

