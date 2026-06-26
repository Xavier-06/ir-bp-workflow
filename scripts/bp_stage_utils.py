#!/usr/bin/env python3
"""
BP 融资阶段分级工具 — 全管线共用。

将 financing_stage（从 BP 提取的文本）归类为四个等级（T1-T4），
提供每个等级对应的评估框架、估值方法、风险覆盖、DD 重点方向，
以及可直接注入子代理 prompt 的阶段感知块。

使用方式：
    from bp_stage_utils import classify_stage, get_stage_meta, build_stage_prompt_block

    stage_tier = classify_stage(profile.get("financing_stage", ""))
    meta = get_stage_meta(stage_tier)
    prompt_block = build_stage_prompt_block(stage_tier, entity="乾昇真空")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ── 阶段关键词 ─────────────────────────────────────────

T1_KEYWORDS = ("种子", "天使", "seed", "angel")
T2_KEYWORDS = ("pre-a", "pre_a", "prea", "a轮", "a 轮", "series a", "series-a")
T3_KEYWORDS = ("b轮", "b 轮", "series b", "series-b", "pre-b", "pre_b")
T4_KEYWORDS = (
    "c轮", "c 轮", "series c", "series-c",
    "pre-ipo", "d轮", "d+", "ipo", "已上市", "listed",
)

TIER_ORDER = ["T1", "T2", "T3", "T4"]


def classify_stage(financing_stage: str) -> str:
    """将融资阶段文本归类为 T1/T2/T3/T4。

    匹配顺序：T4 → T3 → T2 → T1（默认 T1，最保守）。
    """
    s = str(financing_stage or "").strip().lower()
    if not s or s in ("未融资", "无", "none", "n/a", "unknown", "未知"):
        return "T1"
    for kw in T4_KEYWORDS:
        if kw in s:
            return "T4"
    for kw in T3_KEYWORDS:
        if kw in s:
            return "T3"
    for kw in T2_KEYWORDS:
        if kw in s:
            return "T2"
    return "T1"


# ── 阶段元数据 ─────────────────────────────────────────

STAGE_META: dict[str, dict[str, Any]] = {
    "T1": {
        "label": "极早期（种子/天使）",
        "short_label": "种子/天使轮",
        "valuation_methods": ["可比交易法（同类极早期公司）"],
        "forbidden_valuation_methods": ["PE", "DCF"],
        "customer_required": False,
        "revenue_required": False,
        "early_customer_feedback_bonus": True,
        "team_verification_priority": "critical",
        "tech_differentiation_priority": "critical",
        "ip_priority": "high",
        "financial_audit_required": False,
        "risk_severity_override": {
            "no_customer_revenue": "low",
            "no_financial_data": "low",
            "no_audit_report": "low",
            "team_background_unverified": "critical",
            "tech_not_differentiated": "high",
            "key_person_dependency": "high",
            "ip_ownership_unclear": "high",
            "related_party_competition": "high",
        },
        "dd_focus": [
            "创始人背景深度验证（前雇主、LinkedIn、行业口碑、竞业限制）",
            "技术差异化验证（第三方测试、专利深度分析、与竞品技术对比）",
            "早期客户/试用方访谈（2-3 个，重点看产品反馈和复购意愿）",
            "竞品对标（同赛道早期公司估值区间、融资历程）",
            "关联交易和同业竞争排查（关联公司业务重叠度）",
            "知识产权归属确认（专利发明人、职务发明排查）",
        ],
        "valuation_discount": {
            "liquidity": 0.0,
            "tech_risk": 0.15,
            "key_person": 0.20,
            "customer": 0.0,
            "total_cap": 0.35,
        },
        "moic_expectation": "10-30x（高风险高回报，以组合投资对冲）",
        "typical_hold_period": "5-7 年",
    },
    "T2": {
        "label": "早期（Pre-A/A轮）",
        "short_label": "Pre-A/A轮",
        "valuation_methods": ["可比交易法", "早期 PS（市销率）"],
        "forbidden_valuation_methods": ["PE", "DCF"],
        "customer_required": True,
        "revenue_required": False,
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "critical",
        "tech_differentiation_priority": "critical",
        "ip_priority": "critical",
        "financial_audit_required": False,
        "risk_severity_override": {
            "no_customer_revenue": "high",
            "no_paying_customer": "high",
            "no_financial_data": "medium",
            "no_audit_report": "low",
            "team_background_unverified": "critical",
            "tech_not_differentiated": "critical",
            "no_pmf_evidence": "critical",
            "ip_ownership_unclear": "critical",
            "related_party_competition": "high",
        },
        "dd_focus": [
            "付费客户验证（合同、回款、NPS、留存率）",
            "PMF 验证（留存率、复购率、客单价趋势、用户增长）",
            "产品 roadmap 和技术壁垒深度分析",
            "团队扩张计划（关键岗位招聘进展、期权池设计）",
            "竞品对标（同赛道 A 轮估值区间、融资历程）",
            "知识产权完整尽调（专利组合、FTO 分析）",
        ],
        "valuation_discount": {
            "liquidity": 0.10,
            "tech_risk": 0.10,
            "key_person": 0.15,
            "customer": 0.15,
            "total_cap": 0.50,
        },
        "moic_expectation": "5-15x",
        "typical_hold_period": "4-6 年",
    },
    "T3": {
        "label": "成长期（B轮）",
        "short_label": "B轮",
        "valuation_methods": ["PS（市销率）", "DCF 参考", "可比交易法"],
        "forbidden_valuation_methods": [],
        "customer_required": True,
        "revenue_required": True,
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "high",
        "tech_differentiation_priority": "critical",
        "ip_priority": "critical",
        "financial_audit_required": True,
        "risk_severity_override": {
            "no_customer_revenue": "critical",
            "no_financial_data": "critical",
            "no_audit_report": "critical",
            "team_background_unverified": "high",
            "tech_not_differentiated": "critical",
            "revenue_decline": "critical",
            "customer_concentration_high": "high",
            "gross_margin_decline": "high",
            "related_party_transactions": "high",
        },
        "dd_focus": [
            "收入确认（审计报告、大客户合同、回款周期、收入质量）",
            "增长质量（收入增长率、毛利率趋势、客户集中度、LTV/CAC）",
            "市场地位（市占率变化、竞品动态、行业排名）",
            "规模化能力（产能、交付能力、团队扩张节奏）",
            "财务健康度（现金流、应收账款周转、负债率）",
            "治理结构（董事会构成、股东协议、对赌条款）",
        ],
        "valuation_discount": {
            "liquidity": 0.20,
            "tech_risk": 0.05,
            "key_person": 0.10,
            "customer": 0.20,
            "total_cap": 0.60,
        },
        "moic_expectation": "3-8x",
        "typical_hold_period": "3-5 年",
    },
    "T4": {
        "label": "成熟期（C轮/Pre-IPO）",
        "short_label": "C轮/Pre-IPO",
        "valuation_methods": ["PE（市盈率）", "DCF", "可比公司分析"],
        "forbidden_valuation_methods": [],
        "customer_required": True,
        "revenue_required": True,
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "high",
        "tech_differentiation_priority": "high",
        "ip_priority": "critical",
        "financial_audit_required": True,
        "risk_severity_override": {
            "no_customer_revenue": "critical",
            "no_financial_data": "critical",
            "no_audit_report": "critical",
            "governance_issues": "critical",
            "revenue_decline": "critical",
            "ipo_readiness_gap": "high",
            "related_party_transactions": "critical",
            "historical_compliance_issues": "critical",
            "earnings_quality_concerns": "critical",
        },
        "dd_focus": [
            "盈利能力和路径（毛利率、净利率、EBITDA、自由现金流）",
            "IPO 就绪度（合规历史、治理结构、历史沿革清晰度）",
            "市场天花板（TAM 饱和度、第二增长曲线、国际化）",
            "退出路径（IPO 时间表、并购可能性、二级市场流动性）",
            "对赌和优先条款审查（回购条款、反稀释、优先清算权）",
            "关联交易和利益输送排查",
        ],
        "valuation_discount": {
            "liquidity": 0.15,
            "tech_risk": 0.05,
            "key_person": 0.05,
            "customer": 0.15,
            "total_cap": 0.50,
        },
        "moic_expectation": "2-5x",
        "typical_hold_period": "2-4 年",
    },
}


def get_stage_meta(stage_tier: str) -> dict[str, Any]:
    """获取阶段元数据，无效 tier 回退到 T1。"""
    return STAGE_META.get(stage_tier, STAGE_META["T1"])


# ── 阶段感知 Prompt 块 ─────────────────────────────────

def build_stage_prompt_block(stage_tier: str, entity: str = "") -> str:
    """生成可注入子代理 prompt 的阶段感知文本块。

    返回 Markdown 格式的文本，包含评估框架调整、DD 重点方向、风险严重度覆盖。
    """
    meta = get_stage_meta(stage_tier)
    entity_prefix = f"关于 {entity}，" if entity else ""

    lines: list[str] = [
        "## 融资阶段感知",
        "",
        f"{entity_prefix}当前融资阶段判定为 **{meta['label']}**。",
        f"请按照 {meta['short_label']} 公司的评估框架进行分析。",
        "",
        "### 评估框架调整",
        "",
    ]

    # 客户/收入要求
    if not meta["customer_required"]:
        lines.append("- **客户/收入验证为加分项而非必须项**，不因无客户或无收入而判定为高风险")
        if meta.get("early_customer_feedback_bonus"):
            lines.append("- 如有早期客户试用反馈或用户访谈，应作为正面信号重点呈现")
    elif not meta["revenue_required"]:
        lines.append("- **需要有付费客户或 LOI（意向书）**，但不要求规模化收入")
        lines.append("- 重点关注 PMF 信号：留存率、复购率、客单价趋势")
    else:
        revenue_desc = "规模化收入（千万级以上）" if stage_tier == "T3" else "成熟财务数据（盈利或接近盈利）"
        lines.append(f"- **需要有{revenue_desc}**，财务数据需经审计")

    # 团队/技术优先级
    lines.append(f"- 团队验证优先级：**{meta['team_verification_priority']}**")
    lines.append(f"- 技术差异化优先级：**{meta['tech_differentiation_priority']}**")

    # 估值方法
    forbidden = meta.get("forbidden_valuation_methods", [])
    methods = meta.get("valuation_methods", [])
    if forbidden:
        forbidden_str = "、".join(forbidden)
        methods_str = "、".join(methods)
        lines.append(f"- 估值方法：使用 {methods_str}；**禁用 {forbidden_str}**")
    else:
        methods_str = "、".join(methods)
        lines.append(f"- 估值方法：使用 {methods_str}")

    # 折价上限
    discount = meta.get("valuation_discount", {})
    total_cap = discount.get("total_cap", 0)
    lines.append(f"- 总折价上限：**{total_cap*100:.0f}%**")

    # MOIC
    lines.append(f"- MOIC 预期：{meta.get('moic_expectation', '视具体情况')}")
    lines.append(f"- 典型持有期：{meta.get('typical_hold_period', '视具体情况')}")

    # DD 重点方向
    lines.append("")
    lines.append("### DD 重点方向")
    lines.append("")
    for item in meta.get("dd_focus", []):
        lines.append(f"- {item}")

    # 风险严重度覆盖
    lines.append("")
    lines.append("### 风险严重度调整（覆盖默认评级）")
    lines.append("")
    for risk_key, severity in meta.get("risk_severity_override", {}).items():
        risk_label = _RISK_KEY_LABELS.get(risk_key, risk_key)
        lines.append(f"- {risk_label} → **{severity}**")

    return "\n".join(lines)


_RISK_KEY_LABELS: dict[str, str] = {
    "no_customer_revenue": "无客户/无收入",
    "no_paying_customer": "无付费客户",
    "no_financial_data": "无财务数据",
    "no_audit_report": "无审计报告",
    "team_background_unverified": "团队履历不可验证",
    "tech_not_differentiated": "技术无差异化",
    "key_person_dependency": "关键人依赖",
    "ip_ownership_unclear": "知识产权归属不清",
    "related_party_competition": "关联交易/同业竞争",
    "related_party_transactions": "关联交易",
    "no_pmf_evidence": "无 PMF 证据",
    "revenue_decline": "收入下滑",
    "customer_concentration_high": "客户集中度过高",
    "gross_margin_decline": "毛利率下滑",
    "governance_issues": "治理结构问题",
    "ipo_readiness_gap": "IPO 就绪度不足",
    "historical_compliance_issues": "历史合规问题",
    "earnings_quality_concerns": "盈利质量存疑",
}


# ── 辅助函数 ───────────────────────────────────────────

def read_stage_from_task(task_dir: str | Path, default: str = "T3") -> str:
    """从 task 目录中读取 stage_tier（统一入口）。

    查找顺序：bp_shared_state.json → bp_step0_profile.json → company_verify_report.json。
    全部找不到时返回 default（默认 T3，即不放宽也不收紧）。

    gate 类调用方建议传 default="T4"（保守），handler 类传 default="T3"。
    """
    task_dir = Path(task_dir)

    # 优先：bp_shared_state.json
    shared_path = task_dir / "bp_shared_state.json"
    if shared_path.exists():
        try:
            shared = json.loads(shared_path.read_text(encoding="utf-8"))
            tier = shared.get("stage_tier", "")
            if tier in TIER_ORDER:
                return tier
        except Exception:
            pass

    # 其次：bp_step0_profile.json
    for name in ("bp_step0_profile.json", "bp_profile.json"):
        profile_path = task_dir / name
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                stage_text = profile.get("financing_stage", "")
                tier = classify_stage(stage_text)
                if tier in TIER_ORDER:
                    return tier
            except Exception:
                pass

    # 最后：company_verify_report.json（Phase 05 的产物）
    cv_path = task_dir / "company_verify_report.json"
    if cv_path.exists():
        try:
            cv = json.loads(cv_path.read_text(encoding="utf-8"))
            tier = cv.get("stage_tier", "")
            if tier in TIER_ORDER:
                return tier
            stage_text = cv.get("financing_stage", "")
            tier = classify_stage(stage_text)
            if tier in TIER_ORDER:
                return tier
        except Exception:
            pass

    return default


# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        stage_text = " ".join(sys.argv[1:])
        tier = classify_stage(stage_text)
        meta = get_stage_meta(tier)
        print(f"输入: {stage_text}")
        print(f"分级: {tier} — {meta['label']}")
        print()
        print(build_stage_prompt_block(tier))
    else:
        print("用法: python bp_stage_utils.py <融资阶段文本>")
        print("示例: python bp_stage_utils.py '天使轮'")
