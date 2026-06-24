from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_ir_script_documents_replacement_entrypoint():
    text = (ROOT / "scripts" / "run_ir_pipeline.py").read_text(encoding="utf-8")

    assert "LEGACY_ENTRYPOINT_NOTICE" in text
    assert "runtime/entrypoints/run_ir_pipeline_entry.py" in text


def test_legacy_ir_runtime_adapter_documents_replacement_entrypoint():
    text = (ROOT / "ir_runtime.py").read_text(encoding="utf-8")

    assert "LEGACY_ENTRYPOINT_NOTICE" in text
    assert "runtime/entrypoints/run_ir_pipeline_entry.py" in text
