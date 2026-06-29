---
name: ir-reporter
version: 2.1.0
description: "投研报告统稿与交付Agent。仅被 ir-coordinator 内部调度，负责统稿、DOCX 生成、对抗验证和交付。不搜索新数据，只基于所有 step 的完整输出写报告。⚠️ 此 skill 不应被用户直接触发——用户说'写研报'、'做尽调'、'分析股票'应触发 ir-coordinator 而非此 skill。仅当用户明确说'统稿'、'生成 DOCX'、'把已有分析整理成报告'时才直接触发。"
allowed-tools:
  - Read
  - Write
  - execute_command
  - use_skill
---

# IR Reporter — 投研报告撰写 Agent（架构文档，非运行时指令）

> ⚠️ 本文件是**项目架构文档**，供维护者理解管线用。运行时子代理的 system_prompt 从 `instruction_store_ir/*.md` 加载，不从本文件加载。

你是 IR/BP 管线的统稿和交付环节。你负责 step8（统稿）、DOCX 生成、对抗验证和交付。

## 环境常量

**IR_RUNTIME**: `~/.workbuddy/ir_runtime/` (symlink → 实际管线目录)
**INSTRUCTION_STORE**: `~/.workbuddy/ir_runtime/instruction_store_ir/`

## 执行流程

### 1. 读取质量生产型产物和所有 step 输出

IR 新管线优先使用质量生产型产物：Research Plan、Fact Store、Section Packages、Debate Review、Final Assembly。统稿是组装和交付，不是新增事实。

统稿必须基于所有前序 step 的完整输出（不是摘要）。

输出文件路径（二选一，取决于管线入口）：
- **WorkBuddy 直调模式**：`{IR_RUNTIME}/data/tasks/{TASK_ID}-{step}.md`（ir_subagent_launcher_wb.py 使用，当前活跃）
- **PipelineOrchestrator 模式**：`{IR_RUNTIME}/jobs/{JOB_ID}/outputs/{step}.md`

**判断方法**：先检查 `data/tasks/` 目录是否存在 step 输出；若不存在再检查 `jobs/` 目录。

**完整 step 列表（5-Wave 11-Agent）**：
- step0_tech（技术分析）、step1_data（数据收集）、step2_industry（行业分析）、step3_biz（商业模式）
- step4_finance（财务分析）、step5_mgmt（管理层）、step_macro（宏观分析）
- step6b_valuation（预测与估值）、step6_insight（差异化洞察）、step7_risk（风险催化）

**预计算数据**（由 Phase 1.2 预计算引擎提供，统稿时可交叉验证）：
- `{IR_RUNTIME}/data/tasks/{TASK_ID}_precompute_financial_metrics.json` — 财务五大维度指标
- `{IR_RUNTIME}/data/tasks/{TASK_ID}_precompute_technical_indicators.json` — 11项技术指标
- `{IR_RUNTIME}/data/tasks/{TASK_ID}_precompute_sector_benchmarks.json` — 行业对标数据

### 3. 撰写统稿

IR 写作规范和事故教训 → 读 **references/ir-writing-standards.md**

### 4. 交叉验证

统稿前必须运行：
```bash
python3 {IR_RUNTIME}/scripts/verify_cross_step_consistency.py --task-id TASK-XXXXX
```
FAIL 级必须修正。

### 5. 生成 DOCX

**IR 研报**：
```bash
python3 {IR_RUNTIME}/scripts/build_ir_broker_report_docx.py TASK-XXXXX
```

**IC 行业研报**：
```bash
python3 {IR_RUNTIME}/scripts/build_ic_industry_report_docx.py TASK-XXXXX
```

**BP DD 报告**（v4.4+ — 从 bp_synthesis.md 生成）：
```bash
python3 {IR_RUNTIME}/scripts/build_bp_dd_report_docx.py \
  --task-id TASK-XXXXX \
  --entity "公司名称" \
  --output /path/to/output.docx \
  --dimensions-dir /path/to/dimensions_dir
```

> ⚠️ BP 管线 v4.4+ 变更：最终交付物是 DOCX（从 bp_synthesis.md 生成），fallback 到 bp_final_report.md。字体动态检测（macOS 优先 PingFang SC），来源渲染保留所有有名称的来源。python-docx 不可用时走 subprocess fallback。

### 6. 对抗验证

```bash
python3 {IR_RUNTIME}/scripts/verification_agent.py --task-id TASK-XXXXX --pipeline ir
```

FAIL → 修复 → 重验（最多 1 次）。

**验证结果写入**：`{IR_RUNTIME}/jobs/{JOB_ID}/verification/`

### 7. 交付

交付协议、产物归档 → 读 **references/delivery-protocol.md**

### 8. BP 尽调报告

BP 防缺陷规则（17 条铁律）→ 读 **references/bp-anti-defect-rules.md**

VL OCR 配置 → 读 **`../ir-researcher/references/bp-ocr-config.md`**（不重复列出）

### 9. BP Phase33 交付详细流程（2026-06-29 更新）

Phase33 由 `heavy_phase_bg` 子进程执行（不复用缓存，确保最新 gate 结果）。

**Step 1: 对抗验证**（`AdversarialVerifier`）
- 对 synthesis 统稿跑对抗检查，结果写入 `bp_verification_result.json`
- 优先级：`bp_synthesis.md` > `bp_final_report.md` > 无可用文本时 FAIL

**Step 2: Delivery Gate**（`bp_delivery_gate.py`，8 项检查）

| 检查项 | 阻断规则 |
|---|---|
| final_assembly | 不存在或 ok=False → 硬阻断 |
| readability | 非 PASS → deferred_fixes，不阻断 |
| claim_coverage | PASS_WITH_DISCLOSURE → deferred；repair_exhausted → 降级 WARN；未 repair 的 FAIL → 硬阻断 |
| wave evidence gates (1-4) | repair_exhausted / blocking_claims_degraded → deferred，不阻断 |
| debate_review | FAIL_BLOCKING → 硬阻断；WARN → deferred，不阻断 |
| cross_dimension | FAIL 或非 PASS → 硬阻断 |
| verification | T1/T2 FAIL → deferred；T3+ FAIL → 硬阻断 |
| sidecar JSON | 损坏 → 记录但不阻断 |

- `checks` 有 FAIL → `ok=False`，管线终止
- `deferred_fixes` → 写入 `delivery_deferred_fixes.json`，允许交付

**Step 3: 产物生成**（gate 通过后）
1. 主报告 DOCX：`build_bp_dd_report()` 用 synthesis 统稿（fallback 到 final_report）→ `delivery/{entity}BP投资备忘录.docx`
2. 8 维度独立 DOCX：`build_bp_dimension_docx()` → `delivery/维度分析/{维度标题}.docx`
3. 双模式 MD：`{entity}BP投资备忘录.md`（synthesis 叙事版）+ `{entity}BP审计底稿.md`（assembler 骨架）
4. 维度分析文件夹副本：统稿 DOCX + 统稿 MD + 审计底稿 MD 复制到 `delivery/维度分析/00.*`
5. 附件收集：xlsx 等文件复制到 delivery/
6. StateStore 注册：所有产物注册 artifact
7. 审计日志：`bp_delivery_audit.json`

**Step 4: 交付链路**（管线外）
- 管线只写文件 + 返回路径，不做"推送到用户"
- kernel → orchestrator → 主 AI 拿到 result dict → 主 AI 用 `present_files` 呈现给用户
- `deliver_to_user: True` 只是标记"可以交付"，实际交付动作在主 AI 层

**交付产物清单（delivery/ 目录）**：
```
delivery/
├── {entity}BP投资备忘录.docx          ← 主报告 (build_bp_dd_report)
├── {entity}BP投资备忘录.md            ← 叙事版 (给决策者)
├── {entity}BP审计底稿.md              ← assembler 骨架 (给尽调团队)
├── bp_delivery_audit.json             ← 交付审计
├── delivery_deferred_fixes.json       ← 降级放行项 (如有)
├── {job_id}_*.xlsx                    ← Excel 附件 (数量不定)
└── 维度分析/
    ├── 00. 统稿投资备忘录.docx        ← 主报告副本
    ├── 00. 统稿投资备忘录.md          ← 统稿 MD 副本
    ├── 00. 审计底稿.md                ← 审计底稿副本
    └── 1-8. {维度标题}.docx           ← 8 个维度独立 DOCX
```

**Gate 硬阻断时**：`deliver_to_user: False` + `block_reason` + `bp_delivery_audit.json`（mode: delivery_blocked_by_gate）

## 核心约束

1. **不搜索新数据** — 只基于 step0_tech~step7_risk + 预计算数据
2. **不编数据** — 不够标 `[数据不足]`
3. **不跳过验证** — consistency + adversarial 是硬规则
4. **交付必须清洗** — 绝不暴露内部工作流信息
5. **估值假设锚定** — 偏差 >20% 需告警
6. **搜索未果≠不存在** — 搜不到不代表没有，标注并继续
7. **脚注不得丢失** — 子代理 [^N] 标记必须保留到最终 DOCX，正文+末尾都要有

## References（按需加载）

| 触发条件 | 读取文件 |
|---------|---------|
| IR 统稿写作规范/事故教训 | `references/ir-writing-standards.md` |
| BP 统稿防缺陷规则 | `references/bp-anti-defect-rules.md` |
| 交付协议/微信推送/产物归档 | `references/delivery-protocol.md` |
| BP OCR 配置 | `../ir-researcher/references/bp-ocr-config.md` |
