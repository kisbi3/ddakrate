from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from eligibility.application_service import ApplicationService
from eligibility.schema.application_input import QuickInputProfile
from eligibility.schema.search import IntentPatch


class RestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int
    body: dict[str, Any]


class RestApplicationAdapter:
    """Framework-neutral REST contract over the shared ApplicationService."""

    def __init__(self, service: ApplicationService) -> None:
        self.service = service

    def handle(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> RestResponse:
        method = method.upper()
        payload = payload or {}
        try:
            if method == "POST" and path == "/search-sessions":
                utterance = payload.get("natural_language_query", payload.get("utterance"))
                quick_input = (
                    QuickInputProfile.model_validate(payload["quick_input"])
                    if payload.get("quick_input") is not None
                    else None
                )
                session = self.service.create_search_session(
                    user_id=payload["user_id"],
                    utterance=utterance,
                    quick_input=quick_input,
                    as_of=date.fromisoformat(payload["as_of"]) if payload.get("as_of") else None,
                    subscription_date=(
                        date.fromisoformat(payload["subscription_date"])
                        if payload.get("subscription_date")
                        else None
                    ),
                )
                initial_question_id = payload.get("initial_pre_search_question_id")
                if (
                    initial_question_id
                    and utterance
                    and session.active_question_id == initial_question_id
                ):
                    # The Web UI displays the backend-owned first pre-search
                    # question before a SearchSession exists. If the general
                    # initial intent parser did not resolve that field, route
                    # the same utterance through the focused active-question
                    # interpreter so the user never sees a duplicate question.
                    session = self.service.handle_user_message(
                        session.search_session_id,
                        message=utterance,
                    ).session
                return RestResponse(status_code=201, body=session.model_dump(mode="json"))


            match = re.fullmatch(r"/search-sessions/([^/]+)/clarifications/current", path)
            if method == "GET" and match:
                clarification = self.service.get_clarification_request(match.group(1))
                return RestResponse(
                    status_code=200,
                    body=(
                        clarification.model_dump(mode="json")
                        if clarification is not None
                        else {"clarification": None}
                    ),
                )

            match = re.fullmatch(r"/search-sessions/([^/]+)/clarifications", path)
            if method == "POST" and match:
                session = self.service.resolve_intent_conflict(
                    match.group(1),
                    conflict_id=payload["conflict_id"],
                    resolution=payload["resolution"],
                )
                return RestResponse(status_code=200, body=session.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/answers", path)
            if method == "POST" and match:
                session = self.service.submit_user_answer(
                    match.group(1),
                    answer=payload["answer"],
                    question_id=payload.get("question_id"),
                )
                return RestResponse(status_code=200, body=session.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/answers/([^/]+)", path)
            if method == "PATCH" and match:
                session = self.service.revise_user_answer(
                    match.group(1),
                    request_reference=match.group(2),
                    new_value=payload["new_value"],
                )
                return RestResponse(status_code=200, body=session.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/intent", path)
            if method == "PATCH" and match:
                patch = (
                    IntentPatch.model_validate(payload["patch"])
                    if payload.get("patch") is not None
                    else None
                )
                session = self.service.update_search_intent(
                    match.group(1),
                    utterance=payload.get("utterance"),
                    patch=patch,
                )
                return RestResponse(status_code=200, body=session.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/messages", path)
            if method == "POST" and match:
                result = self.service.handle_user_message(
                    match.group(1),
                    message=payload["message"],
                )
                return RestResponse(status_code=200, body=result.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/semantic-review", path)
            if method == "POST" and match:
                retry = payload.get("retry_failed", False)
                if not isinstance(retry, bool):
                    raise ValueError("retry_failed must be a JSON boolean")
                result = self.service.advance_semantic_review(match.group(1), retry_failed=retry)
                return RestResponse(status_code=200, body=result.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/state", path)
            if method == "GET" and match:
                state = self.service.get_mutable_search_state(match.group(1))
                return RestResponse(status_code=200, body=state)

            match = re.fullmatch(r"/search-sessions/([^/]+)", path)
            if method == "GET" and match:
                status = self.service.get_search_status(match.group(1))
                return RestResponse(status_code=200, body=status.model_dump(mode="json"))
            if method == "DELETE" and match:
                self.service.close_search_session(match.group(1))
                return RestResponse(status_code=204, body={})

            match = re.fullmatch(r"/search-sessions/([^/]+)/recommendations", path)
            if method == "GET" and match:
                result = self.service.get_top_recommendations(match.group(1))
                return RestResponse(status_code=200, body=result.model_dump(mode="json"))

            match = re.fullmatch(
                r"/search-sessions/([^/]+)/recommendations/([^/]+)/preview", path
            )
            if method == "GET" and match:
                detail = self.service.get_product_recommendation_detail(
                    match.group(1), match.group(2), include_explanation=False
                )
                return RestResponse(status_code=200, body=detail.model_dump(mode="json"))

            match = re.fullmatch(
                r"/search-sessions/([^/]+)/recommendations/([^/]+)", path
            )
            if method == "GET" and match:
                detail = self.service.get_product_recommendation_detail(
                    match.group(1), match.group(2), include_explanation=False
                )
                return RestResponse(status_code=200, body=detail.model_dump(mode="json"))

            match = re.fullmatch(r"/search-sessions/([^/]+)/questions/next", path)
            if method == "GET" and match:
                question = self.service.get_next_question(match.group(1))
                return RestResponse(
                    status_code=200,
                    body=(question.model_dump(mode="json") if question else {"question": None}),
                )

            match = re.fullmatch(r"/search-sessions/([^/]+)/trace", path)
            if method == "GET" and match:
                events = self.service.get_evaluation_trace(match.group(1))
                return RestResponse(
                    status_code=200,
                    body={"events": [event.model_dump(mode="json") for event in events]},
                )

            return RestResponse(
                status_code=404,
                body={"error": "ROUTE_NOT_FOUND", "method": method, "path": path},
            )
        except KeyError as exc:
            return RestResponse(status_code=404, body={"error": "NOT_FOUND", "detail": str(exc)})
        except (TypeError, ValueError) as exc:
            return RestResponse(status_code=400, body={"error": "BAD_REQUEST", "detail": str(exc)})
