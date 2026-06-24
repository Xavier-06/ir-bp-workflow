from scripts.bp_readability_reviewer import review_bp_readability


def test_readability_fails_fact_id_leakage_in_main_body(tmp_path):
    report = tmp_path / "bp_final_report.md"
    report.write_text(
        "\n".join(
            [
                "# 测试公司 BP尽调报告",
                "",
                "## 1. 投资结论",
                "",
                "- 当前建议：observe",
                "- 置信度：low",
                "- 关键支持理由：暂无",
                "- Deal Breakers：商业化验证缺失",
                "- 下一步 DD：补客户合同",
                "- BP claim：BC001, BC002, BC003",
                "- 外部 fact：BP-COMPANY-F001, BP-CUSTOMER-F002, BP-TECH-F003, BP-MARKET-F004",
                "- 反证/缺口：缺客户证据",
                "- 投资影响：不得推进。",
                "",
                "## 附录：事实引用清单",
                "- BP-COMPANY-F001: 工商信息",
            ]
        ),
        encoding="utf-8",
    )

    result = review_bp_readability(report)

    assert result["verdict"] == "FAIL"
    assert any(issue["code"] == "MACHINE_ID_LEAKAGE" for issue in result["issues"])


def test_readability_fails_extremely_long_bullet(tmp_path):
    report = tmp_path / "bp_final_report.md"
    report.write_text(
        "\n".join(
            [
                "# 测试公司 BP尽调报告",
                "",
                "## 1. 投资结论",
                "",
                "- 当前建议：observe",
                "- 置信度：low",
                "- 关键支持理由：暂无",
                "- Deal Breakers：商业化验证缺失",
                "- 下一步 DD：补客户合同",
                "- 反证/缺口：" + "客户证据缺失；" * 120,
                "- 投资影响：不得推进。",
            ]
        ),
        encoding="utf-8",
    )

    result = review_bp_readability(report)

    assert result["verdict"] == "FAIL"
    assert any(issue["code"] == "OVERLONG_BULLET" for issue in result["issues"])
