"""Recover ruled tables and horizontal reading units without flattening PDF text."""

import re
from dataclasses import dataclass

import pymupdf

from japanese_tutor.schemas.source import SourceRef, Table

from .pdf_spans import BoundingBox, PageLayout, Span
from .pitch import lexical_notations
from .report import QualityReport
from .ruby import body_lines, is_ruby_candidate, reconstruct_text


@dataclass
class LayoutUnit:
    y: float
    x: float
    spans: list[Span]
    cells: list[list[BoundingBox | None]] | None = None
    table_type: str = "unknown"
    unresolved: bool = False

    @property
    def text(self) -> str:
        return "".join(s.text for line in body_lines(self.spans) for s in line.spans).strip()

    @property
    def size(self) -> float:
        return max((s.size for s in self.spans if not is_ruby_candidate(s)), default=0)


def inside(span: Span, bbox: BoundingBox) -> bool:
    x = (span.bbox[0] + span.bbox[2]) / 2
    y = (span.bbox[1] + span.bbox[3]) / 2
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def recover_layout(
    page: pymupdf.Page, layout: PageLayout, report: QualityReport
) -> list[LayoutUnit]:
    units = []
    used = set()
    try:
        tables = page.find_tables(strategy="lines_strict").tables
    except Exception as error:
        report.add("unrecovered_tables", layout.page, "Table detection failed", reason=str(error))
        tables = []
    for table in tables:
        bbox = tuple(table.bbox)
        spans = [s for s in layout.spans if inside(s, bbox)]
        if table.col_count < 2 or table.row_count < 2 or not spans:
            continue
        cells = [[tuple(cell) if cell else None for cell in row.cells] for row in table.rows]
        table_type = "unknown"
        if table.col_count == 5 and any(re.fullmatch(r"\d+", s.text.strip()) for s in spans):
            table_type = "vocabulary"
        elif table.col_count == 3 and {"时态", "肯定", "否定"}.issubset(
            {s.text.strip() for s in spans}
        ):
            table_type = "conjugation"
        if table_type == "unknown" or any(cell is None for row in cells for cell in row):
            report.add(
                "unrecovered_tables",
                layout.page,
                "".join(s.text for s in spans)[:160],
                reason="Unknown or merged table structure retained for review",
            )
        units.append(LayoutUnit(bbox[1], bbox[0], spans, cells, table_type))
        used.update(s.order for s in spans)
    remaining = [s for s in layout.spans if s.order not in used]
    lines = body_lines(remaining)
    ruby_by_line: dict[int, list[Span]] = {i: [] for i in range(len(lines))}
    orphaned = []
    for ruby in (s for s in remaining if is_ruby_candidate(s)):
        candidates = [
            (i, line)
            for i, line in enumerate(lines)
            if line.size * 0.5 <= line.baseline - ruby.baseline <= line.size * 1.4
        ]
        if candidates:
            index = min(
                candidates,
                key=lambda pair: abs((pair[1].baseline - ruby.baseline) / pair[1].size - 0.92),
            )[0]
            ruby_by_line[index].append(ruby)
        else:
            orphaned.append(ruby)
    for i, line in enumerate(lines):
        units.append(LayoutUnit(line.baseline, line.x, line.spans + ruby_by_line[i]))
    for ruby in orphaned:
        report.add("unbound_ruby", layout.page, ruby.text, reason="No nearby body baseline")
    # Unruled, repeated wide gutters are ambiguous: don't merge them into a sentence.
    for unit in units:
        if unit.cells is None:
            spans = sorted(
                (s for s in unit.spans if not is_ruby_candidate(s)), key=lambda s: s.bbox[0]
            )
            if any(
                b.bbox[0] - a.bbox[2] > unit.size * 5
                for a, b in zip(spans, spans[1:], strict=False)
            ):
                unit.unresolved = True
                report.add(
                    "unsupported_layout",
                    layout.page,
                    unit.text,
                    reason="Wide column gap; source row order retained for review",
                )
    return sorted(units, key=lambda unit: (unit.y, unit.x))


def merge_paragraphs(units: list[LayoutUnit]) -> list[LayoutUnit]:
    """Join clear wrapped continuations, keeping list starts and headings separate."""
    merged: list[LayoutUnit] = []
    previous_line = None
    for unit in units:
        can_join = False
        if (
            previous_line
            and merged
            and previous_line.cells is None
            and unit.cells is None
            and not previous_line.unresolved
            and not unit.unresolved
        ):
            previous_text = previous_line.text
            can_join = (
                abs(unit.size - previous_line.size) < 0.5
                and unit.size <= 12.5
                and 0 < unit.y - previous_line.y <= unit.size * 2.8
                and unit.x <= previous_line.x + unit.size * 1.5
                and previous_text
                and previous_text[-1] not in "。？！：:＞〉】)）"
                and not re.match(r"^(?:\d+[.．]|[①-⑳]|[（(][0-9０-９]|[ABC][:：]|表\d)", unit.text)
                and unit.text not in {"基础训练", "口语训练", "表达扩展"}
            )
        if can_join:
            merged[-1].spans.extend(unit.spans)
        else:
            # Copy the spans so previous_line continues to represent a single source line.
            merged.append(
                LayoutUnit(
                    unit.y, unit.x, list(unit.spans), unit.cells, unit.table_type, unit.unresolved
                )
            )
        previous_line = unit
    return merged


def reconstruct_table(
    unit: LayoutUnit, source: SourceRef, report: QualityReport, *, series: str = "liangshuang"
) -> Table:
    assert unit.cells is not None
    columns = (
        ["number", "word", "part_of_speech", "meaning", "jlpt"]
        if unit.table_type == "vocabulary"
        else [f"column_{i + 1}" for i in range(len(unit.cells[0]))]
    )
    rows = []
    assigned = set()
    for row in unit.cells:
        contents = []
        for column, bbox in enumerate(row):
            if bbox is None:
                contents.append(None)
                continue
            spans = [s for s in unit.spans if inside(s, bbox)]
            assigned.update(s.order for s in spans)
            content = reconstruct_text(spans, source, report)
            if unit.table_type == "vocabulary" and column == 1 and content.text:
                notations = lexical_notations(content.text, series=series)
                content = content.model_copy(update={"lexical_notations": notations})
                for notation in notations:
                    if notation.reading_status == "unresolved":
                        report.add(
                            "unresolved_reading",
                            source.page,
                            notation.reading,
                            surface=notation.surface,
                            scope=notation.reading_scope,
                            section_id=source.section_id,
                        )
                    if notation.pitch_accent and notation.pitch_accent.status == "unresolved":
                        report.add(
                            "ambiguous_pitch",
                            source.page,
                            notation.pitch_accent.raw_notation,
                            surface=notation.surface,
                            section_id=source.section_id,
                        )
            contents.append(content)
        rows.append(contents)
    missing = [s.text for s in unit.spans if s.order not in assigned]
    unresolved = unit.table_type == "unknown" or any(c is None for row in rows for c in row)
    if missing:
        unresolved = True
        report.add(
            "unrecovered_tables",
            source.page,
            "".join(missing),
            reason="Spans could not be assigned to cells",
            section_id=source.section_id,
        )
    return Table(
        table_type=unit.table_type,
        columns=columns,
        rows=rows,
        sources=[source],
        status="unresolved" if unresolved else "reconstructed",
    )
