import json
import builtins
import subprocess
from types import SimpleNamespace

from runtime.intake import bp_document_intake


def test_pdf_ocr_uses_pdftoppm_vl_when_pdf2image_missing(tmp_path, monkeypatch):
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    output_dir = tmp_path / "extraction"
    output_dir.mkdir()

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pdf2image":
            raise ImportError("pdf2image unavailable")
        return real_import(name, *args, **kwargs)

    def fake_which(name):
        return "/usr/local/bin/pdftoppm" if name == "pdftoppm" else None

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        assert cmd[0] == "/usr/local/bin/pdftoppm"
        prefix = cmd[-1]
        (tmp_path / "extraction" / "pdf_pages" / "page-1.png").write_bytes(b"png1")
        (tmp_path / "extraction" / "pdf_pages" / "page-2.png").write_bytes(b"png2")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    calls = []

    def fake_ocr_image(image_path, page_hint=""):
        calls.append((image_path.name, page_hint))
        return f"这是{page_hint}的BP文字，包含团队、产品、市场、客户、融资、收入、订单、技术壁垒和竞争格局。" * 6

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(bp_document_intake.shutil, "which", fake_which)
    monkeypatch.setattr(bp_document_intake.subprocess, "run", fake_run)
    monkeypatch.setattr(bp_document_intake, "_ocr_image", fake_ocr_image)

    text, pages, timed_out = bp_document_intake._ocr_pdf(pdf, output_dir)

    assert pages == 2
    assert len(calls) == 2
    assert calls[0][1] == "第1页"
    assert "Page 1" in text


def test_document_intake_rejects_ocr_failure_placeholder(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        return "[无法提取 PDF 文本: deck.pdf，请安装 pdf2image、pdfminer 或 PyPDF2]", 0, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    workspace = SimpleNamespace(root=tmp_path / "job", extraction_dir=tmp_path / "job" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-FAIL", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is False
    assert result["error"] == "OCR_RESULT_UNUSABLE"
    assert not (workspace.root / "bp_ocr_text.txt").exists()
    manifest = json.loads((workspace.root / "bp_ocr_manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_gate"]["verdict"] == "FAIL"
    assert manifest["quality_gate"]["reason"] == "OCR_RESULT_UNUSABLE"
    assert manifest["pages"][0]["status"] == "failed"


def test_document_intake_rejects_multi_page_ocr_failures(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        return "\n\n".join(f"--- 第{i}页 ---\n[OCR失败: timeout and model unavailable]" for i in range(1, 8)), 7, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    workspace = SimpleNamespace(root=tmp_path / "job2", extraction_dir=tmp_path / "job2" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-FAIL-MULTI", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is False
    assert result["error"] == "OCR_RESULT_UNUSABLE"


def test_document_intake_rejects_low_success_page_ratio(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")
    good_text = "这是一页有效 BP 内容，包含团队、产品、市场、客户、融资、收入、订单、技术壁垒、竞争格局和资金用途。" * 10

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        failed = "\n\n".join(f"--- 第{i}页 ---\n[OCR失败: timeout]" for i in range(2, 10))
        return f"--- 第1页 ---\n{good_text}\n\n{failed}", 9, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    workspace = SimpleNamespace(root=tmp_path / "job_ratio", extraction_dir=tmp_path / "job_ratio" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-RATIO", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is False
    assert result["error"] == "OCR_LOW_SUCCESS_RATIO"
    manifest = json.loads((workspace.root / "bp_ocr_manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_gate"]["success_ratio"] < 0.6


def test_document_intake_accepts_plain_full_document_text_without_page_markers(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")
    good_text = "这是一份从 PDF 纯文本 fallback 提取出的 BP 内容，包含团队、产品、市场、客户、融资、收入、订单、技术壁垒、竞争格局和资金用途。" * 40

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        return good_text, 16, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    monkeypatch.setattr(bp_document_intake, "extract_structured_info", lambda _text: {"company_name": "测试公司", "industry": "测试行业"})
    workspace = SimpleNamespace(root=tmp_path / "job_plain", extraction_dir=tmp_path / "job_plain" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-PLAIN", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is True
    manifest = json.loads((workspace.root / "bp_ocr_manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_gate"]["verdict"] == "PASS"
    assert manifest["quality_gate"]["success_ratio"] == 1.0
    assert len(manifest["pages"]) == 1
    assert manifest["pages"][0]["label"] == "document"


def test_document_intake_writes_success_manifest(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")
    good_text = "这是一页有效 BP 内容，包含团队、产品、市场、客户、融资、收入、订单、技术壁垒、竞争格局和资金用途。" * 20

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        return "\n\n".join(f"--- 第{i}页 ---\n{good_text}" for i in range(1, 4)), 3, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    monkeypatch.setattr(bp_document_intake, "extract_structured_info", lambda _text: {"company_name": "测试公司", "industry": "测试行业"})
    workspace = SimpleNamespace(root=tmp_path / "job_ok", extraction_dir=tmp_path / "job_ok" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-OK", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is True
    manifest = json.loads((workspace.root / "bp_ocr_manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_gate"]["verdict"] == "PASS"
    assert manifest["quality_gate"]["success_ratio"] == 1.0
    assert len(manifest["pages"]) == 3


def test_document_intake_rejects_multi_page_no_text(tmp_path, monkeypatch):
    input_file = tmp_path / "deck.pdf"
    input_file.write_bytes(b"%PDF-1.4 placeholder")

    def fake_ocr_pdf(_pdf_path, _output_dir, deadline=0):
        return "\n\n".join(f"--- 第{i}页 ---\n（VL识别无文字）" for i in range(1, 12)), 11, False

    monkeypatch.setattr(bp_document_intake, "_ocr_pdf", fake_ocr_pdf)
    workspace = SimpleNamespace(root=tmp_path / "job3", extraction_dir=tmp_path / "job3" / "extraction")
    workspace.root.mkdir(parents=True)
    workspace.extraction_dir.mkdir(parents=True)
    job_ctx = SimpleNamespace(job_id="BP-OCR-NO-TEXT", workspace=workspace)

    result = bp_document_intake.run_document_intake(job_ctx, str(input_file))

    assert result["ok"] is False
    assert result["error"] == "OCR_RESULT_UNUSABLE"
