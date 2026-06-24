# IR 研报质量生产型管线重构方案

**核心立场：** 你说得对。交付闸门只能防止坏报告流出去，不能让管线天然产出好报告。真正要修的是“研报生产方式”：从“搜索一堆资料 → 分 step 写 → 统稿”改成“研究问题驱动 → 证据图谱 → 事实卡 → 投资论证 → 章节生成 → 批判复盘 → 定向重写”。

---

## 1. 当前管线为什么天然容易不达标

当前 IR 管线大体是：

```text
preflight
  ↓
company verify
  ↓
presearch
  ↓
extract
  ↓
precompute
  ↓
dispatch step1~step8
  ↓
quality gate
  ↓
delivery
```

这个设计的问题不是“没有闸门”，而是上游生产逻辑不对：

### 1.1 先检索，后思考

现在是先用 query 找资料，再让模型基于资料写。问题是：如果 query 本身没有围绕投资问题拆解，检索结果再多也只是噪音。

应改成：

```text
先生成研究计划 / 投资问题树
再为每个问题设计证据需求
再检索
```

### 1.2 Step 分工是资料维度，不是论证维度

当前 step 分成：行情、行业、业务、财务、管理层、洞察、估值、风险、统稿。

这看起来完整，但缺陷是：每个 step 都在“各写各的”，最后 step8 只能拼装，很难形成强投资主线。

应改成：

```text
每个 step 都服务于同一个 Investment Thesis
每个 step 输出的不是散文，而是证据卡、判断卡、分歧卡、风险卡
```

### 1.3 缺少“事实库存”

现在模型可以在 step 内部直接写数字、写判断、写估值。这样必然出现：

- 数字无来源
- 来源质量不一
- 同一个数字在不同 step 不一致
- 统稿时模型新编数字

应改成：

```text
所有关键事实先进入 Fact Store
正文只能引用 Fact Store 中已登记事实
```

### 1.4 缺少“投资逻辑链”中间产物

研报不是百科资料堆砌。高质量研报要回答：

```text
为什么现在值得看？
核心变化是什么？
市场错在哪里？
未来业绩怎么变？
估值怎么重估？
风险会怎样打破这个判断？
```

当前管线缺少这个中间层，所以最后容易出现“资料很多，但结论跳”。

### 1.5 统稿太晚介入

现在 step8_master 最后才统稿。等到最后才发现前面 step 互相矛盾，成本太高。

应改成：

```text
研究计划阶段就定义主线
每个 step 产物都被主线约束
统稿不是拼接，而是按主线选择证据和组织论证
```

---

## 2. 新管线总设计：从“生成报告”改成“生产投资论证”

目标管线：

```text
Phase A: Research Planner 研究计划器
  ↓
Phase B: Evidence Retrieval 证据检索器
  ↓
Phase C: Evidence Graph / Fact Store 证据图谱与事实库
  ↓
Phase D: Thesis Builder 投资主线生成器
  ↓
Phase E: Section Writers 章节写手
  ↓
Phase F: Debate & Review 批判复盘器
  ↓
Phase G: Targeted Rewrite 定向重写器
  ↓
Phase H: Final Assembly 最终成稿
  ↓
Phase I: Delivery Gate 交付闸门
```

注意：Delivery Gate 仍保留，但它是最后保险丝，不是质量来源。

---

## 3. Phase A：Research Planner 研究计划器

### 3.1 输入

- entity / ticker / market
- 用户 query
- 公司基础验证结果
- 当前市场环境

### 3.2 输出

新增文件：

```text
data/tasks/{task_id}-research_plan.json
```

结构：

```json
{
  "entity": "Alibaba",
  "report_type": "broker_ir",
  "investment_questions": [
    {
      "id": "Q1",
      "question": "阿里核心利润增长是否可持续？",
      "required_evidence": ["FY revenue", "EBITA", "segment margin", "cost trend"],
      "preferred_sources": ["annual_report", "quarterly_results", "company_ir"],
      "minimum_sources": 3
    },
    {
      "id": "Q2",
      "question": "云与AI是否构成估值重估，而不是叙事溢价？",
      "required_evidence": ["cloud revenue", "AI product adoption", "capex", "peer multiples"],
      "preferred_sources": ["company_ir", "earnings_call", "industry_report"],
      "minimum_sources": 4
    }
  ],
  "must_answer": [
    "核心投资结论",
    "业绩变化路径",
    "估值重估路径",
    "主要反证",
    "风险触发条件"
  ],
  "forbidden": [
    "无来源数字",
    "模型训练记忆中的管理层信息",
    "未解释的主观估值调整"
  ]
}
```

### 3.3 关键要求

Research Planner 不写正文，只做三件事：

1. 定义投资问题。
2. 定义每个问题需要哪些证据。
3. 定义通过标准。

这一步能解决“query 泛、检索散、章节松”的根问题。

---

## 4. Phase B：Evidence Retrieval 证据检索器

### 4.1 不再按普通 query 搜

旧方式：

```text
阿里巴巴 市场规模 增长率
阿里巴巴 财务分析
阿里巴巴 AI 估值
```

新方式：每个 investment question 生成 evidence queries。

示例：

```json
{
  "question_id": "Q2",
  "queries": [
    "Alibaba Cloud revenue FY2024 annual report",
    "Alibaba AI cloud earnings call 2024",
    "Alibaba cloud EBITA margin quarterly results",
    "Alibaba Cloud AI model Tongyi Qianwen enterprise adoption source"
  ],
  "source_requirements": {
    "financial": ["company_ir", "annual_report", "sec"],
    "industry": ["IDC", "Gartner", "Canalys", "company_disclosure"],
    "valuation": ["filing", "market_data", "peer_company_filings"]
  }
}
```

### 4.2 检索输出不是 Markdown，而是 Search Manifest

新增文件：

```text
data/tasks/{task_id}-search_manifest.json
```

结构：

```json
{
  "question_id": "Q2",
  "results": [
    {
      "url": "https://www.alibabagroup.com/en-US/ir-financial-reports",
      "title": "Alibaba FY2024 Annual Report",
      "source_tier": "official",
      "source_type": "annual_report",
      "published_at": "2024-05-23",
      "supports": ["cloud revenue", "segment EBITA"],
      "quality_score": 100
    }
  ]
}
```

### 4.3 低质量源处理

低质量源不是完全不能用，而是分用途：

| 来源 | 可用于 | 禁止用于 |
|---|---|---|
| SEC/HKEX/年报/公告 | 财务、管理层、估值基础 | 无 |
| 公司 IR/业绩会 | 业务、战略、财务解释 | 单独支撑第三方市场份额 |
| IDC/Gartner/Canalys | 行业规模、份额 | 公司财务 |
| Reuters/Bloomberg/财新 | 事件、监管、市场观点 | 核心估值数字唯一来源 |
| gu.qq.com/雪球/东方财富 | 行情线索、辅助交叉 | 财务、目标价、管理层、估值核心依据 |
| 知乎/百家号/自媒体 | 不采用 | 全部核心事实 |

---

## 5. Phase C：Evidence Graph / Fact Store

这是最关键的改造。

### 5.1 新增 Fact Store

新增文件：

```text
data/tasks/{task_id}-fact_store.json
```

每个事实都必须结构化：

```json
{
  "fact_id": "F-20240604-001",
  "question_id": "Q2",
  "claim": "Alibaba Cloud FY2024 revenue was RMB 106.4 billion",
  "value": 106.4,
  "unit": "RMB billion",
  "period": "FY2024",
  "entity": "Alibaba Cloud",
  "source_url": "https://www.alibabagroup.com/.../annual-report",
  "source_tier": "official",
  "source_quote": "Cloud Intelligence Group revenue was RMB...",
  "confidence": "high",
  "last_verified_at": "2026-06-04",
  "used_by_sections": []
}
```

### 5.2 Fact Store 规则

1. 财务数字必须有 period。
2. 估值数字必须有 currency。
3. 管理层事实必须有 source_quote。
4. 市场规模必须有机构名和年份。
5. 同一个 claim 多来源冲突时，必须生成 conflict record。

冲突结构：

```json
{
  "conflict_id": "C-001",
  "claim_topic": "cloud revenue FY2024",
  "values": [
    {"value": "106.4bn", "source": "annual_report", "tier": "official"},
    {"value": "105.9bn", "source": "media", "tier": "reputable_media"}
  ],
  "resolution": "use_official",
  "reason": "official filing outranks media summary"
}
```

### 5.3 正文生成限制

章节写手不能直接发明数字。Prompt 必须写死：

```text
You may only use numbers from fact_store.json.
If a needed number is absent, write a data gap request, not a guessed number.
```

这一步能从根上解决“无来源数字”和“张冠李戴”。

---

## 6. Phase D：Thesis Builder 投资主线生成器

### 6.1 目的

在写正文前，先生成投资主线，不直接写报告。

新增文件：

```text
data/tasks/{task_id}-investment_thesis.json
```

结构：

```json
{
  "core_view": "谨慎看多 / 中性 / 看空",
  "one_sentence_thesis": "阿里利润修复已被市场部分定价，云与AI提供上行期权，但估值重估依赖云收入增长和资本开支效率的持续验证。",
  "evidence_chain": [
    {
      "claim": "利润修复具有现实基础",
      "supporting_facts": ["F-001", "F-002", "F-003"],
      "counter_facts": ["F-009"],
      "confidence": "medium_high"
    },
    {
      "claim": "AI/MaaS不能脱离云业务重复估值",
      "supporting_facts": ["F-011", "F-012"],
      "counter_facts": [],
      "confidence": "high"
    }
  ],
  "bear_case": {
    "trigger": "云收入增速低于同业且AI货币化不及预期",
    "implication": "云业务估值倍数下修"
  },
  "valuation_principle": "SOTP为主，DCF为交叉验证；AI/MaaS只作为云智能集团估值中的增长因子，不单独重复估值。"
}
```

### 6.2 为什么需要这一步

没有 Thesis Builder，step8_master 就会拼素材。

有 Thesis Builder，后续所有章节围绕同一主线写：

```text
事实 → 判断 → 反证 → 估值影响
```

---

## 7. Phase E：Section Writers 章节生产器

### 7.1 改造方向

旧 step 输出：长篇 Markdown。

新 step 输出：结构化章节包。

新增每个 step 的输出格式：

```json
{
  "section_id": "financial_analysis",
  "section_title": "财务分析：利润修复的质量与持续性",
  "key_messages": [
    "收入增速不是核心，利润率修复才是当前估值支撑",
    "成本优化贡献需和业务增长贡献拆开"
  ],
  "claims": [
    {
      "claim": "FY2024 adjusted EBITA margin improved",
      "fact_ids": ["F-001", "F-002"],
      "reasoning": "margin improvement reflects cost optimization and mix shift",
      "confidence": "high"
    }
  ],
  "data_gaps": [],
  "risks_to_thesis": [
    "利润修复若主要来自费用压缩而非收入增长，持续性较弱"
  ],
  "markdown_draft": "..."
}
```

### 7.2 章节写作规则

每一节必须按这个结构：

```text
1. 本节结论
2. 支撑事实
3. 推理链条
4. 反向证据 / 不确定性
5. 对估值或投资结论的影响
```

禁止：

- 只罗列资料
- 没有 fact_id 的数字
- 没有反向证据
- 和 investment_thesis 无关的百科内容

---

## 8. Phase F：Debate & Review 批判复盘器

### 8.1 不是最后验收，而是中途纠偏

在所有章节生成后，不直接统稿，而是跑一个 Debate Agent。

新增文件：

```text
data/tasks/{task_id}-debate_review.json
```

它问六个问题：

1. 主线是否被所有章节支撑？
2. 有没有章节和主线矛盾？
3. 哪些关键结论证据不足？
4. 哪些数字来源等级不够？
5. 哪些估值假设不可复算？
6. bear case 是否足以推翻目标价？

输出：

```json
{
  "verdict": "REWRITE_REQUIRED",
  "issues": [
    {
      "severity": "HIGH",
      "section": "valuation",
      "issue": "AI/MaaS appears separately valued while cloud SOTP already includes cloud growth",
      "required_action": "merge AI/MaaS into cloud growth sensitivity; remove standalone valuation line"
    }
  ]
}
```

这一步的作用是：在最终成稿前就纠正方向，而不是等 DOCX 生成后才发现。

---

## 9. Phase G：Targeted Rewrite 定向重写器

### 9.1 不要整篇重写

整篇重写会引入新错误。必须只改失败点。

Rewrite input：

```json
{
  "section": "valuation",
  "issue": "AI/MaaS duplicate valuation",
  "allowed_facts": ["F-011", "F-012", "F-013"],
  "required_change": "remove standalone AI/MaaS valuation; treat AI as cloud growth sensitivity",
  "forbidden": ["new numbers", "new source", "unsupported synergy adjustment"]
}
```

Rewrite output 必须包含：

```json
{
  "changed_paragraphs": [...],
  "removed_claims": [...],
  "new_claims": [],
  "fact_ids_used": [...],
  "remaining_issues": []
}
```

### 9.2 重写循环上限

建议最多 2 轮：

```text
review → rewrite → review → rewrite → if still fail: mark unresolved and block
```

不能无限循环。

---

## 10. Phase H：Final Assembly 最终成稿器

### 10.1 统稿器职责变化

旧统稿器：整合各 step 的 Markdown。

新统稿器：根据 investment_thesis 和 section packages 组装论证。

它不允许新增事实，只能：

- 选择事实
- 排列论证
- 消除重复
- 统一语气
- 生成摘要、目录、图表说明、来源附录

### 10.2 最终报告结构

券商版研报建议固定结构：

```text
1. 投资摘要
   - 评级 / 观点
   - 目标价 / 估值区间
   - 核心逻辑三条
   - 主要风险三条

2. 核心投资主线
   - 市场分歧
   - 我们的判断
   - 证据链

3. 公司基本面
   - 收入结构
   - 利润质量
   - 业务变化

4. 行业与竞争
   - 行业增速
   - 竞争格局
   - 公司位置

5. 云与AI专题
   - 业务事实
   - 商业化路径
   - 估值含义
   - 不能重复计价说明

6. 财务预测
   - 收入预测
   - 利润预测
   - 关键假设

7. 估值
   - SOTP
   - DCF交叉验证
   - 同业比较
   - 敏感性分析

8. 风险
   - 业务风险
   - 竞争风险
   - 监管风险
   - 估值风险

9. 附录
   - Claim Cards
   - 来源清单
   - 冲突处理记录
```

---

## 11. Phase I：Delivery Gate 还是要保留

但现在它的定位变了：

```text
不是靠 gate 修报告
而是靠 gate 证明生产过程没漏掉问题
```

Delivery Gate 检查：

- 是否所有 section package 通过。
- 是否所有关键 claim 有 fact_id。
- 是否所有 fact_id 可追溯。
- 是否 debate_review verdict 为 PASS。
- 是否 rewrite 后无 HIGH issue。
- 是否 DOCX 无污染。

---

## 12. 代码落地建议

### 12.1 新增模块

```text
scripts/ir_research_planner.py
scripts/ir_evidence_retriever.py
scripts/ir_fact_store.py
scripts/ir_thesis_builder.py
scripts/ir_section_package.py
scripts/ir_debate_review.py
scripts/ir_targeted_rewrite.py
scripts/ir_final_assembler.py
scripts/ir_source_policy.py
scripts/ir_claim_cards.py
```

### 12.2 修改现有管线

文件：`runtime/profiles/ir_profile.py`

从当前：

```python
phase_handlers={
    "phase0_preflight": ...,
    "phase02_company_verify": ...,
    "phase1_presearch": ...,
    "phase15_extract": ...,
    "phase12_precompute": ...,
    "phase4_dispatch_prepare": ...,
    "phase4_dispatch_collect": ...,
    "phase5_delivery": ...,
}
```

升级为：

```python
phase_handlers={
    "phase0_preflight": ...,
    "phase02_company_verify": ...,
    "phase1_research_plan": _run_research_plan,
    "phase2_evidence_retrieval": _run_evidence_retrieval,
    "phase3_fact_store": _run_fact_store,
    "phase4_thesis": _run_thesis_builder,
    "phase5_sections": _run_section_writers,
    "phase6_debate_review": _run_debate_review,
    "phase7_targeted_rewrite": _run_targeted_rewrite,
    "phase8_final_assembly": _run_final_assembly,
    "phase9_delivery": _run_delivery,
}
```

兼容策略：先不删除旧 phase，新增 v2 profile 或 metadata 开关：

```python
metadata["ir_quality_pipeline_v2"] = True
```

### 12.3 旧模块复用

不是推倒重来，可以复用：

| 旧模块 | 新用途 |
|---|---|
| `phase02_company_verify` | 继续做公司身份和行情基础验证 |
| `phase1_presearch` | 改造成 evidence retrieval 的底层工具 |
| `phase15_extract` | 改造成 evidence extraction 工具 |
| `phase12_precompute` | 继续做财务/估值 baseline |
| `verification_agent.py` | 从最终闸门升级为中途 review + 最终 audit |
| `ir_quality_gate.py` | 从 step 字数评分升级为 section package 验证 |
| `build_ir_broker_report_docx.py` | 只接受 final_assembly 产物，不直接从 step 拼 |

---

## 13. 质量达标的真正定义

不是“验证器 PASS”。

而是每篇报告都有完整链条：

```text
研究问题
  → 证据需求
  → 权威来源
  → fact store
  → investment thesis
  → section claims
  → debate review
  → targeted rewrite
  → final assembly
  → delivery audit
```

只要这条链条断了，就不能说质量达标。

---

## 14. 实施优先级

### 第一阶段：生产结构先立起来

1. `ir_research_planner.py`
2. `ir_fact_store.py`
3. `ir_thesis_builder.py`
4. `ir_section_package.py`

目的：让管线不再直接写散文，而是先生产结构化研究资产。

### 第二阶段：让章节自然变好

5. section writer 只能用 fact store。
6. 每节必须输出 key messages、claims、risks、data gaps。
7. 每节必须绑定 investment thesis。

目的：解决章节松散、重复、逻辑跳的问题。

### 第三阶段：让统稿不再乱编

8. final assembler 只能组装 section packages。
9. 禁止新增事实。
10. 自动去重、合并、统一口径。

目的：解决统稿阶段新引入错误。

### 第四阶段：批判复盘和重写闭环

11. debate review 找主线矛盾、证据不足、估值漏洞。
12. targeted rewrite 只修失败片段。
13. 二轮后仍失败就阻断。

目的：让质量在生成过程中被修好，而不是最后被挡住。

### 第五阶段：交付闸门

14. verification gate。
15. source audit。
16. docx audit。

目的：保险丝。

---

## 15. 我建议的 MVP

不要一次做全。MVP 做 5 个东西就能明显改善输出质量：

1. **Research Plan**：先定义投资问题和证据需求。
2. **Fact Store**：所有关键数字先入库。
3. **Thesis Builder**：写正文前先形成投资主线。
4. **Section Package**：章节输出结构化，不再自由散文。
5. **Debate Review**：统稿前先批判复盘并定向重写。

交付闸门放第 6。

也就是说，真正的修复顺序应该是：

```text
研究计划 → 事实库 → 投资主线 → 结构化章节 → 批判复盘 → 闸门
```

而不是：

```text
原管线 → 加闸门
```

---

## 16. 最终判断

如果只做闸门，结果会变成：

```text
坏报告被挡住，但经常没有报告可交付
```

如果按这个方案改，目标是：

```text
上游生产过程本身收敛到合格报告，闸门只是兜底
```

这才是 IR 管线该有的设计。
