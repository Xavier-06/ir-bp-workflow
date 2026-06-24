# IR 管线执行协议 (WorkBuddy Team sequential 模式)

> **铁律：全自动推进，Zero Human Intervention。用户不需要发"继续"。**

## 触发条件

用户提供 **公司名/股票代码 + "研究/研报/IR"** → 读取本协议并执行。

## 总览

IR 管线 = 5 Wave × 10 Step + Phase 5 交付。主 AI 作为编排者（orchestrator），循环推进。

```
Wave 1: step1_data                                         (串行)
Wave 2: step2_industry, step3_biz, step4_finance, step5_mgmt, step_macro  (串行派发：逐个)
Wave 3: step6b_valuation                                   (串行)
Wave 4: step6_insight, step7_risk                          (串行派发：逐个)
Wave 5: step8_master                                       (串行)
Phase 5: 质量门禁 → DOCX → 桌面 → 通知
```

> ⚠️ 同 wave 内步骤逐个派发（sequential mode），避免并行 Task 子代理触发 API 429 限流。

## 执行流程（伪代码）

```python
# 1. 初始化任务
task_id = create_task_id()
write_task_package(task_id, entity, market, query)

# 2. 创建 team
team_create(team_name=f"ir-{task_id}")

# 3. 循环发射 wave（sequential 模式：每次只派发一个 step）
while True:
    result = launch_next_wave(task_id, entity, query, market, sequential=True)
    
    if result['all_done']:
        break
    
    # ⚠️ sequential 模式下 task_tool_instructions 最多包含 1 个 step
    for instruction in result['task_tool_instructions']:
        task(
            subagent_name='code-explorer',
            name=instruction['step'],
            team_name=f'ir-{task_id}',
            mode='bypassPermissions',
            description=instruction['step'],
            prompt=instruction['prompt'],
        )
    
    # 轮询等待本 step 输出文件
    # execute_command: sleep 30 && test -s {output_path}
    # 最多等 15 分钟，超时则重派
    # → 下一个 step 在下一轮循环自动派发（has_more 标志驱动）

# 4. 清理 team
team_delete()

# 5. 全部 wave 完成 → finalize
result = finalize_pipeline(task_id, entity, market)
# → DOCX 生成 + 桌面复制
```

## 关键 API（ir_subagent_launcher_wb.py）

| 函数 | 用途 |
|------|------|
| `launch_next_wave(task_id, entity, query, market)` | 发射当前 wave，返回 team 派发指令 |
| `get_pipeline_status(task_id)` | 查看管线状态快照 |
| `get_current_wave_index(task_id)` | 当前该发射哪个 wave |
| `finalize_pipeline(task_id, entity, market)` | Phase 5 统稿交付 |
| `check_step_quality(task_id, step)` | 单 step 质检 |

## 铁律

### 1. 子代理派发方式
- **必须用 team 模式（sequential 逐个派发）**：`team_create()` → `Agent(name=..., team_name=..., mode='bypassPermissions')` → 轮询输出文件
- **禁止用同步 `task()`**（无 name 参数）——会返回 code=10003 挂掉
- `subagent_name` 固定为 `code-explorer`
- 派发后通过 `execute_command` 轮询输出文件是否存在且 >100 字节

### 2. Wave 间不停，Wave 内逐个派发
- 调用 `launch_next_wave(sequential=True)` 每次只返回一个 step
- 派发该 step 子代理 → 等待完成 → 再次调用 `launch_next_wave()`
- `has_more` 标志指示当前 wave 是否还有待派发 step
- Wave 间**立即**推进，**禁止**等待用户确认

### 3. 子代理失败处理
- 输出文件超时未出现 → **重派一次**（重新 task with name）
- 重试仍然失败 → 记录失败原因，跳过该 step，继续下一 wave
- step8_master 失败 → 用已有 step 输出人工拼接兜底

### 4. 交付必须完成
- Phase 5 `finalize_pipeline()` 必须执行
- DOCX 失败 → 用 markdown 兜底
- **研报必须复制到桌面**

### 5. 输出格式
- 最终交付物优先 DOCX，DOCX 生成失败才用 markdown
- 中间 step 输出始终为 markdown

## 错误恢复

如果管线中途因 context window 等原因断裂：
1. 调用 `get_pipeline_status(task_id)` 看哪些 step 已完成
2. 调用 `launch_next_wave()` 自动从断点继续
3. 不需要从头开始

## 示例：完整一次执行

```
team_create("ir-TASK-XXX")
🌊 Wave 1/5 → step1_data → task(name=step1_data) → ✅
🌊 Wave 2/5 → step2-5+macro 逐个派发 → ✅✅✅✅✅
🌊 Wave 3/5 → step6b_valuation → ✅
🌊 Wave 4/5 → step6-7 逐个派发 → ✅✅
🌊 Wave 5/5 → step8_master → ✅
team_delete()
📊 finalize → 质量门禁 → DOCX → 桌面 → ✅ Done
```

---
*最后更新: 2026-06-02 — IR 管线 Team sequential 模式执行协议 v3*
