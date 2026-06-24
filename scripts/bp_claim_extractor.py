#!/usr/bin/env python3
"""Rule-based BP claim inventory extractor."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


CLAIM_RULES: list[dict[str, Any]] = [
    {
        "claim_type": "customer",
        "keywords": ("客户", "医院", "采购订单", "订单", "合同", "中标", "签署", "试点", "付费"),
        "owner_section": "bp_product_commercial",
        "priority": "critical",
        "evidence_required": ["contract", "external_source", "interview"],
    },
    {
        "claim_type": "revenue",
        "keywords": ("收入", "营收", "销售额", "回款", "ARR", "MRR", "GMV"),
        "owner_section": "bp_customer_revenue_validation",
        "priority": "critical",
        "evidence_required": ["financial_statement", "bank_record", "contract"],
    },
    {
        "claim_type": "financing",
        "keywords": ("融资", "Pre-A", "A轮", "B轮", "天使轮", "投资", "募资"),
        "owner_section": "bp_valuation_return",
        "priority": "high",
        "evidence_required": ["financing_document", "filing", "interview"],
    },
    {
        "claim_type": "valuation",
        "keywords": ("估值", "投前", "投后", "市值", "估值水平"),
        "owner_section": "bp_valuation_return",
        "priority": "high",
        "evidence_required": ["financing_document", "cap_table", "external_source"],
    },
    {
        "claim_type": "patent",
        "keywords": ("专利", "软著", "知识产权", "发明", "实用新型", "著作权"),
        "owner_section": "bp_tech_ip_moat",
        "priority": "high",
        "evidence_required": ["patent", "ipr_registry", "external_source"],
    },
    {
        "claim_type": "team",
        "keywords": ("团队", "创始人", "核心成员", "来自", "履历", "博士", "清华", "北大", "腾讯", "阿里", "华为"),
        "owner_section": "bp_company_team_compliance",
        "priority": "high",
        "evidence_required": ["resume", "interview", "external_source"],
    },
    {
        "claim_type": "market",
        "keywords": ("市场", "TAM", "SAM", "SOM", "规模", "空间", "行业", "增长率", "CAGR", "百亿", "千亿"),
        "owner_section": "bp_market_supply_chain",
        "priority": "high",
        "evidence_required": ["external_source", "industry_report"],
    },
    {
        "claim_type": "product",
        "keywords": ("产品", "平台", "系统", "机器人", "SaaS", "解决方案", "功能", "量产", "商业化"),
        "owner_section": "bp_product_commercial",
        "priority": "high",
        "evidence_required": ["product_demo", "customer_reference", "external_source"],
    },
    {
        "claim_type": "compliance",
        "keywords": ("合规", "资质", "许可证", "注册证", "认证", "医疗器械", "审批", "备案", "ISO", "GMP"),
        "owner_section": "bp_company_team_compliance",
        "priority": "critical",
        "evidence_required": ["filing", "license", "external_source"],
    },
    {
        "claim_type": "technology",
        "keywords": ("技术", "算法", "模型", "AI", "大模型", "性能", "准确率", "壁垒", "研发"),
        "owner_section": "bp_tech_ip_moat",
        "priority": "high",
        "evidence_required": ["technical_validation", "benchmark", "external_source"],
    },
]

_RULE_BY_TYPE = {rule["claim_type"]: rule for rule in CLAIM_RULES}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).strip(" -:：。；;，,")


def _normalize_claim(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def _split_excerpts(text: str) -> list[str]:
    excerpts: list[str] = []
    for line in str(text or "").splitlines():
        line = _clean_text(line)
        if not line:
            continue
        parts = re.split(r"(?<=[。！？!?；;])\s*", line)
        for part in parts:
            part = _clean_text(part)
            if part:
                excerpts.append(part[:220])
    return excerpts


def _rule_matches(rule: dict[str, Any], text: str) -> bool:
    lowered = text.lower()
    return any(str(keyword).lower() in lowered for keyword in rule["keywords"])


def _claim_from_excerpt(excerpt: str, rule: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "claim": _clean_text(excerpt),
        "claim_type": rule["claim_type"],
        "owner_section": rule["owner_section"],
        "priority": rule["priority"],
        "source": source,
        "evidence_required": list(rule["evidence_required"]),
        "raw_excerpt": _clean_text(excerpt),
    }


def _extract_text_claims(text: str, source: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for excerpt in _split_excerpts(text):
        matched = False
        for rule in CLAIM_RULES:
            if _rule_matches(rule, excerpt):
                claims.append(_claim_from_excerpt(excerpt, rule, source))
                matched = True
        if not matched and len(excerpt) >= 12:
            claims.append({
                "claim": excerpt,
                "claim_type": "other",
                "owner_section": "bp_dealbreaker_risk",
                "priority": "low",
                "source": source,
                "evidence_required": ["external_source"],
                "raw_excerpt": excerpt,
            })
    return claims


def _profile_text_fragments(profile: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    keys = (
        "company_summary",
        "business_summary",
        "product_summary",
        "team_summary",
        "market_summary",
        "financing_summary",
    )
    for key in keys:
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            fragments.append(value)
    return fragments


def _claim_from_plan_row(row: dict[str, Any]) -> dict[str, Any] | None:
    claim = _clean_text(row.get("claim") or row.get("claim_text") or row.get("question") or "")
    if not claim:
        return None
    claim_type = _clean_text(row.get("claim_type") or row.get("type") or "other").lower()
    if claim_type not in _RULE_BY_TYPE and claim_type != "other":
        claim_type = "other"
    rule = _RULE_BY_TYPE.get(claim_type)
    return {
        "claim": claim,
        "claim_type": claim_type,
        "owner_section": row.get("owner_section") or (rule or {}).get("owner_section") or "bp_dealbreaker_risk",
        "priority": str(row.get("priority") or (rule or {}).get("priority") or "medium").lower(),
        "source": "research_plan",
        "evidence_required": row.get("evidence_required") or list((rule or {}).get("evidence_required") or ["external_source"]),
        "raw_excerpt": _clean_text(row.get("raw_excerpt") or claim),
    }


def _is_duplicate(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> bool:
    candidate_norm = _normalize_claim(candidate.get("claim", ""))
    candidate_type = candidate.get("claim_type")
    if not candidate_norm:
        return True
    for item in existing:
        item_norm = _normalize_claim(item.get("claim", ""))
        if not item_norm:
            continue
        same_type = item.get("claim_type") == candidate_type
        exact_match = candidate_norm == item_norm
        existing_more_specific = candidate_norm in item_norm and len(item_norm) > len(candidate_norm)
        if same_type and (exact_match or existing_more_specific):
            return True
    return False


def _replace_less_specific_duplicate(claims: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    candidate_norm = _normalize_claim(candidate.get("claim", ""))
    candidate_type = candidate.get("claim_type")
    if not candidate_norm:
        return False
    for index, item in enumerate(claims):
        item_norm = _normalize_claim(item.get("claim", ""))
        same_type = item.get("claim_type") == candidate_type
        if same_type and item_norm and item_norm in candidate_norm and len(candidate_norm) > len(item_norm):
            claims[index] = candidate
            return True
    return False


def _add_claim(claims: list[dict[str, Any]], claim: dict[str, Any]) -> None:
    priority = str(claim.get("priority") or "medium").lower()
    if priority not in {"critical", "high", "medium", "low"}:
        priority = "medium"
    claim["priority"] = priority
    if not isinstance(claim.get("evidence_required"), list):
        claim["evidence_required"] = [str(claim.get("evidence_required"))]
    claim["claim"] = _clean_text(claim.get("claim", ""))
    claim["raw_excerpt"] = _clean_text(claim.get("raw_excerpt") or claim.get("claim") or "")
    if not claim["claim"] or _is_duplicate(claim, claims):
        return
    if _replace_less_specific_duplicate(claims, claim):
        return
    claims.append(claim)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# BP Claim Inventory - {payload.get('task_id', '')}",
        "",
        f"- Entity: {payload.get('entity', '')}",
        f"- Total claims: {len(payload.get('claims', []))}",
        "",
        "| Claim ID | Priority | Type | Owner | Source | Claim | Evidence Required |",
        "|---|---|---|---|---|---|---|",
    ]
    for claim in payload.get("claims", []):
        evidence = ", ".join(claim.get("evidence_required", []) or [])
        claim_text = str(claim.get("claim", "")).replace("|", "｜")
        lines.append(
            f"| {claim.get('claim_id', '')} | {claim.get('priority', '')} | {claim.get('claim_type', '')} | "
            f"{claim.get('owner_section', '')} | {claim.get('source', '')} | {claim_text} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def build_claim_inventory(task_dir: str | Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    profile = _load_json(task_dir / "bp_step0_profile.json", {})
    research_plan = _load_json(task_dir / "bp_research_plan.json", {})
    ocr_text = (task_dir / "bp_ocr_text.txt").read_text(encoding="utf-8") if (task_dir / "bp_ocr_text.txt").exists() else ""

    task_id = str(profile.get("task_id") or research_plan.get("task_id") or task_dir.name)
    entity = str(profile.get("entity") or profile.get("company_name") or research_plan.get("entity") or "")

    claims: list[dict[str, Any]] = []
    for claim in _extract_text_claims(ocr_text, "bp_text"):
        _add_claim(claims, claim)
    for fragment in _profile_text_fragments(profile):
        for claim in _extract_text_claims(fragment, "step0_profile"):
            _add_claim(claims, claim)
    for row in research_plan.get("claim_matrix", []) or []:
        if isinstance(row, dict):
            claim = _claim_from_plan_row(row)
            if claim:
                _add_claim(claims, claim)

    for index, claim in enumerate(claims, start=1):
        claim["claim_id"] = f"BC{index:03d}"

    payload = {
        "schema_version": "bp_claim_inventory.v1",
        "task_id": task_id,
        "entity": entity,
        "claims": claims,
    }
    (task_dir / "bp_claim_inventory.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (task_dir / "bp_claim_inventory.md").write_text(_render_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract BP claim inventory from task_dir inputs")
    parser.add_argument("task_dir")
    args = parser.parse_args()
    payload = build_claim_inventory(args.task_dir)
    print(json.dumps({"ok": True, "task_id": payload["task_id"], "count": len(payload["claims"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
