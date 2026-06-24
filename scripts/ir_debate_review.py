#!/usr/bin/env python3
"""Generic debate review for IR quality-production section packages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"


def run_debate_review(section_index: dict[str, Any]) -> dict[str, Any]:
    task_id = section_index.get("task_id", "")
    packages = section_index.get("packages", []) or []
    issues: list[dict[str, Any]] = []

    if not packages:
        issues.append({
            "severity": "HIGH",
            "code": "NO_SECTION_PACKAGES",
            "section": "global",
            "issue": "No section packages available for review",
            "required_action": "Run section writers and package validation before final assembly",
        })

    for item in packages:
        step_name = item.get("step_name", "unknown")
        package = item.get("package", {}) or {}
        validation = item.get("validation", {}) or {}

        for validation_issue in validation.get("issues", []) or []:
            severity = "HIGH" if validation_issue.get("severity") == "FAIL" else "MEDIUM"
            issues.append({
                "severity": severity,
                "code": validation_issue.get("code", "VALIDATION_ISSUE"),
                "section": step_name,
                "issue": validation_issue.get("message", "Section package validation issue"),
                "required_action": "Rewrite this section package to satisfy the shared output protocol",
            })

        if not package.get("counter_evidence"):
            issues.append({
                "severity": "MEDIUM",
                "code": "MISSING_COUNTER_EVIDENCE",
                "section": step_name,
                "issue": "Section package lacks counter evidence or uncertainty discussion",
                "required_action": "Add counter evidence, bear-case evidence, or explicit uncertainty limits",
            })

        claims = package.get("claims", []) or []
        for idx, claim in enumerate(claims):
            if not claim.get("fact_ids"):
                issues.append({
                    "severity": "HIGH",
                    "code": "CLAIM_WITHOUT_FACTS",
                    "section": step_name,
                    "issue": f"Claim {idx} is not bound to fact_ids",
                    "required_action": "Bind the claim to Fact Store fact_ids or move it to data_gaps",
                })
            if claim.get("confidence") == "high" and claim.get("source_quality") in ("unknown", "auxiliary", "low"):
                issues.append({
                    "severity": "HIGH",
                    "code": "HIGH_CONFIDENCE_LOW_SOURCE",
                    "section": step_name,
                    "issue": f"Claim {idx} has high confidence but weak source quality",
                    "required_action": "Downgrade confidence or replace with authoritative evidence",
                })

    high_count = sum(1 for issue in issues if issue["severity"] == "HIGH")
    verdict = "REWRITE_REQUIRED" if high_count > 0 else ("WARN" if issues else "PASS")
    return {
        "task_id": task_id,
        "verdict": verdict,
        "issue_count": len(issues),
        "high_count": high_count,
        "issues": issues,
    }


def write_debate_review(task_id: str, tasks_dir: Path = TASKS_DIR) -> str:
    tasks_dir = Path(tasks_dir)
    index_path = tasks_dir / f"{task_id}-section_packages.json"
    if index_path.exists():
        section_index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        section_index = {"task_id": task_id, "summary": {"total": 0, "passed": 0, "failed": 0}, "packages": []}
    review = run_debate_review(section_index)
    output = tasks_dir / f"{task_id}-debate_review.json"
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(output)


def load_debate_review(task_id: str, tasks_dir: Path = TASKS_DIR) -> dict[str, Any] | None:
    path = Path(tasks_dir) / f"{task_id}-debate_review.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
