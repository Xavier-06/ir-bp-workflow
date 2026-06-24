# IR Report Quality Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 IR 管线从“能生成研报”升级为“只能交付质量达标研报”，通过来源治理、事实卡、估值一致性校验、对抗验证硬阻断和回归测试，稳定产出可交付的券商版研报。

**Architecture:** 采用“证据先行、声明可追溯、失败即阻断”的质量架构。检索/提取阶段产出结构化 evidence 和 claim cards；生成阶段只能引用已登记事实；交付阶段执行硬门禁，任何内部泄露、占位残留、无来源关键数字、估值自相矛盾都阻断 DOCX 生成和外部交付。

**Tech Stack:** Python 3.13, existing `runtime/profiles/ir_profile.py`, `scripts/verification_agent.py`, `scripts/ir_quality_gate.py`, `routing/source_router.py`, existing task artifacts under `data/tasks`, pytest tests to be added under `tests/`.

---

## 0. 当前问题判断

这不是单个 prompt 能修好的问题。当前 IR 管线的核心缺陷是：

1. **质量门禁太弱**：`scripts/ir_quality_gate.py` 主要看字数、URL 数、官方域名数量，无法判断事实、估值和逻辑是否正确。
2. **验证失败不阻断**：`runtime/profiles/ir_profile.py:_run_delivery_inner()` 里已运行 `run_verification()`，但无论 verdict 如何，后续仍继续生成 DOCX 和交付。
3. **来源没有强治理**：`routing/source_router.py` 有来源分类能力，但还没有形成“核心事实必须来自权威源”的硬约束。
4. **数字声明没有生命周期**：报告里出现的金额、百分比、目标价、估值参数没有统一 claim card，导致统稿阶段可以引入无来源数字。
5. **估值一致性校验不足**：已有 `verification_agent.py` 规则能检测部分双重计价，但正则脆弱，不能覆盖 DCF vs 目标价、压力情景过乐观、协同调整无计算过程等问题。

---

## 1. 目标质量标准

最终研报必须满足以下门槛才允许生成 DOCX：

### 1.1 硬失败项，任一命中即阻断

- 出现内部管线术语：`Step1/Step6b/phase/task_id/internal prompt` 等。
- 出现占位残留：`待核实`、`待补`、`TODO`、`无法验证`、`需要进一步确认`。
- 出现对外交付污染：`内部研究讨论稿`、`非投资建议`、`草稿`。
- 关键数字没有来源卡：营收、市值、利润率、增长率、估值倍数、目标价、DCF、SOTP、市场规模。
- 核心事实来源不是权威源：财务数据不用 SEC/HKEX/年报/公告/公司 IR；管理层不用公司官网/公告/年报/监管文件。
- 估值自相矛盾：DCF 与目标价偏离过大但无解释；SOTP 与 AI/MaaS 独立估值重复计价；协同调整/扣减无公式；压力情景仍显著上涨。
- 验证器 verdict 为 `FAIL` 或 `ERROR`。

### 1.2 软警告项，累计超过阈值阻断

- 同业对比不足。
- 风险提示少于 300 字。
- 估值方法少于两种。
- 情景分析缺失。
- 来源时效性超过配置阈值。
- 大纲章节和正文结构不一致。

建议阈值：`WARN >= 3` 阻断，除非人工 override。

---

## 2. 目标管线架构

```text
phase0_preflight
  ↓
phase02_company_verify
  ↓
phase1_presearch
  ├─ query decomposition
  ├─ source policy scoring
  └─ search result manifest
  ↓
phase15_extract
  ├─ content extraction
  ├─ evidence cards
  └─ source metadata normalization
  ↓
phase12_precompute
  ├─ financial baseline
  ├─ peer baseline
  └─ valuation baseline
  ↓
phase4_dispatch_prepare / collect
  ├─ step outputs
  ├─ claim cards per step
  └─ step quality gate v2
  ↓
phase5_delivery
  ├─ final verification gate
  ├─ source audit gate
  ├─ valuation consistency gate
  ├─ document contamination gate
  └─ only if PASS: generate DOCX + deliver
```

核心变化：

- 先有 evidence，再有 claim。
- 先过 gate，再写报告。
- 验证失败不再“提示一下”，而是直接阻断。

---

## 3. P0：交付硬阻断与显性缺陷修复

### Task 1: Add delivery hard stop when verification fails

**Files:**
- Modify: `runtime/profiles/ir_profile.py:551-626`
- Test: `tests/runtime/test_ir_delivery_gate.py`

**Step 1: Write failing test**

Create `tests/runtime/test_ir_delivery_gate.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import runtime.profiles.ir_profile as ir_profile


def test_delivery_blocks_docx_when_verification_fails(monkeypatch, tmp_path):
    called = {"docx": False}

    def fake_run_verification(task_id, pipeline):
        return {
            "verdict": "FAIL",
            "summary": "VERDICT: FAIL",
            "checks": [{"name": "占位提示残留", "result": "FAIL"}],
        }

    def fake_subprocess_run(*args, **kwargs):
        called["docx"] = True
        raise AssertionError("DOCX builder must not run when verification fails")

    monkeypatch.setattr("scripts.verification_agent.run_verification", fake_run_verification)
    monkeypatch.setattr(ir_profile.subprocess, "run", fake_subprocess_run, raising=False)
    monkeypatch.setattr(ir_profile, "_workspace_for", lambda job_ctx: None)

    job_ctx = SimpleNamespace(job_id="TASK-TEST", metadata={})
    result = ir_profile._run_delivery_inner(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["blocked"] is True
    assert result["result"]["block_reason"] == "verification_failed"
    assert called["docx"] is False
```

**Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/runtime/test_ir_delivery_gate.py -v
```

Expected: FAIL because `_run_delivery_inner()` currently continues to DOCX generation.

**Step 3: Implement minimal hard stop**

In `runtime/profiles/ir_profile.py`, immediately after `verification_verdict = verification.get("verdict", "UNKNOWN")` and workspace sync, add:

```python
    blocking_verdicts = {"FAIL", "ERROR", "UNKNOWN"}
    if verification_verdict in blocking_verdicts:
        return {
            "ok": False,
            "mode": "legacy_wrapped",
            "phase": "phase5_delivery",
            "job_id": job_ctx.job_id,
            "result": {
                "blocked": True,
                "block_reason": "verification_failed",
                "verification_verdict": verification_verdict,
                "verification_summary": verification.get("summary", ""),
                "verification_path": verification_path,
                "docx_path": "",
                "docx_error": "Blocked by verification gate",
                "delivery_ok": False,
                "delivery_error": "Blocked by verification gate",
                "delivery_quality": verification_verdict.lower(),
            },
        }
```

**Step 4: Run test to verify it passes**

Run:

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/runtime/test_ir_delivery_gate.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add runtime/profiles/ir_profile.py tests/runtime/test_ir_delivery_gate.py
git commit -m "fix(ir): block delivery when verification fails"
```

---

### Task 2: Strengthen final contamination checks

**Files:**
- Modify: `scripts/verification_agent.py:228-279`
- Test: `tests/scripts/test_verification_contamination.py`

**Step 1: Write failing tests**

Create `tests/scripts/test_verification_contamination.py`:

```python
from scripts.verification_agent import AdversarialVerifier


def run_text(text):
    verifier = AdversarialVerifier(pipeline="ir")
    result = verifier.run(text)
    return result


def test_blocks_internal_step_terms():
    text = "本报告综合 Step6b_valuation 和 Step7 风险分析形成结论。" * 30
    result = run_text(text)
    assert result["verdict"] == "FAIL"
    assert any(c["result"] == "FAIL" and "内部" in c["name"] for c in result["checks"])


def test_blocks_to_be_verified_placeholder():
    text = "管理层 CTO 学历背景待核实。" * 30
    result = run_text(text)
    assert result["verdict"] == "FAIL"
    assert any(c["result"] == "FAIL" and "占位" in c["name"] for c in result["checks"])


def test_blocks_internal_draft_phrase():
    text = "内部研究讨论稿 — 非投资建议。" * 30
    result = run_text(text)
    assert result["verdict"] == "FAIL"
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_verification_contamination.py -v
```

Expected: At least one case fails or is weakly classified.

**Step 3: Update patterns**

In `scripts/verification_agent.py`, expand `LEAK_PATTERNS` and `PLACEHOLDER_PATTERNS` to include:

```python
LEAK_PATTERNS += [
    (r'\bStep\s*\d+[a-zA-Z_]*\b', '内部Step标记'),
    (r'\bstep\s*\d+[a-zA-Z_]*\b', '内部step标记'),
    (r'phase\d+[_a-zA-Z]*', '内部Phase标记'),
    (r'task[_-]?id|job[_-]?id', '内部任务ID'),
    (r'内部研究讨论稿|非投资建议|草稿|draft', '交付污染标记'),
]

PLACEHOLDER_PATTERNS += [
    (r'待核实|待确认|待补充|待补|TODO|TBD', '占位/待核实残留'),
    (r'无法验证|无法获取|需要进一步确认', '未闭环声明'),
]
```

If these constants are tuples rather than mutable lists, convert safely to lists before extension.

**Step 4: Run tests**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_verification_contamination.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/verification_agent.py tests/scripts/test_verification_contamination.py
git commit -m "fix(ir): hard fail report contamination markers"
```

---

### Task 3: Add valuation consistency gate

**Files:**
- Modify: `scripts/verification_agent.py:926-1056`
- Test: `tests/scripts/test_ir_valuation_consistency.py`

**Step 1: Write failing tests**

Create `tests/scripts/test_ir_valuation_consistency.py`:

```python
from scripts.verification_agent import AdversarialVerifier


def verify(text):
    verifier = AdversarialVerifier(pipeline="ir")
    return verifier.run(text * 20)


def test_blocks_dcf_target_price_large_gap_without_explanation():
    text = "DCF 每股价值 598 美元，最终目标价 258 美元。"
    result = verify(text)
    assert result["verdict"] == "FAIL"
    assert any("DCF" in c["output"] or "目标价" in c["output"] for c in result["checks"])


def test_blocks_pressure_case_still_upside():
    text = "压力情景目标价 140 美元，相对当前股价上涨 11.6%。"
    result = verify(text)
    assert result["verdict"] == "FAIL"


def test_blocks_subjective_synergy_adjustment_without_formula():
    text = "防双重计价扣减 -300 亿美元，协同效应调整 +200 亿美元。"
    result = verify(text)
    assert result["verdict"] == "FAIL"
```

**Step 2: Run test to verify it fails**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_valuation_consistency.py -v
```

Expected: FAIL because current verifier does not fully cover these cases.

**Step 3: Implement `check_ir_valuation_consistency()`**

In `scripts/verification_agent.py`, add a method under `AdversarialVerifier`:

```python
    def check_ir_valuation_consistency(self, text: str):
        issues = []

        dcf = re.search(r'DCF.*?(\d{2,4})\s*美元', text, re.I | re.S)
        target = re.search(r'目标价.*?(\d{2,4})\s*美元', text, re.I | re.S)
        if dcf and target:
            dcf_val = float(dcf.group(1))
            target_val = float(target.group(1))
            if dcf_val > 0 and abs(dcf_val - target_val) / target_val > 0.5:
                window = text[max(0, target.start() - 300): target.end() + 300]
                has_explanation = any(k in window for k in ["折价", "权重", "剔除", "不采用", "辅助", "敏感性", "解释", "原因"])
                if not has_explanation:
                    issues.append(f"DCF估值{dcf_val:.0f}美元与目标价{target_val:.0f}美元偏离超过50%，且无充分解释")

        pressure = re.search(r'(压力|悲观|bear).*?目标价.*?(\d{2,4})\s*美元.*?(上涨|上行|upside).*?(\d{1,3}(?:\.\d+)?)\s*%', text, re.I | re.S)
        if pressure:
            upside = float(pressure.group(4))
            if upside > 5:
                issues.append(f"压力/悲观情景仍有{upside:.1f}%上行空间，情景定义可能失真")

        adjustments = re.findall(r'(协同|扣减|调整|防重复).*?([+-]?\d{2,4})\s*亿(?:美元|美金|元)?', text, re.S)
        for label, amount in adjustments:
            pos = text.find(amount)
            window = text[max(0, pos - 200): pos + 300]
            has_formula = any(k in window for k in ["=", "公式", "拆解", "明细", "来自", "构成", "按", "乘以", "假设"])
            if not has_formula:
                issues.append(f"{label}调整{amount}亿缺少计算公式或构成明细")

        for issue in issues:
            self.checks.append(VerificationCheck(
                name="IR Anti-Defect: 估值一致性",
                verification="检查DCF/目标价/压力情景/主观调整的一致性",
                output=issue,
                result="FAIL",
                detail="估值结论必须可解释、可复算、情景定义一致。",
            ))
```

Then call it in `run()` after `check_data_fabrication()` and before `check_ir_specific()`:

```python
        if self.pipeline == 'ir':
            self.check_ir_valuation_consistency(text)
```

**Step 4: Run tests**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_valuation_consistency.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/verification_agent.py tests/scripts/test_ir_valuation_consistency.py
git commit -m "fix(ir): add valuation consistency gate"
```

---

## 4. P1：事实卡与来源治理

### Task 4: Add source policy model

**Files:**
- Create: `scripts/ir_source_policy.py`
- Test: `tests/scripts/test_ir_source_policy.py`

**Step 1: Write failing test**

```python
from scripts.ir_source_policy import classify_source, SourceTier


def test_classifies_official_sources():
    assert classify_source("https://www.sec.gov/ixviewer/doc/action").tier == SourceTier.OFFICIAL
    assert classify_source("https://www.hkexnews.hk/listedco/listconews/sehk/2024/...").tier == SourceTier.OFFICIAL


def test_classifies_aggregators_as_auxiliary():
    assert classify_source("https://gu.qq.com/usBABA").tier == SourceTier.AUXILIARY


def test_financial_claim_requires_official_source():
    result = classify_source("https://gu.qq.com/usBABA")
    assert result.allowed_for_financial_claim is False
```

**Step 2: Implement source policy**

Create `scripts/ir_source_policy.py`:

```python
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class SourceTier(str, Enum):
    OFFICIAL = "official"
    REGULATORY = "regulatory"
    INSTITUTIONAL = "institutional"
    REPUTABLE_MEDIA = "reputable_media"
    AUXILIARY = "auxiliary"
    LOW_QUALITY = "low_quality"


OFFICIAL_DOMAINS = {
    "sec.gov", "hkexnews.hk", "cninfo.com.cn", "sse.com.cn", "szse.cn",
}
OFFICIAL_HINTS = ("ir.", "investor.", "investors.")
INSTITUTIONAL_DOMAINS = {"gartner.com", "idc.com", "frost.com", "mckinsey.com", "statista.com"}
AUXILIARY_DOMAINS = {"gu.qq.com", "finance.yahoo.com", "eastmoney.com", "xueqiu.com"}
LOW_QUALITY_DOMAINS = {"zhihu.com", "baijiahao.baidu.com", "toutiao.com"}


@dataclass(frozen=True)
class SourcePolicyResult:
    url: str
    domain: str
    tier: SourceTier
    score: int
    allowed_for_financial_claim: bool
    allowed_for_management_claim: bool
    allowed_for_valuation_claim: bool


def classify_source(url: str) -> SourcePolicyResult:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    if domain in OFFICIAL_DOMAINS or any(h in domain for h in OFFICIAL_HINTS):
        tier = SourceTier.OFFICIAL
        score = 100
    elif domain in INSTITUTIONAL_DOMAINS:
        tier = SourceTier.INSTITUTIONAL
        score = 80
    elif domain in AUXILIARY_DOMAINS:
        tier = SourceTier.AUXILIARY
        score = 40
    elif domain in LOW_QUALITY_DOMAINS:
        tier = SourceTier.LOW_QUALITY
        score = 10
    else:
        tier = SourceTier.REPUTABLE_MEDIA
        score = 60

    return SourcePolicyResult(
        url=url,
        domain=domain,
        tier=tier,
        score=score,
        allowed_for_financial_claim=tier in {SourceTier.OFFICIAL, SourceTier.REGULATORY},
        allowed_for_management_claim=tier in {SourceTier.OFFICIAL, SourceTier.REGULATORY, SourceTier.REPUTABLE_MEDIA},
        allowed_for_valuation_claim=tier in {SourceTier.OFFICIAL, SourceTier.REGULATORY, SourceTier.INSTITUTIONAL},
    )
```

**Step 3: Run tests**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_source_policy.py -v
```

Expected: PASS.

**Step 4: Integrate with existing router**

Modify `routing/source_router.py` to import and use `classify_source()` when ranking evidence. Keep this change minimal: add policy metadata to evidence items; do not rewrite router architecture.

**Step 5: Commit**

```bash
git add scripts/ir_source_policy.py routing/source_router.py tests/scripts/test_ir_source_policy.py
git commit -m "feat(ir): add source policy classification"
```

---

### Task 5: Add claim card extraction

**Files:**
- Create: `scripts/ir_claim_cards.py`
- Modify: `scripts/verification_agent.py`
- Test: `tests/scripts/test_ir_claim_cards.py`

**Step 1: Write tests**

```python
from scripts.ir_claim_cards import extract_claim_cards


def test_extracts_financial_claim_with_source():
    text = "阿里 FY2024 收入为9411.68亿元（来源：https://www.alibabagroup.com/ir）"
    cards = extract_claim_cards(text)
    assert len(cards) == 1
    assert cards[0].claim_type == "financial"
    assert cards[0].value == "9411.68亿元"
    assert cards[0].source_url.startswith("https://")


def test_flags_claim_without_source():
    text = "阿里目标价258美元，对应上行空间16.9%。"
    cards = extract_claim_cards(text)
    assert any(c.source_url == "" for c in cards)
```

**Step 2: Implement claim cards**

Create `scripts/ir_claim_cards.py`:

```python
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ClaimCard:
    claim_type: str
    value: str
    context: str
    source_url: str
    confidence: str


CLAIM_PATTERNS = [
    ("financial", r"\d+(?:\.\d+)?\s*(?:亿元|亿美元|亿港元|万元|美元|港元|元)"),
    ("percentage", r"\d+(?:\.\d+)?\s*%"),
    ("target_price", r"目标价\s*\d+(?:\.\d+)?\s*(?:美元|港元|元)"),
    ("valuation_multiple", r"(?:PE|PB|PS|EV/EBITDA|市盈率|市净率|市销率)\s*[≈=约为]*\s*\d+(?:\.\d+)?\s*x?"),
]

URL_RE = re.compile(r"https?://[^\s\]）)]+")


def extract_claim_cards(text: str) -> list[ClaimCard]:
    cards = []
    for claim_type, pattern in CLAIM_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            start = max(0, m.start() - 160)
            end = min(len(text), m.end() + 160)
            context = text[start:end]
            urls = URL_RE.findall(context)
            cards.append(ClaimCard(
                claim_type=claim_type,
                value=m.group(0),
                context=context[:320],
                source_url=urls[0] if urls else "",
                confidence="high" if urls else "low",
            ))
    return cards
```

**Step 3: Integrate with verification**

In `scripts/verification_agent.py`, update `check_data_fabrication()` to use `extract_claim_cards()` and fail hard for high-impact claims without source:

```python
from scripts.ir_claim_cards import extract_claim_cards

# inside check_data_fabrication
cards = extract_claim_cards(text)
missing = [c for c in cards if not c.source_url and c.claim_type in {"financial", "target_price", "valuation_multiple"}]
if missing:
    self.checks.append(VerificationCheck(
        name="SYS-3: 关键数字来源卡缺失",
        verification=f"抽取{len(cards)}个claim cards",
        output=f"{len(missing)}个关键声明缺少来源卡: {[c.value for c in missing[:5]]}",
        result="FAIL",
        detail="关键数字必须绑定来源URL，否则禁止交付。",
    ))
```

**Step 4: Run tests**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_claim_cards.py tests/scripts/test_verification_contamination.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/ir_claim_cards.py scripts/verification_agent.py tests/scripts/test_ir_claim_cards.py
git commit -m "feat(ir): require claim cards for key numbers"
```

---

## 5. P1：Step 质量门禁 v2

### Task 6: Replace score-only quality gate with structured gate result

**Files:**
- Modify: `scripts/ir_quality_gate.py:30-123`
- Test: `tests/scripts/test_ir_quality_gate_v2.py`

**Step 1: Write tests**

```python
from scripts.ir_quality_gate import check_step_quality_v2


def test_step_fails_when_contains_placeholder():
    result = check_step_quality_v2("管理层背景待核实。" * 100, step="step5_mgmt")
    assert result.passed is False
    assert any(i.severity == "FAIL" for i in result.issues)


def test_finance_step_requires_official_source():
    text = "收入9411亿元，来源：https://gu.qq.com/usBABA " * 50
    result = check_step_quality_v2(text, step="step4_finance")
    assert result.passed is False


def test_step_passes_with_official_source_and_claims():
    text = "FY2024收入9411亿元，来源：https://www.sec.gov/Archives/example " * 50
    result = check_step_quality_v2(text, step="step4_finance")
    assert result.passed is True
```

**Step 2: Implement structured result**

In `scripts/ir_quality_gate.py`, add dataclasses:

```python
from dataclasses import dataclass, field

@dataclass
class QualityIssue:
    severity: str
    code: str
    message: str

@dataclass
class StepQualityResult:
    step: str
    score: int
    passed: bool
    issues: list[QualityIssue] = field(default_factory=list)
```

Add `check_step_quality_v2(text: str, step: str) -> StepQualityResult` that:

- Fails on placeholders/leaks.
- Uses `extract_claim_cards()`.
- Uses `classify_source()`.
- Requires official source for `step4_finance` and management source for `step5_mgmt`.
- Requires at least 3 sources for normal analytical steps.

Keep existing `check_step_quality()` for backward compatibility; have it call v2 internally and return numeric score.

**Step 3: Run tests**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_quality_gate_v2.py -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/ir_quality_gate.py tests/scripts/test_ir_quality_gate_v2.py
git commit -m "feat(ir): add structured step quality gate"
```

---

## 6. P2：生成约束与重写闭环

### Task 7: Inject claim-card discipline into subagent prompts

**Files:**
- Modify: `scripts/ir_subagent_launcher_wb.py:323-457`
- Test: `tests/scripts/test_ir_subagent_prompt_rules.py`

**Step 1: Write test**

```python
from scripts.ir_subagent_launcher_wb import build_step_prompt


def test_prompt_requires_claim_cards():
    prompt = build_step_prompt("step4_finance", "阿里巴巴", market="us")
    assert "claim card" in prompt.lower() or "事实卡" in prompt
    assert "关键数字" in prompt
    assert "来源URL" in prompt
    assert "禁止无来源数字" in prompt
```

**Step 2: Implement prompt rules**

Add to the base prompt in `build_step_prompt()`:

```python
CLAIM CARD DISCIPLINE:
- Every key number must be written as a claim backed by a source URL.
- Key numbers include revenue, profit, growth rate, margin, market cap, valuation multiple, target price, market size, peer valuation.
- If no source is available after supplementary search, do not invent the number. Write "未找到独立来源" and omit the number from final conclusion.
- At the end of your output, include a "## Claim Cards" section with rows: claim | value | source URL | source type | date | confidence.
- 禁止无来源数字进入投资结论、估值结论和风险结论。
```

**Step 3: Run test**

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts/test_ir_subagent_prompt_rules.py -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/ir_subagent_launcher_wb.py tests/scripts/test_ir_subagent_prompt_rules.py
git commit -m "feat(ir): enforce claim card discipline in step prompts"
```

---

### Task 8: Rewrite loop only for failed dimensions

**Files:**
- Modify: `scripts/ir_subagent_launcher_wb.py:764-820`
- Modify: `runtime/profiles/ir_profile.py:460-521`
- Test: `tests/scripts/test_ir_rewrite_dispatch.py`

**Design:**

When a step fails quality gate v2, rewrite prompt must include exact failure reasons:

```text
Rewrite because:
- FAIL PLACEHOLDER: CTO background still says 待核实
- FAIL SOURCE_POLICY: revenue claim uses gu.qq.com, official source required
- FAIL CLAIM_CARD_MISSING: target price has no source URL

Rewrite only the failed sections. Preserve verified content.
```

**Acceptance:**

- Failed step produces `rewrite_manifest.json` with issue codes.
- Rewrite prompt includes issue codes and source requirements.
- Successful steps are not rewritten.

**Commit:**

```bash
git add scripts/ir_subagent_launcher_wb.py runtime/profiles/ir_profile.py tests/scripts/test_ir_rewrite_dispatch.py
git commit -m "feat(ir): rewrite only failed quality dimensions"
```

---

## 7. P2：DOCX 交付前最终审计

### Task 9: Add DOCX text extraction final audit

**Files:**
- Create: `scripts/audit_ir_docx_text.py`
- Modify: `runtime/profiles/ir_profile.py`
- Test: `tests/scripts/test_audit_ir_docx_text.py`

**Purpose:** 防止 Markdown 内容干净，但 DOCX 构建器或模板阶段重新引入污染。

**Audit checks:**

- Extract text from generated DOCX.
- Re-run `AdversarialVerifier(pipeline="ir")` on extracted text.
- Check report title, TOC, source appendix, page count basic sanity.
- If DOCX audit fails, block delivery notification.

**Commit:**

```bash
git add scripts/audit_ir_docx_text.py runtime/profiles/ir_profile.py tests/scripts/test_audit_ir_docx_text.py
git commit -m "feat(ir): audit generated docx before delivery"
```

---

## 8. Regression fixture: Alibaba defective report

### Task 10: Add regression fixture for current failed report patterns

**Files:**
- Create: `tests/fixtures/ir/alibaba_defective_excerpt.md`
- Create: `tests/scripts/test_ir_alibaba_regression.py`

**Fixture must include:**

```markdown
内部研究讨论稿 — 非投资建议
本报告综合 Step6b_valuation 与 Step7 风险分析。
CTO 学历背景待核实。
DCF 每股价值 598 美元，最终目标价 258 美元。
压力情景目标价 140 美元，相对当前股价上涨 11.6%。
阿里云SOTP估值577亿美元，AI/MaaS独立估值300亿美元。
防双重计价扣减 -300 亿美元，协同效应调整 +200 亿美元。
来源：https://gu.qq.com/usBABA
```

**Test:**

```python
from pathlib import Path
from scripts.verification_agent import AdversarialVerifier


def test_alibaba_defective_excerpt_fails():
    text = Path("tests/fixtures/ir/alibaba_defective_excerpt.md").read_text(encoding="utf-8")
    result = AdversarialVerifier(pipeline="ir").run(text * 20)
    assert result["verdict"] == "FAIL"
    failed_names = {c["name"] for c in result["checks"] if c["result"] == "FAIL"}
    assert any("内部" in n for n in failed_names)
    assert any("占位" in n for n in failed_names)
    assert any("估值" in n for n in failed_names)
```

**Commit:**

```bash
git add tests/fixtures/ir/alibaba_defective_excerpt.md tests/scripts/test_ir_alibaba_regression.py
git commit -m "test(ir): lock regression for alibaba report defects"
```

---

## 9. 验收标准

### 9.1 单元测试

Run:

```bash
PYTHONPATH=/Users/xavier/WorkBuddy/ir-bp-workflow pytest tests/scripts tests/runtime -v
```

Expected:

- All tests PASS.
- Alibaba defective fixture must FAIL verification as expected.
- Delivery gate test proves DOCX builder is not called on verification FAIL.

### 9.2 端到端验收

Run an IR task on a known entity, then inspect workspace artifacts:

Expected artifacts:

- `verification_result.json`
- `source_audit.json` or markdown equivalent
- `execution_audit.json`
- `claim_cards.json`
- `broker_report_docx` only if final verdict is PASS/WARN below threshold

Expected behavior:

- If report contains `待核实` → no DOCX.
- If report contains `Step6b_valuation` → no DOCX.
- If target price has no source → no DOCX.
- If DCF and target price conflict without explanation → no DOCX.
- If all gates pass → DOCX generated and delivery proceeds.

### 9.3 Manual quality checklist

A report is considered quality达标 only if:

- 每个关键数字可追溯到来源。
- 财务数据来自官方披露。
- 管理层信息有独立来源。
- 估值方法至少两种，且权重/折价有解释。
- 风险情景真的体现 downside。
- 来源附录没有把低质量页面当核心证据。
- 最终 DOCX 没有内部术语、占位词、任务痕迹。

---

## 10. 实施顺序

不要一口气大改。按下面顺序做：

1. **P0 Task 1**：验证失败阻断交付。
2. **P0 Task 2**：污染词硬失败。
3. **P0 Task 3**：估值一致性硬失败。
4. **Regression Task 10**：把当前阿里样本缺陷固化成回归测试。
5. **P1 Task 4**：来源策略。
6. **P1 Task 5**：claim cards。
7. **P1 Task 6**：质量门禁 v2。
8. **P2 Task 7-8**：生成约束和定向重写。
9. **P2 Task 9**：DOCX 成品审计。

如果只能先做一天，必须完成 1-4。它们能立刻防止坏报告继续交付。

---

## 11. 不做什么

- 不先大改整个检索框架。先把交付硬门禁立起来。
- 不把所有问题都塞进 prompt。Prompt 是辅助，gate 才是保险丝。
- 不依赖人工肉眼检查。人工只处理 override，不负责基础质量兜底。
- 不追求一次性完美。先用当前阿里缺陷做回归集，后续每次发现新缺陷都加 fixture。

---

## 12. Execution Handoff

Plan complete and saved to `docs/plans/2026-06-04-ir-report-quality-pipeline.md`.

Two execution options:

**1. Subagent-Driven (this session)** - Dispatch fresh subagent per task, review between tasks, fast iteration.

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints.

Recommended: choose **Subagent-Driven** for P0 Tasks 1-4, because these are tightly related and need review after each gate change.
