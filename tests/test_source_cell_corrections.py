"""Source-bound cell repairs cannot silently apply to changed tables."""

import copy
import json
from pathlib import Path

import pytest
from test_importer import make_pdf

from japanese_tutor.importer.corrections import apply_corrections
from japanese_tutor.importer.pipeline import import_pdf
from japanese_tutor.importer.pitch import lexical_notations
from japanese_tutor.importer.report import QualityReport
from japanese_tutor.schemas.source import LessonDocument


def test_repeated_latin_prefix_preserves_partial_reading() -> None:
    for source, expected_range in [("A型（Aがた）④", (1, 2)), ("AB 型（AB がた）④", (3, 4))]:
        word = lexical_notations(source)[0]
        assert word.reading == "がた"
        assert word.reading_scope == "partial"
        assert word.reading_range == expected_range
        assert word.reading_status == "observed"
        assert word.loanword_original is None
        assert word.pitch_accent.raw_notation == "④"
    for source, series in [("A型（Bがた）④", "liangshuang"), ("A型（Aがた）④", "other")]:
        assert lexical_notations(source, series=series)[0].reading_status == "unresolved"


def test_bracketed_kanji_spelling_is_not_a_reading() -> None:
    for source, spelling in [("すぐ（【直】ぐ）①", "直ぐ"), ("ところ（【所】）③", "所")]:
        notation = lexical_notations(source)[0]
        assert notation.reading is None
        assert notation.reading_status == "unknown"
        assert notation.orthographic_variants == [spelling]


def test_table_cell_correction_is_source_bound(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    document, _ = import_pdf(pdf, series="sample", number=9)
    payload = json.loads(document.model_dump_json())
    section = payload["sections"][0]
    section["blocks"] = [section["blocks"][0]]
    ref = dict(document_id=document.manifest.document_id, page=1, section_id=section["section_id"])
    table = dict(
        kind="table",
        table_type="unknown",
        columns=["particle", "meaning"],
        rows=[
            [dict(text="ね", sources=[ref]), dict(text="確認", sources=[ref])],
            [None, dict(text="感嘆", sources=[ref])],
        ],
        sources=[ref],
        status="unresolved",
    )
    section["blocks"].append(table)
    document = LessonDocument.model_validate_json(json.dumps(payload))
    title = next(b.content.text for b in document.sections[0].blocks if b.kind == "text")
    overlay = dict(
        document_id=document.manifest.document_id,
        document_sha256=document.manifest.document_sha256,
        reviewer="synthetic page reviewer",
        evidence="synthetic merged cell",
        corrections=[dict(page=1, expected_text=title, replacement=title)],
        table_cells=[
            dict(
                page=1,
                expected_table_text="ね|確認||感嘆",
                row=1,
                column=0,
                expected_text=None,
                replacement="ね",
            )
        ],
        resolutions=[dict(page=1, category="unrecovered_tables")],
    )
    path = tmp_path / f"{document.manifest.document_id}.json"
    for fault in ["table", "cell", "range", "edition"]:
        invalid = copy.deepcopy(overlay)
        if fault == "table":
            invalid["table_cells"][0]["expected_table_text"] = "different table"
        elif fault == "cell":
            invalid["table_cells"][0]["expected_text"] = "not empty"
        elif fault == "range":
            invalid["table_cells"][0]["row"] = 99
        else:
            invalid["document_sha256"] = "0" * 64
        path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            apply_corrections(document, QualityReport(document.manifest.document_id), tmp_path)
    path.write_text(json.dumps(overlay), encoding="utf-8")
    empty_report = QualityReport(document.manifest.document_id)
    with pytest.raises(ValueError, match="pending issue"):
        apply_corrections(document, empty_report, tmp_path)
    assert not empty_report.issues
    report = QualityReport(document.manifest.document_id)
    report.add("unrecovered_tables", 1, "merged cell")
    corrected = apply_corrections(document, report, tmp_path)
    result = corrected.sections[0].blocks[-1]
    assert result.rows[1][0].text == "ね"
    assert result.rows[1][0].sources[0].section_id == ref["section_id"]
    assert result.status == "reconstructed"
    assert document.sections[0].blocks[-1].rows[1][0] is None
    assert report.issues[0]["severity"] == "info"

    reviewed = copy.deepcopy(overlay)
    reviewed["table_cells"] = []
    reviewed["resolutions"] = [
        dict(
            page=1,
            category="unrecovered_tables",
            expected_table_text="ね|確認||感嘆",
            intentional_empty_cells=[[1, 0]],
        )
    ]
    path.write_text(json.dumps(reviewed), encoding="utf-8")
    report = QualityReport(document.manifest.document_id)
    report.add("unrecovered_tables", 1, "visually confirmed merged cell")
    retained = apply_corrections(document, report, tmp_path)
    assert retained.sections[0].blocks[-1].rows[1][0] is None
    assert retained.sections[0].blocks[-1].status == "reconstructed"
    reviewed["resolutions"][0]["intentional_empty_cells"] = []
    path.write_text(json.dumps(reviewed), encoding="utf-8")
    with pytest.raises(ValueError, match="missing cells differ"):
        apply_corrections(document, report, tmp_path)
