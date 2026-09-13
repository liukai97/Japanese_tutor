"""Deterministic source inventory; a hash identifies an edition, not a lesson."""

import hashlib
import re
from collections import Counter
from pathlib import Path

import pymupdf

from japanese_tutor.ids import document_id, lesson_id
from japanese_tutor.schemas.source import DocumentManifest, FontStat, Page

from .pdf_spans import PageLayout, extract_page


def identify_lesson(path: Path, number: int | None = None) -> int:
    if number is None:
        match = re.search(r"第\s*(\d+)\s*课", path.stem)
        if match is None:
            raise ValueError("Cannot infer lesson number; supply --lesson-number")
        number = int(match[1])
    if number < 1:
        raise ValueError("Lesson number must be positive")
    return number


def make_manifest(
    path: Path, layouts: list[PageLayout], series: str, number: int
) -> DocumentManifest:
    counts = Counter((s.font, round(s.size, 3)) for p in layouts for s in p.spans)
    with path.open("rb") as source:
        sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    return DocumentManifest(
        document_id=document_id(series, number),
        lesson_id=lesson_id(series, number),
        filename=path.name,
        document_sha256=sha256,
        page_count=len(layouts),
        pages=[Page(page=p.page, width=p.width, height=p.height) for p in layouts],
        fonts=[
            FontStat(name=font, size=size, span_count=count)
            for (font, size), count in sorted(counts.items())
        ],
    )


def scan_materials(directory: Path, series: str = "liangshuang") -> list[DocumentManifest]:
    manifests = []
    for path in sorted(directory.glob("*.pdf"), key=lambda p: (identify_lesson(p), p.name)):
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError(f"Encrypted PDF requires a password: {path.name}")
            layouts = [extract_page(page) for page in pdf]
            manifests.append(make_manifest(path, layouts, series, identify_lesson(path)))
    return manifests
