#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / 'data' / 'tasks'

LEGACY_HOOKS = [
    'search-plan-review',
    'clean-evidence-review',
    'analysis-writer-polish',
]


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return default
    return default


def _collect_phase_states(task_id: str, runtime_root: Path) -> list[dict]:
    state_dir = runtime_root / 'jobs' / task_id / 'state'
    rows = []
    if not state_dir.exists():
        return rows
    for path in sorted(state_dir.glob('*.json')):
        data = load_json(path, {}) or {}
        rows.append({
            'phase': data.get('phase') or path.stem,
            'status': data.get('status', ''),
            'attempt': data.get('attempt', ''),
            'resume_from': data.get('resume_from'),
            'started_at': data.get('started_at'),
            'finished_at': data.get('finished_at'),
            'path': str(path),
        })
    return rows


def _collect_step_receipts(task_id: str, tasks_dir: Path) -> list[dict]:
    receipts = []
    patterns = [f'{task_id}-step*-spawn.json', f'{task_id}-step_macro-spawn.json']
    paths = []
    for pattern in patterns:
        paths.extend(tasks_dir.glob(pattern))
    seen_paths = set()
    for path in sorted(paths):
        if path in seen_paths:
            continue
        seen_paths.add(path)
        data = load_json(path, {}) or {}
        if isinstance(data, dict) and (data.get('childSessionKey') or data.get('runId')):
            step = data.get('step') or path.name.replace(f'{task_id}-', '').replace('-spawn.json', '')
            receipts.append({
                'step': step,
                'path': str(path),
                'label': data.get('label'),
                'childSessionKey': data.get('childSessionKey'),
                'runId': data.get('runId'),
                'status': data.get('status'),
                'runtime': data.get('runtime', 'workbuddy-task'),
            })
    for hook in LEGACY_HOOKS:
        path = tasks_dir / f'{task_id}-{hook}-spawn.json'
        data = load_json(path, {}) or {}
        if isinstance(data, dict) and (data.get('childSessionKey') or data.get('runId')):
            receipts.append({
                'step': hook,
                'path': str(path),
                'label': data.get('label'),
                'childSessionKey': data.get('childSessionKey'),
                'runId': data.get('runId'),
                'status': data.get('status'),
                'runtime': data.get('runtime', 'subagent'),
            })
    return receipts


def _collect_step_manifests(task_id: str, tasks_dir: Path) -> list[dict]:
    manifests = []
    patterns = [f'{task_id}-step*-manifest.json', f'{task_id}-step_macro-manifest.json']
    paths = []
    for pattern in patterns:
        paths.extend(tasks_dir.glob(pattern))
    for path in sorted(set(paths)):
        data = load_json(path, {}) or {}
        manifests.append({
            'step': data.get('step') or path.name.replace(f'{task_id}-', '').replace('-manifest.json', ''),
            'path': str(path),
            'output_path': data.get('output_path', ''),
        })
    return manifests


def _duplicates(rows: list[dict], key: str) -> list[str]:
    seen = set()
    dup = []
    for row in rows:
        value = row.get(key)
        if not value:
            continue
        if value in seen and value not in dup:
            dup.append(value)
        seen.add(value)
    return dup


def build_execution_audit(task_id: str, tasks_dir: Path = TASKS, runtime_root: Path = ROOT) -> dict:
    tasks_dir = Path(tasks_dir)
    runtime_root = Path(runtime_root)
    pkg_path = tasks_dir / f'{task_id}.json'
    manifest_path = tasks_dir / f'{task_id}-execution-manifest.json'
    pkg = load_json(pkg_path, {}) or {}
    manifest = load_json(manifest_path, {}) or {}

    phase_states = _collect_phase_states(task_id, runtime_root)
    receipts = _collect_step_receipts(task_id, tasks_dir)
    step_manifests = _collect_step_manifests(task_id, tasks_dir)
    duplicate_dispatches = _duplicates(receipts, 'step')
    events = manifest.get('events', []) or []
    model_route = pkg.get('model_route', {}) or {}

    payload = {
        'task_id': task_id,
        'output': str(tasks_dir / f'{task_id}-execution-audit.json'),
        'markdown_output': str(tasks_dir / f'{task_id}-execution-audit.md'),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'execution_mode': manifest.get('execution_mode', 'workspace-manifest-phase-state'),
        'model_route': model_route,
        'instruction_keys_loaded': pkg.get('instruction_keys', []) or [],
        'events_count': len(events),
        'events': events,
        'phase_state_count': len(phase_states),
        'phase_states': phase_states,
        'step_manifest_count': len(step_manifests),
        'step_manifests': step_manifests,
        'subagent_spawn_receipts': receipts,
        'real_subagent_receipts': len(receipts),
        'multi_agent_real_collab': len(receipts) > 0,
        'duplicate_dispatches': duplicate_dispatches,
        'verdict': 'FAIL' if duplicate_dispatches else 'PASS',
        'valuation_execution': {
            'independent_agent_session': len(receipts) > 0,
            'executor': 'workspace phase states + WorkBuddy step receipts' if receipts else 'orchestrator phase states only',
            'model': model_route.get('preferred_model', 'unknown'),
            'note': '已检测到真实 step/subagent receipt。' if receipts else '未检测到 step/subagent receipt。'
        }
    }

    out_json = tasks_dir / f'{task_id}-execution-audit.json'
    out_md = tasks_dir / f'{task_id}-execution-audit.md'
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    lines = [
        f'# Execution Audit - {task_id}',
        '',
        f"- generated_at: {payload['generated_at']}",
        f"- execution_mode: {payload['execution_mode']}",
        f"- verdict: {payload['verdict']}",
        f"- phase_state_count: {payload['phase_state_count']}",
        f"- step_manifest_count: {payload['step_manifest_count']}",
        f"- multi_agent_real_collab: {payload['multi_agent_real_collab']}",
        '',
        '## Phase states',
        '| phase | status | attempt | resume_from |',
        '|---|---|---:|---|',
    ]
    for row in phase_states:
        lines.append(f"| {row.get('phase','')} | {row.get('status','')} | {row.get('attempt','')} | {row.get('resume_from','')} |")
    lines += ['', '## Step receipts']
    if receipts:
        for r in receipts:
            lines.append(f"- {r.get('step')} | childSessionKey={r.get('childSessionKey')} | runId={r.get('runId')} | status={r.get('status')}")
    else:
        lines.append('- none')
    out_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('task_id')
    args = ap.parse_args()
    payload = build_execution_audit(args.task_id, tasks_dir=TASKS, runtime_root=ROOT)
    print(json.dumps({
        'task_id': args.task_id,
        'output': str(TASKS / f'{args.task_id}-execution-audit.json'),
        'execution_audit_json': str(TASKS / f'{args.task_id}-execution-audit.json'),
        'execution_audit_md': str(TASKS / f'{args.task_id}-execution-audit.md'),
        'phase_states': payload['phase_state_count'],
        'real_subagent_receipts': payload['real_subagent_receipts'],
        'verdict': payload['verdict'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
