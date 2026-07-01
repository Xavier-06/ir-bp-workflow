#!/usr/bin/env python3
"""PDF → Markdown (marker-pdf, 适合行业报告/白皮书)。

用法:
    python marker_extractor.py input.pdf --output markdown
    python marker_extractor.py input.pdf --output json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def extract_pdf_markdown(pdf_path: str) -> dict:
    """用 marker-pdf 提取 PDF 为 Markdown。"""
    result = {
        "pdf_path": pdf_path,
        "method": "marker",
        "markdown": "",
        "char_count": 0,
    }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                ["marker_single", pdf_path, "--output_dir", tmpdir],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                result["error"] = f"marker exit {proc.returncode}: {proc.stderr[:500]}"
                return result

            # 找输出的 md 文件
            md_files = list(Path(tmpdir).rglob("*.md"))
            if md_files:
                result["markdown"] = md_files[0].read_text(encoding="utf-8")
                result["char_count"] = len(result["markdown"])
            else:
                result["error"] = "marker produced no markdown output"

    except FileNotFoundError:
        result["error"] = "marker not installed. Run: pip install marker-pdf"
    except subprocess.TimeoutExpired:
        result["error"] = "marker timed out (300s)"
    except Exception as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PDF Markdown 提取 (marker)")
    ap.add_argument("pdf_path", help="PDF 文件路径")
    ap.add_argument("--output", choices=["markdown", "json"], default="markdown")
    args = ap.parse_args()

    result = extract_pdf_markdown(args.pdf_path)

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("error"):
            print(f"错误: {result['error']}", file=sys.stderr)
        else:
            print(result["markdown"])
