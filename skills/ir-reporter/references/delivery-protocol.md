# 交付协议

**唯一来源**：此文件是交付链路规则的 single source of truth。

## 交付文件选择（硬规则）

- BP 报告：交付 `{JOB_ID}_bp_dd_report.docx`（统稿版），**不发**中文命名的 copy
- IR 研报：交付 `{JOB_ID}_broker_report.docx`

## 交付动作（必须全部执行）

1. DOCX 生成到 `jobs/{JOB_ID}/delivery/` 目录
2. 复制到用户桌面（macOS `~/Desktop/`）
3. 在聊天窗口明确告知用户文件完整路径，方便用户自行获取

**注意**：`deliver_attachments` 工具在用户客户端无法显示附件，**禁止使用**。

## 交付链路说明

- IR 管线：DOCX 生成 → 桌面复制 → 聊天告知路径
- BP 管线：DOCX 生成 → 桌面复制 → `register_delivery_media.py` → WorkBuddy media-index + message-queue

## 交付清洗（硬规则）

- sanitize_text() 清洗所有内部信息
- 标题页不暴露 task ID
- Markdown 表格 → Word 原生表格
- 包含免责声明页

## 产物归档

所有产物自动同步到 workspace：
- DOCX → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/`
- 验证报告 → `{IR_RUNTIME}/jobs/{JOB_ID}/verification/`
- 审计日志 → `{IR_RUNTIME}/jobs/{JOB_ID}/delivery/`
- artifacts.json 记录所有产物路径
