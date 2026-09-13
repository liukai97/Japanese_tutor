"""Importer observations are separate from the stable textbook contract."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from japanese_tutor.schemas.source import JapaneseText, LessonDocument, Table, TextBlock

REVIEW_CATEGORIES = {
    "unbound_ruby": ("未绑定的振假名", "对照 PDF 确认读音对应的正文；不要直接丢弃读音。"),
    "low_confidence_ruby": ("振假名匹配置信度不足", "核对候选正文与读音是否对应，以及标注范围。"),
    "ambiguous_pitch": ("音调标记含糊", "确认教材中复合或多值标记的含义；原始符号已保留。"),
    "unresolved_reading": ("读音尚未解析", "确认括号内是完整读音、部分读音，还是外语拼写。"),
    "unrecovered_tables": ("表格恢复不完整", "核对行列、合并单元格及未分配的文字。"),
    "low_confidence_sections": ("区块识别置信度不足", "核对标题、区块类型及章节边界。"),
    "missing_sources": ("缺少来源", "补充对应文档、页码和区块来源。"),
    "font_mapping": ("字体字符映射异常", "对照 PDF 阅读原文，确认符号串应表示的文字。"),
    "unsupported_layout": ("版面需要复核", "核对文字层、阅读顺序及多列文字的分隔。"),
}


def _markdown_text(value: object) -> str:
    return re.sub(r"([\\`*_\[\]<>#~|])", r"\\\1", str(value).replace("\n", " "))


def _literal_text(value: object) -> list[str]:
    """Keep extracted symbols and line breaks literal, including Markdown-like garbage."""
    text = str(value)
    fence = "~" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"~+", text)), default=3))
    return [fence + "text", text, fence, ""]


@dataclass
class QualityReport:
    document_id: str
    issues: list[dict] = field(default_factory=list)

    def add(
        self,
        category: str,
        page: int,
        text: str,
        *,
        severity: str = "review_required",
        **details: object,
    ) -> None:
        if severity == "review_required":
            details.setdefault(
                "review_stage",
                "llm_first" if category in {"font_mapping", "unsupported_layout"} else "human",
            )
        self.issues.append(
            dict(category=category, severity=severity, page=page, text=text, **details)
        )

    def write(self, path: Path) -> None:
        categories = [
            "filtered_objects",
            "unbound_ruby",
            "low_confidence_ruby",
            "ambiguous_pitch",
            "unrecovered_tables",
            "low_confidence_sections",
            "missing_sources",
            "font_mapping",
            "unsupported_layout",
        ]
        categories.append("unresolved_reading")
        categories.append("source_correction")
        payload = dict(
            document_id=self.document_id,
            summary={key: sum(i["category"] == key for i in self.issues) for key in categories},
            review_required=sum(i["severity"] == "review_required" for i in self.issues),
            model_review_required=sum(
                i["severity"] == "review_required" and i.get("review_stage") == "llm_first"
                for i in self.issues
            ),
            issues=self.issues,
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_review_markdown(
        self,
        path: Path,
        document: LessonDocument,
        *,
        model_review: bool = False,
        evidence_pages: set[int] | None = None,
    ) -> None:
        pending = [
            (index, issue)
            for index, issue in enumerate(self.issues, start=1)
            if issue["severity"] == "review_required"
            and (not model_review or issue.get("review_stage") == "llm_first")
        ]
        pending.sort(key=lambda item: item[1]["page"])
        sections = {section.section_id: section for section in document.sections}
        lines = [
            "# 教材导入大模型审核" if model_review else "# 教材导入人工复核",
            "",
            f"教材：{_markdown_text(document.manifest.filename)}",
            "",
            f"文档：{_markdown_text(self.document_id)}",
            "",
            f"待复核：{len(pending)} 项。页码为 PDF 文件页码（从 1 开始）。",
            "",
            "请对照原始 PDF 检查下列条目，记录确认结果或修正内容。",
            "",
            "此文件由导入流程生成，再次导入会覆盖；复核记录请另存。",
            "",
        ]
        if model_review:
            lines.extend(
                [
                    "大模型须先读取对应 PDF 页图，对照抽取原文逐项核验；"
                    "不要凭记忆改写。输出支持原文的修正与判断依据，"
                    "无法确定的条目再交人工。任务文件不表示已经完成模型审核。",
                    "",
                ]
            )
        if not pending:
            lines.extend(
                ["没有需要大模型审核的条目。" if model_review else "没有需要人工复核的条目。", ""]
            )
        page = None
        for number, (index, issue) in enumerate(pending, start=1):
            if issue["page"] != page:
                page = issue["page"]
                lines.extend([f"## 第 {page} 页", ""])
                if model_review and page in (evidence_pages or set()):
                    lines.extend([f"![PDF 第 {page} 页](review_evidence/page-{page}.png)", ""])
            label, instruction = REVIEW_CATEGORIES.get(
                issue["category"], (issue["category"], "对照 PDF 确认该条目的内容。")
            )
            subject = f"：{_markdown_text(issue['surface'])}" if issue.get("surface") else ""
            lines.extend(
                [
                    f"### {number}. {label}{subject}",
                    "",
                    instruction,
                    "",
                    f"质量报告条目：{index}（quality_report.json 的 issues 顺序）。",
                    "",
                ]
            )
            if issue.get("review_stage") == "llm_first":
                lines.extend(
                    [
                        "审核顺序：先由大模型对照 PDF 审核；仍不能确定时再交人工。"
                        + ("" if model_review else "任务见 [model_review.md](model_review.md)。"),
                        "",
                    ]
                )
            section_id = issue.get("section_id")
            if section_id:
                section = sections.get(section_id)
                if section:
                    lines.extend([f"区块：{_markdown_text(section.title)}", ""])
                lines.extend([f"区块 ID：{_markdown_text(section_id)}", ""])
            else:
                titles = [
                    section.title
                    for section in document.sections
                    if any(ref.page == page for ref in section.pages)
                ]
                if titles:
                    lines.extend(
                        [
                            "本页区块（条目未绑定具体区块）："
                            + "；".join(_markdown_text(title) for title in titles),
                            "",
                        ]
                    )
            if issue.get("reason"):
                lines.extend([f"检测说明：{_markdown_text(issue['reason'])}", ""])
            if "confidence" in issue:
                lines.extend([f"匹配／识别置信度：{issue['confidence']:.3f}", ""])
            if issue.get("font"):
                lines.extend([f"字体：{_markdown_text(issue['font'])}", ""])
            for diagnostic in issue.get("font_diagnostics", []):
                lines.extend(
                    [
                        f"字符映射诊断：{_markdown_text(diagnostic['subset'])}，"
                        f"ToUnicode 对象 {diagnostic['to_unicode_xref']}，"
                        f"状态 {_markdown_text(diagnostic['state'])}。",
                        "",
                    ]
                )
            if issue.get("scope"):
                scope = {"partial": "部分读音", "whole_word": "整词括注", "unknown": "未知"}
                lines.extend([f"读音范围：{scope.get(issue['scope'], issue['scope'])}", ""])
            lines.extend(["抽取原文／标记：", "", *_literal_text(issue["text"])])
            if "candidate_base" in issue:
                lines.extend(["候选正文：", "", *_literal_text(issue["candidate_base"])])
            lines.extend(["- [ ] 已复核", "", "复核结论／修正：", ""])
        path.write_text("\n".join(lines), encoding="utf-8")


def display_text(content: JapaneseText) -> str:
    result = content.text
    for ruby in reversed(content.ruby):
        start, end = ruby.range
        result = result[:start] + f"{result[start:end]}〔{ruby.reading}〕" + result[end:]
    return result.replace("|", "\\|").replace("\n", "<br>")


def write_preview(document: LessonDocument, path: Path) -> None:
    lines = [
        f"# {document.manifest.filename}",
        "",
        "自动导入预览；人工复核条目见 [review_required.md](review_required.md)。",
        "",
    ]
    for section in document.sections:
        pages = ", ".join(str(p.page) for p in section.pages)
        lines.extend([f"## {section.title} ({section.section_type}, p. {pages})", ""])
        for block in section.blocks:
            if isinstance(block, TextBlock):
                lines.extend([display_text(block.content), ""])
            elif isinstance(block, Table):
                lines.append("| " + " | ".join(block.columns) + " |")
                lines.append("| " + " | ".join("---" for _ in block.columns) + " |")
                lines.extend(
                    "| " + " | ".join(display_text(c) if c else "" for c in row) + " |"
                    for row in block.rows
                )
                lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
