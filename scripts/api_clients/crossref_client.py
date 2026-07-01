#!/usr/bin/env python3
"""Crossref API client — DOI 解析 + 参考文献列表 + 基金资助。

端点: https://api.crossref.org
限流: ~50/s (polite pool, 需 mailto=)
特色: 最权威的 DOI 元数据, reference linking, BibTeX 输出

用法:
    python crossref_client.py "10.1016/j.cossms.2022.101002" --json
    python crossref_client.py --query "solid state battery" --max-results 10 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import requests

CROSSREF_BASE = "https://api.crossref.org"
POLITE_EMAIL = os.getenv("CROSSREF_EMAIL", "xavier@example.com")
REQUEST_TIMEOUT = 15


def _headers() -> dict:
    return {
        "User-Agent": f"LitReviewPipeline/1.0 (mailto:{POLITE_EMAIL})",
    }


def _parse_work(work: dict) -> Optional[dict]:
    """解析 Crossref work 为统一格式。"""
    title_list = work.get("title", [])
    title = title_list[0] if title_list else ""
    if not title:
        return None

    authors = []
    for a in work.get("author", [])[:10]:
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        if name:
            authors.append(name)

    year = None
    date_parts = work.get("published-print", {}).get("date-parts", [[]])
    if not date_parts or not date_parts[0]:
        date_parts = work.get("published-online", {}).get("date-parts", [[]])
    if date_parts and date_parts[0] and date_parts[0][0]:
        year = date_parts[0][0]

    doi = work.get("DOI", "")

    # 参考文献
    references = []
    for ref in work.get("reference", [])[:20]:
        references.append({
            "doi": ref.get("DOI", ""),
            "title": ref.get("article-title", "") or ref.get("unstructured", "")[:200],
            "author": ref.get("author", ""),
            "year": ref.get("year", ""),
        })

    # 基金
    funders = []
    for f in work.get("funder", []):
        funders.append({
            "name": f.get("name", ""),
            "doi": f.get("DOI", ""),
            "awards": f.get("award", []),
        })

    return {
        "title": title,
        "abstract": work.get("abstract", "")[:2000],
        "year": year,
        "venue": work.get("container-title", [""])[0] if work.get("container-title") else "",
        "authors": authors,
        "institutions": [],
        "doi": doi,
        "citation_count": work.get("is-referenced-by-count", 0),
        "references": references,
        "reference_count": work.get("references-count", 0),
        "funders": funders,
        "type": work.get("type", ""),
        "license": work.get("license", []),
        "open_access_pdf_url": "",
        "discovery_source": "crossref",
    }


def get_work_by_doi(doi: str) -> Optional[dict]:
    """通过 DOI 获取元数据。"""
    try:
        resp = requests.get(
            f"{CROSSREF_BASE}/works/{doi}",
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return _parse_work(resp.json().get("message", {}))
    except Exception as e:
        print(f"[Crossref] DOI 查询失败: {e}", file=sys.stderr)
        return None


def search_crossref(query: str, max_results: int = 10) -> list[dict]:
    """搜索 Crossref works。"""
    params = {
        "query": query,
        "rows": max_results + 5,
        "mailto": POLITE_EMAIL,
        "sort": "relevance",
        "order": "desc",
    }
    try:
        resp = requests.get(
            f"{CROSSREF_BASE}/works",
            params=params,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Crossref] 搜索失败: {e}", file=sys.stderr)
        return []

    results = []
    for item in data.get("message", {}).get("items", []):
        parsed = _parse_work(item)
        if parsed:
            results.append(parsed)
            if len(results) >= max_results:
                break

    return results


def get_bibtex(doi: str) -> str:
    """获取 BibTeX 格式引用。"""
    try:
        resp = requests.get(
            f"{CROSSREF_BASE}/works/{doi}",
            headers={"Accept": "application/x-bibtex", **_headers()},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Crossref API client")
    ap.add_argument("doi", nargs="?", help="DOI 查询")
    ap.add_argument("--query", default="", help="关键词搜索")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--bibtex", action="store_true", help="输出 BibTeX")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.doi and not args.query:
        if args.bibtex:
            print(get_bibtex(args.doi))
        else:
            result = get_work_by_doi(args.doi)
            if args.json:
                print(json.dumps([result] if result else [], ensure_ascii=False, indent=2))
            elif result:
                print(f"[{result.get('year', '?')}] {result.get('title', '')}")
                print(f"DOI: {result.get('doi', '')} | 引用: {result.get('citation_count', 0)}")
                refs = result.get("references", [])
                if refs:
                    print(f"\n参考文献 ({result.get('reference_count', 0)} 条):")
                    for r in refs[:5]:
                        print(f"  - {r.get('title', '')[:80]} ({r.get('year', '?')})")
    elif args.query:
        results = search_crossref(args.query, max_results=args.max_results)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
                print(f"   DOI: {r.get('doi', 'N/A')} | 引用: {r.get('citation_count', 0)}")
                print()
    else:
        ap.error("需要 doi 或 --query")
