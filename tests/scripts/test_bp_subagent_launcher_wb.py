import json
from pathlib import Path

from scripts.bp_subagent_launcher_wb import (
    CURRENT_BP_ROLES,
    ROLE_SYSTEM_PROMPTS,
    _BP_SEARCH_TEMPLATES,
    _build_brief,
    _check_role_quality,
    _read_brief_content,
    _rewrite_role,
    _spawn_one,
    launch_and_verify,
)


def test_read_brief_content_keeps_paths_without_inlining_referenced_files(tmp_path, monkeypatch):
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    large_prior = task_dir / "bp_dim_team.md"
    large_prior.write_text("前序输出" * 5000, encoding="utf-8")
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)

    brief = task_dir / "brief.md"
    brief.write_text(f"# Brief\n- `{large_prior.relative_to(tmp_path)}`\n", encoding="utf-8")

    content = _read_brief_content(brief)

    assert str(large_prior.relative_to(tmp_path)) in content
    assert "前序输出" not in content


def test_spawn_one_manifest_uses_general_purpose_agent_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    sub = {
        "role_name": "bp_company_team_compliance",
        "description": "团队分析",
        "output_file": str(task_dir / "bp_dim_company_team_compliance.md"),
        "key_inputs": {},
    }

    result = _spawn_one("TASK-BP", sub, task_dir=task_dir)

    manifest = json.loads((task_dir / "bp_dim_manifest_company_team_compliance.json").read_text(encoding="utf-8"))
    assert result["status"] == "dispatched"
    assert manifest["subagent_type"] == "general-purpose"
    assert manifest["dispatch_mode"] == "team_async"
    assert manifest["mode"] == "bypassPermissions"
    assert "subagent_name" not in manifest


def test_build_brief_includes_fact_store_and_research_plan_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text("{}", encoding="utf-8")
    (task_dir / "bp_fact_store.json").write_text("{}", encoding="utf-8")
    (task_dir / "bp_shared_diligence_page.md").write_text("# shared", encoding="utf-8")
    sub = {
        "role_name": "bp_company_team_compliance",
        "description": "团队分析",
        "output_file": str(task_dir / "bp_dim_company_team_compliance.md"),
        "key_inputs": {},
    }

    brief_path = _build_brief("TASK-BP", sub, task_dir=task_dir)
    brief = brief_path.read_text(encoding="utf-8")

    assert "bp_research_plan.json" in brief
    assert "bp_fact_store.json" in brief
    assert "bp_shared_diligence_page.md" in brief
    assert "必须先读共享尽调页" in brief
    assert "必须用 Read 工具读取" in brief


def test_build_brief_injects_role_owned_research_and_claim_slices(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_research_plan.v2",
            "prepared_by": "script_scaffold_plus_orchestrator_enrichment",
            "generation_roles": {
                "script": "schema_fact_requirements_coverage_matrix_validation",
                "orchestrator_agent": "strategic_questions_claim_prioritization_owner_assignment",
            },
            "core_questions": [
                {"question_id": "BQ1", "question": "团队是否可信？", "owner_section": "bp_company_team_compliance", "required_fact_keys": ["team_background"]},
                {"question_id": "BQ2", "question": "产品是否商业化？", "owner_section": "bp_product_commercial", "required_fact_keys": ["commercial_stage"]},
            ],
            "strategic_questions": [
                {"question_id": "BSQ1", "question": "关键人风险是否可缓释？", "owner_section": "bp_company_team_compliance", "required_fact_keys": ["governance_risk"]}
            ],
            "claim_matrix": [
                {"claim_id": "BC001", "claim": "团队履历优秀", "owner_section": "bp_company_team_compliance", "priority": "critical"},
                {"claim_id": "BC002", "claim": "产品已量产", "owner_section": "bp_product_commercial", "priority": "critical"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text("{}", encoding="utf-8")
    sub = {
        "role_name": "bp_company_team_compliance",
        "description": "团队分析",
        "output_file": str(task_dir / "bp_dim_company_team_compliance.md"),
        "key_inputs": {},
    }

    brief_path = _build_brief("TASK-BP", sub, task_dir=task_dir)
    brief = brief_path.read_text(encoding="utf-8")

    assert "## 当前角色 Research Plan Slice" in brief
    assert "script_scaffold_plus_orchestrator_enrichment" in brief
    assert "团队是否可信？" in brief
    assert "关键人风险是否可缓释？" in brief
    assert "团队履历优秀" in brief
    assert "产品是否商业化？" not in brief
    assert "产品已量产" not in brief
    assert "禁止处理非 owner claims" in brief


def test_build_brief_includes_claim_level_search_work_order(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    (task_dir / "bp_search_plan.json").write_text(
        json.dumps({
            "schema_version": "bp_search_plan.v1",
            "search_tasks": [
                {
                    "search_task_id": "BST-001",
                    "claim_id": "BC005",
                    "owner_section": "bp_product_commercial",
                    "queries": ["\"测试公司\" 客户 合同 订单 回款"],
                    "min_unique_queries": 4,
                    "min_fetched_urls": 2,
                    "min_independent_domains": 2,
                    "requires_counter_search": True,
                    "required_source_tiers": ["customer_or_partner_disclosure", "reputable_media"],
                },
                {
                    "search_task_id": "BST-002",
                    "claim_id": "BC007",
                    "owner_section": "bp_valuation_return",
                    "queries": ["\"测试公司\" 融资 估值"],
                },
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    sub = {
        "role_name": "bp_product_commercial",
        "description": "产品商业化验证",
        "output_file": str(task_dir / "bp_dim_product_commercial.md"),
        "key_inputs": {},
    }

    brief_path = _build_brief("TASK-BP", sub, task_dir=task_dir)
    brief = brief_path.read_text(encoding="utf-8")

    assert "bp_search_plan.json" in brief
    assert "## 当前角色 Search Work Order" in brief
    assert "BST-001" in brief
    assert "BC005" in brief
    assert "min_unique_queries" in brief
    assert "requires_counter_search" in brief
    assert "BST-002" not in brief



def test_build_brief_enforces_bp_sidecar_output_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    sub = {
        "role_name": "bp_tech_ip_moat",
        "description": "技术分析",
        "output_file": str(task_dir / "bp_dim_tech_ip_moat.md"),
        "key_inputs": {},
    }

    brief_path = _build_brief("TASK-BP", sub, task_dir=task_dir)
    brief = brief_path.read_text(encoding="utf-8")

    assert "bp_dim_tech_ip_moat-facts.json" in brief
    assert "bp_dim_tech_ip_moat-section.json" in brief
    assert "schema_version" in brief
    assert "bp_section_package.v2" in brief
    assert "answers" in brief
    assert "claim_ids_covered" in brief
    assert "narrative_blocks" in brief
    assert "fact_ids" in brief
    assert "缺少 sidecar = 任务未完成" in brief


def test_spawn_one_manifest_exposes_sidecar_paths_for_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    sub = {
        "role_name": "bp_valuation_return",
        "description": "估值分析",
        "output_file": str(task_dir / "bp_dim_valuation_return.md"),
        "key_inputs": {},
    }

    _spawn_one("TASK-BP", sub, task_dir=task_dir)

    manifest = json.loads((task_dir / "bp_dim_manifest_valuation_return.json").read_text(encoding="utf-8"))
    assert manifest["sidecar_paths"] == {
        "facts": str(task_dir / "bp_dim_valuation_return-facts.json"),
        "section_package": str(task_dir / "bp_dim_valuation_return-section.json"),
    }
    assert "bp_dim_valuation_return-facts.json" in manifest["brief_content_preview"]


def test_spawn_one_manifest_persists_wave_inputs_beyond_preview(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    shared_inputs = {
        "shared_page": str(task_dir / "bp_shared_diligence_page.md"),
        "shared_state": str(task_dir / "bp_shared_state.json"),
        "claim_coverage": str(task_dir / "bp_claim_coverage.json"),
        "fact_store": str(task_dir / "bp_fact_store.json"),
    }
    prior_outputs = {
        "bp_company_team_compliance": str(task_dir / "bp_dim_company_team_compliance.md"),
        "bp_product_commercial": str(task_dir / "bp_dim_product_commercial.md"),
    }
    for path in list(shared_inputs.values()) + list(prior_outputs.values()):
        Path(path).write_text("{}", encoding="utf-8")
    sub = {
        "role_name": "bp_product_commercial",
        "description": "产品商业化验证",
        "output_file": str(task_dir / "bp_dim_product_commercial.md"),
        "key_inputs": {"shared_inputs": shared_inputs, "prior_dimension_outputs": prior_outputs},
        "wave_inputs": {**shared_inputs, **prior_outputs},
    }

    _spawn_one("TASK-BP", sub, task_dir=task_dir)

    manifest = json.loads((task_dir / "bp_dim_manifest_product_commercial.json").read_text(encoding="utf-8"))
    assert manifest["key_inputs"]["shared_inputs"] == shared_inputs
    assert manifest["wave_inputs"]["bp_company_team_compliance"] == prior_outputs["bp_company_team_compliance"]


def test_new_bp_roles_have_role_specific_system_prompt_boundaries():
    assert "客户" in ROLE_SYSTEM_PROMPTS["bp_product_commercial"]
    assert "Deal Breakers" in ROLE_SYSTEM_PROMPTS["bp_dealbreaker_risk"]
    assert "公司主体" in ROLE_SYSTEM_PROMPTS["bp_company_team_compliance"]
    assert "市场规模" in ROLE_SYSTEM_PROMPTS["bp_market_supply_chain"]


def test_wave_dealbreaker_prompts_do_not_reuse_competition_final_chapter():
    dealbreaker_prompt = ROLE_SYSTEM_PROMPTS["bp_dealbreaker_risk"]

    assert "writing the final chapter" not in dealbreaker_prompt
    assert "Deal Breaker 清单" in dealbreaker_prompt
    assert "不可缓释" in dealbreaker_prompt


def test_market_supply_chain_uses_dedicated_simplified_role_prompt():
    # Legacy key bp_行业与供应链 is no longer loaded; check current role instead
    assert "bp_market_supply_chain" in ROLE_SYSTEM_PROMPTS
    prompt = ROLE_SYSTEM_PROMPTS["bp_market_supply_chain"]

    # Should be the dedicated market supply chain analyst prompt
    assert "市场" in prompt or "supply chain" in prompt.lower() or "供应链" in prompt


def test_all_current_bp_roles_are_registered_in_instruction_store():
    root = Path(__file__).resolve().parents[2]
    index = json.loads((root / "instruction_store_bp" / "index.json").read_text(encoding="utf-8"))
    role_files = {role["key"]: role["file"] for role in index["roles"]}
    required_roles = {
        "bp_company_team_compliance",
        "bp_product_commercial",
        "bp_tech_ip_moat",
        "bp_market_supply_chain",
        "bp_competition_positioning",
        "bp_valuation_return",
        "bp_dealbreaker_risk",
    }

    assert required_roles <= set(role_files)
    for role in required_roles:
        path = root / "instruction_store_bp" / role_files[role]
        assert path.exists(), f"missing instruction file for {role}: {path}"


def test_current_bp_role_prompts_are_loaded_exactly_from_instruction_store():
    root = Path(__file__).resolve().parents[2]
    index = json.loads((root / "instruction_store_bp" / "index.json").read_text(encoding="utf-8"))
    role_files = {role["key"]: role["file"] for role in index["roles"]}

    for role in CURRENT_BP_ROLES:
        instruction = (root / "instruction_store_bp" / role_files[role]).read_text(encoding="utf-8")
        assert ROLE_SYSTEM_PROMPTS[role] == instruction

    assert "# BP 产品商业化分析师" in ROLE_SYSTEM_PROMPTS["bp_product_commercial"]
    assert "# BP 技术、IP 与壁垒分析师" in ROLE_SYSTEM_PROMPTS["bp_tech_ip_moat"]
    assert "# BP Deal Breaker 红队分析师" in ROLE_SYSTEM_PROMPTS["bp_dealbreaker_risk"]
    assert "技术原理深度分析" not in ROLE_SYSTEM_PROMPTS["bp_product_commercial"]
    assert "客户清单" not in ROLE_SYSTEM_PROMPTS["bp_tech_ip_moat"]
    assert "cp /tmp" not in ROLE_SYSTEM_PROMPTS["bp_valuation_return"]


def test_current_bp_role_prompts_state_vc_diligence_identity():
    # v4.5 narrative roles (Wave 4) have different identities — buyer analyst, not VC researcher
    # (Wave 0 investment_hypothesis 已删除 2026-07-29)
    narrative_roles = {"bp_consensus_challenge", "bp_catalyst", "bp_industry_research"}
    diligence_roles = CURRENT_BP_ROLES - narrative_roles
    for role in diligence_roles:
        prompt = ROLE_SYSTEM_PROMPTS[role]
        assert "VC 投资研究员" in prompt, f"{role} missing 'VC 投资研究员'"
        assert "项目尽调" in prompt, f"{role} missing '项目尽调'"


def test_build_brief_states_vc_diligence_context(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    sub = {
        "role_name": "bp_product_commercial",
        "description": "产品商业化分析",
        "output_file": str(task_dir / "bp_dim_product_commercial.md"),
        "key_inputs": {},
    }

    brief_path = _build_brief("TASK-BP", sub, task_dir=task_dir)
    brief = brief_path.read_text(encoding="utf-8")

    # Brief delegates identity to System Prompt; check the delegation line exists
    assert "System Prompt" in brief
    assert "bp_product_commercial" in brief


def test_current_bp_roles_have_supplementary_search_templates():
    assert CURRENT_BP_ROLES <= set(_BP_SEARCH_TEMPLATES)
    for role in CURRENT_BP_ROLES:
        assert len(_BP_SEARCH_TEMPLATES[role]) >= 3


def test_unknown_bp_role_does_not_fallback_to_competition_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    sub = {
        "role_name": "bp_unknown_role",
        "description": "未知角色",
        "output_file": str(task_dir / "bp_dim_unknown.md"),
        "key_inputs": {},
    }

    _spawn_one("TASK-BP", sub, task_dir=task_dir)

    manifest = json.loads((task_dir / "bp_dim_manifest_unknown_role.json").read_text(encoding="utf-8"))
    assert "UNKNOWN BP ROLE" in manifest["system_prompt"]
    assert "writing the final chapter" not in manifest["system_prompt"]


# ── PR4: 质量门禁 + rewrite + launch_and_verify ───────────


def _write_full_sidecars(task_dir: Path, slug: str) -> Path:
    """写一组完整合规的 facts + section sidecar，验门禁能放行。"""
    output = task_dir / f"bp_dim_{slug}.md"
    output.write_text("# 估值章节\n" + ("val " * 200), encoding="utf-8")
    (task_dir / f"bp_dim_{slug}-facts.json").write_text(
        json.dumps({
            "role": "bp_valuation_return",
            "facts": [{
                "fact_id": "BP-VAL-F001",
                "claim": "中位PE", "value": "25.3", "unit": "x",
                "period": "TTM", "source_url": "https://example.com/a",
                "source_tier": "database", "source_quote": "样本...",
                "question_id": "val_q1", "fact_type": "valuation",
                "confidence": "high",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / f"bp_dim_{slug}-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "section_id": "bp_valuation_return",
            "section_title": "估值",
            "key_messages": ["基于..."],
            "answers": [{
                "question_id": "val_q1", "answer": "A",
                "fact_ids": ["BP-VAL-F001"],
                "confidence": "high", "limits": "sample",
            }],
            "claim_ids_covered": ["BC001"],
            "claims": [{
                "claim_id": "BC001", "claim": "x",
                "fact_ids": ["BP-VAL-F001"],
                "reasoning": "r", "confidence": "high",
                "source_quality": "database",
            }],
            "facts_used": ["BP-VAL-F001"],
            "counter_evidence": [], "data_gaps": [],
            "search_audit": {
                "queries": [
                    {"query": f"q{i}", "purpose": "p",
                     "result_count": 3, "fetched_urls": ["https://a.com"]}
                    for i in range(10)
                ],
                "fetched_urls": [
                    "https://a.com", "https://b.com",
                    "https://c.com", "https://d.com",
                ],
                "source_domains": ["a.com", "b.com", "c.com", "d.com"],
            },
            "narrative_blocks": [{
                "block_id": "val_NB001", "question_id": "val_q1",
                "claim_ids": ["BC001"], "fact_ids": ["BP-VAL-F001"],
                "text": "t",
            }],
            "markdown_draft": "d",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return output


def test_check_role_quality_passes_for_full_sidecars(tmp_path):
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    output = _write_full_sidecars(task_dir, "valuation_return")
    quality = _check_role_quality("bp_valuation_return", task_dir, output)
    assert quality["passed"] is True
    assert quality["score"] == 1.0
    assert quality["errors"] == []
    assert quality["details"]["fact_count"] == 1
    assert quality["details"]["search_audit_queries"] == 10


def test_check_role_quality_flags_missing_sidecars(tmp_path):
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    output = task_dir / "bp_dim_valuation_return.md"
    output.write_text("hi", encoding="utf-8")  # < 200 字符
    quality = _check_role_quality("bp_valuation_return", task_dir, output)
    assert quality["passed"] is False
    assert quality["score"] < 1.0
    assert any("output_markdown_too_short" in e for e in quality["errors"])
    assert any("facts_sidecar_missing" in e for e in quality["errors"])
    assert any("section_sidecar_missing" in e for e in quality["errors"])


def test_check_role_quality_flags_invalid_fact_binding(tmp_path):
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    output = task_dir / "bp_dim_valuation_return.md"
    output.write_text("x" * 500, encoding="utf-8")
    (task_dir / "bp_dim_valuation_return-facts.json").write_text(
        json.dumps({"facts": [{
            "fact_id": "F1", "claim": "c", "value": "v", "unit": "",
            "period": "p", "source_url": "https://x.com",
            "source_tier": "media", "source_quote": "q",
            "question_id": "q1", "fact_type": "valuation",
            "confidence": "high",
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_dim_valuation_return-section.json").write_text(
        json.dumps({
            "schema_version": "bp_section_package.v2",
            "answers": [{"question_id": "q1", "answer": "A",
                         "fact_ids": ["F999"],  # 未知 fact_id
                         "confidence": "high", "limits": "l"}],
            "claim_ids_covered": ["BC001"],
            "claims": [], "facts_used": ["F1"],
            "counter_evidence": [], "data_gaps": [],
            "search_audit": {
                "queries": [{"query": f"q{i}", "purpose": "p",
                             "result_count": 1, "fetched_urls": ["https://a.com"]}
                            for i in range(8)],
                "fetched_urls": ["https://a.com", "https://b.com", "https://c.com"],
                "source_domains": ["a.com", "b.com", "c.com"],
            },
            "narrative_blocks": [], "markdown_draft": "d",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    quality = _check_role_quality("bp_valuation_return", task_dir, output)
    assert quality["passed"] is False
    assert any("F999" in e for e in quality["errors"])


def test_rewrite_role_injects_memo_hint_into_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "TASK-BP"
    task_dir.mkdir()
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.TASKS_DIR", task_dir.parent)

    # 写最小 manifest（不实际派发）
    (task_dir / "bp_dim_manifest_valuation_return.json").write_text(
        json.dumps({
            "task_id": "TASK-BP", "role": "bp_valuation_return",
            "brief_content_preview": "preview before",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    ok = _rewrite_role("TASK-BP", "bp_valuation_return", task_dir, "/tmp/memo.md")
    assert ok is True

    manifest = json.loads((task_dir / "bp_dim_manifest_valuation_return.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "rewrite_pending"
    assert "PR4 补搜 Memo 引用" in manifest["brief_content_preview"]
    assert "/tmp/memo.md" in manifest["brief_content_preview"]


def test_launch_and_verify_no_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.ROOT", tmp_path)
    task_dir = tmp_path / "tasks" / "TASK-NOOP"
    task_dir.mkdir(parents=True)
    monkeypatch.setattr("scripts.bp_subagent_launcher_wb.TASKS_DIR", tmp_path / "tasks")

    result = launch_and_verify("TASK-NOOP", "bp_valuation_return", task_dir=task_dir)
    assert result["status"] == "no_dispatch"
    assert result["role"] == "bp_valuation_return"
