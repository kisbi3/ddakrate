"""Small, deterministic policy for user-facing eligibility questions.

The catalog still contains a mixture of historic/current facts and conditions
that a customer can perform after opening an account.  The latter must never be
phrased as though they had to be true before the search begins.
"""
from __future__ import annotations

from eligibility.schema.enums import FactSemanticType
from eligibility.schema.evaluation import MissingFactRequest


_FUTURE_ACTION_MARKERS = (
    "CONSENT",
    "MARKETING",
    "PAYMENT_ACCOUNT",
    "PAYMENT_",
    "AUTO_TRANSFER",
    "AUTOMATIC_TRANSFER",
    "RECURRING_PAYMENT",
    "APP_USAGE",
    "APP_LOGIN",
    "MEMBERSHIP",
    "REGISTERED",
    "REGISTRATION",
    "CARD_USAGE",
    "CARD_SPEND",
    "CARD_MONTHLY_SPEND",
    "SALARY_LINKAGE",
    "UTILITY_PAYMENT",
    "BALANCE_THRESHOLD",
    "LIVING_EXPENSE",
    "PENSION_LINKAGE",
    "SUBSCRIPTION_CHANNEL",
    "CROSS_TRANSACTION",
    "HOME_LOAN_HELD_THROUGH",
)
_HISTORICAL_OR_CURRENT_MARKERS = (
    "EXISTING_",
    "_HELD",
    "_HISTORY",
    "HOLDING",
    "PRIOR_",
    "FIRST_DEPOSIT_OR_SAVINGS_CUSTOMER",
)
_ROUTINE_ONBOARDING_MARKERS = (
    "IDENTIFICATION_DOCUMENT",
    "IDENTITY_DOCUMENT",
    "RESIDENT_REGISTRATION",
    "DRIVER_LICENSE",
    "주민등록증",
    "운전면허증",
)
_OPAQUE_GENERIC_QUESTIONS = (
    "가입후우대에필요한조건을수행하고유지할수있나요",
    "우대조건을수행할수있나요",
    "조건을수행할수있나요",
    "조건에해당하는지알려주세요",
)
_INFORMATION_ONLY_MARKERS = (
    "FEE_WAIVER",
    "FEE_EXEMPT",
    "COMMISSION_WAIVER",
    "TRANSFER_FEE",
    "ATM_FEE",
    "PRIZE",
    "GIFT",
)


def is_future_action_fact(fact_type: str) -> bool:
    """Whether a yes/no answer is an after-opening capability, not a fact now."""

    key = fact_type.upper()
    if any(marker in key for marker in _HISTORICAL_OR_CURRENT_MARKERS):
        return False
    return any(marker in key for marker in _FUTURE_ACTION_MARKERS)


def is_routine_onboarding_fact(fact_type: str, question: str | None = None) -> bool:
    """True for normal sign-up preparation that does not improve the ranking."""

    text = f"{fact_type} {question or ''}".upper()
    return any(marker in text for marker in _ROUTINE_ONBOARDING_MARKERS)


def is_official_random_promotion_result_fact(fact_type: str) -> bool:
    """True when only the institution can determine a promotion outcome."""

    key = fact_type.upper()
    return key.startswith(("PROMOTION.", "PROMOTION_")) and any(
        marker in key
        for marker in ("_WON", "_WINNER", "_DRAW_RESULT", "_WIN_COUNT")
    )


def is_institution_product_holding_history_fact(fact_type: str) -> bool:
    """Whether a bank ledger fact belongs to the shared history question."""

    return "PRODUCT_HOLDING" in fact_type.upper()


def is_first_transaction_history_fact(request: MissingFactRequest) -> bool:
    """Recognize a transaction-history question without trusting loose wording."""

    key = request.fact_type.upper()
    if is_future_action_fact(key):
        return False
    return (
        "FIRST_TRANSACTION" in key
        or "FIRST_DEPOSIT_OR_SAVINGS_CUSTOMER" in key
        or "NEW_CUSTOMER" in key
        or (
            request.expected_semantic_type == FactSemanticType.SELF_REPORTED_FACT
            and any("첫거래" in term or "신규고객" in term for term in request.grounding_terms)
        )
    )


def is_opaque_generic_question(question: str | None) -> bool:
    """Return True when wording hides the actual action or threshold."""

    compact = "".join((question or "").split()).rstrip("?.")
    return any(marker in compact for marker in _OPAQUE_GENERIC_QUESTIONS)


def is_information_only_fact(fact_type: str) -> bool:
    """Recognize non-rate ancillary benefits that should not drive questions."""

    key = fact_type.upper()
    return any(marker in key for marker in _INFORMATION_ONLY_MARKERS)


def is_card_benefit_request(request: MissingFactRequest) -> bool:
    """Recognize a card-dependent rate condition without name guessing.

    The normalized catalog still uses several card fact namespaces.  Inspect
    the typed identifiers first and grounded wording second so an explicit
    global refusal of card issuance/use can suppress every equivalent product
    question, while unrelated payment/utility conditions remain untouched.
    """

    text = " ".join(
        [
            str(request.action_id or ""),
            request.fact_type,
            request.question or "",
            *request.grounding_terms,
        ]
    ).upper()
    return any(
        marker in text
        for marker in (
            "CARD_",
            "CARD.",
            "CARD ACTIVITY",
            "카드 발급",
            "카드 사용",
            "카드 이용",
            "카드 결제",
            "카드 매입",
            "결제계좌",
        )
    )


def is_salary_benefit_request(request: MissingFactRequest) -> bool:
    text = " ".join(
        [
            str(request.action_id or ""),
            request.fact_type,
            request.question or "",
            *request.grounding_terms,
        ]
    ).upper()
    return any(marker in text for marker in ("SALARY", "INCOME_CREDIT", "급여이체", "급여 수령"))


def deterministic_family_question(request: MissingFactRequest) -> str | None:
    """Render known user-variable families without inventing product facts."""

    key = str(request.action_id or request.fact_type).upper()
    source_question = request.question or ""
    if "인터넷/모바일뱅킹에서 가입" in source_question:
        return "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?"
    if "주택담보대출 또는 전세자금대출" in source_question:
        return (
            "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, "
            "적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?"
        )
    if "교차거래 우대이율" in source_question:
        return (
            "가입 3개월이 지난 달에 급여이체 실적을 만들거나, "
            "KB국민카드를 30만원 이상 사용할 수 있나요?"
        )
    if "AUTO_TRANSFER" in key or "AUTOMATIC_TRANSFER" in key:
        return "가입 후 자동이체로 납입하는 조건을 유지할 수 있나요?"
    if "CARD_MONTHLY_SPEND" in key or "CARD_SPEND" in key or "CARD_USAGE" in key:
        return "카드 이용실적은 한 달에 최대 얼마까지 가능하신가요?"
    if "NEW_CARD_ISSUANCE" in key:
        return "우대금리를 위해 새 카드를 발급받을 의향이 있나요?"
    if "CARD_SETTLEMENT_ACCOUNT" in key:
        return "우대금리를 위해 카드 결제계좌를 지정된 계좌로 바꿀 수 있나요?"
    if "SALARY" in key or "INCOME_CREDIT" in key:
        return "가입 후 급여를 이 계좌로 받을 수 있나요?"
    if "MARKETING" in key or "CONSENT" in key:
        return "가입 후 상품 안내·마케팅 수신 동의를 유지할 수 있나요?"
    if "BALANCE" in key:
        return "우대금리를 위해 필요한 계좌 잔액을 유지할 수 있나요?"
    if "PAYMENT" in key or "BILL" in key:
        # A payment/account identifier alone does not tell us whether the
        # customer needs an existing card, a newly issued card, an automatic
        # bill payment, or merely an account designation. Asking a generic
        # "결제나 납부" question would make the customer guess. A concrete
        # source-grounded question is preserved earlier; otherwise this family
        # remains unasked until the catalog supplies the missing action.
        return None
    if "APP_LOGIN" in key or "APP_USAGE" in key:
        return "가입 후 우대에 필요한 앱 이용 조건을 충족할 수 있나요?"
    if "MEMBERSHIP" in key or "REGISTRATION" in key:
        return "가입 후 우대에 필요한 서비스 회원가입을 진행할 수 있나요?"
    return None


def normalize_user_question_request(request: MissingFactRequest) -> MissingFactRequest:
    """Apply safe present-vs-future semantics and wording before presentation.

    A legacy rule may have modeled these actions as a self-reported fact.  The
    evaluator accepts the resulting future-intent answer through a narrow
    compatibility path, while the user-facing question now asks the truthful
    thing: whether the action can be performed after opening.
    """

    if request.semantic_input is not None:
        return request
    source_question = request.question or ""
    source_rewrite = None
    if "인터넷/모바일뱅킹에서 가입" in source_question:
        source_rewrite = "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?"
    elif "주택담보대출 또는 전세자금대출" in source_question:
        source_rewrite = (
            "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, "
            "적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?"
        )
    elif "교차거래 우대이율" in source_question:
        source_rewrite = (
            "가입 3개월이 지난 달에 급여이체 실적을 만들거나, "
            "KB국민카드를 30만원 이상 사용할 수 있나요?"
        )
    if source_rewrite is not None:
        expected_semantic_type = (
            FactSemanticType.FUTURE_INTENT
            if is_future_action_fact(request.fact_type)
            or any(
                marker in (request.question or "")
                for marker in (
                    "인터넷/모바일뱅킹에서 가입",
                    "주택담보대출 또는 전세자금대출",
                    "교차거래 우대이율",
                )
            )
            else request.expected_semantic_type
        )
        return request.model_copy(
            update={
                "expected_semantic_type": expected_semantic_type,
                "question": source_rewrite,
            },
            deep=True,
        )
    if not is_future_action_fact(request.fact_type):
        return request
    key = request.fact_type.upper()
    # A normalized product can already carry an exact, source-grounded action
    # question (for example, the required number of auto-transfer months).
    # Replacing that text with one generic sentence made different conditions
    # look like the same repeated question.  Preserve the concrete wording and
    # change only its time semantics.
    if request.question and request.question.strip() and not is_opaque_generic_question(
        request.question
    ):
        return request.model_copy(
            update={
                "expected_semantic_type": FactSemanticType.FUTURE_INTENT,
                "question": request.question.strip(),
            },
            deep=True,
        )
    grounding = next(
        (
            term.strip()
            for term in request.grounding_terms
            if term.strip() not in {"우대조건", "가입조건", "공식 가입대상"}
        ),
        None,
    )
    question = deterministic_family_question(request)
    if question is None and grounding is not None:
        question = f"가입 후 {grounding} 조건을 충족할 수 있나요?"
    if question is None:
        # Leave the request non-renderable. The planner keeps the underlying
        # uncertainty in the optimistic/data-incomplete state, but does not ask
        # the customer to answer a sentence whose meaning is unknown.
        question = None
    return request.model_copy(
        update={
            "expected_semantic_type": FactSemanticType.FUTURE_INTENT,
            "question": question,
        },
        deep=True,
    )
