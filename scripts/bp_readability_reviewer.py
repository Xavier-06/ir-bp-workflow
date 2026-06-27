#!/usr/bin/env python3
"""Rule-based readability gate for BP final report."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_DIMENSION_HEADING_KEYWORDS = ("团队与合规", "技术与产品", "行业与供应链", "竞争与结论", "估值")
# 通用高频技术缩写（所有行业可能用到）
_TECH_TERMS_GENERIC = ("API", "SDK", "SaaS", "PaaS", "IaaS", "AI", "ML", "GPU", "CPU", "SoC")
# 按行业补充的术语映射
_INDUSTRY_TECH_TERMS = {
    "半导体": ("ASIC", "FPGA", "MEMS", "RHBD", "EDA", "DRC", "LVS", "FinFET", "EUV", "CMP"),
    "生物医药": ("IND", "NDA", "GLP", "GMP", "GCP", "CDMO", "CRO", "CMO", "PK/PD", "ADC"),
    "新能源": ("BESS", "PCS", "BMS", "EMS", "LFP", "NCM", "HJT", "TOPCon", "PERC"),
    "汽车": ("ADAS", "LIDAR", "V2X", "OTA", "BMS", "EPS", "ESP", "OBD"),
    "default": (),
}


def _extract_tech_terms(text: str, task_dir: Path | None = None) -> tuple[str, ...]:
    """从报告文本中提取高频英文缩写（2+ 大写字母，出现 2+ 次）。"""
    # 从 profile 获取行业
    industry = ""
    if task_dir:
        try:
            profile = json.loads((Path(task_dir) / "bp_step0_profile.json").read_text(encoding="utf-8"))
            industry = profile.get("industry", "")
        except Exception:
            pass

    # 行业特定术语
    industry_terms: set[str] = set()
    for key, terms in _INDUSTRY_TECH_TERMS.items():
        if key == "default":
            continue
        if key in industry or industry in key:
            industry_terms.update(terms)
    if not industry_terms:
        industry_terms.update(_INDUSTRY_TECH_TERMS.get("default", ()))
    industry_terms.update(_TECH_TERMS_GENERIC)

    # 从文本中提取 2+ 大写字母的缩写，出现 2+ 次
    # 排除内部 ID（gap_id、evidence_id 等）、含 / 的复合缩写片段、纯数字后缀
    _ABB_EXCLUDE = frozenset({
        "BP", "CEO", "CFO", "CTO", "COO", "IPO", "PE", "PS", "VC", "TTM",
        "CAGR", "ROI", "IRR", "MOIC", "USD", "RMB", "EBITDA", "ARR", "MRR",
        "GMV", "DAU", "MAU", "NPS", "YoY", "QoQ",
        # 内部 ID 前缀，不是技术术语
        "DG", "CE", "BC", "BF", "ESQ", "G0", "G1", "P1",
    })
    abbr_pattern = re.compile(r'\b([A-Z][A-Z0-9]{1,10})\b')
    from collections import Counter
    abbrs = Counter(m.group(1) for m in abbr_pattern.finditer(text))
    text_terms = set()
    for abbr, count in abbrs.items():
        if count < 2:
            continue
        if abbr in _ABB_EXCLUDE:
            continue
        # 排除纯数字结尾的内部 ID（如 DG001, G008）
        if re.match(r'^[A-Z]{1,3}\d{2,}$', abbr):
            continue
        text_terms.add(abbr)

    return tuple(sorted(industry_terms | text_terms))
_FIRST_PAGE_REQUIRED_BLOCKS = ("当前建议", "置信度", "关键支持理由", "Deal Breakers", "下一步 DD")
_CHAPTER_REQUIRED_BLOCKS = ("证据覆盖", "外部证据", "反证/缺口", "投资影响")
_REPEAT_EXEMPT_PHRASES = (
    "本章回答的问题",
    "fact_ids",
    "claim_ids",
    "confidence",
    "source_quality",
    "暂无结构化",
    "数据缺口",
    "暂无重大反证",
    "投资影响",
    "投资含义",
    "条核心声称",
    "存在反证",
    "关键未解决",
    "声称验证",
    "外部事实",
    "结构化事实",
    "证据覆盖",
    "外部证据",
    "机器编号",
    "机器ID",
    "内部审计材料",
    "具体来源保留在内部审计材料中",
    "明细见附录",
    "主要反证",
)


def _section_text(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    next_start = text.find("\n## ", start + 1)
    end = next_start if next_start != -1 else len(text)
    return text[start:end]


def _looks_explained(term: str, text: str) -> bool:
    idx = text.find(term)
    if idx < 0:
        return True
    window = text[max(0, idx - 80): idx + 160]
    explanation_markers = ("即", "也就是", "指", "意思是", "用于", "作用是", "通过", "例如", "是一种")
    return any(marker in window for marker in explanation_markers)


def _normalized_bullet(line: str) -> str:
    text = re.sub(r"^[-*]\s+", "", line.strip())
    text = re.sub(r"[\s，,。；;：:、（）()]+", "", text)
    return text[:80]


def _markdown_table_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"^\|\s*[-:]{3,}", line.strip()))


def review_bp_readability(report_path: Path) -> dict[str, Any]:
    report_path = Path(report_path)
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""

    # 提取报告标题中的公司名，加入重复检测豁免列表（公司名在报告中高频出现是正常的）
    _entity_name_cache: set[str] = set()
    title_match = re.match(r'^#\s+(.+?)\s*(?:—|—|BP|尽调|投资)', text)
    if title_match:
        entity_name = title_match.group(1).strip()
        if 3 <= len(entity_name) <= 20:
            _entity_name_cache.add(entity_name)
    # 也从 profile 提取
    _task_dir = report_path.parent if report_path.parent.exists() else None
    if _task_dir:
        try:
            _profile = json.loads((_task_dir / "bp_step0_profile.json").read_text(encoding="utf-8"))
            _entity_name_cache.add(_profile.get("company_name", ""))
        except Exception:
            pass
    _entity_name_cache.discard("")
    issues: list[dict[str, str]] = []
    first_800 = text[:800]
    if "投资结论" not in first_800 and "当前建议" not in first_800 and "建议" not in first_800:
        issues.append({"severity": "FAIL", "code": "OPENING_NO_INVESTMENT_RECOMMENDATION", "message": "前 800 字没有投资建议或当前建议"})

    main_body = text.split("## 附录", 1)[0]
    machine_id_hits = re.findall(r"\b(?:BP-[A-Z0-9_]+-F\d{3,}|BF-\d{3,}|F\d{3,}|BC\d{3,})\b", main_body)
    if machine_id_hits:
        issues.append({"severity": "FAIL", "code": "MACHINE_ID_LEAKAGE", "message": f"主报告正文泄漏机器ID: {', '.join(machine_id_hits[:6])}"})
    table_count = _markdown_table_count(main_body)
    if table_count < 4:
        issues.append({"severity": "FAIL", "code": "MISSING_STRUCTURED_SUMMARY", "message": f"正文表格不足，至少需要摘要表、证据矩阵、风险矩阵、DD清单；当前 {table_count} 个"})
    overlong_bullets = [line[:80] for line in main_body.splitlines() if line.startswith("- ") and len(line) > 180]
    if overlong_bullets:
        issues.append({"severity": "FAIL", "code": "OVERLONG_BULLET", "message": f"正文存在超过180字的项目符号行: {overlong_bullets[0]}..."})
    long_paragraphs = [line[:80] for line in main_body.splitlines() if line and not line.startswith(("#", "- ", "|", ">", "**")) and len(line) > 350]
    if long_paragraphs:
        issues.append({"severity": "FAIL", "code": "OVERLONG_PARAGRAPH", "message": f"正文存在超过350字的长段落: {long_paragraphs[0]}..."})
    if "fact_ids:" in main_body or "claim_ids:" in main_body or "facts_used:" in main_body:
        issues.append({"severity": "FAIL", "code": "RAW_METADATA_LEAKAGE", "message": "主报告正文泄漏 fact_ids/claim_ids/facts_used 等机器字段"})
    missing_first_page = [block for block in _FIRST_PAGE_REQUIRED_BLOCKS if block not in first_800]
    if "结论置信度" in first_800 and "置信度" in missing_first_page:
        missing_first_page.remove("置信度")
    if ("支持理由" in first_800 or "关键支持理由" in first_800) and "关键支持理由" in missing_first_page:
        missing_first_page.remove("关键支持理由")
    if ("Deal Breakers" in first_800 or "反证/缺口" in first_800) and "Deal Breakers" in missing_first_page:
        missing_first_page.remove("Deal Breakers")
    if ("投资前必须验证" in first_800 or "下一步 DD" in first_800 or "外部证据" in first_800) and "下一步 DD" in missing_first_page:
        missing_first_page.remove("下一步 DD")
    if missing_first_page:
        issues.append({"severity": "FAIL", "code": "FIRST_PAGE_MISSING_DECISION_BLOCKS", "message": f"第一页缺少决策模块: {', '.join(missing_first_page)}"})
    if "根据某维度报告" in text or "据技术维度分析" in text or "维度报告" in text:
        issues.append({"severity": "FAIL", "code": "DIMENSION_REPORT_TRACE", "message": "报告保留维度报告/拼接痕迹"})
    if text.count("第一部分") + text.count("第二部分") + text.count("第三部分") >= 2:
        issues.append({"severity": "FAIL", "code": "MULTIPLE_PART_SYSTEMS", "message": "出现多个第一部分/第二部分体系"})
    h2_sections = [line for line in text.splitlines() if line.startswith("## ") and not line.startswith("## 附录")]
    dimension_heading_hits = 0
    for heading in h2_sections:
        section_text = _section_text(text, heading)
        if "本章回答的问题" not in section_text:
            issues.append({"severity": "FAIL", "code": "SECTION_WITHOUT_QUESTION", "message": f"章节缺少本章回答的问题: {heading}"})
        if "投资结论" in heading:
            required = ("当前建议", "置信度", "关键支持理由", "Deal Breakers", "下一步 DD")
        elif "摘要表" in heading:
            required = ("| 模块 |", "| 结论 |", "投资含义")
        elif "证据矩阵" in heading:
            required = ("证据覆盖", "外部证据", "| 模块 |", "来源强度")
        elif "声称覆盖" in heading:
            required = ("| 事项 |", "状态", "处置建议")
        elif "风险矩阵" in heading:
            required = ("| 风险项 |", "等级", "处置动作")
        elif "数据缺口" in heading:
            required = ("| 缺口 |", "需补材料", "优先级")
        elif "交叉判断" in heading:
            required = ("投资影响", "推进条件", "结论使用边界")
        else:
            required = ()
        missing = [block for block in required if block not in section_text]
        if missing:
            issues.append({"severity": "FAIL", "code": "CHAPTER_MISSING_DECISION_CHAIN", "message": f"章节缺少职责字段: {heading} / {', '.join(missing)}"})
        if any(keyword in heading for keyword in _DIMENSION_HEADING_KEYWORDS):
            dimension_heading_hits += 1
    if dimension_heading_hits >= 2:
        issues.append({"severity": "FAIL", "code": "AGENT_DIMENSION_HEADING", "message": "一级章节仍按子代理/维度标题组织，未改成投资决策链"})

    bullet_counts: dict[str, int] = {}
    for line in main_body.splitlines():
        if not line.startswith("- "):
            continue
        normalized = _normalized_bullet(line)
        if len(normalized) >= 24:
            bullet_counts[normalized] = bullet_counts.get(normalized, 0) + 1
    duplicated_bullets = [key for key, count in bullet_counts.items() if count > 1]
    if duplicated_bullets:
        issues.append({"severity": "FAIL", "code": "DUPLICATED_BULLET_CONTENT", "message": f"同一风险/缺口项目跨章节重复: {duplicated_bullets[0][:40]}"})

    repeated_phrases: list[str] = []
    for match in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9]{4,20})(?:\1){2,}", text):
        phrase = match.group(1)
        if not any(exempt in phrase or phrase in exempt for exempt in _REPEAT_EXEMPT_PHRASES):
            repeated_phrases.append(phrase)
    for phrase in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,30}", main_body):
        if any(exempt in phrase or phrase in exempt for exempt in _REPEAT_EXEMPT_PHRASES):
            continue
        if phrase.endswith("公司") or "成立于" in phrase or re.fullmatch(r"[A-Za-z0-9_]+", phrase):
            continue
        # 排除公司简称/全称（在标题或首段高频出现是正常的）
        if phrase in _entity_name_cache:
            continue
        direct_count = text.count(phrase)
        consecutive_repeats = re.search(rf"(?:{re.escape(phrase)}){{3,}}", text) is not None
        if (direct_count > 3 or consecutive_repeats) and phrase not in repeated_phrases:
            repeated_phrases.append(phrase)
    if repeated_phrases:
        issues.append({"severity": "FAIL", "code": "REPEATED_FACT_PHRASE", "message": f"同一事实/短语重复超过 2 次: {', '.join(repeated_phrases[:5])}"})

    # 动态提取技术术语（基于报告文本和 profile 行业信息）
    _task_dir = report_path.parent if report_path.parent.exists() else None
    _tech_terms = _extract_tech_terms(text, task_dir=_task_dir)
    unexplained_terms = [term for term in _tech_terms if term in text and not _looks_explained(term, text)]
    if unexplained_terms:
        issues.append({"severity": "FAIL", "code": "UNEXPLAINED_TECH_TERMS", "message": f"技术术语未解释: {', '.join(unexplained_terms)}"})

    verdict = "FAIL" if any(issue["severity"] == "FAIL" for issue in issues) else "PASS"
    return {"schema_version": "bp_readability_review.v1", "verdict": verdict, "issues": issues, "report_path": str(report_path)}


def write_readability_review(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    result = review_bp_readability(task_dir / "bp_final_report.md")
    path = task_dir / "bp_readability_review.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"review_path": str(path)}
