import json

from scripts.bp_search_plan_compiler import compile_bp_search_plan, write_bp_search_plan


def _research_plan():
    return {
        "schema_version": "bp_research_plan.v2",
        "task_id": "BP-SEARCH",
        "entity": "测试公司",
        "core_questions": [
            {
                "question_id": "BQ2",
                "question": "客户收入是否可验证？",
                "owner_section": "bp_customer_revenue_validation",
                "priority": "critical",
                "required_fact_keys": ["customer_evidence", "revenue_evidence", "order_evidence"],
            },
            {
                "question_id": "BQ6",
                "question": "估值是否成立？",
                "owner_section": "bp_valuation_return",
                "priority": "high",
                "required_fact_keys": ["financing_terms", "valuation_multiples", "return_model"],
            },
        ],
        "strategic_questions": [],
        "claim_matrix": [
            {
                "claim_id": "BC005",
                "claim": "测试公司的客户、订单、收入可以被独立验证。",
                "owner_section": "bp_customer_revenue_validation",
                "priority": "critical",
                "required_fact_keys": ["customer_evidence", "revenue_evidence"],
            },
            {
                "claim_id": "BC007",
                "claim": "测试公司的融资估值支持目标回报。",
                "owner_section": "bp_valuation_return",
                "priority": "high",
                "required_fact_keys": ["financing_terms", "valuation_multiples", "return_model"],
            },
        ],
        "fact_requirements": [
            {
                "fact_key": "customer_evidence",
                "source_priority": ["customer_or_partner_disclosure", "public_tender", "reputable_media"],
                "criticality": "critical",
            },
            {
                "fact_key": "revenue_evidence",
                "source_priority": ["regulatory", "customer_or_partner_disclosure", "reputable_media"],
                "criticality": "critical",
            },
            {
                "fact_key": "valuation_multiples",
                "source_priority": ["listed_peer_filings", "market_database"],
                "criticality": "high",
            },
        ],
    }


def test_critical_customer_revenue_claim_generates_deep_search_work_order():
    plan = compile_bp_search_plan(_research_plan(), profile={"company_name": "测试公司"}, company_verify={})

    task = next(item for item in plan["search_tasks"] if item["claim_id"] == "BC005")

    assert plan["schema_version"] == "bp_search_plan.v1"
    assert task["search_task_id"] == "BST-001"
    assert task["owner_section"] == "bp_customer_revenue_validation"
    assert task["priority"] == "critical"
    assert len(task["queries"]) >= 4
    assert task["min_unique_queries"] >= 4
    assert task["min_fetched_urls"] >= 2
    assert task["min_independent_domains"] >= 2
    assert task["requires_counter_search"] is True
    assert "customer_or_partner_disclosure" in task["required_source_tiers"]


def test_valuation_claim_depends_on_verified_customer_revenue_facts():
    plan = compile_bp_search_plan(_research_plan(), profile={"company_name": "测试公司"}, company_verify={})

    task = next(item for item in plan["search_tasks"] if item["claim_id"] == "BC007")

    assert task["owner_section"] == "bp_valuation_return"
    assert "customer_evidence" in task["depends_on_fact_keys"]
    assert "revenue_evidence" in task["depends_on_fact_keys"]
    assert task["bp_only_support_policy"] == "bp_only_cannot_support_main_conclusion"


def test_owner_section_search_plan_slice_contains_only_relevant_tasks():
    plan = compile_bp_search_plan(_research_plan(), profile={"company_name": "测试公司"}, company_verify={})

    slices = plan["owner_section_index"]

    assert slices["bp_customer_revenue_validation"] == ["BST-001"]
    assert slices["bp_valuation_return"] == ["BST-002"]


def test_write_bp_search_plan_persists_json(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-SEARCH"
    payload = compile_bp_search_plan(_research_plan(), profile={"company_name": "测试公司"}, company_verify={})

    path = write_bp_search_plan(task_dir, payload)

    assert path.name == "bp_search_plan.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["search_tasks"][0]["search_task_id"] == "BST-001"
