import json

import scripts.ir_extract_content as extract_content


def test_extract_from_presearch_collects_bare_urls_and_records_skip_reason(tmp_path, monkeypatch):
    task_id = "TASK-EXTRACT-URLS"
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / f"{task_id}-search-step1.md").write_text(
        "资料来源：https://example.com/report?x=1 以及 https://example.com/short。",
        encoding="utf-8",
    )

    monkeypatch.setattr(extract_content, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(extract_content, "_fetch_text", lambda url: ("短标题", "太短"))

    result = extract_content.extract_from_presearch(task_id, entity="测试公司", max_pages=5)

    assert result["total_urls"] == 2
    assert result["ok_count"] == 0
    assert result["results"][0]["status"] == "too_short"
    assert result["results"][0]["reason"] == "fetched_text_length_lt_200"
    assert result["results"][0]["text_length"] == 2
    assert result["results"][0]["title"] == "短标题"
    facts = json.loads((tasks_dir / f"{task_id}_body_content" / "ir_extracted_facts.json").read_text(encoding="utf-8"))
    assert facts["skip_summary"] == {"too_short": 2}
