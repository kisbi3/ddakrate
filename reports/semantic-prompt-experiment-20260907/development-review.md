# Independent review: v2 development cases

Reviewed input.json, parsed.json, accepted.json for v2-01 and v2-01-repeat. No API calls or production edits.

## Confirmed failures

1. **Repeat card identity corruption rejects an otherwise substantially structured extraction.** Input source_hash is `c5d40b70ddfcb64646ff2d6920418477bfddaa863e86c4a72525a1faf988b978`; repeated output inserts an extra `b` (`c5d40bb70...`). Card clause is wholly absent from accepted.json. This is an identifier-copy error, not failed Korean interpretation. Do not silently rebind identities to make this pass; consider immutable short request IDs with server-owned hashes in a later transport redesign.

2. **Repeat salary has a materially wrong accepted Boolean tree.** Raw and accepted both have `ANY(salary half-term ratio, salary amount/window, merchant settlement)`. The 50만원 definition and half-term requirement are not alternatives. A single qualifying salary deposit could satisfy the amount node while the half-term condition is false, yet this ANY would evaluate true if transactional facts were available. Merchant branch also loses the half-term qualification. Correct interpretation must attach the definition and temporal requirement to the relevant fulfillment paths; splitting them as sibling OR leaves is incorrect. Exact temporal counting semantics should be preserved rather than inferred from the compact source.

3. **First v2 salary raw extraction is richer than accepted, but invents SOURCE_TEXT confirmation.** It extracts 50만원, 5/10 offsets, 1/2 ratio, merchant count and network list. Both leaves add official_confirmation_required=true and official_confirmation_basis=SOURCE_TEXT even though the clause does not mention approval/documents. Input catalog metadata has requires_official_confirmation=true, a different provenance. Grounding rejects both leaves to UNKNOWN. Production should keep catalog policy separate from the model's source-text claim; reporting must distinguish this validation rejection from extraction omission.

## Improvements and remaining limitations

First v2 card retains ANY of 3 ALL(term, amount), EQ/LTE term comparators, amount literals/units, institution scope and actual aggregation start/end quotes. Six PREDICATE leaves survive. This is a substantial improvement over baseline.

Both v2 calls still use period.unit=null for 5/10 salary offsets. Business-day wording exists in quotes but is not structurally encoded. Card aggregation basis is UNSPECIFIED; start/end quotes do preserve evidence, but full executable temporal semantics are not established.

Logical-node metadata (existing_rule_id and official flags) is silently discarded by coerce_expression. This does not prove the raw response was strict-schema-valid. In first v2, unsupported SOURCE_TEXT official flags survive on salary leaves until grounding rejects them, while root flags are dropped without a diagnostic equivalent.

Overall: do not call either run end-to-end extraction success. v2-01 improves card structure but loses salary leaves; repeat loses card identity and accepts a wrong salary OR structure. PREDICATE counts alone would mis-score these cases.
