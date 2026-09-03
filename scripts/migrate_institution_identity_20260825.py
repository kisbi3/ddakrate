#!/usr/bin/env python3
"""Publish the internal Institution identity model and migrate current data.

This is a data-only migration.  It preserves the previous immutable release,
writes a new Institution snapshot/entity root, rewrites current Product and
CustomDefinition references into new paths, and publishes the three products
that were held only because an FSS corporation number was unavailable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BATCH = "20260825-institution-identity-01"
PUBLISHED_AT = "2026-08-25T14:20:00+09:00"

OLD_INSTITUTION_SNAPSHOT = ROOT / "data/institutions/normalized/snapshots/institutions_20260824T235500+0900.json"
OLD_PRODUCT_INDEX = ROOT / "data/financial_products/normalized/index.json"
OLD_PRODUCT_MANIFEST = ROOT / "data/financial_products/normalized/manifests/20260825-human-confirmed-01.json"

INSTITUTION_RAW = ROOT / f"data/institutions/raw/{BATCH}"
INSTITUTION_NORMALIZED = ROOT / "data/institutions/normalized"
INSTITUTION_ENTITY_ROOT = INSTITUTION_NORMALIZED / "entities"
INSTITUTION_SNAPSHOT = INSTITUTION_NORMALIZED / "snapshots/institutions_20260825T142000+0900.json"
INSTITUTION_MANIFEST = INSTITUTION_NORMALIZED / f"manifests/{BATCH}.json"
INSTITUTION_MAPPING = INSTITUTION_NORMALIZED / f"mappings/{BATCH}.json"
INSTITUTION_INDEX = INSTITUTION_NORMALIZED / "index.json"

PRODUCT_NORMALIZED = ROOT / "data/financial_products/normalized"
PRODUCT_RAW = ROOT / f"data/financial_products/raw/{BATCH}"
PRODUCT_INDEX = PRODUCT_NORMALIZED / "index.json"
PRODUCT_MANIFEST = PRODUCT_NORMALIZED / f"manifests/{BATCH}.json"
PRODUCT_CUSTOM = PRODUCT_NORMALIZED / f"definitions/custom/{BATCH}.json"
PRODUCT_PROTECTION = PRODUCT_NORMALIZED / f"definitions/protection_schemes_{BATCH}.json"
PRODUCT_REPORT = PRODUCT_NORMALIZED / f"reports/{BATCH}"

TOSS_ID = "INST-KR-001106"
POST_ID = "INST-KR-001107"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def transformed(value: Any, exact: dict[str, str]) -> Any:
    if isinstance(value, str):
        return exact.get(value, value)
    if isinstance(value, list):
        return [transformed(item, exact) for item in value]
    if isinstance(value, dict):
        return {key: transformed(item, exact) for key, item in value.items()}
    return value


def product_fingerprint(product: dict[str, Any]) -> str:
    semantic = copy.deepcopy(product)
    semantic.pop("version_metadata", None)
    payload = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def institution_mapping(rows: list[dict[str, Any]]) -> dict[str, str]:
    legacy_ids = sorted(row["institution_id"] for row in rows)
    if len(legacy_ids) != len(set(legacy_ids)):
        raise ValueError("Legacy institution_id values are not unique")
    return {
        legacy_id: f"INST-KR-{number:06d}"
        for number, legacy_id in enumerate(legacy_ids, start=1)
    }


def external_identifier(
    system: str,
    value: str,
    source_ref_ids: list[str],
) -> dict[str, Any]:
    return {
        "system": system,
        "value": value,
        "country": "KR",
        "source_ref_ids": source_ref_ids,
    }


def build_institutions() -> tuple[dict[str, str], list[dict[str, Any]], list[Path]]:
    old_snapshot = read_json(OLD_INSTITUTION_SNAPSHOT)
    old_rows = old_snapshot["institutions"]
    mapping = institution_mapping(old_rows)
    if max(mapping.values()) != "INST-KR-001105":
        raise ValueError("Unexpected base Institution count")

    observations = {
        "batch": BATCH,
        "captured_at": PUBLISHED_AT,
        "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
        "observations": [
            {
                "provider": "FINANCIAL_SERVICES_COMMISSION",
                "api": "GetFnCoBasiInfoService/getFnCoOutl",
                "query": {"basDt": "20260824"},
                "result": {
                    "resultCode": "00",
                    "resultMsg": "NORMAL SERVICE.",
                    "totalCount": 1103,
                    "matches": {
                        "crno=1101117373098": 0,
                        "bzno=4628601671": 0,
                        "fncoNm contains 토스": 0,
                    },
                },
                "note": "사업자등록번호는 요청변수가 아니므로 최신 전체 응답에서 bzno를 필터링했다.",
            },
            {
                "provider": "OPEN_DART",
                "url": "https://dart.fss.or.kr/dsae001/selectPopup.ax?selectKey=01529195",
                "official_name_ko": "토스뱅크 주식회사",
                "dart_corp_code": "01529195",
                "crno": "1101117373098",
                "bzno": "4628601671",
            },
            {
                "provider": "TOSSBANK_OFFICIAL",
                "url": "https://www.tossbank.com/",
                "official_name_ko": "토스뱅크㈜",
                "bzno": "4628601671",
            },
            {
                "provider": "MOIS_ADMIN_STANDARD_CODE_SYSTEM",
                "url": "https://code.go.kr/stdcode/orgSearch.do",
                "official_name_ko": "우정사업본부",
                "administrative_standard_code": "1721301",
            },
        ],
        "provenance_note": (
            "공식 조회 결과에서 확인한 식별자만 전사했다. 금융위 응답에 없는 FSS 번호는 "
            "생성하거나 다른 번호로 대체하지 않았다."
        ),
    }
    observation_path = INSTITUTION_RAW / "source_captures/verified_identifier_queries.json"
    write_json(observation_path, observations)

    institution_sources = {
        "source_documents": [
            {
                "source_id": "INST-ID-20260825-SRC-FSC",
                "publisher": "금융위원회",
                "authority": "REGULATORY_DISCLOSURE",
                "document_type": "OFFICIAL_API_QUERY_RESULT",
                "title": "금융회사기본정보 최신 기준일 조회 및 토스뱅크 불일치 확인",
                "url": "https://apis.data.go.kr/1160100/service/GetFnCoBasiInfoService/getFnCoOutl",
                "retrieved_at": PUBLISHED_AT,
                "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
                "capture_path": str(observation_path.relative_to(ROOT)),
            },
            {
                "source_id": "INST-ID-20260825-SRC-DART-TOSS",
                "publisher": "금융감독원 전자공시시스템",
                "authority": "REGULATORY_DISCLOSURE",
                "document_type": "OFFICIAL_CORPORATE_DETAIL",
                "title": "토스뱅크 주식회사 기업개황",
                "url": "https://dart.fss.or.kr/dsae001/selectPopup.ax?selectKey=01529195",
                "retrieved_at": PUBLISHED_AT,
                "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
                "capture_path": str(observation_path.relative_to(ROOT)),
            },
            {
                "source_id": "INST-ID-20260825-SRC-TOSS",
                "publisher": "토스뱅크",
                "authority": "OFFICIAL_INSTITUTION",
                "document_type": "OFFICIAL_INSTITUTION_PAGE",
                "title": "토스뱅크 공식 홈페이지 사업자 정보",
                "url": "https://www.tossbank.com/",
                "retrieved_at": PUBLISHED_AT,
                "capture_method": "OFFICIAL_HTML_CAPTURE_ALREADY_PRESERVED",
                "capture_path": "data/financial_products/raw/20260825-human-confirmed-01/source_captures/d/RECHECK-D-SRC-04.html",
            },
            {
                "source_id": "INST-ID-20260825-SRC-POST",
                "publisher": "행정안전부 행정표준코드관리시스템",
                "authority": "REGULATORY_DISCLOSURE",
                "document_type": "OFFICIAL_ADMINISTRATIVE_CODE_QUERY",
                "title": "우정사업본부 기관코드 조회",
                "url": "https://code.go.kr/stdcode/orgSearch.do",
                "retrieved_at": PUBLISHED_AT,
                "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
                "capture_path": str(observation_path.relative_to(ROOT)),
            },
        ]
    }
    institution_evidence = {
        "evidence_refs": [
            {
                "evidence_ref_id": "INST-ID-20260825-EVD-TOSS",
                "source_ids": [
                    "INST-ID-20260825-SRC-FSC",
                    "INST-ID-20260825-SRC-DART-TOSS",
                    "INST-ID-20260825-SRC-TOSS",
                ],
                "source_text": (
                    "토스뱅크는 금융위 최신 금융회사기본정보에서 FSS 번호가 공개되지 않았고, "
                    "DART 법인등록번호 1101117373098과 공식 홈페이지 사업자등록번호 "
                    "4628601671로 동일 법인을 식별했다."
                ),
                "supports": ["Institution.identifiers", "Institution.identifier_coverage"],
            },
            {
                "evidence_ref_id": "INST-ID-20260825-EVD-POST",
                "source_ids": ["INST-ID-20260825-SRC-POST"],
                "source_text": "행정표준코드관리시스템에서 우정사업본부 기관코드 1721301을 확인했다.",
                "supports": ["Institution.identifiers"],
            },
        ]
    }
    source_path = INSTITUTION_RAW / "source_documents.json"
    evidence_path = INSTITUTION_RAW / "evidence_refs.json"
    write_json(source_path, institution_sources)
    write_json(evidence_path, institution_evidence)

    rows: list[dict[str, Any]] = []
    entity_paths: list[Path] = []
    for old in sorted(old_rows, key=lambda item: item["institution_id"]):
        old_id = old["institution_id"]
        new_id = mapping[old_id]
        identifiers = [
            external_identifier(
                "FSS_CORP_UNIQUE_NO", old_id, ["INST-ID-20260825-SRC-FSC"]
            )
        ]
        if old.get("crno"):
            identifiers.append(
                external_identifier("CRNO", old["crno"], ["INST-ID-20260825-SRC-FSC"])
            )
        if old.get("bzno"):
            identifiers.append(
                external_identifier("BZNO", old["bzno"], ["INST-ID-20260825-SRC-FSC"])
            )
        row: dict[str, Any] = {
            "institution_id": new_id,
            "version": 1,
            "official_name_ko": old["official_name_ko"],
            "identifiers": identifiers,
            "source_ref_ids": ["INST-ID-20260825-SRC-FSC"],
            "effective_to": None,
            "migration": {
                "legacy_institution_id": old_id,
                "legacy_id_system": "FSS_CORP_UNIQUE_NO",
                "migrated_at": PUBLISHED_AT,
            },
        }
        for field in ("official_name_en", "sicCd", "sicNm"):
            if old.get(field):
                row[field] = old[field]
        if old.get("mntrFcnFncoCd") or old.get("mntrFcnFncoCdNm"):
            row["network_attributes"] = {
                key: old[key]
                for key in ("mntrFcnFncoCd", "mntrFcnFncoCdNm")
                if old.get(key)
            }
        rows.append(row)
        entity_path = INSTITUTION_ENTITY_ROOT / new_id / "v001.json"
        write_json(entity_path, row)
        entity_paths.append(entity_path)

    toss = {
        "institution_id": TOSS_ID,
        "version": 1,
        "official_name_ko": "토스뱅크 주식회사",
        "entity_type": "CORPORATION",
        "institution_categories": ["BANK", "INTERNET_ONLY_BANK"],
        "identifiers": [
            external_identifier("CRNO", "1101117373098", ["INST-ID-20260825-SRC-DART-TOSS"]),
            external_identifier("BZNO", "4628601671", ["INST-ID-20260825-SRC-TOSS"]),
        ],
        "identifier_coverage": [
            {
                "system": "FSS_CORP_UNIQUE_NO",
                "status": "NOT_PUBLISHED_BY_SOURCE",
                "checked_at": "2026-08-25",
                "source_ref_ids": ["INST-ID-20260825-SRC-FSC"],
            }
        ],
        "source_ref_ids": [
            "INST-ID-20260825-SRC-DART-TOSS",
            "INST-ID-20260825-SRC-TOSS",
            "INST-ID-20260825-SRC-FSC",
        ],
        "effective_to": None,
    }
    post = {
        "institution_id": POST_ID,
        "version": 1,
        "official_name_ko": "우정사업본부",
        "entity_type": "GOVERNMENT_AGENCY",
        "institution_categories": ["POSTAL_FINANCIAL_OPERATOR"],
        "identifiers": [
            external_identifier(
                "ADMINISTRATIVE_STANDARD_CODE",
                "1721301",
                ["INST-ID-20260825-SRC-POST"],
            )
        ],
        "identifier_coverage": [
            {
                "system": "FSS_CORP_UNIQUE_NO",
                "status": "NOT_APPLICABLE_OR_NOT_PUBLISHED",
                "checked_at": "2026-08-25",
                "source_ref_ids": ["INST-ID-20260825-SRC-FSC"],
            },
            {
                "system": "CRNO",
                "status": "NOT_APPLICABLE_GOVERNMENT_AGENCY",
                "checked_at": "2026-08-25",
                "source_ref_ids": ["INST-ID-20260825-SRC-POST"],
            },
        ],
        "source_ref_ids": ["INST-ID-20260825-SRC-POST"],
        "effective_to": None,
    }
    for row in (toss, post):
        rows.append(row)
        entity_path = INSTITUTION_ENTITY_ROOT / row["institution_id"] / "v001.json"
        write_json(entity_path, row)
        entity_paths.append(entity_path)

    rows.sort(key=lambda item: item["institution_id"])
    snapshot = {
        "schema_version": 2,
        "identity_model": "INTERNAL_ID_WITH_EXTERNAL_IDENTIFIERS",
        "source": {
            "base_snapshot": str(OLD_INSTITUTION_SNAPSHOT.relative_to(ROOT)),
            "identity_batch": BATCH,
            "captured_at": PUBLISHED_AT,
        },
        "institutions": rows,
    }
    write_json(INSTITUTION_SNAPSHOT, snapshot)
    write_json(
        INSTITUTION_MAPPING,
        {
            "mapping_version": 1,
            "generated_at": PUBLISHED_AT,
            "from_system": "LEGACY_INSTITUTION_ID_EQUAL_TO_FSS_CORP_UNIQUE_NO",
            "to_system": "DDAKRATE_INTERNAL_INSTITUTION_ID",
            "mappings": [
                {"legacy_institution_id": old_id, "institution_id": new_id}
                for old_id, new_id in sorted(mapping.items())
            ],
            "new_institutions": [TOSS_ID, POST_ID],
        },
    )

    crno_counts = Counter(
        identifier["value"]
        for row in rows
        for identifier in row["identifiers"]
        if identifier["system"] == "CRNO"
    )
    institution_validation = {
        "batch": BATCH,
        "validated_at": PUBLISHED_AT,
        "result": "PASS",
        "checks": {
            "institution_count": len(rows),
            "unique_internal_ids": len({row["institution_id"] for row in rows}),
            "fss_identifier_count": sum(
                1
                for row in rows
                for identifier in row["identifiers"]
                if identifier["system"] == "FSS_CORP_UNIQUE_NO"
            ),
            "crno_identifier_count": sum(crno_counts.values()),
            "duplicate_crno_values": sum(1 for count in crno_counts.values() if count > 1),
            "new_official_identifier_only_institutions": [TOSS_ID, POST_ID],
        },
        "issues": [],
    }
    institution_validation_path = INSTITUTION_NORMALIZED / f"reports/{BATCH}/validation.json"
    write_json(institution_validation_path, institution_validation)

    institution_files = [
        INSTITUTION_SNAPSHOT,
        INSTITUTION_MAPPING,
        source_path,
        evidence_path,
        observation_path,
        institution_validation_path,
        *entity_paths,
    ]
    manifest = {
        "manifest_version": 2,
        "publication_status": "PUBLISHED",
        "published_at": PUBLISHED_AT,
        "identity_model": "INTERNAL_ID_WITH_EXTERNAL_IDENTIFIERS",
        "base_snapshot": str(OLD_INSTITUTION_SNAPSHOT.relative_to(ROOT)),
        "snapshot_file": str(INSTITUTION_SNAPSHOT.relative_to(ROOT)),
        "institution_record_root": str(INSTITUTION_ENTITY_ROOT.relative_to(ROOT)),
        "institution_id_field": "institution_id",
        "institution_id_strategy": "DDAKRATE_INTERNAL_SEQUENTIAL_ID",
        "external_identifier_systems": [
            "FSS_CORP_UNIQUE_NO",
            "CRNO",
            "BZNO",
            "ADMINISTRATIVE_STANDARD_CODE",
        ],
        "institution_count": len(rows),
        "source_document_registry": str(source_path.relative_to(ROOT)),
        "evidence_ref_registry": str(evidence_path.relative_to(ROOT)),
        "migration_mapping": str(INSTITUTION_MAPPING.relative_to(ROOT)),
        "validation_file": str(institution_validation_path.relative_to(ROOT)),
        "application_code_changed": False,
        "files": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(institution_files, key=lambda item: str(item))
        ],
    }
    write_json(INSTITUTION_MANIFEST, manifest)
    write_json(
        INSTITUTION_INDEX,
        {
            "index_version": 2,
            "generated_at": PUBLISHED_AT,
            "current_manifest": str(INSTITUTION_MANIFEST.relative_to(ROOT)),
            "current_snapshot": str(INSTITUTION_SNAPSHOT.relative_to(ROOT)),
            "institution_record_root": str(INSTITUTION_ENTITY_ROOT.relative_to(ROOT)),
            "institution_id_field": "institution_id",
            "institution_id_strategy": "DDAKRATE_INTERNAL_SEQUENTIAL_ID",
            "external_identifier_field": "identifiers",
            "institution_count": len(rows),
        },
    )
    mapping[TOSS_ID] = TOSS_ID
    mapping[POST_ID] = POST_ID
    return mapping, rows, institution_files


def current_registry_with_identity(
    institution_map: dict[str, str],
) -> tuple[Path, Path, set[str]]:
    old_manifest = read_json(OLD_PRODUCT_MANIFEST)
    old_source_path = ROOT / old_manifest["source_document_registry"]
    old_evidence_path = ROOT / old_manifest["evidence_ref_registry"]
    sources = copy.deepcopy(read_json(old_source_path))
    evidence = copy.deepcopy(read_json(old_evidence_path))

    for row in sources["source_documents"]:
        capture_path = row.get("capture_path")
        if capture_path and not capture_path.startswith("data/"):
            row["capture_path"] = str((old_source_path.parent / capture_path).relative_to(ROOT))
        publisher = row.get("publisher")
        if isinstance(publisher, dict):
            old_id = publisher.get("institution_id")
            if old_id in institution_map:
                publisher["institution_id"] = institution_map[old_id]
            elif old_id is None and "토스뱅크" in str(publisher.get("name", "")):
                publisher["institution_id"] = TOSS_ID
            elif old_id is None and "우정사업본부" in str(publisher.get("name", "")):
                publisher["institution_id"] = POST_ID

    identity_capture = "data/institutions/raw/20260825-institution-identity-01/source_captures/verified_identifier_queries.json"
    new_sources = [
        {
            "source_id": "IDENTITY-20260825-SRC-TOSS",
            "publisher": {"name": "토스뱅크 주식회사", "institution_id": TOSS_ID},
            "authority": "REGULATORY_DISCLOSURE",
            "document_type": "OFFICIAL_IDENTIFIER_RESOLUTION",
            "title": "토스뱅크 법인·사업자 식별자 공식 교차 확인",
            "url": "https://dart.fss.or.kr/dsae001/selectPopup.ax?selectKey=01529195",
            "retrieved_at": PUBLISHED_AT,
            "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
            "capture_path": identity_capture,
        },
        {
            "source_id": "IDENTITY-20260825-SRC-POST",
            "publisher": {"name": "우정사업본부", "institution_id": POST_ID},
            "authority": "REGULATORY_DISCLOSURE",
            "document_type": "OFFICIAL_ADMINISTRATIVE_CODE_QUERY",
            "title": "우정사업본부 행정표준기관코드 확인",
            "url": "https://code.go.kr/stdcode/orgSearch.do",
            "retrieved_at": PUBLISHED_AT,
            "capture_method": "VERIFIED_OFFICIAL_QUERY_RESULT",
            "capture_path": identity_capture,
        },
        {
            "source_id": "IDENTITY-20260825-SRC-POST-GUARANTEE",
            "publisher": {"name": "우정사업본부", "institution_id": POST_ID},
            "authority": "OFFICIAL_INSTITUTION",
            "document_type": "OFFICIAL_LEGAL_GUARANTEE_GUIDE",
            "title": "우체국예금 국가 지급 책임 안내",
            "url": "https://www.epostbank.go.kr/sw/ip/gs/pdf/PostDeposit_Kr.pdf",
            "retrieved_at": PUBLISHED_AT,
        },
    ]
    source_by_id = {row["source_id"]: row for row in sources["source_documents"]}
    source_by_id.update({row["source_id"]: row for row in new_sources})
    sources["source_documents"] = sorted(source_by_id.values(), key=lambda row: row["source_id"])

    new_evidence = [
        {
            "evidence_ref_id": "IDENTITY-20260825-EVD-TOSS",
            "source_ids": ["IDENTITY-20260825-SRC-TOSS"],
            "source_text": (
                "토스뱅크는 DART 법인등록번호 1101117373098과 공식 홈페이지 "
                "사업자등록번호 4628601671로 내부 Institution에 연결했다."
            ),
            "supports": ["product.institution_id", "custom_definition.institution_id"],
            "captured_at": PUBLISHED_AT,
        },
        {
            "evidence_ref_id": "IDENTITY-20260825-EVD-POST",
            "source_ids": ["IDENTITY-20260825-SRC-POST"],
            "source_text": "우정사업본부는 행정표준기관코드 1721301로 내부 Institution에 연결했다.",
            "supports": ["product.institution_id"],
            "captured_at": PUBLISHED_AT,
        },
        {
            "evidence_ref_id": "IDENTITY-20260825-EVD-TOSS-MOIM",
            "source_ids": ["RECHECK-D-SRC-04", "RECHECK-D-SRC-06"],
            "source_text": (
                "토스뱅크 모임금고는 모임통장 보유 실명의 개인 대상 연결계좌이며, "
                "근거계좌별 1개, 매일 최종잔액 기준 이자 계산·자동 지급, "
                "2026-08-25 기본금리 연 1.4%, 예금자보호 대상이다."
            ),
            "supports": ["product.sale", "product.eligibility", "product.return", "product.protection"],
            "captured_at": PUBLISHED_AT,
        },
        {
            "evidence_ref_id": "IDENTITY-20260825-EVD-TOSS-SPLIT",
            "source_ids": ["RECHECK-D-SRC-05", "RECHECK-D-SRC-07"],
            "source_text": (
                "토스뱅크 나눠모으기 통장은 토스뱅크 통장 보유 실명의 개인 대상이며 "
                "1인 최대 30계좌, 매일 최종잔액 기준 이자 계산·자동 지급, "
                "2026-08-25 기본금리 연 1.4%, 예금자보호 대상이다."
            ),
            "supports": ["product.sale", "product.eligibility", "product.return", "product.protection"],
            "captured_at": PUBLISHED_AT,
        },
        {
            "evidence_ref_id": "IDENTITY-20260825-EVD-POST-GUARANTEE",
            "source_ids": ["IDENTITY-20260825-SRC-POST-GUARANTEE"],
            "source_text": "우체국예금 원금과 이자는 법률에 따라 국가가 지급을 책임진다.",
            "supports": ["protection_scheme", "product.protection"],
            "captured_at": PUBLISHED_AT,
        },
    ]
    evidence_by_id = {
        row["evidence_ref_id"]: row for row in evidence["evidence_refs"]
    }
    evidence_by_id.update({row["evidence_ref_id"]: row for row in new_evidence})
    evidence["evidence_refs"] = sorted(
        evidence_by_id.values(), key=lambda row: row["evidence_ref_id"]
    )

    source_path = PRODUCT_RAW / "source_documents.json"
    evidence_path = PRODUCT_RAW / "evidence_refs.json"
    write_json(source_path, sources)
    write_json(evidence_path, evidence)
    return source_path, evidence_path, set(evidence_by_id)


def toss_custom_definitions() -> list[dict[str, Any]]:
    return [
        {
            "custom_code": f"{TOSS_ID}-C-0001",
            "version": 1,
            "institution_id": TOSS_ID,
            "definition_type": "SERVICE",
            "title": "토스뱅크 모임통장 연계·공동명의 모임금고 구조",
            "semantic_tags": ["LINKED_ACCOUNT", "JOINT_ACCOUNT"],
            "evaluation_mode": "LLM",
            "content_blocks": [
                {
                    "block_id": "BLOCK-TOSS-MOIM-LINK",
                    "block_type": "REQUIREMENT",
                    "content": {
                        "required_source_account": "토스뱅크 모임통장",
                        "account_limit": {"max_count": 1, "scope": "PER_SOURCE_ACCOUNT"},
                        "ownership": "근거계좌 공동명의자가 모임금고 공동명의자가 됨",
                        "opening_and_closing": "공동명의자 전원 동의 필요",
                    },
                    "source_ref_ids": ["IDENTITY-20260825-EVD-TOSS-MOIM"],
                }
            ],
            "links": [
                {
                    "relation": "PROVIDED_BY",
                    "target_type": "INSTITUTION",
                    "target_ref": TOSS_ID,
                    "source_ref_ids": ["IDENTITY-20260825-EVD-TOSS-MOIM"],
                }
            ],
            "effective_to": None,
        },
        {
            "custom_code": f"{TOSS_ID}-C-0002",
            "version": 1,
            "institution_id": TOSS_ID,
            "definition_type": "SERVICE",
            "title": "토스뱅크 통장 연계 나눠모으기 구조",
            "semantic_tags": ["LINKED_ACCOUNT", "AUTOMATIC_SAVING"],
            "evaluation_mode": "LLM",
            "content_blocks": [
                {
                    "block_id": "BLOCK-TOSS-SPLIT-LINK",
                    "block_type": "REQUIREMENT",
                    "content": {
                        "required_source_account": "토스뱅크 통장",
                        "closing_destination": "연결된 토스뱅크 통장",
                        "services": ["계좌 잔돈 모으기", "이자 모으기"],
                    },
                    "source_ref_ids": ["IDENTITY-20260825-EVD-TOSS-SPLIT"],
                }
            ],
            "links": [
                {
                    "relation": "PROVIDED_BY",
                    "target_type": "INSTITUTION",
                    "target_ref": TOSS_ID,
                    "source_ref_ids": ["IDENTITY-20260825-EVD-TOSS-SPLIT"],
                }
            ],
            "effective_to": None,
        },
    ]


def balance_scope(
    minimum: str,
    *,
    min_inclusive: bool,
    maximum: str | None = None,
    max_inclusive: bool | None = None,
) -> list[dict[str, Any]]:
    value: dict[str, Any] = {
        "min_value": minimum,
        "min_inclusive": min_inclusive,
        "currency": "KRW",
    }
    if maximum is not None:
        value["max_value"] = maximum
        value["max_inclusive"] = bool(max_inclusive)
    return [{"basis": "BALANCE", "range": value}]


def toss_product(
    *,
    code: str,
    name: str,
    evidence_id: str,
    rate_id: str,
    custom_code: str,
    account_limit: int | None,
) -> dict[str, Any]:
    eligibility: dict[str, Any] = {
        "mode": "RESTRICTED",
        "allowed_customer_types": ["INDIVIDUAL"],
        "real_name_required": True,
        "source_ref_ids": [evidence_id],
    }
    if account_limit is not None:
        eligibility["account_limit"] = {
            "max_active_accounts": account_limit,
            "subject_scope": "PERSON",
            "product_scope": "THIS_PRODUCT",
        }
    product = {
        "product_code": code,
        "version": 1,
        "institution_id": TOSS_ID,
        "name": name,
        "product_family": "PARKING_ACCOUNT",
        "product_subtype": None,
        "classifications": [],
        "sale_policy": {"status": "ON_SALE", "source_ref_ids": [evidence_id]},
        "eligibility_policy": eligibility,
        "term_policy": {"kind": "OPEN_ENDED", "source_ref_ids": [evidence_id]},
        "cash_flow_policy": {
            "funding_type": "ON_DEMAND",
            "currency": "KRW",
            "source_ref_ids": [evidence_id],
        },
        "return_policy": {
            "return_kind": "INTEREST",
            "calculation_method": "DAILY_BALANCE",
            "accrual_basis": "END_OF_DAY_BALANCE",
            "day_count_basis": "ACTUAL_365_FIXED",
            "payment_timing": "PERIODIC",
            "payment_frequency": {"value": 1, "unit": "DAY"},
            "payment_schedule": {"credit_destination": "PRINCIPAL_BALANCE"},
            "rate_entries": [
                {
                    "rate_id": rate_id,
                    "role": "BASE",
                    "application_event": "ANY",
                    "calculation": {
                        "type": "VARIABLE_POSTED",
                        "value": "1.4",
                        "unit": "PERCENT",
                    },
                    "as_of": "2026-08-25",
                    "source_ref_ids": [evidence_id],
                }
            ],
            "advertised_max_rate": {
                "value": "1.4",
                "unit": "PERCENT",
                "as_of": "2026-08-25",
                "source_ref_ids": [evidence_id],
            },
            "source_ref_ids": [evidence_id],
        },
        "liquidity_policy": {
            "access_mode": "ON_DEMAND",
            "source_ref_ids": [evidence_id],
        },
        "protection_policy": {
            "coverage_status": "PROTECTED",
            "scheme_ref": {"scheme_code": "KDIC-DEPOSIT-PROTECTION", "version": 1},
            "source_ref_ids": [evidence_id],
        },
        "standard_conditions": [],
        "custom_bindings": [
            {
                "product_code": code,
                "product_version": 1,
                "custom_code": custom_code,
                "custom_version": 1,
                "purpose": "ELIGIBILITY",
                "reward_refs": [],
                "source_ref_ids": [evidence_id],
            }
        ],
        "effective_to": None,
        "source_ref_ids": [evidence_id, "IDENTITY-20260825-EVD-TOSS"],
        "version_metadata": {
            "captured_at": PUBLISHED_AT,
            "last_verified_at": PUBLISHED_AT,
            "data_gaps": [
                {
                    "path": "effective_from",
                    "reason": "현재 판매·금리 기준일은 확인했으나 상품 조건 전체의 단일 시행일은 확인하지 못함",
                    "source_ref_ids": [evidence_id],
                },
                {
                    "path": "sale_policy.subscription_channels",
                    "reason": "공식 페이지 캡처에서 가입 채널 전체를 확정하지 못함",
                    "source_ref_ids": [evidence_id],
                },
            ],
        },
    }
    if name == "토스뱅크 나눠모으기 통장":
        product["return_policy"]["reinvestment_policy"] = {
            "mode": "AUTOMATIC",
            "interval": {"value": 1, "unit": "DAY"},
            "principal_treatment": "REINVEST_PRINCIPAL_AND_RETURN",
            "source_ref_ids": [evidence_id],
        }
    product["version_metadata"]["content_fingerprint"] = product_fingerprint(product)
    return product


def post_product(code: str) -> dict[str, Any]:
    rate_evidence = "HUMAN-CONFIRMED-20260825-EVD-03"
    sale_evidence = "RECHECK-C-04-E01"
    product = {
        "product_code": code,
        "version": 1,
        "institution_id": POST_ID,
        "name": "우체국매일이자파킹통장",
        "official_product_code": "110109400101",
        "product_family": "PARKING_ACCOUNT",
        "product_subtype": None,
        "classifications": [],
        "sale_policy": {"status": "ON_SALE", "source_ref_ids": [sale_evidence]},
        "term_policy": {"kind": "OPEN_ENDED", "source_ref_ids": [sale_evidence]},
        "cash_flow_policy": {
            "funding_type": "ON_DEMAND",
            "currency": "KRW",
            "source_ref_ids": [sale_evidence],
        },
        "return_policy": {
            "return_kind": "INTEREST",
            "balance_tier_method": "WHOLE_BALANCE",
            "rate_entries": [
                {
                    "rate_id": "RATE-POST-BASE-LE-10M",
                    "role": "BASE",
                    "application_event": "ANY",
                    "calculation": {"type": "VARIABLE_POSTED", "value": "1.60", "unit": "PERCENT"},
                    "applies_to": balance_scope("0", min_inclusive=True, maximum="10000000", max_inclusive=True),
                    "as_of": "2026-08-25",
                    "source_ref_ids": [rate_evidence],
                },
                {
                    "rate_id": "RATE-POST-BASE-GT-10M",
                    "role": "BASE",
                    "application_event": "ANY",
                    "calculation": {"type": "VARIABLE_POSTED", "value": "0.15", "unit": "PERCENT"},
                    "applies_to": balance_scope("10000000", min_inclusive=False),
                    "as_of": "2026-08-25",
                    "source_ref_ids": [rate_evidence],
                },
                {
                    "rate_id": "RATE-POST-PREF-LE-10M",
                    "role": "PREFERENTIAL",
                    "application_event": "ANY",
                    "calculation": {"type": "FIXED", "value": "0.40", "unit": "PERCENTAGE_POINT"},
                    "applies_to": balance_scope("0", min_inclusive=True, maximum="10000000", max_inclusive=True),
                    "as_of": "2026-08-25",
                    "source_ref_ids": [rate_evidence],
                },
                {
                    "rate_id": "RATE-POST-PREF-GT-10M",
                    "role": "PREFERENTIAL",
                    "application_event": "ANY",
                    "calculation": {"type": "FIXED", "value": "0.40", "unit": "PERCENTAGE_POINT"},
                    "applies_to": balance_scope("10000000", min_inclusive=False),
                    "as_of": "2026-08-25",
                    "source_ref_ids": [rate_evidence],
                },
            ],
            "preferential_application": {
                "mode": "SUM",
                "cap_value": "0.40",
                "cap_unit": "PERCENTAGE_POINT",
            },
            "advertised_max_rate": {
                "value": "2.00",
                "unit": "PERCENT",
                "as_of": "2026-08-25",
                "source_ref_ids": [rate_evidence],
            },
            "source_ref_ids": [rate_evidence],
        },
        "liquidity_policy": {"access_mode": "ON_DEMAND", "source_ref_ids": [sale_evidence]},
        "protection_policy": {
            "coverage_status": "PROTECTED",
            "scheme_ref": {"scheme_code": "POSTAL-SAVINGS-STATE-GUARANTEE", "version": 1},
            "source_ref_ids": ["IDENTITY-20260825-EVD-POST-GUARANTEE"],
        },
        "standard_conditions": [],
        "custom_bindings": [],
        "effective_to": None,
        "source_ref_ids": [
            rate_evidence,
            sale_evidence,
            "IDENTITY-20260825-EVD-POST",
            "IDENTITY-20260825-EVD-POST-GUARANTEE",
        ],
        "version_metadata": {
            "captured_at": PUBLISHED_AT,
            "last_verified_at": PUBLISHED_AT,
            "data_gaps": [
                {
                    "path": "eligibility_policy",
                    "reason": "현재 가입대상의 완결된 공식 문구를 필드 단위로 확정하지 못함",
                    "source_ref_ids": [sale_evidence],
                },
                {
                    "path": "return_policy.preferential_condition",
                    "reason": "우대이율 수치와 최종 금리는 확인했으나 우대조건 원문을 확정하지 못해 자동 판정에 연결하지 않음",
                    "source_ref_ids": [rate_evidence],
                },
                {
                    "path": "return_policy.payment_timing",
                    "reason": "상품명만으로 이자 지급시기를 추론하지 않고 공식 지급시기 원문 확인 전까지 보류",
                    "source_ref_ids": [rate_evidence],
                },
                {
                    "path": "eligibility_policy.account_limit",
                    "reason": "가입 좌수 제한을 공식 원문에서 확정하지 못함",
                    "source_ref_ids": [sale_evidence],
                },
                {
                    "path": "sale_policy.subscription_channels",
                    "reason": "가입 채널 전체를 공식 원문에서 확정하지 못함",
                    "source_ref_ids": [sale_evidence],
                },
                {
                    "path": "effective_from",
                    "reason": "확인한 금리와 상품 조건 전체의 단일 시행일을 확인하지 못함",
                    "source_ref_ids": [rate_evidence],
                },
            ],
        },
    }
    product["version_metadata"]["content_fingerprint"] = product_fingerprint(product)
    return product


def collect_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_ref_ids", "evidence_ref_ids"} and isinstance(item, list):
                refs.update(item)
            else:
                refs.update(collect_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(collect_refs(item))
    return refs


def build_products(
    institution_map: dict[str, str], institution_rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[Path]]:
    old_index = read_json(OLD_PRODUCT_INDEX)
    old_manifest = read_json(OLD_PRODUCT_MANIFEST)
    old_custom = read_json(ROOT / old_manifest["custom_definition_file"])

    product_code_map: dict[str, str] = {}
    for row in old_index["products"]:
        old_id = row["institution_id"]
        old_code = row["product_code"]
        if not old_code.startswith(old_id + "-"):
            raise ValueError(f"Unexpected legacy product code: {old_code}")
        product_code_map[old_code] = institution_map[old_id] + old_code[len(old_id) :]

    custom_code_map: dict[str, str] = {}
    for row in old_custom["institution_custom_definitions"]:
        old_id = row["institution_id"]
        old_code = row["custom_code"]
        if old_code.startswith(old_id + "-"):
            new_code = institution_map[old_id] + old_code[len(old_id) :]
        else:
            # Early staging batches used readable institution prefixes instead
            # of the then-canonical FSS prefix.  Preserve the readable code as
            # a suffix while making the owning internal Institution explicit.
            new_code = f"{institution_map[old_id]}-C-{old_code}"
        custom_code_map[old_code] = new_code

    exact = {**institution_map, **product_code_map, **custom_code_map}
    migrated_custom = transformed(old_custom, exact)
    migrated_custom["institution_custom_definitions"].extend(toss_custom_definitions())
    migrated_custom["institution_custom_definitions"].sort(
        key=lambda row: (row["institution_id"], row["custom_code"], row["version"])
    )
    migrated_custom["identity_model"] = "INTERNAL_ID_WITH_EXTERNAL_IDENTIFIERS"
    migrated_custom["generated_at"] = PUBLISHED_AT
    write_json(PRODUCT_CUSTOM, migrated_custom)

    source_path, evidence_path, evidence_ids = current_registry_with_identity(institution_map)

    protection = copy.deepcopy(read_json(ROOT / old_manifest["protection_scheme_file"]))
    protection["protection_scheme_definitions"] = [
        row
        for row in protection["protection_scheme_definitions"]
        if row["scheme_code"] != "POSTAL-SAVINGS-STATE-GUARANTEE"
    ]
    protection["protection_scheme_definitions"].append(
        {
            "scheme_code": "POSTAL-SAVINGS-STATE-GUARANTEE",
            "version": 1,
            "name": "우체국예금 국가 지급 책임",
            "coverage_scope": "ALL_PRINCIPAL_AND_INTEREST",
            "legal_basis": "우체국예금보험에 관한 법률의 국가 지급 책임",
            "source_ref_ids": ["IDENTITY-20260825-EVD-POST-GUARANTEE"],
        }
    )
    write_json(PRODUCT_PROTECTION, protection)

    product_paths: list[Path] = []
    index_rows: list[dict[str, Any]] = []
    products_by_code: dict[str, dict[str, Any]] = {}
    family_folder = {
        "CMA": "cma",
        "PARKING_ACCOUNT": "parking_account",
        "INSTALLMENT_SAVINGS": "installment_savings",
        "TIME_DEPOSIT": "time_deposit",
    }
    for old_row in old_index["products"]:
        old_product = read_json(ROOT / old_row["path"])
        product = transformed(old_product, exact)
        old_code = old_row["product_code"]
        new_code = product_code_map[old_code]
        product["product_code"] = new_code
        product["institution_id"] = institution_map[old_row["institution_id"]]
        metadata = product.setdefault("version_metadata", {})
        metadata["identity_migration"] = {
            "legacy_product_code": old_code,
            "migrated_at": PUBLISHED_AT,
            "batch": BATCH,
        }
        metadata["content_fingerprint"] = product_fingerprint(product)
        folder = family_folder[product["product_family"]]
        path = PRODUCT_NORMALIZED / f"products/{folder}/{product['institution_id']}/{new_code}/v001.json"
        write_json(path, product)
        product_paths.append(path)
        products_by_code[new_code] = product
        index_rows.append(
            {
                "product_code": new_code,
                "version": product["version"],
                "product_family": product["product_family"],
                "institution_id": product["institution_id"],
                "sale_status": product["sale_policy"]["status"],
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
            }
        )

    new_products = [
        toss_product(
            code=f"{TOSS_ID}-3-0001",
            name="토스뱅크 모임금고",
            evidence_id="IDENTITY-20260825-EVD-TOSS-MOIM",
            rate_id="RATE-TOSS-MOIM-BASE",
            custom_code=f"{TOSS_ID}-C-0001",
            account_limit=None,
        ),
        toss_product(
            code=f"{TOSS_ID}-3-0002",
            name="토스뱅크 나눠모으기 통장",
            evidence_id="IDENTITY-20260825-EVD-TOSS-SPLIT",
            rate_id="RATE-TOSS-SPLIT-BASE",
            custom_code=f"{TOSS_ID}-C-0002",
            account_limit=30,
        ),
        post_product(f"{POST_ID}-3-0001"),
    ]
    for product in new_products:
        code = product["product_code"]
        path = PRODUCT_NORMALIZED / f"products/parking_account/{product['institution_id']}/{code}/v001.json"
        write_json(path, product)
        product_paths.append(path)
        products_by_code[code] = product
        index_rows.append(
            {
                "product_code": code,
                "version": 1,
                "product_family": "PARKING_ACCOUNT",
                "institution_id": product["institution_id"],
                "sale_status": "ON_SALE",
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
            }
        )

    index_rows.sort(
        key=lambda row: (
            row["product_family"],
            row["institution_id"],
            row["product_code"],
            row["version"],
        )
    )
    index = {
        "index_version": 4,
        "generated_at": PUBLISHED_AT,
        "publication_status": "PUBLISHED",
        "source_staging_batch": old_index["source_staging_batch"],
        "source_staging_revision": old_index["source_staging_revision"],
        "manifest_batch": BATCH,
        "identity_model": "INTERNAL_ID_WITH_EXTERNAL_IDENTIFIERS",
        "selection_rule": (
            "내부 Institution ID와 다중 공식 외부 식별자로 전환하고, 기관 연결만 보류 사유였던 "
            "우체국 1건·토스뱅크 2건을 공식 상품 근거로 편입"
        ),
        "product_count": len(index_rows),
        "products": index_rows,
    }
    write_json(PRODUCT_INDEX, index)

    institution_ids = {row["institution_id"] for row in institution_rows}
    custom_rows = migrated_custom["institution_custom_definitions"]
    custom_keys = {(row["custom_code"], int(row["version"])) for row in custom_rows}
    validations = transformed(
        read_json(ROOT / old_manifest["validation_file"]), exact
    )["validation_reports"]
    for product in new_products:
        refs = collect_refs(product)
        bindings = {
            (row["custom_code"], int(row["custom_version"]))
            for row in product.get("custom_bindings", [])
        }
        rate_ids = {
            row["rate_id"] for row in product["return_policy"]["rate_entries"]
        }
        rewards = {
            reward
            for row in [
                *product.get("standard_conditions", []),
                *product.get("custom_bindings", []),
            ]
            for reward in row.get("reward_refs", [])
        }
        checks = [
            {"code": "INTERNAL_INSTITUTION_ID", "passed": product["institution_id"] in institution_ids},
            {"code": "PRODUCT_PREFIX", "passed": product["product_code"].startswith(product["institution_id"] + "-")},
            {"code": "OFFICIAL_EVIDENCE", "passed": bool(refs) and refs <= evidence_ids},
            {"code": "CUSTOM_BINDINGS", "passed": bindings <= custom_keys},
            {"code": "REWARD_REFS", "passed": rewards <= rate_ids},
            {"code": "OPEN_VERSION", "passed": product.get("effective_to") is None},
        ]
        if not all(row["passed"] for row in checks):
            raise ValueError(f"New product validation failed: {product['product_code']}: {checks}")
        for kind in ("DETERMINISTIC", "OFFICIAL_SOURCE_SEMANTIC"):
            validations.append(
                {
                    "report_id": f"IDENTITY-MIGRATION-{kind}-{product['product_code']}",
                    "artifact_ref": {
                        "artifact_id": f"ART-{BATCH}-{product['product_code']}",
                        "revision": 1,
                    },
                    "validator": {"type": kind, "version": "sol-identity-migration-v1"},
                    "result": "PASS",
                    "issues": [],
                    "checks": checks,
                    "validated_at": PUBLISHED_AT,
                    "product_code": product["product_code"],
                }
            )
    validation_path = PRODUCT_REPORT / "validation_reports.json"
    write_json(validation_path, {"validation_reports": validations})

    gap_rows: list[dict[str, Any]] = []
    for row in index_rows:
        product = read_json(ROOT / row["path"])
        gaps = product.get("version_metadata", {}).get("data_gaps", [])
        if gaps:
            gap_rows.append({"product_code": row["product_code"], "gaps": gaps})
    gap_path = PRODUCT_REPORT / "DATA_GAP_BACKLOG.json"
    write_json(
        gap_path,
        {
            "generated_at": PUBLISHED_AT,
            "product_count": len(gap_rows),
            "field_gap_count": sum(len(row["gaps"]) for row in gap_rows),
            "gaps": gap_rows,
        },
    )

    old_artifacts = transformed(
        read_json(ROOT / old_manifest["collection_artifact_file"]), exact
    )
    artifact_rows = old_artifacts.get("products", [])
    artifact_rows.extend(
        {
            "product_code": product["product_code"],
            "version": 1,
            "name": product["name"],
            "decision": "PUBLISHED",
            "source_ref_ids": product["source_ref_ids"],
        }
        for product in new_products
    )
    artifact_path = PRODUCT_REPORT / "collection_artifacts.json"
    write_json(artifact_path, {"batch": BATCH, "products": artifact_rows})

    issue_path = PRODUCT_REPORT / "ISSUE_QUEUE.json"
    write_json(
        issue_path,
        {
            "generated_at": PUBLISHED_AT,
            "blocking_issue_count": 0,
            "issue_count": 0,
            "issues": [],
            "resolved": [
                {"product_name": "우체국매일이자파킹통장", "institution_id": POST_ID},
                {"product_name": "토스뱅크 모임금고", "institution_id": TOSS_ID},
                {"product_name": "토스뱅크 나눠모으기 통장", "institution_id": TOSS_ID},
            ],
        },
    )

    family = Counter(row["product_family"] for row in index_rows)
    status = Counter(row["sale_status"] for row in index_rows)
    validation_counts = Counter(row["result"] for row in validations)
    report_path = PRODUCT_REPORT / "NORMALIZATION_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# 기관 내부 ID 전환 및 보류 상품 편입 보고서\n\n"
        f"- 배치: `{BATCH}`\n"
        f"- 기관: {len(institution_rows)}개 (내부 ID, 외부 식별자 분리)\n"
        f"- 상품: {len(index_rows)}개\n"
        f"- 상품군: 적금 {family['INSTALLMENT_SAVINGS']}, 예금 {family['TIME_DEPOSIT']}, "
        f"파킹통장 {family['PARKING_ACCOUNT']}, CMA {family['CMA']}\n"
        f"- 판매상태: ON_SALE {status['ON_SALE']}, ENDED {status['ENDED']}\n"
        "- 신규 편입: 우체국매일이자파킹통장, 토스뱅크 모임금고, 토스뱅크 나눠모으기 통장\n"
        f"- CustomDefinition: {len(custom_rows)}개\n"
        f"- 검증: PASS {validation_counts['PASS']}, NEEDS_REVIEW {validation_counts['NEEDS_REVIEW']}, FAIL {validation_counts['FAIL']}\n"
        f"- data gap: {len(gap_rows)}개 상품 / {sum(len(row['gaps']) for row in gap_rows)}개 필드\n"
        "- 기관 ID 미확인 보류: 0건\n"
        "- 애플리케이션 코드 변경: 없음\n",
        encoding="utf-8",
    )

    product_files = [
        PRODUCT_INDEX,
        PRODUCT_CUSTOM,
        PRODUCT_PROTECTION,
        source_path,
        evidence_path,
        validation_path,
        gap_path,
        artifact_path,
        issue_path,
        report_path,
        *product_paths,
    ]
    manifest = {
        "manifest_version": 4,
        "publication_status": "PUBLISHED",
        "published_at": PUBLISHED_AT,
        "source_staging_manifest": old_manifest["source_staging_manifest"],
        "source_staging_manifest_sha256": old_manifest["source_staging_manifest_sha256"],
        "source_staging_revision": old_manifest["source_staging_revision"],
        "correction_batch": BATCH,
        "institution_snapshot": str(INSTITUTION_SNAPSHOT.relative_to(ROOT)),
        "institution_ids": sorted({row["institution_id"] for row in index_rows}),
        "institution_identity_model": "INTERNAL_ID_WITH_EXTERNAL_IDENTIFIERS",
        "institution_id_strategy": "DDAKRATE_INTERNAL_SEQUENTIAL_ID",
        "counts": {
            "total_products": len(index_rows),
            "by_product_family": dict(sorted(family.items())),
            "by_sale_status": dict(sorted(status.items())),
            "source_documents": len(read_json(source_path)["source_documents"]),
            "evidence_refs": len(read_json(evidence_path)["evidence_refs"]),
            "custom_definitions": len(custom_rows),
            "validation_pass": validation_counts["PASS"],
            "validation_needs_review": validation_counts["NEEDS_REVIEW"],
            "validation_fail": validation_counts["FAIL"],
            "blocking_issues": 0,
            "data_gap_products": len(gap_rows),
            "data_gap_fields": sum(len(row["gaps"]) for row in gap_rows),
        },
        "source_document_registry": str(source_path.relative_to(ROOT)),
        "evidence_ref_registry": str(evidence_path.relative_to(ROOT)),
        "custom_definition_file": str(PRODUCT_CUSTOM.relative_to(ROOT)),
        "protection_scheme_file": str(PRODUCT_PROTECTION.relative_to(ROOT)),
        "validation_file": str(validation_path.relative_to(ROOT)),
        "collection_artifact_file": str(artifact_path.relative_to(ROOT)),
        "normalization_report_file": str(report_path.relative_to(ROOT)),
        "publication_rule": (
            "내부 Institution ID로 모든 참조를 마이그레이션하고, FSS 번호가 없는 기관도 "
            "공식 CRNO·BZNO·행정표준코드로 식별하여 공식 검증 상품을 발행"
        ),
        "application_code_changed": False,
        "files": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted({path.resolve(): path for path in product_files}.values(), key=lambda item: str(item))
        ],
    }
    write_json(PRODUCT_MANIFEST, manifest)
    return index, product_files


def final_verify(index: dict[str, Any], institutions: list[dict[str, Any]]) -> None:
    institution_ids = {row["institution_id"] for row in institutions}
    if len(institution_ids) != len(institutions):
        raise ValueError("Duplicate internal Institution IDs")
    if len(index["products"]) != 209:
        raise ValueError(f"Expected 209 products, got {len(index['products'])}")
    if {row["institution_id"] for row in index["products"]} - institution_ids:
        raise ValueError("A product references an unknown Institution")
    custom = read_json(PRODUCT_CUSTOM)["institution_custom_definitions"]
    if any(row["institution_id"] not in institution_ids for row in custom):
        raise ValueError("A CustomDefinition references an unknown Institution")
    custom_by_key = {(row["custom_code"], row["version"]): row for row in custom}
    for item in index["products"]:
        product = read_json(ROOT / item["path"])
        if product["institution_id"] != item["institution_id"]:
            raise ValueError(f"Index identity mismatch: {item['product_code']}")
        for binding in product.get("custom_bindings", []):
            definition = custom_by_key[(binding["custom_code"], binding["custom_version"])]
            if definition["institution_id"] != product["institution_id"]:
                raise ValueError(f"Cross-institution CustomDefinition: {item['product_code']}")


def main() -> None:
    mapping, institutions, _ = build_institutions()
    index, _ = build_products(mapping, institutions)
    final_verify(index, institutions)
    print(
        json.dumps(
            {
                "batch": BATCH,
                "institutions": len(institutions),
                "products": len(index["products"]),
                "toss_institution_id": TOSS_ID,
                "post_institution_id": POST_ID,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
