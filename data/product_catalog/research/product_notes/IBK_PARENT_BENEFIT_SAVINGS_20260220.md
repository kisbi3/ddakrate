# IBK부모급여우대적금

- product_id: `IBK_PARENT_BENEFIT_SAVINGS_20260220`
- institution: `IBK_BANK`
- quality: **B**
- complexity: COMPLEX
- rate: 2.5% → 6.5% (cap 4.0%p)
- source: https://mybank.ibk.co.kr/uib/jsp/guest/ntr/ntr70/ntr7010/PNTR701000_i2.jsp?grcd=21&lncd=01&pdcd=0124&tmcd=121
- validation: PASS

## Dependencies
- resolver: RELATED_PERSON_RESOLVER, GOVERNMENT_BENEFIT_RESOLVER, CERTIFICATE_RESOLVER
- service: IBK_FAMILY_PERFORMANCE_REGISTRATION
- user fact/future intent: none

## Warnings
- Golden regression fixture preserved verbatim; semantics cross-checked against current official source.