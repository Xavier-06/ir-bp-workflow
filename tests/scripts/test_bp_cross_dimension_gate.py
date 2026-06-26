import json

from scripts.bp_cross_dimension_gate import evaluate_bp_cross_dimension_gate


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_cross_gate_detects_valuation_depends_on_unsupported_revenue(tmp_path):
    """Valuation claim references an unsupported revenue claim via fact_ids → detected as WARN."""
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [{
                "validation": {"passed": True},
                "package": {
                    "section_id": "bp_valuation_return",
                    "claims": [{
                        "claim_id": "BC_VAL",
                        "claim": "估值模型使用客户订单和收入作为核心假设",
                        "fact_ids": ["BF-REV"],
                        "source_quality": "official",
                    }],
                },
            }]
        },
    )
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "claims": [
                {"claim_id": "BC_REV", "claim": "客户收入已验证", "owner_section": "bp_customer_revenue_validation", "priority": "critical", "status": "unverified", "fact_ids": ["BF-REV"]}
            ],
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    # HIGH issues are downgraded to WARN (non-dealbreaker) → PASS
    assert result["gate_verdict"] == "PASS"
    assert any("VALUATION_DEPENDS_ON_UNSUPPORTED_REVENUE" in issue["code"] for issue in result["issues"])


def test_cross_gate_detects_competitor_superiority_without_coverage(tmp_path):
    """Competition claim asserts superiority without search coverage → detected as WARN."""
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [{
                "validation": {"passed": True},
                "package": {
                    "section_id": "bp_competition_positioning",
                    "claims": [{
                        "claim_id": "BC_COMP",
                        "claim": "公司产品明显领先所有竞品",
                        "fact_ids": ["BF-COMP"],
                        "source_quality": "media",
                    }],
                    "search_audit": {"claim_coverage": []},
                },
            }]
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    # HIGH issues downgraded to WARN → PASS
    assert result["gate_verdict"] == "PASS"
    assert any(issue["code"] == "COMPETITOR_SUPERIORITY_WITHOUT_COVERAGE" for issue in result["issues"])


def test_cross_gate_detects_high_confidence_weak_source(tmp_path):
    """High confidence claim with weak source → detected as WARN."""
    _write_json(
        tmp_path / "bp_section_packages.json",
        {
            "packages": [{
                "validation": {"passed": True},
                "package": {
                    "section_id": "bp_product_commercial",
                    "claims": [{
                        "claim_id": "BC_PROD",
                        "claim": "产品已被市场充分验证",
                        "fact_ids": ["BF-PROD"],
                        "confidence": "high",
                        "source_quality": "media",
                    }],
                },
            }]
        },
    )

    result = evaluate_bp_cross_dimension_gate(tmp_path)

    # HIGH issues downgraded to WARN → PASS
    assert result["gate_verdict"] == "PASS"
    assert any(issue["code"] == "HIGH_CONFIDENCE_WEAK_SOURCE" for issue in result["issues"])
