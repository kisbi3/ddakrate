from __future__ import annotations

from typing import Any, Callable, Mapping, TypeVar

from pydantic import BaseModel

from eligibility.audit import AuditEventType, AuditSession, LLMPayloadMode, canonical_hash
from eligibility.llm.client import LLMClient, LLMError
from eligibility.llm.mock_adapter import MockLLMAdapter
from eligibility.llm.models import (
    LLMHealthStatus,
    LLMMessage,
    LLMPurpose,
    LLMProfile,
    LLMRole,
    LLMSettings,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TextGenerationRequest,
    TextGenerationResponse,
)
from eligibility.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from eligibility.llm.openai_responses_adapter import OpenAIResponsesAdapter
from eligibility.llm.profiles import DEFAULT_PROFILES


T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    def __init__(
        self,
        client: LLMClient,
        *,
        profiles: Mapping[LLMPurpose, LLMProfile] | None = None,
        audit: AuditSession | None = None,
        debug_observer: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.profiles = dict(profiles or DEFAULT_PROFILES)
        self.audit = audit
        self.debug_observer = debug_observer

    @classmethod
    def from_settings(
        cls,
        settings: LLMSettings,
        *,
        mock_responses: Mapping[LLMPurpose | str, Any] | None = None,
        profiles: Mapping[LLMPurpose, LLMProfile] | None = None,
        audit: AuditSession | None = None,
        debug_observer: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "LLMGateway":
        if settings.provider == "MOCK":
            client: LLMClient = MockLLMAdapter(
                mock_responses,
                model=settings.model,
            )
        elif settings.provider == "OPENAI":
            client = OpenAIResponsesAdapter(
                base_url=settings.base_url,
                model=settings.model,
                api_key=settings.api_key,
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
                reasoning_effort=settings.reasoning_effort,
            )
        elif settings.provider == "OPENAI_COMPATIBLE":
            client = OpenAICompatibleAdapter(
                base_url=settings.base_url,
                model=settings.model,
                api_key=settings.api_key,
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            )
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: {settings.provider}")

        configured_profiles = {
            purpose: profile.model_copy(
                update={
                    "temperature": settings.temperature,
                    "timeout_seconds": settings.timeout_seconds,
                }
            )
            for purpose, profile in (profiles or DEFAULT_PROFILES).items()
        }
        if audit is not None:
            audit.llm_payload_mode = settings.log_payload_mode
        return cls(
            client,
            profiles=configured_profiles,
            audit=audit,
            debug_observer=debug_observer,
        )

    def generate_text(
        self,
        purpose: LLMPurpose,
        prompt: str,
        *,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TextGenerationResponse:
        profile = self.profiles[purpose]
        request = TextGenerationRequest(
            purpose=purpose,
            messages=self._messages(prompt, system_prompt),
            prompt_template_id=profile.prompt_template_id,
            prompt_template_version=profile.prompt_template_version,
            temperature=profile.temperature,
            timeout_seconds=profile.timeout_seconds,
            metadata=metadata or {},
        )
        self._debug_started(request)
        self._audit_started(request, profile)
        try:
            response = self.client.generate_text(request)
        except Exception as exc:
            self._debug_failed(exc)
            self._audit_failed(request, profile, exc)
            raise
        self._debug_completed(response, response.text)
        self._audit_completed(request, profile, response, response.text)
        return response

    def generate_structured(
        self,
        purpose: LLMPurpose,
        prompt: str,
        response_model: type[T],
        *,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructuredGenerationResponse[T]:
        profile = self.profiles[purpose]
        response_schema_version = profile.response_schema_version or "unspecified"
        request = StructuredGenerationRequest(
            purpose=purpose,
            messages=self._messages(prompt, system_prompt),
            prompt_template_id=profile.prompt_template_id,
            prompt_template_version=profile.prompt_template_version,
            response_schema_version=response_schema_version,
            response_schema_name=response_model.__name__,
            response_json_schema=response_model.model_json_schema(),
            temperature=profile.temperature,
            timeout_seconds=profile.timeout_seconds,
            metadata=metadata or {},
        )
        self._debug_started(request)
        self._audit_started(request, profile)
        try:
            response = self.client.generate_structured(request, response_model)
        except Exception as exc:
            self._debug_failed(exc)
            self._audit_failed(request, profile, exc)
            raise
        self._debug_completed(response, response.data.model_dump(mode="json"))
        self._audit_completed(
            request,
            profile,
            response,
            response.data.model_dump(mode="json"),
            schema_validation_result="VALID",
        )
        return response

    def health_check(self) -> LLMHealthStatus:
        return self.client.health_check()

    def _debug_started(self, request: TextGenerationRequest) -> None:
        if self.debug_observer is None:
            return
        self.debug_observer(
            "started",
            {
                "purpose": request.purpose.value,
                "provider": getattr(self.client, "provider", type(self.client).__name__),
                "model": getattr(self.client, "model", "unknown"),
                "request": request.model_dump(mode="json"),
            },
        )

    def _debug_completed(
        self,
        response: TextGenerationResponse | StructuredGenerationResponse[Any],
        output: Any,
    ) -> None:
        if self.debug_observer is None:
            return
        self.debug_observer(
            "completed",
            {
                "output": output,
                "latency_ms": response.latency_ms,
                "token_usage": (
                    response.token_usage.model_dump(mode="json")
                    if response.token_usage is not None
                    else None
                ),
                "retry_count": response.retry_count,
                "model_version": response.model_version,
            },
        )

    def _debug_failed(self, error: Exception) -> None:
        if self.debug_observer is None:
            return
        self.debug_observer(
            "failed",
            {
                "error": f"{type(error).__name__}: {error}",
                "error_code": getattr(error, "error_code", None),
            },
        )

    @staticmethod
    def _messages(prompt: str, system_prompt: str | None) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        if system_prompt:
            messages.append(LLMMessage(role=LLMRole.SYSTEM, content=system_prompt))
        messages.append(LLMMessage(role=LLMRole.USER, content=prompt))
        return messages

    def _safe_metadata(
        self,
        request: TextGenerationRequest,
        profile: LLMProfile,
    ) -> dict[str, Any]:
        input_document_id = request.metadata.get("input_document_id")
        input_document_hash = request.metadata.get("input_document_hash")
        input_context_hash = request.metadata.get("input_context_hash")
        if input_document_hash is None and input_document_id is not None:
            input_document_hash = canonical_hash(request.messages)
        if input_context_hash is None:
            input_context_hash = canonical_hash(request.metadata)
        base_url = getattr(self.client, "base_url", None)
        model_profile = getattr(self.client, "model_profile", None)
        supports_temperature = getattr(model_profile, "supports_temperature", True)
        return {
            "purpose": request.purpose.value,
            "provider": getattr(self.client, "provider", type(self.client).__name__),
            "base_url_identifier": (
                canonical_hash(base_url)[:16] if base_url is not None else None
            ),
            "model": getattr(self.client, "model", "unknown"),
            "prompt_template_id": profile.prompt_template_id,
            "prompt_template_version": profile.prompt_template_version,
            "response_schema_version": profile.response_schema_version,
            "model_profile": getattr(model_profile, "name", None),
            "temperature": request.temperature if supports_temperature else None,
            "reasoning_effort": getattr(self.client, "reasoning_effort", None),
            "timeout_seconds": request.timeout_seconds,
            "input_document_id": input_document_id,
            "input_document_hash": input_document_hash,
            "input_context_hash": input_context_hash,
        }

    def _request_payload_metadata(
        self,
        request: TextGenerationRequest,
    ) -> dict[str, Any]:
        if self.audit is None:
            return {}
        mode = self.audit.llm_payload_mode
        details: dict[str, Any] = {"llm_log_payload_mode": mode.value}
        if mode == LLMPayloadMode.NONE:
            details["request_payload_stored"] = False
        elif mode == LLMPayloadMode.HASHED:
            details["request_payload_hash"] = canonical_hash(request)
            details["request_payload_stored"] = False
        elif mode == LLMPayloadMode.REDACTED:
            details["request_payload_hash"] = canonical_hash(request)
            details["request_payload_stored"] = False
            details["message_summary"] = [
                {"role": message.role.value, "content_length": len(message.content)}
                for message in request.messages
            ]
        elif mode == LLMPayloadMode.FULL_DEBUG:
            # FULL_DEBUG is only suitable for synthetic/non-sensitive fixtures.
            # AuditSession still recursively removes credentials and tokens.
            details["request_payload"] = request.model_dump(mode="json")
            details["request_payload_stored"] = True
        return details

    def _output_payload_metadata(self, output: Any) -> dict[str, Any]:
        if self.audit is None:
            return {}
        mode = self.audit.llm_payload_mode
        if mode == LLMPayloadMode.NONE:
            return {"output_payload_stored": False}
        if mode == LLMPayloadMode.HASHED:
            return {
                "output_payload_hash": canonical_hash(output),
                "output_payload_stored": False,
            }
        if mode == LLMPayloadMode.REDACTED:
            return {
                "output_payload_hash": canonical_hash(output),
                "output_payload_stored": False,
                "output_payload_type": type(output).__name__,
            }
        return {"output_payload": output, "output_payload_stored": True}

    def _audit_started(
        self,
        request: TextGenerationRequest,
        profile: LLMProfile,
    ) -> None:
        if self.audit is None:
            return
        metadata = self._safe_metadata(request, profile)
        metadata.update(self._request_payload_metadata(request))
        self.audit.emit(
            "LLM_GATEWAY",
            AuditEventType.LLM_CALL_STARTED,
            entity_refs={"purpose": request.purpose.value},
            input_data=request,
            payload=metadata,
        )

    def _audit_completed(
        self,
        request: TextGenerationRequest,
        profile: LLMProfile,
        response: TextGenerationResponse | StructuredGenerationResponse[Any],
        output: Any,
        *,
        schema_validation_result: str | None = None,
    ) -> None:
        if self.audit is None:
            return
        metadata = self._safe_metadata(request, profile)
        metadata.update(self._request_payload_metadata(request))
        metadata.update(self._output_payload_metadata(output))
        metadata.update(
            {
                "model_version": response.model_version,
                "output_hash": canonical_hash(output),
                "latency_ms": response.latency_ms,
                "token_usage": (
                    response.token_usage.model_dump(mode="json")
                    if response.token_usage is not None
                    else None
                ),
                "retry_count": response.retry_count,
                "schema_validation_result": schema_validation_result,
                "semantic_validation_result": None,
                "error_code": None,
            }
        )
        self.audit.emit(
            "LLM_GATEWAY",
            AuditEventType.LLM_CALL_COMPLETED,
            entity_refs={"purpose": request.purpose.value},
            input_data=request,
            output_data=output,
            payload=metadata,
        )

    def _audit_failed(
        self,
        request: TextGenerationRequest,
        profile: LLMProfile,
        error: Exception,
    ) -> None:
        if self.audit is None:
            return
        metadata = self._safe_metadata(request, profile)
        metadata.update(self._request_payload_metadata(request))
        metadata.update(
            {
                "schema_validation_result": "INVALID"
                if getattr(error, "error_code", "") == "INVALID_STRUCTURED_OUTPUT"
                else None,
                "semantic_validation_result": None,
                "error_code": getattr(error, "error_code", type(error).__name__),
                "error_type": type(error).__name__,
            }
        )
        self.audit.emit(
            "LLM_GATEWAY",
            AuditEventType.LLM_CALL_FAILED,
            entity_refs={"purpose": request.purpose.value},
            input_data=request,
            payload=metadata,
        )
