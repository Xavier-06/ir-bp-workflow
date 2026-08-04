#!/usr/bin/env python3
"""
IR Subagent Launcher — WorkBuddy 版本 v3

无需外部 LLM API。发射器负责：
1. 构建 step brief（角色指令 + pre-search + 前序 step 输出）
2. 写入 spawn receipt（让 execution-loop 知道 step 已发射）
3. 写入 agent task manifest（让主 AI 知道需要执行什么）

实际的 LLM 推理由 WorkBuddy 主 AI 通过 Task 子代理完成：
- 方式 A（推荐）: 主 AI 读取 manifest，用 Task 工具逐 step 派发
- 方式 B（CLI）: python3 ir_agent_runner.py --manifest <path> 逐 step 执行
- 方式 C（DashScope 回退）: 如果 DASHSCOPE_API_KEY 可用，可直调

保留原有 8-step 拓扑、4-wave 并行发射、质量门控、补搜重写机制。

2026-04-13 v1: DashScope 直调版
2026-04-13 v3: 改为 WorkBuddy Task 子代理版（无外部 API 依赖）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from scripts.ir_research_planner import (
        load_research_plan,
        normalize_research_plan_contract,
        prepare_research_plan,
        research_plan_path,
        validate_research_plan_ready,
    )
except ModuleNotFoundError:  # direct script execution from scripts/
    from ir_research_planner import (
        load_research_plan,
        normalize_research_plan_contract,
        prepare_research_plan,
        research_plan_path,
        validate_research_plan_ready,
    )

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / 'data' / 'tasks'
INSTRUCTION_STORE = ROOT / 'instruction_store_ir'
INDUSTRY_OVERLAYS_DIR = INSTRUCTION_STORE / 'industry_overlays'
REFS_DIR = ROOT / 'skills' / 'ir-coordinator' / 'references'

# 行业关键词 → overlay 文件名映射
_INDUSTRY_KEYWORDS = {
    'semiconductor': [
        '半导体', '芯片', '晶圆', '封测', '光刻', 'EDA', 'ASIC', 'GPU', 'CPU',
        'DRAM', 'NAND', '存储', '模拟', '射频', '功率', 'MCU', 'FPGA', 'SoC',
        '澜起', '中芯', '台积电', '海光', '寒武纪', '韦尔', '兆易', '北方华创',
        'semiconductor', 'chip', 'foundry', 'wafer',
    ],
    'consumer': [
        '白酒', '食品', '饮料', '零售', '餐饮', '消费', '美妆', '服装', '家电',
        '茅台', '五粮液', '海天', '伊利', '农夫山泉', '海底捞', '安踏', '美的',
        'consumer', 'liquor', 'beverage', 'food',
    ],
    'internet': [
        '互联网', '电商', '游戏', '社交', '广告', 'SaaS', '直播', '短视频',
        '阿里巴巴', '腾讯', '美团', '拼多多', '京东', '字节', '百度', '快手',
        'MiniMax', '商汤', '科大讯飞', '金山办公',
        'internet', 'ecommerce', 'gaming', 'social', 'AI', 'cloud',
    ],
    'heavy_asset': [
        '锂电', '光伏', '储能', '风电', '电动车', '化工', '钢铁', '航运', '养殖',
        '水泥', '有色', '铜', '铝', '锂', '钴', '镍', '稀土',
        '宁德', '隆基', '通威', '比亚迪', '中远', '万华', '紫金',
        'solar', 'battery', 'lithium', 'shipping', 'chemical',
    ],
    'financial': [
        '银行', '保险', '券商', '证券', '信托', '基金', '金融', '金融科技',
        '工商', '建设', '招商', '平安', '中信', '国泰', '华泰', '新华', '中国人寿',
        'bank', 'insurance', 'broker', 'securities', 'financial',
    ],
}

# Overlay 缓存（避免每次 build_step_prompt 都读文件）
_OVERLAY_CACHE: dict[str, str] = {}


def _infer_ir_industry(entity: str) -> str:
    """从标的名称推断行业 overlay 标签。三层匹配（照抄 IC ic_topic_intake._infer_category 模式）。

    返回 overlay 文件名（不含 .md）或空字符串。
    """
    # ── 层1：精确匹配（多字符专有名词，直接子串）──
    _EXACT: dict[str, str] = {
        # AI 硬件 / 机器人
        '人形机器人': 'ai_hardware', 'Optimus': 'ai_hardware', '灵巧手': 'ai_hardware',
        '具身': 'ai_hardware', '减速器': 'ai_hardware', '丝杠': 'ai_hardware',
        '宇树': 'ai_hardware', '优必选': 'ai_hardware', '伺服电机': 'ai_hardware',
        # 半导体
        '晶圆': 'semiconductor', '光刻': 'semiconductor', '封测': 'semiconductor',
        'GPU': 'semiconductor', 'Fabless': 'semiconductor', 'Foundry': 'semiconductor',
        # 医药
        '创新药': 'pharma', 'ADC': 'pharma', '管线': 'pharma', 'Biotech': 'pharma',
        'CXO': 'pharma', '临床': 'pharma',
        # 汽车
        '电动车': 'auto', '智能驾驶': 'auto', '自动驾驶': 'auto', '智驾': 'auto',
        '整车': 'auto', '新势力': 'auto',
        # 重资产 / 新能源
        '光伏': 'heavy_asset', '锂电': 'heavy_asset', '锂矿': 'heavy_asset',
        '化工': 'heavy_asset', '钢铁': 'heavy_asset', '航运': 'heavy_asset',
        '电池': 'heavy_asset', '储能': 'heavy_asset',
        # 房地产 / REITs
        'REITs': 'realestate', '物流地产': 'realestate', '产业园': 'realestate',
        '房地产': 'realestate', '住宅开发': 'realestate',
        # 消费
        '白酒': 'consumer', '免税': 'consumer', '餐饮': 'consumer', '零售': 'consumer',
        # 金融
        '银行': 'financial', '保险': 'financial', '券商': 'financial', '证券': 'financial',
        '信托': 'financial', '基金': 'financial',
    }
    for kw, industry in _EXACT.items():
        if kw in entity:
            return industry

    # ── 层2：单字符边界匹配（排除化合物误判）──
    _SINGLE: dict[str, tuple[str, set, set]] = {
        # (行业, 前缀排除字符集, 后缀排除字符集)
        '锂': ('heavy_asset', set('氢氧'), set('')),  # 氢氧化锂 → 不是氢能
        '芯': ('semiconductor', set(''), set('')),
        '药': ('pharma', set(''), set('')),
    }
    for kw, (ind, prev_excl, next_excl) in _SINGLE.items():
        idx = entity.find(kw)
        if idx >= 0:
            ok_prev = (idx == 0 or entity[idx - 1] not in prev_excl)
            ok_next = (idx + 1 >= len(entity) or entity[idx + 1] not in next_excl)
            if ok_prev and ok_next:
                return ind

    # ── 层3：兜底关键词计分匹配（保留 _INDUSTRY_KEYWORDS 作 fallback）──
    entity_lower = entity.lower()
    scores: dict[str, int] = {}
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in entity_lower)
        if score > 0:
            scores[industry] = score
    if not scores:
        return ''
    return max(scores, key=scores.get)


def _load_industry_overlay(industry: str) -> str:
    """加载行业 overlay 文件内容。带缓存。行业为空则返回空字符串。"""
    if not industry:
        return ''
    if industry in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[industry]
    overlay_path = INDUSTRY_OVERLAYS_DIR / f'{industry}.md'
    if not overlay_path.exists():
        return ''
    content = overlay_path.read_text(encoding='utf-8')
    _OVERLAY_CACHE[industry] = content
    return content


# 质量线
STEP_QUALITY_THRESHOLD = 3

# Step 角色名
# v3.0 (2026-07-28): 删除 step1_data，数据收集合并到 phase04 research plan
# 下游 step 不再依赖 step1_data，改为读取 research plan 阶段产出的 enriched_data_pack.json
STEP_ROLE = {
    'step1_industry': '投研_主笔_行业分析',
    'step2_biz': '投研_主笔_商业模式',
    'step3_finance': '投研_主笔_财务分析',
    'step4_mgmt': '投研_主笔_管理层',
    'step5_macro': '投研_主笔_宏观分析',
    'step7_insight': '投研_主笔_差异化洞察',
    'step6_valuation': '投研_主笔_预测与估值',
    'step8_risk': '投研_主笔_风险催化',
}

# IR 子代理可调用的数据源 connector（仅限实际可用、已验证的源）
# tyc-mcp: 天眼查（工商/股东/司法/专利/知产）
# westock-mcp: 腾讯自选股（A/HK/美股实时行情/财务/券商研报/板块/产业链/资金流/北向/评级/新闻/选股）
# ima-mcp: IMA 知识库（12万+机构研报/专家纪要/外资研报/行业报告，语义搜索）
# ⚠️ 之前写死为 ['tyc-mcp']，导致 westock 从未授权给子代理，
#   子代理只能用 neodata(bash) + web_search + 天眼查，金融数据源利用率极低。
IR_SUBAGENT_CONNECTOR_IDS = ['tyc-mcp', 'westock-mcp', 'ima-mcp']

# 步间依赖关系
# v3.1 (2026-08-04): 回归大行真实研究链 — 基于 4 篇公司首发研报全文解剖
#（爱建/先锋精科、交银国际/鸣鸣很忙、东方/中国太保、Bernstein/Booking）：
# 估值永远是研究的收口步骤（行业→业务→盈利预测→估值），报告开头的目标价
# 只是"结论前置"的成文技巧，不是研究顺序。旧版 v2.1 把估值放 Wave1 是误读。
# 数据收集在 phase04 research plan 完成，产出 enriched_data_pack.json 供所有 step 引用。
STEP_DEPS = {
    'step1_industry': [],                                        # Wave 1 行业格局（背景层）
    'step2_biz': [],                                             # Wave 1 商业模式（背景层）
    'step5_macro': [],                                           # Wave 1 宏观环境（背景层）
    'step3_finance': ['step1_industry', 'step2_biz'],            # Wave 2 预测假设必须可追溯到行业/业务
    'step4_mgmt': ['step2_biz'],                                 # Wave 2 执行力验证需业务语境
    'step6_valuation': ['step3_finance', 'step1_industry',       # Wave 3 估值收口：消费预测，
                        'step2_biz', 'step5_macro'],             #   行业/业务/宏观供 SOTP/可比/折现率
    'step7_insight': ['step1_industry', 'step2_biz', 'step3_finance',
                      'step4_mgmt', 'step5_macro', 'step6_valuation'],  # Wave 4 全维度综合出预期差
    'step8_risk': ['step3_finance', 'step4_mgmt', 'step5_macro', 'step6_valuation'],
}

# 并行发射波次
# v3.1 (2026-08-04): 研究链 4 波 — 背景（行业+业务+宏观）→ 预测与验证（财务+管理层）
# → 估值收口 → 预期差收口。对标大行首发研报的真实研究顺序。
# anchor / thesis 不是 step——它们在 research_plan（phase04）里已产出，所有 wave 共享读。
LAUNCH_WAVES = [
    ['step1_industry', 'step2_biz', 'step5_macro'],  # Wave 1 背景层（并行）
    ['step3_finance', 'step4_mgmt'],                 # Wave 2 预测与验证（消费 Wave 1）
    ['step6_valuation'],                             # Wave 3 估值收口（消费预测，按公司特征选方法）
    ['step7_insight', 'step8_risk'],                 # Wave 4 预期差收口
    # step8_master 已剥离为独立 synthesis 子代理（phase13）
]

# ── 报告类型分流（v2.1, Batch 3；2026-08-03 修复断点B；2026-08-04 v3.1 按新波次重配）──
# report_type → active_waves（LAUNCH_WAVES 索引白名单，None=全量）。
# ⚠️ 白名单必须是依赖闭包安全的：Wave4(insight/risk) 依赖全部 Wave1-3 产出，
#    短路径只能裁到依赖链的干净前缀（[0] / [0,1] / [0,1,2] / 全量）。
#    白名单外的依赖由 deps_ready(active_steps) 自动降级（改读 enriched_data_pack + 自搜），
#    不会阻塞发射——即大行财报点评模式：只更新模型与目标价，不重做全套背景研究。
#   - deep_dive / company_deep_dive / industry_research : 全量 4 波
#   - event_update / data_track : wave1+2+3（背景+预测更新+估值更新，洞察/风险退到统稿）
#   - earnings_note: wave2+3（只对数字反应：更新预测与目标价，step3 假设降级走数据包+自搜）
REPORT_TYPE_ACTIVE_WAVES: dict[str, list[int] | None] = {
    'deep_dive': None,            # 全量
    'company_deep_dive': None,    # 兼容 research_plan 旧 report_type 取值
    'broker_ir': None,            # 兼容
    'industry_research': None,    # 兼容
    'event_update': [0, 1, 2],    # 事件快报：背景+预测更新+估值更新
    'data_track': [0, 1, 2],      # 数据跟踪，同 event_update
    'earnings_note': [1, 2],      # 财报点评：仅预测+估值（大行模式）
}


def active_waves_for_report_type(report_type: str | None) -> list[int] | None:
    """按 report_type 返回 active_waves 白名单。未知类型回退全量（None）。"""
    if not report_type:
        return None
    return REPORT_TYPE_ACTIVE_WAVES.get(report_type, None)


def active_steps_for_report_type(report_type: str | None) -> set[str] | None:
    """v3.1: 按 report_type 返回本报告类型会实际执行的 step 集合。

    None=全量（所有 step 都执行，无短路径降级）；否则返回白名单波次内的 step 集合。
    deps_ready 用它判定哪些依赖在当前报告类型下"不会执行、自动降级放行"。
    """
    waves = active_waves_for_report_type(report_type)
    if waves is None:
        return None
    steps: set[str] = set()
    for idx in waves:
        if 0 <= idx < len(LAUNCH_WAVES):
            steps.update(LAUNCH_WAVES[idx])
    return steps

# 超时
STEP_TIMEOUTS = {
    'step1_industry': 900,
    'step2_biz': 900,
    'step3_finance': 900,
    'step4_mgmt': 900,
    'step5_macro': 900,
    'step7_insight': 900,
    'step6_valuation': 900,
    'step8_risk': 900,
}

# Step 查询关键词（用于自动补搜）
_STEP_KEYWORDS = {
    'step1_industry': 'industry market size market share growth rate TAM penetration competitive landscape 行业规模 竞争格局',
    'step2_biz': 'business model product revenue customer supply chain 商业模式 产品线 客户 收入结构',
    'step3_finance': 'financial report revenue profit margin cash flow ROE debt 财报 营收 毛利率 净利润 现金流',
    'step4_mgmt': 'management board governance ownership ESG compensation 管理层 董事会 股权结构 治理',
    'step5_macro': 'CPI PMI interest rate LPR GDP inflation monetary policy 宏观 利率 通胀 PMI 社融',
    'step7_insight': 'catalyst valuation target price investment thesis risk-reward 催化剂 估值 目标价 投资亮点',
    'step6_valuation': 'DCF valuation PE PB PS EV/EBITDA target price WACC comparable company valuation model 目标价 估值',
    'step8_risk': 'risk regulatory litigation competition macro threat 风险 监管 诉讼 竞争威胁 宏观',
}

_STEP_QUERY_TEMPLATES = {
    'step1_industry': [
        '"{entity}" industry market size competitive landscape',
        '"{entity}" market share industry report',
        '"{entity}" 行业 竞争格局 市场规模',
    ],
    'step2_biz': [
        '"{entity}" business model revenue segments',
        '"{entity}" products services overview',
        '"{entity}" 商业模式 收入结构 产品',
    ],
    'step3_finance': [
        '"{entity}" financial report revenue profit margin cash flow ROE debt',
        '"{entity}" annual report results announcement revenue profit',
        'site:hkexnews.hk "{entity}" annual report',
        'site:hkexnews.hk "{entity}" results announcement',
        '"{entity}" 财报 营收 毛利率 净利润 现金流',
    ],
    'step4_mgmt': [
        '"{entity}" CEO management team leadership governance',
        '"{entity}" executive changes board ownership',
        '"{entity}" 管理层 董事会 股权结构 治理',
    ],
    'step5_macro': [
        '"{entity}" sector macro impact CPI PMI interest rate',
        'China macro economy GDP inflation monetary policy latest',
        '宏观 利率 通胀 PMI 社融 最新数据',
    ],
    'step7_insight': [
        '"{entity}" investment thesis valuation target price catalyst',
        '"{entity}" analyst report target price catalyst',
        '"{entity}" 投资逻辑 估值 催化剂',
    ],
    'step8_risk': [
        '"{entity}" risks regulatory litigation competition macro',
        '"{entity}" risk analysis report',
        '"{entity}" 风险 监管 诉讼 竞争',
    ],
    'step6_valuation': [
        '"{entity}" DCF valuation target price WACC',
        '"{entity}" comparable company valuation PE PB PS EV/EBITDA',
        '"{entity}" analyst consensus target price',
        '"{entity}" 估值 目标价 可比公司',
    ],
}


# ═══════════════════════════════════════════════════════
# 通知
# ═══════════════════════════════════════════════════════

def notify_wx(text: str) -> bool:
    """Notification stub — messaging removed for open-source release."""
    return False


# ═══════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════

def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return default


def step_output_path(task_id: str, step: str) -> Path:
    return TASKS_DIR / f'{task_id}-{step}.md'


def step_spawn_receipt_path(task_id: str, step: str) -> Path:
    return TASKS_DIR / f'{task_id}-spawn-receipt-{step}.json'


def step_manifest_path(task_id: str, step: str) -> Path:
    """WorkBuddy Task 子代理的 manifest 文件"""
    return TASKS_DIR / f'{task_id}-manifest-{step}.json'


def pipeline_manifest_path(task_id: str) -> Path:
    """整个 pipeline 的 step manifest 汇总"""
    return TASKS_DIR / f'{task_id}-pipeline-manifest.json'


def deps_ready(task_id: str, step: str, active_steps: set[str] | None = None) -> tuple[bool, list[str]]:
    """检查依赖步骤的输出文件是否已存在且完整（>100 bytes）

    v3.1 (2026-08-04): active_steps 用于短路径（报告类型分流）。不在 active_steps 内的
    依赖在当前报告类型下不会执行，自动降级放行（子代理改读 enriched_data_pack.json + 自搜），
    不计入 missing——对标大行财报点评模式：只更新模型与目标价，不重做全套背景研究。
    """
    missing = []
    for dep in STEP_DEPS.get(step, []):
        if active_steps is not None and dep not in active_steps:
            continue  # 短路径降级：该依赖不在本报告类型执行范围内
        p = step_output_path(task_id, dep)
        if not p.exists() or p.stat().st_size < 100:
            missing.append(dep)
    return len(missing) == 0, missing


# ── Instruction Store Cache (module-level, mtime detection) ──
# 对标 Lit 管线的 _load_instruction_prompts() 模式
_INSTRUCTION_INDEX_CACHE: dict = {}
_INSTRUCTION_INDEX_MTIME: float = 0
_INSTRUCTION_ROLE_CACHE: dict[str, tuple[str, float]] = {}  # role_name → (content, mtime)
_PROTOCOL_CACHE: str = ""
_PROTOCOL_MTIME: float = 0


def _load_index() -> dict:
    """Load instruction_store_ir/index.json with mtime cache."""
    global _INSTRUCTION_INDEX_CACHE, _INSTRUCTION_INDEX_MTIME
    index_path = INSTRUCTION_STORE / 'index.json'
    if not index_path.exists():
        return _INSTRUCTION_INDEX_CACHE
    current_mtime = index_path.stat().st_mtime
    if _INSTRUCTION_INDEX_CACHE and current_mtime == _INSTRUCTION_INDEX_MTIME:
        return _INSTRUCTION_INDEX_CACHE
    try:
        _INSTRUCTION_INDEX_CACHE = json.loads(index_path.read_text(encoding='utf-8'))
        _INSTRUCTION_INDEX_MTIME = current_mtime
    except Exception:
        pass
    return _INSTRUCTION_INDEX_CACHE


def _load_role_file(role_name: str) -> str:
    """Load a single role .md file with mtime cache."""
    global _INSTRUCTION_ROLE_CACHE
    role_file = INSTRUCTION_STORE / f'{role_name}.md'
    if not role_file.exists():
        return f'Role instructions for {role_name} not found.'
    current_mtime = role_file.stat().st_mtime
    cached = _INSTRUCTION_ROLE_CACHE.get(role_name)
    if cached and cached[1] == current_mtime:
        return cached[0]
    content = role_file.read_text(encoding='utf-8')
    _INSTRUCTION_ROLE_CACHE[role_name] = (content, current_mtime)
    return content


def load_instruction(role_key: str) -> str:
    """加载角色指令（从 instruction_store_ir/index.json 映射，带 mtime cache）"""
    index = _load_index()
    bindings = index.get('pipeline_bindings', {}).get('ir', {})
    role_name = bindings.get(role_key, role_key)
    return _load_role_file(role_name)


def load_shared_output_protocol() -> str:
    """加载所有 IR 子代理共享的结构化输出协议（带 mtime cache）。"""
    global _PROTOCOL_CACHE, _PROTOCOL_MTIME
    protocol_file = INSTRUCTION_STORE / '_shared_output_protocol.md'
    if not protocol_file.exists():
        return _PROTOCOL_CACHE or (
            '你不是直接写最终研报。请输出结构化 Section Package，并确保关键数字绑定来源与 fact_id。'
        )
    current_mtime = protocol_file.stat().st_mtime
    if _PROTOCOL_CACHE and current_mtime == _PROTOCOL_MTIME:
        return _PROTOCOL_CACHE
    _PROTOCOL_CACHE = protocol_file.read_text(encoding='utf-8')
    _PROTOCOL_MTIME = current_mtime
    return _PROTOCOL_CACHE


def build_step_brief(task_id: str, step: str, entity: str = '', query: str = '') -> str:
    """构建子代理任务 brief"""
    role_key = step
    instruction = load_instruction(role_key)
    shared_protocol = load_shared_output_protocol()
    
    output_path = step_output_path(task_id, step)
    research_plan_path = TASKS_DIR / f'{task_id}-research_plan.json'
    fact_store_path = TASKS_DIR / f'{task_id}-fact_store.json'

    brief_lines = [
        f'# Step Brief: {STEP_ROLE.get(step, step)} ({step})',
        f'',
        f'Task: {task_id}',
        f'Entity: {entity}',
        f'Query: {query}',
        f'',
        f'## ⚠️ CRITICAL: 输出文件路径（必须写入此路径）',
        f'',
        f'**你必须将最终分析报告写入以下文件：**',
        f'',
        f'`{output_path}`',
        f'',
        f'**禁止写入其他路径（如 search-stepX.md、brief-stepX.md 等）。**',
        f'**唯一完成条件：上述文件写入成功。**',
        f'',
        f'## Quality Production Inputs',
        f'',
        f'- Research Plan: `{research_plan_path}`',
        f'- Fact Store: `{fact_store_path}`',
        f'- 你不是直接写最终研报；你要生产可验证、可统稿的结构化研究资产。',
        f'- 如果上述文件不存在，请在 data_gaps 中声明缺口，不得自行编造事实。',
        f'',
        f'## Shared Output Protocol',
        f'',
        shared_protocol,
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
        f'### 补搜工具使用指南',
        f'',
        f'数据源路由以本次派发 prompt 中的「数据源路由（强制）」表为准（含 westock-mcp / NeoData / yfinance / 天眼查 / IMA 研报库 KB ID / search_deep 的完整路由与调用方式）。',
        f'核心原则：结构化源优先——行情/财务/研报/板块/产业链走 westock-mcp（MCP 直调），研报深度走 NeoData doc + IMA 自建研报库，工商/司法走天眼查；search_deep(Bash) 仅作突发新闻和长尾兜底。禁止只用通用搜索做所有搜索。',
        f'',
        f'### ⏰ 数据时效性硬要求（最高优先级，违反即任务失败）',
        f'',
        f'**第零轮搜索（在所有广度搜索之前必须执行，不可跳过，全部用 Bash search_deep）：**',
        f'1. search_deep(Bash, "{entity} {{YYYY}}年{{M}}月 最新动态") — 锁定标的当前状态',
        f'2. search_deep(Bash, "{entity} latest news {{YYYY}}") — 英文视角补充',
        f'3. 如涉及产品/技术: search_deep(Bash, "{{product}} 最新版本 发布 {{YYYY}}") — 锁定当前版本',
        f'',
        f'**搜索 query 必须含时间锚点：**',
        f'- ❌ "腾讯 AI 大模型" → ✅ "腾讯 混元 最新模型 2026年7月"',
        f'- ❌ "优必选 机器人" → ✅ "优必选 超仿真机器人 2026 最新发布"',
        f'- ❌ "行业市场规模" → ✅ "行业市场规模 2025 2026 最新数据"',
        f'',
        f'**引用数据必须标注日期 + 时效等级：**',
        f'- 3 个月内: 正常引用 | 6 个月内: 正常引用 | 6-12 个月: ⚠️ 标注 | >12 个月: ❌ 必须补搜最新版',
        f'',
        f'**产品/技术版本验证（AI/科技公司必查）：**',
        f'- 搜索: "{{product}} latest version release date {{YYYY}}"',
        f'- 确认引用的是最新版本，如有更新版本必须用最新数据',
        f'- 禁止引用已淘汰/被替代的旧版本而不标注',
        f'- 示例: 分析腾讯 AI 时必须搜 "腾讯 混元 最新模型 2026年7月"，如果最新是 HY3 就绝不能引用 HY1',
        f'',
        f'**新闻/动态类搜索：**',
        f'- 查询必须包含当前年月（如 2026年7月）',
        f'- 优先引用最近 30 天的信息',
        f'- 超过 3 个月的新闻需验证是否有更新报道',
        f'- 禁止引用 1 年前的新闻作为"最新动态"',
        f'',
        f'### 搜索深度硬要求（宁滥勿缺）',
        f'',
        f'**原则：宁可多搜、多抓、多引，不可漏搜。泛搜一轮远远不够，必须多角度交叉验证。**',
        f'',
        f'**最低搜索量（质量门禁会校验，不达标 = 任务失败）：**',
        f'- ≥ 8 个独立搜索 query（不同角度：公司名+财务、公司名+行业、公司名+竞品、公司名+风险 等）',
        f'- ≥ 3 个实际深读过的 URL（search_deep(fetch_top_n) 抓到的正文，不是只看 snippet）',
        f'- ≥ 3 个独立来源域名（不能全是同一个站点的页面）',
        f'',
        f'**搜索策略（必须按顺序执行）：**',
        f'0. **第零轮：时效锚定（最先执行，不可跳过）**',
        f'   - 腾讯新闻（Bash 调用）:',
        f'     ```bash',
        f'     cd ~/.workbuddy/ir_runtime && python3 -c "',
        f'     import json, sys; sys.path.insert(0, \'.\')',
        f'     from scripts.search_gateway import tencent_news_search',
        f'     result = tencent_news_search(\'{entity} 最新动态\', max_results=5)',
        f'     print(json.dumps(result, ensure_ascii=False, indent=2))',
        f'     "',
        f'     ```',
        f'   - NeoData doc（Bash 调用）:',
        f'     ```bash',
        f'     cd ~/.workbuddy/ir_runtime && python3 -c "',
        f'     import json, sys; sys.path.insert(0, \'.\')',
        f'     from scripts.search_gateway import neodata_search',
        f'     result = neodata_search(\'{entity} 最新动态\', data_type=\'doc\')',
        f'     print(json.dumps(result, ensure_ascii=False, indent=2))',
        f'     "',
        f'     ```',
        f'   - search_deep(Bash): "{entity} {{当前年月}} 最新动态" — 锁定英文源和长尾信息',
        f'   - 如涉及产品/技术: search_deep(Bash, "{{product}} 最新版本 发布 {{YYYY}}") — 锁定当前版本',
        f'   - 目的: 先知道"最新"是什么，后续分析才不会引用过期信息',
        f'   - 三层组合: 腾讯新闻(分钟级) → NeoData doc(深度) → search_deep(兜底)',
        f'1. **第一轮：广度扫描** — NeoData doc 拿研报/分析 + Bash 调 search_gateway prefer=multi 多关键词并行',
        f'2. **第二轮：深度验证** — 对第一轮发现的关键 claim，用 search_deep(Bash, fetch_top_n) 读全文验证',
        f'3. **第三轮：交叉验证/反证** — 搜竞品对比、负面信息、行业报告、分析师观点',
        f'4. **TYC 天眼查必查项**（如标的涉及中国大陆企业）：工商信息、司法诉讼、专利、资质',
        f'5. **金融数据必查**：NeoData api（Bash） → yfinance（Bash） 交叉验证',
        f'',
        f'**输出必须包含搜索审计（search_audit）：**',
        f'在你的 Markdown 输出末尾加一个 `## 搜索审计` 章节，记录：',
        f'- `queries`：你实际执行的所有搜索词（≥8 个）',
        f'- `fetched_urls`：你实际深读过正文的 URL（≥3 个）',
        f'- `source_domains`：所有引用来源的独立域名（≥3 个）',
        f'',
        f'### 补搜纪律',
        f'- 最多补搜 3 轮，避免无限循环；但 3 轮是上限不是目标——每轮必须有效',
        f'- 补搜结果必须标注来源 URL',
        f'- 仍搜不到的标注"经 X 次搜索未找到独立来源"',
        f'- 禁止只用通用搜索做所有搜索——它没有 NeoData 金融数据和 TYC（天眼查）结构化数据',
        f'- 禁止只搜一轮就结束——泛搜一轮不够，必须多角度验证',
        f'- 禁止只搜不读——搜到 URL 后必须用 search_deep(Bash, fetch_top_n) 读正文提取事实',
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
    
    # Extraction results (phase15 产出)
    extraction_dir = TASKS_DIR / f'{task_id}_body_content'
    extraction_facts = extraction_dir / 'ir_extracted_facts.json'
    if extraction_facts.exists():
        brief_lines.append(f'')
        brief_lines.append(f'## URL Content Extraction Results')
        brief_lines.append(f'')
        brief_lines.append(f'提取事实文件: `{extraction_facts}`')
        brief_lines.append(f'提取内容目录: `{extraction_dir}`')
        brief_lines.append(f'请读取提取事实文件获取预提取的 URL 内容（年报、公告、行业报告等）。')
    
    # Company verification results (phase05 产出)
    verify_path = TASKS_DIR / f'{task_id}-ir_company_verify.json'
    if verify_path.exists():
        brief_lines.append(f'')
        brief_lines.append(f'## Company Verification Data')
        brief_lines.append(f'')
        brief_lines.append(f'文件路径: `{verify_path}`')
        brief_lines.append(f'请读取此文件获取公司验证和估值数据（PE/PB/市值等）。')

    # Industry KPI checklist — 对 step1~step4 注入行业特定 KPI 指引
    kpi_checklist_path = REFS_DIR / 'industry-kpi-checklist.md'
    if step in ('step1_industry', 'step2_biz', 'step3_finance') and kpi_checklist_path.exists():
        brief_lines.append(f'')
        brief_lines.append(f'## Industry-Specific KPI Checklist')
        brief_lines.append(f'')
        brief_lines.append(f'请读取行业KPI清单文件: `{kpi_checklist_path}`')
        brief_lines.append(f'根据标的所属行业选择对应的KPI清单，将这些指标作为数据采集和分析的必查项。')
        brief_lines.append(f'如果某KPI数据无法获取，标注"公司未披露[X指标]"。')
    
    # Prior steps
    for dep in STEP_DEPS.get(step, []):
        dep_path = step_output_path(task_id, dep)
        if dep_path.exists():
            brief_lines.append(f'')
            brief_lines.append(f'## Prior Step Output: {dep}')
            brief_lines.append(f'')
            brief_lines.append(f'完整输出文件路径：`{dep_path}`')
            brief_lines.append(f'请使用 Read 工具读取该文件的完整内容（不要依赖摘要，必须读原文）。')
    
    return '\n'.join(brief_lines)


def build_step_prompt(step: str, entity: str, market: str = 'us') -> str:
    """构建给 WorkBuddy Task 子代理的系统级提示词 — v2: 按角色加入专属验证规则"""
    role_name = STEP_ROLE.get(step, step)

    # 通用基础指令
    base = (
        f"You are an expert investment research analyst specializing in {role_name}. "
        f"You are working on step '{step}' of an investment research pipeline for '{entity}' (market: {market}). "
        f"Your output must be in Markdown format, well-structured with multiple sections (## headers), "
        f"include at least 3 source citations (URLs), and contain substantive analysis (minimum 3000 characters). "
        f"Write your analysis directly — do not include meta-commentary about the task itself. "
        f"If you cannot find specific data, SUPPLEMENTARY SEARCH FIRST before writing '未找到独立外部证据'. "
        f"Use thinking=high — reason carefully before writing each section.\n\n"
        f"CRITICAL: You must autonomously close the loop. When you discover data gaps during analysis:\n"
        f"1. Search for the missing data yourself (Bash: search_gateway → yfinance → search_deep(Bash))\n"
        f"2. Integrate the found data into your analysis\n"
        f"3. Only mark as '待核实' after 3 rounds of supplementary search still yield nothing\n"
        f"Do NOT return to the coordinator for search instructions — you ARE the search agent.\n\n"
        f"DATA SOURCE PRIORITY (all via Bash `cd ~/.workbuddy/ir_runtime && python3 -c ...`, see tool guide):\n"
        f"- A/HK financials: NeoData api (Bash) → yfinance (Bash) → search_deep(Bash)\n"
        f"- US stocks: yfinance (Bash) → NeoData (Bash) → search_deep(Bash)\n"
        f"- News/analysis/reports: NeoData doc (深度分析, 首选!) → search_deep(Bash) (突发新闻补充)\n"
        f"- Product/tech launches: NeoData doc + search_deep(Bash) (必须含当前年月)\n"
        f"- Company registry/legal: TYC MCP (已在 manifest 配置) → search_deep(Bash)\n"
        f"- Tencent News (Bash: tencent_news_search): real-time Chinese news\n"
        f"- search_gateway (Bash) auto-routes financial queries to NeoData Layer 0\n\n"
        f"TEMPORAL ANCHORING (CRITICAL):\n"
        f"- Step 0: NeoData doc search '{entity} 最新动态' + search_deep(Bash, '{entity} YYYY年M月 最新动态')\n"
        f"- ALL search queries MUST include current year/month\n"
        f"- Product/tech version: search '{{product}} latest version YYYY' before citing\n"
        f"- Data >12 months old → mark ❌ and search for latest replacement\n"
        f"- NEVER cite an outdated product/model version when a newer one exists\n\n"
        f"- Required fields coverage ≥ 70%\n"
        f"- ≥ 3 independent sources\n"
        f"- ≥ 3 ## level sections\n"
        f"- Content length ≥ 3000 chars\n"
        f"If self-check fails, do more research before outputting.\n\n"
        f"INVESTMENT-IMPLICATION MANDATE (CRITICAL): This is a BUY-SIDE research report for internal "
        f"investment decision-making, NOT a sell-side broker note. You are NOT writing a descriptive "
        f"data dossier. Every analytical section MUST state its investment implication — how the finding "
        f"affects the investment thesis, valuation, or risk assessment (bull/bear impact, conviction level, "
        f"what would change the view). Prioritize decision-relevant insight over exhaustive description.\n\n"
    )

    # 角色专属 ANTI-DEFECT RULES
    step_rules = {
        'step1_industry': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPETITOR STATUS VERIFICATION: For every competitor listed, search-verify their current '
            'financing/IPO status. A competitor marked as "private, B轮" may have since IPO\'d. '
            'Update status and note date of verification.\n'
            '2. INDUSTRY REPORT CURRENCY: When citing market size data, verify you are using the LATEST '
            'edition of the report. Search "{report} {year} latest edition" before citing.\n'
            '3. REGULATORY STATUS: For regulated industries, verify current policy status before citing '
            'policy-driven market assumptions. Search "{policy} 现行 有效 最新政策".\n'
            '4. PRODUCT/TECH CURRENCY (AI/TECH COMPANIES): When analyzing products, models, '
            'or technologies, ALWAYS verify you are referencing the LATEST version. '
            'Search "{product} latest version {year}" and "{product} release date". '
            'Use NeoData doc for depth analysis + search_deep(Bash) with current year/month for breaking news. '
            'If a newer version exists, use the newer data. '
            'NEVER cite an older model/version when a newer one has been released.\n'
        ),
        'step2_biz': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPETITOR MOAT VERIFICATION: When scoring competitor moat dimensions, each score must be '
            'based on SEARCH-VERIFIED current data, not model training data. A competitor\'s capability '
            'may have changed significantly since training cutoff.\n'
            '2. PRODUCT LINE CURRENCY: For each product/service mentioned, verify it is '
            'currently active and the latest iteration. Use NeoData doc for depth + '
            'search_deep(Bash, "{product} latest {year}") for breaking news. '
            'Deprecated or superseded products must be noted as such.\n'
        ),
        'step3_finance': (
            'ANTI-DEFECT RULES:\n'
            '1. LATEST FILING VERIFICATION: Before citing annual report data, verify it is the LATEST filing. '
            'Search "{company} 最新年报 {year}" and check HKEX/SEC for recent filings. '
            'If a newer report exists, use the newer data.\n'
            '2. AUDIT OPINION CHECK: Note the audit opinion for each year cited. A change in audit opinion '
            '(e.g., from "unqualified" to "qualified") is a significant red flag that must be highlighted.\n'
        ),
        'step4_mgmt': (
            'ANTI-DEFECT RULES:\n'
            '1. PERSON EXISTENCE VERIFICATION (CRITICAL): EVERY person name mentioned in the management '
            'team section MUST be verified to actually exist at this company. Search "{person name} '
            '{company} 高管/董事/管理层" to confirm. If no independent source confirms this person\'s '
            'association with the company after 2 searches, write "⚠ 该人员信息未经独立来源验证". '
            'NEVER fabricate person names from model training data — this is the HIGHEST RISK area '
            'for data fabrication in this step.\n'
            '2. MANAGEMENT CURRENCY: Management team data from annual reports may be outdated (CEO changes, '
            'director resignations). Search "{company} 管理层变动 CEO变更 {year}" for recent changes.\n'
            '3. MANAGEMENT LEGAL STATUS: For key management members, search for recent legal/regulatory '
            'issues: "{person name} 处罚 调查 诉讼". Recent issues are material to governance assessment.\n'
        ),
        'step7_insight': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPETITOR DATA CURRENCY: When citing competitor data from prior steps, verify it is current. '
            'If prior steps used stale competitor data, note this as a limitation.\n'
            '2. PRODUCT/TECH CATALYST CURRENCY: When identifying investment catalysts related to '
            'product launches or tech milestones, verify you have the LATEST product/version info. '
            'Use NeoData doc for depth + search_deep(Bash) with current year/month. '
            'A catalyst based on outdated product info (e.g. citing model v1 when v3 exists) '
            'invalidates the entire investment thesis.\n'
        ),
        'step6_valuation': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPARABLE COMPANY STATUS VERIFICATION (CRITICAL): For EVERY comparable company in the '
            'comps table, search-verify their CURRENT status: (a) If currently listed: use yfinance to '
            'verify ticker is active, pull latest market cap/PE/PS. (b) If currently private: search '
            'IT桔子/36氪/天眼查 for latest round and date. CRITICAL: check whether they have IPO\'d '
            'SINCE the last private valuation you found. (c) If delisted/privatized: note date and last '
            'available valuation. (d) If acquired: note acquisition price — this IS a valuation data point. '
            'Status column format: "上市公司(代码) 市值X亿" or "未上市 X轮 金额(日期)" or '
            '"已IPO(代码) 市值X亿" or "已收购 价格X亿(日期)". NEVER assume private companies '
            'remain private without verification. This is the #1 cause of valuation errors.\n'
            '2. VALUATION DATA TIMELINESS: All financial data (revenue, PE, PS, etc.) must be verified '
            'as current within 6 months. Data >12 months old must be labeled with ⚠ warning.\n'
        ),
        'step8_risk': (
            'ANTI-DEFECT RULES:\n'
            '1. REGULATORY STATUS CURRENCY: Every regulatory risk cited must be search-verified for CURRENT '
            'status. A regulation described as "即将出台" in older sources may have been enacted, revised, '
            'or shelved. Search "{regulation} 最新 现行 有效 {year}" before citing.\n'
            '2. COMPETITOR COMPLIANCE EVENTS: For competition-related risks, search whether major competitors '
            'have recent regulatory penalties — this may reduce competitive pressure on the target.\n'
        ),
        'step5_macro': (
            'ANTI-DEFECT RULES:\n'
            '1. MACRO DATA TIMELINESS: Every macro indicator cited (CPI, PMI, LPR, GDP, etc.) must have '
            'a publication date. Indicators older than 60 days must be marked with "⚠ 数据滞后 X 天". '
            'Search "{indicator} 最新 {year}" to verify you have the latest release.\n'
            '2. POLICY STATUS CURRENCY: When citing monetary/fiscal policy, verify it is CURRENT. '
            'Search "{policy} 现行 最新 {year}" to confirm. Policies announced but not yet implemented '
            'must be labeled as "待实施".\n'
            '3. CROSS-MARKET IMPACT: For A/HK stocks, analyze BOTH China domestic macro AND global/'
            'US macro spillover effects (Fed rates, USD/CNY). Do not analyze only one dimension.\n'
            '4. IMPACT PATHWAY: Every macro judgment must include a concrete transmission mechanism '
            'to the target company\'s sector. "利好" without an impact pathway is insufficient — '
            'explain HOW (e.g., "降息→融资成本下降→资本密集型行业受益").\n'
            '5. CONFIDENCE CALIBRATION: If a key indicator (e.g. PMI) is from a single source with no '
            'cross-verification, set confidence to "medium" max. "high" requires ≥2 independent sources.\n'
        ),
    }

    rules = step_rules.get(step, '')
    prompt = base + rules if rules else base

    # 拼接 _common_tool_guide.md（对标 BP/Lit 管线的 instruction store 模式）
    tool_guide_path = INSTRUCTION_STORE / '_common_tool_guide.md'
    if tool_guide_path.exists():
        tool_guide = tool_guide_path.read_text(encoding='utf-8')
        # 替换占位符
        tool_guide = tool_guide.replace('{RUNTIME_ROOT}', str(ROOT))
        tool_guide = tool_guide.replace('{TASK_DIR}', str(TASKS_DIR))
        prompt = prompt + '\n\n' + tool_guide

    # ── 行业 Overlay（轻量版 archetype，v2.0 新增）──
    industry = _infer_ir_industry(entity)
    overlay = _load_industry_overlay(industry)
    if overlay:
        prompt = prompt + '\n\n# INDUSTRY-SPECIFIC ANALYSIS FRAMEWORK\n\n' + overlay

    return prompt


# ═══════════════════════════════════════════════════════
# 核心：子代理发射（WorkBuddy 版 v3 — Task 子代理）
# ═══════════════════════════════════════════════════════

def launch_step(task_id: str, step: str, entity: str = '', query: str = '',
                timeout: int = 900, dry_run: bool = False, market: str = 'us') -> dict:
    """启动单个子代理 step — WorkBuddy 版 v3。
    
    发射器只负责：
    1. 构建 brief 并写入文件
    2. 写入 manifest（给 WorkBuddy 主 AI 读取用）
    3. 写入 spawn receipt（让 execution-loop 知道 step 已发射）
    
    实际的 LLM 推理由主 AI 通过 WorkBuddy Task 子代理完成。
    """
    output_path = step_output_path(task_id, step)
    receipt_path = step_spawn_receipt_path(task_id, step)
    manifest = step_manifest_path(task_id, step)

    research_plan_gate = ensure_research_plan_ready(task_id, entity, query, market)
    if not research_plan_gate['ready']:
        return {
            'step': step,
            'status': 'blocked',
            'reason': 'research_plan_not_ready',
            'research_plan_gate': research_plan_gate,
        }

    # v3.1: 从 research_plan 读 report_type，推导本报告类型实际执行的 step 集合，
    # 让 deps_ready 对"不会执行的依赖"自动降级放行（短路径财报点评模式）。
    _plan = load_research_plan(task_id, TASKS_DIR) or {}
    _active_steps = active_steps_for_report_type(_plan.get('report_type'))

    # 检查依赖
    ready, missing = deps_ready(task_id, step, active_steps=_active_steps)
    if not ready:
        return {
            'step': step,
            'status': 'blocked',
            'reason': f'Dependencies not ready: {missing}',
        }

    # 构建 brief
    brief = build_step_brief(task_id, step, entity, query)
    brief_path = TASKS_DIR / f'{task_id}-brief-{step}.md'
    brief_path.write_text(brief, encoding='utf-8')

    if dry_run:
        return {
            'step': step,
            'status': 'dry_run',
            'brief_path': str(brief_path),
            'output_path': str(output_path),
            'manifest_path': str(manifest),
        }

    # 清理旧输出
    for p in (receipt_path, manifest):
        if p.exists():
            p.unlink()

    # ─── 写入 manifest（WorkBuddy Task 子代理的任务描述）───
    role_name = STEP_ROLE.get(step, step)
    system_prompt = build_step_prompt(step, entity, market)
    
    manifest_data = {
        'task_id': task_id,
        'step': step,
        'role': role_name,
        'entity': entity,
        'query': query,
        'market': market,
        'system_prompt': system_prompt,
        'brief_path': str(brief_path),
        'output_path': str(output_path),
        'timeout': timeout,
        'thinking': 'high',
        'connectorIds': IR_SUBAGENT_CONNECTOR_IDS,  # 天眼查 + 腾讯自选股（已授权 MCP 数据源）
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'status': 'pending',  # pending → running → completed/failed
    }
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')

    # ─── 写入 spawn receipt（兼容原格式，确保 execution-loop 无缝衔接）───
    label = f'{task_id}-{step}'
    receipt = {
        'task_id': task_id,
        'step': step,
        'hook': step,
        'label': label,
        'status': 'dispatched',  # dispatched = 已派发，等待子代理完成
        'runId': f'wb-task-{int(time.time())}',
        'childSessionKey': f'wb-{task_id}-{step}',
        'runtime': 'workbuddy-task',
        'thinking': 'high',
        'manifest_path': str(manifest),
        'output_path': str(output_path),
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"  📋 已派发 {role_name} ({step}) → manifest: {manifest.name}")

    return {
        'step': step,
        'status': 'dispatched',  # dispatched = 等待 WorkBuddy Task 子代理执行
        'label': label,
        'childSessionKey': receipt['childSessionKey'],
        'runId': receipt['runId'],
        'thinking': 'high',
        'brief_path': str(brief_path),
        'output_path': str(output_path),
        'receipt_path': str(receipt_path),
        'manifest_path': str(manifest),
    }


def wait_for_output(task_id: str, step: str, timeout: int = 900, poll_interval: int = 15) -> dict:
    """等待 step 输出文件出现。
    
    WorkBuddy Task 子代理完成分析后会写入 output_path。
    主 AI 在派发 Task 子代理后应轮询此函数来检查输出。
    """
    output_path = step_output_path(task_id, step)
    start = time.time()
    while time.time() - start < timeout:
        if output_path.exists() and output_path.stat().st_size > 100:
            return {
                'step': step,
                'status': 'completed',
                'output_path': str(output_path),
                'output_size': output_path.stat().st_size,
                'elapsed_s': int(time.time() - start),
            }
        time.sleep(poll_interval)
    return {
        'step': step,
        'status': 'timeout',
        'timeout_s': timeout,
        'elapsed_s': int(time.time() - start),
    }


# ═══════════════════════════════════════════════════════
# 质量门控 + 补搜
# ═══════════════════════════════════════════════════════

def _parse_search_audit(text: str) -> dict:
    """从 Markdown 输出中解析搜索审计章节。
    
    期望格式：
    ## 搜索审计
    - queries: [...]
    - fetched_urls: [...]
    - source_domains: [...]
    """
    import re as _re
    audit: dict = {'queries': [], 'fetched_urls': [], 'source_domains': []}
    
    # 找搜索审计章节
    pattern = _re.compile(
        r'##\s*搜索审计\s*\n(.*?)(?=\n##\s|\Z)',
        _re.DOTALL | _re.IGNORECASE
    )
    m = pattern.search(text)
    if not m:
        return audit
    
    block = m.group(1)
    
    # 解析 queries
    qm = _re.search(r'queries?\s*[:：]\s*\n?((?:\s*[-*]\s*.+\n?)+)', block, _re.IGNORECASE)
    if qm:
        audit['queries'] = _re.findall(r'[-*]\s*(.+)', qm.group(1))
    
    # 解析 fetched_urls
    fm = _re.search(r'fetched[_ ]?urls?\s*[:：]\s*\n?((?:\s*[-*]\s*.+\n?)+)', block, _re.IGNORECASE)
    if fm:
        audit['fetched_urls'] = _re.findall(r'[-*]\s*(.+)', fm.group(1))
    
    # 解析 source_domains
    dm = _re.search(r'source[_ ]?domains?\s*[:：]\s*\n?((?:\s*[-*]\s*.+\n?)+)', block, _re.IGNORECASE)
    if dm:
        audit['source_domains'] = _re.findall(r'[-*]\s*(.+)', dm.group(1))
    
    return audit


def _check_step_quality(task_id: str, step: str) -> dict:
    """单 step 质量评估 (0-5 分) — 含搜索深度门禁"""
    output_path = step_output_path(task_id, step)
    if not output_path.exists():
        return {'score': 0, 'verdict': 'fail', 'issues': ['output file missing']}
    
    text = output_path.read_text(encoding='utf-8')
    content_len = len(text)
    urls = text.count('http')
    sections = text.count('## ')
    
    score = 0
    issues = []
    
    # ── 内容量评分 ──
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
    
    # ── 搜索深度门禁（对齐 BP 管线 bp_section_package.v2 的 search_audit 标准）──
    search_audit_issues = []
    audit = _parse_search_audit(text)
    n_queries = len(audit['queries'])
    n_fetched = len(audit['fetched_urls'])
    n_domains = len(set(audit['source_domains']))

    if n_queries < 8:
        search_audit_issues.append(f'search_audit.queries={n_queries} (需≥8)')
    if n_fetched < 3:
        search_audit_issues.append(f'search_audit.fetched_urls={n_fetched} (需≥3)')
    if n_domains < 3:
        search_audit_issues.append(f'search_audit.source_domains={n_domains} (需≥3独立域名)')

    if search_audit_issues:
        score = max(0, score - 1)
        issues.extend(search_audit_issues)

    threshold = STEP_QUALITY_THRESHOLD

    return {
        'score': score,
        'content_length': content_len,
        'url_count': urls,
        'section_count': sections,
        'search_audit': {
            'queries': len(audit['queries']),
            'fetched_urls': len(audit['fetched_urls']),
            'source_domains': len(set(audit['source_domains'])),
        },
        'threshold': threshold,
        'verdict': 'pass' if score >= threshold else 'fail',
        'issues': issues,
    }


def _do_targeted_search(entity: str, step: str, market: str = 'us') -> str:
    """针对某个 step 做补搜，统一走 scripts.search_gateway.search。"""
    templates = _STEP_QUERY_TEMPLATES.get(step, [])
    if not templates:
        kw = _STEP_KEYWORDS.get(step, '')
        if not kw:
            return ''
        templates = [f'"{{entity}}" {kw}']

    memo_lines = []
    seen_urls: set[str] = set()

    try:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from search_gateway import search as gateway_search

        collected = []
        for template in templates[:5]:
            query = template.format(entity=entity).strip()
            rows = gateway_search(query, max_results=5, timeout=20)
            for row in rows:
                url = row.get('url', '') or ''
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                collected.append((query, row))

        if collected:
            memo_lines.append(f"## SearchGateway 补搜结果 ({len(collected)} 条)\n\n")
            for i, (query, row) in enumerate(collected[:12], 1):
                title = row.get('title', '') or ''
                url = row.get('url', '') or ''
                snippet = row.get('content', '') or row.get('snippet', '') or ''
                engine = row.get('engine', '?')
                memo_lines.append(f"### {i}. [{engine}] {title}\n")
                memo_lines.append(f"Query: {query}\n")
                memo_lines.append(f"URL: {url}\n")
                memo_lines.append(f"{snippet[:300]}\n\n")
    except Exception as exc:
        print(f"    ⚠ SearchGateway 补搜异常: {exc}")

    return '\n'.join(memo_lines)


def _rewrite_step(task_id: str, step: str, entity: str, query: str,
                  quality: dict, market: str = 'us', timeout: int = 900) -> dict:
    """质量不达标 → 补搜 + 重写。"""
    step_name = STEP_ROLE.get(step, step)

    # 1. 补搜
    print(f"  🔍 补搜 ({step_name})...")
    memo = _do_targeted_search(entity, step, market)

    memo_path = TASKS_DIR / f'{task_id}-{step}-followup-research.md'
    if memo:
        memo_path.write_text(memo, encoding='utf-8')
        print(f"  📝 补搜结果已写入 {memo_path.name}")
    else:
        print(f"  ⚠ 补搜无结果，用已有内容重写")

    # 2. 重新写 brief
    brief = build_step_brief(task_id, step, entity, query)
    brief_path = TASKS_DIR / f'{task_id}-brief-{step}.md'
    
    rewrite_brief = brief
    if memo_path.exists():
        rewrite_brief += f'\n\n## 补充搜索笔记\n- 文件: `{memo_path}`\n- 必读其中内容\n'
    brief_path.write_text(rewrite_brief, encoding='utf-8')

    # 3. 清理旧输出
    output_path = step_output_path(task_id, step)
    receipt_path = step_spawn_receipt_path(task_id, step)
    for p in (output_path, receipt_path):
        if p.exists():
            p.unlink()

    # 4. Re-dispatch
    step_info = launch_step(task_id, step, entity, query, timeout=timeout, dry_run=False, market=market)
    if step_info.get('status') not in ('dispatched', 'spawned'):
        return {'status': 'rewrite_dispatch_failed', 'error': (step_info.get('error', '') or '')[:500]}

    return {
        'status': 'rewrite_dispatched',
        'manifest_path': step_info.get('manifest_path', ''),
        'output_path': str(output_path),
    }


MAX_SPAWN_RETRIES = 2


def launch_and_verify(task_id: str, step: str, entity: str = '', query: str = '',
                      timeout: int = 900, market: str = 'us', retries: int = 1) -> dict:
    """完整流程：发射 → 等待输出 → 超时补发 → 质检 → 补搜重写
    
    注意：在 WorkBuddy Task 模式下，此函数只做发射 + 写 manifest。
    等待输出和质检需要主 AI 在 Task 子代理完成后调用 check_step_quality()。
    """
    results = []

    # 发射
    launch_result = launch_step(task_id, step, entity, query, timeout, market=market)
    results.append(launch_result)

    if launch_result.get('status') in ('blocked', 'spawn_failed'):
        return {
            'step': step,
            'status': launch_result.get('status'),
            'steps': results,
            'error': launch_result.get('error') or launch_result.get('reason', ''),
        }

    # WorkBuddy Task 模式：发射即返回，主 AI 负责等待和质检
    return {
        'step': step,
        'status': 'dispatched',
        'manifest_path': launch_result.get('manifest_path', ''),
        'output_path': str(step_output_path(task_id, step)),
        'steps': results,
    }


def check_step_quality(task_id: str, step: str) -> dict:
    """检查 step 输出质量（供主 AI 在 Task 子代理完成后调用）"""
    return _check_step_quality(task_id, step)


def do_supplementary_search(entity: str, step: str, task_id: str, market: str = 'us') -> dict:
    """执行补搜（供主 AI 在质量不达标时调用）"""
    memo = _do_targeted_search(entity, step, market)
    memo_path = TASKS_DIR / f'{task_id}-{step}-followup-research.md'
    if memo:
        memo_path.write_text(memo, encoding='utf-8')
    return {
        'step': step,
        'memo_path': str(memo_path) if memo else '',
        'has_results': bool(memo),
    }


def dispatch_rewrite(task_id: str, step: str, entity: str, query: str, market: str = 'us') -> dict:
    """重新派发 step（补搜后重写，供主 AI 调用）"""
    # 清理旧输出
    output_path = step_output_path(task_id, step)
    receipt_path = step_spawn_receipt_path(task_id, step)
    for p in (output_path, receipt_path):
        if p.exists():
            p.unlink()
    return launch_step(task_id, step, entity, query, dry_run=False, market=market)


def launch_wave(task_id: str, steps: list[str], entity: str, query: str, market: str) -> dict:
    """并行发射一组无依赖关系的 step。"""
    results = []
    
    for step in steps:
        result = launch_and_verify(task_id, step, entity, query, STEP_TIMEOUTS.get(step, 600), market)
        results.append(result)

    step_map = {r['step']: r for r in results if 'step' in r}
    ordered_results = [step_map[s] for s in steps if s in step_map]
    
    return {
        'results': ordered_results,
    }


def launch_all(task_id: str, entity: str = '', query: str = '', dry_run: bool = False, market: str = 'us') -> dict:
    """按依赖拓扑并行发射所有 step — 4 波次。

    ⚠️ 仅适用于 DashScope 直调模式（同步等待每个 step 完成）。
    WorkBuddy Task 模式下请使用 launch_next_wave() 循环，因为 Task 子代理
    是异步的，launch_step() 只写 manifest 就返回，后续 wave 的依赖检查必然失败。
    """
    import warnings
    warnings.warn(
        "launch_all() 在 WorkBuddy Task 模式下无法正确推进 wave 2-4，"
        "请改用 launch_next_wave() 循环。",
        DeprecationWarning,
        stacklevel=2,
    )
    all_results = []
    all_manifests = []
    
    for wave_idx, wave_steps in enumerate(LAUNCH_WAVES):
        print(f"\n{'=' * 50}")
        print(f"🌊 Wave {wave_idx + 1}: {', '.join(wave_steps)}")
        print(f"{'=' * 50}")
        
        if dry_run:
            for step in wave_steps:
                result = launch_step(task_id, step, entity, query, STEP_TIMEOUTS.get(step, 600), dry_run=True)
                all_results.append(result)
            continue
        
        wave_result = launch_wave(task_id, wave_steps, entity, query, market)
        all_results.extend(wave_result['results'])
        
        # 收集 manifest 路径
        for r in wave_result['results']:
            if r.get('manifest_path'):
                all_manifests.append(r['manifest_path'])
    
    # 写入 pipeline manifest 汇总
    pipeline_manifest = {
        'task_id': task_id,
        'entity': entity,
        'query': query,
        'market': market,
        'mode': 'workbuddy-task',
        'runtime': 'workbuddy-task',
        'dry_run': dry_run,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'steps': all_results,
        'manifest_files': all_manifests,
        'total_steps_dispatched': sum(1 for r in all_results if r.get('status') in ('dispatched', 'spawned')),
    }
    pipeline_manifest_path(task_id).write_text(
        json.dumps(pipeline_manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    
    result = {
        **pipeline_manifest,
        'pipeline_manifest_path': str(pipeline_manifest_path(task_id)),
    }
    
    # 通知 — 已移除（开源发布版本不含消息推送）
    
    return result


def get_current_wave_index(task_id: str, active_waves: list[int] | None = None) -> int:
    """根据已完成的 step 输出文件推算当前应该发射的 wave 索引。

    active_waves: 报告类型分流（v2.1）。None=全部 wave；指定时只在这些 wave 中推进，
    全部完成返回 len(LAUNCH_WAVES)。用于 earnings_note/data_track 等裁剪场景。
    """
    candidates = active_waves if active_waves is not None else range(len(LAUNCH_WAVES))
    for idx in candidates:
        wave_steps = LAUNCH_WAVES[idx]
        for step in wave_steps:
            out = step_output_path(task_id, step)
            if not out.exists() or out.stat().st_size < 100:
                return idx
    return len(LAUNCH_WAVES)  # 全部完成


def ensure_research_plan_ready(task_id: str, entity: str = '', query: str = '', market: str = 'us') -> dict:
    """Prepare and validate the dispatch-time Research Plan gate."""
    plan = load_research_plan(task_id, TASKS_DIR)
    if plan is None:
        prepare_research_plan(
            task_id=task_id,
            entity=entity,
            query=query,
            market=market,
            tasks_dir=TASKS_DIR,
            report_type='industry_research' if ('行业' in query or '赛道' in query) else 'company_deep_dive',
        )
        plan = load_research_plan(task_id, TASKS_DIR)
    plan = normalize_research_plan_contract(plan or {})
    validation = validate_research_plan_ready(plan)
    if plan:
        research_plan_path(task_id, TASKS_DIR).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
    return {
        'ready': validation['ready'],
        'errors': validation['errors'],
        'plan_path': str(TASKS_DIR / f'{task_id}-research_plan.json'),
        'plan_status': plan.get('plan_status', ''),
    }


def get_pipeline_status(task_id: str) -> dict:
    """返回整个管线当前状态快照。

    v3.1: 感知报告类型分流——按 research_plan.report_type 计算本报告类型
    应执行的 step 集合（active_steps）。短路径下未列入白名单的 step 标记
    为 'skipped'（不参与完成度计数），all_steps_done 仅统计应执行集合。
    """
    _plan = load_research_plan(task_id, TASKS_DIR) or {}
    _active_steps = active_steps_for_report_type(_plan.get('report_type'))

    steps_status = {}
    for step in STEP_DEPS:
        if _active_steps is not None and step not in _active_steps:
            steps_status[step] = 'skipped'
            continue
        out = step_output_path(task_id, step)
        if out.exists() and out.stat().st_size >= 100:
            steps_status[step] = 'completed'
        else:
            ready, missing = deps_ready(task_id, step, active_steps=_active_steps)
            steps_status[step] = 'ready' if ready else f'blocked_by:{",".join(missing)}'

    _expected = _active_steps if _active_steps is not None else set(STEP_DEPS)
    expected_done = all(
        (step_output_path(task_id, s).exists()
         and step_output_path(task_id, s).stat().st_size >= 100)
        for s in _expected
    )
    wave_idx = get_current_wave_index(task_id)
    return {
        'task_id': task_id,
        'steps': steps_status,
        'current_wave': wave_idx if not expected_done else 'all_done',
        'total_waves': len(LAUNCH_WAVES),
        'completed_count': sum(1 for v in steps_status.values() if v == 'completed'),
        'total_steps': len(_expected),
        'expected_steps': sorted(_expected),
        'all_steps_done': expected_done,
        'next_action': 'finalize' if expected_done else f'launch_wave_{wave_idx}',
    }


def launch_next_wave(task_id: str, entity: str = '', query: str = '', market: str = 'us',
                     sequential: bool = False,
                     active_waves: list[int] | None = None) -> dict:
    """发射当前应该执行的 wave。主 AI 每轮调用一次，直到所有 wave 完成。

    sequential=True: 每次只发射当前 wave 的一个 step，返回 has_more 标志。
    主 AI 应循环调用 → 派发一个 Task 子代理 → 等待完成 → 再调用。
    避免并行 Task 子代理触发 API 429。

    active_waves (v2.1 Batch3): 报告类型分流白名单。None=全量；指定 LAUNCH_WAVES
    索引列表时只在这些 wave 中推进，白名单耗尽即视为全部完成（all_done=True）。
    由 ir_profile._run_dispatch_prepare 按 research_plan.report_type 计算传入。

    返回值包含：
    - wave_index: 发射的 wave 编号
    - steps: 本 wave 的 step 列表及 manifest 信息
    - all_done: 所有 wave 是否已完成
    - has_more: 当前 wave 还有未发射的 step（仅 sequential=True 时有意义）
    - next_action: 下一步该做什么（'dispatch_tasks' / 'finalize' / 'already_done'）
    - task_tool_instructions: 给主 AI 的精确派发指令
    """
    research_plan_gate = ensure_research_plan_ready(task_id, entity, query, market)
    if not research_plan_gate['ready']:
        return {
            'wave_index': -1,
            'steps': [],
            'all_done': False,
            'has_more': False,
            'dispatched_count': 0,
            'next_action': 'fix_research_plan',
            'research_plan_gate': research_plan_gate,
            'task_tool_instructions': [],
        }

    wave_idx = get_current_wave_index(task_id, active_waves=active_waves)

    # 完成判定：active_waves 模式下白名单耗尽（返回 len(LAUNCH_WAVES)）或越界即完成
    _done = wave_idx >= len(LAUNCH_WAVES) or (
        active_waves is not None and wave_idx not in active_waves
    )
    if _done:
        return {
            'wave_index': -1,
            'steps': [],
            'all_done': True,
            'has_more': False,
            'next_action': 'finalize',
            'active_waves': active_waves,
            'message': '所有激活 wave 已完成，请调用 finalize_pipeline()',
        }

    wave_steps = LAUNCH_WAVES[wave_idx]
    results = []
    has_more = False

    # v3.1: 短路径降级集合——白名单外依赖自动放行（财报点评模式）
    _active_steps: set[str] | None = None
    if active_waves is not None:
        _active_steps = set()
        for _wi in active_waves:
            if 0 <= _wi < len(LAUNCH_WAVES):
                _active_steps.update(LAUNCH_WAVES[_wi])

    for i, step in enumerate(wave_steps):
        # 已完成的跳过
        out = step_output_path(task_id, step)
        if out.exists() and out.stat().st_size >= 100:
            results.append({'step': step, 'status': 'already_completed', 'output_path': str(out)})
            continue

        ready, missing = deps_ready(task_id, step, active_steps=_active_steps)
        if not ready:
            results.append({'step': step, 'status': 'blocked', 'missing': missing})
            # Sequential mode: skip blocked, continue looking for first launchable
            if sequential:
                continue
            continue

        result = launch_step(task_id, step, entity, query, STEP_TIMEOUTS.get(step, 900), market=market)
        results.append(result)

        if sequential:
            # 只发射一个 step，检查后续是否还有待发射的（de-dup completed+blocked）
            for j in range(i + 1, len(wave_steps)):
                next_step = wave_steps[j]
                next_out = step_output_path(task_id, next_step)
                if next_out.exists() and next_out.stat().st_size >= 100:
                    continue  # 已完成的跳过
                ready_n, _ = deps_ready(task_id, next_step, active_steps=_active_steps)
                if ready_n:
                    has_more = True
                    break
            break

    dispatched = [r for r in results if r.get('status') == 'dispatched']

    task_instructions = []
    for r in dispatched:
        step = r['step']
        role = STEP_ROLE.get(step, step)
        brief_path = r.get('brief_path', '')
        output_path = r.get('output_path', '')

        # 构建 prompt
        prompt_body = (
            f'你是投研分析师，负责 {role}（{step}）。\n\n'
            f'【输出路径 - 必须严格遵守】\n'
            f'你必须将完整 Markdown 报告写入以下文件（绝对路径）：\n'
            f'{output_path}\n'
            f'禁止写入任何其他路径（如 search-stepX.md、bref-stepX.md 等）。\n'
            f'唯一完成条件：上述文件成功写入且内容完整。\n\n'
        )

        # ── 统一注入：数据包路径（v3.0 替代 step1_data）──
        # v3.0 (2026-07-28): step1_data 已删除，数据在 phase04 research plan 阶段产出
        # 所有 step 读取 enriched_data_pack.json 获取行情/财务/研报/行业数据
        data_pack_path = TASKS_DIR / f'{task_id}-enriched_data_pack.json'
        prompt_body += (
            f'【数据包 - 核心输入】\n'
            f'phase04 research plan 已完成数据收集，结构化数据包在：\n'
            f'{data_pack_path}\n'
            f'你必须用 Read 工具读取此文件，获取行情/财务/研报摘要/行业数据/工商信息。\n'
            f'此文件替代了原 step1_data，是你分析的基础数据输入。\n\n'
        )

        # ── 统一注入：所有有依赖的 step 都列出前序文件路径 ──
        step_deps_list = STEP_DEPS.get(step, [])
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

        # step6_valuation 额外提醒（v3.1 研究链：估值是 Wave 3 收口，消费 Wave 1-2 全部产出）
        if step == 'step6_valuation':
            prompt_body += (
                f'💡 估值提示：你是 Wave 3 收口步骤——估值是研究的终点，不是起点。\n'
                f'step3_finance 的盈利预测（核心输入）+ step1_industry 的行业格局 + step2_biz 的业务拆解\n'
                f'+ step5_macro 的宏观/利率环境均已完成，务必全部读取后再建模。\n'
                f'⚠️ 估值方法必须按公司特征选择（对标大行）：成熟盈利公司 PE/PEG、保险 P/EV、\n'
                f'周期/资源 NAV 或 EV/EBITDA、亏损成长 PS/管线/DCF、多业务 SOTP——不是统一套 DCF。\n'
                f'短路径（财报点评）下部分前序文件可能缺失，此时降级用 enriched_data_pack.json + 自搜。\n\n'
            )

        # step7_insight 额外提醒（v3.1：Wave 4，Wave 1-3 全部产出可用）
        if step == 'step7_insight':
            prompt_body += (
                f'💡 洞察提示：Wave 1-3 全部完成——行业/业务/宏观/财务/管理层/估值六份产出都是你的输入。\n'
                f'核心方法：对照 step6_valuation 的目标价与 step1-3 的基本面判断，找出"市场定价 vs 基本面"的背离点，形成预期差。\n\n'
            )

        # step8_risk 额外提醒（v3.1：Wave 4，Wave 1-3 全部产出可用）
        if step == 'step8_risk':
            prompt_body += (
                f'💡 风险提示：Wave 1-3 全部完成——重点消费：step4_mgmt 管理层风险、step6_valuation 估值敏感性、\n'
                f'step5_macro 宏观/政策风险、step3_finance 财务脆弱点。逐一读取后综合成风险矩阵。\n\n'
            )

        # ── 行业 Overlay + 估值范式联动（v2.1，2026-07-29）──
        _industry = _infer_ir_industry(entity)
        _overlay = _load_industry_overlay(_industry)
        # 读取 research_plan 的 valuation_paradigm（模型中心化的核心联动）
        _plan = load_research_plan(task_id, TASKS_DIR) or {}
        _paradigm = _plan.get('valuation_paradigm') or 'unknown'
        if _overlay and entity:
            _overlay = _overlay.replace('{entity}', entity)
            prompt_body += (
                f'【分析框架（行业={_industry}，估值范式={_paradigm}）】\n\n'
                f'⚠️ 估值范式（{_paradigm}）是主驱动，行业 overlay 是补充。两者冲突时以 paradigm 为准。\n'
                f'⚠️ overlay 含产业链定位段，先判定标的环节，只用对应段指标。\n\n'
                f'{_overlay}\n\n'
            )
        elif _paradigm != 'unknown':
            prompt_body += (
                f'【估值范式（{_paradigm}）】\n\n'
                f'⚠️ 本标的估值范式为 {_paradigm}，所有分析框架和估值方法选择以此为准。\n'
                f'（行业 overlay 未命中，但范式已从 research_plan 获取。）\n\n'
            )

        prompt_body += (
            f'【执行步骤】\n'
            f'1. 读取 brief 文件：{brief_path}\n'
        )

        if step_deps_list:
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件（不是跳过，是强制）\n'
                f'3. 根据 brief 中的角色指令执行分析，前序 step 的完整数据是你的核心输入\n'
                f'4. 搜索数据时按下方数据源路由表选工具（禁止只用通用搜索）\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        else:
            prompt_body += (
                f'2. 根据 brief 中的角色指令和预搜索数据，执行完整分析\n'
                f'3. 搜索数据时按下方数据源路由表选工具（禁止只用通用搜索）\n'
                f'4. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )

        # ── 数据源路由硬约束（注入 prompt_body，确保子代理看到）──
        prompt_body += (
            f'【⚠️ 数据源路由（强制，违反即扣分）】\n\n'
            f'搜索数据时**必须**按以下路由选择工具，禁止所有查询都走通用搜索：\n\n'
            f'| 查什么 | 首选工具 | 调用方式 | 兜底 |\n'
            f'|--------|----------|----------|------|\n'
            f'| A/HK 股行情/财务/估值/板块/产业链/研报/评级/资金流 | **westock-mcp** | MCP 直接调用 data_quote/data_finance/data_report/data_sector 等 | NeoData → search_deep(Bash) |\n'
            f'| 企业工商/股东/高管/专利/司法/招投标 | **tyc-mcp** | search_companies → call_tool | search_deep(Bash) |\n'
            f'| A/HK 股行情/财报/估值(结构化数字) | **NeoData api** | Bash: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; import json; print(json.dumps(neodata_search(\\"查询词\\", data_type=\\"api\\"), ensure_ascii=False))"` | yfinance → search_deep(Bash) |\n'
            f'| **券商研报/行业深度/财经新闻/政策分析** | **NeoData doc** | Bash: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import neodata_search; import json; print(json.dumps(neodata_search(\\"查询词\\", data_type=\\"doc\\"), ensure_ascii=False))"` | search_deep(Bash) |\n'
            f'| **机构调研纪要/专家交流/外资研报/行业深度报告** | **ima-mcp** | MCP 直接调用 `mcp__ima-mcp__search_knowledge(knowledge_base_id="KB_ID", query="查询词")` | search_deep(Bash) |\n'
            f'| 突发新闻/实时动态（中文） | **中文实时新闻** | Bash: `cd {ROOT} && python3 -c "from scripts.search_gateway import tencent_news_search; import json; print(json.dumps(tencent_news_search(\\"{{关键词}}\\", max_results=5), ensure_ascii=False))"`（CLI积分耗尽自动降级NeoData doc） | search_deep(Bash) |\n'
            f'| 上市公司公告/新闻/研报动态 | **腾讯自选股 `data_news`** | MCP: `mcp__westock-mcp__data_news(symbol="sh600519", type=3, limit=10)`（需股票代码，type: 0公告 1研报 2新闻 3全部） | tencent_news_search → search_deep(Bash) |\n'
            f'| 美股估值/财务 | **yfinance** | Bash: `cd ~/.workbuddy/ir_runtime && python3 -c "import yfinance as yf; print(yf.Ticker(\\"AAPL\\").info)"` | westock-mcp → search_deep(Bash) |\n'
            f'| **美股英文新闻/earnings/分析师动态** | **Yahoo Finance** | Bash: `cd ~/.workbuddy/ir_runtime && python3 -c "from scripts.search_gateway import _yahoo_search; import json; print(json.dumps(_yahoo_search(\\"NVDA earnings\\", max_results=5), ensure_ascii=False))"` | search_deep(Bash) |\n'
            f'| 学术论文/政策文件/英文技术文档 | **search_deep(Bash, fetch_top_n)** | Bash 调用，自动抓全文 | — |\n\n'
            f'⚠️ IMA 知识库使用提示（ima-mcp，已授权，直接调用，v4.8 自建研报库为主力源）：\n'
            f'- KB ID 速查：★自建研报库(投行/券商研报全文)=001a89fa4b807b92 | 机构调研纪要=7300811407257275 | 行研智库=7311568991699459 | 精选报告=7302509206984644\n'
            f'- ★所有搜索第一优先「自建研报库」001a89fa4b807b92（GS/MS/JPM/BofA/Citi/UBS/Bernstein 等投行研报，全文可 fetch）\n'
            f'- 行业深度/TAM/竞争格局 → 「自建研报库」+「行研智库」\n'
            f'- 公司基本面/估值/目标价方法论 → 「自建研报库」\n'
            f'- 机构观点/电话会纪要/外资视角 → 「自建研报库」+「机构调研纪要」\n'
            f'- 时间过滤：优先最近 30 天内的投行研报（标题含日期如 -260703.pdf），大行优先\n'
            f'- 每个查询建议搜 2-3 个 KB，取交叉验证后的高价值信息；全文提取 search→fetch_media_content\n'
            f'- 脚注格式：IMA知识库 — {{KB名称}} — "{{文档标题}}" (检索日期)\n'
            f'- ⚠️ fetch权限(v4.8)：★自建研报库/行研智库/精选报告=100%可fetch全文→search后取media_id调fetch_media_content | 机构调研纪要=仅NOTE可fetch(失败用intro摘要)\n\n'
            f'⚠️ 禁止行为：\n'
            f'- 禁止用通用搜索搜公司财务数据（用 westock-mcp: data_finance）\n'
            f'- 禁止用通用搜索搜公司股东信息（用 tyc-mcp: search_companies → call_tool）\n'
            f'- 禁止用通用搜索搜行业板块走势（用 westock-mcp: data_sector）\n'
            f'- 禁止用通用搜索搜最新新闻动态（用 tencent_news_search，自动降级 NeoData doc）\n'
            f'- 禁止忽略 IMA 知识库——机构调研/专家纪要是公开 web 搜不到的增量信息，行业分析/竞争格局/投资逻辑类查询必须搜 IMA\n\n'
        )

        prompt_body += (
            f'【输出要求】\n'
            f'- ≥3000 字符\n'
            f'- ≥3 个来源引用（带 URL）\n'
            f'- 多个 ## 章节\n'
            f'- 关键数据加粗\n'
            f'- ⚠️ 买方研究要求：每项分析必须落到投资含义（对 thesis/估值/风险的影响），禁止纯描述性资料堆砌\n'
            f'- 禁止输出"Pre-search Results"格式的搜索备忘录——必须是正式分析报告\n'
            f'- ⚠️ 报告末尾必须包含「搜索审计」章节，列出：\n'
            f'  - 每次搜索用了哪个数据源（westock-mcp / tyc-mcp / NeoData / 腾讯新闻 / yfinance / search_deep(Bash)）\n'
            f'  - 查询关键词\n'
            f'  - 来源域名列表\n'
            f'  - 如果全部来源都是通用搜索(search_deep)，说明为什么没用结构化数据源（没有合理理由将被视为质量不合格）'
        )

        team_name = f'ir-{task_id}'
        task_instructions.append({
            'step': step,
            'role': role,
            'action': 'team_async_agent',
            'tool': 'Agent',
            'dispatch_mode': 'team_async',
            'subagent_type': 'general-purpose',
            'name': step,
            'description': f'IR {step}: {role}',
            'team_name_template': 'ir-{task_id}',
            'team_name': team_name,
            'mode': 'bypassPermissions',
            'connectorIds': IR_SUBAGENT_CONNECTOR_IDS,  # 天眼查 + 腾讯自选股（已授权 MCP 数据源）
            'prompt': prompt_body,
            'brief_path': brief_path,
            'output_path': output_path,
        })

    # 推进判定：active_waves 模式下看白名单内 wave_idx 之后是否还有 wave
    if active_waves is not None:
        _has_next_wave = any(
            w > wave_idx and w < len(LAUNCH_WAVES) for w in active_waves
        )
    else:
        _has_next_wave = wave_idx < len(LAUNCH_WAVES) - 1

    return {
        'wave_index': wave_idx,
        'wave_label': f'Wave {wave_idx + 1}/{len(LAUNCH_WAVES)}',
        'research_plan_gate': research_plan_gate,
        'active_waves': active_waves,
        'steps': results,
        'dispatched_count': len(dispatched),
        'has_more': has_more,
        'all_done': False,
        'next_action': 'dispatch_tasks',
        'task_tool_instructions': task_instructions,
        'after_all_tasks_complete': (
            'launch_next_wave()' if _has_next_wave else 'finalize_pipeline()'
        ),
    }


def finalize_pipeline(task_id: str, entity: str = '', market: str = 'us') -> dict:
    """Phase 5：统稿 → DOCX → 交付。所有 step 完成后由主 AI 调用。

    自动执行：
    1. 质量门禁
    2. DOCX 生成
    3. 复制到桌面
    4. 交付完成
    """
    from pathlib import Path as _P

    # 确认所有应执行的 step 都完成（v3.1：短路径下 skipped 的 step 不阻塞）
    status = get_pipeline_status(task_id)
    if not status['all_steps_done']:
        incomplete = [s for s, v in status['steps'].items()
                      if v not in ('completed', 'skipped')]
        return {
            'status': 'not_ready',
            'incomplete_steps': incomplete,
            'message': f'尚有 {len(incomplete)} 个 step 未完成',
        }

    result = {'status': 'finalizing', 'task_id': task_id}

    # 质量门禁（内联，避免导入 run_ir_pipeline 触发重量级模块链）
    # v3.1: 只对应执行的 step 打分；阈值按执行数等比缩放（全量 8 step = 16/24）
    try:
        _OFFICIAL = ['sec.gov','hkexnews.hk','cninfo.com.cn','szse.cn','sse.com.cn','ir.','investor.']
        _REPUTABLE = ['reuters.com','bloomberg.com','wsj.com','ft.com','economist.com','scmp.com','caixin.com','36kr.com','cls.cn','eastmoney.com','xueqiu.com']
        _REDFLAGS = ['待补','待填','TODO','无法验证','无法获取','需要进一步']
        _STEP_ORDER = [s for s in status.get('expected_steps', list(STEP_DEPS))
                       if s in STEP_DEPS]
        scores, issues = {}, []
        for step in _STEP_ORDER:
            f = TASKS_DIR / f'{task_id}-{step}.md'
            if not f.exists():
                scores[step] = 0; issues.append(f"❰{step}❱ 缺失"); continue
            txt = f.read_text(encoding='utf-8')
            if len(txt) < 200:
                scores[step] = 0; issues.append(f"❰{step}❱ 内容过短"); continue
            t = txt.lower()
            oc = sum(1 for d in _OFFICIAL if d in t)
            rc = sum(1 for d in _REPUTABLE if d in t)
            uc = txt.count('http')
            if oc >= 2 and len(txt) > 2000: sc = 3
            elif (oc >= 1 or rc >= 2) and len(txt) > 1000: sc = 2
            elif uc >= 1: sc = 1
            else: sc = 0
            fl = sum(1 for x in _REDFLAGS if x in txt)
            if fl >= 3 and sc > 1: sc = max(1, sc - 1); issues.append(f"❰{step}❱ {fl} 红旗")
            scores[step] = sc
        total = sum(scores.values())
        _max = len(_STEP_ORDER) * 3
        _threshold = max(1, int(_max * 16 / 24))  # 等比：全量 8 step → 16
        qg = {'scores': scores, 'total': total, 'max': _max,
              'pass': total >= _threshold, 'issues': issues}
        result['quality_gate'] = qg
        print(f"  {'✅' if qg['pass'] else '⚠️'} 质量: {qg['total']}/{qg['max']}")
    except Exception as e:
        result['quality_gate_error'] = str(e)

    # DOCX 生成（subprocess 调用，与 ir_profile Phase 5 一致）
    docx_path = None
    build_script = ROOT / 'scripts' / 'build_ir_broker_report_docx.py'
    if build_script.exists():
        try:
            import subprocess
            r = subprocess.run(
                [sys.executable, str(build_script), task_id],
                capture_output=True, text=True, timeout=180,
                cwd=str(ROOT),
            )
            if r.returncode == 0:
                try:
                    payload = json.loads(r.stdout.strip())
                    dp = payload.get('output', '')
                    if dp and _P(dp).exists():
                        docx_path = dp
                        result['docx_path'] = dp
                        print(f"  ✅ DOCX: {dp}")
                except (json.JSONDecodeError, KeyError):
                    pass
            else:
                err_msg = r.stderr[:300] if r.stderr else r.stdout[:300]
                print(f"  ⚠ DOCX 生成失败 (exit {r.returncode}): {err_msg}")
                result['docx_error'] = f"exit {r.returncode}: {err_msg}"
        except Exception as e:
            print(f"  ⚠ DOCX 生成异常: {e}")
            result['docx_error'] = str(e)
    else:
        result['docx_error'] = f"build script not found: {build_script}"

    # 如果 DOCX 失败，用 markdown 兜底（phase13 产出 synthesis.md，step8_master.md 为兼容副本）
    synthesis_md = TASKS_DIR / f'{task_id}-synthesis.md'
    fallback_md = synthesis_md if synthesis_md.exists() else TASKS_DIR / f'{task_id}-step8_master.md'
    if not docx_path and fallback_md.exists():
        result['markdown_path'] = str(fallback_md)
        result['docx_fallback'] = True

    # 复制到桌面
    desktop = _P.home() / 'Desktop'
    deliver_path = None
    try:
        if docx_path and _P(docx_path).exists():
            dst = desktop / _P(docx_path).name
            import shutil
            shutil.copy2(docx_path, dst)
            deliver_path = str(dst)
            result['desktop_path'] = deliver_path
            print(f"  📄 已复制到桌面: {dst.name}")
        elif fallback_md.exists():
            entity_clean = entity.replace(' ', '_').replace('/', '_') or task_id
            dst = desktop / f'{entity_clean}_投资研报.md'
            import shutil
            shutil.copy2(fallback_md, dst)
            deliver_path = str(dst)
            result['desktop_path'] = deliver_path
            print(f"  📄 已复制到桌面: {dst.name}")
    except Exception as e:
        result['desktop_error'] = str(e)

    # 通知 — 已移除（开源发布版本不含消息推送）

    result['status'] = 'delivered'
    result['message'] = f"研报已生成并复制到桌面: {deliver_path or '(markdown)'}"
    return result


def get_pending_steps(task_id: str) -> list[dict]:
    """获取所有待执行的 step manifest（供主 AI 读取后派发 Task）"""
    pending = []
    for step in STEP_DEPS:
        manifest = step_manifest_path(task_id, step)
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding='utf-8'))
            output = step_output_path(task_id, step)
            if not output.exists() and data.get('status') == 'pending':
                pending.append(data)
    return pending


def main():
    ap = argparse.ArgumentParser(description='IR Subagent Launcher — WorkBuddy 版 v3 (Task 子代理)')
    ap.add_argument('--task-id', required=True, help='Task ID')
    ap.add_argument('--step', choices=list(STEP_DEPS.keys()), help='Single step to launch')
    ap.add_argument('--all', action='store_true', help='Launch all steps (parallel waves)')
    ap.add_argument('--entity', default='', help='Entity name (e.g. 宁德时代)')
    ap.add_argument('--query', default='', help='Research query')
    ap.add_argument('--market', default='us', choices=['us', 'hk', 'cn'], help='Market')
    ap.add_argument('--dry-run', action='store_true', help='Show what would be launched')
    ap.add_argument('--retries', type=int, default=1, help='Max quality-gated retries')
    ap.add_argument('--check-quality', action='store_true', help='Check quality of completed step')
    ap.add_argument('--pending', action='store_true', help='List pending steps for Task dispatch')
    ap.add_argument('--do-search', action='store_true', help='Do supplementary search for step')
    sub = ap.add_subparsers(dest='action')
    info_sub = sub.add_parser('info', help='Show pipeline status')
    info_sub.add_argument('--task-id', required=True)
    super_main = ap.parse_args()

    # Handle --pending
    if super_main.pending:
        pending = get_pending_steps(super_main.task_id)
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return

    # Handle --check-quality
    if super_main.check_quality and super_main.step:
        quality = check_step_quality(super_main.task_id, super_main.step)
        print(json.dumps(quality, ensure_ascii=False, indent=2))
        return

    # Handle --do-search
    if super_main.do_search and super_main.step:
        result = do_supplementary_search(super_main.entity, super_main.step, super_main.task_id, super_main.market)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Handle info
    if super_main.action == 'info':
        pm_path = pipeline_manifest_path(super_main.task_id)
        if pm_path.exists():
            print(pm_path.read_text(encoding='utf-8'))
        else:
            print(json.dumps({'error': 'No pipeline manifest found', 'task_id': super_main.task_id}))
        return

    if super_main.step:
        timeout = STEP_TIMEOUTS.get(super_main.step, 600)
        result = launch_and_verify(super_main.task_id, super_main.step, super_main.entity, super_main.query, timeout, market=super_main.market)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif super_main.all:
        result = launch_all(super_main.task_id, super_main.entity, super_main.query, super_main.dry_run, super_main.market)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
