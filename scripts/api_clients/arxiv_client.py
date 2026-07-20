#!/usr/bin/env python3
"""arXiv API client — CS/AI/Tech 预印本搜索 + PDF 直下。

端点: https://export.arxiv.org/api/query
限流: 建议 3s 间隔
特色: PDF 直下 (arxiv.org/pdf/{id}.pdf), 无需认证
注意: 已配置 https 直连 (proxies={"http": None, "https": None}) 绕过代理。
      沙箱 HTTP(S)_PROXY 端口动态变化且常拒绝连接，故学术源统一走直连更稳。
      请勿加 --noproxy（脚本无此参数，且直连已隐含绕过代理）。

用法:
    python arxiv_client.py '"solid state battery"' --categories cs,cond-mat --max-results 20 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import quote

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 直连绕过代理：沙箱 HTTP(S)_PROXY 端口动态变化且常 refuse，学术源统一走直连更稳
NO_PROXY = {"http": None, "https": None}

# [P0 修复 2026-07-14] arXiv 429 限流根因：requests.get 缺 User-Agent 且无重试。
# 构造带 backoff 重试 + 合规 UA 的 session，search/get 两处共用。
_UA = "ir-coordinator-lit/1.0 (automated literature review; mailto:research-pipeline@example.com)"
_RETRY = Retry(
    total=4,
    backoff_factor=2.0,           # 退避 2/4/8/16s，覆盖 arXiv 429 抖动
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
    raise_on_status=False,
)
_SESSION = requests.Session()
_SESSION.proxies = NO_PROXY
_SESSION.headers.update({"User-Agent": _UA})
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY))
_SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY))

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF_BASE = "https://arxiv.org/pdf"
# arXiv export API 响应抖动极大（实测 2s~31s 不等），客户端超时须留足余量；
# 必须 < unified_search 里 _SOURCE_TIMEOUT["arxiv"]（signal 硬超时），否则 signal 会提前杀请求。
REQUEST_TIMEOUT = 45
RATE_LIMIT_SLEEP = 3.0

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_entry(entry: ET.Element) -> Optional[dict]:
    """解析单篇 arXiv entry 为统一格式。"""
    title = entry.findtext("atom:title", "", NS).strip().replace("\n", " ")
    if not title:
        return None

    arxiv_id_raw = entry.findtext("atom:id", "", NS)
    arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw
    arxiv_id = arxiv_id.split("v")[0] if "v" in arxiv_id and arxiv_id[-1].isdigit() else arxiv_id

    authors = [a.findtext("atom:name", "", NS) for a in entry.findall("atom:author", NS)]
    categories = [c.get("term", "") for c in entry.findall("atom:category", NS)]
    published = entry.findtext("atom:published", "", NS)[:10]
    year = int(published[:4]) if published[:4].isdigit() else None

    doi = ""
    for link in entry.findall("arxiv:doi", NS):
        doi = link.text or ""
    if not doi:
        doi_link = entry.findtext("arxiv:doi", "", NS)
        if doi_link:
            doi = doi_link

    pdf_url = f"{ARXIV_PDF_BASE}/{arxiv_id}.pdf"

    return {
        "title": title,
        "abstract": entry.findtext("atom:summary", "", NS).strip().replace("\n", " ")[:2000],
        "year": year,
        "venue": "arXiv",
        "authors": authors[:10],
        "institutions": [],
        "doi": doi,
        "arxiv_id": arxiv_id,
        "citation_count": 0,
        "open_access_pdf_url": pdf_url,
        "full_text_available": True,
        "categories": categories,
        "published": published,
        "discovery_source": "arxiv",
    }


def search_arxiv(
    query: str,
    max_results: int = 20,
    categories: list[str] | None = None,
    sort_by: str = "relevance",
) -> list[dict]:
    """搜索 arXiv，返回统一格式结果列表。

    categories: 过滤分类 (如 ["cs.AI", "cond-mat.mtrl-sci"])
    """
    search_query = query
    if categories:
        cat_filter = " OR ".join(f"cat:{c}" for c in categories)
        search_query = f"({query}) AND ({cat_filter})"

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": min(max_results + 10, 100),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }

    try:
        # 遵守 arXiv 3s 间隔限流，连续调用前主动让出，避免触发 429
        time.sleep(RATE_LIMIT_SLEEP)
        resp = _SESSION.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"[arXiv] 请求失败: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[arXiv] XML 解析失败: {e}", file=sys.stderr)
        return []

    results = []
    for entry in root.findall("atom:entry", NS):
        parsed = _parse_entry(entry)
        if parsed:
            results.append(parsed)
            if len(results) >= max_results:
                break

    return results


def get_paper_by_id(arxiv_id: str) -> Optional[dict]:
    """通过 arXiv ID 获取单篇论文。"""
    params = {"id_list": arxiv_id}
    try:
        time.sleep(RATE_LIMIT_SLEEP)
        resp = _SESSION.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", NS)
        if entries:
            return _parse_entry(entries[0])
    except Exception as e:
        print(f"[arXiv] ID 查询失败: {e}", file=sys.stderr)
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="arXiv API client")
    ap.add_argument("query", nargs="?", help="搜索关键词 (引号包裹精确搜索)")
    ap.add_argument("--max-results", type=int, default=20)
    ap.add_argument("--categories", default="", help="分类过滤 (逗号分隔: cs,cond-mat)")
    ap.add_argument("--sort-by", choices=["relevance", "lastUpdatedDate", "submittedDate"], default="relevance")
    ap.add_argument("--id", default="", help="arXiv ID 查询 (替代搜索)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if args.id:
        result = get_paper_by_id(args.id)
        results = [result] if result else []
    elif args.query:
        cats = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None
        results = search_arxiv(args.query, max_results=args.max_results, categories=cats, sort_by=args.sort_by)
    else:
        ap.error("需要 query 或 --id")
        sys.exit(1)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            cats = ", ".join(r.get("categories", [])[:3])
            print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
            print(f"   arXiv: {r.get('arxiv_id', '')} | 分类: {cats}")
            print(f"   PDF: {r.get('open_access_pdf_url', '')}")
            print()
