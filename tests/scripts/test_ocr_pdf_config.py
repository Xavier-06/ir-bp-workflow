from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OCR_PATH = ROOT / "scripts" / "ocr_pdf.py"


def load_ocr_module():
    spec = importlib.util.spec_from_file_location("ocr_pdf_under_test", OCR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_ocr_config_prefers_environment(monkeypatch):
    module = load_ocr_module()
    monkeypatch.setenv("BP_OCR_API_KEY", "env-key")
    monkeypatch.setenv("BP_OCR_MODEL", "env-model")
    monkeypatch.setenv("BP_OCR_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-key")

    config = module.load_ocr_config()

    assert config["api_key"] == "env-key"
    assert config["model"] == "env-model"
    assert config["base_url"] == "https://example.test/v1"


def test_load_ocr_config_reads_bp_ocr_keys_from_credentials(monkeypatch, tmp_path):
    module = load_ocr_module()
    for name in ("BP_OCR_API_KEY", "BP_OCR_MODEL", "BP_OCR_BASE_URL", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cred_dir = tmp_path / ".credentials"
    cred_dir.mkdir()
    (cred_dir / "investment-research.env").write_text(
        "BP_OCR_API_KEY=file-key\n"
        "BP_OCR_MODEL=file-model\n"
        "BP_OCR_BASE_URL=https://file.test/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)

    config = module.load_ocr_config()

    assert config == {
        "api_key": "file-key",
        "model": "file-model",
        "base_url": "https://file.test/v1",
    }


def test_ocr_uses_openai_compatible_chat_completions(monkeypatch):
    module = load_ocr_module()
    captured = {}

    class FakeChoice:
        message = type("Message", (), {"content": "识别结果"})()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeChatCompletions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return FakeCompletion()

    class FakeClient:
        def __init__(self, *, api_key: str, base_url: str):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = type("Chat", (), {"completions": FakeChatCompletions})()

    monkeypatch.setattr(module, "OpenAI", FakeClient)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module._ocr_images_with_qwen_vl(
        [b"fake-image"],
        api_key="test-key",
        model="qwen3-vl-30b-a3b-instruct",
        base_url="https://api.example.com/v1",
    )

    assert "识别结果" in result
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["model"] == "qwen3-vl-30b-a3b-instruct"
    message = captured["messages"][0]
    assert message["role"] == "user"
    assert message["content"][0]["type"] == "image_url"
    assert message["content"][1]["type"] == "text"
