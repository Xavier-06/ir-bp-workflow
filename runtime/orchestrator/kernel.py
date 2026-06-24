from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.orchestrator.workspace_layout import JobWorkspace, build_job_workspace
from runtime.profiles.base import JobContext


@dataclass
class OrchestratorKernel:
    runtime_root: Path

    def prepare_job(self, job_ctx: JobContext) -> JobWorkspace:
        """Build (or reuse) a JobWorkspace and inject it into the JobContext."""
        workspace = build_job_workspace(self.runtime_root, job_ctx.job_id)
        job_ctx.workspace = workspace
        return workspace

    def _read_phase_state(self, workspace: JobWorkspace, phase_name: str) -> dict | None:
        """读取 phase state 文件，不存在返回 None。"""
        state_file = workspace.state_dir / f"{phase_name}.json"
        if not state_file.exists():
            return None
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def run(self, profile, job_ctx: JobContext,
            start_phase: str | None = None) -> dict[str, Any]:
        workspace = self.prepare_job(job_ctx)

        all_phases = profile.phases()
        phases = all_phases
        prerequisites = getattr(profile, "phase_prerequisites", lambda: {})()
        # phase_outputs: 声明每个 phase 产出哪些文件（相对 workspace.root）
        # kernel 用它在缺失文件时精准定位应该回填哪个 phase，而非盲选前置
        phase_outputs = getattr(profile, "phase_outputs", lambda: {})()

        # 构建反查表：file → 产出该文件的 phase_name
        file_to_producer: dict[str, str] = {}
        for phase_name, outputs in phase_outputs.items():
            for rel_path in outputs:
                file_to_producer[rel_path] = phase_name

        if start_phase:
            try:
                idx = all_phases.index(start_phase)
            except ValueError:
                return {"ok": False, "error": f"Unknown start_phase: {start_phase}"}

            # ── 依赖自动回填：检查 start_phase 及后续 phase 所需的前置产物 ──
            earliest_backfill = idx  # 默认从 start_phase 开始
            for check_idx in range(idx, len(all_phases)):
                phase_to_check = all_phases[check_idx]
                required_files = prerequisites.get(phase_to_check, [])
                for rel_path in required_files:
                    if not (workspace.root / rel_path).exists():
                        # 1. 找到产出该文件的 phase（精确匹配，非盲选）
                        producer = file_to_producer.get(rel_path)
                        if producer is None:
                            # 没人声明产出该文件 → 降级为警告，不回填
                            print(
                                f"  ⚠️ [kernel] {phase_to_check} 依赖 {rel_path} 缺失，"
                                f"但无 phase 声明产出该文件，跳过回填",
                                flush=True,
                            )
                            continue

                        # 2. 检查产出 phase 是否已完成
                        state = self._read_phase_state(workspace, producer)
                        if state and state.get("status") == "completed":
                            # 已完成但文件消失 → 记录警告，不回填
                            print(
                                f"  ⚠️ [kernel] {rel_path} 缺失，但产出 phase {producer} "
                                f"状态为 completed（第 {state.get('attempt', '?')} 次），跳过回填",
                                flush=True,
                            )
                            continue

                        # 3. 精准回填：只回退到产出该文件的 phase
                        producer_idx = all_phases.index(producer) if producer in all_phases else -1
                        if 0 <= producer_idx < earliest_backfill:
                            earliest_backfill = producer_idx
                            print(
                                f"  🔄 [kernel] {phase_to_check} 依赖 {rel_path} 缺失，"
                                f"精准回填从 {producer} 开始",
                                flush=True,
                            )
                        break  # 只要有一个缺失就够了，不需要继续检查该 phase

            if earliest_backfill < idx:
                phases = all_phases[earliest_backfill:]
            else:
                phases = all_phases[idx:]

        results: dict[str, Any] = {
            "job_id": job_ctx.job_id,
            "profile": profile.name,
            "workspace": str(workspace.root),
            "phases": [],
        }
        for i, phase_name in enumerate(phases):
            # 跳过已完成的 phase（回填可能拉回一些已完成的前置 phase）
            phase_state = self._read_phase_state(workspace, phase_name)
            if phase_state and phase_state.get("status") == "completed":
                print(f"\n⏭️  跳过已完成阶段: {phase_name}（第 {phase_state.get('attempt', '?')} 次完成）", flush=True)
                results["phases"].append({
                    "phase": phase_name,
                    "result": {"skipped": True, "reason": "already_completed"},
                })
                continue

            print(f"\n{'='*50}", flush=True)
            print(f"▶ 开始阶段: {phase_name}", flush=True)
            print(f"{'='*50}", flush=True)
            started_at = time.time()
            phase_result = profile.run_phase(phase_name, job_ctx)
            finished_at = time.time()
            phase_ok = phase_result.get("ok", True)
            print(f"{'✅' if phase_ok else '❌'} 阶段完成: {phase_name} → {'成功' if phase_ok else '失败'}", flush=True)
            results["phases"].append({
                "phase": phase_name,
                "result": phase_result,
            })

            self._write_phase_state(
                workspace,
                phase_name,
                phase_result,
                started_at=started_at,
                finished_at=finished_at,
                resume_from=start_phase,
            )

            if phase_result.get("needs_dispatch"):
                dispatch_info = phase_result.get("dispatch_info") or phase_result.get("result", {})
                results["dispatch_info"] = dispatch_info
                results["status"] = "needs_dispatch"
                results["paused_after"] = phase_name
                legacy_dispatch_result = phase_result.get("result", {})
                has_more = dispatch_info.get("has_more") or legacy_dispatch_result.get("has_more")
                if has_more:
                    next_phase = phase_name
                else:
                    next_phase = phases[i + 1] if i + 1 < len(phases) else None
                results["next_phase"] = next_phase
                results["ok"] = True  # 不是失败，是暂停
                print(f"  ⏸ needs_dispatch — 暂停于 {phase_name}，等待子代理完成后用 start_phase='{next_phase if next_phase else 'done'}' 恢复", flush=True)
                return results

            if phase_result.get("needs_poll"):
                results["poll_info"] = phase_result
                results["status"] = "needs_poll"
                results["paused_after"] = phase_name
                results["next_phase"] = phases[i + 1] if i + 1 < len(phases) else None
                results["ok"] = True  # 不是失败，是暂停
                bg_pid = phase_result.get("bg_pid", "?")
                timeout = phase_result.get("timeout", 900)
                print(f"  ⏸ needs_poll — 后台子进程 PID={bg_pid} 执行中 ({phase_name})", flush=True)
                print(f"    用 scripts/heavy_phase_bg.py poll_heavy_phase() 或 start_phase='{phases[i + 1] if i + 1 < len(phases) else 'done'}' 恢复", flush=True)
                return results

            if phase_result.get("ok") is False:
                results["ok"] = False
                results["failed_phase"] = phase_name
                return results
        results["ok"] = True
        return results

    def _write_phase_state(self, workspace: JobWorkspace, phase_name: str,
                           phase_result: dict[str, Any], *, started_at: float,
                           finished_at: float, resume_from: str | None = None):
        """Write a phase state envelope with resume/audit metadata."""
        state_file = workspace.state_dir / f"{phase_name}.json"
        existing_attempt = 0
        if state_file.exists():
            try:
                existing = json.loads(state_file.read_text(encoding="utf-8"))
                if "attempt" in existing:
                    existing_attempt = int(existing.get("attempt", 0) or 0)
                else:
                    existing_attempt = 1
            except Exception:
                existing_attempt = 1
        phase_ok = phase_result.get("ok", True)
        if phase_result.get("needs_dispatch"):
            status = "needs_dispatch"
        elif phase_result.get("needs_poll"):
            status = "needs_poll"
        elif phase_ok is False:
            status = "failed"
        else:
            status = "completed"
        payload = {
            "phase": phase_name,
            "status": status,
            "attempt": existing_attempt + 1,
            "resume_from": resume_from,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": round(max(finished_at - started_at, 0.0), 3),
            "result": phase_result,
        }
        state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
