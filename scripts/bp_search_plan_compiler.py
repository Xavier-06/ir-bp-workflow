#!/usr/bin/env python3
"""Compile BP research plans into claim-level search work orders."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CUSTOMER_REVENUE_KEYS = {"customer_evidence", "revenue_evidence", "order_evidence", "commercial_stage", "commercial_gap"}
VALUATION_KEYS = {"financing_terms", "valuation_multiples", "peer_set", "exit_path", "return_model", "sensitivity", "valuation_downside"}
COUNTER_SEARCH_KEYWORDS = ("客户", "订单", "收入", "营收", "回款", "估值", "valuation", "revenue", "customer", "order")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _entity(research_plan: dict[str, Any], profile: dict[str, Any] | None) -> str:
    profile = profile or {}
    for key in ("company_name", "entity", "project_name"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = research_plan.get("entity")
    return str(value).strip() if value else "目标公司"


def _fact_requirement_index(research_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("fact_key")): item
        for item in _as_list(research_plan.get("fact_requirements"))
        if isinstance(item, dict) and item.get("fact_key")
    }


def _question_index(research_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    questions = _as_list(research_plan.get("core_questions")) + _as_list(research_plan.get("strategic_questions"))
    by_owner: dict[str, dict[str, Any]] = {}
    for item in questions:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_section") or "")
        if owner and owner not in by_owner:
            by_owner[owner] = item
    return by_owner


def _claim_fact_keys(claim: dict[str, Any], owner_question: dict[str, Any] | None) -> list[str]:
    keys = [str(item) for item in _as_list(claim.get("required_fact_keys")) if str(item).strip()]
    if not keys and owner_question:
        keys = [str(item) for item in _as_list(owner_question.get("required_fact_keys")) if str(item).strip()]
    return keys


def _query_family(fact_keys: list[str], claim_text: str) -> str:
    joined = " ".join(fact_keys) + " " + claim_text
    # ── New specialized families first (before broad customer/competition checks) ──
    if any(key in fact_keys for key in ("scene_performance_threshold",)) or "门槛" in claim_text:
        return "scene_threshold_validation"
    if any(key in fact_keys for key in ("alternative_tech_routes",)) or ("技术路线" in claim_text and ("对比" in claim_text or "替代" in claim_text or "最优" in claim_text)):
        return "tech_route_comparison"
    if any(key in fact_keys for key in ("product_level_benchmark",)) or ("竞品" in claim_text and ("参数" in claim_text or "价格" in claim_text)):
        return "product_benchmark"
    # ── Existing families ──
    if any(key in fact_keys for key in CUSTOMER_REVENUE_KEYS) or any(word in joined for word in ("客户", "订单", "收入", "营收", "回款")):
        return "product_commercial"
    if any(key in fact_keys for key in VALUATION_KEYS) or "估值" in joined:
        return "valuation_validation"
    if any(key in fact_keys for key in ("competitor_set", "differentiation", "market_position")):
        return "competition_validation"
    if any(key in fact_keys for key in ("tech_route", "ipr_evidence", "certification", "performance_evidence")):
        return "technology_ip_validation"
    if any(key in fact_keys for key in ("company_registration", "ownership", "team_background", "legal_risk")):
        return "company_team_compliance"
    return "general_bp_claim_validation"


def _queries(entity: str, family: str, fact_keys: list[str], claim_text: str) -> list[str]:
    templates = {
        "product_commercial": [
            '"{entity}" 客户 合同 订单 回款',
            '"{entity}" 招投标 采购 中标',
            '"{entity}" revenue customer contract delivery',
            '"{entity}" 合作 客户 量产 交付',
            '"{entity}" 负面 纠纷 客户 订单',
        ],
        "valuation_validation": [
            '"{entity}" 融资 估值 投资',
            '"{entity}" financing valuation round',
            '"{entity}" 可比公司 估值 倍数',
            '"{entity}" 退出 IPO 并购',
        ],
        "competition_validation": [
            '"{entity}" 竞品 对比 替代方案',
            '"{entity}" competitor alternative comparison',
            '"{entity}" 市场份额 排名',
            '"{entity}" 领先 唯一 首家 反证',
        ],
        "technology_ip_validation": [
            '"{entity}" 专利 认证 测试报告',
            '"{entity}" 技术路线 性能 第三方验证',
            '"{entity}" patent certification performance',
            '"{entity}" 标准 国标 行标 IEC ISO',
        ],
        "company_team_compliance": [
            '"{entity}" 工商 股权 实控人',
            '"{entity}" 创始人 团队 履历',
            '"{entity}" 诉讼 行政处罚 失信',
            '"{entity}" company registry shareholder founder',
        ],
        "scene_threshold_validation": [
            '"{entity}" 应用场景 选型 门槛 参数 认证',
            '"{entity}" 行业标准 性能要求 准入 规格',
            '"{entity}" application selection criteria threshold specification',
            '"{entity}" 客户 采购 技术要求 测试标准',
        ],
        "tech_route_comparison": [
            '"{entity}" 技术路线 对比 替代方案 优缺点',
            '"{entity}" technology route alternative comparison benchmark',
            '"{entity}" 不同技术路线 性能 成本 成熟度 选型',
            '"{entity}" 技术方案 比较 适用场景 局限',
        ],
        "product_benchmark": [
            '"{entity}" 产品 参数 对比 竞品 型号',
            '"{entity}" product benchmark price comparison specification',
            '"{entity}" 同类产品 价格 性能 性价比 替代',
            '"{entity}" 竞品 规格 报价 出货量 市场份额',
        ],
        "general_bp_claim_validation": [
            '"{entity}" {claim}',
            '"{entity}" 官方 披露',
            '"{entity}" 新闻 报道 验证',
            '"{entity}" 反证 风险',
        ],
    }
    rendered = []
    for template in templates.get(family, templates["general_bp_claim_validation"]):
        rendered.append(template.format(entity=entity, claim=claim_text[:40]))
    for fact_key in fact_keys[:2]:
        rendered.append(f'"{entity}" {fact_key}')
    seen: set[str] = set()
    unique: list[str] = []
    for query in rendered:
        if query not in seen:
            seen.add(query)
            unique.append(query)
    return unique


def _source_tiers(fact_keys: list[str], facts: dict[str, dict[str, Any]]) -> list[str]:
    tiers: list[str] = []
    for key in fact_keys:
        req = facts.get(key) or {}
        for source in _as_list(req.get("source_priority")):
            source_str = str(source)
            if source_str == "bp_source_document":
                continue
            if source_str not in tiers:
                tiers.append(source_str)
    if not tiers:
        tiers = ["official", "regulatory", "reputable_media"]
    normalized = []
    aliases = {
        "company_registry": "official",
        "government_disclosure": "regulatory",
        "court_or_regulatory_database": "regulatory",
        "company_official": "official",
        "customer_disclosure": "customer_or_partner_disclosure",
        "public_tender": "customer_or_partner_disclosure",
    }
    for tier in tiers:
        normalized_tier = aliases.get(tier, tier)
        if normalized_tier not in normalized:
            normalized.append(normalized_tier)
    return normalized


def _requires_counter_search(priority: str, family: str, claim_text: str) -> bool:
    if priority == "critical":
        return True
    if family in {"product_commercial", "valuation_validation", "tech_route_comparison", "product_benchmark"}:
        return True
    lowered = claim_text.lower()
    return any(keyword.lower() in lowered for keyword in COUNTER_SEARCH_KEYWORDS)


def compile_bp_search_plan(
    research_plan: dict[str, Any],
    profile: dict[str, Any] | None = None,
    company_verify: dict[str, Any] | None = None,
    presearch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity = _entity(research_plan, profile)
    facts = _fact_requirement_index(research_plan)
    questions_by_owner = _question_index(research_plan)
    search_tasks: list[dict[str, Any]] = []
    owner_index: dict[str, list[str]] = {}

    for idx, claim in enumerate(_as_list(research_plan.get("claim_matrix")), 1):
        if not isinstance(claim, dict):
            continue
        owner = str(claim.get("owner_section") or "bp_dealbreaker_risk")
        priority = str(claim.get("priority") or "high").lower()
        claim_text = str(claim.get("claim") or "").strip()
        fact_keys = _claim_fact_keys(claim, questions_by_owner.get(owner))
        family = _query_family(fact_keys, claim_text)
        search_task_id = f"BST-{idx:03d}"
        question_id = str((questions_by_owner.get(owner) or {}).get("question_id") or "")
        task = {
            "search_task_id": search_task_id,
            "claim_id": str(claim.get("claim_id") or f"BC{idx:03d}"),
            "question_id": question_id,
            "owner_section": owner,
            "fact_key": fact_keys[0] if fact_keys else "general_claim_evidence",
            "required_fact_keys": fact_keys,
            "depends_on_fact_keys": ["customer_evidence", "revenue_evidence"] if owner == "bp_valuation_return" else [],
            "priority": priority,
            "query_family": family,
            "queries": _queries(entity, family, fact_keys, claim_text),
            "required_source_tiers": _source_tiers(fact_keys, facts),
            "min_unique_queries": 4 if priority == "critical" else 3,
            "min_fetched_urls": 2 if priority in {"critical", "high"} else 1,
            "min_independent_domains": 2 if priority in {"critical", "high"} else 1,
            "requires_counter_search": _requires_counter_search(priority, family, claim_text),
            "bp_only_support_policy": "bp_only_cannot_support_main_conclusion",
            "status": "planned",
        }
        search_tasks.append(task)
        owner_index.setdefault(owner, []).append(search_task_id)

    return {
        "schema_version": "bp_search_plan.v1",
        "task_id": research_plan.get("task_id", ""),
        "entity": entity,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "research_plan": "bp_research_plan.json",
            "profile": "bp_step0_profile.json",
            "company_verify": "company_verify_report.json",
            "presearch": "bp_presearch*.json",
        },
        "search_tasks": search_tasks,
        "owner_section_index": owner_index,
        "policy": {
            "critical_claims": "critical claims require counter-search and at least two fetched external sources unless one authoritative source is available",
            "bp_only": "BP-only facts remain unverified or partially_supported; they cannot support main conclusions",
            "valuation": "valuation assumptions must not rely on unsupported customer/revenue/order facts",
        },
    }


def write_bp_search_plan(task_dir: Path, payload: dict[str, Any]) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / "bp_search_plan.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
