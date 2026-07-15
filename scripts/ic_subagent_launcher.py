#!/usr/bin/env python3
"""
IC (Industry Coverage) Subagent Launcher — WorkBuddy 版本 v2

行业研究管线核心编排引擎。v2 重构：
1. Archetype 驱动 — 5 种课题原型（chain_scan/tech_compare/company_deep/early_theme/commercial_mode）
2. 扁平化 Wave — 砍掉 6 维度拆分，每环节/路线一个深度分析子代理
3. Instruction Store 对齐 — roles/ 目录存放角色指令，archetypes/ 存放原型模板
4. 动态 Wave 生成 — chain_scan/tech_compare 根据 value_chain/tech_landscape 输出动态展开

子代理数对比（v1→v2）：
  chain_scan 3 环节: 32 → 11
  tech_compare 3 路线: 30 → 10
  company_deep: 32 → 9
  early_theme: 32 → 7
  commercial_mode: 32 → 10

2026-07-09 v2: archetype-driven 重构，instruction store 对齐
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

# ── Archetype 常量 ──
ARCHETYPE_CHAIN_SCAN = "chain_scan"
ARCHETYPE_TECH_COMPARE = "tech_compare"
ARCHETYPE_COMPANY_DEEP = "company_deep"
ARCHETYPE_EARLY_THEME = "early_theme"
ARCHETYPE_COMMERCIAL_MODE = "commercial_mode"

ALL_ARCHETYPES = [
    ARCHETYPE_CHAIN_SCAN, ARCHETYPE_TECH_COMPARE, ARCHETYPE_COMPANY_DEEP,
    ARCHETYPE_EARLY_THEME, ARCHETYPE_COMMERCIAL_MODE,
]

# ── 默认 archetype（当无法判定时） ──
DEFAULT_ARCHETYPE = ARCHETYPE_CHAIN_SCAN

# ── ConnectorIds 授权 ──
from scripts.ic_constants import IC_ROLE_CONNECTOR_IDS, IC_DEFAULT_CONNECTOR_IDS

# ── Tool Guide 缓存 ──
_TOOL_GUIDE_CACHE: str = ""
_TOOL_GUIDE_MTIME: float = 0.0


def _load_tool_guide(runtime_root: Path | None = None) -> str:
    """热加载 instruction_store_ic/_common_tool_guide.md（mtime 缓存）。"""
    global _TOOL_GUIDE_CACHE, _TOOL_GUIDE_MTIME
    root = runtime_root or ROOT
    guide_path = root / "instruction_store_ic" / "_common_tool_guide.md"
    if not guide_path.exists():
        return ""
    current_mtime = guide_path.stat().st_mtime
    if _TOOL_GUIDE_CACHE and current_mtime == _TOOL_GUIDE_MTIME:
        return _TOOL_GUIDE_CACHE
    _TOOL_GUIDE_CACHE = guide_path.read_text(encoding="utf-8")
    _TOOL_GUIDE_MTIME = current_mtime
    return _TOOL_GUIDE_CACHE

# ═══════════════════════════════════════════════════════
# Archetype 模板加载
# ═══════════════════════════════════════════════════════

def load_archetype_template(archetype: str) -> dict:
    """加载 archetype JSON 模板。"""
    path = INSTRUCTION_STORE / 'archetypes' / f'{archetype}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    print(f"  ⚠️ archetype 模板不存在: {path}，降级为 {DEFAULT_ARCHETYPE}")
    path = INSTRUCTION_STORE / 'archetypes' / f'{DEFAULT_ARCHETYPE}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}


def load_index() -> dict:
    """加载 instruction_store_ic/index.json。"""
    path = INSTRUCTION_STORE / 'index.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}


def _load_research_content(task_id: str) -> list[str]:
    """从 ic_topic_metadata.json 读取课题定义的 research_content 列表。

    返回空列表表示该课题没有定义 research_content（如纯 entity fallback）。
    """
    meta_path = TASKS_DIR / 'ic_topic_metadata.json'
    if not meta_path.exists():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        rc = meta.get('research_content', [])
        return rc if isinstance(rc, list) else []
    except Exception:
        return []


def _get_cross_cutting_dim_info(task_id: str, dim_id: str) -> dict | None:
    """从 wave_manifest.json 读取某个 cross_cutting 维度的信息。"""
    manifest = load_json(wave_manifest_path(task_id))
    if not manifest:
        return None
    for d in manifest.get('cross_cutting_dims', []):
        if d.get('dim_id') == dim_id:
            return d
    return None


def resolve_archetype(task_id: str) -> str:
    """从 ic_research_plan.json 或 ic_topic_metadata.json 读取 archetype。"""
    # 优先读 research plan
    plan_path = TASKS_DIR / f'{task_id}-ic_research_plan.json'
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            arch = plan.get("archetype", "")
            if arch and arch in ALL_ARCHETYPES:
                return arch
        except Exception:
            pass

    # 次选 topic metadata
    meta_path = TASKS_DIR / f'{task_id}-ic_topic_metadata.json'
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            arch = meta.get("archetype", "")
            if arch and arch in ALL_ARCHETYPES:
                return arch
        except Exception:
            pass

    return DEFAULT_ARCHETYPE


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
# 指令加载（v2: roles/ 子目录）
# ═══════════════════════════════════════════════════════

def load_instruction(role_key: str) -> str:
    """加载角色指令（从 roles/ 子目录）。"""
    role_file = INSTRUCTION_STORE / 'roles' / f'{role_key}.md'
    if role_file.exists():
        return role_file.read_text(encoding='utf-8')
    return f'Role instructions for {role_key} not found.'


def _get_step_role_key(step_name: str, archetype: str | None = None) -> str:
    """根据 step 名 + archetype 查 role key。

    v2: 从 archetype 模板的 role_map 查。
    支持精确匹配和模板匹配（如 step_segment_deep_{seg} → ic_segment_deep）。
    """
    if archetype is None:
        # 尝试从 wave_manifest 读 archetype
        wm = load_json(wave_manifest_path(_current_task_id_for_role))
        if wm:
            archetype = wm.get("archetype", DEFAULT_ARCHETYPE)
        else:
            archetype = DEFAULT_ARCHETYPE

    template = load_archetype_template(archetype)
    role_map = template.get("role_map", {})

    # 1. 精确匹配
    if step_name in role_map:
        return role_map[step_name]

    # 2. 模板匹配（step_segment_deep_upstream → step_segment_deep_{seg} → ic_segment_deep）
    #    支持 {seg} / {route} / {dim} 三种模板
    for template_key, role in role_map.items():
        if "{seg}" in template_key or "{route}" in template_key or "{dim}" in template_key:
            base = template_key.replace("_{seg}", "").replace("_{route}", "").replace("_{dim}", "")
            if step_name.startswith(f"{base}_"):
                return role

    # 3. fallback: 用 index.json 的 mcp_connector_authorization 做模糊匹配
    index = load_index()
    connector_defaults = index.get("mcp_connector_authorization", {})

    # 尝试从 step 名推断 role（去 step_ 前缀，取第一个有意义的部分）
    clean = step_name.replace("step_", "")
    parts = clean.split("_")

    # 尝试 progressively longer prefixes
    for prefix_len in range(len(parts), 0, -1):
        prefix = "_".join(parts[:prefix_len])
        candidate = f"ic_{prefix}"
        if candidate in connector_defaults:
            return candidate

    return step_name


# 全局变量 hack — 在 launch_step 时设置，供 _get_step_role_key fallback 使用
_current_task_id_for_role = ""


def _get_step_connector_ids(step: str, archetype: str | None = None) -> list[str]:
    """获取 step 的 connectorIds。

    v2: 先查 archetype 模板的 connector_map，再查 index.json 的 mcp_connector_authorization。
    """
    role_key = _get_step_role_key(step, archetype)

    # 1. 从 archetype 模板的 connector_map 查
    template = load_archetype_template(archetype or DEFAULT_ARCHETYPE)
    connector_map = template.get("connector_map", {})
    if role_key in connector_map:
        return connector_map[role_key]

    # 2. 从 index.json 的 mcp_connector_authorization 查
    index = load_index()
    defaults = index.get("mcp_connector_authorization", {})
    if role_key in defaults:
        return defaults[role_key]

    # 3. 从 ic_constants 查（兼容旧 step 前缀匹配）
    for prefix, ids in IC_ROLE_CONNECTOR_IDS.items():
        if step.startswith(prefix):
            return ids

    return IC_DEFAULT_CONNECTOR_IDS


# ═══════════════════════════════════════════════════════
# 动态解析：segments / routes
# ═══════════════════════════════════════════════════════

def parse_segments_from_value_chain(task_id: str) -> list[dict]:
    """从 value_chain step 的输出中解析 segments JSON。"""
    vc_path = step_output_path(task_id, "step_value_chain")
    if not vc_path.exists():
        print("  ⚠️ value_chain 输出不存在，降级为固定三段")
        return _fallback_segments()

    text = vc_path.read_text(encoding='utf-8')

    json_blocks = re.findall(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block)
            segments = data.get("segments", [])
            if segments and len(segments) >= 2:
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


def parse_routes_from_tech_landscape(task_id: str) -> list[dict]:
    """从 tech_landscape step 的输出中解析 competing_routes JSON。"""
    tl_path = step_output_path(task_id, "step_tech_landscape")
    if not tl_path.exists():
        print("  ⚠️ tech_landscape 输出不存在，降级为固定两路线")
        return _fallback_routes()

    text = tl_path.read_text(encoding='utf-8')

    json_blocks = re.findall(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block)
            routes = data.get("competing_routes", [])
            if routes and len(routes) >= 2:
                for route in routes:
                    raw_id = route.get("id", "")
                    normalized = _normalize_seg_id(raw_id)
                    if not normalized:
                        idx = routes.index(route)
                        normalized = f"route_{idx + 1}"
                    route["id"] = normalized
                print(f"  ✅ 解析到 {len(routes)} 条技术路线: {[r['name'] for r in routes]}")
                return routes
        except json.JSONDecodeError:
            continue

    print("  ⚠️ tech_landscape JSON 解析失败，降级为固定两路线")
    return _fallback_routes()


def _normalize_seg_id(raw_id: str) -> str:
    """规范化 segment/route id: lowercase + underscore + 纯 ASCII"""
    if not raw_id:
        return ""
    normalized = re.sub(r'[^a-zA-Z0-9_\-]', '_', raw_id).lower().strip('_')
    normalized = re.sub(r'_+', '_', normalized)
    return normalized.strip('_')


def _fallback_segments() -> list[dict]:
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


def _fallback_routes() -> list[dict]:
    return [
        {"id": "route_a", "name": "路线A", "description": "主流技术路线",
         "maturity": "mass_production", "key_players": [], "key_metrics": {}},
        {"id": "route_b", "name": "路线B", "description": "新兴技术路线",
         "maturity": "pilot", "key_players": [], "key_metrics": {}},
    ]


def _detect_cross_cutting_dimensions(task_id: str, segments: list[dict]) -> list[dict]:
    """从 research_plan.json 识别跨切面维度。

    跨切面维度 = research_dimensions 中，key_questions 提到的实体名词
    横跨 ≥2 个 segment 的 key_companies，或维度名本身暗示跨环节对比。

    Returns:
        list of dict: [{"dim_id": "chip_type", "name": "芯片品类定位与用途",
                        "key_questions": [...], "related_segments": ["gpu_design", "asic_ai_accel"]}]
    """
    plan_path = TASKS_DIR / f'{task_id}-ic_research_plan.json'
    if not plan_path.exists():
        return []
    try:
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
    except Exception:
        return []

    dimensions = plan.get('research_dimensions', [])
    if not dimensions:
        return []

    # 收集所有 segment 的 key_companies 集合（用于匹配）
    seg_company_map: dict[str, set[str]] = {}
    for seg in segments:
        seg_id = seg.get('id', '')
        companies = seg.get('key_companies', [])
        if isinstance(companies, dict):
            # 处理 {"global_leader": [...], "china_player": [...]} 格式
            flat = set()
            for v in companies.values():
                if isinstance(v, list):
                    flat.update(c.split('(')[0].strip() for c in v)
            seg_company_map[seg_id] = flat
        elif isinstance(companies, list):
            seg_company_map[seg_id] = set(c.split('(')[0].strip() for c in companies)
        else:
            seg_company_map[seg_id] = set()

    # 也收集 segment 名称/id 作为关键词
    # 额外提取英文关键词别名（如 gpu_design → "gpu"，asic_ai_accel → "asic"）
    seg_keywords: dict[str, str] = {}
    seg_aliases: dict[str, list[str]] = {}
    for seg in segments:
        seg_id = seg.get('id', '')
        seg_name = seg.get('name', '')
        seg_keywords[seg_id] = (seg_name + ' ' + seg_id).lower()
        # 从 id 中提取英文 token 作为别名（gpu_design → ["gpu", "design"]）
        tokens = [t for t in re.split(r'[_/\-\s]+', seg_id) if t.isascii() and len(t) >= 2]
        # 从 name 中提取英文 token（如 "NPU/FPGA加速" → ["npu", "fpga"]）
        name_tokens = re.findall(r'[A-Za-z][A-Za-z0-9]{1,}', seg_name)
        aliases = list(set(t.lower() for t in tokens + name_tokens))
        seg_aliases[seg_id] = aliases

    cross_cutting: list[dict] = []
    for dim in dimensions:
        dim_name = dim.get('dimension', '')
        key_qs = dim.get('key_questions', [])
        all_text = (dim_name + ' ' + ' '.join(key_qs)).lower()

        # 检查这个维度涉及几个 segment
        related_segs: set[str] = set()
        for seg_id in seg_keywords:
            # 方式1: segment name/id 完整匹配
            if seg.get('name', '').lower() in all_text or seg_id.lower() in all_text:
                related_segs.add(seg_id)
                continue
            # 方式2: 英文别名匹配（如 "gpu" 匹配 "GPU/ASIC/NPU/FPGA"）
            matched_alias = False
            for alias in seg_aliases.get(seg_id, []):
                # 使用 word boundary 匹配避免 "ed" 匹配 "EDA"
                if re.search(r'\b' + re.escape(alias) + r'\b', all_text):
                    related_segs.add(seg_id)
                    matched_alias = True
                    break
            if matched_alias:
                continue
            # 方式3: 维度文本提到 segment 的 key_companies
            for company in seg_company_map.get(seg_id, set()):
                if len(company) >= 2 and company.lower() in all_text:
                    related_segs.add(seg_id)
                    break

        # 判定: 涉及 ≥2 个 segment → 跨切面
        if len(related_segs) >= 2:
            # 生成 slug id（优先提取英文 token，纯中文则用序号）
            eng_tokens = re.findall(r'[A-Za-z][A-Za-z0-9]{1,}', dim_name)
            if eng_tokens:
                dim_slug = '_'.join(t.lower() for t in eng_tokens[:4])
            else:
                # 纯中文维度名：用维度序号 + 前几个 segment id 组合
                idx = dimensions.index(dim) + 1
                seg_prefix = '_'.join(sorted(related_segs)[:2])
                dim_slug = f'dim{idx}_{seg_prefix}'
            dim_slug = re.sub(r'[^a-zA-Z0-9_]', '_', dim_slug).strip('_')
            dim_slug = re.sub(r'_+', '_', dim_slug)
            cross_cutting.append({
                'dim_id': dim_slug,
                'name': dim_name,
                'key_questions': key_qs,
                'related_segments': sorted(related_segs),
            })

    return cross_cutting


# ═══════════════════════════════════════════════════════
# 动态 Wave 生成（v2: archetype 驱动）
# ═══════════════════════════════════════════════════════

def build_dynamic_wave_plan(task_id: str, step_filter: set[str] | None = None,
                               archetype: str | None = None) -> dict:
    """v2: 读 archetype 模板，动态生成 wave plan。

    chain_scan: 从 value_chain 解析 segments → 展开 segment_deep_{seg}
    tech_compare: 从 tech_landscape 解析 routes → 展开 route_deep_{route}
    其他: 直接用静态模板
    """
    if archetype is None:
        archetype = resolve_archetype(task_id)

    template = load_archetype_template(archetype)
    if not template:
        print(f"  ❌ archetype 模板加载失败: {archetype}")
        return {}

    waves_config = template.get("waves", [])
    is_dynamic = template.get("is_dynamic", False)
    dynamic_source = template.get("dynamic_source", "")
    always_active = set(template.get("always_active_steps", []))

    # 解析动态实体（segments 或 routes）
    dynamic_items = []
    if is_dynamic and dynamic_source == "value_chain_segments":
        dynamic_items = parse_segments_from_value_chain(task_id)
    elif is_dynamic and dynamic_source == "tech_routes":
        dynamic_items = parse_routes_from_tech_landscape(task_id)

    # 展开 waves
    waves = []
    step_deps = {}
    all_dynamic_steps = []

    for wave_cfg in waves_config:
        wave_idx = wave_cfg.get("index", len(waves))
        static_steps = wave_cfg.get("steps", [])
        steps_template = wave_cfg.get("steps_template", [])

        if steps_template and dynamic_items:
            # 动态 wave: 展开模板
            expanded = []
            for item in dynamic_items:
                item_id = item.get("id", "")
                for tpl in steps_template:
                    step = tpl.replace("{seg}", item_id).replace("{route}", item_id)
                    expanded.append(step)
                    all_dynamic_steps.append(step)
            wave_steps = expanded
        else:
            wave_steps = static_steps

        # 应用 step_filter（always_active 不受影响）
        if step_filter:
            filtered = []
            for s in wave_steps:
                if s in always_active or s in step_filter:
                    filtered.append(s)
                else:
                    # 动态 step 做前缀匹配
                    base = s.rsplit("_", 1)[0] if "_" in s else s
                    if base in step_filter or s in step_filter:
                        filtered.append(s)
                    else:
                        filtered.append(s)  # 保留——宁可多跑也不漏
            wave_steps = filtered

        waves.append(wave_steps)

    # ── 跨切面维度 wave 注入（v2026-07-10 新增）──
    # 对 chain_scan: 在 segment_deep wave 和 cross_compare wave 之间插入 cross_cutting wave
    cross_cutting_dims: list[dict] = []
    if archetype == "chain_scan" and dynamic_items:
        cross_cutting_dims = _detect_cross_cutting_dimensions(task_id, dynamic_items)
        if cross_cutting_dims:
            # 找到 segment_deep wave 的位置（通常是 Wave 2，index=2）
            # 在它之后插入一个新 wave
            seg_deep_wave_idx = None
            for wi, w in enumerate(waves):
                if w and w[0].startswith("step_segment_deep_"):
                    seg_deep_wave_idx = wi
                    break

            if seg_deep_wave_idx is not None:
                cc_steps = [f"step_cross_cutting_{d['dim_id']}" for d in cross_cutting_dims]
                # 插入在 segment_deep wave 之后
                insert_at = seg_deep_wave_idx + 1
                waves.insert(insert_at, cc_steps)
                # 更新 deps_template 中的引用（cross_compare 等依赖 __all_cross_cutting__）
                print(f"  🔄 注入 {len(cc_steps)} 个跨切面 step: {cc_steps}")

    # 构建 step_deps
    deps_template = template.get("step_deps_template", template.get("step_deps", {}))

    for step_name_flat in _flatten_waves(waves):
        if step_name_flat in deps_template:
            # 静态 step
            raw_deps = deps_template[step_name_flat]
            resolved = _resolve_deps(raw_deps, dynamic_items, archetype, template, cross_cutting_dims)
            step_deps[step_name_flat] = resolved
        elif step_name_flat.startswith("step_cross_cutting_"):
            # 跨切面 step: 依赖 value_chain + 所有 segment_deep
            cc_deps = ["step_value_chain"]
            for item in dynamic_items:
                cc_deps.append(f"step_segment_deep_{item['id']}")
            step_deps[step_name_flat] = cc_deps
        else:
            # 动态 step: 从 deps_template 的模板 key 推断
            dep_key = _find_deps_template_key(step_name_flat, deps_template)
            if dep_key:
                raw_deps = deps_template[dep_key]
                resolved = _resolve_deps(raw_deps, dynamic_items, archetype, template, cross_cutting_dims)
                step_deps[step_name_flat] = resolved
            else:
                step_deps[step_name_flat] = []

    manifest = {
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "dynamic_generated": True,
        "archetype": archetype,
        "current_wave_index": 2 if is_dynamic else 1,
        "segments": dynamic_items if dynamic_source == "value_chain_segments" else [],
        "routes": dynamic_items if dynamic_source == "tech_routes" else [],
        "cross_cutting_dims": cross_cutting_dims,
        "waves": waves,
        "step_deps": step_deps,
        "completed_steps": _get_initial_completed(waves, is_dynamic),
        "total_waves": len(waves),
        "step_filter": list(step_filter) if step_filter else [],
    }

    save_json(wave_manifest_path(task_id), manifest)
    total_steps = sum(len(w) for w in waves)
    print(f"  ✅ [{archetype}] Wave 计划已生成: {total_steps} steps, {len(waves)} waves")
    return manifest


def _flatten_waves(waves: list[list[str]]) -> list[str]:
    return [s for w in waves for s in w]


def _get_initial_completed(waves: list[list[str]], is_dynamic: bool) -> list[str]:
    """初始已完成 steps（Wave 0 + Wave 1 如果已跑完）。"""
    completed = []
    if len(waves) >= 1:
        completed.extend(waves[0])
    if is_dynamic and len(waves) >= 2:
        completed.extend(waves[1])
    return completed


def _resolve_deps(raw_deps: list, dynamic_items: list[dict], archetype: str, template: dict,
                  cross_cutting_dims: list[dict] | None = None) -> list[str]:
    """解析依赖中的特殊标记（如 __all_segment_deep__、__all_cross_cutting__）。"""
    resolved = []
    dynamic_source = template.get("dynamic_source", "")

    for dep in raw_deps:
        if dep == "__all_segment_deep__" and dynamic_source == "value_chain_segments":
            for item in dynamic_items:
                resolved.append(f"step_segment_deep_{item['id']}")
        elif dep == "__all_route_deep__" and dynamic_source == "tech_routes":
            for item in dynamic_items:
                resolved.append(f"step_route_deep_{item['id']}")
        elif dep == "__all_cross_cutting__":
            if cross_cutting_dims:
                for d in cross_cutting_dims:
                    resolved.append(f"step_cross_cutting_{d['dim_id']}")
        elif "{seg}" in dep or "{route}" in dep:
            for item in dynamic_items:
                resolved.append(dep.replace("{seg}", item["id"]).replace("{route}", item["id"]))
        elif "{dim}" in dep:
            if cross_cutting_dims:
                for d in cross_cutting_dims:
                    resolved.append(dep.replace("{dim}", d['dim_id']))
            # 如果没有 cross_cutting_dims，跳过该依赖（该 step 不会被实际生成）
        else:
            resolved.append(dep)
    return resolved


def _find_deps_template_key(step_name: str, deps_template: dict) -> str | None:
    """为动态 step 找到对应的 deps 模板 key。支持 {seg}/{route}/{dim} 模板。"""
    for key in deps_template:
        if "{seg}" in key or "{route}" in key:
            base = key.replace("_{seg}", "").replace("_{route}", "")
            if step_name.startswith(f"{base}_"):
                return key
        if "{dim}" in key:
            base = key.replace("_{dim}", "")
            if step_name.startswith(f"{base}_"):
                return key
    return None


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
                     archetype: str | None = None,
                     segments: list[dict] | None = None,
                     routes: list[dict] | None = None) -> str:
    """构建子代理任务 brief。v2: 从 archetype role_map 加载指令。"""
    global _current_task_id_for_role
    _current_task_id_for_role = task_id

    if archetype is None:
        archetype = resolve_archetype(task_id)

    role_key = _get_step_role_key(step, archetype)
    instruction = load_instruction(role_key)

    output_path = step_output_path(task_id, step)

    # 解析当前 step 的环节/路线信息
    seg_name, seg_id, seg_info = _parse_step_segment(step, segments or routes or [])
    dimension = _parse_step_dimension(step)

    # 模板占位符替换
    if "{seg_name}" in instruction or "{seg_description}" in instruction:
        instruction = instruction.replace("{seg_name}", seg_name or "未知环节")
        instruction = instruction.replace("{seg_description}", seg_info.get("description", "") if seg_info else "")
        instruction = instruction.replace("{seg_key_companies}",
                                          ", ".join(seg_info.get("key_companies", [])) if seg_info else "")
        instruction = instruction.replace("{seg_profit_pool_pct}",
                                          str(seg_info.get("profit_pool_pct", "")) if seg_info else "")

    if "{route_name}" in instruction or "{route_description}" in instruction:
        instruction = instruction.replace("{route_name}", seg_name or "未知路线")
        instruction = instruction.replace("{route_description}", seg_info.get("description", "") if seg_info else "")
        instruction = instruction.replace("{route_key_players}",
                                          ", ".join(seg_info.get("key_players", [])) if seg_info else "")
        instruction = instruction.replace("{route_maturity}",
                                          seg_info.get("maturity", "") if seg_info else "")

    if "{dimension}" in instruction:
        instruction = instruction.replace("{dimension}", _DIMENSION_CN.get(dimension, dimension or ""))

    # 跨切面维度占位符替换（v2026-07-10 新增）
    if step.startswith("step_cross_cutting_"):
        dim_id = step.replace("step_cross_cutting_", "")
        cc_dim_info = _get_cross_cutting_dim_info(task_id, dim_id)
        if cc_dim_info:
            instruction = instruction.replace("{dimension_name}", cc_dim_info.get("name", ""))
            instruction = instruction.replace("{dimension_description}", cc_dim_info.get("name", ""))
            kq = cc_dim_info.get("key_questions", [])
            kq_text = "\n".join(f"  - {q}" for q in kq) if kq else "(无)"
            instruction = instruction.replace("{key_questions}", kq_text)
            instruction = instruction.replace("{related_segments}",
                                              ", ".join(cc_dim_info.get("related_segments", [])))

    brief_lines = [
        f'# Step Brief: {role_key} ({step})',
        f'',
        f'Task: {task_id}',
        f'Entity: {entity}',
        f'Query: {query}',
        f'Archetype: {archetype}',
        f'Segment/Route: {seg_name or "N/A"}',
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
        f'1. `westock-mcp` — 行业/公司/财务/估值数据（MCP 直接调用）',
        f'2. `tyc-mcp` — 工商/股东/专利/风险信息（MCP 直接调用）',
        f'3. NeoData（Bash: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; ..."`）— A/HK股行业深度研报/宏观数据',
        f'4. 腾讯新闻（Bash: `sh ~/.workbuddy/skills/skill_2053082907836022784/scripts/run-cli.sh search "关键词" --limit 5`）— 突发新闻/实时动态',
        f'5. `web_search` — 通用搜索（内置工具，兜底）',
        f'',
        f'⚠️ NeoData/腾讯新闻/yfinance 是 Bash 脚本调用，不是直接工具。完整 Bash 代码块见 System Prompt 中的工具指南。',
        f'',
        f'### 补搜纪律',
        f'- 最多补搜 3 轮',
        f'- 补搜结果必须标注来源 URL',
        f'- 仍搜不到的标注"经 X 次搜索未找到独立来源"',
        f'',
    ]

    # Prior steps
    manifest = load_json(wave_manifest_path(task_id))
    step_deps = manifest.get("step_deps", {}) if manifest else {}

    for dep in step_deps.get(step, []):
        dep_path = step_output_path(task_id, dep)
        if dep_path.exists():
            brief_lines.append(f'')
            brief_lines.append(f'## Prior Step Output: {dep}')
            brief_lines.append(f'')
            brief_lines.append(f'完整输出文件路径：`{dep_path}`')
            brief_lines.append(f'请使用 Read 工具读取该文件的完整内容（不要依赖摘要，必须读原文）。')

    # ── 注入课题研究方向 checklist（v2026-07-10 新增）──
    # 让所有 step 的子代理"看得见"课题定义的 research_content，
    # 避免维度丢失（如"品类定位与用途"在 chain_scan 环节展开中被遗忘）。
    research_content = _load_research_content(task_id)
    if research_content:
        brief_lines.append('')
        brief_lines.append('## 课题研究方向（研究内容 Checklist）')
        brief_lines.append('')
        brief_lines.append('本课题定义了以下研究方向，你的分析必须覆盖与当前环节/任务相关的项。')
        brief_lines.append('未覆盖的项必须明确标注"本环节不涉及"而非省略。')
        brief_lines.append('')
        for i, rc in enumerate(research_content, 1):
            brief_lines.append(f'{i}. {rc}')

    return '\n'.join(brief_lines)


# ── 辅助：解析 step 的环节和维度 ──

_DIMENSION_CN = {
    "competitive": "竞争格局",
    "tech": "技术趋势",
    "market": "市场规模",
    "financial": "财务基准",
    "valuation": "估值基准",
    "capital": "资本动向",
    "segment_deep": "环节深度分析",
    "route_deep": "路线深度分析",
    "seg_synthesis": "环节小结",
}


def _parse_step_segment(step: str, items: list[dict]) -> tuple[str | None, str | None, dict | None]:
    """解析 step 属于哪个环节/路线。返回 (name, id, info)"""
    clean = step.replace("step_", "")
    for item in items:
        sid = item["id"]
        if clean.endswith(f"_{sid}") or clean == f"seg_synthesis_{sid}":
            return item.get("name"), sid, item
    return None, None, None


def _parse_step_dimension(step: str) -> str | None:
    """解析 step 的分析维度"""
    clean = step.replace("step_", "")
    for dim in ["segment_deep", "route_deep", "competitive", "tech", "market",
                 "financial", "valuation", "capital", "seg_synthesis"]:
        if clean.startswith(f"{dim}_") or clean == dim:
            return dim
    return None


# ═══════════════════════════════════════════════════════
# 子代理发射
# ═══════════════════════════════════════════════════════

def launch_step(task_id: str, step: str, entity: str = '', query: str = '',
                timeout: int = 900, dry_run: bool = False, market: str = 'cn',
                archetype: str | None = None,
                segments: list[dict] | None = None,
                routes: list[dict] | None = None) -> dict:
    """启动单个子代理 step"""
    global _current_task_id_for_role
    _current_task_id_for_role = task_id

    if archetype is None:
        archetype = resolve_archetype(task_id)

    output_path = step_output_path(task_id, step)
    receipt_path = step_spawn_receipt_path(task_id, step)
    manifest = step_manifest_path(task_id, step)

    # 检查依赖
    wm = load_json(wave_manifest_path(task_id))
    step_deps = wm.get("step_deps", {}) if wm else {}
    ready, missing = deps_ready(task_id, step, step_deps)
    if not ready:
        return {'step': step, 'status': 'blocked', 'reason': f'Dependencies not ready: {missing}'}

    # 构建 brief
    brief = build_step_brief(task_id, step, entity, query, archetype, segments, routes)
    brief_path = TASKS_DIR / f'{task_id}-brief-{step}.md'
    brief_path.write_text(brief, encoding='utf-8')

    if dry_run:
        return {'step': step, 'status': 'dry_run', 'brief_path': str(brief_path), 'output_path': str(output_path)}

    # 清理旧输出
    for p in (receipt_path, manifest):
        if p.exists():
            p.unlink()

    # 写入 manifest
    role_key = _get_step_role_key(step, archetype)
    system_prompt = build_step_prompt(step, entity, market, archetype, segments, routes)

    manifest_data = {
        'task_id': task_id,
        'step': step,
        'role': role_key,
        'entity': entity,
        'query': query,
        'archetype': archetype,
        'market': market,
        'system_prompt': system_prompt,
        'connectorIds': _get_step_connector_ids(step, archetype),
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


def _build_inline_data_source_guide(role: str, step: str, entity: str = "") -> str:
    """为 inline prompt 生成角色专属的数据源路由指引。

    核心目的：子代理的 inline prompt 直接告诉它"搜什么→用什么工具"，
    不要让它自己去翻 brief 或 system_prompt 里的 _common_tool_guide.md。

    entity: 课题名称，用于动态引用行业板块和公司名称，避免硬编码。
    """
    sector_hint = entity if entity else "课题对应行业"
    # 按角色分发数据源路由
    if role == 'ic_executive_hypothesis':
        return (
            f'⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            f'- 行业板块走势/估值 → westock-mcp: data_sector（查 {sector_hint} 板块）\n'
            f'- 最新行业动态 → 腾讯新闻 CLI（Bash 调用，见 System Prompt 工具指南）\n'
            f'- 龙头公司实时估值锚 → westock-mcp: data_quote（查 {sector_hint} 龙头公司）\n'
            f'- 行业研报/市场规模 → westock-mcp: data_report\n'
            f'- web_search 仅作兜底，结构化源搜不到才用\n\n'
        )
    elif role == 'ic_market_overview':
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 行业板块走势/估值水平 → westock-mcp: data_sector\n'
            '- 行业市场规模/TAM/CAGR → NeoData(Bash) → web_search 兜底\n'
            '- 券商行业研报 → westock-mcp: data_report → NeoData(Bash)\n'
            '- 突发行业动态 → 腾讯新闻 CLI(Bash)\n'
            '- 可比公司估值/财务 → westock-mcp: data_finance\n'
            '- web_search 仅作兜底，结构化源搜不到才用\n\n'
        )
    elif role in ('ic_competitive', 'ic_segment_deep'):
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 企业工商/股东/融资 → tyc-mcp: search_companies → call_tool\n'
            '- 上市公司财务对比 → westock-mcp: data_finance + data_quote\n'
            '- 机构评级/一致预期 → westock-mcp: data_rating\n'
            '- 竞品最新动态 → 腾讯新闻 CLI(Bash)\n'
            '- 专利布局/研发能力 → tyc-mcp: call_tool(search_patents)\n'
            '- 市场份额/CR3/CR5 → westock-mcp + web_search 交叉验证\n'
            '- web_search 仅作兜底，结构化源搜不到才用\n\n'
        )
    elif role in ('ic_tech_product', 'ic_route_deep'):
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 技术论文/arxiv → web_search("arxiv {关键词} {YYYY}") + web_fetch 读全文\n'
            '- 专利检索 → tyc-mcp: search_patents\n'
            '- 产品参数/性能对比 → web_search + web_fetch\n'
            '- 技术突破新闻 → 腾讯新闻 CLI(Bash)\n'
            '- 公司研发投入/研发费用率 → westock-mcp: data_finance\n'
            '- web_search 用于学术/技术类搜索是合理的，但商业数据仍优先结构化源\n\n'
        )
    elif role == 'ic_supply_chain':
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 产业链图谱/环节梳理 → westock-mcp: data_industry_chain\n'
            '- 企业画像/技术能力 → tyc-mcp: search_companies → get_company_capabilities\n'
            '- 招投标/政府采购 → tyc-mcp: search_bids\n'
            '- 产能/订单动态 → 腾讯新闻 CLI(Bash)\n'
            '- 行业深度数据 → NeoData(Bash)\n'
            '- web_search 仅作兜底，结构化源搜不到才用\n\n'
        )
    elif role == 'ic_policy_risk':
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 国内政策文件/产业规划 → web_search("site:gov.cn {关键词}")\n'
            '- 企业司法/风险/行政处罚 → tyc-mcp: call_tool（风险扫描类）\n'
            '- 出口管制/制裁清单 → web_search("BIS entity list {关键词}")\n'
            '- 政策最新动态/解读 → 腾讯新闻 CLI(Bash)\n'
            '- 地缘风险/贸易摩擦 → web_search + 腾讯新闻 CLI\n\n'
        )
    elif role in ('ic_unit_economics', 'ic_business_overview'):
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 公司财务 → westock-mcp: data_finance\n'
            '- 客户/供应商关系 → tyc-mcp: call_tool\n'
            '- 定价/收费模式 → web_search + web_fetch（产品官网）\n'
            '- 用户数据/留存/渗透率 → web_search\n\n'
        )
    elif role == 'ic_feasibility':
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 学术论文/前沿研究 → web_search("arxiv ...") + web_fetch\n'
            '- 实验进展/里程碑 → web_search + 腾讯新闻 CLI(Bash)\n'
            '- 专利 → tyc-mcp: search_patents\n'
            '- 项目/公司融资 → tyc-mcp: search_companies → web_search\n\n'
        )
    elif role in ('ic_catalyst', 'ic_consensus'):
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 重大事件/业绩会/并购 → westock-mcp: data_events\n'
            '- 机构评级/一致预期 → westock-mcp: data_rating\n'
            '- 资金流向/北向持仓 → westock-mcp: data_fund_flow + data_north_holding\n'
            '- 最新动态 → 腾讯新闻 CLI(Bash)\n'
            '- web_search 仅作兜底\n\n'
        )
    elif role == 'ic_report_synthesizer':
        return (
            '⚠️ 数据源路由：统稿不搜索新数据。综合全部前序 wave 输出。\n'
            '如需补充验证，仅通过 westock-mcp / tyc-mcp 定向查询，不超过 2 次。\n\n'
        )
    else:
        # 通用 fallback
        return (
            '⚠️ 数据源路由（按优先级执行，不要只用 web_search）：\n'
            '- 公司财务/行情/估值 → westock-mcp: data_finance / data_quote\n'
            '- 企业工商/股东/专利 → tyc-mcp: search_companies → call_tool\n'
            '- 行业研报/板块数据 → westock-mcp: data_report / data_sector\n'
            '- **券商研报/行业深度报告/财经新闻** → NeoData Bash(`data_type="doc"`) — 质量远优于 web_search\n'
            '- 中文实时新闻 → 腾讯新闻 CLI(Bash)\n'
            '- **美股新闻/earnings/分析师** → Yahoo Finance Bash(`_yahoo_search`) — 英文金融新闻首选\n'
            '- 美股估值 → yfinance(Bash)\n'
            '- web_search 仅作兜底，结构化源搜不到才用\n\n'
        )


def build_step_prompt(step: str, entity: str, market: str = 'cn',
                      archetype: str | None = None,
                      segments: list[dict] | None = None,
                      routes: list[dict] | None = None) -> str:
    """构建子代理系统级提示词"""
    role_key = _get_step_role_key(step, archetype)
    items = segments or routes or []
    seg_name, seg_id, seg_info = _parse_step_segment(step, items)
    dimension = _parse_step_dimension(step)

    base = (
        f"You are an expert industry research analyst specializing in {role_key}. "
        f"You are working on step '{step}' of an industry research pipeline for '{entity}' (market: {market}). "
        f"Archetype: {archetype or 'unknown'}. "
    )

    if seg_name:
        base += f"You are analyzing the '{seg_name}' segment/route. "

    if dimension and dimension not in ("seg_synthesis",):
        base += f"Your focus dimension is: {_DIMENSION_CN.get(dimension, dimension)}. "

    base += (
        f"Your output must be in Markdown format, well-structured with multiple sections (## headers), "
        f"include at least 3 source citations (URLs), and contain substantive analysis (minimum 3000 characters). "
        f"Write your analysis directly — do not include meta-commentary about the task itself. "
        f"If you cannot find specific data, SUPPLEMENTARY SEARCH FIRST before writing '未找到独立外部证据'. "
        f"Use thinking=high — reason carefully before writing each section.\n\n"
        f"CRITICAL: You must autonomously close the loop. When you discover data gaps during analysis:\n"
        f"1. Search for the missing data yourself (westock-mcp → tyc-mcp → NeoData(Bash) → 腾讯新闻(Bash) → web_search)\n"
        f"2. Integrate the found data into your analysis\n"
        f"3. Only mark as '待核实' after 3 rounds of supplementary search still yield nothing\n"
        f"Do NOT return to the coordinator for search instructions — you ARE the search agent.\n\n"
    )

    # 注入 _common_tool_guide.md — 子代理需要 Bash 调用示例才能使用 NeoData/腾讯新闻/yfinance
    tool_guide = _load_tool_guide()
    if tool_guide:
        base += f"\n\n## 数据源使用指南（Bash 调用示例）\n\n{tool_guide}\n\n"

    step_rules = _get_step_rules(step, seg_name, seg_info)
    return base + step_rules


def _get_step_rules(step: str, seg_name: str | None, seg_info: dict | None) -> str:
    """根据 step 类型返回 ANTI-DEFECT RULES"""
    dimension = _parse_step_dimension(step)

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

    if step == "step_tech_landscape":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. STRUCTURED JSON OUTPUT: You MUST include a ```json block containing competing_routes array.\n'
            '2. ROUTE ID FORMAT: Route IDs must be lowercase_with_underscores, NO Chinese characters.\n'
            '3. ROUTE COUNT: Typically 2-5 routes. Each route must have name, description, maturity, key_players.\n'
            '4. MATURITY LEVELS: Use "lab", "pilot", or "mass_production" — no other values.\n'
        )

    if dimension == "segment_deep":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. COVER ALL 5 DIMENSIONS: competitive, tech, market, financial, investment mapping — each must have data anchors.\n'
            '2. DATA ANCHORS: Every dimension must have at least 1 specific number with source.\n'
            '3. TABLES REQUIRED: Must include a competitor tier table and a financial comparison table.\n'
            '4. NO VAGUE CLAIMS: "市场前景广阔" without numbers = defect.\n'
        )

    if dimension == "route_deep":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. COVER ALL 5 DIMENSIONS: performance, cost, ecosystem, adoption, supply chain — each must have data anchors.\n'
            '2. QUANTIFY EVERYTHING: "性能优异" without numbers = defect.\n'
            '3. PARAMETER TABLE REQUIRED: Must include comparison table with specific metrics.\n'
            '4. COST CROSSOVER: Must estimate when this route reaches cost parity with alternatives.\n'
        )

    if dimension == "competitive":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. MARKET SHARE DATA: Every market share claim must have a source.\n'
            '2. COMPETITOR STATUS: Verify current financing/IPO status of every competitor listed.\n'
            '3. CONCENTRATION METRICS: Include CR3/CR5/HHI where available with sources.\n'
        )

    if dimension == "market":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. SCENARIO TABLE REQUIRED: (当前/5年后 × 乐观/中性/保守) with specific numbers.\n'
            '2. TAM/SAM/SOM: All three layers must have numbers with sources.\n'
            '3. GLOBAL ≠ CHINA: Never derive China market size by dividing global by population ratio.\n'
        )

    if step == "step_master_synthesis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. PRESERVE TABLES: Core comparison tables from segment/route analyses must be preserved verbatim.\n'
            '2. SOURCE INTEGRITY: Merge all sources into the appendix. Total source count ≥ sum of individual step sources minus duplicates.\n'
            '3. CROSS-SEGMENT SYNTHESIS: Your value is making dimensions talk to each other, not just concatenating.\n'
        )

    if step == "step_executive_hypothesis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. FALSIFIABLE HYPOTHESIS: Every hypothesis must have a falsification condition.\n'
            '2. LOGICAL CHAIN: Each hypothesis must have: premise → deduction → conclusion.\n'
            '3. QUANTITATIVE ANCHORS: At least one specific number threshold per hypothesis.\n'
            '4. VERIFIABLE QUESTIONS: ≥5 questions that can be answered Yes/No or with specific values.\n'
        )

    if step == "step_catalyst" or step == "step_catalyst_analysis":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. TIME-ANCHORED: Every catalyst must have a time window (Q1/Q2/Q3/Q4).\n'
            '2. CATALYST ≠ NEWS: Forward-looking triggers only, not past events.\n'
            '3. CONDUCTION CHAIN: Every high-impact catalyst must include: event → impact → quantitative estimate.\n'
        )

    if step == "step_consensus" or step == "step_consensus_challenge":
        return (
            'ANTI-DEFECT RULES:\n'
            '1. REAL SOURCES: Every consensus statement must cite ≥2 actual analyst views with sources.\n'
            '2. FALSIFIABLE DIFFERENCE: Every differentiated view must include a verifiable falsification condition.\n'
            '3. ≥3 DIFFERENTIATED VIEWS: At least 3 concrete points where we differ from consensus.\n'
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

    # tech_landscape 特殊检查
    if step == "step_tech_landscape":
        if '```json' not in text and '"competing_routes"' not in text:
            score = max(0, score - 2)
            issues.append('缺少结构化 competing_routes JSON block')

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
    return _check_step_quality(task_id, step)


# ═══════════════════════════════════════════════════════
# 核心：launch_next_wave（v2: archetype 驱动）
# ═══════════════════════════════════════════════════════

def launch_next_wave(task_id: str, entity: str = '', query: str = '', market: str = 'cn',
                     sequential: bool = False, step_filter: set[str] | None = None) -> dict:
    """统一入口 — 根据当前状态决定发射哪个 wave。

    v2: archetype 驱动。
    - 首次调用 → 从 research plan 读 archetype → 生成 Wave 0
    - Wave 0 完成 → Wave 1
    - Wave 1 完成 → 动态 archetype 触发 build_dynamic_wave_plan
    - 后续正常推进

    sequential=True: 每次只发射当前 wave 的第一个待处理 step。
    step_filter: activated_steps 集合。
    """
    global _current_task_id_for_role
    _current_task_id_for_role = task_id

    manifest = load_json(wave_manifest_path(task_id))
    archetype = resolve_archetype(task_id)

    # ── 状态 1：首次调用，没有 manifest ──
    if manifest is None:
        template = load_archetype_template(archetype)
        waves_config = template.get("waves", [])

        # 初始化前 2 个 wave（Wave 0 + Wave 1）
        initial_waves = []
        initial_deps = {}
        deps_template = template.get("step_deps_template", template.get("step_deps", {}))
        always_active = set(template.get("always_active_steps", []))

        for i, wave_cfg in enumerate(waves_config[:2]):
            steps = wave_cfg.get("steps", [])
            # 应用 step_filter
            if step_filter:
                steps = [s for s in steps if s in step_filter or s in always_active or not step_filter]
            initial_waves.append(steps)
            for s in steps:
                if s in deps_template:
                    initial_deps[s] = deps_template[s]
                else:
                    initial_deps[s] = []

        manifest = {
            "generated_at": datetime.now().isoformat(timespec='seconds'),
            "dynamic_generated": False,
            "archetype": archetype,
            "current_wave_index": 0,
            "segments": [],
            "routes": [],
            "waves": initial_waves,
            "step_deps": initial_deps,
            "completed_steps": [],
            "total_waves": len(initial_waves),
            "step_filter": list(step_filter) if step_filter else [],
        }
        save_json(wave_manifest_path(task_id), manifest)
        print(f"  🌊 [{archetype}] 初始 Wave 计划: {len(initial_waves)} waves", flush=True)

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

    # ── 状态 2：动态 archetype 的 Wave 1 完成 → 触发动态生成 ──
    is_dynamic = load_archetype_template(archetype).get("is_dynamic", False)
    if is_dynamic and not manifest.get("dynamic_generated"):
        # 检查 Wave 1 是否完成
        if len(waves) >= 2:
            w1_steps = waves[1]
            w1_completed = all(
                step_output_path(task_id, step).exists() and step_output_path(task_id, step).stat().st_size > 100
                for step in w1_steps
            )
            if w1_completed:
                saved_filter = set(manifest.get("step_filter", []))
                effective_filter = step_filter or saved_filter

                manifest = build_dynamic_wave_plan(task_id, step_filter=effective_filter, archetype=archetype)
                waves = manifest["waves"]
                current_wave = waves[manifest["current_wave_index"]]

                # 标记 Wave 0 + Wave 1 完成
                for w_idx in range(2):
                    for step in waves[w_idx]:
                        if step not in manifest["completed_steps"]:
                            manifest["completed_steps"].append(step)
                save_json(wave_manifest_path(task_id), manifest)
                print(f"  🌊 [{archetype}] 动态 Wave 已生成，共 {len(waves)} 波", flush=True)

    # ── 正常发射当前 wave ──
    pending_steps = []
    for step_name in current_wave:
        if step_name not in manifest.get("completed_steps", []):
            out = step_output_path(task_id, step_name)
            if not out.exists() or out.stat().st_size < 100:
                pending_steps.append(step_name)

    if not pending_steps:
        manifest["current_wave_index"] = current_idx + 1
        save_json(wave_manifest_path(task_id), manifest)
        return launch_next_wave(task_id, entity, query, market, sequential=sequential)

    # 发射 pending steps
    step_deps = manifest.get("step_deps", {})
    segments = manifest.get("segments", [])
    routes = manifest.get("routes", [])
    results = []
    has_more = False

    for i, step in enumerate(pending_steps):
        ready, missing = deps_ready(task_id, step, step_deps)
        if not ready:
            results.append({'step': step, 'status': 'blocked', 'missing': missing})
            if sequential:
                continue
            continue

        # 从 archetype 模板读 timeout
        template = load_archetype_template(archetype)
        timeouts = template.get("step_timeouts", {})
        default_timeout = template.get("default_timeout", 900)
        # 尝试精确匹配，再尝试模板匹配
        timeout = timeouts.get(step, default_timeout)
        for tpl_key, tpl_timeout in timeouts.items():
            if "{seg}" in tpl_key or "{route}" in tpl_key:
                base = tpl_key.replace("_{seg}", "").replace("_{route}", "")
                if step.startswith(f"{base}_"):
                    timeout = tpl_timeout
                    break

        result = launch_step(task_id, step, entity, query, timeout, market=market,
                            archetype=archetype, segments=segments, routes=routes)
        results.append(result)

        if sequential:
            if i + 1 < len(pending_steps):
                has_more = True
            break

    dispatched = [r for r in results if r.get('status') == 'dispatched']

    # 构建 task_tool_instructions
    task_instructions = []
    for r in dispatched:
        step = r['step']
        role = _get_step_role_key(step, archetype)
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
                f'brief 中的 "Prior Step Output" 部分也列出了这些路径。你必须用 Read 工具读取每个文件的完整内容。\n'
                f'\n'
            )

        # ── 角色专属数据源路由（v2.1: 嵌入 inline prompt，子代理直接看到）──
        data_source_guide = _build_inline_data_source_guide(role, step, entity=entity)

        prompt_body += (
            f'【执行步骤】\n'
            f'1. 读取 brief 文件：{brief_path}\n'
        )

        # step 专用指令
        if step == "step_master_synthesis":
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 综合为完整的行业深度研报\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
                f'⚠️ 统稿保留硬约束：\n'
                f'- 核心对比表必须原文保留，不得删除或压缩\n'
                f'- 所有step的来源必须合并到"来源附录"\n'
                f'- 去重只做跨环节/跨路线，不做环节内压缩\n\n'
            )
        elif step == "step_executive_hypothesis":
            prompt_body += (
                f'2. {data_source_guide}'
                f'3. 根据 brief 中的角色指令，快速扫描行业基本面（最多2轮搜索）\n'
                f'4. 构建核心投资假说（必须有对立面和量化锚点）\n'
                f'5. 产出≥5个待验证问题\n'
                f'6. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        elif step == "step_value_chain":
            prompt_body += (
                f'2. {data_source_guide}'
                f'3. 根据 brief 执行产业链分析\n'
                f'4. ⚠️ 必须包含 ```json block（segments 定义），否则管线无法继续\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        elif step == "step_tech_landscape":
            prompt_body += (
                f'2. {data_source_guide}'
                f'3. 根据 brief 执行技术全景扫描\n'
                f'4. ⚠️ 必须包含 ```json block（competing_routes 定义），否则管线无法继续\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        elif step_deps_list:
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. {data_source_guide}'
                f'4. 根据 brief 中的角色指令执行分析，如发现数据缺口按上方数据源路由补搜（最多 3 轮）\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        else:
            prompt_body += (
                f'2. {data_source_guide}'
                f'3. 根据 brief 中的角色指令执行完整分析，如发现数据缺口按上方数据源路由补搜（最多 3 轮）\n'
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
            'connectorIds': _get_step_connector_ids(step, archetype),
            'prompt': prompt_body,
            'brief_path': brief_path,
            'output_path': output_path,
        })

    return {
        'wave_index': current_idx,
        'wave_label': f'Wave {current_idx + 1}/{len(waves)}',
        'archetype': archetype,
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
        'archetype': manifest.get("archetype", "unknown"),
        'steps': steps_status,
        'current_wave': current_wave if not all_done else 'all_done',
        'total_waves': total_waves,
        'completed_count': sum(1 for v in steps_status.values() if v == 'completed'),
        'total_steps': len(step_deps),
        'all_steps_done': all_done,
        'segments': manifest.get("segments", []),
        'routes': manifest.get("routes", []),
        'next_action': 'finalize' if all_done else f'launch_wave_{current_wave}',
    }


def finalize_pipeline(task_id: str, entity: str = '', market: str = 'cn') -> dict:
    """交付流程"""
    status = get_pipeline_status(task_id)
    if not status.get('all_steps_done'):
        incomplete = [s for s, v in status['steps'].items() if v != 'completed']
        return {'status': 'not_ready', 'incomplete_steps': incomplete}

    result = {'status': 'finalizing', 'task_id': task_id, 'archetype': status.get('archetype')}

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
        qg = {'scores': scores, 'total': total, 'max': len(all_steps) * 3,
              'pass': total >= len(all_steps) * 2, 'issues': issues}
        result['quality_gate'] = qg
    except Exception as e:
        result['quality_gate_error'] = str(e)

    # 复制到桌面
    desktop = Path.home() / 'Desktop'
    master_md = step_output_path(task_id, "step_master_synthesis")
    deliver_path = None

    if master_md.exists():
        entity_clean = entity.replace(' ', '_').replace('/', '_') or task_id

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

        if not deliver_path:
            dst = desktop / f'{entity_clean}_行业深度研究.md'
            import shutil
            shutil.copy2(master_md, dst)
            deliver_path = str(dst)
            print(f"  📄 已复制 Markdown 到桌面: {dst.name}")

        result['desktop_path'] = deliver_path

    result['status'] = 'delivered'
    result['message'] = f"行业研报已生成并复制到桌面: {deliver_path or '(markdown)'}"
    return result


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='IC Subagent Launcher — 行业研究管线 v2 (archetype-driven)')
    ap.add_argument('--task-id', required=True, help='Task ID')
    ap.add_argument('--step', help='Single step to launch')
    ap.add_argument('--entity', default='', help='Industry name')
    ap.add_argument('--query', default='', help='Research query')
    ap.add_argument('--market', default='cn', choices=['us', 'hk', 'cn'], help='Market')
    ap.add_argument('--dry-run', action='store_true', help='Show what would be launched')
    ap.add_argument('--check-quality', action='store_true', help='Check quality of completed step')
    ap.add_argument('--status', action='store_true', help='Show pipeline status')
    ap.add_argument('--finalize', action='store_true', help='Finalize pipeline (delivery)')
    ap.add_argument('--archetype', default='', help='Override archetype (chain_scan/tech_compare/company_deep/early_theme/commercial_mode)')

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
