# IC Pipeline — 行业研究管线参考

## 管线概览

IC (Industry Coverage) 管线用于行业深度研究，覆盖一二级市场。

### 触发条件

- query 中包含行业研究关键词："行业研究"、"产业分析"、"赛道扫描"、"行业分析"、"行业报告"等
- PipelineOrchestrator.classify_job() 返回 JobType.IC

### 入口

```python
from runtime.entrypoints.run_ic_pipeline_entry import run_ic_job
result = run_ic_job(job_id=..., entity="半导体", query="行业研究", market="cn")
```

## Phase 设计（8 phases）

| Phase | 名称 | 说明 | 执行模式 |
|-------|------|------|---------|
| phase0 | scope_definition | 行业边界定义 + 关键词扩展 + 关键公司名单 | Python 自动 |
| phase0.5 | multi_company_verify | 批量公司工商验证（企查查MCP） | Python 自动 |
| phase1 | industry_presearch | 行业数据预搜索（NeoData + SearchGateway） | 后台子进程 |
| phase1.5 | content_extraction | URL 抓取提取 | Python 自动 |
| phase1.2 | industry_precompute | 行业规模预计算 + 财务基准 | Python 自动 |
| phase4a | dispatch_prepare | launch_next_wave 发射 Wave 1 | Coordinator 接管 |
| phase4b | dispatch_collect | 检查动态 step 输出 + 质量门禁 | Coordinator 接管 |
| phase5 | delivery | 对抗验证 + DOCX + 桌面 + 微信 | 后台子进程 |

## Wave 编排（6 波，Wave 2-4 动态生成）

```
Wave 1（静态·3个，sequential 逐个派发）:
  step_ind_overview   — 行业概览
  step_policy_scan    — 政策法规扫描
  step_value_chain    — 产业链分析 ★ 输出 segments JSON

    ↓ 模板引擎读取 segments，动态生成 Wave 2-4

Wave 2（动态·每环节×3维度，sequential 逐个派发）:
  step_competitive_{seg_id}  — 竞争格局
  step_tech_{seg_id}         — 技术趋势
  step_market_{seg_id}       — 市场规模

Wave 3（动态·每环节×3维度，sequential 逐个派发）:
  step_financial_{seg_id}    — 财务基准
  step_valuation_{seg_id}    — 估值基准
  step_capital_{seg_id}      — 资本动向

Wave 4（动态·每环节1个，sequential 逐个派发）:
  step_seg_synthesis_{seg_id} — 环节小结（两阶段统稿的第一阶段）

Wave 5（静态·3个，sequential 逐个派发）:
  step_cross_chain_compare  — 跨环节对比
  step_investment_thesis     — 投资机会（一二级市场映射）
  step_risk_assessment       — 风险评估

Wave 6（静态·串行）:
  step_master_synthesis      — 行业研报统稿
```

## 动态生成机制

1. `step_value_chain` 子代理输出结构化 JSON，定义产业链 segments
2. `build_dynamic_wave_plan()` 读取 segments，对每个 segment × 6 维度生成动态 step
3. `wave_manifest.json` 持久化完整计划，支持断点续跑

### value_chain 输出格式

```json
{
  "industry": "半导体",
  "segments": [
    {
      "id": "upstream_equipment",
      "name": "上游设备与材料",
      "key_companies": ["ASML", "应用材料"],
      "profit_pool_pct": 35,
      "concentration": "极高（CR3>80%）"
    }
  ]
}
```

### segment id 规则

- lowercase + underscore
- 不含中文
- 例：upstream_equipment, midstream_foundry, downstream_design

### 降级策略

如果 value_chain JSON 解析失败，自动降级为固定三段（上游/中游/下游）。

## 两阶段统稿

为避免 master_synthesis 上下文过载：

1. **Wave 4**: 每个环节先做 `seg_synthesis_{seg_id}`，综合该环节6个维度输出
2. **Wave 6**: `master_synthesis` 只综合 N 个环节小结 + 3 个综合 step

## 关键文件

| 文件 | 说明 |
|------|------|
| `scripts/ic_subagent_launcher.py` | 核心编排：模板引擎 + launch_next_wave + 动态生成 |
| `scripts/ic_precompute.py` | IC 预计算引擎：行业规模 + 板块基准 + 关键公司指标 |
| `scripts/build_ic_industry_report_docx.py` | IC 行业研报 DOCX 生成器 |
| `runtime/profiles/ic_profile.py` | IC Profile，8 个 phase handler |
| `runtime/entrypoints/run_ic_pipeline_entry.py` | 入口文件 |
| `instruction_store_ic/` | 10 个角色指令文件 |

## 核心 API

```python
from ic_subagent_launcher import (
    launch_next_wave,      # 发射当前 wave，返回 team 派发指令
    get_pipeline_status,   # 管线状态快照
    check_step_quality,    # 单 step 质检
    finalize_pipeline,     # Phase 5 全自动（质检→DOCX→桌面→微信）
    wave_manifest_path,    # wave_manifest.json 路径
    step_output_path,      # 单 step 输出文件路径
)
```

## 提交任务

```bash
# ⚠️ 所有 python3 管线命令必须带 cd 前缀（Bash 每次调用是独立 shell）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator submit \
  --entity "行业名称" --market cn --query "行业研究"

# 执行管线（同样必须带 cd）
cd ~/.workbuddy/ir_runtime && python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX
```

返回 `job_id`（如 `TASK-XXXXXXXX-XXX`）。

## 执行伪代码（Coordinator 循环）

### 默认模式（sequential，避免 API 429）

```python
# Phase 0-1.5: 管线自动跑 scope_definition → multi_company_verify → presearch → extract → precompute
python3 -m runtime.orchestrator.pipeline_orchestrator execute --job-id TASK-XXXXX
# → 管线在 phase4_dispatch_prepare 暂停，返回 needs_dispatch=True + task_tool_instructions

# Phase 4: Coordinator 用 team sequential 模式逐个发射 wave
MAX_RETRIES = 2
TOOL_LIMITS = """⚠️ 工具限制：你没有 Glob/Grep 工具。搜索文件用 Bash（find/ls），读文件用 Read，搜索内容用 Bash（grep）。不要调用 Glob 或 Grep。
NeoData 金融数据查询（A/HK 股首选，token 已在 preflight 存好）：
  cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; import json; print(json.dumps(neodata_search('查询语句'), ensure_ascii=False))"
yfinance 估值数据（需精确 PE/PS/市值时使用，⚠️ 必须用 /opt/anaconda3/bin/python3）：
  /opt/anaconda3/bin/python3 -c "import yfinance as yf; t = yf.Ticker('0700.HK'); print(t.info.get('marketCap'), t.info.get('trailingPE'))"
"""

# 1. 创建 team（⚠️ IC 管线用 ic- 前缀，不是 ir-）
team_create(team_name=f"ic-{task_id}")

while True:
    # 调用 Python 获取当前 wave 的 task_tool_instructions
    result = cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.ic_subagent_launcher import launch_next_wave
import json
r = launch_next_wave(task_id='{task_id}', entity='{entity}', query='{query}', market='{market}', sequential=True)
print(json.dumps(r, ensure_ascii=False))
"
    
    if result['all_done']:
        break
    
    # sequential 模式：task_tool_instructions 最多 1 个 step
    # ⚠️ 规则4：prompt 开头必须加 TOOL_LIMITS
    if not result['task_tool_instructions']:
        sleep(60)  # 当前 wave 全阻塞，等待重试
        continue

    for instruction in result['task_tool_instructions']:
        step = instruction['step']
        output_path = instruction['output_path']
        
        Agent(
            name=f'{step}',
            team_name=f'ic-{task_id}',
            mode='bypassPermissions',
            description=step,
            prompt=TOOL_LIMITS + "\n" + instruction['prompt'],
        )
    
    # 规则5：主动轮询输出文件，不等消息
    # bash: while true; do all_ok=true; for f in path1 path2 ...; do test -s "$f" || all_ok=false; done; $all_ok && break; sleep 60; done
    # 超过 20 分钟未完成的 step → 重派（最多 2 次）
    
    # ⚠️ IC 动态 wave 特有：Wave 1 完成后 launch_next_wave 会自动触发
    # build_dynamic_wave_plan()，从 step_value_chain 输出解析 segments，
    # 生成 Wave 2-4 的动态 step，追加到 wave_manifest.json
    # Coordinator 不需要做任何额外操作，直接继续循环即可

# 清理 team
team_delete()

# Phase 5: 全自动交付
result = finalize_pipeline(task_id, entity, market)
```

### 并行模式（parallel，仅无 API 限流时使用，⚠️ 不推荐）

```python
# 创建 team
team_create(team_name=f"ic-{task_id}")

while True:
    # ⚠️ 关键：sequential=True → 每次只返回 1 个 task_instruction
    result = cd ~/.workbuddy/ir_runtime && python3 -c "
from scripts.ic_subagent_launcher import launch_next_wave
import json
r = launch_next_wave(task_id='{task_id}', entity='{entity}', query='{query}', market='{market}', sequential=True)
print(json.dumps(r, ensure_ascii=False))
"
    
    if result.get('all_done'):
        break
    
    if result.get('dispatched_count', 0) == 0:
        if not result.get('has_more'):
            break  # 当前 wave 全部被阻塞或已完成
        continue  # sequential 跳过阻塞 step，继续
    
    # 只有 1 个 instruction，直接派发（在前台执行，等它完成）
    # sequential 模式：单个 step，派发 → 轮询输出 → 下一轮
    for instruction in result['task_tool_instructions']:
        step = instruction['step']
        Agent(
            name=f'{step}',
            team_name=f'ic-{task_id}',
            mode='bypassPermissions',
            description=step,
            prompt=TOOL_LIMITS + "\n" + instruction['prompt'],
        )
    
    # result['has_more'] 由 launch_next_wave 在 sequential 模式下返回
    # has_more=True → 同一 wave 还有 step，继续循环
    # has_more=False → 当前 wave 所有 step 处理完，下一次调用自动推进到下一 wave

team_delete()
result = finalize_pipeline(task_id, entity, market)
```

**sequential 模式关键差异：**
- `has_more=True` 表示同一 wave 内还有待处理的 step，Coordinator 应继续调用
- wave 边界不变 —— 当前 wave 所有 step 完成后，`launch_next_wave` 自动推进到下一 wave
- 如果 step 被依赖阻塞（`deps_ready=False`），sequential 模式自动跳过找下一个可派发的
- **如果当前 wave 所有 step 均被阻塞**：task_tool_instructions 为空，Coordinator 应 sleep 后重试

## IC 子代理派发规则

- **必须用 team 模式**：`team_create(team_name=f"ic-{task_id}")` → `Agent(name=..., team_name=..., mode='bypassPermissions')` → 轮询输出文件
- **team_name 用 `ic-{task_id}`**（不是 `ir-`）
- **禁止用同步 `task()`**（无 name 参数）——会返回 code=10003 挂掉
- `mode="bypassPermissions"` 确保子代理可写文件
- **⚠️ 子代理 prompt 必须加工具限制声明**（规则4）：Glob/Grep 不存在，用 Bash+Read 替代
- `launch_next_wave()` 返回的 `task_tool_instructions` 包含完整的 prompt（含 brief_path + output_path）
- **派发后主动轮询**（规则5）：每 60 秒用 Bash `test -s` 检查输出文件，不依赖子代理消息
- **shutdown 后清理 team config**（规则6）：从 config.json members 移除已退出成员
- 输出文件超时未出现 → 重派（最多重试 2 次）
- 重试仍然失败 → 记录失败原因，跳过该 step，继续下一 wave
- step_master_synthesis 失败 → 用已有 step 输出拼接兜底

## IC 交付规则

- 与 IR 交付规则完全一致（finalize_pipeline → 质检 → DOCX → 桌面 → 微信通知）
- DOCX 失败 → 用 markdown 兜底
- **研报必须复制到桌面**
- **微信通知必须尝试发送**
- 交付完成后，在聊天窗口告知用户文件完整路径

## IC Wave 6 step_master_synthesis 统稿硬约束

- 读取所有 seg_synthesis_{seg_id} + cross_chain_compare + investment_thesis + risk_assessment，汇总为完整行业深度研报
- **统稿保留硬约束**（与 IR 一致）：
  - **核心对比表必须原文保留**：行业竞争格局对比表、产品参数对比表——不得删除或压缩
  - **市占率/份额/渗透率数据必须完整保留**
  - **去重只做跨step，不做step内压缩**
  - **来源合并不得丢来源**：所有 step 的来源必须合并到"来源附录"
- 总字数不低于原始各 step 内容总量的 70%（禁止过度压缩）

## 错误恢复（断点续跑）

如果管线中途因 context window 等原因断裂：
1. `get_pipeline_status(task_id)` 看哪些 step 已完成
2. `launch_next_wave()` 自动从断点继续（已完成的 step 自动跳过）
3. 不需要从头开始
