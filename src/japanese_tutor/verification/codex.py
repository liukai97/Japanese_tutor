"""Codex drives verification; Python prepares and validates artifacts only."""

from pathlib import Path

from japanese_tutor.curriculum.extract import (
    artifact_hash,
    source_view,
    validate_curriculum,
    validate_support,
    write_json,
)
from japanese_tutor.schemas.curriculum import (
    Concept,
    ConceptRelation,
    Dialogue,
    Example,
    ExerciseExample,
    LexicalData,
    SemanticCurriculum,
    TopicData,
)
from japanese_tutor.schemas.source import LessonDocument
from japanese_tutor.schemas.verification import VerificationSubmission


def required_fields(obj) -> set[str]:
    fields = {"source_alignment", "provenance"}
    if isinstance(obj, Concept):
        fields |= {"concept_claim", "taxonomy"}
        if isinstance(obj.data, LexicalData):
            fields |= {
                "surface",
                "reading",
                "pitch_notation",
                "meaning",
                "part_of_speech",
                "jlpt",
                "lexical_variants",
            }
        elif isinstance(obj.data, TopicData):
            fields |= {
                "topic_boundary",
                "topic_notes",
                "topic_forms",
                "topic_examples",
                "topic_tables",
            }
            for field in ["notes", "examples", "tables"]:
                fields |= {f"{field}[{i}]" for i in range(len(getattr(obj.data, field)))}
    elif isinstance(obj, Example):
        fields |= {"text", "ruby", "translation", "usage"}
    elif isinstance(obj, Dialogue):
        fields |= {"setting", "utterances", "ruby"}
    elif isinstance(obj, ExerciseExample):
        fields |= {"source_text", "exercise_targets", "progression"}
    elif isinstance(obj, ConceptRelation):
        fields |= {"relation_support", "endpoints"}
    return fields


def prepare_verification(document: LessonDocument, curriculum: SemanticCurriculum, output: Path):
    validate_curriculum(document, curriculum)
    write_json(output / "verification_schema.json", VerificationSubmission.model_json_schema())
    write_json(
        output / "verification_tasks.json",
        {
            "curriculum_sha256": artifact_hash(curriculum),
            "source_artifact_sha256": artifact_hash(document),
            "instruction_version": "stage2-v3",
            "status": "awaiting_independent_codex_verification",
            "source": source_view(document),
            "curriculum": curriculum.model_dump(mode="json"),
            "required_fields": {o.id: sorted(required_fields(o)) for o in curriculum.objects},
            "coverage_sections": curriculum.lesson.section_ids,
        },
    )


def validate_verification(document, curriculum, submission: VerificationSubmission) -> None:
    validate_curriculum(document, curriculum)
    if submission.curriculum_sha256 != artifact_hash(
        curriculum
    ) or submission.source_artifact_sha256 != artifact_hash(document):
        raise ValueError("Stale verification: submitted artifact hashes do not match")
    objects = {o.id: o for o in curriculum.objects}
    checked = [c.object_id for c in submission.objects]
    sections = [c.section_id for c in submission.sections]
    if len(checked) != len(set(checked)) or not set(checked) <= objects.keys():
        raise ValueError("Duplicate or unknown verified object")
    if len(sections) != len(set(sections)) or not set(sections) <= set(
        curriculum.lesson.section_ids
    ):
        raise ValueError("Duplicate or unknown coverage section")
    # Partial verification can be saved, but the report must expose every missing check.
    for check in submission.objects:
        obj = objects[check.object_id]
        if not required_fields(obj) <= set(check.checked_fields):
            raise ValueError("Verification does not check every required field")
        scope = {s.source.section_id for s in obj.supports}
        supports = check.supports + [s for issue in check.issues for s in issue.supports]
        for support in supports:
            validate_support(document, support)
            if support.source.section_id not in scope:
                raise ValueError("Verification support is outside the object's source sections")
    for check in submission.sections:
        for support in check.supports:
            validate_support(document, support)
            if support.source.section_id != check.section_id:
                raise ValueError("Coverage support is outside the checked section")


def accept_verification(document, curriculum, submission: Path, output: Path):
    result = VerificationSubmission.model_validate_json(submission.read_text(encoding="utf-8"))
    validate_verification(document, curriculum, result)
    write_json(output / "verification.json", result)
    return result
