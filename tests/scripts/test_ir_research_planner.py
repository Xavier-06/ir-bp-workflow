"""Contract tests for ir_research_planner.

v3.2 (2026-08-04): 骨架生成已删除，research plan 由 phase04 子代理全权产出。
本文件只测保留的契约工具：normalize_research_plan_contract / validate_research_plan_ready /
research_plan_path / load_research_plan。用手工构造的合法 plan 做夹具。
"""
import json
from pathlib import Path

from scripts.ir_research_planner import (
    load_research_plan,
    normalize_research_plan_contract,
    research_plan_path,
    validate_research_plan_ready,
)


def _valid_plan() -> dict:
    return {
        "schema_version": "ir_research_plan.v5",
        "task_id": "TASK-TEST",
        "entity": "泡泡玛特",
        "market": "hk",
        "plan_status": "ready",
        "core_questions": [
            {
                "question_id": "Q1",
                "question": "基本面是否支撑投资主线？",
                "priority": "high",
                "owner_section": "step3_finance",
                "supporting_sections": ["step1_industry"],
                "required_fact_keys": ["revenue_trend", "profitability"],
                "decision_relevance": "决定收入预测方向。",
            },
        ],
        "strategic_questions": [
            {
                "question_id": "SQ1",
                "question": "估值上行和下行由什么触发？",
                "priority": "high",
                "owner_section": "step6_valuation",
                "required_fact_keys": ["valuation_multiples", "sensitivity"],
                "decision_relevance": "让估值可复算。",
            },
        ],
        "section_requirements": {
            "step3_finance": {
                "must_answer": ["Q1"],
                "required_fact_keys": ["revenue_trend", "profitability"],
                "required_outputs": ["claims", "facts_used", "data_gaps", "markdown_draft"],
            },
            "step6_valuation": {
                "must_answer": ["SQ1"],
                "required_fact_keys": ["valuation_multiples", "sensitivity"],
                "required_outputs": ["claims", "facts_used", "data_gaps", "markdown_draft"],
            },
        },
        "fact_requirements": [
            {"fact_key": "revenue_trend", "description": "收入", "source_priority": ["annual_report"], "required_for": ["step3_finance"], "criticality": "high"},
            {"fact_key": "profitability", "description": "利润率", "source_priority": ["annual_report"], "required_for": ["step3_finance"], "criticality": "high"},
            {"fact_key": "valuation_multiples", "description": "估值倍数", "source_priority": ["market_data"], "required_for": ["step6_valuation"], "criticality": "high"},
            {"fact_key": "sensitivity", "description": "敏感性", "source_priority": ["market_data"], "required_for": ["step6_valuation"], "criticality": "medium"},
        ],
        "coverage_matrix": {
            "Q1": {"owner": "step3_finance", "supporting_sections": ["step1_industry"], "required_fact_keys": ["revenue_trend", "profitability"]},
            "SQ1": {"owner": "step6_valuation", "supporting_sections": [], "required_fact_keys": ["valuation_multiples", "sensitivity"]},
        },
    }


def test_valid_subagent_plan_passes_validation():
    result = validate_research_plan_ready(_valid_plan())
    assert result["ready"] is True
    assert result["errors"] == []


def test_missing_strategic_questions_blocked():
    plan = _valid_plan()
    plan["strategic_questions"] = []
    result = validate_research_plan_ready(plan)
    assert result["ready"] is False
    assert "strategic_questions_missing" in result["errors"]


def test_non_ready_status_blocked():
    plan = _valid_plan()
    plan["plan_status"] = "blocked"
    result = validate_research_plan_ready(plan)
    assert result["ready"] is False
    assert "plan_status_not_ready" in result["errors"]


def test_invalid_owner_section_blocked():
    plan = _valid_plan()
    plan["strategic_questions"][0]["owner_section"] = "step_unknown"
    result = validate_research_plan_ready(plan)
    assert result["ready"] is False
    assert "strategic_questions_owner_section_invalid" in result["errors"]


def test_undefined_fact_keys_blocked():
    plan = _valid_plan()
    plan["strategic_questions"][0]["required_fact_keys"] = ["not_a_fact_key"]
    result = validate_research_plan_ready(plan)
    assert result["ready"] is False
    assert any(e.startswith("undefined_fact_keys") for e in result["errors"])


def test_normalize_accepts_legacy_status_and_string_questions():
    plan = _valid_plan()
    plan.pop("plan_status")
    plan["status"] = "ready"
    plan["strategic_questions"] = ["海外增长是否可持续？", plan["strategic_questions"][0]]

    normalized = normalize_research_plan_contract(plan)
    result = validate_research_plan_ready(normalized)

    assert normalized["plan_status"] == "ready"
    assert all(isinstance(item, dict) for item in normalized["strategic_questions"])
    assert normalized["strategic_questions"][0]["question"] == "海外增长是否可持续？"
    assert result["ready"] is True


def test_normalize_core_questions_all_strings_no_crash():
    """回归 TASK-20260806-002 上午 bug：core_questions 全是字符串时
    validate 曾在 question.get(...) 抛 AttributeError（'str' has no attribute 'get'）。
    归一化器必须把纯字符串列表补成 dict（带 owner_section + required_fact_keys）。"""
    plan = _valid_plan()
    # 完全模拟子代理产出：core / strategic 都是纯字符串列表
    plan["core_questions"] = ["光纤涨价周期能持续多久？", "光通信毛利率到底是 45% 还是 26%？"]
    plan["strategic_questions"] = ["海缆在手订单能否按期转化？"]

    normalized = normalize_research_plan_contract(plan)
    result = validate_research_plan_ready(normalized)

    # 无 AttributeError，且全部转为 dict
    assert all(isinstance(item, dict) for item in normalized["core_questions"])
    assert all(isinstance(item, dict) for item in normalized["strategic_questions"])
    # 补上 owner_section / required_fact_keys（默认兜底）
    assert normalized["core_questions"][0]["owner_section"]
    assert normalized["core_questions"][0]["required_fact_keys"]
    assert result["ready"] is True


def test_research_plan_path_and_load_roundtrip(tmp_path):
    path = research_plan_path("TASK-RT", tmp_path)
    assert path == tmp_path / "TASK-RT-research_plan.json"

    plan = _valid_plan()
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    loaded = load_research_plan("TASK-RT", tmp_path)
    assert loaded is not None
    assert loaded["entity"] == "泡泡玛特"

    assert load_research_plan("TASK-MISSING", tmp_path) is None
