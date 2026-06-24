from __future__ import annotations

import json

from scripts.ir_quality_gate import run_report_gate, run_section_gate, run_step_gate


def test_run_step_gate_requires_markdown_facts_and_section_sidecars(tmp_path):
    task_id = "TASK-GATE"
    (tmp_path / f"{task_id}-step4_finance.md").write_text(
        "## 财务分析\n" + "正文" * 200 + "\nhttps://example.com/a\nhttps://example.com/b\nhttps://example.com/c\n",
        encoding="utf-8",
    )
    (tmp_path / f"{task_id}-step4_finance-facts.json").write_text(
        json.dumps({"task_id": task_id, "step": "step4_finance", "facts": []}),
        encoding="utf-8",
    )

    result = run_step_gate(task_id, step_order=["step4_finance"], tasks_dir=tmp_path)

    assert result["passed"] is False
    assert result["steps"]["step4_finance"]["markdown_exists"] is True
    assert any(issue["code"] == "MISSING_SECTION_SIDECAR" for issue in result["issues"])


def test_run_section_gate_fails_when_required_inputs_missing(tmp_path):
    result = run_section_gate("TASK-MISSING", tasks_dir=tmp_path)

    assert result["passed"] is False
    assert any(issue["code"] == "MISSING_FACT_STORE_INDEX" for issue in result["issues"])
    assert any(issue["code"] == "MISSING_SECTION_PACKAGES" for issue in result["issues"])


def test_run_section_gate_fails_claim_referencing_missing_fact_id(tmp_path):
    task_id = "TASK-SECTION-GATE"
    (tmp_path / f"{task_id}-fact_store_index.json").write_text(
        json.dumps({"task_id": task_id, "fact_ids": ["F-0001"], "total_facts": 1}),
        encoding="utf-8",
    )
    (tmp_path / f"{task_id}-section_packages.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "summary": {"total": 1, "passed": 1, "failed": 0},
                "packages": [
                    {
                        "step_name": "step4_finance",
                        "validation": {"passed": True, "issues": []},
                        "package": {
                            "claims": [
                                {"claim": "c", "fact_ids": ["F-404"], "confidence": "high", "source_quality": "official"}
                            ],
                            "counter_evidence": ["risk"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_section_gate(task_id, tasks_dir=tmp_path)

    assert result["passed"] is False
    assert any(issue["code"] == "UNKNOWN_FACT_ID" for issue in result["issues"])


def test_run_report_gate_fails_placeholders_and_missing_sources(tmp_path):
    task_id = "TASK-REPORT-GATE"
    (tmp_path / f"{task_id}-final_report.md").write_text(
        "# 研报\n这里还有 TODO 和 待补。\n",
        encoding="utf-8",
    )

    result = run_report_gate(task_id, tasks_dir=tmp_path)

    assert result["passed"] is False
    assert any(issue["code"] == "REPORT_RED_FLAGS" for issue in result["issues"])
    assert any(issue["code"] == "REPORT_SOURCE_INSUFFICIENT" for issue in result["issues"])
