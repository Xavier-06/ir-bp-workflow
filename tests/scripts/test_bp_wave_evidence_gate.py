import json

from scripts.bp_wave_evidence_gate import evaluate_bp_wave_evidence_gate


def test_wave_gate_fails_missing_section_sidecar(tmp_path):
    task_dir = tmp_path / "BP-WAVE"
    task_dir.mkdir()
    (task_dir / "bp_dim_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")

    result = evaluate_bp_wave_evidence_gate(task_dir, wave=1)

    assert result["ok"] is False
    assert result["gate_verdict"] == "REPAIR"  # v4.4: 缺 sidecar 触发 repair 分支，不再硬 FAIL
    assert result["role_results"][0]["status"] == "missing_sidecar"
    assert result["repair_tasks"]


def test_wave_gate_fails_critical_claim_not_addressed(tmp_path):
    task_dir = tmp_path / "BP-WAVE"
    task_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_dim_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")
    (task_dir / "bp_dim_company_team_compliance-facts.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    (task_dir / "bp_dim_company_team_compliance-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = evaluate_bp_wave_evidence_gate(task_dir, wave=1)

    assert result["ok"] is False
    assert "BC001" in result["blocking_claims"]
    assert result["repair_tasks"][0]["claim_id"] == "BC001"


def test_wave_gate_passes_valid_role_package(tmp_path):
    task_dir = tmp_path / "BP-WAVE"
    task_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_dim_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")
    (task_dir / "bp_dim_company_team_compliance-facts.json").write_text(json.dumps({"facts": [{"fact_id": "BF-1"}]}), encoding="utf-8")
    (task_dir / "bp_dim_company_team_compliance-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": ["BC001"], "search_audit": {"claim_coverage": [{"claim_id": "BC001", "evidence_verdict": "supported"}]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = evaluate_bp_wave_evidence_gate(task_dir, wave=1)

    assert result["ok"] is True
    assert result["gate_verdict"] == "PASS"


def test_wave_gate_finds_sidecar_in_outputs_dir(tmp_path):
    """Bug 3: sidecar 在 outputs_dir 时，gate 应该能找到并通过"""
    task_dir = tmp_path / "BP-WAVE"
    outputs_dir = tmp_path / "outputs"
    task_dir.mkdir()
    outputs_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_market_supply_chain", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 子代理把三件套写到 outputs_dir
    (outputs_dir / "bp_dim_market_supply_chain.md").write_text("## market\n" + "x" * 200, encoding="utf-8")
    (outputs_dir / "bp_dim_market_supply_chain-facts.json").write_text(json.dumps({"facts": [{"fact_id": "BF-1"}]}), encoding="utf-8")
    (outputs_dir / "bp_dim_market_supply_chain-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": ["BC001"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = evaluate_bp_wave_evidence_gate(task_dir, wave=1, outputs_dir=outputs_dir)

    assert result["ok"] is True
    assert result["gate_verdict"] == "PASS"
    assert result["role_results"][0]["status"] == "pass"


def test_wave_gate_blocking_claims_degraded_after_retry(tmp_path):
    """Bug 4: blocking_claims 重试超过阈值后应降级为 WARN 放行"""
    task_dir = tmp_path / "BP-WAVE"
    task_dir.mkdir()
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps({"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "priority": "critical"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_dim_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")
    (task_dir / "bp_dim_company_team_compliance-facts.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    # section 存在但没覆盖 BC001
    (task_dir / "bp_dim_company_team_compliance-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 模拟 gate 已执行过 1 次（达到降级阈值）
    (task_dir / "bp_wave1_evidence_gate.json").write_text(
        json.dumps({"attempt": 1, "blocking_claims": ["BC001"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = evaluate_bp_wave_evidence_gate(task_dir, wave=1)

    assert result["ok"] is True
    assert result["gate_verdict"] == "PASS"
    assert result["blocking_claims_degraded"] is True
    assert "BC001" in result["blocking_claims"]
