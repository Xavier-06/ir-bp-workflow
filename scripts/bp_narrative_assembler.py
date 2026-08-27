#!/usr/bin/env python3
"""Deterministic BP narrative assembly.

Transforms validated BP section packages into an investment-decision-chain report.
This intentionally avoids concatenating dimension markdown drafts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import re

from scripts.bp_utils import dimension_label


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _valid_packages(section_index: dict[str, Any]) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for item in section_index.get("packages", []) or []:
        if not isinstance(item, dict):
            continue
        if not (item.get("validation") or {}).get("passed"):
            continue
        package = item.get("package") if isinstance(item.get("package"), dict) else {}
        if package:
            packages.append(package)
    return packages


def _collect_unique(packages: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for package in packages:
        for item in package.get(field, []) or []:
            text = str(item).strip()
            if text and text not in values:
                values.append(text)
    return values


def _collect_facts(packages: list[dict[str, Any]]) -> list[str]:
    fact_ids: list[str] = []
    for package in packages:
        for fact_id in package.get("facts_used", []) or []:
            if fact_id not in fact_ids:
                fact_ids.append(str(fact_id))
        for block in package.get("narrative_blocks", []) or []:
            if isinstance(block, dict):
                for fact_id in block.get("fact_ids", []) or []:
                    if fact_id not in fact_ids:
                        fact_ids.append(str(fact_id))
    return fact_ids


def _collect_claims(packages: list[dict[str, Any]]) -> list[str]:
    claim_ids: list[str] = []
    for package in packages:
        for claim_id in package.get("claim_ids_covered", []) or []:
            claim_id_text = str(claim_id)
            if claim_id_text not in claim_ids:
                claim_ids.append(claim_id_text)
        for claim in package.get("claims", []) or []:
            if isinstance(claim, dict) and claim.get("claim_id"):
                claim_id_text = str(claim.get("claim_id"))
                if claim_id_text not in claim_ids:
                    claim_ids.append(claim_id_text)
    return claim_ids


def _fact_index(fact_store: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for fact in fact_store.get("facts", []) or []:
        if isinstance(fact, dict) and fact.get("fact_id"):
            index[str(fact["fact_id"])] = fact
    return index


def _fact_text(fact_id: str, facts: dict[str, dict[str, Any]]) -> str:
    fact = facts.get(str(fact_id), {})
    if not fact:
        return "事实详情未进入 Fact Store"
    claim = _clean_main_text(fact.get("claim") or fact.get("fact") or fact.get("value") or "未命名事实")
    source_url = str(fact.get("source_url") or "").strip()
    source_name = str(fact.get("source") or "").strip()
    tier = str(fact.get("source_tier") or fact.get("source_quality") or "unknown").strip()
    confidence = str(fact.get("confidence") or "unknown").strip()
    # 来源带 Markdown 链接：有 URL 就生成 [来源名](URL)，否则用来源名或兜底
    if source_url and source_url not in ("无外部来源URL", "无外部来源"):
        display_name = source_name if source_name else source_url
        source_ref = f"[{display_name}]({source_url})"
    elif source_name:
        source_ref = source_name
    else:
        source_ref = "无外部来源"
    return f"{claim}（来源等级: {tier}；置信度: {confidence}；来源: {source_ref}）"


def _coverage_gap_lines(coverage: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for claim in coverage.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        status = str(claim.get("status", "")).lower()
        if status in {"unverified", "partially_supported", "contradicted", "not_addressed"}:
            claim_text = str(claim.get("claim", "")).strip() or "未命名声称"
            priority = str(claim.get("priority", "")).strip() or "普通"
            gaps = claim.get("data_gaps", []) or ["需补充外部证据后才能进入主结论"]
            lines.append(f"- {claim_text}（{priority}，{_status_label(status)}）：下一步补证：{_short_list(gaps, limit=3)}")
    return lines


def _short_list(values: list[Any], limit: int = 4, empty: str = "暂无") -> str:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        return empty
    shown = cleaned[:limit]
    suffix = f"；另有{len(cleaned) - limit}项" if len(cleaned) > limit else ""
    return "；".join(shown) + suffix


def _status_label(status: str) -> str:
    return {
        "supported": "已由外部证据支持",
        "partially_supported": "部分支持",
        "unverified": "未验证",
        "contradicted": "存在反证",
        "not_addressed": "未覆盖",
        "planned": "待验证",
    }.get(str(status).lower(), str(status) or "未知")


def _source_summary(count: int) -> str:
    if count <= 0:
        return "暂无可用于主结论的外部事实"
    return f"已引用 {count} 条结构化事实，具体来源保留在内部审计材料中"


def _claim_summary(coverage: dict[str, Any]) -> str:
    summary = coverage.get("summary", {}) or {}
    return (
        f"共 {summary.get('total', 0)} 条核心声称；"
        f"已支持 {summary.get('supported', 0)} 条，"
        f"未验证 {summary.get('unverified', 0)} 条，"
        f"存在反证 {summary.get('contradicted', 0)} 条，"
        f"关键未解决 {summary.get('critical_not_addressed', 0)} 条"
    )


def _quality_review_summary(debate: dict[str, Any]) -> str:
    verdict = str(debate.get("verdict") or "UNKNOWN")
    if verdict == "PASS":
        return "结构化对抗评审通过"
    if verdict == "WARN":
        return "结构化对抗评审提示需关注事项"
    return "结构化对抗评审未通过，需重写后交付"


def _recommendation_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {
        "go": "建议推进下一步投资流程",
        "proceed": "建议推进下一步投资流程",
        "conditional_go": "有条件推进，先补齐关键尽调材料",
        "observe": "继续观察，暂不进入投资决策",
        "hold": "暂缓推进",
        "no_go": "不建议推进",
        "undecided": "暂无法形成明确建议",
    }.get(text, str(value or "暂无法形成明确建议"))


def _confidence_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {
        "high": "较高",
        "medium": "中等",
        "low": "较低",
        "unknown": "未知",
    }.get(text, str(value or "未知"))


_MACHINE_ID_RE = re.compile(r"\b(?:BP-[A-Z0-9_]+-F\d{3,}|BF-\d{3,}|F\d{3,}|BC\d{3,})\b|\b(?:fact_ids?|claim_ids?)\s*[:=]\s*[^。；，,）)]+", re.I)


def _clean_main_text(text: str) -> str:
    cleaned = _MACHINE_ID_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([。；，,）)])", r"\1", cleaned)
    cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
    cleaned = cleaned.replace("见 /", "见").replace("来源 /", "来源")
    return cleaned.strip(" ；，,/")


def _answer_line(answer: dict[str, Any]) -> str:
    text = _clean_main_text(answer.get("answer", ""))
    confidence = str(answer.get("confidence", "")).strip()
    limits = _clean_main_text(answer.get("limits", ""))
    suffix_parts = []
    if confidence:
        suffix_parts.append(f"置信度：{confidence}")
    if limits:
        suffix_parts.append(f"限制：{limits}")
    suffix = f"（{'；'.join(suffix_parts)}）" if suffix_parts else ""
    return f"- {text}{suffix}" if text else ""


def _block_line(block: dict[str, Any]) -> str:
    text = _clean_main_text(block.get("text", ""))
    return f"- {text}" if text else ""


def _narrative_blocks(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for package in packages:
        for block in package.get("narrative_blocks", []) or []:
            if isinstance(block, dict):
                blocks.append(block)
    return blocks


def _answers(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answers: list[dict[str, Any]] = []
    for package in packages:
        for answer in package.get("answers", []) or []:
            if isinstance(answer, dict):
                answers.append(answer)
    return answers


def _module_name(package: dict[str, Any]) -> str:
    title = str(package.get("section_title") or "").strip()
    if title:
        return re.sub(r"^bp[_-]", "", title)
    # 无中文标题时，用 section_id 解析出中文角色名（数据驱动，禁止裸英文 slug 进成品）
    slug = str(package.get("section_id") or "").strip()
    if slug:
        return dimension_label(slug)
    return "综合尽调"


def _compact_text(value: Any, limit: int = 90) -> str:
    text = _clean_main_text(str(value or "").replace("\n", " "))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("；，,。 ") + "..."


def _dedupe_by_key(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _compact_text(value, limit=140)
        key = re.sub(r"[\W_]+", "", text.lower())[:42]
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _markdown_table(headers: list[str], rows: list[list[Any]], empty_row: list[str] | None = None) -> list[str]:
    safe_rows = rows or ([empty_row] if empty_row else [])
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in safe_rows:
        cells = [_compact_text(cell, limit=120).replace("|", "/") or "-" for cell in row]
        if len(cells) < len(headers):
            cells.extend(["-"] * (len(headers) - len(cells)))
        lines.append("| " + " | ".join(cells[:len(headers)]) + " |")
    return lines


def _module_summary_rows(packages: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for package in packages:
        module = _module_name(package)
        answers = [a for a in package.get("answers", []) or [] if isinstance(a, dict)]
        claims = [c for c in package.get("claims", []) or [] if isinstance(c, dict)]
        conclusion = answers[0].get("answer") if answers else (claims[0].get("claim") if claims else package.get("markdown_draft", ""))
        confidence = answers[0].get("confidence") if answers else (claims[0].get("confidence") if claims else "unknown")
        gaps = package.get("data_gaps", []) or []
        impact = "可支撑下一步判断" if not gaps else f"需优先补齐 {len(gaps)} 项缺口"
        rows.append([module, conclusion, _confidence_label(confidence), impact])
    return rows


def _evidence_rows(packages: list[dict[str, Any]], facts_by_id: dict[str, dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for package in packages:
        module = _module_name(package)
        for claim in package.get("claims", []) or []:
            if not isinstance(claim, dict):
                continue
            fact_ids = [str(fid) for fid in claim.get("fact_ids", []) or []]
            fact_texts = [_fact_text(fid, facts_by_id) for fid in fact_ids[:2]]
            evidence = "；".join(text for text in fact_texts if text and text != "事实详情未进入 Fact Store") or claim.get("reasoning") or claim.get("claim")
            rows.append([module, claim.get("claim", ""), evidence, claim.get("source_quality", "unknown"), "支撑主结论" if fact_ids else "仅作线索"])
    if not rows:
        for package in packages:
            for answer in package.get("answers", []) or []:
                if isinstance(answer, dict):
                    rows.append([_module_name(package), answer.get("answer", ""), answer.get("limits", ""), answer.get("confidence", "unknown"), "需结合其他模块复核"])
    return rows[:12]


def _risk_level(text: str) -> str:
    high_keywords = ("阻断", "违法", "诉讼", "失信", "无客户", "无订单", "无收入", "撤回", "造假", "重大")
    medium_keywords = ("缺失", "不足", "不明确", "待验证", "依赖", "有限", "缺口")
    if any(keyword in text for keyword in high_keywords):
        return "高"
    if any(keyword in text for keyword in medium_keywords):
        return "中"
    return "低"


def _risk_rows(counter_evidence: list[str], gaps: list[str], coverage: dict[str, Any]) -> list[list[str]]:
    raw_items = [item for item in counter_evidence if "暂无" not in str(item)]
    for claim in coverage.get("claims", []) or []:
        if isinstance(claim, dict) and str(claim.get("status", "")).lower() in {"unverified", "partially_supported", "contradicted", "not_addressed"}:
            raw_items.append(str(claim.get("claim") or "未验证声称"))
    rows: list[list[str]] = []
    for item in _dedupe_by_key(raw_items)[:10]:
        level = _risk_level(item)
        action = "投资前必须补证" if level == "高" else "下一轮 DD 核验"
        rows.append([item, level, "来自多维尽调/覆盖校验", "影响估值折扣和推进节奏", action])
    if not rows and gaps:
        for item in _dedupe_by_key(gaps)[:6]:
            rows.append([item, _risk_level(item), "数据缺口", "未补齐前不支撑主结论", "补充原始材料"])
    return rows


def _gap_rows(gaps: list[str], coverage: dict[str, Any]) -> list[list[str]]:
    raw_gaps = list(gaps)
    for claim in coverage.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        for gap in claim.get("data_gaps", []) or []:
            raw_gaps.append(str(gap))
    rows: list[list[str]] = []
    for gap in _dedupe_by_key(raw_gaps)[:10]:
        priority = "P0" if _risk_level(gap) == "高" else "P1"
        rows.append([gap, "原始合同/订单/访谈/工商或财务证明", priority, "未补齐则维持观察或暂缓"])
    return rows


def assemble_bp_report(task_dir: Path, entity: str = "目标公司") -> dict[str, Any]:
    task_dir = Path(task_dir)
    section_index = _load_json(task_dir / "bp_section_packages.json", {"packages": []})
    coverage = _load_json(task_dir / "bp_claim_coverage.json", {"summary": {}, "claims": []})
    debate = _load_json(task_dir / "bp_debate_review.json", {})
    shared_state = _load_json(task_dir / "bp_shared_state.json", {})
    fact_store = _load_json(task_dir / "bp_fact_store.json", {"facts": []})
    facts_by_id = _fact_index(fact_store)
    packages = _valid_packages(section_index)
    if not packages:
        return {"ok": False, "block_reason": "no_valid_sections", "markdown_path": "", "issues": [{"code": "NO_VALID_SECTIONS"}]}

    facts_used = _collect_facts(packages)
    claim_ids = _collect_claims(packages)
    gaps = _collect_unique(packages, "data_gaps")
    counter_evidence = _collect_unique(packages, "counter_evidence")
    blocks = _narrative_blocks(packages)
    answers = _answers(packages)
    current_rec = shared_state.get("current_recommendation") or {}
    recommendation = current_rec.get("verdict")
    if not recommendation:
        print("  ⚠️ [assembler] shared_state 无 recommendation，降级为 undecided", flush=True)
        recommendation = "undecided"
    confidence = current_rec.get("confidence")
    if not confidence:
        confidence = "low"

    support_reasons = current_rec.get("supporting_reasons", [])
    deal_breakers = current_rec.get("deal_breakers", [])
    must_verify = current_rec.get("must_verify_before_investment", [])
    module_rows = _module_summary_rows(packages)
    evidence_rows = _evidence_rows(packages, facts_by_id)
    risk_rows = _risk_rows(counter_evidence, gaps, coverage)
    gap_rows = _gap_rows(gaps + list(must_verify), coverage)
    claim_rows = []
    for claim in coverage.get("claims", []) or []:
        if isinstance(claim, dict):
            gaps_text = _short_list(claim.get("data_gaps", []) or [], limit=2, empty="暂无")
            claim_rows.append([claim.get("claim", ""), _status_label(str(claim.get("status") or "")), claim.get("priority", "普通"), gaps_text])

    lines: list[str] = [
        f"# {entity} BP尽调审计底稿",
        "",
        "> 本底稿按投资决策链结构化整理，基于已通过结构化校验的尽调材料。未验证内容进入数据缺口，不支撑主结论。",
        "> 本底稿为内部复核用，投资决策请以主报告（投资备忘录）为准。",
        "",
        "## 1. 投资结论",
        "",
        "**本章回答的问题：这家公司当前是否值得进入下一步投资流程？**",
        "",
        f"- 当前建议：{_recommendation_label(recommendation)}",
        f"- 结论置信度：{_confidence_label(confidence)}",
        f"- 关键支持理由：{_short_list(list(support_reasons), limit=3, empty='暂无明确正向理由')}",
        f"- Deal Breakers：{_short_list(list(deal_breakers) or [row[0] for row in risk_rows if row[1] == '高'], limit=3, empty='暂无立即阻断项')}",
        f"- 下一步 DD：{_short_list(list(must_verify) or [row[0] for row in gap_rows], limit=3, empty='按数据缺口清单补证')}",
        "",
        "## 2. 一页摘要表",
        "",
        "**本章回答的问题：各模块的结论、证据强度和投资含义是什么？**",
        "",
    ]
    lines.extend(_markdown_table(["模块", "结论", "证据强度", "投资含义"], module_rows, ["综合", "暂无结构化模块结论", "未知", "需回补"] ))
    lines.extend([
        "",
        "## 3. 核心证据矩阵",
        "",
        "**本章回答的问题：哪些事实支持或约束投资判断？**",
        "",
        f"- 证据覆盖：{_claim_summary(coverage)}",
        f"- 外部证据：{_source_summary(len(facts_used))}",
        "",
    ])
    lines.extend(_markdown_table(["模块", "事项", "事实/证据", "来源强度", "投资含义"], evidence_rows, ["综合", "暂无", "暂无可用事实", "未知", "不支撑主结论"] ))
    lines.extend([
        "",
        "## 4. 声称覆盖与处置表",
        "",
        "**本章回答的问题：核心商业声称哪些已覆盖，哪些仍未验证？**",
        "",
    ])
    lines.extend(_markdown_table(["事项", "状态", "优先级", "处置建议"], claim_rows, ["暂无结构化声称", "未覆盖", "普通", "补充 Section Package"] ))
    lines.extend([
        "",
        "## 5. 关键风险矩阵",
        "",
        "**本章回答的问题：哪些风险影响推进、估值和交易条件？**",
        "",
    ])
    lines.extend(_markdown_table(["风险项", "等级", "当前证据", "投资影响", "处置动作"], risk_rows, ["暂无重大反证", "低", "结构化材料未提示", "不构成当前阻断", "常规复核"] ))
    lines.extend([
        "",
        "## 6. 数据缺口与 DD 清单",
        "",
        "**本章回答的问题：投资前必须补齐哪些材料？**",
        "",
    ])
    lines.extend(_markdown_table(["缺口", "需补材料", "优先级", "不补齐影响"], gap_rows, ["暂无结构化数据缺口", "常规访谈和底稿", "P2", "维持常规复核"] ))
    cross_rows = []
    for block in blocks[:8]:
        if not isinstance(block, dict):
            continue
        text = _clean_main_text(block.get("text", ""))
        if text:
            cross_rows.append([text, "影响商业化可信度、估值可用性或推进条件", "纳入下一轮 DD 核验"])
    lines.extend([
        "",
        "## 7. 交叉判断与推进条件",
        "",
        "**本章回答的问题：事实、风险和缺口合在一起意味着什么？**",
        "",
        f"- 投资影响：当前建议由 {len(evidence_rows)} 条核心证据、{len(risk_rows)} 项风险和 {len(gap_rows)} 项 DD 缺口共同决定。",
        f"- 推进条件：{_short_list([row[4] for row in risk_rows if len(row) > 4] + [row[1] for row in gap_rows if len(row) > 1], limit=4, empty='完成常规复核')}",
        "- 结论使用边界：未验证事项不得进入估值基准或投资主结论，只能作为下一轮 DD 问题清单。",
        "",
        "### 关键交叉判断",
    ])
    lines.extend(_markdown_table(["判断", "投资含义", "下一步动作"], cross_rows, ["暂无结构化交叉判断", "不改变当前结论", "常规复核"] ))
    lines.extend([
        "",
        "## 附录：证据台账说明",
        "",
        "**本章回答的问题：本报告的证据链如何留痕？**",
        "",
        f"- 本报告引用 {len(facts_used)} 条结构化事实，覆盖 {len(claim_ids)} 条核心声称。",
        "- 具体机器编号、来源映射和校验状态保留在内部审计材料中，不进入交付正文。",
        "- 投资决策前应以内部事实台账、工商/司法/专利数据库和访谈底稿做最终复核。",
        "",
        "### 外部事实摘要",
    ])
    fact_rows = []
    for fact_id in facts_used[:12]:
        fact = facts_by_id.get(str(fact_id), {})
        claim = _clean_main_text(fact.get("claim") or fact.get("fact") or fact.get("value") or "未命名事实")
        source_url = str(fact.get("source_url") or "").strip()
        source_name = str(fact.get("source") or "").strip()
        tier = str(fact.get("source_tier") or fact.get("source_quality") or "unknown").strip()
        if source_url and source_url not in ("无外部来源URL", "无外部来源"):
            display = source_name if source_name else source_url
            source_cell = f"[{display}]({source_url})"
        elif source_name:
            source_cell = source_name
        else:
            source_cell = "无外部来源"
        fact_rows.append([claim, tier, source_cell])
    lines.extend(_markdown_table(["事实摘要", "来源等级", "来源（含链接）"], fact_rows, ["暂无外部事实摘要", "-", "-"] ))
    lines.append("")

    report_path = task_dir / "bp_final_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    result = {
        "ok": True,
        "block_reason": "",
        "markdown_path": str(report_path),
        "facts_used": facts_used,
        "claim_ids_used": claim_ids,
        "sections_assembled": [str(package.get("section_id") or package.get("section_title")) for package in packages],
        "assembly_mode": "investment_decision_chain",
        "issues": [],
    }
    (task_dir / "bp_final_assembly.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
