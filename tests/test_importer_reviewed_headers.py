"""Reviewed header removal and fullwidth foreign originals stay lossless."""

import json
from pathlib import Path

from test_importer import make_pdf

from japanese_tutor.importer.corrections import apply_corrections
from japanese_tutor.importer.pipeline import import_pdf
from japanese_tutor.importer.pitch import lexical_notations
from japanese_tutor.importer.report import QualityReport
from japanese_tutor.schemas.source import LessonDocument


def test_fullwidth_foreign_original() -> None:
    word = lexical_notations("オッケー（ＯＫ）①")[0]
    assert word.loanword_original == "ＯＫ"
    assert word.reading is None
    assert word.pitch_accent.raw_notation == "①"
    assert lexical_notations("オッケー（ＯＫ）①", series="other")[0].reading_status == "unresolved"


def test_only_reviewed_empty_unknown_section_is_removed(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    document, _ = import_pdf(pdf, series="sample", number=10)
    payload = json.loads(document.model_dump_json())
    section = payload["sections"][0]
    original_id = section["section_id"]
    section.update(section_type="unknown", status="unresolved", confidence=0.0)
    section["blocks"] = [section["blocks"][0]]
    literal = section["blocks"][0]["content"]["text"]
    document = LessonDocument.model_validate_json(json.dumps(payload))
    overlay = dict(
        document_id=document.manifest.document_id,
        document_sha256=document.manifest.document_sha256,
        reviewer="synthetic reviewer",
        evidence="synthetic header page evidence",
        corrections=[dict(page=1, expected_text=literal, replacement=None)],
    )
    (tmp_path / f"{document.manifest.document_id}.json").write_text(
        json.dumps(overlay), encoding="utf-8"
    )
    report = QualityReport(document.manifest.document_id)
    report.add("low_confidence_sections", 1, literal, section_id=original_id)
    corrected = apply_corrections(document, report, tmp_path)
    assert original_id not in {s.section_id for s in corrected.sections}
    assert report.issues[0]["severity"] == "info"
    assert document.sections[0].blocks
    # A known section remains a meaningful structural boundary when its text is removed.
    payload["sections"][0]["section_type"] = "lesson_goal"
    known = LessonDocument.model_validate_json(json.dumps(payload))
    retained = apply_corrections(known, QualityReport(document.manifest.document_id), tmp_path)
    assert retained.sections[0].section_id == original_id
    assert not retained.sections[0].blocks
