-- Rebuildable curriculum only. Learner evidence lives in a separate database.
PRAGMA foreign_keys = ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
    id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE lessons (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL UNIQUE REFERENCES documents(id),
    book_id TEXT NOT NULL, book_title TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE sections (
    id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id),
    position INTEGER NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
    section_json TEXT NOT NULL, UNIQUE(lesson_id, position)
);
CREATE TABLE section_pages (
    section_id TEXT NOT NULL REFERENCES sections(id),
    document_id TEXT NOT NULL REFERENCES documents(id), page INTEGER NOT NULL CHECK(page > 0),
    PRIMARY KEY(section_id, document_id, page)
);
CREATE TABLE objects (
    id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id), kind TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK(provenance IN ('asserted', 'derived')),
    confidence REAL NOT NULL, status TEXT NOT NULL, supports_json TEXT NOT NULL
);
CREATE TABLE concepts (
    id TEXT PRIMARY KEY REFERENCES objects(id), lesson_id TEXT NOT NULL REFERENCES lessons(id),
    category TEXT NOT NULL, subtype TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
    surface TEXT, reading TEXT, meaning TEXT, data_json TEXT NOT NULL
);
CREATE INDEX concept_filters ON concepts(lesson_id, category);
CREATE TABLE concept_sources (
    concept_id TEXT NOT NULL REFERENCES concepts(id), document_id TEXT NOT NULL,
    page INTEGER NOT NULL, section_id TEXT NOT NULL,
    PRIMARY KEY(concept_id, document_id, page, section_id),
    FOREIGN KEY(section_id, document_id, page)
        REFERENCES section_pages(section_id, document_id, page)
);
CREATE TABLE dialogues (
    id TEXT PRIMARY KEY REFERENCES objects(id), title TEXT NOT NULL, setting TEXT
);
CREATE TABLE utterances (
    dialogue_id TEXT NOT NULL REFERENCES dialogues(id), position INTEGER NOT NULL,
    speaker TEXT NOT NULL, text_json TEXT NOT NULL, stage_direction TEXT,
    PRIMARY KEY(dialogue_id, position)
);
CREATE TABLE exercise_examples (
    id TEXT PRIMARY KEY REFERENCES objects(id), title TEXT NOT NULL,
    task_type TEXT NOT NULL, progression TEXT NOT NULL, source_text_json TEXT NOT NULL
);
CREATE TABLE exercise_targets (
    exercise_id TEXT NOT NULL REFERENCES exercise_examples(id),
    concept_id TEXT NOT NULL REFERENCES concepts(id), position INTEGER NOT NULL,
    PRIMARY KEY(exercise_id, concept_id), UNIQUE(exercise_id, position)
);
CREATE TABLE concept_relations (
    id TEXT PRIMARY KEY REFERENCES objects(id), source_id TEXT NOT NULL REFERENCES concepts(id),
    target_id TEXT NOT NULL, relation_type TEXT NOT NULL,
    UNIQUE(source_id, target_id, relation_type)
);
CREATE INDEX relation_targets ON concept_relations(target_id, relation_type);
-- Relation targets can be another semantic object or the lesson itself.
CREATE TRIGGER relation_target_exists BEFORE INSERT ON concept_relations
WHEN NOT EXISTS (SELECT 1 FROM objects WHERE id=NEW.target_id)
 AND NOT EXISTS (SELECT 1 FROM lessons WHERE id=NEW.target_id)
BEGIN SELECT RAISE(ABORT, 'Unknown curriculum relation target'); END;
CREATE TABLE search_entries (
    id INTEGER PRIMARY KEY, object_id TEXT NOT NULL, kind TEXT NOT NULL,
    lesson_id TEXT NOT NULL REFERENCES lessons(id), concept_type TEXT,
    title TEXT NOT NULL, body TEXT NOT NULL, source_quote TEXT NOT NULL,
    document_id TEXT NOT NULL, page INTEGER NOT NULL, section_id TEXT NOT NULL,
    FOREIGN KEY(section_id, document_id, page)
        REFERENCES section_pages(section_id, document_id, page)
);
CREATE INDEX search_filters ON search_entries(lesson_id, concept_type, page);
-- Trigrams allow substring retrieval in Japanese/Chinese without word segmentation.
CREATE VIRTUAL TABLE textbook_fts USING fts5(title, body, tokenize='trigram');
