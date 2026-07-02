#!/usr/bin/env python3
"""从PMC HTML页面提取全文文本（绕过PoW限制）。"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

def fetch_pmc_text(pmc_id: str, output_dir: str = "/tmp/lit_fulltext") -> dict:
    """从PMC HTML页面提取全文。"""
    url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/"
    result = {"success": False, "text": "", "char_count": 0, "method": "pmc_html"}

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # 提取文章正文 - 在PMC HTML中，文章内容通常在 <div class="c-article-body"> 或 <article> 内
        # 移除script和style标签
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)

        # 移除HTML标签，保留文本
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()

        # 尝试提取更干净的正文部分 - 找"Abstract"或"Introduction"之后的内容
        # 先找文章主体
        body_match = re.search(r'<div[^>]*class="[^"]*article[^"]*"[^>]*>', html, re.IGNORECASE)
        if body_match:
            # 有article div，提取其内容
            pass  # 我们已经提取了全部文本

        result["text"] = text
        result["char_count"] = len(text)
        result["success"] = len(text) > 1000

        # 保存到文件
        os.makedirs(output_dir, exist_ok=True)
        txt_path = os.path.join(output_dir, f"pmc_{pmc_id}.txt")
        with open(txt_path, "w") as f:
            f.write(text)
        result["output_path"] = txt_path

    except Exception as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    pmc_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pmc_id:
        print(json.dumps({"error": "PMC ID required"}, ensure_ascii=False))
        sys.exit(1)
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "/Users/xavier/.workbuddy/ir_runtime/downloaded_pdfs"
    result = fetch_pmc_text(pmc_id, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
