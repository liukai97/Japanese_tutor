"""Reviewed header removal and fullwidth foreign originals stay lossless."""

import json
from pathlib import Path

import pymupdf
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


def test_kana_in_bracketed_alternate_spelling() -> None:
    word = lexical_notations("うるさい（【煩い】）③")[0]
    assert word.surface == "うるさい"
    assert word.orthographic_variants == ["煩い"]
    assert word.reading is None and word.reading_status == "unknown"
    assert word.pitch_accent.raw_notation == "③"
    assert lexical_notations("すぐ（【直】ぐ）①")[0].orthographic_variants == ["直ぐ"]
    other_word = lexical_notations("うるさい（【煩い】）③", series="other")[0]
    assert other_word.reading_status == "unresolved"


def test_wrapped_latin_abbreviation_expansion() -> None:
    words = lexical_notations(
        "SNS（ソーシャル・ネットワーク・サービス\nの略）（social network service）"
    )
    assert [word.surface for word in words] == ["SNS", "ソーシャル・ネットワーク・サービス"]
    for word in words:
        assert word.loanword_original == "social network service"
        assert word.reading is None and word.reading_status == "unknown"
        assert word.pitch_accent is None
    # A kana expansion without the explicit abbreviation marker is still a reading.
    assert lexical_notations("SNS（エスエヌエス）")[0].reading == "エスエヌエス"


def test_passage_and_passage_vocabulary_boundaries(tmp_path: Path) -> None:
    pdf = tmp_path / "passage.pdf"
    with pymupdf.open() as source:
        page = source.new_page(width=600, height=800)
        for y, text in [(100, "学习目标"), (145, "学习简体"), (220, "课文"),
                        (265, "生活について"), (340, "课文单词"), (385, "単語")]:
            page.insert_text((90, y), text, fontname="china-s",
                             fontsize=14 if text in {"学习目标", "课文", "课文单词"} else 12)
        source.save(pdf)
    document, _ = import_pdf(pdf, series="sample", number=17)
    assert [(section.title, section.section_type) for section in document.sections] == [
        ("学习目标", "lesson_goal"), ("课文", "unknown"), ("课文单词", "vocabulary")
    ]
    assert len(document.sections[0].blocks) == 2
    assert len(document.sections[1].blocks) == 2


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


def test_reviewed_ruby_correction_resolves_its_observation(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    document, _ = import_pdf(pdf, series="sample", number=18)
    payload = json.loads(document.model_dump_json())
    section = payload["sections"][0]
    section["blocks"] = [section["blocks"][0]]
    section["blocks"][0]["content"].update(text="家族・大切", ruby=[], status="unresolved")
    document = LessonDocument.model_validate_json(json.dumps(payload))
    overlay = dict(
        document_id=document.manifest.document_id,
        document_sha256=document.manifest.document_sha256,
        reviewer="synthetic page reviewer", evidence="synthetic split readings on page 1",
        corrections=[dict(page=1, expected_text="家族・大切", replacement="家族・大切",
                          ruby=[dict(range=[0, 2], reading="かぞく"),
                                dict(range=[3, 5], reading="たいせつ")])],
        resolutions=[dict(page=1, category="low_confidence_ruby")],
    )
    (tmp_path / f"{document.manifest.document_id}.json").write_text(
        json.dumps(overlay), encoding="utf-8"
    )
    report = QualityReport(document.manifest.document_id)
    report.add("low_confidence_ruby", 1, "かぞく たいせつ", candidate_base="家族・大切")
    corrected = apply_corrections(document, report, tmp_path)
    assert report.issues[0]["severity"] == "info"
    assert report.issues[0]["resolution"] == "model_reviewed_and_corrected"
    assert [(r.range, r.reading) for r in corrected.sections[0].blocks[0].content.ruby] == [
        ((0, 2), "かぞく"), ((3, 5), "たいせつ")
    ]
