import json

from scripts.bp_ic_redteam_gate import evaluate_bp_ic_redteam_gate


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_ic_gate_fails_missing_schema(tmp_path):
    _write_json(tmp_path / "bp_investment_thesis.json", {"recommendation": "go"})
    _write_json(tmp_path / "bp_red_team_review.json", {"issues": []})

    result = evaluate_bp_ic_redteam_gate(tmp_path)

    assert result["ok"] is False
    assert any(issue["code"] == "IC_MISSING_SCHEMA_VERSION" for issue in result["issues"])


def test_ic_go_blocked_by_failed_coverage_gate(tmp_path):
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": False, "gate_verdict": "FAIL"})
    _write_json(
        tmp_path / "bp_investment_thesis.json",
        {
            "schema_version": "bp_investment_thesis.v1",
            "recommendation": "go",
            "supporting_reasons": ["市场大"],
            "must_verify_before_investment": [],
            "deal_breakers": [],
            "open_data_gaps": [],
            "confidence": "high",
        },
    )
    _write_json(
        tmp_path / "bp_red_team_review.json",
        {
            "schema_version": "bp_red_team_review.v1",
            "issues": [],
            "deal_breakers": [],
            "open_data_gaps": [],
        },
    )

    result = evaluate_bp_ic_redteam_gate(tmp_path)

    assert result["ok"] is False
    assert any(issue["code"] == "IC_GO_BLOCKED_BY_FAILED_COVERAGE" for issue in result["issues"])


def test_red_team_fails_on_unverified_critical_claims_without_attack(tmp_path):
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {
        "ok": True,
        "failed_claims": [],
        "coverage": {
            "claims": [
                {"claim_id": "BC001", "claim": "客户收入", "priority": "critical", "status": "unverified", "evidence_strength": "bp_only"},
            ]
        }
    })
    _write_json(
        tmp_path / "bp_investment_thesis.json",
        {
            "schema_version": "bp_investment_thesis.v1",
            "recommendation": "proceed_with_caution",
            "supporting_reasons": ["市场大"],
            "must_verify_before_investment": ["客户收入验证"],
            "deal_breakers": [],
            "open_data_gaps": [],
            "confidence": "medium",
        },
    )
    _write_json(
        tmp_path / "bp_red_team_review.json",
        {
            "schema_version": "bp_red_team_review.v1",
            "issues": [{"claim_id": "BC999", "severity": "MEDIUM", "description": "攻击了一个不存在的claim"}],
            "deal_breakers": [],
            "open_data_gaps": [],
        },
    )

    result = evaluate_bp_ic_redteam_gate(tmp_path)

    assert result["ok"] is False
    assert any(issue["code"] == "RED_TEAM_DID_NOT_ATTACK_CRITICAL_GAP" for issue in result["issues"])


def test_ic_redteam_gate_passes_with_valid_outputs(tmp_path):
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": True, "failed_claims": []})
    _write_json(
        tmp_path / "bp_investment_thesis.json",
        {
            "schema_version": "bp_investment_thesis.v1",
            "recommendation": "proceed_with_caution",
            "supporting_reasons": ["市场大", "团队强"],
            "must_verify_before_investment": ["客户合同原件"],
            "deal_breakers": [],
            "open_data_gaps": ["客户收入第三方验证"],
            "confidence": "medium",
        },
    )
    _write_json(
        tmp_path / "bp_red_team_review.json",
        {
            "schema_version": "bp_red_team_review.v1",
            "issues": [{"claim_id": "", "severity": "HIGH", "description": "收入假设缺乏独立验证"}],
            "deal_breakers": [],
            "open_data_gaps": ["客户收入第三方验证"],
        },
    )

    result = evaluate_bp_ic_redteam_gate(tmp_path)

    assert result["ok"] is True
    assert result["gate_verdict"] == "PASS"


def test_red_team_fails_with_empty_issues_and_no_explicit_clearance(tmp_path):
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": True, "failed_claims": []})
    _write_json(
        tmp_path / "bp_investment_thesis.json",
        {
            "schema_version": "bp_investment_thesis.v1",
            "recommendation": "go",
            "supporting_reasons": ["市场大"],
            "must_verify_before_investment": [],
            "deal_breakers": [],
            "open_data_gaps": [],
            "confidence": "high",
        },
    )
    _write_json(
        tmp_path / "bp_red_team_review.json",
        {
            "schema_version": "bp_red_team_review.v1",
            "issues": [],
            "deal_breakers": [],
            "open_data_gaps": [],
        },
    )

    result = evaluate_bp_ic_redteam_gate(tmp_path)

    assert result["ok"] is False
    assert any(issue["code"] == "RED_TEAM_NO_ISSUES_NO_EXPLICIT_CLEARANCE" for issue in result["issues"])
