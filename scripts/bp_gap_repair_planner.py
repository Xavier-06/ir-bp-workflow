#!/usr/bin/env python3
"""Build targeted BP evidence gap repair plans from failed gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GATE_FILES = [
    "bp_wave1_evidence_gate.json",
    "bp_claim_coverage_gate.json",
    "bp_cross_dimension_gate.json",
]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _search_tasks_by_claim(task_dir: Path) -> dict[str, dict[str, Any]]:
    plan = _load_json(task_dir / "bp_search_plan.json")
    tasks: dict[str, dict[str, Any]] = {}
    for item in plan.get("search_tasks") or []:
        if isinstance(item, dict) and item.get("claim_id"):
            tasks[str(item.get("claim_id"))] = item
    return tasks


def _iter_gate_repairs(gate: dict[str, Any]) -> list[dict[str, Any]]:
    repairs = [item for item in gate.get("repair_tasks") or [] if isinstance(item, dict)]
    for claim in gate.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        status = str(claim.get("status") or "").lower()
        if status in {"unverified", "contradicted", "not_addressed"} or claim.get("blocking_gaps"):
            repairs.append({
                "claim_id": claim.get("claim_id"),
                "owner_section": claim.get("owner_section"),
                "reason": "CLAIM_COVERAGE_GAP",
            })
    for issue in gate.get("issues") or []:
        if isinstance(issue, dict) and issue.get("claim_id"):
            repairs.append({
                "claim_id": issue.get("claim_id"),
                "owner_section": issue.get("owner_section") or issue.get("section"),
                "reason": issue.get("code") or "GATE_ISSUE",
            })
    return repairs


def plan_bp_gap_repairs(task_dir: Path, repair_round: int = 1, max_rounds: int = 2) -> dict[str, Any]:
    search_by_claim = _search_tasks_by_claim(task_dir)
    dedup: set[tuple[str, str]] = set()
    tasks: list[dict[str, Any]] = []

    if repair_round > max_rounds:
        result = {
            "schema_version": "bp_gap_repair_plan.v1",
            "repair_round": repair_round,
            "blocked": True,
            "block_reason": "MAX_REPAIR_ROUNDS_EXCEEDED",
            "tasks": [],
        }
        (task_dir / "bp_gap_repair_plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    for gate_name in GATE_FILES:
        gate = _load_json(task_dir / gate_name)
        for repair in _iter_gate_repairs(gate):
            claim_id = str(repair.get("claim_id") or "")
            owner = str(repair.get("owner_section") or "")
            if not claim_id and not owner:
                continue
            key = (claim_id, owner)
            if key in dedup:
                continue
            dedup.add(key)
            search_task = search_by_claim.get(claim_id, {})
            tasks.append({
                "repair_task_id": f"BGR-{len(tasks)+1:03d}",
                "owner_section": owner,
                "claim_id": claim_id,
                "reason": repair.get("reason") or "EVIDENCE_GAP",
                "required_actions": repair.get("required_actions") or ["search", "fetch", "write_fact", "update_section_package"],
                "queries": search_task.get("queries", []),
                "min_fetched_urls": search_task.get("min_fetched_urls", 2),
                "search_task_ids": [search_task.get("search_task_id")] if search_task.get("search_task_id") else [],
            })

    result = {
        "schema_version": "bp_gap_repair_plan.v1",
        "repair_round": repair_round,
        "blocked": False,
        "tasks": tasks,
    }
    (task_dir / "bp_gap_repair_plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
