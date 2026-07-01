#!/usr/bin/env python3
"""PDF → 文本 + 表格提取 (pdfplumber, 轻量无需 GPU)。

用法:
    python pdfplumber_extractor.py input.pdf --output text
    python pdfplumber_extractor.py input.pdf --output json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> dict:
    """用 pdfplumber 提取 PDF 文本和表格。"""
    try:
        import pdfplumber
    except ImportError:
        return {"error": "pdfplumber not installed. Run: pip install pdfplumber"}

    result = {
        "pdf_path": pdf_path,
        "method": "pdfplumber",
        "pages_extracted": 0,
        "text": "",
        "tables": [],
        "char_count": 0,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = min(len(pdf.pages), max_pages)
            all_text = []
            all_tables = []

            for i, page in enumerate(pdf.pages[:total_pages]):
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                result["pages_extracted"] = i + 1

                tables = page.extract_tables()
                for table in tables:
                    if table:
                        all_tables.append({
                            "page": i + 1,
                            "rows": len(table),
                            "data": table[:20],  # 限制表格大小
                        })

            result["text"] = "\n\n".join(all_text)
            result["tables"] = all_tables
            result["char_count"] = len(result["text"])

    except Exception as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PDF 文本提取 (pdfplumber)")
    ap.add_argument("pdf_path", help="PDF 文件路径")
    ap.add_argument("--output", choices=["text", "json"], default="text")
    ap.add_argument("--max-pages", type=int, default=50)
    args = ap.parse_args()

    result = extract_pdf_text(args.pdf_path, max_pages=args.max_pages)

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("error"):
            print(f"错误: {result['error']}", file=sys.stderr)
        else:
            print(result["text"])
            print(f"\n--- {result['pages_extracted']} pages, {result['char_count']} chars ---")
