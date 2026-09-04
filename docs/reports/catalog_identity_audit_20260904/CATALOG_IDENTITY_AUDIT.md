# Catalog identity candidate audit (2026-09-04)

- Active records: **4,306**
- Same institution/family pairs scanned: **9,562**
- Automatic candidates: **210**
- Automatic verdicts: `{'DISTINCT_VARIANT': 152, 'LIKELY_DUPLICATE_NEEDS_EVIDENCE': 58}`

## Methodology

Candidates require meaningful name/code/evidence/URL blocking within institution and family. Missing values never match. Variant tokens remain distinct. Automatic candidates and partial primary-source review are separate; no pair is confirmed without canonical issuer evidence.

## Partial primary-source review

Issuer pages were checked on 2026-09-04. Reviews remain conservative and recommend `NONE_PENDING_EVIDENCE` unless canonical identity is published.

## Manual priority review

| Pair | Verdict | Source | Evidence / unresolved |
|---|---|---|---|
| INST-KR-000830-1-0001 ↔ INST-KR-000830-1-C98752E9523 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/savings | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-1-0002 ↔ INST-KR-000830-1-CA7BF0C5E7B | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/26weeks | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-1-0003 ↔ INST-KR-000830-1-CD19BB23CD9 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/m1savings | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-1-0004 ↔ INST-KR-000830-1-CAAB9B11CC0 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/childsavings | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-1-0005 ↔ INST-KR-000830-1-C2AFF66ADCD | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/youthFutureSavings | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-2-0001 ↔ INST-KR-000830-2-C0A3C28D4F5 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/deposit | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-3-0004 ↔ INST-KR-000830-3-C90225C0EC4 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://www.kakaobank.com/products/safeboxes | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000830-3-0005 ↔ INST-KR-000830-3-C67C2196311 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.kakaobank.com/view/service; https://kakaobank.com/products/sohoVatBox | Issuer service index and product page identify the same named listing; canonical ID not public. / NONE_PENDING_EVIDENCE |
| INST-KR-000055-2-0001 ↔ INST-KR-000055-2-0004 | DISTINCT_VARIANT | https://www.acuonsb.co.kr/sv_pdt0010113.act | Official list separates 6M and 1-year rotation products. / NONE_PENDING_EVIDENCE |
| INST-KR-000055-2-0007 ↔ INST-KR-000055-2-CEF9405DC03 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.acuonsb.co.kr/sv_dpt0070110.act | Registry lists general deposit interest-method expressions; code identity remains unresolved. / NONE_PENDING_EVIDENCE |
| INST-KR-000408-2-0001 ↔ INST-KR-000408-2-C9F745A6513 ↔ INST-KR-000408-2-CD7A1D43C4E | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.hanwhasbank.com/ProdList_001.act?rnum=34 | One issuer page contains simple/compound methods; registry separates rows. / NONE_PENDING_EVIDENCE |
| INST-KR-000408-2-0003 ↔ INST-KR-000408-2-C77B5A42C75 ↔ INST-KR-000408-2-CCF9516A8D7 | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.hanwhasbank.com/ProdList_001.act?rnum=89 | 6M rotation issuer page contains simple/compound methods. / NONE_PENDING_EVIDENCE |
| INST-KR-000408-2-0004 ↔ INST-KR-000408-2-C6470067B1C ↔ INST-KR-000408-2-CB40DE9089C | LIKELY_DUPLICATE_NEEDS_EVIDENCE | https://www.hanwhasbank.com/ProdList_001.act?rnum=92 | 12M rotation issuer page contains simple/compound methods. / NONE_PENDING_EVIDENCE |
| INST-KR-000408-2-0010 ↔ INST-KR-000408-2-0011 ↔ INST-KR-000408-2-0012 ↔ INST-KR-000408-2-0013 ↔ INST-KR-000408-2-0014 | NOT_DUPLICATE | https://www.hanwhasbank.com/ProdList_001.act?rnum=40 | HS numbered products are separately listed by product/term. / NONE_PENDING_EVIDENCE |

## Coverage observations

- `INST-KR-000408-2-0002` (Hanwha 3M) has no hash sibling candidate; issuer observation is recorded separately.

## Auto-candidate appendix

Full automatic candidates are in `candidates.json`; non-candidate pair count is the scan total minus retained candidates.
