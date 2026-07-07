#!/usr/bin/env python3
"""IR Shared State — 跨 Wave 上下文传递（对标 BP bp_shared_page_builder.py，轻量版）。

BP 有 claim_matrix / claim_inventory / stage_tier 三层结构，IR 没有这些。
IR 版本聚焦三个核心功能：
1. fact_summary: 已收集的 fact 概览（数量、按 step 分布、来源层级分布）
2. data_gaps: 各 step 标记的数据缺口
3. step_progress: 哪些 step 已完成、质量评分

输出两个文件：
- {task_id}-shared_state.json: 机器可读状态
- {task_id}-shared_state_page.md: 人类/子代理可读摘要
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.ir_subagent_launcher_wb import LAUNCH_WAVES, STEP_DEPS, step_output_path
from scripts.bp_file_lock import atomic_write


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_ir_shared_state(
    task_id: str,
    tasks_dir: Path,
    after_wave: int = 0,
    entity: str = "",
) -> dict[str, Any]:
    """构建 IR 管线的跨 Wave 共享状态。

    Args:
        task_id: 任务 ID
        tasks_dir: data/tasks/ 目录
        after_wave: 刚完成的 wave 编号（0-based）
        entity: 标的名称
    """
    tasks_dir = Path(tasks_dir)

    # 1. 从 fact_store 提取 fact 摘要
    fact_store = _load_json(tasks_dir / f"{task_id}-fact_store.json") or {}
    facts = fact_store.get("facts", [])
    fact_count = len(facts)
    facts_by_step: dict[str, int] = {}
    facts_by_tier: dict[str, int] = {}
    for f in facts:
        step = f.get("step", "unknown")
        tier = f.get("source_tier", "unknown")
        facts_by_step[step] = facts_by_step.get(step, 0) + 1
        facts_by_tier[tier] = facts_by_tier.get(tier, 0) + 1

    # 2. 从各 step 的 section sidecar 提取 data_gaps 和 claims 概览
    data_gaps: list[dict[str, Any]] = []
    claims_summary: list[dict[str, Any]] = []
    step_progress: list[dict[str, Any]] = []

    for wave_idx in range(min(after_wave + 1, len(LAUNCH_WAVES))):
        for step in LAUNCH_WAVES[wave_idx]:
            md_path = step_output_path(task_id, step)
            facts_path = tasks_dir / f"{task_id}-{step}-facts.json"
            section_path = tasks_dir / f"{task_id}-{step}-section.json"

            step_info = {
                "step": step,
                "wave": wave_idx,
                "md_exists": md_path.exists() and md_path.stat().st_size > 100,
                "facts_exists": facts_path.exists(),
                "section_exists": section_path.exists(),
            }

            # 读 section sidecar
            section = _load_json(section_path)
            if section:
                step_info["claims_count"] = len(section.get("claims", []))
                step_info["counter_evidence"] = section.get("counter_evidence", [])[:3]

                for gap in section.get("data_gaps", []):
                    if isinstance(gap, str):
                        data_gaps.append({"step": step, "gap": gap})
                    elif isinstance(gap, dict):
                        data_gaps.append({"step": step, **gap})

                for claim in section.get("claims", [])[:5]:
                    claims_summary.append({
                        "step": step,
                        "claim": claim.get("claim", "")[:100],
                        "confidence": claim.get("confidence", "unknown"),
                        "fact_ids": claim.get("fact_ids", []),
                    })

            step_progress.append(step_info)

    # 3. 读取 research plan 的 strategic_questions（如果已 enrichment）
    plan = _load_json(tasks_dir / f"{task_id}-research_plan.json") or {}
    strategic_questions = plan.get("strategic_questions", [])

    state = {
        "schema_version": "ir_shared_state.v1",
        "task_id": task_id,
        "entity": entity or plan.get("entity", ""),
        "after_wave": after_wave,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fact_summary": {
            "total": fact_count,
            "by_step": facts_by_step,
            "by_source_tier": facts_by_tier,
        },
        "data_gaps": data_gaps[:20],
        "claims_highlights": claims_summary[:30],
        "step_progress": step_progress,
        "strategic_questions": [
            {"question_id": sq.get("question_id", ""), "question": sq.get("question", "")[:100]}
            for sq in strategic_questions[:10]
        ],
        "wave_history": [{
            "wave": after_wave,
            "completed_steps": [
                s["step"] for s in step_progress if s.get("md_exists")
            ],
        }],
    }

    return state


def write_ir_shared_state(
    task_id: str,
    tasks_dir: Path,
    after_wave: int = 0,
    entity: str = "",
) -> str:
    """构建并写入 shared_state.json + shared_state_page.md。

    Returns:
        shared_state.json 路径
    """
    tasks_dir = Path(tasks_dir)
    state = build_ir_shared_state(task_id, tasks_dir, after_wave, entity)

    # 写 JSON
    json_path = tasks_dir / f"{task_id}-shared_state.json"
    atomic_write(json_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    # 写 Markdown 摘要页
    page_path = tasks_dir / f"{task_id}-shared_state_page.md"
    page = _render_shared_state_page(state)
    atomic_write(page_path, page)

    return str(json_path)


def _render_shared_state_page(state: dict[str, Any]) -> str:
    """渲染人类可读的共享状态摘要页。"""
    lines = [
        f"# IR 共享状态 — Wave {state.get('after_wave', '?')} 后",
        f"",
        f"**标的**: {state.get('entity', '?')}  ",
        f"**更新时间**: {state.get('updated_at', '?')}",
        f"",
        f"## 事实收集概况",
        f"",
        f"总 fact 数: **{state['fact_summary']['total']}**",
        f"",
        f"| Step | Facts 数 |",
        f"|------|---------|",
    ]
    for step, count in sorted(state["fact_summary"]["by_step"].items()):
        lines.append(f"| {step} | {count} |")

    lines.extend([
        f"",
        f"### 来源层级分布",
        f"",
        f"| Tier | 数量 |",
        f"|------|------|",
    ])
    for tier, count in sorted(state["fact_summary"]["by_source_tier"].items()):
        lines.append(f"| {tier} | {count} |")

    # Step 进度
    lines.extend([
        f"",
        f"## Step 完成进度",
        f"",
        f"| Step | Wave | MD | Facts | Section | Claims |",
        f"|------|------|----|-------|---------|--------|",
    ])
    for sp in state.get("step_progress", []):
        md = "✅" if sp.get("md_exists") else "❌"
        fc = "✅" if sp.get("facts_exists") else "❌"
        sc = "✅" if sp.get("section_exists") else "❌"
        claims = sp.get("claims_count", "-")
        lines.append(f"| {sp['step']} | W{sp['wave']} | {md} | {fc} | {sc} | {claims} |")

    # Data Gaps
    gaps = state.get("data_gaps", [])
    if gaps:
        lines.extend([
            f"",
            f"## 数据缺口 ({len(gaps)} 项)",
            f"",
        ])
        for g in gaps[:10]:
            lines.append(f"- **{g.get('step', '?')}**: {g.get('gap', '?')}")

    # Strategic Questions
    sqs = state.get("strategic_questions", [])
    if sqs:
        lines.extend([
            f"",
            f"## 战略问题",
            f"",
        ])
        for sq in sqs:
            lines.append(f"- **{sq['question_id']}**: {sq['question']}")

    return "\n".join(lines) + "\n"
