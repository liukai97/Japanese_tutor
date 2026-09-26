"""Synthetic coverage for letter ruby and vocabulary table continuation pages."""

from pathlib import Path

import pymupdf

from japanese_tutor.importer.pipeline import import_pdf
from japanese_tutor.importer.pitch import lexical_notations
from japanese_tutor.schemas.source import Table, TextBlock


def test_letter_and_optional_kana_ruby(tmp_path: Path) -> None:
    path = tmp_path / "ruby.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((80, 130), "会话", fontname="china-s", fontsize=14)
        page.insert_text((100, 200), "Sサイズ", fontname="japan", fontsize=16)
        page.insert_text((100, 185), "エス", fontname="japan", fontsize=7.9)
        page.insert_text((100, 250), "10本", fontname="japan", fontsize=12)
        page.insert_text((100, 239), "じ（ゅ）っぽん", fontname="japan", fontsize=5.5)
        pdf.save(path)
    document, report = import_pdf(path, series="sample", number=8)
    texts = [b.content for s in document.sections for b in s.blocks if isinstance(b, TextBlock)]
    letter = next(t for t in texts if t.text == "Sサイズ")
    assert [(r.range, r.reading) for r in letter.ruby] == [((0, 1), "エス")]
    counter = next(t for t in texts if t.text == "10本")
    assert [(r.range, r.reading) for r in counter.ruby] == [((0, 3), "じ（ゅ）っぽん")]
    assert not any(i["category"] == "unbound_ruby" for i in report.issues)


def test_single_row_table_splits_cross_cell_span(tmp_path: Path) -> None:
    path = tmp_path / "table.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((80, 130), "会话用单词", fontname="china-s", fontsize=14)
        xs = [80, 110, 330, 390, 510, 560]
        for x in xs:
            page.draw_line((x, 170), (x, 210))
        for y in [170, 210]:
            page.draw_line((80, y), (560, y))
        page.insert_text((85, 195), "1", fontsize=10)
        page.insert_text((115, 195), "テスト", fontname="japan", fontsize=10)
        # One PDF span straddles the POS/meaning border; each character owns a cell.
        page.insert_text((370, 195), "名词测试", fontname="china-s", fontsize=10)
        page.insert_text((520, 195), "N5", fontsize=10)
        pdf.save(path)
    document, _ = import_pdf(path, series="sample", number=8)
    tables = [b for s in document.sections for b in s.blocks if isinstance(b, Table)]
    assert len(tables) == 1
    table = tables[0]
    assert table.table_type == "vocabulary" and len(table.rows) == 1
    assert table.rows[0][2].text == "名词"
    assert table.rows[0][3].text == "测试"


def test_language_tag_is_part_of_loanword_original() -> None:
    word = lexical_notations("ヨーグルト（【德】yoghurt）③")[0]
    assert word.loanword_original == "【德】yoghurt"
    assert word.reading is None and word.reading_status == "unknown"
    unknown = lexical_notations("ヨーグルト（【德】yoghurt）③", series="other")[0]
    assert unknown.reading_status == "unresolved" and unknown.loanword_original is None
