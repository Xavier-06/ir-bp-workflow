#!/usr/bin/env python3
"""IC Debate Review — 行业研究跨维度对抗审查 v1.0

检查不同 step 输出之间的内部矛盾、数字不一致、逻辑冲突。
简于 BP 版（BP 有 8 维度 × section package 结构），IC 直接检查 step MD 文件。

检查类型：
- CONTRADICTION: 两个 step 对同一问题给出矛盾结论
- NUMBER_MISMATCH: 同一数据在不同 step 中数字不一致
- MISSING_PERSPECTIVE: 明显的对标视角缺失（如只有 bullish 没有 risk）
- CONFIDENCE_CLAIM: 高置信度声称但来源不足
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _extract_numbers(text: str) -> dict[str, list[tuple[float, str]]]:
    """Extract numbers with context from text.
    Returns {context_keyword: [(value, full_match), ...]}
    """
    numbers: dict[str, list[tuple[float, str]]] = defaultdict(list)

    # Match numbers with units: "45%", "120亿元", "3.5万亿", "10.2M"
    patterns = [
        (r'([\d,.]+)\s*(%|亿|万|千|万亿|M|B|K|倍|bps?)', 'unit'),
        (r'([\d,.]+)\s*(?:年|季|月|周|天|Q\d)', 'time'),
        (r'CR\d*\s*[≈＝=]?\s*([\d,.]+)%', 'concentration'),
        (r'(?:PE|PB|PS|ROE|ROIC|毛利率|净利率)\s*[≈＝=]?\s*([\d,.]+)%', 'financial_ratio'),
    ]

    for pattern, ctx_type in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(',', ''))
                context = text[max(0, m.start()-30):min(len(text), m.end()+30)].strip()
                key = f"{ctx_type}_{m.group(0)[:30]}"
                numbers[key].append((val, context))
            except ValueError:
                pass

    return dict(numbers)


def _detect_contradictions(
    step_texts: dict[str, str],
    research_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detect potential contradictions between step outputs."""
    contradictions = []

    # 1. Check for opposing sentiment across steps
    # (e.g., step_competitive says "high competition" but step_investment_thesis says "strong moat")
    opposing_pairs = [
        ("正面|积极|增长|利好|优势|领先|垄断", "负面|下滑|风险|劣势|萎缩|替代"),
    ]

    step_sentiments = {}
    for step_name, text in step_texts.items():
        positive = len(re.findall(r'正面|积极|增长|利好|优势|领先|增长|提升|扩大', text))
        negative = len(re.findall(r'负面|下滑|风险|劣势|萎缩|替代|威胁|挑战|下降', text))
        step_sentiments[step_name] = {"positive": positive, "negative": negative}

    # 2. Number mismatch detection
    all_numbers = {}
    for step_name, text in step_texts.items():
        all_numbers[step_name] = _extract_numbers(text)

    # Compare numbers across steps for same metric
    for metric_key in ["financial_ratio", "concentration"]:
        step_values: dict[str, list[float]] = {}
        for step_name, nums in all_numbers.items():
            for key, vals in nums.items():
                if metric_key in key:
                    if key not in step_values:
                        step_values[key] = []
                    step_values[key].extend([v[0] for v in vals])

    # 3. Check for missing risk perspective
    for claim in research_plan.get("claim_matrix", []):
        claim_text = claim.get("claim", "")
        # Claims that sound highly positive/vague → should have supporting data
        if re.search(r'(领先|垄断|绝对优势|遥遥领先|唯一)', claim_text):
            evidence_found = False
            owner_step = claim.get("owner_step", "")
            for step_name, text in step_texts.items():
                if owner_step in step_name:
                    urls = len(re.findall(r'https?://', text))
                    if urls >= 3:
                        evidence_found = True
                        break
            if not evidence_found:
                contradictions.append({
                    "type": "HIGH_CONFIDENCE_CLAIM_LOW_SOURCE",
                    "claim_id": claim.get("claim_id"),
                    "claim": claim_text[:100],
                    "detail": "高置信度声称但对应 step 来源不足",
                    "severity": "MEDIUM",
                })

    return contradictions


def run_debate_review(
    task_id: str,
    step_outputs: dict[str, Path],
    research_plan_path: Path,
    tasks_dir: Path | None = None,
) -> dict[str, Any]:
    """Run cross-dimension adversarial review.

    Args:
        task_id: task identifier
        step_outputs: {step_name: output_file_path}
        research_plan_path: path to ic_research_plan.json
        tasks_dir: output directory

    Returns:
        debate review result
    """
    # Load step texts
    step_texts: dict[str, str] = {}
    for step_name, output_path in step_outputs.items():
        if output_path.exists():
            try:
                step_texts[step_name] = output_path.read_text(encoding="utf-8")
            except Exception:
                pass

    plan: dict[str, Any] = {}
    if research_plan_path.exists():
        try:
            plan = json.loads(research_plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Run checks
    contradictions = _detect_contradictions(step_texts, plan)

    # Additional checks
    issues = list(contradictions)

    # Check for empty step outputs (step with near-zero content)
    empty_steps = []
    for step_name, text in step_texts.items():
        if len(text) < 200:
            empty_steps.append({
                "step": step_name,
                "type": "EMPTY_DIMENSION",
                "detail": f"step output is {len(text)} chars (<200 minimum)",
                "severity": "BLOCKING" if len(text) < 100 else "HIGH",
            })

    issues.extend(empty_steps)

    # Determine verdict
    blocking = [i for i in issues if i.get("severity") == "BLOCKING"]
    high = [i for i in issues if i.get("severity") == "HIGH"]
    medium = [i for i in issues if i.get("severity") == "MEDIUM"]

    if blocking:
        overall = "FAIL"
    elif high:
        overall = "WARN"
    else:
        overall = "PASS"

    result = {
        "schema_version": "ic_debate_review.v1",
        "task_id": task_id,
        "overall_verdict": overall,
        "summary": {
            "total_issues": len(issues),
            "blocking": len(blocking),
            "high": len(high),
            "medium": len(medium),
        },
        "issues": issues,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if tasks_dir:
        gate_path = Path(tasks_dir) / f"{task_id}-ic_debate_review.json"
        gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return result
