#!/usr/bin/env python3
"""
IC Pre-search — 行业研究预搜索

与 ir_presearch（个股导向）的核心区别：
  - 搜索 query 以行业关键词为核心，不用 ticker / annual report / analyst
  - 搜索维度覆盖：行业概览 / 政策法规 / 产业链 / 竞争格局 / 技术趋势 / 市场规模 / 财务基准 / 估值 / 资本动向
  - 支持从 scope 文件读取 company_list 追加公司名搜索

用法：
    python3 scripts/ic_presearch.py --task-id TASK-XXX --entity "半导体" --market cn
    python3 scripts/ic_presearch.py --task-id TASK-XXX --entity "人工智能" --query "人工智能,英伟达,寒武纪"
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / 'data' / 'tasks'

os.environ.setdefault('SSL_CERT_FILE', '/opt/homebrew/etc/openssl@3/cert.pem')
os.environ.setdefault('REQUESTS_CA_BUNDLE', '/opt/homebrew/etc/openssl@3/cert.pem')

CURRENT_YEAR = datetime.now().year
PREV_YEAR = CURRENT_YEAR - 1

# ═══════════════════════════════════════════════════════════
# 行业导向搜索 Query 模板
# {industry} = 行业名（如"半导体"、"人工智能"）
# {year} / {prev_year} = 年份
# ═══════════════════════════════════════════════════════════

IC_STEP_QUERIES = {
    # ── Wave 1: 行业全景 ──
    'step_ind_overview': [
        '{industry} 行业 概览 定义 分类 发展阶段 {year}',
        '{industry} industry overview market size growth {year}',
        '{industry} 行业 核心驱动力 发展趋势 {year}',
        '{industry} industry drivers trends outlook {year}',
        '{industry} 行业 现状 产值 从业人数 {year}',
        '{industry} industry value chain ecosystem map',
        '{industry} 国民经济行业分类 申万 中信 行业代码',
    ],
    'step_policy_scan': [
        '{industry} 产业政策 十四五 规划 {year}',
        '{industry} policy regulation government support subsidy {year}',
        '{industry} 监管 准入 牌照 合规 要求',
        '{industry} industry regulation compliance standard {year}',
        '{industry} 补贴 税收优惠 扶持政策 {year}',
        '{industry} trade policy tariff export control {year}',
        '{industry} 行业标准 国家标准 GB {year}',
        '{industry} 国务院 发改委 工信部 政策文件 {year}',
    ],
    'step_value_chain': [
        '{industry} 产业链 上中下游 结构 {year}',
        '{industry} value chain upstream downstream analysis',
        '{industry} 产业链 利润分配 价值链 议价能力',
        '{industry} industry profit pool value chain margin analysis',
        '{industry} 供应链 关键环节 卡脖子 瓶颈',
        '{industry} supply chain bottleneck critical nodes',
        '{industry} 产业链 关键公司 龙头 企业 {year}',
        '{industry} vertical integration horizontal expansion trend',
    ],

    # ── Wave 2: 每环节 × 竞争/技术/市场 ──
    'step_competitive': [
        '{industry} 竞争格局 市场份额 CR3 CR5 集中度 {year}',
        '{industry} competitive landscape market share concentration {year}',
        '{industry} 行业壁垒 进入壁垒 护城河 技术 资金 规模',
        '{industry} industry barriers to entry moat differentiation',
        '{industry} 波特五力 供应商 客户 替代品 竞争',
        '{industry} 龙头企业 排名 TOP10 市占率 {year}',
        '{industry} price war overcapacity competition intensity',
    ],
    'step_tech': [
        '{industry} 技术路线 技术趋势 迭代方向 {year}',
        '{industry} technology roadmap innovation trend {year}',
        '{industry} 专利 核心技术 技术壁垒 知识产权',
        '{industry} patent landscape technology barrier IP {year}',
        '{industry} 国产替代 自主可控 进口替代 进度 {year}',
        '{industry} import substitution domestic replacement progress',
        '{industry} 颠覆性技术 新技术 替代威胁 {year}',
        '{industry} disruptive technology alternative threat {year}',
    ],
    'step_market': [
        '{industry} 市场规模 TAM SAM SOM {year}',
        '{industry} market size forecast growth rate CAGR {year}',
        '{industry} 市场空间 测算 自上而下 自下而上',
        '{industry} addressable market segment sizing methodology',
        '{industry} 细分市场 场景 增量 存量 {year}',
        '{industry} market segmentation scenario analysis {year}',
        '{industry} 市场规模 乐观 中性 保守 预测 {year}',
    ],

    # ── Wave 3: 每环节 × 财务/估值/资本 ──
    'step_financial': [
        '{industry} 行业 财务指标 ROE 毛利率 净利率 周转率 {year}',
        '{industry} industry average financial metrics margin ROE {year}',
        '{industry} 行业 资产负债率 现金流 资本开支 {year}',
        '{industry} industry balance sheet cash flow capex ratio',
        '{industry} 上市公司 财报 营收增速 利润增速 {prev_year}',
        '{industry} sector earnings growth revenue trend {prev_year}',
    ],
    'step_valuation': [
        '{industry} 行业 估值 PE PB PS 中枢 分位数 {year}',
        '{industry} industry valuation multiple PE PB PS {year}',
        '{industry} 板块 估值 历史 分位 当前 水位',
        '{industry} sector valuation historical percentile current level',
        '{industry} 可比公司 估值法 DCF 可比交易',
        '{industry} comparable company valuation relative valuation',
    ],
    'step_capital': [
        '{industry} 融资 投资 IPO 并购 {year}',
        '{industry} venture capital PE VC funding investment {year}',
        '{industry} 一级市场 融资事件 融资金额 轮次 {year}',
        '{industry} startup unicorn funding round series {year}',
        '{industry} IPO 上市 排队 审核 募资 {year}',
        '{industry} M&A acquisition deal activity {year}',
        '{industry} 头部机构 投资 布局 红杉 高瓴 {year}',
    ],

    # ── Wave 4-5: 综合 ──
    'step_cross_chain': [
        '{industry} 产业链 利润迁移 议价能力变化 垂直整合 {year}',
        '{industry} value chain profit migration bargaining power shift',
        '{industry} 垂直整合 横向扩张 一体化 趋势 {year}',
    ],
    'step_investment': [
        '{industry} 投资机会 一级市场 二级市场 配置 {year}',
        '{industry} investment opportunity sector allocation {year}',
        '{industry} 独角兽 明星项目 创业公司 {year}',
        '{industry} stock picks sector overweight underweight {year}',
    ],
    'step_risk': [
        '{industry} 行业风险 挑战 不确定性 {year}',
        '{industry} industry risks challenges uncertainty {year}',
        '{industry} 周期性 政策风险 技术风险 竞争风险',
        '{industry} cyclical risk policy risk technology disruption',
    ],
}

# ── 公司名追加搜索模板 ──
# 如果 scope 中提供了 company_list，对每个公司追加一轮搜索
_COMPANY_QUERIES = [
    '{company} {industry} 市场份额 业务 营收 {year}',
    '{company} competitive position market share {industry} {year}',
]


def run_ic_presearch(
    task_id: str,
    entity: str,
    market: str = 'cn',
    query: str = '',
    steps: list[str] | None = None,
) -> dict:
    """
    行业研究预搜索主函数。

    与 IR presearch 的区别：
    1. 搜索 query 以行业名+行业关键词为核心，不用 ticker / annual report / analyst
    2. 从 scope 文件读取 company_list，追加公司名搜索
    3. step 名与 IC 管线 wave step 对齐
    """
    sys.path.insert(0, str(ROOT))
    from scripts.search_gateway import search as gateway_search

    if steps is None:
        steps = list(IC_STEP_QUERIES.keys())

    # 读取 scope 文件，提取 company_list
    scope_path = TASKS_DIR / f'{task_id}-ic_scope.json'
    company_list: list[str] = []
    if scope_path.exists():
        try:
            scope = json.loads(scope_path.read_text(encoding='utf-8'))
            company_list = scope.get('company_list', [])
        except Exception:
            pass
    # 也从 query 解析公司名
    if query and query != entity:
        import re
        parts = re.split(r'[,，、\s]+', query)
        for p in parts:
            p = p.strip()
            if p and p != entity and p not in company_list:
                company_list.append(p)

    results = {}
    total_evidence = 0

    for step_name in steps:
        queries = IC_STEP_QUERIES.get(step_name, [])
        if not queries:
            continue

        # 替换模板变量
        def fmt(q):
            return q.format(industry=entity, year=CURRENT_YEAR, prev_year=PREV_YEAR)

        MAX_QUERIES_PER_STEP = 6  # 行业搜索维度更聚焦，6 条够用
        all_queries = [fmt(q) for q in queries[:MAX_QUERIES_PER_STEP]]

        # 公司名追加搜索（competitive / financial / capital step 才加）
        company_queries = []
        if step_name in ('step_competitive', 'step_financial', 'step_capital') and company_list:
            for company in company_list[:5]:  # 最多 5 家公司
                for q_tmpl in _COMPANY_QUERIES:
                    company_queries.append(
                        q_tmpl.format(company=company, industry=entity, year=CURRENT_YEAR)
                    )
            # 合并到 all_queries，但不超过总上限
            all_queries.extend(company_queries[:4])
            all_queries = all_queries[:MAX_QUERIES_PER_STEP + 4]

        output_path = TASKS_DIR / f'{task_id}-search-{step_name}.md'
        if output_path.exists() and output_path.stat().st_size > 500:
            results[step_name] = {'status': 'cached', 'path': str(output_path)}
            continue

        all_memo_lines = []
        total_accepted = 0
        all_citations = {}
        citation_counter = 1

        print(f'  📡 [IC] {step_name}: {len(all_queries)} queries ...', flush=True)
        for i, single_query in enumerate(all_queries):
            try:
                rows = gateway_search(single_query, max_results=8, timeout=20)
                if rows:
                    all_memo_lines.append(f'### [{i+1}] {single_query[:120]}')
                    all_memo_lines.append('')
                    for row in rows:
                        title = row.get('title', '') or ''
                        url = row.get('url', '') or ''
                        snippet = row.get('content', '') or row.get('snippet', '') or ''
                        engine = row.get('engine', '?')
                        if url:
                            all_citations[str(citation_counter)] = url
                            all_memo_lines.append(f'- [{engine}] [{title}]({url})')
                            if snippet:
                                all_memo_lines.append(f'  > {snippet[:300]}')
                            citation_counter += 1
                            total_accepted += 1
                    all_memo_lines.append('')
            except Exception as e:
                all_memo_lines.append(f'⚠ Query {i+1} 失败: {str(e)[:100]}')

            if i < len(all_queries) - 1:
                time.sleep(0.5)

        lines = [
            f'# IC Pre-search Results: {step_name}',
            f'',
            f'- Industry: {entity}',
            f'- Market: {market}',
            f'- Queries: {len(all_queries)}',
            f'- Accepted evidence: {total_accepted}',
            f'- Generated: {datetime.now().isoformat(timespec="seconds")}',
            f'',
            f'## Search Memo',
            f'',
            '\n'.join(all_memo_lines) if all_memo_lines else '_No search results._',
            f'',
            f'## Citations',
            f'',
        ]
        for idx, url in sorted(all_citations.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            lines.append(f'[{idx}] {url}')

        output_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        total_evidence += total_accepted
        results[step_name] = {
            'status': 'ok',
            'path': str(output_path),
            'accepted_count': total_accepted,
            'memo_length': sum(len(l) for l in all_memo_lines),
            'query_count': len(all_queries),
        }

    summary = {
        'task_id': task_id,
        'entity': entity,
        'market': market,
        'pipeline': 'ic',
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'steps': results,
        'total_evidence': total_evidence,
        'company_list': company_list,
    }

    if total_evidence < 20:
        summary['warning'] = f'预搜索产出偏低 ({total_evidence} 条证据)，可能影响后续分析质量'

    return summary


def main():
    ap = argparse.ArgumentParser(description='IC 行业研究预搜索')
    ap.add_argument('--task-id', required=True)
    ap.add_argument('--entity', required=True, help='行业名称')
    ap.add_argument('--market', default='cn')
    ap.add_argument('--query', default='', help='附加关键词或公司名')
    ap.add_argument('--steps', nargs='*', help='指定 step（默认全部）')
    args = ap.parse_args()

    result = run_ic_presearch(args.task_id, args.entity, args.market, args.query, args.steps)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
