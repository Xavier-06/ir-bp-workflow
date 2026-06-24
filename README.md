# 🐲 IR/BP Workflow

> AI 驱动的投研（IR）+ 商业计划书尽调（BP）双管线工作流，专为 WorkBuddy / OpenClaw 平台设计。
> 从数据采集到研报交付，全自动运行，零人工干预。

## 🏗️ 双管线架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ir-coordinator（调度中心）                       │
│            接收自然语言指令 → 自动识别管线类型 → 全自动执行             │
├───────────────────────────────┬─────────────────────────────────────┤
│     IR 管线（券商研报）         │       BP 管线（尽调报告）              │
│     9 步 · 5 波子代理           │       33 Phase · 4 波 + 统稿          │
│                               │                                      │
│ ┌─ 预搜索阶段 ─────────────┐  │  ┌─ 文档识别 ──────────────────┐     │
│ │ P0  环境检测 + 任务注册    │  │  │ P01  VL OCR 文档识别        │     │
│ │ P0.5 公司验证 + 估值预取   │  │  │ P02  企查查工商/风险验证     │     │
│ │ P1   8步预搜索 + URL提取   │  │  │ P03  研究计划               │     │
│ │ P1.5 URL内容提取           │  │  │ P04  4维度预搜索            │     │
│ │ P12  三引擎预计算          │  │  │ P05  共享尽调页初始化        │     │
│ └──────────────────────────┘  │  │ P06  搜索工单编译            │     │
│                               │  │ P07  Fact Store 初始化       │     │
│ ┌─ 5波子代理派发 ─────────┐   │  └──────────────────────────────┘     │
│ │ W1  技术+数据采集         │   │                                      │
│ │ W2  行业/商业/财务/管理    │   │  ┌─ 4波并行分析 + 门禁 ────────┐    │
│ │ W3  估值                  │   │  │ W1-P08~P12  派发→收集→门禁   │    │
│ │ W4  洞察+风险             │   │  │ W2-P13~P15  派发→收集→门禁   │    │
│ │ W5  统稿 (11-Agent)       │   │  │ W3-P16~P18  派发→收集→门禁   │    │
│ └──────────────────────────┘   │  │ W4-P20~P22  派发→收集→门禁   │    │
│                               │  └──────────────────────────────┘     │
│ ┌─ 交付阶段 ─────────────┐    │                                      │
│ │ 跨Step一致性 + 对抗验证   │    │  ┌─ 校验+统稿+交付 ────────────┐    │
│ │ DOCX生成 + 桌面复制       │    │  │ P24  Claim覆盖校验 [repair] │    │
│ └─────────────────────────┘    │  │ P25  跨维度一致性             │    │
│                               │  │ P26  Section Package校验      │    │
│                               │  │ P27~P28 统稿 [repair]         │    │
│                               │  │ P29  对抗评审                 │    │
│                               │  │ P30  最终组装                 │    │
│                               │  │ P31  可读性审查               │    │
│                               │  │ P32  投资判断汇总             │    │
│                               │  │ P33  交付 DOCX + 桌面         │    │
│                               │  └──────────────────────────────┘     │
├───────────────────────────────┴─────────────────────────────────────┤
│                         共享基础设施                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │
│  │ 有状态编排器   │ │ 搜索网关(6层) │ │ Fact Store   │ │ 文件锁机制  │ │
│  │ kernel.py     │ │ search_gw    │ │ fact_store   │ │ bp_file_lock│ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
         ↓ 交付 ↓
   📊 券商级研报 / DD尽调报告 (DOCX) → 桌面 + 聊天窗口告知路径
```

## ❓ 解决什么问题

### 痛点一：AI 投研"浅尝辄止"
市面 AI 研报工具只能生成"资料汇编"——堆砌公开信息，缺乏投研逻辑深度。真正的券商研报需要 9 个维度的系统性分析（数据/行业/商业/财务/管理层/洞察/估值/风险/统稿），单轮对话无法完成。

### 痛点二：多 Agent 协作"各自为政"
现有 Agent 框架（AutoGPT、CrewAI）的痛点：子 Agent 挂掉（code=10003）、上下文断裂、数据口径不一致、最终需要人工拼接。我们构建了**有状态编排器**——统一状态协调、manifest 派发、自动重试、断点续跑。

### 痛点三：BP 尽调"信息黑洞"
早期项目的 BP 尽调面临两难：创始人自说自话（信息偏差）vs 昂贵的人工尽调（成本高、周期长）。本管线自动化完成 **VL OCR → 结构化抽取 → 4 维度并行分析 → 竞争格局 → 统稿 → DOCX 交付**，33 个 Phase 全链路自动。

### 痛点四：交付链路断裂
研报写完了，但复制到桌面、转 DOCX——这些"最后一公里"经常被模型遗忘。管线有**强制 finalize 步骤**：对抗验证 → DOCX 生成 → 桌面复制（三步协议），报告不会丢。

### 痛点五：数据时效性和真实性
AI 生成研报最大的隐患：**编造不存在的人名、使用过时的融资状态、引用已撤销的政策**。管线引入 **ANTI-DEFECT RULES**——每个 step/维度都有专属验证规则（人员存在性验证、融资状态搜索验证、数据时效性检查等），从根源上防止幻觉。

## 🧠 核心设计

### 设计一：有状态编排器（Phase-Driven Orchestrator）

管线由 Phase 序列驱动，每个 Phase 有明确的输入/输出声明和依赖关系。编排器（`kernel.py`）负责：

| 能力 | 机制 |
|------|------|
| Phase 状态机 | `pending → running → completed / failed`，持久化到 `job_record.json` |
| 断点续跑 | 中断后从任意 Phase 恢复，不丢中间结果 |
| 依赖回填 | `phase_prerequisites()` + `phase_outputs()` 声明依赖图，缺失时精准回填而非从头重跑 |
| 暂停恢复 | `needs_dispatch` → 暂停等子代理 → `has_more` 机制循环派发 → 自动推进 |
| 三种执行模式 | 同步直跑 / 后台轮询（heavy phase）/ 暂停等派发（子代理） |

### 设计二：Profile 模式 — 双管线共享内核

IR 和 BP 管线共用同一套编排器、搜索网关、子代理发射器等基础设施，差异通过 Profile 定义：

```
runtime/profiles/
├── base.py          # 抽象基类：phases() / handler() / dispatch()
├── ir_profile.py    # IR 管线：9 步 + 5 波
├── bp_profile.py    # BP 管线：33 Phase + 4 波 + 统稿
└── ic_profile.py    # IC 管线：行业研究（第三管线）
```

### 设计三：证据链 — 从 Claim 到 Report 的数据流

管线的核心理念是 **evidence chain（证据链）**：每个分析结论都必须有可追溯的证据支撑，而非 AI 凭空生成。数据在管线中按以下链路流动：

```
Claim（待验证的主张）
  → Search（搜索网关 6 层降级链）
    → Source（来源评级：T0官方/T1权威/T2可信/T3一般）
      → Fact（结构化事实，存入 Fact Store）
        → Section（维度分析，含 Section Package）
          → Gate（证据门禁校验）
            → Synthesis（统稿）
              → Report（最终 DOCX）
```

#### 子代理三文件输出契约

每个 BP 子代理（无论是分析 role 还是 repair role）完成后必须输出 **3 个文件**，这是管线的硬契约：

```
jobs/{JOB_ID}/outputs/
├── bp_phase2_{slug}.md             ← 文件 1：Markdown 正文
├── bp_phase2_{slug}-facts.json     ← 文件 2：事实 Sidecar
└── bp_phase2_{slug}-section.json   ← 文件 3：Section Package Sidecar
```

| 文件 | 格式 | 内容 | 下游消费者 | 为什么需要它 |
|------|------|------|-----------|-------------|
| **`.md`** | Markdown | 该维度的完整分析文本（>100 bytes） | 统稿子代理直接读取，拼入最终报告 | 人类可读的分析产物 |
| **`-facts.json`** | JSON | 结构化事实数组：每条含 `fact_id`, `claim`, `value`, `unit`, `period`, `source_url`, `source_tier`, `source_quote`, `confidence`（>10 bytes） | Fact Store 合并（P11）→ Claim 覆盖校验（P24）→ 统稿脚注生成 | 将"AI 说了什么"变成"可验证的结构化数据"，是整个证据链的核心 |
| **`-section.json`** | JSON | Section Package：`schema_version`, `section_id`, `section_title`, `key_messages`, `claims[]`（含 `fact_ids` 引用）, `counter_evidence`, `data_gaps`, `markdown_draft`（>10 bytes） | Section Package 校验（P26）→ 门禁判定 PASS/FAIL | 结构化的"这一节说了什么、哪些是核心观点、哪些有证据、哪些是数据缺口" |

**写入顺序**：子代理先写 `.md` 再写 sidecar（JSON 序列化耗时较长）。因此 collect 阶段不能只看 `.md` 是否存在就认为完成。

**完成判定（`_role_outputs_complete`）**：三文件全部存在 + 体积达标 + JSON 合法 + 文件大小稳定（3 秒内不增长）才算一个 role 完成。

#### Fact Store — 全局事实数据库

所有子代理的 facts sidecar 在 P11（Fact Store Merge）阶段合并为中央 `bp_fact_store.json`：

```json
{
  "task_id": "TASK-20260615-001",
  "entity": "XX科技",
  "market": "cn",
  "facts": [
    {
      "fact_id": "F-001",
      "claim": "2024年营收",
      "value": "3.2",
      "unit": "亿元",
      "source_url": "https://...",
      "source_tier": "T1",
      "source_quote": "原文摘录...",
      "confidence": "high"
    }
  ],
  "conflicts": []
}
```

Fact Store 的作用：
- **Claim 覆盖校验（P24）**：检查每个 claim 是否有对应的 fact 证据，没有则触发 repair
- **统稿脚注生成（P27-28）**：统稿子代理从 Fact Store 提取 source_url 生成脚注，而非自己编造
- **跨维度一致性**：不同 role 对同一指标的引用通过 fact_id 关联，避免矛盾

#### Section Package — 结构化质量校验

Section Package 让管线能用程序化的方式校验每个维度的输出质量，而非依赖 AI 主观判断：

```json
{
  "schema_version": "bp_section_package.v2",
  "section_id": "tech",
  "section_title": "技术与产品",
  "key_messages": ["核心技术壁垒为...", "产品矩阵覆盖..."],
  "claims": [
    {
      "claim": "自研 XX 算法精度达 99.5%",
      "fact_ids": ["F-042", "F-043"],
      "reasoning": "来源为论文 + 客户验证",
      "confidence": "high",
      "source_quality": "T1"
    }
  ],
  "counter_evidence": ["竞品 YY 声称同等精度..."],
  "data_gaps": ["缺少第三方独立评测"],
  "markdown_draft": "## 技术与产品\n..."
}
```

校验维度：`claims` 是否有 `fact_ids` 引用 → `fact_ids` 是否在 Fact Store 中存在 → `counter_evidence` 是否非空 → `data_gaps` 是否标注 → 门禁综合判定 PASS / FAIL / WARN。

### 设计四：Wave Evidence Gate Repair 机制

门禁不再是非 PASS 即 FAIL 的二元判断。gate FAIL 时触发 repair 子代理修复，而非直接终止管线：

```
gate FAIL → REPAIR verdict → 生成 repair manifest（按 role 聚合）
    → sequential 派发单个 repair 子代理 → 重跑 gate
    → 超过 _MAX_BLOCKING_RETRIES → 降级为 WARN 放行
```

覆盖范围：
- **Wave 门禁**（P10/P15/P18/P22）：每个 wave 收集后检查 evidence 完整性
- **Claim 覆盖校验**（P24）：检查所有 claim 是否有足够证据支持
- **统稿收集**（P28）：脚注密度动态阈值（每 2000 字 ≥ 3 个脚注）

关键设计决策：
- **Sequential 派发**：repair 子代理逐个执行（而非并行），避免 fact_store/sidecar 写冲突
- **T1/T2 早期项目降级**：种子轮到 A 轮的项目，blocking claims 直接降级为 WARN，不走 repair
- **文件锁**：`bp_file_lock.py` 提供 `locked_read_modify_write()` + `atomic_write()`，repair 子代理写共享文件时加 flock

### 设计四：Sequential Dispatch — 防并行写冲突

BP 管线的 4 个 wave 各有多个 role（如 Wave1 有技术/数据两个 role），每个 role 都要往 `fact_store.json` 和 `section sidecar` 写数据。之前并行派发导致数据丢失。

解决方案：参照 IR 管线的 `has_more` 机制，prepare 函数每次只返回一个 manifest：

```
prepare() → 找第一个未完成的 role → 返回 {needs_dispatch: true, has_more: true, manifests: [1个]}
    → kernel 看到 has_more=true → next_phase = 当前 phase（重跑）
    → 主 AI 派发下一个 role
    → 所有 role 派完 → has_more: false → kernel 推进到 collect phase
```

### 设计五：ANTI-DEFECT RULES — 反幻觉验证体系

每个 step/维度内置专属验证规则，从根源防止 AI 幻觉：

| 规则 | 适用步骤 | 防御目标 |
|------|---------|---------|
| 融资状态验证 | step1, BP 竞争 | 防止引用已 IPO 公司的过时融资数据 |
| 人员存在性验证 | step5, BP 团队 | 防止编造不存在的高管/董事姓名 |
| 数据时效性检查 | step4, step6b | 确保财务/估值数据在 6 个月内 |
| 可比公司状态验证 | step6b, BP 估值 | 确认 comps 表中公司仍在经营/已上市 |
| 政策时效性验证 | step7, BP 行业 | 确认引用的政策仍然有效 |
| 竞品运营状态 | step3, BP 技术 | 确认竞品未被收购/重组/转型 |
| 审计意见检查 | step4 | 关注审计意见变更（无保留→保留 = 红旗） |
| 跨 step 一致性 | step8 | 同一实体在不同 step 中的状态描述一致 |

### 设计六：搜索系统 — 6 层降级链 + NeoData

```
Layer 0: NeoData 金融数据（A/HK股行情、财报、板块、研报）— 需 WorkBuddy NeoData skill
Layer 1: DuckDuckGo（通用搜索，免密钥）
Layer 2: SearXNG 本地实例（Baidu + Bing 补充）
Layer 3: Google 直接抓取（走代理，自己解析）
Layer 4: Scrapling StealthyFetcher（深度正文提取）
Layer 5: yfinance 估值数据（IR 管线专用）
```

- **金融查询自动路由**：搜索网关自动检测金融类查询（股价/财报/估值/PE 等），优先走 NeoData
- **数据源优先级**：A/HK 股 → NeoData → yfinance(交叉验证) → web_search；美股 → yfinance → web_search
- 7 个适配器（NeoData/DDG/SearXNG/SEC/HKEX/Yahoo/RSS），支持实体解析、查询计划、证据评级

### 设计七：4 角色 Agent 协作

| Agent | 职责 | 触发方式 |
|-------|------|---------|
| **ir-coordinator** | 调度中心，识别管线类型，编排全自动执行 | 用户对话直接触发 |
| **ir-researcher** | 单维度数据采集，自主补搜闭环 | coordinator 内部调度 |
| **ir-reporter** | 统稿 + DOCX + 对抗验证 + 交付 | coordinator 内部调度 |
| **ir-verifier** | 6 层对抗验证（L1-L5 脚本 + L6 人工论证） | coordinator 内部调度 |

### 设计八：质量门禁矩阵

| 门禁 | IR 管线 | BP 管线 | 失败策略 |
|------|--------|--------|---------|
| Step/Wave 完整性 | <50% → 熔断 | evidence gate FAIL → repair → 降级放行 | repair 子代理修复 |
| 跨维度一致性 | 跨 Step 一致性 FAIL → 必须修正 | P25 HIGH→WARN 放行 | 不阻断交付 |
| ANTI-DEFECT | 每个 step 输出前验证 | 每个 wave gate 验证 | 搜索验证而非信任模型 |
| Claim 覆盖 | — | P24 not_addressed → repair → 降级 | repair 子代理补证据 |
| 统稿质量 | — | P28 脚注密度不达标 → repair | repair 子代理补脚注 |
| 对抗评审 | L6 主动找证据推翻结论 | P29 对抗评审 | T1/T2 降级为 WARN |
| 完成率 | <50% → 阻断交付 | — | 不交付半成品 |

### 设计九：全自动交付

```
finalize_pipeline() → 对抗验证 → DOCX 生成 → 桌面复制 → 聊天窗口告知路径
```

## 📦 目录结构

```
ir-bp-workflow/
├── runtime/                         # 核心运行时（三线共用）
│   ├── profiles/                    # 管线 Profile
│   │   ├── base.py                  # 抽象基类
│   │   ├── ir_profile.py            # IR 管线（9步 + 5波）
│   │   ├── bp_profile.py            # BP 管线（33 Phase + 4波 + 统稿）
│   │   ├── bp_constants.py          # BP 共享常量
│   │   └── ic_profile.py            # IC 管线（行业研究）
│   ├── entrypoints/                 # 入口点
│   ├── intake/                      # 输入处理（BP OCR）
│   └── orchestrator/                # 管线编排器
│       └── kernel.py                # Phase 执行引擎
├── scripts/                         # 功能脚本（180+）
│   ├── ir_subagent_launcher_wb.py   # IR 子代理发射器
│   ├── bp_subagent_launcher_wb.py   # BP 子代理发射器
│   ├── ic_subagent_launcher.py      # IC 子代理发射器
│   ├── search_gateway.py            # 搜索网关 v5（6层降级链）
│   ├── build_ir_broker_report_docx.py  # IR 研报 DOCX
│   ├── build_bp_dd_report_docx.py   # BP DD DOCX
│   ├── build_valuation_excel.py     # 估值 Excel 生成
│   ├── verification_agent.py        # 6层对抗验证
│   ├── bp_file_lock.py              # 文件锁（防并行写冲突）
│   ├── bp_utils.py                  # BP 公共工具
│   ├── bp_delivery_gate.py          # 交付门禁
│   ├── bp_claim_coverage_validator.py  # Claim 覆盖校验
│   ├── bp_wave_evidence_gate.py     # Wave 证据门禁
│   ├── bp_narrative_assembler.py    # 叙事组装器
│   ├── bp_company_verify.py         # 公司验证（企查查 MCP）
│   ├── ir_auto_orchestrator.py      # IR 全自动编排器
│   ├── info_propagation_check.py    # 7-Agent 信息传导验证
│   ├── sector_agent_middleware.py   # 板块代理中间件
│   ├── sector_benchmarks.py/v2.py   # 行业基准测试
│   ├── ensemble_runner.py           # 多策略集成执行器
│   ├── financial_metrics_precompute.py  # 财务指标预计算
│   └── ...                          # 运维工具集（清理/监控/健康检查）
├── instruction_store_ir/            # IR 角色指令库（11 个角色）
├── instruction_store_bp/            # BP 角色指令库（7 个维度）
├── skills/                          # AI Agent Skill 定义
│   ├── ir-coordinator/SKILL.md      # 🧠 调度中心
│   ├── ir-researcher/SKILL.md       # 🔍 数据采集 Agent
│   ├── ir-reporter/SKILL.md         # 📝 统稿 Agent
│   └── ir-verifier/SKILL.md         # 🛡️ 对抗验证 Agent
├── search/                          # 搜索子系统
│   ├── adapters/                    # 7 个搜索引擎适配器
│   │   ├── ddg.py / hkex.py / rss.py / searxng.py
│   │   ├── sec.py / tavily.py / yahoo.py
│   └── models/                      # 搜索数据模型
├── memory/                          # 分层记忆系统
├── research/                        # 研究子系统
├── content/                         # 内容抓取（Scrapling 三层递进）
├── routing/                         # 路由子系统
├── rules/                           # 执行协议
├── docs/                            # 文档
├── tests/                           # 测试
├── setup.sh                         # 🚀 一键安装脚本
├── ir_runtime.py                    # CLI 管理入口
├── run_bp.py                        # BP 管线运行脚本
├── .env.example                     # 环境变量模板
├── requirements.txt                 # Python 依赖
└── README.md
```

## 🚀 一键安装

```bash
# 一行命令安装
curl -fsSL https://raw.githubusercontent.com/Xavier-06/ir-bp-workflow/main/setup.sh | bash

# 或手动安装
git clone https://github.com/Xavier-06/ir-bp-workflow.git ~/.workbuddy/ir_runtime
cd ~/.workbuddy/ir_runtime && bash setup.sh
```

安装脚本自动完成：克隆仓库 → 安装 Python 依赖 → 创建 .env → 安装 4 个 Skills → 创建运行时目录 → 验证管线编排器

### 前置条件

| 依赖 | 版本 | 用途 | 必需？ |
|------|------|------|--------|
| Python | 3.10+ | 管线运行 | ✅ 必需 |
| WorkBuddy / OpenClaw | 最新版 | AI Agent 平台 | ✅ 必需 |
| DuckDuckGo Search | `pip install duckduckgo-search` | 搜索引擎 | ✅ 必需 |
| yfinance | `pip install yfinance` | 金融数据 | ✅ 必需 |
| SearXNG | Docker 镜像 | 本地搜索引擎 | ⚡ 推荐 |
| HTTP 代理 | Clash/V2Ray 等 | Google/Scrapling 翻墙 | ⚡ 按需 |
| NeoData | WorkBuddy 内置 | A/HK 股金融数据 | ⚡ 推荐 |
| VL 视觉模型 | qwen3-vl 等 | BP OCR | ⚡ BP 管线需 |
| 企查查 MCP | WorkBuddy 内置 | BP 工商验证/竞争分析/知识产权 | ⚡ BP 管线需 |

### MCP 工具依赖

BP 管线的 Phase 02 工商验证和维度分析大量依赖企查查 MCP（WorkBuddy 内置），需要在 WorkBuddy 设置中连接 `qcc-company` 服务：

| MCP 工具 | 能力 | 管线用途 |
|---------|------|---------|
| `mcp__qcc-company` | 工商信息（股东、注册资本、法人、变更记录、分支机构、对外投资） | P02 验证、竞争分析、产业链分析 |
| `mcp__qcc-risk` | 风险信息（诉讼、失信被执行人、行政处罚、经营异常） | 团队合规维度 |
| `mcp__qcc-ipr` | 知识产权（专利、商标、著作权） | 团队合规维度 |
| `mcp__qcc-operation` | 经营信息（招投标、资质许可、年报） | 竞争分析、行业供应链、估值 |

> **注意**：企查查 MCP 仅用于 BP 管线。IR 管线通过搜索网关获取工商数据，不依赖 MCP。

### 网络配置说明

**搜索网关采用 6 层降级链**，即使没有代理也能用，但部分层需要特定网络条件：

| 层 | 搜索引擎 | 需要代理？ | 说明 |
|----|---------|-----------|------|
| Layer 0 | NeoData | ❌ | 腾讯内网，WorkBuddy 自动鉴权 |
| Layer 1 | DuckDuckGo | ❌ | 直接搜索，无需翻墙 |
| Layer 2 | SearXNG | ❌ | 本地 Docker 实例 |
| Layer 3 | Google | ✅ | 需要代理 (`PROXY_URL`) |
| Layer 4 | Scrapling | ✅ | StealthyFetcher 走代理 |
| Layer 5 | yfinance | ❌ | Yahoo Finance API |

**配置代理**（仅 Google/Scrapling 层需要）：
```bash
# 在 .env 中设置
PROXY_URL=你的代理端口
```

**配置 SearXNG**（推荐，提升搜索质量）：
```bash
docker run -d -p 8888:8888 --name searxng searxng/searxng:latest
```

**配置 NeoData**（推荐，A/HK 股首选数据源）：
- 通过 WorkBuddy 的 `neodata-financial-search` skill 自动获取 token
- 搜索网关会自动将金融类查询路由到 NeoData Layer 0

## 📋 使用方式

### IR 管线：股票研报

对话触发（推荐）：
- "分析比亚迪"
- "跑个研报看看腾讯"
- "对优必选做个尽调"

ir-coordinator 自动识别意图并启动 IR 管线。

### BP 管线：商业计划书尽调

对话触发（推荐）：
- "帮我看下这个 BP" + 上传文件
- "分析一下 XX 公司的商业计划书"

### CLI 管理命令

`ir_runtime.py` 提供命令行管理入口，支持任务全生命周期操作：

```bash
# 环境检测
python3 ir_runtime.py check

# 创建任务
python3 ir_runtime.py create "比亚迪" --type 专题研究类

# 执行管线
python3 ir_runtime.py run TASK-20260515-001

# 重命名任务（修改标的/类型/标签）
python3 ir_runtime.py rename TASK-20260515-001 --target "优必选" --type 快报类
python3 ir_runtime.py rename TASK-20260515-001 --label "重点跟踪"

# 查看任务状态
python3 ir_runtime.py status TASK-20260515-001

# 列出所有任务
python3 ir_runtime.py list
```

| 命令 | 功能 | 说明 |
|------|------|------|
| `check` | 环境检测 | Python依赖/API凭证/搜索服务/子模块完整性 |
| `create` | 创建任务 | 指定标的和类型（专题研究/晨报/快报/资料整理/回顾） |
| `run` | 执行管线 | 从指定Phase开始，默认从Phase 0全自动运行 |
| `rename` | 重命名任务 | 修改任务标的、类型或标签，自动同步到索引 |
| `status` | 查看状态 | 显示任务当前Phase/完成率/步骤详情 |
| `list` | 列出任务 | 按创建时间排序，显示标的/类型/状态 |

### 运维工具集

`scripts/` 目录提供完整的运维工具链，覆盖日常巡检、清理和监控：

| 工具 | 用途 | 用法 |
|------|------|------|
| `check-reminders.sh` | 提醒检查 | `bash scripts/check-reminders.sh` |
| `check-skills.sh` | Skills 健康检查 | `bash scripts/check-skills.sh` |
| `cleanup_completed_tasks.sh` | 已完成任务清理 | `bash scripts/cleanup_completed_tasks.sh` |
| `cleanup_memory.sh` | 记忆文件清理 | `bash scripts/cleanup_memory.sh` |
| `cleanup_sessions.sh` | 会话文件清理 | `bash scripts/cleanup_sessions.sh` |
| `start_local_searxng.sh` | 启动本地SearXNG | `bash scripts/start_local_searxng.sh` |
| `watch-agent.sh` | Agent运行监控 | `bash scripts/watch-agent.sh` |
| `load_workspace_env.sh` | 环境变量加载 | `source scripts/load_workspace_env.sh` |
| `python_ssl_env.sh` | Python SSL配置 | `source scripts/python_ssl_env.sh` |
| `tools/patch_paths.py` | 路径修复工具 | `python3 tools/patch_paths.py --root $HOME/.workbuddy/ir_runtime` |

## 🎯 设计理念

1. **Phase 驱动** — 管线由 Phase 序列组成，可独立运行/暂停/恢复
2. **Profile 模式** — IR/BP/IC 共享编排内核，Profile 定义差异
3. **子代理自主闭环** — 数据缺口时自主补搜，不回主控等待
4. **搜索可插拔** — 网关抽象层 + NeoData Layer 0，支持多种搜索引擎/插件
5. **断点续跑** — 中断后从任意 Phase 恢复
6. **交付清洗** — 报告绝不暴露内部路径/Task ID
7. **Zero Human Intervention** — 全自动推进，无需发"继续"
8. **ANTI-DEFECT** — 每个 step 都有反幻觉规则，搜索验证而非信任模型记忆

## 📊 项目数据

- **300+ 个 Python 文件**，**~77,000 行代码**
- **IR 管线**：9 步分析 + 5 波子代理 + 对抗验证
- **BP 管线**：33 Phase + 4 波并行分析 + 3 层 repair 机制
- **已分析标的**：AVGO、泡泡玛特、优必选、东江环保、佰维存储、阅文集团、中芯国际、及部分融资项目等
- **交付物**：券商级 DOCX 研报（执行摘要 + 估值分析 + 风险矩阵 + 免责声明）+ 估值 Excel
- **自动化率**：全管线全自动，Zero Human Intervention

## ⚙️ 环境变量配置（.env）

安装后编辑 `.env` 文件，配置你的 API 密钥和服务：

```bash
# ── VL OCR（BP 管线必需）──
# 用于 BP 文档的 OCR 识别和结构化抽取
# 支持任何兼容 OpenAI API 格式的视觉模型（如 qwen3-vl）
VL_API_BASE=https://your-vl-api-base/v1   # ← 必填
VL_API_KEY=sk-xxxx                         # ← 必填
VL_MODEL=qwen3-vl-30b-a3b-instruct        # 默认模型名

# ── 搜索（可选，不配也能用）──
# SearXNG 本地搜索（推荐）: docker run -d -p 8888:8888 --name searxng searxng/searxng:latest
SEARXNG_URL=http://127.0.0.1:8888

# HTTP 代理（Google/Scrapling 层需要）
PROXY_URL=http://127.0.0.1:7897

# ── WorkBuddy MCP 服务（BP 管线必需）──
# 在 WorkBuddy 设置中启用以下 MCP connector：
# - qcc-company  （工商验证）
# - qcc-risk     （风险扫描）
# - qcc-ipr      （知识产权）
# - qcc-operation（经营信息）
```

### 必需 vs 可选配置速查

| 配置项 | 必需？ | 说明 |
|--------|--------|------|
| `VL_API_BASE` + `VL_API_KEY` | **BP 必需** | 任何兼容 OpenAI API 的视觉模型服务 |
| WorkBuddy MCP: `qcc-*` 系列 | **BP 必需** | 在 WorkBuddy Connector 设置中连接企查查 |
| `SEARXNG_URL` | 推荐 | 提升搜索质量，不配则降级到 DDG |
| `PROXY_URL` | 按需 | Google/Scrapling 层需要翻墙时使用 |
| NeoData skill | 推荐 | WorkBuddy 内置，A/HK 股首选数据源 |
| GitHub connector | 推荐 | 用于 `gh` CLI 搜索能力 |

### 快速验证安装

```bash
# 1. 检查环境
python3 ir_runtime.py check

# 2. 试运行 IR 管线
python3 ir_runtime.py create "测试公司" && python3 ir_runtime.py run TASK-XXXXXXXX-XXX

# 3. 对话触发（推荐）
# 在 WorkBuddy 中直接说："分析比亚迪" 或 "帮我看下这个 BP"
```

## 📄 License

MIT License

---

*Built with 🐲 for the AI agent community*
