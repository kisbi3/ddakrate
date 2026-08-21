from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def mermaid_escape(value: Any) -> str:
    """Escape arbitrary data for a Mermaid quoted node label."""

    text = _stable_text(value)
    escaped = html.escape(text, quote=True).replace("\n", "<br/>")
    # Backticks can trigger Mermaid markdown parsing in some renderers.
    return escaped.replace("`", "&#96;")


def truncate(value: Any, limit: int = 96) -> str:
    text = _stable_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _stable_text(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
