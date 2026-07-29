#!/usr/bin/env python3
"""
BP Phase 2 Subagent Launcher — WorkBuddy 版本 v4

无需外部 LLM API。发射器负责构建 brief、写入 manifest 和 spawn receipt，
实际的 LLM 推理由 WorkBuddy 主 AI 通过 Task 子代理完成。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from runtime.profiles.bp_constants import BP_TYC_CONNECTOR_IDS, BP_ROLE_CONNECTOR_IDS

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / 'tasks'

ROLE_TO_KEY = {
    'bp_company_team_compliance': 'company_team_compliance',
    'bp_product_commercial': 'product_commercial',
    'bp_tech_ip_moat': 'tech_ip_moat',
    'bp_market_supply_chain': 'market_supply_chain',
    'bp_competition_positioning': 'competition_positioning',
    'bp_valuation_return': 'valuation_return',
    'bp_dealbreaker_risk': 'dealbreaker_risk',
    # v4.5 新增：投资叙事层 3 角色（Wave 4）
    'bp_consensus_challenge': 'consensus_challenge',
    'bp_catalyst': 'catalyst',
    'bp_industry_research': 'industry_research',
    # Legacy slugs kept for old task recovery.
    'bp_团队与合规': 'team',
    'bp_技术与产品': 'tech',
    'bp_行业与供应链': 'industry',
    'bp_估值': 'valuation',
    'bp_竞争与结论': 'competition',
}

# ── System prompts are loaded from instruction_store_bp/*.md files. ──
# The code below is a FALLBACK dict used only when instruction store files are missing.
# Real prompts are loaded by _load_instruction_store_prompts() and override these.
ROLE_SYSTEM_PROMPTS: dict[str, str] = {}

INSTRUCTION_STORE = ROOT / 'instruction_store_bp'
CURRENT_BP_ROLES = {
    'bp_company_team_compliance',
    'bp_product_commercial',
    'bp_tech_ip_moat',
    'bp_market_supply_chain',
    'bp_competition_positioning',
    'bp_valuation_return',
    'bp_dealbreaker_risk',
    # v4.5 新增：投资叙事层（Wave 4）
    'bp_consensus_challenge',
    'bp_catalyst',
    'bp_industry_research',
}


_INSTRUCTION_STORE_CACHE: dict[str, str] | None = None
_INSTRUCTION_STORE_MTIME: float = 0


def _load_instruction_store_prompts(force_reload: bool = False) -> dict[str, str]:
    """加载当前 BP 八角色指令库，支持 mtime 检测自动刷新。

    - index.json mtime 未变时直接返回缓存，避免重复读盘
    - force_reload=True 或 mtime 变化时重新读取并同步更新 ROLE_SYSTEM_PROMPTS
    - index.json 缺失或解析失败时返回上次缓存（若有），否则返回空 dict
    """
    global _INSTRUCTION_STORE_CACHE, _INSTRUCTION_STORE_MTIME

    index_path = INSTRUCTION_STORE / 'index.json'
    if not index_path.exists():
        return _INSTRUCTION_STORE_CACHE if _INSTRUCTION_STORE_CACHE is not None else {}

    current_mtime = index_path.stat().st_mtime
    if not force_reload and _INSTRUCTION_STORE_CACHE is not None:
        if current_mtime == _INSTRUCTION_STORE_MTIME:
            return _INSTRUCTION_STORE_CACHE

    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
    except Exception:
        return _INSTRUCTION_STORE_CACHE if _INSTRUCTION_STORE_CACHE is not None else {}

    prompts: dict[str, str] = {}
    for role in index.get('roles', []):
        role_key = role.get('key', '')
        role_file = role.get('file', '')
        if role_key not in CURRENT_BP_ROLES or not role_file:
            continue
        path = INSTRUCTION_STORE / role_file
        if path.exists():
            prompts[role_key] = path.read_text(encoding='utf-8')

    _INSTRUCTION_STORE_CACHE = prompts
    _INSTRUCTION_STORE_MTIME = current_mtime

    # 同步更新 ROLE_SYSTEM_PROMPTS，保持模块级 dict 接口不变（测试中有直接引用）
    for _role_key in CURRENT_BP_ROLES:
        ROLE_SYSTEM_PROMPTS[_role_key] = prompts.get(
            _role_key,
            f"UNKNOWN BP ROLE: {_role_key}. No instruction-store prompt is registered. "
            "Do not reuse another BP role prompt; stop and report this configuration gap.",
        )

    return prompts


# 模块级初始化（后续调用时自动检测 mtime 刷新）
_load_instruction_store_prompts()


def _load_tool_usage_guide() -> str:
    """从 instruction_store_bp/_common_tool_guide.md 加载工具使用指南。

    支持热更新：修改 markdown 文件后，新派发的子代理会拿到最新内容。
    """
    guide_path = INSTRUCTION_STORE / '_common_tool_guide.md'
    if guide_path.exists():
        return guide_path.read_text(encoding='utf-8')
    return ''  # fallback: 空字符串，不影响 system_prompt 主体


_TOOL_USAGE_GUIDE = _load_tool_usage_guide()


def _slug(role_name: str) -> str:
    return ROLE_TO_KEY.get(role_name, role_name.replace('bp_', '').replace('与', '_').replace(' ', '_'))


def _sidecar_paths(output_path: Path) -> dict[str, Path]:
    return {
        'facts': output_path.with_name(f'{output_path.stem}-facts.json'),
        'section_package': output_path.with_name(f'{output_path.stem}-section.json'),
    }


def notify_wx(text: str) -> bool:
    """Notification stub — longshao_notify removed for open-source release."""
    return False


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _owned_items(plan: dict, collection_name: str, role_name: str) -> list[dict]:
    items = plan.get(collection_name) or []
    return [item for item in items if isinstance(item, dict) and item.get('owner_section') == role_name]


def load_bp_research_slice(task_dir: Path, role_name: str) -> dict:
    plan = _load_json(task_dir / 'bp_research_plan.json')
    if not plan:
        return {}
    return {
        'schema_version': plan.get('schema_version', ''),
        'prepared_by': plan.get('prepared_by', ''),
        'generation_roles': plan.get('generation_roles', {}),
        'core_questions': _owned_items(plan, 'core_questions', role_name),
        'strategic_questions': _owned_items(plan, 'strategic_questions', role_name),
        'section_requirements': (plan.get('section_requirements') or {}).get(role_name, {}),
    }


def load_bp_claim_slice(task_dir: Path, role_name: str) -> list[dict]:
    plan = _load_json(task_dir / 'bp_research_plan.json')
    return _owned_items(plan, 'claim_matrix', role_name)



def load_bp_search_work_order(task_dir: Path, role_name: str) -> dict:
    plan = _load_json(task_dir / 'bp_search_plan.json')
    if not plan:
        return {}
    tasks = [
        item for item in plan.get('search_tasks') or []
        if isinstance(item, dict) and item.get('owner_section') == role_name
    ]
    if not tasks:
        return {}
    return {
        'schema_version': plan.get('schema_version', ''),
        'policy': plan.get('policy', {}),
        'search_tasks': tasks,
    }



def _build_brief(task_id: str, sub: dict, task_dir: Path | None = None) -> Path:
    task_dir = task_dir or TASKS_DIR / task_id
    slug = _slug(sub['role_name'])
    brief_path = task_dir / f'bp_phase2_brief_{slug}.md'

    output_rel = Path(sub['output_file'])
    sidecar_paths = _sidecar_paths(output_rel)
    try:
        output_display = str(output_rel.relative_to(ROOT))
    except Exception:
        output_display = str(output_rel)
    try:
        facts_display = str(sidecar_paths['facts'].relative_to(ROOT))
    except Exception:
        facts_display = str(sidecar_paths['facts'])
    try:
        section_display = str(sidecar_paths['section_package'].relative_to(ROOT))
    except Exception:
        section_display = str(sidecar_paths['section_package'])

    lines = [
        f'# BP Research Brief — {sub["role_name"]}',
        '',
        f'- Output file: `{output_display}`',
        '',
        '> **身份、写作标准、章节结构、调查范围、防缺陷规则**均在 System Prompt 中，本 brief 不重复。',
        '> 请先读 System Prompt，再按以下输入文件和交付协议执行。',
        '',
        '## 角色说明',
        sub.get('description', ''),
        '',
    ]

    lines.append('## 关键输入文件（都在 workspace 内）')
    lines.append('')
    lines.append('以下均为完整输入文件路径。你必须用 Read 工具读取完整内容，不得依赖 brief 摘要或截断预览。必须先读共享尽调页 `bp_shared_diligence_page.md`，再读 research plan / fact store / 原始材料。')

    # ── Wave 交接：提前取 wave_inputs，后面两处都要用 ──
    wave_inputs = sub.get('wave_inputs', {})

    candidates = [
        task_dir / 'bp_shared_diligence_page.md',
        task_dir / 'bp_research_plan.json',
        task_dir / 'bp_search_plan.json',
        task_dir / 'bp_claim_coverage.json',
        task_dir / 'bp_shared_state.json',
        task_dir / 'bp_fact_store.json',
        task_dir / 'bp_fact_store_index.json',
        task_dir / 'bp_ocr_text.txt',
        task_dir / 'bp_step0_profile.json',
        task_dir / 'bp_step0_profile.md',
        task_dir / 'company_verify_report.json',
        task_dir / 'bp_presearch_results.json',
    ]
    candidates += sorted(task_dir.glob('bp_presearch_step*.md'))
    candidates += sorted((task_dir / 'body_content').glob('*.json')) if (task_dir / 'body_content').exists() else []

    # ── Wave 交接：前一波子代理输出也加入 candidates，让 _read_brief_content 自动内联 ──
    for slug, path in wave_inputs.items():
        if isinstance(path, str):
            p = Path(path)
            if p.exists() and p not in candidates:
                candidates.append(p)

    for p in candidates:
        if p.exists():
            try:
                rel = str(p.relative_to(ROOT))
            except Exception:
                rel = str(p)
            lines.append(f'- `{rel}`')

    lines += [
        '',
        '## 子任务键值输入',
        '```json',
        json.dumps(sub.get('key_inputs', {}), ensure_ascii=False, indent=2),
        '```',
    ]

    research_slice = load_bp_research_slice(task_dir, sub['role_name'])
    claim_slice = load_bp_claim_slice(task_dir, sub['role_name'])
    if research_slice or claim_slice:
        lines += [
            '',
            '## 当前角色 Research Plan Slice',
            '',
            'Research Plan 由脚本和主控 Agent 协同生成：脚本负责 schema / fact requirements / coverage matrix / validation，主控 Agent 负责 strategic questions / claim prioritization / owner assignment。',
            '你必须只围绕当前角色 owner 的问题和 claims 工作，禁止处理非 owner claims；如发现其他角色 claim 有明显冲突，只能写入 data_gaps 或 counter_evidence，不能越权展开成主章节。',
            '',
            '```json',
            json.dumps({
                'research_slice': research_slice,
                'claim_slice': claim_slice,
            }, ensure_ascii=False, indent=2),
            '```',
        ]

    search_work_order = load_bp_search_work_order(task_dir, sub['role_name'])
    if search_work_order:
        lines += [
            '',
            '## 当前角色 Search Work Order',
            '',
            '这些是主控从 BP Research Plan 编译出的 claim-level 搜索任务。你必须逐条执行对应 search_task_id，不得只做泛泛搜索。',
            '- 每个 search_task_id 必须在 section package 的 search_audit.claim_coverage 中出现。',
            '- 达不到 min_unique_queries / min_fetched_urls / min_independent_domains 时，不得把 claim 写成 supported；必须写入 data_gaps。',
            '- requires_counter_search=true 的任务必须做反向搜索，搜索负面、失败、纠纷、未验证、竞品替代等反证。',
            '- BP-only 信息只能作为待验证 claim 来源，不能作为高置信外部证据。',
            '',
            '```json',
            json.dumps(search_work_order, ensure_ascii=False, indent=2),
            '```',
        ]

    # ── 跨维度交接：Wave 1 并行子代理互相感知 ──
    # Wave 1 子代理没有 wave_inputs（它们是第一批），但它们需要知道其他维度在研究什么，
    # 以避免重复搜索、发现交叉点、标注潜在矛盾。
    if not wave_inputs:
        # 从 task_dir 的 manifest 文件中推断同波次的其他角色
        sibling_briefs: list[tuple[str, str]] = []
        for manifest_file in sorted(task_dir.glob("bp_phase2_manifest_*.json")):
            try:
                m = json.loads(manifest_file.read_text(encoding="utf-8"))
                m_role = str(m.get("role", ""))
                m_desc = str(m.get("description", ""))
                if m_role and m_role != sub["role_name"]:
                    sibling_briefs.append((m_role, m_desc))
            except Exception:
                pass
        if sibling_briefs:
            lines += [
                '',
                '## 并行维度感知（你不需要调查这些维度，但可以在分析中交叉引用）',
                '',
                '以下维度正由其他子代理并行调查。如果你在调查过程中发现与本维度相关的交叉信息，'
                '可以简要提及并标注"详见XX维度分析"；如果发现明显矛盾，写入你的 data_gaps 或 counter_evidence。',
                '',
            ]
            for sib_role, sib_desc in sibling_briefs:
                lines.append(f'- **{sib_role}**：{sib_desc}')
            lines += [
                '',
                '⚠️ 不要越权展开其他维度的主章节。只在你自己维度的分析中做交叉引用或矛盾标注。',
                '',
            ]

    # ── Wave 交接：前一波子代理输出 ──
    if wave_inputs:
        lines += [
            '',
            '## 前一波输出参考（你必须先读完这些，再开始写）',
            '',
            '你是后续 Wave 的子代理，前面已经有其他维度的分析完成。'
            '**你的分析必须基于这些已有结论，不得与前置分析矛盾，也不得重复前置已有的分析。**',
            '',
        ]
        for slug, path in wave_inputs.items():
            if isinstance(path, str) and Path(path).exists():
                try:
                    rp = str(Path(path).relative_to(ROOT))
                except Exception:
                    rp = str(path)
                lines.append(f'- {slug}: `{rp}`')
        lines += [
            '',
            '### 使用规则',
            '- **只验证不重复**：前置分析已有明确结论的，你只需交叉验证，不需要重新论证',
            '- **只补充不覆盖**：前置分析有遗漏的，你补充；前置分析有误的，你标注"⚠与前置分析矛盾"并给出证据',
            '- **交叉引用**：你的输出中引用前置维度数据时，标注来源维度（如"据技术维度分析"）',
            '- **矛盾处理**：如果你发现前置分析的结论与你搜到的数据不一致，必须在报告中明确指出矛盾点并给出你的判断',
            '',
        ]

    lines += [
        '',
        '## ⚠️ 自主闭环规则',
        '',
        '你在执行过程中必须自主闭环，不要返回主控等待指示：',
        '1. **发现数据缺口** → 自己用正确工具补搜（工具使用指南见 System Prompt 末尾），继续推进',
        '2. **来源不足** → 自己搜更多来源，补充到输出中',
        '3. **数据矛盾** → 自己判断哪个更可靠，标注矛盾来源',
        '4. **唯一完成条件** → 输出文件写完',
        '',
        '## ⚠️ 搜索深度硬要求（宁滥勿缺 — 质量门禁会校验，不达标 = 任务失败）',
        '',
        '**原则：宁可多搜、多抓、多引，不可漏搜。泛搜一轮远远不够，必须多角度交叉验证。**',
        '',
        '**最低搜索量（section_package search_audit 会校验）：**',
        '- ≥ 8 个独立搜索 query（不同角度：公司名+维度关键词，中英文各搜）',
        '- ≥ 3 个实际深读过的 URL（search_deep(fetch_top_n) 抓到的正文，不是只看 snippet）',
        '- ≥ 3 个独立来源域名（不能全是同一个站点的页面）',
        '',
        '**搜索策略（必须执行）：**',
        '1. **第一轮：广度扫描** — 用 Bash 调 `cd {RUNTIME_ROOT} && python3 -c "from scripts.search_gateway import search; results = search(\'关键词\', prefer=\'multi\')"` 或 search_deep(Bash) 多关键词搜索',
        '2. **第二轮：深度验证** — 对第一轮发现的关键 claim，用 search_deep(Bash, fetch_top_n) 读全文验证',
        '3. **第三轮：交叉验证/反证** — 搜竞品对比、负面信息、行业报告',
        '4. **TYC 必查项**（中国大陆企业）：工商信息、司法诉讼、专利、资质、历史变更',
        '5. **金融数据必查**（如涉及上市公司/可比公司）：用 Bash 调 search_gateway 或 yfinance（见 System Prompt 工具指南）',
        '',
        '**禁止行为：**',
        '- 禁止只搜一轮就结束——泛搜一轮不够',
        '- 禁止只搜不读——搜到 URL 后必须用 search_deep(Bash, fetch_top_n) 读正文提取事实',
        '- 禁止只用通用搜索做所有搜索',
        '',
        '## 执行要求',
        '- 先读 OCR / Step0 / 工商验证 / Presearch / BP Research Plan / BP Fact Store，再补搜索。',
        '- **搜索时必须按 System Prompt 中的工具优先级选择工具**：金融数据用 search_gateway，企业工商/司法/专利用 TYC MCP，通用搜索用 search_deep(Bash)。',
        '- 补搜最多 3 轮，但 3 轮是上限不是目标——每轮必须有效产出新事实。',
        '- 仍搜不到的标注"经 X 次搜索未找到独立来源"，写入 data_gaps。',
        '- 直接把最终 Markdown 写到指定 output file。',
        '',
        '## ⚠️ 交付文件协议（最高优先级，缺少 sidecar = 任务未完成）',
        '',
        f'你必须一次性交付 3 个文件，任何一个缺失都算未完成：',
        f'1. Markdown 正文：`{output_display}`',
        f'2. 事实 sidecar：`{facts_display}`',
        f'3. 结构化 Section Package sidecar：`{section_display}`',
        '',
        '### 事实 sidecar schema',
        '写入 JSON，格式必须是：',
        '```json',
        json.dumps({
            'role': sub['role_name'],
            'facts': [{
                'fact_id': f'BP-{slug.upper()}-F001',
                'claim': '可被验证的事实断言',
                'value': '事实值；没有数值也要写定性事实值',
                'unit': '单位；无则为空字符串',
                'period': '事实对应时间/区间；未知则写待验证',
                'source_url': '外部来源URL；不得写内部文件路径',
                'source_tier': 'official/regulatory/database/media/research/bp/unknown',
                'source_quote': '来源原文摘录或可核验摘要',
                'question_id': f'{slug}_q1',
                'fact_type': slug,
                'confidence': 'high/medium/low',
            }],
        }, ensure_ascii=False, indent=2),
        '```',
        '- facts 每项必须包含：fact_id、claim、value、unit、period、source_url、source_tier、source_quote、question_id、fact_type、confidence。',
        '- fact_id 必须在本文件内唯一，推荐格式 `BP-<ROLE>-F001`。',
        '- 没有外部来源支撑的内容不要写入 facts；写入 Section Package 的 data_gaps。',
        '',
        '### Section Package sidecar schema',
        '写入 JSON，格式必须是：',
        '```json',
        json.dumps({
            'schema_version': 'bp_section_package.v2',
            'section_id': sub['role_name'],
            'section_title': sub['role_name'].replace('bp_', ''),
            'key_messages': ['本章节最重要的结论，必须有事实支撑'],
            'answers': [{
                'question_id': f'{slug}_q1',
                'answer': '对当前角色 owner 问题的直接回答',
                'fact_ids': [f'BP-{slug.upper()}-F001'],
                'confidence': 'high/medium/low',
                'limits': '该回答的边界、假设或仍需验证事项',
            }],
            'claim_ids_covered': ['来自 Research Plan Slice 的 claim_id，如 BC001'],
            'claims': [{
                'claim_id': '来自 Research Plan Slice 的 claim_id，如 BC001',
                'claim': '关键判断',
                'fact_ids': [f'BP-{slug.upper()}-F001'],
                'reasoning': '为什么这些事实支持该判断',
                'confidence': 'high/medium/low',
                'source_quality': 'official/regulatory/database/media/research/bp/unknown',
            }],
            'facts_used': [f'BP-{slug.upper()}-F001'],
            'counter_evidence': ['反面证据、限制条件或不确定性；没有也要写说明'],
            'data_gaps': ['无法验证或来源不足的断言；不要硬写成结论'],
            'search_audit': {
                'queries': [{
                    'query': '实际执行的搜索词，不得写泛泛占位',
                    'purpose': '该搜索要验证的问题/claim_id',
                    'result_count': 5,
                    'fetched_urls': ['https://example.com/deep-page'],
                }],
                'fetched_urls': ['实际用 search_deep(fetch_top_n) 深读过的 URL'],
                'source_domains': ['example.com'],
            },
            'narrative_blocks': [{
                'block_id': f'{slug}_NB001',
                'question_id': f'{slug}_q1',
                'claim_ids': ['来自 Research Plan Slice 的 claim_id，如 BC001'],
                'fact_ids': [f'BP-{slug.upper()}-F001'],
                'text': '可被最终统稿复用的叙事块，必须绑定 question_id / claim_ids / fact_ids',
            }],
            'markdown_draft': '可直接进入最终报告的正文草稿，必须与 Markdown 正文一致或为其核心摘录',
        }, ensure_ascii=False, indent=2),
        '```',
        '- schema_version 必须严格等于 `bp_section_package.v2`。',
        '- Section Package 必须包含：section_id、section_title、key_messages、answers、claim_ids_covered、claims、facts_used、counter_evidence、data_gaps、search_audit、narrative_blocks、markdown_draft。',
        '- search_audit 必须记录真实搜索行为：至少 8 个独立 query、至少 3 个正文深读/抓取 URL、至少 3 个独立来源域名；泛泛搜索、不 fetch 正文会被门禁判失败。',
        '- answers 每项必须包含 question_id、answer、fact_ids、confidence、limits。',
        '- claims 每项必须包含 claim_id、claim、fact_ids、reasoning、confidence、source_quality；claim_id 必须来自当前角色 Research Plan Slice。',
        '- narrative_blocks 每项必须包含 block_id、question_id、claim_ids、fact_ids、text，用于最终统稿按投资决策链重写。',
        '- 所有关键判断都要绑定已写入 facts sidecar 的 fact_id；缺事实就写入 data_gaps，禁止虚构 fact_id。',
        '- 写完后自检：Markdown 存在、facts sidecar 存在、section sidecar 存在、search_audit 达标、answers/claims/narrative_blocks 的 fact_ids 都能在 facts 中找到。',
    ]

    brief_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return brief_path


def _read_brief_content(brief_path: Path) -> str:
    if not brief_path.exists():
        return ''
    text = brief_path.read_text(encoding='utf-8')
    return text[:60000]


def _spawn_one(task_id: str, sub: dict, task_dir: Path | None = None) -> dict:
    task_dir = task_dir or TASKS_DIR / task_id
    slug = _slug(sub['role_name'])
    output_path = Path(sub['output_file'])
    receipt_path = task_dir / f'bp_phase2_spawn_{slug}.json'
    manifest_path = task_dir / f'bp_phase2_manifest_{slug}.json'

    if output_path.exists() and output_path.stat().st_size > 50:
        return {'role': sub['role_name'], 'status': 'already_exists', 'output': str(output_path)}

    brief_path = _build_brief(task_id, sub, task_dir=task_dir)
    label = f'{task_id}-bp-phase2-{slug}'
    brief_content = _read_brief_content(brief_path)
    if not brief_content:
        brief_content = (
            f'Role: {sub["role_name"]}\n'
            f'Task ID: {task_id}\n'
            'Complete due diligence analysis for your assigned dimension.'
        )

    sidecar_paths = _sidecar_paths(output_path)
    system_prompt = ROLE_SYSTEM_PROMPTS.get(
        sub['role_name'],
        f"UNKNOWN BP ROLE: {sub['role_name']}. No instruction-store prompt is registered. "
        "Do not reuse another BP role prompt; stop and report this configuration gap.",
    )
    # ── Mandatory conclusion / verdict appendix (appended to ALL roles) ──
    _CONCLUSION_APPENDIX = (
        '\n\n## ⚠️ 强制结论规则（最高优先级 — 所有维度通用）\n'
        '你必须在输出末尾包含一个独立的"本维度结论"章节，格式如下：\n\n'
        '### 本维度结论\n'
        '对每个必须回答的核心问题，给出：\n'
        '1. **一句话结论**（不超过50字，直接回答"是/否/部分成立/无法判断"）\n'
        '2. **置信度**：高（多源交叉验证）/ 中（单一可信来源）/ 低（仅BP自述或推断）\n'
        '3. **关键证据摘要**（1-2句，引用最重要的事实或数据）\n'
        '4. **如果置信度为中或低**：明确列出"需要补充什么信息才能升级到更高置信度"\n\n'
        '示例格式：\n'
        '> **结论：XX技术在国内赛道属于第二梯队，不具有领先性（置信度：高）**\n'
        '> 竞品A出货量7亿颗 vs 标的数千万颗，差距约10倍（来源：百度百家号、爱企查）。\n\n'
        '> **结论：HCSEL路线具有独创性但市场验证严重不足（置信度：中）**\n'
        '> 行业无其他厂商布局HCSEL，但无客户应用案例。需补充：客户导入进度、出货数据。\n\n'
        '⚠️ 禁止只罗列事实不给结论。每个问题的结论行必须出现"置信度"三个字。\n'
        '⚠️ 如果信息不足以形成判断，结论写"无法判断"并列出 data_gaps，不要强行给结论。\n'
    )
    # ── Tool usage guide: _TOOL_USAGE_GUIDE 现从 instruction_store_bp/_common_tool_guide.md 加载（模块级） ──
    # ── P1-5: 融资阶段感知注入 ──────────────────────────
    _stage_block = ""
    try:
        from scripts.bp_stage_utils import classify_stage, build_stage_prompt_block
        _profile_path = task_dir / "bp_step0_profile.json"
        if _profile_path.exists():
            _profile = json.loads(_profile_path.read_text(encoding="utf-8"))
            _stage_tier = classify_stage(_profile.get("financing_stage", ""))
            _entity_name = _profile.get("entity", "") or task_dir.name
            _stage_block = "\n\n" + build_stage_prompt_block(_stage_tier, _entity_name)
    except Exception:
        pass

    system_prompt = system_prompt + _CONCLUSION_APPENDIX + _TOOL_USAGE_GUIDE + _stage_block
    # ── 企业数据 MCP connector IDs（天眼查） ──
    
    manifest_data = {
        'task_id': task_id,
        'role': sub['role_name'],
        'slug': slug,
        'label': label,
        'system_prompt': system_prompt,
        'brief_path': str(brief_path),
        'brief_content_preview': brief_content[:2000],
        'key_inputs': sub.get('key_inputs', {}),
        'wave_inputs': sub.get('wave_inputs', {}),
        'output_path': str(output_path),
        'sidecar_paths': {name: str(path) for name, path in sidecar_paths.items()},
        'timeout': 1200,
        'thinking': 'high',
        'dispatch_mode': 'team_async',
        'mode': 'bypassPermissions',
        'subagent_type': 'general-purpose',
        'team_name_template': 'bp-{task_id}',
        'connectorIds': BP_ROLE_CONNECTOR_IDS.get(sub['role_name'], BP_TYC_CONNECTOR_IDS),
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'status': 'pending',
    }
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')

    receipt = {
        'task_id': task_id,
        'role': sub['role_name'],
        'label': label,
        'status': 'dispatched',
        'runId': f'wb-bp-{int(time.time())}',
        'childSessionKey': f'wb-bp-{task_id}-{slug}',
        'runtime': 'workbuddy-task',
        'thinking': 'high',
        'manifest_path': str(manifest_path),
        'output_path': str(output_path),
        'sidecar_paths': {name: str(path) for name, path in sidecar_paths.items()},
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'  📋 已派发 BP 子代理: {sub["role_name"]} → manifest: {manifest_path.name}')

    return {
        'role': sub['role_name'],
        'status': 'dispatched',
        'label': label,
        'runId': receipt['runId'],
        'childSessionKey': receipt['childSessionKey'],
        'output': str(output_path),
        'sidecar_paths': {name: str(path) for name, path in sidecar_paths.items()},
        'receipt': str(receipt_path),
        'manifest_path': str(manifest_path),
    }


# ── PR4: 质量门禁 + 重写 + launch_and_verify 闭环节 ───────────

def _check_role_quality(role_name: str, task_dir: Path, output_path: Path) -> dict:
    """PR4: 按 bp_section_package.v2 schema 校验 role 输出质量。

    校验项（与 IR `_check_step_quality` 对齐）：
      1. Markdown 正文存在且 >200 字符
      2. facts sidecar 存在，schema 完整
      3. section sidecar 存在，schema_version == 'bp_section_package.v2'
      4. search_audit 满足：≥8 query、≥3 fetched_urls、≥3 独立 source_domains
      5. claim_ids_covered 至少 1 个
      6. answers/claims/narrative_blocks 的 fact_ids 都能在 facts 中找到
    """
    errors: list[str] = []
    details: dict = {}

    if not output_path.exists():
        return {'passed': False, 'score': 0.0, 'errors': ['output_markdown_missing'], 'details': details}

    md_text = output_path.read_text(encoding='utf-8')
    if len(md_text) < 200:
        errors.append('output_markdown_too_short(<200 chars)')

    sidecars = _sidecar_paths(output_path)
    facts_path = sidecars['facts']
    section_path = sidecars['section_package']

    if not facts_path.exists():
        errors.append('facts_sidecar_missing')
        facts_data: dict = {}
    else:
        try:
            facts_data = json.loads(facts_path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'facts_sidecar_invalid_json: {exc}')
            facts_data = {}

    if not section_path.exists():
        errors.append('section_sidecar_missing')
        section_data: dict = {}
    else:
        try:
            section_data = json.loads(section_path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'section_sidecar_invalid_json: {exc}')
            section_data = {}

    required_fact_keys = {'fact_id', 'claim', 'value', 'unit', 'period', 'source_url', 'source_tier', 'source_quote', 'question_id', 'fact_type', 'confidence'}
    facts = facts_data.get('facts', []) if isinstance(facts_data, dict) else []
    if not facts:
        errors.append('facts_list_empty')
    fact_ids: set[str] = set()
    for idx, f in enumerate(facts):
        if not isinstance(f, dict):
            continue
        missing = required_fact_keys - set(f.keys())
        if missing:
            errors.append(f'facts[{idx}] missing keys: {sorted(missing)}')
        if not f.get('source_url'):
            errors.append(f"facts[{idx}] missing source_url (fact_id={f.get('fact_id', '?')})")
        fid = f.get('fact_id', '')
        if fid:
            fact_ids.add(fid)
    details['fact_count'] = len(facts)
    details['fact_id_set_size'] = len(fact_ids)

    if isinstance(section_data, dict):
        sv = section_data.get('schema_version', '')
        if sv != 'bp_section_package.v2':
            errors.append(f"section.schema_version != 'bp_section_package.v2' (got {sv!r})")

        sa = section_data.get('search_audit') or {}
        queries = sa.get('queries', []) if isinstance(sa, dict) else []
        fetched = sa.get('fetched_urls', []) if isinstance(sa, dict) else []
        domains = sa.get('source_domains', []) if isinstance(sa, dict) else []

        if len(queries) < 8:
            errors.append(f'search_audit.queries < 8 (got {len(queries)})')
        if len(fetched) < 3:
            errors.append(f'search_audit.fetched_urls < 3 (got {len(fetched)})')
        if len(set(domains)) < 3:
            errors.append(f'search_audit.source_domains < 3 unique (got {len(set(domains))})')

        details['search_audit_queries'] = len(queries)
        details['search_audit_fetched_urls'] = len(fetched)
        details['search_audit_domains'] = len(set(domains))

        for blk_name in ('answers', 'claims', 'narrative_blocks'):
            blocks = section_data.get(blk_name, []) or []
            for i, b in enumerate(blocks):
                if not isinstance(b, dict):
                    continue
                b_fids = b.get('fact_ids', []) or []
                for fid in b_fids:
                    if fid not in fact_ids:
                        errors.append(f'{blk_name}[{i}].fact_ids contains unknown {fid!r}')

        if not section_data.get('claim_ids_covered'):
            errors.append('section.claim_ids_covered empty')
    else:
        errors.append('section_package_not_dict')

    passed = len(errors) == 0
    score = 1.0 - (len(errors) / 12.0)

    return {
        'passed': passed,
        'score': max(0.0, round(score, 3)),
        'errors': errors,
        'details': details,
    }


def _rewrite_role(task_id: str, role_name: str, task_dir: Path, followup_memo_path: str) -> bool:
    """PR4: 把补搜 memo 注入到 role manifest，让子代理重读后重写。"""
    slug = _slug(role_name)
    manifest_path = task_dir / f'bp_phase2_manifest_{slug}.json'
    if not manifest_path.exists():
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception:
        return False

    hint_lines = [
        '',
        '## PR4 补搜 Memo 引用（请重新阅读后再修改 output）',
        f'- Memo: `{followup_memo_path}`',
        '- 阅读 memo 中标注的真实搜索结果，把缺失的事实 / 弱来源的判断补到 facts sidecar + section sidecar。',
        '- 重新校验 search_audit.queries ≥8 / fetched_urls ≥3 / source_domains ≥3。',
    ]
    manifest['brief_content_preview'] = (manifest.get('brief_content_preview') or '') + '\n'.join(hint_lines)
    manifest['status'] = 'rewrite_pending'
    manifest['rewritten_at'] = datetime.now().isoformat(timespec='seconds')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def launch_and_verify(
    task_id: str,
    role_name: str,
    task_dir: Path | None = None,
    retries: int = 0,
    entity: str = '',
) -> dict:
    """PR4: 单 role 派发 + 质量门禁 + 补搜重写闭环节。"""
    task_dir = task_dir or TASKS_DIR / task_id
    dispatch_path = task_dir / 'phase2_dispatch.json'
    if not dispatch_path.exists():
        return {'status': 'no_dispatch', 'task_id': task_id, 'role': role_name}

    try:
        dispatch = json.loads(dispatch_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'status': 'dispatch_invalid_json', 'task_id': task_id, 'role': role_name, 'error': str(exc)}

    sub = next(
        (s for s in (dispatch.get('subagents') or []) if s.get('role_name') == role_name),
        None,
    )
    if not sub:
        return {'status': 'role_not_in_dispatch', 'task_id': task_id, 'role': role_name}

    output_path = Path(sub['output_file'])
    history: list[dict] = []

    spawn_result = _spawn_one(task_id, sub, task_dir=task_dir)
    history.append({'step': 'spawn', 'result': spawn_result})

    quality: dict = {}
    passed = False
    retries_used = 0

    quality = _check_role_quality(role_name, task_dir, output_path)
    history.append({'step': 'check_quality', 'attempt': 0, 'quality': quality})
    passed = quality['passed']

    for attempt in range(1, retries + 1):
        if passed:
            break
        retries_used = attempt
        entity_for_search = entity or sub.get('description', '') or role_name
        search_result = do_supplementary_search(task_id, role_name, entity_for_search)
        history.append({'step': 'do_supplementary_search', 'attempt': attempt, 'result': search_result})

        if search_result.get('memo_path'):
            _rewrite_role(task_id, role_name, task_dir, search_result['memo_path'])
            history.append({'step': 'rewrite_role_manifest', 'attempt': attempt, 'role': role_name})

        quality = _check_role_quality(role_name, task_dir, output_path)
        history.append({'step': 'check_quality', 'attempt': attempt, 'quality': quality})
        passed = quality['passed']

    return {
        'status': 'ok' if passed else 'quality_below_threshold',
        'role': role_name,
        'task_id': task_id,
        'dispatched': spawn_result.get('status') in ('dispatched', 'already_exists'),
        'passed': passed,
        'retries_used': retries_used,
        'quality': quality,
        'history': history,
    }


def get_pending_bp_tasks(task_id: str) -> list[dict]:
    task_dir = TASKS_DIR / task_id
    pending = []
    for role_name, slug in ROLE_TO_KEY.items():
        manifest_path = task_dir / f'bp_phase2_manifest_{slug}.json'
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
            output_path = Path(data.get('output_path', ''))
            if not output_path.exists() and data.get('status') == 'pending':
                pending.append(data)
    return pending


def main():
    ap = argparse.ArgumentParser(description='BP Phase 2 Subagent Launcher — WorkBuddy 版 v4 + PR4 quality gate (Task 子代理)')
    ap.add_argument('--task-id', required=True)
    ap.add_argument('--pending', action='store_true', help='List pending BP tasks for Task dispatch')
    ap.add_argument('--role', help='PR4: target role key (e.g. bp_valuation_return)')
    ap.add_argument('--check-quality', action='store_true', help='PR4: check quality of a single role (requires --role)')
    ap.add_argument('--do-search', action='store_true', help='PR4: run supplementary search for a single role (requires --role)')
    ap.add_argument('--launch-and-verify', action='store_true', help='PR4: dispatch role + check-quality + retry loop (requires --role)')
    ap.add_argument('--retries', type=int, default=0, help='PR4: max retries of quality-gated loop (default 0)')
    ap.add_argument('--entity', default='', help='PR4: entity name for supplementary search')
    args = ap.parse_args()

    if args.pending:
        pending = get_pending_bp_tasks(args.task_id)
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return

    task_dir = TASKS_DIR / args.task_id

    # PR4: 单 role 质量检查（不派发）
    if args.check_quality:
        if not args.role:
            print(json.dumps({'status': 'role_required', 'task_id': args.task_id}, ensure_ascii=False))
            raise SystemExit(2)
        dispatch_path = task_dir / 'phase2_dispatch.json'
        if not dispatch_path.exists():
            print(json.dumps({'status': 'no_dispatch', 'task_id': args.task_id}, ensure_ascii=False))
            raise SystemExit(1)
        dispatch = json.loads(dispatch_path.read_text(encoding='utf-8'))
        sub = next((s for s in (dispatch.get('subagents') or []) if s.get('role_name') == args.role), None)
        if not sub:
            print(json.dumps({'status': 'role_not_in_dispatch', 'task_id': args.task_id, 'role': args.role}, ensure_ascii=False))
            raise SystemExit(3)
        output_path = Path(sub['output_file'])
        quality = _check_role_quality(args.role, task_dir, output_path)
        print(json.dumps({'task_id': args.task_id, 'role': args.role, 'quality': quality}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if quality['passed'] else 4)

    # PR4: 单 role 补搜
    if args.do_search:
        if not args.role:
            print(json.dumps({'status': 'role_required', 'task_id': args.task_id}, ensure_ascii=False))
            raise SystemExit(2)
        entity = args.entity or args.role
        result = do_supplementary_search(args.task_id, args.role, entity)
        print(json.dumps({'task_id': args.task_id, 'role': args.role, 'supplementary': result}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get('has_results') else 5)

    # PR4: 派发 + 校验 + 重试循环
    if args.launch_and_verify:
        if not args.role:
            print(json.dumps({'status': 'role_required', 'task_id': args.task_id}, ensure_ascii=False))
            raise SystemExit(2)
        result = launch_and_verify(args.task_id, args.role, task_dir=task_dir, retries=args.retries, entity=args.entity)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get('passed') else 6)

    dispatch_path = task_dir / 'phase2_dispatch.json'
    if not dispatch_path.exists():
        print(json.dumps({'status': 'no_dispatch', 'task_id': args.task_id}, ensure_ascii=False))
        raise SystemExit(1)

    dispatch = json.loads(dispatch_path.read_text(encoding='utf-8'))
    subs = dispatch.get('subagents', [])
    results = []
    for sub in subs:
        results.append(_spawn_one(args.task_id, sub))
        time.sleep(1)

    dispatched = sum(1 for r in results if r.get('status') == 'dispatched')
    ok = all(r.get('status') in ('dispatched', 'already_exists') for r in results)

    if dispatched > 0:
        notify_wx(f'🐲 BP Phase2 已派发\n任务: {args.task_id}\n已派发: {dispatched}/{len(subs)} roles\n运行时: WorkBuddy Task')

    print(json.dumps({
        'task_id': args.task_id,
        'status': 'ok' if ok else 'partial',
        'results': results,
        'runtime': 'workbuddy-task',
        'pending_tasks': dispatched,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 2)




_BP_SEARCH_TEMPLATES = {
    'bp_company_team_compliance': [
        '"{entity}" founder background legal compliance litigation',
        '"{entity}" management governance ownership financing history',
        '"{entity}" 创始人 股权 合规 诉讼 管理层',
    ],
    'bp_product_commercial': [
        '"{entity}" product customers contracts orders revenue',
        '"{entity}" product commercialization customer case delivery',
        '"{entity}" 产品 客户 订单 合同 回款 商业化',
    ],
    'bp_tech_ip_moat': [
        '"{entity}" technology patent certification test report',
        '"{entity}" technical roadmap IP R&D moat',
        '"{entity}" 技术路线 专利 认证 测试报告 壁垒',
    ],
    'bp_market_supply_chain': [
        '"{entity}" industry supply chain market landscape',
        '"{entity}" upstream downstream suppliers capacity policy',
        '"{entity}" 行业 供应链 市场 产业链 产能 政策',
    ],
    'bp_competition_positioning': [
        '"{entity}" competitors market share differentiation',
        '"{entity}" alternative solutions competitive landscape',
        '"{entity}" 竞品 竞争格局 市场份额 差异化',
    ],
    'bp_valuation_return': [
        '"{entity}" financing valuation comparable companies transaction',
        '"{entity}" funding round valuation exit IPO acquisition',
        '"{entity}" 融资 估值 可比公司 退出 并购 IPO',
    ],
    'bp_dealbreaker_risk': [
        '"{entity}" risk litigation debt default qualification',
        '"{entity}" controversy negative news supply chain risk',
        '"{entity}" 风险 诉讼 债务 资质 负面 供应链',
    ],
    # v4.5 新增：投资叙事层（Wave 4）
    'bp_consensus_challenge': [
        '"{entity}" analyst consensus market expectation sell side',
        '"{entity}" expectation gap contrarian view consensus vs reality',
        '"{entity}" 卖方共识 预期差 市场定价 非共识',
        # IMA: 长安投研 + 机构调研纪要（搜卖方共识、外资非共识、预期差案例）
    ],
    'bp_catalyst': [
        '"{entity}" catalyst event timeline milestone trigger',
        '"{entity}" upcoming event policy product launch earnings',
        '"{entity}" 催化剂 事件 时间窗口 里程碑 触发器',
        # IMA: 长安投研 + 机构调研纪要（搜催化事件时间线、政策落地节奏）
    ],
    'bp_industry_research': [
        '"{entity}" industry report market size TAM SAM deep research',
        '"{entity}" supply chain cost structure technology roadmap benchmark',
        '"{entity}" 行业研报 市场规模 技术路线 成本结构 产业链',
        # IMA: 行研智库 + 精选报告 + 机构调研纪要（搜行业深度研报、第三方白皮书）
    ],
    'bp_团队与合规': [
        '"{entity}" founder background legal compliance litigation',
        '"{entity}" management governance ownership',
        '"{entity}" 创始人 合规 诉讼 管理层',
    ],
    'bp_技术与产品': [
        '"{entity}" technology product patent R&D',
        '"{entity}" product roadmap customer feedback',
        '"{entity}" 技术 产品 专利 研发',
    ],
    'bp_行业与供应链': [
        '"{entity}" industry supply chain market landscape',
        '"{entity}" upstream downstream suppliers customers',
        '"{entity}" 行业 供应链 市场 产业链',
    ],
    'bp_估值': [
        '"{entity}" financing valuation comparable companies transaction',
        '"{entity}" funding round valuation exit IPO acquisition',
        '"{entity}" 融资 估值 可比公司 退出 并购 IPO',
    ],
    'bp_竞争与结论': [
        '"{entity}" competitors market share differentiation',
        '"{entity}" risk analysis investment thesis',
        '"{entity}" 竞争格局 市场份额 风险 投资逻辑',
    ],
}


def do_supplementary_search(task_id: str, role_name: str, entity: str) -> dict:
    """供主控在BP子代理质量不达标时执行补搜。"""
    templates = _BP_SEARCH_TEMPLATES.get(role_name, [])
    if not templates:
        return {'role': role_name, 'memo_path': '', 'has_results': False}

    sys.path.insert(0, str(ROOT / 'scripts'))
    from search_gateway import search as gateway_search

    memo_lines = []
    seen_urls: set[str] = set()
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
        memo_lines.append(f"## BP 补搜结果 ({len(collected)} 条)\n\n")
        for i, (query, row) in enumerate(collected[:12], 1):
            title = row.get('title', '') or ''
            url = row.get('url', '') or ''
            snippet = row.get('content', '') or row.get('snippet', '') or ''
            engine = row.get('engine', '?')
            memo_lines.append(f"### {i}. [{engine}] {title}\n")
            memo_lines.append(f"Query: {query}\n")
            memo_lines.append(f"URL: {url}\n")
            memo_lines.append(f"{snippet[:300]}\n\n")

    slug = _slug(role_name)
    memo_path = TASKS_DIR / task_id / f'bp_phase2_followup_{slug}.md'
    if memo_lines:
        memo_path.write_text(''.join(memo_lines), encoding='utf-8')
        return {'role': role_name, 'memo_path': str(memo_path), 'has_results': True}
    return {'role': role_name, 'memo_path': '', 'has_results': False}
