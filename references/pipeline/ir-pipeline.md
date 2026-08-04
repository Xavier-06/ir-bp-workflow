# IR 管线详细流程

> 本文档与代码真相源对齐：`runtime/profiles/ir_profile.py`（phase 注册表）+
> `scripts/ir_subagent_launcher_wb.py`（STEP_DEPS/LAUNCH_WAVES）。
> **2026-08-04 v3.2 重写**：旧版描述的 presearch/extract/脚本 research plan 骨架均已删除。

## 管线阶段（29 phases，实际执行顺序）

```
01  phase01_preflight               — 环境检测 + 任务注册 + 指令库/角色校验 + research plan 状态检查
02  phase02_company_verify          — 公司验证 + 估值数据 [heavy_bg，kernel 内部自动等待]
    （phase03/phase05 编号永久空缺：presearch + extract 已于 v5.3 删除，
      搜索由 phase04 research plan 子代理全权执行）
03  phase04_research_plan           — ★ 派发研究计划子代理（指令: instruction_store_ir/ir_research_plan.md）
    │                                 → 返回 needs_dispatch，Coordinator 手动派发子代理
    └ 子代理产出: ir_research_plan.json + benchmark_skeleton.json（大行研报骨架）
                  + enriched_data_pack.json（替代旧 step1_data 数据包）
04  phase04_research_plan_collect   — 校验并落盘子代理 plan（v3.2: 缺失/校验失败=硬失败，无脚本兜底）
05  phase06_fact_store_bootstrap    — 初始化 Fact Store
06  phase07_precompute              — 预计算引擎: financial_metrics + sector_benchmarks
07  phase08_dispatch_prepare        — launch_next_wave() 发射 Wave1 → needs_dispatch
08  phase09_dispatch_collect        — 检查子代理输出 + 质量门禁（collect/prepare 成对循环推进 4 波）
09-12 phase09_wave1~4_evidence_gate — 每个 wave 的证据完整性门禁
13  phase10_fact_store_merge        — 合并各 step fact sidecar 到 Fact Store
14-18 phase10_shared_state_refresh  — 跨 Wave 共享状态摘要（+ wave1~4 各一份）
19  phase11_section_package_validation — 抽取并校验各 step 的 Section Package
20  phase12_debate_review           — 中途批判复盘（证据不足/无反证/弱来源高置信）
21  phase13_synthesis_prepare       — 构建统稿子代理 manifest（指令: instruction_store_ir/ir_统稿.md）→ needs_dispatch
22  phase13_synthesis_collect       — 校验统稿输出完整性 + 脚注密度 repair
23  phase14_final_assembly          — 只组装通过校验的 Section Package，不新增事实
24  phase14_readability_review      — 可读性审查（机器ID泄漏/长段落/脚注完整性）
25  phase14_claim_coverage          — claim ↔ fact 覆盖校验
26  phase14_cross_dimension_gate    — 跨 step 指标一致性与逻辑矛盾检查
27  phase14_delivery_gate           — 汇总上游 gate 的最终交付门禁（FAIL 记 deferred，不阻断）
28  phase14_investment_judgment     — 投资判断汇总（明确建议 + 逻辑 + 风险）
29  phase15_delivery                — 对抗验证 + DOCX + 交付 [heavy_bg，kernel 内部自动等待]
```

**heavy phase 只有两个**：phase02_company_verify、phase15_delivery。kernel 内部 block-wait，
Coordinator 不需要轮询 bg_pid，但 Bash 必须设 `timeout=600000`（否则 Bash 120s 切断会话）。

## Research Plan（phase04，v3.2 起子代理唯一产出）

- **指令库**：`instruction_store_ir/ir_research_plan.md`（ir_profile 读模板 + token 替换渲染）
- **Step 0.5 大行研报骨架（最高优先级）**：IMA 自建研报库 `001a89fa4b807b92`，
  搜 GS/MS/JPM/Citi/HSBC/UBS/BofA/Bernstein 最新研报 → fetch 全文 → 解析为
  `{task_id}-benchmark_skeleton.json`（key_debates/财务预测/估值方法/情景分析）
- **Step 0.6 market_anchor**：市场共识锚（一致预期/目标价/隐含假设，研报时效 ≤30 天铁律）
- **输出**：plan 含 valuation_paradigm（6 选 1）/ key_debates（P0-P2）/ dim_priority /
  report_type（deep_dive/event_update/earnings_note，决定 wave 裁剪）
- **脚本不再生成 plan**：`ir_research_planner.py` 只剩契约校验（normalize/validate/load/path）。
  Coordinator 不要手动调任何 plan 生成函数。

## Wave 编排（v3.1 研究链 4 波，2026-08-04 回归大行真实研究顺序）

```
Wave 1: step1_industry, step2_biz, step5_macro     — 背景层（并行）
Wave 2: step3_finance, step4_mgmt                  — 预测与验证（消费 Wave1）
Wave 3: step6_valuation                            — 估值收口（消费预测）
Wave 4: step7_insight, step8_risk                  — 预期差收口
统稿:   phase13 synthesis（ir_统稿.md），不在 LAUNCH_WAVES 内
```

> 估值是研究的**收口**不是起点——报告开头放目标价只是"结论前置"的成文技巧。
> 依据：4 篇大行公司首发研报全文解剖（爱建/先锋精科、交银国际/鸣鸣很忙、东方/中国太保、Bernstein/Booking）。

### Step 依赖

| Step | 依赖 | 角色指令 |
|------|------|---------|
| step1_industry | — | step1_industry.md |
| step2_biz | — | step2_biz.md |
| step5_macro | — | step5_macro.md |
| step3_finance | step1_industry, step2_biz | step3_finance.md |
| step4_mgmt | step2_biz | step4_mgmt.md |
| step6_valuation | step3_finance, step1_industry, step2_biz, step5_macro | step6_valuation.md |
| step7_insight | step1~step6 全部 | step7_insight.md |
| step8_risk | step3_finance, step4_mgmt, step5_macro, step6_valuation | step8_risk.md |

### 报告类型分流（active_waves 白名单）

| report_type | 波次 | 场景 |
|-------------|------|------|
| deep_dive（默认） | 全量 4 波 | 完整投研 |
| event_update | Wave1+2+3 | 订单/新品/中标/合作快速跟踪 |
| earnings_note | Wave2+3 | 财报点评（仅预测+估值，大行点评模式） |

report_type 由 phase04 子代理判定（`_backfill_thesis_fields` 按 query 关键词兜底），
白名单外依赖由 `deps_ready(active_steps)` 自动降级放行。Coordinator 无需干预。

## 核心 API

```python
from ir_subagent_launcher_wb import (
    launch_next_wave,      # 发射当前 wave，返回 team 派发指令（sequential=True 每次 1 个 step）
    get_pipeline_status,   # 管线状态快照
    finalize_pipeline,     # 交付收尾（质检 → DOCX → 桌面复制）
)
```

## 提交与执行

```bash
# ⚠️ 所有 python3 管线命令必须带 cd 前缀（Bash 每次调用是独立 shell）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator submit \
  --entity "标的名称" --market cn --query "研究重点"

cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX
# ⚠️ Bash 必须设 timeout=600000：kernel block-wait heavy phase（phase02/15）
```

## 执行伪代码（Coordinator 循环）

```python
# 阶段一：execute 自动跑 preflight → company_verify[heavy 自动等] → phase04 派发暂停
result = execute(job_id)  # → needs_dispatch=True，返回 research plan 子代理派发指令
# dispatch_info: subagent_type=general-purpose, connectorIds=[westock-mcp, tyc-mcp, ima-mcp]

# 阶段二：派发研究计划子代理（ir-research-planner），等其写完 ir_research_plan.json
Agent(name='ir-research-planner', team_name=f'ir-{task_id}', mode='bypassPermissions',
      subagent_type='general-purpose', prompt=result['instruction'])

# 阶段三：继续 execute，collect 校验 plan → fact_store → precompute → Wave1 prepare 暂停
result = execute(job_id, start_phase='phase04_research_plan_collect')

# 阶段四：team 模式 sequential 派发 4 波 8 step
team_create(team_name=f"ir-{task_id}")
while True:
    r = launch_next_wave(task_id, entity, query, market, sequential=True)
    if r['all_done']: break
    for inst in r['task_tool_instructions']:  # sequential 模式最多 1 个
        Agent(name=inst['name'], team_name=f"ir-{task_id}", mode='bypassPermissions',
              subagent_type='general-purpose', prompt=inst['prompt'])
        # 轮询 inst['output_path'] 就绪后再派发下一个（超时重派，最多 2 次）
team_delete()

# 阶段五：质量链推进（fact_store_merge → gates → debate_review）
execute(job_id, start_phase='phase10_fact_store_merge')   # → phase13 synthesis_prepare 暂停

# 阶段六：派发统稿子代理（ir_统稿.md），完成后继续
execute(job_id, start_phase='phase13_synthesis_collect')  # → phase14 各 gate → phase15 delivery[heavy 自动等]

# 阶段七：交付
finalize_pipeline(task_id, entity, market)
```

## 质量资产清单

| 资产 | 路径（data/tasks/） | 产出者 |
|------|------|--------|
| Research Plan | `{TASK_ID}-research_plan.json` | phase04 子代理（collect 落盘） |
| 大行研报骨架 | `{TASK_ID}-benchmark_skeleton.json` | phase04 子代理 Step 0.5 |
| 数据包 | `{TASK_ID}-enriched_data_pack.json` | phase04 子代理（替代旧 step1_data） |
| Fact Store | `{TASK_ID}-fact_store.json` | phase06 初始化，phase10 合并 |
| Section Packages | `{TASK_ID}-section_packages.json` | phase11 抽取校验 |
| Debate Review | `{TASK_ID}-debate_review.json` | phase12 |
| Final Assembly | `{TASK_ID}-final_assembly.json` | phase14 |
| 最终报告 | `{TASK_ID}-final_report.md` | phase13 统稿 |

子代理 brief 自动注入 Research Plan（含 market_anchor/valuation_paradigm/key_debates）、
enriched_data_pack、Fact Store 和共享输出协议（_shared_output_protocol.md）。
子代理生产 Section Package，不直接写最终研报。

## 预计算数据（phase07 输出）

| 引擎 | 输出文件 | 使用者 |
|------|---------|--------|
| financial_metrics | `{TASK_ID}_precompute_financial_metrics.json/.md` | step3_finance, step6_valuation |
| sector_benchmarks | `{TASK_ID}_precompute_sector_benchmarks.json/.md` | step1_industry, step7_insight |

子代理 brief 含预计算数据路径，应优先读取再按需补搜。

## IR 子代理派发规则

- **subagent_type 固定 `general-purpose`**（需要 MCP 搜索能力；受限类型会静默失败）
- **必须用 team 模式 + sequential**：`launch_next_wave(sequential=True)` 每次 1 个 step，
  逐个派发等完成，避免并行触发 API 429
- **禁止同步 `task()`**（无 name 参数）——code=10003 挂掉
- `mode="bypassPermissions"` 确保子代理可写文件
- **派发后主动轮询输出文件**（30s 一次 `test -s`），不依赖子代理消息
- 输出超时未出现 → 重派（最多 2 次）；仍失败 → 记录原因，跳过该 step 继续
- 子代理输出必须含 Section Package JSON block，只写散文视为质量失败

## IR 交付规则

- `finalize_pipeline()` 收尾：质检 → DOCX → 桌面复制（`~/Desktop/`）
- DOCX 失败 → markdown 兜底
- 交付后在聊天窗口告知完整路径；**禁止** `deliver_attachments`（客户端不显示附件）
