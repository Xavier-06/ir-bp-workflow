#!/usr/bin/env python3
"""
IC 行业研究预计算引擎 — Phase 1.2

功能：
  1. industry_size: 行业规模估算（TAM/SAM/SOM 三层推算）
  2. sector_benchmarks: 行业板块基准数据（复用 sector_benchmarks.py + 行业模式扩展）
  3. key_company_metrics: 关键公司财务指标汇总（从 scope 中提取公司列表）

与 IR 管线 financial_metrics_precompute.py 的区别：
  - IR 聚焦单 ticker 五大维度
  - IC 聚焦行业整体：规模估算 + 板块均值 + 多公司汇总

用法：
  python3 ic_precompute.py <行业名称> [--json|--markdown] [--market cn]
  python3 ic_precompute.py 半导体 --markdown
"""

import sys
import json
import re
import argparse
from typing import Optional, Dict, Any, List
from datetime import datetime

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))


# ============================================================
# 行业规模 — 动态搜索优先，硬编码仅为 fallback
# ============================================================

# 子行业 → 一级行业映射（用于模糊匹配，这个是分类逻辑不是数据，保留）
SUB_TO_MAJOR_IC: Dict[str, str] = {
    "芯片": "半导体", "集成电路": "半导体", "IC设计": "半导体", "晶圆": "半导体",
    "封测": "半导体", "光刻": "半导体",
    "电池": "电力设备", "光伏": "电力设备", "风电": "电力设备", "储能": "电力设备",
    "锂电池": "电力设备", "新能源": "电力设备",
    "AI": "计算机", "人工智能": "计算机", "云计算": "计算机", "SaaS": "计算机",
    "消费电子": "电子", "面板": "电子",
    "创新药": "医药生物", "医疗器械": "医药生物", "CXO": "医药生物",
    "白酒": "食品饮料", "啤酒": "食品饮料",
    "新能源车": "新能源汽车", "电动车": "新能源汽车",
    "自动驾驶": "汽车",
    "光通信": "通信", "5G": "通信",
    "锂": "有色金属", "稀土": "有色金属", "铜": "有色金属",
    "工业自动化": "机械设备", "机器人": "机械设备",
    "化肥": "基础化工", "精细化工": "基础化工",
}

# 硬编码 fallback：仅当动态搜索完全失败时使用
# 注意：这些数字会过期，仅作为保底兜底，不应作为主数据源
_FALLBACK_SIZES: Dict[str, Dict[str, Any]] = {
    "电子": {"tam_2025": 120000, "cagr_5y": 8.5},
    "医药生物": {"tam_2025": 45000, "cagr_5y": 10.2},
    "电力设备": {"tam_2025": 85000, "cagr_5y": 15.3},
    "计算机": {"tam_2025": 55000, "cagr_5y": 12.0},
    "汽车": {"tam_2025": 95000, "cagr_5y": 6.8},
    "半导体": {"tam_2025": 15000, "cagr_5y": 12.5},
    "新能源汽车": {"tam_2025": 35000, "cagr_5y": 18.0},
}


# ============================================================
# 核心函数
# ============================================================

def _match_industry(name: str) -> Optional[str]:
    """将用户输入的行业名匹配到已知行业分类（仅用于子行业→一级行业映射）"""
    if not name:
        return None

    # 子行业匹配
    if name in SUB_TO_MAJOR_IC:
        return SUB_TO_MAJOR_IC[name]

    for sub, major in SUB_TO_MAJOR_IC.items():
        if sub in name or name in sub:
            return major

    return None


def _extract_number(text: str, patterns: List[str]) -> Optional[float]:
    """从搜索结果文本中提取数字（亿元/万亿/亿美元等）"""
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            val_str = m.group(1).replace(',', '').replace('，', '').strip()
            try:
                val = float(val_str)
                # 如果匹配到"万亿"，转为亿元
                if '万亿' in pattern and val > 0:
                    val = val * 10000
                elif '万亿美元' in pattern or ('亿美元' in pattern and '万亿' not in pattern):
                    val = val * 7.2  # 近似汇率
                    if '万亿' in pattern:
                        val = val * 10000
                elif '亿美元' in pattern and '万亿' not in pattern:
                    val = val * 7.2
                return val
            except (ValueError, TypeError):
                continue
    return None


def _search_industry_size(industry_name: str) -> Dict[str, Any]:
    """通过搜索动态获取行业规模数据

    优先级：NeoData → SearchGateway(DDG+SearXNG) → 多个查询角度交叉验证
    """
    try:
        from scripts.search_gateway import search, neodata_search
    except ImportError:
        return {"error": "search_gateway not available"}

    queries = [
        f'"{industry_name}" 行业 市场规模 2025 亿元',
        f'"{industry_name}" industry market size TAM 2025',
        f'"{industry_name}" 产业链 细分领域 规模',
    ]

    all_results = []
    for q in queries:
        try:
            results = search(q, max_results=5, prefer="multi")
            all_results.extend(results)
        except Exception:
            continue

    if not all_results:
        return {"error": "no search results"}

    # 从搜索结果中提取行业规模数据
    tam_candidates = []
    cagr_candidates = []
    segments = {}
    sources = []

    tam_patterns = [
        r'市场规模[约达到为]*\s*([\d,.]+)\s*万亿',
        r'市场规模[约达到为]*\s*([\d,.]+)\s*亿',
        r'行业规模[约达到为]*\s*([\d,.]+)\s*万亿',
        r'行业规模[约达到为]*\s*([\d,.]+)\s*亿',
        r'市场[约达到为]*\s*([\d,.]+)\s*万亿元',
        r'市场[约达到为]*\s*([\d,.]+)\s*亿元',
        r'TAM[^\d]{0,20}([\d,.]+)\s*(?:billion|亿)',
        r'market size[^\d]{0,20}([\d,.]+)\s*(?:billion|亿)',
    ]

    cagr_patterns = [
        r'CAGR[^\d]{0,20}([\d.]+)\s*%',
        r'复合[增成长]率[^\d]{0,20}([\d.]+)\s*%',
        r'年[均增]增[长速][^\d]{0,20}([\d.]+)\s*%',
        r'(?:growth rate|增长率)[^\d]{0,20}([\d.]+)\s*%',
    ]

    for r in all_results:
        content = r.get('content', '') or r.get('title', '')
        if not content:
            continue

        # 提取 TAM
        tam = _extract_number(content, tam_patterns)
        if tam and 100 < tam < 500000:  # 合理范围：100亿~50万亿
            tam_candidates.append(tam)
            sources.append(r.get('url', r.get('source', '')))

        # 提取 CAGR
        for pattern in cagr_patterns:
            m = re.search(pattern, content)
            if m:
                try:
                    cagr = float(m.group(1))
                    if 0.5 < cagr < 50:  # 合理范围
                        cagr_candidates.append(cagr)
                except (ValueError, TypeError):
                    pass

    if not tam_candidates:
        return {"error": "could not extract TAM from search results", "raw_results_count": len(all_results)}

    # 取中位数作为 TAM 估值
    tam_candidates.sort()
    tam_median = tam_candidates[len(tam_candidates) // 2]

    # CAGR 取中位数（如果有）
    cagr_median = None
    if cagr_candidates:
        cagr_candidates.sort()
        cagr_median = round(cagr_candidates[len(cagr_candidates) // 2], 1)

    # 提取细分领域（从搜索结果的标题/content中找"细分"/"segment"/"环节"相关描述）
    seg_keywords = set()
    for r in all_results:
        content = r.get('content', '') or r.get('title', '')
        for m in re.finditer(r'(?:细分[领市]域|子行业|环节|segment)[：:]*\s*([^。\n]{2,20})', content):
            seg_keywords.add(m.group(1).strip())

    return {
        "tam_yi": round(tam_median),
        "cagr_5y_pct": cagr_median,
        "segments_found": list(seg_keywords)[:10],
        "sources": list(set(sources))[:5],
        "data_points": len(tam_candidates),
        "method": "search_dynamic",
    }


def compute_industry_size(industry_name: str, market: str = "cn") -> Dict[str, Any]:
    """计算行业规模估算（TAM/SAM/SOM 三层推算）

    逻辑：
    1. 动态搜索获取行业规模数据（NeoData + DDG + SearXNG）
    2. 搜索失败时降级到硬编码 fallback（标注为过期数据）
    3. 基于 CAGR 推算 5 年后规模，生成乐观/中性/保守三档
    """
    result = {
        "industry": industry_name,
        "original_query": industry_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "market": market,
    }

    # Step 1: 动态搜索
    search_result = _search_industry_size(industry_name)

    if "error" not in search_result and search_result.get("tam_yi"):
        tam_current = search_result["tam_yi"]
        cagr = search_result.get("cagr_5y_pct", 10.0) / 100  # 默认 10% 如果没搜到 CAGR
        result["source"] = "search_dynamic"
        result["data_points"] = search_result.get("data_points", 0)
        result["sources"] = search_result.get("sources", [])
        result["segments_found"] = search_result.get("segments_found", [])
    else:
        # Step 2: 硬编码 fallback
        matched = _match_industry(industry_name)
        if matched and matched in _FALLBACK_SIZES:
            ref = _FALLBACK_SIZES[matched]
            tam_current = ref["tam_2025"]
            cagr = ref["cagr_5y"] / 100
            result["source"] = "fallback_hardcoded"
            result["warning"] = (
                f"动态搜索未找到'{industry_name}'的行业规模数据"
                f"（搜索错误：{search_result.get('error', 'unknown')}），"
                f"使用硬编码参考值（可能已过期），子代理需自行验证"
            )
        else:
            result["source"] = "unavailable"
            result["error"] = f"无法获取'{industry_name}'的行业规模数据（搜索失败且无硬编码 fallback）"
            result["tam_yi"] = None
            result["cagr_5y_pct"] = None
            result["projections"] = None
            return result

    # Step 3: TAM/SAM/SOM 推算
    result["tam_current_yi"] = tam_current
    result["tam_current_bn"] = round(tam_current / 10000, 2)
    result["cagr_5y_pct"] = round(cagr * 100, 1) if cagr else None

    tam_5y_neutral = tam_current * (1 + cagr) ** 5
    tam_5y_optimistic = tam_current * (1 + cagr * 1.3) ** 5
    tam_5y_conservative = tam_current * (1 + cagr * 0.7) ** 5

    # SAM ≈ 60-80% TAM（中国市场口径）
    sam_pct = 0.70
    # SOM ≈ 15-30% SAM（可触达市场份额）
    som_pct = 0.22

    result["projections"] = {
        "current": {
            "TAM": tam_current,
            "SAM": round(tam_current * sam_pct),
            "SOM": round(tam_current * sam_pct * som_pct),
        },
        "5y_optimistic": {
            "TAM": round(tam_5y_optimistic),
            "SAM": round(tam_5y_optimistic * sam_pct),
            "SOM": round(tam_5y_optimistic * sam_pct * som_pct),
        },
        "5y_neutral": {
            "TAM": round(tam_5y_neutral),
            "SAM": round(tam_5y_neutral * sam_pct),
            "SOM": round(tam_5y_neutral * sam_pct * som_pct),
        },
        "5y_conservative": {
            "TAM": round(tam_5y_conservative),
            "SAM": round(tam_5y_conservative * sam_pct),
            "SOM": round(tam_5y_conservative * sam_pct * som_pct),
        },
    }

    return result


def compute_sector_benchmarks(industry_name: str) -> Dict[str, Any]:
    """计算行业板块基准数据

    复用 sector_benchmarks.py 的 INDUSTRY_PEERS 数据。
    """
    matched = _match_industry(industry_name)

    try:
        from scripts.sector_benchmarks import INDUSTRY_PEERS, HK_PEERS, _safe_float
        import yfinance as yf

        # 找到匹配的行业 peer 列表
        peers = []
        industry_label = matched or industry_name

        if matched and matched in INDUSTRY_PEERS:
            peers = INDUSTRY_PEERS[matched]
        elif matched and matched in HK_PEERS:
            peers = HK_PEERS[matched]
        else:
            # 模糊匹配
            for ind, p in INDUSTRY_PEERS.items():
                if matched and matched in ind:
                    peers = p
                    industry_label = ind
                    break
            if not peers:
                for ind, p in HK_PEERS.items():
                    if matched and matched in ind:
                        peers = p
                        industry_label = ind
                        break

        if not peers:
            return {
                "industry": industry_name,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "error": f"未找到'{industry_name}'的行业对标标的",
                "peers": [],
                "benchmarks": {},
            }

        # 收集 peer 指标
        peer_data = []
        for peer_ticker, peer_name in peers:
            try:
                stock = yf.Ticker(peer_ticker)
                info = stock.info
                peer_data.append({
                    "ticker": peer_ticker,
                    "name": peer_name,
                    "pe": _safe_float(info.get("trailingPE")),
                    "roe": _safe_float(info.get("returnOnEquity")),
                    "gross_margin": _safe_float(info.get("grossMargins")),
                    "revenue_growth": _safe_float(info.get("revenueGrowth")),
                    "market_cap": _safe_float(info.get("marketCap")),
                })
            except Exception:
                continue

        # 计算行业均值
        def _avg(values):
            valid = [v for v in values if v is not None]
            return round(sum(valid) / len(valid), 4) if valid else None

        benchmarks = {
            "PE_avg": _avg([d["pe"] for d in peer_data]),
            "ROE_avg": _avg([d["roe"] for d in peer_data if d["roe"] is not None]),
            "Gross_Margin_avg": _avg([d["gross_margin"] for d in peer_data if d["gross_margin"] is not None]),
            "Revenue_Growth_avg": _avg([d["revenue_growth"] for d in peer_data if d["revenue_growth"] is not None]),
            "peer_count": len(peer_data),
            "peers_used": [{"ticker": d["ticker"], "name": d["name"]} for d in peer_data],
        }

        return {
            "industry": industry_label,
            "original_query": industry_name,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "benchmarks": benchmarks,
        }

    except ImportError:
        return {
            "industry": industry_name,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "error": "sector_benchmarks module not available",
            "benchmarks": {},
        }


def compute_key_company_metrics(industry_name: str, task_id: str = "") -> Dict[str, Any]:
    """读取 scope 中提取的关键公司列表，获取财务指标汇总"""
    # 尝试从 scope 文件读取公司列表
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    scope_path = ROOT / "data" / "tasks" / f"{task_id}-ic_scope.json"

    company_list = []
    if scope_path.exists():
        try:
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            company_list = scope.get("company_list", [])
        except Exception:
            pass

    if not company_list:
        return {
            "industry": industry_name,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "companies": [],
            "note": "无指定公司列表，关键公司指标汇总跳过",
        }

    # 对每个公司尝试获取 yfinance 数据
    try:
        from scripts.sector_benchmarks import _safe_float
        import yfinance as yf
    except ImportError:
        return {
            "industry": industry_name,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "companies": [],
            "error": "yfinance not available",
        }

    from tasks.valuation_enricher import _resolve_ticker

    companies = []
    for company_name in company_list[:10]:  # 最多 10 家
        try:
            ticker = _resolve_ticker(company_name)
            if not ticker:
                companies.append({"name": company_name, "status": "ticker_not_found"})
                continue

            stock = yf.Ticker(ticker)
            info = stock.info
            companies.append({
                "name": company_name,
                "ticker": ticker,
                "pe": _safe_float(info.get("trailingPE")),
                "pb": _safe_float(info.get("priceToBook")),
                "roe": _safe_float(info.get("returnOnEquity")),
                "gross_margin": _safe_float(info.get("grossMargins")),
                "revenue_growth": _safe_float(info.get("revenueGrowth")),
                "market_cap_bn": round(_safe_float(info.get("marketCap", 0)) / 1e9, 2),
            })
        except Exception as e:
            companies.append({"name": company_name, "status": "error", "error": str(e)[:100]})

    return {
        "industry": industry_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "companies": companies,
    }


def compute_all(industry_name: str, market: str = "cn", task_id: str = "") -> Dict[str, Any]:
    """主入口：运行所有 IC 预计算引擎"""
    result = {
        "industry": industry_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "market": market,
    }

    # 引擎 1: 行业规模
    try:
        result["industry_size"] = compute_industry_size(industry_name, market)
    except Exception as e:
        result["industry_size"] = {"error": str(e)}

    # 引擎 2: 板块基准
    try:
        result["sector_benchmarks"] = compute_sector_benchmarks(industry_name)
    except Exception as e:
        result["sector_benchmarks"] = {"error": str(e)}

    # 引擎 3: 关键公司指标
    try:
        result["key_company_metrics"] = compute_key_company_metrics(industry_name, task_id)
    except Exception as e:
        result["key_company_metrics"] = {"error": str(e)}

    return result


# ============================================================
# Markdown 输出
# ============================================================

def format_markdown(result: Dict[str, Any]) -> str:
    """生成 IC 预计算摘要表"""

    def fmt(val, suffix=""):
        if val is None:
            return "N/A"
        if isinstance(val, float):
            return f"{val:,.0f}{suffix}" if abs(val) >= 100 else f"{val:.2f}{suffix}"
        return str(val)

    lines = [
        f"## IC 行业预计算摘要 — {result['industry']}",
        f"_数据截至: {result['date']} ｜ 市场: {result['market']}_",
        "",
    ]

    # 行业规模
    size = result.get("industry_size", {})
    if "error" not in size:
        lines.append("### 1. 行业规模估算")
        lines.append("| 场景 | TAM(亿元) | SAM(亿元) | SOM(亿元) |")
        lines.append("|------|----------|----------|----------|")
        proj = size.get("projections", {})
        if proj:
            for label, key in [("当前", "current"), ("5年·乐观", "5y_optimistic"),
                               ("5年·中性", "5y_neutral"), ("5年·保守", "5y_conservative")]:
                p = proj.get(key, {})
                if p:
                    lines.append(f"| {label} | {fmt(p.get('TAM'))} | {fmt(p.get('SAM'))} | {fmt(p.get('SOM'))} |")

        if size.get("cagr_5y_pct"):
            lines.append(f"\n**5年 CAGR**: {size['cagr_5y_pct']}%")

        if size.get("source"):
            src_label = {"search_dynamic": "实时搜索", "fallback_hardcoded": "硬编码兜底（可能过期）"}.get(size["source"], size["source"])
            lines.append(f"\n**数据来源**: {src_label}")
            if size.get("warning"):
                lines.append(f"⚠ {size['warning']}")

        segs = size.get("segments_found", [])
        if segs:
            lines.append(f"\n**搜索发现的细分领域**: {', '.join(segs)}")
    else:
        lines.append(f"### 1. 行业规模估算\n⚠ {size.get('error', '未知错误')}")

    lines.append("")

    # 板块基准
    bench = result.get("sector_benchmarks", {})
    if "error" not in bench and bench.get("benchmarks"):
        lines.append("### 2. 行业板块基准")
        bm = bench["benchmarks"]
        lines.append("| 指标 | 行业均值 |")
        lines.append("|------|---------|")
        if bm.get("PE_avg") is not None:
            lines.append(f"| PE(TTM) | {bm['PE_avg']:.2f}x |")
        if bm.get("ROE_avg") is not None:
            lines.append(f"| ROE | {bm['ROE_avg']*100:.2f}% |")
        if bm.get("Gross_Margin_avg") is not None:
            lines.append(f"| 毛利率 | {bm['Gross_Margin_avg']*100:.2f}% |")
        if bm.get("Revenue_Growth_avg") is not None:
            lines.append(f"| 营收增速 | {bm['Revenue_Growth_avg']*100:.2f}% |")
        lines.append(f"| 对标样本 | {bm.get('peer_count', 0)} 家 |")
    else:
        lines.append(f"### 2. 行业板块基准\n⚠ {bench.get('error', '无数据')}")

    lines.append("")

    # 关键公司
    comp = result.get("key_company_metrics", {})
    if comp.get("companies"):
        lines.append("### 3. 关键公司财务指标")
        lines.append("| 公司 | Ticker | PE | ROE | 毛利率 | 营收增速 | 市值(亿) |")
        lines.append("|------|--------|-----|-----|-------|---------|---------|")
        for c in comp["companies"]:
            if "error" in c or c.get("status"):
                continue
            lines.append(
                f"| {c.get('name', '')} | {c.get('ticker', '')} | "
                f"{fmt(c.get('pe'), 'x')} | {fmt(c.get('roe'), '%')} | "
                f"{fmt(c.get('gross_margin'), '%')} | {fmt(c.get('revenue_growth'), '%')} | "
                f"{fmt(c.get('market_cap_bn'), 'B')} |"
            )

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="IC 行业研究预计算引擎 — 行业规模 + 板块基准 + 关键公司指标"
    )
    parser.add_argument(
        "industry",
        nargs="?",
        default=None,
        help="行业名称（如 半导体、新能源汽车、医药生物）",
    )
    parser.add_argument(
        "--market", default="cn",
        help="市场区域（cn/hk/us），默认 cn",
    )
    parser.add_argument(
        "--task-id", default="",
        help="任务ID（用于读取 scope 文件）",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON 格式输出（供 Agent 消费）",
    )
    parser.add_argument(
        "--markdown", action="store_true",
        help="Markdown 表格格式输出",
    )
    parser.add_argument(
        "--list-industries", action="store_true",
        help="列出所有子行业→一级行业映射",
    )

    args = parser.parse_args()

    if args.list_industries:
        print("# IC 子行业映射\n")
        for sub, major in SUB_TO_MAJOR_IC.items():
            print(f"  {sub} → {major}")
        print(f"\n(fallback 硬编码 TAM 数据: {', '.join(_FALLBACK_SIZES.keys())})")
        sys.exit(0)

    if args.industry is None:
        print("用法: python ic_precompute.py <行业名称> [--json|--markdown] [--market cn]")
        print("示例: python ic_precompute.py 半导体 --markdown")
        sys.exit(0)

    try:
        result = compute_all(args.industry, args.market, args.task_id)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.markdown:
            print(format_markdown(result))
        else:
            print(format_markdown(result))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
