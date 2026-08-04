#!/usr/bin/env python3
"""BP Research Plan subagent handlers - v5.2 (2026-07-08).

Replaces main AI enrichment with subagent dispatch.
Subagent has MCP access to westock-mcp/tyc-mcp for structured data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.profiles.base import JobContext


def bp_build_research_plan_brief(
    task_dir: Path,
    entity: str,
    market: str,
    stage_tier: str,
    job_ctx: JobContext,
    metadata: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Build context brief for the research plan subagent."""
    financing_stage = profile.get("financing_stage", "")
    industry = profile.get("industry", "") or profile.get("industry_text", "")
    sub_industry = profile.get("sub_industry", "")
    products = profile.get("product_service", [])
    founders = profile.get("team_highlights", [])
    competitors = profile.get("competitors", [])
    advantages = profile.get("competitive_advantages", [])
    revenue_model = profile.get("revenue_model", "")

    has_claim_inventory = (task_dir / "bp_claim_inventory.json").exists()

    return {
        "entity": entity,
        "market": market,
        "stage_tier": stage_tier,
        "financing_stage": financing_stage,
        "industry": industry,
        "sub_industry": sub_industry,
        "products": products if isinstance(products, list) else [],
        "founders": founders if isinstance(founders, list) else [],
        "competitors": competitors if isinstance(competitors, list) else [],
        "competitive_advantages": advantages if isinstance(advantages, list) else [],
        "revenue_model": revenue_model,
        "has_claim_inventory": has_claim_inventory,
        "input_files": {
            "ocr_text": str(task_dir / "bp_ocr_text.txt"),
            "profile": str(task_dir / "bp_step0_profile.json"),
            "claim_inventory": str(task_dir / "bp_claim_inventory.json") if has_claim_inventory else None,
        },
    }


def bp_build_research_plan_instruction(
    entity: str,
    market: str,
    stage_tier: str,
    job_id: str,
    task_dir: Path,
    brief_path: Path,
    has_skeleton: bool,
    skeleton_path: Path | None,
) -> str:
    """Build the main-AI instruction for dispatching the research plan subagent.

    v13 (2026-08-04): 子代理 prompt 主体从硬编码英文字符串迁移到
    instruction_store_bp/bp_r00_research_plan.md 模板（与其它角色指令同库同骨架），
    此处只做占位符替换。模板缺失时报配置缺口，不复用其它角色 prompt。
    """
    project_root = Path(__file__).resolve().parent.parent
    template_path = project_root / "instruction_store_bp" / "bp_r00_research_plan.md"
    if not template_path.exists():
        raise FileNotFoundError(
            f"BP research plan instruction template missing: {template_path}. "
            "Do not reuse another BP role prompt; stop and report this configuration gap."
        )
    prompt_body = template_path.read_text(encoding="utf-8")

    skeleton_note = ""
    if has_skeleton and skeleton_path:
        skeleton_note = f"4. `{skeleton_path}` — legacy 脚本骨架（仅参考 claim 结构）\n"

    # 占位符替换用 replace 链（模板含 JSON 大括号，不能用 str.format）
    replacements = {
        "{ENTITY}": entity,
        "{MARKET}": market,
        "{TASK_DIR}": str(task_dir),
        "{TASK_ID}": job_id,
        "{BRIEF_PATH}": str(brief_path),
        "{PROJECT_ROOT}": str(project_root),
        "{SKELETON_NOTE}": skeleton_note,
    }
    for placeholder, value in replacements.items():
        prompt_body = prompt_body.replace(placeholder, value)
    # stage_tier 在 schema 示例中留空待填；此处显式写入 brief 判定值
    prompt_body = prompt_body.replace('"stage_tier": "",', f'"stage_tier": "{stage_tier}",')

    instruction = (
        "PHASE04 BP RESEARCH PLAN - Dispatch Subagent\n"
        "\n"
        "## Dispatch\n"
        "\n"
        "Use the Agent tool to spawn a subagent that generates the due diligence research plan:\n"
        "\n"
        "Agent tool params:\n"
        f"- name = 'bp-research-planner'\n"
        f"- team_name = 'bp-{job_id}'\n"
        "- mode = 'bypassPermissions'\n"
        "- connectorIds = ['tyc-mcp', 'westock-mcp', 'ima-mcp']\n"
        "- prompt = the FULL prompt below (do not truncate)\n"
        "\n"
        "### Subagent Prompt (copy ALL to the Agent):\n"
        "\n"
        "---\n"
        "\n"
        + prompt_body
    )

    return instruction


def _normalize_research_plan_schema(plan: dict[str, Any]) -> dict[str, Any]:
    """归一化子代理产出的 research plan schema，对齐管线期望字段名。

    子代理（LLM）经常自由发挥字段名，这里做兜底映射：
    - claim_matrix: id→claim_id, section→owner_section, risk_level→priority, current_status→status
    - fact_requirements: key→fact_key, description→label, source_hint→domain
    - core_questions/strategic_questions: section→owner_section, fact_keys→required_fact_keys
    """
    section_keys = {
        "bp_company_team_compliance", "bp_product_commercial", "bp_tech_ip_moat",
        "bp_market_supply_chain", "bp_competition_positioning", "bp_valuation_return",
        "bp_dealbreaker_risk",
        # 投资叙事层（Wave 4）
        "bp_consensus_challenge", "bp_catalyst", "bp_industry_research",
    }

    # ── claim_matrix 归一化 ──
    for i, claim in enumerate(plan.get("claim_matrix", [])):
        # id → claim_id
        if "claim_id" not in claim and "id" in claim:
            claim["claim_id"] = claim.pop("id")
        # 确保 claim_id 有 BC 前缀
        cid = claim.get("claim_id", "")
        if cid and not cid.startswith("BC"):
            claim["claim_id"] = f"BC{cid}" if cid.isdigit() else f"BC_{cid}"

        # section → owner_section
        if "owner_section" not in claim and "section" in claim:
            claim["owner_section"] = claim.pop("section")

        # risk_level → priority
        if "priority" not in claim and "risk_level" in claim:
            claim["priority"] = claim.pop("risk_level")

        # current_status → status
        if "status" not in claim and "current_status" in claim:
            claim["status"] = claim.pop("current_status")

        # 补齐缺失字段
        if "source" not in claim:
            claim["source"] = "bp_claim|external"
        if "required_fact_keys" not in claim:
            claim["required_fact_keys"] = []

    # ── fact_requirements 归一化 ──
    for fact in plan.get("fact_requirements", []):
        # key → fact_key
        if "fact_key" not in fact and "key" in fact:
            fact["fact_key"] = fact.pop("key")

        # description → label
        if "label" not in fact and "description" in fact:
            fact["label"] = fact.pop("description")

        # source_hint → domain
        if "domain" not in fact and "source_hint" in fact:
            fact["domain"] = fact.pop("source_hint")

        # required (bool) → required_for_stage
        if "required_for_stage" not in fact:
            req = fact.pop("required", None)
            if req is True:
                fact["required_for_stage"] = "T1-T4"
            elif req is False:
                fact["required_for_stage"] = ""
            else:
                fact["required_for_stage"] = ""

    # ── core_questions / strategic_questions 归一化 ──
    cq_count = len(plan.get("core_questions", []))
    all_questions = list(plan.get("core_questions", [])) + list(plan.get("strategic_questions", []))
    for qi, q in enumerate(all_questions):
        # section → owner_section
        if "owner_section" not in q and "section" in q:
            q["owner_section"] = q.pop("section")

        # fact_keys → required_fact_keys
        if "required_fact_keys" not in q and "fact_keys" in q:
            q["required_fact_keys"] = q.pop("fact_keys")
        if "required_fact_keys" not in q:
            q["required_fact_keys"] = []

        # required_fact_keys 为空时从已有 fact_requirements 按 section 匹配
        if not q.get("required_fact_keys") and q.get("owner_section"):
            sec = q["owner_section"]
            matched = [
                f.get("fact_key") for f in plan.get("fact_requirements", [])
                if f.get("owner_section") == sec and f.get("fact_key")
            ]
            q["required_fact_keys"] = matched[:5] if matched else [f"{sec}_overview"]
            # 如果生成了新 key，加进 fact_requirements
            if not matched:
                plan.setdefault("fact_requirements", []).append({
                    "fact_key": f"{sec}_overview",
                    "label": f"{sec} 综合评估",
                    "domain": "general",
                    "owner_section": sec,
                    "required_for_stage": "T1-T4",
                })

        # priority 缺失时补 "high"
        if "priority" not in q:
            q["priority"] = "high"

        # 确保 question_id 存在
        if "question_id" not in q:
            prefix = "CQ" if qi < cq_count else "ESQ"
            q["question_id"] = f"{prefix}{qi + 1:02d}"

    # ── coverage_matrix 自动构建（从 questions 派生）──
    if not plan.get("coverage_matrix"):
        coverage = {}
        for q in all_questions:
            qid = q.get("question_id")
            owner = q.get("owner_section", "")
            if qid and owner:
                coverage[qid] = {
                    "owner": owner,
                    "supporting_sections": [],
                    "required_fact_keys": q.get("required_fact_keys", []),
                    "priority": q.get("priority", "high"),
                }
        plan["coverage_matrix"] = coverage

    return plan


def bp_collect_research_plan(
    runtime_root: Path,
    job_ctx: JobContext,
    task_dir: Path,
    entity: str,
    market: str,
    metadata: dict[str, Any],
    _load_bp_profile_fn,
) -> dict[str, Any]:
    """Phase04 collect v5.2: read subagent output (bp_research_plan.json)."""
    from scripts.bp_research_planner import validate_bp_research_plan_ready, write_bp_research_plan

    plan_path = task_dir / "bp_research_plan.json"

    if plan_path.exists() and plan_path.stat().st_size > 200:
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            # 归一化子代理产出的 schema（字段名对齐管线期望格式）
            plan = _normalize_research_plan_schema(plan)
            validation = validate_bp_research_plan_ready(plan)
            if validation["ready"]:
                plan["plan_status"] = "ready"
                plan["validation"] = validation
                write_bp_research_plan(task_dir, plan)
                return {
                    "ok": True,
                    "mode": "bp_research_plan",
                    "phase": "phase05_research_plan_collect",
                    "job_id": job_ctx.job_id,
                    "result": {
                        "plan_path": str(plan_path),
                        "plan_status": "ready",
                        "enrichment_status": "subagent_generated",
                        "validation": validation,
                        "claims": len(plan.get("claim_matrix", [])),
                        "facts": len(plan.get("fact_requirements", [])),
                        "questions": len(plan.get("strategic_questions", [])),
                    },
                }
            else:
                # 断点修复（2026-08-03）：子代理产出了 plan 但校验不过时，
                # 旧逻辑直接 ok=False 终止管线（此时子代理已跑完，重派无意义）。
                # 改为降级走下方 skeleton 兜底，保住管线——plan 质量降级但可用，
                # 审计信息记录在 result.subagent_validation_errors 中。
                print(
                    f"  ⚠️ [bp phase04_collect] 子代理 plan 校验失败: {validation['errors']}，"
                    f"降级为 script skeleton 兜底（不终止管线）",
                    flush=True,
                )
        except Exception as exc:
            print(f"  WARN [bp phase04_collect] failed to read subagent plan: {exc}", flush=True)

    # Fallback: subagent didn't produce output (or produced invalid output) -> use script skeleton
    print(f"  WARN [bp phase04_collect] subagent plan unavailable or invalid, falling back to script skeleton", flush=True)
    from scripts.bp_research_planner import build_bp_research_plan
    from scripts.bp_stage_utils import read_stage_from_task

    profile = _load_bp_profile_fn(task_dir)
    stage_tier = read_stage_from_task(task_dir)
    claim_inventory_path = task_dir / "bp_claim_inventory.json"
    claim_inventory = None
    if claim_inventory_path.exists():
        try:
            claim_inventory = json.loads(claim_inventory_path.read_text(encoding="utf-8"))
        except Exception:
            claim_inventory = None

    plan = build_bp_research_plan(
        task_id=job_ctx.job_id, entity=entity,
        query=getattr(job_ctx, "query", "") or metadata.get("query", ""),
        market=market, input_file=metadata.get("input_file", ""),
        profile=profile, claim_inventory=claim_inventory, stage_tier=stage_tier,
    )
    plan_path_write = write_bp_research_plan(task_dir, plan)
    return {
        "ok": plan.get("plan_status") == "ready",
        "mode": "bp_research_plan",
        "phase": "phase05_research_plan_collect",
        "job_id": job_ctx.job_id,
        "result": {"plan_path": str(plan_path_write), "enrichment": "fallback_script"},
    }
