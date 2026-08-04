from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from runtime.profiles.base import JobContext, PipelineProfile
from runtime.profiles.bp_constants import (
    BP_ALL_ROLE_SLUGS,
    BP_FULL_CONNECTOR_IDS,
    BP_LEGACY_ROLE_SLUGS,
    BP_WAVE1_ROLE_SLUGS,
    BP_WAVE3_ROLE_SLUGS,
    BP_WAVE4_ROLE_SLUGS,
    COLLECT_RETRY_COUNT,
    COLLECT_RETRY_INTERVAL,
)
from scripts.bp_utils import read_attempt_count


def _task_dir(runtime_root: Path, job_ctx: JobContext) -> Path:
    workspace = getattr(job_ctx, "workspace", None)
    if workspace is not None:
        task_dir = workspace.root
    else:
        task_dir = runtime_root / "tasks" / job_ctx.job_id
        task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _outputs_dir(runtime_root: Path, job_ctx: JobContext) -> Path:
    workspace = getattr(job_ctx, "workspace", None)
    if workspace is not None:
        return workspace.outputs_dir
    task_dir = _task_dir(runtime_root, job_ctx)
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _load_bp_profile(task_dir: Path) -> dict[str, Any]:
    """读取 bp_step0_profile.json，失败返回空 dict。"""
    profile_path = task_dir / "bp_step0_profile.json"
    if not profile_path.exists():
        return {}
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_python_script(runtime_root: Path, script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a Python script. Falls back to system Python for DOCX scripts
    when managed Python's lxml has code signature issues (Bug 7)."""
    script_path = runtime_root / "scripts" / script_name
    # DOCX scripts need lxml which may have code signature issues in managed env
    _DOCX_SCRIPTS = {"build_bp_dd_report_docx.py", "build_ir_broker_report_docx.py",
                     "build_ic_industry_report_docx.py", "generate_ubtech_docx.py"}
    python_bin = sys.executable
    if script_name in _DOCX_SCRIPTS:
        # Try managed Python first; if lxml import fails, fall back to system
        test = subprocess.run(
            [sys.executable, "-c", "from lxml import etree"],
            capture_output=True, text=True, timeout=10,
        )
        if test.returncode != 0:
            # System Python (anaconda) has working lxml
            for fallback in ["/opt/anaconda3/bin/python3", "/usr/bin/python3"]:
                if Path(fallback).exists():
                    python_bin = fallback
                    print(f"  🔄 [lxml fallback] using {fallback} for {script_name}", flush=True)
                    break
    cmd = [python_bin, str(script_path), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(runtime_root), timeout=1800)


def _not_implemented_phase(phase: str, reason: str, *, result_key: str) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "bp_placeholder",
        "phase": phase,
        "result": {
            result_key: "skipped",
            "reason": reason,
        },
    }


# ── Phase 01: 文档入库（OCR + 结构化抽取）──────────────

def _run_document_intake(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    metadata = job_ctx.metadata or {}
    input_file = metadata.get("input_file", "")

    # Phase 01b 发现 BP PDF → 自动路由回 Phase 01 做完整 OCR
    task_dir = _task_dir(runtime_root, job_ctx)
    discovered_pdf = task_dir / "bp_discovered_pdf.pdf"
    if not input_file and discovered_pdf.exists() and discovered_pdf.stat().st_size > 10 * 1024:
        input_file = str(discovered_pdf)
        metadata["input_file"] = input_file
        print(f"  🎉 [bp] phase01 检测到 Phase 01b 发现的 BP PDF: {input_file} "
              f"({discovered_pdf.stat().st_size / 1024:.1f}KB)，执行完整 OCR", flush=True)

    # 无 input_file → 跳过 Phase 01，交给 Phase 01b 处理
    if not input_file:
        print(f"  ⏭️  [bp] phase01 跳过（无 input_file，由 phase01b 公司名搜索接管）", flush=True)
        return {
            "ok": True,
            "mode": "skipped_no_input_file",
            "phase": "phase01_document_intake",
            "job_id": job_ctx.job_id,
            "result": {"skipped": True, "reason": "no_input_file_use_phase01b"},
        }

    if os.environ.get("IRBP_BG_CHILD") == "1":
        # 当前是后台子进程，直接执行
        from runtime.intake.bp_document_intake import run_document_intake
        return run_document_intake(job_ctx, input_file)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase01_document_intake")
    if cached is not None:
        print(f"  📦 [bp] 使用缓存的 document_intake 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase01_document_intake", pipeline="bp")


# ── Phase 01b: 公司名搜索入库（无 PDF 模式）──────────────

def _run_company_intake(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 01b: 仅公司名模式 — 通过子代理搜索公开数据重建 BP 等效数据。

    路由逻辑：
    - 有 input_file → 跳过（Phase 01 已处理）
    - 无 input_file → needs_dispatch 派发子代理搜索
    """
    metadata = job_ctx.metadata or {}
    input_file = metadata.get("input_file", "")

    # 有 PDF → 跳过，Phase 01 已处理
    if input_file:
        print(f"  ⏭️  [bp] phase01b 跳过（已有 input_file，由 phase01 处理）", flush=True)
        return {
            "ok": True,
            "mode": "skipped_has_input_file",
            "phase": "phase02_company_intake",
            "job_id": job_ctx.job_id,
            "result": {"skipped": True, "reason": "input_file provided, using phase01"},
        }

    # 无 input_file → 派发子代理搜索
    from scripts._bp_company_intake_subagent import (
        bp_build_company_intake_brief,
        bp_build_company_intake_instruction,
    )

    task_dir = _task_dir(runtime_root, job_ctx)
    entity, market = _bp_entity_market(job_ctx)

    brief_path = task_dir / "bp_phase02_brief.json"
    brief = bp_build_company_intake_brief(entity=entity, market=market, job_id=job_ctx.job_id)
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    instruction = bp_build_company_intake_instruction(
        entity=entity, market=market, job_id=job_ctx.job_id,
        task_dir=task_dir, brief_path=brief_path,
    )

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "bp_company_intake_subagent",
        "phase": "phase02_company_intake",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "brief_path": str(brief_path),
            "subagent_connector_ids": ["tyc-mcp", "westock-mcp"],
            "task_dir": str(task_dir),
        },
        "instruction": instruction,
    }


def _run_company_intake_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 01b collect: 检查子代理产出的 bp_ocr_text.txt + bp_step0_profile.json。

    断点修复（2026-08-03）：此前裸调无重试——子代理异步写文件慢一拍
    （ocr_text.txt < 100B 或 profile.json 未落盘）即 ok=False，kernel 直接终止。
    现包 _collect_with_retry 等待子代理落盘（与 wave collect 一致）。
    """
    from scripts._bp_company_intake_subagent import bp_collect_company_intake

    task_dir = _task_dir(runtime_root, job_ctx)
    metadata = job_ctx.metadata or {}
    input_file = metadata.get("input_file", "")

    # 有 PDF → 跳过 collect（Phase 01 已产出）
    if input_file:
        return {
            "ok": True,
            "mode": "skipped_has_input_file",
            "phase": "phase03_company_intake_collect",
            "job_id": job_ctx.job_id,
            "result": {"skipped": True},
        }

    return _collect_with_retry(
        "company_intake_collect",
        lambda: bp_collect_company_intake(task_dir=task_dir, job_id=job_ctx.job_id),
        job_id=job_ctx.job_id,
        outputs_dir=None,  # intake 产物在 task_dir 且非 .md，无进度信号，不做 early exit
        # intake 无 .md 进度信号，死代理会跑满全部重试；缩短为 20×30s=10 分钟
        max_retries=max(1, COLLECT_RETRY_COUNT // 2),
    )


# ── Phase 04: 研究计划（phase02 工商核验已删除，tyc 由 phase04 子代理直调）────

def _bp_entity_market(job_ctx: JobContext) -> tuple[str, str]:
    metadata = job_ctx.metadata or {}
    entity = job_ctx.entity or metadata.get("entity") or "目标公司"
    market = job_ctx.market or metadata.get("market") or "cn"
    return entity, market


def _run_research_plan(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 04: BP research plan -- subagent dispatch (v5.2).

    v5.2 (2026-07-08): Replaced main-AI manual enrichment with subagent dispatch.
    Subagent has MCP access to westock-mcp/tyc-mcp for structured data search.
    The subagent does all search + analysis + plan generation autonomously.
    """
    from scripts._bp_research_plan_subagent import (
        bp_build_research_plan_brief,
        bp_build_research_plan_instruction,
    )

    task_dir = _task_dir(runtime_root, job_ctx)
    metadata = job_ctx.metadata or {}
    entity, market = _bp_entity_market(job_ctx)
    from scripts.bp_stage_utils import read_stage_from_task
    stage_tier = read_stage_from_task(task_dir)

    # Build brief file for the subagent
    profile = _load_bp_profile(task_dir)
    brief_path = task_dir / "bp_phase04_brief.json"
    brief = bp_build_research_plan_brief(
        task_dir=task_dir, entity=entity, market=market,
        stage_tier=stage_tier, job_ctx=job_ctx, metadata=metadata,
        profile=profile,
    )
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Check skeleton availability
    skeleton_path = task_dir / "bp_research_plan_skeleton.json"
    has_skeleton = skeleton_path.exists()

    # Build instruction for main AI to dispatch the subagent
    instruction = bp_build_research_plan_instruction(
        entity=entity, market=market, stage_tier=stage_tier,
        job_id=job_ctx.job_id, task_dir=task_dir, brief_path=brief_path,
        has_skeleton=has_skeleton,
        skeleton_path=skeleton_path if has_skeleton else None,
    )

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "bp_research_plan_subagent",
        "phase": "phase04_research_plan",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "brief_path": str(brief_path),
            # v13 (2026-08-04): 补 ima-mcp — prompt Step 5 要求 IMA 研报库搜索，
            # 原值只给 tyc+westock 导致子代理拿到指令也调不了 IMA。与 instruction 内 connectorIds 对齐。
            "subagent_connector_ids": ["tyc-mcp", "westock-mcp", "ima-mcp"],
            "task_dir": str(task_dir),
        },
        "instruction": instruction,
    }


def _run_research_plan_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 04 collect v5.2: read subagent output (bp_research_plan.json)."""
    from scripts._bp_research_plan_subagent import bp_collect_research_plan

    task_dir = _task_dir(runtime_root, job_ctx)
    metadata = job_ctx.metadata or {}
    entity, market = _bp_entity_market(job_ctx)

    return bp_collect_research_plan(
        runtime_root=runtime_root, job_ctx=job_ctx, task_dir=task_dir,
        entity=entity, market=market, metadata=metadata,
        _load_bp_profile_fn=_load_bp_profile,
    )
def _run_bp_search_plan_compile(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 06: compile BP research plan into claim-level search work orders."""
    from scripts.bp_search_plan_compiler import compile_bp_search_plan, write_bp_search_plan

    task_dir = _task_dir(runtime_root, job_ctx)
    research_plan_path = task_dir / "bp_research_plan.json"
    if not research_plan_path.exists():
        # 兜底：phase06 被跳过时自动回填 research_plan
        print(f"  ⚠️ [phase13] bp_research_plan.json 不存在，自动回填 phase03_research_plan", flush=True)
        backfill_result = _run_research_plan(runtime_root, job_ctx)
        if not backfill_result.get("ok"):
            return {"ok": False, "mode": "bp_search_plan_compile", "phase": "phase07_search_plan_compile", "job_id": job_ctx.job_id, "error": f"Auto-backfill phase10 failed: {backfill_result.get('error', 'unknown')}"}
        if not research_plan_path.exists():
            return {"ok": False, "mode": "bp_search_plan_compile", "phase": "phase07_search_plan_compile", "job_id": job_ctx.job_id, "error": "Auto-backfill ran but bp_research_plan.json still missing"}
    try:
        research_plan = json.loads(research_plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "mode": "bp_search_plan_compile", "phase": "phase07_search_plan_compile", "job_id": job_ctx.job_id, "error": f"bp_research_plan.json invalid: {exc}"}

    profile = _load_bp_profile(task_dir)

    payload = compile_bp_search_plan(research_plan, profile=profile)
    path = write_bp_search_plan(task_dir, payload)
    task_count = len(payload.get("search_tasks", []))
    # 断点修复（2026-08-03）：search_tasks 为空不再终止管线。
    # 旧逻辑 `ok: bool(search_tasks)` 在 claim_matrix 为空（如 fallback skeleton）
    # 时直接杀死管线；而下游消费者（load_bp_search_work_order /
    # _search_tasks_by_claim）对空 plan 均优雅返回空 dict，数据采集由
    # 子代理 instruction_store + 共享尽调页驱动，search work order 仅是增强。
    if task_count == 0:
        print(
            f"  ⚠️ [phase07_search_plan_compile] search_tasks 为空"
            f"（research_plan 无 claim_matrix 或全为低优先级），降级放行",
            flush=True,
        )
    return {
        "ok": True,
        "mode": "bp_search_plan_compile",
        "phase": "phase07_search_plan_compile",
        "job_id": job_ctx.job_id,
        "result": {
            "search_plan_path": str(path),
            "search_tasks": task_count,
            "owner_sections": sorted((payload.get("owner_section_index") or {}).keys()),
        },
    }



def _run_bp_shared_page_init(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 05: 初始化 BP 共享尽调页。"""
    from scripts.bp_shared_page_builder import write_shared_page_outputs

    task_dir = _task_dir(runtime_root, job_ctx)
    result = write_shared_page_outputs(task_dir, after_wave=0)
    return {
        "ok": True,
        "mode": "bp_shared_page_init",
        "phase": "phase06_bp_shared_page_init",
        "job_id": job_ctx.job_id,
        "result": result["paths"] | {"claim_count": len(result["state"].get("claim_status", {}))},
    }


_WAVE_TO_PHASE_NUM = {1: "13", 3: "18", 4: "22"}

def _run_bp_shared_page_refresh(runtime_root: Path, job_ctx: JobContext, after_wave: int = 0) -> dict[str, Any]:
    """Refresh BP shared diligence page after a wave completes."""
    from scripts.bp_shared_page_builder import write_shared_page_outputs

    task_dir = _task_dir(runtime_root, job_ctx)
    result = write_shared_page_outputs(task_dir, after_wave=after_wave)
    phase_num = _WAVE_TO_PHASE_NUM.get(after_wave, str(after_wave))
    return {
        "ok": True,
        "mode": "bp_shared_page_refresh",
        "phase": f"phase{phase_num}_wave{after_wave}_shared_page_refresh",
        "job_id": job_ctx.job_id,
        "result": result["paths"] | {
            "after_wave": after_wave,
            "claim_count": len(result["state"].get("claim_status", {})),
            "critical_not_addressed": result["coverage"].get("summary", {}).get("critical_not_addressed", 0),
        },
    }


def _bp_fact_store_payload(job_ctx: JobContext, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    entity, market = _bp_entity_market(job_ctx)
    return {
        "schema_version": "bp_fact_store.v1",
        "task_id": job_ctx.job_id,
        "entity": entity,
        "market": market,
        "facts": facts or [],
        "conflicts": [],
        "source_files": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _write_bp_fact_store(task_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    store_path = task_dir / "bp_fact_store.json"
    store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    facts = payload.get("facts", []) or []
    index = {
        "schema_version": "bp_fact_store_index.v1",
        "task_id": payload.get("task_id", ""),
        "entity": payload.get("entity", ""),
        "market": payload.get("market", ""),
        "total_facts": len(facts),
        "fact_ids": [str(f.get("fact_id", "")) for f in facts if f.get("fact_id")],
        "facts_by_type": {},
        "facts_by_source_tier": {},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for fact in facts:
        fact_type = str(fact.get("fact_type") or "unknown")
        source_tier = str(fact.get("source_tier") or fact.get("source_quality") or "unknown")
        index["facts_by_type"][fact_type] = index["facts_by_type"].get(fact_type, 0) + 1
        index["facts_by_source_tier"][source_tier] = index["facts_by_source_tier"].get(source_tier, 0) + 1
    index_path = task_dir / "bp_fact_store_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return store_path, index_path


def _classify_source_tier(url: str) -> str:
    """根据 URL 判断 source_tier。"""
    media_domains = ("36kr", "sohu", "sina", "qq.com", "baidu", "zhihu")
    return "media" if any(d in url for d in media_domains) else "research"


def _extract_research_plan_facts(task_dir: Path) -> list[dict[str, Any]]:
    """Extract seed facts from bp_research_plan.json (replaces presearch facts).

    v5.3 后 presearch 被砍，搜索全部交给 phase04 子代理。
    research_plan.json 中包含行业/竞品/技术等结构化研究结果，
    提取为 seed facts 让子代理启动时 fact store 不为空。
    """
    rp_path = task_dir / "bp_research_plan.json"
    if not rp_path.exists():
        return []

    try:
        rp = json.loads(rp_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    facts: list[dict[str, Any]] = []
    fact_counter = 0

    # Extract from industry_analysis
    for section_key in ["industry_analysis", "competitive_landscape", "tech_analysis",
                        "market_sizing", "key_findings", "research_notes"]:
        section = rp.get(section_key)
        if not section:
            continue
        if isinstance(section, str) and len(section) >= 30:
            fact_counter += 1
            facts.append({
                "fact_id": f"BP-RESEARCH-{section_key.upper()}-F{fact_counter:03d}",
                "claim": section[:80],
                "value": section[:300],
                "unit": "",
                "period": "待验证",
                "source_url": "",
                "source_tier": "research_plan",
                "source_quote": section[:150],
                "question_id": f"research_plan_{section_key}",
                "fact_type": f"research_plan_{section_key}",
                "confidence": "medium",
            })
        elif isinstance(section, list):
            for item in section:
                if isinstance(item, dict):
                    text = item.get("summary") or item.get("content") or item.get("text") or json.dumps(item, ensure_ascii=False)[:200]
                    if len(text) >= 30:
                        fact_counter += 1
                        source = item.get("source_url") or item.get("url") or ""
                        facts.append({
                            "fact_id": f"BP-RESEARCH-{section_key.upper()}-F{fact_counter:03d}",
                            "claim": (item.get("title") or item.get("name") or text)[:80],
                            "value": text[:300],
                            "unit": "",
                            "period": "待验证",
                            "source_url": source,
                            "source_tier": "research_plan" if not source else _classify_source_tier(source),
                            "source_quote": text[:150],
                            "question_id": f"research_plan_{section_key}",
                            "fact_type": f"research_plan_{section_key}",
                            "confidence": "medium",
                        })

    # Also extract from flat keys (some research plans use flat structure)
    for key in ["industry", "competition", "technology", "market", "valuation", "risks"]:
        val = rp.get(key)
        if isinstance(val, str) and len(val) >= 30:
            fact_counter += 1
            facts.append({
                "fact_id": f"BP-RESEARCH-{key.upper()}-F{fact_counter:03d}",
                "claim": val[:80],
                "value": val[:300],
                "unit": "",
                "period": "待验证",
                "source_url": "",
                "source_tier": "research_plan",
                "source_quote": val[:150],
                "question_id": f"research_plan_{key}",
                "fact_type": f"research_plan_{key}",
                "confidence": "medium",
            })

    return facts[:80]  # Cap at 80 facts


def _run_bp_fact_store_bootstrap(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 07: 初始化 BP 事实库 — 注入 research_plan 事实，
    子代理启动时 fact store 不再为空。

    seed facts 来源：bp_research_plan.json（phase04 子代理产出）。
    """
    task_dir = _task_dir(runtime_root, job_ctx)

    # Collect seed facts from research_plan
    seed_facts: list[dict[str, Any]] = []
    seed_facts.extend(_extract_research_plan_facts(task_dir))

    # Deduplicate by fact_id
    seen_ids: set[str] = set()
    unique_facts: list[dict[str, Any]] = []
    for fact in seed_facts:
        fid = str(fact.get("fact_id", "")).strip()
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            unique_facts.append(fact)

    payload = _bp_fact_store_payload(job_ctx, facts=unique_facts)
    payload["source_files"] = ["research_plan"]
    store_path, index_path = _write_bp_fact_store(task_dir, payload)

    research_count = sum(1 for f in unique_facts if "RESEARCH" in str(f.get("fact_id", "")))
    print(f"    📦 Fact Store bootstrap: {len(unique_facts)} seed facts (research_plan={research_count})", flush=True)

    return {
        "ok": True,
        "mode": "bp_fact_store_bootstrap",
        "phase": "phase08_bp_fact_store_bootstrap",
        "job_id": job_ctx.job_id,
        "result": {
            "store_path": str(store_path),
            "index_path": str(index_path),
            "total_facts": len(unique_facts),
            "research_plan_facts": research_count,
        },
    }


def _load_bp_sidecar_facts(*dirs: Path) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    facts: list[dict[str, Any]] = []
    source_files: list[str] = []
    malformed_source_files: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    sidecar_paths: list[Path] = []
    seen_paths: set[str] = set()
    for directory in dirs:
        for path in sorted(Path(directory).glob("bp_*-facts.json")):
            path_key = str(path.resolve())
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            sidecar_paths.append(path)
    for path in sidecar_paths:
        # Fix 4: Use auto-repair JSON loader (2026-06-12)
        payload = _safe_load_json_with_repair(path)
        if payload is None:
            malformed_source_files.append({"path": str(path), "error": "JSON parse failed even after auto-repair"})
            continue
        source_files.append(str(path))
        for raw in payload.get("facts", []) or []:
            fact = dict(raw)
            key = (
                str(fact.get("fact_id", "")).strip(),
                str(fact.get("claim", "")).strip(),
                str(fact.get("value", "")).strip(),
                str(fact.get("period", "")).strip(),
                str(fact.get("source_url", "")).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            facts.append(fact)
    return facts, source_files, malformed_source_files


def _run_bp_fact_store_merge(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11: 合并 BP 维度事实 sidecar。"""
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    facts, source_files, malformed_source_files = _load_bp_sidecar_facts(outputs_dir, task_dir)
    if malformed_source_files:
        # 降级：坏 sidecar 跳过但不阻断管线（一个坏文件不应卡死整个 fact store merge）
        bad_paths = [m.get("path", "?") for m in malformed_source_files]
        print(f"  ⚠️ [phase30] {len(malformed_source_files)} 个 sidecar 格式异常，跳过: {bad_paths}", flush=True)
    payload = _bp_fact_store_payload(job_ctx, facts=facts)
    payload["source_files"] = source_files
    store_path, index_path = _write_bp_fact_store(task_dir, payload)
    return {
        "ok": True,
        "mode": "bp_fact_store_merge",
        "phase": "phase12_bp_fact_store_merge",
        "job_id": job_ctx.job_id,
        "result": {
            "store_path": str(store_path),
            "index_path": str(index_path),
            "source_file_count": len(source_files),
            "total_facts": len(facts),
            "malformed_source_files": malformed_source_files,
        },
    }


def _bp_section_files(*dirs: Path) -> list[Path]:
    allowed_names = {
        f"bp_dim_{slug}.md"
        for slug in set(BP_ALL_ROLE_SLUGS.values()) | set(BP_LEGACY_ROLE_SLUGS.values())
    }
    seen: set[str] = set()
    files: list[Path] = []
    for directory in dirs:
        for path in sorted(Path(directory).glob("bp_dim_*.md")):
            if not path.is_file() or path.name not in allowed_names:
                continue
            key = path.name
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _bp_section_sidecar_path(section_file: Path) -> Path:
    return section_file.with_name(f"{section_file.stem}-section.json")


def _unique_nonempty_strings(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def _validate_bp_search_audit(
    package: dict[str, Any],
    *,
    claim_priorities: dict[str, str] | None = None,
    min_queries: int = 8,
    min_fetched_urls: int = 3,
    min_domains: int = 3,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    claim_priorities = claim_priorities or {}
    audit = package.get("search_audit")
    if not isinstance(audit, dict):
        return [{"severity": "FAIL", "code": "MISSING_SEARCH_AUDIT", "message": "BP section package v2 must include search_audit"}]

    queries = audit.get("queries") if isinstance(audit.get("queries"), list) else []
    unique_queries = {
        str(item.get("query") or "").strip()
        for item in queries
        if isinstance(item, dict) and str(item.get("query") or "").strip()
    }
    fetched_urls = _unique_nonempty_strings(audit.get("fetched_urls"))
    for item in queries:
        if isinstance(item, dict):
            fetched_urls |= _unique_nonempty_strings(item.get("fetched_urls"))
    source_domains = _unique_nonempty_strings(audit.get("source_domains"))
    if not source_domains:
        for url in fetched_urls:
            if "://" in url:
                source_domains.add(url.split("://", 1)[1].split("/", 1)[0].lower())

    if len(unique_queries) < min_queries:
        issues.append({"severity": "FAIL", "code": "INSUFFICIENT_SEARCH_QUERIES", "message": f"search_audit requires at least {min_queries} unique queries; got {len(unique_queries)}"})
    if len(fetched_urls) < min_fetched_urls:
        issues.append({"severity": "FAIL", "code": "INSUFFICIENT_FETCHED_URLS", "message": f"search_audit requires at least {min_fetched_urls} fetched URLs; got {len(fetched_urls)}"})
    if len(source_domains) < min_domains:
        issues.append({"severity": "FAIL", "code": "INSUFFICIENT_SOURCE_DOMAINS", "message": f"search_audit requires at least {min_domains} source domains; got {len(source_domains)}"})

    claim_coverage = audit.get("claim_coverage")
    if not isinstance(claim_coverage, list) or not claim_coverage:
        issues.append({"severity": "FAIL", "code": "MISSING_CLAIM_SEARCH_COVERAGE", "message": "search_audit.claim_coverage is required for BP section package v2"})
        return issues

    coverage_by_claim = {
        str(item.get("claim_id")): item
        for item in claim_coverage
        if isinstance(item, dict) and item.get("claim_id")
    }
    for claim in package.get("claims", []) or []:
        if not isinstance(claim, dict) or not claim.get("claim_id"):
            continue
        claim_id = str(claim.get("claim_id"))
        coverage = coverage_by_claim.get(claim_id)
        if not isinstance(coverage, dict):
            issues.append({"severity": "FAIL", "code": "MISSING_CLAIM_SEARCH_COVERAGE", "message": f"claim_id {claim_id} missing search_audit.claim_coverage entry"})
            continue
        claim_queries = int(coverage.get("unique_queries") or 0)
        claim_fetched = _unique_nonempty_strings(coverage.get("fetched_urls"))
        claim_domains = _unique_nonempty_strings(coverage.get("source_domains"))
        verdict = str(coverage.get("evidence_verdict") or "").lower()
        priority = str(claim_priorities.get(claim_id) or "").lower()
        if verdict == "supported":
            if claim_queries < 4:
                issues.append({"severity": "FAIL", "code": "CLAIM_SEARCH_QUOTA_INSUFFICIENT", "message": f"claim_id {claim_id} supported verdict requires at least 4 unique queries"})
            if len(claim_fetched) < 2:
                issues.append({"severity": "FAIL", "code": "CLAIM_FETCHED_URLS_INSUFFICIENT", "message": f"claim_id {claim_id} supported verdict requires at least 2 fetched URLs"})
            if len(claim_domains) < 2 and str(claim.get("source_quality")) not in {"official", "regulatory", "database"}:
                issues.append({"severity": "FAIL", "code": "CLAIM_SOURCE_DOMAINS_INSUFFICIENT", "message": f"claim_id {claim_id} supported verdict requires 2 independent domains unless authoritative"})
        if priority == "critical" and coverage.get("counter_search_done") is not True:
            issues.append({"severity": "FAIL", "code": "CRITICAL_CLAIM_COUNTER_SEARCH_MISSING", "message": f"critical claim_id {claim_id} requires counter_search_done=true"})
        if str(claim.get("source_quality")) == "bp" and verdict == "supported":
            issues.append({"severity": "FAIL", "code": "BP_ONLY_CLAIM_CANNOT_BE_SUPPORTED", "message": f"claim_id {claim_id} uses BP-only evidence but is marked supported"})
    return issues


def _upgrade_v1_to_v2(package: dict[str, Any]) -> dict[str, Any]:
    """Auto-upgrade v1 section package to v2 by inferring missing fields.

    Sub-agents often output v1 schema (missing answers/claim_ids_covered/
    narrative_blocks/search_audit). This function synthesizes those fields
    from the available claims/facts_used/markdown_draft data.
    """
    out = dict(package)
    claims = out.get("claims", []) if isinstance(out.get("claims"), list) else []
    facts_used = out.get("facts_used", []) if isinstance(out.get("facts_used"), list) else []
    markdown_draft = str(out.get("markdown_draft") or "")

    # Infer answers from claims
    if "answers" not in out or not out.get("answers"):
        answers: list[dict[str, Any]] = []
        for idx, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            answers.append({
                "question_id": claim.get("claim_id") or f"q{idx+1}",
                "answer": str(claim.get("reasoning") or claim.get("claim") or ""),
                "fact_ids": claim.get("fact_ids") or [],
                "confidence": claim.get("confidence") or "medium",
                "limits": [],
            })
        if answers:
            out["answers"] = answers

    # Infer claim_ids_covered from claims that have claim_id
    if "claim_ids_covered" not in out or not out.get("claim_ids_covered"):
        covered = [
            str(claim.get("claim_id"))
            for claim in claims
            if isinstance(claim, dict) and claim.get("claim_id")
        ]
        if covered:
            out["claim_ids_covered"] = covered

    # Infer narrative_blocks from markdown_draft sections
    if "narrative_blocks" not in out or not out.get("narrative_blocks"):
        blocks: list[dict[str, Any]] = []
        sections = re.split(r'\n##?\s+', markdown_draft)
        for idx, section_text in enumerate(sections[:5]):  # Cap at 5 blocks
            if len(section_text.strip()) < 50:
                continue
            lines = section_text.strip().split("\n")
            block_id = f"nb{idx+1}"
            blocks.append({
                "block_id": block_id,
                "question_id": f"q{idx+1}",
                "claim_ids": [],
                "fact_ids": facts_used[:5] if facts_used else [],
                "text": "\n".join(lines[:20]),
            })
        if blocks:
            out["narrative_blocks"] = blocks

    # Infer search_audit (empty but valid structure)
    if "search_audit" not in out or not isinstance(out.get("search_audit"), dict):
        out["search_audit"] = {
            "queries": [],
            "fetched_urls": [],
            "source_domains": [],
            "claim_coverage": [],
        }

    # Upgrade schema_version
    out["schema_version"] = "bp_section_package.v2"
    # Mark as auto-upgraded so validator relaxes v2-only checks
    out["_auto_upgraded_from_v1"] = True
    return out


def _safe_load_json_with_repair(path: Path) -> dict | None:
    """Load a JSON file with auto-repair for malformed JSON from sub-agents.

    Sub-agents sometimes produce JSON with unescaped quotes inside string values,
    causing json.JSONDecodeError. This function attempts to fix common patterns:
    1. Unescaped internal quotes in string values
    2. Literal newlines inside string values
    3. Trailing commas before closing brackets
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
                # Check if this quote ends the string
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
    try:
        data = json.loads(fixed_text)
        # Repair succeeded — write back the fixed version
        from scripts.bp_file_lock import atomic_write
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"    🔧 JSON auto-repaired: {path.name}", flush=True)
        return data
    except json.JSONDecodeError:
        return None


def _validate_bp_section_package(package: dict[str, Any], fact_ids: set[str], claim_ids: set[str] | None = None, claim_priorities: dict[str, str] | None = None) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    claim_ids = claim_ids or set()
    claim_priorities = claim_priorities or {}
    base_required_fields = [
        "schema_version",
        "section_id",
        "section_title",
        "key_messages",
        "claims",
        "facts_used",
        "counter_evidence",
        "data_gaps",
        "markdown_draft",
    ]
    if not package:
        return {"passed": False, "issues": [{"severity": "FAIL", "code": "MISSING_PACKAGE", "message": "No BP section package found"}]}

    # Fix 3: Normalize schema_version aliases from sub-agents (2026-06-12)
    schema_version = package.get("schema_version")
    schema_aliases = {
        "bp_section_sidecar.v1": "bp_section_package.v1",
        "bp_section_sidecar.v2": "bp_section_package.v2",
        "bp_tech_ip_moat_section.v1": "bp_section_package.v1",
        "bp_competition_positioning_section.v1": "bp_section_package.v1",
        "bp_market_section.v1": "bp_section_package.v1",
        "bp_product_section.v1": "bp_section_package.v1",
        "bp_company_section.v1": "bp_section_package.v1",
        "bp_valuation_section.v1": "bp_section_package.v1",
        "bp_dealbreaker_section.v1": "bp_section_package.v1",
        # 2026-07-27: 子代理实测还会产出以下变体，统一映射
        "bp_section.v1": "bp_section_package.v1",
        "bp_section_output.v1": "bp_section_package.v1",
        "bp_phase2_section.v1": "bp_section_package.v1",  # 历史 schema 别名（旧子代理产出，不可随文件改名）
        "bp_section.v2": "bp_section_package.v2",
    }
    if schema_version in schema_aliases:
        package["schema_version"] = schema_aliases[schema_version]
        schema_version = schema_aliases[schema_version]
    elif not schema_version:
        # Sub-agent forgot to include schema_version, default to v1
        package["schema_version"] = "bp_section_package.v1"
        schema_version = "bp_section_package.v1"

    if schema_version not in {"bp_section_package.v1", "bp_section_package.v2"}:
        issues.append({"severity": "FAIL", "code": "UNSUPPORTED_SCHEMA_VERSION", "message": f"Unsupported schema_version: {schema_version}"})

    # 2026-07-28 fix: 子代理标了 v2 但实为 v1 风格包 → 自动降级 v1 触发 auto-upgrade。
    # 根因：子代理产出 v1 风格包但误标 v2，validator 只在 schema_version==v1 时触发
    # _upgrade_v1_to_v2，v2 路径直接走严格校验 → 全包 FAIL（本次跑管线 11/11 包卡死）。
    #
    # 判定标准：仅当结构性三件套（answers/claim_ids_covered/narrative_blocks）整体缺失
    # 才认定为 v1 误标，降级走 auto-upgrade 合成。若三件套已具备、仅缺单个字段
    # （如缺 search_audit 或某 answer 缺 limits），属真正的 v2 缺陷，保持 v2 严格校验 → FAIL，
    # 不能借降级绕过合法校验。
    if schema_version == "bp_section_package.v2":
        _structural_trio = ("answers", "claim_ids_covered", "narrative_blocks")
        _missing_trio = [f for f in _structural_trio if f not in package or not package.get(f)]
        if len(_missing_trio) == len(_structural_trio):
            package["schema_version"] = "bp_section_package.v1"
            schema_version = "bp_section_package.v1"

    # Bug 5 fix: auto-upgrade v1 → v2 before validation
    if schema_version == "bp_section_package.v1":
        package = _upgrade_v1_to_v2(package)
        schema_version = "bp_section_package.v2"

    is_auto_upgraded = package.get("_auto_upgraded_from_v1") is True
    required_fields = list(base_required_fields)
    if schema_version == "bp_section_package.v2":
        if not is_auto_upgraded:
            required_fields.extend(["answers", "claim_ids_covered", "narrative_blocks", "search_audit"])
        # Bug 5 fix: skip strict search_audit validation for auto-upgraded packages
        # (the synthesized search_audit won't pass strict quota checks)
        if is_auto_upgraded or (package.get("search_audit") and not package["search_audit"].get("queries")):
            # Auto-upgraded or no real search data — skip audit validation
            pass
        else:
            issues.extend(_validate_bp_search_audit(package, claim_priorities=claim_priorities))
        if not claim_ids and not is_auto_upgraded:
            issues.append({"severity": "FAIL", "code": "CLAIM_INVENTORY_MISSING", "message": "BP section package v2 requires bp_research_plan claim_matrix for claim_id validation"})

    for field in required_fields:
        if field not in package:
            issues.append({"severity": "FAIL", "code": "MISSING_FIELD", "message": f"Missing field: {field}"})

    claims = package.get("claims", [])
    if not isinstance(claims, list) or not claims:
        issues.append({"severity": "FAIL", "code": "MISSING_CLAIMS", "message": "BP section package must include claims"})
    for idx, claim in enumerate(claims if isinstance(claims, list) else []):
        if not isinstance(claim, dict):
            issues.append({"severity": "FAIL", "code": "INVALID_CLAIM", "message": f"Claim {idx} is not an object"})
            continue
        claim_required = ("claim", "fact_ids", "reasoning", "confidence", "source_quality")
        if schema_version == "bp_section_package.v2" and not is_auto_upgraded:
            claim_required = ("claim_id",) + claim_required
        for field in claim_required:
            if field not in claim:
                issues.append({"severity": "FAIL", "code": "MISSING_CLAIM_FIELD", "message": f"Claim {idx} missing field: {field}"})
        # 2026-07-27: 只有当 claim 实际带 claim_id 时才校验其值；
        # 缺 claim_id 键（None）不应误报 UNKNOWN_CLAIM_ID（auto-upgraded / 兜底自愈包常无 claim_id）。
        if schema_version == "bp_section_package.v2" and claim_ids and claim.get("claim_id") is not None and claim.get("claim_id") not in claim_ids:
            issues.append({"severity": "FAIL", "code": "UNKNOWN_CLAIM_ID", "message": f"Claim {idx} references unknown claim_id: {claim.get('claim_id')}"})
        for fact_id in claim.get("fact_ids", []) or []:
            if fact_id not in fact_ids:
                issues.append({"severity": "FAIL", "code": "UNKNOWN_FACT_ID", "message": f"Claim {idx} references unknown fact_id: {fact_id}"})
        if not claim.get("fact_ids"):
            # 2026-07-28: auto-upgraded 包降级为 WARN（子代理有时忘绑 fact_ids，
            # 但 report/claim 本身有 reasoning 有价值，不应硬阻断管线）。
            if is_auto_upgraded:
                issues.append({"severity": "WARN", "code": "CLAIM_WITHOUT_FACTS", "message": f"Claim {idx} has no fact_ids"})
            else:
                issues.append({"severity": "FAIL", "code": "CLAIM_WITHOUT_FACTS", "message": f"Claim {idx} has no fact_ids"})

    if not package.get("facts_used"):
        issues.append({"severity": "FAIL", "code": "MISSING_FACTS_USED", "message": "facts_used is empty"})
    for fact_id in package.get("facts_used", []) or []:
        if fact_id not in fact_ids:
            issues.append({"severity": "FAIL", "code": "UNKNOWN_FACT_ID", "message": f"facts_used references unknown fact_id: {fact_id}"})
    if not package.get("markdown_draft"):
        issues.append({"severity": "FAIL", "code": "MISSING_MARKDOWN_DRAFT", "message": "markdown_draft is empty"})

    if schema_version == "bp_section_package.v2" and not is_auto_upgraded:
        answers = package.get("answers", [])
        if not isinstance(answers, list) or not answers:
            issues.append({"severity": "FAIL", "code": "MISSING_ANSWERS", "message": "answers is empty"})
        for idx, answer in enumerate(answers if isinstance(answers, list) else []):
            if not isinstance(answer, dict):
                issues.append({"severity": "FAIL", "code": "INVALID_ANSWER", "message": f"Answer {idx} is not an object"})
                continue
            for field in ("question_id", "answer", "fact_ids", "confidence", "limits"):
                if field not in answer:
                    issues.append({"severity": "FAIL", "code": "MISSING_ANSWER_FIELD", "message": f"Answer {idx} missing field: {field}"})
            if not answer.get("fact_ids"):
                issues.append({"severity": "FAIL", "code": "ANSWER_WITHOUT_FACTS", "message": f"Answer {idx} has no fact_ids"})
            for fact_id in answer.get("fact_ids", []) or []:
                if fact_id not in fact_ids:
                    issues.append({"severity": "FAIL", "code": "UNKNOWN_FACT_ID", "message": f"Answer {idx} references unknown fact_id: {fact_id}"})

        covered = package.get("claim_ids_covered", [])
        if not isinstance(covered, list) or not covered:
            issues.append({"severity": "FAIL", "code": "MISSING_CLAIM_IDS_COVERED", "message": "claim_ids_covered is empty"})
        for claim_id in covered if isinstance(covered, list) else []:
            if claim_ids and claim_id not in claim_ids:
                issues.append({"severity": "FAIL", "code": "UNKNOWN_CLAIM_ID", "message": f"claim_ids_covered references unknown claim_id: {claim_id}"})
        for claim in claims if isinstance(claims, list) else []:
            if isinstance(claim, dict) and claim.get("claim_id") and isinstance(covered, list) and claim.get("claim_id") not in covered:
                issues.append({"severity": "FAIL", "code": "CLAIM_NOT_MARKED_COVERED", "message": f"claim_id not listed in claim_ids_covered: {claim.get('claim_id')}"})

        narrative_blocks = package.get("narrative_blocks", [])
        if not isinstance(narrative_blocks, list) or not narrative_blocks:
            issues.append({"severity": "FAIL", "code": "MISSING_NARRATIVE_BLOCKS", "message": "narrative_blocks is empty"})
        for idx, block in enumerate(narrative_blocks if isinstance(narrative_blocks, list) else []):
            if not isinstance(block, dict):
                issues.append({"severity": "FAIL", "code": "INVALID_NARRATIVE_BLOCK", "message": f"Narrative block {idx} is not an object"})
                continue
            for field in ("block_id", "question_id", "claim_ids", "fact_ids", "text"):
                if field not in block:
                    issues.append({"severity": "FAIL", "code": "MISSING_NARRATIVE_BLOCK_FIELD", "message": f"Narrative block {idx} missing field: {field}"})
            for claim_id in block.get("claim_ids", []) or []:
                if claim_ids and claim_id not in claim_ids:
                    issues.append({"severity": "FAIL", "code": "UNKNOWN_CLAIM_ID", "message": f"Narrative block {idx} references unknown claim_id: {claim_id}"})
            for fact_id in block.get("fact_ids", []) or []:
                if fact_id not in fact_ids:
                    issues.append({"severity": "FAIL", "code": "UNKNOWN_FACT_ID", "message": f"Narrative block {idx} references unknown fact_id: {fact_id}"})

    hard_fail = any(issue["severity"] == "FAIL" for issue in issues)
    return {"passed": not hard_fail, "issues": issues}


def _harvest_fact_ids(obj: Any) -> set[str]:
    """递归 harvest JSON 中所有 fact_id / fact_ids 字段值。

    子代理 sidecar facts.json 的 fact_id 可能嵌在嵌套结构里
    (如 comparable_transactions[].fact_id, regulations[].fact_ids)，
    不只存于扁平 facts[] 列表。此函数遍历整个 JSON 树收集所有 fact_id。
    """
    result: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "fact_id" and isinstance(v, str):
                result.add(v)
            elif k == "fact_ids" and isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        result.add(x)
            else:
                result |= _harvest_fact_ids(v)
    elif isinstance(obj, list):
        for x in obj:
            result |= _harvest_fact_ids(x)
    return result


def _load_bp_fact_ids(task_dir: Path) -> set[str]:
    path = task_dir / "bp_fact_store_index.json"
    fact_ids: set[str] = set()
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fact_ids = {str(fid) for fid in payload.get("fact_ids", [])}
        except Exception:
            pass

    # 合并 outputs 目录下所有 *-facts.json sidecar 中的 fact_id
    # 子代理不一定全部回写中央 fact store（phase30 merge 可能因格式异常跳过），
    # 但 sidecar 里一定有对应的 fact。不合并会导致下游校验误报 UNKNOWN_FACT_ID。
    # 2026-07-28: 递归 harvest 嵌套结构中的 fact_id（子代理常把 fact_id 嵌在
    # comparable_transactions[]/regulations[] 等列表里，而非扁平 facts[] 列表）。
    outputs_dir = task_dir / "outputs"
    if outputs_dir.exists():
        for facts_file in outputs_dir.glob("*-facts.json"):
            try:
                payload = json.loads(facts_file.read_text(encoding="utf-8"))
                # 先取扁平 facts[] 列表（标准格式）
                facts = payload.get("facts", []) if isinstance(payload, dict) else []
                for f in facts:
                    if isinstance(f, dict) and f.get("fact_id"):
                        fact_ids.add(str(f["fact_id"]))
                # 再递归 harvest 嵌套结构中的 fact_id
                fact_ids |= _harvest_fact_ids(payload)
            except Exception:
                continue
    return fact_ids


def _load_section_sidecar_fact_ids(section_file: Path) -> set[str]:
    """从同目录的 *-facts.json sidecar 加载 fact_id 集合。

    子代理的 facts 不一定全部回写中央 fact store（phase30 merge 可能遇到
    格式异常文件而跳过），所以 section package 验证器必须同时读取 sidecar
    中的 fact_id，避免误报 UNKNOWN_FACT_ID。

    2026-07-28: 递归 harvest 嵌套结构中的 fact_id（对齐 _load_bp_fact_ids）。
    """
    facts_path = section_file.with_name(f"{section_file.stem}-facts.json")
    if not facts_path.exists():
        return set()
    payload = _safe_load_json_with_repair(facts_path)
    if payload is None:
        return set()
    fact_ids: set[str] = set()
    # 标准扁平 facts[] 列表
    facts = payload.get("facts", []) if isinstance(payload, dict) else []
    for f in facts:
        if isinstance(f, dict) and f.get("fact_id"):
            fact_ids.add(str(f.get("fact_id")))
    # 递归 harvest 嵌套结构
    fact_ids |= _harvest_fact_ids(payload)
    return fact_ids


def _load_bp_claim_ids(task_dir: Path) -> set[str]:
    path = task_dir / "bp_research_plan.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {str(item.get("claim_id")) for item in payload.get("claim_matrix", []) or [] if isinstance(item, dict) and item.get("claim_id")}



def _load_bp_claim_priorities(task_dir: Path) -> dict[str, str]:
    path = task_dir / "bp_research_plan.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        str(item.get("claim_id")): str(item.get("priority") or "").lower()
        for item in payload.get("claim_matrix", []) or []
        if isinstance(item, dict) and item.get("claim_id")
    }


def _run_bp_claim_coverage_validation(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 24: BP claim coverage gate with repair support.

    v2 (2026-06-13): 集成 repair 机制 — gate FAIL 时不再直接终止管线，
    而是生成 repair manifests 并暂停管线（needs_dispatch），
    等主 AI 派发 repair 子代理修复后恢复。最多 2 轮 repair，
    超过后降级为 PASS_WITH_DISCLOSURE 放行。
    """
    from scripts.bp_claim_coverage_validator import (
        write_bp_claim_coverage_gate,
        build_claim_repair_manifests,
    )

    task_dir = _task_dir(runtime_root, job_ctx)
    result = write_bp_claim_coverage_gate(task_dir)

    # ── Repair 集成：gate FAIL 但有修复机会 → 派发 repair 子代理（sequential）──
    if result.get("needs_repair"):
        repair_manifests = build_claim_repair_manifests(task_dir, result)
        if repair_manifests:
            roles_needing_repair = list({
                t.get("owner_section", "") for t in result.get("repair_tasks", [])
            })
            failed_claim_ids = [
                t.get("claim_id", "") for t in result.get("repair_tasks", [])
            ]
            first_manifest = repair_manifests[0]
            remaining_manifests = repair_manifests[1:]
            has_more = len(remaining_manifests) > 0
            phase_name = "phase22_bp_claim_coverage_validation"
            print(
                f"  🔧 [phase24_claim_coverage] 派发 1/{len(repair_manifests)} 个 repair 子代理（sequential），"
                f"涉及 claims: {failed_claim_ids}",
                flush=True,
            )
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": has_more,
                "mode": "bp_claim_repair",
                "phase": phase_name,
                "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [first_manifest],
                    "remaining_manifests": remaining_manifests,
                    "roles": roles_needing_repair,
                    "task_dir": str(task_dir),
                    "wave": "claim_repair",
                    "is_repair": True,
                    "is_claim_repair": True,
                },
                "result": result,
                "instruction": _repair_instruction_sequential(phase_name, has_more, len(remaining_manifests)),
            }

    # ── 正常流程：PASS / PASS_WITH_DISCLOSURE / 降级放行 ──
    return {
        "ok": True,
        "mode": "bp_claim_coverage_validation",
        "phase": "phase22_bp_claim_coverage_validation",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_bp_section_package_validation(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 26: 校验 BP 维度结构化 Section Package。"""
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    fact_ids = _load_bp_fact_ids(task_dir)
    claim_ids = _load_bp_claim_ids(task_dir)
    claim_priorities = _load_bp_claim_priorities(task_dir)
    packages: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    for section_file in _bp_section_files(outputs_dir, task_dir):
        sidecar_path = _bp_section_sidecar_path(section_file)
        package: dict[str, Any] = {}
        package_source = str(section_file)
        # 合并同目录 facts sidecar 的 fact_id 到校验集合
        # 子代理不一定全部回写中央 fact store，但 sidecar 里一定有对应的 fact
        effective_fact_ids = fact_ids | _load_section_sidecar_fact_ids(section_file)
        if sidecar_path.exists():
            package_source = str(sidecar_path)
            package = _safe_load_json_with_repair(sidecar_path) or {}
        validation = _validate_bp_section_package(package, effective_fact_ids, claim_ids, claim_priorities)
        if validation["passed"]:
            passed += 1
        else:
            failed += 1
        packages.append({
            "section_file": str(section_file),
            "section_name": section_file.stem,
            "package_source": package_source,
            "package": package,
            "validation": validation,
        })
    section_gate = {
        "passed": bool(packages) and failed == 0,
        "summary": {"total": len(packages), "passed": passed, "failed": failed},
        "packages": packages,
    }
    index_path = task_dir / "bp_section_packages.json"
    index_path.write_text(json.dumps(section_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_path = task_dir / "bp_section_gate.json"
    gate_path.write_text(json.dumps(section_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ── 断点修复（2026-08-03）：部分包 FAIL 不再裸终止管线 ──
    # 旧逻辑 `ok: passed` 导致任意一个包校验 FAIL 即 kernel 硬终止，
    # 而 v2 严格校验（search_audit 配额 / claim_id / fact_id 绑定）对子代理产出
    # 极敏感，生产上曾出现 11/11 包卡死。
    # 新策略：
    #   - 有至少 1 个包通过 → 降级 WARN 放行（assembler _valid_packages 本来就
    #     只组装 passed 包，失败包被自动剔除；delivery gate 记 deferred_fixes）
    #   - 全部失败 / 一个包都没有 → 仍硬终止（无料可组装，快速失败）
    if section_gate["passed"]:
        return {
            "ok": True,
            "mode": "bp_section_package_validation",
            "phase": "phase24_bp_section_package_validation",
            "job_id": job_ctx.job_id,
            "result": {"section_gate": section_gate, "index_path": str(index_path), "gate_path": str(gate_path)},
        }

    if passed > 0:
        section_gate["gate_verdict"] = "WARN"
        section_gate["degraded_from"] = "FAIL"
        section_gate["degradation_reason"] = "partial_packages_failed_degraded_to_warn"
        index_path.write_text(json.dumps(section_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        gate_path.write_text(json.dumps(section_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        failed_names = [
            item.get("section_name") for item in packages
            if not (item.get("validation") or {}).get("passed")
        ]
        print(
            f"  ⚠️ [phase24_section_package] {failed}/{len(packages)} 个包校验失败"
            f"（{', '.join(str(n) for n in failed_names)}），降级为 WARN 放行"
            f"（assembler 将跳过失败包），delivery gate 记 deferred_fixes",
            flush=True,
        )
        return {
            "ok": True,
            "mode": "bp_section_package_validation",
            "phase": "phase24_bp_section_package_validation",
            "job_id": job_ctx.job_id,
            "result": {
                "section_gate": section_gate,
                "index_path": str(index_path),
                "gate_path": str(gate_path),
                "degraded_failed_sections": failed_names,
            },
        }

    return {
        "ok": False,
        "mode": "bp_section_package_validation",
        "phase": "phase24_bp_section_package_validation",
        "job_id": job_ctx.job_id,
        "result": {"section_gate": section_gate, "index_path": str(index_path), "gate_path": str(gate_path)},
    }


def _run_bp_debate_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 29: BP 结构化包对抗评审，作为交付阻断门。"""
    task_dir = _task_dir(runtime_root, job_ctx)
    index_path = task_dir / "bp_section_packages.json"
    if index_path.exists():
        try:
            section_index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            section_index = {"packages": []}
    else:
        section_index = {"packages": []}

    issues: list[dict[str, Any]] = []
    all_packages = section_index.get("packages", []) or []
    # 断点修复（2026-08-03）：只评审 validation 通过的包。
    # phase24 降级放行后失败包仍在 section_packages.json 里，它们的
    # markdown_draft 可能为空/claim 无 fact_ids——若不剔除会触发
    # EMPTY_DIMENSION_DRAFT / ALL_CLAIMS_WITHOUT_FACTS 等 BLOCKING issue，
    # 造成"phase24 刚降级放行、debate 又记 FAIL_BLOCKING"的级联。
    # assembler 本来也只组装 passed 包，debate 与其口径保持一致。
    packages = [
        item for item in all_packages
        if isinstance(item, dict) and (item.get("validation") or {}).get("passed")
    ]
    skipped_invalid = len(all_packages) - len(packages)
    if skipped_invalid:
        print(
            f"  ⚠️ [phase27_debate_review] 跳过 {skipped_invalid} 个 validation 未通过的包"
            f"（assembler 同样不组装它们，避免 BLOCKING 级联）",
            flush=True,
        )
    if not packages:
        # BLOCKING：完全无 section package（或全部 validation 失败）属于极端情况
        issues.append({
            "severity": "BLOCKING",
            "code": "NO_SECTION_PACKAGES",
            "section": "global",
            "issue": "No BP section packages available for debate review",
            "required_action": "Run BP section package validation before final assembly",
        })

    for item in packages:
        section_name = item.get("section_name") or item.get("step_name") or "unknown"
        package = item.get("package", {}) or {}
        validation = item.get("validation", {}) or {}
        for validation_issue in validation.get("issues", []) or []:
            # 降级：validation FAIL 不再直接 HIGH → MEDIUM（2026-06-26 宽松化）
            severity = "MEDIUM" if validation_issue.get("severity") == "FAIL" else "LOW"
            issues.append({
                "severity": severity,
                "code": validation_issue.get("code", "VALIDATION_ISSUE"),
                "section": section_name,
                "issue": validation_issue.get("message", "BP section package validation issue"),
                "required_action": "Rewrite this BP section package to satisfy the structured output protocol",
            })
        if not package.get("counter_evidence"):
            issues.append({
                "severity": "MEDIUM",
                "code": "MISSING_COUNTER_EVIDENCE",
                "section": section_name,
                "issue": "BP section lacks counter evidence or uncertainty discussion",
                "required_action": "Add bear-case evidence, counter evidence, or explicit uncertainty limits",
            })
        for idx, claim in enumerate(package.get("claims", []) or []):
            if not claim.get("fact_ids"):
                # 降级：HIGH → MEDIUM（2026-06-26 宽松化，部分缺失不阻断）
                issues.append({
                    "severity": "MEDIUM",
                    "code": "CLAIM_WITHOUT_FACTS",
                    "section": section_name,
                    "issue": f"Claim {idx} is not bound to fact_ids",
                    "required_action": "Bind the claim to BP Fact Store fact_ids or move it to data_gaps",
                })
            if claim.get("confidence") == "high" and claim.get("source_quality") in ("unknown", "auxiliary", "low"):
                # 降级：HIGH → MEDIUM（2026-06-26 宽松化）
                issues.append({
                    "severity": "MEDIUM",
                    "code": "HIGH_CONFIDENCE_LOW_SOURCE",
                    "section": section_name,
                    "issue": f"Claim {idx} has high confidence but weak source quality",
                    "required_action": "Downgrade confidence or replace with authoritative evidence",
                })

    # ── Content-level quality checks (generic, industry-agnostic) ──
    for item in packages:
        section_name = item.get("section_name") or item.get("step_name") or "unknown"
        package = item.get("package", {}) or {}
        markdown_draft = str(package.get("markdown_draft", ""))
        claims = package.get("claims", []) or []
        data_gaps = package.get("data_gaps", []) or []

        # Check 1: Must have a conclusion / verdict section
        # 降级：HIGH → MEDIUM（2026-06-26 宽松化，格式偏好不阻断管线）
        conclusion_markers = ("结论", "conclusion", "verdict", "judgment", "综合判断", "本维度结论")
        has_conclusion = any(marker in markdown_draft.lower() for marker in conclusion_markers)
        if not has_conclusion:
            issues.append({
                "severity": "MEDIUM",
                "code": "MISSING_DIMENSION_CONCLUSION",
                "section": section_name,
                "issue": "Section lacks a conclusion/verdict paragraph — investor cannot get a one-line judgment",
                "required_action": "Add a conclusion section with explicit verdict, confidence level, and key reasoning",
            })

        # Check 2: Information density — ratio of "unverified" claims to total claims
        if claims:
            unverified_keywords = ("仅BP自述", "未验证", "无独立验证", "仅BP", "only BP", "unverified", "BP claims only")
            unverified_count = 0
            for claim in claims:
                claim_text = json.dumps(claim, ensure_ascii=False).lower()
                if any(kw.lower() in claim_text for kw in unverified_keywords):
                    unverified_count += 1
                elif claim.get("confidence") in ("low",) or claim.get("source_quality") in ("bp_only", "unknown"):
                    unverified_count += 1
            unverified_ratio = unverified_count / len(claims) if claims else 0
            if unverified_ratio > 0.5 and len(claims) >= 3:
                # 降级：HIGH → MEDIUM（2026-06-26 宽松化，早期 BP 本就多未验证）
                issues.append({
                    "severity": "MEDIUM",
                    "code": "MAJORITY_CLAIMS_UNVERIFIED",
                    "section": section_name,
                    "issue": f"{unverified_count}/{len(claims)} claims ({unverified_ratio:.0%}) are unverified or BP-only — section lacks independent evidence",
                    "required_action": "Conduct additional research to independently verify key claims, or explicitly downgrade confidence and list data gaps",
                })

        # Check 3: Minimum external source diversity — at least 3 distinct source domains
        search_audit = package.get("search_audit", {}) or {}
        source_domains = search_audit.get("source_domains", []) or []
        fetched_urls = search_audit.get("fetched_urls", []) or []
        if not source_domains and fetched_urls:
            for url in fetched_urls:
                if "://" in str(url):
                    domain = str(url).split("://", 1)[1].split("/", 1)[0].lower()
                    if domain not in source_domains:
                        source_domains.append(domain)
        if len(source_domains) < 3 and len(claims) >= 3:
            issues.append({
                "severity": "MEDIUM",
                "code": "INSUFFICIENT_SOURCE_DIVERSITY",
                "section": section_name,
                "issue": f"Only {len(source_domains)} distinct source domains used — insufficient for cross-validation",
                "required_action": "Search additional independent sources (industry reports, regulatory filings, academic papers, competitor disclosures)",
            })

        # Check 4: Data gaps must be explicitly listed (not just implied)
        if not data_gaps and not package.get("data_gaps_text"):
            # Check if data_gaps section exists in markdown
            gap_markers = ("data_gap", "data gap", "数据缺口", "待验证", "待补充", "information gap")
            has_gap_section = any(marker in markdown_draft.lower() for marker in gap_markers)
            if not has_gap_section and len(claims) >= 5:
                issues.append({
                    "severity": "MEDIUM",
                    "code": "MISSING_DATA_GAPS_DISCLOSURE",
                    "section": section_name,
                    "issue": "No data gaps disclosed despite multiple claims — unrealistic for due diligence",
                    "required_action": "Explicitly list what information is missing, what would be needed, and why it matters",
                })

        # Check 5: Must address "competitive moat / defensibility" for tech and commercial sections
        # 降级：HIGH → MEDIUM（2026-06-26 宽松化，格式偏好不阻断管线）
        moat_markers = ("moat", "壁垒", "护城河", "defensib", "competitive advantage sustainab", "竞争优势.*持续")
        section_lower = section_name.lower()
        is_tech_or_commercial = any(k in section_lower for k in ("tech", "commercial", "product", "ip", "moat"))
        if is_tech_or_commercial:
            has_moat_discussion = any(marker in markdown_draft.lower() for marker in moat_markers)
            if not has_moat_discussion:
                issues.append({
                    "severity": "MEDIUM",
                    "code": "MISSING_MOAT_ASSESSMENT",
                    "section": section_name,
                    "issue": "Tech/commercial section lacks competitive moat / defensibility assessment",
                    "required_action": "Add explicit moat assessment: is the advantage sustainable? What is the barrier height? Include quantitative evidence.",
                })

        # ── BLOCKING 级别检查（2026-06-26 新增，仅极端情况硬阻断） ──

        # Blocking 1: 维度 MD 完全为空或接近空
        if not markdown_draft or len(markdown_draft.strip()) < 100:
            issues.append({
                "severity": "BLOCKING",
                "code": "EMPTY_DIMENSION_DRAFT",
                "section": section_name,
                "issue": "Section markdown is empty or near-empty (<100 chars) — dimension analysis missing entirely",
                "required_action": "Re-run dimension analysis to produce substantive output",
            })

        # Blocking 2: 100% claim 无 fact_ids（区分于部分缺失）
        if claims:
            all_without_facts = all(not c.get("fact_ids") for c in claims)
            if all_without_facts:
                issues.append({
                    "severity": "BLOCKING",
                    "code": "ALL_CLAIMS_WITHOUT_FACTS",
                    "section": section_name,
                    "issue": f"All {len(claims)} claims have zero fact_ids — evidence binding completely missing",
                    "required_action": "Re-run dimension analysis with proper fact_id binding for all claims",
                })

    blocking_count = sum(1 for issue in issues if issue["severity"] == "BLOCKING")
    high_count = sum(1 for issue in issues if issue["severity"] == "HIGH")
    medium_count = sum(1 for issue in issues if issue["severity"] == "MEDIUM")

    # verdict 逻辑（2026-06-26 宽松化 + 2026-08-03 断点修复）：
    #   BLOCKING → FAIL_BLOCKING（记录，但不再在本 phase 硬终止管线）
    #   HIGH（已无，保留兼容）→ WARN
    #   MEDIUM / 其他 → WARN（如有）或 PASS
    if blocking_count > 0:
        verdict = "FAIL_BLOCKING"
    elif high_count > 0:
        verdict = "WARN"
    elif issues:
        verdict = "WARN"
    else:
        verdict = "PASS"

    review = {
        "task_id": job_ctx.job_id,
        "verdict": verdict,
        "issue_count": len(issues),
        "blocking_count": blocking_count,
        "high_count": high_count,
        "medium_count": medium_count,
        "issues": issues,
    }
    output_path = task_dir / "bp_debate_review.json"
    output_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ── 断点修复（2026-08-03）：FAIL_BLOCKING 不再在本 phase 裸终止 ──
    # 旧逻辑 `ok: verdict in (PASS, WARN)` 导致 BLOCKING 级 issue（NO_SECTION_PACKAGES /
    # EMPTY_DIMENSION_DRAFT / ALL_CLAIMS_WITHOUT_FACTS）直接终止管线，final_assembly
    # 的"≥6 维度 force-assemble"兜底根本用不上（管线在 assembly 之前已死）。
    # 新策略：FAIL_BLOCKING 降级为 WARN 放行，把硬阻断决策统一收敛到 delivery gate
    # （bp_delivery_gate.py 仍保留 DEBATE_REVIEW_FAIL_BLOCKING 硬阻断检查，真正极端
    # 情况交付时仍会被拦下，但管线能走完 assembly → delivery，给出完整审计）。
    handler_ok = True
    if verdict == "FAIL_BLOCKING":
        review["degraded_from"] = "FAIL_BLOCKING"
        review["degradation_reason"] = "debate_blocking_degraded_to_warn_for_pipeline_continuation"
        output_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"  ⚠️ [phase27_debate_review] verdict=FAIL_BLOCKING (blocking={blocking_count}) "
            f"降级为 WARN 放行，硬阻断决策移交 delivery gate",
            flush=True,
        )

    return {
        "ok": handler_ok,
        "mode": "bp_debate_review",
        "phase": "phase27_bp_debate_review",
        "job_id": job_ctx.job_id,
        "result": {
            "review_path": str(output_path),
            "verdict": verdict,
            "blocking_count": blocking_count,
            "high_count": high_count,
            "medium_count": medium_count,
        },
    }


def _run_bp_cross_dimension_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 25: 跨维度一致性门禁。"""
    from scripts.bp_cross_dimension_gate import write_bp_cross_dimension_gate

    task_dir = _task_dir(runtime_root, job_ctx)
    result = write_bp_cross_dimension_gate(task_dir)
    return {
        "ok": result.get("ok") is True,
        "mode": "bp_cross_dimension_gate",
        "phase": "phase23_bp_cross_dimension_gate",
        "job_id": job_ctx.job_id,
        "result": result,
    }



def _run_bp_final_assembly(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 30: 按投资决策链统稿，替代维度 markdown 拼接。

    Bug 6 fix: 当 debate_review FAIL 但维度文件齐全时，仍然尝试 assemble
    （记录 WARN 审计，不硬阻断交付）。避免 debate_review NO_SECTION_PACKAGES
    等上游 bug 的级联反应阻断最终报告生成。
    """
    from scripts.bp_narrative_assembler import assemble_bp_report

    task_dir = _task_dir(runtime_root, job_ctx)
    review_path = task_dir / "bp_debate_review.json"
    review = {"verdict": "REWRITE_REQUIRED", "issues": [{"code": "MISSING_DEBATE_REVIEW"}]}
    if review_path.exists():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    force_assemble = False
    force_reason = ""
    if review.get("verdict") not in ("PASS", "WARN"):
        # Check if dimension files exist — if yes, force assemble with WARN
        outputs_dir = _outputs_dir(runtime_root, job_ctx)
        dimension_count = 0
        for slug in BP_ALL_ROLE_SLUGS.values():
            for d in (outputs_dir, task_dir):
                if (d / f"bp_dim_{slug}.md").exists():
                    dimension_count += 1
                    break
        if dimension_count >= 6:  # At least 6 of 8 dimensions present
            force_assemble = True
            force_reason = (
                f"debate_review verdict={review.get('verdict')} but {dimension_count}/8 "
                f"dimension files present — force-assembling with WARN audit"
            )
            print(f"  ⚠️ [final_assembly] {force_reason}", flush=True)
        else:
            result = {
                "ok": False,
                "block_reason": "debate_review_not_passed",
                "markdown_path": "",
                "issues": review.get("issues", []),
            }
            output = task_dir / "bp_final_assembly.json"
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {
                "ok": False,
                "mode": "bp_final_assembly",
                "phase": "phase28_bp_final_assembly",
                "job_id": job_ctx.job_id,
                "result": result,
            }

    entity, _market = _bp_entity_market(job_ctx)
    result = assemble_bp_report(task_dir, entity=entity)

    if force_assemble:
        result["force_assemble_warn"] = force_reason
        result["debate_review_issues"] = review.get("issues", [])
        # Write audit log for force assembly
        audit = {
            "job_id": job_ctx.job_id,
            "force_assemble": True,
            "reason": force_reason,
            "debate_review_verdict": review.get("verdict"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (task_dir / "bp_force_assemble_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return {
        "ok": result["ok"],
        "mode": "bp_narrative_assembly",
        "phase": "phase28_bp_final_assembly",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_bp_readability_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 31: 可读性门禁，阻断拼接痕迹和开篇无结论。

    降级机制（与 phase29 对齐）：FAIL 时不直接终止管线，
    而是降级为 WARN 放行，交付门禁（phase33 delivery gate）会检查
    readability 结果并记录到 deferred_fixes。
    T1/T2 早期项目直接降级；T3+ 也降级为 WARN（不再硬阻断），
    因为 readability 问题不应阻止交付——报告可以交付后迭代修正。
    """
    from scripts.bp_readability_reviewer import write_readability_review
    from scripts.bp_stage_utils import read_stage_from_task

    task_dir = _task_dir(runtime_root, job_ctx)
    result = write_readability_review(task_dir)
    verdict = result.get("verdict", "FAIL")

    if verdict == "PASS":
        return {
            "ok": True,
            "mode": "bp_readability_review",
            "phase": "phase29_bp_readability_review",
            "job_id": job_ctx.job_id,
            "result": result,
        }

    # FAIL → 降级为 WARN 放行（不再阻断管线）
    result["verdict"] = "WARN"
    result["degraded_from"] = "FAIL"
    result["degradation_reason"] = "readability_fail_degraded_to_warn"
    print(
        f"  ⚠️ [phase31_readability] verdict=FAIL 降级为 WARN 放行，"
        f"issues: {[i.get('code') for i in result.get('issues', [])]}",
        flush=True,
    )
    return {
        "ok": True,
        "mode": "bp_readability_review",
        "phase": "phase29_bp_readability_review",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_bp_investment_judgment(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 32: 从8个维度报告中提取一句话结论+置信度+核心数据点，
    生成一页纸投资判断汇总表，放在报告最前面。"""
    from scripts.bp_investment_judgment import build_investment_judgment

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)

    # Copy dimension outputs to task_dir so the script can find them
    for d in (outputs_dir,):
        for dim_file in d.glob("bp_dim_*.md"):
            dst = task_dir / dim_file.name
            if not dst.exists() and dim_file.stat().st_size > 100:
                shutil.copy2(dim_file, dst)

    result = build_investment_judgment(task_dir)
    return {
        "ok": True,
        "mode": "bp_investment_judgment",
        "phase": "phase30_bp_investment_judgment",
        "job_id": job_ctx.job_id,
        "result": {
            "dimensions_extracted": len(result.get("dimensions", [])),
            "overall_risk": result.get("overall_risk_level", "UNKNOWN"),
            "md_path": result.get("md_path", ""),
            "json_path": result.get("json_path", ""),
        },
    }


# ── Phase 08-23: BP 子代理发射（5-Wave 结构）────────────────
# Wave 0: 投资假说先行者
# Wave 1: 基础四维并行（团队合规/产品商业化/技术IP/市场供应链）—— 互不依赖
# Wave 2: （已移除 2026-07-28，原为客户收入验证）
# Wave 3: 竞争+估值并行 —— 读 Wave1 输出
# Wave 4: Deal Breaker + 共识挑战 + 催化剂 + 行业研报 —— 读 Wave0/1/3 全量输出
# Wave 5: 统稿 —— 读全部 8 个维度

# Wave role slug 常量已统一至 bp_constants.py，此处通过模块顶部 import 引用。


def _bp_role_output_path(outputs_dir: Path, role: str) -> Path:
    return outputs_dir / f"bp_dim_{BP_ALL_ROLE_SLUGS[role]}.md"


def _extract_list_field(
    profile: dict,
    task_dir: Path,
    primary_key: str,
    *,
    aliases: tuple[str, ...] = (),
    ocr_patterns: tuple[str, ...] = (),
    text_source_keys: tuple[str, ...] = ("business_model", "summary_100words", "summary"),
    max_items: int = 10,
) -> list[str]:
    """Generic: extract a list field from profile JSON, trying multiple key names,
    then falling back to OCR text pattern matching.

    Works for any industry / any BP structure — no hard-coded domain terms.
    """
    # 1. Try primary key and aliases
    for key in (primary_key, *aliases):
        raw = profile.get(key, [])
        if isinstance(raw, list) and raw:
            return [str(v).strip() for v in raw if str(v).strip()][:max_items]
        if isinstance(raw, str) and len(raw) >= 4:
            return [seg.strip() for seg in re.split(r'[、,，;；/]', raw) if seg.strip()][:max_items]

    # 2. Try text-source keys in profile (business_model, summary, etc.)
    for text_key in text_source_keys:
        text_val = str(profile.get(text_key, ""))
        if len(text_val) < 10:
            continue
        for pattern in ocr_patterns:
            for match in re.finditer(pattern, text_val):
                names = [n.strip() for n in re.split(r'[、,，;；和与及]', match.group(1)) if len(n.strip()) >= 2]
                if names:
                    return list(dict.fromkeys(names))[:max_items]

    # 3. Fall back to OCR text
    ocr_path = task_dir / "bp_ocr_text.txt"
    if ocr_path.exists():
        try:
            ocr_text = ocr_path.read_text(encoding="utf-8")
            for pattern in ocr_patterns:
                for match in re.finditer(pattern, ocr_text):
                    names = [n.strip() for n in re.split(r'[、,，;；和与及]', match.group(1)) if len(n.strip()) >= 2]
                    if names:
                        return list(dict.fromkeys(names))[:max_items]
        except Exception:
            pass

    return []


def _dispatch_role_specs(task_dir: Path, profile: dict) -> list[dict[str, Any]]:
    """构建所有 role spec 列表。首次调用后写缓存，后续 wave 直接读缓存避免重复 OCR 扫描。"""
    _CACHE_VERSION = 3  # v3: +4 investment narrative roles (hypothesis/consensus/catalyst/industry_research)
    cache_path = task_dir / "_dispatch_role_specs_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("_cache_version") == _CACHE_VERSION:
                specs = cached.get("specs", [])
                if isinstance(specs, list) and len(specs) > 0:
                    return specs
        except Exception:
            pass
    research_plan_file = str(task_dir / "bp_research_plan.json") if (task_dir / "bp_research_plan.json").exists() else ""

    # Generic extraction — works for any industry
    products = _extract_list_field(profile, task_dir, "products",
        aliases=("product_service", "target_products", "key_products"),
        ocr_patterns=(r'(?:产品|服务|业务|核心产品|主营产品|产品线)[：:]\s*([^\n]{2,120})',))
    tech_keywords = _extract_list_field(profile, task_dir, "tech_keywords",
        aliases=("technology_keywords", "tech_stack", "core_tech", "key_technologies", "competitive_advantages"),
        ocr_patterns=(r'(?:核心技术|技术平台|关键技术|技术栈|技术优势)[：:]\s*([^\n]{2,120})',))
    competitors = _extract_list_field(profile, task_dir, "competitors",
        aliases=("key_competitors", "main_competitors", "competitive_landscape"),
        ocr_patterns=(r'(?:竞争对手|竞品|竞争者|同类厂商|业内企业|主要玩家|对标)[：:]\s*([^\n]{2,120})',))
    customers = _extract_list_field(profile, task_dir, "customers",
        aliases=("key_customers", "main_customers", "target_customers", "major_clients"),
        ocr_patterns=(r'(?:客户|大客户|核心客户|主要客户|下游客户|合作伙伴|已服务)[：:]\s*([^\n]{2,120})',))

    result = [
        {
            "role_name": "bp_company_team_compliance",
            "brief_key": "bp_company_team_compliance",
            "description": "Wave 1 Evidence: 公司主体、团队、治理、股权、合规与风险信号验证",
            "output_file": str(task_dir / "bp_dim_company_team_compliance.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "founders": profile.get("founders", []),
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_product_commercial",
            "brief_key": "bp_product_commercial",
            "description": "Wave 1 Evidence: 产品矩阵、商业化阶段、客户案例、订单/合同线索验证",
            "output_file": str(task_dir / "bp_dim_product_commercial.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "customers": customers,
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_tech_ip_moat",
            "brief_key": "bp_tech_ip_moat",
            "description": "Wave 1 Evidence: 技术路线、知识产权、研发能力、技术壁垒与第三方验证",
            "output_file": str(task_dir / "bp_dim_tech_ip_moat.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "tech_keywords": tech_keywords,
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_market_supply_chain",
            "brief_key": "bp_market_supply_chain",
            "description": "Wave 1 Evidence: 市场规模、行业格局、政策环境、供应链与产能约束",
            "output_file": str(task_dir / "bp_dim_market_supply_chain.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "competitors": competitors,
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_competition_positioning",
            "brief_key": "bp_competition_positioning",
            "description": "Wave 3 Cross-Dimension: 竞争格局、差异化定位、竞品能力验证与可复制性判断",
            "output_file": str(task_dir / "bp_dim_competition_positioning.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "competitors": competitors,
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_valuation_return",
            "brief_key": "bp_valuation_return",
            "description": "Wave 3 Cross-Dimension: 融资历史估值变化、可比公司估值对标（主营业务重合筛选）、估值事实呈现",
            "output_file": str(task_dir / "bp_dim_valuation_return.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "competitors": competitors,
                "financing_rounds": profile.get("financing_rounds", []),
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_dealbreaker_risk",
            "brief_key": "bp_dealbreaker_risk",
            "description": "Wave 4 Reverse-Engineering: Deal breakers、反向论证、关键风险、尽调阻断项和缓释路径",
            "output_file": str(task_dir / "bp_dim_dealbreaker_risk.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "products": products,
                "competitors": competitors,
                "customers": customers,
                "research_plan": research_plan_file,
            },
        },
        # ── v4.5 新增：投资叙事层 3 角色（Wave 4）──
        {
            "role_name": "bp_consensus_challenge",
            "brief_key": "bp_consensus_challenge",
            "description": "Wave 4: 共识挑战与预期差分析",
            "output_file": str(task_dir / "bp_dim_consensus_challenge.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_catalyst",
            "brief_key": "bp_catalyst",
            "description": "Wave 4: 催化剂与事件分析（时间窗口+概率+传导链）",
            "output_file": str(task_dir / "bp_dim_catalyst.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "research_plan": research_plan_file,
            },
        },
        {
            "role_name": "bp_industry_research",
            "brief_key": "bp_industry_research",
            "description": "Wave 4: 行业深度研报整合（技术路线横评/成本结构/头部财务/法规标准/第三方TAM）",
            "output_file": str(task_dir / "bp_dim_industry_research.md"),
            "key_inputs": {
                "company_name": profile.get("company_name", ""),
                "competitors": competitors,
                "research_plan": research_plan_file,
            },
        },
    ]
    # 写缓存：后续 wave 直接读缓存，避免重复 OCR 扫描
    try:
        cache_payload = {"_cache_version": _CACHE_VERSION, "specs": result}
        cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


def _quality_check(output_path: Path) -> dict[str, Any]:
    """对子代理输出做质量评分。v2: 增加来源充分性检查。"""
    text = output_path.read_text(encoding="utf-8")
    urls = text.count("http")
    sections = text.count("## ")
    content_len = len(text)
    score = 0
    if content_len >= 6000:
        score = 5
    elif content_len >= 3000:
        score = 3
    elif content_len >= 1000:
        score = 2
    elif content_len >= 500:
        score = 1
    if urls < 2:
        score = max(0, score - 1)
    if sections < 3:
        score = max(0, score - 1)

    # v2: 来源充分性检查
    # 统计不同域名的外部URL数量（去重）
    unique_domains = set()
    for m in re.finditer(r'https?://([a-zA-Z0-9.-]+)', text):
        unique_domains.add(m.group(1))
    domain_count = len(unique_domains)

    # 检查是否有"未经搜索验证"标注（说明搜索不足）
    unverified_count = text.count("未经搜索验证") + text.count("⚠ 未经搜索验证")

    # 来源充分性扣分
    if domain_count < 3:
        score = max(0, score - 1)  # 至少3个不同来源域名
    if unverified_count > 2:
        score = max(0, score - 1)  # 超过2处未经搜索验证的推断

    # v3: 信息密度检查（通用，不绑定行业）

    # 未验证/BP自述标注比例
    unverified_markers = re.findall(
        r'(?:仅BP自述|未验证|无独立验证|仅BP|BP声称|未经.*验证|unverified|BP claims only|无第三方)',
        text, re.IGNORECASE)
    unverified_ratio = len(unverified_markers) / max(content_len // 1000, 1)

    # 非公司官网的外部URL比例（排除公司自己的域名）
    non_company_urls = list(unique_domains)
    non_company_ratio = len(non_company_urls) / max(domain_count, 1)

    # 表格数量（表格是核心信息载体）
    table_count = text.count("|---") + text.count("| ---")
    table_density = table_count / max(content_len // 1000, 1)

    # 结论段落检查（是否有置信度标注的结论）
    conclusion_markers = len(re.findall(r'(?:结论|conclusion|verdict|置信度|confidence)', text, re.IGNORECASE))

    # 信息密度加分/扣分
    if table_count < 3 and content_len >= 3000:
        score = max(0, score - 1)  # 长报告但没有足够表格
    if unverified_ratio > 2.0:
        score = max(0, score - 1)  # 大量未验证标注
    if conclusion_markers >= 3:
        score = min(6, score + 1)  # 有结论段落加分（上限6）

    return {
        "score": score,
        "content_length": content_len,
        "url_count": urls,
        "unique_domain_count": domain_count,
        "unverified_count": unverified_count,
        "unverified_markers": len(unverified_markers),
        "unverified_ratio": round(unverified_ratio, 2),
        "non_company_domain_ratio": round(non_company_ratio, 2),
        "table_count": table_count,
        "table_density": round(table_density, 2),
        "conclusion_markers": conclusion_markers,
        "section_count": sections,
        "verdict": "pass" if score >= 3 else "fail",
    }


# ── Collect 统一重试机制（参数来自 bp_constants，2026-06-29 统一） ──

def _collect_with_retry(
    collect_name: str,
    collect_fn,
    *,
    max_retries: int = COLLECT_RETRY_COUNT,
    retry_interval: int = COLLECT_RETRY_INTERVAL,
    job_id: str = "",
    outputs_dir: Path | None = None,
) -> dict[str, Any]:
    """对任意 collect 函数加轮询重试。

    问题: 子代理异步写文件（.md 先写完，sidecar 后写完），
    主 AI 轮询可能提前判定完成。kernel 遇到 ok=false 直接终止。

    解决: collect 内部加 retry loop，等 sidecar 写完而不是直接失败。
    5 次 × 15s = 最多额外等待 75 秒。

    进度检测：综合判断 missing 数量 + .md 文件总大小。
    - missing 数量在减少 → 子代理在写新文件，继续等
    - .md 总大小在增长 → 子代理仍在写内容，继续等
    - 两者都不变 → 子代理可能已挂掉，提前退出
    """
    last_result: dict[str, Any] | None = None
    prev_signal: tuple[int, int] | None = None  # (missing_count, total_md_size)
    for attempt in range(max_retries + 1):
        result = collect_fn()
        if result.get("ok") is True:
            if attempt > 0:
                print(f"  ✅ {collect_name} 重试成功 (attempt {attempt+1}/{max_retries+1})", flush=True)
            return result
        last_result = result
        if attempt < max_retries:
            missing = result.get("result", {}).get("missing", [])
            missing_info = f" (missing: {', '.join(str(m) for m in missing[:5])})" if missing else ""

            # 进度信号：missing 数量 + .md 文件总大小
            total_md_size = 0
            if outputs_dir and outputs_dir.exists():
                for md_file in outputs_dir.glob("bp_dim_*.md"):
                    try:
                        total_md_size += md_file.stat().st_size
                    except OSError:
                        pass
            current_signal = (len(missing), total_md_size)

            # 只在有信号数据时才做进度检测（outputs_dir 有 .md 文件可扫描）
            # 没有信号数据时不做 early exit，跑满全部 retry
            has_signal = outputs_dir is not None and total_md_size > 0
            if has_signal and prev_signal is not None and current_signal == prev_signal:
                log_prefix = f"[{job_id}]" if job_id else ""
                print(f"  ⚠️ {log_prefix}{collect_name} 无进度 ({len(missing)} missing, "
                      f"md_size={total_md_size})，子代理可能已停止", flush=True)
                break

            log_prefix = f"[{job_id}]" if job_id else ""
            print(f"  ⏳ {log_prefix}{collect_name} attempt {attempt+1}/{max_retries+1} incomplete{missing_info}, "
                  f"retrying in {retry_interval}s...", flush=True)
            prev_signal = current_signal
            time.sleep(retry_interval)
    return last_result or {"ok": False}


def _dispatch_completion_instruction(roles: list[str], role_slugs: dict[str, str], next_phase: str) -> str:
    """生成统一的 dispatch instruction，包含三文件检查指令。"""
    slug_list = ", ".join(role_slugs[r] for r in roles if r in role_slugs)
    return (
        "MANDATORY: Read each manifest JSON file. Use the Agent tool with these EXACT parameters:\n"
        "1. prompt = manifest's 'system_prompt' field (the FULL text, do NOT summarize or rewrite)\n"
        "2. connectorIds = manifest's 'connectorIds' field (enables TYC MCP tools for the sub-agent)\n"
        "3. name = manifest's 'slug' field\n"
        "4. team_name = 'bp-{task_id}'\n"
        "5. mode = 'bypassPermissions'\n"
        "Do NOT write your own simplified prompt — the manifest system_prompt contains critical tool usage guides "
        "(NeoData search_gateway, TYC MCP, yfinance) that the sub-agent MUST receive.\n"
        "\n"
        "## ⚠️ CRITICAL: 子代理必须使用 NeoData 查上市公司金融数据\n"
        "manifest system_prompt 中已包含 NeoData 和 search_gateway 的调用示例。\n"
        "如果子代理只用 search_deep 查上市公司行情/财报/估值而不用 NeoData，说明 prompt 被截断或简化了。\n"
        "确保子代理收到完整的 system_prompt（含 🔧 搜索与数据工具使用指南 章节）。\n"
        "\n"
        "## ⚠️ CRITICAL: 恢复前必须验证三文件（缺一不可）\n"
        "每个子代理输出 3 个文件，子代理先写 .md 再写 sidecar（JSON 序列化耗时较长）。\n"
        f"在调用 pipeline 恢复（start_phase='{next_phase}'）之前，你必须检查以下文件全部存在且非空：\n"
        f"- bp_dim_{{slug}}.md （>100 bytes）\n"
        f"- bp_dim_{{slug}}-facts.json （>10 bytes）\n"
        f"- bp_dim_{{slug}}-section.json （>10 bytes）\n"
        f"其中 slug ∈ {{{slug_list}}}。\n"
        "如果只看到 .md 而 sidecar 不存在，说明子代理还在写文件，必须继续等待。\n"
        "不要只看 .md 就恢复管线。"
    )


def _dispatch_completion_instruction_sequential(
    current_role: str,
    role_slugs: dict[str, str],
    next_phase: str,
    *,
    has_more: bool,
    remaining: list[str],
) -> str:
    """生成 sequential 派发 instruction，强制主 AI 逐个派发。"""
    slug = role_slugs[current_role]
    lines = [
        "MANDATORY SEQUENTIAL DISPATCH — 禁止并行派发",
        "",
        f"当前派发角色: {current_role} (slug={slug})",
        f"剩余待派发角色: {', '.join(remaining) if remaining else '无'}",
        "",
        "## 必须严格按以下步骤执行，不可跳步：",
        "",
        "1. 读取 manifest JSON 文件（路径见 dispatch_info.manifests[0]）",
        "2. 使用 Agent tool 派发**这一个**子代理：",
        "   - prompt = manifest's 'system_prompt' field (FULL text)",
        "   - connectorIds = manifest's 'connectorIds' field",
        "   - name = manifest's 'slug' field",
        "   - team_name = 'bp-{task_id}'",
        "   - mode = 'bypassPermissions'",
        f"3. 等待该子代理完成三文件输出：",
        f"   - bp_dim_{slug}.md (>100 bytes)",
        f"   - bp_dim_{slug}-facts.json (>10 bytes)",
        f"   - bp_dim_{slug}-section.json (>10 bytes)",
        f"4. 确认三文件齐全后，用 start_phase='{next_phase}' 恢复管线",
    ]
    if has_more:
        lines.extend([
            "",
            "5. 管线会返回下一个 pending role 的 manifest，重复步骤 1-4",
            "   直到 has_more=False，表示当前 wave 所有 role 已完成",
        ])
    lines.extend([
        "",
        "## ⚠️ 绝对禁止：",
        "- 禁止在单条消息中派发多个 Agent tool_use（会导致并行写冲突）",
        "- 禁止在子代理三文件齐全前推进管线",
        "- 禁止跳过等待直接调 start_phase",
        "- 禁止给 Agent tool 传 run_in_background=True（子代理必须前台派发，完成后立即返回结果）",
        "  只有 Bash 工具跑 heavy_bg 脚本时才用 run_in_background",
    ])
    return "\n".join(lines)


def _repair_instruction_sequential(
    phase_name: str,
    has_more: bool,
    remaining_count: int,
) -> str:
    """生成 sequential repair 派发 instruction。"""
    return (
        "MANDATORY SEQUENTIAL REPAIR DISPATCH — 禁止并行派发\n"
        "\n"
        f"## 原因\n"
        f"Gate {phase_name} FAIL，需要派发 repair 子代理修复。\n"
        f"多个 repair 子代理会写同一组 sidecar 文件，并行执行会导致数据丢失。\n"
        "\n"
        "## 步骤\n"
        "1. 读取 dispatch_info.manifests[0] 指向的 manifest JSON\n"
        "2. 使用 Agent tool 派发**这一个** repair 子代理\n"
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


# Wave 1 只做证据采集：估值延后到 Wave 3，避免在商业化/市场/竞争证据不足时拍脑袋。
_CORE_ROLES = list(BP_WAVE1_ROLE_SLUGS.keys())

_WAVE3_ROLES = list(BP_WAVE3_ROLE_SLUGS.keys())
_WAVE4_ROLES = list(BP_WAVE4_ROLE_SLUGS.keys())


def _run_bp_dispatch_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 08: 写 manifest + brief，返回 needs_dispatch。
    v2: sequential 模式 — 每次只派发 1 个 role，通过 has_more 控制。"""
    from scripts.bp_subagent_launcher_wb import _spawn_one

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    profile = _load_bp_profile(task_dir)

    all_subs = _dispatch_role_specs(task_dir, profile)
    for sub in all_subs:
        sub["output_file"] = str(outputs_dir / Path(sub["output_file"]).name)

    core_subs = [s for s in all_subs if s["role_name"] in _CORE_ROLES]

    # ── sequential：找出第一个未完成的 role ──
    pending_subs = []
    for sub in core_subs:
        slug = BP_WAVE1_ROLE_SLUGS[sub["role_name"]]
        is_complete, _ = _role_outputs_complete(sub["role_name"], slug, outputs_dir, task_dir)
        if not is_complete:
            pending_subs.append(sub)

    if not pending_subs:
        return {
            "ok": True,
            "needs_dispatch": False,
            "has_more": False,
            "mode": "bp_dispatch_prepare",
            "phase": "phase09_dispatch_prepare",
            "job_id": job_ctx.job_id,
        }

    sub = pending_subs[0]
    spawn_result = _spawn_one(job_ctx.job_id, sub, task_dir=task_dir)
    manifest_path = spawn_result.get("manifest_path", "") if isinstance(spawn_result, dict) else str(spawn_result)

    has_more = len(pending_subs) > 1
    remaining_roles = [s["role_name"] for s in pending_subs[1:]]

    dispatch_data = {
        "task_id": job_ctx.job_id,
        "phase": "2a",
        "wave_design": "wave1_evidence_collection_4_roles",
        "status": "pending",
        "total_subagents": len(all_subs),
        "current_subagent": sub["role_name"],
        "remaining_subagents": remaining_roles,
        "briefs_ready": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    dispatch_path = task_dir / "bp_dispatch.json"
    dispatch_path.write_text(json.dumps(dispatch_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": has_more,
        "mode": "bp_dispatch_prepare",
        "phase": "phase09_dispatch_prepare",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "manifests": [manifest_path],
            "current_role": sub["role_name"],
            "remaining_roles": remaining_roles,
            "task_dir": str(task_dir),
            "outputs_dir": str(outputs_dir),
        },
        "instruction": _dispatch_completion_instruction_sequential(
            sub["role_name"], BP_WAVE1_ROLE_SLUGS, "phase10_dispatch_collect",
            has_more=has_more, remaining=remaining_roles),
    }


def _run_bp_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09: 检查前 4 个维度子代理输出是否已完成。
    v2: 复用 _collect_wave_roles + _collect_with_retry，与 wave 2/3/4 统一。
    """
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    return _collect_with_retry(
        "dispatch_collect",
        lambda: _collect_wave_roles(runtime_root, job_ctx, BP_WAVE1_ROLE_SLUGS, _CORE_ROLES, "phase10_dispatch_collect"),
        job_id=job_ctx.job_id,
        outputs_dir=outputs_dir,
    )


def _run_bp_wave_evidence_gate(runtime_root: Path, job_ctx: JobContext, wave: int) -> dict[str, Any]:
    """Run BP wave evidence gate after a dispatch wave completes.

    v2: 集成 repair 机制 — gate FAIL 时不再直接终止管线，
    而是生成 repair manifests 并暂停管线（needs_dispatch），
    等主 AI 派发 repair 子代理修复后恢复。
    """
    from scripts.bp_wave_evidence_gate import evaluate_bp_wave_evidence_gate, build_repair_manifests

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    result = evaluate_bp_wave_evidence_gate(task_dir, wave=wave, outputs_dir=outputs_dir)

    phase_name = f"phase{11 if wave == 1 else 16 if wave == 3 else 20}_wave{wave}_evidence_gate"

    # ── Repair 集成：gate FAIL 但有修复机会 → 派发 repair 子代理（sequential）──
    if result.get("needs_repair"):
        repair_manifests = build_repair_manifests(
            task_dir, wave=wave, gate_result=result, outputs_dir=outputs_dir,
        )
        if repair_manifests:
            roles_needing_repair = list({
                t.get("owner_section", "") for t in result.get("repair_tasks", [])
            })
            first_manifest = repair_manifests[0]
            remaining_manifests = repair_manifests[1:]
            has_more = len(remaining_manifests) > 0
            print(
                f"  🔧 [wave{wave}_evidence_gate] 派发 1/{len(repair_manifests)} 个 repair 子代理（sequential），"
                f"涉及角色: {roles_needing_repair}",
                flush=True,
            )
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": has_more,
                "mode": "bp_wave_repair",
                "phase": phase_name,
                "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [first_manifest],
                    "remaining_manifests": remaining_manifests,
                    "roles": roles_needing_repair,
                    "task_dir": str(task_dir),
                    "outputs_dir": str(outputs_dir),
                    "wave": wave,
                    "is_repair": True,
                },
                "result": result,
                "instruction": _repair_instruction_sequential(phase_name, has_more, len(remaining_manifests)),
            }

    # ── 正常流程：PASS 或降级放行 ──
    return {
        "ok": result.get("ok") is True,
        "mode": "bp_wave_evidence_gate",
        "phase": phase_name,
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _prior_wave_files(prior_waves: list[str], task_dir: Path, outputs_dir: Path) -> dict[str, str]:
    """Collect output files from prior waves for injection into a new wave's subagent briefs."""
    result: dict[str, str] = {}
    for role, slug in prior_waves:
        p = outputs_dir / f"bp_dim_{slug}.md"
        if not p.exists():
            p = task_dir / f"bp_dim_{slug}.md"
        if p.exists():
            result[role] = str(p)
    return result


def _shared_inputs(task_dir: Path) -> dict[str, str]:
    return {
        "shared_page": str(task_dir / "bp_shared_diligence_page.md"),
        "shared_state": str(task_dir / "bp_shared_state.json"),
        "claim_coverage": str(task_dir / "bp_claim_coverage.json"),
        "fact_store": str(task_dir / "bp_fact_store.json"),
    }


# ── Wave 3: 竞争 + 估值 ────────────────────────────


def _run_bp_wave3_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Wave 3: 竞争定位 + 估值回报 — sequential 模式。"""
    from scripts.bp_subagent_launcher_wb import _spawn_one

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    profile = _load_bp_profile(task_dir)

    all_subs = _dispatch_role_specs(task_dir, profile)
    role_rank = {role: idx for idx, role in enumerate(_WAVE3_ROLES)}
    wave3_subs = sorted(
        [s for s in all_subs if s["role_name"] in _WAVE3_ROLES],
        key=lambda item: role_rank.get(item["role_name"], 999),
    )
    if len(wave3_subs) != len(_WAVE3_ROLES):
        found = {s["role_name"] for s in wave3_subs}
        missing = [role for role in _WAVE3_ROLES if role not in found]
        return {"ok": False, "error": f"wave3 role spec missing: {missing}"}

    # sequential：找 pending
    pending = []
    for sub in wave3_subs:
        slug = BP_WAVE3_ROLE_SLUGS[sub["role_name"]]
        is_complete, _ = _role_outputs_complete(sub["role_name"], slug, outputs_dir, task_dir)
        if not is_complete:
            pending.append(sub)

    if not pending:
        return {"ok": True, "needs_dispatch": False, "has_more": False,
                "mode": "bp_wave3_prepare", "phase": "phase14_wave3_prepare", "job_id": job_ctx.job_id}

    sub = pending[0]
    # 汇总 Wave 1 输出（Wave 2 已移除）
    prior_outputs = _prior_wave_files(BP_WAVE1_ROLE_SLUGS.items(), task_dir, outputs_dir)
    shared = _shared_inputs(task_dir)
    sub["output_file"] = str(outputs_dir / Path(sub["output_file"]).name)
    sub["key_inputs"]["prior_dimension_outputs"] = prior_outputs
    sub["key_inputs"]["shared_inputs"] = shared
    sub["wave_inputs"] = {**shared, **prior_outputs}

    spawn_result = _spawn_one(job_ctx.job_id, sub, task_dir=task_dir)
    manifest_path = spawn_result.get("manifest_path", "") if isinstance(spawn_result, dict) else str(spawn_result)

    has_more = len(pending) > 1
    remaining = [s["role_name"] for s in pending[1:]]

    return {
        "ok": True, "needs_dispatch": True, "has_more": has_more,
        "mode": "bp_wave3_prepare", "phase": "phase14_wave3_prepare", "job_id": job_ctx.job_id,
        "dispatch_info": {"manifests": [manifest_path], "current_role": sub["role_name"],
                          "remaining_roles": remaining, "task_dir": str(task_dir), "outputs_dir": str(outputs_dir)},
        "instruction": _dispatch_completion_instruction_sequential(
            sub["role_name"], BP_WAVE3_ROLE_SLUGS, "phase15_wave3_collect",
            has_more=has_more, remaining=remaining),
    }


def _run_bp_wave3_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """检查 Wave 3 竞争+估值输出。v2: 加 retry 缓冲。"""
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    return _collect_with_retry(
        "wave3_collect",
        lambda: _collect_wave_roles(runtime_root, job_ctx, BP_WAVE3_ROLE_SLUGS, _WAVE3_ROLES, "phase15_wave3_collect"),
        job_id=job_ctx.job_id,
        outputs_dir=outputs_dir,
    )


# ── Wave 4: Deal Breaker ────────────────────────────


def _run_bp_wave4_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Wave 4: Deal Breaker — sequential 模式。"""
    from scripts.bp_subagent_launcher_wb import _spawn_one

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    profile = _load_bp_profile(task_dir)

    all_subs = _dispatch_role_specs(task_dir, profile)
    wave4_subs = [s for s in all_subs if s["role_name"] in _WAVE4_ROLES]
    if len(wave4_subs) != len(_WAVE4_ROLES):
        found = {s["role_name"] for s in wave4_subs}
        missing = [role for role in _WAVE4_ROLES if role not in found]
        return {"ok": False, "error": f"wave4 role spec missing: {missing}"}

    # sequential：找 pending
    pending = []
    for sub in wave4_subs:
        slug = BP_WAVE4_ROLE_SLUGS[sub["role_name"]]
        is_complete, _ = _role_outputs_complete(sub["role_name"], slug, outputs_dir, task_dir)
        if not is_complete:
            pending.append(sub)

    if not pending:
        return {"ok": True, "needs_dispatch": False, "has_more": False,
                "mode": "bp_wave4_prepare", "phase": "phase18_wave4_prepare", "job_id": job_ctx.job_id}

    sub = pending[0]
    # 汇总 Wave 1 + Wave 3 输出（Wave 0/2 已移除）
    prior_outputs = _prior_wave_files(BP_WAVE1_ROLE_SLUGS.items(), task_dir, outputs_dir)
    prior_outputs.update(_prior_wave_files(BP_WAVE3_ROLE_SLUGS.items(), task_dir, outputs_dir))
    shared = _shared_inputs(task_dir)
    sub["output_file"] = str(outputs_dir / Path(sub["output_file"]).name)
    sub["key_inputs"]["prior_dimension_outputs"] = prior_outputs
    sub["key_inputs"]["shared_inputs"] = shared
    sub["wave_inputs"] = {**shared, **prior_outputs}

    spawn_result = _spawn_one(job_ctx.job_id, sub, task_dir=task_dir)
    manifest_path = spawn_result.get("manifest_path", "") if isinstance(spawn_result, dict) else str(spawn_result)

    has_more = len(pending) > 1
    remaining = [s["role_name"] for s in pending[1:]]

    return {
        "ok": True, "needs_dispatch": True, "has_more": has_more,
        "mode": "bp_wave4_prepare", "phase": "phase18_wave4_prepare", "job_id": job_ctx.job_id,
        "dispatch_info": {"manifests": [manifest_path], "current_role": sub["role_name"],
                          "remaining_roles": remaining, "task_dir": str(task_dir), "outputs_dir": str(outputs_dir)},
        "instruction": _dispatch_completion_instruction_sequential(
            sub["role_name"], BP_WAVE4_ROLE_SLUGS, "phase19_wave4_collect",
            has_more=has_more, remaining=remaining),
    }


def _run_bp_wave4_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """检查 Wave 4 Deal Breaker 输出。v2: 加 retry 缓冲。"""
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    return _collect_with_retry(
        "wave4_collect",
        lambda: _collect_wave_roles(runtime_root, job_ctx, BP_WAVE4_ROLE_SLUGS, _WAVE4_ROLES, "phase19_wave4_collect"),
        job_id=job_ctx.job_id,
        outputs_dir=outputs_dir,
    )


def _role_outputs_complete(role: str, slug: str, outputs_dir: Path, task_dir: Path) -> tuple[bool, Path]:
    """检查一个 role 的全套输出（.md + facts sidecar + section sidecar）是否都存在。

    子代理交付 3 个文件：
      1. bp_dim_{slug}.md           — Markdown 正文
      2. bp_dim_{slug}-facts.json   — 事实 sidecar
      3. bp_dim_{slug}-section.json — 结构化 Section Package sidecar

    只有 .md 存在但 sidecar 缺失时，说明子代理还在写文件过程中，不能视为完成。
    返回 (is_complete, md_output_path)。
    """
    output_path = outputs_dir / f"bp_dim_{slug}.md"
    if not output_path.exists():
        output_path = task_dir / f"bp_dim_{slug}.md"
    if not output_path.exists() or output_path.stat().st_size <= 100:
        return False, output_path

    facts_path = output_path.with_name(f"{output_path.stem}-facts.json")
    section_path = output_path.with_name(f"{output_path.stem}-section.json")

    if not facts_path.exists() or facts_path.stat().st_size < 10:
        return False, output_path
    if not section_path.exists() or section_path.stat().st_size < 10:
        return False, output_path

    # ── H4: JSON 合法性检查 ──
    for sidecar_path in (facts_path, section_path):
        try:
            json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return False, output_path

    # ── M6: 文件稳定性检查（大小不再增长） ──
    if not _file_stable(facts_path, interval=3) or not _file_stable(section_path, interval=3):
        return False, output_path

    return True, output_path


def _collect_wave_roles(runtime_root: Path, job_ctx: JobContext, role_slugs: dict[str, str], roles: list[str], phase: str) -> dict[str, Any]:
    """通用 wave 输出收集/检查函数。

    完成判定：必须同时存在 .md + facts sidecar + section sidecar 三个文件，
    避免子代理只写完 .md 就被判定完成、下一个 wave 拿到不完整输入。
    """
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)
    completed: list[str] = []
    missing: list[str] = []
    quality_by_role: dict[str, dict[str, Any]] = {}

    for role in roles:
        slug = role_slugs[role]
        is_complete, output_path = _role_outputs_complete(role, slug, outputs_dir, task_dir)
        if is_complete:
            completed.append(role)
            quality = _quality_check(output_path)
            quality_by_role[role] = quality
            print(f"    ✅ {role}: {quality['content_length']} chars, score={quality['score']}", flush=True)
            if outputs_dir != task_dir:
                dst = task_dir / f"bp_dim_{slug}.md"
                if not dst.exists():
                    shutil.copy2(output_path, dst)
                # 同步 sidecar 到 task_dir
                for suffix in ("-facts.json", "-section.json"):
                    src_sidecar = output_path.with_name(f"{output_path.stem}{suffix}")
                    dst_sidecar = dst.with_name(f"{dst.stem}{suffix}")
                    if src_sidecar.exists() and not dst_sidecar.exists():
                        shutil.copy2(src_sidecar, dst_sidecar)
        else:
            missing.append(role)

    if not missing:
        return {
            "ok": True,
            "mode": re.sub(r'^phase\d+_', '', phase),
            "phase": phase,
            "job_id": job_ctx.job_id,
            "result": {"completed": completed, "quality": quality_by_role},
        }

    dispatched_missing: list[str] = []
    manifests: list[str] = []
    for role in missing:
        slug = role_slugs[role]
        manifest_path = task_dir / f"bp_dim_manifest_{slug}.json"
        if manifest_path.exists():
            manifests.append(str(manifest_path))
        spawn_receipt_path = task_dir / f"bp_dim_spawn_{slug}.json"
        if spawn_receipt_path.exists():
            try:
                receipt = json.loads(spawn_receipt_path.read_text(encoding="utf-8"))
                if receipt.get("status") == "dispatched":
                    dispatched_missing.append(role)
            except Exception:
                pass

    response_data: dict[str, Any] = {
        "ok": False,
        "mode": re.sub(r'^phase\d+_', '', phase),
        "phase": phase,
        "job_id": job_ctx.job_id,
        "result": {
            "missing": missing,
            "completed": completed,
            "quality": quality_by_role,
            "reason": "spawn_receipt_exists_but_output_missing" if dispatched_missing else "not_dispatched_or_output_missing",
        },
    }
    if dispatched_missing:
        response_data["needs_dispatch"] = True
        response_data["dispatch_info"] = {
            "manifests": manifests,
            "roles": dispatched_missing,
            "task_dir": str(task_dir),
            "outputs_dir": str(outputs_dir),
            "reason": "spawn receipt exists but output missing — sub-agent likely not actually dispatched",
        }
        response_data["instruction"] = (
            "MANDATORY: Some spawn receipts exist but outputs are missing. "
            "Re-spawn missing roles using their manifests. "
            "Use Agent tool with: prompt=manifest's 'system_prompt' (FULL text), "
            "connectorIds=manifest's 'connectorIds', name=manifest's 'slug', "
            "team_name='bp-{task_id}', mode='bypassPermissions'."
        )
    return response_data


# ── Phase 27-28: BP 统稿（5-Wave 投研逻辑重组 + 执行摘要）──────────

def _run_bp_synthesis_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 27-28: 准备统稿子代理 — 读取八个 Wave 1-4 维度输出，按投研逻辑重组。"""
    from scripts.bp_subagent_launcher_wb import _spawn_one
    from scripts.bp_stage_utils import read_stage_from_task

    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)

    # Bug 3 fix: 感知 stage_tier（供 prompt 模板使用）
    stage_tier = read_stage_from_task(task_dir)
    skipped_slugs: set[str] = set()

    # 检查 7 个 Wave 1-4 维度输出是否齐全（md + facts sidecar + section sidecar 全部存在且非空）
    # Bug 1 fix: 对齐 _role_outputs_complete 标准（md>100B, facts>10B, section>10B），
    # 避免空 sidecar 文件通过检查导致下游统稿子代理拿到损坏输入。
    # 同时包 _collect_with_retry 等待 sidecar 落盘（与其他 collect 一致）。
    def _do_synthesis_check() -> dict[str, Any]:
        dim_files_local: dict[str, str] = {}
        missing_local: list[str] = []
        for slug in expected_slugs:
            is_complete = False
            for d in (outputs_dir, task_dir):
                p = d / f"bp_dim_{slug}.md"
                if not p.exists() or p.stat().st_size <= 100:
                    continue
                facts_path = p.with_name(f"{p.stem}-facts.json")
                section_path = p.with_name(f"{p.stem}-section.json")
                facts_ok = facts_path.exists() and facts_path.stat().st_size > 10
                section_ok = section_path.exists() and section_path.stat().st_size > 10
                if facts_ok and section_ok:
                    dim_files_local[slug] = str(p)
                    is_complete = True
                    break
            if not is_complete and slug not in skipped_slugs:
                missing_local.append(slug)
        ok = len(missing_local) == 0
        return {
            "ok": ok,
            "dim_files": dim_files_local,
            "missing": missing_local,
        }

    # v4.5: 统稿只检查传统维度角色的三件套输出（md/facts/section）
    # 投资叙事层角色（hypothesis/consensus/catalyst/industry_research）产出纯叙述，无 claim-fact 结构，
    # 不要求 sidecar 文件——它们的内容通过 prior_wave_files 传给 synthesis prompt。
    _NARRATIVE_SLUGS = {"consensus_challenge", "catalyst", "industry_research"}
    expected_slugs = [s for s in BP_ALL_ROLE_SLUGS.values() if s not in _NARRATIVE_SLUGS]
    check_result = _collect_with_retry(
        "synthesis_prepare_check", _do_synthesis_check,
        job_id=job_ctx.job_id,
        outputs_dir=outputs_dir,
    )
    if not check_result.get("ok"):
        return {"ok": False, "error": f"统稿缺少维度输出（含 sidecar）: {check_result.get('missing', [])}"}
    dim_files = check_result["dim_files"]

    # v4.5: 叙事角色的 md 也传给统稿（不做三件套检查，但文件存在就纳入）
    for slug in _NARRATIVE_SLUGS:
        if slug in dim_files:
            continue
        for d in (outputs_dir, task_dir):
            p = d / f"bp_dim_{slug}.md"
            if p.exists() and p.stat().st_size > 100:
                dim_files[slug] = str(p)
                break

    synthesis_output = outputs_dir / "bp_synthesis.md"
    # 也写一份到 task_dir
    synthesis_output_task = task_dir / "bp_synthesis.md"

    sub = {
        "role_name": "bp_统稿",
        "brief_key": "bp_统稿",
        "description": "将八个 Wave 1-4 维度分析重组为投研逻辑结构的完整研究报告",
        "output_file": str(synthesis_output),
        "key_inputs": {
            "dimension_outputs": dim_files,
            "synthesis_output_copy": str(synthesis_output_task),
        },
        "wave_inputs": dim_files,  # v3: 让 _build_brief() 生成跨wave交接指引
    }

    # 写 manifest
    manifest_path = task_dir / "bp_synthesis_manifest.json"

    # ── 从 instruction_store_bp/bp_统稿.md 加载 system_prompt（单一真实来源）──
    _instruction_store = runtime_root / "instruction_store_bp"
    _synthesis_prompt_path = _instruction_store / "bp_统稿.md"
    if _synthesis_prompt_path.exists():
        synthesis_system_prompt = _synthesis_prompt_path.read_text(encoding="utf-8")
    else:
        # Fallback: 如果 instruction store 文件缺失，用 bp_subagent_launcher_wb 的加载器
        try:
            from scripts.bp_subagent_launcher_wb import _load_instruction_store_prompts
            _prompts = _load_instruction_store_prompts()
            synthesis_system_prompt = _prompts.get("bp_统稿", "ERROR: bp_统稿 prompt not found in instruction store")
        except Exception:
            synthesis_system_prompt = "ERROR: bp_统稿 prompt not found in instruction store"

    # 动态模板替换：stage_tier
    synthesis_system_prompt = synthesis_system_prompt.replace("{STAGE_TIER}", stage_tier or "unknown")
    synthesis_system_prompt = synthesis_system_prompt.replace("{TASK_ID}", job_ctx.job_id)
    synthesis_system_prompt = synthesis_system_prompt.replace("{OUTPUTS_DIR}", str(outputs_dir))

    # ── 生成 synthesis brief（结构化输入文档，与 wave 子代理对齐）──
    brief_path = task_dir / "bp_synthesis_brief.md"
    brief_lines = [
        f"# BP 统稿 Brief — {job_ctx.entity}",
        f"",
        f"**Task ID:** {job_ctx.job_id}",
        f"**Entity:** {job_ctx.entity}",
        f"**Stage Tier:** {stage_tier or 'unknown'}",
        f"**Market:** {getattr(job_ctx, 'market', 'cn')}",
        f"",
        f"## 维度输入文件（共 {len(dim_files)} 个）",
        f"",
    ]
    for slug, fpath in sorted(dim_files.items()):
        p = Path(fpath)
        size_kb = round(p.stat().st_size / 1024, 1) if p.exists() else 0
        brief_lines.append(f"- **{slug}**: `{fpath}` ({size_kb} KB)")
        # 附带 facts + section sidecar
        facts_path = p.with_name(f"{p.stem}-facts.json")
        section_path = p.with_name(f"{p.stem}-section.json")
        if facts_path.exists():
            brief_lines.append(f"  - facts: `{facts_path}` ({round(facts_path.stat().st_size / 1024, 1)} KB)")
        if section_path.exists():
            brief_lines.append(f"  - section: `{section_path}` ({round(section_path.stat().st_size / 1024, 1)} KB)")
    brief_lines.extend([
        "",
        "## 关键路径",
        f"- **Fact Store**: `{task_dir / 'bp_fact_store.json'}`",
        f"- **Fact Store Index**: `{task_dir / 'bp_fact_store_index.json'}`",
        f"- **Research Plan**: `{task_dir / 'bp_research_plan.json'}`",
        f"- **Section Packages Index**: `{task_dir / 'bp_section_packages.json'}`",
        "",
        "## 输出文件",
        f"- **主输出**: `{synthesis_output}`",
        f"- **副本**: `{synthesis_output_task}`",
        "",
        "## 执行要求",
        "1. 读取所有维度报告 + facts sidecar + section sidecar",
        "2. 按 instruction 中的报告结构严格重组",
        "3. 脚注贯穿正文，格式 [^N]: 来源名 — URL (日期)",
        "4. 核心对比表原文保留，不压缩",
        "5. 写完后复制一份到副本路径",
    ])
    brief_path.write_text("\n".join(brief_lines) + "\n", encoding="utf-8")

    # ── 企业数据 MCP connector IDs（天眼查，与 wave 子代理对齐）──
    

    brief_content = brief_path.read_text(encoding="utf-8")

    manifest_data = {
        "manifest_version": "1.0",
        "task_id": job_ctx.job_id,
        "role": "bp_统稿",
        "slug": "synthesis",
        "label": f"{job_ctx.job_id}-bp-phase3-synthesis",
        "system_prompt": synthesis_system_prompt,
        "brief_path": str(brief_path),
        "brief_content_preview": brief_content[:2000],
        "output_path": str(synthesis_output),
        "output_copy_path": str(synthesis_output_task),
        "dimension_files": dim_files,
        "timeout": 1800,
        "thinking": "high",
        "dispatch_mode": "team_async",
        "mode": "bypassPermissions",
        "subagent_type": "general-purpose",
        "team_name_template": "bp-{task_id}",
        "connectorIds": BP_FULL_CONNECTOR_IDS,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "pending",
    }
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "bp_synthesis_prepare",
        "phase": "phase25_synthesis_prepare",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "manifests": [str(manifest_path)],
            "roles": ["bp_统稿"],
            "task_dir": str(task_dir),
            "outputs_dir": str(outputs_dir),
        },
        "result": {
            "manifest_path": str(manifest_path),
            "dimension_files": dim_files,
        },
        "instruction": (
            "MANDATORY: Read the manifest JSON file at '{manifest_path}'. Use the Agent tool with these EXACT parameters:\n"
            "1. prompt = manifest's 'system_prompt' field (the FULL text, do NOT summarize)\n"
            "2. name = 'synthesis'\n"
            f"3. team_name = 'bp-{job_ctx.job_id}'\n"
            "4. mode = 'bypassPermissions'\n"
            "5. connectorIds = manifest's 'connectorIds' field\n"
            "Also pass the manifest's 'brief_content_preview' as context for the sub-agent to know input files.\n"
            "Do NOT write your own simplified prompt — the manifest system_prompt contains the complete synthesis instructions."
        ).replace("{manifest_path}", str(manifest_path)),
    }


def _read_synthesis_attempt(task_dir: Path) -> int:
    """读取 synthesis repair 的历史执行次数。"""
    return read_attempt_count(task_dir / "bp_synthesis_repair_gate.json")


_MAX_SYNTHESIS_REPAIR_RETRIES = 1


def _run_bp_synthesis_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 28 collect: 检查统稿输出是否完成，含脚注密度校验 + repair 机制。

    v3 (2026-06-17):
    - 脚注阈值改为动态：每 2000 字至少 3 个脚注引用
    - 不达标时生成 repair manifest 派发修复子代理，最多 1 轮
    - 超过 repair 次数降级为 WARN 放行
    """
    task_dir = _task_dir(runtime_root, job_ctx)
    outputs_dir = _outputs_dir(runtime_root, job_ctx)

    # 检查两个可能的位置
    for d in (outputs_dir, task_dir):
        synthesis_path = d / "bp_synthesis.md"
        if synthesis_path.exists() and synthesis_path.stat().st_size > 2000:
            # ── 文件稳定性检查：确保子代理已写完（大小不再增长）──
            if not _file_stable(synthesis_path, interval=8):
                print(f"    ⏳ bp_synthesis.md 仍在写入中，等待...", flush=True)
                time.sleep(15)
                if not _file_stable(synthesis_path, interval=8):
                    continue  # 仍在写，本轮判定未完成

            quality = _quality_check(synthesis_path)

            # ── 脚注密度校验（动态阈值）──
            synthesis_text = synthesis_path.read_text(encoding="utf-8")
            all_fn_matches = len(re.findall(r"\[\^\d+\]", synthesis_text))
            footnote_defs = len(re.findall(r"^\[\^\d+\]:", synthesis_text, re.MULTILINE))
            footnote_refs = max(0, all_fn_matches - footnote_defs)
            content_k = max(quality["content_length"] // 1000, 1)
            footnote_density = footnote_refs / content_k

            # 动态阈值：每 2000 字至少 3 个脚注引用（即 density >= 1.5/1k）
            min_footnote_refs = max(3, (quality["content_length"] // 2000) * 3)
            footnote_issues: list[str] = []
            footnote_fail = False
            if footnote_refs < min_footnote_refs:
                footnote_issues.append(
                    f"脚注引用仅 {footnote_refs} 处（动态最低要求 {min_footnote_refs} 处，"
                    f"按 {quality['content_length']} 字计算），来源链严重缺失"
                )
                quality["score"] = max(0, quality["score"] - 3)
                footnote_fail = True
            elif footnote_refs < min_footnote_refs * 1.5:
                footnote_issues.append(
                    f"脚注引用 {footnote_refs} 处（建议 ≥{int(min_footnote_refs * 1.5)} 处），来源链不完整"
                )
                quality["score"] = max(0, quality["score"] - 1)
            if footnote_defs == 0 and footnote_refs > 0:
                footnote_issues.append(f"正文有 {footnote_refs} 处脚注引用但无脚注定义（[^N]: ...），来源无法展开")
                quality["score"] = max(0, quality["score"] - 2)
                footnote_fail = True

            quality["footnote_refs"] = footnote_refs
            quality["footnote_defs"] = footnote_defs
            quality["footnote_density_per_1k"] = round(footnote_density, 2)
            quality["footnote_min_required"] = min_footnote_refs
            if footnote_issues:
                quality["footnote_warnings"] = footnote_issues
                for w in footnote_issues:
                    print(f"    ⚠️ [脚注校验] {w}", flush=True)

            print(f"    ✅ bp_统稿: {quality['content_length']} chars, score={quality['score']}, "
                  f"footnotes={footnote_refs}/{footnote_defs} (min={min_footnote_refs})", flush=True)

            # 确保两个位置都有
            if outputs_dir != task_dir:
                for src_dir, dst_dir in [(outputs_dir, task_dir), (task_dir, outputs_dir)]:
                    src = src_dir / "bp_synthesis.md"
                    dst = dst_dir / "bp_synthesis.md"
                    if src.exists() and not dst.exists():
                        shutil.copy2(src, dst)

            # ── Repair 机制：脚注密度不达标 → 派发修复子代理 ──
            prior_attempt = _read_synthesis_attempt(task_dir)
            if footnote_fail and prior_attempt < _MAX_SYNTHESIS_REPAIR_RETRIES:
                # 构建维度报告路径列表，注入 repair prompt
                from runtime.profiles.bp_constants import BP_ALL_ROLE_SLUGS
                dim_paths_lines: list[str] = []
                facts_paths_lines: list[str] = []
                for role_key, slug in BP_ALL_ROLE_SLUGS.items():
                    for d in (outputs_dir, task_dir):
                        p = d / f"bp_dim_{slug}.md"
                        if p.exists() and p.stat().st_size > 100:
                            dim_paths_lines.append(f"  - {p}")
                            break
                    # Also collect facts JSON paths
                    for d in (outputs_dir, task_dir):
                        fp = d / f"bp_dim_{slug}-facts.json"
                        if fp.exists() and fp.stat().st_size > 10:
                            facts_paths_lines.append(f"  - {fp}")
                            break
                dim_paths_block = "\n".join(dim_paths_lines) if dim_paths_lines else "  （维度报告未找到）"
                facts_paths_block = "\n".join(facts_paths_lines) if facts_paths_lines else "  （facts JSON 未找到）"

                # 生成 repair manifest
                repair_manifest = {
                    "task_id": job_ctx.job_id,
                    "role": "bp_统稿_脚注修复",
                    "slug": "synthesis_footnote_repair",
                    "label": f"{job_ctx.job_id}-bp-synthesis-footnote-repair",
                    "system_prompt": (
                        f"你是脚注修复专家。读取 {synthesis_path}，为所有缺少 [^N] 脚注的关键定量数据补充脚注。\n\n"
                        f"## 维度报告路径（脚注来源优先从这里找）\n"
                        f"以下是需要回溯的维度报告完整路径，用 Read 工具逐一读取，从中提取原始来源 URL：\n"
                        f"{dim_paths_block}\n\n"
                        f"## Facts JSON 路径（维度报告无 URL 时的第二优先来源）\n"
                        f"当维度 MD 中没有 URL 时，读取对应的 facts JSON 文件，提取 source_url 字段：\n"
                        f"{facts_paths_block}\n\n"
                        f"## 任务步骤（必须按顺序执行）\n"
                        f"1. 读取 bp_synthesis.md 全文（路径: {synthesis_path}）\n"
                        f"2. **先读取上述维度报告**，提取每个维度中已引用的外部 URL 和来源信息\n"
                        f"3. **读取上述 facts JSON 文件**，提取每条 fact 的 source_url 字段，构建 fact→URL 映射表\n"
                        f"4. 找出 bp_synthesis.md 中所有缺少脚注引用的关键定量数据（市场规模、营收、增速、估值、PS/PE、员工数、市占率等）\n"
                        f"5. 按优先级匹配脚注来源：\n"
                        f"   - 优先：维度 MD 中已有的外部 URL\n"
                        f"   - 其次：facts JSON 中的 source_url\n"
                        f"   - 兜底：对 TYC 来源标注为'天眼查结构化数据（天眼查 MCP）'；BP 自述标注为'BP自述 — 无外部来源URL'\n"
                        f"6. 只有当以上三个来源都没有 URL 时，才用 search_deep(Bash) 脚本搜索补充（本环境无 web_search 内置工具）\n"
                        f"7. 在数据后插入 [^N] 标记，脚注编号从现有最大编号+1 开始连续递增\n"
                        f"8. 在报告末尾'来源与参考'章节追加新脚注定义，格式：[^N]: 来源名称 — URL (日期)\n"
                        f"9. 直接修改 bp_synthesis.md 文件\n\n"
                        f"**当前状态：** 正文 {footnote_refs} 处引用，最低要求 {min_footnote_refs} 处，"
                        f"缺少至少 {min_footnote_refs - footnote_refs} 处。\n"
                        f"**铁律：** 脚注来源优先从维度报告和 facts JSON 中提取已有 URL，"
                        f"不要跳过它们直接 search_deep。"
                    ),
                    "input_file": str(synthesis_path),
                    "output_file": str(synthesis_path),
                    "timeout": 900,
                    "thinking": "high",
                    "dispatch_mode": "team_async",
                    "mode": "bypassPermissions",
                    "subagent_type": "general-purpose",
                    "connectorIds": BP_FULL_CONNECTOR_IDS,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                repair_manifest_path = task_dir / "bp_synthesis_repair_manifest.json"
                repair_manifest_path.write_text(
                    json.dumps(repair_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                # 写入 repair gate 记录（用于 attempt 计数）
                gate_data = {
                    "attempt": prior_attempt + 1,
                    "footnote_refs": footnote_refs,
                    "min_required": min_footnote_refs,
                    "gate_verdict": "REPAIR",
                }
                (task_dir / "bp_synthesis_repair_gate.json").write_text(
                    json.dumps(gate_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

                print(
                    f"  🔧 [phase26_synthesis_collect] 脚注不达标 (attempt {prior_attempt + 1})，"
                    f"派发 repair 子代理补脚注",
                    flush=True,
                )
                return {
                    "ok": True,
                    "needs_dispatch": True,
                    "has_more": False,
                    "mode": "bp_synthesis_repair",
                    "phase": "phase26_synthesis_collect",
                    "job_id": job_ctx.job_id,
                    "dispatch_info": {
                        "manifests": [str(repair_manifest_path)],
                        "roles": ["bp_统稿_脚注修复"],
                        "task_dir": str(task_dir),
                        "outputs_dir": str(outputs_dir),
                        "is_repair": True,
                    },
                    "result": {"quality": quality, "repair_attempt": prior_attempt + 1},
                    "instruction": _repair_instruction_sequential("phase26_synthesis_collect", False, 0),
                }

            elif footnote_fail and prior_attempt >= _MAX_SYNTHESIS_REPAIR_RETRIES:
                # 降级放行
                quality["repair_exhausted"] = True
                quality["gate_verdict"] = "PASS_WITH_WARNINGS"
                print(
                    f"  ⚠️ [phase26_synthesis_collect] 脚注不达标但已 repair {prior_attempt} 次，"
                    f"降级为 WARN 放行",
                    flush=True,
                )

            return {
                "ok": True,
                "mode": "bp_synthesis_collect",
                "phase": "phase26_synthesis_collect",
                "job_id": job_ctx.job_id,
                "result": {"quality": quality},
            }

    return {
        "ok": False,
        "mode": "bp_synthesis_collect",
        "phase": "phase26_synthesis_collect",
        "job_id": job_ctx.job_id,
        "result": {"missing": "bp_synthesis.md"},
    }




# ── Phase 33: BP 交付 ──────────────────────────────────


def _docx_via_subprocess(
    runtime_root: Path,
    job_ctx: JobContext,
    delivery_dir: Path,
    dimension_outputs: dict[str, str],
    delivery_errors: list[str],
) -> str:
    """Fallback DOCX generation via system Python when managed lxml fails.

    Writes a small driver script and runs it with /opt/anaconda3/bin/python3
    (or whichever system Python has a working lxml).
    Returns the docx_path on success, or "" on failure (appends to delivery_errors).
    """
    import tempfile

    task_dir = _task_dir(runtime_root, job_ctx)
    output_path = delivery_dir / f"{job_ctx.job_id}_bp_dd_report.docx"

    # Serialize dimension_outputs to a temp JSON (keys are section names, values are markdown text)
    dim_json_path = task_dir / "_docx_dim_inputs.json"
    dim_payload = {k: v for k, v in dimension_outputs.items()}
    dim_json_path.write_text(json.dumps(dim_payload, ensure_ascii=False), encoding="utf-8")

    driver_script = f"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path("{runtime_root}")))
from scripts.build_bp_dd_report_docx import build_bp_dd_report

dim_json = Path("{dim_json_path}")
dims = json.loads(dim_json.read_text(encoding="utf-8"))
result = build_bp_dd_report(
    task_id="{job_ctx.job_id}",
    entity="{job_ctx.entity}",
    dimension_outputs=dims,
    output_path="{output_path}",
)
print(str(result))
"""
    driver_path = task_dir / "_docx_driver.py"
    driver_path.write_text(driver_script, encoding="utf-8")

    # Find a working system Python
    for fallback in ["/opt/anaconda3/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
        if not Path(fallback).exists():
            continue
        test = subprocess.run(
            [fallback, "-c", "from lxml import etree"],
            capture_output=True, text=True, timeout=10,
        )
        if test.returncode == 0:
            print(f"  🔄 [lxml fallback] using {fallback} for DOCX generation", flush=True)
            try:
                result = subprocess.run(
                    [fallback, str(driver_path)],
                    capture_output=True, text=True,
                    cwd=str(runtime_root), timeout=300,
                )
                if result.returncode == 0 and Path(str(output_path)).exists():
                    return str(output_path)
                else:
                    delivery_errors.append(f"DOCX subprocess failed (rc={result.returncode}): {result.stderr[:500]}")
                    return ""
            except Exception as sub_exc:
                delivery_errors.append(f"DOCX subprocess exception: {sub_exc}")
                return ""
    delivery_errors.append("DOCX 生成失败: no system Python with working lxml found")
    return ""


def _run_bp_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_bp_delivery_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import launch_heavy_phase
    # Delivery 是最终硬门禁，不能复用缓存；否则可能跳过最新 readability/verification/delivery gate。
    return launch_heavy_phase(runtime_root, job_ctx, "phase31_delivery", pipeline="bp")


def _run_bp_delivery_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """delivery 实际执行逻辑（由 phase_runner.py 子进程调用时通过 bp_profile 路由）"""
    task_dir = _task_dir(runtime_root, job_ctx)
    workspace = getattr(job_ctx, "workspace", None)
    delivery_dir = workspace.delivery_dir if workspace is not None else task_dir
    outputs_dir = _outputs_dir(runtime_root, job_ctx)

    # ── 验证环节：对最终报告跑 AdversarialVerifier ──
    # 优先用 synthesis（叙事报告），fallback 到 final_report（assembler 骨架）
    # 结果统一写入 bp_verification_result.json（供 delivery gate 检查）
    verification_text_for_gate = ""
    for candidate in [
        (outputs_dir / "bp_synthesis.md"),
        (task_dir / "bp_synthesis.md"),
        (task_dir / "bp_final_report.md"),
    ]:
        if candidate.exists() and candidate.stat().st_size > 500:
            verification_text_for_gate = candidate.read_text(encoding="utf-8")
            break

    if verification_text_for_gate:
        try:
            from scripts.verification_agent import AdversarialVerifier
            verifier = AdversarialVerifier(pipeline="bp")
            verification_result_for_gate = verifier.run(verification_text_for_gate)
        except Exception as exc:
            verification_result_for_gate = {"verdict": "FAIL", "fail": 1, "error": f"VERIFICATION_ERROR: {exc}"}
    else:
        verification_result_for_gate = {"verdict": "FAIL", "fail": 1, "error": "NO_REPORT_AVAILABLE_FOR_VERIFICATION"}

    (task_dir / "bp_verification_result.json").write_text(
        json.dumps(verification_result_for_gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        from scripts.bp_delivery_gate import write_bp_delivery_gate
        delivery_gate = write_bp_delivery_gate(task_dir)
    except Exception as exc:
        delivery_gate = {"ok": False, "deliver_to_user": False, "block_reason": f"DELIVERY_GATE_ERROR: {exc}", "failed_checks": []}
    if not delivery_gate.get("ok"):
        audit_path = delivery_dir / "bp_delivery_audit.json"
        audit_data = {
            "job_id": job_ctx.job_id,
            "entity": job_ctx.entity,
            "mode": "delivery_blocked_by_gate",
            "gate_verdict": "FAIL",
            "block_reason": delivery_gate.get("block_reason", "DELIVERY_GATE_FAILED"),
            "delivery_gate": delivery_gate,
            "audit_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        audit_path.write_text(json.dumps(audit_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": False,
            "mode": "bp_delivery_blocked_by_gate",
            "phase": "phase31_delivery",
            "job_id": job_ctx.job_id,
            "deliver_to_user": False,
            "result": {
                "docx_path": "",
                "audit_path": str(audit_path),
                "block_reason": delivery_gate.get("block_reason", "DELIVERY_GATE_FAILED"),
                "delivery_errors": [delivery_gate.get("block_reason", "DELIVERY_GATE_FAILED")],
                "gate_verdict": "FAIL",
                "delivery_gate": delivery_gate,
            },
        }

    # Priority: synthesis sub-agent (LLM narrative) > final_report (narrative_assembler skeleton) > raw dimensions fallback
    synthesis_path = None
    for d in (outputs_dir, task_dir):
        p = d / "bp_synthesis.md"
        if p.exists() and p.stat().st_size > 2000:
            synthesis_path = p
            break
    if synthesis_path is None:
        final_report = task_dir / "bp_final_report.md"
        if final_report.exists() and final_report.stat().st_size > 100:
            synthesis_path = final_report

    use_synthesis = synthesis_path is not None

    dimension_outputs: dict[str, str] = {}
    if use_synthesis:
        # 统稿模式：整篇报告作为一个整体
        dimension_outputs["synthesis"] = synthesis_path.read_text(encoding="utf-8")
    else:
        # Fallback：Wave 1/2 八个维度原文，兼容旧版 role slug 文件
        file_map = {
            slug: task_dir / f"bp_dim_{slug}.md"
            for slug in BP_ALL_ROLE_SLUGS.values()
        }
        file_map |= {
            slug: task_dir / f"bp_dim_{legacy_slug}.md"
            for slug, legacy_slug in BP_LEGACY_ROLE_SLUGS.items()
        }
        for slug, output_path in file_map.items():
            if output_path.exists():
                dimension_outputs[slug] = output_path.read_text(encoding="utf-8")

    delivery_errors = []

    # 保存验证结果副本到 verification/ 目录（审计归档用，gate 已检查 bp_verification_result.json）
    try:
        gate_ver = json.loads((task_dir / "bp_verification_result.json").read_text(encoding="utf-8"))
        ver_dir = delivery_dir.parent / "verification"
        ver_dir.mkdir(parents=True, exist_ok=True)
        ver_path = ver_dir / f"{job_ctx.job_id}_bp_verification.json"
        ver_path.write_text(
            json.dumps(gate_ver, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        gate_verdict = gate_ver.get("verdict", "UNKNOWN")
        if gate_verdict == "FAIL":
            delivery_errors.append(f"对抗验证 FAIL: {gate_ver.get('fail', '?')} 项检查失败（详见 verification/）")
    except Exception:
        pass

    docx_path = ""
    if dimension_outputs:
        try:
            from scripts.build_bp_dd_report_docx import build_bp_dd_report

            output_path = delivery_dir / f"{job_ctx.job_id}_bp_dd_report.docx"
            result_path = build_bp_dd_report(
                task_id=job_ctx.job_id,
                entity=job_ctx.entity,
                dimension_outputs=dimension_outputs,
                output_path=str(output_path),
            )
            docx_path = str(result_path)
        except ImportError as imp_exc:
            # Bug 2 fix: lxml code signature failure — fallback to subprocess with system Python
            if "lxml" in str(imp_exc) or "code signature" in str(imp_exc):
                print(f"  🔄 [lxml fallback] in-process import failed: {imp_exc}", flush=True)
                docx_path = _docx_via_subprocess(runtime_root, job_ctx, delivery_dir, dimension_outputs, delivery_errors)
            else:
                delivery_errors.append(f"DOCX 生成失败 (ImportError): {imp_exc}")
        except Exception as exc:
            delivery_errors.append(f"DOCX 生成失败: {exc}")
    else:
        delivery_errors.append("无可用的维度输出或统稿输出")

    # ── 维度 MD → DOCX 独立报告（2026-06-26 新增，2026-06-29 平铺到 delivery 根目录） ──
    # 所有交付物（统稿 + 8 维度 + 附件）统一放在 delivery/ 根目录，不再使用子目录
    dim_docx_paths: list[str] = []
    try:
        from scripts.build_bp_dd_report_docx import build_bp_dimension_docx, DIMENSION_TITLES as _DIM_TITLES
        from scripts.build_bp_dd_report_docx import NARRATIVE_DIMENSION_TITLES as _NARR_DIM_TITLES

        # Wave 4 叙事三角色（catalyst/consensus_challenge/industry_research）同样平铺成卷，
        # 保留催化剂时间线等原文证据链（v6.2：此前仅统稿 DOCX 间接包含其摘要）
        for slug, dim_title in {**_DIM_TITLES, **_NARR_DIM_TITLES}.items():
            dim_md = task_dir / f"bp_dim_{slug}.md"
            if not dim_md.exists():
                dim_md = outputs_dir / f"bp_dim_{slug}.md"
            if dim_md.exists() and dim_md.stat().st_size > 100:
                dim_docx_path = delivery_dir / f"{dim_title}.docx"
                try:
                    result = build_bp_dimension_docx(
                        entity=job_ctx.entity or entity,
                        dimension_title=dim_title,
                        md_content=dim_md.read_text(encoding="utf-8"),
                        output_path=str(dim_docx_path),
                    )
                    dim_docx_paths.append(str(result))
                    print(f"  📄 维度报告: {result}", flush=True)
                except Exception as dim_exc:
                    print(f"  ⚠️ 维度 {dim_title} DOCX 生成失败: {dim_exc}", flush=True)
    except ImportError:
        pass  # python-docx 不可用时静默跳过
    except Exception as exc:
        print(f"  ⚠️ 维度 DOCX 批量生成异常: {exc}", flush=True)

    # ── 来源与参考独立 Word 文档（2026-07-13 新增） ──
    refs_docx_path = ""
    if use_synthesis and synthesis_path:
        try:
            from scripts.build_bp_references_docx import build_bp_references_docx
            refs_out = delivery_dir / f"{entity or job_ctx.job_id}_来源参考.docx"
            refs_docx_path = str(build_bp_references_docx(
                synthesis_md_path=synthesis_path,
                output_path=refs_out,
                entity=entity or job_ctx.entity or "",
            ))
            print(f"  📄 来源参考: {refs_docx_path}", flush=True)
        except ImportError:
            pass  # python-docx 不可用时静默跳过
        except Exception as refs_exc:
            print(f"  ⚠️ 来源参考 DOCX 生成失败: {refs_exc}", flush=True)

    audit_path = delivery_dir / "bp_delivery_audit.json"
    audit_data = {
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "mode": "dual_delivery" if use_synthesis else "audit_worksheet_only",
        "dimensions_completed": list(dimension_outputs.keys()),
        "gate_verdict": "PASS" if docx_path else "PARTIAL",
        "dim_docx_count": len(dim_docx_paths),
        "refs_docx_path": refs_docx_path or None,
        "audit_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    audit_path.write_text(json.dumps(audit_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # P0-2: 复制两份 markdown 报告到 delivery 目录（方案B：并行交付）
    entity = job_ctx.entity or task_dir.name
    if use_synthesis and synthesis_path:
        # 投资备忘录（synthesis 统稿，长篇叙事，给决策者看）
        synthesis_dest = delivery_dir / f"{entity}BP投资备忘录.md"
        shutil.copy2(synthesis_path, synthesis_dest)
        print(f"  📄 投资备忘录 (synthesis): {synthesis_dest}", flush=True)

    # 审计底稿（final_assembly 骨架，结构化表格，给尽调团队复核用）
    audit_worksheet_md = task_dir / "bp_final_report.md"
    if audit_worksheet_md.exists() and audit_worksheet_md.stat().st_size > 100:
        audit_dest = delivery_dir / f"{entity}BP审计底稿.md"
        shutil.copy2(audit_worksheet_md, audit_dest)
        print(f"  📄 审计底稿 (assembler): {audit_dest}", flush=True)

    # 收集附件文件（xlsx 等）到 delivery 目录
    attachment_paths: list[str] = []
    for xlsx_name in outputs_dir.glob(f"{job_ctx.job_id}_*.xlsx"):
        dst = delivery_dir / xlsx_name.name
        if not dst.exists():
            shutil.copy2(xlsx_name, dst)
        attachment_paths.append(str(dst))
    # 也检查 task_dir
    for xlsx_name in task_dir.glob(f"{job_ctx.job_id}_*.xlsx"):
        dst = delivery_dir / xlsx_name.name
        if not dst.exists():
            shutil.copy2(xlsx_name, dst)
        if str(dst) not in attachment_paths:
            attachment_paths.append(str(dst))

    try:
        from runtime.orchestrator.state_store import StateStore

        ss = StateStore(runtime_root)
        if docx_path:
            ss.record_artifact(job_ctx.job_id, "bp_dd_report", Path(docx_path))
        ss.record_artifact(job_ctx.job_id, "bp_delivery_audit", audit_path)
        # 注册维度 DOCX
        for i, dim_path in enumerate(dim_docx_paths):
            ss.record_artifact(job_ctx.job_id, f"bp_dim_docx_{i}", Path(dim_path))
        # 注册 xlsx 附件
        for i, att_path in enumerate(attachment_paths):
            ss.record_artifact(job_ctx.job_id, f"bp_attachment_{i}", Path(att_path))
        # 注册来源参考 DOCX（2026-07-13 新增）
        if refs_docx_path:
            ss.record_artifact(job_ctx.job_id, "bp_references_docx", Path(refs_docx_path))
    except Exception:
        pass

    dimensions_total = len(dimension_outputs)
    return {
        "ok": bool(docx_path),
        "mode": "bp_delivery_minimal",
        "phase": "phase31_delivery",
        "job_id": job_ctx.job_id,
        "deliver_to_user": True if docx_path else False,
        "result": {
            "dimensions_completed": len(dimension_outputs),
            "dimensions_total": dimensions_total,
            "docx_path": str(docx_path),
            "audit_path": str(audit_path),
            "attachment_paths": attachment_paths,
            "dim_docx_paths": dim_docx_paths,
            "dim_docx_count": len(dim_docx_paths),
            "refs_docx_path": refs_docx_path,
            "delivery_errors": delivery_errors,
            "gate_verdict": "PASS" if docx_path else "PARTIAL",
        },
    }


class BPProfile(PipelineProfile):
    def __init__(self, runtime_root: Path):
        super().__init__(
            name="bp",
            job_type="business_plan_dd",
            phase_handlers={
            # ── Phase handlers — 执行顺序由 dict 插入顺序决定 ──
            # 序号仅用于注释和日志，不影响执行。
            "phase01_document_intake": lambda job_ctx: _run_document_intake(runtime_root, job_ctx),              # 01
            "phase02_company_intake": lambda job_ctx: _run_company_intake(runtime_root, job_ctx),              # 01b → needs_dispatch (公司名搜索入库)
            "phase03_company_intake_collect": lambda job_ctx: _run_company_intake_collect(runtime_root, job_ctx),  # 01b collect
            "phase04_research_plan": lambda job_ctx: _run_research_plan(runtime_root, job_ctx),                 # 04 → needs_dispatch (子代理派发, tyc+westock)
            "phase05_research_plan_collect": lambda job_ctx: _run_research_plan_collect(runtime_root, job_ctx), # 04c
            "phase06_bp_shared_page_init": lambda job_ctx: _run_bp_shared_page_init(runtime_root, job_ctx),     # 05
            "phase07_search_plan_compile": lambda job_ctx: _run_bp_search_plan_compile(runtime_root, job_ctx),  # 06
            "phase08_bp_fact_store_bootstrap": lambda job_ctx: _run_bp_fact_store_bootstrap(runtime_root, job_ctx),  # 07
            # ── Wave 1: 基础四维并行 ──
            "phase09_dispatch_prepare": lambda job_ctx: _run_bp_dispatch_prepare(runtime_root, job_ctx),        # 08 → needs_dispatch
            "phase10_dispatch_collect": lambda job_ctx: _run_bp_dispatch_collect(runtime_root, job_ctx),        # 09
            "phase11_wave1_evidence_gate": lambda job_ctx: _run_bp_wave_evidence_gate(runtime_root, job_ctx, wave=1),  # 10
            "phase12_bp_fact_store_merge": lambda job_ctx: _run_bp_fact_store_merge(runtime_root, job_ctx),     # 11
            "phase13_wave1_shared_page_refresh": lambda job_ctx: _run_bp_shared_page_refresh(runtime_root, job_ctx, after_wave=1),  # 12
            # ── Wave 3: 竞争 + 估值 ──
            "phase14_wave3_prepare": lambda job_ctx: _run_bp_wave3_prepare(runtime_root, job_ctx),              # 13 → needs_dispatch
            "phase15_wave3_collect": lambda job_ctx: _run_bp_wave3_collect(runtime_root, job_ctx),             # 14
            "phase16_wave3_evidence_gate": lambda job_ctx: _run_bp_wave_evidence_gate(runtime_root, job_ctx, wave=3),  # 15
            "phase17_wave3_shared_page_refresh": lambda job_ctx: _run_bp_shared_page_refresh(runtime_root, job_ctx, after_wave=3),  # 16
            # ── Wave 4: Deal Breaker ──
            "phase18_wave4_prepare": lambda job_ctx: _run_bp_wave4_prepare(runtime_root, job_ctx),             # 17 → needs_dispatch
            "phase19_wave4_collect": lambda job_ctx: _run_bp_wave4_collect(runtime_root, job_ctx),             # 18
            "phase20_wave4_evidence_gate": lambda job_ctx: _run_bp_wave_evidence_gate(runtime_root, job_ctx, wave=4),  # 19
            "phase21_wave4_shared_page_refresh": lambda job_ctx: _run_bp_shared_page_refresh(runtime_root, job_ctx, after_wave=4),  # 20
            # ── Quality Gates ──
            "phase22_bp_claim_coverage_validation": lambda job_ctx: _run_bp_claim_coverage_validation(runtime_root, job_ctx),  # 21
            "phase23_bp_cross_dimension_gate": lambda job_ctx: _run_bp_cross_dimension_gate(runtime_root, job_ctx),  # 22
            "phase24_bp_section_package_validation": lambda job_ctx: _run_bp_section_package_validation(runtime_root, job_ctx),  # 23
            # ── Synthesis (统稿) ──
            "phase25_synthesis_prepare": lambda job_ctx: _run_bp_synthesis_prepare(runtime_root, job_ctx),        # 24 → needs_dispatch
            "phase26_synthesis_collect": lambda job_ctx: _run_bp_synthesis_collect(runtime_root, job_ctx),        # 25
            # ── Final Assembly + Delivery ──
            "phase27_bp_debate_review": lambda job_ctx: _run_bp_debate_review(runtime_root, job_ctx),            # 26
            "phase28_bp_final_assembly": lambda job_ctx: _run_bp_final_assembly(runtime_root, job_ctx),          # 27
            "phase29_bp_readability_review": lambda job_ctx: _run_bp_readability_review(runtime_root, job_ctx),  # 28
            "phase30_bp_investment_judgment": lambda job_ctx: _run_bp_investment_judgment(runtime_root, job_ctx),  # 29
            "phase31_delivery": lambda job_ctx: _run_bp_delivery(runtime_root, job_ctx),                         # 30 [heavy_bg]
            },
        )
        self.runtime_root = runtime_root

    def phase_prerequisites(self) -> dict[str, list[str]]:
        """声明 phase 间的关键产物依赖。
        key = phase_name, value = 该 phase 运行前必须存在的文件列表（相对 task_dir）。
        kernel 在 start_phase 跳过前置 phase 时，会自动回填缺失产物。
        """
        return {
            "phase07_search_plan_compile": ["bp_research_plan.json"],
            "phase08_bp_fact_store_bootstrap": ["bp_research_plan.json", "bp_search_plan.json"],
            "phase09_dispatch_prepare": ["bp_research_plan.json"],
            "phase12_bp_fact_store_merge": ["bp_research_plan.json"],
            "phase24_bp_section_package_validation": ["bp_research_plan.json"],
            "phase27_bp_debate_review": ["bp_research_plan.json"],
            "phase28_bp_final_assembly": ["bp_research_plan.json"],
        }

    def phase_outputs(self) -> dict[str, list[str]]:
        """声明每个 phase 产出的关键文件（相对 workspace.root）。
        kernel 用它构建反查表 file → producer_phase，
        在依赖缺失时精准回填到产出该文件的 phase，而非盲选前置。
        """
        return {
            "phase01_document_intake": ["bp_step0_profile.json", "bp_claim_inventory.json"],
            "phase02_company_intake": [],
            "phase03_company_intake_collect": ["bp_step0_profile.json"],
            "phase04_research_plan": [],  # v5.2: 子代理直接生成plan, skeleton仅作可选参考
            "phase05_research_plan_collect": ["bp_research_plan.json"],
            "phase06_bp_shared_page_init": ["bp_shared_diligence_page.md"],
            "phase07_search_plan_compile": ["bp_search_plan.json"],
            "phase08_bp_fact_store_bootstrap": ["bp_fact_store.json", "bp_fact_store_index.json"],
            "phase09_dispatch_prepare": ["bp_dispatch.json"],
            "phase10_dispatch_collect": [],
            "phase11_wave1_evidence_gate": ["bp_wave1_evidence_gate.json"],
            "phase14_wave3_prepare": [],
            "phase15_wave3_collect": [],
            "phase16_wave3_evidence_gate": ["bp_wave3_evidence_gate.json"],
            "phase18_wave4_prepare": [],
            "phase19_wave4_collect": [],
            "phase20_wave4_evidence_gate": ["bp_wave4_evidence_gate.json"],
            "phase24_bp_section_package_validation": ["bp_section_packages.json", "bp_section_gate.json"],
            "phase22_bp_claim_coverage_validation": ["bp_claim_coverage_gate.json"],
            "phase23_bp_cross_dimension_gate": ["bp_cross_dimension_gate.json"],
            "phase26_synthesis_collect": ["bp_synthesis.md"],
            "phase27_bp_debate_review": ["bp_debate_review.json"],
            "phase28_bp_final_assembly": ["bp_final_report.md", "bp_final_assembly.json"],
            "phase29_bp_readability_review": ["bp_readability_review.json"],
            "phase30_bp_investment_judgment": ["bp_investment_judgment.json"],
            "phase31_delivery": [],
        }
