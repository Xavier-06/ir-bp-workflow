#!/usr/bin/env python3
"""Final hard gate for BP delivery.

Checks assembly, readability, claim coverage, debate, cross-dimension,
verification, source quality, fact store integrity, and sidecar JSON validity.
Also runs WARN-level checks for source completeness, adversarial warnings,
and gate degradation (repair_exhausted) for T1/T2 early-stage projects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.bp_utils import load_json
from runtime.profiles.bp_constants import BP_ALL_ROLE_SLUGS

# 维度 slug — 用于检查维度输出文件完整性（从 bp_constants 动态导入，加角色只需改一处）
_BP_ALL_SLUGS = list(BP_ALL_ROLE_SLUGS.values())


def _investment_conclusion_section(markdown: str) -> str:
    marker = "## 1. 投资结论"
    start = markdown.find(marker)
    if start < 0:
        return markdown
    rest = markdown[start + len(marker):]
    next_heading = rest.find("\n## ")
    return rest if next_heading < 0 else rest[:next_heading]


def _section_packages(task_dir: Path) -> list[dict[str, Any]]:
    section_index = load_json(task_dir / "bp_section_packages.json", {})
    packages: list[dict[str, Any]] = []
    for item in section_index.get("packages", []) or []:
        if not isinstance(item, dict):
            continue
        package = item.get("package") if isinstance(item.get("package"), dict) else {}
        if package:
            packages.append(package)
    return packages


def _bp_only_main_claims(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for package in packages:
        section_id = str(package.get("section_id") or package.get("section_title") or "")
        for claim in package.get("claims", []) or []:
            if not isinstance(claim, dict):
                continue
            confidence = str(claim.get("confidence") or "").lower()
            source_quality = str(claim.get("source_quality") or "").lower()
            fact_ids = [str(fact_id).strip() for fact_id in claim.get("fact_ids", []) or [] if str(fact_id).strip()]
            used_in_main = claim.get("used_in_main_conclusion") is True or str(claim.get("role") or "").lower() == "main_conclusion"
            if confidence == "high" and source_quality == "bp" and fact_ids and used_in_main:
                claims.append({"section_id": section_id, "claim_id": claim.get("claim_id", ""), "claim": claim.get("claim", "")})
    return claims


def _fact_ids_from_packages(packages: list[dict[str, Any]]) -> set[str]:
    fact_ids: set[str] = set()
    for package in packages:
        for fact_id in package.get("facts_used", []) or []:
            if str(fact_id).strip():
                fact_ids.add(str(fact_id).strip())
        for field in ("answers", "narrative_blocks", "claims"):
            for item in package.get(field, []) or []:
                if isinstance(item, dict):
                    for fact_id in item.get("fact_ids", []) or []:
                        if str(fact_id).strip():
                            fact_ids.add(str(fact_id).strip())
    return fact_ids


def evaluate_bp_delivery_gate(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    checks: list[dict[str, Any]] = []
    
    # Read stage_tier — 统一使用 bp_stage_utils.read_stage_from_task
    from scripts.bp_stage_utils import read_stage_from_task
    stage_tier = read_stage_from_task(task_dir, default="T4")
    is_early_stage = stage_tier in {"T1", "T2"}
    
    # Track deferred fixes for early stage delivery
    deferred_fixes: list[dict[str, Any]] = []

    assembly = load_json(task_dir / "bp_final_assembly.json", {})
    if not assembly.get("ok") or not assembly.get("markdown_path"):
        checks.append({"name": "final_assembly", "ok": False, "reason": "FINAL_ASSEMBLY_NOT_READY"})

    # Readability check — phase31 已降级为 WARN，这里统一记录到 deferred_fixes
    readability = load_json(task_dir / "bp_readability_review.json", {})
    if readability.get("verdict") not in ("PASS", None):
        # WARN / FAIL（phase31 已将 FAIL 降级为 WARN，但兼容旧 job 的 FAIL）
        deferred_fixes.append({
            "check": "readability",
            "severity": readability.get("degraded_from", "FAIL"),
            "reason": "READABILITY_REWRITE_REQUIRED",
            "t1_degradation": is_early_stage,
            "fix_suggestion": "优化报告可读性：缩短超长段落、消除重复短语、解释技术术语"
        })

    coverage_gate = load_json(task_dir / "bp_claim_coverage_gate.json", {})
    coverage_verdict = coverage_gate.get("gate_verdict")
    repair_exhausted = coverage_gate.get("repair_exhausted") is True
    coverage_attempt = int(coverage_gate.get("attempt", 0) or 0)
    if not coverage_gate:
        checks.append({"name": "claim_coverage", "ok": False, "reason": "CLAIM_COVERAGE_GATE_MISSING"})
    elif coverage_verdict not in {"PASS", "PASS_WITH_DISCLOSURE", "FAIL", "REPAIR"}:
        checks.append({"name": "claim_coverage", "ok": False, "reason": "CLAIM_COVERAGE_GATE_INVALID", "payload": coverage_gate})
    elif coverage_verdict == "PASS_WITH_DISCLOSURE":
        # 2026-06-26: PASS_WITH_DISCLOSURE 降级为 deferred_fixes，不再阻断交付
        deferred_fixes.append({
            "check": "claim_coverage",
            "severity": "WARN",
            "reason": "PASS_WITH_DISCLOSURE_DELIVERABLE_WITH_WARNINGS",
            "fix_suggestion": "Claim 覆盖度有 disclosure 标记，建议后续补充验证"
        })
    elif coverage_gate.get("ok") is False or coverage_verdict == "FAIL":
        if repair_exhausted:
            # 已充分 repair 仍 FAIL → 降级为 WARN，允许交付
            deferred_fixes.append({
                "check": "claim_coverage",
                "severity": "FAIL",
                "reason": "CLAIM_COVERAGE_FAILED_AFTER_REPAIR",
                "repair_exhausted": True,
                "attempt": coverage_attempt,
                "fix_suggestion": "经多轮 repair 仍无法获取外部证据的 claim，建议在后续 DD 中重点关注"
            })
        else:
            checks.append({"name": "claim_coverage", "ok": False,
                           "reason": coverage_gate.get("block_reason") or "CLAIM_COVERAGE_FAILED",
                           "payload": coverage_gate})

    # ── 统一检查所有 evidence gate 的降级标记 ──
    for gate_filename in [
        "bp_wave1_evidence_gate.json",
        "bp_wave3_evidence_gate.json",
        "bp_wave4_evidence_gate.json",
    ]:
        gate_data = load_json(task_dir / gate_filename, {})
        if gate_data.get("repair_exhausted") or gate_data.get("blocking_claims_degraded"):
            deferred_fixes.append({
                "check": gate_filename.replace(".json", ""),
                "severity": "WARN",
                "reason": "GATE_DEGRADED_AFTER_REPAIR",
                "repair_exhausted": True,
                "fix_suggestion": f"{gate_filename} 经 repair 后仍无法完全通过，已降级放行"
            })

    # Debate review check（2026-06-26 宽松化联动）
    # FAIL_BLOCKING → 硬阻断；WARN → 记录但不阻断（所有 stage tier 统一）
    debate = load_json(task_dir / "bp_debate_review.json", {})
    debate_verdict = debate.get("verdict", "PASS")
    if debate_verdict == "FAIL_BLOCKING":
        # 仅极端情况硬阻断（维度完全为空 / 全量 claim 无 fact_ids）
        checks.append({"name": "debate", "ok": False, "reason": "DEBATE_REVIEW_FAIL_BLOCKING", "payload": debate})
    elif debate_verdict == "WARN":
        # WARN：记录到 deferred_fixes 但不阻断（统一所有 stage tier）
        deferred_fixes.append({
            "check": "debate",
            "severity": "WARN",
            "reason": "DEBATE_REVIEW_WARN",
            "fix_suggestion": "对抗评审有 WARN 级问题，建议后续优化报告逻辑一致性，不阻断交付"
        })
    # 其他（PASS / REWRITE_REQUIRED 等旧 verdict）→ 不阻断

    cross_gate_path = task_dir / "bp_cross_dimension_gate.json"
    if cross_gate_path.exists():
        cross_gate = load_json(cross_gate_path, {})
        cross_verdict = cross_gate.get("gate_verdict")
        if cross_verdict == "FAIL" or cross_gate.get("ok") is False:
            checks.append({"name": "cross_dimension", "ok": False, "reason": "CROSS_DIMENSION_GATE_FAILED", "payload": cross_gate})
        elif cross_verdict != "PASS":
            checks.append({"name": "cross_dimension", "ok": False, "reason": "CROSS_DIMENSION_GATE_INVALID", "payload": cross_gate})
    else:
        checks.append({"name": "cross_dimension", "ok": False, "reason": "CROSS_DIMENSION_GATE_MISSING"})

    verification = load_json(task_dir / "bp_verification_result.json", {})
    if not verification:
        if is_early_stage:
            deferred_fixes.append({"check": "verification", "severity": "FAIL", "reason": "VERIFICATION_RESULT_MISSING", "t1_degradation": True, "fix_suggestion": "补充对抗验证"})
        else:
            checks.append({"name": "verification", "ok": False, "reason": "VERIFICATION_RESULT_MISSING"})
    elif verification.get("verdict") == "FAIL" or int(verification.get("fail", 0) or 0) > 0:
        if is_early_stage:
            # T1/T2: 验证 FAIL 降级为 WARN（报告可以交付后迭代修正）
            deferred_fixes.append({
                "check": "verification", "severity": "FAIL", "reason": "VERIFICATION_FAILED",
                "t1_degradation": True,
                "fix_suggestion": "对抗验证有 FAIL 项，建议后续迭代修正：补充来源标注、添加流动性折价、多种估值方法交叉验证等"
            })
        else:
            checks.append({"name": "verification", "ok": False, "reason": "VERIFICATION_FAILED", "payload": verification})


    packages = _section_packages(task_dir)
    bp_only_claims = _bp_only_main_claims(packages)
    if bp_only_claims:
        if is_early_stage:
            deferred_fixes.append({"check": "source_quality", "severity": "FAIL", "reason": "BP_ONLY_MAIN_CONCLUSION", "t1_degradation": True, "fix_suggestion": "部分核心结论仅依赖 BP 自述，建议后续补充独立来源验证"})
        else:
            checks.append({"name": "source_quality", "ok": False, "reason": "BP_ONLY_MAIN_CONCLUSION", "payload": {"claims": bp_only_claims}})
    fact_ids = _fact_ids_from_packages(packages)
    assembly_fact_ids = {str(fact_id).strip() for fact_id in assembly.get("facts_used", []) or [] if str(fact_id).strip()}
    missing_fact_ids = sorted(fact_id for fact_id in fact_ids if fact_id not in assembly_fact_ids)
    if missing_fact_ids:
        checks.append({"name": "fact_store", "ok": False, "reason": "FINAL_ASSEMBLY_FACT_STORE_NOT_REFERENCED", "payload": {"missing_fact_ids": missing_fact_ids}})

    # ── WARN-level checks (不阻断交付，但记录警告) ──────────────
    warnings: list[dict[str, Any]] = []
    
    # Add deferred fixes as warnings for T1/T2
    if deferred_fixes:
        for df in deferred_fixes:
            warnings.append({
                "name": df["check"],
                "severity": "WARN",
                "reason": df["reason"],
                "payload": {"t1_degradation": df.get("t1_degradation"), "issue_count": df.get("issue_count"), "fix_suggestion": df.get("fix_suggestion")}
            })

    # WARN-1: 来源完整性 — synthesis.md 中"来源与参考"不能为空
    synthesis_path = task_dir / "bp_synthesis.md"
    if synthesis_path.exists():
        synthesis_text = synthesis_path.read_text(encoding="utf-8")
        source_section = ""
        for marker in ("## 来源与参考", "## 来源", "## 参考文献", "## References"):
            idx = synthesis_text.find(marker)
            if idx >= 0:
                source_section = synthesis_text[idx:]
                break
        # 检查来源章节是否有实质内容（至少 5 条脚注定义或 URL）
        footnote_count = source_section.count("[^")
        url_count = source_section.count("http")
        if footnote_count < 5 and url_count < 5:
            warnings.append({"name": "source_completeness", "severity": "WARN",
                            "reason": f"SYNTHESIS_SOURCES_TOO_FEW (脚注={footnote_count}, URL={url_count})"})

    # WARN-2: claim unverified 占比 — critical/high claim 中 unverified > 50%
    coverage_data = load_json(task_dir / "bp_claim_coverage.json", {})
    coverage_summary = coverage_data.get("summary", {})
    total_claims = coverage_summary.get("total", 0)
    unverified_claims = coverage_summary.get("unverified", 0)
    if total_claims > 0 and unverified_claims / total_claims > 0.5:
        warnings.append({"name": "claim_unverified_ratio", "severity": "WARN",
                        "reason": f"HIGH_UNVERIFIED_RATIO ({unverified_claims}/{total_claims} = {unverified_claims/total_claims:.0%})"})

    # WARN-3: 对抗验证 WARN 数量 ≥ 3
    if verification:
        warn_count = int(verification.get("warn", 0) or 0)
        if warn_count >= 3:
            warnings.append({"name": "adversarial_warnings", "severity": "WARN",
                            "reason": f"ADVERSARIAL_HIGH_WARN_COUNT ({warn_count} warnings)"})

    # WARN-4: 维度输出文件完整性 — 8 个维度的 .md 是否都存在
    missing_dims: list[str] = []
    for slug in _BP_ALL_SLUGS:
        dim_found = False
        for prefix_dir in [task_dir, task_dir / "outputs"]:
            dim_path = prefix_dir / f"bp_phase2_{slug}.md"
            if dim_path.exists() and dim_path.stat().st_size > 100:
                dim_found = True
                break
        if not dim_found:
            missing_dims.append(slug)
    if missing_dims:
        warnings.append({"name": "dimension_completeness", "severity": "WARN",
                        "reason": f"DIMENSION_OUTPUTS_MISSING ({len(missing_dims)}/8): {', '.join(missing_dims)}"})

    # WARN-5: sidecar 文件 JSON 合法性
    for slug in _BP_ALL_SLUGS:
        for prefix_dir in [task_dir, task_dir / "outputs"]:
            for suffix in ("-facts.json", "-section.json"):
                sidecar_path = prefix_dir / f"bp_phase2_{slug}{suffix}"
                if sidecar_path.exists():
                    try:
                        json.loads(sidecar_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, Exception):
                        warnings.append({
                            "name": "sidecar_integrity",
                            "severity": "WARN",
                            "reason": f"SIDECAR_JSON_INVALID: {sidecar_path.name}"
                        })

    first_failure = checks[0] if checks else {}
    result = {
        "ok": not checks,
        "deliver_to_user": not checks,
        "docx_path": "",
        "block_reason": first_failure.get("reason", ""),
        "failed_checks": checks,
        "warnings": warnings,
        "warning_count": len(warnings),
        "stage_tier": stage_tier,
        "is_early_stage": is_early_stage,
        "deferred_fixes_count": len(deferred_fixes),
    }
    
    # Write deferred fixes file if T1/T2 had degraded checks
    if deferred_fixes:
        import datetime as _dt
        deferred_fixes_data = {
            "stage_tier": stage_tier,
            "is_early_stage": is_early_stage,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "deferred_fixes": deferred_fixes,
            "note": "以下检查在 T1/T2 早期阶段被降级为 WARN，允许交付但建议在后续 DD 中修复"
        }
        deferred_fixes_path = task_dir / "delivery_deferred_fixes.json"
        deferred_fixes_path.write_text(
            json.dumps(deferred_fixes_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    
    return result


def write_bp_delivery_gate(task_dir: Path) -> dict[str, Any]:
    result = evaluate_bp_delivery_gate(task_dir)
    path = Path(task_dir) / "bp_delivery_gate.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"gate_path": str(path)}
