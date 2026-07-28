import json

from scripts.bp_shared_page_builder import build_shared_state, write_shared_page_outputs


def test_bp_claim_with_only_gap_facts_stays_unverified(tmp_path):
    (tmp_path / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_research_plan.v2",
                "task_id": "BP-GAP",
                "entity": "测试公司",
                "claim_matrix": [
                    {
                        "claim_id": "BC005",
                        "claim": "客户、订单、收入可以被独立验证",
                        "owner_section": "bp_product_commercial",
                        "priority": "critical",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "bp_fact_store.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "BP-CUSTOMER-F001",
                        "claim": "未发现客户、订单、收入外部验证信息",
                        "source_tier": "database",
                        "confidence": "high",
                        "fact_type": "evidence_gap",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "bp_phase2_product_commercial-section.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_section_package.v2",
                "section_id": "bp_product_commercial",
                "section_title": "产品商业化",
                "key_messages": ["无外部商业化证据"],
                "claims": [
                    {
                        "claim_id": "BC005",
                        "claim": "客户、订单、收入可以被独立验证",
                        "fact_ids": ["BP-CUSTOMER-F001"],
                        "reasoning": "事实只证明没有找到外部证据，不能支持BP声称",
                        "confidence": "high",
                        "source_quality": "database",
                    }
                ],
                "facts_used": ["BP-CUSTOMER-F001"],
                "counter_evidence": ["无任何外部验证的客户、订单或收入"],
                "data_gaps": ["客户、订单、收入证明缺失"],
                "answers": [],
                "claim_ids_covered": ["BC005"],
                "narrative_blocks": [],
                "markdown_draft": "无外部商业化证据。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = write_shared_page_outputs(tmp_path, after_wave=2)

    claim = result["coverage"]["claims"][0]
    assert claim["status"] == "unverified"
    assert result["coverage"]["summary"]["supported"] == 0
    assert result["coverage"]["summary"]["critical_not_addressed"] == 1


def test_bp_claim_with_gap_then_positive_fact_remains_unverified_for_critical_claim(tmp_path):
    (tmp_path / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_research_plan.v2",
                "task_id": "BP-GAP-POSITIVE",
                "entity": "测试公司",
                "claim_matrix": [{"claim_id": "BC005", "claim": "客户订单收入可验证", "owner_section": "bp_product_commercial", "priority": "critical"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "bp_fact_store.json").write_text(
        json.dumps(
            {
                "facts": [
                    {"fact_id": "BP-CUSTOMER-F001", "claim": "未发现客户订单收入外部验证", "source_tier": "database", "fact_type": "evidence_gap"},
                    {"fact_id": "BP-CUSTOMER-F002", "claim": "公司工商状态正常", "source_tier": "official", "fact_type": "company_registration"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for name, fact_id, gaps in [
        ("bp_phase2_product_commercial-section.json", "BP-CUSTOMER-F001", ["客户合同缺失"]),
        ("bp_phase2_company_team_compliance-section.json", "BP-CUSTOMER-F002", []),
    ]:
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "section_id": name,
                    "claims": [{"claim_id": "BC005", "claim": "客户订单收入可验证", "fact_ids": [fact_id], "confidence": "high", "source_quality": "official"}],
                    "facts_used": [fact_id],
                    "counter_evidence": [],
                    "data_gaps": gaps,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    state = build_shared_state(tmp_path, after_wave=2)

    assert state["claim_status"]["BC005"]["status"] == "unverified"


def test_bp_claim_with_contradiction_is_not_overwritten_by_later_supported_fact(tmp_path):
    (tmp_path / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_research_plan.v2",
                "task_id": "BP-CONTRA-POSITIVE",
                "entity": "测试公司",
                "claim_matrix": [{"claim_id": "BC002", "claim": "产品已量产", "owner_section": "bp_product_commercial", "priority": "critical"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "bp_fact_store.json").write_text(
        json.dumps(
            {
                "facts": [
                    {"fact_id": "BP-PRODUCT-F001", "claim": "公开信息未见量产或交付证据", "source_tier": "database", "fact_type": "negative_evidence"},
                    {"fact_id": "BP-PRODUCT-F002", "claim": "公司存在产品介绍页", "source_tier": "official", "fact_type": "product_info"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for name, fact_id in [
        ("bp_phase2_product_commercial-section.json", "BP-PRODUCT-F001"),
        ("bp_phase2_market_supply_chain-section.json", "BP-PRODUCT-F002"),
    ]:
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "section_id": name,
                    "claims": [{"claim_id": "BC002", "claim": "产品已量产", "fact_ids": [fact_id], "confidence": "high", "source_quality": "official"}],
                    "facts_used": [fact_id],
                    "counter_evidence": [],
                    "data_gaps": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    state = build_shared_state(tmp_path, after_wave=2)

    assert state["claim_status"]["BC002"]["status"] == "contradicted"


def test_bp_claim_with_contradictory_evidence_is_contradicted(tmp_path):
    (tmp_path / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_research_plan.v2",
                "task_id": "BP-CONTRA",
                "entity": "测试公司",
                "claim_matrix": [
                    {"claim_id": "BC002", "claim": "产品已量产", "owner_section": "bp_product_commercial", "priority": "critical"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "bp_fact_store.json").write_text(
        json.dumps({"facts": [{"fact_id": "BP-PRODUCT-F001", "claim": "公开信息未见量产或交付证据", "source_tier": "database", "fact_type": "negative_evidence"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "bp_phase2_product_commercial-section.json").write_text(
        json.dumps(
            {
                "schema_version": "bp_section_package.v2",
                "section_id": "bp_product_commercial",
                "section_title": "产品商业化",
                "key_messages": ["未见量产证据"],
                "claims": [{"claim_id": "BC002", "claim": "产品已量产", "fact_ids": ["BP-PRODUCT-F001"], "reasoning": "负向证据反驳该声称", "confidence": "high", "source_quality": "database"}],
                "facts_used": ["BP-PRODUCT-F001"],
                "counter_evidence": ["未见量产或交付证据"],
                "data_gaps": [],
                "answers": [],
                "claim_ids_covered": ["BC002"],
                "narrative_blocks": [],
                "markdown_draft": "未见量产证据。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = build_shared_state(tmp_path, after_wave=2)

    assert state["claim_status"]["BC002"]["status"] == "contradicted"
