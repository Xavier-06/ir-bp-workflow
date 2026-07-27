

## 🚨 工具可用性硬约束（最高优先级，违反即崩溃）

本环境子代理（general-purpose）**没有 `web_search` 和 `web_fetch` 工具**。调用它们会直接报错 `Tool web_search/web_fetch not found` 并中断任务。

**唯一正确的做法：**
- 通用网络搜索 → Bash 调 `search_deep`（见下方第 6 节）
- 读网页正文 → 用 `search_deep(query, fetch_top_n=N)`，它会搜索并**自动抓取 top N 篇正文**，一步到位，无需手动 fetch
- 结构化金融数据 → NeoData / westock-mcp
- 企业工商/风险 → tyc-mcp
- 机构研报/纪要 → ima-mcp

下文任何出现 `web_search:` / `web_fetch:` 字样的示例块，都**只是查询词/URL 的示意写法**，实际执行时必须套进 `search_deep` 的 Bash 命令里，**绝不能直接当工具调用**。

## 📝 脚注引用规范（所有维度强制，最高优先级）

你在撰写 Markdown 报告时，**必须**对每个关键定量数据添加 `[^N]` 脚注标记，脚注定义放在报告末尾的"来源与参考"章节。

### 什么是"关键定量数据"
市场规模、营收、增速、估值、PS/PE 倍数、专利数、员工数、市占率、毛利率、融资金额、持股比例、认证编号等任何带数字的关键断言。

### 脚注格式

**正文中**：在数据后面紧跟脚注标记
```
2024年营收约1.78亿元[^3]，B+轮投后估值14.2亿元[^4]
```

**报告末尾"来源与参考"章节**：展开完整脚注
```
[^1]: 天眼查工商信息 — 天眼查结构化数据（天眼查 MCP）
[^2]: Yole Intelligence — https://www.yole.com/reports/laser-market-2025 (2025-12)
[^3]: BP自述 — 无外部来源URL
[^4]: 人民网 — https://ah.people.com.cn/n2/2024/0603/c374164-40866555.html (2024-06)
```

### 脚注来源优先级
1. **外部 URL**（search_gateway / search_deep 返回的 URL）→ 写完整 URL
2. **天眼查结构化数据** → 写 `天眼查结构化数据（天眼查 MCP）`
3. **BP 自述数据**（无外部来源）→ 写 `BP自述 — 无外部来源URL`
4. **NeoData 金融数据** → 写 `NeoData 金融数据 — neodata_search`

### ⚠️ 铁律
- **facts JSON 的 source_url 和 MD 正文的 [^N] 脚注必须对应**——你写入 facts JSON 的每条 fact，如果 source_url 有值，对应的 MD 正文必须有脚注
- **禁止只在 facts JSON 里写 URL 而不在 MD 正文写脚注**——统稿子代理依赖你 MD 中的脚注标记
- **禁止只写内部文件名**：❌ `[^N]: bp_phase2_xxx.md`
- **每条脚注必须有真实来源标注**，不能编造 URL

### 输出结构要求
MD 报告末尾必须包含"来源与参考"章节，列出所有 `[^N]` 定义。格式：
```markdown
## 来源与参考
[^1]: 来源名称 — URL (日期)
[^2]: 来源名称 — URL (日期)
...
```

### 三件套输出规范（2026-07-27 新增 — 所有维度子代理必读）

每个维度子代理必须输出**三个文件**（管线 collect 阶段逐一校验）：

| 文件 | 最低要求 | 说明 |
|------|---------|------|
| `bp_phase2_{slug}.md` | >100 bytes | 维度分析正文（markdown） |
| `bp_phase2_{slug}-facts.json` | >10 bytes，合法 JSON | 该维度的事实 sidecar |
| `bp_phase2_{slug}-section.json` | >10 bytes，合法 JSON | 结构化 section package（**schema 见下**） |

**`-section.json` 必须遵循以下 schema（管线 phase26 validator 只认这个）：**

```json
{
  "schema_version": "bp_section_package.v2",
  "section_id": "bp_{slug}",
  "section_title": "中文章节标题",
  "key_messages": ["核心发现1", "核心发现2", "核心发现3"],
  "claims": [
    {
      "claim": "一句话结论",
      "fact_ids": ["F-XXX-001", "F-XXX-002"],
      "reasoning": "推理过程",
      "confidence": "high/medium/low",
      "source_quality": "third_party/bp_self_reported/inferred"
    }
  ],
  "facts_used": ["F-XXX-001", "F-XXX-002"],
  "counter_evidence": ["反面证据或不确定性"],
  "data_gaps": ["未能验证的缺口"],
  "markdown_draft": "（可直接复用 .md 全文，或精简版 ≥100 字符）"
}
```

**规则**：
- `schema_version` 必须写 `"bp_section_package.v2"`（写 `"bp_section_package.v1"` 也可，管线会自动升级）
- `fact_ids` 里的 ID 必须与同目录 `-facts.json` 中 `facts[].fact_id` 一致
- `claims` 至少 1 条，每条必须有 `fact_ids`（不能为空数组）
- `markdown_draft` 不能为空（≥100 字符）
- 禁止用自造 schema（如 `"bp_section.v1"`、`"bp_section_output.v1"`、`"bp_phase2_section.v1"`）——管线不认，会触发 phase26 FAIL

---

## 🔧 搜索与数据工具使用指南（所有维度通用）

你有以下工具可用，按场景选择正确的工具：

### 1. 上市公司金融数据（A/HK/美股行情、财报、估值）

**⚠️ A/HK 股首选 NeoData（结构化金融数据 + 券商研报，token 已存好）：**

```bash
# data_type='api' — 行情/财报结构化数据（市值/营收/利润/PE/PS 等精确数字）
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import neodata_search
result = neodata_search('公司名 营收 净利润 市值 市盈率', data_type='api')
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

```bash
# data_type='doc' — 券商研报/行业深度报告（市场分析/竞争格局/估值逻辑/政策/趋势）
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import neodata_search
result = neodata_search('行业名 行业深度报告 市场规模 竞争格局', data_type='doc')
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

```bash
# data_type='all' — 两者同时返回（最全面，但结果较多）
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import neodata_search
result = neodata_search('公司名 营收 净利润 市值', data_type='all')
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

**NeoData 三种 data_type 及其适用场景：**

| data_type | 返回内容 | 适用场景 |
|-----------|---------|---------|
| `api` | 结构化行情/财报数据（市值/营收/利润/PE/PS 等精确数字） | 需要精确数字的估值、财务对比 |
| `doc` | **券商研报 + 行业深度报告 + 财经新闻 + 政策分析** | 行业分析、竞争格局、政策解读、新闻舆情、供应链、估值逻辑 |
| `all` | api + doc 两者同时返回 | 最全面，但结果较多 |

**⚠️ `data_type=doc` 是新闻和研报的主力数据源——所有维度都应该用它搜行业报告和新闻，不要只用通用搜索。**

**NeoData `data_type=doc` 常用场景（所有维度通用）：**

| 搜什么 | 关键词示例 | data_type |
|--------|-----------|-----------|
| 行业深度报告 | `{行业名} 行业深度报告 市场规模 TAM 增速` | `doc` |
| 公司深度研报 | `{公司名} 深度报告 产品 客户 竞争` | `doc` |
| **财经新闻/行业动态** | `{公司名} 最新动态 融资 合作 订单` | `doc` |
| **行业新闻/政策变化** | `{行业名} 行业新闻 政策 监管 2024 2025` | `doc` |
| 竞争格局/市场份额 | `{行业名} 竞争格局 市场份额 主要厂商 市占率` | `doc` |
| 供应链/上游材料 | `{材料名} 产能 供应 格局 价格` | `doc` |
| 政策/补贴/国产替代 | `{行业名} 政策 补贴 国产替代 自主可控` | `doc` |
| 估值/可比交易 | `{行业名} 估值 PE PS 行业平均 可比公司` | `doc` |
| 下游需求/采购趋势 | `{行业名} 下游需求 订单趋势 景气度` | `doc` |
| 行业风险/监管 | `{行业名} 风险 监管 政策变化 合规` | `doc` |
| 一级市场融资/IPO | `{赛道名} 融资 估值 IPO 并购 投后` | `doc` |
| **负面新闻/风险信号** | `{公司名} 诉讼 纠纷 风险 问题` | `doc` |

- `data_type`：`api`=行情/财报结构化数据，`doc`=研报/券商深度报告/**新闻**，`all`=两者
- 返回结构化数据、研报摘要或新闻标题+URL，可直接引用数字和结论

**search_gateway 聚合搜索（自动识别金融查询，优先走 NeoData）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search
results = search('公司名 营收 利润', prefer='auto')
for r in results[:5]: print(r['title'], r['url'], r['content'][:200])
"
```
- `prefer` 参数：`auto`（默认，金融查 NeoData → DDG → SearXNG）、`multi`（四路合并最全）、`neodata`（纯金融数据）、`ddg`、`searxng`、`google`
- 返回 title + url + content，可直接引用

**深度搜索（搜索 + 自动抓正文）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search_deep
results = search_deep('公司名 行业报告', max_results=5, fetch_top_n=3)
for r in results: print(r['title'], r.get('full_text', '')[:500])
"
```

**批量搜索（多关键词并行）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search_many
all_results = search_many(['公司名 营收', '公司名 融资', '公司名 客户'])
for q, rs in all_results.items(): print(q, len(rs))
"
```

### 2. 上市公司估值指标（PE/PB/PS/市值/股息率/beta）

**yfinance — 精确估值数字**
```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import yfinance_summary
result = yfinance_summary('688052.SS')  # A股 .SS/.SZ，港股 .HK，美股直接 ticker
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```
- 返回：price / market_cap / pe_trailing / pe_forward / ps / pb / ev_ebitda / revenue / profit_margin / sector / industry
- A/HK 股优先走 NeoData（`neodata_search`），美股走 yfinance
- 适合需要精确估值数字、可比公司估值对比时使用

**enrich_valuation — 结构化估值快照（含 NeoData + yfinance 双源交叉验证）**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.valuation_enricher import enrich_valuation
v = enrich_valuation('纳芯微', market='cn')
print(v)
"
```

### 3. 非上市企业工商/司法/专利/资质（BP 尽调核心）

**天眼查 TYC MCP 工具 — 结构化企业数据（两阶段调用）**

天眼查 MCP 采用聚合网关模式，只有一个 connector `tyc-mcp`。工作流程：
1. **锁定企业**：`search_companies(query="公司名")` → 返回候选表，取精确企业名
2. **查可用工具**：`get_company_capabilities(company_id=..., company_name=...)` → 返回该企业可调用的 tool_name 列表
3. **调数据**：`call_tool(tool_name="精确工具名", company_name="...", arguments={...})` → 获取维度数据

**直接画像工具（无需 call_tool，直接调用）：**
- `get_company_basic_profile(company_name="...")` — 基础画像：工商登记、简介、联系方式、标签、规模、曾用名、地址、Logo
- `get_company_people(company_name="...")` — 人员列表：高管、董监高、核心团队
- `get_person_profile(company_name="...", person_name="...")` — 个人画像：任职 + 控制企业
- `get_person_risk_profile(company_name="...", person_name="...")` — 个人风险画像
- `get_company_group_profile(company_name="...")` — 集团画像：成员、对外投资、投资方
- `get_group_info(company_name="...")` — 集团基本信息 + 实控人

**跨公司搜索工具（直接调用）：**
- `search_patents(query="...", applicant="公司名")` — 专利搜索
- `search_trademarks(query="...", applicant="公司名")` — 商标搜索
- `search_bids(query="公司名 招投标")` — 招投标搜索
- `search_listed_companies(query="公司名")` — 上市公司搜索
- `search_companies_by_tag(query="标签名")` — 按标签搜索公司
- `search_companies_by_industry_region(query="...", industry="行业代码", region="地区代码")` — 按行业+地区搜索

**call_tool 常用维度（必须先从 get_company_capabilities 取真实 tool_name）：**
- 股东信息 / 实际控制人 / 受益所有人
- 变更记录 / 分支机构
- 对外投资
- 财务数据 / 上市信息
- 司法文书 / 失信信息 / 行政处罚 / 经营异常 / 股权冻结
- 企业资质 / 招投标
- 专利信息 / 商标信息 / 软件著作权
- 历史股东 / 历史投资 / 历史失信 / 历史司法文书

**什么时候用 TYC：**
- 查公司工商信息（注册资本、股东、高管、实控人）
- 查司法诉讼、失信、行政处罚（风险维度必查）
- 查专利、商标、软著（技术维度必查）
- 查资质、招投标（市场/供应链维度）
- 查历史变更（股权变更、法人变更）
- 查对外投资、关联企业（估值/竞争维度）

**注意：TYC 查的是中国大陆注册企业。如果标的是境外注册，TYC 可能无数据，用 search_deep(Bash) 兜底。**

**⚠️ 关键纪律：call_tool 的 tool_name 必须逐字复制 get_company_capabilities 返回表格中的真实名称，不能按中文含义猜测或翻译。**

### 3.5 腾讯自选股 westock-mcp（A/HK/美股结构化金融，全部 8 维度可用）

**✅ 已对全部 8 个维度开放授权（connector `westock-mcp`）。各维度子代理在角色指令范围内按需调用即可，不再有"未授权角色"限制。westock-mcp 提供的板块/产业链/资金流/北向/机构评级/券商研报是 NeoData/yfinance 之外的增量补充。**

westock-mcp 提供 NeoData/yfinance 之外**独有**的结构化维度，是做可比上市公司分析的增量补充：

| 维度 | westock-mcp 工具 | 查什么 |
|------|-----------------|--------|
| 板块/概念 | `data_sector` | 个股所属板块、概念成分、板块涨跌 |
| 产业链 | `data_industry_chain` | 上下游产业链、关联标的 |
| 资金流 | `data_fund_flow` | 主力/北向资金净流入、筹码分布 |
| 北向持仓 | `data_north_holding` | 北向资金持股变动、外资偏好 |
| 机构评级 | `data_rating` | 券商评级、目标价、评级变动 |
| 券商研报 | `data_report` | 个股研报、盈利预测、催化剂 |
| **个股新闻/公告** | **`data_news`**（需 symbol，如 `sh600519`） | 上市公司公告/研报/新闻列表（type: 0公告 1研报 2新闻 3全部），返回 title/time/url |
| 行情/财务 | `data_quote` / `data_finance` | 实时行情、财报、估值指标 |
| 个股筛选 | `tool_filter` / `tool_ranking` / `tool_strategy` | 按条件/策略/排行筛标的 |

**什么时候用 westock-mcp（优先于 NeoData 的对应维度）：**
- 做可比公司分析时，先拿竞品的**板块归属、产业链位置、北向资金动向、机构评级共识**——这些是 NeoData 不强的
- 需要**券商最新评级/目标价/研报催化**时走 `data_rating` / `data_report`
- 需要**实时资金面/筹码面**时走 `data_fund_flow` / `data_north_holding`
- 需要**某只上市公司的最新公告/新闻/研报动态**时走 `data_news(symbol="sh600519", type=3)`——比通用新闻更聚焦该公司，与 `tencent_news_search`（通用中文新闻）互补

**与 NeoData 的关系**：NeoData 仍是 A/HK 行情/财报/研报主力；westock-mcp 补充板块/产业链/资金流/北向/评级/个股新闻这些 NeoData 覆盖弱的维度，二者交叉验证。**非上市标的没有 westock 数据，直接走 NeoData + WebSearch。**


### 4. 中文实时新闻搜索（tencent_news_search，BP 管线专用）

**⚠️ 搜中文公司新闻的首选——覆盖融资/产品/人事报道。v4.8.1：腾讯新闻 API 积分耗尽，该函数已改为 CLI 优先 → 失败自动降级 NeoData doc，对调用方透明。**

```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
result = tencent_news_search('公司名 融资', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

**适合搜什么：**

| 场景 | 查询示例 |
|------|---------|
| 公司融资/投资新闻 | `{公司名} 融资 投资 轮次` |
| 产品发布/合作动态 | `{公司名} 产品 发布 合作 签约` |
| 高管人事变动 | `{公司名} CEO 任命 离职 加入` |
| 行业政策/监管动态 | `{行业名} 政策 监管 新规` |
| 早期公司报道 | `{公司名} 创业 获投` |

- 返回：title + url + content(摘要) + publishedDate + source
- `source` 字段为 `tencent_news`（CLI）或 `tencent_news:neodata_fallback`（降级 NeoData doc）
- ⚠️ 降级后偏研报/深度分析、弱突发实时；`publishedDate` 常为空，从 content 文本提取时间线索
- 局限：纯中文新闻源，英文查询噪声大；不支持结构化金融数据
- **search_gateway auto 模式已自动集成**：中文查询自动补充中文实时新闻

### 5. Yahoo Finance 搜索（美股新闻 + quote 页面）

**适合搜美股竞品新闻和 quote 页面，免费无 API key，走 7897 代理。**

```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import _yahoo_search
result = _yahoo_search('NVDA earnings revenue', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

- 返回：新闻标题 + Yahoo Finance URL + quote 页面链接
- 适合：美股竞品新闻、earnings 报道、行业趋势
- 局限：不支持中文查询；非金融查询返回空
- **search_gateway auto/multi 模式已自动集成**：金融查询自动补充 Yahoo 新闻

### 6. 通用网络搜索（新闻、行业报告、通用信息）

**search_deep（Bash 调用 search_gateway，替代 search_deep(Bash)）**
- **必须通过 Bash 调用**（不是内置工具，直接调会报错）
- 设 `fetch_top_n≥2` 会搜索并自动抓取正文，相当于 search_deep(Bash) 一步完成
- 适合：搜新闻、行业趋势、媒体报道、通用信息、读全文提取细节
- 不适合：结构化金融数据（用 NeoData/westock）、结构化企业数据（用 TYC 天眼查）
- 作为所有搜索的兜底手段

```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search_deep
import json
r = search_deep('你的查询词', max_results=5, fetch_top_n=3)
print(json.dumps(r, ensure_ascii=False, indent=2))
"
```

### 7. 网页正文深度阅读

**search_deep(Bash)（WorkBuddy 内置工具）**
- 给一个 URL，返回正文内容
- 适合：拿到搜索结果 URL 后，需要读全文提取细节
- 不适合：需要 JS 渲染的页面、反爬严格的站点

**search_gateway search_deep（上面的深度搜索）**
- 搜索 + 自动抓 top N 正文，一步到位
- 适合：搜索并深度阅读，省去手动 fetch

### ⚠️ 工具优先级总结

| 你要查什么 | 首选工具 | 兜底 |
|-----------|---------|------|
| A/HK 股行情/财报/板块 | NeoData (`neodata_search` data_type=api) | WebSearch |
| 美股估值/可比公司 | yfinance (`yfinance_summary`) | NeoData + WebSearch |
| A/HK 可比公司估值 | NeoData + enrich_valuation (双源交叉验证) | yfinance |
| 企业工商/股东/高管 | TYC `get_company_basic_profile` → `call_tool` | WebSearch |
| 司法诉讼/风险/处罚 | TYC `call_tool`（先 `get_company_capabilities` 取 tool_name） | WebSearch |
| 专利/商标/软著 | TYC `search_patents` / `search_trademarks` / `call_tool` | WebSearch |
| 企业资质/招投标 | TYC `call_tool`（先 `get_company_capabilities` 取 tool_name） | WebSearch |
| **投行/券商研报（GS/MS/JPM/BofA/Citi/UBS 等）** | **IMA 自建研报库 (`001a89fa4b807b92`) — 全文可 fetch，所有角色第一优先** | NeoData(doc) → WebSearch → WebFetch 深读 |
| **券商行业深度/技术路线横评** | **IMA 行研智库 (`7311568991699459`) — 全文可 fetch** | IMA 自建研报库 → NeoData(doc) → WebSearch |
| **专家交流/外资研报/机构纪要** | **IMA 机构调研纪要 (`7300811407257275`) — NOTE 可 fetch** | IMA 自建研报库 → NeoData(doc) → WebSearch |
| **第三方白皮书/市场规模** | **IMA 精选行业报告 (`7302509206984644`) — 全文可 fetch** | NeoData(doc) → WebSearch |
| **中文公司新闻（融资/产品/人事）** | **腾讯新闻 (`tencent_news_search`) — 0.7s最快** | NeoData(doc) → WebSearch |
| **财经新闻/行业动态/政策** | **腾讯新闻 (`tencent_news_search`)** | NeoData(doc) → WebSearch |
| **负面新闻/风险舆情** | **腾讯新闻 (`tencent_news_search`)** | NeoData(doc) → WebSearch |
| **美股竞品新闻/earnings** | **Yahoo Finance (`_yahoo_search`)** | WebSearch |
| 通用网络搜索 | WebSearch → WebFetch 深读 | — |
| 读某个 URL 的正文 | WebFetch | — |
| **可比公司板块/产业链/资金流/北向/机构评级** | **westock-mcp（`data_sector`/`data_industry_chain`/`data_fund_flow`/`data_north_holding`/`data_rating`）** | NeoData(doc) → WebSearch |
| **某只上市公司的公告/新闻/研报动态** | **westock-mcp `data_news(symbol="sh600519", type=3)`** | `tencent_news_search` → WebSearch |

### 3.6 IMA 知识库（ima-mcp，自建研报库为主力源，全文可 fetch）

**✅ 已对全部 12 个角色开放授权（connector `ima-mcp`）。v4.8（2026-07-27）起主力源升级为「用户自建研报库」——投行/券商研报（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等）全文可 fetch（实测 ✅），按周分文件夹（每周含 `03_投行报告`=大行研报）。所有角色第一优先搜自建库。**

**为什么自建库是主力源**：用户实测自建库可拉 GS 人形机器人研报全文（含 BOM 成本/ASP/量产指引/目标价方法论），是真正的正文源。旧版主力「长安投研/公司调研报告」两个订阅库**库主禁止导出，0% 可 fetch，只能拿 200 字摘要**——已于 v4.8 彻底删除，不再路由。

| 知识库 | KB ID | 定位 | fetch | 何时用 |
|--------|-------|------|-------|--------|
| **★ 自建研报库** | `001a89fa4b807b92` | 投行/券商研报（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等），按周分文件夹 | ✅ **全文** | **所有角色第一优先**：估值方法论/目标价/BOM 成本/量产指引/行业深度 |
| 行研智库 | `7311568991699459` | 券商行业深度（分年份/行业）3786篇 | ✅ 全文 | 行业研报、技术路线横评、TAM/SAM、产业链 |
| 机构调研纪要 | `7300811407257275` | 专家交流/外资研报/券商点评 33331篇 | ✅ NOTE 可 | 专家观点、外资视角、共识挑战、风险信号 |
| 精选行业数据报告 | `7302509206984644` | 第三方白皮书（艾瑞/头豹/奥纬等）1442篇 | ✅ 全文 | 市场规模、用户画像、竞争格局、趋势预测 |

**ima-mcp 工具调用方式（4 个库全部可 fetch 全文，统一用模式 A）：**
```
# Step 1: 语义搜索 → 拿到 media_id + introduction 摘要
ima-mcp.search_knowledge(knowledge_base_id="库ID", query="搜索词")
# Step 2: 全文提取 → 取最相关 1-3 篇结果的 media_id（多源交叉验证）
ima-mcp.fetch_media_content(media_id="搜索结果中的 media_id")
```
> 机构调研纪要库若某条返回 `can_fetch_content=false`（非 NOTE 类型），退而用 introduction 摘要；其余 3 库直接 fetch 全文。

**⚠️ 全文提取纪律（fetch 到全文后必须做，2026-07-27 新增）：**
fetch 到研报全文不是终点，必须**逐表逐参数抄录**以下硬数据写入 facts sidecar，禁止只读 introduction 摘要后泛泛概括：
- **成本颗粒度**：电芯/产品分档价（元/Wh）、关键材料吨价（万元/吨）+差距倍数、降本时间线（带年份节点）
- **工程 spec 硬参数**：孔容/比表面积 BET/电导率/压实密度/比容量/ICE 等，带数值区间 + 行业实用区间锚
- **技术路线 pros/cons**：每条路线优缺点分条
- **市场规模结构拆分**：半固态 vs 全固态、按下游细分的量（万吨）+空间（亿元）双口径，多机构交叉
- **企业布局全景**：布局企业数/规划产能/已投产/头部产能门槛 + 按路线列企业及量产进度
- **前驱体/工艺路线经济学**（材料/原料类）：≥3 条路线优缺点+成本量级+行业共识
研报里的表格数据要原样落到 sidecar，不要"研报说成本会下降"这种转述。

**⚠️ 时间过滤纪律（自建研报库重要）：**
- 优先取**最近 30 天内**的投行研报——超过 1 个月的研报参考价值显著下降
- 研报标题常含日期（如 `GS-人形机器人-260703.pdf` = 2026-07-03），据此判断时效
- 大行研报优先（GS/MS/JPM/BofA/Citi/UBS/Bernstein），中小行作补充
- 自建库按周分文件夹，搜不到近期内容时可按文件夹时间范围收窄

**来源标注格式：**
- 全文提取成功：`[^N]: IMA 自建研报库 —《{标题}》({日期}, {投行名})`
- 订阅库全文：`[^N]: IMA {库名} —《{标题}》({日期})`
- 仅用摘要：`[^N]: IMA {库名} 搜索摘要 —《{标题}》({日期})`

**角色 → IMA 知识库路由（从 bp_constants.IMA_ROLE_KB_MAP 读取，v4.8 自建库优先）：**

| 角色 | 主力库 | 补充库 |
|------|--------|--------|
| investment_hypothesis | 自建研报库 | 机构调研纪要 |
| company_team_compliance | 自建研报库 | 机构调研纪要 |
| product_commercial | 自建研报库 | 行研智库 |
| tech_ip_moat | 自建研报库 | 行研智库 |
| market_supply_chain | 自建研报库 | 行研智库、精选行业报告 |
| competition_positioning | 自建研报库 | 机构调研纪要 |
| valuation_return | 自建研报库 | 机构调研纪要 |
| customer_revenue_validation | 自建研报库 | 行研智库 |
| dealbreaker_risk | 自建研报库 | 机构调研纪要 |
| consensus_challenge | 自建研报库 | 机构调研纪要 |
| catalyst | 自建研报库 | 机构调研纪要 |
| industry_research | 自建研报库 | 行研智库、精选行业报告 |

**知识库 ID 速查（v4.8，已删除长安投研/公司调研报告）：**
- ★ 自建研报库: `001a89fa4b807b92`（主力源，所有角色第一优先）
- 行研智库: `7311568991699459`
- 机构调研纪要: `7300811407257275`
- 精选行业报告: `7302509206984644`

**搜索纪律：**
- IMA 搜索与结构化源（TYC/NeoData/westock）**并行执行**，不是 web 搜索之后的兜底——每个角色的搜索策略分步流程中必须有显式 IMA Step
- 每次搜索最多取 top 5 结果（浏览标题+摘要选最相关的），全文提取最多 3 篇/库
- **自建研报库优先于订阅库**——先用 `001a89fa4b807b92` 搜投行研报，命中不足再补订阅库
- IMA 搜索结果标注来源时必须写清库名+标题+投行名，如 `[^N]: IMA 自建研报库 —《xxx》(2026-07, GS)`
- 如果 IMA 搜不到相关内容（返回空或无关），直接跳过，不要硬凑

### ⚠️ 不可用工具清单

> 以下工具/数据源已确认不可用，**禁止调用**，浪费时间：

| 工具/数据源 | 不可用原因 | 替代方案 |
|------------|-----------|---------|
| ~~OpenAlex~~ | 503 Service Unavailable（匿名限流，需 API Key） | 用 WebSearch 搜学术信息 |
| ~~Semantic Scholar~~ | 429 Rate Limit（无限挂起，需 API Key） | 用 WebSearch 搜学术信息 |
| ~~CORE~~ | 无 API Key，脚本直接跳过 | 用 WebSearch 搜学术信息 |
| ~~GROBID~~ | Docker 未运行 | 不需要 PDF 提取（BP 管线不处理论文全文） |

### ⚠️ 禁止行为
- 禁止只用 WebSearch 做所有搜索——WebSearch 没有 NeoData 金融数据，没有 TYC（天眼查）结构化数据
- 禁止在能用 TYC 直接查到结构化数据时用 WebSearch 去搜（如查股东信息，TYC 直接返回结构列表，WebSearch 只能搜到新闻）
- 禁止在需要精确估值数字时只用 WebSearch（用 yfinance 或 NeoData）
- 禁止编造 URL、编造数据源、编造引用
- 所有 8 个维度均已授权 westock-mcp（板块/产业链/资金流/北向/机构评级/券商研报），子代理在角色指令范围内按需调用即可，不再有"非授权角色"限制

### ⚠️ 搜索审计（强制 — 报告末尾必须包含）

报告末尾必须包含「搜索审计」章节，格式：

```markdown
## 搜索审计

| 搜索内容 | 数据源 | 查询关键词 | 结果数 |
|---------|--------|-----------|-------|
| 公司财务数据 | NeoData api / westock-mcp | "公司名 营收 利润" | N 条 |
| 股东信息 | TYC call_tool | "公司名 股东" | N 条 |
| 行业新闻 | 腾讯新闻 | "关键词" | N 条 |
| 机构调研/专家观点 | IMA 自建研报库/机构调研纪要 | "关键词" | N 条 |
| 行业研报/白皮书 | IMA 行研智库/精选报告 | "关键词" | N 条 |
| ... | ... | ... | ... |

来源域名: [列出所有引用的域名]
IMA 来源: [列出引用的 IMA 知识库名 + 标题]
```

如果全部来源都是通用搜索(search_deep)，说明为什么没用结构化数据源（没有合理理由将被视为质量不合格）。

## 角色边界（写在每个角色指令开头）

| 角色 | 可以做 | 禁止做 |
|------|--------|--------|
| company_team_compliance | TYC 工商/股东/高管/实控人/风险/资质 + WebSearch 人物履历 + NeoData(api)上市股东 + **NeoData(doc)行业新闻/人物报道** + **westock-mcp 上市关联方板块/产业链/资金流** + **IMA 自建研报库/机构调研纪要（投行研报/人物报道）** | 估值分析/市场规模推算/技术路线/论文 |
| product_commercial | TYC 客户验证/招投标 + WebSearch 产品/客户/合同 + NeoData(api)上市客户 + **NeoData(doc)行业新闻/产品报道** + **westock-mcp 可比上市公司客户所在板块/产业链/资金流** + **IMA 自建研报库/行研智库（产品/客户/订单研报）** | 估值分析/技术路线/市场规模 |
| tech_ip_moat | TYC 专利/商标/软著 + WebSearch 技术路线/论文/标准 + NeoData(api)竞品研发 + **NeoData(doc)技术趋势/行业研报** + **westock-mcp 上市可比技术公司板块/产业链/机构评级** + **IMA 自建研报库/行研智库（技术路线横评/投行研报）** | 估值分析/市场规模/客户收入 |
| market_supply_chain | NeoData(api+doc)行业研报/竞对/新闻 + yfinance 美股竞对 + TYC 供应商 + WebSearch 行业/政策 + **westock-mcp 上市竞对板块/产业链/资金流/北向/机构评级** + **IMA 自建研报库/行研智库/精选报告（行业深度/白皮书/TAM）** | 估值建模/技术路线/团队分析 |
| competition_positioning | TYC 竞品验证 + NeoData(api+doc)/yfinance 竞品财务/新闻/研报 + **westock-mcp 板块/产业链/资金流/北向/机构评级** + WebSearch 竞品情报 + **IMA 自建研报库/机构调研纪要（竞品投行研报/机构点评）** | 估值主模型/客户收入主结论/泛化风险 |
| valuation_return | NeoData(api+doc)/yfinance/enrich_valuation 三源估值+研报 + **westock-mcp 板块/产业链/机构评级/资金流** + WebSearch 可比交易 + **IMA 自建研报库/机构调研纪要（可比公司估值/目标价方法论）** | 客户验证/技术判断/竞品主分析/市场规模 |
| customer_revenue_validation | TYC 客户全量验证 + WebSearch 收入/订单 + NeoData(api)上市客户 + **NeoData(doc)客户新闻/订单报道** + **westock-mcp 上市客户板块/产业链/资金流** + **IMA 自建研报库/行研智库（客户投行研报/机构点评）** | 估值分析/技术判断/市场规模/竞品主分析 |
| dealbreaker_risk | TYC 风险全扫(35项) + **NeoData(doc)负面新闻/风险舆情/监管动态** + WebSearch 负面/舆情/监管 + NeoData(api)前置验证 + **westock-mcp 上市主体板块/产业链/资金流/北向/机构评级（风险佐证）** + **IMA 自建研报库/机构调研纪要（风险信号/外资观点）** | 估值建模/客户主验证/技术判断/市场规模 |
| 统稿 | Read 所有维度输出 + Write 完整报告 | 搜任何外部数据 |

## 搜索规范

1. **双语搜索**: 同一维度英文搜一次、中文搜一次，确保覆盖中英文信息源
2. **多源交叉验证**: 关键数据（市场规模/估值/客户/专利）至少 2 个独立来源确认
3. **审计日志**: 每个搜索查询记录（查了什么、返回多少、保留多少），写入 .md 审计部分
4. **时间过滤**: 优先近 2-3 年数据，历史数据标注年份
5. **去重**: 同一公司/事实的多条结果合并，不重复计数
6. **缺口标注**: 搜不到的字段必须写入 `data_gaps`，不能静默跳过
7. **否定结论需强证据**: "竞品没有 X 能力" 必须有搜索证据，搜不到则改为 "未找到竞品具有 X 能力的公开证据"
