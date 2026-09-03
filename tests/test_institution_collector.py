from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import eligibility.ingestion.institution_collector as collector
from eligibility.ingestion.institution_collector import (
    MissingServiceKey,
    collect_institutions,
    normalize_institution_record,
    resolve_service_key,
    write_collection,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_keeps_fss_leading_zero_and_omits_unknowns() -> None:
    record = normalize_institution_record(
        _fixture("fsc_get_fnco_outl_page1.json")["response"]["body"]["items"]["item"][0]
    )

    assert record == {
        "institution_id": "00685935",
        "crno": "1101113892240",
        "bzno": "1078708658",
        "official_name_ko": "메리츠자산운용",
        "official_name_en": "Meritz Asset Management",
        "sicCd": "64201",
        "sicNm": "금융업",
        "mntrFcnFncoCd": "020",
        "mntrFcnFncoCdNm": "우리은행",
    }


def test_collects_all_pages_and_writes_source_separately(tmp_path: Path) -> None:
    responses = [
        _fixture("fsc_get_fnco_outl_page1.json"),
        _fixture("fsc_get_fnco_outl_page2.json"),
    ]
    calls: list[dict[str, object]] = []

    def fake_fetcher(key: str, **kwargs: object) -> dict:
        calls.append({"key": key, **kwargs})
        return responses[len(calls) - 1]

    result = collect_institutions(
        "abc+123/decoded",
        page_size=1,
        fetcher=fake_fetcher,
        captured_at="2026-08-23T00:00:00+00:00",
    )

    assert [call["page_no"] for call in calls] == [1, 2]
    assert [item["institution_id"] for item in result.institutions] == [
        "00685935",
        "00001234",
    ]
    assert result.institutions[0]["source"]["api_name"] == (
        "GetFnCoBasiInfoService/getFnCoOutl"
    )
    raw_path, normalized_path = write_collection(result, tmp_path / "institutions")
    assert raw_path.parent.name == "raw"
    assert normalized_path.parent.name == "normalized"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert len(raw["pages"]) == 2
    assert len(normalized["institutions"]) == 2
    assert "abc+123" not in raw_path.read_text(encoding="utf-8")


def test_missing_fss_id_is_preserved_as_raw_gap_without_stopping_collection() -> None:
    response = _fixture("fsc_get_fnco_outl_page1.json")
    items = response["response"]["body"]["items"]["item"]
    items.append({"fncoNm": "고유번호 미제공 기관", "crno": "1234567890123"})
    response["response"]["body"]["totalCount"] = 2

    result = collect_institutions(
        "key",
        page_size=100,
        fetcher=lambda *_args, **_kwargs: response,
        captured_at="2026-08-23T00:00:00+00:00",
    )

    assert len(result.raw_pages[0]["response"]["body"]["items"]["item"]) == 2
    assert len(result.institutions) == 1
    assert result.normalization_gaps == (
        {
            "page_no": 1,
            "item_index": 1,
            "reason": "기관 응답에 fssCorpUnqNo가 없어 정규화할 수 없습니다.",
            "fncoNm": "고유번호 미제공 기관",
            "crno": "1234567890123",
        },
    )


def test_resolve_service_key_prefers_environment_and_decodes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FSC_OPENAPI_SERVICE_KEY", "abc%2B123%2Fkey")
    assert resolve_service_key(dotenv_path=tmp_path / ".env", prompt=False) == "abc+123/key"


def test_request_encodes_decoded_key_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"response":{"header":{"resultCode":"00"},"body":{}}}'

    def fake_urlopen(request: object, timeout: float) -> Response:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        return Response()

    monkeypatch.setattr(collector.urllib.request, "urlopen", fake_urlopen)
    collector._request_json(
        "abc%2B123%2Fkey",
        page_no=1,
        page_size=1,
        bas_dt=None,
        crno=None,
        fnco_nm=None,
        timeout=1,
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["serviceKey"] == ["abc+123/key"]


def test_resolve_service_key_uses_hidden_prompt_after_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "FSC_OPENAPI_SERVICE_KEY",
        "FSC_API_KEY",
        "DATA_GO_KR_SERVICE_KEY",
        "FSS_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("FSC_OPENAPI_SERVICE_KEY='dotenv%2Fkey'\n", encoding="utf-8")
    assert resolve_service_key(dotenv_path=tmp_path / ".env", prompt=False) == "dotenv/key"


def test_missing_key_does_not_fabricate_collection(tmp_path: Path) -> None:
    with pytest.raises(MissingServiceKey):
        resolve_service_key(dotenv_path=tmp_path / ".env", environ={}, prompt=False)
