from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    """Copy a completed step output file into the workspace outputs dir."""
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
# Phase 01: Topic Intake — 课题元数据解析
# ═══════════════════════════════════════════════════════════

def _run_topic_intake(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 01: 解析课题元数据（从 DOCX/MD/JSON 或直接 entity/query）。

    产出 ic_topic_metadata.json，供下游 presearch 和 research plan 使用。
    """
    from scripts.ic_topic_intake import parse_topic_metadata

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 获取课题源文件路径
    metadata = job_ctx.metadata or {}
    topic_file = metadata.get("topic_file", "")
    entity = job_ctx.entity or metadata.get("entity", "")

    result = parse_topic_metadata(
        topic_source=topic_file or entity,
        entity=entity,
        output_dir=tasks_dir,
    )

    # 同时保留 scope 兼容性（legacy ic_presearch 需要）
    company_list = result.get("key_companies", [])
    scope_data = {
        "industry": entity,
        "query": job_ctx.query or "",
        "market": job_ctx.market,
        "keywords": {"primary": entity, "variants": [entity], "search_queries": []},
        "company_list": company_list,
        "defined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    scope_path = tasks_dir / f"{job_ctx.job_id}-ic_scope.json"
    scope_path.write_text(json.dumps(scope_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "mode": "topic_intake",
        "phase": "phase01_topic_intake",
        "job_id": job_ctx.job_id,
        "result": {
            "metadata_path": str(tasks_dir / "ic_topic_metadata.json"),
            "core_question": result.get("core_question", ""),
            "sub_questions_count": len(result.get("sub_questions", [])),
            "key_companies": company_list,
            "category": result.get("category", ""),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 0.5: Multi-Company Verify — 批量公司工商验证
# ═══════════════════════════════════════════════════════════

def _run_multi_company_verify(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 0.5: 对 query 中提到的公司做批量工商验证（天眼查MCP）。

    如果没有提供具体公司名，则跳过。
    """
    tasks_dir = runtime_root / "data" / "tasks"
    scope_path = tasks_dir / f"{job_ctx.job_id}-ic_scope.json"

    company_list = []
    if scope_path.exists():
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        company_list = scope.get("company_list", [])

    if not company_list:
        return {
            "ok": True,
            "mode": "skipped",
            "phase": "phase02_multi_company_verify",
            "job_id": job_ctx.job_id,
            "result": {"message": "No companies to verify", "verified_count": 0},
        }

    # 调用 ir_company_verify 做批量验证
    verified = []
    for company in company_list:
        try:
            from scripts.ir_company_verify import run as run_verify
            result = run_verify(
                task_id=job_ctx.job_id,
                entity=company,
                market=job_ctx.market,
            )
            verified.append({"company": company, "result": result})
        except Exception as e:
            verified.append({"company": company, "error": str(e)})

    verify_path = tasks_dir / f"{job_ctx.job_id}-ic_company_verify.json"
    verify_path.write_text(
        json.dumps(verified, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "ok": True,
        "mode": "multi_company_verify",
        "phase": "phase02_multi_company_verify",
        "job_id": job_ctx.job_id,
        "result": {"verified_count": len(verified), "companies": verified},
    }


# ═══════════════════════════════════════════════════════════
# Phase 1: Industry Presearch — 行业数据预搜索
# ═══════════════════════════════════════════════════════════

def _run_industry_presearch(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 1: 行业数据预搜索（NeoData + SearchGateway）"""
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_industry_presearch_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase03_presearch")
    if cached is not None:
        print(f"  📦 [ic] 使用缓存的 presearch 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase03_presearch", pipeline="ic")


def _run_industry_presearch_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """presearch actual execution — v1.0: unified presearch_query_builder.

    Data sources: web_search + tencent_news + westock sector/finance + tyc + neodata
    Queries driven by topic metadata (core_question, sub_questions, key_companies).
    """
    from scripts.presearch_query_builder import execute_presearch

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # Read topic metadata for IC-specific query generation
    topic_metadata = None
    topic_path = tasks_dir / "ic_topic_metadata.json"
    if topic_path.exists():
        try:
            topic_metadata = json.loads(topic_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    result = execute_presearch(
        pipeline="ic",
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        market=job_ctx.market,
        query=job_ctx.query or "",
        topic_metadata=topic_metadata,
        output_dir=tasks_dir,
    )
    return {
        "ok": True,
        "mode": "ic_presearch",
        "phase": "phase03_presearch",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 1.5: Content Extraction
# ═══════════════════════════════════════════════════════════

def _run_extract(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 1.5: URL 内容提取（行业研究导向）"""
    from scripts.ir_extract_content import extract_from_presearch

    result = extract_from_presearch(
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        max_pages=15,
        pipeline='ic',
    )
    ok_count = result.get("ok_count", 0)
    total = result.get("total_urls", 0)

    return {
        "ok": ok_count > 0,
        "mode": "legacy_wrapped",
        "phase": "phase04_extract",
        "job_id": job_ctx.job_id,
        "result": {"total_urls": total, "ok_count": ok_count},
    }


# ═══════════════════════════════════════════════════════════
# Phase 1.2: Industry Precompute — 行业预计算
# ═══════════════════════════════════════════════════════════

def _run_industry_precompute(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 1.2: 行业规模预计算 + 财务基准（行业整体层面）。

    IC 管线的 precompute 侧重行业整体数据，不依赖单一 ticker。
    三大引擎：
      1. industry_size: TAM/SAM/SOM 三层推算
      2. sector_benchmarks: 行业板块基准（PE/ROE/毛利率均值）
      3. key_company_metrics: 关键公司财务指标汇总
    """
    import subprocess

    entity = job_ctx.entity
    market = job_ctx.market
    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    precompute_results: dict[str, Any] = {}
    all_ok = True
    errors: list[str] = []

    # IC 预计算引擎
    ic_script = runtime_root / "scripts" / "ic_precompute.py"
    if not ic_script.exists():
        return {
            "ok": False,
            "mode": "precompute",
            "phase": "phase05_precompute",
            "job_id": job_ctx.job_id,
            "result": {"error": f"ic_precompute.py not found at {ic_script}"},
        }

    try:
        print(f"  🔢 [ic-precompute] 运行 IC 行业预计算...", flush=True)
        r = subprocess.run(
            ["python3", str(ic_script), entity, "--market", market,
             "--task-id", job_ctx.job_id, "--json"],
            capture_output=True, text=True, timeout=120,
        )

        if r.returncode != 0:
            error_msg = f"ic_precompute: exit {r.returncode}, stderr: {(r.stderr or '')[:200]}"
            errors.append(error_msg)
            print(f"  ⚠️  [ic-precompute] {error_msg}", flush=True)
            precompute_results["ic_precompute"] = {
                "status": "error",
                "error": error_msg,
            }
            all_ok = False
        else:
            try:
                output_data = json.loads(r.stdout.strip())
            except json.JSONDecodeError:
                output_data = {"raw": r.stdout.strip()}

            # 保存 JSON 输出到 data/tasks/
            output_file = tasks_dir / f"{job_ctx.job_id}_precompute_ic.json"
            output_file.write_text(
                json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # 同时保存 markdown 版本
            try:
                r_md = subprocess.run(
                    ["python3", str(ic_script), entity, "--market", market,
                     "--task-id", job_ctx.job_id, "--markdown"],
                    capture_output=True, text=True, timeout=60,
                )
                if r_md.returncode == 0:
                    md_file = tasks_dir / f"{job_ctx.job_id}_precompute_ic.md"
                    md_file.write_text(r_md.stdout, encoding="utf-8")
            except Exception:
                pass

            precompute_results["ic_precompute"] = {
                "status": "ok",
                "output_file": str(output_file),
            }
            print(f"  ✅ [ic-precompute] 完成 → {output_file.name}", flush=True)

    except subprocess.TimeoutExpired:
        errors.append("ic_precompute: timeout (120s)")
        precompute_results["ic_precompute"] = {"status": "timeout"}
        all_ok = False
    except Exception as e:
        errors.append(f"ic_precompute: {e}")
        precompute_results["ic_precompute"] = {"status": "error", "error": str(e)}
        all_ok = False

    # 同步到 workspace outputs
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            src = tasks_dir / f"{job_ctx.job_id}_precompute_ic.json"
            if src.exists():
                shutil.copy2(src, ws.outputs_dir / "precompute_ic.json")
            src_md = tasks_dir / f"{job_ctx.job_id}_precompute_ic.md"
            if src_md.exists():
                shutil.copy2(src_md, ws.outputs_dir / "precompute_ic.md")
        except Exception:
            pass

    return {
        "ok": all_ok,
        "mode": "precompute",
        "phase": "phase05_precompute",
        "job_id": job_ctx.job_id,
        "result": {
            "entity": entity,
            "market": market,
            "engines": precompute_results,
            "errors": errors,
            "output_dir": str(tasks_dir),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 4a: Dispatch Prepare
# ═══════════════════════════════════════════════════════════

def _run_dispatch_prepare(runtime_root: Path, job_ctx: JobContext,
                           sequential: bool = False) -> dict[str, Any]:
    """Phase 07: launch_next_wave 发射子代理。

    v1.0: 读取 ic_research_plan.json 的 activated_steps，只对激活的维度生成 wave step。
    不相关的维度（如纯技术比较课题的 financial/valuation）不会被派发。
    """
    from scripts.ic_subagent_launcher import (
        launch_next_wave,
        get_pipeline_status,
    )

    entity = job_ctx.entity
    market = job_ctx.market

    # Read research plan to get step filter
    tasks_dir = runtime_root / "data" / "tasks"
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"
    step_filter: set[str] | None = None
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            activated = plan.get("activated_steps", [])
            if activated:
                step_filter = set(activated)
                print(f"  🎯 [ic-dispatch] 步骤过滤器: {sorted(step_filter)}", flush=True)
        except Exception:
            pass

    wave_result = launch_next_wave(
        task_id=job_ctx.job_id,
        entity=entity,
        query=job_ctx.query,
        market=market,
        sequential=sequential,
        step_filter=step_filter,
    )

    if wave_result.get('all_done'):
        return {
            "ok": True,
            "needs_dispatch": False,
            "has_more": False,
            "mode": "wave_orchestration",
            "phase": "phase07_dispatch_prepare",
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
                "phase": "phase07_dispatch_prepare",
                "job_id": job_ctx.job_id,
                "result": {
                    "message": "当前 wave 所有 step 被依赖阻塞，等待前序 step 完成后重试",
                    "blocked_steps": [r.get('step') for r in wave_result.get('steps', [])],
                    "task_tool_instructions": [],
                    "pipeline_status": get_pipeline_status(job_ctx.job_id),
                },
            }
        return {
            "ok": False,
            "has_more": False,
            "mode": "wave_orchestration",
            "phase": "phase07_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {"error": "No steps dispatched in wave", "wave_result": wave_result},
        }

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": has_more,
        "mode": "wave_orchestration",
        "phase": "phase07_dispatch_prepare",
        "job_id": job_ctx.job_id,
        "result": {
            "wave_index": wave_result.get('wave_index'),
            "wave_label": wave_result.get('wave_label'),
            "dispatched_count": dispatched_count,
            "task_tool_instructions": wave_result.get('task_tool_instructions', []),
            "after_all_tasks_complete": wave_result.get('after_all_tasks_complete'),
            "pipeline_status": get_pipeline_status(job_ctx.job_id),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 4b: Dispatch Collect
# ═══════════════════════════════════════════════════════════

def _run_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 4b: 检查子代理输出 + 质量门禁"""
    from scripts.ic_subagent_launcher import (
        check_step_quality,
        step_output_path,
        get_pipeline_status,
    )
    import json as _json

    entity = job_ctx.entity
    market = job_ctx.market

    pipeline_status = get_pipeline_status(job_ctx.job_id)

    # 从 wave manifest 读取 step_deps
    from scripts.ic_subagent_launcher import wave_manifest_path, load_json
    wm = load_json(wave_manifest_path(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    completed_steps: list[str] = []
    step_quality: dict[str, dict[str, Any]] = {}

    for step_name in step_deps:
        output_path = step_output_path(job_ctx.job_id, step_name)
        if output_path.exists() and output_path.stat().st_size > 100:
            completed_steps.append(step_name)
            _sync_step_to_workspace(job_ctx, step_name, output_path)
            quality = check_step_quality(job_ctx.job_id, step_name)
            step_quality[step_name] = quality

    total_expected = len(step_deps)
    completion_rate = len(completed_steps) / max(total_expected, 1)
    circuit_break = completion_rate < 0.5

    return {
        "ok": not circuit_break,
        "mode": "wave_orchestration",
        "phase": "phase07_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "completed": len(completed_steps),
            "total_expected": total_expected,
            "completion_rate": round(completion_rate, 2),
            "circuit_break": circuit_break,
            "completed_steps": completed_steps,
            "step_quality": step_quality,
            "pipeline_status": pipeline_status,
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 5: Delivery
# ═══════════════════════════════════════════════════════════

def _run_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 5: 对抗验证 + DOCX + 交付"""
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_delivery_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase08_delivery")
    if cached is not None:
        print(f"  📦 [ic] 使用缓存的 delivery 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase08_delivery", pipeline="ic")


def _run_delivery_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 5: 对抗验证 + DOCX + 交付（子进程内直接调用）"""
    from scripts.verification_agent import run_verification
    from scripts.ic_subagent_launcher import finalize_pipeline

    # 1. 对抗式验证
    verification = {}
    try:
        verification = run_verification(task_id=job_ctx.job_id, pipeline="ic")
    except Exception as e:
        verification = {"verdict": "ERROR", "summary": str(e)}

    verification_verdict = verification.get("verdict", "UNKNOWN")

    # 2. finalize（桌面 + 通知）
    finalize_result = finalize_pipeline(job_ctx.job_id, entity=job_ctx.entity, market=job_ctx.market)

    return {
        "ok": True,
        "mode": "legacy_wrapped",
        "phase": "phase08_delivery",
        "job_id": job_ctx.job_id,
        "result": {
            "verification_verdict": verification_verdict,
            "verification_summary": verification.get("summary", ""),
            "finalize": finalize_result,
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 04: Research Plan — LLM 驱动的 enrichment
# ═══════════════════════════════════════════════════════════

def _run_research_plan(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 04: IC 研究计划 — needs_dispatch 模式。

    phase03 已完成 presearch → 生成空骨架 → 主 AI 读 presearch 数据 enrichment。
    脚本只提供空骨架和文件路径，所有研究内容由 LLM 决定。
    """
    from scripts.ic_research_planner import build_empty_skeleton

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 读取课题元数据
    topic_path = tasks_dir / "ic_topic_metadata.json"
    topic_metadata: dict[str, Any] = {}
    if topic_path.exists():
        try:
            topic_metadata = json.loads(topic_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    entity = job_ctx.entity or topic_metadata.get("entity", "")

    # 生成空骨架
    skeleton = build_empty_skeleton(
        task_id=job_ctx.job_id,
        entity=entity,
        topic_metadata=topic_metadata,
    )
    skeleton_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan_skeleton.json"
    skeleton_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 检查 presearch 数据
    presearch_path = tasks_dir / f"{job_ctx.job_id}-presearch_results.json"
    presearch_ready = presearch_path.exists()

    # 写 enrichment instruction
    instruction_path = tasks_dir / f"{job_ctx.job_id}-ic_phase04_enrichment_instruction.md"
    instruction_text = _ic_enrichment_instruction(
        entity=entity,
        tasks_dir=tasks_dir,
        presearch_ready=presearch_ready,
    )
    instruction_path.write_text(instruction_text, encoding="utf-8")

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "ic_research_plan",
        "phase": "phase04_research_plan",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "instruction_path": str(instruction_path),
            "skeleton_path": str(skeleton_path),
            "presearch_path": str(presearch_path),
            "presearch_ready": presearch_ready,
            "topic_metadata_path": str(topic_path),
            "task_dir": str(tasks_dir),
        },
        "instruction": instruction_text,
    }


def _ic_enrichment_instruction(
    entity: str,
    tasks_dir: Path,
    presearch_ready: bool = False,
) -> str:
    """生成 phase04 enrichment 的主 AI 指令。"""
    presearch_section = ""
    if presearch_ready:
        presearch_section = """\
## Presearch 数据（已就绪）
phase03 已完成预搜索。你必须先阅读 presearch 数据再生成研究计划：
- 完整结果: `*_presearch_results.md` 或 `*_presearch_results.json`
- 摘要: `*_presearch_summary.json`

Presearch 数据消费规则：
- presearch 覆盖好的维度 → 可适当降低对应 claim/question 的 priority
- presearch 明显搜不到的维度 → 提升 priority，扩展搜索关键词
- presearch 中发现的意外线索 → 生成 additional_claims
"""
    else:
        presearch_section = """\
## Presearch 数据（未就绪）
phase03 预搜索可能未完成或未产生结果。你仍需要生成完整的研究计划，但在 data_gaps 中标注"presearch unavailable"。
"""

    return f"""\
PHASE04 IC RESEARCH PLAN ENRICHMENT — 主 AI 执行

## 背景
课题 `{entity}` 的研究规划由你全权负责。脚本不提供任何预定义的 core_questions、claim_matrix 或 fact_requirements。

{presearch_section}

## 输入文件（按读取顺序）
1. Presearch 数据: `*_presearch_results.*` + `*_presearch_summary.json`（如就绪）
2. 课题元数据: `ic_topic_metadata.json`
3. 空骨架: `*_ic_research_plan_skeleton.json`
4. Enrichment 指令库: `instruction_store_ic/ic_research_plan_enrichment.md`

## 执行步骤
1. 读取 instruction_store_ic/ic_research_plan_enrichment.md 了解完整输出格式
2. 读取课题元数据，理解核心问题、子问题、研究内容
3. （如 presearch 就绪）读取 presearch 数据，了解当前信息覆盖情况
4. 基于课题元数据 + presearch 数据，生成完整的研究计划（core_questions、claim_matrix、fact_requirements、step 激活列表）
5. 将输出写入 `*_ic_research_plan_enrichment.json`
6. 用 start_phase='phase04_research_plan_collect' 恢复管线

## 关键约束
- core_questions[0] 必须是课题元数据中的原始核心问题
- 所有 fact_key 由你命名，无预定义列表
- step 激活决策基于课题内容，不要默认激活所有 step
- 输出格式严格遵循 instruction_store_ic/ic_research_plan_enrichment.md
"""


def _run_research_plan_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 04 collect: 读取 LLM 输出的完整 research plan → 校验 → 落盘。"""
    from scripts.ic_research_planner import (
        apply_enrichment,
        load_skeleton,
        load_enrichment,
        write_research_plan,
    )

    tasks_dir = runtime_root / "data" / "tasks"
    skeleton_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan_skeleton.json"

    if not skeleton_path.exists():
        return {
            "ok": False,
            "mode": "ic_research_plan",
            "phase": "phase04_research_plan_collect",
            "job_id": job_ctx.job_id,
            "result": {"error": "skeleton file not found"},
        }

    skeleton = load_skeleton(skeleton_path)

    enrichment_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan_enrichment.json"
    if not enrichment_path.exists():
        print(f"  ⚠️ [phase04_collect] enrichment 文件不存在，plan 保持空骨架状态", flush=True)
        return {
            "ok": True,
            "mode": "ic_research_plan",
            "phase": "phase04_research_plan_collect",
            "job_id": job_ctx.job_id,
            "result": {"plan_path": str(skeleton_path), "enrichment": "missing_fallback"},
        }

    enrichment = load_enrichment(enrichment_path)
    plan = apply_enrichment(skeleton, enrichment)

    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"
    write_research_plan(plan_path, plan)

    return {
        "ok": plan.get("plan_status") == "ready",
        "mode": "ic_research_plan",
        "phase": "phase04_research_plan_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "plan_path": str(plan_path),
            "plan_status": plan.get("plan_status"),
            "core_questions_count": len(plan.get("core_questions", [])),
            "claim_count": len(plan.get("claim_matrix", [])),
            "activated_steps": plan.get("activated_steps", []),
        },
    }


# ═══════════════════════════════════════════════════════════
# IC Profile
# ═══════════════════════════════════════════════════════════

class ICProfile(PipelineProfile):
    def __init__(self, runtime_root: Path):
        super().__init__(
            name="ic",
            job_type="industry_coverage",
            phase_handlers={
                "phase01_topic_intake": lambda job_ctx: _run_topic_intake(runtime_root, job_ctx),
                "phase02_multi_company_verify": lambda job_ctx: _run_multi_company_verify(runtime_root, job_ctx),
                "phase03_presearch": lambda job_ctx: _run_industry_presearch(runtime_root, job_ctx),
                "phase04_research_plan": lambda job_ctx: _run_research_plan(runtime_root, job_ctx),
                "phase04_research_plan_collect": lambda job_ctx: _run_research_plan_collect(runtime_root, job_ctx),
                "phase05_extract": lambda job_ctx: _run_extract(runtime_root, job_ctx),
                "phase06_precompute": lambda job_ctx: _run_industry_precompute(runtime_root, job_ctx),
                "phase07_dispatch_prepare": lambda job_ctx: _run_dispatch_prepare(runtime_root, job_ctx, sequential=True),
                "phase08_dispatch_collect": lambda job_ctx: _run_dispatch_collect(runtime_root, job_ctx),
                "phase09_delivery": lambda job_ctx: _run_delivery(runtime_root, job_ctx),
            },
        )
        self.runtime_root = runtime_root
