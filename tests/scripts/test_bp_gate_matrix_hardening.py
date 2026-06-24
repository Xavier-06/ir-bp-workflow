import json

from scripts.bp_cross_dimension_gate import evaluate_bp_cross_dimension_gate
from scripts.bp_delivery_gate import evaluate_bp_delivery_gate
from scripts.bp_readability_reviewer import review_bp_readability


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cross_dimension_gate_detects_company_and_market_conflicts(tmp_path):
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_company_team_compliance",
                        "claims": [
                            {"claim_id": "C1", "claim": "主体A", "fact_type": "company_name", "value": "测试科技有限公司", "fact_ids": ["BF-001"], "source_quality": "official"},
                            {"claim_id": "C2", "claim": "注册资本", "fact_type": "registered_capital", "value": "1000万元", "fact_ids": ["BF-002"], "source_quality": "official"},
                        ],
                    },
                },
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_valuation_return",
                        "claims": [
                            {"claim_id": "V1", "claim": "主体B", "fact_type": "company_name", "value": "测试智能有限公司", "fact_ids": ["BF-003"], "source_quality": "database"},
                            {"claim_id": "V2", "claim": "TAM", "fact_type": "market_size", "value": "500亿元", "fact_ids": ["BF-004"], "source_quality": "research"},
                        ],
                    },
                },
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_market_supply_chain",
                        "claims": [
                            {"claim_id": "M1", "claim": "TAM", "fact_type": "market_size", "value": "50亿元", "fact_ids": ["BF-005"], "source_quality": "research"},
                        ],
                    },
                },
            ]
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    codes = {issue["code"] for issue in result["issues"]}
    assert result["ok"] is False
    assert "COMPANY_IDENTITY_CONFLICT" in codes
    assert "MARKET_SIZE_CONFLICT" in codes


def test_cross_dimension_gate_detects_competitor_and_scale_mismatches(tmp_path):
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_competition_positioning",
                        "claims": [{"claim_id": "CP1", "claim": "主要竞品没有量产能力", "fact_ids": ["BF-001"], "source_quality": "media"}],
                    },
                },
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_product_commercial",
                        "claims": [
                            {"claim_id": "P1", "claim": "主要竞品已经量产并交付", "fact_ids": ["BF-002"], "source_quality": "official"},
                            {"claim_id": "P2", "claim": "收入规模", "fact_type": "revenue", "value": "2亿元", "fact_ids": ["BF-003"], "source_quality": "official"},
                        ],
                    },
                },
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_company_team_compliance",
                        "claims": [{"claim_id": "T1", "claim": "团队人数", "fact_type": "team_size", "value": "5人", "fact_ids": ["BF-004"], "source_quality": "official"}],
                    },
                },
            ]
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    codes = {issue["code"] for issue in result["issues"]}
    assert result["ok"] is False
    assert "COMPETITOR_CAPABILITY_CONFLICT" in codes
    assert "TEAM_REVENUE_SCALE_MISMATCH" in codes


def test_cross_dimension_gate_does_not_treat_single_negative_competitor_claim_as_conflict(tmp_path):
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_competition_positioning",
                        "claims": [{"claim_id": "CP1", "claim": "主要竞品不具备量产能力", "fact_ids": ["BF-001"], "source_quality": "media"}],
                    },
                }
            ]
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    codes = {issue["code"] for issue in result["issues"]}
    assert "COMPETITOR_CAPABILITY_CONFLICT" not in codes


def test_delivery_gate_allows_disclosed_bp_claims_not_used_as_main_conclusion(tmp_path):
    (tmp_path / "bp_final_report.md").write_text("# 报告\n\n## 1. 投资结论\n\n当前建议：conditional_go\nBF-001\n", encoding="utf-8")
    _write_json(tmp_path / "bp_final_assembly.json", {"ok": True, "markdown_path": str(tmp_path / "bp_final_report.md")})
    _write_json(tmp_path / "bp_readability_review.json", {"verdict": "PASS"})
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": True, "gate_verdict": "PASS"})
    _write_json(tmp_path / "bp_debate_review.json", {"verdict": "PASS"})
    _write_json(tmp_path / "bp_cross_dimension_gate.json", {"ok": True, "gate_verdict": "PASS"})
    _write_json(tmp_path / "bp_verification_result.json", {"verdict": "PASS", "fail": 0})
    _write_json(tmp_path / "bp_thesis_reconciliation.json", {"schema_version": "bp_thesis_reconciliation.v1", "recommendation": "conditional_go", "unresolved_high_issues": [], "confidence": "medium"})
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_product_commercial",
                        "data_gaps": ["收入高速增长仅为 BP 自述，待验证"],
                        "claims": [{"claim_id": "BC001", "claim": "收入高速增长仅为 BP 自述，待验证", "confidence": "high", "source_quality": "bp", "fact_ids": ["BF-001"], "used_in_main_conclusion": False}],
                        "facts_used": ["BF-001"],
                    },
                }
            ]
        },
    )

    result = evaluate_bp_delivery_gate(tmp_path)

    reasons = {check["reason"] for check in result["failed_checks"]}
    assert "BP_ONLY_MAIN_CONCLUSION" not in reasons


def test_readability_gate_requires_first_page_and_chapter_decision_structure(tmp_path):
    report = tmp_path / "bp_final_report.md"
    report.write_text(
        "# 报告\n\n"
        "## 1. 投资结论\n\n当前建议：conditional_go\n\n"
        "## 2. 产品\n\n只有产品描述，没有 BP claim、外部 fact、反证/缺口和投资影响。\n",
        encoding="utf-8",
    )

    result = review_bp_readability(report)

    codes = {issue["code"] for issue in result["issues"]}
    assert result["verdict"] == "FAIL"
    assert "FIRST_PAGE_MISSING_DECISION_BLOCKS" in codes
    assert "CHAPTER_MISSING_DECISION_CHAIN" in codes


def test_readability_gate_passes_structured_decision_chain_report(tmp_path):
    report = tmp_path / "bp_final_report.md"
    report.write_text(
        "# 报告\n\n"
        "## 1. 投资结论\n\n**本章回答的问题：是否推进？**\n\n当前建议：conditional_go\n置信度：medium\n关键支持理由：技术壁垒有证据。\nDeal Breakers：客户合同待验证。\n下一步 DD：客户访谈。\nBP claim：核心产品可商业化。\n外部 fact：BF-001，https://example.com。\n反证/缺口：合同原件待补。\n投资影响：估值需打折。\n\n"
        "## 2. 关键证据链\n\n**本章回答的问题：证据是否支撑？**\n\nBP claim：客户存在。\n外部 fact：BF-002，https://example.com/customer。\n反证/缺口：仍需访谈。\n投资影响：决定是否进入下一轮。\n",
        encoding="utf-8",
    )

    result = review_bp_readability(report)

    assert result["verdict"] == "PASS"


def test_delivery_gate_blocks_bp_only_main_claim_and_missing_fact_store_references(tmp_path):
    (tmp_path / "bp_final_report.md").write_text("# 报告\n\n## 1. 投资结论\n\n当前建议：conditional_go\nBF-001\n", encoding="utf-8")
    _write_json(tmp_path / "bp_final_assembly.json", {"ok": True, "markdown_path": str(tmp_path / "bp_final_report.md")})
    _write_json(tmp_path / "bp_readability_review.json", {"verdict": "PASS"})
    _write_json(tmp_path / "bp_claim_coverage_gate.json", {"ok": True, "gate_verdict": "PASS"})
    _write_json(tmp_path / "bp_debate_review.json", {"verdict": "PASS"})
    _write_json(tmp_path / "bp_cross_dimension_gate.json", {"ok": True, "gate_verdict": "PASS"})
    _write_json(tmp_path / "bp_verification_result.json", {"verdict": "PASS", "fail": 0})
    _write_json(tmp_path / "bp_thesis_reconciliation.json", {"schema_version": "bp_thesis_reconciliation.v1", "recommendation": "conditional_go", "unresolved_high_issues": [], "confidence": "medium"})
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [
                {
                    "validation": {"passed": True},
                    "package": {
                        "section_id": "bp_valuation_return",
                            "claims": [{"claim_id": "BC001", "claim": "收入高速增长", "confidence": "high", "source_quality": "bp", "fact_ids": ["BF-001", "BF-002"], "used_in_main_conclusion": True}],
                    },
                }
            ]
        },
    )

    result = evaluate_bp_delivery_gate(tmp_path)

    reasons = {check["reason"] for check in result["failed_checks"]}
    assert result["ok"] is False
    assert "BP_ONLY_MAIN_CONCLUSION" in reasons
    assert "FINAL_REPORT_FACT_STORE_NOT_REFERENCED" in reasons
