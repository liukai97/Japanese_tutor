"""Conservative geometric filtering, with an audit record for every removed object."""

import re
from collections import defaultdict

from .pdf_spans import PageLayout
from .report import QualityReport


def suspicious_mapping(text: str) -> bool:
    compact = text.strip()
    return "\ufffd" in text or (
        len(compact) >= 8
        and sum(c.isascii() and not c.isalnum() and not c.isspace() for c in compact) / len(compact)
        > 0.3
    )


def clean_pages(layouts: list[PageLayout], report: QualityReport) -> list[PageLayout]:
    occurrences = defaultdict(set)
    for page in layouts:
        for span in page.spans:
            if span.bbox[1] < page.height * 0.115 and span.text.strip():
                key = (span.text.strip(), round(span.bbox[0] / 5), round(span.bbox[1] / 5))
                occurrences[key].add(page.page)
    cleaned = []
    for page in layouts:
        kept = []
        for span in page.spans:
            if not span.text.strip():
                continue
            key = (span.text.strip(), round(span.bbox[0] / 5), round(span.bbox[1] / 5))
            repeated = len(occurrences[key]) >= max(2, len(layouts) * 0.6)
            reason = None
            if (
                span.bbox[1] < page.height * 0.115
                and repeated
                and (span.size < 11 or suspicious_mapping(span.text))
            ):
                reason = "repeated_header"
            elif span.bbox[1] > page.height * 0.92 and re.fullmatch(r"\d+", span.text.strip()):
                reason = "page_number"
            if reason:
                report.add("filtered_objects", page.page, span.text, severity="info", reason=reason)
            else:
                kept.append(span)
                if span.direction != (1.0, 0.0):
                    report.add(
                        "unsupported_layout",
                        page.page,
                        span.text,
                        reason="Nonhorizontal text requires review",
                    )
        suspect_fonts = {s.font for s in kept if suspicious_mapping(s.text)} | {
            m["font"]
            for m in page.font_mappings
            if m["state"] == "empty_to_unicode"
            and any(s.font == m["font"] for s in kept)
            and all(
                other["state"] == "empty_to_unicode"
                for other in page.font_mappings
                if other["font"] == m["font"]
            )
        }
        for font in sorted(suspect_fonts):
            segments = [s.text for s in kept if s.font == font]
            report.add(
                "font_mapping",
                page.page,
                "\n".join(segments),
                font=font,
                reason=(
                    "疑似字符映射异常；需先由大模型对照 PDF 审核。"
                    "空 ToUnicode 映射不能通过改 UTF-8/GBK 恢复。"
                ),
                font_diagnostics=[m for m in page.font_mappings if m["font"] == font],
            )
        for bbox in page.images:
            report.add(
                "filtered_objects",
                page.page,
                "image",
                severity="info",
                reason="image_excluded_from_text",
                bbox=bbox,
            )
        cleaned.append(
            PageLayout(page.page, page.width, page.height, kept, page.images, page.font_mappings)
        )
    return cleaned
