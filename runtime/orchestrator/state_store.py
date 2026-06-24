from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from runtime.orchestrator.workspace_layout import JobWorkspace, build_job_workspace
from runtime.profiles.base import JobContext, PipelineProfile


class StateStore:
    """Unified state coordinator.

    Wraps three subsystems:
    - task_ledger (scripts/task_ledger.py) — human-facing task tracking
    - task_registry (scripts/task_registry.py) — machine-facing dependency/phase tracking
    - JobWorkspace — per-job artifact directory structure

    All phase state mutations go through here so there's one source of truth.
    """

    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.legacy_tasks_dir = runtime_root / "data" / "tasks"
        self.legacy_registry_dir = runtime_root / "tasks" / "task_registry"
        self._ledger = None
        self._registry = None

    # ── Lazy subsystem access ────────────────────────────
    @property
    def ledger(self):
        """Access task_ledger (human-facing)."""
        if self._ledger is None:
            from scripts.task_ledger import load_store
            self._ledger = load_store()
        return self._ledger

    @property
    def registry(self):
        """Access task_registry (machine-facing)."""
        if self._registry is None:
            from scripts.task_registry import TaskRegistry
            self._registry = TaskRegistry(task_dir=self.legacy_registry_dir)
        return self._registry

    # ── Job lifecycle ────────────────────────────────────
    def get_or_create_job(self, job_ctx: JobContext) -> JobWorkspace:
        """Initialize or reuse workspace + ledger state for a job.

        Pipeline resume calls may enter with the same job_id multiple times.
        This method is intentionally idempotent: it always ensures the
        workspace exists, but it registers the ledger task only once.
        """
        workspace = build_job_workspace(self.runtime_root, job_ctx.job_id)

        from scripts.task_ledger import load_store, save_store, now_iso, ensure_task_shape
        store = load_store()
        tasks = store.setdefault("tasks", [])
        existing_matches = [task for task in tasks if task.get("task_id") == job_ctx.job_id]

        if existing_matches:
            existing = ensure_task_shape(existing_matches[0])
            merged_progress = []
            for match in existing_matches:
                for event in match.get("progress_updates", []) or []:
                    if event not in merged_progress:
                        merged_progress.append(event)
            existing["progress_updates"] = merged_progress
            existing.setdefault("output_path", str(workspace.delivery_dir))
            existing["updated_at"] = now_iso()
            if len(existing_matches) > 1:
                seen = False
                deduped_tasks = []
                for task in tasks:
                    if task.get("task_id") != job_ctx.job_id:
                        deduped_tasks.append(task)
                        continue
                    if not seen:
                        deduped_tasks.append(existing)
                        seen = True
                store["tasks"] = deduped_tasks
            save_store(store)
            return workspace

        task = ensure_task_shape({
            "task_id": job_ctx.job_id,
            "title": f"IR Pipeline: {job_ctx.entity}",
            "task_type": "投研研报",
            "status": "进行中",
            "owner": "IR Orchestrator",
            "recipient": "internal",
            "next_action": "phase0_preflight",
            "blocked_reason": "",
            "output_path": str(workspace.delivery_dir),
            "notes": f"market={job_ctx.market} query={job_ctx.query}",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
        tasks.append(task)
        save_store(store)

        return workspace

    def create_job(self, job_ctx: JobContext) -> JobWorkspace:
        """Backward-compatible alias for idempotent job initialization."""
        return self.get_or_create_job(job_ctx)

    def update_phase_status(self, job_id: str, phase: str, status: str,
                            result: Optional[dict] = None):
        """Update both ledger and registry after a phase completes."""
        # Update ledger progress
        from scripts.task_ledger import load_store, save_store, find_task, now_iso, ensure_task_shape
        store = load_store()
        try:
            task = find_task(store, job_id)
            event = {
                "message": f"Phase {phase} → {status}",
                "stage": phase,
                "created_at": now_iso(),
                "sent_at": None,
            }
            task.setdefault("progress_updates", []).append(event)
            if status == "completed":
                task["status"] = "进行中"
                task["next_action"] = f"next after {phase}"
            elif status == "failed":
                task["status"] = "已阻塞"
                task["blocked_reason"] = f"Phase {phase} failed"
            task["updated_at"] = now_iso()
            save_store(store)
        except SystemExit:
            pass  # task not in ledger yet, non-blocking

    def record_artifact(self, job_id: str, artifact_type: str, path: Path):
        """Record an artifact location in the job's state dir."""
        workspace = build_job_workspace(self.runtime_root, job_id)
        manifest_path = workspace.state_dir / "artifacts.json"
        artifacts = {}
        if manifest_path.exists():
            try:
                artifacts = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        artifacts[artifact_type] = {
            "path": str(path),
            "recorded_at": time.time(),
        }
        manifest_path.write_text(
            json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Snapshot (for debugging / API) ───────────────────
    def snapshot(self, job_id: str) -> dict[str, Any]:
        workspace = build_job_workspace(self.runtime_root, job_id)
        snap = {
            "job_id": job_id,
            "workspace": str(workspace.root),
            "workspace_dirs": {k: str(v) for k, v in workspace.__dict__.items()},
            "legacy_tasks_dir": str(self.legacy_tasks_dir),
            "legacy_registry_dir": str(self.legacy_registry_dir),
        }
        # Read artifacts manifest if exists
        artifacts_path = workspace.state_dir / "artifacts.json"
        if artifacts_path.exists():
            try:
                snap["artifacts"] = json.loads(artifacts_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return snap


def run_pipeline(profile: PipelineProfile, job_ctx: JobContext, runtime_root: Path,
                 start_phase: str | None = None) -> dict[str, Any]:
    """Main entry: create workspace, inject into context, run phases, track state."""
    from runtime.orchestrator.kernel import OrchestratorKernel

    kernel = OrchestratorKernel(runtime_root=runtime_root)
    state_store = StateStore(runtime_root=runtime_root)

    # Create or reuse workspace and ledger registration.
    workspace = state_store.get_or_create_job(job_ctx)
    job_ctx.workspace = workspace

    # Run the pipeline
    result = kernel.run(profile, job_ctx, start_phase=start_phase)

    # Update final state
    final_status = "completed" if result.get("ok") else "failed"
    phases_run = result.get("phases") or []
    if result.get("failed_phase"):
        last_phase = result["failed_phase"]
    elif phases_run:
        last_phase = phases_run[-1].get("phase", "unknown")
    elif start_phase:
        last_phase = start_phase
        result["failed_phase"] = start_phase
    else:
        last_phase = "unknown"
    state_store.update_phase_status(
        job_ctx.job_id, phase=last_phase, status=final_status, result=result
    )

    result["state_snapshot"] = state_store.snapshot(job_ctx.job_id)
    return result
