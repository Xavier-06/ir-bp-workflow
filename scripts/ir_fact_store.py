#!/usr/bin/env python3
"""Generic IR Fact Store.

Stores traceable facts for any investment research target. The module is
entity-parameterized and intentionally contains no company-specific rules.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "data" / "tasks"

URL_RE = re.compile(r"https?://[^\s\]）)]+")
VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿元|亿美元|亿港元|万元|美元|港元|元|%)")


@dataclass
class Fact:
    fact_id: str
    entity: str
    claim: str
    value: str
    unit: str
    period: str
    source_url: str
    source_tier: str
    source_quote: str
    question_id: str
    fact_type: str
    confidence: str = "medium"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    used_by_sections: list[str] = field(default_factory=list)


@dataclass
class Conflict:
    conflict_id: str
    claim_topic: str
    values: list[dict[str, Any]]
    resolution: str = ""
    reason: str = ""


@dataclass
class FactStore:
    task_id: str
    entity: str
    market: str = "generic"
    facts: list[Fact] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "entity": self.entity,
            "market": self.market,
            "created_at": self.created_at,
            "facts": [asdict(fact) for fact in self.facts],
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
        }


def _next_fact_id(store: FactStore) -> str:
    return f"F-{len(store.facts) + 1:04d}"


def add_fact(
    store: FactStore,
    claim: str,
    value: str,
    unit: str,
    period: str,
    source_url: str,
    source_tier: str,
    source_quote: str,
    question_id: str,
    fact_type: str,
    confidence: str = "medium",
) -> Fact:
    fact = Fact(
        fact_id=_next_fact_id(store),
        entity=store.entity,
        claim=claim,
        value=value,
        unit=unit,
        period=period,
        source_url=source_url,
        source_tier=source_tier,
        source_quote=source_quote,
        question_id=question_id,
        fact_type=fact_type,
        confidence=confidence,
    )
    store.facts.append(fact)
    return fact


def extract_fact_candidates(text: str, entity: str, question_id: str = "", fact_type: str = "numeric") -> list[dict[str, str]]:
    """Extract generic numeric fact candidates from text.

    This is intentionally conservative. It does not decide that a candidate is
    verified; it only captures value + nearby context + URL if present.
    """
    candidates: list[dict[str, str]] = []
    if not text:
        return candidates

    for match in VALUE_RE.finditer(text):
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 160)
        context = text[start:end].strip()
        urls = URL_RE.findall(context)
        value = match.group(0).replace(" ", "")
        unit_match = re.search(r"(亿元|亿美元|亿港元|万元|美元|港元|元|%)$", value)
        unit = unit_match.group(1) if unit_match else ""
        candidates.append({
            "entity": entity,
            "claim": context[:320],
            "value": value,
            "unit": unit,
            "period": _infer_period(context),
            "source_url": urls[0] if urls else "",
            "source_quote": context[:320],
            "question_id": question_id,
            "fact_type": fact_type,
            "confidence": "medium" if urls else "low",
        })
    return candidates


def _infer_period(context: str) -> str:
    period_patterns = [
        r"FY\s?20\d{2}",
        r"20\d{2}\s*年(?:度)?",
        r"20\d{2}\s*[QH][1-4]?",
        r"Q[1-4]\s*20\d{2}",
        r"H[12]\s*20\d{2}",
    ]
    for pattern in period_patterns:
        found = re.search(pattern, context, re.I)
        if found:
            return found.group(0)
    return ""


def fact_store_path(task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return Path(tasks_dir) / f"{task_id}-fact_store.json"


def fact_store_index_path(task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return Path(tasks_dir) / f"{task_id}-fact_store_index.json"


def step_fact_sidecar_path(task_id: str, step: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return Path(tasks_dir) / f"{task_id}-{step}-facts.json"


def write_fact_store(store: FactStore, tasks_dir: Path = TASKS_DIR) -> str:
    tasks_dir = Path(tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = fact_store_path(store.task_id, tasks_dir)
    from scripts.bp_file_lock import atomic_write
    atomic_write(path, json.dumps(store.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return str(path)


def load_fact_store(task_id: str, tasks_dir: Path = TASKS_DIR) -> FactStore | None:
    path = fact_store_path(task_id, tasks_dir)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    store = FactStore(task_id=payload["task_id"], entity=payload["entity"], market=payload.get("market", "generic"))
    store.created_at = payload.get("created_at", store.created_at)
    store.facts = [Fact(**item) for item in payload.get("facts", [])]
    store.conflicts = [Conflict(**item) for item in payload.get("conflicts", [])]
    return store


def _fact_dedupe_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("claim", "")).strip(),
        str(item.get("value", "")).strip(),
        str(item.get("period", "")).strip(),
        str(item.get("source_url", "")).strip(),
    )


def _normalize_sidecar_fact(raw: dict[str, Any], entity: str, fallback_step: str) -> Fact:
    required = ("fact_id", "claim", "value", "source_url", "source_quote")
    normalized = {field: str(raw.get(field, "")).strip() for field in required}
    missing = [field for field, value in normalized.items() if not value]
    if missing:
        raise ValueError(f"sidecar fact missing required fields: {','.join(missing)}")
    return Fact(
        fact_id=normalized["fact_id"],
        entity=str(raw.get("entity") or entity),
        claim=normalized["claim"],
        value=normalized["value"],
        unit=str(raw.get("unit", "")).strip(),
        period=str(raw.get("period", "")).strip(),
        source_url=normalized["source_url"],
        source_tier=str(raw.get("source_tier") or raw.get("source_quality") or "unknown"),
        source_quote=normalized["source_quote"],
        question_id=str(raw.get("question_id", "")).strip(),
        fact_type=str(raw.get("fact_type", "step_sidecar")).strip(),
        confidence=str(raw.get("confidence", "medium")).strip() or "medium",
        used_by_sections=[fallback_step],
    )


def write_fact_store_index(store: FactStore, tasks_dir: Path = TASKS_DIR) -> str:
    tasks_dir = Path(tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "task_id": store.task_id,
        "entity": store.entity,
        "market": store.market,
        "total_facts": len(store.facts),
        "fact_ids": [fact.fact_id for fact in store.facts],
        "facts_by_type": {},
        "facts_by_source_tier": {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    for fact in store.facts:
        index["facts_by_type"][fact.fact_type] = index["facts_by_type"].get(fact.fact_type, 0) + 1
        index["facts_by_source_tier"][fact.source_tier] = index["facts_by_source_tier"].get(fact.source_tier, 0) + 1
    path = fact_store_index_path(store.task_id, tasks_dir)
    from scripts.bp_file_lock import atomic_write
    atomic_write(path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return str(path)


def merge_step_fact_sidecars(task_id: str, tasks_dir: Path = TASKS_DIR,
                             entity: str = "", market: str = "generic") -> dict[str, Any]:
    tasks_dir = Path(tasks_dir)
    store = load_fact_store(task_id, tasks_dir)
    if store is None:
        store = FactStore(task_id=task_id, entity=entity, market=market)
    elif entity and not store.entity:
        store.entity = entity
    if market and store.market == "generic":
        store.market = market

    seen = {_fact_dedupe_key(asdict(fact)) for fact in store.facts}
    fact_ids = {fact.fact_id for fact in store.facts}
    merged_count = 0
    duplicate_count = 0
    invalid: list[dict[str, str]] = []
    sidecar_paths = sorted(tasks_dir.glob(f"{task_id}-*-facts.json"))

    for path in sidecar_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid.append({"path": str(path), "error": str(exc)})
            continue
        step = str(payload.get("step") or path.name.replace(f"{task_id}-", "").replace("-facts.json", ""))
        for raw in payload.get("facts", []) or []:
            try:
                fact = _normalize_sidecar_fact(raw, store.entity or entity, step)
            except Exception as exc:
                invalid.append({"path": str(path), "error": str(exc)})
                continue
            key = _fact_dedupe_key(asdict(fact))
            if key in seen or fact.fact_id in fact_ids:
                duplicate_count += 1
                continue
            seen.add(key)
            fact_ids.add(fact.fact_id)
            store.facts.append(fact)
            merged_count += 1

    output_path = write_fact_store(store, tasks_dir=tasks_dir)
    index_path = write_fact_store_index(store, tasks_dir=tasks_dir)
    return {
        "task_id": task_id,
        "output_path": output_path,
        "index_path": index_path,
        "sidecar_count": len(sidecar_paths),
        "merged_count": merged_count,
        "duplicate_count": duplicate_count,
        "invalid_count": len(invalid),
        "invalid": invalid,
        "total_facts": len(store.facts),
    }
