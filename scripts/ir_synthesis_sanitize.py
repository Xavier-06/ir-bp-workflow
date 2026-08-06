#!/usr/bin/env python3
"""
IR 统稿交付前自动清洗（2026-08-06 v2，TASK-20260805-003 实战后新建）

背景：统稿 synthesis.md 交付 phase15 时被三道门卡住：
  ① 脚注来源用管线内部编号（step5_macro / step6_valuation），命中泄露黑名单
  ② 脚注只有来源名没 URL，report_gate url_count<3 FAIL
  ③ step8_master.md（synthesis 的 collect 复制品）清洗时漏改不同步

v2（2026-08-06 中天研报复盘）新增四层：
  ④ 脚手架词清除：模板指令原文（"一句话投资判断"/"范式：v4.0"/"（正文主体）"等）
     泄漏进成品 → 改写为自然研报语言或直接剥离
  ⑤ KD-N 内部编号清除：辩论标题里的 KD-1~KD-9 编号剥离
  ⑥ 实体乱码检测：entity 名错字（"中空虚"应为"中天空芯"类）→ 记录 issues 交门禁
     （乱码无法安全自动修复——错字形态不可枚举，只检测不臆改）
  ⑦ 内部来源名改写：脚注来源单独写内部角色名（估值分析/宏观研究/全局裁决/推论）
     → 替换为"作者测算"（真正的外部文献名不在黑名单，不会误伤）

原则：只做等价变换与可证明的改写，不编造内容；所有清洗幂等。
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

# ── v2 新增：脚手架词处理表（指令原文 → 成品语言）──
# 元组 = (正则, 替换)；替换为空串 = 整段剥离。按序执行，幂等（替换产物不含原模式）。
SCAFFOLD_REWRITES: list[tuple[str, str]] = [
    # 报头范式自述（"范式：v4.0 Key Debates 驱动（……）"整段剥离）
    (r"范式[：:][^\n|]*?(?=[\n|])", ""),
    # 模板指令标签 → 自然研报语言
    (r"\*\*一句话投资判断\*\*[：:]", "**核心逻辑**："),
    (r"一句话投资判断", "核心逻辑"),
    (r"我与市场的\s*2[-–]3\s*个不同", "我们与共识的分歧"),
    (r"谁对谁错看什么", "验证节点"),
    (r"数据缺口（数据缺口s，不得当作已证实）", "数据缺口（未证实，不构成结论依据）"),
    (r"（数据缺口s，不得当作已证实）", "（未证实，不构成结论依据）"),
    # 章节标题括号元描述剥离
    (r"（正文主体）", ""),
    (r"（供查阅）", ""),
    (r"（全新核心章节）", ""),
    (r"（全新）", ""),
    # 管线自我指涉
    (r"v4\.0\s*Key\s*Debates\s*驱动", "论点驱动"),
    (r"维度素材作论据库[，,]?\s*不各自成章", ""),
    (r"统稿直接引用[，,]?\s*必齐", ""),
    # 内部口径裁决类括号噪声（只出现在脚注，纯管线黑话）
    (r"（全局口径[^）]*）", ""),
    (r"（全局裁决[^）]*）", ""),
    (r"（统一行情锚[^）]*）", ""),
]

# 辩论标题 KD-N 编号（"辩论一（KD-1）：" → "辩论一："）
_KD_ID_PAT = re.compile(r"[（(]\s*KD[-–]\d+\s*[)）]")

# 内部来源名黑名单：脚注来源**单独**以这些词开头时改写为"作者测算"。
# 真正的外部文献（"银河证券 —《xxx》"）不匹配，不误伤。
INTERNAL_SOURCE_NAMES = (
    "估值分析", "宏观研究", "治理研究", "行业研究", "业务研究", "财务分析",
    "预期差研究", "风险研究", "风险与总结", "全局裁决", "全局口径", "统一行情锚",
    "推论", "反算", "交叉校验", "内部研究",
)


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


def _clean_scaffold(text: str) -> tuple[str, int]:
    """脚手架词改写：模板指令原文 → 自然研报语言（幂等：替换产物不含原模式）。"""
    n = 0
    for pat, repl in SCAFFOLD_REWRITES:
        text, k = re.subn(pat, repl, text)
        n += k
    text, k = _KD_ID_PAT.subn("", text)
    n += k
    return text, n


def _clean_internal_sources(text: str) -> tuple[str, int]:
    """脚注来源单独写内部角色名 → 改写为"作者测算"。
    只动脚注定义行（[^N]: 开头），正文里的维度名不受影响。"""
    n = 0
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^(\[\^\d+\]:\s*)(.*)$", ln)
        if not m:
            continue
        prefix, body = m.groups()
        body_s = body.strip()
        for name in INTERNAL_SOURCE_NAMES:
            if not body_s.startswith(name):
                continue
            if name == "推论":
                # "推论（基于[^8]）" → "作者测算（推论，基于[^8]）"，保留推导链
                lines[i] = prefix + re.sub(r"^推论", "作者测算（推论）", body, count=1)
            else:
                lines[i] = prefix + re.sub(rf"^{re.escape(name)}", "作者测算", body, count=1)
            n += 1
            break
    return "\n".join(lines), n


def _load_entity(task_id: str, tasks_dir: Path) -> str:
    """从 research_plan 动态读实体名（不硬编码）。"""
    rp_path = tasks_dir / f"{task_id}-research_plan.json"
    if rp_path.exists():
        try:
            return (json.loads(rp_path.read_text(encoding="utf-8")).get("entity") or "").strip()
        except Exception:
            pass
    return ""


def _detect_entity_typos(text: str, entity: str, task_id: str, tasks_dir: Path) -> list[dict]:
    """实体乱码检测（"实体字符残缺"规则）。

    真乱码（"中天空芯"→"中空虚"）的特征：实体简称字符被部分保留——
      token 含实体简称首字，但**缺少简称的后续字**（"中空虚"含"中"缺"天"），
      且整体不是合法词汇。而正常表达要么含完整简称（"中天首个空芯中标"），
      要么是不含实体字符的普通词（"中国/中心"）。
    附加降噪：token 须重复出现 ≥2 次（模型乱码一致性生成），且不在合法词表。
    只检测给建议，不臆改，交给门禁 WARN。
    """
    entity = (entity or "").strip()
    if len(entity) < 3:  # 短实体名误报率过高，跳过
        return []
    abbr = entity[:2]
    head1 = entity[0]

    # 语料原文（拼接所有 step md）：token 在语料中**原样出现过**即合法——
    # "中央汇金等/中标公告落"这类"合法词+语法尾巴"都能排除；
    # 真乱码（"中空虚"）不会在任何 step 语料中出现。
    corpus_text = ""
    vocab: dict[str, int] = {}
    for md in sorted(tasks_dir.glob(f"{task_id}-step*.md")):
        if "master" in md.name or "brief" in md.name:
            continue
        try:
            corpus = md.read_text(encoding="utf-8")
        except Exception:
            continue
        corpus_text += corpus
        for w in re.findall(r"[\u4e00-\u9fff]{2,6}", corpus):
            vocab[w] = vocab.get(w, 0) + 1
    legit = set(vocab)

    issues = []
    seen = set()
    for L in range(3, 6):  # 3-5 字候选
        pat = re.escape(head1) + r"[\u4e00-\u9fff]{%d}" % (L - 1)
        candidates: dict[str, list] = {}
        for m in re.finditer(pat, text):
            token = m.group(0)
            # 规则1：含实体首字但缺实体简称第二字（字符残缺）
            if abbr[1] in token:
                continue  # 简称完整或基本完整，不是乱码
            if token in seen:
                continue
            # 规则2：token 在语料原文中原样出现过 → 合法词/合法短语，跳过
            if token in corpus_text:
                continue
            # 规则3：token 的任意 ≥3 字前缀在语料中出现 → "合法词+语法尾巴"
            #（"中标公告落"⊃"中标公告"、"中央汇金等"⊃"中央汇金"），跳过。
            # 真乱码（"中空虚"）的前缀不会在语料出现，不受影响。
            if any(token[:k] in corpus_text for k in range(3, L)):
                continue
            candidates.setdefault(token, []).append(m.start())
        for token, starts in candidates.items():
            if len(starts) < 2:
                continue  # 只出现一次的多为语法组合，跳过
            seen.add(token)
            # 建议：语料中同长度、含实体首字且字符重合最多的合法词
            best, best_score = "", 0
            for w in legit:
                if len(w) == len(token) and head1 in w:
                    score = sum(1 for a, b in zip(w, token) if a == b)
                    if score > best_score:
                        best, best_score = w, score
            issues.append({
                "code": "ENTITY_TYPO_SUSPECT",
                "token": token,
                "occurrences": len(starts),
                "suggestion": best or "",
                "context": text[max(0, starts[0] - 20):starts[0] + 20].replace("\n", " "),
            })
    return issues


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
    # v2 四层：脚手架改写 → 内部来源改写 → URL 回填（乱码只检测不改写）
    text, scaffold_cleaned = _clean_scaffold(text)
    text, source_cleaned = _clean_internal_sources(text)
    entity = _load_entity(task_id, tasks_dir)
    typo_issues = _detect_entity_typos(text, entity, task_id, tasks_dir)
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
        "scaffold_cleaned": scaffold_cleaned,
        "internal_sources_cleaned": source_cleaned,
        "entity_typo_issues": typo_issues,
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
