"""Literature Review Pipeline Profile — 3 Wave / 6 Role / 20 Phase.

基于 multi-agent-pipeline 框架的 VC 技术评估文献综述管线。

架构:
  Wave 1: academic_scout / industry_scout / enterprise_scout  (数据独立, has_more 串行)
  Wave 2: deep_reader → tech_strategist                       (前后依赖)
  Wave 3: report_writer                                       (产出层)

Phase 清单 (20 phases):
  01 intake             解析输入
  02 tech_decomposition 分解 sub_topic + 搜索关键词矩阵
  03 presearch          预搜索 [heavy_bg] ★v2.0: 基于 intake 数据独立运行，前置到 research plan 之前
  04 research_plan      核心问题 + claim matrix (cached check, 基于 presearch 结果规划)
  05 shared_state_init  初始化 fact_store + shared_state
  06 wave1_dispatch_prepare  W1 调度 (3 角色)
  07 wave1_dispatch_collect  W1 收集 (4 层防御)
  08 wave1_evidence_gate     W1 质量门 + repair
  09 wave1_fact_store_merge  W1 合并 → fact_store.json
  10 wave1_shared_state_refresh  W1 刷新 → reading_tasks
  11 wave2_dispatch_prepare  W2 调度 (2 角色)
  12 wave2_dispatch_collect  W2 收集
  13 wave2_evidence_gate     W2 质量门 + repair
  14 wave2_shared_state_refresh  W2 刷新
  15 wave3_dispatch_prepare  W3 调度 (1 角色)
  16 wave3_dispatch_collect  W3 收集
  17 claim_coverage          全 claim 覆盖检查
  18 debate_review           跨章节对抗审查
  19 final_assembly          排版 + md/docx/bib
  20 delivery                交付
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from runtime.profiles.base import JobContext, PipelineProfile
from runtime.profiles.lit_constants import (
    COLLECT_RETRY_COUNT,
    COLLECT_RETRY_INTERVAL,
    SIDECAR_RETRY_COUNT,
    SIDECAR_RETRY_INTERVAL,
    LIT_ALL_ROLE_SLUGS,
    LIT_ROLE_CONNECTOR_IDS,
    LIT_WAVE1_ROLE_SLUGS,
    LIT_WAVE2_ROLE_SLUGS,
    LIT_WAVE3_ROLE_SLUGS,
    LIT_WAVE_ROLES,
    WAVE1_GATE_THRESHOLDS,
    WAVE2_GATE_THRESHOLDS,
    WAVE3_GATE_THRESHOLDS,
    DEEP_READER_BATCH_SIZE,
    FACTS_SUFFIX,
    SECTION_SUFFIX,
    NOTES_SUFFIX,
    GATE_REPAIR_MAX_ATTEMPTS,
)


# ── ACTIVE_ROLES safety net ────────────────────────────────
LIT_ACTIVE_ROLES: set[str] = {
    "academic_scout", "industry_scout", "enterprise_scout",
    "deep_reader", "tech_strategist", "report_writer",
    "tech_decomposition",
}

# ── Instruction Store Cache (module-level, mtime detection) ─
_INSTRUCTION_STORE_CACHE: dict[str, str] | None = None
_INSTRUCTION_STORE_MTIME: float = 0
_TOOL_GUIDE_CACHE: str = ""
_TOOL_GUIDE_MTIME: float = 0


def _load_instruction_prompts(runtime_root: Path, force_reload: bool = False) -> dict[str, str]:
    """Hot-load all active role prompts from instruction_store_lit/ with mtime cache."""
    global _INSTRUCTION_STORE_CACHE, _INSTRUCTION_STORE_MTIME
    store_dir = runtime_root / "instruction_store_lit"
    index_path = store_dir / "index.json"
    if not index_path.exists():
        return {}
    current_mtime = index_path.stat().st_mtime
    if not force_reload and _INSTRUCTION_STORE_CACHE is not None and current_mtime == _INSTRUCTION_STORE_MTIME:
        return _INSTRUCTION_STORE_CACHE
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return _INSTRUCTION_STORE_CACHE or {}
    prompts = {}
    for role in index.get("roles", []):
        key = role.get("key", "")
        if key not in LIT_ACTIVE_ROLES:
            continue
        path = store_dir / role.get("file", "")
        if path.exists():
            prompts[key] = path.read_text(encoding="utf-8")
    _INSTRUCTION_STORE_CACHE = prompts
    _INSTRUCTION_STORE_MTIME = current_mtime
    return prompts


def _load_tool_guide(runtime_root: Path) -> str:
    """Load _common_tool_guide.md with mtime cache."""
    global _TOOL_GUIDE_CACHE, _TOOL_GUIDE_MTIME
    guide_path = runtime_root / "instruction_store_lit" / "_common_tool_guide.md"
    if not guide_path.exists():
        return _TOOL_GUIDE_CACHE
    current_mtime = guide_path.stat().st_mtime
    if _TOOL_GUIDE_CACHE and current_mtime == _TOOL_GUIDE_MTIME:
        return _TOOL_GUIDE_CACHE
    _TOOL_GUIDE_CACHE = guide_path.read_text(encoding="utf-8")
    _TOOL_GUIDE_MTIME = current_mtime
    return _TOOL_GUIDE_CACHE


# Conclusion appendix — appended to ALL role prompts
_CONCLUSION_APPENDIX = """

## ⚠️ 结论格式要求 (所有角色通用)

1. 每个事实陈述必须绑定 fact_id (READ-XXX / IND-XXX / ENT-XXX)，不绑定就是猜测
2. Counter Evidence 和 Data Gaps 章节必须存在且非空
3. 质量评估标签: A 级证据优先引用，B 级需交叉验证，C 级标注局限性
4. 输出三文件: {role}.md + {role}-facts.json + {role}-section.json
5. 不要使用 SendMessage 与 Coordinator 通信，直接写文件完成任务
"""


def _assemble_system_prompt(runtime_root: Path, role: str, task_dir: Path | None = None) -> str:
    """Assemble full system prompt: instruction + conclusion + tool guide.

    Replaces {RUNTIME_ROOT} and {TASK_DIR} placeholders with actual paths
    so sub-agents can execute commands without guessing directories.

    Returns complete prompt text, or error marker if role is unknown.
    """
    prompts = _load_instruction_prompts(runtime_root)
    base = prompts.get(role, f"UNKNOWN ROLE: {role}. No instruction-store prompt is registered.")
    tool_guide = _load_tool_guide(runtime_root)
    full_prompt = base + _CONCLUSION_APPENDIX + tool_guide

    # ── Placeholder substitution (critical for sub-agent command execution) ──
    full_prompt = full_prompt.replace("{RUNTIME_ROOT}", str(runtime_root))
    if task_dir is not None:
        full_prompt = full_prompt.replace("{TASK_DIR}", str(task_dir))
    else:
        # Fallback: if task_dir not provided, still replace with runtime_root
        # (some roles like tech_decomposition use runtime_root as task dir)
        full_prompt = full_prompt.replace("{TASK_DIR}", str(runtime_root))

    return full_prompt


# ── Dispatch Instruction Template ──────────────────────────
def _lit_dispatch_instruction(role: str, slug: str, next_phase: str, has_more: bool) -> str:
    """Generate dispatch instruction for Coordinator."""
    return (
        f"MANDATORY: Read the manifest JSON file at the path below.\n"
        f"Use the Agent tool with these EXACT parameters:\n"
        f"  - name = '{role}'\n"
        f"  - team_name = 'lit-{{task_id}}'\n"
        f"  - mode = 'bypassPermissions'\n"
        f"  - prompt = manifest's 'system_prompt' field (COMPLETE, do NOT simplify)\n"
        f"  - connectorIds = manifest's 'connectorIds' field\n\n"
        f"## ⚠️ CRITICAL: 三文件验证 (缺一不可)\n"
        f"子代理输出 3 个文件:\n"
        f"  - {role}.md (>100 bytes)\n"
        f"  - {role}-facts.json (>10 bytes, valid JSON)\n"
        f"  - {role}-section.json (>10 bytes, valid JSON)\n"
        f"如果只看到 .md 而 sidecar 不存在，说明子代理还在写文件，必须继续等待。\n\n"
        f"确认三文件齐全后，用 start_phase='{next_phase}' 恢复管线。\n"
    ) + (
        f"\n⚠️ has_more=True: 还有更多角色待派发，恢复后会返回下一个 manifest。\n"
        if has_more else
        f"\n✅ 这是最后一个角色，恢复后会推进到下一阶段。\n"
    ) + (
        "\n## ⚠️ 绝对禁止:\n"
        "- 禁止在单条消息中派发多个 Agent tool_use\n"
        "- 禁止在子代理三文件齐全前推进管线\n"
        "- 禁止简化或截断 manifest 中的 system_prompt\n"
    )


# ── Prompt Truncation Guard ────────────────────────────────
MIN_PROMPT_LENGTH = 200  # system_prompt must be at least this long


# ── 工具函数 ────────────────────────────────────────────────

def _task_dir(runtime_root: Path, job_ctx: JobContext) -> Path:
    ws = job_ctx.workspace
    if ws:
        return ws.root
    d = runtime_root / "data" / "tasks" / job_ctx.job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _outputs_dir(runtime_root: Path, job_ctx: JobContext) -> Path:
    ws = job_ctx.workspace
    if ws:
        return ws.outputs_dir
    return _task_dir(runtime_root, job_ctx)


def _sync_to_workspace(job_ctx: JobContext, src: Path, dest_name: str):
    ws = job_ctx.workspace
    if ws is None or not src.exists():
        return
    try:
        shutil.copy2(src, ws.outputs_dir / dest_name)
    except Exception:
        pass


# ── P0: File Stability / JSON Self-Repair / Atomic Write ────

def _file_stable(path: Path, interval: float = 5) -> bool:
    """检查文件是否已写完（大小不再增长）。"""
    if not path.exists():
        return False
    size1 = path.stat().st_size
    time.sleep(interval)
    if not path.exists():
        return False
    size2 = path.stat().st_size
    return size2 == size1


def _safe_load_json_with_repair(path: Path) -> dict | list | None:
    """Load a JSON file with auto-repair for malformed JSON from sub-agents.

    Sub-agents sometimes produce JSON with unescaped quotes inside string values,
    causing json.JSONDecodeError. This function attempts to fix common patterns:
    1. Unescaped internal quotes in string values
    2. Trailing commas before closing brackets
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        pass

    text = path.read_text(encoding="utf-8")

    # Strategy 1: Escape internal quotes
    result: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(text):
        c = text[i]
        if escape_next:
            result.append(c)
            escape_next = False
            i += 1
            continue
        if c == '\\' and in_string:
            result.append(c)
            escape_next = True
            i += 1
            continue
        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                rest = text[i + 1:].lstrip()
                if rest and rest[0] in ':,]}\n':
                    in_string = False
                    result.append(c)
                elif not rest:
                    in_string = False
                    result.append(c)
                else:
                    result.append('\\"')
            i += 1
            continue
        result.append(c)
        i += 1

    fixed_text = ''.join(result)

    # Strategy 2: Remove trailing commas before closing brackets/braces
    import re
    fixed_text = re.sub(r',(\s*[}\]])', r'\1', fixed_text)

    # Strategy 3: Fix smart/curly quotes → ASCII straight quotes
    fixed_text = fixed_text.replace('\u201c', '"').replace('\u201d', '"')
    fixed_text = fixed_text.replace('\u2018', "'").replace('\u2019', "'")

    try:
        data = json.loads(fixed_text)
        from scripts.bp_file_lock import atomic_write
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"    🔧 JSON auto-repaired: {path.name}", flush=True)
        return data
    except json.JSONDecodeError:
        return None


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write a JSON file using bp_file_lock.atomic_write."""
    from scripts.bp_file_lock import atomic_write
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write a text file using bp_file_lock.atomic_write."""
    from scripts.bp_file_lock import atomic_write
    atomic_write(path, text)


def _merge_quality_summary(task: Path, shared_state: dict, non_empty_tasks: list) -> None:
    """合并所有 sub_topic 的 quality_tier 统计到 shared_state.quality_summary。

    在 wave2 collect 全部 sub_topic 完成后调用。
    兼容 quality_tier / overall_grade / grade 多种字段名。
    """
    tier_dist = {"A": 0, "B": 0, "C": 0, "unknown": 0}
    a_tier_ids: list[str] = []
    total_assessed = 0

    for idx, _rt in non_empty_tasks:
        notes_file = task / f"sub_topic_{idx + 1}{NOTES_SUFFIX}"
        if not notes_file.exists():
            continue
        try:
            data = json.loads(notes_file.read_text(encoding="utf-8"))
            notes_list = data.get("notes", data.get("reading_notes", []))
            if not isinstance(notes_list, list):
                continue
            for note in notes_list:
                qa = note.get("quality_assessment", {})
                if not qa:
                    tier_dist["unknown"] += 1
                    continue
                total_assessed += 1
                # 容错读取: quality_tier > overall_grade > grade
                tier = (
                    qa.get("quality_tier")
                    or qa.get("overall_grade")
                    or qa.get("grade")
                    or "unknown"
                )
                tier = str(tier).upper().strip()
                if tier in ("A", "B", "C"):
                    tier_dist[tier] = tier_dist.get(tier, 0) + 1
                else:
                    tier_dist["unknown"] += 1
                if tier == "A":
                    doc_id = note.get("doc_id", note.get("fact_id", ""))
                    if doc_id:
                        a_tier_ids.append(doc_id)
        except Exception:
            pass

    a_rate = tier_dist["A"] / max(total_assessed, 1)
    shared_state["quality_summary"] = {
        "total_assessed": total_assessed,
        "tier_distribution": tier_dist,
        "a_tier_paper_ids": a_tier_ids,
        "a_tier_ratio": round(a_rate, 3),
        "merged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 写回 shared_state
    ss_path = task / "shared_state.json"
    _atomic_write_json(ss_path, shared_state)


def _collect_with_sidecar_retry(
    collect_fn,
    *,
    task_dir: Path,
    collect_name: str = "lit_collect",
    max_retries: int = SIDECAR_RETRY_COUNT,
    retry_interval: int = SIDECAR_RETRY_INTERVAL,
) -> dict[str, Any]:
    """对 collect 函数加 sidecar 落盘重试。

    问题: 子代理先写 .md 再写 sidecar (JSON 序列化耗时)，
    直接判定 incomplete 会触发代价高昂的重 dispatch。

    解决: 在 collect 内部加 retry loop，等 sidecar 写完。
    max_retries × retry_interval = 最多额外等待。

    进度检测: missing 数量 + .md 文件总大小。
    - missing 减少 或 md 增长 → 子代理还在写，继续等
    - 两者都不变 → 子代理可能已挂，提前退出
    """
    last_result: dict[str, Any] | None = None
    prev_signal: tuple[int, int] | None = None

    for attempt in range(max_retries + 1):
        result = collect_fn()
        if result.get("ok") is True and not result.get("needs_dispatch"):
            if attempt > 0:
                print(f"  ✅ {collect_name} 重试成功 (attempt {attempt+1}/{max_retries+1})", flush=True)
            return result
        last_result = result
        if attempt < max_retries:
            # 计算进度信号
            incomplete = result.get("dispatch_info", {}).get("incomplete_roles", [])
            total_md_size = 0
            for md_file in task_dir.glob("*.md"):
                try:
                    total_md_size += md_file.stat().st_size
                except OSError:
                    pass
            current_signal = (len(incomplete), total_md_size)

            has_signal = total_md_size > 0
            if has_signal and prev_signal is not None and current_signal == prev_signal:
                print(f"  ⚠️ {collect_name} 无进度 ({len(incomplete)} incomplete, "
                      f"md_size={total_md_size})，子代理可能已停止", flush=True)
                break

            print(f"  ⏳ {collect_name} attempt {attempt+1}/{max_retries+1} incomplete "
                  f"({', '.join(incomplete[:3])}), retrying in {retry_interval}s...", flush=True)
            prev_signal = current_signal
            time.sleep(retry_interval)

    return last_result or {"ok": False}


def _build_lit_repair_manifest(
    task_dir: Path,
    *,
    role: str,
    slug: str,
    failure_reason: str,
    gate_phase: str,
    runtime_root: Path,
) -> str:
    """构建 LIT gate repair manifest，让子代理针对失败项补充采集。

    返回 manifest 文件路径。
    """
    system_prompt = _assemble_system_prompt(runtime_root, role, task_dir=task_dir)

    repair_prompt = (
        f"## 🔧 REPAIR TASK — Gate {gate_phase} FAIL\n\n"
        f"**失败原因**: {failure_reason}\n\n"
        f"## 要求\n"
        f"1. 阅读现有的 {role}.md 和 {role}-facts.json\n"
        f"2. 针对上述失败原因，补充搜索/采集\n"
        f"3. 将新发现追加到现有文件（不覆盖已有内容）\n"
        f"4. 更新 {role}-section.json 的 stats\n"
        f"5. 完成后正常退出，管线会自动重跑 gate\n"
    )

    manifest = {
        "role": role,
        "slug": slug,
        "system_prompt": system_prompt + "\n\n" + repair_prompt,
        "output_path": str(task_dir / f"{role}.md"),
        "connectorIds": LIT_ROLE_CONNECTOR_IDS.get(role, []),
        "is_repair": True,
        "repair_reason": failure_reason,
        "expected_outputs": [
            f"{role}.md",
            f"{role}{FACTS_SUFFIX}",
            f"{role}{SECTION_SUFFIX}",
        ],
    }
    manifest_path = task_dir / f"repair_manifest_{role}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(manifest_path)


def _repair_instruction(phase_name: str, has_more: bool, remaining_count: int) -> str:
    """生成 LIT gate repair 派发指令。"""
    return (
        "MANDATORY SEQUENTIAL REPAIR DISPATCH — 禁止并行派发\n"
        "\n"
        f"## 原因\n"
        f"LIT Gate {phase_name} FAIL，需要派发 repair 子代理补充采集。\n"
        f"多个 repair 子代理会写同一组 sidecar 文件，并行执行会导致数据丢失。\n"
        "\n"
        "## 步骤\n"
        "1. 读取 dispatch_info.manifests[0] 指向的 manifest JSON\n"
        "2. 使用 Agent tool 派发**这一个** repair 子代理（mode='bypassPermissions'）\n"
        "3. 等待 repair 子代理完成（修改 sidecar 文件）\n"
        f"4. 用 start_phase='{phase_name}' 恢复管线，重跑 gate\n"
    ) + (
        f"\n5. 管线会返回下一个 repair manifest（剩余 {remaining_count} 个），重复步骤 1-4"
        if has_more else
        f"\n5. 这是最后一个 repair manifest，完成后 gate 会重新评估"
    ) + (
        "\n\n## ⚠️ 绝对禁止并行派发 repair 子代理\n"
        "⚠️ 禁止在单条消息中派发多个 Agent tool_use\n"
    )


# ═══════════════════════════════════════════════════════════
# Phase 01-05: 准备阶段 (主控执行，无子代理)
# ═══════════════════════════════════════════════════════════

def _run_intake(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 01: 解析用户输入 — 技术方向、关注维度、语言。"""
    task = _task_dir(runtime_root, job_ctx)
    metadata = job_ctx.metadata or {}

    intake_data = {
        "schema_version": "lit_intake.v1",
        "job_id": job_ctx.job_id,
        "tech_direction": job_ctx.entity,
        "query": job_ctx.query,
        "focus_dimensions": metadata.get("focus_dimensions", [
            "技术成熟度", "市场竞争格局", "商业化时间线", "投资判断"
        ]),
        "language": metadata.get("language", "zh-CN"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    output_path = task / "intake.json"
    output_path.write_text(json.dumps(intake_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_to_workspace(job_ctx, output_path, "intake.json")

    return {
        "ok": True,
        "mode": "script",
        "phase": "phase01_intake",
        "job_id": job_ctx.job_id,
        "result": {"output_path": str(output_path)},
    }


def _run_tech_decomposition(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 02: 技术拆解 + 研究规划 (子代理直接输出 research_plan.json)。

    v3: 子代理预搜后直接输出 tech_decomposition.json + research_plan.json，
    phase03 只做 cached check。
    """
    task = _task_dir(runtime_root, job_ctx)
    decompose_path = task / "tech_decomposition.json"
    plan_path = task / "research_plan.json"

    # 双文件 cached 检查: 两个文件都存在则跳过
    decompose_ok = decompose_path.exists() and decompose_path.stat().st_size > 50
    plan_ok = plan_path.exists() and plan_path.stat().st_size > 100
    if decompose_ok and plan_ok:
        data = json.loads(decompose_path.read_text(encoding="utf-8"))
        return {"ok": True, "mode": "cached", "phase": "phase02_tech_decomposition", "job_id": job_ctx.job_id, "result": data}

    # 读 intake
    intake_path = task / "intake.json"
    intake = {}
    if intake_path.exists():
        intake = json.loads(intake_path.read_text(encoding="utf-8"))

    # 组装 system_prompt: instruction + conclusion + tool_guide
    system_prompt = _assemble_system_prompt(runtime_root, "tech_decomposition", task_dir=task)
    assert len(system_prompt) > MIN_PROMPT_LENGTH, f"system_prompt too short for tech_decomposition: {len(system_prompt)} chars"

    # 追加输入上下文
    system_prompt += (
        f"\n\n## 你的输入\n\n"
        f"- **tech_direction**: {intake.get('tech_direction', job_ctx.entity)}\n"
        f"- **query**: {intake.get('query', job_ctx.query)}\n"
        f"- **job_id**: {job_ctx.job_id}\n\n"
        f"## 输出要求\n\n"
        f"写 **2 个文件**:\n"
        f"1. `{decompose_path}` — 技术拆解 (预搜发现 + PICO + 目标公司)\n"
        f"2. `{plan_path}` — 研究计划 (sub_topics + claim_matrix + search_keywords)\n\n"
        f"⚠️ research_plan.json 是下游搜索代理的直接输入，格式必须严格遵守指令中的 schema。\n"
        f"sub_topics 必须是 list[str]（纯字符串列表）。\n"
        f"写完后正常退出即可。\n"
    )

    manifest = {
        "role": "tech_decomposition",
        "slug": "tech_decomposition",
        "system_prompt": system_prompt,
        "output_path": str(decompose_path),
        "brief_path": str(task / "intake.json"),
        "connectorIds": LIT_ROLE_CONNECTOR_IDS.get("tech_decomposition", []),
        "key_inputs": {
            "intake": str(task / "intake.json"),
            "tech_direction": intake.get("tech_direction", job_ctx.entity),
            "query": intake.get("query", job_ctx.query),
            "job_id": job_ctx.job_id,
        },
        "expected_outputs": ["tech_decomposition.json", "research_plan.json"],
    }
    manifest_path = task / "wave0_manifest_tech_decomposition.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    instruction = (
        f"MANDATORY: Read the manifest JSON file at the path below.\n"
        f"Use the Agent tool with these EXACT parameters:\n"
        f"  - name = 'tech_decomposition'\n"
        f"  - team_name = 'lit-{{task_id}}'\n"
        f"  - mode = 'bypassPermissions'\n"
        f"  - prompt = manifest's 'system_prompt' field (COMPLETE, do NOT simplify)\n"
        f"  - connectorIds = manifest's 'connectorIds' field\n\n"
        f"## ⚠️ 双文件验证\n"
        f"子代理输出 **2 个文件**:\n"
        f"  - tech_decomposition.json (>50 bytes, valid JSON)\n"
        f"  - research_plan.json (>100 bytes, valid JSON, sub_topics 必须是 list[str])\n"
        f"确认两个文件都存在且 JSON 有效后，用 start_phase='phase03_research_plan' 恢复管线。\n\n"
        f"✅ 完成后管线自动进入 Phase 03（仅 cached check）→ Phase 04（预搜验证）。\n"
    )

    return {
        "ok": True,
        "needs_dispatch": True,
        "dispatch_info": {
            "type": "phase02_dispatch",
            "manifests": [str(manifest_path)],
            "has_more": False,
            "current_role": "tech_decomposition",
        },
        "instruction": instruction,
        "paused_after": "phase02_tech_decomposition",
        "next_phase": "phase03_research_plan",
        "phase": "phase02_tech_decomposition",
        "job_id": job_ctx.job_id,
    }


def _run_research_plan(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 03: 验证 research_plan.json 存在。

    v3: research_plan 由 phase02 子代理直接生成。此 phase 只做 cached check + 兜底生成。
    如果 phase02 没写 research_plan.json，从 tech_decomposition.json 兜底构建。
    """
    task = _task_dir(runtime_root, job_ctx)
    plan_path = task / "research_plan.json"

    if plan_path.exists() and plan_path.stat().st_size > 100:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        return {"ok": True, "mode": "cached", "phase": "phase04_research_plan", "job_id": job_ctx.job_id, "result": data}

    # 兜底: phase02 子代理没写 research_plan → 从 tech_decomposition.json 构建
    decompose_path = task / "tech_decomposition.json"
    decompose = {}
    if decompose_path.exists():
        decompose = json.loads(decompose_path.read_text(encoding="utf-8"))

    raw_sub_topics = decompose.get("sub_topics", [job_ctx.entity])

    # 兼容 list[str] 和 list[dict]
    sub_topic_names: list[str] = []
    for item in raw_sub_topics:
        if isinstance(item, str):
            sub_topic_names.append(item)
        elif isinstance(item, dict):
            sub_topic_names.append(item.get("name") or item.get("sub_topic") or str(item))
        else:
            sub_topic_names.append(str(item))

    # search_keywords 兼容
    raw_kw = decompose.get("search_keywords", {})
    search_keywords = raw_kw if isinstance(raw_kw, dict) else {}

    claim_matrix = []
    for i, st in enumerate(sub_topic_names):
        claim_matrix.append({
            "claim_id": f"CLAIM-{i+1:03d}",
            "claim": f"{st} 领域存在显著技术进展",
            "owner_section": st,
            "status": "planned",
            "search_plan": {"en": [st], "zh": [st]},
        })

    plan = {
        "schema_version": "lit_research_plan.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "sub_topics": sub_topic_names,
        "target_companies": decompose.get("target_companies", []),
        "claim_matrix": claim_matrix,
        "search_keywords": search_keywords,
        "plan_status": "ready",
        "validation": {"ready": True, "claim_count": len(claim_matrix)},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_to_workspace(job_ctx, plan_path, "research_plan.json")

    return {
        "ok": True,
        "mode": "fallback_script",
        "phase": "phase04_research_plan",
        "job_id": job_ctx.job_id,
        "result": {"output_path": str(plan_path), "claim_count": len(claim_matrix)},
    }


def _run_presearch(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 03: 预搜索 — v2.0 heavy_bg + westock。

    v2.0 变更 (2026-07-08):
      - 改为 heavy_bg 后台执行，不再阻塞管线
      - 基于 intake.json 的 tech_direction 生成查询，不再依赖 tech_decomposition
      - 新增 westock_report + westock_sector + neodata 数据源
      - 查询预算提升到 20+

    数据源: search_deep + tencent_news + tyc + westock_report + westock_sector + neodata
    """
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_presearch_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase03_presearch")
    if cached is not None:
        print(f"  📦 [lit] 使用缓存的 presearch 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase03_presearch", pipeline="lit")


def _run_presearch_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """presearch 实际执行 — v2.0: 基于 intake 数据独立搜索。

    v2.0: 从 intake.json 提取 tech_direction 和 query 生成搜索方向，
    不再依赖 tech_decomposition 的 sub_topics——presearch 在 research plan 之前独立运行。
    """
    task = _task_dir(runtime_root, job_ctx)
    presearch_path = task / "presearch.json"

    # v2.0: 从 intake.json 读取 tech_direction 和 query（不依赖 tech_decomposition）
    intake_path = task / "intake.json"
    intake = {}
    if intake_path.exists():
        try:
            intake = json.loads(intake_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    tech_direction = intake.get("tech_direction") or job_ctx.entity
    query = intake.get("query") or job_ctx.query or ""

    # 从 tech_direction 拆分关键词作为 sub_topics
    # 按常见分隔符切分 (逗号/顿号/分号/和/与/及)
    raw_parts = __import__('re').split(r'[,，、;；和与及]', tech_direction)
    sub_topics = [p.strip() for p in raw_parts if len(p.strip()) > 1]
    if not sub_topics:
        sub_topics = [tech_direction]

    # target_companies 从 intake 中提取（如果有）
    target_companies = intake.get("target_companies", [])

    # 使用统一 presearch 引擎
    try:
        from scripts.presearch_query_builder import execute_presearch

        result = execute_presearch(
            pipeline="lit",
            task_id=job_ctx.job_id,
            entity=job_ctx.entity,
            sub_topics=sub_topics,
            target_companies=target_companies,
            query=query,
            output_dir=task,
        )

        presearch_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _sync_to_workspace(job_ctx, presearch_path, "presearch.json")

        return {
            "ok": result.get("total_evidence", 0) > 0,
            "mode": "presearch",
            "phase": "phase03_presearch",
            "job_id": job_ctx.job_id,
            "result": result,
        }
    except Exception as e:
        # Fallback: 简化的 viability check
        results = {"sub_topic_results": [], "viable": True, "warnings": [f"presearch error (fallback): {str(e)}"]}
        try:
            sys_path = str(runtime_root)
            if sys_path not in __import__('sys').path:
                __import__('sys').path.insert(0, sys_path)
            from scripts.search_gateway import search
            for st in sub_topics[:3]:
                ddg_results = search(st, max_results=5, prefer="ddg")
                results["sub_topic_results"].append({
                    "sub_topic": st,
                    "ddg_count": len(ddg_results),
                    "viable": len(ddg_results) > 0,
                })
        except Exception:
            pass

        presearch_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _sync_to_workspace(job_ctx, presearch_path, "presearch.json")

        return {
            "ok": results["viable"],
            "mode": "presearch_fallback",
            "phase": "phase03_presearch",
            "job_id": job_ctx.job_id,
            "result": results,
        }


def _run_shared_state_init(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 05: 初始化 fact_store + shared_state + reading_tasks 骨架。"""
    task = _task_dir(runtime_root, job_ctx)

    # fact_store.json
    fact_store = {
        "schema_version": "lit_fact_store.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "facts": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fs_path = task / "fact_store.json"
    _atomic_write_json(fs_path, fact_store)

    # shared_state.json
    plan_path = task / "research_plan.json"
    plan = {}
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    shared_state = {
        "schema_version": "lit_shared_state.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "wave_progress": 0,
        "claim_status": {
            claim["claim_id"]: "planned"
            for claim in plan.get("claim_matrix", [])
        },
        "reading_tasks": [],
        "open_questions": [],
        "evidence_conflicts": [],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    ss_path = task / "shared_state.json"
    _atomic_write_json(ss_path, shared_state)
    _sync_to_workspace(job_ctx, ss_path, "shared_state.json")

    return {
        "ok": True,
        "mode": "script",
        "phase": "phase05_shared_state_init",
        "job_id": job_ctx.job_id,
        "result": {"claim_count": len(shared_state["claim_status"])},
    }


# ═══════════════════════════════════════════════════════════
# Phase 06-10: Wave 1 — 三路采集 (3 角色, 数据独立)
# ═══════════════════════════════════════════════════════════

def _run_wave1_dispatch_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 06: Wave 1 dispatch — academic_scout / industry_scout / enterprise_scout.

    返回 needs_dispatch + has_more 让 Coordinator 串行调度 3 个角色。
    每次返回 1 个 manifest (sequential dispatch)。
    """
    task = _task_dir(runtime_root, job_ctx)

    # 检查哪些角色已完成
    completed_roles = []
    for role_key in LIT_WAVE1_ROLE_SLUGS:
        facts_path = task / f"{role_key}{FACTS_SUFFIX}"
        section_path = task / f"{role_key}{SECTION_SUFFIX}"
        if facts_path.exists() and facts_path.stat().st_size > 50 and section_path.exists() and section_path.stat().st_size > 10:
            completed_roles.append(role_key)

    remaining_roles = [r for r in LIT_WAVE1_ROLE_SLUGS if r not in completed_roles]

    if not remaining_roles:
        # 全部完成，推进到 collect
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {
                "type": "wave1_complete",
                "manifests": [],
                "has_more": False,
                "completed_roles": completed_roles,
            },
            "phase": "phase06_wave1_dispatch_prepare",
            "job_id": job_ctx.job_id,
        }

    # 返回下一个未完成角色的 manifest
    next_role = remaining_roles[0]
    has_more = len(remaining_roles) > 1

    plan_path = task / "research_plan.json"
    plan = {}
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # 构建 manifest (4-part prompt assembly: instruction + conclusion + tool_guide)
    system_prompt = _assemble_system_prompt(runtime_root, next_role, task_dir=task)
    assert len(system_prompt) > MIN_PROMPT_LENGTH, f"system_prompt too short for {next_role}: {len(system_prompt)} chars"

    # enterprise_scout: 追加 JSON 格式前置校验提示（dict 嵌套 + ASCII 直引号）
    if next_role == "enterprise_scout":
        system_prompt += (
            "\n\n## ⚠️⚠️ JSON 格式前置校验（enterprise_scout 专用）\n\n"
            "你的 enterprise_scout-facts.json 包含 **dict 嵌套**（companies 数组内嵌 management 对象），"
            "这是管线中最容易出错的 JSON 结构。写文件前必须遵守：\n\n"
            "1. **ASCII 直引号**：所有 key/value 用 `\"` (U+0022)，禁止中文引号 `\"…\"` `\"…\"`\n"
            "2. **dict/array 闭合**：每个 `{` 配 `}`，每个 `[` 配 `]`，嵌套层级写完后自查\n"
            "3. **尾逗号禁止**：最后一个元素后不能有逗号\n"
            "4. **数值不加引号**：`\"founded\": 2010` 不是 `\"founded\": \"2010\"`\n"
            "5. **写完后必须验证**：`python3 -c \"import json; json.load(open('enterprise_scout-facts.json'))\"`\n\n"
            "违反以上规则 → JSON 解析失败 → gate FAIL → 管线卡死。\n"
        )

    manifest = {
        "role": next_role,
        "slug": LIT_WAVE1_ROLE_SLUGS[next_role],
        "system_prompt": system_prompt,
        "output_path": str(task / f"{next_role}.md"),
        "brief_path": str(plan_path),
        "connectorIds": LIT_ROLE_CONNECTOR_IDS.get(next_role, []),
        "key_inputs": {
            "research_plan": str(plan_path),
            "shared_state": str(task / "shared_state.json"),
            "sub_topics": plan.get("sub_topics", []),
            "target_companies": plan.get("target_companies", []),
        },
        "expected_outputs": [
            f"{next_role}.md",
            f"{next_role}{FACTS_SUFFIX}",
            f"{next_role}{SECTION_SUFFIX}",
        ],
    }

    manifest_path = task / f"wave1_manifest_{next_role}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "dispatch_info": {
            "type": "wave1_dispatch",
            "manifests": [str(manifest_path)],
            "has_more": has_more,
            "current_role": next_role,
            "completed_roles": completed_roles,
            "remaining_roles": remaining_roles[1:],
        },
        "instruction": _lit_dispatch_instruction(next_role, LIT_WAVE1_ROLE_SLUGS[next_role], "phase07_wave1_dispatch_collect", has_more),
        "paused_after": "phase06_wave1_dispatch_prepare",
        "next_phase": "phase07_wave1_dispatch_collect",
        "phase": "phase06_wave1_dispatch_prepare",
        "job_id": job_ctx.job_id,
    }


def _wave1_collect_check(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Wave 1 单次 collect 检查 — 4 层防御验证每个角色的输出。"""
    task = _task_dir(runtime_root, job_ctx)

    role_status = {}
    all_complete = True

    for role_key in LIT_WAVE1_ROLE_SLUGS:
        facts_path = task / f"{role_key}{FACTS_SUFFIX}"
        section_path = task / f"{role_key}{SECTION_SUFFIX}"
        md_path = task / f"{role_key}.md"

        facts_ok = facts_path.exists() and facts_path.stat().st_size > 50
        section_ok = section_path.exists() and section_path.stat().st_size > 10
        md_ok = md_path.exists() and md_path.stat().st_size > 100

        # File stability: size must not grow for 5s (prevents reading half-written sidecar)
        if facts_ok:
            facts_stable = _file_stable(facts_path, interval=5)
        else:
            facts_stable = False

        # JSON 有效性 (with auto-repair for malformed sub-agent JSON)
        facts_valid = False
        if facts_ok and facts_stable:
            facts_valid = _safe_load_json_with_repair(facts_path) is not None

        section_valid = False
        if section_ok and _file_stable(section_path, interval=5):
            section_valid = _safe_load_json_with_repair(section_path) is not None

        complete = facts_valid and section_valid and md_ok
        role_status[role_key] = {
            "complete": complete,
            "facts_ok": facts_ok,
            "facts_valid": facts_valid,
            "section_ok": section_ok,
            "md_ok": md_ok,
        }
        if not complete:
            all_complete = False

    incomplete_roles = [r for r, s in role_status.items() if not s["complete"]]

    if incomplete_roles:
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {
                "type": "wave1_incomplete",
                "incomplete_roles": incomplete_roles,
                "role_status": role_status,
                "has_more": len(incomplete_roles) > 1,
            },
            "paused_after": "phase06_wave1_dispatch_prepare",
            "next_phase": "phase07_wave1_dispatch_collect",
            "phase": "phase07_wave1_dispatch_collect",
            "job_id": job_ctx.job_id,
        }

    return {
        "ok": True,
        "mode": "collect",
        "phase": "phase07_wave1_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {"all_complete": True, "role_status": role_status},
    }


def _run_wave1_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 07: Wave 1 collect — 带 sidecar 落盘重试的 4 层防御验证。"""
    task = _task_dir(runtime_root, job_ctx)

    def collect_fn():
        return _wave1_collect_check(runtime_root, job_ctx)

    return _collect_with_sidecar_retry(
        collect_fn,
        task_dir=task,
        collect_name="wave1_collect",
    )


def _run_wave1_evidence_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 08: Wave 1 Evidence Gate — 检查采集质量 + repair dispatch。"""
    task = _task_dir(runtime_root, job_ctx)
    gate_results = {"passed": True, "checks": {}, "warnings": [], "failures": []}

    # 检查 academic_scout
    academic_facts_path = task / f"academic_scout{FACTS_SUFFIX}"
    if academic_facts_path.exists():
        try:
            data = json.loads(academic_facts_path.read_text(encoding="utf-8"))
            papers = data.get("papers", [])
            total_papers = len(papers)
            sources = set(p.get("discovery_source", "") for p in papers)
            institutions = set()
            oa_count = 0
            for p in papers:
                for inst in p.get("institutions", []):
                    institutions.add(inst)
                if p.get("open_access_pdf_url") or p.get("full_text_available"):
                    oa_count += 1

            gate_results["checks"]["total_paper_count"] = {
                "actual": total_papers,
                "threshold": WAVE1_GATE_THRESHOLDS["total_paper_count"],
                "passed": total_papers >= WAVE1_GATE_THRESHOLDS["total_paper_count"],
            }
            gate_results["checks"]["source_diversity"] = {
                "actual": len(sources),
                "threshold": WAVE1_GATE_THRESHOLDS["source_diversity"],
                "passed": len(sources) >= WAVE1_GATE_THRESHOLDS["source_diversity"],
            }
            gate_results["checks"]["institution_coverage"] = {
                "actual": len(institutions),
                "threshold": WAVE1_GATE_THRESHOLDS["institution_coverage"],
                "passed": len(institutions) >= WAVE1_GATE_THRESHOLDS["institution_coverage"],
            }
            oa_rate = oa_count / max(total_papers, 1)
            gate_results["checks"]["oa_url_rate"] = {
                "actual": round(oa_rate, 2),
                "threshold": WAVE1_GATE_THRESHOLDS["oa_url_rate"],
                "passed": oa_rate >= WAVE1_GATE_THRESHOLDS["oa_url_rate"],
            }
        except Exception as e:
            gate_results["failures"].append(f"academic_scout facts parse error: {e}")
    else:
        gate_results["failures"].append("academic_scout-facts.json not found")
        gate_results["passed"] = False

    # 检查 industry_scout
    industry_facts_path = task / f"industry_scout{FACTS_SUFFIX}"
    if industry_facts_path.exists():
        try:
            data = json.loads(industry_facts_path.read_text(encoding="utf-8"))
            facts = data.get("facts", [])
            broker_count = sum(1 for f in facts if f.get("type") == "broker_report")
            report_count = sum(1 for f in facts if f.get("type") == "industry_report")
            news_count = sum(1 for f in facts if f.get("type") == "news")

            gate_results["checks"]["broker_report_count"] = {
                "actual": broker_count,
                "threshold": WAVE1_GATE_THRESHOLDS["broker_report_count"],
                "passed": broker_count >= WAVE1_GATE_THRESHOLDS["broker_report_count"],
            }
            gate_results["checks"]["industry_report_count"] = {
                "actual": report_count,
                "threshold": WAVE1_GATE_THRESHOLDS["industry_report_count"],
                "passed": report_count >= WAVE1_GATE_THRESHOLDS["industry_report_count"],
            }
            gate_results["checks"]["news_count"] = {
                "actual": news_count,
                "threshold": WAVE1_GATE_THRESHOLDS["news_count"],
                "passed": news_count >= WAVE1_GATE_THRESHOLDS["news_count"],
            }
        except Exception as e:
            gate_results["failures"].append(f"industry_scout facts parse error: {e}")
    else:
        gate_results["failures"].append("industry_scout-facts.json not found")
        gate_results["passed"] = False

    # 检查 enterprise_scout
    enterprise_facts_path = task / f"enterprise_scout{FACTS_SUFFIX}"
    if enterprise_facts_path.exists():
        try:
            data = json.loads(enterprise_facts_path.read_text(encoding="utf-8"))
            companies = data.get("companies", [])
            gate_results["checks"]["company_profiles"] = {
                "actual": len(companies),
                "threshold": WAVE1_GATE_THRESHOLDS["company_profiles"],
                "passed": len(companies) >= WAVE1_GATE_THRESHOLDS["company_profiles"],
            }
        except Exception as e:
            gate_results["failures"].append(f"enterprise_scout facts parse error: {e}")
    else:
        gate_results["failures"].append("enterprise_scout-facts.json not found")
        gate_results["passed"] = False

    # 判定 pass/fail
    for check_name, check in gate_results["checks"].items():
        if not check["passed"]:
            gate_results["warnings"].append(f"{check_name}: {check['actual']} < {check['threshold']}")

    # ── Severity levels: tag each check ──
    SEVERITY_MAP = {
        "total_paper_count": "BLOCKING",
        "source_diversity": "MEDIUM",
        "institution_coverage": "MEDIUM",
        "oa_url_rate": "LOW",
        "broker_report_count": "MEDIUM",
        "industry_report_count": "MEDIUM",
        "news_count": "LOW",
        "company_profiles": "MEDIUM",
    }
    for check_name, check in gate_results["checks"].items():
        check["severity"] = SEVERITY_MAP.get(check_name, "LOW")

    # 聚合 verdict
    blocking_failures = [n for n, c in gate_results["checks"].items()
                         if not c["passed"] and c.get("severity") == "BLOCKING"]
    medium_failures = [n for n, c in gate_results["checks"].items()
                       if not c["passed"] and c.get("severity") == "MEDIUM"]
    gate_results["verdict"] = (
        "BLOCKING" if blocking_failures
        else "WARN" if medium_failures
        else "PASS"
    )

    # 写 gate 结果 (atomic)
    gate_path = task / "wave1_gate.json"
    _atomic_write_json(gate_path, gate_results)
    _sync_to_workspace(job_ctx, gate_path, "wave1_gate.json")

    # BLOCKING: 完全没有任何数据
    has_any_data = any(
        gate_results["checks"].get(k, {}).get("actual", 0) > 0
        for k in ("total_paper_count", "broker_report_count", "company_profiles")
    )
    if not has_any_data:
        return {"ok": False, "phase": "phase08_wave1_evidence_gate", "job_id": job_ctx.job_id,
                "result": gate_results, "error": "BLOCKING: Wave 1 produced zero data"}

    # ── PRISMA 漏斗完整性检查 (academic_scout) ──
    academic_section_path = task / f"academic_scout{SECTION_SUFFIX}"
    if academic_section_path.exists():
        try:
            sec_data = json.loads(academic_section_path.read_text(encoding="utf-8"))
            prisma = sec_data.get("prisma_funnel", {})
            if not prisma:
                gate_results["warnings"].append("PRISMA funnel: missing from section.json")
            else:
                stages = ["identification_total", "duplicates_removed", "screening_excluded", "included"]
                missing_stages = [s for s in stages if s not in prisma]
                if missing_stages:
                    gate_results["warnings"].append(f"PRISMA funnel: missing stages {missing_stages}")
                else:
                    # 筛选率合理性: screening_excluded / after_dedup 应在 30-80%
                    after_dedup = prisma.get("identification_total", 0) - prisma.get("duplicates_removed", 0)
                    if after_dedup > 0:
                        screening_rate = prisma.get("screening_excluded", 0) / after_dedup
                        if screening_rate < 0.3:
                            gate_results["warnings"].append(
                                f"PRISMA screening rate {screening_rate:.0%} < 30%: 筛选太松，可能引入噪音")
                        elif screening_rate > 0.8:
                            gate_results["warnings"].append(
                                f"PRISMA screening rate {screening_rate:.0%} > 80%: 搜索噪音太大")
        except Exception:
            gate_results["warnings"].append("PRISMA funnel: section.json parse error")

    # ── Gate Repair: FAIL checks → 生成 repair manifest ──
    failed_checks = [name for name, c in gate_results["checks"].items() if not c["passed"]]
    gate_results["passed"] = len(failed_checks) == 0

    if failed_checks and gate_results["warnings"]:
        # 检查 repair 次数限制
        repair_state_path = task / "wave1_repair_state.json"
        repair_attempt = 0
        if repair_state_path.exists():
            try:
                repair_attempt = json.loads(repair_state_path.read_text(encoding="utf-8")).get("attempt", 0)
            except Exception:
                pass

        if repair_attempt < GATE_REPAIR_MAX_ATTEMPTS:
            # 确定需要 repair 的角色
            roles_needing_repair = set()
            for check_name in failed_checks:
                if check_name in ("total_paper_count", "source_diversity", "institution_coverage", "oa_url_rate"):
                    roles_needing_repair.add("academic_scout")
                elif check_name in ("broker_report_count", "industry_report_count", "news_count"):
                    roles_needing_repair.add("industry_scout")
                elif check_name == "company_profiles":
                    roles_needing_repair.add("enterprise_scout")

            if roles_needing_repair:
                # 记录 repair 次数
                repair_state_path.write_text(
                    json.dumps({"attempt": repair_attempt + 1, "failed_checks": failed_checks},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                first_role = sorted(roles_needing_repair)[0]
                remaining_roles = sorted(roles_needing_repair)[1:]
                failure_desc = ", ".join(failed_checks)

                manifest_path = _build_lit_repair_manifest(
                    task, role=first_role, slug=LIT_WAVE1_ROLE_SLUGS[first_role],
                    failure_reason=f"Gate checks failed: {failure_desc}",
                    gate_phase="phase08_wave1_evidence_gate",
                    runtime_root=runtime_root,
                )

                print(f"  🔧 [wave1_gate] repair attempt {repair_attempt + 1}/{GATE_REPAIR_MAX_ATTEMPTS}, "
                      f"role={first_role}, failed={failure_desc}", flush=True)

                # 如果有多个角色需要 repair，生成额外 manifest
                remaining_manifests = []
                for r in remaining_roles:
                    mp = _build_lit_repair_manifest(
                        task, role=r, slug=LIT_WAVE1_ROLE_SLUGS[r],
                        failure_reason=f"Gate checks failed: {failure_desc}",
                        gate_phase="phase08_wave1_evidence_gate",
                        runtime_root=runtime_root,
                    )
                    remaining_manifests.append(mp)

                has_more = len(remaining_manifests) > 0
                return {
                    "ok": True,
                    "needs_dispatch": True,
                    "has_more": has_more,
                    "mode": "lit_wave_repair",
                    "phase": "phase08_wave1_evidence_gate",
                    "job_id": job_ctx.job_id,
                    "dispatch_info": {
                        "manifests": [manifest_path],
                        "remaining_manifests": remaining_manifests,
                        "roles": sorted(roles_needing_repair),
                        "is_repair": True,
                    },
                    "result": gate_results,
                    "instruction": _repair_instruction(
                        "phase08_wave1_evidence_gate", has_more, len(remaining_manifests)),
                }

    return {
        "ok": True,  # MEDIUM/LOW warnings don't block
        "mode": "evidence_gate",
        "phase": "phase08_wave1_evidence_gate",
        "job_id": job_ctx.job_id,
        "result": gate_results,
    }


def _run_wave1_fact_store_merge(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09: Wave 1 合并 — 3 个 scout 的 facts → fact_store.json。"""
    task = _task_dir(runtime_root, job_ctx)
    merged_facts = []

    # 读取 academic_scout
    academic_path = task / f"academic_scout{FACTS_SUFFIX}"
    if academic_path.exists():
        try:
            data = json.loads(academic_path.read_text(encoding="utf-8"))
            for p in data.get("papers", []):
                p["wave"] = 1
                p["scout"] = "academic"
                merged_facts.append(p)
        except Exception:
            pass

    # 读取 industry_scout
    industry_path = task / f"industry_scout{FACTS_SUFFIX}"
    if industry_path.exists():
        try:
            data = json.loads(industry_path.read_text(encoding="utf-8"))
            for f in data.get("facts", []):
                f["wave"] = 1
                f["scout"] = "industry"
                merged_facts.append(f)
        except Exception:
            pass

    # 读取 enterprise_scout
    enterprise_path = task / f"enterprise_scout{FACTS_SUFFIX}"
    if enterprise_path.exists():
        try:
            data = json.loads(enterprise_path.read_text(encoding="utf-8"))
            for c in data.get("companies", []):
                c["wave"] = 1
                c["scout"] = "enterprise"
                merged_facts.append(c)
        except Exception:
            pass

    # 写入 fact_store (atomic — prevents half-read by other processes)
    fact_store = {
        "schema_version": "lit_fact_store.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "facts": merged_facts,
        "stats": {
            "total": len(merged_facts),
            "academic": sum(1 for f in merged_facts if f.get("scout") == "academic"),
            "industry": sum(1 for f in merged_facts if f.get("scout") == "industry"),
            "enterprise": sum(1 for f in merged_facts if f.get("scout") == "enterprise"),
        },
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fs_path = task / "fact_store.json"
    _atomic_write_json(fs_path, fact_store)
    _sync_to_workspace(job_ctx, fs_path, "fact_store.json")

    return {
        "ok": True,
        "mode": "merge",
        "phase": "phase09_wave1_fact_store_merge",
        "job_id": job_ctx.job_id,
        "result": fact_store["stats"],
    }


def _run_wave1_shared_state_refresh(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 10: Wave 1 Shared State Refresh — 生成 per-sub_topic reading_tasks。

    这是 Wave 1 → Wave 2 的关键桥梁: 把 academic_scout 搜到的论文
    按 sub_topic 分组排序，生成 deep_reader 的阅读任务清单。
    """
    task = _task_dir(runtime_root, job_ctx)

    # 读 research plan 获取 sub_topics
    plan_path = task / "research_plan.json"
    plan = {}
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sub_topics = plan.get("sub_topics", [])

    # 读 academic_scout facts
    academic_path = task / f"academic_scout{FACTS_SUFFIX}"
    papers_by_subtopic: dict[str, list] = {}
    all_academic_papers: list[dict] = []  # 全部论文（用于模糊匹配兜底）
    if academic_path.exists():
        try:
            data = json.loads(academic_path.read_text(encoding="utf-8"))
            for p in data.get("papers", []):
                st = p.get("sub_topic", "general")
                papers_by_subtopic.setdefault(st, []).append(p)
                all_academic_papers.append(p)
        except Exception:
            pass

    # 生成 reading_tasks（精确匹配 + 模糊兜底）
    reading_tasks = []
    used_paper_ids: set[str] = set()  # 防止一篇被分到多个 sub_topic
    for st in sub_topics:
        # 第一层：精确匹配
        papers = list(papers_by_subtopic.get(st, []))
        for p in papers:
            used_paper_ids.add(p.get("fact_id", ""))

        # 第二层：模糊匹配兜底（针对精确匹配为空的 sub_topic）
        if len(papers) < 3:
            st_lower = st.lower()
            # 提取 sub_topic 关键词（≥2 字符的有意义词）
            keywords = [w for w in st_lower.replace("-", " ").replace("_", " ").split() if len(w) >= 2]
            for p in all_academic_papers:
                pid = p.get("fact_id", "")
                if pid in used_paper_ids:
                    continue
                # 检查论文 title/abstract/keywords 是否包含 sub_topic 关键词
                text_fields = " ".join([
                    p.get("title", ""),
                    p.get("abstract", ""),
                    " ".join(p.get("keywords", [])),
                    p.get("sub_topic", ""),
                ]).lower()
                match_count = sum(1 for kw in keywords if kw in text_fields)
                # 匹配 ≥50% 关键词 → 归入此 sub_topic
                if keywords and match_count / len(keywords) >= 0.5:
                    papers.append(p)
                    used_paper_ids.add(pid)
        # 按 relevance_score × citation_count 排序
        papers.sort(key=lambda p: (
            p.get("relevance_score", 0) * 0.6 +
            min(p.get("citation_count", 0) / 100, 1.0) * 0.4
        ), reverse=True)

        doc_ids = [p.get("fact_id", "") for p in papers]
        fulltext_available = sum(1 for p in papers if p.get("full_text_available") or p.get("open_access_pdf_url"))

        reading_tasks.append({
            "sub_topic": st,
            "doc_ids": doc_ids,
            "total_docs": len(papers),
            "fulltext_available": fulltext_available,
            "abstract_only": len(papers) - fulltext_available,
            "priority_order": doc_ids,
        })

    # 更新 shared_state
    ss_path = task / "shared_state.json"
    shared_state = {}
    if ss_path.exists():
        shared_state = json.loads(ss_path.read_text(encoding="utf-8"))

    shared_state["wave_progress"] = 1
    shared_state["reading_tasks"] = reading_tasks
    shared_state["claim_status"] = {
        **shared_state.get("claim_status", {}),
        **{f"CLAIM-{i+1:03d}": "supported" for i in range(len(reading_tasks)) if reading_tasks[i]["total_docs"] > 0},
    }
    shared_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    _atomic_write_json(ss_path, shared_state)
    _sync_to_workspace(job_ctx, ss_path, "shared_state.json")

    # ── Shared State Page (human-readable dashboard for Wave 1) ──
    page_lines = [
        f"# LIT Shared State — After Wave 1",
        f"",
        f"- **Wave Progress**: 1/3",
        f"- **Updated**: {shared_state.get('updated_at', 'N/A')}",
        f"- **Sub-topics**: {len(reading_tasks)}",
        f"- **Total Papers**: {sum(t['total_docs'] for t in reading_tasks)}",
        f"- **Total Full-text**: {sum(t['fulltext_available'] for t in reading_tasks)}",
        f"",
        f"## Reading Tasks",
        f"| Sub-topic | Papers | Full-text | Abstract-only |",
        f"|-----------|--------|-----------|---------------|",
    ]
    for rt in reading_tasks:
        page_lines.append(f"| {rt['sub_topic']} | {rt['total_docs']} | {rt['fulltext_available']} | {rt['abstract_only']} |")
    page_lines.extend([
        f"",
        f"## Claim Status",
        f"| Claim | Status |",
        f"|-------|--------|",
    ])
    for cid, st in shared_state.get("claim_status", {}).items():
        page_lines.append(f"| {cid} | {st} |")
    page_lines.extend([
        f"",
        f"## Wave 2 Handoff",
        f"- Read `fact_store.json` for all collected evidence",
        f"- Read `academic_scout.md` / `industry_scout.md` / `enterprise_scout.md` for scout reports",
        f"- Read `shared_state.json` for machine-readable progress",
    ])
    page_path = task / "shared_state_page.md"
    _atomic_write_text(page_path, "\n".join(page_lines) + "\n")

    return {
        "ok": True,
        "mode": "shared_state_refresh",
        "phase": "phase10_wave1_shared_state_refresh",
        "job_id": job_ctx.job_id,
        "result": {
            "sub_topic_count": len(reading_tasks),
            "total_papers": sum(t["total_docs"] for t in reading_tasks),
            "total_fulltext": sum(t["fulltext_available"] for t in reading_tasks),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 11-14: Wave 2 — 深读 + 分析 (2 角色, 有前后依赖)
# ═══════════════════════════════════════════════════════════

def _run_wave2_dispatch_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11: Wave 2 dispatch — per-sub_topic deep_reader (has_more) → tech_strategist.

    v2: deep_reader 按 sub_topic 逐个派发，每个 sub_topic 读全部论文。
    所有 sub_topic 完成后派发 tech_strategist。
    """
    task = _task_dir(runtime_root, job_ctx)

    # 读 shared_state 获取 reading_tasks
    ss_path = task / "shared_state.json"
    shared_state = {}
    if ss_path.exists():
        shared_state = json.loads(ss_path.read_text(encoding="utf-8"))
    reading_tasks = shared_state.get("reading_tasks", [])

    # 跳过空 sub_topic
    non_empty = [(i, rt) for i, rt in enumerate(reading_tasks) if rt.get("total_docs", 0) > 0]

    # 检查每个 sub_topic 的 deep_reader 完成状态
    sub_topic_status = []
    for idx, rt in non_empty:
        notes_file = task / f"sub_topic_{idx + 1}{NOTES_SUFFIX}"
        done = notes_file.exists() and notes_file.stat().st_size > 100
        if done and _file_stable(notes_file, interval=3):
            if _safe_load_json_with_repair(notes_file) is not None:
                sub_topic_status.append({"index": idx + 1, "sub_topic": rt["sub_topic"], "done": True})
            else:
                sub_topic_status.append({"index": idx + 1, "sub_topic": rt["sub_topic"], "done": False})
        else:
            sub_topic_status.append({"index": idx + 1, "sub_topic": rt["sub_topic"], "done": False})

    all_sub_topics_done = all(s["done"] for s in sub_topic_status)

    # 检查 tech_strategist 完成状态
    assessment_path = task / "tech_assessment.md"
    strategist_done = assessment_path.exists() and assessment_path.stat().st_size > 200

    if all_sub_topics_done and strategist_done:
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {"type": "wave2_complete", "manifests": [], "has_more": False},
            "phase": "phase11_wave2_dispatch_prepare",
            "job_id": job_ctx.job_id,
        }

    # 找到第一个未完成的 sub_topic
    incomplete_sub = None
    for s in sub_topic_status:
        if not s["done"]:
            incomplete_sub = s
            break

    # 4-part prompt assembly
    system_prompt = _assemble_system_prompt(runtime_root, "deep_reader" if incomplete_sub else "tech_strategist", task_dir=task)
    assert len(system_prompt) > MIN_PROMPT_LENGTH, f"system_prompt too short: {len(system_prompt)} chars"

    if incomplete_sub:
        # 派发下一个 sub_topic 的 deep_reader
        idx = incomplete_sub["index"]
        rt = reading_tasks[idx - 1]
        has_more = any(s["done"] is False for s in sub_topic_status if s["index"] > idx) or not strategist_done
        next_role = "deep_reader"
        sub_topic_name = incomplete_sub["sub_topic"]

        manifest = {
            "role": "deep_reader",
            "slug": f"deep_reader_sub{idx}",
            "system_prompt": system_prompt,
            "output_path": str(task / f"{next_role}.md"),
            "brief_path": str(task / "shared_state.json"),
            "connectorIds": LIT_ROLE_CONNECTOR_IDS.get("deep_reader", []),
            "key_inputs": {
                "shared_state": str(task / "shared_state.json"),
                "fact_store": str(task / "fact_store.json"),
                "assigned_sub_topic": sub_topic_name,
                "sub_topic_index": idx,
                "doc_ids": rt.get("doc_ids", []),
                "total_docs": rt.get("total_docs", 0),
                "priority_order": rt.get("priority_order", []),
            },
            "expected_outputs": [f"sub_topic_{idx}{NOTES_SUFFIX}"],
        }
        manifest_path = task / f"wave2_manifest_deep_reader_sub{idx}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Per-sub_topic dispatch instruction
        instruction = (
            f"MANDATORY: Read the manifest JSON file at the path below.\n"
            f"Use the Agent tool with these EXACT parameters:\n"
            f"  - name = 'deep_reader_sub{idx}'\n"
            f"  - team_name = 'lit-{{task_id}}'\n"
            f"  - mode = 'bypassPermissions'\n"
            f"  - prompt = manifest's 'system_prompt' field (COMPLETE, do NOT simplify)\n"
            f"  - connectorIds = manifest's 'connectorIds' field\n\n"
            f"## ⚠️ 此子代理负责 sub_topic #{idx}: {sub_topic_name}\n"
            f"共 {rt.get('total_docs', 0)} 篇论文，全部阅读（全文优先、摘要兜底）。\n\n"
            f"## ⚠️ 单文件验证\n"
            f"子代理输出 1 个文件:\n"
            f"  - sub_topic_{idx}{NOTES_SUFFIX} (>100 bytes, valid JSON)\n"
            f"确认文件存在且 JSON 有效后，用 start_phase='phase12_wave2_dispatch_collect' 恢复管线。\n"
        )
        if has_more:
            instruction += "\n⚠️ has_more=True: 还有更多 sub_topic 待派发，恢复后会返回下一个 manifest。\n"
        else:
            instruction += "\n✅ 这是最后一个 deep_reader sub_topic，恢复后将派发 tech_strategist。\n"
        instruction += (
            "\n## ⚠️ 绝对禁止:\n"
            "- 禁止在单条消息中派发多个 Agent tool_use\n"
            "- 禁止简化或截断 manifest 中的 system_prompt\n"
        )

        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {
                "type": "wave2_dispatch",
                "manifests": [str(manifest_path)],
                "has_more": has_more,
                "current_role": "deep_reader",
                "sub_topic": sub_topic_name,
                "sub_topic_index": idx,
                "total_sub_topics": len(non_empty),
                "completed": sum(1 for s in sub_topic_status if s["done"]),
            },
            "instruction": instruction,
            "paused_after": "phase11_wave2_dispatch_prepare",
            "next_phase": "phase12_wave2_dispatch_collect",
            "phase": "phase11_wave2_dispatch_prepare",
            "job_id": job_ctx.job_id,
        }

    # 所有 sub_topic 完成 → 派发 tech_strategist
    next_role = "tech_strategist"
    manifest = {
        "role": next_role,
        "slug": next_role,
        "system_prompt": system_prompt,
        "output_path": str(task / f"{next_role}.md"),
        "brief_path": str(task / "shared_state.json"),
        "connectorIds": LIT_ROLE_CONNECTOR_IDS.get(next_role, []),
        "key_inputs": {
            "shared_state": str(task / "shared_state.json"),
            "fact_store": str(task / "fact_store.json"),
            "reading_notes": "sub_topic_*_reading_notes.json",
            "quality_summary": "shared_state.json → quality_summary",
        },
        "expected_outputs": ["tech_assessment.md", "tech_assessment-facts.json", "tech_assessment-section.json"],
    }
    manifest_path = task / f"wave2_manifest_{next_role}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "dispatch_info": {
            "type": "wave2_dispatch",
            "manifests": [str(manifest_path)],
            "has_more": False,
            "current_role": next_role,
        },
        "instruction": _lit_dispatch_instruction(next_role, next_role, "phase12_wave2_dispatch_collect", False),
        "paused_after": "phase11_wave2_dispatch_prepare",
        "next_phase": "phase12_wave2_dispatch_collect",
        "phase": "phase11_wave2_dispatch_prepare",
        "job_id": job_ctx.job_id,
    }


def _wave2_collect_check(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Wave 2 单次 collect 检查 — per-sub_topic deep_reader + tech_strategist。

    v2: 检查当前 sub_topic 的 notes 文件，或 tech_strategist 的三文件。
    如果当前是 deep_reader sub_topic，只检查对应索引的 notes 文件。
    如果当前是 tech_strategist，检查 md + sidecar 三文件。
    """
    task = _task_dir(runtime_root, job_ctx)

    # 读 shared_state 获取 reading_tasks 和当前 dispatch 状态
    ss_path = task / "shared_state.json"
    shared_state = {}
    if ss_path.exists():
        shared_state = json.loads(ss_path.read_text(encoding="utf-8"))
    reading_tasks = shared_state.get("reading_tasks", [])
    non_empty = [(i, rt) for i, rt in enumerate(reading_tasks) if rt.get("total_docs", 0) > 0]

    # 检查所有 sub_topic 的 notes 完成状态
    all_sub_topics_done = True
    for idx, rt in non_empty:
        notes_file = task / f"sub_topic_{idx + 1}{NOTES_SUFFIX}"
        if not (notes_file.exists() and notes_file.stat().st_size > 100):
            all_sub_topics_done = False
            break
        if not _file_stable(notes_file, interval=5):
            all_sub_topics_done = False
            break
        if _safe_load_json_with_repair(notes_file) is None:
            all_sub_topics_done = False
            break

    # 检查 tech_strategist 产出 (md + sidecar 三文件)
    assessment_path = task / "tech_assessment.md"
    assessment_facts = task / "tech_assessment-facts.json"
    assessment_section = task / "tech_assessment-section.json"
    strategist_ok = (
        assessment_path.exists() and assessment_path.stat().st_size > 200
        and assessment_facts.exists() and assessment_facts.stat().st_size > 10
        and assessment_section.exists() and assessment_section.stat().st_size > 10
    )

    # strategist: stability + JSON repair
    if strategist_ok:
        if not _file_stable(assessment_facts, interval=5):
            strategist_ok = False
        elif _safe_load_json_with_repair(assessment_facts) is None:
            strategist_ok = False
        if strategist_ok and _file_stable(assessment_section, interval=5):
            if _safe_load_json_with_repair(assessment_section) is None:
                strategist_ok = False

    all_complete = all_sub_topics_done and strategist_ok

    if not all_complete:
        incomplete = []
        if not all_sub_topics_done:
            # 找出哪些 sub_topic 未完成
            for idx, rt in non_empty:
                notes_file = task / f"sub_topic_{idx + 1}{NOTES_SUFFIX}"
                if not (notes_file.exists() and notes_file.stat().st_size > 100):
                    incomplete.append(f"deep_reader_sub{idx + 1}")
        if not strategist_ok:
            incomplete.append("tech_strategist")
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {
                "type": "wave2_incomplete",
                "incomplete_roles": incomplete,
                "has_more": len(incomplete) > 1,
            },
            "paused_after": "phase11_wave2_dispatch_prepare",
            "next_phase": "phase12_wave2_dispatch_collect",
            "phase": "phase12_wave2_dispatch_collect",
            "job_id": job_ctx.job_id,
        }

    # 全部完成 → 合并 quality_summary 到 shared_state
    if all_sub_topics_done:
        _merge_quality_summary(task, shared_state, non_empty)

    return {
        "ok": True,
        "mode": "collect",
        "phase": "phase12_wave2_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "notes_count": len(non_empty),
            "assessment_ok": strategist_ok,
        },
    }


def _run_wave2_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 12: Wave 2 collect — 带 sidecar 落盘重试。"""
    task = _task_dir(runtime_root, job_ctx)

    def collect_fn():
        return _wave2_collect_check(runtime_root, job_ctx)

    return _collect_with_sidecar_retry(
        collect_fn,
        task_dir=task,
        collect_name="wave2_collect",
    )


def _run_wave2_evidence_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 13: Wave 2 Evidence Gate — 含质量评估覆盖率 + counter_evidence/data_gaps 检查。"""
    task = _task_dir(runtime_root, job_ctx)
    gate_results = {"passed": True, "checks": {}, "warnings": [], "failures": []}

    # ── deep_reader 覆盖率 + 质量评估检查 ──
    notes_files = list(task.glob(f"sub_topic_*{NOTES_SUFFIX}"))
    gate_results["checks"]["reading_notes_count"] = {
        "actual": len(notes_files),
        "threshold": 1,
        "passed": len(notes_files) >= 1,
    }

    # 质量评估覆盖率: 检查 notes 中是否包含 quality_assessment
    total_notes = 0
    notes_with_qa = 0
    quality_tier_counts = {"A": 0, "B": 0, "C": 0, "unknown": 0}
    for nf in notes_files:
        try:
            data = json.loads(nf.read_text(encoding="utf-8"))
            notes_list = data.get("notes", data.get("reading_notes", []))
            if isinstance(notes_list, list):
                for note in notes_list:
                    total_notes += 1
                    qa = note.get("quality_assessment", {})
                    if qa:
                        notes_with_qa += 1
                        # 容错读取: quality_tier > overall_grade > grade
                        tier = (
                            qa.get("quality_tier")
                            or qa.get("overall_grade")
                            or qa.get("grade")
                            or "unknown"
                        )
                        tier = str(tier).upper().strip()
                        if tier not in ("A", "B", "C"):
                            tier = "unknown"
                        quality_tier_counts[tier] = quality_tier_counts.get(tier, 0) + 1
                    else:
                        quality_tier_counts["unknown"] += 1
        except Exception:
            pass

    if total_notes > 0:
        qa_coverage = notes_with_qa / total_notes
        gate_results["checks"]["quality_assessment_coverage"] = {
            "actual": round(qa_coverage, 2),
            "threshold": 1.0,
            "passed": qa_coverage >= 0.95,  # 允许少量遗漏 (摘要级笔记可能无法打分)
        }
        a_rate = quality_tier_counts["A"] / max(notes_with_qa, 1)
        gate_results["checks"]["a_tier_evidence_rate"] = {
            "actual": round(a_rate, 2),
            "threshold": 0.20,
            "passed": a_rate >= 0.20,
        }
        gate_results["checks"]["quality_tier_distribution"] = {
            "actual": quality_tier_counts,
            "threshold": "A>=20%",
            "passed": a_rate >= 0.20,
        }

    # ── tech_strategist 检查 (增强版) ──
    assessment_path = task / "tech_assessment.md"
    if assessment_path.exists():
        content = assessment_path.read_text(encoding="utf-8")
        has_trl = "TRL" in content or "trl" in content.lower()
        has_gartner = "Gartner" in content or "gartner" in content.lower()
        has_route_table = "|" in content and "路线" in content
        has_counter_evidence = "Counter Evidence" in content or "反方证据" in content or "counter evidence" in content.lower()
        has_data_gaps = "Data Gaps" in content or "数据缺口" in content or "data gaps" in content.lower()

        gate_results["checks"]["trl_assessment"] = {"actual": has_trl, "threshold": True, "passed": has_trl}
        gate_results["checks"]["gartner_position"] = {"actual": has_gartner, "threshold": True, "passed": has_gartner}
        gate_results["checks"]["route_comparison"] = {"actual": has_route_table, "threshold": True, "passed": has_route_table}
        gate_results["checks"]["counter_evidence"] = {
            "actual": has_counter_evidence, "threshold": True, "passed": has_counter_evidence}
        gate_results["checks"]["data_gaps"] = {
            "actual": has_data_gaps, "threshold": True, "passed": has_data_gaps}
    else:
        # tech_assessment 缺失 → 降级为 WARN（不阻断），report_writer 走 fallback 模式
        gate_results["warnings"].append("tech_assessment.md not found — will use fallback mode in report_writer")
        gate_results["checks"]["tech_assessment_exists"] = {
            "actual": False,
            "threshold": True,
            "passed": False,
            "severity": "MEDIUM",  # 不阻断，只是 WARN
        }

    # ── Severity levels ──
    W2_SEVERITY = {
        "reading_notes_count": "BLOCKING",
        "quality_assessment_coverage": "MEDIUM",
        "a_tier_evidence_rate": "MEDIUM",
        "quality_tier_distribution": "LOW",
        "trl_assessment": "BLOCKING",
        "gartner_position": "MEDIUM",
        "route_comparison": "MEDIUM",
        "counter_evidence": "MEDIUM",
        "data_gaps": "MEDIUM",
    }
    for check_name, check in gate_results["checks"].items():
        check["severity"] = W2_SEVERITY.get(check_name, "LOW")
        if not check["passed"]:
            gate_results["warnings"].append(f"{check_name}: not met")

    blocking_failures = [n for n, c in gate_results["checks"].items()
                         if not c["passed"] and c.get("severity") == "BLOCKING"]
    medium_failures = [n for n, c in gate_results["checks"].items()
                       if not c["passed"] and c.get("severity") == "MEDIUM"]
    gate_results["verdict"] = (
        "BLOCKING" if blocking_failures
        else "WARN" if medium_failures
        else "PASS"
    )

    gate_path = task / "wave2_gate.json"
    _atomic_write_json(gate_path, gate_results)
    _sync_to_workspace(job_ctx, gate_path, "wave2_gate.json")

    return {
        "ok": True,
        "mode": "evidence_gate",
        "phase": "phase13_wave2_evidence_gate",
        "job_id": job_ctx.job_id,
        "result": gate_results,
    }


def _run_wave2_shared_state_refresh(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 14: Wave 2 Shared State Refresh。"""
    task = _task_dir(runtime_root, job_ctx)
    ss_path = task / "shared_state.json"
    shared_state = {}
    if ss_path.exists():
        shared_state = json.loads(ss_path.read_text(encoding="utf-8"))

    shared_state["wave_progress"] = 2
    shared_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    _atomic_write_json(ss_path, shared_state)
    _sync_to_workspace(job_ctx, ss_path, "shared_state.json")

    # ── Shared State Page (human-readable dashboard) ──
    page_lines = [
        f"# LIT Shared State — After Wave 2",
        f"",
        f"- **Wave Progress**: 2/3",
        f"- **Updated**: {shared_state.get('updated_at', 'N/A')}",
        f"- **Claim Status**: {json.dumps(shared_state.get('claim_status', {}), ensure_ascii=False)}",
        f"- **Evidence Conflicts**: {len(shared_state.get('evidence_conflicts', []))}",
        f"- **Open Questions**: {len(shared_state.get('open_questions', []))}",
        f"",
        f"## Wave 2 Sub-Agent Handoff",
        f"",
        f"- Read `tech_assessment.md` for TRL/Gartner/route analysis",
        f"- Read `sub_topic_*_reading_notes.json` for detailed reading notes",
        f"- Read `fact_store.json` for all collected evidence",
        f"- Read `shared_state.json` for machine-readable progress",
    ]
    page_path = task / "shared_state_page.md"
    _atomic_write_text(page_path, "\n".join(page_lines) + "\n")

    return {
        "ok": True,
        "mode": "shared_state_refresh",
        "phase": "phase14_wave2_shared_state_refresh",
        "job_id": job_ctx.job_id,
        "result": {"wave_progress": 2},
    }


# ═══════════════════════════════════════════════════════════
# Phase 15-20: Wave 3 + Quality Chain + Delivery
# ═══════════════════════════════════════════════════════════

def _run_wave3_dispatch_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 15: Wave 3 dispatch — report_writer。"""
    task = _task_dir(runtime_root, job_ctx)

    report_path = task / "report.md"
    if report_path.exists() and report_path.stat().st_size > 500:
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {"type": "wave3_complete", "manifests": [], "has_more": False},
            "phase": "phase15_wave3_dispatch_prepare",
            "job_id": job_ctx.job_id,
        }

    # 4-part prompt assembly: instruction + conclusion + tool_guide
    system_prompt = _assemble_system_prompt(runtime_root, "report_writer", task_dir=task)
    assert len(system_prompt) > MIN_PROMPT_LENGTH, f"system_prompt too short for report_writer: {len(system_prompt)} chars"

    # 检查 tech_assessment 是否存在 → 决定 fallback 模式
    tech_assessment_path = task / "tech_assessment.md"
    fallback_mode = not (tech_assessment_path.exists() and tech_assessment_path.stat().st_size > 200)

    key_inputs = {
        "reading_notes": "sub_topic_*_reading_notes.json",
        "industry_facts": str(task / f"industry_scout{FACTS_SUFFIX}"),
        "enterprise_facts": str(task / f"enterprise_scout{FACTS_SUFFIX}"),
        "shared_state": str(task / "shared_state.json"),
        "shared_state_page": str(task / "shared_state_page.md"),
    }
    if not fallback_mode:
        key_inputs["tech_assessment"] = str(tech_assessment_path)

    # fallback 模式 → 追加额外数据源 + 降级说明
    if fallback_mode:
        key_inputs["academic_facts"] = str(task / f"academic_scout{FACTS_SUFFIX}")
        system_prompt += (
            "\n\n## ⚠️ FALLBACK 模式\n\n"
            "tech_strategist 阶段被跳过，tech_assessment.md 不存在。\n"
            "你直接从以下来源提取技术战略分析：\n"
            "- sub_topic_*_reading_notes.json (deep_reader 阅读笔记)\n"
            "- shared_state.json → quality_summary (质量评估汇总)\n"
            "- academic_scout-facts.json + industry_scout-facts.json\n\n"
            "在报告的技术评估章节中注明：'技术战略分析由阅读笔记直接支撑，未经独立技术战略评审'。\n"
            "仍需包含 Counter Evidence 和 Data Gaps 章节。\n"
        )

    manifest = {
        "role": "report_writer",
        "slug": "report_writer",
        "system_prompt": system_prompt,
        "output_path": str(task / "report_writer.md"),
        "brief_path": str(task / "shared_state.json"),
        "connectorIds": [],
        "key_inputs": key_inputs,
        "expected_outputs": ["report.md", "report-facts.json", "report-section.json"],
        "fallback_mode": fallback_mode,
    }
    manifest_path = task / "wave3_manifest_report_writer.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "dispatch_info": {
            "type": "wave3_dispatch",
            "manifests": [str(manifest_path)],
            "has_more": False,
        },
        "instruction": _lit_dispatch_instruction("report_writer", "report_writer", "phase16_wave3_dispatch_collect", False),
        "paused_after": "phase15_wave3_dispatch_prepare",
        "next_phase": "phase16_wave3_dispatch_collect",
        "phase": "phase15_wave3_dispatch_prepare",
        "job_id": job_ctx.job_id,
    }


def _wave3_collect_check(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Wave 3 单次 collect 检查 — md + sidecar 三文件。"""
    task = _task_dir(runtime_root, job_ctx)

    report_path = task / "report.md"
    report_facts = task / "report-facts.json"
    report_section = task / "report-section.json"

    report_ok = report_path.exists() and report_path.stat().st_size > 500
    facts_ok = report_facts.exists() and report_facts.stat().st_size > 10
    section_ok = report_section.exists() and report_section.stat().st_size > 10

    # JSON 有效性 + stability
    facts_valid = False
    if facts_ok and _file_stable(report_facts, interval=5):
        facts_valid = _safe_load_json_with_repair(report_facts) is not None

    section_valid = False
    if section_ok and _file_stable(report_section, interval=5):
        section_valid = _safe_load_json_with_repair(report_section) is not None

    all_complete = report_ok and facts_valid and section_valid

    if not all_complete:
        return {
            "ok": True,
            "needs_dispatch": True,
            "dispatch_info": {"type": "wave3_incomplete", "incomplete_roles": ["report_writer"], "has_more": False},
            "paused_after": "phase15_wave3_dispatch_prepare",
            "next_phase": "phase16_wave3_dispatch_collect",
            "phase": "phase16_wave3_dispatch_collect",
            "job_id": job_ctx.job_id,
        }

    return {
        "ok": True,
        "mode": "collect",
        "phase": "phase16_wave3_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {"report_size": report_path.stat().st_size},
    }


def _run_wave3_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 16: Wave 3 collect — 带 sidecar 落盘重试。"""
    task = _task_dir(runtime_root, job_ctx)

    def collect_fn():
        return _wave3_collect_check(runtime_root, job_ctx)

    return _collect_with_sidecar_retry(
        collect_fn,
        task_dir=task,
        collect_name="wave3_collect",
    )


def _run_claim_coverage(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 17: 全 claim 覆盖检查。"""
    task = _task_dir(runtime_root, job_ctx)
    ss_path = task / "shared_state.json"
    shared_state = {}
    if ss_path.exists():
        shared_state = json.loads(ss_path.read_text(encoding="utf-8"))

    claim_status = shared_state.get("claim_status", {})
    total = len(claim_status)
    covered = sum(1 for s in claim_status.values() if s in ("supported", "partially_supported"))
    coverage_rate = covered / max(total, 1)

    result = {
        "total_claims": total,
        "covered": covered,
        "coverage_rate": round(coverage_rate, 2),
        "uncovered": [cid for cid, s in claim_status.items() if s == "planned"],
        "passed": coverage_rate >= 0.6,
    }

    cov_path = task / "claim_coverage.json"
    cov_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_to_workspace(job_ctx, cov_path, "claim_coverage.json")

    return {
        "ok": True,
        "mode": "quality_gate",
        "phase": "phase17_claim_coverage",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_debate_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 18: 跨章节对抗审查。"""
    task = _task_dir(runtime_root, job_ctx)

    # 简化版 debate review: 检查报告结构完整性
    report_path = task / "report.md"
    review = {"verdict": "PASS", "issues": [], "checks": {}}

    if report_path.exists():
        content = report_path.read_text(encoding="utf-8")
        sections_found = []
        for section in ["Executive Summary", "技术概述", "技术成熟度", "痛点", "技术路线", "技术难点", "竞争格局", "商业化", "投资判断"]:
            if section in content:
                sections_found.append(section)

        review["checks"]["sections_found"] = {"actual": len(sections_found), "expected": 9}
        review["checks"]["char_count"] = {"actual": len(content), "min": 5000}

        # counter_evidence / data_gaps 章节检查
        has_counter = "Counter Evidence" in content or "反方证据" in content or "counter evidence" in content.lower()
        has_gaps = "Data Gaps" in content or "数据缺口" in content or "data gaps" in content.lower()
        review["checks"]["counter_evidence"] = {"actual": has_counter, "expected": True}
        review["checks"]["data_gaps"] = {"actual": has_gaps, "expected": True}

        if len(sections_found) < 5:
            review["verdict"] = "REWRITE_REQUIRED"
            review["issues"].append(f"Only {len(sections_found)}/9 report sections found")
        if len(content) < 3000:
            review["verdict"] = "WARN"
            review["issues"].append(f"Report too short: {len(content)} chars")
        if not has_counter:
            review["issues"].append("Missing counter_evidence section — report lacks intellectual honesty")
        if not has_gaps:
            review["issues"].append("Missing data_gaps section — report overstates confidence")

        # ── L1: 信息泄露检测 (路径/API key/token) ──
        import re as _re
        leak_patterns = [
            (r'/Users/\w+/', "absolute path leak"),
            (r'sk-[a-zA-Z0-9]{20,}', "API key leak"),
            (r'bearer\s+[a-zA-Z0-9_\-\.]+', "bearer token leak"),
            (r'password\s*[:=]\s*\S+', "password leak"),
        ]
        for pat, desc in leak_patterns:
            if _re.search(pat, content, _re.IGNORECASE):
                review["issues"].append(f"L1 BLOCKING: {desc} detected")
                review["verdict"] = "FAIL"

        # ── L2: 占位残留检测 ──
        placeholder_patterns = [r'\[TODO\]', r'\[待补充\]', r'\[TBD\]', r'\[PLACEHOLDER\]', r'占位符']
        found_placeholders = [p for p in placeholder_patterns if _re.search(p, content, _re.IGNORECASE)]
        if found_placeholders:
            review["issues"].append(f"L2 WARN: placeholder residues found: {found_placeholders}")
            if review["verdict"] == "PASS":
                review["verdict"] = "WARN"

        # ── L4: 数字一致性验证 (fact_id 引用检查) ──
        fact_ids_in_text = set(_re.findall(r'\[(READ-\d+|IND-\d+|ENT-\d+)\]', content))
        # 检查 fact_store 中的 ID
        fact_store_path = task / "fact_store.json"
        if fact_store_path.exists():
            try:
                fs_data = _safe_load_json_with_repair(fact_store_path) or {}
                valid_ids = set()
                for f in fs_data.get("facts", []):
                    fid = f.get("fact_id", "")
                    if fid:
                        valid_ids.add(fid)
                # 加上 sidecar facts
                for sidecar in task.glob(f"*{FACTS_SUFFIX}"):
                    sd = _safe_load_json_with_repair(sidecar) or {}
                    for item in sd.get("papers", sd.get("facts", sd.get("companies", []))):
                        fid = item.get("fact_id", "")
                        if fid:
                            valid_ids.add(fid)
                unknown_refs = fact_ids_in_text - valid_ids
                if unknown_refs and len(unknown_refs) < 10:
                    review["issues"].append(f"L4 WARN: {len(unknown_refs)} fact_ids referenced but not in fact_store: {list(unknown_refs)[:5]}")
            except Exception:
                pass

        # ── Citation density check ──
        total_refs = len(fact_ids_in_text)
        content_len = len(content)
        density = total_refs / max(content_len / 2000, 1)
        review["checks"]["citation_density"] = {
            "actual": round(density, 1),
            "threshold": 3.0,
            "passed": density >= 2.0,  # LIT 允许稍低 (学术论文引用密度低于商业分析)
        }
        if density < 2.0:
            review["issues"].append(f"Citation density too low: {density:.1f} refs/2k chars (threshold: 3.0)")
    else:
        review["verdict"] = "FAIL"
        review["issues"].append("report.md not found")

    review_path = task / "debate_review.json"
    _atomic_write_json(review_path, review)
    _sync_to_workspace(job_ctx, review_path, "debate_review.json")

    return {
        "ok": review["verdict"] in ("PASS", "WARN"),
        "mode": "quality_gate",
        "phase": "phase18_debate_review",
        "job_id": job_ctx.job_id,
        "result": review,
    }


def _run_final_assembly(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 19: 排版 + md/docx/bib 输出。

    v2: 增加引用脚注格式化 + DOCX 构建。
    - 从 fact_store.json + 各 facts.json 构建引用映射
    - 将 report.md 中的 [READ-XXX] / [IND-XXX] / [ENT-XXX] 转为 [^N] 脚注
    - 调用 build_lit_report_docx.py 生成 DOCX
    """
    import subprocess
    import re as _re

    task = _task_dir(runtime_root, job_ctx)
    report_path = task / "report.md"

    if not report_path.exists():
        return {"ok": False, "phase": "phase19_final_assembly", "job_id": job_ctx.job_id,
                "error": "report.md not found"}

    # ── Step 1: 引用脚注格式化 ─────────────────────────────────
    # 从 fact_store + 各 sidecar facts 构建 fact_id → 来源描述 映射
    fact_id_to_source: dict[str, str] = {}

    # 1a. fact_store.json
    fs_path = task / "fact_store.json"
    if fs_path.exists():
        try:
            fs_data = _safe_load_json_with_repair(fs_path) or {}
            for f in fs_data.get("facts", []):
                fid = f.get("fact_id", "")
                if not fid:
                    continue
                title = f.get("title", "")
                source = f.get("discovery_source", "")
                url = f.get("open_access_pdf_url") or f.get("url", "")
                fact_id_to_source[fid] = f"{source} — {title}" + (f" — {url}" if url else "")
        except Exception:
            pass

    # 1b. 各 sidecar facts
    for sidecar in task.glob(f"*{FACTS_SUFFIX}"):
        try:
            sd = _safe_load_json_with_repair(sidecar) or {}
            items = sd.get("papers", sd.get("facts", sd.get("companies", [])))
            for item in items:
                fid = item.get("fact_id", "")
                if not fid:
                    continue
                title = item.get("title") or item.get("company_name", "")
                source = item.get("discovery_source") or item.get("source", "")
                url = item.get("open_access_pdf_url") or item.get("url", "")
                year = item.get("year", "")
                if fid not in fact_id_to_source:
                    desc = f"{source} — {title}"
                    if year:
                        desc += f" ({year})"
                    if url:
                        desc += f" — {url}"
                    fact_id_to_source[fid] = desc
        except Exception:
            pass

    # 1c. 将 report.md 中的 [READ-XXX] / [IND-XXX] / [ENT-XXX] 转为 [^N] 脚注
    report_content = report_path.read_text(encoding="utf-8")
    all_fact_refs = sorted(set(_re.findall(r"\[(READ-\d+|IND-\d+|ENT-\d+)\]", report_content)))

    if all_fact_refs and fact_id_to_source:
        # 建立 fact_id → footnote number 映射
        fn_map: dict[str, int] = {}
        for idx, fid in enumerate(all_fact_refs, 1):
            fn_map[fid] = idx

        # 替换正文中的 [READ-001] → [^1]
        for fid, fn_num in fn_map.items():
            report_content = report_content.replace(f"[{fid}]", f"[^{fn_num}]")

        # 在报告末尾追加脚注定义
        if "## 参考文献" not in report_content and "## References" not in report_content:
            report_content += "\n\n## 参考文献\n\n"

        for fid, fn_num in fn_map.items():
            source_desc = fact_id_to_source.get(fid, fid)
            report_content += f"[^{fn_num}]: {source_desc}\n"

        # 回写格式化后的 report.md
        report_path.write_text(report_content, encoding="utf-8")
        print(f"  📝 Citation footnotes: {len(fn_map)} references formatted", flush=True)

    # ── Step 2: 复制 report.md 到 delivery ──
    ws = job_ctx.workspace
    if ws is not None:
        try:
            shutil.copy2(report_path, ws.delivery_dir / "report.md")
        except Exception:
            pass

    # ── Step 3: 调用 DOCX 构建 ──────────────────────────────
    docx_result: dict[str, Any] = {"success": False, "method": "skipped"}
    docx_path = task / "report.docx"
    academic_section_path = task / f"academic_scout{SECTION_SUFFIX}"

    try:
        build_script = runtime_root / "scripts" / "build_lit_report_docx.py"
        if not build_script.exists():
            build_script = runtime_root.parent / "scripts" / "build_lit_report_docx.py"

        cmd = [
            "python3", str(build_script), job_ctx.job_id,
            "--input", str(report_path),
            "--output", str(docx_path),
            "--entity", job_ctx.entity or "",
        ]
        if academic_section_path.exists():
            cmd.extend(["--academic-section", str(academic_section_path)])
        if (task / "fact_store.json").exists():
            cmd.extend(["--fact-store", str(task / "fact_store.json")])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            try:
                docx_result = json.loads(result.stdout)
            except Exception:
                docx_result = {"success": True, "output": str(docx_path), "raw": result.stdout[:200]}
        else:
            docx_result = {"success": False, "error": result.stderr[:500]}
            print(f"  ⚠️ DOCX build failed: {result.stderr[:200]}", flush=True)
    except FileNotFoundError:
        print("  ⚠️ build_lit_report_docx.py not found, DOCX generation skipped", flush=True)
    except Exception as e:
        docx_result = {"success": False, "error": str(e)[:200]}
        print(f"  ⚠️ DOCX build exception: {e}", flush=True)

    # 复制 DOCX 到 delivery
    if docx_result.get("success") and docx_path.exists():
        if ws is not None:
            try:
                shutil.copy2(docx_path, ws.delivery_dir / "report.docx")
            except Exception:
                pass

    return {
        "ok": True,
        "mode": "assembly",
        "phase": "phase19_final_assembly",
        "job_id": job_ctx.job_id,
        "result": {
            "report_path": str(report_path),
            "report_size": report_path.stat().st_size,
            "docx": docx_result,
            "citations_formatted": len(all_fact_refs) if all_fact_refs else 0,
        },
    }


def _run_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 20: 交付 — 含 DOCX。"""
    task = _task_dir(runtime_root, job_ctx)
    ws = job_ctx.workspace

    delivery_summary = {
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "artifacts": [],
    }

    # 收集交付物 (含 DOCX)
    for artifact_name in ["report.md", "report.docx",
                          "fact_store.json", "shared_state.json",
                          "claim_coverage.json", "debate_review.json",
                          "wave1_gate.json", "wave2_gate.json"]:
        src = task / artifact_name
        if src.exists():
            delivery_summary["artifacts"].append(artifact_name)
            if ws is not None:
                try:
                    shutil.copy2(src, ws.delivery_dir / artifact_name)
                except Exception:
                    pass

    # 如果 DOCX 只在 delivery 目录（Phase 19 已复制过去），也列入
    if ws is not None:
        docx_delivery = ws.delivery_dir / "report.docx"
        if docx_delivery.exists() and "report.docx" not in delivery_summary["artifacts"]:
            delivery_summary["artifacts"].append("report.docx")

    return {
        "ok": True,
        "mode": "delivery",
        "phase": "phase20_delivery",
        "job_id": job_ctx.job_id,
        "result": delivery_summary,
    }


# ═══════════════════════════════════════════════════════════
# Profile 类
# ═══════════════════════════════════════════════════════════

def create_lit_review_profile(runtime_root: Path) -> "LitReviewProfile":
    """Factory: create LitReviewProfile with all 20 phase handlers bound."""
    return LitReviewProfile(runtime_root=runtime_root)


class LitReviewProfile(PipelineProfile):
    """VC 技术评估文献综述管线 Profile — 3 Wave / 6 Role / 20 Phase."""

    def __init__(self, runtime_root: Path):
        self._runtime_root = runtime_root

        def _bind(handler):
            """Bind runtime_root as first arg."""
            def _wrapper(job_ctx: JobContext) -> dict[str, Any]:
                return handler(self._runtime_root, job_ctx)
            return _wrapper

        super().__init__(
            name="lit_review",
            job_type="lit",
            phase_handlers={
                # Phase 01-05: 准备
                "phase01_intake": _bind(_run_intake),
                "phase02_tech_decomposition": _bind(_run_tech_decomposition),
                "phase03_presearch": _bind(_run_presearch),
                "phase04_research_plan": _bind(_run_research_plan),
                "phase05_shared_state_init": _bind(_run_shared_state_init),
                # Phase 06-10: Wave 1
                "phase06_wave1_dispatch_prepare": _bind(_run_wave1_dispatch_prepare),
                "phase07_wave1_dispatch_collect": _bind(_run_wave1_dispatch_collect),
                "phase08_wave1_evidence_gate": _bind(_run_wave1_evidence_gate),
                "phase09_wave1_fact_store_merge": _bind(_run_wave1_fact_store_merge),
                "phase10_wave1_shared_state_refresh": _bind(_run_wave1_shared_state_refresh),
                # Phase 11-14: Wave 2
                "phase11_wave2_dispatch_prepare": _bind(_run_wave2_dispatch_prepare),
                "phase12_wave2_dispatch_collect": _bind(_run_wave2_dispatch_collect),
                "phase13_wave2_evidence_gate": _bind(_run_wave2_evidence_gate),
                "phase14_wave2_shared_state_refresh": _bind(_run_wave2_shared_state_refresh),
                # Phase 15-20: Wave 3 + Quality + Delivery
                "phase15_wave3_dispatch_prepare": _bind(_run_wave3_dispatch_prepare),
                "phase16_wave3_dispatch_collect": _bind(_run_wave3_dispatch_collect),
                "phase17_claim_coverage": _bind(_run_claim_coverage),
                "phase18_debate_review": _bind(_run_debate_review),
                "phase19_final_assembly": _bind(_run_final_assembly),
                "phase20_delivery": _bind(_run_delivery),
            },
        )

    def phase_prerequisites(self) -> dict[str, list[str]]:
        """声明每个 phase 需要的文件依赖 — Kernel 据此做 auto-backfill。"""
        return {
            "phase02_tech_decomposition": ["intake.json"],
            "phase04_research_plan": ["intake.json", "tech_decomposition.json"],
            "phase03_presearch": ["tech_decomposition.json"],
            "phase05_shared_state_init": ["research_plan.json"],
            # Wave 1
            "phase06_wave1_dispatch_prepare": ["research_plan.json", "shared_state.json"],
            "phase07_wave1_dispatch_collect": ["research_plan.json"],
            "phase08_wave1_evidence_gate": [
                "academic_scout-facts.json",
                "industry_scout-facts.json",
                "enterprise_scout-facts.json",
            ],
            "phase09_wave1_fact_store_merge": [
                "academic_scout-facts.json",
                "industry_scout-facts.json",
                "enterprise_scout-facts.json",
            ],
            "phase10_wave1_shared_state_refresh": [
                "fact_store.json",
                "research_plan.json",
                "academic_scout-facts.json",
            ],
            # Wave 2
            "phase11_wave2_dispatch_prepare": [
                "shared_state.json",
                "fact_store.json",
            ],
            "phase12_wave2_dispatch_collect": ["shared_state.json"],
            "phase13_wave2_evidence_gate": [
                "tech_assessment.md",
            ],
            "phase14_wave2_shared_state_refresh": [
                "tech_assessment.md",
            ],
            # Wave 3
            "phase15_wave3_dispatch_prepare": [
                "shared_state.json",
                "tech_assessment.md",
            ],
            "phase16_wave3_dispatch_collect": ["report.md"],
            "phase17_claim_coverage": ["shared_state.json"],
            "phase18_debate_review": ["report.md"],
            "phase19_final_assembly": ["report.md", "report.docx"],
            "phase20_delivery": ["report.md", "report.docx", "fact_store.json"],
        }

    def phase_outputs(self) -> dict[str, list[str]]:
        """声明每个 phase 产出哪些文件 — Kernel 据此判断 phase 是否已完成。"""
        return {
            "phase01_intake": ["intake.json"],
            "phase02_tech_decomposition": ["tech_decomposition.json"],
            "phase03_research_plan": ["research_plan.json"],
            "phase03_presearch": ["presearch.json"],
            "phase05_shared_state_init": ["fact_store.json", "shared_state.json"],
            # Wave 1
            "phase06_wave1_dispatch_prepare": [],  # dispatch 不产出文件
            "phase07_wave1_dispatch_collect": [],
            "phase08_wave1_evidence_gate": ["wave1_gate.json"],
            "phase09_wave1_fact_store_merge": ["fact_store.json"],
            "phase10_wave1_shared_state_refresh": ["shared_state.json"],
            # Wave 2
            "phase11_wave2_dispatch_prepare": [],
            "phase12_wave2_dispatch_collect": [],
            "phase13_wave2_evidence_gate": ["wave2_gate.json"],
            "phase14_wave2_shared_state_refresh": ["shared_state.json"],
            # Wave 3
            "phase15_wave3_dispatch_prepare": [],
            "phase16_wave3_dispatch_collect": [],
            "phase17_claim_coverage": ["claim_coverage.json"],
            "phase18_debate_review": ["debate_review.json"],
            "phase19_final_assembly": ["report.md", "report.docx"],
            "phase20_delivery": [],
        }

    def search_policy(self) -> dict[str, Any]:
        return {
            "wave_roles": LIT_WAVE_ROLES,
            "role_connector_ids": LIT_ROLE_CONNECTOR_IDS,
            "collect_retry_count": COLLECT_RETRY_COUNT,
            "collect_retry_interval": COLLECT_RETRY_INTERVAL,
        }

    def verification_policy(self) -> dict[str, Any]:
        return {
            "wave1_gate": WAVE1_GATE_THRESHOLDS,
            "wave2_gate": WAVE2_GATE_THRESHOLDS,
            "wave3_gate": WAVE3_GATE_THRESHOLDS,
        }
