#!/usr/bin/env python3
"""NeoData 研报搜索封装 — industry_scout 使用。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def search_neodata(query: str, data_type: str = "all") -> list[dict]:
    """封装 search_gateway.neodata_search。"""
    from scripts.search_gateway import neodata_search
    return neodata_search(query, data_type=data_type)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NeoData 研报搜索")
    ap.add_argument("query", help="搜索关键词")
    ap.add_argument("--data-type", choices=["api", "doc", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = search_neodata(args.query, data_type=args.data_type)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r.get('source', 'neodata')}] {r.get('title', '')}")
            print(f"   {r.get('content', '')[:200]}")
            print()
