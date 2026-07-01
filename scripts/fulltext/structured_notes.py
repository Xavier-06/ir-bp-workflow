#!/usr/bin/env python3
"""生成 VC 视角结构化阅读笔记 — 6 维度，~800 字/篇。

用法:
    python structured_notes.py --input extracted_text.txt --metadata '{"title":"...","fact_id":"DOC-001"}' --json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def generate_reading_note(
    text: str,
    metadata: dict[str, Any],
    sub_topic: str = "",
) -> dict:
    """从提取的全文生成 VC 视角结构化笔记。

    6 个维度:
    - tech_contribution: 核心技术贡献
    - problem_addressed: 解决什么问题
    - key_metrics: 性能指标
    - limitations: 局限性
    - comparison_with_sota: 与最优方案对比
    - commercial_readiness: TRL / 产业化阶段
    """
    note = {
        "fact_id": metadata.get("fact_id", ""),
        "doc_id": metadata.get("fact_id", ""),
        "sub_topic": sub_topic,
        "doc_type": metadata.get("type", "paper"),
        "title": metadata.get("title", ""),

        "tech_contribution": "",
        "problem_addressed": "",
        "approach": "",
        "key_metrics": {},
        "limitations": [],
        "comparison_with_sota": "",
        "commercial_readiness": "",
        "key_players": [],

        "full_text_available": bool(text),
        "extraction_method": metadata.get("extraction_method", "unknown"),
        "text_length_chars": len(text),
    }

    if not text:
        note["tech_contribution"] = "(全文不可用，仅基于摘要)"
        note["problem_addressed"] = metadata.get("abstract", "")[:200]
        return note

    # 简单的启发式提取 (实际使用时由子代理 LLM 做)
    # 这里提供骨架，子代理填充内容
    lines = text.split("\n")

    # 尝试提取 abstract / introduction
    abstract_section = ""
    for i, line in enumerate(lines):
        low = line.lower().strip()
        if low.startswith("abstract") or low.startswith("introduction"):
            abstract_section = "\n".join(lines[i:i+10])
            break

    if not abstract_section:
        abstract_section = text[:2000]

    note["tech_contribution"] = "(由子代理 LLM 基于全文提取)"
    note["problem_addressed"] = "(由子代理 LLM 基于全文提取)"
    note["approach"] = "(由子代理 LLM 基于全文提取)"
    note["raw_extract_preview"] = abstract_section[:1000]

    return note


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="结构化阅读笔记生成")
    ap.add_argument("--input", default="-", help="提取文本文件 (- = stdin)")
    ap.add_argument("--metadata", default="{}", help="JSON metadata")
    ap.add_argument("--sub-topic", default="", help="所属 sub_topic")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.input == "-":
        text = sys.stdin.read()
    else:
        with open(args.input) as f:
            text = f.read()

    meta = json.loads(args.metadata)
    note = generate_reading_note(text, meta, sub_topic=args.sub_topic)

    if args.json:
        print(json.dumps(note, ensure_ascii=False, indent=2))
    else:
        print(f"# {note.get('title', 'Unknown')}")
        print(f"  tech_contribution: {note['tech_contribution']}")
        print(f"  problem_addressed: {note['problem_addressed']}")
        print(f"  text_length: {note['text_length_chars']} chars")
