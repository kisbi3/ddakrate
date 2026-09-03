from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Iterable

from eligibility.schema.enums import PreSearchAnswerStatus
from eligibility.schema.product import ProductDefinition
from eligibility.schema.search import (
    ContributionPlan,
    PlannedQuestion,
    PreSearchProfileEntry,
    ProductSearchIntent,
)


PRODUCT_TYPE = "PRODUCT_TYPE"
APPLICATION_CAPACITY = "APPLICATION_CAPACITY"
BIRTH_DATE = "BIRTH_DATE"
YOUTH_POLICY_ACCOUNT_HOLDING = "YOUTH_POLICY_ACCOUNT_HOLDING"
SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY = "SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY"
CONTRIBUTION_AND_TERM = "CONTRIBUTION_AND_TERM"
PROTECTION_AND_CMA_SCOPE = "PROTECTION_AND_CMA_SCOPE"
INSTITUTION_SCOPE = "INSTITUTION_SCOPE"
COMMON_BENEFIT_WILLINGNESS = "COMMON_BENEFIT_WILLINGNESS"
INSTITUTION_PRODUCT_HOLDING_HISTORY = "INSTITUTION_PRODUCT_HOLDING_HISTORY"

INITIAL_PRODUCT_TYPE_QUESTION = (
    "돈을 정기적으로 모으고 싶으신가요, 목돈을 일정 기간 맡기고 "
    "싶으신가요, 아니면 필요할 때 넣고 뺄 수 있는 계좌를 찾으시나요?"
)


QUESTION_ORDER = (
    PRODUCT_TYPE,
    CONTRIBUTION_AND_TERM,
    APPLICATION_CAPACITY,
    BIRTH_DATE,
    YOUTH_POLICY_ACCOUNT_HOLDING,
    SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY,
    PROTECTION_AND_CMA_SCOPE,
    INSTITUTION_SCOPE,
    COMMON_BENEFIT_WILLINGNESS,
    INSTITUTION_PRODUCT_HOLDING_HISTORY,
)


def pre_search_answer_examples(
    key: str,
    product_types: Iterable[str] = (),
    *,
    contribution_plan: ContributionPlan | None = None,
) -> list[str]:
    """Return deterministic natural-language examples for one pre-search question."""

    kinds = set(product_types)
    if key == PRODUCT_TYPE:
        return [
            "정기적으로 돈을 모으고 싶어요.",
            "목돈을 일정 기간 맡기고 싶어요.",
            "필요할 때 넣고 뺄 수 있는 계좌를 찾고 있어요.",
        ]
    if key == APPLICATION_CAPACITY:
        return [
            "개인 명의로 가입할 상품을 찾고 있어요.",
            "개인사업자 명의로 가입하려고 해요.",
            "법인 명의로 가입할 상품을 비교하고 싶어요.",
        ]
    if key == BIRTH_DATE:
        return [
            "1995년 3월 12일이에요.",
            "2001-08-24예요.",
            "생년월일은 알려드리기 어려워요.",
        ]
    if key == YOUTH_POLICY_ACCOUNT_HOLDING:
        return [
            "둘 다 가지고 있지 않아요.",
            "청년도약계좌를 가지고 있어요.",
            "청년미래적금에 이미 가입했어요.",
        ]
    if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
        return [
            "네, 현역병이에요.",
            "네, 사회복무요원이에요.",
            "아니요, 어느 대상에도 해당하지 않아요.",
        ]
    if key == CONTRIBUTION_AND_TERM:
        has_amount = bool(
            contribution_plan is not None
            and any(
                value is not None
                for value in (
                    contribution_plan.desired_periodic_amount,
                    contribution_plan.maximum_affordable_periodic_amount,
                    contribution_plan.preferred_start_amount,
                )
            )
        )
        has_term = bool(
            contribution_plan is not None
            and (
                contribution_plan.term_strictness == "ANY"
                or (
                    contribution_plan.selected_term_value is not None
                    and contribution_plan.selected_term_unit is not None
                )
            )
        )
        # Once one half of this combined question is known, do not show a
        # suggested answer that asks the user to repeat it. In particular, a
        # liquidity search that already has a balance needs only a comparison
        # horizon, not another balance prompt.
        if has_amount and not has_term:
            return [
                "1년 기준으로 비교해 주세요.",
                "6개월 정도로 비교해 주세요.",
                "기간은 아직 정하지 않았어요.",
            ]
        if has_term and not has_amount:
            if kinds and kinds <= {"PARKING_ACCOUNT", "CMA"}:
                return [
                    "평소 300만원 정도를 보관할 예정이에요.",
                    "보통 1천만원 정도 있지만 중간에 잔액이 달라질 수 있어요.",
                    "금액은 아직 정하지 않았어요.",
                ]
        if kinds == {"TIME_DEPOSIT"}:
            return [
                "1천만원을 1년 동안 맡기고 싶고, 기간은 꼭 1년이어야 해요.",
                "3천만원을 6개월 정도 맡길 수 있어요.",
                "금액은 정했지만 기간은 아직 비교해 보고 싶어요.",
            ]
        if kinds == {"INSTALLMENT_SAVINGS"}:
            return [
                "매달 30만원씩 1년 동안 모으고 싶어요.",
                "매주 5만원씩 넣고, 월 최대 30만원까지 가능해요.",
                "월 50만원까지 가능하고 기간은 비교해 보고 싶어요.",
            ]
        if kinds and kinds <= {"PARKING_ACCOUNT", "CMA"}:
            return [
                "평소 300만원 정도 보관하고 1년 기준으로 비교해 주세요.",
                "보통 1천만원 정도 있지만 중간에 잔액이 달라질 수 있어요.",
                "금액은 정했지만 비교 기간은 아직 모르겠어요.",
            ]
        return [
            "매달 30만원씩 1년 동안 모으고 싶어요.",
            "1천만원을 6개월 동안 맡기고 싶어요.",
            "평소 300만원 정도를 보관할 예정이에요.",
        ]
    if key == PROTECTION_AND_CMA_SCOPE:
        return [
            "예금자보호되는 입출금 계좌만 보고 싶어요.",
            "CMA도 함께 비교해 주세요.",
            "차이를 잘 모르겠으니 먼저 설명해 주세요.",
        ]
    if key == INSTITUTION_SCOPE:
        if "CMA" in kinds:
            return [
                "은행·저축은행·증권사 상품을 모두 비교해 주세요.",
                "은행(1금융권)과 증권사 상품만 비교해 주세요.",
                "증권사 CMA만 비교하고 싶어요.",
            ]
        if kinds == {"PARKING_ACCOUNT"}:
            return [
                "은행과 저축은행 파킹통장을 모두 비교해 주세요.",
                "은행(1금융권) 상품만 보고 싶어요.",
                "금융기관 유형은 상관없어요.",
            ]
        return [
            "은행·저축은행·증권사 상품을 모두 비교해 주세요.",
            "은행(1금융권) 상품만 보고 싶어요.",
            "금융기관 유형은 상관없어요.",
        ]
    if key == COMMON_BENEFIT_WILLINGNESS:
        return [
            "새 계좌 개설이나 급여계좌 변경, 카드 사용 모두 가능해요.",
            "새 계좌는 가능하지만 카드 발급이나 사용은 원하지 않아요.",
            "추가 거래 없이 받을 수 있는 혜택만 보고 싶어요.",
        ]
    if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
        return [
            "최근 6개월 동안 신한은행과 국민은행에 예금이나 적금이 있었어요.",
            "최근 6개월 동안 이용한 은행이나 저축은행의 예금·적금·청약은 없어요.",
            "어느 금융기관에 있었는지 확인이 필요해요.",
        ]
    return []


class DeterministicPreSearchQuestionPlanner:
    """Fixed policy for questions shown before product-specific verification.

    The class contains no LLM boundary. Given the same profile, intent, and
    candidate metadata it always selects the same next question and wording.
    """

    @staticmethod
    def initialize_profile(intent: ProductSearchIntent) -> dict[str, PreSearchProfileEntry]:
        profile = {
            key: PreSearchProfileEntry(question_key=key)
            for key in QUESTION_ORDER
        }
        DeterministicPreSearchQuestionPlanner.sync_from_intent(profile, intent)
        return profile

    @staticmethod
    def sync_from_intent(
        profile: dict[str, PreSearchProfileEntry],
        intent: ProductSearchIntent,
    ) -> None:
        now = datetime.now(timezone.utc)
        if intent.product_types:
            DeterministicPreSearchQuestionPlanner._mark_if_not_asked(
                profile,
                PRODUCT_TYPE,
                value={"product_types": list(intent.product_types)},
                source_text=(intent.source_utterances[-1] if intent.source_utterances else None),
                answered_at=now,
            )

        if intent.application_capacity is not None:
            DeterministicPreSearchQuestionPlanner._mark_if_not_asked(
                profile,
                APPLICATION_CAPACITY,
                value={"application_capacity": intent.application_capacity},
                source_text=(intent.source_utterances[-1] if intent.source_utterances else None),
                answered_at=now,
            )

        # ``PARKING_ACCOUNT`` and ``CMA`` were already explicitly selected by
        # the user's initial request.  The protection/CMA question exists only
        # to decide that scope; asking it again cannot change the search.
        if {"PARKING_ACCOUNT", "CMA"}.issubset(set(intent.product_types)):
            DeterministicPreSearchQuestionPlanner._mark_if_not_asked(
                profile,
                PROTECTION_AND_CMA_SCOPE,
                value={"liquid_product_scope": "INCLUDE_CMA"},
                source_text=(intent.source_utterances[-1] if intent.source_utterances else None),
                answered_at=now,
            )

        plan = intent.contribution_plan
        if (
            plan is not None
            and (
                plan.desired_periodic_amount is not None
                or plan.maximum_affordable_periodic_amount is not None
                or plan.preferred_start_amount is not None
            )
            and (
                plan.term_strictness == "ANY"
                or (
                    plan.selected_term_value is not None
                    and plan.selected_term_unit is not None
                )
            )
        ):
            DeterministicPreSearchQuestionPlanner._mark_if_not_asked(
                profile,
                CONTRIBUTION_AND_TERM,
                value={
                    "desired_amount_krw": plan.desired_periodic_amount,
                    "maximum_amount_krw": plan.maximum_affordable_periodic_amount,
                    "balance_or_lump_sum_krw": plan.preferred_start_amount,
                    "contribution_frequency": (
                        plan.frequency.value if plan.frequency is not None else None
                    ),
                    "term_value": plan.selected_term_value,
                    "term_unit": (
                        plan.selected_term_unit.value
                        if plan.selected_term_unit is not None
                        else None
                    ),
                    "term_strictness": plan.term_strictness,
                },
                source_text=(intent.source_utterances[-1] if intent.source_utterances else None),
                answered_at=now,
            )

    @staticmethod
    def _mark_if_not_asked(
        profile: dict[str, PreSearchProfileEntry],
        key: str,
        *,
        value: dict,
        source_text: str | None,
        answered_at: datetime,
    ) -> None:
        current = profile[key]
        if current.answer_status != PreSearchAnswerStatus.NOT_ASKED:
            return
        profile[key] = current.model_copy(
            update={
                "answer_status": PreSearchAnswerStatus.ANSWERED,
                "value": value,
                "source_text": source_text,
                "answered_at": answered_at,
            },
            deep=True,
        )

    def select_next(
        self,
        profile: dict[str, PreSearchProfileEntry],
        intent: ProductSearchIntent,
        products: Iterable[ProductDefinition],
    ) -> PlannedQuestion | None:
        products = list(products)
        self.sync_from_intent(profile, intent)
        applicable = [
            key for key in QUESTION_ORDER
            if self._is_applicable(key, products, intent=intent, profile=profile)
        ]
        for order, key in enumerate(applicable):
            if profile[key].answer_status != PreSearchAnswerStatus.NOT_ASKED:
                continue
            affected_product_ids = [product.product_id for product in products]
            if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
                affected_product_ids = [
                    product.product_id
                    for product in products
                    if self._is_soldier_tomorrow_savings(product)
                ]
            if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
                affected_product_ids = [
                    product.product_id
                    for product in products
                    if self._has_institution_product_holding_history_condition(product)
                ]
            explanation_details = {
                "question_owner": "DETERMINISTIC_BACKEND",
                "answer_interpreter": "LLM_STRUCTURED_PATCH",
                "question_key": key,
            }
            if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
                explanation_details["institution_history_options"] = (
                    self._institution_history_options(products)
                )
            return PlannedQuestion(
                question_id=f"PRESEARCH-{key}",
                question_kind="PRE_SEARCH_PROFILE",
                question=self._question(key, intent),
                affected_product_ids=affected_product_ids,
                score=Decimal(1_000_000 - order),
                product_context=None,
                explanation=self._explanation(key),
                explanation_details=explanation_details,
                question_stage="PRE_SEARCH",
                pre_search_key=key,
                answer_mode=(
                    "OPTIONS"
                    if key == INSTITUTION_PRODUCT_HOLDING_HISTORY
                    else "FREE_TEXT"
                ),
                answer_examples=pre_search_answer_examples(
                    key,
                    intent.product_types,
                    contribution_plan=intent.contribution_plan,
                ),
            )
        return None

    @staticmethod
    def _is_applicable(
        key: str,
        products: list[ProductDefinition],
        *,
        intent: ProductSearchIntent | None = None,
        profile: dict[str, PreSearchProfileEntry] | None = None,
    ) -> bool:
        if key in {
            PRODUCT_TYPE,
            APPLICATION_CAPACITY,
            CONTRIBUTION_AND_TERM,
            INSTITUTION_SCOPE,
        }:
            return True
        if key in {
            YOUTH_POLICY_ACCOUNT_HOLDING,
        }:
            selected_types = (
                set(intent.product_types)
                if intent is not None
                else {product.product_type for product in products}
            )
            return "INSTALLMENT_SAVINGS" in selected_types
        if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
            # This is a global fact for the *remaining* 장병내일준비적금
            # candidates, not a question every savings search must answer.
            # For example, a one-year search already excludes the 15-month
            # product by term before this stage; asking then would not affect
            # any visible result.
            return any(
                DeterministicPreSearchQuestionPlanner._is_soldier_tomorrow_savings(
                    product
                )
                for product in products
            )
        if key == BIRTH_DATE:
            return (
                intent is None
                or intent.application_capacity in {None, "INDIVIDUAL", "SOLE_PROPRIETOR"}
            )
        product_types = {product.product_type for product in products}
        if key == PROTECTION_AND_CMA_SCOPE:
            return {"PARKING_ACCOUNT", "CMA"}.issubset(product_types)
        if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
            return any(
                DeterministicPreSearchQuestionPlanner._has_institution_product_holding_history_condition(
                    product
                )
                for product in products
            )
        return key == COMMON_BENEFIT_WILLINGNESS

    @staticmethod
    def _has_institution_product_holding_history_condition(product: ProductDefinition) -> bool:
        """Whether a bank/저축은행 product uses a prior product-holding condition."""

        sector = product.metadata.institution_sector if product.metadata is not None else None
        if sector not in {"BANK", "SAVINGS_BANK"}:
            return False
        stack: list[object] = [
            rule.rule.model_dump(mode="json") for rule in product.preferential_rules
        ]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                fact_type = str(node.get("fact_type") or "").upper()
                if "PRODUCT_HOLDING" in fact_type:
                    return True
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return False

    @classmethod
    def _institution_history_options(cls, products: Iterable[ProductDefinition]) -> list[dict[str, str]]:
        """Return all relevant 1금융권/저축은행 toggles, without duplicates."""

        options: dict[tuple[str, str], dict[str, str]] = {}
        for product in products:
            metadata = product.metadata
            if (
                metadata is None
                or metadata.institution_sector not in {"BANK", "SAVINGS_BANK"}
            ):
                continue
            key = (metadata.institution_sector, metadata.institution_name or product.institution_id)
            options[key] = {
                "name": metadata.institution_name or product.institution_id,
                "sector": metadata.institution_sector,
            }
        return sorted(
            options.values(),
            key=lambda item: (
                item["sector"],
                re.sub(
                    r"(?:\(주\)|주식회사|㈜|\s)+",
                    "",
                    item["name"],
                ),
            ),
        )

    @staticmethod
    def _is_soldier_tomorrow_savings(product: ProductDefinition) -> bool:
        """Whether the product needs the shared 장병내일준비 eligibility fact."""

        metadata = product.metadata
        searchable_text = " ".join(
            value
            for value in (
                product.name,
                metadata.target_customer_summary if metadata is not None else None,
            )
            if value
        )
        if "장병내일준비" in searchable_text:
            return True

        # Catalogs may use institution-specific fact names (for example
        # ``WOORI_ARMY_ELIGIBILITY_VERIFIED``), so inspect the rule tree rather
        # than relying only on a product name.
        stack: list[object] = [product.eligibility_rule.model_dump(mode="json")]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                fact_type = str(node.get("fact_type") or "").upper()
                if "SOLDIER" in fact_type or "ARMY" in fact_type:
                    return True
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return False

    @staticmethod
    def _question(key: str, intent: ProductSearchIntent) -> str:
        if key == PRODUCT_TYPE:
            return INITIAL_PRODUCT_TYPE_QUESTION
        if key == APPLICATION_CAPACITY:
            return (
                "개인 명의, 개인사업자 명의, 법인 명의 중 어떤 명의로 가입할 "
                "상품을 찾으시나요? 외국인이면 말해주세요."
            )
        if key == BIRTH_DATE:
            return (
                "가입 가능한 연령대의 상품을 한 번에 확인할 수 있도록 생년월일을 "
                "알려주세요."
            )
        if key == YOUTH_POLICY_ACCOUNT_HOLDING:
            return "청년미래적금이 이미 있거나 청년도약계좌에 중복가입되어 있나요?"
        if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
            return (
                "현역병, 상근예비역, 의무경찰, 대체복무요원, 사회복무요원 중 "
                "하나에 해당하시나요? 모르거나 확인할 수 없으면 가입 불가로 "
                "처리됩니다."
            )
        if key == CONTRIBUTION_AND_TERM:
            kinds = set(intent.product_types)
            plan = intent.contribution_plan
            has_amount = bool(
                plan is not None
                and (
                    plan.desired_periodic_amount is not None
                    or plan.maximum_affordable_periodic_amount is not None
                    or plan.preferred_start_amount is not None
                )
            )
            has_term = bool(
                plan is not None
                and (
                    plan.term_strictness == "ANY"
                    or (
                        plan.selected_term_value is not None
                        and plan.selected_term_unit is not None
                    )
                )
            )
            if has_amount and not has_term:
                if kinds and kinds <= {"PARKING_ACCOUNT", "CMA"}:
                    return "말씀하신 잔액을 어느 정도 기간으로 비교해 볼까요?"
                return (
                    "말씀하신 금액을 얼마나 맡기거나 모으고 싶으세요? 대략적인 "
                    "기간인지 반드시 지켜야 하는 기간인지도 알려주세요."
                )
            if has_term and not has_amount:
                if kinds and kinds <= {"PARKING_ACCOUNT", "CMA"}:
                    return "그 기간 동안 평소 어느 정도 잔액을 보관할 예정인가요?"
                return "말씀하신 기간을 기준으로 얼마를 맡기거나 정기적으로 넣고 싶으세요?"
            if kinds == {"TIME_DEPOSIT"}:
                return (
                    "얼마 정도의 목돈을, 얼마나 맡기고 싶으세요? 대략적인 기간인지 "
                    "반드시 지켜야 하는 기간인지도 함께 알려주세요."
                )
            if kinds == {"INSTALLMENT_SAVINGS"}:
                return (
                    "얼마씩, 어느 주기로, 얼마나 모으고 싶으세요? 부담 가능한 최대 "
                    "금액이나 반드시 지켜야 하는 기간이 있다면 함께 알려주세요."
                )
            if kinds and kinds <= {"PARKING_ACCOUNT", "CMA"}:
                return (
                    "평소 어느 정도 잔액을 보관할 예정인가요? 수익을 비교할 "
                    "대략적인 기간도 함께 알려주세요."
                )
            return (
                "목돈으로 맡길 수 있는 금액이나 정기적으로 넣을 금액, 그리고 원하는 "
                "기간을 함께 알려주세요."
            )
        if key == PROTECTION_AND_CMA_SCOPE:
            return (
                "예금자보호가 되는 입출금 계좌만 볼까요, 아니면 예금자보호 대상이 "
                "아니거나 수익이 달라질 수 있는 CMA도 함께 비교할까요?"
            )
        if key == INSTITUTION_SCOPE:
            kinds = set(intent.product_types)
            if "CMA" in kinds:
                return (
                    "은행·저축은행의 파킹통장과 증권사의 CMA를 모두 비교해도 "
                    "괜찮으신가요, 아니면 제외하고 싶은 금융기관 유형이 있으신가요?"
                )
            if kinds == {"PARKING_ACCOUNT"}:
                return (
                    "은행과 저축은행의 파킹통장을 모두 비교해도 괜찮으신가요, "
                    "아니면 은행(1금융권) 상품만 보고 싶으신가요?"
                )
            return (
                "은행(1금융권) 상품만 볼까요, 아니면 저축은행이나 증권사 상품도 함께 "
                "비교해도 괜찮으신가요?"
            )
        if key == COMMON_BENEFIT_WILLINGNESS:
            return (
                "더 유리한 혜택을 받을 수 있다면 새 금융기관 거래, 급여계좌 변경, "
                "카드 사용 같은 추가 조건을 활용할 의향이 있으신가요?"
            )
        if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
            return (
                "가입 직전 6개월 동안 예금·적금·청약을 보유했던 은행이나 저축은행이 "
                "있나요? 있다면 금융기관 이름을 모두 알려주세요. 없었다면 없다고 "
                "말씀해 주세요."
            )
        raise KeyError(key)

    @staticmethod
    def _explanation(key: str) -> str:
        if key == APPLICATION_CAPACITY:
            return (
                "한 번 확인한 가입 명의를 모든 상품의 공식 가입대상 판정에 "
                "공통으로 적용합니다."
            )
        if key == BIRTH_DATE:
            return (
                "생년월일로 가입일 기준 만 나이를 계산해 연령 제한이 있는 상품에 "
                "공통으로 적용합니다."
            )
        if key == YOUTH_POLICY_ACCOUNT_HOLDING:
            return (
                "청년 정책형 적금의 중복가입 제한을 모든 청년미래적금 후보에 "
                "공통으로 적용합니다."
            )
        if key == SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY:
            return (
                "한 번 확인한 복무 대상 여부를 모든 장병내일준비적금 후보의 "
                "가입대상 판정에 공통으로 적용합니다."
            )
        if key == COMMON_BENEFIT_WILLINGNESS:
            return (
                "특정 상품의 우대 달성을 확정하는 질문이 아니라, 이 행동이 필요한 "
                "상품을 비교 대상으로 유지할지 정하기 위한 공통 의향 질문입니다."
            )
        if key == INSTITUTION_PRODUCT_HOLDING_HISTORY:
            return (
                "은행·저축은행의 첫거래 우대처럼 과거 상품 보유 이력이 필요한 조건을 "
                "한 번에 확인합니다. 답변은 상품별 공식 가입·우대 판정 전에 다시 "
                "확인해야 합니다."
            )
        if key == PROTECTION_AND_CMA_SCOPE:
            return (
                "파킹통장과 CMA는 예금자보호 여부와 수익의 확정성이 다를 수 있어 "
                "비교할 상품 범위를 확인하는 질문입니다."
            )
        return (
            "상품 범위를 좁히고 세전 금리와 세전 예상 이자를 계산하기 위한 "
            "사전 질문입니다."
        )
