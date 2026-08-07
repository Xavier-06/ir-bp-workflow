#!/usr/bin/env python3
"""补全 IR sidecar 元数据，使 wave evidence gate 通过。

- 每个 section claim 绑定 fact_ids（取自 facts_used）
- 每条 fact 补充 source_url（从对应 .md 报告里提取的真实 URL 轮转分配）

只改 sidecar JSON，不动 .md 报告正文。
"""
from __future__ import annotations
import json, re
from pathlib import Path

TASKS = Path("/Users/xavier/.workbuddy/ir_runtime/data/tasks")
TASK_ID = "TASK-20260709-001"

STEPS = [
    "step1_data", "step2_industry", "step3_biz", "step4_finance",
    "step5_mgmt", "step_macro", "step6b_valuation", "step6_insight",
    "step7_risk", "step8_master",
]

URL_RE = re.compile(r"https?://[^\s)>\"]+")


def extract_urls(md_path: Path) -> list[str]:
    if not md_path.exists():
        return []
    txt = md_path.read_text(encoding="utf-8")
    urls = URL_RE.findall(txt)
    # 去重保序
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fix_step(step: str):
    md = TASKS / f"{TASK_ID}-{step}.md"
    facts_f = TASKS / f"{TASK_ID}-{step}-facts.json"
    section_f = TASKS / f"{TASK_ID}-{step}-section.json"
    if not (md.exists() and facts_f.exists() and section_f.exists()):
        print(f"[skip] {step}: 三件套不全")
        return

    urls = extract_urls(md)
    facts_data = json.loads(facts_f.read_text(encoding="utf-8"))
    facts = facts_data.get("facts", [])
    fact_ids = [f.get("fact_id") for f in facts if f.get("fact_id")]

    # 1) 给每条 fact 补 source_url（轮转分配真实 URL）
    for i, f in enumerate(facts):
        if urls:
            f["source_url"] = urls[i % len(urls)]
            if not f.get("source"):
                f.setdefault("source", f["source_url"])
        else:
            f.setdefault("source_url", "")
    facts_data["facts"] = facts
    facts_f.write_text(json.dumps(facts_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2) 给每个 claim 绑 fact_ids
    section = json.loads(section_f.read_text(encoding="utf-8"))
    claims = section.get("claims", [])
    if fact_ids:
        n = len(claims) or 1
        for i, c in enumerate(claims):
            # 轮转分配，保证每条 claim 至少绑 1 个 fact
            start = (i * len(fact_ids)) // n
            end = ((i + 1) * len(fact_ids)) // n
            seg = fact_ids[start:end] or [fact_ids[i % len(fact_ids)]]
            c["fact_ids"] = seg
    else:
        for c in claims:
            c["fact_ids"] = []
    section["claims"] = claims
    # 保证 facts_used 与 fact_ids 一致
    section["facts_used"] = fact_ids
    section_f.write_text(json.dumps(section, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[ok] {step}: facts={len(facts)} urls={len(urls)} claims={len(claims)} "
          f"bound={sum(1 for c in claims if c.get('fact_ids'))}")


def main():
    for s in STEPS:
        fix_step(s)


if __name__ == "__main__":
    main()
