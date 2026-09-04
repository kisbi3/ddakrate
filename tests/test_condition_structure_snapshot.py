from eligibility.catalog.normalized_loader import load_normalized_product_catalog


# NOTE on deletions (see git history for the removed test bodies):
#
# - test_condition_snapshot_covers_every_published_product_and_keeps_text_only_safe
#   pinned data/financial_products/normalized/condition_snapshots/eligibility_preferential_20260828.json,
#   which no longer exists. The condition snapshot the catalog actually loads today
#   (resolved via the active manifest's "condition_snapshot_file", currently
#   20260831-installment-audited-01.json) is *deliberately* supplemental and partial:
#   per src/eligibility/catalog/normalized_loader.py's _load_condition_snapshots
#   docstring, contract rules/rate entries -- not the condition snapshot -- remain the
#   sole input to eligibility calculation, and the file is published incrementally per
#   audited shard. As of this data, it covers 4,012 of 4,289 active products, so the
#   "covers every published product" invariant no longer holds by design and cannot be
#   restored by pointing at a different reference file. Deleted rather than weakened.
#
# - test_known_high_rate_savings_keep_structured_conditions_separate_from_eligibility
#   asserted on condition_snapshot["eligibility"]["constraints"], a structured
#   constraint list. The rebuilt snapshot schema (see condition_record() in
#   scripts/migrations/publish_installment_audit_20260831.py) only emits
#   eligibility.{status,display_text,source_ref_ids} -- the structured "constraints"
#   field was removed entirely, so this invariant has no equivalent field left to check
#   in the condition snapshot. Deleted rather than weakened.
#
# - test_display_only_conditions_are_distinguished_from_partially_linked_conditions
#   asserted a preferential_rate.status of "DISCLOSED_ONLY", distinct from
#   "PARTIALLY_STRUCTURED"/"STRUCTURED_LINKED". The rebuilt schema collapsed
#   preferential_rate.status to a plain binary: "STRUCTURED" (conditions present) or
#   "NO_CONDITIONS_DISCLOSED" (none) -- see condition_record() in the same migration
#   script. The "display-only vs partially-linked" distinction this test existed to
#   protect no longer exists in the data model, so there is nothing left to assert.
#   Deleted rather than weakened.


def test_term_rate_differences_are_not_misrepresented_as_preferential_conditions():
    products = {product.product_id: product for product in load_normalized_product_catalog()}
    term_tiered = products["INST-KR-000005-1-CDC5C03A063"].normalized.condition_snapshot
    assert term_tiered["preferential_rate"]["status"] == "NO_CONDITIONS_DISCLOSED"
    assert term_tiered["preferential_rate"]["conditions"] == []
