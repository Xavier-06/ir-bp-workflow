"""归一化器回归测试（2026-08-06，TASK-20260805-003 实战 bug 全复现）。"""
import json

from scripts.ir_sidecar_normalize import (
    SECTION_SCHEMA_VERSION,
    normalize_facts_sidecar,
    normalize_section_sidecar,
)


def test_bare_list_root_wrapped():
    """bug①：facts 根节点是裸 list → 包成标准 dict。"""
    root = [{"fact_id": "F1", "claim": "净利 46.57 亿", "value": "46.57",
             "source_url": "https://x.com", "source_quote": "净利 46.57 亿"}]
    out, changes = normalize_facts_sidecar(root, "step6_valuation")
    assert isinstance(out, dict)
    assert out["step"] == "step6_valuation"
    assert len(out["facts"]) == 1
    assert "root_wrapped" in changes


def test_statement_claim_fallback():
    """bug③：step3 用 statement 而非 claim。"""
    root = {"step": "step3_finance", "facts": [
        {"fact_id": "F1", "statement": "营收 525 亿", "value": "525",
         "source_url": "https://x.com", "source_quote": "营收 525 亿"}]}
    out, changes = normalize_facts_sidecar(root, "step3_finance")
    assert out["facts"][0]["claim"] == "营收 525 亿"
    assert "claim_fallback" in changes


def test_source_quote_recovered_from_md():
    """bug②：缺 source_quote 时从 md 报告原文真实找回（不编造）。"""
    md_lines = ["铜价约 13800 美元每吨，处于历史高位", "光纤价格双口径"]
    root = {"facts": [{"fact_id": "F1", "claim": "铜价 13800 美元", "value": "13800",
                        "source_url": "https://x.com"}]}
    out, changes = normalize_facts_sidecar(root, "step5_macro", md_lines=md_lines)
    assert out["facts"][0]["source_quote"] == "铜价约 13800 美元每吨，处于历史高位"
    assert "source_quote_from_md" in changes


def test_source_quote_from_url_fallback():
    """v3.6+ 下午：md 找不到出处但 source_url 存在 → 用真实 URL 兜底（非编造）。

    TASK-20260806-002 实战：子代理 facts 普遍只有 source_url、无引用句，
    旧版留空导致 merge 一次 invalid 34 条。source_url 本身是真实出处，
    作最后兜底合法，traceability 链保留。
    """
    root = {"facts": [{"fact_id": "F1", "claim": "铜价 13800 美元", "value": "13800",
                        "source_url": "https://x.com"}]}
    out, changes = normalize_facts_sidecar(root, "step5_macro", md_lines=[])
    assert out["facts"][0].get("source_quote", "") == "https://x.com"
    assert "source_quote_from_url" in changes
    assert "source_quote_from_md" not in changes


def test_source_quote_empty_when_no_source_at_all():
    """traceability 保护：既无 md 出处又无 source_url 时才留空（交 gate 拒收），不编造。"""
    root = {"facts": [{"fact_id": "F1", "claim": "铜价 13800 美元", "value": "13800"}]}
    out, changes = normalize_facts_sidecar(root, "step5_macro", md_lines=[])
    assert out["facts"][0].get("source_quote", "") == ""
    assert "source_quote_from_md" not in changes
    assert "source_quote_from_url" not in changes


def test_value_not_fabricated_from_claim():
    """traceability 保护：claim 有数字但 value 空 → 留空让 gate 拒收，不抽数编造。"""
    root = {"facts": [{"fact_id": "F1", "claim": "毛利率 35%", "source_url": "https://x.com",
                        "source_quote": "毛利率35%"}]}
    out, _ = normalize_facts_sidecar(root, "step4_finance", md_lines=[])
    assert out["facts"][0].get("value", "") == ""


def test_qualitative_value_fallback():
    """bug④：定性事件（claim 无数字）缺 value → 标 qualitative。"""
    root = {"facts": [{"fact_id": "F1", "claim": "FCC 拟禁止进口中国光模块",
                        "source_url": "https://x.com", "source_quote": "FCC 提案"}]}
    out, changes = normalize_facts_sidecar(root, "step1_industry")
    assert out["facts"][0]["value"] == "qualitative"
    assert out["facts"][0]["fact_type"] == "qualitative_event"


def test_section_schema_version_fixed():
    """bug⑥：schema_version 1.0 → ir_section_package.v1。"""
    root = {"schema_version": "1.0", "step": "step6_valuation",
            "section_title": "估值", "claims": [], "facts_used": []}
    out, changes = normalize_section_sidecar(root, "step6_valuation")
    assert out["schema_version"] == SECTION_SCHEMA_VERSION
    assert "schema_version_fixed" in changes


def test_section_claims_str_to_dict():
    """bug⑤：claims str 数组 → dict 数组。"""
    root = {"schema_version": SECTION_SCHEMA_VERSION, "step": "step6_valuation",
            "section_title": "估值",
            "claims": ["目标价 49 元", "SOTP 38.9 元"], "facts_used": ["F1"]}
    out, changes = normalize_section_sidecar(root, "step6_valuation")
    assert all(isinstance(c, dict) for c in out["claims"])
    assert out["claims"][0]["claim"] == "目标价 49 元"
    assert "claims_str_to_dict" in changes


def test_section_markdown_draft_from_key_messages():
    """step8 缺 markdown_draft 但 key_messages 完整 → 聚合兜底。"""
    root = {"schema_version": SECTION_SCHEMA_VERSION, "step": "step8_risk",
            "section_title": "风险", "claims": [], "facts_used": [],
            "key_messages": ["最大风险是斜率", "铜价是唯一一阶宏观风险"]}
    out, changes = normalize_section_sidecar(root, "step8_risk")
    assert "最大风险是斜率" in out["markdown_draft"]
    assert "markdown_draft_from_key_messages" in changes


def test_facts_used_from_claims():
    """facts_used 缺失 → 从 claims 的 fact_ids 并集兜底。"""
    root = {"schema_version": SECTION_SCHEMA_VERSION, "step": "step6_valuation",
            "section_title": "估值",
            "claims": [{"claim": "目标价 49 元", "fact_ids": ["F1", "F2"]},
                       {"claim": "SOTP 38.9 元", "fact_ids": ["F2", "F3"]}]}
    out, _ = normalize_section_sidecar(root, "step6_valuation")
    assert out["facts_used"] == ["F1", "F2", "F3"]


def test_idempotent():
    """归一两次结果一致（幂等）。"""
    root = [{"fact_id": "F1", "claim": "净利 46.57 亿", "value": "46.57",
             "source_url": "https://x.com", "source_quote": "净利 46.57 亿"}]
    out1, _ = normalize_facts_sidecar(root, "step6_valuation")
    out2, changes2 = normalize_facts_sidecar(out1, "step6_valuation")
    assert out1 == out2
    assert "root_wrapped" not in changes2


def test_merge_aggregates_failed_steps(tmp_path):
    """bug 报错聚合：merge 失败按 step 聚合全量清单。"""
    from scripts.ir_fact_store import merge_step_fact_sidecars

    tid = "TASK-NORM-TEST"
    # 一个合法 sidecar
    good = {"schema_version": "1.0", "step": "step1_industry", "facts": [
        {"fact_id": "F1", "claim": "营收 525 亿", "value": "525",
         "source_url": "https://x.com/a", "source_quote": "营收 525 亿"}]}
    (tmp_path / f"{tid}-step1_industry-facts.json").write_text(
        json.dumps(good, ensure_ascii=False), encoding="utf-8")
    # 一个裸 list（归一化器会救回）
    bare = [{"fact_id": "F2", "claim": "净利 46.57 亿", "value": "46.57",
             "source_url": "https://x.com/b", "source_quote": "净利 46.57 亿"}]
    (tmp_path / f"{tid}-step6_valuation-facts.json").write_text(
        json.dumps(bare, ensure_ascii=False), encoding="utf-8")

    result = merge_step_fact_sidecars(tid, tasks_dir=tmp_path, entity="测试", market="cn")
    # 归一化后两个 sidecar 都应合并成功，无失败 step
    assert result["merged_count"] == 2
    assert result["failed_step_count"] == 0
    assert result["failed_steps"] == []
