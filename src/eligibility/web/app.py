from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from eligibility.adapters.rest import RestApplicationAdapter
from eligibility.application_service import ApplicationService
from eligibility.web.runtime import WebRuntime, build_web_runtime


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    service: ApplicationService | None = None,
    runtime: WebRuntime | None = None,
) -> FastAPI:
    if runtime is None:
        runtime = build_web_runtime() if service is None else WebRuntime(
            service=service,
            sample_user_id=os.environ.get("WEB_SAMPLE_USER_ID", "U001"),
            product_count=len(service.products),
            llm_enabled=service.conversation_orchestrator is not None,
            llm_provider="CUSTOM",
            llm_model="custom",
            llm_api_family="CUSTOM",
            llm_gateway=None,
            llm_configuration_error=None,
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

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "backend_version": "0.4.6"}

    @app.get("/api/runtime")
    def runtime_info() -> dict[str, Any]:
        return {
            "backend_version": "0.4.6",
            "web_sprint": "1",
            "sample_user_id": runtime.sample_user_id,
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

    @app.api_route(
        "/api/{api_path:path}",
        methods=["GET", "POST", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy_application_api(api_path: str, request: Request) -> JSONResponse:
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
