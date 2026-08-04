import json
import subprocess
import sys
from types import SimpleNamespace

import scripts.ir_preflight_check as preflight
import scripts.ir_subagent_launcher_wb as launcher
from runtime.profiles.ir_profile import IRProfile, _run_precompute


def _write_instruction_store(path):
    path.mkdir(parents=True, exist_ok=True)
    role_files = {
        "投研_研究计划": "research_plan_role.md",
        "投研_主笔_数据收集": "step1_role.md",
        "投研_主笔_行业分析": "industry_role.md",
        "投研_主笔_商业模式": "biz_role.md",
        "投研_主笔_财务分析": "finance_role.md",
        "投研_主笔_管理层": "mgmt_role.md",
        "投研_主笔_宏观分析": "macro_role.md",
        "投研_主笔_差异化洞察": "insight_role.md",
        "投研_主笔_预测与估值": "valuation_role.md",
        "投研_主笔_风险催化": "risk_role.md",
        "投研_主笔_文档汇总": "master_role.md",
    }
    bindings = {
        "roles": [
            {"key": key, "name": key, "file": file_name}
            for key, file_name in role_files.items()
        ],
        "pipeline_bindings": {
            "ir": {
                "step1_data": "step1_role",
                "step_macro": "macro_role",
            }
        },
    }
    (path / "index.json").write_text(json.dumps(bindings, ensure_ascii=False), encoding="utf-8")
    for key, file_name in role_files.items():
        (path / file_name).write_text(f"# {key}", encoding="utf-8")
    (path / "macro_role.md").write_text("# Macro role", encoding="utf-8")
    (path / "_shared_output_protocol.md").write_text("# Shared protocol", encoding="utf-8")


def _write_valid_plan(path):
    """v3.2: 子代理产出的合法 plan 夹具（gate 不再脚本兜底，测试需预置 plan）"""
    plan = {
        "schema_version": "ir_research_plan.v5",
        "task_id": "TASK-WB", "entity": "任意公司", "market": "cn",
        "plan_status": "ready",
        "core_questions": [{
            "question_id": "Q1", "question": "q", "priority": "high",
            "owner_section": "step3_finance", "supporting_sections": [],
            "required_fact_keys": ["revenue_trend"], "decision_relevance": "d",
        }],
        "strategic_questions": [{
            "question_id": "SQ1", "question": "sq", "priority": "high",
            "owner_section": "step6_valuation",
            "required_fact_keys": ["valuation_multiples"], "decision_relevance": "d",
        }],
        "section_requirements": {
            "step3_finance": {"must_answer": ["Q1"], "required_fact_keys": ["revenue_trend"],
                              "required_outputs": ["claims", "facts_used", "data_gaps", "markdown_draft"]},
            "step6_valuation": {"must_answer": ["SQ1"], "required_fact_keys": ["valuation_multiples"],
                                "required_outputs": ["claims", "facts_used", "data_gaps", "markdown_draft"]},
        },
        "fact_requirements": [
            {"fact_key": "revenue_trend", "description": "收入", "source_priority": ["annual_report"], "required_for": ["step3_finance"], "criticality": "high"},
            {"fact_key": "valuation_multiples", "description": "估值倍数", "source_priority": ["market_data"], "required_for": ["step6_valuation"], "criticality": "high"},
        ],
        "coverage_matrix": {
            "Q1": {"owner": "step3_finance", "supporting_sections": [], "required_fact_keys": ["revenue_trend"]},
            "SQ1": {"owner": "step6_valuation", "supporting_sections": [], "required_fact_keys": ["valuation_multiples"]},
        },
    }
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_launch_next_wave_emits_team_async_workbuddy_agent_tasks(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    # v3.2: plan 由子代理产出，脚本不再兜底 → 测试预置合法 plan
    _write_valid_plan(tasks_dir / "TASK-WB-research_plan.json")

    monkeypatch.setattr(launcher, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(launcher, "INSTRUCTION_STORE", instruction_store)

    result = launcher.launch_next_wave(
        "TASK-WB", entity="任意公司", query="写研报", market="cn", sequential=True
    )

    plan = json.loads((tasks_dir / "TASK-WB-research_plan.json").read_text(encoding="utf-8"))
    assert plan["plan_status"] == "ready"
    assert plan["strategic_questions"]
    assert result["research_plan_gate"]["ready"] is True

    instructions = result["task_tool_instructions"]
    assert len(instructions) == 1
    instruction = instructions[0]
    assert instruction["action"] == "team_async_agent"
    assert instruction["tool"] == "Agent"
    assert instruction["dispatch_mode"] == "team_async"
    assert instruction["subagent_type"] == "general-purpose"
    assert instruction["team_name_template"] == "ir-{task_id}"
    assert instruction["team_name"] == "ir-TASK-WB"
    assert instruction["mode"] == "bypassPermissions"
    assert instruction["connectorIds"] == ["tyc-mcp", "westock-mcp", "ima-mcp"]
    assert instruction["prompt"]
    assert instruction["brief_path"]
    # v3.0+: step1_data 已删除，Wave1 首发 step1_industry
    assert instruction["name"] == "step1_industry"


def test_launch_next_wave_blocks_when_research_plan_missing(tmp_path, monkeypatch):
    """v3.2: plan 缺失时 gate 直接拦截（不再脚本兜底生成）"""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)

    monkeypatch.setattr(launcher, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(launcher, "INSTRUCTION_STORE", instruction_store)

    result = launcher.launch_next_wave("TASK-NOPLAN", entity="任意公司", query="写研报", market="cn")

    assert result["next_action"] == "fix_research_plan"
    assert result["dispatched_count"] == 0
    assert not (tasks_dir / "TASK-NOPLAN-research_plan.json").exists()


def test_launch_next_wave_blocks_when_existing_research_plan_is_not_ready(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    (tasks_dir / "TASK-BADPLAN-research_plan.json").write_text(
        json.dumps({
            "schema_version": "ir_research_plan_mvp_v1",
            "core_questions": [],
            "section_requirements": {},
            "fact_requirements": [],
            "coverage_matrix": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(launcher, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(launcher, "INSTRUCTION_STORE", instruction_store)

    result = launcher.launch_next_wave("TASK-BADPLAN", entity="泡泡玛特", query="泡泡玛特公司深度研究", market="hk")

    assert result["next_action"] == "fix_research_plan"
    assert result["dispatched_count"] == 0
    assert "strategic_questions_missing" in result["research_plan_gate"]["errors"]


def test_launch_step_blocks_direct_dispatch_when_research_plan_is_not_ready(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    (tasks_dir / "TASK-DIRECT-BAD-research_plan.json").write_text(
        json.dumps({
            "schema_version": "ir_research_plan_mvp_v1",
            "plan_status": "blocked",
            "core_questions": [],
            "section_requirements": {},
            "fact_requirements": [],
            "coverage_matrix": {},
            "strategic_questions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(launcher, "INSTRUCTION_STORE", instruction_store)

    result = launcher.launch_step("TASK-DIRECT-BAD", "step1_data", entity="泡泡玛特", query="泡泡玛特公司深度研究")

    assert result["status"] == "blocked"
    assert result["reason"] == "research_plan_not_ready"
    assert not (tasks_dir / "TASK-DIRECT-BAD-manifest-step1_data.json").exists()


def test_ir_profile_runs_fact_store_bootstrap_before_precompute(tmp_path):
    phases = IRProfile(runtime_root=tmp_path).phases()

    assert phases.index("phase06_fact_store_bootstrap") < phases.index("phase07_precompute")


def test_preflight_marks_research_plan_pending_for_registered_task(tmp_path, monkeypatch):
    """v3.2: preflight 不再预建 plan——无 plan 时标记 pending_subagent_generation（不阻断）"""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    runtime_config = tmp_path / "ir-runtime.json"
    runtime_config.write_text(
        json.dumps({
            "routing": {
                "subagent_policy": {"enabled": True},
                "thinking_policy": {
                    "default_subagent_thinking": "high",
                    "default_main_reasoning": "high",
                },
            }
        }),
        encoding="utf-8",
    )
    (tasks_dir / "tasks.json").write_text(
        json.dumps({
            "tasks": [
                {
                    "task_id": "TASK-RP-MVP",
                    "title": "泡泡玛特公司深度研究",
                    "task_type": "专题研究类",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(preflight, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(preflight, "TASK_LEDGER", tasks_dir / "tasks.json")
    monkeypatch.setattr(preflight, "IR_RUNTIME", runtime_config)
    monkeypatch.setattr(preflight, "INSTRUCTION_INDEX", instruction_store / "index.json")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)

    result = preflight.run_preflight(
        "TASK-RP-MVP",
        mode="subagent",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究",
        market="hk",
    )

    # plan 由 phase04 子代理生成，preflight 不预建
    assert not (tasks_dir / "TASK-RP-MVP-research_plan.json").exists()
    rp_check = next(c for c in result["checks"] if c["check"] == "research_plan")
    assert rp_check["passed"] is True
    assert "pending_subagent_generation" in rp_check["detail"]


def test_preflight_does_not_write_research_plan_when_policy_blocks(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    runtime_config = tmp_path / "ir-runtime.json"
    runtime_config.write_text(
        json.dumps({
            "routing": {
                "subagent_policy": {"enabled": False},
                "thinking_policy": {
                    "default_subagent_thinking": "high",
                    "default_main_reasoning": "high",
                },
            }
        }),
        encoding="utf-8",
    )
    (tasks_dir / "tasks.json").write_text(
        json.dumps({"tasks": [{"task_id": "TASK-BLOCKED", "title": "泡泡玛特公司深度研究"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(preflight, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(preflight, "TASK_LEDGER", tasks_dir / "tasks.json")
    monkeypatch.setattr(preflight, "IR_RUNTIME", runtime_config)
    monkeypatch.setattr(preflight, "INSTRUCTION_INDEX", instruction_store / "index.json")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)

    result = preflight.run_preflight("TASK-BLOCKED", entity="泡泡玛特", query="泡泡玛特公司深度研究", market="hk")

    assert result["passed"] is False
    assert not (tasks_dir / "TASK-BLOCKED-research_plan.json").exists()


def test_preflight_validates_existing_research_plan(tmp_path, monkeypatch):
    """v3.2: 已有子代理产出的 plan 时，preflight 校验其就绪性。

    合法 plan → research_plan check passed；损坏 plan → failed。
    """
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)
    runtime_config = tmp_path / "ir-runtime.json"
    runtime_config.write_text(
        json.dumps({
            "routing": {
                "subagent_policy": {"enabled": True},
                "thinking_policy": {
                    "default_subagent_thinking": "high",
                    "default_main_reasoning": "high",
                },
            }
        }),
        encoding="utf-8",
    )
    (tasks_dir / "tasks.json").write_text(
        json.dumps({"tasks": [{"task_id": "TASK-IND", "title": "机器人赛道研究"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(preflight, "TASK_LEDGER", tasks_dir / "tasks.json")
    monkeypatch.setattr(preflight, "IR_RUNTIME", runtime_config)
    monkeypatch.setattr(preflight, "INSTRUCTION_INDEX", instruction_store / "index.json")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)

    # 场景 1：合法 plan → passed
    plan = {
        "schema_version": "ir_research_plan.v5",
        "task_id": "TASK-IND", "entity": "机器人", "market": "cn",
        "report_type": "industry_research",
        "plan_status": "ready",
        "core_questions": [{
            "question_id": "Q1", "question": "q", "priority": "high",
            "owner_section": "step1_industry", "supporting_sections": [],
            "required_fact_keys": ["market_size"], "decision_relevance": "d",
        }],
        "strategic_questions": [{
            "question_id": "SQ1", "question": "sq", "priority": "high",
            "owner_section": "step1_industry",
            "required_fact_keys": ["market_size"], "decision_relevance": "d",
        }],
        "section_requirements": {
            "step1_industry": {"must_answer": ["Q1", "SQ1"], "required_fact_keys": ["market_size"],
                               "required_outputs": ["claims", "facts_used", "data_gaps", "markdown_draft"]},
        },
        "fact_requirements": [
            {"fact_key": "market_size", "description": "行业规模", "source_priority": ["industry_report"], "required_for": ["step1_industry"], "criticality": "high"},
        ],
        "coverage_matrix": {
            "Q1": {"owner": "step1_industry", "supporting_sections": [], "required_fact_keys": ["market_size"]},
            "SQ1": {"owner": "step1_industry", "supporting_sections": [], "required_fact_keys": ["market_size"]},
        },
    }
    (tasks_dir / "TASK-IND-research_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    result = preflight.run_preflight("TASK-IND", entity="机器人", query="机器人赛道研究", market="cn")
    rp_check = next(c for c in result["checks"] if c["check"] == "research_plan")
    assert rp_check["passed"] is True

    # 场景 2：损坏 plan（缺 strategic_questions）→ failed
    bad = dict(plan)
    bad["strategic_questions"] = []
    (tasks_dir / "TASK-IND-research_plan.json").write_text(
        json.dumps(bad, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    result2 = preflight.run_preflight("TASK-IND", entity="机器人", query="机器人赛道研究", market="cn")
    rp_check2 = next(c for c in result2["checks"] if c["check"] == "research_plan")
    assert rp_check2["passed"] is False
    assert result2["passed"] is False


def test_run_precompute_uses_current_python_executable(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    for script_name in ("financial_metrics_precompute.py", "sector_benchmarks.py"):
        (scripts_dir / script_name).write_text("# dummy", encoding="utf-8")

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='{"status":"ok"}', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="# markdown", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    job_ctx = SimpleNamespace(
        job_id="TASK-PRECOMPUTE",
        entity="任意公司",
        query="写研报",
        market="cn",
        metadata={"ticker": "0700.HK"},
        workspace=None,
    )

    result = _run_precompute(tmp_path, job_ctx)

    assert result["ok"] is True
    assert calls
    assert all(cmd[0] == sys.executable for cmd in calls)
