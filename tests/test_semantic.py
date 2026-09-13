"""Stage 2 checks: lossless conversion, source boundaries and release gates."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from japanese_tutor.cli import app
from japanese_tutor.curriculum.extract import (
    accept_extraction,
    artifact_hash,
    deterministic_curriculum,
    prepare_extraction,
    slice_japanese_text,
    validate_curriculum,
    write_json,
)
from japanese_tutor.importer.pitch import lexical_notations
from japanese_tutor.schemas.curriculum import (
    ClaimData,
    Concept,
    ConceptRelation,
    SectionRecord,
    SemanticCurriculum,
    Support,
    TopicData,
    TopicExample,
    TopicNote,
    TopicTable,
    TopicTurn,
)
from japanese_tutor.schemas.source import (
    BuildMetadata,
    DocumentManifest,
    JapaneseText,
    LessonDocument,
    Page,
    PageRef,
    Section,
    SourceRef,
    Table,
    TextBlock,
)
from japanese_tutor.schemas.verification import (
    CoverageCheck,
    FieldIssue,
    ObjectCheck,
    VerificationSubmission,
)
from japanese_tutor.verification.codex import (
    accept_verification,
    prepare_verification,
    required_fields,
    validate_verification,
)
from japanese_tutor.verification.report import build_report


def sample():
    ref = SourceRef(document_id="sample-l05", page=1, section_id="sample:L05:words")

    def text(value, notation=False):
        return JapaneseText(
            text=value,
            sources=[ref],
            lexical_notations=lexical_notations(value) if notation else [],
        )

    word = text("お母（かあ）さん②", True)
    section = Section(
        section_id=ref.section_id,
        section_type="vocabulary",
        title="Words",
        pages=[PageRef(document_id=ref.document_id, page=1)],
        confidence=0.95,
        blocks=[
            Table(
                table_type="vocabulary",
                columns=["number", "word", "part_of_speech", "meaning", "jlpt"],
                rows=[
                    [text("1"), word, text("［名］"), text("妈妈"), text("N5")],
                    [
                        text("2"),
                        text("日本人（にほんじん）④\n日本（にほん）②", True),
                        text("［名］\n［专］"),
                        text("日本人\n日本"),
                        text("N5"),
                    ],
                ],
                sources=[ref],
            ),
            TextBlock(content=text("教材规则：名词句使用です。")),
        ],
    )
    document = LessonDocument(
        metadata=BuildMetadata(
            extractor_version="test", pymupdf_version="test", notation_convention="test-reviewed-v1"
        ),
        manifest=DocumentManifest(
            document_id=ref.document_id,
            lesson_id="sample:L05",
            filename="sample.pdf",
            document_sha256="a" * 64,
            page_count=1,
            pages=[Page(page=1, width=600.0, height=800.0)],
            fonts=[],
        ),
        sections=[section],
    )
    candidate = deterministic_curriculum(document)
    grammar = Concept(
        id="sample:L05:grammar:nominal",
        lesson_id=document.manifest.lesson_id,
        provenance="asserted",
        supports=[Support(source=ref, quote="教材规则：名词句使用です。", reason="测试教材说明。")],
        confidence=0.95,
        title="Nominal predicate",
        category="grammar",
        subtype="topic",
        summary="名词句使用です。",
        data=TopicData(notes=[TopicNote(text="名词句使用です。", source_indices=[0])]),
    )
    candidate = candidate.model_copy(update={"objects": candidate.objects + [grammar]})
    records = [
        SectionRecord(
            section_id=ref.section_id,
            disposition="extracted",
            object_ids=[o.id for o in candidate.objects],
            reason="已完成来源内容处理。",
        )
    ]
    curriculum = candidate.model_copy(update={"sections": records})
    return document, curriculum


def verified(document, curriculum):
    return VerificationSubmission(
        curriculum_sha256=artifact_hash(curriculum),
        source_artifact_sha256=artifact_hash(document),
        verifier="independent-test-context",
        instruction_version="stage2-v3",
        context_id="test-verifier",
        independent_context=True,
        objects=[
            ObjectCheck(
                object_id=o.id,
                verdict="pass",
                checked_fields=sorted(required_fields(o)),
                supports=o.supports,
                issues=[],
                confidence=0.95,
            )
            for o in curriculum.objects
        ],
        sections=[
            CoverageCheck(
                section_id=s.section_id,
                verdict="pass",
                omissions=[],
                reason="覆盖内容经独立检查。",
                supports=[curriculum.objects[0].supports[0]],
            )
            for s in curriculum.sections
        ],
    )


def test_deterministic_conversion_preserves_and_defers(tmp_path: Path):
    document, curriculum = sample()
    assert len(deterministic_curriculum(document).objects) == 1
    assert len(curriculum.objects) == 2  # one lexical entry and a sourced grammar claim
    data = curriculum.objects[0].data
    assert data.notation.reading_range == (1, 2)
    assert data.notation.reading_scope == "partial"
    assert data.notation.pitch_accent.raw_notation == "②"
    assert (data.meaning, data.part_of_speech, data.jlpt) == ("妈妈", "［名］", "N5")
    source = document.sections[0].blocks[0].rows[0][1]
    assert (data.source_text.text, data.source_text.ruby, data.source_text.sources) == (
        source.text,
        source.ruby,
        source.sources,
    )
    assert data.source_text.lexical_notations == []
    prepare_extraction(document, tmp_path)
    tasks = json.loads((tmp_path / "extraction_tasks.json").read_text(encoding="utf-8"))
    assert tasks["status"] == "awaiting_codex_extraction"
    assert not (tmp_path / "semantic_curriculum.json").exists()
    assert build_report(document, curriculum)["summary"]["automatically_passed"] == 0
    # JSON contract round-trip remains strict, with no layout coordinates.
    assert SemanticCurriculum.model_validate_json(curriculum.model_dump_json()) == curriculum


def test_source_scope_quotes_records_and_atomic_rejection(tmp_path: Path):
    document, curriculum = sample()
    submission = tmp_path / "submission.json"
    write_json(submission, curriculum)
    accept_extraction(document, submission, tmp_path / "out")
    original = (tmp_path / "out/semantic_curriculum.json").read_bytes()
    payload = curriculum.model_dump(mode="json")
    for fault in ["page", "quote", "hash", "records", "unknown", "target"]:
        invalid = copy.deepcopy(payload)
        if fault == "page":
            invalid["objects"][0]["supports"][0]["source"]["page"] = 2
        elif fault == "quote":
            invalid["objects"][0]["supports"][0]["quote"] = "模型虚构的教材原文"
        elif fault == "hash":
            invalid["metadata"]["source_artifact_sha256"] = "0" * 64
        elif fault == "records":
            invalid["sections"] = []
        elif fault == "unknown":
            invalid["objects"][0]["bbox"] = [0, 0, 1, 1]
        else:
            invalid["sections"][0]["object_ids"] = ["missing"]
        write_json(submission, invalid)
        with pytest.raises((ValueError, ValidationError)):
            accept_extraction(document, submission, tmp_path / "out")
        assert (tmp_path / "out/semantic_curriculum.json").read_bytes() == original


def test_verification_hash_scope_required_fields_and_partial(tmp_path: Path):
    document, curriculum = sample()
    verification = verified(document, curriculum)
    validate_verification(document, curriculum, verification)
    prepare_verification(document, curriculum, tmp_path)
    path = tmp_path / "result.json"
    write_json(path, verification)
    accept_verification(document, curriculum, path, tmp_path)
    for fault in ["hash", "fields", "duplicate", "context", "quote"]:
        payload = verification.model_dump(mode="json")
        if fault == "hash":
            payload["curriculum_sha256"] = "0" * 64
        elif fault == "fields":
            payload["objects"][0]["checked_fields"] = ["source_alignment"]
        elif fault == "duplicate":
            payload["objects"].append(payload["objects"][0])
        elif fault == "context":
            payload["independent_context"] = False
        else:
            payload["objects"][0]["supports"][0]["quote"] = "unobserved"
        write_json(path, payload)
        with pytest.raises((ValueError, ValidationError)):
            accept_verification(document, curriculum, path, tmp_path)
    partial = verification.model_copy(update={"objects": verification.objects[:1], "sections": []})
    report = build_report(document, curriculum, partial)
    assert report["summary"]["missing_object_checks"] == 1
    assert report["summary"]["missing_coverage_checks"] == 1
    assert report["accepted_object_ids"] == []


def test_disagreement_omissions_unresolved_proposals_and_dependency_gates():
    document, curriculum = sample()
    verification = verified(document, curriculum)
    assert build_report(document, curriculum, verification)["status"] == "automatically_verified"
    obj = curriculum.objects[0]
    issue = FieldIssue(field="meaning", message="教材意义与对象不符。", supports=obj.supports)
    check = verification.objects[0].model_copy(
        update={"verdict": "review_required", "issues": [issue]}
    )
    changed = verification.model_copy(update={"objects": [check] + verification.objects[1:]})
    report = build_report(document, curriculum, changed)
    assert report["summary"]["disagreements"] == 1
    coverage = verification.sections[0].model_copy(
        update={"verdict": "review_required", "omissions": ["多条目词汇行尚未处理。"]}
    )
    report = build_report(
        document, curriculum, verification.model_copy(update={"sections": [coverage]})
    )
    assert report["summary"]["omissions"] == 1 and not report["accepted_object_ids"]
    for field, value, reason in [
        ("status", "unresolved", "unresolved"),
        ("provenance", "proposed", "proposed"),
        ("confidence", 0.5, "low_confidence_review"),
    ]:
        modified = obj.model_copy(update={field: value})
        c = curriculum.model_copy(update={"objects": [modified] + curriculum.objects[1:]})
        report = build_report(document, c, verified(document, c))
        assert any(reason in e["reasons"] for e in report["review_queue"])
    relation = ConceptRelation(
        id="sample:L05:relation:test",
        lesson_id=curriculum.lesson.id,
        provenance="derived",
        supports=obj.supports,
        confidence=0.99,
        source_id=obj.id,
        target_id=curriculum.objects[1].id,
        relation_type="co_practiced_with",
    )
    modified = obj.model_copy(update={"status": "unresolved"})
    objects = [modified] + curriculum.objects[1:] + [relation]
    record = curriculum.sections[0].model_copy(update={"object_ids": [o.id for o in objects]})
    c = curriculum.model_copy(update={"objects": objects, "sections": [record]})
    report = build_report(document, c, verified(document, c))
    assert relation.id not in report["accepted_object_ids"]
    assert any("endpoint_not_approved" in e["reasons"] for e in report["review_queue"])


def test_semantic_graph_taxonomy_and_rejected_pass():
    document, curriculum = sample()
    support = Support(
        source=curriculum.objects[0].supports[0].source,
        quote="教材规则：名词句使用です。",
        reason="教材直接说明。",
    )
    with pytest.raises(ValidationError):
        Concept(
            id="sample:L05:grammar:bad",
            lesson_id="sample:L05",
            provenance="asserted",
            supports=[support],
            confidence=0.9,
            title="bad",
            category="grammar",
            subtype="invented",
            summary="bad",
            data=ClaimData(statement="bad"),
        )
    payload = curriculum.model_dump(mode="json")
    payload["objects"].append(payload["objects"][0])
    with pytest.raises(ValidationError):
        SemanticCurriculum.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        ObjectCheck(
            object_id="x",
            verdict="pass",
            checked_fields=["meaning"],
            supports=[support],
            issues=[FieldIssue(field="meaning", message="wrong", supports=[support])],
            confidence=1.0,
        )
    validate_curriculum(document, curriculum)


def test_cli_driven_workflow(tmp_path: Path):
    document, curriculum = sample()
    doc = tmp_path / "lesson_document.json"
    submission = tmp_path / "submission.json"
    write_json(doc, document)
    write_json(submission, curriculum)
    runner = CliRunner()
    assert runner.invoke(app, ["extract", str(doc)]).exit_code == 0
    source = runner.invoke(app, ["show-source", str(doc), "--section", "sample:L05:words"])
    assert source.exit_code == 0 and "sections" in json.loads(source.stdout)
    assert runner.invoke(app, ["show-source", str(doc), "--section", "absent"]).exit_code == 1
    assert runner.invoke(app, ["extract", str(doc), "--submission", str(submission)]).exit_code == 0
    semantic = tmp_path / "semantic/semantic_curriculum.json"
    pending = runner.invoke(app, ["verify", str(doc), str(semantic)])
    assert pending.exit_code == 0 and "review_required" in pending.stdout
    check = tmp_path / "checks.json"
    write_json(check, verified(document, curriculum))
    result = runner.invoke(app, ["verify", str(doc), str(semantic), "--submission", str(check)])
    assert result.exit_code == 0 and "automatically_verified" in result.stdout
    report = json.loads(
        (tmp_path / "semantic/verification_report.json").read_text(encoding="utf-8")
    )
    assert report["human_approved"] is False


def test_source_uncertainty_and_ruby_slice_boundaries():
    from japanese_tutor.schemas.source import RubyAnnotation

    document, curriculum = sample()
    section = document.sections[0]
    table = section.blocks[0].model_copy(update={"status": "unresolved"})
    section = section.model_copy(update={"blocks": [table] + section.blocks[1:]})
    modified = document.model_copy(update={"sections": [section]})
    c = curriculum.model_copy(
        update={
            "metadata": curriculum.metadata.model_copy(
                update={
                    "source_artifact_sha256": artifact_hash(modified),
                }
            ),
        }
    )
    report = build_report(modified, c, verified(modified, c))
    assert not report["accepted_object_ids"]
    assert any("source_unresolved" in e["reasons"] for e in report["review_queue"])
    ref = curriculum.objects[0].supports[0].source
    text = JapaneseText(
        text="A: 私は学生です。",
        sources=[ref],
        ruby=[
            RubyAnnotation(range=(3, 4), reading="わたし"),
            RubyAnnotation(range=(5, 7), reading="がくせい"),
        ],
    )
    sliced = slice_japanese_text(text, 3, len(text.text))
    assert sliced.text == "私は学生です。"
    assert [r.range for r in sliced.ruby] == [(0, 1), (2, 4)]
    assert sliced.sources == text.sources
    with pytest.raises(ValueError, match="ruby"):
        slice_japanese_text(text, 6, len(text.text))


def test_graph_and_exercise_dependency_review_propagates():
    from japanese_tutor.schemas.curriculum import ExerciseExample

    document, c = sample()
    concept = c.objects[0].model_copy(update={"status": "unresolved"})
    exercise = ExerciseExample(
        id="sample:L05:exercise:source-training",
        lesson_id=c.lesson.id,
        provenance="derived",
        supports=concept.supports,
        confidence=0.9,
        title="Source training",
        source_text=[concept.data.source_text],
        target_concept_ids=[concept.id],
        task_type="controlled",
        progression="controlled",
    )
    relation = ConceptRelation(
        id="sample:L05:relation:source-example",
        lesson_id=c.lesson.id,
        provenance="derived",
        supports=concept.supports,
        confidence=0.9,
        source_id=c.objects[1].id,
        target_id=exercise.id,
        relation_type="exemplified_by",
    )
    # Relation precedes its exercise in submission order, exercising transitive gates.
    objects = [concept] + c.objects[1:] + [relation, exercise]
    record = c.sections[0].model_copy(update={"object_ids": [o.id for o in objects]})
    c = c.model_copy(update={"objects": objects, "sections": [record]})
    report = build_report(document, c, verified(document, c))
    assert exercise.id not in report["accepted_object_ids"]
    assert relation.id not in report["accepted_object_ids"]
    assert {e["reasons"][0] for e in report["review_queue"]} >= {
        "exercise_target_not_approved",
        "endpoint_not_approved",
    }
    payload = c.model_dump(mode="json")
    payload["objects"][-1]["target_concept_ids"] = ["absent-concept"]
    with pytest.raises(ValidationError, match="targets"):
        SemanticCurriculum.model_validate_json(json.dumps(payload))


def test_example_contrast_context_is_preserved_and_must_be_checked():
    from japanese_tutor.schemas.curriculum import Example

    document, c = sample()
    support = c.objects[0].supports[0]
    example = Example(
        id="sample:L05:example:dispreferred",
        lesson_id=c.lesson.id,
        provenance="asserted",
        supports=[support],
        confidence=0.9,
        text=c.objects[0].data.source_text,
        translation="妈妈",
        usage="dispreferred",
    )
    restored = Example.model_validate_json(example.model_dump_json())
    assert restored.usage == "dispreferred" and restored.translation == "妈妈"
    objects = c.objects + [example]
    c = c.model_copy(
        update={
            "objects": objects,
            "sections": [
                c.sections[0].model_copy(update={"object_ids": [o.id for o in objects]}),
            ],
        }
    )
    check = verified(document, c)
    checks = check.objects[:-1] + [
        check.objects[-1].model_copy(
            update={
                "checked_fields": sorted(required_fields(example) - {"usage"}),
            }
        )
    ]
    with pytest.raises(ValueError, match="required field"):
        validate_verification(document, c, check.model_copy(update={"objects": checks}))


def test_no_per_word_phonology_or_duplicate_lexemes():
    document, c = sample()
    lexical = c.objects[0]
    payload = lexical.model_dump(mode="json")
    for subtype in ["kana_reading", "lexical_pitch_accent"]:
        invalid = copy.deepcopy(payload)
        invalid.update(category="phonology", subtype=subtype)
        with pytest.raises(ValidationError):
            Concept.model_validate_json(json.dumps(invalid))
    duplicate = lexical.model_copy(update={"id": lexical.id + "-other-source"})
    invalid = c.model_copy(update={"objects": c.objects + [duplicate]})
    with pytest.raises(ValidationError, match="Duplicate lexical identity"):
        SemanticCurriculum.model_validate_json(invalid.model_dump_json())
    assert lexical.data.notation.reading == "かあ"
    assert lexical.data.notation.pitch_accent.raw_notation == "②"


def test_lexical_merge_preserves_alternate_gloss_and_source_labels():
    from japanese_tutor.curriculum.extract import merge_lexical_concepts

    _, c = sample()
    first = c.objects[0]
    other = first.model_copy(
        update={
            "id": first.id + "-alternate",
            "data": first.data.model_copy(update={"meaning": "母亲", "part_of_speech": "［代］"}),
        }
    )
    merged = merge_lexical_concepts(first, other)
    assert merged.id == first.id
    assert merged.data.notation == first.data.notation
    assert len(merged.data.variants) == 1
    variant = merged.data.variants[0]
    assert (variant.meaning, variant.part_of_speech) == ("母亲", "［代］")
    assert variant.sources == [first.supports[0].source]
    assert merge_lexical_concepts(merged, other).data.variants == merged.data.variants
    changed_reading = other.model_copy(
        update={
            "data": other.data.model_copy(
                update={
                    "notation": other.data.notation.model_copy(update={"reading": "ちがう"}),
                }
            )
        }
    )
    with pytest.raises(ValueError, match="matching lexical"):
        merge_lexical_concepts(first, changed_reading)


def test_shared_group_jlpt_requires_confirmed_series_convention(tmp_path: Path):
    from japanese_tutor.curriculum.extract import load_curriculum_convention, resolve_group_jlpt
    from japanese_tutor.schemas.curriculum import CurriculumConvention

    document, _ = sample()
    rule = CurriculumConvention(
        series="sample",
        version="sample:review-v1",
        shared_group_jlpt=True,
        reviewer="human",
        reviewed_on="2026-09-13",
        reason="Human confirmed shared group label attribution.",
    )
    write_json(tmp_path / "sample-curriculum.json", rule)
    assert load_curriculum_convention(document, tmp_path) == rule
    assert resolve_group_jlpt("N5", 2, rule) == ["N5", "N5"]
    assert resolve_group_jlpt("N4\nN4", 3, rule) == ["N4", "N4", "N4"]
    assert resolve_group_jlpt(None, 3, rule) == [None, None, None]
    assert resolve_group_jlpt("N5", 2, None) is None
    assert resolve_group_jlpt("N4\nN5", 2, rule) == ["N4", "N5"]
    assert resolve_group_jlpt("N4\nN5", 3, rule) is None
    assert resolve_group_jlpt("probably N5", 2, rule) is None
    write_json(tmp_path / "sample-curriculum.json", rule.model_copy(update={"series": "other"}))
    with pytest.raises(ValueError, match="another textbook"):
        load_curriculum_convention(document, tmp_path)


def test_topic_sources_compact_payload_and_item_verification():
    document, c = sample()
    topic = c.objects[1]
    data = topic.data.model_copy(
        update={
            "examples": [
                TopicExample(turns=[TopicTurn(japanese="名词句使用です。")], source_indices=[0])
            ],
            "tables": [
                TopicTable(
                    title="Source table", columns=["形式"], rows=[["です"]], source_indices=[0]
                )
            ],
        }
    )
    topic = topic.model_copy(update={"data": data})
    payload = topic.model_dump(mode="json")
    assert Concept.model_validate_json(topic.model_dump_json()) == topic
    for fault in ["source", "ruby", "table"]:
        invalid = copy.deepcopy(payload)
        if fault == "source":
            invalid["data"]["examples"][0]["source_indices"] = [1]
        elif fault == "ruby":
            invalid["data"]["examples"][0]["turns"][0]["ruby"] = []
        else:
            invalid["data"]["tables"][0]["rows"] = [["です", "余分"]]
        with pytest.raises(ValidationError):
            Concept.model_validate_json(json.dumps(invalid))
    c = c.model_copy(update={"objects": [c.objects[0], topic]})
    check = verified(document, c)
    assert {"notes[0]", "examples[0]", "tables[0]"} <= required_fields(topic)
    last = check.objects[1].model_copy(
        update={"checked_fields": sorted(required_fields(topic) - {"examples[0]"})}
    )
    with pytest.raises(ValueError, match="required field"):
        validate_verification(
            document, c, check.model_copy(update={"objects": [check.objects[0], last]})
        )


def test_topic_sections_reject_standalone_examples_and_micro_claims():
    from japanese_tutor.schemas.curriculum import Example

    document, c = sample()
    document = document.model_copy(
        update={"sections": [document.sections[0].model_copy(update={"section_type": "grammar"})]}
    )
    c = c.model_copy(
        update={
            "metadata": c.metadata.model_copy(
                update={"source_artifact_sha256": artifact_hash(document)}
            )
        }
    )
    validate_curriculum(document, c)
    lexical = c.objects[0]
    example = Example(
        id="sample:L05:example:old",
        lesson_id=c.lesson.id,
        provenance="asserted",
        supports=lexical.supports,
        confidence=0.9,
        text=lexical.data.source_text,
    )
    from japanese_tutor.schemas.curriculum import Dialogue, Utterance

    dialogue = Dialogue(
        id="sample:L05:dialogue:old",
        lesson_id=c.lesson.id,
        provenance="asserted",
        supports=lexical.supports,
        confidence=0.9,
        title="Old mini dialogue",
        utterances=[Utterance(speaker="A", text=lexical.data.source_text)],
    )
    for extra in [
        example,
        dialogue,
        Concept(
            id="sample:L05:phonology:old",
            lesson_id=c.lesson.id,
            provenance="asserted",
            supports=lexical.supports,
            confidence=0.9,
            title="Old fragment",
            category="phonology",
            subtype="sentence_intonation",
            summary="Old fragment",
            data=ClaimData(statement="Old fragment"),
        ),
    ]:
        objects = c.objects + [extra]
        invalid = c.model_copy(
            update={
                "objects": objects,
                "sections": [
                    c.sections[0].model_copy(update={"object_ids": [o.id for o in objects]})
                ],
            }
        )
        with pytest.raises(ValueError, match="topics|topic concepts"):
            validate_curriculum(document, invalid)
