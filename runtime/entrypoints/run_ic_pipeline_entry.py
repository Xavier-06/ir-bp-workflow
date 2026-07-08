from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.orchestrator.state_store import run_pipeline
from runtime.profiles.base import JobContext
from runtime.profiles.ic_profile import ICProfile
from runtime.profiles.ic_topic_profile import ICTopicProfile


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
    research_tier: str = "deep",
) -> dict:
    """行业研究管线入口（旧版，行业全景模式）。

    entity: 行业名称（如"半导体"、"新能源汽车"）
    query: 研究查询（可包含重点公司名单）
    market: 市场区域（cn/hk/us）
    research_tier: 研究深度 (deep/standard/quick)
    """
    profile = ICProfile(runtime_root=RUNTIME_ROOT, research_tier=research_tier)
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


def run_ic_topic_job(
    job_id: str,
    topic_name: str = "",
    direction: str = "",
    core_question: str = "",
    sub_questions: list[str] | None = None,
    research_scope: str = "",
    market: str = "cn",
    start_phase: str | None = None,
) -> dict:
    """课题制 IC 研究管线入口（新版）。每次运行处理一个课题。

    参数:
        job_id: 任务 ID
        topic_name: 课题名称，如 "HBM供需与国产替代研究"
        direction: 所属方向，如 "AI芯片"、"可控核聚变"
        core_question: 核心问题，如 "HBM是AI算力瓶颈还是阶段性短缺？"
        sub_questions: 子问题列表，每个子问题是一个研究方向
        research_scope: 研究范围说明
        market: 市场区域（cn/hk/us）
        start_phase: 断点续跑起始 phase
    """
    profile = ICTopicProfile(runtime_root=RUNTIME_ROOT)
    job_ctx = JobContext(
        job_id=job_id,
        entity=topic_name,
        query=core_question,
        market=market,
        metadata={
            "direction": direction,
            "core_question": core_question,
            "sub_questions": sub_questions or [],
            "research_scope": research_scope,
            "market": market,
        },
    )
    return run_pipeline(profile=profile, job_ctx=job_ctx,
                        runtime_root=RUNTIME_ROOT, start_phase=start_phase)
