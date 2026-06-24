import json

from scripts.bp_gap_repair_planner import plan_bp_gap_repairs


def test_coverage_failure_creates_repair_tasks(tmp_path):
    task_dir = tmp_path / "BP-REPAIR"
    task_dir.mkdir()
    (task_dir / "bp_claim_coverage_gate.json").write_text(
        json.dumps({"claims": [{"claim_id": "BC005", "owner_section": "bp_customer_revenue_validation", "status": "unverified", "blocking_gaps": ["missing customer evidence"]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_search_plan.json").write_text(
        json.dumps({"search_tasks": [{"search_task_id": "BST-001", "claim_id": "BC005", "owner_section": "bp_customer_revenue_validation", "queries": ["客户 订单"], "min_fetched_urls": 2}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = plan_bp_gap_repairs(task_dir)

    assert result["schema_version"] == "bp_gap_repair_plan.v1"
    assert result["tasks"][0]["repair_task_id"] == "BGR-001"
    assert result["tasks"][0]["claim_id"] == "BC005"
    assert result["tasks"][0]["owner_section"] == "bp_customer_revenue_validation"
    assert result["tasks"][0]["queries"] == ["客户 订单"]


def test_wave_gate_repair_tasks_are_carried_forward(tmp_path):
    task_dir = tmp_path / "BP-REPAIR"
    task_dir.mkdir()
    (task_dir / "bp_wave1_evidence_gate.json").write_text(
        json.dumps({"repair_tasks": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "reason": "CRITICAL_CLAIM_NOT_ADDRESSED"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = plan_bp_gap_repairs(task_dir)

    assert result["tasks"][0]["claim_id"] == "BC001"
    assert result["tasks"][0]["required_actions"] == ["search", "fetch", "write_fact", "update_section_package"]


def test_repair_plan_dedupes_claim_owner_pairs(tmp_path):
    task_dir = tmp_path / "BP-REPAIR"
    task_dir.mkdir()
    for name in ("bp_wave1_evidence_gate.json", "bp_claim_coverage_gate.json"):
        (task_dir / name).write_text(
            json.dumps({"repair_tasks": [{"claim_id": "BC001", "owner_section": "bp_company_team_compliance", "reason": "SAME"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

    result = plan_bp_gap_repairs(task_dir)

    assert len(result["tasks"]) == 1
