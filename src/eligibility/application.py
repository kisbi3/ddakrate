from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eligibility.audit import AuditEventType, AuditSession, canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.llm.system_prompts import QUESTION_GENERATION_SYSTEM_PROMPT
from eligibility.llm.grounding import (
    CanonicalQuestionPayload,
    ClaimBinding,
    build_question_payload,
    validate_grounded_text,
)
from eligibility.schema.enums import (
    FactRecordStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
)
from eligibility.schema.evaluation import MissingFactRequest
from eligibility.schema.user_fact import FactProvenance, UserFact, UserFactStore


class GeneratedQuestion(BaseModel):
    """Structured wording plus the grounding identifiers it claims to express."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    fact_type: str
    rule_id: str
    action_id: str | None
    reward_id: str | None
    claim_bindings: list[ClaimBinding] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        return value


class QuestionGenerator:
    """LLM-assisted wording with deterministic semantic grounding and fallback."""

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway
        self._cache: dict[str, str] = {}

    def generate(self, request: MissingFactRequest) -> str:
        fallback = self.deterministic_fallback(request)
        if self.gateway is None:
            return fallback
        # ApplicationService supplies grounded, product-aware wording and
        # explanation payloads for these known question families. Calling an LLM
        # first would add several seconds only to discard that wording afterward.
        if request.fact_type in {
            "SALARY_ACCOUNT_CHANGE_POSSIBLE",
            "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE",
            "SPECIAL_RATE_COUPON_VALID",
            "WILL_KAKAO_M1_MANUAL_DEPOSIT_DAY",
            "WILL_KN_TOUCH_DEPOSIT_DAY",
            "WILL_TOSS_MONTHLY_AUTO_TRANSFER_ALL",
            "KBANK_MYKIDS_ELIGIBLE",
        } or request.resolution_strategy in {
            ResolutionStrategy.QUERY_INSTITUTION,
            ResolutionStrategy.QUERY_MYDATA,
        }:
            return fallback
        canonical_payload = build_question_payload(request, fallback)
        payload = canonical_payload.model_dump(mode="json")
        cache_key = canonical_hash(payload)
        if cache_key in self._cache:
            return self._cache[cache_key]
        prompt = "CANONICAL_QUESTION_PAYLOAD:\n" + json.dumps(
            payload, ensure_ascii=False, indent=2
        )
        try:
            response = self.gateway.generate_structured(
                LLMPurpose.QUESTION_GENERATION,
                prompt,
                GeneratedQuestion,
                system_prompt=QUESTION_GENERATION_SYSTEM_PROMPT,
                metadata={"missing_fact_request_hash": cache_key},
            )
        except Exception:
            return fallback
        if not self._is_grounded(response.data, request, canonical_payload):
            return fallback
        if self._contains_rate(response.data.question):
            return fallback
        rendered = self._add_brand_context(response.data.question)
        if request.fact_type.startswith("NORMALIZED_ELIGIBILITY::"):
            rendered_compact = re.sub(r"\s+", "", rendered).casefold()
            required_details = [
                re.sub(r"\s+", "", term).casefold()
                for term in request.grounding_terms
                if term and term != "공식 가입대상"
            ]
            # Eligibility is a mandatory gate, so a fluent summary that drops
            # one of the published target conditions is less useful than the
            # exact deterministic wording. The LLM call remains observable and
            # may be accepted when it preserves every structured condition.
            if any(term not in rendered_compact for term in required_details):
                return fallback
        self._cache[cache_key] = rendered
        return rendered

    @staticmethod
    def deterministic_fallback(request: MissingFactRequest) -> str:
        if request.question:
            question = QuestionGenerator._strip_rate_wording(request.question)
            if "인터넷/모바일뱅킹에서 가입" in request.question:
                return "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?"
            if "주택담보대출 또는 전세자금대출" in request.question:
                return (
                    "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, "
                    "적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?"
                )
            if "교차거래 우대이율" in request.question:
                return (
                    "가입 3개월이 지난 달에 급여이체 실적을 만들거나, "
                    "KB국민카드를 30만원 이상 사용할 수 있나요?"
                )
            if request.fact_type == "SALARY_ACCOUNT_CHANGE_POSSIBLE":
                return (
                    "현재 급여를 어느 은행 계좌로 받고 계신가요? 다른 은행 상품이 더 "
                    "유리하다면 급여 수령계좌를 옮길 의향이 있는지도 함께 알려주세요."
                )
            if request.fact_type == "CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE":
                return (
                    "신한카드를 사용하고 계시다면 현재 카드대금은 어느 은행 계좌에서 "
                    "결제되나요? 더 유리하다면 결제계좌를 신한은행으로 바꿀 의향이 "
                    "있는지도 함께 알려주세요."
                )
            if request.fact_type == "SPECIAL_RATE_COUPON_VALID":
                return (
                    "신한은행 청년 처음적금에 적용되는 특별금리 쿠폰을 "
                    "가지고 계신가요?"
                )
            if "자동이체 전 회차 성공" in question:
                return "매월 자동이체가 빠짐없이 되도록 유지할 수 있으세요?"
            repeated_deposit = re.fullmatch(
                r"직접\s*입금(?:\s*누적)?\s*(\d+)(?:일\s*이상(?::\s*매일\s*우대\s*\d+번째)?|일차)"
                r"(?:\s*우대)?(?:을|를)?\s*목표로\s*관리할까요\?",
                question,
            )
            if repeated_deposit:
                days = repeated_deposit.group(1)
                return f"가입 기간 동안 매일 직접 입금해 {days}일 이상 채울 수 있으세요?"
            count_bonus = re.fullmatch(
                r"누적\s*(\d+)회\s*입금\s*보너스(?:을|를)?\s*목표로\s*관리할까요\?",
                question,
            )
            if count_bonus:
                count = count_bonus.group(1)
                return f"{count}일 동안 매일 직접 입금하는 것을 꾸준히 할 수 있으세요?"
            fraction = re.fullmatch(
                r"(?:전체\s*)?계약월수\s*(\d+)\s*/\s*(\d+)\s*이상\s*"
                r"(납입월\s*달성|자동이체\s*성공)(?:을|를)?\s*목표로\s*관리할까요\?",
                question,
            )
            if fraction:
                numerator, denominator, action = fraction.groups()
                if "자동이체" in action:
                    return (
                        f"가입 기간 동안 {denominator}개월 중 {numerator}개월 이상 "
                        "자동이체로 납입할 수 있으세요?"
                    )
                return (
                    f"가입 기간 동안 {denominator}개월 중 {numerator}개월 이상 "
                    "꾸준히 납입할 수 있으세요?"
                )
            question = re.sub(
                r"(?:을|를)?\s*목표로\s*관리할까요\?\s*$",
                "을 꾸준히 할 수 있으세요?",
                question,
            )
            question = re.sub(
                r"^이\s*상품의\s*(?:우대(?:금리)?\s*)?(?:을|를)?\s*받으려면\s*",
                "",
                question,
            )
            question = re.sub(
                r"해야\s*합니다\.\s*이\s*조건을\s*꾸준히\s*할\s*수\s*있으세요\?\s*$",
                "할 수 있으세요?",
                question,
            )
            rendered = QuestionGenerator._add_brand_context(question.strip())
            if (
                request.expected_semantic_type == FactSemanticType.FUTURE_INTENT
                and not re.search(r"가입\s*(?:후|기간)|앞으로", rendered)
            ):
                rendered = f"가입 후 {rendered}"
            return rendered

        if request.expected_semantic_type == FactSemanticType.FUTURE_INTENT:
            action = request.action_id or request.fact_type
            return QuestionGenerator._add_brand_context(
                f"앞으로 {action} 조건을 꾸준히 지킬 수 있으세요?"
            )

        if request.expected_semantic_type == FactSemanticType.SELF_REPORTED_FACT:
            return (
                f"과거 또는 현재 {request.fact_type} 사실에 해당했습니까?"
                " 답변은 개인화 판정에 사용되며 금융데이터 확인값과 구분하여 표시됩니다."
                " 기관 검증 정보가 아닙니다."
            )

        return f"{request.fact_type} 사실을 확인해 주시겠습니까?"

    @staticmethod
    def _add_brand_context(text: str) -> str:
        return re.sub(r"(?<!신한\s)SuperSOL", "신한 SuperSOL", text)

    @staticmethod
    def _contains_rate(text: str) -> bool:
        return bool(
            re.search(
                r"[+-]?\s*\d+(?:\.\d+)?\s*(?:%p|퍼센트포인트|%|퍼센트)",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _strip_rate_wording(text: str) -> str:
        text = re.sub(
            r"\s*(?:을|를)?\s*(?:받기\s*위해\s*)?"
            r"[+-]?\s*\d+(?:\.\d+)?\s*(?:%p|퍼센트포인트|%|퍼센트)"
            r"(?:\s*(?:우대(?:금리)?|금리))?",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s+", " ", text).strip()
        return re.sub(r"\s+([?.!,])", r"\1", text)

    @classmethod
    def _is_grounded(
        cls,
        generated: GeneratedQuestion,
        request: MissingFactRequest,
        payload: CanonicalQuestionPayload | None = None,
    ) -> bool:
        if generated.fact_type != request.fact_type:
            return False
        if generated.rule_id != request.requested_by_rule_id:
            return False
        if generated.action_id != request.action_id:
            return False
        if generated.reward_id != request.reward_id:
            return False

        canonical_payload = payload or build_question_payload(
            request, cls.deterministic_fallback(request)
        )
        return validate_grounded_text(
            generated.question,
            canonical_payload,
            bindings=generated.claim_bindings,
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def _numbers_are_grounded(candidate: str, source: str) -> bool:
        """Deprecated compatibility helper.

        New validation uses typed value/unit claims. This helper remains for
        callers that imported it directly, but intentionally returns a stricter
        unit-aware result for recognizable Korean financial units.
        """

        from eligibility.llm.grounding import extract_numeric_claims

        candidate_claims = {
            (claim.claim_type.value, claim.value, claim.unit)
            for claim in extract_numeric_claims(candidate)
        }
        source_claims = {
            (claim.claim_type.value, claim.value, claim.unit)
            for claim in extract_numeric_claims(source)
        }
        return candidate_claims <= source_claims


def submit_user_fact(
    store: UserFactStore,
    fact: UserFact,
    *,
    audit: AuditSession | None = None,
) -> UserFactStore:
    updated = store.with_fact(fact)
    if audit is not None:
        audit.emit(
            "APPLICATION_LAYER",
            AuditEventType.USER_FACT_RECEIVED,
            entity_refs={"fact_id": fact.fact_id, "fact_type": fact.fact_type},
            input_data={"store_user_id": store.user_id},
            output_data=fact,
            payload={
                "fact_type": fact.fact_type,
                "semantic_type": fact.semantic_type.value,
                "source_type": fact.source_type.value,
                "subject_person_id": fact.effective_subject_person_id,
                "value_hash": canonical_hash(fact.value),
            },
            provenance=[item.model_dump(mode="json") for item in fact.provenance],
        )
    return updated


class UserAnswerSubmission(BaseModel):
    """Web answer envelope tied to a concrete MissingFactRequest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_reference: str
    answer: Any
    answered_at: datetime


class UserAnswerRecord(BaseModel):
    """Append-only answer lifecycle record exposed to the Application Layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_record_id: str
    request_reference: str
    fact_id: str
    version: int
    status: FactRecordStatus
    supersedes_fact_id: str | None = None
    answered_at: datetime


class UserAnswerMapper:
    """Maps a UI answer to a typed UserFact without asserting its truth itself.

    The mapper uses the semantic expectation emitted by the deterministic core.
    Historical/current answers become SELF_REPORTED_FACT; forward-looking answers
    become FUTURE_INTENT.  Both remain USER_DECLARED at the evidence layer.
    """

    SUPPORTED_USER_SEMANTICS = {
        FactSemanticType.SELF_REPORTED_FACT,
        FactSemanticType.FUTURE_INTENT,
    }

    @staticmethod
    def request_reference(request: MissingFactRequest) -> str:
        if request.missing_fact_id:
            return request.missing_fact_id
        payload = {
            "fact_type": request.fact_type,
            "rule_id": request.requested_by_rule_id,
            "expected_semantic_type": (
                request.expected_semantic_type.value
                if request.expected_semantic_type is not None
                else None
            ),
            "action_id": request.action_id,
            "reward_id": request.reward_id,
        }
        return f"MFR-{canonical_hash(payload)[:16]}"

    def map(
        self,
        request: MissingFactRequest,
        submission: UserAnswerSubmission,
        *,
        user_id: str,
    ) -> UserFact:
        expected_ref = self.request_reference(request)
        if submission.request_reference != expected_ref:
            raise ValueError("User answer does not reference the supplied MissingFactRequest")
        semantic_type = request.expected_semantic_type
        if semantic_type not in self.SUPPORTED_USER_SEMANTICS:
            raise ValueError(
                "ASK_USER answer mapping requires expected_semantic_type to be "
                "SELF_REPORTED_FACT or FUTURE_INTENT"
            )
        answered_at = submission.answered_at
        if answered_at.tzinfo is None:
            answered_at = answered_at.replace(tzinfo=timezone.utc)
        fact_payload = {
            "request_reference": expected_ref,
            "user_id": user_id,
            "fact_type": request.fact_type,
            "semantic_type": semantic_type.value,
            "answer": submission.answer,
            "answered_at": answered_at.isoformat(),
        }
        return UserFact(
            fact_id=f"WEB-{canonical_hash(fact_payload)[:20]}",
            user_id=user_id,
            fact_type=request.fact_type,
            value=submission.answer,
            valid_from=answered_at.date(),
            source_type=FactSourceType.USER_DECLARED,
            semantic_type=semantic_type,
            provenance=[
                FactProvenance(
                    reference=f"web-answer/{expected_ref}",
                    description="User answer submitted through the Web interaction contract",
                    attributes={
                        "requested_by_rule_id": request.requested_by_rule_id,
                        "missing_fact_id": request.missing_fact_id,
                    },
                )
            ],
            collected_at=answered_at,
            confidence=1.0,
            request_reference=expected_ref,
        )


def submit_user_answer(
    store: UserFactStore,
    request: MissingFactRequest,
    submission: UserAnswerSubmission,
    *,
    mapper: UserAnswerMapper | None = None,
    audit: AuditSession | None = None,
) -> tuple[UserFactStore, UserFact]:
    """Map a Web answer into the fact store; callers then deterministically re-evaluate."""

    mapper = mapper or UserAnswerMapper()
    fact = mapper.map(request, submission, user_id=store.user_id)
    updated, superseded = store.with_answer_fact(fact)
    active = next(
        item
        for item in reversed(updated.facts)
        if item.request_reference == submission.request_reference
        and item.record_status == FactRecordStatus.ACTIVE
    )
    if audit is not None:
        for prior in superseded:
            audit.emit(
                "APPLICATION_LAYER",
                AuditEventType.USER_ANSWER_SUPERSEDED,
                entity_refs={
                    "fact_id": prior.fact_id,
                    "request_reference": submission.request_reference,
                },
                input_data=prior,
                output_data=active,
                payload={
                    "prior_fact_id": prior.fact_id,
                    "new_fact_id": active.fact_id,
                    "prior_version": prior.version,
                    "new_version": active.version,
                },
            )
        audit.emit(
            "APPLICATION_LAYER",
            AuditEventType.USER_FACT_RECEIVED,
            entity_refs={"fact_id": active.fact_id, "fact_type": active.fact_type},
            input_data={"request_reference": submission.request_reference},
            output_data=active,
            payload={
                "fact_type": active.fact_type,
                "semantic_type": active.semantic_type.value,
                "source_type": active.source_type.value,
                "version": active.version,
                "value_hash": canonical_hash(active.value),
            },
            provenance=[item.model_dump(mode="json") for item in active.provenance],
        )
    return updated, active


def revise_user_answer(
    store: UserFactStore,
    request: MissingFactRequest,
    submission: UserAnswerSubmission,
    *,
    mapper: UserAnswerMapper | None = None,
    audit: AuditSession | None = None,
) -> tuple[UserFactStore, UserFact, list[UserAnswerRecord]]:
    """Explicit revision operation with an auditable lifecycle projection."""

    updated, active = submit_user_answer(
        store,
        request,
        submission,
        mapper=mapper,
        audit=audit,
    )
    records = [
        UserAnswerRecord(
            answer_record_id=f"ANS-{canonical_hash({'fact_id': fact.fact_id, 'version': fact.version})[:20]}",
            request_reference=submission.request_reference,
            fact_id=fact.fact_id,
            version=fact.version,
            status=fact.record_status,
            supersedes_fact_id=fact.supersedes_fact_id,
            answered_at=fact.collected_at,
        )
        for fact in updated.answer_history(submission.request_reference)
    ]
    return updated, active, records
