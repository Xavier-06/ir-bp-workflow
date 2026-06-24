#!/usr/bin/env python3
"""BP due-diligence research planner.

The BP planner mirrors the IR planner contract: scripts own the stable schema,
fact requirements, section ownership, coverage matrix, and validation. The
orchestrator/agent enrichment layer owns sharper strategic questions, BP claim
prioritization, and owner assignment. In this MVP the enrichment is deterministic
so the pipeline can run without an external model call, but the contract makes
that collaboration explicit.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BP_SECTION_IDS = [
    "bp_company_team_compliance",
    "bp_product_commercial",
    "bp_tech_ip_moat",
    "bp_market_supply_chain",
    "bp_competition_positioning",
    "bp_valuation_return",
    "bp_customer_revenue_validation",
    "bp_dealbreaker_risk",
]


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
            "BQ1",
            f"{entity} 的工商主体、股权结构、实控人和核心团队是否真实、稳定且与 BP 表述一致？",
            "bp_company_team_compliance",
            ["company_registration", "ownership", "controller", "team_background", "governance_risk"],
            ["bp_dealbreaker_risk", "bp_competition_positioning"],
            "决定投资主体可信度、关键人风险和后续尽调对象是否明确。",
            "critical",
        ),
        _core_question(
            "BQ2",
            f"{entity} 的产品矩阵、量产状态、客户/订单/收入证据是否证明其已进入可商业化阶段？",
            "bp_product_commercial",
            ["product_matrix", "commercial_stage", "customer_evidence", "revenue_evidence", "order_evidence"],
            ["bp_market_supply_chain", "bp_customer_revenue_validation", "bp_valuation_return", "bp_competition_positioning"],
            "决定公司是技术样机、试用阶段还是可销售产品，直接影响估值和投资条件。",
            "critical",
        ),
        _core_question(
            "BQ3",
            f"{entity} 的技术路线、知识产权、认证和性能声称是否有独立证据支撑？",
            "bp_tech_ip_moat",
            ["tech_route", "ipr_evidence", "certification", "performance_evidence", "moat_evidence"],
            ["bp_competition_positioning", "bp_dealbreaker_risk"],
            "决定技术壁垒是否真实存在，避免把 BP 宣传口径写成投资结论。",
        ),
        _core_question(
            "BQ4",
            f"{entity} 所处市场的 TAM/SAM/SOM、增速、渗透率和供应链位置是否支持 BP 的增长假设？",
            "bp_market_supply_chain",
            ["market_size", "growth_rate", "sam_som", "penetration_assumption", "supply_chain_position"],
            ["bp_valuation_return", "bp_competition_positioning", "bp_dealbreaker_risk"],
            "决定市场空间和增长天花板是否足以支撑融资故事。",
        ),
        _core_question(
            "BQ5",
            f"{entity} 的竞品、替代方案、差异化和进入壁垒是否经得起横向比较？",
            "bp_competition_positioning",
            ["competitor_set", "differentiation", "substitution_risk", "market_position", "moat_evidence"],
            ["bp_product_commercial", "bp_tech_ip_moat", "bp_market_supply_chain"],
            "决定标的是否真的领先，以及竞争结论是否依赖未验证的产品/技术事实。",
        ),
        _core_question(
            "BQ6",
            f"{entity} 的融资诉求、估值区间、可比公司、退出路径和 MOIC/IRR 假设是否可复算？",
            "bp_valuation_return",
            ["financing_terms", "valuation_multiples", "peer_set", "exit_path", "return_model", "sensitivity"],
            ["bp_company_team_compliance", "bp_product_commercial", "bp_tech_ip_moat", "bp_market_supply_chain", "bp_customer_revenue_validation", "bp_competition_positioning"],
            "决定风险收益比和是否有条件推进。估值不得使用未验证营收/订单作为主假设。",
        ),
        _core_question(
            "BQ7",
            f"哪些反证、数据缺口或 Deal Breakers 会直接推翻 {entity} 的投资建议？",
            "bp_dealbreaker_risk",
            ["deal_breakers", "counter_evidence", "legal_risk", "commercial_gap", "valuation_downside"],
            ["bp_company_team_compliance", "bp_product_commercial", "bp_tech_ip_moat", "bp_market_supply_chain", "bp_valuation_return", "bp_customer_revenue_validation"],
            "强制形成反方视角，防止最终报告只拼接正向故事。",
            "critical",
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


def _build_fact_requirements(stage_tier: str = "T3") -> list[dict[str, Any]]:
    """构建事实需求列表。stage_tier 控制各 fact_key 的 criticality。

    T1(种子/天使): customer_evidence/order_evidence/revenue_evidence 降为 medium,
                   team_background 升为 critical, commercial_gap 降为 medium
    T2(Pre-A/A):   customer_evidence 保持 critical, order/revenue 降为 high,
                   commercial_gap 降为 high
    T3(B轮):       全部默认 critical/high
    T4(C轮+):      全部默认, financial 类升为 critical
    """
    official = ["company_registry", "government_disclosure", "court_or_regulatory_database", "company_official"]
    public = ["industry_report", "reputable_media", "customer_or_partner_disclosure", "public_tender"]
    market = ["peer_financing_database", "listed_peer_filings", "market_database", "valuation_report"]
    bp_only = ["bp_source_document"]

    # ── P1-6: 按 stage_tier 调整 criticality ──
    # 客户/收入相关：T1 不要求 → medium, T2 有客户要求 → high
    if stage_tier == "T1":
        cust_crit = "medium"       # 客户证据：加分项
        order_crit = "medium"      # 订单证据：加分项
        rev_crit = "medium"        # 收入证据：加分项
        comm_gap_crit = "medium"   # 商业化缺口：正常
        team_crit = "critical"     # 团队背景：极早期最关键
        ipr_crit = "high"
    elif stage_tier == "T2":
        cust_crit = "critical"     # 需要有付费客户
        order_crit = "high"        # 有订单更好但不强制
        rev_crit = "high"          # 有收入更好但不强制
        comm_gap_crit = "high"     # 缺口需关注但非阻断
        team_crit = "critical"     # 团队仍关键
        ipr_crit = "critical"      # 知识产权更关键
    else:  # T3, T4
        cust_crit = "critical"
        order_crit = "critical"
        rev_crit = "critical"
        comm_gap_crit = "critical"
        team_crit = "high"
        ipr_crit = "critical"

    return [
        _fact_requirement("company_registration", "工商主体、成立时间、注册资本、经营范围、主体状态", official, ["bp_company_team_compliance"]),
        _fact_requirement("ownership", "股权结构、股东、出资、历史融资和穿透关系", official + market, ["bp_company_team_compliance", "bp_valuation_return"]),
        _fact_requirement("controller", "实际控制人、控制链和关键人依赖", official, ["bp_company_team_compliance", "bp_dealbreaker_risk"]),
        _fact_requirement("team_background", "创始人/核心团队履历、任职、教育、过往成果", official + public, ["bp_company_team_compliance"], team_crit),
        _fact_requirement("governance_risk", "诉讼、处罚、失信、关联交易、劳动或知识产权争议", official, ["bp_company_team_compliance", "bp_dealbreaker_risk"]),
        _fact_requirement("product_matrix", "产品线、型号、目标场景、交付状态、价格或规格", ["company_official", "customer_disclosure", "bp_source_document"], ["bp_product_commercial"]),
        _fact_requirement("commercial_stage", "研发、样机、试用、小批量、量产、规模销售等阶段证据", public + official, ["bp_product_commercial", "bp_customer_revenue_validation", "bp_valuation_return"], "critical"),
        _fact_requirement("customer_evidence", "客户、试用、合作、采购、招投标或合同线索", public + official, ["bp_product_commercial", "bp_customer_revenue_validation", "bp_competition_positioning"], cust_crit),
        _fact_requirement("order_evidence", "订单、收入、交付、产能利用或在手合同证据", public + official, ["bp_product_commercial", "bp_customer_revenue_validation", "bp_valuation_return"], order_crit),
        _fact_requirement("revenue_evidence", "营收规模、收入结构、回款或商业闭环证据", public + official, ["bp_product_commercial", "bp_customer_revenue_validation", "bp_valuation_return"], rev_crit),
        _fact_requirement("tech_route", "技术路线、主流替代路线和路线成熟度", ["technical_standard", "academic_paper", "industry_report", "patent_database"], ["bp_tech_ip_moat", "bp_competition_positioning"]),
        _fact_requirement("ipr_evidence", "专利、软著、商标、论文、专有认证或知识产权覆盖", official + ["patent_database"], ["bp_tech_ip_moat"]),
        _fact_requirement("certification", "资质、认证、测试报告及有效期", official + ["technical_standard"], ["bp_tech_ip_moat", "bp_company_team_compliance"]),
        _fact_requirement("performance_evidence", "性能参数、测试结果、第三方验证及口径", ["third_party_test", "technical_standard", "academic_paper", "customer_disclosure"], ["bp_tech_ip_moat"]),
        _fact_requirement("moat_evidence", "壁垒证据：技术、客户、渠道、资质、成本、规模或数据", public + official, ["bp_tech_ip_moat", "bp_competition_positioning"]),
        _fact_requirement("market_size", "TAM/SAM/SOM、细分市场规模和统计口径", ["industry_report", "government_statistics", "association_data", "listed_peer_filings"], ["bp_market_supply_chain", "bp_valuation_return"]),
        _fact_requirement("growth_rate", "行业增速、渗透率、需求驱动和周期变量", ["industry_report", "government_statistics", "association_data"], ["bp_market_supply_chain"]),
        _fact_requirement("sam_som", "可服务市场和可获得市场的推算参数", ["industry_report", "customer_or_partner_disclosure", "bp_source_document"], ["bp_market_supply_chain", "bp_valuation_return"]),
        _fact_requirement("penetration_assumption", "渗透率假设、对标对象和必要条件", ["industry_report", "listed_peer_filings", "reputable_media"], ["bp_market_supply_chain", "bp_valuation_return"], "medium"),
        _fact_requirement("supply_chain_position", "产业链位置、上下游、供应商/客户议价和关键资源", public + official, ["bp_market_supply_chain"]),
        _fact_requirement("competitor_set", "竞品名单、当前状态、产品能力和融资/经营阶段", public + market, ["bp_competition_positioning", "bp_valuation_return"]),
        _fact_requirement("differentiation", "与竞品相比的差异化、领先性和可替代性", public + ["technical_standard"], ["bp_competition_positioning"]),
        _fact_requirement("substitution_risk", "替代方案、客户自研、进口替代或价格战风险", public + ["industry_report"], ["bp_competition_positioning"]),
        _fact_requirement("market_position", "市场份额、排名、客户认可或生态位置", public + market, ["bp_competition_positioning"]),
        _fact_requirement("financing_terms", "融资轮次、融资金额、投前/投后估值、资金用途", bp_only + market, ["bp_valuation_return"]),
        _fact_requirement("valuation_multiples", "PS/PE/EV 等估值倍数及适用条件", market, ["bp_valuation_return"]),
        _fact_requirement("peer_set", "可比公司/交易样本、口径、阶段和排除理由", market + public, ["bp_valuation_return"]),
        _fact_requirement("exit_path", "IPO、并购、股权转让等退出路径及可行性", market + public, ["bp_valuation_return"]),
        _fact_requirement("return_model", "MOIC/IRR、稀释、跟投、退出估值和持有期假设", market + bp_only, ["bp_valuation_return"]),
        _fact_requirement("sensitivity", "估值敏感性、下行情景和关键变量弹性", market, ["bp_valuation_return", "bp_dealbreaker_risk"]),
        _fact_requirement("deal_breakers", "会直接阻断投资的事实、缺口或反证", official + public + market, ["bp_dealbreaker_risk"], "critical"),
        _fact_requirement("counter_evidence", "与 BP 主张相反的证据和替代解释", official + public + market, ["bp_dealbreaker_risk"], "critical"),
        _fact_requirement("legal_risk", "重大诉讼、处罚、资质缺失、监管限制", official, ["bp_company_team_compliance", "bp_dealbreaker_risk"], "critical"),
        _fact_requirement("commercial_gap", "客户/订单/收入/交付无法验证造成的商业化缺口", public + official, ["bp_product_commercial", "bp_customer_revenue_validation", "bp_dealbreaker_risk"], comm_gap_crit),
        _fact_requirement("valuation_downside", "估值下修空间、倍数压缩和退出失败情景", market, ["bp_valuation_return", "bp_dealbreaker_risk"], "high"),
        _fact_requirement("scene_performance_threshold", "目标应用场景的性能、认证、价格门槛参数", ["industry_report", "technical_standard", "customer_disclosure"], ["bp_market_supply_chain", "bp_tech_ip_moat", "bp_competition_positioning"], "high"),
        _fact_requirement("alternative_tech_routes", "同场景替代技术路线及性能、成本、成熟度对比", ["technical_standard", "academic_paper", "industry_report"], ["bp_tech_ip_moat", "bp_competition_positioning"], "high"),
        _fact_requirement("product_level_benchmark", "产品级竞品参数和价格横向对比", ["customer_disclosure", "industry_report", "listed_peer_filings"], ["bp_competition_positioning", "bp_product_commercial"], "high"),
    ]


def _section_requirement(must_answer: list[str], required_facts: list[str], required_outputs: list[str] | None = None) -> dict[str, Any]:
    return {
        "must_answer": must_answer,
        "required_fact_keys": required_facts,
        "required_outputs": required_outputs or ["answers", "claims", "facts_used", "counter_evidence", "data_gaps", "narrative_blocks", "markdown_draft"],
    }


def _build_section_requirements() -> dict[str, dict[str, Any]]:
    return {
        "bp_company_team_compliance": _section_requirement(["BQ1"], ["company_registration", "ownership", "controller", "team_background", "governance_risk", "legal_risk"]),
        "bp_product_commercial": _section_requirement(["BQ2"], ["product_matrix", "commercial_stage", "customer_evidence", "order_evidence", "revenue_evidence", "commercial_gap"]),
        "bp_tech_ip_moat": _section_requirement(["BQ3"], ["tech_route", "ipr_evidence", "certification", "performance_evidence", "moat_evidence", "alternative_tech_routes", "scene_performance_threshold"]),
        "bp_market_supply_chain": _section_requirement(["BQ4"], ["market_size", "growth_rate", "sam_som", "penetration_assumption", "supply_chain_position", "scene_performance_threshold"]),
        "bp_competition_positioning": _section_requirement(["BQ5"], ["competitor_set", "differentiation", "substitution_risk", "market_position", "moat_evidence", "product_level_benchmark", "alternative_tech_routes"]),
        "bp_valuation_return": _section_requirement(["BQ6"], ["financing_terms", "valuation_multiples", "peer_set", "exit_path", "return_model", "sensitivity", "valuation_downside"]),
        "bp_customer_revenue_validation": _section_requirement(["BQ2"], ["customer_evidence", "order_evidence", "revenue_evidence", "commercial_stage", "commercial_gap"]),
        "bp_dealbreaker_risk": _section_requirement(["BQ7"], ["deal_breakers", "counter_evidence", "legal_risk", "commercial_gap", "valuation_downside"], ["answers", "claims", "facts_used", "counter_evidence", "data_gaps", "deal_breakers", "narrative_blocks", "markdown_draft"]),
    }


def build_strategic_questions(entity: str, query: str, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    profile = profile or {}
    products = profile.get("products") or []
    tech_keywords = profile.get("tech_keywords") or []
    product_hint = "、".join(str(item) for item in products[:2]) if products else "核心产品"
    tech_hint = "、".join(str(item) for item in tech_keywords[:2]) if tech_keywords else "核心技术路线"
    return [
        _core_question(
            "BSQ1",
            f"{entity} 的 {product_hint} 是否已有可独立验证的付费客户、订单或交付证据？",
            "bp_customer_revenue_validation",
            ["customer_evidence", "order_evidence", "revenue_evidence", "commercial_stage"],
            ["bp_product_commercial", "bp_valuation_return", "bp_dealbreaker_risk"],
            "这是 BP 尽调最容易从故事变成事实的分水岭。",
            "critical",
        ),
        _core_question(
            "BSQ2",
            f"BP 对 {tech_hint} 的领先性描述，是可量化技术壁垒，还是尚未验证的宣传口径？",
            "bp_tech_ip_moat",
            ["tech_route", "performance_evidence", "ipr_evidence", "certification", "moat_evidence"],
            ["bp_competition_positioning", "bp_dealbreaker_risk"],
            "决定技术溢价是否成立。",
        ),
        _core_question(
            "BSQ3",
            f"{entity} 的融资估值在未验证客户/营收情景下还能否成立？",
            "bp_valuation_return",
            ["financing_terms", "valuation_multiples", "peer_set", "return_model", "sensitivity", "commercial_gap"],
            ["bp_customer_revenue_validation", "bp_dealbreaker_risk"],
            "防止估值模型把未验证商业化数据当作主假设。",
            "critical",
        ),
        _core_question(
            "BSQ4",
            f"如果要否决 {entity}，最强的 3 条反证或 Deal Breakers 是什么？",
            "bp_dealbreaker_risk",
            ["deal_breakers", "counter_evidence", "legal_risk", "commercial_gap", "valuation_downside"],
            ["bp_company_team_compliance", "bp_product_commercial", "bp_tech_ip_moat", "bp_market_supply_chain", "bp_valuation_return", "bp_customer_revenue_validation"],
            "强制后续报告保留反方审查，而不是只写支持理由。",
            "critical",
        ),
        _core_question(
            "BSQ5",
            f"{entity} 的目标场景下，客户选型的性能/价格/认证门槛是什么？替代技术路线和竞品在这些门槛上的表现如何？",
            "bp_competition_positioning",
            ["scene_performance_threshold", "alternative_tech_routes", "product_level_benchmark"],
            ["bp_tech_ip_moat", "bp_market_supply_chain", "bp_product_commercial"],
            "决定标的产品在场景中的真实竞争力和替代风险。",
            "high",
        ),
    ]


def _default_claim_matrix(entity: str) -> list[dict[str, Any]]:
    defaults = [
        ("BC001", f"{entity} 的团队履历、行业资源或顾问背书支持公司执行力。", "bp_company_team_compliance", "critical"),
        ("BC002", f"{entity} 的核心产品已达到 BP 所称商业化或量产阶段。", "bp_product_commercial", "critical"),
        ("BC003", f"{entity} 的技术路线、专利、认证或性能参数构成可持续壁垒。", "bp_tech_ip_moat", "high"),
        ("BC004", f"{entity} 的市场规模、增速和 TAM/SAM/SOM 假设足以支持增长空间。", "bp_market_supply_chain", "high"),
        ("BC005", f"{entity} 的客户、订单、收入或渠道进展可以被独立验证。", "bp_customer_revenue_validation", "critical"),
        ("BC006", f"{entity} 的竞品比较、领先性或唯一性声明成立。", "bp_competition_positioning", "high"),
        ("BC007", f"{entity} 的融资估值、可比公司和退出路径支持目标回报。", "bp_valuation_return", "high"),
        ("BC008", f"{entity} 的目标应用场景下存在多条技术路线，当前路线在性能、成本、成熟度上是最优选择。", "bp_tech_ip_moat", "high"),
        ("BC009", f"{entity} 的核心产品与同场景竞品在关键性能参数和价格上具有可比竞争力。", "bp_competition_positioning", "high"),
        ("BC010", f"{entity} 的目标应用场景对客户选型有明确的性能、认证、价格门槛要求。", "bp_market_supply_chain", "high"),
    ]
    return [
        {
            "claim_id": claim_id,
            "claim": claim,
            "owner_section": owner,
            "priority": priority,
            "source": "bp_or_inferred_from_intake",
            "status": "planned",
            "required_fact_keys": [],
        }
        for claim_id, claim, owner, priority in defaults
    ]


def build_claim_matrix(entity: str, claim_inventory: dict[str, Any] | list[Any] | None = None) -> list[dict[str, Any]]:
    if not claim_inventory:
        return _default_claim_matrix(entity)
    raw_claims = claim_inventory.get("claims", []) if isinstance(claim_inventory, dict) else claim_inventory
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_claims or [], 1):
        if isinstance(item, str):
            claim = item
            owner = "bp_dealbreaker_risk"
            priority = "high"
            claim_id = f"BC{idx:03d}"
        elif isinstance(item, dict):
            claim = str(item.get("claim") or item.get("text") or item.get("bp_claim") or "").strip()
            owner = str(item.get("owner_section") or item.get("owner") or "bp_dealbreaker_risk")
            priority = str(item.get("priority") or item.get("importance") or "high")
            claim_id = str(item.get("claim_id") or item.get("id") or f"BC{idx:03d}")
        else:
            continue
        if not claim:
            continue
        if owner not in BP_SECTION_IDS:
            owner = "bp_dealbreaker_risk"
        rows.append({
            "claim_id": claim_id,
            "claim": claim,
            "owner_section": owner,
            "priority": priority,
            "source": "bp_claim_inventory",
            "status": "planned",
            "required_fact_keys": [],
        })
    return rows or _default_claim_matrix(entity)


def validate_bp_research_plan_ready(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    for key in ("schema_version", "core_questions", "strategic_questions", "fact_requirements", "section_requirements", "coverage_matrix", "claim_matrix"):
        if not plan.get(key):
            errors.append(f"{key}_missing")
    if plan.get("plan_status") != "ready":
        errors.append("plan_status_not_ready")

    section_keys = set((plan.get("section_requirements") or {}).keys())
    fact_keys = {item.get("fact_key") for item in plan.get("fact_requirements", []) if isinstance(item, dict) and item.get("fact_key")}
    referenced_fact_keys: set[str] = set()
    for collection_name in ("core_questions", "strategic_questions"):
        for question in plan.get(collection_name, []) or []:
            owner = question.get("owner_section")
            if owner not in section_keys:
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
    for claim in plan.get("claim_matrix", []) or []:
        if claim.get("owner_section") not in section_keys:
            errors.append("claim_owner_section_invalid")
    undefined = sorted(k for k in referenced_fact_keys if k not in fact_keys)
    if undefined:
        errors.append(f"undefined_fact_keys:{','.join(undefined)}")
    return {"ready": not errors, "errors": sorted(set(errors))}


def build_bp_research_plan(
    task_id: str,
    entity: str,
    query: str,
    market: str = "cn",
    input_file: str = "",
    profile: dict[str, Any] | None = None,
    claim_inventory: dict[str, Any] | list[Any] | None = None,
    stage_tier: str = "T3",
) -> dict[str, Any]:
    entity = (entity or "").strip() or "目标公司"
    query = (query or "").strip()
    market = (market or "cn").strip().lower() or "cn"
    profile = profile or {}
    core_questions = _build_core_questions(entity)
    strategic_questions = build_strategic_questions(entity, query, profile)
    fact_requirements = _build_fact_requirements(stage_tier)
    section_requirements = _build_section_requirements()
    claim_matrix = build_claim_matrix(entity, claim_inventory)
    coverage_matrix = {
        question["question_id"]: {
            "owner": question["owner_section"],
            "supporting_sections": question["supporting_sections"],
            "required_fact_keys": question["required_fact_keys"],
            "priority": question["priority"],
        }
        for question in core_questions + strategic_questions
    }
    plan = {
        "schema_version": "bp_research_plan.v2",
        "task_id": task_id,
        "entity": entity,
        "market": market,
        "query": query,
        "input_file": input_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": f"围绕 {entity} 的商业计划书形成证据可追溯、反证充分、估值可复算、交付可门禁的投资尽调研究计划。",
        "prepared_by": "script_scaffold_plus_orchestrator_enrichment",
        "generation_roles": {
            "script": "schema_fact_requirements_coverage_matrix_validation",
            "orchestrator_agent": "strategic_questions_claim_prioritization_owner_assignment",
        },
        "core_questions": core_questions,
        "strategic_questions": strategic_questions,
        "fact_requirements": fact_requirements,
        "section_requirements": section_requirements,
        "coverage_matrix": coverage_matrix,
        "claim_matrix": claim_matrix,
        "must_answer": [
            "当前投资判断快照",
            "BP 核心声称验证状态",
            "公司与团队可信度",
            "产品商业化和客户/订单证据",
            "技术/IP/认证壁垒",
            "市场空间和供应链位置",
            "竞争定位和替代方案",
            "估值、回报和下行情景",
            "Deal Breakers、反证和数据缺口",
        ],
        "forbidden": [
            "把 BP 自述当作已验证外部事实",
            "无 fact_id 的核心结论",
            "用未验证客户/收入支撑估值主假设",
            "忽略 critical claim 的 not_addressed 状态",
            "最终报告按子代理章节直接拼接",
        ],
        "source_policy": {
            "bp_source_document": "只能作为 BP claim 来源，不能单独支撑主结论",
            "critical_claims": "必须绑定官方/监管/数据库/客户或第三方证据；否则进入 data_gaps",
            "valuation_claims": "必须使用可比口径和敏感性，不得使用未验证营收/订单作为主假设",
        },
        "section_package_schema": {
            "required_fields": ["section_id", "section_title", "key_messages", "answers", "claim_ids_covered", "claims", "facts_used", "counter_evidence", "data_gaps", "narrative_blocks", "markdown_draft"],
            "answer_fields": ["question_id", "answer", "fact_ids", "confidence", "limits"],
            "claim_fields": ["claim", "claim_id", "fact_ids", "reasoning", "confidence", "source_quality"],
        },
        "stop_conditions": [
            "all_critical_claims_addressed_or_blocked",
            "all_core_questions_have_answers_or_data_gaps",
            "every_core_claim_binds_fact_ids_or_is_disclosed_as_unverified",
            "valuation_uses_only_verified_commercial_assumptions_or_labels_sensitivity",
            "final_delivery_gate_passed",
        ],
        "plan_status": "ready",
    }
    validation = validate_bp_research_plan_ready(plan)
    plan["plan_status"] = "ready" if validation["ready"] else "blocked"
    plan["validation"] = validation
    return plan


def write_bp_research_plan(task_dir: Path, plan: dict[str, Any]) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / "bp_research_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
