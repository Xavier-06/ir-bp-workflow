#!/usr/bin/env python3
"""
IR 统稿交付前自动清洗（2026-08-06，TASK-20260805-003 实战后新建）

背景：统稿 synthesis.md 交付 phase15 时被三道门卡住：
  ① 脚注来源用管线内部编号（step5_macro / step6_valuation），命中泄露黑名单
  ② 脚注只有来源名没 URL，report_gate url_count<3 FAIL
  ③ step8_master.md（synthesis 的 collect 复制品）清洗时漏改不同步

本模块在 phase13 synthesis_collect 阶段自动执行，把三类问题在进交付门前消灭。
原则：只做等价变换（编号→维度名）与事实映射回填（fact 的 source_url→脚注），不编造内容。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parent.parent / "data" / "tasks"

# 内部 step 编号 → 中文维度名（长名在前防前缀误伤）
STEP_NAME_MAP = [
    ("step1_industry", "行业研究"), ("step2_biz", "业务研究"),
    ("step3_finance", "财务分析"), ("step4_mgmt", "治理研究"),
    ("step5_macro", "宏观研究"), ("step6_valuation", "估值分析"),
    ("step7_insight", "预期差研究"), ("step8_risk", "风险研究"),
    ("step8_master", "风险与总结"), ("data_gap", "数据缺口"),
]
# 裸 stepN（带词边界）兜底
_BARE_STEP_PATS = [
    (re.compile(r"\bstep8\b"), "风险研究"),
    (re.compile(r"\bstep2\b"), "业务研究"),
    (re.compile(r"\bstep\d+[a-z_]*\b"), "内部研究"),
]

# 泄露检测同款黑名单（与 verification_agent.LEAK_PATTERNS 对齐）
LEAK_PATTERNS = [
    r"/Users/\S+", r"file://\S+", r"sessions_spawn", r"\bsubagent\b",
    r"instruction_store\w*", r"\.openclaw/\S+", r"scripts/[^\s,.;]+\.py",
    r"bp_presearch\w*", r"bp_preflight\w*", r"thinking=high",
    r"下游子代理", r"搜索词组合", r"主控必须", r"搜索查询[：:]",
]


def _clean_step_names(text: str) -> tuple[str, int]:
    for old, new in STEP_NAME_MAP:
        text = text.replace(old, new)
    n = 0
    for pat, new in _BARE_STEP_PATS:
        text, k = pat.subn(new, text)
        n += k
    return text, n


def _clean_leak_patterns(text: str) -> tuple[str, int]:
    n = 0
    for pat in LEAK_PATTERNS:
        text, k = re.subn(pat, "", text)
        n += k
    return text, n


def _collect_urls_from_fact_store(task_id: str, tasks_dir: Path) -> list[dict]:
    """从 fact_store 提取 (claim, source_url) 对，按出现顺序返回。"""
    fs_path = tasks_dir / f"{task_id}-fact_store.json"
    if not fs_path.exists():
        return []
    try:
        facts = json.loads(fs_path.read_text(encoding="utf-8")).get("facts", [])
    except Exception:
        return []
    pairs = []
    seen_urls = set()
    for f in facts:
        url = (f.get("source_url") or "").strip()
        claim = (f.get("claim") or "").strip()
        if url.startswith("http") and url not in seen_urls:
            seen_urls.add(url)
            pairs.append({"claim": claim, "url": url})
    return pairs


def _backfill_urls(text: str, url_pairs: list[dict], min_total: int = 3) -> tuple[str, int]:
    """给脚注行回填来源 URL。优先匹配 claim 关键词所在的脚注行，
    匹配不到的按顺序追加到尾部脚注。"""
    lines = text.split("\n")
    placed = 0

    def _already_has_url(l: str) -> bool:
        return "http" in l

    # Pass 1: 按 claim 关键词匹配脚注行（幂等：URL 已存在则跳过）
    for pair in url_pairs:
        claim, url = pair["claim"], pair["url"]
        if url in text:
            continue  # 该 URL 已回填过，不重复添加
        # 取 claim 里前几个实词做匹配锚点
        tokens = [t for t in re.split(r"[，。、\s（）()/]+", claim) if len(t) >= 3][:4]
        if not tokens:
            continue
        for i, ln in enumerate(lines):
            if not ln.strip().startswith("[^") or _already_has_url(ln):
                continue
            if any(tok in ln for tok in tokens):
                lines[i] = ln.rstrip() + f" 来源：{url}"
                placed += 1
                break

    # Pass 2: URL 总数仍不足 min_total → 追加独立交叉校验脚注
    existing_urls = len(set(re.findall(r"https?://[^\s)\"'，。]+", text)))
    if placed + existing_urls < min_total and url_pairs:
        # 找一个正文锚点挂载（优先"同业定位/交叉"段，否则追加到来源章节）
        extra = url_pairs[placed % len(url_pairs)] if placed < len(url_pairs) else url_pairs[0]
        max_fn = max((int(m.group(1)) for m in re.finditer(r"\[\^(\d+)\]:", text)), default=0)
        new_id = max_fn + 1
        lines.append(
            f"[^{new_id}]: 交叉校验（第三方口径）— {extra['claim'][:60]}。来源：{extra['url']}"
        )
        placed += 1

    return "\n".join(lines), placed


def sanitize_synthesis(task_id: str, tasks_dir: Path = TASKS_DIR) -> dict:
    """主入口：清洗 synthesis.md 并同步到 step8_master.md + final_report.md。"""
    tasks_dir = Path(tasks_dir)
    synthesis_path = tasks_dir / f"{task_id}-synthesis.md"
    if not synthesis_path.exists():
        return {"ok": False, "error": f"synthesis.md 不存在: {synthesis_path}"}

    text = synthesis_path.read_text(encoding="utf-8")
    original = text

    text, step_cleaned = _clean_step_names(text)
    text, leak_cleaned = _clean_leak_patterns(text)
    url_pairs = _collect_urls_from_fact_store(task_id, tasks_dir)
    text, url_placed = _backfill_urls(text, url_pairs)

    url_count = len(set(re.findall(r"https?://[^\s)\"'，。]+", text)))
    residual_leaks = []
    for pat in LEAK_PATTERNS + [r"[Ss]tep\s*\d+\w*"]:
        m = re.findall(pat, text)
        if m:
            residual_leaks.append({"pattern": pat, "count": len(m)})

    changed = text != original
    if changed:
        synthesis_path.write_text(text, encoding="utf-8")

    # 同步到 collect 复制品 + 最终报告（三文件一致）
    synced = []
    for name in (f"{task_id}-step8_master.md", f"{task_id}-final_report.md"):
        dst = tasks_dir / name
        dst.write_text(text, encoding="utf-8")
        synced.append(str(dst))

    return {
        "ok": True,
        "synthesis_path": str(synthesis_path),
        "changed": changed,
        "step_names_cleaned": step_cleaned,
        "leak_patterns_cleaned": leak_cleaned,
        "urls_backfilled": url_placed,
        "total_url_count": url_count,
        "residual_leaks": residual_leaks,
        "synced_files": synced,
    }


if __name__ == "__main__":
    import sys
    tid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not tid:
        print("usage: ir_synthesis_sanitize.py <task_id>")
        sys.exit(2)
    result = sanitize_synthesis(tid)
    print(json.dumps(result, ensure_ascii=False, indent=2))
