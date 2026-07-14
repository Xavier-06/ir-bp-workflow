#!/usr/bin/env python3
"""DBLP API client — CS 领域论文补充检索。

端点: https://dblp.org/search/publ/api
限流: 宽松，无需认证
特色: CS 会议/期刊覆盖好，返回 DOI + EE 链接

用法:
    python dblp_client.py "solid state battery" --max-results 10 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import requests

# 直连绕过代理：沙箱 HTTP(S)_PROXY 端口动态变化且常 refuse，学术源统一走直连更稳
NO_PROXY = {"http": None, "https": None}

DBLP_API = "https://dblp.org/search/publ/api"
REQUEST_TIMEOUT = 15


def _parse_hit(hit: dict) -> Optional[dict]:
    """解析单条 DBLP hit。"""
    info = hit.get("info", {})
    if not info.get("title"):
        return None

    title = info.get("title", "")
    if isinstance(title, list):
        title = title[0] if title else ""

    authors_raw = info.get("authors", {}).get("author", [])
    if isinstance(authors_raw, dict):
        authors_raw = [authors_raw]
    authors = []
    for a in authors_raw[:10]:
        if isinstance(a, dict):
            authors.append(a.get("text", ""))
        else:
            authors.append(str(a))

    year = info.get("year")
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    else:
        year = None

    ee = info.get("ee", "")
    if isinstance(ee, list):
        ee = ee[0] if ee else ""

    doi = info.get("doi", "")

    return {
        "title": title,
        "abstract": "",
        "year": year,
        "venue": info.get("venue", ""),
        "authors": authors,
        "institutions": [],
        "doi": doi,
        "ee_url": ee,
        "citation_count": 0,
        "open_access_pdf_url": "",
        "dblp_key": info.get("key", ""),
        "type": info.get("type", ""),
        "discovery_source": "dblp",
    }


def search_dblp(query: str, max_results: int = 10) -> list[dict]:
    """搜索 DBLP。"""
    params = {
        "q": query,
        "format": "json",
        "h": max_results + 5,
    }
    try:
        resp = requests.get(DBLP_API, params=params, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DBLP] 请求失败: {e}", file=sys.stderr)
        return []

    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    results = []
    for hit in hits:
        parsed = _parse_hit(hit)
        if parsed:
            results.append(parsed)
            if len(results) >= max_results:
                break
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DBLP API client")
    ap.add_argument("query", help="搜索关键词")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = search_dblp(args.query, max_results=args.max_results)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
            print(f"   venue: {r.get('venue', '')} | DOI: {r.get('doi', 'N/A')}")
            print()
