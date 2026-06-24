#!/usr/bin/env python3
"""
BP/IR 通用估值补充器 — 统一入口（PR1）。

把 IR 专用 `tasks.valuation_enricher.enrich_with_yahoo` 包成 BP 子代理
（包括主控脚本 `bp_company_verify` / BP role 子代理）也能直接 import 的形式。

行为：
  - A/HK 股：内部走 NeoData 优先 + yfinance 交叉验证
  - 美股：内部走 yfinance
  - 价格差异 >5% 自动写 `price_warning`
  - 失败/非上市公司 → 返回空 dict，不抛异常

激活场景：
  - `scripts/bp_company_verify.py` 主控验证层补估值字段
  - `scripts/bp_subagent_launcher_wb.py` 子代理 brief 调用
  - IR 端 `ir_company_verify.py` 继续走 `tasks.valuation_enricher`（不破坏现有 import）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"

# 确保 `tasks/valuation_enricher` 可被 import
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tasks.valuation_enricher import enrich_with_yahoo  # type: ignore
    _ENRICHER_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    enrich_with_yahoo = None  # type: ignore
    _ENRICHER_AVAILABLE = False
    _IMPORT_ERROR = str(e)


def enrich_valuation(entity: str, market: str = "auto") -> dict[str, Any]:
    """统一估值补充接口。

    Args:
        entity: 公司中文名 / A股代码 / 港股代码 / 美股代码
        market: 'auto' | 'cn' | 'hk' | 'us'
                auto 时由内部 `_is_a_hk_stock` 决定 NeoData 优先级

    Returns:
        dict with keys: ticker / price / currency / pe_ratio / ps_ratio / pb_ratio /
                        market_cap / 52w_high / 52w_low / revenue_ttm / eps / beta /
                        data_source / price_warning
        空 dict 表示未找到（可能是非上市公司、ticker 解析失败、网络异常等）。
    """
    if not entity:
        return {}

    if not _ENRICHER_AVAILABLE:
        return {
            "error": f"valuation_enricher not importable: {_IMPORT_ERROR}",
            "data_source": "unavailable",
        }

    try:
        return enrich_with_yahoo(entity) or {}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "data_source": "exception"}


def yfinance_snapshot(ticker: str) -> dict[str, Any]:
    """激活 `search_gateway.yfinance_summary`（PR1: 0 callers → 至少 1 调用方）。

    给定 ticker（如 'AAPL'、'BABA'、'0700.HK'），返回估值快照。
    美股和港股直接用；A 股会被 search_gateway 拒绝（无 .SS/.SZ 后缀路径），
    这种情况下回落到 `enrich_valuation` 走 NeoData + yfinance 完整流程。

    Returns:
        dict with keys: ticker / price / market_cap / pe_trailing / pe_forward /
                        ps / pb / ev_ebitda / revenue / profit_margin /
                        sector / industry / currency
        空 dict 表示 ticker 无效或 yfinance 不可用。
    """
    if not ticker:
        return {}

    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from search_gateway import yfinance_summary

        snap = yfinance_summary(ticker)
        if snap:
            return snap
    except Exception:  # noqa: BLE001
        pass

    # A 股兜底走 enrich_valuation（内部会从 ticker 还原公司名再调 NeoData）
    return {}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="统一估值补充（PR1 shim）")
    parser.add_argument("--entity", required=True, help="公司名 / ticker")
    parser.add_argument("--market", default="auto", choices=["auto", "cn", "hk", "us"])
    args = parser.parse_args()

    result = enrich_valuation(args.entity, market=args.market)
    print(json.dumps(result, ensure_ascii=False, indent=2))
