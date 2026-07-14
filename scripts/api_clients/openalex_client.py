#!/usr/bin/env python3
"""OpenAlex API client — 学术元数据主力。

端点: https://api.openalex.org/works
限流: 10万次/天 (免费), mailto= 进入 polite pool
特色: 四级领域分类 (Domain→Field→Subfield→Topic), OA URL, 引用数

用法:
    python openalex_client.py "solid state battery" --max-results 30 --json
    python openalex_client.py "固态电池" --max-results 20 --topic-filter "Physical Sciences"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

# 直连绕过代理：沙箱 HTTP(S)_PROXY 端口动态变化且常 refuse，学术源统一走直连更稳
NO_PROXY = {"http": None, "https": None}

# ── 常量 ──────────────────────────────────────────────────
OPENALEX_BASE = "https://api.openalex.org"
POLITE_EMAIL = os.getenv("OPENALEX_EMAIL", "xavier@example.com")
REQUEST_TIMEOUT = 15


def _reconstruct_abstract(inverted_index: dict) -> str:
    """从倒排索引格式还原完整摘要。"""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in word_positions)


def _parse_work(work: dict) -> Optional[dict]:
    """解析单篇 OpenAlex work 为统一格式。"""
    if not work.get("title"):
        return None

    # OA URL
    oa_url = ""
    oa = work.get("best_oa_location") or {}
    if oa.get("url_for_pdf"):
        oa_url = oa["url_for_pdf"]
    elif oa.get("url"):
        oa_url = oa["url"]

    # 机构
    institutions = set()
    for authorship in work.get("authorships", []):
        for inst in authorship.get("institutions", []):
            if inst.get("display_name"):
                institutions.add(inst["display_name"])

    # 领域分类 (四级)
    topics = []
    for topic in work.get("topics", [])[:3]:
        topics.append({
            "domain": topic.get("domain", {}).get("display_name", ""),
            "field": topic.get("field", {}).get("display_name", ""),
            "subfield": topic.get("subfield", {}).get("display_name", ""),
            "topic": topic.get("display_name", ""),
            "score": topic.get("score", 0),
        })

    # DOI
    doi = work.get("doi", "")
    if doi:
        doi = doi.replace("https://doi.org/", "")

    return {
        "title": work.get("title", ""),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index", {})),
        "year": work.get("publication_year"),
        "venue": work.get("primary_location", {}).get("source", {}).get("display_name", ""),
        "authors": [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])[:10]
        ],
        "institutions": list(institutions),
        "doi": doi,
        "citation_count": work.get("cited_by_count", 0),
        "open_access_pdf_url": oa_url,
        "open_access": work.get("open_access", {}).get("is_oa", False),
        "topics": topics,
        "type": work.get("type", ""),
        "cited_by_api": work.get("cited_by_api_url", ""),
        "openalex_id": work.get("id", ""),
        "discovery_source": "openalex",
    }


def search_openalex(
    query: str,
    max_results: int = 30,
    topic_filter: str = "",
    year_from: int = 0,
    per_page: int = 50,
) -> list[dict]:
    """搜索 OpenAlex works，返回统一格式结果列表。"""
    results = []
    cursor = "*"
    collected = 0

    while collected < max_results:
        params = {
            "search": query,
            "per-page": min(per_page, max_results - collected),
            "cursor": cursor,
            "mailto": POLITE_EMAIL,
        }
        if topic_filter:
            params["filter"] = f"topics.domain.display_name:{topic_filter}"
        if year_from:
            params["filter"] = params.get("filter", "") + f",from_publication_date:{year_from}-01-01"

        try:
            resp = requests.get(
                f"{OPENALEX_BASE}/works",
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "LitReviewPipeline/1.0"},
                proxies=NO_PROXY,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[OpenAlex] 请求失败: {e}", file=sys.stderr)
            break

        works = data.get("results", [])
        if not works:
            break

        for work in works:
            parsed = _parse_work(work)
            if parsed:
                results.append(parsed)
                collected += 1
                if collected >= max_results:
                    break

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

        time.sleep(0.1)  # 礼貌间隔

    return results


def get_work_by_doi(doi: str) -> Optional[dict]:
    """通过 DOI 获取单篇 work。"""
    try:
        resp = requests.get(
            f"{OPENALEX_BASE}/works/https://doi.org/{doi}",
            params={"mailto": POLITE_EMAIL},
timeout=REQUEST_TIMEOUT, proxies=NO_PROXY,
            )
        resp.raise_for_status()
        return _parse_work(resp.json())
    except Exception as e:
        print(f"[OpenAlex] DOI 查询失败: {e}", file=sys.stderr)
        return None


def get_work_citations(openalex_id: str, max_results: int = 20) -> list[dict]:
    """获取引用某篇论文的论文列表。"""
    results = []
    try:
        resp = requests.get(
            f"{OPENALEX_BASE}/works",
            params={
                "filter": f"cites:{openalex_id}",
                "per-page": max_results,
                "mailto": POLITE_EMAIL,
                "sort": "cited_by_count:desc",
            },
timeout=REQUEST_TIMEOUT, proxies=NO_PROXY,
            )
        resp.raise_for_status()
        for work in resp.json().get("results", []):
            parsed = _parse_work(work)
            if parsed:
                results.append(parsed)
    except Exception as e:
        print(f"[OpenAlex] 引用查询失败: {e}", file=sys.stderr)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OpenAlex API client")
    ap.add_argument("query", nargs="?", help="搜索关键词")
    ap.add_argument("--max-results", type=int, default=30)
    ap.add_argument("--topic-filter", default="", help="领域过滤 (如 'Physical Sciences')")
    ap.add_argument("--year-from", type=int, default=0, help="起始年份")
    ap.add_argument("--doi", default="", help="DOI 查询 (替代搜索)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if args.doi:
        result = get_work_by_doi(args.doi)
        results = [result] if result else []
    elif args.query:
        results = search_openalex(args.query, max_results=args.max_results,
                                  topic_filter=args.topic_filter, year_from=args.year_from)
    else:
        ap.error("需要 query 或 --doi")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
            print(f"   引用: {r.get('citation_count', 0)} | DOI: {r.get('doi', 'N/A')}")
            if r.get("open_access_pdf_url"):
                print(f"   OA: {r['open_access_pdf_url']}")
            print()
