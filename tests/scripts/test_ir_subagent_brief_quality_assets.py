from pathlib import Path

import scripts.ir_subagent_launcher_wb as launcher


def test_build_step_brief_injects_shared_protocol_and_asset_paths(tmp_path, monkeypatch):
    protocol_dir = tmp_path / "instruction_store_ir"
    protocol_dir.mkdir()
    (protocol_dir / "index.json").write_text(
        '{"pipeline_bindings":{"ir":{"step4_finance":"finance_role"}}}',
        encoding="utf-8",
    )
    (protocol_dir / "finance_role.md").write_text("# Finance role instruction", encoding="utf-8")
    (protocol_dir / "_shared_output_protocol.md").write_text("# Shared protocol\nSection Package", encoding="utf-8")

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "TASK-TEST-research_plan.json").write_text('{"entity":"任意公司"}', encoding="utf-8")
    (tasks_dir / "TASK-TEST-fact_store.json").write_text('{"facts":[]}', encoding="utf-8")

    monkeypatch.setattr(launcher, "INSTRUCTION_STORE", protocol_dir)
    monkeypatch.setattr(launcher, "TASKS_DIR", tasks_dir)

    brief = launcher.build_step_brief("TASK-TEST", "step4_finance", entity="任意公司", query="写研报")

    assert "Shared protocol" in brief
    assert "Section Package" in brief
    assert "TASK-TEST-research_plan.json" in brief
    assert "TASK-TEST-fact_store.json" in brief
    assert "你不是直接写最终研报" in brief
