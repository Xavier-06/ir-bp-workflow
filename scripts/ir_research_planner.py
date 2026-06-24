#!/usr/bin/env python3
"""IR quality-production research planner.

This module builds a generic investment-research plan for any listed or
private company. It intentionally avoids company-specific hardcoding: all
questions are expressed as reusable analytical dimensions and parameterized by
entity, query, market, and report_type.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"


def _question(qid: str, question: str, required_evidence: list[str], preferred_sources: list[str], minimum_sources: int = 3) -> dict[str, Any]:
    return {
        "id": qid,
        "question": question,
        "required_evidence": required_evidence,
        "preferred_sources": preferred_sources,
        "minimum_sources": minimum_sources,
    }


def _normalize_research_type(report_type: str, query: str) -> str:
    text = f"{report_type} {query}".lower()
    if "industry" in text or "行业" in text or "赛道" in text:
        return "industry_research"
    if "news" in text or "快报" in text or "事件" in text:
        return "event_update"
    return "company_deep_dive"


def _core_question(
    question_id: str,
    question: str,
    owner_section: str,
    required_fact_keys: list[str],
    supporting_sections: list[str],
    decision_relevance: str,
    priority: str = "high",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": question,
        "priority": priority,
        "owner_section": owner_section,
        "supporting_sections": supporting_sections,
        "required_fact_keys": required_fact_keys,
        "decision_relevance": decision_relevance,
    }


def _build_core_questions(entity: str) -> list[dict[str, Any]]:
    return [
        _core_question(
            "Q1",
            f"{entity} 当前业绩趋势和经营质量是否支持投资主线？",
            "step1_data",
            ["revenue_trend", "profitability", "cash_flow", "segment_performance"],
            ["step4_finance", "step6_insight"],
            "决定报告的基本面底座和收入预测方向。",
        ),
        _core_question(
            "Q2",
            f"{entity} 所处行业空间、增速和竞争格局是否提供足够上行空间？",
            "step2_industry",
            ["market_size", "growth_rate", "market_share", "competitive_landscape"],
            ["step1_data", "step6b_valuation"],
            "决定 TAM/SAM/SOM、竞争地位和估值倍数上限。",
        ),
        _core_question(
            "Q3",
            f"{entity} 的商业模式、产品结构和护城河是否可持续？",
            "step3_biz",
            ["business_model", "product_matrix", "customer_base", "pricing_power", "moat_evidence"],
            ["step2_industry", "step6_insight"],
            "决定增长质量、利润率韧性和长期竞争优势。",
        ),
        _core_question(
            "Q4",
            f"{entity} 的财务质量和关键指标是否支持估值假设？",
            "step4_finance",
            ["revenue_trend", "profitability", "cash_flow", "balance_sheet", "guidance"],
            ["step1_data", "step6b_valuation"],
            "决定盈利预测、现金流折现和财务风险判断。",
        ),
        _core_question(
            "Q5",
            f"{entity} 的治理、管理层和激励是否支持执行力判断？",
            "step5_mgmt",
            ["management_roster", "executive_changes", "ownership", "incentives", "governance_risks"],
            ["step7_risk"],
            "决定治理折价、执行风险和管理层可信度。",
        ),
        _core_question(
            "Q6",
            f"{entity} 的估值是否可复算，当前价格隐含了什么预期？",
            "step6b_valuation",
            ["valuation_multiples", "peer_set", "dcf_inputs", "target_price_range", "sensitivity"],
            ["step2_industry", "step4_finance", "step_macro"],
            "决定目标价、风险收益比和核心分歧。",
        ),
        _core_question(
            "Q7",
            f"哪些风险、反向证据或宏观变量会推翻 {entity} 的投资结论？",
            "step7_risk",
            ["bear_case", "risk_triggers", "regulatory_risks", "competitive_risks", "macro_sensitivity"],
            ["step_macro", "step5_mgmt", "step6b_valuation"],
            "决定报告是否充分处理反证和下行情景。",
        ),
    ]


def _fact_requirement(
    fact_key: str,
    description: str,
    source_priority: list[str],
    required_for: list[str],
    criticality: str = "high",
) -> dict[str, Any]:
    return {
        "fact_key": fact_key,
        "description": description,
        "source_priority": source_priority,
        "required_for": required_for,
        "criticality": criticality,
    }


def _build_fact_requirements() -> list[dict[str, Any]]:
    official = ["annual_report", "quarterly_results", "regulatory_filing", "company_ir"]
    industry = ["industry_report", "regulatory_data", "industry_association", "company_disclosure"]
    market = ["market_data", "peer_filings", "financial_database", "analyst_consensus"]
    return [
        _fact_requirement("revenue_trend", "收入规模、增速、分部/地区结构", official, ["step1_data", "step4_finance", "step6b_valuation"]),
        _fact_requirement("profitability", "毛利率、经营利润率、净利率及变化原因", official, ["step1_data", "step4_finance", "step6b_valuation"]),
        _fact_requirement("cash_flow", "经营现金流、自由现金流和资本开支", official, ["step4_finance", "step6b_valuation"]),
        _fact_requirement("segment_performance", "核心业务/产品/地区分部表现", official, ["step1_data", "step3_biz", "step4_finance"]),
        _fact_requirement("market_size", "行业规模、TAM/SAM/SOM 或可比口径", industry, ["step2_industry", "step6b_valuation"]),
        _fact_requirement("growth_rate", "行业增速、渗透率或需求驱动", industry, ["step2_industry", "step6_insight"]),
        _fact_requirement("market_share", "市场份额、排名或竞争地位", industry, ["step2_industry", "step3_biz"]),
        _fact_requirement("competitive_landscape", "主要竞争者、差异化和替代风险", industry, ["step2_industry", "step7_risk"]),
        _fact_requirement("business_model", "收入模式、客户结构和商业闭环", official, ["step3_biz", "step6_insight"]),
        _fact_requirement("product_matrix", "产品/服务矩阵和关键 KPI", official, ["step3_biz"]),
        _fact_requirement("customer_base", "客户结构、渠道结构、集中度或地域结构", official, ["step3_biz", "step7_risk"]),
        _fact_requirement("pricing_power", "定价权、价格带、折扣率或 ASP 变化", official + industry, ["step3_biz", "step6_insight"]),
        _fact_requirement("unit_economics", "单店/单用户/单产品经济模型或单位利润", official, ["step3_biz", "step6b_valuation"], "medium"),
        _fact_requirement("moat_evidence", "品牌、渠道、技术、规模或网络效应护城河证据", official + industry, ["step3_biz", "step6_insight"]),
        _fact_requirement("management_roster", "管理层名单、职位和任期", official, ["step5_mgmt"]),
        _fact_requirement("executive_changes", "近期高管、董事或控制权变化", official, ["step5_mgmt", "step7_risk"], "medium"),
        _fact_requirement("ownership", "股权结构、实际控制人和主要股东变化", official, ["step5_mgmt"]),
        _fact_requirement("incentives", "管理层薪酬、股权激励和绩效条件", official, ["step5_mgmt"], "medium"),
        _fact_requirement("governance_risks", "治理、关联交易、审计意见或合规风险", official, ["step5_mgmt", "step7_risk"]),
        _fact_requirement("balance_sheet", "资产负债表质量、杠杆、现金和营运资本", official, ["step4_finance", "step7_risk"]),
        _fact_requirement("guidance", "公司指引、管理层展望或业绩预测口径", official, ["step4_finance", "step6b_valuation"], "medium"),
        _fact_requirement("valuation_multiples", "PE/PB/PS/EV 等估值倍数", market, ["step6b_valuation"]),
        _fact_requirement("peer_set", "可比公司名单、口径和状态", market, ["step2_industry", "step6b_valuation"]),
        _fact_requirement("dcf_inputs", "DCF/WACC/增长率/利润率核心假设", official + market, ["step6b_valuation"]),
        _fact_requirement("target_price_range", "目标价区间、估值区间或隐含回报", market, ["step6b_valuation", "step8_master"]),
        _fact_requirement("sensitivity", "估值敏感性、关键假设弹性和下行情景", market, ["step6b_valuation", "step7_risk"]),
        _fact_requirement("sotp_inputs", "SOTP 分部估值输入和分部口径", official + market, ["step6b_valuation"], "medium"),
        _fact_requirement("bear_case", "空头情景、反向证据和结论推翻条件", official + industry, ["step7_risk", "step8_master"]),
        _fact_requirement("risk_triggers", "会改变投资结论的关键风险触发条件", official + industry, ["step7_risk", "step8_master"]),
        _fact_requirement("regulatory_risks", "监管、政策、诉讼或处罚风险", official + industry, ["step7_risk"]),
        _fact_requirement("competitive_risks", "竞争加剧、替代品、价格战或份额流失风险", industry, ["step7_risk"]),
        _fact_requirement("valuation_downside", "估值下修、倍数压缩和目标价下行空间", market, ["step7_risk", "step8_master"], "medium"),
        _fact_requirement("operating_metrics", "经营 KPI、用户/门店/产能/订单等运营指标", official, ["step1_data", "step3_biz"], "medium"),
        _fact_requirement("industry_kpis", "行业特定 KPI、渗透率、渠道或供需指标", industry, ["step2_industry"], "medium"),
        _fact_requirement("macro_sensitivity", "利率、汇率、通胀、政策等宏观敏感性", ["official_macro_data", "central_bank", "statistics_agency", "reputable_media"], ["step_macro", "step7_risk"], "medium"),
    ]


def _section_requirement(must_answer: list[str], required_facts: list[str], required_outputs: list[str] | None = None) -> dict[str, Any]:
    return {
        "must_answer": must_answer,
        "required_fact_keys": required_facts,
        "required_outputs": required_outputs or ["claims", "facts_used", "counter_evidence", "data_gaps", "markdown_draft"],
    }


def _build_section_requirements() -> dict[str, dict[str, Any]]:
    return {
        "step1_data": _section_requirement(["Q1"], ["revenue_trend", "profitability", "segment_performance"]),
        "step2_industry": _section_requirement(["Q2"], ["market_size", "growth_rate", "market_share", "competitive_landscape"]),
        "step3_biz": _section_requirement(["Q3"], ["business_model", "product_matrix", "customer_base", "pricing_power", "moat_evidence"]),
        "step4_finance": _section_requirement(["Q1", "Q4"], ["revenue_trend", "profitability", "cash_flow", "balance_sheet", "guidance"]),
        "step5_mgmt": _section_requirement(["Q5"], ["management_roster", "executive_changes", "ownership", "incentives", "governance_risks"]),
        "step_macro": _section_requirement(["Q7"], ["macro_sensitivity", "risk_triggers"], ["claims", "facts_used", "counter_evidence", "data_gaps", "markdown_draft"]),
        "step6b_valuation": _section_requirement(["Q6"], ["valuation_multiples", "peer_set", "dcf_inputs", "target_price_range", "sensitivity"]),
        "step6_insight": _section_requirement(["Q1", "Q2", "Q3"], ["revenue_trend", "growth_rate", "business_model", "moat_evidence"]),
        "step7_risk": _section_requirement(["Q7"], ["bear_case", "risk_triggers", "regulatory_risks", "competitive_risks", "macro_sensitivity"]),
        "step8_master": _section_requirement(["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"], ["revenue_trend", "market_size", "valuation_multiples", "risk_triggers"], ["verified_section_assembly", "source_appendix", "data_gaps", "markdown_draft"]),
    }


def build_research_plan(
    entity: str,
    query: str,
    market: str = "generic",
    report_type: str = "broker_ir",
    task_id: str = "",
) -> dict[str, Any]:
    """Build the MVP IR research plan contract.

    The MVP deliberately stays deterministic: scripts own schema, section
    coverage, fact requirements, and validation hooks. Later LLM/strategist
    expansion can append sharper questions without changing this contract.
    """
    entity = (entity or "").strip() or "目标公司"
    market = (market or "generic").strip().lower() or "generic"
    query = (query or "").strip()
    research_type = _normalize_research_type(report_type, query)

    investment_questions = [
        _question(
            "Q1",
            f"{entity} 的基本面变化是否足以支撑投资结论？",
            ["revenue_trend", "profitability", "cash_flow", "segment_performance", "operating_metrics"],
            ["annual_report", "quarterly_results", "company_ir", "regulatory_filing"],
            4,
        ),
        _question(
            "Q2",
            f"{entity} 所处行业和竞争格局是否支持未来增长？",
            ["market_size", "growth_rate", "market_share", "competitive_landscape", "industry_kpis"],
            ["industry_report", "company_disclosure", "regulatory_data", "reputable_media"],
            3,
        ),
        _question(
            "Q3",
            f"{entity} 的商业模式、产品和护城河是否具备可持续性？",
            ["business_model", "product_matrix", "customer_base", "pricing_power", "unit_economics", "moat_evidence"],
            ["company_ir", "annual_report", "earnings_call", "customer_or_partner_evidence"],
            3,
        ),
        _question(
            "Q4",
            f"{entity} 的管理层、治理和执行力是否支持投资主线？",
            ["management_roster", "executive_changes", "ownership", "incentives", "governance_risks"],
            ["annual_report", "company_governance_page", "exchange_disclosure", "regulatory_filing"],
            3,
        ),
        _question(
            "Q5",
            f"{entity} 的估值是否可复算，目标价是否由事实和假设支撑？",
            ["valuation_multiples", "peer_set", "sotp_inputs", "dcf_inputs", "target_price_range", "sensitivity"],
            ["market_data", "company_filing", "peer_filings", "financial_database", "analyst_consensus"],
            4,
        ),
        _question(
            "Q6",
            f"哪些风险或反向证据会推翻 {entity} 的投资结论？",
            ["bear_case", "risk_triggers", "regulatory_risks", "competitive_risks", "valuation_downside"],
            ["regulatory_filing", "litigation_database", "company_disclosure", "reputable_media", "industry_report"],
            3,
        ),
    ]
    core_questions = _build_core_questions(entity)
    fact_requirements = _build_fact_requirements()
    section_requirements = _build_section_requirements()
    coverage_matrix = {
        question["question_id"]: {
            "owner": question["owner_section"],
            "supporting_sections": question["supporting_sections"],
            "required_fact_keys": question["required_fact_keys"],
        }
        for question in core_questions
    }

    return {
        "schema_version": "ir_research_plan_mvp_v1",
        "task_id": task_id,
        "entity": entity,
        "market": market,
        "query": query,
        "report_type": report_type,
        "research_type": research_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": f"围绕 {entity} 形成证据可追溯、估值可复算、反证充分的投资研究报告。",
        "investment_questions": investment_questions,
        "core_questions": core_questions,
        "section_requirements": section_requirements,
        "fact_requirements": fact_requirements,
        "coverage_matrix": coverage_matrix,
        "must_answer": [
            "核心投资结论",
            "关键证据链",
            "业绩变化路径",
            "估值重估或下修路径",
            "主要反证与风险触发条件",
            "结论置信度与数据缺口",
        ],
        "forbidden": [
            "无来源数字",
            "模型训练记忆中的管理层信息",
            "未解释的主观估值调整",
            "未登记在 Fact Store 的关键事实",
            "低质量来源支撑核心财务或估值结论",
        ],
        "source_policy": {
            "financial_claims": ["annual_report", "quarterly_results", "regulatory_filing", "company_ir"],
            "management_claims": ["annual_report", "company_governance_page", "exchange_disclosure", "regulatory_filing"],
            "industry_claims": ["industry_report", "regulatory_data", "company_disclosure", "reputable_media"],
            "valuation_claims": ["market_data", "company_filing", "peer_filings", "financial_database", "analyst_consensus"],
            "auxiliary_only": ["stock_quote_page", "forum", "encyclopedia", "social_media"],
        },
        "stop_conditions": [
            "all_high_priority_questions_have_claims",
            "all_key_claims_bind_fact_ids_or_data_gaps",
            "each_section_package_has_counter_evidence",
            "step8_master_uses_only_verified_section_packages",
        ],
        "review_checklist": [
            "核心问题是否覆盖基本面、行业、商业模式、治理、估值、风险？",
            "每个高优先级问题是否分配 owner_section？",
            "每个 owner section 是否有 required_fact_keys？",
            "关键数字是否要求进入 Fact Store？",
            "不能确认的信息是否进入 data_gaps 而不是正文判断？",
        ],
        "section_package_schema": {
            "required_fields": [
                "section_id",
                "section_title",
                "key_messages",
                "claims",
                "facts_used",
                "counter_evidence",
                "data_gaps",
                "markdown_draft",
            ],
            "claim_fields": ["claim", "fact_ids", "reasoning", "confidence", "source_quality"],
        },
    }


def _strategic_question(
    question_id: str,
    question: str,
    owner_section: str,
    required_fact_keys: list[str],
    decision_relevance: str,
    priority: str = "high",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": question,
        "priority": priority,
        "owner_section": owner_section,
        "required_fact_keys": required_fact_keys,
        "decision_relevance": decision_relevance,
    }


def build_strategic_questions(entity: str, query: str, research_type: str) -> list[dict[str, Any]]:
    """Build deterministic orchestrator-style strategic questions for MVP.

    This is the dispatch-time enrichment layer. It does not call an LLM yet;
    it translates the task query into sharper, section-owned questions that
    downstream agents can answer through Section Packages.
    """
    text = f"{entity} {query}".lower()
    questions: list[dict[str, Any]] = []

    if "泡泡玛特" in text or "popmart" in text or "pop mart" in text:
        questions.extend([
            _strategic_question(
                "SQ1",
                "海外增长是渠道铺货驱动，还是消费者品牌认知和复购驱动？",
                "step3_biz",
                ["revenue_trend", "segment_performance", "customer_base", "growth_rate"],
                "决定海外收入增速可持续性和估值溢价是否成立。",
            ),
            _strategic_question(
                "SQ2",
                "Labubu 等爆款 IP 是一次性单品红利，还是可复制的 IP 运营能力？",
                "step3_biz",
                ["product_matrix", "pricing_power", "moat_evidence", "operating_metrics"],
                "决定商业模式是否能跨周期复制，而不是依赖单一爆款。",
            ),
            _strategic_question(
                "SQ3",
                "当前估值隐含了多少海外收入 CAGR、新品成功率和利润率改善？",
                "step6b_valuation",
                ["valuation_multiples", "dcf_inputs", "target_price_range", "sensitivity"],
                "决定估值是否已经充分定价乐观情景。",
            ),
            _strategic_question(
                "SQ4",
                "二级市场炒作、黄牛溢价和 IP 生命周期缩短是否会反噬品牌长期价值？",
                "step7_risk",
                ["bear_case", "risk_triggers", "competitive_risks", "moat_evidence"],
                "决定核心反证和下行情景。",
            ),
        ])
    elif research_type == "industry_research":
        questions.extend([
            _strategic_question(
                "SQ1",
                f"{entity} 的需求爆发是周期性主题还是长期产业趋势？",
                "step2_industry",
                ["market_size", "growth_rate", "industry_kpis", "macro_sensitivity"],
                "决定行业研究的主线和增长持续性。",
            ),
            _strategic_question(
                "SQ2",
                f"{entity} 产业链中利润池和议价权主要集中在哪些环节？",
                "step2_industry",
                ["competitive_landscape", "market_share", "pricing_power"],
                "决定应重点关注哪些公司和商业模式。",
            ),
            _strategic_question(
                "SQ3",
                f"{entity} 当前估值分歧来自业绩兑现、政策变化还是竞争格局？",
                "step6b_valuation",
                ["valuation_multiples", "peer_set", "risk_triggers"],
                "决定行业配置和个股筛选框架。",
            ),
        ])
    else:
        questions.extend([
            _strategic_question(
                "SQ1",
                f"{entity} 本轮研究最可能改变投资结论的核心变量是什么？",
                "step6_insight",
                ["revenue_trend", "growth_rate", "risk_triggers"],
                "强制子代理围绕决策变量而不是百科信息展开。",
            ),
            _strategic_question(
                "SQ2",
                f"{entity} 的估值上行和下行分别由哪些事实触发？",
                "step6b_valuation",
                ["valuation_multiples", "target_price_range", "sensitivity"],
                "让估值判断可复算、可反驳。",
            ),
            _strategic_question(
                "SQ3",
                f"哪些反向证据会推翻 {entity} 的主线判断？",
                "step7_risk",
                ["bear_case", "risk_triggers", "valuation_downside"],
                "保证报告保留反证，不只写正向故事。",
            ),
        ])

    return questions


def normalize_research_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy or hand-written research plans to the dispatch contract.

    Older plan producers used ``status`` instead of ``plan_status`` and sometimes
    emitted ``strategic_questions`` as a list of strings. The dispatch gate needs
    the newer ``plan_status`` field plus section-owned question objects.
    """
    normalized = dict(plan or {})
    if not normalized.get("plan_status") and normalized.get("status"):
        normalized["plan_status"] = normalized.get("status")

    strategic_questions = normalized.get("strategic_questions") or []
    if strategic_questions and any(not isinstance(item, dict) for item in strategic_questions):
        core_questions = normalized.get("core_questions") or []
        default_owner = "step6_insight"
        default_fact_keys = ["revenue_trend", "growth_rate", "risk_triggers"]
        if core_questions and isinstance(core_questions[0], dict):
            default_owner = core_questions[0].get("owner_section") or default_owner
            default_fact_keys = core_questions[0].get("required_fact_keys") or default_fact_keys

        converted: list[dict[str, Any]] = []
        for idx, item in enumerate(strategic_questions, 1):
            if isinstance(item, dict):
                converted.append(item)
                continue
            converted.append(_strategic_question(
                f"SQ{idx}",
                str(item),
                default_owner,
                list(default_fact_keys),
                "Legacy string question normalized to the dispatch-time strategic question schema.",
            ))
        normalized["strategic_questions"] = converted

    return normalized



def validate_research_plan_ready(plan: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_research_plan_contract(plan)
    errors: list[str] = []
    required_top_level = [
        "schema_version",
        "core_questions",
        "section_requirements",
        "fact_requirements",
        "coverage_matrix",
    ]
    for key in required_top_level:
        if not plan.get(key):
            errors.append(f"{key}_missing")

    if plan.get("plan_status") != "ready":
        errors.append("plan_status_not_ready")

    strategic_questions = plan.get("strategic_questions") or []
    if not strategic_questions:
        errors.append("strategic_questions_missing")

    section_keys = set((plan.get("section_requirements") or {}).keys())
    fact_keys = {item.get("fact_key") for item in plan.get("fact_requirements", []) if item.get("fact_key")}
    referenced_fact_keys: set[str] = set()
    for collection_name in ("core_questions", "strategic_questions"):
        for question in plan.get(collection_name, []) or []:
            owner_section = question.get("owner_section")
            if not owner_section:
                errors.append(f"{collection_name}_owner_section_missing")
            elif owner_section not in section_keys:
                errors.append(f"{collection_name}_owner_section_invalid")
            if not question.get("required_fact_keys"):
                errors.append(f"{collection_name}_required_fact_keys_missing")
            referenced_fact_keys.update(question.get("required_fact_keys", []) or [])
    for section in (plan.get("section_requirements") or {}).values():
        referenced_fact_keys.update(section.get("required_fact_keys", []) or [])
    for coverage in (plan.get("coverage_matrix") or {}).values():
        owner = coverage.get("owner")
        if owner and owner not in section_keys:
            errors.append("coverage_owner_invalid")
        referenced_fact_keys.update(coverage.get("required_fact_keys", []) or [])

    undefined = sorted(k for k in referenced_fact_keys if k not in fact_keys)
    if undefined:
        errors.append(f"undefined_fact_keys:{','.join(undefined)}")

    return {"ready": not errors, "errors": sorted(set(errors))}


def prepare_research_plan(
    task_id: str,
    entity: str,
    query: str,
    market: str = "generic",
    tasks_dir: Path = TASKS_DIR,
    report_type: str = "broker_ir",
) -> str:
    tasks_dir = Path(tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    plan = build_research_plan(entity=entity, query=query, market=market, report_type=report_type, task_id=task_id)
    plan["strategic_questions"] = build_strategic_questions(
        entity=plan["entity"],
        query=plan["query"],
        research_type=plan["research_type"],
    )
    plan["prepared_by"] = "script_scaffold_plus_orchestrator_enrichment"
    plan["plan_status"] = "ready"
    validation = validate_research_plan_ready(plan)
    plan["plan_status"] = "ready" if validation["ready"] else "blocked"
    plan["validation"] = validation
    path = research_plan_path(task_id, tasks_dir)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def research_plan_path(task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return Path(tasks_dir) / f"{task_id}-research_plan.json"


def write_research_plan(
    task_id: str,
    entity: str,
    query: str,
    market: str = "generic",
    tasks_dir: Path = TASKS_DIR,
    report_type: str = "broker_ir",
) -> str:
    tasks_dir = Path(tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    plan = build_research_plan(entity=entity, query=query, market=market, report_type=report_type, task_id=task_id)
    path = research_plan_path(task_id, tasks_dir)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def load_research_plan(task_id: str, tasks_dir: Path = TASKS_DIR) -> dict[str, Any] | None:
    path = research_plan_path(task_id, tasks_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
