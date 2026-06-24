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
        "投研_主笔_数据收集": "step1_role.md",
        "投研_主笔_行业分析": "industry_role.md",
        "投研_主笔_商业模式": "biz_role.md",
        "投研_主笔_财务分析": "finance_role.md",
        "投研_主笔_管理层": "mgmt_role.md",
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


def test_launch_next_wave_emits_team_async_workbuddy_agent_tasks(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    instruction_store = tmp_path / "instruction_store_ir"
    _write_instruction_store(instruction_store)

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
    assert instruction["run_in_background"] is True
    assert instruction["name"] == "step1_data"


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

    assert phases.index("phase2_fact_store_bootstrap") < phases.index("phase12_precompute")


def test_preflight_writes_mvp_research_plan_for_registered_task(tmp_path, monkeypatch):
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

    research_plan_path = tasks_dir / "TASK-RP-MVP-research_plan.json"
    assert research_plan_path.exists()
    payload = json.loads(research_plan_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ir_research_plan_mvp_v1"
    assert payload["research_type"] == "company_deep_dive"
    assert payload["section_requirements"]["step1_data"]["must_answer"]
    assert payload["coverage_matrix"]["Q1"]["required_fact_keys"]
    assert any(check["check"] == "research_plan" and check["passed"] for check in result["checks"])


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


def test_preflight_maps_industry_task_to_industry_research_plan(tmp_path, monkeypatch):
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

    preflight.run_preflight("TASK-IND", entity="机器人", query="机器人赛道研究", market="cn")

    payload = json.loads((tasks_dir / "TASK-IND-research_plan.json").read_text(encoding="utf-8"))
    assert payload["research_type"] == "industry_research"


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
