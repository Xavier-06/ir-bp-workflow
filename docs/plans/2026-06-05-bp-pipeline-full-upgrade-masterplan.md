# BP Pipeline Full Upgrade Master Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 BP 管线升级为“共享事实页 + 跨 wave 推理 + 投资决策链统稿 + 严格交付门禁”的完整尽调生产系统，彻底解决拼接感、逻辑断裂、看不懂、验证 FAIL 仍交付等问题。

**Architecture:** 当前 BP 管线有效运行的是 Wave 1 四个维度子代理 + Wave 2 竞争与结论；代码里虽有 `bp_统稿` Wave 3 函数，但没有注册进 `BPProfile.phase_handlers`，实际运行的 `phase30_bp_final_assembly` 只是把 Section Package 的 `markdown_draft` 逐段拼接。升级方案改为：先建立 BP Claim Inventory 和 Shared Diligence Page，所有子代理围绕同一份 Research Plan v2 和共享输出页工作；后续 wave 不直接读前序长文，而是读结构化共享页、事实库、claim coverage 和 question slice，再继续推理。

**Tech Stack:** Python, WorkBuddy Agent team_async/sequential dispatch, JSON contracts, Markdown shared page, existing `runtime/profiles/bp_profile.py`, `scripts/bp_subagent_launcher_wb.py`, `scripts/ir_research_planner.py`, `scripts/build_bp_dd_report_docx.py`, verification scripts.

---

## 1. 直接回答三个关键问题

### 1.1 当前 BP 管线实际分几个 wave？

当前代码和文档不完全一致，需要先说清楚：

| 类型 | 代码事实 | 结论 |
|---|---|---|
| Wave 1 | `phase08_dispatch_prepare` 派发 `bp_团队与合规`、`bp_技术与产品`、`bp_行业与供应链`、`bp_估值` | 有效运行 |
| Wave 2 | `phase25_competition_prepare` 派发 `bp_竞争与结论`，并通过 `wave_inputs` 传入前 4 个维度输出路径 | 有效运行 |
| Wave 3 | `runtime/profiles/bp_profile.py` 里有 `_run_bp_synthesis_prepare/_collect` 和 `bp_统稿` prompt | 目前未注册进 `BPProfile.phase_handlers`，实际未跑 |
| Final Assembly | `phase30_bp_final_assembly` 直接读取 `bp_section_packages.json` 并 append `markdown_draft` | 有效运行，但就是拼接 |

所以严格说：**当前有效 wave 是 2 个；文档里写的 Wave 3 统稿在代码里是半废弃/未接入状态。**

这解释了为什么报告像拼接：本来应该由 Wave 3 统稿重新组织投资逻辑，但实际走的是脚本拼接 `markdown_draft`。

### 1.2 后面 wave 是否需要拿前面子代理输出继续推理？

需要，而且非常需要。但不能只是“拿前面 markdown 原文”。

后续 wave 的依赖关系应当是：

| 后续模块 | 必须读取的前序信息 | 原因 |
|---|---|---|
| 竞争与定位 | 产品、技术路线、市场分层、竞品列表、客户/订单线索 | 不知道产品和客户，就无法判断竞品是谁 |
| 估值与回报 | 商业化阶段、市场/SOM、竞品估值、团队风险、融资诉求 | 估值不能在 Wave 1 过早做，必须等商业/市场/竞争证据出来 |
| Deal Breakers | 团队、技术、商业化、合规、估值所有高风险项 | Deal Breaker 是跨维度结论，不是单维度工作 |
| 投资建议 | 所有 core questions 的 answer、反证、data gaps、claim coverage | 投资建议必须来自完整证据链 |
| 最终叙事统稿 | Research Plan、Shared Page、Fact Store、Section Packages、Debate Review | 统稿不能读一堆长文后凭感觉拼接 |

当前 Wave 2 的 `bp_竞争与结论` 已经通过 `wave_inputs` 拿到前 4 个输出，但问题是：

1. 它拿的是长 markdown，不是结构化事实。
2. 没有 shared page 汇总“哪些事实已确认、哪些矛盾、哪些未验证”。
3. 没有 question/claim coverage，无法知道哪些问题已回答、哪些没回答。
4. 估值被放在 Wave 1，太早了；估值应该吃掉前面产品、市场、竞争和团队风险之后再做。

### 1.3 BP 是否需要 IR 那种共享输出 page？

需要，而且 BP 比 IR 更需要。

IR 分析上市公司时，至少有标准化财务、行情、公告、行业数据。BP 尽调面对的是：

- BP 自述可能夸张；
- OCR 可能丢页；
- 创始团队信息可能查不到；
- 客户/订单/收入可能没有公开来源；
- 估值高度依赖假设；
- 不同子代理容易各自编一套逻辑。

所以 BP 必须有一个 **Shared Diligence Page**，作为所有 wave 的单一事实页。后续 agent 不是直接读前一个 agent 的长文，而是先读共享页，再按需读取原始输出。

---

## 2. 新目标：从“维度拼接”改成“共享推理图”

当前错误结构：

```text
团队报告 + 技术报告 + 行业报告 + 估值报告 + 竞争报告
  → markdown_draft 拼接
  → DOCX
```

目标结构：

```text
BP 原文 claims
  → Research Plan v2
  → Shared Diligence Page
  → Wave 1 证据采集
  → Shared Page Refresh
  → Wave 2 跨维度推理
  → Shared Page Refresh
  → Wave 3 投委会叙事/反方审查
  → Narrative Assembly
  → Readability Review
  → Verification Gate
  → DOCX Delivery
```

核心原则：

1. **所有人围绕同一份投资问题工作**，不是各写各的章节。
2. **所有跨 wave 信息先沉淀到 shared page**，不是扔 raw markdown 给下一个人自己消化。
3. **后续 wave 做推理，不做重复采集**。
4. **统稿不是拼接，是按投资决策链重写**。
5. **验证失败绝不交付**。

---

## 3. 推荐 wave 设计：6 个 wave，4 个 agent wave + 2 个 gate wave

### Wave 0：Intake & Planning（无子代理，脚本阶段）

**目标：** 建立任务级“单一真相源”。

阶段：

```text
phase01_document_intake
phase02_company_verify
phase08_bp_claim_extract
phase10_bp_research_plan
phase05_bp_shared_page_init
phase07_bp_fact_store_bootstrap
phase04_presearch
```

产物：

| 产物 | 路径 | 作用 |
|---|---|---|
| OCR 文本 | `jobs/{TASK_ID}/bp_ocr_text.txt` | BP 原文基础 |
| Step0 profile | `jobs/{TASK_ID}/bp_step0_profile.json` | 公司、产品、团队、融资、市场等结构化摘要 |
| Claim Inventory | `jobs/{TASK_ID}/bp_claim_inventory.json` | BP 原文声称清单 |
| Research Plan v2 | `jobs/{TASK_ID}/bp_research_plan.json` | 投资问题、section owner、claim matrix、coverage matrix |
| Shared Page | `jobs/{TASK_ID}/bp_shared_diligence_page.md` | 所有 agent 先读的人类可读共享页 |
| Shared State | `jobs/{TASK_ID}/bp_shared_state.json` | 机器可读共享状态 |
| Fact Store | `jobs/{TASK_ID}/bp_fact_store.json` | 事实库 |

Wave 0 必须能回答：

- BP 说了什么？
- 哪些声称影响投资判断？
- 哪些声称要被谁验证？
- 哪些信息已经来自 BP，哪些来自外部？
- 哪些问题禁止子代理自由发挥？

### Wave 1：Evidence Collection（证据采集 wave）

**目标：** 各自验证事实，不做最终投资建议。

建议角色：

| Agent | 负责问题 | 输出 |
|---|---|---|
| `bp_company_team_compliance` | 主体、股权、实控人、团队履历、诉讼/合规/资质 | Section Package v2 + facts |
| `bp_product_commercial` | 产品矩阵、量产状态、客户/订单/收入/渠道证据 | Section Package v2 + facts |
| `bp_tech_ip_moat` | 技术路线、专利、认证、性能、壁垒 | Section Package v2 + facts |
| `bp_market_supply_chain` | TAM/SAM/SOM、供应链位置、政策、下游场景 | Section Package v2 + facts |

注意：**当前 Wave 1 里的 `bp_估值` 应该挪走。** 估值依赖商业化、市场、竞品、融资阶段，放在 Wave 1 是过早判断，容易拍脑袋。

Wave 1 子代理 brief 必须包含：

```text
1. 必读：bp_shared_diligence_page.md
2. 必读：bp_research_plan.json 中 owner_section == 当前角色的问题
3. 必读：bp_claim_inventory.json 中 owner_section == 当前角色的 claims
4. 必须输出：answers / claims / facts / counter_evidence / data_gaps / narrative_blocks
5. 禁止：写最终投资建议
```

Wave 1 完成后必须跑：

```text
phase12_bp_shared_page_refresh
phase23_bp_wave1_quality_gate
```

### Wave 2：Cross-Dimension Reasoning（跨维度推理 wave）

**目标：** 消化 Wave 1 事实，做依赖前序输出的推理。

建议角色：

| Agent | 依赖 | 负责问题 |
|---|---|---|
| `bp_competition_positioning` | 产品 + 技术 + 市场 | 谁是竞品，标的处在什么位置，替代方案是什么 |
| `bp_valuation_return` | 市场 + 商业化 + 团队 + 竞品 | 估值是否合理、可比公司、MOIC/IRR、融资条件 |
| `bp_customer_revenue_validation` | 产品 + 市场 + 公司验证 | 客户/订单/收入是否真实，有无采购/招投标/合作证据 |
| `bp_dealbreaker_risk` | 所有 Wave 1 输出 | 哪些事实会直接推翻投资，哪些缺口必须补充 |

这里的关键是：Wave 2 **必须拿 Wave 1 的共享页继续推理**，但不要读四篇长文后自己总结。读取顺序应是：

```text
bp_shared_diligence_page.md
bp_shared_state.json
bp_claim_coverage_after_wave1.json
bp_fact_store.json
必要时再读原始 section output
```

Wave 2 完成后跑：

```text
phase27_bp_shared_page_refresh
phase24_bp_claim_coverage_validation
phase29_bp_cross_dimension_consistency_gate
```

### Wave 3：Investment Committee & Red Team（投委会/反方 wave）

**目标：** 形成投资主线，并主动推翻它。

建议角色：

| Agent | 角色 | 输出 |
|---|---|---|
| `bp_investment_committee` | 正方投委会分析师，基于证据形成投资建议 | `bp_investment_thesis.json/md` |
| `bp_red_team_verifier` | 反方审查，专门找证据漏洞、逻辑跳跃、估值幻觉 | `bp_red_team_review.json/md` |

这两个角色都必须读取完整 shared page 和 claim coverage，但职责不同：

- Investment Committee 负责形成：建议、支持理由、投资条件、下一步 DD 清单。
- Red Team 负责问：什么证据会推翻建议？哪些数字没来源？哪些 claim 没回答？哪些结论来自 BP 自述？

Wave 3 完成后跑：

```text
phase29_bp_debate_review
phase36b_bp_thesis_reconciliation
```

`phase36b` 的作用是把正方和反方合并成一个“可交付前提清单”：

```json
{
  "recommendation": "observe|conditional_go|reject|go",
  "supporting_reasons": [],
  "deal_breakers": [],
  "must_verify_before_investment": [],
  "open_data_gaps": [],
  "confidence": "low|medium|high"
}
```

### Wave 4：Narrative Assembly（叙事统稿 wave）

**目标：** 写一份人能看懂的报告，不再让维度文章直接进入最终报告。

这一步建议优先用脚本 deterministic assembly，而不是完全交给 LLM：

```text
phase37_bp_narrative_assembly
```

输入：

- `bp_research_plan.json`
- `bp_shared_state.json`
- `bp_claim_coverage.json`
- `bp_fact_store.json`
- `bp_section_packages.json`
- `bp_investment_thesis.json`
- `bp_red_team_review.json`

输出：

- `bp_final_report.md`
- `bp_final_assembly.json`

如果要用 LLM 统稿，也必须是在脚本生成 report skeleton 后做“可读性重写”，不是自由拼接。

### Wave 5：Quality Gates & Delivery（质量门禁与交付）

阶段：

```text
phase31_bp_readability_review
phase39_bp_adversarial_verification
phase33_delivery
```

硬规则：

- readability FAIL → 不交付。
- verification FAIL → 不交付。
- claim coverage 有 critical not_addressed → 不交付。
- red team 有 unresolved HIGH issue → 不交付。
- final assembly 用了 source_quality=bp 的事实支撑主结论 → 不交付。

---

## 4. Shared Diligence Page 设计

### 4.1 为什么 raw markdown handoff 不够

当前 Wave 2 可以拿到 Wave 1 输出路径，但这不是合格的共享输出机制。问题：

1. **太长**：后续 agent 读不完或只读摘要。
2. **无优先级**：不知道哪些事实是核心投资事实。
3. **无冲突管理**：团队说 A，技术说 B，没人合并。
4. **无 coverage 状态**：不知道哪些 BP claims 已验证。
5. **无读者主线**：后续 agent 继续按自己维度写，最后还是拼。

所以需要 shared page。

### 4.2 Shared Page 文件

建议同时维护 Markdown + JSON：

| 文件 | 用途 |
|---|---|
| `bp_shared_diligence_page.md` | 给人/agent 读，短而清晰 |
| `bp_shared_state.json` | 给脚本/gate 使用，结构化状态 |
| `bp_claim_coverage.json` | 每条 BP claim 当前验证状态 |
| `bp_open_questions.json` | 未解决问题队列 |
| `bp_evidence_conflicts.json` | 冲突事实和口径差异 |

### 4.3 Shared Page Markdown 模板

```markdown
# BP Shared Diligence Page — {entity}

## 1. 当前投资判断快照
- 当前建议：未形成 / 观望 / 有条件推进 / 否决
- 置信度：low / medium / high
- 关键支持理由：
- 关键 Deal Breakers：
- 下一步最重要的 5 个验证动作：

## 2. BP 核心声称验证看板
| Claim ID | BP 声称 | 重要性 | Owner | 当前状态 | 证据等级 | 下一步 |
|---|---|---|---|---|---|---|

## 3. 已确认事实
| Fact ID | 事实 | 来源 | 置信度 | 影响哪个投资问题 |
|---|---|---|---|---|

## 4. 反证与风险
| Risk ID | 风险/反证 | 严重度 | 支撑证据 | 是否 Deal Breaker |
|---|---|---|---|---|

## 5. 数据缺口
| Gap ID | 缺口 | 影响 | Owner | 需要谁补充 |
|---|---|---|---|---|

## 6. 跨维度冲突
| Conflict ID | 冲突 | 涉及模块 | 当前判断 | 处理动作 |
|---|---|---|---|---|

## 7. Wave 交接指令
### Next Wave Must Use
- 必须复用的事实：
- 禁止重复论证的内容：
- 必须继续验证的问题：
- 不能进入主结论的未验证内容：
```

### 4.4 Shared State JSON 模板

```json
{
  "schema_version": "bp_shared_state.v1",
  "task_id": "TASK-...",
  "entity": "...",
  "current_recommendation": {
    "verdict": "undecided|go|conditional_go|observe|reject",
    "confidence": "low|medium|high",
    "supporting_reasons": [],
    "deal_breakers": []
  },
  "claim_status": {
    "C001": {
      "status": "planned|supported|partially_supported|contradicted|unverified|not_addressed",
      "owner": "bp_product_commercial",
      "fact_ids": [],
      "data_gaps": []
    }
  },
  "fact_index": {},
  "open_questions": [],
  "conflicts": [],
  "wave_history": [
    {
      "wave": 1,
      "completed_roles": [],
      "new_facts": [],
      "new_gaps": [],
      "new_conflicts": []
    }
  ]
}
```

### 4.5 Shared Page Refresh

每个 wave 完成后都跑：

```text
scripts/bp_shared_page_builder.py --task-id {TASK_ID} --after-wave {N}
```

它负责：

1. 读取本 wave 的 Section Packages。
2. 合并 facts 到 Fact Store。
3. 更新 claim coverage。
4. 标记 conflicts。
5. 生成下一 wave 的 handoff 指令。
6. 重写 `bp_shared_diligence_page.md`。

这一步是 BP 管线的“中枢神经”。没有它，后续 wave 就会继续各写各的。

---

## 5. 新 wave 依赖图

```text
Wave 0: Intake / Claim Extract / Research Plan / Shared Page Init
   |
   v
Wave 1: Evidence Collection
   ├─ company_team_compliance
   ├─ product_commercial
   ├─ tech_ip_moat
   └─ market_supply_chain
   |
   v
Shared Page Refresh + Wave1 Gate
   |
   v
Wave 2: Cross-Dimension Reasoning
   ├─ competition_positioning  <- product + tech + market
   ├─ valuation_return         <- market + product + competition + team
   ├─ customer_revenue_check    <- product + company + external evidence
   └─ dealbreaker_risk         <- all Wave1 facts
   |
   v
Shared Page Refresh + Claim Coverage Gate
   |
   v
Wave 3: IC / Red Team
   ├─ investment_committee
   └─ red_team_verifier
   |
   v
Thesis Reconciliation
   |
   v
Wave 4: Narrative Assembly
   |
   v
Wave 5: Readability + Verification + Delivery
```

---

## 6. 每个 wave 的输入输出协议

### Wave 1 输入

必须读：

- `bp_shared_diligence_page.md`
- `bp_research_plan.json`
- `bp_claim_inventory.json`
- `bp_fact_store.json`
- `bp_ocr_text.txt`
- `bp_step0_profile.json`
- role-specific presearch files

输出：

- `outputs/bp_{role}.md`
- `outputs/bp_{role}.facts.json`
- `outputs/bp_{role}.section.json`

禁止：

- 写最终投资建议。
- 写自己 owner 之外的 claims。
- 把 BP 自述当外部事实。

### Wave 2 输入

必须读：

- `bp_shared_diligence_page.md` after Wave 1
- `bp_shared_state.json`
- `bp_claim_coverage.json`
- `bp_fact_store.json`
- role-needed raw outputs only when necessary

输出：

- cross-dimension Section Package v2
- updated facts
- updated risks/dealbreakers

禁止：

- 重复 Wave 1 已完成的基础事实采集。
- 引用 Wave 1 的结论但不绑定 fact_ids。
- 估值使用未验证营收/客户/订单作为主假设。

### Wave 3 输入

必须读：

- latest shared page
- claim coverage
- fact store
- debate issues

输出：

- `bp_investment_thesis.json`
- `bp_red_team_review.json`
- `bp_thesis_reconciliation.json`

禁止：

- Red Team 只写泛泛风险。
- Investment Committee 忽略未验证 critical claims。

### Wave 4 输入

必须读：

- `bp_thesis_reconciliation.json`
- all validated section packages
- fact store
- claim coverage

输出：

- `bp_final_report.md`
- `bp_final_assembly.json`

禁止：

- 直接 append markdown_draft。
- 保留“维度报告”生产口吻。
- 一级标题使用各子代理原始标题。

---

## 7. 需要改哪些文件

### 7.1 核心新脚本

| 文件 | 作用 |
|---|---|
| `scripts/bp_claim_extractor.py` | 抽取 BP 原文 claims |
| `scripts/bp_research_planner.py` | 生成 Research Plan v2 |
| `scripts/bp_shared_page_builder.py` | 生成/刷新共享输出页 |
| `scripts/bp_claim_coverage_validator.py` | 检查 claim 覆盖 |
| `scripts/bp_cross_dimension_gate.py` | 检查跨维度冲突 |
| `scripts/bp_narrative_assembler.py` | 投资决策链统稿 |
| `scripts/bp_readability_reviewer.py` | 可读性门禁 |
| `scripts/bp_delivery_gate.py` | 汇总所有 gate，决定是否交付 |

### 7.2 需要修改的现有文件

| 文件 | 修改点 |
|---|---|
| `runtime/profiles/bp_profile.py` | 注册新 phases，重排 wave，移除/替换拼接式 final assembly |
| `scripts/bp_subagent_launcher_wb.py` | 注入 shared page、question slice、claim slice；调整角色和 wave |
| `scripts/build_bp_dd_report_docx.py` | 只接受 gate-passed final report；不从维度 markdown 兜底拼接 |
| `scripts/verification_agent.py` | 加 BP claim coverage/readability/source-quality 检查 |
| `skills/ir-coordinator/references/bp-pipeline.md` | 更新真实阶段、wave、shared page 协议 |
| `skills/ir-coordinator/SKILL.md` | 更新 BP 调度硬规则 |

### 7.3 测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/scripts/test_bp_claim_extractor.py` | claims 抽取 |
| `tests/scripts/test_bp_research_planner.py` | Research Plan v2 validation |
| `tests/scripts/test_bp_shared_page_builder.py` | shared page refresh |
| `tests/scripts/test_bp_subagent_launcher_wb.py` | brief 注入 shared page/question slice |
| `tests/scripts/test_bp_claim_coverage_validator.py` | claim coverage gate |
| `tests/scripts/test_bp_narrative_assembler.py` | 不拼接、投资结论优先 |
| `tests/scripts/test_bp_readability_reviewer.py` | 拼接报告 FAIL |
| `tests/scripts/test_bp_delivery_gate.py` | FAIL 不交付 |
| `tests/scripts/test_bp_profile_wave_flow.py` | phase_handlers 完整 wave 流程 |

---

## 8. 实施任务拆解

### Task 1: 修正 wave 事实与文档不一致

**Files:**
- Modify: `skills/ir-coordinator/references/bp-pipeline.md`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_profile_wave_flow.py`

**Steps:**

1. 写测试：断言 `BPProfile.phase_handlers` 包含所有文档声明的 BP phases。
2. 运行测试，预期失败：当前 synthesis phases 未注册。
3. 决定废弃旧 `phase27_synthesis_prepare/collect`，或重命名接入为新 `phase37_bp_narrative_assembly`。
4. 更新文档，删除“当前 Wave 3 统稿已自动运行”的误导表述。
5. 运行测试通过。

### Task 2: 新增 Shared Page Builder

**Files:**
- Create: `scripts/bp_shared_page_builder.py`
- Test: `tests/scripts/test_bp_shared_page_builder.py`
- Modify: `runtime/profiles/bp_profile.py`

**Steps:**

1. 写测试：给定 claim inventory、fact store、section packages，能生成 markdown 和 json。
2. 实现 `build_shared_state(task_dir)`。
3. 实现 `render_shared_page(state)`。
4. 增加 phase：`phase05_bp_shared_page_init` 和 `phase22/27_bp_shared_page_refresh`。
5. 测试 shared page 包含：投资判断快照、claim 看板、已确认事实、风险、数据缺口、wave 交接指令。

### Task 3: 重排 BP wave

**Files:**
- Modify: `runtime/profiles/bp_profile.py`
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_profile_wave_flow.py`

**Steps:**

1. 把 `bp_估值` 从 Wave 1 移到 Wave 2。
2. Wave 1 改为：team/compliance、product/commercial、tech/ip、market/supply-chain。
3. Wave 2 改为：competition、valuation、customer/revenue、dealbreaker/risk。
4. Wave 3 改为：investment_committee、red_team_verifier。
5. 每个 wave collect 后必须 refresh shared page。
6. 测试每个 Wave 只在依赖满足后派发。

### Task 4: brief 注入 shared page 和 slice

**Files:**
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_subagent_launcher_wb.py`

**Steps:**

1. 写测试：brief 必须包含 `bp_shared_diligence_page.md`。
2. 写测试：brief 必须包含当前 role 的 `core_questions`/`strategic_questions`/`claim_matrix` slice。
3. 实现 `load_bp_research_slice(task_id, role)`。
4. 实现 `load_bp_claim_slice(task_id, role)`。
5. brief 中加入“禁止处理非 owner claims”。
6. 测试 Wave 2 brief 包含上一 wave shared page refresh 结果，而不只是 raw markdown path。

### Task 5: Section Package v2 和 shared-state merge

**Files:**
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_profile_quality_phases.py`

**Steps:**

1. schema 增加 `answers`、`claim_ids_covered`、`narrative_blocks`。
2. 校验所有 answers 绑定 `question_id` 和 `fact_ids`。
3. 校验 `claim_ids_covered` 必须存在于 claim inventory。
4. merge facts 时写入 shared state。
5. data gaps 自动进入 shared page。

### Task 6: Claim Coverage Gate

**Files:**
- Create: `scripts/bp_claim_coverage_validator.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_claim_coverage_validator.py`

**Steps:**

1. 给定 high-priority claim 没被覆盖，测试 FAIL。
2. 给定 claim 被 marked unverified 且有 data_gap，允许进入下一步但最终报告必须披露。
3. critical claim not_addressed 阻断后续 wave。
4. 输出 `bp_claim_coverage.json`。

### Task 7: Cross-Dimension Consistency Gate

**Files:**
- Create: `scripts/bp_cross_dimension_gate.py`
- Test: `tests/scripts/test_bp_cross_dimension_gate.py`

检查：

- 同一公司主体名称是否一致。
- 注册资本、成立时间、股权结构是否一致。
- 市场规模口径是否一致。
- 估值是否使用了未验证收入/客户。
- 竞争结论是否和产品/技术事实冲突。

HIGH conflict 未解决 → 不进入 narrative assembly。

### Task 8: Narrative Assembly 替代拼接

**Files:**
- Create: `scripts/bp_narrative_assembler.py`
- Modify: `runtime/profiles/bp_profile.py:_run_bp_final_assembly`
- Test: `tests/scripts/test_bp_narrative_assembler.py`

**Rules:**

- 第一章必须是投资结论。
- 章节顺序按投资决策链，不按 agent 维度。
- 每章声明回答的问题。
- 每个核心结论绑定 fact_ids 和 claim_ids。
- 未验证 claim 进入 data gaps。
- 不保留子代理原始一级标题。

### Task 9: Readability Gate

**Files:**
- Create: `scripts/bp_readability_reviewer.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_readability_reviewer.py`

**Fail conditions:**

- 前 800 字没有投资建议。
- 出现多个“第一部分/第二部分”体系。
- 出现“根据某维度报告”。
- 同一事实重复解释超过 2 次。
- 技术术语未解释。
- 任一一级章节没有“本章回答的问题”。

### Task 10: Delivery Gate 全收口

**Files:**
- Create: `scripts/bp_delivery_gate.py`
- Modify: `runtime/profiles/bp_profile.py:_run_bp_delivery_inner`
- Test: `tests/scripts/test_bp_delivery_gate.py`

Delivery 必须读取：

- `bp_final_assembly.json`
- `bp_readability_review.json`
- `bp_claim_coverage.json`
- `bp_debate_review.json`
- `bp_cross_dimension_gate.json`
- verification result

任一 hard fail：

```json
{
  "ok": false,
  "deliver_to_user": false,
  "docx_path": "",
  "block_reason": "READABILITY_REWRITE_REQUIRED"
}
```

---

## 9. 验收标准：无死角 gate matrix

| Gate | 阻断条件 | 阶段 |
|---|---|---|
| Research Plan Gate | plan_status != ready / validation.ready != true | 派发前 |
| Shared Page Gate | shared page 缺 claim board / fact board / gaps | 每个 wave 前 |
| Section Package Gate | answer 缺 question_id/fact_ids/claim_ids | 每个 wave 后 |
| Claim Coverage Gate | critical/high claim not_addressed | Wave 2 后 |
| Cross-Dimension Gate | 核心事实冲突未解决 | Wave 2 后 |
| Debate Gate | red team HIGH issue unresolved | Wave 3 后 |
| Narrative Gate | final report 不按投资决策链 | Wave 4 后 |
| Readability Gate | 拼接痕迹/看不懂/开篇无结论 | Delivery 前 |
| Verification Gate | 数字无来源/内部泄露/占位残留 | Delivery 前 |
| Delivery Gate | 任一 gate FAIL | 最终交付 |

---

## 10. 最小可落地版本和完整版本

### MVP：先救命

必须先做：

1. Shared Page Builder。
2. Research Plan v2 Gate。
3. Narrative Assembly 替换拼接。
4. Delivery Gate FAIL 不交付。

做完这四个，报告至少不会再像纯拼接，也不会 FAIL 仍交付。

### Full：真正无死角

完整做：

1. BP Claim Extractor。
2. Shared Page 全 wave 刷新。
3. Wave 重排。
4. Section Package v2。
5. Claim Coverage。
6. Cross-Dimension Consistency。
7. IC + Red Team。
8. Readability Review。
9. Delivery Gate。
10. 文档和测试全覆盖。

---

## 11. 对当前代码的明确改造判断

### 11.1 当前 Wave 1 需要改

当前：

```text
team + tech + industry + valuation
```

建议：

```text
team/compliance + product/commercial + tech/ip + market/supply-chain
```

原因：估值不能早于商业化和市场事实。

### 11.2 当前 Wave 2 需要扩展

当前：

```text
competition/conclusion
```

建议：

```text
competition/positioning + valuation/return + customer/revenue + dealbreaker/risk
```

原因：竞争、估值、客户验证、风险都是跨维度推理，不能压在一个“竞争与结论”agent 里。

### 11.3 当前 Wave 3 需要重接入或删除旧代码

当前 `bp_统稿` 函数存在但未注册，属于危险的“文档说有、实际没跑”。

建议：不要恢复旧 `bp_统稿`，直接替换为 `bp_narrative_assembler.py` + 可选 readability rewrite。

### 11.4 当前 Final Assembly 必须废弃拼接逻辑

当前：

```python
lines.extend([f"## {title}", "", draft, ""])
```

必须替换为：

```python
from scripts.bp_narrative_assembler import assemble_bp_report
result = assemble_bp_report(task_dir)
```

---

## 12. 最终报告应该如何变得连贯

最终报告的每个一级章节都必须满足：

```text
本章回答什么问题？
  → 用哪些 BP claims？
  → 用哪些外部 facts？
  → 有哪些反证/缺口？
  → 对投资建议有什么影响？
```

示例：

```markdown
## 3. 产品与商业化验证

**本章回答：这家公司是否已经从“技术想法”进入“可销售产品”？**

结论：目前只能确认公司存在真空泵相关专利和业务范围，但未找到独立客户、订单或收入证据。因此，商业化状态应标记为“未验证”，不能按已量产企业估值。

证据链：
- BP 声称 C004：已具备 XXX 产品能力。
- 外部事实 F012：公司经营范围包含真空设备制造。
- 外部事实 F019：未检索到公开招投标/客户案例。
- 反证 R003：注册资本和参保人数显示组织规模偏早期。

对投资建议的影响：商业化证据缺失是 P0 尽调项。若创始人无法提供客户试用、订单或第三方测试报告，建议不进入投资。
```

这才是可读的投资报告，不是维度拼接。

---

## 13. 推荐提交顺序

```text
Commit 1: docs(bp): document real BP waves and shared page target architecture
Commit 2: feat(bp): add shared diligence page builder
Commit 3: feat(bp): add BP research plan v2 and dispatch gate
Commit 4: refactor(bp): reorder BP waves around evidence and cross-dimension reasoning
Commit 5: feat(bp): add section package v2 and claim coverage gate
Commit 6: feat(bp): add narrative assembler to replace markdown concatenation
Commit 7: feat(bp): add readability and cross-dimension gates
Commit 8: fix(bp): enforce delivery hard gate before DOCX generation
Commit 9: test(bp): add regression cases for TASK-20260605-002 style failures
```

---

## 14. 最终立场

BP 管线要做到无死角，不能只问“分几个 wave”。真正关键是：

> 每个 wave 完成后，是否把“事实、claim 状态、反证、缺口、当前投资判断”沉淀到共享页，并让下一 wave 基于共享页继续推理。

所以答案是：

1. 当前有效 2 个 wave，旧文档写的 Wave 3 统稿没有真正接入。
2. 后面 wave 必须拿前面输出继续推理，但应该拿 shared page 和 structured state，不应该直接吞 raw markdown。
3. BP 必须做 IR 类似的共享输出页，而且要比 IR 更强：除了 Fact Store，还要有 Claim Board、Coverage Board、Open Questions、Conflicts、Current Thesis。
4. 最终优化的重点是共享推理图，不是增加章节数量。
