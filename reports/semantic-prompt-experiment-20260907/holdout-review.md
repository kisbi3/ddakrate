# Held-out manual review (2026-09-07)

Reviewed baseline-23, v2-23, baseline-45, v2-45 raw_text.json, accepted.json and diagnostics.json against local catalog originals. No API calls or runtime edits by reviewer. Cases 2–5 were held out from prompt-specific examples. This is a small manual assessment, not a statistically representative pass rate.

## Findings

| Case | Baseline raw extraction | V2 raw extraction | Accepted result / assessment |
|---|---|---|---|
| WithUs first transaction | Understands bank/product scope and preceding year, but uses unsupported PRODUCT_HOLDING kind, EQUALS comparator, Korean subject/period enums. | Substantially better: ABSENCE_HISTORY, correct bank scope, deposits/savings including subscription, one YEAR and subscription-day anchor quotes. No whole-bank scope expansion. AS_OF remains an imprecise normalized anchor; source quotes preserve subscription date. | Both UNKNOWN. V2 is strict-schema valid but boolean expected=true is rejected by numeric grounding bug. Raw semantic extraction is substantially successful; runtime integration fails. |
| Shinhan child tier | Recognizes 2007 filter, minimum two children and official approval; loses 3-child tier as a distinct condition, uses invalid enums. | Distinguishes EQ 2 and GTE 3 under ANY and keeps minor qualifier as UNKNOWN, avoiding invented age18. | All meaningful leaves become UNKNOWN: scope.institution=null violates schema. Second ALL source_quote splices noncontiguous text and is replaced by UNKNOWN. Approval deadline is not encoded. Tier relations list only one rule ID, so are invalid (schema requires >=2); cannot represent within-one-rule tier linkage. Partial raw improvement, not full successful tier extraction. |
| Shinhan lifeevent | Correct ALL(ANY(marriage,pregnancy,infertility,childbirth), approval window). Uses invalid EXISTS/EQUALS/variable/basis values. | Retains four OR paths and approval time window, fixes several enums. Infertility/childbirth have op=null, not UNKNOWN. Marriage uses MARRIAGE_DATE as boolean predicate. | All leaves UNKNOWN, including strict-schema-valid bool predicates due numeric grounding bug. OR paths are safely retained. Source does not explicitly identify event subject, so SELF/PREGNANT_SELF is an assumption to revisit; cannot infer spouse paths absent policy. Meaning partially preserved in raw structure, not usable typed result. |
| Naver marketing | Entire conjunction collapsed into one predicate; invalid variable/comparator; invents '은행 동의 기록' confirmation basis. | Valid CONSENT enum but still one predicate holding compound literal rather than two ALL leaves. Adds official_confirmation_required=true / SOURCE_TEXT although this short source does not state bank approval. | UNKNOWN; boolean bug and unsupported source-based confirmation both matter. V2 is not full semantic success: lexical conjunction retained but independently evaluable structure absent and authority overclaimed. |

## Cross-cutting problems

1. **Boolean rejected as number:** `_leaf_is_grounded` does `isinstance(node.expected, int)`. Python bool is int, so true/false is added to numeric_values and later compared as 'True'/'False' to regex digit tokens. No ordinary source can satisfy this. This is a validator bug, not AI inability to read Korean. It explains multiple v2 GROUNDING_REJECTED records.
2. Baseline uses enum values absent from strict schema. V2 improves enum adherence markedly, but null enums still occur because transport schema allows them.
3. Every relation in these held-out outputs is a one-rule relation and gets dropped. INDEPENDENT_ADD with one rule is unsupported inference; within-source child tiers need a representation that does not invent multiple canonical IDs.
4. v2 quote construction can concatenate disjoint spans; validator correctly refuses a false verbatim quote. Multi-span evidence support or explicit contiguous quote instructions would help.
5. Parent logical nodes' official flags are discarded during coercion; therefore retaining a raw top-level flag does not mean official requirement reached the engine.
6. None of the four cases yields a usable fully extracted condition through current runtime. This must not be described as no raw extraction progress: v2 firsttx and lifeevent skeleton clearly show better model reading that validation discards.

## Next experiment recommendation

Keep production OFF. First isolate bool/int grounding with a small offline reproduction and fix deliberately under parent scope if authorized; replay saved raw outputs without new API calls to quantify recovered structure. Separately improve schema contract examples/null handling, require distinct consent leaves and do not infer official authority. Do not turn lexical pattern acceptance into semantic correctness or weaken source provenance merely to raise accepted leaf counts.
