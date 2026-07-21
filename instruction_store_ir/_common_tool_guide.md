
## 🎯 数据源精确路由（按查询类型选数据源，禁止混用）

**不同类型的查询必须使用对应的数据源，禁止只用 web_search 做所有搜索。**

### 路由矩阵

| 查什么 | 首选数据源 | 调用方式 | 兜底 |
|--------|-----------|---------|------|
| **A/HK/美股实时行情/财务/券商研报/板块·产业链/资金流/北向/评级/新闻** | **腾讯自选股 MCP (westock-mcp)** | `mcp__westock-mcp__data_quote` / `data_finance` / `data_report` / `data_sector` / `data_industry_chain` / `data_fund_flow` / `data_north_holding` / `data_rating` / `data_news` | NeoData → web_search |
| A/HK 股行情/财报/估值 | NeoData `api` | `neodata_search('公司名 营收 净利润 市值', data_type='api')` | yfinance → web_search |
| 美股行情/估值/分红 | yfinance | `yf.Ticker('AAPL').info` | NeoData → web_search |
| **美股英文新闻/earnings/分析师** | **Yahoo Finance `_yahoo_search`** | `_yahoo_search('NVDA earnings AI chip', max_results=5)` | web_search |
| **券商研报/行业深度/产业链** | **NeoData `doc` + 腾讯自选股 `data_report`/`data_sector`/`data_industry_chain`** | `neodata_search('公司名 最新动态', data_type='doc')`；行业/产业链数据优先 `mcp__westock-mcp__data_sector` / `data_industry_chain` / `data_report` | web_search |
| **突发新闻/实时动态（分钟级）** | **腾讯新闻 CLI** | `sh {SKILL_DIR}/scripts/run-cli.sh search "关键词" --limit 5` | web_search |
| **产品发布/技术动态/新闻分析** | NeoData `doc` + 腾讯新闻补充 | NeoData doc 拿深度分析，腾讯新闻补实时动态 | web_search |
| 企业工商/股东/司法/专利 | 天眼查 MCP | `mcp__tyc-mcp__search_companies` → `call_tool` | web_search |
| 技术论文/arxiv | web_search | `web_search('arxiv {company} {technology} {YYYY}')` | web_fetch 读论文页 |
| 开源项目/GitHub/HF | web_search | `web_search('github.com/{company} latest release {YYYY}')` | web_fetch 读 README |
| 网页正文深度阅读 | web_fetch | 直接传 URL | search_deep |

> ⚠️ **westock-mcp（腾讯自选股）是已授权的 MCP connector，子代理可直接调用，无需 bash。行业数据、行情、财务、研报、板块、产业链、资金流、选股类查询必须优先走该结构化源，禁止只用 web_search 兜底。**

### NeoData 能力细分

- `data_type='api'` → 行情/财报/估值等**结构化数字**（市值、PE、营收、利润等精确数字）
- `data_type='doc'` → **券商研报 + 行业新闻分析 + 产品动态报道**（内容深度高，平均 200-500 字/条，来源经过过滤，质量远优于 web_search snippet）
  - ✅ 能搜到：产品发布（如混元HY3）、行业动态、券商深度、公司战略分析、管理层变动报道
  - ⚠️ `publishedDate` 字段经常为空 → 必须从 content 中提取时间线索（"4月23日"、"2026年Q1"等）
  - **作为新闻/分析的首选数据源**，web_search 用于补充 NeoData 未覆盖的突发新闻和昨日动态
- `data_type='all'` → 两者都取（搜索量更大但混合返回）

### yfinance + Yahoo Finance 能力边界

- **yfinance 能拿**: 实时行情、市值、PE/PB/PS、EPS、股息率、beta、52 周高低、财务摘要
- **Yahoo Finance `_yahoo_search` 能拿**: **美股英文新闻、earnings calls、分析师评级变动、产品动态**（免费无需 API key）
- **不能拿**: A 股数据、中文新闻（中文新闻用腾讯新闻 CLI）
- A/HK 股 ticker: `600519.SS`（沪）、`000001.SZ`（深）、`0700.HK`（港）
- 美股: `AAPL`、`NVDA`、`MSFT`

**Yahoo Finance 新闻搜索（美股竞品/earnings/分析师动态）**:
```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import _yahoo_search
result = _yahoo_search('NVDA earnings revenue AI chip 2025', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

### 天眼查 MCP 能力边界

- **能拿**: 工商注册、股东结构、董监高、司法风险、专利、招投标
- **不能拿**: 实时行情、财报、新闻
- **注意**: 工商数据可能滞后 1-3 个月，重大变更需 web_search 交叉验证
- 已在 manifest 中配置天眼查 MCP，可直接调用

### 腾讯自选股 MCP (westock-mcp) 能力边界

- **能拿（IR 最核心的结构化金融源）**: A/HK/美股实时行情、财务数据、券商研报、板块/行业、产业链、资金流、北向持仓、评级/评分、新闻、分红、IPO、选股/排名/策略
- **不能拿**: 深度新闻分析正文（用 NeoData doc 补）、工商司法（用天眼查）
- **工具前缀**: `mcp__westock-mcp__*`
  - 行情: `data_quote`；财务: `data_finance`；研报: `data_report`；板块: `data_sector`；产业链: `data_industry_chain`；资金流: `data_fund_flow`；北向持仓: `data_north_holding`；评级: `data_rating`；评分: `data_score`；新闻: `data_news`；K线: `data_kline`；宏观: `data_macro`；股东: `data_shareholder`
  - 选股/筛选: `tool_filter` / `tool_ranking` / `tool_strategy`
- **用法**: 直接在子代理会话里调用 MCP 工具，无需 bash。行业规模/竞争格局/产业链/资金面/北向/机构评级等数据优先用对应工具，不要只靠 web_search。
- 已在 manifest 中配置腾讯自选股 MCP，可直接调用

### IMA 知识库 MCP (ima-mcp) — 机构级研报/纪要/外资研报

**12万+机构级文档，公开 web 搜不到的增量信息。已授权 MCP connector，子代理可直接调用。**

#### 可用知识库目录

| KB ID | 名称 | 文档数 | 内容特色 | 最佳使用场景 |
|-------|------|--------|---------|-------------|
| `7297585010204027` | 长安投研【持续更新】 | 46,493 | 投行产业专家调研纪要 | 行业深度、专家洞察、一线调研纪要、产业链调研 |
| `7302533890465245` | 公司调研报告研究报告 | 35,458 | 上市公司调研+券商研报 | 公司基本面分析、券商深度研报、财务解读 |
| `7300811407257275` | 机构调研纪要/外资研报库 | 33,331 | 机构调研、电话会议、专家交流、外资研报 | 机构视角、外资观点、电话会纪要、专家交流 |
| `7311568991699459` | 行业研究报告库(行研智库) | 3,786 | 行研报告（新能源/AI/消费/医药/金融/地产） | 行业趋势、政策解读、TAM/竞争格局 |
| `7302509206984644` | 精选行业数据报告 | 1,442 | 精选精品报告 | 高质量筛选的行业/公司报告 |

#### 调用方式

**search_knowledge — 语义搜索（核心工具）：**
```
mcp__ima-mcp__search_knowledge(
  knowledge_base_id="7297585010204027",
  query="公司名 行业 关键词"
)
```

**get_knowledge_list — 列出文档（按时间/标题排序）：**
```
mcp__ima-mcp__get_knowledge_list(
  knowledge_base_id="7311568991699459",
  limit=20,
  sort_type="UPDATE_TS_DESC_SORT_TYPE"
)
```

#### 什么时候该搜 IMA（优先级定位）

| 场景 | 首选 KB | 为什么搜 IMA 而不是 NeoData/web |
|------|---------|-------------------------------|
| **行业深度/竞争格局/TAM** | 行研智库 + 长安投研 | 机构行研报告比 web 深度高 10 倍，比 NeoData doc 覆盖广 |
| **公司基本面/财务解读** | 公司调研报告 | 券商深度研报含盈利预测和估值逻辑 |
| **机构观点/市场共识** | 机构调研纪要 | 电话会纪要、专家交流是公开源搜不到的 |
| **外资视角/跨境对比** | 外资研报库 | 外资券商研报（高盛/摩根/瑞银等）独立视角 |
| **产业链上下游调研** | 长安投研 | 投行产业专家的一线调研纪要 |
| **政策/监管解读** | 行研智库 | 机构级政策解读报告 |

#### 搜索策略建议

1. **Step1（数据收集）**：必搜全部 5 个 KB，用 "{公司名}" 和 "{行业名}" 各搜一轮，建立机构信息基线
2. **Step2（行业分析）**：重点搜行研智库 + 长安投研，用行业关键词 + 竞争格局 + TAM
3. **Step3（商业模式）**：搜公司调研报告 + 长安投研，关注客户/订单/收入结构
4. **Step4（财务分析）**：搜公司调研报告，关注盈利预测和估值逻辑
5. **Step6b（估值）**：搜外资研报库，看外资估值方法和可比公司
6. **Step7（风险催化）**：搜机构调研纪要，关注机构担忧的风险点
7. **Step6/Step8（洞察/统稿）**：按需补搜，关注差异化观点

#### IMA 在数据源路由中的位置

```
NeoData doc / westock-mcp（结构化优先）
    ↓ 未覆盖时
IMA 知识库（机构研报/纪要 — 增量层）
    ↓ 未覆盖时
search_deep / 腾讯新闻（web 兜底）
```

**与 NeoData doc 的关系**：NeoData doc 返回的是券商研报摘要（200-500字），IMA 返回的是机构研报/纪要全文片段——深度更高、视角更独特（专家交流、电话会纪要等公开源搜不到的内容）。两者互补，不替代。

#### 脚注格式

```
[^N]: IMA知识库 — {KB名称} — "{文档标题}" (检索日期)
```

示例：
```
[^15]: IMA知识库 — 长安投研 — "新能源汽车产业链调研纪要：电池材料格局与投资机会" (2026-07-21)
[^16]: IMA知识库 — 机构调研纪要 — "某头部锂电企业专家交流纪要：固态电池量产时间表" (2026-07-21)
```

#### ⚠️ IMA 使用纪律

- **不要只用一个 KB**：5 个 KB 内容互补，同一查询应搜 2-3 个相关 KB
- **搜索结果可能很长**：提取关键数据点和结论即可，不要全文照搬
- **注意时效性**：KB 持续更新但可能有滞后，近 6 个月内的信息优先引用
- **脚注必须标注 KB 名称和文档标题**：方便统稿子代理追溯来源
- **已在 manifest 中配置 ima-mcp**：子代理可直接调用 MCP 工具，无需 bash

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
