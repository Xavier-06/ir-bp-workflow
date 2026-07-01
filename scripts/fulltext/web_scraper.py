#!/usr/bin/env python3
"""网页报告全文抓取 — HTML → 纯文本。

用法:
    python web_scraper.py "https://example.com/report" --output text
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Optional

import requests

PROXY_URL = "http://127.0.0.1:7897"
REQUEST_TIMEOUT = 30


def scrape_url(url: str, use_proxy: bool = False) -> dict:
    """抓取网页并提取正文。"""
    result = {"url": url, "text": "", "char_count": 0, "title": ""}

    proxies = {"http": PROXY_URL, "https": PROXY_URL} if use_proxy else None
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, proxies=proxies,
                           headers={"User-Agent": "LitReviewPipeline/1.0"})
        resp.raise_for_status()
    except Exception as e:
        result["error"] = str(e)
        return result

    html = resp.text

    # 提取 title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # 去除 script/style/nav/footer
    for tag in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 去除 HTML 标签
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()

    result["text"] = text[:50000]
    result["char_count"] = len(result["text"])
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="网页全文抓取")
    ap.add_argument("url", help="URL")
    ap.add_argument("--output", choices=["text", "json"], default="text")
    ap.add_argument("--proxy", action="store_true", help="使用代理")
    args = ap.parse_args()

    result = scrape_url(args.url, use_proxy=args.proxy)
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("error"):
            print(f"错误: {result['error']}", file=sys.stderr)
        else:
            if result.get("title"):
                print(f"# {result['title']}\n")
            print(result["text"])
            print(f"\n--- {result['char_count']} chars ---")
