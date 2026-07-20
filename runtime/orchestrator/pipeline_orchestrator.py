#!/usr/bin/env python3
"""
Pipeline Orchestrator — 常驻主控 Agent

统一 IR/BP/IC 管线入口，负责：
1. 路由：根据 input_file / query 关键词判断 IR / BP / IC
2. 生命周期：创建 → 运行 → 监控 → 完成/失败
3. 恢复：检测未完成任务，自动 resume
4. 降级：旧 ir_auto_orchestrator 作为 fallback

使用方式：
    # IR 任务
    python3 pipeline_orchestrator.py submit --entity 宁德时代 --market cn --query 动力电池

    # BP 任务
    python3 pipeline_orchestrator.py submit --entity 星际荣耀 --market cn --input-file /path/to/bp.pdf

    # IC（行业研究）任务
    python3 pipeline_orchestrator.py submit --entity 半导体 --market cn --query "行业研究"

    # LIT（文献综述/技术评估）任务
    python3 pipeline_orchestrator.py submit --entity 固态电池 --market cn --query "技术评估"

    # 恢复未完成任务
    python3 pipeline_orchestrator.py recover

    # 查看状态
    python3 pipeline_orchestrator.py status --job-id TASK-20260415-001
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "jobs"


class JobType(str, Enum):
    IR = "ir"
    BP = "bp"
    IC = "ic"
    LIT = "lit"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class JobRecord:
    job_id: str
    job_type: JobType
    entity: str
    market: str
    status: JobStatus = JobStatus.PENDING
    query: str = ""
    input_file: str = ""
    current_phase: str = ""
    failed_phase: str = ""
    error: str = ""
    workspace_root: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["job_type"] = self.job_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobRecord":
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in field_names}
        if isinstance(filtered.get("job_type"), str):
            filtered["job_type"] = JobType(filtered["job_type"])
        if isinstance(filtered.get("status"), str):
            filtered["status"] = JobStatus(filtered["status"])
        return cls(**filtered)


class PipelineOrchestrator:
    """常驻主控：路由 + 生命周期 + 恢复"""

    def __init__(self, runtime_root: Path | None = None):
        self.runtime_root = runtime_root or ROOT
        self.jobs_dir = self.runtime_root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    # ── 路由 ──────────────────────────────────────────
    # IC（行业研究）关键词
    _IC_KEYWORDS = {"行业研究", "产业分析", "赛道扫描", "行业分析", "产业研究",
                     "行业报告", "产业链分析", "产业链", "行业深度", "行业概览",
                     "产业全景", "标的对标", "行业对比", "赛道深度", "赛道分析",
                     "产业对标", "行业对标", "全景研究", "赛道研究", "行业扫描",
                     "industry research", "sector analysis", "industry overview",
                     "sector deep dive", "industry landscape"}

    # LIT（文献综述/技术评估）关键词
    _LIT_KEYWORDS = {"技术评估", "文献综述", "技术尽调", "技术调研", "技术全景",
                      "技术成熟度", "trl", "技术路线分析", "竞争格局分析",
                      "tech assessment", "literature review", "tech due diligence",
                      "technology landscape", "tech readiness"}

    def classify_job(self, input_file: str = "", query: str = "",
                     force_pipeline: str = "") -> JobType:
        """判断是 IR / BP / IC / LIT 任务。

        force_pipeline: 强制指定管线类型（bp/ir/ic/lit），跳过自动分类。
        用于公司名模式 BP：无 input_file 但用户明确要走 BP 管线。
        """
        # 强制指定管线（公司名模式 BP 的核心入口）
        if force_pipeline:
            fp = force_pipeline.strip().upper()
            try:
                return JobType(fp)
            except ValueError:
                pass  # 无效值，回退到自动分类

        if input_file:
            ext = Path(input_file).suffix.lower()
            if ext in (".pdf", ".pptx", ".ppt", ".docx", ".doc", ".png", ".jpg", ".jpeg"):
                return JobType.BP
        if query:
            query_lower = query.lower()
            # LIT 优先于 IC（"技术评估" 比 "行业研究" 更具体）
            for kw in self._LIT_KEYWORDS:
                if kw in query_lower:
                    return JobType.LIT
            for kw in self._IC_KEYWORDS:
                if kw in query_lower:
                    return JobType.IC
        return JobType.IR

    # ── 任务注册 ──────────────────────────────────────
    def _next_job_id(self) -> str:
        today = time.strftime("%Y%m%d")
        existing = []
        for p in self.jobs_dir.glob("*/job_record.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                jid = d.get("job_id", "")
                if jid.startswith(f"TASK-{today}-"):
                    existing.append(int(jid.rsplit("-", 1)[1]))
            except Exception:
                pass
        n = max(existing) + 1 if existing else 1
        return f"TASK-{today}-{n:03d}"

    def _job_record_path(self, job_id: str) -> Path:
        return self.jobs_dir / job_id / "job_record.json"

    def _save_record(self, record: JobRecord):
        record.updated_at = time.time()
        path = self._job_record_path(record.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_record(self, job_id: str) -> Optional[JobRecord]:
        path = self._job_record_path(job_id)
        if not path.exists():
            return None
        try:
            return JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    # ── 提交任务 ──────────────────────────────────────
    def submit(
        self,
        entity: str,
        market: str = "cn",
        query: str = "",
        input_file: str = "",
        job_id: str | None = None,
        force_pipeline: str = "",
        **kwargs,
    ) -> JobRecord:
        """提交一个新任务。

        force_pipeline: 强制指定管线（bp/ir/ic/lit），跳过自动分类。
        用于公司名模式 BP：无 input_file 但用户明确要走 BP 管线。
        """
        job_type = self.classify_job(input_file, query, force_pipeline=force_pipeline)
        job_id = job_id or self._next_job_id()

        record = JobRecord(
            job_id=job_id,
            job_type=job_type,
            entity=entity,
            market=market,
            query=query,
            input_file=input_file,
            workspace_root=str(self.jobs_dir / job_id),
        )
        self._save_record(record)
        return record

    # ── 执行任务 ──────────────────────────────────────
    def execute(self, job_id: str, start_phase: str | None = None) -> dict[str, Any]:
        """执行任务 — 通过 shared kernel 跑管线，支持从指定 phase 断点恢复

        自动续跑逻辑：
        - 如果 start_phase 未指定，尝试从 record.result["next_phase"] 恢复
        - 避免每次都从 phase0 开始
        """
        record = self._load_record(job_id)
        if record is None:
            return {"ok": False, "error": f"Job {job_id} not found"}

        # 自动推断 start_phase：如果未指定，尝试从上次暂停的位置继续
        if start_phase is None and record.result:
            next_phase = record.result.get("next_phase")
            if next_phase:
                start_phase = next_phase
                print(f"  🔄 自动续跑：从上次暂停的下一阶段开始: {start_phase}", flush=True)

        record.status = JobStatus.RUNNING
        self._save_record(record)

        try:
            if record.job_type == JobType.IC:
                result = self._run_ic(record, start_phase=start_phase)
            elif record.job_type == JobType.IR:
                result = self._run_ir(record, start_phase=start_phase)
            elif record.job_type == JobType.LIT:
                result = self._run_lit(record, start_phase=start_phase)
            else:
                result = self._run_bp(record, start_phase=start_phase)
        except Exception as exc:
            record.status = JobStatus.FAILED
            record.error = str(exc)[:500]
            record.completed_at = time.time()
            self._save_record(record)
            return {"ok": False, "error": str(exc)[:500], "job_id": job_id}

        # 更新状态：needs_dispatch 不是失败，是正常暂停，不标记为 COMPLETED
        if result.get("ok"):
            if result.get("status") == "needs_dispatch":
                # 正常暂停，等待子代理完成
                record.status = JobStatus.PAUSED
            else:
                record.status = JobStatus.COMPLETED
                record.completed_at = time.time()
        else:
            record.status = JobStatus.FAILED
            record.failed_phase = result.get("failed_phase", "")
            record.error = str(result.get("error", ""))[:500]
            record.completed_at = time.time()

        record.result = result
        self._save_record(record)
        return result

    def _run_ir(self, record: JobRecord, start_phase: str | None = None) -> dict[str, Any]:
        """通过 shared kernel 跑 IR 管线"""
        from runtime.entrypoints.run_ir_pipeline_entry import run_ir_job

        metadata = {}
        for key in ("ticker", "english_name", "rounds", "max_new_queries",
                     "use_facts", "dispatch_max_wait", "dispatch_poll_interval", "session_id"):
            if key in record.result:
                metadata[key] = record.result[key]

        return run_ir_job(
            job_id=record.job_id,
            entity=record.entity,
            query=record.query,
            market=record.market,
            start_phase=start_phase,
            **metadata,
        )

    def _run_bp(self, record: JobRecord, start_phase: str | None = None) -> dict[str, Any]:
        """通过 shared kernel 跑 BP 管线"""
        from runtime.entrypoints.run_bp_pipeline_entry import run_bp_job

        metadata = {}
        for key in ("ticker", "english_name", "rounds", "max_new_queries",
                     "use_facts", "dispatch_max_wait", "dispatch_poll_interval", "session_id"):
            if key in record.result:
                metadata[key] = record.result[key]

        return run_bp_job(
            job_id=record.job_id,
            entity=record.entity,
            query=record.query,
            market=record.market,
            input_file=record.input_file,
            start_phase=start_phase,
            **metadata,
        )

    def _run_ic(self, record: JobRecord, start_phase: str | None = None) -> dict[str, Any]:
        """通过 shared kernel 跑 IC（行业研究）管线"""
        from runtime.entrypoints.run_ic_pipeline_entry import run_ic_job

        metadata = {}
        for key in ("rounds", "max_new_queries", "use_facts",
                     "dispatch_max_wait", "dispatch_poll_interval", "session_id"):
            if key in record.result:
                metadata[key] = record.result[key]

        # 把 input_file 传给 topic_file，让 phase01 能从 DOCX 解析 research_content
        if record.input_file:
            metadata["topic_file"] = record.input_file

        return run_ic_job(
            job_id=record.job_id,
            entity=record.entity,
            query=record.query,
            market=record.market,
            start_phase=start_phase,
            **metadata,
        )

    def _run_lit(self, record: JobRecord, start_phase: str | None = None) -> dict[str, Any]:
        """通过 shared kernel 跑 LIT（文献综述/技术评估）管线"""
        from runtime.entrypoints.run_lit_pipeline_entry import run_lit_job

        metadata = {}
        for key in ("focus_dimensions", "language"):
            if key in record.result:
                metadata[key] = record.result[key]

        return run_lit_job(
            job_id=record.job_id,
            entity=record.entity,
            query=record.query,
            market=record.market,
            start_phase=start_phase,
            **metadata,
        )

    # ── 恢复 ──────────────────────────────────────────
    def recover(self) -> list[dict[str, Any]]:
        """检测并恢复未完成任务"""
        recovered = []
        for p in self.jobs_dir.glob("*/job_record.json"):
            try:
                record = JobRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue

            if record.status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.PAUSED):
                print(f"  🔄 恢复任务: {record.job_id} ({record.job_type.value}) — {record.entity}")
                result = self.execute(record.job_id)
                recovered.append({
                    "job_id": record.job_id,
                    "type": record.job_type.value,
                    "entity": record.entity,
                    "result_ok": result.get("ok", False),
                })

        return recovered

    # ── 状态查询 ──────────────────────────────────────
    def status(self, job_id: str) -> dict[str, Any]:
        record = self._load_record(job_id)
        if record is None:
            return {"error": f"Job {job_id} not found"}

        snap = record.to_dict()

        # 读取 workspace 状态
        ws_state = self.runtime_root / "jobs" / job_id / "state"
        if ws_state.exists():
            phase_states = {}
            for p in ws_state.glob("*.json"):
                if p.name != "artifacts.json":
                    try:
                        phase_states[p.stem] = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        pass
            snap["phase_states"] = phase_states

            artifacts_path = ws_state / "artifacts.json"
            if artifacts_path.exists():
                try:
                    snap["artifacts"] = json.loads(artifacts_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

        return snap

    def list_jobs(self, status_filter: str | None = None) -> list[dict]:
        jobs = []
        for p in sorted(self.jobs_dir.glob("*/job_record.json")):
            try:
                record = JobRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
                if status_filter and record.status.value != status_filter:
                    continue
                jobs.append(record.to_dict())
            except Exception:
                continue
        return jobs


def main():
    ap = argparse.ArgumentParser(description="Pipeline Orchestrator — 常驻主控")
    sub = ap.add_subparsers(dest="cmd")

    # submit
    s = sub.add_parser("submit", help="提交新任务")
    s.add_argument("--entity", required=True)
    s.add_argument("--market", default="cn", choices=["cn", "us", "hk"])
    s.add_argument("--query", default="")
    s.add_argument("--input-file", default="", help="BP 文件路径（有则走 BP 管线）")
    s.add_argument("--pipeline", default="", choices=["bp", "ir", "ic", "lit"],
                    help="强制指定管线类型（公司名模式 BP 用 --pipeline bp）")
    s.add_argument("--job-id", default=None)
    s.set_defaults(func=cmd_submit)

    # execute
    e = sub.add_parser("execute", help="执行任务")
    e.add_argument("--job-id", required=True)
    e.add_argument("--start-phase", default=None, help="从指定 phase 断点恢复")
    e.set_defaults(func=cmd_execute)

    # recover
    r = sub.add_parser("recover", help="恢复未完成任务")
    r.set_defaults(func=cmd_recover)

    # status
    st = sub.add_parser("status", help="查看任务状态")
    st.add_argument("--job-id", required=True)
    st.set_defaults(func=cmd_status)

    # list
    l = sub.add_parser("list", help="列出所有任务")
    l.add_argument("--status", default=None, choices=["pending", "running", "completed", "failed", "paused"])
    l.set_defaults(func=cmd_list)

    # ---- 近似参数拦截 ----
    # 捕获所有未被识别的参数，检查是否是常见误用
    known_args, unknown = ap.parse_known_args()
    if unknown:
        _NEAR_MISS = {
            "--resume-from": "--start-phase",
            "--resume": "--start-phase",
            "--continue-from": "--start-phase",
            "--from-phase": "--start-phase",
            "--start_phase": "--start-phase",   # underscore → hyphen
        }
        for arg in unknown:
            key = arg.split("=")[0]  # 支持 --resume-from=xxx 形式
            if key in _NEAR_MISS:
                correct = _NEAR_MISS[key]
                print(f"❌ 未知参数: {key}\n   你是不是想用 {correct}？", file=__import__("sys").stderr)
                return
        # 非已知近似参数 → 标准 argparse 报错
        ap.error(f"unrecognized arguments: {' '.join(unknown)}")

    if not hasattr(known_args, "func"):
        ap.print_help()
        return
    known_args.func(known_args)


def cmd_submit(args):
    orch = PipelineOrchestrator()
    record = orch.submit(
        entity=args.entity,
        market=args.market,
        query=args.query,
        input_file=args.input_file,
        job_id=args.job_id,
        force_pipeline=getattr(args, "pipeline", ""),
    )
    print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))


def cmd_execute(args):
    orch = PipelineOrchestrator()
    result = orch.execute(args.job_id, start_phase=getattr(args, 'start_phase', None))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def cmd_recover(args):
    orch = PipelineOrchestrator()
    recovered = orch.recover()
    if not recovered:
        print("没有需要恢复的任务")
    else:
        print(json.dumps(recovered, ensure_ascii=False, indent=2))


def cmd_status(args):
    orch = PipelineOrchestrator()
    snap = orch.status(args.job_id)
    print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))


def cmd_list(args):
    orch = PipelineOrchestrator()
    jobs = orch.list_jobs(status_filter=args.status)
    if not jobs:
        print("(no jobs)")
    else:
        for j in jobs:
            marker = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "paused": "⏸️",
            }.get(j.get("status", ""), "?")
            print(f"  {marker} {j['job_id']} [{j['job_type']}] {j['entity']} — {j['status']}")


if __name__ == "__main__":
    main()
