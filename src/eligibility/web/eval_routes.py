# Web eval harness. Disabled unless ELIGIBILITY_EVAL_TOKEN is set, because
# these endpoints drive the real conversation service.
#
# Lets an external client (a ChatGPT custom GPT "Action", or a plain browser)
# drive the *real* conversation service over multiple turns. Three GET
# endpoints plus a self-describing OpenAPI document:
#
#   GET /__eval/openapi.json          -> OpenAPI 3.1 spec for the GPT Action
#   GET /__eval/chat?session_id=..&message=..[&reset=1][&fmt=json|text]
#   GET /__eval/state?session_id=..[&fmt=json|text]
#   GET /__eval/reset?session_id=..
#
# Auth: token via header "X-Eval-Token: <t>", or "Authorization: Bearer <t>",
# or "?token=<t>" query (browser convenience).
#
# No parallel chat logic lives here: every call goes through the same
# RestApplicationAdapter.handle(...) surface the frontend "send" button uses.
#
# To enable:  export ELIGIBILITY_EVAL_TOKEN='<a fresh random token>'
# To remove:  delete this file and the mount block in app.py.

from __future__ import annotations

import hmac
import os
import uuid
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

# Supplied via ELIGIBILITY_EVAL_TOKEN. Never hardcode a token here: these
# endpoints drive the real conversation service, so a token in git is a
# credential in git.
_EVAL_TOKEN = os.environ.get("ELIGIBILITY_EVAL_TOKEN", "")

# alias (caller-chosen, e.g. "mt001") -> real search_session_id
_SESSIONS: dict[str, str] = {}


def _check_token(request: Request, token_qs: str | None) -> None:
    if not _EVAL_TOKEN:
        raise HTTPException(status_code=403, detail="eval harness not configured")
    supplied = token_qs
    if supplied is None:
        header = request.headers.get("x-eval-token")
        if header:
            supplied = header
    if supplied is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:]
    if supplied is None or not hmac.compare_digest(supplied, _EVAL_TOKEN):
        raise HTTPException(status_code=403, detail="bad or missing token")


def _render_text(p: dict[str, Any]) -> str:
    """Compact, browser-friendly transcript. Avoids the multi-KB id arrays
    that make the JSON form hard for a browsing tool to fetch/read."""
    lines: list[str] = []
    status = p.get("status") or {}
    lines.append(f"session_id      : {p.get('session_id')}")
    lines.append(f"search_session  : {p.get('search_session_id')}")
    lines.append(f"status          : {status.get('status')}"
                 + (f" ({status.get('completion_reason')})"
                    if status.get("completion_reason") else ""))
    if p.get("user_message") is not None:
        lines.append(f"user_message    : {p.get('user_message')}")
    turn = p.get("turn_result") or {}
    if isinstance(turn, dict) and turn.get("operations_executed"):
        lines.append(f"operations      : {', '.join(turn['operations_executed'])}")

    intent = ((p.get("state") or {}).get("intent")) or {}
    cp = intent.get("contribution_plan") or {}
    lines.append("")
    lines.append("[state]")
    lines.append(f"  product_types : {intent.get('product_types')}")
    if cp:
        lines.append(f"  amount        : {cp.get('desired_periodic_amount')} {cp.get('currency') or ''}".rstrip())
        lines.append(f"  term          : {cp.get('selected_term_value')} {cp.get('selected_term_unit')} "
                     f"({cp.get('term_strictness')}, freq={cp.get('frequency')})")
    if intent.get("numeric_preferences"):
        lines.append(f"  numeric_prefs : {intent['numeric_preferences']}")
    if intent.get("excluded_institution_ids"):
        lines.append(f"  excluded_inst : {intent['excluded_institution_ids']}")
    if intent.get("hard_constraints"):
        lines.append(f"  hard_constr   : {intent['hard_constraints']}")

    nq = p.get("next_question") or {}
    lines.append("")
    q = nq.get("question") if isinstance(nq, dict) else None
    lines.append(f"[next_question] {q if q else '(none - no further questions)'}")
    if isinstance(nq, dict) and nq.get("answer_examples"):
        for ex in nq["answer_examples"][:6]:
            txt = ex.get("text") if isinstance(ex, dict) else ex
            lines.append(f"  e.g. {txt}")

    recs = p.get("recommendations") or {}
    tp = recs.get("top_products") if isinstance(recs, dict) else None
    lines.append("")
    lines.append(f"[recommendations] {len(tp) if tp else 0}")
    for it in (tp or []):
        lines.append(
            f"  #{it.get('rank')} {it.get('product_name')} / {it.get('institution_name')}"
            f"  {it.get('published_rate_summary') or it.get('realizable_rate')}"
            f"  {it.get('term_summary')}  [{it.get('eligibility_badge')}]"
        )
        lines.append(
            f"      realizable={it.get('realizable_rate')}  base={it.get('base_rate')}  "
            f"adv_max={it.get('advertised_max_rate')}  "
            f"after_tax_interest={it.get('estimated_after_tax_interest')}"
        )
    return "\n".join(lines) + "\n"


def _openapi_doc(server_url: str) -> dict[str, Any]:
    alias = {
        "name": "session_id",
        "in": "query",
        "required": True,
        "schema": {"type": "string"},
        "description": "Caller-chosen conversation alias, e.g. mt001. Reuse it to continue the same multi-turn conversation.",
    }
    fmt = {
        "name": "fmt",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "enum": ["json", "text"], "default": "json"},
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Ddakrate Eval Harness",
            "version": "0.1.0",
            "description": "Temporary test surface over the real conversation service. Drive a multi-turn savings/deposit consultation and inspect internal state.",
        },
        "servers": [{"url": server_url}],
        "components": {
            "securitySchemes": {
                "EvalToken": {"type": "apiKey", "in": "header", "name": "X-Eval-Token"}
            }
        },
        "security": [{"EvalToken": []}],
        "paths": {
            "/__eval/chat": {
                "get": {
                    "operationId": "chat",
                    "summary": "Send one user turn (creates the session on first call) and return the assistant question, internal state, and current recommendations.",
                    "parameters": [
                        alias,
                        {
                            "name": "message",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "The user's utterance for this turn. Omit on the very first call to just open a session.",
                        },
                        {
                            "name": "reset",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "enum": [0, 1], "default": 0},
                            "description": "1 = discard any existing session for this alias before processing.",
                        },
                        fmt,
                    ],
                    "responses": {"200": {"description": "Turn processed"}},
                }
            },
            "/__eval/state": {
                "get": {
                    "operationId": "getState",
                    "summary": "Return current internal state, next question, and recommendations without sending a turn.",
                    "parameters": [alias, fmt],
                    "responses": {"200": {"description": "Current snapshot"}},
                }
            },
            "/__eval/reset": {
                "get": {
                    "operationId": "reset",
                    "summary": "Close and forget the session for this alias.",
                    "parameters": [alias],
                    "responses": {"200": {"description": "Cleared"}},
                }
            },
        },
    }


def mount_eval_routes(app: FastAPI, adapter: Any) -> None:
    def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        resp = adapter.handle(method, path, payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.body)
        return resp.body

    def _new_session(alias: str, first_message: str | None) -> str:
        today = date.today().isoformat()
        body = _call(
            "POST",
            "/search-sessions",
            {
                "user_id": f"eval-{alias}-{uuid.uuid4().hex[:8]}",
                "natural_language_query": first_message or None,
                "as_of": today,
                "subscription_date": today,
            },
        )
        sid = body["search_session_id"]
        _SESSIONS[alias] = sid
        return sid

    def _snapshot(sid: str) -> dict[str, Any]:
        def _safe(method: str, path: str) -> Any:
            try:
                return _call(method, path, None)
            except HTTPException as exc:
                return {"error": exc.detail}

        return {
            "state": _safe("GET", f"/search-sessions/{sid}/state"),
            "status": _safe("GET", f"/search-sessions/{sid}"),
            "next_question": _safe("GET", f"/search-sessions/{sid}/questions/next"),
            "recommendations": _safe("GET", f"/search-sessions/{sid}/recommendations"),
        }

    @app.get("/__eval/openapi.json", include_in_schema=False)
    def eval_openapi(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return _openapi_doc(base)

    @app.get("/__eval/chat", include_in_schema=False)
    def eval_chat(
        request: Request,
        session_id: str = Query(..., description="caller-chosen alias"),
        message: str | None = Query(None),
        reset: int = Query(0),
        fmt: str = Query("text", description="text | json"),
        token: str | None = Query(None),
    ) -> Any:
        _check_token(request, token)

        if reset:
            old = _SESSIONS.pop(session_id, None)
            if old:
                try:
                    _call("DELETE", f"/search-sessions/{old}", None)
                except HTTPException:
                    pass

        turn: Any = None
        if session_id not in _SESSIONS:
            sid = _new_session(session_id, message)
            turn = {"created": True}
        else:
            sid = _SESSIONS[session_id]
            if message is not None:
                turn = _call(
                    "POST",
                    f"/search-sessions/{sid}/messages",
                    {"message": message},
                )

        payload = {
            "session_id": session_id,
            "search_session_id": sid,
            "user_message": message,
            "turn_result": turn,
            **_snapshot(sid),
        }
        if fmt == "json":
            return payload
        return PlainTextResponse(_render_text(payload))

    @app.get("/__eval/state", include_in_schema=False)
    def eval_state(
        request: Request,
        session_id: str = Query(...),
        fmt: str = Query("text", description="text | json"),
        token: str | None = Query(None),
    ) -> Any:
        _check_token(request, token)
        sid = _SESSIONS.get(session_id)
        if sid is None:
            raise HTTPException(status_code=404, detail="unknown alias")
        payload = {"session_id": session_id, "search_session_id": sid, **_snapshot(sid)}
        if fmt == "json":
            return payload
        return PlainTextResponse(_render_text(payload))

    @app.get("/__eval/reset", include_in_schema=False)
    def eval_reset(
        request: Request,
        session_id: str = Query(...),
        token: str | None = Query(None),
    ) -> dict[str, Any]:
        _check_token(request, token)
        sid = _SESSIONS.pop(session_id, None)
        if sid is not None:
            try:
                _call("DELETE", f"/search-sessions/{sid}", None)
            except HTTPException:
                pass
        return {"session_id": session_id, "cleared": sid is not None}
