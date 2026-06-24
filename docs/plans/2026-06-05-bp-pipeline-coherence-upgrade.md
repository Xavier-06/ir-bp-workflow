# BP 管线连贯性与可读性升级方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 BP 管线从“五个维度章节拼接器”升级为“围绕投资决策树组织证据、论证和结论的 BP 尽调生产系统”。

**Architecture:** 当前 BP 报告的断裂不是文风问题，而是生产链断裂：Research Plan 没有形成任务级投资问题契约，子代理按维度各写各的，Final Assembly 只是顺序拼接 markdown_draft。升级方案采用 IR 质量生产管线的核心机制：Research Plan Gate → BP Claim Matrix → Fact Store → Section Package → Narrative Assembly → Adversarial Gate。

**Tech Stack:** Python runtime profiles, WorkBuddy Agent dispatch, JSON contracts, Markdown/DOCX generation, existing `runtime/profiles/bp_profile.py`, `scripts/bp_subagent_launcher_wb.py`, `scripts/ir_research_planner.py` pattern, `scripts/verification_agent.py`.

---

## 0. 诊断结论

用户反馈“像拼接、逻辑不连贯、看不懂”是准确的。代码层面已经验证：

- `runtime/profiles/bp_profile.py::_run_bp_final_assembly()` 当前核心逻辑是遍历 `bp_section_packages.json`，把每个 package 的 `markdown_draft` 依次追加到 `bp_final_report.md`。
- 这不是“像拼接”，而是实现上就是拼接。
- 当前 BP Research Plan 只有静态模板字段：`due_diligence_questions`、`bp_claims_to_verify`、`required_evidence_types`、`section_requirements`、`plan_status`。
- 它缺少 IR 管线里真正让报告连贯的契约：`core_questions`、`strategic_questions`、`coverage_matrix`、`validation.ready`、按 section 切分的 question slice。
- 子代理 brief 只是告诉每个维度“写一份专业章节”，并列出输入文件；没有强约束它回答哪些投资问题、引用哪些事实、如何服务最终投资结论。
- Delivery gate 虽然已经尝试对抗验证，但曾出现验证 FAIL 后仍有 DOCX 交付的问题，说明后台 result 消费、旧产物复用、状态推进之间存在硬门禁漏洞。

根因不是“prompt 不够长”，而是**报告生产对象错了**：现在生产的是“维度文章”，不是“投资论证”。

---

## 1. 目标报告形态

BP 尽调报告不应该按“团队、技术、行业、估值、竞争”机械并排。投资人真正想看的是：

1. **这家公司到底做什么？** 产品、客户、场景、商业化阶段。
2. **BP 里哪些核心声称是真的？哪些不成立？** 一条一条验证。
3. **如果声称成立，为什么这是一个好投资？** 市场空间、进入窗口、技术/产品壁垒、客户验证。
4. **如果声称不成立，会怎么推翻投资逻辑？** Deal breakers、反证、data gaps。
5. **现在该不该投？以什么条件继续？** 进入尽调 / 观望 / 否决，附验证清单。

推荐最终报告结构改为：

```text
1. 投资结论一页纸
   - 建议：投 / 有条件推进 / 观望 / 否决
   - 3 条支持理由
   - 3 条 Deal Breakers
   - 下一步尽调清单

2. 公司与 BP 核心声称地图
   - 公司主体、产品、融资诉求
   - BP claims table：声称 / 所属主题 / 重要性 / 验证结论 / 证据等级

3. 产品与商业化验证
   - 公司卖什么
   - 产品是否存在
   - 是否量产/交付/客户验证
   - 收入、订单、渠道是否有证据

4. 技术与壁垒验证
   - 技术路线在行业里处于什么位置
   - 专利/认证/性能是否支持 BP 声称
   - 技术壁垒是不是能转化为商业壁垒

5. 市场与竞争验证
   - TAM/SAM/SOM 三档推算
   - 竞品和替代方案
   - 标的可获取份额是否合理

6. 团队、治理与合规
   - 实控人、核心团队、股权结构
   - 法律、资质、知识产权、经营异常

7. 估值与回报模型
   - BP 估值是否和阶段匹配
   - 可比公司/一级市场对标
   - MOIC/IRR 敏感性

8. 反向论证与 Deal Breakers
   - 哪些事实能推翻投资
   - 缺失证据对应的风险等级

9. 最终建议与尽调清单
   - 投资条件
   - 必做验证
   - 需要创始人补充的材料

10. 来源与附录
```

核心变化：报告按“投资决策链”组织，维度只是证据来源，不是最终章节顺序。

---

## 2. 总体架构：BP Quality Production v2

新管线阶段建议如下：

```text
phase01_document_intake
  → phase02_company_verify
  → phase08_bp_claim_extract
  → phase10_bp_research_plan
  → phase04_presearch
  → phase07_bp_fact_store_bootstrap
  → phase08_dispatch_prepare
  → phase09_dispatch_collect
  → phase25_competition_prepare
  → phase25_competition_collect
  → phase11_bp_fact_store_merge
  → phase34_bp_claim_coverage_validation
  → phase26_bp_section_package_validation
  → phase29_bp_debate_review
  → phase37_bp_narrative_assembly
  → phase31_bp_readability_review
  → phase33_delivery
```

新增的关键层：

| 阶段 | 作用 |
|---|---|
| `phase08_bp_claim_extract` | 从 OCR、Step0、BP 结构化内容中抽取 BP 原文声称，形成 claim inventory。 |
| `phase10_bp_research_plan` | 脚本骨架 + 主控增强，生成投资问题、claim matrix、coverage matrix、section owner。 |
| `phase34_bp_claim_coverage_validation` | 检查每条 high-priority BP claim 是否有结论：支持、部分支持、反驳、未验证。 |
| `phase37_bp_narrative_assembly` | 不再拼 markdown_draft，而是按投资叙事骨架重组 claims、facts、counter_evidence。 |
| `phase31_bp_readability_review` | 专门检查逻辑连贯性、读者可理解性、标题层级、重复与跳跃。 |

---

## 3. 数据契约设计

### 3.1 BP Claim Inventory

新增文件：`jobs/{TASK_ID}/bp_claim_inventory.json`

```json
{
  "schema_version": "bp_claim_inventory.v1",
  "task_id": "TASK-...",
  "entity": "目标公司",
  "claims": [
    {
      "claim_id": "C001",
      "claim_text": "BP 原文声称",
      "claim_type": "team|product|technology|market|customer|financial|valuation|competition|compliance",
      "source_location": "page/section/chunk id",
      "importance": "critical|high|medium|low",
      "decision_relevance": "为什么影响投资判断",
      "owner_section": "bp_product_commercial|bp_tech_moat|bp_market_competition|bp_team_compliance|bp_valuation_return",
      "required_evidence": ["external_source", "bp_source", "database", "customer_evidence"],
      "status": "planned"
    }
  ]
}
```

说明：

- BP 管线必须先知道“BP 说了什么”，再验证“说得对不对”。
- 当前报告问题之一是很多“BP 声称验证”其实不确定是否来自 BP 原文，甚至出现“由于 BP OCR 失败，以下基于公开信息和行业常识”的表述。这会让报告失去可信度。
- Claim Inventory 必须绑定 BP 原文位置；无法定位原文的声称，不得写成“BP 声称”。

### 3.2 BP Research Plan v2

新增/替换文件：`jobs/{TASK_ID}/bp_research_plan.json`

必须包含：

```json
{
  "schema_version": "bp_research_plan.v2",
  "prepared_by": "script_scaffold_plus_orchestrator_enrichment",
  "plan_status": "ready|blocked",
  "validation": {"ready": true, "errors": []},
  "core_questions": [],
  "strategic_questions": [],
  "claim_matrix": {},
  "section_requirements": {},
  "fact_requirements": [],
  "coverage_matrix": {},
  "report_narrative": {}
}
```

推荐 core questions：

| ID | 问题 | Owner |
|---|---|---|
| BQ1 | 这家公司卖什么，是否真实存在且已进入商业化？ | `bp_product_commercial` |
| BQ2 | BP 的关键技术/产品性能声称是否有独立证据支撑？ | `bp_tech_moat` |
| BQ3 | 市场空间、增长率、SOM 和客户切入路径是否合理？ | `bp_market_competition` |
| BQ4 | 团队、股权、合规和资源是否支持执行？ | `bp_team_compliance` |
| BQ5 | 融资诉求、估值和预期回报是否可接受？ | `bp_valuation_return` |
| BQ6 | 哪些 Deal Breakers 会推翻投资建议？ | `bp_risk_dealbreakers` |
| BQ7 | 最终建议是什么，下一步尽调应验证什么？ | `bp_investment_committee` |

区别于当前版本：每个问题必须有 owner、required_fact_keys、supporting_sections、decision_relevance。

### 3.3 Section Package v2

当前 package 有 `claims`，但没有明确回答 Research Plan 问题，也没有 claim coverage。升级为：

```json
{
  "schema_version": "bp_section_package.v2",
  "section_id": "bp_tech_moat",
  "answers": [
    {
      "question_id": "BQ2",
      "answer": "结论",
      "confidence": "high|medium|low",
      "fact_ids": ["F001"],
      "claim_ids_covered": ["C003", "C004"],
      "counter_evidence": [],
      "data_gaps": []
    }
  ],
  "claims": [],
  "facts_used": [],
  "narrative_blocks": [
    {
      "block_id": "tech_route",
      "role_in_report": "evidence|context|risk|conclusion",
      "markdown": "..."
    }
  ]
}
```

关键变化：从“给我一段 markdown_draft”变成“给我结构化答案 + 可复用叙事块”。

---

## 4. 统稿方式重构：从 Markdown 拼接到 Narrative Assembly

当前 `phase37` 的问题：

```python
for item in section_index.get("packages", []):
    draft = package.get("markdown_draft")
    lines.extend([title, draft])
```

这必然导致：

- 标题体系冲突：每个维度内部都有“第一部分/第二部分”。
- 读者路径混乱：先看到竞争，再看到团队，再技术，再估值，缺少投资主线。
- 重复：市场规模、竞品、风险在多个维度反复出现。
- 结论散落：每个维度都有自己的建议，但最终没有统一投资建议。
- 数据来源断裂：事实 ID 只在附录列出，正文不解释为什么支撑结论。

目标实现：新增 `scripts/bp_narrative_assembler.py`，按固定投资叙事 schema 生成 `bp_final_report.md`。

### Narrative Assembly 输入

- `bp_research_plan.json`
- `bp_claim_inventory.json`
- `bp_fact_store.json`
- `bp_section_packages.json`
- `bp_debate_review.json`

### Narrative Assembly 输出

- `bp_final_report.md`
- `bp_final_assembly.json`

### Assembly 规则

1. 先写最终投资结论，不从背景开始。
2. 每个结论必须反查：`question_id → answer → claim_ids → fact_ids`。
3. 同一事实只解释一次，其它地方交叉引用。
4. 未验证 claim 不得进入主结论，只能进入 data gaps 或尽调清单。
5. `source_quality=bp` 的事实只能证明“BP 这么说”，不能证明“事实成立”。
6. 每章开头写“本章回答什么投资问题”。
7. 每章结尾写“对投资建议的影响”。
8. 最终报告必须有“论证链摘要”：

```text
投资建议 → 支持理由 → 关键证据 → 反向证据 → 待验证事项 → 下一步动作
```

---

## 5. Readability Review：专门解决“看不懂”

新增 `phase31_bp_readability_review`，不要和事实验证混在一起。

检查项：

| 检查 | 失败条件 |
|---|---|
| 标题层级 | 出现多个互相冲突的“第一部分/第二部分”体系 |
| 开篇可读性 | 前 800 字没有给出投资建议、核心理由、Deal Breakers |
| 章节目的 | 任一一级章节没有说明回答哪个投资问题 |
| 逻辑跳跃 | 结论前没有事实或推理链 |
| 重复率 | 同一市场规模、竞品格局、注册资本等内容重复出现超过 2 次 |
| 来源可读性 | 正文关键数字没有脚注或 fact_id 映射 |
| 读者对象 | 出现大量行业术语但无解释，尤其技术章节 |
| 拼接痕迹 | 出现“据技术维度分析”“根据团队维度报告”等内部生产口吻 |

输出：`bp_readability_review.json`

```json
{
  "verdict": "PASS|REWRITE_REQUIRED",
  "issues": [
    {
      "severity": "HIGH",
      "location": "chapter 3",
      "issue": "技术结论出现前没有解释产品是什么",
      "required_action": "将产品矩阵提前，技术解释改为支持产品竞争力"
    }
  ]
}
```

Delivery 必须要求：

- `bp_debate_review.verdict in (PASS, WARN)`
- `bp_readability_review.verdict == PASS`
- `verification_agent.verdict != FAIL`
- `bp_final_assembly.ok == true`

任一失败，不生成 DOCX、不复制桌面、不发送通知。

---

## 6. 文件级实施计划

### Task 1: 新增 BP Claim Extractor

**Files:**
- Create: `scripts/bp_claim_extractor.py`
- Test: `tests/scripts/test_bp_claim_extractor.py`
- Modify: `runtime/profiles/bp_profile.py`

**实现要点:**

- 输入：`bp_ocr_text.txt`、`bp_step0_profile.json`、`body_content/*.json`。
- 输出：`bp_claim_inventory.json`。
- 第一版不用 LLM，先基于 Step0 结构化字段和关键词规则抽取：产品、技术、市场、客户、融资、估值、团队、资质。
- 每条 claim 必须有 `claim_id`、`claim_text`、`claim_type`、`importance`、`owner_section`。
- 如果 OCR/Step0 无法定位 BP 原文，则标记 `source_location="unlocated"`，并在 Research Plan 中降级为 data gap。

**验收标准:**

- 给定最小 Step0 profile，能生成至少 5 类 claim。
- `claim_id` 稳定、唯一。
- 空输入不会假造 claim，只输出空 claims + warning。

### Task 2: 新增 BP Research Planner v2

**Files:**
- Create: `scripts/bp_research_planner.py`
- Test: `tests/scripts/test_bp_research_planner.py`
- Modify: `runtime/profiles/bp_profile.py:_run_research_plan`

**实现要点:**

- 参考 `scripts/ir_research_planner.py` 的 contract，而不是当前硬编码 dict。
- 提供：
  - `build_bp_research_plan()`
  - `build_bp_strategic_questions()`
  - `validate_bp_research_plan_ready()`
  - `prepare_bp_research_plan()`
- 将 claim inventory 映射到 `claim_matrix`。
- 每个 high/critical claim 必须有 owner section。
- plan 写入 `prepared_by="script_scaffold_plus_orchestrator_enrichment"`。

**验收标准:**

- plan 缺 `strategic_questions` 时 validation fail。
- plan 缺 `coverage_matrix` 时 validation fail。
- high-priority claim 无 owner 时 validation fail。
- 正常输入生成 `validation.ready == true`。

### Task 3: 派发前加入 BP Research Plan Gate

**Files:**
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_subagent_launcher_wb.py`

**实现要点:**

- 新增 `ensure_bp_research_plan_ready(task_id, entity, input_file, market)`。
- `_spawn_one()` 前必须检查 gate。
- brief 中明确注入当前 role 的 slice：
  - `core_questions` where owner == role
  - `strategic_questions` where owner == role
  - `claim_matrix` where owner == role
  - `section_requirements[role]`
- 子代理不得自由扩写与自己无关的内容。

**验收标准:**

- plan 不 ready 时 `_spawn_one()` 返回 blocked，不写 spawn receipt。
- brief 中包含 `必须回答的问题` 和 `必须验证的 BP claims`。
- brief 中不只是列 plan 路径。

### Task 4: Section Package 升级到 v2

**Files:**
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Modify: `runtime/profiles/bp_profile.py:_validate_bp_section_package`
- Test: `tests/scripts/test_bp_profile_quality_phases.py`

**实现要点:**

- sidecar schema 新增 `answers`、`claim_ids_covered`、`narrative_blocks`。
- 校验：
  - high-priority question 必须回答。
  - high/critical claim 必须覆盖或明确进入 data_gaps。
  - answer 必须绑定 fact_ids。
  - claim_ids 必须存在于 inventory。

**验收标准:**

- 只有 markdown_draft、没有 answers 的 package fail。
- claim_id 不存在 fail。
- high-priority question 漏答 fail。

### Task 5: 新增 Claim Coverage Validation

**Files:**
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_claim_coverage_validation.py`

**实现要点:**

- 新 phase：`phase34_bp_claim_coverage_validation`。
- 输入：claim inventory + section packages。
- 输出：`bp_claim_coverage.json`。
- 每条 claim 状态：`supported|partially_supported|contradicted|unverified|not_addressed`。
- `critical/high` claim 出现 `not_addressed` 时阻断。

**验收标准:**

- 任一 critical claim 未覆盖 → `ok=false`。
- 未验证 claim 可通过，但必须进入 final report 的 data gaps。

### Task 6: 新增 Narrative Assembler

**Files:**
- Create: `scripts/bp_narrative_assembler.py`
- Modify: `runtime/profiles/bp_profile.py:_run_bp_final_assembly`
- Test: `tests/scripts/test_bp_narrative_assembler.py`

**实现要点:**

- 不再按 section 顺序 append markdown_draft。
- 按固定投资决策报告结构生成 markdown。
- 从 Section Package v2 中取 `answers` 和 `narrative_blocks`。
- 生成 `investment_thesis_summary`：recommendation、supporting_reasons、deal_breakers、next_diligence。
- 对重复 facts 做去重。
- 对无证据 claims 放入 data gaps。

**验收标准:**

- 最终报告第一章必须是投资结论。
- 不出现“基于已通过结构化校验的章节保守组装”。
- 不出现“据某维度分析”。
- 同一个 fact_id 不在正文重复解释超过 2 次。

### Task 7: 新增 Readability Review Gate

**Files:**
- Create: `scripts/bp_readability_reviewer.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_readability_reviewer.py`

**实现要点:**

- 基于规则先做 deterministic 检查：标题层级、开篇结论、内部术语、重复标题、脚注密度、章节目标句。
- 输出 `bp_readability_review.json`。
- `HIGH` issue 阻断 delivery。

**验收标准:**

- 拼接式报告样例必须 FAIL。
- 第一章非投资结论必须 FAIL。
- 出现“维度报告/子代理/phase/dispatch”必须 FAIL。

### Task 8: 修复 Delivery Hard Gate

**Files:**
- Modify: `runtime/profiles/bp_profile.py:_run_bp_delivery_inner`
- Modify: `scripts/heavy_phase_bg.py` if cached result handling needs hardening
- Test: `tests/scripts/test_bp_delivery_gate.py`

**实现要点:**

- delivery 前读取：
  - `bp_final_assembly.json`
  - `bp_debate_review.json`
  - `bp_claim_coverage.json`
  - `bp_readability_review.json`
  - adversarial verification result
- 任一 FAIL，不生成 DOCX。
- 如果旧 DOCX 已存在但当前 gate fail，不能复用旧文件。
- audit 中记录所有 gate verdict。

**验收标准:**

- verification FAIL 时 `deliver_to_user=false`，`docx_path=""`。
- readability FAIL 时不生成 DOCX。
- debate REWRITE_REQUIRED 时不生成 DOCX。

### Task 9: 更新文档与协调器说明

**Files:**
- Modify: `skills/ir-coordinator/references/bp-pipeline.md`
- Modify: `skills/ir-coordinator/SKILL.md`
- Modify: `skills/ir-reporter/SKILL.md` if BP reporter behavior is referenced

**实现要点:**

- 文档不再写旧版 phase20→phase25→phase40 直达流程。
- 明确 BP 质量链：claim extract → plan gate → claim coverage → narrative assembly → readability gate。
- 明确失败时必须定向重写，不得强行交付。

**验收标准:**

- 文档阶段与 `BPProfile.phase_handlers` 一致。
- 明确“BP 不得拼接 markdown_draft”。

---

## 7. MVP 优先级

### P0：必须先修

1. `bp_research_planner.py` + Research Plan Gate。
2. `bp_narrative_assembler.py` 替代当前拼接式 final assembly。
3. Delivery hard gate，FAIL 不交付。

不做这三项，继续优化 prompt 没意义。

### P1：第二轮

1. Claim extractor 和 claim coverage validation。
2. Section Package v2。
3. Readability Review Gate。

### P2：第三轮

1. 更好的 BP 原文定位。
2. 自动生成创始人补充材料清单。
3. 投委会版一页纸摘要模板。
4. DOCX 视觉层优化。

---

## 8. 验收用例

用当前失败样本 `TASK-20260605-002` 做回归测试。

### Case A: 拼接报告必须失败

输入当前 `bp_final_report.md`。

期望：

- Readability Review FAIL。
- 原因包括：第一章不是投资结论、标题体系冲突、维度拼接痕迹。

### Case B: Research Plan v1 必须失败

输入当前 `bp_research_plan.json`。

期望：

- `validate_bp_research_plan_ready()` 返回 false。
- errors 包含：
  - `core_questions_missing`
  - `strategic_questions_missing`
  - `coverage_matrix_missing`
  - `validation_missing`

### Case C: Verification FAIL 不得交付

输入当前 verification 结果：`VERDICT: FAIL`。

期望：

- `phase33_delivery.ok == false`
- `deliver_to_user == false`
- `docx_path == ""`

### Case D: 新报告首章必须可读

输入新 assembly 输出。

期望首 800 字包含：

- 投资建议
- 关键支持理由
- 关键 Deal Breakers
- 下一步尽调动作

---

## 9. 推荐实施顺序

```text
Commit 1: feat(bp): add BP research plan v2 gate
Commit 2: feat(bp): inject research question slices into BP briefs
Commit 3: feat(bp): replace markdown concatenation with narrative assembler
Commit 4: fix(bp): enforce delivery hard gates for verification and readability
Commit 5: feat(bp): add claim coverage validation
Commit 6: docs(bp): update BP quality-production pipeline reference
```

每个 commit 后跑对应测试，避免把管线迁移和 bugfix 混在一起。

---

## 10. 最终判断

这次 BP 报告质量差的核心原因是：

> BP 管线目前在生产“章节”，但投资报告需要生产“论证链”。

优化方向不是让每个维度写得更长，而是让所有维度共同服务一棵投资决策树：

```text
投资建议
  ├── 为什么值得看
  ├── 为什么现在不能投/可以投
  ├── 哪些 BP 声称被验证
  ├── 哪些声称被反驳或未验证
  ├── 哪些事实会改变结论
  └── 下一步该问创始人/客户/供应商什么
```

只有这样，报告才会从“材料堆叠”变成“投委会能读懂的判断”。
