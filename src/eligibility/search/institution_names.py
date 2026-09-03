from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable


_LEGAL_PREFIX_RE = re.compile(r"^(?:\(주\)|주식회사|㈜)+", re.IGNORECASE)
_LEGAL_SUFFIX_RE = re.compile(r"(?:주식회사|㈜)$", re.IGNORECASE)
_LEADING_LATIN_BRAND_RE = re.compile(r"^([a-z]{2,5})(.+)$")
_INSTITUTION_SUFFIXES = (
    "신용협동조합",
    "상호저축은행",
    "저축은행",
    "새마을금고",
    "자산운용",
    "투자증권",
    "생명보험",
    "손해보험",
    "협동조합",
    "은행",
    "증권",
    "보험",
    "캐피탈",
    "뱅크",
)
_GENERIC_ALIASES = {
    "은행",
    "저축은행",
    "상호저축은행",
    "증권",
    "보험",
    "금고",
    "협동조합",
    "신용협동조합",
    "뱅크",
    "bank",
}
_ALIAS_DATA_PATH = (
    Path(__file__).resolve().parents[1] / "catalog" / "data" / "institution_aliases.json"
)


@dataclass(frozen=True)
class InstitutionReferenceResolution:
    resolved: tuple[dict[str, Any], ...]
    ambiguous: tuple[dict[str, Any], ...]


def normalize_institution_text(value: str) -> str:
    """Normalize a name for deterministic matching, not for display."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = _LEGAL_PREFIX_RE.sub("", normalized).strip()
    normalized = _LEGAL_SUFFIX_RE.sub("", normalized).strip()
    return "".join(character for character in normalized.casefold() if character.isalnum())


def generated_institution_aliases(name: str) -> set[str]:
    """Generate conservative aliases from one catalog institution name."""

    canonical = normalize_institution_text(name)
    if not canonical:
        return set()
    aliases = {canonical}

    brand_match = _LEADING_LATIN_BRAND_RE.match(canonical)
    if brand_match:
        brand, remainder = brand_match.groups()
        aliases.add(brand)
        aliases.add(remainder)

    for candidate in tuple(aliases):
        for suffix in _INSTITUTION_SUFFIXES:
            normalized_suffix = normalize_institution_text(suffix)
            if candidate.endswith(normalized_suffix) and len(candidate) > len(normalized_suffix):
                aliases.add(candidate[: -len(normalized_suffix)])

    return {
        alias
        for alias in aliases
        if len(alias) >= 2 and alias not in _GENERIC_ALIASES
    }


def resolve_institution_references(
    message: str,
    institution_catalog: Iterable[dict[str, Any]],
) -> InstitutionReferenceResolution:
    """Resolve institution mentions only when an alias identifies one catalog ID.

    Ambiguous aliases are returned separately so the caller can allow a model to
    ask a clarification question without exposing every institution in the catalog.
    """

    records_by_id: dict[str, dict[str, Any]] = {}
    names_by_id: dict[str, set[str]] = {}
    for raw_item in institution_catalog:
        if not isinstance(raw_item, dict):
            continue
        institution_id = str(raw_item.get("institution_id") or "").strip()
        institution_name = str(raw_item.get("institution_name") or "").strip()
        if not institution_id or not institution_name:
            continue
        records_by_id.setdefault(
            institution_id,
            {
                "institution_id": institution_id,
                "institution_name": institution_name,
            },
        )
        names_by_id.setdefault(institution_id, set()).add(institution_name)

    alias_to_ids: dict[str, set[str]] = {}
    for institution_id, names in names_by_id.items():
        aliases: set[str] = set()
        for name in names:
            aliases.update(generated_institution_aliases(name))
        for alias in aliases:
            alias_to_ids.setdefault(alias, set()).add(institution_id)

    for override in _load_alias_overrides():
        matching_ids = {
            institution_id
            for institution_id, names in names_by_id.items()
            if any(
                normalize_institution_text(match_name)
                in {normalize_institution_text(name) for name in names}
                for match_name in override.get("match_names", [])
            )
        }
        if not matching_ids:
            continue
        for alias_value in override.get("aliases", []):
            alias = normalize_institution_text(alias_value)
            if len(alias) >= 2 and alias not in _GENERIC_ALIASES:
                alias_to_ids.setdefault(alias, set()).update(matching_ids)

    compact_message = normalize_institution_text(message)
    ascii_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKC", message))
    }
    matching_aliases = [
        alias
        for alias in alias_to_ids
        if _alias_occurs(alias, compact_message=compact_message, ascii_tokens=ascii_tokens)
    ]
    # A full name must win over its shorter alias. For example, the ambiguous
    # alias "신한" must not obscure the exact, unique "신한은행" mention.
    maximal_aliases = [
        alias
        for alias in matching_aliases
        if not any(alias != other and alias in other for other in matching_aliases)
    ]

    resolved_aliases_by_id: dict[str, list[str]] = {}
    ambiguous: list[dict[str, Any]] = []
    for alias in sorted(maximal_aliases, key=lambda item: (-len(item), item)):
        institution_ids = sorted(alias_to_ids[alias])
        if len(institution_ids) == 1:
            resolved_aliases_by_id.setdefault(institution_ids[0], []).append(alias)
            continue
        ambiguous.append(
            {
                "matched_alias": alias,
                "candidates": [records_by_id[item] for item in institution_ids],
            }
        )

    resolved = []
    for institution_id, aliases in resolved_aliases_by_id.items():
        item = dict(records_by_id[institution_id])
        item["matched_aliases"] = aliases
        resolved.append(item)
    resolved.sort(key=lambda item: item["institution_id"])
    return InstitutionReferenceResolution(
        resolved=tuple(resolved),
        ambiguous=tuple(ambiguous),
    )


def _alias_occurs(
    alias: str,
    *,
    compact_message: str,
    ascii_tokens: set[str],
) -> bool:
    if alias.isascii():
        return alias in ascii_tokens
    return alias in compact_message


def _load_alias_overrides() -> list[dict[str, list[str]]]:
    try:
        payload = json.loads(_ALIAS_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    aliases = payload.get("aliases", []) if isinstance(payload, dict) else []
    return [item for item in aliases if isinstance(item, dict)]
