"""IC Topic 课题研究管线共享常量模块。

集中存放 IC 课题研究管线中多处重复使用的常量，包括:
- 企业数据 MCP connector ID 列表（天眼查）
- 结构化金融数据 MCP connector ID 列表（腾讯自选股）
- Wave 角色 slug 映射（3 Wave / 6 角色架构）
- 按角色分配的 Connector IDs
- 收集重试参数
- Evidence Gate 阈值
"""
from __future__ import annotations

# ── 企业数据 MCP Connector IDs（天眼查） ──────────────────────
# tyc-mcp: 天眼查聚合网关（工商/股东/司法/专利/知产）
IC_TOPIC_TYC_CONNECTOR_IDS = ['tyc-mcp']

# ── 结构化金融数据 MCP Connector IDs（腾讯自选股） ────────────
# westock-mcp: A/HK/美股行情、财务、板块、产业链、机构评级、券商研报
IC_TOPIC_WESTOCK_CONNECTOR_IDS = ['westock-mcp']

# ⚠️ tdx-connector(通达信) / qcc-company(企查查) 当前环境不可用，已剔除

# ── 所有 IC 子代理统一授权双源 ────────────────────────────
IC_TOPIC_CONNECTOR_IDS = IC_TOPIC_TYC_CONNECTOR_IDS + IC_TOPIC_WESTOCK_CONNECTOR_IDS
# = ['tyc-mcp', 'westock-mcp']

# ── Wave 1: 基础扫描（3 角色，数据独立，sequential has_more） ─
IC_TOPIC_WAVE1_ROLE_SLUGS: dict[str, str] = {
    "ic_market_overview": "ic_market_overview",
    "ic_competitive_landscape": "ic_competitive_landscape",
    "ic_tech_product": "ic_tech_product",
}

# ── Wave 2: 深度分析（2 角色，依赖 W1 输出，sequential has_more） ─
IC_TOPIC_WAVE2_ROLE_SLUGS: dict[str, str] = {
    "ic_supply_chain": "ic_supply_chain",
    "ic_policy_risk": "ic_policy_risk",
}

# ── Wave 3: 统稿（1 角色，依赖 W1+W2 全部输出） ─────────────
IC_TOPIC_WAVE3_ROLE_SLUGS: dict[str, str] = {
    "ic_report_synthesizer": "ic_report_synthesizer",
}

# 全部角色 slug 合并映射
IC_TOPIC_ALL_ROLE_SLUGS: dict[str, str] = {
    **IC_TOPIC_WAVE1_ROLE_SLUGS,
    **IC_TOPIC_WAVE2_ROLE_SLUGS,
    **IC_TOPIC_WAVE3_ROLE_SLUGS,
}

# Wave 编号到 role 列表的映射
IC_TOPIC_WAVE_ROLES: dict[int, list[str]] = {
    1: list(IC_TOPIC_WAVE1_ROLE_SLUGS.keys()),
    2: list(IC_TOPIC_WAVE2_ROLE_SLUGS.keys()),
    3: list(IC_TOPIC_WAVE3_ROLE_SLUGS.keys()),
}

# 全部 slug 列表
IC_TOPIC_ALL_SLUGS: list[str] = list(IC_TOPIC_ALL_ROLE_SLUGS.values())

# ── Connector IDs 按角色分配 ────────────────────────────────
# 当前所有角色权限相同，预留差异化配置空间
IC_TOPIC_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    "ic_market_overview": IC_TOPIC_CONNECTOR_IDS,
    "ic_competitive_landscape": IC_TOPIC_CONNECTOR_IDS,
    "ic_tech_product": IC_TOPIC_CONNECTOR_IDS,
    "ic_supply_chain": IC_TOPIC_CONNECTOR_IDS,
    "ic_policy_risk": IC_TOPIC_CONNECTOR_IDS,
    "ic_report_synthesizer": IC_TOPIC_CONNECTOR_IDS,
}

# ── Collect 重试参数 ─────────────────────────────────────────
# 课题粒度小于 BP 尽调，retry 参数适当缩减
COLLECT_RETRY_COUNT = 20
COLLECT_RETRY_INTERVAL = 30

# ── Collect Sidecar 落盘重试（轻量） ─────────────────────────
SIDECAR_RETRY_COUNT = 5
SIDECAR_RETRY_INTERVAL = 15

# ── Gate Repair 参数 ─────────────────────────────────────────
# Evidence Gate FAIL 后允许 repair 子代理补充采集的最大次数
GATE_REPAIR_MAX_ATTEMPTS = 1

# ── Synthesis Repair 参数 ────────────────────────────────────
SYNTHESIS_REPAIR_MAX_ATTEMPTS = 1

# ── Evidence Gate 阈值 ──────────────────────────────────────
WAVE1_GATE_THRESHOLDS = {
    "min_chars": 2000,
    "min_sources": 4,
    "min_sections": 3,
}

WAVE2_GATE_THRESHOLDS = {
    "min_chars": 2000,
    "min_sources": 4,
    "min_sections": 3,
}

WAVE3_GATE_THRESHOLDS = {
    "min_chars": 5000,
    "min_sources": 8,
    "min_sections": 6,
    "citation_density_per_2k": 3,
}

# ── 文件后缀约定 ────────────────────────────────────────────
FACTS_SUFFIX = "-facts.json"
SECTION_SUFFIX = "-section.json"
