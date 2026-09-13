"""Reconstruct source ruby with conservative character-level geometric matching."""

import re
from dataclasses import dataclass

from japanese_tutor.schemas.source import JapaneseText, RubyAnnotation, SourceRef

from .pdf_spans import Character, Span
from .pitch import prosody_notations
from .report import QualityReport


def is_ruby_candidate(span: Span) -> bool:
    compact = span.text.strip().replace(" ", "")
    return span.size <= 7 and bool(compact) and bool(re.fullmatch(r"[ぁ-ゖァ-ヺー]+", compact))


def base_character(char: Character) -> bool:
    return bool(re.fullmatch(r"[\u3400-\u9fff々〇0-9０-９]", char.text))


@dataclass
class Line:
    spans: list[Span]

    @property
    def baseline(self) -> float:
        return max(self.spans, key=lambda s: s.size).baseline

    @property
    def x(self) -> float:
        return min(s.bbox[0] for s in self.spans)

    @property
    def size(self) -> float:
        return max(s.size for s in self.spans)


def body_lines(spans: list[Span]) -> list[Line]:
    lines: list[Line] = []
    for span in sorted(
        (s for s in spans if not is_ruby_candidate(s)),
        key=lambda s: (s.baseline, s.bbox[0], s.order),
    ):
        matches = [line for line in lines if abs(line.baseline - span.baseline) <= 3.5]
        if matches:
            min(matches, key=lambda line: abs(line.baseline - span.baseline)).spans.append(span)
        else:
            lines.append(Line([span]))
    for line in lines:
        line.spans.sort(key=lambda s: (s.bbox[0], s.order))
    return sorted(lines, key=lambda line: (line.baseline, line.x))


def reconstruct_text(
    spans: list[Span], source: SourceRef, report: QualityReport, *, separator: str = "\n"
) -> JapaneseText:
    issue_count = len(report.issues)
    lines = body_lines(spans)
    text_parts = []
    indexed: list[tuple[int, Character, Span]] = []
    length = 0
    for line in lines:
        if text_parts:
            text_parts.append(separator)
            length += len(separator)
        # Keep source spacing, trim only the ends of each recovered line.
        chars = [(char, span) for span in line.spans for char in span.characters]
        while chars and chars[0][0].text.isspace():
            chars.pop(0)
        while chars and chars[-1][0].text.isspace():
            chars.pop()
        for char, span in chars:
            text_parts.append(char.text)
            indexed.append((length, char, span))
            length += len(char.text)
    annotations = []
    claimed = set()
    for ruby in sorted(
        (s for s in spans if is_ruby_candidate(s)), key=lambda s: (s.baseline, s.bbox[0], s.order)
    ):
        candidates = []
        for index, char, span in indexed:
            cx = (char.bbox[0] + char.bbox[2]) / 2
            distance = span.baseline - ruby.baseline
            if (
                base_character(char)
                and span.size >= ruby.size * 1.6
                and span.size * 0.5 <= distance <= span.size * 1.4
                and ruby.bbox[0] - span.size * 0.1 <= cx <= ruby.bbox[2] + span.size * 0.1
            ):
                overlap = max(0, min(char.bbox[2], ruby.bbox[2]) - max(char.bbox[0], ruby.bbox[0]))
                coverage = overlap / min(char.bbox[2] - char.bbox[0], ruby.bbox[2] - ruby.bbox[0])
                score = 0.75 * min(1.0, coverage) + 0.25 * max(
                    0, 1 - abs(distance / span.size - 0.92)
                )
                candidates.append((index, score, span.baseline))
        if not candidates:
            report.add("unbound_ruby", source.page, ruby.text, section_id=source.section_id)
            continue
        # Select one baseline, then all consecutive base characters covered by the reading.
        baseline = max(candidates, key=lambda c: c[1])[2]
        selected = sorted((i, score) for i, score, y in candidates if abs(y - baseline) < 2)
        start, end = selected[0][0], selected[-1][0] + 1
        score = min(s for _, s in selected)
        contiguous = [i for i, _ in selected] == list(range(start, end))
        competing_baseline = any(
            abs(y - baseline) >= 2 and alternative >= score - 0.05
            for _, alternative, y in candidates
        )
        if (
            not contiguous
            or score < 0.85
            or competing_baseline
            or any(i in claimed for i in range(start, end))
        ):
            report.add(
                "low_confidence_ruby",
                source.page,
                ruby.text,
                section_id=source.section_id,
                candidate_base="".join(text_parts)[start:end],
                confidence=round(score, 3),
            )
            continue
        claimed.update(range(start, end))
        annotations.append(RubyAnnotation(range=(start, end), reading=ruby.text.strip()))
    text = "".join(text_parts)
    unresolved = len(report.issues) > issue_count or any(
        i["category"] == "font_mapping"
        and i["page"] == source.page
        and any(s.font == i["font"] for s in spans)
        for i in report.issues
    )
    return JapaneseText(
        text=text,
        ruby=sorted(annotations, key=lambda a: a.range),
        utterance_prosody=prosody_notations(text),
        sources=[source],
        status="unresolved" if unresolved else "reconstructed",
    )
