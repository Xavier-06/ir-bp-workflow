#!/usr/bin/env python3
"""CORE API client — 全球最大 OA 全文聚合。

端点: https://api.core.ac.uk/v3
限流: 10 req/10s (需 API Key)
特色: OA 全文 PDF 下载 + 全文搜索

用法:
    python core_client.py "solid state battery" --max-results 10 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import requests

CORE_BASE = "https://api.core.ac.uk/v3"
CORE_API_KEY = os.getenv("CORE_API_KEY", "")
REQUEST_TIMEOUT = 15
RATE_LIMIT_SLEEP = 1.1  # 10 req / 10s = 1 req/s


def _headers() -> dict:
    h = {"User-Agent": "LitReviewPipeline/1.0"}
    if CORE_API_KEY:
        h["Authorization"] = f"Bearer {CORE_API_KEY}"
    return h


def _parse_work(work: dict) -> Optional[dict]:
    """解析 CORE work 为统一格式。"""
    title = work.get("title", "")
    if not title:
        return None

    authors = [a.get("name", "") for a in (work.get("authors") or [])[:10]]
    year = work.get("yearPublished")

    pdf_url = ""
    if work.get("downloadUrl"):
        pdf_url = work["downloadUrl"]

    doi = ""
    for ident in work.get("identifiers", []):
        if isinstance(ident, str) and ident.startswith("doi:"):
            doi = ident[4:]
            break

    return {
        "title": title,
        "abstract": (work.get("abstract") or "")[:2000],
        "year": year,
        "venue": work.get("publisher", ""),
        "authors": authors,
        "institutions": [],
        "doi": doi,
        "citation_count": 0,
        "open_access_pdf_url": pdf_url,
        "full_text_available": bool(pdf_url),
        "core_id": work.get("id", ""),
        "source_fulltext_urls": work.get("sourceFulltextUrls", []),
        "discovery_source": "core",
    }


def search_core(query: str, max_results: int = 10) -> list[dict]:
    """搜索 CORE。需要 API Key。"""
    if not CORE_API_KEY:
        print("[CORE] ⚠️ 未设置 CORE_API_KEY，跳过搜索", file=sys.stderr)
        return []

    params = {"q": query, "limit": max_results + 5}
    try:
        resp = requests.get(
            f"{CORE_BASE}/search/works",
            params=params,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[CORE] 请求失败: {e}", file=sys.stderr)
        return []

    results = []
    for work in data.get("results", []):
        parsed = _parse_work(work)
        if parsed:
            results.append(parsed)
            if len(results) >= max_results:
                break

    return results


def download_pdf(core_id: str, output_path: str) -> bool:
    """下载 OA 全文 PDF。"""
    if not CORE_API_KEY:
        print("[CORE] ⚠️ 未设置 CORE_API_KEY", file=sys.stderr)
        return False
    try:
        resp = requests.get(
            f"{CORE_BASE}/articles/get/{core_id}/download/pdf",
            headers=_headers(),
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"[CORE] PDF 下载失败: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CORE API client")
    ap.add_argument("query", nargs="?", help="搜索关键词")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--download", default="", help="CORE ID → 下载 PDF")
    ap.add_argument("--output", default="core_download.pdf", help="PDF 输出路径")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.download:
        ok = download_pdf(args.download, args.output)
        print(f"下载{'成功' if ok else '失败'}: {args.output}")
    elif args.query:
        results = search_core(args.query, max_results=args.max_results)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
                print(f"   DOI: {r.get('doi', 'N/A')} | PDF: {'✅' if r.get('open_access_pdf_url') else '❌'}")
                print()
    else:
        ap.error("需要 query 或 --download")
