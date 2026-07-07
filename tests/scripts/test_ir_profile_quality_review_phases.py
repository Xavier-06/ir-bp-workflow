import json
import subprocess
import sys
from types import SimpleNamespace

from runtime.profiles.ir_profile import IRProfile, _run_debate_review_phase, _run_delivery_inner, _run_final_assembly_phase, _run_section_package_validation


def test_ir_profile_registers_quality_review_phases(tmp_path):
    phases = IRProfile(runtime_root=tmp_path).phases()

    assert "phase11_section_package_validation" in phases
    assert "phase12_debate_review" in phases
    assert "phase14_final_assembly" in phases
    assert phases.index("phase09_dispatch_collect") < phases.index("phase11_section_package_validation")
    assert phases.index("phase14_final_assembly") < phases.index("phase15_delivery")


def test_run_section_package_validation_writes_index(tmp_path):
    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "TASK-TEST-fact_store_index.json").write_text(
        json.dumps({"fact_ids": ["F-0001"], "total_facts": 1}),
        encoding="utf-8",
    )
    (tasks_dir / "TASK-TEST-step4_finance.md").write_text(
        """```json
{"schema_version":"ir_section_package.v1","section_id":"step4_finance","section_title":"财务质量","key_messages":["k"],"claims":[{"claim":"c","fact_ids":["F-0001"],"reasoning":"r","confidence":"high","source_quality":"official"}],"facts_used":["F-0001"],"counter_evidence":["risk"],"data_gaps":[],"markdown_draft":"draft"}
```""",
        encoding="utf-8",
    )
    job_ctx = SimpleNamespace(job_id="TASK-TEST", entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_section_package_validation(tmp_path, job_ctx)

    assert result["ok"] is True
    assert (tasks_dir / "TASK-TEST-section_packages.json").exists()


def test_run_debate_review_phase_writes_review(tmp_path):
    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "TASK-TEST-section_packages.json").write_text(json.dumps({"task_id":"TASK-TEST","summary":{"total":0},"packages":[]}), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="TASK-TEST", entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_debate_review_phase(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["verdict"] == "REWRITE_REQUIRED"


def test_run_final_assembly_phase_writes_assembly(tmp_path):
    tasks_dir = tmp_path / "data" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "TASK-TEST-research_plan.json").write_text(json.dumps({"entity":"任意公司","objective":"形成投资研究报告"}), encoding="utf-8")
    (tasks_dir / "TASK-TEST-section_packages.json").write_text(json.dumps({
        "task_id":"TASK-TEST",
        "packages":[{"step_name":"step4_finance","package":{"section_id":"step4_finance","section_title":"财务质量","key_messages":["k"],"claims":[{"claim":"c","fact_ids":["F-0001"],"reasoning":"r","confidence":"high","source_quality":"official"}],"facts_used":["F-0001"],"counter_evidence":["risk"],"data_gaps":[],"markdown_draft":"draft"},"validation":{"passed":True,"issues":[]}}]
    }), encoding="utf-8")
    (tasks_dir / "TASK-TEST-debate_review.json").write_text(json.dumps({"verdict":"PASS","issues":[]}), encoding="utf-8")
    job_ctx = SimpleNamespace(job_id="TASK-TEST", entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_final_assembly_phase(tmp_path, job_ctx)

    assert result["ok"] is True
    assert (tasks_dir / "TASK-TEST-final_report.md").exists()


def test_delivery_inner_runs_report_gate_before_docx(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "data" / "tasks"
    scripts_dir = tmp_path / "scripts"
    tasks_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    task_id = "TASK-DELIVERY-GATE"
    (tasks_dir / f"{task_id}-final_report.md").write_text(
        "# 研报\nTODO\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IRBP_BG_CHILD", "1")
    monkeypatch.setattr("scripts.verification_agent.run_verification", lambda task_id, pipeline: {"verdict": "PASS", "summary": "ok"})
    job_ctx = SimpleNamespace(job_id=task_id, entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_delivery_inner(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["report_gate"]["passed"] is False
    assert result["result"]["docx_path"] == ""
    assert (tasks_dir / f"{task_id}-report_gate.json").exists()


def test_delivery_inner_fails_when_audit_verdict_fails(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "data" / "tasks"
    scripts_dir = tmp_path / "scripts"
    tasks_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    task_id = "TASK-DELIVERY-AUDIT"
    (tasks_dir / f"{task_id}-final_report.md").write_text(
        "# 研报\nhttps://example.com/a\nhttps://example.com/b\nhttps://example.com/c\n",
        encoding="utf-8",
    )
    for script_name in ("build_ir_source_audit.py", "build_ir_execution_audit.py", "build_ir_broker_report_docx.py"):
        (scripts_dir / script_name).write_text("# dummy", encoding="utf-8")
    docx_path = tmp_path / "report.docx"
    docx_path.write_text("docx", encoding="utf-8")

    def fake_run(cmd, capture_output, text, timeout, cwd=None):
        script_name = str(cmd[1]) if len(cmd) > 1 else ""
        if "build_ir_source_audit.py" in script_name:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": str(tmp_path / "source.json"), "verdict": "FAIL"}), stderr="")
        if "build_ir_execution_audit.py" in script_name:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": str(tmp_path / "exec.json"), "verdict": "PASS"}), stderr="")
        if "build_ir_broker_report_docx.py" in script_name:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": str(docx_path)}), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.verification_agent.run_verification", lambda task_id, pipeline: {"verdict": "PASS", "summary": "ok"})
    job_ctx = SimpleNamespace(job_id=task_id, entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_delivery_inner(tmp_path, job_ctx)

    assert result["ok"] is False
    assert result["result"]["audits_ok"] is False
    assert result["result"]["docx_path"] == ""
    assert result["result"]["delivery_ok"] is False
    assert any("verdict FAIL" in err for err in result["result"]["audit_errors"])


def test_delivery_inner_uses_current_python_for_audits_and_docx(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "data" / "tasks"
    scripts_dir = tmp_path / "scripts"
    tasks_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    task_id = "TASK-DELIVERY-PYTHON"
    (tasks_dir / f"{task_id}-final_report.md").write_text(
        "# 研报\nhttps://example.com/a\nhttps://example.com/b\nhttps://example.com/c\n",
        encoding="utf-8",
    )
    for script_name in ("build_ir_source_audit.py", "build_ir_execution_audit.py", "build_ir_broker_report_docx.py"):
        (scripts_dir / script_name).write_text("# dummy", encoding="utf-8")
    docx_path = tmp_path / "report.docx"
    docx_path.write_text("docx", encoding="utf-8")
    seen = []

    def fake_run(cmd, capture_output, text, timeout, cwd=None):
        seen.append(cmd)
        script_name = str(cmd[1]) if len(cmd) > 1 else ""
        if "build_ir_broker_report_docx.py" in script_name:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": str(docx_path)}), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": str(tmp_path / "audit.json"), "verdict": "PASS"}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.verification_agent.run_verification", lambda task_id, pipeline: {"verdict": "PASS", "summary": "ok"})
    job_ctx = SimpleNamespace(job_id=task_id, entity="任意公司", query="", market="cn", metadata={}, workspace=None)

    result = _run_delivery_inner(tmp_path, job_ctx)

    assert result["result"]["docx_path"] == str(docx_path)
    assert seen
    assert all(cmd[0] == sys.executable for cmd in seen)
