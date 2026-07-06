"""
Heavy Phase Background Runner — 用于 profile handler 内部

将耗时 > 2 分钟的 phase（company_verify, presearch, delivery）放到
独立子进程中执行，避免受 Bash 工具超时限制。

工作原理：
    1. Handler 调用 launch_heavy_phase() → fork 子进程
    2. 函数内部 block-wait 直到子进程完成或超时
    3. 直接返回结果 dict（不再返回 needs_poll）
    4. Agent 永远不需要 poll，管线自动推进

子进程使用 start_new_session=True，不受父进程 SIGTERM 影响。
等待发生在 Python 进程内部（免费），不消耗 agent API turn。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PHASE_RUNNER = Path(__file__).resolve().parent / "phase_runner.py"

PHASE_TIMEOUTS = {
    "phase02_company_verify": 600,
    "phase04_presearch": 900,
    "phase33_delivery": 600,
    "phase5_delivery": 600,
    "phase15_extract": 900,
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
    """启动一个 heavy phase 的后台子进程，block-wait 直到完成。

    子进程通过 Popen(start_new_session=True) 启动，脱离父进程组。
    本函数在 Python 进程内部等待（不消耗 agent API turn），直到子进程
    写出结果文件或超时。

    结果写入 state/{phase}.result.json。
    """
    metadata = job_ctx.metadata or {}

    # 清理旧的 PID/result/error 文件
    for p in (_result_path(runtime_root, job_ctx.job_id, phase),
              _error_path(runtime_root, job_ctx.job_id, phase)):
        p.unlink(missing_ok=True)

    cmd = [
        sys.executable,
        str(PHASE_RUNNER),
        "--job-id", job_ctx.job_id,
        "--phase", phase,
        "--entity", job_ctx.entity or "",
        "--market", getattr(job_ctx, "market", "cn") or "cn",
        "--input-file", metadata.get("input_file", ""),
        "--query", job_ctx.query or "",
        "--session-id", metadata.get("session_id", ""),
        "--pipeline", pipeline,
        "--run",
    ]

    timeout = PHASE_TIMEOUTS.get(phase, 900)
    print(f"  🔄 [heavy_phase_bg] 启动后台子进程: {phase} (超时 {timeout}s)", flush=True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(runtime_root),
            start_new_session=True,
        )

        # 写 PID 文件（仅用于调试/监控）
        pid_file = _pid_path(runtime_root, job_ctx.job_id, phase)
        pid_file.write_text(str(proc.pid), encoding="utf-8")

        print(f"  📌 [heavy_phase_bg] 子进程 PID={proc.pid}，block-waiting ...", flush=True)

        # ── Block-wait：在 Python 内部等待，不烧 agent turn ──
        start_time = time.time()
        while time.time() - start_time < timeout:
            # 检查结果文件
            result_file = _result_path(runtime_root, job_ctx.job_id, phase)
            if result_file.exists():
                data = json.loads(result_file.read_text(encoding="utf-8"))
                result_file.unlink(missing_ok=True)
                pid_file.unlink(missing_ok=True)
                elapsed = time.time() - start_time
                print(f"  ✅ [heavy_phase_bg] {phase} 完成 ({elapsed:.1f}s)", flush=True)
                return data

            # 检查错误文件
            error_file = _error_path(runtime_root, job_ctx.job_id, phase)
            if error_file.exists():
                error_text = error_file.read_text(encoding="utf-8")
                error_file.unlink(missing_ok=True)
                pid_file.unlink(missing_ok=True)
                elapsed = time.time() - start_time
                print(f"  ❌ [heavy_phase_bg] {phase} 失败 ({elapsed:.1f}s): {error_text[:200]}", flush=True)
                return {"ok": False, "error": error_text}

            # 检查进程是否已死
            if proc.poll() is not None:
                # 进程已结束但没写结果/错误文件 → 最后一次检查
                break

            time.sleep(2)  # 2 秒检查间隔，Python sleep 免费

        # 超时或进程意外退出
        # 最后一次检查结果文件
        cached = check_cached_result(runtime_root, job_ctx.job_id, phase)
        if cached is not None:
            elapsed = time.time() - start_time
            print(f"  ✅ [heavy_phase_bg] {phase} 完成 ({elapsed:.1f}s, cached)", flush=True)
            return cached

        # 进程还在跑 → kill
        if proc.poll() is None:
            print(f"  ⏰ [heavy_phase_bg] {phase} 超时 ({timeout}s)，终止子进程 PID={proc.pid}", flush=True)
            try:
                proc.kill()
            except Exception:
                pass
            pid_file.unlink(missing_ok=True)
            return {"ok": False, "phase": phase, "error": f"子进程超时 ({timeout}s)，已终止"}

        # 进程已死，无结果
        stderr_text = proc.stderr.read() if proc.stderr else ""
        pid_file.unlink(missing_ok=True)
        return {
            "ok": False,
            "phase": phase,
            "error": f"子进程 PID={proc.pid} 异常退出 (rc={proc.returncode})，无结果文件",
            "stderr_tail": stderr_text[-500:] if stderr_text else "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "phase": phase,
            "error": f"Failed to launch subprocess: {exc}",
        }


def poll_heavy_phase(
    runtime_root: Path,
    job_id: str,
    phase: str,
    timeout: int = 0,
) -> dict[str, Any]:
    """轮询 heavy phase 的后台执行状态。

    timeout=0: 只查一次，立即返回
    timeout>0: 阻塞等待直到完成或超时
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
