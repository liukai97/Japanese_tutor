"""Transient contracts for Codex-led tutoring and assessment."""

from typing import Literal, Self

from pydantic import Field, model_validator

from japanese_tutor.schemas.learner import Dimension, Identifier, LearningEvidence
from japanese_tutor.schemas.source import Contract, SourceRef

ActivityForm = Literal[
    "recognition",
    "recall",
    "translation",
    "error_correction",
    "sentence_building",
    "dialogue",
    "role_play",
    "information_gap",
    "free_expression",
    "explanation_check",
]
PresentationMode = Literal["chat_text", "chat_with_image", "interactive_panel"]
ResponseMode = Literal["free_text", "short_text", "choice", "ordering"]
TargetRole = Literal["primary", "review", "extension"]


class TutorSessionPlan(Contract):
    """A revisable session intention, never a pre-generated question sequence."""

    session_id: Identifier
    objective: str = Field(min_length=1, max_length=2000)
    curriculum_scope_lesson_ids: list[Identifier] = Field(min_length=1, max_length=100)
    primary_concept_ids: list[Identifier] = Field(min_length=1, max_length=20)
    review_concept_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    explicit_extension_concept_ids: list[Identifier] = Field(default_factory=list, max_length=10)
    planned_dimensions: list[Dimension] = Field(min_length=1, max_length=5)
    planned_activity_forms: list[ActivityForm] = Field(min_length=1, max_length=10)
    recent_activity_forms: list[ActivityForm] = Field(default_factory=list, max_length=10)
    prerequisite_policy: Literal["learned_or_explicit_extension"] = (
        "learned_or_explicit_extension"
    )
    rationale: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def unique_scope(self) -> Self:
        lists = (
            self.curriculum_scope_lesson_ids,
            self.primary_concept_ids,
            self.review_concept_ids,
            self.explicit_extension_concept_ids,
            self.planned_dimensions,
            self.planned_activity_forms,
        )
        if any(len(values) != len(set(values)) for values in lists):
            raise ValueError("Session plan lists must not contain duplicate values")
        concept_groups = (
            set(self.primary_concept_ids),
            set(self.review_concept_ids),
            set(self.explicit_extension_concept_ids),
        )
        if any(
            left & right
            for index, left in enumerate(concept_groups)
            for right in concept_groups[index + 1 :]
        ):
            raise ValueError("Primary, review and extension concepts must be disjoint")
        return self


class ActivityTarget(Contract):
    concept_id: Identifier
    role: TargetRole
    dimensions: list[Dimension] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_dimensions(self) -> Self:
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("Activity target dimensions must be unique")
        return self


class RubricCriterion(Contract):
    """Internal criterion. It is never included in learner-visible content."""

    id: Identifier
    concept_id: Identifier
    dimension: Dimension
    criterion: str = Field(min_length=1, max_length=2000)
    success_indicators: list[str] = Field(min_length=1, max_length=10)
    common_error_codes: list[Identifier] = Field(default_factory=list, max_length=20)
    sources: list[SourceRef] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_values(self) -> Self:
        if len(self.success_indicators) != len(set(self.success_indicators)):
            raise ValueError("Rubric success indicators must be unique")
        if len(self.common_error_codes) != len(set(self.common_error_codes)):
            raise ValueError("Rubric error codes must be unique")
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("Rubric sources must be unique")
        return self


class GeneratedActivity(Contract):
    """One ephemeral activity generated for the current learner and turn."""

    id: Identifier
    session_id: Identifier
    form: ActivityForm
    presentation_mode: PresentationMode = "chat_text"
    response_mode: ResponseMode = "free_text"
    learner_context: str | None = Field(default=None, max_length=8000)
    learner_prompt: str = Field(min_length=1, max_length=8000)
    choices: list[str] = Field(default_factory=list, max_length=20)
    extension_notice: str | None = Field(default=None, min_length=1, max_length=1000)
    targets: list[ActivityTarget] = Field(min_length=1, max_length=20)
    difficulty: Literal["introductory", "standard", "stretch"] = "standard"
    transfer_level: Literal["same_context", "varied_context", "novel_context"]
    hidden_rubric: list[RubricCriterion] = Field(min_length=1, max_length=50)
    sources: list[SourceRef] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def coherent_activity(self) -> Self:
        if len({target.concept_id for target in self.targets}) != len(self.targets):
            raise ValueError("Each activity concept must have one target entry")
        if len({criterion.id for criterion in self.hidden_rubric}) != len(
            self.hidden_rubric
        ):
            raise ValueError("Rubric criterion IDs must be unique")
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("Activity sources must be unique")

        expected_pairs = {
            (target.concept_id, dimension)
            for target in self.targets
            for dimension in target.dimensions
        }
        rubric_pairs = {
            (criterion.concept_id, criterion.dimension) for criterion in self.hidden_rubric
        }
        if len(rubric_pairs) != len(self.hidden_rubric) or rubric_pairs != expected_pairs:
            raise ValueError("Rubric must define exactly one criterion per target dimension")
        activity_sources = set(self.sources)
        if any(not set(criterion.sources) <= activity_sources for criterion in self.hidden_rubric):
            raise ValueError("Rubric sources must be listed on the activity")

        needs_choices = self.response_mode in {"choice", "ordering"}
        if needs_choices and len(self.choices) < 2:
            raise ValueError("Choice and ordering activities need at least two choices")
        if not needs_choices and self.choices:
            raise ValueError("Choices are only valid for choice or ordering responses")

        has_extension = any(target.role == "extension" for target in self.targets)
        if has_extension != (self.extension_notice is not None):
            raise ValueError("Extension targets require one learner-visible extension notice")
        return self


class ActivitySet(Contract):
    """One learner turn containing at most three independently assessable activities."""

    id: Identifier
    session_id: Identifier
    activities: list[GeneratedActivity] = Field(min_length=1, max_length=3)
    response_instruction: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def coherent_set(self) -> Self:
        if len({activity.id for activity in self.activities}) != len(self.activities):
            raise ValueError("Activity IDs must be unique within an activity set")
        if any(activity.session_id != self.session_id for activity in self.activities):
            raise ValueError("Every activity must belong to the activity set session")
        return self


class ErrorClassification(Contract):
    code: Identifier
    concept_id: Identifier
    dimension: Dimension
    explanation: str = Field(min_length=1, max_length=1000)


class AssessmentResult(Contract):
    """The structured judgment for one submitted learner attempt."""

    activity_id: Identifier
    evidence: list[LearningEvidence] = Field(default_factory=list, max_length=100)
    errors: list[ErrorClassification] = Field(default_factory=list, max_length=100)
    feedback: str = Field(min_length=1, max_length=4000)
    follow_up_required: bool
    follow_up_kind: Literal["clarification", "unhinted_retry", "hinted_retry"] | None = None
    follow_up_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    next_step: Literal[
        "continue_plan",
        "retry_activity",
        "change_form",
        "remediate",
        "answer_question",
        "end_session",
    ]
    next_step_reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def coherent_assessment(self) -> Self:
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Assessment evidence IDs must be unique")
        pairs = [(item.concept_id, item.dimension) for item in self.evidence]
        if len(pairs) != len(set(pairs)):
            raise ValueError("An attempt can produce at most one observation per target dimension")
        if any(
            item.kind != "observation" or item.supersedes is not None
            for item in self.evidence
        ):
            raise ValueError(
                "Tutor assessment creates observations, not corrections or retractions"
            )

        classified = [(item.concept_id, item.dimension, item.code) for item in self.errors]
        if len(classified) != len(set(classified)):
            raise ValueError("Error classifications must be unique")
        observed = {
            (item.concept_id, item.dimension, code)
            for item in self.evidence
            for code in item.error_codes
        }
        if set(classified) != observed:
            raise ValueError("Error classifications must explain every evidence error code exactly")

        if (self.follow_up_kind is None) != (self.follow_up_prompt is None):
            raise ValueError("Follow-up kind and prompt must be supplied together")
        has_follow_up = self.follow_up_kind is not None
        if self.follow_up_required != has_follow_up:
            raise ValueError(
                "Follow-up kind and prompt are required exactly when follow-up is needed"
            )
        if self.follow_up_required != (self.next_step == "retry_activity"):
            raise ValueError("A retry_activity next step requires a complete follow-up")
        return self
