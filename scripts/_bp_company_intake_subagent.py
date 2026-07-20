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
        "- connectorIds = ['tyc-mcp', 'westock-mcp']\n"
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
        "## Search Strategy (execute in order)\n"
        "\n"
        "### Step 1: 天眼查 — Company Registration (MANDATORY)\n"
        "Use tyc-mcp tools:\n"
        "1. `tyc-mcp.search_companies` with query = "
        f'"{entity}" → get company_id\n'
        "2. If found, call `tyc-mcp.get_company_basic_profile` with company_id\n"
        "3. Extract: registration info, shareholders, legal representative, registered capital,\n"
        "   establishment date, business scope, financing history\n"
        "4. Call `tyc-mcp.get_key_personnel` for board/management team\n"
        "5. Call `tyc-mcp.get_shareholder_info` for shareholder structure\n"
        "6. Call `tyc-mcp.get_external_investments` for portfolio companies (if holding company)\n"
        "7. Search IP: use Bash script below for patents/trademarks\n"
        "\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import search\n"
        f'rows = search(\'"{entity}" 专利 知识产权\', max_results=5)\n'
        "print(json.dumps(rows, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "\n"
        "### Step 2: Westock — Industry Data\n"
        "Use westock-mcp tools (if company can be mapped to a sector):\n"
        "1. `westock-mcp.data_search` with query = "
        f'"{entity}" → check if listed company\n'
        "2. `westock-mcp.data_sector` to find industry/sector data\n"
        "3. `westock-mcp.data_report` to search for industry research reports\n"
        "\n"
        "### Step 3: Web Search — Comprehensive Coverage\n"
        f'Use web_search for EACH of these queries:\n'
        f'- "{entity}" 融资 估值 投资人 2025 2026\n'
        f'- "{entity}" 产品 服务 解决方案 官网\n'
        f'- "{entity}" 创始人 CEO 团队 背景\n'
        f'- "{entity}" 竞品 竞争 市场份额\n'
        f'- "{entity}" 技术 专利 研发 路线\n'
        f'- "{entity}" 客户 订单 合作 案例\n'
        f'- "{entity}" 36氪 OR IT桔子 OR 虎嗅 OR 钛媒体\n'
        f'- "{entity}" site:36kr.com OR site:itjuzi.com\n'
        "\n"
        "### Step 4: Tencent News — Latest Updates\n"
        "Use Bash to call search_gateway:\n"
        "```bash\n"
        f'cd {runtime_root} && python3 -c "\n'
        "import json, sys; sys.path.insert(0, '.')\n"
        "from scripts.search_gateway import tencent_news_search\n"
        f'result = tencent_news_search(\'"{entity}" 融资 产品 合作 最新\', max_results=5)\n'
        "print(json.dumps(result, ensure_ascii=False, indent=2))\n"
        '"\n'
        "```\n"
        "\n"
        "### Step 5: Try to Find BP PDF (Optional — best effort)\n"
        "Try these web_search queries to find a public BP:\n"
        f'- "{entity}" 商业计划书 filetype:pdf\n'
        f'- "{entity}" BP 融资 路演 下载\n'
        f'If you find a promising URL, use web_fetch to read it. If it\'s a PDF or contains\n'
        f'structured BP content, incorporate it into the ocr_text.\n'
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
            "bp_pdf_found": meta.get("bp_pdf_found", False),
        },
    }
