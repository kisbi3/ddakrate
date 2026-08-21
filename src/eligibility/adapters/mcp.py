from __future__ import annotations

from datetime import date
from typing import Any

from eligibility.application_service import ApplicationService
from eligibility.schema.application_input import QuickInputProfile
from eligibility.schema.search import IntentPatch


class MCPToolAdapter:
    """Thin tool-style adapter; no financial logic is implemented here."""

    def __init__(self, service: ApplicationService) -> None:
        self.service = service

    def search_products(
        self,
        *,
        user_id: str,
        utterance: str,
        quick_input: dict[str, Any] | None = None,
        as_of: str | None = None,
        subscription_date: str | None = None,
    ) -> dict[str, Any]:
        session = self.service.create_search_session(
            user_id=user_id,
            utterance=utterance,
            quick_input=(
                QuickInputProfile.model_validate(quick_input)
                if quick_input is not None
                else None
            ),
            as_of=date.fromisoformat(as_of) if as_of else None,
            subscription_date=date.fromisoformat(subscription_date) if subscription_date else None,
        )
        return session.model_dump(mode="json")

    def get_search_status(self, *, search_session_id: str) -> dict[str, Any]:
        return self.service.get_search_status(search_session_id).model_dump(mode="json")

    def get_current_clarification(self, *, search_session_id: str) -> dict[str, Any]:
        clarification = self.service.get_clarification_request(search_session_id)
        return (
            clarification.model_dump(mode="json")
            if clarification is not None
            else {"clarification": None}
        )

    def get_next_question(self, *, search_session_id: str) -> dict[str, Any]:
        question = self.service.get_next_question(search_session_id)
        return question.model_dump(mode="json") if question else {"question": None}

    def submit_user_fact(
        self,
        *,
        search_session_id: str,
        answer: Any,
        question_id: str | None = None,
    ) -> dict[str, Any]:
        return self.service.submit_user_answer(
            search_session_id,
            answer=answer,
            question_id=question_id,
        ).model_dump(mode="json")

    def revise_user_fact(
        self,
        *,
        search_session_id: str,
        request_reference: str,
        new_value: Any,
    ) -> dict[str, Any]:
        return self.service.revise_user_answer(
            search_session_id,
            request_reference=request_reference,
            new_value=new_value,
        ).model_dump(mode="json")

    def update_search_intent(
        self,
        *,
        search_session_id: str,
        utterance: str | None = None,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.service.update_search_intent(
            search_session_id,
            utterance=utterance,
            patch=(IntentPatch.model_validate(patch) if patch is not None else None),
        ).model_dump(mode="json")

    def handle_user_message(
        self,
        *,
        search_session_id: str,
        message: str,
    ) -> dict[str, Any]:
        return self.service.handle_user_message(
            search_session_id,
            message=message,
        ).model_dump(mode="json")

    def revise_product_contribution_choice(
        self,
        *,
        search_session_id: str,
        product_id: str,
        field: str,
        new_value: Any,
    ) -> dict[str, Any]:
        return self.service.revise_product_contribution_choice(
            search_session_id,
            product_id=product_id,
            field=field,
            new_value=new_value,
        ).model_dump(mode="json")

    def set_product_contribution_choice(
        self,
        *,
        search_session_id: str,
        product_id: str,
        field: str,
        value: Any,
    ) -> dict[str, Any]:
        return self.service.set_product_contribution_choice(
            search_session_id,
            product_id=product_id,
            field=field,
            value=value,
        ).model_dump(mode="json")

    def set_product_exclusion(
        self,
        *,
        search_session_id: str,
        product_id: str,
        excluded: bool,
    ) -> dict[str, Any]:
        return self.service.set_product_exclusion(
            search_session_id,
            product_id=product_id,
            excluded=excluded,
        ).model_dump(mode="json")


    def close_search_session(self, *, search_session_id: str) -> dict[str, Any]:
        self.service.close_search_session(search_session_id)
        return {"closed": True, "search_session_id": search_session_id}

    def get_top_recommendations(self, *, search_session_id: str) -> dict[str, Any]:
        return self.service.get_top_recommendations(search_session_id).model_dump(mode="json")

    def get_product_detail(
        self,
        *,
        search_session_id: str,
        product_id: str,
    ) -> dict[str, Any]:
        return self.service.get_product_recommendation_detail(
            search_session_id,
            product_id,
        ).model_dump(mode="json")

    def get_evaluation_trace(self, *, search_session_id: str) -> list[dict[str, Any]]:
        return [
            event.model_dump(mode="json")
            for event in self.service.get_evaluation_trace(search_session_id)
        ]
