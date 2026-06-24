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
# Phase 0: Scope Definition — 行业边界定义
# ═══════════════════════════════════════════════════════════

def _run_scope_definition(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 0: 定义行业边界 + 关键词扩展 + 关键公司名单解析。

    输出行业关键词列表和初步公司清单，供后续 phase 使用。
    """
    entity = job_ctx.entity
    query = job_ctx.query or entity

    # 基础关键词扩展
    keywords = {
        "primary": entity,
        "variants": [entity],
        "search_queries": [
            f'"{entity}" 行业 市场规模 竞争格局',
            f'"{entity}" 产业链 上中下游',
            f'"{entity}" industry market size competitive landscape',
        ],
    }

    # 解析 query 中的公司名（逗号/顿号/空格分隔）
    company_list = []
    if query and query != entity:
        import re
        parts = re.split(r'[,，、\s]+', query)
        company_list = [p.strip() for p in parts if p.strip() and p.strip() != entity]

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    scope_data = {
        "industry": entity,
        "query": query,
        "market": job_ctx.market,
        "keywords": keywords,
        "company_list": company_list,
        "defined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    scope_path = tasks_dir / f"{job_ctx.job_id}-ic_scope.json"
    scope_path.write_text(
        json.dumps(scope_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ok": True,
        "mode": "scope_definition",
        "phase": "phase0_scope_definition",
        "job_id": job_ctx.job_id,
        "result": scope_data,
    }


# ═══════════════════════════════════════════════════════════
# Phase 0.5: Multi-Company Verify — 批量公司工商验证
# ═══════════════════════════════════════════════════════════

def _run_multi_company_verify(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 0.5: 对 query 中提到的公司做批量工商验证（企查查MCP）。

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
            "phase": "phase05_multi_company_verify",
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
        "phase": "phase05_multi_company_verify",
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
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase04_presearch")
    if cached is not None:
        print(f"  📦 [ic] 使用缓存的 presearch 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase04_presearch", pipeline="ic")


def _run_industry_presearch_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """presearch 实际执行逻辑 — 使用行业专用预搜索"""
    from scripts.ic_presearch import run_ic_presearch

    result = run_ic_presearch(
        task_id=job_ctx.job_id,
        entity=job_ctx.entity,
        market=job_ctx.market,
        query=job_ctx.query or "",
    )
    return {
        "ok": True,
        "mode": "ic_presearch",
        "phase": "phase04_presearch",
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
        "phase": "phase15_extract",
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
            "phase": "phase12_precompute",
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
        "phase": "phase12_precompute",
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
    """Phase 4a: 使用 launch_next_wave 发射第一个 wave。

    sequential=True: 每次只派发 wave 内一个 step，配合 has_more 循环调用，
    避免并行 Task 子代理触发 API 429。
    """
    from scripts.ic_subagent_launcher import (
        launch_next_wave,
        get_pipeline_status,
    )

    entity = job_ctx.entity
    market = job_ctx.market

    wave_result = launch_next_wave(
        task_id=job_ctx.job_id,
        entity=entity,
        query=job_ctx.query,
        market=market,
        sequential=sequential,
    )

    if wave_result.get('all_done'):
        return {
            "ok": True,
            "needs_dispatch": False,
            "has_more": False,
            "mode": "wave_orchestration",
            "phase": "phase4_dispatch_prepare",
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
                "phase": "phase4_dispatch_prepare",
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
            "phase": "phase4_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {"error": "No steps dispatched in wave", "wave_result": wave_result},
        }

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": has_more,
        "mode": "wave_orchestration",
        "phase": "phase4_dispatch_prepare",
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
        "phase": "phase4_dispatch_collect",
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
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase5_delivery")
    if cached is not None:
        print(f"  📦 [ic] 使用缓存的 delivery 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase5_delivery", pipeline="ic")


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
        "phase": "phase5_delivery",
        "job_id": job_ctx.job_id,
        "result": {
            "verification_verdict": verification_verdict,
            "verification_summary": verification.get("summary", ""),
            "finalize": finalize_result,
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
                "phase0_scope_definition": lambda job_ctx: _run_scope_definition(runtime_root, job_ctx),
                "phase05_multi_company_verify": lambda job_ctx: _run_multi_company_verify(runtime_root, job_ctx),
                "phase04_presearch": lambda job_ctx: _run_industry_presearch(runtime_root, job_ctx),
                "phase15_extract": lambda job_ctx: _run_extract(runtime_root, job_ctx),
                "phase12_precompute": lambda job_ctx: _run_industry_precompute(runtime_root, job_ctx),
                "phase4_dispatch_prepare": lambda job_ctx: _run_dispatch_prepare(runtime_root, job_ctx, sequential=True),
                "phase4_dispatch_collect": lambda job_ctx: _run_dispatch_collect(runtime_root, job_ctx),
                "phase5_delivery": lambda job_ctx: _run_delivery(runtime_root, job_ctx),
            },
        )
        self.runtime_root = runtime_root
