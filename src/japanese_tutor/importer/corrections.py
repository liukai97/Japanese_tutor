"""Small, source-bound document corrections, applied through the import service."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from japanese_tutor.schemas.source import (
    Contract,
    JapaneseText,
    LessonDocument,
    RubyAnnotation,
    SourceRef,
    Table,
    TextBlock,
)

from .report import QualityReport

DEFAULT_CORRECTIONS_DIR = Path(__file__).resolve().parents[3] / "data" / "corrections"


class TextCorrection(Contract):
    page: int = Field(ge=1)
    expected_text: str = Field(min_length=1)
    replacement: str | None
    ruby: list[RubyAnnotation] = Field(default_factory=list)


class ReviewResolution(Contract):
    page: int = Field(ge=1)
    category: Literal["font_mapping", "unbound_ruby", "unsupported_layout", "unrecovered_tables"]
    expected_table_text: str | None = None
    intentional_empty_cells: list[tuple[int, int]] = Field(default_factory=list)


class TableCellCorrection(Contract):
    page: int = Field(ge=1)
    expected_table_text: str = Field(min_length=1)
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    expected_text: str | None
    replacement: str


class CorrectionOverlay(Contract):
    document_id: str
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    corrections: list[TextCorrection] = Field(min_length=1)
    table_cells: list[TableCellCorrection] = Field(default_factory=list)
    resolutions: list[ReviewResolution] = Field(default_factory=list)


def apply_corrections(
    document: LessonDocument, report: QualityReport, directory: Path
) -> LessonDocument:
    path = directory / f"{document.manifest.document_id}.json"
    if not path.is_file():
        return document
    overlay = CorrectionOverlay.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        overlay.document_id != document.manifest.document_id
        or overlay.document_sha256 != document.manifest.document_sha256
    ):
        raise ValueError("Correction overlay does not match the source PDF edition")
    updates = {s.section_id: list(s.blocks) for s in document.sections}
    for correction in overlay.corrections:
        matches = [
            (section_id, index, block)
            for section_id, blocks in updates.items()
            for index, block in enumerate(blocks)
            if isinstance(block, TextBlock)
            and block.content.text == correction.expected_text
            and any(ref.page == correction.page for ref in block.content.sources)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Correction requires exactly one matching text on page {correction.page}"
            )
        section_id, index, block = matches[0]
        if correction.replacement is None:
            if correction.ruby:
                raise ValueError("A removed text block cannot have ruby")
            updates[section_id].pop(index)
        else:
            source = SourceRef(
                document_id=overlay.document_id, page=correction.page, section_id=section_id
            )
            content = JapaneseText(
                text=correction.replacement, ruby=correction.ruby, sources=[source]
            )
            updates[section_id][index] = TextBlock(role="paragraph", content=content)
    for correction in overlay.table_cells:
        matches = [
            (section_id, index, block)
            for section_id, blocks in updates.items()
            for index, block in enumerate(blocks)
            if isinstance(block, Table)
            and any(ref.page == correction.page for ref in block.sources)
            and "|".join(cell.text if cell else "" for row in block.rows for cell in row)
            == correction.expected_table_text
        ]
        if len(matches) != 1:
            raise ValueError("Table correction requires exactly one matching table")
        section_id, index, block = matches[0]
        if correction.row >= len(block.rows) or correction.column >= len(block.columns):
            raise ValueError("Table correction cell is out of range")
        cell = block.rows[correction.row][correction.column]
        if (cell.text if cell else None) != correction.expected_text:
            raise ValueError("Table correction does not match expected cell")
        rows = [list(row) for row in block.rows]
        rows[correction.row][correction.column] = JapaneseText(
            text=correction.replacement,
            sources=[
                SourceRef(
                    document_id=overlay.document_id, page=correction.page, section_id=section_id
                )
            ],
        )
        updates[section_id][index] = block.model_copy(update={"rows": rows})
    for resolution in overlay.resolutions:
        if resolution.category != "unrecovered_tables":
            continue
        matched = 0
        for section_id, blocks in updates.items():
            for index, block in enumerate(blocks):
                if not isinstance(block, Table) or not any(
                    source.page == resolution.page for source in block.sources
                ):
                    continue
                table_text = "|".join(
                    cell.text if cell else "" for row in block.rows for cell in row
                )
                if (
                    resolution.expected_table_text is not None
                    and table_text != resolution.expected_table_text
                ):
                    continue
                matched += 1
                missing = {(r, c) for r, row in enumerate(block.rows)
                           for c, cell in enumerate(row) if cell is None}
                if missing != set(resolution.intentional_empty_cells):
                    raise ValueError("Reviewed table missing cells differ from the page evidence")
                updates[section_id][index] = block.model_copy(update={"status": "reconstructed"})
        if not matched or (resolution.expected_table_text is not None and matched != 1):
            raise ValueError("Table review requires a matching table on the page")
    removed_sections = {
        s.section_id
        for s in document.sections
        if s.section_type == "unknown" and s.blocks and not updates[s.section_id]
    }
    result = document.model_copy(
        update={
            "sections": [
                s.model_copy(update={"blocks": updates[s.section_id]})
                for s in document.sections
                if s.section_id not in removed_sections
            ]
        }
    )
    result = LessonDocument.model_validate_json(result.model_dump_json())
    resolved_issues = []
    for resolution in overlay.resolutions:
        matches = [
            i
            for i in report.issues
            if i["page"] == resolution.page
            and i["category"] == resolution.category
            and i["severity"] == "review_required"
        ]
        if not matches:
            raise ValueError("Correction resolution does not match a pending issue")
        if any(
            (
                isinstance(b, TextBlock)
                and b.content.status == "unresolved"
                and any(ref.page == resolution.page for ref in b.content.sources)
            )
            or (
                isinstance(b, Table)
                and b.status == "unresolved"
                and any(ref.page == resolution.page for ref in b.sources)
            )
            for s in result.sections
            for b in s.blocks
        ):
            raise ValueError("Cannot resolve a page with remaining unresolved text")
        resolved_issues.extend(matches)
    for issue in report.issues:
        if (
            issue["category"] == "low_confidence_sections"
            and issue.get("section_id") in removed_sections
        ):
            issue.update(
                severity="info",
                resolution="source_corrected_empty_section_removed",
                reviewer=overlay.reviewer,
                evidence=overlay.evidence,
            )
    for issue in resolved_issues:
        issue.update(
            severity="info",
            resolution="model_reviewed_and_corrected",
            reviewer=overlay.reviewer,
            evidence=overlay.evidence,
        )
    for correction in [*overlay.corrections, *overlay.table_cells]:
        report.add(
            "source_correction",
            correction.page,
            correction.expected_text or "",
            severity="info",
            replacement=correction.replacement,
            reviewer=overlay.reviewer,
            evidence=overlay.evidence,
        )
    return result
