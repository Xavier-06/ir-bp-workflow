# 数据源与搜索策略

## 数据源优先级

| 优先级 | 数据源 | 使用方式 |
|-------|--------|---------|
| 1 | NeoData 金融搜索 | A/HK 股行情、**最新季度财报**、板块、券商研报（search_gateway Layer 0 自动调用） |
| 2 | yfinance (Python) | 估值指标、美股主力、A/HK 股交叉验证（PE/PS/市值/财报/key statistics） |
| 3 | web_search | 实时搜索（东财/雪球/同花顺 行情、公告、行业报告、**财报新闻稿**） |
| 4 | 企查查 MCP | 国内公司工商信息、融资轮、诉讼、知产 |
| 5 | RAG_search | 向量记忆知识库 |
| 6 | tushare / yahoo skill | 补充金融数据 |

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
