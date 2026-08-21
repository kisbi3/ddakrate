from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from eligibility import ApplicationService
from eligibility.fixtures.hana_run import hana_run_product
from eligibility.fixtures.ibk_parent_benefit import ibk_parent_benefit_product
from eligibility.fixtures.kakao_26_week import kakao_26_week_product
from eligibility.fixtures.shinhan_youth_first import shinhan_youth_first_product
from eligibility.fixtures.user_001 import user_001
from eligibility.search.intent import IntentParser


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "v0.4"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = user_001()
    products = [
        shinhan_youth_first_product(),
        kakao_26_week_product(),
        ibk_parent_benefit_product(),
        hana_run_product(),
    ]
    service = ApplicationService(products, user_fact_stores={store.user_id: store})
    intent = IntentParser().parse(
        "1년 정도 월 30만원을 넣을 적금 중 나에게 가장 좋은 상품 5개를 찾아줘.",
        user_id=store.user_id,
    )
    session = service.create_search_session(
        user_id=store.user_id,
        intent=intent,
        as_of=date(2026, 8, 19),
        subscription_date=date(2026, 8, 20),
    )
    question = service.get_next_question(session.search_session_id)
    recommendation = service.get_top_recommendations(session.search_session_id)
    detail = service.get_product_recommendation_detail(
        session.search_session_id,
        recommendation.top_products[0].product_id,
        include_explanation=True,
    )
    trace = service.get_evaluation_trace(session.search_session_id)

    payload = {
        "intent": intent.model_dump(mode="json"),
        "session": service.get_search_status(session.search_session_id).model_dump(
            mode="json"
        ),
        "next_question": question.model_dump(mode="json") if question else None,
        "recommendations": recommendation.model_dump(mode="json"),
        "top_product_detail": detail.model_dump(mode="json"),
        "audit_event_count": len(trace),
        "audit_event_types": sorted({event.event_type.value for event in trace}),
    }
    output = OUT / "official-fixture-search-flow.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWROTE {output}")


if __name__ == "__main__":
    main()
