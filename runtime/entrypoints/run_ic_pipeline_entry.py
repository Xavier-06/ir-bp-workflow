from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.orchestrator.state_store import run_pipeline
from runtime.profiles.base import JobContext
from runtime.profiles.ic_profile import ICProfile


RUNTIME_ROOT = Path(__file__).resolve().parents[2]


def run_ic_job(
    job_id: str,
    entity: str = "",
    query: str = "",
    market: str = "cn",
    max_extract_pages: int = 15,
    rounds: int = 3,
    use_facts: bool = True,
    max_new_queries: int = 12,
    session_id: str = "",
    dispatch_max_wait: int = 1800,
    dispatch_poll_interval: int = 30,
    start_phase: str | None = None,
) -> dict:
    """行业研究管线入口。

    entity: 行业名称（如"半导体"、"新能源汽车"）
    query: 研究查询（可包含重点公司名单）
    market: 市场区域（cn/hk/us）
    """
    profile = ICProfile(runtime_root=RUNTIME_ROOT)
    job_ctx = JobContext(
        job_id=job_id,
        entity=entity,
        query=query,
        market=market,
        metadata={
            "max_extract_pages": max_extract_pages,
            "rounds": rounds,
            "use_facts": use_facts,
            "max_new_queries": max_new_queries,
            "market": market,
            "session_id": session_id,
            "dispatch_max_wait": dispatch_max_wait,
            "dispatch_poll_interval": dispatch_poll_interval,
        },
    )
    return run_pipeline(profile=profile, job_ctx=job_ctx, runtime_root=RUNTIME_ROOT,
                        start_phase=start_phase)
