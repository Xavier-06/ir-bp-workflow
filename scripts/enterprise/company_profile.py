#!/usr/bin/env python3
"""统一公司画像生成 — 合并 TYC + SEC + WebSearch 数据。

输出格式匹配设计文档中 enterprise_scout-facts.json 的 company_profile schema。

用法:
    python company_profile.py "QuantumScape" --sources tyc,sec,web --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Optional


def build_company_profile(
    company_name: str,
    sources: list[str],
    tyc_data: dict | None = None,
    sec_data: dict | None = None,
    web_data: dict | None = None,
) -> dict:
    """合并多源数据生成统一公司画像。

    sources: 启用的数据源列表 (tyc/sec/web)
    tyc_data/sec_data/web_data: 预查询的数据 (子代理运行时传入)
    """
    profile = {
        "fact_id": f"ENT-{company_name[:3].upper()}",
        "type": "company_profile",
        "company_name": company_name,
        "founded": None,
        "hq": "",
        "stage": "",
        "total_funding": "",
        "key_investors": [],
        "tech_route": "",
        "patent_count": 0,
        "key_patents": [],
        "partnerships": [],
        "management": {},
        "latest_milestone": "",
        "risks": [],
        "relevance": "",
        "data_sources_used": sources,
        "profiled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # 合并 TYC 数据
    if tyc_data and "tyc" in sources:
        profile["founded"] = tyc_data.get("founded")
        profile["hq"] = tyc_data.get("hq", "")
        profile["patent_count"] = tyc_data.get("patent_count", 0)
        profile["key_patents"] = tyc_data.get("key_patents", [])
        profile["management"] = tyc_data.get("management", {})
        profile["risks"] = tyc_data.get("risks", [])

    # 合并 SEC 数据
    if sec_data and "sec" in sources:
        profile["stage"] = sec_data.get("stage", profile["stage"])
        profile["total_funding"] = sec_data.get("market_cap", profile["total_funding"])
        profile["risks"] = list(set(profile["risks"] + sec_data.get("risks", [])))

    # 合并 Web 数据
    if web_data and "web" in sources:
        profile["total_funding"] = web_data.get("total_funding", profile["total_funding"])
        profile["key_investors"] = web_data.get("key_investors", profile["key_investors"])
        profile["partnerships"] = web_data.get("partnerships", profile["partnerships"])
        profile["latest_milestone"] = web_data.get("latest_milestone", "")
        profile["tech_route"] = web_data.get("tech_route", "")

    return profile


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="公司画像生成")
    ap.add_argument("company_name", help="公司名称")
    ap.add_argument("--sources", default="tyc,sec,web", help="数据源 (逗号分隔)")
    ap.add_argument("--tyc-data", default="{}", help="TYC 数据 JSON")
    ap.add_argument("--sec-data", default="{}", help="SEC 数据 JSON")
    ap.add_argument("--web-data", default="{}", help="Web 数据 JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",")]
    profile = build_company_profile(
        args.company_name, sources,
        tyc_data=json.loads(args.tyc_data),
        sec_data=json.loads(args.sec_data),
        web_data=json.loads(args.web_data),
    )

    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print(f"# {profile['company_name']} 公司画像")
        print(f"  成立: {profile.get('founded', '?')} | 总部: {profile.get('hq', '?')}")
        print(f"  阶段: {profile.get('stage', '?')} | 融资: {profile.get('total_funding', '?')}")
        print(f"  专利: {profile.get('patent_count', 0)} | 数据源: {', '.join(sources)}")
