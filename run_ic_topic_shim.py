#!/usr/bin/env python3
"""IC Topic (课题研究) 管线驱动 shim。

绕过 phase_runner.run_phase 的 pipeline 只认 ir/bp 的 bug：
ICTopicProfile 的 phase02_presearch / phase18_delivery 走 launch_heavy_phase
时传 pipeline='ic_topic'，phase_runner 不认识 → 回退 BP → 'Unknown phase'。

本 shim 直接用 ICTopicProfile + kernel 推进，并且：
- 调用前设置 IRBP_BG_CHILD=1，使 heavy phase 内部函数直接当前进程执行；
- 不依赖 phase_runner，避免 pipeline 映射错误。

用法:
  python3 run_ic_topic_shim.py run TASK-XXXX [--start-phase ...]
  python3 run_ic_topic_shim.py launch TASK-XXXX        # 仅发射下一个待派发角色
  python3 run_ic_topic_shim.py status TASK-XXXX
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# 让 heavy phase 内部函数直接当前进程执行（不 fork、不调 phase_runner）
os.environ["IRBP_BG_CHILD"] = "1"


def _profile():
    from runtime.profiles.ic_topic_profile import ICTopicProfile
    return ICTopicProfile(runtime_root=ROOT)


def run(job_id: str, start_phase: str | None = None) -> dict:
    from runtime.orchestrator.state_store import run_pipeline
    prof = _profile()
    job_ctx = _ctx(job_id)
    return run_pipeline(profile=prof, job_ctx=job_ctx, runtime_root=ROOT, start_phase=start_phase)


def _ctx(job_id: str) -> "JobContext":
    from runtime.profiles.base import JobContext
    plan = _read_plan(job_id)
    return JobContext(
        job_id=job_id,
        entity=plan.get("topic_name", job_id),
        query=plan.get("core_question", ""),
        market=plan.get("market", "cn"),
        metadata={
            "direction": plan.get("direction", ""),
            "core_question": plan.get("core_question", ""),
            "sub_questions": plan.get("sub_questions", []),
            "research_scope": plan.get("research_scope", ""),
            "market": plan.get("market", "cn"),
        },
    )


def _read_plan(job_id: str) -> dict:
    from runtime.orchestrator.workspace_layout import build_job_workspace
    ws = build_job_workspace(ROOT, job_id)
    p = ws.root / "research_plan.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def launch(job_id: str) -> dict:
    """发射下一个待派发角色（sequential, has_more）。"""
    from scripts.ic_topic_subagent_launcher import launch_next_wave
    prof = _profile()
    job_ctx = _ctx(job_id)
    return launch_next_wave(
        runtime_root=ROOT, job_id=job_id, entity=job_ctx.entity,
        query=job_ctx.query, market=job_ctx.market, metadata=job_ctx.metadata,
    )


def status(job_id: str) -> dict:
    from scripts.ic_topic_subagent_launcher import get_pipeline_status
    return get_pipeline_status(job_id)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("job_id")
    p_run.add_argument("--start-phase", default=None)
    p_launch = sub.add_parser("launch")
    p_launch.add_argument("job_id")
    p_status = sub.add_parser("status")
    p_status.add_argument("job_id")
    args = ap.parse_args()

    if args.cmd == "run":
        r = run(args.job_id, start_phase=args.start_phase)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3500])
    elif args.cmd == "launch":
        r = launch(args.job_id)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3500])
    elif args.cmd == "status":
        print(json.dumps(status(args.job_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
