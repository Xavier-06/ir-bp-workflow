import json

from scripts.bp_claim_extractor import build_claim_inventory


def test_bp_claim_extractor_writes_inventory_from_ocr_profile_and_plan(tmp_path):
    task_dir = tmp_path / "tasks" / "BP-CLAIMS"
    task_dir.mkdir(parents=True)
    (task_dir / "bp_ocr_text.txt").write_text(
        "星河智能BP\n"
        "核心产品为AI康复机器人，已经与三家医院签署采购订单。\n"
        "2025年预计收入3000万元，当前完成Pre-A轮融资2000万元，投后估值2亿元。\n"
        "公司拥有12项发明专利，核心团队来自清华和腾讯。\n"
        "产品已取得二类医疗器械注册证，目标市场规模超过百亿元。\n",
        encoding="utf-8",
    )
    (task_dir / "bp_step0_profile.json").write_text(
        json.dumps({"task_id": "BP-CLAIMS", "entity": "星河智能"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "claim_matrix": [
                    {
                        "claim": "产品已取得二类医疗器械注册证",
                        "claim_type": "compliance",
                        "owner_section": "bp_company_team_compliance",
                        "priority": "critical",
                    },
                    {
                        "claim": "公司计划进入华东市场",
                        "claim_type": "market",
                        "owner_section": "bp_market_supply_chain",
                        "priority": "medium",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_claim_inventory(task_dir)

    json_path = task_dir / "bp_claim_inventory.json"
    md_path = task_dir / "bp_claim_inventory.md"
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert result == payload
    assert payload["schema_version"] == "bp_claim_inventory.v1"
    assert payload["task_id"] == "BP-CLAIMS"
    assert payload["entity"] == "星河智能"
    assert [claim["claim_id"] for claim in payload["claims"]] == [f"BC{i:03d}" for i in range(1, len(payload["claims"]) + 1)]
    claim_types = {claim["claim_type"] for claim in payload["claims"]}
    assert {"product", "customer", "revenue", "financing", "valuation", "patent", "team", "market", "compliance"} <= claim_types
    for claim in payload["claims"]:
        assert claim["claim"]
        assert claim["owner_section"]
        assert claim["priority"] in {"critical", "high", "medium", "low"}
        assert claim["source"] in {"bp_text", "step0_profile", "research_plan"}
        assert isinstance(claim["evidence_required"], list)
        assert claim["raw_excerpt"]

    md = md_path.read_text(encoding="utf-8")
    assert "# BP Claim Inventory - BP-CLAIMS" in md
    assert "| Claim ID | Priority | Type | Owner | Source | Claim | Evidence Required |" in md
    assert "公司计划进入华东市场" in md


def test_bp_claim_extractor_merges_research_plan_claim_matrix_without_duplicates(tmp_path):
    task_dir = tmp_path / "BP-DEDUPE"
    task_dir.mkdir()
    (task_dir / "bp_ocr_text.txt").write_text("公司已获得A医院采购订单。", encoding="utf-8")
    (task_dir / "bp_step0_profile.json").write_text(
        json.dumps({"task_id": "BP-DEDUPE", "entity": "测试公司"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "claim_matrix": [
                    {"claim_id": "OLD001", "claim": "公司已获得A医院采购订单", "claim_type": "customer", "owner_section": "bp_product_commercial", "priority": "critical"},
                    {"claim_id": "OLD002", "claim": "公司计划进入海外市场", "claim_type": "market", "owner_section": "bp_market_supply_chain", "priority": "medium"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = build_claim_inventory(task_dir)

    claims = payload["claims"]
    matching_order_claims = [claim for claim in claims if "A医院采购订单" in claim["claim"]]
    assert len(matching_order_claims) == 1
    assert any(claim["claim"] == "公司计划进入海外市场" and claim["source"] == "research_plan" for claim in claims)
    assert [claim["claim_id"] for claim in claims] == [f"BC{i:03d}" for i in range(1, len(claims) + 1)]


def test_bp_claim_extractor_keeps_more_specific_duplicate_claim(tmp_path):
    task_dir = tmp_path / "BP-SPECIFIC-DEDUPE"
    task_dir.mkdir()
    (task_dir / "bp_ocr_text.txt").write_text("公司已获得A医院采购订单。", encoding="utf-8")
    (task_dir / "bp_step0_profile.json").write_text(
        json.dumps({"task_id": "BP-SPECIFIC-DEDUPE", "entity": "测试公司"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "bp_research_plan.json").write_text(
        json.dumps(
            {
                "claim_matrix": [
                    {
                        "claim": "公司已获得A医院采购订单，合同金额500万元且2025年交付",
                        "claim_type": "customer",
                        "owner_section": "bp_product_commercial",
                        "priority": "critical",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = build_claim_inventory(task_dir)

    matching_order_claims = [claim for claim in payload["claims"] if "A医院采购订单" in claim["claim"]]
    assert len(matching_order_claims) == 1
    assert matching_order_claims[0]["claim"] == "公司已获得A医院采购订单，合同金额500万元且2025年交付"
