#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / 'data' / 'tasks'

ESTIMATE_HINTS = ['预计', '预期', '假设', '推算', '估算', '可能', '或将']
OFFICIAL_TIERS = {'official', 'primary', 'filing'}


def classify(row: dict) -> str:
    claim = (row.get('claim') or '').strip()
    url = (row.get('source_url') or '').strip()
    if not url.startswith('http'):
        return 'process_or_query'
    if any(k in claim for k in ESTIMATE_HINTS):
        return 'estimate_or_inference'
    return 'retrieved_fact'


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _rows_from_legacy_evidence(task_id: str, tasks_dir: Path) -> list[dict]:
    evidence_json = tasks_dir / f'{task_id}-evidence.json'
    payload = _read_json(evidence_json, {}) or {}
    return payload.get('rows', []) or []


def _rows_from_fact_store(task_id: str, tasks_dir: Path) -> list[dict]:
    fact_store = _read_json(tasks_dir / f'{task_id}-fact_store.json', {}) or {}
    rows = []
    for fact in fact_store.get('facts', []) or []:
        rows.append({
            'fact_id': fact.get('fact_id', ''),
            'section': ','.join(fact.get('used_by_sections', []) or []),
            'claim': fact.get('claim', ''),
            'source_title': fact.get('source_title', '') or fact.get('source_tier', ''),
            'source_url': fact.get('source_url', ''),
            'confidence': fact.get('confidence', ''),
            'source_tier': fact.get('source_tier', ''),
        })
    return rows


def _unknown_fact_references(task_id: str, tasks_dir: Path, known_fact_ids: set[str]) -> list[str]:
    section_index = _read_json(tasks_dir / f'{task_id}-section_packages.json', {}) or {}
    unknown = []
    for item in section_index.get('packages', []) or []:
        package = item.get('package', {}) or {}
        for claim in package.get('claims', []) or []:
            for fact_id in claim.get('fact_ids', []) or []:
                if fact_id not in known_fact_ids and fact_id not in unknown:
                    unknown.append(fact_id)
    return unknown


def build_source_audit(task_id: str, tasks_dir: Path = TASKS) -> dict:
    tasks_dir = Path(tasks_dir)
    rows = _rows_from_fact_store(task_id, tasks_dir)
    evidence_source = 'fact_store'
    if not rows:
        rows = _rows_from_legacy_evidence(task_id, tasks_dir)
        evidence_source = 'legacy_evidence'

    out_rows = []
    counts = {'retrieved_fact': 0, 'estimate_or_inference': 0, 'process_or_query': 0}
    source_urls = set()
    official_source_count = 0
    low_quality_source_count = 0
    claims_without_sources = []
    known_fact_ids = set()

    for i, r in enumerate(rows, start=1):
        t = classify(r)
        counts[t] += 1
        fact_id = (r.get('fact_id') or '').strip()
        if fact_id:
            known_fact_ids.add(fact_id)
        source_url = (r.get('source_url') or '').strip()
        source_tier = (r.get('source_tier') or '').strip()
        if source_url.startswith('http'):
            source_urls.add(source_url)
        else:
            if fact_id:
                claims_without_sources.append(fact_id)
        if source_tier in OFFICIAL_TIERS:
            official_source_count += 1
        if not source_url.startswith('http') or source_tier in ('unknown', 'low', 'auxiliary'):
            low_quality_source_count += 1
        out_rows.append({
            'idx': i,
            'fact_id': fact_id,
            'section': r.get('section', ''),
            'classification': t,
            'claim': (r.get('claim') or '').strip(),
            'source_title': (r.get('source_title') or '').strip(),
            'source_url': source_url,
            'source_tier': source_tier,
            'confidence': r.get('confidence', ''),
        })

    unknown_fact_refs = _unknown_fact_references(task_id, tasks_dir, known_fact_ids)
    payload = {
        'task_id': task_id,
        'output': str(tasks_dir / f'{task_id}-source-audit.json'),
        'markdown_output': str(tasks_dir / f'{task_id}-source-audit.md'),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'evidence_source': evidence_source,
        'counts': counts,
        'source_count': len(source_urls),
        'official_source_count': official_source_count,
        'low_quality_source_count': low_quality_source_count,
        'claims_without_sources': claims_without_sources,
        'unknown_fact_references': unknown_fact_refs,
        'verdict': 'PASS' if not claims_without_sources and not unknown_fact_refs else 'FAIL',
        'rows': out_rows,
        'policy': {
            'retrieved_fact': '有可追溯URL的检索事实（不代表真实性已终审）',
            'estimate_or_inference': '带有假设/推算/预期语义的内容',
            'process_or_query': '流程说明、待补问题、查询模板残留，不能当事实结论',
        }
    }

    out_json = tasks_dir / f'{task_id}-source-audit.json'
    out_md = tasks_dir / f'{task_id}-source-audit.md'
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    lines = [
        f'# Source Audit - {task_id}',
        '',
        f"- generated_at: {payload['generated_at']}",
        f"- evidence_source: {payload['evidence_source']}",
        f"- verdict: {payload['verdict']}",
        f"- source_count: {payload['source_count']}",
        f"- official_source_count: {payload['official_source_count']}",
        f"- claims_without_sources: {len(claims_without_sources)}",
        f"- unknown_fact_references: {len(unknown_fact_refs)}",
        '',
        '| # | fact_id | section | classification | claim | source | confidence |',
        '|---|---|---|---|---|---|---|',
    ]
    for r in out_rows:
        claim = (r['claim'] or '').replace('|', ' ')[:120]
        source = (r['source_title'] or r['source_url'] or '').replace('|', ' ')[:80]
        lines.append(f"| {r['idx']} | {r['fact_id']} | {r['section']} | {r['classification']} | {claim} | {source} | {r['confidence']} |")
    out_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('task_id')
    args = ap.parse_args()
    payload = build_source_audit(args.task_id, tasks_dir=TASKS)
    print(json.dumps({
        'task_id': args.task_id,
        'output': str(TASKS / f'{args.task_id}-source-audit.json'),
        'source_audit_json': str(TASKS / f'{args.task_id}-source-audit.json'),
        'source_audit_md': str(TASKS / f'{args.task_id}-source-audit.md'),
        'counts': payload['counts'],
        'verdict': payload['verdict'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
