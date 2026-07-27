#!/usr/bin/env python3
"""
Presearch Query Builder — 统一预搜索引擎 v1.0

从 pipeline config 和输入上下文生成搜索查询计划，多数据源执行，产出结构化结果和摘要。

设计原则：
- 查询生成基于 pipeline config 中的 domain definitions（不是硬编码 per-entity 的 STEP_QUERIES）
- 数据源路由基于 config 中的 source 声明
- 支持 tencent_news（分钟级新闻）+ web_search（通用搜索）+ structured（westock/tyc/neodata）
- 可被所有四条管线（IR/BP/IC/Lit）共用

用法：
    python3 scripts/presearch_query_builder.py \
        --pipeline ir --entity "腾讯" --market hk \
        --task-id TASK-001 --output-dir data/tasks/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "presearch"
TASKS_DIR = ROOT / "data" / "tasks"

# SSL
os.environ.setdefault("SSL_CERT_FILE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/opt/homebrew/etc/openssl@3/cert.pem")

CURRENT_YEAR = datetime.now().year
PREV_YEAR = CURRENT_YEAR - 1


def load_sources_config() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "sources.json").read_text(encoding="utf-8"))


def load_pipeline_config() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "pipeline_queries.json").read_text(encoding="utf-8"))


def generate_queries(
    pipeline: str,
    entity: str,
    market: str = "cn",
    ticker: str = "",
    query: str = "",
    topic_metadata: dict[str, Any] | None = None,
    sub_topics: list[str] | None = None,
    target_companies: list[str] | None = None,
    english_name: str = "",
) -> list[dict[str, Any]]:
    """Generate search queries for a presearch run.

    Args:
        pipeline: "ir" | "bp" | "ic" | "lit"
        entity: 主体名称（公司名/行业名/课题名）
        market: cn/hk/us
        ticker: 股票代码（IR 管线专用）
        query: 附加查询文本
        topic_metadata: 课题元数据（IC 管线专用）
        sub_topics: 子方向列表（Lit 管线专用）
        target_companies: 目标公司列表
        english_name: 公司英文名（IR 管线专用）

    Returns:
        List of query dicts: [{"query": "...", "domain": "...", "sources": [...], "priority": "..."}]
    """
    pipeline_cfg = load_pipeline_config()
    sources_cfg = load_sources_config()

    pl = pipeline_cfg.get("pipelines", {}).get(pipeline, {})
    domains = pl.get("query_domains", [])
    max_total = pl.get("max_total_queries", 20)

    all_queries: list[dict[str, Any]] = []

    # Prepare template variables
    template_vars = {
        "entity": entity,
        "market": market,
        "year": str(CURRENT_YEAR),
        "prev_year": str(PREV_YEAR),
        "ticker": ticker,
        "english_name": english_name,
    }

    for domain in domains:
        domain_key = domain["key"]
        domain_sources = domain.get("sources", ["web_search"])
        domain_max = domain.get("max_queries", 3)
        domain_priority = domain.get("priority", "medium")
        query_strategy = domain.get("query_strategy", "")

        # Skip domains with no viable sources
        active_sources = [s for s in domain_sources if s in sources_cfg.get("sources", {})]
        if not active_sources:
            continue

        # Generate queries based on domain strategy + pipeline context
        domain_queries = _build_domain_queries(
            pipeline=pipeline,
            domain_key=domain_key,
            strategy=query_strategy,
            template_vars=template_vars,
            max_queries=domain_max,
            topic_metadata=topic_metadata,
            sub_topics=sub_topics,
            target_companies=target_companies,
            extra_query=query,
        )

        for dq in domain_queries:
            dq["sources"] = active_sources
            dq["domain"] = domain_key
            dq["priority"] = domain_priority

        all_queries.extend(domain_queries)
        if len(all_queries) >= max_total:
            break

    # Trim to max_total
    return all_queries[:max_total]


def _build_domain_queries(
    pipeline: str,
    domain_key: str,
    strategy: str,
    template_vars: dict[str, str],
    max_queries: int,
    topic_metadata: dict[str, Any] | None,
    sub_topics: list[str] | None,
    target_companies: list[str] | None,
    extra_query: str,
) -> list[dict[str, Any]]:
    """Build queries for a specific domain based on pipeline context."""

    entity = template_vars["entity"]
    year = template_vars["year"]
    prev_year = template_vars["prev_year"]
    en_name = template_vars["english_name"] or ""

    # ── IR pipeline: ticker-based financial queries ──
    if pipeline == "ir":
        return _ir_queries(domain_key, entity, en_name, year, prev_year, max_queries)

    # ── BP pipeline: company due diligence ──
    if pipeline == "bp":
        return _bp_queries(domain_key, entity, year, max_queries)

    # ── IC pipeline: topic-driven queries ──
    if pipeline == "ic":
        return _ic_queries(domain_key, entity, topic_metadata, target_companies, year, max_queries)

    # ── Lit pipeline: literature review ──
    if pipeline == "lit":
        return _lit_queries(domain_key, entity, sub_topics, target_companies, year, max_queries)

    # Fallback: simple keyword expansion
    return [{"query": f"{entity} {domain_key} {year}"}]


def _ir_queries(
    domain_key: str,
    entity: str,
    en_name: str,
    year: str,
    prev_year: str,
    max_queries: int,
) -> list[dict[str, Any]]:
    """Generate IR-specific queries per domain."""
    queries: list[dict[str, Any]] = []

    if domain_key == "latest_news":
        queries = [
            {"query": f"{entity} 最新 公告 财报 {year}"},
            {"query": f"{entity} 突发 新闻 事件"},
            {"query": f"{entity} latest news announcement {'results' if en_name else ''} {year}".rstrip()},
        ]
    elif domain_key == "stock_financial":
        queries = [
            {"query": f"{entity} revenue net profit gross margin ROE {prev_year}"},
            {"query": f"{entity} 年报 营收 净利润 毛利率 PE PB {prev_year}"},
            {"query": f"{entity} free cash flow operating margin balance sheet {prev_year}"},
            {"query": f"{entity} analyst rating target price consensus {year}"},
        ]
    elif domain_key == "industry_competition":
        queries = [
            {"query": f"{entity} 行业 竞争格局 市场份额 {year}"},
            {"query": f"{entity} industry competitive landscape market share"},
            {"query": f"{entity} 竞争对手 对比 {year}"},
        ]
    elif domain_key == "business_moat":
        queries = [
            {"query": f"{entity} business model competitive advantage moat"},
            {"query": f"{entity} 商业模式 护城河 差异化"},
            {"query": f"{entity} revenue breakdown segment product line {prev_year}"},
        ]
    elif domain_key == "valuation_debate":
        queries = [
            {"query": f"{entity} DCF valuation comparable PE PS target price {year}"},
            {"query": f"{entity} 估值 目标价 机构评级 {year}"},
        ]
    elif domain_key == "risk_catalyst":
        queries = [
            {"query": f"{entity} risk bear case downside {year}"},
            {"query": f"{entity} 风险 挑战 负面 {year}"},
            {"query": f"{entity} upcoming catalyst event earnings date {year}"},
        ]

    return queries[:max_queries]


def _bp_queries(
    domain_key: str,
    entity: str,
    year: str,
    max_queries: int,
) -> list[dict[str, Any]]:
    """Generate BP-specific queries per domain."""
    queries: list[dict[str, Any]] = []

    if domain_key == "company_background":
        queries = [
            {"query": f"{entity} 公司 工商 信息"},
            {"query": f"{entity} 融资 轮次 估值"},
            {"query": f"{entity} 创始人 团队 背景"},
        ]
    elif domain_key == "latest_news":
        queries = [
            {"query": f"{entity} 融资 产品 合作 最新"},
            {"query": f"{entity} 发布 上线 签约 {year}"},
            {"query": f"{entity} latest funding product announcement"},
        ]
    elif domain_key == "industry_market":
        queries = [
            {"query": f"{entity} 行业 市场规模 增速 {year}"},
            {"query": f"{entity} 赛道 竞争 格局"},
            {"query": f"{entity} industry market size TAM SAM growth"},
            {"query": f"{entity} 产业政策 监管 合规"},
        ]
    elif domain_key == "competitor_check":
        queries = [
            {"query": f"{entity} 竞争对手 对比 分析"},
            {"query": f"{entity} competitors comparison review"},
            {"query": f"{entity} 竞品 融资 数据"},
        ]
    elif domain_key == "tech_validation":
        queries = [
            {"query": f"{entity} 技术 专利 壁垒"},
            {"query": f"{entity} technology patent IP"},
        ]
    elif domain_key == "risk_signal":
        queries = [
            {"query": f"{entity} 风险 诉讼 纠纷 负面"},
            {"query": f"{entity} controversy lawsuit risk"},
        ]

    return queries[:max_queries]


def _ic_queries(
    domain_key: str,
    entity: str,
    topic_metadata: dict[str, Any] | None,
    target_companies: list[str] | None,
    year: str,
    max_queries: int,
) -> list[dict[str, Any]]:
    """Generate IC topic-driven queries per domain."""
    queries: list[dict[str, Any]] = []

    core_q = (topic_metadata or {}).get("core_question", entity)
    sub_qs = (topic_metadata or {}).get("sub_questions", [])
    companies = target_companies or (topic_metadata or {}).get("key_companies", [])

    if domain_key == "latest_news":
        # Extract keywords from topic for news search
        keywords = _extract_keywords(entity, core_q)
        for kw in keywords[:max_queries]:
            queries.append({"query": f"{kw} 最新 动态 {year}"})
        queries.append({"query": f"{entity} latest news developments {year}"})

    elif domain_key == "industry_overview":
        queries = [
            {"query": f"{entity} 行业 市场规模 增速 {year}"},
            {"query": f"{entity} 产业链 格局 趋势 {year}"},
            {"query": f"{entity} industry market size competitive landscape {year}"},
            {"query": f"{entity} 行业 龙头 排名 TOP10 {year}"},
        ]

    elif domain_key == "topic_deep_dive":
        # Generate queries from core question and sub-questions
        core_keywords = _extract_keywords(entity, core_q)[:3]
        for ck in core_keywords:
            queries.append({"query": f"{ck} {year}"})

        for sq in sub_qs[:max_queries - len(core_keywords) - 1]:
            query_text = _question_to_query(sq)
            if query_text:
                queries.append({"query": f"{query_text} {year}"})

    elif domain_key == "company_search":
        for comp in companies[:max_queries]:
            queries.append({"query": f"{comp} {entity} 业务 市场 {year}"})

    elif domain_key == "financial_benchmarks":
        queries = [
            {"query": f"{entity} 行业 ROE 毛利率 净利率 {year}"},
            {"query": f"{entity} 板块 PE PB 估值 中枢 {year}"},
            {"query": f"{entity} sector financial metrics average {year}"},
        ]

    elif domain_key == "policy_scan":
        queries = [
            {"query": f"{entity} 产业政策 法规 监管 {year}"},
            {"query": f"{entity} industry policy regulation {year}"},
        ]

    return queries[:max_queries]


def _lit_queries(
    domain_key: str,
    entity: str,
    sub_topics: list[str] | None,
    target_companies: list[str] | None,
    year: str,
    max_queries: int,
) -> list[dict[str, Any]]:
    """Generate Lit-specific queries per domain."""
    queries: list[dict[str, Any]] = []
    topics = sub_topics or [entity]
    companies = target_companies or []

    if domain_key == "academic_search":
        for st in topics[:max_queries]:
            queries.append({"query": f"{st} review survey state of the art {year}"})
            queries.append({"query": f"{st} breakthrough recent advances {year}"})

    elif domain_key == "industry_news":
        for st in topics[:max_queries]:
            queries.append({"query": f"{st} 产业化 商业化 进展 {year}"})
            queries.append({"query": f"{st} company funding product launch {year}"})

    elif domain_key == "company_tracking":
        for comp in companies[:max_queries]:
            queries.append({"query": f"{comp} {entity} product pipeline {year}"})

    elif domain_key == "patent_scan":
        for st in topics[:max_queries]:
            queries.append({"query": f"{st} patent technology IP {year}"})

    return queries[:max_queries]


def _extract_keywords(entity: str, text: str) -> list[str]:
    """Extract search keywords from text combined with entity."""
    keywords = [entity]
    if text and text != entity:
        # Extract key noun phrases
        words = re.findall(r"[\u4e00-\u9fff\w]+", text)
        seen = {entity}
        for w in words:
            if len(w) >= 2 and w not in seen:
                keywords.append(f"{entity} {w}")
                seen.add(w)
                if len(keywords) >= 6:
                    break
    return keywords


def _question_to_query(question: str) -> str:
    """Convert a Chinese research question to a search query."""
    # Strip question marks and common question prefixes
    q = re.sub(r"[？?]$", "", question.strip())
    q = re.sub(r"^(是否|会不会|能否|如何|什么是|怎么看)", "", q)
    return q.strip() or question.strip()


# ═══════════════════════════════════════════════════════════
# Search executor: call per-source search
# ═══════════════════════════════════════════════════════════

def execute_presearch(
    pipeline: str,
    task_id: str,
    entity: str,
    market: str = "cn",
    ticker: str = "",
    query: str = "",
    topic_metadata: dict[str, Any] | None = None,
    sub_topics: list[str] | None = None,
    target_companies: list[str] | None = None,
    english_name: str = "",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Full presearch execution: generate queries → search → output results + summary.

    Returns dict with 'results' (per-domain search memos) and 'summary' (structured summary).
    """
    queries = generate_queries(
        pipeline=pipeline,
        entity=entity,
        market=market,
        ticker=ticker,
        query=query,
        topic_metadata=topic_metadata,
        sub_topics=sub_topics,
        target_companies=target_companies,
        english_name=english_name,
    )

    tasks_dir = output_dir or TASKS_DIR
    tasks_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    sources_cfg = load_sources_config()

    # Group queries by domain
    domain_results: dict[str, Any] = {}
    total_evidence = 0

    for q in queries:
        domain = q["domain"]
        query_text = q["query"]
        active_sources = q.get("sources", ["web_search"])

        if domain not in domain_results:
            domain_results[domain] = {
                "label": domain,
                "queries": [],
                "total_accepted": 0,
                "memo_lines": [],
                "citations": {},
                "citation_counter": 1,
            }
        dr = domain_results[domain]

        # For tencent_news source
        if "tencent_news" in active_sources:
            try:
                news_results = _search_tencent_news(query_text)
                if news_results:
                    dr["memo_lines"].append(f"### [腾讯新闻] {query_text[:120]}")
                    dr["memo_lines"].append("")
                    for nr in news_results:
                        dr["citations"][str(dr["citation_counter"])] = nr.get("url", "")
                        dr["memo_lines"].append(f"- {nr.get('title', '')}")
                        if nr.get("summary"):
                            dr["memo_lines"].append(f"  > {nr['summary'][:300]}")
                        dr["citation_counter"] += 1
                        dr["total_accepted"] += 1
                    dr["memo_lines"].append("")
            except Exception as e:
                dr["memo_lines"].append(f"⚠ 腾讯新闻搜索失败: {str(e)[:100]}")

        # For web_search source (can combine with tencent_news)
        if "web_search" in active_sources or any(
            s in active_sources for s in ["neodata"]
        ):
            try:
                from scripts.search_gateway import search as gateway_search
                from scripts.search_gateway import neodata_search, verify_engines

                # 如果还没初始化 engines，先检查
                engines = verify_engines() if hasattr(verify_engines, '__call__') else {}

                rows = gateway_search(query_text, max_results=8, timeout=20)
                if rows:
                    dr["memo_lines"].append(f"### [Web] {query_text[:120]}")
                    dr["memo_lines"].append("")
                    for row in rows:
                        title = row.get("title", "") or ""
                        url = row.get("url", "") or ""
                        snippet = row.get("content", "") or row.get("snippet", "") or ""
                        engine = row.get("engine", "web")
                        if url:
                            dr["citations"][str(dr["citation_counter"])] = url
                            dr["memo_lines"].append(f"- [{engine}] [{title}]({url})")
                            if snippet:
                                dr["memo_lines"].append(f"  > {snippet[:300]}")
                            dr["citation_counter"] += 1
                            dr["total_accepted"] += 1
                    dr["memo_lines"].append("")
            except Exception as e:
                dr["memo_lines"].append(f"⚠ Web搜索失败: {str(e)[:100]}")

        dr["queries"].append(query_text)
        total_evidence += dr["total_accepted"]

    # Write per-domain results
    results: dict[str, dict[str, Any]] = {}
    for domain, dr_data in domain_results.items():
        output_path = tasks_dir / f"{task_id}-presearch-{domain}.md"
        if dr_data["total_accepted"] > 0 or not output_path.exists():
            _write_domain_result(output_path, domain, entity, dr_data, pipeline)
        results[domain] = {
            "status": "ok",
            "path": str(output_path),
            "accepted_count": dr_data["total_accepted"],
            "query_count": len(dr_data["queries"]),
        }

    # Build summary
    summary = _build_summary(results, domain_results, entity, total_evidence)
    summary_path = tasks_dir / f"{task_id}-presearch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Write consolidated results
    results_path = tasks_dir / f"{task_id}-presearch_results.json"
    results_path.write_text(json.dumps(
        {"task_id": task_id, "entity": entity, "pipeline": pipeline, "results": results, "total_evidence": total_evidence},
        ensure_ascii=False, indent=2,
    ) + "\n", encoding="utf-8")

    full_result = {
        "task_id": task_id,
        "entity": entity,
        "pipeline": pipeline,
        "results": results,
        "summary": summary,
        "total_evidence": total_evidence,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return full_result


def _search_tencent_news(query_text: str) -> list[dict[str, str]]:
    """Search Chinese real-time news via search_gateway. Returns list of {title, url, summary}.

    v4.8.1（2026-07-27）：原直调腾讯新闻 CLI 已废弃（skill 目录失效 + API 积分耗尽），
    改为走 search_gateway.tencent_news_search（CLI 优先，失败自动降级 NeoData doc）。
    """
    try:
        from scripts.search_gateway import tencent_news_search
        results = tencent_news_search(query_text, max_results=5)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": r.get("content", ""),
            }
            for r in results
        ]
    except Exception:
        return []


def _write_domain_result(
    output_path: Path,
    domain: str,
    entity: str,
    dr_data: dict[str, Any],
    pipeline: str,
) -> None:
    """Write per-domain search results as Markdown."""
    lines = [
        f"# Presearch Results: {domain}",
        "",
        f"- Entity: {entity}",
        f"- Pipeline: {pipeline}",
        f"- Queries: {len(dr_data['queries'])}",
        f"- Accepted evidence: {dr_data['total_accepted']}",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Search Memo",
        "",
        "\n".join(dr_data["memo_lines"]) if dr_data["memo_lines"] else "_No search results._",
        "",
        "## Citations",
        "",
    ]
    for idx, url in sorted(dr_data["citations"].items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        lines.append(f"[{idx}] {url}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_summary(
    results: dict[str, Any],
    domain_results: dict[str, Any],
    entity: str,
    total_evidence: int,
) -> dict[str, Any]:
    """Build structured summary for main AI consumption."""
    coverage = {d: r["accepted_count"] for d, r in results.items()}

    # Identify data gaps (domains with < 3 results)
    data_gaps = []
    for domain, dr_data in domain_results.items():
        if dr_data["total_accepted"] < 3:
            data_gaps.append({
                "domain": domain,
                "accepted_count": dr_data["total_accepted"],
                "note": f"{domain} 预搜索覆盖不足，需要子代理深入搜索",
            })

    headline_findings = []
    for domain, dr_data in domain_results.items():
        if dr_data["total_accepted"] > 5:
            headline_findings.append(f"{domain}: {dr_data['total_accepted']} 条证据")

    summary = {
        "entity": entity,
        "total_evidence": total_evidence,
        "coverage": coverage,
        "headline_findings": headline_findings[:5],
        "data_gaps": data_gaps,
        "warning": f"预搜索产出偏低 ({total_evidence} 条)" if total_evidence < 15 else "",
    }
    return summary


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Presearch Query Builder & Executor v1.0")
    ap.add_argument("--pipeline", required=True, choices=["ir", "bp", "ic", "lit"])
    ap.add_argument("--entity", required=True, help="主体名称（公司/行业/课题）")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--market", default="cn")
    ap.add_argument("--ticker", default="")
    ap.add_argument("--query", default="", help="附加查询文本")
    ap.add_argument("--topic-metadata", default="", help="课题元数据 JSON 文件路径（IC 管线）")
    ap.add_argument("--output-dir", default=str(TASKS_DIR))
    ap.add_argument("--generate-only", action="store_true", help="只生成查询计划，不执行搜索")
    args = ap.parse_args()

    topic_metadata = None
    if args.topic_metadata:
        try:
            topic_metadata = json.loads(Path(args.topic_metadata).read_text(encoding="utf-8"))
        except Exception:
            pass

    if args.generate_only:
        queries = generate_queries(
            pipeline=args.pipeline,
            entity=args.entity,
            market=args.market,
            ticker=args.ticker,
            query=args.query,
            topic_metadata=topic_metadata,
        )
        print(json.dumps(queries, ensure_ascii=False, indent=2))
    else:
        result = execute_presearch(
            pipeline=args.pipeline,
            task_id=args.task_id,
            entity=args.entity,
            market=args.market,
            ticker=args.ticker,
            query=args.query,
            topic_metadata=topic_metadata,
            output_dir=Path(args.output_dir),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
