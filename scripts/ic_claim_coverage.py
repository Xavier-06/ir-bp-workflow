#!/usr/bin/env python3
"""IC Claim Coverage Validator — 行业研究 claim 覆盖校验 v1.0

读取 ic_research_plan.json 的 claim_matrix，检查每条 claim 是否在对应 step 输出中有所覆盖。

验证方式：
1. 对于每个 claim，找到其 owner_step 对应的 step 输出文件
2. 在 step 输出中搜索 claim 关键词（包含至少 3 个关键词视为"可能覆盖"）
3. 更宽松的匹配：claim 中的核心实体出现在 step 输出中也算

输出：coverage report with PASS/FAIL/WARN per claim
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def _extract_keywords(text: str, min_len: int = 2) -> list[str]:
    """Extract meaningful keywords from claim text."""
    # 提取中文词和英文词
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)
    # 去重 + 去停用词
    stopwords = {'的是', '是否', '什么', '如何', '怎样', '以及', '及其', '或者',
                 'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'was'}
    keywords = [w for w in words if w.lower() not in stopwords]
    return list(dict.fromkeys(keywords))  # ordered dedup


def _find_claim_in_text(claim: str, step_text: str) -> dict[str, Any]:
    """Check if a claim is covered in step output text."""
    keywords = _extract_keywords(claim)
    if not keywords:
        return {"covered": False, "reason": "no_extractable_keywords", "matched_keywords": []}

    # Count matched keywords
    matched = []
    for kw in keywords:
        if kw.lower() in step_text.lower():
            matched.append(kw)

    match_ratio = len(matched) / len(keywords) if keywords else 0

    # Coverage determination:
    # >= 50% keyword match → covered
    # >= 25% keyword match → possibly covered (PARTIAL)
    # < 25% → not covered
    if match_ratio >= 0.5:
        return {"covered": True, "level": "COVERED", "matched_keywords": matched, "match_ratio": match_ratio}
    elif match_ratio >= 0.25:
        return {"covered": True, "level": "PARTIAL", "matched_keywords": matched, "match_ratio": match_ratio}
    else:
        return {"covered": False, "level": "NOT_COVERED", "matched_keywords": matched, "match_ratio": match_ratio}


def run_claim_coverage(
    task_id: str,
    research_plan_path: Path,
    step_outputs: dict[str, Path],
    tasks_dir: Path | None = None,
) -> dict[str, Any]:
    """Run claim coverage validation.

    Args:
        task_id: task identifier
        research_plan_path: path to ic_research_plan.json
        step_outputs: {step_name: output_file_path} for all completed steps
        tasks_dir: output directory

    Returns:
        coverage validation result
    """
    if not research_plan_path.exists():
        return {
            "schema_version": "ic_claim_coverage.v1",
            "task_id": task_id,
            "overall_verdict": "FAIL",
            "error": "research_plan.json not found",
        }

    plan = json.loads(research_plan_path.read_text(encoding="utf-8"))
    claim_matrix = plan.get("claim_matrix", [])

    if not claim_matrix:
        return {
            "schema_version": "ic_claim_coverage.v1",
            "task_id": task_id,
            "overall_verdict": "WARN",
            "error": "claim_matrix is empty, nothing to validate",
        }

    # Map owner_step to output files
    # IC step names may be generic (step_tech) while output files are specific (step_tech_upstream)
    step_text_map: dict[str, str] = {}
    for step_name, output_path in step_outputs.items():
        if output_path.exists():
            try:
                step_text_map[step_name] = output_path.read_text(encoding="utf-8")
            except Exception:
                pass

    per_claim: list[dict[str, Any]] = []
    covered_count = 0
    partial_count = 0
    not_covered_count = 0

    for claim in claim_matrix:
        claim_id = claim.get("claim_id", "unknown")
        claim_text = claim.get("claim", "")
        owner_step = claim.get("owner_step", "")
        priority = claim.get("priority", "medium")

        # Find matching step output
        matched_text = ""
        matched_step = ""
        for step_name, text in step_text_map.items():
            if owner_step in step_name or step_name.startswith(owner_step):
                matched_text = text
                matched_step = step_name
                break

        if not matched_text:
            per_claim.append({
                "claim_id": claim_id,
                "claim": claim_text[:120],
                "owner_step": owner_step,
                "priority": priority,
                "coverage": "NOT_COVERED",
                "reason": f"no step output found for owner_step '{owner_step}'",
                "matched_step": None,
            })
            not_covered_count += 1
            continue

        result = _find_claim_in_text(claim_text, matched_text)
        coverage_level = result["level"]

        if coverage_level == "COVERED":
            covered_count += 1
        elif coverage_level == "PARTIAL":
            partial_count += 1
        else:
            not_covered_count += 1

        per_claim.append({
            "claim_id": claim_id,
            "claim": claim_text[:120],
            "owner_step": owner_step,
            "priority": priority,
            "coverage": coverage_level,
            "matched_step": matched_step,
            "matched_keywords": result["matched_keywords"][:8],
            "match_ratio": round(result["match_ratio"], 2),
        })

    # Overall verdict
    total = len(claim_matrix)
    if total == 0:
        overall = "WARN"
    elif not_covered_count > total * 0.3:
        overall = "FAIL"
    elif not_covered_count > 0:
        overall = "WARN"  # Some claims not covered but majority are
    else:
        overall = "PASS"

    result = {
        "schema_version": "ic_claim_coverage.v1",
        "task_id": task_id,
        "overall_verdict": overall,
        "summary": {
            "total_claims": total,
            "covered": covered_count,
            "partial": partial_count,
            "not_covered": not_covered_count,
            "coverage_rate": round((covered_count + partial_count) / max(total, 1), 2),
        },
        "per_claim": per_claim,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if tasks_dir:
        gate_path = Path(tasks_dir) / f"{task_id}-ic_claim_coverage.json"
        gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return result
