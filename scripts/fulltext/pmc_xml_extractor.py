#!/usr/bin/env python3
"""使用NCBI E-utilities API从PMC获取全文XML并提取文本。"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET


def fetch_pmc_xml(pmc_id: str) -> str:
    """通过NCBI E-utilities获取PMC文章的XML全文。"""
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmc_id}&retmode=xml"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 LitReviewPipeline/1.0"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_text_from_pmc_xml(xml_text: str) -> dict:
    """从PMC XML中提取结构化文本。"""
    result = {
        "title": "",
        "abstract": "",
        "body_text": "",
        "full_text": "",
        "char_count": 0,
        "sections": []
    }

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Fallback: use regex to extract text
        text = re.sub(r'<[^>]+>', ' ', xml_text)
        text = re.sub(r'\s+', ' ', text).strip()
        result["full_text"] = text
        result["char_count"] = len(text)
        return result

    ns = {
        'x': 'http://www.w3.org/1999/xhtml',
    }

    # Try to find article in various namespace configurations
    # The PMC XML doesn't always use namespace prefixes
    article = root.find('.//article') or root.find('.//{*}article')
    if article is None:
        article = root

    # Extract title
    title_el = article.find('.//article-title') or article.find('.//{*}article-title')
    if title_el is not None:
        result["title"] = ''.join(title_el.itertext()).strip()

    # Extract abstract
    abstract_el = article.find('.//abstract') or article.find('.//{*}abstract')
    if abstract_el is not None:
        result["abstract"] = ''.join(abstract_el.itertext()).strip()

    # Extract body sections
    body = article.find('.//body') or article.find('.//{*}body')
    if body is not None:
        sections_text = []
        for sec in body.findall('.//sec') or body.findall('.//{*}sec'):
            sec_title = sec.find('./title') or sec.find('./{*}title')
            title_text = ''.join(sec_title.itertext()).strip() if sec_title is not None else ""
            sec_text = ''.join(sec.itertext()).strip()
            section = {
                "heading": title_text,
                "text": sec_text,
                "char_count": len(sec_text)
            }
            sections_text.append(section)
            result["sections"].append(section)
        result["body_text"] = "\n\n".join([f"{s['heading']}\n{s['text']}" for s in sections_text])

    # Full text
    full = ""
    if result["abstract"]:
        full += "Abstract:\n" + result["abstract"] + "\n\n"
    if result["body_text"]:
        full += result["body_text"]
    result["full_text"] = full
    result["char_count"] = len(full)

    return result


def process_paper(pmc_id: str, output_dir: str) -> dict:
    """处理一篇PMC论文：获取XML → 提取文本 → 保存。"""
    paper_dir = os.path.join(output_dir, f"pmc_{pmc_id}")
    os.makedirs(paper_dir, exist_ok=True)

    xml_path = os.path.join(paper_dir, f"pmc_{pmc_id}.xml")
    text_path = os.path.join(paper_dir, f"pmc_{pmc_id}.txt")

    try:
        xml_text = fetch_pmc_xml(pmc_id)
        with open(xml_path, "w") as f:
            f.write(xml_text)

        extracted = extract_text_from_pmc_xml(xml_text)
        with open(text_path, "w") as f:
            f.write(extracted["full_text"])

        return {
            "success": True,
            "pmc_id": pmc_id,
            "char_count": extracted["char_count"],
            "title": extracted["title"],
            "abstract": extracted["abstract"],
            "text_path": text_path,
            "has_body": len(extracted.get("body_text", "")) > 0,
            "sections": len(extracted.get("sections", [])),
        }
    except Exception as e:
        return {"success": False, "pmc_id": pmc_id, "error": str(e)}


if __name__ == "__main__":
    pmc_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pmc_id:
        print(json.dumps({"error": "PMC ID required"}))
        sys.exit(1)
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "/Users/xavier/.workbuddy/ir_runtime/downloaded_pdfs"
    result = process_paper(pmc_id, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
