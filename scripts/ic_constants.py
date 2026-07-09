#!/usr/bin/env python3
"""IC 管线共享常量。

统一管理 step 标签、维度分类、connector 配置、retry 参数、archetype 定义等。
避免在 ic_profile.py 和 ic_subagent_launcher.py 中硬编码。
"""
from __future__ import annotations

# ── Archetype 常量 ──
IC_ARCHETYPE_CHAIN_SCAN = "chain_scan"
IC_ARCHETYPE_TECH_COMPARE = "tech_compare"
IC_ARCHETYPE_COMPANY_DEEP = "company_deep"
IC_ARCHETYPE_EARLY_THEME = "early_theme"
IC_ARCHETYPE_COMMERCIAL_MODE = "commercial_mode"

IC_ALL_ARCHETYPES = [
    IC_ARCHETYPE_CHAIN_SCAN, IC_ARCHETYPE_TECH_COMPARE, IC_ARCHETYPE_COMPANY_DEEP,
    IC_ARCHETYPE_EARLY_THEME, IC_ARCHETYPE_COMMERCIAL_MODE,
]

IC_DEFAULT_ARCHETYPE = IC_ARCHETYPE_CHAIN_SCAN

# ── Archetype → 中文标签 ──
IC_ARCHETYPE_LABELS: dict[str, str] = {
    "chain_scan": "产业链扫描",
    "tech_compare": "技术路线比较",
    "company_deep": "公司深度",
    "early_theme": "早期主题",
    "commercial_mode": "商业模式研究",
}

# ── IC Step → 中文标签（v2: 新增 archetype 专用 step）──
IC_STEP_LABELS: dict[str, str] = {
    "step_ind_overview": "行业概览",
    "step_policy_scan": "政策法规扫描",
    "step_value_chain": "产业链分析",
    "step_executive_hypothesis": "投研假说",
    "step_cross_chain_compare": "跨环节对比",
    "step_cross_compare": "跨环节/路线对比",
    "step_catalyst": "催化剂分析",
    "step_catalyst_analysis": "催化剂分析",
    "step_consensus": "共识挑战",
    "step_consensus_challenge": "共识挑战",
    "step_master_synthesis": "行业研报统稿",
    # v2 archetype-specific steps
    "step_tech_landscape": "技术全景扫描",
    "step_business_overview": "业务概览",
    "step_competitive_position": "竞争定位",
    "step_financial_deep": "财务深度",
    "step_valuation_benchmark": "估值基准",
    "step_moat_analysis": "护城河分析",
    "step_risk_assessment": "风险评估",
    "step_feasibility": "可行性评估",
    "step_timeline": "里程碑时间表",
    "step_market_overview": "市场概览",
    "step_competitive_landscape": "竞争格局",
    "step_unit_economics": "单元经济",
    "step_customer_analysis": "客户分析",
    "step_pricing_model": "定价模型",
    "step_financial_projection": "财务预测",
    "step_tech_overview": "技术概览",
    "step_key_players": "关键玩家",
    "step_supply_sketch": "供应链速写",
    # v1 dynamic step prefix labels (legacy, kept for backward compat)
    "_competitive": "竞争格局",
    "_tech": "技术趋势",
    "_market": "市场规模",
    "_financial": "财务基准",
    "_valuation": "估值基准",
    "_capital": "资本动向",
    "_seg_synthesis": "环节小结",
    # v2 dynamic step prefix labels
    "_segment_deep": "环节深度分析",
    "_route_deep": "路线深度分析",
}

# ── Step 维度分类 ──
# 用于跨维度一致性检查
IC_STEP_CLASSIFICATIONS: dict[str, str] = {
    "overview": "industry",
    "policy": "policy",
    "value_chain": "chain",
    "competitive": "competitive",
    "tech": "tech",
    "market": "market",
    "financial": "finance",
    "valuation": "valuation",
    "capital": "capital",
    "cross_chain": "cross",
    "catalyst": "catalyst",
    "consensus": "consensus",
    "investment": "investment",
    "risk": "risk",
    "scenario": "scenario",
    "synthesis": "synthesis",
    "playbook": "playbook",
}

# ── 参与投资判断的核心 step ──
# 排除 synthesis/playbook（汇总性质）和 executive_hypothesis（先行）
IC_JUDGE_STEPS: tuple[str, ...] = (
    "step_ind_overview", "step_policy_scan", "step_value_chain",
    "step_cross_chain_compare", "step_catalyst_analysis",
    "step_consensus_challenge", "step_investment_thesis",
    "step_risk_assessment", "step_scenario_sensitivity",
)

# ── Connector 配置 ──
# 按 step 前缀授予 connectorIds。tyc-mcp（天眼查）+ westock-mcp（腾讯自选股）。
# tdx（通达信）/ qcc（企查查）当前环境不可用。
# ★ 单一真实来源：在此 dict 维护，ic_subagent_launcher.py 从本文件导入。
IC_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    "step_ind_overview": ["tyc-mcp", "westock-mcp"],
    "step_policy_scan": ["tyc-mcp", "westock-mcp"],
    "step_value_chain": ["tyc-mcp", "westock-mcp"],
    "step_competitive": ["tyc-mcp", "westock-mcp"],
    "step_tech": ["tyc-mcp", "westock-mcp"],
    "step_market": ["tyc-mcp", "westock-mcp"],
    "step_financial": ["westock-mcp"],
    "step_valuation": ["westock-mcp"],
    "step_capital": ["tyc-mcp", "westock-mcp"],
    "step_executive_hypothesis": ["tyc-mcp", "westock-mcp"],
    "step_cross_chain_compare": ["tyc-mcp", "westock-mcp"],
    "step_catalyst_analysis": ["tyc-mcp", "westock-mcp"],
    "step_consensus_challenge": ["tyc-mcp", "westock-mcp"],
    "step_investment_thesis": ["tyc-mcp", "westock-mcp"],
    "step_risk_assessment": ["tyc-mcp", "westock-mcp"],
    "step_scenario_sensitivity": ["tyc-mcp", "westock-mcp"],
    "step_master_synthesis": ["tyc-mcp", "westock-mcp"],
    "step_investment_playbook": ["tyc-mcp", "westock-mcp"],
}

IC_DEFAULT_CONNECTOR_IDS: list[str] = ["tyc-mcp", "westock-mcp"]

# ── Dispatch Retry 参数 ──
IC_COLLECT_RETRY_COUNT: int = 20
IC_COLLECT_RETRY_INTERVAL: int = 30  # seconds, total timeout = 20*30 = 10 min

# ── 质量线 ──
IC_STEP_QUALITY_THRESHOLD: int = 3
IC_REPORT_MIN_LENGTH: int = 2000
IC_REPORT_MIN_SECTIONS: int = 4
IC_REPORT_MIN_CITATIONS: int = 5
