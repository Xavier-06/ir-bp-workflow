#!/usr/bin/env python3
"""Semantic Scholar API client — 引用图谱 + tldr + SPECTER2。

端点: https://api.semanticscholar.org/graph/v1
限流: 无 Key = 共享限流; 有 Key = 1 RPS
特色: 引用链遍历 (references/citations), tldr, SPECTER2 嵌入

用法:
    python s2_client.py "solid state electrolyte" --max-results 20 --expand-citations --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import requests

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY = os.getenv("S2_API_KEY", "")
REQUEST_TIMEOUT = 15
RATE_LIMIT_SLEEP = 1.1  # 略大于 1s 确保不触发限流


def _headers() -> dict:
    h = {"User-Agent": "LitReviewPipeline/1.0"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


def _parse_paper(paper: dict) -> Optional[dict]:
    """解析 S2 paper 为统一格式。"""
    if not paper.get("title"):
        return None

    oa_url = ""
    oa = paper.get("openAccessPdf") or {}
    if oa.get("url"):
        oa_url = oa["url"]

    external_ids = paper.get("externalIds") or {}

    return {
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", "") or "",
        "year": paper.get("year"),
        "venue": paper.get("venue", ""),
        "authors": [a.get("name", "") for a in (paper.get("authors") or [])[:10]],
        "institutions": [],
        "doi": external_ids.get("DOI", ""),
        "arxiv_id": external_ids.get("ArXiv", ""),
        "corpus_id": paper.get("corpusId"),
        "citation_count": paper.get("citationCount", 0),
        "influential_citation_count": paper.get("influentialCitationCount", 0),
        "open_access_pdf_url": oa_url,
        "tldr": (paper.get("tldr") or {}).get("text", ""),
        "topics": [t.get("name", "") for t in (paper.get("topics") or [])[:5]],
        "fields_of_study": paper.get("fieldsOfStudy", []),
        "s2_paper_id": paper.get("paperId", ""),
        "discovery_source": "s2",
    }


PAPER_FIELDS = (
    "title,abstract,year,venue,authors,citationCount,influentialCitationCount,"
    "openAccessPdf,externalIds,tldr,topics,fieldsOfStudy,corpusId,paperId"
)


def search_s2(query: str, max_results: int = 20, year_from: int = 0) -> list[dict]:
    """搜索 Semantic Scholar。"""
    results = []
    offset = 0
    limit = min(100, max_results)

    while len(results) < max_results:
        params = {
            "query": query,
            "limit": limit,
            "offset": offset,
            "fields": PAPER_FIELDS,
        }
        if year_from:
            params["year"] = f"{year_from}-"

        try:
            resp = requests.get(
                f"{S2_BASE}/paper/search",
                params=params,
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                print("[S2] 限流，等待 5s...", file=sys.stderr)
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[S2] 请求失败: {e}", file=sys.stderr)
            break

        papers = data.get("data", [])
        if not papers:
            break

        for paper in papers:
            parsed = _parse_paper(paper)
            if parsed:
                results.append(parsed)
                if len(results) >= max_results:
                    break

        total = data.get("total", 0)
        offset += limit
        if offset >= total:
            break

        time.sleep(RATE_LIMIT_SLEEP)

    return results


def get_paper(paper_id: str) -> Optional[dict]:
    """获取单篇论文 (支持 S2 ID / DOI / arXiv ID)。"""
    try:
        resp = requests.get(
            f"{S2_BASE}/paper/{paper_id}",
            params={"fields": PAPER_FIELDS},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 429:
            time.sleep(5)
            resp = requests.get(
                f"{S2_BASE}/paper/{paper_id}",
                params={"fields": PAPER_FIELDS},
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        return _parse_paper(resp.json())
    except Exception as e:
        print(f"[S2] 论文查询失败: {e}", file=sys.stderr)
        return None


def get_references(paper_id: str, max_results: int = 20) -> list[dict]:
    """获取某篇论文的参考文献列表 (引用链扩展)。"""
    results = []
    try:
        resp = requests.get(
            f"{S2_BASE}/paper/{paper_id}/references",
            params={"fields": PAPER_FIELDS, "limit": max_results},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 429:
            time.sleep(5)
            return get_references(paper_id, max_results)
        resp.raise_for_status()
        for ref in resp.json().get("data", []):
            cited = ref.get("citedPaper", {})
            parsed = _parse_paper(cited)
            if parsed:
                results.append(parsed)
    except Exception as e:
        print(f"[S2] 参考文献查询失败: {e}", file=sys.stderr)
    return results


def get_citations(paper_id: str, max_results: int = 20) -> list[dict]:
    """获取引用某篇论文的论文列表。"""
    results = []
    try:
        resp = requests.get(
            f"{S2_BASE}/paper/{paper_id}/citations",
            params={"fields": PAPER_FIELDS, "limit": max_results},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 429:
            time.sleep(5)
            return get_citations(paper_id, max_results)
        resp.raise_for_status()
        for cit in resp.json().get("data", []):
            citing = cit.get("citingPaper", {})
            parsed = _parse_paper(citing)
            if parsed:
                results.append(parsed)
    except Exception as e:
        print(f"[S2] 引用查询失败: {e}", file=sys.stderr)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Semantic Scholar API client")
    ap.add_argument("query", nargs="?", help="搜索关键词")
    ap.add_argument("--max-results", type=int, default=20)
    ap.add_argument("--year-from", type=int, default=0)
    ap.add_argument("--paper-id", default="", help="S2/DOI/arXiv ID 查询")
    ap.add_argument("--expand-citations", action="store_true", help="对 top 5 结果扩展引用链")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.paper_id:
        result = get_paper(args.paper_id)
        results = [result] if result else []
    elif args.query:
        results = search_s2(args.query, max_results=args.max_results, year_from=args.year_from)
        if args.expand_citations and results:
            seed_ids = [r.get("s2_paper_id", "") for r in results[:5] if r.get("s2_paper_id")]
            for sid in seed_ids:
                time.sleep(RATE_LIMIT_SLEEP)
                refs = get_references(sid, max_results=5)
                for ref in refs:
                    if not any(r.get("doi") == ref.get("doi") for r in results if ref.get("doi")):
                        results.append(ref)
                time.sleep(RATE_LIMIT_SLEEP)
                cites = get_citations(sid, max_results=5)
                for cit in cites:
                    if not any(r.get("doi") == cit.get("doi") for r in results if cit.get("doi")):
                        results.append(cit)
    else:
        ap.error("需要 query 或 --paper-id")
        sys.exit(1)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            tldr = r.get("tldr", "")[:100]
            print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
            print(f"   引用: {r.get('citation_count', 0)} | DOI: {r.get('doi', 'N/A')}")
            if tldr:
                print(f"   TLDR: {tldr}...")
            print()
