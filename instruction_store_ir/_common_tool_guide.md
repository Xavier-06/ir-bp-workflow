
## 🎯 数据源精确路由（按查询类型选数据源，禁止混用）

**不同类型的查询必须使用对应的数据源，禁止只用 web_search 做所有搜索。**

### 路由矩阵

| 查什么 | 首选数据源 | 调用方式 | 兜底 |
|--------|-----------|---------|------|
| **A/HK/美股实时行情/财务/券商研报/板块/新闻** | **腾讯自选股 MCP (westock-mcp)** | `mcp__westock-mcp__data_quote` / `data_finance` / `data_report` / `data_sector` / `data_news` | NeoData → web_search |
| A股 K线/选股/技术指标/行业链数据 | **通达信 MCP (tdx-connector)** | `mcp__tdx-connector__tdx_quotes` / `tdx_kline` / `tdx_screener` / `tdx_indicator_select` | NeoData → web_search |
| A/HK 股行情/财报/估值 | NeoData `api` | `neodata_search('公司名 营收 净利润 市值', data_type='api')` | yfinance → web_search |
| 美股行情/估值/分红 | yfinance | `yf.Ticker('AAPL').info` | NeoData → web_search |
| **券商研报/行业深度/新闻分析** | **NeoData `doc` + 腾讯自选股 `data_report`/`data_sector`** | `neodata_search('公司名 最新动态', data_type='doc')`；行业数据优先 `mcp__westock-mcp__data_sector` / `data_report` | web_search |
| **突发新闻/实时动态（分钟级）** | **腾讯新闻 CLI** | `sh {SKILL_DIR}/scripts/run-cli.sh search "关键词" --limit 5` | web_search |
| **产品发布/技术动态/新闻分析** | NeoData `doc` + 腾讯新闻补充 | NeoData doc 拿深度分析，腾讯新闻补实时动态 | web_search |
| 企业工商/股东/司法/专利（主源） | 天眼查 MCP | `mcp__tyc-mcp__search_companies` → `call_tool` | web_search |
| 企业工商/股东/司法/专利（交叉验证第二源） | 企查查 MCP (qcc-company) | `mcp__qcc-company__get_company_basic_profile` / `search_companies` | 天眼查 → web_search |
| 技术论文/arxiv | web_search | `web_search('arxiv {company} {technology} {YYYY}')` | web_fetch 读论文页 |
| 开源项目/GitHub/HF | web_search | `web_search('github.com/{company} latest release {YYYY}')` | web_fetch 读 README |
| 网页正文深度阅读 | web_fetch | 直接传 URL | search_deep |

> ⚠️ **westock-mcp / tdx-connector / qcc-company 是已授权的 MCP connector，子代理可直接调用，无需 bash。行业数据、行情、财务、研报、板块、选股类查询必须优先走这些结构化源，禁止只用 web_search 兜底。**

### NeoData 能力细分

- `data_type='api'` → 行情/财报/估值等**结构化数字**（市值、PE、营收、利润等精确数字）
- `data_type='doc'` → **券商研报 + 行业新闻分析 + 产品动态报道**（内容深度高，平均 200-500 字/条，来源经过过滤，质量远优于 web_search snippet）
  - ✅ 能搜到：产品发布（如混元HY3）、行业动态、券商深度、公司战略分析、管理层变动报道
  - ⚠️ `publishedDate` 字段经常为空 → 必须从 content 中提取时间线索（"4月23日"、"2026年Q1"等）
  - **作为新闻/分析的首选数据源**，web_search 用于补充 NeoData 未覆盖的突发新闻和昨日动态
- `data_type='all'` → 两者都取（搜索量更大但混合返回）

### yfinance 能力边界

- **能拿**: 实时行情、市值、PE/PB/PS、EPS、股息率、beta、52 周高低、财务摘要
- **不能拿**: 新闻、研报、分析师报告、产品动态
- A/HK 股 ticker: `600519.SS`（沪）、`000001.SZ`（深）、`0700.HK`（港）
- 美股: `AAPL`、`NVDA`、`MSFT`

### 天眼查 MCP 能力边界

- **能拿**: 工商注册、股东结构、董监高、司法风险、专利、招投标
- **不能拿**: 实时行情、财报、新闻
- **注意**: 工商数据可能滞后 1-3 个月，重大变更需 web_search 交叉验证
- 已在 manifest 中配置天眼查 MCP，可直接调用

### 腾讯自选股 MCP (westock-mcp) 能力边界

- **能拿**: A/HK/美股实时行情、财务数据、券商研报、板块/行业数据、公司新闻、选股/排名/策略
- **不能拿**: 深度新闻分析正文（用 NeoData doc 补）、工商司法（用天眼查/企查查）
- **工具前缀**: `mcp__westock-mcp__*`
  - 行情: `data_quote`；财务: `data_finance`；研报: `data_report`；板块/行业: `data_sector`；新闻: `data_news`；搜索: `data_search`；K线: `data_kline`
  - 选股/筛选: `tool_filter` / `tool_ranking` / `tool_strategy`
- **用法**: 直接在子代理会话里调用 MCP 工具，无需 bash。行业规模/竞争格局/板块数据优先用 `data_sector` + `data_report`，不要只靠 web_search。
- 已在 manifest 中配置腾讯自选股 MCP，可直接调用

### 通达信 MCP (tdx-connector) 能力边界

- **能拿**: A股/全球股票实时行情、K线、技术指标、选股/筛选、行业数据、财经问答
- **不能拿**: 深度研报正文（用 NeoData doc / 腾讯自选股补）、工商司法
- **工具前缀**: `mcp__tdx-connector__*`
  - 行情: `tdx_quotes`；K线: `tdx_kline`；查股票: `tdx_lookup_stock`；选股: `tdx_screener`；指标: `tdx_indicator_select`；通用数据: `tdx_api_data`；问答: `wenda_*`
- **用法**: A股-specific 数据、技术面、选股、行业链数据优先走通达信，不要只靠 web_search。
- 已在 manifest 中配置通达信 MCP，可直接调用

### 企查查 MCP (qcc-company) 能力边界

- **能拿**: 企业工商注册、股东、董监高、司法风险、知识产权、资质、投标
- **不能拿**: 实时行情、财报、新闻
- **工具前缀**: `mcp__qcc-company__*`
  - `get_company_basic_profile` / `search_companies` / `get_company_capabilities` / `get_company_people` / `get_company_capabilities`
- **用法**: 作为天眼查的交叉验证第二来源（两个独立工商源互相印证），提升工商数据可信度。
- 已在 manifest 中配置企查查 MCP，可直接调用

---

## ⏰ 数据时效性硬要求（防止引用过期数据，所有 step 强制）

### 时效锚定协议（每次搜索前必须执行，不可跳过）

**第零轮搜索 — 必须在所有广度搜索之前执行：**

```
1. web_search("{entity} {YYYY}年{M}月 最新动态")
2. web_search("{entity} latest news {YYYY}")
3. web_search("{product} 最新版本 发布 {YYYY}") — 如果涉及具体产品/技术
```

**目的：在任何分析之前，先知道"最新"是什么，避免引用过期信息。**

### 搜索 query 必须含时间锚点

| ❌ 禁止 | ✅ 正确 |
|---------|--------|
| `腾讯 AI 大模型` | `腾讯 混元 最新模型 2026年7月` |
| `优必选 机器人` | `优必选 超仿真机器人 2026 最新发布` |
| `Tesla autonomous driving` | `Tesla FSD latest version July 2026` |
| `行业市场规模` | `行业市场规模 2025 2026 最新数据` |
| `OpenAI GPT` | `OpenAI GPT latest model 2026` |

### 引用数据必须标注日期 + 时效等级

| 时效等级 | 标注 | 条件 |
|---------|------|------|
| 新鲜 | 无标记 | 发布日期在 3 个月内 |
| 可接受 | 无标记 | 发布日期在 6 个月内 |
| 警告 | ⚠️ | 发布日期在 6-12 个月，需说明可能有更新 |
| 过期 | ❌ + 必须补搜 | 发布日期超过 12 个月，必须搜最新版替换 |

### 产品/技术版本验证（AI/科技/制造业公司必查）

当分析涉及具体产品、模型、技术版本时：
1. **搜索**: `web_search("{product} latest version release date {YYYY}")`
2. **搜索**: `web_search("{company} {product} {YYYY}年{M}月 发布")`
3. **确认引用的是最新版本**，如有更新版本必须用最新数据
4. **禁止引用已淘汰/被替代的旧版本而不标注**

**示例**：分析腾讯 AI 能力时，必须搜 `腾讯 混元 最新模型 2026年7月`，如果最新是 HY3 就绝不能引用 HY1。

### 新闻/动态类搜索的时效控制

- web_search 查询关键词**必须包含当前年月**（如 `2026年7月`）
- 搜索结果**优先引用最近 30 天**的信息
- 超过 3 个月的新闻需验证是否有更新报道
- **禁止引用 1 年前的新闻作为"最新动态"**

---

## 📝 脚注引用规范（所有 step 强制，最高优先级）

你在撰写 Markdown 报告时，**必须**对每个关键定量数据添加 `[^N]` 脚注标记，脚注定义放在报告末尾的"来源与参考"章节。

### 什么是"关键定量数据"

市场规模、营收、增速、估值、PE/PS/PB 倍数、EPS、股息率、专利数、员工数、市占率、毛利率、融资金额、持股比例、产能、用户数等任何带数字的关键断言。

### 脚注格式

**正文中**：在数据后面紧跟脚注标记
```
2024年营收约178亿元[^3]，当前PE约18倍[^4]
```

**报告末尾"来源与参考"章节**：展开完整脚注
```
[^1]: 公司年报 — https://www.company.com/annual-report-2024.pdf (2024)
[^2]: Bloomberg — https://www.bloomberg.com/quote/XXX:HK (2025-01)
[^3]: 财报公告 — https://www.hkexnews.hk/listedco/listconews/... (2024-03)
[^4]: NeoData 金融数据 — neodata_search (2026-07-07)
```

### 脚注来源优先级
1. **外部 URL**（web_search / search_gateway 返回的 URL）→ 写完整 URL + 发布日期
2. **NeoData 金融数据** → 写 `NeoData 金融数据 — neodata_search (查询日期)`
3. **公司年报/公告** → 写 `公司年报/公告 — URL (发布日期)`
4. **yfinance** → 写 `yfinance — yfinance.Ticker() (查询日期)`
5. **天眼查** → 写 `天眼查 — mcp__tyc-mcp (查询日期)`

### ⚠️ 铁律
- **facts JSON 的 source_url 和 MD 正文的 [^N] 脚注必须对应**
- **禁止只在 facts JSON 里写 URL 而不在 MD 正文写脚注**——统稿子代理依赖你 MD 中的脚注标记
- **禁止只写内部文件名**：❌ `[^N]: step1_data.md`
- **每条脚注必须有真实来源标注 + 日期**，不能编造 URL

### 输出结构要求
MD 报告末尾必须包含"来源与参考"章节：
```markdown
## 来源与参考
[^1]: 来源名称 — URL (日期)
[^2]: 来源名称 — URL (日期)
...
```

---

## 🔧 搜索与数据工具使用指南（所有 step 通用）

你有以下工具可用，**按场景选择正确的工具**：

### 1. NeoData 金融数据（A/HK 股行情/财报/估值 — 结构化数字首选）

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
# data_type='doc' — 券商研报 + 行业新闻分析 + 产品动态报道
# ⚠️ 这是新闻/分析的首选数据源（内容深度远超 web_search snippet）
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import neodata_search
# 搜研报/深度分析
result = neodata_search('行业名 行业深度报告 市场规模 竞争格局', data_type='doc')
# 搜产品动态/新闻分析
result2 = neodata_search('公司名 最新发布 最新动态', data_type='doc')
# 合并去重，按内容中的时间线索排序（publishedDate 经常为空）
for r in (result + result2):
    print(r.get('title',''), '|', r.get('content','')[:150])
"
```

**⚠️ publishedDate 经常为空** → 必须从 content 文本中提取时间线索（如"4月23日"、"2026年Q1"），据此判断信息新旧。
**NeoData doc + web_search 组合拳**：NeoData doc 拿深度分析 → web_search 补昨日突发新闻和实时动态。

### 2. 腾讯新闻 CLI（突发新闻/实时动态 — 分钟级时效）

**腾讯新闻是突发新闻的首选数据源**，能搜到分钟级的实时报道（如"腾讯混元Hy3昨天发布"）。

```bash
# 搜索新闻（返回标题、摘要、来源、发布时间、链接）
sh {SKILL_DIR}/scripts/run-cli.sh search "腾讯 混元 大模型" --limit 5
```

- `{SKILL_DIR}` = `/Users/xavier/.workbuddy/skills/skill_2053082907836022784`
- 支持的子命令: `search`（搜索）、`hot`（热点榜）、`morning`（早报）、`evening`（晚报）
- `--limit N` 控制返回条数（默认 10）
- 返回结果包含：标题、摘要（100-200字）、来源媒体、精确到分钟的发布时间、原文链接

**使用场景**:
- 第零轮时效锚定：`search "{entity} 最新动态" --limit 5`
- 产品发布验证：`search "{company} {product} 发布" --limit 5`
- 突发新闻：`search "关键词" --limit 5`
- 行业热点：`hot`（当前热点榜）

**腾讯新闻 + NeoData doc 组合拳**：
- 腾讯新闻 → 分钟级突发（标题+摘要，深度有限）
- NeoData doc → 小时~天级深度分析（200-500字/条，分析深度高）
- web_search → 兜底（覆盖英文源和长尾信息）

### 3. 通用网络搜索（新闻、产品发布、技术动态 — 兜底）

**web_search（WorkBuddy 内置工具，直接用）：**
- 用于新闻/产品发布/技术动态等**时效敏感**查询
- **查询必须含当前年月**（如 `腾讯 混元 HY3 2026年7月 发布`）
- 作为所有搜索的兜底手段

```bash
# search_gateway 多引擎聚合（含 DDG + SearXNG + Google）
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import search
results = search('关键词 搜索内容 2026', prefer='auto')
for r in results[:5]:
    print(r.get('title',''), r.get('url',''), r.get('snippet','')[:100])
"
```

- `prefer='auto'` → 金融查 NeoData → DDG → SearXNG
- `prefer='multi'` → 多路并行合并（搜索量最大）

### 3. 工商信息（天眼查 MCP — 已在 manifest 中配置）

**天眼查 MCP 可直接调用**（已在 manifest 中配置）：

- `mcp__tyc-mcp__search_companies` → 按名称搜索公司 → 拿到 company_id
- `mcp__tyc-mcp__get_company_basic_profile` → 基础画像（工商登记+简介+标签+规模）
- `mcp__tyc-mcp__get_company_people` → 人员列表（高管、董监高）
- `mcp__tyc-mcp__get_person_risk_profile` → 个人风险画像
- `mcp__tyc-mcp__search_patents(query="...", applicant="公司名")` → 专利搜索
- `mcp__tyc-mcp__search_bids(query="公司名 招投标")` → 招投标搜索

**⚠️ 天眼查查中国大陆注册企业；境外企业（港股/美股）用 web_search 兜底。**

### 4. yfinance（美股估值精确数据）

```bash
# A/HK 股
/opt/anaconda3/bin/python3 -c "
import yfinance as yf
t = yf.Ticker('688052.SS')  # A股加 .SS(沪) 或 .SZ(深)
info = t.info
print(f'市值: {info.get(\"marketCap\")}')
print(f'PE: {info.get(\"trailingPE\")}')
print(f'PB: {info.get(\"priceToBook\")}')
print(f'股息率: {info.get(\"dividendYield\")}')
"
```

```bash
# 美股
/opt/anaconda3/bin/python3 -c "
import yfinance as yf
t = yf.Ticker('NVDA')
info = t.info
print(f'市值: {info.get(\"marketCap\")}')
print(f'PE: {info.get(\"trailingPE\")}')
print(f'52周高: {info.get(\"fiftyTwoWeekHigh\")}')
print(f'52周低: {info.get(\"fiftyTwoWeekLow\")}')
"
```

**⚠️ yfinance 只能拿行情/估值数字，不能拿新闻/研报/产品动态。**

### 5. 预计算数据（Phase07 输出，优先使用）

```bash
# 财务指标
cat {TASK_DIR}/{JOB_ID}_precompute_financial_metrics.json | python3 -m json.tool

# 行业对标
cat {TASK_DIR}/{JOB_ID}_precompute_sector_benchmarks.json | python3 -m json.tool
```

### 6. 技术/产品数据（AI/科技公司专用）

**产品版本和发布动态（时效性最高）：**
```
web_search("{company} {product} 最新版本 发布 {YYYY}年{M}月")
web_search("{company} product roadmap {YYYY}")
web_search("{product} release date changelog")
```

**开源项目（GitHub/HuggingFace）：**
```
web_search("github.com/{company}/{repo} latest release {YYYY}")
web_fetch("https://github.com/{company}/{repo}") — 读 README 和 release 信息
web_search("huggingface.co/{company} models {YYYY}")
```

**论文和技术验证（arxiv）：**
```
web_search("arxiv {company} {technology} {YYYY}")
web_search("{paper_title} latest follow-up {YYYY}")
web_fetch("https://arxiv.org/abs/XXXX.XXXXX") — 读论文摘要
```

### 7. 网页正文深度阅读

- `web_fetch`（WorkBuddy 内置工具）— 传 URL 返回正文
- `search_deep` — 搜索 + 自动抓 top N 正文，一步到位

---

## 📦 输出三文件协议（每个 step 必须写 3 个文件）

### 1. Markdown 报告
`{TASK_DIR}/{JOB_ID}-{step}.md` — 完整分析报告，含 [^N] 脚注

### 2. Facts Sidecar（JSON）
`{TASK_DIR}/{JOB_ID}-{step}-facts.json`
```json
{
  "step": "step_name",
  "facts": [
    {
      "fact_id": "F-001",
      "claim": "2024年营收178亿元",
      "value": "178",
      "unit": "亿元",
      "period": "2024",
      "source_url": "https://...",
      "source_tier": "official",
      "confidence": "high",
      "retrieved_date": "2026-07-07"
    }
  ]
}
```

### 3. Section Sidecar（JSON）
`{TASK_DIR}/{JOB_ID}-{step}-section.json`
```json
{
  "schema_version": "ir_section_package.v1",
  "section_id": "step_name",
  "section_title": "章节标题",
  "key_messages": ["核心观点1", "核心观点2"],
  "claims": [
    {
      "claim": "具体声称",
      "fact_ids": ["F-001"],
      "reasoning": "推理过程",
      "confidence": "high",
      "source_quality": "official"
    }
  ],
  "facts_used": ["F-001"],
  "counter_evidence": ["反证或风险"],
  "data_gaps": ["无法获取的数据"],
  "markdown_draft": "章节正文"
}
```

### ⚠️ 三文件缺一不可
- 只写 .md 不写 sidecar = 质量生产失败
- 只写 sidecar 不写 .md = 质量生产失败
- JSON 不合法 = 质量生产失败
- **facts 中每条记录必须有 `retrieved_date` 字段**（搜索日期）

---

## 🔒 文件操作规范

- **禁止使用 Glob/Grep 工具**。搜索文件用 Bash（`find`/`ls`），搜索内容用 Bash（`grep`）
- **不要调用 Glob 或 Grep**——这两个工具在你的环境中不存在
- 读文件用 Read 工具
- 写文件用 Write 工具
- 使用 `scripts.bp_file_lock.locked_read_modify_write` 写共享文件（fact_store / sidecar）
