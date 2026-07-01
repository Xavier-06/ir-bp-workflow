#!/usr/bin/env python3
"""DOI + title fuzzy 去重。"""
from __future__ import annotations

import re
from typing import Any


def _normalize_title(title: str) -> str:
    """标准化标题用于模糊比较。"""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _title_similarity(a: str, b: str) -> float:
    """简单 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    words_a = set(_normalize_title(a).split())
    words_b = set(_normalize_title(b).split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def deduplicate(results: list[dict], similarity_threshold: float = 0.85) -> list[dict]:
    """去重: 精确 DOI 匹配 + title fuzzy match。

    保留 citation_count 最高的版本。
    """
    # Phase 1: DOI 精确去重
    seen_dois: dict[str, int] = {}
    deduped = []
    for r in results:
        doi = r.get("doi", "").lower().strip()
        if doi:
            if doi in seen_dois:
                existing = deduped[seen_dois[doi]]
                if r.get("citation_count", 0) > existing.get("citation_count", 0):
                    deduped[seen_dois[doi]] = r
                continue
            seen_dois[doi] = len(deduped)
        deduped.append(r)

    # Phase 2: Title fuzzy 去重
    final = []
    for r in deduped:
        title = r.get("title", "")
        is_dup = False
        for existing in final:
            if _title_similarity(title, existing.get("title", "")) >= similarity_threshold:
                if r.get("citation_count", 0) > existing.get("citation_count", 0):
                    final.remove(existing)
                    final.append(r)
                is_dup = True
                break
        if not is_dup:
            final.append(r)

    return final


if __name__ == "__main__":
    import json, sys
    data = json.load(sys.stdin) if not sys.stdin.isatty() else []
    result = deduplicate(data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
