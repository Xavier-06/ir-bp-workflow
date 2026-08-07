#  IR / BP / IC / LIT Workflow

> AI 驱动的四管线投研工作流：研报（IR）· BP 尽调（BP）· 行业研究（IC）· 文献综述（LIT）。
> 全自动闭环，零人工干预。

**四条管线，统一架构：**

| 管线 | 触发方式 | 规模 | 产出 |
|------|---------|------|------|
| **IR** | "分析比亚迪" | 7 step + 统稿 · 4 波 · 29 phase（deep） | 研报 DOCX |
| **BP** | "帮我看下这个 BP" + 上传文件 | 10 角色 + 统稿 · 3 波 · 31 phase | 尽调报告 DOCX |
| **IC** | "做个半导体行业研究" | 5 种原型 · 最多 16 角色 · 18 phase | 行业研究报告 DOCX |
| **LIT** | "做个固态电池技术评估" | 7 角色 · 3 波 · 20 phase | 技术评估报告 MD |

---

## 版本演进（v6.1，2026-07-29）

本仓库近期做了几轮结构性瘦身，如果你拿旧文档对照代码会发现对不上，以下是关键变更：

- **BP 删除 Wave 0 投资假说先行者**：原 `phase07b/07c/07d`（`bp_investment_hypothesis` 角色）彻底移除。假说在所有维度之前跑属于空转——Wave1 各维度本身就在做验证，且无硬下游依赖。角色从 11 减为 10。
- **BP 删除独立工商核验 phase**：原 `phase02_company_verify` 移除。工商核验已内化进研究计划子代理（`phase04_research_plan` 自带 tyc-mcp 直调），不再单独占一个 phase。
- **BP phase 连续重编号**：从断档编号（01/01b/04/04c/05-30）连续化为 **01-31**。子代理输出文件也从远古命名 `bp_phase2_{slug}.*` 改为 `bp_dim_{slug}.*`，dispatch 记录改为 `bp_dispatch.json`。
- **IR step 连续重编号**：重编号为连续的 **step1-step8**（原 step2_industry→step1_industry、step_macro→step5_macro 等），统稿从 step8_master 剥离为独立 synthesis 子代理。

> ⚠️ 旧 job 数据的 phase/step 字符串已失效，断点续跑需用新名。

---

## 管线全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ir-coordinator（调度中心）                       │
│          接收自然语言指令 → 自动识别管线类型 → 全自动执行              │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│   IR 管线     │   BP 管线     │   IC 管线     │     LIT 管线           │
│  券商研报      │  BP 尽调      │  行业研究      │     文献综述           │
│  8步+统稿     │  10角色+统稿   │  5原型×16角色  │     7角色             │
│  3波·28phase  │  3波·31phase  │  18 phase     │     3波·20phase       │
├──────────────┴──────────────┴──────────────┴────────────────────────┤
│                          共享基础设施                                 │
│  kernel.py（Phase 状态机）· search_gateway（6 层搜索降级链）            │
│  bp_file_lock（文件锁）· Fact Store（全局事实库）                      │
│  IMA 知识库（投行研报全文源）· subagent_launcher（子代理发射器）         │
└─────────────────────────────────────────────────────────────────────┘
```

四条管线共享同一套 kernel 编排器、搜索网关、文件锁和 Fact Store。差异在于 Profile（phase 定义 + 子代理编排）。

---

## BP 管线（商业计划书尽调）

**一句话**：BP 文件进来 → OCR 结构化 → 研究计划（含工商核验）→ Wave1 四维基础采集 → Wave3 竞争+估值交叉验证 → Wave4 红队/共识/催化剂/行业研报 → Fact Store 全局事实库 → 门禁校验 → 不合格自动修复 → 统稿 → 对抗评审 → DOCX 交付。

每个分析结论都必须有可追溯的证据（claim → fact → source），没有证据的结论会被门禁拦截并修复。

```
BP 文件（PDF/PPTX）
        │
        ▼
Phase 01-08: 预处理
  01  OCR 识别 → 结构化公司数据（无文件时跳过）
  02  公司名搜索入库（无 PDF 模式，子代理搜索）
  03  搜索入库收集
  04  研究计划 → 子代理派发（tyc 工商核验 + westock + search_deep 自主搜索）
  05  研究计划收集
  06  共享尽调页初始化
  07  搜索工单编译
  08  Fact Store 初始化
        │
        ▼
Wave 1: 基础证据采集（4 角色 sequential）
  09 派发 → 10 收集 → 11 门禁（FAIL → repair → 重跑）→ 12 Fact 合并 → 13 共享页刷新
  团队合规 / 产品商业 / 技术IP / 市场供应链
  每角色产出：.md + facts.json + section.json
        │
        ▼
Wave 3: 竞争 + 估值（2 角色 sequential）
  14 派发 → 15 收集 → 16 门禁 → 17 共享页刷新
  读 Wave 1 输出做交叉验证
        │
        ▼
Wave 4: 叙事层（4 角色）
  18 派发 → 19 收集 → 20 门禁 → 21 共享页刷新
  红队风险 / 共识挑战 / 催化剂 / 行业研报
  读全量输出做反向论证
        │
        ▼
Phase 22-24: 全局校验
  22 Claim 覆盖校验（每个 claim 有证据？）
  23 跨维度一致性（不同维度引用同一数字？）
  24 Section Package 校验（格式完整性）
        │
        ▼
Phase 25-26: 统稿（1 个子代理）
  读 10 维度 .md + Fact Store → 写最终报告
  脚注密度不达标 → repair 子代理补脚注
        │
        ▼
Phase 27-31: 交付
  27 对抗评审 → 28 最终组装 → 29 可读性审查
  30 投资判断汇总 → 31 DOCX 生成 + 桌面复制
        │
        ▼
  DD 尽调报告.docx + 维度独立 DOCX
```

### BP 10 角色 + 统稿

| 角色 | Wave | 职责 | 核心工具 |
|------|------|------|---------|
| `bp_company_team_compliance` | W1 | 团队 / 合规 / 治理 | 天眼查 + westock + IMA |
| `bp_product_commercial` | W1 | 产品 / 商业化 / 客户 | 天眼查 + westock + IMA |
| `bp_tech_ip_moat` | W1 | 技术 / IP / 护城河 | 天眼查 + westock + IMA |
| `bp_market_supply_chain` | W1 | 市场 / 行业 / 供应链 | 天眼查 + westock + IMA |
| `bp_competition_positioning` | W3 | 竞品清单 / 差异化 / 可复制性 | 天眼查 + westock + IMA |
| `bp_valuation_return` | W3 | 估值 / 可比公司 | 天眼查 + westock + IMA |
| `bp_dealbreaker_risk` | W4 | 红队风险 / deal breaker | 天眼查 + westock + IMA |
| `bp_consensus_challenge` | W4 | 共识挑战 / 预期差分析 | 天眼查 + westock + IMA |
| `bp_catalyst` | W4 | 催化剂事件 / 时间窗口 / 传导链 | 天眼查 + westock + IMA |
| `bp_industry_research` | W4 | 行业研报整合（6 大类基准数据） | westock + IMA |
| `bp_统稿` | — | 读全量维度输出 + Fact Store → 最终报告 | — |

### BP 31 Phase 完整清单

| # | Phase | 类型 | 说明 |
|---|-------|------|------|
| 01 | phase01_document_intake | 脚本 | VL OCR 识别 + 结构化抽取（无 input_file 时跳过）[heavy_bg] |
| 02 | phase02_company_intake | dispatch | 公司名搜索入库（无 PDF 模式，子代理搜索）★双入口 |
| 03 | phase03_company_intake_collect | 收集 | 搜索入库收集（校验产出文件） |
| 04 | phase04_research_plan | dispatch | 研究计划子代理派发（tyc 工商核验 + westock + search_deep 自主搜索） |
| 05 | phase05_research_plan_collect | 收集 | 读子代理输出 → schema 归一化 → 校验落盘 |
| 06 | phase06_bp_shared_page_init | 脚本 | 共享尽调页初始化 |
| 07 | phase07_search_plan_compile | 脚本 | 搜索工单编译 |
| 08 | phase08_bp_fact_store_bootstrap | 脚本 | Fact Store 初始化（seed facts 来自 research_plan） |
| 09 | phase09_dispatch_prepare | dispatch | Wave 1 派发（sequential, 4 角色） |
| 10 | phase10_dispatch_collect | 收集 | Wave 1 收集（retry + 三文件检查） |
| 11 | phase11_wave1_evidence_gate | 门禁 | Wave 1 证据校验（repair） |
| 12 | phase12_bp_fact_store_merge | 脚本 | Fact Store 合并 |
| 13 | phase13_wave1_shared_page_refresh | 脚本 | 共享页刷新（after W1） |
| 14 | phase14_wave3_prepare | dispatch | Wave 3 派发（2 角色 sequential） |
| 15 | phase15_wave3_collect | 收集 | Wave 3 收集 |
| 16 | phase16_wave3_evidence_gate | 门禁 | Wave 3 证据校验（repair） |
| 17 | phase17_wave3_shared_page_refresh | 脚本 | 共享页刷新（after W3） |
| 18 | phase18_wave4_prepare | dispatch | Wave 4 派发（4 角色：dealbreaker + 共识 + 催化剂 + 行业研报） |
| 19 | phase19_wave4_collect | 收集 | Wave 4 收集 |
| 20 | phase20_wave4_evidence_gate | 门禁 | Wave 4 证据校验（repair，叙事类角色跳过 claim 检查） |
| 21 | phase21_wave4_shared_page_refresh | 脚本 | 共享页刷新（after W4） |
| 22 | phase22_bp_claim_coverage_validation | 门禁 | Claim 覆盖校验（repair，最多 2 轮 → 降级放行） |
| 23 | phase23_bp_cross_dimension_gate | 门禁 | 跨维度一致性（HIGH → WARN 放行） |
| 24 | phase24_bp_section_package_validation | 校验 | Section Package 校验 |
| 25 | phase25_synthesis_prepare | dispatch | 统稿派发（7 维度三件套 + 3 叙事角色仅 md） |
| 26 | phase26_synthesis_collect | 收集 | 统稿收集（脚注密度 repair） |
| 27 | phase27_bp_debate_review | 校验 | 对抗评审（HIGH → MEDIUM，仅 BLOCKING 硬阻断） |
| 28 | phase28_bp_final_assembly | 脚本 | 最终组装 |
| 29 | phase29_bp_readability_review | 校验 | 可读性审查 |
| 30 | phase30_bp_investment_judgment | 脚本 | 投资判断汇总 |
| 31 | phase31_delivery | 交付 | DOCX 生成 + 维度独立 DOCX + delivery gate [heavy_bg, 600s] |

> ⚠️ **子代理输出文件命名（v6.1 起）**：`bp_dim_{slug}.md` / `bp_dim_{slug}-facts.json` / `bp_dim_{slug}-section.json`（不再是 `bp_phase2_*`）。dispatch 记录为 `bp_dispatch.json`；统稿为 `bp_synthesis_brief.md` / `bp_synthesis_manifest.json`。schema 别名 `bp_phase2_section.v1` 仍映射到 `bp_section_package.v1`（历史兼容，勿删）。

---

## IR 管线（研报）

**一句话**：标的进来 → 工商核验 → 预搜索 → 研究计划子代理 → 行业+业务并行采集 → 财务预测 + 管理层验证 → 估值收口 → 洞察 + 风险 → 统稿 → 对抗评审 → DOCX 交付。

### IR 7 step + 统稿（v3.6）

| Step | 角色 | Wave | 依赖 | 职责 |
|------|------|------|------|------|
| step1_industry | 投研_主笔_行业分析 | W1 | — | 行业规模 / 增速 / 格局 |
| step2_biz | 投研_主笔_商业模式 | W1 | — | 商业模式 / 单元经济 |
| step3_finance | 投研_主笔_财务分析 | W2 | step1 + step2 | 三表分析 / 前瞻预测 / **成本端原材料实时价格锚定** |
| step4_mgmt | 投研_主笔_管理层 | W2 | step2 | 管理层 / 治理 / 激励 |
| step6_valuation | 投研_主笔_预测与估值 | W3 | step1 + step2 + step3 | 估值收口 / **折现率利率环境自取数** |
| step7_insight | 投研_主笔_差异化洞察 | W4 | step1-4 + step6 | 预期差 / 核心矛盾 |
| step8_risk | 投研_主笔_风险催化 | W4 | step3 + step4 + step6 | 风险 + 催化剂 / **宏观与大宗风险动态取数** |
| synthesis | ir_统稿 | — | 全部 step | 读 7 步输出 + Fact Store → 论点驱动叙事 |

> **step5_macro（宏观分析）已于 v3.6 删除**：五维宏观评分与个股定价脱节。职责下沉——大宗原材料实时价格 → step3_finance（成本假设）+ step8_risk（风险量化）；利率/折现率环境 → step6_valuation 自取数。取数纪律统一写在 `_common_tool_guide.md`「宏观与大宗数据取数纪律」。
> **step1_data（数据收集）已删除**，改用大行研报为骨架。原 step8_master（文档汇总）已剥离为独立 synthesis 子代理（phase13）。

### IR 行业 Overlay 系统

不改 step 数量和 wave 编排，只让 prompt 按行业切换分析框架：

| Overlay | 适用标的 | 核心分析框架 |
|---------|---------|-------------|
| semiconductor | 半导体 | 制程节点 / 良率 / 设计-制造-封测 |
| consumer | 消费 | 品牌力 / 渠道 / 同店增长 / SKU |
| internet | 互联网 | DAU/MAU / ARPU / 变现率 / 获客成本 |
| heavy_asset | 重资产 | 产能利用率 / 资本开支 / 折旧周期 |
| financial | 金融 | 净息差 / 不良率 / 偿付能力充足率 |

由 `_infer_ir_industry(entity)` 从标的名称自动匹配，未知标的优雅降级（无 overlay）。

### IR Stage Tier 分级

由 `resolve_ir_research_tier()` 解析（环境变量 → 配置文件 → 默认 `deep`）：

| Tier | Phase 数 | 裁剪内容 |
|------|---------|---------|
| **deep**（默认） | 28 | 全量，不裁剪 |
| **standard** | 26 | 跳过 claim_coverage + cross_dimension_gate |
| **quick** | 18 | 跳过 per-wave gate / debate / claim / cross / readability |

核心数据采集链（preflight → delivery + 统稿）任何 tier 均完整。

---

## IC 管线（行业研究）

**一句话**：课题进来 → 元数据解析 → 公司批量验证 → 研究计划子代理判定原型 → 按原型动态展开 wave → 证据门禁 → 统稿 → 投资判断 → DOCX 交付。

### 5 种课题原型（Archetype）

| 原型 | 适用场景 | 子代理数 | 关键特征 |
|------|---------|---------|---------|
| **chain_scan** | 成熟行业全景 | 11~15 | 产业链环节分解 → segment_deep |
| **tech_compare** | 多条技术路线 PK | 10~14 | 路线对比 → route_deep |
| **company_deep** | 单/少公司跟踪 | 9 | 围绕业务 / 财务 / 竞争力 |
| **early_theme** | 数据稀缺前瞻方向 | 7 | 侧重可行性 + 里程碑 |
| **commercial_mode** | 聚焦变现逻辑 | 10 | 侧重单元经济 / 定价 / 客户 |

原型由 phase04 研究计划子代理在 research plan 中输出 `archetype` 字段自动判定。

### IC 16 角色

| 角色 | 适用原型 | 职责 |
|------|---------|------|
| ic_executive_hypothesis | 全部 | 投研假说 |
| ic_market_overview | chain_scan / commercial_mode | 市场全景 |
| ic_competitive | 多原型 | 竞争格局 |
| ic_tech_product | chain_scan / tech_compare / early_theme | 技术产品 |
| ic_supply_chain | chain_scan / early_theme | 产业链 |
| ic_policy_risk | chain_scan / company_deep | 政策风险 |
| ic_segment_deep | chain_scan 专用 | 环节深度分析（合并 6 维度） |
| ic_tech_landscape | tech_compare 专用 | 技术全景扫描 |
| ic_route_deep | tech_compare 专用 | 路线深度分析 |
| ic_business_overview | company_deep 专用 | 业务概览 |
| ic_feasibility | early_theme 专用 | 可行性评估 |
| ic_unit_economics | commercial_mode 专用 | 单元经济 |
| ic_catalyst | 全部 | 催化剂分析 |
| ic_consensus | 全部 | 共识挑战 |
| ic_cross_cutting | 全部 | 交叉维度 |
| ic_report_synthesizer | 全部 | 统稿 |

### IC 18 Phase

```
01   topic_intake              课题元数据解析（DOCX/MD/JSON）
02   multi_company_verify      批量公司工商验证
04   research_plan             研究计划 → 子代理全权搜索 + archetype 判定
04   research_plan_collect     读子代理输出 → ic_research_plan.json
06   precompute                行业规模预计算 + 财务基准
07   dispatch_prepare          Wave 派发（sequential, archetype-driven）
08   dispatch_collect          Wave 收集 + 质量检查 [retry]
08b  fact_store_init           Fact Store 初始化
08b5 shared_state_init         共享状态初始化
09   evidence_gate             Step 输出质量门禁 [repair]
09b  fact_store_merge          Fact Store 合并
10   claim_coverage            Claim 覆盖校验（FAIL → 非阻断）
10b  cross_dimension_gate      跨维度一致性（FAIL → WARN 放行）
11   debate_review             对抗审查
11b  final_assembly            最终组装
11c  readability_review        可读性审查（FAIL → WARN 放行）
11d  investment_judgment       投资判断汇总（超配/标配/低配）
12   delivery                  交付 [heavy_bg]
```

---

## LIT 管线（文献综述）

**一句话**：技术方向 + 查询 → 子代理预搜拆解 sub_topics → 三路采集（学术/行业/企业）→ per-sub_topic 并行深读全部论文 → 质量评估 + 技术战略分析 → 完整评估报告交付。

### LIT 7 角色

| 角色 | Wave | 职责 |
|------|------|------|
| research_plan_enrichment | — | 子代理预搜 + 拆解 sub_topics + 输出 research_plan.json |
| academic_scout | W1 | S2 / OpenAlex / arXiv / DBLP / PMC / Crossref / CORE |
| industry_scout | W1 | NeoData / 研报 / 新闻 + IMA 知识库 |
| enterprise_scout | W1 | 天眼查 / SEC 企业尽调 + IMA 知识库 |
| deep_reader | W2 | per-sub_topic 并行深读（全文优先 / 摘要兜底） |
| tech_strategist | W2 | TRL / Gartner / 路线 / 竞争（缺失时 report_writer 自动 fallback） |
| report_writer | W3 | 完整 9 章评估报告 |

### LIT 20 Phase

```
01  intake                    解析用户输入
02  tech_decomposition        子代理预搜 + 拆解 sub_topics + research_plan.json
03  research_plan             cached check + 兜底生成
04  presearch                 方向可行性验证
05  shared_state_init         初始化共享状态
06  wave1_dispatch_prepare    W1 调度（3 角色 sequential）
07  wave1_dispatch_collect    W1 收集 + 4 层防御
08  wave1_evidence_gate       W1 质量门 + repair（PRISMA 完整性）
09  wave1_fact_store_merge    W1 合并 → fact_store
10  wave1_shared_state_refresh W1 刷新 → reading_tasks（含模糊匹配）
11  wave2_dispatch_prepare    W2 调度（per-sub_topic + tech_strategist）
12  wave2_dispatch_collect    W2 收集 + quality_summary 合并
13  wave2_evidence_gate       W2 质量门（quality_tier 容错）
14  wave2_shared_state_refresh W2 刷新
15  wave3_dispatch_prepare    W3 调度（report_writer + fallback）
16  wave3_dispatch_collect    W3 收集
17  claim_coverage            全 claim 覆盖检查
18  debate_review             跨章节对抗审查
19  final_assembly            排版 + 引用格式化 → report.md
20  delivery                  交付
```

### LIT 关键设计

- **per-sub_topic 并行**：deep_reader 拆分为 N 个独立 agent（每 sub_topic 一个），每个读全部论文，不再限 8 篇
- **phase02 子代理化**：子代理直接预搜 + 输出 research_plan.json，Coordinator 不再手写 JSON
- **quality_tier 容错**：gate 兼容 `quality_tier` / `overall_grade` / `grade` 字段名
- **tech_strategist fallback**：缺失时 gate 降为 WARN，report_writer 自动降级模式
- **模糊匹配兜底**：shared_state_refresh 对空 sub_topic 做关键词匹配补充

---

## 核心机制

### 闭环一：Kernel 状态机

管线核心引擎是 `runtime/orchestrator/kernel.py`，一个 Phase 状态机：

```
Phase 执行结果                    Kernel 动作
─────────────────────────────────────────────────────
ok=true, 无 dispatch          →  推进到下一个 Phase
ok=true, needs_dispatch       →  暂停，等子代理完成
ok=true, has_more=true        →  暂停，next_phase = 当前（再派一个子代理）
ok=false                      →  直接终止管线（不可恢复）
```

- **断点续跑**：每个 Phase 状态持久化到 `job_record.json`，中断后从最后 completed Phase 继续
- **依赖回填**：`start_phase` 从中间开始时，检查 `phase_prerequisites()` 依赖图，缺失文件精准回退重跑
- **`{task_id}` 占位符**：kernel 统一替换为实际 job_id（IR/IC 用占位符，BP 用固定文件名）

### 闭环二：Sequential 子代理派发

防止并行写冲突——prepare 每次只返回第一个未完成 role 的 manifest，kernel 看到 `has_more=true` 重跑 prepare 派下一个：

```
prepare() → 找第一个未完成 role → 返回 1 个 manifest + has_more: true
    → 主 AI 派发 → 完成 → 再跑 prepare → 派下一个
    → 所有 role 完成 → has_more: false → 推进到 collect
```

覆盖范围：所有 wave prepare + 3 个 repair 分支 + 统稿。

> ⚠️ **禁止后台派发**：Agent tool 不能传 `run_in_background=True`，否则通知延迟导致管线每步卡住。只有 Bash 跑 heavy_bg 脚本时才用后台。

### 闭环三：子代理三文件输出契约

每个子代理完成后必须输出 3 个文件，缺一不可：

| 文件 | 内容 | 下游消费者 |
|------|------|-----------|
| `.md` | 维度完整分析文本 | 统稿子代理读取 |
| `-facts.json` | 结构化事实（fact_id / claim / value / source_url / source_tier / confidence） | Fact Store 合并 → Claim 覆盖 → 脚注生成 |
| `-section.json` | Section Package（key_messages / claims[] / counter_evidence / data_gaps） | 门禁判定 |

**完成判定**：三文件全部存在 + 体积达标 + JSON 合法 + 文件大小 3 秒内不增长。

**收集重试**：`COLLECT_RETRY_COUNT=40 × COLLECT_RETRY_INTERVAL=30s = 20 分钟`。超时返回 `needs_dispatch` 重派。

### 闭环四：Fact Store — 全局事实数据库

所有子代理的 facts sidecar 合并为中央数据库，形成三条闭环：

1. **Claim 覆盖校验**：每个 claim 是否有对应 fact？没有 → repair 子代理补证据
2. **统稿脚注生成**：统稿从 Fact Store 提取 source_url 生成脚注，而非自己编造
3. **跨维度一致性**：不同 role 引用同一指标时，通过 fact_id 关联验证

### 闭环五：门禁 + Repair

```
门禁校验
    ├─ PASS → 推进
    ├─ REPAIR → 生成 manifest → sequential 派发 repair 子代理 → 重跑门禁
    │           超过最大重试 → 降级为 WARN 放行（标记 repair_exhausted）
    ├─ WARN → 记录到 deferred_fixes，不阻断
    └─ FAIL → 管线终止
```

| 门禁 | Phase | 最大 repair | 特殊处理 |
|------|-------|------------|---------|
| Wave 证据门禁 | P11 / P16 / P20 | 1 次 | T1/T2 blocking claims 直接降级 WARN |
| Claim 覆盖 | P22 | 2 次 | 超过后降级 PASS_WITH_DISCLOSURE |
| 统稿脚注 | P26 | 1 次 | 动态阈值：每 2000 字 ≥ 3 个脚注 |
| 对抗评审 | P27 | — | 仅 BLOCKING 硬阻断（空维度 / 100% 无 facts / 无 section） |
| 交付门禁 | P31 | — | 可读性 / 对抗 FAIL → WARN |

**文件锁保护**：repair 子代理写共享文件时用 `bp_file_lock.locked_read_modify_write()`（flock 独占锁），防止并行写丢数据。

### 闭环六：搜索系统 — 6 层降级链

```
Layer 0: NeoData（A/HK 股行情/财报/板块）    ← 金融查询首选
Layer 1: DuckDuckGo（通用搜索）
Layer 2: SearXNG（本地 Baidu+Bing）
Layer 3: Google 直接抓取
Layer 4: Scrapling StealthyFetcher（深度正文）
Layer 5: yfinance（美股估值）
```

**金融查询自动路由**：检测查询含"市值/营收/PE/财报"等关键词时优先走 NeoData。

**数据源优先级**：A/HK 股 → NeoData → yfinance（交叉验证）→ web_search；美股 → yfinance → web_search。

> ⚠️ 子代理环境无 `web_search` 内置工具，必须用 `search_deep(Bash)` / `tencent_news_search(Bash)` / `web_fetch(内置)` 替代。

---

## IMA 知识库（投行研报全文源）

四条管线全量接入 IMA 知识库，作为子代理的第一优先数据源：

| 库 | ID | 内容 | 可 fetch |
|----|-----|------|---------|
| **用户自建研报库**（主力） | `001a89fa4b807b92` | 投行/券商研报（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等），按周分文件夹 | ✅ 全文 |
| 行研智库 | `7311568991699459` | 行业研究报告 | ✅ 全文 |
| 机构调研纪要 | `7300811407257275` | 调研纪要 | ✅ NOTE 可 fetch |
| 精选行业报告 | `7302509206984644` | 精选行业报告 | ✅ 全文 |

> 已删除 2 个仅摘要库（长安投研 + 公司调研报告，合计 8.2 万篇但 0% 可 fetch）。

**使用纪律**：
- 优先取最近 30 天内的投行研报（超 1 个月参考价值显著下降）
- 标题含日期（如 `-260703.pdf` = 2026-07-03）据此判断时效
- 大行优先（GS / MS / JPM / BofA / Citi / UBS）
- IMA 与结构化源（TYC / NeoData / westock）**并行执行**，不是兜底

---

## 目录结构

```
ir-bp-workflow/
├── runtime/                              # 核心运行时（四线共用）
│   ├── profiles/                         # 管线 Profile
│   │   ├── base.py                       # 抽象基类
│   │   ├── ir_profile.py                 # IR 管线（7 step + 统稿 + Stage Tier）
│   │   ├── bp_profile.py                 # BP 管线（31 Phase + 3 波 + 统稿）
│   │   ├── bp_constants.py               # BP 共享常量
│   │   ├── ic_profile.py                 # IC 管线（18 Phase + 5 原型）
│   │   ├── ic_topic_profile.py           # IC 课题 Profile
│   │   ├── lit_review_profile.py         # LIT 管线（20 Phase + 3 波）
│   │   └── lit_constants.py              # LIT 共享常量
│   ├── orchestrator/
│   │   ├── kernel.py                     # Phase 执行引擎
│   │   ├── pipeline_orchestrator.py      # 管线类型路由
│   │   ├── state_store.py                # 状态持久化
│   │   └── workspace_layout.py           # 工作区布局
│   ├── entrypoints/                      # 四管线入口
│   │   ├── run_ir_pipeline_entry.py
│   │   ├── run_bp_pipeline_entry.py
│   │   ├── run_ic_pipeline_entry.py
│   │   └── run_lit_pipeline_entry.py
│   └── intake/
│       └── bp_document_intake.py         # BP 文档入库（OCR + 结构化）
├── scripts/                              # 功能脚本（150+）
│   ├── search_gateway.py                 # 搜索网关（6 层降级链）
│   ├── bp_subagent_launcher_wb.py        # BP 子代理发射器
│   ├── ir_subagent_launcher_wb.py        # IR 子代理发射器
│   ├── ic_subagent_launcher.py           # IC 子代理发射器（archetype-driven）
│   ├── bp_file_lock.py                   # 文件锁（flock 独占锁）
│   ├── bp_utils.py                       # BP 公共工具
│   ├── bp_delivery_gate.py               # 交付门禁
│   ├── bp_claim_coverage_validator.py    # Claim 覆盖校验
│   ├── bp_wave_evidence_gate.py          # Wave 证据门禁
│   ├── bp_cross_dimension_gate.py        # 跨维度一致性
│   ├── bp_investment_judgment.py         # BP 投资判断
│   ├── build_bp_dd_report_docx.py        # BP DOCX 生成
│   ├── build_ir_broker_report_docx.py    # IR DOCX 生成
│   ├── build_ic_industry_report_docx.py  # IC DOCX 生成
│   ├── build_lit_report_docx.py          # LIT DOCX 生成
│   ├── ir_investment_judgment.py         # IR 投资判断
│   ├── ir_step_docx.py                   # IR per-step DOCX
│   ├── ic_investment_judgment.py         # IC 投资判断
│   ├── ic_evidence_gate.py               # IC 证据门禁
│   ├── ic_precompute.py                  # IC 行业预计算
│   ├── ic_topic_intake.py                # IC 课题元数据解析
│   ├── fix_ir_sidecars_facts.py          # 修复 IR sidecar 元数据（补 source_url/绑定 fact_ids，历史任务用）
│   ├── rebuild_ir_facts_sidecars.py      # 重建 IR facts sidecar（phase10 合规，历史任务用）
│   ├── rebuild_ir_section_pkgs.py        # 重建 IR section package（phase11 合规，历史任务用）
│   ├── _bp_research_plan_subagent.py     # BP 研究计划子代理
│   ├── _bp_company_intake_subagent.py    # BP 公司名搜索入库子代理
│   ├── heavy_phase_bg.py                 # heavy phase 后台启动器
│   ├── phase_runner.py                   # phase 执行器（前台/后台双模式）
│   ├── api_clients/                      # LIT 学术 API 客户端
│   │   ├── openalex_client.py            # OpenAlex
│   │   ├── arxiv_client.py               # arXiv
│   │   ├── s2_client.py                  # Semantic Scholar
│   │   ├── dblp_client.py                # DBLP
│   │   ├── pmc_client.py                 # PubMed Central
│   │   ├── crossref_client.py            # Crossref
│   │   └── core_client.py                # CORE
│   ├── fulltext/                         # LIT 全文获取 + 提取
│   │   ├── pdf_downloader.py
│   │   ├── pdfplumber_extractor.py
│   │   ├── marker_extractor.py
│   │   └── web_scraper.py
│   ├── enterprise/                       # 天眼查企业数据查询
│   │   ├── company_profile.py            # 企业画像
│   │   ├── tyc_company_lookup.py         # 天眼查公司查询
│   │   └── tyc_patent_search.py          # 天眼查专利检索
│   └── search/                           # LIT 搜索
│       ├── unified_search.py             # 多源并行搜索 + 去重
│       ├── neodata_search.py             # NeoData 研报搜索
│       ├── dedup.py                      # DOI + title 去重
│       └── rate_limiter.py               # API 限速
├── instruction_store_bp/                 # BP 角色指令库（11 角色：R00-R10 + 统稿 S1，文件名带派发编号）
│   ├── index.json                        # 角色 → 文件映射（含 wave / dispatch_order 编号）
│   ├── _common_tool_guide.md             # 通用工具使用指南（含 IMA §3.6）
│   ├── bp_r00_research_plan.md           # R00/W0 研究计划生成（最先派发）
│   ├── bp_r01_company_team_compliance.md # R01/W1 团队合规
│   ├── bp_r02_product_commercial.md      # R02/W1 产品商业
│   ├── bp_r03_tech_ip_moat.md            # R03/W1 技术 IP
│   ├── bp_r04_market_supply_chain.md     # R04/W1 市场供应链
│   ├── bp_r05_competition_positioning.md # R05/W3 竞争定位
│   ├── bp_r06_valuation_return.md        # R06/W3 估值回报
│   ├── bp_r07_dealbreaker_risk.md        # R07/W4 红队风险
│   ├── bp_r08_consensus_challenge.md     # R08/W4 共识挑战
│   ├── bp_r09_catalyst.md                # R09/W4 催化剂
│   ├── bp_r10_industry_research.md       # R10/W4 行业研报
│   └── bp_s1_统稿.md                     # S1 统稿（唯一中文命名，最后派发）
├── instruction_store_ir/                 # IR 角色指令库（研究计划 + 7 个 step + 统稿）
│   ├── index.json                        # role → file 映射（preflight 校验）
│   ├── _common_tool_guide.md
│   ├── _shared_output_protocol.md
│   ├── ir_统稿.md                        # IR 统稿（论点驱动叙事）
│   ├── ir_research_plan.md               # phase04 研究计划子代理指令（含大行研报骨架）
│   ├── industry_overlays/                # 行业 Overlay（5 个）
│   │   ├── semiconductor.md
│   │   ├── consumer.md
│   │   ├── internet.md
│   │   ├── heavy_asset.md
│   │   └── financial.md
│   └── step*.md                          # 7 个 step 指令文件（step1_industry/2_biz/3_finance/4_mgmt/6_valuation/7_insight/8_risk，编号不连续，无 step5）
├── instruction_store_ic/                 # IC 角色指令库（16 角色）
│   ├── index.json                        # archetype → role → file 三级映射
│   ├── _common_tool_guide.md
│   ├── ic_research_plan_enrichment.md
│   ├── roles/                            # 16 个角色指令文件
│   ├── archetypes/                       # 5 个原型模板 JSON
│   └── archived/                         # 已归档角色指令（旧版）
├── instruction_store_lit/                # LIT 角色指令库（7 角色）
│   ├── index.json
│   ├── _common_tool_guide.md
│   ├── research_plan_enrichment.md
│   ├── academic_scout.md
│   ├── industry_scout.md
│   ├── enterprise_scout.md
│   ├── deep_reader.md
│   ├── tech_strategist.md
│   └── report_writer.md
├── references/                           # 项目级统一知识库（单一真实来源）
│   ├── pipeline/                         # 管线流程文档
│   ├── quality/                          # 质量门禁 + 验证策略
│   ├── operations/                       # 数据源 + 搜索 + 估值方法论
│   ├── delivery/                         # 交付协议
│   └── coordinator/                      # coordinator 专用文档
├── skills/                               # AI Agent Skill 定义
│   ├── ir-coordinator/SKILL.md           # 唯一活跃运行时 skill
│   ├── ir-researcher/SKILL.md            # 架构文档
│   ├── ir-reporter/SKILL.md              # 架构文档
│   └── ir-verifier/SKILL.md              # 架构文档
├── search/                               # 搜索引擎适配器
├── content/                              # 内容抓取 + PDF 提取
├── routing/                              # 数据源路由
├── config/                               # 运行时配置
├── rules/                                # 管线规则文档
├── sources/                              # 数据源解析/实体画像/喂入器
├── tasks/                                # 任务工具
├── tools/                                # 工具脚本
├── memory/                               # 记忆系统
├── docs/                                 # 文档
├── tests/                                # 测试
├── setup.sh                              # 一键安装
├── ir_runtime.py                         # CLI 管理入口
├── run_bp.py                             # BP 管线运行脚本
├── run_ic_topic_shim.py                  # IC 课题管线驱动 shim（修复 phase_runner pipeline 误判）
├── TOOLS.md                              # 工具使用说明（搜索/记忆/OCR 等）
└── requirements.txt                      # Python 依赖
```

---

## 🚀 安装

```bash
curl -fsSL https://raw.githubusercontent.com/Xavier-06/ir-bp-workflow/main/setup.sh | bash
```

安装脚本自动完成：克隆仓库 → 安装 Python 依赖 → 创建 .env → 安装 Skills → 创建运行时目录。

### 前置条件

| 依赖 | 必需？ | 说明 |
|------|--------|------|
| Python 3.10+ | ✅ | 管线运行 |
| WorkBuddy / OpenClaw | ✅ | AI Agent 平台 |
| VL 视觉模型（qwen3-vl 等） | BP 必需 | BP OCR 识别 |
| 天眼查 MCP（tyc-mcp） | BP/IC 必需 | 工商验证 / 竞争分析 |
| 腾讯自选股（westock-mcp） | 推荐 | 行情 / 财报 / 研报 / 行业数据 |
| IMA 知识库（ima-mcp） | 推荐 | 投行研报全文源 |
| DuckDuckGo Search | ✅ | 搜索引擎 |
| yfinance | ✅ | 金融估值数据 |
| NeoData | 推荐 | A/HK 股首选数据源 |
| SearXNG Docker | 推荐 | 本地搜索引擎 |
| HTTP 代理 | 按需 | Google / Scrapling 翻墙 |

### 环境变量

```bash
# BP 管线必需
VL_API_BASE=https://your-vl-api-base/v1
VL_API_KEY=sk-xxxx

# 可选
SEARXNG_URL=http://127.0.0.1:8888
PROXY_URL=http://127.0.0.1:7897
IR_RESEARCH_TIER=deep          # deep / standard / quick
```

## 📋 使用

**对话触发（推荐）：**
- "分析比亚迪" → IR 管线
- "帮我看下这个 BP" + 上传文件 → BP 管线
- "做个半导体行业研究" / "产业链全景" → IC 管线
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
