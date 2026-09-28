"""Small opt-in live experiment; does not modify runtime prompts or catalog.

Run with --run NAME --variant baseline|contract --cases 0,1.
Credentials are read from environment or the ignored .env, never serialized.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import get_args

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError
from eligibility.catalog.normalized_loader import load_normalized_product_catalog
from eligibility.ingestion.institution_collector import _read_dotenv_value
from eligibility.llm import LLMGateway, LLMSettings, LLMPurpose
from eligibility.schema import semantic as schema
from eligibility.search import semantic as sem

OUT = ROOT / "reports/semantic-prompt-experiment-20260907"


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def contract_prompt():
    enums = {name: list(get_args(getattr(schema, name))) for name in (
        "ConditionKind", "SubjectRole", "ScopeKind", "MetricKind", "AmountUnit",
        "Comparator", "OfficialBasis", "RewardRelationType",
    )}
    enums["PeriodWindow.basis"] = [
        "SUBSCRIPTION_MONTH", "MATURITY_PREV_PREV_MONTH_END", "SALARY_DESIGNATED_DATE",
        "TERM_RATIO", "AS_OF", "UNSPECIFIED",
    ]
    return sem.SYSTEM_PROMPT + """

추출 절차와 출력 계약:
이 작업은 원문의 조건을 기록하는 일입니다. 사용자 답변이 없다는 것은 원문을 해석하지 못했다는 뜻이 아닙니다.
먼저 각 우대 규칙에 필요한 원자 조건을 구분하고, 대상·비교값·단위·시간 범위를 각 조건에 기록한 뒤 ALL/ANY로 연결하세요.
원문 안의 다른 우대 항목은 문맥일 수 있습니다. source_metadata.rule_id가 가리키는 우대의 조건만 expression으로 반환하세요.
거래 조건의 잎은 op=PREDICATE, kind=아래 허용값으로 작성합니다. 적절한 전용 종류가 없으면 kind=OTHER와 원문 근거로 보존하세요.
PREDICATE는 children=[], variable=null, child_filter=null입니다. 논리 노드는 children 외 술어 필드를 null/[]로 둡니다.
expected_literal에는 원문에 쓰인 수치 표기를 그대로 복사하고 expected_unit에 단위를 기록하세요. 쉼표·분수 표기를 유지하세요.
expected는 스키마에 맞는 비교값일 때 채우고, 수치가 expected_literal에 충분히 표현되면 null로 둘 수 있습니다.
기간은 period.start_quote/end_quote에 정확한 원문을, 명시된 기간·영업일 오프셋·비율은 해당 필드에 담으세요.
아래 basis로 표현되지 않는 기준은 UNSPECIFIED로 두고 근거 인용을 보존하세요. 모르는 값을 임의 enum 이름으로 만들지 마세요.
한 period로 서로 다른 기간을 표현할 수 없으면 각각 조건 노드로 나누되 같은 경로에 ALL로 연결하세요.
원문에서 정의하지 않은 부분만 UNKNOWN으로 남기세요. 명시된 인원이나 기간은 함께 버리지 마세요.
기존 질문, 사용자 사실, 충족 판정은 생성하지 않습니다. required_facts는 근거 있는 기존 fact ID를 모르면 []입니다.
enum 계약(표현 형식이며 상품별 판단 규칙이 아님):
""" + json.dumps(enums, ensure_ascii=False)


def contract_v2_prompt():
    return contract_prompt() + """

필드별 정확한 대응과 완전성 확인:
- kind는 ConditionKind, subject는 SubjectRole, scope.institution은 ScopeKind,
  metric은 MetricKind, expected_unit은 AmountUnit, comparator는 Comparator의 값만 씁니다.
- '당행'은 scope.institution=THIS_INSTITUTION입니다. 은행 이름은 이 enum 필드에 쓰지 않습니다.
  명시된 상품 종류는 scope.product_kind, 카드사 목록은 scope.networks에 원문대로 씁니다.
- 각 source_quote는 원문에 실제로 연속해서 존재하는 한 구간을 그대로 복사합니다. 생략 표시, 설명, 문장 재조합은 금지합니다.
  떨어진 조건을 함께 다룰 때는 필요한 부분을 포함하는 연속된 더 긴 원문 구간을 인용하거나 별도 노드로 나눕니다.
- 가입 계약기간과 거래실적 집계기간은 다릅니다. 계약기간별 조건이면 각 ANY 갈래 안에서
  계약기간 비교(OTHER, PERIOD, EQ/LTE 등)와 거래금액 조건을 ALL로 연결하세요.
  거래금액 노드의 period에는 원문이 정한 거래실적 집계 시작/끝을 보존하세요.
  계약기간을 TERM_RATIO라고 표기하지 마세요. TERM_RATIO는 기간의 비율에만 사용합니다.
- 서로 다른 기간 제약이 있다면 하나만 남기지 마세요. 비율이 적용되는 모든 대체 경로에 그 제약을 보존하세요.
- unit=BUSINESS_DAY는 영업일, DAY는 달력일입니다. 오프셋이 있으면 단위를 함께 기록하세요.
- NULL은 값 부재입니다. 불필요한 필드를 자연어 또는 기존 boolean placeholder로 채우지 마세요.
  특히 PREDICATE.variable=null이며 원문의 의미를 SOURCE_CLAUSE_GATE fact 하나로 되돌리지 마세요.
- 반환 전 원문에 명시된 대상·범위·각 임계값·단위·각 기간·대체 경로가 식의 필드에 보존됐는지 확인하세요.
  표현할 수 없는 요소는 해당 UNKNOWN 갈래에 근거를 남기고, 표현 가능한 다른 요소는 살려 둡니다.
"""


def node_diagnostics(raw, clause, path="expression"):
    if not isinstance(raw, dict):
        return []
    result = []
    coerced = sem.coerce_expression(raw, clause)
    item = {"path": path, "raw_op": raw.get("op"),
            "accepted_op": coerced.op if coerced else None, "issues": []}
    if not isinstance(raw.get("source_quote"), str) or raw["source_quote"] not in clause.text:
        item["issues"].append("NONCONTIGUOUS_OR_MISSING_QUOTE")
    if raw.get("op") == "UNKNOWN":
        item["issues"].append("MODEL_UNKNOWN")
    if raw.get("op") not in {"ALL", "ANY", "NOT", "UNKNOWN"}:
        try:
            strict = schema.SemanticExpression.model_validate(raw)
            if not sem._leaf_is_grounded(strict, clause):
                item["issues"].append("GROUNDING_REJECTED")
        except ValidationError as exc:
            item["issues"].append("STRICT_RAW_SCHEMA_INVALID")
            item["schema_errors"] = [{"loc": list(e["loc"]), "msg": e["msg"]}
                                     for e in exc.errors(include_input=False, include_url=False)]
        if raw.get("op") == "PREDICATE":
            normalized = dict(raw)
            normalized["scope"] = sem._coerce_scope(raw.get("scope"))
            normalized["period"] = sem._coerce_period(raw.get("period"), clause)
            try:
                strict = schema.SemanticExpression.model_validate(normalized)
                if not sem._leaf_is_grounded(strict, clause):
                    item["issues"].append("POST_NORMALIZATION_GROUNDING_REJECTED")
            except ValidationError as exc:
                item["issues"].append("POST_NORMALIZATION_SCHEMA_INVALID")
                item["normalized_errors"] = [{"loc": list(e["loc"]), "msg": e["msg"]}
                                             for e in exc.errors(include_input=False, include_url=False)]
        if coerced:
            for field in ("period", "scope"):
                if raw.get(field) is not None and getattr(coerced, field) is None:
                    item["issues"].append(field.upper() + "_DROPPED")
        if raw.get("op") != "UNKNOWN" and (coerced is None or coerced.op == "UNKNOWN"):
            item["issues"].append("VALIDATOR_UNKNOWN")
    if coerced:
        cleaned = coerced.model_dump(mode="json")
        item["changed_fields"] = {k: {"raw": v, "accepted": cleaned.get(k)}
                                  for k, v in raw.items()
                                  if k != "children" and v not in (None, []) and v != cleaned.get(k)}
    result.append(item)
    for i, child in enumerate(raw.get("children") or []):
        result.extend(node_diagnostics(child, clause, f"{path}.children[{i}]"))
    return result


def clause_diagnostics(row, clause):
    issues = []
    if row.get("source_hash") != clause.source_hash:
        issues.append("CLAUSE_HASH_MISMATCH")
    if row.get("source_quote") != clause.text:
        issues.append("CLAUSE_QUOTE_MISMATCH")
    return {"clause_id": clause.clause_id, "clause_issues": issues,
            "nodes": node_diagnostics(row.get("expression"), clause)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--variant", choices=["baseline", "contract", "contract-v2"], required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--cases-file", type=Path, default=OUT / "cases.json")
    args = parser.parse_args()
    if not args.run.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("Invalid run name")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dest = args.output_dir / args.run
    dest.mkdir(exist_ok=False)
    cases = json.loads(args.cases_file.read_text())
    selected = [cases[int(i)] for i in args.cases.split(",")]
    catalog = {p.product_id: p for p in load_normalized_product_catalog(verify_hashes=False)}
    packets = []
    routes = []
    for case in selected:
        packet = sem.product_packet(catalog[case["product_id"]], "prompt-experiment")
        clauses = [c for c in packet.clauses if c.canonical_id == case["rule_id"]] if packet else []
        if not clauses:
            raise SystemExit(f"Case not on compiler route: {case['case_id']}")
        assert clauses[0].text == case["source_text"], "Source changed"
        packets.append(sem.packet_for_clauses(packet, tuple(clauses)))
        routes.append({"case_id": case["case_id"], "clause_id": clauses[0].clause_id})
    prompt = {"baseline": lambda: sem.SYSTEM_PROMPT, "contract": contract_prompt,
              "contract-v2": contract_v2_prompt}[args.variant]()
    (dest / "prompt.txt").write_text(prompt)
    payload = {"products": [p.payload for p in packets]}
    save(dest / "input.json", payload)
    save(dest / "cases.json", selected)
    save(dest / "transport_schema.json", schema.LooseSemanticCompilation.model_json_schema())
    key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or _read_dotenv_value(
        ROOT / ".env", ("OPENAI_API_KEY", "LLM_API_KEY"))
    if not key:
        raise SystemExit("No configured API key")
    settings = LLMSettings(provider="OPENAI", base_url="https://api.openai.com/v1",
                           model="gpt-5.6-luna", api_key=key, timeout_seconds=90, max_retries=0)
    gateway = LLMGateway.from_settings(settings)
    # Capture only credential-free request bodies and model response bodies.
    original = gateway.client._request_with_retry
    def capture(*a, **kw):
        save(dest / "request_body.json", a[2])
        response, retries = original(*a, **kw)
        save(dest / "response_body.json", response.json())
        return response, retries
    gateway.client._request_with_retry = capture
    print(f"START {args.run}: {len(selected)} clauses", flush=True)
    try:
        response = gateway.generate_structured(
            LLMPurpose.SEMANTIC_CONDITION_COMPILATION,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            schema.LooseSemanticCompilation, system_prompt=prompt)
    except Exception as exc:
        # Never serialize exception text: HTTP errors may contain server detail.
        save(dest / "error.json", {"type": type(exc).__name__})
        print(f"FAILED {args.run}: {type(exc).__name__}", flush=True)
        return
    (dest / "raw_text.json").write_text(response.raw_text)
    raw = response.data.model_dump(mode="json")
    save(dest / "parsed.json", raw)
    accepted = sem.accept_compilation(response.data, packets)
    save(dest / "accepted.json", accepted.model_dump(mode="json"))
    allowed = {c.clause_id: c for p in packets for c in p.clauses}
    diagnostics = []
    for row in raw["clauses"]:
        clause = allowed.get(row["clause_id"])
        if clause:
            diagnostics.append(clause_diagnostics(row, clause))
    save(dest / "diagnostics.json", diagnostics)
    meta = {"variant": args.variant, "model": response.model, "latency_ms": response.latency_ms,
            "usage": response.token_usage.model_dump(mode="json") if response.token_usage else None,
            "retry_count": response.retry_count, "routes": routes,
            "source_hashes": {str(p.relative_to(ROOT)): sem.digest(p.read_text()) for p in (
                ROOT / "src/eligibility/search/semantic.py", ROOT / "src/eligibility/schema/semantic.py",
                Path(__file__).resolve())},
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    save(dest / "metadata.json", meta)
    print(f"DONE {args.run}: {response.latency_ms}ms", flush=True)


if __name__ == "__main__":
    main()
