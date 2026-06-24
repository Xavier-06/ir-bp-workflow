from __future__ import annotations

import os

import run_bp


def test_load_env_file_sets_vl_ocr_config_without_overriding_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VL_API_BASE=https://example.test/v1\n"
        "VL_API_KEY=from-env-file\n"
        "VL_MODEL=qwen3-vl-30b-a3b-instruct\n"
        "EXISTING=from-env-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VL_API_BASE", raising=False)
    monkeypatch.delenv("VL_API_KEY", raising=False)
    monkeypatch.delenv("VL_MODEL", raising=False)
    monkeypatch.setenv("EXISTING", "already-set")

    run_bp._load_env_file(env_file)

    assert os.environ["VL_API_BASE"] == "https://example.test/v1"
    assert os.environ["VL_API_KEY"] == "from-env-file"
    assert os.environ["VL_MODEL"] == "qwen3-vl-30b-a3b-instruct"
    assert os.environ["EXISTING"] == "already-set"
