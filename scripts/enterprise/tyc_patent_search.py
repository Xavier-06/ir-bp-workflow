#!/usr/bin/env python3
"""TYC 专利检索 — enterprise_scout 使用。

通过 TYC MCP 工具查询公司专利组合。

用法:
    python tyc_patent_search.py "QuantumScape" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def search_patents(company_name: str) -> dict:
    """检索公司专利。

    实际运行时通过 TYC MCP 调用:
    - mcp__tyc-ipr__get_patent_info: 专利检索
    """
    return {
        "company_name": company_name,
        "lookup_method": "tyc_mcp",
        "mcp_tools": {
            "patent_search": "mcp__tyc-ipr__get_patent_info",
            "software_copyright": "mcp__tyc-ipr__get_software_copyright_info",
            "trademark": "mcp__tyc-ipr__get_trademark_info",
        },
        "note": "需要通过 TYC MCP connector (tyc-ipr) 调用。",
        "data": {},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TYC 专利检索")
    ap.add_argument("company_name", help="公司名称")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = search_patents(args.company_name)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"公司: {result['company_name']}")
        print(f"专利检索 MCP: {result['mcp_tools']['patent_search']}")
