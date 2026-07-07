from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from runtime.profiles.base import JobContext, PipelineProfile


def _not_implemented_phase(name: str):
    def _runner(job_ctx: JobContext) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "skeleton",
            "phase": name,
            "job_id": job_ctx.job_id,
        }
    return _runner


def _workspace_for(job_ctx: JobContext):
    """Get JobWorkspace from context (injected by kernel)."""
    return job_ctx.workspace


def _sync_step_to_workspace(job_ctx: JobContext, step_name: str, output_path: Path):
    """Copy a completed step output file into the workspace outputs dir.

    Keeps the legacy path intact while also populating the workspace.
    """
    ws = _workspace_for(job_ctx)
    if ws is None or not output_path.exists():
        return
    dest = ws.outputs_dir / f"{step_name}.md"
    try:
        shutil.copy2(output_path, dest)
    except Exception:
        pass


def _sync_artifact_to_workspace(job_ctx: JobContext, artifact_type: str, src_path: Path):
    """Copy a delivery artifact into the workspace delivery dir and record it."""
    ws = _workspace_for(job_ctx)
    if ws is None or not src_path.exists():
        return
    dest = ws.delivery_dir / src_path.name
    try:
        shutil.copy2(src_path, dest)
        # Record artifact
        manifest_path = ws.state_dir / "artifacts.json"
        artifacts = {}
        if manifest_path.exists():
            try:
                artifacts = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        artifacts[artifact_type] = {
            "path": str(dest),
            "original_path": str(src_path),
            "recorded_at": time.time(),
        }
        manifest_path.write_text(
            json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# Phase 0-3: Research chain (unchanged, now with workspace sync)
# ═══════════════════════════════════════════════════════════

def _run_preflight(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_preflight_check import run_preflight

    metadata = job_ctx.metadata or {}
    result = run_preflight(
        job_ctx.job_id,
        entity=job_ctx.entity,
        query=job_ctx.query,
        market=job_ctx.market,
    )
    return {
        "ok": bool(result.get("passed", False)),
        "mode": "legacy_wrapped",
        "phase": "phase01_preflight",
        "job_id": job_ctx.job_id,
        "result": result,
        "metadata_used": {
            "entity": job_ctx.entity,
            "query": job_ctx.query,
            "market": job_ctx.market,
            "ticker": metadata.get("ticker", ""),
            "english_name": metadata.get("english_name", ""),
        },
    }


def _run_company_verify(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        from scripts.ir_company_verify import run as run_company_verify
        result = run_company_verify(
            task_id=job_ctx.job_id,
            entity=job_ctx.entity,
            market=job_ctx.market,
        )
        return {
            "ok": "error" not in result,
            "mode": "legacy_wrapped",
            "phase": "phase02_company_verify",
            "job_id": job_ctx.job_id,
            "result": result,
        }
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase02_company_verify")
    if cached is not None:
        print(f"  📦 [ir] 使用缓存的 company_verify 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase02_company_verify", pipeline="ir")


def _run_research_plan(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase03: IR 研究计划 — needs_dispatch 模式（对标 BP v5.0）。

    首次执行：脚本生成骨架计划 → 返回 needs_dispatch + instruction
    主 AI 读 instruction → 输出 ir_research_plan_enrichment.json
    恢复时由 _run_research_plan_collect 合并 enrichment
    """
    from scripts.ir_research_planner import build_ir_research_plan_skeleton

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    skeleton = build_ir_research_plan_skeleton(
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        query=job_ctx.query,
        market=job_ctx.market,
    )

    # 写入骨架
    skeleton_path = tasks_dir / f"{job_ctx.job_id}-ir_research_plan_skeleton.json"
    skeleton_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 写入 enrichment instruction
    instruction_path = tasks_dir / f"{job_ctx.job_id}-ir_phase03_enrichment_instruction.md"
    instruction_path.write_text(_ir_research_plan_enrichment_instruction(
        entity=job_ctx.entity, query=job_ctx.query, tasks_dir=tasks_dir,
    ), encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "ir_research_plan",
        "phase": "phase03_research_plan",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "instruction_path": str(instruction_path),
            "skeleton_path": str(skeleton_path),
            "task_dir": str(tasks_dir),
        },
        "instruction": _ir_research_plan_enrichment_instruction(
            entity=job_ctx.entity, query=job_ctx.query, tasks_dir=tasks_dir,
        ),
    }


def _ir_research_plan_enrichment_instruction(entity: str, query: str, tasks_dir: Path) -> str:
    """生成 phase03 enrichment 的主 AI 指令。"""
    return f"""\
PHASE03 IR RESEARCH PLAN ENRICHMENT — 主 AI 执行

## 背景
脚本已生成确定性骨架计划（{entity}，{query}）。
你需要阅读骨架计划和标的信息，输出定制化的 enrichment delta。

## 输入文件
1. 骨架计划: `{tasks_dir / f"{entity}-ir_research_plan_skeleton.json"}` (用实际 task_id 替换)
2. Enrichment 指令库: `instruction_store_ir/ir_research_plan_enrichment.md`

## 执行步骤
1. 读取 instruction_store_ir/ir_research_plan_enrichment.md 了解完整要求
2. 读取骨架计划，理解 core_questions / fact_requirements / section_requirements 结构
3. 围绕标的 `{entity}` 和研究重点 `{query}` 生成 5 条尖锐的 strategic_questions
4. 按 instruction 要求生成 delta JSON
5. 将输出写入 `{tasks_dir / f"{entity}-ir_research_plan_enrichment.json"}`
6. 用 start_phase='phase03c_research_plan_collect' 恢复管线

## 输出格式
严格遵循 instruction_store_ir/ir_research_plan_enrichment.md 中定义的 JSON schema。
所有 fact_key 引用必须是骨架计划 fact_requirements 中已有的。
所有 owner_section 必须是 10 个 IR step 之一。
"""


def _run_research_plan_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase03 collect: 读取主 AI 的 enrichment，合并到骨架计划。"""
    from scripts.ir_research_planner import (
        apply_ir_enrichment,
        build_ir_research_plan_skeleton,
        build_strategic_questions,
        research_plan_path,
        validate_research_plan_ready,
    )

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 读取骨架
    skeleton_path = tasks_dir / f"{job_ctx.job_id}-ir_research_plan_skeleton.json"
    if not skeleton_path.exists():
        # 降级：直接用 prepare_research_plan 生成（无 enrichment 的 fallback）
        from scripts.ir_research_planner import prepare_research_plan
        path = prepare_research_plan(
            task_id=job_ctx.job_id,
            entity=job_ctx.entity,
            query=job_ctx.query,
            market=job_ctx.market,
            tasks_dir=tasks_dir,
        )
        return {
            "ok": True,
            "mode": "ir_research_plan",
            "phase": "phase03c_research_plan_collect",
            "job_id": job_ctx.job_id,
            "result": {"plan_path": path, "enrichment": "skipped_fallback"},
        }

    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))

    # 读取 enrichment
    enrichment_path = tasks_dir / f"{job_ctx.job_id}-ir_research_plan_enrichment.json"
    enrichment: dict[str, Any] = {}
    if enrichment_path.exists():
        try:
            enrichment = json.loads(enrichment_path.read_text(encoding="utf-8"))
        except Exception:
            print(f"  ⚠️ [phase03_collect] enrichment JSON 解析失败，使用模板版 strategic_questions", flush=True)
    else:
        print(f"  ⚠️ [phase03_collect] enrichment 文件不存在，使用模板版 strategic_questions", flush=True)

    # 合并
    plan = apply_ir_enrichment(skeleton, enrichment)
    path = research_plan_path(job_ctx.job_id, tasks_dir)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(path, ws.outputs_dir / "research_plan.json")
        except Exception:
            pass

    return {
        "ok": plan.get("plan_status") == "ready",
        "mode": "ir_research_plan",
        "phase": "phase03c_research_plan_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "plan_path": str(path),
            "plan_status": plan.get("plan_status", "blocked"),
            "enrichment_status": plan.get("enrichment_status", "none"),
            "validation": plan.get("validation", {}),
        },
    }


def _run_fact_store_bootstrap(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Initialize a generic Fact Store and seed candidates from existing extracted facts when available."""
    from scripts.ir_fact_store import FactStore, add_fact, extract_fact_candidates, write_fact_store

    tasks_dir = runtime_root / "data" / "tasks"
    store = FactStore(task_id=job_ctx.job_id, entity=job_ctx.entity, market=job_ctx.market)
    seeded = 0

    candidate_texts: list[str] = []
    extracted_facts_path = tasks_dir / f"{job_ctx.job_id}_body_content" / "ir_extracted_facts.json"
    if extracted_facts_path.exists():
        try:
            payload = json.loads(extracted_facts_path.read_text(encoding="utf-8"))
            candidate_texts.append(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    company_verify_path = tasks_dir / f"{job_ctx.job_id}-ir_company_verify.json"
    if company_verify_path.exists():
        try:
            payload = json.loads(company_verify_path.read_text(encoding="utf-8"))
            candidate_texts.append(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    for text in candidate_texts:
        for item in extract_fact_candidates(text, entity=job_ctx.entity):
            add_fact(
                store,
                claim=item["claim"],
                value=item["value"],
                unit=item["unit"],
                period=item["period"],
                source_url=item["source_url"],
                source_tier="unknown",
                source_quote=item["source_quote"],
                question_id=item.get("question_id", ""),
                fact_type=item.get("fact_type", "numeric"),
                confidence=item.get("confidence", "low"),
            )
            seeded += 1

    output_path = write_fact_store(store, tasks_dir=tasks_dir)
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "fact_store.json")
        except Exception:
            pass
    return {
        "ok": True,
        "mode": "quality_production",
        "phase": "phase06_fact_store_bootstrap",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "facts_seeded": seeded},
    }


def _run_presearch(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_presearch_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase04_presearch")
    if cached is not None:
        print(f"  📦 [ir] 使用缓存的 presearch 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase04_presearch", pipeline="ir")


def _run_presearch_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """presearch 实际执行逻辑（子进程内直接调用）"""
    from scripts.ir_presearch import run_presearch

    metadata = job_ctx.metadata or {}
    ticker = metadata.get("ticker", "")
    english_name = metadata.get("english_name", "")

    # 自动解析 ticker 和英文名（如果 submit 时没传）
    if not ticker:
        try:
            from tasks.valuation_enricher import _resolve_ticker, _CN_TO_EN_SEARCH
            resolved = _resolve_ticker(job_ctx.entity)
            if resolved:
                ticker = resolved
                print(f"  🔍 自动解析 ticker: {job_ctx.entity} → {ticker}", flush=True)
            if not english_name:
                english_name = _CN_TO_EN_SEARCH.get(job_ctx.entity, "")
                if english_name:
                    print(f"  🔍 自动解析英文名: {job_ctx.entity} → {english_name}", flush=True)
        except Exception:
            pass

    result = run_presearch(
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        market=job_ctx.market,
        ticker=ticker,
        english_name=english_name,
    )
    return {
        "ok": True,
        "mode": "legacy_wrapped",
        "phase": "phase04_presearch",
        "job_id": job_ctx.job_id,
        "result": result,
        "query_context": {
            "ticker": ticker,
            "english_name": english_name,
        },
    }


def _run_extract(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_extract_content import extract_from_presearch

    metadata = job_ctx.metadata or {}
    max_pages = metadata.get("max_extract_pages", 15)
    result = extract_from_presearch(
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        max_pages=max_pages,
    )
    ok_count = result.get("ok_count", 0)
    total = result.get("total_urls", 0)

    # Sync extraction results to workspace
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            extract_facts = runtime_root / "data" / "tasks" / f"{job_ctx.job_id}_body_content" / "ir_extracted_facts.json"
            if extract_facts.exists():
                shutil.copy2(extract_facts, ws.extraction_dir / "ir_extracted_facts.json")
        except Exception:
            pass

    return {
        "ok": ok_count > 0,
        "mode": "legacy_wrapped",
        "phase": "phase05_extract",
        "job_id": job_ctx.job_id,
        "result": {
            "total_urls": total,
            "ok_count": ok_count,
            "agg_entities": result.get("agg_entities", []),
            "agg_financials": result.get("agg_financials", []),
            "agg_events": result.get("agg_events", []),
            "agg_risks": result.get("agg_risks", []),
            "agg_valuation_views": result.get("agg_valuation_views", []),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 1.2: Precompute — 三大预计算引擎（财务指标/技术指标/行业对标）
# ═══════════════════════════════════════════════════════════

def _run_precompute(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 1.2: 运行预计算引擎（财务指标 / 行业对标）。

    输出写入 data/tasks/ 供子代理（step4_finance/step6b_valuation 等）使用。
    预计算引擎需要股票代码（ticker），如果 metadata 没有则尝试解析。
    """
    import subprocess

    metadata = job_ctx.metadata or {}
    ticker = metadata.get("ticker", "")
    market = metadata.get("market", job_ctx.market)

    # 如果没有 ticker，尝试解析
    if not ticker:
        try:
            from tasks.valuation_enricher import _resolve_ticker
            ticker = _resolve_ticker(job_ctx.entity)
            if ticker:
                print(f"  🔍 [precompute] 自动解析 ticker: {job_ctx.entity} → {ticker}", flush=True)
        except Exception:
            pass

    precompute_results: dict[str, Any] = {}
    all_ok = True
    errors: list[str] = []

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 预计算引擎
    engines = {
        "financial_metrics": runtime_root / "scripts" / "financial_metrics_precompute.py",
        "sector_benchmarks": runtime_root / "scripts" / "sector_benchmarks.py",
    }

    for engine_name, script_path in engines.items():
        if not script_path.exists():
            errors.append(f"{engine_name}: script not found at {script_path}")
            all_ok = False
            continue

        try:
            # 没有 ticker 时跳过需要 ticker 的引擎
            if not ticker:
                precompute_results[engine_name] = {"status": "skipped", "reason": "no ticker available"}
                print(f"  ⚠️  [precompute] {engine_name}: 无 ticker，跳过", flush=True)
                continue

            print(f"  🔢 [precompute] 运行 {engine_name}...", flush=True)
            r = subprocess.run(
                [sys.executable, str(script_path), ticker, "--json"],
                capture_output=True, text=True, timeout=120,
            )

            if r.returncode != 0:
                error_msg = f"{engine_name}: exit {r.returncode}, stderr: {(r.stderr or '')[:200]}"
                errors.append(error_msg)
                print(f"  ⚠️  [precompute] {error_msg}", flush=True)
                precompute_results[engine_name] = {
                    "status": "error",
                    "error": error_msg,
                    "stdout": (r.stdout or "")[:500],
                }
                all_ok = False
                continue

            # 解析 JSON 输出
            try:
                output_data = json.loads(r.stdout.strip())
            except json.JSONDecodeError:
                output_data = {"raw": r.stdout.strip()}

            # 保存 JSON 输出到 data/tasks/
            output_file = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.json"
            output_file.write_text(
                json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # 同时保存 markdown 版本（可选，方便子代理阅读）
            try:
                r_md = subprocess.run(
                    [sys.executable, str(script_path), ticker, "--markdown"],
                    capture_output=True, text=True, timeout=60,
                )
                if r_md.returncode == 0:
                    md_file = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.md"
                    md_file.write_text(r_md.stdout, encoding="utf-8")
            except Exception:
                pass  # markdown 是可选的

            precompute_results[engine_name] = {
                "status": "ok",
                "output_file": str(output_file),
                "data": output_data,
            }
            print(f"  ✅ [precompute] {engine_name} 完成 → {output_file.name}", flush=True)

        except subprocess.TimeoutExpired:
            errors.append(f"{engine_name}: timeout (120s)")
            precompute_results[engine_name] = {"status": "timeout"}
            all_ok = False
        except Exception as e:
            errors.append(f"{engine_name}: {e}")
            precompute_results[engine_name] = {"status": "error", "error": str(e)}
            all_ok = False

    # 同步到 workspace outputs
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            for engine_name in engines:
                src = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.json"
                if src.exists():
                    shutil.copy2(src, ws.outputs_dir / f"precompute_{engine_name}.json")
                src_md = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.md"
                if src_md.exists():
                    shutil.copy2(src_md, ws.outputs_dir / f"precompute_{engine_name}.md")
        except Exception:
            pass

    return {
        "ok": all_ok,
        "mode": "precompute",
        "phase": "phase07_precompute",
        "job_id": job_ctx.job_id,
        "result": {
            "ticker": ticker,
            "market": market,
            "engines": precompute_results,
            "errors": errors,
            "output_dir": str(tasks_dir),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 4: Dispatch — 拆成 prepare + collect，避免死锁
# ═══════════════════════════════════════════════════════════

def _run_dispatch_prepare(runtime_root: Path, job_ctx: JobContext,
                           sequential: bool = False) -> dict[str, Any]:
    """Phase 4a: 使用 launch_next_wave 发射第一个 wave，返回 needs_dispatch=True。

    sequential=True: 每次只派发 wave 内一个 step，配合 has_more 循环调用，
    避免并行 Task 子代理触发 API 429。

    Coordinator 读取返回的 task_tool_instructions 后用 team 模式派发子代理。
    后续 wave 由 Coordinator 循环调用 launch_next_wave() 推进。
    """
    from scripts.ir_subagent_launcher_wb import (
        launch_next_wave,
        get_pipeline_status,
        step_output_path,
        STEP_DEPS,
        LAUNCH_WAVES,
    )

    metadata = job_ctx.metadata or {}
    entity = job_ctx.entity
    market = metadata.get("market", job_ctx.market) if metadata else job_ctx.market

    # 发射当前 wave（自动检测已完成的 step，支持断点恢复）
    wave_result = launch_next_wave(
        task_id=job_ctx.job_id,
        entity=entity,
        query=job_ctx.query,
        market=market,
        sequential=sequential,
    )

    if wave_result.get('all_done'):
        # 所有 step 已完成（恢复场景），直接进 collect
        return {
            "ok": True,
            "needs_dispatch": False,
            "has_more": False,
            "mode": "wave_orchestration",
            "phase": "phase08_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {
                "message": "All waves already completed, proceed to collect",
                "pipeline_status": get_pipeline_status(job_ctx.job_id),
            },
        }

    dispatched_count = wave_result.get('dispatched_count', 0)
    has_more = wave_result.get('has_more', False)

    if dispatched_count == 0 and not has_more:
        # sequential 模式下全阻塞 = 暂时无法推进，返回 needs_dispatch=True
        # 让 kernel 暂停，Coordinator 看到空 task_tool_instructions 后应等待重试
        if sequential:
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": False,
                "mode": "wave_orchestration",
                "phase": "phase08_dispatch_prepare",
                "job_id": job_ctx.job_id,
                "result": {
                    "message": "当前 wave 所有 step 被依赖阻塞，等待前序 step 完成后重试",
                    "task_tool_instructions": [],
                    "pipeline_status": get_pipeline_status(job_ctx.job_id),
                },
            }
        return {
            "ok": False,
            "mode": "wave_orchestration",
            "phase": "phase08_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {"error": "No steps dispatched in wave", "wave_result": wave_result},
        }

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": has_more,
        "mode": "wave_orchestration",
        "phase": "phase08_dispatch_prepare",
        "job_id": job_ctx.job_id,
        "result": {
            "wave_index": wave_result.get('wave_index'),
            "wave_label": wave_result.get('wave_label'),
            "dispatched_count": dispatched_count,
            "has_more": has_more,
            "task_tool_instructions": wave_result.get('task_tool_instructions', []),
            "after_all_tasks_complete": wave_result.get('after_all_tasks_complete'),
            "total_waves": len(LAUNCH_WAVES),
            "pipeline_status": get_pipeline_status(job_ctx.job_id),
        },
    }


def _run_fact_store_merge(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 3.5: merge step facts sidecars into the generic Fact Store."""
    from scripts.ir_fact_store import merge_step_fact_sidecars

    tasks_dir = runtime_root / "data" / "tasks"
    result = merge_step_fact_sidecars(
        job_ctx.job_id,
        tasks_dir=tasks_dir,
        entity=job_ctx.entity,
        market=job_ctx.market,
    )
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            output_path = result.get("output_path", "")
            index_path = result.get("index_path", "")
            if output_path and Path(output_path).exists():
                shutil.copy2(output_path, ws.outputs_dir / "fact_store.json")
            if index_path and Path(index_path).exists():
                shutil.copy2(index_path, ws.outputs_dir / "fact_store_index.json")
        except Exception:
            pass
    return {
        "ok": result.get("invalid_count", 0) == 0,
        "mode": "quality_production",
        "phase": "phase10_fact_store_merge",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 4b: 检查子代理输出是否完成，做质量门禁。

    Coordinator 在所有 wave 的 task 子代理完成后调用此 phase。
    """
    from scripts.ir_subagent_launcher_wb import (
        check_step_quality,
        dispatch_rewrite,
        step_output_path,
        get_pipeline_status,
        STEP_DEPS,
    )

    metadata = job_ctx.metadata or {}
    entity = job_ctx.entity
    market = metadata.get("market", job_ctx.market) if metadata else job_ctx.market

    # 获取管线状态
    pipeline_status = get_pipeline_status(job_ctx.job_id)

    completed_steps: list[str] = []
    step_quality: dict[str, dict[str, Any]] = {}

    for step_name in STEP_DEPS:
        output_path = step_output_path(job_ctx.job_id, step_name)
        if output_path.exists() and output_path.stat().st_size > 100:
            completed_steps.append(step_name)
            _sync_step_to_workspace(job_ctx, step_name, output_path)
            quality = check_step_quality(job_ctx.job_id, step_name)
            step_quality[step_name] = quality

    total_expected = len(STEP_DEPS)
    completion_rate = len(completed_steps) / max(total_expected, 1)
    circuit_break = completion_rate < 0.5

    rewrite_dispatched: list[str] = []
    for step_name, quality in step_quality.items():
        if quality.get("verdict") == "fail" and quality.get("score", 0) > 0:
            try:
                rewrite_result = dispatch_rewrite(
                    job_ctx.job_id, step_name, entity, job_ctx.query, market
                )
                if rewrite_result.get("status") == "dispatched":
                    rewrite_dispatched.append(step_name)
            except Exception:
                pass

    from scripts.ir_quality_gate import run_step_gate
    step_gate = run_step_gate(
        job_ctx.job_id,
        step_order=list(STEP_DEPS.keys()),
        tasks_dir=runtime_root / "data" / "tasks",
    )

    return {
        "ok": not circuit_break and step_gate.get("passed", False),
        "mode": "wave_orchestration",
        "phase": "phase09_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "completed": len(completed_steps),
            "total_expected": total_expected,
            "completion_rate": round(completion_rate, 2),
            "circuit_break": circuit_break,
            "completed_steps": completed_steps,
            "step_quality": step_quality,
            "step_gate": step_gate,
            "rewrite_dispatched": rewrite_dispatched,
            "pipeline_status": pipeline_status,
            "workspace_outputs_dir": str(_workspace_for(job_ctx).outputs_dir) if _workspace_for(job_ctx) else "",
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 09c: Wave Evidence Gate (对标 BP wave evidence gate)
# ═══════════════════════════════════════════════════════════

def _run_wave_evidence_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09c: Wave Evidence Gate — 检查各 wave step 的证据完整性。

    REPAIR 时返回 needs_dispatch + has_more（sequential 派发 repair 子代理）。
    降级放行时记录 repair_exhausted 继续推进。
    """
    from scripts.ir_wave_evidence_gate import (
        evaluate_wave_evidence_gate,
        write_wave_gate,
        build_ir_repair_manifests,
    )
    from scripts.bp_utils import read_attempt_count

    tasks_dir = runtime_root / "data" / "tasks"

    # 对每个 wave 跑 gate（wave 0-4 对应 LAUNCH_WAVES）
    all_verdicts: list[str] = []
    all_issues: list[dict] = []
    all_repair_manifests: list[str] = []
    last_wave = 0

    for wave_idx in range(5):
        gate_result = evaluate_wave_evidence_gate(
            task_id=job_ctx.job_id,
            wave=wave_idx,
            tasks_dir=tasks_dir,
        )
        write_wave_gate(job_ctx.job_id, wave_idx, gate_result, tasks_dir)
        all_verdicts.append(gate_result["verdict"])
        all_issues.extend(gate_result.get("issues", []))

        if gate_result.get("needs_repair"):
            manifests = build_ir_repair_manifests(
                task_id=job_ctx.job_id,
                wave=wave_idx,
                gate_result=gate_result,
                tasks_dir=tasks_dir,
            )
            all_repair_manifests.extend(manifests)
            last_wave = wave_idx

    # 判定总体 verdict
    has_repair = "REPAIR" in all_verdicts
    has_fail = "FAIL" in all_verdicts

    if has_fail:
        # 直接终止
        return {
            "ok": False,
            "mode": "wave_evidence_gate",
            "phase": "phase09c_wave_evidence_gate",
            "job_id": job_ctx.job_id,
            "result": {
                "verdicts": all_verdicts,
                "issues": all_issues,
            },
        }

    if has_repair:
        # 检查 repair 次数
        attempt = read_attempt_count(job_ctx.job_id, "wave_evidence_gate", tasks_dir=tasks_dir)
        if attempt > 1:
            # 降级放行
            print(f"  ⚠️ [wave_evidence_gate] repair 次数已用尽 (attempt={attempt}), 降级放行", flush=True)
            return {
                "ok": True,
                "mode": "wave_evidence_gate",
                "phase": "phase09c_wave_evidence_gate",
                "job_id": job_ctx.job_id,
                "result": {
                    "verdict": "PASS_WITH_DISCLOSURE",
                    "repair_exhausted": True,
                    "verdicts": all_verdicts,
                    "issues": all_issues,
                },
            }

        # 返回 needs_dispatch + 第一个 manifest + has_more
        if all_repair_manifests:
            first_manifest = all_repair_manifests[0]
            remaining = all_repair_manifests[1:]
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": len(remaining) > 0,
                "mode": "wave_evidence_gate",
                "phase": "phase09c_wave_evidence_gate",
                "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [first_manifest],
                    "roles": ["ir_repair"],
                    "task_dir": str(tasks_dir),
                },
                "instruction": (
                    f"Read manifest: {first_manifest}\n"
                    f"Dispatch ONE repair sub-agent using Agent tool.\n"
                    f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
                    f"修复完成后用 start_phase='phase09c_wave_evidence_gate' 恢复管线。"
                    + (f"\n\n还有 {len(remaining)} 个 repair manifest 待处理。" if remaining else "")
                ),
                "result": {
                    "verdict": "REPAIR",
                    "verdicts": all_verdicts,
                    "issues": all_issues,
                    "remaining_manifests": remaining,
                },
            }

    # PASS
    return {
        "ok": True,
        "mode": "wave_evidence_gate",
        "phase": "phase09c_wave_evidence_gate",
        "job_id": job_ctx.job_id,
        "result": {
            "verdict": "PASS",
            "verdicts": all_verdicts,
            "issues": all_issues,
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 11-13: Quality production review and assembly
# ═══════════════════════════════════════════════════════════

def _run_section_package_validation(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_quality_gate import run_section_gate
    from scripts.ir_section_package import write_section_package_index

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_section_package_index(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    section_gate = run_section_gate(job_ctx.job_id, tasks_dir=tasks_dir)
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "section_packages.json")
        except Exception:
            pass
    summary = payload.get("summary", {})
    return {
        "ok": summary.get("failed", 0) == 0 and summary.get("total", 0) > 0 and section_gate.get("passed", False),
        "mode": "quality_production",
        "phase": "phase11_section_package_validation",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "summary": summary, "section_gate": section_gate},
    }


def _run_debate_review_phase(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_debate_review import write_debate_review

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_debate_review(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "debate_review.json")
        except Exception:
            pass
    verdict = payload.get("verdict", "REWRITE_REQUIRED")
    return {
        "ok": verdict in ("PASS", "WARN"),
        "mode": "quality_production",
        "phase": "phase12_debate_review",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "verdict": verdict, "issues": payload.get("issues", [])},
    }


def _run_final_assembly_phase(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_final_assembler import write_final_report

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_final_report(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "final_assembly.json")
            md_path = payload.get("markdown_path")
            if md_path and Path(md_path).exists():
                shutil.copy2(md_path, ws.outputs_dir / "final_report.md")
        except Exception:
            pass
    return {
        "ok": bool(payload.get("ok", False)),
        "mode": "quality_production",
        "phase": "phase13_final_assembly",
        "job_id": job_ctx.job_id,
        "result": payload,
    }


# ═══════════════════════════════════════════════════════════
# Phase 5: Delivery — 对抗验证 + DOCX + 交付（workspace-aware）
# ═══════════════════════════════════════════════════════════

def _run_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_delivery_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase14_delivery")
    if cached is not None:
        print(f"  📦 [ir] 使用缓存的 delivery 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase14_delivery", pipeline="ir")


def _run_delivery_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 5: 对抗验证 + 审计 + DOCX + 交付（子进程内直接调用）。

    All artifacts are synced to workspace.delivery_dir.
    Legacy paths remain intact.
    """
    import subprocess
    from scripts.verification_agent import run_verification

    metadata = job_ctx.metadata or {}
    session_id = metadata.get("session_id", "")

    # 1. 对抗式验证
    verification = {}
    verification_path = ""
    try:
        verification = run_verification(task_id=job_ctx.job_id, pipeline="ir")
    except Exception as e:
        verification = {"verdict": "ERROR", "summary": str(e)}

    verification_verdict = verification.get("verdict", "UNKNOWN")

    # Sync verification to workspace
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            vdest = ws.verification_dir / "verification_result.json"
            vdest.write_text(
                json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            verification_path = str(vdest)
        except Exception:
            pass

    # 2. 来源审计 + 执行审计
    audits_ok = True
    audit_errors: list[str] = []
    audit_paths: dict[str, str] = {}
    for audit_script in ("build_ir_source_audit.py", "build_ir_execution_audit.py"):
        script_path = runtime_root / "scripts" / audit_script
        if script_path.exists():
            try:
                r = subprocess.run(
                    [sys.executable, str(script_path), job_ctx.job_id],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode != 0:
                    audits_ok = False
                    audit_errors.append(f"{audit_script}: exit {r.returncode}")
                else:
                    # Try to parse output path and verdict from stdout
                    try:
                        payload = json.loads(r.stdout.strip())
                        if payload.get("verdict") and payload.get("verdict") != "PASS":
                            audits_ok = False
                            audit_errors.append(f"{audit_script}: verdict {payload.get('verdict')}")
                        audit_output = payload.get("output", "")
                        if audit_output:
                            audit_paths[audit_script] = audit_output
                            if Path(audit_output).exists():
                                _sync_artifact_to_workspace(job_ctx, audit_script, Path(audit_output))
                    except Exception:
                        pass
            except Exception as e:
                audits_ok = False
                audit_errors.append(f"{audit_script}: {e}")

    # 3. 最终报告门禁
    from scripts.ir_quality_gate import run_report_gate
    report_gate = run_report_gate(job_ctx.job_id, tasks_dir=runtime_root / "data" / "tasks")

    # 4. 生成券商风格 Word 报告
    docx_path = ""
    docx_error = ""
    build_docx_script = runtime_root / "scripts" / "build_ir_broker_report_docx.py"
    if report_gate.get("passed", False) and audits_ok and verification_verdict != "ERROR" and build_docx_script.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(build_docx_script), job_ctx.job_id],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                try:
                    payload = json.loads(r.stdout)
                    docx_path = payload.get("output", "")
                    if docx_path and Path(docx_path).exists():
                        _sync_artifact_to_workspace(job_ctx, "broker_report_docx", Path(docx_path))
                except Exception:
                    docx_path = ""
            else:
                docx_error = f"exit {r.returncode}: {r.stderr[:200]}"
        except Exception as e:
            docx_error = str(e)

    # 4. 交付通知 — 已移除（开源发布版本不含消息推送）
    delivery_ok = False
    delivery_error = "Notification removed for open-source release"

    # Collect workspace artifact summary
    workspace_artifacts = {}
    if ws is not None:
        artifacts_manifest = ws.state_dir / "artifacts.json"
        if artifacts_manifest.exists():
            try:
                workspace_artifacts = json.loads(artifacts_manifest.read_text(encoding="utf-8"))
            except Exception:
                pass

    delivery_phase_ok = report_gate.get("passed", False) and audits_ok and verification_verdict != "ERROR" and bool(docx_path)

    return {
        "ok": delivery_phase_ok,
        "mode": "legacy_wrapped",
        "phase": "phase14_delivery",
        "job_id": job_ctx.job_id,
        "result": {
            "verification_verdict": verification_verdict,
            "verification_summary": verification.get("summary", ""),
            "verification_path": verification_path,
            "audits_ok": audits_ok,
            "audit_errors": audit_errors,
            "audit_paths": audit_paths,
            "docx_path": docx_path,
            "docx_error": docx_error,
            "report_gate": report_gate,
            "delivery_ok": delivery_ok,
            "delivery_error": delivery_error,
            "delivery_quality": verification_verdict.lower() if verification_verdict != "ERROR" else "unknown",
            "workspace_artifacts": workspace_artifacts,
            "workspace_delivery_dir": str(ws.delivery_dir) if ws else "",
        },
    }


class IRProfile(PipelineProfile):
    def __init__(self, runtime_root: Path):
        super().__init__(
            name="ir",
            job_type="investment_research",
            phase_handlers={
                "phase01_preflight": lambda job_ctx: _run_preflight(runtime_root, job_ctx),
                "phase02_company_verify": lambda job_ctx: _run_company_verify(runtime_root, job_ctx),
                "phase03_research_plan": lambda job_ctx: _run_research_plan(runtime_root, job_ctx),
                "phase03c_research_plan_collect": lambda job_ctx: _run_research_plan_collect(runtime_root, job_ctx),
                "phase04_presearch": lambda job_ctx: _run_presearch(runtime_root, job_ctx),
                "phase05_extract": lambda job_ctx: _run_extract(runtime_root, job_ctx),
                "phase06_fact_store_bootstrap": lambda job_ctx: _run_fact_store_bootstrap(runtime_root, job_ctx),
                "phase07_precompute": lambda job_ctx: _run_precompute(runtime_root, job_ctx),
                "phase08_dispatch_prepare": lambda job_ctx: _run_dispatch_prepare(runtime_root, job_ctx, sequential=True),
                "phase09_dispatch_collect": lambda job_ctx: _run_dispatch_collect(runtime_root, job_ctx),
                "phase09c_wave_evidence_gate": lambda job_ctx: _run_wave_evidence_gate(runtime_root, job_ctx),
                "phase10_fact_store_merge": lambda job_ctx: _run_fact_store_merge(runtime_root, job_ctx),
                "phase11_section_package_validation": lambda job_ctx: _run_section_package_validation(runtime_root, job_ctx),
                "phase12_debate_review": lambda job_ctx: _run_debate_review_phase(runtime_root, job_ctx),
                "phase13_final_assembly": lambda job_ctx: _run_final_assembly_phase(runtime_root, job_ctx),
                "phase14_delivery": lambda job_ctx: _run_delivery(runtime_root, job_ctx),
            },
        )
        self.runtime_root = runtime_root
