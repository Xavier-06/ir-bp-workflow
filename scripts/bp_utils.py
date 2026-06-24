"""BP 管线公共工具函数 — 消除多文件重复定义。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    """统一的 JSON 文件加载。

    文件不存在或解析失败时返回 default（默认 None）。
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_attempt_count(path: Path) -> int:
    """读取 gate JSON 文件的 attempt 字段（统一 3 处重复的 _read_gate_attempt / _read_claim_gate_attempt / _read_synthesis_attempt）。

    文件不存在或解析失败时返回 0。
    """
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("attempt", 0) or 0)
    except Exception:
        return 0


def load_sidecar_facts(*dirs: Path) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    """从多个目录加载 BP sidecar facts 文件（bp_*-facts.json）。

    返回 (facts, source_files, malformed_source_files)。
    与 bp_profile.py 的 _load_bp_sidecar_facts 接口对齐。
    """
    facts: list[dict[str, Any]] = []
    source_files: list[str] = []
    malformed_source_files: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    seen_paths: set[str] = set()

    sidecar_paths: list[Path] = []
    for directory in dirs:
        for path in sorted(Path(directory).glob("bp_*-facts.json")):
            path_key = str(path.resolve())
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            sidecar_paths.append(path)

    for path in sidecar_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            malformed_source_files.append({"path": str(path), "error": "JSON parse failed"})
            continue

        source_files.append(str(path))
        for raw in payload.get("facts", []) or []:
            fact = dict(raw)
            key = (
                str(fact.get("fact_id", "")).strip(),
                str(fact.get("claim", "")).strip(),
                str(fact.get("value", "")).strip(),
                str(fact.get("period", "")).strip(),
                str(fact.get("source_url", "")).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            facts.append(fact)

    return facts, source_files, malformed_source_files
