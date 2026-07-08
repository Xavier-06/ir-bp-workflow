#!/usr/bin/env python3
"""IC Research Planner — 研究计划骨架生成 + enrichment 合并。

设计原则：脚本只提供空骨架（纯结构外壳），所有研究内容由 LLM 在 enrichment 阶段填充。

不包含任何硬编码的 core_questions、claim_matrix、fact_requirements 或 step 激活列表。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


# ── 空骨架生成 ──────────────────────────────────────────

def build_empty_skeleton(
    task_id: str,
    entity: str,
    topic_metadata: dict[str, Any],
) -> dict[str, Any]:
    """生成空的 IC research plan 骨架。

    只含 schema 声明和元数据引用，所有研究内容字段为空。
    LLM 在 enrichment 阶段填充所有字段。
    """
    return {
        "schema_version": "ic_research_plan.v4",
        "task_id": task_id,
        "entity": entity,
        "topic_metadata": topic_metadata,
        "plan_status": "pending_enrichment",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # ── 以下字段全空，由 LLM 填充 ──
        "core_questions": [],
        "claim_matrix": [],
        "fact_requirements": [],
        "activated_steps": [],
        "deactivated_steps": [],
        "deactivation_reasons": {},
        "search_keywords": {},
    }


# ── Enrichment 合并 ─────────────────────────────────────

REQUIRED_TOP_KEYS = {
    "core_questions", "claim_matrix", "fact_requirements",
    "activated_steps", "deactivated_steps",
}


def validate_enrichment(enrichment: dict[str, Any]) -> list[str]:
    """校验 LLM 输出的 enrichment 是否符合 schema 约束。

    只检查结构完整性，不检查内容"对不对"。
    """
    errors: list[str] = []

    # 必需顶层 key
    for key in REQUIRED_TOP_KEYS:
        if key not in enrichment:
            errors.append(f"缺少必需字段: {key}")

    # core_questions 校验
    for i, q in enumerate(enrichment.get("core_questions", [])):
        for required in ("id", "question", "owner_step", "priority"):
            if required not in q:
                errors.append(f"core_questions[{i}] 缺少字段: {required}")

    # claim_matrix 校验
    for i, c in enumerate(enrichment.get("claim_matrix", [])):
        for required in ("claim_id", "claim", "owner_step", "priority"):
            if required not in c:
                errors.append(f"claim_matrix[{i}] 缺少字段: {required}")

    # fact_requirements 校验
    for i, f in enumerate(enrichment.get("fact_requirements", [])):
        for required in ("fact_key", "label"):
            if required not in f:
                errors.append(f"fact_requirements[{i}] 缺少字段: {required}")

    return errors


def apply_enrichment(
    skeleton: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """将 LLM 输出的 enrichment 合并到骨架。

    不做任何内容修改——LLM 说什么就是什么。
    只校验结构完整性。
    """
    errors = validate_enrichment(enrichment)
    if errors:
        print(f"  ⚠️ [ic_research_planner] enrichment 校验发现 {len(errors)} 个问题:", flush=True)
        for e in errors[:5]:
            print(f"    - {e}", flush=True)

    # 直接合并
    for key in REQUIRED_TOP_KEYS:
        skeleton[key] = enrichment.get(key, [])

    # 更新状态
    skeleton["plan_status"] = "ready" if not errors else "ready_with_warnings"
    skeleton["merged_at"] = datetime.now().isoformat(timespec="seconds")

    return skeleton


# ── IO helpers ──────────────────────────────────────────

def write_research_plan(
    output_path: Path,
    plan: dict[str, Any],
) -> str:
    """写入 research plan 到文件。"""
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(output_path)


def load_skeleton(path: Path) -> dict[str, Any]:
    """读取 skeleton 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_enrichment(path: Path) -> dict[str, Any]:
    """读取 enrichment 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))
