from __future__ import annotations

import json

from scripts.ir_quality_gate import run_report_gate, run_section_gate, run_step_gate

_VALID_URLS = "\nhttps://example.com/a\nhttps://example.com/b\nhttps://example.com/c\n"


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

def test_run_report_gate_fails_rr_inconsistent_with_own_inputs(tmp_path):
    """R/R 声称值与报告自给上下行输入矛盾 → FAIL（2026-08-06 中天复盘）。"""
    task_id = "TASK-RR-INCONSISTENT"
    report = (
        "# 研报\n"
        "现价 32.00 元，目标价 49 元。概率加权目标价 43.9 元"
        "（牛 20%/61、基准 45%/49、熊 28%/31、极端 7%/14），"
        "R/R ≈ 8.2–8.7:1（以熊市下沿 28 元为下行、base 49 为上行），"
        "即下行 28 / 上行 49。\n" + _VALID_URLS
    )
    (tmp_path / f"{task_id}-final_report.md").write_text(report, encoding="utf-8")
    result = run_report_gate(task_id, tasks_dir=tmp_path)
    assert result["passed"] is False
    assert any(issue["code"] == "NUMBER_INCONSISTENT_RR" for issue in result["issues"])


def test_run_report_gate_fails_weighted_target_price_inconsistent(tmp_path):
    """概率加权目标价按自给情景概率复算偏差 >10% → FAIL。"""
    task_id = "TASK-WEIGHTED-INCONSISTENT"
    report = (
        "# 研报\n现价 32.00 元。概率加权目标价 60.0 元"
        "（牛 50%/50、熊 50%/10），上行空间显著。\n" + _VALID_URLS
    )
    (tmp_path / f"{task_id}-final_report.md").write_text(report, encoding="utf-8")
    result = run_report_gate(task_id, tasks_dir=tmp_path)
    assert result["passed"] is False
    assert any(issue["code"] == "NUMBER_INCONSISTENT" for issue in result["issues"])


def test_run_report_gate_passes_when_numbers_consistent(tmp_path):
    """数字自洽时不因一致性校验误伤（WARN 不阻断）。"""
    task_id = "TASK-NUMBERS-CONSISTENT"
    # 加权复算：0.5*50 + 0.5*10 = 30，与声称 30 一致；R/R：(50-30)/(30-10)=1.0，声称 1.0:1 一致
    report = (
        "# 研报\n现价 30.00 元。概率加权目标价 30.0 元"
        "（牛 50%/50、熊 50%/10）。R/R ≈ 1.0:1（下行 10 / 上行 50）。\n" + _VALID_URLS
    )
    (tmp_path / f"{task_id}-final_report.md").write_text(report, encoding="utf-8")
    result = run_report_gate(task_id, tasks_dir=tmp_path)
    assert result["passed"] is True
    assert not any(issue["code"] in ("NUMBER_INCONSISTENT", "NUMBER_INCONSISTENT_RR")
                   for issue in result["issues"])
