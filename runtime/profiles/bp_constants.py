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

# IMA 知识库 MCP connector ID 列表 —— 投研研报全文提取
# v4.8（2026-07-27）：主力源升级为共享研报库（投行/券商研报全文可 fetch），
# 删除长安投研(7297585010204027) + 公司调研报告(7302533890465245)（仅摘要，库主禁止导出）
# KB IDs: 研报库(7498615127803592) / 行研智库(7311568991699459) /
#         机构调研纪要(7300811407257275) / 精选行业数据报告(7302509206984644)
BP_IMA_CONNECTOR_IDS = ['ima-mcp']

# 全量子代理 connector 集合 —— 统稿 / repair 等非维度角色统一使用（2026-08-03 新增）
# 背景：统稿与 repair manifest 此前写死 BP_TYC_CONNECTOR_IDS（仅 tyc-mcp），
# 导致兜底搜索时拿不到 westock 结构化数据与 IMA 研报库全文。
BP_FULL_CONNECTOR_IDS = BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS

# IMA 知识库 ID 映射 —— v4.8：研报库为主力源（全文可 fetch），3 个订阅库为补充
IMA_KB_IDS = {
    "self_built_research": "7498615127803592",    # ★主力源：共享研报库（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等投行研报，全文可 fetch，按周分文件夹，03_投行报告=大行研报）
    "industry_reports": "7311568991699459",       # 行研智库 3786篇（券商行业深度，全文可 fetch）
    "institutional_notes": "7300811407257275",    # 机构调研纪要 33331篇（NOTE 类型可 fetch）
    "curated_reports": "7302509206984644",        # 精选行业数据报告 1442篇（第三方白皮书，全文可 fetch）
}

# 角色 → IMA 知识库路由 —— v4.8：所有角色第一优先搜研报库，辅以 1-2 个订阅库
IMA_ROLE_KB_MAP = {
    "bp_company_team_compliance":  ["self_built_research", "institutional_notes"],
    "bp_product_commercial":       ["self_built_research", "industry_reports"],
    "bp_tech_ip_moat":             ["self_built_research", "industry_reports"],
    "bp_market_supply_chain":      ["self_built_research", "industry_reports", "curated_reports"],
    "bp_competition_positioning":  ["self_built_research", "institutional_notes"],
    "bp_valuation_return":         ["self_built_research", "institutional_notes"],
    "bp_dealbreaker_risk":         ["self_built_research", "institutional_notes"],
    "bp_consensus_challenge":      ["self_built_research", "institutional_notes"],
    "bp_catalyst":                 ["self_built_research", "institutional_notes"],
    "bp_industry_research":        ["self_built_research", "industry_reports", "curated_reports"],
}

# ── Connector IDs 按角色分配 ──
# 基础四维（团队合规/技术IP/客户验证/风险）只走天眼查；
# 所有 8 维度均开放 westock-mcp（子代理按需调用，不再按角色限制）
# （可比公司的板块/产业链/资金流/北向/机构评级/券商研报 data_report）
BP_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    'bp_company_team_compliance': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_product_commercial': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_tech_ip_moat': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_market_supply_chain': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_competition_positioning': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_valuation_return': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_dealbreaker_risk': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    # v4.5 新增：投资叙事层 3 角色（Wave 4）
    'bp_consensus_challenge': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_catalyst': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
    'bp_industry_research': BP_TYC_CONNECTOR_IDS + BP_WESTOCK_CONNECTOR_IDS + BP_IMA_CONNECTOR_IDS,
}

# Wave 1: 公司团队合规 / 产品商业化 / 技术IP护城河 / 市场供应链（4 维度并行）
BP_WAVE1_ROLE_SLUGS: dict[str, str] = {
    "bp_company_team_compliance": "company_team_compliance",
    "bp_product_commercial": "product_commercial",
    "bp_tech_ip_moat": "tech_ip_moat",
    "bp_market_supply_chain": "market_supply_chain",
}

# Wave 3: 竞争定位 + 估值回报 —— 读 Wave1 输出
BP_WAVE3_ROLE_SLUGS: dict[str, str] = {
    "bp_competition_positioning": "competition_positioning",
    "bp_valuation_return": "valuation_return",
}

# Wave 4: Deal Breaker + 共识挑战 + 催化剂 + 行业研报 —— 读 Wave1/3 全量输出
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

# 全部 role slug 合并映射（Wave1-4 + Legacy）
BP_ALL_ROLE_SLUGS: dict[str, str] = {
    **BP_WAVE1_ROLE_SLUGS,
    **BP_WAVE3_ROLE_SLUGS,
    **BP_WAVE4_ROLE_SLUGS,
}

# Wave 编号到 role 列表的映射 —— wave evidence gate 等处使用
BP_WAVE_ROLES: dict[int, list[str]] = {
    1: list(BP_WAVE1_ROLE_SLUGS.keys()),
    3: list(BP_WAVE3_ROLE_SLUGS.keys()),
    4: list(BP_WAVE4_ROLE_SLUGS.keys()),
}

# 8 维度 slug 列表 —— delivery gate 检查维度输出文件完整性
BP_ALL_SLUGS: list[str] = list(BP_ALL_ROLE_SLUGS.values())

# Collect 统一重试机制参数 —— bp_profile._collect_with_retry 默认值
COLLECT_RETRY_COUNT = 40
COLLECT_RETRY_INTERVAL = 30
