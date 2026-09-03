from __future__ import annotations

from dataclasses import dataclass
import hashlib
from datetime import date
from typing import Any, Iterable, TypeVar

from eligibility.audit import AuditEventType, AuditSession
from eligibility.engine.fact_acceptance import (
    AcceptanceResult,
    accept_records,
    verification_for_record,
)
from eligibility.question_policy import is_future_action_fact
from eligibility.schema.enums import (
    EvaluationTrustMode,
    FactSemanticType,
    FactSourceType,
    FactSubjectMode,
    ResolutionStatus,
    VerificationLevel,
)
from eligibility.schema.evaluation import MissingFactImpact, MissingFactRequest
from eligibility.schema.rule import (
    FactAcceptancePolicy,
    FactSubjectSelector,
    MissingFactSpec,
)
from eligibility.schema.user_fact import (
    PersonRelationship,
    UserFact,
    UserFactStore,
)


_SOURCE_PRIORITY: dict[FactSourceType, int] = {
    FactSourceType.INSTITUTION_VERIFIED: 500,
    FactSourceType.MYDATA_VERIFIED: 400,
    FactSourceType.DERIVED: 300,
    FactSourceType.USER_DECLARED: 200,
    FactSourceType.UNKNOWN: 0,
}


@dataclass(frozen=True)
class FactResolution:
    status: ResolutionStatus
    fact: UserFact | None = None
    competing_facts: tuple[UserFact, ...] = ()
    rejected_facts: tuple[UserFact, ...] = ()
    verification_level: VerificationLevel = VerificationLevel.UNKNOWN

    @property
    def value(self) -> Any:
        return None if self.fact is None else self.fact.value

    @property
    def is_provisional(self) -> bool:
        return self.verification_level in {
            VerificationLevel.SELF_REPORTED,
            VerificationLevel.MIXED,
        }


@dataclass(frozen=True)
class SubjectResolution:
    status: ResolutionStatus
    person_ids: tuple[str, ...] = ()
    relationships: tuple[PersonRelationship, ...] = ()
    unverified_relationships: tuple[PersonRelationship, ...] = ()


T = TypeVar("T")


class FactResolver:
    """Resolve effective facts using source authority while preserving evidence type."""

    def __init__(
        self,
        store: UserFactStore,
        audit: AuditSession | None = None,
        trust_mode: EvaluationTrustMode | None = None,
    ):
        self.store = store
        self.audit = audit
        # Deprecated compatibility input. v0.3.2 does not branch financial
        # semantics on a global trust mode.
        self.trust_mode = trust_mode

    def resolve(
        self,
        fact_type: str,
        effective_at: date,
        *,
        subject_person_ids: set[str] | None = None,
        required_semantic_type: FactSemanticType | None = None,
        acceptance_policy: FactAcceptancePolicy | None = None,
    ) -> FactResolution:
        subjects = subject_person_ids or {self.store.user_id}
        if self.audit is not None:
            self.audit.emit(
                "FACT_RESOLVER",
                AuditEventType.FACT_REQUESTED,
                entity_refs={"fact_type": fact_type},
                payload={
                    "effective_at": effective_at.isoformat(),
                    "subject_person_ids": sorted(subjects),
                    "required_semantic_type": (
                        required_semantic_type.value if required_semantic_type else None
                    ),
                    "legacy_trust_mode_ignored": (
                        self.trust_mode.value if self.trust_mode is not None else None
                    ),
                    "acceptance_policy": (
                        acceptance_policy.model_dump(mode="json")
                        if acceptance_policy is not None
                        else None
                    ),
                },
            )

        all_candidates = [
            fact
            for fact in self.store.active_facts
            if fact.fact_type == fact_type
            and fact.effective_subject_person_id in subjects
            and fact.is_effective_at(effective_at)
        ]
        legacy_future_action = (
            required_semantic_type == FactSemanticType.SELF_REPORTED_FACT
            and is_future_action_fact(fact_type)
        )
        semantic_candidates = [
            fact
            for fact in all_candidates
            if required_semantic_type is None
            or fact.semantic_type == required_semantic_type
            # Some normalized legacy conditions still declare an after-opening
            # action as a current self-reported fact.  The question layer writes
            # it as FUTURE_INTENT; accept that exact fact family without opening
            # future-intent acceptance to unrelated eligibility facts.
            or (
                legacy_future_action
                and fact.semantic_type == FactSemanticType.FUTURE_INTENT
            )
        ]
        acceptance = accept_records(
            semantic_candidates,
            policy=acceptance_policy,
            trust_mode=self.trust_mode,
            allow_future_intent=(
                required_semantic_type == FactSemanticType.FUTURE_INTENT
                or legacy_future_action
            ),
        )
        candidates = list(acceptance.accepted)
        rejected = list(acceptance.rejected)
        if self.audit is not None:
            self.audit.emit(
                "FACT_RESOLVER",
                AuditEventType.FACT_CANDIDATES_FOUND,
                entity_refs={"fact_type": fact_type},
                payload={
                    "candidate_count": len(candidates),
                    "rejected_semantic_count": len(all_candidates)
                    - len(semantic_candidates),
                    "rejected_authority_count": len(rejected),
                    "candidate_fact_ids": sorted(fact.fact_id for fact in candidates),
                    "rejected_fact_ids": sorted(fact.fact_id for fact in rejected),
                    "candidate_source_types": sorted(
                        {fact.source_type.value for fact in candidates}
                    ),
                    "verification_level": acceptance.verification_level.value,
                    "evidence_levels": sorted({level.value for level in acceptance.evidence_levels}),
                },
            )
        if not candidates:
            return FactResolution(
                status=ResolutionStatus.UNRESOLVED,
                rejected_facts=tuple(rejected),
                verification_level=acceptance.verification_level,
            )

        candidates.sort(
            key=lambda fact: (
                _SOURCE_PRIORITY[fact.source_type],
                fact.collected_at,
                fact.fact_id,
            ),
            reverse=True,
        )
        top_priority = _SOURCE_PRIORITY[candidates[0].source_type]
        top = [
            fact
            for fact in candidates
            if _SOURCE_PRIORITY[fact.source_type] == top_priority
        ]

        if len({self._canonical_value(fact.value) for fact in top}) > 1:
            if self.audit is not None:
                self.audit.emit(
                    "FACT_RESOLVER",
                    AuditEventType.FACT_CONFLICT_DETECTED,
                    entity_refs={"fact_type": fact_type},
                    input_data=[fact.model_dump(mode="json") for fact in top],
                    payload={
                        "competing_fact_ids": [fact.fact_id for fact in top],
                        "source_priority": top_priority,
                    },
                )
            return FactResolution(
                status=ResolutionStatus.CONFLICT,
                competing_facts=tuple(top),
                rejected_facts=tuple(rejected),
                verification_level=acceptance.verification_level,
            )

        selected = top[0]
        selected_level = verification_for_record(selected)
        if self.audit is not None:
            self.audit.emit(
                "FACT_RESOLVER",
                AuditEventType.FACT_SOURCE_SELECTED,
                entity_refs={
                    "fact_type": fact_type,
                    "fact_id": selected.fact_id,
                },
                input_data=[fact.model_dump(mode="json") for fact in candidates],
                output_data=selected,
                payload={
                    "selected_fact_id": selected.fact_id,
                    "source_type": selected.source_type.value,
                    "semantic_type": selected.semantic_type.value,
                    "verification_level": selected_level.value,
                    "is_self_reported": selected_level == VerificationLevel.SELF_REPORTED,
                    "competing_candidate_count": len(candidates) - 1,
                },
            )
        return FactResolution(
            status=ResolutionStatus.RESOLVED,
            fact=selected,
            competing_facts=tuple(candidates[1:]),
            rejected_facts=tuple(rejected),
            verification_level=selected_level,
        )

    def all_facts(
        self,
        fact_type: str,
        *,
        subject_person_ids: set[str] | None = None,
    ) -> list[UserFact]:
        subjects = subject_person_ids or {self.store.user_id}
        return [
            fact
            for fact in self.store.active_facts
            if fact.fact_type == fact_type
            and fact.effective_subject_person_id in subjects
        ]

    def accept_all(
        self,
        records: Iterable[Any],
        *,
        policy: FactAcceptancePolicy | None,
    ) -> AcceptanceResult:
        return accept_records(
            records,
            policy=policy,
            trust_mode=self.trust_mode,
        )

    def resolve_subjects(
        self,
        selector: FactSubjectSelector | None,
        effective_at: date,
    ) -> SubjectResolution:
        if selector is None or selector.mode == FactSubjectMode.STORE_USER:
            return SubjectResolution(
                status=ResolutionStatus.RESOLVED,
                person_ids=(self.store.user_id,),
            )

        if selector.mode == FactSubjectMode.EXPLICIT:
            assert selector.person_id is not None
            return SubjectResolution(
                status=ResolutionStatus.RESOLVED,
                person_ids=(selector.person_id,),
            )

        assert selector.mode == FactSubjectMode.RELATED_PERSON
        assert selector.relationship_type is not None
        anchor = selector.person_id or self.store.user_id
        candidates = [
            relationship
            for relationship in self.store.relationships
            if relationship.relationship_type == selector.relationship_type
            and relationship.is_effective_at(effective_at)
            and relationship.other_person(anchor) is not None
        ]
        allowed = set(selector.allowed_relationship_source_types)
        verified = [
            relationship
            for relationship in candidates
            if relationship.source_type in allowed
        ]
        unverified = [
            relationship
            for relationship in candidates
            if relationship.source_type not in allowed
        ]
        if not verified:
            return SubjectResolution(
                status=ResolutionStatus.UNRESOLVED,
                unverified_relationships=tuple(unverified),
            )

        person_ids = {
            other
            for relationship in verified
            if (other := relationship.other_person(anchor)) is not None
        }
        if selector.include_store_user:
            person_ids.add(anchor)
        return SubjectResolution(
            status=ResolutionStatus.RESOLVED,
            person_ids=tuple(sorted(person_ids)),
            relationships=tuple(sorted(verified, key=lambda item: item.relationship_id)),
            unverified_relationships=tuple(
                sorted(unverified, key=lambda item: item.relationship_id)
            ),
        )

    @staticmethod
    def missing_request(
        fact_type: str,
        spec: MissingFactSpec,
        requested_by_rule_id: str,
    ) -> MissingFactRequest:
        impact = None
        if spec.impact is not None:
            impact = MissingFactImpact(rate_pp=spec.impact.rate_pp)
        identity = "|".join(
            [
                fact_type,
                requested_by_rule_id,
                spec.expected_semantic_type.value if spec.expected_semantic_type else "",
                spec.action_id or "",
                spec.reward_id or "",
            ]
        )
        missing_fact_id = "MFR-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return MissingFactRequest(
            fact_type=fact_type,
            resolution_strategy=spec.resolution_strategy,
            impact=impact,
            required_source=spec.required_source,
            question=spec.question,
            requested_by_rule_id=requested_by_rule_id,
            expected_semantic_type=spec.expected_semantic_type,
            action_id=spec.action_id,
            reward_id=spec.reward_id,
            grounding_terms=spec.grounding_terms,
            missing_fact_id=missing_fact_id,
        )

    @staticmethod
    def _canonical_value(value: Any) -> str:
        return repr(value)
