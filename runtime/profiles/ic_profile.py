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

    # Bug fix (2026-07-09): orchestrator 走 run_ic_job() 时 query 被丢弃。
    # 当没有 topic_file 但 job_ctx.query 含有结构化字段(核心问题/研究内容/关键子问题)，
    # 把它写成临时 .md 让 parse_topic_metadata 解析，避免 research_content 为空。
    topic_source = topic_file
    query_tmp_path: Path | None = None
    if not topic_file and job_ctx.query:
        q = job_ctx.query.strip()
        has_structured = any(kw in q for kw in ("核心问题", "研究内容", "关键子问题", "key_companies"))
        if has_structured:
            query_tmp_path = tasks_dir / f"{job_ctx.job_id}_query_as_topic.md"
            # 给 markdown 解析器一个标题行 + 原始 query
            query_tmp_path.write_text(
                f"# {entity}\n\n{q}\n", encoding="utf-8"
            )
            topic_source = str(query_tmp_path)

    result = parse_topic_metadata(
        topic_source=topic_source or entity,
        entity=entity,
        output_dir=tasks_dir,
    )

    # 清理临时文件
    if query_tmp_path and query_tmp_path.exists():
        try:
            query_tmp_path.unlink()
        except Exception:
            pass

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


# [v1.5] phase03_presearch 已删除: 子代理全权搜索, 脚本presearch无用


# [v1.5] phase03b_extract 已删除: presearch已砍无URL可提取


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

    # Read research plan to get step filter + archetype
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
            archetype = plan.get("archetype", "")
            if archetype:
                print(f"  🏷️ [ic-dispatch] archetype: {archetype} ({plan.get('archetype_reasoning', '')})", flush=True)
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
# Phase 09: Evidence Gate — step 输出质量门禁
# ═══════════════════════════════════════════════════════════

def _run_evidence_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09: 检查所有 completed step 的输出质量。

    检查维度: citations/content_length/structure。
    v1.1: FAIL → 派发修复子代理 → max 1 retry → 降级 WARN 放行。
    """
    from scripts.ic_evidence_gate import (
        run_evidence_gate,
        build_repair_manifest,
        read_repair_attempts,
        write_repair_attempt,
        _MAX_REPAIR_RETRIES,
    )
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj, step_output_path as _sop

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    step_outputs: dict[str, Path] = {}
    for step_name in step_deps:
        out = _sop(job_ctx.job_id, step_name)
        if out.exists():
            step_outputs[step_name] = out

    result = run_evidence_gate(
        task_id=job_ctx.job_id,
        step_outputs=step_outputs,
        tasks_dir=tasks_dir,
    )

    overall = result.get("overall_verdict", "WARN")

    # ── Repair 逻辑 (v1.1) ──
    if overall == "FAIL":
        repair_attempt = read_repair_attempts(job_ctx.job_id, tasks_dir)

        if repair_attempt < _MAX_REPAIR_RETRIES:
            # 取第一个 FAIL step 做修复
            failed_steps = [
                sn for sn, sr in result.get("per_step", {}).items()
                if sr.get("verdict") == "FAIL"
            ]
            if not failed_steps:
                return {
                    "ok": False,
                    "mode": "ic_evidence_gate",
                    "phase": "phase09_evidence_gate",
                    "job_id": job_ctx.job_id,
                    "result": result,
                }

            first_fail = failed_steps[0]
            fail_path = _sop(job_ctx.job_id, first_fail)
            fail_reasons = [
                i.get("detail", i.get("type", ""))
                for i in result.get("per_step", {}).get(first_fail, {}).get("issues", [])
            ]

            manifest = build_repair_manifest(
                task_id=job_ctx.job_id,
                failed_step=first_fail,
                step_output_path=fail_path,
                failure_reasons=fail_reasons,
                tasks_dir=tasks_dir,
            )
            write_repair_attempt(job_ctx.job_id, tasks_dir, repair_attempt + 1, failed_steps)

            has_more = len(failed_steps) > 1
            instruction = (
                f"## IC Evidence Gate Repair — {first_fail}\n\n"
                f"MANDATORY: Read the repair manifest at:\n"
                f"  {manifest.get('manifest_path', '')}\n\n"
                f"Use the Agent tool with:\n"
                f"  - name = 'ic-repair-{first_fail}'\n"
                f"  - team_name = 'ic-{{task_id}}'\n"
                f"  - mode = 'bypassPermissions'\n"
                f"  - prompt = manifest's 'system_prompt' field (COMPLETE)\n"
                f"  - connectorIds = ['westock-mcp', 'tyc-mcp']\n\n"
                f"子代理修复完成后，用 start_phase='phase09_evidence_gate' 恢复管线。\n"
                + (f"\n⚠️ has_more=True: 还有 {len(failed_steps)-1} 个 step 待修复，恢复后会返回下一个 manifest。\n"
                   if has_more else "\n✅ 这是最后一个待修复 step，恢复后推进到 fact_store_merge。\n")
                + "\n## ⚠️ 绝对禁止:\n"
                  "- 禁止并行派发多个 repair 子代理\n"
                  "- 禁止在 repair 完成前推进管线\n"
            )

            print(f"  🔧 [ic] evidence gate REPAIR #{repair_attempt + 1}: {first_fail}", flush=True)

            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": has_more,
                "mode": "ic_evidence_gate_repair",
                "phase": "phase09_evidence_gate",
                "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [manifest.get("manifest_path", "")],
                    "remaining_manifests": [],
                    "is_repair": True,
                },
                "result": result,
                "instruction": instruction,
            }

        else:
            # Repair 次数耗尽 → 降级 WARN 放行
            print(f"  ⚠️ [ic] evidence gate repair exhausted ({repair_attempt}/{_MAX_REPAIR_RETRIES}), "
                  f"降级为 WARN 放行", flush=True)
            result["overall_verdict"] = "WARN"
            result["repair_exhausted"] = True
            overall = "WARN"

    print(f"  🔍 [ic] evidence gate: {overall}, "
          f"pass={result.get('summary', {}).get('pass', 0)}, "
          f"warn={result.get('summary', {}).get('warn', 0)}, "
          f"fail={result.get('summary', {}).get('fail', 0)}", flush=True)

    # IC evidence gate: FAIL → ok=False (but repair handles this), WARN → ok=True
    return {
        "ok": overall != "FAIL",
        "mode": "ic_evidence_gate",
        "phase": "phase09_evidence_gate",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 08b: Fact Store Init — IC 事实库初始化
# ═══════════════════════════════════════════════════════════

def _run_fact_store_init(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 08b: 初始化 IC fact_store + 共享状态。

    在 dispatch_collect 完成、子代理都已产出后，初始化结构化事实库。
    后续 evidence_gate 和 claim_coverage 可以使用 fact_store 做交叉验证。
    """
    import time as _time

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 读取 research plan 获取 claim 列表
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"
    claims: list[dict[str, Any]] = []
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            claims = plan.get("claim_matrix", [])
        except Exception:
            pass

    # 读取 wave manifest 获取 step 列表
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj
    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    # 初始化 fact_store
    fact_store = {
        "schema_version": "ic_fact_store.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "facts": [],
        "step_outputs": list(step_deps.keys()),
        "claim_count": len(claims),
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fs_path = tasks_dir / f"{job_ctx.job_id}-ic_fact_store.json"
    fs_path.write_text(json.dumps(fact_store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _sync_step_to_workspace(job_ctx, "ic_fact_store", fs_path)

    print(f"  📦 [ic] fact_store 初始化: {len(step_deps)} steps, {len(claims)} claims", flush=True)

    return {
        "ok": True,
        "mode": "fact_store_init",
        "phase": "phase08b_fact_store_init",
        "job_id": job_ctx.job_id,
        "result": {
            "step_count": len(step_deps),
            "claim_count": len(claims),
            "fact_store_path": str(fs_path),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 08b5: Shared State Init — IC 跨 Wave 共享状态
# ═══════════════════════════════════════════════════════════

def _run_shared_state_init(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 08b5: 初始化 IC 跨 Wave 共享状态。

    在 fact_store_init 之后创建 ic_shared_state.json，
    记录管线进度、claim 状态、开放问题和冲突，
    供各 wave 子代理读取上下文。
    """
    import time as _time

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 读取 research plan 获取 claim 列表
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"
    claim_status: dict[str, str] = {}
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            for claim in plan.get("claim_matrix", []):
                cid = claim.get("claim_id", "")
                if cid:
                    claim_status[cid] = "planned"
        except Exception:
            pass

    # 读取 wave manifest 获取当前进度
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj
    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}
    current_wave = wm.get("current_wave", 1) if wm else 1

    shared_state = {
        "schema_version": "ic_shared_state.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "wave_progress": current_wave,
        "total_waves": wm.get("total_waves", 8) if wm else 8,
        "claim_status": claim_status,
        "claim_count": len(claim_status),
        "completed_steps": list(step_deps.keys()),
        "open_questions": [],
        "evidence_conflicts": [],
        "key_findings": {},
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    ss_path = tasks_dir / f"{job_ctx.job_id}-ic_shared_state.json"
    ss_path.write_text(json.dumps(shared_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _sync_step_to_workspace(job_ctx, "ic_shared_state", ss_path)

    print(f"  📦 [ic] shared_state 初始化: wave={current_wave}, "
          f"claims={len(claim_status)}, steps={len(step_deps)}", flush=True)

    return {
        "ok": True,
        "mode": "shared_state_init",
        "phase": "phase08b5_shared_state_init",
        "job_id": job_ctx.job_id,
        "result": {
            "wave_progress": current_wave,
            "claim_count": len(claim_status),
            "step_count": len(step_deps),
            "shared_state_path": str(ss_path),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 09b: Fact Store Merge — 从 step 输出汇总事实
# ═══════════════════════════════════════════════════════════

def _run_fact_store_merge(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09b: 扫描所有 step 输出，提取关键事实到 fact_store。

    在 evidence_gate 之后运行，基于已验证的 step 输出做结构化提取。
    """
    import time as _time
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj, step_output_path as _sop

    tasks_dir = runtime_root / "data" / "tasks"
    fs_path = tasks_dir / f"{job_ctx.job_id}-ic_fact_store.json"

    # 加载已有 fact_store
    existing_facts: list[dict[str, Any]] = []
    if fs_path.exists():
        try:
            existing = json.loads(fs_path.read_text(encoding="utf-8"))
            existing_facts = existing.get("facts", [])
        except Exception:
            pass

    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    new_facts: list[dict[str, Any]] = []
    merged_count = 0

    for step_name in step_deps:
        output_path = _sop(job_ctx.job_id, step_name)
        if not output_path.exists():
            continue
        try:
            text = output_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if len(text) < 100:
            continue

        # 简单提取：每个 step 的前 200 字摘要 + 步名作为 fact
        summary = text[:200].replace("\n", " ").strip()
        if summary:
            fact_id = f"IC-{job_ctx.job_id[-8:]}-{step_name}"
            # 避免重复
            if not any(f.get("fact_id") == fact_id for f in existing_facts):
                new_facts.append({
                    "fact_id": fact_id,
                    "step": step_name,
                    "summary": summary,
                    "source": "step_output",
                    "extracted_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                merged_count += 1

    all_facts = existing_facts + new_facts
    fact_store = {
        "schema_version": "ic_fact_store.v1",
        "job_id": job_ctx.job_id,
        "entity": job_ctx.entity,
        "facts": all_facts,
        "fact_count": len(all_facts),
        "merged_count": merged_count,
        "updated_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fs_path.write_text(json.dumps(fact_store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _sync_step_to_workspace(job_ctx, "ic_fact_store", fs_path)

    print(f"  📦 [ic] fact_store 合并: +{merged_count} facts, 总计 {len(all_facts)}", flush=True)

    return {
        "ok": True,
        "mode": "fact_store_merge",
        "phase": "phase09b_fact_store_merge",
        "job_id": job_ctx.job_id,
        "result": {
            "total_facts": len(all_facts),
            "new_facts": merged_count,
            "fact_store_path": str(fs_path),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 10: Claim Coverage — 研究计划 claim 覆盖校验
# ═══════════════════════════════════════════════════════════

def _run_claim_coverage(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 10: 校验 ic_research_plan.json claim_matrix 中各 claim 是否被 step 输出覆盖。"""
    from scripts.ic_claim_coverage import run_claim_coverage
    from scripts.ic_subagent_launcher import wave_manifest_path, load_json, step_output_path

    tasks_dir = runtime_root / "data" / "tasks"
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"

    wm = load_json(wave_manifest_path(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    step_outputs: dict[str, Path] = {}
    for step_name in step_deps:
        out = step_output_path(job_ctx.job_id, step_name)
        if out.exists():
            step_outputs[step_name] = out

    result = run_claim_coverage(
        task_id=job_ctx.job_id,
        research_plan_path=plan_path,
        step_outputs=step_outputs,
        tasks_dir=tasks_dir,
    )

    overall = result.get("overall_verdict", "WARN")
    # Claim coverage FAIL is not blocking for IC (P2 quality gate, not blocking yet)
    return {
        "ok": True,
        "mode": "ic_claim_coverage",
        "phase": "phase10_claim_coverage",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 11: Debate Review — 跨维度对抗审查
# ═══════════════════════════════════════════════════════════

def _run_debate_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11: 跨 step 对抗审查 — 检测矛盾、数据不一致、缺失视角。"""
    from scripts.ic_debate_review import run_debate_review
    from scripts.ic_subagent_launcher import wave_manifest_path, load_json, step_output_path

    tasks_dir = runtime_root / "data" / "tasks"
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"

    wm = load_json(wave_manifest_path(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    step_outputs: dict[str, Path] = {}
    for step_name in step_deps:
        out = step_output_path(job_ctx.job_id, step_name)
        if out.exists():
            step_outputs[step_name] = out

    result = run_debate_review(
        task_id=job_ctx.job_id,
        step_outputs=step_outputs,
        research_plan_path=plan_path,
        tasks_dir=tasks_dir,
    )

    overall = result.get("overall_verdict", "WARN")
    return {
        "ok": overall != "FAIL",
        "mode": "ic_debate_review",
        "phase": "phase11_debate_review",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 10b: Cross-Dimension Gate — 跨维度一致性
# ═══════════════════════════════════════════════════════════

def _run_cross_dimension_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 10b: 跨维度一致性门禁。

    检查不同 step 之间的市场规模、增速、CRn 一致性。
    FAIL → WARN 放行，记录到 deferred_fixes。
    """
    from scripts.ic_cross_dimension_gate import run_cross_dimension_gate
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj, step_output_path as _sop

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    step_outputs: dict[str, Path] = {}
    for step_name in step_deps:
        out = _sop(job_ctx.job_id, step_name)
        if out.exists():
            step_outputs[step_name] = out

    result = run_cross_dimension_gate(
        task_id=job_ctx.job_id,
        step_outputs=step_outputs,
        tasks_dir=tasks_dir,
    )

    overall = result.get("overall_verdict", "WARN")
    print(f"  🔍 [ic] 跨维度一致性: {overall}, issues={len(result.get('issues', []))}", flush=True)

    # IC 跨维度门禁：FAIL → WARN 放行（非阻断）
    return {
        "ok": True,
        "mode": "ic_cross_dimension_gate",
        "phase": "phase10b_cross_dimension_gate",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 11b: Final Assembly — 最终组装
# ═══════════════════════════════════════════════════════════

def _run_final_assembly(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11b: 将所有 step 输出组装为统一报告。

    按产业链顺序编排 step 输出，统一标题层级，添加目录。
    """
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj, step_output_path as _sop

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    wm = _lj(_wmp(job_ctx.job_id))
    step_deps = wm.get("step_deps", {}) if wm else {}

    # 按 wave 顺序收集 step 输出
    wave_order = wm.get("wave_order", []) if wm else []
    assembled: list[str] = [
        f"# {job_ctx.entity} — 行业深度研究报告",
        "",
        f"*自动生成 | 日期: {__import__('time').strftime('%Y-%m-%d')}*",
        "",
        "---",
        "",
    ]

    step_count = 0
    for wave_key in wave_order:
        wave_steps = wm.get(wave_key, [])
        for step_name in wave_steps:
            out = _sop(job_ctx.job_id, step_name)
            if out.exists():
                try:
                    text = out.read_text(encoding="utf-8")
                except Exception:
                    continue
                if len(text) < 50:
                    continue
                assembled.append(text)
                assembled.append("")
                assembled.append("---")
                assembled.append("")
                step_count += 1

    if step_count == 0:
        # 回退: 遍历 step_deps
        for step_name in step_deps:
            out = _sop(job_ctx.job_id, step_name)
            if out.exists():
                try:
                    text = out.read_text(encoding="utf-8")
                except Exception:
                    continue
                if len(text) < 50:
                    continue
                assembled.append(text)
                assembled.append("")
                assembled.append("---")
                assembled.append("")
                step_count += 1

    assembled.append("")
    assembled.append("---")
    assembled.append("")
    assembled.append("*本报告由 IC 管线自动生成。*")

    report_path = tasks_dir / f"{job_ctx.job_id}-ic_final_report.md"
    report_text = "\n".join(assembled)
    report_path.write_text(report_text, encoding="utf-8")

    _sync_step_to_workspace(job_ctx, "ic_final_report", report_path)

    print(f"  📝 [ic] 最终组装: {step_count} steps → {len(report_text)} chars", flush=True)

    return {
        "ok": step_count > 0,
        "mode": "final_assembly",
        "phase": "phase11b_final_assembly",
        "job_id": job_ctx.job_id,
        "result": {
            "step_count": step_count,
            "total_length": len(report_text),
            "report_path": str(report_path),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 11c: Readability Review — 可读性审查
# ═══════════════════════════════════════════════════════════

def _run_readability_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11c: 可读性审查 — 字数/结构/引用密度/数据多样性。

    FAIL → WARN 放行，记录到 deferred_fixes。
    """
    from scripts.ic_readability_reviewer import run_readability_review
    from scripts.ic_subagent_launcher import wave_manifest_path as _wmp, load_json as _lj, step_output_path as _sop

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 收集待审查文件
    report_paths: list[Path] = []
    final_path = tasks_dir / f"{job_ctx.job_id}-ic_final_report.md"
    if final_path.exists():
        report_paths.append(final_path)

    # 若 final_report 不存在，回退到所有 step 输出
    if not report_paths:
        wm = _lj(_wmp(job_ctx.job_id))
        step_deps = wm.get("step_deps", {}) if wm else {}
        for step_name in step_deps:
            out = _sop(job_ctx.job_id, step_name)
            if out.exists():
                report_paths.append(out)

    result = run_readability_review(
        task_id=job_ctx.job_id,
        report_paths=report_paths,
        tasks_dir=tasks_dir,
    )

    overall = result.get("overall_verdict", "WARN")
    print(f"  📖 [ic] 可读性: {overall}, length={result.get('total_length', 0)}, "
          f"issues={len(result.get('issues', []))}", flush=True)

    # IC 可读性审查：FAIL → WARN 放行
    return {
        "ok": True,
        "mode": "ic_readability_review",
        "phase": "phase11c_readability_review",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 11d: Investment Judgment — 投资判断汇总
# ═══════════════════════════════════════════════════════════

def _run_investment_judgment(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 11d: 行业投资判断汇总。

    扫描所有 step 输出，提取结论、置信度、风险，输出超配/标配/低配建议。
    """
    from scripts.ic_investment_judgment import build_ic_investment_judgment

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    result = build_ic_investment_judgment(
        job_id=job_ctx.job_id,
        tasks_dir=tasks_dir,
    )

    recommendation = result.get("recommendation", "")
    print(f"  💡 [ic] 投资判断: {recommendation}, "
          f"dims={result.get('dimension_count', 0)}", flush=True)

    # 同步到 workspace
    json_p = tasks_dir / f"{job_ctx.job_id}-ic_investment_judgment.json"
    md_p = tasks_dir / f"{job_ctx.job_id}-ic_investment_judgment.md"
    _sync_step_to_workspace(job_ctx, "ic_investment_judgment", json_p)
    _sync_step_to_workspace(job_ctx, "ic_investment_judgment_md", md_p)

    return {
        "ok": True,
        "mode": "ic_investment_judgment",
        "phase": "phase11d_investment_judgment",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 12: Delivery
# ═══════════════════════════════════════════════════════════

def _run_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 12: 对抗验证 + DOCX + 交付"""
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_delivery_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase12_delivery")
    if cached is not None:
        print(f"  📦 [ic] 使用缓存的 delivery 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase12_delivery", pipeline="ic")


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
        "phase": "phase12_delivery",
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
    """Phase 04: IC 研究计划 — 子代理派发模式 (v5.2)。

    v5.2 (2026-07-08): 从主 AI 手动 enrichment 重构为子代理派发。
    子代理有 MCP 工具可搜 westock-mcp/tyc-mcp 的结构化行业数据。
    脚本不再生成骨架——子代理全权决定研究计划的内容和结构。
    """
    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 读取课题元数据
    topic_metadata: dict[str, Any] = {}
    topic_path = tasks_dir / "ic_topic_metadata.json"
    if topic_path.exists():
        try:
            topic_metadata = json.loads(topic_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    entity = job_ctx.entity or topic_metadata.get("entity", "")
    core_q = topic_metadata.get("core_question", "")
    sub_qs = topic_metadata.get("sub_questions", [])
    research_content = topic_metadata.get("research_content", [])
    companies = topic_metadata.get("key_companies", [])
    year = time.strftime("%Y")

    # 写 brief 文件（v1.4: 不再含 presearch 引用，子代理全权搜索）
    brief_path = tasks_dir / f"{job_ctx.job_id}-ic_phase04_brief.json"
    brief = {
        "entity": entity, "core_question": core_q,
        "sub_questions": sub_qs if isinstance(sub_qs, list) else [],
        "research_content": research_content if isinstance(research_content, list) else [],
        "key_companies": companies if isinstance(companies, list) else [],
        "metadata_path": str(topic_path),
    }
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    instruction = f"""\
PHASE04 IC RESEARCH PLAN — 派发子代理

Agent tool 参数：
- name = 'ic-research-planner'
- team_name = 'ic-{job_ctx.job_id}'
- mode = 'bypassPermissions'
- connectorIds = ['westock-mcp', 'tyc-mcp']
- prompt = 下面的完整 prompt

### 子代理 Prompt:

你是行业研究分析师。基于课题元数据为 {entity} 生成完整的研究计划。

## Step 0: 读取输入文件（必须先做）

搜索之前必须先读 brief 文件(`*_ic_phase04_brief.json`), 从中提取:
- core_question(核心课题), sub_questions(子问题列表), key_companies(关键公司列表)
- research_content(研究内容列表) — 定义了课题的具体研究方向和维度，research plan 的 research_dimensions 必须 1:1 覆盖其中每一项
- 你是唯一的数据搜索者，没有上游 presearch 帮你做初步搜索，所有数据源都需要你自己查

## 搜索策略（按顺序执行）

### Step 1: 行业数据 (westock-mcp)
- westock-mcp.data_sector: 查课题对应行业板块 -> PE分位、成分股、涨跌幅
- westock-mcp.data_report: 搜行业研报, 用课题核心问题中的关键词 -> 市场规模、增长趋势、机构观点
- westock-mcp.data_finance: 查行业财务基准 -> ROE/毛利率中枢、营收增速均值

### Step 2: 关键公司验证 (tyc-mcp)
- 从 brief 中读取 key_companies 列表, 逐个搜索
- 对每个公司: tyc-mcp.search_companies -> get_company_basic_profile
- 记录: 注册资本、成立日期、经营范围、股东、融资历史、与课题的相关性

### Step 3: Web 全量搜索（结构化源优先，web_search 兜底，中英双语）

你是唯一的搜索者（没有上游 presearch 预搜索），请系统性地完成以下搜索:
- 结构化源优先: westock-mcp / 天眼查 已有数据优先使用
- web_search 作为兜底和补充，最多 10 轮:

**A. 行业宏观搜索（必做）:**
- web_search: "{entity} 行业 市场规模 TAM SAM SOM {year}"
- web_search: "{entity} industry market size growth forecast {year}"
- web_search: "{entity} 产业政策 监管 补贴 {year}"
- web_search: "{entity} industry policy regulation subsidy {year}"

**B. 竞争格局搜索（必做）:**
- web_search: "{entity} 竞争格局 CR3 CR5 市场份额 TOP"
- web_search: "{entity} competitive landscape market share ranking"
- web_search: "{entity} 龙头 排名 对比 优劣势"

**C. 产业链搜索（必做）:**
- web_search: "{entity} 产业链 上游 中游 下游 环节"
- web_search: "{entity} supply chain value chain segments"
- web_search: "{entity} 国产替代 自主可控 卡脖子"

**D. 研究内容定向搜索（必做）:**
- 对 brief 中 `research_content` 的每一项，生成 1-2 条搜索词:
  - 例: "GPU/ASIC/NPU/FPGA各品类定位" → web_search: "AI芯片 GPU ASIC NPU 品类 定位 用途"
  - 例: "各环节全球市场份额与龙头公司" → web_search: "AI芯片产业链 各环节 市场份额 龙头公司"

**E. 子问题搜索（必做）:**
- 对 brief 中每个 `sub_question` 转化为 1-2 条搜索词（去掉问号, 保留核心名词）

**F. 关键公司搜索（必做）:**
- 对 brief 中每个 `key_company` → web_search: "{{company}} {entity} 业务 市场 融资 {year}"

**G. 新闻搜索（必做，Bash 调用腾讯新闻）:**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
result = tencent_news_search('{entity} 最新动态 政策 行业事件', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```
对每个 key_company 也用同样方式搜最新动态（替换查询词为公司名）。

**H. 搜索质量控制:**
- 每条搜索结果记录来源类型（westock/tyc/web/tencent_news）
- 如果某个维度搜索结果少于 3 条有效证据，主动追加搜索
- 所有搜索关键词必须中英双语覆盖

## 任务一：判定 Archetype（最高优先级）

基于课题特征判定研究原型。这是自然语言判断，不是关键词匹配：

| 原型 | 判定信号 | 举例 |
|------|---------|------|
| early_theme | 实验室/概念阶段，商业化>5年，财务数据极度稀缺 | 可控核聚变、氢储运 |
| company_deep | 围绕1-2家公司，核心问题"这家公司怎么样" | 思摩尔、传思生物 |
| commercial_mode | 核心问题是"怎么赚钱"，聚焦变现逻辑 | 算力租赁、AI Agent商业化 |
| tech_compare | 对比2+条技术路线，回答"哪条更可能胜出" | GPU vs ASIC、电解槽路线 |
| chain_scan | 以上都不符合，产业链有清晰环节分解 | AI芯片产业链、绿氢产业链 |

如果不确定，默认选 chain_scan。

## 分析任务

生成研究计划写入 `{tasks_dir / f'{job_ctx.job_id}-ic_research_plan.json'}`：

```json
{{
  "schema_version": "ic_research_plan.v5",
  "task_id": "{job_ctx.job_id}", "entity": "{entity}",
  "archetype": "chain_scan|tech_compare|company_deep|early_theme|commercial_mode",
  "archetype_reasoning": "一句话说明为什么选这个原型",
  "data_sources_used": ["westock-mcp:行业数据/研报", "tyc-mcp:公司验证", "web_search:公开信息", "tencent_news:实时动态"],
  "research_dimensions": [
    {{"dimension": "行业规模与增长", "key_questions": [...], "data_sources": [...], "priority": "high"}}
  ],
  "claim_matrix": [
    {{"claim_id": "IC001", "claim": "...", "dimension": "...", "priority": "high", "status": "planned"}}
  ],
  "company_profiles": [{{"company": "...", "tyc_verified": true, "relevance": "..."}}],
  "search_keywords": {{"en": [...], "zh": [...]}},
  "plan_status": "ready"
}}
```

⚠️ archetype 字段必填，这是管线编排的核心依据。

维度至少覆盖6个: 行业规模与增长、竞争格局、政策与监管、技术趋势、产业链分析、关键公司画像、财务基准对标、风险与催化剂。每个维度须包含 key_questions/data_sources/priority。

## 研究内容覆盖（硬约束）

brief 中的 `research_content` 列表定义了课题的具体研究方向。你的 `research_dimensions` 必须为 `research_content` 中的每一项设立对应的维度。
例如: 若 research_content 包含 "GPU/ASIC/NPU/FPGA各品类定位与用途"，则必须有一个维度专门分析芯片品类。
`claim_matrix` 也必须围绕 research_content 设计 claim，确保每个研究方向都有对应的验证 claim。
"""

    return {
        "ok": True, "needs_dispatch": True, "has_more": False,
        "mode": "ic_research_plan_subagent",
        "phase": "phase04_research_plan", "job_id": job_ctx.job_id,
        "dispatch_info": {
            "brief_path": str(brief_path),
            "subagent_connector_ids": ["westock-mcp", "tyc-mcp"],
            "task_dir": str(tasks_dir),
        },
        "instruction": instruction,
    }


def _run_research_plan_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase04 collect v5.2: 读取子代理产出的 ic_research_plan.json。"""
    tasks_dir = runtime_root / "data" / "tasks"
    plan_path = tasks_dir / f"{job_ctx.job_id}-ic_research_plan.json"

    if plan_path.exists() and plan_path.stat().st_size > 200:
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            # 基本校验
            has_dimensions = len(plan.get("research_dimensions", [])) > 0
            has_claims = len(plan.get("claim_matrix", [])) > 0
            archetype = plan.get("archetype", "")
            if has_dimensions and has_claims:
                # archetype 校验：缺失则降级为 chain_scan
                if not archetype or archetype not in ("chain_scan", "tech_compare", "company_deep", "early_theme", "commercial_mode"):
                    plan["archetype"] = "chain_scan"
                    plan["archetype_reasoning"] = "collect 阶段未检测到有效 archetype，降级为默认"
                    print(f"  ⚠️ [ic phase04_collect] archetype 缺失或无效，降级为 chain_scan", flush=True)
                else:
                    print(f"  🏷️ [ic phase04_collect] archetype: {archetype}", flush=True)
                plan["plan_status"] = "ready"
                plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return {
                    "ok": True, "mode": "ic_research_plan",
                    "phase": "phase04_research_plan_collect", "job_id": job_ctx.job_id,
                    "result": {"plan_path": str(plan_path), "enrichment": "subagent_generated",
                                "archetype": plan.get("archetype", "chain_scan"),
                                "dimensions": len(plan.get("research_dimensions", [])),
                                "claims": len(plan.get("claim_matrix", []))},
                }
            print(f"  ⚠️ [ic phase04_collect] plan 内容不完整", flush=True)
            return {"ok": False, "mode": "ic_research_plan", "phase": "phase04_research_plan_collect",
                    "job_id": job_ctx.job_id, "result": {"error": "plan_incomplete"}}
        except Exception as exc:
            print(f"  ⚠️ [ic phase04_collect] 读取子代理 plan 失败: {exc}", flush=True)

    # 降级
    print(f"  ⚠️ [ic phase04_collect] 子代理未产出 plan，使用空骨架降级", flush=True)
    from scripts.ic_research_planner import build_empty_skeleton
    topic_path = tasks_dir / "ic_topic_metadata.json"
    fallback_topic_meta: dict[str, Any] = {}
    if topic_path.exists():
        try:
            fallback_topic_meta = json.loads(topic_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    skeleton = build_empty_skeleton(task_id=job_ctx.job_id, entity=job_ctx.entity, topic_metadata=fallback_topic_meta)
    plan_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True, "mode": "ic_research_plan",
        "phase": "phase04_research_plan_collect", "job_id": job_ctx.job_id,
        "result": {"plan_path": str(plan_path), "enrichment": "fallback_script"},
    }


# ═══════════════════════════════════════════════════════════
# IC Profile
# ═══════════════════════════════════════════════════════════

class ICProfile(PipelineProfile):
    """IC (Industry Coverage) 行业研究管线 Profile — 20 Phase (v1.3).

    核心 phase 链: topic_intake → verify → presearch → research_plan → extract
    → precompute → dispatch → collect → fact_store → shared_state → evidence_gate
    → fact_store_merge → claim_coverage → cross_dimension_gate → debate_review
    → final_assembly → readability_review → investment_judgment → delivery

    Stage Tier:
      deep (默认): 全量 20 phase
      standard: 18 phase (跳过 readability_review + investment_judgment)
      quick: 13 phase (跳过 evidence_gate + claim_coverage + cross_dimension_gate
              + debate_review + final_assembly + readability + investment_judgment)
    """
    def __init__(self, runtime_root: Path, research_tier: str = "deep"):
        # ── Stage Tier 分类 ──
        # quick: 跳过 evidence_gate, claim_coverage, cross_dimension_gate,
        #        debate_review, final_assembly, readability_review, investment_judgment
        # standard: 跳过 readability_review, investment_judgment
        # deep: 全量 18 phase
        _QUICK_SKIP = frozenset({
            "phase09_evidence_gate", "phase10_claim_coverage",
            "phase10b_cross_dimension_gate", "phase11_debate_review",
            "phase11b_final_assembly", "phase11c_readability_review",
            "phase11d_investment_judgment",
        })
        _STANDARD_SKIP = frozenset({
            "phase11c_readability_review", "phase11d_investment_judgment",
        })

        skip_phases: set[str] = set()
        effective_tier = research_tier.strip().lower()
        if effective_tier == "quick":
            skip_phases = set(_QUICK_SKIP)
        elif effective_tier == "standard":
            skip_phases = set(_STANDARD_SKIP)

        if skip_phases:
            print(f"  🎯 [ic] Stage Tier: {effective_tier}, 跳过 {len(skip_phases)} phase(s): "
                  f"{sorted(skip_phases)}", flush=True)

        all_handlers = {
            "phase01_topic_intake": lambda job_ctx: _run_topic_intake(runtime_root, job_ctx),
            "phase02_multi_company_verify": lambda job_ctx: _run_multi_company_verify(runtime_root, job_ctx),
            # [v1.5] phase03_presearch + phase03b_extract 已删除: 子代理全权搜索
            "phase04_research_plan": lambda job_ctx: _run_research_plan(runtime_root, job_ctx),
            "phase04_research_plan_collect": lambda job_ctx: _run_research_plan_collect(runtime_root, job_ctx),
            "phase06_precompute": lambda job_ctx: _run_industry_precompute(runtime_root, job_ctx),
            "phase07_dispatch_prepare": lambda job_ctx: _run_dispatch_prepare(runtime_root, job_ctx, sequential=True),
            "phase08_dispatch_collect": lambda job_ctx: _run_dispatch_collect(runtime_root, job_ctx),
            # ── v1.1 新增 ──
            "phase08b_fact_store_init": lambda job_ctx: _run_fact_store_init(runtime_root, job_ctx),
            "phase08b5_shared_state_init": lambda job_ctx: _run_shared_state_init(runtime_root, job_ctx),
            "phase09_evidence_gate": lambda job_ctx: _run_evidence_gate(runtime_root, job_ctx),
            "phase09b_fact_store_merge": lambda job_ctx: _run_fact_store_merge(runtime_root, job_ctx),
            "phase10_claim_coverage": lambda job_ctx: _run_claim_coverage(runtime_root, job_ctx),
            "phase10b_cross_dimension_gate": lambda job_ctx: _run_cross_dimension_gate(runtime_root, job_ctx),
            "phase11_debate_review": lambda job_ctx: _run_debate_review(runtime_root, job_ctx),
            "phase11b_final_assembly": lambda job_ctx: _run_final_assembly(runtime_root, job_ctx),
            "phase11c_readability_review": lambda job_ctx: _run_readability_review(runtime_root, job_ctx),
            "phase11d_investment_judgment": lambda job_ctx: _run_investment_judgment(runtime_root, job_ctx),
            "phase12_delivery": lambda job_ctx: _run_delivery(runtime_root, job_ctx),
        }

        # 按 tier 过滤
        active_handlers = {
            ph: handler for ph, handler in all_handlers.items()
            if ph not in skip_phases
        }

        super().__init__(
            name="ic",
            job_type="industry_coverage",
            phase_handlers=active_handlers,
        )
        self.runtime_root = runtime_root
        self.research_tier = effective_tier
        self._active_phases = set(active_handlers.keys())

    def phase_prerequisites(self) -> dict[str, list[str]]:
        """声明 phase 间的关键产物依赖（对标 BP/IR profile）。

        kernel 在 start_phase 跳过前置 phase 时，自动回填缺失产物。
        """
        return {
            "phase04_research_plan": ["ic_topic_metadata.json"],
            "phase06_precompute": ["{task_id}-ic_research_plan.json"],
            "phase07_dispatch_prepare": ["{task_id}-ic_research_plan.json"],
            "phase09_evidence_gate": ["{task_id}-ic_research_plan.json"],
            "phase10_claim_coverage": ["{task_id}-ic_research_plan.json"],
            "phase10b_cross_dimension_gate": ["{task_id}-ic_research_plan.json"],
        }

    def phase_outputs(self) -> dict[str, list[str]]:
        """声明每个 phase 产出的关键文件（相对 workspace.root）。

        kernel 用它构建反查表 file → producer_phase，
        在依赖缺失时精准回填到产出该文件的 phase，而非盲选前置。
        """
        return {
            "phase01_topic_intake": ["ic_topic_metadata.json"],
            "phase02_multi_company_verify": [],
            "phase04_research_plan": [],  # 子代理直接生成 plan
            "phase04_research_plan_collect": ["{task_id}-ic_research_plan.json"],
            "phase06_precompute": [],
            "phase07_dispatch_prepare": [],
            "phase08_dispatch_collect": [],
            "phase08b_fact_store_init": ["{task_id}-fact_store.json"],
            "phase08b5_shared_state_init": ["{task_id}-shared_state.json"],
            "phase09_evidence_gate": ["{task_id}-ic_evidence_gate.json"],
            "phase09b_fact_store_merge": ["{task_id}-fact_store.json"],
            "phase10_claim_coverage": ["{task_id}-ic_claim_coverage.json"],
            "phase10b_cross_dimension_gate": ["{task_id}-ic_cross_dimension_gate.json"],
            "phase11_debate_review": ["{task_id}-debate_review.json"],
            "phase11b_final_assembly": ["{task_id}-final_report.md"],
            "phase11c_readability_review": ["{task_id}-ic_readability_review.json"],
            "phase11d_investment_judgment": ["{task_id}-ic_investment_judgment.json"],
            "phase12_delivery": [],
        }
