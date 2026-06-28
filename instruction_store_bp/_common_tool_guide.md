

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
[^1]: 企查查工商信息 — 企查查结构化数据（企查查 MCP）
[^2]: Yole Intelligence — https://www.yole.com/reports/laser-market-2025 (2025-12)
[^3]: BP自述 — 无外部来源URL
[^4]: 人民网 — https://ah.people.com.cn/n2/2024/0603/c374164-40866555.html (2024-06)
```

### 脚注来源优先级
1. **外部 URL**（web_search / search_gateway 返回的 URL）→ 写完整 URL
2. **企查查结构化数据** → 写 `企查查结构化数据（企查查 MCP）`
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

---

## 🔧 搜索与数据工具使用指南（所有维度通用）

你有以下工具可用，按场景选择正确的工具：

### 1. 上市公司金融数据（A/HK/美股行情、财报、估值）

**⚠️ A/HK 股首选 NeoData（结构化金融数据，token 已存好）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import neodata_search
import json
results = neodata_search('公司名 营收 净利润 市值', data_type='all')
print(json.dumps(results, ensure_ascii=False))
"
```
- `data_type`：`api`=行情/财报结构化数据，`doc`=研报/新闻，`all`=两者
- 返回结构化金融数据，可直接引用数字

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

**yfinance — 精确估值数字（⚠️ 必须用 /opt/anaconda3/bin/python3，不能用默认 python3）**
```bash
/opt/anaconda3/bin/python3 -c "
import yfinance as yf
t = yf.Ticker('688052.SS')  # A股 .SS/.SZ，港股 .HK，美股直接 ticker
info = t.info
print(info.get('marketCap'), info.get('trailingPE'), info.get('priceToSalesTrailing12Months'))
"
```
- 返回：ticker / price / market_cap / pe_trailing / pe_forward / ps / pb / ev_ebitda / revenue / profit_margin / sector / industry
- A/HK 股优先走 NeoData（`search_gateway neodata_search`），美股走 yfinance
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

**企查查 QCC MCP 工具 — 结构化企业数据**

直接用 MCP 工具调用（不需要 Bash），常用工具：
- `mcp__qcc-company__get_company_info` — 企业基本信息（注册、股东、高管）
- `mcp__qcc-executive__get_executive_positions` — 高管任职
- `mcp__qcc-executive__get_executive_controlled_companies` — 实控人关联企业
- `mcp__qcc-risk__get_judicial_documents` — 司法文书
- `mcp__qcc-risk__get_dishonest_info` — 失信信息
- `mcp__qcc-risk__get_case_filing_info` — 立案信息
- `mcp__qcc-risk__get_administrative_penalty` — 行政处罚
- `mcp__qcc-ipr__get_patent_info` — 专利信息
- `mcp__qcc-ipr__get_trademark_info` — 商标信息
- `mcp__qcc-ipr__get_software_copyright_info` — 软件著作权
- `mcp__qcc-operation__get_qualifications` — 企业资质
- `mcp__qcc-operation__get_bidding_info` — 招投标信息
- `mcp__qcc-history__get_historical_shareholders` — 历史股东变更
- `mcp__qcc-history__get_historical_investments` — 历史对外投资

**什么时候用 QCC：**
- 查公司工商信息（注册资本、股东、高管、实控人）
- 查司法诉讼、失信、行政处罚（风险维度必查）
- 查专利、商标、软著（技术维度必查）
- 查资质、招投标（市场/供应链维度）
- 查历史变更（股权变更、法人变更）
- 查对外投资、关联企业（估值/竞争维度）

**注意：QCC 查的是中国大陆注册企业。如果标的是境外注册，QCC 可能无数据，用 web_search 兜底。**

### 4. 通用网络搜索（新闻、行业报告、通用信息）

**web_search（WorkBuddy 内置工具）**
- 直接用，不需要 Bash
- 适合：搜新闻、行业趋势、媒体报道、通用信息
- 不适合：结构化金融数据（用 search_gateway）、结构化企业数据（用 QCC）
- 作为所有搜索的兜底手段

### 5. 网页正文深度阅读

**web_fetch（WorkBuddy 内置工具）**
- 给一个 URL，返回正文内容
- 适合：拿到搜索结果 URL 后，需要读全文提取细节
- 不适合：需要 JS 渲染的页面、反爬严格的站点

**search_gateway search_deep（上面的深度搜索）**
- 搜索 + 自动抓 top N 正文，一步到位
- 适合：搜索并深度阅读，省去手动 fetch

### ⚠️ 工具优先级总结

| 你要查什么 | 首选工具 | 兜底 |
|-----------|---------|------|
| A/HK 股行情/财报/板块 | search_gateway (prefer=auto/neodata) | web_search |
| 美股估值/可比公司 | /opt/anaconda3/bin/python3 + yfinance | search_gateway |
| A/HK 可比公司估值 | NeoData neodata_search 或 enrich_valuation | yfinance |
| 企业工商/股东/高管 | QCC MCP (qcc-company) | web_search |
| 司法诉讼/风险/处罚 | QCC MCP (qcc-risk) | web_search |
| 专利/商标/软著 | QCC MCP (qcc-ipr) | web_search |
| 企业资质/招投标 | QCC MCP (qcc-operation) | web_search |
| 新闻/行业报告/通用 | search_gateway (prefer=multi) | web_search |
| 读某个 URL 的正文 | web_fetch | — |
| 搜索+读正文一步到位 | search_gateway search_deep | — |

### ⚠️ 禁止行为
- 禁止只用 web_search 做所有搜索——web_search 没有 NeoData 金融数据，没有 QCC 结构化数据
- 禁止在能用 QCC 直接查到结构化数据时用 web_search 去搜（如查股东信息，QCC 直接返回结构列表，web_search 只能搜到新闻）
- 禁止在需要精确估值数字时只用 web_search（用 yfinance 或 search_gateway）
