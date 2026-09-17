"""Validated learner services. Models submit evidence, never write derived state."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from japanese_tutor.curriculum.repository import CurriculumRepository
from japanese_tutor.learner.projection import PROJECTION_VERSION, project
from japanese_tutor.schemas.learner import (
    EvidenceBatch,
    EvidenceBatchSet,
    FrontierUpdate,
    LearnerProfile,
)

DATABASE_VERSION = "1"
SQL_PATH = Path(__file__).resolve().parents[3] / "sql" / "learner.sql"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)


def _bounds(limit: int, offset: int = 0) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise ValueError("offset must be 0..10000")


def initialize_learner_database(database: Path, sql_path: Path = SQL_PATH) -> dict:
    """Initialize a new or stage-0 empty learner DB; never clear existing history."""
    database = Path(database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        if tables:
            if "metadata" not in tables:
                raise ValueError("Not an empty or supported learner database")
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='learner_database_version'"
            ).fetchone()
            if version is None or version[0] != DATABASE_VERSION:
                raise ValueError("Unsupported learner database; explicit migration required")
        try:
            connection.executescript(Path(sql_path).read_text(encoding="utf-8"))
        except Exception:
            connection.rollback()
            raise
        connection.execute(
            "INSERT OR IGNORE INTO learner_profile VALUES (1, ?)",
            (_json(LearnerProfile().model_dump(mode="json")),),
        )
    return {"database": str(database), "database_version": DATABASE_VERSION}


class LearnerRepository:
    def __init__(self, database: Path, curriculum_database: Path | None = None):
        self.database = Path(database).resolve()
        self.curriculum = (
            CurriculumRepository(curriculum_database) if curriculum_database is not None else None
        )
        if self.curriculum is not None and self.curriculum.database == self.database:
            raise ValueError("Curriculum and learner database paths must be separate")

    @contextmanager
    def _connect(self, write: bool = False):
        connection = None
        try:
            mode = "rw" if write else "ro"
            connection = sqlite3.connect(self.database.as_uri() + f"?mode={mode}", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
                # Evidence rows are projected before their immutable receipt is stored.
                # Deferral keeps the whole set atomic while satisfying the batch FK at commit.
                connection.execute("PRAGMA defer_foreign_keys=ON")
            else:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='learner_database_version'"
            ).fetchone()
            if version is None or version[0] != DATABASE_VERSION:
                raise ValueError("Unsupported learner database; initialize or migrate explicitly")
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error as error:
            raise ValueError(f"Learner database unavailable or write rejected: {error}") from error
        finally:
            if connection is not None:
                connection.close()  # Closing also rolls back all failed transactions.

    def _curriculum(self) -> CurriculumRepository:
        if self.curriculum is None:
            raise ValueError("This operation requires a curriculum database")
        return self.curriculum

    def get_profile(self) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT data_json FROM learner_profile WHERE id=1").fetchone()
            return json.loads(row[0]) if row else LearnerProfile().model_dump(mode="json")

    def set_profile(self, profile: LearnerProfile) -> dict:
        profile = LearnerProfile.model_validate_json(profile.model_dump_json())
        result = profile.model_dump(mode="json")
        with self._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO learner_profile VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json",
                (_json(result),),
            )
        return result

    @staticmethod
    def _frontier(connection) -> dict:
        row = connection.execute(
            "SELECT payload_json FROM curriculum_frontier ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"learned_lesson_ids": [], "allowed_lesson_ids": [], "focus_concept_ids": []}
        )

    def get_frontier(self) -> dict:
        with self._connect() as connection:
            return self._frontier(connection)

    def get_learned_lesson_ids(self) -> list[str]:
        return self.get_frontier()["learned_lesson_ids"]

    def record_frontier(self, update: FrontierUpdate) -> dict:
        """Explicit append-only scope snapshots; learning evidence does not advance scope."""
        update = FrontierUpdate.model_validate_json(update.model_dump_json())
        result = update.model_dump(mode="json")
        payload = _json(result)
        with self._connect(write=True) as connection:
            previous = connection.execute(
                "SELECT payload_json FROM curriculum_frontier WHERE id=?", (update.id,)
            ).fetchone()
            if previous:
                if previous[0] != payload:
                    raise ValueError("Frontier idempotency conflict")
                return json.loads(previous[0])
            curriculum = self._curriculum()
            for lesson_id in update.allowed_lesson_ids:
                curriculum.get_lesson_outline(lesson_id)
            if update.focus_concept_ids:
                concepts = curriculum.get_concepts(update.focus_concept_ids)
                if any(item["lesson_id"] not in update.allowed_lesson_ids for item in concepts):
                    raise ValueError("Focus concepts must belong to allowed lessons")
            connection.execute(
                "INSERT INTO curriculum_frontier(id,payload_json,recorded_at) VALUES (?,?,?)",
                (update.id, payload, _now().isoformat()),
            )
        return result

    @staticmethod
    def _effective(connection) -> list[dict]:
        rows = connection.execute(
            "SELECT e.payload_json,e.lesson_id,i.activity_id,i.observed_at,e.sequence "
            "FROM learning_evidence e JOIN interactions i ON i.id=e.interaction_id "
            "WHERE e.kind='observation' AND NOT EXISTS "
            "(SELECT 1 FROM learning_evidence successor WHERE successor.supersedes=e.id) "
            "ORDER BY i.observed_at,i.rowid,e.sequence"
        )
        return [
            {
                **json.loads(row[0]),
                "lesson_id": row[1],
                "activity_id": row[2],
                "observed_at": row[3],
                "sequence": row[4],
            }
            for row in rows
        ]

    @classmethod
    def _rebuild(cls, connection) -> list[dict]:
        states = project(cls._effective(connection))
        connection.execute("DELETE FROM concept_state")
        connection.executemany(
            "INSERT INTO concept_state VALUES (?,?,?)",
            [(item["concept_id"], item["dimension"], _json(item)) for item in states],
        )
        for key, value in (
            ("projection_version", PROJECTION_VERSION),
            ("state_rebuilt_at", _now().isoformat()),
        ):
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, value))
        return states

    def rebuild_state(self) -> dict:
        # Rebuild requires only portable learner evidence, including orphaned curriculum IDs.
        with self._connect(write=True) as connection:
            states = self._rebuild(connection)
        return {"state_count": len(states), "projection_version": PROJECTION_VERSION}

    @staticmethod
    def _state_delta(states: list[dict], batches: list[EvidenceBatch]) -> list[dict]:
        """Return post-write state only for concept dimensions touched by the write."""

        affected = sorted(
            {
                (item.concept_id, item.dimension)
                for batch in batches
                for item in batch.evidence
            }
        )
        indexed = {(item["concept_id"], item["dimension"]): item for item in states}
        return [
            {
                "concept_id": concept_id,
                "dimension": dimension,
                "state": indexed.get((concept_id, dimension)),
            }
            for concept_id, dimension in affected
        ]

    @staticmethod
    def _merge_state_deltas(receipts: list[dict]) -> list[dict]:
        indexed = {
            (item["concept_id"], item["dimension"]): item
            for receipt in receipts
            for item in receipt["state_delta"]
        }
        return [indexed[key] for key in sorted(indexed)]

    def _append_batch(
        self,
        connection,
        batch: EvidenceBatch,
        *,
        allowed_lesson_ids: list[str],
        concepts: dict[str, dict],
    ) -> dict:
        """Append one prevalidated batch inside the caller's transaction."""

        interaction = batch.interaction
        interaction_payload = _json(interaction.model_dump(mode="json"))
        if interaction.observed_at > _now() + timedelta(minutes=5):
            raise ValueError("Observation time cannot be in the future")
        existing = connection.execute(
            "SELECT payload_json FROM interactions WHERE id=?", (interaction.id,)
        ).fetchone()
        if existing and existing[0] != interaction_payload:
            raise ValueError("Interaction ID already has different content")
        if not existing:
            if interaction.retry_of:
                original = connection.execute(
                    "SELECT payload_json FROM interactions WHERE id=?", (interaction.retry_of,)
                ).fetchone()
                if original is None:
                    raise ValueError("Unknown retry interaction")
                original = json.loads(original[0])
                if (
                    original["activity_id"] != interaction.activity_id
                    or original["session_id"] != interaction.session_id
                    or datetime.fromisoformat(original["observed_at"]) > interaction.observed_at
                ):
                    raise ValueError("Retry must share activity/session and follow original")
            elif connection.execute(
                "SELECT 1 FROM interactions WHERE activity_id=?", (interaction.activity_id,)
            ).fetchone():
                raise ValueError("Repeated activity must identify retry_of")
            connection.execute(
                "INSERT INTO interactions VALUES (?,?,?,?,?,?)",
                (
                    interaction.id,
                    interaction.activity_id,
                    interaction.session_id,
                    interaction.observed_at.isoformat(),
                    interaction.task_type,
                    interaction_payload,
                ),
            )
        for item in batch.evidence:
            if item.supersedes:
                original = connection.execute(
                    "SELECT * FROM learning_evidence WHERE id=?", (item.supersedes,)
                ).fetchone()
                if original is None:
                    raise ValueError("Unknown superseded evidence")
                if connection.execute(
                    "SELECT 1 FROM learning_evidence WHERE supersedes=?", (item.supersedes,)
                ).fetchone():
                    raise ValueError("Correction must supersede the latest evidence")
                if (
                    original["interaction_id"] != interaction.id
                    or original["concept_id"] != item.concept_id
                    or original["dimension"] != item.dimension
                ):
                    raise ValueError("Correction must preserve interaction/concept/dimension")
                lesson_id = original["lesson_id"]
            else:
                concept = concepts[item.concept_id]
                lesson_id = concept["lesson_id"]
                if lesson_id not in allowed_lesson_ids:
                    raise ValueError(f"Concept is outside allowed lessons: {item.concept_id}")
                if item.dimension == "natural_usage" and concept["category"] == "phonology":
                    raise ValueError("Phonology cannot be assessed as natural_usage")
            connection.execute(
                "INSERT INTO learning_evidence "
                "(id,batch_id,interaction_id,concept_id,lesson_id,dimension,kind,supersedes,"
                "payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item.id,
                    batch.idempotency_key,
                    interaction.id,
                    item.concept_id,
                    lesson_id,
                    item.dimension,
                    item.kind,
                    item.supersedes,
                    _json(item.model_dump(mode="json")),
                ),
            )
        return {
            "interaction_id": interaction.id,
            "evidence_ids": [item.id for item in batch.evidence],
            "projection_version": PROJECTION_VERSION,
        }

    def record_evidence_set(self, batch_set: EvidenceBatchSet) -> dict:
        """Commit one to three activity batches atomically and rebuild state once."""

        batch_set = EvidenceBatchSet.model_validate_json(batch_set.model_dump_json())
        payload_hashes = {
            batch.idempotency_key: hashlib.sha256(
                _json(batch.model_dump(mode="json")).encode()
            ).hexdigest()
            for batch in batch_set.batches
        }
        with self._connect(write=True) as connection:
            stored: dict[str, dict] = {}
            pending: list[EvidenceBatch] = []
            for batch in batch_set.batches:
                receipt = connection.execute(
                    "SELECT payload_sha256,response_json FROM evidence_batches WHERE id=?",
                    (batch.idempotency_key,),
                ).fetchone()
                if receipt:
                    if receipt[0] != payload_hashes[batch.idempotency_key]:
                        raise ValueError(
                            "Evidence idempotency conflict: same key, different payload"
                        )
                    stored[batch.idempotency_key] = json.loads(receipt[1])
                else:
                    pending.append(batch)

            if stored and pending:
                raise ValueError("Evidence batch set cannot mix recorded and new batches")

            if not pending:
                states = self._states(connection)
                receipts = []
                for batch in batch_set.batches:
                    receipt = stored[batch.idempotency_key]
                    if "state_delta" not in receipt:  # Backward compatibility for old receipts.
                        receipt = {
                            **receipt,
                            "state_delta": self._state_delta(states, [batch]),
                        }
                    receipts.append(receipt)
                return {
                    "batch_receipts": receipts,
                    "projection_version": PROJECTION_VERSION,
                    "state_delta": self._merge_state_deltas(receipts),
                }

            new_ids = list(
                dict.fromkeys(
                    item.concept_id
                    for batch in pending
                    for item in batch.evidence
                    if item.supersedes is None
                )
            )
            concepts = (
                {item["id"]: item for item in self._curriculum().get_concepts(new_ids)}
                if new_ids
                else {}
            )
            allowed = self._frontier(connection)["allowed_lesson_ids"]
            base_receipts = [
                self._append_batch(
                    connection,
                    batch,
                    allowed_lesson_ids=allowed,
                    concepts=concepts,
                )
                for batch in pending
            ]
            states = (
                self._rebuild(connection)
                if any(batch.evidence for batch in pending)
                else self._states(connection)
            )
            receipts = []
            recorded_at = _now().isoformat()
            for batch, base_receipt in zip(pending, base_receipts, strict=True):
                receipt = {
                    **base_receipt,
                    "state_delta": self._state_delta(states, [batch]),
                }
                connection.execute(
                    "INSERT INTO evidence_batches VALUES (?,?,?,?)",
                    (
                        batch.idempotency_key,
                        payload_hashes[batch.idempotency_key],
                        recorded_at,
                        _json(receipt),
                    ),
                )
                receipts.append(receipt)
            return {
                "batch_receipts": receipts,
                "projection_version": PROJECTION_VERSION,
                "state_delta": self._state_delta(states, pending),
            }

    def record_evidence(self, batch: EvidenceBatch) -> dict:
        """Commit one activity batch while preserving the original single-write API."""

        batch = EvidenceBatch.model_validate_json(batch.model_dump_json())
        result = self.record_evidence_set(EvidenceBatchSet(batches=[batch]))
        return result["batch_receipts"][0]

    @staticmethod
    def _states(connection) -> list[dict]:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key='projection_version'"
        ).fetchone()
        if version and version[0] != PROJECTION_VERSION:
            raise ValueError("State projection is outdated; run rebuild-state")
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT data_json FROM concept_state ORDER BY concept_id,dimension"
            )
        ]

    def get_concept_state(
        self, concept_ids: list[str] | None = None, limit=20, offset=0
    ) -> list[dict]:
        _bounds(limit, offset)
        if concept_ids is not None and (
            not isinstance(concept_ids, list)
            or len(concept_ids) > 100
            or any(not isinstance(value, str) or not value for value in concept_ids)
        ):
            raise ValueError("concept_ids must contain at most 100 nonempty IDs")
        with self._connect() as connection:
            states = self._states(connection)
            if concept_ids is not None:
                states = [item for item in states if item["concept_id"] in concept_ids]
            return states[offset : offset + limit]

    def get_recent_history(self, limit=10, offset=0) -> list[dict]:
        _bounds(limit, offset)
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM interactions "
                    "ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def get_learning_evidence(
        self, concept_id: str | None = None, limit=20, offset=0
    ) -> list[dict]:
        _bounds(limit, offset)
        if concept_id is not None and (not isinstance(concept_id, str) or not concept_id):
            raise ValueError("concept_id must be nonempty")
        with self._connect() as connection:
            where = "WHERE e.concept_id=?" if concept_id is not None else ""
            params = ([concept_id] if concept_id is not None else []) + [limit, offset]
            return [
                {
                    **json.loads(row["payload_json"]),
                    "interaction_id": row["interaction_id"],
                    "lesson_id": row["lesson_id"],
                    "recorded_at": row["recorded_at"],
                    "observed_at": row["observed_at"],
                    "superseded": bool(row["superseded"]),
                }
                for row in connection.execute(
                    "SELECT e.*,b.recorded_at,i.observed_at,EXISTS "
                    "(SELECT 1 FROM learning_evidence successor WHERE successor.supersedes=e.id) "
                    "AS superseded FROM learning_evidence e "
                    "JOIN evidence_batches b ON b.id=e.batch_id "
                    "JOIN interactions i ON i.id=e.interaction_id "
                    f"{where} ORDER BY e.sequence DESC LIMIT ? OFFSET ?",
                    params,
                )
            ]

    @staticmethod
    def _candidates(states: list[dict], frontier: dict, now: datetime) -> list[dict]:
        result = []
        for state in states:
            if state["lesson_id"] not in frontier["allowed_lesson_ids"]:
                continue
            reasons = []
            priority = 0
            if state["recent_results"] and state["recent_results"][-1] in {
                "partial",
                "unsuccessful",
            }:
                reasons.append("recent_difficulty")
                priority += 40
            if state["recent_hint_dependency_count"]:
                reasons.append("hint_dependency")
                priority += 20
            if (
                state["status"] == "insufficient_evidence"
                or state["last_attempt_confidence"] == "insufficient"
            ):
                reasons.append("needs_confirmation")
                priority += 10
            reference_time = state["last_supported_at"] or state["last_observed_at"]
            age = max(0, (now - datetime.fromisoformat(reference_time)).days)
            if age >= 14:
                reasons.append("not_observed_recently")
                priority += 15
            if state["concept_id"] in frontier["focus_concept_ids"]:
                reasons.append("current_focus")
                priority += 10
            if reasons:
                result.append(
                    {
                        "concept_id": state["concept_id"],
                        "dimension": state["dimension"],
                        "priority": priority,
                        "reasons": reasons,
                        "days_since_observation": age,
                    }
                )
        return sorted(
            result, key=lambda item: (-item["priority"], item["concept_id"], item["dimension"])
        )

    def get_review_candidates(self, limit=10) -> list[dict]:
        _bounds(limit)
        with self._connect() as connection:
            return self._candidates(self._states(connection), self._frontier(connection), _now())[
                :limit
            ]

    def get_learner_context(self, max_chars: int = 12000) -> dict:
        """Bound JSON characters explicitly; never silently truncate the curriculum boundary."""
        if type(max_chars) is not int or not 1000 <= max_chars <= 64000:
            raise ValueError("max_chars must be 1000..64000")
        with self._connect() as connection:
            frontier = self._frontier(connection)
            states = self._states(connection)
            result = {
                "frontier": frontier,
                "projection_version": PROJECTION_VERSION,
                "review_candidates": [],
                "observed_abilities": [],
                "recent_activities": [],
                "omitted": {
                    "review_candidates": 0,
                    "observed_abilities": 0,
                    "recent_activities": 0,
                },
            }
            if len(_json(result)) > max_chars:
                raise ValueError("Budget cannot fit complete frontier; increase max_chars")
            candidates = self._candidates(states, frontier, _now())
            visible_states = [
                item for item in states if item["lesson_id"] in frontier["allowed_lesson_ids"]
            ]
            history = [
                {
                    "interaction_id": row["id"],
                    "activity_id": row["activity_id"],
                    "task_type": row["task_type"],
                    "observed_at": row["observed_at"],
                }
                for row in connection.execute(
                    "SELECT id,activity_id,task_type,observed_at FROM interactions "
                    "ORDER BY observed_at DESC,id DESC LIMIT 10"
                )
            ]
            profile = connection.execute(
                "SELECT data_json FROM learner_profile WHERE id=1"
            ).fetchone()
            if profile:
                result["profile"] = json.loads(profile[0])
                if len(_json(result)) > max_chars:
                    del result["profile"]
                    result["profile_omitted"] = True
            for key, values, cap in (
                ("review_candidates", candidates, 10),
                ("recent_activities", history, 10),
                ("observed_abilities", visible_states, 20),
            ):
                result["omitted"][key] = len(values)
                for value in values[:cap]:
                    result[key].append(value)
                    result["omitted"][key] -= 1
                    if len(_json(result)) > max_chars:
                        result[key].pop()
                        result["omitted"][key] += 1
                        break
            if len(_json(result)) > max_chars:
                raise ValueError("Character budget cannot fit context metadata; increase max_chars")
            return result
