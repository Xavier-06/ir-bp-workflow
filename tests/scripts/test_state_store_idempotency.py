from __future__ import annotations

import json
from pathlib import Path

from runtime.orchestrator.state_store import StateStore
from runtime.profiles.base import JobContext


def _patch_ledger_store(tmp_path: Path, monkeypatch) -> Path:
    import scripts.task_ledger as task_ledger

    ledger_path = tmp_path / "data" / "tasks" / "tasks.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps({"meta": {"version": 1, "updated_at": None}, "tasks": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(task_ledger, "STORE", ledger_path)
    return ledger_path


def _read_ledger(ledger_path: Path) -> dict:
    return json.loads(ledger_path.read_text(encoding="utf-8"))


def test_get_or_create_job_does_not_duplicate_ledger_entries(tmp_path, monkeypatch):
    ledger_path = _patch_ledger_store(tmp_path, monkeypatch)

    store = StateStore(runtime_root=tmp_path)
    job_ctx = JobContext(job_id="ir_resume_001", entity="TestCo", query="test", market="us")

    first_workspace = store.get_or_create_job(job_ctx)
    second_workspace = store.get_or_create_job(job_ctx)

    ledger = _read_ledger(ledger_path)
    matching = [task for task in ledger["tasks"] if task.get("task_id") == "ir_resume_001"]

    assert first_workspace.root == second_workspace.root
    assert len(matching) == 1
    assert matching[0]["title"] == "IR Pipeline: TestCo"


def test_get_or_create_job_collapses_preexisting_duplicate_ledger_entries(tmp_path, monkeypatch):
    ledger_path = _patch_ledger_store(tmp_path, monkeypatch)
    duplicate_store = {
        "meta": {"version": 1, "updated_at": None},
        "tasks": [
            {"task_id": "ir_resume_002", "title": "old one", "created_at": "t1", "progress_updates": [{"message": "first"}]},
            {"task_id": "ir_resume_002", "title": "old two", "created_at": "t2", "progress_updates": [{"message": "second"}]},
            {"task_id": "other", "title": "keep me", "created_at": "t3"},
        ],
    }
    ledger_path.write_text(json.dumps(duplicate_store, ensure_ascii=False), encoding="utf-8")

    store = StateStore(runtime_root=tmp_path)
    job_ctx = JobContext(job_id="ir_resume_002", entity="TestCo", query="test", market="us")

    store.get_or_create_job(job_ctx)

    ledger = _read_ledger(ledger_path)
    matching = [task for task in ledger["tasks"] if task.get("task_id") == "ir_resume_002"]
    others = [task for task in ledger["tasks"] if task.get("task_id") == "other"]

    assert len(matching) == 1
    assert matching[0]["title"] == "old one"
    assert [event["message"] for event in matching[0]["progress_updates"]] == ["first", "second"]
    assert len(others) == 1
