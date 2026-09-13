"""Validated build inputs and bounded, literal textbook queries."""

from typing import Literal, Self

from pydantic import Field, model_validator

from japanese_tutor.schemas.curriculum import TAXONOMY
from japanese_tutor.schemas.source import Contract


class CurriculumInput(Contract):
    document: str = Field(min_length=1)
    curriculum: str = Field(min_length=1)
    verification: str = Field(min_length=1)
    approval: str = Field(min_length=1)


class BuildInputs(Contract):
    lessons: list[CurriculumInput] = Field(min_length=1)


class CurriculumApproval(Contract):
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    curriculum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    human_approved: Literal[True]
    approved_on: str = Field(min_length=1)
    approval_basis: str = Field(min_length=1)


class SearchQuery(Contract):
    query: str = Field(min_length=1, max_length=500)
    learned_only: bool = False
    allowed_lesson_ids: list[str] | None = Field(default=None, max_length=100)
    lesson_range: list[str] | None = Field(default=None, max_length=100)
    concept_types: list[str] | None = Field(default=None, max_length=10)
    source_page: int | None = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        if not self.query.strip():
            raise ValueError("Search query must contain literal text")
        if self.learned_only and self.allowed_lesson_ids is None:
            raise ValueError("learned_only requires explicit allowed_lesson_ids")
        if self.concept_types is not None and not set(self.concept_types) <= TAXONOMY.keys():
            raise ValueError("Unknown concept type")
        return self
