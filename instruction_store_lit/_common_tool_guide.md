# 全局工具指南 (子代理工具箱架构)

> 本文件所有角色共享。你的角色专属指令会告诉你**该用哪些**，本文件告诉你**怎么用**。

## ⚠️ 工具限制 (所有角色)

你没有 Glob/Grep 工具。
- 搜索文件 → `Bash: find {path} -name "*.json"`
- 读取文件 → `Read`
- 搜索内容 → `Bash: grep -r "keyword" {path}`

## 子代理工具箱 (按维度分组，非降级链)

### A. 学术元数据 & 引用网络

> ⚠️ 以下数据源已确认不可用，**禁止调用**：
> - ~~OpenAlex~~ — 503 Service Unavailable（匿名限流，需 API Key）
> - ~~Semantic Scholar~~ — 429 Rate Limit（无限挂起，需 API Key）
> - ~~CORE~~ — 无 API Key，脚本直接跳过

> ⚠️⚠️ **领域判定**：搜索前必须先读 `research_plan.json` 的 `domain` 字段或关键词信号判定领域。
> - **生物医学** (biomedical/clinical/pharma/oncology/genomics/drug/cell therapy/CRISPR/protein/antibody/neuro/cardio/vaccine/mRNA/lipid nanoparticle) → PMC 为 **PRIMARY** 搜索源，排第一
> - **CS/AI/Tech** → arXiv 为主，PMC 补充
> - **交叉领域** (biomedical AI/biomaterials/digital health) → PMC 为 **PRIMARY**

| 工具 | 调用方式 | 查什么 | 不可替代价值 |
|------|---------|--------|------------|
| **PMC** | `python3 scripts/api_clients/pmc_client.py "query" --json` | 生物医学论文搜索 | 生物医学 **PRIMARY** + OA 全文 XML |
| **PMC EFetch** | `python3 scripts/api_clients/pmc_client.py --pmc-id 12345678` | PMC 全文 XML 获取 | 生物医学全文获取核心 |
| Crossref | `python3 scripts/api_clients/crossref_client.py "doi" --json` | DOI+参考文献+基金 | DOI 权威元数据 + 引用链 |
| DBLP | `python3 scripts/api_clients/dblp_client.py "query" --json` | CS 论文补充 | CS 会议覆盖 |
| arXiv | `python3 scripts/api_clients/arxiv_client.py "query" --json` | 预印本 | 预印本首发 + PDF 直下 |

**统一搜索（多源并行 + 去重 + 排序）：**
```bash
# CS/AI/Tech 领域
cd {RUNTIME_ROOT} && python3 scripts/search/unified_search.py "关键词" --sources arxiv,dblp,pmc --max-results 30 --json

# 生物医学领域 — PMC 放第一位
cd {RUNTIME_ROOT} && python3 scripts/search/unified_search.py "关键词" --sources pmc,arxiv,crossref --max-results 30 --json
```

### B. 论文全文获取 (按文档类型路由)

> ⚠️⚠️ **metadata 参数必须传** — `pdf_downloader.py` 的路由逻辑完全依赖 `--metadata` 中的标识符字段。
> 不传 metadata = 路由无法工作 = 拿不到全文。

**路径 A: 学术论文**

pdf_downloader 按以下优先级路由，**你需要从 fact_store.json 中读取对应字段填入 metadata**：

| 优先级 | 路由 | metadata 必需字段 | 下载方式 |
|--------|------|------------------|---------|
| A1 | arXiv PDF 直下 | `arxiv_id` | `https://arxiv.org/pdf/{arxiv_id}.pdf` |
| A2 | OA URL 直下 | `open_access_pdf_url` | 直接下载 PDF |
| A3 | DOI → Unpaywall | `doi` | Unpaywall API 查 OA PDF URL |
| A4 | PMC 全文 | `pmc_id` | `https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/pdf/` |

**不同数据源的 metadata 传法**：

```bash
# arXiv 论文 — 从 facts.json 读 arxiv_id
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id DOC-001 --type paper \
  --metadata '{"arxiv_id":"2108.10150"}' --json

# PMC 论文 (生物医学) — 从 facts.json 读 pmc_id
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id DOC-002 --type paper \
  --metadata '{"pmc_id":"12345678"}' --json

# 有 DOI 的论文 — 走 Unpaywall OA 查找
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id DOC-003 --type paper \
  --metadata '{"doi":"10.1016/j.cossms.2022.101002"}' --json

# 有 OA URL 的论文 — 直接下载
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id DOC-004 --type paper \
  --metadata '{"open_access_pdf_url":"https://example.com/paper.pdf"}' --json

# 多字段组合 (推荐，按优先级自动选最快的)
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id DOC-005 --type paper \
  --metadata '{"arxiv_id":"2108.10150","doi":"10.xxx","pmc_id":"12345","open_access_pdf_url":"..."}' --json
```

**关键**：从 fact_store.json 读每篇论文时，把 `arxiv_id`、`pmc_id`、`doi`、`open_access_pdf_url` 全部塞进 `--metadata`，pdf_downloader 会自动按优先级选最优路径。

**路径 A-bis: PMC 全文 XML (生物医学首选)**
```bash
# 生物医学论文优先用 PMC EFetch 拿全文 XML（比 PDF 提取质量高得多）
cd {RUNTIME_ROOT} && python3 scripts/api_clients/pmc_client.py --pmc-id 12345678
```

**路径 B: 券商研报**
```bash
cd {RUNTIME_ROOT} && python3 scripts/search/neodata_search.py "固态电池 行业研报" --data-type all --json
# 或 westock-data MCP
```

**路径 C: 行业报告/白皮书**
```bash
# 行业报告 — 传 url 字段
cd {RUNTIME_ROOT} && python3 scripts/fulltext/pdf_downloader.py \
  --fact-id IND-010 --type industry_report \
  --metadata '{"url":"https://example.com/report.pdf"}' --json
```

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

> ⚠️ GROBID 已确认不可用（Docker 未运行），不要尝试调用。

| 优先级 | 提取器 | 适用 | 调用 |
|-------|--------|------|------|
| 1 | Marker | 报告/白皮书 | `python3 scripts/fulltext/marker_extractor.py input.pdf` |
| 2 | pdfplumber | 兜底 | `python3 scripts/fulltext/pdfplumber_extractor.py input.pdf` |

## 搜索规范

1. **审计日志**: 每个搜索查询记录 (查了什么、返回多少、保留多少)
2. **双语搜索**: 同一维度英文一次、中文一次
3. **时间过滤**: 优先近 3 年，seminal paper 不限年份
4. **去重**: DOI + title fuzzy match

## 角色边界（写在每个角色指令开头）

| 角色 | 可以做 | 禁止做 |
|------|--------|--------|
| academic_scout | 搜论文 (arXiv/DBLP/PMC/Crossref) | 搜研报/新闻/企业信息/下载全文 |
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
[^1]: arXiv 元数据 — https://arxiv.org/abs/...
[^2]: NeoData 研报 — 中信证券行业深度报告 (2025-03)
[^3]: BP自述 — 无外部来源URL
```
