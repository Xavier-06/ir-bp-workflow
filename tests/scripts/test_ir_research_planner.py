import json
from pathlib import Path

from scripts.ir_research_planner import (
    build_research_plan,
    normalize_research_plan_contract,
    prepare_research_plan,
    validate_research_plan_ready,
    write_research_plan,
)


def test_build_research_plan_contains_investment_questions_and_evidence_needs():
    plan = build_research_plan(entity="阿里巴巴", query="分析阿里巴巴AI和云业务估值", market="us")

    assert plan["entity"] == "阿里巴巴"
    assert plan["market"] == "us"
    assert len(plan["investment_questions"]) >= 5
    question_ids = {q["id"] for q in plan["investment_questions"]}
    assert {"Q1", "Q2", "Q3", "Q4", "Q5"}.issubset(question_ids)
    for question in plan["investment_questions"]:
        assert question["question"]
        assert question["required_evidence"]
        assert question["preferred_sources"]
        assert question["minimum_sources"] >= 2


def test_build_research_plan_mvp_contract_has_section_and_fact_coverage():
    plan = build_research_plan(
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究，重点看海外增长和估值",
        market="hk",
        report_type="company_deep_dive",
    )

    assert plan["research_type"] == "company_deep_dive"
    assert len(plan["core_questions"]) >= 6

    core_question = plan["core_questions"][0]
    assert core_question["question_id"] == "Q1"
    assert core_question["owner_section"]
    assert core_question["required_fact_keys"]
    assert core_question["decision_relevance"]
    assert core_question["priority"] in {"high", "medium", "low"}

    section_requirements = plan["section_requirements"]
    assert "step1_data" in section_requirements
    assert "step3_finance" in section_requirements
    assert "step8_master" in section_requirements
    assert "Q1" in section_requirements["step1_data"]["must_answer"]
    assert "claims" in section_requirements["step1_data"]["required_outputs"]
    assert "data_gaps" in section_requirements["step1_data"]["required_outputs"]

    fact_requirements = {item["fact_key"]: item for item in plan["fact_requirements"]}
    assert "revenue_trend" in fact_requirements
    assert fact_requirements["revenue_trend"]["required_for"]
    assert fact_requirements["revenue_trend"]["source_priority"]

    coverage = plan["coverage_matrix"]
    assert coverage["Q1"]["owner"] == "step1_data"
    assert "revenue_trend" in coverage["Q1"]["required_fact_keys"]


def test_research_plan_fact_key_references_are_defined():
    plan = build_research_plan(entity="泡泡玛特", query="泡泡玛特公司深度研究", market="hk")

    defined_fact_keys = {item["fact_key"] for item in plan["fact_requirements"]}
    referenced_fact_keys = set()
    for question in plan["core_questions"]:
        referenced_fact_keys.update(question["required_fact_keys"])
    for section in plan["section_requirements"].values():
        referenced_fact_keys.update(section["required_fact_keys"])
    for coverage in plan["coverage_matrix"].values():
        referenced_fact_keys.update(coverage["required_fact_keys"])

    assert referenced_fact_keys <= defined_fact_keys


def test_research_plan_has_forbidden_rules_and_section_contract():
    plan = build_research_plan(entity="阿里巴巴", query="写券商版研报", market="us")

    assert "无来源数字" in plan["forbidden"]
    assert "模型训练记忆中的管理层信息" in plan["forbidden"]
    assert "section_package_schema" in plan
    assert "claims" in plan["section_package_schema"]["required_fields"]
    assert "facts_used" in plan["section_package_schema"]["required_fields"]


def test_write_research_plan_creates_json_file(tmp_path):
    output = write_research_plan(
        task_id="TASK-TEST",
        entity="阿里巴巴",
        query="写券商版研报",
        market="us",
        tasks_dir=tmp_path,
    )

    path = Path(output)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "TASK-TEST"
    assert payload["entity"] == "阿里巴巴"
    assert payload["schema_version"] == "ir_research_plan_mvp_v1"
    assert payload["section_requirements"]["step8_master"]["must_answer"]


def test_prepare_research_plan_adds_strategic_questions_and_ready_status(tmp_path):
    path = prepare_research_plan(
        task_id="TASK-POP",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究，重点看海外增长和估值",
        market="hk",
        tasks_dir=tmp_path,
    )

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["plan_status"] == "ready"
    assert payload["prepared_by"] == "script_scaffold_plus_orchestrator_enrichment"
    assert len(payload["strategic_questions"]) >= 3
    assert any("海外" in item["question"] for item in payload["strategic_questions"])
    assert all(item["owner_section"] for item in payload["strategic_questions"])
    assert all(item["required_fact_keys"] for item in payload["strategic_questions"])


def test_validate_research_plan_ready_blocks_missing_strategic_questions():
    plan = build_research_plan(entity="泡泡玛特", query="泡泡玛特公司深度研究", market="hk")

    result = validate_research_plan_ready(plan)

    assert result["ready"] is False
    assert "strategic_questions_missing" in result["errors"]


def test_validate_research_plan_ready_accepts_prepared_plan(tmp_path):
    path = prepare_research_plan(
        task_id="TASK-READY",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究，重点看海外增长和估值",
        market="hk",
        tasks_dir=tmp_path,
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    result = validate_research_plan_ready(payload)

    assert result["ready"] is True
    assert result["errors"] == []


def test_normalize_research_plan_contract_accepts_legacy_status_and_string_questions(tmp_path, monkeypatch):
    path = prepare_research_plan(
        task_id="TASK-LEGACY-CONTRACT",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究，重点看海外增长和估值",
        market="hk",
        tasks_dir=tmp_path,
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload.pop("plan_status")
    payload["status"] = "ready"
    payload["strategic_questions"] = ["海外增长是否可持续？", payload["strategic_questions"][0]]

    normalized = normalize_research_plan_contract(payload)
    result = validate_research_plan_ready(normalized)

    assert normalized["plan_status"] == "ready"
    assert all(isinstance(item, dict) for item in normalized["strategic_questions"])
    assert normalized["strategic_questions"][0]["question"] == "海外增长是否可持续？"
    assert result["ready"] is True
    assert result["errors"] == []

    import scripts.ir_subagent_launcher_wb as launcher

    plan_file = Path(path)
    plan_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(launcher, "TASKS_DIR", tmp_path)

    gate = launcher.ensure_research_plan_ready(
        "TASK-LEGACY-CONTRACT",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究，重点看海外增长和估值",
        market="hk",
    )
    persisted = json.loads(plan_file.read_text(encoding="utf-8"))

    assert gate["ready"] is True
    assert gate["plan_status"] == "ready"
    assert persisted["plan_status"] == "ready"
    assert all(isinstance(item, dict) for item in persisted["strategic_questions"])


def test_validate_research_plan_ready_blocks_non_ready_plan_status(tmp_path):
    path = prepare_research_plan(
        task_id="TASK-BLOCKED-STATUS",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究",
        market="hk",
        tasks_dir=tmp_path,
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["plan_status"] = "blocked"

    result = validate_research_plan_ready(payload)

    assert result["ready"] is False
    assert "plan_status_not_ready" in result["errors"]


def test_validate_research_plan_ready_blocks_invalid_strategic_question_owner(tmp_path):
    path = prepare_research_plan(
        task_id="TASK-BAD-OWNER",
        entity="泡泡玛特",
        query="泡泡玛特公司深度研究",
        market="hk",
        tasks_dir=tmp_path,
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["strategic_questions"][0]["owner_section"] = "step_unknown"

    result = validate_research_plan_ready(payload)

    assert result["ready"] is False
    assert "strategic_questions_owner_section_invalid" in result["errors"]
