#!/usr/bin/env python3
"""IC 管线 — 跨维度一致性门禁。

检查不同 step 之间的关键指标一致性和逻辑矛盾：
- 市场规模数据跨 step 一致性
- 行业增速/渗透率跨 step 一致性  
- CRn 集中度跨 step 一致性
- 技术路线描述跨 step 一致性

FAIL → WARN 放行（不阻断管线，记录到 deferred_fixes）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _numeric_value(value: str) -> float | None:
    """提取数值，支持中文单位（万/亿/万亿）。"""
    text = str(value or "").replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    if "万亿" in text:
        number *= 1_000_000_000_000
    elif "亿" in text:
        number *= 100_000_000
    elif "万" in text:
        number *= 10_000
    return number


def _extract_numerics_from_text(text: str, keyword: str) -> list[dict[str, Any]]:
    """从文本中提取与关键词相关的数值断言。

    返回 [{"value": float, "context": str, "raw": str}]
    """
    results: list[dict[str, Any]] = []
    # 匹配 pattern: "{keyword}[:：]? ... {数字}{单位}"
    # 弹性匹配——关键词出现在 50 字内且有数字
    for kw in [keyword] + [keyword.replace(" ", "")]:
        for m in re.finditer(
            rf'({re.escape(kw)})[^\n]*?([\d,.]+\s*(?:万|亿|万亿)?(?:元|美元|RMB|%)?)',
            text, re.IGNORECASE,
        ):
            raw = m.group(2).strip()
            val = _numeric_value(raw)
            context = text[max(0, m.start() - 20):m.end() + 50].replace("\n", " ")
            results.append({"value": val, "context": context, "raw": raw})
    return results


def _check_consistency(sources: list[dict[str, Any]], max_deviation_pct: float = 0.5) -> list[dict[str, Any]]:
    """检查多个数据源的数值一致性。

    max_deviation_pct: 允许的最大偏差百分比（0.5 = 50%）

    返回 issues 列表。
    """
    issues: list[dict[str, Any]] = []
    vals_with_src = [(s["value"], s["source"], s["raw"]) for s in sources if s["value"] is not None]

    if len(vals_with_src) < 2:
        return issues

    # 检查 50% 偏差
    for i, (v1, src1, raw1) in enumerate(vals_with_src):
        for v2, src2, raw2 in vals_with_src[i + 1:]:
            if v1 == 0 and v2 == 0:
                continue
            if v1 == 0 or v2 == 0:
                issues.append({
                    "severity": "WARN",
                    "check": "zero_value_mismatch",
                    "detail": f"{raw1} ({src1}) vs {raw2} ({src2}) — 一个为零",
                })
                continue
            deviation = abs(v2 - v1) / max(abs(v1), abs(v2))
            if deviation > max_deviation_pct:
                issues.append({
                    "severity": "WARN" if deviation < 0.8 else "MEDIUM",
                    "check": "numeric_consistency",
                    "detail": (
                        f"{raw1} ({src1}) vs {raw2} ({src2}) "
                        f"— 偏差 {deviation:.0%}"
                    ),
                })
    return issues


def run_cross_dimension_gate(
    task_id: str,
    step_outputs: dict[str, Path],
    tasks_dir: Path,
) -> dict[str, Any]:
    """IC 跨维度一致性检查。

    Args:
        task_id: 任务 ID
        step_outputs: {step_name: output_file_path} 字典
        tasks_dir: 任务数据目录

    Returns:
        {"overall_verdict": str, "checks": [...], "issues": [...]}
    """
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    dest: Path = tasks_dir

    # ── 检查 1: 市场规模一致性 ──
    market_data: list[dict[str, Any]] = []
    for step_name, path in step_outputs.items():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        source_label = step_name.replace("step_", "").replace("_", " ")

        for kw in ["市场规模", "market size", "TAM"]:
            numerics = _extract_numerics_from_text(text, kw)
            for n in numerics:
                n["source"] = source_label
            market_data.extend(numerics)

    if market_data:
        market_issues = _check_consistency(market_data, max_deviation_pct=0.5)
        checks.append({
            "name": "market_size_consistency",
            "status": "FAIL" if market_issues else "PASS",
            "sources_count": len(market_data),
            "issues": market_issues,
        })
        issues.extend([
            {"check": "market_size_consistency", **i}
            for i in market_issues
        ])

    # ── 检查 2: 行业增速/渗透率一致性 ──
    growth_data: list[dict[str, Any]] = []
    for step_name, path in step_outputs.items():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        source_label = step_name.replace("step_", "").replace("_", " ")

        for kw in ["CAGR", "增速", "增长率", "渗透率", "growth rate"]:
            numerics = _extract_numerics_from_text(text, kw)
            for n in numerics:
                n["source"] = source_label
            growth_data.extend(numerics)

    if growth_data:
        growth_issues = _check_consistency(growth_data, max_deviation_pct=0.5)
        checks.append({
            "name": "growth_rate_consistency",
            "status": "FAIL" if growth_issues else "PASS",
            "sources_count": len(growth_data),
            "issues": growth_issues,
        })
        issues.extend([
            {"check": "growth_rate_consistency", **i}
            for i in growth_issues
        ])

    # ── 检查 3: CRn 集中度一致性 ──
    cr_data: list[dict[str, Any]] = []
    for step_name, path in step_outputs.items():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        source_label = step_name.replace("step_", "").replace("_", " ")

        for kw in ["CR", "集中度", "CR3", "CR5", "CR10", "concentration"]:
            numerics = _extract_numerics_from_text(text, kw)
            for n in numerics:
                n["source"] = source_label
            cr_data.extend(numerics)

    if cr_data:
        cr_issues = _check_consistency(cr_data, max_deviation_pct=0.3)
        checks.append({
            "name": "concentration_consistency",
            "status": "FAIL" if cr_issues else "PASS",
            "sources_count": len(cr_data),
            "issues": cr_issues,
        })
        issues.extend([
            {"check": "concentration_consistency", **i}
            for i in cr_issues
        ])

    # ── 检查 4: 关键公司名单一致性 ──
    company_sets: list[tuple[str, set[str]]] = []
    company_pattern = re.compile(r'(?:关键公司|龙头企业|头部企业|主要参与方)[：:\s]*(.*?)(?=\n\n|\n#|$)')
    for step_name, path in step_outputs.items():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        source_label = step_name.replace("step_", "").replace("_", " ")

        for m in company_pattern.finditer(text):
            companies_text = m.group(1)
            # 简单提取公司名（逗号/顿号分隔）
            companies = set(
                c.strip()
                for c in re.split(r'[,，、;；\n]', companies_text)
                if len(c.strip()) > 1
            )
            if companies:
                company_sets.append((source_label, companies))

    if len(company_sets) >= 2:
        all_companies = set()
        for _, cs in company_sets:
            all_companies.update(cs)

        for label, cs in company_sets:
            overlap = cs & all_companies
            if len(overlap) < len(cs) * 0.5 and len(cs) > 2:
                issues.append({
                    "severity": "WARN",
                    "check": "company_list_consistency",
                    "detail": (
                        f"[{label}] 名单 {cs} 与其他 step 重叠度低 "
                        f"({len(overlap)}/{len(cs)})"
                    ),
                })
        checks.append({
            "name": "company_list_consistency",
            "status": "PASS",
            "sources_count": len(company_sets),
        })

    # ── 判定 overall ──
    medium_issues = [i for i in issues if i.get("severity") == "MEDIUM"]
    warn_issues = [i for i in issues if i.get("severity") == "WARN"]

    # IC 跨维度门禁：MEDIUM → FAIL（记录不阻断），WARN → 记录
    if medium_issues:
        overall = "WARN"
    elif warn_issues:
        overall = "WARN"
    elif not checks:
        overall = "PASS"
    else:
        any_fail = any(c.get("status") == "FAIL" for c in checks)
        overall = "WARN" if any_fail else "PASS"

    result = {
        "schema_version": "ic_cross_dimension_gate.v1",
        "task_id": task_id,
        "overall_verdict": overall,
        "checks": checks,
        "issues": issues,
        "deferred_fixes": [
            {
                "issue": i.get("detail", ""),
                "check": i.get("check", ""),
                "severity": i.get("severity", "WARN"),
            }
            for i in issues
        ] if overall != "PASS" else [],
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 写入文件
    gate_path = dest / f"{task_id}-ic_cross_dimension_gate.json"
    gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["output_path"] = str(gate_path)

    return result
