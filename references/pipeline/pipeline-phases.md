# BP 管线 Phase 详解

## Phase 0: Document Intake

**输入**：BP 文件（PDF/PPTX/DOCX/图片）
**输出**：`bp_ocr_text.txt` + `bp_step0_profile.json`

流程：
1. 根据文件类型选择解析策略：
   - PDF → VL 模型 OCR（优先） → pdfminer（回退） → PyPDF2（兜底）
   - PPTX → LibreOffice 渲染逐页 PNG → VL OCR → python-pptx（回退）
   - DOCX → python-docx 读文字 → 嵌入图片 VL OCR
   - 图片 → VL OCR 直接识别
2. VL 模型做结构化抽取（公司名/行业/融资阶段/团队/财务/竞品等）
3. 基于客观事实推断融资阶段（硬规则，不靠 VL 判断）

**融资阶段推断规则**：
- 搜不到公开工商/财报信息 → 绝不可能是 Pre-IPO/C轮+
- 零营收 → 不可能是 B轮+
- "量产样机/工程样机" → 判为"小批量"而非"量产"

## Phase 0.5: Company Verify + 护城河锚定

**输入**：`bp_step0_profile.json`
**输出**：`company_verify_report.json` + `company_verify_report.md` + `bp_moat_analysis.md`

### Part A: 公司核验

流程：
1. 读取 Step0 Profile 获取公司名、创始人、融资阶段
2. 根据融资阶段选择搜索策略：
   - 早期（种子/天使）：跳过工商搜索，聚焦创始人个人 + 技术验证
   - 成熟（A轮+）：搜工商注册、法定代表人等
3. 创始人履历验证（所有阶段）
4. 顾问验证（直接搜顾问本人）
5. 风险搜索（早期搜创始人个人风险，成熟搜公司风险）

### Part B: 护城河锚定

在核验完成后，根据 Step0 提取的商业模式，执行"发动机/油箱"分析：

| 商业模式 | 发动机（真壁垒） | 油箱（可替代部分） |
|----------|-----------------|-------------------|
| IDM | 工艺制程/良率/外延质量 | 封装测试/标准应用 |
| Fabless | 差异化IP/代工资源/架构设计 | 外延制造/标准工艺节点 |
| 系统集成 | 方案定义能力/核心模块自主性 | 标准零部件/成熟制造工艺 |
| 平台型 | 技术平台完整性/跨场景复用/生态协同 | 单点技术绝对性能 |
| ERP/MES/工业软件 | 核心算法/行业Know-how/客户黏性 | 标准SaaS功能/通用实施 |

搜索任务：
- `[赛道]` + 核心瓶颈/物理极限/工程难题
- `[赛道]` + 成本结构/规模化障碍
- `[赛道]` + 客户采购决策因子/替代方案
- `[赛道]` + 失败案例/被放弃的技术路线

叙事断裂检测：
- BP 声称的技术壁垒 vs 公开信息的落差
- 发动机是否足以驱动油箱
- 关键叙事是否存在逻辑断裂

**输出**：护城河分析结果写入 `company_verify_report.md` 的"护城河锚定"章节

## Phase 1: Presearch

**输入**：`bp_step0_profile.json`
**输出**：`bp_presearch_step_*.md`（4 个维度）+ `bp_presearch_results.json`

流程：
1. 读取 Profile 获取 entity、tech、industry、founder 等变量
2. 根据融资阶段选择查询模板（early_stage vs mature_stage）
3. 对 4 个维度（team/tech/industry/competition）分别搜索
4. 去重、截断，输出 Markdown 格式搜索结果

## Phase 2: 多维度分析

### Wave 1（Phase 2a-2b）

**子代理**：前 3 个维度并行

| 维度 | 角色 | 输出 |
|------|------|------|
| 团队与合规 | bp_团队与合规 | `bp_dim_team.md` |
| 技术与产品 | bp_技术与产品 | `bp_dim_tech.md` |
| 行业与供应链 | bp_行业与供应链 | `bp_dim_industry.md` |

流程：
1. Phase 2a: `_run_bp_dispatch_prepare()` — 构建 brief + manifest → `needs_dispatch`
2. 主 AI 读取 manifest → 用 Task 工具派发 3 个子代理（team 异步模式）
3. Phase 2b: `_run_bp_dispatch_collect()` — 检查输出文件 + 质量评分

### Wave 2（Phase 2.5a-2.5b）

**子代理**：竞争与结论（依赖 Wave 1 输出）

| 维度 | 角色 | 输出 |
|------|------|------|
| 竞争与结论 | bp_竞争与结论 | `bp_dim_competition.md` |

流程：
1. Phase 2.5a: 准备竞争与结论 manifest，注入前 3 维度输出作为上下文
2. 主 AI 派发竞争与结论子代理
3. Phase 2.5b: 检查输出

### 子代理自主闭环规则

子代理执行时必须自主闭环：
1. 发现数据缺口 → 自己补搜（最多 3 轮）
2. 来源不足 → 自己搜更多来源
3. 数据矛盾 → 自己判断，标注矛盾
4. 唯一完成条件 → 输出文件写完

## Phase 3: 统稿 + 交付

### 统稿（Phase 3a-3b）

| 子代理 | 输出 |
|--------|------|
| bp_统稿 | `bp_synthesis.md` |

统稿角色读取 4 个维度输出，按投研逻辑重组为完整研报：
执行摘要 → 技术原理 → 痛点解决 → 方案对比 → 厂商情况 → 市场规模 → 民用拓展 → BP验证 → 风险 → 结论建议

### 交付（Phase 33，2026-06-29 更新）

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

## 质量门禁

| 门禁 | 标准 | 后果 |
|------|------|------|
| 子代理输出长度 | ≥6000 chars → score=5 | score<3 判为 fail |
| URL 数量 | <2 个 → 扣 1 分 | — |
| 章节数量 | <3 个 → 扣 1 分 | — |
| 统稿输出 | >2000 chars | 否则判缺失 |
