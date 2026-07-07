#!/usr/bin/env python3
"""Rule-based readability gate for IR final report.

移植自 bp_readability_reviewer.py，裁剪 BP 专用检查项，保留通用可读性规则。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# ── 通用高频技术缩写 ──
_TECH_TERMS_GENERIC = ("API", "SDK", "SaaS", "PaaS", "IaaS", "AI", "ML", "GPU", "CPU", "SoC")

# 按行业补充
_INDUSTRY_TECH_TERMS = {
    "半导体": ("ASIC", "FPGA", "MEMS", "RHBD", "EDA", "DRC", "LVS", "FinFET", "EUV", "CMP"),
    "生物医药": ("IND", "NDA", "GLP", "GMP", "GCP", "CDMO", "CRO", "CMO", "PK/PD", "ADC"),
    "新能源": ("BESS", "PCS", "BMS", "EMS", "LFP", "NCM", "HJT", "TOPCon", "PERC"),
    "汽车": ("ADAS", "LIDAR", "V2X", "OTA", "BMS", "EPS", "ESP", "OBD"),
    "default": (),
}

_ABB_EXCLUDE = frozenset({
    "BP", "CEO", "CFO", "CTO", "COO", "IPO", "PE", "PS", "PB", "VC", "TTM",
    "CAGR", "ROI", "IRR", "MOIC", "USD", "RMB", "CNY", "HKD",
    "EBITDA", "ARR", "MRR", "GMV", "DAU", "MAU", "NPS", "YoY", "QoQ",
    "TAM", "SAM", "SOM", "WACC", "ROE", "ROA", "D/E", "NAV",
    "DG", "CE", "BC", "BF", "ESQ", "G0", "G1", "P1", "F0",
})

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
    "搜索审计",
    "来源与参考",
)


def _extract_tech_terms(text: str, task_dir: Path | None = None) -> tuple[str, ...]:
    """从报告文本中提取高频英文缩写（2+ 大写字母，出现 2+ 次）。"""
    industry = ""
    if task_dir:
        try:
            profile = json.loads((Path(task_dir) / "ir_company_verify.json").read_text(encoding="utf-8"))
            industry = profile.get("industry", "") or profile.get("sector", "")
        except Exception:
            pass

    industry_terms: set[str] = set()
    for key, terms in _INDUSTRY_TECH_TERMS.items():
        if key == "default":
            continue
        if key in industry or industry in key:
            industry_terms.update(terms)
    if not industry_terms:
        industry_terms.update(_INDUSTRY_TECH_TERMS.get("default", ()))
    industry_terms.update(_TECH_TERMS_GENERIC)

    abbr_pattern = re.compile(r'\b([A-Z][A-Z0-9]{1,10})\b')
    abbrs = Counter(m.group(1) for m in abbr_pattern.finditer(text))
    text_terms = set()
    for abbr, count in abbrs.items():
        if count < 2:
            continue
        if abbr in _ABB_EXCLUDE:
            continue
        if re.match(r'^[A-Z]{1,3}\d{2,}$', abbr):
            continue
        text_terms.add(abbr)

    return tuple(sorted(industry_terms | text_terms))


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


def review_ir_readability(report_path: Path, task_dir: Path | None = None) -> dict[str, Any]:
    """对 IR 最终报告做可读性检查。

    保留通用规则，裁剪 BP 专用检查项。
    """
    report_path = Path(report_path)
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if task_dir is None:
        task_dir = report_path.parent if report_path.parent.exists() else None

    # 提取公司名用于重复检测豁免
    _entity_name_cache: set[str] = set()
    if task_dir:
        try:
            verify = json.loads((Path(task_dir) / "ir_company_verify.json").read_text(encoding="utf-8"))
            name = verify.get("company_name", "") or verify.get("entity", "")
            if name and 2 <= len(name) <= 20:
                _entity_name_cache.add(name)
        except Exception:
            pass
    _entity_name_cache.discard("")

    issues: list[dict[str, str]] = []

    # ── 1. 开头是否有投资结论/摘要 ──
    first_800 = text[:800]
    opening_keywords = ("投资结论", "投资建议", "投资摘要", "核心观点", "核心结论", "Executive Summary", "投资亮点")
    if not any(kw in first_800 for kw in opening_keywords):
        issues.append({
            "severity": "MEDIUM",
            "code": "OPENING_NO_INVESTMENT_SUMMARY",
            "message": "前 800 字没有投资结论/摘要/核心观点",
        })

    # ── 2. 机器 ID 泄漏 ──
    main_body = text.split("## 来源与参考", 1)[0].split("## 附录", 1)[0]
    machine_id_hits = re.findall(r"\bF-\d{3,}\b", main_body)
    if machine_id_hits:
        issues.append({
            "severity": "FAIL",
            "code": "MACHINE_ID_LEAKAGE",
            "message": f"主报告正文泄漏 fact_id: {', '.join(machine_id_hits[:6])}",
        })

    # ── 3. 原始元数据泄漏 ──
    if any(kw in main_body for kw in ("fact_ids:", "claim_ids:", "facts_used:")):
        issues.append({
            "severity": "FAIL",
            "code": "RAW_METADATA_LEAKAGE",
            "message": "主报告正文泄漏 fact_ids/claim_ids/facts_used 等机器字段",
        })

    # ── 4. 超长段落 ──
    long_paragraphs = [
        line[:80] for line in main_body.splitlines()
        if line and not line.startswith(("#", "- ", "|", ">", "**")) and len(line) > 400
    ]
    if long_paragraphs:
        issues.append({
            "severity": "MEDIUM",
            "code": "OVERLONG_PARAGRAPH",
            "message": f"正文存在超过400字的长段落: {long_paragraphs[0]}...",
        })

    # ── 5. 超长项目符号 ──
    overlong_bullets = [
        line[:80] for line in main_body.splitlines()
        if line.startswith("- ") and len(line) > 200
    ]
    if overlong_bullets:
        issues.append({
            "severity": "MEDIUM",
            "code": "OVERLONG_BULLET",
            "message": f"正文存在超过200字的项目符号行: {overlong_bullets[0]}...",
        })

    # ── 6. 重复 bullet 内容 ──
    bullet_counts: dict[str, int] = {}
    for line in main_body.splitlines():
        if not line.startswith("- "):
            continue
        normalized = _normalized_bullet(line)
        if len(normalized) >= 24:
            bullet_counts[normalized] = bullet_counts.get(normalized, 0) + 1
    duplicated_bullets = [key for key, count in bullet_counts.items() if count > 1]
    if duplicated_bullets:
        issues.append({
            "severity": "MEDIUM",
            "code": "DUPLICATED_BULLET_CONTENT",
            "message": f"同一内容跨章节重复: {duplicated_bullets[0][:40]}",
        })

    # ── 7. 重复短语 ──
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
        if phrase in _entity_name_cache:
            continue
        direct_count = text.count(phrase)
        consecutive_repeats = re.search(rf"(?:{re.escape(phrase)}){{3,}}", text) is not None
        if (direct_count > 4 or consecutive_repeats) and phrase not in repeated_phrases:
            repeated_phrases.append(phrase)
    if repeated_phrases:
        issues.append({
            "severity": "MEDIUM",
            "code": "REPEATED_FACT_PHRASE",
            "message": f"同一事实/短语重复过多: {', '.join(repeated_phrases[:5])}",
        })

    # ── 8. 技术术语未解释 ──
    _tech_terms = _extract_tech_terms(text, task_dir=task_dir)
    unexplained_terms = [term for term in _tech_terms if term in text and not _looks_explained(term, text)]
    if unexplained_terms:
        issues.append({
            "severity": "MEDIUM",
            "code": "UNEXPLAINED_TECH_TERMS",
            "message": f"技术术语未解释: {', '.join(unexplained_terms[:10])}",
        })

    # ── 9. 脚注连续性检查 ──
    footnote_refs = re.findall(r"\[\^(\d+)\]", main_body)
    footnote_defs = re.findall(r"^\[\^(\d+)\]:", text, re.MULTILINE)
    if footnote_refs and footnote_defs:
        ref_nums = set(int(n) for n in footnote_refs)
        def_nums = set(int(n) for n in footnote_defs)
        missing_defs = ref_nums - def_nums
        if missing_defs:
            issues.append({
                "severity": "MEDIUM",
                "code": "FOOTNOTE_MISSING_DEFINITION",
                "message": f"脚注引用但无定义: [^{min(missing_defs)}] 等 {len(missing_defs)} 个",
            })
        unused_defs = def_nums - ref_nums
        if len(unused_defs) > len(def_nums) * 0.3:
            issues.append({
                "severity": "LOW",
                "code": "FOOTNOTE_UNUSED_DEFINITIONS",
                "message": f"{len(unused_defs)} 个脚注定义未被引用（占比 {len(unused_defs)}/{len(def_nums)}）",
            })

    # ── 10. 报告结构完整性 ──
    h2_count = len([l for l in text.splitlines() if l.startswith("## ")])
    if h2_count < 5:
        issues.append({
            "severity": "MEDIUM",
            "code": "INSUFFICIENT_SECTIONS",
            "message": f"报告章节不足 ({h2_count} 个 ## 章节)，完整研报通常需要 ≥5 个章节",
        })

    table_count = _markdown_table_count(main_body)
    if table_count < 2:
        issues.append({
            "severity": "LOW",
            "code": "LOW_TABLE_COUNT",
            "message": f"正文表格较少 ({table_count} 个)，投研报告通常需要对比表格",
        })

    # ── 判定 ──
    has_fail = any(i["severity"] == "FAIL" for i in issues)
    verdict = "FAIL" if has_fail else ("PASS_WITH_WARNINGS" if issues else "PASS")

    return {
        "schema_version": "ir_readability_review.v1",
        "verdict": verdict,
        "issues": issues,
        "issue_count": len(issues),
        "fail_count": sum(1 for i in issues if i["severity"] == "FAIL"),
        "medium_count": sum(1 for i in issues if i["severity"] == "MEDIUM"),
        "low_count": sum(1 for i in issues if i["severity"] == "LOW"),
        "report_path": str(report_path),
        "content_length": len(text),
        "h2_count": h2_count,
        "table_count": table_count,
        "footnote_refs": len(footnote_refs) if footnote_refs else 0,
        "footnote_defs": len(footnote_defs) if footnote_defs else 0,
    }


def write_ir_readability_review(task_dir: Path) -> dict[str, Any]:
    """对 IR 最终报告做可读性审查并写入结果。"""
    task_dir = Path(task_dir)

    # 优先用 final_report.md，fallback 到 step8_master 输出
    report_candidates = [
        task_dir / "ir_final_report.md",
        task_dir / "final_report.md",
    ]
    # 也尝试找 step8_master 输出
    for p in task_dir.glob("*-step8_master.md"):
        report_candidates.append(p)

    report_path = None
    for candidate in report_candidates:
        if candidate.exists() and candidate.stat().st_size > 500:
            report_path = candidate
            break

    if report_path is None:
        result = {
            "schema_version": "ir_readability_review.v1",
            "verdict": "FAIL",
            "issues": [{"severity": "FAIL", "code": "NO_REPORT_FOUND", "message": "未找到最终报告文件"}],
            "report_path": "",
        }
    else:
        result = review_ir_readability(report_path, task_dir=task_dir)

    path = task_dir / "ir_readability_review.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result | {"review_path": str(path)}
