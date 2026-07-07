import json
from types import SimpleNamespace

from runtime.profiles.ir_profile import (
    IRProfile,
    _run_dispatch_collect,
    _run_fact_store_bootstrap,
    _run_research_plan,
    _run_section_package_validation,
)


def test_ir_profile_registers_quality_production_phases(tmp_path):
    profile = IRProfile(runtime_root=tmp_path)

    phases = profile.phases()
    assert "phase03_research_plan" in phases
    assert "phase06_fact_store_bootstrap" in phases
    assert "phase10_fact_store_merge" in phases
    assert phases.index("phase03_research_plan") < phases.index("phase04_presearch")
    assert phases.index("phase06_fact_store_bootstrap") < phases.index("phase08_dispatch_prepare")
    assert phases.index("phase09_dispatch_collect") < phases.index("phase10_fact_store_merge")
    assert phases.index("phase10_fact_store_merge") < phases.index("phase11_section_package_validation")


def test_run_research_plan_writes_generic_plan(tmp_path):
    job_ctx = SimpleNamespace(job_id="TASK-GENERIC", entity="任意公司", query="写券商版研报", market="cn", metadata={}, workspace=None)

    result = _run_research_plan(tmp_path, job_ctx)

    assert result["ok"] is True
    path = tmp_path / "data" / "tasks" / "TASK-GENERIC-research_plan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entity"] == "任意公司"
    assert len(payload["investment_questions"]) >= 5


def test_run_fact_store_bootstrap_writes_empty_generic_store_when_no_sources(tmp_path):
    job_ctx = SimpleNamespace(job_id="TASK-GENERIC", entity="任意公司", query="写券商版研报", market="cn", metadata={}, workspace=None)

    result = _run_fact_store_bootstrap(tmp_path, job_ctx)

    assert result["ok"] is True
    path = tmp_path / "data" / "tasks" / "TASK-GENERIC-fact_store.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entity"] == "任意公司"
    assert payload["market"] == "cn"
    assert payload["facts"] == []


def test_dispatch_collect_runs_unified_step_gate(tmp_path, monkeypatch):
    task_id = "TASK-GATE-COLLECT"
    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / f"{task_id}-step1_data.md").write_text(
        "## 数据\n" + "正文" * 300 + "\nhttps://example.com/1\nhttps://example.com/2\nhttps://example.com/3\n",
        encoding="utf-8",
    )
    (tasks_dir / f"{task_id}-step1_data-facts.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    (tasks_dir / f"{task_id}-step1_data-section.json").write_text(json.dumps({"schema_version": "ir_section_package.v1"}), encoding="utf-8")

    monkeypatch.setattr("scripts.ir_subagent_launcher_wb.TASKS_DIR", tasks_dir)
    monkeypatch.setattr("scripts.ir_subagent_launcher_wb.STEP_DEPS", {"step1_data": []})
    monkeypatch.setattr("scripts.ir_subagent_launcher_wb.get_pipeline_status", lambda job_id: {"task_id": job_id})
    monkeypatch.setattr("scripts.ir_subagent_launcher_wb.check_step_quality", lambda job_id, step: {"verdict": "pass", "score": 5})

    job_ctx = SimpleNamespace(job_id=task_id, entity="任意公司", query="写券商版研报", market="cn", metadata={}, workspace=None)
    result = _run_dispatch_collect(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["step_gate"]["passed"] is True
    assert (tasks_dir / f"{task_id}-step_gate.json").exists()


def test_section_package_validation_runs_unified_section_gate(tmp_path):
    task_id = "TASK-SECTION-PROFILE"
    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / f"{task_id}-fact_store_index.json").write_text(
        json.dumps({"fact_ids": ["F-0001"], "total_facts": 1}),
        encoding="utf-8",
    )
    (tasks_dir / f"{task_id}-step4_finance.md").write_text("## 财务\n正文", encoding="utf-8")
    (tasks_dir / f"{task_id}-step4_finance-section.json").write_text(
        json.dumps({
            "schema_version": "ir_section_package.v1",
            "section_id": "step4_finance",
            "section_title": "财务质量",
            "key_messages": ["k"],
            "claims": [{"claim": "c", "fact_ids": ["F-0001"], "reasoning": "r", "confidence": "high", "source_quality": "official"}],
            "facts_used": ["F-0001"],
            "counter_evidence": ["risk"],
            "data_gaps": [],
            "markdown_draft": "draft",
        }),
        encoding="utf-8",
    )

    job_ctx = SimpleNamespace(job_id=task_id, entity="任意公司", query="写券商版研报", market="cn", metadata={}, workspace=None)
    result = _run_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["section_gate"]["passed"] is True
    assert (tasks_dir / f"{task_id}-section_gate.json").exists()
