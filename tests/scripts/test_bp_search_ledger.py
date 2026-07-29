import json

from scripts.bp_search_ledger import append_search_event, load_search_ledger, summarize_search_coverage


def test_append_search_event_dedupes_urls_but_keeps_query_attempts(tmp_path):
    task_dir = tmp_path / "BP-LEDGER"
    event = {
        "search_task_id": "BST-001",
        "role": "bp_product_commercial",
        "claim_id": "BC005",
        "query": "测试公司 客户 订单",
        "engine": "web_search",
        "result_count": 5,
        "result_urls": ["https://a.example/r", "https://a.example/r"],
        "fetched_urls": ["https://a.example/r", "https://b.example/r"],
        "used_fact_ids": ["BF-001"],
    }

    append_search_event(task_dir, event)
    append_search_event(task_dir, dict(event, query="测试公司 招投标 中标"))

    ledger = load_search_ledger(task_dir)
    assert ledger["schema_version"] == "bp_search_ledger.v1"
    assert len(ledger["events"]) == 2
    assert ledger["events"][0]["result_urls"] == ["https://a.example/r"]
    assert ledger["events"][0]["fetched_urls"] == ["https://a.example/r", "https://b.example/r"]
    assert ledger["events"][0]["source_domains"] == ["a.example", "b.example"]


def test_summarize_search_coverage_counts_by_role_and_claim(tmp_path):
    task_dir = tmp_path / "BP-LEDGER"
    append_search_event(task_dir, {"search_task_id": "BST-001", "role": "bp_product_commercial", "claim_id": "BC005", "query": "q1", "fetched_urls": ["https://a.example/1"]})
    append_search_event(task_dir, {"search_task_id": "BST-001", "role": "bp_product_commercial", "claim_id": "BC005", "query": "q2", "fetched_urls": ["https://b.example/2"]})
    append_search_event(task_dir, {"search_task_id": "BST-002", "role": "bp_valuation_return", "claim_id": "BC007", "query": "q3", "fetched_urls": ["https://c.example/3"]})

    summary = summarize_search_coverage(task_dir)

    assert summary["audit_source"] == "central_ledger"
    assert summary["by_role"]["bp_product_commercial"]["unique_queries"] == 2
    assert summary["by_role"]["bp_product_commercial"]["fetched_url_count"] == 2
    assert summary["by_claim"]["BC005"]["source_domain_count"] == 2
    assert summary["by_claim"]["BC007"]["search_task_ids"] == ["BST-002"]


def test_missing_ledger_falls_back_to_section_search_audit(tmp_path):
    task_dir = tmp_path / "BP-LEDGER-FALLBACK"
    task_dir.mkdir()
    (task_dir / "bp_section_packages.json").write_text(
        json.dumps({
            "packages": [{
                "section_name": "bp_dim_product_commercial",
                "package": {
                    "section_id": "bp_product_commercial",
                    "search_audit": {
                        "claim_coverage": [{
                            "claim_id": "BC005",
                            "search_task_ids": ["BST-001"],
                            "unique_queries": 4,
                            "fetched_urls": ["https://a.example/1", "https://b.example/2"],
                            "source_domains": ["a.example", "b.example"],
                        }]
                    },
                },
            }]
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = summarize_search_coverage(task_dir)

    assert summary["audit_source"] == "agent_reported"
    assert summary["by_claim"]["BC005"]["unique_queries"] == 4
    assert summary["by_claim"]["BC005"]["source_domain_count"] == 2
