#!/usr/bin/env python3
"""IC 管线 — 行业投资判断汇总。

读取所有 step 输出，提取各维度结论、置信度、关键风险和数据缺口，
综合给出行业投资建议（超配/标配/低配），并输出结构化 JSON + Markdown。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── IC Step → 中文标签 ──
IC_STEP_LABELS: dict[str, str] = {
    "step_ind_overview": "行业概览",
    "step_policy_scan": "政策法规",
    "step_value_chain": "产业链分析",
    "step_cross_chain_compare": "跨环节对比",
    "step_catalyst_analysis": "催化剂分析",
    "step_consensus_challenge": "共识挑战",
    "step_investment_thesis": "投资机会",
    "step_risk_assessment": "风险评估",
    "step_scenario_sensitivity": "场景敏感性",
}

# 参与判断的核心 step
_JUDGE_STEPS = (
    "step_ind_overview", "step_policy_scan", "step_value_chain",
    "step_cross_chain_compare", "step_catalyst_analysis",
    "step_consensus_challenge", "step_investment_thesis",
    "step_risk_assessment", "step_scenario_sensitivity",
)


def _extract_conclusion(text: str, max_len: int = 300) -> dict[str, str]:
    """从 step 报告中提取结论段。"""
    conclusion_patterns = [
        r'(?:##?\s*结论[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*综合判断[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*投资含义[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*conclusion[^#\n]*\n)(.*?)(?=\n##?|\Z)',
    ]
    for pattern in conclusion_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            verdict = match.group(1).strip()[:max_len]
            confidence = _detect_confidence(verdict)
            return {"verdict_text": verdict, "confidence": confidence}

    # Fallback: 取文末 400 字
    if len(text) > 400:
        verdict = text[-400:].strip()
    else:
        verdict = text
    confidence = _detect_confidence(verdict)
    return {"verdict_text": verdict[:max_len], "confidence": confidence}


def _detect_confidence(text: str) -> str:
    """从文本推断置信度。"""
    high_signals = ("多源验证", "财报数据", "官方统计", "工商信息", "行业协会", "交叉验证")
    low_signals = ("推断", "假设", "缺乏数据", "未披露", "待验证", "不确定性高")

    high_count = sum(1 for s in high_signals if s in text)
    low_count = sum(1 for s in low_signals if s in text)

    if high_count >= 2:
        return "高"
    elif low_count >= 2:
        return "低"
    elif high_count >= 1:
        return "中"
    else:
        return "中"


def _extract_risk_flags(text: str, max_flags: int = 3) -> list[str]:
    """提取风险标记。"""
    patterns = [
        r'(?:⚠|❌|风险|利空|不利|下行|挑战|威胁)[^。\n]{10,100}[。\n]',
    ]
    flags: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            flag = match.group(0).strip()[:120]
            if flag and flag not in flags:
                flags.append(flag)
                if len(flags) >= max_flags:
                    return flags
    return flags[:max_flags]


def _extract_data_points(text: str, max_points: int = 4) -> list[str]:
    """提取关键数据点。"""
    pattern = r'[\d,.]+\s*(?:亿|万|%|倍|家|元|美元|RMB)'
    return list(dict.fromkeys(
        m.group(0) for m in re.finditer(pattern, text)
    ))[:max_points]


def _derive_recommendation(dimensions: list[dict[str, Any]]) -> tuple[str, str]:
    """综合给出行业投资建议。

    评级:
      - 低配 (UNDERWEIGHT): 多数维度负面 + 存在行业级风险
      - 标配 (EQUALWEIGHT): 中性/分歧
      - 超配 (OVERWEIGHT): 多数维度正面 + 有催化剂
    """
    positive_signals = ("高增长", "政策支持", "技术突破", "国产替代", "需求爆发",
                        "渗透率低", "集中度提升", "估值合理", "景气上行")
    negative_signals = ("产能过剩", "需求萎缩", "政策收紧", "技术瓶颈",
                        "竞争恶化", "估值过高", "周期性下行", "替代风险")

    pos_count = 0
    neg_count = 0
    low_conf = 0
    high_conf = 0
    risk_total = 0

    for d in dimensions:
        verdict = d.get("verdict_text", "")
        pos_count += sum(1 for s in positive_signals if s in verdict)
        neg_count += sum(1 for s in negative_signals if s in verdict)
        risk_total += len(d.get("risk_flags", []))
        if d.get("confidence") == "低":
            low_conf += 1
        elif d.get("confidence") == "高":
            high_conf += 1

    dealbreaker = any(
        kw in str(d.get("verdict_text", ""))
        for d in dimensions
        for kw in ("衰退", "崩盘", "政策封杀", "颠覆性替代", "不可逆下行")
    )

    if dealbreaker:
        return "低配 (UNDERWEIGHT)", (
            "检测到行业级阻断风险（衰退/政策封杀/颠覆性替代），"
            "建议回避或大幅减配。"
        )
    elif neg_count >= pos_count + 3 and low_conf >= 3:
        return "低配 (UNDERWEIGHT)", (
            f"负面信号 ({neg_count}) 明显多于正面 ({pos_count})，"
            f"且有 {low_conf} 个维度置信度低，建议减配。"
        )
    elif pos_count >= neg_count + 3:
        return "超配 (OVERWEIGHT)", (
            f"正面信号 ({pos_count}) 明显多于负面 ({neg_count})，"
            f"行业景气上行，建议超配。"
        )
    elif pos_count >= neg_count:
        return "标配/谨慎乐观 (EQUALWEIGHT)", (
            f"正面 ({pos_count}) vs 负面 ({neg_count})，"
            "整体偏正面但需关注风险点。"
        )
    else:
        return "标配 (EQUALWEIGHT)", (
            f"信号分歧较大（正面 {pos_count} vs 负面 {neg_count}），"
            "建议标配观察。"
        )


def build_ic_investment_judgment(
    job_id: str,
    tasks_dir: Path | None = None,
) -> dict[str, Any]:
    """构建 IC 行业投资判断汇总。

    Args:
        job_id: 任务 ID
        tasks_dir: 数据目录

    Returns:
        {"recommendation": str, "rationale": str, "dimensions": [...]}
    """
    tasks = Path(tasks_dir) if tasks_dir else (
        Path(__file__).resolve().parent.parent / "data" / "tasks"
    )
    job_id = str(job_id)

    dimensions: list[dict[str, Any]] = []

    for step in _JUDGE_STEPS:
        # IC step 输出可以是多种命名格式
        candidates = [
            tasks / f"{job_id}-{step}.md",
            tasks / f"{job_id}_{step}.md",
        ]
        found = None
        for c in candidates:
            if c.exists():
                found = c
                break
        if found is None:
            continue

        try:
            text = found.read_text(encoding="utf-8")
        except Exception:
            continue

        if len(text) < 80:
            continue

        conclusion = _extract_conclusion(text)
        risk_flags = _extract_risk_flags(text)
        data_points = _extract_data_points(text)

        dimensions.append({
            "step": step,
            "label": IC_STEP_LABELS.get(step, step),
            "verdict_text": conclusion["verdict_text"],
            "confidence": conclusion["confidence"],
            "key_data": data_points,
            "risk_flags": risk_flags,
        })

    recommendation, rationale = _derive_recommendation(dimensions)

    result = {
        "schema_version": "ic_investment_judgment.v1",
        "recommendation": recommendation,
        "rationale": rationale,
        "dimensions": dimensions,
        "dimension_count": len(dimensions),
        "low_confidence_count": sum(
            1 for d in dimensions if d["confidence"] == "低"
        ),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # ── 写 JSON ──
    json_path = tasks / f"{job_id}-ic_investment_judgment.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # ── 写 Markdown ──
    md_lines = [
        "# IC 行业投资判断",
        "",
        f"**投资建议：{recommendation}**",
        "",
        f"> {rationale}",
        "",
        f"- 覆盖维度数：{len(dimensions)}",
        f"- 低置信度维度：{result['low_confidence_count']}",
        "",
        "## 各维度结论",
        "",
        "| 维度 | 一句话结论 | 置信度 | 关键数据点 |",
        "|------|-----------|--------|-----------|",
    ]
    for d in dimensions:
        verdict = d["verdict_text"][:80].replace("\n", " ").replace("|", "/")
        data_str = ", ".join(d["key_data"]) if d["key_data"] else "-"
        md_lines.append(
            f"| {d['label']} | {verdict} | {d['confidence']} | {data_str} |"
        )

    md_lines += ["", "## 关键风险标记", ""]
    all_risks: list[str] = []
    for d in dimensions:
        for flag in d["risk_flags"]:
            all_risks.append(f"- **[{d['label']}]** {flag.strip()}")
    md_lines.extend(all_risks[:10] if all_risks else ["- 无重大风险标记"])

    md_path = tasks / f"{job_id}-ic_investment_judgment.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    result["json_path"] = str(json_path)
    result["md_path"] = str(md_path)

    return result
