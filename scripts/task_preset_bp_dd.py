#!/usr/bin/env python3
"""
BP DD 管线任务树预设

为完整 BP 尽调链路创建任务树：
phase01_document_intake -> phase03_research_plan -> phase04_presearch
-> Wave 1/2 dispatch/collect -> shared page/gates -> synthesis -> IC/RedTeam/reviews -> phase31_delivery

注：phase02_company_verify 已于 2026-07-29 删除（tyc 工商核验由 phase04 research_plan 子代理直调）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import sys

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.task_registry import TaskRegistry


def create_bp_dd_tasks(task_id: str, reg: Optional["TaskRegistry"] = None) -> "TaskRegistry":
    """为 BP DD 管线创建当前完整任务树。"""
    if reg is None:
        reg = TaskRegistry()

    pipeline = f"bp_dd_{task_id}"

    existing = [t for t in reg.list_all() if t.pipeline == pipeline]
    for task in existing:
        reg.delete(task.id)

    def create(subject: str, description: str, active_form: str, phase: str, blocked_by: list[int]):
        return reg.create(
            subject=subject,
            description=description,
            active_form=active_form,
            phase=phase,
            pipeline=pipeline,
            blocked_by=blocked_by,
            metadata={"step": phase},
        )

    intake = create(
        subject="文档入库与 OCR",
        description="解析 BP 文件并抽取 step0 结构化画像",
        active_form="正在解析 BP 文档",
        phase="phase01_document_intake",
        blocked_by=[],
    )

    research_plan = create(
        subject="研究计划",
        description="生成 BP 尽调研究计划、claim matrix、fact requirements 和 section requirements",
        active_form="正在生成研究计划",
        phase="phase03_research_plan",
        blocked_by=[intake.id],
    )

    presearch = create(
        subject="预搜索",
        description="围绕 BP 核心 claim、公司主体和关键市场问题生成共享搜索底稿",
        active_form="正在做预搜索",
        phase="phase04_presearch",
        blocked_by=[research_plan.id],
    )

    shared_init = create(
        subject="初始化共享尽调页",
        description="创建共享尽调页、共享状态和 claim coverage 初始文件",
        active_form="正在初始化共享尽调页",
        phase="phase06_bp_shared_page_init",
        blocked_by=[presearch.id],
    )

    fact_bootstrap = create(
        subject="初始化事实库",
        description="根据 BP 画像和研究计划初始化 fact store 与 fact index",
        active_form="正在初始化事实库",
        phase="phase08_bp_fact_store_bootstrap",
        blocked_by=[shared_init.id],
    )

    wave1_prepare = create(
        subject="Wave 1 四维证据采集派发",
        description="派发公司团队合规、产品商业化、技术IP壁垒、市场供应链四个证据采集维度",
        active_form="正在派发 Wave 1 子任务",
        phase="phase09_dispatch_prepare",
        blocked_by=[fact_bootstrap.id],
    )

    wave1_collect = create(
        subject="Wave 1 四维证据采集收集",
        description="确认 Wave 1 四个维度输出齐全并做基础质量检查",
        active_form="正在收集 Wave 1 输出",
        phase="phase10_dispatch_collect",
        blocked_by=[wave1_prepare.id],
    )

    shared_refresh_1 = create(
        subject="刷新共享尽调页 Wave 1",
        description="把 Wave 1 输出合并进共享尽调页、共享状态和 claim coverage",
        active_form="正在刷新共享尽调页",
        phase="phase12_bp_shared_page_refresh",
        blocked_by=[wave1_collect.id],
    )

    wave3_prepare = create(
        subject="Wave 3 竞争+估值推理派发",
        description="派发竞争定位、估值回报两个跨维分析任务，读取 Wave 1 输出",
        active_form="正在派发 Wave 3 子任务",
        phase="phase14_wave3_prepare",
        blocked_by=[shared_refresh_1.id],
    )

    wave3_collect = create(
        subject="Wave 3 竞争+估值推理收集",
        description="确认 Wave 3 竞争/估值两个维度输出齐全并做基础质量检查",
        active_form="正在收集 Wave 3 输出",
        phase="phase15_wave3_collect",
        blocked_by=[wave3_prepare.id],
    )

    wave3_evidence_gate = create(
        subject="Wave 3 证据门禁",
        description="检查 Wave 3 竞争+估值输出是否达到质量要求",
        active_form="正在执行 Wave 3 证据门禁",
        phase="phase16_wave3_evidence_gate",
        blocked_by=[wave3_collect.id],
    )

    shared_refresh_3 = create(
        subject="刷新共享尽调页 Wave 3",
        description="把 Wave 2-3 输出合并进共享尽调页、共享状态和 claim coverage",
        active_form="正在三次刷新共享尽调页",
        phase="phase17_wave3_shared_page_refresh",
        blocked_by=[wave3_evidence_gate.id],
    )

    wave4_prepare = create(
        subject="Wave 4 Deal Breaker 派发",
        description="派发 Deal Breaker 反向论证任务，读取 Wave 1-3 全量输出",
        active_form="正在派发 Wave 4 子任务",
        phase="phase18_wave4_prepare",
        blocked_by=[shared_refresh_3.id],
    )

    wave4_collect = create(
        subject="Wave 4 Deal Breaker 收集",
        description="确认 Wave 4 Deal Breaker 输出齐全并做基础质量检查",
        active_form="正在收集 Wave 4 输出",
        phase="phase19_wave4_collect",
        blocked_by=[wave4_prepare.id],
    )

    wave4_evidence_gate = create(
        subject="Wave 4 证据门禁",
        description="检查 Wave 4 Deal Breaker 输出是否达到质量要求",
        active_form="正在执行 Wave 4 证据门禁",
        phase="phase20_wave4_evidence_gate",
        blocked_by=[wave4_collect.id],
    )

    shared_refresh_4 = create(
        subject="刷新共享尽调页 Wave 4",
        description="把 Wave 4 输出合并进共享尽调页、共享状态和 claim coverage",
        active_form="正在四次刷新共享尽调页",
        phase="phase21_wave4_shared_page_refresh",
        blocked_by=[wave4_evidence_gate.id],
    )

    claim_gate = create(
        subject="BP Claim 覆盖门禁",
        description="检查关键 BP claim 是否已被八维输出覆盖、验证或明确列为 data gap",
        active_form="正在检查 BP claim 覆盖",
        phase="phase22_bp_claim_coverage_validation",
        blocked_by=[shared_refresh_4.id],
    )

    fact_merge = create(
        subject="事实库合并",
        description="合并八维输出 sidecar 和共享页事实，生成统一 fact store",
        active_form="正在合并事实库",
        phase="phase12_bp_fact_store_merge",
        blocked_by=[claim_gate.id],
    )

    section_gate = create(
        subject="Section Package 门禁",
        description="验证 section package 的 claim、fact、答案边界和叙事块结构",
        active_form="正在验证 section package",
        phase="phase24_bp_section_package_validation",
        blocked_by=[fact_merge.id],
    )

    cross_gate = create(
        subject="跨维度一致性门禁",
        description="检查估值、客户收入、竞争定位、风险结论之间的跨维度矛盾",
        active_form="正在检查跨维度一致性",
        phase="phase23_bp_cross_dimension_gate",
        blocked_by=[section_gate.id],
    )

    synthesis_prepare = create(
        subject="统稿派发",
        description="把 Wave 1-4 八个维度输出重组为投资逻辑连贯的 BP 尽调报告底稿",
        active_form="正在派发统稿任务",
        phase="phase25_synthesis_prepare",
        blocked_by=[cross_gate.id],
    )

    synthesis_collect = create(
        subject="统稿收集",
        description="确认 bp_synthesis.md 已生成并同步到任务根目录和 outputs 目录",
        active_form="正在收集统稿输出",
        phase="phase26_synthesis_collect",
        blocked_by=[synthesis_prepare.id],
    )

    debate_review = create(
        subject="对抗评审",
        description="执行 BP 报告对抗评审，拦截高置信低来源、逻辑漏洞和未披露数据缺口",
        active_form="正在做对抗评审",
        phase="phase27_bp_debate_review",
        blocked_by=[synthesis_collect.id],
    )

    final_assembly = create(
        subject="最终报告组装",
        description="结合统稿和门禁结果生成最终 BP 报告 markdown",
        active_form="正在组装最终报告",
        phase="phase28_bp_final_assembly",
        blocked_by=[debate_review.id],
    )

    readability = create(
        subject="可读性门禁",
        description="检查最终报告是否仍像维度拼接、是否重复事实、是否残留内部术语",
        active_form="正在检查报告可读性",
        phase="phase29_bp_readability_review",
        blocked_by=[final_assembly.id],
    )

    create(
        subject="交付报告",
        description="通过交付门禁后生成尽调报告 DOCX，并登记交付审计与附件",
        active_form="正在生成尽调报告",
        phase="phase31_delivery",
        blocked_by=[readability.id],
    )

    return reg


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", default="demo", help="Task ID prefix")
    ap.add_argument("--action", default="create", choices=["create", "list", "status"])
    args = ap.parse_args()

    reg = TaskRegistry()

    if args.action == "create":
        reg = create_bp_dd_tasks(args.task_id, reg)
        print(f"✅ 创建了 BP DD 任务树 (pipeline: bp_dd_{args.task_id})")
        print()
        reg.print_tree()
        print()
        ready = reg.get_ready_tasks(f"bp_dd_{args.task_id}")
        print(f"Ready to execute: {[f'Task {t.id}: {t.subject}' for t in ready]}")
    elif args.action == "list":
        status = reg.pipeline_status(f"bp_dd_{args.task_id}")
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif args.action == "status":
        reg.print_tree(f"bp_dd_{args.task_id}")
