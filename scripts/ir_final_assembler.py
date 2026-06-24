#!/usr/bin/env python3
"""Final report assembler for IR quality-production packages.

The assembler is intentionally conservative: it only assembles validated section
packages and does not create new facts, numbers, or claims.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"


def assemble_final_report(
    research_plan: dict[str, Any],
    section_index: dict[str, Any],
    debate_review: dict[str, Any],
) -> dict[str, Any]:
    if debate_review.get("verdict") not in ("PASS", "WARN"):
        return {
            "ok": False,
            "block_reason": "debate_review_not_passed",
            "markdown": "",
            "facts_used": [],
            "sections_assembled": [],
            "issues": debate_review.get("issues", []),
        }

    entity = research_plan.get("entity", "目标公司")
    objective = research_plan.get("objective", "形成投资研究报告")
    lines = [f"# {entity} 投资研究报告", "", f"> {objective}", ""]
    facts_used: list[str] = []
    sections_assembled: list[str] = []

    for item in section_index.get("packages", []) or []:
        validation = item.get("validation", {}) or {}
        if not validation.get("passed"):
            continue
        package = item.get("package", {}) or {}
        title = package.get("section_title") or package.get("section_id") or item.get("step_name", "未命名章节")
        draft = package.get("markdown_draft", "")
        if not draft:
            continue
        lines.extend([f"## {title}", "", draft.strip(), ""])
        sections_assembled.append(package.get("section_id") or item.get("step_name", title))
        for fact_id in package.get("facts_used", []) or []:
            if fact_id not in facts_used:
                facts_used.append(fact_id)

    if not sections_assembled:
        return {
            "ok": False,
            "block_reason": "no_valid_sections",
            "markdown": "",
            "facts_used": [],
            "sections_assembled": [],
            "issues": [{"code": "NO_VALID_SECTIONS", "message": "No passed section packages available"}],
        }

    lines.extend(["## 附录：事实引用清单", ""])
    for fact_id in facts_used:
        lines.append(f"- {fact_id}")
    lines.append("")

    return {
        "ok": True,
        "block_reason": "",
        "markdown": "\n".join(lines),
        "facts_used": facts_used,
        "sections_assembled": sections_assembled,
        "issues": [],
    }


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_final_report(task_id: str, tasks_dir: Path = TASKS_DIR) -> str:
    tasks_dir = Path(tasks_dir)
    research_plan = _read_json(tasks_dir / f"{task_id}-research_plan.json", {"entity": "目标公司", "objective": "形成投资研究报告"})
    section_index = _read_json(tasks_dir / f"{task_id}-section_packages.json", {"task_id": task_id, "packages": []})
    debate_review = _read_json(tasks_dir / f"{task_id}-debate_review.json", {"verdict": "REWRITE_REQUIRED", "issues": [{"code": "MISSING_DEBATE_REVIEW"}]})

    result = assemble_final_report(research_plan, section_index, debate_review)
    md_path = tasks_dir / f"{task_id}-final_report.md"
    if result.get("ok"):
        md_path.write_text(result["markdown"], encoding="utf-8")
        result["markdown_path"] = str(md_path)
    else:
        result["markdown_path"] = ""

    output = tasks_dir / f"{task_id}-final_assembly.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(output)
