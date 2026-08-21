from __future__ import annotations

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
from eligibility.web.debug_trace import DebugTraceStore


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

    app = FastAPI(
        title="Financial Eligibility Engine Web MVP",
        version="0.4.6-web-sprint1",
        docs_url="/api/docs",
        redoc_url=None,
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
        }

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
            session.update(
                trace_stats.get(
                    session["search_session_id"],
                    {"request_count": 0, "llm_call_count": 0, "last_request_at": None},
                )
            )
        return {
            "sessions": sessions,
            "runtime": {
                "llm_enabled": runtime.llm_enabled,
                "provider": runtime.llm_provider,
                "model": runtime.llm_model,
                "api_family": runtime.llm_api_family,
                "trace_capture_enabled": runtime.debug_trace_store is not None,
            },
        }

    @app.get("/api/debug/sessions/{search_session_id}", include_in_schema=False)
    def debug_session(search_session_id: str) -> JSONResponse:
        try:
            snapshot = runtime.service.get_debug_search_snapshot(search_session_id)
        except KeyError as exc:
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
            content={
                "search_session_id": search_session_id,
                "requests": requests,
                "snapshot": snapshot,
            }
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
                try:
                    response = adapter.handle(request.method, f"/{api_path}", payload)
                except Exception as exc:
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
                runtime.debug_trace_store.finish_request(
                    debug_request_id,
                    status_code=response.status_code,
                    response_payload=response.body,
                    search_session_id=response_session_id,
                )
        if response.status_code == 204:
            return Response(status_code=204)
        return JSONResponse(status_code=response.status_code, content=response.body)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("WEB_PORT", "8000")))
    uvicorn.run("eligibility.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
