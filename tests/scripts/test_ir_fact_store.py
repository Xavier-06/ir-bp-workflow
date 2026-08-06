import json
from pathlib import Path

from scripts.ir_fact_store import (
    FactStore,
    add_fact,
    extract_fact_candidates,
    merge_step_fact_sidecars,
    write_fact_store,
)


def test_add_fact_creates_generic_fact_without_company_hardcoding():
    store = FactStore(task_id="TASK-TEST", entity="任意公司", market="generic")
    fact = add_fact(
        store,
        claim="任意公司 FY2024 revenue was 100亿元",
        value="100",
        unit="亿元",
        period="FY2024",
        source_url="https://example.com/annual-report",
        source_tier="official",
        source_quote="Revenue was 100亿元",
        question_id="Q1",
        fact_type="financial",
    )

    assert fact.fact_id.startswith("F-")
    assert fact.entity == "任意公司"
    assert fact.source_tier == "official"
    assert store.facts[0].claim == "任意公司 FY2024 revenue was 100亿元"


def test_extract_fact_candidates_finds_numbers_and_urls_generically():
    text = "公司收入达到100亿元，同比增长12.5%，来源：https://example.com/report"
    candidates = extract_fact_candidates(text, entity="测试公司")

    values = {c["value"] for c in candidates}
    assert "100亿元" in values
    assert "12.5%" in values
    assert all(c["entity"] == "测试公司" for c in candidates)
    assert all(c["source_url"] == "https://example.com/report" for c in candidates)


def test_write_fact_store_creates_json(tmp_path):
    store = FactStore(task_id="TASK-TEST", entity="测试公司", market="cn")
    add_fact(
        store,
        claim="测试公司收入100亿元",
        value="100",
        unit="亿元",
        period="FY2024",
        source_url="https://example.com/report",
        source_tier="official",
        source_quote="收入100亿元",
        question_id="Q1",
        fact_type="financial",
    )

    output = write_fact_store(store, tasks_dir=tmp_path)
    payload = json.loads(Path(output).read_text(encoding="utf-8"))
    assert payload["task_id"] == "TASK-TEST"
    assert payload["entity"] == "测试公司"
    assert len(payload["facts"]) == 1


def test_merge_step_fact_sidecars_dedupes_and_writes_index(tmp_path):
    bootstrap = FactStore(task_id="TASK-MERGE", entity="测试公司", market="cn")
    add_fact(
        bootstrap,
        claim="测试公司收入100亿元",
        value="100亿元",
        unit="亿元",
        period="FY2024",
        source_url="https://example.com/report",
        source_tier="official",
        source_quote="收入100亿元",
        question_id="Q1",
        fact_type="financial",
    )
    write_fact_store(bootstrap, tasks_dir=tmp_path)

    sidecar = {
        "task_id": "TASK-MERGE",
        "step": "step4_finance",
        "facts": [
            {
                "fact_id": "step4_finance.rev_001",
                "claim": "测试公司收入100亿元",
                "value": "100亿元",
                "unit": "亿元",
                "period": "FY2024",
                "source_url": "https://example.com/report",
                "source_tier": "official",
                "source_quote": "收入100亿元",
                "question_id": "Q1",
                "fact_type": "financial",
                "confidence": "high",
            },
            {
                "fact_id": "step4_finance.margin_001",
                "claim": "测试公司毛利率达到35%",
                "value": "35%",
                "unit": "%",
                "period": "FY2024",
                "source_url": "https://example.com/margin",
                "source_tier": "official",
                "source_quote": "毛利率35%",
                "question_id": "Q2",
                "fact_type": "financial",
                "confidence": "high",
            },
        ],
    }
    (tmp_path / "TASK-MERGE-step4_finance-facts.json").write_text(
        json.dumps(sidecar, ensure_ascii=False),
        encoding="utf-8",
    )

    result = merge_step_fact_sidecars("TASK-MERGE", tasks_dir=tmp_path)

    fact_store = json.loads((tmp_path / "TASK-MERGE-fact_store.json").read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "TASK-MERGE-fact_store_index.json").read_text(encoding="utf-8"))

    assert result["merged_count"] == 1
    assert result["duplicate_count"] == 1
    assert len(fact_store["facts"]) == 2
    assert index["total_facts"] == 2
    assert "step4_finance.margin_001" in index["fact_ids"]

    second_result = merge_step_fact_sidecars("TASK-MERGE", tasks_dir=tmp_path)
    second_fact_store = json.loads((tmp_path / "TASK-MERGE-fact_store.json").read_text(encoding="utf-8"))
    assert second_result["merged_count"] == 0
    assert second_result["duplicate_count"] == 2
    assert len(second_fact_store["facts"]) == 2


def test_merge_step_fact_sidecars_rejects_missing_required_traceability_fields(tmp_path):
    store = FactStore(task_id="TASK-BAD", entity="测试公司", market="cn")
    write_fact_store(store, tasks_dir=tmp_path)
    bad_sidecar = {
        "task_id": "TASK-BAD",
        "step": "step4_finance",
        "facts": [
            {
                "fact_id": "step4_finance.bad_001",
                "claim": "",
                "value": "35%",
                "source_url": "https://example.com/margin",
                "source_quote": "毛利率35%",
            },
            {
                "fact_id": "step4_finance.bad_002",
                "claim": "测试公司毛利率达到35%",
                "value": "",
                "source_url": "https://example.com/margin",
                "source_quote": "毛利率35%",
            },
            {
                "fact_id": "step4_finance.bad_003",
                "claim": "测试公司毛利率达到35%",
                "value": "35%",
                "source_url": "https://example.com/margin",
                "source_quote": "",
            },
        ],
    }
    (tmp_path / "TASK-BAD-step4_finance-facts.json").write_text(
        json.dumps(bad_sidecar, ensure_ascii=False),
        encoding="utf-8",
    )

    result = merge_step_fact_sidecars("TASK-BAD", tasks_dir=tmp_path)
    fact_store = json.loads((tmp_path / "TASK-BAD-fact_store.json").read_text(encoding="utf-8"))

    # v3.6 变更（2026-08-06）：归一化器现在用真实 source_url 回填空的 source_quote
    # （source_quote_from_url，真实出处非编造）。因此 bad_003（有 source_url 但 source_quote
    # 为空）不再被拒收，而是回填后合并。仍拒收的是真正缺字段的 bad_001（空 claim）
    # 和 bad_002（claim 有数字但 value 为空）。
    assert result["merged_count"] == 1
    assert result["invalid_count"] == 2
    assert len(fact_store["facts"]) == 1
    assert fact_store["facts"][0]["fact_id"] == "step4_finance.bad_003"
    assert fact_store["facts"][0]["source_quote"] == "https://example.com/margin"
