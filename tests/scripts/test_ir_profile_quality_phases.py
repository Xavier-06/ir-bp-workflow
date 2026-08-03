import json
from pathlib import Path
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


def test_run_research_plan_collect_financial_inference_fallback(tmp_path):
    """v2.2: 子代理未产出 plan 时，collect 降级脚本生成 + 财务感知兜底。

    场景：company_verify 的 financial_data 含亏损信号（净亏损），且子代理 plan 缺失。
    期望：fallback plan 的 valuation_paradigm 被推断为 preprofit_growth，
    valuation_method_primary=PS/EV-Sales，PE/DCF 被禁，并打 thesis_source 标记。
    （修复 MiniMax 类亏损标的被套上 PE 框架的缺陷）
    """
    from runtime.profiles.ir_profile import _run_research_plan_collect

    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)

    # 写含亏损信号的 company_verify（无子代理 ir_research_plan.json，触发降级）
    company_verify = {
        "task_id": "TASK-LOSS",
        "entity": "亏损科技公司",
        "market": "hk",
        "financial_data": [
            {"text": "公司2025年营收同比增长158%，经调整净亏损2.51亿美元，资产负债率343%。", "url": "", "metric": "毛利率25.4%"},
        ],
        "valuation_data": {},
        "key_events": [],
        "source_urls": [],
    }
    (tasks_dir / "TASK-LOSS-ir_company_verify.json").write_text(
        json.dumps(company_verify, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    job_ctx = SimpleNamespace(job_id="TASK-LOSS", entity="亏损科技公司", query="深度研究", market="hk", metadata={}, workspace=None)
    result = _run_research_plan_collect(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["enrichment"] == "fallback_script"
    assert result["result"]["thesis_source"] == "fallback_financial_inference"

    # 读取落盘的 fallback plan，验证财务感知推断生效
    plan = json.loads(Path(result["result"]["plan_path"]).read_text(encoding="utf-8"))
    assert plan["valuation_paradigm"] == "preprofit_growth"
    assert plan["valuation_method_primary"] == "PS / EV-Sales"
    assert "PE" in plan["valuation_forbidden"]
    assert "DCF" in plan["valuation_forbidden"]
    assert plan.get("thesis_source") == "fallback_financial_inference"


def test_run_research_plan_collect_no_loss_keeps_default(tmp_path):
    """v2.2 对照组：company_verify 无亏损信号时，fallback 保持默认 profitable_growth。"""
    from runtime.profiles.ir_profile import _run_research_plan_collect

    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)

    company_verify = {
        "task_id": "TASK-PROFIT",
        "entity": "盈利公司",
        "market": "cn",
        "financial_data": [
            {"text": "公司2025年营收同比增长20%，归母净利润12亿元，毛利率40%。", "url": "", "metric": "毛利率40%"},
        ],
        "valuation_data": {},
        "key_events": [],
        "source_urls": [],
    }
    (tasks_dir / "TASK-PROFIT-ir_company_verify.json").write_text(
        json.dumps(company_verify, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    job_ctx = SimpleNamespace(job_id="TASK-PROFIT", entity="盈利公司", query="深度研究", market="cn", metadata={}, workspace=None)
    result = _run_research_plan_collect(tmp_path, job_ctx)

    assert result["ok"] is True
    assert result["result"]["thesis_source"] == "fallback_default"
    plan = json.loads(Path(result["result"]["plan_path"]).read_text(encoding="utf-8"))
    assert plan["valuation_paradigm"] == "profitable_growth"
    assert plan["valuation_method_primary"] == "PE / EV-EBITDA"


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
