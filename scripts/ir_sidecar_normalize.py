#!/usr/bin/env python3
"""
IR sidecar schema 归一化器（2026-08-06，TASK-20260805-003 实战后新建）

背景：8 个子代理对 facts.json/section.json 契约各写各的变体，曾导致
phase10/11/13 连环报错（6 类 schema 异构 bug）：
  ① facts 根节点是裸 list（期望 {step, facts:[...]} dict）
  ② fact 缺 source_quote
  ③ step3 用 statement 而非 claim
  ④ 定性事件 fact 缺 value（schema 强制要求）
  ⑤ section claims 是 str 数组（期望 dict 数组）
  ⑥ schema_version 写 1.0（合法枚举 ir_section_package.v1）

本模块在 phase09 collect 校验前自动归一，让下游所有消费者只见到标准 schema。
设计原则：只做"等价变换/兜底补全"，不编造内容——无法兜底的字段留空并记录 gap。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

SECTION_SCHEMA_VERSION = "ir_section_package.v1"
FACTS_SCHEMA_VERSION = "1.0"

_CLAIM_KEYS = ("claim", "statement", "title", "text", "content", "description")
_QUOTE_KEYS = ("source_quote", "source_text", "quote", "context", "source")

_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _first_str(d: dict, keys: tuple) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _extract_facts_list(root: Any) -> list:
    """从任意根结构里挖出 facts 列表。"""
    if isinstance(root, list):
        return root
    if isinstance(root, dict):
        for k in ("facts", "items", "data", "entries"):
            v = root.get(k)
            if isinstance(v, list):
                return v
        # 单条 fact dict（无包裹）
        if root.get("claim") or root.get("statement"):
            return [root]
    return []


def _recover_quote_from_md(md_lines: list[str], fact: dict) -> str:
    """从 md 报告原文里找回 fact 的真实出处句（真实数据，非编造）。

    策略：用 fact 的 value（数字）+ claim 关键词在 md 句子里找相似度最高的一句。
    找不到返回空串（交给 traceability gate 拒收，不编造）。
    """
    if not md_lines:
        return ""
    claim = (fact.get("claim") or fact.get("statement") or "").strip()
    value = str(fact.get("value") or "").strip()
    if not claim and not value:
        return ""

    import difflib
    best, best_score = "", 0.0
    claim_tokens = set(t for t in re.split(r"[，。、\s（）()/×≈+\-]+", claim) if len(t) >= 2)
    for sent in md_lines:
        sent = sent.strip()
        if len(sent) < 8:
            continue
        score = 0.0
        if value and value.replace(",", "") in sent.replace(",", ""):
            score += 0.5
        if claim_tokens:
            hit = sum(1 for t in claim_tokens if t in sent)
            score += 0.5 * (hit / len(claim_tokens))
        if score <= 0:
            continue
        # 相似度再校准
        sim = difflib.SequenceMatcher(None, claim, sent[:120]).ratio()
        score += 0.2 * sim
        if score > best_score:
            best_score, best = score, sent
    return best if best_score >= 0.5 else ""


def normalize_fact(fact: Any, idx: int, md_lines: list[str] | None = None) -> tuple[dict, list[str]]:
    """归一单条 fact，返回 (fact, 变更记录)。

    原则（2026-08-06 修正）：只做**结构等价变换与真实数据回填**，不编造。
    - claim 字段别名兜底（statement/title/text 是真实字段）
    - source_quote 从真实出处字段或 **md 报告原文**找回；找不到留空（交给 gate 拒收）
    - value 仅在 claim 完全无数字时标 qualitative（定性事件）；claim 有数字但 value
      为空属于数据录入缺陷，留空让 traceability gate 拒收，**不**从 claim 抽数编造
    """
    changes: list[str] = []
    if not isinstance(fact, dict):
        return {"fact_id": f"AUTO-{idx}", "claim": str(fact), "value": "unparseable",
                "fact_type": "unparsed", "confidence": "low"}, ["coerced_non_dict"]

    f = dict(fact)

    # claim 兜底：statement/title/text/content（真实字段别名）
    if not (f.get("claim") or "").strip():
        alt = _first_str(f, _CLAIM_KEYS)
        if alt:
            f["claim"] = alt
            changes.append("claim_fallback")

    # source_quote：真实出处字段别名 → md 报告原文找回；都找不到留空（不编造）
    if not (f.get("source_quote") or "").strip():
        alt = _first_str(f, _QUOTE_KEYS)
        if alt:
            f["source_quote"] = alt
            changes.append("source_quote_alias")
        else:
            recovered = _recover_quote_from_md(md_lines or [], f)
            if recovered:
                f["source_quote"] = recovered
                changes.append("source_quote_from_md")

    # value：已有则保留；缺失且 claim 无数字 → 定性事件标记（非编造）；
    # claim 有数字但 value 空 → 留空（traceability gate 拒收，暴露子代理录入缺陷）
    if f.get("value") in (None, ""):
        claim = f.get("claim", "")
        if _NUM_RE.search(claim or ""):
            pass  # 有数字却缺 value：留空，让 gate 拒收
        else:
            f["value"] = "qualitative"
            f.setdefault("fact_type", "qualitative_event")
            changes.append("value_qualitative")

    f.setdefault("fact_id", f"AUTO-{idx}")
    f.setdefault("confidence", "medium")
    return f, changes


def _load_md_lines(step_name: str, tasks_dir, task_id: str) -> list[str]:
    """读取该 step 的 md 报告，切成句子，供 source_quote 找回。"""
    try:
        from pathlib import Path
        md = Path(tasks_dir) / f"{task_id}-{step_name}.md"
        if not md.exists():
            return []
        text = md.read_text(encoding="utf-8")
        sents = re.split(r"[。！？\n]", text)
        return [s.strip() for s in sents if len(s.strip()) >= 8]
    except Exception:
        return []


def normalize_facts_sidecar(root: Any, step_name: str,
                            md_lines: list[str] | None = None) -> tuple[dict, list[str]]:
    """归一 facts.json 根结构。返回 (归一后 dict, 变更记录)。"""
    changes: list[str] = []

    facts = _extract_facts_list(root)
    norm_facts, idx = [], 0
    for item in facts:
        nf, ch = normalize_fact(item, idx, md_lines=md_lines)
        norm_facts.append(nf)
        changes.extend(ch)
        idx += 1

    if isinstance(root, dict) and isinstance(root.get("facts"), list):
        # 根结构已标准，只更新 facts 内容
        out = dict(root)
        out["facts"] = norm_facts
    else:
        out = {
            "schema_version": FACTS_SCHEMA_VERSION,
            "step": step_name,
            "retrieved_date": date.today().isoformat(),
            "facts": norm_facts,
        }
        changes.append("root_wrapped")

    out.setdefault("schema_version", FACTS_SCHEMA_VERSION)
    out.setdefault("step", step_name)
    out.setdefault("retrieved_date", date.today().isoformat())
    return out, changes


def normalize_section_sidecar(root: Any, step_name: str) -> tuple[dict, list[str]]:
    """归一 section.json。返回 (归一后 dict, 变更记录)。"""
    changes: list[str] = []
    if not isinstance(root, dict):
        return {
            "schema_version": SECTION_SCHEMA_VERSION,
            "step": step_name,
            "section_title": "",
            "claims": [],
            "facts_used": [],
            "markdown_draft": "",
        }, ["coerced_non_dict"]

    s = dict(root)

    # schema_version 强制合法枚举
    if s.get("schema_version") != SECTION_SCHEMA_VERSION:
        s["schema_version"] = SECTION_SCHEMA_VERSION
        changes.append("schema_version_fixed")

    # section_title 兜底
    if not (s.get("section_title") or "").strip():
        alt = _first_str(s, ("section_title", "title", "name"))
        s["section_title"] = alt
        if alt:
            changes.append("section_title_fallback")

    # claims：str 数组 → dict 数组
    claims = s.get("claims")
    if isinstance(claims, list) and claims:
        norm_claims = []
        for c in claims:
            if isinstance(c, str):
                norm_claims.append({"claim": c, "fact_ids": []})
                changes.append("claims_str_to_dict")
            elif isinstance(c, dict):
                nc = dict(c)
                nc.setdefault("claim", _first_str(nc, _CLAIM_KEYS))
                nc.setdefault("fact_ids", [])
                norm_claims.append(nc)
            else:
                norm_claims.append({"claim": str(c), "fact_ids": []})
                changes.append("claims_coerced")
        s["claims"] = norm_claims
    else:
        s.setdefault("claims", [])

    # facts_used 兜底：claims 的 fact_ids 并集
    if not s.get("facts_used"):
        used = []
        for c in s["claims"]:
            for fid in (c.get("fact_ids") or []):
                if fid not in used:
                    used.append(fid)
        s["facts_used"] = used
        if used:
            changes.append("facts_used_from_claims")

    # markdown_draft 兜底（不编造内容，只用已有字段）
    if not (s.get("markdown_draft") or "").strip():
        alt = _first_str(s, ("markdown_draft", "content", "draft", "md_draft", "body"))
        if not alt:
            # key_messages 列表聚合（step8 曾缺 draft 但 key_messages 完整）
            km = s.get("key_messages")
            if isinstance(km, list) and km:
                parts = [str(x).strip() for x in km if str(x).strip()]
                if parts:
                    alt = "\n\n".join(f"- {p}" for p in parts)
                    changes.append("markdown_draft_from_key_messages")
        s["markdown_draft"] = alt
        if alt:
            changes.append("markdown_draft_fallback")

    s.setdefault("step", step_name)
    s.setdefault("keywords", [])
    return s, changes
