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


def _coerce_question_items(items: Any, prefix: str,
                           owner: str, fact_keys: list[str]) -> list[dict[str, Any]]:
    """把字符串形式的 question 列表转成 dispatch 契约要求的 dict 列表。

    v3.2+ (2026-08-06 下午, TASK-20260806-002)：phase04 子代理曾把
    core_questions/strategic_questions 写成纯字符串列表，validate 在
    question.get(...) 处崩溃（'str' object has no attribute 'get'）。
    这里按 _strategic_question 同一 schema 补齐 owner_section/required_fact_keys。
    """
    if not isinstance(items, list):
        return []
    converted: list[dict[str, Any]] = []
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            converted.append(item)
            continue
        converted.append(_strategic_question(
            f"{prefix}{idx}",
            str(item),
            owner,
            list(fact_keys),
            "String question normalized to the dispatch-time question schema.",
        ))
    return converted


def normalize_research_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy or hand-written research plans to the dispatch contract.

    Older plan producers used ``status`` instead of ``plan_status`` and sometimes
    emitted ``core_questions`` / ``strategic_questions`` as a list of strings.
    The dispatch gate needs the newer ``plan_status`` field plus section-owned
    question objects (``owner_section`` + ``required_fact_keys``).

    v3.2+ (2026-08-06 下午, TASK-20260806-002)：之前只归一 strategic_questions，
    core_questions 仍是字符串列表导致 validate 在 question.get(...) 崩溃
    （'str' object has no attribute 'get'）。两个列表统一用 _coerce_question_items 处理。
    """
    normalized = dict(plan or {})
    if not normalized.get("plan_status") and normalized.get("status"):
        normalized["plan_status"] = normalized.get("status")

    core_questions = normalized.get("core_questions") or []
    # 默认 owner/fact_keys：从 core_questions 中已有的 dict 提取；
    # 否则 owner 取 plan.section_requirements 里第一个真实 section（避免 owner_section_invalid），
    # fact_keys 取 plan 中实际定义过的 fact_keys（避免 undefined_fact_keys），最后才回退硬编码。
    section_keys = list((normalized.get("section_requirements") or {}).keys())
    default_owner = section_keys[0] if section_keys else "step1_industry"
    defined_fact_keys = [
        item.get("fact_key")
        for item in (normalized.get("fact_requirements") or [])
        if isinstance(item, dict) and item.get("fact_key")
    ]
    default_fact_keys = defined_fact_keys[:3] or ["revenue_trend", "growth_rate", "risk_triggers"]
    first_dict = next((q for q in core_questions if isinstance(q, dict)), None)
    if first_dict is not None:
        default_owner = first_dict.get("owner_section") or default_owner
        first_fk = first_dict.get("required_fact_keys")
        if first_fk:
            default_fact_keys = first_fk

    if core_questions and any(not isinstance(item, dict) for item in core_questions):
        normalized["core_questions"] = _coerce_question_items(
            core_questions, "CQ", default_owner, list(default_fact_keys))

    strategic_questions = normalized.get("strategic_questions") or []
    if strategic_questions and any(not isinstance(item, dict) for item in strategic_questions):
        normalized["strategic_questions"] = _coerce_question_items(
            strategic_questions, "SQ", default_owner, list(default_fact_keys))

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
