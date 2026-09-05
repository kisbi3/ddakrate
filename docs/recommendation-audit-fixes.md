# Recommendation audit fixes (base 7b76c6f)

This change addresses R1, R2, R3, R4 and R10 from the recommendation audit.

- R1: publish Kakao deposit v006 with the source-backed continuous 1–36 month domain. Previous releases and all rate/source data remain unchanged. Run `python scripts/publish_recommendation_term_correction.py` to reproduce the publication from the specified previous manifest.
- R2: resolve MINIMUM/MAXIMUM within the product's discrete or continuous domain, and report valid bound selections as `WITHIN_BOUNDS` rather than a mismatch.
- R3: use the minimum realizable score of *all* selected products in both Top-K stability and the question frontier. Possible-max order is not lower-bound order. This is deliberately conservative; suitability-aware intervals remain a separate issue.
- R4: share deterministic target grounding between the prompt projection and the operation validator, including legacy/pre-search executor paths and institution AnswerPlans. Generic product names, ambiguous short brand words and shared ranks do not authorize arbitrary products. Preserve unique active-question, previous-choice and explicit name/ID references. Do not expose the full catalog ID allowlist in prompts.
- R10: recommendations and next-question GETs do not advance, reopen or recompute business state. The explicit legacy `complete=True` command remains supported. Audit events are separate from business state.

## Verification

`tests/test_recommendation_audit_regressions.py` imports original runtime modules and includes HTTP read invariance tests. The initial 20-test subset reproduced 16 failures and 4 passes on the base commit; all 20 passed after the initial fix. Further compatibility and boundary tests are included in the final CI run.

The review container has a 4 GiB memory limit; a concurrent baseline/full-catalog run was killed with exit 137. Full-suite validation is also performed in GitHub Actions using the project's Python 3.11 environment. No test result is inferred from static inspection.

## Deliberately out of scope

This is not a claim that every audit finding is fixed. R5 (rate layer ceilings), R6 (ranking/interval comparison policy), R7 (quantitative question parsing), R8 (EXACT day tolerance), R9 (noncandidate detail rank), individual tax/compound-interest calculations and concurrency require separate changes. Other catalog representative/domain inconsistencies must be reviewed against their source clauses rather than bulk-coerced into ranges.
