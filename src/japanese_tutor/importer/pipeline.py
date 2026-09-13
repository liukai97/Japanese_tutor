"""Validated PDF import service used by the maintenance CLI."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pymupdf

from japanese_tutor.ids import document_id, object_id
from japanese_tutor.schemas.source import (
    BuildMetadata,
    LessonDocument,
    PageRef,
    Section,
    SourceRef,
    TextBlock,
)

from .cleanup import clean_pages
from .corrections import DEFAULT_CORRECTIONS_DIR, apply_corrections
from .layout import merge_paragraphs, reconstruct_table, recover_layout
from .manifest import identify_lesson, make_manifest
from .pdf_spans import extract_page
from .report import QualityReport, write_preview
from .ruby import reconstruct_text
from .sections import heading, text_role

EXTRACTOR_VERSION = "0.2.0"


def import_pdf(
    path: Path,
    *,
    series: str = "liangshuang",
    number: int | None = None,
    debug_dir: Path | None = None,
    corrections_dir: Path | None = DEFAULT_CORRECTIONS_DIR,
) -> tuple[LessonDocument, QualityReport]:
    number = identify_lesson(path, number)
    report = QualityReport(document_id(series, number))
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("Encrypted PDFs are not supported; supply an unlocked source")
        if not len(pdf):
            raise ValueError("PDF contains no pages")
        raw = [extract_page(page) for page in pdf]
        manifest = make_manifest(path, raw, series, number)
        cleaned = clean_pages(raw, report)
        sections: list[dict] = []
        current = None
        title_counts: dict[str, int] = {}
        debug_units = []
        for page, layout in zip(pdf, cleaned, strict=True):
            if not any(s.text.strip() for s in layout.spans):
                report.add(
                    "unsupported_layout",
                    layout.page,
                    "No usable text layer",
                    reason="OCR is not enabled",
                )
            units = merge_paragraphs(recover_layout(page, layout, report))
            vocabulary_page = any(
                (detected := heading(unit, "unknown")) and detected[1] == "vocabulary"
                for unit in units
            )
            if vocabulary_page and not any(unit.cells is not None for unit in units):
                report.add(
                    "unrecovered_tables",
                    layout.page,
                    "\n".join(unit.text for unit in units)[:240],
                    reason="Vocabulary heading found but no ruled table recovered",
                )
            if debug_dir:
                debug_units.append(dict(page=layout.page, units=[asdict(u) for u in units]))
            for unit in units:
                detected = heading(unit, current["section_type"] if current else "unknown")
                if current is None or detected:
                    title, kind, confidence = detected or ("未识别区块", "unknown", 0.0)
                    title_counts[title] = title_counts.get(title, 0) + 1
                    key = title if title_counts[title] == 1 else f"{title}-{title_counts[title]}"
                    current = dict(
                        section_id=object_id(manifest.lesson_id, kind, key),
                        section_type=kind,
                        title=title,
                        pages=[],
                        blocks=[],
                        confidence=confidence,
                        status="unresolved" if confidence < 0.85 else "reconstructed",
                    )
                    sections.append(current)
                    if confidence < 0.85:
                        report.add(
                            "low_confidence_sections",
                            layout.page,
                            title,
                            section_id=current["section_id"],
                            confidence=confidence,
                        )
                ref = PageRef(document_id=manifest.document_id, page=layout.page)
                if ref not in current["pages"]:
                    current["pages"].append(ref)
                source = SourceRef(
                    document_id=manifest.document_id,
                    page=layout.page,
                    section_id=current["section_id"],
                )
                if unit.cells is not None:
                    current["blocks"].append(reconstruct_table(unit, source, report, series=series))
                else:
                    content = reconstruct_text(unit.spans, source, report, separator="")
                    if unit.unresolved:
                        content = content.model_copy(update={"status": "unresolved"})
                    current["blocks"].append(
                        TextBlock(
                            role="heading"
                            if detected
                            else "unknown"
                            if unit.unresolved
                            else text_role(content.text),
                            content=content,
                        )
                    )
        document = LessonDocument(
            metadata=BuildMetadata(
                extractor_version=EXTRACTOR_VERSION,
                pymupdf_version=pymupdf.__version__,
                notation_convention="liangshuang:2026-09-13"
                if series == "liangshuang"
                else "unknown",
            ),
            manifest=manifest,
            sections=[Section(**section) for section in sections],
        )
        # Validate nested objects again after reconstruction, including any model_copy updates.
        document = LessonDocument.model_validate_json(document.model_dump_json())
        if corrections_dir is not None:
            document = apply_corrections(document, report, corrections_dir)
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            for name, payload in [
                ("spans", [asdict(p) for p in raw]),
                ("cleaned_layout", [asdict(p) for p in cleaned]),
                ("reconstructed_sections", debug_units),
            ]:
                (debug_dir / f"{name}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    return document, report


def write_import(
    document: LessonDocument,
    report: QualityReport,
    output_dir: Path,
    *,
    source_pdf: Path | None = None,
) -> None:
    if source_pdf is not None:
        with source_pdf.open("rb") as source:
            sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        if sha256 != document.manifest.document_sha256:
            raise ValueError("Review evidence PDF does not match the document source edition")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "lesson_document.json").write_text(
        document.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "build_metadata.json").write_text(
        document.metadata.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    report.write(output_dir / "quality_report.json")
    report.write_review_markdown(output_dir / "review_required.md", document)
    pages = set()
    if source_pdf is not None:
        pending_pages = {
            i["page"]
            for i in report.issues
            if i["severity"] == "review_required" and i.get("review_stage") == "llm_first"
        }
        if pending_pages:
            evidence_dir = output_dir / "review_evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            with pymupdf.open(source_pdf) as pdf:
                for page in sorted(pending_pages):
                    pdf[page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(
                        evidence_dir / f"page-{page}.png"
                    )
                    pages.add(page)
    report.write_review_markdown(
        output_dir / "model_review.md", document, model_review=True, evidence_pages=pages
    )
    write_preview(document, output_dir / "preview.md")
