from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from eligibility.schema.rule import SourceReference


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceFactDefinition(StrictModel):
    fact_type: str
    description: str
    authoritative_source: str
    derivation_chain: list[str] = Field(default_factory=list)


class InstitutionServiceDefinition(StrictModel):
    institution_id: str
    service_id: str
    name: str
    description: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    facts: list[ServiceFactDefinition] = Field(default_factory=list)
    source_provenance: list[SourceReference] = Field(default_factory=list)
