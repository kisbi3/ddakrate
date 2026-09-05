# Kakao deposit term-domain correction

The active v005 record already preserves the official 1–36 month range, selectable by month/day. Its stale DISCRETE rate-table breakpoints contradicted that range and rejected EXACT 12/24 month searches. v006 restores RANGE, preserves all rate/source/identity data, and leaves v005 immutable. The new manifest inherits registries and refreshes active product/index hashes.
