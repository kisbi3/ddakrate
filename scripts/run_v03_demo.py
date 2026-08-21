from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eligibility.application import QuestionGenerator, submit_user_fact
from eligibility.audit import AuditSession, InMemoryAuditSink, format_event_sequence
from eligibility.engine.evaluator import FinancialEligibilityEngine
from eligibility.fixtures.extraction import (
    salary_envelope_document_sections_payload,
    salary_envelope_extraction_draft,
)
from eligibility.fixtures.future_goals import (
    SALARY_INTENT_FACT_TYPE,
    STEP_INTENT_FACT_TYPE,
    future_intent_fact,
    salary_envelope_context,
    salary_envelope_user,
    step_30000_300d_product,
    step_30000_context,
    step_30000_user,
)
from eligibility.goal import GoalFactory, GoalTracker
from eligibility.ingestion import (
    DocumentSection,
    RuleExtractor,
    activate_approved_draft,
    approve_rule_draft,
    product_definition_from_draft,
    compare_drafts,
)
from eligibility.llm import LLMGateway, LLMPurpose, MockLLMAdapter
from eligibility.visualization import product_rule_to_mermaid, user_facts_to_mermaid


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "v0.3"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_json(filename: str, value: Any) -> None:
    (OUTPUT / filename).write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(filename: str, value: str) -> None:
    (OUTPUT / filename).write_text(value, encoding="utf-8")


def _rule_summary(evaluation) -> dict[str, Any]:
    rule = evaluation.preferential_rule_results[0]
    return {
        "status": rule.status.value,
        "reason_code": rule.reason_code,
        "progress": rule.progress.model_dump(mode="json") if rule.progress else None,
        "missing_fact_types": [item.fact_type for item in rule.missing_facts],
        "confirmed_rate": str(evaluation.rates.confirmed_rate),
        "realizable_rate": str(evaluation.rates.realizable_rate),
        "user_specific_conditional_upper_rate": str(
            evaluation.rates.user_specific_conditional_upper_rate
        ),
    }


def salary_flow(sink: InMemoryAuditSink) -> dict[str, Any]:
    audit = AuditSession(
        sink,
        request_id="REQ-SALARY-DEMO-001",
        trace_id="TRACE-SALARY-DEMO-001",
    )
    golden_draft = salary_envelope_extraction_draft()
    sections = [
        DocumentSection.model_validate(item)
        for item in salary_envelope_document_sections_payload()
    ]
    gateway = LLMGateway(
        MockLLMAdapter(
            {LLMPurpose.RULE_EXTRACTION: golden_draft.model_dump(mode="json")}
        ),
        audit=audit,
    )
    record = RuleExtractor(gateway, audit=audit).extract(sections)
    approved = approve_rule_draft(
        record,
        reviewer_id="HUMAN-DEMO-REVIEWER",
        note="Golden extraction demo reviewed",
        audit=audit,
    )
    active, version = activate_approved_draft(approved)
    assert active.draft is not None
    product = product_definition_from_draft(active.draft)

    engine = FinancialEligibilityEngine()
    context = salary_envelope_context()
    initial_store = salary_envelope_user()
    before = engine.evaluate_product(product, initial_store, context, audit=audit)
    question = QuestionGenerator().generate(before.missing_facts[0])

    intent = future_intent_fact(SALARY_INTENT_FACT_TYPE, True)
    answered_store = submit_user_fact(initial_store, intent, audit=audit)
    after = engine.evaluate_product(product, answered_store, context, audit=audit)

    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=after,
        context=context,
        subscription_id="SUB-SALARY-DEMO-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    update = GoalTracker(audit).update_accumulative(
        goal,
        current_progress=4,
        as_of=context.subscription_date,
        remaining_opportunities=2,
    )

    write_json("salary_document_sections.json", sections)
    write_json("salary_extraction_draft.json", active.draft)
    write_json("salary_extraction_review.json", approved)
    write_json("salary_active_rule_version.json", version)
    write_json("salary_before_intent.json", before)
    write_text("salary_question.txt", question + "\n")
    write_json("salary_future_intent_fact.json", intent)
    write_json("salary_after_intent.json", after)
    write_text(
        "salary_future_rule.mmd",
        product_rule_to_mermaid(product, include_source=True),
    )
    write_text(
        "salary_future_user_facts.mmd",
        user_facts_to_mermaid(answered_store, include_provenance=True),
    )
    write_json("salary_goal_created.json", goal)
    write_json("salary_goal_update_4_of_6.json", update)
    if update.alert is not None:
        write_json("salary_alert_critical.json", update.alert)

    golden_metrics = compare_drafts(active.draft, golden_draft)
    write_json("salary_golden_diff_metrics.json", golden_metrics)

    rate_draft = next(
        item for item in active.draft.rules if item.rule_id == "RATE_SALARY_ENVELOPE_6M"
    )
    return {
        "trace_id": audit.trace_id,
        "extraction": {
            "review_state": active.state.value,
            "rule_ast": rate_draft.ast,
            "service_reference": active.draft.service_references[0],
            "required_facts": active.draft.required_facts,
            "provenance": rate_draft.provenance,
            "unresolved_items": active.draft.unresolved_items,
            "golden_diff_metrics": golden_metrics,
        },
        "before_user_answer": _rule_summary(before),
        "question": question,
        "submitted_fact": intent,
        "after_user_answer": _rule_summary(after),
        "goal": {
            "created": goal,
            "after_progress": update.goal,
            "alert": update.alert,
        },
    }


def step_flow(sink: InMemoryAuditSink) -> dict[str, Any]:
    audit = AuditSession(
        sink,
        request_id="REQ-STEP-DEMO-001",
        trace_id="TRACE-STEP-DEMO-001",
    )
    engine = FinancialEligibilityEngine()
    product = step_30000_300d_product()
    context = step_30000_context(available_days=365)
    initial_store = step_30000_user()

    case_a = engine.evaluate_product(product, initial_store, context, audit=audit)
    answered_store = submit_user_fact(
        initial_store,
        future_intent_fact(STEP_INTENT_FACT_TYPE, True),
        audit=audit,
    )
    case_b = engine.evaluate_product(product, answered_store, context, audit=audit)
    goal = GoalFactory(audit).create_from_evaluation(
        product=product,
        evaluation=case_b,
        context=context,
        subscription_id="SUB-STEP-DEMO-001",
        subscription_confirmed=True,
        user_tracking_intent=True,
    )[0]
    tracker = GoalTracker(audit)
    case_c = tracker.update_accumulative(
        goal,
        current_progress=170,
        as_of=context.subscription_date,
        remaining_opportunities=150,
    )
    case_d = tracker.update_accumulative(
        goal,
        current_progress=249,
        as_of=context.subscription_date,
        remaining_opportunities=50,
    )
    infeasible = engine.evaluate_product(
        product,
        initial_store,
        step_30000_context(available_days=299),
        audit=audit,
    )

    write_json("step_case_a_unknown.json", case_a)
    write_json("step_case_b_achievable.json", case_b)
    write_json("step_case_c_active_170_of_300.json", case_c)
    write_json("step_case_d_failed_249_of_300.json", case_d)
    write_json("step_time_infeasible_299_days.json", infeasible)

    return {
        "trace_id": audit.trace_id,
        "case_a": _rule_summary(case_a),
        "case_b": _rule_summary(case_b),
        "case_c": {
            "current_progress": case_c.goal.current_progress,
            "remaining_required": case_c.goal.remaining_required,
            "remaining_opportunities": case_c.goal.remaining_opportunities,
            "buffer": case_c.goal.buffer,
            "status": case_c.goal.status.value,
            "alert": case_c.alert,
        },
        "case_d": {
            "current_progress": case_d.goal.current_progress,
            "remaining_required": case_d.goal.remaining_required,
            "remaining_opportunities": case_d.goal.remaining_opportunities,
            "buffer": case_d.goal.buffer,
            "status": case_d.goal.status.value,
            "alert": case_d.alert,
        },
        "time_infeasible": _rule_summary(infeasible),
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sink = InMemoryAuditSink()
    salary = salary_flow(sink)
    steps = step_flow(sink)

    write_json(
        "summary.json",
        {
            "schema_version": "0.3",
            "salary_envelope_6m": salary,
            "step_30000_300d": steps,
        },
    )
    audit_jsonl = "\n".join(
        event.model_dump_json(exclude_none=True) for event in sink.events
    ) + "\n"
    write_text("audit_trace.jsonl", audit_jsonl)
    write_text("audit_sequence_all.txt", format_event_sequence(sink.events) + "\n")
    salary_events = sink.read(trace_id="TRACE-SALARY-DEMO-001")
    write_text(
        "audit_sequence_salary.txt", format_event_sequence(salary_events) + "\n"
    )
    step_events = sink.read(trace_id="TRACE-STEP-DEMO-001")
    write_text("audit_sequence_steps.txt", format_event_sequence(step_events) + "\n")

    print(json.dumps({"salary": salary, "steps": steps}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
