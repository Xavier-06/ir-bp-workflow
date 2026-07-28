# BP 管线详细流程（v4.5+双入口 — 2026-07-20 更新）

## 管线阶段（35 phases，含 phase01b 双入口）

⚠️ **v4.5+双入口变更（2026-07-20）**：
- 新增 Phase 01b 公司名搜索入库（双入口：PDF 模式 + 公司名模式）
- Phase 01 无 input_file 时自动跳过（ok: True），交给 Phase 01b
- Phase 01b 无 input_file 时派子代理搜索（tyc-mcp + westock-mcp + web）
- 两条路径产出相同格式文件（bp_ocr_text.txt + bp_step0_profile.json），Phase 02-33 零改动
- v4.5 新增 Wave 0 投资假说先行者 + Wave 4 扩展（详见 codegraph-gate skill 速查）

## 双入口路由

```
run_bp_job(entity, input_file="")         → Phase 01 跳过 → Phase 01b 搜索入库
run_bp_job(entity, input_file="/path.pdf") → Phase 01 OCR  → Phase 01b 跳过
```

Phase 01b 产出 profile 标记 `extraction_source: "public_search"` + `data_completeness` 各维度完整度。
搜不到 BP 是常态——天眼查 + web + westock 拼出的 profile 在多数维度比 BP 自述更可靠。
唯一缺口是财务预测，Stage Tier 机制已兜住（T1/T2 不要求财务数据）。

## BP PDF 自动发现与路由

Phase 01b 子代理在搜索过程中会尝试发现公开的 BP PDF：
1. 子代理搜到 PDF URL → 下载到 `bp_discovered_pdf.pdf`
2. collect 阶段检测文件存在（>10KB）→ 返回 `reroute_to_phase01: true` + `reroute_input_file`
3. coordinator 用 `start_phase=phase01_document_intake` 恢复管线
4. Phase 01 handler 自动检测 `bp_discovered_pdf.pdf` → 执行完整 OCR + 结构化抽取
5. Phase 01b 第二次经过时检测到 input_file → 自动跳过
6. Phase 02-33 正常执行（此时有完整 OCR 数据）

```
01 phase01_document_intake                    — VL OCR + Step0 结构化抽取（无 input_file 时跳过）
01b phase01b_company_intake                   — 公司名搜索入库（无 PDF 时 → needs_dispatch 子代理）★新增
01b phase01b_company_intake_collect            — 搜索入库收集（校验 bp_ocr_text.txt + bp_step0_profile.json）★新增
02 phase02_company_verify                     — 天眼查工商验证（输出 stage_tier）[heavy_bg]
03 phase03_research_plan                      — 研究计划骨架 → needs_dispatch（LLM enrichment）
03c phase03_research_plan_collect             — 合并 enrichment delta 到骨架计划
04 phase04_presearch                          — BP 预搜索 + URL 内容提取 [heavy_bg]
05 phase05_bp_shared_page_init                — 初始化 shared state（含 stage_tier）
06 phase06_search_plan_compile                — 研究计划编译为 claim 级搜索工单
07 phase07_bp_fact_store_bootstrap            — 预搜索 fact 入库
08 phase08_dispatch_prepare                   — Wave 1 manifest（4 维度），sequential 返回 needs_dispatch
09 phase09_dispatch_collect                   — 检查 Wave 1 输出（三文件 + file_stable）
10 phase10_wave1_evidence_gate                — Wave 1 证据门禁（FAIL → repair 子代理 → 重跑）
11 phase11_bp_fact_store_merge                — Wave 1 后 Fact store 合并（仅 Wave 1 sidecar）
12 phase12_wave1_shared_page_refresh          — Wave 1 后刷新 shared state
13 phase13_wave2_prepare                      — Wave 2 已移除（2026-07-28），no-op
14 phase14_wave2_collect                      — Wave 2 已移除，no-op
15 phase15_wave2_evidence_gate                — Wave 2 已移除，no-op
16 phase16_wave3_prepare                      — Wave 3 manifest（competition + valuation）
17 phase17_wave3_collect                      — 检查 Wave 3 输出
18 phase18_wave3_evidence_gate                — Wave 3 证据门禁（FAIL → repair → 重跑）
19 phase19_wave3_shared_page_refresh          — Wave 3 后刷新 shared state
20 phase20_wave4_prepare                      — Wave 4 manifest（dealbreaker_risk）
21 phase21_wave4_collect                      — 检查 Wave 4 输出
22 phase22_wave4_evidence_gate                — Wave 4 证据门禁（FAIL → repair → 重跑）
23 phase23_wave4_shared_page_refresh          — Wave 4 后刷新 shared state
─── Quality Gates ───
24 phase24_bp_claim_coverage_validation       — Claim 覆盖校验（repair → 最多 2 轮 → 降级放行）
25 phase25_bp_cross_dimension_gate            — 跨维度一致性（HIGH→WARN 放行，仅 CRITICAL 阻断）
26 phase26_bp_section_package_validation      — Section package 校验（v1→v2 自动升级）
─── Synthesis ───
27 phase27_synthesis_prepare                  — 统稿子代理 manifest（instruction store 加载）
28 phase28_synthesis_collect                  — 统稿收集（脚注密度 repair → 最多 1 轮 → 降级）
─── Final Assembly + Delivery ───
29 phase29_bp_debate_review                   — 对抗评审（BLOCKING 硬阻断，其余 WARN 放行）
30 phase30_bp_final_assembly                  — Assembler 生成快速浏览版（降级为附件）
31 phase31_bp_readability_review              — 可读性审查（技术术语动态化）
32 phase32_bp_investment_judgment             — 投资判断汇总（stage_tier 感知阈值）
33 phase33_delivery                           — Delivery gate + DOCX 生成 + 维度 DOCX + 交付 [heavy_bg]
```

## 提交任务

```bash
# ⚠️ 所有 python3 管线命令必须带 cd 前缀（Bash 每次调用是独立 shell）

# PDF 模式（有 BP 文件）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator submit \
  --entity "公司名称" --market cn --input-file /path/to/bp.pdf

# 公司名模式（无 PDF，仅公司名）— 必须加 --pipeline bp 强制路由
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator submit \
  --entity "公司名称" --market cn --pipeline bp

# 执行管线
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX

# 恢复管线（start_phase 用上面列表中的 phase 名，如 phase08_dispatch_prepare）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX --start-phase phase08_dispatch_prepare
```

## 融资阶段分级（stage_tier 贯穿全管线）

管线在 phase01 提取 `financing_stage`，phase02 开始计算 `stage_tier`（T1-T4）：

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
- T1 investment_judgment 中客户/收入相关 HIGH 降为 MEDIUM

## BP Step 波次（4 阶段派发，Wave 2 已移除 2026-07-28）

| 波次 | 维度 | 依赖 |
|------|------|------|
| Wave 0 | investment_hypothesis | 无（先行者） |
| Wave 1 | company_team_compliance, product_commercial, tech_ip_moat, market_supply_chain | 无（sequential 逐个派发） |
| Wave 3 | competition_positioning, valuation_return | Wave 1 |
| Wave 4 | dealbreaker_risk, consensus_challenge, catalyst, industry_research | Wave 0 + Wave 1 + Wave 3 |
| Synthesis | 统稿（读取全部维度输出） | 全部 |

## BP 子代理派发硬规则（2026-07-06 更新）

- **必须用 team 模式**：`team_create(team_name=f"bp-{task_id}")` → `Agent(name=..., team_name=..., mode='bypassPermissions')` → 轮询输出文件
- **sequential 派发（管线内部强制）**：BP 管线已实现 `has_more` 机制——每个 wave prepare 只返回 1 个 manifest + `has_more=True`，主 AI 完成当前 role 后用 `start_phase=当前phase` 恢复，管线返回下一个 manifest，直到 `has_more=False` 才推进到 collect。**Coordinator 不需要自行做 sequential 循环**，管线已自动处理。
- **repair 派发也是 sequential**：gate FAIL 时的 repair manifest 按 role 聚合 + 只返回第一个 + `has_more`，instruction 含"禁止并行派发"强制指令。repair 子代理使用 `bp_file_lock.locked_read_modify_write` 写共享文件。
- **禁止用同步 `task()`**（无 name 参数）——会返回 code=10003 挂掉
- `mode="bypassPermissions"`
- **⚠️ 禁止 Agent 工具传 `run_in_background=True`**（2026-07-06 新增）：子代理必须前台派发，完成后立即返回结果给 coordinator。只有 Bash 工具跑 heavy_bg 脚本（phase02/04/33）时才用 `run_in_background`。Agent 后台派发会导致通知延迟，管线每步都卡住。
- **⚠️ 规则4：子代理 prompt 必须声明工具限制**（SKILL.md 规则4）
  所有子代理 prompt 开头加：
  ```
  ⚠️ 工具限制：你没有 Glob/Grep 工具。搜索文件用 Bash（find/ls），读文件用 Read，搜索内容用 Bash（grep）。不要调用 Glob 或 Grep。
  NeoData 金融数据查询（A/HK 股首选，token 已在 preflight 存好）：
    cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; import json; print(json.dumps(neodata_search('查询语句'), ensure_ascii=False))"
  search_gateway 聚合搜索（自动识别金融查询，优先走 NeoData）：
    cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import search; results = search('公司名 营收 利润', prefer='auto'); [print(r['title'], r['url']) for r in results[:5]]"
  yfinance 估值数据（需精确 PE/PS/市值时使用，⚠️ 必须用 /opt/anaconda3/bin/python3）：
    /opt/anaconda3/bin/python3 -c "import yfinance as yf; t = yf.Ticker('688052.SS'); print(t.info.get('marketCap'), t.info.get('trailingPE'))"
  ```
- **⚠️ 规则5：子代理派发与轮询协议**（SKILL.md 规则5，2026-06-12 修订）
  - **5a 派发**：必须先调用 Agent 工具 + 验证返回含 agent_id，否则禁止轮询
  - **5b 轮询**：spawn 成功后启动后台轮询脚本（三文件检查：.md + -facts.json + -section.json 都存在且非空），主线程用短 `test -s poll_{slug}.done` 检查标志文件
  - **5c 三文件**：只看 .md 就推进 = sidecar 丢失，必须三文件齐全才算完成
  - 禁止阻塞式 Bash for/while 循环轮询；超时 20 分钟未就绪 → 重派（最多 2 次）
- **⚠️ 规则6：shutdown 后清理 team config**（SKILL.md 规则6）
  - 收到 shutdown_response approve 后，立即用 Python 从 config.json members 移除该成员
  - 如果仍无法派发 → TeamDelete → 新建 team
- 收到所有同 wave 输出文件后 → 自动调用 `execute(..., start_phase=...)` 推进下一 phase
- **绝对不要等待用户说"继续"**

## Phase03 Research Plan Enrichment（v5.0，2026-06-26 新增）

Phase03 从纯脚本升级为 needs_dispatch 模式（脚本管骨架，主 AI 管大脑）：

1. `_run_research_plan()` 生成确定性骨架（36 facts + 7 core Qs + 10 default claims with `required_fact_keys`）
2. 返回 `needs_dispatch=True, has_more=False` + instruction
3. 主 AI 读 `instruction_store_bp/bp_research_plan_enrichment.md` + BP 原文 + 骨架 → 输出 enrichment delta JSON
4. 用 `start_phase='phase03_research_plan_collect'` 恢复管线
5. `_run_research_plan_collect()` 调用 `apply_enrichment()` 合并 4 项增量：
   - `strategic_questions`（5 条定制化问题，替代模板版）
   - `claim_priority_deltas`（按 BP 内容调整 claim 优先级）
   - `additional_claims`（BP 独有声称，BC011+）
   - `excluded_fact_keys`（按行业裁剪无关 fact）
6. `claim_matrix[*].required_fact_keys` 由 `_section_to_fact_keys()` 自动填充
7. T1/T2 BC005 降级统一在 `build_claim_matrix()` 内处理
8. `research/planner.py` 死代码已删除

## Wave Evidence Gate Repair 机制（v4.4 新增，2026-06-22 更新）

gate FAIL 时不再直接终止管线：
1. `gate_verdict = "REPAIR"`，`needs_repair = true`
2. `build_repair_manifests()` **按 role 聚合**生成 manifest JSON
3. 返回 `needs_dispatch: true` + **只返回第一个 manifest** + `has_more` + `remaining_manifests`（sequential 派发）
4. 主 AI 派发**单个** repair 子代理
5. repair 完成后 `start_phase=waveN_evidence_gate` 重跑 gate
6. `_MAX_BLOCKING_RETRIES=1`：第二次仍 FAIL 时 blocking_claims 降级为 WARN 放行
7. sidecar 缺失永远不降级（硬卡）
8. **T1/T2 早期项目**：blocking_claims 直接降级为 WARN，不走 repair
9. 降级标记统一为 `repair_exhausted: true`

## Claim Coverage Repair 机制

Phase24 claim coverage FAIL 时：
1. `build_claim_repair_manifests()` **按 owner_section 聚合**生成 manifest
2. 返回 sequential manifest（同 wave repair 模式）
3. repair 子代理使用 `locked_read_modify_write` 写 fact_store/sidecar
4. `_MAX_CLAIM_REPAIR_RETRIES=2`：超过后降级为 `PASS_WITH_DISCLOSURE` 放行
5. T1/T2 早期项目走 validator 内部降级路径，不触发 repair

## Synthesis Repair 机制

Phase28 统稿收集时脚注密度不达标触发 repair：
- 动态阈值：每 2000 字至少 3 个脚注引用
- `_MAX_SYNTHESIS_REPAIR_RETRIES=1`：超过后降级为 WARN 放行
- synthesis prompt 从 `instruction_store_bp/bp_统稿.md` 加载（不硬编码）
- 统稿子代理有结构化 brief 文件（`bp_phase3_brief_synthesis.md`）

## Phase29 对抗评审（2026-06-26 宽松化）

**MEDIUM 级别检查（从 HIGH 降级）**：
- 缺少 counter_evidence、部分 claim 无 fact_ids、high confidence + low source
- 缺少结论段落、>50% claims 未验证、<3 个独立来源域名
- 未列出 data gaps、缺少 moat 评估、validation FAIL

**BLOCKING 级别（仅极端情况硬阻断）**：
- `EMPTY_DIMENSION_DRAFT`：维度 MD 为空或 <100 字符
- `ALL_CLAIMS_WITHOUT_FACTS`：100% claim 无 fact_ids
- `NO_SECTION_PACKAGES`：完全无 section package

**Verdict 逻辑**：`BLOCKING→FAIL_BLOCKING` / `issues→WARN` / `无→PASS`
- delivery gate：仅 `FAIL_BLOCKING` 硬阻断，`WARN` 记录到 deferred_fixes 不阻断

## 统稿子代理（Wave Synthesis）

- 读取八个 Wave 1-4 维度输出，按投研逻辑重组为完整研究报告
- 输出路径：`{outputs_dir}/bp_synthesis.md`（同时复制到 `{task_dir}/bp_synthesis.md`）
- manifest 路径：`{task_dir}/bp_phase3_manifest_synthesis.json`
- 必须用 team 模式派发：`Agent(name='bp-synthesis', team_name=..., mode='bypassPermissions')`

### 统稿 prompt 四板斧

1. **表格规范**：表格仅放结构化数据，论述放正文段落，单元格不超 40 字
2. **论证链保留**：每个结论必须有推理过程（搜了什么→发现什么→为什么得出结论）
3. **天使轮适配**：T1 公司无客户/收入是正常状态，估值用可比交易法
4. **去重规则**：章节引导语只保留一次，跨维度去重标注"多维交叉验证"

### 其他统稿硬约束

- **脚注硬规则**：子代理 [^N] 标记必须保留，正文每个关键数据点都要有 [^N]
- **专利不堆砌**：核心≤5项，其余概括性描述
- **技术壁垒量化评估**必须独立成节
- **统稿保留硬约束**：核心对比表原文保留、市占率数据完整保留

## BP 质量门禁（Phase 24-33）

| 门禁 | 文件 | 通过条件 |
|------|------|---------|
| Claim Coverage | `bp_claim_coverage_gate.json` | PASS 或 PASS_WITH_DISCLOSURE 允许交付 |
| Cross Dimension | `bp_cross_dimension_gate.json` | HIGH 降级为 WARN，仅 CRITICAL_CLAIM_CONTRADICTED 阻断 |
| Section Package | `bp_section_gate.json` | 全部维度 passed（v1→v2 自动升级） |
| Debate Review | `bp_debate_review.json` | verdict != FAIL_BLOCKING（BLOCKING 硬阻断，其余 WARN 放行） |
| Readability | `bp_readability_review.json` | verdict == PASS |
| Investment Judgment | `bp_investment_judgment.json` | 投资判断汇总完成 |
| Delivery Gate | `bp_delivery_gate.json` | 全部 hard check 通过 |

### Delivery Gate WARN 级检查（不阻断但记录）

1. **来源完整性**：synthesis.md "来源与参考"章节脚注≥5 或 URL≥5
2. **Claim unverified 占比**：critical/high claim 中 unverified < 50%
3. **对抗验证 WARN 数量**：< 3 个 WARN

### Claim Coverage 否定性发现判定

fact 内容为"未找到/无法验证/无外部证据"时，即使 source_tier 不是 bp，claim 也被判定为 `unverified` 而非 `supported`。

### Claim Coverage sidecar facts 合并

`bp_claim_coverage_validator` 自动读取子代理 sidecar 文件（`*-facts.json`）中的 facts，不再仅依赖中央 `bp_fact_store.json`。

- `_reconstruct_ghost_facts()`：合成 section sidecar 中引用但未定义的 fact_id
- `_fact_tier()`：归一化 17 种 source 字段别名到标准 tier 名
- **T1/T2 stage 感知**：BP_ONLY_EVIDENCE 的 critical/high claim 降为 disclosure

### Section Package v1→v2 自动升级

检测到 `schema_version: bp_section_package.v1` 时自动合成 v2 缺失字段。

### Final Assembly 降级策略

debate_review FAIL 但 6+ 维度文件齐全时，自动 force-assemble 并写入审计日志。

### DOCX 生成 lxml fallback

managed Python lxml 签名无效时自动 fallback 到系统 Python（`/opt/anaconda3/bin/python3`）。

## BP 最终交付（phase33，2026-06-26 更新）

**最终交付物是 DOCX 文件**，由 `build_bp_dd_report_docx.py` 从 `bp_synthesis.md` 生成。

- `bp_synthesis.md` 是主报告（有完整推理链和脚注）
- `bp_final_report.md`（assembler 输出）降级为快速浏览版附件
- **维度 MD → DOCX 独立报告**（2026-06-29 更新）：8 个维度各自生成独立 DOCX，平铺在 `delivery/` 根目录（不再使用 `维度分析/` 子目录）
- DOCX 字体动态检测：macOS 优先 PingFang SC，Windows 用 Microsoft YaHei
- DOCX 来源渲染：保留所有有名称的来源（不再强制要求 URL）
- 报告路径：`{job_dir}/delivery/TASK-XXXX_bp_dd_report.docx`

## Team 清理硬规则

- 交付完成后**必须清理 team**
- 清理顺序：
  1. 每个子代理完成后，立即 `send_message(type="shutdown_request", recipient=member)`
  2. 收到 shutdown_response approve 后，**立即用 Python 从 config.json 移除该成员**
  3. 全部成员清理完毕 → `team_delete()`
- 绝对不能跳过 team 清理就结束对话

## DD 报告生成与交付

- 8 维度原材料先进入 section package 与 quality gate
- `build_bp_dd_report_docx.py` 生成 Word 报告
- **⚠️ 交付硬规则**：管线 `phase33_delivery` 完成后，返回值含 `deliver_to_user: true` 和 `docx_path`
  Coordinator 必须执行以下交付动作：
  1. 在聊天窗口告知用户报告完成 + 文件路径
  2. 调用 `open_result_view` 展示报告（如适用）
  3. 按当前客户端能力决定是否额外交付附件；不要绕过管线生成的 `docx_path`
