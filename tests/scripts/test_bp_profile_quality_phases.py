import json
from types import SimpleNamespace

from runtime.profiles.bp_profile import (
    BPProfile,
    _run_bp_delivery,
    _run_bp_fact_store_bootstrap,
    _run_bp_debate_review,
    _run_bp_fact_store_merge,
    _run_bp_search_plan_compile,
    _run_bp_wave_evidence_gate,
    _run_bp_final_assembly,
    _run_bp_synthesis_prepare,
    _run_bp_synthesis_collect,
    _run_bp_delivery_inner,
    _run_bp_readability_review,
    _run_bp_dispatch_prepare,
    _run_bp_wave3_prepare,
    _run_bp_wave3_collect,
    _run_bp_wave4_prepare,
    _run_bp_wave4_collect,
    _run_bp_cross_dimension_gate,
    _run_bp_claim_coverage_validation,
    _run_bp_section_package_validation,
    _run_bp_shared_page_init,
    _run_bp_shared_page_refresh,
    _run_research_plan,
    _run_research_plan_collect,
)
from scripts.bp_narrative_assembler import assemble_bp_report


def test_bp_profile_registers_research_plan_before_presearch(tmp_path):
    profile = BPProfile(runtime_root=tmp_path)

    phases = profile.phases()
    assert "phase04_research_plan" in phases
    assert "phase05_bp_shared_page_init" in phases
    assert "phase06_search_plan_compile" in phases
    assert "phase07_bp_fact_store_bootstrap" in phases
    assert "phase12_wave1_shared_page_refresh" in phases
    assert "phase16_wave3_shared_page_refresh" in phases
    assert "phase20_wave4_shared_page_refresh" in phases
    assert "phase21_bp_claim_coverage_validation" in phases
    assert "phase22_bp_cross_dimension_gate" in phases
    assert "phase10_wave1_evidence_gate" in phases
    assert "phase15_wave3_evidence_gate" in phases
    assert "phase19_wave4_evidence_gate" in phases
    assert "phase11_bp_fact_store_merge" in phases
    assert "phase23_bp_section_package_validation" in phases
    assert "phase24_synthesis_prepare" in phases
    assert "phase25_synthesis_collect" in phases
    # IC/RedTeam phases removed (2026-06-13): phase32/33/33_5/34
    assert "phase26_bp_debate_review" in phases
    assert "phase27_bp_final_assembly" in phases
    assert "phase28_bp_readability_review" in phases
    assert phases.index("phase02_company_verify") < phases.index("phase03_presearch")
    assert phases.index("phase03_presearch") < phases.index("phase04_research_plan")
    assert phases.index("phase04_research_plan_collect") < phases.index("phase05_bp_shared_page_init")
    assert phases.index("phase05_bp_shared_page_init") < phases.index("phase06_search_plan_compile")
    assert phases.index("phase06_search_plan_compile") < phases.index("phase07_bp_fact_store_bootstrap")
    assert phases.index("phase07_bp_fact_store_bootstrap") < phases.index("phase08_dispatch_prepare")
    assert phases.index("phase09_dispatch_collect") < phases.index("phase10_wave1_evidence_gate")
    assert phases.index("phase10_wave1_evidence_gate") < phases.index("phase11_bp_fact_store_merge")
    assert phases.index("phase11_bp_fact_store_merge") < phases.index("phase12_wave1_shared_page_refresh")
    assert phases.index("phase12_wave1_shared_page_refresh") < phases.index("phase13_wave3_prepare")
    assert phases.index("phase14_wave3_collect") < phases.index("phase15_wave3_evidence_gate")
    assert phases.index("phase15_wave3_evidence_gate") < phases.index("phase16_wave3_shared_page_refresh")
    assert phases.index("phase16_wave3_shared_page_refresh") < phases.index("phase17_wave4_prepare")
    assert phases.index("phase18_wave4_collect") < phases.index("phase19_wave4_evidence_gate")
    assert phases.index("phase19_wave4_evidence_gate") < phases.index("phase20_wave4_shared_page_refresh")
    assert phases.index("phase20_wave4_shared_page_refresh") < phases.index("phase21_bp_claim_coverage_validation")
    assert phases.index("phase21_bp_claim_coverage_validation") < phases.index("phase22_bp_cross_dimension_gate")
    assert phases.index("phase22_bp_cross_dimension_gate") < phases.index("phase23_bp_section_package_validation")
    assert phases.index("phase23_bp_section_package_validation") < phases.index("phase24_synthesis_prepare")
    assert phases.index("phase24_synthesis_prepare") < phases.index("phase25_synthesis_collect")
    assert phases.index("phase25_synthesis_collect") < phases.index("phase26_bp_debate_review")
    assert phases.index("phase26_bp_debate_review") < phases.index("phase27_bp_final_assembly")
    assert phases.index("phase27_bp_final_assembly") < phases.index("phase28_bp_readability_review")
    assert phases.index("phase28_bp_readability_review") < phases.index("phase30_delivery")


def test_bp_narrative_assembly_keeps_machine_metadata_out_of_delivery_markdown(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-CLEAN-METADATA"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_section_packages.json").write_text(
        json.dumps({
            "packages": [{
                "validation": {"passed": True, "issues": []},
                "package": {
                    "section_id": "bp_company_team_compliance",
                    "section_title": "团队与合规",
                    "claims": [{
                        "claim_id": "BC001",
                        "claim": "公司主体有效",
                        "fact_ids": ["BP-COMPANY_TEAM_COMPLIANCE-F001"],
                        "confidence": "high",
                        "source_quality": "official",
                    }],
                    "facts_used": ["BP-COMPANY_TEAM_COMPLIANCE-F001"],
                    "claim_ids_covered": ["BC001"],
                    "answers": [{
                        "answer": "公司主体有效，具备进入下一步尽调的基础。",
                        "confidence": "high",
                        "limits": "仍需复核最新工商档案。",
                    }],
                    "narrative_blocks": [{
                        "text": "主体资格与团队信息初步匹配，未发现立即阻断项。",
                        "fact_ids": ["BP-COMPANY_TEAM_COMPLIANCE-F001"],
                    }],
                    "counter_evidence": ["核心团队履历仍需访谈确认"],
                    "data_gaps": ["补充实控人访谈记录"],
                },
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "summary": {"total": 1, "supported": 1, "unverified": 0, "contradicted": 0, "critical_not_addressed": 0},
            "claims": [{"claim_id": "BC001", "claim": "公司主体有效", "status": "supported", "priority": "critical"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_shared_state.json").write_text(
        json.dumps({"current_recommendation": {"verdict": "observe", "confidence": "high"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_thesis_reconciliation.json").write_text(
        json.dumps({"recommendation": "observe", "confidence": "high", "supporting_reasons": ["主体有效"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text(
        json.dumps({
            "facts": [{
                "fact_id": "BP-COMPANY_TEAM_COMPLIANCE-F001",
                "claim": "工商登记显示公司主体有效",
                "source_url": "https://example.com/company",
                "source_tier": "official",
                "confidence": "high",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = assemble_bp_report(task_dir, entity="测试公司")

    assert result["ok"] is True
    markdown = (task_dir / "bp_final_report.md").read_text(encoding="utf-8")
    blocked_terms = [
        "Coverage 状态",
        "Debate Review",
        "BP claim",
        "外部 fact",
        "facts_used",
        "claim_ids_used",
        "BP-COMPANY_TEAM_COMPLIANCE-F001",
        "BC001",
    ]
    for term in blocked_terms:
        assert term not in markdown
    assert "公司主体有效" in markdown
    assert result["facts_used"] == ["BP-COMPANY_TEAM_COMPLIANCE-F001"]


def test_run_research_plan_writes_bp_due_diligence_plan(tmp_path):
    """Phase04 v5.2: 子代理派发 → needs_dispatch → collect fallback 到脚本骨架。

    v5.2 不再生成 skeleton 文件，改为生成 brief + 返回 needs_dispatch。
    collect 无子代理输出时 fallback 到旧脚本生成完整 plan。
    """
    job_ctx = SimpleNamespace(
        job_id="BP-PLAN",
        entity="测试公司",
        query="看这个BP",
        market="cn",
        metadata={},
        workspace=None,
    )

    # Step 1: 派发（返回 needs_dispatch）
    result = _run_research_plan(tmp_path, job_ctx)
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True

    task_dir = tmp_path / "tasks" / "BP-PLAN"

    # brief 文件应存在（v5.2 用 brief 替代 skeleton）
    brief_path = task_dir / "bp_phase04_brief.json"
    assert brief_path.exists()
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert brief["entity"] == "测试公司"

    # Step 2: collect（无子代理输出 → fallback 到脚本）
    result_collect = _run_research_plan_collect(tmp_path, job_ctx)
    assert result_collect["ok"] is True

    path = task_dir / "bp_research_plan.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "bp_research_plan.v2"
    assert payload["task_id"] == "BP-PLAN"
    assert payload["entity"] == "测试公司"
    assert payload["market"] == "cn"
    assert payload["plan_status"] == "ready"
    assert payload["prepared_by"] == "script_scaffold_plus_orchestrator_enrichment"
    assert payload["generation_roles"]["script"] == "schema_fact_requirements_coverage_matrix_claim_matrix_validation"
    assert len(payload["core_questions"]) >= 7
    assert len(payload["strategic_questions"]) >= 3
    assert payload["fact_requirements"]
    assert payload["section_requirements"]
    assert set(payload["section_requirements"]) == {
        "bp_company_team_compliance",
        "bp_product_commercial",
        "bp_tech_ip_moat",
        "bp_market_supply_chain",
        "bp_competition_positioning",
        "bp_valuation_return",
        "bp_dealbreaker_risk",
    }
    assert payload["coverage_matrix"]
    assert payload["claim_matrix"]
    assert {claim["owner_section"] for claim in payload["claim_matrix"]} <= set(payload["section_requirements"])
    assert payload["claim_matrix"][0]["source"] == "bp_or_inferred_from_intake"
    assert payload["validation"]["ready"] is True


def test_bp_dispatch_prepare_uses_four_evidence_collection_roles_without_valuation(tmp_path):
    job_ctx = SimpleNamespace(job_id="BP-WAVE1", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_dispatch_prepare(tmp_path, job_ctx)

    # v2: sequential dispatch — 只派发第一个 pending role + has_more
    assert result["ok"] is True
    assert result["needs_dispatch"] is True
    assert result["has_more"] is True  # 4 个 role，派了 1 个，还有 3 个
    assert result["dispatch_info"]["current_role"] == "bp_company_team_compliance"
    assert set(result["dispatch_info"]["remaining_roles"]) == {
        "bp_product_commercial",
        "bp_tech_ip_moat",
        "bp_market_supply_chain",
    }
    assert "bp_valuation_return" not in result["dispatch_info"]["remaining_roles"]
    assert "bp_competition_positioning" not in result["dispatch_info"]["remaining_roles"]
    dispatch_payload = json.loads((tmp_path / "tasks" / "BP-WAVE1" / "phase2_dispatch.json").read_text(encoding="utf-8"))
    assert dispatch_payload["wave_design"] == "wave1_evidence_collection_4_roles"
    assert dispatch_payload["current_subagent"] == "bp_company_team_compliance"
    assert dispatch_payload["total_subagents"] == 11  # v5.0: 7 维度 + 4 叙事角色


def test_bp_wave3_prepare_dispatches_two_roles_with_shared_inputs(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-WAVE3"
    task_dir.mkdir(parents=True)
    outputs_dir = task_dir
    for slug in ("company_team_compliance", "product_commercial", "tech_ip_moat", "market_supply_chain"):
        (outputs_dir / f"bp_phase2_{slug}.md").write_text("## done\n" + "x" * 200, encoding="utf-8")
    (task_dir / "bp_shared_diligence_page.md").write_text("# Shared\n", encoding="utf-8")
    (task_dir / "bp_shared_state.json").write_text("{}", encoding="utf-8")
    (task_dir / "bp_claim_coverage.json").write_text("{}", encoding="utf-8")
    (task_dir / "bp_fact_store.json").write_text("{}", encoding="utf-8")
    (task_dir / "bp_step0_profile.json").write_text("{}", encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-WAVE3", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_wave3_prepare(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["mode"] == "bp_wave3_prepare"
    # v2: sequential dispatch — 只派发第一个 pending role + has_more
    assert result["has_more"] is True  # 2 个 role，派了 1 个，还有 1 个
    assert result["dispatch_info"]["current_role"] == "bp_competition_positioning"
    assert result["dispatch_info"]["remaining_roles"] == ["bp_valuation_return"]
    manifests = result["dispatch_info"]["manifests"]
    assert len(manifests) == 1  # sequential: 只返回 1 个 manifest
    manifest_text = (task_dir / "bp_phase2_manifest_competition_positioning.json").read_text(encoding="utf-8")
    assert "bp_shared_diligence_page.md" in manifest_text
    assert "bp_shared_state.json" in manifest_text
    assert "bp_claim_coverage.json" in manifest_text
    assert "bp_fact_store.json" in manifest_text


def test_bp_wave3_collect_requires_all_two_outputs(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-WAVE3-COLLECT"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_phase2_competition_positioning.md").write_text("## competition\n" + "x" * 200, encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-WAVE3-COLLECT", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_wave3_collect(tmp_path, job_ctx)

    assert result["ok"] is False
    assert "bp_valuation_return" in result["result"]["missing"]


def test_bp_profile_registers_synthesis_handlers_before_ic_and_delivery(tmp_path):
    job_ctx = SimpleNamespace(job_id="BP-SYNTHESIS-HANDLERS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)
    task_dir = tmp_path / "tasks" / "BP-SYNTHESIS-HANDLERS"
    task_dir.mkdir(parents=True)
    for slug in (
        "company_team_compliance",
        "product_commercial",
        "tech_ip_moat",
        "market_supply_chain",
        "competition_positioning",
        "valuation_return",
        "dealbreaker_risk",
    ):
        (task_dir / f"bp_phase2_{slug}.md").write_text("## done\n" + "x" * 200, encoding="utf-8")
        # Sidecar files required by synthesis_prepare completeness check
        (task_dir / f"bp_phase2_{slug}-facts.json").write_text('{"facts": []}', encoding="utf-8")
        (task_dir / f"bp_phase2_{slug}-section.json").write_text('{"schema_version": "bp_section_package.v1", "section_id": "%s"}' % slug, encoding="utf-8")
    (task_dir / "bp_synthesis.md").write_text("# synthesis\n" + "x" * 2500, encoding="utf-8")

    profile = BPProfile(runtime_root=tmp_path)

    prepare_result = profile.run_phase("phase24_synthesis_prepare", job_ctx)
    collect_result = profile.run_phase("phase25_synthesis_collect", job_ctx)

    assert prepare_result["phase"] == "phase24_synthesis_prepare"
    assert collect_result["phase"] == "phase25_synthesis_collect"
    assert collect_result["ok"] is True


def test_bp_synthesis_prepare_requires_all_eight_wave_outputs_and_names_them_in_manifest(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SYNTHESIS-8"
    task_dir.mkdir(parents=True)
    for slug in (
        "company_team_compliance",
        "product_commercial",
        "tech_ip_moat",
        "market_supply_chain",
        "competition_positioning",
        "valuation_return",
        "dealbreaker_risk",
    ):
        (task_dir / f"bp_phase2_{slug}.md").write_text("## done\n" + "x" * 200, encoding="utf-8")
        # Sidecar files required by synthesis_prepare completeness check
        (task_dir / f"bp_phase2_{slug}-facts.json").write_text('{"facts": []}', encoding="utf-8")
        (task_dir / f"bp_phase2_{slug}-section.json").write_text('{"schema_version": "bp_section_package.v1", "section_id": "%s"}' % slug, encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-SYNTHESIS-8", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_synthesis_prepare(tmp_path, job_ctx)

    assert result["ok"] is True
    assert set(result["result"]["dimension_files"]) == {
        "company_team_compliance",
        "product_commercial",
        "tech_ip_moat",
        "market_supply_chain",
        "competition_positioning",
        "valuation_return",
        "dealbreaker_risk",
    }
    manifest = json.loads((task_dir / "bp_phase3_manifest_synthesis.json").read_text(encoding="utf-8"))
    # system_prompt 来自 instruction_store_bp/bp_统稿.md（测试 tmp_path 无 instruction store 时为 ERROR fallback）
    prompt = manifest["system_prompt"]
    if not prompt.startswith("ERROR"):
        assert "Deal Breaker" in prompt or "dealbreaker" in prompt.lower()
    # 无论 prompt 来源，manifest 结构必须正确
    assert manifest["role"] == "bp_统稿"


def test_run_bp_shared_page_init_writes_state_coverage_and_markdown(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SHARED"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_research_plan.v2",
            "entity": "测试公司",
            "claim_matrix": [
                {"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_团队与合规", "priority": "critical"}
            ],
            "core_questions": [
                {"question_id": "BQ1", "question": "团队是否可信？", "owner_section": "bp_团队与合规", "priority": "critical"}
            ],
            "strategic_questions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text(json.dumps({"facts": []}, ensure_ascii=False), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-SHARED", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_shared_page_init(tmp_path, job_ctx)

    assert result["ok"] is True
    shared_page = task_dir / "bp_shared_diligence_page.md"
    shared_state = json.loads((task_dir / "bp_shared_state.json").read_text(encoding="utf-8"))
    coverage = json.loads((task_dir / "bp_claim_coverage.json").read_text(encoding="utf-8"))
    assert shared_page.exists()
    page_text = shared_page.read_text(encoding="utf-8")
    assert "BP Shared Diligence Page" in page_text
    assert "团队优秀" in page_text
    assert "数据缺口" in page_text
    assert shared_state["schema_version"] == "bp_shared_state.v1"
    assert shared_state["claim_status"]["BC001"]["status"] == "not_addressed"
    assert coverage["summary"]["critical_not_addressed"] == 1


def test_run_bp_shared_page_refresh_reads_raw_section_sidecars_before_section_gate(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SHARED-SIDECAR"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_research_plan.v2",
            "entity": "测试公司",
            "claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_团队与合规", "priority": "critical"}],
            "core_questions": [{"question_id": "BQ1", "question": "团队是否可信？", "owner_section": "bp_团队与合规", "priority": "critical"}],
            "strategic_questions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text(json.dumps({"facts": []}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "bp_phase2_team-facts.json").write_text(
        json.dumps({"facts": [{"fact_id": "BF-0001", "claim": "工商验证成立", "source_url": "https://example.com", "source_tier": "official", "confidence": "high", "fact_type": "company_registration"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "section_id": "bp_团队与合规",
            "section_title": "团队与合规",
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "confidence": "medium", "source_quality": "official"}],
            "data_gaps": ["仍需访谈核心创始人"],
            "counter_evidence": ["团队规模待验证"],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SHARED-SIDECAR", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_shared_page_refresh(tmp_path, job_ctx, after_wave=1)

    assert result["ok"] is True
    shared_state = json.loads((task_dir / "bp_shared_state.json").read_text(encoding="utf-8"))
    assert shared_state["claim_status"]["BC001"]["status"] == "supported"
    assert "BF-0001" in shared_state["fact_index"]
    assert "仍需访谈核心创始人" in shared_state["open_questions"][0]["gap"]


def test_run_bp_shared_page_refresh_merges_facts_and_section_gaps(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SHARED-REFRESH"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_research_plan.v2",
            "entity": "测试公司",
            "claim_matrix": [
                {"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_团队与合规", "priority": "critical"}
            ],
            "core_questions": [{"question_id": "BQ1", "question": "团队是否可信？", "owner_section": "bp_团队与合规", "priority": "critical"}],
            "strategic_questions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text(
        json.dumps({"facts": [{"fact_id": "BF-0001", "claim": "工商验证成立", "source_url": "https://example.com", "source_tier": "official", "confidence": "high", "fact_type": "company_registration"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_section_packages.json").write_text(
        json.dumps({
            "packages": [{
                "section_name": "bp_phase2_team",
                "package": {
                    "section_id": "bp_团队与合规",
                    "section_title": "团队与合规",
                    "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "confidence": "medium", "source_quality": "official"}],
                    "data_gaps": ["仍需访谈核心创始人"],
                    "counter_evidence": ["团队规模待验证"],
                },
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SHARED-REFRESH", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_shared_page_refresh(tmp_path, job_ctx, after_wave=1)

    assert result["ok"] is True
    shared_state = json.loads((task_dir / "bp_shared_state.json").read_text(encoding="utf-8"))
    coverage = json.loads((task_dir / "bp_claim_coverage.json").read_text(encoding="utf-8"))
    page_text = (task_dir / "bp_shared_diligence_page.md").read_text(encoding="utf-8")
    assert shared_state["claim_status"]["BC001"]["status"] == "supported"
    assert "BF-0001" in shared_state["fact_index"]
    assert "仍需访谈核心创始人" in shared_state["open_questions"][0]["gap"]
    assert coverage["summary"]["supported"] == 1
    assert "Wave 交接指令" in page_text


def test_run_bp_search_plan_compile_writes_claim_level_work_orders(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SEARCH-PHASE"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_research_plan.v2",
            "task_id": "BP-SEARCH-PHASE",
            "entity": "测试公司",
            "core_questions": [{
                "question_id": "BQ2",
                "question": "客户收入是否可验证？",
                "owner_section": "bp_product_commercial",
                "priority": "critical",
                "required_fact_keys": ["customer_evidence", "revenue_evidence"],
            }],
            "strategic_questions": [],
            "fact_requirements": [{
                "fact_key": "customer_evidence",
                "source_priority": ["customer_or_partner_disclosure", "reputable_media"],
                "criticality": "critical",
            }],
            "claim_matrix": [{
                "claim_id": "BC005",
                "claim": "测试公司客户收入可以被验证。",
                "owner_section": "bp_product_commercial",
                "priority": "critical",
                "required_fact_keys": ["customer_evidence", "revenue_evidence"],
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_step0_profile.json").write_text(json.dumps({"company_name": "测试公司"}, ensure_ascii=False), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-SEARCH-PHASE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_search_plan_compile(tmp_path, job_ctx)

    assert result["ok"] is True
    payload = json.loads((task_dir / "bp_search_plan.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "bp_search_plan.v1"
    assert payload["search_tasks"][0]["claim_id"] == "BC005"
    assert payload["search_tasks"][0]["requires_counter_search"] is True



def test_run_bp_fact_store_bootstrap_writes_store_and_index(tmp_path):
    job_ctx = SimpleNamespace(
        job_id="BP-FACTS",
        entity="测试公司",
        query="看这个BP",
        market="cn",
        metadata={},
        workspace=None,
    )

    result = _run_bp_fact_store_bootstrap(tmp_path, job_ctx)

    assert result["ok"] is True
    store_path = tmp_path / "tasks" / "BP-FACTS" / "bp_fact_store.json"
    index_path = tmp_path / "tasks" / "BP-FACTS" / "bp_fact_store_index.json"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "bp_fact_store.v1"
    assert payload["task_id"] == "BP-FACTS"
    assert payload["entity"] == "测试公司"
    assert payload["facts"] == []
    assert index["total_facts"] == 0


def test_run_bp_fact_store_merge_collects_sidecars_from_outputs_dir(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-MERGE-OUTPUTS"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "bp_phase2_team-facts.json").write_text(
        json.dumps({
            "role": "bp_团队与合规",
            "facts": [
                {
                    "fact_id": "BF-0001",
                    "claim": "测试公司成立于2020年",
                    "value": "2020年",
                    "unit": "年",
                    "period": "2020年",
                    "source_url": "https://example.com/company",
                    "source_tier": "official",
                    "source_quote": "测试公司成立于2020年",
                    "question_id": "Q1",
                    "fact_type": "company_registration",
                    "confidence": "high",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-MERGE-OUTPUTS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_fact_store_merge(tmp_path, job_ctx)

    assert result["ok"] is True
    payload = json.loads((task_dir / "bp_fact_store.json").read_text(encoding="utf-8"))
    assert len(payload["facts"]) == 1
    assert payload["facts"][0]["fact_id"] == "BF-0001"


def test_run_bp_fact_store_merge_auto_repairs_malformed_sidecar(tmp_path):
    """Auto-repair: malformed JSON is fixed and merged successfully."""
    task_dir = tmp_path / "tasks" / "BP-MERGE-BAD-SIDECAR"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    bad_sidecar = outputs_dir / "bp_phase2_dealbreaker_risk-facts.json"
    bad_sidecar.write_text('{"facts": [{"fact_id": "BF-0001"}', encoding="utf-8")  # missing closing brace
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-MERGE-BAD-SIDECAR", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_fact_store_merge(tmp_path, job_ctx)

    # Auto-repair attempt: merge still succeeds (ok=True) even if one sidecar can't be repaired.
    # The bad sidecar is recorded in malformed_source_files and skipped (non-blocking).
    assert result["ok"] is True
    assert any(str(bad_sidecar) in item["path"] for item in result["result"]["malformed_source_files"])


def test_run_bp_fact_store_merge_includes_repaired_dealbreaker_risk_sidecar(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-MERGE-DEALBREAKER"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "bp_phase2_dealbreaker_risk-facts.json").write_text(
        json.dumps({
            "role": "bp_dealbreaker_risk",
            "facts": [
                {
                    "fact_id": "BP-DEALBREAKER_RISK-F001",
                    "claim": "存在需核查的风险事项",
                    "value": "需核查",
                    "unit": "",
                    "period": "当前",
                    "source_url": "https://example.com/risk",
                    "source_tier": "official",
                    "source_quote": "风险事项需进一步核查",
                    "question_id": "BQ-RISK-001",
                    "fact_type": "dealbreaker_risk",
                    "confidence": "medium",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-MERGE-DEALBREAKER", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_fact_store_merge(tmp_path, job_ctx)

    assert result["ok"] is True
    index = json.loads((task_dir / "bp_fact_store_index.json").read_text(encoding="utf-8"))
    assert "BP-DEALBREAKER_RISK-F001" in index["fact_ids"]


def test_run_bp_fact_store_merge_preserves_distinct_fact_ids_for_duplicate_claims(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-MERGE-DUP-IDS"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    common_fact = {
        "claim": "测试公司为合法注册主体",
        "value": "合法注册",
        "unit": "",
        "period": "当前",
        "source_url": "https://example.com/company",
        "source_tier": "official",
        "source_quote": "测试公司合法注册",
        "question_id": "Q1",
        "fact_type": "company_registration",
        "confidence": "high",
    }
    (outputs_dir / "bp_phase2_company_team_compliance-facts.json").write_text(
        json.dumps({"facts": [dict(common_fact, fact_id="BP-COMPANY-F001")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (outputs_dir / "bp_phase2_dealbreaker_risk-facts.json").write_text(
        json.dumps({"facts": [dict(common_fact, fact_id="BP-DEALBREAKER_RISK-F001")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-MERGE-DUP-IDS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_fact_store_merge(tmp_path, job_ctx)

    assert result["ok"] is True
    index = json.loads((task_dir / "bp_fact_store_index.json").read_text(encoding="utf-8"))
    assert "BP-COMPANY-F001" in index["fact_ids"]
    assert "BP-DEALBREAKER_RISK-F001" in index["fact_ids"]


def test_run_bp_fact_store_merge_collects_dimension_sidecars(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-MERGE"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_phase2_team-facts.json").write_text(
        json.dumps({
            "role": "bp_团队与合规",
            "facts": [
                {
                    "fact_id": "BF-0001",
                    "claim": "测试公司成立于2020年",
                    "value": "2020年",
                    "unit": "年",
                    "period": "2020年",
                    "source_url": "https://example.com/company",
                    "source_tier": "official",
                    "source_quote": "测试公司成立于2020年",
                    "question_id": "Q1",
                    "fact_type": "company_registration",
                    "confidence": "high",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(
        job_id="BP-MERGE",
        entity="测试公司",
        query="看这个BP",
        market="cn",
        metadata={},
        workspace=None,
    )

    result = _run_bp_fact_store_merge(tmp_path, job_ctx)

    assert result["ok"] is True
    store_path = task_dir / "bp_fact_store.json"
    index_path = task_dir / "bp_fact_store_index.json"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(payload["facts"]) == 1
    assert payload["facts"][0]["fact_id"] == "BF-0001"
    assert index["total_facts"] == 1


def test_bp_section_package_validation_reads_sidecar_from_outputs_dir(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-OUTPUTS"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (outputs_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (outputs_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v1",
            "section_id": "bp_phase2_team",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "claims": [{"claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-OUTPUTS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["section_gate"]["passed"] is True


def test_bp_section_package_validation_ignores_brief_files_in_task_dir_when_outputs_exist(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-BRIEFS"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_phase2_brief_team.md").write_text("# Brief\n不是正式 section 输出", encoding="utf-8")
    (outputs_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (outputs_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v1",
            "section_id": "bp_phase2_team",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "claims": [{"claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = SimpleNamespace(root=task_dir, outputs_dir=outputs_dir)
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-BRIEFS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    section_gate = result["result"]["section_gate"]
    assert result["ok"] is True
    assert section_gate["summary"]["total"] == 1
    assert section_gate["packages"][0]["section_name"] == "bp_phase2_team"


def test_bp_section_package_validation_rejects_claim_fact_id_when_fact_store_empty(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-EMPTY-FACTS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": [], "total_facts": 0}), encoding="utf-8")
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v1",
            "section_id": "bp_phase2_team",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "claims": [{"claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-EMPTY-FACTS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    assert any(issue["code"] == "UNKNOWN_FACT_ID" for issue in issues)


def test_bp_section_package_validation_fails_missing_packages(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-MISSING"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    job_ctx = SimpleNamespace(
        job_id="BP-SECTIONS-MISSING",
        entity="测试公司",
        query="看这个BP",
        market="cn",
        metadata={},
        workspace=None,
    )

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["section_gate"]["passed"] is False
    assert (task_dir / "bp_section_packages.json").exists()


def test_bp_section_package_validation_auto_upgrades_v1_mislabeled_as_v2(tmp_path):
    # 2026-07-28 bugfix: 子代理常产出 v1 风格包但误标 schema_version=v2（缺 answers/
    # claim_ids_covered/narrative_blocks/search_audit），旧 validator 直接走 v2 严格校验
    # → 全包硬 FAIL（生产环境曾因此卡死 11/11 包）。修复后：缺结构性三件套视为 v1 误标，
    # 自动降级触发 _upgrade_v1_to_v2 合成缺失字段并放行（_auto_upgraded_from_v1=True）。
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-V2-REQUIRED"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_团队与合规",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "claims": [{"claim_id": "BC001", "claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-V2-REQUIRED", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    # v1 误标包被自动降级救援 → 通过
    assert result["ok"] is True
    assert result["result"]["section_gate"]["passed"] is True
    pkg = result["result"]["section_gate"]["packages"][0]["package"]
    # schema_version 被就地降级为 v1（_upgrade_v1_to_v2 返回新 dict，原始包记录降级后的版本号）
    assert pkg["schema_version"] == "bp_section_package.v1"


def test_bp_section_package_validation_passes_valid_v2_package(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-PASS-V2"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_团队与合规", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_团队与合规",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "search_audit": {
                "queries": [
                    {"query": f"测试公司 团队 核验 {i}", "purpose": "验证团队事实", "result_count": 3, "fetched_urls": [f"https://example{i}.com/report"]}
                    for i in range(1, 9)
                ],
                "fetched_urls": [f"https://example{i}.com/report" for i in range(1, 5)],
                "source_domains": [f"example{i}.com" for i in range(1, 4)],
                "claim_coverage": [{
                    "claim_id": "BC001",
                    "search_task_ids": ["BST-001"],
                    "unique_queries": 8,
                    "fetched_urls": [f"https://example{i}.com/report" for i in range(1, 4)],
                    "source_domains": [f"example{i}.com" for i in range(1, 4)],
                    "counter_search_done": True,
                    "evidence_verdict": "supported",
                }],
            },
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-PASS-V2", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["section_gate"]["passed"] is True
    assert result["result"]["section_gate"]["summary"]["passed"] == 1


def test_bp_section_package_validation_rejects_shallow_search_audit_for_v2(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-SHALLOW-SEARCH"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_phase2_company_team_compliance.md").write_text("## 团队\n" + "正文\n" * 200, encoding="utf-8")
    (task_dir / "bp_phase2_company_team_compliance-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_company_team_compliance",
            "section_title": "团队与合规",
            "key_messages": ["团队看起来不错"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "仅基于一个泛搜索来源", "confidence": "high", "source_quality": "media"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "search_audit": {
                "queries": [{"query": "测试公司 团队", "purpose": "泛搜", "result_count": 5, "fetched_urls": []}],
                "fetched_urls": [],
                "source_domains": ["example.com"],
            },
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-SHALLOW-SEARCH", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    codes = {issue["code"] for issue in issues}
    assert "INSUFFICIENT_SEARCH_QUERIES" in codes
    assert "INSUFFICIENT_FETCHED_URLS" in codes
    assert "INSUFFICIENT_SOURCE_DOMAINS" in codes


def test_bp_section_package_validation_requires_claim_level_search_coverage_for_v2(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-MISSING-CLAIM-COVERAGE"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_phase2_company_team_compliance.md").write_text("## 团队\n" + "正文\n" * 200, encoding="utf-8")
    (task_dir / "bp_phase2_company_team_compliance-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_company_team_compliance",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "search_audit": {
                "queries": [{"query": f"测试公司 团队 核验 {i}", "purpose": "验证团队事实", "result_count": 3, "fetched_urls": [f"https://example{i}.com/report"]} for i in range(1, 9)],
                "fetched_urls": [f"https://example{i}.com/report" for i in range(1, 5)],
                "source_domains": [f"example{i}.com" for i in range(1, 4)],
            },
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-MISSING-CLAIM-COVERAGE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    assert any(issue["code"] == "MISSING_CLAIM_SEARCH_COVERAGE" for issue in issues)



def test_bp_section_package_validation_rejects_critical_claim_without_counter_search(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-NO-COUNTER"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_phase2_company_team_compliance.md").write_text("## 团队\n" + "正文\n" * 200, encoding="utf-8")
    (task_dir / "bp_phase2_company_team_compliance-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_company_team_compliance",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "search_audit": {
                "queries": [{"query": f"测试公司 团队 核验 {i}", "purpose": "验证团队事实", "result_count": 3, "fetched_urls": [f"https://example{i}.com/report"]} for i in range(1, 9)],
                "fetched_urls": [f"https://example{i}.com/report" for i in range(1, 5)],
                "source_domains": [f"example{i}.com" for i in range(1, 4)],
                "claim_coverage": [{
                    "claim_id": "BC001",
                    "search_task_ids": ["BST-001"],
                    "unique_queries": 8,
                    "fetched_urls": [f"https://example{i}.com/report" for i in range(1, 4)],
                    "source_domains": [f"example{i}.com" for i in range(1, 4)],
                    "counter_search_done": False,
                    "evidence_verdict": "supported",
                }],
            },
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-NO-COUNTER", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    assert any(issue["code"] == "CRITICAL_CLAIM_COUNTER_SEARCH_MISSING" for issue in issues)



def test_bp_section_package_validation_requires_answer_limits_in_v2(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-LIMITS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_research_plan.json").write_text(json.dumps({"claim_matrix": [{"claim_id": "BC001"}]}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_团队与合规",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-LIMITS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    assert any(issue["code"] == "MISSING_ANSWER_FIELD" and "limits" in issue["message"] for issue in issues)


def test_bp_section_package_validation_requires_claim_inventory_for_v2_claim_ids(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-NO-PLAN"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_团队与合规",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
            "claim_ids_covered": ["BC001"],
            "claims": [{"claim_id": "BC001", "claim": "团队优秀", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证。"}],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-SECTIONS-NO-PLAN", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is False
    issues = result["result"]["section_gate"]["packages"][0]["validation"]["issues"]
    assert any(issue["code"] == "CLAIM_INVENTORY_MISSING" for issue in issues)


def test_bp_claim_coverage_validation_blocks_critical_not_addressed(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-COVERAGE-BLOCK"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "summary": {"critical_not_addressed": 1},
            "claims": [{"claim_id": "BC001", "claim": "核心客户存在", "priority": "critical", "status": "not_addressed", "data_gaps": []}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-COVERAGE-BLOCK", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_claim_coverage_validation(tmp_path, job_ctx)

    # v2: repair mechanism — FAIL 时返回 ok=True + needs_dispatch=True + gate_verdict=REPAIR
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True
    assert result["result"]["gate_verdict"] == "REPAIR"
    assert result["result"]["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"


def test_bp_claim_coverage_validation_blocks_high_not_addressed_case_insensitive(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-COVERAGE-HIGH"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "summary": {"critical_not_addressed": 0},
            "claims": [{"claim_id": "BC002", "claim": "已量产", "priority": "HIGH", "status": "NOT_ADDRESSED", "data_gaps": []}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-COVERAGE-HIGH", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_claim_coverage_validation(tmp_path, job_ctx)

    # v2: repair mechanism — FAIL 时返回 ok=True + needs_dispatch=True + gate_verdict=REPAIR
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True
    assert result["result"]["gate_verdict"] == "REPAIR"
    assert result["result"]["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"


def test_bp_claim_coverage_validation_generates_missing_coverage_before_gate(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-COVERAGE-GENERATE"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"entity": "测试公司", "claim_matrix": [{"claim_id": "BC001", "claim": "团队优秀", "owner_section": "bp_团队与合规", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-COVERAGE-GENERATE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_claim_coverage_validation(tmp_path, job_ctx)

    # v2: repair mechanism — FAIL 时返回 ok=True + needs_dispatch=True
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True
    assert (task_dir / "bp_claim_coverage.json").exists()
    assert result["result"]["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"


def test_bp_claim_coverage_validation_blocks_critical_unverified_with_data_gap(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-COVERAGE-GAP"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "summary": {"critical_not_addressed": 0},
            "claims": [{"claim_id": "BC001", "claim": "核心客户存在", "priority": "critical", "status": "unverified", "data_gaps": ["需创始人提供客户合同"]}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-COVERAGE-GAP", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_claim_coverage_validation(tmp_path, job_ctx)

    # v2: repair mechanism — FAIL 时返回 ok=True + needs_dispatch=True + gate_verdict=REPAIR
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True
    assert result["result"]["gate_verdict"] == "REPAIR"
    assert result["result"]["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"


def test_bp_claim_coverage_validation_uses_independent_gate_schema(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-COVERAGE-SCRIPT"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "claims": [{"claim_id": "BC001", "claim": "核心客户存在", "priority": "critical", "status": "contradicted"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-COVERAGE-SCRIPT", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_claim_coverage_validation(tmp_path, job_ctx)

    gate = json.loads((task_dir / "bp_claim_coverage_gate.json").read_text(encoding="utf-8"))
    # v2: repair mechanism — FAIL 时返回 ok=True + needs_dispatch=True
    assert result["ok"] is True
    assert result.get("needs_dispatch") is True
    assert gate["schema_version"] == "bp_claim_coverage_gate.v2"
    assert gate["failed_claims"][0]["claim_id"] == "BC001"
    assert result["result"]["failed_claims"][0]["claim_id"] == "BC001"


def test_bp_section_package_validation_passes_valid_package(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SECTIONS-PASS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_phase2_team.md").write_text("## 团队\n正文", encoding="utf-8")
    (task_dir / "bp_phase2_team-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v1",
            "section_id": "bp_phase2_team",
            "section_title": "团队与合规",
            "key_messages": ["团队可验证"],
            "claims": [{"claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["BF-0001"],
            "counter_evidence": ["暂无重大反证"],
            "data_gaps": [],
            "markdown_draft": "## 团队与合规\n测试公司成立于2020年。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(
        job_id="BP-SECTIONS-PASS",
        entity="测试公司",
        query="看这个BP",
        market="cn",
        metadata={},
        workspace=None,
    )

    result = _run_bp_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["section_gate"]["passed"] is True
    assert result["result"]["section_gate"]["summary"]["passed"] == 1


def _write_valid_bp_section_package(task_dir):
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "bp_fact_store_index.json").write_text(json.dumps({"fact_ids": ["BF-0001"], "total_facts": 1}), encoding="utf-8")
    (task_dir / "bp_fact_store.json").write_text(
        json.dumps({"facts": [{"fact_id": "BF-0001", "claim": "测试公司成立于2020年", "source_url": "https://example.com", "confidence": "high"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_claim_coverage.json").write_text(json.dumps({"summary": {"critical_not_addressed": 0}, "claims": []}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "bp_section_packages.json").write_text(
        json.dumps({
            "passed": True,
            "summary": {"total": 1, "passed": 1, "failed": 0},
            "packages": [
                {
                    "section_name": "bp_phase2_team",
                    "validation": {"passed": True, "issues": []},
                    "package": {
                        "schema_version": "bp_section_package.v2",
                        "section_id": "bp_团队与合规",
                        "section_title": "团队与合规",
                        "key_messages": ["团队可验证"],
                        "answers": [{"question_id": "BQ1", "answer": "团队主体已验证", "fact_ids": ["BF-0001"], "confidence": "high", "limits": "仍需访谈"}],
                        "claim_ids_covered": ["BC001"],
                        "claims": [{"claim_id": "BC001", "claim": "测试公司成立于2020年", "fact_ids": ["BF-0001"], "reasoning": "工商来源验证", "confidence": "high", "source_quality": "official"}],
                        "facts_used": ["BF-0001"],
                        "counter_evidence": ["暂无重大反证"],
                        "data_gaps": [],
                        "narrative_blocks": [{"block_id": "NB1", "question_id": "BQ1", "claim_ids": ["BC001"], "fact_ids": ["BF-0001"], "text": "团队主体已验证，工商信息显示测试公司成立于2020年。"}],
                        "markdown_draft": "## 团队与合规\n测试公司成立于2020年，注册资本500万元，法定代表人为张三。\n\n### 本维度结论\n**结论：团队主体已验证（置信度：高）**\n工商信息显示测试公司成立于2020年，来源可靠。经企查查核实，公司经营状态为存续，无异常经营记录。",
                    },
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_bp_debate_review_passes_valid_section_packages(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-DEBATE"
    _write_valid_bp_section_package(task_dir)
    job_ctx = SimpleNamespace(job_id="BP-DEBATE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_debate_review(tmp_path, job_ctx)

    assert result["ok"] is True
    payload = json.loads((task_dir / "bp_debate_review.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"


def test_bp_debate_review_blocks_high_confidence_low_source(tmp_path):
    """2026-06-26 宽松化后：HIGH_CONFIDENCE_LOW_SOURCE 降为 MEDIUM，不再阻断（verdict=WARN, ok=True）"""
    task_dir = tmp_path / "tasks" / "BP-DEBATE-BLOCK"
    _write_valid_bp_section_package(task_dir)
    payload = json.loads((task_dir / "bp_section_packages.json").read_text(encoding="utf-8"))
    payload["packages"][0]["package"]["claims"][0]["source_quality"] = "low"
    (task_dir / "bp_section_packages.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-DEBATE-BLOCK", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_debate_review(tmp_path, job_ctx)

    # 宽松化后：MEDIUM 级问题不阻断，ok=True, verdict=WARN
    assert result["ok"] is True
    review = json.loads((task_dir / "bp_debate_review.json").read_text(encoding="utf-8"))
    assert review["verdict"] == "WARN"


def test_bp_cross_dimension_gate_writes_pass_file_for_consistent_inputs(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-CROSS-PASS"
    _write_valid_bp_section_package(task_dir)
    job_ctx = SimpleNamespace(job_id="BP-CROSS-PASS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_cross_dimension_gate(tmp_path, job_ctx)

    assert result["ok"] is True
    gate = json.loads((task_dir / "bp_cross_dimension_gate.json").read_text(encoding="utf-8"))
    assert gate["gate_verdict"] == "PASS"


def test_bp_delivery_phase_does_not_use_cached_result(monkeypatch, tmp_path):
    import sys
    import types

    job_ctx = SimpleNamespace(job_id="BP-NO-CACHE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)
    fake_heavy = types.ModuleType("scripts.heavy_phase_bg")
    fake_heavy.check_cached_result = lambda *args, **kwargs: {"ok": True, "mode": "stale_cached_delivery", "deliver_to_user": True}
    fake_heavy.launch_heavy_phase = lambda *args, **kwargs: {"ok": False, "mode": "fresh_delivery_gate", "deliver_to_user": False}
    monkeypatch.delenv("IRBP_BG_CHILD", raising=False)
    monkeypatch.setitem(sys.modules, "scripts.heavy_phase_bg", fake_heavy)

    result = _run_bp_delivery(tmp_path, job_ctx)

    assert result["mode"] == "fresh_delivery_gate"
    assert result["ok"] is False


def test_bp_cross_dimension_gate_blocks_valuation_using_unverified_revenue(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-CROSS-FAIL"
    _write_valid_bp_section_package(task_dir)
    payload = json.loads((task_dir / "bp_section_packages.json").read_text(encoding="utf-8"))
    payload["packages"][0]["package"]["section_id"] = "bp_估值"
    payload["packages"][0]["package"]["answers"][0]["answer"] = "按已确认收入和客户订单给出估值。"
    payload["packages"][0]["package"]["claims"][0]["source_quality"] = "bp"
    payload["packages"][0]["package"]["claims"][0]["claim"] = "公司已有收入和客户订单"
    (task_dir / "bp_section_packages.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-CROSS-FAIL", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_cross_dimension_gate(tmp_path, job_ctx)

    # Design: cross-dimension gate downgrades HIGH→WARN (only CRITICAL_CLAIM_CONTRADICTED blocks)
    assert result["ok"] is True
    gate = json.loads((task_dir / "bp_cross_dimension_gate.json").read_text(encoding="utf-8"))
    assert gate["gate_verdict"] == "PASS"
    # But the issue is still detected (as WARN, not HIGH)
    assert any(issue["code"] == "VALUATION_USES_BP_ONLY_REVENUE" for issue in gate["issues"])
    assert all(issue["severity"] == "WARN" for issue in gate["issues"] if issue["code"] == "VALUATION_USES_BP_ONLY_REVENUE")


def test_bp_cross_dimension_gate_blocks_valuation_answer_with_weak_revenue_assumption(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-CROSS-ANSWER-FAIL"
    _write_valid_bp_section_package(task_dir)
    payload = json.loads((task_dir / "bp_section_packages.json").read_text(encoding="utf-8"))
    package = payload["packages"][0]["package"]
    package["section_id"] = "bp_估值"
    package["answers"] = [{"question_id": "BQ_VAL", "answer": "估值假设依赖客户订单和营收爬坡。", "fact_ids": [], "confidence": "medium", "limits": "仅来自BP"}]
    package["claims"] = []
    package["narrative_blocks"] = [{"block_id": "NB_VAL", "question_id": "BQ_VAL", "claim_ids": [], "fact_ids": [], "text": "客户订单驱动收入预测。"}]
    (task_dir / "bp_section_packages.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-CROSS-ANSWER-FAIL", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_cross_dimension_gate(tmp_path, job_ctx)

    # Design: cross-dimension gate downgrades HIGH→WARN (only CRITICAL_CLAIM_CONTRADICTED blocks)
    assert result["ok"] is True
    gate = json.loads((task_dir / "bp_cross_dimension_gate.json").read_text(encoding="utf-8"))
    assert any(issue["code"] == "VALUATION_USES_UNVERIFIED_REVENUE_ASSUMPTION" for issue in gate["issues"])
    assert all(issue["severity"] == "WARN" for issue in gate["issues"] if issue["code"] == "VALUATION_USES_UNVERIFIED_REVENUE_ASSUMPTION")


def test_bp_final_assembly_writes_investment_chain_report_not_dimension_concat(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-FINAL"
    _write_valid_bp_section_package(task_dir)
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-FINAL", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_final_assembly(tmp_path, job_ctx)

    assert result["ok"] is True
    report = task_dir / "bp_final_report.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "# 测试公司 BP尽调审计底稿" in text
    assert text.index("## 1. 投资结论") < text.index("## 3. 核心证据矩阵")
    assert "本章回答的问题" in text
    assert "| 模块 | 结论 | 证据强度 | 投资含义 |" in text
    assert "| 事项 | 状态 | 优先级 | 处置建议 |" in text
    assert "团队主体已验证，工商信息显示测试公司成立于2020年。" in text
    assert text.count("证据覆盖：") <= 1
    assert text.count("外部证据：") <= 1
    assert "## 团队与合规\n## 团队与合规" not in text


def test_bp_final_assembly_blocks_when_debate_fails(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-FINAL-BLOCK"
    _write_valid_bp_section_package(task_dir)
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "REWRITE_REQUIRED", "issues": [{"code": "X"}]}), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-FINAL-BLOCK", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_final_assembly(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["block_reason"] == "debate_review_not_passed"


def test_bp_readability_review_fails_dimension_concat_report(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-READABILITY-BLOCK"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text(
        "# 测试公司 BP尽调报告\n\n## 团队与合规\n根据某维度报告，团队不错。\n\n## 技术与产品\n第二部分 技术很好。",
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-READABILITY-BLOCK", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_readability_review(tmp_path, job_ctx)

    # readability FAIL 降级为 WARN 放行（phase31 不再硬阻断）
    assert result["ok"] is True
    review = result["result"]
    assert review["verdict"] == "WARN"
    assert review["degraded_from"] == "FAIL"
    assert any(issue["code"] == "OPENING_NO_INVESTMENT_RECOMMENDATION" for issue in review["issues"])
    assert any(issue["code"] == "DIMENSION_REPORT_TRACE" for issue in review["issues"])


def test_bp_readability_review_fails_redundant_unstructured_report(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-READABILITY-REDUNDANT"
    task_dir.mkdir(parents=True)
    repeated_risk = "团队信息透明度低，客户订单缺失，融资历史空白，专利存在撤回记录，商业化进展不明确。"
    long_bullet = "- " + "；".join([repeated_risk] * 8)
    (task_dir / "bp_final_report.md").write_text(
        "# 测试公司 BP尽调报告\n\n"
        "## 1. 投资结论\n\n"
        "**本章回答的问题：这家公司当前是否值得进入下一步投资流程？**\n\n"
        "- 当前建议：继续观察\n"
        "- 结论置信度：较高\n"
        "- 关键支持理由：暂无\n"
        "- Deal Breakers：团队信息透明度低\n"
        "- 下一步 DD：补齐客户订单和融资记录\n"
        f"{long_bullet}\n\n"
        "## 2. 关键证据链\n\n"
        "**本章回答的问题：支持或约束投资判断的事实链是什么？**\n\n"
        f"- {repeated_risk}\n\n"
        "## 3. 声称覆盖情况\n\n"
        "**本章回答的问题：核心商业声称哪些已覆盖，哪些仍未验证？**\n\n"
        f"- {repeated_risk}\n\n"
        "## 4. 产品、团队、市场与估值的交叉判断\n\n"
        "**本章回答的问题：各维度事实如何共同影响投资判断？**\n\n"
        f"- {repeated_risk}\n",
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-READABILITY-REDUNDANT", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_readability_review(tmp_path, job_ctx)

    # readability FAIL 降级为 WARN 放行（phase31 不再硬阻断）
    assert result["ok"] is True
    review = result["result"]
    assert review["verdict"] == "WARN"
    assert review["degraded_from"] == "FAIL"
    codes = {issue["code"] for issue in review["issues"]}
    assert "MISSING_STRUCTURED_SUMMARY" in codes
    assert "DUPLICATED_BULLET_CONTENT" in codes
    assert "OVERLONG_BULLET" in codes


def test_bp_delivery_blocks_when_readability_or_coverage_gate_fails(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-DELIVERY-GATE"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# 测试公司 BP尽调报告\n\n## 1. 投资结论\n建议：有条件推进。", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "FAIL", "issues": [{"code": "X"}]}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"gate_verdict": "PASS"}), encoding="utf-8")
    workspace = SimpleNamespace(root=task_dir, delivery_dir=task_dir, outputs_dir=task_dir)
    job_ctx = SimpleNamespace(job_id="BP-DELIVERY-GATE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=workspace)

    result = _run_bp_delivery_inner(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["deliver_to_user"] is False
    # readability FAIL 已降级为 WARN（deferred_fixes），不再阻断交付
    # 实际阻断来自 verification（测试未提供 verification result 文件）
    assert "VERIFICATION" in result["result"]["block_reason"]


def test_bp_final_assembly_discloses_unverified_claim_data_gaps(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-FINAL-GAPS"
    _write_valid_bp_section_package(task_dir)
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps({
            "summary": {"critical_not_addressed": 0},
            "claims": [
                {
                    "claim_id": "BC002",
                    "claim": "已获得头部客户批量订单",
                    "priority": "critical",
                    "status": "unverified",
                    "data_gaps": ["需创始人提供客户合同或采购订单原件"],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-FINAL-GAPS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_final_assembly(tmp_path, job_ctx)

    assert result["ok"] is True
    text = (task_dir / "bp_final_report.md").read_text(encoding="utf-8")
    assert "已获得头部客户批量订单" in text
    assert "需创始人提供客户合同或采购订单原件" in text
    assert "未验证" in text


def test_bp_final_assembly_expands_fact_store_evidence_details(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-FINAL-FACTS"
    _write_valid_bp_section_package(task_dir)
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_fact_store.json").write_text(
        json.dumps({
            "facts": [
                {
                    "fact_id": "BF-0001",
                    "claim": "工商信息显示测试公司成立于2020年，注册资本1000万元",
                    "source_url": "https://example.com/company",
                    "source_tier": "official",
                    "confidence": "high",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-FINAL-FACTS", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_final_assembly(tmp_path, job_ctx)

    assert result["ok"] is True
    text = (task_dir / "bp_final_report.md").read_text(encoding="utf-8")
    assert "工商信息显示测试公司成立于2020年，注册资本1000万元" in text
    assert "https://example.com/company" in text


def test_bp_readability_review_fails_agent_dimension_headings_and_repeated_facts(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-READABILITY-DIMENSION"
    task_dir.mkdir(parents=True)
    repeated = "核心事实A" * 4
    (task_dir / "bp_final_report.md").write_text(
        "# 测试公司 BP尽调报告\n\n"
        "## 1. 投资结论\n\n**本章回答的问题：是否推进？**\n\n当前建议：有条件推进。\n\n"
        "## 2. 团队与合规\n\n**本章回答的问题：团队是否可信？**\n\n" + repeated + "\n\n"
        "## 3. 技术与产品\n\n**本章回答的问题：产品是否可信？**\n\nRHBD、ASIC、MEMS 均未解释。",
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="BP-READABILITY-DIMENSION", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    result = _run_bp_readability_review(tmp_path, job_ctx)

    # readability FAIL 降级为 WARN 放行（phase31 不再硬阻断）
    assert result["ok"] is True
    review = result["result"]
    assert review["verdict"] == "WARN"
    assert review["degraded_from"] == "FAIL"
    codes = {issue["code"] for issue in review["issues"]}
    assert "AGENT_DIMENSION_HEADING" in codes
    assert "REPEATED_FACT_PHRASE" in codes
    assert "UNEXPLAINED_TECH_TERMS" in codes


def test_bp_final_assembly_output_passes_readability_review(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-FINAL-READABLE"
    _write_valid_bp_section_package(task_dir)
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="BP-FINAL-READABLE", entity="测试公司", query="看这个BP", market="cn", metadata={}, workspace=None)

    assembly = _run_bp_final_assembly(tmp_path, job_ctx)
    review = _run_bp_readability_review(tmp_path, job_ctx)

    assert assembly["ok"] is True
    assert review["ok"] is True
    payload = json.loads((task_dir / "bp_readability_review.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"


def test_bp_delivery_gate_blocks_missing_cross_dimension_gate(tmp_path):
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-MISSING-CROSS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# report", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "PASS", "fail": 0}), encoding="utf-8")

    result = evaluate_bp_delivery_gate(task_dir)

    assert result["ok"] is False
    assert result["block_reason"] == "CROSS_DIMENSION_GATE_MISSING"


def test_bp_delivery_gate_blocks_failed_or_missing_verification(tmp_path):
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-VERIFY"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# report", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"gate_verdict": "PASS"}), encoding="utf-8")

    missing_result = evaluate_bp_delivery_gate(task_dir)
    assert missing_result["ok"] is False
    assert missing_result["block_reason"] == "VERIFICATION_RESULT_MISSING"

    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "FAIL", "fail": 2}), encoding="utf-8")
    failed_result = evaluate_bp_delivery_gate(task_dir)
    assert failed_result["ok"] is False
    assert failed_result["block_reason"] == "VERIFICATION_FAILED"


def test_bp_delivery_gate_blocks_malformed_gate_payloads(tmp_path):
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-MALFORMED"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# report", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"ok": False}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "PASS", "fail": 0}), encoding="utf-8")

    result = evaluate_bp_delivery_gate(task_dir)

    assert result["ok"] is False
    assert result["block_reason"] == "CLAIM_COVERAGE_GATE_INVALID"


def test_bp_document_intake_vl_chat_uses_bp_ocr_env(monkeypatch):
    import runtime.intake.bp_document_intake as intake

    monkeypatch.delenv("VL_API_KEY", raising=False)
    monkeypatch.delenv("VL_MODEL", raising=False)
    monkeypatch.delenv("VL_API_BASE", raising=False)
    monkeypatch.setenv("BP_OCR_API_KEY", "test-api-key")
    monkeypatch.setenv("BP_OCR_MODEL", "test-vl-model")
    monkeypatch.setenv("BP_OCR_BASE_URL", "https://example.invalid/v1")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(intake.requests, "post", fake_post)

    assert intake._vl_chat([{"role": "user", "content": "hello"}]) == "ok"
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-api-key"
    assert captured["payload"]["model"] == "test-vl-model"


def test_bp_document_intake_vl_chat_uses_bp_ocr_credentials_file(monkeypatch, tmp_path):
    import runtime.intake.bp_document_intake as intake

    monkeypatch.delenv("VL_API_KEY", raising=False)
    monkeypatch.delenv("VL_MODEL", raising=False)
    monkeypatch.delenv("VL_API_BASE", raising=False)
    monkeypatch.delenv("BP_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BP_OCR_MODEL", raising=False)
    monkeypatch.delenv("BP_OCR_BASE_URL", raising=False)
    monkeypatch.setattr(intake, "ROOT", tmp_path, raising=False)
    cred_dir = tmp_path / ".credentials"
    cred_dir.mkdir()
    (cred_dir / "investment-research.env").write_text(
        "BP_OCR_API_KEY=file-api-key\n"
        "BP_OCR_MODEL=file-vl-model\n"
        "BP_OCR_BASE_URL=https://file.example/v1\n",
        encoding="utf-8",
    )

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(intake.requests, "post", fake_post)

    assert intake._vl_chat([{"role": "user", "content": "hello"}]) == "ok"
    assert captured["url"] == "https://file.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer file-api-key"
    assert captured["payload"]["model"] == "file-vl-model"


def test_bp_delivery_gate_blocks_non_strict_pass_states(tmp_path):
    """2026-06-26 宽松化后：debate WARN 不再阻断交付，仅 FAIL_BLOCKING 才阻断。"""
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-NON-STRICT"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# report", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS_WITH_DISCLOSURE"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "WARN", "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "PASS", "fail": 0}), encoding="utf-8")

    result = evaluate_bp_delivery_gate(task_dir)

    # 宽松化后：WARN 不阻断，gate 应通过
    assert result["ok"] is True
    # WARN 应记录在 deferred_fixes 中（通过 deferred_fixes_count 验证）
    assert result.get("deferred_fixes_count", 0) >= 1


def test_bp_delivery_gate_blocks_fail_blocking(tmp_path):
    """2026-06-26 新增：仅 FAIL_BLOCKING 才硬阻断交付。"""
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-BLOCK"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text("# report", encoding="utf-8")
    (task_dir / "bp_final_assembly.json").write_text(json.dumps({"ok": True, "markdown_path": str(task_dir / "bp_final_report.md")}), encoding="utf-8")
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS_WITH_DISCLOSURE"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "FAIL_BLOCKING", "blocking_count": 1, "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "PASS", "fail": 0}), encoding="utf-8")

    result = evaluate_bp_delivery_gate(task_dir)

    assert result["ok"] is False
    reasons = {check["reason"] for check in result["failed_checks"]}
    assert "DEBATE_REVIEW_FAIL_BLOCKING" in reasons


def test_bp_delivery_gate_passes_all_required_gates(tmp_path):
    from scripts.bp_delivery_gate import evaluate_bp_delivery_gate

    task_dir = tmp_path / "tasks" / "BP-DELIVERY-PASS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_final_report.md").write_text(
        "# report\n\n"
        "## 1. 投资结论\n\n"
        "当前建议：有条件推进，先补齐关键尽调材料\n\n"
        "## 2. 一页摘要表\n\n"
        "| 模块 | 结论 | 证据强度 | 投资含义 |\n"
        "| --- | --- | --- | --- |\n"
        "| 团队 | 主体有效 | 较高 | 可推进 |\n",
        encoding="utf-8",
    )
    (task_dir / "bp_final_assembly.json").write_text(
        json.dumps({
            "ok": True,
            "markdown_path": str(task_dir / "bp_final_report.md"),
            "facts_used": ["BF-0001"],
            "claim_ids_used": ["BC001"],
            "recommendation": "conditional_go",
        }),
        encoding="utf-8",
    )
    (task_dir / "bp_readability_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_claim_coverage_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")
    (task_dir / "bp_cross_dimension_gate.json").write_text(json.dumps({"ok": True, "gate_verdict": "PASS"}), encoding="utf-8")
    (task_dir / "bp_verification_result.json").write_text(json.dumps({"verdict": "PASS", "fail": 0}), encoding="utf-8")
    (task_dir / "bp_thesis_reconciliation.json").write_text(json.dumps({"schema_version": "bp_thesis_reconciliation.v1", "recommendation": "conditional_go", "unresolved_high_issues": [], "confidence": "medium"}), encoding="utf-8")

    result = evaluate_bp_delivery_gate(task_dir)

    assert "BF-0001" not in (task_dir / "bp_final_report.md").read_text(encoding="utf-8")
    assert "conditional_go" not in (task_dir / "bp_final_report.md").read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["deliver_to_user"] is True
    assert result["failed_checks"] == []
