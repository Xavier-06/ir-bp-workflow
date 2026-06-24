from __future__ import annotations

import json

from runtime.orchestrator.state_store import run_pipeline
from runtime.profiles.base import JobContext, PipelineProfile


def test_run_pipeline_handles_unknown_start_phase_without_crashing(tmp_path, monkeypatch):
    import scripts.task_ledger as task_ledger

    ledger_path = tmp_path / "data" / "tasks" / "tasks.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps({"meta": {"version": 1}, "tasks": []}), encoding="utf-8")
    monkeypatch.setattr(task_ledger, "STORE", ledger_path)

    profile = PipelineProfile(
        name="ir",
        job_type="investment_research",
        phase_handlers={"phase_a": lambda job_ctx: {"ok": True}},
    )

    result = run_pipeline(profile, JobContext(job_id="TASK-BAD-RESUME", entity="TestCo"), tmp_path, start_phase="missing_phase")

    assert result["ok"] is False
    assert result["failed_phase"] == "missing_phase"
    assert "state_snapshot" in result
