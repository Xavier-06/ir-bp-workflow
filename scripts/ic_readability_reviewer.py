#!/usr/bin/env python3
"""IC 管线 — 可读性审查。

从最终报告检查：
1. 最低字数
2. 章节结构完整性
3. 引用/脚注密度
4. 数据来源标注

FAIL → WARN 放行（不阻断，记录到 deferred_fixes）。
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 阈值 ──
_MIN_TOTAL_LENGTH = 2000       # 最低字符数
_MIN_SECTION_COUNT = 4         # 最低章节数
_MIN_CITATION_PER_2K = 2       # 每 2000 字最低引用数
_MIN_DATA_POINT_TYPES = 3      # 最低数据类型数（市场规模/增速/份额/集中度/估值）

# ── 行业术语白名单（不报首字母缩写警告）──
_TECH_ABBR_EXEMPT = frozenset({
    "TAM", "SAM", "SOM", "CAGR", "YoY", "QoQ", "FY", "H1", "H2",
    "CR3", "CR5", "CR10", "PE", "PB", "PS", "EV", "EBITDA", "ROE", "ROA",
    "VC", "IPO", "M&A", "R&D", "B2B", "B2C", "SaaS", "PaaS", "IaaS",
    "USD", "RMB", "CNY", "HKD",
})


def run_readability_review(
    task_id: str,
    report_paths: list[Path],
    tasks_dir: Path,
) -> dict[str, Any]:
    """IC 可读性审查。

    Args:
        task_id: 任务 ID
        report_paths: 报告文件路径列表（可以是 step 输出或最终报告）
        tasks_dir: 输出目录

    Returns:
        {"overall_verdict": str, "checks": [...], "issues": [...]}
    """
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    all_text = ""

    for rp in report_paths:
        if rp.exists() and rp.stat().st_size > 50:
            try:
                all_text += rp.read_text(encoding="utf-8") + "\n"
            except Exception:
                pass

    total_len = len(all_text)

    # ── 检查 1: 最低字数 ──
    if total_len < _MIN_TOTAL_LENGTH:
        issues.append({
            "severity": "WARN",
            "check": "min_length",
            "detail": f"总字数 {total_len} < {_MIN_TOTAL_LENGTH}（最低要求）",
        })
        checks.append({
            "name": "min_length",
            "status": "FAIL",
            "actual": total_len,
            "threshold": _MIN_TOTAL_LENGTH,
        })
    else:
        checks.append({
            "name": "min_length",
            "status": "PASS",
            "actual": total_len,
        })

    # ── 检查 2: 章节结构 ──
    # 计算 markdown 标题数量
    h2_count = len(re.findall(r'^##\s', all_text, re.MULTILINE))
    h3_count = len(re.findall(r'^###\s', all_text, re.MULTILINE))
    total_sections = h2_count + h3_count

    if total_sections < _MIN_SECTION_COUNT:
        issues.append({
            "severity": "WARN",
            "check": "section_structure",
            "detail": (
                f"章节数 {total_sections} (##={h2_count}, ###={h3_count}) "
                f"< {_MIN_SECTION_COUNT}（最低要求）"
            ),
        })
        checks.append({
            "name": "section_structure",
            "status": "FAIL",
            "actual": total_sections,
            "threshold": _MIN_SECTION_COUNT,
        })
    else:
        checks.append({
            "name": "section_structure",
            "status": "PASS",
            "actual": total_sections,
        })

    # ── 检查 3: 引用/脚注密度 ──
    citation_patterns = [
        (r'\[(\d+)\]', "bracket"),           # [1]
        (r'来源[:：]', "source_label"),       # 来源: 
        (r'参考[:：]', "reference_label"),    # 参考:
        (r'根据\S+(?:研报|报告|数据|年报|公告)', "attribution"),  # 根据XX研报
    ]
    total_citations = 0
    for pattern, _ in citation_patterns:
        total_citations += len(re.findall(pattern, all_text))

    expected_citations = max(_MIN_CITATION_PER_2K, int(total_len / 2000 * _MIN_CITATION_PER_2K))

    if total_citations < expected_citations:
        issues.append({
            "severity": "WARN",
            "check": "citation_density",
            "detail": (
                f"引用/来源标注 {total_citations} < {expected_citations} "
                f"（每 2000 字 {_MIN_CITATION_PER_2K} 条）"
            ),
        })
        checks.append({
            "name": "citation_density",
            "status": "FAIL",
            "actual": total_citations,
            "threshold": expected_citations,
        })
    else:
        checks.append({
            "name": "citation_density",
            "status": "PASS",
            "actual": total_citations,
        })

    # ── 检查 4: 数据点多样性 ──
    # 检查是否覆盖了多种数据类型
    data_type_patterns = {
        "market_size": r'市场(?:规模|空间)[：:\s]*[\d,.]+\s*(?:亿|万|万亿)',
        "growth_rate": r'(?:CAGR|增速|增长率|YoY|同比)[：:\s]*[\d,.]+%',
        "market_share": r'(?:市[场占]?[率份]|渗透率|份额)[：:\s]*[\d,.]+%',
        "concentration": r'(?:CR\d+|集中度)[：:\s]*[\d,.]+%',
        "valuation": r'(?:PE|PB|PS|估值|EV/)[：:\s]*[\d,.]+',
    }
    found_types = [
        dtype for dtype, pattern in data_type_patterns.items()
        if re.search(pattern, all_text, re.IGNORECASE)
    ]

    if len(found_types) < _MIN_DATA_POINT_TYPES:
        issues.append({
            "severity": "WARN",
            "check": "data_diversity",
            "detail": (
                f"数据类型覆盖 {len(found_types)}/{len(data_type_patterns)} "
                f"(找到了: {found_types})，缺少定量分析"
            ),
        })
        checks.append({
            "name": "data_diversity",
            "status": "FAIL",
            "actual": len(found_types),
            "found": found_types,
            "threshold": _MIN_DATA_POINT_TYPES,
        })
    else:
        checks.append({
            "name": "data_diversity",
            "status": "PASS",
            "actual": len(found_types),
        })

    # ── 检查 5: 未定义缩写 ──
    abbr_pattern = re.compile(r'\b([A-Z][A-Z0-9]{2,})\b')
    abbrs_in_text = Counter(m.group(1) for m in abbr_pattern.finditer(all_text))

    undefined_abbrs: list[str] = []
    for abbr, count in abbrs_in_text.most_common(20):
        if abbr in _TECH_ABBR_EXEMPT:
            continue
        if count < 2:
            continue
        # 检查是否在文本中定义过（"XXX (Abbreviation)" 或 "Abbreviation（XXX）"）
        defined = bool(
            re.search(rf'{re.escape(abbr)}\s*[\(（].*?[\)）]', all_text) or
            re.search(rf'[\(（]\s*{re.escape(abbr)}\s*[\)）]', all_text)
        )
        if not defined:
            undefined_abbrs.append(abbr)

    if undefined_abbrs:
        issues.append({
            "severity": "WARN",
            "check": "undefined_abbreviations",
            "detail": f"未定义缩写: {', '.join(undefined_abbrs[:8])}",
        })
        checks.append({
            "name": "undefined_abbreviations",
            "status": "WARN",
            "undefined": undefined_abbrs[:8],
        })
    else:
        checks.append({
            "name": "undefined_abbreviations",
            "status": "PASS",
        })

    # ── 判定 ──
    fail_count = sum(1 for c in checks if c["status"] in ("FAIL",))
    if fail_count >= 2:
        overall = "FAIL"
    elif fail_count >= 1:
        overall = "WARN"
    else:
        overall = "PASS"

    result = {
        "schema_version": "ic_readability_review.v1",
        "task_id": task_id,
        "overall_verdict": overall,
        "total_length": total_len,
        "checks": checks,
        "issues": issues,
        "deferred_fixes": [
            {"issue": i.get("detail", ""), "check": i.get("check", "")}
            for i in issues
        ] if overall != "PASS" else [],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    gate_path = tasks_dir / f"{task_id}-ic_readability_review.json"
    gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["output_path"] = str(gate_path)

    return result
