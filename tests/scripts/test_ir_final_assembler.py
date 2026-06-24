import json
from pathlib import Path

from scripts.ir_final_assembler import assemble_final_report, write_final_report


def test_assemble_final_report_uses_only_passed_packages():
    section_index = {
        "task_id": "TASK-TEST",
        "packages": [
            {
                "step_name": "step4_finance",
                "package": {
                    "section_id": "step4_finance",
                    "section_title": "财务质量",
                    "key_messages": ["利润率改善"],
                    "claims": [{"claim": "利润率改善", "fact_ids": ["F-0001"], "reasoning": "r", "confidence": "high", "source_quality": "official"}],
                    "facts_used": ["F-0001"],
                    "counter_evidence": ["收入增速偏弱"],
                    "data_gaps": [],
                    "markdown_draft": "财务质量章节正文。",
                },
                "validation": {"passed": True, "issues": []},
            },
            {
                "step_name": "step6b_valuation",
                "package": {"section_title": "估值", "markdown_draft": "不应进入成稿。"},
                "validation": {"passed": False, "issues": []},
            },
        ],
    }
    research_plan = {"entity": "任意公司", "objective": "形成投资研究报告"}
    debate_review = {"verdict": "PASS", "issues": []}

    result = assemble_final_report(research_plan, section_index, debate_review)

    assert result["ok"] is True
    assert "财务质量章节正文" in result["markdown"]
    assert "不应进入成稿" not in result["markdown"]
    assert result["facts_used"] == ["F-0001"]


def test_assemble_final_report_blocks_when_debate_requires_rewrite():
    result = assemble_final_report(
        {"entity": "任意公司"},
        {"task_id": "TASK-TEST", "packages": []},
        {"verdict": "REWRITE_REQUIRED", "issues": [{"code": "CLAIM_WITHOUT_FACTS"}]},
    )

    assert result["ok"] is False
    assert result["block_reason"] == "debate_review_not_passed"


def test_write_final_report_creates_markdown_and_json(tmp_path):
    (tmp_path / "TASK-TEST-research_plan.json").write_text(json.dumps({"entity": "任意公司", "objective": "形成投资研究报告"}), encoding="utf-8")
    (tmp_path / "TASK-TEST-section_packages.json").write_text(json.dumps({
        "task_id": "TASK-TEST",
        "packages": [{
            "step_name": "step4_finance",
            "package": {
                "section_id": "step4_finance",
                "section_title": "财务质量",
                "key_messages": ["k"],
                "claims": [{"claim": "c", "fact_ids": ["F-0001"], "reasoning": "r", "confidence": "high", "source_quality": "official"}],
                "facts_used": ["F-0001"],
                "counter_evidence": ["risk"],
                "data_gaps": [],
                "markdown_draft": "正文。",
            },
            "validation": {"passed": True, "issues": []},
        }],
    }), encoding="utf-8")
    (tmp_path / "TASK-TEST-debate_review.json").write_text(json.dumps({"verdict": "PASS", "issues": []}), encoding="utf-8")

    output = write_final_report("TASK-TEST", tasks_dir=tmp_path)
    payload = json.loads(Path(output).read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert (tmp_path / "TASK-TEST-final_report.md").exists()
