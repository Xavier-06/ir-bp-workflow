#!/usr/bin/env python3
"""
IR 质量评估标准 — IR 管线和交叉验证模块共享
从 run_ir_pipeline.py 提取，避免循环依赖。
"""
from __future__ import annotations
import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
TASKS_DIR_IR = WORKSPACE / 'data' / 'tasks'

# step 清单从调度层单一真相源（ir_subagent_launcher_wb.STEP_DEPS）动态派生，
# 避免删/加 step 时多处硬编码漏改（v3.6 教训：step1_data/step8_master 化石残留）。
# 派生失败（如循环依赖/文件缺失）回退到 7-step 快照。
_FALLBACK_STEP_ORDER = [
    'step1_industry', 'step2_biz',
    'step3_finance', 'step4_mgmt', 'step7_insight',
    'step6_valuation', 'step8_risk',
]
try:
    from scripts.ir_subagent_launcher_wb import STEP_DEPS as _LAUNCHER_STEP_DEPS
    STEP_ORDER = list(_LAUNCHER_STEP_DEPS)
except Exception:
    STEP_ORDER = list(_FALLBACK_STEP_ORDER)

STEP_NAMES = {
    'step1_industry': '行业与市场格局',
    'step2_biz': '业务模式',
    'step3_finance': '财务分析',
    'step4_mgmt': '管理与治理',
    'step7_insight': '投资洞察',
    'step6_valuation': '预测与估值',
    'step8_risk': '风险提示',
    'step8_master': '统稿',
}

MIN_OVERALL_SCORE = max(1, len(STEP_ORDER) * 3 * 2 // 3)  # 每维 0-3，≥2/3 满分才达标（随 step 数动态缩放，不硬编码）

RED_FLAGS = ['待补', '待填', 'TODO', '无法验证', '无法获取', '需要进一步', '[待补]']

OFFICIAL_DOMAINS = ['sec.gov', 'hkexnews.hk', 'cninfo.com.cn', 'szse.cn', 'sse.com.cn',
                    'ir.', 'investor.', 'investors.']
REPUTABLE_DOMAINS = ['reuters.com', 'bloomberg.com', 'wsj.com', 'ft.com', 'economist.com',
                     'scmp.com', 'caixin.com', '36kr.com', 'cls.cn', 'eastmoney.com',
                     'xueqiu.com', 'zhihu.com', 'wikipedia.org']


def check_step_quality(text: str) -> int:
    """单 step 质量评分 (0-3)。"""
    sz = len(text)
    if sz < 200:
        return 0

    text_lower = text.lower()
    official_count = sum(1 for d in OFFICIAL_DOMAINS if d in text_lower)
    reputable_count = sum(1 for d in REPUTABLE_DOMAINS if d in text_lower)
    url_count = text.count('http')

    if official_count >= 2 and sz > 2000:
        score = 3
    elif (official_count >= 1 or reputable_count >= 2) and sz > 1000:
        score = 2
    elif url_count >= 1:
        score = 1
    else:
        score = 0

    flags = sum(1 for flag in RED_FLAGS if flag in text)
    if flags >= 3 and score > 1:
        score = max(1, score - 1)

    return score


def quality_gate_results(task_id: str, step_order=None, step_names=None,
                         min_score=None, tasks_dir=None) -> dict:
    """
    通用质量门禁。从 run_ir_pipeline.py 的 _quality_gate_results 提取。
    参数全部可选，使用模块默认值。
    """
    if step_order is None:
        step_order = STEP_ORDER
    if step_names is None:
        step_names = STEP_NAMES
    if min_score is None:
        min_score = MIN_OVERALL_SCORE
    if tasks_dir is None:
        tasks_dir = TASKS_DIR_IR

    scores = {}
    issues = []
    for step in step_order:
        fpath = tasks_dir / f'{task_id}-{step}.md'
        if not fpath.exists():
            scores[step] = 0
            issues.append(f"❰{step}❱ 文件缺失")
            continue

        text = fpath.read_text(encoding='utf-8')
        sz = len(text)
        if sz < 200:
            scores[step] = 0
            issues.append(f"❰{step}❱ 内容过短 ({sz} 字符)")
            continue

        score = check_step_quality(text)

        flags = sum(1 for flag in RED_FLAGS if flag in text)
        if flags >= 3 and score > 1:
            issues.append(f"❰{step}❱ {flags} 处红旗标记")

        scores[step] = score
        if score < 2:
            label = step_names.get(step, step)
            text_lower = text.lower()
            official_count = sum(1 for d in OFFICIAL_DOMAINS if d in text_lower)
            reputable_count = sum(1 for d in REPUTABLE_DOMAINS if d in text_lower)
            url_count = text.count('http')
            issues.append(f"❰{label}❱ 得分 {score}/3"
                         f"(官方={official_count}, 权威={reputable_count}, URL={url_count})")

    total = sum(scores.values())
    return {
        'scores': scores,
        'total': total,
        'max_possible': len(step_order) * 3,
        'passed': total >= min_score,
        'issues': issues,
        'threshold': min_score,
    }


def _issue(severity: str, code: str, message: str, step: str = "") -> dict:
    payload = {"severity": severity, "code": code, "message": message}
    if step:
        payload["step"] = step
    return payload


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def run_step_gate(task_id: str, step_order=None, tasks_dir=None,
                  min_chars: int = 500, min_urls: int = 3) -> dict:
    if step_order is None:
        step_order = STEP_ORDER
    tasks_dir = Path(tasks_dir or TASKS_DIR_IR)
    issues = []
    steps = {}
    for step in step_order:
        md_path = tasks_dir / f'{task_id}-{step}.md'
        facts_path = tasks_dir / f'{task_id}-{step}-facts.json'
        section_path = tasks_dir / f'{task_id}-{step}-section.json'
        text = md_path.read_text(encoding='utf-8') if md_path.exists() else ''
        step_payload = {
            'markdown_exists': md_path.exists(),
            'facts_sidecar_exists': facts_path.exists(),
            'section_sidecar_exists': section_path.exists(),
            'content_length': len(text),
            'url_count': text.count('http'),
        }
        if not md_path.exists():
            issues.append(_issue('FAIL', 'MISSING_MARKDOWN', f'{step} markdown output is missing', step))
        elif len(text) < min_chars:
            issues.append(_issue('FAIL', 'MARKDOWN_TOO_SHORT', f'{step} markdown is too short: {len(text)}', step))
        if md_path.exists() and text.count('http') < min_urls:
            issues.append(_issue('FAIL', 'SOURCE_URL_INSUFFICIENT', f'{step} has fewer than {min_urls} source URLs', step))
        if not facts_path.exists():
            issues.append(_issue('FAIL', 'MISSING_FACTS_SIDECAR', f'{step} facts sidecar is missing', step))
        if not section_path.exists():
            issues.append(_issue('FAIL', 'MISSING_SECTION_SIDECAR', f'{step} section sidecar is missing', step))
        steps[step] = step_payload
    output = {
        'task_id': task_id,
        'gate': 'step',
        'passed': not any(issue['severity'] == 'FAIL' for issue in issues),
        'issues': issues,
        'steps': steps,
    }
    (tasks_dir / f'{task_id}-step_gate.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output


def run_section_gate(task_id: str, tasks_dir=None) -> dict:
    tasks_dir = Path(tasks_dir or TASKS_DIR_IR)
    fact_index_path = tasks_dir / f'{task_id}-fact_store_index.json'
    packages_path = tasks_dir / f'{task_id}-section_packages.json'
    index = _read_json(fact_index_path, {}) or {}
    known_fact_ids = set(index.get('fact_ids', []) or [])
    packages_index = _read_json(packages_path, {}) or {}
    issues = []
    if not fact_index_path.exists():
        issues.append(_issue('FAIL', 'MISSING_FACT_STORE_INDEX', 'Fact Store index is missing'))
    if not packages_path.exists():
        issues.append(_issue('FAIL', 'MISSING_SECTION_PACKAGES', 'Section packages index is missing'))
    elif not packages_index.get('packages'):
        issues.append(_issue('FAIL', 'EMPTY_SECTION_PACKAGES', 'Section packages index has no packages'))
    for item in packages_index.get('packages', []) or []:
        step = item.get('step_name', '')
        validation = item.get('validation', {}) or {}
        for validation_issue in validation.get('issues', []) or []:
            severity = 'FAIL' if validation_issue.get('severity') == 'FAIL' else 'WARN'
            issues.append(_issue(severity, validation_issue.get('code', 'SECTION_VALIDATION_ISSUE'), validation_issue.get('message', 'Section validation issue'), step))
        package = item.get('package', {}) or {}
        for idx, claim in enumerate(package.get('claims', []) or []):
            for fact_id in claim.get('fact_ids', []) or []:
                if fact_id not in known_fact_ids:
                    issues.append(_issue('FAIL', 'UNKNOWN_FACT_ID', f'Claim {idx} references unknown fact_id: {fact_id}', step))
            if claim.get('confidence') == 'high' and claim.get('source_quality') in ('unknown', 'low', 'auxiliary'):
                issues.append(_issue('FAIL', 'HIGH_CONFIDENCE_WEAK_SOURCE', f'Claim {idx} has high confidence with weak source quality', step))
        if not package.get('counter_evidence'):
            issues.append(_issue('WARN', 'MISSING_COUNTER_EVIDENCE', 'Section lacks counter evidence', step))
    output = {
        'task_id': task_id,
        'gate': 'section',
        'passed': not any(issue['severity'] == 'FAIL' for issue in issues),
        'issues': issues,
        'known_fact_count': len(known_fact_ids),
    }
    (tasks_dir / f'{task_id}-section_gate.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output


def _check_number_consistency(text: str) -> list[dict]:
    """数字自洽校验（2026-08-06，中天研报复盘新增）。

    设计原则——定义唯一可复算的矛盾才 FAIL 阻断，定义有歧义的记 WARN：
      - 概率加权目标价复算（定义唯一）：偏差 >10% → FAIL
      - 情景三档单调性（牛>基准>熊）：违反 → WARN
      - R/R 比值复算（下行价口径有歧义）：偏离任何合理口径 >50% → WARN
      - "自有 DCF" 借鉴声明缺失 → WARN
    提取失败一律跳过，不臆断。
    """
    import re
    issues = []

    # ── 1. 概率加权目标价复算：「概率加权 X 元（牛 p1%/v1、基准 p2%/v2、熊 p3%/v3…）」──
    m = re.search(r"概率加权[目标价 ]*([\d.]+)\s*元?\s*[（(]([^）)]+)[)）]", text)
    weighted_claim = None
    if m:
        weighted_claim = float(m.group(1))
        body = m.group(2)
        # 两种写法：「牛 20%/61」「牛 61×20%」
        pairs = re.findall(r"(牛|基准|熊|极端|乐观|悲观)\s*([\d.]+)\s*[%％]\s*[/／]\s*([\d.]+)", body)
        pairs = [(name, float(p) / 100.0, float(v)) for name, p, v in pairs]
        if not pairs:
            pairs2 = re.findall(r"(牛|基准|熊|极端|乐观|悲观)\s*([\d.]+)\s*[×x*]\s*([\d.]+)\s*[%％]", body)
            pairs = [(name, float(p) / 100.0, float(v)) for name, v, p in pairs2]
        if pairs and weighted_claim:
            calc = sum(prob * val for _, prob, val in pairs)
            prob_sum = sum(prob for _, prob, _ in pairs)
            if prob_sum > 0 and abs(calc - weighted_claim) / weighted_claim > 0.10:
                issues.append(_issue(
                    'FAIL', 'NUMBER_INCONSISTENT',
                    f'概率加权目标价自相矛盾：声称 {weighted_claim} 元，按报告自给情景概率复算 = {calc:.1f} 元'
                    f'（偏差 {abs(calc - weighted_claim) / weighted_claim * 100:.0f}%）'))

    # ── 2. 情景三档单调性：牛 > 基准 > 熊 ──
    if m:
        body = m.group(2)
        vals = {}
        for name, _, v in re.findall(r"(牛|基准|熊|极端|乐观|悲观)\s*([\d.]+)\s*[%％]\s*[/／]\s*([\d.]+)", body):
            vals[name] = float(v)
        bull = vals.get("牛", vals.get("乐观"))
        bear = vals.get("熊", vals.get("悲观"))
        base = vals.get("基准")
        if bull is not None and base is not None and bear is not None:
            if not (bull > base > bear):
                issues.append(_issue(
                    'WARN', 'SCENARIO_NOT_MONOTONIC',
                    f'情景目标价不单调：牛 {bull} / 基准 {base} / 熊 {bear}，应为牛>基准>熊'))

    # ── 3. R/R 复算（下行价口径有歧义，只记 WARN）──
    rr_claims = [float(x) for x in re.findall(r"R/?R\s*[≈~]*\s*([\d.]+)\s*[:：]\s*1", text)]
    rr_range = re.findall(r"R/?R\s*[≈~]*\s*([\d.]+)\s*[–\-~至]\s*([\d.]+)\s*[:：]?1", text)
    for lo, hi in rr_range:
        rr_claims += [float(lo), float(hi)]
    if rr_claims:
        # 提取现价与情景价，构造所有合理 R/R 口径
        price_m = re.findall(r"现价\s*[^\d]{0,6}([\d.]+)\s*元", text)
        prices = [float(p) for p in price_m if 1 < float(p) < 100000]
        scen_prices = []
        if m:
            scen_prices = [float(v) for _, _, v in
                           re.findall(r"(牛|基准|熊|极端|乐观|悲观)\s*([\d.]+)\s*[%％]\s*[/／]\s*([\d.]+)", m.group(2))]
        # 显式标注口径：「下行 28 / 上行 49」「下行 28、上行 49」——报告自己声明的上下行价，
        # 是复算 R/R 的最强口径（无歧义）
        explicit = re.findall(r"下行\s*([\d.]+)\s*[/、，,]?\s*(?:base\s*)?上行\s*([\d.]+)", text)
        recal = []
        explicit_recal = []
        cur = max(prices) if prices else None
        for down_s, up_s in explicit:
            down, up = float(down_s), float(up_s)
            if cur and up > cur > down:
                explicit_recal.append((up - cur) / (cur - down))
        recal = explicit_recal[:]
        # 兜底口径：情景价组合（上下行选择有歧义）
        if not recal and cur and scen_prices:
            for up in [v for v in scen_prices if v > cur]:
                for down in [v for v in scen_prices if v < cur]:
                    if cur > down:
                        recal.append((up - cur) / (cur - down))
        if recal:
            claimed = max(rr_claims)
            if all(abs(claimed - r) / r > 0.5 for r in recal):
                if explicit_recal:
                    # 报告自己声明了上下行输入还对不上 → 无歧义自相矛盾，FAIL 阻断
                    issues.append(_issue(
                        'FAIL', 'NUMBER_INCONSISTENT_RR',
                        f'R/R 与报告自给输入矛盾：声称 {claimed:.1f}:1，按自标"下行 {explicit[0][0]} / '
                        f'上行 {explicit[0][1]}"复算 = {explicit_recal[0]:.1f}:1'))
                else:
                    # 情景价推导口径有歧义 → WARN 留人工核对
                    issues.append(_issue(
                        'WARN', 'RR_RATIO_SUSPECT',
                        f'R/R 声称 {claimed:.1f}:1 偏离可复算口径（'
                        f'{min(recal):.1f}~{max(recal):.1f}:1），请核对上下行价取值'))

    # ── 4. "自有 DCF" 借鉴声明：声称自有估值但未声明参数来源 → WARN ──
    if re.search(r"自有\s*(交叉\s*)?DCF", text) and not re.search(r"参数(借鉴|参考|沿用)自|方法(借鉴|沿用)", text):
        # 只有当文中出现过外部机构 WACC 参数时才有借鉴嫌疑
        if re.search(r"WACC\s*[\d.]+%", text):
            issues.append(_issue(
                'WARN', 'VALUATION_BORROWING_UNDECLARED',
                '声称"自有 DCF"但未声明 WACC 等参数来源——若参数借鉴外部研报，须注明"参数借鉴自 {机构} {报告}"'))

    return issues


def run_report_gate(task_id: str, tasks_dir=None, min_urls: int = 3) -> dict:
    tasks_dir = Path(tasks_dir or TASKS_DIR_IR)
    candidates = [
        tasks_dir / f'{task_id}-final_report.md',
        tasks_dir / f'{task_id}-step8_master.md',
    ]
    report_path = next((path for path in candidates if path.exists()), None)
    text = report_path.read_text(encoding='utf-8') if report_path else ''
    issues = []
    if report_path is None:
        issues.append(_issue('FAIL', 'MISSING_FINAL_REPORT', 'Final report markdown is missing'))
    red_flags = [flag for flag in RED_FLAGS if flag in text]
    if red_flags:
        issues.append(_issue('FAIL', 'REPORT_RED_FLAGS', f'Final report contains red flags: {red_flags}'))
    if text.count('http') < min_urls:
        issues.append(_issue('FAIL', 'REPORT_SOURCE_INSUFFICIENT', f'Final report has fewer than {min_urls} source URLs'))
    # 数字自洽校验（2026-08-06）：概率加权复算 FAIL 阻断；R/R/单调性/借鉴声明 WARN 记录
    if text:
        issues.extend(_check_number_consistency(text))
    output = {
        'task_id': task_id,
        'gate': 'report',
        'passed': not any(issue['severity'] == 'FAIL' for issue in issues),
        'issues': issues,
        'report_path': str(report_path) if report_path else '',
        'url_count': text.count('http'),
    }
    (tasks_dir / f'{task_id}-report_gate.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output
