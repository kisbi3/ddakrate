from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from eligibility.adapters.rest import RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.web.runtime import WebRuntime, build_web_runtime
from eligibility.web.debug_trace import (
    DebugTraceStore,
    assemble_conversation_turns,
)
from eligibility.search.pre_search import (
    INITIAL_PRODUCT_TYPE_QUESTION,
    PRODUCT_TYPE,
    pre_search_answer_examples,
)
from eligibility.search.contribution import ContributionPlanner, term_summary
from eligibility.search.product_facts import project_product_search_facts


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    service: ApplicationService | None = None,
    runtime: WebRuntime | None = None,
) -> FastAPI:
    if runtime is None:
        if service is None:
            runtime = build_web_runtime()
        else:
            debug_trace_store = DebugTraceStore()
            gateways = {
                candidate
                for candidate in (
                    getattr(service.intent_parser, "gateway", None),
                    getattr(
                        getattr(service.question_planner, "question_generator", None),
                        "gateway",
                        None,
                    ),
                    getattr(
                        getattr(service.recommendation_service, "explainer", None),
                        "gateway",
                        None,
                    ),
                    getattr(service.conversation_orchestrator, "gateway", None),
                )
                if candidate is not None
            }
            for gateway in gateways:
                gateway.debug_observer = debug_trace_store.observer()
            runtime = WebRuntime(
                service=service,
                sample_user_id=None,
                user_data_mode="CONVERSATIONAL_INPUT",
                product_count=len(service.products),
                llm_enabled=service.conversation_orchestrator is not None,
                llm_provider="CUSTOM",
                llm_model="custom",
                llm_api_family="CUSTOM",
                llm_gateway=None,
                llm_configuration_error=None,
                debug_trace_store=debug_trace_store,
            )
    adapter = RestApplicationAdapter(runtime.service)
    catalog_contribution_planner = ContributionPlanner()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if runtime.debug_trace_store is not None:
                runtime.debug_trace_store.flush(timeout_seconds=5.0)

    app = FastAPI(
        title="Financial Eligibility Engine Web MVP",
        version="0.4.6-web-sprint1",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.adapter = adapter

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/debug", include_in_schema=False)
    def debug_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "debug.html")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "backend_version": "0.4.6"}

    @app.get("/api/runtime")
    def runtime_info() -> dict[str, Any]:
        return {
            "backend_version": "0.4.6",
            "web_sprint": "1",
            "sample_user_id": runtime.sample_user_id,
            "user_data_mode": runtime.user_data_mode,
            "product_count": runtime.product_count,
            "llm_enabled": runtime.llm_enabled,
            "llm_provider": runtime.llm_provider,
            "llm_model": runtime.llm_model,
            "llm_api_family": runtime.llm_api_family,
            "llm_configuration_error": runtime.llm_configuration_error,
            "debug_history": (
                runtime.debug_trace_store.persistence_status()
                if runtime.debug_trace_store is not None
                else {
                    "enabled": False,
                    "history_dir": None,
                    "error_count": 0,
                    "last_error": None,
                }
            ),
        }

    @app.get("/api/pre-search/initial-question", include_in_schema=False)
    def initial_pre_search_question() -> dict[str, Any]:
        """Expose the backend-owned first question before a session exists."""

        return {
            "question_id": f"PRESEARCH-{PRODUCT_TYPE}",
            "question_kind": "PRE_SEARCH_PROFILE",
            "question": INITIAL_PRODUCT_TYPE_QUESTION,
            "question_stage": "PRE_SEARCH",
            "pre_search_key": PRODUCT_TYPE,
            "answer_mode": "FREE_TEXT",
            "confirmation_required": False,
            "answer_examples": pre_search_answer_examples(PRODUCT_TYPE),
            "product_context": None,
        }

    def catalog_summary(product) -> dict[str, Any]:
        normalized = product.normalized
        metadata = product.metadata
        listing = normalized.listing_snapshot if normalized is not None else {}
        conditions = normalized.condition_snapshot if normalized is not None else {}
        eligibility_view = conditions.get("eligibility") or {}
        preferential_view = conditions.get("preferential_rate") or {}
        contribution_projection = catalog_contribution_planner.build(
            product,
            None,
        ).projection
        open_ended = (
            normalized is not None
            and normalized.term_policy.get("kind") == "OPEN_ENDED"
        )
        term_policy = normalized.term_policy if normalized is not None else {}
        if (
            term_policy.get("kind") == "RANGE"
            and term_policy.get("min_value") is not None
            and term_policy.get("max_value") is not None
        ):
            unit = {"DAY": "일", "WEEK": "주", "MONTH": "개월", "YEAR": "년"}.get(
                term_policy.get("unit"),
                "",
            )
            ranged_term_summary = (
                f"{term_policy['min_value']}~{term_policy['max_value']}{unit}"
            )
            source_expression = str(term_policy.get("source_expression") or "")
            if "전역예정일" in source_expression or "소집해제예정일" in source_expression:
                ranged_term_summary = f"전역 예정일까지 · {ranged_term_summary}"
        else:
            ranged_term_summary = None
        # Catalog cards compare the published rate at its representative term.
        # The contribution planner may choose a minimum selectable term for a
        # lump-sum deposit, which must not replace the term of that rate.
        catalog_term_summary = (
            "만기 없음"
            if open_ended
            else ranged_term_summary
            if ranged_term_summary is not None
            else term_summary(product.contract_term)
            if product.contract_term is not None
            else "기간 확인"
        )
        catalog_contribution_summary = (
            "수시입출금"
            if open_ended
            else contribution_projection.contribution_summary.replace(
                "자유 자유적립 납입",
                "자유 납입",
            )
        )
        performance_linked_cma = (
            product.product_type == "CMA"
            and normalized is not None
            and normalized.return_policy.get("return_kind") == "PERFORMANCE_LINKED"
        )
        return {
            "product_id": product.product_id,
            "institution_id": product.institution_id,
            "institution_name": (
                normalized.institution_name
                if normalized is not None
                else metadata.institution_name if metadata is not None else product.institution_id
            ),
            "institution_sector": (
                metadata.institution_sector if metadata is not None else "UNKNOWN"
            ),
            "product_name": product.name,
            "product_family": product.product_type,
            "product_subtype": normalized.product_subtype if normalized is not None else None,
            "sale_status": (
                normalized.sale_status
                if normalized is not None
                else metadata.sale_status.value if metadata is not None else "UNKNOWN"
            ),
            "base_rate": (
                None
                if performance_linked_cma
                else str(product.base_rate) if product.base_rate is not None else None
            ),
            "listing_base_rate": listing.get("base_rate"),
            "listing_advertised_max_rate": listing.get("advertised_max_rate"),
            "listing_provider": listing.get("provider"),
            "listing_as_of": listing.get("snapshot_at"),
            "eligibility_condition_status": eligibility_view.get("status"),
            "eligibility_condition_summary": eligibility_view.get("display_text"),
            "preferential_condition_status": preferential_view.get("status"),
            "preferential_condition_count": len(preferential_view.get("conditions") or []),
            "advertised_max_rate": (
                None
                if performance_linked_cma
                else str(product.advertised_max_rate)
                if product.advertised_max_rate is not None
                else None
            ),
            "return_kind": (
                normalized.return_policy.get("return_kind")
                if normalized is not None
                else "INTEREST"
            ),
            "term_summary": catalog_term_summary,
            "contribution_summary": catalog_contribution_summary,
            "maximum_deposit_summary": contribution_projection.maximum_deposit_summary,
            "planned_contribution_summary": (
                contribution_projection.planned_contribution_summary
            ),
            "data_gap_count": len(normalized.data_gaps) if normalized is not None else 0,
        }

    def public_value(value: Any) -> Any:
        """Remove ingestion/provenance internals from serving payloads."""

        # The application layer deliberately keeps monetary amounts and rates as
        # Decimals.  A few diagnostic and conversational payloads contain those
        # values outside Pydantic's JSON-mode serializers, so handing them
        # directly to Starlette's JSONResponse raises TypeError and turns an
        # otherwise successful request into HTTP 500.  Preserve precision by
        # exposing their canonical decimal representation rather than coercing
        # to float.
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            hidden_keys = {
                "candidate_id",
                "data_approval_status",
                "evidence_ref_ids",
                "field_confirmation_status",
                "institution_link_review",
                "luna_field_fill",
                "naver_detail_url",
                "naver_raw_text",
                "naver_structured_fill",
                "raw_product",
                "source_id",
                "source_authority_policy",
                "source_data_readiness",
                "source_record_id",
                "source_ref_ids",
                "source_text_sha256",
                "source_status",
                "source_system",
                "status_basis",
            }
            return {
                key: public_value(item)
                for key, item in value.items()
                if key not in hidden_keys and not key.startswith("naver_")
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [public_value(item) for item in value]
        return value

    def public_product_payload(product) -> dict[str, Any]:
        """Return catalog policies without ingestion/provenance internals.

        The serving identity is the canonical ``product_code``.  Raw source
        envelopes are useful for audit, but exposing them from a user-facing
        catalog endpoint leaks crawler-specific identifiers and implementation
        details.
        """

        normalized = product.normalized
        if normalized is None:
            return public_value(product.model_dump(mode="json"))

        return_policy = public_value(normalized.return_policy)
        if (
            product.product_type == "CMA"
            and normalized.return_policy.get("return_kind") == "PERFORMANCE_LINKED"
        ):
            return_policy = {
                key: value
                for key, value in return_policy.items()
                if key
                not in {
                    "advertised_max_rate",
                    "observed_rates_percent",
                    "rate_entries",
                }
            }
            return_policy["rate_entries"] = []

        return {
            "product_code": product.product_id,
            "institution_id": product.institution_id,
            "name": product.name,
            "product_family": product.product_type,
            "product_subtype": normalized.product_subtype,
            "version": normalized.version,
            "sale_policy": public_value(normalized.raw_product.get("sale_policy", {})),
            "eligibility_policy": public_value(normalized.eligibility_policy),
            "term_policy": public_value(normalized.term_policy),
            "cash_flow_policy": public_value(normalized.cash_flow_policy),
            "return_policy": return_policy,
            "fee_policy": public_value(normalized.fee_policy),
            "tax_policy": public_value(normalized.tax_policy),
            "liquidity_policy": public_value(normalized.liquidity_policy),
            "protection_policy": public_value(normalized.protection_policy),
            "standard_conditions": public_value(normalized.standard_conditions),
            "custom_bindings": public_value(normalized.custom_bindings),
            "condition_snapshot": public_value(normalized.condition_snapshot),
            "search_facts": public_value(
                project_product_search_facts(product).model_dump(mode="json")
            ),
        }

    @app.get("/api/catalog/products", include_in_schema=False)
    def catalog_products(
        institution_id: str | None = None,
        product_family: str | None = None,
    ) -> dict[str, Any]:
        products = list(runtime.service.products.values())
        if institution_id is not None:
            products = [item for item in products if item.institution_id == institution_id]
        if product_family is not None:
            products = [item for item in products if item.product_type == product_family]
        items = [catalog_summary(item) for item in products]
        return {"count": len(items), "items": items}

    @app.get("/api/catalog/products/{product_id}", include_in_schema=False)
    def catalog_product_detail(product_id: str) -> JSONResponse:
        product = runtime.service.products.get(product_id)
        if product is None:
            return JSONResponse(
                status_code=404,
                content={"error": "NOT_FOUND", "detail": f"Unknown product_id: {product_id}"},
            )
        normalized = product.normalized
        return JSONResponse(
            content=public_value({
                **catalog_summary(product),
                "normalized_version": normalized.version if normalized is not None else None,
                "product": public_product_payload(product),
                "search_facts": public_value(
                    project_product_search_facts(product).model_dump(mode="json")
                ),
                "custom_definitions": (
                    public_value(normalized.custom_definitions)
                    if normalized is not None
                    else []
                ),
                "data_gaps": (
                    public_value(normalized.data_gaps)
                    if normalized is not None
                    else []
                ),
                "official_sources": (
                    public_value(normalized.official_sources)
                    if normalized is not None
                    else []
                ),
            })
        )

    @app.get(
        "/api/catalog/institutions/{institution_id}/products",
        include_in_schema=False,
    )
    def catalog_products_by_institution(institution_id: str) -> dict[str, Any]:
        # No reverse catalog is stored; this is intentionally an institution_id
        # filter over the published ON_SALE runtime products.
        products = [
            item
            for item in runtime.service.products.values()
            if item.institution_id == institution_id
        ]
        return {"institution_id": institution_id, "count": len(products), "items": [catalog_summary(item) for item in products]}

    @app.get("/api/llm/health")
    def llm_health() -> dict[str, Any]:
        if runtime.llm_gateway is None:
            return {
                "configured": False,
                "healthy": False,
                "provider": runtime.llm_provider,
                "model": runtime.llm_model,
                "api_family": runtime.llm_api_family,
                "detail": runtime.llm_configuration_error or "LLM provider is not configured",
                "latency_ms": None,
            }
        status = runtime.llm_gateway.health_check()
        return {
            "configured": True,
            "healthy": status.healthy,
            "provider": status.provider,
            "model": status.model,
            "api_family": runtime.llm_api_family,
            "detail": status.detail,
            "latency_ms": status.latency_ms,
        }

    @app.get("/api/debug/sessions", include_in_schema=False)
    def debug_sessions() -> dict[str, Any]:
        sessions = runtime.service.list_debug_search_sessions()
        trace_stats = (
            runtime.debug_trace_store.session_stats()
            if runtime.debug_trace_store is not None
            else {}
        )
        for session in sessions:
            session_requests = (
                runtime.debug_trace_store.requests_for_session(
                    session["search_session_id"]
                )
                if runtime.debug_trace_store is not None
                else []
            )
            session.update(
                {
                    "source": "LIVE",
                    "read_only": False,
                    "resumable": True,
                    "turn_count": len(
                        assemble_conversation_turns(session_requests)
                    ),
                }
            )
            session.update(
                trace_stats.get(
                    session["search_session_id"],
                    {"request_count": 0, "llm_call_count": 0, "last_request_at": None},
                )
            )
        archived = (
            runtime.debug_trace_store.archived_session_summaries()
            if runtime.debug_trace_store is not None
            else []
        )
        live_ids = {session["search_session_id"] for session in sessions}
        archived_ids = {
            session["search_session_id"] for session in archived
        }
        for session in sessions:
            session["archive_available"] = (
                session["search_session_id"] in archived_ids
            )
        sessions.extend(
            session
            for session in archived
            if session["search_session_id"] not in live_ids
        )
        sessions.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return {
            "sessions": sessions,
            "runtime": {
                "llm_enabled": runtime.llm_enabled,
                "provider": runtime.llm_provider,
                "model": runtime.llm_model,
                "api_family": runtime.llm_api_family,
                "trace_capture_enabled": runtime.debug_trace_store is not None,
                "history": (
                    runtime.debug_trace_store.persistence_status()
                    if runtime.debug_trace_store is not None
                    else {"enabled": False}
                ),
            },
        }

    @app.get("/api/debug/sessions/{search_session_id}", include_in_schema=False)
    def debug_session(search_session_id: str) -> JSONResponse:
        try:
            snapshot = runtime.service.get_debug_search_snapshot(search_session_id)
        except KeyError as exc:
            archived = (
                runtime.debug_trace_store.archived_session_bundle(search_session_id)
                if runtime.debug_trace_store is not None
                else None
            )
            if archived is not None:
                archived.pop("audit_events", None)
                return JSONResponse(content=public_value(archived))
            return JSONResponse(
                status_code=404,
                content={"error": "NOT_FOUND", "detail": str(exc)},
            )
        requests = (
            runtime.debug_trace_store.requests_for_session(search_session_id)
            if runtime.debug_trace_store is not None
            else []
        )
        return JSONResponse(
            content=public_value({
                "search_session_id": search_session_id,
                "source": "LIVE",
                "read_only": False,
                "resumable": True,
                "requests": requests,
                "turns": assemble_conversation_turns(requests),
                "schemas": (
                    runtime.debug_trace_store.schema_registry()
                    if runtime.debug_trace_store is not None
                    else {}
                ),
                "snapshot": snapshot,
            })
        )

    @app.get(
        "/api/debug/sessions/{search_session_id}/audit",
        include_in_schema=False,
    )
    def debug_session_audit(search_session_id: str) -> JSONResponse:
        try:
            events = runtime.service.get_debug_audit_timeline(search_session_id)
        except KeyError as exc:
            events = (
                runtime.debug_trace_store.archived_audit_events(search_session_id)
                if runtime.debug_trace_store is not None
                else None
            )
            if events is not None:
                return JSONResponse(content=public_value({"events": events}))
            return JSONResponse(
                status_code=404,
                content={"error": "NOT_FOUND", "detail": str(exc)},
            )
        return JSONResponse(content=public_value({"events": events}))

    @app.get(
        "/api/debug/sessions/{search_session_id}/evaluations/{product_id}",
        include_in_schema=False,
    )
    def debug_session_evaluation(
        search_session_id: str,
        product_id: str,
    ) -> JSONResponse:
        try:
            evaluation = runtime.service.get_debug_candidate_evaluation(
                search_session_id,
                product_id,
            )
        except KeyError as exc:
            archived = (
                runtime.debug_trace_store.archived_session_bundle(search_session_id)
                if runtime.debug_trace_store is not None
                else None
            )
            archived_evaluation = (
                archived.get("snapshot", {})
                .get("engine", {})
                .get("evaluation", {})
                .get("output", {})
                .get(product_id)
                if archived is not None
                else None
            )
            if archived_evaluation is not None:
                return JSONResponse(content=public_value(archived_evaluation))
            return JSONResponse(
                status_code=404,
                content={"error": "NOT_FOUND", "detail": str(exc)},
            )
        return JSONResponse(content=public_value(evaluation))

    def debug_request_state(search_session_id: str | None) -> dict[str, Any] | None:
        """Capture a compact, point-in-time state for one traced Web request."""

        if not search_session_id:
            return None
        try:
            return runtime.service.get_debug_request_state(search_session_id)
        except KeyError:
            return None

    def debug_events_since(
        search_session_id: str | None,
        start_index: int,
    ) -> list[dict[str, Any]]:
        if not search_session_id:
            return []
        try:
            events = runtime.service.get_debug_audit_timeline(search_session_id)
        except KeyError:
            return []
        # One search may emit hundreds of repeated per-product/per-rule events.
        # Keep their execution evidence without making every request trace a
        # multi-megabyte payload: preserve first-seen order and one redacted
        # sample while counting repetitions of the same component/event pair.
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events[start_index:]:
            key = (
                str(event.get("component") or "UNKNOWN"),
                str(event.get("event_type") or "UNKNOWN"),
            )
            row = grouped.get(key)
            if row is None:
                grouped[key] = {
                    "first_occurred_at": event.get("occurred_at"),
                    "last_occurred_at": event.get("occurred_at"),
                    "component": key[0],
                    "event_type": key[1],
                    "count": 1,
                    "sample_entity_refs": event.get("entity_refs") or {},
                    "sample_payload": event.get("payload") or {},
                }
                continue
            row["count"] += 1
            row["last_occurred_at"] = event.get("occurred_at")
        return list(grouped.values())

    def persist_debug_history_snapshot(
        search_session_id: str | None,
        *,
        state_before: dict[str, Any] | None,
        state_after: dict[str, Any] | None,
        method: str,
    ) -> None:
        """Keep one complete latest snapshot beside the append-only request log."""

        if runtime.debug_trace_store is None or not search_session_id:
            return
        # Ordinary presentation GETs do not need to rewrite a potentially large
        # 153-product snapshot.  A state-changing GET (for example selecting the
        # next question) is still persisted because its before/after view differs.
        if method == "GET" and state_before == state_after:
            return
        try:
            snapshot = runtime.service.get_debug_history_snapshot(search_session_id)
        except KeyError:
            # DELETE keeps the last pre-close snapshot; its completed DELETE
            # request is still appended to requests.jsonl.
            return
        runtime.debug_trace_store.persist_session_snapshot(
            search_session_id,
            snapshot=snapshot,
            # The append-only request log already preserves grouped application
            # events. Persist only the compact recent timeline here instead of
            # duplicating the complete raw audit history in every snapshot.
            audit_events=snapshot.get("audit_timeline") or [],
        )

    @app.api_route(
        "/api/{api_path:path}",
        methods=["GET", "POST", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy_application_api(api_path: str, request: Request) -> Response:
        payload: dict[str, Any] | None = None
        if request.method in {"POST", "PATCH"}:
            if request.headers.get("content-length") not in {None, "0"}:
                try:
                    payload = await request.json()
                except Exception:
                    return JSONResponse(
                        status_code=400,
                        content={"error": "BAD_REQUEST", "detail": "Invalid JSON body"},
                    )
        session_match = re.match(r"search-sessions/([^/]+)", api_path)
        known_session_id = session_match.group(1) if session_match else None
        trace_context = (
            runtime.debug_trace_store.request(
                method=request.method,
                path=f"/api/{api_path}",
                payload=payload,
                search_session_id=known_session_id,
            )
            if runtime.debug_trace_store is not None
            else None
        )
        if trace_context is None:
            try:
                response = adapter.handle(request.method, f"/{api_path}", payload)
            except Exception as exc:  # provider/runtime errors are not business-state 400s
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "RUNTIME_UNAVAILABLE",
                        "detail": str(exc),
                        "llm_enabled": runtime.llm_enabled,
                    },
                )
        else:
            with trace_context as debug_request_id:
                state_before = debug_request_state(known_session_id)
                audit_start_index = (
                    state_before.get("audit_event_count", 0)
                    if state_before is not None
                    else 0
                )
                try:
                    response = adapter.handle(request.method, f"/{api_path}", payload)
                except Exception as exc:
                    state_after = debug_request_state(known_session_id)
                    runtime.debug_trace_store.record_execution(
                        debug_request_id,
                        state_before=state_before,
                        state_after=state_after,
                        application_events=debug_events_since(
                            known_session_id,
                            audit_start_index,
                        ),
                    )
                    runtime.debug_trace_store.finish_request(
                        debug_request_id,
                        status_code=503,
                        response_payload={
                            "error": "RUNTIME_UNAVAILABLE",
                            "detail": str(exc),
                            "llm_enabled": runtime.llm_enabled,
                        },
                        search_session_id=known_session_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    persist_debug_history_snapshot(
                        known_session_id,
                        state_before=state_before,
                        state_after=state_after,
                        method=request.method,
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "RUNTIME_UNAVAILABLE",
                            "detail": str(exc),
                            "llm_enabled": runtime.llm_enabled,
                        },
                    )
                response_session_id = known_session_id or response.body.get(
                    "search_session_id"
                )
                state_after = debug_request_state(response_session_id)
                runtime.debug_trace_store.record_execution(
                    debug_request_id,
                    state_before=state_before,
                    state_after=state_after,
                    application_events=debug_events_since(
                        response_session_id,
                        audit_start_index,
                    ),
                )
                runtime.debug_trace_store.finish_request(
                    debug_request_id,
                    status_code=response.status_code,
                    response_payload=response.body,
                    search_session_id=response_session_id,
                )
                persist_debug_history_snapshot(
                    response_session_id,
                    state_before=state_before,
                    state_after=state_after,
                    method=request.method,
                )
        if response.status_code == 204:
            return Response(status_code=204)
        # Application endpoints may contain source references for audit and
        # recommendation explanations.  Keep those internally, but remove
        # crawler/source identifiers from the public service response just as
        # the catalog detail endpoint does.
        return JSONResponse(
            status_code=response.status_code,
            content=public_value(response.body),
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("WEB_PORT", "8000")))
    uvicorn.run("eligibility.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
