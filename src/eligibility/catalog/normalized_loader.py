from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from eligibility.schema.enums import (
    ComparisonOperator,
    ContributionFrequency,
    ContributionMode,
    EvaluationStatus,
    FactSemanticType,
    FactSourceType,
    ResolutionStrategy,
    RulePurpose,
    SaleStatus,
    SubscriptionChannel,
    TermUnit,
)
from eligibility.schema.product import (
    ContractTerm,
    ContributionPolicy,
    NormalizedProductData,
    PreferentialRateRule,
    ProductDefinition,
    ProductFeature,
    ProductMetadata,
    Reward,
)
from eligibility.question_policy import is_future_action_fact
from eligibility.schema.rule import (
    AndRule,
    FactAcceptancePolicy,
    FactComparisonRule,
    MissingFactSpec,
    OrRule,
    RateImpact,
    SourceReference,
)


class NormalizedCatalogError(ValueError):
    """Published catalog integrity or reference failure."""


_SHARED_FACT_TYPE_ALIASES = {
    # Birth date is collected once during pre-search and materialized as
    # AGE_YEARS.  Product-family source schemas use several names for that same
    # subscription-date age; normalize them at the catalog boundary instead of
    # asking the user for an already known fact.
    "CUSTOMER_AGE_AT_SUBSCRIPTION": "AGE_YEARS",
    "CUSTOMER_AGE_YEARS": "AGE_YEARS",
    "CUSTOMER_AGE_AT_SUBSCRIPTION_YEARS": "AGE_YEARS",
}


def _canonical_fact_type(fact_key: str) -> str:
    normalized = fact_key.replace(".", "_")
    return _SHARED_FACT_TYPE_ALIASES.get(normalized, normalized)


def _canonical_predicate_fact_type(fact_key: str, title: str) -> str:
    """Repair narrowly identifiable source-schema key mistakes at load time.

    One published savings rule stores an agricultural-school/young-farmer
    certificate requirement under ``CUSTOMER.AGE``.  Treating that as age lets
    a birth-date answer resolve an unrelated documentary condition and also
    groups the question with genuine age rules.  Keep the source JSON intact,
    but expose the actual domain to the runtime.
    """

    fact_type = _canonical_fact_type(fact_key)
    if fact_type in {"CUSTOMER_AGE", "AGE_YEARS"} and any(
        marker in title for marker in ("농업계고", "청년농부사관학교")
    ):
        return "AGRICULTURAL_EDUCATION_GRADUATE_CERTIFICATE_AVAILABLE"
    return fact_type


def _canonical_fact_acceptance_policy(
    semantic_type: FactSemanticType,
) -> FactAcceptancePolicy:
    if semantic_type == FactSemanticType.FUTURE_INTENT:
        return FactAcceptancePolicy(
            allow_provisional=True,
            provisional_semantic_types=[FactSemanticType.FUTURE_INTENT],
            provisional_source_types=[FactSourceType.USER_DECLARED],
        )
    if semantic_type == FactSemanticType.OBSERVED_FACT:
        return FactAcceptancePolicy(allow_provisional=False)
    return FactAcceptancePolicy(allow_provisional=True)


def _normalized_product_features(product: dict[str, Any]) -> list[ProductFeature]:
    """Project explicitly supported normalized structures into typed features.

    This deliberately keys off the canonical preferential application mode;
    variable rates and free-form text are not evidence of a lottery benefit.
    """
    preferential = (product.get("return_policy") or {}).get("preferential_application") or {}
    if preferential.get("mode") == "CUMULATIVE_LOTTERY":
        return [ProductFeature(feature_id="LOTTERY_BASED_BENEFIT", present=True)]
    return []


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NormalizedCatalogError(f"Cannot read published JSON: {path}: {exc}") from exc


def _resolve_published_path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise NormalizedCatalogError(f"Published path escapes repository root: {value}") from exc
    return candidate


def _verify_sha256(path: Path, expected: str) -> None:
    if not expected.startswith("sha256:"):
        raise NormalizedCatalogError(f"Unsupported digest format for {path}: {expected}")
    actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise NormalizedCatalogError(
            f"Published file hash mismatch: {path} (expected {expected}, got {actual})"
        )


def _load_institutions(root: Path) -> dict[str, dict[str, Any]]:
    index_path = root / "data/institutions/normalized/index.json"
    index = _read_json(index_path)
    snapshot_path = _resolve_published_path(root, index["current_snapshot"])
    snapshot = _read_json(snapshot_path)
    rows = snapshot.get("institutions")
    if not isinstance(rows, list) or len(rows) != index.get("institution_count"):
        raise NormalizedCatalogError("Institution snapshot count does not match its index")
    institutions = {row["institution_id"]: row for row in rows}
    if len(institutions) != len(rows):
        raise NormalizedCatalogError("Institution snapshot contains duplicate institution_id values")
    return institutions


def _load_naver_listing_snapshots(
    root: Path, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    path = _resolve_published_path(
        root,
        manifest.get(
            "listing_snapshot_file",
            "data/financial_products/normalized/listing_snapshots/naver_savings_20260826.json",
        ),
    )
    if not path.exists():
        return {}
    payload = _read_json(path)
    return {row["product_code"]: row for row in payload.get("records", [])}


def _load_condition_snapshots(
    root: Path, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Load the source-preserving condition display model when published.

    Its rows are deliberately supplemental: contract rules and rate entries
    continue to be the only input to personalized eligibility calculation.
    """
    path = _resolve_published_path(
        root,
        manifest.get(
            "condition_snapshot_file",
            "data/financial_products/normalized/condition_snapshots/eligibility_preferential_20260828.json",
        ),
    )
    if not path.exists():
        return {}
    payload = _read_json(path)
    return {row["product_code"]: row for row in payload.get("records", [])}


def _source_registries(
    root: Path, manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    source_payload = _read_json(
        _resolve_published_path(root, manifest["source_document_registry"])
    )
    evidence_payload = _read_json(
        _resolve_published_path(root, manifest["evidence_ref_registry"])
    )
    sources = {
        item["source_id"]: item for item in source_payload.get("source_documents", [])
    }
    evidence = {
        item["evidence_ref_id"]: item for item in evidence_payload.get("evidence_refs", [])
    }
    return sources, evidence


def _all_ref_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_ref_ids", "evidence_ref_ids"} and isinstance(item, list):
                found.update(str(ref) for ref in item)
            else:
                found.update(_all_ref_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_all_ref_ids(item))
    return found


def _official_sources(
    product: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    source_ids: set[str] = set()
    for ref_id in _all_ref_ids(product):
        evidence_row = evidence.get(ref_id)
        if evidence_row is not None and evidence_row.get("source_id"):
            source_ids.add(evidence_row["source_id"])
        elif ref_id in sources:
            source_ids.add(ref_id)
    rows = [sources[source_id] for source_id in sorted(source_ids) if source_id in sources]
    official = [
        row
        for row in rows
        if str(row.get("authority", "")).startswith("OFFICIAL")
        or row.get("authority_for_official_product_terms") is True
    ]
    if official:
        return official
    non_audit_rows = [row for row in rows if row.get("source_class") != "INTERNAL_AUDIT"]
    return non_audit_rows or rows


def _source_reference(
    ref_ids: Iterable[str],
    sources: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> SourceReference | None:
    for ref_id in ref_ids:
        evidence_row = evidence.get(ref_id, {})
        source = sources.get(evidence_row.get("source_id"))
        if source is None:
            continue
        locator = evidence_row.get("locator") or {}
        page = locator.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        return SourceReference(
            document=source.get("title") or source.get("document_type") or source["source_id"],
            document_id=source["source_id"],
            version_date=source.get("version_date"),
            page=page,
            section=locator.get("section") or locator.get("value"),
            source_url=source.get("url"),
            source_text=evidence_row.get("source_text") or evidence_row.get("claim"),
        )
    return None


def _term_unit(value: str | None) -> TermUnit:
    try:
        return TermUnit(value or "MONTH")
    except ValueError:
        return TermUnit.MONTH


def _terms(term_policy: dict[str, Any]) -> tuple[ContractTerm, ContractTerm | None, ContractTerm | None, list[ContractTerm]]:
    unit = _term_unit(term_policy.get("unit"))
    kind = term_policy.get("kind")
    representative_value = term_policy.get("representative_value")
    available: list[ContractTerm] = []
    minimum: ContractTerm | None = None
    maximum: ContractTerm | None = None
    fixed_value = term_policy.get("fixed_value", term_policy.get("value"))
    if kind == "FIXED" and fixed_value is not None:
        representative = ContractTerm(value=int(fixed_value), unit=unit)
        minimum = maximum = representative
    elif kind == "DISCRETE" and term_policy.get("allowed_values"):
        available = [
            ContractTerm(value=int(value), unit=unit)
            for value in term_policy["allowed_values"]
            if int(value) > 0
        ]
        minimum, maximum = min(available, key=lambda item: item.value), max(
            available, key=lambda item: item.value
        )
        representative = next(
            (item for item in available if representative_value is not None and item.value == int(representative_value)),
            next(
            (item for item in available if item.unit == TermUnit.MONTH and item.value == 12),
            minimum,
            ),
        )
    elif kind == "RANGE" and term_policy.get("min_value") and term_policy.get("max_value"):
        minimum = ContractTerm(value=int(term_policy["min_value"]), unit=unit)
        maximum = ContractTerm(value=int(term_policy["max_value"]), unit=unit)
        target = (
            int(representative_value)
            if representative_value is not None
            and minimum.value <= int(representative_value) <= maximum.value
            else 12 if unit == TermUnit.MONTH and minimum.value <= 12 <= maximum.value else minimum.value
        )
        representative = ContractTerm(value=target, unit=unit)
    else:
        # Open-ended and explicitly unknown terms use a comparison horizon only.
        # The canonical term_policy remains untouched and is what the API shows.
        representative = ContractTerm(value=12, unit=TermUnit.MONTH)
    return representative, minimum, maximum, available


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _range_contains(value: Decimal, row: dict[str, Any]) -> bool:
    minimum = _decimal(row.get("min_value"))
    maximum = _decimal(row.get("max_value"))
    if minimum is not None:
        if value < minimum or (value == minimum and row.get("min_inclusive") is False):
            return False
    if maximum is not None:
        if value > maximum or (value == maximum and row.get("max_inclusive") is False):
            return False
    return True


def _term_value(term: ContractTerm, unit: str) -> Decimal:
    if term.unit.value == unit:
        return Decimal(term.value)
    if unit == "DAY":
        if term.unit == TermUnit.WEEK:
            return Decimal(term.value * 7)
        if term.unit == TermUnit.MONTH:
            return (Decimal(term.value) * Decimal("30.4375")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        if term.unit == TermUnit.YEAR:
            return Decimal(term.value * 365)
    months = {
        TermUnit.DAY: Decimal(term.value) / Decimal("30.4375"),
        TermUnit.WEEK: Decimal(term.value * 7) / Decimal("30.4375"),
        TermUnit.MONTH: Decimal(term.value),
        TermUnit.YEAR: Decimal(term.value * 12),
    }[term.unit]
    if unit == "YEAR":
        return months / Decimal(12)
    if unit == "WEEK":
        return months * Decimal("30.4375") / Decimal(7)
    return months


def _condition_text_matches_term(text: str, term: ContractTerm) -> bool:
    normalized = re.sub(r"\s+", "", text)
    months = _term_value(term, "MONTH")
    exact_months = re.search(
        r"(?<!초과)(?<!이상)(\d+)개월(?!이상|초과|이하|미만)", normalized
    )
    if exact_months and months == Decimal(exact_months.group(1)):
        return True
    exact_years = re.search(
        r"(?<!초과)(?<!이상)(\d+)년(?!이상|초과|이하|미만)", normalized
    )
    if exact_years and months == Decimal(exact_years.group(1)) * 12:
        return True
    lower = re.search(r"(\d+)개월(이상|초과)", normalized)
    upper = re.search(r"(\d+)개월(이하|미만)", normalized)
    if lower or upper:
        if lower:
            value = Decimal(lower.group(1))
            if months < value or (months == value and lower.group(2) == "초과"):
                return False
        if upper:
            value = Decimal(upper.group(1))
            if months > value or (months == value and upper.group(2) == "미만"):
                return False
        return True
    return False


def select_base_rate(
    return_policy: dict[str, Any],
    term: ContractTerm,
    *,
    balance: Decimal | None = None,
    customer_scope: str | None = None,
    as_of: date | str | None = None,
) -> Decimal | None:
    """Select only an official numeric BASE entry applicable to this scenario."""

    if return_policy.get("return_kind") == "PERFORMANCE_LINKED":
        return None
    reinvestment = return_policy.get("reinvestment_policy") or {}
    reinvestment_interval = reinvestment.get("interval") or {}
    if isinstance(as_of, str):
        try:
            as_of = date.fromisoformat(as_of)
        except ValueError:
            as_of = None
    rate_entries = list(return_policy.get("rate_entries", []))
    if as_of is not None:
        active_changes: list[tuple[date, dict[str, Any]]] = []
        for change in return_policy.get("scheduled_rate_changes", []):
            effective_from = change.get("effective_from")
            if not effective_from or not change.get("rate_entries"):
                continue
            try:
                effective_date = date.fromisoformat(effective_from)
            except ValueError:
                continue
            if effective_date <= as_of and change.get("status") not in {"CANCELLED", "WITHDRAWN"}:
                active_changes.append((effective_date, change))
        if active_changes:
            # A scheduled table is a complete replacement effective on that
            # date, not another set to maximize against the prior table.
            rate_entries = list(max(active_changes, key=lambda item: item[0])[1]["rate_entries"])

    candidates: list[tuple[dict[str, Any], Decimal]] = []
    for entry in rate_entries:
        # A displayed snapshot or unresolved external reference may still carry
        # a number.  It is evidence for presentation, never a calculation input.
        if entry.get("executable") is False:
            continue
        calculation = entry.get("calculation") or {}
        value = _decimal(calculation.get("value"))
        if entry.get("role") != "BASE" or value is None:
            continue
        entry_scope = entry.get("customer_scope")
        if customer_scope is not None and entry_scope not in {None, customer_scope}:
            continue
        if as_of is not None:
            effective_from = entry.get("effective_from")
            effective_to = entry.get("effective_to")
            if effective_from:
                try:
                    if as_of < date.fromisoformat(effective_from):
                        continue
                except ValueError:
                    pass
            if effective_to:
                try:
                    if as_of > date.fromisoformat(effective_to):
                        continue
                except ValueError:
                    pass
        applies_to = _applies_to_rows(entry)
        applicable = True
        for application in applies_to:
            basis = application.get("basis")
            range_row = application.get("range") or {}
            if basis == "BALANCE":
                if balance is None:
                    applicable = False
                elif (
                    _balance_tier_method(return_policy) != "MARGINAL"
                    and not _range_contains(balance, range_row)
                ):
                    applicable = False
            elif basis in {"CONTRACT_TERM", "ACCOUNT_AGE"}:
                unit = range_row.get("unit", "MONTH")
                if not _range_contains(_term_value(term, unit), range_row):
                    applicable = False
            elif basis == "ELAPSED_TERM":
                unit = range_row.get("unit", "DAY")
                elapsed_value = _term_value(term, unit)
                if (
                    reinvestment.get("mode") == "AUTOMATIC"
                    and reinvestment_interval.get("value") is not None
                ):
                    interval_unit = reinvestment_interval.get("unit", "DAY")
                    interval_term = ContractTerm(
                        value=int(reinvestment_interval["value"]),
                        unit=TermUnit(interval_unit),
                    )
                    interval_value = _term_value(interval_term, unit)
                    if interval_value > 0 and elapsed_value > 0:
                        # Elapsed tiers restart with each new investment lot.
                        # Clamping to the last day incorrectly kept a mature
                        # lot in its highest tier forever.
                        elapsed_value = ((elapsed_value - 1) % interval_value) + 1
                if not _range_contains(elapsed_value, range_row):
                    applicable = False
        if applicable:
            candidates.append((entry, value))

    if len(candidates) == 1:
        return candidates[0][1]
    if len(candidates) > 1:
        # Marginal balance tiers require a weighted rate, never a maximum tier.
        if _balance_tier_method(return_policy) == "MARGINAL":
            return _marginal_balance_rate(candidates, balance)
        labelled = [
            value
            for entry, value in candidates
            if _condition_text_matches_term(str(entry.get("condition_text", "")), term)
        ]
        return labelled[0] if len(set(labelled)) == 1 else None
    return None


def _balance_tier_method(return_policy: dict[str, Any]) -> str | None:
    """Normalize canonical and legacy balance-application spellings."""

    declared = return_policy.get("balance_tier_method")
    if declared in {"MARGINAL", "MARGINAL_TRANCHE"}:
        return "MARGINAL"
    if declared in {"WHOLE_BALANCE", "WHOLE_BALANCE_TIER", "SINGLE_RATE"}:
        return "WHOLE_BALANCE"
    methods = {
        (entry.get("balance_application") or {}).get("method")
        for entry in return_policy.get("rate_entries", [])
        if entry.get("role") == "BASE" and entry.get("executable") is not False
    }
    methods.discard(None)
    if methods and methods <= {"MARGINAL", "MARGINAL_TRANCHE"}:
        return "MARGINAL"
    if methods and methods <= {"WHOLE_BALANCE", "WHOLE_BALANCE_TIER", "SINGLE_RATE"}:
        return "WHOLE_BALANCE"
    return declared


def _applies_to_rows(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical application rows while accepting legacy singleton objects.

    The rebuilt savings catalog contains both the current list representation
    and older product versions where ``applies_to`` is a single object.  The
    two forms carry the same meaning, so normalizing at the loader boundary
    keeps immutable source versions readable without mutating them in place.
    """

    raw = entry.get("applies_to")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _marginal_balance_rate(
    candidates: list[tuple[dict[str, Any], Decimal]], balance: Decimal | None
) -> Decimal | None:
    if balance is None or balance <= 0:
        return None
    interest_weight = Decimal("0")
    covered = Decimal("0")
    for entry, rate in candidates:
        applications = [
            item for item in _applies_to_rows(entry) if item.get("basis") == "BALANCE"
        ]
        if not applications:
            continue
        range_row = applications[0].get("range") or {}
        lower = _decimal(range_row.get("min_value")) or Decimal("0")
        upper = _decimal(range_row.get("max_value"))
        slice_end = min(balance, upper) if upper is not None else balance
        slice_amount = max(Decimal("0"), slice_end - lower)
        if slice_amount:
            interest_weight += slice_amount * rate
            covered += slice_amount
    return interest_weight / covered if covered == balance and covered > 0 else None


def _principal_scope_factor(
    scope: dict[str, Any] | str | None,
    balance: Decimal | None,
) -> Decimal:
    """Return the share of scenario principal receiving one reward."""

    if not isinstance(scope, dict) or balance is None or balance <= 0:
        # A few legacy records preserve a human-readable scope such as
        # ``ALL_CONTRIBUTIONS``. It has no numeric range to proportion, so keep
        # the declared reward unscaled rather than failing the entire search.
        return Decimal("1")
    method = scope.get("method") or "ALL_BALANCE"
    range_row = scope.get("range") or {}
    if not range_row:
        return Decimal("1")
    if method in {"WHOLE_BALANCE", "WHOLE_BALANCE_TIER", "ALL_BALANCE"}:
        return Decimal("1") if _range_contains(balance, range_row) else Decimal("0")
    if method not in {
        "CAPPED_PORTION",
        "MARGINAL",
        "MARGINAL_TRANCHE",
    }:
        # Dynamic formulas require their declared external/user parameter.  Do
        # not pretend they apply to the full balance.
        return Decimal("0")
    lower = _decimal(range_row.get("min_value")) or Decimal("0")
    upper = _decimal(range_row.get("max_value"))
    slice_end = min(balance, upper) if upper is not None else balance
    eligible = max(Decimal("0"), slice_end - lower)
    return min(Decimal("1"), eligible / balance)


def _scenario_reward_value(
    item: PreferentialRateRule,
    *,
    selected_base_rate: Decimal | None,
    balance: Decimal | None,
) -> Decimal:
    payload = item.reward_payload
    kind = item.reward_kind
    value = item.reward.value
    if kind == "TIERED_RATE" and balance is not None:
        chosen: dict[str, Any] | None = None
        for tier in payload.get("tiers") or []:
            if _range_contains(balance, tier.get("condition_range") or {}):
                chosen = tier
                break
        if chosen is None:
            return Decimal("0")
        final_rate = _decimal(chosen.get("final_rate"))
        if final_rate is not None and selected_base_rate is not None:
            value = max(Decimal("0"), final_rate - selected_base_rate)
        else:
            value = _decimal(chosen.get("reward_value")) or Decimal("0")
    elif kind == "SET_FINAL_RATE":
        final_rate = _decimal(payload.get("value"))
        if final_rate is None or selected_base_rate is None:
            return Decimal("0")
        value = max(Decimal("0"), final_rate - selected_base_rate)
    return value


def product_with_scenario_rate(
    product: ProductDefinition,
    term: ContractTerm,
    *,
    balance: Decimal | None = None,
    customer_scope: str | None = None,
    as_of: date | str | None = None,
) -> ProductDefinition:
    if product.normalized is None:
        return product
    selected = select_base_rate(
        product.normalized.return_policy,
        term,
        balance=balance,
        customer_scope=customer_scope,
        as_of=as_of,
    )
    preferential_rules = product.preferential_rules
    preferential_cap = product.preferential_rate_cap
    policy = product.normalized.return_policy
    published_policy = policy.get("preferential_policy") or {}
    published_rule_ids = {
        str(row.get("rule_id"))
        for row in published_policy.get("rules") or []
        if row.get("rule_id")
    }
    executable_rule_ids = {
        str(item.canonical_rule_id or item.rule.rule_id)
        for item in preferential_rules
    }
    has_unmodeled_preferential_rules = bool(
        published_rule_ids - executable_rule_ids
    )
    if _balance_tier_method(policy) == "MARGINAL" and balance is not None and balance > 0:
        entries_by_id = {
            entry.get("rate_id"): entry
            for entry in policy.get("rate_entries", [])
            if entry.get("role") == "PREFERENTIAL" and entry.get("rate_id")
        }
        scaled_rules: list[PreferentialRateRule] = []
        for item in preferential_rules:
            entry = next(
                (
                    row
                    for rate_id, row in entries_by_id.items()
                    if item.rule.rule_id.endswith(f":{rate_id}")
                ),
                None,
            )
            factor = Decimal("1")
            if entry is not None:
                balance_ranges = [
                    application.get("range") or {}
                    for application in _applies_to_rows(entry)
                    if application.get("basis") == "BALANCE"
                ]
                if balance_ranges:
                    range_row = balance_ranges[0]
                    lower = _decimal(range_row.get("min_value")) or Decimal("0")
                    upper = _decimal(range_row.get("max_value"))
                    slice_end = min(balance, upper) if upper is not None else balance
                    eligible_balance = max(Decimal("0"), slice_end - lower)
                    factor = eligible_balance / balance
            scaled_rules.append(
                item.model_copy(
                    update={"reward": item.reward.model_copy(update={"value": item.reward.value * factor})},
                    deep=True,
                )
            )
        preferential_rules = scaled_rules
        if preferential_cap is not None and not has_unmodeled_preferential_rules:
            preferential_cap = min(
                preferential_cap,
                sum((item.reward.value for item in preferential_rules), Decimal("0")),
            )
    if preferential_rules:
        scenario_rules: list[PreferentialRateRule] = []
        for item in preferential_rules:
            reward_value = _scenario_reward_value(
                item,
                selected_base_rate=selected,
                balance=balance,
            )
            scope = item.application.get("principal_scope")
            factor = _principal_scope_factor(scope, balance)
            reward_value *= factor
            scenario_rules.append(
                item.model_copy(
                    update={
                        "reward": item.reward.model_copy(
                            update={"value": reward_value}
                        ),
                        "application": {
                            **item.application,
                            "_scenario_principal_factor": str(factor),
                        },
                    },
                    deep=True,
                )
            )
        preferential_rules = scenario_rules
        if preferential_cap is not None and not has_unmodeled_preferential_rules:
            preferential_cap = min(
                preferential_cap,
                sum((item.reward.value for item in preferential_rules), Decimal("0")),
            )
    advertised_max_rate = product.advertised_max_rate
    if selected is not None:
        # A displayed maximum cannot be below the rate actually selected for
        # this term. Comparison pages occasionally attach their headline value
        # to a representative term even when another base tier is higher.
        advertised_max_rate = max(advertised_max_rate or selected, selected)
    if selected is not None and preferential_cap is not None and advertised_max_rate is not None:
        # The published maximum is a ceiling.  For a chosen term, its displayed
        # maximum must use that term's base rate rather than a different tier.
        advertised_max_rate = min(advertised_max_rate, selected + preferential_cap)
    if (
        selected == product.base_rate
        and preferential_rules == product.preferential_rules
        and preferential_cap == product.preferential_rate_cap
        and advertised_max_rate == product.advertised_max_rate
    ):
        return product
    return product.model_copy(
        update={
            "base_rate": selected,
            "advertised_max_rate": advertised_max_rate,
            "preferential_rules": preferential_rules,
            "preferential_rate_cap": preferential_cap,
            "metadata": (
                product.metadata.model_copy(
                    update={"base_rate": selected, "advertised_max_rate": advertised_max_rate},
                    deep=True,
                )
                if product.metadata is not None
                else None
            ),
        },
        deep=True,
    )


def _contribution_policy(cash_flow: dict[str, Any]) -> ContributionPolicy | None:
    if not cash_flow:
        return None
    funding_type = cash_flow.get("funding_type")
    frequency_map = {
        "DAILY": ContributionFrequency.DAILY,
        "WEEKLY": ContributionFrequency.WEEKLY,
        "MONTHLY": ContributionFrequency.MONTHLY,
        "IRREGULAR": ContributionFrequency.FLEXIBLE,
    }
    pattern_map = {
        "CONSTANT": ContributionMode.FIXED,
        "INCREMENTAL": ContributionMode.INCREMENTAL,
        "USER_DEFINED": ContributionMode.FLEXIBLE,
    }
    payload: dict[str, Any] = {
        "funding_type": funding_type,
        "currency": cash_flow.get("currency", "KRW"),
        "contribution_frequency": frequency_map.get(cash_flow.get("contribution_frequency")),
        "contribution_mode": pattern_map.get(cash_flow.get("contribution_pattern")),
    }
    challenge_rules = cash_flow.get("challenge_amount_rules", {})
    per_account = challenge_rules.get("per_account", {})
    aggregate = challenge_rules.get("aggregate_across_active_accounts", {})
    if per_account:
        payload.update(
            {
                "target_amount_min": _decimal(per_account.get("min_value")),
                "target_amount_max_per_account": _decimal(per_account.get("max_value")),
                "target_amount_increment": _decimal(per_account.get("increment")),
                "target_amount_immutable": bool(per_account.get("immutable_after_setting")),
            }
        )
    if aggregate:
        payload["target_amount_aggregate_max"] = _decimal(aggregate.get("max_value"))
    for rule in cash_flow.get("amount_rules", []):
        scope = rule.get("scope")
        minimum = _decimal(rule.get("min_value"))
        maximum = _decimal(rule.get("max_value"))
        options = [_decimal(value) for value in rule.get("allowed_values", [])]
        options = [value for value in options if value is not None]
        if scope == "INITIAL_DEPOSIT":
            payload["initial_amount_min"] = minimum
            payload["initial_amount_max"] = maximum
            payload["initial_amount_options"] = options
        elif scope in {"PER_CONTRIBUTION", "PER_PERIOD"}:
            payload["periodic_amount_min"] = minimum
            payload["periodic_amount_max"] = maximum
        elif scope in {"TOTAL_PRINCIPAL", "BALANCE"}:
            payload["total_principal_limit"] = maximum
            if scope == "BALANCE":
                payload["initial_amount_min"] = minimum
                payload["initial_amount_max"] = maximum
    if funding_type == "LUMP_SUM":
        payload["contribution_frequency"] = ContributionFrequency.FLEXIBLE
        payload["contribution_mode"] = ContributionMode.FIXED
    elif funding_type == "ON_DEMAND":
        payload["contribution_frequency"] = ContributionFrequency.FLEXIBLE
        payload["contribution_mode"] = ContributionMode.FLEXIBLE
    elif funding_type == "CHALLENGE_TARGET":
        payload["contribution_frequency"] = ContributionFrequency.WEEKLY
        payload["contribution_mode"] = ContributionMode.FIXED
    elif (
        payload.get("contribution_mode") == ContributionMode.INCREMENTAL
        and payload.get("increment_amount") is None
        and not payload.get("increment_amount_options")
    ):
        # The canonical record confirms an incremental pattern but does not
        # state the increment amount. Keep that fact in normalized data and do
        # not invent an executable increment for the legacy cashflow planner.
        payload["contribution_mode"] = ContributionMode.FLEXIBLE
    return ContributionPolicy.model_validate(payload)


def _eligibility_summary(
    policy: dict[str, Any],
    *,
    include_customer_types: bool = True,
    include_shared_identity: bool = True,
    include_age: bool = True,
    include_account_limit: bool = True,
) -> str | None:
    if not policy:
        return None
    if policy.get("mode") == "UNRESTRICTED":
        return "공식 가입대상 제한 없음"
    parts: list[str] = []
    customer_labels = {
        "INDIVIDUAL": "개인",
        "SOLE_PROPRIETOR": "개인사업자",
        "CORPORATION": "법인",
        "ORGANIZATION": "단체",
    }
    customers = [
        customer_labels.get(item, item)
        for item in policy.get("allowed_customer_types", [])
    ] if include_customer_types else []
    if customers:
        parts.append(" 또는 ".join(customers))
    age = (policy.get("age_range") or {}) if include_age else {}
    minimum_age = age.get("min_age", age.get("min_value"))
    maximum_age = age.get("max_age", age.get("max_value"))
    if minimum_age is not None:
        parts.append(f"만 {minimum_age}세 이상")
    if maximum_age is not None:
        parts.append(f"만 {maximum_age}세 이하")
    if include_shared_identity and policy.get("nationality_scope") == "KOREAN_ONLY":
        parts.append("대한민국 국적")
    if policy.get("residency_scope") == "RESIDENT_ONLY":
        parts.append("거주자")
    if policy.get("military_service_required"):
        parts.append("군 복무 요건")
    if include_shared_identity and policy.get("real_name_required"):
        parts.append("실명 가입")
    if policy.get("online_banking_membership_required"):
        parts.append("인터넷뱅킹 회원")
    employment = policy.get("employment_scope") or []
    if employment:
        parts.append("직업 요건 " + "/".join(str(item) for item in employment))
    excluded = [
        customer_labels.get(item, item)
        for item in policy.get("excluded_customer_types", [])
    ]
    if excluded:
        parts.append("가입 제외 대상 " + "/".join(excluded))
    account_limit = (policy.get("account_limit") or {}) if include_account_limit else {}
    if account_limit.get("max_active_accounts") is not None:
        parts.append(f"상품 계좌 최대 {account_limit['max_active_accounts']}개")
    return ", ".join(parts) if parts else "공식 가입대상 세부 확인 필요"


def _has_non_capacity_eligibility(policy: dict[str, Any]) -> bool:
    ignored = {
        "mode",
        "allowed_customer_types",
        "age_range",
        "account_limit",
        "nationality_scope",
        "real_name_required",
        # Relationship clauses preserved from the source are not sufficiently
        # typed to make a reliable yes/no eligibility question.  Keep them in
        # the product detail, but never turn a data gap into a user gate.
        "relationship_requirements",
        # Source-preserving presentation fields are not extra predicates once
        # their contents have been normalized into the typed fields above.
        "raw_text",
        "eligibility_text",
        "display_text",
        "source_ref_ids",
        "evidence_ref_ids",
    }
    for key, value in policy.items():
        if (
            key in ignored
            or value is None
            or value is False
            or value == ""
            or value == "ANY"
        ):
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return True
    return False


def _age_eligibility_rules(
    product_code: str,
    *,
    age_range: dict[str, Any],
    source: SourceReference | None,
) -> list[FactComparisonRule]:
    """Evaluate published age bounds from the one pre-search birth date."""

    rules: list[FactComparisonRule] = []
    bounds = (
        ("MIN", ComparisonOperator.GTE, age_range.get("min_age", age_range.get("min_value"))),
        ("MAX", ComparisonOperator.LTE, age_range.get("max_age", age_range.get("max_value"))),
    )
    for suffix, operator, expected in bounds:
        if expected is None:
            continue
        rules.append(
            FactComparisonRule(
                rule_id=f"{product_code}:AGE:{suffix}",
                name=(
                    f"만 {expected}세 이상"
                    if operator == ComparisonOperator.GTE
                    else f"만 {expected}세 이하"
                ),
                purpose=RulePurpose.ELIGIBILITY,
                source=source,
                fact_type="AGE_YEARS",
                operator=operator,
                expected=expected,
                on_true_status=EvaluationStatus.SATISFIED,
                on_false_status=EvaluationStatus.UNSATISFIABLE,
                on_missing_status=EvaluationStatus.UNKNOWN,
                missing_fact=None,
                fact_acceptance_policy=FactAcceptancePolicy(allow_provisional=True),
            )
        )
    return rules


def _account_limit_rule(
    product_code: str,
    *,
    product_name: str,
    account_limit: dict[str, Any],
    source: SourceReference | None,
) -> FactComparisonRule | None:
    """Turn a THIS_PRODUCT limit into the question a user can actually answer."""

    maximum = account_limit.get("max_active_accounts")
    if maximum is None or account_limit.get("product_scope") != "THIS_PRODUCT":
        return None
    if maximum == 1:
        question = f"현재 ‘{product_name}’ 계좌를 이미 가지고 계신가요?"
        grounding_terms = [product_name, "현재 보유 계좌"]
    else:
        question = (
            f"현재 ‘{product_name}’ 계좌를 {maximum}개 이상 가지고 계신가요?"
        )
        grounding_terms = [product_name, f"{maximum}개 이상 보유"]
    return _question_rule(
        product_code=product_code,
        rule_id="ACCOUNT_LIMIT",
        fact_type=f"EXISTING_PRODUCT_ACCOUNT_LIMIT_REACHED::{product_code}",
        name="기존 상품 계좌 보유 여부",
        question=question,
        purpose=RulePurpose.ELIGIBILITY,
        source=source,
        grounding_terms=grounding_terms,
        expected=False,
    )


def _shared_boolean_eligibility_rule(
    product_code: str,
    *,
    rule_id: str,
    fact_type: str,
    name: str,
    source: SourceReference | None,
) -> FactComparisonRule:
    """Use one session-level answer for a condition shared across products."""

    return FactComparisonRule(
        rule_id=f"{product_code}:{rule_id}",
        name=name,
        purpose=RulePurpose.ELIGIBILITY,
        source=source,
        fact_type=fact_type,
        operator=ComparisonOperator.EQ,
        expected=True,
        on_true_status=EvaluationStatus.SATISFIED,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        on_missing_status=EvaluationStatus.UNKNOWN,
        missing_fact=None,
        fact_acceptance_policy=FactAcceptancePolicy(allow_provisional=True),
    )


def _application_capacity_rule(
    product_code: str,
    *,
    allowed_customer_types: list[str],
    source: SourceReference | None,
) -> FactComparisonRule:
    return FactComparisonRule(
        rule_id=f"{product_code}:APPLICATION_CAPACITY",
        name="가입 명의",
        purpose=RulePurpose.ELIGIBILITY,
        source=source,
        fact_type="APPLICATION_CAPACITY",
        operator=ComparisonOperator.IN,
        expected=allowed_customer_types,
        on_true_status=EvaluationStatus.SATISFIED,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        on_missing_status=EvaluationStatus.UNKNOWN,
        true_reason_code="APPLICATION_CAPACITY_ALLOWED",
        false_reason_code="APPLICATION_CAPACITY_NOT_ALLOWED",
        missing_reason_code="APPLICATION_CAPACITY_NOT_ASKED",
        missing_fact=None,
        fact_acceptance_policy=FactAcceptancePolicy(allow_provisional=True),
    )


def _question_rule(
    *,
    product_code: str,
    rule_id: str,
    fact_type: str,
    name: str,
    question: str,
    purpose: RulePurpose,
    source: SourceReference | None,
    reward: Decimal | None = None,
    grounding_terms: list[str] | None = None,
    expected: bool = True,
) -> FactComparisonRule:
    return FactComparisonRule(
        rule_id=f"{product_code}:{rule_id}",
        name=name,
        purpose=purpose,
        description=question,
        source=source,
        fact_type=fact_type,
        operator=ComparisonOperator.EQ,
        expected=expected,
        on_true_status=EvaluationStatus.SATISFIED,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        on_missing_status=EvaluationStatus.UNKNOWN,
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.ASK_USER,
            impact=RateImpact(rate_pp=reward) if reward is not None else None,
            question=question,
            expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
            grounding_terms=grounding_terms or [name],
        ),
        fact_acceptance_policy=FactAcceptancePolicy(allow_provisional=True),
    )


def _pass_rule(product_code: str, source: SourceReference | None) -> FactComparisonRule:
    return FactComparisonRule(
        rule_id=f"{product_code}:BASE_ELIGIBILITY",
        name="구조화된 필수 가입조건 없음",
        purpose=RulePurpose.ELIGIBILITY,
        source=source,
        fact_type=f"NORMALIZED_BASE_ELIGIBILITY::{product_code}",
        operator=ComparisonOperator.EQ,
        expected=True,
        on_missing_status=EvaluationStatus.SATISFIED,
        missing_fact=None,
    )


def _data_gap_rule(
    product_code: str,
    *,
    field: str,
    source: SourceReference | None,
) -> FactComparisonRule:
    """Keep a published Data Gap unknown without turning it into a user quiz."""

    return FactComparisonRule(
        rule_id=f"{product_code}:DATA_GAP:{field}",
        name=f"{field} 확인 전 (Data Gap)",
        purpose=RulePurpose.ELIGIBILITY,
        source=source,
        fact_type=f"NORMALIZED_DATA_GAP::{field}::{product_code}",
        operator=ComparisonOperator.EQ,
        expected=True,
        on_true_status=EvaluationStatus.SATISFIED,
        on_false_status=EvaluationStatus.UNSATISFIABLE,
        on_missing_status=EvaluationStatus.UNKNOWN,
        missing_fact=MissingFactSpec(
            resolution_strategy=ResolutionStrategy.UNRESOLVABLE,
            required_source="OFFICIAL_PRODUCT_SOURCE",
            question=None,
            expected_semantic_type=None,
            grounding_terms=[field, "Data Gap"],
        ),
    )


def _reward_values(product: dict[str, Any]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for entry in product.get("return_policy", {}).get("rate_entries", []):
        calculation = entry.get("calculation") or {}
        value = _decimal(calculation.get("value"))
        if (
            entry.get("role") == "PREFERENTIAL"
            and calculation.get("unit") == "PERCENTAGE_POINT"
            and value is not None
        ):
            result[entry["rate_id"]] = value
    return result


def _build_rules(
    product: dict[str, Any],
    custom_by_key: dict[tuple[str, int], dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> tuple[Any, list[PreferentialRateRule], list[dict[str, Any]]]:
    code = product["product_code"]
    default_source = _source_reference(product.get("source_ref_ids", []), sources, evidence)
    eligibility_rules: list[Any] = []
    eligibility = product.get("eligibility_policy") or {}
    data_gap_paths = {
        row.get("path")
        for row in (product.get("version_metadata", {}) or {}).get("data_gaps", [])
    }
    summary = _eligibility_summary(eligibility)
    if "eligibility_policy" in data_gap_paths:
        eligibility_rules.append(
            _data_gap_rule(
                code,
                field="eligibility_policy",
                source=default_source,
            )
        )
    elif (
        (product.get("version_metadata") or {}).get("source_priority")
        == "INTERNAL_ORIGINAL_FALLBACK"
        and not eligibility.get("mode")
    ):
        # A legacy fallback may preserve useful official prose without a
        # machine-readable customer scope.  Treat that omission as unknown;
        # silently accepting it would make restricted personal/corporate
        # products look universally available during personalization.
        eligibility_rules.append(
            _data_gap_rule(
                code,
                field="eligibility_policy.customer_scope",
                source=default_source,
            )
        )
    elif eligibility.get("mode") == "RESTRICTED":
        eligibility_source = (
            _source_reference(
                eligibility.get("source_ref_ids", []), sources, evidence
            )
            or default_source
        )
        allowed_customer_types = list(
            eligibility.get("allowed_customer_types") or []
        )
        if allowed_customer_types:
            eligibility_rules.append(
                _application_capacity_rule(
                    code,
                    allowed_customer_types=allowed_customer_types,
                    source=eligibility_source,
                )
            )
        if eligibility.get("nationality_scope") == "KOREAN_ONLY":
            eligibility_rules.append(
                _shared_boolean_eligibility_rule(
                    code,
                    rule_id="KOREAN_NATIONAL",
                    fact_type="KOREAN_NATIONAL",
                    name="대한민국 국적",
                    source=eligibility_source,
                )
            )
        if eligibility.get("real_name_required"):
            eligibility_rules.append(
                _shared_boolean_eligibility_rule(
                    code,
                    rule_id="REAL_NAME_SUBSCRIPTION",
                    fact_type="REAL_NAME_SUBSCRIPTION_POSSIBLE",
                    name="실명 가입",
                    source=eligibility_source,
                )
            )
        eligibility_rules.extend(
            _age_eligibility_rules(
                code,
                age_range=eligibility.get("age_range") or {},
                source=eligibility_source,
            )
        )
        account_limit_rule = _account_limit_rule(
            code,
            product_name=product["name"],
            account_limit=eligibility.get("account_limit") or {},
            source=eligibility_source,
        )
        if account_limit_rule is not None:
            eligibility_rules.append(account_limit_rule)
        if _has_non_capacity_eligibility(eligibility):
            residual_summary = _eligibility_summary(
                eligibility,
                include_customer_types=False,
                include_shared_identity=False,
                include_age=False,
                include_account_limit=False,
            )
            eligibility_terms = [
                item.strip()
                for item in (residual_summary or "").split(",")
                if item.strip()
            ]
            eligibility_rules.append(
                _question_rule(
                    product_code=code,
                    rule_id="OFFICIAL_ELIGIBILITY",
                    fact_type=f"NORMALIZED_ELIGIBILITY::{code}",
                    name="공식 가입대상 세부 조건",
                    question=(
                        f"{product['name']}의 가입대상 세부 조건은 "
                        f"{residual_summary}입니다. 이 조건에 해당하시나요?"
                    ),
                    purpose=RulePurpose.ELIGIBILITY,
                    source=eligibility_source,
                    grounding_terms=eligibility_terms or ["공식 가입대상 세부 조건"],
                )
            )

    if "청년미래적금" in product["name"] and not any(
        getattr(rule, "fact_type", None) == "YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD"
        for rule in eligibility_rules
    ):
        eligibility_rules.append(
            _question_rule(
                product_code=code,
                rule_id="YOUTH_POLICY_ACCOUNT_HOLDING",
                fact_type="YOUTH_FUTURE_OR_LEAP_ACCOUNT_HELD",
                name="청년 정책형 적금 중복가입 제한",
                question=(
                    "청년미래적금이 이미 있거나 청년도약계좌에 중복가입되어 있나요?"
                ),
                purpose=RulePurpose.ELIGIBILITY,
                source=default_source,
                grounding_terms=["청년미래적금", "청년도약계좌", "중복가입"],
                expected=False,
            )
        )

    if "장병내일준비" in product["name"] and not any(
        getattr(rule, "fact_type", None) == "SOLDIER_TOMORROW_SAVINGS_ELIGIBLE"
        for rule in eligibility_rules
    ):
        eligibility_rules.append(
            _question_rule(
                product_code=code,
                rule_id="SOLDIER_TOMORROW_SAVINGS_ELIGIBILITY",
                fact_type="SOLDIER_TOMORROW_SAVINGS_ELIGIBLE",
                name="장병내일준비적금 공통 복무 대상",
                question=(
                    "현역병, 상근예비역, 의무경찰, 대체복무요원, "
                    "사회복무요원 중 하나에 해당하시나요? 모르거나 확인할 수 "
                    "없으면 가입 불가로 처리됩니다."
                ),
                purpose=RulePurpose.ELIGIBILITY,
                source=default_source,
                grounding_terms=[
                    "현역병",
                    "상근예비역",
                    "의무경찰",
                    "대체복무요원",
                    "사회복무요원",
                ],
                expected=True,
            )
        )

    reward_values = _reward_values(product)
    all_rate_ids = {
        entry.get("rate_id")
        for entry in product.get("return_policy", {}).get("rate_entries", [])
        if entry.get("rate_id")
    }
    preferential: list[PreferentialRateRule] = []
    resolved_custom: list[dict[str, Any]] = []

    condition_rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = [
        (condition, None)
        for condition in product.get("standard_conditions", [])
        # Some audited products keep contractual notices (for example,
        # advance/late-installment handling) beside executable conditions.
        # They belong in product details, but are not eligibility/rate rules.
        if condition.get("condition_id")
    ]
    for binding in product.get("custom_bindings", []):
        if (
            binding.get("product_code") != code
            or int(binding.get("product_version", -1)) != int(product["version"])
        ):
            raise NormalizedCatalogError(
                f"CustomBinding product identity mismatch: {code} -> {binding}"
            )
        key = (binding["custom_code"], int(binding["custom_version"]))
        definition = custom_by_key.get(key)
        if definition is None:
            raise NormalizedCatalogError(f"Unresolved CustomDefinition binding: {code} -> {key}")
        if definition["institution_id"] != product["institution_id"]:
            raise NormalizedCatalogError(f"Cross-institution CustomDefinition binding: {code} -> {key}")
        resolved_custom.append(definition)
        condition_rows.append((binding, definition))

    for condition, custom in condition_rows:
        is_custom = custom is not None
        condition_id = (
            f"CUSTOM-{condition['custom_code']}-V{condition['custom_version']}"
            if is_custom
            else condition["condition_id"]
        )
        condition_type = condition.get("condition_type")
        title = custom["title"] if custom is not None else condition.get("title") or {
            "FIRST_TRANSACTION": "해당 금융기관 첫거래 우대",
            "MARKETING_CONSENT": "상품·서비스 마케팅 동의",
            "SALARY_LINKAGE": "급여 수령 또는 급여이체 실적",
            "DEMAND_DEPOSIT_ACCOUNT": "입출금계좌 연결 또는 보유",
            "CARD_USAGE": "카드 이용 실적",
            "CARD_SPEND": "카드 결제금액 실적",
            "NON_FACE_TO_FACE_SIGNUP": "비대면 가입",
            "BANK_APP_USAGE": "금융기관 앱 이용",
            "UTILITY_PAYMENT_LINKAGE": "공과금 자동납부 연결",
            "BALANCE_THRESHOLD": "요구 잔액 유지",
            "LIVING_EXPENSE_LINKAGE": "생활비 결제 실적",
            "PENSION_LINKAGE": "연금 수령 또는 이체 실적",
            "AGE": "공식 연령 조건",
            "DOCUMENT_VERIFICATION": "증빙서류 확인",
        }.get(condition_type, "공식 우대조건")
        refs = condition.get("source_ref_ids", [])
        if custom is not None:
            refs = [*refs, *_all_ref_ids(custom)]
        source = _source_reference(refs, sources, evidence) or default_source
        base_fact_type = (
            f"CUSTOM::{condition['custom_code']}::V{condition['custom_version']}"
            if is_custom
            else "STANDARD::" + hashlib.sha256(
                json.dumps(
                    {
                        "type": condition.get("condition_type"),
                        "criteria": condition.get("criteria", {}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:20]
        )
        if condition_type == "FIRST_TRANSACTION":
            question = (
                "해당 금융기관의 첫거래 우대 기준에 따라, 기존 거래가 없는 "
                "고객에 해당하시나요?"
            )
        else:
            question = {
                "MARKETING_CONSENT": "상품·서비스 마케팅 동의 조건을 수락할 수 있으신가요?",
                "SALARY_LINKAGE": "급여를 이체받거나 급여이체 실적을 만들 수 있으신가요?",
                "DEMAND_DEPOSIT_ACCOUNT": "우대에 필요한 입출금계좌를 연결하거나 유지할 수 있으신가요?",
                "CARD_USAGE": "우대에 필요한 카드 이용 실적을 채울 수 있으신가요?",
                "CARD_SPEND": "우대에 필요한 카드 결제금액 실적을 채울 수 있으신가요?",
                "NON_FACE_TO_FACE_SIGNUP": "앱이나 웹을 통한 비대면 가입이 가능하신가요?",
                "BANK_APP_USAGE": "우대에 필요한 해당 금융기관 앱 이용 조건을 지킬 수 있으신가요?",
                "UTILITY_PAYMENT_LINKAGE": "공과금 자동납부를 연결할 수 있으신가요?",
                "BALANCE_THRESHOLD": "우대에 필요한 잔액을 유지할 수 있으신가요?",
                "LIVING_EXPENSE_LINKAGE": "생활비 결제 실적을 연결하거나 채울 수 있으신가요?",
                "PENSION_LINKAGE": "연금 수령이나 이체 실적을 연결할 수 있으신가요?",
                "AGE": "공식 연령 조건에 해당하시나요?",
                "DOCUMENT_VERIFICATION": "가입에 필요한 증빙서류를 제출할 수 있으신가요?",
            }.get(
                condition_type,
                f"{product['name']}의 ‘{title}’ 조건을 충족하시거나 충족할 수 있나요?",
            )
        purpose = condition.get("purpose")
        if purpose == "ELIGIBILITY":
            eligibility_rules.append(
                _question_rule(
                    product_code=code,
                    rule_id=condition_id,
                    fact_type=base_fact_type,
                    name=title,
                    question=question,
                    purpose=RulePurpose.ELIGIBILITY,
                    source=source,
                )
            )
        for reward_ref in condition.get("reward_refs", []):
            reward = reward_values.get(reward_ref)
            if reward is None:
                if reward_ref not in all_rate_ids:
                    raise NormalizedCatalogError(
                        f"reward_ref does not resolve to a RateEntry: {code}:{reward_ref}"
                    )
                # Some canonical bindings select a complete BASE RateEntry
                # rather than add a percentage-point reward.  The relationship
                # stays in normalized.custom_bindings; the legacy additive
                # evaluator must not reinterpret it as +%p.
                continue
            reward_condition = None
            if custom is not None:
                for block in custom.get("content_blocks", []):
                    for item in (block.get("content") or {}).get("rules", []):
                        if item.get("reward_ref") == reward_ref:
                            reward_condition = item.get("condition")
                            break
                    if reward_condition:
                        break
            reward_title = reward_condition or title
            reward_question = (
                f"{reward_condition}. 이 조건을 충족하시거나 충족할 수 있으신가요?"
                if reward_condition
                else question
            )
            reward_fact_type = (
                f"{base_fact_type}::{reward_ref}"
                if reward_condition
                else base_fact_type
            )
            rule = _question_rule(
                product_code=code,
                rule_id=f"{condition_id}:{reward_ref}",
                fact_type=reward_fact_type,
                name=reward_title,
                question=reward_question,
                purpose=RulePurpose.PREFERENTIAL_RATE,
                source=source,
                reward=reward,
                grounding_terms=[reward_title],
            )
            preferential.append(PreferentialRateRule(rule=rule, reward=Reward(value=reward)))

    canonical_preferential = product.get("return_policy", {}).get("preferential_policy") or {}
    if not preferential and canonical_preferential.get("mode") == "ADD_RATE":
        reward_value = _decimal((canonical_preferential.get("add_rate") or {}).get("value"))
        policy_conditions = canonical_preferential.get("conditions") or []
        if reward_value is not None and policy_conditions:
            operator_map = {
                "EQ": ComparisonOperator.EQ,
                "NEQ": ComparisonOperator.NEQ,
                "GT": ComparisonOperator.GT,
                "GTE": ComparisonOperator.GTE,
                "LT": ComparisonOperator.LT,
                "LTE": ComparisonOperator.LTE,
                "IN": ComparisonOperator.IN,
            }
            policy_source = (
                _source_reference(canonical_preferential.get("source_ref_ids", []), sources, evidence)
                or default_source
            )
            nodes: list[FactComparisonRule] = []
            for number, condition in enumerate(policy_conditions, start=1):
                operator = operator_map.get(condition.get("operator"))
                fact_type = _canonical_fact_type(
                    str(condition.get("basis") or "")
                )
                if operator is None or not fact_type or "value" not in condition:
                    nodes = []
                    break
                nodes.append(
                    FactComparisonRule(
                        rule_id=f"{code}:CANONICAL_PREFERENTIAL:{number}",
                        name=str(condition.get("basis")),
                        purpose=RulePurpose.PREFERENTIAL_RATE,
                        source=policy_source,
                        fact_type=fact_type,
                        operator=operator,
                        expected=condition["value"],
                        on_true_status=EvaluationStatus.SATISFIED,
                        on_false_status=EvaluationStatus.UNSATISFIABLE,
                        on_missing_status=EvaluationStatus.UNKNOWN,
                        missing_fact=MissingFactSpec(
                            resolution_strategy=ResolutionStrategy.ASK_USER,
                            impact=RateImpact(rate_pp=reward_value),
                            question=f"{condition.get('basis')} 값을 알려주세요.",
                            expected_semantic_type=FactSemanticType.SELF_REPORTED_FACT,
                            grounding_terms=[str(condition.get("basis"))],
                        ),
                        fact_acceptance_policy=FactAcceptancePolicy(allow_provisional=True),
                    )
                )
            if nodes:
                policy_rule: Any = nodes[0] if len(nodes) == 1 else AndRule(
                    rule_id=f"{code}:CANONICAL_PREFERENTIAL:ALL",
                    name="구조화된 CMA 우대조건 전체",
                    purpose=RulePurpose.PREFERENTIAL_RATE,
                    source=policy_source,
                    children=nodes,
                )
                preferential.append(
                    PreferentialRateRule(rule=policy_rule, reward=Reward(value=reward_value))
                )

    if (
        not preferential
        and canonical_preferential.get("rules")
        and canonical_preferential.get("executable") is not False
    ):
        operator_map = {
            "EQ": ComparisonOperator.EQ,
            "EQUALS": ComparisonOperator.EQ,
            "NEQ": ComparisonOperator.NEQ,
            "NOT_EQUALS": ComparisonOperator.NEQ,
            "GT": ComparisonOperator.GT,
            "GTE": ComparisonOperator.GTE,
            "LT": ComparisonOperator.LT,
            "LTE": ComparisonOperator.LTE,
            "IN": ComparisonOperator.IN,
            "NOT_IN": ComparisonOperator.NOT_IN,
            "BETWEEN": ComparisonOperator.BETWEEN_INCLUSIVE,
        }

        def compile_policy_condition(
            condition: dict[str, Any],
            *,
            prefix: str,
            source: SourceReference | None,
            reward_value: Decimal,
            title: str,
        ) -> Any | None:
            predicate = condition.get("predicate") or {}
            if predicate:
                raw_operator = predicate.get("operator")
                fact_key = str(predicate.get("fact_key") or "")
                fact_type = _canonical_predicate_fact_type(fact_key, title)
                expected = predicate.get("expected_value")
                if raw_operator == "IS_KNOWN":
                    operator = ComparisonOperator.EQ
                    expected = True
                    fact_type = f"{fact_type}__KNOWN"
                else:
                    operator = operator_map.get(raw_operator)
                if raw_operator == "BETWEEN" and isinstance(expected, dict):
                    expected = [expected.get("start"), expected.get("end")]
                if operator is None or not fact_type or expected is None:
                    return None
                if (
                    predicate.get("self_report_eligible") is False
                    or predicate.get("authority")
                ):
                    semantic_type = FactSemanticType.OBSERVED_FACT
                    resolution_strategy = ResolutionStrategy.QUERY_INSTITUTION
                elif is_future_action_fact(fact_type):
                    semantic_type = FactSemanticType.FUTURE_INTENT
                    resolution_strategy = ResolutionStrategy.ASK_USER
                else:
                    semantic_type = FactSemanticType.SELF_REPORTED_FACT
                    resolution_strategy = ResolutionStrategy.ASK_USER
                return FactComparisonRule(
                    rule_id=prefix,
                    name=title,
                    purpose=RulePurpose.PREFERENTIAL_RATE,
                    source=source,
                    fact_type=fact_type,
                    operator=operator,
                    expected=expected,
                    on_true_status=EvaluationStatus.SATISFIED,
                    on_false_status=EvaluationStatus.UNSATISFIABLE,
                    on_missing_status=EvaluationStatus.UNKNOWN,
                    missing_fact=MissingFactSpec(
                        resolution_strategy=resolution_strategy,
                        impact=RateImpact(rate_pp=reward_value),
                        question=f"{title} 조건에 해당하는지 알려주세요.",
                        expected_semantic_type=semantic_type,
                        grounding_terms=[title, fact_key],
                    ),
                    required_semantic_type=semantic_type,
                    fact_acceptance_policy=_canonical_fact_acceptance_policy(
                        semantic_type
                    ),
                )
            children = []
            operands = (
                condition.get("operands")
                or condition.get("children")
                or condition.get("conditions")
                or []
            )
            for index, child in enumerate(operands, start=1):
                compiled = compile_policy_condition(
                    child,
                    prefix=f"{prefix}:{index}",
                    source=source,
                    reward_value=reward_value,
                    title=title,
                )
                if compiled is None:
                    return None
                children.append(compiled)
            if not children:
                # Calendar-relative and other already-derived canonical leaves
                # are represented as one explicit boolean fact instead of being
                # silently discarded or guessed by the adapter.
                if condition:
                    leaf_key = hashlib.sha256(
                        json.dumps(condition, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest()[:20]
                    return _question_rule(
                        product_code=code,
                        rule_id=f"CANONICAL_LEAF:{leaf_key}",
                        fact_type=f"CANONICAL_DERIVED::{code}::{leaf_key}",
                        name=title,
                        question=f"{title} 조건에 해당하시나요?",
                        purpose=RulePurpose.PREFERENTIAL_RATE,
                        source=source,
                        reward=reward_value,
                        grounding_terms=[title],
                    )
                return None
            operator = condition.get("operator") or condition.get("op")
            node_type = AndRule if operator == "AND" else OrRule if operator == "OR" else None
            if node_type is None:
                return None
            return node_type(
                rule_id=prefix,
                name=title,
                purpose=RulePurpose.PREFERENTIAL_RATE,
                source=source,
                children=children,
            )

        def canonical_reward_ceiling(reward_row: dict[str, Any]) -> Decimal | None:
            kind = reward_row.get("kind")
            if kind in {"ADD_RATE", "SET_FINAL_RATE"}:
                return _decimal(reward_row.get("value"))
            if kind == "ADD_RATE_FROM_FACT":
                return _decimal(reward_row.get("max_value"))
            if kind == "REPEATABLE_ADD_RATE":
                maximum = _decimal(reward_row.get("maximum_reward"))
                if maximum is not None:
                    return maximum
                per_count = _decimal(
                    reward_row.get("value_per_count") or reward_row.get("value")
                )
                max_count = _decimal(reward_row.get("max_count"))
                if per_count is not None and max_count is not None:
                    return per_count * max_count
            if kind == "TIERED_RATE":
                values = [
                    _decimal(tier.get("reward_value") or tier.get("final_rate"))
                    for tier in reward_row.get("tiers") or []
                ]
                values = [value for value in values if value is not None]
                return max(values) if values else None
            if kind == "ADJUST_TOTAL_RATE_STATE":
                return _decimal((reward_row.get("on_condition_met") or {}).get("delta"))
            if kind == "CUMULATIVE_RATE":
                cap = _decimal(reward_row.get("cap_value"))
                if cap is not None:
                    return cap
                per_occurrence = _decimal(reward_row.get("value_per_occurrence"))
                max_occurrences = _decimal(reward_row.get("max_occurrences"))
                if per_occurrence is not None and max_occurrences is not None:
                    return per_occurrence * max_occurrences
            return None

        global_application = canonical_preferential.get("global_application") or {}
        for number, policy_row in enumerate(canonical_preferential.get("rules") or [], start=1):
            if policy_row.get("executable") is False:
                continue
            reward_row = policy_row.get("reward") or {}
            reward_value = canonical_reward_ceiling(reward_row)
            if reward_value is None:
                continue
            policy_source = (
                _source_reference(policy_row.get("source_ref_ids", []), sources, evidence)
                or default_source
            )
            canonical_rule_id = str(
                policy_row.get("rule_id") or f"{code}-PREF-{number:02d}"
            )
            title = str(policy_row.get("title") or "구조화된 우대조건")
            condition_payload = dict(policy_row.get("condition") or {})
            source_clause_text = str(policy_row.get("source_clause_text") or "")
            # Repair a few recurring source-schema gates whose predicate key is
            # less expressive than the official clause.  These are semantic
            # families, not product-ID exceptions, so every matching product is
            # compiled into the same answerable user variable.
            if "인터넷/모바일뱅킹에서 가입" in title:
                title = "인터넷뱅킹이나 모바일뱅킹으로 가입할 예정인가요?"
                condition_payload = {
                    "predicate": {
                        "fact_key": "SUBSCRIPTION_CHANNEL_DIGITAL",
                        "operator": "EQUALS",
                        "expected_value": True,
                    }
                }
            elif (
                "교차거래 우대이율" in title
                and "급여이체실적" in source_clause_text
                and "KB국민카드" in source_clause_text
            ):
                title = (
                    "가입 3개월이 지난 달에 급여이체 실적을 만들거나, "
                    "KB국민카드를 30만원 이상 사용할 수 있나요?"
                )
                condition_payload = {
                    "predicate": {
                        "fact_key": "KB_CROSS_TRANSACTION_REQUIREMENT_POSSIBLE",
                        "operator": "EQUALS",
                        "expected_value": True,
                    }
                }
            elif (
                "주택담보대출 또는 전세자금대출" in title
                and "해지시점" in source_clause_text
            ):
                title = (
                    "해당 은행의 주택담보대출이나 전세자금대출을 이미 이용 중이거나, "
                    "적금 가입 기간 중 이용해 만기까지 유지할 계획이 있나요?"
                )
                condition_payload = {
                    "predicate": {
                        "fact_key": "QUALIFYING_HOME_LOAN_HELD_THROUGH_SAVINGS_TERMINATION",
                        "operator": "EQUALS",
                        "expected_value": True,
                    }
                }
            reward_kind = reward_row.get("kind")
            supplemental_predicate: dict[str, Any] | None = None
            if reward_kind == "REPEATABLE_ADD_RATE" and reward_row.get("count_fact_key"):
                supplemental_predicate = {
                    "predicate": {
                        "fact_key": reward_row["count_fact_key"],
                        "operator": "GTE",
                        "expected_value": 1,
                    }
                }
            elif reward_kind == "ADD_RATE_FROM_FACT" and reward_row.get("fact_key"):
                supplemental_predicate = {
                    "predicate": {
                        "fact_key": reward_row["fact_key"],
                        "operator": "BETWEEN",
                        "expected_value": {
                            "start": reward_row.get("min_value"),
                            "end": reward_row.get("max_value"),
                        },
                    }
                }
            if supplemental_predicate is not None:
                condition_payload = {
                    "operator": "AND",
                    "operands": [condition_payload, supplemental_predicate],
                }
            compiled = compile_policy_condition(
                condition_payload,
                prefix=f"{code}:CANONICAL:{canonical_rule_id}",
                source=policy_source,
                reward_value=reward_value,
                title=title,
            )
            if compiled is not None:
                application = dict(global_application)
                application.update(policy_row.get("application") or {})
                preferential.append(
                    PreferentialRateRule(
                        rule=compiled,
                        reward=Reward(value=reward_value),
                        canonical_rule_id=canonical_rule_id,
                        reward_kind=str(reward_kind or "ADD_RATE"),
                        reward_payload=reward_row,
                        application=application,
                    )
                )

    if not eligibility_rules:
        eligibility_rule: Any = _pass_rule(code, default_source)
    elif len(eligibility_rules) == 1:
        eligibility_rule = eligibility_rules[0]
    else:
        eligibility_rule = AndRule(
            rule_id=f"{code}:ALL_ELIGIBILITY",
            name="공식 가입조건 전체",
            purpose=RulePurpose.ELIGIBILITY,
            source=default_source,
            children=eligibility_rules,
        )
    return eligibility_rule, preferential, resolved_custom


def _advertised_rate(return_policy: dict[str, Any]) -> Decimal | None:
    return _decimal((return_policy.get("advertised_max_rate") or {}).get("value"))


def _preferential_cap(
    product: dict[str, Any], base_rate: Decimal | None, advertised: Decimal | None
) -> Decimal | None:
    # The published headline maximum is evidence for the entire preferential
    # envelope.  A partially structured policy can cover only one component,
    # so it must not lower that published envelope.
    advertised_floor = (
        advertised - base_rate
        if advertised is not None
        and base_rate is not None
        and advertised >= base_rate
        else None
    )

    def with_advertised_floor(cap: Decimal | None) -> Decimal | None:
        if advertised_floor is None:
            return cap
        return max(cap, advertised_floor) if cap is not None else advertised_floor

    declared = _decimal(
        (product.get("return_policy", {}).get("preferential_application") or {}).get(
            "cap_value"
        )
    )
    if declared is not None:
        return with_advertised_floor(declared)
    canonical_policy = (
        product.get("return_policy", {}).get("preferential_policy") or {}
    )
    canonical_cap = _decimal((canonical_policy.get("global_cap") or {}).get("value"))
    if canonical_cap is not None:
        return with_advertised_floor(canonical_cap)
    relation_caps: list[tuple[bool, Decimal]] = []
    for relation in canonical_policy.get("relations") or []:
        relation_type = str(
            relation.get("type") or relation.get("operator") or ""
        ).upper()
        if relation_type != "SUM_WITH_CAP":
            continue
        value = _decimal(
            (relation.get("cap") or {}).get("value") or relation.get("cap_value")
        )
        if value is None:
            continue
        scope = str(relation.get("scope") or "").upper()
        relation_caps.append(("TOTAL" in scope or "ENVELOPE" in scope, value))
    total_relation_caps = [value for is_total, value in relation_caps if is_total]
    if total_relation_caps:
        return with_advertised_floor(max(total_relation_caps))
    # A lone SUM_WITH_CAP relation can cap only one subgroup (for example,
    # cross-selling) while other independent bonuses still apply.  Treat it
    # as the product-wide cap only when its scope explicitly says so.
    if canonical_policy.get("rules"):
        known_rewards: list[Decimal] = []
        for rule in canonical_policy.get("rules") or []:
            if rule.get("executable") is False:
                continue
            reward = rule.get("reward") or {}
            kind = reward.get("kind")
            value: Decimal | None = None
            if kind == "ADD_RATE":
                value = _decimal(reward.get("value"))
            elif kind == "ADD_RATE_FROM_FACT":
                value = _decimal(reward.get("max_value"))
            elif kind == "REPEATABLE_ADD_RATE":
                value = _decimal(reward.get("maximum_reward"))
                if value is None:
                    per_count = _decimal(
                        reward.get("value_per_count") or reward.get("value")
                    )
                    max_count = _decimal(reward.get("max_count"))
                    if per_count is not None and max_count is not None:
                        value = per_count * max_count
            elif kind == "CUMULATIVE_RATE":
                value = _decimal(
                    reward.get("cap_value") or reward.get("maximum_reward")
                )
                if value is None:
                    per_occurrence = _decimal(
                        reward.get("value_per_occurrence") or reward.get("value")
                    )
                    max_units = _decimal(
                        reward.get("max_qualifying_units") or reward.get("max_count")
                    )
                    if per_occurrence is not None and max_units is not None:
                        value = per_occurrence * max_units
            elif kind == "TIERED_RATE":
                tiers = [
                    _decimal(row.get("reward_value"))
                    for row in reward.get("tiers") or []
                ]
                tiers = [item for item in tiers if item is not None]
                value = max(tiers) if tiers else None
            elif kind == "SET_FINAL_RATE" and base_rate is not None:
                final_rate = _decimal(reward.get("value"))
                if final_rate is not None:
                    value = max(Decimal("0"), final_rate - base_rate)
            if value is not None:
                known_rewards.append(value)
        known_cap = sum(known_rewards, Decimal("0")) if known_rewards else None
        # Some source clauses encode a repeatable per-event increment using a
        # legacy ADD_RATE row.  The executable rule stays conservative, while
        # metadata must still retain the source-listed maximum envelope.
        return with_advertised_floor(known_cap)
    if advertised_floor is not None:
        return advertised_floor
    rewards = _reward_values(product)
    referenced = {
        ref
        for row in [
            *product.get("standard_conditions", []),
            *product.get("custom_bindings", []),
        ]
        for ref in row.get("reward_refs", [])
    }
    values = [rewards[ref] for ref in referenced if ref in rewards]
    return sum(values, Decimal("0")) if values else None


def _sale_status(value: str) -> SaleStatus:
    try:
        return SaleStatus(value)
    except ValueError:
        return SaleStatus.UNKNOWN


def _channels(values: list[str]) -> list[SubscriptionChannel]:
    mapping = {
        "MOBILE": SubscriptionChannel.MOBILE,
        "MOBILE_APP": SubscriptionChannel.MOBILE,
        "MOBILE_WEB": SubscriptionChannel.WEB,
        "WEB": SubscriptionChannel.WEB,
        "INTERNET": SubscriptionChannel.WEB,
        "BRANCH": SubscriptionChannel.BRANCH,
        "TELEPHONE": SubscriptionChannel.CALL_CENTER,
        "CALL_CENTER": SubscriptionChannel.CALL_CENTER,
        "PARTNER": SubscriptionChannel.PARTNER,
        "TABLET": SubscriptionChannel.OTHER,
        "OTHER": SubscriptionChannel.OTHER,
    }
    channels: list[SubscriptionChannel] = []
    for value in values:
        channel = mapping.get(value)
        if channel is not None and channel not in channels:
            channels.append(channel)
    return channels


def _institution_sector(
    institution: dict[str, Any],
    *,
    product_family: str,
) -> str:
    """Map official institution identity data to the comparison scope."""

    sic_code = str(institution.get("sicCd") or "")
    if sic_code in {"64131", "64132"}:
        return "SAVINGS_BANK"
    if sic_code in {"64121", "64912"}:
        return "BANK"
    if sic_code.startswith("6612") or product_family == "CMA":
        return "SECURITIES"
    # The current official snapshot has no sicCd for Toss Bank. Keep this as
    # an explicit canonical-institution exception, not a name substring rule.
    if institution.get("institution_id") == "INST-KR-001106":
        return "BANK"
    if institution.get("institution_id") == "INST-KR-001107":
        return "OTHER"
    return "UNKNOWN"


def _adapt_product(
    product: dict[str, Any],
    institution: dict[str, Any],
    custom_by_key: dict[tuple[str, int], dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    listing_snapshot: dict[str, Any] | None = None,
    condition_snapshot: dict[str, Any] | None = None,
) -> ProductDefinition:
    representative, minimum, maximum, available = _terms(product.get("term_policy", {}))
    return_policy = product.get("return_policy", {})
    base_rate = select_base_rate(return_policy, representative)
    advertised = _advertised_rate(return_policy)
    cap = _preferential_cap(product, base_rate, advertised)
    # Tiered contract-term products advertise their maximum using the highest
    # base tier, while the representative runtime term (normally 12 months)
    # may select a lower tier.  Use that highest tier for the metadata
    # arithmetic guard so a valid advertised maximum remains loadable.
    if (
        advertised is not None
        and cap is not None
        and base_rate is not None
        and advertised > base_rate + cap
    ):
        tiered_bases = []
        for entry in return_policy.get("rate_entries", []):
            if entry.get("role") != "BASE":
                continue
            if not any(a.get("basis") == "CONTRACT_TERM" for a in _applies_to_rows(entry)):
                continue
            value = _decimal((entry.get("calculation") or {}).get("value"))
            if value is not None:
                tiered_bases.append(value)
        if tiered_bases and advertised <= max(tiered_bases) + cap:
            base_rate = max(tiered_bases)
    eligibility_rule, preferential_rules, resolved_custom = _build_rules(
        product, custom_by_key, sources, evidence
    )
    official_sources = _official_sources(product, sources, evidence)
    source_reference = _source_reference(product.get("source_ref_ids", []), sources, evidence)
    data_gap_paths = {
        row.get("path")
        for row in (product.get("version_metadata", {}) or {}).get("data_gaps", [])
    }
    metadata = ProductMetadata(
        institution_id=product["institution_id"],
        institution_name=institution["official_name_ko"],
        institution_sector=_institution_sector(
            institution,
            product_family=product["product_family"],
        ),
        product_id=product["product_code"],
        product_name=product["name"],
        product_type=product["product_family"],
        product_subtype=product.get("product_subtype"),
        sale_status=_sale_status(product["sale_policy"]["status"]),
        min_term=minimum,
        max_term=maximum,
        available_terms=available,
        contribution_policy=_contribution_policy(product.get("cash_flow_policy", {})),
        base_rate=base_rate,
        advertised_max_rate=advertised,
        preferential_rate_cap=cap,
        allowed_channels=_channels(product.get("sale_policy", {}).get("subscription_channels", [])),
        features=_normalized_product_features(product),
        target_customer_summary=(
            None
            if "eligibility_policy" in data_gap_paths
            else _eligibility_summary(product.get("eligibility_policy", {}))
        ),
        one_account_per_person=(
            None
            if "eligibility_policy" in data_gap_paths
            else (
                (product.get("eligibility_policy", {}).get("account_limit") or {}).get(
                    "max_active_accounts"
                )
                == 1
                or None
            )
        ),
        sale_start=(product.get("sale_policy", {}).get("sale_period") or {}).get("start"),
        sale_end=(product.get("sale_policy", {}).get("sale_period") or {}).get("end"),
        quantity_limit=(product.get("sale_policy", {}).get("quantity_limit") or {}).get("max_count"),
        early_termination_policy=(
            "구조화됨" if product.get("liquidity_policy", {}).get("early_termination_rate_refs") else None
        ),
        partial_withdrawal_policy=(
            "가능" if (product.get("liquidity_policy", {}).get("partial_withdrawal") or {}).get("allowed") else None
        ),
        effective_from=product.get("effective_from"),
        effective_to=product.get("effective_to"),
        source_reference=source_reference,
    )
    normalized = NormalizedProductData(
        version=product["version"],
        institution_name=institution["official_name_ko"],
        product_subtype=product.get("product_subtype"),
        sale_status=product["sale_policy"]["status"],
        eligibility_policy=product.get("eligibility_policy", {}),
        term_policy=product.get("term_policy", {}),
        cash_flow_policy=product.get("cash_flow_policy", {}),
        investment_policy=product.get("investment_policy", {}),
        return_policy=return_policy,
        fee_policy=product.get("fee_policy", {}),
        tax_policy=product.get("tax_policy", {}),
        liquidity_policy=product.get("liquidity_policy", {}),
        protection_policy=product.get("protection_policy", {}),
        standard_conditions=product.get("standard_conditions", []),
        custom_bindings=product.get("custom_bindings", []),
        custom_definitions=resolved_custom,
        data_gaps=(product.get("version_metadata", {}) or {}).get("data_gaps", []),
        listing_snapshot=listing_snapshot or {},
        condition_snapshot=condition_snapshot or {},
        official_sources=official_sources,
        raw_product=product,
    )
    return ProductDefinition(
        product_id=product["product_code"],
        institution_id=product["institution_id"],
        name=product["name"],
        product_type=product["product_family"],
        contract_term=representative,
        base_rate=base_rate,
        advertised_max_rate=advertised,
        preferential_rate_cap=cap,
        eligibility_rule=eligibility_rule,
        preferential_rules=preferential_rules,
        metadata=metadata,
        normalized=normalized,
    )


def load_normalized_product_catalog(
    index_path: str | Path | None = None,
    *,
    include_sale_statuses: set[str] | None = None,
    verify_hashes: bool = True,
) -> list[ProductDefinition]:
    root = repository_root()
    path = Path(index_path) if index_path is not None else root / "data/financial_products/normalized/index.json"
    path = path.expanduser().resolve()
    index = _read_json(path)
    if not isinstance(index, dict) or not isinstance(index.get("products"), list):
        raise NormalizedCatalogError(f"Not a normalized published index: {path}")
    if index.get("publication_status") != "PUBLISHED":
        raise NormalizedCatalogError("Normalized product index is not PUBLISHED")
    if len(index["products"]) != index.get("product_count"):
        raise NormalizedCatalogError("Normalized product count does not match index")

    # A normalized correction can publish a new immutable manifest while still
    # inheriting the original staging batch. Older indexes only have
    # ``source_staging_batch`` and remain fully compatible.
    batch = str(index.get("manifest_batch") or index["source_staging_batch"])
    manifest_path = root / f"data/financial_products/normalized/manifests/{batch}.json"
    manifest = _read_json(manifest_path)
    if manifest.get("publication_status") != "PUBLISHED":
        raise NormalizedCatalogError("Normalized manifest is not PUBLISHED")
    if manifest.get("counts", {}).get("total_products") != index["product_count"]:
        raise NormalizedCatalogError("Manifest and index product counts disagree")
    if verify_hashes:
        for row in manifest.get("files", []):
            _verify_sha256(_resolve_published_path(root, row["path"]), row["sha256"])

    institutions = _load_institutions(root)
    sources, evidence = _source_registries(root, manifest)
    listing_snapshots = _load_naver_listing_snapshots(root, manifest)
    condition_snapshots = _load_condition_snapshots(root, manifest)
    custom_payload = _read_json(
        _resolve_published_path(root, manifest["custom_definition_file"])
    )
    custom_rows = custom_payload.get("institution_custom_definitions", [])
    custom_by_key = {
        (row["custom_code"], int(row["version"])): row for row in custom_rows
    }
    if len(custom_by_key) != manifest.get("counts", {}).get("custom_definitions"):
        raise NormalizedCatalogError("CustomDefinition count does not match manifest")
    for definition in custom_rows:
        if definition.get("institution_id") not in institutions:
            raise NormalizedCatalogError(
                "CustomDefinition references an unknown Institution: "
                f"{definition.get('custom_code')}"
            )

    indexed_institutions = {row["institution_id"] for row in index["products"]}
    if indexed_institutions != set(manifest.get("institution_ids", [])):
        raise NormalizedCatalogError("Manifest institution_ids do not match product index")

    allowed = include_sale_statuses or {"ON_SALE"}
    products: list[ProductDefinition] = []
    seen: set[tuple[str, int]] = set()
    exposed_equivalence_groups: set[str] = set()
    hidden_duplicate_count = 0
    for row in index["products"]:
        key = (row["product_code"], int(row["version"]))
        if key in seen:
            raise NormalizedCatalogError(f"Duplicate product version in index: {key}")
        seen.add(key)
        product_path = _resolve_published_path(root, row["path"])
        if verify_hashes:
            _verify_sha256(product_path, row["sha256"])
        product = _read_json(product_path)
        identity = (
            product.get("product_code"),
            product.get("version"),
            product.get("institution_id"),
            product.get("product_family"),
            product.get("sale_policy", {}).get("status"),
        )
        indexed_identity = (
            row["product_code"],
            row["version"],
            row["institution_id"],
            row["product_family"],
            row["sale_status"],
        )
        if identity != indexed_identity:
            raise NormalizedCatalogError(f"Index/product identity mismatch: {key}")
        institution = institutions.get(product["institution_id"])
        if institution is None:
            raise NormalizedCatalogError(
                f"Product references an unknown Institution: {product['product_code']}"
            )
        if row["sale_status"] in allowed:
            identity_audit = (product.get("version_metadata") or {}).get("identity_audit") or {}
            identity_status = identity_audit.get("status")
            equivalence_group = identity_audit.get("equivalence_group_id")
            if identity_status in {
                "SOURCE_ALIAS_OF_EXISTING_CANONICAL",
                "AGGREGATE_LISTING_SPANS_VARIANTS",
            }:
                hidden_duplicate_count += 1
                continue
            if (
                identity_status == "SAME_COMMERCIAL_PRODUCT_CROSS_INSTITUTION_ID"
                and equivalence_group
            ):
                if equivalence_group in exposed_equivalence_groups:
                    hidden_duplicate_count += 1
                    continue
                exposed_equivalence_groups.add(equivalence_group)
            try:
                adapted = _adapt_product(
                    product,
                    institution,
                    custom_by_key,
                    sources,
                    evidence,
                    listing_snapshots.get(product["product_code"]),
                    condition_snapshots.get(product["product_code"]),
                )
            except Exception as exc:
                raise NormalizedCatalogError(
                    f"Failed to adapt published product {product['product_code']} "
                    f"v{product['version']}: {exc}"
                ) from exc
            products.append(adapted)

    expected = sum(
        count
        for status, count in manifest.get("counts", {}).get("by_sale_status", {}).items()
        if status in allowed
    ) - hidden_duplicate_count
    if len(products) != expected:
        raise NormalizedCatalogError(
            f"Filtered normalized count mismatch: expected {expected}, got {len(products)}"
        )
    return products
