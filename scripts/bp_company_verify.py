#!/usr/bin/env python3
"""BP company verification.

Verifies company identity, founders, registration signals, litigation/compliance
signals, and writes a compact evidence report for downstream BP due diligence.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"


def _task_dir(job_ctx: Any) -> Path:
    workspace = getattr(job_ctx, "workspace", None)
    if workspace is not None:
        return workspace.root
    path = TASKS_DIR / job_ctx.job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_profile(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "bp_step0_profile.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _entity_from_profile(profile: dict[str, Any], fallback: str = "") -> str:
    for key in ("company_name", "entity", "project_name"):
        value = str(profile.get(key) or "").strip()
        if value:
            return value
    return fallback.strip()


def _founder_names(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("team_highlights") or profile.get("founders") or []
    if isinstance(raw, str):
        raw = [raw]
    names: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        first = re.split(r"\s|[-—,，/／|｜]", text, maxsplit=1)[0].strip()
        if first and first not in names:
            names.append(first)
    return names[:8]


def _advisor_names(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("advisors") or []
    if isinstance(raw, str):
        raw = [raw]
    names: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        first = re.split(r"\s|[-—,，/／|｜]", text, maxsplit=1)[0].strip()
        if first and first not in names:
            names.append(first)
    return names[:8]


def _search(query: str, max_results: int = 6) -> list[dict[str, Any]]:
    try:
        from scripts.search_gateway import search
        rows = search(query, max_results=max_results)
        result = rows if isinstance(rows, list) else []
        if not result:
            print(f"    ⚠️ 搜索无结果: {query[:50]}", flush=True)
        return result
    except Exception as e:
        print(f"    ❌ 搜索异常: {e}", flush=True)
        return []


# ── PR2: 估值补充（NeoData + yfinance） ───────────────────────

_LISTED_OFFICIAL_DOMAINS = (
    "sec.gov", "hkexnews.hk", "cninfo.com.cn",
    "sse.com.cn", "szse.cn", "nasdaq.com", "nyse.com",
)


def _is_company_listed(profile: dict[str, Any], all_search_rows: list[dict[str, Any]]) -> bool:
    """判断主体是否为上市公司。

    命中条件（任一）：
    1. profile 显式标记 `is_listed=True` 或 `exchange`/`ticker` 字段
    2. 搜索结果出现上市公司官方源域名
    3. profile 含 `financing_stage` 提示为已上市（"IPO"/"已上市"/"上市"）
    """
    if profile.get("is_listed") is True:
        return True
    if profile.get("ticker") or profile.get("exchange"):
        return True
    stage = str(profile.get("financing_stage") or "").lower()
    if any(kw in stage for kw in ("ipo", "已上市", "上市", "listed")):
        return True
    for row in all_search_rows:
        url = str(row.get("url", "")).lower()
        if any(d in url for d in _LISTED_OFFICIAL_DOMAINS):
            return True
    return False


def _detect_market(profile: dict[str, Any], entity: str) -> str:
    """根据 profile / 实体名粗略判断市场，供估值补充器参考。"""
    explicit = str(profile.get("market") or "").lower()
    if explicit in ("cn", "hk", "us"):
        return explicit
    stage = str(profile.get("financing_stage") or "")
    if any(kw in entity for kw in ("HK", ".HK", "港股")) or any(
        kw in stage for kw in ("港股", "HK")
    ):
        return "hk"
    if any(kw in entity for kw in ("Inc.", "Corp.", "Ltd.", "US")):
        return "us"
    return "auto"


def _attach_valuation(entity: str, profile: dict[str, Any]) -> dict[str, Any]:
    """PR2: 调 scripts.valuation_enricher.enrich_valuation 补估值字段。

    非上市公司 / ticker 解析失败 / 网络异常 → 返回空 dict，不抛异常。
    """
    if not entity:
        return {}
    try:
        from scripts.valuation_enricher import enrich_valuation
        market = _detect_market(profile, entity)
        valuation = enrich_valuation(entity, market=market) or {}
        if not valuation:
            return {}
        # 扁平化关键字段，方便下游 BP role 直接读
        return {
            "ticker": valuation.get("ticker", ""),
            "price": valuation.get("price", ""),
            "currency": valuation.get("currency", ""),
            "pe_ratio": valuation.get("pe_ratio", ""),
            "ps_ratio": valuation.get("ps_ratio", ""),
            "pb_ratio": valuation.get("pb_ratio", ""),
            "market_cap": valuation.get("market_cap", ""),
            "52w_high": valuation.get("52w_high", ""),
            "52w_low": valuation.get("52w_low", ""),
            "revenue_ttm": valuation.get("revenue_ttm", ""),
            "eps": valuation.get("eps", ""),
            "data_source": valuation.get("data_source", ""),
            "price_warning": valuation.get("price_warning", ""),
            "market": market,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "data_source": "exception"}


def _extract_comparables(task_dir: Path, profile: dict[str, Any], entity: str = "") -> list[dict[str, str]]:
    """PR4: 提取可比上市公司，返回 [{name, ticker}] 列表。

    策略：
    1. profile.competitors（BP 自述，无 ticker）
    2. research_plan fact_requirements（结构化搜索任务，无 ticker）
    3. NeoData 板块表格（同时拿 ticker + name，最可靠）
    """
    items: list[dict[str, str]] = []
    seen_names: set[str] = set()

    def _add(name: str, ticker: str = "") -> None:
        n = name.strip()
        if not n or len(n) < 3 or len(n) > 10 or n in seen_names:
            return
        seen_names.add(n)
        items.append({"name": n, "ticker": ticker})

    # 1. profile.competitors（最高优先级，BP 自述的直接竞品）
    raw_comps = profile.get("competitors") or profile.get("key_competitors") or []
    if isinstance(raw_comps, str):
        raw_comps = [s.strip() for s in re.split(r'[,，、;/]', raw_comps) if s.strip()]
    for c in raw_comps:
        _add(str(c))

    # 2. research_plan fact_requirements 中显式搜索的竞品名
    rp_path = task_dir / "bp_research_plan.json"
    if rp_path.exists():
        try:
            rp = json.loads(rp_path.read_text(encoding="utf-8"))
            for fr in rp.get("fact_requirements", []):
                q = str(fr.get("question", ""))
                for match in re.finditer(
                    r'(?<![一-鿿])([\u4e00-\u9fff]{3,6}(?:股份|微电子))',
                    q
                ):
                    _add(match.group(1))
        except Exception:
            pass

    # 3. NeoData 搜索可比公司（核心补充源，同时拿 ticker）
    if len(items) < 5 and entity:
        try:
            from scripts.search_gateway import neodata_search
            industry = profile.get("sub_industry") or profile.get("industry") or ""
            products = profile.get("product_service") or []
            product_tag = products[0] if products else ""
            queries = []
            if industry:
                queries.append(f"{industry} 龙头上市公司 市值")
            if product_tag:
                queries.append(f"{product_tag} 行业上市公司 PS估值")
            for query in queries[:2]:
                rows = neodata_search(query, data_type="api")
                for row in rows:
                    content = str(row.get("content", ""))
                    # 解析 NeoData 板块表格: | 股票代码 | 股票名称 | 价格 | ...
                    for table_match in re.finditer(
                        r'\|\s*(\d{6}\.(?:SZ|SH|BJ))\s*\|\s*([\u4e00-\u9fff]{2,8}(?:股份|电子|科技|半导体|微电子|集团|材料|光电|芯片)?)\s*\|',
                        content
                    ):
                        ticker = table_match.group(1)
                        comp = table_match.group(2).strip()
                        if comp in ("股票名称", "证券名称", "名称"):
                            continue
                        if entity and (comp in entity or entity.endswith(comp)):
                            continue
                        _add(comp, ticker=ticker)
                        if len(items) >= 8:
                            break
                    if len(items) >= 8:
                        break
                if len(items) >= 8:
                    break
        except Exception:
            pass

    return items[:8]  # 最多8家，避免 API 调用过多


def _enrich_by_ticker(name: str, ticker: str) -> dict[str, Any] | None:
    """PR4: 用已知 ticker 直接获取估值快照（跳过中文名解析）。

    对 A/HK 股：NeoData (neodata_summary) + yfinance 双源。
    对美股：yfinance 直接查。
    """
    try:
        import sys
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from search_gateway import neodata_summary

        # NeoData（A/HK 股优先）
        is_ahk = any(ticker.endswith(s) for s in ('.SS', '.SZ', '.BJ', '.HK'))
        nd_result = None
        if is_ahk:
            nd_result = neodata_summary(ticker)
            if not nd_result:
                nd_result = neodata_summary(name)

        # yfinance
        yf_result = None
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.info or {}
            if info.get('regularMarketPrice'):
                yf_result = {
                    'ticker': ticker,
                    'price': info.get('regularMarketPrice'),
                    'currency': info.get('currency', ''),
                    'pe_ratio': info.get('trailingPE') or info.get('forwardPE'),
                    'ps_ratio': info.get('priceToSalesTrailing12Months'),
                    'pb_ratio': info.get('priceToBook'),
                    'market_cap': info.get('marketCap'),
                    'revenue_ttm': info.get('totalRevenue'),
                    'eps': info.get('trailingEps'),
                    'data_source': 'yfinance',
                }
        except Exception:
            pass

        # 合并：NeoData 优先（A/HK 股更准）
        if nd_result and nd_result.get('price'):
            return {
                'ticker': ticker,
                'price': nd_result.get('price'),
                'currency': nd_result.get('currency', 'CNY'),
                'pe_ratio': nd_result.get('pe_trailing'),
                'ps_ratio': nd_result.get('ps'),
                'pb_ratio': nd_result.get('pb'),
                'market_cap': nd_result.get('market_cap'),
                'revenue_ttm': nd_result.get('revenue'),
                'eps': None,
                'data_source': 'neodata',
            }
        if yf_result:
            return yf_result
        if nd_result:
            return {
                'ticker': ticker,
                'price': nd_result.get('price'),
                'currency': nd_result.get('currency', 'CNY'),
                'pe_ratio': nd_result.get('pe_trailing'),
                'ps_ratio': nd_result.get('ps'),
                'pb_ratio': nd_result.get('pb'),
                'market_cap': nd_result.get('market_cap'),
                'revenue_ttm': nd_result.get('revenue'),
                'eps': None,
                'data_source': 'neodata',
            }
        return None
    except Exception:
        return None


def _batch_enrich_comparables(comparables: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """PR4: 对可比上市公司批量获取估值快照，返回 {公司名: 估值快照} dict。

    优先用 ticker 直接查 NeoData/yfinance，中文名作 fallback。
    单个失败不影响其他公司，跳过即可。
    """
    if not comparables:
        return {}

    results: dict[str, dict[str, Any]] = {}
    for item in comparables:
        name = item["name"]
        ticker = item.get("ticker", "")
        try:
            # 优先用 ticker 直接查（跳过中文名解析）
            v = None
            if ticker:
                v = _enrich_by_ticker(name, ticker)
            # Fallback: 用公司名调 enrich_valuation
            if not v or not v.get("ticker"):
                try:
                    from scripts.valuation_enricher import enrich_valuation
                except ImportError:
                    enrich_valuation = None
                if enrich_valuation:
                    v = enrich_valuation(name, market="auto")
            if v and v.get("ticker"):
                results[name] = {
                    "ticker": v.get("ticker", ""),
                    "price": v.get("price", ""),
                    "currency": v.get("currency", ""),
                    "pe_ratio": v.get("pe_ratio", ""),
                    "ps_ratio": v.get("ps_ratio", ""),
                    "pb_ratio": v.get("pb_ratio", ""),
                    "market_cap": v.get("market_cap", ""),
                    "revenue_ttm": v.get("revenue_ttm", ""),
                    "eps": v.get("eps", ""),
                    "data_source": v.get("data_source", ""),
                    "price_warning": v.get("price_warning", ""),
                }
                print(f"     ✅ {name}: ticker={v['ticker']} PE={v.get('pe_ratio','')} PS={v.get('ps_ratio','')}", flush=True)
            else:
                print(f"     ⚠️ {name}: 未获取到估值数据（可能非上市或 ticker 解析失败）", flush=True)
        except Exception as exc:
            print(f"     ❌ {name}: enrich 异常 {exc}", flush=True)
    return results


def _compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append({
            "title": str(row.get("title") or "")[:180],
            "url": str(row.get("url") or ""),
            "content": str(row.get("content") or row.get("snippet") or "")[:500],
            "source": str(row.get("source") or row.get("engine") or ""),
        })
    return out


def _write_markdown(task_dir: Path, entity: str, report: dict[str, Any]) -> Path:
    path = task_dir / "company_verify_report.md"
    lines = [
        f"# BP 工商与主体核验：{entity or '未知主体'}",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 核验结论：{report['verdict']}",
        "",
        "## 主体线索",
    ]
    for item in report.get("identity_signals", []):
        lines += [f"- {item.get('title', '')}", f"  - {item.get('url', '')}"]
    if not report.get("identity_signals"):
        lines.append("- 未找到稳定的公开主体线索")

    lines += ["", "## 创始人与管理层线索"]
    if not report.get("founders"):
        lines.append("- ⚠️ BP 未披露实际创始人/CEO/管理层")
    for founder, rows in report.get("founder_signals", {}).items():
        lines.append(f"### {founder}")
        if rows:
            for item in rows:
                lines += [f"- {item.get('title', '')}", f"  - {item.get('url', '')}"]
        else:
            lines.append("- 未找到独立公开线索")

    lines += ["", "## 科学顾问/外部顾问线索"]
    if not report.get("advisors"):
        lines.append("- BP 未提及外部顾问")
    for advisor, rows in report.get("advisor_signals", {}).items():
        lines.append(f"### {advisor}")
        if rows:
            for item in rows:
                lines += [f"- {item.get('title', '')}", f"  - {item.get('url', '')}"]
        else:
            lines.append("- 未找到独立公开线索")

    lines += ["", "## 风险与合规线索"]
    for item in report.get("risk_signals", []):
        lines += [f"- {item.get('title', '')}", f"  - {item.get('url', '')}"]
    if not report.get("risk_signals"):
        lines.append("- 未发现明确公开风险线索；不代表不存在风险")

    lines += ["", "## 备注", "公开搜索只能作为线索发现，不能替代工商数据库、法院公告、征信或律师尽调。"]
    # PR2: 估值快照（仅上市公司）
    valuation = report.get("valuation_data") or {}
    if valuation.get("ticker"):
        currency = valuation.get("currency", "")
        lines += [
            "",
            "## 估值快照（NeoData + yfinance 公开行情，仅供 bp_valuation_return 角色辅助）",
            "",
            f"- Ticker: `{valuation.get('ticker', '')}`",
            f"- 价格: {currency}{valuation.get('price', '')}",
            f"- PE (TTM): {valuation.get('pe_ratio', '')}",
            f"- PS: {valuation.get('ps_ratio', '')}",
            f"- PB: {valuation.get('pb_ratio', '')}",
            f"- 市值: {valuation.get('market_cap', '')}",
            f"- 52W 高/低: {valuation.get('52w_high', '')} / {valuation.get('52w_low', '')}",
            f"- 收入 TTM: {valuation.get('revenue_ttm', '')}",
            f"- EPS: {valuation.get('eps', '')}",
            f"- 数据源: {valuation.get('data_source', '')}",
            f"- 估测市场: {valuation.get('market', '')}",
        ]
        if valuation.get("price_warning"):
            lines.append(f"- ⚠️ {valuation['price_warning']}")
    # PR4: 可比公司估值快照（目标非上市时）
    comparable_vals = report.get("comparable_valuations") or {}
    if comparable_vals:
        lines += [
            "",
            "## 可比公司估值快照（NeoData + yfinance，供 bp_valuation_return 角色直接使用）",
            "",
        ]
        for comp_name, cv in comparable_vals.items():
            currency = cv.get("currency", "")
            lines.append(f"### {comp_name}")
            lines += [
                f"- Ticker: `{cv.get('ticker', '')}`",
                f"- 价格: {currency}{cv.get('price', '')}",
                f"- PE (TTM): {cv.get('pe_ratio', '')}",
                f"- PS: {cv.get('ps_ratio', '')}",
                f"- PB: {cv.get('pb_ratio', '')}",
                f"- 市值: {cv.get('market_cap', '')}",
                f"- 收入 TTM: {cv.get('revenue_ttm', '')}",
                f"- EPS: {cv.get('eps', '')}",
                f"- 数据源: {cv.get('data_source', '')}",
            ]
            if cv.get("price_warning"):
                lines.append(f"- ⚠️ {cv['price_warning']}")
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_company_verify(job_ctx: Any) -> dict[str, Any]:
    task_dir = _task_dir(job_ctx)
    profile = _read_profile(task_dir)
    entity = _entity_from_profile(profile, getattr(job_ctx, "entity", ""))
    founders = _founder_names(profile)
    advisors = _advisor_names(profile)
    stage = str(profile.get("financing_stage") or "").strip().lower()
    is_early = any(kw in stage for kw in ("种子", "天使", "seed", "angel", "pre-a", "pre_a"))

    print(f"  🔍 主体核验: entity={entity}, stage={stage}, early={is_early}", flush=True)
    print(f"     founders={founders}, advisors={advisors}", flush=True)
    if not founders and advisors:
        print(f"  ⚠️ BP 未披露实际创始人/CEO，仅有顾问: {advisors}", flush=True)

    # 根据融资阶段选择搜索策略
    identity_rows: list[dict[str, Any]] = []
    if is_early:
        # 种子/天使轮：公司大概率没注册，搜创始人个人 + 技术关键词
        print(f"  📋 早期项目策略：跳过工商搜索，聚焦创始人和技术验证", flush=True)
        tech_keywords = profile.get("sub_industry") or profile.get("industry") or ""
        products = profile.get("product_service") or []
        product_str = " ".join(str(p) for p in products[:3]) if products else ""

        early_queries = []
        tech_tag = " ".join(filter(None, [tech_keywords, product_str]))[:40] or entity
        for founder in founders[:3]:
            early_queries.append(f'"{founder}" {tech_tag} 研究')
            early_queries.append(f'"{founder}" 竞赛 获奖 论文')
        if tech_keywords:
            early_queries.append(f'{tech_keywords} 技术 研究 进展')
        if product_str:
            early_queries.append(f'{product_str} 市场 应用')

        for query in early_queries:
            rows = _search(query, max_results=4)
            identity_rows.extend(rows)
    else:
        # B轮及以后：正常搜工商
        if entity:
            for query in (
                f'"{entity}" 工商 注册 法定代表人',
                f'"{entity}" 统一社会信用代码',
                f'"{entity}" 官网 公司 简介',
            ):
                identity_rows.extend(_search(query, max_results=4))

    # 创始人个人履历（所有阶段都搜，但早期不绑公司名）
    founder_signals: dict[str, list[dict[str, str]]] = {}
    for founder in founders:
        if is_early:
            query = f'"{founder}" 履历 背景 研究'
        else:
            query = f'"{entity}" "{founder}" 创始人 CEO' if entity else f'"{founder}" 创始人 CEO'
        founder_signals[founder] = _compact_rows(_search(query, max_results=4))

    # 顾问验证（不绑公司名，直接搜顾问本人）
    advisor_signals: dict[str, list[dict[str, str]]] = {}
    for advisor in advisors:
        query = f'"{advisor}" 院士 教授 专家'
        advisor_signals[advisor] = _compact_rows(_search(query, max_results=3))

    # 风险搜索（早期项目搜创始人个人风险，不搜公司）
    risk_rows: list[dict[str, Any]] = []
    if is_early:
        for founder in founders[:3]:
            for query in (
                f'"{founder}" 诉讼 失信 处罚',
                f'"{founder}" 骗局 造假 争议',
            ):
                risk_rows.extend(_search(query, max_results=3))
    elif entity:
        for query in (
            f'"{entity}" 诉讼 行政处罚',
            f'"{entity}" 失信 被执行人',
            f'"{entity}" 经营异常 风险',
        ):
            risk_rows.extend(_search(query, max_results=4))

    compact_identity = _compact_rows(identity_rows)[:10]
    compact_risk = _compact_rows(risk_rows)[:10]
    verdict = "verified_with_public_signals" if compact_identity else "insufficient_public_signals"

    # PR2: 估值补充（仅上市公司）— NeoData + yfinance 双源
    all_searched = list(identity_rows) + list(risk_rows)
    valuation_block: dict[str, Any] = {}
    if entity and _is_company_listed(profile, all_searched):
        print(f"  📈 [PR2] 主体判定为上市公司，触发估值补充", flush=True)
        valuation_block = _attach_valuation(entity, profile)
        if valuation_block.get("ticker"):
            print(
                f"     ticker={valuation_block['ticker']} "
                f"price={valuation_block['currency']}{valuation_block['price']} "
                f"PE={valuation_block['pe_ratio']} "
                f"source={valuation_block['data_source']}",
                flush=True,
            )
            if valuation_block.get("price_warning"):
                print(f"  ⚠ [PR2] {valuation_block['price_warning']}", flush=True)
        else:
            print(f"  📈 [PR2] 估值数据未获取到（可能 ticker 解析失败）", flush=True)

    # PR4: 可比公司批量 enrich — 目标非上市时，对可比上市公司预注入估值快照
    comparable_valuations: dict[str, dict[str, Any]] = {}
    if entity and not valuation_block.get("ticker"):
        # 目标非上市（或目标估值未获取到），尝试 enrich 可比公司
        comparables = _extract_comparables(task_dir, profile, entity=entity)
        if comparables:
            print(f"  📊 [PR4] 目标非上市，对 {len(comparables)} 家可比公司批量 enrich 估值", flush=True)
            for c in comparables:
                ticker_hint = f" (ticker={c['ticker']})" if c.get('ticker') else ""
                print(f"     - {c['name']}{ticker_hint}", flush=True)
            comparable_valuations = _batch_enrich_comparables(comparables)
            if comparable_valuations:
                print(f"  📊 [PR4] 成功 enrich {len(comparable_valuations)}/{len(comparables)} 家可比公司", flush=True)
            else:
                print(f"  📊 [PR4] 无可比公司估值数据获取成功", flush=True)

    report = {
        "task_id": job_ctx.job_id,
        "entity": entity,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "verdict": verdict,
        "identity_signals": compact_identity,
        "founders": founders,
        "founder_signals": founder_signals,
        "advisors": advisors,
        "advisor_signals": advisor_signals,
        "risk_signals": compact_risk,
        "valuation_data": valuation_block,
        "comparable_valuations": comparable_valuations,
        "limitations": [
            "公开搜索结果只作线索，不等同于工商数据库核验结论。",
            "如进入投资流程，应继续使用工商数据库、法院公告和律师尽调做最终确认。",
            "估值字段为公开行情快照，仅供 bp_valuation_return 角色辅助，不构成投资建议。",
        ],
    }

    json_path = task_dir / "company_verify_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = _write_markdown(task_dir, entity, report)

    return {
        "ok": True,
        "mode": "bp_company_verify",
        "phase": "phase02_company_verify",
        "job_id": job_ctx.job_id,
        "result": {
            "entity": entity,
            "verdict": verdict,
            "identity_signal_count": len(compact_identity),
            "risk_signal_count": len(compact_risk),
            "valuation_attached": bool(valuation_block.get("ticker")),
            "valuation_ticker": valuation_block.get("ticker", ""),
            "comparable_count": len(comparable_valuations),
            "comparable_names": list(comparable_valuations.keys()),
            "json_path": str(json_path),
            "markdown_path": str(md_path),
        },
    }


if __name__ == "__main__":
    import argparse
    from runtime.profiles.base import JobContext

    parser = argparse.ArgumentParser(description="Run BP company verification")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--entity", default="")
    args = parser.parse_args()
    result = run_company_verify(JobContext(job_id=args.task_id, entity=args.entity))
    print(json.dumps(result, ensure_ascii=False, indent=2))
