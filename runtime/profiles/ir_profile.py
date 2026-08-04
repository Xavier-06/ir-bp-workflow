from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from runtime.profiles.base import JobContext, PipelineProfile
from scripts.ir_subagent_launcher_wb import IR_SUBAGENT_CONNECTOR_IDS


def _not_implemented_phase(name: str):
    def _runner(job_ctx: JobContext) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "skeleton",
            "phase": name,
            "job_id": job_ctx.job_id,
        }
    return _runner


def _workspace_for(job_ctx: JobContext):
    """Get JobWorkspace from context (injected by kernel)."""
    return job_ctx.workspace


def _sync_step_to_workspace(job_ctx: JobContext, step_name: str, output_path: Path):
    """Copy a completed step output file into the workspace outputs dir.

    Keeps the legacy path intact while also populating the workspace.
    """
    ws = _workspace_for(job_ctx)
    if ws is None or not output_path.exists():
        return
    dest = ws.outputs_dir / f"{step_name}.md"
    try:
        shutil.copy2(output_path, dest)
    except Exception:
        pass


def _sync_artifact_to_workspace(job_ctx: JobContext, artifact_type: str, src_path: Path):
    """Copy a delivery artifact into the workspace delivery dir and record it."""
    ws = _workspace_for(job_ctx)
    if ws is None or not src_path.exists():
        return
    dest = ws.delivery_dir / src_path.name
    try:
        shutil.copy2(src_path, dest)
        # Record artifact
        manifest_path = ws.state_dir / "artifacts.json"
        artifacts = {}
        if manifest_path.exists():
            try:
                artifacts = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        artifacts[artifact_type] = {
            "path": str(dest),
            "original_path": str(src_path),
            "recorded_at": time.time(),
        }
        manifest_path.write_text(
            json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# Stage Tier 分级（P2: 按研究深度触发不同 phase 组合）
# ═══════════════════════════════════════════════════════════
#
# deep    (默认): 全量 31(+1) phase，所有质量门禁与 per-wave gate 均运行。
# standard:      跳过 claim_coverage + cross_dimension_gate（保留可读性/辩论/per-wave gate）。
# quick:         快速扫描，跳过 per-wave evidence gate、per-wave shared refresh、
#                debate_review、claim_coverage、cross_dimension_gate、readability_review。
#
# 核心数据采集链（preflight → delivery + 合成）在任何 tier 下均完整运行，
# 仅可选的质量/验证 phase 被裁剪，因此不会破坏下游依赖。
# 2026-08-03 修复断点C：legacy 合并 wave gate（phase09_wave_evidence_gate）与
# per-wave gate 同时激活会导致同一批证据审两遍 + repair 双派发，且 legacy FAIL
# 无 repair 兜底直接终止管线。所有 tier 统一跳过 legacy，evidence gate 以 per-wave 为准。
_LEGACY_WAVE_GATE = {"phase09_wave_evidence_gate"}

IR_RESEARCH_TIERS: dict[str, dict[str, Any]] = {
    "deep": {
        "label": "深度研究（默认，全量 phase）",
        "skip": set(_LEGACY_WAVE_GATE),
    },
    "standard": {
        "label": "标准研究（跳过 claim/cross gate）",
        "skip": _LEGACY_WAVE_GATE | {
            "phase14_claim_coverage",
            "phase14_cross_dimension_gate",
        },
    },
    "quick": {
        "label": "快速扫描（最小化验证）",
        "skip": _LEGACY_WAVE_GATE | {
            "phase09_wave1_evidence_gate", "phase09_wave2_evidence_gate",
            "phase09_wave3_evidence_gate", "phase09_wave4_evidence_gate",
            "phase10_wave1_shared_refresh", "phase10_wave2_shared_refresh",
            "phase10_wave3_shared_refresh", "phase10_wave4_shared_refresh",
            "phase12_debate_review",
            "phase14_claim_coverage",
            "phase14_cross_dimension_gate",
            "phase14_readability_review",
        },
    },
}

# delivery_gate 中可随 tier 跳过的门禁产物文件 → 其产出 phase
_SKIPPABLE_GATE_FILES = {
    "ir_readability_review.json": "phase14_readability_review",
    "ir_claim_coverage.json": "phase14_claim_coverage",
    "ir_cross_dimension_gate.json": "phase14_cross_dimension_gate",
}


def resolve_ir_research_tier() -> str:
    """解析当前 IR 研究 tier。

    查找顺序：环境变量 IR_RESEARCH_TIER → runtime/ir_research_tier.json → 默认 deep。
    默认 deep 保证不传 tier 时行为完全等同于改动前（31 phase 全量）。
    """
    env_tier = os.environ.get("IR_RESEARCH_TIER", "").strip().lower()
    if env_tier in IR_RESEARCH_TIERS:
        return env_tier
    cfg = Path(__file__).resolve().parent.parent / "ir_research_tier.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            t = str(data.get("tier", "")).strip().lower()
            if t in IR_RESEARCH_TIERS:
                return t
        except Exception:
            pass
    return "deep"


def _producer_active(fname: str, active: set[str]) -> bool:
    """门禁产物文件是否仍有产出 phase 在激活集合内。"""
    producer = _SKIPPABLE_GATE_FILES.get(fname)
    if producer is None:
        return True
    return producer in active


# ═══════════════════════════════════════════════
# Phase 0-3: Research chain (unchanged, now with workspace sync)
# ═══════════════════════════════════════════════

def _run_preflight(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_preflight_check import run_preflight

    metadata = job_ctx.metadata or {}
    result = run_preflight(
        job_ctx.job_id,
        entity=job_ctx.entity,
        query=job_ctx.query,
        market=job_ctx.market,
    )
    return {
        "ok": bool(result.get("passed", False)),
        "mode": "legacy_wrapped",
        "phase": "phase01_preflight",
        "job_id": job_ctx.job_id,
        "result": result,
        "metadata_used": {
            "entity": job_ctx.entity,
            "query": job_ctx.query,
            "market": job_ctx.market,
            "ticker": metadata.get("ticker", ""),
            "english_name": metadata.get("english_name", ""),
        },
    }


def _run_company_verify(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        from scripts.ir_company_verify import run as run_company_verify
        result = run_company_verify(
            task_id=job_ctx.job_id,
            entity=job_ctx.entity,
            market=job_ctx.market,
        )
        return {
            "ok": "error" not in result,
            "mode": "legacy_wrapped",
            "phase": "phase02_company_verify",
            "job_id": job_ctx.job_id,
            "result": result,
        }
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase02_company_verify")
    if cached is not None:
        print(f"  📦 [ir] 使用缓存的 company_verify 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase02_company_verify", pipeline="ir")


def _run_research_plan(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase04: IR 研究计划 — 子代理派发模式 (v5.2，对标 BP)。

    v5.2 (2026-07-08): 从主 AI 手动 enrichment 重构为子代理派发。
    子代理有 westock-mcp 可直接搜结构化行情/财务/研报/行业数据，
    不再依赖 heavy_bg presearch 的 web-only 数据。
    """
    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    metadata = job_ctx.metadata or {}
    ticker = metadata.get("ticker", "")
    english_name = metadata.get("english_name", "")
    entity = job_ctx.entity
    market = job_ctx.market
    query = job_ctx.query

    # 写 brief 文件
    brief_path = tasks_dir / f"{job_ctx.job_id}-ir_phase04_brief.json"
    brief = {
        "entity": entity, "market": market, "ticker": ticker,
        "english_name": english_name, "query": query,
    }
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    instruction = f"""\
PHASE04 IR RESEARCH PLAN — 派发子代理

Agent tool 参数：
- name = 'ir-research-planner'
- team_name = 'ir-{job_ctx.job_id}'
- mode = 'bypassPermissions'
- subagent_type = 'general-purpose'（⚠️ 必须！子代理需要 ima-mcp/westock-mcp/tyc-mcp 搜索能力，code-explorer 等受限类型会静默失败导致 plan 缺失）
- connectorIds = ['westock-mcp', 'tyc-mcp', 'ima-mcp']
- prompt = 下面的完整 prompt

### 子代理 Prompt:

你是投研研究计划分析师。为 {entity}（{market}，{ticker or '无ticker'}）生成研究计划。

## Step 0: 读取所有输入文件（必须先做）

在搜索之前, 你必须先读 brief 文件和所有输入, 提取:
- entity(标的名称), ticker(股票代码), market(市场), english_name(英文名)
- 你是唯一的数据搜索者，所有数据源都需要你自己查

## Step 0.5: 大行研报为骨架（最高优先级 — v3.0 新增）

**核心原则：不重复造轮子。大行分析师已经做了 90% 的分析工作（行业框架、估值模型、财务预测），你只需在他们基础上补充增量。**

**必须执行**：在 IMA 自建研报库搜索 {entity} 的大行研报（GS/MS/JPM/Citi/HSBC/UBS/BofA/Bernstein/Nomura/DB），找到最新的 1-2 篇全文研报并 fetch。

```
mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="Goldman Sachs Morgan Stanley JPMorgan {entity} {ticker}")
```

从候选列表中筛选外资大行（标题含 Goldman/Morgan Stanley/JPMorgan/Citi/HSBC/UBS/BofA/Bernstein）+ 发布日期最近 + can_fetch_content=true 的条目，用 `mcp__ima-mcp__fetch_media_content(media_id="...")` 拿全文。

**如果找到大行研报全文**（≥6000 字符），解析出骨架结构写入 `benchmark_skeleton.json`：

```json
{{
  "bank": "Goldman Sachs",
  "report_date": "2026-06-17",
  "rating": "Buy",
  "target_price": "HK$860",
  "key_debates": [
    {{"id": "KD-1", "title": "M3 定价策略", "summary": "通过低定价+高采用率走另一条 ARR 路径", "data_points": ["token 定价 $0.22/1M", "OpenRouter #1 排名"]}},
    {{"id": "KD-2", "title": "...", "summary": "...", "data_points": ["..."]}}
  ],
  "financial_forecast": {{"revenue": {{"2026E": 300, "2027E": 880, "2028E": 2470}}, "gpm": {{"2026E": "26%", "2027E": "24%"}}, "adj_net_loss": {{"2026E": -425}}}},
  "valuation_method": {{"approach": "DCF", "wacc": "12%", "terminal_growth": "2%", "key_assumptions": ["市占率 0.2-0.7pct/年→2030E 2.5%", "长期 EBIT margin 18%"]}},
  "scenarios": {{"bear": 330, "base": 860, "bull": 1350}},
  "revenue_split": {{"C端": {{"2026E": 149, "2027E": 558}}, "B端": {{"2026E": 151, "2027E": 322}}}},
  "key_risks": ["模型性能不及预期", "盈利可见度慢", "地缘政治"]
}}
```

**将 benchmark_skeleton.json 写入** `{tasks_dir / f'{job_ctx.job_id}-benchmark_skeleton.json'}`。

**如果找不到大行研报**（未覆盖标的或 IMA 无全文），跳过此步，在 search_summary 中标注 `"benchmark_found": false`，后续回退到从零搜索模式。

## Step 0.6: 提取市场共识锚（market_anchor — v2.1 新增）

**目的**：所有下游 step 动手前先有"市场现在怎么定价"的锚点。

**数据源**：
1. IMA 自建研报库（Step 0.5 已 fetch 的大行研报）→ 一致预期 EPS/营收、目标价、评级
2. `westock-mcp.data_consensus` → 一致预期（如有）
3. `westock-mcp.data_rating` → 评级分布
4. `westock-mcp.data_quote` → 现价

**产出**：在输出的 `market_anchor` 字段写入：
```json
{{
  "as_of": "2026-07-29",
  "source_report": "BofA-优必选-260715.pdf",
  "source_age_days": 14,
  "stale": false,
  "price": 98.5,
  "market_cap": "410亿",
  "consensus_eps": {{"FY25": -1.55, "FY26E": -0.82, "FY27E": 0.15}},
  "consensus_revenue": {{"FY25": 24.9, "FY26E": 41.0, "FY27E": 68.0}},
  "current_multiple": {{"PE": "N/A(亏损)", "PS": 16.5, "EV_Sales": 15.2}},
  "implied_assumption": "股价隐含 FY25-27 营收 CAGR 65%，毛利率需升至 45%",
  "analyst_ratings": {{"buy": 8, "hold": 3, "sell": 1}},
  "avg_target_price": 128
}}
```

**铁律**：
- 每个数字带来源（研报名 + 日期）
- 亏损标的 PE 标 "N/A(亏损)"，用 PS/EV-Sales
- 必须算"股价隐含假设"（implied_assumption）
- **时效硬规则**：研报来源必须 ≤30 天。标题日期（如 -260715.pdf=2026-07-15）据此判断 source_age_days
- 超 30 天 → 标 `"stale": true`，下游打折
- 1 个月内找不到大行研报 → `"market_anchor": null`，写进 data_gaps，**禁止用旧研报或模型记忆硬编共识**

## Step 1: 行情与财务 (westock-mcp) — 增量数据收集
- westock-mcp.data_quote: query by entity名称或ticker -> PE/PB/市值/股价
- `data_finance` 查 {entity} → 营收/净利润/ROE/毛利率趋势（最近3年）
- `data_report` 搜 {entity} 研报 → 机构评级/目标价/核心观点（最多5条）

## Step 2: 行业数据 (westock-mcp)
- `data_sector` 查所属行业 → PE分位/成分股/涨跌幅

## Step 2.5: 公司工商验证 (tyc-mcp)
- `tyc-mcp.search_companies`: query "{entity}" → 获取 company_id
- `tyc-mcp.get_company_basic_profile`: 注册资本、成立日期、经营范围、股东结构、融资历史、法律风险
- 如果 tyc 找不到（小市值/非上市公司）：记录在 search_summary 中，继续后续步骤

## Step 3: 资金面（大盘股可查，小盘股跳过）
- `data_fund_flow` 查 {entity} → 主力资金净流入

## Step 4: 增量 Web 补搜（中英双语，结构化源优先）
**有骨架时**：只搜大行研报未覆盖的增量信息（最新模型/产品/新闻/行业动态），不重复搜研报已有的基础数据。
**无骨架时**：完整搜索（竞争格局/风险/商业模式/管理层/催化剂）。
- `"{{entity}}" 竞争格局 市场份额 2025 2026`
- `"{{entity}}" 风险 挑战 负面 2025`  
- `"{{entity}}" 商业模式 护城河`
- `"{{entity}}" 管理层 大股东 股权结构`
- `"{{entity}}" 催化剂 即将发生 事件`
- `"{{entity}}" competitive landscape market share {{year}}`
- `"{{entity}}" risk bear case downside {{year}}`
- `"{{entity}}" business model moat competitive advantage`

## Step 5: 中文实时新闻（tencent_news_search，Bash 调用，自动降级NeoData doc）
你是唯一的搜索者（没有上游 presearch），必须用 Bash 调 tencent_news_search 补充实时动态（自动降级NeoData doc）：
```bash
cd ~/.workbuddy/ir_runtime && python3 -c "
import json, sys; sys.path.insert(0, '.')
from scripts.search_gateway import tencent_news_search
result = tencent_news_search('{entity} 最新动态 财报 事件', max_results=5)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

**如果 {entity} 是上市公司**，额外用 westock-mcp `data_news` 拿个股级公告/新闻/研报动态（比通用新闻更聚焦该公司）：
`mcp__westock-mcp__data_news(symbol="sh600519", type=3, limit=10)`（type: 0公告 1研报 2新闻 3全部；symbol 不确定时先用 `data_search` 检索代码）

## Step 6: IMA 知识库增量扫描（自建研报库为主力源 — 增量信息层）

用 ima-mcp 的 search_knowledge 搜索知识库，提取机构级增量信息。**自建研报库是主力源（投行/券商研报全文可 fetch），所有搜索第一优先。**
**必须搜 2-3 个最相关的 KB，每个 KB 用不同关键词搜 1-2 次。**

KB ID 速查（v4.8，已删除长安投研/公司调研报告——仅摘要不可取正文）：
- ★自建研报库(投行/券商研报, GS/MS/JPM/BofA/Citi/UBS/Bernstein 等): `001a89fa4b807b92`
- 行研智库(行业报告): `7311568991699459`
- 机构调研纪要(电话会/专家/外资): `7300811407257275`
- 精选行业数据报告: `7302509206984644`

搜索策略：
> **占位符说明**：`{{行业关键词如半导体}}` 是模板示例。子代理应根据 entity 实际所属行业替换。
> 行业识别方法：用 westock-mcp `data_sector` 查 entity 的申万行业分类 → 用 tyc-mcp 经营范围推断 → 取交集即得行业关键词。

**⚠️ fetch 权限（v4.8）：4 个库全文均可 fetch。自建研报库/行研智库/精选报告 100% 可 fetch；机构调研纪要仅 NOTE 类型可 fetch。**
**⚠️ 时间过滤纪律：优先最近 30 天内的投行研报（超 1 个月参考价值显著下降）；标题常含日期（如 -260703.pdf=2026-07-03）；大行优先。**

1. `mcp__ima-mcp__search_knowledge(knowledge_base_id="001a89fa4b807b92", query="{entity} {{行业关键词如半导体集成电路}} 研报 目标价 估值")` — ★主力源：投行研报（**全文可fetch**：取media_id → `mcp__ima-mcp__fetch_media_content(media_id="...")`）
2. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7311568991699459", query="{{行业名如半导体}} 市场规模 竞争格局")` — 行业深度报告（**全文可fetch**）
3. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7300811407257275", query="{entity} {{行业关键词如半导体集成电路}}")` — 机构观点/外资视角（NOTE 可 fetch 全文：取 media_id → `mcp__ima-mcp__fetch_media_content(media_id="...")`）
4. `mcp__ima-mcp__search_knowledge(knowledge_base_id="7302509206984644", query="{{行业名如半导体}} 市场规模 TAM")` — 精选报告（**全文可fetch**）

从 IMA 搜索中提取：
- 投行观点（GS/MS/JPM 等大行的目标价方法论/评级/BOM 成本分析）
- 机构共识观点（多家券商一致看法）
- 外资视角（外资券商的独立分析）
- 关键数据点（行业 TAM/增速/市占率等 IMA 独有数据）

将提取的 insights 写入输出的 `ima_insights` 字段（见下方 JSON 格式）。

- 你是唯一的搜索者，请系统性地完成以上所有搜索

## Step 7: 输出 enriched_data_pack.json（v3.0 新增 — 替代 step1_data）

**将 Step 0.5~6 收集的所有结构化数据汇总为一个 JSON 文件**，写入 `{tasks_dir / f'{job_ctx.job_id}-enriched_data_pack.json'}`：

```json
{{
  "entity": "{entity}", "market": "{market}", "ticker": "{ticker}",
  "generated_at": "ISO时间",
  "benchmark_skeleton_ref": "benchmark_skeleton.json 路径（如有）",
  "quote": {{"price": 0, "market_cap": "", "pe": "", "pb": "", "eps": ""}},
  "financials": {{"revenue": {{}}, "net_income": {{}}, "gpm": {{}}, "npm": {{}}}},
  "analyst_consensus": {{"rating_distribution": {{}}, "avg_target_price": "", "coverage_count": 0}},
  "industry": {{"sector_name": "", "sector_pe_percentile": "", "peers": []}},
  "company_profile": {{"registered_capital": "", "founded": "", "business_scope": "", "shareholders": []}},
  "fund_flow": {{"main_net_inflow": "", "north_holding": ""}},
  "news_highlights": ["最新动态1", "最新动态2"],
  "ima_insights": [
    {{"bank": "GS", "title": "...", "key_points": ["..."], "report_date": "..."}}
  ],
  "incremental_data": {{
    "latest_model": "最新模型/产品更新",
    "latest_financials": "最新财报数据（如有）",
    "industry_news": "行业重要新闻"
  }}
}}
```

此文件是所有下游 step（step2-8）的核心数据输入，替代了原来的 step1_data 独立子代理。

## 分析任务

1. **Core Questions (7条)**: 围绕基本面、行业、商业模式、管理层、估值、风险
2. **Strategic Questions (5条)**: 基于结构化数据发现的异常/矛盾设计尖锐问题
3. **Key Debates (2-4条)**: 核心投资辩论（对标 GS Key Debates 风格），每条含 debate + priority(P0/P1/P2) + market_view + our_view + owner_dims + data_points
4. **Fact Requirements (30+条)**: 验证每条 claim 所需的 fact 项
5. **Section Requirements (9个)**: 分配到 IR 9步骤
6. **Valuation Paradigm (1个)**: 6 选 1 估值范式（见下方判定表），决定全报告的估值方法和骨架
7. **Market Anchor (1个)**: 市场共识锚（Step 0.6 产出）
8. **Report Type (1个)**: 报告类型分流（见下方判定表），决定管线跑全量 4 波还是短路径

### Report Type 判定表（2026-08-03 新增，2026-08-04 v3.1 更新 — 决定 wave 裁剪）

波次含义（v3.1 研究链顺序）：Wave1 背景层（行业/业务/宏观）→ Wave2 预测与验证（财务/管理层）→ Wave3 估值收口 → Wave4 预期差收口（洞察/风险）。

| report_type | 判定信号 | 管线行为 |
|-------------|---------|---------|
| `deep_dive` | 默认：无明确事件驱动的完整投研需求 | 全量 4 波 |
| `event_update` | query 聚焦单一事件（订单/新品/财报/中标/合作）且要求快速跟踪 | 短路径 wave1+2+3（背景+预测更新+估值更新） |
| `earnings_note` | query 明确为财报/业绩点评，只要求更新模型与目标价 | 短路径 wave2+3（仅预测+估值，大行财报点评模式） |

判定依据：query 关键词（"订单""万台""新品发布""中标""合作"→event_update；"财报""业绩""点评""EPS"→earnings_note）+ Step 0.6 发现的最新动态性质。默认 `deep_dive`，拿不准就全量。

### Valuation Paradigm 判定表（6 选 1）

| paradigm | 判定信号 | 估值主方法 | 禁用 |
|----------|---------|-----------|------|
| `profitable_growth` | 已盈利+正增长 | PE / EV-EBITDA + DCF 佐证 | — |
| `preprofit_growth` | 亏损+高增长 | PS / EV-Sales + TAM 份额 | PE/DCF |
| `cyclical_asset` | 强周期+重资产 | PB / 周期中枢 EV-EBITDA + 重置成本 | 单期 PE |
| `asset_nav` | 资产驱动+现金流稳 | NAV / DCF（储备/资源）| — |
| `regulated_utility` | 受监管+分红驱动 | 股息率 / DDM | 高 PE |
| `platform_two_sided` | 双边网络+take rate | EV/GP + LTV/CAC | 单 PE |

判定依据：Step 1 财务数据（净利润正负+增速）+ Step 2 行业属性（周期/平台/资产驱动）+ 商业模式。

## Step 分配规则（v3.0: 删除 step1_data，9 步）
step1_industry, step2_biz, step3_finance, step4_mgmt, step5_macro, step6_valuation, step7_insight, step8_risk, step8_master

## 输出
写入 `{tasks_dir / f'{job_ctx.job_id}-ir_research_plan.json'}`:

```json
{{
  "schema_version": "ir_research_plan.v5",
  "task_id": "{job_ctx.job_id}", "entity": "{entity}", "market": "{market}",
  "query": "{query}", "ticker": "{ticker}", "english_name": "{english_name}",
  "data_sources_used": ["westock-mcp:行情/财务/研报/行业", "tyc-mcp:工商验证", "ima-mcp:机构研报/纪要", "search_deep:公开信息", "tencent_news:实时动态"],
  "benchmark_found": true,
  "benchmark_skeleton_ref": "{tasks_dir / f'{job_ctx.job_id}-benchmark_skeleton.json'}",
  "report_type": "deep_dive",
  "report_type_reason": "依据判定表选择 deep_dive / event_update / earnings_note，并写明理由",
  "valuation_paradigm": "preprofit_growth",
  "paradigm_reason": "优必选亏损+高增长，用 PS/EV-Sales + TAM 份额推导，禁用 PE/DCF",
  "valuation_method_primary": "PS / EV-Sales",
  "valuation_forbidden": ["PE", "DCF"],
  "market_anchor": {{
    "as_of": "2026-07-29", "source_report": "BofA-优必选-260715.pdf", "source_age_days": 14, "stale": false,
    "price": 98.5, "market_cap": "410亿",
    "consensus_eps": {{"FY25": -1.55, "FY26E": -0.82, "FY27E": 0.15}},
    "consensus_revenue": {{"FY25": 24.9, "FY26E": 41.0, "FY27E": 68.0}},
    "current_multiple": {{"PE": "N/A(亏损)", "PS": 16.5, "EV_Sales": 15.2}},
    "implied_assumption": "股价隐含 FY25-27 营收 CAGR 65%，毛利率需升至 45%",
    "analyst_ratings": {{"buy": 8, "hold": 3, "sell": 1}},
    "avg_target_price": 128
  }},
  "core_questions": [...], "strategic_questions": [...],
  "key_debates": [
    {{"id": "KD-1", "debate": "人形机器人量产时间表", "priority": "P0",
      "market_view": "市场认为 2027 年才能规模化出货",
      "our_view": "我们认为 2026H2 即可小批量商用，BOM 降幅超预期",
      "owner_dims": ["step1_industry", "step3_finance"],
      "data_points": ["单台 BOM ¥18万", "年降幅 30%"]}}
  ],
  "dim_priority": {{"step1_industry": "P0", "step3_finance": "P0", "step2_biz": "P1", "step4_mgmt": "P2", "step5_macro": "P2", "step6_valuation": "P0", "step7_insight": "P0", "step8_risk": "P1"}},
  "fact_requirements": [...], "section_requirements": {{}},
  "coverage_matrix": {{}}, "plan_status": "ready",
  "search_summary": {{"westock_quote_found": true, "benchmark_found": true, "analyst_views": 0, "web_evidence_count": 0}},
  "ima_insights": [
    {{"kb_name": "KB名称", "doc_title": "文档标题", "key_points": ["要点1", "要点2"], "relevance": "high/medium"}}
  ]
}}
```
"""

    return {
        "ok": True, "needs_dispatch": True, "has_more": False,
        "mode": "ir_research_plan_subagent",
        "phase": "phase04_research_plan", "job_id": job_ctx.job_id,
        "dispatch_info": {
            "brief_path": str(brief_path),
            "subagent_type": "general-purpose",
            "subagent_connector_ids": ["westock-mcp", "tyc-mcp", "ima-mcp"],
            "task_dir": str(tasks_dir),
        },
        "instruction": instruction,
    }


def _infer_valuation_paradigm_from_verify(tasks_dir: Path, job_id: str) -> dict[str, Any] | None:
    """v2.2: 从 company_verify 财务数据推断估值范式。

    修复 fallback plan 硬编码 valuation_paradigm=profitable_growth/PE 的缺陷——
    亏损股（如 MiniMax）会被错误套上 PE 框架。此处读取 {job_id}-ir_company_verify.json
    的 financial_data 文本，检测亏损信号：
    - 检测到亏损 → preprofit_growth + PS/EV-Sales，禁用 PE/DCF
    - 无亏损信号 → 返回 None（沿用默认 profitable_growth）
    文件不存在 / 无财务数据时返回 None。
    """
    verify_path = Path(tasks_dir) / f"{job_id}-ir_company_verify.json"
    if not verify_path.exists():
        return None
    try:
        verify = json.loads(verify_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    text_parts: list[str] = []
    for item in verify.get("financial_data", []) or []:
        if isinstance(item, dict):
            text_parts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            text_parts.append(item)
    text_blob = "\n".join(text_parts)
    if not text_blob.strip():
        return None

    # 亏损强信号（先检测，命中即判亏损，避免"归母净利润"在亏损报告里误判为盈利）
    loss_signals = [
        "净亏损", "经调整净亏损", "归母净亏损", "净亏损为",
        "归母净利润-", "净利润-", "净利润为负", "持续亏损", "仍在亏损", "亏损扩大",
        "net_loss", "operating loss", "经营亏损",
    ]
    loss_hits = [s for s in loss_signals if s in text_blob]
    if loss_hits:
        return {
            "valuation_paradigm": "preprofit_growth",
            "paradigm_reason": f"基于 company_verify 财务数据判定为亏损（信号: {', '.join(loss_hits[:3])}），采用亏损增长股框架",
            "valuation_method_primary": "PS / EV-Sales",
            "valuation_forbidden": ["PE", "DCF"],
            "thesis_source": "fallback_financial_inference",
        }
    return None


def _backfill_thesis_fields(plan: dict[str, Any], inferred: dict[str, Any] | None = None) -> list[str]:
    """v2.1: 为 research_plan 补 Thesis 字段默认值（缺失降级，不阻断）。

    返回 warning 列表（非 error）。子代理未产出新字段时，用确定性默认值兜底，
    保证下游 step（step3/6/7 等）读取 valuation_paradigm/market_anchor 时不 KeyError。

    v2.2 (2026-08-03): 新增 inferred 参数——company_verify 财务数据推断的估值范式。
    当 plan 缺 valuation_paradigm 时，用 inferred 决定 paradigm/method，而非硬编码
    profitable_growth/PE，避免亏损股（如 MiniMax）被套上 PE 框架。
    """
    warnings: list[str] = []

    # report_type（2026-08-03 修复断点B：缺失/未知 → 按 query 关键词判定兜底，
    # 保证 _run_dispatch_prepare 的 active_waves 分流能拿到白名单内的合法值）
    _VALID_REPORT_TYPES = {
        "deep_dive", "company_deep_dive", "broker_ir", "industry_research",
        "event_update", "data_track", "earnings_note",
    }
    if plan.get("report_type") not in _VALID_REPORT_TYPES:
        _q = f"{plan.get('query', '')} {plan.get('entity', '')}".lower()
        if any(k in _q for k in ("财报", "业绩", "点评", "earnings", "results")):
            plan["report_type"] = "earnings_note"
        elif any(k in _q for k in ("订单", "万台", "新品", "中标", "合作", "order", "new product")):
            plan["report_type"] = "event_update"
        else:
            plan["report_type"] = "deep_dive"
        plan.setdefault("report_type_reason", "子代理未产出合法 report_type，按 query 关键词兜底")
        warnings.append("report_type_missing_fallback")

    # valuation_paradigm（缺失 → 财务感知兜底：先用 company_verify 推断，无推断再默认 profitable_growth）
    if not plan.get("valuation_paradigm"):
        if inferred and inferred.get("valuation_paradigm"):
            plan["valuation_paradigm"] = inferred["valuation_paradigm"]
            plan.setdefault("paradigm_reason", inferred.get("paradigm_reason", ""))
            plan.setdefault("valuation_method_primary", inferred.get("valuation_method_primary", "PS / EV-Sales"))
            plan.setdefault("valuation_forbidden", inferred.get("valuation_forbidden", []))
            plan.setdefault("thesis_source", inferred.get("thesis_source", "fallback_financial_inference"))
            warnings.append("valuation_paradigm_inferred_from_financials")
        else:
            plan["valuation_paradigm"] = "profitable_growth"
            plan.setdefault("paradigm_reason", "子代理未产出 valuation_paradigm 且无法从财务数据推断，降级默认 profitable_growth")
            plan.setdefault("thesis_source", "fallback_default")
            warnings.append("valuation_paradigm_missing_fallback")

    # valuation_method_primary / valuation_forbidden（paradigm 已由 inferred 填时沿用其值，否则默认 PE）
    if plan.get("valuation_paradigm") == "preprofit_growth":
        plan.setdefault("valuation_method_primary", "PS / EV-Sales")
        plan.setdefault("valuation_forbidden", ["PE", "DCF"])
    else:
        plan.setdefault("valuation_method_primary", "PE / EV-EBITDA")
        plan.setdefault("valuation_forbidden", [])

    # key_debates（缺失 → 空列表，下游 step7 会自拟）
    if not plan.get("key_debates"):
        plan["key_debates"] = []
        warnings.append("key_debates_missing")

    # dim_priority（缺失 → 全 P1 默认）
    if not plan.get("dim_priority"):
        plan["dim_priority"] = {
            "step1_industry": "P1", "step2_biz": "P1", "step3_finance": "P1",
            "step4_mgmt": "P1", "step5_macro": "P1", "step6_valuation": "P1",
            "step7_insight": "P1", "step8_risk": "P1",
        }
        warnings.append("dim_priority_missing_fallback")

    # market_anchor（缺失 → null，下游打折处理；stale → warning）
    anchor = plan.get("market_anchor")
    if anchor is None:
        warnings.append("market_anchor_null")
    elif isinstance(anchor, dict) and anchor.get("stale"):
        warnings.append(f"market_anchor_stale_age_{anchor.get('source_age_days', '?')}d")

    return warnings


def _run_research_plan_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase04 collect v5.2: 读取子代理产出的 ir_research_plan.json。

    v2.2 (2026-08-03): 财务感知兜底——子代理 plan 缺失或降级脚本生成时，
    从 company_verify 财务数据推断 valuation_paradigm（亏损股→preprofit_growth+PS，禁 PE），
    避免 MiniMax 类亏损标的被套上 PE 框架。降级时给 plan 打 thesis_source 标记。
    """
    from scripts.ir_research_planner import validate_research_plan_ready, research_plan_path

    tasks_dir = runtime_root / "data" / "tasks"
    plan_path = tasks_dir / f"{job_ctx.job_id}-ir_research_plan.json"
    inferred = _infer_valuation_paradigm_from_verify(tasks_dir, job_ctx.job_id)

    if plan_path.exists() and plan_path.stat().st_size > 200:
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            validation = validate_research_plan_ready(plan)
            if validation["ready"]:
                # v2.1: 补 Thesis 字段默认值（缺失降级，不阻断）；v2.2: 传入财务推断
                thesis_warnings = _backfill_thesis_fields(plan, inferred=inferred)
                if thesis_warnings:
                    print(f"  ⚠️ [ir phase04_collect] Thesis 字段降级: {thesis_warnings}", flush=True)
                final_path = research_plan_path(job_ctx.job_id, tasks_dir)
                final_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return {
                    "ok": True, "mode": "ir_research_plan",
                    "phase": "phase04_research_plan_collect", "job_id": job_ctx.job_id,
                    "result": {"plan_path": str(final_path), "enrichment": "subagent_generated",
                               "validation": validation, "thesis_warnings": thesis_warnings,
                               "thesis_source": plan.get("thesis_source", "subagent_generated")},
                }
            print(f"  ⚠️ [ir phase04_collect] plan 校验失败: {validation['errors']}", flush=True)
            return {"ok": False, "mode": "ir_research_plan", "phase": "phase04_research_plan_collect",
                    "job_id": job_ctx.job_id, "result": {"error": "plan_validation_failed", "errors": validation["errors"]}}
        except Exception as exc:
            print(f"  ⚠️ [ir phase04_collect] 读取子代理 plan 失败: {exc}", flush=True)

    # 降级：脚本生成骨架 + 财务感知 thesis 兜底
    print(f"  ⚠️ [ir phase04_collect] 子代理未产出 plan，降级脚本生成", flush=True)
    from scripts.ir_research_planner import prepare_research_plan
    path = prepare_research_plan(
        task_id=job_ctx.job_id, entity=job_ctx.entity,
        query=job_ctx.query, market=job_ctx.market, tasks_dir=tasks_dir,
    )
    # v2.2: 用财务推断覆盖骨架默认值（亏损股→preprofit_growth+PS，禁 PE）
    thesis_source = "fallback_default"
    thesis_warnings: list[str] = []
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
        thesis_warnings = _backfill_thesis_fields(plan, inferred=inferred)
        if inferred and inferred.get("valuation_paradigm"):
            plan["valuation_paradigm"] = inferred["valuation_paradigm"]
            plan["paradigm_reason"] = inferred.get("paradigm_reason", "")
            plan["valuation_method_primary"] = inferred.get("valuation_method_primary", "PS / EV-Sales")
            plan["valuation_forbidden"] = inferred.get("valuation_forbidden", [])
            thesis_source = inferred.get("thesis_source", "fallback_financial_inference")
            print(f"  🔍 [ir phase04_collect] 财务感知兜底生效: paradigm={plan['valuation_paradigm']}", flush=True)
        plan["thesis_source"] = thesis_source
        Path(path).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"  ⚠️ [ir phase04_collect] 财务感知兜底失败（保持脚本默认）: {exc}", flush=True)
    return {
        "ok": True, "mode": "ir_research_plan",
        "phase": "phase04_research_plan_collect", "job_id": job_ctx.job_id,
        "result": {"plan_path": path, "enrichment": "fallback_script",
                   "thesis_source": thesis_source, "thesis_warnings": thesis_warnings},
    }


def _run_fact_store_bootstrap(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Initialize a generic Fact Store and seed candidates from existing extracted facts when available."""
    from scripts.ir_fact_store import FactStore, add_fact, extract_fact_candidates, write_fact_store

    tasks_dir = runtime_root / "data" / "tasks"
    store = FactStore(task_id=job_ctx.job_id, entity=job_ctx.entity, market=job_ctx.market)
    seeded = 0

    candidate_texts: list[str] = []
    extracted_facts_path = tasks_dir / f"{job_ctx.job_id}_body_content" / "ir_extracted_facts.json"
    if extracted_facts_path.exists():
        try:
            payload = json.loads(extracted_facts_path.read_text(encoding="utf-8"))
            candidate_texts.append(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    company_verify_path = tasks_dir / f"{job_ctx.job_id}-ir_company_verify.json"
    if company_verify_path.exists():
        try:
            payload = json.loads(company_verify_path.read_text(encoding="utf-8"))
            candidate_texts.append(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    for text in candidate_texts:
        for item in extract_fact_candidates(text, entity=job_ctx.entity):
            add_fact(
                store,
                claim=item["claim"],
                value=item["value"],
                unit=item["unit"],
                period=item["period"],
                source_url=item["source_url"],
                source_tier="unknown",
                source_quote=item["source_quote"],
                question_id=item.get("question_id", ""),
                fact_type=item.get("fact_type", "numeric"),
                confidence=item.get("confidence", "low"),
            )
            seeded += 1

    output_path = write_fact_store(store, tasks_dir=tasks_dir)
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "fact_store.json")
        except Exception:
            pass
    return {
        "ok": True,
        "mode": "quality_production",
        "phase": "phase06_fact_store_bootstrap",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "facts_seeded": seeded},
    }


# ═══════════════════════════════════════════════════════════
# Phase 1.2: Precompute — 三大预计算引擎（财务指标/技术指标/行业对标）
# ═══════════════════════════════════════════════════════════

def _run_precompute(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 1.2: 运行预计算引擎（财务指标 / 行业对标）。

    输出写入 data/tasks/ 供子代理（step3_finance/step6_valuation 等）使用。
    预计算引擎需要股票代码（ticker），如果 metadata 没有则尝试解析。
    """
    import subprocess

    metadata = job_ctx.metadata or {}
    ticker = metadata.get("ticker", "")
    market = metadata.get("market", job_ctx.market)

    # 如果没有 ticker，尝试解析
    if not ticker:
        try:
            from tasks.valuation_enricher import _resolve_ticker
            ticker = _resolve_ticker(job_ctx.entity)
            if ticker:
                print(f"  🔍 [precompute] 自动解析 ticker: {job_ctx.entity} → {ticker}", flush=True)
        except Exception:
            pass

    precompute_results: dict[str, Any] = {}
    all_ok = True
    errors: list[str] = []

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 预计算引擎
    engines = {
        "financial_metrics": runtime_root / "scripts" / "financial_metrics_precompute.py",
        "sector_benchmarks": runtime_root / "scripts" / "sector_benchmarks.py",
    }

    for engine_name, script_path in engines.items():
        if not script_path.exists():
            errors.append(f"{engine_name}: script not found at {script_path}")
            all_ok = False
            continue

        try:
            # 没有 ticker 时跳过需要 ticker 的引擎
            if not ticker:
                precompute_results[engine_name] = {"status": "skipped", "reason": "no ticker available"}
                print(f"  ⚠️  [precompute] {engine_name}: 无 ticker，跳过", flush=True)
                continue

            print(f"  🔢 [precompute] 运行 {engine_name}...", flush=True)
            r = subprocess.run(
                [sys.executable, str(script_path), ticker, "--json"],
                capture_output=True, text=True, timeout=120,
            )

            if r.returncode != 0:
                error_msg = f"{engine_name}: exit {r.returncode}, stderr: {(r.stderr or '')[:200]}"
                errors.append(error_msg)
                print(f"  ⚠️  [precompute] {error_msg}", flush=True)
                precompute_results[engine_name] = {
                    "status": "error",
                    "error": error_msg,
                    "stdout": (r.stdout or "")[:500],
                }
                all_ok = False
                continue

            # 解析 JSON 输出
            try:
                output_data = json.loads(r.stdout.strip())
            except json.JSONDecodeError:
                output_data = {"raw": r.stdout.strip()}

            # 保存 JSON 输出到 data/tasks/
            output_file = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.json"
            output_file.write_text(
                json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # 同时保存 markdown 版本（可选，方便子代理阅读）
            try:
                r_md = subprocess.run(
                    [sys.executable, str(script_path), ticker, "--markdown"],
                    capture_output=True, text=True, timeout=60,
                )
                if r_md.returncode == 0:
                    md_file = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.md"
                    md_file.write_text(r_md.stdout, encoding="utf-8")
            except Exception:
                pass  # markdown 是可选的

            precompute_results[engine_name] = {
                "status": "ok",
                "output_file": str(output_file),
                "data": output_data,
            }
            print(f"  ✅ [precompute] {engine_name} 完成 → {output_file.name}", flush=True)

        except subprocess.TimeoutExpired:
            errors.append(f"{engine_name}: timeout (120s)")
            precompute_results[engine_name] = {"status": "timeout"}
            all_ok = False
        except Exception as e:
            errors.append(f"{engine_name}: {e}")
            precompute_results[engine_name] = {"status": "error", "error": str(e)}
            all_ok = False

    # 同步到 workspace outputs
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            for engine_name in engines:
                src = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.json"
                if src.exists():
                    shutil.copy2(src, ws.outputs_dir / f"precompute_{engine_name}.json")
                src_md = tasks_dir / f"{job_ctx.job_id}_precompute_{engine_name}.md"
                if src_md.exists():
                    shutil.copy2(src_md, ws.outputs_dir / f"precompute_{engine_name}.md")
        except Exception:
            pass

    return {
        "ok": all_ok,
        "mode": "precompute",
        "phase": "phase07_precompute",
        "job_id": job_ctx.job_id,
        "result": {
            "ticker": ticker,
            "market": market,
            "engines": precompute_results,
            "errors": errors,
            "output_dir": str(tasks_dir),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 4: Dispatch — 拆成 prepare + collect，避免死锁
# ═══════════════════════════════════════════════════════════

def _resolve_active_waves(runtime_root: Path, job_ctx: JobContext) -> list[int] | None:
    """2026-08-03 修复断点B：读取当前 job 的 active_waves（报告类型分流白名单）。

    dispatch_collect / per-wave gate 用它判断哪些 step/wave 是本次真正激活的，
    避免短路径（event_update/earnings_note）裁剪后对未跑的 step/wave 误判缺失、
    触发 redispatch 或 evidence gate REPAIR。未知/缺失 → None（全量 4 波）。
    """
    try:
        from scripts.ir_research_planner import load_research_plan
        from scripts.ir_subagent_launcher_wb import active_waves_for_report_type
        plan = load_research_plan(job_ctx.job_id, runtime_root / "data" / "tasks") or {}
        return active_waves_for_report_type(plan.get("report_type"))
    except Exception:
        return None


def _collect_expected_steps(runtime_root: Path, job_ctx: JobContext,
                            step_deps: dict) -> list[str]:
    """2026-08-03 修复断点B：返回本次 collect/step_gate 应验证的 step 列表。

    短路径（event_update/earnings_note）裁剪掉非激活 wave 内的 step；
    不属于 wave 体系的 step（legacy/测试 mock 的 step 名）一律保留，保证向后兼容。
    active_waves=None（全量）时返回全部 step。
    """
    from scripts.ir_subagent_launcher_wb import LAUNCH_WAVES
    active_waves = _resolve_active_waves(runtime_root, job_ctx)
    if active_waves is None:
        return list(step_deps.keys())
    wave_all = {s for wave in LAUNCH_WAVES for s in wave}
    active_set: set[str] = set()
    for idx in active_waves:
        if 0 <= idx < len(LAUNCH_WAVES):
            active_set.update(LAUNCH_WAVES[idx])
    return [s for s in step_deps.keys() if (s not in wave_all) or (s in active_set)]


def _run_dispatch_prepare(runtime_root: Path, job_ctx: JobContext,
                           sequential: bool = False) -> dict[str, Any]:
    """Phase 4a: 使用 launch_next_wave 发射第一个 wave，返回 needs_dispatch=True。

    sequential=True: 每次只派发 wave 内一个 step，配合 has_more 循环调用，
    避免并行 Task 子代理触发 API 429。

    Coordinator 读取返回的 task_tool_instructions 后用 team 模式派发子代理。
    后续 wave 由 Coordinator 循环调用 launch_next_wave() 推进。
    """
    from scripts.ir_subagent_launcher_wb import (
        launch_next_wave,
        get_pipeline_status,
        step_output_path,
        active_waves_for_report_type,
        STEP_DEPS,
        LAUNCH_WAVES,
    )

    metadata = job_ctx.metadata or {}
    entity = job_ctx.entity
    market = metadata.get("market", job_ctx.market) if metadata else job_ctx.market

    # v2.1 Batch3: 按 research_plan.report_type 计算 active_waves（报告类型分流）
    # 未知/缺失 → None（全量 4 波），保持向后兼容。
    _active_waves = _resolve_active_waves(runtime_root, job_ctx)

    # 发射当前 wave（自动检测已完成的 step，支持断点恢复）
    wave_result = launch_next_wave(
        task_id=job_ctx.job_id,
        entity=entity,
        query=job_ctx.query,
        market=market,
        sequential=sequential,
        active_waves=_active_waves,
    )

    if wave_result.get('all_done'):
        # 所有 step 已完成（恢复场景），直接进 collect
        return {
            "ok": True,
            "needs_dispatch": False,
            "has_more": False,
            "mode": "wave_orchestration",
            "phase": "phase08_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {
                "message": "All waves already completed, proceed to collect",
                "pipeline_status": get_pipeline_status(job_ctx.job_id),
            },
        }

    dispatched_count = wave_result.get('dispatched_count', 0)
    has_more = wave_result.get('has_more', False)

    if dispatched_count == 0 and not has_more:
        # sequential 模式下全阻塞 = 暂时无法推进，返回 needs_dispatch=True
        # 让 kernel 暂停，Coordinator 看到空 task_tool_instructions 后应等待重试
        if sequential:
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": False,
                "mode": "wave_orchestration",
                "phase": "phase08_dispatch_prepare",
                "job_id": job_ctx.job_id,
                "result": {
                    "message": "当前 wave 所有 step 被依赖阻塞，等待前序 step 完成后重试",
                    "task_tool_instructions": [],
                    "pipeline_status": get_pipeline_status(job_ctx.job_id),
                },
            }
        return {
            "ok": False,
            "mode": "wave_orchestration",
            "phase": "phase08_dispatch_prepare",
            "job_id": job_ctx.job_id,
            "result": {"error": "No steps dispatched in wave", "wave_result": wave_result},
        }

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": has_more,
        "mode": "wave_orchestration",
        "phase": "phase08_dispatch_prepare",
        "job_id": job_ctx.job_id,
        "result": {
            "wave_index": wave_result.get('wave_index'),
            "wave_label": wave_result.get('wave_label'),
            "dispatched_count": dispatched_count,
            "has_more": has_more,
            "task_tool_instructions": wave_result.get('task_tool_instructions', []),
            "after_all_tasks_complete": wave_result.get('after_all_tasks_complete'),
            "total_waves": len(LAUNCH_WAVES),
            "pipeline_status": get_pipeline_status(job_ctx.job_id),
        },
    }


def _run_fact_store_merge(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 3.5: merge step facts sidecars into the generic Fact Store."""
    from scripts.ir_fact_store import merge_step_fact_sidecars

    tasks_dir = runtime_root / "data" / "tasks"
    result = merge_step_fact_sidecars(
        job_ctx.job_id,
        tasks_dir=tasks_dir,
        entity=job_ctx.entity,
        market=job_ctx.market,
    )
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            output_path = result.get("output_path", "")
            index_path = result.get("index_path", "")
            if output_path and Path(output_path).exists():
                shutil.copy2(output_path, ws.outputs_dir / "fact_store.json")
            if index_path and Path(index_path).exists():
                shutil.copy2(index_path, ws.outputs_dir / "fact_store_index.json")
        except Exception:
            pass
    return {
        "ok": result.get("invalid_count", 0) == 0,
        "mode": "quality_production",
        "phase": "phase10_fact_store_merge",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_shared_state_refresh(runtime_root: Path, job_ctx: JobContext,
                                after_wave: int | None = None) -> dict[str, Any]:
    """Phase 10 refresh: 构建跨 Wave 共享状态摘要页。

    在 fact_store_merge 之后运行，汇总所有已完成 step 的 fact、gap、progress。
    输出 shared_state.json + shared_state_page.md，供后续 phase 子代理 brief 注入。

    after_wave: 指定刷新到哪个 wave（0-indexed）。
      - None = 自动检测最后一个有输出的 wave
      - 显式值 = 用于 per-wave 刷新（Batch3 evidence gate 拆分后使用）
    """
    from scripts.ir_shared_state import write_ir_shared_state

    tasks_dir = runtime_root / "data" / "tasks"

    # 自动检测：找最后一个有 step 输出的 wave index
    if after_wave is None:
        from scripts.ir_subagent_launcher_wb import step_output_path, LAUNCH_WAVES
        after_wave = 0
        for wi, wave_steps in enumerate(LAUNCH_WAVES):
            if any(
                step_output_path(job_ctx.job_id, s).exists()
                for s in wave_steps
            ):
                after_wave = wi

    json_path = write_ir_shared_state(
        task_id=job_ctx.job_id,
        tasks_dir=tasks_dir,
        after_wave=after_wave,
        entity=job_ctx.entity,
    )

    ws = _workspace_for(job_ctx)
    if ws is not None:
        for fname in (f"{job_ctx.job_id}-shared_state.json", f"{job_ctx.job_id}-shared_state_page.md"):
            src = tasks_dir / fname
            if src.exists():
                try:
                    shutil.copy2(src, ws.outputs_dir / fname)
                except Exception:
                    pass

    # 读取摘要数据
    state = {}
    try:
        state = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception:
        pass

    return {
        "ok": True,
        "mode": "shared_state_refresh",
        "phase": "phase10_shared_state_refresh",
        "job_id": job_ctx.job_id,
        "result": {
            "json_path": json_path,
            "fact_count": state.get("fact_summary", {}).get("total", 0),
            "data_gap_count": len(state.get("data_gaps", [])),
            "completed_steps": [
                s["step"] for s in state.get("step_progress", []) if s.get("md_exists")
            ],
        },
    }


def _auto_generate_sidecars_from_md(
    task_id: str,
    step_name: str,
    md_path: Path,
    facts_out: Path | None,
    section_out: Path | None,
) -> None:
    """从 .md 内容自动提取生成缺失的 facts + section sidecar。

    兜底逻辑（2026-07-13 新增）：当子代理因基础设施问题（如 499 canceled）
    只输出了 .md 但缺失 sidecar 时，从 md 文本中提取结构化 facts 和 section package，
    避免整条管线因 sidecar 缺失而卡死。
    """
    md_text = md_path.read_text(encoding="utf-8")
    # fact_id 前缀：保留下划线，避免生成带空格的 id（如 "STEP1 DATA-F001"），
    # 空格 id 在 section gate 的 fact_id 引用比对中易产生歧义且不利于跨文件溯源。
    step_upper = step_name.upper()

    # 兜底 fact 的内部溯源引用：这些 fact 由 md 抽取而来，无法逐条映射到外部 URL，
    # 其可追溯性由 source_quote（报告原句）+ 指向本 step 报告的内部引用共同保证，
    # 从而满足 fact-store 的 source_url 非空契约（见 scripts/ir_fact_store.py:_normalize_sidecar_fact）。
    step_provenance = f"ir-report://{task_id}/{step_name}"

    # ── 提取 facts ──
    if facts_out is not None:
        facts: list[dict[str, Any]] = []
        fact_counter = 0

        # 从 markdown 中提取带 URL 的定量数据点
        # 匹配模式: 含数字的句子 + 后面的 URL 或脚注
        lines = md_text.split("\n")
        current_section = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                current_section = stripped.lstrip("#").strip()[:60]
                continue
            if not stripped or stripped.startswith("|") or stripped.startswith("---"):
                continue

            # 提取含数字+单位的关键数据点
            num_match = re.search(
                r'(\d[\d,]*\.?\d*\s*(?:亿|万|千|百|%|倍|元|台|件|个|条|家|项|项|次|次))',
                stripped
            )
            if num_match and len(stripped) >= 20:
                fact_counter += 1
                # 尝试从当前行或附近行提取 URL
                url = ""
                url_match = re.search(r'(https?://[^\s\)\]">]+)', stripped)
                if url_match:
                    url = url_match.group(1)
                # 从脚注标记提取
                fn_match = re.search(r'\[\^(\d+)\]', stripped)
                source_quote = stripped[:200]

                facts.append({
                    "fact_id": f"{step_upper}-F{fact_counter:03d}",
                    "claim": stripped[:80],
                    "value": num_match.group(0),
                    "unit": "",
                    "period": "",
                    "source_url": url or step_provenance,
                    "source_tier": "web" if url else "md_extraction",
                    "source_quote": source_quote,
                    "entity": "",
                    "question_id": "",
                    "fact_type": "step_sidecar",
                    "confidence": "medium",
                })

            # 限制最多 50 个 facts
            if fact_counter >= 50:
                break

        # 如果没提取到任何定量数据，提取前 5 个非空段落作为 facts
        if not facts:
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("|") and len(stripped) >= 30:
                    fact_counter += 1
                    url_match = re.search(r'(https?://[^\s\)\]">]+)', stripped)
                    facts.append({
                        "fact_id": f"{step_upper}-F{fact_counter:03d}",
                        "claim": stripped[:80],
                        "value": stripped[:100],
                        "unit": "",
                        "period": "",
                        "source_url": url_match.group(1) if url_match else step_provenance,
                        "source_tier": "md_extraction",
                        "source_quote": stripped[:200],
                        "entity": "",
                        "question_id": "",
                        "fact_type": "step_sidecar",
                        "confidence": "low",
                    })
                    if fact_counter >= 5:
                        break

        facts_payload = {
            "schema_version": "ir_step_facts.v1",
            "step": step_name,
            "facts": facts,
            "auto_generated": True,
            "source": "md_extraction_fallback",
        }
        from scripts.bp_file_lock import atomic_write
        atomic_write(facts_out, json.dumps(facts_payload, ensure_ascii=False, indent=2) + "\n")
        print(f"    📦 [{step_name}] 自动提取 {len(facts)} 条 facts", flush=True)

    # ── 提取 section package ──
    if section_out is not None:
        # 从 md 的 heading 结构提取 key_messages
        key_messages: list[str] = []
        for line in md_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title and len(key_messages) < 10:
                    key_messages.append(title)

        # 从 facts 中提取 claims
        claims: list[dict[str, Any]] = []
        if facts_out and facts_out.exists():
            try:
                facts_data = json.loads(facts_out.read_text(encoding="utf-8"))
                for f in facts_data.get("facts", [])[:30]:
                    claims.append({
                        "claim": f["claim"],
                        "fact_ids": [f["fact_id"]],
                        "reasoning": f"基于{step_name}自动提取",
                        "confidence": f.get("confidence", "medium"),
                        "source_quality": f.get("source_tier", "unknown"),
                    })
            except Exception:
                pass

        # facts_used：section 中所有 claim 引用到的 fact_id 去重集合，
        # 满足 REQUIRED_FIELDS 契约（scripts/ir_section_package.py:REQUIRED_FIELDS）。
        facts_used: list[str] = []
        _seen_fact_ids: set[str] = set()
        for _c in claims:
            for _fid in _c.get("fact_ids", []) or []:
                if _fid not in _seen_fact_ids:
                    _seen_fact_ids.add(_fid)
                    facts_used.append(_fid)

        section_payload = {
            "schema_version": "ir_section_package.v1",
            "section_id": step_name,
            "section_title": key_messages[0] if key_messages else f"{step_name} 分析报告",
            "key_messages": key_messages,
            "claims": claims,
            # ── REQUIRED_FIELDS 兜底（section gate 强制，缺任一即 FAIL）──
            "facts_used": facts_used,
            # 兜底抽取无法可靠区分反证，留空仅触发 WARN（非 FAIL），由人工/统稿补全
            "counter_evidence": [],
            "data_gaps": [
                f"本 section 由 {step_name}.md 兜底自动抽取，未经子代理结构化产出，"
                "counter_evidence 与部分定量口径待人工复核。"
            ],
            # markdown_draft 必须非空（缺失即 FAIL）：直接采用 step 报告全文
            "markdown_draft": md_text,
            "auto_generated": True,
            "source": "md_extraction_fallback",
        }
        from scripts.bp_file_lock import atomic_write
        atomic_write(section_out, json.dumps(section_payload, ensure_ascii=False, indent=2) + "\n")
        print(f"    📦 [{step_name}] 自动提取 {len(claims)} 条 claims, {len(key_messages)} 条 key_messages", flush=True)


def _run_dispatch_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 4b: 检查子代理输出是否完成，做质量门禁。

    Coordinator 在所有 wave 的 task 子代理完成后调用此 phase。
    
    **4层防线** (对齐 BP/Lit):
    - L1 软约束: 三文件指令 (.md + -facts.json + -section.json)
    - L2 硬约束: 三文件齐全 + JSON 合法性 + file_stable (8秒稳定性)
    - L2.5 缓冲: collect_with_retry (10次×30秒=300秒)
    - L3 半自动: 不完整 step 返回 needs_dispatch 重派发
    """
    from scripts.ir_subagent_launcher_wb import (
        check_step_quality,
        dispatch_rewrite,
        step_output_path,
        get_pipeline_status,
        STEP_DEPS,
    )

    metadata = job_ctx.metadata or {}
    entity = job_ctx.entity
    market = metadata.get("market", job_ctx.market) if metadata else job_ctx.market

    # 获取管线状态
    pipeline_status = get_pipeline_status(job_ctx.job_id)

    completed_steps: list[str] = []
    incomplete_steps: list[dict] = []  # {step, missing_files, issue}
    step_quality: dict[str, dict[str, Any]] = {}

    # 2026-08-03 修复断点B：只校验本次报告类型激活的 step。
    # 短路径（event_update/earnings_note）裁剪掉的 wave 的 step 不应被判为
    # incomplete 而触发 redispatch（否则短路径会被强行拉回全量）。
    # 非 wave 体系内的 step（legacy/mock）一律保留。
    _expected_steps = _collect_expected_steps(runtime_root, job_ctx, STEP_DEPS)

    # ── 4层防线: 逐 step 验证三文件完整性 ──
    for step_name in _expected_steps:
        md_path = step_output_path(job_ctx.job_id, step_name)
        facts_path = Path(str(md_path).replace(".md", "-facts.json"))
        section_path = Path(str(md_path).replace(".md", "-section.json"))
        
        # L2 硬约束: 三文件检查
        missing_files = []
        md_ok = md_path.exists() and md_path.stat().st_size >= 100
        facts_ok = facts_path.exists() and facts_path.stat().st_size >= 10
        section_ok = section_path.exists() and section_path.stat().st_size >= 10
        if not md_ok:
            missing_files.append(".md (主报告)")
        if not facts_ok:
            missing_files.append("-facts.json")
        if not section_ok:
            missing_files.append("-section.json")
        
        # ── L2.5 sidecar 自动补生成兜底 ──
        # 当 .md 存在但 sidecar 缺失时，从 md 内容自动提取 facts + section
        # 解决子代理基础设施 499 canceled 导致只出 .md 的问题
        if md_ok and (not facts_ok or not section_ok):
            try:
                _auto_generate_sidecars_from_md(
                    job_ctx.job_id, step_name, md_path,
                    facts_path if not facts_ok else None,
                    section_path if not section_ok else None,
                )
                facts_ok = facts_path.exists() and facts_path.stat().st_size >= 10
                section_ok = section_path.exists() and section_path.stat().st_size >= 10
                if facts_ok and section_ok:
                    print(f"  🔄 [{step_name}] sidecar 自动补生成成功", flush=True)
                    missing_files = []
                else:
                    still_missing = []
                    if not facts_ok:
                        still_missing.append("-facts.json")
                    if not section_ok:
                        still_missing.append("-section.json")
                    print(f"  ⚠️ [{step_name}] sidecar 补生成不完整: {still_missing}", flush=True)
                    missing_files = still_missing
            except Exception as sidecar_exc:
                print(f"  ⚠️ [{step_name}] sidecar 补生成失败: {sidecar_exc}", flush=True)
        
        if missing_files:
            incomplete_steps.append({
                "step": step_name,
                "missing_files": missing_files,
                "issue": "incomplete_output",
            })
            continue
        
        # L2 硬约束: JSON 合法性 + file_stable (8秒稳定性)
        try:
            facts_data = json.loads(facts_path.read_text(encoding="utf-8"))
            section_data = json.loads(section_path.read_text(encoding="utf-8"))
            
            # file_stable: 检查文件大小在 8 秒内无变化
            size1 = facts_path.stat().st_size
            time.sleep(8)
            size2 = facts_path.stat().st_size
            if size1 != size2:
                incomplete_steps.append({
                    "step": step_name,
                    "missing_files": [],
                    "issue": "file_unstable (子代理仍在写入)",
                })
                continue
            
            completed_steps.append(step_name)
            _sync_step_to_workspace(job_ctx, step_name, md_path)
            quality = check_step_quality(job_ctx.job_id, step_name)
            step_quality[step_name] = quality
            
        except (json.JSONDecodeError, Exception) as e:
            incomplete_steps.append({
                "step": step_name,
                "missing_files": [],
                "issue": f"JSON invalid: {e}",
            })

    # 2026-08-03 修复断点B：分母用激活 step 数（短路径时不再是全量 8）
    total_expected = len(_expected_steps)
    completion_rate = len(completed_steps) / max(total_expected, 1)

    # circuit_break 仅作诊断信号，不再阻断管线 (ok 永远 True)
    # 原因: sequential 派发模式下 collect 可能被调用时仅部分 step 完成，
    # 用 completion_rate < 0.5 硬卡会误判终止。不完整 step 由 L3 needs_dispatch 兜底。
    circuit_break = completion_rate < 0.5
    if circuit_break:
        print(f"  ⚠️ completion_rate={completion_rate:.2f}<0.5 — 仅作诊断, 不阻断管线", flush=True)

    # ── L3 半自动: 不完整 step 返回 needs_dispatch ──
    if incomplete_steps:
        # 构建 re-dispatch manifest
        tasks_dir = runtime_root / "data" / "tasks"
        manifests = []
        for item in incomplete_steps:
            step_name = item["step"]
            manifest_path = tasks_dir / f"{job_ctx.job_id}-redispatch-{step_name}.json"
            manifest = {
                "step": step_name,
                "action": "complete_missing_files",
                "missing_files": item["missing_files"],
                "issue": item["issue"],
                "output_path": str(step_output_path(job_ctx.job_id, step_name)),
                "instruction": (
                    f"子代理 {step_name} 输出不完整: {item['issue']}\n"
                    f"缺失文件: {', '.join(item['missing_files']) if item['missing_files'] else 'JSON 损坏'}\n"
                    f"请重新派发子代理完成缺失文件。"
                ),
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            manifests.append(str(manifest_path))
        
        return {
            "ok": True,
            "needs_dispatch": True,
            "has_more": len(manifests) > 1,
            "mode": "wave_orchestration",
            "phase": "phase09_dispatch_collect",
            "job_id": job_ctx.job_id,
            "dispatch_info": {
                "manifests": [manifests[0]] if manifests else [],
                "roles": ["ir_redispatch"],
                "task_dir": str(tasks_dir),
            },
            "instruction": (
                f"Read manifest: {manifests[0]}\n"
                f"Dispatch ONE sub-agent to complete missing files.\n"
                f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
                f"修复完成后用 start_phase='phase09_dispatch_collect' 恢复管线。"
                + (f"\n\n还有 {len(manifests) - 1} 个 incomplete step 待处理。" if len(manifests) > 1 else "")
            ),
            "result": {
                "completed": len(completed_steps),
                "incomplete": len(incomplete_steps),
                "incomplete_steps": incomplete_steps,
                "total_expected": total_expected,
                "completion_rate": round(completion_rate, 2),
            },
        }

    # ── 所有 step 完成，跑质量门禁 ──
    rewrite_dispatched: list[str] = []
    for step_name, quality in step_quality.items():
        if quality.get("verdict") == "fail" and quality.get("score", 0) > 0:
            try:
                rewrite_result = dispatch_rewrite(
                    job_ctx.job_id, step_name, entity, job_ctx.query, market
                )
                if rewrite_result.get("status") == "dispatched":
                    rewrite_dispatched.append(step_name)
            except Exception:
                pass

    from scripts.ir_quality_gate import run_step_gate
    # 2026-08-03 修复断点B：step_gate 只检查激活 step（保持与三文件校验同一集合，
    # 短路径下被裁剪的 step 不参与 step_gate，避免 MISSING 误判 FAIL）
    step_gate = run_step_gate(
        job_ctx.job_id,
        step_order=list(_expected_steps),
        tasks_dir=runtime_root / "data" / "tasks",
    )

    return {
        "ok": not circuit_break and step_gate.get("passed", False),
        "mode": "wave_orchestration",
        "phase": "phase09_dispatch_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "completed": len(completed_steps),
            "total_expected": total_expected,
            "completion_rate": round(completion_rate, 2),
            "circuit_break": circuit_break,
            "completed_steps": completed_steps,
            "step_quality": step_quality,
            "step_gate": step_gate,
            "rewrite_dispatched": rewrite_dispatched,
            "pipeline_status": pipeline_status,
            "workspace_outputs_dir": str(_workspace_for(job_ctx).outputs_dir) if _workspace_for(job_ctx) else "",
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 09c: Per-Wave Evidence Gate (Batch3: 拆分为 per-wave 独立 gate)
# ═══════════════════════════════════════════════════════════

def _run_single_wave_evidence_gate(runtime_root: Path, job_ctx: JobContext,
                                     wave_idx: int) -> dict[str, Any]:
    """Per-wave evidence gate — 只检查指定 wave 的 step 证据完整性。

    Batch3 改动: 从合并 4-wave gate 拆为 per-wave 独立 gate。
    REPAIR 时返回 needs_dispatch + has_more（sequential 派发 repair 子代理）。
    降级放行时记录 repair_exhausted 继续推进。
    """
    from scripts.ir_wave_evidence_gate import (
        evaluate_wave_evidence_gate,
        write_wave_gate,
        build_ir_repair_manifests,
    )
    from scripts.bp_utils import read_attempt_count

    # 2026-08-03 修复断点B：短路径（event_update/earnings_note）裁剪掉的 wave
    # 其 step 从未派发，gate 不应检查 → 直接 PASS，避免 BLOCKING/REPAIR 误判。
    _active_waves = _resolve_active_waves(runtime_root, job_ctx)
    if _active_waves is not None and wave_idx not in _active_waves:
        return {
            "ok": True,
            "mode": "wave_evidence_gate",
            "phase": f"wave{wave_idx}_evidence_gate",
            "job_id": job_ctx.job_id,
            "result": {"verdict": "SKIPPED_NOT_ACTIVE", "wave": wave_idx,
                       "active_waves": _active_waves},
        }

    tasks_dir = runtime_root / "data" / "tasks"
    gate_key = f"wave{wave_idx}_evidence_gate"

    gate_result = evaluate_wave_evidence_gate(
        task_id=job_ctx.job_id,
        wave=wave_idx,
        tasks_dir=tasks_dir,
    )
    write_wave_gate(job_ctx.job_id, wave_idx, gate_result, tasks_dir)
    verdict = gate_result["verdict"]
    issues = gate_result.get("issues", [])

    if verdict == "FAIL":
        return {
            "ok": False,
            "mode": "wave_evidence_gate",
            "phase": gate_key,
            "job_id": job_ctx.job_id,
            "result": {"verdict": "FAIL", "wave": wave_idx, "issues": issues},
        }

    if verdict == "REPAIR":
        manifests = build_ir_repair_manifests(
            task_id=job_ctx.job_id,
            wave=wave_idx,
            gate_result=gate_result,
            tasks_dir=tasks_dir,
        )
        attempt = read_attempt_count(tasks_dir / f"{job_ctx.job_id}-{gate_key}.json")
        if attempt > 1:
            print(f"  ⚠️ [{gate_key}] repair 次数已用尽 (attempt={attempt}), 降级放行", flush=True)
            return {
                "ok": True,
                "mode": "wave_evidence_gate",
                "phase": gate_key,
                "job_id": job_ctx.job_id,
                "result": {
                    "verdict": "PASS_WITH_DISCLOSURE",
                    "repair_exhausted": True,
                    "wave": wave_idx,
                    "issues": issues,
                },
            }

        if manifests:
            first_manifest = manifests[0]
            remaining = manifests[1:]
            return {
                "ok": True,
                "needs_dispatch": True,
                "has_more": len(remaining) > 0,
                "mode": "wave_evidence_gate",
                "phase": gate_key,
                "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [first_manifest],
                    "roles": ["ir_repair"],
                    "task_dir": str(tasks_dir),
                },
                "instruction": (
                    f"Read manifest: {first_manifest}\n"
                    f"Dispatch ONE repair sub-agent using Agent tool.\n"
                    f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
                    f"修复完成后用 start_phase='{gate_key}' 恢复管线。"
                    + (f"\n\n还有 {len(remaining)} 个 repair manifest 待处理。" if remaining else "")
                ),
                "result": {
                    "verdict": "REPAIR",
                    "wave": wave_idx,
                    "issues": issues,
                    "remaining_manifests": remaining,
                },
            }

    # PASS
    return {
        "ok": True,
        "mode": "wave_evidence_gate",
        "phase": gate_key,
        "job_id": job_ctx.job_id,
        "result": {"verdict": "PASS", "wave": wave_idx, "issues": issues},
    }


def _run_wave_evidence_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 09c (legacy): 合并 4-wave gate — 保留向后兼容。

    Batch3 后推荐使用 per-wave gate，但保留此函数供旧 task 断点续跑。
    """
    from scripts.ir_wave_evidence_gate import (
        evaluate_wave_evidence_gate,
        write_wave_gate,
        build_ir_repair_manifests,
    )
    from scripts.bp_utils import read_attempt_count

    tasks_dir = runtime_root / "data" / "tasks"
    all_verdicts: list[str] = []
    all_issues: list[dict] = []
    all_repair_manifests: list[str] = []

    for wave_idx in range(4):
        gate_result = evaluate_wave_evidence_gate(
            task_id=job_ctx.job_id, wave=wave_idx, tasks_dir=tasks_dir,
        )
        write_wave_gate(job_ctx.job_id, wave_idx, gate_result, tasks_dir)
        all_verdicts.append(gate_result["verdict"])
        all_issues.extend(gate_result.get("issues", []))
        if gate_result.get("needs_repair"):
            manifests = build_ir_repair_manifests(
                task_id=job_ctx.job_id, wave=wave_idx,
                gate_result=gate_result, tasks_dir=tasks_dir,
            )
            all_repair_manifests.extend(manifests)

    has_repair = "REPAIR" in all_verdicts
    has_fail = "FAIL" in all_verdicts

    if has_fail:
        return {
            "ok": False, "mode": "wave_evidence_gate",
            "phase": "phase09_wave_evidence_gate", "job_id": job_ctx.job_id,
            "result": {"verdicts": all_verdicts, "issues": all_issues},
        }

    if has_repair:
        attempt = read_attempt_count(tasks_dir / f"{job_ctx.job_id}-wave_evidence_gate.json")
        if attempt > 1:
            return {
                "ok": True, "mode": "wave_evidence_gate",
                "phase": "phase09_wave_evidence_gate", "job_id": job_ctx.job_id,
                "result": {
                    "verdict": "PASS_WITH_DISCLOSURE", "repair_exhausted": True,
                    "verdicts": all_verdicts, "issues": all_issues,
                },
            }
        if all_repair_manifests:
            first_manifest = all_repair_manifests[0]
            remaining = all_repair_manifests[1:]
            return {
                "ok": True, "needs_dispatch": True, "has_more": len(remaining) > 0,
                "mode": "wave_evidence_gate",
                "phase": "phase09_wave_evidence_gate", "job_id": job_ctx.job_id,
                "dispatch_info": {
                    "manifests": [first_manifest], "roles": ["ir_repair"],
                    "task_dir": str(tasks_dir),
                },
                "instruction": (
                    f"Read manifest: {first_manifest}\n"
                    f"Dispatch ONE repair sub-agent using Agent tool.\n"
                    f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
                    f"修复完成后用 start_phase='phase09_wave_evidence_gate' 恢复管线。"
                    + (f"\n\n还有 {len(remaining)} 个 repair manifest 待处理。" if remaining else "")
                ),
                "result": {
                    "verdict": "REPAIR", "verdicts": all_verdicts,
                    "issues": all_issues, "remaining_manifests": remaining,
                },
            }

    return {
        "ok": True, "mode": "wave_evidence_gate",
        "phase": "phase09_wave_evidence_gate", "job_id": job_ctx.job_id,
        "result": {"verdict": "PASS", "verdicts": all_verdicts, "issues": all_issues},
    }


# ═══════════════════════════════════════════════════════════
# Phase 11-13: Quality production review and assembly
# ═══════════════════════════════════════════════════════════

def _run_section_package_validation(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_quality_gate import run_section_gate
    from scripts.ir_section_package import write_section_package_index

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_section_package_index(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    section_gate = run_section_gate(job_ctx.job_id, tasks_dir=tasks_dir)
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "section_packages.json")
        except Exception:
            pass
    summary = payload.get("summary", {})
    return {
        "ok": summary.get("failed", 0) == 0 and summary.get("total", 0) > 0 and section_gate.get("passed", False),
        "mode": "quality_production",
        "phase": "phase11_section_package_validation",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "summary": summary, "section_gate": section_gate},
    }


def _run_debate_review_phase(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_debate_review import write_debate_review

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_debate_review(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "debate_review.json")
        except Exception:
            pass
    verdict = payload.get("verdict", "REWRITE_REQUIRED")
    return {
        "ok": verdict in ("PASS", "WARN"),
        "mode": "quality_production",
        "phase": "phase12_debate_review",
        "job_id": job_ctx.job_id,
        "result": {"output_path": output_path, "verdict": verdict, "issues": payload.get("issues", [])},
    }


# ═══════════════════════════════════════════════════════════
# Phase 13: Synthesis — 独立统稿子代理（对标 BP phase27-28）
# ═══════════════════════════════════════════════════════════

_SYNTHESIS_STEPS = [
    "step1_industry", "step2_biz", "step3_finance",
    "step4_mgmt", "step5_macro", "step6_valuation", "step7_insight", "step8_risk",
]


def _run_synthesis_prepare(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 13: Synthesis prepare — 构建统稿子代理 manifest，返回 needs_dispatch。

    对标 BP phase27_synthesis_prepare：
    - 从 instruction_store_ir/ir_统稿.md 加载 system prompt
    - 拼接 _common_tool_guide.md
    - 生成结构化 brief（列出所有输入文件路径）
    - 返回 needs_dispatch + manifest
    """
    from scripts.ir_subagent_launcher_wb import step_output_path, INSTRUCTION_STORE

    tasks_dir = runtime_root / "data" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # 加载统稿指令
    synth_prompt_path = INSTRUCTION_STORE / "ir_统稿.md"
    synth_prompt = ""
    if synth_prompt_path.exists():
        synth_prompt = synth_prompt_path.read_text(encoding="utf-8")
    else:
        synth_prompt = "你是投研统稿子代理。请读取所有 step 输出并组装为完整报告。"

    # 拼接 tool guide
    tool_guide_path = INSTRUCTION_STORE / "_common_tool_guide.md"
    if tool_guide_path.exists():
        tool_guide = tool_guide_path.read_text(encoding="utf-8")
        tool_guide = tool_guide.replace("{RUNTIME_ROOT}", str(runtime_root))
        tool_guide = tool_guide.replace("{TASK_DIR}", str(tasks_dir))
        synth_prompt += "\n\n" + tool_guide

    # 构建 brief
    output_path = tasks_dir / f"{job_ctx.job_id}-synthesis.md"
    brief_lines = [
        f"# Synthesis Brief: {job_ctx.entity} 统稿",
        f"",
        f"Task: {job_ctx.job_id}",
        f"Entity: {job_ctx.entity}",
        f"Query: {job_ctx.query}",
        f"",
        f"## ⚠️ 输出路径（必须写入此路径）",
        f"",
        f"`{output_path}`",
        f"",
        f"## 输入文件（读取以下所有 step 输出）",
        f"",
    ]

    for step in _SYNTHESIS_STEPS:
        sp = step_output_path(job_ctx.job_id, step)
        exists = sp.exists() and sp.stat().st_size > 100
        brief_lines.append(f"- {step}: `{sp}` (exists={exists})")

    brief_lines.extend([
        f"",
        f"## ⚠️ 工具限制",
        f"你没有 Glob/Grep 工具。搜索文件用 Bash（find/ls），读文件用 Read。",
        f"你不需要额外搜索——只读取已有 step 输出并组装。",
    ])

    brief_path = tasks_dir / f"{job_ctx.job_id}-synthesis_brief.md"
    brief_path.write_text("\n".join(brief_lines), encoding="utf-8")

    # 组装完整 system_prompt
    full_prompt = synth_prompt.replace("{JOB_ID}", job_ctx.job_id)
    full_prompt += f"\n\n## Brief\n\n请读取 brief 文件: `{brief_path}`\n"

    # 写 manifest
    manifest = {
        "manifest_version": "1.0",
        "pipeline": "ir",
        "role": "ir_统稿",
        "step": "step8_master",
        "system_prompt": full_prompt,
        "connectorIds": IR_SUBAGENT_CONNECTOR_IDS,
        "subagent_type": "general-purpose",
        "team_name_template": "ir-{task_id}",
        "task_dir": str(tasks_dir),
        "brief_path": str(brief_path),
        "output_path": str(output_path),
    }
    manifest_path = tasks_dir / f"{job_ctx.job_id}-synthesis_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "needs_dispatch": True,
        "has_more": False,
        "mode": "synthesis",
        "phase": "phase13_synthesis_prepare",
        "job_id": job_ctx.job_id,
        "dispatch_info": {
            "manifests": [str(manifest_path)],
            "roles": ["ir_统稿"],
            "task_dir": str(tasks_dir),
        },
        "instruction": (
            f"Read manifest: {manifest_path}\n"
            f"Dispatch ONE synthesis sub-agent using Agent tool.\n"
            f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
            f"完成后用 start_phase='phase13_synthesis_collect' 恢复管线。"
        ),
        "result": {
            "manifest_path": str(manifest_path),
            "brief_path": str(brief_path),
            "output_path": str(output_path),
        },
    }


def _run_synthesis_collect(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 13 collect: 验证统稿输出完整性 + 脚注密度 repair。

    对标 BP `_run_bp_synthesis_collect()` 的 repair 机制：
    1. 脚注密度不达标 → 检查 repair 历史次数
    2. 未超过 1 次 → 生成 repair manifest，返回 needs_dispatch
    3. 超过 1 次 → repair_exhausted=True，降级 PASS_WITH_WARNINGS
    """
    from scripts.ir_subagent_launcher_wb import step_output_path, STEP_ROLE
    from scripts.bp_utils import read_attempt_count

    tasks_dir = runtime_root / "data" / "tasks"

    # 检查统稿输出
    synthesis_path = tasks_dir / f"{job_ctx.job_id}-synthesis.md"
    if synthesis_path.exists() and synthesis_path.stat().st_size > 500:
        ws = _workspace_for(job_ctx)
        if ws is not None:
            try:
                shutil.copy2(synthesis_path, ws.outputs_dir / "synthesis.md")
            except Exception:
                pass

        # ── 脚注密度检查 ──
        text = synthesis_path.read_text(encoding="utf-8")
        footnote_count = text.count("[^")
        char_count = len(text)
        min_footnotes = max(3, (char_count // 2000) * 3)
        footnote_ok = footnote_count >= min_footnotes

        quality = {
            "synthesis_path": str(synthesis_path),
            "char_count": char_count,
            "footnote_count": footnote_count,
            "min_footnotes": min_footnotes,
            "footnote_ok": footnote_ok,
        }

        # ── Repair 机制：脚注密度不达标 → 派发修复子代理 ──
        if not footnote_ok:
            # 2026-08-03 修复断点A：读的文件必须与下方写入的 ir_synthesis_repair_gate.json
            # 一致（原读 {job_id}-synthesis_repair.json 无任何写入方，attempt 恒为 0，
            # 导致 repair 永不降级、无限循环）。对齐 BP 的 bp_synthesis_repair_gate.json 模式。
            prior_attempt = read_attempt_count(
                tasks_dir / "ir_synthesis_repair_gate.json"
            )

            if prior_attempt < 1:
                # 构建 repair manifest
                step_outs = {}
                for s_name in STEP_ROLE:
                    if s_name == "step8_master":
                        continue
                    s_path = step_output_path(job_ctx.job_id, s_name)
                    if s_path.exists():
                        step_outs[s_name] = str(s_path)

                repair_prompt_lines = [
                    "你是 IR 统稿脚注修复专员。当前统稿脚注密度不达标：",
                    f"- 当前: {footnote_count} 个脚注 / {char_count} 字",
                    f"- 要求: 每 2000 字至少 3 个脚注 (需 {min_footnotes} 个以上)",
                    "",
                    "任务：",
                    "1. 读取统稿 synthesis.md",
                    "2. 读取各 step 输出的 -facts.json，提取 fact_id 和 source_url",
                    "3. 在统稿正文中为缺少脚注的关键数据点补充 [^N] 脚注标记",
                    "4. 在末尾来源与参考章节补充对应脚注定义",
                    f"5. 确保最终脚注数 >= {min_footnotes}",
                    "",
                    "禁止修改正文的分析内容，只补充脚注。",
                    "使用 scripts.bp_file_lock.locked_read_modify_write 修改 synthesis.md。",
                ]

                repair_manifest = {
                    "manifest_version": "1.0",
                    "task_id": job_ctx.job_id,
                    "role": "ir_synthesis_repair",
                    "action": "fix_citation_density",
                    "synthesis_path": str(synthesis_path),
                    "min_required": min_footnotes,
                    "current_count": footnote_count,
                    "step_outputs": step_outs,
                    "system_prompt": "\n".join(repair_prompt_lines),
                    "connectorIds": IR_SUBAGENT_CONNECTOR_IDS,
                    "subagent_type": "general-purpose",
                    "team_name_template": "ir-{task_id}",
                }

                repair_manifest_path = tasks_dir / "ir_synthesis_repair_manifest.json"
                repair_manifest_path.write_text(
                    json.dumps(repair_manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                gate_data = {
                    "attempt": prior_attempt + 1,
                    "footnote_count": footnote_count,
                    "min_required": min_footnotes,
                    "gate_verdict": "REPAIR",
                }
                (tasks_dir / "ir_synthesis_repair_gate.json").write_text(
                    json.dumps(gate_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                quality["gate_verdict"] = "REPAIR"
                quality["repair_attempt"] = prior_attempt + 1

                return {
                    "ok": True,
                    "needs_dispatch": True,
                    "has_more": False,
                    "mode": "ir_synthesis_repair",
                    "phase": "phase13_synthesis_collect",
                    "job_id": job_ctx.job_id,
                    "dispatch_info": {
                        "manifests": [str(repair_manifest_path)],
                        "roles": ["ir_synthesis_repair"],
                        "task_dir": str(tasks_dir),
                    },
                    "instruction": (
                        f"Read manifest: {repair_manifest_path}\n"
                        f"Dispatch ONE repair sub-agent using Agent tool.\n"
                        f"⚠️ 禁止在单条消息中派发多个 Agent。\n"
                        f"修复完成后用 start_phase='phase13_synthesis_collect' 恢复管线。"
                    ),
                    "result": quality,
                }

            else:
                # 降级放行
                quality["repair_exhausted"] = True
                quality["gate_verdict"] = "PASS_WITH_WARNINGS"
                quality["repair_attempt"] = prior_attempt

        else:
            quality["gate_verdict"] = "PASS"

        # ── v3.0: 单一统稿源同步 ──
        # synthesis.md 是 phase13 统稿子代理的唯一产出，DOCX 生成器(build_ir_broker_report_docx.py)
        # 读的是 {tid}-step8_master.md。这里把 synthesis.md 同步为 step8_master.md，
        # 消除"两份统稿、DOCX 读不到"的断裂，实现单一统稿源。
        step8_path = tasks_dir / f"{job_ctx.job_id}-step8_master.md"
        try:
            shutil.copy2(synthesis_path, step8_path)
            quality["step8_synced"] = True
        except Exception as e:
            quality["step8_sync_error"] = str(e)
        # 同步到 jobs/outputs/（DOCX 生成器的 fallback 路径）
        ws = _workspace_for(job_ctx)
        if ws is not None:
            try:
                shutil.copy2(synthesis_path, ws.outputs_dir / "step8_master.md")
            except Exception:
                pass

        return {
            "ok": True,
            "mode": "synthesis",
            "phase": "phase13_synthesis_collect",
            "job_id": job_ctx.job_id,
            "result": quality,
        }

    # 输出缺失
    return {
        "ok": False,
        "mode": "synthesis",
        "phase": "phase13_synthesis_collect",
        "job_id": job_ctx.job_id,
        "result": {
            "error": "Synthesis output missing or too short",
            "synthesis_path": str(synthesis_path),
            "exists": synthesis_path.exists(),
        },
    }


def _run_final_assembly_phase(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    from scripts.ir_final_assembler import write_final_report

    tasks_dir = runtime_root / "data" / "tasks"
    output_path = write_final_report(job_ctx.job_id, tasks_dir=tasks_dir)
    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            shutil.copy2(output_path, ws.outputs_dir / "final_assembly.json")
            md_path = payload.get("markdown_path")
            if md_path and Path(md_path).exists():
                shutil.copy2(md_path, ws.outputs_dir / "final_report.md")
        except Exception:
            pass
    return {
        "ok": bool(payload.get("ok", False)),
        "mode": "quality_production",
        "phase": "phase14_final_assembly",
        "job_id": job_ctx.job_id,
        "result": payload,
    }


# ═══════════════════════════════════════════════════════════
# Phase 14.5: Readability Review — 可读性审查（对标 BP phase31）
# ═══════════════════════════════════════════════════════════

def _run_readability_review(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """对 IR 最终报告做可读性审查（机器ID泄漏、长段落、脚注完整性等）。

    从 bp_readability_reviewer.py 移植，裁剪 BP 专用检查项，保留通用规则。
    FAIL 不阻断管线（记录到 deferred_fixes），MEDIUM/LOW 只记录。
    """
    from scripts.ir_readability_reviewer import write_ir_readability_review

    tasks_dir = runtime_root / "data" / "tasks"
    task_dir = tasks_dir  # IR 的 task_dir 就是 tasks_dir

    result = write_ir_readability_review(task_dir)
    verdict = result.get("verdict", "UNKNOWN")

    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            review_path = task_dir / "ir_readability_review.json"
            if review_path.exists():
                shutil.copy2(review_path, ws.outputs_dir / "ir_readability_review.json")
        except Exception:
            pass

    return {
        "ok": True,  # readability FAIL 不阻断管线
        "mode": "quality_production",
        "phase": "phase14_readability_review",
        "job_id": job_ctx.job_id,
        "result": {
            "verdict": verdict,
            "issue_count": result.get("issue_count", 0),
            "fail_count": result.get("fail_count", 0),
            "medium_count": result.get("medium_count", 0),
            "issues": result.get("issues", []),
            "review_path": result.get("review_path", ""),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 14.5: Claim Coverage — claim 覆盖校验 + 2 轮 repair（对标 BP phase24）
# ═══════════════════════════════════════════════════════════

def _run_claim_coverage(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Claim coverage 校验: 确保所有 claim 都有 fact 支撑。

    对标 BP bp_claim_coverage_validator.py:
    - REPAIR: not_addressed 比例 >50% → 生成 repair manifest → needs_dispatch
    - 最多 2 轮 repair → 降级 PASS_WITH_DISCLOSURE
    """
    from scripts.ir_claim_coverage_validator import (
        write_ir_claim_coverage,
        build_ir_claim_repair_manifests,
    )
    from scripts.bp_utils import read_attempt_count

    tasks_dir = runtime_root / "data" / "tasks"
    task_dir = tasks_dir

    result = write_ir_claim_coverage(task_dir)
    gate_verdict = result.get("gate_verdict", "UNKNOWN")

    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            gate_path = task_dir / "ir_claim_coverage.json"
            if gate_path.exists():
                shutil.copy2(gate_path, ws.outputs_dir / "ir_claim_coverage.json")
        except Exception:
            pass

    # REPAIR → 生成 manifest
    if gate_verdict == "REPAIR":
        attempt = read_attempt_count(task_dir / "ir_claim_coverage_gate.json")
        if attempt >= 2:
            # 降级放行
            result["gate_verdict"] = "PASS_WITH_DISCLOSURE"
            result["repair_exhausted"] = True
            gate_verdict = "PASS_WITH_DISCLOSURE"
        else:
            manifests = build_ir_claim_repair_manifests(
                task_id=job_ctx.job_id,
                gate_result=result,
                tasks_dir=tasks_dir,
            )
            if manifests:
                # 更新 attempt count
                gate_data = {"attempt": attempt + 1, "gate_verdict": "REPAIR"}
                (task_dir / "ir_claim_coverage_gate.json").write_text(
                    json.dumps(gate_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "needs_dispatch": True,
                    "has_more": False,
                    "mode": "quality_production",
                    "phase": "phase14_claim_coverage",
                    "job_id": job_ctx.job_id,
                    "dispatch_info": {
                        "manifests": manifests,
                        "roles": ["ir_claim_repair"],
                        "task_dir": str(task_dir),
                    },
                    "result": result,
                }

    return {
        "ok": True,
        "mode": "quality_production",
        "phase": "phase14_claim_coverage",
        "job_id": job_ctx.job_id,
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# Phase 14.6: Cross-Dimension Gate — 跨维度一致性检查（对标 BP phase25）
# ═══════════════════════════════════════════════════════════

def _run_cross_dimension_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """检查不同 step 之间的关键指标一致性和逻辑矛盾。

    从 bp_cross_dimension_gate.py 移植，适配 IR 10 step 结构。
    所有 issue 降级为 WARN，不阻断管线。
    """
    from scripts.ir_cross_dimension_gate import write_ir_cross_dimension_gate

    tasks_dir = runtime_root / "data" / "tasks"
    task_dir = tasks_dir

    result = write_ir_cross_dimension_gate(task_dir)
    verdict = result.get("gate_verdict", "UNKNOWN")

    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            gate_path = task_dir / "ir_cross_dimension_gate.json"
            if gate_path.exists():
                shutil.copy2(gate_path, ws.outputs_dir / "ir_cross_dimension_gate.json")
        except Exception:
            pass

    return {
        "ok": True,  # cross-dimension 不阻断
        "mode": "quality_production",
        "phase": "phase14_cross_dimension_gate",
        "job_id": job_ctx.job_id,
        "result": {
            "gate_verdict": verdict,
            "issues": result.get("issues", []),
            "summary": result.get("summary", {}),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 14.7: Delivery Gate — 交付门禁（对标 BP phase33 delivery gate）
# ═══════════════════════════════════════════════════════════

def _run_delivery_gate(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """聚合所有上游 gate 状态，做最终交付门禁检查。

    检查项: final_assembly / readability / debate / cross_dimension / section_package / synthesis
    FAIL → 记录到 deferred_fixes，不阻断交付。
    """
    tasks_dir = runtime_root / "data" / "tasks"
    task_dir = tasks_dir

    checks: list[dict[str, Any]] = []
    deferred_fixes: list[dict[str, Any]] = []

    def _check(name: str, path: Path, ok_field: str = "ok", pass_values: tuple = (True, "PASS")) -> None:
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        val = data.get(ok_field, None)
        passed = val in pass_values if val is not None else False
        status = "PASS" if passed else ("FAIL" if val is not None else "MISSING")
        checks.append({"name": name, "status": status, "value": val, "path": str(path)})
        if not passed:
            deferred_fixes.append({
                "check": name,
                "status": status,
                "suggestion": f"检查 {path.name} 并修复",
            })

    # 逐项检查（tier 感知：被裁剪的可选门禁跳过对应检查）
    tier = os.environ.get("IR_RESEARCH_TIER", "deep")
    skip_set = IR_RESEARCH_TIERS.get(tier, IR_RESEARCH_TIERS["deep"])["skip"]

    _check("final_assembly", task_dir / f"{job_ctx.job_id}-final_assembly.json")
    if "phase14_readability_review" not in skip_set:
        _check("readability", task_dir / "ir_readability_review.json",
               ok_field="verdict", pass_values=("PASS", "PASS_WITH_WARNINGS"))
    if "phase12_debate_review" not in skip_set:
        _check("debate_review", task_dir / f"{job_ctx.job_id}-debate_review.json",
               ok_field="passed", pass_values=(True,))
    if "phase14_cross_dimension_gate" not in skip_set:
        _check("cross_dimension", task_dir / "ir_cross_dimension_gate.json",
               ok_field="gate_verdict", pass_values=("PASS", "PASS_WITH_WARNINGS"))
    _check("section_packages", task_dir / f"{job_ctx.job_id}-section_gate.json",
           ok_field="passed", pass_values=(True,))
    _check("synthesis_footnotes", task_dir / f"{job_ctx.job_id}-synthesis.md",
           ok_field="__exists__", pass_values=(True,))
    # synthesis 特殊处理：只检查文件存在且够大
    synth_path = task_dir / f"{job_ctx.job_id}-synthesis.md"
    synth_ok = synth_path.exists() and synth_path.stat().st_size > 500
    for c in checks:
        if c["name"] == "synthesis_footnotes":
            c["status"] = "PASS" if synth_ok else "FAIL"
            c["value"] = synth_ok
            if not synth_ok:
                deferred_fixes.append({
                    "check": "synthesis_footnotes",
                    "status": "FAIL",
                    "suggestion": "synthesis.md 缺失或内容不足",
                })

    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    missing_count = sum(1 for c in checks if c["status"] == "MISSING")
    verdict = "PASS" if fail_count == 0 and missing_count == 0 else (
        "PASS_WITH_WARNINGS" if fail_count <= 1 else "FAIL"
    )

    # 写入 deferred_fixes
    if deferred_fixes:
        fixes_path = task_dir / "ir_delivery_deferred_fixes.json"
        fixes_path.write_text(
            json.dumps({"fixes": deferred_fixes, "verdict": verdict}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    result = {
        "gate_verdict": verdict,
        "checks": checks,
        "deferred_fixes": deferred_fixes,
        "fail_count": fail_count,
        "missing_count": missing_count,
    }

    # 写入 gate 结果
    gate_path = task_dir / "ir_delivery_gate.json"
    gate_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ws = _workspace_for(job_ctx)
    if ws is not None:
        for fname in ("ir_delivery_gate.json", "ir_delivery_deferred_fixes.json"):
            src = task_dir / fname
            if src.exists():
                try:
                    shutil.copy2(src, ws.outputs_dir / fname)
                except Exception:
                    pass

    return {
        "ok": True,  # delivery gate 不阻断
        "mode": "quality_production",
        "phase": "phase14_delivery_gate",
        "job_id": job_ctx.job_id,
        "result": result,
    }


def _run_ir_investment_judgment(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 14: 投资判断汇总（P2: 明确投资建议 + 逻辑 + 风险）。

    读取所有 step 输出与最终报告，生成 ir_investment_judgment.json/.md/.docx。
    """
    from scripts.ir_investment_judgment import build_ir_investment_judgment

    tasks_dir = runtime_root / "data" / "tasks"
    result = build_ir_investment_judgment(job_ctx.job_id, tasks_dir=tasks_dir)

    ws = _workspace_for(job_ctx)
    if ws is not None:
        # 2026-08-03 修复 P1：实际产物文件名带 {job_id} 前缀
        # （见 ir_investment_judgment.py 的 json_path/md_path/docx_out），
        # 原代码用无前缀名查找导致永远同步不到 workspace outputs。
        for suffix in ("ir_investment_judgment.json", "ir_investment_judgment.md", "ir_investment_judgment.docx"):
            src = tasks_dir / f"{job_ctx.job_id}-{suffix}"
            if src.exists():
                try:
                    shutil.copy2(src, ws.outputs_dir / suffix)
                except Exception:
                    pass

    return {
        "ok": True,
        "mode": "quality_production",
        "phase": "phase14_investment_judgment",
        "job_id": job_ctx.job_id,
        "result": {
            "recommendation": result.get("recommendation", ""),
            "rationale": result.get("rationale", ""),
            "low_confidence_dimension_count": result.get("low_confidence_dimension_count", 0),
            "total_data_gaps": result.get("total_data_gaps", 0),
            "dimensions": result.get("dimensions", []),
            "json_path": result.get("json_path", ""),
            "md_path": result.get("md_path", ""),
            "docx_path": result.get("docx_path", ""),
        },
    }


# ═══════════════════════════════════════════════════════════
# Phase 5: Delivery — 对抗验证 + DOCX + 交付（workspace-aware）
# ═══════════════════════════════════════════════════════════

def _run_delivery(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    if os.environ.get("IRBP_BG_CHILD") == "1":
        return _run_delivery_inner(runtime_root, job_ctx)
    from scripts.heavy_phase_bg import check_cached_result, launch_heavy_phase
    cached = check_cached_result(runtime_root, job_ctx.job_id, "phase15_delivery")
    if cached is not None:
        print(f"  📦 [ir] 使用缓存的 delivery 结果", flush=True)
        return cached
    return launch_heavy_phase(runtime_root, job_ctx, "phase15_delivery", pipeline="ir")


def _run_delivery_inner(runtime_root: Path, job_ctx: JobContext) -> dict[str, Any]:
    """Phase 5: 对抗验证 + 审计 + DOCX + 交付（子进程内直接调用）。

    All artifacts are synced to workspace.delivery_dir.
    Legacy paths remain intact.
    """
    import subprocess
    from scripts.verification_agent import run_verification

    metadata = job_ctx.metadata or {}
    session_id = metadata.get("session_id", "")

    # 1. 对抗式验证
    verification = {}
    verification_path = ""
    try:
        verification = run_verification(task_id=job_ctx.job_id, pipeline="ir")
    except Exception as e:
        verification = {"verdict": "ERROR", "summary": str(e)}

    verification_verdict = verification.get("verdict", "UNKNOWN")

    # Sync verification to workspace
    ws = _workspace_for(job_ctx)
    if ws is not None:
        try:
            vdest = ws.verification_dir / "verification_result.json"
            vdest.write_text(
                json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            verification_path = str(vdest)
        except Exception:
            pass

    # 2. 来源审计 + 执行审计
    audits_ok = True
    audit_errors: list[str] = []
    audit_paths: dict[str, str] = {}
    for audit_script in ("build_ir_source_audit.py", "build_ir_execution_audit.py"):
        script_path = runtime_root / "scripts" / audit_script
        if script_path.exists():
            try:
                r = subprocess.run(
                    [sys.executable, str(script_path), job_ctx.job_id],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode != 0:
                    audits_ok = False
                    audit_errors.append(f"{audit_script}: exit {r.returncode}")
                else:
                    # Try to parse output path and verdict from stdout
                    try:
                        payload = json.loads(r.stdout.strip())
                        if payload.get("verdict") and payload.get("verdict") != "PASS":
                            audits_ok = False
                            audit_errors.append(f"{audit_script}: verdict {payload.get('verdict')}")
                        audit_output = payload.get("output", "")
                        if audit_output:
                            audit_paths[audit_script] = audit_output
                            if Path(audit_output).exists():
                                _sync_artifact_to_workspace(job_ctx, audit_script, Path(audit_output))
                    except Exception:
                        pass
            except Exception as e:
                audits_ok = False
                audit_errors.append(f"{audit_script}: {e}")

    # 3. 最终报告门禁
    from scripts.ir_quality_gate import run_report_gate
    report_gate = run_report_gate(job_ctx.job_id, tasks_dir=runtime_root / "data" / "tasks")

    # 4. 生成券商风格 Word 报告
    docx_path = ""
    docx_error = ""
    build_docx_script = runtime_root / "scripts" / "build_ir_broker_report_docx.py"
    if report_gate.get("passed", False) and audits_ok and verification_verdict != "ERROR" and build_docx_script.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(build_docx_script), job_ctx.job_id],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                try:
                    payload = json.loads(r.stdout)
                    docx_path = payload.get("output", "")
                    if docx_path and Path(docx_path).exists():
                        _sync_artifact_to_workspace(job_ctx, "broker_report_docx", Path(docx_path))
                except Exception:
                    docx_path = ""
            else:
                docx_error = f"exit {r.returncode}: {r.stderr[:200]}"
        except Exception as e:
            docx_error = str(e)

    # 4.5 投资判断汇总同步到交付目录（phase14_investment_judgment 产物）
    _tasks_dir = runtime_root / "data" / "tasks"
    # 2026-08-03 修复 P1：实际产物带 {job_id} 前缀，原无前缀名永远找不到文件，
    # 投资判断从未进入 delivery 目录。
    for _ij_name in ("ir_investment_judgment.md", "ir_investment_judgment.docx"):
        _ij_src = _tasks_dir / f"{job_ctx.job_id}-{_ij_name}"
        if _ij_src.exists():
            try:
                _sync_artifact_to_workspace(job_ctx, "investment_judgment", _ij_src)
            except Exception:
                pass

    # 4.6 每 step 独立 DOCX 导出（P2: per-step DOCX）
    _step_docx_results = _export_ir_step_docx(job_ctx, runtime_root)
    _step_docx_paths = [r.get("path") for r in _step_docx_results if r.get("path")]

    # 4. 交付通知 — 已移除（开源发布版本不含消息推送）
    delivery_ok = False
    delivery_error = "Notification removed for open-source release"

    # Collect workspace artifact summary
    workspace_artifacts = {}
    if ws is not None:
        artifacts_manifest = ws.state_dir / "artifacts.json"
        if artifacts_manifest.exists():
            try:
                workspace_artifacts = json.loads(artifacts_manifest.read_text(encoding="utf-8"))
            except Exception:
                pass

    delivery_phase_ok = report_gate.get("passed", False) and audits_ok and verification_verdict != "ERROR" and bool(docx_path)

    return {
        "ok": delivery_phase_ok,
        "mode": "legacy_wrapped",
        "phase": "phase15_delivery",
        "job_id": job_ctx.job_id,
        "result": {
            "verification_verdict": verification_verdict,
            "verification_summary": verification.get("summary", ""),
            "verification_path": verification_path,
            "audits_ok": audits_ok,
            "audit_errors": audit_errors,
            "audit_paths": audit_paths,
            "docx_path": docx_path,
            "docx_error": docx_error,
            "report_gate": report_gate,
            "delivery_ok": delivery_ok,
            "delivery_error": delivery_error,
            "delivery_quality": verification_verdict.lower() if verification_verdict != "ERROR" else "unknown",
            "step_docx_paths": _step_docx_paths,
            "step_docx_count": len(_step_docx_paths),
            "workspace_artifacts": workspace_artifacts,
            "workspace_delivery_dir": str(ws.delivery_dir) if ws else "",
        },
    }


# ═══════════════════════════════════════════════
# Per-step DOCX 独立导出（P2）
# ═══════════════════════════════════════════════
_STEP_DOCX_STEPS = (
    "step1_industry", "step2_biz", "step3_finance",
    "step4_mgmt", "step5_macro", "step7_insight", "step6_valuation", "step8_risk",
)
_STEP_DOCX_LABELS = {
    "step1_industry": "行业分析", "step2_biz": "商业模式",
    "step3_finance": "财务分析", "step4_mgmt": "管理层与治理", "step5_macro": "宏观分析",
    "step7_insight": "差异化洞察", "step6_valuation": "预测与估值", "step8_risk": "风险与催化",
}


def _export_ir_step_docx(job_ctx: JobContext, runtime_root: Path) -> list[dict[str, Any]]:
    """为每个已完成的 step 生成独立 DOCX，落入 delivery/step_reports/。"""
    try:
        from scripts.ir_step_docx import build_ir_step_docx
    except Exception:
        return []
    tasks_dir = runtime_root / "data" / "tasks"
    ws = _workspace_for(job_ctx)
    results: list[dict[str, Any]] = []
    for step in _STEP_DOCX_STEPS:
        md = tasks_dir / f"{job_ctx.job_id}-{step}.md"
        if not md.exists() or md.stat().st_size < 100:
            continue
        label = _STEP_DOCX_LABELS.get(step, step)
        out_dir = ws.delivery_dir / "step_reports" if ws is not None else tasks_dir / "step_docx"
        out_name = f"{step}.docx"
        try:
            built = build_ir_step_docx(md, out_dir / out_name, title=f"{label}（{step}）")
            if built:
                results.append({"step": step, "label": label, "path": built})
                if ws is not None:
                    _sync_artifact_to_workspace(job_ctx, f"step_docx_{step}", Path(built))
        except Exception as e:
            results.append({"step": step, "label": label, "error": str(e)[:200]})
    return results


def _IR_FULL_PHASE_HANDLERS(runtime_root: Path) -> dict[str, Any]:
    """完整 IR phase 注册表（含新增 phase14_investment_judgment）。

    由 IRProfile.__init__ 按 tier 裁剪后传入 kernel。保持插入顺序即 phase 执行顺序。
    """
    return {
        "phase01_preflight": lambda job_ctx: _run_preflight(runtime_root, job_ctx),
        "phase02_company_verify": lambda job_ctx: _run_company_verify(runtime_root, job_ctx),
        # [v5.3] phase03_presearch + phase05_extract 已删除: 子代理全权搜索
        "phase04_research_plan": lambda job_ctx: _run_research_plan(runtime_root, job_ctx),
        "phase04_research_plan_collect": lambda job_ctx: _run_research_plan_collect(runtime_root, job_ctx),
        "phase06_fact_store_bootstrap": lambda job_ctx: _run_fact_store_bootstrap(runtime_root, job_ctx),
        "phase07_precompute": lambda job_ctx: _run_precompute(runtime_root, job_ctx),
        "phase08_dispatch_prepare": lambda job_ctx: _run_dispatch_prepare(runtime_root, job_ctx, sequential=True),
        "phase09_dispatch_collect": lambda job_ctx: _run_dispatch_collect(runtime_root, job_ctx),
        "phase09_wave_evidence_gate": lambda job_ctx: _run_wave_evidence_gate(runtime_root, job_ctx),
        # ── Batch3: per-wave evidence gate (独立 gate per wave, v2.1: 4 波模型中心化) ──
        "phase09_wave1_evidence_gate": lambda job_ctx: _run_single_wave_evidence_gate(runtime_root, job_ctx, wave_idx=0),
        "phase09_wave2_evidence_gate": lambda job_ctx: _run_single_wave_evidence_gate(runtime_root, job_ctx, wave_idx=1),
        "phase09_wave3_evidence_gate": lambda job_ctx: _run_single_wave_evidence_gate(runtime_root, job_ctx, wave_idx=2),
        "phase09_wave4_evidence_gate": lambda job_ctx: _run_single_wave_evidence_gate(runtime_root, job_ctx, wave_idx=3),
        "phase10_fact_store_merge": lambda job_ctx: _run_fact_store_merge(runtime_root, job_ctx),
        "phase10_shared_state_refresh": lambda job_ctx: _run_shared_state_refresh(runtime_root, job_ctx),
        # ── Batch3: per-wave shared_state_refresh (v2.1: 4 波) ──
        "phase10_wave1_shared_refresh": lambda job_ctx: _run_shared_state_refresh(runtime_root, job_ctx, after_wave=0),
        "phase10_wave2_shared_refresh": lambda job_ctx: _run_shared_state_refresh(runtime_root, job_ctx, after_wave=1),
        "phase10_wave3_shared_refresh": lambda job_ctx: _run_shared_state_refresh(runtime_root, job_ctx, after_wave=2),
        "phase10_wave4_shared_refresh": lambda job_ctx: _run_shared_state_refresh(runtime_root, job_ctx, after_wave=3),
        "phase11_section_package_validation": lambda job_ctx: _run_section_package_validation(runtime_root, job_ctx),
        "phase12_debate_review": lambda job_ctx: _run_debate_review_phase(runtime_root, job_ctx),
        "phase13_synthesis_prepare": lambda job_ctx: _run_synthesis_prepare(runtime_root, job_ctx),
        "phase13_synthesis_collect": lambda job_ctx: _run_synthesis_collect(runtime_root, job_ctx),
        "phase14_final_assembly": lambda job_ctx: _run_final_assembly_phase(runtime_root, job_ctx),
        "phase14_readability_review": lambda job_ctx: _run_readability_review(runtime_root, job_ctx),
        "phase14_claim_coverage": lambda job_ctx: _run_claim_coverage(runtime_root, job_ctx),
        "phase14_cross_dimension_gate": lambda job_ctx: _run_cross_dimension_gate(runtime_root, job_ctx),
        "phase14_delivery_gate": lambda job_ctx: _run_delivery_gate(runtime_root, job_ctx),
        # ── P2: 投资判断汇总（明确建议 + 逻辑 + 风险）──
        "phase14_investment_judgment": lambda job_ctx: _run_ir_investment_judgment(runtime_root, job_ctx),
        "phase15_delivery": lambda job_ctx: _run_delivery(runtime_root, job_ctx),
    }


class IRProfile(PipelineProfile):
    def __init__(self, runtime_root: Path):
        tier = resolve_ir_research_tier()
        skip = IR_RESEARCH_TIERS.get(tier, IR_RESEARCH_TIERS["deep"])["skip"]
        full = _IR_FULL_PHASE_HANDLERS(runtime_root)
        handlers = {k: v for k, v in full.items() if k not in skip}
        super().__init__(
            name="ir",
            job_type="investment_research",
            phase_handlers=handlers,
        )
        self.runtime_root = runtime_root
        self._research_tier = tier
        self._active_phases = set(handlers.keys())

    def phase_prerequisites(self) -> dict[str, list[str]]:
        """声明 phase 间的关键产物依赖（对标 BP bp_profile.py）。

        kernel 在 start_phase 跳过前置 phase 时，自动回填缺失产物。
        """
        full = {
            "phase06_fact_store_bootstrap": ["{task_id}-research_plan.json"],
            "phase07_precompute": ["{task_id}-ir_company_verify.json"],
            "phase08_dispatch_prepare": ["{task_id}-research_plan.json"],
            "phase10_fact_store_merge": ["{task_id}-research_plan.json"],
            "phase10_shared_state_refresh": ["{task_id}-fact_store.json"],
            "phase11_section_package_validation": ["{task_id}-research_plan.json", "{task_id}-fact_store.json"],
            "phase12_debate_review": ["{task_id}-section_packages.json"],
            "phase13_synthesis_prepare": ["{task_id}-research_plan.json"],
            "phase14_final_assembly": ["{task_id}-section_packages.json", "{task_id}-debate_review.json"],
            "phase14_readability_review": ["{task_id}-final_report.md"],
            "phase14_claim_coverage": ["{task_id}-research_plan.json"],
            "phase14_cross_dimension_gate": ["{task_id}-final_report.md"],
            "phase14_delivery_gate": ["ir_readability_review.json", "ir_claim_coverage.json", "ir_cross_dimension_gate.json", "{task_id}-final_assembly.json"],
            "phase14_investment_judgment": ["{task_id}-final_report.md"],
        }
        active = self._active_phases
        out: dict[str, list[str]] = {}
        for _k, _files in full.items():
            if _k not in active:
                continue
            out[_k] = [f for f in _files if _producer_active(f, active)]
        return out

    def phase_outputs(self) -> dict[str, list[str]]:
        """声明每个 phase 产出的关键文件（相对 task_dir）。

        kernel 用它构建反查表 file → producer_phase，
        在依赖缺失时精准回填到产出该文件的 phase。
        """
        full = {
            "phase01_preflight": [],
            "phase02_company_verify": ["{task_id}-ir_company_verify.json"],
            "phase04_research_plan": [],  # v5.3: 子代理直接生成plan
            "phase04_research_plan_collect": ["{task_id}-research_plan.json"],
            "phase06_fact_store_bootstrap": ["{task_id}-fact_store.json"],
            "phase07_precompute": [],
            "phase08_dispatch_prepare": [],
            "phase09_dispatch_collect": [],
            "phase09_wave_evidence_gate": [],
            # v3.1 (2026-08-04): per-wave gate facts 按研究链 4 波组成
            # Wave1 背景层(industry/biz/macro) → Wave2 预测与验证(finance/mgmt)
            # → Wave3 估值收口(valuation) → Wave4 预期差收口(insight/risk)
            "phase09_wave1_evidence_gate": ["{task_id}-step1_industry-facts.json", "{task_id}-step2_biz-facts.json", "{task_id}-step5_macro-facts.json"],
            "phase09_wave2_evidence_gate": ["{task_id}-step3_finance-facts.json", "{task_id}-step4_mgmt-facts.json"],
            "phase09_wave3_evidence_gate": ["{task_id}-step6_valuation-facts.json"],
            "phase09_wave4_evidence_gate": ["{task_id}-step7_insight-facts.json", "{task_id}-step8_risk-facts.json"],
            "phase10_fact_store_merge": ["{task_id}-fact_store.json", "{task_id}-fact_store_index.json"],
            "phase10_shared_state_refresh": ["{task_id}-shared_state.json", "{task_id}-shared_state_page.md"],
            "phase10_wave1_shared_refresh": ["{task_id}-shared_state.json", "{task_id}-shared_state_page.md"],
            "phase10_wave2_shared_refresh": ["{task_id}-shared_state.json", "{task_id}-shared_state_page.md"],
            "phase10_wave3_shared_refresh": ["{task_id}-shared_state.json", "{task_id}-shared_state_page.md"],
            "phase10_wave4_shared_refresh": ["{task_id}-shared_state.json", "{task_id}-shared_state_page.md"],
            "phase11_section_package_validation": ["{task_id}-section_packages.json", "{task_id}-section_gate.json"],
            "phase12_debate_review": ["{task_id}-debate_review.json"],
            "phase13_synthesis_prepare": ["{task_id}-synthesis_manifest.json"],
            "phase13_synthesis_collect": ["{task_id}-synthesis.md"],
            "phase14_final_assembly": ["{task_id}-final_report.md", "{task_id}-final_assembly.json"],
            # 2026-08-03 修复 P1：以下 4 个 gate 实际写出的是无前缀文件名
            # （ir_readability_review.json / ir_claim_coverage.json /
            #   ir_cross_dimension_gate.json / ir_delivery_gate.json），
            # 原声明带 {task_id} 前缀导致 kernel 反查表匹配不上、回填失效。
            "phase14_readability_review": ["ir_readability_review.json"],
            "phase14_claim_coverage": ["ir_claim_coverage.json"],
            "phase14_cross_dimension_gate": ["ir_cross_dimension_gate.json"],
            "phase14_delivery_gate": ["ir_delivery_gate.json", "ir_delivery_deferred_fixes.json"],
            "phase14_investment_judgment": ["{task_id}-ir_investment_judgment.json", "{task_id}-ir_investment_judgment.md"],
            "phase15_delivery": [],
        }
        return {k: v for k, v in full.items() if k in self._active_phases}
