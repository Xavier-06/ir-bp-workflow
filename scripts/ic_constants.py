#!/usr/bin/env python3
"""IC 管线共享常量。

统一管理 step 标签、维度分类、connector 配置、retry 参数等。
避免在 ic_profile.py 和 ic_subagent_launcher.py 中硬编码。
"""
from __future__ import annotations

# ── IC Step → 中文标签 ──
IC_STEP_LABELS: dict[str, str] = {
    "step_ind_overview": "行业概览",
    "step_policy_scan": "政策法规扫描",
    "step_value_chain": "产业链分析",
    "step_executive_hypothesis": "投研假说",
    "step_cross_chain_compare": "跨环节对比",
    "step_catalyst_analysis": "催化剂分析",
    "step_consensus_challenge": "共识挑战",
    "step_investment_thesis": "投资机会",
    "step_risk_assessment": "风险评估",
    "step_scenario_sensitivity": "场景敏感性",
    "step_master_synthesis": "行业研报统稿",
    "step_investment_playbook": "投资手册",
    # Dynamic step prefix labels
    "_competitive": "竞争格局",
    "_tech": "技术趋势",
    "_market": "市场规模",
    "_financial": "财务基准",
    "_valuation": "估值基准",
    "_capital": "资本动向",
    "_seg_synthesis": "环节小结",
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
IC_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {}
# 从 ic_subagent_launcher 中的配置动态加载

# ── Dispatch Retry 参数 ──
IC_COLLECT_RETRY_COUNT: int = 20
IC_COLLECT_RETRY_INTERVAL: int = 30  # seconds, total timeout = 20*30 = 10 min

# ── 质量线 ──
IC_STEP_QUALITY_THRESHOLD: int = 3
IC_REPORT_MIN_LENGTH: int = 2000
IC_REPORT_MIN_SECTIONS: int = 4
IC_REPORT_MIN_CITATIONS: int = 5
