#!/usr/bin/env python3
"""Reproducible read-only duplicate candidate audit."""
from __future__ import annotations
import difflib, hashlib, json, re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; NORM=ROOT/"data/financial_products/normalized"; OUT=ROOT/"docs/reports/catalog_identity_audit_20260904"; INDEX=NORM/"index.json"
VARIANT=re.compile(r"(?:\d+\s*(?:개월|m)|단리식|복리식|mmf형|mmw형|rp형|단리|복리|법인|개인|mmf|mmw|rp|isa|퇴직연금|\d+호)",re.I)
def known(v:Any)->bool:return v is not None and v not in ("",[],{},())
def name_parts(v:Any):
 r=str(v or "").lower(); toks=tuple(sorted(set(x.replace(" ","") for x in VARIANT.findall(r)))); b=re.sub(r"[^0-9a-z가-힣]","",VARIANT.sub(" ",r)); return b.replace("카카오뱅크","").replace("한화","").replace("애큐온", ""),toks
def fp(p):
 t=p.get("term_policy") or {}; terms=(t.get("min_value"),t.get("max_value"),tuple(t.get("allowed_values") or [])); terms=terms if any(known(x) for x in terms) else None
 ch=tuple(sorted((p.get("sale_policy") or {}).get("subscription_channels") or [])) or None; rates=[]
 for e in (p.get("return_policy") or {}).get("rate_entries") or []:
  c=e.get("calculation") or {}; applies=tuple(sorted(json.dumps(x,ensure_ascii=False,sort_keys=True) for x in e.get("applies_to") or []))
  if e.get("role") and known(c.get("value")): rates.append((e["role"],c.get("type"),str(c["value"]),c.get("unit"),applies))
 return {"terms":terms,"channels":ch,"rates":tuple(sorted(rates)) or None,"refs":tuple(sorted(str(x) for x in p.get("source_ref_ids") or [])) or None,"url":(p.get("official_site_link") or {}).get("url")}
fingerprint=fp
def load(row):
 p=json.loads((ROOT/row["path"]).read_text(encoding="utf-8")); b,t=name_parts(p.get("name")); return {"product_code":row["product_code"],"name":p.get("name"),"path":row["path"],"name_base":b,"variant_tokens":t,"institution_id":row["institution_id"],"product_family":row["product_family"],"fingerprint":fp(p)}
def compare(a,b):
 fa,fb=a["fingerprint"],b["fingerprint"]; sim=difflib.SequenceMatcher(None,a["name_base"],b["name_base"]).ratio(); same=a["name_base"] and a["name_base"]==b["name_base"]; terms=known(fa["terms"]) and known(fb["terms"]) and fa["terms"]==fb["terms"]; rates=known(fa["rates"]) and known(fb["rates"]) and fa["rates"]==fb["rates"]; refs=sorted(set(fa["refs"] or ())&set(fb["refs"] or ())); url=known(fa["url"]) and fa["url"]==fb["url"]
 if not (url or refs or (same and (terms or rates)) or (sim>=.88 and terms and rates)): return None
 return {"left":{k:a[k] for k in ("product_code","name","path","variant_tokens")},"right":{k:b[k] for k in ("product_code","name","path","variant_tokens")},"signals":{"same_known_terms":terms,"same_known_rates":rates,"shared_evidence_refs":refs,"same_official_url":url,"name_similarity":round(sim,3)},"verdict":"DISTINCT_VARIANT" if a["variant_tokens"]!=b["variant_tokens"] else "LIKELY_DUPLICATE_NEEDS_EVIDENCE"}
def manual():
 def row(pair,verdict,source_urls,note): return {"pair":pair,"verdict":verdict,"source_urls":source_urls,"checked_at":"2026-09-04","evidence_note":note,"unresolved_point":"canonical product ID mapping requires issuer confirmation","recommended_canonical":"NONE_PENDING_EVIDENCE"}
 kak=[("INST-KR-000830-1-0001","INST-KR-000830-1-C98752E9523","https://www.kakaobank.com/products/savings"),("INST-KR-000830-1-0002","INST-KR-000830-1-CA7BF0C5E7B","https://www.kakaobank.com/products/26weeks"),("INST-KR-000830-1-0003","INST-KR-000830-1-CD19BB23CD9","https://www.kakaobank.com/products/m1savings"),("INST-KR-000830-1-0004","INST-KR-000830-1-CAAB9B11CC0","https://www.kakaobank.com/products/childsavings"),("INST-KR-000830-1-0005","INST-KR-000830-1-C2AFF66ADCD","https://www.kakaobank.com/products/youthFutureSavings"),("INST-KR-000830-2-0001","INST-KR-000830-2-C0A3C28D4F5","https://www.kakaobank.com/products/deposit"),("INST-KR-000830-3-0004","INST-KR-000830-3-C90225C0EC4","https://www.kakaobank.com/products/safeboxes"),("INST-KR-000830-3-0005","INST-KR-000830-3-C67C2196311","https://kakaobank.com/products/sohoVatBox")]
 reviews=[row(list(x[:2]),"LIKELY_DUPLICATE_NEEDS_EVIDENCE",["https://www.kakaobank.com/view/service",x[2]],"Issuer service index and product page identify the same named listing; canonical ID not public.") for x in kak]
 reviews += [row(["INST-KR-000055-2-0001","INST-KR-000055-2-0004"],"DISTINCT_VARIANT",["https://www.acuonsb.co.kr/sv_pdt0010113.act"],"Official list separates 6M and 1-year rotation products."),row(["INST-KR-000055-2-0007","INST-KR-000055-2-CEF9405DC03"],"LIKELY_DUPLICATE_NEEDS_EVIDENCE",["https://www.acuonsb.co.kr/sv_dpt0070110.act"],"Registry lists general deposit interest-method expressions; code identity remains unresolved."),row(["INST-KR-000408-2-0001","INST-KR-000408-2-C9F745A6513","INST-KR-000408-2-CD7A1D43C4E"],"LIKELY_DUPLICATE_NEEDS_EVIDENCE",["https://www.hanwhasbank.com/ProdList_001.act?rnum=34"],"One issuer page contains simple/compound methods; registry separates rows."),row(["INST-KR-000408-2-0003","INST-KR-000408-2-C77B5A42C75","INST-KR-000408-2-CCF9516A8D7"],"LIKELY_DUPLICATE_NEEDS_EVIDENCE",["https://www.hanwhasbank.com/ProdList_001.act?rnum=89"],"6M rotation issuer page contains simple/compound methods."),row(["INST-KR-000408-2-0004","INST-KR-000408-2-C6470067B1C","INST-KR-000408-2-CB40DE9089C"],"LIKELY_DUPLICATE_NEEDS_EVIDENCE",["https://www.hanwhasbank.com/ProdList_001.act?rnum=92"],"12M rotation issuer page contains simple/compound methods."),row(["INST-KR-000408-2-0010","INST-KR-000408-2-0011","INST-KR-000408-2-0012","INST-KR-000408-2-0013","INST-KR-000408-2-0014"],"NOT_DUPLICATE",["https://www.hanwhasbank.com/ProdList_001.act?rnum=40"],"HS numbered products are separately listed by product/term.")]
 return {"status":"PARTIAL_PRIMARY_SOURCE_REVIEW","policy":"Primary issuer pages only; no deletion/merge/alias; confirmation requires canonical ID evidence.","reviews":reviews,"coverage_observations":[{"pair":["INST-KR-000408-2-0002"],"status":"NO_SIBLING_CANDIDATE","source_urls":["https://www.hanwhasbank.com/ProdList_001.act?rnum=88"],"checked_at":"2026-09-04","evidence_note":"3M issuer page observed; no matching hash sibling in catalog."}]}
def run():
 idx=json.loads(INDEX.read_text()); rows=[load(x) for x in idx["products"]]; groups=defaultdict(list)
 for r in rows:groups[(r["institution_id"],r["product_family"])].append(r)
 candidates=[]; total=0
 for key,ms in sorted(groups.items()):
  for i,a in enumerate(ms):
   for b in ms[i+1:]: total+=1; c=compare(a,b); c and (c.update({"group":{"institution_id":key[0],"product_family":key[1]}}),candidates.append(c))
 result={"audit":{"index_sha256":"sha256:"+hashlib.sha256(INDEX.read_bytes()).hexdigest(),"product_count":len(rows),"same_group_pair_count":total,"candidate_pair_count":len(candidates),"priority_candidate_pair_count":sum(c["group"]["institution_id"] in {"INST-KR-000830","INST-KR-000055","INST-KR-000408"} for c in candidates)},"verdict_counts":dict(sorted(Counter(c["verdict"] for c in candidates).items())),"manual_review":manual(),"candidates":candidates}
 OUT.mkdir(parents=True,exist_ok=True); (OUT/"candidates.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 lines=["# Catalog identity candidate audit (2026-09-04)","",f"- Active records: **{len(rows):,}**",f"- Same institution/family pairs scanned: **{total:,}**",f"- Automatic candidates: **{len(candidates):,}**",f"- Automatic verdicts: `{result['verdict_counts']}`","","## Methodology","","Candidates require meaningful name/code/evidence/URL blocking within institution and family. Missing values never match. Variant tokens remain distinct. Automatic candidates and partial primary-source review are separate; no pair is confirmed without canonical issuer evidence.","","## Partial primary-source review","", "Issuer pages were checked on 2026-09-04. Reviews remain conservative and recommend `NONE_PENDING_EVIDENCE` unless canonical identity is published.","","## Manual priority review","","| Pair | Verdict | Source | Evidence / unresolved |", "|---|---|---|---|"]
 for x in result["manual_review"]["reviews"]: lines.append(f"| {' ↔ '.join(x['pair'])} | {x['verdict']} | {'; '.join(x['source_urls'])} | {x['evidence_note']} / {x['recommended_canonical']} |")
 lines += ["", "## Coverage observations", "", "- `INST-KR-000408-2-0002` (Hanwha 3M) has no hash sibling candidate; issuer observation is recorded separately.","", "## Auto-candidate appendix", "", "Full automatic candidates are in `candidates.json`; non-candidate pair count is the scan total minus retained candidates."]
 (OUT/"CATALOG_IDENTITY_AUDIT.md").write_text("\n".join(lines)+"\n",encoding="utf-8"); return result
if __name__=="__main__": print(json.dumps(run()["audit"],ensure_ascii=False,indent=2))
