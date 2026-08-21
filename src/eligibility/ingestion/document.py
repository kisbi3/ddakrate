from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentSection(BaseModel):
    """Page/section-aware text passed to the Rule Extractor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    document_name: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    text: str
    source_url: str | None = None

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DocumentSection.text must not be empty")
        return value
