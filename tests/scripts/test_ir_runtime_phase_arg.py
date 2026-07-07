from __future__ import annotations

import subprocess

import ir_runtime


def test_ir_runtime_maps_phase_to_start_phase(monkeypatch):
    calls = []

    def fake_py(script_name, *args):
        calls.append((script_name, args))
        return subprocess.CompletedProcess([script_name, *args], 0, stdout="ok", stderr="")

    monkeypatch.setattr(ir_runtime, "_load_env", lambda: True)
    monkeypatch.setattr(ir_runtime, "_py", fake_py)

    result = ir_runtime.run_pipeline("TASK-PHASE", phase="phase09_dispatch_collect")

    assert result["ok"] is True
    assert calls[0][0] == "run_ir_pipeline.py"
    assert "--start-phase" in calls[0][1]
    assert "phase09_dispatch_collect" in calls[0][1]
    assert "--phase" not in calls[0][1]


def test_ir_runtime_maps_numeric_phase_to_runtime_phase_name(monkeypatch):
    calls = []

    def fake_py(script_name, *args):
        calls.append((script_name, args))
        return subprocess.CompletedProcess([script_name, *args], 0, stdout="ok", stderr="")

    monkeypatch.setattr(ir_runtime, "_load_env", lambda: True)
    monkeypatch.setattr(ir_runtime, "_py", fake_py)

    result = ir_runtime.run_pipeline("TASK-PHASE", phase="4")

    assert result["ok"] is True
    assert "--start-phase" in calls[0][1]
    assert "phase08_dispatch_prepare" in calls[0][1]
