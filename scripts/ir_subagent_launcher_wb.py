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
REFS_DIR = ROOT / 'skills' / 'ir-coordinator' / 'references'

# 质量线
STEP_QUALITY_THRESHOLD = 3

# Step 角色名
STEP_ROLE = {
    'step1_data': '投研_主笔_数据收集',
    'step2_industry': '投研_主笔_行业分析',
    'step3_biz': '投研_主笔_商业模式',
    'step4_finance': '投研_主笔_财务分析',
    'step5_mgmt': '投研_主笔_管理层',
    'step_macro': '投研_主笔_宏观分析',
    'step6_insight': '投研_主笔_差异化洞察',
    'step6b_valuation': '投研_主笔_预测与估值',
    'step7_risk': '投研_主笔_风险催化',
    'step8_master': '投研_主笔_文档汇总',
}

# 步间依赖关系
STEP_DEPS = {
    'step1_data': [],
    'step2_industry': ['step1_data'],
    'step3_biz': ['step1_data'],
    'step4_finance': ['step1_data'],
    'step5_mgmt': ['step1_data'],
    'step_macro': [],
    'step6_insight': ['step1_data', 'step2_industry', 'step3_biz', 'step6b_valuation', 'step_macro'],
    'step6b_valuation': ['step1_data', 'step2_industry', 'step4_finance', 'step_macro'],
    'step7_risk': ['step1_data', 'step3_biz', 'step4_finance', 'step5_mgmt', 'step6b_valuation', 'step_macro'],
    'step8_master': ['step1_data', 'step2_industry', 'step3_biz', 'step4_finance', 'step5_mgmt', 'step_macro', 'step6_insight', 'step6b_valuation', 'step7_risk'],
}

# 并行发射波次
LAUNCH_WAVES = [
    ['step1_data'],
    ['step2_industry', 'step3_biz', 'step4_finance', 'step5_mgmt', 'step_macro'],
    ['step6b_valuation'],
    ['step6_insight', 'step7_risk'],
    # step8_master 已剥离为独立 synthesis 子代理（phase13）
]

# 超时
STEP_TIMEOUTS = {
    'step1_data': 900,
    'step2_industry': 900,
    'step3_biz': 900,
    'step4_finance': 900,
    'step5_mgmt': 900,
    'step_macro': 900,
    'step6_insight': 900,
    'step6b_valuation': 900,
    'step7_risk': 900,
    'step8_master': 1800,
}

# Step 查询关键词（用于自动补搜）
_STEP_KEYWORDS = {
    'step1_data': 'stock price market cap PE ratio EPS dividend analyst rating 市值 股价 市盈率',
    'step2_industry': 'industry market size market share growth rate TAM penetration competitive landscape 行业规模 竞争格局',
    'step3_biz': 'business model product revenue customer supply chain 商业模式 产品线 客户 收入结构',
    'step4_finance': 'financial report revenue profit margin cash flow ROE debt 财报 营收 毛利率 净利润 现金流',
    'step5_mgmt': 'management board governance ownership ESG compensation 管理层 董事会 股权结构 治理',
    'step_macro': 'CPI PMI interest rate LPR GDP inflation monetary policy 宏观 利率 通胀 PMI 社融',
    'step6_insight': 'catalyst valuation target price investment thesis risk-reward 催化剂 估值 目标价 投资亮点',
    'step6b_valuation': 'DCF valuation PE PB PS EV/EBITDA target price WACC comparable company valuation model 目标价 估值',
    'step7_risk': 'risk regulatory litigation competition macro threat 风险 监管 诉讼 竞争威胁 宏观',
    'step8_master': '',
}

_STEP_QUERY_TEMPLATES = {
    'step1_data': [
        '"{entity}" stock price market cap PE EPS analyst rating',
        '"{entity}" investor relations results announcement',
        'site:hkexnews.hk "{entity}" results announcement',
    ],
    'step2_industry': [
        '"{entity}" industry market size competitive landscape',
        '"{entity}" market share industry report',
        '"{entity}" 行业 竞争格局 市场规模',
    ],
    'step3_biz': [
        '"{entity}" business model revenue segments',
        '"{entity}" products services overview',
        '"{entity}" 商业模式 收入结构 产品',
    ],
    'step4_finance': [
        '"{entity}" financial report revenue profit margin cash flow ROE debt',
        '"{entity}" annual report results announcement revenue profit',
        'site:hkexnews.hk "{entity}" annual report',
        'site:hkexnews.hk "{entity}" results announcement',
        '"{entity}" 财报 营收 毛利率 净利润 现金流',
    ],
    'step5_mgmt': [
        '"{entity}" CEO management team leadership governance',
        '"{entity}" executive changes board ownership',
        '"{entity}" 管理层 董事会 股权结构 治理',
    ],
    'step_macro': [
        '"{entity}" sector macro impact CPI PMI interest rate',
        'China macro economy GDP inflation monetary policy latest',
        '宏观 利率 通胀 PMI 社融 最新数据',
    ],
    'step6_insight': [
        '"{entity}" investment thesis valuation target price catalyst',
        '"{entity}" analyst report target price catalyst',
        '"{entity}" 投资逻辑 估值 催化剂',
    ],
    'step7_risk': [
        '"{entity}" risks regulatory litigation competition macro',
        '"{entity}" risk analysis report',
        '"{entity}" 风险 监管 诉讼 竞争',
    ],
    'step6b_valuation': [
        '"{entity}" DCF valuation target price WACC',
        '"{entity}" comparable company valuation PE PB PS EV/EBITDA',
        '"{entity}" analyst consensus target price',
        '"{entity}" 估值 目标价 可比公司',
    ],
    'step8_master': [],
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


def deps_ready(task_id: str, step: str) -> tuple[bool, list[str]]:
    """检查依赖步骤的输出文件是否已存在且完整（>100 bytes）"""
    missing = []
    for dep in STEP_DEPS.get(step, []):
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
        f'**按你要查的数据类型选择工具，禁止只用 web_search 做所有搜索：**',
        f'',
        f'**1. 上市公司金融数据（A/HK/美股行情、财报、板块、金融新闻）**',
        f'→ `search_gateway`（多引擎聚合，含 NeoData 金融数据）',
        f'```bash',
        f'cd ~/.workbuddy/ir_runtime && python3 -c "',
        f'from scripts.search_gateway import search',
        f'results = search(\'公司名 营收 利润\', prefer=\'auto\')',
        f'for r in results[:5]: print(r[\'title\'], r[\'url\'], r[\'content\'][:200])',
        f'"',
        f'```',
        f'- prefer=auto：金融查 NeoData → DDG → SearXNG；prefer=multi：四路合并最全',
        f'- search_deep：搜索 + 自动抓正文一步到位',
        f'- search_many：多关键词批量搜索',
        f'',
        f'**2. 上市公司估值指标（PE/PB/PS/市值/股息率/beta）**',
        f'→ `yfinance`（精确估值数字）',
        f'```bash',
        f'cd ~/.workbuddy/ir_runtime && python3 -c "',
        f'from tasks.valuation_enricher import enrich_with_yahoo',
        f'data = enrich_with_yahoo(\'公司名\')',
        f'print(data)',
        f'"',
        f'```',
        f'- A/HK 股优先走 NeoData，美股走 yfinance',
        f'',
        f'**3. 企业工商/司法/专利/资质（天眼查 TYC MCP）**',
        f'→ TYC MCP 两阶段调用：search_companies → get_company_capabilities → call_tool',
        f'- `get_company_basic_profile(company_name="...")` — 基础画像（工商登记+简介+标签+规模）',
        f'- `get_company_people(company_name="...")` — 人员列表（高管、董监高）',
        f'- `get_person_risk_profile(company_name="...", person_name="...")` — 个人风险画像',
        f'- `search_patents(query="...", applicant="公司名")` — 专利搜索',
        f'- `search_bids(query="公司名 招投标")` — 招投标搜索',
        f'- TYC 查中国大陆注册企业；境外企业用 web_search 兜底',
        f'- ⚠️ call_tool 的 tool_name 必须逐字复制 get_company_capabilities 返回的真实名称',
        f'',
        f'**4. 通用网络搜索（新闻、行业报告、通用信息）**',
        f'→ `web_search`（WorkBuddy 内置工具，直接用）',
        f'- 不适合结构化金融数据（用 search_gateway）和企业数据（用 TYC 天眼查）',
        f'- 作为所有搜索的兜底手段',
        f'',
        f'**5. 网页正文深度阅读**',
        f'→ `web_fetch`（WorkBuddy 内置工具）— 给 URL 返回正文',
        f'→ `search_deep` — 搜索 + 自动抓 top N 正文，一步到位',
        f'',
        f'**工具优先级总结：**',
        f'| 查什么 | 首选工具 | 兜底 |',
        f'|--------|---------|------|',
        f'| A/HK 股行情/财报/估值 | NeoData api → yfinance 交叉 | web_search |',
        f'| 美股行情/估值/分红 | yfinance | NeoData → web_search |',
        f'| 券商研报/行业深度 | NeoData doc (按日期降序) | web_search |',
        f'| **新闻/产品发布/技术动态** | web_search + 时效锚定(含年月) | search_gateway |',
        f'| 企业工商/司法/专利 | 天眼查 MCP (已配置) | web_search |',
        f'| 技术论文/arxiv | web_search + 年份 | web_fetch 读论文 |',
        f'| 开源项目/GitHub/HF | web_search + 年份 | web_fetch 读 README |',
        f'| 读某个 URL 正文 | web_fetch | — |',
        f'',
        f'### ⏰ 数据时效性硬要求（最高优先级，违反即任务失败）',
        f'',
        f'**第零轮搜索（在所有广度搜索之前必须执行，不可跳过）：**',
        f'1. web_search("{entity} {{YYYY}}年{{M}}月 最新动态") — 锁定标的当前状态',
        f'2. web_search("{entity} latest news {{YYYY}}") — 英文视角补充',
        f'3. 如涉及产品/技术: web_search("{{product}} 最新版本 发布 {{YYYY}}") — 锁定当前版本',
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
        f'- ≥ 3 个实际深读过的 URL（web_fetch 或 search_deep 抓到的正文，不是只看 snippet）',
        f'- ≥ 3 个独立来源域名（不能全是同一个站点的页面）',
        f'',
        f'**搜索策略（必须按顺序执行）：**',
        f'0. **第零轮：时效锚定（最先执行，不可跳过）**',
        f'   - NeoData doc: neodata_search(\'{entity} 最新动态\', data_type=\'doc\') — 拿深度分析文章',
        f'   - web_search: "{entity} {{当前年月}} 最新动态" — 锁定突发新闻',
        f'   - 如涉及产品/技术: web_search("{{product}} 最新版本 发布 {{YYYY}}") — 锁定当前版本',
        f'   - 目的: 先知道"最新"是什么，后续分析才不会引用过期信息',
        f'1. **第一轮：广度扫描** — NeoData doc 拿研报/分析 + search_gateway prefer=multi 多关键词并行',
        f'2. **第二轮：深度验证** — 对第一轮发现的关键 claim，用 web_fetch 或 search_deep 读全文验证',
        f'3. **第三轮：交叉验证/反证** — 搜竞品对比、负面信息、行业报告、分析师观点',
        f'4. **TYC 天眼查必查项**（如标的涉及中国大陆企业）：工商信息、司法诉讼、专利、资质',
        f'5. **金融数据必查**：NeoData api → yfinance 交叉验证',
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
        f'- 禁止只用 web_search 做所有搜索——它没有 NeoData 金融数据和 TYC（天眼查）结构化数据',
        f'- 禁止只搜一轮就结束——泛搜一轮不够，必须多角度验证',
        f'- 禁止只搜不读——搜到 URL 后必须 web_fetch 读正文提取事实',
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
    if step in ('step1_data', 'step2_industry', 'step3_biz', 'step4_finance') and kpi_checklist_path.exists():
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
        f"1. Search for the missing data yourself (NeoData via search_gateway → yfinance → web_search)\n"
        f"2. Integrate the found data into your analysis\n"
        f"3. Only mark as '待核实' after 3 rounds of supplementary search still yield nothing\n"
        f"Do NOT return to the coordinator for search instructions — you ARE the search agent.\n\n"
        f"DATA SOURCE PRIORITY:\n"
        f"- A/HK financials: NeoData api → yfinance → web_search\n"
        f"- US stocks: yfinance → NeoData → web_search\n"
        f"- News/analysis/reports: NeoData doc (深度分析, 首选!) → web_search (突发新闻补充)\n"
        f"- Product/tech launches: NeoData doc + web_search (必须含当前年月)\n"
        f"- Company registry/legal: TYC MCP (已在 manifest 配置) → web_search\n"
        f"- search_gateway auto-routes financial queries to NeoData Layer 0\n\n"
        f"TEMPORAL ANCHORING (CRITICAL):\n"
        f"- Step 0: NeoData doc search '{entity} 最新动态' + web_search '{entity} YYYY年M月 最新动态'\n"
        f"- ALL search queries MUST include current year/month\n"
        f"- Product/tech version: search '{{product}} latest version YYYY' before citing\n"
        f"- Data >12 months old → mark ❌ and search for latest replacement\n"
        f"- NEVER cite an outdated product/model version when a newer one exists\n\n"
        f"- Required fields coverage ≥ 70%\n"
        f"- ≥ 3 independent sources\n"
        f"- ≥ 3 ## level sections\n"
        f"- Content length ≥ 3000 chars\n"
        f"If self-check fails, do more research before outputting.\n\n"
    )

    # 角色专属 ANTI-DEFECT RULES
    step_rules = {
        'step1_data': (
            'ANTI-DEFECT RULES:\n'
            '1. FINANCING/LISTING STATUS: Before citing any company (target or competitor), verify their '
            'current listing/financing status. If yfinance returns no data for a previously known ticker, '
            'search whether the company has been delisted, privatized, or acquired.\n'
            '2. PERSON VERIFICATION: Every person name cited must be verified via at least 1 independent '
            'source. NEVER fabricate person names or positions from model training data.\n'
            '3. YFINANCE ACCURACY: For key financial data (revenue, market cap, PE), cross-verify yfinance '
            'data with at least 1 web_search source (东财/雪球/公司IR页). If discrepancy >10%, investigate.\n'
            '4. COMPETITOR FINANCING VERIFICATION: For every competitor in comparison tables, search-verify '
            'their current financing/IPO status. NEVER use stale training data (e.g. "private, B轮" when '
            'company has IPO\'d). If listed, use yfinance for real-time market cap and cite ticker.\n'
        ),
        'step2_industry': (
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
            'Use NeoData doc for depth analysis + web_search with current year/month for breaking news. '
            'If a newer version exists, use the newer data. '
            'NEVER cite an older model/version when a newer one has been released.\n'
        ),
        'step3_biz': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPETITOR MOAT VERIFICATION: When scoring competitor moat dimensions, each score must be '
            'based on SEARCH-VERIFIED current data, not model training data. A competitor\'s capability '
            'may have changed significantly since training cutoff.\n'
            '2. PRODUCT LINE CURRENCY: For each product/service mentioned, verify it is '
            'currently active and the latest iteration. Use NeoData doc for depth + '
            'web_search "{product} latest {year}" for breaking news. '
            'Deprecated or superseded products must be noted as such.\n'
        ),
        'step4_finance': (
            'ANTI-DEFECT RULES:\n'
            '1. LATEST FILING VERIFICATION: Before citing annual report data, verify it is the LATEST filing. '
            'Search "{company} 最新年报 {year}" and check HKEX/SEC for recent filings. '
            'If a newer report exists, use the newer data.\n'
            '2. AUDIT OPINION CHECK: Note the audit opinion for each year cited. A change in audit opinion '
            '(e.g., from "unqualified" to "qualified") is a significant red flag that must be highlighted.\n'
        ),
        'step5_mgmt': (
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
        'step6_insight': (
            'ANTI-DEFECT RULES:\n'
            '1. COMPETITOR DATA CURRENCY: When citing competitor data from prior steps, verify it is current. '
            'If prior steps used stale competitor data, note this as a limitation.\n'
            '2. PRODUCT/TECH CATALYST CURRENCY: When identifying investment catalysts related to '
            'product launches or tech milestones, verify you have the LATEST product/version info. '
            'Use NeoData doc for depth + web_search with current year/month. '
            'A catalyst based on outdated product info (e.g. citing model v1 when v3 exists) '
            'invalidates the entire investment thesis.\n'
        ),
        'step6b_valuation': (
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
        'step7_risk': (
            'ANTI-DEFECT RULES:\n'
            '1. REGULATORY STATUS CURRENCY: Every regulatory risk cited must be search-verified for CURRENT '
            'status. A regulation described as "即将出台" in older sources may have been enacted, revised, '
            'or shelved. Search "{regulation} 最新 现行 有效 {year}" before citing.\n'
            '2. COMPETITOR COMPLIANCE EVENTS: For competition-related risks, search whether major competitors '
            'have recent regulatory penalties — this may reduce competitive pressure on the target.\n'
        ),
        'step_macro': (
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
        'step8_master': (
            'ANTI-DEFECT RULES:\n'
            '1. FINANCING STATUS CONSISTENCY: Check that the same entity\'s financing/IPO status is consistent '
            'across all steps. If step1 describes a competitor as "private" but step6b uses listed-company '
            'multiples for it, this is a critical inconsistency that must be resolved by search verification.\n'
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

    # 检查依赖
    ready, missing = deps_ready(task_id, step)
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
        'connectorIds': ['tyc-mcp'],  # 天眼查 MCP — 工商/股东/司法/专利查询
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
    # step8_master 是统稿 step，不做搜索深度校验
    search_audit_issues = []
    if step != 'step8_master':
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
            'queries': len(audit['queries']) if step != 'step8_master' else None,
            'fetched_urls': len(audit['fetched_urls']) if step != 'step8_master' else None,
            'source_domains': len(set(audit['source_domains'])) if step != 'step8_master' else None,
        } if step != 'step8_master' else None,
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


def get_current_wave_index(task_id: str) -> int:
    """根据已完成的 step 输出文件推算当前应该发射的 wave 索引（0-3）。"""
    for idx, wave_steps in enumerate(LAUNCH_WAVES):
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
    """返回整个管线当前状态快照。"""
    steps_status = {}
    for step in STEP_DEPS:
        out = step_output_path(task_id, step)
        if out.exists() and out.stat().st_size >= 100:
            steps_status[step] = 'completed'
        else:
            ready, missing = deps_ready(task_id, step)
            steps_status[step] = 'ready' if ready else f'blocked_by:{",".join(missing)}'
    wave_idx = get_current_wave_index(task_id)
    all_done = wave_idx >= len(LAUNCH_WAVES)
    return {
        'task_id': task_id,
        'steps': steps_status,
        'current_wave': wave_idx if not all_done else 'all_done',
        'total_waves': len(LAUNCH_WAVES),
        'completed_count': sum(1 for v in steps_status.values() if v == 'completed'),
        'total_steps': len(STEP_DEPS),
        'all_steps_done': all_done,
        'next_action': 'finalize' if all_done else f'launch_wave_{wave_idx}',
    }


def launch_next_wave(task_id: str, entity: str = '', query: str = '', market: str = 'us',
                     sequential: bool = False) -> dict:
    """发射当前应该执行的 wave。主 AI 每轮调用一次，直到所有 wave 完成。

    sequential=True: 每次只发射当前 wave 的一个 step，返回 has_more 标志。
    主 AI 应循环调用 → 派发一个 Task 子代理 → 等待完成 → 再调用。
    避免并行 Task 子代理触发 API 429。

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

    wave_idx = get_current_wave_index(task_id)

    if wave_idx >= len(LAUNCH_WAVES):
        return {
            'wave_index': -1,
            'steps': [],
            'all_done': True,
            'has_more': False,
            'next_action': 'finalize',
            'message': '所有 wave 已完成，请调用 finalize_pipeline()',
        }

    wave_steps = LAUNCH_WAVES[wave_idx]
    results = []
    has_more = False

    for i, step in enumerate(wave_steps):
        # 已完成的跳过
        out = step_output_path(task_id, step)
        if out.exists() and out.stat().st_size >= 100:
            results.append({'step': step, 'status': 'already_completed', 'output_path': str(out)})
            continue

        ready, missing = deps_ready(task_id, step)
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
                ready_n, _ = deps_ready(task_id, next_step)
                if ready_n:
                    has_more = True
                    break
            break

    dispatched = [r for r in results if r.get('status') == 'dispatched']

    # 构建主 AI 的精确执行指令
    # step8_master 的前序 step 列表（需要读取它们的完整输出）
    _STEP8_PRIOR_STEPS = ['step1_data', 'step2_industry', 'step3_biz', 'step4_finance', 'step5_mgmt', 'step_macro', 'step6_insight', 'step6b_valuation', 'step7_risk']

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

        # step6b_valuation 额外提醒
        if step == 'step6b_valuation':
            prompt_body += (
                f'💡 估值提示：step2_industry 中的竞争格局和 step_macro 中的利率/通胀数据对 WACC 和增长假设至关重要，务必在估值前完整阅读。\n\n'
            )

        # step6_insight 额外提醒
        if step == 'step6_insight':
            prompt_body += (
                f'💡 洞察提示：step6b_valuation 的估值结论和 step_macro 的宏观环境判断是形成投资洞察的核心输入，务必完整阅读后再下判断。\n\n'
            )

        # step7_risk 额外提醒
        if step == 'step7_risk':
            prompt_body += (
                f'💡 风险提示：step5_mgmt 的管理层评估、step6b_valuation 的估值敏感性和 step_macro 的政策/宏观风险是风险分析的核心输入，务必完整阅读。\n\n'
            )

        # step8_master 统稿硬约束
        if step == 'step8_master':
            prompt_body += (
                f'⚠️ 统稿保留硬约束（最高优先级，违反任一条即视为统稿失败）：\n\n'
                f'【规则1】核心对比表必须原文保留：行业技术路线全景对比表、产品级竞品参数对比表、现有方案深度对比大表、核心组件拆解表——不得删除或压缩为文字叙述。如果某个step有5张竞品对比表，统稿必须保留5张，不能合并成1张。\n\n'
                f'【规则2】市占率/份额/渗透率数据必须完整保留：TAM/SAM/SOM分层推算及每层具体数字、各细分市场渗透率及驱动力、竞品市占率（具体数字和百分比，不能只写"垄断竞争"等模糊表述）、标的公司渗透率——这些是判断市场空间的核心依据。\n\n'
                f'【规则3】去重只做跨step，不做step内压缩：跨step重复内容可合并，但单个step内部的表格、数据、分析段落不得删除或压缩。\n\n'
                f'【规则4】来源合并不得丢来源：所有step的来源索引表/脚注列表都必须合并到统稿末尾"来源附录"章节；不能因格式不同（[^N]脚注/编号表格/URL直接引用/评级格式）就丢弃；非[^N]格式的来源必须转换为[^N]脚注格式纳入统一编号；目标：统稿来源总数 ≥ 各step来源去重后总数。统稿完成后必须自检：数末尾来源附录条目数，对比各step来源总数，显著减少则说明有来源丢失，必须补回。\n\n'
            )

        prompt_body += (
            f'【执行步骤】\n'
            f'1. 读取 brief 文件：{brief_path}\n'
        )

        if step == 'step8_master':
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件\n'
                f'3. 根据 brief 中的统稿规则，将 step1~step7 的内容汇总为一份完整研报\n'
                f'   （step_macro 宏观判断需纳入投资摘要和风险章节）\n'
                f'4. 如发现数据缺口或矛盾，用 web_search 补搜验证（最多 3 轮）\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        elif step_deps_list:
            # 有依赖的 step（step2/3/4/5/6/6b/7）：强制读取前序文件
            prompt_body += (
                f'2. 逐一读取上方列出的前序 step 完整输出文件（不是跳过，是强制）\n'
                f'3. 根据 brief 中的角色指令执行分析，前序 step 的完整数据是你的核心输入\n'
                f'4. 如发现数据缺口，用 web_search 补搜（最多 3 轮）\n'
                f'5. 将完整 Markdown 报告写入上方指定的输出路径\n\n'
            )
        else:
            # 无依赖的 step（step1_data, step_macro）：直接从 brief 开始
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
            f'- 禁止输出"Pre-search Results"格式的搜索备忘录——必须是正式分析报告'
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
            'connectorIds': ['tyc-mcp'],  # 天眼查 MCP
            'prompt': prompt_body,
            'brief_path': brief_path,
            'output_path': output_path,
        })

    return {
        'wave_index': wave_idx,
        'wave_label': f'Wave {wave_idx + 1}/{len(LAUNCH_WAVES)}',
        'research_plan_gate': research_plan_gate,
        'steps': results,
        'dispatched_count': len(dispatched),
        'has_more': has_more,
        'all_done': False,
        'next_action': 'dispatch_tasks',
        'task_tool_instructions': task_instructions,
        'after_all_tasks_complete': (
            'launch_next_wave()' if wave_idx < len(LAUNCH_WAVES) - 1 else 'finalize_pipeline()'
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

    # 确认所有 step 都完成
    status = get_pipeline_status(task_id)
    if not status['all_steps_done']:
        incomplete = [s for s, v in status['steps'].items() if v != 'completed']
        return {
            'status': 'not_ready',
            'incomplete_steps': incomplete,
            'message': f'尚有 {len(incomplete)} 个 step 未完成',
        }

    result = {'status': 'finalizing', 'task_id': task_id}

    # 质量门禁（内联，避免导入 run_ir_pipeline 触发重量级模块链）
    try:
        _OFFICIAL = ['sec.gov','hkexnews.hk','cninfo.com.cn','szse.cn','sse.com.cn','ir.','investor.']
        _REPUTABLE = ['reuters.com','bloomberg.com','wsj.com','ft.com','economist.com','scmp.com','caixin.com','36kr.com','cls.cn','eastmoney.com','xueqiu.com']
        _REDFLAGS = ['待补','待填','TODO','无法验证','无法获取','需要进一步']
        _STEP_ORDER = ['step1_data','step2_industry','step3_biz','step4_finance','step5_mgmt','step_macro','step6_insight','step6b_valuation','step7_risk','step8_master']
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
        qg = {'scores': scores, 'total': total, 'max': len(_STEP_ORDER) * 3, 'pass': total >= 16, 'issues': issues}
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

    # 如果 DOCX 失败，用 markdown 兜底
    master_md = TASKS_DIR / f'{task_id}-step8_master.md'
    if not docx_path and master_md.exists():
        result['markdown_path'] = str(master_md)
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
        elif master_md.exists():
            entity_clean = entity.replace(' ', '_').replace('/', '_') or task_id
            dst = desktop / f'{entity_clean}_投资研报.md'
            import shutil
            shutil.copy2(master_md, dst)
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
