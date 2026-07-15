# 🐲 IR/BP/LIT Workflow

> AI 驱动的投研（IR）+ 商业计划书尽调（BP）+ 文献综述（LIT）三管线工作流。
> 全自动闭环，零人工干预。

**三条管线，统一架构：**
- **IR 管线**：股票/标的深度研报（9 步 + 5 波）
- **BP 管线**：商业计划书尽调（37 Phase + 5 波 + 统稿）
- **LIT 管线**：技术评估文献综述（20 Phase + 3 波 + per-sub_topic 并行深读）

---

## 一句话看懂管线

**BP 文件进来 → 提取结构化数据 → Wave 0 投资假说先行者 → 8 个维度子代理分 4 波调研 → 共识挑战/催化剂/行业研报 → 每个维度产出的事实存入全局 Fact Store → 门禁校验证据完整性 → 不合格的自动修复 → 合格的进入统稿 → 生成 DOCX 交付。**

整个管线是一个**数据流闭环**：每个分析结论都必须有可追溯的证据（claim → fact → source），没有证据的结论会被门禁拦截并要求修复。

---

## 管线全景图

```
                         ┌──────────────────────────────────────┐
                         │          BP 文件（PDF/PPTX）           │
                         └──────────────┬───────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Phase 01-07: 预处理（脚本，无子代理）     │
                    │                                        │
                    │  01 OCR 识别 → 结构化公司数据              │
                    │  02 天眼查工商验证 → 公司基本信息           │
                    │  03 研究计划 → claim 矩阵 + LLM enrichment │
                    │  04 四维度预搜索 → 初始事实种子             │
                    │  05 共享尽调页 → 跨维度信息板               │
                    │  06 搜索工单 → 子代理的搜索任务清单          │
                    │  07 Fact Store 初始化 → 注入预搜索事实       │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 1: 基础证据采集（4 角色 sequential）  │
                    │                                        │
                    │  08 prepare → 09 collect → 10 gate      │
                    │                                        │
                    │  角色：团队合规 / 产品商业 / 技术IP / 市场供应链 │
                    │  每个角色产出：.md + facts.json + section.json │
                    └───────────────────┬────────────────────┘
                                        │
                              ┌─────────▼─────────┐
                              │  Wave1 证据门禁     │
                              │  FAIL → repair → 重跑 │
                              │  PASS → 推进        │
                              └─────────┬─────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 2: 客户收入验证（1 角色，可跳过）     │
                    │  13 prepare → 14 collect → 15 gate      │
                    │  T1 种子轮项目直接跳过此 Wave              │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 3: 竞争 + 估值（2 角色 sequential）  │
                    │  16 prepare → 17 collect → 18 gate      │
                    │  读 Wave1+2 输出做交叉验证                 │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 4: 红队风险（1 角色）               │
                    │  20 prepare → 21 collect → 22 gate      │
                    │  读 Wave1+2+3 全量输出做反向论证           │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Phase 24-26: 全局校验                   │
                    │                                        │
                    │  24 Claim 覆盖校验（每个 claim 有证据？）   │
                    │  25 跨维度一致性（不同维度引用同一数字？）    │
                    │  26 Section Package 校验（格式完整性）      │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Phase 27-28: 统稿（1 个子代理）           │
                    │  读 8 个维度 .md + Fact Store → 写最终报告  │
                    │  脚注密度不达标 → repair 子代理补脚注       │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Phase 29-33: 交付                      │
                    │                                        │
                    │  29 对抗评审（找证据推翻投资结论）           │
                    │  30 最终组装（合并所有章节）                │
                    │  31 可读性审查                           │
                    │  32 投资判断汇总                          │
                    │  33 DOCX 生成 + 桌面复制 + 通知用户        │
                    └───────────────────┬────────────────────┘
                                        │
                                        ▼
                              ┌─────────────────┐
                              │  DD 尽调报告.docx │
                              │  桌面 + 聊天窗口   │
                              └─────────────────┘
```

---

## LIT 管线全景图（v4.0）

**技术方向 + 查询 → 子代理预搜拆解 sub_topics → 三路采集（学术/行业/企业）→ per-sub_topic 并行深读全部论文 → 质量评估 + 技术战略分析 → 完整评估报告交付。**

```
                         ┌──────────────────────────────────────┐
                         │     技术方向 + 查询（如"固态电池"）      │
                         └──────────────┬───────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Phase 01-05: 准备阶段                   │
                    │                                        │
                    │  01 intake → 解析输入                    │
                    │  02 tech_decomposition → 子代理预搜       │
                    │     + 直接输出 research_plan.json         │
                    │     （sub_topics + claim_matrix +         │
                    │      search_keywords + search_matrix）    │
                    │  03 research_plan → cached check + 兜底   │
                    │  04 presearch → 方向可行性验证              │
                    │  05 shared_state_init → 初始化骨架         │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 1: 三路采集（3 角色 sequential）     │
                    │  06 prepare → 07 collect → 08 gate      │
                    │                                        │
                    │  academic_scout: S2/OpenAlex/arXiv/DBLP  │
                    │  industry_scout: NeoData/研报/新闻         │
                    │  enterprise_scout: 天眼查/SEC 企业尽调      │
                    │  → Evidence Gate (PRISMA 完整性)           │
                    │  → Fact Store Merge → Shared State Refresh│
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 2: 深读 + 分析（per-sub_topic 并行） │
                    │  11 prepare → 12 collect → 13 gate      │
                    │                                        │
                    │  deep_reader × N: 每个 sub_topic 独立     │
                    │    agent，读全部论文（全文优先/摘要兜底）    │
                    │    → sub_topic_N_reading_notes.json      │
                    │    → quality_assessment (4维度+tier)       │
                    │  tech_strategist: TRL/Gartner/路线/竞争    │
                    │    （缺失时 report_writer 自动 fallback）  │
                    │  → Evidence Gate (quality_tier 容错)       │
                    │  → Shared State Refresh (+ quality_summary)│
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │  Wave 3: 报告产出 + 质量链 + 交付          │
                    │  15 prepare → 16 collect                │
                    │                                        │
                    │  report_writer → 完整 9 章评估报告         │
                    │  → Claim Coverage → Debate Review         │
                    │  → Final Assembly → Delivery              │
                    └───────────────────┬────────────────────┘
                                        │
                                        ▼
                              ┌─────────────────┐
                              │  report.md       │
                              │  + fact_store    │
                              └─────────────────┘
```

### LIT Phase 清单

| # | Phase | 作用 | 输出 |
|---|-------|------|------|
| 01 | intake | 解析用户输入 | intake.json |
| 02 | tech_decomposition | 子代理预搜 + 拆解 sub_topics | tech_decomposition.json + research_plan.json |
| 03 | research_plan | cached check + 兜底生成 | research_plan.json |
| 04 | presearch | 方向可行性验证 | presearch.json |
| 05 | shared_state_init | 初始化共享状态 | shared_state.json |
| 06 | wave1_dispatch_prepare | W1 调度（3 角色 sequential） | manifest |
| 07 | wave1_dispatch_collect | W1 收集 + 4 层防御 | — |
| 08 | wave1_evidence_gate | W1 质量门 + repair | wave1_gate.json |
| 09 | wave1_fact_store_merge | W1 合并 → fact_store | fact_store.json |
| 10 | wave1_shared_state_refresh | W1 刷新 → reading_tasks（含模糊匹配） | shared_state.json |
| 11 | wave2_dispatch_prepare | W2 调度（per-sub_topic + tech_strategist） | manifest |
| 12 | wave2_dispatch_collect | W2 收集 + quality_summary 合并 | — |
| 13 | wave2_evidence_gate | W2 质量门（quality_tier 容错） | wave2_gate.json |
| 14 | wave2_shared_state_refresh | W2 刷新 | shared_state.json |
| 15 | wave3_dispatch_prepare | W3 调度（report_writer + fallback） | manifest |
| 16 | wave3_dispatch_collect | W3 收集 | — |
| 17 | claim_coverage | 全 claim 覆盖检查 | claim_coverage.json |
| 18 | debate_review | 跨章节对抗审查 | debate_review.json |
| 19 | final_assembly | 排版 + 引用格式化 | report.md |
| 20 | delivery | 交付 | — |

### LIT v4.0 关键设计

- **per-sub_topic 并行**：deep_reader 拆分为 N 个独立 agent（每 sub_topic 一个），每个读全部论文，不再限 8 篇
- **phase02 子代理化**：不再让 Coordinator 手写 JSON，子代理直接预搜 + 输出 research_plan.json
- **quality_tier 容错**：gate 兼容 `quality_tier` / `overall_grade` / `grade` 字段名
- **tech_strategist fallback**：缺失时 gate 降为 WARN，report_writer 自动降级模式
- **模糊匹配兜底**：shared_state_refresh 对空 sub_topic 做关键词匹配补充

---

## 闭环一：Kernel 状态机 — 管线怎么推进

管线的核心引擎是 `kernel.py`，一个 **Phase 状态机**。每个 Phase 返回三种结果之一，kernel 据此决定下一步：

```
Phase 执行结果                    Kernel 动作
─────────────────────────────────────────────────────
ok=true, 无 dispatch          →  推进到下一个 Phase
ok=true, needs_dispatch       →  暂停，等子代理完成
ok=true, has_more=true        →  暂停，但 next_phase = 当前（再派一个子代理）
ok=false                      →  直接终止管线（不可恢复）
```

**断点续跑**：kernel 把每个 Phase 的状态持久化到 `job_record.json`。管线中断后重启，kernel 从最后一个 completed Phase 继续，不丢中间结果。

**依赖回填**：如果指定 `start_phase` 从中间开始，kernel 检查 `phase_prerequisites()` 声明的依赖图，发现缺失文件时精准回退到产出该文件的 Phase 重跑，而非从头开始。

---

## 闭环二：Sequential 子代理派发 — 怎么防止并行写冲突

8 个维度的子代理需要往共享文件（Fact Store、Sidecar）写数据。之前并行派发导致写冲突丢数据。现在的方案：

```
prepare() 函数
    │
    ├─ 扫描所有 role，找到第一个未完成的
    │
    ├─ 返回 { needs_dispatch: true, has_more: true, manifests: [这1个] }
    │         │
    │         └─ kernel 看到 has_more=true → next_phase = 当前 Phase → 重跑 prepare
    │
    ├─ 主 AI 派发这个子代理 → 等待完成 → 再跑 prepare → 派下一个
    │
    └─ 所有 role 都完成 → has_more: false → kernel 推进到 collect Phase
```

**覆盖范围**：Wave 1（4 角色）+ Wave 2（1 角色）+ Wave 3（2 角色）+ Wave 4（1 角色）+ 3 个 repair 分支 + 统稿。全部走同一套 sequential 逻辑。

---

## 闭环三：子代理三文件输出契约

每个子代理（分析 role 或 repair role）完成后必须输出 3 个文件，缺一不可：

```
jobs/{JOB_ID}/outputs/
├── bp_phase2_{slug}.md             ← 文件 1：Markdown 分析报告
├── bp_phase2_{slug}-facts.json     ← 文件 2：结构化事实数组
└── bp_phase2_{slug}-section.json   ← 文件 3：Section Package
```

| 文件 | 内容 | 下游消费者 | 为什么需要 |
|------|------|-----------|-----------|
| `.md` | 该维度的完整分析文本 | 统稿子代理读取，拼入最终报告 | 人类可读的分析产物 |
| `-facts.json` | 结构化事实：每条含 `fact_id`、`claim`、`value`、`source_url`、`source_tier`、`confidence` | Fact Store 合并 → Claim 覆盖校验 → 统稿脚注生成 | 把"AI 说了什么"变成可验证的结构化数据 |
| `-section.json` | Section Package：`key_messages`、`claims[]`（含 `fact_ids` 引用）、`counter_evidence`、`data_gaps` | Section Package 校验 → 门禁判定 | 结构化描述"这一节说了什么、证据在哪、缺口在哪" |

**完成判定**（`_role_outputs_complete`）：三文件全部存在 + 体积达标 + JSON 合法 + 文件大小 3 秒内不增长。

**collect 阶段**（`_collect_with_retry`）：10 次轮询 × 30 秒间隔 = 最长等 300 秒。如果子代理未能在窗口内完成，collect 返回 `needs_dispatch`，kernel 重跑 prepare 再派一次。

---

## 闭环四：Fact Store — 全局事实数据库

所有子代理的 facts sidecar 在 Phase 11 合并为一个中央数据库：

```json
{
  "schema_version": "bp_fact_store.v1",
  "facts": [
    {
      "fact_id": "BP-PRESEARCH-TECH-F001",
      "claim": "2024年营收",
      "value": "3.2",
      "unit": "亿元",
      "source_url": "https://...",
      "source_tier": "T1",
      "source_quote": "原文摘录",
      "confidence": "high"
    }
  ]
}
```

Fact Store 的作用形成三条闭环：
1. **Claim 覆盖校验**（P24）：每个 claim 是否有对应 fact？没有 → repair 子代理补证据
2. **统稿脚注生成**（P27-28）：统稿子代理从 Fact Store 提取 source_url 生成脚注，而非自己编造
3. **跨维度一致性**（P25）：不同 role 引用同一指标时，通过 fact_id 关联验证

---

## 闭环五：门禁 + Repair — 不合格不放过

管线有 4 层门禁，每层都遵循同一套容错逻辑：

```
门禁校验
    │
    ├─ PASS → 推进到下一个 Phase
    │
    ├─ REPAIR（可修复）
    │     │
    │     ├─ 生成 repair manifest（按 role 聚合）
    │     ├─ sequential 派发 repair 子代理
    │     ├─ 子代理修复 → 重跑门禁
    │     └─ 超过最大重试次数 → 降级为 WARN 放行（标记 repair_exhausted）
    │
    ├─ WARN（可放行）→ 记录到 deferred_fixes，不阻断交付
    │
    └─ FAIL（不可修复）→ 管线终止
```

| 门禁 | Phase | 校验内容 | 最大 repair 次数 | T1/T2 特殊处理 |
|------|-------|---------|-----------------|---------------|
| Wave 证据门禁 | P10/P15/P18/P22 | 每个 wave 的 evidence 完整性 | 1 次 | blocking claims 直接降级 WARN |
| Claim 覆盖校验 | P24 | 每个 claim 有对应 fact？ | 2 次 | 直接降级 disclosure |
| 统稿质量 | P28 | 脚注密度（每 2000 字 ≥ 3 个） | 1 次 | — |
| 交付门禁 | P33 | 可读性 + 对抗评审 + 格式完整 | — | 可读性/对抗 FAIL → WARN |

**文件锁保护**：repair 子代理写共享文件时用 `bp_file_lock.locked_read_modify_write()`，加 flock 独占锁，防止并行写丢数据。

---

## 闭环六：搜索系统 — 6 层降级链

子代理搜索数据时走 `search_gateway.py`，自动按优先级降级：

```
Layer 0: NeoData（A/HK 股行情/财报/板块）    ← 金融查询首选
    ↓ 不可用或非金融查询
Layer 1: DuckDuckGo（通用搜索）
    ↓ 结果不足
Layer 2: SearXNG（本地 Baidu+Bing）
    ↓ 不可用
Layer 3: Google 直接抓取
    ↓ 需要代理
Layer 4: Scrapling StealthyFetcher（深度正文）
    ↓
Layer 5: yfinance（美股估值）
```

**金融查询自动路由**：搜索网关检测查询含"市值/营收/PE/财报"等关键词时，优先走 NeoData。非金融查询走通用搜索。

**数据源优先级**：A/HK 股 → NeoData → yfinance（交叉验证）→ web_search；美股 → yfinance → web_search。

---

## 12 个子代理速查（v4.5: 5 波 12 角色）

| 角色 | Wave | 职责 | 核心工具 |
|------|------|------|---------|
| `bp_investment_hypothesis` | W0 | 投资假说先行者（提假说+可验证问题） | 天眼查 + westock-mcp + web_search |
| `bp_company_team_compliance` | W1 | 团队/合规/治理 | 天眼查 + westock-mcp |
| `bp_product_commercial` | W1 | 产品/商业化/客户 | 天眼查 + westock-mcp |
| `bp_tech_ip_moat` | W1 | 技术/IP/护城河 | 天眼查 + westock-mcp |
| `bp_market_supply_chain` | W1 | 市场/行业/供应链 | 天眼查 + westock-mcp |
| `bp_customer_revenue_validation` | W2 | 客户收入交叉验证 | 天眼查 + westock-mcp |
| `bp_competition_positioning` | W3 | 竞品清单/差异化/可复制性 | 天眼查 + westock-mcp |
| `bp_valuation_return` | W3 | 估值情况/可比公司 | 天眼查 + westock-mcp |
| `bp_dealbreaker_risk` | W4 | 红队风险/deal breaker | 天眼查 + westock-mcp |
| `bp_consensus_challenge` | W4 | 共识挑战/预期差分析 | 天眼查 + westock-mcp + web_search |
| `bp_catalyst` | W4 | 催化剂事件/时间窗口/传导链 | 天眼查 + westock-mcp + web_search |
| `bp_industry_research` | W4 | 行业研报整合（6 大类基准数据） | westock-mcp + web_search |

每个角色的指令文件在 `instruction_store_bp/` 目录，含角色专属工具映射表。通用工具使用指南在 `_common_tool_guide.md`。

---

## 37 Phase 完整清单（v4.5: +Wave 0 先行者 + Wave 4 扩展）

| # | Phase | 类型 | 说明 |
|---|-------|------|------|
| 01 | document_intake | 脚本 | VL OCR 识别 + 结构化抽取 |
| 02 | company_verify | 脚本 | 天眼查工商/风险验证 |
| 03 | presearch | 脚本 | web+新闻预搜索 |
| 04 | research_plan | dispatch | 研究计划子代理派发 |
| 04c | research_plan_collect | 收集 | 读子代理输出 |
| 05 | bp_shared_page_init | 脚本 | 共享尽调页初始化 |
| 06 | search_plan_compile | 脚本 | 搜索工单编译 |
| 07 | bp_fact_store_bootstrap | 脚本 | Fact Store 初始化 |
| 07b | wave0_prepare | dispatch | **Wave 0 投资假说先行者（1 角色）** ★v4.5 |
| 07c | wave0_collect | 收集 | Wave 0 收集 |
| 07d | wave0_shared_page_refresh | 脚本 | 共享页刷新（含假说） |
| 08 | dispatch_prepare | dispatch | Wave 1 派发（sequential, 4 角色） |
| 09 | dispatch_collect | 收集 | Wave 1 收集（retry + 三文件检查） |
| 10 | wave1_evidence_gate | 门禁 | Wave 1 证据校验（repair） |
| 11 | bp_fact_store_merge | 脚本 | Fact Store 合并 |
| 12 | wave1_shared_page_refresh | 脚本 | 共享页刷新 |
| 13 | wave2_prepare | dispatch | Wave 2 派发（T1 跳过） |
| 14 | wave2_collect | 收集 | Wave 2 收集 |
| 15 | wave2_evidence_gate | 门禁 | Wave 2 证据校验（repair） |
| 16 | wave3_prepare | dispatch | Wave 3 派发 |
| 17 | wave3_collect | 收集 | Wave 3 收集 |
| 18 | wave3_evidence_gate | 门禁 | Wave 3 证据校验（repair） |
| 19 | wave3_shared_page_refresh | 脚本 | 共享页刷新 |
| 20 | wave4_prepare | dispatch | Wave 4 派发（4 角色: dealbreaker+共识+催化剂+行业研报）★v4.5 |
| 21 | wave4_collect | 收集 | Wave 4 收集 |
| 22 | wave4_evidence_gate | 门禁 | Wave 4 证据校验（repair, _NON_CLAIM_ROLES 跳过假说类）★v4.5 |
| 23 | wave4_shared_page_refresh | 脚本 | 共享页刷新 |
| 24 | bp_claim_coverage_validation | 门禁 | Claim 覆盖校验（repair） |
| 25 | bp_cross_dimension_gate | 门禁 | 跨维度一致性 |
| 26 | bp_section_package_validation | 校验 | Section Package 校验 |
| 27 | synthesis_prepare | dispatch | 统稿派发（8 维度三件套 + 4 叙事仅 md）★v4.5 |
| 28 | synthesis_collect | 收集 | 统稿收集（脚注密度 repair） |
| 29 | bp_debate_review | 校验 | 对抗评审 |
| 30 | bp_final_assembly | 脚本 | 最终组装 |
| 31 | bp_readability_review | 校验 | 可读性审查 |
| 32 | bp_investment_judgment | 脚本 | 投资判断汇总 |
| 33 | delivery | 交付 | DOCX 生成 + 交付门禁（动态 12 slug）★v4.5 |

---

## 双管线架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      ir-coordinator（调度中心）                    │
│            接收自然语言指令 → 自动识别管线类型 → 全自动执行           │
├───────────────────────────────┬─────────────────────────────────┤
│     IR 管线（券商研报）         │       BP 管线（尽调报告）          │
│     9 步 · 5 波子代理           │       37 Phase · 5 波 + 统稿      │
│                               │                                  │
│  预搜索 → 5波派发 → 对抗验证    │  OCR → W0假说 → 4波+门禁 → 统稿  │
│  → DOCX 生成 + 桌面复制        │  → 对抗评审 → 桌面复制             │
├───────────────────────────────┴─────────────────────────────────┤
│                         共享基础设施                               │
│  kernel.py（编排器）· search_gateway（搜索）· bp_file_lock（文件锁）  │
│  Fact Store（事实库）· bp_subagent_launcher_wb（子代理发射器）        │
└─────────────────────────────────────────────────────────────────┘
```

IR 管线通过 `ir-coordinator` skill 对话触发（如"分析比亚迪"），BP 管线通过"帮我看下这个 BP"触发。两者共享同一套 kernel 和搜索基础设施。

---

## 📦 目录结构

```
ir-bp-workflow/
├── runtime/                         # 核心运行时（三线共用）
│   ├── profiles/                    # 管线 Profile
│   │   ├── base.py                  # 抽象基类
│   │   ├── ir_profile.py            # IR 管线（9步 + 5波）
│   │   ├── bp_profile.py            # BP 管线（33 Phase + 4波 + 统稿）
│   │   ├── bp_constants.py          # BP 共享常量
│   │   ├── ic_profile.py            # IC 管线（行业研究）
│   │   ├── lit_review_profile.py    # LIT 管线（20 Phase + 3波 + per-sub_topic 并行）
│   │   └── lit_constants.py         # LIT 共享常量
│   └── orchestrator/
│       └── kernel.py                # Phase 执行引擎
├── scripts/                         # 功能脚本
│   ├── bp_subagent_launcher_wb.py   # BP 子代理发射器
│   ├── ir_subagent_launcher_wb.py   # IR 子代理发射器
│   ├── search_gateway.py            # 搜索网关（6层降级链）
│   ├── bp_file_lock.py              # 文件锁
│   ├── bp_utils.py                  # BP 公共工具
│   ├── bp_delivery_gate.py          # 交付门禁
│   ├── bp_claim_coverage_validator.py
│   ├── bp_wave_evidence_gate.py     # Wave 证据门禁
│   ├── build_bp_dd_report_docx.py   # DOCX 生成
│   ├── api_clients/                 # LIT 学术 API 客户端
│   │   ├── openalex_client.py       # OpenAlex (全领域)
│   │   ├── arxiv_client.py          # arXiv (预印本)
│   │   ├── s2_client.py             # Semantic Scholar (引用图谱)
│   │   ├── dblp_client.py           # DBLP (CS)
│   │   ├── pmc_client.py            # PubMed Central (生物医学)
│   │   ├── crossref_client.py       # Crossref (DOI)
│   │   └── core_client.py           # CORE (OA)
│   ├── fulltext/                    # LIT 全文获取 + 提取
│   │   ├── pdf_downloader.py        # PDF 下载路由
│   │   ├── pdfplumber_extractor.py  # pdfplumber 提取
│   │   ├── marker_extractor.py      # Marker 提取
│   │   └── web_scraper.py           # 网页抓取
│   └── search/                      # LIT 搜索
│       ├── unified_search.py        # 多源并行搜索 + 去重
│       ├── neodata_search.py        # NeoData 研报搜索
│       ├── dedup.py                 # DOI + title 去重
│       └── rate_limiter.py          # API 限速
├── instruction_store_bp/            # BP 角色指令库（12 角色: 8 维度 + W0假说 + W4共识/催化剂/行业研报）
│   ├── _common_tool_guide.md        # 通用工具使用指南
│   ├── bp_company_team_compliance.md
│   ├── bp_competition_positioning.md
│   ├── bp_dealbreaker_risk.md
│   └── ...
├── instruction_store_ir/            # IR 角色指令库（11 个角色）
├── instruction_store_lit/           # LIT 角色指令库（7 个角色）
│   ├── index.json                   # 角色 → 文件映射
│   ├── _common_tool_guide.md        # LIT 通用工具指南
│   ├── research_plan_enrichment.md  # tech_decomposition 子代理指令
│   ├── academic_scout.md            # 学术搜索专家
│   ├── industry_scout.md            # 行业情报搜索专家
│   ├── enterprise_scout.md          # 企业侦察专家
│   ├── deep_reader.md               # 深度阅读分析师（per-sub_topic）
│   ├── tech_strategist.md           # 技术战略师
│   └── report_writer.md             # 报告撰写专家
├── references/                      # 项目级统一知识库（单一真实来源）
│   ├── pipeline/                    # 管线流程文档
│   ├── quality/                     # 质量门禁 + 验证策略
│   ├── operations/                  # 数据源 + 搜索 + 估值方法论
│   ├── delivery/                    # 交付协议
│   └── coordinator/                 # coordinator 专用文档
├── skills/                          # AI Agent Skill 定义
│   ├── ir-coordinator/SKILL.md      # 唯一活跃运行时 skill
│   ├── ir-researcher/SKILL.md       # 架构文档
│   ├── ir-reporter/SKILL.md         # 架构文档
│   └── ir-verifier/SKILL.md         # 架构文档
├── search/adapters/                 # 搜索引擎适配器
├── tests/                           # 测试
├── setup.sh                         # 一键安装
├── ir_runtime.py                    # CLI 管理入口
└── run_bp.py                        # BP 管线运行脚本
```

## 🚀 安装

```bash
curl -fsSL https://raw.githubusercontent.com/Xavier-06/ir-bp-workflow/main/setup.sh | bash
```

安装脚本自动完成：克隆仓库 → 安装 Python 依赖 → 创建 .env → 安装 Skills → 创建运行时目录。

### 前置条件

| 依赖 | 必需？ | 说明 |
|------|--------|------|
| Python 3.10+ | ✅ | 管线运行 |
| WorkBuddy | ✅ | AI Agent 平台 |
| VL 视觉模型（qwen3-vl 等） | BP 必需 | BP OCR 识别 |
| 天眼查 MCP（tyc-mcp） | BP 必需 | 工商验证/竞争分析 |
| DuckDuckGo Search | ✅ | 搜索引擎 |
| yfinance | ✅ | 金融估值数据 |
| NeoData | 推荐 | A/HK 股首选数据源 |
| SearXNG Docker | 推荐 | 本地搜索引擎 |
| HTTP 代理 | 按需 | Google/Scrapling 翻墙 |

### 环境变量

```bash
# BP 管线必需
VL_API_BASE=https://your-vl-api-base/v1
VL_API_KEY=sk-xxxx

# 可选
SEARXNG_URL=http://127.0.0.1:8888
PROXY_URL=http://127.0.0.1:7897
```

## 📋 使用

**对话触发（推荐）：**
- "分析比亚迪" → IR 管线
- "帮我看下这个 BP" + 上传文件 → BP 管线
- "做个固态电池的技术评估" / "文献综述" → LIT 管线

**CLI：**
```bash
python3 ir_runtime.py check          # 环境检测
python3 ir_runtime.py run TASK-XXX   # 执行管线
python3 ir_runtime.py status TASK-XXX # 查看状态
python3 ir_runtime.py list           # 列出任务
```

## 📄 License

MIT License

---

*Built with 🐲*
