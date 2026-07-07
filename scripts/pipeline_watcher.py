#!/usr/bin/env python3
"""
Pipeline Watcher — 后台看门狗

sleep → 检查管线状态 → 能自动推进就推进 → 需要人工介入就写标记文件退出。

用法:
    python3 scripts/pipeline_watcher.py --job-id TASK-XXX [--interval 300]

自动推进的 phase（不需要人工介入）:
    - 所有非 needs_dispatch 的 phase（OCR、天眼查、预搜索、门禁、统稿等）

需要人工介入、停下来等 Agent 的阶段:
    - phase03_research_plan  → 需要 Agent 做 enrichment
    - phase08_dispatch_prepare → 需要 Agent 派发 Wave1 子代理
    - phase13/16/20 wave_prepare → 需要 Agent 派发后续 Wave 子代理
    - phase27_synthesis_prepare → 需要 Agent 派发统稿子代理
    - phase03_research_plan_collect → 需要 enrichment.json 已写好
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 需要 Agent 介入的暂停点
DISPATCH_PHASES = {
    "phase03_research_plan",
    "phase03_research_plan_collect",
    "phase08_dispatch_prepare",
    "phase13_wave2_prepare",
    "phase16_wave3_prepare",
    "phase20_wave4_prepare",
    "phase27_synthesis_prepare",
}

MARKER_DIR = ROOT / "jobs"


def _marker_path(job_id: str) -> Path:
    return MARKER_DIR / job_id / "state" / "pipeline_paused.ready"


def _write_marker(job_id: str, phase: str, detail: dict):
    """写暂停标记文件，Agent 巡检时看到这个就知道该介入了"""
    path = _marker_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "job_id": job_id,
        "paused_at_phase": phase,
        "paused_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "next_phase": detail.get("next_phase", ""),
        "needs": detail.get("needs", ""),
        "timestamp": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  📝 写暂停标记: {path}", flush=True)


def get_status(job_id: str) -> dict:
    """获取管线当前状态"""
    cmd = [sys.executable, str(ROOT / "runtime" / "orchestrator" / "pipeline_orchestrator.py"),
           "status", "--job-id", job_id]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"error": "parse_error", "stdout": result.stdout[:500]}
    return {"error": "no_output", "stderr": result.stderr[:500]}


def resume(job_id: str, start_phase: str) -> dict:
    """恢复管线从指定 phase 继续"""
    cmd = [sys.executable, str(ROOT / "runtime" / "orchestrator" / "pipeline_orchestrator.py"),
           "execute", "--job-id", job_id, "--start-phase", start_phase]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    print(f"  ▶ 恢复管线: {start_phase}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"ok": False, "stdout": result.stdout[-2000:]}
    return {"ok": False, "stderr": result.stderr[:500]}


def watch(job_id: str, interval: int = 300):
    """主循环：sleep → 检查 → 推进 or 暂停"""
    print(f"🐕 [watcher] 启动 — job={job_id}, 检查间隔 {interval}s", flush=True)

    # 先清掉旧的标记文件
    marker = _marker_path(job_id)
    if marker.exists():
        marker.unlink()

    while True:
        status = get_status(job_id)

        if "error" in status and "parse_error" not in status:
            print(f"  ⚠️ 获取状态失败: {status}", flush=True)
            time.sleep(interval)
            continue

        job_status = status.get("status", "unknown")
        result = status.get("result", {})
        result_status = result.get("status", "")

        print(f"  🔍 [{time.strftime('%H:%M:%S')}] job={job_status}, pipeline={result_status}", flush=True)

        if job_status == "completed":
            print(f"  ✅ 管线已完成！", flush=True)
            _write_marker(job_id, "done", {"needs": "查看交付产物"})
            return

        if job_status == "failed":
            failed_phase = result.get("failed_phase", "?")
            error = result.get("error", "?")[:200]
            print(f"  ❌ 管线失败: {failed_phase} — {error}", flush=True)
            _write_marker(job_id, failed_phase, {"needs": f"修复失败: {error}"})
            return

        if result_status == "needs_dispatch":
            paused_phase = result.get("paused_after", "?")
            next_phase = result.get("next_phase", "")

            # 判断是否需要 Agent 介入
            if paused_phase in DISPATCH_PHASES or next_phase in DISPATCH_PHASES:
                print(f"  ⏸ 需要 Agent 介入: {paused_phase} → {next_phase}", flush=True)
                _write_marker(job_id, paused_phase, {
                    "next_phase": next_phase,
                    "needs": f"Agent 派发子代理或执行 enrichment",
                    "dispatch_info": result.get("dispatch_info", {}),
                })
                return  # 退出，等 Agent 处理完重新启动 watcher

            # 不需要 Agent 介入的 dispatch（比如 has_more=true 的 sequential 派发）
            # → 自动恢复
            if next_phase:
                print(f"  🔄 自动推进: {paused_phase} → {next_phase}", flush=True)
                resume_result = resume(job_id, next_phase)
                if not resume_result.get("ok"):
                    print(f"  ⚠️ 恢复结果: {resume_result}", flush=True)
                # resume 后 status 会变，下一轮循环会重新检查
                continue
            else:
                print(f"  ⏸ 暂停但无 next_phase，等 Agent", flush=True)
                _write_marker(job_id, paused_phase, {"needs": "手动决定下一步"})
                return

        if job_status == "paused":
            # job 级别是 paused，但 result 里可能有 next_phase
            next_phase = result.get("next_phase", "")
            if next_phase and next_phase != "done":
                print(f"  🔄 job=paused，尝试自动恢复: {next_phase}", flush=True)
                resume_result = resume(job_id, next_phase)
                if not resume_result.get("ok"):
                    print(f"  ⚠️ 恢复失败，等待...", flush=True)
                    time.sleep(interval)
                continue
            else:
                paused_phase = result.get("paused_after", "?")
                print(f"  ⏸ job=paused，无 next_phase，等 Agent", flush=True)
                _write_marker(job_id, paused_phase, {"needs": "手动决定下一步"})
                return

        if job_status in ("running", "pending"):
            print(f"  ⏳ 管线正在跑... 等 {interval}s 再看", flush=True)
            time.sleep(interval)
            continue

        # 未知状态
        print(f"  ❓ 未知状态: {job_status}/{result_status}，等 {interval}s", flush=True)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="Pipeline Watcher — 后台看门狗")
    ap.add_argument("--job-id", required=True, help="任务 ID")
    ap.add_argument("--interval", type=int, default=300, help="检查间隔（秒，默认 300）")
    args = ap.parse_args()

    watch(args.job_id, args.interval)


if __name__ == "__main__":
    main()
