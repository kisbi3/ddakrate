import json
import subprocess
import sys
from pathlib import Path
from scripts.audit_catalog_identity_candidates_20260904 import compare, fingerprint, name_parts

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_catalog_identity_candidates_20260904.py"

def test_identity_audit_is_deterministic_and_bounded() -> None:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True, capture_output=True)
    out = ROOT / "docs/reports/catalog_identity_audit_20260904/candidates.json"
    first = out.read_bytes()
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True, capture_output=True)
    assert out.read_bytes() == first
    payload = json.loads(first)
    assert payload["audit"]["product_count"] == 4306
    assert payload["audit"]["candidate_pair_count"] < payload["audit"]["same_group_pair_count"]
    assert out.stat().st_size < 2_000_000

def test_identity_audit_preserves_variant_and_missing_value_guards() -> None:
    payload = json.loads((ROOT / "docs/reports/catalog_identity_audit_20260904/candidates.json").read_text())
    assert all(c["signals"]["same_known_terms"] or c["signals"]["same_known_rates"] or c["signals"]["shared_evidence_refs"] or c["signals"]["same_official_url"] for c in payload["candidates"])
    assert payload["manual_review"]["status"] == "PARTIAL_PRIMARY_SOURCE_REVIEW"
    assert "official_confirmation_performed" not in payload["manual_review"]

def item(code, name, terms=None, rates=None):
    base, variants = name_parts(name)
    return {"product_code": code, "name": name, "path": "", "variant_tokens": variants, "name_base": base, "fingerprint": fingerprint({"name": name, "term_policy": terms or {}, "return_policy": {"rate_entries": rates or []}, "sale_policy": {}})}

def test_same_prefix_unrelated_and_missing_are_not_candidates() -> None:
    a = item("INST-KR-000408-2-0001", "e뱅킹 정기예금")
    b = item("INST-KR-000408-2-0002", "한화 e-회전 3개월 정기예금")
    assert compare(a, b) is None
    missing = compare(item("A", "정기예금"), item("B", "정기예금"))
    assert missing is None
    missing_fp = fingerprint({"term_policy": {}, "return_policy": {}})
    assert missing_fp["terms"] is None
    assert missing_fp["rates"] is None
    known_terms_missing_rates = compare(
        item("C", "정기예금", {"min_value": 1}, []),
        item("D", "정기예금", {"min_value": 1}, []),
    )
    assert known_terms_missing_rates is not None
    assert known_terms_missing_rates["signals"]["same_known_rates"] is False

def test_variant_tokens_survive_parentheses_and_known_kakao_pairs_detect() -> None:
    _, tokens = name_parts("한화 e-회전(6M) 정기예금 복리식 법인 MMW형")
    assert {"6m", "복리식", "법인", "mmw형"} <= set(tokens)
    rates = [{"role": "BASE", "calculation": {"value": "2", "type": "FIXED", "unit": "PERCENT"}}]
    a = item("A", "정기예금 (6M) 단리식", {"min_value": 1, "max_value": 12}, rates)
    b = item("B", "정기예금 (6M) 복리식", {"min_value": 1, "max_value": 12}, rates)
    assert compare(a, b)["verdict"] == "DISTINCT_VARIANT"
    payload = json.loads((ROOT / "docs/reports/catalog_identity_audit_20260904/candidates.json").read_text())
    reviews = payload["manual_review"]["reviews"]
    kakao = [r for r in reviews if r["pair"][0].startswith("INST-KR-000830")]
    assert len(kakao) == 8
    expected_urls = {
        "savings", "26weeks", "m1savings", "childsavings", "youthFutureSavings",
        "deposit", "safeboxes",
    }
    assert expected_urls <= {u.rsplit("/", 1)[-1] for r in kakao for u in r["source_urls"]}
    assert any("sohoVatBox" in u for r in kakao for u in r["source_urls"])
    assert any("INST-KR-000055-2-0007" in json.dumps(r) and "CEF9405DC03" in json.dumps(r) for r in payload["manual_review"]["reviews"])
    assert any("INST-KR-000408-2-CB40DE9089C" in json.dumps(r) for r in payload["manual_review"]["reviews"])
    assert any("CA7BF0C5E7B" in json.dumps(r) for r in payload["manual_review"]["reviews"])

def test_manual_priority_status_and_exact_pair_mappings() -> None:
    payload = json.loads((ROOT / "docs/reports/catalog_identity_audit_20260904/candidates.json").read_text())
    assert payload["manual_review"]["status"] == "PARTIAL_PRIMARY_SOURCE_REVIEW"
    text = json.dumps(payload["manual_review"], ensure_ascii=False)
    for code in ("INST-KR-000830-3-C90225C0EC4", "INST-KR-000830-3-C67C2196311", "INST-KR-000055-2-CEF9405DC03", "INST-KR-000408-2-CB40DE9089C"):
        assert code in text

    reviews = payload["manual_review"]["reviews"]
    twelve = next(r for r in reviews if any("C6470067B1C" in c for c in r["pair"]))
    assert twelve["verdict"] == "LIKELY_DUPLICATE_NEEDS_EVIDENCE"
    assert "HS" not in twelve["evidence_note"]
    smart = next(r for r in reviews if any(c.endswith("-0010") for c in r["pair"]))
    assert smart["verdict"] == "NOT_DUPLICATE"
    assert all("0002" not in r["pair"] for r in reviews)
    assert any(o["pair"] == ["INST-KR-000408-2-0002"] for o in payload["manual_review"]["coverage_observations"])
    report = (ROOT / "docs/reports/catalog_identity_audit_20260904/CATALOG_IDENTITY_AUDIT.md").read_text()
    for stale in ("priority_candidates", "No primary-source confirmation", "Correction:"):
        assert stale not in report


def test_manual_sources_match_each_reviewed_institution() -> None:
    payload = json.loads((ROOT / "docs/reports/catalog_identity_audit_20260904/candidates.json").read_text())
    expected_domains = {
        "INST-KR-000830": "kakaobank.com",
        "INST-KR-000055": "acuonsb.co.kr",
        "INST-KR-000408": "hanwhasbank.com",
    }
    for review in payload["manual_review"]["reviews"]:
        institution_id = review["pair"][0].rsplit("-", 2)[0]
        expected_domain = expected_domains[institution_id]
        assert review["source_urls"]
        assert all(expected_domain in url for url in review["source_urls"])
