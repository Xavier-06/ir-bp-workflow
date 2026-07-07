# 数据源与搜索策略

## 数据源优先级

| 优先级 | 数据源 | 使用方式 |
|-------|--------|---------|
| 1 | **腾讯自选股 MCP (westock-mcp)** | A/HK/美股实时行情、财务、券商研报、板块/行业数据、公司新闻、选股（结构化首选，已授权） |
| 2 | **通达信 MCP (tdx-connector)** | A股/全球行情、K线、选股筛选、技术指标、行业链数据（已授权） |
| 3 | NeoData 金融搜索 | A/HK 股行情、**最新季度财报**、板块、券商研报（search_gateway Layer 0 自动调用） |
| 4 | yfinance (Python) | 估值指标、美股主力、A/HK 股交叉验证（PE/PS/市值/财报/key statistics） |
| 5 | 天眼查 MCP | 国内公司工商信息、融资轮、诉讼、知产（已授权） |
| 6 | **企查查 MCP (qcc-company)** | 企业工商注册，作为天眼查的交叉验证第二来源（已授权） |
| 7 | web_search | 实时搜索（公告、行业报告、财报新闻稿）、突发新闻、长尾兜底 |
| 8 | RAG_search | 向量记忆知识库 |
| 9 | tushare / yahoo skill | 补充金融数据 |

> ⚠️ westock-mcp / tdx-connector / qcc-company 为 MCP connector，子代理在 `connectorIds` 中已授权，可直接调用，无需 bash。行业/行情/财务/研报类查询必须优先走这些结构化源，web_search 仅作突发新闻与长尾兜底。

## 最新季度财报获取策略

**核心问题**：管线预搜索基于历史数据，可能未覆盖最新发布的季度/半年报。

### 数据源对比（季度财报场景）

| 数据源 | 时效性 | 结构化程度 | 适合场景 |
|--------|--------|-----------|---------|
| **NeoData** | ★★★★★ | ★★★★★ | 分业务营收/利润、财务指标、券商研报 |
| **WebSearch** | ★★★★☆ | ★★☆☆☆ | 新闻稿、业绩公告原文、管理层表态 |
| **Yahoo Finance** | ★★★★☆ | ★★★★☆ | 港股/美股行情、估值数据 |

### 获取流程

```
NeoData（结构化利润表 + 财务复合指标 + 分业务构成）
    ↓ 如果分业务数据不够细
WebSearch（"{公司名} {季度} 财报 分业务 营收 毛利率"）
    ↓ 抓取新闻稿正文
WebFetch（业绩公告原文页面）
```

### NeoData 查询关键词模板

```bash
# 利润表（营收、净利润、毛利率、EPS）
neodata_search('{公司名} 最新季度 利润表', data_type='api')

# 分业务收入构成
neodata_search('{公司名} 主营构成 分业务收入', data_type='api')

# 财务复合指标（ROE、资产负债率、现金流）
neodata_search('{公司名} 财务主要复合指标', data_type='api')

# 综合查询（一次拿到全部）
neodata_search('{公司名} 最新季度财报 营收 净利润', data_type='api')
```

## 搜索降级链

NeoData Layer 0（金融查询自动触发）→ DDG → SearXNG(8888) → Yahoo Finance

## 估值数据获取

使用 `valuation_enricher.py` 获取实时估值（A/HK 股优先 NeoData + yfinance 交叉验证）：
```bash
python3 {IR_RUNTIME}/tasks/valuation_enricher.py --entity "标的名称"
```

## A 股特殊处理

- 股票代码：6 位数字（60xxxx / 00xxxx / 30xxxx / 688xxx）
- 红涨绿跌
- NeoData 原生支持 A 股中文查询（如"贵州茅台股价"直接返回行情）
- valuation_enricher 自动映射：6位代码→SZ/SS/BJ 后缀
- 中文名映射：公司名→股票代码→yfinance 查询
- NeoData 估值数据含：实时价格、PE(TTM)、PB、市值、成交额、资金流向、换手率

## 文献综述管线数据源 (Literature Review)

> 与 BP/IR 管线共享 NeoData / 天眼查 / 腾讯新闻 / Yahoo，但额外有学术源与全文提取链。子代理工具箱详见 `instruction_store_lit/_common_tool_guide.md`。

### 数据源优先级

| 优先级 | 数据源 | 使用方式 |
|-------|--------|---------|
| 1 | arXiv / DBLP / PMC / Crossref | 学术论文元数据（`scripts/api_clients/*` + `unified_search`，领域判定后路由） |
| 2 | PMC EFetch | 生物医学全文 XML（OA 首选） |
| 3 | pdf_downloader | 全文 PDF 获取（arXiv OA / Unpaywall DOI / PMC） |
| 4 | NeoData | A/HK 行业研报 + 行情估值（`scripts/search/neodata_search.py`） |
| 5 | 腾讯新闻 | 中文企业/行业快讯（0.7s，`tencent_news_search`） |
| 6 | Yahoo Finance | 美股竞品新闻/earnings（`_yahoo_search`） |
| 7 | TYC 天眼查 MCP | 中国企业工商/融资/诉讼/知产（enterprise_scout） |
| 8 | yfinance | 美股估值快照（price/PE/PB/market_cap） |
| 9 | WeStock / SEC EDGAR | 个股研报 / 美股 10-K·S-1 |
| 10 | WebSearch / WebFetch | 白皮书/咨询报告/已知 URL 爬取 |

### 全文提取链
- Marker（报告/白皮书，首选）→ pdfplumber（兜底）
- GROBID 不可用（Docker 未运行）

### 不可用源（已确认）
- OpenAlex（503）、Semantic Scholar（429）、CORE（无 key）：学术引用网络分析受限，改用 WebSearch
