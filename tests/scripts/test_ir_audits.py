from __future__ import annotations

import json

from scripts.build_ir_execution_audit import build_execution_audit
from scripts.build_ir_source_audit import build_source_audit


def test_build_source_audit_reads_fact_store_and_section_packages(tmp_path):
    task_id = "TASK-AUDIT"
    (tmp_path / f"{task_id}-fact_store.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "entity": "测试公司",
                "facts": [
                    {
                        "fact_id": "F-0001",
                        "claim": "测试公司收入100亿元",
                        "source_url": "https://example.com/report",
                        "source_tier": "official",
                        "confidence": "high",
                    },
                    {
                        "fact_id": "F-0002",
                        "claim": "预计未来增长",
                        "source_url": "",
                        "source_tier": "unknown",
                        "confidence": "low",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{task_id}-section_packages.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "packages": [
                    {"step_name": "step4_finance", "package": {"claims": [{"claim": "c", "fact_ids": ["F-0001", "F-404"]}]}}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = build_source_audit(task_id, tasks_dir=tmp_path)

    assert payload["output"] == str(tmp_path / f"{task_id}-source-audit.json")
    assert payload["counts"]["retrieved_fact"] == 1
    assert payload["counts"]["process_or_query"] == 1
    assert payload["source_count"] == 1
    assert payload["claims_without_sources"] == ["F-0002"]
    assert payload["unknown_fact_references"] == ["F-404"]
    assert (tmp_path / f"{task_id}-source-audit.json").exists()


def test_build_execution_audit_reads_workspace_phase_state_and_step_receipts(tmp_path):
    task_id = "TASK-EXEC"
    state_dir = tmp_path / "jobs" / task_id / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "phase4_dispatch_collect.json").write_text(
        json.dumps({"phase": "phase4_dispatch_collect", "status": "completed", "attempt": 2}),
        encoding="utf-8",
    )
    (tmp_path / f"{task_id}-step4_finance-spawn.json").write_text(
        json.dumps({"step": "step4_finance", "childSessionKey": "child-1", "runId": "run-1", "status": "dispatched"}),
        encoding="utf-8",
    )
    (tmp_path / f"{task_id}-step4_finance-manifest.json").write_text(
        json.dumps({"step": "step4_finance", "output_path": "out.md"}),
        encoding="utf-8",
    )

    payload = build_execution_audit(task_id, tasks_dir=tmp_path, runtime_root=tmp_path)

    assert payload["output"] == str(tmp_path / f"{task_id}-execution-audit.json")
    assert payload["multi_agent_real_collab"] is True
    assert payload["phase_state_count"] == 1
    assert payload["step_manifest_count"] == 1
    assert payload["subagent_spawn_receipts"][0]["step"] == "step4_finance"
    assert payload["duplicate_dispatches"] == []
    assert (tmp_path / f"{task_id}-execution-audit.json").exists()
