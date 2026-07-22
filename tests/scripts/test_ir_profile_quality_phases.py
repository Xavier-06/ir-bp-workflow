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
    assert "phase04_research_plan" in phases
    assert "phase04_research_plan_collect" in phases
    assert "phase06_fact_store_bootstrap" in phases
    assert "phase10_fact_store_merge" in phases
    assert phases.index("phase04_research_plan") < phases.index("phase04_research_plan_collect")
    assert phases.index("phase04_research_plan_collect") < phases.index("phase06_fact_store_bootstrap")
    assert phases.index("phase06_fact_store_bootstrap") < phases.index("phase08_dispatch_prepare")
    assert phases.index("phase09_dispatch_collect") < phases.index("phase10_fact_store_merge")
    assert phases.index("phase10_fact_store_merge") < phases.index("phase11_section_package_validation")


def test_run_research_plan_writes_skeleton_and_returns_needs_dispatch(tmp_path):
    """v5.2: 子代理派发模式，生成 brief 而非 skeleton。"""
    job_ctx = SimpleNamespace(job_id="TASK-GENERIC", entity="任意公司", query="写券商版研报", market="cn", metadata={}, workspace=None)

    result = _run_research_plan(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["needs_dispatch"] is True
    assert result["has_more"] is False
    # brief 文件已写入（v5.2 用 brief 替代 skeleton）
    brief_path = tmp_path / "data" / "tasks" / "TASK-GENERIC-ir_phase04_brief.json"
    assert brief_path.exists()
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert brief["entity"] == "任意公司"


def test_run_research_plan_collect_merges_enrichment(tmp_path):
    from runtime.profiles.ir_profile import _run_research_plan_collect

    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)

    # 先写骨架
    from scripts.ir_research_planner import build_ir_research_plan_skeleton
    skeleton = build_ir_research_plan_skeleton(
        task_id="TASK-ENRICH", entity="测试公司", query="测试研究",
    )
    (tasks_dir / "TASK-ENRICH-ir_research_plan_skeleton.json").write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # 写 enrichment delta
    enrichment = {
        "strategic_questions": [
            {
                "question_id": "ESQ1",
                "question": "测试公司的核心壁垒是什么？",
                "priority": "high",
                "owner_section": "step3_biz",
                "supporting_sections": ["step2_industry"],
                "required_fact_keys": ["moat_evidence", "competitive_landscape"],
                "decision_relevance": "决定长期竞争优势",
            },
            {
                "question_id": "ESQ2",
                "question": "收入增长驱动因素？",
                "priority": "high",
                "owner_section": "step1_data",
                "required_fact_keys": ["revenue_trend", "growth_rate"],
                "decision_relevance": "决定增长可持续性",
            },
            {
                "question_id": "ESQ3",
                "question": "估值隐含什么预期？",
                "priority": "high",
                "owner_section": "step6b_valuation",
                "required_fact_keys": ["valuation_multiples", "dcf_inputs"],
                "decision_relevance": "决定估值合理性",
            },
            {
                "question_id": "ESQ4",
                "question": "管理层执行力如何？",
                "priority": "high",
                "owner_section": "step5_mgmt",
                "required_fact_keys": ["management_roster", "ownership"],
                "decision_relevance": "决定治理判断",
            },
            {
                "question_id": "ESQ5",
                "question": "哪些风险会推翻结论？",
                "priority": "high",
                "owner_section": "step7_risk",
                "required_fact_keys": ["bear_case", "risk_triggers"],
                "decision_relevance": "决定反证充分性",
            },
        ],
    }
    (tasks_dir / "TASK-ENRICH-ir_research_plan_enrichment.json").write_text(
        json.dumps(enrichment, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    job_ctx = SimpleNamespace(job_id="TASK-ENRICH", entity="测试公司", query="测试研究", market="cn", metadata={}, workspace=None)
    result = _run_research_plan_collect(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["plan_status"] == "ready"
    assert result["result"]["enrichment_status"] == "enriched"

    # 最终计划已写入
    plan_path = tasks_dir / "TASK-ENRICH-research_plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(plan["strategic_questions"]) == 5
    assert plan["strategic_questions"][0]["question_id"] == "ESQ1"


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
