#!/usr/bin/env python3
"""
IC (Industry Coverage) Subagent Launcher — WorkBuddy 版本 v1

行业研究管线核心编排引擎。核心特性：
1. 动态 Wave 生成 — value_chain step 完成后，根据产业链环节数动态展开后续 wave
2. 两阶段统稿 — seg_synthesis_{seg_id} 先做环节小结，master_synthesis 再综合
3. 断点续跑 — wave_manifest.json 持久化，支持从中断处恢复
4. 模板引擎 — 6个维度模板 × N个环节 = 动态 step

Wave 编排：
  Wave 0（静态·1个）: executive_hypothesis（投研假说先行）
  Wave 1（静态·3个并行）: ind_overview / policy_scan / value_chain
  Wave 2（动态·每环节×3维度）: competitive_{seg} / tech_{seg} / market_{seg}
  Wave 3（动态·每环节×3维度）: financial_{seg} / valuation_{seg} / capital_{seg}
  Wave 4（动态·每环节1个）: seg_synthesis_{seg}
  Wave 5（静态·3个并行）: cross_chain_compare / catalyst_analysis / consensus_challenge
  Wave 6（静态·3个并行）: investment_thesis / risk_assessment / scenario_sensitivity
  Wave 7（静态·串行）: master_synthesis
  Wave 8（静态·串行）: investment_playbook（投资手册交付）

2026-05-29 v1: 初始版本
2026-06-02 v2: P0升级（+Wave 0投研假说、+Wave 7投资手册、+分析师思维注入）
2026-06-02 v3: P1P2升级（+Wave 5催化剂/共识挑战、+Wave 6场景敏感性、+P2深度增强）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / 'data' / 'tasks'
INSTRUCTION_STORE = ROOT / 'instruction_store_ic'

# ── 质量线 ──
STEP_QUALITY_THRESHOLD = 3

# ── 维度模板 ──
SEGMENT_DIMS_W2 = ["competitive", "tech", "market"]
SEGMENT_DIMS_W3 = ["financial", "valuation", "capital"]

# ── 静态 Wave 定义 ──
STATIC_WAVE_0 = ["step_executive_hypothesis"]
STATIC_WAVE_1 = ["step_ind_overview", "step_policy_scan", "step_value_chain"]
STATIC_WAVE_5 = ["step_cross_chain_compare", "step_catalyst_analysis", "step_consensus_challenge"]
STATIC_WAVE_6 = ["step_investment_thesis", "step_risk_assessment", "step_scenario_sensitivity"]
STATIC_WAVE_7 = ["step_master_synthesis"]
STATIC_WAVE_8 = ["step_investment_playbook"]

# ── ConnectorIds 授权 ──
# 按 step 前缀授予 connectorIds。tyc-mcp（天眼查）+ westock-mcp（腾讯自选股）。
# tdx（通达信）/ qcc（企查查）当前环境不可用。
IC_ROLE_CONNECTOR_IDS: dict[str, list[str]] = {
    "step_ind_overview": ["tyc-mcp", "westock-mcp"],
    "step_policy_scan": ["tyc-mcp", "westock-mcp"],
    "step_value_chain": ["tyc-mcp", "westock-mcp"],
    "step_competitive": ["tyc-mcp", "westock-mcp"],
    "step_tech": ["tyc-mcp", "westock-mcp"],
    "step_market": ["tyc-mcp", "westock-mcp"],
    "step_financial": ["westock-mcp"],
    "step_valuation": ["westock-mcp"],
    "step_capital": ["tyc-mcp", "westock-mcp"],
    "step_executive_hypothesis": ["tyc-mcp", "westock-mcp"],
    "step_cross_chain_compare": ["tyc-mcp", "westock-mcp"],
    "step_catalyst_analysis": ["tyc-mcp", "westock-mcp"],
    "step_consensus_challenge": ["tyc-mcp", "westock-mcp"],
    "step_investment_thesis": ["tyc-mcp", "westock-mcp"],
    "step_risk_assessment": ["tyc-mcp", "westock-mcp"],
    "step_scenario_sensitivity": ["tyc-mcp", "westock-mcp"],
    "step_master_synthesis": ["tyc-mcp", "westock-mcp"],
    "step_investment_playbook": ["tyc-mcp", "westock-mcp"],
}

def _get_step_connector_ids(step: str) -> list[str]:
    """Get connectorIds for a step, matching by prefix."""
    # 动态 step（如 step_competitive_upstream）匹配前缀
    for prefix, ids in IC_ROLE_CONNECTOR_IDS.items():
        if step.startswith(prefix):
            return ids
    return ["tyc-mcp", "westock-mcp"]  # default

# ── 静态 step 的 STEP_DEPS（Wave 1 无依赖） ──
STATIC_DEPS = {
    "step_executive_hypothesis": [],
    "step_ind_overview": [],
    "step_policy_scan": [],
    "step_value_chain": [],
    "step_cross_chain_compare": [],       # 动态填充
    "step_catalyst_analysis": [],         # 动态填充
    "step_consensus_challenge": [],       # 动态填充
    "step_investment_thesis": [],         # 动态填充
    "step_risk_assessment": [],           # 动态填充
    "step_scenario_sensitivity": [],      # 动态填充
    "step_master_synthesis": [],          # 动态填充
    "step_investment_playbook": [],       # 动态填充
}

# ── 静态 step 超时 ──
STEP_TIMEOUTS = {
    "step_executive_hypothesis": 600,
    "step_ind_overview": 900,
    "step_policy_scan": 900,
    "step_value_chain": 1200,
    "step_catalyst_analysis": 900,
    "step_consensus_challenge": 900,
    "step_scenario_sensitivity": 900,
    "step_investment_playbook": 1200,
    # 动态 step 默认 900
}
DEFAULT_STEP_TIMEOUT = 900

# 维度 → W2 指令 key
DIM_W2_ROLE = {
    "competitive": "行业_环节分析_W2",
    "tech": "行业_环节分析_W2",
    "market": "行业_环节分析_W2",
}
# 维度 → W3 指令 key
DIM_W3_ROLE = {
    "financial": "行业_环节分析_W3",
    "valuation": "行业_环节分析_W3",
    "capital": "行业_环节分析_W3",
}


# ═══════════════════════════════════════════════════════
# 通知
# ═══════════════════════════════════════════════════════

def notify_wx(text: str) -> bool:
    """Notification stub — longshao_notify removed for open-source release."""
    return False


# ═══════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════

def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def step_output_path(task_id: str, step: str) -> Path:
    return TASKS_DIR / f'{task_id}-{step}.md'


def step_spawn_receipt_path(task_id: str, step: str) -> Path:
    return TASKS_DIR / f'{task_id}-spawn-receipt-{step}.json'


def step_manifest_path(task_id: str, step: str) -> Path:
    return TASKS_DIR / f'{task_id}-manifest-{step}.json'


def pipeline_manifest_path(task_id: str) -> Path:
    return TASKS_DIR / f'{task_id}-pipeline-manifest.json'


def wave_manifest_path(task_id: str) -> Path:
    return TASKS_DIR / f'{task_id}-wave-manifest.json'


# ═══════════════════════════════════════════════════════
# 指令加载
# ═══════════════════════════════════════════════════════

def load_instruction(role_key: str) -> str:
    """加载角色指令（instruction_store_ic）"""
    role_file = INSTRUCTION_STORE / f'{role_key}.md'
    if role_file.exists():
        return role_file.read_text(encoding='utf-8')
    return f'Role instructions for {role_key} not found.'


def _get_step_role_key(step_name: str) -> str:
    """根据 step 名推断指令 key（优先读 index.json pipeline_bindings.ic）"""
    index_path = INSTRUCTION_STORE / 'index.json'
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding='utf-8'))
        static_map = index.get('pipeline_bindings', {}).get('ic', {})
        if step_name in static_map:
            return static_map[step_name]

    # 动态 step: 解析维度
    parts = step_name.replace("step_", "").split("_", 1)
    if len(parts) == 2:
        dim, seg_id = parts[0], parts[1]
        if dim in SEGMENT_DIMS_W2:
            return "行业_环节分析_W2"
        if dim in SEGMENT_DIMS_W3:
            return "行业_环节分析_W3"
        if dim == "seg_synthesis":
            return "行业_环节小结"

    return step_name


# ═══════════════════════════════════════════════════════
# 动态 Wave 生成（核心）
# ═══════════════════════════════════════════════════════

def parse_segments_from_value_chain(task_id: str) -> list[dict]:
    """从 value_chain step 的输出中解析 segments JSON。

    寻找 ```json block，提取 segments 数组。
    如果解析失败，降级为固定三段（上游/中游/下游）。
    """
    vc_path = step_output_path(task_id, "step_value_chain")
    if not vc_path.exists():
        print("  ⚠️ value_chain 输出不存在，降级为固定三段")
        return _fallback_segments()

    text = vc_path.read_text(encoding='utf-8')

    # 提取 json block
    json_blocks = re.findall(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block)
            segments = data.get("segments", [])
            if segments and len(segments) >= 2:
                # 规范化 segment id：确保是合法的 ASCII 标识符
                for seg in segments:
                    raw_id = seg.get("id", "")
                    normalized = _normalize_seg_id(raw_id)
                    if not normalized:
                        idx = segments.index(seg)
                        normalized = f"segment_{idx + 1}"
                    seg["id"] = normalized
                print(f"  ✅ 解析到 {len(segments)} 个产业链环节: {[s['name'] for s in segments]}")
                return segments
        except json.JSONDecodeError:
            continue

    # 尝试整体解析
    try:
        data = json.loads(text)
        segments = data.get("segments", [])
        if segments and len(segments) >= 2:
            for seg in segments:
                normalized = _normalize_seg_id(seg.get("id", ""))
                if not normalized:
                    idx = segments.index(seg)
                    normalized = f"segment_{idx + 1}"
                seg["id"] = normalized
            return segments
    except json.JSONDecodeError:
        pass

    print("  ⚠️ value_chain JSON 解析失败，降级为固定三段")
    return _fallback_segments()


def _normalize_seg_id(raw_id: str) -> str:
    """规范化 segment id: lowercase + underscore + 无中文（纯ASCII）"""
    if not raw_id:
        return ""
    # 只保留 ASCII 字母、数字、下划线、连字符，其余替换为下划线
    normalized = re.sub(r'[^a-zA-Z0-9_\-]', '_', raw_id).lower().strip('_')
    # 去除连续下划线
    normalized = re.sub(r'_+', '_', normalized)
    # 去除前后下划线
    normalized = normalized.strip('_')
    return normalized


def _fallback_segments() -> list[dict]:
    """降级方案：固定三段"""
    return [
        {"id": "upstream", "name": "上游", "description": "上游原材料与核心部件",
         "key_companies": [], "profit_pool_pct": 30, "concentration": "",
         "listed_tickers": [], "primary_market_hotspots": []},
        {"id": "midstream", "name": "中游", "description": "中游制造与集成",
         "key_companies": [], "profit_pool_pct": 40, "concentration": "",
         "listed_tickers": [], "primary_market_hotspots": []},
        {"id": "downstream", "name": "下游", "description": "下游应用与终端",
         "key_companies": [], "profit_pool_pct": 30, "concentration": "",
         "listed_tickers": [], "primary_market_hotspots": []},
    ]


def build_dynamic_wave_plan(task_id: str) -> dict:
    """Wave 1 完成后调用。读取 value_chain 输出，生成完整动态计划。

    返回完整 wave_manifest。
    """
    segments = parse_segments_from_value_chain(task_id)
    all_seg_ids = [s["id"] for s in segments]

    # Wave 2: 竞争/技术/市场 × N segments
    dynamic_wave2 = []
    for seg in segments:
        for dim in SEGMENT_DIMS_W2:
            dynamic_wave2.append(f"step_{dim}_{seg['id']}")

    # Wave 3: 财务/估值/资本 × N segments
    dynamic_wave3 = []
    for seg in segments:
        for dim in SEGMENT_DIMS_W3:
            dynamic_wave3.append(f"step_{dim}_{seg['id']}")

    # Wave 4: 环节小结 × N segments（两阶段统稿的第一阶段）
    dynamic_wave4 = []
    for seg in segments:
        dynamic_wave4.append(f"step_seg_synthesis_{seg['id']}")

    # 完整 waves
    waves = [
        STATIC_WAVE_0,   # Wave 0: 投研假说
        STATIC_WAVE_1,   # Wave 1: 静态
        dynamic_wave2,   # Wave 2: 动态竞争/技术/市场
        dynamic_wave3,   # Wave 3: 动态财务/估值/资本
        dynamic_wave4,   # Wave 4: 环节小结
        STATIC_WAVE_5,   # Wave 5: 跨环节对比+催化剂+共识挑战
        STATIC_WAVE_6,   # Wave 6: 投资论点+风险评估+场景敏感性
        STATIC_WAVE_7,   # Wave 7: 统稿
        STATIC_WAVE_8,   # Wave 8: 投资手册
    ]

    # ── 构建 STEP_DEPS ──
    step_deps = {}

    # Wave 0 deps: 无依赖
    step_deps["step_executive_hypothesis"] = []

    # 静态 deps
    step_deps["step_ind_overview"] = []
    step_deps["step_policy_scan"] = []
    step_deps["step_value_chain"] = []

    # Wave 2 deps: 依赖 value_chain
    for seg in segments:
        for dim in SEGMENT_DIMS_W2:
            step_deps[f"step_{dim}_{seg['id']}"] = ["step_value_chain"]

    # Wave 3 deps: 依赖同环节的 Wave 2 输出
    for seg in segments:
        sid = seg["id"]
        step_deps[f"step_financial_{sid}"] = [f"step_competitive_{sid}", f"step_market_{sid}"]
        step_deps[f"step_valuation_{sid}"] = [f"step_financial_{sid}"]
        step_deps[f"step_capital_{sid}"] = [f"step_competitive_{sid}", f"step_tech_{sid}"]

    # Wave 4 deps: 环节小结依赖同环节所有维度
    for seg in segments:
        sid = seg["id"]
        step_deps[f"step_seg_synthesis_{sid}"] = [
            f"step_competitive_{sid}", f"step_tech_{sid}", f"step_market_{sid}",
            f"step_financial_{sid}", f"step_valuation_{sid}", f"step_capital_{sid}",
        ]

    # Wave 5 deps: 跨环节对比+催化剂+共识挑战，依赖所有环节小结
    step_deps["step_cross_chain_compare"] = all_seg_synthesis
    step_deps["step_catalyst_analysis"] = all_seg_synthesis
    step_deps["step_consensus_challenge"] = all_seg_synthesis + ["step_cross_chain_compare"]

    # Wave 6 deps: 投资论点+风险评估+场景敏感性
    step_deps["step_investment_thesis"] = (all_seg_synthesis + all_valuation + all_capital
                                           + all_financial + ["step_ind_overview", "step_policy_scan"]
                                           + ["step_consensus_challenge"])
    step_deps["step_risk_assessment"] = (all_competitive + all_seg_synthesis
                                         + ["step_ind_overview"] + all_financial
                                         + all_capital + ["step_policy_scan"])
    step_deps["step_scenario_sensitivity"] = (all_financial + all_valuation
                                              + ["step_cross_chain_compare"])

    # Wave 7 deps: 统稿依赖 Wave 5 + Wave 6 全部
    step_deps["step_master_synthesis"] = [
        "step_cross_chain_compare", "step_catalyst_analysis", "step_consensus_challenge",
        "step_investment_thesis", "step_risk_assessment", "step_scenario_sensitivity"
    ]

    # Wave 8 deps: 投资手册依赖统稿
    step_deps["step_investment_playbook"] = [
        "step_master_synthesis"
    ]

    manifest = {
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "dynamic_generated": True,
        "current_wave_index": 2,  # Wave 0 + Wave 1 已完成
        "segments": segments,
        "waves": waves,
        "step_deps": step_deps,
        "completed_steps": list(STATIC_WAVE_0) + list(STATIC_WAVE_1),  # Wave 0 + Wave 1 已完成
        "total_waves": len(waves),
    }

    save_json(wave_manifest_path(task_id), manifest)
    print(f"  ✅ 动态 Wave 计划已生成: {len(segments)} 个环节, {sum(len(w) for w in waves)} 个 step, {len(waves)} 个 Wave")

    return manifest


# ═══════════════════════════════════════════════════════
# 依赖检查
# ═══════════════════════════════════════════════════════

def deps_ready(task_id: str, step: str, step_deps: dict) -> tuple[bool, list[str]]:
    """检查依赖步骤的输出文件是否已存在且完整（>100 bytes）"""
    missing = []
    for dep in step_deps.get(step, []):
        p = step_output_path(task_id, dep)
        if not p.exists() or p.stat().st_size < 100:
            missing.append(dep)
    return len(missing) == 0, missing


# ═══════════════════════════════════════════════════════
# Step Brief 构建
# ═══════════════════════════════════════════════════════

def build_step_brief(task_id: str, step: str, entity: str = '', query: str = '',
                     segments: list[dict] | None = None) -> str:
    """构建子代理任务 brief"""
    role_key = _get_step_role_key(step)
    instruction = load_instruction(role_key)

    output_path = step_output_path(task_id, step)

    # 解析当前 step 的环节信息
    seg_name, seg_id, seg_info = _parse_step_segment(step, segments or [])
    dimension = _parse_step_dimension(step)

    # 如果是模板指令，替换占位符
    if "{seg_name}" in instruction or "{dimension}" in instruction:
        instruction = instruction.replace("{seg_name}", seg_name or "未知环节")
        instruction = instruction.replace("{dimension}", _DIMENSION_CN.get(dimension, dimension))

    brief_lines = [
        f'# Step Brief: {role_key} ({step})',
        f'',
        f'Task: {task_id}',
        f'Entity: {entity}',
        f'Query: {query}',
        f'Industry: {entity}',
        f'Segment: {seg_name or "N/A"}',
        f'Dimension: {dimension or "N/A"}',
        f'',
        f'## ⚠️ CRITICAL: 输出文件路径（必须写入此路径）',
        f'',
        f'**你必须将最终分析报告写入以下文件：**',
        f'',
        f'`{output_path}`',
        f'',
        f'**禁止写入其他路径。**',
        f'**唯一完成条件：上述文件写入成功。**',
        f'',
        f'## Role Instruction',
        f'',
        instruction,
        f'',
        f'## ⚠️ 自主闭环规则（最高优先级）',
        f'',
        f'你在执行过程中必须自主闭环，不要返回主控等待指示：',
        f'1. **发现数据缺口** → 自己补搜（工具优先级见下方），继续推进',
        f'2. **来源不足** → 自己搜更多来源，补充到输出中',
        f'3. **数据矛盾** → 自己判断哪个更可靠，标注矛盾来源',
        f'4. **前序 step 输出有 gap** → 自己补充搜索填补',
        f'5. **唯一完成条件** → 将完整报告写入上方指定的输出文件路径',
        f'',
        f'### 补搜工具优先级',
        f'1. `NeoData 金融搜索` — A/HK 股首选',
        f'2. `yfinance (Python)` — 估值指标、美股数据',
        f'3. `web_search` — 通用搜索',
        f'4. DuckDuckGo / SearXNG — 备用搜索',
        f'',
        f'### 补搜纪律',
        f'- 最多补搜 3 轮',
        f'- 补搜结果必须标注来源 URL',
        f'- 仍搜不到的标注"经 X 次搜索未找到独立来源"',
        f'',
        f'## Pre-search Results（输入参考，只读）',
        f'',
    ]

    # Pre-search
    search_path = TASKS_DIR / f'{task_id}-search-{step}.md'
    if search_path.exists():
        brief_lines.append(search_path.read_text(encoding='utf-8'))
    else:
        brief_lines.append('_No pre-search results._')

    # Prior steps
    manifest = load_json(wave_manifest_path(task_id))
    step_deps = manifest.get("step_deps", {}) if manifest else STATIC_DEPS

    for dep in step_deps.get(step, []):
        dep_path = step_output_path(task_id, dep)
        if dep_path.exists():
            brief_lines.append(f'')
            brief_lines.append(f'## Prior Step Output: {dep}')
            brief_lines.append(f'')
            brief_lines.append(f'完整输出文件路径：`{dep_path}`')
            brief_lines.append(f'请使用 Read 工具读取该文件的完整内容（不要依赖摘要，必须读原文）。')

    return '\n'.join(brief_lines)


# ── 辅助：解析 step 的环节和维度 ──

_DIMENSION_CN = {
    "competitive": "竞争格局",
    "tech": "技术趋势",
    "market": "市场规模",
    "financial": "财务基准",
    "valuation": "估值基准",
    "capital": "资本动向",
}


def _parse_step_segment(step: str, segments: list[dict]) -> tuple[str | None, str | None, dict | None]:
    """解析 step 属于哪个环节。返回 (seg_name, seg_id, seg_info)"""
    clean = step.replace("step_", "")
    for seg in segments:
        sid = seg["id"]
        # 匹配 dim_seg_id 或 seg_synthesis_seg_id
        if clean.endswith(f"_{sid}") or clean == f"seg_synthesis_{sid}":
            return seg["name"], sid, seg
    return None, None, None


def _parse_step_dimension(step: str) -> str | None:
    """解析 step 的分析维度"""
    clean = step.replace("step_", "")
    for dim in SEGMENT_DIMS_W2 + SEGMENT_DIMS_W3:
        if clean.startswith(f"{dim}_"):
            return dim
    if clean.startswith("seg_synthesis_"):
        return "seg_synthesis"
    return None


# ═══════════════════════════════════════════════════════
# 子代理发射
# ═══════════════════════════════════════════════════════

def launch_step(task_id: str, step: str, entity: str = '', query: str = '',
                timeout: int = 900, dry_run: bool = False, market: str = 'cn',
                segments: list[dict] | None = None) -> dict:
    """启动单个子代理 step"""
    output_path = step_output_path(task_id, step)
    receipt_path = step_spawn_receipt_path(task_id, step)
    manifest = step_manifest_path(task_id, step)

    # 检查依赖
    wm = load_json(wave_manifest_path(task_id))
    step_deps = wm.get("step_deps", {}) if wm else STATIC_DEPS
    ready, missing = deps_ready(task_id, step, step_deps)
    if not ready:
        return {'step': step, 'status': 'blocked', 'reason': f'Dependencies not ready: {missing}'}

    # 构建 brief
    brief = build_step_brief(task_id, step, entity, query, segments)
    brief_path = TASKS_DIR / f'{task_id}-brief-{step}.md'
    brief_path.write_text(brief, encoding='utf-8')

    if dry_run:
        return {'step': step, 'status': 'dry_run', 'brief_path': str(brief_path), 'output_path': str(output_path)}

    # 清理旧输出
    for p in (receipt_path, manifest):
        if p.exists():
            p.unlink()

    # 写入 manifest
    role_key = _get_step_role_key(step)
    system_prompt = build_step_prompt(step, entity, market, segments)

    manifest_data = {
        'task_id': task_id,
        'step': step,
        'role': role_key,
        'entity': entity,
        'query': query,
        'market': market,
        'system_prompt': system_prompt,
        'connectorIds': _get_step_connector_ids(step),
        'brief_path': str(brief_path),
        'output_path': str(output_path),
        'timeout': timeout,
        'thinking': 'high',
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'status': 'pending',
    }
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')

    # 写入 spawn receipt
    label = f'{task_id}-{step}'
    receipt = {
        'task_id': task_id,
        'step': step,
        'hook': step,
        'label': label,
        'status': 'dispatched',
        'runId': f'wb-task-{int(time.time())}',
        'childSessionKey': f'wb-{task_id}-{step}',
        'runtime': 'workbuddy-task',
        'thinking': 'high',
        'manifest_path': str(manifest),
        'output_path': str(output_path),
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"  📋 已派发 {role_key} ({step}) → manifest: {manifest.name}")

    return {
        'step': step,
        'status': 'dispatched',
        'label': label,
        'childSessionKey': receipt['childSessionKey'],
        'runId': receipt['runId'],
        'thinking': 'high',
        'brief_path': str(brief_path),
        'output_path': str(output_path),
        'receipt_path': str(receipt_path),
        'manifest_path': str(manifest),
    }


def build_step_prompt(step: str, entity: str, market: str = 'cn',
                      segments: list[dict] | None = None) -> str:
    """构建子代理系统级提示词"""
    role_key = _get_step_role_key(step)
    seg_name, seg_id, seg_info = _parse_step_segment(step, segments or [])
    dimension = _parse_step_dimension(step)

    base = (
        f"You are an expert industry research analyst specializing in {role_key}. "
        f"You are working on step '{step}' of an industry research pipeline for '{entity}' (market: {market}). "
    )

    if seg_name:
        base += f"You are analyzing the '{seg_name}' segment of the {entity} industry. "

    if dimension and dimension != "seg_synthesis":
        base += f"Your focus dimension is: {_DIMENSION_CN.get(dimension, dimension)}. "

    base += (
        f"Your output must be in Markdown format, well-structured with multiple sections (## headers), "
        f"include at least 3 source citations (URLs), and contain substantive analysis (minimum 3000 characters). "
        f"Write your analysis directly — do not include meta-commentary about the task itself. "
        f"If you cannot find specific data, SUPPLEMENTARY SEARCH FIRST before writing '未找到独立外部证据'. "
        f"Use thinking=high — reason carefully before writing each section.\n\n"
        f"CRITICAL: You must autonomously close the loop. When you discover data gaps during analysis:\n"
        f"1. Search for the missing data yourself (NeoData → yfinance → web_search)\n"
        f"2. Integrate the found data into your analysis\n"
        f"3. Only mark as '待核实' after 3 rounds of supplementary search still yield nothing\n"
        f"Do NOT return to the coordinator for search instructions — you ARE the search agent.\n\n"
    )

    # 角色专属规则
    step_rules = _get_step_rules(step, seg_name, seg_info)
    return base + step_rules


def _get_step_rules(step: str, seg_name: str | None, seg_info: dict | None) -> str:
    """根据 step 类型返回 ANTI-DEFECT RULES"""
    dimension = _parse_step_dimension(step)

    # value_chain 特殊规则
    if step == "step_value_chain":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. STRUCTURED JSON OUTPUT: You MUST include a ```json block at the end of your analysis '
            'containing the segments definition. Without this, the pipeline cannot proceed.\n'
            '2. SEGMENT ID FORMAT: Segment IDs must be lowercase_with_underscores, NO Chinese characters. '
            'Example: "upstream_equipment", "midstream_foundry", "downstream_design".\n'
            '3. SEGMENT COUNT: Typically 3-5 segments. Avoid over-segmentation (>6 is too granular).\n'
            '4. KEY COMPANIES: For each segment, list at least 3 key companies with their current status verified.\n'
            '5. PROFIT POOL: Each segment must have a profit_pool_pct (percentage). Total should approximate 100%.\n'
        )

    # 竞争格局规则
    if dimension == "competitive":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. MARKET SHARE DATA: Every market share claim must have a source. '
            '"CR3 > 80%" without source is insufficient.\n'
            '2. COMPETITOR STATUS: Verify current financing/IPO status of every competitor listed.\n'
            '3. CONCENTRATION METRICS: Include CR3/CR5/HHI where available with sources.\n'
        )

    # 市场规模规则
    if dimension == "market":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. SCENARIO TABLE REQUIRED: You MUST produce a scenario table '
            '(当前/5年后 × 乐观/中性/保守) with specific numbers.\n'
            '2. TAM/SAM/SOM: All three layers must have numbers with sources.\n'
            '3. GLOBAL ≠ CHINA: Never derive China market size by dividing global by population ratio.\n'
        )

    # 估值规则
    if dimension == "valuation":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. PE/PB/PS must distinguish between static/TTM/forward. Default to TTM unless specified.\n'
            '2. Historical percentile must have at least 3 years of data.\n'
            '3. Comparable companies must have their listing status verified.\n'
        )

    # 统稿规则
    if step == "step_master_synthesis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. PRESERVE TABLES: Core comparison tables from segment analyses must be preserved verbatim. '
            'Do not compress tables into narrative text.\n'
            '2. SOURCE INTEGRITY: Merge all sources from all steps into the appendix. '
            'Total source count in final report must be ≥ sum of individual step sources minus duplicates.\n'
            '3. CROSS-SEGMENT ONLY: Deduplication should only apply across segments, not within a segment.\n'
        )

    # 环节小结规则
    if dimension == "seg_synthesis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. You are summarizing ONE segment only. Read all 6 dimension outputs for this segment.\n'
            '2. Produce: Investment highlights + Risk points + Key data summary table.\n'
            '3. Keep it concise (2000-3000 chars) — this will be fed into the master synthesis.\n'
        )

    # 投研假说规则
    if step == "step_executive_hypothesis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. FALSIFIABLE HYPOTHESIS: Every hypothesis must have a falsification condition. '
            'No "always true" claims (e.g., "the industry will grow").\n'
            '2. LOGICAL CHAIN: Each hypothesis must have: premise → deduction → conclusion. '
            'No jumping directly to conclusions.\n'
            '3. QUANTITATIVE ANCHORS: At least one specific number threshold per hypothesis.\n'
            '4. VERIFIABLE QUESTIONS: ≥5 questions that can be answered Yes/No or with specific values.\n'
            '5. MARKET CONSENSUS: Cite ≥2 actual analyst views (with sources) — do not fabricate a straw-man consensus.\n'
        )

    # 投资手册规则
    if step == "step_investment_playbook":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. READ MASTER SYNTHESIS FIRST: Your primary input is the master synthesis output. '
            'Read it in full before anything.\n'
            '2. EXECUTIVE SUMMARY FIRST: The 1-page summary must be complete independently — '
            'a fund manager should be able to screenshot it and act.\n'
            '3. TARGET PRICE DERIVATION: Every stock recommendation must have ≥2 valuation methods with explicit parameters. '
            'Never give a target price without showing how you derived it.\n'
            '4. RATING SYSTEM: Use Strong Buy / Buy / Hold / Underperform / Sell. '
            'Do NOT invent new rating labels.\n'
            '5. KPI PANEL: ≥8 trackable indicators with thresholds + source + update frequency. '
            'Pure qualitative indicators (e.g., "management confidence") must be paired with quantitative proxies.\n'
            '6. DISCLAIMER: Always include "本报告不构成投资建议" at the end.\n'
        )

    # 催化剂分析规则
    if step == "step_catalyst_analysis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. TIME-ANCHORED: Every catalyst must have a time window (Q1/Q2/Q3/Q4). No vague "future" entries.\n'
            '2. CATALYST ≠ NEWS: Catalysts are forward-looking triggers, not past events. '
            'Do not list things that already happened.\n'
            '3. CONDUCTION CHAIN: Every 4-5 star catalyst must include a full conduction chain analysis '
            '(event → impact → quantitative estimate).\n'
            '4. BEAR CATALYSTS: Include at least 1-2 bear (downside) catalysts. '
            'Not everything is bullish.\n'
            '5. PRICE-IN ASSESSMENT: For each catalyst, assess how much the market has already priced in.\n'
        )

    # 共识挑战规则
    if step == "step_consensus_challenge":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. REAL SOURCES: Every consensus statement must cite ≥2 actual analyst views with sources. '
            'No fabricated "market believes" claims.\n'
            '2. FALSIFIABLE DIFFERENCE: Every differentiated view must include a verifiable falsification condition '
            'with a specific time window.\n'
            '3. RESPECT CONSENSUS: Do not construct straw-man consensus views just to knock them down. '
            'Accurately represent what the market actually believes.\n'
            '4. ERROR TYPE CLASSIFICATION: Classify each potential error by type '
            '(linear extrapolation / structural misjudgment / overreaction / underreaction / survivorship bias / fallacy of composition).\n'
            '5. ≥3 DIFFERENTIATED VIEWS: At least 3 concrete, specific points where we differ from consensus.\n'
        )

    # 场景敏感性规则
    if step == "step_scenario_sensitivity":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. HYPOTHESIS-DRIVEN: All scenario variations must be driven by changes in identifiable assumptions, '
            'not arbitrary number tweaks. Never do "optimistic = base × 1.2".\n'
            '2. PROBABILITY WEIGHTS: Three scenario probabilities must sum to 100%. '
            'Explain the reasoning behind each probability assignment.\n'
            '3. SENSITIVITY MATRIX: At least 5 assumptions × bidirectional (±) = ≥10 rows in the sensitivity matrix. '
            'Each row must include an elasticity coefficient.\n'
            '4. INFLECTION POINTS: Identify ≥2 specific assumption thresholds where the investment conclusion reverses. '
            'Include current distance to each threshold.\n'
            '5. DECISION TREE: Include a clear decision tree with specific action recommendations for each branch.\n'
        )

    return ''


# ═══════════════════════════════════════════════════════
# 质量门控
# ═══════════════════════════════════════════════════════

def _check_step_quality(task_id: str, step: str) -> dict:
    """单 step 质量评估 (0-5 分)"""
    output_path = step_output_path(task_id, step)
    if not output_path.exists():
        return {'score': 0, 'verdict': 'fail', 'issues': ['output file missing']}

    text = output_path.read_text(encoding='utf-8')
    content_len = len(text)
    urls = text.count('http')
    sections = text.count('## ')

    score = 0
    issues = []

    if content_len < 500:
        score = 0
        issues.append(f'内容过短 ({content_len} 字符)')
    elif content_len < 1000:
        score = 1
        issues.append(f'内容偏少 ({content_len} 字符)')
    elif content_len < 3000:
        score = 2
        issues.append(f'内容尚可 ({content_len} 字符)')
    elif content_len < 6000:
        score = 3
    elif content_len < 10000:
        score = 4
    else:
        score = 5

    if urls < 2:
        score = max(0, score - 1)
        issues.append(f'来源不足 ({urls} 个 URL)')

    if sections < 3:
        score = max(0, score - 1)
        issues.append(f'章节不足 ({sections} 个)')

    # value_chain 特殊检查：必须有 JSON block
    if step == "step_value_chain":
        if '```json' not in text and '"segments"' not in text:
            score = max(0, score - 2)
            issues.append('缺少结构化 segments JSON block')

    return {
        'score': score,
        'content_length': content_len,
        'url_count': urls,
        'section_count': sections,
        'threshold': STEP_QUALITY_THRESHOLD,
        'verdict': 'pass' if score >= STEP_QUALITY_THRESHOLD else 'fail',
        'issues': issues,
    }


def check_step_quality(task_id: str, step: str) -> dict:
    """公开接口：检查 step 输出质量"""
    return _check_step_quality(task_id, step)


# ═══════════════════════════════════════════════════════
# 核心：launch_next_wave
# ═══════════════════════════════════════════════════════

def launch_next_wave(task_id: str, entity: str = '', query: str = '', market: str = 'cn',
                     sequential: bool = False) -> dict:
    """统一入口 — 根据当前状态决定发射哪个 wave。

    状态机：
    1. 首次调用 → 发射 Wave 1（静态3个step）
    2. Wave 1 完成 → 触发动态生成（build_dynamic_wave_plan）
    3. 后续 Wave → 正常发射
    4. 所有 Wave 完成 → 返回 all_done

    sequential=True: 每次只发射当前 wave 的第一个待处理 step，
    完成后再调下一次 → 避免并行 Task 子代理触发 API 429。
    返回 has_more 标志需继续调用。
    """
    manifest = load_json(wave_manifest_path(task_id))

    # ── 状态 1：首次调用，Wave 0 还没跑 ──
    if manifest is None:
        manifest = {
            "generated_at": datetime.now().isoformat(timespec='seconds'),
            "dynamic_generated": False,
            "current_wave_index": 0,
            "segments": [],
            "waves": [STATIC_WAVE_0, STATIC_WAVE_1],
            "step_deps": {
                "step_executive_hypothesis": [],
                "step_ind_overview": [],
                "step_policy_scan": [],
                "step_value_chain": [],
            },
            "completed_steps": [],
            "total_waves": 2,  # Wave 0 + Wave 1，后续动态追加
        }
        save_json(wave_manifest_path(task_id), manifest)

    current_idx = manifest["current_wave_index"]
    waves = manifest["waves"]

    # ── 所有 wave 已完成 ──
    if current_idx >= len(waves):
        return {
            "all_done": True,
            "has_more": False,
            "dispatched_count": 0,
            "pipeline_status": get_pipeline_status(task_id),
        }

    current_wave = waves[current_idx]

    # ── 状态 2：STATIC_WAVE_1 完成 → 触发动态生成 ──
    if not manifest.get("dynamic_generated"):
        # 检查 Wave 1 是否真的完成了
        w1_completed = all(
            step_output_path(task_id, step).exists() and step_output_path(task_id, step).stat().st_size > 100
            for step in STATIC_WAVE_1
        )
        if w1_completed:
            # Wave 1 完成 + 之前没有动态生成 → 触发
            manifest = build_dynamic_wave_plan(task_id)
            waves = manifest["waves"]
            current_wave = waves[manifest["current_wave_index"]]
            # 更新 completed_steps
            for step in STATIC_WAVE_0:
                if step not in manifest["completed_steps"]:
                    manifest["completed_steps"].append(step)
            for step in STATIC_WAVE_1:
                if step not in manifest["completed_steps"]:
                    manifest["completed_steps"].append(step)
            save_json(wave_manifest_path(task_id), manifest)
            print(f"  🌊 动态 Wave 已生成，共 {len(waves)} 波", flush=True)

    # ── 正常发射当前 wave ──
    pending_steps = []
    for step_name in current_wave:
        if step_name not in manifest.get("completed_steps", []):
            out = step_output_path(task_id, step_name)
            if not out.exists() or out.stat().st_size < 100:
                pending_steps.append(step_name)

    if not pending_steps:
        # 当前 wave 已完成，推进到下一 wave
        manifest["current_wave_index"] = current_idx + 1
        save_json(wave_manifest_path(task_id), manifest)
        return launch_next_wave(task_id, entity, query, market, sequential=sequential)  # 递归

    # 发射 pending steps
    step_deps = manifest.get("step_deps", {})
    segments = manifest.get("segments", [])
    results = []
    has_more = False

    for i, step in enumerate(pending_steps):
        ready, missing = deps_ready(task_id, step, step_deps)
        if not ready:
            results.append({'step': step, 'status': 'blocked', 'missing': missing})
            if sequential:
                continue  # 跳过被阻塞的，继续找第一个可发射的
            continue

        timeout = STEP_TIMEOUTS.get(step, DEFAULT_STEP_TIMEOUT)
        result = launch_step(task_id, step, entity, query, timeout, market=market, segments=segments)
        results.append(result)

        if sequential:
            # 只发射一个 step，标记后续还有待处理
            if i + 1 < len(pending_steps):
                has_more = True
            break

    dispatched = [r for r in results if r.get('status') == 'dispatched']

    # 构建 task_tool_instructions
    task_instructions = []
    for r in dispatched:
        step = r['step']
        role = _get_step_role_key(step)
        brief_path = r.get('brief_path', '')
        output_path = r.get('output_path', '')

        prompt_body = (
            f'你是行业研究分析师，负责 {role}（{step}）。\n\n'
            f'【输出路径 - 必须严格遵守】\n'
            f'你必须将完整 Markdown 报告写入以下文件（绝对路径）：\n'
            f'{output_path}\n'
            f'禁止写入任何其他路径。\n'
            f'唯一完成条件：上述文件成功写入且内容完整。\n\n'
        )

        # ── 统一注入：所有有依赖的 step 都列出前序文件路径 ──
        step_deps_list = step_deps.get(step, [])
        if step_deps_list:
            prior_paths = []
            for ps in step_deps_list:
                pp = step_output_path(task_id, ps)
                prior_paths.append(f'  {ps}: {pp}')
            prompt_body += (
                f'⚠️ 前序 Step 完整输出文件（你必须逐一读取，不是跳过，是强制）：\n'
                + '\n'.join(prior_paths) + '\n'
                f'\n'
                f'brief 中的 "Prior Step Output" 部分也列出了这些路径。你必须用 Read 工具读取每个文件的完整内容——这些是你分析的核心输入数据。\n'
                f'\n'
            )

        prompt_body += (
            f'【执行步骤】\n'
            f'1. 读取 brief 文件：{brief_path}\n'
        )

        # seg_synthesis 专用指令
        if step.startswith("step_seg_synthesis_"):
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 综合该环节6个维度的分析，撰写环节小结（2000-3000字）\n'
                f'4. 产出：投资亮点 + 风险点 + 关键数据汇总表\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # cross_chain_compare 专用指令
        elif step == "step_cross_chain_compare":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 对比各环节的竞争格局、利润率、集中度、增长阶段，找出跨环节规律和结构性机会\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # catalyst_analysis 专用指令 (Wave 5)
        elif step == "step_catalyst_analysis":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 构建催化剂事件日历（12个月内，按Q分布）\n'
                f'4. 对4-5星催化剂做传导链分析（事件→影响→量化）\n'
                f'5. 产出预期差矩阵 + 催化剂热力图\n'
                f'6. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # consensus_challenge 专用指令 (Wave 5)
        elif step == "step_consensus_challenge":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 构建市场共识画像（6维度，每条有≥2家机构来源）\n'
                f'4. 挑战共识中的潜在错误（≥3条，各有错误类型分类）\n'
                f'5. 对Top 3差异化观点做详细逻辑展开+证伪条件\n'
                f'6. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # investment_thesis 专用指令
        elif step == "step_investment_thesis":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 形成投资论点（包含：核心看多逻辑、关键催化剂、估值锚定、风险收益比）\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # risk_assessment 专用指令
        elif step == "step_risk_assessment":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 识别各环节及跨环节的风险点（技术/竞争/政策/资本/宏观），给出风险矩阵\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # scenario_sensitivity 专用指令 (Wave 6)
        elif step == "step_scenario_sensitivity":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 定义三个场景（基准/乐观/悲观），各有触发条件和假设差异\n'
                f'4. 构建敏感性矩阵（≥5假设×双向=≥10行，含弹性系数）\n'
                f'5. 识别≥2个投资结论逆转的拐点阈值\n'
                f'6. 产出概率加权期望值 + 决策树\n'
                f'7. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # master_synthesis 统稿硬约束
        elif step == "step_master_synthesis":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 综合为完整的行业深度研报\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
                f'⚠️ 统稿保留硬约束：\n'
                f'- 核心对比表必须原文保留，不得删除或压缩\n'
                f'- 所有step的来源必须合并到"来源附录"，来源总数不得少于各step来源去重后总数\n'
                f'- 去重只做跨环节，不做环节内压缩\n\n'
            )

        # executive_hypothesis 专用指令 (Wave 0)
        elif step == "step_executive_hypothesis":
            prompt_body += (
                f'2. 根据 brief 中的角色指令，快速扫描行业基本面（最多2轮搜索）\n'
                f'3. 构建核心投资假说（必须有对立面和量化锚点）\n'
                f'4. 产出≥5个待验证问题，分配给后续Agent\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # investment_playbook 专用指令 (Wave 7)
        elif step == "step_investment_playbook":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 基于所有前序分析，输出投资手册：\n'
                f'   - Part 1: 一页纸投资摘要（独立完整，可截图使用）\n'
                f'   - Part 2: 二级市场标的推荐（附评级+目标价+估值推导）\n'
                f'   - Part 3: 一级市场配置地图\n'
                f'   - Part 4: 组合配置建议（三种风险偏好）\n'
                f'   - Part 5: KPI跟踪面板（≥8个指标+阈值）\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        elif step_deps_list:
            # 有依赖的常规 step（competitive/tech/market/financial/valuation/capital）
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件（不是跳过，是强制）\n'
                f'3. 根据 brief 中的角色指令执行分析，前序 step 的完整数据是你的核心输入\n'
                f'4. 如发现数据缺口，用 web_search 补搜（最多 3 轮）\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        else:
            # 无依赖的 step（ind_overview/policy_scan/value_chain）
            prompt_body += (
                f'2. 根据 brief 中的角色指令和预搜索数据，执行完整分析\n'
                f'3. 如发现数据缺口，用 web_search 补搜（最多 3 轮）\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        prompt_body += (
            f'【输出要求】\n'
            f'- ≥3000 字符\n'
            f'- ≥3 个来源引用（带 URL）\n'
            f'- 多个 ## 章节\n'
            f'- 关键数据加粗\n'
            f'- 禁止输出搜索备忘录格式的原始记录——必须是正式分析报告'
        )

        task_instructions.append({
            'step': step,
            'role': role,
            'action': 'team_task',
            'tool': 'task(name=..., team_name=...)',
            'subagent_name': 'code-explorer',
            'name': step,
            'team_name': f'ic-{task_id}',
            'mode': 'bypassPermissions',
            'prompt': prompt_body,
            'brief_path': brief_path,
            'output_path': output_path,
        })

    return {
        'wave_index': current_idx,
        'wave_label': f'Wave {current_idx + 1}/{len(waves)}',
        'steps': results,
        'dispatched_count': len(dispatched),
        'has_more': has_more,
        'all_done': False,
        'next_action': 'dispatch_tasks',
        'task_tool_instructions': task_instructions,
        'after_all_tasks_complete': (
            'launch_next_wave()' if current_idx < len(waves) - 1 else 'finalize_pipeline()'
        ),
        'pipeline_status': get_pipeline_status(task_id),
    }


# ═══════════════════════════════════════════════════════
# 管线状态 + 交付
# ═══════════════════════════════════════════════════════

def get_pipeline_status(task_id: str) -> dict:
    """返回管线当前状态快照。"""
    manifest = load_json(wave_manifest_path(task_id))
    if manifest is None:
        return {"task_id": task_id, "status": "not_started"}

    step_deps = manifest.get("step_deps", {})
    completed = manifest.get("completed_steps", [])

    steps_status = {}
    for step, deps in step_deps.items():
        out = step_output_path(task_id, step)
        if out.exists() and out.stat().st_size >= 100:
            steps_status[step] = 'completed'
            if step not in completed:
                completed.append(step)
        else:
            ready, missing = deps_ready(task_id, step, step_deps)
            steps_status[step] = 'ready' if ready else f'blocked_by:{",".join(missing)}'

    manifest["completed_steps"] = completed
    save_json(wave_manifest_path(task_id), manifest)

    current_wave = manifest.get("current_wave_index", 0)
    total_waves = len(manifest.get("waves", []))
    all_done = current_wave >= total_waves

    return {
        'task_id': task_id,
        'steps': steps_status,
        'current_wave': current_wave if not all_done else 'all_done',
        'total_waves': total_waves,
        'completed_count': sum(1 for v in steps_status.values() if v == 'completed'),
        'total_steps': len(step_deps),
        'all_steps_done': all_done,
        'segments': manifest.get("segments", []),
        'next_action': 'finalize' if all_done else f'launch_wave_{current_wave}',
    }


def finalize_pipeline(task_id: str, entity: str = '', market: str = 'cn') -> dict:
    """Phase 5：统稿完成后的交付流程"""
    from pathlib import Path as _P
    import shutil

    status = get_pipeline_status(task_id)
    if not status.get('all_steps_done'):
        incomplete = [s for s, v in status['steps'].items() if v != 'completed']
        return {'status': 'not_ready', 'incomplete_steps': incomplete}

    result = {'status': 'finalizing', 'task_id': task_id}

    # 质量门禁
    try:
        wm = load_json(wave_manifest_path(task_id))
        all_steps = list(wm.get("step_deps", {}).keys())
        scores, issues = {}, []
        for step in all_steps:
            f = step_output_path(task_id, step)
            if not f.exists():
                scores[step] = 0; issues.append(f"❰{step}❱ 缺失"); continue
            txt = f.read_text(encoding='utf-8')
            if len(txt) < 200:
                scores[step] = 0; issues.append(f"❰{step}❱ 内容过短"); continue
            uc = txt.count('http')
            if uc >= 3 and len(txt) > 3000: sc = 3
            elif uc >= 1 and len(txt) > 1000: sc = 2
            elif len(txt) > 500: sc = 1
            else: sc = 0
            scores[step] = sc
        total = sum(scores.values())
        qg = {'scores': scores, 'total': total, 'max': len(all_steps) * 3, 'pass': total >= len(all_steps) * 2, 'issues': issues}
        result['quality_gate'] = qg
    except Exception as e:
        result['quality_gate_error'] = str(e)

    # 复制到桌面
    from pathlib import Path as _P
    desktop = _P.home() / 'Desktop'
    master_md = step_output_path(task_id, "step_master_synthesis")
    deliver_path = None

    if master_md.exists():
        entity_clean = entity.replace(' ', '_').replace('/', '_') or task_id

        # 尝试生成 DOCX
        try:
            from scripts.build_ic_industry_report_docx import build_ic_report
            docx_path = build_ic_report(task_id)
            if docx_path:
                deliver_path = docx_path
                result['docx_path'] = docx_path
                print(f"  📄 DOCX 已生成: {docx_path}")
        except Exception as e:
            result['docx_error'] = str(e)
            print(f"  ⚠️ DOCX 生成失败（回退到 markdown）: {e}")

        # 如果 DOCX 失败，复制 markdown 到桌面
        if not deliver_path:
            dst = desktop / f'{entity_clean}_行业深度研究.md'
            shutil.copy2(master_md, dst)
            deliver_path = str(dst)
            print(f"  📄 已复制 Markdown 到桌面: {dst.name}")

        result['desktop_path'] = deliver_path

    # 通知 — 已移除（开源发布版本不含消息推送）

    result['status'] = 'delivered'
    result['message'] = f"行业研报已生成并复制到桌面: {deliver_path or '(markdown)'}"
    return result


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='IC Subagent Launcher — 行业研究管线 v1')
    ap.add_argument('--task-id', required=True, help='Task ID')
    ap.add_argument('--step', help='Single step to launch')
    ap.add_argument('--entity', default='', help='Industry name')
    ap.add_argument('--query', default='', help='Research query')
    ap.add_argument('--market', default='cn', choices=['us', 'hk', 'cn'], help='Market')
    ap.add_argument('--dry-run', action='store_true', help='Show what would be launched')
    ap.add_argument('--check-quality', action='store_true', help='Check quality of completed step')
    ap.add_argument('--status', action='store_true', help='Show pipeline status')
    ap.add_argument('--finalize', action='store_true', help='Finalize pipeline (delivery)')

    args = ap.parse_args()

    if args.status:
        print(json.dumps(get_pipeline_status(args.task_id), ensure_ascii=False, indent=2))
        return

    if args.check_quality and args.step:
        print(json.dumps(check_step_quality(args.task_id, args.step), ensure_ascii=False, indent=2))
        return

    if args.finalize:
        print(json.dumps(finalize_pipeline(args.task_id, args.entity, args.market), ensure_ascii=False, indent=2))
        return

    # 默认：发射下一波
    result = launch_next_wave(args.task_id, args.entity, args.query, args.market)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
