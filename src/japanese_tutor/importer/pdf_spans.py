"""Ephemeral character geometry. No stable span identities are persisted."""

import re
from dataclasses import dataclass, field

import pymupdf

BoundingBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Character:
    text: str
    bbox: BoundingBox


@dataclass(frozen=True)
class Span:
    text: str
    bbox: BoundingBox
    font: str
    size: float
    baseline: float
    order: int
    characters: tuple[Character, ...]
    direction: tuple[float, float] = (1.0, 0.0)


@dataclass
class PageLayout:
    page: int
    width: float
    height: float
    spans: list[Span]
    images: list[BoundingBox]
    font_mappings: list[dict] = field(default_factory=list)


def font_mappings(page: pymupdf.Page) -> list[dict]:
    """Inspect explicit ToUnicode maps; internal font codes are not text encodings."""
    result = []
    for xref, _, subtype, name, resource, encoding, _ in page.get_fonts(full=True):
        kind, reference = page.parent.xref_get_key(xref, "ToUnicode")
        if subtype != "TrueType" or kind != "xref":
            continue
        cmap_xref = int(reference.split()[0])
        cmap = page.parent.xref_stream(cmap_xref)
        if cmap is not None:
            mapped = bool(re.search(rb"\b[1-9]\d*\s+beginbf(?:char|range)\b", cmap))
            result.append(
                dict(
                    font=name.split("+")[-1],
                    subset=name,
                    resource=resource,
                    font_xref=xref,
                    to_unicode_xref=cmap_xref,
                    encoding=encoding or "unspecified",
                    state="mapped" if mapped else "empty_to_unicode",
                )
            )
    return result


def extract_page(page: pymupdf.Page) -> PageLayout:
    spans = []
    images = []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] == 1:
            images.append(tuple(block["bbox"]))
        elif block["type"] == 0:
            for line in block["lines"]:
                for span in line["spans"]:
                    chars = tuple(Character(c["c"], tuple(c["bbox"])) for c in span["chars"])
                    spans.append(
                        Span(
                            "".join(c.text for c in chars),
                            tuple(span["bbox"]),
                            span["font"],
                            float(span["size"]),
                            float(span["origin"][1]),
                            len(spans),
                            chars,
                            tuple(line["dir"]),
                        )
                    )
    return PageLayout(
        page.number + 1,
        float(page.rect.width),
        float(page.rect.height),
        spans,
        images,
        font_mappings(page),
    )
