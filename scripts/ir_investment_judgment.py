#!/usr/bin/env python3
"""IR 管线 — 投资判断汇总（phase14_investment_judgment）。

读取所有 step 输出（step1_data ... step8_risk）与最终报告，提取每个维度/步骤的
结论、置信度、关键风险标记与数据缺口，综合给出明确的投资建议
（买入/增持 · 观望 · 回避）及逻辑与风险提示。

设计对标 BP 的 bp_investment_judgment.py，但适配 IR 的 10 个 step 结构与
行业/公司研究语境（推荐评级使用券商口径：回避/观望/买入）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


# ── IR step → 中文标签（与 ir_subagent_launcher_wb.STEP_ROLE 对齐）──
IR_STEP_LABELS = {
    "step1_data": "数据收集",
    "step1_industry": "行业分析",
    "step2_biz": "商业模式",
    "step3_finance": "财务分析",
    "step4_mgmt": "管理层与治理",
    "step7_insight": "差异化洞察",
    "step6_valuation": "预测与估值",
    "step8_risk": "风险与催化",
}

# 参与判断的核心 step（统稿由 phase13 synthesis 独立处理，不参与维度评分）
# v3.6: step5_macro 已删除
_JUDGE_STEPS = (
    "step1_data", "step1_industry", "step2_biz", "step3_finance",
    "step4_mgmt", "step7_insight", "step6_valuation", "step8_risk",
)


def _slug_to_label(step: str) -> str:
    return IR_STEP_LABELS.get(step, step.replace("_", " ").title())


def _extract_verdict_block(text: str) -> dict[str, str]:
    """从 step 报告中提取结论段。

    优先匹配 结论 / 综合判断 / 投资逻辑 / conclusion 等小节；
    找不到则回退到文末 500 字（结论通常在末尾）。
    """
    conclusion_patterns = [
        r'(?:##?\s*(?:本步)?结论[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*综合判断[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*投资逻辑[^#\n]*\n)(.*?)(?=\n##?|\Z)',
        r'(?:##?\s*conclusion[^#\n]*\n)(.*?)(?=\n##?|\Z)',
    ]
    verdict_text = ""
    for pattern in conclusion_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            verdict_text = match.group(1).strip()
            break

    if not verdict_text and len(text) > 500:
        verdict_text = text[-500:].strip()

    # 置信度
    confidence = "未知"
    confidence_match = re.search(r'置信度[：:]\s*(高|中|低)', verdict_text)
    if confidence_match:
        confidence = confidence_match.group(1)
    else:
        if re.search(r'(?:已验证|多源|交叉验证|官方|工商|财报)', verdict_text):
            confidence = "中"
        elif re.search(r'(?:未验证|仅BP|推断|假设|缺乏|未知)', verdict_text):
            confidence = "低"

    # 关键数据点
    data_points = re.findall(
        r'[\d,.]+\s*(?:亿|万|%|倍|颗|片|台|个|家|轮|元|美元|USD|RMB|港元|HKD)',
        verdict_text,
    )[:5]

    return {
        "verdict_text": verdict_text[:300],
        "confidence": confidence,
        "key_data": data_points,
    }


def _extract_risk_flags(text: str, max_flags: int = 3) -> list[str]:
    risk_patterns = [
        r'(?:⚠|❌|⛔|风险|red\s*flag|deal\s*breaker|催化| downside)[^。\n]*[。\n]',
        r'(?:FAIL|不成立|不具备|严重|致命|阻断|利空)[^。\n]*[。\n]',
    ]
    flags: list[str] = []
    for pattern in risk_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            flag = match.group(0).strip()[:120]
            if flag and flag not in flags:
                flags.append(flag)
                if len(flags) >= max_flags:
                    return flags
    return flags[:max_flags]


def _extract_data_gaps(text: str, max_gaps: int = 3) -> list[str]:
    gap_patterns = [
        r'(?:data[_ ]?gap|数据缺口|待验证|待补充|需要补充|缺失|未披露|missing data)[^。\n]*[。\n]',
    ]
    gaps: list[str] = []
    for pattern in gap_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            gap = match.group(0).strip()[:120]
            if gap and gap not in gaps:
                gaps.append(gap)
                if len(gaps) >= max_gaps:
                    return gaps
    return gaps[:max_gaps]


def _derive_recommendation(dimensions: list[dict[str, Any]],
                           total_data_gaps: int) -> tuple[str, str]:
    """综合给出投资建议与逻辑。

    评级口径（券商风格）：
      - 回避 (AVOID)：存在 Deal Breaker 级风险
      - 观望 (HOLD)：多数维度置信度低 或 存在显著不确定性
      - 买入/增持 (BUY)：核心维度已验证、无重大阻断风险
    """
    _DB_KEYWORDS = (
        "失信", "诉讼", "造假", "违法", "欺诈", "竞业限制", "知识产权纠纷",
        "deal breaker", "阻断", "致命", "不可缓释", "财务造假", "退市",
    )
    dealbreaker_count = 0
    for d in dimensions:
        for flag in d.get("risk_flags", []):
            if any(kw in flag.lower() for kw in _DB_KEYWORDS):
                dealbreaker_count += 1

    high_risk_count = sum(1 for d in dimensions if d["confidence"] == "低")

    if dealbreaker_count > 0:
        rec = "回避 (AVOID)"
        rationale = (
            f"检测到 {dealbreaker_count} 处 Deal Breaker 级别风险标记，"
            "存在不可缓释的硬伤，不建议在当前阶段投资。"
        )
    elif high_risk_count >= 3:
        rec = "观望 (HOLD)"
        rationale = (
            f"有 {high_risk_count} 个维度置信度为低，核心证据不足，"
            "建议等待更多公开数据或基本面变化后再决策。"
        )
    elif high_risk_count >= 1:
        rec = "观望/审慎 (HOLD)"
        rationale = (
            f"有 {high_risk_count} 个维度存在不确定性，"
            "建议进一步验证关键假设后审慎参与。"
        )
    else:
        rec = "买入/增持 (BUY)"
        rationale = (
            "核心维度（数据、行业、财务、管理层）已初步验证，"
            "未见重大阻断风险，具备投资价值，可择机布局。"
        )

    if total_data_gaps >= 5:
        rationale += f" 需注意：全维度共 {total_data_gaps} 处数据缺口，结论存在相应不确定性。"
    return rec, rationale


def build_ir_investment_judgment(job_id: str, tasks_dir: Path | None = None) -> dict[str, Any]:
    """从所有 step 输出构建 IR 投资判断汇总。"""
    tasks_dir = Path(tasks_dir) if tasks_dir else (Path(__file__).resolve().parent.parent / "data" / "tasks")
    job_id = str(job_id)

    dimensions: list[dict[str, Any]] = []

    for step in _JUDGE_STEPS:
        md_path = tasks_dir / f"{job_id}-{step}.md"
        if not md_path.exists():
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if len(text) < 80:
            continue

        verdict = _extract_verdict_block(text)
        risk_flags = _extract_risk_flags(text)
        data_gaps = _extract_data_gaps(text)

        dimensions.append({
            "step": step,
            "label": _slug_to_label(step),
            "verdict_text": verdict["verdict_text"],
            "confidence": verdict["confidence"],
            "key_data": verdict["key_data"],
            "risk_flags": risk_flags,
            "data_gaps": data_gaps,
        })

    # 最终报告执行摘要
    final_path = tasks_dir / f"{job_id}-final_report.md"
    exec_summary = ""
    if final_path.exists():
        try:
            final_text = final_path.read_text(encoding="utf-8")
            exec_match = re.search(
                r'(?:##?\s*执行摘要[^#\n]*\n|##?\s*摘要[^#\n]*\n)(.*?)(?=\n##?|\Z)',
                final_text, re.DOTALL | re.IGNORECASE,
            )
            if exec_match:
                exec_summary = exec_match.group(1).strip()[:600]
        except Exception:
            pass

    total_data_gaps = sum(len(d["data_gaps"]) for d in dimensions)
    recommendation, rationale = _derive_recommendation(dimensions, total_data_gaps)

    result = {
        "schema_version": "ir_investment_judgment.v1",
        "recommendation": recommendation,
        "rationale": rationale,
        "dimensions": dimensions,
        "executive_summary": exec_summary,
        "low_confidence_dimension_count": sum(1 for d in dimensions if d["confidence"] == "低"),
        "total_data_gaps": total_data_gaps,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 写 JSON
    json_path = tasks_dir / f"{job_id}-ir_investment_judgment.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 写 Markdown 汇总
    md_lines = [
        "# IR 投资判断汇总",
        "",
        f"**投资建议：{recommendation}**",
        "",
        f"> {rationale}",
        "",
        f"- 低置信度维度数：{result['low_confidence_dimension_count']}",
        f"- 数据缺口总数：{total_data_gaps}",
        "",
        "## 各维度结论一览",
        "",
        "| 维度 | 一句话结论 | 置信度 | 关键数据点 |",
        "|------|-----------|--------|-----------|",
    ]
    for d in dimensions:
        verdict_short = d["verdict_text"][:80].replace("\n", " ")
        data_str = ", ".join(d["key_data"][:3]) if d["key_data"] else "-"
        md_lines.append(f"| {d['label']} | {verdict_short} | {d['confidence']} | {data_str} |")

    md_lines += ["", "## 关键风险标记", ""]
    all_risks: list[str] = []
    for d in dimensions:
        for flag in d["risk_flags"]:
            all_risks.append(f"- **[{d['label']}]** {flag.strip()}")
    md_lines.extend(all_risks[:12] if all_risks else ["- 无重大风险标记"])

    md_lines += ["", "## 关键数据缺口", ""]
    all_gaps: list[str] = []
    for d in dimensions:
        for gap in d["data_gaps"]:
            all_gaps.append(f"- **[{d['label']}]** {gap.strip()}")
    md_lines.extend(all_gaps[:12] if all_gaps else ["- 无重大数据缺口"])

    if exec_summary:
        md_lines += ["", "## 报告执行摘要", "", exec_summary]

    md_path = tasks_dir / f"{job_id}-ir_investment_judgment.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    result["md_path"] = str(md_path)
    result["json_path"] = str(json_path)

    # 可选：生成 DOCX（复用 per-step docx 渲染器；docx 不可用时静默跳过）
    try:
        from scripts.ir_step_docx import build_ir_step_docx
        docx_out = tasks_dir / f"{job_id}-ir_investment_judgment.docx"
        built = build_ir_step_docx(md_path, docx_out, title="IR 投资判断汇总")
        if built:
            result["docx_path"] = built
    except Exception:
        pass

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        _tasks = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        _result = build_ir_investment_judgment(sys.argv[1], tasks_dir=_tasks)
        print(json.dumps(_result, ensure_ascii=False, indent=2))
