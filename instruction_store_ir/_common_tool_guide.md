
# IR 工具使用作业手册（v2.0 · 目标驱动）

> **本手册的正确用法**：不是从头读到尾，而是**先定交付物 → 倒推数据点 → 照路由表查数据源**。下面的路由矩阵是字典，不是小说。

## 0. 作业纪律（规则前置 · 所有搜索前必读）

### 目标驱动倒推（先定交付物，再倒推搜索）
1. 读你的 step 指令里的「交付物清单」——那是你必须交的表/结论
2. 对每张表，列出需要哪些**具体数据点**（如"TAM 出货量口径 → 需要：行业总出货量 × ASP"）
3. 对每个数据点，查下方路由矩阵选数据源
4. **禁止漫无目的搜索**——没有对应交付物的数据不搜

### 搜索预算与停止纪律
- **每个数据点最多 3 轮补搜**。3 轮仍无 → 标"经 3 次搜索未找到独立来源"，写进 data_gaps，继续下一项
- **结构化源优先**：westock-mcp / NeoData / 天眼查 / IMA 能拿到的，不用 search_deep(Bash) 兜底
- **IMA 研报库（001a89fa4b807b92）是所有搜索第一优先**——投行研报全文可 fetch，含估值方法论/BOM 成本/目标价推导
- **时效硬规则**：估值/共识类数据优先取 ≤30 天内来源；超 30 天标注"可能过时"

### 数据源选择决策树（3 秒判断）
```
要查什么？
├─ 实时行情/财务/评级/研报/板块/产业链/资金流 → westock-mcp（MCP 直调）
├─ A/HK 结构化财务数字（市值/PE/营收/利润）→ NeoData api
├─ 券商研报深度/行业分析/产品动态 → NeoData doc + IMA 研报库
├─ 美股行情/估值/英文新闻 → yfinance + _yahoo_search
├─ 中文突发新闻/实时动态 → tencent_news_search
├─ 工商/股东/司法/专利 → 天眼查 MCP
├─ 机构研报全文/估值方法论/BOM 拆解 → IMA（001a89fa4b807b92）fetch
└─ 以上都不覆盖 → search_deep(Bash)（最多 3 轮）
```

---

## 🎯 数据源精确路由（按查询类型选数据源，禁止混用）

**不同类型的查询必须使用对应的数据源，禁止只用 web_search 做所有搜索。**

### 路由矩阵

| 查什么 | 首选数据源 | 调用方式 | 兜底 |
|--------|-----------|---------|------|
| **A/HK/美股实时行情/财务/券商研报/板块·产业链/资金流/北向/评级/新闻** | **腾讯自选股 MCP (westock-mcp)** | `mcp__westock-mcp__data_quote` / `data_finance` / `data_report` / `data_sector` / `data_industry_chain` / `data_fund_flow` / `data_north_holding` / `data_rating` / `data_news` | NeoData → search_deep |
| A/HK 股行情/财报/估值 | NeoData `api` | `neodata_search('公司名 营收 净利润 市值', data_type='api')` | yfinance → search_deep |
| 美股行情/估值/分红 | yfinance | `yf.Ticker('AAPL').info` | NeoData → search_deep |
| **美股英文新闻/earnings/分析师** | **Yahoo Finance `_yahoo_search`** | `_yahoo_search('NVDA earnings AI chip', max_results=5)` | search_deep |
| **券商研报/行业深度/产业链** | **NeoData `doc` + 腾讯自选股 `data_report`/`data_sector`/`data_industry_chain`** | `neodata_search('公司名 最新动态', data_type='doc')`；行业/产业链数据优先 `mcp__westock-mcp__data_sector` / `data_industry_chain` / `data_report` | search_deep |
| **突发新闻/实时动态（中文）** | **中文实时新闻 `tencent_news_search`**（CLI 积分耗尽自动降级 NeoData doc） | `cd {RUNTIME_ROOT} && python3 -c "from scripts.search_gateway import tencent_news_search; ..."` | search_deep |
| **上市公司公告/新闻/研报动态** | **腾讯自选股 `data_news`**（需 symbol，type: 0公告 1研报 2新闻 3全部） | `mcp__westock-mcp__data_news(symbol="sh600519", type=3, limit=10)` | tencent_news_search → search_deep |
| **产品发布/技术动态/新闻分析** | NeoData `doc` + tencent_news_search 补充 | NeoData doc 拿深度分析，tencent_news_search 补实时动态 | search_deep |
| 企业工商/股东/司法/专利 | 天眼查 MCP | `mcp__tyc-mcp__search_companies` → `call_tool` | search_deep |
| 技术论文/arxiv | search_deep(Bash, "arxiv {company} {technology} {YYYY}", fetch_top_n) | 搜索+自动抓论文页正文 | — |
| 开源项目/GitHub/HF | search_deep(Bash, "github.com/{company} latest release {YYYY}", fetch_top_n) | 搜索+自动抓 README | — |
| 网页正文深度阅读 | search_deep(Bash, fetch_top_n) | 给关键词或 URL，自动抓 top N 正文 | — |

> ⚠️ **westock-mcp（腾讯自选股）是已授权的 MCP connector，子代理可直接调用，无需 bash。行业数据、行情、财务、研报、板块、产业链、资金流、选股类查询必须优先走该结构化源，禁止只用 search_deep(Bash) 兜底。**

### NeoData 能力细分

- `data_type='api'` → 行情/财报/估值等**结构化数字**（市值、PE、营收、利润等精确数字）
- `data_type='doc'` → **券商研报 + 行业新闻分析 + 产品动态报道**（内容深度高，平均 200-500 字/条，来源经过过滤，质量远优于 search_deep snippet）
  - ✅ 能搜到：产品发布（如混元HY3）、行业动态、券商深度、公司战略分析、管理层变动报道
  - ⚠️ `publishedDate` 字段经常为空 → 必须从 content 中提取时间线索（"4月23日"、"2026年Q1"等）
  - **作为新闻/分析的首选数据源**，search_deep(Bash) 用于补充 NeoData 未覆盖的突发新闻和昨日动态
- `data_type='all'` → 两者都取（搜索量更大但混合返回）

### yfinance + Yahoo Finance 能力边界

- **yfinance 能拿**: 实时行情、市值、PE/PB/PS、EPS、股息率、beta、52 周高低、财务摘要
- **Yahoo Finance `_yahoo_search` 能拿**: **美股英文新闻、earnings calls、分析师评级变动、产品动态**（免费无需 API key）
- **不能拿**: A 股数据、中文新闻（中文新闻用 `tencent_news_search`，自动降级 NeoData doc）
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
- **注意**: 工商数据可能滞后 1-3 个月，重大变更需 search_deep(Bash) 交叉验证
- 已在 manifest 中配置天眼查 MCP，可直接调用

### ⚠️ 宏观与大宗数据取数纪律（v3.6 起，所有消费方必读）

**v3.6 起管线不再设独立宏观子代理**——利率/汇率/大宗商品/宏观指标数据由**消费它的 step 自行取数**（谁用谁取，取数结果写进自己的产出并标注来源+日期）。禁止引用研报里的旧宏观数字当现值。

**第一步：动态识别标的需要哪些宏观/大宗变量（禁止套固定清单）**
- 成本敏感型标的（制造/资源/材料密集型）：从 step2_biz 的业务描述、年报成本拆分、research_plan 的 `key_debates` 中**提取该标的成本结构里占比最大的原材料**，逐个取实时价格——不是所有公司都要查铜价/油价
- 估值/折现需求：凡是要算 WACC/DDM/股息率锚的，必须取**当前**无风险利率（标的本币计价国债收益率）
- 汇率敞口：海外收入占比显著的标的才取对应汇率

**第二步：按类型路由取数**

| 数据类型 | 首选 | 次选 | 兜底 |
|---------|------|------|------|
| 宏观指标（PMI/CPI/LPR/社融/国债收益率） | westock-mcp `data_macro` | NeoData（`neodata_search('<指标名> 最新数据', data_type='api')`） | search_deep(Bash) 搜央行/国家统计局官网 |
| 大宗原材料价格（现货/期货） | westock-mcp `data_futures`（合约代码按识别出的材料自定） | NeoData（`neodata_search('<材料名> 现货价格 最新', data_type='api')`） | search_deep(Bash) 搜"<材料名> 现货价格 {year}" |
| 汇率 | yfinance（如 `CNY=X`） | NeoData | search_deep(Bash) |
| 海外宏观（美股标的） | NeoData（Fed/BLS 数据） | search_deep(Bash) | — |

**时效与置信度纪律（硬性）**：
- 每个宏观/大宗数字必须带发布日期；发布超过 60 天 → 标"⚠ 数据滞后 X 天"并重搜"{指标} 最新 {year}"
- 单一来源、无法交叉验证 → 置信度最高记 medium（high 需 ≥2 独立来源）
- 政策引用必须验证现行有效（"待实施"与"已生效"严格区分）
- 取数失败不得编造——记入 data_gaps 并注明"缺实时数据，以 X 口径近似"

### ⚠️ 信息丰富度评级响应协议（A/B/C，所有 step 强制）

**核心洞察：资料多 ≠ 确定性高，AI 输出的置信度 ≠ 投资的真实确定性。**

phase04 研究计划会对标的做"信息丰富度评级"（见 research_plan 的 `information_richness` 字段），决定你该用什么姿势研究。活派发 prompt 里会告诉你本标的评级，按以下响应执行：

| 评级 | 特征 | AI 研究陷阱 | 你的响应策略 |
|------|------|------------|------------|
| **A 级**（信息充裕） | 大行覆盖多、研报密集、媒体铺天盖地 | 共识过强，输出趋同市场定价，通篇"正确的废话" | **重点做反面检验**：聪明人为什么不买？被市场忽略的风险/事实是什么？你的增量观点必须明确回答"我与共识哪里不同"——没有分歧就不写 |
| **B 级**（信息适中） | 覆盖有限、部分数据需推算（如分部数据缺失、需从产能/单价反推） | 用"合理推测"填补空白，看起来完整实则虚假确定性 | **每个推算数据标注置信度**：区分"有据推算"（写清推算链和输入）与"凭空填充"（禁止）。推算数字进 facts 时 confidence ≤ medium |
| **C 级**（信息稀缺） | 冷门/新上市/次新股、几乎无机构覆盖 | 资料不足导致过度保守，误判"看不清 = 不好"，或干脆编故事填空白 | **第一性原理提问**，从有限信息中提取商业本质：①客户是谁？为什么付钱？有替代选择吗？②复购靠什么驱动？③竞争对手拿 100 亿能复制这门生意吗？④管理层做过什么关键决策，反映了什么判断力？**宁可留白标注"数据不足"，也不用推测伪装确定性** |

**评级响应纪律**：
- 你的产出开头（MD 报告首段）必须用一行声明信息丰富度评级与它对你本次分析的影响（如"B 级：分部收入为作者推算，置信度 medium"）
- A 级标的禁止产出与市场一致观点雷同的"资料汇编"——至少给出 1 个反共识检验结论（哪怕是"检验后确认共识成立，理由是 X"）
- C 级标的的 data_gaps 必须列出"需要一手验证的问题清单"（田野调查/产品体验/产业链访谈方向），交给下游判断者
- 评级缺失时按 B 级处理（保守默认）

### ⚠️ 数据源不可达必须显式声明降级（禁止伪装）

**某数据源（westock/NeoData/IMA/search_deep）调用失败、超时或返回空时，禁止用记忆或推测冒充查询结果**。必须在该 step 报告顶部醒目标注：

```
⚠️ 数据源降级声明：{源名} 不可达（{错误}），以下 {数据类别} 基于 {替代源/训练知识}，置信度降级
```

并把受影响的数据点全部记入 data_gaps。宁可留白标注"数据不足"，也不要用推测填满框架伪装确定性——**AI 输出的置信度 ≠ 投资的真实确定性**。

### ⚠️ 数据冲突处理协议（多源数据打架时怎么办，所有 step 强制）

**每个关键数据点应至少 2 个独立来源交叉验证**（high 置信的硬条件）。当两个来源给出不同数字时，按以下协议处理，禁止"挑一个顺眼的用"。

**第一步：确定主源（冲突时以主源为准）**

| 数据类别 | 主源 | 副源 |
|---------|------|------|
| 实时行情/市值/总股本 | westock-mcp `data_quote` | NeoData api / yfinance |
| 财务报表数字（营收/净利/毛利率） | NeoData api / westock `data_finance` | IMA 研报全文 / 公司公告原文 |
| 行业规模/市占率/TAM | IMA 研报全文（投行口径） | NeoData doc / search_deep |
| 管理层承诺/战略/订单 | 公司公告原文（一手） | IMA 调研纪要 / NeoData doc |
| 宏观/大宗价格 | westock `data_futures`/`data_macro` | NeoData api |

**第二步：误差分级处理**

```
误差率 = |主源 - 副源| / 主源 × 100%
```

| 误差 | 处理 |
|------|------|
| ≤ 1% | ✅ 一致——取主源值，facts 中 source_tier 可记 official/high |
| 1% ~ 5% | ⚠️ 口径差异——正文双值并列注明各自来源与可能原因（见下方口径差异清单），facts 的 confidence 降为 medium |
| > 5% | ❌ 重大差异——必须查一手来源（公司公告/年报原文）核实后才能使用；无法核实时该数据点置信度强制记 low 并写进 data_gaps |

**第三步：先排查口径差异，再判定"数据错误"**

两源不一致时**最常见原因不是谁错了，而是口径不同**——先按下表对号：

| 口径差异 | 典型场景 |
|---------|---------|
| 归母 vs 扣非净利润 | A 股研报常用扣非，行情源给归母 |
| GAAP vs Non-GAAP | 美股/港股研报常用 Non-GAAP（剔除股权激励/一次性项） |
| 并表范围 | 是否含少数股东损益、是否含并表子公司 |
| 汇率与折算时点 | 港币/人民币/美元，不同源折算时点不同 |
| 财年定义 | 自然年 vs 财年（如阿里财年 3 月结束） |
| 更新滞后 | 某平台尚未更新最新一期财报 |
| 市值口径 | 实时市值 vs 研报发布日市值（研报写死时点值，对比实时值必然漂移） |

> 排查后若确认是口径差异，按 1%-5% 档双值并列即可，不算数据错误；若同口径仍差 >5%，按重大差异处理。

**第四步：市值必须手算验算（防单位错误）**

凡正文出现市值，必须用 `股价 × 总股本` 验算一遍并与引用值比对——防止"港币亿 vs 人民币亿"少写/多写一个零。有偏差用数值验算工具确认（见 step3/step6 指令中的 `ir_financial_rigor.py`）。

### 腾讯自选股 MCP (westock-mcp) 能力边界

- **能拿（IR 最核心的结构化金融源）**: A/HK/美股实时行情、财务数据、券商研报、板块/行业、产业链、资金流、北向持仓、评级/评分、新闻、分红、IPO、选股/排名/策略
- **不能拿**: 深度新闻分析正文（用 NeoData doc 补）、工商司法（用天眼查）
- **工具前缀**: `mcp__westock-mcp__*`
  - 行情: `data_quote`；财务: `data_finance`；研报: `data_report`；板块: `data_sector`；产业链: `data_industry_chain`；资金流: `data_fund_flow`；北向持仓: `data_north_holding`；评级: `data_rating`；评分: `data_score`；新闻: `data_news`；K线: `data_kline`；宏观: `data_macro`；股东: `data_shareholder`
  - 选股/筛选: `tool_filter` / `tool_ranking` / `tool_strategy`
- **用法**: 直接在子代理会话里调用 MCP 工具，无需 bash。行业规模/竞争格局/产业链/资金面/北向/机构评级等数据优先用对应工具，不要只靠 search_deep(Bash)。
- 已在 manifest 中配置腾讯自选股 MCP，可直接调用

### IMA 知识库 MCP (ima-mcp) — 机构级研报/纪要/外资研报

**12万+机构级文档，公开 web 搜不到的增量信息。已授权 MCP connector，子代理可直接调用。**

#### 可用知识库目录（v4.8，Xavier 研报库为主力源）

| KB ID | 名称 | 内容特色 | fetch | 最佳使用场景 |
|-------|------|---------|-------|-------------|
| `001a89fa4b807b92` | ★Xavier 研报库（用户主力库，25991 篇） | 用户亲自收集的投行/券商研报（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等），按周分文件夹 | ✅ **全文** | 估值方法论/目标价/BOM 成本/行业深度，**所有搜索第一优先** |
| `7311568991699459` | 行业研究报告库(行研智库) | 行研报告（新能源/AI/消费/医药/金融/地产）3786篇 | ✅ 全文 | 行业趋势、政策解读、TAM/竞争格局 |
| `7300811407257275` | 机构调研纪要/外资研报库 | 机构调研、电话会议、专家交流、外资研报 33331篇 | ✅ NOTE 可 | 机构视角、外资观点、电话会纪要、专家交流 |
| `7302509206984644` | 精选行业数据报告 | 精选精品报告 1442篇 | ✅ 全文 | 高质量筛选的行业/公司报告 |

> v4.8 已删除：长安投研 `7297585010204027` + 公司调研报告 `7302533890465245`（库主禁止导出，仅 200 字摘要，不再路由）。

#### 调用方式（4 个库全文均可 fetch，统一模式 A）
```
# Step 1: 语义搜索 → 拿到 media_id + introduction 摘要
mcp__ima-mcp__search_knowledge(knowledge_base_id="库ID", query="搜索词")
# Step 2: 全文提取 → 取最相关 5-8 篇结果的 media_id（放开拉，宁多勿少，多源交叉验证）
mcp__ima-mcp__fetch_media_content(media_id="搜索结果中的 media_id")  # 逐篇 fetch
```
> 机构调研纪要库若某条返回 `can_fetch_content=false`（非 NOTE 类型），退而用 introduction 摘要；其余 3 库直接 fetch 全文。

**⚠️ 时间过滤纪律（Xavier 研报库重要）：**
- 只拉最近 3 个月内的投行研报（超 3 个月参考意义不大，直接跳过）
- 标题常含日期（如 `GS-人形机器人-260703.pdf`=2026-07-03），据此判断时效
- 大行研报优先（GS/MS/JPM/BofA/Citi/UBS/Bernstein）

**get_knowledge_list — 列出文档（按时间/标题排序）：**
```
mcp__ima-mcp__get_knowledge_list(
  knowledge_base_id="001a89fa4b807b92",
  limit=20,
  sort_type="UPDATE_TS_DESC_SORT_TYPE"
)
```

#### 什么时候该搜 IMA（优先级定位）

| 场景 | 首选 KB | 为什么搜 IMA 而不是 NeoData/web |
|------|---------|-------------------------------|
| **行业深度/竞争格局/TAM** | Xavier 研报库 + 行研智库 | 投行行研报告比 web 深度高 10 倍，比 NeoData doc 覆盖广 |
| **公司基本面/估值/目标价** | Xavier 研报库 | 投行研报含目标价方法论、盈利预测和估值逻辑（全文可取） |
| **机构观点/市场共识** | Xavier 研报库 + 机构调研纪要 | 电话会纪要、专家交流是公开源搜不到的 |
| **外资视角/跨境对比** | Xavier 研报库 | GS/MS/JPM/UBS 等外资券商研报独立视角 |
| **产业链上下游调研** | Xavier 研报库 + 行研智库 | 投行产业专家的一线调研纪要 |
| **政策/监管解读** | 行研智库 | 机构级政策解读报告 |

#### 搜索策略建议

1. **Step1（数据收集）**：必搜Xavier 研报库 + 机构调研纪要，用 "{公司名}" 和 "{行业名}" 各搜一轮，建立机构信息基线
2. **Step2（行业分析）**：重点搜Xavier 研报库 + 行研智库，用行业关键词 + 竞争格局 + TAM
3. **Step3（商业模式）**：搜Xavier 研报库，关注客户/订单/收入结构
4. **Step4（财务分析）**：搜Xavier 研报库，关注盈利预测和估值逻辑
5. **Step6b（估值）**：搜Xavier 研报库，看外资估值方法和可比公司
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
[^15]: IMA知识库 — Xavier 研报库 — "Goldman Sachs: 人形机器人产业链深度，BOM成本拆解与量产指引" (2026-07-21)
[^16]: IMA知识库 — 机构调研纪要 — "某头部锂电企业专家交流纪要：固态电池量产时间表" (2026-07-21)
```

#### ⚠️ IMA 使用纪律

- **Xavier 研报库优先**：所有搜索第一优先搜 `001a89fa4b807b92`（投行研报全文可取），命中不足再补订阅库
- **中英双语搜索（2026-08-05 实测新增，违反 = 漏掉最值钱的外资研报）**：IMA 检索偏关键词匹配、跨语言能力极弱——实测中文 query 命中的全是中文标题研报（含译成中文标题的伯恩斯坦/交银国际），英文 query 命中的全是英文原标题外资大行（Goldman Sachs-/Morgan Stanley-/JPMorgan-/UBS- 开头），两组结果**几乎零重叠**。因此 Xavier 研报库每次必搜两轮：
  ```
  # 第 1 轮中文
  search_knowledge(knowledge_base_id="001a89fa4b807b92", query="{中文公司名} {中文行业} 目标价 估值 研报")
  # 第 2 轮英文
  search_knowledge(knowledge_base_id="001a89fa4b807b92", query="{公司英文名或ticker} {行业英文术语} Goldman Sachs Morgan Stanley JPMorgan")
  ```
  两轮合并去重后再取 5-8 篇 fetch。公司英文名取自 web 搜索/Yahoo Finance。机构调研纪要含外资内容，建议同样中英各一轮；行研智库/精选报告中文为主可只搜中文。
- **不要只用一个 KB**：4 个 KB 内容互补，同一查询应搜 2-3 个相关 KB
- **全文提取**：search 命中后取最相关 5-8 篇 media_id → fetch_media_content 读全文（Xavier 研报库/行研智库/精选报告均可 fetch）
- **搜索结果可能很长**：提取关键数据点和结论即可，不要全文照搬
- **时间过滤**：只拉最近 3 个月内的投行研报（超 3 个月参考意义不大，直接跳过），大行优先
- **脚注必须标注 KB 名称和文档标题**：方便统稿子代理追溯来源
- **已在 manifest 中配置 ima-mcp**：子代理可直接调用 MCP 工具，无需 bash

---

## ⏰ 数据时效性硬要求（防止引用过期数据，所有 step 强制）

### 时效锚定协议（每次搜索前必须执行，不可跳过）

**第零轮搜索 — 必须在所有广度搜索之前执行：**

```
1. search_deep(Bash, "{entity} {YYYY}年{M}月 最新动态")
2. search_deep(Bash, "{entity} latest news {YYYY}")
3. search_deep(Bash, "{product} 最新版本 发布 {YYYY}") — 如果涉及具体产品/技术
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
1. **搜索**: `search_deep(Bash, "{product} latest version release date {YYYY}")`
2. **搜索**: `search_deep(Bash, "{company} {product} {YYYY}年{M}月 发布")`
3. **确认引用的是最新版本**，如有更新版本必须用最新数据
4. **禁止引用已淘汰/被替代的旧版本而不标注**

**示例**：分析腾讯 AI 能力时，必须搜 `腾讯 混元 最新模型 2026年7月`，如果最新是 HY3 就绝不能引用 HY1。

### 新闻/动态类搜索的时效控制

- search_deep(Bash) 查询关键词**必须包含当前年月**（如 `2026年7月`）
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
1. **外部 URL**（search_deep / search_gateway 返回的 URL）→ 写完整 URL + 发布日期
2. **NeoData 金融数据** → 写 `NeoData 金融数据 — neodata_search (查询日期)`
3. **公司年报/公告** → 写 `公司年报/公告 — URL (发布日期)`
4. **yfinance** → 写 `yfinance — yfinance.Ticker() (查询日期)`
5. **天眼查** → 写 `天眼查 — mcp__tyc-mcp (查询日期)`

### ⚠️ 铁律
- **facts JSON 的 source_url 和 MD 正文的 [^N] 脚注必须对应**
- **禁止只在 facts JSON 里写 URL 而不在 MD 正文写脚注**——统稿子代理依赖你 MD 中的脚注标记
- **禁止只写内部文件名**：❌ `[^N]: step1_industry.md`
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
# ⚠️ 这是新闻/分析的首选数据源（内容深度远超 search_deep snippet）
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
**NeoData doc + search_deep 组合拳**：NeoData doc 拿深度分析 → search_deep 补昨日突发新闻和实时动态。

### 2. 中文实时新闻（tencent_news_search — 突发新闻/实时动态）

**`tencent_news_search` 是中文突发新闻的首选数据源**。⚠️ v4.8.1（2026-07-27）：腾讯新闻 API 积分耗尽，该函数已改为 **CLI 优先 → 失败/空结果自动降级 NeoData doc**，对调用方透明，返回格式不变（`source` 字段标记为 `tencent_news:neodata_fallback`）。

```bash
# 通过 search_gateway 调用（自动降级，推荐）
cd {RUNTIME_ROOT} && python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
import json
print(json.dumps(tencent_news_search('腾讯 混元 大模型', max_results=5), ensure_ascii=False, indent=2))
"
```

- 返回结果包含：标题、摘要（100-500字）、来源媒体、发布时间、原文链接
- ⚠️ **不要直接调 CLI 脚本**（`run-cli.sh` 硬编码路径已失效且积分耗尽）；统一走上面的 `tencent_news_search`，让 gateway 处理降级

**使用场景**:
- 第零轮时效锚定：`tencent_news_search('{entity} 最新动态', max_results=5)`
- 产品发布验证：`tencent_news_search('{company} {product} 发布', max_results=5)`
- 突发新闻：`tencent_news_search('关键词', max_results=5)`

**中文实时新闻 + NeoData doc + search_deep 组合拳**：
- tencent_news_search → 突发新闻（自动降级 NeoData doc，标题+摘要）
- NeoData doc → 小时~天级深度分析（200-500字/条，分析深度高）
- search_deep → 兜底（覆盖英文源和长尾信息）

### 3. 通用网络搜索（新闻、产品发布、技术动态 — 兜底）

**search_deep(Bash)（Bash 脚本调用，见下方示例）：**
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

**⚠️ 天眼查查中国大陆注册企业；境外企业（港股/美股）用 search_deep(Bash) 兜底。**

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
search_deep(Bash, "{company} {product} 最新版本 发布 {YYYY}年{M}月")
search_deep(Bash, "{company} product roadmap {YYYY}")
search_deep(Bash, "{product} release date changelog")
```

**开源项目（GitHub/HuggingFace）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search_deep
import json
r = search_deep('github.com/{company}/{repo} latest release {YYYY}', max_results=5, fetch_top_n=3)
print(json.dumps(r, ensure_ascii=False, indent=2))
"
# fetch_top_n 会自动抓 README / release 正文，无需单独 fetch
```

**论文和技术验证（arxiv）：**
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.search_gateway import search_deep
import json
r = search_deep('arxiv {company} {technology} {YYYY}', max_results=5, fetch_top_n=3)
print(json.dumps(r, ensure_ascii=False, indent=2))
"
# 论文摘要/正文由 fetch_top_n 自动抓取
```

### 7. 网页正文深度阅读

- ⚠️ 本环境**无 `web_fetch` 内置工具**，直接调用会报错崩溃
- `search_deep(Bash, fetch_top_n=N)` — 搜索 + 自动抓 top N 正文，一步到位（读已知 URL 也用这个，把 URL 当查询词的一部分）

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
