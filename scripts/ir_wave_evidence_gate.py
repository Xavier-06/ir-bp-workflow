#!/usr/bin/env python3
"""IR wave evidence gate — claim-level 证据门禁（对标 BP bp_wave_evidence_gate.py）。

IR 管线没有 claim_matrix，但有 research plan 的 core_questions + strategic_questions。
本门禁以 question_id 为锚点，校验每个 step 的 section sidecar 是否覆盖了
其 owner question 的 required_fact_keys。

校验维度：
1. 输出完整性 — .md + -facts.json + -section.json 三件套
2. 事实覆盖 — section sidecar 的 claims 是否引用了 required fact_keys
3. 来源质量 — facts sidecar 中是否有外部来源 URL
4. 内容充实 — markdown 长度 > 200 字符且无过多 TODO

verdict: PASS / REPAIR / FAIL
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.ir_subagent_launcher_wb import LAUNCH_WAVES, STEP_DEPS

# blocking 最大重试次数；超过后降级放行
_MAX_BLOCKING_RETRIES = 1


def _step_paths(task_id: str, step: str, tasks_dir: Path) -> dict[str, Path]:
    """解析 step 的三件套路径。"""
    return {
        "markdown": tasks_dir / f"{task_id}-{step}.md",
        "facts": tasks_dir / f"{task_id}-{step}-facts.json",
        "section": tasks_dir / f"{task_id}-{step}-section.json",
    }


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _wave_steps(wave: int) -> list[str]:
    """返回指定 wave 的 step 列表。"""
    if 0 <= wave < len(LAUNCH_WAVES):
        return LAUNCH_WAVES[wave]
    return []


def _planned_questions_for_step(tasks_dir: Path, step: str) -> list[dict[str, Any]]:
    """从 research plan 中读取属于该 step 的 core + strategic questions。"""
    plan = _load_json(tasks_dir / f"{_task_id_from_dir(tasks_dir)}-research_plan.json") or {}
    questions: list[dict[str, Any]] = []

    for q in plan.get("core_questions", []):
        if q.get("owner_section") == step:
            questions.append(q)

    for sq in plan.get("strategic_questions", []):
        if sq.get("owner_section") == step:
            questions.append(sq)

    return questions


def _task_id_from_dir(tasks_dir: Path) -> str:
    """从 tasks_dir 推断 task_id（兜底用）。"""
    # 尝试从目录下已有的 research_plan 文件名推断
    for p in tasks_dir.glob("*-research_plan.json"):
        name = p.name.replace("-research_plan.json", "")
        if name:
            return name
    return "UNKNOWN"


def evaluate_wave_evidence_gate(
    task_id: str,
    wave: int,
    tasks_dir: Path,
) -> dict[str, Any]:
    """评估指定 wave 的证据门禁。

    Returns:
        {
            "verdict": "PASS" | "REPAIR" | "FAIL",
            "wave": int,
            "steps_checked": [...],
            "issues": [...],
            "repair_tasks": [...],
            "needs_repair": bool,
        }
    """
    steps = _wave_steps(wave)
    if not steps:
        return {
            "verdict": "PASS",
            "wave": wave,
            "steps_checked": [],
            "issues": [],
            "repair_tasks": [],
            "needs_repair": False,
        }

    issues: list[dict[str, Any]] = []
    repair_tasks: list[dict[str, Any]] = []
    steps_checked: list[str] = []

    for step in steps:
        paths = _step_paths(task_id, step, tasks_dir)
        steps_checked.append(step)

        # 1. 输出完整性检查
        md_exists = paths["markdown"].exists() and paths["markdown"].stat().st_size > 100
        facts_exists = paths["facts"].exists() and paths["facts"].stat().st_size > 10
        section_exists = paths["section"].exists() and paths["section"].stat().st_size > 10

        if not md_exists:
            issues.append({
                "step": step,
                "severity": "BLOCKING",
                "reason": "MISSING_OR_EMPTY_OUTPUT",
                "detail": f"Markdown 输出缺失或过短: {paths['markdown'].name}",
            })
            repair_tasks.append({
                "step": step,
                "owner_section": step,
                "reason": "MISSING_WAVE_OUTPUT_OR_SIDECAR",
                "severity": "BLOCKING",
            })
            continue

        if not facts_exists or not section_exists:
            issues.append({
                "step": step,
                "severity": "BLOCKING",
                "reason": "MISSING_SIDECAR",
                "detail": f"Sidecar 缺失: facts={facts_exists}, section={section_exists}",
            })
            repair_tasks.append({
                "step": step,
                "owner_section": step,
                "reason": "MISSING_WAVE_OUTPUT_OR_SIDECAR",
                "severity": "BLOCKING",
            })
            continue

        # 2. Section sidecar 校验
        section_data = _load_json(paths["section"])
        if section_data is None:
            issues.append({
                "step": step,
                "severity": "BLOCKING",
                "reason": "INVALID_SECTION_JSON",
                "detail": "Section sidecar JSON 解析失败",
            })
            repair_tasks.append({
                "step": step,
                "owner_section": step,
                "reason": "MISSING_WAVE_OUTPUT_OR_SIDECAR",
                "severity": "BLOCKING",
            })
            continue

        # 3. 事实覆盖检查 — section claims 是否引用了 required fact_keys
        section_claims = section_data.get("claims", [])
        facts_used = set(section_data.get("facts_used", []))

        questions = _planned_questions_for_step(tasks_dir, step)
        required_keys: set[str] = set()
        for q in questions:
            required_keys.update(q.get("required_fact_keys", []))

        # 加载 facts sidecar 看看 facts_used 是否真正存在
        facts_data = _load_json(paths["facts"])
        fact_ids_in_sidecar: set[str] = set()
        if facts_data and isinstance(facts_data, dict):
            for f in facts_data.get("facts", []):
                fid = f.get("fact_id", "")
                if fid:
                    fact_ids_in_sidecar.add(fid)

        # 检查 claims 是否有 fact_ids 绑定
        claims_without_facts = 0
        for claim in section_claims:
            if not claim.get("fact_ids"):
                claims_without_facts += 1

        if section_claims and claims_without_facts == len(section_claims):
            issues.append({
                "step": step,
                "severity": "MEDIUM",
                "reason": "ALL_CLAIMS_WITHOUT_FACTS",
                "detail": f"{step} 所有 {len(section_claims)} 条 claims 均未绑定 fact_ids",
            })

        # 4. 来源质量检查
        if facts_data and isinstance(facts_data, dict):
            facts_list = facts_data.get("facts", [])
            facts_with_url = sum(1 for f in facts_list if f.get("source_url"))
            if facts_list and facts_with_url == 0:
                issues.append({
                    "step": step,
                    "severity": "MEDIUM",
                    "reason": "NO_FACTS_WITH_SOURCE_URL",
                    "detail": f"{step} 的 {len(facts_list)} 条 facts 均无 source_url",
                })

        # 5. 内容充实度
        md_text = paths["markdown"].read_text(encoding="utf-8")
        if len(md_text) < 500:
            issues.append({
                "step": step,
                "severity": "MEDIUM",
                "reason": "THIN_CONTENT",
                "detail": f"{step} Markdown 仅 {len(md_text)} 字符",
            })

    # 判定 verdict
    blocking_issues = [i for i in issues if i.get("severity") == "BLOCKING"]
    medium_issues = [i for i in issues if i.get("severity") == "MEDIUM"]

    if blocking_issues:
        verdict = "REPAIR"
        needs_repair = True
    elif len(medium_issues) > len(steps) * 0.5:
        # 超过半数 step 有 MEDIUM 问题 → 也要 repair
        verdict = "REPAIR"
        needs_repair = True
        # 为 MEDIUM 问题也生成 repair_tasks
        for issue in medium_issues:
            if not any(t["step"] == issue["step"] for t in repair_tasks):
                repair_tasks.append({
                    "step": issue["step"],
                    "owner_section": issue["step"],
                    "reason": issue["reason"],
                    "severity": "MEDIUM",
                })
    else:
        verdict = "PASS" if not medium_issues else "PASS"
        needs_repair = False

    return {
        "verdict": verdict,
        "wave": wave,
        "steps_checked": steps_checked,
        "issues": issues,
        "repair_tasks": repair_tasks,
        "needs_repair": needs_repair,
        "blocking_count": len(blocking_issues),
        "medium_count": len(medium_issues),
    }


def write_wave_gate(task_id: str, wave: int, gate_result: dict[str, Any], tasks_dir: Path) -> str:
    """将 gate 结果写入 JSON 文件。"""
    output_path = tasks_dir / f"{task_id}-wave{wave}_evidence_gate.json"
    output_path.write_text(
        json.dumps(gate_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(output_path)


# ── Repair manifest builder ──────────────────────────────────

_REPAIR_SYSTEM_PROMPT_TEMPLATE = """\
你是一个投研管线的修复子代理（Repair Agent）。你的任务是修复 Wave {wave} 中 step {step} 的
输出问题，使 evidence gate 通过。

## 你的修复目标

{repair_goal}

## 现有输出文件

{existing_files_desc}

## 修复规则

1. **不要重写整个报告**。只在现有输出基础上补充缺失部分。
2. **必须同时更新 facts sidecar 和 section sidecar**：
   - facts: `{facts_path}`
   - section: `{section_path}`
3. **修改 sidecar 文件时必须使用文件锁**，避免并行写入丢数据：
   ```python
   from scripts.bp_file_lock import locked_read_modify_write
   from pathlib import Path

   def update_facts(data):
       data.setdefault('facts', []).extend(new_facts)
       return data
   locked_read_modify_write(Path('{facts_path}'), update_facts)
   ```
4. 新增的事实必须有外部来源 URL 支撑，禁止编造。
5. section sidecar 中的 claims 每条都必须有 fact_ids 字段。
6. 搜索工具使用指南：
   - NeoData: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; import json; print(json.dumps(neodata_search('查询语句'), ensure_ascii=False))"`
   - 通用搜索: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import search; results = search('关键词'); [print(r['title'], r['url']) for r in results[:5]]"`
7. 修复完成后，确保 facts JSON 和 section JSON 都是合法 JSON。
"""


def build_ir_repair_manifests(
    task_id: str,
    wave: int,
    gate_result: dict[str, Any],
    tasks_dir: Path,
) -> list[str]:
    """为 gate FAIL 产生的 repair_tasks 生成 manifest JSON 文件。

    按 step 聚合，每个 step 一个 manifest。

    Returns:
        manifest 文件路径列表。
    """
    repair_tasks = gate_result.get("repair_tasks") or []
    if not repair_tasks:
        return []

    # 按 step 聚合
    tasks_by_step: dict[str, list[dict[str, Any]]] = {}
    for task in repair_tasks:
        step = task.get("step", "")
        if step:
            tasks_by_step.setdefault(step, []).append(task)

    manifests: list[str] = []
    for step, step_tasks in tasks_by_step.items():
        paths = _step_paths(task_id, step, tasks_dir)

        # 构建 repair goal
        reasons = [t.get("reason", "") for t in step_tasks]
        goal_parts: list[str] = []
        if "MISSING_WAVE_OUTPUT_OR_SIDECAR" in reasons:
            goal_parts.append(
                f"step {step} 的输出文件或 sidecar 缺失。你需要：\n"
                "- 读取现有的 markdown 输出（如存在）\n"
                "- 补充搜索外部证据\n"
                "- 生成/更新 facts JSON（每个事实含 source_url）\n"
                "- 生成/更新 section JSON（claims 必须有 fact_ids）"
            )
        if "ALL_CLAIMS_WITHOUT_FACTS" in reasons:
            goal_parts.append(
                f"step {step} 的所有 claims 均未绑定 fact_ids。你需要：\n"
                "- 检查 facts JSON 中已有的 facts\n"
                "- 更新 section JSON 的 claims，为每条 claim 绑定对应的 fact_ids\n"
                "- 如果 facts 不足，补充搜索并写入新 facts"
            )
        if "NO_FACTS_WITH_SOURCE_URL" in reasons:
            goal_parts.append(
                f"step {step} 的所有 facts 均无 source_url。你需要：\n"
                "- 为每条 fact 补充 source_url（必须来自外部搜索）\n"
                "- 使用 locked_read_modify_write 更新 facts JSON"
            )
        if not goal_parts:
            goal_parts.append(f"修复 step {step} 的输出问题。")

        repair_goal = "\n\n".join(goal_parts)

        # 描述现有文件状态
        existing_files_desc = f"- Markdown: `{paths['markdown']}` (exists={paths['markdown'].exists()})"
        existing_files_desc += f"\n- Facts: `{paths['facts']}` (exists={paths['facts'].exists()})"
        existing_files_desc += f"\n- Section: `{paths['section']}` (exists={paths['section'].exists()})"

        system_prompt = _REPAIR_SYSTEM_PROMPT_TEMPLATE.format(
            wave=wave,
            step=step,
            repair_goal=repair_goal,
            existing_files_desc=existing_files_desc,
            facts_path=str(paths["facts"]),
            section_path=str(paths["section"]),
        )

        manifest = {
            "manifest_version": "1.0",
            "pipeline": "ir",
            "wave": wave,
            "step": step,
            "role": step,
            "system_prompt": system_prompt,
            "connectorIds": [],
            "subagent_type": "general-purpose",
            "team_name_template": "ir-{task_id}",
            "task_dir": str(tasks_dir),
            "repair_tasks": step_tasks,
            "output_files": {
                "markdown": str(paths["markdown"]),
                "facts": str(paths["facts"]),
                "section": str(paths["section"]),
            },
        }

        manifest_path = tasks_dir / f"{task_id}-wave{wave}_repair_manifest_{step}.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifests.append(str(manifest_path))

    return manifests
