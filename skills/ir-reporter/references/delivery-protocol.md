# 交付协议

**唯一来源**：此文件是交付链路规则的 single source of truth。

## 交付文件选择（硬规则）

- BP 报告：交付 `{JOB_ID}_bp_dd_report.docx`（统稿版），**不发**中文命名的 copy
- BP 维度报告：`delivery/维度分析/{维度标题}.docx`（2026-06-26 新增）
- IR 研报：交付 `{JOB_ID}_broker_report.docx`
- IC 行业研报：交付 `{JOB_ID}_ic_industry_report.docx`

## 交付动作（必须全部执行）

1. 在聊天窗口明确告知用户文件完整路径，方便用户自行获取
2. 使用 `present_files` 工具展示报告

**注意**：`deliver_attachments` 工具在用户客户端无法显示附件，**禁止使用**。

## 交付清洗（硬规则）

- sanitize_text() 清洗所有内部信息
- 标题页不暴露 task ID
- Markdown 表格 → Word 原生表格
- 包含免责声明页
- DOCX 来源渲染：保留所有有名称的来源（不再强制要求 URL）

## 产物归档

所有产物自动同步到 workspace：
- DOCX → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/`
- 维度 DOCX → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/维度分析/`
- 验证报告 → `{IR_RUNTIME}/jobs/{JOB_ID}/verification/`
- 审计日志 → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/`
- artifacts.json 记录所有产物路径
