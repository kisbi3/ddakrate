# 손님케어 적금

- product_id: `HANA_CUSTOMER_CARE_SAVINGS_2026`
- institution: `HANA_BANK`
- quality: **B**
- complexity: MEDIUM
- rate: 3.3% → 4.7% (cap 1.4%p)
- source: https://www.hanabank.com/cont/mall/mall08/mall0801/mall080102/1470313_115157.jsp
- validation: PASS

## Dependencies
- resolver: AUTO_TRANSFER_RESOLVER, ELIGIBILITY_PROFILE_RESOLVER
- service: HANA_MARKETING_CONSENT_SERVICE, HANA_VALUED_CUSTOMER_SERVICE
- user fact/future intent: none

## Warnings
- none