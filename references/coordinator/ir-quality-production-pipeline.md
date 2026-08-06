# IR 质量生产型管线

> ⚠️ **编号对齐声明（2026-08-06）**：本文档的 step/phase 编号为历史版本
>（step1_data/step2_industry/step_macro 等旧命名、phase0~phase47 旧清单），
> 与现行管线脱节。现行唯一真相源：step 编号见 `scripts/ir_subagent_launcher_wb.py`
> 的 STEP_DEPS（7 个研究 step，编号保留缺口不重排），phase 清单见
> `runtime/profiles/ir_profile.py`（29 phase）+ README「IR 管线」章节。
> 本文档只保留质量生产理念（Section Package / Fact Store / Debate Review 生产链），
> 不再维护编号清单。

本文件是 IR 管线的新质量协议。目标不是只在交付前拦截坏报告，而是让管线在生产过程中持续生成可验证、可复用、可统稿的研究资产。

## 核心原则

1. **不硬编码任何标的**：所有逻辑必须按 `entity`、`market`、`query`、`task_id` 动态执行。
2. **先研究计划，后检索写作**：派发前必须通过 Research Plan Gate，先明确投资问题、strategic_questions、证据需求和 step owner，再检索和写作。
3. **先事实库，后章节**：关键数字、管理层信息、估值假设必须进入 Fact Store 或作为 data gap 标出。
4. **子代理生产资产，不直接写最终研报**：每个 step 输出 Section Package，而不是自由散文。
5. **统稿器只组装，不新编事实**：Final Assembly 只能使用通过校验的 Section Package。
6. **交付闸门只是保险丝**：质量来自 Research Plan → Fact Store → Section Package → Debate Review 的生产链。

## 新管线阶段

```text
phase0_preflight
  → phase02_company_verify
  → phase03_research_plan
  → phase04_presearch
  → phase15_extract
  → phase12_precompute
  → phase2_fact_store_bootstrap
  → phase4_dispatch_prepare
  → phase4_dispatch_collect
  → phase45_section_package_validation
  → phase46_debate_review
  → phase47_final_assembly
  → phase5_delivery
```

## 新增产物

| 阶段 | 产物 | 用途 |
|---|---|---|
| phase03_research_plan | `data/tasks/{TASK_ID}-research_plan.json` | Research Plan Gate 产物：脚本骨架 + 主控增强 strategic_questions + `plan_status=ready` 校验 |
| phase2_fact_store_bootstrap | `data/tasks/{TASK_ID}-fact_store.json` | 保存可追溯事实、候选事实、冲突记录 |
| 子代理 step | `data/tasks/{TASK_ID}-{step}.md` | Markdown 中必须包含 Section Package JSON block |
| phase45_section_package_validation | `data/tasks/{TASK_ID}-section_packages.json` | 抽取并校验所有 Section Package |
| phase46_debate_review | `data/tasks/{TASK_ID}-debate_review.json` | 找证据不足、无反证、弱来源高置信等问题 |
| phase47_final_assembly | `data/tasks/{TASK_ID}-final_assembly.json` + `final_report.md` | 只组装通过校验的章节包 |

## 子代理输出协议

每个 IR 子代理必须遵守 `instruction_store_ir/_shared_output_protocol.md`。输出文件必须包含结构化 JSON block：

```json
{
  "section_id": "step_name_or_section_id",
  "section_title": "章节标题",
  "key_messages": ["本节最重要的判断"],
  "claims": [
    {
      "claim": "明确判断",
      "fact_ids": ["F-0001"],
      "reasoning": "从事实到判断的推理链",
      "confidence": "high|medium|low",
      "source_quality": "official|institutional|reputable|auxiliary|unknown"
    }
  ],
  "facts_used": ["F-0001"],
  "counter_evidence": ["反向证据或不确定性"],
  "data_gaps": ["仍缺什么事实或来源"],
  "markdown_draft": "可进入最终报告的章节草稿"
}
```

## 各 Agent 新职责

| Agent / Step（现行编号） | 新职责 |
|---|---|
| phase04 研究计划子代理 | 生产市场数据底座（enriched_data_pack）和基础事实卡，不写最终行情散文 |
| step1_industry | 回答行业是否支持投资主线，输出行业事实、竞争图谱、反证 |
| step2_biz | 输出商业模式、产品矩阵、护城河和业务弱点的结构化判断 |
| step3_finance | 输出财务质量、利润修复、现金流、ROIC/ROE 等事实绑定判断 + 成本端原材料价格锚定 |
| step4_mgmt | 验证管理层与治理事实，禁止使用模型记忆补人名和履历 |
| （step5_macro 已于 v3.6 删除） | 宏观传导链由消费方按需取数：step3（成本价格）/step6（折现率）/step8（风险） |
| step6_valuation | 输出可复算估值、假设表、敏感性、防重复计价说明 |
| step7_insight | 从事实中提炼市场分歧、variant view、催化剂和反证 |
| step8_risk | 主动攻击投资主线，输出 bear case、risk triggers、估值影响 |
| phase13 synthesis（统稿） | 只做编辑组装，不新增事实、数字、人名、估值假设 |

## Coordinator 调度要求

1. 调度 IR 任务时必须知道新阶段已存在，不要跳过 `phase03_research_plan` 和 `phase2_fact_store_bootstrap`。
2. 子代理派发 prompt 会由 `ir_subagent_launcher_wb.py` 自动注入共享协议、Research Plan 路径和 Fact Store 路径；`launch_next_wave/launch_step` 必须先确认 Research Plan Gate ready，否则返回 `fix_research_plan` 并禁止派发。
3. dispatch_collect 后必须继续推进质量生产阶段：
   - `phase45_section_package_validation`
   - `phase46_debate_review`
   - `phase47_final_assembly`
4. 如果 Debate Review 返回 `REWRITE_REQUIRED`，应优先定向重写失败 step，而不是强行交付。
5. Final Assembly 失败时不能生成或交付 DOCX。

## 硬禁区

- 禁止无来源数字进入最终结论。
- 禁止使用低质量来源支撑核心财务、管理层和估值结论。
- 禁止子代理只输出散文而没有 Section Package。
- 禁止 step8_master 新增事实。
- 禁止为了过审删除反向证据。
- 禁止把当前样本公司特例写死到管线或 skill 中。
