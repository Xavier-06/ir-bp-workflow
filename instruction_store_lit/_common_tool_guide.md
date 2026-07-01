# 全局工具指南 (子代理工具箱架构)

> 本文件所有角色共享。你的角色专属指令会告诉你**该用哪些**，本文件告诉你**怎么用**。

## ⚠️ 工具限制 (所有角色)

你没有 Glob/Grep 工具。
- 搜索文件 → `Bash: find {path} -name "*.json"`
- 读取文件 → `Read`
- 搜索内容 → `Bash: grep -r "keyword" {path}`

## 子代理工具箱 (按维度分组，非降级链)

### A. 学术元数据 & 引用网络

| 工具 | 调用方式 | 查什么 | 不可替代价值 |
|------|---------|--------|------------|
| S2 | `python3 scripts/api_clients/s2_client.py "query" --json` | 引用图谱+tldr+SPECTER2 | 引用链遍历 (references/citations) |
| OpenAlex | `python3 scripts/api_clients/openalex_client.py "query" --json` | 四级领域分类+机构+趋势 | 领域分类体系 |
| Crossref | `python3 scripts/api_clients/crossref_client.py "doi" --json` | DOI+参考文献+基金 | DOI 权威元数据 |
| DBLP | `python3 scripts/api_clients/dblp_client.py "query" --json` | CS 论文补充 | CS 会议覆盖 |
| PMC | `python3 scripts/api_clients/pmc_client.py "query" --json` | 生物医学论文 | 生物医学 OA 全文 |
| arXiv | `python3 scripts/api_clients/arxiv_client.py "query" --json` | 预印本 | 预印本首发 + PDF 直下 |

**统一搜索（多源并行 + 去重 + 排序）：**
```bash
cd {RUNTIME_ROOT} && python3 scripts/search/unified_search.py "关键词" --sources openalex,arxiv,s2,dblp,pmc --max-results 30 --json
```

### B. 论文全文获取 (按文档类型路由)

**路径 A: 学术论文**
```bash
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py --fact-id DOC-001 --type paper --json
# 路由: arXiv ID → arXiv PDF / PMC ID → PMC XML / DOI → Unpaywall / OpenAlex OA URL / CORE
```

**路径 B: 券商研报**
```bash
cd {RUNTIME_ROOT} && python3 scripts/search/neodata_search.py "固态电池 行业研报" --data-type all --json
# 或 westock-data MCP
```

**路径 C: 行业报告/白皮书**
```
WebFetch → 已知 PDF URL 直接下载
WebFetch → 已知网页 URL 爬取正文
```

**路径 D: 公司披露**
```
WebFetch → SEC EDGAR 10-K/S-1
QCC MCP → 中国企业工商
```

**路径 E: 新闻**
```
WebSearch → WebFetch 爬取
```

### C. 行业研报

| 工具 | 调用方式 | 查什么 |
|------|---------|--------|
| NeoData | `python3 scripts/search/neodata_search.py "关键词" --json` | A股/港股券商行业研报 |
| WeStock | westock-data MCP | 个股研报+分析师评级 |
| SEC EDGAR | WebFetch | 美股上市公司 10-K/S-1 |

### D. 企业情报

| 工具 | 调用方式 | 查什么 |
|------|---------|--------|
| QCC | qcc-company MCP | 工商/融资/股东/高管 |
| QCC | qcc-ipr MCP | 专利/软著 |
| QCC | qcc-risk MCP | 诉讼/处罚/异常 |

### E. 通用搜索

| 工具 | 用途 |
|------|------|
| WebSearch | 白皮书/咨询报告/新闻/公司官网 |
| WebFetch | 已知 URL 爬取正文 |

## PDF 提取器选择

| 优先级 | 提取器 | 适用 | 调用 |
|-------|--------|------|------|
| 1 | GROBID | 学术论文 (双栏) | Docker 8070 端口 |
| 2 | Marker | 报告/白皮书 | `python3 scripts/fulltext/marker_extractor.py input.pdf` |
| 3 | pdfplumber | 兜底 | `python3 scripts/fulltext/pdfplumber_extractor.py input.pdf` |

## 搜索规范

1. **审计日志**: 每个搜索查询记录 (查了什么、返回多少、保留多少)
2. **双语搜索**: 同一维度英文一次、中文一次
3. **时间过滤**: 优先近 3 年，seminal paper 不限年份
4. **去重**: DOI + title fuzzy match

## 角色边界（写在每个角色指令开头）

| 角色 | 可以做 | 禁止做 |
|------|--------|--------|
| academic_scout | 搜论文 (S2/OpenAlex/arXiv/DBLP/PMC) | 搜研报/新闻/企业信息/下载全文 |
| industry_scout | 搜研报/报告/新闻 (NeoData/WebSearch) | 搜论文/企业信息 |
| enterprise_scout | 企业尽调 (QCC/SEC/WebSearch) | 搜论文/研报 |
| deep_reader | 读全文+压缩笔记 | 搜新文档 |
| tech_decomposition | 快速预搜+拆解方向 | 下载全文/深度阅读 |
| tech_strategist | 读笔记+分析 | 搜任何外部数据 |
| report_writer | 读分析+写报告 | 搜任何外部数据 |

**通用禁止**: ❌ 所有角色不编造引用

## 脚注引用规范

正文中在数据后面紧跟脚注标记:
```
2024年营收约1.78亿元[^3]，B+轮投后估值14.2亿元[^4]
```

报告末尾展开:
```
[^1]: OpenAlex 元数据 — https://api.openalex.org/works/...
[^2]: NeoData 研报 — 中信证券行业深度报告 (2025-03)
[^3]: BP自述 — 无外部来源URL
```
