#!/usr/bin/env python3
"""Rule-based cross-dimension consistency gate for IR pipeline.

移植自 bp_cross_dimension_gate.py，适配 IR 的 10 step 结构。
检查不同 step 之间的关键指标一致性（市值、营收、PE 等）和逻辑矛盾。
FAIL → WARN 放行（不阻断，记录到 deferred_fixes）。
"""
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


def _numeric_value(value: Any) -> float | None:
    """提取数值，支持中文单位（万/亿）。"""
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


def _classify_step(step_id: str) -> str:
    """将 IR step 分类为语义角色，用于跨维度检查。"""
    s = step_id.lower()
    if "data" in s or "step1" in s:
        return "data"
    if "industry" in s or "step2" in s:
        return "industry"
    if "biz" in s or "business" in s or "step3" in s:
        return "business"
    if "finance" in s or "step4" in s:
        return "finance"
    if "mgmt" in s or "management" in s or "step5" in s:
        return "management"
    if "macro" in s:
        return "macro"
    if "valuation" in s or "step6b" in s:
        return "valuation"
    if "insight" in s or "step6" in s:
        return "insight"
    if "risk" in s or "step7" in s:
        return "risk"
    if "master" in s or "step8" in s:
        return "master"
    return "other"


def _extract_numeric_facts_from_sidecar(sidecar_path: Path) -> list[dict[str, Any]]:
    """从 step sidecar 中提取数值型 fact，用于跨 step 一致性检查。"""
    data = _load_json(sidecar_path, {})
    facts = data.get("facts", []) if isinstance(data, dict) else []
    numeric_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        val = _numeric_value(fact.get("value"))
        if val is not None:
            numeric_facts.append({
                "fact_id": fact.get("fact_id", ""),
                "claim": fact.get("claim", ""),
                "value": val,
                "raw_value": fact.get("value", ""),
                "unit": fact.get("unit", ""),
                "source_url": fact.get("source_url", ""),
            })
    return numeric_facts


def _check_metric_consistency(
    all_step_facts: dict[str, list[dict[str, Any]]],
    issues: list[dict[str, Any]],
) -> None:
    """检查同一指标在不同 step 中是否一致（市值、营收、PE 等）。

    策略：按 claim 文本中的关键指标词分组，同指标不同值且差异 >15% 则报 WARN。
    """
    # 关键指标模式
    metric_patterns = {
        "market_cap": re.compile(r"市值|market\s*cap", re.IGNORECASE),
        "revenue": re.compile(r"营收|营业收入|revenue|收入", re.IGNORECASE),
        "net_profit": re.compile(r"净利润|net\s*profit|净利", re.IGNORECASE),
        "pe_ratio": re.compile(r"市盈率|PE(?:\s|比|倍数)|price.to.earnings", re.IGNORECASE),
        "eps": re.compile(r"每股收益|EPS", re.IGNORECASE),
    }

    for metric_name, pattern in metric_patterns.items():
        # 收集所有 step 中匹配该指标的 fact
        metric_values: list[tuple[str, float, str]] = []  # (step, value, claim)
        for step_name, facts in all_step_facts.items():
            for fact in facts:
                claim_text = str(fact.get("claim", ""))
                if pattern.search(claim_text):
                    metric_values.append((step_name, fact["value"], claim_text))

        if len(metric_values) < 2:
            continue

        # 检查跨 step 一致性
        values = [v for _, v, _ in metric_values]
        if not values:
            continue
        max_val = max(values)
        min_val = min(values)
        if min_val == 0:
            continue
        ratio = max_val / min_val
        if ratio > 1.15:  # 差异超过 15%
            steps_involved = list(set(s for s, _, _ in metric_values))
            sample_claims = [c for _, _, c in metric_values[:3]]
            issues.append({
                "severity": "MEDIUM",
                "code": f"CROSS_STEP_{metric_name.upper()}_INCONSISTENCY",
                "message": (
                    f"{metric_name} 在不同 step 中的值差异超过 15%: "
                    f"min={min_val:.0f}, max={max_val:.0f}, ratio={ratio:.2f}"
                ),
                "steps": steps_involved,
                "sample_claims": sample_claims,
            })


def _check_valuation_vs_finance(
    all_step_facts: dict[str, list[dict[str, Any]]],
    step_texts: dict[str, str],
    issues: list[dict[str, Any]],
) -> None:
    """检查估值假设是否与财务数据一致。"""
    val_facts = all_step_facts.get("step6_valuation", [])
    fin_facts = all_step_facts.get("step3_finance", [])

    if not val_facts or not fin_facts:
        return

    # 检查：估值中使用的增长率假设 vs 财务数据中的历史增长率
    val_text = step_texts.get("step6_valuation", "").lower()
    fin_text = step_texts.get("step3_finance", "").lower()

    # 高增长假设但历史低增长
    high_growth_markers = ("高增长", "快速增长", "高速增长", "high growth", "加速增长")
    low_growth_markers = ("低增长", "增长放缓", "增速下降", "负增长", "low growth", "declining")

    val_high = any(m in val_text for m in high_growth_markers)
    fin_low = any(m in fin_text for m in low_growth_markers)

    if val_high and fin_low:
        issues.append({
            "severity": "MEDIUM",
            "code": "VALUATION_GROWTH_VS_FINANCE_CONFLICT",
            "message": "估值假设高增长，但财务数据显示增长放缓/下降——需明确增长假设的依据",
        })

    # 检查：估值 PE 倍数 vs 财务 PE
    val_pe = [_numeric_value(f.get("raw_value")) for f in val_facts
              if re.search(r"PE|市盈率", str(f.get("claim", "")), re.IGNORECASE)]
    fin_pe = [_numeric_value(f.get("raw_value")) for f in fin_facts
              if re.search(r"PE|市盈率", str(f.get("claim", "")), re.IGNORECASE)]

    val_pe = [v for v in val_pe if v is not None and v > 0]
    fin_pe = [v for v in fin_pe if v is not None and v > 0]

    if val_pe and fin_pe:
        val_avg = sum(val_pe) / len(val_pe)
        fin_avg = sum(fin_pe) / len(fin_pe)
        if fin_avg > 0 and abs(val_avg - fin_avg) / fin_avg > 0.3:
            issues.append({
                "severity": "MEDIUM",
                "code": "VALUATION_PE_VS_FINANCE_PE_MISMATCH",
                "message": (
                    f"估值使用的 PE ({val_avg:.1f}) 与财务报告的 PE ({fin_avg:.1f}) "
                    f"差异超过 30%，需解释估值溢价/折价原因"
                ),
            })


def _check_logic_contradictions(
    step_texts: dict[str, str],
    issues: list[dict[str, Any]],
) -> None:
    """检测跨 step 逻辑矛盾。"""
    industry_text = step_texts.get("step1_industry", "").lower()
    business_text = step_texts.get("step2_biz", "").lower()
    risk_text = step_texts.get("step8_risk", "").lower()
    insight_text = step_texts.get("step7_insight", "").lower()

    # 矛盾1: 行业分析说"红海/高度竞争"，但差异化洞察说"蓝海/无竞争"
    red_ocean = ("红海", "激烈竞争", "高度竞争", "red ocean", "fiercely competitive")
    blue_ocean = ("蓝海", "无竞争", "低竞争", "blue ocean", "no competition")

    industry_red = any(m in industry_text for m in red_ocean)
    insight_blue = any(m in insight_text for m in blue_ocean)
    if industry_red and insight_blue:
        issues.append({
            "severity": "MEDIUM",
            "code": "INDUSTRY_RED_OCEAN_BUT_BLUE_OCEAN_CLAIM",
            "message": "行业分析判断为红海/高竞争，但差异化洞察声称蓝海/无竞争——逻辑矛盾",
        })

    # 矛盾2: 商业模式说"轻资产"，但财务数据显示重资产（高 capex/固定资产）
    asset_light = ("轻资产", "asset light", "低资本开支", "平台模式")
    asset_heavy = ("重资产", "高资本开支", "高固定资产", "asset heavy", "capex")

    biz_light = any(m in business_text for m in asset_light)
    fin_heavy = any(m in step_texts.get("step3_finance", "").lower() for m in asset_heavy)
    if biz_light and fin_heavy:
        issues.append({
            "severity": "MEDIUM",
            "code": "ASSET_LIGHT_CLAIM_VS_HEAVY_FINANCIALS",
            "message": "商业模式声称轻资产，但财务数据显示高资本开支/重资产——需解释",
        })

    # 矛盾3: 管理层评估正面，但风险章节列出重大治理风险
    mgmt_positive = ("优秀", "卓越", "经验丰富", "杰出", "outstanding", "excellent")
    governance_risk = ("治理风险", "关联交易", "利益输送", "管理层动荡", "governance risk", "related party")

    mgmt_text = step_texts.get("step4_mgmt", "").lower()
    mgmt_good = any(m in mgmt_text for m in mgmt_positive)
    risk_governance = any(m in risk_text for m in governance_risk)
    if mgmt_good and risk_governance:
        issues.append({
            "severity": "LOW",
            "code": "POSITIVE_MGMT_BUT_GOVERNANCE_RISK",
            "message": "管理层评估正面，但风险分析列出治理风险——需评估风险对管理层评价的影响",
        })


def evaluate_ir_cross_dimension_gate(task_dir: Path) -> dict[str, Any]:
    """对 IR 管线做跨维度一致性检查。"""
    task_dir = Path(task_dir)

    # 收集各 step 的 facts sidecar
    all_step_facts: dict[str, list[dict[str, Any]]] = {}
    step_texts: dict[str, str] = {}

    step_names = [
        "step1_data", "step1_industry", "step2_biz", "step3_finance",
        "step4_mgmt", "step6_valuation", "step7_insight", "step8_risk",
    ]

    for step_name in step_names:
        # facts sidecar
        facts_path = task_dir / f"{step_name}-facts.json"
        if not facts_path.exists():
            # 尝试带 task_id 前缀
            matches = list(task_dir.glob(f"*-{step_name}-facts.json"))
            if matches:
                facts_path = matches[0]
        if facts_path.exists():
            all_step_facts[step_name] = _extract_numeric_facts_from_sidecar(facts_path)

        # step MD 文本
        md_path = task_dir / f"{step_name}.md"
        if not md_path.exists():
            matches = list(task_dir.glob(f"*-{step_name}.md"))
            if matches:
                md_path = matches[0]
        if md_path.exists():
            step_texts[step_name] = md_path.read_text(encoding="utf-8")

    issues: list[dict[str, Any]] = []

    if not all_step_facts and not step_texts:
        issues.append({
            "severity": "LOW",
            "code": "NO_STEP_DATA",
            "message": "没有找到任何 step 的 sidecar 或 MD 输出，无法做跨维度检查",
        })
    else:
        # 检查1: 关键指标跨 step 一致性
        _check_metric_consistency(all_step_facts, issues)

        # 检查2: 估值假设 vs 财务数据
        _check_valuation_vs_finance(all_step_facts, step_texts, issues)

        # 检查3: 跨 step 逻辑矛盾
        _check_logic_contradictions(step_texts, issues)

    # IR cross-dimension gate: 所有 issue 都是 WARN 级别，不阻断管线
    high_count = sum(1 for i in issues if i.get("severity") == "HIGH")
    medium_count = sum(1 for i in issues if i.get("severity") == "MEDIUM")
    low_count = sum(1 for i in issues if i.get("severity") == "LOW")

    # IR 管线: cross-dimension 问题不阻断，全部降级为 WARN
    for issue in issues:
        if issue.get("severity") == "HIGH":
            issue["severity"] = "WARN"

    verdict = "FAIL" if high_count > 0 else ("PASS_WITH_WARNINGS" if medium_count > 0 else "PASS")

    return {
        "schema_version": "ir_cross_dimension_gate.v1",
        "ok": True,  # cross-dimension 不阻断
        "gate_verdict": verdict,
        "issues": issues,
        "summary": {
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "steps_checked": len(all_step_facts),
            "steps_with_text": len(step_texts),
        },
    }


def write_ir_cross_dimension_gate(task_dir: Path) -> dict[str, Any]:
    """执行跨维度一致性检查并写入结果。"""
    result = evaluate_ir_cross_dimension_gate(task_dir)
    path = Path(task_dir) / "ir_cross_dimension_gate.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"gate_path": str(path)}
