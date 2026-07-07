"""
Heavy Phase Background Runner — 用于 profile handler 内部

将耗时 > 2 分钟的 phase（company_verify, presearch, delivery）在**当前进程内**
直接执行，避免子进程管理的复杂性。

2026-07-06 重构：彻底删除子进程架构。

之前的设计（v1）：
  Popen → phase_runner --background → os.fork() → daemon 子进程
  → launch_heavy_phase 轮询 .result.json 文件
  问题：Popen 跟踪 fork 父进程（立即退出 rc=0），实际工作在 daemon 子进程。
  launch_heavy_phase 看到父进程退出但找不到结果文件 → 误判为"异常退出"。

之后的修复（v2）：
  Popen → phase_runner --run → 从 stdout 读 JSON
  问题：stdout 混杂了 print 日志和 JSON，解析不稳定。
  start_new_session=True 可能影响 stdout 缓冲。

当前设计（v3）：
  直接在当前进程内调用 phase_runner.run_phase()。
  kernel.run() 已经在 Python 进程内执行 profile handler，
  heavy phase handler 直接调 run_phase 即可。
  不需要子进程、不需要文件轮询、不需要 stdout 解析。
  "block-wait" 就是函数调用本身。

poll_heavy_phase 保留作为安全网（kernel 内部消化遗留 needs_poll）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# phase_runner 的 ROOT 和 sys.path 在 launch_heavy_phase 内按需添加
PHASE_RUNNER = Path(__file__).resolve().parent / "phase_runner.py"

PHASE_TIMEOUTS = {
    # BP phases
    "phase02_company_verify": 600,
    "phase04_presearch": 900,
    "phase33_delivery": 600,
    # IR phases (continuous numbering phase01-14)
    "phase14_delivery": 600,
    "phase05_extract": 900,
    # IC phases (continuous numbering phase01-08)
    "phase03_presearch": 900,
    "phase04_extract": 900,
    "phase08_delivery": 600,
}


def _state_dir(runtime_root: Path, job_id: str) -> Path:
    d = runtime_root / "jobs" / job_id / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _result_path(runtime_root: Path, job_id: str, phase: str) -> Path:
    return _state_dir(runtime_root, job_id) / f"{phase}.result.json"


def _pid_path(runtime_root: Path, job_id: str, phase: str) -> Path:
    return _state_dir(runtime_root, job_id) / f"{phase}.pid"


def _error_path(runtime_root: Path, job_id: str, phase: str) -> Path:
    return _state_dir(runtime_root, job_id) / f"{phase}.error"


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def check_cached_result(runtime_root: Path, job_id: str, phase: str) -> dict | None:
    """检查是否有缓存的结果文件（来自之前的后台运行）。
    如果有，返回解析后的 dict；否则返回 None。
    同时清理 PID 文件。"""
    result_file = _result_path(runtime_root, job_id, phase)
    pid_file = _pid_path(runtime_root, job_id, phase)

    if result_file.exists():
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            # 清理：删除结果文件避免下次误读（结果已被消费）
            result_file.unlink(missing_ok=True)
            _error_path(runtime_root, job_id, phase).unlink(missing_ok=True)
            pid_file.unlink(missing_ok=True)
            return data
        except Exception:
            pass

    # 检查是否有错误文件
    error_file = _error_path(runtime_root, job_id, phase)
    if error_file.exists():
        error_text = error_file.read_text(encoding="utf-8")
        # 也清理错误文件
        error_file.unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)
        return {"ok": False, "error": error_text}

    return None


def launch_heavy_phase(
    runtime_root: Path,
    job_ctx: Any,
    phase: str,
    pipeline: str = "bp",
) -> dict[str, Any]:
    """在当前进程内直接执行 heavy phase，block-wait 直到完成。

    2026-07-06 v3：不再使用子进程，直接调用 phase_runner.run_phase()。
    "block-wait" 就是函数调用本身——没有 fork、没有文件轮询、没有 stdout 解析。
    """
    metadata = job_ctx.metadata or {}

    # 确保 phase_runner 的路径在 sys.path 中（run_phase 需要 ROOT）
    import sys
    root_str = str(runtime_root)
    scripts_str = str(runtime_root / "scripts")
    for p in [root_str, scripts_str]:
        if p not in sys.path:
            sys.path.insert(0, p)

    # 设置 IRBP_BG_CHILD 标记，让 handler 内部不再 fork
    os.environ["IRBP_BG_CHILD"] = "1"

    # 先检查是否有缓存结果（来自之前中断的执行）
    cached = check_cached_result(runtime_root, job_ctx.job_id, phase)
    if cached is not None:
        print(f"  📦 [heavy_phase_bg] 使用缓存结果: {phase}", flush=True)
        return cached

    timeout = PHASE_TIMEOUTS.get(phase, 900)
    print(f"  🔄 [heavy_phase_bg] 当前进程执行: {phase} (超时 {timeout}s)", flush=True)

    start_time = time.time()
    try:
        from phase_runner import run_phase

        result = run_phase(
            job_id=job_ctx.job_id,
            phase=phase,
            entity=job_ctx.entity or "",
            market=getattr(job_ctx, "market", "cn") or "cn",
            input_file=metadata.get("input_file", ""),
            query=job_ctx.query or "",
            session_id=metadata.get("session_id", ""),
            pipeline=pipeline,
        )
        elapsed = time.time() - start_time
        ok = result.get("ok", False)
        icon = "✅" if ok else "❌"
        print(f"  {icon} [heavy_phase_bg] {phase} 完成 ({elapsed:.1f}s)", flush=True)
        return result

    except Exception as exc:
        elapsed = time.time() - start_time
        import traceback
        tb = traceback.format_exc()
        print(f"  ❌ [heavy_phase_bg] {phase} 异常 ({elapsed:.1f}s): {exc}", flush=True)
        return {
            "ok": False,
            "phase": phase,
            "error": str(exc),
            "traceback": tb,
        }


def poll_heavy_phase(
    runtime_root: Path,
    job_id: str,
    phase: str,
    timeout: int = 0,
) -> dict[str, Any]:
    """轮询 heavy phase 的后台执行状态。

    注意：launch_heavy_phase 已改为当前进程执行，不再写文件。
    此函数仅作为安全网保留（kernel 内部消化遗留 needs_poll）。
    """
    pid_file = _pid_path(runtime_root, job_id, phase)

    # 检查是否已有结果
    cached = check_cached_result(runtime_root, job_id, phase)
    if cached is not None:
        return {"status": "completed", "ok": cached.get("ok", False), "result": cached}

    # 检查进程是否在运行
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_process_alive(pid):
                if timeout <= 0:
                    elapsed = time.time() - pid_file.stat().st_mtime
                    return {"status": "running", "ok": None, "pid": pid, "elapsed_seconds": int(elapsed)}
                # 阻塞等待模式
                start = time.time()
                while time.time() - start < timeout:
                    cached = check_cached_result(runtime_root, job_id, phase)
                    if cached is not None:
                        return {"status": "completed", "ok": cached.get("ok", False), "result": cached}
                    if not _is_process_alive(pid):
                        break
                    time.sleep(3)
                # 超时或进程已死
                cached = check_cached_result(runtime_root, job_id, phase)
                if cached is not None:
                    return {"status": "completed", "ok": cached.get("ok", False), "result": cached}
                return {"status": "timeout" if timeout > 0 else "failed", "ok": False,
                        "error": f"Process {pid} dead without result"}
            else:
                # 进程已死但没结果 → 检查 error
                pid_file.unlink(missing_ok=True)
                cached = check_cached_result(runtime_root, job_id, phase)
                if cached is not None:
                    return {"status": "completed", "ok": cached.get("ok", False), "result": cached}
                return {"status": "failed", "ok": False, "error": f"Process {pid} died without writing result"}
        except ValueError:
            pid_file.unlink(missing_ok=True)

    return {"status": "not_started", "ok": None}
