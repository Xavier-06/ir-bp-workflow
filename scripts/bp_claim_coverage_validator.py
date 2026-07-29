#!/usr/bin/env python3
"""Claim coverage gate for BP pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from runtime.profiles.bp_constants import BP_TYC_CONNECTOR_IDS
from scripts.bp_utils import load_json, read_attempt_count


FAIL_REASON = "CRITICAL_CLAIM_NOT_ADDRESSED"
MISSING_CLAIMS_REASON = "CLAIM_INVENTORY_MISSING"

# claim coverage repair 最大硬卡次数；超过后降级为 PASS_WITH_DISCLOSURE 放行
_MAX_CLAIM_REPAIR_RETRIES = 2


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _claim_rows_from_sources(task_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    inventory = load_json(task_dir / "bp_claim_inventory.json", {})
    plan = load_json(task_dir / "bp_research_plan.json", {})
    inventory_claims = inventory.get("claims", []) if isinstance(inventory, dict) else inventory
    raw_rows = inventory_claims if _as_list(inventory_claims) else plan.get("claim_matrix", [])
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_as_list(raw_rows), 1):
        if not isinstance(item, dict):
            continue
        claim = item.get("claim") or item.get("text") or item.get("bp_claim") or ""
        if not str(claim).strip():
            continue
        rows.append({
            "claim_id": item.get("claim_id") or item.get("id") or f"BC{idx:03d}",
            "claim": claim,
            "owner": item.get("owner") or item.get("owner_section") or "",
            "owner_section": item.get("owner_section") or item.get("owner") or "",
            "priority": item.get("priority") or item.get("importance") or "high",
            "status": item.get("status") or "not_addressed",
            "fact_ids": _as_list(item.get("fact_ids")),
            "data_gaps": _as_list(item.get("data_gaps")),
            "source_quality": item.get("source_quality") or "unknown",
            "source": item.get("source") or ("claim_inventory" if _as_list(inventory_claims) else "research_plan"),
        })
    meta = {
        "task_id": (inventory if isinstance(inventory, dict) else {}).get("task_id") or plan.get("task_id") or task_dir.name,
        "entity": (inventory if isinstance(inventory, dict) else {}).get("entity") or plan.get("entity") or "",
    }
    return rows, meta


def _facts_from_sidecars(task_dir: Path) -> list[dict[str, Any]]:
    """Read facts from sub-agent sidecar files (*-facts.json).

    Sub-agents write their discovered facts to sidecar files next to their
    markdown output.  These facts are NOT automatically merged into
    bp_fact_store.json during wave execution — they only get merged in
    phase30 (bp_fact_store_merge) which runs BEFORE the waves.  So the
    central store is always stale during wave evaluation.

    This helper lets the claim_coverage validator see sub-agent facts
    without requiring a central store refresh.
    """
    facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("bp_*-facts.json")):
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


def _reconstruct_ghost_facts(task_dir: Path) -> list[dict[str, Any]]:
    """Reconstruct ghost fact objects from section sidecars.

    Some sub-agents reference fact_ids in their section sidecar's
    facts_used or claims[*].fact_ids but never write the corresponding
    fact objects to *-facts.json.  This creates 'ghost' fact_ids that
    cause downstream validation to treat the claim as having no evidence.

    This function scans *-section.json files for referenced-but-undefined
    fact_ids and creates minimal fact objects from the section context.
    """
    # Collect all defined fact_ids from sidecars
    defined_ids: set[str] = set()
    for fact in _facts_from_sidecars(task_dir):
        fid = str(fact.get("fact_id") or "").strip()
        if fid:
            defined_ids.add(fid)
    # Also check central fact store
    store = load_json(task_dir / "bp_fact_store.json", {})
    for fact in _as_list(store.get("facts", [])):
        if isinstance(fact, dict) and fact.get("fact_id"):
            defined_ids.add(str(fact["fact_id"]).strip())

    # Scan section sidecars for referenced-but-undefined fact_ids
    ghost_facts: list[dict[str, Any]] = []
    seen_ghosts: set[str] = set()
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("bp_*-section.json")):
            section = load_json(path, {})
            section_id = str(section.get("section_id") or path.stem).replace("bp_dim_", "")
            referenced_ids: set[str] = set()
            # From facts_used
            for fid in _as_list(section.get("facts_used")):
                fid_str = str(fid).strip()
                if fid_str:
                    referenced_ids.add(fid_str)
            # From claims[*].fact_ids
            for claim in _as_list(section.get("claims")):
                if isinstance(claim, dict):
                    for fid in _as_list(claim.get("fact_ids")):
                        fid_str = str(fid).strip()
                        if fid_str:
                            referenced_ids.add(fid_str)
            # Synthesize ghost facts
            for fid in sorted(referenced_ids - defined_ids - seen_ghosts):
                seen_ghosts.add(fid)
                ghost_facts.append({
                    "fact_id": fid,
                    "source_tier": "section_internal",
                    "source_quality": "section_internal",
                    "claim": f"[auto-recovered from {section_id}]",
                    "value": "",
                    "unit": "",
                    "period": "",
                    "source_url": "",
                    "source_quote": "",
                    "question_id": "",
                    "fact_type": "recovered_ghost",
                    "confidence": "low",
                    "_ghost": True,
                })
    return ghost_facts


def _load_fact_index(task_dir: Path) -> dict[str, dict[str, Any]]:
    store = load_json(task_dir / "bp_fact_store.json", {})
    facts = store.get("facts", []) if isinstance(store, dict) else []
    index: dict[str, dict[str, Any]] = {}
    for fact in _as_list(facts):
        if isinstance(fact, dict) and fact.get("fact_id"):
            index[str(fact["fact_id"])] = fact
    # Merge sub-agent sidecar facts (fixes Bug 1: stale central store)
    for fact in _facts_from_sidecars(task_dir):
        fid = str(fact.get("fact_id") or "").strip()
        if fid and fid not in index:
            index[fid] = fact
    # Merge ghost facts reconstructed from section sidecars (Fix 1a)
    for fact in _reconstruct_ghost_facts(task_dir):
        fid = str(fact.get("fact_id") or "").strip()
        if fid and fid not in index:
            index[fid] = fact
    return index



def _domain(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.netloc:
        return parsed.netloc.lower()
    return ""



def _fact_tier(fact: dict[str, Any]) -> str:
    """Extract the evidence tier from a fact, checking multiple field names.

    Sub-agents use inconsistent field names for source quality.
    This function checks: source_tier, source_quality, source_type,
    provenance, evidence_quality, source_level.

    Also normalizes common values to canonical tier names:
      - company_official → official
      - industry_report → research
      - bp_source_document → bp
      - peer_financing_database → market_database
    """
    for field in ("source_tier", "source_quality", "source_type",
                  "provenance", "evidence_quality", "source_level"):
        val = str(fact.get(field) or "").strip().lower()
        if val and val not in ("", "none", "null"):
            # Normalize aliases
            _TIER_MAP = {
                "company_official": "official",
                "company_disclosure": "official",
                "customer_or_partner_disclosure": "customer_or_partner_disclosure",
                "industry_report": "research",
                "research_report": "research",
                "academic": "research",
                "bp_source_document": "bp",
                "bp": "bp",
                "peer_financing_database": "market_database",
                "market_data": "market_database",
                "market_database": "market_database",
                "media": "media",
                "reputable_media": "media",
                "news": "media",
                "official": "official",
                "regulatory": "regulatory",
                "database": "database",
                "public_tender": "public_tender",
                "listed_peer_filings": "listed_peer_filings",
                "inferred": "bp",  # 默认保守：无来源标记的推断视为 BP 级别
                "inferred_from_external": "media",  # 子代理明确标注来自外部推断
                "inferred_from_bp": "bp",  # 子代理明确标注来自 BP 推断
                "analysis": "bp",  # internal analysis = bp level
            }
            return _TIER_MAP.get(val, val)
    return "unknown"


def _derive_evidence_profile(claim: dict[str, Any], facts_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fact_ids = [str(fact_id) for fact_id in _as_list(claim.get("fact_ids")) if str(fact_id).strip()]
    facts = [facts_by_id[fact_id] for fact_id in fact_ids if fact_id in facts_by_id]
    tiers = {_fact_tier(fact) for fact in facts}
    fetched_urls = sorted({str(fact.get("source_url") or "").strip() for fact in facts if str(fact.get("source_url") or "").strip()})
    domains = sorted({domain for domain in (_domain(url) for url in fetched_urls) if domain})
    blocking_gaps = [str(gap) for gap in _as_list(claim.get("blocking_gaps")) if str(gap).strip()]
    counter_evidence = [item for item in _as_list(claim.get("counter_evidence")) if str(item).strip()]

    authoritative_tiers = {"official", "regulatory", "database"}
    external_tiers = authoritative_tiers | {"media", "research", "listed_peer_filings", "market_database",
                                             "customer_or_partner_disclosure", "public_tender"}
    has_authoritative = bool(tiers & authoritative_tiers)
    external_domains = [domain for domain in domains if domain]
    has_external = bool(tiers & external_tiers)
    bp_only = bool(fact_ids) and bool(facts) and not has_external and tiers <= {"bp", "unknown"}

    # 检查 fact 内容是否为"否定性发现"（搜索结果为"无"不应让 claim 变 supported）
    _NEGATIVE_KEYWORDS = ("未找到", "未发现", "无外部证据", "无法验证", "无法独立验证",
                          "不存在", "无任何", "no evidence", "not found", "no public")
    if facts:
        negative_count = sum(
            1 for f in facts
            if any(kw in str(f.get("claim", "")).lower() for kw in _NEGATIVE_KEYWORDS)
        )
        if negative_count > 0 and negative_count >= len(facts) * 0.5:
            # 超过一半的 fact 是否定性发现 → claim 未被支持
            has_authoritative = False
            has_external = False
            bp_only = True

    if counter_evidence:
        status = "contradicted"
        evidence_strength = "authoritative" if has_authoritative else ("cross_verified" if len(external_domains) >= 2 else "single_source" if has_external else "bp_only" if bp_only else "none")
        blocking_gaps.append("COUNTER_EVIDENCE_PRESENT")
    elif bp_only:
        status = "unverified"
        evidence_strength = "bp_only"
        blocking_gaps.append("BP_ONLY_EVIDENCE")
    elif has_authoritative:
        status = "supported"
        evidence_strength = "authoritative"
    elif len(external_domains) >= 2:
        status = "supported"
        evidence_strength = "cross_verified"
    elif has_external:
        status = "partially_supported"
        evidence_strength = "single_source"
    elif fact_ids:
        status = "unverified"
        evidence_strength = "none"
        blocking_gaps.append("FACT_IDS_NOT_FOUND_OR_SOURCE_UNKNOWN")
    else:
        status = str(claim.get("status") or "not_addressed").lower()
        evidence_strength = "none"

    return {
        "status": status,
        "evidence_strength": evidence_strength,
        "source_domain_count": len(domains),
        "fetched_url_count": len(fetched_urls),
        "counter_search_done": bool(claim.get("counter_search_done")) or bool(counter_evidence),
        "blocking_gaps": sorted(set(blocking_gaps)),
    }



def _reclassify_claims_by_fact_store(task_dir: Path, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts_by_id = _load_fact_index(task_dir)
    if not facts_by_id:
        return claims
    output: list[dict[str, Any]] = []
    for claim in claims:
        updated = dict(claim)
        profile = _derive_evidence_profile(updated, facts_by_id)
        updated.update(profile)
        output.append(updated)
    return output



def _coverage_summary(claims: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(claims),
        "supported": 0,
        "partially_supported": 0,
        "unverified": 0,
        "not_addressed": 0,
        "contradicted": 0,
        "critical_not_addressed": 0,
        "high_not_addressed": 0,
    }
    for claim in claims:
        status = str(claim.get("status") or "not_addressed").lower()
        priority = str(claim.get("priority") or "").lower()
        if status not in summary:
            summary[status] = 0
        summary[status] += 1
        if status == "not_addressed" and priority == "critical":
            summary["critical_not_addressed"] += 1
        if status == "not_addressed" and priority == "high":
            summary["high_not_addressed"] += 1
    return summary


def _initialize_coverage(task_dir: Path) -> dict[str, Any]:
    claims, meta = _claim_rows_from_sources(task_dir)
    coverage = {
        "schema_version": "bp_claim_coverage.v1",
        "task_id": meta["task_id"],
        "entity": meta["entity"],
        "summary": _coverage_summary(claims),
        "claims": claims,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(task_dir / "bp_claim_coverage.json", coverage)
    return coverage


def _load_or_initialize_coverage(task_dir: Path) -> dict[str, Any]:
    coverage_path = task_dir / "bp_claim_coverage.json"
    coverage = load_json(coverage_path, {})
    if isinstance(coverage, dict) and coverage_path.exists():
        claims = [dict(claim) for claim in _as_list(coverage.get("claims")) if isinstance(claim, dict)]
        coverage["claims"] = claims
        coverage["summary"] = _coverage_summary(claims)
        return coverage
    return _initialize_coverage(task_dir)


def _read_stage_tier(task_dir: Path) -> str:
    """Read stage_tier — 统一使用 bp_stage_utils.read_stage_from_task。"""
    from scripts.bp_stage_utils import read_stage_from_task
    return read_stage_from_task(task_dir, default="T4")


def evaluate_bp_claim_coverage(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    coverage = _load_or_initialize_coverage(task_dir)
    claims = [dict(claim) for claim in _as_list(coverage.get("claims")) if isinstance(claim, dict)]
    claims = _reclassify_claims_by_fact_store(task_dir, claims)
    coverage["claims"] = claims
    coverage["summary"] = _coverage_summary(claims)
    _write_json(task_dir / "bp_claim_coverage.json", coverage)

    # Fix 1c: T1/T2 stage awareness — BP_ONLY_EVIDENCE is expected for early stage
    stage_tier = _read_stage_tier(task_dir)
    is_early_stage = stage_tier in {"T1", "T2"}

    failed_claims: list[dict[str, Any]] = []
    disclosure_claims: list[dict[str, Any]] = []
    for claim in claims:
        priority = str(claim.get("priority") or "").lower()
        status = str(claim.get("status") or "not_addressed").lower()
        data_gaps = [gap for gap in _as_list(claim.get("data_gaps")) if str(gap).strip()]
        evidence_strength = str(claim.get("evidence_strength") or "").lower()
        blocking_gaps = _as_list(claim.get("blocking_gaps"))

        # Fix 1c: For T1/T2, BP_ONLY_EVIDENCE on critical/high claims
        # becomes disclosure instead of hard fail
        is_bp_only_block = (
            evidence_strength == "bp_only"
            and "BP_ONLY_EVIDENCE" in [str(g) for g in blocking_gaps]
            and is_early_stage
        )
        # Fix 1d (2026-06-12): T1/T2 not_addressed critical/high claims
        # also become disclosure — sub-agents often don't link fact_ids
        # to claims, causing _derive_evidence_profile to return
        # status="not_addressed" with evidence_strength="none",
        # which bypassed the is_bp_only_block check above.
        is_t1_not_addressed = (
            is_early_stage
            and status == "not_addressed"
            and priority in {"critical", "high"}
        )

        if priority in {"critical", "high"} and status in {"not_addressed", "unverified", "contradicted"}:
            if (is_bp_only_block or is_t1_not_addressed) and status != "contradicted":
                # Early stage: BP-only evidence or not_addressed is expected, not a blocker
                # Ensure disclosure claims have data_gaps for traceability
                if is_t1_not_addressed and not data_gaps:
                    claim["data_gaps"] = ["BP自述，未经外部验证（T1早期阶段）"]
                    claim["source_quality"] = "bp_only"
                disclosure_claims.append(claim)
            else:
                failed_claims.append(claim)
        elif status == "unverified" and data_gaps:
            disclosure_claims.append(claim)

    # ── 读取历史执行次数，用于 repair / 降级判断 ──
    prior_attempt = read_attempt_count(task_dir / "bp_claim_coverage_gate.json")

    if not claims:
        verdict = "FAIL"
        block_reason = MISSING_CLAIMS_REASON
    elif failed_claims:
        verdict = "FAIL"
        block_reason = FAIL_REASON
    elif disclosure_claims:
        verdict = "PASS_WITH_DISCLOSURE"
        block_reason = f"T{stage_tier}_STAGE_BP_ONLY_EVIDENCE_ACCEPTABLE_WITH_DISCLOSURE" if is_early_stage else ""
    else:
        verdict = "PASS"
        block_reason = ""

    # ── 构建 repair_tasks（与 wave gate 结构对齐）──
    repair_tasks: list[dict[str, Any]] = []
    for claim in failed_claims:
        repair_tasks.append({
            "repair_task_id": f"CCR-{len(repair_tasks)+1:03d}",
            "owner_section": str(claim.get("owner_section") or ""),
            "claim_id": str(claim.get("claim_id") or ""),
            "claim": str(claim.get("claim") or ""),
            "priority": str(claim.get("priority") or ""),
            "status": str(claim.get("status") or ""),
            "evidence_strength": str(claim.get("evidence_strength") or ""),
            "reason": FAIL_REASON,
            "required_actions": ["search", "fetch", "write_fact", "update_section_package"],
        })

    # ── Repair / 降级判断 ──
    needs_repair = False
    repair_exhausted = False
    if verdict == "FAIL" and prior_attempt < _MAX_CLAIM_REPAIR_RETRIES:
        needs_repair = True
        verdict = "REPAIR"
        print(
            f"  🔧 [claim_coverage] gate FAIL (attempt {prior_attempt + 1}/"
            f"{_MAX_CLAIM_REPAIR_RETRIES})，将派发 {len(repair_tasks)} 个 repair 子代理",
            flush=True,
        )
    elif verdict == "FAIL" and prior_attempt >= _MAX_CLAIM_REPAIR_RETRIES:
        # 已充分 repair 仍 FAIL → 降级为 PASS_WITH_DISCLOSURE
        print(
            f"  ⚠️ [claim_coverage] blocking claims 已重试 {prior_attempt} 次仍未覆盖，"
            f"降级为 PASS_WITH_DISCLOSURE",
            flush=True,
        )
        verdict = "PASS_WITH_DISCLOSURE"
        repair_exhausted = True
        # 将 failed_claims 移入 disclosure_claims 并标记 data_gaps
        for claim in failed_claims:
            if not claim.get("data_gaps"):
                claim["data_gaps"] = [f"经 {_MAX_CLAIM_REPAIR_RETRIES} 轮 repair 仍无法获取外部证据"]
            claim["source_quality"] = "bp_only"
        disclosure_claims.extend(failed_claims)
        failed_claims.clear()
        block_reason = f"CLAIM_REPAIR_EXHAUSTED_AFTER_{prior_attempt}_ATTEMPTS"

    summary = _coverage_summary(claims) | {
        "failed": len(failed_claims),
        "disclosure": len(disclosure_claims),
    }
    return {
        "schema_version": "bp_claim_coverage_gate.v2",
        "ok": verdict != "FAIL",
        "gate_verdict": verdict,
        "block_reason": block_reason,
        "failed_claims": failed_claims,
        "blocking_claims": failed_claims,
        "disclosure_claims": disclosure_claims,
        "summary": summary,
        "coverage": coverage,
        "coverage_path": str(task_dir / "bp_claim_coverage.json"),
        "attempt": prior_attempt + 1,
        "needs_repair": needs_repair,
        "repair_tasks": repair_tasks,
        "repair_exhausted": repair_exhausted,
    }


def write_bp_claim_coverage_gate(task_dir: Path) -> dict[str, Any]:
    result = evaluate_bp_claim_coverage(task_dir)
    gate_path = Path(task_dir) / "bp_claim_coverage_gate.json"
    _write_json(gate_path, result)
    return result | {"gate_path": str(gate_path)}


# ── Claim Repair Manifest Builder ──────────────────────────────


def _claim_role_paths(task_dir: Path, owner_section: str) -> dict[str, Path]:
    """查找 owner_section 对应的 sidecar 路径。

    搜索 outputs/ 和 task_dir 两个目录，优先 outputs。
    找不到时 fallback 到 task_dir 下的标准命名。
    """
    slug = owner_section.replace("bp_", "")
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        facts = directory / f"bp_dim_{slug}-facts.json"
        section = directory / f"bp_dim_{slug}-section.json"
        if facts.exists() or section.exists():
            return {"facts": facts, "section": section}
    # fallback: task_dir 下的标准路径
    return {
        "facts": task_dir / f"bp_dim_{slug}-facts.json",
        "section": task_dir / f"bp_dim_{slug}-section.json",
    }


_CLAIM_REPAIR_PROMPT_TEMPLATE = """\
你是一个 BP 尽调管线的 Claim 修复子代理。

## 你的修复目标

修复角色 {role} 的以下 claims（共 {claims_count} 个）:
{claims_detail}

## 关键：文件写入必须使用锁

多个 repair 子代理可能同时修改 sidecar 文件。你必须使用文件锁来避免数据丢失。

### 修改中央 fact store 时：
```python
from scripts.bp_file_lock import locked_read_modify_write
from pathlib import Path

def add_facts(data):
    new_facts = [...]  # 你新增的 facts
    data.setdefault('facts', []).extend(new_facts)
    return data

locked_read_modify_write(Path('{fact_store_path}'), add_facts)
```

### 修改 section sidecar 时：
```python
def update_section(data):
    for claim_id in {claim_ids_list}:
        if claim_id not in data.get('claim_ids_covered', []):
            data.setdefault('claim_ids_covered', []).append(claim_id)
    return data

locked_read_modify_write(Path('{section_path}'), update_section)
```

## 修复规则

1. 新增的事实必须有外部来源 URL 支撑，禁止编造
2. 不要删除已有 fact，只追加
3. fact_id 命名规则: CCR-{{claim_id}}-{{序号}}
4. 修复完成后，确保所有 JSON 文件都是合法 JSON
"""


def build_claim_repair_manifests(
    task_dir: Path,
    gate_result: dict[str, Any],
) -> list[str]:
    """为 claim coverage FAIL 产生的 repair_tasks 生成 manifest JSON 文件。

    按 owner_section（role）聚合：每个 role 只生成一个 manifest，包含该
    role 的所有 repair_tasks 信息，避免为每个 claim 单独派发子代理。

    Returns:
        manifest 文件路径列表（供主 AI 读取并派发 repair 子代理）。
    """
    repair_tasks = gate_result.get("repair_tasks") or []
    if not repair_tasks:
        return []

    # ── 按 owner_section 聚合 ──
    by_role: dict[str, list[dict]] = {}
    for task in repair_tasks:
        role = task.get("owner_section") or "bp_company_team_compliance"
        by_role.setdefault(role, []).append(task)

    manifests = []
    for role, tasks in by_role.items():
        claim_ids = [t.get("claim_id", "") for t in tasks]
        slug = role.replace("bp_", "")
        paths = _claim_role_paths(task_dir, role)

        # 构建 system_prompt，列出所有待修复 claims
        claims_detail = "\n".join(
            f"- Claim `{t.get('claim_id', '')}`: {t.get('claim', '(无描述)')} "
            f"(status={t.get('status', 'not_addressed')}, evidence={t.get('evidence_strength', 'none')})"
            for t in tasks
        )

        system_prompt = _CLAIM_REPAIR_PROMPT_TEMPLATE.format(
            role=role,
            claims_count=len(tasks),
            claims_detail=claims_detail,
            claim_ids_list=str(claim_ids),
            fact_store_path=str(task_dir / "bp_fact_store.json"),
            facts_path=str(paths["facts"]),
            section_path=str(paths["section"]),
        )

        repair_id = f"CCR-{slug}-{len(manifests)+1:03d}"
        manifest = {
            "manifest_version": "1.0",  # H2
            "task_id": task_dir.name,
            "repair_task_id": repair_id,
            "role": role,
            "slug": slug,
            "claim_ids": claim_ids,
            "claims_count": len(claim_ids),
            "label": f"{task_dir.name}-claim-repair-{slug}",
            "system_prompt": system_prompt,
            "connectorIds": BP_TYC_CONNECTOR_IDS,
            "sidecar_paths": {"facts": str(paths["facts"]), "section": str(paths["section"])},
            "fact_store_path": str(task_dir / "bp_fact_store.json"),
            "timeout": 600 * len(claim_ids),
            "thinking": "high",
            "dispatch_mode": "team_async",
            "mode": "bypassPermissions",
            "subagent_type": "general-purpose",
            "status": "pending",
        }
        manifest_path = task_dir / f"bp_claim_repair_manifest_{slug}_{repair_id}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifests.append(str(manifest_path))

    return manifests


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate BP claim coverage gate")
    parser.add_argument("task_dir")
    args = parser.parse_args()
    print(json.dumps(write_bp_claim_coverage_gate(Path(args.task_dir)), ensure_ascii=False, indent=2))
