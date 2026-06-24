import json

from scripts.bp_claim_coverage_validator import evaluate_bp_claim_coverage, write_bp_claim_coverage_gate


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_coverage_validator_fails_critical_or_high_not_addressed_and_contradicted(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "已有大客户订单", "priority": "critical", "status": "not_addressed"},
                {"claim_id": "BC002", "claim": "已实现量产", "priority": "HIGH", "status": "CONTRADICTED"},
                {"claim_id": "BC003", "claim": "补充信息", "priority": "medium", "status": "not_addressed"},
            ],
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    assert result["schema_version"] == "bp_claim_coverage_gate.v1"
    assert result["ok"] is False
    assert result["gate_verdict"] == "FAIL"
    assert result["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"
    assert [claim["claim_id"] for claim in result["failed_claims"]] == ["BC001", "BC002"]
    assert result["summary"]["failed"] == 2


def test_coverage_validator_blocks_critical_unverified_even_when_data_gap_is_disclosed(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {
                    "claim_id": "BC001",
                    "claim": "客户合同待验证",
                    "priority": "critical",
                    "status": "unverified",
                    "data_gaps": ["需创始人提供客户合同原件"],
                }
            ],
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    assert result["ok"] is False
    assert result["gate_verdict"] == "FAIL"
    assert result["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"
    assert [claim["claim_id"] for claim in result["failed_claims"]] == ["BC001"]
    assert result["summary"]["failed"] == 1


def test_coverage_validator_passes_when_all_claims_are_covered(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "主体信息已核验", "priority": "critical", "status": "supported", "fact_ids": ["BF-0001"]},
                {"claim_id": "BC002", "claim": "技术路线有外部佐证", "priority": "high", "status": "partially_supported", "fact_ids": ["BF-0002"]},
            ],
        },
    )

    result = write_bp_claim_coverage_gate(tmp_path)

    assert result["ok"] is True
    assert result["gate_verdict"] == "PASS"
    gate_payload = json.loads((tmp_path / "bp_claim_coverage_gate.json").read_text(encoding="utf-8"))
    assert gate_payload["gate_verdict"] == "PASS"
    assert gate_payload["summary"]["total"] == 2


def test_coverage_validator_initializes_missing_coverage_from_claim_inventory(tmp_path):
    _write_json(
        tmp_path / "bp_claim_inventory.json",
        {
            "schema_version": "bp_claim_inventory.v1",
            "task_id": "BP-CLAIMS",
            "entity": "测试公司",
            "claims": [
                {"claim_id": "BC010", "claim": "团队来自头部机构", "owner_section": "bp_团队与合规", "priority": "high", "source": "bp_text"}
            ],
        },
    )

    result = write_bp_claim_coverage_gate(tmp_path)

    coverage = json.loads((tmp_path / "bp_claim_coverage.json").read_text(encoding="utf-8"))
    assert coverage["schema_version"] == "bp_claim_coverage.v1"
    assert coverage["claims"][0]["claim_id"] == "BC010"
    assert coverage["claims"][0]["status"] == "not_addressed"
    assert result["gate_verdict"] == "FAIL"
    assert result["failed_claims"][0]["claim_id"] == "BC010"


def test_coverage_validator_initializes_missing_coverage_from_research_plan_when_inventory_absent(tmp_path):
    _write_json(
        tmp_path / "bp_research_plan.json",
        {
            "schema_version": "bp_research_plan.v2",
            "task_id": "BP-PLAN",
            "entity": "测试公司",
            "claim_matrix": [
                {"claim_id": "BC001", "claim": "产品已进入量产", "owner_section": "bp_product_commercial", "priority": "critical"}
            ],
        },
    )

    result = write_bp_claim_coverage_gate(tmp_path)

    coverage = json.loads((tmp_path / "bp_claim_coverage.json").read_text(encoding="utf-8"))
    assert coverage["task_id"] == "BP-PLAN"
    assert coverage["entity"] == "测试公司"
    assert coverage["summary"]["critical_not_addressed"] == 1
    assert result["ok"] is False
    assert result["block_reason"] == "CRITICAL_CLAIM_NOT_ADDRESSED"


def test_coverage_validator_keeps_bp_only_fact_unverified(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "已有客户订单", "priority": "critical", "status": "supported", "fact_ids": ["BF-BP"]}
            ],
        },
    )
    _write_json(
        tmp_path / "bp_fact_store.json",
        {
            "facts": [
                {"fact_id": "BF-BP", "source_tier": "bp", "source_url": "bp://deck", "confidence": "high"}
            ]
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    claim = result["coverage"]["claims"][0]
    assert claim["status"] == "unverified"
    assert claim["evidence_strength"] == "bp_only"
    assert "BP_ONLY_EVIDENCE" in claim["blocking_gaps"]
    assert result["gate_verdict"] == "FAIL"



def test_coverage_validator_supports_claim_with_official_source(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "主体信息已核验", "priority": "critical", "status": "unverified", "fact_ids": ["BF-OFFICIAL"]}
            ],
        },
    )
    _write_json(
        tmp_path / "bp_fact_store.json",
        {
            "facts": [
                {"fact_id": "BF-OFFICIAL", "source_tier": "official", "source_url": "https://official.example/company", "confidence": "high"}
            ]
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    claim = result["coverage"]["claims"][0]
    assert claim["status"] == "supported"
    assert claim["evidence_strength"] == "authoritative"
    assert claim["source_domain_count"] == 1
    assert result["ok"] is True



def test_coverage_validator_marks_two_media_domains_as_cross_verified(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "技术路线有外部佐证", "priority": "high", "status": "supported", "fact_ids": ["BF-M1", "BF-M2"]}
            ],
        },
    )
    _write_json(
        tmp_path / "bp_fact_store.json",
        {
            "facts": [
                {"fact_id": "BF-M1", "source_tier": "media", "source_url": "https://a.example/report", "confidence": "medium"},
                {"fact_id": "BF-M2", "source_tier": "media", "source_url": "https://b.example/report", "confidence": "medium"},
            ]
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    claim = result["coverage"]["claims"][0]
    assert claim["status"] == "supported"
    assert claim["evidence_strength"] == "cross_verified"
    assert claim["source_domain_count"] == 2



def test_coverage_validator_contradiction_overrides_supported(tmp_path):
    _write_json(
        tmp_path / "bp_claim_coverage.json",
        {
            "schema_version": "bp_claim_coverage.v1",
            "claims": [
                {"claim_id": "BC001", "claim": "已有客户订单", "priority": "critical", "status": "supported", "fact_ids": ["BF-OFFICIAL"], "counter_evidence": ["客户官网显示合作已终止"]}
            ],
        },
    )
    _write_json(
        tmp_path / "bp_fact_store.json",
        {
            "facts": [
                {"fact_id": "BF-OFFICIAL", "source_tier": "official", "source_url": "https://official.example/company", "confidence": "high"}
            ]
        },
    )

    result = evaluate_bp_claim_coverage(tmp_path)

    claim = result["coverage"]["claims"][0]
    assert claim["status"] == "contradicted"
    assert "COUNTER_EVIDENCE_PRESENT" in claim["blocking_gaps"]
    assert result["gate_verdict"] == "FAIL"



def test_coverage_validator_fails_when_no_claim_sources_exist(tmp_path):
    result = write_bp_claim_coverage_gate(tmp_path)

    coverage = json.loads((tmp_path / "bp_claim_coverage.json").read_text(encoding="utf-8"))
    assert coverage["claims"] == []
    assert result["ok"] is False
    assert result["gate_verdict"] == "FAIL"
    assert result["block_reason"] == "CLAIM_INVENTORY_MISSING"
    assert result["summary"]["total"] == 0
