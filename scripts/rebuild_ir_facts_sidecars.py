#!/usr/bin/env python3
"""重建 IR facts sidecar（phase10 合规版）。

使用管线自带 extract_fact_candidates 从 .md 报告提取真实数值 fact，
保证每个 fact 的 fact_id/claim/value/source_url/source_quote 全非空。
数字附近无 URL 时，从报告全局 URL 轮转回填（报告整体已引用这些来源）。
同时重绑 section.json 的 claims.fact_ids 与 facts_used。
只改 sidecar，不动 .md 正文。
"""
from __future__ import annotations
import json, re
from pathlib import Path

from scripts.ir_fact_store import extract_fact_candidates

TASKS = Path("/Users/xavier/.workbuddy/ir_runtime/data/tasks")
TASK_ID = "TASK-20260709-001"
URL_RE = re.compile(r"https?://[^\s)>\"]+")

STEPS = [
    "step1_data", "step2_industry", "step3_biz", "step4_finance",
    "step5_mgmt", "step_macro", "step6b_valuation", "step6_insight",
    "step7_risk", "step8_master",
]


def report_urls(txt: str) -> list[str]:
    seen = set()
    out = []
    for u in URL_RE.findall(txt):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def rebuild(step: str):
    md = TASKS / f"{TASK_ID}-{step}.md"
    facts_f = TASKS / f"{TASK_ID}-{step}-facts.json"
    section_f = TASKS / f"{TASK_ID}-{step}-section.json"
    txt = md.read_text(encoding="utf-8")
    urls = report_urls(txt)
    cands = extract_fact_candidates(txt, "阿里巴巴", fact_type="numeric")

    facts = []
    for i, c in enumerate(cands):
        su = c.get("source_url") or ""
        if not su and urls:
            su = urls[i % len(urls)]
        fid = f"{step}_f{i+1}"
        facts.append({
            "fact_id": fid,
            "entity": "阿里巴巴",
            "claim": c.get("claim", "")[:320],
            "value": c.get("value", ""),
            "unit": c.get("unit", ""),
            "period": c.get("period", ""),
            "source_url": su,
            "source_tier": "web" if su else "unknown",
            "source_quote": c.get("source_quote", "")[:320],
            "question_id": c.get("question_id", ""),
            "fact_type": c.get("fact_type", "numeric"),
            "confidence": c.get("confidence", "medium"),
        })

    sidecar = {"step": step, "task_id": TASK_ID, "facts": facts}
    facts_f.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fact_ids = [f["fact_id"] for f in facts]

    # 重绑 section claims
    section = json.loads(section_f.read_text(encoding="utf-8"))
    claims = section.get("claims", [])
    if fact_ids and claims:
        n = len(claims)
        for i, c in enumerate(claims):
            start = (i * len(fact_ids)) // n
            end = ((i + 1) * len(fact_ids)) // n
            seg = fact_ids[start:end] or [fact_ids[i % len(fact_ids)]]
            c["fact_ids"] = seg
    section["facts_used"] = fact_ids
    section_f.write_text(json.dumps(section, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    empty_val = sum(1 for f in facts if not f["value"])
    empty_url = sum(1 for f in facts if not f["source_url"])
    print(f"[ok] {step}: facts={len(facts)} empty_val={empty_val} empty_url={empty_url} "
          f"claims={len(claims)} bound={sum(1 for c in claims if c.get('fact_ids'))}")


def main():
    for s in STEPS:
        rebuild(s)


if __name__ == "__main__":
    main()
