from __future__ import annotations

from collections.abc import Iterable

from eligibility.schema.user_fact import (
    AccountHoldingInterval,
    AccountLifecycleEvent,
    DataCoverage,
    FactProvenance,
    PersonRelationship,
    ScheduledOccurrence,
    UserFact,
    UserFactStore,
)
from eligibility.visualization.common import mermaid_escape, truncate


def user_facts_to_mermaid(
    store: UserFactStore,
    *,
    include_provenance: bool = True,
    max_items: int | None = None,
) -> str:
    """Render the User Fact Store without deriving any financial judgment."""

    user_label = mermaid_escape(f"사용자\n{store.user_id}")
    lines = ["flowchart LR", f'    U["{user_label}"]']
    person_nodes: dict[str, str] = {store.user_id: "U"}

    def person_node(person_id: str) -> str:
        if person_id in person_nodes:
            return person_nodes[person_id]
        node_id = f"P{len(person_nodes)}"
        person_nodes[person_id] = node_id
        person_label = mermaid_escape(f"관련인\n{person_id}")
        lines.append(f'    {node_id}["{person_label}"]')
        return node_id

    relationships = sorted(store.relationships, key=lambda item: item.relationship_id)
    for index, relationship in enumerate(_limited(relationships, max_items)):
        left = person_node(relationship.person_a)
        right = person_node(relationship.person_b)
        node_id = f"REL{index}"
        label = (
            f"{relationship.relationship_type}\n"
            f"source={relationship.source_type.value}\n"
            f"valid={_interval(relationship.valid_from, relationship.valid_to)}"
        )
        lines.append(f'    {node_id}["{mermaid_escape(label)}"]')
        lines.append(f"    {left} --> {node_id} --> {right}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"RELPV{index}",
                provenance=relationship.provenance,
                direct_reference=relationship.source_reference,
            )

    facts = sorted(store.facts, key=lambda item: item.fact_id)
    for index, fact in enumerate(_limited(facts, max_items)):
        node_id = f"F{index}"
        parent = person_node(fact.effective_subject_person_id)
        label = _fact_label(fact)
        lines.append(f'    {node_id}["{mermaid_escape(label)}"]')
        lines.append(f"    {parent} --> {node_id}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"FPV{index}",
                provenance=fact.provenance,
            )

    holdings = sorted(store.account_holdings, key=lambda item: item.account_id)
    for index, holding in enumerate(_limited(holdings, max_items)):
        node_id = f"A{index}"
        parent = person_node(holding.effective_subject_person_id)
        lines.append(f'    {node_id}["{mermaid_escape(_holding_label(holding))}"]')
        lines.append(f"    {parent} --> {node_id}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"APV{index}",
                provenance=holding.provenance,
            )

    occurrences = sorted(
        store.scheduled_occurrences,
        key=lambda item: (item.schedule_id, item.sequence_no, item.occurrence_id),
    )
    for index, occurrence in enumerate(_limited(occurrences, max_items)):
        node_id = f"O{index}"
        parent = person_node(occurrence.effective_subject_person_id)
        lines.append(
            f'    {node_id}["{mermaid_escape(_occurrence_label(occurrence))}"]'
        )
        lines.append(f"    {parent} --> {node_id}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"OPV{index}",
                provenance=occurrence.provenance,
            )

    coverages = sorted(store.data_coverages, key=lambda item: item.coverage_id)
    for index, coverage in enumerate(_limited(coverages, max_items)):
        node_id = f"COV{index}"
        lines.append(f'    {node_id}["{mermaid_escape(_coverage_label(coverage))}"]')
        lines.append(f"    U --> {node_id}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"COVPV{index}",
                provenance=coverage.provenance,
            )

    lifecycle = sorted(
        store.account_lifecycle_events,
        key=lambda item: (item.account_id, item.occurred_at, item.event_id),
    )
    for index, event in enumerate(_limited(lifecycle, max_items)):
        node_id = f"L{index}"
        lines.append(f'    {node_id}["{mermaid_escape(_lifecycle_label(event))}"]')
        lines.append(f"    U --> {node_id}")
        if include_provenance:
            _render_provenance(
                lines,
                parent_id=node_id,
                prefix=f"LPV{index}",
                provenance=event.provenance,
            )

    return "\n".join(lines) + "\n"


def _fact_label(fact: UserFact) -> str:
    return (
        f"{fact.fact_type}\n"
        f"value={truncate(fact.value, 84)}\n"
        f"source={fact.source_type.value}\n"
        f"semantic={fact.semantic_type.value}\n"
        f"valid={_interval(fact.valid_from, fact.valid_to)}"
    )


def _holding_label(holding: AccountHoldingInterval) -> str:
    return (
        f"계좌보유 | {holding.product_name or holding.product_type}\n"
        f"institution={holding.institution}\n"
        f"held={_interval(holding.held_from, holding.held_to)}\n"
        f"source={holding.source_type.value}"
    )


def _occurrence_label(occurrence: ScheduledOccurrence) -> str:
    return (
        f"{occurrence.schedule_id} #{occurrence.sequence_no}\n"
        f"{occurrence.method.value} / {occurrence.status.value}\n"
        f"scheduled={occurrence.scheduled_at.isoformat()}\n"
        f"source={occurrence.source_type.value}"
    )


def _coverage_label(coverage: DataCoverage) -> str:
    return (
        f"Coverage | {coverage.fact_domain}\n"
        f"institution={coverage.institution or 'ALL'}\n"
        f"covered={coverage.covered_from}~{coverage.covered_to}\n"
        f"source={coverage.source_type.value}"
    )


def _lifecycle_label(event: AccountLifecycleEvent) -> str:
    return (
        f"AccountLifecycle | {event.event_type.value}\n"
        f"account={event.account_id}\n"
        f"at={event.occurred_at.isoformat()}\n"
        f"source={event.source_type.value}"
    )


def _render_provenance(
    lines: list[str],
    *,
    parent_id: str,
    prefix: str,
    provenance: Iterable[FactProvenance],
    direct_reference: str | None = None,
) -> None:
    references: list[tuple[str, str | None]] = []
    if direct_reference:
        references.append((direct_reference, None))
    references.extend((item.reference, item.description) for item in provenance)
    for index, (reference, description) in enumerate(references[:4]):
        node_id = f"{prefix}_{index}"
        label = f"provenance\n{truncate(reference, 92)}"
        if description:
            label += f"\n{truncate(description, 72)}"
        lines.append(f'    {node_id}["{mermaid_escape(label)}"]')
        lines.append(f"    {parent_id} -. source .-> {node_id}")


def _interval(start, end) -> str:
    return f"{start or '-'}~{end or 'OPEN'}"


def _limited(items: list, max_items: int | None):
    return items if max_items is None else items[:max_items]
