"""Readable product identities, independent of PDF hashes and database row IDs."""

import hashlib
import re
import unicodedata


def lesson_id(series: str, number: int) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", series) or number < 1:
        raise ValueError("Expected a lowercase series key and a positive lesson number")
    return f"{series}:L{number:02d}"


def document_id(series: str, number: int) -> str:
    return lesson_id(series, number).replace(":L", "-l")


def object_id(lesson: str, kind: str, key: str) -> str:
    """Caller supplies a semantic key for concepts; importer derives section keys."""
    if not key or not re.fullmatch(r"[a-z][a-z0-9_]*", kind):
        raise ValueError("Expected a kind and a nonempty semantic key")
    normalized = unicodedata.normalize("NFKC", key).strip()
    slug = re.sub(r"[^\w-]+", "-", normalized).strip("-")[:48]
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    return f"{lesson}:{kind}:{slug or 'unknown'}-{digest}"
