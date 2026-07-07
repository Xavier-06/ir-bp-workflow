from __future__ import annotations

from runtime.orchestrator.kernel import OrchestratorKernel
from runtime.profiles.base import JobContext, PipelineProfile


def test_kernel_keeps_next_phase_on_dispatch_prepare_when_has_more_steps():
    profile = PipelineProfile(
        name="ir",
        job_type="investment_research",
        phase_handlers={
            "phase08_dispatch_prepare": lambda job_ctx: {
                "ok": True,
                "needs_dispatch": True,
                "result": {"has_more": True, "task_tool_instructions": [{"step": "step2_industry"}]},
            },
            "phase09_dispatch_collect": lambda job_ctx: {"ok": True},
        },
    )

    result = OrchestratorKernel(runtime_root=__import__("pathlib").Path("/tmp")).run(
        profile,
        JobContext(job_id="TASK-DISPATCH-HAS-MORE"),
    )

    assert result["next_phase"] == "phase08_dispatch_prepare"


def test_kernel_keeps_current_phase_when_top_level_dispatch_info_pairs_with_legacy_has_more():
    profile = PipelineProfile(
        name="ir",
        job_type="investment_research",
        phase_handlers={
            "phase08_dispatch_prepare": lambda job_ctx: {
                "ok": True,
                "needs_dispatch": True,
                "dispatch_info": {"manifests": ["/tmp/manifest.json"], "roles": ["step2_industry"]},
                "result": {"has_more": True, "task_tool_instructions": [{"step": "step2_industry"}]},
            },
            "phase09_dispatch_collect": lambda job_ctx: {"ok": True},
        },
    )

    result = OrchestratorKernel(runtime_root=__import__("pathlib").Path("/tmp")).run(
        profile,
        JobContext(job_id="TASK-DISPATCH-MIXED-HAS-MORE"),
    )

    assert result["next_phase"] == "phase08_dispatch_prepare"
    assert result["dispatch_info"]["manifests"] == ["/tmp/manifest.json"]


def test_kernel_returns_top_level_dispatch_info_for_agent_handoff():
    profile = PipelineProfile(
        name="bp",
        job_type="business_plan_dd",
        phase_handlers={
            "phase27_synthesis_prepare": lambda job_ctx: {
                "ok": True,
                "needs_dispatch": True,
                "dispatch_info": {
                    "manifests": ["/tmp/bp_phase3_manifest_synthesis.json"],
                    "roles": ["bp_统稿"],
                    "task_dir": "/tmp/task",
                },
                "result": {"manifest_path": "/tmp/bp_phase3_manifest_synthesis.json"},
            },
            "phase28_synthesis_collect": lambda job_ctx: {"ok": True},
        },
    )

    result = OrchestratorKernel(runtime_root=__import__("pathlib").Path("/tmp")).run(
        profile,
        JobContext(job_id="TASK-DISPATCH-TOPLEVEL"),
    )

    assert result["dispatch_info"]["manifests"] == ["/tmp/bp_phase3_manifest_synthesis.json"]
    assert result["dispatch_info"]["roles"] == ["bp_统稿"]
    assert result["next_phase"] == "phase28_synthesis_collect"
