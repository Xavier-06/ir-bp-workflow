import json

from scripts.bp_claim_coverage_validator import evaluate_bp_claim_coverage
from scripts.bp_cross_dimension_gate import evaluate_bp_cross_dimension_gate
from scripts.bp_ic_redteam_gate import evaluate_bp_ic_redteam_gate
from scripts.bp_wave_evidence_gate import evaluate_bp_wave_evidence_gate
from scripts.bp_gap_repair_planner import plan_bp_gap_repairs


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── Shallow search package blocks before Wave2 ──

def test_shallow_wave1_blocks_before_wave2(tmp_path):
    _write_json(
        tmp_path / "bp_research_plan.json",
        {"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "priority": "critical"}]},
    )
    (tmp_path / "bp_phase2_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")
    (tmp_path / "bp_phase2_company_team_compliance-facts.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    (tmp_path / "bp_phase2_company_team_compliance-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": []}), encoding="utf-8",
    )

    result = evaluate_bp_wave_evidence_gate(tmp_path, wave=1)

    # Gate verdict is now REPAIR (not FAIL) on first attempt — repair sub-agents will be dispatched
    assert result["gate_verdict"] == "REPAIR"
    assert result["needs_repair"] is True
    assert "BC001" in result["blocking_claims"]
    assert result["repair_tasks"]


# ── Unsupported revenue blocks coverage and cross gates ──

def test_unsupported_revenue_blocks_coverage_then_cross_gate(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC_REV", "claim": "客户收入", "priority": "critical", "status": "unverified", "fact_ids": ["BF-BP"]}
            ],
        },
    )
    _write_json(
        tmp_path / "bp_fact_store.json",
        {"facts": [{"fact_id": "BF-BP", "source_tier": "bp", "source_url": "bp://deck", "confidence": "high"}]},
    )

    coverage = evaluate_bp_claim_coverage(tmp_path)
    assert coverage["gate_verdict"] == "FAIL"
    assert coverage["coverage"]["claims"][0]["status"] == "unverified"

    _write_json(tmp_path / "bp_claim_coverage_gate.json", coverage)
    _write_json(
        tmp_path / "bp_section_packages.json",
        {"packages": [{
            "validation": {"passed": True},
            "package": {
                "section_id": "bp_valuation_return",
                "claims": [{"claim_id": "BC_VAL", "claim": "估值使用客户订单收入", "fact_ids": ["BF-BP"], "source_quality": "official"}],
            },
        }]},
    )
    cross = evaluate_bp_cross_dimension_gate(tmp_path)
    assert any(issue["code"] == "VALUATION_DEPENDS_ON_UNSUPPORTED_REVENUE_CLAIM" for issue in cross["issues"])


# ── Missing Red Team schema blocks IC/RedTeam gate ──

def test_missing_red_team_schema_blocks_ic_gate(tmp_path):
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": True})
    _write_json(
        tmp_path / "bp_investment_thesis.json",
        {"schema_version": "bp_investment_thesis.v1", "recommendation": "proceed_with_caution",
         "supporting_reasons": ["市场大"], "must_verify_before_investment": [], "deal_breakers": [],
         "open_data_gaps": [], "confidence": "medium"},
    )
    _write_json(tmp_path / "bp_red_team_review.json", {"issues": []})

    result = evaluate_bp_ic_redteam_gate(tmp_path)
    assert result["gate_verdict"] == "FAIL"
    assert any(issue["code"] == "RT_MISSING_SCHEMA_VERSION" for issue in result["issues"])


# ── Valid packages flow through successfully ──

def test_valid_packages_proceed_through_gates(tmp_path):
    _write_json(
        tmp_path / "bp_research_plan.json",
        {"claim_matrix": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "priority": "high"}]},
    )
    (tmp_path / "bp_phase2_company_team_compliance.md").write_text("## team\n" + "x" * 200, encoding="utf-8")
    (tmp_path / "bp_phase2_company_team_compliance-facts.json").write_text(json.dumps({"facts": [{"fact_id": "BF-1"}]}), encoding="utf-8")
    (tmp_path / "bp_phase2_company_team_compliance-section.json").write_text(
        json.dumps({"schema_version": "bp_section_package.v2", "claim_ids_covered": ["BC001"], "search_audit": {"claim_coverage": [{"claim_id": "BC001", "evidence_verdict": "supported"}]}}),
        encoding="utf-8",
    )
    wave = evaluate_bp_wave_evidence_gate(tmp_path, wave=1)
    assert wave["gate_verdict"] == "PASS"

    _write_json(tmp_path / "bp_claim_coverage.json", {"schema_version": "bp_claim_coverage.v1", "claims": [{"claim_id": "BC001", "claim": "团队可验证", "priority": "high", "status": "supported", "fact_ids": ["BF-OFF"]}]})
    _write_json(tmp_path / "bp_fact_store.json", {"facts": [{"fact_id": "BF-OFF", "source_tier": "official", "source_url": "https://a.example/company", "confidence": "high"}]})
    coverage = evaluate_bp_claim_coverage(tmp_path)
    assert coverage["gate_verdict"] == "PASS"

    _write_json(tmp_path / "bp_claim_coverage_gate.json", coverage)
    _write_json(tmp_path / "bp_section_packages.json", {"packages": [{"validation": {"passed": True}, "package": {"section_id": "bp_company", "claims": [{"claim_id": "BC001", "claim": "团队可验证", "fact_ids": ["BF-OFF"], "source_quality": "official"}]}}]})
    cross = evaluate_bp_cross_dimension_gate(tmp_path)
    assert cross["gate_verdict"] == "PASS"

    _write_json(tmp_path / "bp_cross_dimension_gate.json", cross)
    _write_json(tmp_path / "bp_investment_thesis.json", {"schema_version": "bp_investment_thesis.v1", "recommendation": "proceed_with_caution", "supporting_reasons": ["市场大"], "must_verify_before_investment": [], "deal_breakers": [], "open_data_gaps": [], "confidence": "medium"})
    _write_json(tmp_path / "bp_red_team_review.json", {"schema_version": "bp_red_team_review.v1", "issues": [{"severity": "MEDIUM", "description": "需进一步验证"}]})
    ic_gate = evaluate_bp_ic_redteam_gate(tmp_path)
    assert ic_gate["gate_verdict"] == "PASS"

    repair = plan_bp_gap_repairs(tmp_path)
    assert not repair["tasks"]
