"""Literature Review 管线共享常量模块。

集中存放文献综述管线中多处重复使用的常量，包括:
- 企业数据 MCP connector ID 列表 (企业侦察角色使用，天眼查)
- Wave 角色 slug 映射 (3 Wave 架构)
- 收集重试参数
- Evidence Gate 阈值
- Connector IDs 按角色分配
"""
from __future__ import annotations

# ── 企业数据 MCP Connector IDs（天眼查） ──────────────────────
# enterprise_scout 角色使用
LIT_TYC_CONNECTOR_IDS = ['tyc-mcp']

# 向后兼容别名
LIT_QCC_CONNECTOR_IDS = LIT_TYC_CONNECTOR_IDS

# ── Wave 1: 三路采集 (数据独立, has_more 串行) ───────────────
LIT_WAVE1_ROLE_SLUGS: dict[str, str] = {
    "academic_scout": "academic_scout",
    "industry_scout": "industry_scout",
    "enterprise_scout": "enterprise_scout",
}

# ── Wave 2: 深读 + 分析 (有前后依赖) ────────────────────────
LIT_WAVE2_ROLE_SLUGS: dict[str, str] = {
    "deep_reader": "deep_reader",
    "tech_strategist": "tech_strategist",
}

# ── Wave 3: 报告产出 ────────────────────────────────────────
LIT_WAVE3_ROLE_SLUGS: dict[str, str] = {
    "report_writer": "report_writer",
}

# 全部角色 slug 合并映射
LIT_ALL_ROLE_SLUGS: dict[str, str] = {
    **LIT_WAVE1_ROLE_SLUGS,
    **LIT_WAVE2_ROLE_SLUGS,
    **LIT_WAVE3_ROLE_SLUGS,
}

# Wave 编号到 role 列表的映射
LIT_WAVE_ROLES: dict[int, list[str]] = {
    1: list(LIT_WAVE1_ROLE_SLUGS.keys()),
    2: list(LIT_WAVE2_ROLE_SLUGS.keys()),
    3: list(LIT_WAVE3_ROLE_SLUGS.keys()),
}

# 全部 slug 列表
LIT_ALL_SLUGS: list[str] = list(LIT_ALL_ROLE_SLUGS.values())

# ── Connector IDs 按角色分配 ────────────────────────────────
LIT_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    "tech_decomposition": ['westock-mcp'],       # 板块/产业链/机构评级/资金流（技术背景补充）
    "academic_scout": ['westock-mcp'],           # 板块/产业链/机构评级（行业背景补充）
    "industry_scout": ['westock-mcp'],           # 板块/产业链/机构评级/券商研报（westock-mcp），补充 NeoData 行业研报
    "enterprise_scout": LIT_QCC_CONNECTOR_IDS + ['westock-mcp'],  # 天眼查 MCP + westock-mcp（板块/产业链/机构评级/资金流）
    "deep_reader": ['westock-mcp'],              # 板块/产业链（按需查标的背景）
    "tech_strategist": ['westock-mcp'],          # 板块/产业链/机构评级/资金流（补充技术路线行业背景）
    "report_writer": ['westock-mcp'],            # 板块/产业链/机构评级（按需引用）
}

# ── Collect 重试参数 ─────────────────────────────────────────
COLLECT_RETRY_COUNT = 40
COLLECT_RETRY_INTERVAL = 30

# ── Collect Sidecar 落盘重试 (轻量) ─────────────────────────
# 子代理先写 .md 再写 sidecar (JSON 序列化耗时)，直接判定 incomplete 会触发代价高昂的重 dispatch。
# 参考 BP 的 _collect_with_retry: 5 次 × 15s = 最多额外等待 75s。
SIDECAR_RETRY_COUNT = 5
SIDECAR_RETRY_INTERVAL = 15

# ── Gate Repair 参数 ─────────────────────────────────────────
# Evidence Gate FAIL 后允许 repair 子代理补充采集的最大次数。
# 超过后降级放行 (WARN disclosure)，不阻断管线。
GATE_REPAIR_MAX_ATTEMPTS = 2

# ── Evidence Gate 阈值 ──────────────────────────────────────
WAVE1_GATE_THRESHOLDS = {
    # academic_scout
    "per_subtopic_paper_count": 15,
    "total_paper_count": 50,
    "source_diversity": 4,       # 至少 4 个学术 API 有结果
    "institution_coverage": 5,   # 至少 5 个不同机构
    "oa_url_rate": 0.40,         # 40% 论文有 OA URL
    # industry_scout
    "broker_report_count": 3,
    "industry_report_count": 3,
    "news_count": 5,
    # enterprise_scout
    "company_profiles": 3,
}

WAVE2_GATE_THRESHOLDS = {
    # deep_reader
    "reading_coverage_per_subtopic": 5,
    "full_text_rate": 0.40,
    "metric_extraction_per_subtopic": 3,
    # tech_strategist
    "route_comparison_min_routes": 2,
}

WAVE3_GATE_THRESHOLDS = {
    "citation_density_per_2k": 3,
    "report_sections": 8,
}

# ── deep_reader per-sub_topic 并行 ─────────────────────────────
# v2: 每个 sub_topic 独立派发一个 deep_reader agent，读全部论文
DEEP_READER_BATCH_SIZE = 0          # 0 = 不限制，读全部
DEEP_READER_NOTE_TARGET_CHARS = 800 # 每篇压缩笔记目标字数

# ── 文件后缀约定 ────────────────────────────────────────────
FACTS_SUFFIX = "-facts.json"
SECTION_SUFFIX = "-section.json"
NOTES_SUFFIX = "_reading_notes.json"
