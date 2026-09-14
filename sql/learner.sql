-- Independent learner data. Only evidence/frontier history is append-only.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('learner_database_version', '1');
CREATE TABLE IF NOT EXISTS learner_profile (
    id INTEGER PRIMARY KEY CHECK(id=1), data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS curriculum_frontier (
    sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY, activity_id TEXT NOT NULL, session_id TEXT,
    observed_at TEXT NOT NULL, task_type TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS interaction_history ON interactions(observed_at, id);
-- Receipts permit a later correction of the same interaction without rewriting it.
CREATE TABLE IF NOT EXISTS evidence_batches (
    id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_evidence (
    sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL REFERENCES evidence_batches(id),
    interaction_id TEXT NOT NULL REFERENCES interactions(id),
    concept_id TEXT NOT NULL, lesson_id TEXT NOT NULL, dimension TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('observation', 'retraction')),
    supersedes TEXT UNIQUE REFERENCES learning_evidence(id), payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS first_observation
    ON learning_evidence(interaction_id, concept_id, dimension)
    WHERE supersedes IS NULL;
CREATE INDEX IF NOT EXISTS evidence_scope ON learning_evidence(concept_id, dimension);
CREATE TABLE IF NOT EXISTS concept_state (
    concept_id TEXT NOT NULL, dimension TEXT NOT NULL, data_json TEXT NOT NULL,
    PRIMARY KEY(concept_id, dimension)
);
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON learning_evidence
BEGIN SELECT RAISE(ABORT, 'Learning evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON learning_evidence
BEGIN SELECT RAISE(ABORT, 'Learning evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS interaction_no_update BEFORE UPDATE ON interactions
BEGIN SELECT RAISE(ABORT, 'Interactions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS interaction_no_delete BEFORE DELETE ON interactions
BEGIN SELECT RAISE(ABORT, 'Interactions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS frontier_no_update BEFORE UPDATE ON curriculum_frontier
BEGIN SELECT RAISE(ABORT, 'Frontier records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS frontier_no_delete BEFORE DELETE ON curriculum_frontier
BEGIN SELECT RAISE(ABORT, 'Frontier records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS batch_no_update BEFORE UPDATE ON evidence_batches
BEGIN SELECT RAISE(ABORT, 'Evidence receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS batch_no_delete BEFORE DELETE ON evidence_batches
BEGIN SELECT RAISE(ABORT, 'Evidence receipts are append-only'); END;
COMMIT;
