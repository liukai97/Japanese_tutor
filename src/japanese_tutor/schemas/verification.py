"""Codex reports are bound to the exact submitted curriculum artifact."""

from typing import Literal, Self

from pydantic import Field, model_validator

from japanese_tutor.schemas.curriculum import Support
from japanese_tutor.schemas.source import Contract


class FieldIssue(Contract):
    field: str = Field(min_length=1)
    message: str = Field(min_length=1)
    supports: list[Support] = Field(min_length=1)


class ObjectCheck(Contract):
    object_id: str
    verdict: Literal["pass", "review_required"]
    checked_fields: list[str] = Field(min_length=1)
    supports: list[Support] = Field(min_length=1)
    issues: list[FieldIssue]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def check_pass(self) -> Self:
        if self.verdict == "pass" and self.issues:
            raise ValueError("Pass cannot contain issues")
        if self.verdict == "review_required" and not self.issues:
            raise ValueError("Review requires specific issues")
        return self


class CoverageCheck(Contract):
    section_id: str
    verdict: Literal["pass", "review_required"]
    omissions: list[str]
    reason: str = Field(min_length=1)
    supports: list[Support] = Field(min_length=1)

    @model_validator(mode="after")
    def check_pass(self) -> Self:
        if (self.verdict == "pass") != (not self.omissions):
            raise ValueError("Coverage omissions and verdict disagree")
        return self


class VerificationSubmission(Contract):
    schema_version: Literal["0.1.0-draft"] = "0.1.0-draft"
    curriculum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier: str = Field(min_length=1)
    instruction_version: str = Field(min_length=1)
    context_id: str = Field(min_length=1)
    independent_context: Literal[True]
    objects: list[ObjectCheck]
    sections: list[CoverageCheck]
