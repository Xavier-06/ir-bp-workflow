#!/usr/bin/env python3
"""
Phase Runner — 后台独立执行单个管线阶段

用法：
    # 后台运行（写 PID 文件 + state 文件）
    python3 scripts/phase_runner.py --job-id TASK-XXX --phase phase02_company_verify --entity "某某科技" &
    
    # 轮询检查
    python3 scripts/phase_runner.py --status --job-id TASK-XXX --phase phase02_company_verify
    
    # 等待完成（阻塞）
    python3 scripts/phase_runner.py --wait --job-id TASK-XXX --phase phase02_company_verify

输出文件：
    jobs/{job_id}/state/{phase}.result.json  — 执行结果
    jobs/{job_id}/state/{phase}.pid          — 进程 PID（运行中）
    jobs/{job_id}/state/{phase}.error        — 错误信息（失败时）

支持的 phase：
    phase01_document_intake   — 文档 OCR + 结构化
    phase02_company_intake   — 公司名搜索入库（无 PDF 模式，needs_dispatch）
    phase02_company_verify   — 主体核验
    phase04_research_plan     — 研究计划派发
    phase08_dispatch_prepare  — 子代理派发准备（Wave 1）
    phase09_dispatch_collect  — 子代理输出收集（Wave 1）
    phase14_wave3_prepare/collect — Wave 3 竞争+估值
    phase18_wave4_prepare/collect — Wave 4 Deal Breaker
    phase25_synthesis_prepare — 统稿派发
    phase26_synthesis_collect — 统稿收集
    phase31_delivery          — 交付
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("SSL_CERT_FILE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ["IRBP_BG_CHILD"] = "1"  # 标记：当前进程是后台子进程，heavy handler 不要再 fork

JOBS_DIR = ROOT / "jobs"
POLL_INTERVAL = 2  # 轮询间隔秒数


def _state_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_path(job_id: str, phase: str) -> Path:
    return _state_dir(job_id) / f"{phase}.pid"


def _result_path(job_id: str, phase: str) -> Path:
    return _state_dir(job_id) / f"{phase}.result.json"


def _error_path(job_id: str, phase: str) -> Path:
    return _state_dir(job_id) / f"{phase}.error"


def _running_path(job_id: str, phase: str) -> Path:
    return _state_dir(job_id) / f"{phase}.running"


def is_running(job_id: str, phase: str) -> bool:
    """检查 phase 是否正在运行（通过 PID 文件 + 进程存活检测）"""
    pid_file = _pid_path(job_id, phase)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # 检查进程是否存活
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # PID 文件损坏或进程已死，清理
        pid_file.unlink(missing_ok=True)
        return False


def is_completed(job_id: str, phase: str) -> bool:
    """检查 phase 是否已完成（有 result 文件）"""
    return _result_path(job_id, phase).exists()


def get_status(job_id: str, phase: str) -> dict:
    """获取 phase 执行状态"""
    result_file = _result_path(job_id, phase)
    error_file = _error_path(job_id, phase)
    pid_file = _pid_path(job_id, phase)

    if result_file.exists():
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            return {"status": "completed", "ok": data.get("ok", False), "result": data}
        except Exception:
            return {"status": "completed", "ok": False, "error": "result file corrupted"}

    if error_file.exists():
        error_text = error_file.read_text(encoding="utf-8")[:2000]
        return {"status": "failed", "ok": False, "error": error_text}

    if is_running(job_id, phase):
        pid = pid_file.read_text().strip() if pid_file.exists() else "?"
        elapsed = 0
        if pid_file.exists():
            elapsed = time.time() - pid_file.stat().st_mtime
        return {"status": "running", "ok": None, "pid": pid, "elapsed_seconds": int(elapsed)}

    return {"status": "not_started", "ok": None}


# phase01 OCR 和 phase04 预搜索无超时，其他 phase 保留超时
_NO_TIMEOUT_PHASES = {"phase01_document_intake", "phase04_presearch"}


def wait_for_completion(job_id: str, phase: str, timeout: int = 1800) -> dict:
    """阻塞等待 phase 完成。NO_TIMEOUT_PHASES 忽略 timeout，跑到完为止。"""
    if phase in _NO_TIMEOUT_PHASES:
        timeout = 0
    start = time.time()
    while timeout <= 0 or (time.time() - start < timeout):
        status = get_status(job_id, phase)
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(POLL_INTERVAL)
    return {"status": "timeout", "ok": False, "error": f"Timed out after {timeout}s"}


# ═══════════════════════════════════════════════
# Phase 执行器
# ═══════════════════════════════════════════════

def _build_job_ctx(job_id: str, entity: str, market: str, input_file: str,
                   query: str, session_id: str) -> "JobContext":
    """构建 JobContext 并注入 workspace"""
    from runtime.profiles.base import JobContext
    from runtime.orchestrator.workspace_layout import build_job_workspace

    job_ctx = JobContext(
        job_id=job_id,
        entity=entity,
        query=query or f"{entity} BP 尽调",
        market=market,
        metadata={
            "input_file": input_file,
            "session_id": session_id,
        },
    )
    workspace = build_job_workspace(ROOT, job_id)
    job_ctx.workspace = workspace
    return job_ctx


# pipeline 名称 → (模块路径, Profile 类名) 路由表
# 新增管线时在此登记即可，run_phase 自动支持。
_PROFILE_ROUTES: dict[str, tuple[str, str]] = {
    "bp": ("runtime.profiles.bp_profile", "BPProfile"),
    "ir": ("runtime.profiles.ir_profile", "IRProfile"),
    "ic": ("runtime.profiles.ic_profile", "ICProfile"),
    "ic_topic": ("runtime.profiles.ic_topic_profile", "ICTopicProfile"),
    "lit": ("runtime.profiles.lit_review_profile", "LitReviewProfile"),
}


def _resolve_profile(pipeline: str) -> "PipelineProfile":
    """按 pipeline 名称实例化对应 Profile。未知 pipeline 直接抛 ValueError，
    不再静默回退到 BPProfile（旧 bug：ic/ic_topic/lit 全被当成 BP，报 Unknown phase）。"""
    import importlib

    route = _PROFILE_ROUTES.get(pipeline)
    if route is None:
        valid = ", ".join(sorted(_PROFILE_ROUTES))
        raise ValueError(f"Unknown pipeline: {pipeline}. Valid: {valid}")
    module_name, class_name = route
    profile_cls = getattr(importlib.import_module(module_name), class_name)
    return profile_cls(runtime_root=ROOT)


def run_phase(job_id: str, phase: str, entity: str = "", market: str = "cn",
              input_file: str = "", query: str = "", session_id: str = "",
              pipeline: str = "bp") -> dict:
    """执行单个 phase，返回结果 dict"""
    job_ctx = _build_job_ctx(job_id, entity, market, input_file, query, session_id)

    try:
        profile = _resolve_profile(pipeline)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    phases = profile.phases()
    if phase not in phases:
        return {"ok": False, "error": f"Unknown phase: {phase}. Valid: {phases}"}

    print(f"▶ [phase_runner] 开始执行: {phase}", flush=True)
    start = time.time()
    try:
        result = profile.run_phase(phase, job_ctx)
        elapsed = time.time() - start
        result["_elapsed_seconds"] = round(elapsed, 1)
        print(f"✅ [phase_runner] {phase} 完成 ({elapsed:.0f}s)", flush=True)
        return result
    except Exception as exc:
        elapsed = time.time() - start
        tb = traceback.format_exc()
        print(f"❌ [phase_runner] {phase} 失败 ({elapsed:.0f}s): {exc}", flush=True)
        print(tb, flush=True)
        return {"ok": False, "error": str(exc), "traceback": tb, "_elapsed_seconds": round(elapsed, 1)}


def _write_pid(job_id: str, phase: str):
    """写 PID 文件"""
    pid_file = _pid_path(job_id, phase)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _cleanup_pid(job_id: str, phase: str):
    """清理 PID 文件"""
    _pid_path(job_id, phase).unlink(missing_ok=True)
    _running_path(job_id, phase).unlink(missing_ok=True)


def _write_result(job_id: str, phase: str, result: dict):
    """写结果文件"""
    path = _result_path(job_id, phase)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_error(job_id: str, phase: str, error: str):
    """写错误文件"""
    path = _error_path(job_id, phase)
    path.write_text(error, encoding="utf-8")


# _run_in_background 已删除（2026-07-06）
# os.fork() 模式导致 Popen 跟踪父进程（立即退出 rc=0），实际工作在 daemon 子进程。
# 现在 heavy phase 由 launch_heavy_phase 在当前进程直接调用 run_phase()。


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Phase Runner — 后台独立执行管线阶段")
    ap.add_argument("--job-id", required=True, help="任务 ID")
    ap.add_argument("--phase", required=True, help="阶段名称")
    ap.add_argument("--entity", default="", help="实体名称")
    ap.add_argument("--market", default="cn", help="市场 (cn/us/hk)")
    ap.add_argument("--input-file", default="", help="输入文件路径")
    ap.add_argument("--query", default="", help="查询关键词")
    ap.add_argument("--session-id", default="", help="会话 ID")
    ap.add_argument("--pipeline", default="bp", choices=list(_PROFILE_ROUTES.keys()),
                    help="管线类型 (bp=尽调, ir=研报, ic=行业研究, ic_topic=课题研究, lit=文献综述)")

    # 模式选择
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", default=True, help="前台运行（默认）")
    # --background 已废弃（2026-07-06）：os.fork() 导致 Popen 跟踪父进程（立即退出 rc=0），
    # 实际工作在 daemon 子进程中，launch_heavy_phase 误判为"异常退出"。
    # 现在 heavy phase 由 launch_heavy_phase 在当前进程直接调用 run_phase()，不需要子进程。
    mode.add_argument("--status", action="store_true", help="查询状态")
    mode.add_argument("--wait", action="store_true", help="等待完成")
    mode.add_argument("--wait-all", action="store_true", help="等待所有指定 phases 完成")

    ap.add_argument("--timeout", type=int, default=1800, help="等待超时（秒，默认1800；phase01/04忽略此参数）")

    args = ap.parse_args()

    # 查询状态
    if args.status:
        status = get_status(args.job_id, args.phase)
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        return

    # 等待完成（phase01/04 内部忽略 timeout）
    if args.wait:
        status = wait_for_completion(args.job_id, args.phase, timeout=args.timeout)
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        return

    # --background 已删除（2026-07-06），直接前台运行
    result = run_phase(args.job_id, args.phase, args.entity, args.market,
                       args.input_file, args.query, args.session_id,
                       pipeline=args.pipeline)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
