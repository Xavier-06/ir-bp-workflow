#!/usr/bin/env python3
"""统一搜索 — 多源并行 → 去重 → 排序 → 输出。

用法:
    python unified_search.py "solid state battery" --sources arxiv,dblp,pmc --max-results 30 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

# 将 bp-workflow 加入 path
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent.parent))


# 各源 signal 硬超时（秒）。必须 >= 对应客户端 REQUEST_TIMEOUT，否则 signal 会在
# 客户端超时前杀掉请求。arXiv 实测延迟可达 ~31s，故给 50s 余量。
_SOURCE_TIMEOUT = {
    "openalex": 20,
    "arxiv": 50,  # arXiv 抖动大（2~31s），客户端 REQUEST_TIMEOUT=45，signal 兜底到 50
    "s2": 20,
    "dblp": 20,
    "pmc": 20,
    "crossref": 20,
}


def _call_source(source: str, query: str, max_results: int, year_from: int) -> list[dict]:
    """单源调用，带超时保护。"""
    import signal

    timeout = _SOURCE_TIMEOUT.get(source, 10)

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"{source} 超时 ({timeout}s)")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        if source == "openalex":
            from scripts.api_clients.openalex_client import search_openalex
            results = search_openalex(query, max_results=max_results, year_from=year_from)
        elif source == "arxiv":
            from scripts.api_clients.arxiv_client import search_arxiv
            results = search_arxiv(query, max_results=max_results)
        elif source == "s2":
            from scripts.api_clients.s2_client import search_s2
            results = search_s2(query, max_results=max_results, year_from=year_from)
        elif source == "dblp":
            from scripts.api_clients.dblp_client import search_dblp
            results = search_dblp(query, max_results=max_results)
        elif source == "pmc":
            from scripts.api_clients.pmc_client import search_pmc
            results = search_pmc(query, max_results=max_results)
        elif source == "crossref":
            from scripts.api_clients.crossref_client import search_crossref
            results = search_crossref(query, max_results=max_results)
        else:
            print(f"[unified] 未知源: {source}", file=sys.stderr)
            return []
        return results
    except TimeoutError:
        print(f"[unified] {source} 超时 ({timeout}s)，跳过", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[unified] {source} 搜索失败: {e}", file=sys.stderr)
        return []
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def search_multi_source(
    query: str,
    sources: list[str],
    max_results: int = 30,
    year_from: int = 0,
) -> list[dict]:
    """对多个学术 API 串行搜索（每源带超时保护），合并去重排序。"""
    all_results = []

    for source in sources:
        results = _call_source(source, query, max_results, year_from)
        all_results.extend(results)

    # 去重
    from scripts.search.dedup import deduplicate
    all_results = deduplicate(all_results)

    # 排序: relevance_score × recency × citation_count
    now_year = time.localtime().tm_year
    for r in all_results:
        year = r.get("year") or now_year
        recency = max(0, 1 - (now_year - year) * 0.05)
        cite_score = min(r.get("citation_count", 0) / 100, 1.0)
        r["composite_score"] = round(recency * 0.4 + cite_score * 0.4 + r.get("relevance_score", 0.5) * 0.2, 3)

    all_results.sort(key=lambda r: r.get("composite_score", 0), reverse=True)
    return all_results[:max_results]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="统一搜索网关")
    ap.add_argument("query", help="搜索关键词")
    ap.add_argument("--sources", default="arxiv,dblp,pmc", help="数据源 (逗号分隔)")
    ap.add_argument("--max-results", type=int, default=30)
    ap.add_argument("--year-from", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",")]
    results = search_multi_source(args.query, sources, max_results=args.max_results, year_from=args.year_from)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"共 {len(results)} 条结果:")
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r.get('year', '?')}] [{r.get('discovery_source', '?')}] {r.get('title', '')}")
            print(f"   引用: {r.get('citation_count', 0)} | DOI: {r.get('doi', 'N/A')} | score: {r.get('composite_score', 0)}")
            print()
