

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
[^4]: NeoData 金融数据 — neodata_search
```

### 脚注来源优先级
1. **外部 URL**（web_search / search_gateway 返回的 URL）→ 写完整 URL
2. **NeoData 金融数据** → 写 `NeoData 金融数据 — neodata_search`
3. **公司年报/公告** → 写 `公司年报/公告 — URL`
4. **yfinance** → 写 `yfinance — yfinance.Ticker()`

### ⚠️ 铁律
- **facts JSON 的 source_url 和 MD 正文的 [^N] 脚注必须对应**
- **禁止只在 facts JSON 里写 URL 而不在 MD 正文写脚注**——统稿子代理依赖你 MD 中的脚注标记
- **禁止只写内部文件名**：❌ `[^N]: step1_data.md`
- **每条脚注必须有真实来源标注**，不能编造 URL

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
# data_type='doc' — 券商研报/行业深度报告
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import neodata_search
result = neodata_search('行业名 行业深度报告 市场规模 竞争格局', data_type='doc')
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

### 2. 通用网络搜索（新闻、政策、行业动态）

```bash
cd {RUNTIME_ROOT} && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import search
results = search('关键词 搜索内容', prefer='auto')
for r in results[:5]:
    print(r.get('title',''), r.get('url',''), r.get('snippet','')[:100])
"
```

### 3. 工商信息（天眼查 MCP）

使用天眼查 MCP 查询公司注册信息、股东结构、董监高、司法风险等：
- `mcp__qcc-company__get_company_by_query` — 按名称搜索公司
- `mcp__qcc-company__get_shareholder_info` — 股东信息
- `mcp__qcc-company__get_key_personnel` — 董监高
- `mcp__qcc-risk__get_risk_scan` — 风险扫描

### 4. yfinance（精确估值数据）

```bash
# ⚠️ 必须用 /opt/anaconda3/bin/python3
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

### 5. 预计算数据（Phase07 输出，优先使用）

```bash
# 财务指标
cat {TASK_DIR}/{JOB_ID}_precompute_financial_metrics.json | python3 -m json.tool

# 行业对标
cat {TASK_DIR}/{JOB_ID}_precompute_sector_benchmarks.json | python3 -m json.tool
```

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
      "confidence": "high"
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

---

## 🔒 文件操作规范

- **禁止使用 Glob/Grep 工具**。搜索文件用 Bash（`find`/`ls`），搜索内容用 Bash（`grep`）
- **不要调用 Glob 或 Grep**——这两个工具在你的环境中不存在
- 读文件用 Read 工具
- 写文件用 Write 工具
