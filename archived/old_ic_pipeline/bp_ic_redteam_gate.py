#!/usr/bin/env python3
"""Validate BP IC (Investment Committee) and Red Team outputs against evidence gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_IC_RECOMMENDATIONS = {"go", "proceed_with_caution", "no_go", "more_dd"}
IC_REQUIRED_FIELDS = {"schema_version", "recommendation", "supporting_reasons", "must_verify_before_investment", "deal_breakers", "open_data_gaps", "confidence"}
RT_REQUIRED_FIELDS = {"schema_version", "issues"}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalize_ic_thesis(ic_thesis: dict[str, Any]) -> dict[str, Any]:
    """Normalize IC thesis JSON to handle common sub-agent output variants.

    Sub-agents sometimes output:
    - recommendation as {"verdict": "pass", ...} instead of a plain string
    - confidence nested inside recommendation object
    - must_verify_items instead of must_verify_before_investment
    - high_issues + medium_issues instead of merged issues (RT)
    """
    if not isinstance(ic_thesis, dict):
        return ic_thesis

    out = dict(ic_thesis)

    # Fix: recommendation is an object like {"verdict": "pass", ...}
    rec = out.get("recommendation")
    if isinstance(rec, dict):
        # Extract verdict string
        verdict = str(rec.get("verdict") or rec.get("recommendation") or "").lower()
        verdict_map = {
            "pass": "go", "approve": "go", "proceed": "go",
            "pass_with_caution": "proceed_with_caution", "conditional": "proceed_with_caution",
            "reject": "no_go", "fail": "no_go",
            "more_dd": "more_dd", "more_diligence": "more_dd",
        }
        out["recommendation"] = verdict_map.get(verdict, verdict or "more_dd")
        # Extract confidence if nested inside recommendation
        if "confidence" not in out and "confidence" in rec:
            out["confidence"] = rec["confidence"]
    elif isinstance(rec, str):
        # Normalize common string variants
        rec_lower = rec.lower().strip()
        rec_map = {
            "pass": "go", "approve": "go", "proceed": "go",
            "pass_with_caution": "proceed_with_caution", "conditional": "proceed_with_caution",
            "reject": "no_go", "fail": "no_go",
        }
        if rec_lower in rec_map:
            out["recommendation"] = rec_map[rec_lower]

    # Fix: must_verify_items → must_verify_before_investment
    if "must_verify_before_investment" not in out and "must_verify_items" in out:
        out["must_verify_before_investment"] = out["must_verify_items"]

    # Fix: confidence normalization
    conf = str(out.get("confidence") or "").lower().strip()
    conf_map = {"medium-high": "medium", "moderate": "medium", "strong": "high", "weak": "low"}
    if conf in conf_map:
        out["confidence"] = conf_map[conf]

    return out


def _normalize_red_team(rt_review: dict[str, Any]) -> dict[str, Any]:
    """Normalize Red Team JSON to handle common sub-agent output variants.

    Sub-agents sometimes output:
    - high_issues + medium_issues instead of merged issues array
    - description field missing, using 'issue' or 'text' instead
    """
    if not isinstance(rt_review, dict):
        return rt_review

    out = dict(rt_review)

    # Fix: merge high_issues + medium_issues into issues
    if "issues" not in out or not isinstance(out.get("issues"), list) or not out.get("issues"):
        merged: list[dict[str, Any]] = []
        for severity_key, severity_label in [("high_issues", "HIGH"), ("medium_issues", "MEDIUM"), ("low_issues", "LOW")]:
            for item in (out.get(severity_key) or []):
                if isinstance(item, dict):
                    item["severity"] = severity_label
                    merged.append(item)
                elif isinstance(item, str):
                    merged.append({"severity": severity_label, "description": item})
        if merged:
            out["issues"] = merged

    # Fix: normalize issue field names (description / issue / text)
    if isinstance(out.get("issues"), list):
        normalized_issues: list[dict[str, Any]] = []
        for issue in out["issues"]:
            if not isinstance(issue, dict):
                normalized_issues.append(issue)
                continue
            if "description" not in issue:
                desc = str(issue.get("issue") or issue.get("text") or issue.get("finding") or "").strip()
                issue["description"] = desc
            if "severity" not in issue:
                issue["severity"] = "MEDIUM"
            normalized_issues.append(issue)
        out["issues"] = normalized_issues

    return out


def _validate_ic_thesis(ic_thesis: dict[str, Any], coverage: dict[str, Any], cross: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ic_thesis = ic_thesis if isinstance(ic_thesis, dict) else {}

    if not ic_thesis.get("schema_version"):
        issues.append({"severity": "HIGH", "code": "IC_MISSING_SCHEMA_VERSION", "message": "bp_investment_thesis.json missing schema_version"})
        return issues

    for field in IC_REQUIRED_FIELDS:
        if field not in ic_thesis:
            issues.append({"severity": "HIGH", "code": "IC_MISSING_REQUIRED_FIELD", "message": f"bp_investment_thesis.json missing field: {field}"})

    recommendation = str(ic_thesis.get("recommendation", "")).lower()
    if recommendation not in ALLOWED_IC_RECOMMENDATIONS:
        issues.append({"severity": "HIGH", "code": "IC_INVALID_RECOMMENDATION", "message": f"recommendation '{recommendation}' not in {ALLOWED_IC_RECOMMENDATIONS}"})

    for required_list in ("supporting_reasons", "must_verify_before_investment", "deal_breakers", "open_data_gaps"):
        if not isinstance(ic_thesis.get(required_list), list):
            issues.append({"severity": "HIGH", "code": "IC_MISSING_LIST_FIELD", "message": f"bp_investment_thesis.json {required_list} is not a list"})

    confidence = str(ic_thesis.get("confidence", "")).lower()
    if confidence not in {"high", "medium", "low"}:
        issues.append({"severity": "HIGH", "code": "IC_INVALID_CONFIDENCE", "message": f"confidence '{confidence}' not in high/medium/low"})

    coverage_ok = coverage.get("ok") is True or coverage.get("gate_verdict") in {"PASS", "PASS_WITH_DISCLOSURE"}
    cross_ok = cross.get("ok") is True or cross.get("gate_verdict") == "PASS"
    if recommendation == "go" and not coverage_ok:
        issues.append({"severity": "HIGH", "code": "IC_GO_BLOCKED_BY_FAILED_COVERAGE", "message": "IC recommendation is 'go' but coverage gate has not passed"})
    if recommendation == "go" and not cross_ok:
        issues.append({"severity": "HIGH", "code": "IC_GO_BLOCKED_BY_FAILED_CROSS_GATE", "message": "IC recommendation is 'go' but cross-dimension gate has not passed"})
    if recommendation in {"go", "proceed_with_caution"} and not ic_thesis.get("must_verify_before_investment"):
        issues.append({"severity": "MEDIUM", "code": "IC_NO_MUST_VERIFY_ITEMS", "message": "IC 'go'/'proceed_with_caution' without must_verify_before_investment items"})
    deal_breakers = ic_thesis.get("deal_breakers", [])
    if not isinstance(deal_breakers, list):
        deal_breakers = []
    unresolved = [db for db in deal_breakers if isinstance(db, dict) and str(db.get("status") or "").lower() != "resolved"]
    if recommendation == "go" and unresolved:
        issues.append({"severity": "HIGH", "code": "IC_GO_WITH_UNRESOLVED_DEAL_BREAKERS", "message": "IC 'go' with unresolved deal breakers"})
    if confidence == "high" and not coverage_ok:
        issues.append({"severity": "HIGH", "code": "IC_HIGH_CONFIDENCE_WITHOUT_COVERAGE", "message": "IC high confidence without coverage gate passing"})

    return issues


def _validate_red_team(rt_review: dict[str, Any], coverage: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rt_review = rt_review if isinstance(rt_review, dict) else {}

    if not rt_review.get("schema_version"):
        issues.append({"severity": "HIGH", "code": "RT_MISSING_SCHEMA_VERSION", "message": "bp_red_team_review.json missing schema_version"})
        return issues

    for field in RT_REQUIRED_FIELDS:
        if field not in rt_review:
            issues.append({"severity": "HIGH", "code": "RT_MISSING_REQUIRED_FIELD", "message": f"bp_red_team_review.json missing field: {field}"})

    rt_issues = rt_review.get("issues", [])
    if not isinstance(rt_issues, list):
        rt_issues = []

    for idx, issue in enumerate(rt_issues):
        if not isinstance(issue, dict):
            issues.append({"severity": "HIGH", "code": "RT_INVALID_ISSUE_STRUCTURE", "message": f"Red Team issue {idx} is not an object"})
            continue
        severity = str(issue.get("severity", "")).upper()
        if severity not in {"HIGH", "MEDIUM", "LOW"}:
            issues.append({"severity": "HIGH", "code": "RT_ISSUE_INVALID_SEVERITY", "message": f"Red Team issue {idx} severity '{severity}' not HIGH/MEDIUM/LOW"})
        desc = str(issue.get("description", "")).strip()
        if not desc:
            issues.append({"severity": "HIGH", "code": "RT_ISSUE_MISSING_DESCRIPTION", "message": f"Red Team issue {idx} missing description"})

    critical_gaps = [
        claim for claim in (coverage.get("coverage") or {}).get("claims", []) or coverage.get("claims", []) or []
        if isinstance(claim, dict) and str(claim.get("priority", "")).lower() == "critical" and str(claim.get("status", "")).lower() != "supported"
    ]
    for gap in critical_gaps:
        claim_id = str(gap.get("claim_id", ""))
        claim_text = str(gap.get("claim") or "").lower().strip()
        # Check 1: exact claim_id match
        mentioned_by_id = any(
            str(issue.get("claim_id", "")) == claim_id
            for issue in rt_issues
            if isinstance(issue, dict)
        )
        # Check 2: text match — sub-agent may reference claim content without knowing claim_id
        mentioned_by_text = False
        if not mentioned_by_id and claim_text and len(claim_text) > 5:
            for issue in rt_issues:
                if not isinstance(issue, dict):
                    continue
                desc = str(issue.get("description") or "").lower()
                # Match if any 4+ word substring of the claim text appears in the issue description
                claim_words = claim_text.split()
                if len(claim_words) >= 4:
                    # Check sliding window of 4 words
                    for i in range(len(claim_words) - 3):
                        window = " ".join(claim_words[i:i+4])
                        if window in desc:
                            mentioned_by_text = True
                            break
                if mentioned_by_text:
                    break
        if not mentioned_by_id and not mentioned_by_text:
            issues.append({
                "severity": "HIGH",
                "code": "RED_TEAM_DID_NOT_ATTACK_CRITICAL_GAP",
                "message": f"Red Team did not address critical claim {claim_id} with status {gap.get('status')}",
                "claim_id": claim_id,
            })

    if not rt_issues and not rt_review.get("explicit_clearance"):
        issues.append({"severity": "HIGH", "code": "RED_TEAM_NO_ISSUES_NO_EXPLICIT_CLEARANCE", "message": "Red Team found no issues but did not provide explicit clearance with evidence"})

    return issues


def evaluate_bp_ic_redteam_gate(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    coverage = _load_json(task_dir / "bp_claim_coverage_gate.json", {})
    cross = _load_json(task_dir / "bp_cross_dimension_gate.json", {})
    ic_thesis = _load_json(task_dir / "bp_investment_thesis.json", {})
    rt_review = _load_json(task_dir / "bp_red_team_review.json", {})

    # Bug 4 fix: normalize sub-agent output variants before validation
    ic_thesis = _normalize_ic_thesis(ic_thesis)
    rt_review = _normalize_red_team(rt_review)

    issues: list[dict[str, Any]] = []
    issues.extend(_validate_ic_thesis(ic_thesis, coverage, cross))
    issues.extend(_validate_red_team(rt_review, coverage))

    high_count = sum(1 for issue in issues if issue.get("severity") == "HIGH")
    verdict = "FAIL" if high_count else "PASS"

    result: dict[str, Any] = {
        "schema_version": "bp_ic_redteam_gate.v1",
        "ok": verdict == "PASS",
        "gate_verdict": verdict,
        "issues": issues,
        "summary": {"high_count": high_count, "total_issues": len(issues)},
    }
    path = task_dir / "bp_ic_redteam_gate.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
