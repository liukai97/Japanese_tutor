"""Versioned document contract. Layout coordinates deliberately stay in the importer.

Ruby ranges index Unicode code points, [start, end), not UTF-16 code units.
The draft version must be bumped when this contract changes incompatibly.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.2.0-draft"


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class PageRef(Contract):
    document_id: str = Field(min_length=1)
    page: int = Field(ge=1)


class SourceRef(PageRef):
    section_id: str = Field(min_length=1)


class Page(Contract):
    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class FontStat(Contract):
    name: str
    size: float = Field(gt=0)
    span_count: int = Field(ge=1)


class DocumentManifest(Contract):
    document_id: str = Field(min_length=1)
    lesson_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1)
    pages: list[Page]
    fonts: list[FontStat]

    @model_validator(mode="after")
    def check_pages(self) -> Self:
        if [p.page for p in self.pages] != list(range(1, self.page_count + 1)):
            raise ValueError("Manifest pages must be complete, ordered and one-based")
        return self


class RubyAnnotation(Contract):
    range: tuple[int, int]
    reading: str = Field(min_length=1)


class PitchAccent(Contract):
    raw_notation: str = Field(min_length=1)
    accent_numbers: list[Annotated[int, Field(ge=0, le=20)]] = Field(min_length=1)
    connection: Literal["single", "plus", "adjacent", "unknown"]
    status: Literal["observed", "unresolved"]
    interpretation: Literal["single", "component_accents", "alternatives", "unresolved"]


class UtteranceProsody(Contract):
    raw_notation: str = Field(min_length=1)
    contour: Literal["rising", "falling", "unknown"]
    function: Literal["question", "confirmation", "unknown"] = "unknown"
    status: Literal["observed", "unresolved"] = "observed"


class LexicalNotation(Contract):
    """An observation in a source cell, not a curriculum concept or runtime card."""

    surface: str = Field(min_length=1)
    reading: str | None = None
    reading_status: Literal["observed", "unknown", "unresolved"] = "unknown"
    reading_scope: Literal["whole_word", "partial", "unknown"] = "unknown"
    reading_range: tuple[int, int] | None = None
    loanword_original: str | None = None
    orthographic_variants: list[str] = Field(default_factory=list)
    pitch_accent: PitchAccent | None = None

    @model_validator(mode="after")
    def check_reading_range(self) -> Self:
        if self.reading_range is not None:
            start, end = self.reading_range
            if not self.reading or not 0 <= start < end <= len(self.surface):
                raise ValueError("Reading range must identify a segment of the surface")
        return self


class JapaneseText(Contract):
    text: str
    ruby: list[RubyAnnotation] = Field(default_factory=list)
    lexical_notations: list[LexicalNotation] = Field(default_factory=list)
    utterance_prosody: list[UtteranceProsody] = Field(default_factory=list)
    sources: list[SourceRef] = Field(min_length=1)
    status: Literal["reconstructed", "unresolved"] = "reconstructed"

    @model_validator(mode="after")
    def check_ruby(self) -> Self:
        previous_end = 0
        for annotation in self.ruby:
            start, end = annotation.range
            if not 0 <= start < end <= len(self.text) or start < previous_end:
                raise ValueError("Ruby ranges must be ordered, nonoverlapping and inside text")
            previous_end = end
        return self


class TextBlock(Contract):
    kind: Literal["text"] = "text"
    role: Literal["heading", "paragraph", "list_item", "unknown"] = "paragraph"
    content: JapaneseText


class Table(Contract):
    kind: Literal["table"] = "table"
    table_type: Literal["vocabulary", "conjugation", "unknown"] = "unknown"
    columns: list[str] = Field(min_length=1)
    rows: list[list[JapaneseText | None]] = Field(min_length=1)
    sources: list[SourceRef] = Field(min_length=1)
    status: Literal["reconstructed", "unresolved"] = "reconstructed"

    @model_validator(mode="after")
    def check_width(self) -> Self:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Table row width must match columns")
        return self


Block = Annotated[TextBlock | Table, Field(discriminator="kind")]
SectionKind = Literal[
    "lesson_goal",
    "dialogue",
    "vocabulary",
    "grammar",
    "pragmatics",
    "listening_exercise",
    "controlled_exercise",
    "speaking_exercise",
    "unknown",
]


class Section(Contract):
    section_id: str
    section_type: SectionKind
    title: str
    pages: list[PageRef] = Field(min_length=1)
    blocks: list[Block]
    confidence: float = Field(ge=0, le=1)
    status: Literal["reconstructed", "unresolved"] = "reconstructed"


class BuildMetadata(Contract):
    schema_version: Literal["0.2.0-draft"] = SCHEMA_VERSION
    extractor_version: str
    pymupdf_version: str
    notation_convention: str = "unknown"


class LessonDocument(Contract):
    metadata: BuildMetadata
    manifest: DocumentManifest
    sections: list[Section]

    @model_validator(mode="after")
    def check_sources(self) -> Self:
        section_ids = [s.section_id for s in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Duplicate section IDs")
        for section in self.sections:
            pages = [p.page for p in section.pages]
            if pages != sorted(set(pages)):
                raise ValueError("Section pages must be ordered and unique")
            for page in section.pages:
                if (
                    page.document_id != self.manifest.document_id
                    or page.page > self.manifest.page_count
                ):
                    raise ValueError("Invalid section page reference")
            texts = []
            refs = []
            for block in section.blocks:
                if isinstance(block, TextBlock):
                    texts.append(block.content)
                else:
                    refs.extend(block.sources)
                    texts.extend(cell for row in block.rows for cell in row if cell is not None)
            refs.extend(ref for text in texts for ref in text.sources)
            for ref in refs:
                if (
                    ref.document_id != self.manifest.document_id
                    or ref.section_id != section.section_id
                    or ref.page not in pages
                ):
                    raise ValueError("Invalid document/page/section source reference")
        return self
