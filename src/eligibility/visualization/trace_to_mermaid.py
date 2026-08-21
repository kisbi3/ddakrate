from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from eligibility.schema.evaluation import ProductEvaluation, ProvenanceRecord, RuleEvaluation
from eligibility.visualization.common import mermaid_escape, truncate


@dataclass(frozen=True)
class TraceMermaidOptions:
    max_depth: int | None = None
    include_evidence: bool = True
    include_provenance: bool = False


def trace_to_mermaid(
    evaluation: ProductEvaluation,
    *,
    max_depth: int | None = None,
    include_evidence: bool = True,
    include_provenance: bool = False,
) -> str:
    """Render an Evaluation Trace as deterministic Mermaid flowchart source.

    The function consumes statuses already calculated by the engine. It does
    not evaluate rules, facts, rates, or financial conditions.
    """

    options = TraceMermaidOptions(
        max_depth=max_depth,
        include_evidence=include_evidence,
        include_provenance=include_provenance,
    )
    lines = ["flowchart TD"]
    product_label = (
        f"{evaluation.product_name}\n"
        f"advertised={evaluation.rates.advertised_max_rate}% | "
        f"realizable={evaluation.rates.realizable_rate}%"
    )
    lines.append(f'    P["{mermaid_escape(product_label)}"]')

    groups: list[tuple[str, str, Iterable[RuleEvaluation]]] = [
        ("G_ELIG", "가입자격", [evaluation.eligibility]),
        ("G_RATE", "우대금리", evaluation.preferential_rule_results),
        ("G_GUARD", "Global Guard", evaluation.global_guard_results),
    ]
    for group_id, label, results in groups:
        results_list = list(results)
        if not results_list:
            continue
        lines.append(f'    {group_id}["{mermaid_escape(label)}"]')
        lines.append(f"    P --> {group_id}")
        for index, result in enumerate(results_list):
            _render_rule(
                lines,
                result,
                node_id=f"{group_id}_R{index}",
                parent_id=group_id,
                depth=0,
                options=options,
            )

    return "\n".join(lines) + "\n"


def _render_rule(
    lines: list[str],
    result: RuleEvaluation,
    *,
    node_id: str,
    parent_id: str,
    depth: int,
    options: TraceMermaidOptions,
) -> None:
    rule_type = result.rule_type or "RULE"
    label = f"{result.rule_name}\n{result.rule_id} | {rule_type}"
    if rule_type in {"AND", "OR", "NOT"}:
        lines.append(f'    {node_id}{{"{mermaid_escape(label)}"}}')
    else:
        lines.append(f'    {node_id}["{mermaid_escape(label)}"]')
    lines.append(f"    {parent_id} --> {node_id}")

    if options.max_depth is not None and depth >= options.max_depth and result.children:
        truncated_id = f"{node_id}_TRUNCATED"
        lines.append(f'    {truncated_id}["{mermaid_escape("… deeper trace omitted")}"]')
        lines.append(f"    {node_id} --> {truncated_id}")
    else:
        for child_index, child in enumerate(result.children):
            _render_rule(
                lines,
                child,
                node_id=f"{node_id}_C{child_index}",
                parent_id=node_id,
                depth=depth + 1,
                options=options,
            )

    terminal_parent = node_id
    if options.include_evidence and result.evidence:
        evidence_id = f"{node_id}_E"
        lines.append(
            f'    {evidence_id}["{mermaid_escape(_evidence_label(result))}"]'
        )
        lines.append(f"    {node_id} --> {evidence_id}")
        terminal_parent = evidence_id

    status_id = f"{node_id}_S"
    status_label = f"{result.status.value}\n{result.reason_code}"
    lines.append(f'    {status_id}["{mermaid_escape(status_label)}"]')
    lines.append(f"    {terminal_parent} --> {status_id}")

    if options.include_provenance:
        for index, provenance in enumerate(result.source_provenance[:4]):
            provenance_id = f"{node_id}_PV{index}"
            lines.append(
                f'    {provenance_id}["{mermaid_escape(_provenance_label(provenance))}"]'
            )
            lines.append(f"    {node_id} -. provenance .-> {provenance_id}")


def _evidence_label(result: RuleEvaluation) -> str:
    preferred_order = [
        "actual",
        "expected",
        "operator",
        "current",
        "required",
        "consecutive_count",
        "qualifying_occurrence_count",
        "first_break_sequence",
        "holding_from",
        "holding_to",
        "lookback_from",
        "lookback_to",
        "overlap",
        "matched_count",
        "fact_type",
        "source_type",
    ]
    ordered_keys = [key for key in preferred_order if key in result.evidence]
    ordered_keys.extend(
        key
        for key in sorted(result.evidence)
        if key not in ordered_keys and key not in {"child_statuses", "action"}
    )
    items = [
        f"{key}={truncate(result.evidence[key], 72)}" for key in ordered_keys[:7]
    ]
    return "Evidence\n" + "\n".join(items)


def _provenance_label(provenance: ProvenanceRecord) -> str:
    reference = provenance.reference or "(no reference)"
    return f"{provenance.source_type}\n{truncate(reference, 88)}"
