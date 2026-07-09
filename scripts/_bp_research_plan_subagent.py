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
        "presearch_available": (task_dir / "bp_presearch_results.json").exists(),
        "presearch_path": str(task_dir / "bp_presearch_results.json") if (task_dir / "bp_presearch_results.json").exists() else None,
    }


def bp_build_research_plan_instruction(
    entity: str,
    market: str,
    stage_tier: str,
    job_id: str,
    task_dir: Path,
    brief_path: Path,
    presearch_available: bool,
    has_skeleton: bool,
    skeleton_path: Path | None,
) -> str:
    """Build the main-AI instruction for dispatching the research plan subagent."""

    presearch_path = task_dir / "bp_presearch_results.json"

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
        "- connectorIds = ['tyc-mcp', 'westock-mcp']\n"
        "- prompt = the FULL prompt below (do not truncate)\n"
        "\n"
        "### Subagent Prompt (copy ALL to the Agent):\n"
        "\n"
        "---\n"
        "\n"
        f"You are an investment due diligence researcher. Generate a complete research plan for {entity}.\n"
        "\n"
        "## Input Files (must read)\n"
        "\n"
        f"1. `{brief_path}` - brief with entity info, stage tier, industry, founders\n"
        f"2. `{task_dir / 'bp_ocr_text.txt'}` - BP pitch deck OCR full text\n"
        f"3. `{task_dir / 'bp_step0_profile.json'}` - structured company profile\n"
    )

    if presearch_available:
        instruction += f"4. `{presearch_path}` - phase03 web presearch results (reference only)\n"
    if has_skeleton and skeleton_path:
        instruction += f"5. `{skeleton_path}` - legacy script skeleton (reference claim structure only)\n"

    instruction += (
        "\n"
        "## Step 0: Read ALL Input Files FIRST\n"
        "\n"
        "Before any search, you MUST read the files listed above. From the brief, extract:\n"
        "- entity name, stage_tier, industry, sub_industry, founders, products, competitors\n"
        "- Use stage_tier to determine how strict your verification should be\n"
        "Then read bp_ocr_text.txt for the company's key claims and bp_step0_profile.json for structured data.\n"
        "\n"
        "## Search Strategy (execute strictly in this order)\n"
        "\n"
        "### Step 1: Company Verification (tyc-mcp)\n"
        f'- tyc-mcp.search_companies: query "{entity}" -> get company_id\n'
        "- tyc-mcp.get_company_basic_profile: full registration, shareholders, legal risks\n"
        "- Key fields: registered capital, establishment date, business scope, shareholders, financing history, legal risks\n"
        "- If tyc finds nothing (early stage company): note it, do NOT skip, move to Step 2\n"
        "\n"
        "### Step 2: Industry Data (westock-mcp)\n"
        "- westock-mcp.data_sector: search by industry name from the brief (sub_industry field)\n"
        "- westock-mcp.data_report: search research reports about the industry AND competitor names from the brief\n"
        f'- Cross-check: does sector PE/valuation align with what {entity} claims in the BP?\n'
        "\n"
        "### Step 3: Web Supplement (use BOTH Chinese and English queries)\n"
        f'- web_search: "{entity} 融资 估值 投资人 2025 2026"\n'
        f'- web_search: "{entity} funding valuation investors 2025 2026"\n'
        f'- web_search: "{entity} 竞品 对比 市场份额"\n'
        f'- web_search: "{entity} vs competitors market share"\n'
        f'- web_search: "{entity} 客户 订单 交付 合同"\n'
        f'- web_search: "{entity} customers orders contracts revenue"\n'
        f'- web_search: "{entity} 专利 技术 壁垒 知识产权"\n'
        f'- web_search: "{entity} technology patents IP moat"\n'
        f'- web_search: "[industry from brief] 市场规模 增长 趋势"\n'
        f'- web_search: "[industry from brief] market size growth trend"\n'
        "\n"
        "### Step 4: Tencent News (real-time Chinese news via Bash)\n"
        "Run these two Bash commands to get latest news:\n"
        "```bash\n"
        f'cd {task_dir.parent.parent} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, \'.\')\n"
        "from scripts.search_gateway import tencent_news_search\n"
        f\'result = tencent_news_search(\'"{entity}" 融资 产品 合作 最新\', max_results=5)\n'
        "print(json.dumps(result, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "```bash\n"
        f'cd {task_dir.parent.parent} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, \'.\')\n"
        "from scripts.search_gateway import tencent_news_search\n"
        f\'result = tencent_news_search(\'"[industry from brief]" 行业 政策 动态\', max_results=5)\n'
        "print(json.dumps(result, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "\n"
        "## Analysis & Output\n"
        "\n"
        "After completing all searches, synthesize findings into a research plan:\n"
        "\n"
        "1. **Claim Design**: Extract claims from BP OCR; cross-check with tyc/westock data; design at least 10 verification claims (BC001-BC01X)\n"
        "2. **Strategic Questions**: Design 5 sharp questions (ESQ1-ESQ5) that could change the investment conclusion, using contradictions between BP claims and external data\n"
        "3. **Fact Requirements**: Define at least 30 fact_keys needed to verify all claims\n"
        "4. **Section Assignment**: Map claims/questions to 8 dimension sections\n"
        "5. **Priority**: Set per claim based on BP emphasis, data coverage, and stage_tier\n"
        "\n"
        "## Stage Tier Rules\n"
        "- T1/T2 (seed/angel/Pre-A/Series A): relax verification; focus on team+tech+market; tyc failure is acceptable\n"
        "- T3/T4 (Series B+): strict verification; tyc/westock data is mandatory; focus on finance+customers+compliance\n"
        "\n"
        "## Output File\n"
        "\n"
        f"Write to `{task_dir / 'bp_research_plan.json'}` with this schema:\n"
        "\n"
        "```json\n"
        "{\n"
        f'  "schema_version": "bp_research_plan.v3",\n'
        f'  "task_id": "{job_id}",\n'
        f'  "entity": "{entity}",\n'
        f'  "market": "{market}",\n'
        f'  "stage_tier": "{stage_tier}",\n'
        f'  "data_sources_used": ["tyc-mcp:company", "westock-mcp:industry/reports", "web_search:public"],\n'
        f'  "core_questions": [{{\n'
        f'    "question_id": "CQ1",\n'
        f'    "question": "Does the company legally exist and operate compliantly?",\n'
        f'    "priority": "critical",\n'
        f'    "owner_section": "bp_company_team_compliance",\n'
        f'    "required_fact_keys": ["company_existence", "registration_info", "compliance_record"]\n'
        f'  }}],\n'
        f'  "strategic_questions": [\n'
        f'    {{"question_id": "ESQ1", "question": "...", "priority": "high", "owner_section": "bp_xxx", "required_fact_keys": [...], "decision_relevance": "..."}}\n'
        f'  ],\n'
        f'  "fact_requirements": [\n'
        f'    {{"fact_key": "company_existence", "label": "Business verification", "domain": "background", "required_for_stage": "T1-T4"}}\n'
        f'  ],\n'
        f'  "section_requirements": {{}},\n'
        f'  "claim_matrix": [\n'
        f'    {{"claim_id": "BC001", "claim": "...", "owner_section": "bp_xxx", "priority": "critical", "source": "bp_claim|external", "status": "planned", "required_fact_keys": [...]}}\n'
        f'  ],\n'
        f'  "plan_status": "ready",\n'
        f'  "search_summary": {{"tyc_company_found": true, "westock_sector_available": true, "web_evidence_count": 0, "key_findings": []}}\n'
        f'}}\n'
        f'```\n'
        f'\n'
        f'## Constraints\n'
        f'- All owner_section: bp_company_team_compliance, bp_product_commercial, bp_tech_ip_moat, bp_market_supply_chain, bp_competition_positioning, bp_valuation_return, bp_customer_revenue_validation, bp_dealbreaker_risk\n'
        f'- Minimum: 10 claims, 30 fact_keys, covering all 8 sections\n'
        f'- If tyc cannot find company (T1/T2): note in search_summary and still generate full plan\n'
        f'- Write bp_research_plan.json directly; no need to notify main AI\n'
    )

    return instruction


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
            validation = validate_bp_research_plan_ready(plan)
            if validation["ready"]:
                plan["plan_status"] = "ready"
                plan["validation"] = validation
                write_bp_research_plan(task_dir, plan)
                return {
                    "ok": True,
                    "mode": "bp_research_plan",
                    "phase": "phase04_research_plan_collect",
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
                print(f"  WARN [bp phase04_collect] plan validation failed: {validation['errors']}", flush=True)
                return {
                    "ok": False,
                    "mode": "bp_research_plan",
                    "phase": "phase04_research_plan_collect",
                    "job_id": job_ctx.job_id,
                    "result": {"error": "plan_validation_failed", "errors": validation["errors"]},
                }
        except Exception as exc:
            print(f"  WARN [bp phase04_collect] failed to read subagent plan: {exc}", flush=True)

    # Fallback: subagent didn't produce output -> use script skeleton
    print(f"  WARN [bp phase04_collect] subagent did not produce plan, falling back to script skeleton", flush=True)
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
        "phase": "phase04_research_plan_collect",
        "job_id": job_ctx.job_id,
        "result": {"plan_path": str(plan_path_write), "enrichment": "fallback_script"},
    }
