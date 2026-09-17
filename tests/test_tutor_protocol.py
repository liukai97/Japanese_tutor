"""Stage 6 Tutor contracts, leakage boundary and evidence conversion."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from japanese_tutor.schemas.learner import Evaluator, LearningEvidence
from japanese_tutor.schemas.source import SourceRef
from japanese_tutor.schemas.tutor import (
    ActivitySet,
    ActivityTarget,
    AssessmentResult,
    ErrorClassification,
    GeneratedActivity,
    RubricCriterion,
    TutorSessionPlan,
)
from japanese_tutor.tutor import (
    build_evidence_batch,
    validate_activity,
    validate_activity_set,
    validate_assessment,
)

CONCEPT = "liangshuang:L05:grammar:nominal-predicate"
REVIEW = "liangshuang:L05:communicative-skill:self-introduction"
SOURCE = SourceRef(
    document_id="liangshuang-l05",
    page=5,
    section_id="liangshuang:L05:grammar:nominal-predicate",
)


def plan() -> TutorSessionPlan:
    return TutorSessionPlan(
        session_id="session-1",
        objective="Use the lesson 5 nominal predicate in a short introduction.",
        curriculum_scope_lesson_ids=["liangshuang:L05"],
        primary_concept_ids=[CONCEPT],
        review_concept_ids=[REVIEW],
        planned_dimensions=["controlled_production", "natural_usage"],
        planned_activity_forms=["sentence_building", "role_play"],
        recent_activity_forms=["translation"],
        rationale="Confirm one form, then transfer it into a communicative activity.",
    )


def criterion(identifier: str, concept_id: str, dimension: str) -> RubricCriterion:
    return RubricCriterion(
        id=identifier,
        concept_id=concept_id,
        dimension=dimension,
        criterion="Judge only the named target behavior in the learner's response.",
        success_indicators=["The target behavior is present and fits the prompt."],
        common_error_codes=["target_form_missing"],
        sources=[SOURCE],
    )


def activity() -> GeneratedActivity:
    return GeneratedActivity(
        id="activity-1",
        session_id="session-1",
        form="role_play",
        learner_context="You have just met another student at an exchange event.",
        learner_prompt="Introduce yourself in Japanese in one or two sentences.",
        targets=[
            ActivityTarget(
                concept_id=CONCEPT,
                role="primary",
                dimensions=["controlled_production"],
            ),
            ActivityTarget(
                concept_id=REVIEW,
                role="review",
                dimensions=["natural_usage"],
            ),
        ],
        transfer_level="varied_context",
        hidden_rubric=[
            criterion("criterion-1", CONCEPT, "controlled_production"),
            criterion("criterion-2", REVIEW, "natural_usage"),
        ],
        sources=[SOURCE],
    )


def observation(
    identifier: str,
    concept_id: str,
    dimension: str,
    *,
    result: str = "independent",
    error_codes: list[str] | None = None,
) -> LearningEvidence:
    return LearningEvidence(
        id=identifier,
        concept_id=concept_id,
        dimension=dimension,
        result=result,
        hint_used=result == "assisted",
        error_codes=error_codes or [],
        rationale="The response provides direct evidence for this criterion.",
        evaluator=Evaluator(kind="llm", model="test-model", rubric_version="stage6-v1"),
    )


def assessment() -> AssessmentResult:
    return AssessmentResult(
        activity_id="activity-1",
        evidence=[
            observation("evidence-1", CONCEPT, "controlled_production"),
            observation(
                "evidence-2",
                REVIEW,
                "natural_usage",
                result="partial",
                error_codes=["target_form_missing"],
            ),
        ],
        errors=[
            ErrorClassification(
                code="target_form_missing",
                concept_id=REVIEW,
                dimension="natural_usage",
                explanation="The greeting appropriate to the role-play was omitted.",
            )
        ],
        feedback="The identity sentence is clear; add the expected greeting for this setting.",
        follow_up_required=False,
        next_step="continue_plan",
        next_step_reason="The main form was independently produced.",
    )


def test_valid_turn_builds_append_only_evidence_batch():
    current_plan = plan()
    current_activity = validate_activity(current_plan, activity())
    result = validate_assessment(current_activity, assessment())
    batch = build_evidence_batch(
        current_activity,
        result,
        idempotency_key="write-1",
        interaction_id="attempt-1",
        observed_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        response="私は李です。学生です。",
    )
    assert batch.interaction.activity_id == current_activity.id
    assert batch.interaction.task_type == "role_play"
    assert batch.interaction.feedback == result.feedback
    assert batch.interaction.prompt == (
        "Context:\nYou have just met another student at an exchange event.\n\n"
        "Task:\nIntroduce yourself in Japanese in one or two sentences."
    )
    assert [item.concept_id for item in batch.evidence] == [CONCEPT, REVIEW]


def test_interaction_snapshot_is_plain_text_and_preserves_visible_choices_and_notice():
    raw = activity().model_dump(mode="json")
    raw.update(
        response_mode="choice",
        choices=["学生です。", "先生です。"],
        extension_notice="This item introduces an optional extension.",
    )
    raw["targets"][0]["role"] = "extension"
    result = build_evidence_batch(
        GeneratedActivity.model_validate(raw),
        assessment(),
        idempotency_key="write-choice",
        interaction_id="attempt-choice",
        observed_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        response="1",
    )
    assert result.interaction.prompt == (
        "Extension notice:\nThis item introduces an optional extension.\n\n"
        "Context:\nYou have just met another student at an exchange event.\n\n"
        "Task:\nIntroduce yourself in Japanese in one or two sentences.\n\n"
        "Choices:\n1. 学生です。\n2. 先生です。"
    )
    assert not result.interaction.prompt.startswith("{")


def test_session_plan_rejects_overlapping_concept_roles():
    raw = plan().model_dump(mode="json")
    raw["review_concept_ids"] = [CONCEPT]
    with pytest.raises(ValidationError, match="disjoint"):
        TutorSessionPlan.model_validate(raw)

    raw = plan().model_dump(mode="json")
    raw["recent_activity_forms"] = ["translation", "translation"]
    assert TutorSessionPlan.model_validate(raw).recent_activity_forms == [
        "translation",
        "translation",
    ]


def test_activity_requires_complete_hidden_rubric_and_visible_extension_notice():
    raw = activity().model_dump(mode="json")
    raw["hidden_rubric"].pop()
    with pytest.raises(ValidationError, match="exactly one"):
        GeneratedActivity.model_validate(raw)

    raw = activity().model_dump(mode="json")
    raw["targets"][0]["role"] = "extension"
    with pytest.raises(ValidationError, match="extension notice"):
        GeneratedActivity.model_validate(raw)


def test_choice_contract_and_plan_boundary_are_strict():
    raw = activity().model_dump(mode="json")
    raw.update(response_mode="choice", choices=["only one"])
    with pytest.raises(ValidationError, match="at least two"):
        GeneratedActivity.model_validate(raw)

    outside = activity().model_copy(update={"form": "translation"})
    with pytest.raises(ValueError, match="form"):
        validate_activity(plan(), outside)


def test_activity_set_holds_three_independent_activities_in_one_turn():
    activities = [
        activity().model_copy(update={"id": f"activity-{number}"})
        for number in range(1, 4)
    ]
    result = validate_activity_set(
        plan(),
        ActivitySet(
            id="set-1",
            session_id="session-1",
            activities=activities,
            response_instruction="Reply as 1, 2, and 3 in one message.",
        ),
    )
    assert [item.id for item in result.activities] == [
        "activity-1",
        "activity-2",
        "activity-3",
    ]

    raw = result.model_dump(mode="json")
    raw["activities"].append({**raw["activities"][0], "id": "activity-4"})
    with pytest.raises(ValidationError, match="at most 3"):
        ActivitySet.model_validate(raw)

    raw = result.model_dump(mode="json")
    raw["activities"][1]["session_id"] = "other-session"
    with pytest.raises(ValidationError, match="set session"):
        ActivitySet.model_validate(raw)


def test_assessment_rejects_post_hoc_scope_and_unexplained_errors():
    result = assessment().model_dump(mode="json")
    result["evidence"][0]["concept_id"] = "unplanned-concept"
    changed = AssessmentResult.model_validate(result)
    with pytest.raises(ValueError, match="outside"):
        validate_assessment(activity(), changed)

    result = assessment().model_dump(mode="json")
    result["errors"] = []
    with pytest.raises(ValidationError, match="every evidence error"):
        AssessmentResult.model_validate(result)


def test_follow_up_and_hint_evidence_require_real_visible_support():
    raw = assessment().model_dump(mode="json")
    raw.update(
        follow_up_required=True,
        follow_up_kind="hinted_retry",
        follow_up_prompt="Try again using the identity pattern from the lesson.",
        next_step="retry_activity",
    )
    follow_up = AssessmentResult.model_validate(raw)
    assert follow_up.follow_up_required

    raw["next_step"] = "continue_plan"
    with pytest.raises(ValidationError, match="retry_activity"):
        AssessmentResult.model_validate(raw)

    raw = assessment().model_dump(mode="json")
    raw["follow_up_kind"] = "clarification"
    with pytest.raises(ValidationError, match="supplied together"):
        AssessmentResult.model_validate(raw)

    assisted = assessment().model_copy(
        update={
            "evidence": [
                observation(
                    "assisted-evidence",
                    CONCEPT,
                    "controlled_production",
                    result="assisted",
                )
            ],
            "errors": [],
        }
    )
    with pytest.raises(ValueError, match="actual learner-visible hint"):
        build_evidence_batch(
            activity(),
            assisted,
            idempotency_key="write-assisted",
            interaction_id="attempt-assisted",
            observed_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
            response="私は学生です。",
        )
