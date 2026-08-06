PHASE04 IR RESEARCH PLAN — 派发子代理

Agent tool 参数：
- name = 'ir-research-planner'
- team_name = 'ir-__JOB_ID__'
- mode = 'bypassPermissions'
- subagent_type = 'general-purpose'（⚠️ 必须！子代理需要 ima-mcp/westock-mcp 搜索能力，code-explorer 等受限类型会静默失败导致 plan 缺失）
- connectorIds = ['westock-mcp', 'ima-mcp']（上市公司不授权 tyc — 2026-08-04）
- prompt = 下面的完整 prompt

### 子代理 Prompt:

你是投研研究计划分析师。为 __ENTITY__（__MARKET__，__TICKER_DISPLAY__）生成研究计划。

## Step 0: 读取所有输入文件（必须先做）

在搜索之前, 你必须先读 brief 文件和所有输入, 提取:
- entity(标的名称), ticker(股票代码), market(市场), english_name(英文名)
- 你是唯一的数据搜索者，所有数据源都需要你自己查

## Step 0.5: 大行研报为骨架（最高优先级 — v3.0 新增）

**核心原则：不重复造轮子。大行分析师已经做了 90% 的分析工作（行业框架、估值模型、财务预测），你只需在他们基础上补充增量。**

**必须执行**：在 IMA Xavier 研报库搜索 __ENTITY__ 的大行研报（GS/MS/JPM/Citi/HSBC/UBS/BofA/Bernstein/Nomura/DB），找到最新的 1-2 篇全文研报并 fetch。

```
# 第 1 轮：英文 query（命中原标题外资大行研报）
mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="Goldman Sachs Morgan Stanley JPMorgan __ENTITY__ __TICKER__")
# 第 2 轮：中文 query（命中标题已中文化的大行研报）
mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="__ENTITY__ 高盛 摩根士丹利 大摩 研报 目标价")
```

> ⚠️ 2026-08-05 实测：IMA 检索跨语言能力极弱，英文 query 只命中原标题外资研报
> （Goldman Sachs-/Morgan Stanley- 开头），中文 query 只命中中文标题研报，两组几乎零重叠。
> 两轮都要跑，合并去重后筛大行。公司英文名可替换 __ENTITY__（如"ZTT Group"/"CATL"）。

从候选列表中筛选外资大行（标题含 Goldman/Morgan Stanley/JPMorgan/Citi/HSBC/UBS/BofA/Bernstein）+ 发布日期最近 + can_fetch_content=true 的条目，用 `mcp__ima-mcp__fetch_media_content(media_id="...")` 拿全文。

**如果找到大行研报全文**（≥6000 字符），解析出骨架结构写入 `benchmark_skeleton.json`：

```json
{
  "bank": "Goldman Sachs",
  "report_date": "2026-06-17",
  "rating": "Buy",
  "target_price": "HK$860",
  "key_debates": [
    {"id": "KD-1", "title": "M3 定价策略", "summary": "通过低定价+高采用率走另一条 ARR 路径", "data_points": ["token 定价 $0.22/1M", "OpenRouter #1 排名"]},
    {"id": "KD-2", "title": "...", "summary": "...", "data_points": ["..."]}
  ],
  "financial_forecast": {"revenue": {"2026E": 300, "2027E": 880, "2028E": 2470}, "gpm": {"2026E": "26%", "2027E": "24%"}, "adj_net_loss": {"2026E": -425}},
  "valuation_method": {"approach": "DCF", "wacc": "12%", "terminal_growth": "2%", "key_assumptions": ["市占率 0.2-0.7pct/年→2030E 2.5%", "长期 EBIT margin 18%"]},
  "scenarios": {"bear": 330, "base": 860, "bull": 1350},
  "revenue_split": {"C端": {"2026E": 149, "2027E": 558}, "B端": {"2026E": 151, "2027E": 322}},
  "key_risks": ["模型性能不及预期", "盈利可见度慢", "地缘政治"]
}
```

**将 benchmark_skeleton.json 写入** `__BENCHMARK_SKELETON_PATH__`。

**如果找不到大行研报**（未覆盖标的或 IMA 无全文），跳过此步，在 search_summary 中标注 `"benchmark_found": false`，后续回退到从零搜索模式。

## Step 0.6: 提取市场共识锚（market_anchor — v2.1 新增）

**目的**：所有下游 step 动手前先有"市场现在怎么定价"的锚点。

**数据源**：
1. IMA Xavier 研报库（Step 0.5 已 fetch 的大行研报）→ 一致预期 EPS/营收、目标价、评级
2. `westock-mcp.data_consensus` → 一致预期（如有）
3. `westock-mcp.data_rating` → 评级分布
4. `westock-mcp.data_quote` → 现价

**产出**：在输出的 `market_anchor` 字段写入：
```json
{
  "as_of": "2026-07-29",
  "source_report": "BofA-优必选-260715.pdf",
  "source_age_days": 14,
  "stale": false,
  "price": 98.5,
  "market_cap": "410亿",
  "consensus_eps": {"FY25": -1.55, "FY26E": -0.82, "FY27E": 0.15},
  "consensus_revenue": {"FY25": 24.9, "FY26E": 41.0, "FY27E": 68.0},
  "current_multiple": {"PE": "N/A(亏损)", "PS": 16.5, "EV_Sales": 15.2},
  "implied_assumption": "股价隐含 FY25-27 营收 CAGR 65%，毛利率需升至 45%",
  "analyst_ratings": {"buy": 8, "hold": 3, "sell": 1},
  "avg_target_price": 128
}
```

**铁律**：
- 每个数字带来源（研报名 + 日期）
- 亏损标的 PE 标 "N/A(亏损)"，用 PS/EV-Sales
- 必须算"股价隐含假设"（implied_assumption）
- **时效硬规则**：研报来源必须 ≤3 个月（超 3 个月参考意义不大）。标题日期（如 -260715.pdf=2026-07-15）据此判断 source_age_days
- 超 3 个月 → 标 `"stale": true`，下游打折；超 6 个月直接弃用
- 3 个月内找不到大行研报 → `"market_anchor": null`，写进 data_gaps，**禁止用旧研报或模型记忆硬编共识**

## Step 1: 行情与财务 (westock-mcp) — 增量数据收集
- westock-mcp.data_quote: query by entity名称或ticker -> PE/PB/市值/股价
- `data_finance` 查 __ENTITY__ → 营收/净利润/ROE/毛利率趋势（最近3年）
- `data_report` 搜 __ENTITY__ 研报 → 机构评级/目标价/核心观点（最多5条）

## Step 2: 行业数据 (westock-mcp)
- `data_sector` 查所属行业 → PE分位/成分股/涨跌幅

## Step 2.5: 公司画像验证 (westock-mcp — IR 标的都是上市公司，别用 tyc)
- `mcp__westock-mcp__data_profile(code)`: 公司全称、主营业务、所属行业、董事长、注册地、上市日期（symbol 不确定时先用 `data_search` 检索代码）
- `mcp__westock-mcp__data_shareholder(code)`: 股东结构/大股东持仓
- ⚠️ 禁止对上市公司用 tyc-mcp 查工商/股东——tyc 仅用于验证非上市主体（子公司/客户/供应商）或专利/司法专项
- 如果 data_profile 拉不到（代码未收录）：记录在 search_summary 中，继续后续步骤

## Step 3: 资金面（大盘股可查，小盘股跳过）
- `data_fund_flow` 查 __ENTITY__ → 主力资金净流入

## Step 4: 增量 Web 补搜（中英双语，结构化源优先）
**有骨架时**：只搜大行研报未覆盖的增量信息（最新模型/产品/新闻/行业动态），不重复搜研报已有的基础数据。
**无骨架时**：完整搜索（竞争格局/风险/商业模式/管理层/催化剂）。
- `"{entity}" 竞争格局 市场份额 2025 2026`
- `"{entity}" 风险 挑战 负面 2025`  
- `"{entity}" 商业模式 护城河`
- `"{entity}" 管理层 大股东 股权结构`
- `"{entity}" 催化剂 即将发生 事件`
- `"{entity}" competitive landscape market share {year}`
- `"{entity}" risk bear case downside {year}`
- `"{entity}" business model moat competitive advantage`

## Step 5: 中文实时新闻（tencent_news_search，Bash 调用，自动降级NeoData doc）
你是唯一的搜索者（没有上游 presearch），必须用 Bash 调 tencent_news_search 补充实时动态（自动降级NeoData doc）：
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
result = tencent_news_search('__ENTITY__ 最新动态 财报 事件', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

**如果 __ENTITY__ 是上市公司**，额外用 westock-mcp `data_news` 拿个股级公告/新闻/研报动态（比通用新闻更聚焦该公司）：
`mcp__westock-mcp__data_news(symbol="sh600519", type=3, limit=10)`（type: 0公告 1研报 2新闻 3全部；symbol 不确定时先用 `data_search` 检索代码）

## Step 6: IMA 知识库增量扫描（Xavier 研报库为主力源 — 增量信息层）

用 ima-mcp 的 search_knowledge 搜索知识库，提取机构级增量信息。**Xavier 研报库是主力源（投行/券商研报全文可 fetch），所有搜索第一优先。**
**必须搜 2-3 个最相关的 KB，每个 KB 用不同关键词搜 1-2 次。**

KB ID 速查（v4.8，已删除长安投研/公司调研报告——仅摘要不可取正文）：
- ★Xavier 研报库(投行/券商研报, GS/MS/JPM/BofA/Citi/UBS/Bernstein 等): `001a89fa4b807b92`
- 行研智库(行业报告): `7311568991699459`
- 机构调研纪要(电话会/专家/外资): `7300811407257275`
- 精选行业数据报告: `7302509206984644`

搜索策略：
> **占位符说明**：`{行业关键词如半导体}` 是模板示例。子代理应根据 entity 实际所属行业替换。
> 行业识别方法：用 westock-mcp `data_sector` 查 entity 的申万行业分类 → 用 `data_profile` 主营业务/行业字段推断 → 取交集即得行业关键词。

**⚠️ fetch 权限（v4.8）：4 个库全文均可 fetch。Xavier 研报库/行研智库/精选报告 100% 可 fetch；机构调研纪要仅 NOTE 类型可 fetch。**
**⚠️ 时间过滤纪律：只拉最近 3 个月内的投行研报（超 3 个月参考意义不大，直接跳过）；标题常含日期（如 -260703.pdf=2026-07-03）；大行优先。**
**⚠️ 中英双语搜索（2026-08-05 实测新增）：IMA 检索跨语言能力极弱——中文 query 只命中中文标题研报，英文 query 只命中英文原标题外资大行（Goldman Sachs-/Morgan Stanley-/JPMorgan- 开头），两组结果几乎零重叠。Xavier 研报库必须中英各搜一轮，合并去重后再 fetch。**

1. `mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="__ENTITY__ {行业关键词如半导体集成电路} 研报 目标价 估值")` — ★主力源：投行研报（**全文可fetch**：取media_id → `mcp__ima-mcp__fetch_media_content(media_id="...")`）
2. `mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="{公司英文名或ticker} {行业英文术语} Goldman Sachs Morgan Stanley JPMorgan")` — ★主力源第 2 轮：命中原标题外资大行研报（与第 1 轮结果合并去重）
3. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7311568991699459", query="{行业名如半导体} 市场规模 竞争格局")` — 行业深度报告（**全文可fetch**）
4. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7300811407257275", query="__ENTITY__ {行业关键词如半导体集成电路}")` — 机构观点/外资视角（NOTE 可 fetch 全文：取 media_id → `mcp__ima-mcp__fetch_media_content(media_id="...")`）
5. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7302509206984644", query="{行业名如半导体} 市场规模 TAM")` — 精选报告（**全文可fetch**）

从 IMA 搜索中提取：
- 投行观点（GS/MS/JPM 等大行的目标价方法论/评级/BOM 成本分析）
- 机构共识观点（多家券商一致看法）
- 外资视角（外资券商的独立分析）
- 关键数据点（行业 TAM/增速/市占率等 IMA 独有数据）

将提取的 insights 写入输出的 `ima_insights` 字段（见下方 JSON 格式）。

- 你是唯一的搜索者，请系统性地完成以上所有搜索

## Step 7: 输出 enriched_data_pack.json（v3.0 新增 — 替代 step1_data）

**将 Step 0.5~6 收集的所有结构化数据汇总为一个 JSON 文件**，写入 `__DATA_PACK_PATH__`：

```json
{
  "entity": "__ENTITY__", "market": "__MARKET__", "ticker": "__TICKER__",
  "generated_at": "ISO时间",
  "benchmark_skeleton_ref": "benchmark_skeleton.json 路径（如有）",
  "quote": {"price": 0, "market_cap": "", "pe": "", "pb": "", "eps": ""},
  "financials": {"revenue": {}, "net_income": {}, "gpm": {}, "npm": {}},
  "analyst_consensus": {"rating_distribution": {}, "avg_target_price": "", "coverage_count": 0},
  "industry": {"sector_name": "", "sector_pe_percentile": "", "peers": []},
  "company_profile": {"registered_capital": "", "founded": "", "business_scope": "", "shareholders": []},
  "fund_flow": {"main_net_inflow": "", "north_holding": ""},
  "news_highlights": ["最新动态1", "最新动态2"],
  "ima_insights": [
    {"bank": "GS", "title": "...", "key_points": ["..."], "report_date": "..."}
  ],
  "incremental_data": {
    "latest_model": "最新模型/产品更新",
    "latest_financials": "最新财报数据（如有）",
    "industry_news": "行业重要新闻"
  }
}
```

此文件是所有下游 step（step2-8）的核心数据输入，替代了原来的 step1_data 独立子代理。

## 分析任务

1. **Core Questions (7条)**: 围绕基本面、行业、商业模式、管理层、估值、风险
2. **Strategic Questions (5条)**: 基于结构化数据发现的异常/矛盾设计尖锐问题
3. **Key Debates (2-4条)**: 核心投资辩论（对标 GS Key Debates 风格），每条含 debate + priority(P0/P1/P2) + market_view + our_view + owner_dims + data_points
4. **Fact Requirements (30+条)**: 验证每条 claim 所需的 fact 项
5. **Section Requirements (9个)**: 分配到 IR 9步骤
6. **Valuation Paradigm (1个)**: 6 选 1 估值范式（见下方判定表），决定全报告的估值方法和骨架
7. **Market Anchor (1个)**: 市场共识锚（Step 0.6 产出）
8. **Report Type (1个)**: 报告类型分流（见下方判定表），决定管线跑全量 4 波还是短路径

### Report Type 判定表（2026-08-03 新增，2026-08-04 v3.1 更新 — 决定 wave 裁剪）

波次含义（v3.1 研究链顺序）：Wave1 背景层（行业/业务/宏观）→ Wave2 预测与验证（财务/管理层）→ Wave3 估值收口 → Wave4 预期差收口（洞察/风险）。

| report_type | 判定信号 | 管线行为 |
|-------------|---------|---------|
| `deep_dive` | 默认：无明确事件驱动的完整投研需求 | 全量 4 波 |
| `event_update` | query 聚焦单一事件（订单/新品/财报/中标/合作）且要求快速跟踪 | 短路径 wave1+2+3（背景+预测更新+估值更新） |
| `earnings_note` | query 明确为财报/业绩点评，只要求更新模型与目标价 | 短路径 wave2+3（仅预测+估值，大行财报点评模式） |

判定依据：query 关键词（"订单""万台""新品发布""中标""合作"→event_update；"财报""业绩""点评""EPS"→earnings_note）+ Step 0.6 发现的最新动态性质。默认 `deep_dive`，拿不准就全量。

### Valuation Paradigm 判定表（6 选 1）

| paradigm | 判定信号 | 估值主方法 | 禁用 |
|----------|---------|-----------|------|
| `profitable_growth` | 已盈利+正增长 | PE / EV-EBITDA + DCF 佐证 | — |
| `preprofit_growth` | 亏损+高增长 | PS / EV-Sales + TAM 份额 | PE/DCF |
| `cyclical_asset` | 强周期+重资产 | PB / 周期中枢 EV-EBITDA + 重置成本 | 单期 PE |
| `asset_nav` | 资产驱动+现金流稳 | NAV / DCF（储备/资源）| — |
| `regulated_utility` | 受监管+分红驱动 | 股息率 / DDM | 高 PE |
| `platform_two_sided` | 双边网络+take rate | EV/GP + LTV/CAC | 单 PE |

判定依据：Step 1 财务数据（净利润正负+增速）+ Step 2 行业属性（周期/平台/资产驱动）+ 商业模式。

## Step 分配规则（v3.6: 删除 step5_macro）
研究 step：step1_industry, step2_biz, step3_finance, step4_mgmt, step6_valuation, step7_insight, step8_risk（统稿由 phase13 synthesis 子代理独立处理，不在分配清单）

> ⚠️ 宏观维度（利率/大宗价格/汇率）不设独立子代理，由消费方按需取数：step3_finance（成本端原材料实时价格）、step6_valuation（折现率利率环境）、step8_risk（宏观风险量化）。`dim_priority` 中**不要**再出现 step5_macro 键。

## 输出
写入 `__PLAN_PATH__`:

```json
{
  "schema_version": "ir_research_plan.v5",
  "task_id": "__JOB_ID__", "entity": "__ENTITY__", "market": "__MARKET__",
  "query": "__QUERY__", "ticker": "__TICKER__", "english_name": "__ENGLISH_NAME__",
  "data_sources_used": ["westock-mcp:行情/财务/研报/行业/公司画像", "ima-mcp:机构研报/纪要", "search_deep:公开信息", "tencent_news:实时动态"],
  "benchmark_found": true,
  "benchmark_skeleton_ref": "__BENCHMARK_SKELETON_PATH__",
  "report_type": "deep_dive",
  "report_type_reason": "依据判定表选择 deep_dive / event_update / earnings_note，并写明理由",
  "valuation_paradigm": "preprofit_growth",
  "paradigm_reason": "优必选亏损+高增长，用 PS/EV-Sales + TAM 份额推导，禁用 PE/DCF",
  "valuation_method_primary": "PS / EV-Sales",
  "valuation_forbidden": ["PE", "DCF"],
  "market_anchor": {
    "as_of": "2026-07-29", "source_report": "BofA-优必选-260715.pdf", "source_age_days": 14, "stale": false,
    "price": 98.5, "market_cap": "410亿",
    "consensus_eps": {"FY25": -1.55, "FY26E": -0.82, "FY27E": 0.15},
    "consensus_revenue": {"FY25": 24.9, "FY26E": 41.0, "FY27E": 68.0},
    "current_multiple": {"PE": "N/A(亏损)", "PS": 16.5, "EV_Sales": 15.2},
    "implied_assumption": "股价隐含 FY25-27 营收 CAGR 65%，毛利率需升至 45%",
    "analyst_ratings": {"buy": 8, "hold": 3, "sell": 1},
    "avg_target_price": 128
  },
  "core_questions": [...], "strategic_questions": [...],
  "key_debates": [
    {"id": "KD-1", "debate": "人形机器人量产时间表", "priority": "P0",
      "market_view": "市场认为 2027 年才能规模化出货",
      "our_view": "我们认为 2026H2 即可小批量商用，BOM 降幅超预期",
      "owner_dims": ["step1_industry", "step3_finance"],
      "data_points": ["单台 BOM ¥18万", "年降幅 30%"]}
  ],
  "dim_priority": {"step1_industry": "P0", "step3_finance": "P0", "step2_biz": "P1", "step4_mgmt": "P2", "step6_valuation": "P0", "step7_insight": "P0", "step8_risk": "P1"},
  "fact_requirements": [...], "section_requirements": {},
  "coverage_matrix": {}, "plan_status": "ready",
  "search_summary": {"westock_quote_found": true, "benchmark_found": true, "analyst_views": 0, "web_evidence_count": 0},
  "ima_insights": [
    {"kb_name": "KB名称", "doc_title": "文档标题", "key_points": ["要点1", "要点2"], "relevance": "high/medium"}
  ]
}
```
