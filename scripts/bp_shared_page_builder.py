#!/usr/bin/env python3
"""Build and refresh BP shared diligence page.

This module is the BP pipeline's shared reasoning hub. It converts the research
plan, claim matrix, fact store, and section packages into:
- bp_shared_state.json: machine-readable current state
- bp_claim_coverage.json: claim coverage gate input
- bp_open_questions.json: unresolved data gaps
- bp_evidence_conflicts.json: counter-evidence/conflict queue
- bp_shared_diligence_page.md: human/agent-readable handoff page
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_STATUS = {"supported", "partially_supported", "contradicted", "unverified", "not_addressed", "planned"}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _claim_rows(plan: dict[str, Any], claim_inventory: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    rows = _as_list(plan.get("claim_matrix"))
    if rows:
        return [dict(row) for row in rows if isinstance(row, dict)]
    raw_claims = claim_inventory.get("claims", []) if isinstance(claim_inventory, dict) else claim_inventory
    fallback: list[dict[str, Any]] = []
    for idx, item in enumerate(_as_list(raw_claims), 1):
        if isinstance(item, dict):
            claim = item.get("claim") or item.get("text") or item.get("bp_claim") or ""
            owner = item.get("owner_section") or item.get("owner") or "bp_竞争与结论"
            priority = item.get("priority") or item.get("importance") or "high"
            claim_id = item.get("claim_id") or item.get("id") or f"BC{idx:03d}"
        else:
            claim = str(item)
            owner = "bp_竞争与结论"
            priority = "high"
            claim_id = f"BC{idx:03d}"
        if claim:
            fallback.append({"claim_id": claim_id, "claim": claim, "owner_section": owner, "priority": priority, "status": "planned"})
    return fallback


def _package_items(section_packages_payload: dict[str, Any]) -> list[dict[str, Any]]:
    packages = []
    for item in _as_list(section_packages_payload.get("packages")):
        if isinstance(item, dict):
            package = item.get("package") if isinstance(item.get("package"), dict) else item
            packages.append(package)
    return packages


def _facts_from_store(fact_store: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(fact) for fact in _as_list(fact_store.get("facts")) if isinstance(fact, dict)]


def _looks_negative_or_gap(text: str) -> bool:
    markers = (
        "未发现", "未见", "无任何", "无法验证", "不可验证", "缺失", "没有",
        "无客户", "无订单", "无收入", "无交付", "无回款", "不支持", "反驳",
        "not found", "not verified", "missing", "no evidence",
    )
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _status_from_package_claim(
    claim: dict[str, Any],
    package: dict[str, Any] | None = None,
    facts_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    explicit_status = str(claim.get("status") or "").strip()
    if explicit_status in SUPPORTED_STATUS and explicit_status != "supported":
        return explicit_status

    package = package or {}
    facts_by_id = facts_by_id or {}
    fact_ids = [str(fid) for fid in _as_list(claim.get("fact_ids")) if str(fid).strip()]
    source_quality = str(claim.get("source_quality") or "").lower()
    confidence = str(claim.get("confidence") or "").lower()
    data_gaps = [str(gap) for gap in _as_list(package.get("data_gaps")) if str(gap).strip()]
    counter_evidence = [str(item) for item in _as_list(package.get("counter_evidence")) if str(item).strip()]
    reasoning = str(claim.get("reasoning") or "")

    fact_rows = [facts_by_id.get(fid, {}) for fid in fact_ids]
    fact_types = {str(fact.get("fact_type") or "").lower() for fact in fact_rows if fact}
    fact_text = "；".join(
        str(fact.get("claim") or fact.get("value") or fact.get("source_quote") or "")
        for fact in fact_rows
        if fact
    )
    evidence_text = "；".join([reasoning, fact_text, *counter_evidence, *data_gaps])

    if fact_types & {"evidence_gap", "data_gap", "missing_evidence", "unverified"}:
        return "unverified"
    if fact_types & {"negative_evidence", "counter_evidence", "contradiction"}:
        return "contradicted"
    if counter_evidence and _looks_negative_or_gap("；".join(counter_evidence)):
        return "contradicted"
    if data_gaps and _looks_negative_or_gap(evidence_text):
        return "unverified"
    if _looks_negative_or_gap(evidence_text) and not any(token in evidence_text for token in ("已验证", "已确认", "合同", "订单", "回款", "交付")):
        return "unverified"
    if fact_ids and source_quality in {"official", "regulatory", "database", "media", "research"}:
        return "supported"
    if fact_ids:
        return "partially_supported" if confidence != "low" else "unverified"
    return "unverified"


_STATUS_PRIORITY = {
    "planned": 0,
    "not_addressed": 1,
    "supported": 2,
    "partially_supported": 3,
    "unverified": 4,
    "contradicted": 5,
}


def _more_conservative_status(current_status: str, new_status: str) -> str:
    current = current_status or "not_addressed"
    new = new_status or "not_addressed"
    return new if _STATUS_PRIORITY.get(new, 3) > _STATUS_PRIORITY.get(current, 3) else current


def _merge_claim_status(claim_rows: list[dict[str, Any]], packages: list[dict[str, Any]], facts_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for row in claim_rows:
        claim_id = str(row.get("claim_id") or "").strip()
        if not claim_id:
            continue
        status[claim_id] = {
            "claim_id": claim_id,
            "claim": row.get("claim", ""),
            "priority": row.get("priority", "high"),
            "owner": row.get("owner_section") or row.get("owner") or "",
            "status": "not_addressed",
            "fact_ids": [],
            "data_gaps": [],
            "source_quality": "unknown",
        }

    for package in packages:
        package_gaps = [str(gap) for gap in _as_list(package.get("data_gaps")) if str(gap).strip()]
        for claim in _as_list(package.get("claims")):
            if not isinstance(claim, dict):
                continue
            claim_id = str(claim.get("claim_id") or "").strip()
            if not claim_id:
                matched = None
                claim_text = str(claim.get("claim") or "").strip()
                for known_id, known in status.items():
                    if claim_text and claim_text == str(known.get("claim") or "").strip():
                        matched = known_id
                        break
                claim_id = matched or ""
            if not claim_id:
                continue
            if claim_id not in status:
                status[claim_id] = {
                    "claim_id": claim_id,
                    "claim": claim.get("claim", ""),
                    "priority": "medium",
                    "owner": package.get("section_id") or package.get("section_title") or "",
                    "status": "not_addressed",
                    "fact_ids": [],
                    "data_gaps": [],
                    "source_quality": "unknown",
                }
            fact_ids = [str(fid) for fid in _as_list(claim.get("fact_ids")) if str(fid).strip()]
            current = status[claim_id]
            # Flatten to prevent nested lists (Bug 2: 'list' has no .get())
            existing_ids = [str(f) for f in _as_list(current.get("fact_ids")) if str(f).strip()]
            current["fact_ids"] = sorted(set(existing_ids + fact_ids))
            existing_gaps = [str(g) for g in _as_list(current.get("data_gaps")) if str(g).strip()]
            current["data_gaps"] = sorted(set(existing_gaps + package_gaps))
            current["source_quality"] = claim.get("source_quality") or current.get("source_quality") or "unknown"
            new_status = _status_from_package_claim(claim, package=package, facts_by_id=facts_by_id)
            current["status"] = _more_conservative_status(str(current.get("status") or "not_addressed"), new_status)
    return status


def _open_questions_from_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for package in packages:
        owner = package.get("section_id") or package.get("section_title") or "unknown"
        for idx, gap in enumerate(_as_list(package.get("data_gaps")), 1):
            gap_text = str(gap).strip()
            if gap_text:
                questions.append({"gap_id": f"G{len(questions) + 1:03d}", "gap": gap_text, "owner": owner, "impact": "影响投资判断或证据链完整性", "needed_from": "founder_or_external_source"})
    return questions


_HIGH_SEVERITY_KEYWORDS = (
    "失信", "诉讼", "处罚", "造假", "违法", "注销", "撤回", "终止",
    "同业竞争", "关联交易", "竞业限制", "知识产权纠纷", "侵权",
    "核心.*离职", "关键人", "实际控制人", "股权纠纷",
)
_DEAL_BREAKER_KEYWORDS = (
    "失信被执行人", "刑事", "欺诈", "造假", "知识产权诉讼",
    "竞业限制.*违反", "同业竞争.*未披露",
)
_LOW_SEVERITY_KEYWORDS = (
    "正面信号", "已验证", "有效", "认证", "合规", "无风险", "无诉讼",
    "排除", "通过", "稳定",
)

import re as _re

def _infer_risk_severity(text: str, stage_meta: dict | None = None) -> tuple[str, bool]:
    """根据风险文本内容动态推断 severity 和 is_deal_breaker。"""
    severity = "medium"
    is_deal_breaker = False

    # 正面信号 → low severity, 不是风险
    if any(kw in text for kw in _LOW_SEVERITY_KEYWORDS):
        # 检查是否是"正面信号排除了..."类描述
        if "正面" in text or "排除" in text or "无" in text[:10]:
            return "low", False

    # 高风险关键词
    for kw in _HIGH_SEVERITY_KEYWORDS:
        if _re.search(kw, text):
            severity = "high"
            break

    # Deal breaker 关键词
    for kw in _DEAL_BREAKER_KEYWORDS:
        if _re.search(kw, text):
            severity = "critical"
            is_deal_breaker = True
            break

    # stage_meta 覆盖
    if stage_meta:
        overrides = stage_meta.get("risk_severity_override", {})
        for risk_key, override_sev in overrides.items():
            # 简单关键词匹配 stage override
            risk_key_cn = risk_key.replace("_", "").replace("no", "无").replace("not", "不")
            if any(part in text for part in risk_key_cn.split() if len(part) > 1):
                severity = override_sev
                if override_sev == "critical":
                    is_deal_breaker = True
                break

    return severity, is_deal_breaker


def _risks_from_packages(packages: list[dict[str, Any]], stage_meta: dict | None = None) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for package in packages:
        owner = package.get("section_id") or package.get("section_title") or "unknown"
        for item in _as_list(package.get("counter_evidence")):
            text = str(item).strip()
            if text:
                severity, is_deal_breaker = _infer_risk_severity(text, stage_meta)
                risks.append({"risk_id": f"R{len(risks) + 1:03d}", "risk": text, "severity": severity, "evidence": owner, "is_deal_breaker": is_deal_breaker})
    return risks


def _coverage_payload(task_id: str, claim_status: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": 0, "supported": 0, "partially_supported": 0, "unverified": 0, "contradicted": 0, "not_addressed": 0, "critical_not_addressed": 0}
    for row in claim_status.values():
        if not isinstance(row, dict):
            continue
        summary["total"] += 1
        status = str(row.get("status") or "not_addressed")
        if status not in summary:
            summary[status] = 0
        summary[status] += 1
        if status in {"not_addressed", "unverified", "contradicted"} and str(row.get("priority") or "").lower() == "critical":
            summary["critical_not_addressed"] += 1
    return {"schema_version": "bp_claim_coverage.v1", "task_id": task_id, "summary": summary, "claims": list(claim_status.values()), "generated_at": datetime.now(timezone.utc).isoformat()}


def _packages_from_sidecars(task_dir: Path) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("bp_*-section.json")):
            path_key = str(path.resolve())
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            payload = _load_json(path, {})
            if isinstance(payload, dict):
                packages.append(payload)
    return packages


def _facts_from_sidecars(task_dir: Path) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for directory in [task_dir / "outputs", task_dir]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("bp_*-facts.json")):
            payload = _load_json(path, {})
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


def build_shared_state(task_dir: Path, after_wave: int = 0) -> dict[str, Any]:
    task_dir = Path(task_dir)
    plan = _load_json(task_dir / "bp_research_plan.json", {})
    claim_inventory = _load_json(task_dir / "bp_claim_inventory.json", {})
    fact_store = _load_json(task_dir / "bp_fact_store.json", {"facts": []})
    section_packages_payload = _load_json(task_dir / "bp_section_packages.json", {"packages": []})
    claim_rows = _claim_rows(plan, claim_inventory)
    packages = _package_items(section_packages_payload)
    if not packages:
        packages = _packages_from_sidecars(task_dir)
    facts = _facts_from_store(fact_store)
    known_fact_ids = {str(fact.get("fact_id")) for fact in facts if fact.get("fact_id")}
    for fact in _facts_from_sidecars(task_dir):
        fact_id = str(fact.get("fact_id") or "").strip()
        if fact_id and fact_id in known_fact_ids:
            continue
        if fact_id:
            known_fact_ids.add(fact_id)
        facts.append(fact)
    fact_index = {
        str(fact.get("fact_id")): fact
        for fact in facts
        if fact.get("fact_id")
    }
    claim_status = _merge_claim_status(claim_rows, packages, facts_by_id=fact_index)
    open_questions = _open_questions_from_packages(packages)
    entity = plan.get("entity") or fact_store.get("entity") or task_dir.name
    task_id = plan.get("task_id") or fact_store.get("task_id") or task_dir.name

    # ── P1-4: 融资阶段分级（提前到 risks 之前，以便动态 severity） ──
    from scripts.bp_stage_utils import classify_stage, get_stage_meta
    profile = _load_json(task_dir / "bp_step0_profile.json", {})
    financing_stage = profile.get("financing_stage", "")
    stage_tier = classify_stage(financing_stage)
    stage_meta = get_stage_meta(stage_tier)

    risks = _risks_from_packages(packages, stage_meta=stage_meta)

    return {
        "schema_version": "bp_shared_state.v1",
        "task_id": task_id,
        "entity": entity,
        "after_wave": after_wave,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stage_tier": stage_tier,
        "stage_label": stage_meta.get("label", ""),
        "financing_stage": financing_stage,
        "current_recommendation": {
            "verdict": "undecided",
            "confidence": "low",
            "supporting_reasons": [],
            "deal_breakers": [],
        },
        "claim_status": claim_status,
        "fact_index": fact_index,
        "open_questions": open_questions,
        "risks": risks,
        "conflicts": [],
        "wave_history": [{
            "wave": after_wave,
            "completed_roles": sorted({str(package.get("section_id") or package.get("section_title") or "unknown") for package in packages}),
            "new_facts": sorted(fact_index.keys()),
            "new_gaps": [item["gap_id"] for item in open_questions],
            "new_conflicts": [],
        }],
    }


def render_shared_page(state: dict[str, Any]) -> str:
    recommendation = state.get("current_recommendation") or {}
    claim_status = state.get("claim_status") or {}
    fact_index = state.get("fact_index") or {}
    risks = state.get("risks") or []
    gaps = state.get("open_questions") or []
    conflicts = state.get("conflicts") or []

    lines: list[str] = [
        f"# BP Shared Diligence Page — {state.get('entity', '目标公司')}",
        "",
    ]

    # P1-4: 融资阶段感知块 — 放在最前面，让所有子代理第一时间读到
    stage_tier = state.get("stage_tier") or "T1"
    if stage_tier in {"T1", "T2", "T3", "T4"}:
        from scripts.bp_stage_utils import build_stage_prompt_block
        lines.append(build_stage_prompt_block(stage_tier, state.get("entity", "")))
        lines.append("")

    supporting = [str(r) for r in _as_list(recommendation.get('supporting_reasons'))]
    breakers = [str(r) for r in _as_list(recommendation.get('deal_breakers'))]
    lines.extend([
        "## 1. 当前投资判断快照",
        f"- 当前建议：{recommendation.get('verdict', 'undecided')}",
        f"- 置信度：{recommendation.get('confidence', 'low')}",
        f"- 融资阶段：{state.get('stage_label', '')}（{state.get('financing_stage', '未知')}）",
        f"- 关键支持理由：{'；'.join(supporting) or '未形成'}",
        f"- 关键 Deal Breakers：{'；'.join(breakers) or '未形成'}",
        "- 下一步最重要的 5 个验证动作：",
    ])
    for gap in gaps[:5]:
        lines.append(f"  - {gap.get('gap')}")
    if not gaps:
        lines.append("  - 继续验证 critical/high BP claims 的外部证据")

    lines += ["", "## 2. BP 核心声称验证看板", "| Claim ID | BP 声称 | 重要性 | Owner | 当前状态 | 证据等级 | 下一步 |", "|---|---|---|---|---|---|---|"]
    for claim in claim_status.values():
        if not isinstance(claim, dict):
            continue
        fact_ids = [str(fid) for fid in (claim.get("fact_ids") or []) if str(fid).strip()]
        claim_status_val = str(claim.get("status") or "")
        next_step = "补充外部证据" if claim_status_val in {"not_addressed", "unverified"} else "保持追踪"
        lines.append(f"| {claim.get('claim_id')} | {claim.get('claim')} | {claim.get('priority')} | {claim.get('owner')} | {claim_status_val} | {claim.get('source_quality', 'unknown')} | {next_step} ({','.join(fact_ids) or 'no_fact'}) |")

    lines += ["", "## 3. 已确认事实", "| Fact ID | 事实 | 来源 | 置信度 | 影响哪个投资问题 |", "|---|---|---|---|---|"]
    for fact_id, fact in fact_index.items():
        lines.append(f"| {fact_id} | {fact.get('claim') or fact.get('value') or ''} | {fact.get('source_url') or fact.get('source_tier') or ''} | {fact.get('confidence', '')} | {fact.get('question_id') or fact.get('fact_type') or ''} |")
    if not fact_index:
        lines.append("| - | 暂无已合并外部事实 | - | - | - |")

    lines += ["", "## 4. 反证与风险", "| Risk ID | 风险/反证 | 严重度 | 支撑证据 | 是否 Deal Breaker |", "|---|---|---|---|---|"]
    for risk in risks:
        lines.append(f"| {risk.get('risk_id')} | {risk.get('risk')} | {risk.get('severity')} | {risk.get('evidence')} | {risk.get('is_deal_breaker')} |")
    if not risks:
        lines.append("| - | 暂无结构化反证 | - | - | False |")

    lines += ["", "## 5. 数据缺口", "| Gap ID | 缺口 | 影响 | Owner | 需要谁补充 |", "|---|---|---|---|---|"]
    for gap in gaps:
        lines.append(f"| {gap.get('gap_id')} | {gap.get('gap')} | {gap.get('impact')} | {gap.get('owner')} | {gap.get('needed_from')} |")
    if not gaps:
        lines.append("| - | 暂无结构化数据缺口 | - | - | - |")

    lines += ["", "## 6. 跨维度冲突", "| Conflict ID | 冲突 | 涉及模块 | 当前判断 | 处理动作 |", "|---|---|---|---|---|"]
    for conflict in conflicts:
        lines.append(f"| {conflict.get('conflict_id')} | {conflict.get('conflict')} | {conflict.get('modules')} | {conflict.get('judgment')} | {conflict.get('action')} |")
    if not conflicts:
        lines.append("| - | 暂无已识别冲突 | - | - | 持续监控 |")

    lines += [
        "",
        "## 7. Wave 交接指令",
        "### Next Wave Must Use",
        "- 必须复用的事实：见第 3 节 Fact ID，引用结论必须绑定 fact_id。",
        "- 禁止重复论证的内容：已 supported 的 claim 不要重新写百科式解释，只做交叉验证。",
        "- 必须继续验证的问题：见第 5 节数据缺口和第 2 节 not_addressed/unverified claims。",
        "- 不能进入主结论的未验证内容：所有仅来自 BP 或无 fact_id 的 critical/high claim。",
        "",
    ]
    return "\n".join(lines)


def write_shared_page_outputs(task_dir: Path, after_wave: int = 0) -> dict[str, Any]:
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    state = build_shared_state(task_dir, after_wave=after_wave)
    coverage = _coverage_payload(str(state.get("task_id") or task_dir.name), state.get("claim_status") or {})
    page = render_shared_page(state)

    state_path = task_dir / "bp_shared_state.json"
    page_path = task_dir / "bp_shared_diligence_page.md"
    coverage_path = task_dir / "bp_claim_coverage.json"
    open_questions_path = task_dir / "bp_open_questions.json"
    conflicts_path = task_dir / "bp_evidence_conflicts.json"

    _write_json(state_path, state)
    _write_json(coverage_path, coverage)
    _write_json(open_questions_path, {"schema_version": "bp_open_questions.v1", "task_id": state.get("task_id"), "open_questions": state.get("open_questions", [])})
    _write_json(conflicts_path, {"schema_version": "bp_evidence_conflicts.v1", "task_id": state.get("task_id"), "conflicts": state.get("conflicts", [])})
    page_path.write_text(page + "\n", encoding="utf-8")

    return {
        "state": state,
        "coverage": coverage,
        "paths": {
            "shared_state_path": str(state_path),
            "shared_page_path": str(page_path),
            "claim_coverage_path": str(coverage_path),
            "open_questions_path": str(open_questions_path),
            "evidence_conflicts_path": str(conflicts_path),
        },
    }
