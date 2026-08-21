from __future__ import annotations

from eligibility.fixtures.future_goals import salary_envelope_6m_product
from eligibility.ingestion.knowledge_draft import (
    DraftProvenance,
    ExtractionMetadata,
    ProductKnowledgeDraft,
    ProductMetadataDraft,
    RequiredFactDraft,
    RewardDraft,
    RuleDraft,
    ServiceReference,
    UnresolvedItem,
)
from eligibility.schema.enums import (
    AchievementMode,
    EvaluationPhase,
    FactSemanticType,
    RulePurpose,
    TermUnit,
)


def salary_envelope_document_sections_payload() -> list[dict[str, object]]:
    return [
        {
            "document_id": "DOC-SHINHAN-YOUTH-20260722",
            "document_name": "신한은행 청년 처음적금 상품설명서",
            "page": 2,
            "section": "[1] 주거래 우대",
            "text": "상품 신규 후 인정기간 중 급여클럽 월급봉투를 6개월 이상 받은 경우 연 1.0%p 우대",
            "source_url": "https://example.invalid/shinhan-youth-first.pdf",
        }
    ]


def salary_envelope_extraction_draft() -> ProductKnowledgeDraft:
    product = salary_envelope_6m_product()
    eligibility = product.eligibility_rule
    rate = product.preferential_rules[0]
    provenance = DraftProvenance(
        document_id="DOC-SHINHAN-YOUTH-20260722",
        document_name="신한은행 청년 처음적금 상품설명서",
        page=2,
        section="[1] 주거래 우대",
        source_url="https://example.invalid/shinhan-youth-first.pdf",
        source_text=(
            "상품 신규 후 인정기간 중 급여클럽 월급봉투를 6개월 이상 받은 경우 연 1.0%p 우대"
        ),
    )
    return ProductKnowledgeDraft(
        document_id="DOC-SHINHAN-YOUTH-20260722",
        product_metadata=ProductMetadataDraft(
            product_id=product.product_id,
            institution_id=product.institution_id,
            name=product.name,
            product_type=product.product_type,
            contract_value=12,
            contract_unit=TermUnit.MONTH,
            base_rate=product.base_rate,
            advertised_max_rate=product.advertised_max_rate,
            preferential_rate_cap=product.preferential_rate_cap,
        ),
        rules=[
            RuleDraft(
                rule_id=eligibility.rule_id,
                name=eligibility.name,
                purpose=RulePurpose.ELIGIBILITY,
                evaluation_phase=EvaluationPhase.PRE_SUBSCRIPTION,
                ast=eligibility.model_dump(mode="json"),
                provenance=[provenance],
                extraction_confidence=0.99,
            ),
            RuleDraft(
                rule_id=rate.rule.rule_id,
                name=rate.rule.name,
                purpose=RulePurpose.PREFERENTIAL_RATE,
                evaluation_phase=EvaluationPhase.POST_SUBSCRIPTION,
                achievement_mode=AchievementMode.ACCUMULATIVE,
                ast=rate.rule.model_dump(mode="json"),
                reward=RewardDraft(
                    type=rate.reward.type,
                    value=rate.reward.value,
                    unit=rate.reward.unit,
                ),
                provenance=[provenance],
                extraction_confidence=0.97,
            ),
        ],
        service_references=[
            ServiceReference(
                institution_id="SHINHAN_BANK",
                service_name="급여클럽",
                concept_name="월급봉투",
                reference_type="REQUIRES_SERVICE_DEFINITION",
                used_by_rule_ids=[rate.rule.rule_id],
                provenance=[provenance],
            )
        ],
        required_facts=[
            RequiredFactDraft(
                fact_type="SHINHAN_GOLDEN_ELIGIBLE",
                semantic_type=FactSemanticType.OBSERVED_FACT,
                authority_hint="INSTITUTION_OR_MYDATA",
                used_by_rule_ids=[eligibility.rule_id],
            ),
            RequiredFactDraft(
                fact_type="SHINHAN_SALARY_CLUB_ENVELOPE_RECEIVED",
                semantic_type=FactSemanticType.OBSERVED_EVENT,
                authority_hint="INSTITUTION_SERVICE",
                used_by_rule_ids=[rate.rule.rule_id],
            ),
            RequiredFactDraft(
                fact_type="WILL_TRACK_SHINHAN_SALARY_ENVELOPE_6M",
                semantic_type=FactSemanticType.FUTURE_INTENT,
                authority_hint="USER_DECLARED",
                used_by_rule_ids=[rate.rule.rule_id],
            ),
        ],
        unresolved_items=[
            UnresolvedItem(
                issue_type="EXTERNAL_DEFINITION_REQUIRED",
                description="월급봉투 인정정의는 급여클럽 Service Definition이 필요함",
                related_rule_ids=[rate.rule.rule_id],
                provenance=[provenance],
            )
        ],
        extraction_metadata=ExtractionMetadata(
            extractor_profile="RULE_EXTRACTION",
            prompt_template_id="financial-rule-extraction",
            prompt_template_version="0.3.1",
            response_schema_version="0.3.1",
            provider="MOCK",
            model="mock-model",
        ),
    )
