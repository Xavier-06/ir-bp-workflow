#!/usr/bin/env python3
"""PubMed Central API client — 生物医学/材料科学论文搜索 + OA 全文。

端点: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/
限流: 3/s (无 Key), 10/s (有 API Key)
特色: OA 全文 XML/PDF，生物医学覆盖好

用法:
    python pmc_client.py "solid state electrolyte" --max-results 10 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_API_KEY = os.getenv("PMC_API_KEY", "")
REQUEST_TIMEOUT = 15
RATE_LIMIT_SLEEP = 0.4  # ~2.5 req/s (低于 3/s 限制)


def _build_params(**kwargs) -> dict:
    """构建通用参数，加入 API key (如有)。"""
    params = {"retmode": "xml", **kwargs}
    if PMC_API_KEY:
        params["api_key"] = PMC_API_KEY
    return params


def search_pmc(query: str, max_results: int = 10) -> list[dict]:
    """搜索 PMC OA 子集，返回统一格式。"""
    # Step 1: esearch 获取 PMIDs
    params = _build_params(
        db="pmc",
        term=f"({query}) AND (open_access[filter])",
        retmax=max_results + 5,
        sort="relevance",
    )
    try:
        resp = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as e:
        print(f"[PMC] esearch 失败: {e}", file=sys.stderr)
        return []

    id_list = [id_elem.text for id_elem in root.findall(".//Id") if id_elem.text]
    if not id_list:
        return []

    # Step 2: esummary 获取元数据
    time.sleep(RATE_LIMIT_SLEEP)
    params = _build_params(db="pmc", id=",".join(id_list[:max_results + 5]))
    try:
        resp = requests.get(f"{EUTILS_BASE}/esummary.fcgi", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as e:
        print(f"[PMC] esummary 失败: {e}", file=sys.stderr)
        return []

    results = []
    for doc_sum in root.findall(".//DocSum"):
        pmc_id = doc_sum.findtext("Id", "")
        title = ""
        authors = []
        year = None
        doi = ""

        for field in doc_sum.findall("Item"):
            name = field.get("Name", "")
            if name == "Title":
                title = field.text or ""
            elif name == "AuthorList":
                for author in field.findall("Item"):
                    if author.text:
                        authors.append(author.text)
            elif name == "PubDate":
                y = field.text or ""
                if y[:4].isdigit():
                    year = int(y[:4])
            elif name == "DOI":
                doi = field.text or ""

        if not title:
            continue

        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/pdf/" if pmc_id else ""

        results.append({
            "title": title,
            "abstract": "",
            "year": year,
            "venue": "PubMed Central",
            "authors": authors[:10],
            "institutions": [],
            "doi": doi,
            "pmc_id": pmc_id,
            "citation_count": 0,
            "open_access_pdf_url": pdf_url,
            "full_text_available": True,
            "discovery_source": "pmc",
        })

        if len(results) >= max_results:
            break

    return results


def fetch_fulltext_xml(pmc_id: str) -> Optional[str]:
    """获取 PMC 全文 XML (OA 论文)。"""
    params = _build_params(db="pmc", id=pmc_id, rettype="xml")
    try:
        resp = requests.get(f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[PMC] 全文获取失败: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PubMed Central API client")
    ap.add_argument("query", nargs="?", help="搜索关键词")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--pmc-id", default="", help="PMC ID 全文获取")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.pmc_id:
        xml_text = fetch_fulltext_xml(args.pmc_id)
        if xml_text:
            print(xml_text[:5000])
        else:
            print("全文获取失败", file=sys.stderr)
    elif args.query:
        results = search_pmc(args.query, max_results=args.max_results)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"{i}. [{r.get('year', '?')}] {r.get('title', '')}")
                print(f"   PMC: PMC{r.get('pmc_id', '')} | DOI: {r.get('doi', 'N/A')}")
                print()
    else:
        ap.error("需要 query 或 --pmc-id")
