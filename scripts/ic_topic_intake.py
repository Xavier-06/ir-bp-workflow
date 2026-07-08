#!/usr/bin/env python3
"""IC Topic Intake — 课题元数据解析。

从 DOCX/MD/JSON 三种输入格式中提取课题的：
- core_question（核心问题）
- sub_questions（关键子问题）
- research_content（研究内容）
- key_companies（关键公司）
- category / parent_industry（分类）
- deliverables（交付物）
- update_frequency（更新频率）

不添加任何研究判断——只做结构化提取。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_topic_metadata(
    topic_source: str,
    entity: str = "",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """解析课题元数据。
    
    Args:
        topic_source: 课题文件路径（.docx/.md/.json）或课题标题字符串
        entity: 课题名称（fallback）
        output_dir: 输出目录（用于写 ic_topic_metadata.json）
    
    Returns:
        包含 core_question, sub_questions, research_content, key_companies 等的 dict
    """
    source_path = Path(topic_source) if topic_source else None
    result: dict[str, Any] = {
        "schema_version": "ic_topic.v1",
        "entity": entity or "",
        "core_question": "",
        "sub_questions": [],
        "research_content": [],
        "key_companies": [],
        "category": "",
        "parent_industry": "",
        "deliverables": [],
        "update_frequency": "",
        "source": str(topic_source) if topic_source else "",
        "parsed_at": datetime.now().isoformat(timespec="seconds"),
    }

    if source_path and source_path.exists():
        suffix = source_path.suffix.lower()
        if suffix == ".json":
            _parse_json(source_path, result)
        elif suffix in (".md", ".txt"):
            _parse_markdown(source_path, result)
        elif suffix == ".docx":
            _parse_docx(source_path, result)
    elif entity:
        # 纯文本课题名——使用 entity 作为 core_question 的 fallback
        result["core_question"] = entity
        result["sub_questions"] = []
    else:
        result["core_question"] = topic_source or ""

    # 从 entity 推断 category（如果未指定）
    if not result.get("category"):
        result["category"] = _infer_category(entity or "")

    # 写入
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ic_topic_metadata.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return result


def _parse_json(path: Path, result: dict[str, Any]) -> None:
    """直接从 JSON 文件读取课题元数据。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("entity", "core_question", "sub_questions", "research_content",
                "key_companies", "category", "parent_industry",
                "deliverables", "update_frequency"):
        if key in data and data[key]:
            result[key] = data[key]


def _parse_markdown(path: Path, result: dict[str, Any]) -> None:
    """从 Markdown 文件解析课题元数据。
    
    预期格式:
    # 课题标题
    核心问题：...
    关键子问题：
    - 子问题1
    - 子问题2
    研究内容：
    - 内容1
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 课题标题
    for line in lines[:5]:
        stripped = line.strip()
        if stripped.startswith("# "):
            result["entity"] = stripped.lstrip("# ").strip()
            break

    # 解析核心问题
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^核心问题[：:]', stripped):
            result["core_question"] = re.sub(r'^核心问题[：:]\s*', '', stripped)
            # 核心问题可能跨行
            j = i + 1
            while j < len(lines) and lines[j].strip() and not re.match(r'^(关键子问题|子问题|研究|必要研究|验证|催化|风险|交付)', lines[j].strip()):
                result["core_question"] += " " + lines[j].strip()
                j += 1
            break

    # 解析子问题
    in_sub_questions = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^(关键子问题|子问题)[：:]', stripped):
            in_sub_questions = True
            # 可能同行走子问题
            content = re.sub(r'^(关键子问题|子问题)[：:]\s*', '', stripped)
            if content and not content.startswith("-"):
                # 可能是编号列表: 1. xxx, ① xxx
                pass
            continue
        if in_sub_questions:
            if re.match(r'^(核心问题|研究内容|必要研究|关键公司|验证|催化|风险|交付|适合)', stripped):
                in_sub_questions = False
                continue
            # 提取列表项
            question = re.sub(r'^[-*•\d]+[.)、\s]*', '', stripped)
            if question and len(question) > 5:
                result["sub_questions"].append(question)

    # 解析研究内容
    in_content = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^(研究内容|必要研究内容)[：:]', stripped):
            in_content = True
            continue
        if in_content:
            if re.match(r'^(核心问题|子问题|关键公司|验证|催化|风险|交付|适合|课题)', stripped):
                in_content = False
                continue
            item = re.sub(r'^[-*•\d]+[.)、\s]*', '', stripped)
            if item and len(item) > 3:
                result["research_content"].append(item)

    # 解析关键公司
    for line in lines:
        stripped = line.strip()
        m = re.search(r'(关键公司|key_companies)[：:]\s*(.+)', stripped, re.IGNORECASE)
        if m:
            companies_str = m.group(2)
            # 按逗号/顿号/空格分割
            companies = re.split(r'[,，、\s]+', companies_str)
            result["key_companies"] = [c.strip() for c in companies if c.strip()]
            break

    # 交付物
    for line in lines:
        stripped = line.strip()
        m = re.match(r'^交付物[：:]\s*(.+)', stripped)
        if m:
            result["deliverables"] = [d.strip() for d in re.split(r'[,，、]', m.group(1))]
            break

    # 更新频率
    for line in lines:
        stripped = line.strip()
        m = re.search(r'(季度|月度|周度|年度)更新', stripped)
        if m:
            result["update_frequency"] = m.group(0)
            break


def _parse_docx(path: Path, result: dict[str, Any]) -> None:
    """从 DOCX 课题池文件解析单个课题元数据。
    
    DOCX 课题池格式:
    一、 AI行业研究课题池
    1.1 AI芯片（5个子课题）
    课题 1：全球AI芯片产业链研究
    核心问题：...
    关键子问题：
    - ...
    研究内容：
    - ...
    """
    try:
        from docx import Document
    except ImportError:
        # python-docx 不可用，fallback 到纯文本
        _parse_topic_from_entity(result)
        return

    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    # 找到匹配的课题段落
    entity = result.get("entity", "")
    if not entity:
        for para in paragraphs:
            m = re.match(r'^课题\s*\d+[：:]\s*(.+)', para)
            if m:
                entity = m.group(1)
                result["entity"] = entity
                break

    # 提取课题相关的所有段落（从课题标题到下一个课题标题之间）
    collecting = False
    topic_paragraphs: list[str] = []
    category = ""
    for i, para in enumerate(paragraphs):
        # 检测分类标题
        if re.match(r'^(一|二|三|四|五|六|七)[、.]', para) or re.match(r'^\d+\.\d+\s', para):
            if not collecting:
                category = para
            continue

        # 检测课题标题
        m = re.match(r'^课题\s*\d+[：:]\s*(.+)', para)
        if m:
            if collecting:
                break  # 遇到下一个课题，停止
            topic_name = m.group(1)
            if entity and (entity in topic_name or topic_name in entity):
                collecting = True
                result["entity"] = topic_name
            elif not entity:
                collecting = True
                result["entity"] = topic_name
            continue

        if collecting:
            if re.match(r'^课题\s*\d+[：:]', para):
                break  # 下一个课题
            topic_paragraphs.append(para)

    if topic_paragraphs:
        # 用 markdown 解析器处理提取出的段落
        topic_text = "\n".join(topic_paragraphs)
        tmp_path = path.parent / "_ic_topic_tmp.md"
        tmp_path.write_text(topic_text, encoding="utf-8")
        _parse_markdown(tmp_path, result)
        try:
            tmp_path.unlink()
        except Exception:
            pass

    # 从 DOCX 的分类标题推断 category
    if category:
        result["category"] = category
        for para in paragraphs[:3]:
            if para and not para.startswith("课题"):
                result["parent_industry"] = para
                break


def _parse_topic_from_entity(result: dict[str, Any]) -> None:
    """当没有可解析文件时，从 entity 创建最小元数据。"""
    entity = result.get("entity", "")
    result["core_question"] = entity
    result["category"] = _infer_category(entity)


def _infer_category(entity: str) -> str:
    """从课题名称推断分类。"""
    keywords_map = {
        "AI芯片": "AI芯片", "GPU": "AI芯片", "ASIC": "AI芯片", "HBM": "AI芯片",
        "CoWoS": "AI芯片", "芯片": "AI芯片",
        "服务器": "AI基础设施", "液冷": "AI基础设施", "光模块": "AI基础设施",
        "算力": "AI基础设施", "数据中心": "AI基础设施", "电力": "AI基础设施",
        "大模型": "大模型", "开源": "大模型", "Agent": "大模型",
        "AI Coding": "AI应用", "视频": "AI应用", "AI编程": "AI应用",
        "聚变": "可控核聚变", "核聚变": "可控核聚变", "超导": "可控核聚变",
        "氢": "绿色氢氨醇", "电解": "绿色氢氨醇", "氨": "绿色氢氨醇", "甲醇": "绿色氢氨醇",
        "机器人": "机器人", "Optimus": "机器人", "灵巧手": "机器人",
        "具身": "机器人", "减速器": "机器人", "丝杠": "机器人",
        "Robotaxi": "Robotaxi", "自动驾驶": "Robotaxi", "FSD": "Robotaxi",
        "思摩尔": "跟踪标的", "传思": "跟踪标的",
    }
    for keyword, category in keywords_map.items():
        if keyword in entity:
            return category
    return ""


def main():
    import argparse
    ap = argparse.ArgumentParser(description="IC 课题元数据解析")
    ap.add_argument("--source", required=True, help="课题文件路径或课题标题")
    ap.add_argument("--entity", default="", help="课题名称")
    ap.add_argument("--output-dir", default="", help="输出目录")
    args = ap.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else None
    result = parse_topic_metadata(args.source, args.entity, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
