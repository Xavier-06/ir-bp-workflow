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
> - ~~Semantic Scholar~~ — 429 Rate Limit（无限挂起，需 API Key）
> - ~~CORE~~ — 无 API Key，脚本直接跳过
>
> ✅ **OpenAlex 已启用**（免费, 无需 Key, https 直连稳定），纳入默认学术源池，与 arXiv/PMC/Crossref/DBLP 并列。

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
| OpenAlex | `python3 scripts/api_clients/openalex_client.py "query" --json` | 综合学术元数据 | 四级领域分类 + OA URL + 引用数，覆盖广 |

**统一搜索（多源并行 + 去重 + 排序）：**
```bash
# CS/AI/Tech 领域
cd {RUNTIME_ROOT} && python3 scripts/search/unified_search.py "关键词" --sources arxiv,dblp,pmc,openalex --max-results 30 --json

# 生物医学领域 — PMC 放第一位
cd {RUNTIME_ROOT} && python3 scripts/search/unified_search.py "关键词" --sources pmc,arxiv,crossref,openalex --max-results 30 --json
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
TYC MCP → 中国企业工商
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
| **IMA 自建研报库** | `ima-mcp.search_knowledge(KB="001a89fa4b807b92", query="关键词")` → `fetch_media_content` | ★主力源：投行/券商研报（GS/MS/JPM/BofA/Citi/UBS 等），全文可 fetch，所有搜索第一优先 |
| **IMA 行研智库** | `ima-mcp.search_knowledge(KB="7311568991699459", query="关键词")` → `fetch_media_content` | 券商行业深度报告（可 fetch 全文） |
| **IMA 精选行业报告** | `ima-mcp.search_knowledge(KB="7302509206984644", query="关键词")` → `fetch_media_content` | 第三方白皮书：艾瑞/头豹/奥纬（可 fetch 全文） |
| **IMA 机构调研纪要** | `ima-mcp.search_knowledge(KB="7300811407257275", query="关键词")` | 外资研报/专家交流（NOTE 可 fetch） |

> v4.8 已删除：长安投研 `7297585010204027` + 公司调研报告 `7302533890465245`（库主禁止导出，仅 200 字摘要，不再路由）。

### D. 企业情报

| 工具 | 调用方式 | 查什么 |
|------|---------|--------|
| **TYC** | tyc-mcp | 工商/融资/股东/高管 (中国企业) |
| **TYC** | tyc-mcp | 专利/软著 |
| **TYC** | tyc-mcp | 诉讼/处罚/异常 |
| **NeoData** | `python3 scripts/search/neodata_search.py "公司名" --data-type all --json` | A股/港股研报+行情估值 |
| **yfinance** | `python3 -c "from scripts.search_gateway import yfinance_summary; yfinance_summary('AAPL')"` | 美股估值快照 (price/PE/PB/market_cap) |
| **SEC EDGAR** | WebFetch | 美股上市公司 10-K/S-1 |
| **中文实时新闻** | `python3 -c "from scripts.search_gateway import tencent_news_search; tencent_news_search('公司名 融资', max_results=5)"` | 中文企业融资/产品/人事动态 (0.7s 最快) |
| **Yahoo Finance** | `python3 -c "from scripts.search_gateway import _yahoo_search; _yahoo_search('NVDA earnings revenue', max_results=5)"` | 美股竞品新闻/earnings/quote |

### E. 通用搜索

| 工具 | 用途 |
|------|------|
| WebSearch | 白皮书/咨询报告/新闻/公司官网 |
| WebFetch | 已知 URL 爬取正文 |

### F. 新闻搜索（中文快讯 + 美股竞品）

> 与 BP 管线一致：中文公司/行业新闻走 tencent_news_search（自动降级NeoData doc），美股竞品新闻/earnings 走 Yahoo Finance。两者都在 `scripts/search_gateway.py`，子代理用 Bash 调用。

**中文实时新闻搜索（tencent_news_search，自动降级NeoData doc）**
```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
result = tencent_news_search('公司名 融资', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```
| 场景 | 查询示例 |
|------|---------|
| 公司融资/投资新闻 | `{公司名} 融资 投资 轮次` |
| 产品发布/合作动态 | `{公司名} 产品 发布 合作 签约` |
| 高管人事变动 | `{公司名} CEO 任命 离职 加入` |
| 行业政策/监管动态 | `{行业名} 政策 监管 新规` |
| 早期公司报道 | `{公司名} 创业 获投` |

- 返回 title + url + content(摘要) + publishedDate(精确到秒) + source(媒体名)
- 优势：0.7s，覆盖 7×24 实时中文新闻；局限：纯中文，不支持结构化金融数据

**Yahoo Finance 搜索（美股新闻 + quote）**
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

**工具优先级（新闻类）**

| 你要查什么 | 首选 | 兜底 |
|-----------|------|------|
| 中文公司/行业新闻（融资/产品/人事/政策） | `tencent_news_search` | NeoData(doc) → WebSearch |
| 美股竞品新闻/earnings | Yahoo `_yahoo_search` | WebSearch |

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

## IMA 知识库使用指南（ima-mcp，全部角色可用，academic_scout 除外）

**IMA 与结构化源（westock/NeoData）并行执行，不是兜底。** IMA 提供 web 搜索找不到的机构内部视角——券商电话会议速记、专家交流纪要、外资内部研报、上市公司投关记录原文、第三方行业白皮书。

**调用方式（MCP 工具直接调用，connectorIds 已授权）：**

**模式 A：可 fetch 全文（行研智库 / 精选行业报告 / 机构调研纪要 NOTE）**
```
ima-mcp.search_knowledge(knowledge_base_id="库ID", query="搜索词")
→ 取最相关 1-3 篇结果的 media_id（多源交叉验证）
→ ima-mcp.fetch_media_content(media_id="...")  # 读全文
```

**模式 B：仅搜索摘要（长安投研 / 公司调研报告 — 库主禁止导出）**
```
ima-mcp.search_knowledge(knowledge_base_id="库ID", query="搜索词")
→ 直接使用 introduction 字段（200-500字结构化摘要）
```

**⚠️ 长安投研必须加 TXT 过滤**（否则被 `_导读.docx` 淹没）：
```
filters: [{"filter_type": "MEDIA_TYPE_FILTER_TYPE", "media_type_filter": {"media_type": ["TXT"]}}]
```

**知识库 ID 速查（v4.8）：**
- ★ 自建研报库: `001a89fa4b807b92`（主力源，投行/券商研报全文可取，所有搜索第一优先）
- 行研智库: `7311568991699459`（券商行业深度，可 fetch）
- 机构调研纪要: `7300811407257275`（外资/专家交流，NOTE 可 fetch）
- 精选行业报告: `7302509206984644`（第三方白皮书，可 fetch）

**来源标注：** `[^N]: IMA {库名} —《{标题}》({日期})`

**搜索纪律：**
- 每库最多取 top 5 结果，全文提取最多 3 篇（可 fetch 的库：行研智库/精选报告/机构纪要 NOTE）
- 搜不到直接跳过，不硬凑
- industry_scout / tech_decomposition / tech_strategist 角色**必须**搜 IMA 行研智库+精选报告（行业研报首选）
- enterprise_scout 角色搜 IMA 公司调研报告+长安投研（企业机构视角）

## 角色边界（写在每个角色指令开头）

| 角色 | 可以做 | 禁止做 |
|------|--------|--------|
| academic_scout | 搜论文 (arXiv/DBLP/PMC/Crossref, 4源必用) | 搜研报/新闻/企业信息/下载全文/IMA |
| industry_scout | 搜研报/报告/新闻/板块/产业链/机构评级 (NeoData/westock-mcp/中文实时新闻/Yahoo/WebSearch) + **IMA 自建研报库/行研智库/精选报告/机构纪要** | 搜论文/企业信息 |
| enterprise_scout | 企业尽调 (TYC + NeoData/yfinance + 中文实时新闻 + Yahoo + SEC/WebSearch + **westock-mcp 板块/产业链/机构评级/资金流** + **IMA 自建研报库/机构纪要**) | 搜论文/研报 |
| deep_reader | 读全文+压缩笔记 + **IMA 行研智库 fetch 全文（按需补充行业背景）** | 搜新文档 |
| tech_decomposition | 快速预搜+拆解方向 + **IMA 行研智库/精选报告（行业研报首选）** | 下载全文/深度阅读 |
| tech_strategist | 读笔记+分析 + **IMA 自建研报库/机构纪要（按需补充机构观点）** | 搜任何外部数据（IMA 按需除外） |
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
