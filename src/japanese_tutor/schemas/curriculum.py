"""Draft semantic contracts; source existence is not semantic approval."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from japanese_tutor.schemas.source import (
    Contract,
    JapaneseText,
    LexicalNotation,
    SourceRef,
)

SEMANTIC_VERSION = "0.3.0-draft"
TAXONOMY = {
    "lexical": {"vocabulary", "proper_name", "expression", "collocation"},
    "grammar": {"topic"},
    "pragmatics": {"topic"},
    "phonology": {"kana_reading", "sentence_intonation"},
    "communicative_skill": {
        "self_introduction",
        "introduce_other",
        "greeting",
        "ask_identity",
        "confirm_information",
    },
}


class Support(Contract):
    source: SourceRef
    quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SemanticObject(Contract):
    id: str = Field(min_length=1)
    lesson_id: str = Field(min_length=1)
    provenance: Literal["asserted", "derived", "proposed"]
    supports: list[Support] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: Literal["extracted", "unresolved"] = "extracted"


class LexicalVariant(Contract):
    """Source-specific gloss/labels for the same lexeme, not additional concepts."""

    meaning: str | None = None
    part_of_speech: str | None = None
    jlpt: str | None = None
    sources: list[SourceRef] = Field(min_length=1)


class CurriculumConvention(Contract):
    series: str = Field(min_length=1)
    version: str = Field(min_length=1)
    shared_group_jlpt: Literal[True]
    reviewer: str = Field(min_length=1)
    reviewed_on: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class LexicalData(Contract):
    kind: Literal["lexical"] = "lexical"
    notation: LexicalNotation
    source_text: JapaneseText
    part_of_speech: str | None = None
    meaning: str | None = None
    jlpt: str | None = None
    number: str | None = None
    variants: list[LexicalVariant] = Field(default_factory=list)


class ClaimData(Contract):
    kind: Literal["claim"] = "claim"
    statement: str = Field(min_length=1)
    forms: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


SourceIndices = Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1)]


class TopicNote(Contract):
    text: str = Field(min_length=1)
    source_indices: SourceIndices


class TopicTurn(Contract):
    japanese: str = Field(min_length=1)
    translation: str | None = None
    speaker: str | None = None


class TopicExample(Contract):
    turns: list[TopicTurn] = Field(min_length=1)
    usage: Literal["illustrative", "preferred", "dispreferred"] = "illustrative"
    source_indices: SourceIndices


class TopicTable(Contract):
    title: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1)
    rows: list[list[str | None]] = Field(min_length=1)
    source_indices: SourceIndices

    @model_validator(mode="after")
    def check_rows(self) -> Self:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Topic table rows must match columns")
        return self


class TopicData(Contract):
    """One teaching topic with local content, no global example/claim identities."""

    kind: Literal["topic"] = "topic"
    forms: list[str] = Field(default_factory=list)
    notes: list[TopicNote] = Field(min_length=1)
    examples: list[TopicExample] = Field(default_factory=list)
    tables: list[TopicTable] = Field(default_factory=list)


class Concept(SemanticObject):
    kind: Literal["concept"] = "concept"
    title: str = Field(min_length=1)
    category: Literal["lexical", "grammar", "pragmatics", "phonology", "communicative_skill"]
    subtype: str
    summary: str = Field(min_length=1)
    data: Annotated[LexicalData | ClaimData | TopicData, Field(discriminator="kind")]

    @model_validator(mode="after")
    def check_taxonomy(self) -> Self:
        if self.subtype not in TAXONOMY[self.category]:
            raise ValueError("Unsupported concept taxonomy")
        if self.category == "lexical" and not isinstance(self.data, LexicalData):
            raise ValueError("Lexical concepts require lossless lexical data")
        if self.category != "lexical" and isinstance(self.data, LexicalData):
            raise ValueError(
                "Reading and lexical pitch belong to lexical fields, not copied concepts"
            )
        if isinstance(self.data, LexicalData) and self.data.source_text.lexical_notations:
            raise ValueError("Lexical notation is stored once, outside source_text")
        if self.category in {"grammar", "pragmatics"} and not isinstance(self.data, TopicData):
            raise ValueError("Grammar and expression concepts require topic data")
        if isinstance(self.data, TopicData):
            if self.category not in {"grammar", "pragmatics"} or self.subtype != "topic":
                raise ValueError("Topic data belongs to grammar/expression topics")
            for item in [*self.data.notes, *self.data.examples, *self.data.tables]:
                if len(item.source_indices) != len(set(item.source_indices)) or any(
                    index >= len(self.supports) for index in item.source_indices
                ):
                    raise ValueError("Invalid local topic source index")
        return self


class Example(SemanticObject):
    kind: Literal["example"] = "example"
    text: JapaneseText
    translation: str | None = None
    usage: Literal["illustrative", "preferred", "dispreferred"] = "illustrative"


class Utterance(Contract):
    speaker: str = Field(min_length=1)
    text: JapaneseText
    stage_direction: str | None = None


class Dialogue(SemanticObject):
    kind: Literal["dialogue"] = "dialogue"
    title: str = Field(min_length=1)
    setting: str | None = None
    utterances: list[Utterance] = Field(min_length=1)


class ExerciseExample(SemanticObject):
    """Source training examples, never reusable runtime cards."""

    kind: Literal["exercise_example"] = "exercise_example"
    title: str = Field(min_length=1)
    source_text: list[JapaneseText] = Field(min_length=1)
    target_concept_ids: list[str] = Field(min_length=1)
    task_type: Literal["listening", "controlled", "speaking", "integrated", "unknown"]
    progression: Literal["recognition", "recall", "controlled", "free", "integrated", "unknown"]


class ConceptRelation(SemanticObject):
    kind: Literal["relation"] = "relation"
    source_id: str
    target_id: str
    relation_type: Literal[
        "prerequisite_of",
        "contrasts_with",
        "used_in",
        "exemplified_by",
        "co_practiced_with",
        "introduced_in",
        "reviewed_in",
    ]


Object = Annotated[
    Concept | Example | Dialogue | ExerciseExample | ConceptRelation,
    Field(discriminator="kind"),
]


class Book(Contract):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class Lesson(Contract):
    id: str = Field(min_length=1)
    document_id: str
    section_ids: list[str] = Field(min_length=1)


class SemanticMetadata(Contract):
    schema_version: Literal["0.3.0-draft"] = SEMANTIC_VERSION
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notation_convention: str
    curriculum_convention: CurriculumConvention | None = None
    extractor: str = Field(min_length=1)
    instruction_version: str = Field(min_length=1)


class SectionRecord(Contract):
    section_id: str
    disposition: Literal["extracted", "no_semantic_content", "unresolved"]
    object_ids: list[str]
    reason: str = Field(min_length=1)


class SemanticCurriculum(Contract):
    metadata: SemanticMetadata
    book: Book
    lesson: Lesson
    objects: list[Object]
    sections: list[SectionRecord]

    @model_validator(mode="after")
    def check_graph(self) -> Self:
        objects = {obj.id: obj for obj in self.objects}
        if len(objects) != len(self.objects):
            raise ValueError("Duplicate semantic object IDs")
        if len({s.section_id for s in self.sections}) != len(self.sections):
            raise ValueError("Duplicate section records")
        edges = set()
        lexemes = set()
        for obj in self.objects:
            if obj.lesson_id != self.lesson.id or not obj.id.startswith(self.lesson.id + ":"):
                raise ValueError("Object identity must belong to this lesson")
            if isinstance(obj, Concept) and obj.category == "lexical":
                key = (obj.data.notation.surface, obj.data.notation.reading)
                if key in lexemes:
                    raise ValueError("Duplicate lexical identity; merge sources and variants")
                lexemes.add(key)
            if isinstance(obj, ExerciseExample):
                if len(obj.target_concept_ids) != len(set(obj.target_concept_ids)):
                    raise ValueError("Duplicate exercise targets")
                if any(not isinstance(objects.get(i), Concept) for i in obj.target_concept_ids):
                    raise ValueError("Exercise targets must reference concepts")
            if not isinstance(obj, ConceptRelation):
                continue
            edge = (obj.source_id, obj.target_id, obj.relation_type)
            if edge in edges:
                raise ValueError("Duplicate graph edge")
            edges.add(edge)
            if not isinstance(objects.get(obj.source_id), Concept):
                raise ValueError("Relation source must reference a concept")
            target = objects.get(obj.target_id)
            if obj.relation_type in {"introduced_in", "reviewed_in"}:
                valid = obj.target_id == self.lesson.id
            elif obj.relation_type == "exemplified_by":
                valid = isinstance(target, (Example, Dialogue, ExerciseExample))
            else:
                valid = isinstance(target, Concept) and obj.source_id != obj.target_id
            if not valid:
                raise ValueError("Invalid relation target")
        return self
