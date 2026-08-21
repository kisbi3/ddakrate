from __future__ import annotations

from eligibility.schema.product import ProductDefinition
from eligibility.schema.rule import (
    AndRule,
    CountConsecutiveRule,
    CountDistinctMonthsRule,
    CountDistinctPeriodsRule,
    ExistsRule,
    FactComparisonRule,
    NotExistsRule,
    NotRule,
    OrRule,
    RuleNode,
)
from eligibility.visualization.common import mermaid_escape, truncate


def product_rule_to_mermaid(
    product: ProductDefinition,
    *,
    max_depth: int | None = None,
    include_source: bool = False,
) -> str:
    """Render Product Rule AST only; no user facts or evaluation are used."""

    lines = ["flowchart TD", f'    P["{mermaid_escape(product.name)}"]']
    _render_rule(
        lines,
        product.eligibility_rule,
        node_id="ELIG",
        parent_id="P",
        depth=0,
        max_depth=max_depth,
        include_source=include_source,
    )
    for index, preferential in enumerate(product.preferential_rules):
        _render_rule(
            lines,
            preferential.rule,
            node_id=f"RATE{index}",
            parent_id="P",
            depth=0,
            max_depth=max_depth,
            include_source=include_source,
            edge_label=f"+{preferential.reward.value}%p",
        )
    for index, guard in enumerate(product.global_guards):
        _render_rule(
            lines,
            guard,
            node_id=f"GUARD{index}",
            parent_id="P",
            depth=0,
            max_depth=max_depth,
            include_source=include_source,
            edge_label="guard",
        )
    return "\n".join(lines) + "\n"


def _render_rule(
    lines: list[str],
    rule: RuleNode,
    *,
    node_id: str,
    parent_id: str,
    depth: int,
    max_depth: int | None,
    include_source: bool,
    edge_label: str | None = None,
) -> None:
    label = _rule_label(rule)
    if isinstance(rule, (AndRule, OrRule, NotRule)):
        lines.append(f'    {node_id}{{"{mermaid_escape(label)}"}}')
    else:
        lines.append(f'    {node_id}["{mermaid_escape(label)}"]')
    if edge_label:
        lines.append(f"    {parent_id} -->|{mermaid_escape(edge_label)}| {node_id}")
    else:
        lines.append(f"    {parent_id} --> {node_id}")

    if max_depth is not None and depth >= max_depth:
        return
    children: list[tuple[str, RuleNode]] = []
    if isinstance(rule, (AndRule, OrRule)):
        children = [(str(index), child) for index, child in enumerate(rule.children)]
    elif isinstance(rule, NotRule):
        children = [("0", rule.child)]
    elif isinstance(rule, (CountDistinctMonthsRule, CountDistinctPeriodsRule)):
        if (
            rule.future_achievement is not None
            and rule.future_achievement.capability_rule is not None
        ):
            children = [("CAP", rule.future_achievement.capability_rule)]

    for suffix, child in children:
        _render_rule(
            lines,
            child,
            node_id=f"{node_id}_C{suffix}",
            parent_id=node_id,
            depth=depth + 1,
            max_depth=max_depth,
            include_source=include_source,
        )

    if (
        isinstance(rule, (CountDistinctMonthsRule, CountDistinctPeriodsRule))
        and rule.future_achievement is not None
    ):
        future = rule.future_achievement
        future_id = f"{node_id}_FUTURE"
        goal_metric = (
            future.goal_template.metric if future.goal_template is not None else "-"
        )
        future_label = (
            f"Future Achievement\nmode={future.achievement_mode.value}\n"
            f"intent={future.intent_fact_type or '-'}\ngoal={goal_metric}"
        )
        lines.append(f'    {future_id}["{mermaid_escape(future_label)}"]')
        lines.append(f"    {node_id} -. future .-> {future_id}")

    if include_source and rule.source is not None:
        source_id = f"{node_id}_SRC"
        source_label = (
            f"{rule.source.document}\n"
            f"p.{rule.source.page or '-'} | {rule.source.section or '-'}"
        )
        lines.append(f'    {source_id}["{mermaid_escape(source_label)}"]')
        lines.append(f"    {node_id} -. source .-> {source_id}")


def _rule_label(rule: RuleNode) -> str:
    head = (
        f"{rule.name}\n{rule.rule_id} | {rule.type}\n"
        f"phase={rule.evaluation_phase.value}"
    )
    if isinstance(rule, FactComparisonRule):
        return (
            f"{head}\n{rule.fact_type} {rule.operator.value} "
            f"{truncate(rule.expected, 56)}"
        )
    if isinstance(rule, CountConsecutiveRule):
        return (
            f"{head}\nschedule={rule.schedule_id or '*'} | "
            f"from={rule.from_sequence} | {rule.operator.value} {rule.expected}"
        )
    if isinstance(rule, CountDistinctPeriodsRule):
        return (
            f"{head}\nperiod={rule.period.value} | {rule.fact_type} | "
            f"{rule.operator.value} {rule.expected}"
        )
    if isinstance(rule, CountDistinctMonthsRule):
        return f"{head}\nperiod=MONTH | {rule.fact_type} | {rule.operator.value} {rule.expected}"
    if isinstance(rule, (ExistsRule, NotExistsRule)):
        coverage = rule.coverage.fact_domain if rule.coverage else rule.coverage_fact_type
        return f"{head}\nentity={rule.entity.value} | coverage={coverage or '-'}"
    return head
