"""Collect and normalize Korean financial institution master data.

The collector deliberately keeps the response pages (source material) separate
from the normalized institution records used by the application.  It does not
accept a service key as a command-line argument: command-line arguments are
commonly retained in shell history and process listings.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

API_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/"
    "GetFnCoBasiInfoService/getFnCoOutl"
)
API_NAME = "GetFnCoBasiInfoService/getFnCoOutl"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 30.0

# The first name is documented for this project.  The aliases make migration
# from existing data.go.kr/FSS scripts less surprising while retaining one
# deterministic resolution order.
SERVICE_KEY_ENV_NAMES = (
    "FSC_OPENAPI_SERVICE_KEY",
    "FSC_API_KEY",
    "DATA_GO_KR_SERVICE_KEY",
    "FSS_API_KEY",
)


class InstitutionCollectorError(RuntimeError):
    """A safe, user-facing collector error (without request credentials)."""


class MissingServiceKey(InstitutionCollectorError):
    """Raised when no service key is available and prompting is disabled."""


def _non_empty(value: Any) -> str | None:
    """Return a trimmed string, omitting null/blank API values."""

    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None
    return text


def _decode_service_key(value: str) -> str:
    """Normalize a data.go.kr key before ``urlencode`` encodes it once.

    data.go.kr shows both decoded keys and percent-encoded keys depending on
    where a user copies the key from.  Decoding here and encoding only as part
    of query construction prevents ``%`` from being encoded a second time.
    """

    return urllib.parse.unquote(value.strip())


def _read_dotenv_value(path: Path, names: tuple[str, ...]) -> str | None:
    """Read only the configured key names from a local ignored dotenv file.

    This intentionally supports the small subset needed here instead of
    adding a dependency or loading arbitrary dotenv settings into the process.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return None

    wanted = set(names)
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in wanted:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            return value
    return None


def resolve_service_key(
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    prompt: bool = True,
) -> str:
    """Resolve the API key without exposing it in arguments, logs, or output.

    Resolution order is environment, local ``.env`` (when present), then a
    hidden interactive prompt.  ``dotenv_path`` is injectable for tests and
    defaults to the current working directory's ``.env``.
    """

    env = os.environ if environ is None else environ
    for name in SERVICE_KEY_ENV_NAMES:
        value = _non_empty(env.get(name))
        if value:
            return _decode_service_key(value)

    local_path = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / ".env"
    value = _read_dotenv_value(local_path, SERVICE_KEY_ENV_NAMES)
    if value:
        return _decode_service_key(value)

    if not prompt:
        raise MissingServiceKey(
            "서비스 키가 없습니다. FSC_OPENAPI_SERVICE_KEY를 설정하거나 "
            "대화형 실행으로 입력하세요."
        )
    try:
        value = getpass.getpass("FSC OpenAPI service key (입력은 숨겨집니다): ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise MissingServiceKey("서비스 키 입력이 취소되었습니다.") from exc
    value = _non_empty(value)
    if not value:
        raise MissingServiceKey("서비스 키가 비어 있습니다.")
    return _decode_service_key(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_institution_record(record: Mapping[str, Any]) -> dict[str, str]:
    """Project one API item into the serving/catalog institution shape.

    Unknown, null, and blank fields are omitted.  The FSS identifier is kept
    as text so leading zeroes remain part of the canonical institution ID.
    """

    institution_id = _non_empty(record.get("fssCorpUnqNo"))
    if not institution_id:
        raise InstitutionCollectorError(
            "기관 응답에 fssCorpUnqNo가 없어 정규화할 수 없습니다."
        )

    normalized: dict[str, str] = {"institution_id": institution_id}
    field_map = {
        "crno": "crno",
        "bzno": "bzno",
        "official_name_ko": "fncoNm",
        "official_name_en": "fncoEnsnNm",
        "sicCd": "sicCd",
        "sicNm": "sicNm",
        "mntrFcnFncoCd": "mntrFcnFncoCd",
        "mntrFcnFncoCdNm": "mntrFcnFncoCdNm",
    }
    for output_name, input_name in field_map.items():
        value = _non_empty(record.get(input_name))
        if value is not None:
            normalized[output_name] = value
    return normalized


def _extract_items(response: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any]]:
    """Extract API items and body metadata across JSON shape variants."""

    service_error = response.get("OpenAPI_ServiceResponse")
    if isinstance(service_error, Mapping):
        error_header = service_error.get("cmmMsgHeader", {})
        if isinstance(error_header, Mapping):
            code = _non_empty(error_header.get("returnReasonCode")) or "UNKNOWN"
            message = _non_empty(error_header.get("returnAuthMsg")) or _non_empty(
                error_header.get("errMsg")
            )
            raise InstitutionCollectorError(
                f"FSC API 오류({code}): {message or '요청을 확인하세요.'}"
            )

    root = response.get("response", response)
    if not isinstance(root, Mapping):
        raise InstitutionCollectorError("API 응답의 response 구조가 올바르지 않습니다.")
    header = root.get("header", {})
    if isinstance(header, Mapping):
        result_code = _non_empty(header.get("resultCode"))
        if result_code not in (None, "00", "0"):
            # Do not include the response body: an upstream error might echo
            # request details and must never become a credential leak.
            message = _non_empty(header.get("resultMsg")) or "알 수 없는 API 오류"
            raise InstitutionCollectorError(f"FSC API 오류({result_code}): {message}")
    body = root.get("body", {})
    if not isinstance(body, Mapping):
        raise InstitutionCollectorError("API 응답의 body 구조가 올바르지 않습니다.")
    items_container = body.get("items", {})
    if isinstance(items_container, Mapping):
        raw_items = items_container.get("item", [])
    else:
        raw_items = items_container
    if raw_items is None:
        raw_items = []
    if isinstance(raw_items, Mapping):
        raw_items = [raw_items]
    if not isinstance(raw_items, list) or any(
        not isinstance(item, Mapping) for item in raw_items
    ):
        raise InstitutionCollectorError("API 응답의 items.item 구조가 올바르지 않습니다.")
    return tuple(raw_items), dict(body)


def _request_json(
    service_key: str,
    *,
    page_no: int,
    page_size: int,
    bas_dt: str | None,
    crno: str | None,
    fnco_nm: str | None,
    timeout: float,
) -> dict[str, Any]:
    params: dict[str, str | int] = {
        "numOfRows": page_size,
        "pageNo": page_no,
        "resultType": "json",
        "serviceKey": _decode_service_key(service_key),
    }
    if bas_dt:
        params["basDt"] = bas_dt
    if crno:
        params["crno"] = crno
    if fnco_nm:
        params["fncoNm"] = fnco_nm
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API_ENDPOINT}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise InstitutionCollectorError(
            f"FSC API HTTP 오류({exc.code})"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstitutionCollectorError("FSC API 네트워크 요청에 실패했습니다.") from exc
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstitutionCollectorError("FSC API가 JSON을 반환하지 않았습니다.") from exc
    if not isinstance(decoded, dict):
        raise InstitutionCollectorError("FSC API JSON 최상위 구조가 올바르지 않습니다.")
    return decoded


@dataclass(frozen=True)
class CollectionResult:
    """Immutable source pages plus normalized institution records."""

    source_metadata: dict[str, Any]
    raw_pages: tuple[dict[str, Any], ...]
    institutions: tuple[dict[str, Any], ...]
    normalization_gaps: tuple[dict[str, Any], ...] = ()

    def raw_document(self) -> dict[str, Any]:
        return {"source": self.source_metadata, "pages": list(self.raw_pages)}

    def normalized_document(self) -> dict[str, Any]:
        document = {
            "source": self.source_metadata,
            "institutions": list(self.institutions),
        }
        if self.normalization_gaps:
            document["normalization_gaps"] = list(self.normalization_gaps)
        return document


Fetcher = Callable[..., dict[str, Any]]


def collect_institutions(
    service_key: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    bas_dt: str | None = None,
    crno: str | None = None,
    fnco_nm: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetcher: Fetcher | None = None,
    captured_at: str | None = None,
) -> CollectionResult:
    """Fetch all pages and return separate raw and normalized collections."""

    if page_size < 1:
        raise ValueError("page_size must be positive")
    key = _non_empty(service_key)
    if not key:
        raise MissingServiceKey("서비스 키가 비어 있습니다.")
    fetch = fetcher or _request_json
    captured = captured_at or datetime.now(timezone.utc).isoformat()
    pages: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    normalization_gaps: list[dict[str, Any]] = []
    seen_page_signatures: set[str] = set()
    page_no = 1
    total_count: int | None = None
    raw_item_count = 0

    while True:
        response = fetch(
            key,
            page_no=page_no,
            page_size=page_size,
            bas_dt=bas_dt,
            crno=crno,
            fnco_nm=fnco_nm,
            timeout=timeout,
        )
        if not isinstance(response, dict):
            raise InstitutionCollectorError("fetcher가 JSON object를 반환해야 합니다.")
        items, body = _extract_items(response)
        # A duplicate response is a common failure mode of paging proxies.
        signature = json.dumps(response, ensure_ascii=False, sort_keys=True)
        if signature in seen_page_signatures:
            raise InstitutionCollectorError("페이지 응답이 반복되어 수집을 중단했습니다.")
        seen_page_signatures.add(signature)
        pages.append(response)
        raw_item_count += len(items)
        for item_index, item in enumerate(items):
            try:
                records.append(normalize_institution_record(item))
            except InstitutionCollectorError as exc:
                gap: dict[str, Any] = {
                    "page_no": page_no,
                    "item_index": item_index,
                    "reason": str(exc),
                }
                for field_name in ("fncoNm", "crno", "bzno"):
                    value = _non_empty(item.get(field_name))
                    if value is not None:
                        gap[field_name] = value
                normalization_gaps.append(gap)

        body_total = _as_int(body.get("totalCount"), default=-1)
        if body_total >= 0:
            total_count = body_total
        if total_count == 0 or not items:
            break
        if total_count is not None and raw_item_count >= total_count:
            break
        if len(items) < page_size:
            break
        if total_count is not None and page_no >= (total_count + page_size - 1) // page_size:
            break
        page_no += 1

    source_metadata: dict[str, Any] = {
        "provider": "FINANCIAL_SERVICES_COMMISSION",
        "platform": "data.go.kr",
        "api_name": API_NAME,
        "endpoint": API_ENDPOINT,
        "result_type": "json",
        "captured_at": captured,
        "page_size": page_size,
        "page_count": len(pages),
    }
    if bas_dt:
        source_metadata["requested_bas_dt"] = bas_dt
    if crno:
        source_metadata["requested_crno"] = crno
    if fnco_nm:
        source_metadata["requested_fnco_nm"] = fnco_nm
    record_source = {
        "provider": source_metadata["provider"],
        "api_name": source_metadata["api_name"],
        "endpoint": source_metadata["endpoint"],
        "captured_at": source_metadata["captured_at"],
    }
    for record in records:
        record["source"] = record_source.copy()
    # Deduplicate by canonical ID, keeping the first source occurrence.
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique.setdefault(record["institution_id"], record)
    return CollectionResult(
        source_metadata=source_metadata,
        raw_pages=tuple(pages),
        institutions=tuple(unique.values()),
        normalization_gaps=tuple(normalization_gaps),
    )


def write_collection(result: CollectionResult, output_dir: str | Path) -> tuple[Path, Path]:
    """Write source pages and normalized serving data to separate directories."""

    root = Path(output_dir)
    raw_dir = root / "raw"
    normalized_dir = root / "normalized"
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    captured = str(result.source_metadata["captured_at"])
    stamp = captured.replace("-", "").replace(":", "").replace("+00:00", "Z")
    raw_path = raw_dir / f"fsc_institutions_{stamp}.json"
    normalized_path = normalized_dir / f"institutions_{stamp}.json"
    raw_path.write_text(
        json.dumps(result.raw_document(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    normalized_path.write_text(
        json.dumps(result.normalized_document(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return raw_path, normalized_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FSC 금융회사 기본정보를 수집합니다 (API 키는 CLI 인자로 받지 않음)."
    )
    parser.add_argument("--output-dir", default="data/institutions")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--bas-dt", help="기준일자 YYYYMMDD")
    parser.add_argument("--crno", help="법인등록번호 필터")
    parser.add_argument("--fnco-nm", help="금융회사명 필터")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        service_key = resolve_service_key()
        result = collect_institutions(
            service_key,
            page_size=args.page_size,
            bas_dt=args.bas_dt,
            crno=args.crno,
            fnco_nm=args.fnco_nm,
            timeout=args.timeout,
        )
        raw_path, normalized_path = write_collection(result, args.output_dir)
    except (InstitutionCollectorError, ValueError) as exc:
        print(f"수집 실패: {exc}", file=sys.stderr)
        return 2
    print(f"기관 {len(result.institutions)}건 수집 완료")
    if result.normalization_gaps:
        print(f"정규화 제외 {len(result.normalization_gaps)}건 (결과 파일에서 확인)")
    print(f"원문: {raw_path}")
    print(f"정규화: {normalized_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI smoke tests
    raise SystemExit(main())
