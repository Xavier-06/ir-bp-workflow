"""Literature Review Pipeline 入口 — VC 技术评估文献综述管线。

用法:
    from runtime.entrypoints.run_lit_pipeline_entry import run_lit_job
    result = run_lit_job(job_id="LIT-20260630-001", entity="固态电池", query="技术评估")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.orchestrator.state_store import run_pipeline
from runtime.profiles.base import JobContext
from runtime.profiles.lit_review_profile import LitReviewProfile

RUNTIME_ROOT = Path(__file__).resolve().parents[2]


def run_lit_job(
    job_id: str,
    entity: str = "",
    query: str = "",
    market: str = "cn",
    focus_dimensions: list[str] | None = None,
    language: str = "zh-CN",
    start_phase: str | None = None,
) -> dict:
    profile = LitReviewProfile(runtime_root=RUNTIME_ROOT)
    job_ctx = JobContext(
        job_id=job_id,
        entity=entity,
        query=query,
        market=market,
        metadata={
            "focus_dimensions": focus_dimensions or [
                "技术成熟度", "市场竞争格局", "商业化时间线", "投资判断"
            ],
            "language": language,
        },
    )
    return run_pipeline(profile=profile, job_ctx=job_ctx, runtime_root=RUNTIME_ROOT,
                        start_phase=start_phase)
