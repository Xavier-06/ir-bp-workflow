#!/usr/bin/env python3
"""Extract per-dimension verdicts and produce a one-page investment judgment summary.

Reads all Wave 1-4 dimension outputs, extracts conclusion sections and confidence
markers, and generates a structured judgment table for the front of the report.

Generic — works for any industry, no hard-coded domain terms.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Dimension classification (generic, based on slug keywords)
_DIMENSION_ROLES = {
    "company_team_compliance": "团队与合规",
    "product_commercial": "产品商业化",
    "tech_ip_moat": "技术与壁垒",
    "market_supply_chain": "市场与供应链",
    "customer_revenue_validation": "客户与收入验证",
    "competition_positioning": "竞争定位",
    "valuation_return": "估值与回报",
    "dealbreaker_risk": "Deal Breaker 风险",
}


def _slug_to_label(slug: str) -> str:
    return _DIMENSION_ROLES.get(slug, slug.replace("_", " ").title())


def _extract_verdict_block(text: str) -> dict[str, str]:
    """Extract conclusion/verdict from a dimension report.

    Looks for the conclusion section and extracts:
    - verdict_text: the main conclusion paragraph
    - confidence: high/medium/low
    - key_data: quantitative data points mentioned
    """
    # Find conclusion section
    conclusion_patterns = [
        r'(?:##?\s*(?:本维度)?结论[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*conclusion[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*综合判断[^#\n]*\n)(.*?)(?=\n##?|\Z)',
    ]
    verdict_text = ""
    for pattern in conclusion_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            verdict_text = match.group(1).strip()
            break

    # Fallback: find the last 500 chars (conclusion is usually at the end)
    if not verdict_text and len(text) > 500:
        verdict_text = text[-500:].strip()

    # Extract confidence level
    confidence = "未知"
    confidence_match = re.search(r'置信度[：:]\s*(高|中|低)', verdict_text)
    if confidence_match:
        confidence = confidence_match.group(1)
    else:
        # Infer from keywords
        if re.search(r'(?:已验证|多源|交叉验证|官方|工商)', verdict_text):
            confidence = "中"
        elif re.search(r'(?:未验证|仅BP|推断|假设)', verdict_text):
            confidence = "低"

    # Extract key quantitative data points
    data_points = re.findall(
        r'[\d,.]+\s*(?:亿|万|%|倍|颗|片|台|个|家|轮|元|美元|USD|RMB)',
        verdict_text
    )[:5]

    return {
        "verdict_text": verdict_text[:300],
        "confidence": confidence,
        "key_data": data_points,
    }


def _extract_risk_flags(text: str, max_flags: int = 3) -> list[str]:
    """Extract top risk/red-flag items from a dimension report."""
    risk_patterns = [
        r'(?:⚠|❌|⛔|风险|red\s*flag|deal\s*breaker)[^。\n]*[。\n]',
        r'(?:FAIL|不成立|不具备|严重|致命|阻断)[^。\n]*[。\n]',
    ]
    flags: list[str] = []
    for pattern in risk_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            flag = match.group(0).strip()[:100]
            if flag and flag not in flags:
                flags.append(flag)
                if len(flags) >= max_flags:
                    return flags
    return flags[:max_flags]


def _extract_data_gaps(text: str, max_gaps: int = 3) -> list[str]:
    """Extract data gap items from a dimension report."""
    gap_patterns = [
        r'(?:data[_ ]?gap|数据缺口|待验证|待补充|需要补充|missing data)[^。\n]*[。\n]',
    ]
    gaps: list[str] = []
    for pattern in gap_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            gap = match.group(0).strip()[:100]
            if gap and gap not in gaps:
                gaps.append(gap)
                if len(gaps) >= max_gaps:
                    return gaps
    return gaps[:max_gaps]


def build_investment_judgment(task_dir: Path) -> dict[str, Any]:
    """Build investment judgment summary from all dimension outputs."""
    task_dir = Path(task_dir)
    dimensions: list[dict[str, Any]] = []

    # Scan all dimension output files
    for dim_file in sorted(task_dir.glob("bp_phase2_*.md")):
        slug = dim_file.stem.replace("bp_phase2_", "")
        label = _slug_to_label(slug)
        try:
            text = dim_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if len(text) < 200:
            continue

        verdict = _extract_verdict_block(text)
        risk_flags = _extract_risk_flags(text)
        data_gaps = _extract_data_gaps(text)

        dimensions.append({
            "slug": slug,
            "label": label,
            "verdict_text": verdict["verdict_text"],
            "confidence": verdict["confidence"],
            "key_data": verdict["key_data"],
            "risk_flags": risk_flags,
            "data_gaps": data_gaps,
        })

    # Also check synthesis output
    synthesis_path = task_dir / "bp_synthesis.md"
    synthesis_verdict = ""
    if synthesis_path.exists():
        try:
            synthesis_text = synthesis_path.read_text(encoding="utf-8")
            # Extract executive summary
            exec_match = re.search(
                r'(?:##?\s*执行摘要[^#\n]*\n)(.*?)(?=\n##?|\Z)',
                synthesis_text, re.DOTALL | re.IGNORECASE
            )
            if exec_match:
                synthesis_verdict = exec_match.group(1).strip()[:500]
        except Exception:
            pass

    # Build overall risk level
    high_risk_count = sum(
        1 for d in dimensions if d["confidence"] == "低"
    )
    # 扫描所有维度的 risk_flags，按内容严重度判断是否为 deal breaker
    _DB_KEYWORDS = (
        "失信", "诉讼", "造假", "违法", "欺诈", "竞业限制", "知识产权纠纷",
        "deal breaker", "阻断", "致命", "致命风险", "不可缓释",
    )
    dealbreaker_count = 0
    for d in dimensions:
        for flag in d.get("risk_flags", []):
            flag_lower = flag.lower()
            if any(kw in flag_lower for kw in _DB_KEYWORDS):
                dealbreaker_count += 1
        # 保留原有逻辑：dealbreaker 维度的所有 risk_flags 都计入
        if "dealbreaker" in d["slug"].lower():
            dealbreaker_count += len(d["risk_flags"])
    # 去重：同一 flag 可能被两个维度同时提取
    dealbreaker_count = min(dealbreaker_count, sum(len(d["risk_flags"]) for d in dimensions))
    total_data_gaps = sum(len(d["data_gaps"]) for d in dimensions)

    # ── 融资阶段感知：T1 放宽 customer/revenue gap 权重 ──
    from scripts.bp_stage_utils import read_stage_from_task
    stage_tier = read_stage_from_task(task_dir, default="T3")

    if dealbreaker_count > 0:
        overall_risk = "HIGH — 存在 Deal Breaker"
    elif high_risk_count >= 3 and stage_tier not in ("T1",):
        # T1 阶段多数维度置信度低是正常状态（无公开数据）
        overall_risk = "HIGH — 多数维度置信度低"
    elif high_risk_count >= 1:
        # T1 进一步判断：如果低置信度主要来自 customer_revenue/valuation，不算 MEDIUM
        if stage_tier == "T1":
            non_customer_low = sum(
                1 for d in dimensions
                if d["confidence"] == "低"
                and d["slug"] not in ("customer_revenue_validation", "valuation_return")
            )
            if non_customer_low >= 2:
                overall_risk = "MEDIUM — 团队/技术等核心维度存在不确定性"
            else:
                overall_risk = "LOW — 核心维度（团队/技术）已初步验证，客户/收入未验证为天使轮正常状态"
        else:
            overall_risk = "MEDIUM — 部分维度存在不确定性"
    else:
        overall_risk = "LOW — 多数维度已验证"

    result = {
        "schema_version": "bp_investment_judgment.v1",
        "dimensions": dimensions,
        "synthesis_executive_summary": synthesis_verdict,
        "overall_risk_level": overall_risk,
        "high_risk_dimension_count": high_risk_count,
        "dealbreaker_flag_count": dealbreaker_count,
        "total_data_gaps": total_data_gaps,
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Write JSON
    json_path = task_dir / "bp_investment_judgment.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Write Markdown summary
    md_lines = [
        "# 投资判断汇总",
        "",
        f"**整体风险评级：{overall_risk}**",
        f"- 高不确定性维度数：{high_risk_count}",
        f"- Deal Breaker 标记数：{dealbreaker_count}",
        f"- 数据缺口总数：{total_data_gaps}",
        "",
        "## 各维度结论一览",
        "",
        "| 维度 | 一句话结论 | 置信度 | 关键数据点 |",
        "|------|-----------|--------|-----------|",
    ]
    for d in dimensions:
        verdict_short = d["verdict_text"][:80].replace("\n", " ")
        data_str = ", ".join(d["key_data"][:3]) if d["key_data"] else "-"
        md_lines.append(f"| {d['label']} | {verdict_short} | {d['confidence']} | {data_str} |")

    md_lines += [
        "",
        "## 关键风险标记",
        "",
    ]
    all_risks = []
    for d in dimensions:
        for flag in d["risk_flags"]:
            all_risks.append(f"- **[{d['label']}]** {flag.strip()}")
    if all_risks:
        md_lines.extend(all_risks[:10])
    else:
        md_lines.append("- 无重大风险标记")

    md_lines += [
        "",
        "## 关键数据缺口",
        "",
    ]
    all_gaps = []
    for d in dimensions:
        for gap in d["data_gaps"]:
            all_gaps.append(f"- **[{d['label']}]** {gap.strip()}")
    if all_gaps:
        md_lines.extend(all_gaps[:10])
    else:
        md_lines.append("- 无重大数据缺口")

    md_path = task_dir / "bp_investment_judgment.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    result["md_path"] = str(md_path)
    result["json_path"] = str(json_path)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = build_investment_judgment(Path(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=False, indent=2))
