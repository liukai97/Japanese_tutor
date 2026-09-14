"""Learner write boundaries, replay, corrections and independent practice evidence."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from test_curriculum_db import built  # noqa: F401
from typer.testing import CliRunner

from japanese_tutor.cli import app
from japanese_tutor.learner.repository import LearnerRepository, initialize_learner_database
from japanese_tutor.schemas.learner import EvidenceBatch, FrontierUpdate, LearnerProfile


@pytest.fixture
def learner(built, tmp_path):  # noqa: F811
    _, curriculum_db, (_, first), (_, second) = built
    database = tmp_path / "learner.db"
    initialize_learner_database(database)
    repository = LearnerRepository(database, curriculum_db)
    repository.record_frontier(
        FrontierUpdate(
            id="scope-1",
            learned_lesson_ids=[first.lesson.id],
            allowed_lesson_ids=[first.lesson.id],
            reason="Explicit test scope",
        )
    )
    concepts = [item.id for item in first.objects if item.kind == "concept"]
    outside = next(item.id for item in second.objects if item.kind == "concept")
    return repository, concepts, outside, second.lesson.id


def batch(concept_id, number=1, result="independent", **changes):
    value = {
        "idempotency_key": f"batch-{number}",
        "interaction": {
            "id": f"interaction-{number}",
            "activity_id": f"activity-{number}",
            "observed_at": f"2026-09-{number:02}T10:00:00+08:00",
            "task_type": "controlled",
            "prompt": "Test task context",
            "response": "Test response",
        },
        "evidence": [
            {
                "id": f"evidence-{number}",
                "concept_id": concept_id,
                "dimension": "controlled_production",
                "result": result,
                "hint_used": result == "assisted",
                "rationale": "Test assessment rationale",
                "evaluator": {"kind": "human", "rubric_version": "test-v1"},
            }
        ],
    }
    value.update(changes)
    return EvidenceBatch.model_validate_json(json.dumps(value))


def counts(repository):
    with sqlite3.connect(repository.database) as connection:
        return {
            name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("interactions", "learning_evidence", "evidence_batches", "concept_state")
        }


def test_empty_initialization_and_read_boundaries(tmp_path):
    database = tmp_path / "learner.db"
    repository = LearnerRepository(database)
    with pytest.raises(ValueError, match="unavailable"):
        repository.get_profile()
    assert not database.exists()
    initialize_learner_database(database)
    assert repository.get_concept_state() == []
    assert repository.get_learned_lesson_ids() == []
    assert repository.get_review_candidates() == []
    assert repository.get_learner_context()["observed_abilities"] == []
    assert repository.set_profile(LearnerProfile(available_minutes=20))["available_minutes"] == 20
    initialize_learner_database(database)
    assert repository.get_profile()["available_minutes"] == 20
    with pytest.raises(ValueError, match="separate"):
        LearnerRepository(database, database)


def test_initializer_rejects_curriculum_and_unknown_versions(built, tmp_path):  # noqa: F811
    curriculum_db = built[1]
    before = curriculum_db.read_bytes()
    with pytest.raises(ValueError, match="Unsupported learner"):
        initialize_learner_database(curriculum_db)
    assert curriculum_db.read_bytes() == before
    database = tmp_path / "future.db"
    initialize_learner_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE metadata SET value='future' WHERE key='learner_database_version'"
        )
    with pytest.raises(ValueError, match="migration"):
        initialize_learner_database(database)


def test_multi_concept_restart_idempotency_and_replay(learner):
    repository, concepts, _, _ = learner
    request = batch(concepts[0])
    raw = request.model_dump(mode="json")
    raw["evidence"].append(
        {
            **raw["evidence"][0],
            "id": "evidence-second",
            "concept_id": concepts[1],
            "dimension": "recognition",
            "result": "partial",
            "error_codes": ["test_error"],
        }
    )
    request = EvidenceBatch.model_validate_json(json.dumps(raw))
    receipt = repository.record_evidence(request)
    before = counts(repository)
    states = repository.get_concept_state()
    reopened = LearnerRepository(repository.database)
    assert reopened.record_evidence(request) == receipt
    assert counts(repository) == before
    assert len(states) == 2
    assert {item["dimension"] for item in states} == {"recognition", "controlled_production"}
    assert all(item["status"] == "insufficient_evidence" for item in states)
    assert reopened.rebuild_state()["state_count"] == 2
    assert reopened.get_concept_state() == states
    assert reopened.get_recent_history()[0]["response"] == "Test response"
    changed = request.model_dump(mode="json")
    changed["interaction"]["response"] = "different response"
    with pytest.raises(ValueError, match="idempotency conflict"):
        reopened.record_evidence(EvidenceBatch.model_validate_json(json.dumps(changed)))
    assert counts(repository) == before


@pytest.mark.parametrize("invalid", ["unknown", "outside"])
def test_invalid_concept_rolls_back_entire_batch(learner, invalid):
    repository, concepts, outside, _ = learner
    request = batch(concepts[0]).model_dump(mode="json")
    request["evidence"].append(
        {
            **request["evidence"][0],
            "id": "invalid-evidence",
            "concept_id": "unknown" if invalid == "unknown" else outside,
        }
    )
    before = counts(repository)
    with pytest.raises(ValueError):
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(request)))
    assert counts(repository) == before


def test_projection_failure_rolls_back_evidence_and_receipt(learner, monkeypatch):
    repository, concepts, _, _ = learner
    repository.record_evidence(batch(concepts[0]))
    before = counts(repository)
    previous_state = repository.get_concept_state()

    def fail(connection):
        connection.execute("DELETE FROM concept_state")
        raise RuntimeError("Injected projection failure")

    monkeypatch.setattr(repository, "_rebuild", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        repository.record_evidence(batch(concepts[0], 2, "partial"))
    assert counts(repository) == before
    assert repository.get_concept_state() == previous_state


@pytest.mark.parametrize(
    "changes",
    [
        {"dimension": "pitch_score"},
        {"result": "mastered"},
        {"score": 0.8},
        {"hint_used": True},
        {"result": "assisted", "hint_used": False},
        {"confidence": 0.84},
        {"supersedes": "evidence-1"},
        {"kind": "retraction", "result": None},
        {"evaluator": {"kind": "llm", "rubric_version": "v1"}},
    ],
)
def test_rejects_invalid_evidence_contract(changes):
    raw = batch("concept").model_dump(mode="json")
    raw["evidence"][0].update(changes)
    with pytest.raises(ValidationError):
        EvidenceBatch.model_validate_json(json.dumps(raw))


def test_time_and_interaction_conflicts(learner):
    repository, concepts, _, _ = learner
    raw = batch(concepts[0]).model_dump(mode="json")
    raw["interaction"]["observed_at"] = "2026-09-01T10:00:00"
    with pytest.raises(ValidationError):
        EvidenceBatch.model_validate_json(json.dumps(raw))
    raw["interaction"]["observed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    repository.record_evidence(batch(concepts[0]))
    raw = batch(concepts[0]).model_dump(mode="json")
    raw["idempotency_key"] = "different-batch"
    raw["interaction"]["response"] = "overwrite attempt"
    with pytest.raises(ValueError, match="different content"):
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))


def test_retries_do_not_count_as_independent_practice(learner):
    repository, concepts, _, _ = learner
    repository.record_evidence(batch(concepts[0], result="unsuccessful"))
    for number in (2, 3):
        raw = batch(concepts[0], number, "assisted").model_dump(mode="json")
        raw["interaction"].update({"activity_id": "activity-1", "retry_of": "interaction-1"})
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    state = repository.get_concept_state()[0]
    assert state["effective_observation_count"] == 3
    assert state["sufficient_activity_count"] == 1
    assert state["recent_independent_successes"] == 0
    assert state["last_attempt_result"] == "assisted"
    assert state["recent_hint_dependency_count"] == 1
    assert state["recent_results"] == ["unsuccessful"]
    raw = batch(concepts[0], 4).model_dump(mode="json")
    raw["interaction"]["activity_id"] = "activity-1"
    before = counts(repository)
    with pytest.raises(ValueError, match="retry_of"):
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    assert counts(repository) == before


def test_independent_dates_and_insufficient_confidence(learner):
    repository, concepts, _, _ = learner
    for number in (1, 2, 3):
        repository.record_evidence(batch(concepts[0], number))
    assert repository.get_concept_state()[0]["status"] == "consistent_recently"
    raw = batch(concepts[0], 4, "unsuccessful").model_dump(mode="json")
    raw["evidence"][0]["confidence"] = "insufficient"
    repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    state = repository.get_concept_state()[0]
    assert state["sufficient_activity_count"] == 3
    assert state["status"] == "consistent_recently"
    assert "needs_confirmation" in repository.get_review_candidates()[0]["reasons"]


def correction(request, identifier, supersedes, **changes):
    raw = request.model_dump(mode="json")
    raw["idempotency_key"] = identifier
    raw["evidence"][0].update({"id": identifier, "supersedes": supersedes, **changes})
    return EvidenceBatch.model_validate_json(json.dumps(raw))


def test_correction_retraction_and_recovery_without_curriculum(learner):
    repository, concepts, _, _ = learner
    original = batch(concepts[0], result="unsuccessful")
    repository.record_evidence(original)
    portable = LearnerRepository(repository.database)
    fixed = correction(original, "fixed", "evidence-1", result="independent")
    portable.record_evidence(fixed)
    assert portable.get_concept_state()[0]["recent_results"] == ["independent"]
    history = portable.get_learning_evidence()
    assert len(history) == 2
    assert history[1]["superseded"]
    with pytest.raises(ValueError, match="latest"):
        portable.record_evidence(correction(original, "branch", "evidence-1"))
    withdrawn = correction(original, "withdrawn", "fixed", kind="retraction", result=None)
    portable.record_evidence(withdrawn)
    assert portable.get_concept_state() == []
    restored = correction(original, "restored", "withdrawn", result="partial")
    portable.record_evidence(restored)
    before = portable.get_concept_state()
    portable.rebuild_state()
    assert portable.get_concept_state() == before
    assert len(portable.get_recent_history()) == 1


def test_sql_append_only_guards(learner):
    repository, concepts, _, _ = learner
    repository.record_evidence(batch(concepts[0]))
    for table in ("learning_evidence", "interactions", "curriculum_frontier", "evidence_batches"):
        for operation in (f"DELETE FROM {table}", f"UPDATE {table} SET id=id"):
            with sqlite3.connect(repository.database) as connection:
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    connection.execute(operation)


def test_correction_preserves_first_attempt_order_for_equal_timestamps(learner):
    repository, concepts, _, _ = learner
    original = batch(concepts[0], result="unsuccessful")
    repository.record_evidence(original)
    raw = batch(concepts[0], 2, "assisted").model_dump(mode="json")
    raw["interaction"].update(
        {
            "activity_id": "activity-1",
            "retry_of": "interaction-1",
            "observed_at": original.interaction.observed_at.isoformat(),
        }
    )
    repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    repository.record_evidence(
        correction(original, "equal-time-fixed", "evidence-1", result="partial")
    )
    state = repository.get_concept_state()[0]
    assert state["recent_results"] == ["partial"]
    assert state["last_attempt_result"] == "assisted"
    assert state["sufficient_activity_count"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"concept_id": "different-concept"},
        {"dimension": "recall"},
        {"supersedes": "unknown"},
    ],
)
def test_correction_rejects_scope_changes_and_unknown_reference(learner, changes):
    repository, concepts, _, _ = learner
    original = batch(concepts[0])
    repository.record_evidence(original)
    before = counts(repository)
    target = changes.get("supersedes", "evidence-1")
    other_changes = {key: value for key, value in changes.items() if key != "supersedes"}
    with pytest.raises(ValueError):
        repository.record_evidence(correction(original, "bad-fix", target, **other_changes))
    assert counts(repository) == before


def test_single_day_successes_are_not_stable_and_time_does_not_decay_state(learner, monkeypatch):
    repository, concepts, _, _ = learner
    for number in (1, 2, 3):
        raw = batch(concepts[0], number).model_dump(mode="json")
        raw["interaction"]["observed_at"] = f"2026-09-01T10:00:0{number}+08:00"
        repository.record_evidence(EvidenceBatch.model_validate_json(json.dumps(raw)))
    state = repository.get_concept_state()
    assert state[0]["status"] == "developing"
    monkeypatch.setattr(
        "japanese_tutor.learner.repository._now", lambda: datetime(2026, 12, 1, tzinfo=UTC)
    )
    assert "not_observed_recently" in repository.get_review_candidates()[0]["reasons"]
    assert repository.get_concept_state() == state


def test_context_rejects_a_budget_that_cannot_preserve_frontier(learner):
    repository, _, _, _ = learner
    frontier = repository.get_frontier()
    repository.record_frontier(
        FrontierUpdate(
            id="verbose-scope",
            learned_lesson_ids=frontier["learned_lesson_ids"],
            allowed_lesson_ids=frontier["allowed_lesson_ids"],
            reason="r" * 1000,
        )
    )
    with pytest.raises(ValueError, match="complete frontier"):
        repository.get_learner_context(1000)
    assert repository.get_learner_context(2000)["frontier"] == repository.get_frontier()


def test_frontier_is_explicit_and_separate_from_mastery(learner):
    repository, concepts, outside, second_lesson = learner
    original = repository.get_frontier()
    update = FrontierUpdate(
        id="scope-2",
        learned_lesson_ids=original["learned_lesson_ids"],
        allowed_lesson_ids=original["allowed_lesson_ids"] + [second_lesson],
        focus_concept_ids=[outside],
        reason="Explicit extension",
    )
    assert repository.record_frontier(update) == repository.record_frontier(update)
    repository.record_evidence(batch(outside))
    assert repository.get_learned_lesson_ids() == original["learned_lesson_ids"]
    assert repository.get_frontier()["focus_concept_ids"] == [outside]
    with pytest.raises(ValueError, match="conflict"):
        repository.record_frontier(update.model_copy(update={"reason": "changed"}))
    with pytest.raises(ValueError, match="Unknown lesson"):
        repository.record_frontier(
            FrontierUpdate(
                id="bad-scope",
                allowed_lesson_ids=["unknown"],
                reason="invalid",
            )
        )
    assert repository.get_frontier()["id"] == "scope-2"
    assert all(item["concept_id"] != concepts[0] for item in repository.get_concept_state())


def test_context_budget_candidates_and_pagination(learner):
    repository, concepts, _, _ = learner
    for number in (1, 2, 3):
        repository.record_evidence(batch(concepts[0], number, "assisted"))
    repository.set_profile(LearnerProfile(goals=["long goal " * 50] * 20))
    context = repository.get_learner_context(1000)
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 1000
    assert context["frontier"] == repository.get_frontier()
    assert context["profile_omitted"]
    candidates = repository.get_review_candidates()
    assert "hint_dependency" in candidates[0]["reasons"]
    assert "question" not in candidates[0]
    assert repository.get_recent_history(1, 1)[0]["id"] == "interaction-2"
    assert repository.get_learning_evidence(limit=1, offset=1)[0]["id"] == "evidence-2"
    with pytest.raises(ValueError, match="limit"):
        repository.get_review_candidates(101)


def test_cli_end_to_end_and_frontier_search(learner, tmp_path):
    repository, concepts, _, _ = learner
    runner = CliRunner()
    options = ["--learner-db", str(repository.database)]
    request = tmp_path / "evidence.json"
    request.write_text(batch(concepts[0]).model_dump_json(), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "record-evidence",
            str(request),
            *options,
            "--curriculum-db",
            str(repository.curriculum.database),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["evidence_ids"] == ["evidence-1"]
    result = runner.invoke(app, ["learner-context", *options, "--max-chars", "1000"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip()) <= 1000
    assert json.loads(result.stdout)["frontier"] == repository.get_frontier()
    assert runner.invoke(app, ["rebuild-state", *options]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "search",
            "です",
            "--learned-only",
            *options,
            "--curriculum-db",
            str(repository.curriculum.database),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)
    assert all(
        item["lesson_id"] in repository.get_learned_lesson_ids()
        for item in json.loads(result.stdout)
    )
