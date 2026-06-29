# 交付协议

**唯一来源**：此文件是交付链路规则的 single source of truth。

## 交付文件选择（硬规则）

- BP 统稿报告：`delivery/{JOB_ID}_bp_dd_report.docx`
- BP 维度报告（8 个）：`delivery/{维度标题}.docx`（2026-06-29 改为平铺，不再使用 `维度分析/` 子目录）
- BP 投资备忘录 MD：`delivery/{entity}BP投资备忘录.md`
- BP 审计底稿 MD：`delivery/{entity}BP审计底稿.md`
- IR 研报：交付 `{JOB_ID}_broker_report.docx`
- IC 行业研报：交付 `{JOB_ID}_ic_industry_report.docx`

### BP 交付完整清单（2026-06-29 强制）

**所有文件平铺在 `delivery/` 根目录**，不使用子目录。coordinator **必须** 从 `artifacts.json` 读取产物清单，一次性 `present_files` 交付以下全部文件：

| # | 文件 | artifacts key | 说明 |
|---|------|--------------|------|
| 1 | 统稿 DOCX | `bp_dd_report` | 完整尽调报告 |
| 2-9 | 8 个维度 DOCX | `bp_dim_docx_0` ~ `bp_dim_docx_7` | 各维度独立报告 |
| 10 | 投资备忘录 MD | — | synthesis 统稿原文 |
| 11 | 审计底稿 MD | — | assembler 结构化骨架 |

**禁止只交付统稿 DOCX 而遗漏维度 DOCX**。8 个维度 DOCX 和统稿 DOCX 必须在同一次 `present_files` 调用中交付。

## 交付动作（必须全部执行）

1. 读取 `jobs/{JOB_ID}/state/artifacts.json`，获取所有产物路径
2. 在聊天窗口明确告知用户文件完整路径，方便用户自行获取
3. 使用 `present_files` 工具一次性展示全部报告（统稿 + 维度 + 附件）

**注意**：`deliver_attachments` 工具在用户客户端无法显示附件，**禁止使用**。

## 交付清洗（硬规则）

- sanitize_text() 清洗所有内部信息
- 标题页不暴露 task ID
- Markdown 表格 → Word 原生表格
- 包含免责声明页
- DOCX 来源渲染：保留所有有名称的来源（不再强制要求 URL）

## 产物归档

所有产物平铺在 `delivery/` 根目录（2026-06-29 起不再使用子目录）：
- 统稿 DOCX → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/{JOB_ID}_bp_dd_report.docx`
- 维度 DOCX → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/{维度标题}.docx`（8 个，平铺）
- 投资备忘录 MD → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/{entity}BP投资备忘录.md`
- 审计底稿 MD → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/{entity}BP审计底稿.md`
- 验证报告 → `{IR_RUNTIME}/jobs/{JOB_ID}/verification/`
- 审计日志 → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/bp_delivery_audit.json`
- artifacts.json 记录所有产物路径
