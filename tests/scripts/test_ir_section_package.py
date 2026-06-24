import json
from pathlib import Path

from scripts.ir_section_package import extract_section_package, validate_section_package, write_section_package_index


def test_extract_section_package_from_markdown_json_block():
    text = """
## 财务分析

```json
{
  "schema_version": "ir_section_package.v1",
  "section_id": "step4_finance",
  "section_title": "财务质量",
  "key_messages": ["利润率改善需要来源验证"],
  "claims": [
    {
      "claim": "利润率改善",
      "fact_ids": ["F-0001"],
      "reasoning": "费用率下降带来改善",
      "confidence": "medium",
      "source_quality": "official"
    }
  ],
  "facts_used": ["F-0001"],
  "counter_evidence": ["收入增速仍偏弱"],
  "data_gaps": [],
  "markdown_draft": "利润率改善，但仍需观察收入增速。"
}
```
"""
    package = extract_section_package(text)
    assert package["section_id"] == "step4_finance"
    result = validate_section_package(package)
    assert result["passed"] is True
    assert result["issues"] == []


def test_validate_section_package_fails_missing_claim_fact_ids():
    package = {
        "schema_version": "ir_section_package.v1",
        "section_id": "step4_finance",
        "section_title": "财务质量",
        "key_messages": ["利润率改善"],
        "claims": [{"claim": "利润率改善", "fact_ids": [], "reasoning": "", "confidence": "high", "source_quality": "official"}],
        "facts_used": [],
        "counter_evidence": [],
        "data_gaps": [],
        "markdown_draft": "利润率改善。",
    }

    result = validate_section_package(package)
    assert result["passed"] is False
    assert any(issue["code"] == "CLAIM_WITHOUT_FACTS" for issue in result["issues"])
    assert any(issue["code"] == "MISSING_COUNTER_EVIDENCE" for issue in result["issues"])


def test_write_section_package_index_collects_step_packages(tmp_path):
    step_file = tmp_path / "TASK-TEST-step4_finance.md"
    step_file.write_text(
        """```json
{"schema_version":"ir_section_package.v1","section_id":"step4_finance","section_title":"财务质量","key_messages":["k"],"claims":[{"claim":"c","fact_ids":["F-0001"],"reasoning":"r","confidence":"high","source_quality":"official"}],"facts_used":["F-0001"],"counter_evidence":["risk"],"data_gaps":[],"markdown_draft":"draft"}
```""",
        encoding="utf-8",
    )

    output = write_section_package_index("TASK-TEST", tasks_dir=tmp_path)
    payload = json.loads(Path(output).read_text(encoding="utf-8"))
    assert payload["task_id"] == "TASK-TEST"
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["passed"] == 1


def test_write_section_package_index_prefers_sidecar_json_over_markdown(tmp_path):
    step_file = tmp_path / "TASK-SIDECAR-step4_finance.md"
    step_file.write_text(
        """```json
{"section_id":"legacy_bad"}
```""",
        encoding="utf-8",
    )
    sidecar_file = tmp_path / "TASK-SIDECAR-step4_finance-section.json"
    sidecar_file.write_text(
        json.dumps(
            {
                "schema_version": "ir_section_package.v1",
                "section_id": "step4_finance",
                "section_title": "财务质量",
                "key_messages": ["k"],
                "claims": [
                    {
                        "claim": "c",
                        "fact_ids": ["F-0001"],
                        "reasoning": "r",
                        "confidence": "high",
                        "source_quality": "official",
                    }
                ],
                "facts_used": ["F-0001"],
                "counter_evidence": ["risk"],
                "data_gaps": [],
                "markdown_draft": "draft",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = write_section_package_index("TASK-SIDECAR", tasks_dir=tmp_path)
    payload = json.loads(Path(output).read_text(encoding="utf-8"))

    assert payload["summary"]["passed"] == 1
    assert payload["packages"][0]["package"]["section_id"] == "step4_finance"
    assert payload["packages"][0]["package_source"] == str(sidecar_file)


def test_validate_section_package_requires_schema_version():
    package = {
        "section_id": "step4_finance",
        "section_title": "财务质量",
        "key_messages": ["k"],
        "claims": [{"claim": "c", "fact_ids": ["F-0001"], "reasoning": "r", "confidence": "high", "source_quality": "official"}],
        "facts_used": ["F-0001"],
        "counter_evidence": ["risk"],
        "data_gaps": [],
        "markdown_draft": "draft",
    }

    result = validate_section_package(package)

    assert result["passed"] is False
    assert any(issue["code"] == "MISSING_SCHEMA_VERSION" for issue in result["issues"])
