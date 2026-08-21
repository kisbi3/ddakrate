# Financial Eligibility Engine v0.2 — Change Log

## Source basis

Implementation precedence:

1. `금융상품 규칙 분석.txt`
2. `Financial_Eligibility_Engine_MVP_Technical_Design_v0.1.md`
3. v0.1 code
4. `금융상품 기술검증.txt`

The v0.1 deterministic architecture was retained. New primitives or schemas were added only where the reviewed product fixtures required them.

## Added

### DSL

- `COUNT_CONSECUTIVE`
- `COUNT_DISTINCT_PERIODS(DAY|WEEK|MONTH)`
- `COUNT_DISTINCT_MONTHS` backward-compatible alias
- `FactSubjectSelector`
- structured `CoverageRequirement`

### Fact/Data schemas

- `PersonRelationship`
- `ScheduledOccurrence`
- `DataCoverage`
- `AccountLifecycleEvent`
- `NormalizedMonetaryAmount`
- `subject_person_id` and `related_person_id`
- generic `ContractTerm`
- structured `InstitutionServiceDefinition`

### Fixtures

- KakaoBank 26-week savings
- IBK parent benefit savings
- Hana run savings

### Visualization

- Evaluation Trace to Mermaid
- User Fact Store to Mermaid
- Product Rule AST to Mermaid

### Generated artifacts

- v0.2 JSON Schemas
- product evaluation examples
- service definition examples
- Mermaid `.mmd` and demo Markdown
- schema freeze and validation reports

## Changed

- `RateSummary.conditional_upper_rate` serialized meaning was made explicit as `user_specific_conditional_upper_rate`.
- `NOT_EXISTS` can require time-range coverage before proving absence.
- `FactResolver` can select verified related-person facts.
- period aggregation is generic internally.
- evaluation traces carry AST `rule_type` for rendering.
- CLI supports all four regression products and Mermaid generation.

## Preserved

- v0.1 Shinhan fixture and tests
- four-state evaluation model
- logical and temporal semantics
- rate/interest engine separation
- legacy v0.1 JSON Schema files
- legacy coverage boolean path for migration compatibility

## Deferred

- Account lifecycle normalization resolver
- FX normalization resolver
- real-time pending schedule semantics
- PDF/LLM rule extraction
- production database and Web UI
