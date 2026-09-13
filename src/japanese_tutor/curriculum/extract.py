"""Source preparation and validated submission. No model invocation or SQL."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from japanese_tutor.ids import object_id
from japanese_tutor.schemas.curriculum import (
    Book,
    Concept,
    CurriculumConvention,
    Dialogue,
    Example,
    Lesson,
    LexicalData,
    LexicalVariant,
    SectionRecord,
    SemanticCurriculum,
    SemanticMetadata,
    Support,
    TopicData,
)
from japanese_tutor.schemas.source import JapaneseText, LessonDocument, SourceRef, Table, TextBlock


def artifact_hash(model) -> str:
    raw = json.dumps(
        model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_json(path: Path, value) -> None:
    """Replace one artifact atomically after validation, never truncate an old result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_document(path: Path) -> LessonDocument:
    return LessonDocument.model_validate_json(path.read_text(encoding="utf-8"))


def section_texts(section) -> list[JapaneseText]:
    texts = []
    for block in section.blocks:
        if isinstance(block, TextBlock):
            texts.append(block.content)
        else:
            texts.extend(cell for row in block.rows for cell in row if cell is not None)
    return texts


def source_view(document: LessonDocument, section_id: str | None = None, page: int | None = None):
    sections = [s for s in document.sections if section_id is None or s.section_id == section_id]
    if not sections or (page is not None and not 1 <= page <= document.manifest.page_count):
        raise ValueError("Unknown section or page")
    convention = load_curriculum_convention(document)
    return {
        "source_artifact_sha256": artifact_hash(document),
        "manifest": document.manifest.model_dump(mode="json"),
        "notation_convention": document.metadata.notation_convention,
        "curriculum_convention": (convention.model_dump(mode="json") if convention else None),
        "sections": [
            s.model_dump(mode="json")
            for s in sections
            if page is None or page in {p.page for p in s.pages}
        ],
    }


def validate_ref(document: LessonDocument, ref: SourceRef) -> None:
    sections = {s.section_id: s for s in document.sections}
    section = sections.get(ref.section_id)
    if (
        ref.document_id != document.manifest.document_id
        or section is None
        or ref.page not in {p.page for p in section.pages}
    ):
        raise ValueError("Source outside the supplied document/page/section scope")


def validate_support(document: LessonDocument, support: Support) -> None:
    validate_ref(document, support.source)
    section = next(s for s in document.sections if s.section_id == support.source.section_id)
    texts = [t.text for t in section_texts(section) if support.source in t.sources]
    # Literal source snippets: no normalization that might hide a changed Japanese form.
    if not any(support.quote in text for text in texts):
        raise ValueError("Support quote is absent from the cited source text")


def validate_nested_sources(document: LessonDocument, value) -> None:
    if isinstance(value, dict):
        if {"document_id", "page", "section_id"} <= value.keys():
            validate_ref(document, SourceRef.model_validate(value))
        for nested in value.values():
            validate_nested_sources(document, nested)
    elif isinstance(value, list):
        for nested in value:
            validate_nested_sources(document, nested)


def deterministic_curriculum(document: LessonDocument) -> SemanticCurriculum:
    """Only unambiguous vocabulary rows; never guess a multi-entry field alignment."""
    lesson = document.manifest.lesson_id
    objects = {}
    records = []
    for section in document.sections:
        identifiers = []
        for table in section.blocks:
            if not isinstance(table, Table) or table.table_type != "vocabulary":
                continue
            required = {"word", "part_of_speech", "meaning", "jlpt", "number"}
            if not required <= set(table.columns) or table.status == "unresolved":
                continue
            for row in table.rows:
                cells = dict(zip(table.columns, row, strict=True))
                word = cells["word"]
                if word is None or len(word.lexical_notations) != 1:
                    continue
                # Wrapped meaning text is retained; multiple word observations are deferred.
                notation = word.lexical_notations[0]
                support = Support(
                    source=word.sources[0],
                    quote=word.text,
                    reason="教材词汇表原始条目；列字段直接复制，未补充词典信息。",
                )
                fields = {
                    name: cell.text if cell and cell.text else None for name, cell in cells.items()
                }

                data = LexicalData(
                    notation=notation,
                    source_text=word.model_copy(update={"lexical_notations": []}),
                    part_of_speech=fields["part_of_speech"],
                    meaning=fields["meaning"],
                    jlpt=fields["jlpt"],
                    number=fields["number"],
                )
                key = notation.surface + "|" + (notation.reading or "")
                identifier = object_id(lesson, "lexical", key)
                unresolved = (
                    section.status == "unresolved"
                    or word.status == "unresolved"
                    or notation.reading_status == "unresolved"
                    or (notation.pitch_accent and notation.pitch_accent.status == "unresolved")
                    or any(c and c.status == "unresolved" for c in row)
                )
                concept = Concept(
                    id=identifier,
                    lesson_id=lesson,
                    provenance="asserted",
                    supports=[support],
                    confidence=section.confidence,
                    status="unresolved" if unresolved else "extracted",
                    title=notation.surface,
                    category="lexical",
                    subtype="proper_name" if data.part_of_speech == "［专］" else "vocabulary",
                    summary=data.meaning or notation.surface,
                    data=data,
                )
                if identifier in objects:
                    previous = objects[identifier]
                    if previous.data.notation != concept.data.notation:
                        continue  # Conflicting observations require Codex source review.
                    concept = merge_lexical_concepts(previous, concept)
                objects[identifier] = concept
                if identifier not in identifiers:
                    identifiers.append(identifier)
        records.append(
            SectionRecord(
                section_id=section.section_id,
                disposition="unresolved",
                object_ids=identifiers,
                reason="确定性候选已准备；Codex 尚须完成该 section 的语义抽取与覆盖检查。",
            )
        )
    return SemanticCurriculum(
        metadata=SemanticMetadata(
            document_sha256=document.manifest.document_sha256,
            source_artifact_sha256=artifact_hash(document),
            notation_convention=document.metadata.notation_convention,
            curriculum_convention=load_curriculum_convention(document),
            extractor="python-deterministic-candidates",
            instruction_version="stage2-v3",
        ),
        book=Book(id=lesson.split(":")[0], title=document.manifest.filename),
        lesson=Lesson(
            id=lesson,
            document_id=document.manifest.document_id,
            section_ids=[s.section_id for s in document.sections],
        ),
        objects=list(objects.values()),
        sections=records,
    )


def validate_curriculum(document: LessonDocument, curriculum: SemanticCurriculum) -> None:
    manifest = document.manifest
    if (
        curriculum.metadata.document_sha256 != manifest.document_sha256
        or curriculum.metadata.source_artifact_sha256 != artifact_hash(document)
        or curriculum.metadata.notation_convention != document.metadata.notation_convention
        or curriculum.metadata.curriculum_convention != load_curriculum_convention(document)
        or curriculum.lesson.id != manifest.lesson_id
        or curriculum.lesson.document_id != manifest.document_id
    ):
        raise ValueError("Curriculum is bound to a different source artifact or edition")
    expected = {s.section_id for s in document.sections}
    if (
        set(curriculum.lesson.section_ids) != expected
        or len(curriculum.lesson.section_ids) != len(expected)
        or {s.section_id for s in curriculum.sections} != expected
    ):
        raise ValueError("Every input section requires exactly one processing record")
    objects = {o.id: o for o in curriculum.objects}
    covered = set()
    for record in curriculum.sections:
        if record.disposition == "no_semantic_content" and record.object_ids:
            raise ValueError("No-content section cannot list semantic objects")
        for identifier in record.object_ids:
            obj = objects.get(identifier)
            if obj is None or record.section_id not in {s.source.section_id for s in obj.supports}:
                raise ValueError("Section record references an absent or unaligned object")
            covered.add(identifier)
    if covered != objects.keys():
        raise ValueError("Every object must appear in its source section processing record")
    topic_sections = {
        s.section_id for s in document.sections if s.section_type in {"grammar", "pragmatics"}
    }
    for obj in curriculum.objects:
        in_topic = any(s.source.section_id in topic_sections for s in obj.supports)
        if in_topic and isinstance(obj, (Example, Dialogue)):
            raise ValueError("Grammar/expression examples must be embedded in topics")
        if (
            in_topic
            and isinstance(obj, Concept)
            and obj.category != "lexical"
            and not isinstance(obj.data, TopicData)
        ):
            raise ValueError("Grammar/expression content must use topic concepts")
    for obj in curriculum.objects:
        for support in obj.supports:
            validate_support(document, support)
        validate_nested_sources(document, obj.model_dump(mode="json"))


def prepare_extraction(document: LessonDocument, output: Path) -> None:
    baseline = deterministic_curriculum(document)
    validate_curriculum(document, baseline)
    write_json(output / "candidate_curriculum.json", baseline)
    write_json(output / "extraction_schema.json", SemanticCurriculum.model_json_schema())
    write_json(output / "source.json", source_view(document))
    write_json(
        output / "extraction_tasks.json",
        {
            "source_artifact_sha256": artifact_hash(document),
            "instruction_version": "stage2-v3",
            "status": "awaiting_codex_extraction",
            "sections": [
                {
                    "section_id": s.section_id,
                    "section_type": s.section_type,
                    "candidate_ids": r.object_ids,
                    "source_status": s.status,
                }
                for s, r in zip(document.sections, baseline.sections, strict=True)
            ],
        },
    )


def accept_extraction(document: LessonDocument, submission: Path, output: Path):
    result = SemanticCurriculum.model_validate_json(submission.read_text(encoding="utf-8"))
    validate_curriculum(document, result)
    write_json(output / "semantic_curriculum.json", result)
    return result


def slice_japanese_text(text: JapaneseText, start: int, end: int) -> JapaneseText:
    """Copy a source substring and shift complete ruby ranges without guessing."""
    if not 0 <= start < end <= len(text.text):
        raise ValueError("Invalid Japanese text slice")
    ruby = []
    for annotation in text.ruby:
        a, b = annotation.range
        if a < end and b > start:
            if a < start or b > end:
                raise ValueError("Cannot split a ruby base across substring boundaries")
            ruby.append(annotation.model_copy(update={"range": (a - start, b - start)}))
    selected = text.text[start:end]
    return JapaneseText(
        text=selected,
        ruby=ruby,
        sources=text.sources,
        status=text.status,
        lexical_notations=[n for n in text.lexical_notations if n.surface in selected],
        utterance_prosody=[p for p in text.utterance_prosody if p.raw_notation in selected],
    )


DEFAULT_CORRECTIONS = Path(__file__).resolve().parents[3] / "data" / "corrections"


def load_curriculum_convention(
    document: LessonDocument,
    directory: Path = DEFAULT_CORRECTIONS,
) -> CurriculumConvention | None:
    series = document.manifest.lesson_id.split(":")[0]
    path = directory / f"{series}-curriculum.json"
    if not path.exists():
        return None
    convention = CurriculumConvention.model_validate_json(path.read_text(encoding="utf-8"))
    if convention.series != series:
        raise ValueError("Curriculum convention belongs to another textbook series")
    return convention


def resolve_group_jlpt(
    labels: str | None,
    component_count: int,
    convention: CurriculumConvention | None,
) -> list[str | None] | None:
    """Use confirmed group-label semantics; unresolved attribution returns None."""
    if component_count < 1:
        raise ValueError("Expected at least one component")
    levels = [line.strip() for line in (labels or "").splitlines() if line.strip()]
    if not levels:
        return [None] * component_count
    if any(level not in {"N1", "N2", "N3", "N4", "N5"} for level in levels):
        return None
    if convention and len(set(levels)) == 1:
        return [levels[0]] * component_count
    if len(levels) == component_count:
        return levels
    return None


def merge_lexical_concepts(primary: Concept, other: Concept) -> Concept:
    """Merge source variants after Codex confirms they describe the same lexeme."""
    if (
        primary.category != "lexical"
        or other.category != "lexical"
        or primary.lesson_id != other.lesson_id
        or primary.data.notation != other.data.notation
    ):
        raise ValueError("Only matching lexical observations can be merged")
    supports = list(primary.supports)
    for support in other.supports:
        if support not in supports:
            supports.append(support)
    canonical = primary.data.model_copy(update={"jlpt": primary.data.jlpt or other.data.jlpt})
    fields = (canonical.meaning, canonical.part_of_speech, canonical.jlpt)
    variants = list(primary.data.variants)
    for obj in [primary, other]:
        data = obj.data
        values = (data.meaning, data.part_of_speech, data.jlpt)
        if values != fields:
            refs = list(
                dict.fromkeys(
                    (s.source.document_id, s.source.page, s.source.section_id) for s in obj.supports
                )
            )
            sources = [SourceRef(document_id=d, page=p, section_id=s) for d, p, s in refs]
            variant = LexicalVariant(
                meaning=data.meaning,
                part_of_speech=data.part_of_speech,
                jlpt=data.jlpt,
                sources=sources,
            )
            if variant not in variants:
                variants.append(variant)
        for variant in data.variants:
            if variant not in variants:
                variants.append(variant)
    canonical = canonical.model_copy(update={"variants": variants})
    result = primary.model_copy(
        update={
            "supports": supports,
            "data": canonical,
            "status": "unresolved"
            if "unresolved" in {primary.status, other.status}
            else "extracted",
            "confidence": min(primary.confidence, other.confidence),
        }
    )
    return Concept.model_validate_json(result.model_dump_json())
