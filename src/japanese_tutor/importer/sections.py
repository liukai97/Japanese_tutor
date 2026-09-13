"""Source heading rules; uncertain classification remains explicit."""

import re

from .layout import LayoutUnit

HEADINGS = {
    "学习目标": "lesson_goal",
    "如何用日语表达？": "lesson_goal",
    "会话": "dialogue",
    "会话用单词": "vocabulary",
    "语法解释用单词": "vocabulary",
    "习题用单词": "vocabulary",
    "语法解释": "grammar",
    "表达扩展": "pragmatics",
    "听力训练": "listening_exercise",
    "基础训练": "controlled_exercise",
    "口语训练": "speaking_exercise",
}


def heading(unit: LayoutUnit, current_type: str) -> tuple[str, str, float] | None:
    if unit.cells is not None:
        return None
    text = unit.text
    compact = re.sub(r"\s+", "", text)
    if compact in HEADINGS:
        return text, HEADINGS[compact], 0.98
    if re.match(r"^第\d+課", compact) and unit.size >= 14:
        return text, "lesson_goal", 0.98
    if (
        current_type in {"grammar", "pragmatics"}
        and re.match(r"^\d+[.．]", compact)
        and unit.size >= 11.5
    ):
        return text, current_type, 0.9
    if current_type == "unknown" and unit.size >= 13.5:
        return text, "unknown", 0.4
    return None


def text_role(text: str) -> str:
    if re.match(r"^(?:\d+[.．]|[①-⑳]|[（(]\s*[0-9０-９]+|[ABC][:：])", text):
        return "list_item"
    return "paragraph"
