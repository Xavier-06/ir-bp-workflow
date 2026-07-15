#!/usr/bin/env python3
"""BP wave evidence gate."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime.profiles.bp_constants import BP_WAVE_ROLES as WAVE_ROLES
from runtime.profiles.bp_constants import BP_TYC_CONNECTOR_IDS
from scripts.bp_utils import load_json, read_attempt_count

# blocking_claims 最大硬卡次数；超过后降级为 WARN 放行
_MAX_BLOCKING_RETRIES = 1


def _resolve_path(filename: str, *search_dirs: Path) -> Path:
    """在多个目录中查找文件，返回第一个存在的路径；都不存在时返回第一个目录下的路径。"""
    for d in search_dirs:
        p = d / filename
        if p.exists():
            return p
    return search_dirs[0] / filename


def _role_paths(task_dir: Path, role: str, outputs_dir: Path | None = None) -> dict[str, Path]:
    """解析 role 的三件套路径（markdown / facts / section）。

    子代理实际输出到 outputs_dir，但历史代码只查 task_dir。
    现在 outputs_dir 优先，task_dir fallback，与 _role_outputs_complete() 行为一致。
    """
    slug = role.replace("bp_", "")
    search_dirs = [outputs_dir, task_dir] if outputs_dir else [task_dir]
    return {
        "markdown": _resolve_path(f"bp_phase2_{slug}.md", *search_dirs),
        "facts": _resolve_path(f"bp_phase2_{slug}-facts.json", *search_dirs),
        "section": _resolve_path(f"bp_phase2_{slug}-section.json", *search_dirs),
    }


def _planned_claims(task_dir: Path, roles: list[str]) -> list[dict[str, Any]]:
    plan = load_json(task_dir / "bp_research_plan.json", {})
    claims = plan.get("claim_matrix") or []
    return [
        claim for claim in claims
        if isinstance(claim, dict) and claim.get("owner_section") in roles
    ]



def _critical_claims(task_dir: Path, roles: list[str]) -> list[dict[str, Any]]:
    return [
        claim for claim in _planned_claims(task_dir, roles)
        if str(claim.get("priority") or "").lower() in {"critical", "high"}
    ]



def _expected_roles(task_dir: Path, wave: int, outputs_dir: Path | None = None) -> list[str]:
    wave_roles = WAVE_ROLES.get(wave, [])
    planned_roles = []
    for claim in _planned_claims(task_dir, wave_roles):
        role = str(claim.get("owner_section") or "")
        if role and role not in planned_roles:
            planned_roles.append(role)
    if planned_roles:
        return planned_roles
    detected = []
    for role in wave_roles:
        if _role_paths(task_dir, role, outputs_dir)["markdown"].exists():
            detected.append(role)
    return detected or wave_roles


# Roles that should produce comparison/benchmark tables
_COMPARISON_ROLES = {"bp_tech_ip_moat", "bp_competition_positioning", "bp_market_supply_chain"}

# v4.5: 非 claim-fact 模式的角色 —— 假说/共识/催化剂不产出标准 claim，跳过 claim 覆盖率检查
# 只做基础检查（文件存在 + 字数达标），不参与 evidence gate 的 claim 验证逻辑
_NON_CLAIM_ROLES = {"bp_investment_hypothesis", "bp_consensus_challenge", "bp_catalyst", "bp_industry_research"}


def _comparison_table_check(role: str, markdown_path: Path) -> dict[str, Any]:
    """Soft check: does the role output contain comparison/benchmark tables?

    Only applicable to tech, competition, and market roles.
    Returns a result dict but never blocks the gate — just adds visibility.
    """
    if role not in _COMPARISON_ROLES:
        return {"passed": True, "reason": "not_applicable"}
    if not markdown_path.exists():
        return {"passed": False, "reason": "missing_markdown", "table_count": 0}
    text = markdown_path.read_text(encoding="utf-8")
    # Count markdown tables by looking for separator rows (|---|---|...)
    # Each separator row indicates one table
    sep_pattern = r'^\|[\s\-:]+\|'
    tables = re.findall(sep_pattern, text, re.MULTILINE)
    comparison_keywords = ("对比", "对比表", "竞品", "技术路线", "参数", "价格",
                           "benchmark", "comparison", "门槛", "替代方案")
    has_comparison = any(kw in text for kw in comparison_keywords) and len(tables) >= 2
    return {
        "passed": has_comparison,
        "table_count": len(tables),
        "reason": "comparison_tables_present" if has_comparison else "missing_comparison_tables",
    }


def _role_slug(role: str) -> str:
    return role.replace("bp_", "")


# ── Repair system prompt builder ──────────────────────────────────

_REPAIR_SYSTEM_PROMPT_TEMPLATE = """\
你是一个 BP 尽调管线的修复子代理（Repair Agent）。你的任务是修复 Wave {wave} 中角色 {role} 的
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
5. section sidecar 中的 `claim_ids_covered` 字段必须包含本次修复覆盖的所有 claim ID。
6. 搜索工具使用指南与正常维度子代理相同（search_gateway / TYC MCP / yfinance）。
7. 修复完成后，确保 facts JSON 和 section JSON 都是合法 JSON，能被管线直接读取。
"""


def _build_repair_goal(task: dict[str, Any], role: str) -> str:
    """为单个 repair task 生成人类可读的修复目标描述。"""
    reason = task.get("reason", "")
    claim_id = task.get("claim_id", "")
    if reason == "MISSING_WAVE_OUTPUT_OR_SIDECAR":
        return (
            f"角色 {role} 的 sidecar 文件缺失。你需要：\n"
            "- 读取现有的 markdown 输出（如存在）\n"
            "- 补充搜索外部证据\n"
            "- 生成/更新 facts JSON（每个事实含 source_url）\n"
            "- 生成/更新 section JSON（含 claim_ids_covered）"
        )
    elif reason == "CRITICAL_CLAIM_NOT_ADDRESSED":
        # 从 research_plan 中读取 claim 详情
        return (
            f"Critical claim `{claim_id}` 属于角色 {role} 但未被任何输出覆盖。你需要：\n"
            "- 针对该 claim 进行外部搜索验证\n"
            "- 将验证结果写入 facts JSON\n"
            "- 更新 section JSON 的 `claim_ids_covered` 字段，确保包含 `{claim_id}`\n"
            "- 如有必要，在 markdown 输出中补充相关段落"
        )
    return f"修复 {reason} for role {role}"


def _build_repair_goal_for_role(
    role: str,
    role_tasks: list[dict[str, Any]],
    claim_map: dict[str, dict],
) -> str:
    """为 role 聚合生成修复目标描述，列出该 role 所有待修复的 claims。"""
    claim_lines: list[str] = []
    has_missing_sidecar = False
    for task in role_tasks:
        reason = task.get("reason", "")
        claim_id = task.get("claim_id", "")
        if reason == "MISSING_WAVE_OUTPUT_OR_SIDECAR":
            has_missing_sidecar = True
        elif reason == "CRITICAL_CLAIM_NOT_ADDRESSED" and claim_id:
            detail = claim_map.get(claim_id, {})
            claim_text = detail.get("claim", "(无描述)")
            claim_lines.append(f"- claim `{claim_id}`: {claim_text}")

    parts: list[str] = []
    if has_missing_sidecar:
        parts.append(
            f"角色 {role} 的 sidecar 文件缺失。你需要：\n"
            "- 读取现有的 markdown 输出（如存在）\n"
            "- 补充搜索外部证据\n"
            "- 生成/更新 facts JSON（每个事实含 source_url）\n"
            "- 生成/更新 section JSON（含 claim_ids_covered）"
        )
    if claim_lines:
        parts.append(
            f"以下 critical claims 属于角色 {role} 但未被任何输出覆盖，需要逐个验证：\n"
            + "\n".join(claim_lines)
            + "\n你需要：\n"
            "- 针对每个 claim 进行外部搜索验证\n"
            "- 将验证结果写入 facts JSON\n"
            "- 更新 section JSON 的 `claim_ids_covered` 字段，确保包含上述所有 claim ID\n"
            "- 如有必要，在 markdown 输出中补充相关段落"
        )
    if not parts:
        parts.append(f"修复角色 {role} 的输出问题（见 repair_tasks 详情）。")
    return "\n\n".join(parts)


def build_repair_manifests(
    task_dir: Path,
    wave: int,
    gate_result: dict[str, Any],
    outputs_dir: Path | None = None,
) -> list[str]:
    """为 gate FAIL 产生的 repair_tasks 生成 manifest JSON 文件。

    按 owner_section (role) 聚合：同一 role 的多个 repair_tasks 合并到一个 manifest，
    system_prompt 中列出该 role 所有待修复的 claims，timeout 按 claim 数量延长。

    Returns:
        manifest 文件路径列表（供主 AI 读取并派发 repair 子代理）。
    """
    repair_tasks = gate_result.get("repair_tasks") or []
    if not repair_tasks:
        return []

    # 从 research_plan 中读取 claim 详情，用于 claim 级 repair 的目标描述
    research_plan = load_json(task_dir / "bp_research_plan.json", {})
    claim_map: dict[str, dict] = {}
    for claim in research_plan.get("claim_matrix") or []:
        cid = str(claim.get("claim_id", ""))
        if cid:
            claim_map[cid] = claim

    # ── 按 owner_section (role) 聚合 repair_tasks ──
    tasks_by_role: dict[str, list[dict[str, Any]]] = {}
    for task in repair_tasks:
        role = task.get("owner_section", "")
        if not role:
            continue
        tasks_by_role.setdefault(role, []).append(task)

    manifests: list[str] = []
    for role, role_tasks in tasks_by_role.items():
        slug = _role_slug(role)
        paths = _role_paths(task_dir, role, outputs_dir)

        # 收集该 role 所有待修复的 claim_id
        claim_ids: list[str] = []
        for task in role_tasks:
            cid = task.get("claim_id", "")
            if cid:
                claim_ids.append(cid)

        # 构建 repair goal —— 列出所有待修复的 claims
        repair_goal = _build_repair_goal_for_role(role, role_tasks, claim_map)

        # 描述现有文件状态
        existing_files_desc = "- Markdown: "
        existing_files_desc += (
            f"`{paths['markdown']}` (exists={paths['markdown'].exists()})"
        )
        existing_files_desc += f"\n- Facts: `{paths['facts']}` (exists={paths['facts'].exists()})"
        existing_files_desc += f"\n- Section: `{paths['section']}` (exists={paths['section'].exists()})"

        system_prompt = _REPAIR_SYSTEM_PROMPT_TEMPLATE.format(
            wave=wave,
            role=role,
            repair_goal=repair_goal,
            existing_files_desc=existing_files_desc,
            facts_path=str(paths["facts"]),
            section_path=str(paths["section"]),
        )

        # timeout 按 claim 数量延长（基础 900 秒 × claim 数，至少 900）
        claim_count = max(1, len(claim_ids))
        repair_id = role_tasks[0].get("repair_task_id", f"BGR-W{wave}-000")
        manifest = {
            "manifest_version": "1.0",
            "task_id": task_dir.name,
            "repair_task_id": repair_id,
            "role": role,
            "slug": slug,
            "wave": wave,
            "label": f"{task_dir.name}-repair-w{wave}-{slug}",
            "system_prompt": system_prompt,
            "connectorIds": BP_TYC_CONNECTOR_IDS,
            "claim_ids": claim_ids,
            "output_path": str(paths["markdown"]),
            "sidecar_paths": {
                "facts": str(paths["facts"]),
                "section": str(paths["section"]),
            },
            "existing_outputs": {
                "markdown": str(paths["markdown"]) if paths["markdown"].exists() else None,
                "facts": str(paths["facts"]) if paths["facts"].exists() else None,
                "section": str(paths["section"]) if paths["section"].exists() else None,
            },
            "timeout": 900 * claim_count,
            "thinking": "high",
            "dispatch_mode": "team_async",
            "mode": "bypassPermissions",
            "subagent_type": "general-purpose",
            "status": "pending",
        }
        manifest_path = task_dir / f"bp_wave{wave}_repair_manifest_{slug}.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifests.append(str(manifest_path))

    return manifests


def _read_stage_tier(task_dir: Path) -> str:
    """Read stage_tier — 统一使用 bp_stage_utils.read_stage_from_task。"""
    from scripts.bp_stage_utils import read_stage_from_task
    return read_stage_from_task(task_dir, default="T4")


def evaluate_bp_wave_evidence_gate(
    task_dir: Path,
    wave: int,
    outputs_dir: Path | None = None,
) -> dict[str, Any]:
    roles = _expected_roles(task_dir, wave, outputs_dir)
    role_results: list[dict[str, Any]] = []
    covered_claims: set[str] = set()
    repair_tasks: list[dict[str, Any]] = []

    for role in roles:
        paths = _role_paths(task_dir, role, outputs_dir)
        existing = {key: path.exists() and path.stat().st_size > 0 for key, path in paths.items()}
        status = "pass"
        missing = [key for key, ok in existing.items() if not ok]
        if missing:
            status = "missing_sidecar" if any(key in missing for key in ("facts", "section")) else "missing_output"
            repair_tasks.append({
                "repair_task_id": f"BGR-W{wave}-{len(repair_tasks)+1:03d}",
                "owner_section": role,
                "claim_id": "",
                "reason": "MISSING_WAVE_OUTPUT_OR_SIDECAR",
                "required_actions": ["write_fact", "update_section_package"],
            })
        section = load_json(paths["section"], {})
        for claim_id in section.get("claim_ids_covered") or []:
            covered_claims.add(str(claim_id))
        audit = section.get("search_audit") if isinstance(section, dict) else {}
        if isinstance(audit, dict):
            for item in audit.get("claim_coverage") or []:
                if isinstance(item, dict) and item.get("claim_id"):
                    covered_claims.add(str(item.get("claim_id")))
        # Soft check: comparison/benchmark tables (never blocks, adds visibility)
        comparison_check = _comparison_table_check(role, paths["markdown"])
        if not comparison_check["passed"] and paths["markdown"].exists():
            print(
                f"  ⚠️ [wave{wave}_evidence_gate] {role}: {comparison_check['reason']} "
                f"(table_count={comparison_check.get('table_count', 0)})",
                flush=True,
            )
        role_results.append({
            "role": role,
            "status": status,
            "missing": missing,
            "paths": {key: str(path) for key, path in paths.items()},
            "comparison_check": comparison_check,
        })

    blocking_claims: list[str] = []
    for claim in _critical_claims(task_dir, roles):
        claim_id = str(claim.get("claim_id"))
        if claim_id and claim_id not in covered_claims:
            blocking_claims.append(claim_id)
            repair_tasks.append({
                "repair_task_id": f"BGR-W{wave}-{len(repair_tasks)+1:03d}",
                "owner_section": claim.get("owner_section"),
                "claim_id": claim_id,
                "reason": "CRITICAL_CLAIM_NOT_ADDRESSED",
                "required_actions": ["search", "fetch", "write_fact", "update_section_package"],
            })

    # ── blocking_claims 降级机制 ──
    prior_attempt = read_attempt_count(task_dir / f"bp_wave{wave}_evidence_gate.json")
    stage_tier = _read_stage_tier(task_dir)
    is_early_stage = stage_tier in {"T1", "T2"}

    blocking_degraded = False
    if blocking_claims:
        if prior_attempt >= _MAX_BLOCKING_RETRIES:
            blocking_degraded = True
            print(
                f"  ⚠️ [wave{wave}_evidence_gate] blocking_claims {blocking_claims} "
                f"已重试 {prior_attempt} 次仍未覆盖，降级为 WARN 放行",
                flush=True,
            )
        elif is_early_stage:
            # T1/T2 早期项目：blocking_claims 直接降级为 WARN
            blocking_degraded = True
            print(
                f"  ⚠️ [wave{wave}_evidence_gate] T1/T2 早期项目，blocking_claims {blocking_claims} "
                f"降级为 WARN 放行",
                flush=True,
            )

    # sidecar 缺失仍然是硬卡（数据不完整不能放行）
    all_sidecars_ok = all(item["status"] == "pass" for item in role_results)
    ok = all_sidecars_ok and (not blocking_claims or blocking_degraded)

    # ── 判断是否需要派发 repair 子代理 ──
    # gate FAIL 且尚未超过最大修复轮次 → needs_repair（管线暂停，派发 repair 子代理）
    needs_repair = False
    if not ok and prior_attempt < _MAX_BLOCKING_RETRIES:
        needs_repair = True
        print(
            f"  🔧 [wave{wave}_evidence_gate] gate FAIL (attempt {prior_attempt + 1})，"
            f"将派发 {len(repair_tasks)} 个 repair 子代理进行修复",
            flush=True,
        )

    result = {
        "schema_version": "bp_wave_evidence_gate.v1",
        "wave": wave,
        "ok": ok,
        "needs_repair": needs_repair,
        "gate_verdict": "PASS" if ok else ("REPAIR" if needs_repair else "FAIL"),
        "attempt": prior_attempt + 1,
        "role_results": role_results,
        "blocking_claims": blocking_claims,
        "blocking_claims_degraded": blocking_degraded,
        "repair_exhausted": blocking_degraded,  # 统一降级标记，delivery gate 识别
        "repair_tasks": repair_tasks,
    }
    output_path = task_dir / f"bp_wave{wave}_evidence_gate.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
