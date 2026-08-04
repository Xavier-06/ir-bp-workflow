#!/usr/bin/env python3
"""IR research-plan contract utilities.

v3.2 (2026-08-04): 骨架生成已删除 — research plan 由 phase04 子代理全权生成
（指令见 instruction_store_ir/ir_research_plan.md），脚本不再兜底生成计划。
本模块只保留 dispatch 契约工具：schema 归一化（normalize）、就绪校验（validate）、
读写路径（research_plan_path / load_research_plan）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"


def _strategic_question(
    question_id: str,
    question: str,
    owner_section: str,
    required_fact_keys: list[str],
    decision_relevance: str,
    priority: str = "high",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": question,
        "priority": priority,
        "owner_section": owner_section,
        "required_fact_keys": required_fact_keys,
        "decision_relevance": decision_relevance,
    }


def normalize_research_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy or hand-written research plans to the dispatch contract.

    Older plan producers used ``status`` instead of ``plan_status`` and sometimes
    emitted ``strategic_questions`` as a list of strings. The dispatch gate needs
    the newer ``plan_status`` field plus section-owned question objects.
    """
    normalized = dict(plan or {})
    if not normalized.get("plan_status") and normalized.get("status"):
        normalized["plan_status"] = normalized.get("status")

    strategic_questions = normalized.get("strategic_questions") or []
    if strategic_questions and any(not isinstance(item, dict) for item in strategic_questions):
        core_questions = normalized.get("core_questions") or []
        default_owner = "step7_insight"
        default_fact_keys = ["revenue_trend", "growth_rate", "risk_triggers"]
        if core_questions and isinstance(core_questions[0], dict):
            default_owner = core_questions[0].get("owner_section") or default_owner
            default_fact_keys = core_questions[0].get("required_fact_keys") or default_fact_keys

        converted: list[dict[str, Any]] = []
        for idx, item in enumerate(strategic_questions, 1):
            if isinstance(item, dict):
                converted.append(item)
                continue
            converted.append(_strategic_question(
                f"SQ{idx}",
                str(item),
                default_owner,
                list(default_fact_keys),
                "Legacy string question normalized to the dispatch-time strategic question schema.",
            ))
        normalized["strategic_questions"] = converted

    return normalized


def validate_research_plan_ready(plan: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_research_plan_contract(plan)
    errors: list[str] = []
    required_top_level = [
        "schema_version",
        "core_questions",
        "section_requirements",
        "fact_requirements",
        "coverage_matrix",
    ]
    for key in required_top_level:
        if not plan.get(key):
            errors.append(f"{key}_missing")

    if plan.get("plan_status") != "ready":
        errors.append("plan_status_not_ready")

    strategic_questions = plan.get("strategic_questions") or []
    if not strategic_questions:
        errors.append("strategic_questions_missing")

    section_keys = set((plan.get("section_requirements") or {}).keys())
    fact_keys = {item.get("fact_key") for item in plan.get("fact_requirements", []) if item.get("fact_key")}
    referenced_fact_keys: set[str] = set()
    for collection_name in ("core_questions", "strategic_questions"):
        for question in plan.get(collection_name, []) or []:
            owner_section = question.get("owner_section")
            if not owner_section:
                errors.append(f"{collection_name}_owner_section_missing")
            elif owner_section not in section_keys:
                errors.append(f"{collection_name}_owner_section_invalid")
            if not question.get("required_fact_keys"):
                errors.append(f"{collection_name}_required_fact_keys_missing")
            referenced_fact_keys.update(question.get("required_fact_keys", []) or [])
    for section in (plan.get("section_requirements") or {}).values():
        referenced_fact_keys.update(section.get("required_fact_keys", []) or [])
    for coverage in (plan.get("coverage_matrix") or {}).values():
        owner = coverage.get("owner")
        if owner and owner not in section_keys:
            errors.append("coverage_owner_invalid")
        referenced_fact_keys.update(coverage.get("required_fact_keys", []) or [])

    undefined = sorted(k for k in referenced_fact_keys if k not in fact_keys)
    if undefined:
        errors.append(f"undefined_fact_keys:{','.join(undefined)}")

    return {"ready": not errors, "errors": sorted(set(errors))}


def research_plan_path(task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return Path(tasks_dir) / f"{task_id}-research_plan.json"


def load_research_plan(task_id: str, tasks_dir: Path = TASKS_DIR) -> dict[str, Any] | None:
    path = research_plan_path(task_id, tasks_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
