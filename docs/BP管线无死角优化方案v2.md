# BP 管线无死角优化方案 v2

> 基于：v1 方案 + 11 个核心脚本逐行代码审查 + 乾昇真空（天使轮）实战复盘
> 编写日期：2026-06-10
> 状态：v1 的 P0 已落地，本文档覆盖 **v1 未覆盖的盲区** + **v1 已规划但未实施的细节补充**

---

## 〇、v1 方案落地状态确认

| v1 项 | 状态 | 备注 |
|-------|------|------|
| P0-1: 交付 synthesis 替代 final_report | ✅ 已落地 | phase_runner.py + deliver_ir_report.py 已改 |
| P0-2: 新建 bp_stage_utils.py | ✅ 已落地 | T1-T4 分类 + meta + prompt block |
| P0-3: DOCX 字体统一 | ⚠️ 部分落地 | 已改为 Microsoft YaHei，但 macOS 无此字体需 fallback |
| P1-4: stage_tier 注入 shared_state | ✅ 已落地 | shared_page_builder 已调用 classify_stage |
| P1-5: 子代理 prompt 注入阶段块 | ⚠️ 部分落地 | shared page 有 stage_tier，但 subagent_launcher 的 child_task 不含 stage 信息 |
| P1-6: research_planner 按 tier 调整 | ✅ 已落地 | 已有 stage_tier 字段 |
| P1-7/8: assembler 改造 | ❌ 未落地 | 风险矩阵、估值、DD 清单仍是旧逻辑 |
| P2 全部 | ❌ 未落地 | judgment / section_package / reconciler 均未改 |
| P3 全部 | ❌ 未落地 | presearch / company_verify / ubtech_docx 均未改 |

**结论：v1 的 P0 基本完成，P1 完成约 40%，P2/P3 全部未动。下面重点补充 v1 遗漏的问题。**

---

## 一、v1 未覆盖的盲区（代码审查新发现）

### 1.1 claim_coverage 判定逻辑有根本性缺陷

**文件**：`bp_claim_coverage_validator.py`

**当前逻辑**（`_derive_evidence_profile`）：
```
有 counter_evidence → contradicted
全部 fact 的 source_tier 为 bp/unknown → unverified
有 authoritative tier (official/regulatory/database/customer_or_partner_disclosure/public_tender) → supported
有外部来源但只有一个域名 → partially_supported
没有任何 fact_ids → not_addressed
```

**乾昇真空案例暴露的问题**：
BC005 "客户、订单、收入可以被独立验证" 被标记为 `supported`。原因是关联的 fact（BP-CUSTOMER_REVENUE_VALIDATION-F001）的 source_tier 是 `news`（多源搜索结果），不是 `bp`。但该 fact 的内容是 **"经8+独立搜索，未找到任何外部证据"** —— 搜索结果为"无"本身是一个负面发现，不应让 claim 变成 `supported`。

**根因**：判定逻辑只看 source_tier 的"等级"，不看 source 内容是否真正支持 claim。

**修复方案**：
```python
def _derive_evidence_profile(claim, fact_index):
    fact_ids = claim.get("fact_ids", [])
    facts = [fact_index[fid] for fid in fact_ids if fid in fact_index]
    
    # 新增：检查是否有 counter_evidence 标记
    if claim.get("counter_evidence"):
        return "contradicted"
    
    if not facts:
        return "not_addressed"
    
    # 新增：检查 fact 内容是否为"否定性发现"
    negative_findings = 0
    for f in facts:
        claim_text = str(f.get("claim", "")).lower()
        if any(kw in claim_text for kw in ("未找到", "未发现", "无外部证据", "无法验证", "不存在", "no evidence", "not found")):
            negative_findings += 1
    
    if negative_findings > 0 and negative_findings >= len(facts) * 0.5:
        # 超过一半的 fact 是否定性发现 → claim 未被支持
        return "unverified"
    
    # 原有逻辑继续...
    authoritative_tiers = {"official", "regulatory", "database"}
    # 移除 customer_or_partner_disclosure 和 public_tender（可靠性不足）
    ...
```

### 1.2 delivery_gate 与 claim_coverage 的 PASS_WITH_DISCLOSURE 矛盾

**文件**：`bp_delivery_gate.py` 行 102-103

**问题**：claim_coverage_validator 返回 `ok: True`（verdict != FAIL），但 delivery_gate 把 `PASS_WITH_DISCLOSURE` 当作阻止交付。这导致：coverage 自己认为通过了，gate 却说没通过。

**修复**：改为统一——`PASS_WITH_DISCLOSURE` 允许交付但附加披露声明：
```python
elif coverage_verdict == "PASS_WITH_DISCLOSURE":
    checks.append({"name": "claim_coverage", "ok": True, "warning": "DISCLOSURE_REQUIRED", ...})
```

### 1.3 shared_page_builder 中风险 severity 全为 medium

**文件**：`bp_shared_page_builder.py` 行 217

**问题**：
```python
risks.append({"severity": "medium", "is_deal_breaker": False})
```
所有 counter_evidence 都被硬编码为 medium + 非 deal breaker。即使发现"创始人被列入失信名单"这种严重风险，也不会被标记。

**修复**：从 section package 的 counter_evidence 中读取原始 severity，或通过 stage_meta 的 risk_severity_override 计算。

### 1.4 交付门禁缺少关键检查项

**文件**：`bp_delivery_gate.py`

**缺失检查**：
1. **来源渲染完整性**：DOCX 中来源列表不能为空（当前 `_strip_source_section` 可能过滤掉全部来源）
2. **论证链可追溯**：synthesis.md 中每个关键结论至少有 1 个非 BP 来源的 fact 支撑
3. **claim_coverage 实际分布**：不应只看 verdict，还应检查 unverified 占比（如 >50% 的 critical claim 为 unverified 应阻止交付）
4. **对抗验证 WARN 数量**：当前只看 FAIL=0，不关注 WARN 数量。乾昇真空有 4 个 WARN 仍然通过了

### 1.5 对抗验证的硬编码行业术语

**文件**：`verification_agent.py`

**问题位置**：
- 行 973-1004：双重计价检测硬编码"阿里云"和"AI/MaaS"
- 行 741-761：ADC-7 中 `core_p0` 包含 "EDA替代"、"EDA预案"
- 行 194-201：年份匹配 `20[0-2]\d` 过于宽泛

**修复**：
1. 双重计价检测仅在行业关键词匹配时启用
2. ADC-7 的 P0 列表从 stage_meta 或行业配置中动态获取
3. 年份匹配排除"20XX年营收"等常见表述中的年份

### 1.6 bp_readability_reviewer 的技术术语列表硬编码

**文件**：`bp_readability_reviewer.py` 行 12

```python
_TECH_TERMS_REQUIRING_EXPLANATION = ("RHBD", "ASIC", "MEMS", "FPGA", "SoC", "SaaS", "API")
```

完全只覆盖电子/SaaS。对生物医药、新能源、消费行业完全不适用。

**修复**：从 profile 的 `industry` / `sub_industry` 动态生成术语列表，或从 synthesis 中提取高频英文缩写。

### 1.7 bp_investment_judgment 的 dealbreaker 计数逻辑

**文件**：`bp_investment_judgment.py` 行 166-168

```python
dealbreaker_count = sum(
    len(d["risk_flags"]) for d in dimensions if "dealbreaker" in d["slug"].lower()
)
```

只统计 slug 包含 "dealbreaker" 的维度的 risk_flags。如果团队维度发现了严重风险（如创始人失信），不会被计入 dealbreaker_count，导致 overall_risk 被低估。

**修复**：扫描所有维度的 risk_flags，按 severity 判断是否为 deal breaker，而非按维度 slug。

### 1.8 子代理 prompt 缺少共享状态上下文

**文件**：`bp_subagent_launcher.py` / `bp_subagent_launcher_wb.py`

child_task 中不包含 `bp_shared_diligence_page.md` 或 `bp_shared_state.json` 路径。8 个子代理各自独立分析，无法了解其他维度的发现。

**修复**：在 brief 中添加 shared state 文件路径，让子代理可以读取前序维度的发现。

### 1.9 子代理无重试机制

**文件**：`bp_subagent_launcher.py` 行 126-131

spawn 失败直接返回 `spawn_failed`，无重试。瞬时网络问题会导致整个维度缺失。

**修复**：添加 3 次重试 + 指数退避。

---

## 二、v1 已规划但未实施的补充细节

### 2.1 assembler 风险矩阵：占位符消除

**文件**：`bp_narrative_assembler.py`

v1 方案提到了改造方向，但没给出具体的 `_generate_impact_text` 和 `_stage_aware_action` 实现。

**具体实现**：

```python
def _generate_impact_text(item: dict, stage_meta: dict) -> str:
    """根据风险内容和阶段生成影响描述。"""
    risk = item.get("risk", "")
    severity = item.get("severity", "medium")
    
    if "客户" in risk or "收入" in risk:
        if not stage_meta.get("customer_required"):
            return "早期阶段正常，不影响投资判断"
        return f"直接影响收入确认，{stage_meta['label']}阶段需重点验证"
    
    if "团队" in risk or "履历" in risk:
        return "影响团队可信度和执行力评估"
    
    if "技术" in risk or "专利" in risk:
        return "影响技术壁垒真实性和可持续性"
    
    if "估值" in risk:
        return "影响估值合理性和投资条件谈判"
    
    return f"需进一步验证，影响投资决策"

def _stage_aware_action(item: dict, stage_meta: dict) -> str:
    """根据阶段给出处置建议。"""
    risk = item.get("risk", "")
    
    if stage_meta.get("stage_tier") == "T1":
        if "客户" in risk or "收入" in risk:
            return "天使轮不要求，但建议访谈 2-3 个早期试用方"
        if "团队" in risk:
            return "P0：要求提供 LinkedIn 或前雇主证明"
    
    # 通用逻辑
    severity = item.get("severity", "medium")
    if severity == "high":
        return "要求公司提供书面说明和相关证据"
    return "下一轮 DD 中重点关注"
```

### 2.2 assembler 估值章节阶段化

v1 方案给出了 `_valuation_section` 框架，但缺少与 synthesis 的衔接。

**补充**：synthesis 中的估值章节已经比较完整（有估值区间、折价逻辑），assembler 应直接引用 synthesis 的估值结论而非重新计算。

### 2.3 assembler 摘要表改造

v1 说"结论列限制 40 字 + 章节引用"，但没给出具体实现。

**补充实现**：
```python
def _summary_table_row(dimension: str, conclusion: str, section_ref: str) -> list:
    """摘要表一行：维度 | 一句话结论(≤40字) | 详见章节"""
    compact = conclusion[:37] + "..." if len(conclusion) > 40 else conclusion
    return [dimension, compact, f"详见{section_ref}"]
```

### 2.4 DOCX 字体 fallback 链

v1 说"统一微软雅黑"，但 macOS 上 Microsoft YaHei 不一定存在。

**修复**：
```python
# 字体 fallback 链
_FONT_CHAIN = ["Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB"]

def _pick_font():
    """选择系统中可用的中文字体。"""
    import subprocess
    for font in _FONT_CHAIN:
        # macOS: 检查字体是否安装
        result = subprocess.run(
            ["system_profiler", "SPFontsDataType"],
            capture_output=True, text=True, timeout=5
        )
        if font.lower() in result.stdout.lower():
            return font
    return _FONT_CHAIN[0]  # fallback 到第一个

# 实际使用时
_font_name = _pick_font()
run.font.name = _font_name
rFonts.set(qn('w:eastAsia'), _font_name)
```

### 2.5 DOCX 来源渲染修复

**文件**：`build_bp_dd_report_docx.py`

`_strip_source_section` 中的 URL 过滤条件 `src["url"].startswith("http")` 过滤掉了大量没有 URL 但有名称的来源（如"QCC工商数据"、"BP自述"、"行业常识"）。

**修复**：
```python
# 原过滤条件
if src.get("url", "").startswith("http"):
    rendered_sources.append(src)

# 修改为：保留所有有名称的来源
if src.get("name") or src.get("url"):
    rendered_sources.append(src)
```

同时，在 `_render_markdown_to_doc` 中，确保脚注定义 `[^N]: xxx` 在文末完整渲染。

### 2.6 thesis_reconciler 的 deal_breaker 去重

**文件**：`bp_thesis_reconciler.py` 行 82

两个来源的 deal_breaker 直接拼接，同一风险措辞不同会被当两条保留。

**修复**：用模糊匹配去重（如 Jaccard 相似度 > 0.6 视为同一条）。

### 2.7 ir_section_package 的 JSON 提取

**文件**：`ir_section_package.py` 行 25

```python
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)
```

非贪婪匹配 `*?` 在嵌套 `{}` 时会提前截断。

**修复**：改用括号计数法提取完整 JSON，或使用 `json.JSONDecoder().raw_decode()` 从第一个 `{` 开始解析。

---

## 三、统稿 Agent Prompt 优化（synthesis 质量的核心）

这是 v1 完全没有触及的部分。synthesis 质量取决于统稿 Agent 的 prompt，但当前 prompt 存在以下问题：

### 3.1 表格规范缺失

**问题**：子代理输出的 synthesis.md 中，表格单元格塞入了段落式论述。

**Prompt 补充**：
```
## 表格使用规范
- 表格仅用于结构化数据对比（数字、状态、等级、日期）
- 论述、分析、推理过程必须放在正文段落中，不得放入表格
- 表格单元格内容不超过 40 字
- 如需在表格中引用详细内容，使用"详见 §X.Y"格式
```

### 3.2 论证链保留

**问题**：统稿时把子代理的搜索审计、详细分析压缩成结论，丢失了推理链。

**Prompt 补充**：
```
## 论证链保留要求
- 每个关键结论必须包含"推理过程"：你搜了什么 → 发现了什么 → 为什么得出这个结论
- 禁止只输出结论不输出推理。例如：
  ❌ "经8+搜索，未找到任何外部证据"
  ✅ "通过 NeoData、百度搜索、天眼查、企查查等渠道，以'乾昇真空 客户''乾昇真空 订单''乾昇真空 合同'等关键词进行8次独立搜索，均未找到具体客户名称、订单金额或合同信息。这一结果在半导体行业中较为异常——即使是早期公司，通常也会有创始人前雇主客户或合作方的公开提及。"
```

### 3.3 天使轮适配

**Prompt 补充**：
```
## 早期公司评估框架
当前标的为天使轮公司，请注意：
- 无公开客户和收入是正常状态，不应作为 red flag
- 评估重点应放在：团队背景真实性、技术差异化、市场空间、关联交易风险
- 不要使用成熟公司的尽调模板（如要求审计报告、规模化收入证明）
- 估值应使用可比交易法，禁用 PE 和 DCF
- 折价上限不超过 35%
```

### 3.4 重复内容消除

**问题**：每个维度子代理输出都带"本章回答的问题"引导语，统稿后出现多次重复。

**Prompt 补充**：
```
## 去重规则
- 每个章节只保留一个"本章核心问题"引导语
- 如果多个维度输出了相同结论，只保留一次，标注"多维交叉验证"
- 执行摘要和各章节的内容不应重复——摘要只写结论，章节展开论证
```

---

## 四、完整改动清单（v1 + v2 合并）

### P0：阻塞性问题（必须立即修复）

| # | 改动 | 文件 | v1 覆盖 | 说明 |
|---|------|------|---------|------|
| 1 | claim_coverage 否定性发现判定 | `bp_claim_coverage_validator.py` | ❌ 新增 | "未找到"类 fact 不应让 claim 变 supported |
| 2 | delivery_gate PASS_WITH_DISCLOSURE 修复 | `bp_delivery_gate.py` | ❌ 新增 | 允许交付但附加披露声明 |
| 3 | DOCX 来源渲染过滤修复 | `build_bp_dd_report_docx.py` | ❌ 新增 | 去掉 URL 强制过滤，保留所有有名称的来源 |
| 4 | DOCX 字体 fallback | `build_bp_dd_report_docx.py` | ⚠️ v1 部分 | 添加 PingFang SC 等 macOS fallback |
| 5 | 统稿 prompt：表格规范 | 统稿 Agent prompt | ❌ 新增 | 表格只放数据，论述放正文 |
| 6 | 统稿 prompt：论证链保留 | 统稿 Agent prompt | ❌ 新增 | 禁止只输出结论不输出推理 |
| 7 | 统稿 prompt：天使轮适配 | 统稿 Agent prompt | ❌ 新增 | 评估框架按阶段调整 |

### P1：核心质量提升

| # | 改动 | 文件 | v1 覆盖 | 说明 |
|---|------|------|---------|------|
| 8 | shared_page_builder 风险 severity 动态化 | `bp_shared_page_builder.py` | ❌ 新增 | 从 section package 读取原始 severity |
| 9 | 子代理 prompt 注入 stage_block | `bp_subagent_launcher_wb.py` | ⚠️ v1 部分 | child_task 中加入 stage 信息 |
| 10 | assembler 风险矩阵占位符消除 | `bp_narrative_assembler.py` | ✅ v1 有 | 补充了具体实现 |
| 11 | assembler 估值/DD 清单阶段化 | `bp_narrative_assembler.py` | ✅ v1 有 | 直接引用 synthesis 估值结论 |
| 12 | assembler 摘要表改造 | `bp_narrative_assembler.py` | ✅ v1 有 | 补充了具体实现 |
| 13 | 交付门禁增加检查项 | `bp_delivery_gate.py` | ❌ 新增 | 来源完整性、claim 分布、对抗 WARN |
| 14 | 统稿 prompt：去重规则 | 统稿 Agent prompt | ❌ 新增 | 消除重复的章节引导语 |

### P2：精细化

| # | 改动 | 文件 | v1 覆盖 | 说明 |
|---|------|------|---------|------|
| 15 | investment_judgment dealbreaker 计数修复 | `bp_investment_judgment.py` | ❌ 新增 | 扫描所有维度的 risk_flags |
| 16 | investment_judgment 按 tier 调整阈值 | `bp_investment_judgment.py` | ✅ v1 有 | — |
| 17 | section_package 按 tier 调整 gap 严重度 | `ir_section_package.py` | ✅ v1 有 | — |
| 18 | thesis_reconciler 按 tier 放宽降级 | `bp_thesis_reconciler.py` | ✅ v1 有 | — |
| 19 | thesis_reconciler deal_breaker 模糊去重 | `bp_thesis_reconciler.py` | ❌ 新增 | Jaccard > 0.6 视为同一条 |
| 20 | readability_reviewer 技术术语动态化 | `bp_readability_reviewer.py` | ❌ 新增 | 从 profile industry 动态生成 |
| 21 | 对抗验证行业硬编码清理 | `verification_agent.py` | ❌ 新增 | 双重计价/EDA 改为条件启用 |
| 22 | ir_section_package JSON 提取修复 | `ir_section_package.py` | ❌ 新增 | 括号计数法替代正则 |

### P3：长期优化

| # | 改动 | 文件 | v1 覆盖 | 说明 |
|---|------|------|---------|------|
| 23 | presearch 搜索计划按 tier 调权重 | `bp_presearch.py` | ✅ v1 有 | — |
| 24 | company_verify 宽松策略细化 | `bp_company_verify.py` | ✅ v1 有 | — |
| 25 | 子代理 spawn 重试机制 | `bp_subagent_launcher.py` | ❌ 新增 | 3 次重试 + 指数退避 |
| 26 | 子代理 brief 添加 shared state | `bp_subagent_launcher.py` | ❌ 新增 | 让子代理可读取前序发现 |
| 27 | 删除 generate_ubtech_docx.py | 删除文件 | ✅ v1 有 | — |
| 28 | 对抗验证 WARN 阈值收紧 | `verification_agent.py` | ❌ 新增 | 2 条 WARN 即触发整体 WARN |

---

## 五、实施顺序建议

### 第一轮：修复"报告不可用"问题（对应乾昇真空报告的核心痛点）

1. **#3 DOCX 来源渲染修复** → 引用的资料不再丢失
2. **#4 DOCX 字体 fallback** → 视觉不再混乱
3. **#5 #6 #7 统稿 prompt 三板斧** → 表格规范 + 论证链 + 天使轮适配
4. **#1 claim_coverage 判定修复** → BC005 不再被误判为 supported
5. **#2 delivery_gate 修复** → PASS_WITH_DISCLOSURE 不再阻塞交付

### 第二轮：提升报告质量

6. **#8 风险 severity 动态化** → 风险评级不再全是 medium
7. **#9 子代理 prompt 注入 stage** → 子代理输出就有阶段感知
8. **#10-#12 assembler 改造** → 如果仍保留 assembler 作为快速浏览版
9. **#13 交付门禁增强** → 防止低质量报告漏出
10. **#14 去重规则** → 消除重复内容

### 第三轮：精细化

11. **#15-#22** P2 全部
12. **#23-#28** P3 全部

---

## 六、验收标准（补充 v1 未覆盖的场景）

### v1 已有验收标准（保留）：
- 乾昇真空：风险矩阵、估值、DD 清单、来源引用、交付物
- B 轮案例交叉验证

### v2 新增验收标准：

**claim_coverage 准确性**：
- [ ] BC005 "客户可以被独立验证" + 搜索结果为"未找到" → status 应为 `unverified`，不是 `supported`
- [ ] claim_coverage_gate.json 中 `unverified` 数量 > 0（不再全部 supported）

**来源完整性**：
- [ ] DOCX 末尾"来源与参考"章节非空
- [ ] 来源数量 ≥ synthesis.md 脚注定义数量的 80%（允许少量过滤）

**论证链可追溯**：
- [ ] synthesis.md 中每个"经搜索/未找到"类结论，包含搜索渠道、关键词等具体信息
- [ ] 不出现"经N+搜索，未找到任何外部证据"这种无细节结论

**字体 fallback**：
- [ ] macOS 上 DOCX 使用 PingFang SC 或可用字体
- [ ] Windows 上使用 Microsoft YaHei

**交付门禁**：
- [ ] synthesis 来源列表为空时，delivery_gate 报 WARN
- [ ] 对抗验证 WARN ≥ 3 时，delivery_gate 报 WARN

**统稿去重**：
- [ ] synthesis.md 中"本章回答的问题"只出现一次（在第一个正式章节）
- [ ] 执行摘要和正文不出现 3 次以上相同的 50 字段落

---

## 七、文件改动总清单（v1 + v2 合并去重）

| 文件 | 改动类型 | 涉及项 |
|------|---------|--------|
| `scripts/bp_claim_coverage_validator.py` | 修改 | #1 |
| `scripts/bp_delivery_gate.py` | 修改 | #2, #13 |
| `scripts/build_bp_dd_report_docx.py` | 修改 | #3, #4 |
| 统稿 Agent prompt（位置待确认） | 修改 | #5, #6, #7, #14 |
| `scripts/bp_shared_page_builder.py` | 修改 | #8 |
| `scripts/bp_subagent_launcher_wb.py` | 修改 | #9 |
| `scripts/bp_narrative_assembler.py` | 修改 | #10, #11, #12 |
| `scripts/bp_investment_judgment.py` | 修改 | #15, #16 |
| `scripts/ir_section_package.py` | 修改 | #17, #22 |
| `scripts/bp_thesis_reconciler.py` | 修改 | #18, #19 |
| `scripts/bp_readability_reviewer.py` | 修改 | #20 |
| `scripts/verification_agent.py` | 修改 | #21, #28 |
| `scripts/bp_subagent_launcher.py` | 修改 | #25, #26 |
| `scripts/bp_presearch.py` | 修改 | #23 |
| `scripts/bp_company_verify.py` | 修改 | #24 |
| `scripts/generate_ubtech_docx.py` | 删除 | #27 |
| `scripts/phase_runner.py` | 已改 | — |
| `scripts/deliver_ir_report.py` | 已改 | — |
| `scripts/bp_stage_utils.py` | 已建 | — |
| `scripts/bp_research_planner.py` | 已改 | — |
