#!/usr/bin/env python3
"""
BP Pipeline — 后台分步执行版

替代 run_bp.py 的一口气执行模式，每个 heavy phase 在后台独立运行，
通过 PID 文件 + 轮询实现异步等待。

用法（WorkBuddy Agent 调用）：
    # 1. 启动 phase0（前台，很快）
    python3 scripts/bp_pipeline_bg.py --job-id TASK-XXX start phase01_document_intake ...
    
    # 2. 启动 phase05（后台）
    python3 scripts/bp_pipeline_bg.py --job-id TASK-XXX start phase02_company_verify ...
    
    # 3. 轮询 phase05 是否完成
    python3 scripts/bp_pipeline_bg.py --job-id TASK-XXX poll phase02_company_verify
    
    # 4. 启动 phase1（后台）
    python3 scripts/bp_pipeline_bg.py --job-id TASK-XXX start phase04_presearch ...
    
    # 5. 轮询 + 收集
    python3 scripts/bp_pipeline_bg.py --job-id TASK-XXX poll phase04_presearch
    
    # 6. 后续 phase（dispatch/synthesis/delivery）同理

Heavy phases（后台运行）：
    phase02_company_verify   — 主体核验（大量搜索，2-5 分钟）
    phase04_presearch         — 预搜索（30-42 次搜索，3-10 分钟）
    phase33_delivery          — 交付（含对抗验证 + DOCX 生成，2-5 分钟）

Light phases（前台运行）：
    phase01_document_intake   — OCR（30 秒内）
    phase08_dispatch_prepare  — 准备 manifest（秒级）
    phase09_dispatch_collect  — 检查输出（秒级）
    phase13_wave2_prepare/collect  — Wave 2 客户收入
    phase16_wave3_prepare/collect  — Wave 3 竞争+估值
    phase20_wave4_prepare/collect — Wave 4 Deal Breaker
    phase27_synthesis_prepare/collect    — 同上
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
SCRIPTS_DIR = ROOT / "scripts"
PHASE_RUNNER = SCRIPTS_DIR / "phase_runner.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

# Heavy phases — 走 launch_heavy_phase 路径
HEAVY_PHASES = {"phase01_document_intake", "phase02_company_verify", "phase04_presearch", "phase33_delivery"}

# phase01 OCR 和 phase04 预搜索无超时，其他 phase 保留超时
_NO_TIMEOUT_PHASES = {"phase01_document_intake", "phase04_presearch"}

# 超时设置（秒）— NO_TIMEOUT_PHASES 不使用此表
PHASE_TIMEOUTS = {
    "phase02_company_verify": 600,    # 10 分钟
    "phase08_dispatch_prepare": 120,
    "phase09_dispatch_collect": 120,
    "phase16_wave3_prepare": 120,
    "phase17_wave3_collect": 120,
    "phase13_wave2_prepare": 120,
    "phase14_wave2_collect": 120,
    "phase20_wave4_prepare": 120,
    "phase21_wave4_collect": 120,
    "phase27_synthesis_prepare": 120,
    "phase28_synthesis_collect": 120,
    "phase33_delivery": 600,            # 10 分钟
}


def _python() -> str:
    return sys.executable


def start_phase(job_id: str, phase: str, entity: str = "", market: str = "cn",
                input_file: str = "", query: str = "", session_id: str = "") -> dict:
    """启动一个 phase。NO_TIMEOUT_PHASES 无超时，其他 phase 有超时。"""
    common_args = [
        "--job-id", job_id,
        "--phase", phase,
        "--entity", entity,
        "--market", market,
        "--input-file", input_file,
        "--query", query,
        "--session-id", session_id,
    ]

    cmd = [_python(), str(PHASE_RUNNER), "--run"] + common_args
    if phase in _NO_TIMEOUT_PHASES:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    else:
        timeout = PHASE_TIMEOUTS.get(phase, 300)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=timeout)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"ok": result.returncode == 0, "stdout": result.stdout[-2000:]}
    return {"ok": result.returncode == 0, "stderr": result.stderr[:500]}


def poll_phase(job_id: str, phase: str, timeout: int = 0) -> dict:
    """轮询 phase 状态。timeout=0 表示只查一次，>0 表示阻塞等待。"""
    if timeout > 0:
        cmd = [_python(), str(PHASE_RUNNER), "--wait", "--job-id", job_id, "--phase", phase,
               "--timeout", str(timeout)]
    else:
        cmd = [_python(), str(PHASE_RUNNER), "--status", "--job-id", job_id, "--phase", phase]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=timeout + 30 if timeout > 0 else None)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"status": "parse_error", "stdout": result.stdout[:500]}
    return {"status": "error", "stderr": result.stderr[:500]}


def read_result(job_id: str, phase: str) -> dict:
    """读取已完成 phase 的结果"""
    result_file = ROOT / "jobs" / job_id / "state" / f"{phase}.result.json"
    if not result_file.exists():
        return {"ok": False, "error": f"Result not found: {result_file}"}
    return json.loads(result_file.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description="BP Pipeline 后台分步执行版")
    ap.add_argument("--job-id", required=True)

    sub = ap.add_subparsers(dest="action")

    # start
    p_start = sub.add_parser("start", help="启动一个 phase")
    p_start.add_argument("phase", help="阶段名称")
    p_start.add_argument("--entity", default="")
    p_start.add_argument("--market", default="cn")
    p_start.add_argument("--input-file", default="")
    p_start.add_argument("--query", default="")
    p_start.add_argument("--session-id", default="")

    # poll
    p_poll = sub.add_parser("poll", help="轮询 phase 状态")
    p_poll.add_argument("phase", help="阶段名称")
    p_poll.add_argument("--wait", action="store_true", help="阻塞等待到完成")
    p_poll.add_argument("--timeout", type=int, default=0, help="阻塞等待秒数（0=只查一次）")

    # result
    p_result = sub.add_parser("result", help="读取 phase 结果")
    p_result.add_argument("phase", help="阶段名称")

    # status-all
    sub.add_parser("status-all", help="查看所有 phase 状态")

    args = ap.parse_args()

    if args.action == "start":
        result = start_phase(args.job_id, args.phase, args.entity, args.market,
                             args.input_file, args.query, args.session_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.action == "poll":
        result = poll_phase(args.job_id, args.phase, timeout=args.timeout if args.wait else 0)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.action == "result":
        result = read_result(args.job_id, args.phase)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.action == "status-all":
        from runtime.profiles.bp_profile import BPProfile
        profile = BPProfile(runtime_root=ROOT)
        all_status = {}
        for phase in profile.phases():
            status = poll_phase(args.job_id, phase)
            all_status[phase] = status
            icon = {"completed": "✅", "running": "🔄", "failed": "❌", "not_started": "⏳"}.get(status["status"], "❓")
            print(f"  {icon} {phase}: {status['status']}", flush=True)
        print(json.dumps(all_status, ensure_ascii=False, indent=2, default=str))

    else:
        ap.print_help()


if __name__ == "__main__":
    main()
