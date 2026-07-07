#!/usr/bin/env python3
"""IR claim coverage gate — 对标 BP bp_claim_coverage_validator.py。

IR 管线的 claim 来源:
  - research_plan.json 的 claim_matrix (从 phase03 enrichment 合并后产出)
  - 各 step 的 section sidecar 的 claims 字段

IR 管线的 fact 来源:
  - 各 step 的 facts sidecar (*-facts.json)
  - fact_store.json (phase10 merge 后产出)

检查维度:
  1. 每个 claim 是否有对应的 fact_ids
  2. fact 的证据质量 (source_tier)
  3. 优先级为 critical/high 的 claim 必须有外部证据
  4. not_addressed 的 claim 比例

verdict: PASS / REPAIR / PASS_WITH_DISCLOSURE
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.bp_utils import load_json

# claim coverage repair 最大重试次数
_MAX_CLAIM_REPAIR_RETRIES = 2


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _claim_rows_from_research_plan(task_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从 research_plan.json 读取 claim_matrix。"""
    plan = load_json(task_dir / "ir_research_plan.json", {})
    if not plan:
        # 尝试 task_id 前缀
        for p in task_dir.glob("*-research_plan.json"):
            plan = load_json(p, {})
            if plan:
                break

    raw_claims = plan.get("claim_matrix", [])
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_as_list(raw_claims), 1):
        if not isinstance(item, dict):
            continue
        claim_text = item.get("claim") or item.get("text") or item.get("description") or ""
        if not str(claim_text).strip():
            continue
        rows.append({
            "claim_id": item.get("claim_id") or item.get("id") or f"IR-CL{idx:03d}",
            "claim": claim_text,
            "owner_section": item.get("owner_section") or item.get("owner") or "",
            "priority": item.get("priority") or item.get("importance") or "high",
            "status": item.get("status") or "not_addressed",
            "fact_ids": _as_list(item.get("fact_ids")),
            "data_gaps": _as_list(item.get("data_gaps")),
            "source_quality": item.get("source_quality") or "unknown",
        })

    meta = {
        "entity": plan.get("entity") or "",
        "task_id": plan.get("task_id") or task_dir.name,
    }
    return rows, meta


def _facts_from_sidecars(task_dir: Path) -> list[dict[str, Any]]:
    """从 step sidecar 文件读取 facts。"""
    facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*-facts.json")):
            # 跳过 fact_store 等非 sidecar 文件
            if "fact_store" in path.name or "gate" in path.name:
                continue
            payload = load_json(path, {})
            for fact in _as_list(payload.get("facts")):
                if not isinstance(fact, dict):
                    continue
                fact_id = str(fact.get("fact_id") or "").strip()
                if fact_id and fact_id in seen_ids:
                    continue
                if fact_id:
                    seen_ids.add(fact_id)
                facts.append(dict(fact))
    return facts


def _fact_tier(fact: dict[str, Any]) -> str:
    """提取 fact 的证据等级。"""
    for field in ("source_tier", "source_quality", "source_type"):
        val = str(fact.get(field) or "").strip().lower()
        if val and val not in ("", "none", "null"):
            return val
    return "unknown"


def evaluate_ir_claim_coverage(task_dir: Path) -> dict[str, Any]:
    """评估 IR 管线的 claim 覆盖情况。"""
    task_dir = Path(task_dir)
    claims, meta = _claim_rows_from_research_plan(task_dir)

    if not claims:
        return {
            "schema_version": "ir_claim_coverage.v1",
            "verdict": "PASS",
            "gate_verdict": "PASS",
            "issues": [],
            "claims": [],
            "summary": {
                "total_claims": 0,
                "not_addressed": 0,
                "bp_only": 0,
                "supported": 0,
                "contradicted": 0,
            },
        }

    # 构建 fact index
    fact_index: dict[str, dict[str, Any]] = {}
    for fact in _facts_from_sidecars(task_dir):
        fid = str(fact.get("fact_id") or "").strip()
        if fid:
            fact_index[fid] = fact
    # 也加载中央 fact_store
    store = load_json(task_dir / "fact_store.json", {})
    for fact in _as_list(store.get("facts", [])):
        if isinstance(fact, dict) and fact.get("fact_id"):
            fid = str(fact["fact_id"]).strip()
            if fid and fid not in fact_index:
                fact_index[fid] = fact

    # 评估每个 claim
    issues: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    counters = {"total": 0, "not_addressed": 0, "bp_only": 0, "supported": 0, "contradicted": 0}

    for claim in claims:
        counters["total"] += 1
        claim_id = claim["claim_id"]
        priority = claim["priority"]
        fact_ids = [str(fid) for fid in claim.get("fact_ids", []) if str(fid).strip()]

        # 查找关联的 facts
        linked_facts = [fact_index[fid] for fid in fact_ids if fid in fact_index]
        tiers = {_fact_tier(f) for f in linked_facts}
        has_external = bool(tiers - {"unknown", "bp", "bp_only", ""})

        if not fact_ids:
            status = "not_addressed"
            counters["not_addressed"] += 1
        elif has_external:
            status = "supported"
            counters["supported"] += 1
        elif linked_facts:
            status = "bp_only"
            counters["bp_only"] += 1
        else:
            status = "not_addressed"
            counters["not_addressed"] += 1

        claim_result = {
            "claim_id": claim_id,
            "claim": claim["claim"][:100],
            "priority": priority,
            "status": status,
            "fact_ids": fact_ids,
            "linked_facts_count": len(linked_facts),
            "evidence_tiers": sorted(tiers),
        }
        results.append(claim_result)

        # 高优先级 claim 未覆盖 → issue
        if priority in ("critical", "high") and status in ("not_addressed", "bp_only"):
            issues.append({
                "severity": "MEDIUM",
                "code": "CLAIM_NOT_COVERED",
                "claim_id": claim_id,
                "priority": priority,
                "status": status,
                "message": f"{priority} priority claim not covered by external evidence: {claim['claim'][:80]}",
            })

    # 判定 verdict
    total = counters["total"]
    not_addr_ratio = counters["not_addressed"] / max(total, 1)

    if not_addr_ratio > 0.5:
        gate_verdict = "REPAIR"
    elif counters["bp_only"] > total * 0.3:
        gate_verdict = "PASS_WITH_DISCLOSURE"
    else:
        gate_verdict = "PASS"

    return {
        "schema_version": "ir_claim_coverage.v1",
        "verdict": gate_verdict,
        "gate_verdict": gate_verdict,
        "issues": issues,
        "claims": results,
        "summary": counters,
        "entity": meta.get("entity", ""),
        "task_id": meta.get("task_id", ""),
    }


def write_ir_claim_coverage(task_dir: Path) -> dict[str, Any]:
    """执行 claim coverage 检查并写入结果。"""
    result = evaluate_ir_claim_coverage(task_dir)
    path = Path(task_dir) / "ir_claim_coverage.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"gate_path": str(path)}


def build_ir_claim_repair_manifests(
    task_id: str,
    gate_result: dict[str, Any],
    tasks_dir: Path,
) -> list[str]:
    """为 claim coverage REPAIR 生成 repair manifest。

    按 owner_section 聚合，每个 owner 一个 manifest。
    """
    issues = gate_result.get("issues", [])
    if not issues:
        return []

    # 按 claim 聚合
    claims_to_fix: list[dict[str, Any]] = []
    for issue in issues:
        claims_to_fix.append({
            "claim_id": issue.get("claim_id", ""),
            "priority": issue.get("priority", ""),
            "status": issue.get("status", ""),
            "message": issue.get("message", ""),
        })

    if not claims_to_fix:
        return []

    system_prompt_lines = [
        "你是 IR claim coverage 修复专员。",
        f"当前有 {len(claims_to_fix)} 个 claim 未被外部证据覆盖。",
        "",
        "需要修复的 claims:",
    ]
    for c in claims_to_fix[:10]:  # 最多列 10 个
        system_prompt_lines.append(f"  - [{c['claim_id']}] ({c['priority']}) {c['message'][:80]}")

    system_prompt_lines.extend([
        "",
        "修复步骤:",
        "1. 搜索外部证据补充缺失的 claim 覆盖",
        "2. 将新事实写入对应的 step facts sidecar",
        "3. 更新 section sidecar 的 claims.fact_ids",
        "4. 使用 scripts.bp_file_lock.locked_read_modify_write 写共享文件",
    ])

    manifest = {
        "manifest_version": "1.0",
        "pipeline": "ir",
        "role": "ir_claim_repair",
        "system_prompt": "\n".join(system_prompt_lines),
        "connectorIds": ["tyc-mcp"],
        "subagent_type": "general-purpose",
        "team_name_template": "ir-{task_id}",
        "task_dir": str(tasks_dir),
        "claims_to_fix": claims_to_fix,
    }

    manifest_path = tasks_dir / f"{task_id}-claim_coverage_repair_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return [str(manifest_path)]
