from __future__ import annotations

import re
from decimal import Decimal

from eligibility.schema.enums import EvaluationStatus, VerificationLevel
from eligibility.schema.evaluation import (
    AppliedReward,
    RateEvidenceBreakdown,
    RateSummary,
    RuleEvaluation,
)
from eligibility.schema.product import ProductDefinition


_AUTHORITATIVE_LEVELS = {
    VerificationLevel.INSTITUTION_VERIFIED,
    VerificationLevel.MYDATA_VERIFIED,
    VerificationLevel.DERIVED,
    # v0.3.1 compatibility; new evaluations should not emit this value.
    VerificationLevel.VERIFIED,
}


class RateEngine:
    """Calculate rate layers while preserving evidence semantics."""

    @staticmethod
    def _has_unmodeled_preferential_rules(product: ProductDefinition) -> bool:
        """Whether published source-first rate rules lack executable adapters.

        Non-deterministic rewards such as an official prize draw are valid
        possible outcomes even though the customer cannot assert the draw
        result and the legacy rule evaluator cannot execute the cumulative
        reward. They must stay in the possible maximum until a product feature
        policy excludes the product.
        """

        if product.normalized is None:
            return False
        policy = product.normalized.return_policy.get("preferential_policy") or {}
        published_rule_ids = {
            str(rule.get("rule_id"))
            for rule in policy.get("rules") or []
            if rule.get("rule_id")
        }
        executable_rule_ids = {
            str(item.canonical_rule_id or item.rule.rule_id)
            for item in product.preferential_rules
        }
        return bool(published_rule_ids - executable_rule_ids)

    @staticmethod
    def _relation_type(relation: dict) -> str:
        """Return the canonical relation operator across published schema aliases."""

        return str(relation.get("type") or relation.get("operator") or "").upper()

    @staticmethod
    def _relation_rule_ids(relation: dict) -> list[str]:
        """Return member rule ids across legacy and source-first field names."""

        values = (
            relation.get("rule_ids")
            or relation.get("member_rule_ids")
            or relation.get("members")
            or []
        )
        return [str(value) for value in values if value]

    @staticmethod
    def calculate(
        product: ProductDefinition,
        preferential_results: list[RuleEvaluation],
        guard_results: list[RuleEvaluation],
    ) -> RateSummary:
        result_by_rule_id = {result.rule_id: result for result in preferential_results}

        guard_status = RateEngine._combine_guard_status(guard_results)
        rewards_blocked = guard_status == EvaluationStatus.UNSATISFIABLE
        guard_unknown = guard_status == EvaluationStatus.UNKNOWN

        verified_rule_ids: set[str] = set()
        realizable_rule_ids: set[str] = set()
        upper_rule_ids: set[str] = set()
        effective_rewards: dict[str, Decimal] = {}
        applied: list[AppliedReward] = []

        for preferential_rule in product.preferential_rules:
            result = result_by_rule_id[preferential_rule.rule.rule_id]
            reward = RateEngine._effective_reward(preferential_rule, result)
            effective_rewards[preferential_rule.rule.rule_id] = reward
            bucket = RateEngine._evidence_bucket(result)

            include_confirmed = (
                not rewards_blocked
                and not guard_unknown
                and result.status == EvaluationStatus.SATISFIED
                and bucket == "VERIFIED"
            )
            include_realizable = (
                not rewards_blocked
                and not guard_unknown
                and (
                    (result.status == EvaluationStatus.SATISFIED and bucket in {"VERIFIED", "SELF_REPORTED"})
                    or result.status == EvaluationStatus.ACHIEVABLE
                )
            )
            include_upper = (
                not rewards_blocked
                and result.status
                in {
                    EvaluationStatus.SATISFIED,
                    EvaluationStatus.ACHIEVABLE,
                    EvaluationStatus.UNKNOWN,
                }
            )

            if include_confirmed:
                verified_rule_ids.add(preferential_rule.rule.rule_id)
            if include_realizable:
                realizable_rule_ids.add(preferential_rule.rule.rule_id)
            if include_upper:
                upper_rule_ids.add(preferential_rule.rule.rule_id)

            applied.append(
                AppliedReward(
                    rule_id=result.rule_id,
                    rule_name=result.rule_name,
                    status=result.status,
                    reward_pp=reward,
                    verification_level=result.verification_level,
                    evidence_levels=RateEngine._levels(result),
                    evidence_bucket=bucket,
                    included_in_confirmed=include_confirmed,
                    included_in_realizable=include_realizable,
                    included_in_user_specific_conditional_upper=include_upper,
                )
            )

        declared_cap = product.preferential_rate_cap
        confirmed_total = RateEngine._aggregate_canonical_rewards(
            product, verified_rule_ids, effective_rewards, declared_cap, layer="confirmed"
        )
        realizable_total = RateEngine._aggregate_canonical_rewards(
            product, realizable_rule_ids, effective_rewards, declared_cap, layer="confirmed"
        )
        upper_total = RateEngine._aggregate_canonical_rewards(
            product, upper_rule_ids, effective_rewards, declared_cap, layer="upper"
        )
        if (
            not rewards_blocked
            and product.base_rate is not None
            and product.advertised_max_rate is not None
            and RateEngine._has_unmodeled_preferential_rules(product)
        ):
            # Keep source-first rules that cannot be executed deterministically
            # (for example a bank-authoritative random draw) in the candidate
            # upper bound. This value affects provisional rank/frontier only;
            # it is never promoted to the user's realizable expected rate.
            published_possible_reward = max(
                Decimal("0"),
                product.advertised_max_rate - product.base_rate,
            )
            if declared_cap is not None:
                published_possible_reward = min(
                    published_possible_reward,
                    declared_cap,
                )
            upper_total = max(upper_total, published_possible_reward)
        realizable_increment = max(Decimal("0"), realizable_total - confirmed_total)
        upper_increment = max(Decimal("0"), upper_total - realizable_total)

        raw_self = sum(
            effective_rewards[item.rule_id]
            for item in applied
            if item.included_in_realizable
            and not item.included_in_confirmed
            and item.evidence_bucket == "SELF_REPORTED"
        )
        capped_self = min(raw_self, realizable_increment)
        capped_future = realizable_increment - capped_self
        capped_verified = confirmed_total
        capped_unknown = upper_increment

        return_kind = (
            product.normalized.return_policy.get("return_kind")
            if product.normalized is not None
            else None
        )
        if return_kind == "PERFORMANCE_LINKED":
            calculation_status = "UNSUPPORTED"
            calculation_reason = "PERFORMANCE_LINKED_RETURN_IS_NOT_A_GUARANTEED_RATE"
        elif product.base_rate is None:
            calculation_status = "UNKNOWN"
            calculation_reason = "APPLICABLE_BASE_RATE_NOT_AVAILABLE"
        else:
            calculation_status = "CALCULATED"
            calculation_reason = None

        confirmed_rate = (
            product.base_rate + confirmed_total
            if product.base_rate is not None and calculation_status == "CALCULATED"
            else None
        )
        realizable_rate = (
            product.base_rate + realizable_total
            if product.base_rate is not None and calculation_status == "CALCULATED"
            else None
        )
        upper_rate = (
            product.base_rate + upper_total
            if product.base_rate is not None and calculation_status == "CALCULATED"
            else None
        )
        # The catalog's published maximum is an externally displayed ceiling.
        # Rule extraction can contain mutually-exclusive alternatives or a
        # source sentence that states a total rate (rather than an increment),
        # so an optimistic rule sum must never advertise more than that ceiling.
        if (
            upper_rate is not None
            and product.advertised_max_rate is not None
            and upper_rate > product.advertised_max_rate
        ):
            upper_rate = product.advertised_max_rate

        return RateSummary(
            advertised_max_rate=product.advertised_max_rate,
            confirmed_rate=confirmed_rate,
            realizable_rate=realizable_rate,
            user_specific_conditional_upper_rate=upper_rate,
            preferential_cap=declared_cap,
            calculation_status=calculation_status,
            calculation_reason=calculation_reason,
            guard_status=guard_status,
            applied_rewards=applied,
            evidence_breakdown=RateEvidenceBreakdown(
                base_rate=product.base_rate,
                verified_reward_pp=capped_verified,
                self_reported_reward_pp=capped_self,
                future_action_reward_pp=capped_future,
                unknown_conditional_reward_pp=capped_unknown,
            ),
        )

    @staticmethod
    def _levels(result: RuleEvaluation) -> list[VerificationLevel]:
        if result.evidence_levels:
            return list(dict.fromkeys(result.evidence_levels))
        if result.verification_level != VerificationLevel.UNKNOWN:
            return [result.verification_level]
        return []

    @staticmethod
    def _evidence_bucket(result: RuleEvaluation) -> str:
        if result.status == EvaluationStatus.UNSATISFIABLE:
            return "UNSATISFIABLE"
        levels = set(RateEngine._levels(result))
        if result.status == EvaluationStatus.ACHIEVABLE:
            return "FUTURE_ACTION"
        if result.status == EvaluationStatus.UNKNOWN:
            return "UNKNOWN"
        if VerificationLevel.SELF_REPORTED in levels or result.is_provisional:
            return "SELF_REPORTED"
        if result.verification_level == VerificationLevel.MIXED and not result.is_provisional:
            # Mixed institution/MyData/derived evidence is still authoritative.
            return "VERIFIED"
        if levels and levels <= _AUTHORITATIVE_LEVELS:
            return "VERIFIED"
        return "UNKNOWN"

    @staticmethod
    def _combine_guard_status(guard_results: list[RuleEvaluation]) -> EvaluationStatus:
        if not guard_results:
            return EvaluationStatus.SATISFIED
        statuses = [result.status for result in guard_results]
        if EvaluationStatus.UNSATISFIABLE in statuses:
            return EvaluationStatus.UNSATISFIABLE
        if EvaluationStatus.UNKNOWN in statuses:
            return EvaluationStatus.UNKNOWN
        if EvaluationStatus.ACHIEVABLE in statuses:
            return EvaluationStatus.ACHIEVABLE
        return EvaluationStatus.SATISFIED

    @staticmethod
    def _fact_actual(result: RuleEvaluation, fact_type: str) -> Decimal | None:
        evidence = result.evidence or {}
        if evidence.get("fact_type") == fact_type and evidence.get("actual") is not None:
            try:
                return Decimal(str(evidence["actual"]))
            except Exception:
                return None
        for child in result.children:
            actual = RateEngine._fact_actual(child, fact_type)
            if actual is not None:
                return actual
        return None

    @staticmethod
    def _effective_reward(preferential_rule, result: RuleEvaluation) -> Decimal:
        reward = preferential_rule.reward.value
        payload = preferential_rule.reward_payload
        kind = preferential_rule.reward_kind
        if kind == "ADD_RATE_FROM_FACT":
            fact_type = str(payload.get("fact_key") or "").replace(".", "_")
            actual = RateEngine._fact_actual(result, fact_type)
            if actual is not None:
                minimum = Decimal(str(payload.get("min_value", actual)))
                maximum = Decimal(str(payload.get("max_value", actual)))
                reward = min(maximum, max(minimum, actual))
        elif kind == "REPEATABLE_ADD_RATE":
            fact_type = str(payload.get("count_fact_key") or "").replace(".", "_")
            count = RateEngine._fact_actual(result, fact_type)
            if count is not None:
                maximum_count = Decimal(str(payload.get("max_count", count)))
                per_count = Decimal(
                    str(payload.get("value_per_count") or payload.get("value") or "0")
                )
                reward = min(count, maximum_count) * per_count
                maximum_reward = payload.get("maximum_reward")
                if maximum_reward is not None:
                    reward = min(reward, Decimal(str(maximum_reward)))
        return max(Decimal("0"), reward)

    @staticmethod
    def _aggregate_canonical_rewards(
        product: ProductDefinition,
        included_runtime_rule_ids: set[str],
        reward_by_runtime_rule_id: dict[str, Decimal],
        declared_cap: Decimal | None,
        *,
        layer: str = "confirmed",
    ) -> Decimal:
        if not included_runtime_rule_ids:
            return Decimal("0")
        canonical_by_runtime = {
            item.rule.rule_id: item.canonical_rule_id or item.rule.rule_id
            for item in product.preferential_rules
        }
        values = {
            canonical_by_runtime[runtime_id]: reward_by_runtime_rule_id[runtime_id]
            for runtime_id in included_runtime_rule_ids
        }
        policy = (
            product.normalized.return_policy.get("preferential_policy", {})
            if product.normalized is not None
            else {}
        )
        relations = list(policy.get("relations") or [])

        parent = {rule_id: rule_id for rule_id in values}

        def find(rule_id: str) -> str:
            parent.setdefault(rule_id, rule_id)
            while parent[rule_id] != rule_id:
                parent[rule_id] = parent[parent[rule_id]]
                rule_id = parent[rule_id]
            return rule_id

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        exclusive_types = {
            "MAX_OF",
            "EXCLUSIVE_ONE",
            "MUTUALLY_EXCLUSIVE",
            "MUTUALLY_EXCLUSIVE_MAX",
            "MUTUALLY_EXCLUSIVE_BY_ACCOUNT_AGE",
            "MUTUALLY_EXCLUSIVE_HIGHEST_THRESHOLD",
            "MUTUALLY_EXCLUSIVE_HIGHEST_SPECIFICITY",
            "TIER_SELECT",
            "CONDITION_PRIORITY_OVERRIDE",
            "FINAL_RATE_NOT_ADDITIVE",
        }
        for relation in relations:
            if RateEngine._relation_type(relation) not in exclusive_types:
                continue
            members = [
                rule
                for rule in RateEngine._relation_rule_ids(relation)
                if rule in values
            ]
            for member in members[1:]:
                union(members[0], member)

        # Catalog relations are often empty for staged 1%/2%/3% ladders that
        # still share one official sentence. Take the max only inside a
        # same-condition ladder. Independent bonuses copied onto that sentence
        # stay additive; multiple headcount or period numbers in the sentence
        # are not themselves an exclusive relation. Unclear copies are not
        # forced into either a max or a newly invented exclusive relation.
        for members in RateEngine._same_source_tier_groups(policy, values):
            for member in members[1:]:
                union(members[0], member)
        if layer != "upper":
            # Unclear copies must not inflate the locked-in rate. The possible
            # upper may still sum them; advertised ceilings remain a cap.
            for members in RateEngine._same_source_unclear_groups(policy, values):
                for member in members[1:]:
                    union(members[0], member)

        groups: dict[str, list[str]] = {}
        for rule_id in values:
            groups.setdefault(find(rule_id), []).append(rule_id)
        group_values = {
            root: max(values[rule_id] for rule_id in members)
            for root, members in groups.items()
        }
        total = sum(group_values.values(), Decimal("0"))

        # Component caps are applied after mutually-exclusive choices within
        # the component.  This prevents double counting while retaining truly
        # independent rewards outside the capped relation.
        for relation in relations:
            if RateEngine._relation_type(relation) not in {
                "SUM_WITH_CAP",
                "CUMULATIVE_WITH_GLOBAL_CAP",
            }:
                continue
            member_ids = set(RateEngine._relation_rule_ids(relation))
            if not member_ids:
                continue
            roots = {find(rule_id) for rule_id in member_ids if rule_id in values}
            component = sum((group_values[root] for root in roots), Decimal("0"))
            cap_row = relation.get("cap") or {}
            cap_value = (
                cap_row.get("value")
                or relation.get("cap_value")
                or (policy.get("global_cap") or {}).get("value")
            )
            if cap_value is not None:
                total -= max(Decimal("0"), component - Decimal(str(cap_value)))

        if declared_cap is not None:
            total = min(total, declared_cap)
        replacement_scopes: dict[str, Decimal] = {}
        for item in product.preferential_rules:
            canonical_id = item.canonical_rule_id or item.rule.rule_id
            if canonical_id not in values or item.reward_kind != "ADD_RATE":
                continue
            interaction = str(item.application.get("base_interaction") or "")
            if not interaction.startswith("REPLACE_BASE"):
                continue
            scope_key = str(item.application.get("principal_scope") or "GLOBAL")
            factor = Decimal(
                str(item.application.get("_scenario_principal_factor", "1"))
            )
            replacement_scopes[scope_key] = max(
                replacement_scopes.get(scope_key, Decimal("0")), factor
            )
        if product.base_rate is not None:
            total -= product.base_rate * sum(replacement_scopes.values(), Decimal("0"))
        return max(Decimal("0"), total)

    _PERSON_RATE_RE = re.compile(
        r"(?:\[\s*)?(\d+)\s*명(?:\s*\])?[^%]{0,48}?연\s*(\d+(?:\.\d+)?)\s*%"
    )
    _PERIOD_RATE_RE = re.compile(
        r"(?:\[\s*)?(\d+(?:\.\d+)?)\s*(개월|년)(?:\s*\])?[^%]{0,48}?"
        r"연\s*(\d+(?:\.\d+)?)\s*%"
    )

    @staticmethod
    def _walk_policy_nodes(value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from RateEngine._walk_policy_nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from RateEngine._walk_policy_nodes(child)

    @staticmethod
    def _condition_ladder_key(row: dict) -> tuple[str | None, object | None]:
        fact_key = None
        expected = None
        for node in RateEngine._walk_policy_nodes(row.get("condition") or {}):
            if node.get("fact_key") and fact_key is None:
                fact_key = str(node.get("fact_key"))
            if "expected" in node and expected is None:
                expected = node.get("expected")
        return fact_key, expected

    @staticmethod
    def _rule_reward_value(row: dict) -> Decimal | None:
        reward = row.get("reward") or {}
        try:
            value = Decimal(str(reward.get("value")))
        except Exception:
            return None
        return value if value.is_finite() else None

    @staticmethod
    def _person_rate_pairs(text: str) -> list[tuple[str, Decimal]]:
        return [
            (match.group(1), Decimal(match.group(2)))
            for match in RateEngine._PERSON_RATE_RE.finditer(text)
        ]

    @staticmethod
    def _period_rate_pairs(text: str) -> list[tuple[str, Decimal]]:
        return [
            (f"{match.group(1)}{match.group(2)}", Decimal(match.group(3)))
            for match in RateEngine._PERIOD_RATE_RE.finditer(text)
        ]

    _PERSON_SUBJECT_KEYWORDS = ("명", "자녀", "아이", "다둥이", "가족", "출산", "인원")
    _PERIOD_SUBJECT_KEYWORDS = ("개월", "년", "기간", "만기", "유지")

    @staticmethod
    def _row_own_text(row: dict) -> str:
        parts = [
            str(row.get("title") or ""),
            str(row.get("display", {}).get("summary") or "") if isinstance(row.get("display"), dict) else "",
            str(row.get("name") or ""),
            str(row.get("display_name") or ""),
            str(row.get("condition_text") or ""),
        ]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _row_text(row: dict) -> str:
        return " ".join(
            part for part in (
                RateEngine._row_own_text(row),
                str(row.get("source_clause_text") or ""),
            ) if part
        ).strip()

    @staticmethod
    def _is_staged_same_condition_group(text: str, rows: list[dict]) -> bool:
        """True only when these rows are a same-condition reward ladder.

        Multiple person or period numbers in the shared sentence are not
        evidence that every copied rule is exclusive. A ladder requires the
        same condition key with different thresholds, or rewards that bind to
        two or more distinct bands of that same condition. A family keyword
        such as 급여 does not by itself prove independence or a ladder.
        """

        if len(rows) < 2:
            return False
        keys = [RateEngine._condition_ladder_key(row) for row in rows]
        fact_keys = {fact for fact, _expected in keys if fact}
        expecteds = {expected for _fact, expected in keys if expected is not None}
        if len(fact_keys) > 1:
            return False
        if len(fact_keys) == 1 and len(expecteds) >= 2:
            return True
        values = {RateEngine._rule_reward_value(row) for row in rows}
        values.discard(None)
        person_bands = {
            count
            for count, rate in RateEngine._person_rate_pairs(text)
            if rate in values
        }
        period_bands = {
            band
            for band, rate in RateEngine._period_rate_pairs(text)
            if rate in values
        }
        if len(person_bands) >= 2 and len(person_bands) == len(rows):
            return True
        return len(period_bands) >= 2 and len(period_bands) == len(rows)

    @staticmethod
    def _rows_matching_rate_bands(
        text: str,
        rows: list[dict],
        pairs: list[tuple[str, Decimal]],
    ) -> list[dict]:
        is_period = any("개월" in b or "년" in b for b, _ in pairs)
        subject_keywords = RateEngine._PERIOD_SUBJECT_KEYWORDS if is_period else RateEngine._PERSON_SUBJECT_KEYWORDS

        matched: list[dict] = []
        used_rule_ids: set[str] = set()
        matched_bands: set[str] = set()

        for band, rate in pairs:
            candidates: list[tuple[int, dict]] = []
            for row in rows:
                rule_id = str(row.get("rule_id") or "")
                if rule_id in used_rule_ids:
                    continue
                value = RateEngine._rule_reward_value(row)
                if value is None or value != rate:
                    continue
                own_text = RateEngine._row_own_text(row)
                shared_text = str(row.get("source_clause_text") or text)
                score = 0
                if band in own_text:
                    score += 10
                elif band in shared_text:
                    score += 2
                if any(sk in own_text for sk in subject_keywords):
                    score += 5
                elif any(sk in shared_text for sk in subject_keywords):
                    score += 1
                if score <= 0:
                    continue
                candidates.append((score, row))

            if not candidates:
                continue
            candidates.sort(key=lambda x: x[0], reverse=True)
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                # Two rows equally fit the same band: not a confirmed mapping.
                continue
            best_row = candidates[0][1]
            matched.append(best_row)
            used_rule_ids.add(str(best_row.get("rule_id") or ""))
            matched_bands.add(band)

        if len(matched) >= 2 and len(matched) == len(matched_bands) and len(matched_bands) >= 2:
            return matched
        return []

    @staticmethod
    def _partition_same_source_rows(text: str, rows: list[dict]) -> tuple[list[list[dict]], list[dict]]:
        """Split one copied sentence into condition groups and unclear leftovers."""

        by_fact: dict[str, list[dict]] = {}
        unkeyed: list[dict] = []
        for row in rows:
            fact, _expected = RateEngine._condition_ladder_key(row)
            if fact:
                by_fact.setdefault(fact, []).append(row)
            else:
                unkeyed.append(row)
        partitions = list(by_fact.values())
        used: set[str] = set()
        for pairs in (
            RateEngine._person_rate_pairs(text),
            RateEngine._period_rate_pairs(text),
        ):
            remaining = [row for row in unkeyed if str(row.get("rule_id") or "") not in used]
            matched = RateEngine._rows_matching_rate_bands(text, remaining, pairs)
            if not matched:
                continue
            partitions.append(matched)
            used.update(str(row.get("rule_id") or "") for row in matched)
        leftover = [row for row in unkeyed if str(row.get("rule_id") or "") not in used]
        return partitions, leftover

    @staticmethod
    def _grouped_same_source_rows(policy: dict, values: dict[str, Decimal]) -> list[tuple[str, list[dict]]]:
        grouped: dict[str, list[dict]] = {}
        for row in policy.get("rules") or []:
            if not isinstance(row, dict):
                continue
            rule_id = str(row.get("rule_id") or "")
            if rule_id not in values:
                continue
            text = row.get("source_clause_text") or row.get("condition_text") or ""
            if not isinstance(text, str):
                continue
            text = " ".join(text.split())
            if not text:
                continue
            members = grouped.setdefault(text, [])
            if all(str(item.get("rule_id") or "") != rule_id for item in members):
                members.append(row)
        return [(text, rows) for text, rows in grouped.items() if len(rows) >= 2]

    @staticmethod
    def _same_source_tier_groups(
        policy: dict,
        values: dict[str, Decimal],
    ) -> list[list[str]]:
        """Group staged same-condition rewards that copy one official sentence.

        A child-count or period ladder published as separate ADD_RATE rows
        with empty relations must not sum. Matching source text is necessary
        but not sufficient: only the same condition, subject, and reference
        time form a max group. Independent bonuses in that sentence stay
        additive. An unclear copy is not turned into a max group.
        """

        staged: list[list[str]] = []
        for text, rows in RateEngine._grouped_same_source_rows(policy, values):
            partitions, _leftover = RateEngine._partition_same_source_rows(text, rows)
            for members in partitions:
                if len(members) < 2:
                    continue
                if not RateEngine._is_staged_same_condition_group(text, members):
                    continue
                staged.append([str(row.get("rule_id")) for row in members])
        return staged

    @staticmethod
    def _same_source_unclear_groups(
        policy: dict,
        values: dict[str, Decimal],
    ) -> list[list[str]]:
        """Same-source copies whose exclusive/additive relation is unproven."""

        unclear: list[list[str]] = []
        for text, rows in RateEngine._grouped_same_source_rows(policy, values):
            partitions, leftover = RateEngine._partition_same_source_rows(text, rows)
            leftover_ids = [str(row.get("rule_id")) for row in leftover]
            if len(leftover_ids) >= 2:
                unclear.append(leftover_ids)
            for members in partitions:
                if len(members) < 2:
                    continue
                if RateEngine._is_staged_same_condition_group(text, members):
                    continue
                if len({RateEngine._condition_ladder_key(row)[0] for row in members}) == 1:
                    # Same fact key but not enough distinct thresholds: still
                    # not proven additive.
                    unclear.append([str(row.get("rule_id")) for row in members])
        return unclear

