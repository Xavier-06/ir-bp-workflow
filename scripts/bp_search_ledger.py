#!/usr/bin/env python3
"""Central BP search execution ledger.

The ledger is intentionally simple: each query attempt is preserved as an event,
while URL lists inside each event are normalized and deduplicated for auditability.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LEDGER_NAME = "bp_search_ledger.json"


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    if not isinstance(values, list):
        return output
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _domain(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return parsed.netloc.lower()
    if "://" in url:
        return url.split("://", 1)[1].split("/", 1)[0].lower()
    return ""


def _domains(urls: list[str]) -> list[str]:
    return _unique_strings([_domain(url) for url in urls if _domain(url)])


def _empty_ledger() -> dict[str, Any]:
    return {
        "schema_version": "bp_search_ledger.v1",
        "events": [],
    }


def load_search_ledger(task_dir: Path) -> dict[str, Any]:
    path = task_dir / LEDGER_NAME
    if not path.exists():
        return _empty_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_ledger()
    if not isinstance(payload, dict):
        return _empty_ledger()
    payload.setdefault("schema_version", "bp_search_ledger.v1")
    payload.setdefault("events", [])
    if not isinstance(payload["events"], list):
        payload["events"] = []
    return payload


def append_search_event(task_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    task_dir.mkdir(parents=True, exist_ok=True)
    ledger = load_search_ledger(task_dir)
    result_urls = _unique_strings(event.get("result_urls"))
    fetched_urls = _unique_strings(event.get("fetched_urls"))
    source_domains = _unique_strings(event.get("source_domains")) or _domains(fetched_urls or result_urls)
    normalized = {
        "search_task_id": str(event.get("search_task_id") or ""),
        "role": str(event.get("role") or ""),
        "claim_id": str(event.get("claim_id") or ""),
        "query": str(event.get("query") or ""),
        "engine": str(event.get("engine") or "unknown"),
        "result_count": int(event.get("result_count") or len(result_urls)),
        "result_urls": result_urls,
        "fetched_urls": fetched_urls,
        "source_domains": source_domains,
        "fetch_status": event.get("fetch_status") or ("fetched" if fetched_urls else "not_fetched"),
        "used_fact_ids": _unique_strings(event.get("used_fact_ids")),
        "timestamp": event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }
    ledger["events"].append(normalized)
    (task_dir / LEDGER_NAME).write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ledger


def _blank_bucket() -> dict[str, Any]:
    return {
        "search_task_ids": [],
        "unique_queries": 0,
        "fetched_url_count": 0,
        "source_domain_count": 0,
        "queries": [],
        "fetched_urls": [],
        "source_domains": [],
    }


def _add_to_bucket(bucket: dict[str, Any], *, search_task_ids: list[str], queries: list[str], fetched_urls: list[str], source_domains: list[str]) -> None:
    bucket["search_task_ids"] = _unique_strings(bucket.get("search_task_ids", []) + search_task_ids)
    bucket["queries"] = _unique_strings(bucket.get("queries", []) + queries)
    bucket["fetched_urls"] = _unique_strings(bucket.get("fetched_urls", []) + fetched_urls)
    bucket["source_domains"] = _unique_strings(bucket.get("source_domains", []) + source_domains)
    bucket["unique_queries"] = len(bucket["queries"])
    bucket["fetched_url_count"] = len(bucket["fetched_urls"])
    bucket["source_domain_count"] = len(bucket["source_domains"])


def _load_section_index(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "bp_section_packages.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _summary_from_agent_reported(task_dir: Path, role: str | None = None) -> dict[str, Any]:
    summary = {"audit_source": "agent_reported", "by_role": {}, "by_claim": {}}
    section_index = _load_section_index(task_dir)
    for item in section_index.get("packages", []) or []:
        if not isinstance(item, dict):
            continue
        package = item.get("package") or {}
        if not isinstance(package, dict):
            continue
        section_id = str(package.get("section_id") or item.get("section_name") or "")
        if role and section_id != role:
            continue
        role_bucket = summary["by_role"].setdefault(section_id, _blank_bucket())
        audit = package.get("search_audit") or {}
        if not isinstance(audit, dict):
            continue
        for coverage in audit.get("claim_coverage") or []:
            if not isinstance(coverage, dict):
                continue
            claim_id = str(coverage.get("claim_id") or "")
            if not claim_id:
                continue
            fetched_urls = _unique_strings(coverage.get("fetched_urls"))
            source_domains = _unique_strings(coverage.get("source_domains")) or _domains(fetched_urls)
            search_task_ids = _unique_strings(coverage.get("search_task_ids"))
            query_count = int(coverage.get("unique_queries") or 0)
            queries = [f"agent_reported_query_{idx}" for idx in range(1, query_count + 1)]
            claim_bucket = summary["by_claim"].setdefault(claim_id, _blank_bucket())
            _add_to_bucket(claim_bucket, search_task_ids=search_task_ids, queries=queries, fetched_urls=fetched_urls, source_domains=source_domains)
            _add_to_bucket(role_bucket, search_task_ids=search_task_ids, queries=queries, fetched_urls=fetched_urls, source_domains=source_domains)
    return summary


def summarize_search_coverage(task_dir: Path, role: str | None = None) -> dict[str, Any]:
    ledger = load_search_ledger(task_dir)
    events = ledger.get("events", []) or []
    if not events:
        return _summary_from_agent_reported(task_dir, role=role)

    summary = {"audit_source": "central_ledger", "by_role": {}, "by_claim": {}}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_role = str(event.get("role") or "")
        if role and event_role != role:
            continue
        claim_id = str(event.get("claim_id") or "")
        query = str(event.get("query") or "")
        search_task_id = str(event.get("search_task_id") or "")
        fetched_urls = _unique_strings(event.get("fetched_urls"))
        source_domains = _unique_strings(event.get("source_domains")) or _domains(fetched_urls)
        role_bucket = summary["by_role"].setdefault(event_role, _blank_bucket())
        _add_to_bucket(role_bucket, search_task_ids=[search_task_id], queries=[query], fetched_urls=fetched_urls, source_domains=source_domains)
        if claim_id:
            claim_bucket = summary["by_claim"].setdefault(claim_id, _blank_bucket())
            _add_to_bucket(claim_bucket, search_task_ids=[search_task_id], queries=[query], fetched_urls=fetched_urls, source_domains=source_domains)
    return summary
