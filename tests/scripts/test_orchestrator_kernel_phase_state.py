from __future__ import annotations

import json
from pathlib import Path

from runtime.orchestrator.kernel import OrchestratorKernel
from runtime.profiles.base import JobContext, PipelineProfile


def test_kernel_phase_state_records_resume_metadata_and_attempts(tmp_path):
    call_count = {"phase_b": 0}

    def phase_b_handler(job_ctx):
        call_count["phase_b"] += 1
        return {"ok": True, "value": f"run_{call_count['phase_b']}"}

    profile = PipelineProfile(
        name="ir",
        job_type="investment_research",
        phase_handlers={
            "phase_a": lambda job_ctx: {"ok": True, "value": "skipped"},
            "phase_b": phase_b_handler,
        },
    )
    job_ctx = JobContext(job_id="ir_state_001", entity="TestCo")
    kernel = OrchestratorKernel(runtime_root=tmp_path)

    first = kernel.run(profile, job_ctx, start_phase="phase_b")
    # 第二次调用时 phase_b 状态为 completed，kernel 会跳过
    second = kernel.run(profile, job_ctx, start_phase="phase_b")

    state_path = Path(first["workspace"]) / "state" / "phase_b.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert second["ok"] is True
    assert payload["phase"] == "phase_b"
    assert payload["status"] == "completed"
    # 新行为：已完成的 phase 会被跳过而非重跑，attempt 保持为 1
    assert payload["attempt"] == 1
    assert payload["resume_from"] == "phase_b"
    assert payload["started_at"]
    assert payload["finished_at"] >= payload["started_at"]
    assert payload["elapsed_seconds"] >= 0
    assert payload["result"]["value"] == "run_1"
    # 第二次调用中 phase_b 应该被跳过
    assert call_count["phase_b"] == 1
    skipped_results = [p for p in second["phases"] if p.get("result", {}).get("skipped")]
    assert len(skipped_results) > 0


def test_kernel_phase_state_records_pause_and_failure_statuses(tmp_path):
    cases = {
        "phase_dispatch": (lambda job_ctx: {"ok": True, "needs_dispatch": True}, "needs_dispatch"),
        "phase_poll": (lambda job_ctx: {"ok": True, "needs_poll": True}, "needs_poll"),
        "phase_fail": (lambda job_ctx: {"ok": False, "error": "boom"}, "failed"),
    }

    for phase_name, (handler, expected_status) in cases.items():
        profile = PipelineProfile(
            name="ir",
            job_type="investment_research",
            phase_handlers={phase_name: handler},
        )
        job_ctx = JobContext(job_id=f"ir_state_{phase_name}", entity="TestCo")
        result = OrchestratorKernel(runtime_root=tmp_path).run(profile, job_ctx)

        state_path = Path(result["workspace"]) / "state" / f"{phase_name}.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))

        assert payload["phase"] == phase_name
        assert payload["status"] == expected_status
        assert payload["attempt"] == 1
        assert payload["resume_from"] is None


def test_kernel_phase_state_treats_legacy_raw_state_as_first_attempt(tmp_path):
    profile = PipelineProfile(
        name="ir",
        job_type="investment_research",
        phase_handlers={"phase_legacy": lambda job_ctx: {"ok": True}},
    )
    job_ctx = JobContext(job_id="ir_state_legacy", entity="TestCo")
    kernel = OrchestratorKernel(runtime_root=tmp_path)
    workspace = kernel.prepare_job(job_ctx)
    legacy_path = workspace.state_dir / "phase_legacy.json"
    legacy_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    result = kernel.run(profile, job_ctx, start_phase="phase_legacy")

    payload = json.loads((Path(result["workspace"]) / "state" / "phase_legacy.json").read_text(encoding="utf-8"))
    assert payload["attempt"] == 2
    assert payload["status"] == "completed"
