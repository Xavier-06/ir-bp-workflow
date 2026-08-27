"""BP 管线公共工具函数 — 消除多文件重复定义。"""
from __future__ import annotations

import json
import re
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


# ── 维度角色中文名（数据驱动，单一真实来源 = instruction_store_bp/index.json）──
_ROLE_LABEL_CACHE: dict[str, str] | None = None


def _default_store_index_path() -> Path:
    return Path(__file__).resolve().parent.parent / "instruction_store_bp" / "index.json"


def _slug_from_instruction_file(file: str) -> str:
    """bp_r01_company_team_compliance.md → company_team_compliance。"""
    stem = Path(file).stem
    match = re.match(r"^bp_[a-z]\d+_(.+)$", stem, re.IGNORECASE)
    return match.group(1) if match else stem


def load_role_label_map(store_index_path: Path | None = None) -> dict[str, str]:
    """从 instruction_store_bp/index.json 构建 slug → 中文角色名映射。

    数据驱动：角色中文名直接取 index.json 的 roles[].name（剥离"分析师"后缀），
    避免在多个脚本里各自硬编码映射表。加载失败或文件缺失时返回空 dict（调用方走兜底）。
    """
    global _ROLE_LABEL_CACHE
    if _ROLE_LABEL_CACHE is not None and store_index_path is None:
        return _ROLE_LABEL_CACHE

    index_path = Path(store_index_path) if store_index_path else _default_store_index_path()
    index = load_json(index_path, None)
    mapping: dict[str, str] = {}
    if isinstance(index, dict):
        for role in index.get("roles", []) or []:
            if not isinstance(role, dict):
                continue
            file = str(role.get("file", "") or "")
            name = str(role.get("name", "") or "").strip()
            if not file or not name:
                continue
            slug = _slug_from_instruction_file(file)
            # 剥离"分析师"后缀得到对外可读的模块名
            label = re.sub(r"分析师$", "", name).strip()
            if slug and label:
                mapping[slug] = label

    if store_index_path is None:
        _ROLE_LABEL_CACHE = mapping
    return mapping


def dimension_label(slug: str, store_index_path: Path | None = None) -> str:
    """把维度 slug 转为中文角色名（数据驱动）。

    优先查 index.json 映射；查不到时用通用的下划线转标题兜底，保证任何
    新维度都不会泄漏裸英文 slug 进成品。
    """
    clean = re.sub(r"^bp[_-]", "", str(slug or "").strip())
    label_map = load_role_label_map(store_index_path)
    if clean in label_map:
        return label_map[clean]
    return clean.replace("_", " ").title() if clean else slug


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
