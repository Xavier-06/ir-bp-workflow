# BP 管线全阶段优化方案

> 适用范围：种子轮 → 天使轮 → Pre-A → A轮 → B轮 → C轮 → Pre-IPO
> 编写日期：2026-06-10
> 基于：乾昇真空（天使轮）BP 尽调报告问题复盘

---

## 一、当前管线核心问题

### 1.1 融资阶段(stage)在管线中的传播断裂

```
bp_step0_profile.json
  └─ financing_stage: "未融资" ✅ LLM 正确提取

Phase 05  bp_company_verify.py
  └─ is_early = True ✅ 放宽了工商/财务/专利/资质要求

Phase 11  bp_presearch.py
  └─ is_early = True ✅ 搜索策略微调（加了创始人采访）

Phase 15  bp_research_planner.py     ❌ 不读 stage，统一要求 customer_evidence + revenue_evidence
Phase 20  bp_subagent_launcher.py    ❌ 不读 stage，子代理 prompt 无阶段区分
Phase 32  bp_investment_judgment.py  ❌ 不读 stage，判断标准一刀切
Phase 36  ir_section_package.py      ❌ 不读 stage，结构化压缩无差异
Phase 34  bp_thesis_reconciler.py    ❌ 不读 stage，confidence 阈值无差异
Phase 39  bp_narrative_assembler.py  ❌ 不读 stage，风险矩阵/估值/DD清单全用 PE 模板
Phase 40  deliver_ir_report.py       ❌ 交付 bp_final_report.md 而非 bp_synthesis.md
```

**结论：stage 信息在 Phase 11 之后就丢了。从子代理派遣开始，整个管线不知道这是天使轮还是 C 轮。**

### 1.2 当前 stage 分类太粗

管线只区分"early"和"mature"：

```python
# bp_company_verify.py:258
is_early = any(kw in stage for kw in ("种子", "天使", "seed", "angel", "pre-a", "pre_a"))
```

但种子轮和 A 轮的评估框架完全不同，Pre-A 和 C 轮更是天差地别。需要至少 4 级分类。

### 1.3 交付物选择错误

管线产出了两个版本：
- `bp_synthesis.md`（553 行，135 个编号脚注，推理链完整）← **没交付**
- `bp_final_report.md`（261 行，assembler 压缩的摘要）← **交付了这个**

assembler 的问题：
- `_compact_text()` 截断到 140 字 → 推理链丢失
- `_markdown_table()` 把段落塞进表格单元格 → 视觉灾难
- `_fact_text()` 内联化来源 → 脚注引用体系全部丢失
- `_clean_main_text()` 的正则清洗掉来源标注 → 无法追溯
- `_risk_rows()` 硬编码占位符 → "来自多维尽调/覆盖校验"零信息量

---

## 二、融资阶段分级体系

### 2.1 四级分类

| 级别 | 阶段 | 核心关注点 | 估值方法 | 客户/收入要求 |
|------|------|-----------|---------|-------------|
| **T1 极早期** | 种子轮、天使轮 | 团队 + 技术 + 方向 | 可比交易法（同类天使轮） | 不要求，有早期试用/反馈即加分 |
| **T2 早期** | Pre-A、A轮 | 产品 + PMF + 早期客户 | 可比交易法 + 早期 PS | 有付费客户或 LOI 即可 |
| **T3 成长期** | B轮 | 规模化 + 收入增长 + 市占 | PS 为主 + DCF 参考 | 需有规模化收入（千万级+） |
| **T4 成熟期** | C轮、Pre-IPO | 盈利 + 市场地位 + 退出路径 | PE + DCF + 可比公司 | 需有成熟财务数据 |

### 2.2 stage 分类函数（统一工具函数，所有脚本共用）

**新建文件：`scripts/bp_stage_utils.py`**

```python
"""融资阶段分级工具——所有 BP 脚本共用。"""

T1_KEYWORDS = ("种子", "天使", "seed", "angel")
T2_KEYWORDS = ("pre-a", "pre_a", "prea", "a轮", "series a")
T3_KEYWORDS = ("b轮", "series b", "pre-b", "pre_b")
T4_KEYWORDS = ("c轮", "series c", "pre-ipo", "d轮", "d+", "ipo", "已上市")

def classify_stage(financing_stage: str) -> str:
    """返回 'T1' | 'T2' | 'T3' | 'T4'。"""
    s = str(financing_stage or "").strip().lower()
    if any(kw in s for kw in T4_KEYWORDS):
        return "T4"
    if any(kw in s for kw in T3_KEYWORDS):
        return "T3"
    if any(kw in s for kw in T2_KEYWORDS):
        return "T2"
    return "T1"  # 默认按最保守的极早期处理

STAGE_META = {
    "T1": {
        "label": "极早期（种子/天使）",
        "valuation_methods": ["comparable_deals"],  # 可比交易法
        "forbidden_valuation_methods": ["pe", "dcf"],
        "customer_required": False,
        "revenue_required": False,
        "early_customer_feedback_bonus": True,  # 有试用/反馈是加分项
        "team_verification_priority": "critical",
        "tech_differentiation_priority": "critical",
        "ip_priority": "high",
        "financial_audit_required": False,
        "risk_severity_override": {
            "no_customer_revenue": "low",       # 正常，不标高风险
            "no_financial_data": "low",
            "team_background_unverified": "critical",  # 这是真正的大风险
            "tech_not_differentiated": "high",
        },
        "dd_focus": [
            "创始人背景深度验证（前雇主、LinkedIn、行业口碑）",
            "技术差异化验证（第三方测试、专利深度分析）",
            "早期客户/试用方访谈（2-3 个）",
            "竞品对标（同赛道早期公司估值区间）",
            "关联交易和同业竞争排查",
        ],
        "valuation_discount": {
            "liquidity": 0,        # 天使轮不谈流动性折价
            "tech_risk": 0.15,     # 技术未验证折价 15%
            "key_person": 0.20,    # 关键人风险折价 20%
            "customer": 0,         # 无客户不折价（正常）
            "total_cap": 0.35,     # 总折价上限 35%
        },
    },
    "T2": {
        "label": "早期（Pre-A/A轮）",
        "valuation_methods": ["comparable_deals", "early_ps"],
        "forbidden_valuation_methods": ["pe", "dcf"],
        "customer_required": True,  # 需要有付费客户或 LOI
        "revenue_required": False,  # 收入非必须但 ARR 是加分项
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "critical",
        "tech_differentiation_priority": "critical",
        "ip_priority": "critical",
        "financial_audit_required": False,
        "risk_severity_override": {
            "no_customer_revenue": "high",       # 有客户要求了
            "no_financial_data": "medium",       # 不要求审计但要有基本财务
            "team_background_unverified": "critical",
            "tech_not_differentiated": "critical",
            "no_paying_customer": "high",
        },
        "dd_focus": [
            "付费客户验证（合同、回款、NPS）",
            "PMF 验证（留存率、复购率、客单价趋势）",
            "产品 roadmap 和技术壁垒深度",
            "团队扩张计划（关键岗位招聘进展）",
            "竞品对标（同赛道 A 轮估值区间）",
        ],
        "valuation_discount": {
            "liquidity": 0.10,
            "tech_risk": 0.10,
            "key_person": 0.15,
            "customer": 0.15,     # 无客户开始折价
            "total_cap": 0.50,
        },
    },
    "T3": {
        "label": "成长期（B轮）",
        "valuation_methods": ["ps", "dcf_reference", "comparable_transactions"],
        "forbidden_valuation_methods": [],
        "customer_required": True,
        "revenue_required": True,  # 需有规模化收入
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "high",
        "tech_differentiation_priority": "critical",
        "ip_priority": "critical",
        "financial_audit_required": True,  # 需要审计报告
        "risk_severity_override": {
            "no_customer_revenue": "critical",
            "no_financial_data": "critical",
            "team_background_unverified": "high",
            "tech_not_differentiated": "critical",
            "revenue_decline": "critical",
            "customer_concentration": "high",
        },
        "dd_focus": [
            "收入确认（审计报告、大客户合同、回款周期）",
            "增长质量（收入增长率、毛利率趋势、客户集中度）",
            "市场地位（市占率变化、竞品动态）",
            "规模化能力（产能、交付、团队扩张）",
            "财务健康度（现金流、应收、负债）",
        ],
        "valuation_discount": {
            "liquidity": 0.20,
            "tech_risk": 0.05,
            "key_person": 0.10,
            "customer": 0.20,
            "total_cap": 0.60,
        },
    },
    "T4": {
        "label": "成熟期（C轮/Pre-IPO）",
        "valuation_methods": ["pe", "dcf", "comparable_companies"],
        "forbidden_valuation_methods": [],
        "customer_required": True,
        "revenue_required": True,
        "early_customer_feedback_bonus": False,
        "team_verification_priority": "high",
        "tech_differentiation_priority": "high",
        "ip_priority": "critical",
        "financial_audit_required": True,
        "risk_severity_override": {
            "no_customer_revenue": "critical",
            "no_financial_data": "critical",
            "no_audit_report": "critical",
            "governance_issues": "critical",
            "revenue_decline": "critical",
            "ipo_readiness_gap": "high",
        },
        "dd_focus": [
            "盈利能力和路径（毛利率、净利率、EBITDA）",
            "IPO 就绪度（合规、治理结构、历史沿革）",
            "市场天花板（TAM 饱和度、第二增长曲线）",
            "退出路径（IPO 时间表、并购可能性）",
            "对赌和优先条款审查",
        ],
        "valuation_discount": {
            "liquidity": 0.15,     # 接近上市，流动性折价降低
            "tech_risk": 0.05,
            "key_person": 0.05,
            "customer": 0.15,
            "total_cap": 0.50,
        },
    },
}

def get_stage_meta(stage_tier: str) -> dict:
    return STAGE_META.get(stage_tier, STAGE_META["T1"])


def build_stage_prompt_block(stage_tier: str, entity: str = "") -> str:
    """生成注入子代理 prompt 的阶段感知块。"""
    meta = get_stage_meta(stage_tier)
    entity_prefix = f"关于 {entity}，" if entity else ""
    lines = [
        f"## 融资阶段感知",
        f"{entity_prefix}当前融资阶段判定为 **{meta['label']}**。",
        "",
        "### 评估框架调整",
    ]
    if not meta["customer_required"]:
        lines.append("- 客户/收入验证为 **加分项而非必须项**，不因无客户而判定高风险")
    else:
        lines.append(f"- 客户/收入验证为 **必须项**，需确认有{'规模化收入' if meta['revenue_required'] else '付费客户或LOI'}")
    
    lines.append(f"- 团队验证优先级：**{meta['team_verification_priority']}**")
    lines.append(f"- 技术差异化优先级：**{meta['tech_differentiation_priority']}**")
    
    if meta["forbidden_valuation_methods"]:
        forbidden = "、".join(meta["forbidden_valuation_methods"])
        lines.append(f"- 估值方法：禁用 {forbidden}，使用 {'、'.join(meta['valuation_methods'])}")
    else:
        lines.append(f"- 估值方法：使用 {'、'.join(meta['valuation_methods'])}")
    
    lines.append("")
    lines.append("### DD 重点方向")
    for item in meta["dd_focus"]:
        lines.append(f"- {item}")
    
    lines.append("")
    lines.append("### 风险严重度调整")
    for risk, severity in meta["risk_severity_override"].items():
        lines.append(f"- {risk} → {severity}")
    
    return "\n".join(lines)
```

---

## 三、逐文件改动方案

### 3.1 Phase 05: `bp_company_verify.py`

**当前问题**：is_early 只做了宽松处理，没有输出 stage_tier 供下游使用。

**改动**：
1. 引入 `bp_stage_utils.classify_stage()`
2. 将 `stage_tier` 写入 `company_verify_report.json` 的顶层字段
3. 不同 tier 的宽松策略细化：

```python
from bp_stage_utils import classify_stage, get_stage_meta

stage_tier = classify_stage(profile.get("financing_stage", ""))
meta = get_stage_meta(stage_tier)

# T1/T2: 放宽工商/财务要求
# T3/T4: 正常标准
if stage_tier in ("T1", "T2"):
    # 放宽 QCC 工商缺失、财务数据、行业资质
    ...
if stage_tier == "T1":
    # 进一步放宽：专利深度、环保许可、排污许可
    ...
```

**输出变更**：`company_verify_report.json` 新增 `"stage_tier": "T1"` 字段。

---

### 3.2 Phase 11: `bp_presearch.py`

**当前问题**：is_early 只影响搜索策略的微小调整，没有本质区别。

**改动**：
1. 引入 `bp_stage_utils.classify_stage()`
2. 按 stage_tier 调整搜索 plan 的权重：

| 搜索类别 | T1 权重 | T2 权重 | T3 权重 | T4 权重 |
|---------|---------|---------|---------|---------|
| 创始人/团队背景 | ★★★★★ | ★★★★ | ★★★ | ★★ |
| 技术/专利分析 | ★★★★ | ★★★★★ | ★★★★ | ★★★ |
| 客户/订单/收入 | ★（加分搜索） | ★★★ | ★★★★★ | ★★★★★ |
| 财务数据/审计 | ☆（不搜） | ★★ | ★★★★ | ★★★★★ |
| 市场/竞品格局 | ★★★ | ★★★★ | ★★★★★ | ★★★★ |
| 早期用户反馈/试用 | ★★★★ | ★★★ | ★ | ☆ |
| IPO/退出相关 | ☆ | ☆ | ★★ | ★★★★ |

3. 将 `stage_tier` 写入 `bp_presearch_results.json`。

---

### 3.3 Phase 15: `bp_research_planner.py`

**当前问题**：`_fact_requirement()` 对所有阶段统一要求 `customer_evidence` + `revenue_evidence`。

**改动**：
1. 从 `bp_step0_profile.json` 或 `company_verify_report.json` 读取 `stage_tier`
2. 按 tier 调整 fact_requirement 的 required/optional 状态：

```python
if stage_tier == "T1":
    # customer_evidence, revenue_evidence → optional
    # team_background_verification → critical
    # early_customer_feedback → high (加分项)
    # tech_differentiation → critical
elif stage_tier == "T2":
    # customer_evidence → critical (需有付费客户)
    # revenue_evidence → high (有 ARR 更好)
    # pmf_evidence → critical (留存/复购/NPS)
elif stage_tier == "T3":
    # revenue_evidence → critical (需规模化收入)
    # financial_health → critical
    # market_share → critical
elif stage_tier == "T4":
    # 全面要求，加上 profitability, governance, ipo_readiness
```

3. 将 `stage_tier` 写入 `bp_research_plan.json`。

---

### 3.4 Phase 20: `bp_subagent_launcher.py` + `bp_shared_page_builder.py`

**当前问题**：子代理 prompt 完全没有阶段信息。8 个子代理用同样的评估框架。

**改动**：

#### `bp_shared_page_builder.py`

在 `render_shared_page()` 的输出中注入 stage 信息：

```python
def render_shared_page(state: dict) -> str:
    stage_tier = state.get("stage_tier", "T1")
    lines = [
        ...
        "## 融资阶段感知",
        build_stage_prompt_block(stage_tier, state.get("entity", "")),
        ...
    ]
```

#### `bp_subagent_launcher.py`

在每个子代理的 system prompt 中注入 `build_stage_prompt_block(stage_tier)`：

```python
from bp_stage_utils import classify_stage, build_stage_prompt_block

# 从 bp_step0_profile.json 读取 stage
profile = load_json(task_dir / "bp_step0_profile.json")
stage_tier = classify_stage(profile.get("financing_stage", ""))
stage_block = build_stage_prompt_block(stage_tier, entity)

# 注入到每个子代理 prompt
for subagent in subagents:
    subagent.system_prompt += "\n\n" + stage_block
```

**重点影响**：
- `bp_company_team_compliance` 子代理：T1 放宽团队履历验证要求（不标"不可验证"为 critical gap）
- `bp_customer_revenue_validation` 子代理：T1 明确告知"无客户是正常情况，重点关注早期试用反馈"
- `bp_valuation_return` 子代理：按 tier 使用对应估值方法，禁用不该用的方法
- `bp_dealbreaker_risk` 子代理：按 tier 调整风险严重度评级

---

### 3.5 Phase 32: `bp_investment_judgment.py`

**当前问题**：完全不读 stage。所有公司用同一套判断标准。

**改动**：
1. 从 `bp_step0_profile.json` 或 `company_verify_report.json` 读取 `stage_tier`
2. 按 tier 调整判断阈值：

```python
stage_meta = get_stage_meta(stage_tier)

# 调整 claim 验证要求
if not stage_meta["customer_required"]:
    # "客户收入不可验证" 不应阻止 recommended = "conditional_go"
    customer_gap_is_blocker = False
else:
    customer_gap_is_blocker = True

# 调整 confidence 阈值
if stage_tier == "T1":
    # 即使大部分 claim 是 unverified，只要有 team + tech 的 supported claims
    # confidence 可以 medium 而非 low
    ...
```

---

### 3.6 Phase 36: `ir_section_package.py`

**当前问题**：不读 stage。结构化压缩时不区分阶段。

**改动**：
1. 从 `bp_step0_profile.json` 读取 `stage_tier`
2. 按 tier 调整 `data_gaps` 和 `risk_register` 的严重度：

```python
stage_meta = get_stage_meta(stage_tier)

for gap in data_gaps:
    gap_key = _classify_gap(gap["text"])  # "no_customer", "no_revenue", etc.
    override = stage_meta["risk_severity_override"].get(gap_key)
    if override:
        gap["severity"] = override  # 用 stage 覆盖默认严重度
```

**效果**：天使轮的"无客户收入"gap 从 critical 降为 low。

---

### 3.7 Phase 34: `bp_thesis_reconciler.py`

**当前问题**：不读 stage。

**改动**：
1. 引入 stage_tier
2. T1/T2 公司的 `recommendation` 降级条件放宽：
   - "客户收入不可验证" 不应自动把 confidence 降为 low
   - "核心团队履历不可验证" 仍应降低 confidence（这是 T1 的真正风险）

---

### 3.8 Phase 39: `bp_narrative_assembler.py` ← **最大改动**

**当前问题**：
- 压缩式摘要丢失推理链
- 来源引用全部丢失
- 风险矩阵硬编码占位符
- 表格塞论述段落
- 无阶段感知

**改动分三块**：

#### A. 交付物切换

**`deliver_ir_report.py`** 和 **`phase_runner.py`** (Phase 40)：
- 主报告从 `bp_final_report.md` 切换为 `bp_synthesis.md`
- assembler 产出降级为"快速浏览版"附件（`bp_quick_brief.md`）
- DOCX 生成器的输入也从 synthesis 取

#### B. 来源引用保留

如果仍保留 assembler（作为快速浏览版），修改：

```python
# 不再内联化来源
def _fact_text(fact):
    # 保留 [^N] 格式，不做 (来源等级: xxx) 内联化
    fact_id = fact.get("fact_id", "")
    return f"[^{fact_id}]" if fact_id else ""

# 不再清洗来源标注
_CLEAN_MARKERS = (
    # 移除: re.compile(r"\*\*来源[^*]*\*\*"),
    # 移除: re.compile(r"\*\*Evidence[^*]*\*\*", re.I),
    # 只保留 task ID 清洗
    re.compile(r"TASK-\d{8}-\d{3}"),
)

# 在 final_report 末尾追加脚注定义
def _append_footnote_definitions(lines, fact_store):
    lines.append("")
    lines.append("## 来源与参考")
    for fact in fact_store.get("facts", []):
        fid = fact.get("fact_id", "")
        source = fact.get("source_url", "") or fact.get("source_tier", "")
        claim = fact.get("claim", "")
        lines.append(f"[^{fid}]: {claim} — {source}")
```

#### C. 风险矩阵改造

```python
def _risk_rows(counter_evidence, gaps, coverage, stage_meta):
    rows = []
    for item in counter_evidence:
        # 用实际证据文本替代占位符
        evidence_text = item.get("evidence_detail", "") or item.get("source", "")
        # 用 LLM 生成的影响描述替代模板
        impact = _generate_impact_text(item, stage_meta)
        # 根据 stage 调整处置动作
        action = _stage_aware_action(item, stage_meta)
        rows.append([
            "风险",
            item["risk"],
            item["severity"],
            evidence_text,       # ← 不再是"来自多维尽调/覆盖校验"
            impact,              # ← 不再是"影响估值折扣和推进节奏"
            action,              # ← 不再是"下一轮DD核验"
        ])
    return rows
```

#### D. 摘要表改造

```python
# 结论列限制 40 字 + 章节引用
def _compact_conclusion(text, max_len=40):
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

# 表格只放真正适合对比的数据
# 摘要表 → 一句话结论 + "详见 §X.Y"
```

#### E. 估值章节阶段化

```python
def _valuation_section(stage_meta, valuation_data):
    forbidden = stage_meta["forbidden_valuation_methods"]
    methods = stage_meta["valuation_methods"]
    
    lines = [
        f"### 估值方法：{', '.join(methods)}",
    ]
    if forbidden:
        lines.append(f"**不适用于当前阶段**：{', '.join(forbidden)}")
    
    # 折价逻辑按 stage_meta 的 valuation_discount 计算
    discount = stage_meta["valuation_discount"]
    lines.append(f"- 流动性折价：{discount['liquidity']*100:.0f}%")
    lines.append(f"- 技术风险折价：{discount['tech_risk']*100:.0f}%")
    lines.append(f"- 关键人风险折价：{discount['key_person']*100:.0f}%")
    lines.append(f"- 总折价上限：{discount['total_cap']*100:.0f}%")
    
    return lines
```

#### F. DD 清单阶段化

```python
def _dd_checklist(stage_meta):
    lines = ["### 下一步尽调动作"]
    for item in stage_meta["dd_focus"]:
        lines.append(f"- {item}")
    return lines
```

---

### 3.9 Phase 40: `deliver_ir_report.py` + `build_bp_dd_report_docx.py`

**改动**：

#### `deliver_ir_report.py`

```python
# 主报告用 synthesis
primary_report = task_dir / "bp_synthesis.md"
# 快速浏览版用 assembler 输出
quick_brief = task_dir / "bp_quick_brief.md"  # 原 bp_final_report.md 改名

# 交付时两个都给
deliverables = [primary_report]
if quick_brief.exists():
    deliverables.append(quick_brief)
```

#### `build_bp_dd_report_docx.py`

1. **字体统一**：全栈使用微软雅黑（Microsoft YaHei），移除宋体 eastAsia 设置

```python
# 移除所有 _set_eastasia_font_on_run(run, "宋体") 调用
# 统一为：
run.font.name = "Microsoft YaHei"
# eastAsia 也用 Microsoft YaHei
rFonts.set(qn('w:eastAsia'), "Microsoft YaHei")
```

2. **字号体系**：
   - 正文 10.5pt
   - 表格 8.5pt（数据型）/ 9.5pt（含文本型）
   - H1 16pt / H2 14pt / H3 12pt
   - 封面标题 28pt / 副标题 22pt

3. **表格列宽控制**：对含文本的列设置最小宽度

4. **删除 `generate_ubtech_docx.py`**：统一用一个 DOCX 生成器

---

## 四、管线传播链修复

### 4.1 stage_tier 传播路径

```
bp_step0_profile.json (financing_stage)
        │
        ▼
  classify_stage() → stage_tier: "T1"
        │
        ├──→ company_verify_report.json  → stage_tier
        ├──→ bp_presearch_results.json   → stage_tier
        ├──→ bp_research_plan.json       → stage_tier
        ├──→ bp_shared_state.json        → stage_tier
        ├──→ bp_section_packages.json    → stage_tier (each package)
        │
        ▼ (所有下游脚本从这里读)
  bp_shared_state.json → stage_tier
        │
        ├──→ bp_subagent_launcher.py     → 注入子代理 prompt
        ├──→ bp_investment_judgment.py   → 调整判断阈值
        ├──→ ir_section_package.py       → 调整 gap/risk 严重度
        ├──→ bp_thesis_reconciler.py     → 调整 confidence 降级条件
        ├──→ bp_narrative_assembler.py   → 调整风险/估值/DD 清单
        └──→ build_bp_dd_report_docx.py  → 调整报告模板
```

### 4.2 实现方式

在每个需要 stage 的脚本中：

```python
# 方案 A：从 bp_step0_profile.json 直接读（简单，但耦合）
profile = load_json(task_dir / "bp_step0_profile.json")
stage_tier = classify_stage(profile.get("financing_stage", ""))

# 方案 B：从 bp_shared_state.json 读（已有传播机制）
shared = load_json(task_dir / "bp_shared_state.json")
stage_tier = shared.get("stage_tier", "T1")
```

推荐方案 B，因为 `bp_shared_state.json` 已经是管线的状态中枢。

**在 `bp_shared_page_builder.py` 中**：

```python
def build_shared_state(task_dir, after_wave=0):
    ...
    # 新增：读取 stage_tier
    profile = _load_json(task_dir / "bp_step0_profile.json", {})
    stage_tier = classify_stage(profile.get("financing_stage", ""))
    
    return {
        ...
        "stage_tier": stage_tier,
        ...
    }
```

---

## 五、各阶段子代理 prompt 差异要点

### 5.1 `bp_company_team_compliance` 子代理

| 评估项 | T1（种子/天使） | T2（Pre-A/A） | T3（B轮） | T4（C轮+） |
|--------|---------------|--------------|----------|-----------|
| 创始人履历验证 | critical（这是最大风险） | critical | high | high |
| 核心团队完整性 | high（2-3人即可） | critical（需要完整团队） | critical | critical |
| 公司治理结构 | low（早期不要求） | medium | high | critical |
| 合规资质 | low | medium | high | critical |
| 股权结构 | medium（一股独大正常） | medium | high（需期权池） | critical（需规范） |

### 5.2 `bp_customer_revenue_validation` 子代理

| 评估项 | T1 | T2 | T3 | T4 |
|--------|----|----|----|----| 
| 无客户/无收入 | 正常（low risk） | 需关注（medium） | 重大风险（high） | 阻断（critical） |
| 客户验证方式 | 试用反馈/访谈 | 合同/LOI | 合同+回款+审计 | 同 T3 + 大客户深度 |
| 收入预测评估 | 不做要求 | 有方法论即可 | 需有历史数据支撑 | 需有审计+预测 |
| 评估重点 | 早期用户反馈质量 | PMF 信号（留存/复购） | 收入增长质量 | 盈利路径 |

### 5.3 `bp_valuation_return` 子代理

| 评估项 | T1 | T2 | T3 | T4 |
|--------|----|----|----|----|
| 估值方法 | 可比交易法 | 可比交易 + 早期PS | PS + DCF参考 | PE + DCF |
| 禁用方法 | PE, DCF | PE, DCF | — | — |
| 折价上限 | 35% | 50% | 60% | 50% |
| 对标来源 | 同类天使轮 | A轮/Pre-A轮 | B轮 | 上市公司/Pre-IPO |
| MOIC 预期 | 10-30x（高风险高回报） | 5-15x | 3-8x | 2-5x |

### 5.4 `bp_dealbreaker_risk` 子代理

| 风险项 | T1 | T2 | T3 | T4 |
|--------|----|----|----|----|
| 无客户收入 | 非风险 | medium | high | critical |
| 无审计报告 | 非风险 | 非风险 | critical | critical |
| 团队履历不可验证 | critical | critical | high | high |
| 关联交易/同业竞争 | high | high | high | critical |
| 无行业资质 | 非风险 | medium | high | critical |
| 知识产权归属不清 | high | critical | critical | critical |

---

## 六、实施优先级和排期

### P0（立即做，改动小收益大）

| # | 改动 | 文件 | 效果 |
|---|------|------|------|
| 1 | 交付 `bp_synthesis.md` 替代 `bp_final_report.md` | `deliver_ir_report.py`, `phase_runner.py` | 报告质量直接提升一档 |
| 2 | DOCX 字体统一微软雅黑 | `build_bp_dd_report_docx.py` | 视觉大幅提升 |
| 3 | 新建 `bp_stage_utils.py` 统一分类函数 | 新文件 | 为后续所有改动打基础 |

### P1（核心改动，stage 贯穿）

| # | 改动 | 文件 | 效果 |
|---|------|------|------|
| 4 | stage_tier 注入 `bp_shared_state.json` | `bp_shared_page_builder.py` | 全管线可读 stage |
| 5 | 子代理 prompt 注入阶段感知块 | `bp_subagent_launcher.py` | 子代理输出质量提升 |
| 6 | research_planner 按 tier 调整 fact_requirement | `bp_research_planner.py` | 搜索计划更精准 |
| 7 | assembler 风险矩阵传入实际证据 | `bp_narrative_assembler.py` | 消除占位符废话 |
| 8 | assembler 估值/DD 清单阶段化 | `bp_narrative_assembler.py` | 不再用 PE 审天使轮 |

### P2（精细化）

| # | 改动 | 文件 | 效果 |
|---|------|------|------|
| 9 | investment_judgment 按 tier 调整阈值 | `bp_investment_judgment.py` | 判断更合理 |
| 10 | section_package 按 tier 调整 gap 严重度 | `ir_section_package.py` | 数据缺口评级准确 |
| 11 | thesis_reconciler 按 tier 放宽降级条件 | `bp_thesis_reconciler.py` | confidence 更合理 |
| 12 | 摘要表改为"一句话+章节引用" | `bp_narrative_assembler.py` | 表格回归数据用途 |
| 13 | 交叉判断由 LLM 生成 | `bp_narrative_assembler.py` | 真正的跨维度洞察 |

### P3（长期优化）

| # | 改动 | 文件 | 效果 |
|---|------|------|------|
| 14 | presearch 搜索计划按 tier 调权重 | `bp_presearch.py` | 搜索更聚焦 |
| 15 | company_verify 宽松策略细化 | `bp_company_verify.py` | 核验标准更精准 |
| 16 | 删除 `generate_ubtech_docx.py` | 删除文件 | 消除两套 DOCX 冲突 |

---

## 七、验收标准

### 用乾昇真空（天使轮）案例验证：

1. **风险矩阵**：
   - [ ] "客户收入不可验证" 不再标为 CRITICAL，应为 low
   - [ ] "当前证据" 列有实际文本，不是"来自多维尽调/覆盖校验"
   - [ ] "投资影响" 列有具体描述，不是"影响估值折扣和推进节奏"

2. **估值章节**：
   - [ ] 只用可比交易法，不出现 PE/DCF
   - [ ] 折价上限 ≤ 35%，不出现 60-80% 的 PE 级折价
   - [ ] 不要求"审计报告"

3. **DD 清单**：
   - [ ] 聚焦"创始人背景验证、技术差异化、早期客户访谈"
   - [ ] 不出现"财务审计报告、前雇主确认"等不合理要求

4. **来源引用**：
   - [ ] 正文有 `[^1]`-`[^N]` 脚注标记
   - [ ] 报告末尾有完整的来源列表（含 URL）
   - [ ] DOCX 中来源可点击/可查看

5. **交付物**：
   - [ ] 交付的是 synthesis（有推理链），不是 assembler 压缩版
   - [ ] DOCX 字体统一，无混叠
   - [ ] 表格列宽合理，无段落塞入单元格

### 用 B 轮案例交叉验证：

1. [ ] 风险矩阵中"无客户收入"标为 high
2. [ ] 估值使用 PS + DCF 参考
3. [ ] DD 清单包含"收入确认、增长质量、市场地位"
4. [ ] 要求审计报告

---

## 八、文件改动清单汇总

| 文件 | 改动类型 | 优先级 | 说明 |
|------|---------|--------|------|
| `scripts/bp_stage_utils.py` | **新建** | P0 | 统一 stage 分类 + meta + prompt 生成 |
| `scripts/deliver_ir_report.py` | 修改 | P0 | 交付 synthesis 替代 final_report |
| `scripts/build_bp_dd_report_docx.py` | 修改 | P0 | 字体统一 + 表格列宽 |
| `scripts/bp_shared_page_builder.py` | 修改 | P1 | stage_tier 注入 shared_state |
| `scripts/bp_subagent_launcher.py` | 修改 | P1 | 子代理 prompt 注入阶段块 |
| `scripts/bp_research_planner.py` | 修改 | P1 | fact_requirement 按 tier 调整 |
| `scripts/bp_narrative_assembler.py` | 修改 | P1 | 风险矩阵 + 估值 + DD + 摘要表 + 来源 |
| `scripts/bp_investment_judgment.py` | 修改 | P2 | 判断阈值按 tier 调整 |
| `scripts/ir_section_package.py` | 修改 | P2 | gap/risk 严重度按 tier 调整 |
| `scripts/bp_thesis_reconciler.py` | 修改 | P2 | confidence 降级条件按 tier 调整 |
| `scripts/bp_presearch.py` | 修改 | P3 | 搜索计划权重按 tier 调整 |
| `scripts/bp_company_verify.py` | 修改 | P3 | 宽松策略细化 |
| `scripts/generate_ubtech_docx.py` | **删除** | P3 | 消除两套 DOCX 冲突 |
| `scripts/phase_runner.py` | 修改 | P0 | Phase 40 交付物切换 |
