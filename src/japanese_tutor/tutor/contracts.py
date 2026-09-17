"""Cross-contract validation and conversion to append-only learner evidence."""

from collections.abc import Sequence
from datetime import datetime

from japanese_tutor.schemas.learner import EvidenceBatch, Interaction
from japanese_tutor.schemas.tutor import (
    ActivitySet,
    AssessmentResult,
    GeneratedActivity,
    TutorSessionPlan,
)


def validate_activity(plan: TutorSessionPlan, activity: GeneratedActivity) -> GeneratedActivity:
    """Validate an activity against the latest revisable session plan."""

    if activity.session_id != plan.session_id:
        raise ValueError("Activity session does not match the session plan")
    if activity.form not in plan.planned_activity_forms:
        raise ValueError("Activity form is outside the current session plan")

    expected_roles = {
        **{identifier: "primary" for identifier in plan.primary_concept_ids},
        **{identifier: "review" for identifier in plan.review_concept_ids},
        **{identifier: "extension" for identifier in plan.explicit_extension_concept_ids},
    }
    for target in activity.targets:
        if expected_roles.get(target.concept_id) != target.role:
            raise ValueError("Activity target is outside the plan or has the wrong role")
        if not set(target.dimensions) <= set(plan.planned_dimensions):
            raise ValueError("Activity target dimension is outside the current session plan")
    return activity


def validate_activity_set(plan: TutorSessionPlan, activity_set: ActivitySet) -> ActivitySet:
    """Validate every independently assessable activity against one session plan."""

    if activity_set.session_id != plan.session_id:
        raise ValueError("Activity set session does not match the session plan")
    for activity in activity_set.activities:
        validate_activity(plan, activity)
    return activity_set


def validate_assessment(
    activity: GeneratedActivity, assessment: AssessmentResult
) -> AssessmentResult:
    """Reject post-hoc evidence for concepts or dimensions absent from the hidden rubric."""

    if assessment.activity_id != activity.id:
        raise ValueError("Assessment activity does not match the generated activity")
    rubric_pairs = {
        (criterion.concept_id, criterion.dimension) for criterion in activity.hidden_rubric
    }
    evidence_pairs = {(item.concept_id, item.dimension) for item in assessment.evidence}
    if not evidence_pairs <= rubric_pairs:
        raise ValueError("Assessment evidence is outside the activity rubric")
    return assessment


def interaction_prompt(activity: GeneratedActivity) -> str:
    """Render a compact plain-text snapshot of everything shown to the learner."""

    sections = []
    if activity.extension_notice:
        sections.append(f"Extension notice:\n{activity.extension_notice}")
    if activity.learner_context:
        sections.append(f"Context:\n{activity.learner_context}")
    sections.append(f"Task:\n{activity.learner_prompt}")
    if activity.choices:
        choices = "\n".join(
            f"{index}. {choice}" for index, choice in enumerate(activity.choices, start=1)
        )
        sections.append(f"Choices:\n{choices}")
    return "\n\n".join(sections)


def build_evidence_batch(
    activity: GeneratedActivity,
    assessment: AssessmentResult,
    *,
    idempotency_key: str,
    interaction_id: str,
    observed_at: datetime,
    response: str,
    hints: Sequence[str] = (),
    retry_of: str | None = None,
) -> EvidenceBatch:
    """Convert a validated assessed attempt to the existing learner write contract."""

    validate_assessment(activity, assessment)
    if any(item.hint_used for item in assessment.evidence) and not hints:
        raise ValueError("Hint-dependent evidence requires the actual learner-visible hint")
    interaction = Interaction(
        id=interaction_id,
        activity_id=activity.id,
        session_id=activity.session_id,
        observed_at=observed_at,
        task_type=activity.form,
        prompt=interaction_prompt(activity),
        response=response,
        hints=list(hints),
        feedback=assessment.feedback,
        retry_of=retry_of,
    )
    return EvidenceBatch(
        idempotency_key=idempotency_key,
        interaction=interaction,
        evidence=assessment.evidence,
    )
