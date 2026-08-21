# financial-product-catalog-v0.1

2026-08-20 기준 Financial Eligibility Engine v0.4.6용 **50-product executable installment-savings catalog**.

- 50 strict `ProductDefinition` JSON
- 10 institutions
- 70 discovery candidates (50 selected + 20 rejected/backlog)
- 4 golden fixtures preserved verbatim
- official-source sidecar provenance / resolver / service / schema-gap manifests
- `catalog_summary.csv`
- catalog build baseline tests **13/13 PASS** / baseline integrated regression **382/382 PASS**
- `web-llm1` integration tests **16/16 PASS** / full integrated regression **390/390 PASS**
- 50-product `ApplicationService` → Top 5 smoke **PASS**
- no product-specific Search/Ranking/Application branches
- Kakao 26-week late-streak semantics: `from_sequence=None`

**Final judgment: `READY_WITH_LIMITATIONS`.**

모든 50개는 load/validate/evaluate 가능하지만 conservative quality policy상 B-grade다. resolver/service dependency 또는 coarse eligibility fact가 남아 있으므로 leaf-rule data가 완전히 닫혔다고 과장하지 않는다. Catalog build 상세는 `reports/CATALOG_50_IMPLEMENTATION_REPORT.md`, 현재 Web+LLM 통합 결과는 `docs/reports/web/WEB_LLM1_CATALOG50_INTEGRATION_REPORT.md`를 참고한다.
