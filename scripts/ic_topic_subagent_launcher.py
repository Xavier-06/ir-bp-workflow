#!/usr/bin/env python3
"""
IC Topic (课题研究) Subagent Launcher — WorkBuddy v1

行业课题研究管线核心编排引擎。核心特性：
1. 3 Wave / 6 角色架构 — 参照 LIT 管线的 3 波模式
2. Sequential has_more 派发 — 每次只派 1 个角色，避免并行写丢数据
3. Instruction store 热加载 — 角色指令从 instruction_store_ic/ 动态加载
4. File lock — 复用 bp_file_lock 的原子写入

Wave 编排:
  Wave 1（3 角色·sequential）: ic_market_overview / ic_competitive_landscape / ic_tech_product
  Wave 2（2 角色·sequential，依赖 W1）: ic_supply_chain / ic_policy_risk
  Wave 3（1 角色，依赖 W1+W2）: ic_report_synthesizer

2026-07-08 v1: 初始版本，从 LIT launcher 模式衍生
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from scripts.ic_topic_constants import (
    IC_TOPIC_CONNECTOR_IDS,
    IC_TOPIC_ROLE_CONNECTOR_IDS,
    IC_TOPIC_WAVE1_ROLE_SLUGS,
    IC_TOPIC_WAVE2_ROLE_SLUGS,
    IC_TOPIC_WAVE3_ROLE_SLUGS,
    IC_TOPIC_WAVE_ROLES,
    IC_TOPIC_ALL_ROLE_SLUGS,
    IC_TOPIC_ALL_SLUGS,
    FACTS_SUFFIX,
    SECTION_SUFFIX,
)

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / 'data' / 'tasks'
INSTRUCTION_STORE = ROOT / 'instruction_store_ic'

# ── Active roles ─────────────────────────────────────────────
IC_TOPIC_ACTIVE_ROLES = set(IC_TOPIC_ALL_ROLE_SLUGS.keys())

# ── mtime caches ────────────────────────────────────────────
_INSTRUCTION_STORE_CACHE: dict[str, str] | None = None
_INSTRUCTION_STORE_MTIME: float = 0.0
_TOOL_GUIDE_CACHE: str = ""
_TOOL_GUIDE_MTIME: float = 0.0

# ── Prompt quality guard ────────────────────────────────────
MIN_PROMPT_LENGTH = 200


# ═══════════════════════════════════════════════════════════
# Instruction Store Loaders
# ═══════════════════════════════════════════════════════════

def _load_instruction_prompts(runtime_root: Path | None = None, force_reload: bool = False) -> dict[str, str]:
    """Hot-load all active role prompts from instruction_store_ic/ with mtime cache."""
    global _INSTRUCTION_STORE_CACHE, _INSTRUCTION_STORE_MTIME
    root = runtime_root or ROOT
    store_dir = root / "instruction_store_ic"
    index_path = store_dir / "index.json"
    if not index_path.exists():
        return _INSTRUCTION_STORE_CACHE or {}
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
        if key not in IC_TOPIC_ACTIVE_ROLES:
            continue
        path = store_dir / role.get("file", "")
        if path.exists():
            prompts[key] = path.read_text(encoding="utf-8")
    _INSTRUCTION_STORE_CACHE = prompts
    _INSTRUCTION_STORE_MTIME = current_mtime
    return prompts


def _load_tool_guide(runtime_root: Path | None = None) -> str:
    """Load _common_tool_guide.md with mtime cache."""
    global _TOOL_GUIDE_CACHE, _TOOL_GUIDE_MTIME
    root = runtime_root or ROOT
    guide_path = root / "instruction_store_ic" / "_common_tool_guide.md"
    if not guide_path.exists():
        return _TOOL_GUIDE_CACHE
    current_mtime = guide_path.stat().st_mtime
    if _TOOL_GUIDE_CACHE and current_mtime == _TOOL_GUIDE_MTIME:
        return _TOOL_GUIDE_CACHE
    _TOOL_GUIDE_CACHE = guide_path.read_text(encoding="utf-8")
    _TOOL_GUIDE_MTIME = current_mtime
    return _TOOL_GUIDE_CACHE


# ── Conclusion appendix ─────────────────────────────────────
_IC_CONCLUSION_APPENDIX = """

## 买方研究规范 (所有角色通用)

你是买方研究员，你的产出将直接用于投资决策。

### 数据规范
1. 每个数据点标注来源（工具名+查询参数）和时效
2. 置信度: HIGH(多源一致) / MEDIUM(单源可溯源) / LOW(估算)，发现矛盾必须标注
3. Counter Evidence 必须存在且非空——找不到反例 ≠ 没有反例
4. Data Gaps 必须存在且非空——标注"还没查到"比假装查到好

### 投资视角规范
5. 每个章节的最后必须有「投资含义」段落——这个数据对配置意味着什么？
6. 区分「卖方一致预期」和「我们的判断」——预期差才是超额收益来源
7. 量化优于定性——"毛利率 45% 高于行业均值 30%"优于"盈利能力较强"
8. 不要用「增长迅猛」「前景广阔」——给数字、给概率、给方向

### 输出规范
9. 输出到 manifest 指定的 output_path，不通过 SendMessage 通信
10. 先写 .md 主文件，再写 sidecar (.json)
"""



def _assemble_system_prompt(runtime_root: Path, role: str, task_dir: Path | None = None) -> str:
    """Assemble full system prompt: instruction + conclusion + tool guide."""
    prompts = _load_instruction_prompts(runtime_root)
    base = prompts.get(role, f"UNKNOWN ROLE: {role}. No instruction-store prompt is registered.")
    tool_guide = _load_tool_guide(runtime_root)
    full_prompt = base + _IC_CONCLUSION_APPENDIX + tool_guide
    if task_dir is not None:
        full_prompt = full_prompt.replace("{TASK_DIR}", str(task_dir))
    full_prompt = full_prompt.replace("{RUNTIME_ROOT}", str(runtime_root))
    return full_prompt


# ═══════════════════════════════════════════════════════════
# Dispatch Instruction Template
# ═══════════════════════════════════════════════════════════

def _ic_dispatch_instruction(role: str, slug: str, next_phase: str, has_more: bool) -> str:
    """Generate dispatch instruction for Coordinator."""
    more_note = (
        "has_more=True: 还有更多角色待派发，恢复后会返回下一个 manifest。"
        if has_more else
        "这是最后一个角色，恢复后会推进到下一阶段。"
    )
    return (
        f"## IC Topic 子代理派发指令\n\n"
        f"MANDATORY: 读取 manifest JSON 文件。\n"
        f"使用 Agent 工具，以下精确参数:\n"
        f"  - name = '{role}'\n"
        f"  - team_name = 'ic-{{task_id}}'\n"
        f"  - mode = 'bypassPermissions'\n"
        f"  - prompt = manifest 的 'system_prompt' 字段 (完整原文，不可简化)\n"
        f"  - connectorIds = manifest 的 'connectorIds' 字段\n\n"
        f"## 三文件验证 (缺一不可)\n"
        f"子代理输出到 manifest 指定的 output path:\n"
        f"  - {{role}}.md (>100 bytes)\n"
        f"  - 如果有 sidecar，等它写完\n"
        f"确认输出文件齐全后，用 start_phase='{next_phase}' 恢复管线。\n\n"
        f"{more_note}\n\n"
        f"## 绝对禁止:\n"
        f"- 禁止在单条消息中派发多个 Agent tool_use\n"
        f"- 禁止在子代理输出文件齐全前推进管线\n"
        f"- 禁止简化或截断 manifest 中的 system_prompt\n"
        f"- 禁止给 Agent tool 传 run_in_background=True\n"
    )


# ═══════════════════════════════════════════════════════════
# Path Helpers
# ═══════════════════════════════════════════════════════════

def _task_dir(job_id: str) -> Path:
    d = TASKS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _output_path(job_id: str, role: str) -> Path:
    return _task_dir(job_id) / f"{role}.md"


def _facts_path(job_id: str, role: str) -> Path:
    return _task_dir(job_id) / f"{role}{FACTS_SUFFIX}"


def _section_path(job_id: str, role: str) -> Path:
    return _task_dir(job_id) / f"{role}{SECTION_SUFFIX}"


# ═══════════════════════════════════════════════════════════
# Wave Manifest Generator
# ═══════════════════════════════════════════════════════════

def _build_ic_manifest(
    runtime_root: Path,
    role: str,
    slug: str,
    task: Path,
    wave: int,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a single manifest for one IC topic role."""
    system_prompt = _assemble_system_prompt(runtime_root, role, task_dir=task)
    assert len(system_prompt) > MIN_PROMPT_LENGTH, \
        f"system_prompt too short for {role}: {len(system_prompt)} chars"

    return {
        "role": role,
        "slug": slug,
        "wave": wave,
        "system_prompt": system_prompt,
        "output_path": str(_output_path(job_id="", role=role)),  # placeholder, replaced at dispatch
        "connectorIds": IC_TOPIC_ROLE_CONNECTOR_IDS.get(role, IC_TOPIC_CONNECTOR_IDS),
        "key_inputs": {
            "topic_name": plan.get("topic_name", ""),
            "direction": plan.get("direction", ""),
            "core_question": plan.get("core_question", ""),
            "sub_questions": plan.get("sub_questions", []),
            "research_scope": plan.get("research_scope", ""),
            "research_plan": str(task / "research_plan.json"),
            "fact_store": str(task / "fact_store.json"),
        },
        "expected_outputs": [
            f"{role}.md",
            f"{role}{FACTS_SUFFIX}",
            f"{role}{SECTION_SUFFIX}",
        ],
        "manifest_version": "1.0",
    }


def _role_outputs_complete(task: Path, role: str) -> bool:
    """Check if a role's output files all exist and are non-empty."""
    facts = task / f"{role}{FACTS_SUFFIX}"
    section = task / f"{role}{SECTION_SUFFIX}"
    md = task / f"{role}.md"
    return (
        md.exists() and md.stat().st_size > 100
        and facts.exists() and facts.stat().st_size > 50
        and section.exists() and section.stat().st_size > 10
    )


# ═══════════════════════════════════════════════════════════
# Main Dispatch Entry Point
# ═══════════════════════════════════════════════════════════

def launch_next_wave(
    runtime_root: Path,
    job_id: str,
    entity: str = "",
    query: str = "",
    market: str = "cn",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """IC Topic 管线主入口 — sequential 派发下一个待处理的角色。

    每次调用返回 1 个 manifest 用于 Coordinator 派发。
    has_more=True 表示同 wave 内还有更多角色，Coordinator 应继续调用。
    """
    import sys
    task = _task_dir(job_id)

    # Load research plan
    plan_path = task / "research_plan.json"
    plan = {}
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan = {}

    if not plan:
        plan = {
            "topic_name": entity,
            "core_question": query,
            "sub_questions": [],
            "direction": (metadata or {}).get("direction", ""),
            "research_scope": (metadata or {}).get("research_scope", ""),
        }

    # Determine current wave and role
    all_waves = [1, 2, 3]
    current_wave = None
    current_role = None

    for wave_num in all_waves:
        roles = IC_TOPIC_WAVE_ROLES[wave_num]
        # Check which roles are completed
        completed = [r for r in roles if _role_outputs_complete(task, r)]
        remaining = [r for r in roles if r not in completed]

        if remaining:
            current_wave = wave_num
            current_role = remaining[0]
            break

    if current_role is None:
        # All roles complete
        return {
            "all_done": True,
            "message": f"All {len(IC_TOPIC_ALL_ROLE_SLUGS)} roles completed across all waves",
            "wave_index": None,
            "remaining_roles": [],
            "task_tool_instructions": [],
            "has_more": False,
        }

    # Build manifest
    slug = IC_TOPIC_ALL_ROLE_SLUGS[current_role]
    wave_roles = IC_TOPIC_WAVE_ROLES[current_wave]
    remaining_in_wave = [r for r in wave_roles if r != current_role
                         and not _role_outputs_complete(task, r)]
    has_more = len(remaining_in_wave) > 0

    manifest = _build_ic_manifest(runtime_root, current_role, slug, task, current_wave, plan)
    manifest["output_path"] = str(_output_path(job_id, current_role))

    manifest_path = task / f"wave{current_wave}_manifest_{current_role}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    is_wave1_role = current_role in IC_TOPIC_WAVE1_ROLE_SLUGS
    is_wave2_role = current_role in IC_TOPIC_WAVE2_ROLE_SLUGS
    is_wave3_role = current_role in IC_TOPIC_WAVE3_ROLE_SLUGS

    if is_wave1_role:
        next_phase = "phase07_wave1_dispatch_collect"
    elif is_wave2_role:
        next_phase = "phase10_wave2_dispatch_collect"
    elif is_wave3_role:
        next_phase = "phase14_synthesis_collect"
    else:
        next_phase = "phase07_wave1_dispatch_collect"

    dispatch_instruction = _ic_dispatch_instruction(current_role, slug, next_phase, has_more)

    return {
        "all_done": False,
        "wave_index": current_wave,
        "wave_label": f"Wave {current_wave}",
        "current_role": current_role,
        "slug": slug,
        "dispatched_count": 1,
        "has_more": has_more,
        "remaining_in_wave": remaining_in_wave,
        "next_phase": next_phase,
        "task_tool_instructions": [
            {
                "step": current_role,
                "role": current_role,
                "wave": current_wave,
                "manifest_path": str(manifest_path),
                "output_path": str(_output_path(job_id, current_role)),
                "prompt": dispatch_instruction,
                "connectorIds": manifest["connectorIds"],
            }
        ],
        "pipeline_status": get_pipeline_status(job_id),
    }


# ═══════════════════════════════════════════════════════════
# Pipeline Status & Quality Check
# ═══════════════════════════════════════════════════════════

def get_pipeline_status(job_id: str) -> dict[str, Any]:
    """Return current pipeline status snapshot."""
    task = _task_dir(job_id)
    status = {"total_roles": len(IC_TOPIC_ALL_ROLE_SLUGS), "waves": {}}
    for wave_num in [1, 2, 3]:
        roles = IC_TOPIC_WAVE_ROLES[wave_num]
        completed = [r for r in roles if _role_outputs_complete(task, r)]
        status["waves"][f"wave{wave_num}"] = {
            "total": len(roles),
            "completed": len(completed),
            "completed_roles": completed,
            "pending_roles": [r for r in roles if r not in completed],
        }
    status["overall_progress"] = sum(w["completed"] for w in status["waves"].values())
    return status


def check_step_quality(job_id: str, role: str) -> dict[str, Any]:
    """Check single role output quality."""
    task = _task_dir(job_id)
    md = task / f"{role}.md"
    if not md.exists():
        return {"passed": False, "reason": "md_not_found", "role": role}

    content = md.read_text(encoding="utf-8")
    char_count = len(content)
    has_sections = content.count("# ") >= 2
    source_count = content.count("http") + content.count("https")

    return {
        "passed": char_count >= 1000 and has_sections,
        "role": role,
        "char_count": char_count,
        "has_sections": has_sections,
        "source_count": source_count,
    }


def finalize_pipeline(job_id: str, entity: str = "", market: str = "cn") -> dict[str, Any]:
    """Finalize pipeline: quality summary."""
    task = _task_dir(job_id)
    status = get_pipeline_status(job_id)
    total = status["overall_progress"]
    expected = status["total_roles"]
    return {
        "job_id": job_id,
        "entity": entity,
        "market": market,
        "completion": f"{total}/{expected}",
        "status": status,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
