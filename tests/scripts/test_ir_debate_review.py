import json
from pathlib import Path

from scripts.ir_debate_review import run_debate_review, write_debate_review


def test_debate_review_passes_when_packages_have_claims_facts_and_counter_evidence():
    section_index = {
        "task_id": "TASK-TEST",
        "summary": {"total": 1, "passed": 1, "failed": 0},
        "packages": [
            {
                "step_name": "step4_finance",
                "package": {
                    "section_id": "step4_finance",
                    "section_title": "财务质量",
                    "key_messages": ["利润率改善"],
                    "claims": [{"claim": "利润率改善", "fact_ids": ["F-0001"], "reasoning": "费用率下降", "confidence": "high", "source_quality": "official"}],
                    "facts_used": ["F-0001"],
                    "counter_evidence": ["收入增速偏弱"],
                    "data_gaps": [],
                    "markdown_draft": "利润率改善。",
                },
                "validation": {"passed": True, "issues": []},
            }
        ],
    }

    review = run_debate_review(section_index)
    assert review["verdict"] == "PASS"
    assert review["issues"] == []


def test_debate_review_requires_rewrite_for_failed_package_and_missing_counter_evidence():
    section_index = {
        "task_id": "TASK-TEST",
        "summary": {"total": 1, "passed": 0, "failed": 1},
        "packages": [
            {
                "step_name": "step6b_valuation",
                "package": {
                    "section_id": "step6b_valuation",
                    "section_title": "估值",
                    "key_messages": ["目标价上行"],
                    "claims": [{"claim": "目标价上行", "fact_ids": [], "reasoning": "", "confidence": "high", "source_quality": "unknown"}],
                    "facts_used": [],
                    "counter_evidence": [],
                    "data_gaps": [],
                    "markdown_draft": "目标价上行。",
                },
                "validation": {"passed": False, "issues": [{"severity": "FAIL", "code": "CLAIM_WITHOUT_FACTS", "message": "Claim has no facts"}]},
            }
        ],
    }

    review = run_debate_review(section_index)
    assert review["verdict"] == "REWRITE_REQUIRED"
    assert any(issue["section"] == "step6b_valuation" for issue in review["issues"])
    assert any(issue["code"] == "CLAIM_WITHOUT_FACTS" for issue in review["issues"])


def test_write_debate_review_reads_section_index_and_writes_json(tmp_path):
    section_index = {
        "task_id": "TASK-TEST",
        "summary": {"total": 0, "passed": 0, "failed": 0},
        "packages": [],
    }
    (tmp_path / "TASK-TEST-section_packages.json").write_text(json.dumps(section_index), encoding="utf-8")

    output = write_debate_review("TASK-TEST", tasks_dir=tmp_path)
    payload = json.loads(Path(output).read_text(encoding="utf-8"))
    assert payload["task_id"] == "TASK-TEST"
    assert payload["verdict"] == "REWRITE_REQUIRED"
    assert any(issue["code"] == "NO_SECTION_PACKAGES" for issue in payload["issues"])
