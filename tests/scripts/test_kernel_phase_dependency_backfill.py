"""测试 kernel 的 phase 依赖自动回填机制。

场景：
1. start_phase 跳过 phase10，但 phase13 依赖 bp_research_plan.json → kernel 自动回填 phase10
2. 产物文件已存在时 → kernel 不回填，正常从 start_phase 开始
3. phase13 handler 层兜底：即使绕过 kernel，handler 也能自动回填缺失的 research_plan
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime.profiles.base import JobContext, PipelineProfile
from runtime.orchestrator.kernel import OrchestratorKernel


def _make_profile_with_deps(runtime_root: Path, task_dir: Path) -> PipelineProfile:
    """构造一个带 phase_prerequisites 的 mock profile。"""
    execution_log: list[str] = []

    def run_phase_a(job_ctx: JobContext) -> dict:
        execution_log.append("phase_a")
        # 模拟 phase_a 产出文件
        (task_dir / "artifact_a.json").write_text('{"ok": true}', encoding="utf-8")
        return {"ok": True, "phase": "phase_a"}

    def run_phase_b(job_ctx: JobContext) -> dict:
        execution_log.append("phase_b")
        return {"ok": True, "phase": "phase_b"}

    def run_phase_c(job_ctx: JobContext) -> dict:
        execution_log.append("phase_c")
        return {"ok": True, "phase": "phase_c"}

    profile = PipelineProfile(
        name="test",
        job_type="test",
        phase_handlers={
            "phase_a": run_phase_a,
            "phase_b": run_phase_b,
            "phase_c": run_phase_c,
        },
    )
    profile.execution_log = execution_log  # type: ignore[attr-defined]

    # phase_b 和 phase_c 都依赖 artifact_a.json（由 phase_a 产出）
    profile.phase_prerequisites = lambda: {  # type: ignore[attr-defined]
        "phase_b": ["artifact_a.json"],
        "phase_c": ["artifact_a.json"],
    }
    # 声明 phase_a 产出 artifact_a.json，kernel 才能精准回填
    profile.phase_outputs = lambda: {  # type: ignore[attr-defined]
        "phase_a": ["artifact_a.json"],
    }
    return profile


def test_kernel_backfills_skipped_prerequisite(tmp_path: Path):
    """start_phase=phase_b 但 artifact_a.json 不存在 → kernel 回填 phase_a"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    kernel = OrchestratorKernel(runtime_root=runtime_root)
    job_ctx = JobContext(job_id="test-backfill-001")
    workspace = kernel.prepare_job(job_ctx)

    profile = _make_profile_with_deps(runtime_root, workspace.root)

    result = kernel.run(profile, job_ctx, start_phase="phase_b")

    assert result["ok"] is True
    # phase_a 应该被执行（自动回填）
    assert "phase_a" in profile.execution_log
    assert "phase_b" in profile.execution_log
    assert "phase_c" in profile.execution_log
    # artifact_a.json 应该存在
    assert (workspace.root / "artifact_a.json").exists()


def test_kernel_skips_backfill_when_artifact_exists(tmp_path: Path):
    """start_phase=phase_b 且 artifact_a.json 已存在 → kernel 不回填"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    kernel = OrchestratorKernel(runtime_root=runtime_root)
    job_ctx = JobContext(job_id="test-skip-001")
    workspace = kernel.prepare_job(job_ctx)

    # 预先创建产物文件
    (workspace.root / "artifact_a.json").write_text('{"ok": true}', encoding="utf-8")

    profile = _make_profile_with_deps(runtime_root, workspace.root)

    result = kernel.run(profile, job_ctx, start_phase="phase_b")

    assert result["ok"] is True
    # phase_a 不应该被执行（产物已存在）
    assert "phase_a" not in profile.execution_log
    assert "phase_b" in profile.execution_log
    assert "phase_c" in profile.execution_log


def test_kernel_no_start_phase_runs_all(tmp_path: Path):
    """没有 start_phase → 正常从头跑所有 phase"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    kernel = OrchestratorKernel(runtime_root=runtime_root)
    job_ctx = JobContext(job_id="test-full-001")
    workspace = kernel.prepare_job(job_ctx)

    profile = _make_profile_with_deps(runtime_root, workspace.root)

    result = kernel.run(profile, job_ctx)

    assert result["ok"] is True
    assert profile.execution_log == ["phase_a", "phase_b", "phase_c"]


def test_bp_profile_declares_phase_prerequisites():
    """BPProfile 必须声明 phase13 对 bp_research_plan.json 的依赖"""
    from runtime.profiles.bp_profile import BPProfile
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        profile = BPProfile(runtime_root=Path(td))
        prereqs = profile.phase_prerequisites()

        assert "phase06_search_plan_compile" in prereqs
        assert "bp_research_plan.json" in prereqs["phase06_search_plan_compile"]
