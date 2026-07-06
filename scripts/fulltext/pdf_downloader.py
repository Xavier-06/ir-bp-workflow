#!/usr/bin/env python3
"""按文档类型路由获取全文 — 5 条路径，非降级链。

路径 A: 学术论文 → arXiv PDF / PMC XML / Unpaywall / OA URL
路径 B: 券商研报 → NeoData / WeStock / WebFetch
路径 C: 行业报告 → WebFetch PDF/HTML
路径 D: 公司披露 → SEC EDGAR / TYC
路径 E: 新闻 → WebSearch + WebFetch

用法:
    python pdf_downloader.py --fact-id DOC-001 --type paper --metadata '{"arxiv_id":"2108.10150"}' --output-dir /tmp/pdfs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

REQUEST_TIMEOUT = 30
PROXY_URL = "http://127.0.0.1:7897"


def _download_pdf(url: str, output_path: str, use_proxy: bool = False) -> bool:
    """下载 PDF 文件。"""
    try:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if use_proxy else None
        resp = requests.get(url, timeout=60, stream=True, proxies=proxies,
                           headers={"User-Agent": "LitReviewPipeline/1.0"})
        if resp.status_code == 200 and "pdf" in resp.headers.get("content-type", "").lower():
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return os.path.getsize(output_path) > 1000
    except Exception:
        pass
    return False


def _fetch_text(url: str, use_proxy: bool = False) -> Optional[str]:
    """抓取网页正文。"""
    try:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if use_proxy else None
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, proxies=proxies,
                           headers={"User-Agent": "LitReviewPipeline/1.0"})
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "pdf" in ct.lower():
            return None  # PDF 需要下载处理
        text = resp.text
        # 简单去 HTML 标签
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:50000] if text else None
    except Exception:
        return None


# ── 路径 A: 学术论文 ──────────────────────────────────────

def download_paper(metadata: dict, output_dir: str) -> dict:
    """路径 A: 按标识符选择入口获取论文全文。"""
    result = {"success": False, "method": "", "output_path": ""}

    # A1: arXiv PDF 直下
    arxiv_id = metadata.get("arxiv_id", "")
    if arxiv_id:
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        out = os.path.join(output_dir, f"arxiv_{arxiv_id}.pdf")
        if _download_pdf(url, out, use_proxy=False):
            result.update(success=True, method="arxiv_pdf", output_path=out)
            return result

    # A2: OpenAlex OA URL
    oa_url = metadata.get("open_access_pdf_url", "")
    if oa_url:
        out = os.path.join(output_dir, f"oa_{metadata.get('fact_id', 'unknown')}.pdf")
        if _download_pdf(oa_url, out):
            result.update(success=True, method="openalex_oa", output_path=out)
            return result

    # A3: DOI → Unpaywall
    doi = metadata.get("doi", "")
    if doi:
        try:
            email = os.getenv("UNPAYWALL_EMAIL", "xavier@example.com")
            resp = requests.get(
                f"https://api.unpaywall.org/v2/{doi}?email={email}",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                oa = data.get("best_oa_location") or {}
                pdf_url = oa.get("url_for_pdf") or oa.get("url")
                if pdf_url:
                    out = os.path.join(output_dir, f"unpaywall_{doi.replace('/', '_')}.pdf")
                    if _download_pdf(pdf_url, out):
                        result.update(success=True, method="unpaywall", output_path=out)
                        return result
        except Exception:
            pass

    # A4: PMC 全文
    pmc_id = metadata.get("pmc_id", "")
    if pmc_id:
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/pdf/"
        out = os.path.join(output_dir, f"pmc_{pmc_id}.pdf")
        if _download_pdf(url, out):
            result.update(success=True, method="pmc_pdf", output_path=out)
            return result

    result["method"] = "abstract_only"
    return result


# ── 路径 B: 券商研报 ──────────────────────────────────────

def download_broker_report(metadata: dict, output_dir: str) -> dict:
    """路径 B: NeoData / WeStock / WebFetch。"""
    result = {"success": False, "method": "", "output_path": "", "text": ""}

    # B1: NeoData 研报 (通过 search_gateway)
    title = metadata.get("title", "")
    if title:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
            from scripts.search_gateway import neodata_search
            hits = neodata_search(title, data_type="doc")
            if hits:
                result.update(success=True, method="neodata", text=hits[0].get("content", ""))
                return result
        except Exception:
            pass

    # B2: WebFetch
    url = metadata.get("url", "")
    if url:
        text = _fetch_text(url)
        if text and len(text) > 200:
            result.update(success=True, method="webfetch", text=text[:30000])
            return result

    return result


# ── 路径 C: 行业报告/白皮书 ────────────────────────────────

def download_industry_report(metadata: dict, output_dir: str) -> dict:
    """路径 C: WebFetch PDF/HTML。"""
    result = {"success": False, "method": "", "output_path": "", "text": ""}
    url = metadata.get("url", "")
    if not url:
        return result

    if url.lower().endswith(".pdf"):
        out = os.path.join(output_dir, f"report_{metadata.get('fact_id', 'unknown')}.pdf")
        if _download_pdf(url, out):
            result.update(success=True, method="webfetch_pdf", output_path=out)
            return result

    text = _fetch_text(url)
    if text and len(text) > 200:
        result.update(success=True, method="webfetch_html", text=text[:30000])
    return result


# ── 主入口 ────────────────────────────────────────────────

def download_fulltext(metadata: dict, output_dir: str) -> dict:
    """按 doc type 路由获取全文。"""
    doc_type = metadata.get("type", "paper")
    os.makedirs(output_dir, exist_ok=True)

    if doc_type == "paper":
        return download_paper(metadata, output_dir)
    elif doc_type == "broker_report":
        return download_broker_report(metadata, output_dir)
    elif doc_type in ("industry_report", "news"):
        return download_industry_report(metadata, output_dir)
    else:
        return download_industry_report(metadata, output_dir)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="全文下载路由")
    ap.add_argument("--fact-id", required=True, help="fact ID")
    ap.add_argument("--type", default="paper", choices=["paper", "broker_report", "industry_report", "news"])
    ap.add_argument("--metadata", default="{}", help="JSON metadata")
    ap.add_argument("--output-dir", default="/tmp/lit_fulltext")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    meta = json.loads(args.metadata)
    meta["fact_id"] = args.fact_id
    meta["type"] = args.type

    result = download_fulltext(meta, args.output_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "✅" if result["success"] else "❌"
        print(f"{status} {args.fact_id}: {result.get('method', 'unknown')}")
        if result.get("output_path"):
            print(f"   文件: {result['output_path']}")
        if result.get("text"):
            print(f"   文本: {len(result['text'])} chars")
