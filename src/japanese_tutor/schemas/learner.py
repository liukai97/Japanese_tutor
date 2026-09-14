"""Learner contracts: observations, not model-authored mastery percentages."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from japanese_tutor.schemas.source import Contract

Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
Dimension = Literal[
    "recognition", "recall", "controlled_production", "free_production", "natural_usage"
]
Result = Literal["independent", "assisted", "partial", "unsuccessful"]


class LearnerProfile(Contract):
    goals: list[str] = Field(default_factory=list, max_length=20)
    explanation_language: str = Field(default="zh", min_length=1, max_length=100)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    available_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("goals", "preferences")
    @classmethod
    def short_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("Profile entries must be nonempty and at most 500 characters")
        return values


class FrontierUpdate(Contract):
    id: Identifier
    learned_lesson_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    allowed_lesson_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    focus_concept_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def scope(self) -> Self:
        for values in (self.learned_lesson_ids, self.allowed_lesson_ids, self.focus_concept_ids):
            if len(values) != len(set(values)):
                raise ValueError("Frontier IDs must be unique")
        if not set(self.learned_lesson_ids) <= set(self.allowed_lesson_ids):
            raise ValueError("Learned lessons must be included in allowed lessons")
        return self


class Interaction(Contract):
    id: Identifier
    activity_id: Identifier  # Shared by the first attempt and all prompted retries.
    session_id: Identifier | None = None
    observed_at: AwareDatetime
    task_type: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=12000)
    response: str = Field(max_length=12000)
    hints: list[str] = Field(default_factory=list, max_length=20)
    feedback: str | None = Field(default=None, max_length=4000)
    retry_of: Identifier | None = None

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("hints")
    @classmethod
    def short_hints(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 4000 for value in values):
            raise ValueError("Hints must be nonempty and at most 4000 characters")
        return values


class Evaluator(Contract):
    kind: Literal["llm", "human", "deterministic"]
    model: str | None = Field(default=None, min_length=1, max_length=200)
    rubric_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def llm_model(self) -> Self:
        if self.kind == "llm" and self.model is None:
            raise ValueError("LLM evaluation requires a model identifier")
        return self


class LearningEvidence(Contract):
    id: Identifier
    concept_id: Identifier
    dimension: Dimension
    kind: Literal["observation", "retraction"] = "observation"
    result: Result | None = None
    confidence: Literal["sufficient", "insufficient"] = "sufficient"
    hint_used: bool = False
    error_codes: list[Identifier] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)
    evaluator: Evaluator
    supersedes: Identifier | None = None

    @model_validator(mode="after")
    def observation(self) -> Self:
        if self.kind == "retraction":
            if (
                self.supersedes is None
                or self.result is not None
                or self.error_codes
                or self.hint_used
            ):
                raise ValueError("Retraction needs supersedes and no result, hints or errors")
        elif self.result is None:
            raise ValueError("Observation needs a result")
        if self.result == "independent" and self.hint_used:
            raise ValueError("Independent success cannot use hints")
        if self.result == "assisted" and not self.hint_used:
            raise ValueError("Assisted success must record hint_used")
        if self.supersedes == self.id:
            raise ValueError("Evidence cannot supersede itself")
        return self


class EvidenceBatch(Contract):
    idempotency_key: Identifier
    interaction: Interaction
    evidence: list[LearningEvidence] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique")
        return self
