"""Compilation gates, read-only boundaries and Japanese retrieval behavior."""

import json
import sqlite3

import pytest
from test_semantic import sample, verified
from typer.testing import CliRunner

from japanese_tutor.cli import app
from japanese_tutor.curriculum.build_db import build_curriculum_database
from japanese_tutor.curriculum.extract import artifact_hash, write_json
from japanese_tutor.curriculum.repository import CurriculumRepository
from japanese_tutor.schemas.curriculum import (
    ConceptRelation,
    Dialogue,
    ExerciseExample,
    SemanticCurriculum,
    Support,
    TopicExample,
    TopicNote,
    TopicTable,
    TopicTurn,
    Utterance,
)
from japanese_tutor.schemas.source import (
    JapaneseText,
    LessonDocument,
    Page,
    PageRef,
    SourceRef,
    TextBlock,
)


def bundle(directory, number=5):
    directory.mkdir(parents=True)
    document, curriculum = sample()
    if number != 5:
        raw = (
            document.model_dump_json()
            .replace("L05", f"L{number:02}")
            .replace("l05", f"l{number:02}")
        )
        document = LessonDocument.model_validate_json(raw)
        raw = (
            curriculum.model_dump_json()
            .replace("L05", f"L{number:02}")
            .replace("l05", f"l{number:02}")
        )
        curriculum = SemanticCurriculum.model_validate_json(raw)
    curriculum = curriculum.model_copy(
        update={
            "metadata": curriculum.metadata.model_copy(
                update={"source_artifact_sha256": artifact_hash(document)}
            )
        }
    )
    topic = curriculum.objects[1]
    topic = topic.model_copy(
        update={
            "data": topic.data.model_copy(
                update={
                    "examples": [
                        TopicExample(
                            turns=[TopicTurn(japanese="名词句使用です。")], source_indices=[0]
                        )
                    ],
                    "tables": [
                        TopicTable(
                            title="形式", columns=["形式"], rows=[["です"]], source_indices=[0]
                        )
                    ],
                }
            )
        }
    )
    common = {
        "lesson_id": topic.lesson_id,
        "supports": topic.supports,
        "provenance": "derived",
        "confidence": 0.95,
    }
    text = JapaneseText(text=topic.supports[0].quote, sources=[topic.supports[0].source])
    dialogue = Dialogue(
        id=topic.lesson_id + ":dialogue:sample",
        title="会话",
        **common,
        utterances=[Utterance(speaker="A", text=text)],
    )
    exercise = ExerciseExample(
        id=topic.lesson_id + ":exercise:sample",
        title="练习",
        **common,
        source_text=[text],
        target_concept_ids=[topic.id],
        task_type="controlled",
        progression="controlled",
    )
    relation = ConceptRelation(
        id=topic.lesson_id + ":relation:sample",
        **common,
        source_id=topic.id,
        target_id=dialogue.id,
        relation_type="exemplified_by",
    )
    objects = [curriculum.objects[0], topic, dialogue, exercise, relation]
    curriculum = curriculum.model_copy(
        update={
            "objects": objects,
            "sections": [
                curriculum.sections[0].model_copy(update={"object_ids": [o.id for o in objects]})
            ],
        }
    )
    verification = verified(document, curriculum)
    approval = {
        "document_sha256": document.manifest.document_sha256,
        "source_artifact_sha256": artifact_hash(document),
        "curriculum_sha256": artifact_hash(curriculum),
        "human_approved": True,
        "approved_on": "2026-09-13",
        "approval_basis": "Test approval",
    }
    for name, value in (
        ("document", document),
        ("curriculum", curriculum),
        ("verification", verification),
        ("approval", approval),
    ):
        write_json(directory / f"{name}.json", value)
    return document, curriculum


@pytest.fixture
def built(tmp_path):
    first = bundle(tmp_path / "l05")
    second = bundle(tmp_path / "l06", 6)
    manifest = tmp_path / "inputs.json"
    write_json(
        manifest,
        {
            "lessons": [
                {
                    name: f"{lesson}/{name}.json"
                    for name in ("document", "curriculum", "verification", "approval")
                }
                for lesson in ("l05", "l06")
            ]
        },
    )
    destination = tmp_path / "curriculum.db"
    build_curriculum_database(manifest, destination)
    return manifest, destination, first, second


def test_artifact_roundtrip_and_structured_reads(built):
    _, destination, (document, curriculum), _ = built
    repository = CurriculumRepository(destination)
    concepts = [o.model_dump(mode="json") for o in curriculum.objects if o.kind == "concept"]
    assert repository.get_concepts([o["id"] for o in concepts]) == concepts
    assert repository.get_source(
        document.manifest.document_id, document.sections[0].section_id, page=1
    ) == document.sections[0].model_dump(mode="json")
    assert repository.get_lesson_outline(curriculum.lesson.id)["sections"][0]["pages"] == [1]
    assert repository.get_exercise_examples(curriculum.lesson.id, ["controlled"])[0] == (
        curriculum.objects[3].model_dump(mode="json")
    )
    assert repository.get_related_concepts([concepts[1]["id"]], ["exemplified_by"]) == [
        curriculum.objects[4].model_dump(mode="json")
    ]
    assert (
        repository.get_concept_sources([concepts[1]["id"]])[0]["supports"]
        == concepts[1]["supports"]
    )
    with pytest.raises(ValueError, match="Unknown curriculum object"):
        repository.get_concepts(["missing"])
    with pytest.raises(ValueError, match="outside"):
        repository.get_source(document.manifest.document_id, document.sections[0].section_id, 2)
    with pytest.raises(ValueError, match="relation type"):
        repository.get_related_concepts([concepts[1]["id"]], ["guessed"])


def test_multilingual_literal_search_scope_and_pagination(built):
    _, destination, _, _ = built
    repository = CurriculumRepository(destination)
    for query in ("妈妈", "かあ", "名词句", "です", "Ｎ５"):
        assert repository.search_textbook(query), query
    results = repository.search_textbook("名词句", concept_types=["grammar"])
    assert len(results) == 2 and all(r["source"]["page"] == 1 for r in results)
    assert all(len(r["source_snippet"]) <= 182 for r in results)
    assert "data" not in results[0]
    assert repository.search_textbook("名词句", concept_types=["grammar"], limit=1) == results[:1]
    assert (
        repository.search_textbook("名词句", concept_types=["grammar"], limit=1, offset=1)
        == results[1:]
    )
    assert (
        len(
            repository.search_textbook(
                "名词句",
                learned_only=True,
                allowed_lesson_ids=["sample:L05"],
                concept_types=["grammar"],
            )
        )
        == 1
    )
    assert repository.search_textbook("名词句", learned_only=True, allowed_lesson_ids=[]) == []
    assert (
        repository.search_textbook(
            "名词句",
            learned_only=True,
            allowed_lesson_ids=["sample:L05"],
            lesson_range=["sample:L06"],
        )
        == []
    )
    assert repository.search_textbook("名词句", source_page=2) == []
    assert repository.search_textbook('" OR "anything') == []
    with pytest.raises(ValueError, match="allowed_lesson_ids"):
        repository.search_textbook("名词句", learned_only=True)
    with pytest.raises(ValueError):
        repository.search_textbook("名词句", limit=101)


def test_topic_local_source_indices_preserve_page_evidence(built):
    manifest, destination, (document, curriculum), _ = built
    ref = SourceRef(
        document_id=document.manifest.document_id,
        page=2,
        section_id=document.sections[0].section_id,
    )
    literal = "特殊规则：语调升降需要区分。"
    section = document.sections[0].model_copy(
        update={
            "pages": [*document.sections[0].pages, PageRef(document_id=ref.document_id, page=2)],
            "blocks": [
                *document.sections[0].blocks,
                TextBlock(content=JapaneseText(text=literal, sources=[ref])),
            ],
        }
    )
    document = document.model_copy(
        update={
            "sections": [section],
            "manifest": document.manifest.model_copy(
                update={
                    "page_count": 2,
                    "pages": [*document.manifest.pages, Page(page=2, width=600.0, height=800.0)],
                }
            ),
        }
    )
    topic = curriculum.objects[1]
    topic = topic.model_copy(
        update={
            "supports": [
                *topic.supports,
                Support(source=ref, quote=literal, reason="Second-page rule"),
            ],
            "data": topic.data.model_copy(
                update={"notes": [*topic.data.notes, TopicNote(text=literal, source_indices=[1])]}
            ),
        }
    )
    curriculum = curriculum.model_copy(
        update={
            "objects": [curriculum.objects[0], topic, *curriculum.objects[2:]],
            "metadata": curriculum.metadata.model_copy(
                update={"source_artifact_sha256": artifact_hash(document)}
            ),
        }
    )
    directory = manifest.parent / "l05"
    write_json(directory / "document.json", document)
    write_json(directory / "curriculum.json", curriculum)
    write_json(directory / "verification.json", verified(document, curriculum))
    approval = json.loads((directory / "approval.json").read_text(encoding="utf-8"))
    approval.update(
        source_artifact_sha256=artifact_hash(document), curriculum_sha256=artifact_hash(curriculum)
    )
    write_json(directory / "approval.json", approval)
    build_curriculum_database(manifest, destination)
    repository = CurriculumRepository(destination)
    assert repository.search_textbook("语调升降", source_page=1, concept_types=["grammar"]) == []
    results = repository.search_textbook("语调升降", source_page=2, concept_types=["grammar"])
    assert len(results) == 1 and literal in results[0]["source_snippet"]
    original = repository.get_source(ref.document_id, ref.section_id, page=2)
    assert original["blocks"][0]["content"]["text"] == literal
    # Page-filtered source is still a valid rich Section, including its page context.
    from japanese_tutor.schemas.source import Section

    Section.model_validate_json(json.dumps(original, ensure_ascii=False))


@pytest.mark.parametrize("fault", ["approval", "verification", "coverage", "proposed", "source"])
def test_release_gates_preserve_existing_database(built, fault):
    manifest, destination, _, _ = built
    before = destination.read_bytes()
    directory = manifest.parent / "l05"
    name = {
        "approval": "approval",
        "verification": "verification",
        "coverage": "verification",
        "proposed": "curriculum",
        "source": "curriculum",
    }[fault]
    path = directory / f"{name}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if fault in {"approval", "verification"}:
        value["curriculum_sha256"] = "0" * 64
    elif fault == "coverage":
        value["sections"] = []
    elif fault == "proposed":
        value["objects"][-1]["provenance"] = "proposed"
        changed = SemanticCurriculum.model_validate_json(json.dumps(value, ensure_ascii=False))
        document = LessonDocument.model_validate_json(
            (directory / "document.json").read_text(encoding="utf-8")
        )
        write_json(directory / "verification.json", verified(document, changed))
        approval = json.loads((directory / "approval.json").read_text(encoding="utf-8"))
        approval["curriculum_sha256"] = artifact_hash(changed)
        write_json(directory / "approval.json", approval)
    else:
        value["objects"][0]["supports"][0]["source"]["page"] = 2
    write_json(path, value)
    with pytest.raises(ValueError):
        build_curriculum_database(manifest, destination)
    assert destination.read_bytes() == before


def test_failed_compilation_readonly_and_cli_boundary(built):
    manifest, destination, _, _ = built
    before = destination.read_bytes()
    bad_sql = manifest.parent / "broken.sql"
    bad_sql.write_text("CREATE TABLE metadata(key,value);", encoding="utf-8")
    with pytest.raises(sqlite3.Error):
        build_curriculum_database(manifest, destination, bad_sql)
    assert destination.read_bytes() == before
    assert not list(manifest.parent.glob("*.db.tmp"))
    repository = CurriculumRepository(destination)
    with repository._connect() as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM concepts")
    missing = manifest.parent / "missing.db"
    with pytest.raises(ValueError, match="unavailable"):
        CurriculumRepository(missing).get_lesson_outline("sample:L05")
    assert not missing.exists()
    learner = manifest.parent / "learner.db"
    learner.write_bytes(b"learner evidence must remain untouched")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-db",
            str(manifest),
            "--curriculum-db",
            str(destination),
            "--learner-db",
            str(learner),
        ],
    )
    assert result.exit_code == 0, result.output
    assert learner.read_bytes() == b"learner evidence must remain untouched"
    result = runner.invoke(
        app, ["search", "名词句", "--curriculum-db", str(destination), "--concept-type", "grammar"]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 2
    result = runner.invoke(
        app, ["textbook", "outline", "sample:L05", "--curriculum-db", str(destination)]
    )
    assert result.exit_code == 0, result.output
