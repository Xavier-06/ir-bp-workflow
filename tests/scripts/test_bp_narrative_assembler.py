import json
import re

from scripts.bp_narrative_assembler import assemble_bp_report


def _write_valid_task(task_dir):
    (task_dir / "bp_section_packages.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "validation": {"passed": True, "issues": []},
                        "package": {
                            "schema_version": "bp_section_package.v2",
                            "section_id": "bp_customer_revenue_validation",
                            "section_title": "客户收入验证",
                            "key_messages": ["无商业化证据"],
                            "claims": [{"claim_id": "BC005", "claim": "客户订单收入可验证", "fact_ids": ["BP-CUSTOMER-F001"], "reasoning": "未发现证据", "confidence": "high", "source_quality": "database"}],
                            "facts_used": ["BP-CUSTOMER-F001"],
                            "counter_evidence": ["无任何外部验证的客户、订单或收入"],
                            "data_gaps": ["客户名单缺失", "收入证明缺失"],
                            "answers": [{"question_id": "customer_revenue_validation_q1", "answer": "未发现客户、订单、收入外部验证信息，见 BP-CUSTOMER-F001 / BF-0001。", "fact_ids": ["BP-CUSTOMER-F001"], "confidence": "high", "limits": "可能存在未公开客户，claim_id: BC005。"}],
                            "claim_ids_covered": ["BC005"],
                            "narrative_blocks": [{"block_id": "B1", "question_id": "customer_revenue_validation_q1", "claim_ids": ["BC005"], "fact_ids": ["BP-CUSTOMER-F001"], "text": "商业化证据缺失，不能支撑收入相关声称，来源 BF-0001 / BC005。"}],
                            "markdown_draft": "无商业化证据。",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "bp_claim_coverage.json").write_text(
        json.dumps(
            {
                "summary": {"critical_not_addressed": 1, "supported": 0, "unverified": 1},
                "claims": [{"claim_id": "BC005", "claim": "客户订单收入可验证", "priority": "critical", "status": "unverified", "data_gaps": ["客户名单缺失"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "bp_debate_review.json").write_text(json.dumps({"verdict": "PASS"}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "bp_shared_state.json").write_text(json.dumps({"current_recommendation": {"verdict": "observe", "confidence": "low"}}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "bp_thesis_reconciliation.json").write_text(
        json.dumps({"recommendation": "observe", "confidence": "low", "supporting_reasons": [], "deal_breakers": ["商业化验证完全缺失"], "must_verify_before_investment": ["客户合同"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_fact_store.json").write_text(
        json.dumps({"facts": [{"fact_id": "BP-CUSTOMER-F001", "claim": "未发现客户、订单、收入外部验证信息", "source_url": "https://example.com", "source_tier": "database", "confidence": "high"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_assemble_bp_report_hides_machine_ids_from_main_body(tmp_path):
    _write_valid_task(tmp_path)

    result = assemble_bp_report(tmp_path, entity="测试公司")

    assert result["ok"] is True
    text = (tmp_path / "bp_final_report.md").read_text(encoding="utf-8")
    main_body = text.split("## 附录", 1)[0]
    assert "BP-CUSTOMER-F001" not in main_body
    assert "BF-0001" not in main_body
    assert "BC005" not in main_body
    assert "fact_ids:" not in main_body
    assert "claim_ids:" not in main_body
    assert "claim_id:" not in main_body
    assert "BP claim：BC005" not in main_body


def test_assemble_bp_report_does_not_emit_extremely_long_bullets(tmp_path):
    _write_valid_task(tmp_path)

    assemble_bp_report(tmp_path, entity="测试公司")
    text = (tmp_path / "bp_final_report.md").read_text(encoding="utf-8")

    bullet_lengths = [len(line) for line in text.splitlines() if line.startswith("- ")]
    assert max(bullet_lengths) < 700
    assert not re.search(r"(?:BP-[A-Z_]+-F\d{3,},\s*){3,}", text)
