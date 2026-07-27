#!/usr/bin/env python3
"""BP Company Intake Subagent handlers — Phase 01b (2026-07-20).

公司名模式入库：无 PDF 时，通过子代理搜索公开数据重建 BP 等效数据。
产出与 phase01_document_intake 相同格式的 bp_ocr_text.txt + bp_step0_profile.json。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def bp_build_company_intake_brief(
    entity: str,
    market: str,
    job_id: str,
) -> dict[str, Any]:
    """Build context brief for the company intake subagent."""
    return {
        "entity": entity,
        "market": market,
        "job_id": job_id,
        "input_mode": "company_name_only",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def bp_build_company_intake_instruction(
    entity: str,
    market: str,
    job_id: str,
    task_dir: Path,
    brief_path: Path,
) -> str:
    """Build the main-AI instruction for dispatching the company intake subagent."""

    ocr_path = task_dir / "bp_ocr_text.txt"
    profile_path = task_dir / "bp_step0_profile.json"
    meta_path = task_dir / "bp_company_intake_meta.json"
    runtime_root = task_dir.parent.parent

    instruction = (
        "PHASE01b BP COMPANY INTAKE — Dispatch Subagent\n"
        "\n"
        "## Dispatch\n"
        "\n"
        "Use the Agent tool to spawn a subagent that searches for company information:\n"
        "\n"
        "Agent tool params:\n"
        f"- name = 'bp-company-intake'\n"
        f"- team_name = 'bp-{job_id}'\n"
        "- mode = 'bypassPermissions'\n"
        "- connectorIds = ['tyc-mcp', 'westock-mcp', 'ima-mcp']\n"
        "- prompt = the FULL prompt below (do not truncate)\n"
        "\n"
        "### Subagent Prompt (copy ALL to the Agent):\n"
        "\n"
        "---\n"
        "\n"
        f"You are a company due diligence researcher. Your task is to gather comprehensive\n"
        f"information about **{entity}** using public data sources and produce structured output\n"
        f"files for downstream BP analysis.\n"
        "\n"
        "## Input\n"
        f"- Company name: **{entity}**\n"
        f"- Market: **{market}**\n"
        f"- Brief file: `{brief_path}`\n"
        "\n"
        "## Search Strategy (execute ALL 8 steps in order)\n"
        "\n"
        "### Step 1: 天眼查 — 深度工商扫描 (MANDATORY, 全量 API)\n"
        "Use ALL available tyc-mcp tools (not just basic_profile):\n"
        f'1. `tyc-mcp.search_companies` query="{entity}" → company_id\n'
        "2. If found, call ALL of these with company_id:\n"
        "   - `tyc-mcp.get_company_basic_profile` — 注册信息、法人、经营范围\n"
        "   - `tyc-mcp.get_key_personnel` — 董监高团队\n"
        "   - `tyc-mcp.get_shareholder_info` — 股权结构\n"
        "   - `tyc-mcp.get_change_records` — 变更记录（判断公司活跃度：法人/股东/经营范围变更）\n"
        "   - `tyc-mcp.get_financial_data` — 财务数据（年报营收、资产规模）\n"
        "   - `tyc-mcp.get_risk_scan` — 风险扫描（诉讼/失信/行政处罚/经营异常 → dealbreaker）\n"
        "   - `tyc-mcp.get_annual_reports` — 年报（社保人数=真实员工规模）\n"
        "   - `tyc-mcp.get_branches` — 分支机构（判断地理布局）\n"
        "   - `tyc-mcp.get_external_investments` — 对外投资（判断是否控股集团）\n"
        "3. If NOT found with exact name, try variations:\n"
        f'   - search_companies query="{entity}科技"\n'
        f'   - search_companies query="{entity}技术"\n'
        f'   - search_companies query="{entity}生物"\n'
        "   (common suffixes for tech/bio companies)\n"
        "\n"
        "### Step 2: 天眼查 — 知识产权专项\n"
        "Search patents and trademarks:\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import search\n"
        f'rows = search(\'"{entity}" 专利 发明\', max_results=5)\n'
        "print(json.dumps(rows, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import search\n"
        f'rows = search(\'"{entity}" 商标 品牌\', max_results=5)\n'
        "print(json.dumps(rows, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "\n"
        "### Step 3: Westock — 行业与上市公司对标\n"
        "Use westock-mcp tools:\n"
        f'1. `westock-mcp.data_search` query="{entity}" → 是否上市公司\n'
        "2. `westock-mcp.data_sector` → 行业板块数据（从 tyc 经营范围推断行业关键词）\n"
        "3. `westock-mcp.data_report` → 行业研报（用行业关键词搜索，不是公司名）\n"
        "4. If company not listed: search for comparable LISTED companies in same sector\n"
        "   (these become valuation benchmarks for downstream phases)\n"
        "\n"
        "### Step 4: Web Search — 中文全覆盖（12 组查询）\n"
        "Use WebSearch for EACH query:\n"
        f'- "{entity}" 融资 估值 投资人 2025 2026\n'
        f'- "{entity}" 产品 服务 解决方案 官网\n'
        f'- "{entity}" 创始人 CEO 团队 背景 履历\n'
        f'- "{entity}" 竞品 竞争 市场份额 对标\n'
        f'- "{entity}" 技术 专利 研发 路线 突破\n'
        f'- "{entity}" 客户 订单 合作 案例 中标\n'
        f'- "{entity}" 36氪 OR IT桔子 OR 虎嗅 OR 钛媒体 OR 创业邦\n'
        f'- "{entity}" site:36kr.com OR site:itjuzi.com\n'
        f'- "{entity}" 招聘 Boss直聘 OR 猎聘 OR 拉勾\n'
        f'- "{entity}" 政府采购 OR 中标 OR 招标 OR 公告\n'
        f'- "{entity}" 微信公众号 OR 官方 OR 官网\n'
        f'- "{entity}" 论文 OR paper OR 研究 OR 学术\n'
        "\n"
        "### Step 5: Web Search — 英文覆盖（6 组查询）\n"
        f'- "{entity}" funding investors valuation\n'
        f'- "{entity}" product technology solution\n'
        f'- "{entity}" founder CEO team background\n'
        f'- "{entity}" LinkedIn company\n'
        f'- "{entity}" Crunchbase OR PitchBook OR AngelList\n'
        f'- "{entity}" patent OR publication OR research\n'
        "\n"
        "### Step 6: 腾讯新闻 — 多轮搜索\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import tencent_news_search\n"
        f'result = tencent_news_search(\'"{entity}" 融资 产品 合作 最新\', max_results=5)\n'
        "print(json.dumps(result, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import tencent_news_search\n"
        f'result = tencent_news_search(\'"{entity}" 创始人 OR CEO OR 团队\', max_results=5)\n'
        "print(json.dumps(result, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "\n"
        "### Step 7: 模糊名称搜索（公司名变体）\n"
        "Many companies use abbreviations, brand names, or English names publicly.\n"
        "Try these web_search queries to find alternate names:\n"
        f'- "{entity}" 简称 OR 又名 OR 品牌名\n'
        f'- "{entity}" company OR inc OR ltd OR corp\n'
        f'- "{entity}" 公司 简介 关于我们\n'
        "If you find an alternate name, do ONE more round of tyc-mcp search with that name.\n"
        "\n"
        "### Step 8: Try to Find BP PDF (HIGH PRIORITY — found PDF triggers full OCR pipeline)\n"
        "Search for a public BP PDF:\n"
        f'- web_search: "{entity}" 商业计划书 filetype:pdf\n'
        f'- web_search: "{entity}" BP 融资 路演 下载\n'
        f'- web_search: "{entity}" pitch deck pdf\n'
        "\n"
        "If you find a promising PDF URL:\n"
        f'1. Download it using Bash:\n'
        "```bash\n"
        f'curl -L -o "{task_dir / "bp_discovered_pdf.pdf"}" "<PDF_URL>"\n'
        "```\n"
        f'2. Verify the download succeeded (file size > 10KB):\n'
        "```bash\n"
        f'ls -la "{task_dir / "bp_discovered_pdf.pdf"}"\n'
        "```\n"
        f'3. Set `bp_pdf_found: true` in bp_company_intake_meta.json and record `bp_pdf_url`.\n'
        "\n"
        "⚠️ Do NOT try to read the PDF yourself. Just download it to the path above.\n"
        "The pipeline will detect it and re-run full OCR extraction.\n"
        "\n"
        "## Output Requirements\n"
        "\n"
        "After completing ALL searches, you MUST write these 3 files:\n"
        "\n"
        f"### File 1: `{ocr_path}`\n"
        "Write a structured text file mimicking BP OCR output format:\n"
        "\n"
        "```\n"
        "--- 第1页 ---\n"
        f"# {entity} 商业计划书（基于公开信息整理）\n"
        "\n"
        "## 公司概况\n"
        "[tyc registration info: 成立日期, 注册资本, 法人, 经营范围, 地址]\n"
        "\n"
        "--- 第2页 ---\n"
        "## 产品与服务\n"
        "[web search product info + westock industry classification]\n"
        "\n"
        "--- 第3页 ---\n"
        "## 核心团队\n"
        "[tyc key personnel + web search founder/team coverage]\n"
        "\n"
        "--- 第4页 ---\n"
        "## 技术路线\n"
        "[tyc patents + web search tech coverage]\n"
        "\n"
        "--- 第5页 ---\n"
        "## 市场与竞争\n"
        "[westock sector/report + web search competitor info]\n"
        "\n"
        "--- 第6页 ---\n"
        "## 融资历史\n"
        "[tyc financing events + web search funding coverage]\n"
        "\n"
        "--- 第7页 ---\n"
        "## 财务信息\n"
        "[westock financials if listed + web search revenue info]\n"
        "```\n"
        "\n"
        "Fill each section with ACTUAL data found. If no data for a section, write\n"
        "`[公开信息未找到相关数据]`.\n"
        "\n"
        f"### File 2: `{profile_path}`\n"
        "Write a JSON file with this schema:\n"
        "\n"
        "```json\n"
        "{\n"
        f'  "company_name": "{entity}",\n'
        '  "industry": "[from westock sector or web search]",\n'
        '  "sub_industry": "[specific sub-sector]",\n'
        '  "product_service": ["product1", "product2"],\n'
        '  "team_highlights": ["张三 - CEO - 前XX公司VP", "李四 - CTO - XX大学博士"],\n'
        '  "competitors": ["竞品A", "竞品B"],\n'
        '  "competitive_advantages": ["advantage1"],\n'
        '  "revenue_model": "[from news/reports]",\n'
        '  "financing_stage": "[from tyc financing events or news]",\n'
        '  "advisors": [],\n'
        '  "financial_highlights": {"revenue": "未披露"},\n'
        '  "extraction_source": "public_search",\n'
        '  "data_completeness": {\n'
        '    "has_team": true,\n'
        '    "has_finance": false,\n'
        '    "has_product": true,\n'
        '    "has_tech": true,\n'
        '    "has_market": true,\n'
        '    "source_quality": "public_only"\n'
        '  }\n'
        '}\n'
        "```\n"
        "\n"
        "### Stage Tier Hint\n"
        "For `financing_stage`, classify based on tyc financing events or news:\n"
        "- 种子/天使 → '种子轮' or '天使轮'\n"
        "- Pre-A/A轮 → 'Pre-A轮' or 'A轮'\n"
        "- B轮 → 'B轮'\n"
        "- C轮+/Pre-IPO/IPO → 'C轮' or 'Pre-IPO'\n"
        "- If no financing info found → leave as '未知'\n"
        "\n"
        f"### File 3: `{meta_path}`\n"
        "Write search metadata:\n"
        "```json\n"
        "{\n"
        '  "search_completed_at": "ISO timestamp",\n'
        '  "tyc_company_found": true,\n'
        '  "tyc_company_id": "",\n'
        '  "westock_sector_found": false,\n'
        '  "web_search_count": 8,\n'
        '  "bp_pdf_found": false,\n'
        '  "bp_pdf_url": "",\n'
        '  "key_findings": ["finding1", "finding2"]\n'
        '}\n'
        "```\n"
        "\n"
        "## CRITICAL RULES\n"
        "- You MUST write ALL 3 files before finishing.\n"
        "- Do NOT skip any section in bp_ocr_text.txt — write "
        "`[公开信息未找到相关数据]` if empty.\n"
        "- In bp_step0_profile.json, set `extraction_source: \"public_search\"`.\n"
        "- In bp_step0_profile.json, populate `data_completeness` honestly.\n"
        "- If tyc finds the company, extract AS MUCH detail as possible.\n"
        "- If tyc does NOT find the company, note it in meta and continue with web search.\n"
        "- Search queries should be in BOTH Chinese and English when possible.\n"
    )

    return instruction


def bp_collect_company_intake(
    task_dir: Path,
    job_id: str,
) -> dict[str, Any]:
    """Phase01b collect: check subagent output files."""
    ocr_path = task_dir / "bp_ocr_text.txt"
    profile_path = task_dir / "bp_step0_profile.json"
    meta_path = task_dir / "bp_company_intake_meta.json"

    errors = []
    if not ocr_path.exists() or ocr_path.stat().st_size < 100:
        errors.append("bp_ocr_text.txt 不存在或过短")
    if not profile_path.exists() or profile_path.stat().st_size < 50:
        errors.append("bp_step0_profile.json 不存在或过短")

    if errors:
        return {
            "ok": False,
            "mode": "bp_company_intake_collect",
            "phase": "phase01b_company_intake_collect",
            "job_id": job_id,
            "result": {"error": "子代理未产出完整文件", "errors": errors},
        }

    # Validate profile JSON
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "mode": "bp_company_intake_collect",
            "phase": "phase01b_company_intake_collect",
            "job_id": job_id,
            "result": {"error": f"bp_step0_profile.json 解析失败: {exc}"},
        }

    # Ensure extraction_source marker
    profile["extraction_source"] = "public_search"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Read meta if available
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    completeness = profile.get("data_completeness", {})
    filled = sum(1 for k in (
        "has_team", "has_finance", "has_product", "has_tech", "has_market"
    ) if completeness.get(k))

    # 检测子代理是否下载了 BP PDF
    discovered_pdf = task_dir / "bp_discovered_pdf.pdf"
    pdf_found = (
        discovered_pdf.exists()
        and discovered_pdf.stat().st_size > 10 * 1024  # > 10KB
    )
    pdf_url = meta.get("bp_pdf_url", "")

    if pdf_found:
        print(f"  🎉 [bp phase01b_collect] 发现 BP PDF! {discovered_pdf} "
              f"({discovered_pdf.stat().st_size / 1024:.1f}KB)", flush=True)

    return {
        "ok": True,
        "mode": "bp_company_intake_collect",
        "phase": "phase01b_company_intake_collect",
        "job_id": job_id,
        "result": {
            "input_mode": "company_name_only",
            "ocr_path": str(ocr_path),
            "profile_path": str(profile_path),
            "meta_path": str(meta_path),
            "extraction_source": "public_search",
            "data_completeness": completeness,
            "completeness_score": f"{filled}/5",
            "tyc_company_found": meta.get("tyc_company_found", False),
            "bp_pdf_found": pdf_found,
            "bp_pdf_path": str(discovered_pdf) if pdf_found else "",
            "bp_pdf_url": pdf_url if pdf_found else "",
            # reroute 信号：有 PDF → 重跑 Phase 01 做完整 OCR
            "reroute_to_phase01": pdf_found,
            "reroute_input_file": str(discovered_pdf) if pdf_found else "",
        },
    }
