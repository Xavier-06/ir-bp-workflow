#!/usr/bin/env python3
"""IC Evidence Gate — 行业研究 step 输出质量门禁 v1.0

检查每个完成 step 的输出质量，产生证据门禁判定。
简于 BP 版（BP 有 fact_store + sidecar），IC 直接检查 step MD 文件。

检查维度：
- CITATIONS: 是否有来源引用
- CONTENT_LENGTH: 内容长度是否达标
- STRUCTURE: 是否有基本结构（标题+段落+结论）
- LINK_DENSITY: URL 引用密度

Verdict: PASS / WARN / FAIL
FAIL → 管线终止。WARN → 记录在 deferred_fixes 中。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


# ── 阈值 ──
MIN_CONTENT_LENGTH = 500        # 最少字符数
MIN_URL_COUNT = 3               # 最少 URL 引用数
MIN_SECTIONS = 4                # 最少章节数
WARN_CONTENT_LENGTH = 1500      # 内容偏短的警告阈值
WARN_URL_COUNT = 5              # URL 偏少的警告阈值


def run_evidence_gate(
    task_id: str,
    step_outputs: dict[str, Path],
    tasks_dir: Path | None = None,
) -> dict[str, Any]:
    """Run evidence gate on all completed step outputs.

    Args:
        task_id: task identifier
        step_outputs: {step_name: output_file_path} for all completed steps
        tasks_dir: output directory for gate result

    Returns:
        gate result with per-step verdicts and overall verdict
    """
    per_step: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for step_name, output_path in step_outputs.items():
        if not output_path.exists():
            per_step[step_name] = {
                "verdict": "FAIL",
                "reason": "Output file not found",
                "metrics": {},
            }
            issues.append({"step": step_name, "type": "MISSING_FILE", "severity": "BLOCKING"})
            continue

        try:
            text = output_path.read_text(encoding="utf-8")
        except Exception:
            per_step[step_name] = {
                "verdict": "FAIL",
                "reason": "Cannot read output file",
                "metrics": {},
            }
            issues.append({"step": step_name, "type": "UNREADABLE", "severity": "BLOCKING"})
            continue

        content_len = len(text)
        urls = list(set(re.findall(r'https?://[^\s\)\]>]+', text)))
        url_count = len(urls)

        # Count sections (## headers)
        sections = len(re.findall(r'^#{2,4}\s+', text, re.MULTILINE))

        # Check for citations section
        has_citations = bool(re.search(r'(来源|引用|参考|Citations?|References?)', text, re.IGNORECASE))

        metrics = {
            "content_length": content_len,
            "url_count": url_count,
            "section_count": sections,
            "has_citations_section": has_citations,
        }

        step_issues = []
        step_warnings = []

        # Mandatory checks
        if content_len < MIN_CONTENT_LENGTH:
            step_issues.append({
                "type": "CONTENT_TOO_SHORT",
                "detail": f"content {content_len} chars < {MIN_CONTENT_LENGTH} minimum",
                "severity": "BLOCKING",
            })

        if url_count < MIN_URL_COUNT:
            step_issues.append({
                "type": "INSUFFICIENT_CITATIONS",
                "detail": f"only {url_count} URLs < {MIN_URL_COUNT} minimum",
                "severity": "BLOCKING",
            })

        if sections < MIN_SECTIONS:
            step_issues.append({
                "type": "INSUFFICIENT_SECTIONS",
                "detail": f"only {sections} sections < {MIN_SECTIONS} minimum",
                "severity": "BLOCKING",
            })

        # Warn checks
        if content_len < WARN_CONTENT_LENGTH:
            step_warnings.append({
                "type": "CONTENT_SHORT",
                "detail": f"content {content_len} chars (< {WARN_CONTENT_LENGTH} recommended)",
                "severity": "WARN",
            })

        if url_count < WARN_URL_COUNT:
            step_warnings.append({
                "type": "FEW_CITATIONS",
                "detail": f"only {url_count} URLs (< {WARN_URL_COUNT} recommended)",
                "severity": "WARN",
            })

        # Determine verdict
        if step_issues:
            verdict = "FAIL"
            issues.extend([{"step": step_name, **i} for i in step_issues])
        elif step_warnings:
            verdict = "WARN"
            warnings.extend([{"step": step_name, **w} for w in step_warnings])
        else:
            verdict = "PASS"

        per_step[step_name] = {
            "verdict": verdict,
            "metrics": metrics,
            "issues": step_issues,
            "warnings": step_warnings,
        }

    # Overall verdict
    fail_count = sum(1 for r in per_step.values() if r["verdict"] == "FAIL")
    warn_count = sum(1 for r in per_step.values() if r["verdict"] == "WARN")
    pass_count = sum(1 for r in per_step.values() if r["verdict"] == "PASS")

    if fail_count > 0:
        overall = "FAIL"
    elif warn_count > 0:
        overall = "WARN"
    else:
        overall = "PASS"

    result = {
        "schema_version": "ic_evidence_gate.v1",
        "task_id": task_id,
        "overall_verdict": overall,
        "summary": {
            "total_steps": len(per_step),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
        },
        "per_step": per_step,
        "issues": issues,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if tasks_dir:
        gate_path = Path(tasks_dir) / f"{task_id}-ic_evidence_gate.json"
        gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return result
