#!/usr/bin/env python3
"""Section Package extraction and validation for generic IR agents."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"
SCHEMA_VERSION = "ir_section_package.v1"
REQUIRED_FIELDS = [
    "schema_version",
    "section_id",
    "section_title",
    "key_messages",
    "claims",
    "facts_used",
    "counter_evidence",
    "data_gaps",
    "markdown_draft",
]
CLAIM_FIELDS = ["claim", "fact_ids", "reasoning", "confidence", "source_quality"]
# 匹配 ```json 或 ``` 代码块开头，捕获到块结束 ```
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.I)
# 备用：裸 JSON 开头的 { 位置
_JSON_DECODER = json.JSONDecoder()


def extract_section_package(text: str) -> dict[str, Any]:
    """Extract the first JSON object that looks like a Section Package.

    Uses json.JSONDecoder.raw_decode() which correctly handles nested braces,
    unlike the old regex non-greedy match that would truncate at the first }.
    """
    if not text:
        return {}

    # Strategy 1: Extract from ```json ... ``` code blocks
    for match in _CODE_BLOCK_RE.finditer(text):
        block = match.group(1).strip()
        # Find first { and use raw_decode
        brace_start = block.find("{")
        if brace_start < 0:
            continue
        try:
            payload, _ = _JSON_DECODER.raw_decode(block, brace_start)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and "section_id" in payload:
            return payload

    # Strategy 2: Fallback — try raw_decode from any { in the text
    search_start = 0
    while True:
        brace_start = text.find("{", search_start)
        if brace_start < 0:
            break
        try:
            payload, _ = _JSON_DECODER.raw_decode(text, brace_start)
        except (json.JSONDecodeError, ValueError):
            search_start = brace_start + 1
            continue
        if isinstance(payload, dict) and "section_id" in payload:
            return payload
        search_start = brace_start + 1

    return {}


def validate_section_package(package: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not package:
        return {"passed": False, "issues": [{"severity": "FAIL", "code": "MISSING_PACKAGE", "message": "No section package found"}]}

    # 归一化 schema_version（对齐 BP validator 的 alias 容忍，2026-07-27）：
    # 子代理可能写出各种变体或漏写 schema_version，统一归一化到 SCHEMA_VERSION，
    # 只对真正未知的版本报 UNSUPPORTED_SCHEMA_VERSION。
    schema_aliases = {
        "ir_section.v1": SCHEMA_VERSION,
        "ir_section_sidecar.v1": SCHEMA_VERSION,
        "ir_step_section.v1": SCHEMA_VERSION,
        "ir_section_package.v2": SCHEMA_VERSION,
        "section_package.v1": SCHEMA_VERSION,
    }
    schema_version = package.get("schema_version")
    if schema_version in schema_aliases:
        package["schema_version"] = schema_aliases[schema_version]
        schema_version = schema_aliases[schema_version]
    elif not schema_version:
        package["schema_version"] = SCHEMA_VERSION
        schema_version = SCHEMA_VERSION

    for field in REQUIRED_FIELDS:
        if field not in package:
            code = "MISSING_SCHEMA_VERSION" if field == "schema_version" else "MISSING_FIELD"
            issues.append({"severity": "FAIL", "code": code, "message": f"Missing field: {field}"})
    if schema_version and schema_version != SCHEMA_VERSION:
        issues.append({"severity": "FAIL", "code": "UNSUPPORTED_SCHEMA_VERSION", "message": f"Unsupported schema_version: {schema_version}"})

    claims = package.get("claims", [])
    if not isinstance(claims, list) or not claims:
        issues.append({"severity": "FAIL", "code": "MISSING_CLAIMS", "message": "Section package must include at least one claim"})
    else:
        for idx, claim in enumerate(claims):
            if not isinstance(claim, dict):
                issues.append({"severity": "FAIL", "code": "INVALID_CLAIM", "message": f"Claim {idx} is not an object"})
                continue
            for field in CLAIM_FIELDS:
                if field not in claim:
                    issues.append({"severity": "FAIL", "code": "MISSING_CLAIM_FIELD", "message": f"Claim {idx} missing field: {field}"})
            if not claim.get("fact_ids"):
                issues.append({"severity": "FAIL", "code": "CLAIM_WITHOUT_FACTS", "message": f"Claim {idx} has no fact_ids"})
            if not claim.get("reasoning"):
                issues.append({"severity": "WARN", "code": "CLAIM_WITHOUT_REASONING", "message": f"Claim {idx} has no reasoning"})

    if not package.get("facts_used"):
        issues.append({"severity": "FAIL", "code": "MISSING_FACTS_USED", "message": "facts_used is empty"})
    if not package.get("counter_evidence"):
        issues.append({"severity": "WARN", "code": "MISSING_COUNTER_EVIDENCE", "message": "counter_evidence is empty"})
    if not package.get("markdown_draft"):
        issues.append({"severity": "FAIL", "code": "MISSING_MARKDOWN_DRAFT", "message": "markdown_draft is empty"})

    hard_fail = any(issue["severity"] == "FAIL" for issue in issues)
    return {"passed": not hard_fail, "issues": issues}


def step_files_for_task(task_id: str, tasks_dir: Path = TASKS_DIR) -> list[Path]:
    tasks_dir = Path(tasks_dir)
    return sorted(path for path in tasks_dir.glob(f"{task_id}-step*.md") if path.is_file())


def section_sidecar_path_for_step_file(step_file: Path, task_id: str) -> Path:
    step_name = step_file.stem.replace(f"{task_id}-", "")
    return step_file.with_name(f"{task_id}-{step_name}-section.json")


def load_section_package_for_step(step_file: Path, task_id: str) -> tuple[dict[str, Any], str]:
    sidecar_path = section_sidecar_path_for_step_file(step_file, task_id)
    if sidecar_path.exists():
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload, str(sidecar_path)
        except json.JSONDecodeError:
            return {}, str(sidecar_path)
    return extract_section_package(step_file.read_text(encoding="utf-8")), str(step_file)


def write_section_package_index(task_id: str, tasks_dir: Path = TASKS_DIR) -> str:
    tasks_dir = Path(tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    packages = []
    passed = 0
    failed = 0

    for step_file in step_files_for_task(task_id, tasks_dir):
        package, package_source = load_section_package_for_step(step_file, task_id)
        validation = validate_section_package(package)
        if validation["passed"]:
            passed += 1
        else:
            failed += 1
        packages.append({
            "step_file": str(step_file),
            "step_name": step_file.stem.replace(f"{task_id}-", ""),
            "package_source": package_source,
            "package": package,
            "validation": validation,
        })

    payload = {
        "task_id": task_id,
        "summary": {"total": len(packages), "passed": passed, "failed": failed},
        "packages": packages,
    }
    output = tasks_dir / f"{task_id}-section_packages.json"
    from scripts.bp_file_lock import atomic_write
    atomic_write(output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return str(output)


def load_section_package_index(task_id: str, tasks_dir: Path = TASKS_DIR) -> dict[str, Any] | None:
    path = Path(tasks_dir) / f"{task_id}-section_packages.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
