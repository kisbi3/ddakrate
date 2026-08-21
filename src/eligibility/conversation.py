from __future__ import annotations

import json
from typing import Any

from eligibility.audit import canonical_hash
from eligibility.llm import LLMGateway, LLMPurpose
from eligibility.schema.conversation import ConversationPlan


class ConversationOrchestrator:
    """LLM-only natural-language router over narrow ApplicationService operations.

    The model decides what the user means and which domain operations to call. It
    never decides financial feasibility, rate, interest, eligibility, or ranking.
    Those remain deterministic backend responsibilities after the plan is returned.
    """

    def __init__(self, gateway: LLMGateway) -> None:
        self.gateway = gateway

    def interpret(self, message: str, *, context: dict[str, Any]) -> ConversationPlan:
        message = message.strip()
        if not message:
            raise ValueError("message must not be empty")
        prompt = (
            "사용자의 금융상품 검색 후속 발화를 현재 SearchSession 맥락에서 이해하고, "
            "아래에 허용된 ApplicationService 도메인 operation만 선택하세요. 사용자는 "
            "구조화 명령을 말하지 않으며 평범한 자연어를 사용합니다. 여러 상태를 한 번에 "
            "바꾸면 actions를 여러 개 생성하세요. 과거 workflow 이력보다 사용자의 최신 명시적 "
            "의도를 우선하세요.\n\n"
            "권장 operation:\n"
            "- UPDATE_SEARCH_INTENT: HardConstraint/Preference/Capability/NumericPreference/"
            "Global ContributionPlan/RankingObjective 변경. intent_patch 사용.\n"
            "- SET_PRODUCT_CONTRIBUTION_CHOICE: 특정 상품 납입 선택을 현재 값으로 설정. "
            "과거 선택이 있든, 질문을 거절했든, 아직 값이 없든 product_id, field, new_value 사용.\n"
            "- SET_PRODUCT_EXCLUSION: 특정 상품의 현재 제외 여부 설정. product_id와 excluded=true/false 사용. "
            "'다시 보여줘', '뺀 건 취소'는 excluded=false입니다.\n"
            "- CLEAR_USER_DECLARED_FACT: 사용자가 이전 self-reported/future-intent 값을 '모르겠다/미정'으로 되돌릴 때 fact_type 사용. authoritative fact에는 사용 금지.\n"
            "- CLEAR_PRODUCT_CONTRIBUTION_CHOICE: 특정 상품 납입 선택을 '아직 정하지 않음'으로 되돌릴 때 product_id, field 사용.\n"
            "- SHOW_CURRENT_RESULTS: 사용자가 더 질문받지 않고 이 turn에서 현재 결과를 보고 싶을 때 사용. persistent state를 바꾸지 않습니다.\n"
            "- REVISE_USER_DECLARED_FACT: 현재 user-declared fact의 최신 값을 설정/수정. fact_type, new_value 사용. "
            "authoritative fact는 수정 대상으로 삼지 마세요.\n"
            "- SUBMIT_ACTIVE_QUESTION_ANSWER: 현재 active question에 대한 자연어 답을 structured answer로 변환.\n"
            "- REVISE_USER_ANSWER: 기존 request_reference 기반 답변 수정이 명확할 때만 사용.\n"
            "- REVISE_PRODUCT_CONTRIBUTION_CHOICE / EXCLUDE_PRODUCT: 기존 typed client 호환용 operation. "
            "새 자연어 orchestration에서는 가능하면 SET_* operation을 우선하세요.\n"
            "- NO_OP: 금융 검색 상태 수정이 없는 경우.\n\n"
            "핵심 원칙:\n"
            "1) 금융판정, affordability, cashflow, 금리, 세후이자, Top 5를 계산하지 마세요.\n"
            "2) context에 있는 product_id, fact_type, request_reference, allowed option만 사용하세요.\n"
            "3) MUTABLE_SEARCH_STATE는 사용자 최신 발화로 바꿀 수 있지만 AUTHORITATIVE_FACT_SUMMARY는 바꾸지 마세요.\n"
            "4) 과거 declined/answered/excluded 이력이 있어도 사용자가 새 값을 명시하면 그 최신 의미를 설정하세요.\n"
            "5) 현재 질문의 가능한 옵션이 1천/2천/3천이고 사용자가 '제일 큰 걸로'라고 하면 "
            "현재 allowed option 중 최대값을 선택할 수 있습니다. 옵션 자체는 계산하지 마세요.\n"
            "6) '그거', '아까 그거', '뺀 건 취소' 같은 표현은 active question, recent product focus, excluded products를 "
            "이용해 해석하세요. 의미가 실제로 모호하면 NO_OP가 아니라 backend clarification을 유도할 수 있는 안전한 action만 선택하세요.\n"
            "7) 사용자가 '다시 찾아줘'라고 이미 말했으면 추가 confirmation을 만들지 마세요.\n"
            "8) '잘 모르겠어', '아직 정하지 말자'는 현재 mutable 값을 미결정으로 되돌리는 의도로 해석할 수 있습니다. domain에 맞는 CLEAR 또는 UNKNOWN/NEUTRAL intent patch를 사용하세요.\n"
            "9) '질문은 그만하고 지금 결과 보여줘'는 SHOW_CURRENT_RESULTS입니다. UNKNOWN을 SATISFIED/ACHIEVABLE로 바꾸지 마세요.\n"
            "10) SEARCH_WORKING_NOTE는 continuity 보조자료이고 Structured Backend State가 항상 우선합니다.\n\n"
            "CURRENT_CONTEXT:\n"
            + json.dumps(context, ensure_ascii=False, indent=2, default=str)
            + "\n\nUSER_MESSAGE:\n"
            + message
        )
        response = self.gateway.generate_structured(
            LLMPurpose.CONVERSATION_ORCHESTRATION,
            prompt,
            ConversationPlan,
            metadata={
                "message_hash": canonical_hash(message),
                "context_hash": canonical_hash(context),
            },
        )
        return response.data
