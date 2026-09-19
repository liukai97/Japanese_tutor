"""Small conversion tests using a synthetic, copyright-free text-layer PDF."""

import copy
import json
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from japanese_tutor.cli import app
from japanese_tutor.importer.corrections import apply_corrections
from japanese_tutor.importer.pipeline import import_pdf, write_import
from japanese_tutor.importer.pitch import lexical_notations, parse_pitch
from japanese_tutor.importer.report import QualityReport
from japanese_tutor.schemas.source import LessonDocument, Table, TextBlock


def make_pdf(path: Path) -> None:
    with pymupdf.open() as pdf:
        for number in [1, 2]:
            page = pdf.new_page(width=600, height=800)
            page.insert_text((380, 45), "Textbook header", fontsize=9)
            page.insert_text((500, 780), str(number), fontsize=9)
            if number == 1:
                page.insert_text((90, 130), "会话", fontname="china-s", fontsize=14)
                page.insert_text((90, 180), "私は学生です。", fontname="japan", fontsize=12)
                page.insert_text((87, 169), "わたし", fontname="japan", fontsize=6)
                # One reading for multiple base characters, followed by split annotations.
                page.insert_text((114, 169), "がくせい", fontname="japan", fontsize=6)
                page.insert_text((90, 220), "学生", fontname="japan", fontsize=12)
                page.insert_text((90, 209), "がく", fontname="japan", fontsize=6)
                page.insert_text((102, 209), "せい", fontname="japan", fontsize=5.9)
                page.insert_text((400, 270), "よみ", fontname="japan", fontsize=6)
                page.insert_text((90, 310), "これは", fontname="japan", fontsize=12)
                page.insert_text((90, 331), "説明です。", fontname="japan", fontsize=12)
                page.insert_text((90, 370), "そうですか。（↗）", fontname="japan", fontsize=12)
                page.insert_text((90, 420), "私", fontname="japan", fontsize=12)
                page.insert_text((96.8, 404), "し", fontname="japan", fontsize=6)
            else:
                page.insert_text((90, 130), "会话用单词", fontname="china-s", fontsize=14)
                xs, ys = [80, 110, 340, 410, 520, 560], [170, 205, 240]
                for x in xs:
                    page.draw_line((x, ys[0]), (x, ys[-1]))
                for y in ys:
                    page.draw_line((xs[0], y), (xs[-1], y))
                for row, word in enumerate(["私（わたし）⓪", "留学生（りゅうがくせい）④③"]):
                    y = 190 + row * 35
                    for x, text in zip(
                        [85, 115, 345, 415, 525],
                        [str(row + 1), word, "名", "学生", "N5"],
                        strict=True,
                    ):
                        page.insert_text((x, y), text, fontname="japan", fontsize=10)
        pdf.save(path)


def test_pdf_to_document_core_features(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    make_pdf(path)
    output = tmp_path / "output"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "import",
            str(path),
            "--series",
            "sample",
            "--lesson-number",
            "5",
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    json_path = output / "lesson_document.json"
    document = LessonDocument.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert document.manifest.page_count == 2
    assert [s.section_type for s in document.sections] == ["dialogue", "vocabulary"]
    contents = [b.content for s in document.sections for b in s.blocks if isinstance(b, TextBlock)]
    sentence = next(c for c in contents if c.text == "私は学生です。")
    assert [(c.range, c.reading) for c in sentence.ruby] == [
        ((0, 1), "わたし"),
        ((2, 4), "がくせい"),
    ]
    split = next(c for c in contents if c.text == "学生")
    assert [(c.range, c.reading) for c in split.ruby] == [((0, 1), "がく"), ((1, 2), "せい")]
    assert any(c.text == "これは説明です。" for c in contents)
    assert next(c for c in contents if "↗" in c.text).utterance_prosody[0].contour == "rising"
    assert "Textbook header" not in json_path.read_text(encoding="utf-8")
    table = next(b for s in document.sections for b in s.blocks if isinstance(b, Table))
    assert (len(table.rows), len(table.columns), table.table_type) == (2, 5, "vocabulary")
    accent = table.rows[1][1].lexical_notations[0].pitch_accent
    assert accent.raw_notation == "④③" and accent.accent_numbers == [4, 3]
    assert accent.status == "unresolved"
    report = json.loads((output / "quality_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["unbound_ruby"] == 1
    assert report["summary"]["low_confidence_ruby"] == 1
    uncertain = next(c for c in contents if c.text == "私")
    assert uncertain.status == "unresolved" and uncertain.ruby == []
    assert report["summary"]["ambiguous_pitch"] == 1
    assert (output / "preview.md").is_file() and not (output / "debug").exists()
    review = (output / "review_required.md").read_text(encoding="utf-8")
    assert review.count("### ") == report["review_required"] == 3
    assert "未绑定的振假名" in review and "候选正文：" in review and "留学生" in review
    assert "第 1 页" in review and "第 2 页" in review and "会话用单词" in review
    assert "repeated_header" not in review and "page_number" not in review
    assert str(output / "review_required.md") in result.output
    assert all(c.sources[0].page == 1 for c in contents if c.text != "会话用单词")
    assert runner.invoke(app, ["validate", str(json_path)]).exit_code == 0
    rebuilt, _ = import_pdf(path, series="sample", number=5, debug_dir=tmp_path / "debug")
    assert rebuilt == document
    assert (tmp_path / "debug" / "spans.json").is_file()
    serialized = document.model_dump_json()
    assert all(key not in serialized for key in ['"bbox"', '"characters"', '"baseline"'])
    no_pending = QualityReport(document.manifest.document_id)
    no_pending.add(
        "filtered_objects", 1, "ignored image", severity="info", reason="image_excluded_from_text"
    )
    write_import(document, no_pending, output)
    empty_review = (output / "review_required.md").read_text(encoding="utf-8")
    assert "没有需要人工复核的条目。" in empty_review and "### " not in empty_review
    assert "image_excluded_from_text" not in empty_review


def test_document_rejects_invalid_conversion_output(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    make_pdf(path)
    document, _ = import_pdf(path, series="sample", number=5)
    payload = json.loads(document.model_dump_json())
    text_index = next(
        i for i, b in enumerate(payload["sections"][0]["blocks"]) if b["content"]["ruby"]
    )
    for fault in ["page", "section", "ruby", "unknown", "version"]:
        invalid = copy.deepcopy(payload)
        text = invalid["sections"][0]["blocks"][text_index]["content"]
        if fault == "page":
            text["sources"][0]["page"] = 0
        elif fault == "section":
            text["sources"][0]["section_id"] = "missing"
        elif fault == "ruby":
            text["ruby"][0]["range"] = [0, 100]
        elif fault == "unknown":
            text["bbox"] = [1, 2, 3, 4]
        else:
            invalid["metadata"]["schema_version"] = "future-version"
        with pytest.raises(ValidationError):
            LessonDocument.model_validate_json(json.dumps(invalid))


def test_japanese_notation_preserves_observations() -> None:
    for raw, numbers, connection in [
        ("⓪", [0], "single"),
        ("①＋①", [1, 1], "plus"),
        ("⓪+①", [0, 1], "plus"),
        ("④③", [4, 3], "adjacent"),
    ]:
        accent = parse_pitch(raw)
        assert (accent.raw_notation, accent.accent_numbers, accent.connection) == (
            raw,
            numbers,
            connection,
        )
        assert accent.status == "observed"
        assert (
            accent.interpretation
            == {"single": "single", "plus": "component_accents", "adjacent": "alternatives"}[
                connection
            ]
        )
        if connection != "single":
            assert parse_pitch(raw, series="unknown").status == "unresolved"
    word = lexical_notations("東京大学（とうきょうだいがく）\n⑤")[0]
    assert word.surface == "東京大学" and word.reading == "とうきょうだいがく"
    assert word.pitch_accent.raw_notation == "⑤"
    wrapped = lexical_notations("お疲れ様でした\n（おつかれさまでした）⑦")[0]
    assert wrapped.surface == "お疲れ様でした" and wrapped.reading == "おつかれさまでした"
    assert wrapped.pitch_accent.raw_notation == "⑦"
    abbreviation = lexical_notations(
        "アパート②\n〔アパートメントハウス⑧\n（apartment house）の略〕"
    )
    assert [item.surface for item in abbreviation] == ["アパート", "アパートメントハウス"]
    assert abbreviation[1].loanword_original == "apartment house"
    assert abbreviation[1].pitch_accent.raw_notation == "⑧"
    assert all(item.reading_status != "unresolved" for item in abbreviation)
    partial = lexical_notations("お母（かあ）さん②")[0]
    assert partial.surface == "お母さん" and partial.reading_scope == "partial"
    assert partial.reading_status == "observed" and partial.reading_range == (1, 2)
    loan = lexical_notations("アメリカ（America）⓪")[0]
    assert loan.loanword_original == "America" and loan.reading is None
    mixed = lexical_notations("アメリカ人（America じん）④")[0]
    assert (mixed.loanword_original, mixed.reading, mixed.reading_range) == (
        "America",
        "じん",
        (4, 5),
    )
    suffix = lexical_notations("イタリア語（-ご）⓪")[0]
    assert suffix.reading == "ご" and suffix.reading_range == (4, 5)
    assert lexical_notations("私（watashi）⓪")[0].reading_status == "unresolved"
    assert parse_pitch("④③", is_phrase=True).status == "unresolved"
    assert lexical_notations("こんにちは【今日は】⑤")[0].orthographic_variants == ["今日は"]


def test_model_review_and_source_correction_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    make_pdf(path)
    document, _ = import_pdf(path, series="sample", number=5)
    report = QualityReport(document.manifest.document_id)
    report.add("font_mapping", 2, "会话用单词", font="test_font")
    output = tmp_path / "output"
    write_import(document, report, output, source_pdf=path)
    model_review = (output / "model_review.md").read_text(encoding="utf-8")
    assert "先读取对应 PDF 页图" in model_review and "会话用单词" in model_review
    assert "review_evidence/page-2.png" in model_review
    assert (output / "review_evidence" / "page-2.png").is_file()
    assert report.issues[0]["severity"] == "review_required"
    overlay = dict(
        document_id=document.manifest.document_id,
        document_sha256=document.manifest.document_sha256,
        reviewer="test source reviewer",
        evidence="PDF page 2",
        corrections=[dict(page=2, expected_text="会话用单词", replacement="会话用单词", ruby=[])],
        resolutions=[dict(page=2, category="font_mapping")],
    )
    directory = tmp_path / "corrections"
    directory.mkdir()
    overlay_path = directory / f"{document.manifest.document_id}.json"
    for fault in ["hash", "text", "ruby"]:
        invalid = copy.deepcopy(overlay)
        if fault == "hash":
            invalid["document_sha256"] = "0" * 64
        elif fault == "text":
            invalid["corrections"][0]["expected_text"] = "nonexistent source"
        else:
            invalid["corrections"][0]["ruby"] = [dict(range=[0, 100], reading="よみ")]
        overlay_path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            apply_corrections(document, report, directory)
        assert report.issues[0]["severity"] == "review_required"
    overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
    corrected = apply_corrections(document, report, directory)
    assert corrected.manifest == document.manifest
    assert report.issues[0]["resolution"] == "model_reviewed_and_corrected"
    assert report.issues[-1]["category"] == "source_correction"
