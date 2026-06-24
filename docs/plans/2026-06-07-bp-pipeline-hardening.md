# BP Pipeline Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 BP 尽调管线从“子代理自由写作 + 事后粗门禁”升级为“结构化证据生产 + 波次依赖正确 + 搜索可审计 + 门禁可阻断 + 交付可追溯”的投研级管线。

**Architecture:** 保留现有 shared kernel / BPProfile / WorkBuddy sub-agent dispatch 架构，但重排关键 phase，并新增 evidence/search ledger、claim-level search plan、wave quality gate、review schema gate。核心原则是：任何投资结论必须从 claim → search task → fetched source → fact → section package → coverage/cross gate → final report 全链路可追溯。

**Tech Stack:** Python 3.13, pytest, WorkBuddy Agent dispatch, existing `runtime/profiles/bp_profile.py`, `scripts/search_gateway.py`, BP sidecar JSON schemas, state files under `jobs/<job_id>/state`.

---

## Current Pipeline Diagnosis

### Actual Phase Order

Current `BPProfile` order is:

1. `phase01_document_intake`
2. `phase02_company_verify`
3. `phase03_research_plan`
4. `phase04_presearch`
5. `phase05_bp_shared_page_init`
6. `phase07_bp_fact_store_bootstrap`
7. `phase08_dispatch_prepare`
8. `phase09_dispatch_collect`
9. `phase12_bp_shared_page_refresh`
10. `phase25_competition_prepare`
11. `phase25_competition_collect`
12. `phase27_bp_shared_page_refresh`
13. `phase24_bp_claim_coverage_validation`
14. `phase11_bp_fact_store_merge`
15. `phase26_bp_section_package_validation`
16. `phase25_bp_cross_dimension_gate`
17. `phase27_synthesis_prepare`
18. `phase28_synthesis_collect`
19. `phase32_bp_ic_redteam_prepare`
20. `phase33_bp_ic_redteam_collect`
21. `phase34_bp_thesis_reconciliation`
22. `phase29_bp_debate_review`
23. `phase30_bp_final_assembly`
24. `phase31_bp_readability_review`
25. `phase33_delivery`

### Root Problems

1. **Wave2 reads stale fact store.**
   `phase25_competition_prepare` passes `bp_fact_store.json`, but `phase11_bp_fact_store_merge` currently runs after Wave2 collect. Wave2 therefore often receives an empty/bootstrap fact store.

2. **Search is prompt-level, not pipeline-level.**
   Role prompts ask for 8+ searches, but until the recent `search_audit` guard there was no enforceable evidence that agents actually searched deeply.

3. **Research plan is not compiled into search tasks.**
   `bp_research_planner.py` defines `fact_requirements`, `core_questions`, `strategic_questions`, and `claim_matrix`, but no phase turns these into claim-level search jobs with source quotas.

4. **Presearch is a seed pack, not due diligence.**
   `bp_presearch.py` uses broad templates. It can help agents start, but cannot replace structured validation of critical claims.

5. **Collect phases only check presence/length, not evidence sufficiency.**
   Wave collect checks Markdown existence and `_quality_check()`. This can accept long but shallow reports.

6. **Coverage gate depends on weak upstream status.**
   `bp_claim_coverage_validator.py` blocks unverified critical/high claims, but claim status is derived from section packages. If section packages overstate `supported`, coverage cannot see source weakness unless package validation catches it.

7. **IC/RedTeam are also free-form.**
   `phase33` only checks four files exist. It does not validate schema depth, citations, gate awareness, or whether Red Team attacked the actual weakest claims.

8. **Delivery gate is strong but too late.**
   `bp_delivery_gate.py` catches many final problems, but at that point expensive work is already done. It should be final backstop, not first reliable quality filter.

9. **Intake has no hard quality gate.**
   OCR/profile extraction can miss pages, tables, team names, financials, customer lists, or claims. Downstream phases proceed even if the BP source material is incomplete.

---

## Target Pipeline

Recommended new phase order:

1. `phase01_document_intake`
2. `phase01_intake_quality_gate` **new**
3. `phase02_company_verify`
4. `phase03_research_plan`
5. `phase04_presearch`
6. `phase05_bp_shared_page_init`
7. `phase06_search_plan_compile` **new**
8. `phase14_search_plan_gate` **new**
9. `phase07_bp_fact_store_bootstrap`
10. `phase08_dispatch_prepare` Wave1
11. `phase09_dispatch_collect` Wave1
12. `phase10_wave1_evidence_gate` **new**
13. `phase24_bp_fact_store_merge_wave1` **new or rename existing merge with wave scope**
14. `phase12_bp_shared_page_refresh` after Wave1
15. `phase25_competition_prepare` Wave2
16. `phase25_competition_collect` Wave2
17. `phase26_wave2_evidence_gate` **new**
18. `phase11_bp_fact_store_merge` all waves
19. `phase27_bp_shared_page_refresh` after Wave2
20. `phase26_bp_section_package_validation`
21. `phase24_bp_claim_coverage_validation`
22. `phase25_bp_cross_dimension_gate`
23. `phase31_evidence_gap_repair_prepare` **new optional dispatch if gates fail**
24. `phase31_evidence_gap_repair_collect` **new optional collect**
25. `phase27_synthesis_prepare`
26. `phase28_synthesis_collect`
27. `phase32_bp_ic_redteam_prepare`
28. `phase33_bp_ic_redteam_collect`
29. `phase34_bp_thesis_reconciliation`
30. `phase29_bp_debate_review`
31. `phase30_bp_final_assembly`
32. `phase31_bp_readability_review`
33. `phase33_delivery`

Key ordering changes:

- Move fact merge before Wave2.
- Validate section packages before coverage and cross-dimension gates.
- Run cross-dimension gate after section validation and coverage.
- Add explicit evidence gates immediately after each wave.
- Add gap-repair loop before synthesis, not after final report.

---

## Task 1: Add Intake Quality Gate

**Files:**
- Create: `scripts/bp_intake_quality_gate.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_intake_quality_gate.py`

**Problem:** `bp_document_intake.py` writes `bp_ocr_text.txt`, `bp_step0_profile.json`, and likely `bp_claim_inventory.json`, but there is no gate checking whether intake is complete enough for DD.

**Implementation:**

Create `evaluate_bp_intake_quality(task_dir: Path) -> dict` with checks:

- OCR text exists and length >= 2,000 chars, unless source is explicitly a short memo.
- OCR has page markers for PDF/PPT inputs or `page_count` metadata.
- `bp_step0_profile.json` has at least: `company_name/entity/project_name`, `industry/sub_industry`, `product_service`, `financing_stage` or explicit unknown.
- If OCR contains team/person sections, profile must expose team/founder/advisor fields or raise WARN/FAIL depending severity.
- If OCR contains customer/order/revenue/financial keywords, claim inventory must include corresponding claims.
- No `[OCR失败]` ratio above threshold.

**Gate output:**

```json
{
  "schema_version": "bp_intake_quality_gate.v1",
  "ok": true,
  "gate_verdict": "PASS|WARN|FAIL",
  "checks": [],
  "blocking_reasons": [],
  "profile_fields_seen": {},
  "ocr_stats": {}
}
```

**Tests:**

1. Missing OCR fails.
2. Profile without entity/product fails.
3. OCR with customer/revenue keywords but no claims warns or fails.
4. Valid minimal intake passes.

**Commit:** `feat(bp): add intake quality gate`

---

## Task 2: Compile Research Plan Into Claim-Level Search Plan

**Files:**
- Create: `scripts/bp_search_plan_compiler.py`
- Modify: `runtime/profiles/bp_profile.py`
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_search_plan_compiler.py`

**Problem:** `fact_requirements` and `claim_matrix` exist but are not operational. Agents decide searches ad hoc.

**Implementation:**

Create `bp_search_plan.json` from `bp_research_plan.json`, `bp_step0_profile.json`, `company_verify_report.json`, and presearch outputs.

Each search task should be shaped like:

```json
{
  "search_task_id": "BST-001",
  "claim_id": "BC005",
  "question_id": "BQ2",
  "owner_section": "bp_customer_revenue_validation",
  "fact_key": "customer_evidence",
  "priority": "critical",
  "query_family": "customer_revenue_validation",
  "queries": [
    "\"{entity}\" 客户 合同 订单 回款",
    "\"{entity}\" 招投标 采购 中标",
    "\"{entity}\" customer contract revenue delivery"
  ],
  "required_source_tiers": ["official", "regulatory", "customer_or_partner_disclosure", "reputable_media"],
  "min_unique_queries": 4,
  "min_fetched_urls": 2,
  "min_independent_domains": 2,
  "requires_counter_search": true,
  "status": "planned"
}
```

Rules:

- Critical claims get higher quotas.
- Customer/revenue/order/valuation claims require counter-search.
- BP-only facts cannot support `supported`; they must be `unverified` or `partially_supported` unless external sources exist.
- Wave2 tasks must inherit Wave1 gaps and contradictions.

**Tests:**

1. Critical claim generates at least 4 query templates.
2. Customer/revenue claim requires counter-search.
3. Valuation claim includes dependency on customer/revenue facts.
4. Every owner section receives only its relevant tasks.

**Commit:** `feat(bp): compile claim-level search plan`

---

## Task 3: Add Search Execution Ledger

**Files:**
- Create: `scripts/bp_search_ledger.py`
- Modify: `scripts/search_gateway.py` only if a wrapper hook is needed
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_search_ledger.py`

**Problem:** Search behavior is not auditable. `search_audit` inside section package is a start, but it is agent-reported and not centrally reconciled.

**Implementation:**

Introduce `bp_search_ledger.json` as central append-only-ish search evidence record. It records:

- `search_task_id`
- `role`
- `query`
- `engine`
- `result_count`
- `result_urls`
- `fetched_urls`
- `source_domains`
- `fetch_status`
- `used_fact_ids`
- `timestamp`

Expose helpers:

```python
def load_search_ledger(task_dir: Path) -> dict: ...
def append_search_event(task_dir: Path, event: dict) -> dict: ...
def summarize_search_coverage(task_dir: Path, role: str | None = None) -> dict: ...
```

Agent brief must tell subagents: section package `search_audit` must match ledger counts. If agents cannot write ledger events directly, they must at least write a compatible `search_audit`; the gate reconciles both when ledger exists.

**Tests:**

1. Append events dedupes URLs but keeps distinct query attempts.
2. Coverage summary counts query/domain/fetch per role and per claim.
3. Missing ledger falls back to section `search_audit` but marks `audit_source="agent_reported"`.

**Commit:** `feat(bp): add central search ledger`

---

## Task 4: Harden Section Package Evidence Gate

**Files:**
- Modify: `runtime/profiles/bp_profile.py`
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_profile_quality_phases.py`

**Current State:** A `search_audit` guard has been added. Keep it, but upgrade from role-level counts to claim-level evidence sufficiency.

**Implementation Enhancements:**

- Require `search_audit.claim_coverage`:

```json
{
  "claim_id": "BC005",
  "search_task_ids": ["BST-001", "BST-002"],
  "unique_queries": 5,
  "fetched_urls": ["..."],
  "source_domains": ["..."],
  "counter_search_done": true,
  "evidence_verdict": "supported|partially_supported|unverified|contradicted"
}
```

- For critical/high claims:
  - supported requires at least 2 independent non-BP sources or 1 authoritative source.
  - customer/revenue/order claims require at least one direct customer/official/regulatory/tender source, otherwise not `supported`.
  - valuation claims cannot use revenue/order/customer facts unless those facts are supported by external evidence.
  - counter-evidence search is mandatory for critical claims.

- Reject `source_quality="media"` with high confidence unless at least 2 independent domains exist.
- Reject fact IDs that reference facts with `source_tier="bp"` for main conclusion support.

**Tests:**

1. Shallow audit fails.
2. Critical claim with only BP source fails or becomes unverified.
3. Customer revenue claim without direct evidence fails.
4. Official/regulatory single-source fact can pass.
5. Media-only evidence requires two independent domains.

**Commit:** `fix(bp): enforce claim-level evidence sufficiency`

---

## Task 5: Reorder Wave and Fact Store Phases

**Files:**
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_profile_quality_phases.py`
- Test: `tests/scripts/test_bp_thesis_reconciliation.py`

**Problem:** Wave2 reads empty/stale `bp_fact_store.json`.

**Implementation:**

Change BPProfile order to:

```python
"phase09_dispatch_collect"
"phase10_wave1_evidence_gate"
"phase24_bp_fact_store_merge_wave1"
"phase12_bp_shared_page_refresh"
"phase25_competition_prepare"
```

Then after Wave2:

```python
"phase25_competition_collect"
"phase26_wave2_evidence_gate"
"phase11_bp_fact_store_merge"
"phase27_bp_shared_page_refresh"
"phase26_bp_section_package_validation"
"phase24_bp_claim_coverage_validation"
"phase25_bp_cross_dimension_gate"
```

If avoiding new merge function, reuse `_run_bp_fact_store_merge` but return phase name parameterized by caller.

**Tests:**

1. Phase order asserts Wave1 merge before Wave2 prepare.
2. Section validation before coverage.
3. Coverage before cross-dimension gate.
4. Cross-dimension gate before synthesis and IC/RedTeam.
5. Wave2 prepare manifest includes fact store path after Wave1 merge.

**Commit:** `fix(bp): reorder waves around fact store merge`

---

## Task 6: Add Wave Evidence Gates

**Files:**
- Create: `scripts/bp_wave_evidence_gate.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_wave_evidence_gate.py`

**Problem:** Wave collect only checks output existence and rough `_quality_check()`.

**Implementation:**

`evaluate_bp_wave_evidence_gate(task_dir, wave)` should verify:

- Expected role outputs exist.
- Expected facts sidecar exists per role.
- Expected section package exists per role.
- Section package passes schema/evidence checks.
- Search plan tasks assigned to that wave meet minimum quotas.
- Critical claims owned by completed roles are no longer `not_addressed` unless explicitly marked `data_gap` with reason.
- Missing evidence triggers `needs_repair` rather than silently continuing.

Output:

```json
{
  "schema_version": "bp_wave_evidence_gate.v1",
  "wave": 1,
  "ok": false,
  "gate_verdict": "FAIL",
  "role_results": [],
  "blocking_claims": [],
  "repair_tasks": []
}
```

**Tests:**

1. Missing facts sidecar fails.
2. Valid role package passes.
3. Critical claim not addressed fails.
4. Insufficient search audit fails.
5. Gate emits repair tasks.

**Commit:** `feat(bp): add wave evidence gates`

---

## Task 7: Add Evidence Gap Repair Loop

**Files:**
- Create: `scripts/bp_gap_repair_planner.py`
- Modify: `runtime/profiles/bp_profile.py`
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Test: `tests/scripts/test_bp_gap_repair_planner.py`

**Problem:** When gates fail, the pipeline blocks but does not automatically produce targeted补搜 instructions.

**Implementation:**

Create repair planner that reads:

- `bp_wave1_evidence_gate.json`
- `bp_wave2_evidence_gate.json`
- `bp_claim_coverage_gate.json`
- `bp_cross_dimension_gate.json`
- `bp_search_plan.json`

It writes `bp_gap_repair_plan.json`:

```json
{
  "schema_version": "bp_gap_repair_plan.v1",
  "repair_round": 1,
  "tasks": [
    {
      "repair_task_id": "BGR-001",
      "owner_section": "bp_customer_revenue_validation",
      "claim_id": "BC005",
      "reason": "CUSTOMER_REVENUE_EVIDENCE_INSUFFICIENT",
      "required_actions": ["search", "fetch", "write_fact", "update_section_package"],
      "queries": [],
      "min_fetched_urls": 2
    }
  ]
}
```

`phase31_evidence_gap_repair_prepare` dispatches only the roles with repair tasks.

**Tests:**

1. Coverage failure creates repair tasks.
2. Cross-dimension valuation/customer conflict routes to valuation and customer roles.
3. Repair plan does not dispatch unrelated roles.
4. Repeated repair beyond max rounds blocks with clear reason.

**Commit:** `feat(bp): add targeted evidence repair loop`

---

## Task 8: Upgrade Subagent Briefs From Role Instructions to Work Orders

**Files:**
- Modify: `scripts/bp_subagent_launcher_wb.py`
- Modify: `instruction_store_bp/*.md` only if duplicated prompt logic must be consolidated
- Test: `tests/scripts/test_bp_subagent_launcher_wb.py`

**Problem:** Prompts are long, but not specific enough to current claim/search tasks.

**Implementation:**

Brief should contain these mandatory blocks:

1. `Role Scope`: allowed owner claims only.
2. `Must Read First`: shared page, research plan slice, search plan slice, fact store, prior outputs if Wave2.
3. `Search Work Order`: list exact `search_task_id`s, quotas, required source tiers, counter-search requirement.
4. `Evidence Output Contract`: facts sidecar, section package, search audit, data gaps.
5. `Failure Behavior`: if quota cannot be met, explicitly mark `data_gaps`, do not upgrade to supported.
6. `Forbidden`: model memory as source, BP-only fact as high-confidence external support, generic web snippets as evidence without fetch.

**Tests:**

1. Brief includes search task IDs from `bp_search_plan.json`.
2. Wave2 brief includes Wave1 facts and gaps.
3. Brief says `supported` requires source thresholds.
4. Unknown role still refuses fallback.

**Commit:** `fix(bp): make subagent briefs claim-specific work orders`

---

## Task 9: Strengthen Claim Coverage Semantics

**Files:**
- Modify: `scripts/bp_shared_page_builder.py`
- Modify: `scripts/bp_claim_coverage_validator.py`
- Test: `tests/scripts/test_bp_claim_coverage_validator.py`

**Problem:** `supported` can be inferred too generously from any fact ID with source quality.

**Implementation:**

Coverage should classify using fact store evidence profile:

- `supported`: source threshold met and no unresolved contradiction.
- `partially_supported`: some external evidence but incomplete, weak, or single non-authoritative source.
- `unverified`: BP-only, missing direct source, no fetch, or source_quality unknown.
- `contradicted`: negative/counter facts exist.
- `not_addressed`: no claim package/fact linkage.

Add fields to each claim:

```json
{
  "evidence_strength": "authoritative|cross_verified|single_source|bp_only|none",
  "source_domain_count": 2,
  "fetched_url_count": 2,
  "counter_search_done": true,
  "blocking_gaps": []
}
```

**Tests:**

1. Fact ID with `source_tier=bp` stays unverified.
2. Official source supports claim.
3. Two media domains can partially or fully support depending confidence.
4. Contradiction overrides supported.
5. Critical high not addressed fails.

**Commit:** `fix(bp): tighten claim coverage semantics`

---

## Task 10: Expand Cross-Dimension Gate

**Files:**
- Modify: `scripts/bp_cross_dimension_gate.py`
- Test: `tests/scripts/test_bp_cross_dimension_gate.py`

**Current Checks:** basic conflicts, valuation using BP-only revenue, critical contradicted claim.

**Add Checks:**

- Valuation uses revenue/customer/order facts whose claim status is not supported.
- Market size assumptions conflict with market/supply-chain section source quality.
- Competition claims of superiority lack competitor search coverage.
- Team scale vs revenue/order/production mismatch.
- Technology moat claims lack IP/certification/performance evidence.
- Deal Breaker role found unresolved blocker but IC recommendation is `go`.
- Any package uses `confidence=high` while source evidence is weak.

**Tests:**

1. Unsupported revenue in valuation fails.
2. Competitor superiority without competitor evidence fails.
3. High confidence weak source fails.
4. Deal breaker unresolved blocks `go`.

**Commit:** `fix(bp): expand cross-dimension evidence checks`

---

## Task 11: Validate IC and Red Team Outputs

**Files:**
- Create: `scripts/bp_ic_redteam_gate.py`
- Modify: `runtime/profiles/bp_profile.py`
- Test: `tests/scripts/test_bp_ic_redteam_gate.py`

**Problem:** IC/RedTeam collect only checks files exist.

**Implementation:**

Validate `bp_investment_thesis.json`:

- schema version present.
- recommendation in allowed enum.
- must include `supporting_reasons`, `must_verify_before_investment`, `deal_breakers`, `open_data_gaps`, `confidence`.
- must cite coverage/cross gate statuses.
- `go` is forbidden if coverage/cross/delivery prechecks fail.

Validate `bp_red_team_review.json`:

- must include `issues`, `deal_breakers`, `open_data_gaps`.
- must attack top unverified critical/high claims.
- must include at least one issue or explicit “no issue found” with reason and evidence.
- every HIGH issue needs status and blocking impact.

**Tests:**

1. Missing schema fails.
2. IC `go` with failed coverage fails.
3. Red Team with empty issues and unverified critical claims fails.
4. Valid outputs pass.

**Commit:** `feat(bp): validate IC and red-team outputs`

---

## Task 12: Harden Synthesis and Final Assembly

**Files:**
- Modify: `runtime/profiles/bp_profile.py`
- Modify: `scripts/bp_narrative_assembler.py`
- Modify: `scripts/bp_readability_reviewer.py`
- Test: `tests/scripts/test_bp_narrative_assembler.py`
- Test: `tests/scripts/test_bp_readability_reviewer.py`

**Problem:** `phase28_synthesis_collect` accepts any long synthesis. Final assembly is better, but still relies on upstream valid packages.

**Implementation:**

- Prefer deterministic `bp_narrative_assembler.py` over free-form `bp_synthesis.md` for final report skeleton.
- Treat synthesis agent as commentary/analysis, not authoritative source, unless it cites section package fact IDs.
- Add synthesis gate:
  - all final claims must map to `bp_section_packages.json` or `bp_fact_store.json`.
  - no unsupported critical claims in executive summary.
  - source count in final report must not be below source count in validated packages.
  - no section with investment conclusion but no evidence matrix row.

**Tests:**

1. Synthesis containing unsupported new claim fails.
2. Final assembly omits fact IDs from packages and delivery gate fails.
3. Final report with no source references fails readability/source gate.

**Commit:** `fix(bp): make synthesis evidence-bound`

---

## Task 13: Add End-to-End Simulated Pipeline Tests

**Files:**
- Create: `tests/runtime/test_bp_pipeline_hardening_flow.py`

**Problem:** Current tests are mostly phase-unit tests. They do not verify bad evidence cannot travel end-to-end.

**Implementation:**

Create fixture builders for:

- valid intake.
- valid research plan.
- valid Wave1 package.
- shallow search package.
- Wave2 valuation using unsupported revenue.
- IC/RedTeam outputs.

Tests:

1. A shallow Wave1 package blocks before Wave2.
2. Wave2 valuation cannot run from empty fact store.
3. Unsupported revenue blocks coverage/cross gate before synthesis.
4. Valid packages proceed to final assembly.
5. Missing Red Team schema blocks delivery.

**Commit:** `test(bp): cover hardened pipeline flow`

---

## Task 14: Observability and Resume Safety

**Files:**
- Modify: `runtime/orchestrator/kernel.py`
- Modify: `runtime/orchestrator/state_store.py`
- Create: `scripts/bp_pipeline_status.py`
- Test: `tests/runtime/test_orchestrator_resume_guards.py`

**Problem:** `needs_dispatch` / `needs_poll` pauses are treated as successful pause points, but resume safety is mostly phase-order based.

**Implementation:**

- Write `phase_result.status` as `needs_dispatch`, `needs_poll`, `blocked`, `completed`.
- On resume, validate previous phase state if start phase depends on dispatch/repair completion.
- Add `bp_pipeline_status.py` that prints:
  - current phase
  - blocked reason
  - missing role outputs
  - gate failures
  - repair tasks
  - deliverability

**Tests:**

1. Resume into Wave2 prepare fails if Wave1 evidence gate missing.
2. Resume into synthesis fails if coverage/cross gate missing.
3. Status command summarizes gate failures.

**Commit:** `feat(bp): add resume guards and status report`

---

## Task 15: Update Docs and Migration Notes

**Files:**
- Modify: `docs/pipeline-phases.md`
- Modify: `docs/search-integration.md`
- Create or Modify: `docs/bp-evidence-contract.md`

**Content:**

- New phase order.
- Search plan and ledger schema.
- Section package v2 evidence contract.
- Definition of supported/partially_supported/unverified/contradicted.
- How to resume after `needs_dispatch` and gate failures.
- How to inspect `bp_search_ledger.json`, `bp_claim_coverage_gate.json`, and wave gates.

**Commit:** `docs(bp): document hardened evidence pipeline`

---

## Implementation Order

Do not try to implement everything at once. Use this sequence:

1. Intake gate.
2. Search plan compiler.
3. Search ledger.
4. Section package evidence hardening.
5. Wave/fact-store phase reorder.
6. Wave evidence gates.
7. Gap repair loop.
8. Subagent brief upgrade.
9. Claim coverage semantics.
10. Cross-dimension gate expansion.
11. IC/RedTeam validation.
12. Synthesis/final assembly hardening.
13. End-to-end tests.
14. Resume/status observability.
15. Docs.

Each task should follow TDD:

1. Write failing test.
2. Run exact test and confirm expected failure.
3. Implement minimal code.
4. Run focused test.
5. Run related BP test file.
6. Run `codegraph affected <changed files> -p /Users/xavier/WorkBuddy/ir-bp-workflow` if CodeGraph DB is healthy.
7. Commit locally.

---

## Minimal First Sprint

If we want the fastest high-impact repair, do only these first:

1. Move Wave1 fact merge before Wave2.
2. Add claim-level search plan.
3. Upgrade section package gate from role-level `search_audit` to claim-level evidence sufficiency.
4. Add Wave1/Wave2 evidence gate.
5. Add gap repair planner.

This directly attacks your core concern: 子代理信息不够、搜得泛、Wave2 拿不到足够事实、浅搜报告混过门禁。

---

## Acceptance Criteria

The hardened pipeline is acceptable only when all are true:

1. Wave2 cannot start unless Wave1 outputs, facts, section packages, and evidence gates pass or explicitly produce repair tasks.
2. A critical claim cannot be marked supported by BP-only evidence.
3. Customer/revenue/order claims cannot support valuation unless externally verified.
4. Every final report claim can trace to fact IDs and source URLs/domains.
5. Every role has auditable query/fetch/source-domain counts.
6. Red Team cannot pass without attacking unresolved critical/high claims.
7. Delivery cannot proceed if any required gate is missing, stale, or failed.
8. Resume cannot skip a failed or missing quality gate.
9. Tests include shallow-search, stale-fact-store, unsupported-valuation, missing-red-team, and valid-happy-path scenarios.

---

## Known Environment Blockers

- CodeGraph currently returns `unable to open database file`; run `codegraph status` / rebuild index before using `affected` checks.
- Previous git commit attempts failed because `.git/index.lock` could not be created due to environment permissions. Implementation should verify git write permissions before starting large changes.
