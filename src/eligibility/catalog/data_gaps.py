"""Small compatibility helpers for published ``version_metadata.data_gaps``.

The catalog contains both the original ``path``/``reason`` shape and the
legacy ``field``/``message`` shape.  Consumers should use these accessors so
that a gap cannot disappear merely because its producer used the other name.
"""

from __future__ import annotations

from typing import Any, Mapping


def gap_path(gap: Any) -> str:
    """Return a gap's canonical path, accepting the legacy field key."""

    if isinstance(gap, str):
        return gap
    if not isinstance(gap, Mapping):
        return ""
    return str(gap.get("path") or gap.get("field") or "")


def gap_reason(gap: Any) -> str:
    """Return a gap's explanation, accepting the legacy message key."""

    if isinstance(gap, str):
        return gap
    if not isinstance(gap, Mapping):
        return ""
    return str(gap.get("reason") or gap.get("message") or "")
