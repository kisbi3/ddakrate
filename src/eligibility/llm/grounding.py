from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eligibility.audit import canonical_hash
from eligibility.schema.evaluation import MissingFactRequest


class ClaimType(StrEnum):
    IDENTIFIER = "IDENTIFIER"
    CONDITION_TERM = "CONDITION_TERM"  # backward-compatible alias-like free term
    TARGET = "TARGET"
    ACTION = "ACTION"
    CONDITION = "CONDITION"
    STATUS = "STATUS"
    PRODUCT_ATTRIBUTE = "PRODUCT_ATTRIBUTE"
    RECOMMENDATION_REASON = "RECOMMENDATION_REASON"
    DURATION = "DURATION"
    RATE = "RATE"
    AMOUNT = "AMOUNT"
    COUNT = "COUNT"
    RANK = "RANK"
    INTEREST = "INTEREST"
    NUMBER = "NUMBER"


class CanonicalClaim(BaseModel):
    """Typed, immutable semantic atom that an LLM may verbalize."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_type: ClaimType
    value: str
    unit: str | None = None
    source_ref: str | None = None
    label: str | None = None


class ClaimBinding(BaseModel):
    """A generated output's explicit declaration of a canonical claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_type: ClaimType
    value: str
    unit: str | None = None


class CanonicalQuestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_type: str
    rule_id: str
    action_id: str | None = None
    reward_id: str | None = None
    question_type: str
    claims: list[CanonicalClaim] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    allowed_condition_terms: list[str] = Field(default_factory=list)
    deterministic_fallback: str


# Terms whose appearance generally changes the financial condition rather than
# merely changing wording. The validator intentionally excludes generic words
# such as "상품", "조건", "우대", "금리", "가입", and "관리".
_FINANCIAL_CONDITION_TERMS = (
    "신용카드",
    "체크카드",
    "신한카드",
    "카드",
    "결제계좌",
    "급여계좌",
    "급여이체",
    "급여",
    "월급봉투",
    "자동이체",
    "공과금",
    "통신비",
    "아파트관리비",
    "마케팅동의",
    "마케팅",
    "영업점",
    "supersol",
    "슈퍼sol",
    "쿠폰",
    "첫거래",
    "이벤트",
    "주택청약",
    "해외송금",
    "달리기",
    "건강자산관리",
    "건강데이터",
    "가족등록",
    "친권",
    "아이통장",
    "월급",
 )

# MVP semantic vocabulary.  Each entry maps wording variants to one canonical
# financial/action concept.  The validator reasons about concept IDs, not exact
# strings, so harmless paraphrases remain possible while new actions are blocked.
_SEMANTIC_CONCEPTS: dict[str, tuple[ClaimType, tuple[str, ...]]] = {
    "SALARY_ENVELOPE": (ClaimType.ACTION, ("월급봉투", "급여클럽월급봉투", "salary_envelope")),
    "SALARY_ACCOUNT_CHANGE": (ClaimType.ACTION, ("급여계좌변경", "급여수령계좌변경", "change_salary_account")),
    "SALARY_TRANSFER": (ClaimType.CONDITION, ("급여이체", "급여실적", "급여수령")),
    "CARD_USAGE": (ClaimType.CONDITION, ("카드결제", "카드사용", "신한카드", "체크카드", "신용카드")),
    "NEW_CARD_ISSUANCE": (ClaimType.ACTION, ("신규카드발급", "새카드발급", "카드를새로발급", "카드새로만들")),
    "CARD_SETTLEMENT_ACCOUNT_CHANGE": (ClaimType.ACTION, ("결제계좌변경", "카드결제계좌변경")),
    "DEMAND_DEPOSIT_ACCOUNT_OPENING": (ClaimType.ACTION, ("입출금통장개설", "입출금계좌개설", "새통장개설", "신규입출금통장")),
    "AUTO_TRANSFER": (ClaimType.CONDITION, ("자동이체", "자동납입")),
    "BRANCH_VISIT": (ClaimType.ACTION, ("영업점방문", "지점방문")),
    "SUPERSOL_MEMBERSHIP": (ClaimType.ACTION, ("supersol가입", "슈퍼sol가입", "supersol로그인", "슈퍼sol로그인", "supersol회원", "슈퍼sol회원")),
    "FIRST_TRANSACTION": (ClaimType.CONDITION, ("첫거래", "첫거래우대")),
    "EVENT_COUPON": (ClaimType.CONDITION, ("이벤트쿠폰", "특별금리쿠폰", "우대쿠폰")),
    "MARKETING_CONSENT": (ClaimType.ACTION, ("마케팅동의", "마케팅수신동의")),
    "OVERSEAS_REMITTANCE": (ClaimType.CONDITION, ("해외송금", "외화송금")),
    "HOUSING_SUBSCRIPTION": (ClaimType.CONDITION, ("주택청약", "청약통장")),
    "HEALTH_DATA_SERVICE": (ClaimType.ACTION, ("건강자산관리", "건강데이터연동", "건강데이터동의")),
    "FAMILY_REGISTRATION": (ClaimType.ACTION, ("가족등록", "가족실적등록")),
    "CHILD_ACCOUNT": (ClaimType.PRODUCT_ATTRIBUTE, ("아이통장", "자녀통장")),
    "PARENTAL_AUTHORITY": (ClaimType.CONDITION, ("친권", "부모자녀관계", "보호자확인")),
    "RUNNING_ACTIVITY": (ClaimType.CONDITION, ("달리기", "러닝")),
    "STATUS_ACHIEVABLE": (ClaimType.STATUS, ("달성가능", "적용가능", "받을수있", "할수있")),
    "STATUS_UNSATISFIABLE": (ClaimType.STATUS, ("받을수없", "달성불가", "충족불가", "불가능")),
    "STATUS_UNKNOWN": (ClaimType.STATUS, ("추가확인필요", "확인필요", "미확인", "알수없")),
    "STATUS_SATISFIED": (ClaimType.STATUS, ("충족됨", "충족했", "확인됨", "금융데이터로확인")),
}


def _compact_semantic_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", text).casefold()


def extract_semantic_claims(
    text: str,
    *,
    source_ref: str | None = None,
) -> list[CanonicalClaim]:
    compact = _compact_semantic_text(text)
    claims: list[CanonicalClaim] = []
    for concept_id, (claim_type, aliases) in _SEMANTIC_CONCEPTS.items():
        if any(_compact_semantic_text(alias) in compact for alias in aliases):
            claims.append(
                CanonicalClaim(
                    claim_id=f"CLAIM-{canonical_hash({'concept': concept_id, 'source': source_ref})[:16]}",
                    claim_type=claim_type,
                    value=concept_id,
                    source_ref=source_ref,
                    label=concept_id,
                )
            )
    return claims

# Longest alternatives first so "%p" is not consumed as "%".
_NUMERIC_MENTION = re.compile(
    r"(?P<sign>[+-]?)\s*(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%p|퍼센트포인트|%|퍼센트|개월|년|주|일|만원|원|회|번|위)?",
    re.IGNORECASE,
)


def _normalize_decimal(value: str | Decimal) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _normalize_unit(unit: str | None) -> tuple[ClaimType, str | None, Decimal]:
    if unit is None:
        return ClaimType.NUMBER, None, Decimal("1")
    normalized = unit.casefold()
    if normalized in {"%p", "퍼센트포인트"}:
        return ClaimType.RATE, "PERCENTAGE_POINT", Decimal("1")
    if normalized in {"%", "퍼센트"}:
        return ClaimType.RATE, "PERCENT", Decimal("1")
    if normalized == "개월":
        return ClaimType.DURATION, "MONTH", Decimal("1")
    if normalized == "년":
        return ClaimType.DURATION, "YEAR", Decimal("1")
    if normalized == "주":
        return ClaimType.DURATION, "WEEK", Decimal("1")
    if normalized == "일":
        return ClaimType.DURATION, "DAY", Decimal("1")
    if normalized == "만원":
        return ClaimType.AMOUNT, "KRW", Decimal("10000")
    if normalized == "원":
        return ClaimType.AMOUNT, "KRW", Decimal("1")
    if normalized in {"회", "번"}:
        return ClaimType.COUNT, "OCCURRENCE", Decimal("1")
    if normalized == "위":
        return ClaimType.RANK, "RANK", Decimal("1")
    return ClaimType.NUMBER, normalized.upper(), Decimal("1")


def extract_numeric_claims(text: str, *, source_ref: str | None = None) -> list[CanonicalClaim]:
    claims: list[CanonicalClaim] = []
    for index, match in enumerate(_NUMERIC_MENTION.finditer(text)):
        raw_unit = match.group("unit")
        claim_type, unit, multiplier = _normalize_unit(raw_unit)
        # Ignore digits embedded in identifiers/words such as "v0.4" or
        # "SuperSOL" unless a recognized unit follows.
        start, end = match.span()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if raw_unit is None and (before.isalnum() or after.isalnum() or after in {".", "_"}):
            continue
        number = Decimal(match.group("number")) * multiplier
        if match.group("sign") == "-":
            number = -number
        value = _normalize_decimal(number)
        identity = {
            "source_ref": source_ref,
            "index": index,
            "type": claim_type.value,
            "value": value,
            "unit": unit,
        }
        claims.append(
            CanonicalClaim(
                claim_id=f"CLAIM-{canonical_hash(identity)[:16]}",
                claim_type=claim_type,
                value=value,
                unit=unit,
                source_ref=source_ref,
                label=match.group(0).strip(),
            )
        )
    return claims


def extract_condition_terms(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text).casefold()
    return {
        term.casefold()
        for term in _FINANCIAL_CONDITION_TERMS
        if term.casefold() in compact
    }


def build_question_payload(request: MissingFactRequest, fallback: str) -> CanonicalQuestionPayload:
    claims: list[CanonicalClaim] = []
    claims.extend(extract_numeric_claims(fallback, source_ref="deterministic_fallback"))
    claims.extend(extract_semantic_claims(fallback, source_ref="deterministic_fallback"))
    required_terms: list[str] = []
    for index, term in enumerate(request.grounding_terms):
        source_ref = f"grounding_terms[{index}]"
        numeric = extract_numeric_claims(term, source_ref=source_ref)
        semantic = extract_semantic_claims(term, source_ref=source_ref)
        claims.extend(numeric)
        claims.extend(semantic)
        for numeric_claim in numeric:
            claims.append(
                CanonicalClaim(
                    claim_id=f"CLAIM-{canonical_hash({'target': numeric_claim.value, 'unit': numeric_claim.unit, 'source': source_ref})[:16]}",
                    claim_type=ClaimType.TARGET,
                    value=f"{numeric_claim.value}:{numeric_claim.unit or 'UNITLESS'}",
                    unit=numeric_claim.unit,
                    source_ref=source_ref,
                    label=term,
                )
            )
        # Numeric claims are validated by typed value/unit below, so requiring the
        # entire source phrase here would reject harmless conversational rewrites
        # such as "계약월수 2/3" -> "3개월 중 2개월". Free text without a
        # recognized numeric or semantic claim still has to appear verbatim.
        if not numeric and not semantic:
            required_terms.append(term)
        if not numeric and not semantic:
            claims.append(
                CanonicalClaim(
                    claim_id=f"CLAIM-{canonical_hash({'term': term, 'index': index})[:16]}",
                    claim_type=ClaimType.CONDITION_TERM,
                    value=term,
                    source_ref=source_ref,
                    label=term,
                )
            )
    source_semantic_text = " ".join(
        value for value in (request.fact_type, request.action_id or "", request.reward_id or "") if value
    )
    claims.extend(extract_semantic_claims(source_semantic_text, source_ref="request_identifiers"))
    if request.action_id:
        claims.append(
            CanonicalClaim(
                claim_id=f"CLAIM-{canonical_hash({'action_id': request.action_id})[:16]}",
                claim_type=ClaimType.ACTION,
                value=request.action_id,
                source_ref="action_id",
                label=request.action_id,
            )
        )
    if request.impact is not None and request.impact.rate_pp is not None:
        rate_value = _normalize_decimal(request.impact.rate_pp)
        claims.append(
            CanonicalClaim(
                claim_id=f"CLAIM-{canonical_hash({'rate_pp': rate_value, 'rule': request.requested_by_rule_id})[:16]}",
                claim_type=ClaimType.RATE,
                value=rate_value,
                unit="PERCENTAGE_POINT",
                source_ref=request.reward_id,
                label=f"+{rate_value}%p",
            )
        )

    # De-duplicate typed values without collapsing distinct textual terms.
    unique: dict[tuple[str, str, str | None], CanonicalClaim] = {}
    for claim in claims:
        unique[(claim.claim_type.value, claim.value, claim.unit)] = claim

    allowed_text = " ".join(
        value
        for value in [
            fallback,
            *request.grounding_terms,
            request.fact_type,
            request.action_id or "",
            request.reward_id or "",
        ]
        if value
    )
    return CanonicalQuestionPayload(
        fact_type=request.fact_type,
        rule_id=request.requested_by_rule_id,
        action_id=request.action_id,
        reward_id=request.reward_id,
        question_type=(
            request.expected_semantic_type.value
            if request.expected_semantic_type is not None
            else "FACT_CONFIRMATION"
        ),
        claims=list(unique.values()),
        required_terms=required_terms,
        allowed_condition_terms=sorted(extract_condition_terms(allowed_text)),
        deterministic_fallback=fallback,
    )


def _claim_key(claim: CanonicalClaim | ClaimBinding) -> tuple[str, str, str | None]:
    return (claim.claim_type.value, _normalize_decimal(claim.value), claim.unit)


def validate_grounded_text(
    text: str,
    payload: CanonicalQuestionPayload,
    *,
    bindings: list[ClaimBinding] | None = None,
    require_bindings: bool = False,
) -> bool:
    """Validate typed numeric/unit claims and financial-condition vocabulary.

    This deliberately does *not* accept a number merely because the same digits
    occur somewhere in the source. A ``6 MONTH`` claim cannot authorize a
    ``6 PERCENTAGE_POINT`` claim.
    """

    allowed_numeric = {
        _claim_key(claim)
        for claim in payload.claims
        if claim.claim_type
        in {
            ClaimType.DURATION,
            ClaimType.RATE,
            ClaimType.AMOUNT,
            ClaimType.COUNT,
            ClaimType.RANK,
            ClaimType.INTEREST,
            ClaimType.NUMBER,
        }
    }
    candidate_numeric = {_claim_key(claim) for claim in extract_numeric_claims(text)}
    if not candidate_numeric <= allowed_numeric:
        return False

    required_numeric = {
        _claim_key(claim)
        for claim in payload.claims
        if claim.source_ref is not None
        and claim.source_ref.startswith("grounding_terms[")
        and claim.claim_type != ClaimType.RATE
        and claim.claim_type
        in {
            ClaimType.DURATION,
            ClaimType.AMOUNT,
            ClaimType.COUNT,
            ClaimType.RANK,
            ClaimType.INTEREST,
            ClaimType.NUMBER,
        }
    }
    if not required_numeric <= candidate_numeric:
        return False

    compact = re.sub(r"\s+", "", text).casefold()
    for term in payload.required_terms:
        if re.sub(r"\s+", "", term).casefold() not in compact:
            return False

    candidate_terms = extract_condition_terms(text)
    if not candidate_terms <= set(payload.allowed_condition_terms):
        return False

    semantic_types = {
        ClaimType.ACTION,
        ClaimType.CONDITION,
        ClaimType.STATUS,
        ClaimType.PRODUCT_ATTRIBUTE,
        ClaimType.RECOMMENDATION_REASON,
    }
    allowed_semantic = {
        (claim.claim_type.value, claim.value)
        for claim in payload.claims
        if claim.claim_type in semantic_types
    }
    candidate_semantic_claims = extract_semantic_claims(text)
    candidate_semantic = {
        (claim.claim_type.value, claim.value) for claim in candidate_semantic_claims
    }
    if not candidate_semantic <= allowed_semantic:
        return False

    # A semantic grounding term must still be represented, but approved aliases
    # of the same canonical concept are allowed.
    required_semantic = {
        (claim.claim_type.value, claim.value)
        for claim in payload.claims
        if claim.source_ref is not None
        and claim.source_ref.startswith("grounding_terms[")
        and claim.claim_type in semantic_types
    }
    if not required_semantic <= candidate_semantic:
        return False

    candidate_claim_keys = candidate_numeric | {
        (claim.claim_type.value, claim.value, claim.unit)
        for claim in candidate_semantic_claims
    }
    if require_bindings and candidate_claim_keys and not bindings:
        return False

    if bindings:
        allowed_by_id = {claim.claim_id: claim for claim in payload.claims}
        bound: set[tuple[str, str, str | None]] = set()
        for binding in bindings:
            canonical = allowed_by_id.get(binding.claim_id)
            if canonical is None or _claim_key(binding) != _claim_key(canonical):
                return False
            bound.add(_claim_key(binding))
        if require_bindings and not candidate_claim_keys <= bound:
            return False
    return True


def claims_from_values(values: list[dict[str, Any]]) -> list[CanonicalClaim]:
    """Build canonical claims for result explanation DTOs.

    ``values`` items require ``claim_type``, ``value``, and may include ``unit``,
    ``source_ref``, ``label``. IDs are content-addressed when omitted.
    """

    claims: list[CanonicalClaim] = []
    for item in values:
        payload = dict(item)
        payload["claim_type"] = ClaimType(payload["claim_type"])
        payload["value"] = _normalize_decimal(payload["value"])
        payload.setdefault(
            "claim_id",
            f"CLAIM-{canonical_hash(payload)[:16]}",
        )
        claims.append(CanonicalClaim.model_validate(payload))
    return claims
