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

STEP_ORDER = [
    'step1_data', 'step2_industry', 'step3_biz',
    'step4_finance', 'step5_mgmt', 'step6_insight',
    'step6b_valuation', 'step7_risk', 'step8_master',
]

STEP_NAMES = {
    'step1_data': '行情与基础数据',
    'step2_industry': '行业与市场格局',
    'step3_biz': '业务模式',
    'step4_finance': '财务分析',
    'step5_mgmt': '管理与治理',
    'step6_insight': '投资洞察',
    'step6b_valuation': '预测与估值',
    'step7_risk': '风险提示',
    'step8_master': '统稿',
}

MIN_OVERALL_SCORE = 18  # 9 维度，每维 0-3，≥18 才达标（平均 2/3）

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
