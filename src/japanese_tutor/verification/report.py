"""Deterministic release gates; model confidence never substitutes for evidence."""

from pathlib import Path

from japanese_tutor.curriculum.extract import artifact_hash, write_json
from japanese_tutor.schemas.curriculum import ConceptRelation, ExerciseExample
from japanese_tutor.verification.codex import validate_curriculum, validate_verification


def has_unresolved(value) -> bool:
    if isinstance(value, dict):
        return any(v == "unresolved" or has_unresolved(v) for v in value.values())
    if isinstance(value, list):
        return any(has_unresolved(v) for v in value)
    return False


def build_report(document, curriculum, verification=None):
    validate_curriculum(document, curriculum)
    if verification is not None:
        validate_verification(document, curriculum, verification)
    checks = {c.object_id: c for c in verification.objects} if verification else {}
    coverage = {c.section_id: c for c in verification.sections} if verification else {}
    queue = []
    blocked_sections = set()
    for section in document.sections:
        # Records may be submitted in any order; identify by ID, not JSON ordering.
        record = next(r for r in curriculum.sections if r.section_id == section.section_id)
        check = coverage.get(section.section_id)
        reasons = []
        if check is None:
            reasons.append("missing_coverage_verification")
        elif check.verdict != "pass":
            reasons.append("source_omissions")
        if record.disposition == "unresolved":
            reasons.append("section_processing_unresolved")
        if has_unresolved(section.model_dump(mode="json")):
            reasons.append("source_unresolved")
        if section.confidence < 0.8:
            reasons.append("source_low_confidence_review")
        if reasons:
            blocked_sections.add(section.section_id)
            queue.append(
                {
                    "section_id": section.section_id,
                    "reasons": reasons,
                    "omissions": check.omissions if check else [],
                }
            )
    passed = []
    disagreement = 0
    unresolved = 0
    proposed = 0
    low_confidence = []
    for obj in curriculum.objects:
        reasons = []
        check = checks.get(obj.id)
        if check is None:
            reasons.append("missing_object_verification")
        elif check.verdict != "pass":
            reasons.append("extractor_verifier_disagreement")
            disagreement += 1
        if has_unresolved(obj.model_dump(mode="json")):
            reasons.append("unresolved")
            unresolved += 1
        if obj.provenance == "proposed":
            reasons.append("proposed")
            proposed += 1
        if any(s.source.section_id in blocked_sections for s in obj.supports):
            reasons.append("source_section_blocked")
        if obj.confidence < 0.8 or (check and check.confidence < 0.8):
            # Explicit review policy, not a probability calibration claim.
            reasons.append("low_confidence_review")
            low_confidence.append(obj.id)
        if reasons:
            queue.append(
                {
                    "object_id": obj.id,
                    "reasons": reasons,
                    "sources": [s.source.model_dump(mode="json") for s in obj.supports],
                    "issues": [i.model_dump(mode="json") for i in check.issues] if check else [],
                }
            )
        else:
            passed.append(obj.id)
    # Review dependencies propagate to exercises and relations until stable.
    passed_set = set(passed)
    changed = True
    while changed:
        changed = False
        for obj in curriculum.objects:
            if obj.id not in passed_set:
                continue
            if isinstance(obj, ConceptRelation):
                dependencies = {obj.source_id, obj.target_id} - {curriculum.lesson.id}
                reason = "endpoint_not_approved"
            elif isinstance(obj, ExerciseExample):
                dependencies = set(obj.target_concept_ids)
                reason = "exercise_target_not_approved"
            else:
                continue
            if not dependencies <= passed_set:
                passed_set.remove(obj.id)
                queue.append({"object_id": obj.id, "reasons": [reason]})
                changed = True
    return {
        "curriculum_sha256": artifact_hash(curriculum),
        "status": "review_required" if queue else "automatically_verified",
        "human_approved": False,
        "summary": {
            "objects": len(curriculum.objects),
            "automatically_passed": len(passed_set),
            "disagreements": disagreement,
            "unresolved": unresolved,
            "proposed": proposed,
            "missing_object_checks": len(curriculum.objects) - len(checks),
            "missing_coverage_checks": len(curriculum.sections) - len(coverage),
            "omissions": sum(len(c.omissions) for c in coverage.values()),
            "low_confidence": len(low_confidence),
            "no_source": 0,
            "review_entries": len(queue),
        },
        "accepted_object_ids": sorted(passed_set),
        "review_queue": queue,
        "confidence_review_threshold": 0.8,
        "verification": verification.model_dump(mode="json") if verification else None,
    }


def write_report(document, curriculum, verification, output: Path):
    report = build_report(document, curriculum, verification)
    write_json(output / "verification_report.json", report)
    lines = ["# 教材语义验证报告", "", f"状态：{report['status']}；尚未人工确认。", ""]
    lines += [f"- {key}: {value}" for key, value in report["summary"].items()]
    lines += ["", "## 复核队列", ""]
    for entry in report["review_queue"]:
        lines += [
            f"### {entry.get('object_id', entry.get('section_id'))}",
            "",
            ", ".join(entry["reasons"]),
            "",
        ]
        for issue in entry.get("issues", []):
            lines.append(f"- {issue['field']}: {issue['message']}")
        for omission in entry.get("omissions", []):
            lines.append(f"- 遗漏：{omission}")
        for source in entry.get("sources", []):
            lines.append(f"- p. {source['page']} / {source['section_id']}")
        lines.append("")
    if not report["review_queue"]:
        lines.append("没有自动验证阻断项；阶段 3 仍须异常复核和分层抽样。")
    output.mkdir(parents=True, exist_ok=True)
    (output / "verification_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
