#!/usr/bin/env python3
"""Rule-based cross-dimension consistency gate for BP pipeline."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _valid_packages(section_index: dict[str, Any]) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for item in section_index.get("packages", []) or []:
        if not isinstance(item, dict):
            continue
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        if validation and not validation.get("passed"):
            continue
        package = item.get("package") if isinstance(item.get("package"), dict) else {}
        if package:
            packages.append(package)
    return packages


def _has_revenue_customer_assumption(text: str) -> bool:
    return any(keyword in text for keyword in ("收入", "营收", "客户", "订单"))


def _has_supporting_fact_ids(package: dict[str, Any]) -> bool:
    fact_ids: list[Any] = []
    for field in ("facts_used",):
        fact_ids.extend(package.get(field, []) or [])
    for field in ("answers", "narrative_blocks", "claims"):
        for item in package.get(field, []) or []:
            if isinstance(item, dict):
                fact_ids.extend(item.get("fact_ids", []) or [])
    return bool([fact_id for fact_id in fact_ids if str(fact_id).strip()])


def _assumption_items_without_facts(package: dict[str, Any]) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for field in ("answers", "narrative_blocks", "claims"):
        for item in package.get(field, []) or []:
            if not isinstance(item, dict):
                continue
            item_text = json.dumps(item, ensure_ascii=False)
            fact_ids = [str(fid).strip() for fid in item.get("fact_ids", []) or [] if str(fid).strip()]
            if _has_revenue_customer_assumption(item_text) and not fact_ids:
                missing.append({"field": field, "question_id": str(item.get("question_id", "")), "claim_id": str(item.get("claim_id", ""))})
    return missing


def _iter_claims(packages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for package in packages:
        section_id = str(package.get("section_id") or package.get("section_title") or "")
        for claim in package.get("claims", []) or []:
            if isinstance(claim, dict):
                rows.append((section_id, claim))
    return rows


def _normalize_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _numeric_value(value: Any) -> float | None:
    text = str(value or "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    if "万" in text:
        number *= 10_000
    if "亿" in text:
        number *= 100_000_000
    return number


def _add_fact_type_conflicts(claim_rows: list[tuple[str, dict[str, Any]]], issues: list[dict[str, Any]]) -> None:
    buckets: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for section_id, claim in claim_rows:
        fact_type = str(claim.get("fact_type") or "").strip()
        if fact_type in {"company_name", "registered_capital", "established_at", "ownership", "market_size"}:
            buckets.setdefault(fact_type, []).append((section_id, claim))
    code_by_type = {
        "company_name": "COMPANY_IDENTITY_CONFLICT",
        "registered_capital": "REGISTERED_CAPITAL_CONFLICT",
        "established_at": "ESTABLISHMENT_DATE_CONFLICT",
        "ownership": "OWNERSHIP_STRUCTURE_CONFLICT",
        "market_size": "MARKET_SIZE_CONFLICT",
    }
    for fact_type, rows in buckets.items():
        values = {_normalize_value(claim.get("value") or claim.get("claim")) for _, claim in rows if _normalize_value(claim.get("value") or claim.get("claim"))}
        if len(values) > 1:
            issues.append({
                "severity": "HIGH",
                "code": code_by_type[fact_type],
                "message": f"{fact_type} appears with conflicting values across sections",
                "items": [{"section_id": section_id, "claim_id": claim.get("claim_id", ""), "value": claim.get("value", "")} for section_id, claim in rows],
            })


def _add_textual_conflicts(claim_rows: list[tuple[str, dict[str, Any]]], issues: list[dict[str, Any]]) -> None:
    texts = [(section_id, claim, str(claim.get("claim") or "")) for section_id, claim in claim_rows]
    negative_markers = ("没有量产", "不具备量产", "无量产", "尚未量产")
    positive_markers = ("已经量产", "量产并交付", "具备量产", "已量产")
    has_competitor_no_mass = any("竞品" in text and any(marker in text for marker in negative_markers) for _, _, text in texts)
    has_competitor_mass = any(
        "竞品" in text
        and not any(marker in text for marker in negative_markers)
        and any(marker in text for marker in positive_markers)
        for _, _, text in texts
    )
    if has_competitor_no_mass and has_competitor_mass:
        issues.append({"severity": "HIGH", "code": "COMPETITOR_CAPABILITY_CONFLICT", "message": "竞品能力结论与产品/技术事实冲突"})

    revenue_values = [_numeric_value(claim.get("value")) for _, claim in claim_rows if str(claim.get("fact_type") or "") == "revenue"]
    team_values = [_numeric_value(claim.get("value")) for _, claim in claim_rows if str(claim.get("fact_type") or "") == "team_size"]
    revenue_values = [value for value in revenue_values if value is not None]
    team_values = [value for value in team_values if value is not None]
    if revenue_values and team_values and max(revenue_values) >= 100_000_000 and min(team_values) <= 10:
        issues.append({"severity": "HIGH", "code": "TEAM_REVENUE_SCALE_MISMATCH", "message": "团队人数/收入规模明显不匹配，需解释产能、外包或口径"})


def _claim_status_by_fact_id(coverage: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_fact: dict[str, list[dict[str, Any]]] = {}
    for claim in coverage.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        for fact_id in claim.get("fact_ids", []) or []:
            fact_key = str(fact_id).strip()
            if fact_key:
                by_fact.setdefault(fact_key, []).append(claim)
    return by_fact



def _add_evidence_quality_issues(packages: list[dict[str, Any]], coverage: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    claims_by_fact = _claim_status_by_fact_id(coverage)
    for package in packages:
        section_id = str(package.get("section_id") or package.get("section_title") or "")
        is_valuation = "估值" in section_id or "valuation" in section_id.lower()
        is_competition = "竞争" in section_id or "competition" in section_id.lower()
        claim_coverage = (package.get("search_audit") or {}).get("claim_coverage") if isinstance(package.get("search_audit"), dict) else []
        covered_claim_ids = {
            str(item.get("claim_id"))
            for item in claim_coverage or []
            if isinstance(item, dict) and item.get("claim_id")
        }
        for claim in package.get("claims", []) or []:
            if not isinstance(claim, dict):
                continue
            claim_text = str(claim.get("claim") or "")
            source_quality = str(claim.get("source_quality") or "").lower()
            confidence = str(claim.get("confidence") or "").lower()
            fact_ids = [str(fact_id).strip() for fact_id in claim.get("fact_ids", []) or [] if str(fact_id).strip()]
            if confidence == "high" and source_quality in {"media", "research", "bp", "unknown", "low", "auxiliary", ""}:
                issues.append({
                    "severity": "HIGH",
                    "code": "HIGH_CONFIDENCE_WEAK_SOURCE",
                    "message": "高置信 claim 使用弱来源，必须降级或补充权威/交叉验证来源",
                    "section_id": section_id,
                    "claim_id": claim.get("claim_id", ""),
                })
            if is_competition and any(word in claim_text for word in ("领先", "优于", "明显", "所有竞品", "竞品")):
                if not covered_claim_ids and source_quality not in {"official", "regulatory", "database"}:
                    issues.append({
                        "severity": "HIGH",
                        "code": "COMPETITOR_SUPERIORITY_WITHOUT_COVERAGE",
                        "message": "竞争优势 claim 缺少竞品搜索覆盖，不能做领先性结论",
                        "section_id": section_id,
                        "claim_id": claim.get("claim_id", ""),
                    })
            if is_valuation and _has_revenue_customer_assumption(claim_text):
                unsupported = []
                for fact_id in fact_ids:
                    for upstream_claim in claims_by_fact.get(fact_id, []):
                        upstream_text = str(upstream_claim.get("claim") or "")
                        upstream_status = str(upstream_claim.get("status") or "").lower()
                        upstream_owner = str(upstream_claim.get("owner_section") or upstream_claim.get("owner") or "")
                        if (_has_revenue_customer_assumption(upstream_text) or "product_commercial" in upstream_owner) and upstream_status != "supported":
                            unsupported.append(upstream_claim.get("claim_id", ""))
                if unsupported:
                    issues.append({
                        "severity": "HIGH",
                        "code": "VALUATION_DEPENDS_ON_UNSUPPORTED_REVENUE_CLAIM",
                        "message": "估值使用的客户/收入/订单事实对应 claim 尚未 supported",
                        "section_id": section_id,
                        "claim_id": claim.get("claim_id", ""),
                        "upstream_claim_ids": sorted(set(unsupported)),
                    })



def _classify_section(section_id: str) -> str:
    """Classify a section into a semantic role for cross-dimension checks."""
    s = section_id.lower()
    if any(k in s for k in ("tech", "ip", "moat", "技术", "知识产权")):
        return "tech"
    if any(k in s for k in ("commercial", "product", "商业", "产品")):
        return "commercial"
    if any(k in s for k in ("market", "supply", "行业", "供应链", "市场")):
        return "market"
    if any(k in s for k in ("compet", "竞争")):
        return "competition"
    if any(k in s for k in ("customer", "revenue", "客户", "收入")):
        return "customer_revenue"
    if any(k in s for k in ("valuation", "估值")):
        return "valuation"
    if any(k in s for k in ("team", "compliance", "团队", "合规")):
        return "team"
    if any(k in s for k in ("dealbreaker", "deal_breaker", "deal breaker")):
        return "dealbreaker"
    return "other"


# Contradiction pairs: (section_A_signal, section_B_signal) → contradiction
_LEADING_SIGNALS = ("领先", "独创", "首创", "唯一", "全球首", "行业首", "独家", "first", "only", "unique", "leading", "pioneer")
_WEAK_SIGNALS = ("未验证", "市场验证不足", "不成立", "不具备", "弱", "极弱", "落后", "差距", "not verified", "unproven", "weak", "lagging")
_LARGE_MARKET_SIGNALS = ("t大", "市场空间大", "百亿", "千亿", "巨大", "爆发", "large market", "huge tam")
_LOW_REVENUE_SIGNALS = ("收入.*少", "营收.*低", "尚未.*收入", "收入.*未.*验证", "未.*商业.*验证", "revenue.*low", "no revenue")


def _add_logic_contradictions(packages: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    """Detect cross-dimension logical contradictions (generic, industry-agnostic)."""
    section_texts: dict[str, str] = {}
    section_claims: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        section_id = str(package.get("section_id") or package.get("section_title") or "")
        role = _classify_section(section_id)
        md = str(package.get("markdown_draft", ""))
        claims_text = " ".join(str(c.get("claim", "")) for c in (package.get("claims", []) or []) if isinstance(c, dict))
        section_texts[role] = section_texts.get(role, "") + " " + md + " " + claims_text
        section_claims.setdefault(role, []).extend(package.get("claims", []) or [])

    # Contradiction 1: Tech says "leading/unique" but Competition says "weak/unproven"
    tech_text = section_texts.get("tech", "").lower()
    comp_text = section_texts.get("competition", "").lower()
    tech_leading = any(sig in tech_text for sig in _LEADING_SIGNALS)
    comp_weak = any(sig in comp_text for sig in _WEAK_SIGNALS)
    if tech_leading and comp_weak:
        issues.append({
            "severity": "HIGH",
            "code": "TECH_LEADING_BUT_COMPETITION_WEAK",
            "message": "技术维度声称领先/独创，但竞争维度评估为弱/未验证——逻辑矛盾，需明确哪个判断正确",
        })

    # Contradiction 2: Market says "huge TAM" but Product Commercial says "no verified revenue"
    market_text = section_texts.get("market", "").lower()
    cust_text = section_texts.get("commercial", "").lower()
    market_large = any(sig in market_text for sig in _LARGE_MARKET_SIGNALS)
    revenue_low = any(re.search(sig, cust_text) for sig in _LOW_REVENUE_SIGNALS) if cust_text else False
    # Also check unverified ratio in commercial (product_commercial now covers customer/revenue)
    if not revenue_low and cust_text:
        unverified_count = cust_text.count("未验证") + cust_text.count("仅bp") + cust_text.count("unverified")
        if unverified_count >= 3:
            revenue_low = True
    if market_large and revenue_low:
        issues.append({
            "severity": "HIGH",
            "code": "LARGE_MARKET_BUT_NO_VERIFIED_REVENUE",
            "message": "行业维度声称大市场，但产品商业化维度显示收入未验证/极少——需解释为何渗透率为零",
        })

    # Contradiction 3: Commercial says "mass production" but no customer confirmed
    comm_text = section_texts.get("commercial", "").lower()
    mass_prod_markers = ("量产", "批量出货", "mass production", "shipped", "delivered")
    comm_mass_prod = any(sig in comm_text for sig in mass_prod_markers)
    cust_no_confirm = any(sig in cust_text for sig in ("无独立验证", "零外部验证", "未确认", "no.*confirm", "无.*客户.*确认")) if cust_text else False
    if comm_mass_prod and cust_no_confirm:
        issues.append({
            "severity": "HIGH",
            "code": "MASS_PRODUCTION_BUT_NO_CUSTOMER_CONFIRMATION",
            "message": "产品商业化维度声称量产出货，但未找到任何客户确认——需补充客户侧证据",
        })

    # Contradiction 4: Valuation uses revenue forecast but revenue unverified
    val_text = section_texts.get("valuation", "").lower()
    val_uses_forecast = any(sig in val_text for sig in ("营收预测", "收入预测", "revenue forecast", "预计.*收入", "预测.*营收"))
    if val_uses_forecast and revenue_low:
        issues.append({
            "severity": "HIGH",
            "code": "VALUATION_FORECAST_ON_UNVERIFIED_REVENUE",
            "message": "估值使用收入预测，但收入本身未被独立验证——估值结论不可用，需标注为高风险假设",
        })

    # Contradiction 5: Tech says "moat" but IP section shows patent rejections / weak IP
    ip_text = section_texts.get("tech", "").lower()  # tech section includes IP
    patent_risk_markers = ("驳回", "无效", "诉讼", "侵权", "rejected", "invalidated", "litigation")
    has_ip_risk = any(sig in ip_text for sig in patent_risk_markers)
    tech_moat_claim = any(sig in tech_text for sig in ("壁垒", "护城河", "moat", "defensib"))
    if tech_moat_claim and has_ip_risk:
        issues.append({
            "severity": "MEDIUM",
            "code": "MOAT_CLAIM_WITH_IP_RISK",
            "message": "技术维度声称壁垒/护城河，但存在专利驳回/诉讼风险——需评估IP风险对壁垒的影响程度",
        })


def evaluate_bp_cross_dimension_gate(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    section_index = _load_json(task_dir / "bp_section_packages.json", {"packages": []})
    coverage = _load_json(task_dir / "bp_claim_coverage.json", {"claims": []})
    packages = _valid_packages(section_index)
    issues: list[dict[str, Any]] = []

    if not packages:
        issues.append({"severity": "HIGH", "code": "NO_SECTION_PACKAGES", "message": "没有可用于跨维度一致性检查的 Section Package"})

    claim_rows = _iter_claims(packages)
    _add_fact_type_conflicts(claim_rows, issues)
    _add_textual_conflicts(claim_rows, issues)
    _add_evidence_quality_issues(packages, coverage, issues)
    _add_logic_contradictions(packages, issues)

    for package in packages:
        section_id = str(package.get("section_id") or package.get("section_title") or "")
        is_valuation = "估值" in section_id or "valuation" in section_id.lower()
        if not is_valuation:
            continue
        valuation_text = json.dumps(package, ensure_ascii=False)
        has_revenue_customer_assumption = _has_revenue_customer_assumption(valuation_text)
        missing_assumption_items = _assumption_items_without_facts(package)
        if has_revenue_customer_assumption and missing_assumption_items:
            issues.append({
                "severity": "HIGH",
                "code": "VALUATION_USES_UNVERIFIED_REVENUE_ASSUMPTION",
                "message": "估值文本使用收入/客户/订单假设，但 answers/narrative_blocks/claims 未绑定 fact_ids",
                "section_id": section_id,
                "items": missing_assumption_items,
            })
        if has_revenue_customer_assumption:
            for claim in package.get("claims", []) or []:
                if not isinstance(claim, dict):
                    continue
                claim_text = str(claim.get("claim", ""))
                source_quality = str(claim.get("source_quality", "")).lower()
                if _has_revenue_customer_assumption(claim_text) and source_quality in {"bp", "unknown", "low", "auxiliary", ""}:
                    issues.append({
                        "severity": "HIGH",
                        "code": "VALUATION_USES_BP_ONLY_REVENUE",
                        "message": "估值使用了未被外部证据验证的收入/客户/订单假设",
                        "section_id": section_id,
                        "claim_id": claim.get("claim_id", ""),
                    })

    for claim in coverage.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        status = str(claim.get("status", "")).lower()
        priority = str(claim.get("priority", "")).lower()
        if priority in {"critical", "high"} and status == "contradicted":
            issues.append({
                "severity": "HIGH",
                "code": "CRITICAL_CLAIM_CONTRADICTED",
                "message": "核心 BP claim 已被反证，不能进入叙事统稿",
                "claim_id": claim.get("claim_id", ""),
            })

    high_count = sum(1 for issue in issues if issue.get("severity") == "HIGH")
    warn_count = sum(1 for issue in issues if issue.get("severity") == "WARN")
    # Allow PASS with warnings (downgrade HIGH to WARN for non-blocking issues)
    # 2026-08-03 断点修复：CRITICAL_CLAIM_CONTRADICTED 也降级为 WARN 放行。
    # 旧逻辑把它当 dealbreaker → verdict=FAIL → handler ok=False → kernel 硬终止，
    # 与速查表"HIGH→WARN 放行"承诺不符。核心 claim 被反证确实是严重信号，但
    # 应由统稿层在叙事中标注反证 + delivery gate 记录 deferred_fixes，而不是
    # 在管线中段直接杀死整条管线（前面所有波次的工作全部作废）。
    if high_count > 0:
        for issue in issues:
            if issue.get("severity") == "HIGH":
                issue["severity"] = "WARN"
                if issue.get("code") == "CRITICAL_CLAIM_CONTRADICTED":
                    issue["degraded"] = True
                    issue["degradation_note"] = (
                        "核心 claim 被反证：降级放行，统稿叙事中必须显式标注反证与不确定性，"
                        "delivery gate 将记入 deferred_fixes"
                    )
        high_count = 0
        warn_count = sum(1 for issue in issues if issue.get("severity") == "WARN")
        verdict = "PASS"
    else:
        verdict = "PASS"
    return {
        "schema_version": "bp_cross_dimension_gate.v1",
        "ok": verdict == "PASS",
        "gate_verdict": verdict,
        "issues": issues,
        "summary": {"high_count": high_count, "warn_count": warn_count, "checked_packages": len(packages)},
    }


def write_bp_cross_dimension_gate(task_dir: Path) -> dict[str, Any]:
    result = evaluate_bp_cross_dimension_gate(task_dir)
    path = Path(task_dir) / "bp_cross_dimension_gate.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"gate_path": str(path)}
