from __future__ import annotations

from dataclasses import dataclass


DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class OpenAIResponsesModelProfile:
    """Request capabilities shared by an OpenAI Responses model family."""

    name: str
    model_families: tuple[str, ...]
    known_models: tuple[str, ...]
    supports_temperature: bool
    default_reasoning_effort: str | None
    supported_reasoning_efforts: tuple[str, ...] | None
    supports_structured_outputs: bool | None = True

    def matches(self, model: str) -> bool:
        normalized = model.strip().lower()
        return any(
            normalized == family or normalized.startswith(f"{family}-")
            for family in self.model_families
        )

    def resolve_reasoning_effort(self, configured: str | None) -> str | None:
        if configured is None or configured.strip().lower() in {"", "auto"}:
            return self.default_reasoning_effort

        effort = configured.strip().lower()
        if (
            self.supported_reasoning_efforts is not None
            and effort not in self.supported_reasoning_efforts
        ):
            supported = ", ".join(self.supported_reasoning_efforts)
            raise ValueError(
                f"{self.name} does not support reasoning effort {effort!r}; "
                f"choose one of: {supported}"
            )
        return effort

    def generation_controls(
        self,
        *,
        temperature: float,
        reasoning_effort: str | None,
    ) -> dict[str, object]:
        controls: dict[str, object] = {}
        if self.supports_temperature:
            controls["temperature"] = temperature
        resolved_effort = self.resolve_reasoning_effort(reasoning_effort)
        if resolved_effort is not None:
            controls["reasoning"] = {"effort": resolved_effort}
        return controls


GPT_5_6_RESPONSES_PROFILE = OpenAIResponsesModelProfile(
    name="gpt-5.6",
    model_families=("gpt-5.6",),
    known_models=(
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.6",
    ),
    supports_temperature=False,
    default_reasoning_effort="low",
    supported_reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
)


GENERIC_RESPONSES_PROFILE = OpenAIResponsesModelProfile(
    name="generic-responses",
    model_families=(),
    known_models=(),
    supports_temperature=True,
    default_reasoning_effort=None,
    # Unknown/future models remain configurable without a code release. The
    # provider remains the authority and will reject unsupported explicit values.
    supported_reasoning_efforts=None,
    supports_structured_outputs=None,
)


OPENAI_RESPONSES_MODEL_PROFILES = (GPT_5_6_RESPONSES_PROFILE,)


def resolve_openai_responses_model_profile(
    model: str,
) -> OpenAIResponsesModelProfile:
    for profile in OPENAI_RESPONSES_MODEL_PROFILES:
        if profile.matches(model):
            return profile
    return GENERIC_RESPONSES_PROFILE
