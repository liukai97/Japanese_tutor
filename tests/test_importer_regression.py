"""Small, approved lesson 5 projections; local copyrighted inputs stay optional."""

import hashlib
import json
from pathlib import Path

import pytest

from japanese_tutor.curriculum.extract import (
    deterministic_curriculum,
    load_document,
    section_texts,
    validate_curriculum,
)
from japanese_tutor.importer.pipeline import import_pdf
from japanese_tutor.schemas.curriculum import SemanticCurriculum
from japanese_tutor.schemas.verification import VerificationSubmission
from japanese_tutor.verification.report import build_report

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden" / "lesson_05"
ARTIFACTS = ROOT / "data" / "generated" / "liangshuang-l05"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value):
    if isinstance(value, str):
        return "".join(value.split())
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def check_cases(curriculum, cases):
    objects = {obj.id: obj.model_dump(mode="json") for obj in curriculum.objects}
    for case in cases:
        obj = objects[case["id"]]
        sources = [support["source"] for support in obj["supports"]]
        for source in case["sources"]:
            assert source in sources, case["id"]
        for field, expected in case["fields"].items():
            actual = obj
            for key in field.split("."):
                actual = actual[key]
            if field == "target_concept_ids":
                actual, expected = sorted(actual), sorted(expected)
            elif field == "data.tables":
                actual = [{"columns": table["columns"], "rows": table["rows"]} for table in actual]
            assert normalize(actual) == normalize(expected), f"{case['id']}: {field}"


def test_lesson_05_pdf_regression():
    approval = read_json(GOLDEN / "approval.json")
    pdf = next(
        (
            path for path in (ROOT / "materials").glob("*.pdf")
            if hashlib.sha256(path.read_bytes()).hexdigest() == approval["document_sha256"]
        ),
        None,
    )
    if pdf is None:
        pytest.skip("Approved lesson 5 source PDF is not available locally")
    document, _ = import_pdf(pdf, series="liangshuang", number=5)
    golden = read_json(GOLDEN / "regression.json")
    assert [
        {"id": section.section_id, "pages": [page.page for page in section.pages]}
        for section in document.sections
    ] == golden["sections"]
    sections = {section.section_id: section for section in document.sections}
    for case in golden["document_cases"]:
        texts = section_texts(sections[case["section_id"]])
        matches = [text for text in texts if normalize(text.text) == normalize(case["text"])]
        assert len(matches) == 1, case["section_id"]
        text = matches[0]
        assert case["page"] in {ref.page for ref in text.sources}
        assert [
            {"base": text.text[ruby.range[0]:ruby.range[1]], "reading": ruby.reading}
            for ruby in text.ruby
        ] == case["ruby"]
    curriculum = deterministic_curriculum(document)
    validate_curriculum(document, curriculum)
    check_cases(curriculum, golden["lexical_cases"])


def test_lesson_05_semantic_regression():
    paths = [
        ARTIFACTS / "lesson_document.json",
        ARTIFACTS / "semantic" / "semantic_curriculum.json",
        ARTIFACTS / "semantic" / "verification.json",
    ]
    if any(not path.is_file() for path in paths):
        pytest.skip("Lesson 5 source, curriculum and verification artifacts are required locally")
    document = load_document(paths[0])
    curriculum = SemanticCurriculum.model_validate_json(paths[1].read_text(encoding="utf-8"))
    verification = VerificationSubmission.model_validate_json(paths[2].read_text(encoding="utf-8"))
    approval = read_json(GOLDEN / "approval.json")
    assert document.manifest.document_sha256 == approval["document_sha256"]
    report = build_report(document, curriculum, verification)
    assert report["review_queue"] == [], report["review_queue"]
    golden = read_json(GOLDEN / "regression.json")
    check_cases(curriculum, golden["lexical_cases"] + golden["semantic_cases"])
