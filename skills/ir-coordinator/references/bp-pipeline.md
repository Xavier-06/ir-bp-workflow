# BP 管线详细流程（v4.2 — 2026-06-11 更新）

## 管线阶段

```
phase01_document_intake          — VL OCR + Step0 结构化抽取（含 financing_stage 提取）
phase02_company_verify          — BP 专用工商验证脚本（输出 stage_tier）
phase04_presearch               — BP 专用预搜索脚本 + URL 内容提取
phase05_bp_shared_page_init     — 初始化 shared state（含 stage_tier）
phase06_search_plan_compile     — 研究计划编译（按 stage_tier 调整 fact_requirement）
phase07_bp_fact_store_bootstrap — 预搜索 fact 入库
phase2_dispatch_prepare         — Wave 1 manifest/brief（4 维度），返回 needs_dispatch
│   └── 主 AI 读 manifests → team 派发 4 个子代理（sequential）
phase2_dispatch_collect         — 检查 Wave 1 输出
phase12_bp_shared_page_refresh  — Wave 1 后刷新 shared state（claim/risk 合并）
phase23_wave2_prepare           — Wave 2 manifest（customer_revenue_validation）
phase23b_wave2_collect          — 检查 Wave 2 输出
phase24_wave3_prepare           — Wave 3 manifest（competition_positioning + valuation_return）
phase24b_wave3_collect          — 检查 Wave 3 输出
phase25_wave4_prepare           — Wave 4 manifest（dealbreaker_risk）
phase25b_wave4_collect          — 检查 Wave 4 输出
phase27_bp_shared_page_refresh  — 全部 Wave 后刷新 shared state
phase28_bp_claim_coverage       — Claim coverage 验证（否定性发现 → unverified）
phase25_bp_cross_dimension_gate — 跨维度一致性
phase11_bp_fact_store_merge     — Fact store 合并
phase35_bp_section_package      — Section package 提取与验证
phase29_bp_debate_review        — 辩论审查（5 项检查）
phase30_bp_final_assembly       — Assembler 生成快速浏览版（降级为附件）
phase31_bp_readability_review   — 可读性审查（技术术语动态化）
phase32_bp_investment_judgment  — 投资判断汇总（stage_tier 感知阈值）
phase27_synthesis_prepare        — 统稿子代理 manifest，返回 needs_dispatch
phase28_synthesis_collect        — 检查统稿输出 bp_synthesis.md
phase33_delivery                — delivery gate + DOCX 生成 + 交付
```

## 提交任务

```bash
# ⚠️ 所有 python3 管线命令必须带 cd 前缀（Bash 每次调用是独立 shell）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator submit \
  --entity "公司名称" --market cn --input-file /path/to/bp.pdf

# 执行管线（同样必须带 cd）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX

# ⚠️ 如果返回 needs_poll: true + bg_pid，必须轮询到进程结束才能推进
# while kill -0 {bg_pid} 2>/dev/null; do sleep 30; done
# 确认进程结束后，再用 --start-phase 推进下一 phase
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX --start-phase phase2_dispatch_prepare
```

## 融资阶段分级（stage_tier 贯穿全管线）

管线在 phase0 提取 `financing_stage`，phase05 开始计算 `stage_tier`（T1-T4），此后所有脚本和子代理 prompt 都能读到：

| 级别 | 阶段 | 核心关注 | 禁用估值方法 |
|------|------|---------|-------------|
| **T1 极早期** | 种子/天使 | 团队+技术+方向 | PE, DCF |
| **T2 早期** | Pre-A/A轮 | 产品+PMF+早期客户 | PE, DCF |
| **T3 成长期** | B轮 | 规模化+收入增长 | — |
| **T4 成熟期** | C轮/Pre-IPO | 盈利+退出路径 | — |

**stage_tier 传播路径**：`bp_step0_profile.json` → `bp_shared_state.json` → 所有下游脚本和子代理 prompt。

**关键影响**：
- T1 公司"无客户/无收入"不标为高风险
- T1 估值折价上限 35%（不谈流动性折价）
- T1 claim_coverage 中"客户收入不可验证"不算 blocker
- T1 thesis_reconciler 中客户/收入相关 HIGH 降为 MEDIUM
- T1 investment_judgment 中 customer_revenue/valuation 低置信度不拉高整体风险
- **T1 synthesis_prepare 自动跳过 Wave 2 维度**：为 customer_revenue_validation 创建占位文件（md + facts sidecar + section sidecar），coordinator 不需要手动创建

## BP Step 波次（5 阶段派发）

| 波次 | 维度 | 依赖 |
|------|------|------|
| Wave 1 | company_team_compliance, product_commercial, tech_ip_moat, market_supply_chain | 无（sequential 逐个派发） |
| Wave 2 | customer_revenue_validation | Wave 1 |
| Wave 3 | competition_positioning, valuation_return | Wave 1 + Wave 2 |
| Wave 4 | dealbreaker_risk | Wave 1 + Wave 2 + Wave 3 |
| Synthesis | 统稿（读取全部 8 维度输出） | 全部 |

## BP 子代理派发硬规则

- **必须用 team 模式**：`team_create(team_name=f"bp-{task_id}")` → `Agent(name=..., team_name=..., mode='bypassPermissions')` → 轮询输出文件
- **sequential 派发**：每个 Wave 内的维度逐个派发，完成一个再下一个
- **禁止用同步 `task()`**（无 name 参数）——会返回 code=10003 挂掉
- `mode="bypassPermissions"`
- **⚠️ 规则4：子代理 prompt 必须声明工具限制**（SKILL.md 规则4）
  所有子代理 prompt 开头加：
  ```
  ⚠️ 工具限制：你没有 Glob/Grep 工具。搜索文件用 Bash（find/ls），读文件用 Read，搜索内容用 Bash（grep）。不要调用 Glob 或 Grep。
  ```
- **⚠️ 规则5：派发后主动轮询输出文件，不等消息**（SKILL.md 规则5）
  - 每 60 秒用 Bash `test -s {output_path}` 检查每个 step 的输出文件
  - 文件就绪（>100 bytes）= 该 step 完成，不论是否收到子代理消息
  - 超时 20 分钟未就绪 → 重派（最多 2 次）
- **⚠️ 规则6：shutdown 后清理 team config**（SKILL.md 规则6）
  - 收到 shutdown_response approve 后，立即用 Python 从 config.json members 移除该成员
  - 如果仍无法派发 → TeamDelete → 新建 team
- 收到所有同 wave 输出文件后 → 自动调用 `execute(..., start_phase=...)` 推进下一 phase
- **绝对不要等待用户说"继续"**

## 统稿子代理（Wave Synthesis）

- 读取八个 Wave 1-4 维度输出，按投研逻辑重组为完整研究报告
- 输出路径：`{outputs_dir}/bp_synthesis.md`（同时复制到 `{task_dir}/bp_synthesis.md`）
- manifest 路径：`{task_dir}/bp_phase3_manifest_synthesis.json`
- 必须用 team 模式派发：`Agent(name='bp-synthesis', team_name=..., mode='bypassPermissions')`

### 统稿 prompt 四板斧（2026-06-10 新增）

1. **表格规范**：表格仅放结构化数据（数字/状态/等级），论述放正文段落，单元格不超 40 字
2. **论证链保留**：每个结论必须有推理过程（搜了什么→发现什么→为什么得出结论），禁止只输出结论
3. **天使轮适配**：T1 公司无客户/收入是正常状态，评估重点是团队/技术/市场，估值用可比交易法
4. **去重规则**：章节引导语只保留一次，执行摘要和正文不重复，跨维度去重标注"多维交叉验证"

### 其他统稿硬约束

- **脚注硬规则**：子代理 [^N] 标记必须保留，统稿时补全缺失脚注，正文每个关键数据点都要有 [^N]，末尾"来源与参考"展开
- **专利不堆砌**：核心≤5项，其余概括性描述
- **技术壁垒量化评估**必须独立成节（壁垒高度+实用性+赚钱能力，全部配数字和脚注）
- **统稿保留硬约束**：核心对比表原文保留、市占率数据完整保留、去重只做跨维度不做维度内压缩、来源合并不丢来源

## BP 质量门禁（Phase 28-39）

| 门禁 | 文件 | 通过条件 |
|------|------|---------|
| Claim Coverage | `bp_claim_coverage_gate.json` | FAIL 阻止交付；PASS_WITH_DISCLOSURE 允许交付（附披露声明） |
| Cross Dimension | `bp_cross_dimension_gate.json` | gate_verdict == PASS |
| Debate Review | `bp_debate_review.json` | verdict != REWRITE_REQUIRED |
| Readability | `bp_readability_review.json` | verdict == PASS（技术术语列表按行业动态生成） |
| Delivery Gate | `bp_delivery_gate.json` | 全部 hard check 通过 |

### Delivery Gate WARN 级检查（不阻断但记录）

1. **来源完整性**：synthesis.md "来源与参考"章节脚注≥5 或 URL≥5
2. **Claim unverified 占比**：critical/high claim 中 unverified < 50%
3. **对抗验证 WARN 数量**：< 3 个 WARN

### Claim Coverage 否定性发现判定

fact 内容为"未找到/无法验证/无外部证据"时，即使 source_tier 不是 bp，claim 也被判定为 `unverified` 而非 `supported`。这避免了"搜不到证据反而让 claim 变成 supported"的逻辑错误。

### Claim Coverage sidecar facts 合并（v4.1 新增，v4.2 增强）

`bp_claim_coverage_validator` 现在自动读取子代理 sidecar 文件（`*-facts.json`）中的 facts，不再仅依赖中央 `bp_fact_store.json`。这解决了子代理不回写中央 store 导致 claim 卡在 `not_addressed` 的问题。

**v4.2 新增**：
- `_reconstruct_ghost_facts()`：扫描 section sidecar 中引用但未定义的 fact_id，合成最小 fact 对象。解决 dealbreaker_risk 子代理写 `facts_used` 但不写 `facts[]` 的问题。
- `_fact_tier()`：检查 6 个字段名变体（source_tier/source_quality/source_type/provenance/evidence_quality/source_level），归一化 17 种值别名到标准 tier 名。解决子代理 fact 缺少 source_tier 被误判为 bp_only 的问题。
- **T1/T2 stage 感知**：`evaluate_bp_claim_coverage` 读取 stage_tier，T1/T2 时 BP_ONLY_EVIDENCE 的 critical/high claim 降为 disclosure 而非 hard fail。Coordinator 不需要手动改 gate verdict。

Coordinator 不需要额外操作。

### Section Package v1→v2 自动升级（v4.1 新增）

`_validate_bp_section_package` 检测到 `schema_version: bp_section_package.v1` 时，自动合成 v2 缺失字段（answers / claim_ids_covered / narrative_blocks / search_audit），标记 `_auto_upgraded_from_v1` 并放宽 v2-only 验证。Coordinator 不需要手动干预。

### IC/RT 输出格式归一化（v4.1 新增）

`bp_ic_redteam_gate` 在验证前自动归一化子代理输出变体：
- IC：`recommendation` 对象提取为字符串、`must_verify_items` → `must_verify_before_investment`
- RT：`high_issues + medium_issues` 合并为 `issues` 数组、claim_id 支持文本模糊匹配
- Coordinator 收到 IC/RT 子代理输出后不需要手动修 JSON

### Final Assembly 降级策略（v4.1 新增）

`bp_final_assembly` 当 debate_review FAIL 但 6+ 维度文件齐全时，自动 force-assemble 并写入审计日志 `bp_force_assemble_audit.json`。Coordinator 不需要绕过 debate_review。

### DOCX 生成 lxml fallback（v4.1 新增，v4.2 增强）

`_run_python_script` 检测 DOCX 脚本时如果 managed Python 的 lxml code signature 无效，自动 fallback 到系统 Python（`/opt/anaconda3/bin/python3`）。

**v4.2 增强**：`_run_bp_delivery` 的 in-process DOCX import 也加了 fallback。当 `from scripts.build_bp_dd_report_docx import build_bp_dd_report` 抛 ImportError（lxml 签名失败）时，`_docx_via_subprocess()` 写驱动脚本用系统 Python 执行。覆盖 phase handler 和 delivery 两条路径。

Coordinator 不需要处理。

## BP 最终交付

**最终交付物是 DOCX 文件**，由 `build_bp_dd_report_docx.py` 从 `bp_synthesis.md` 生成。

- `bp_synthesis.md` 是主报告（有完整推理链和脚注）
- `bp_final_report.md`（assembler 输出）降级为快速浏览版附件
- DOCX 字体动态检测：macOS 优先 PingFang SC，Windows 用 Microsoft YaHei
- DOCX 来源渲染：保留所有有名称的来源（不再强制要求 URL）
- 报告路径：`{job_dir}/delivery/TASK-XXXX_bp_dd_report.docx`

## Team 清理硬规则

- 交付完成后**必须清理 team**，否则 workspace 会一直挂着
- 清理顺序：
  1. 每个子代理完成后，立即 `send_message(type="shutdown_request", recipient=member)`
  2. 收到 shutdown_response approve 后，**立即用 Python 从 config.json 移除该成员**（规则6）：
     ```bash
     python3 -c "import json; p='/Users/xavier/.workbuddy/teams/{team}/config.json'; d=json.load(open(p)); d['members']=[m for m in d['members'] if m['name']!='{step}']; json.dump(d,open(p,'w'),ensure_ascii=False,indent=2)"
     ```
  3. 全部成员清理完毕 → `team_delete()`
- 如果 `team_delete()` 因 active member 失败，再次发送 shutdown_request 并等待后重试
- 绝对不能跳过 team 清理就结束对话

## DD 报告生成与交付

- 8 维度原材料（团队/产品/技术/市场/竞争/估值/客户收入/风险）先进入 section package 与 quality gate。
- `build_bp_dd_report_docx.py` 生成 Word 报告（支持 Markdown 表格 → Word 原生表格、行内格式、来源清洗）。
- **⚠️ 交付硬规则**：管线 `phase33_delivery` 完成后，返回值含 `deliver_to_user: true` 和 `docx_path`。
  Coordinator 必须执行以下交付动作：
  1. 在聊天窗口告知用户报告完成 + 文件路径
  2. 调用 `open_result_view` 展示报告（如适用）
  3. 微信通知已由管线自动发送，无需重复
  4. 按当前客户端能力决定是否额外交付附件；不要绕过管线生成的 `docx_path`
