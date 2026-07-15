"""BP 管线共享常量模块。

集中存放 BP 管线中多处重复使用的常量，包括企业数据 MCP connector ID 列表、
Wave 角色 slug 映射、Wave 角色分组、8 维度 slug 列表，以及收集重试参数。
其他模块应优先引用此处常量，避免硬编码重复。
"""
from __future__ import annotations

# 企业数据 MCP connector ID 列表 —— 天眼查（工商/股东/司法/专利/知产）
BP_TYC_CONNECTOR_IDS = ['tyc-mcp']

# 结构化金融数据源 MCP connector ID 列表 —— 腾讯自选股（westock-mcp）
# 面向上市公司的结构化金融数据：板块/产业链/资金流/北向/机构评级/券商研报（data_report）
BP_WESTOCK_CONNECTOR_IDS = ['westock-mcp']

# ── Connector IDs 按角色分配 ──
# 基础四维（团队合规/技术IP/客户验证/风险）只走天眼查；
# 所有 8 维度均开放 westock-mcp（子代理按需调用，不再按角色限制）
# （可比公司的板块/产业链/资金流/北向/机构评级/券商研报 data_report）
BP_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    'bp_company_team_compliance': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_product_commercial': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_tech_ip_moat': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_market_supply_chain': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_competition_positioning': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_valuation_return': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_customer_revenue_validation': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_dealbreaker_risk': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    # v4.5 新增：投资叙事层 4 角色
    'bp_investment_hypothesis': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_consensus_challenge': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_catalyst': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
    'bp_industry_research': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS,
}

# Wave 0: 投资假说框架 —— 先行者，在所有维度子代理之前跑
BP_WAVE0_ROLE_SLUGS: dict[str, str] = {
    "bp_investment_hypothesis": "investment_hypothesis",
}

# Wave 1: 公司团队合规 / 产品商业化 / 技术IP护城河 / 市场供应链（4 维度并行）
BP_WAVE1_ROLE_SLUGS: dict[str, str] = {
    "bp_company_team_compliance": "company_team_compliance",
    "bp_product_commercial": "product_commercial",
    "bp_tech_ip_moat": "tech_ip_moat",
    "bp_market_supply_chain": "market_supply_chain",
}

# Wave 2: 客户收入验证 —— 读 Wave1 产品/技术信息交叉验证
BP_WAVE2_ROLE_SLUGS: dict[str, str] = {
    "bp_customer_revenue_validation": "customer_revenue_validation",
}

# Wave 3: 竞争定位 + 估值回报 —— 读 Wave1+Wave2 输出
BP_WAVE3_ROLE_SLUGS: dict[str, str] = {
    "bp_competition_positioning": "competition_positioning",
    "bp_valuation_return": "valuation_return",
}

# Wave 4: Deal Breaker + 共识挑战 + 催化剂 + 行业研报 —— 读 Wave0/1/2/3 全量输出
BP_WAVE4_ROLE_SLUGS: dict[str, str] = {
    "bp_dealbreaker_risk": "dealbreaker_risk",
    "bp_consensus_challenge": "consensus_challenge",
    "bp_catalyst": "catalyst",
    "bp_industry_research": "industry_research",
}

# 旧版中文 slug 映射 —— 兼容历史 job 数据
BP_LEGACY_ROLE_SLUGS: dict[str, str] = {
    "bp_团队与合规": "team",
    "bp_技术与产品": "tech",
    "bp_行业与供应链": "industry",
    "bp_估值": "valuation",
    "bp_竞争与结论": "competition",
}

# 全部 role slug 合并映射（Wave0-4 + Legacy）
BP_ALL_ROLE_SLUGS: dict[str, str] = {
    **BP_WAVE0_ROLE_SLUGS,
    **BP_WAVE1_ROLE_SLUGS,
    **BP_WAVE2_ROLE_SLUGS,
    **BP_WAVE3_ROLE_SLUGS,
    **BP_WAVE4_ROLE_SLUGS,
}

# Wave 编号到 role 列表的映射 —— wave evidence gate 等处使用
BP_WAVE_ROLES: dict[int, list[str]] = {
    0: list(BP_WAVE0_ROLE_SLUGS.keys()),
    1: list(BP_WAVE1_ROLE_SLUGS.keys()),
    2: list(BP_WAVE2_ROLE_SLUGS.keys()),
    3: list(BP_WAVE3_ROLE_SLUGS.keys()),
    4: list(BP_WAVE4_ROLE_SLUGS.keys()),
}

# 8 维度 slug 列表 —— delivery gate 检查维度输出文件完整性
BP_ALL_SLUGS: list[str] = list(BP_ALL_ROLE_SLUGS.values())

# Collect 统一重试机制参数 —— bp_profile._collect_with_retry 默认值
COLLECT_RETRY_COUNT = 40
COLLECT_RETRY_INTERVAL = 30
