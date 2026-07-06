#!/usr/bin/env python3
"""TYC 公司信息查询 — enterprise_scout 使用。

通过 TYC MCP 工具查询公司工商信息、股东、高管、融资。
注意: 实际运行时需要 TYC MCP connector 已连接。

用法:
    python tyc_company_lookup.py "卫蓝新能源" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def lookup_company(company_name: str) -> dict:
    """查询公司基本信息。

    实际运行时通过 TYC MCP 调用:
    - get_company_by_query: 搜索公司
    - get_company_registration_info: 工商信息
    - get_shareholder_info: 股东信息
    - get_key_personnel: 高管信息
    - get_external_investments: 对外投资
    """
    return {
        "company_name": company_name,
        "lookup_method": "tyc_mcp",
        "note": "需要通过 TYC MCP connector 调用。"
                "子代理运行时通过 mcp__tyc-company__get_company_by_query 等工具直接调用。",
        "mcp_tools": {
            "search": "mcp__tyc-company__get_company_by_query",
            "registration": "mcp__tyc-company__get_company_registration_info",
            "shareholders": "mcp__tyc-company__get_shareholder_info",
            "key_personnel": "mcp__tyc-company__get_key_personnel",
            "investments": "mcp__tyc-company__get_external_investments",
            "financial": "mcp__tyc-company__get_financial_data",
        },
        "data": {},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TYC 公司信息查询")
    ap.add_argument("company_name", help="公司名称")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = lookup_company(args.company_name)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"公司: {result['company_name']}")
        print(f"方法: {result['lookup_method']}")
        print(f"MCP 工具: {json.dumps(result['mcp_tools'], ensure_ascii=False)}")
