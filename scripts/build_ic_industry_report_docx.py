#!/usr/bin/env python3
"""
build_ic_industry_report_docx.py — 将 IC 管线 step_master_synthesis 输出转为行业深度研报 Word 文档

与 build_ir_broker_report_docx.py 对标，但章节结构适配行业研究：
  - 投资摘要 → 行业概览 → 产业链分析 → 竞争格局 → 技术趋势 → 市场规模
  - 财务基准 → 估值基准 → 资本动向 → 跨环节对比 → 投资机会 → 风险评估 → 来源附录

v1 (2026-05-29): 初始版本，复用 IR 的字体/清洗基础设施
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from datetime import datetime
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / 'data' / 'tasks'
JOBS = ROOT / 'jobs'
REPORTS = ROOT / 'reports'


# ─── 东亚字体设置 ────────────────────────────

def _set_eastasia_font_on_style(style, font_name: str):
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = rPr.makeelement(qn('w:rFonts'), {})
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:eastAsia'), font_name)


def _set_eastasia_font_on_run(run, font_name: str):
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = rPr.makeelement(qn('w:rFonts'), {})
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:eastAsia'), font_name)


# ─── 内部信息清洗 ────────────────────────────

INTERNAL_PATTERNS = [
    (r'/Users/\w+/[^\s\n]+', ''),
    (r'/home/\w+/[^\s\n]+', ''),
    (r'~/.workbuddy/[^\s\n]+', ''),
    (r'data/tasks/[^\s\n]+', ''),
    (r'TASK-\d{8}-\d{3}[-\w]*', ''),
    (r'python3?\s+scripts/[^\n]+', ''),
    (r'step\d+_\w+\.md', ''),
    (r'输出文件[：:][^\n]+', ''),
    (r'^[-*]\s*(?:Task|Entity|Accepted evidence|Rounds|Generated)[：:][^\n]*$', ''),
]

LINE_DELETE_PATTERNS = [
    r'^\s*(?:输出文件|Output file|Task ID|任务 ID)\s*[：:]',
    r'^\s*```\s*(?:bash|python|shell)',
    r'^\s*```\s*$',
    r'^\s*(?:子代理|subagent|sub-agent)',
]

_INTERNAL_SOURCE_PATTERNS = [
    r"step_\w+\.md",
    r"ic_presearch",
    r"ic_extract",
    r"data/tasks/",
]


def _is_internal_source_row(row_text: str) -> bool:
    for pat in _INTERNAL_SOURCE_PATTERNS:
        if re.search(pat, row_text, re.IGNORECASE):
            return True
    return False


def sanitize_text(text: str) -> str:
    for pat, repl in INTERNAL_PATTERNS:
        text = re.sub(pat, repl, text)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        skip = False
        for pat in LINE_DELETE_PATTERNS:
            if re.search(pat, line):
                skip = True
                break
        if not skip:
            cleaned.append(line)
    return '\n'.join(cleaned).strip()


# ─── Markdown 解析 → DOCX ────────────────────────────

FONT_NAME = 'Microsoft YaHei'
FONT_NAME_ASCII = 'Calibri'

# IC 研报配色（行业研究风格：深蓝+橙）
COLOR_TITLE = RGBColor(0x1A, 0x3C, 0x6E)       # 深蓝
COLOR_SECTION = RGBColor(0x2C, 0x5F, 0x8A)      # 中蓝
COLOR_ACCENT = RGBColor(0xE8, 0x6C, 0x00)        # 橙色
COLOR_BODY = RGBColor(0x33, 0x33, 0x33)          # 深灰
COLOR_TABLE_HEADER = RGBColor(0x1A, 0x3C, 0x6E)  # 深蓝表头


def _add_run(para, text, bold=False, italic=False, color=None, size=None):
    run = para.add_run(text)
    run.font.name = FONT_NAME_ASCII
    _set_eastasia_font_on_run(run, FONT_NAME)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = color
    if size:
        run.font.size = Pt(size)
    return run


def _add_heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.name = FONT_NAME_ASCII
        _set_eastasia_font_on_run(run, FONT_NAME)
        if level == 1:
            run.font.color.rgb = COLOR_TITLE
        elif level == 2:
            run.font.color.rgb = COLOR_SECTION
    return h


def _add_body_para(doc, text, bold=False):
    p = doc.add_paragraph()
    _add_run(p, text, bold=bold, color=COLOR_BODY, size=10.5)
    p.paragraph_format.space_after = Pt(4)
    return p


def _add_table(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'

    # Header row
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ''
        p = cell.paragraphs[0]
        _add_run(p, h, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), size=10)
        p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        # Background color
        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{COLOR_TABLE_HEADER.__str__()}"/>')
        cell._tc.get_or_add_tcPr().append(shading)

    # Data rows
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            if c_idx < len(headers):
                cell = table.rows[r_idx + 1].cells[c_idx]
                cell.text = ''
                p = cell.paragraphs[0]
                _add_run(p, str(val), color=COLOR_BODY, size=9.5)

    return table


def _parse_markdown_to_docx(doc, md_text: str):
    """将 Markdown 文本增量解析并写入 docx Document"""
    md_text = sanitize_text(md_text)
    lines = md_text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # 空行跳过
        if not line.strip():
            i += 1
            continue

        # 标题
        if line.startswith('#'):
            level = len(re.match(r'^#+', line).group())
            title_text = line.lstrip('#').strip()
            _add_heading(doc, title_text, level=min(level, 4))
            i += 1
            continue

        # 表格
        if '|' in line and line.strip().startswith('|'):
            # 收集连续表格行
            table_lines = []
            while i < len(lines) and '|' in lines[i] and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            # 解析
            headers = [c.strip() for c in table_lines[0].split('|')[1:-1] if c.strip()]
            rows = []
            for tl in table_lines[2:]:  # 跳过分隔行
                cells = [c.strip() for c in tl.split('|')[1:-1]]
                rows.append(cells)
            if headers:
                _add_table(doc, headers, rows)
            continue

        # 列表项
        if re.match(r'^\s*[-*]\s', line):
            text = re.sub(r'^\s*[-*]\s', '', line).strip()
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 去加粗标记
            p = doc.add_paragraph(style='List Bullet')
            _add_run(p, text, color=COLOR_BODY, size=10.5)
            i += 1
            continue

        # 有序列表
        if re.match(r'^\s*\d+\.\s', line):
            text = re.sub(r'^\s*\d+\.\s', '', line).strip()
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            p = doc.add_paragraph(style='List Number')
            _add_run(p, text, color=COLOR_BODY, size=10.5)
            i += 1
            continue

        # 引用块
        if line.startswith('>'):
            text = line.lstrip('>').strip()
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            p = doc.add_paragraph()
            _add_run(p, text, italic=True, color=RGBColor(0x66, 0x66, 0x66), size=10)
            p.paragraph_format.left_indent = Cm(1)
            i += 1
            continue

        # 普通段落
        text = line.strip()
        # 处理加粗标记
        if '**' in text:
            p = doc.add_paragraph()
            parts = re.split(r'(\*\*.*?\*\*)', text)
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    _add_run(p, part[2:-2], bold=True, color=COLOR_BODY, size=10.5)
                else:
                    _add_run(p, part, color=COLOR_BODY, size=10.5)
        else:
            _add_body_para(doc, text)

        i += 1


# ─── 主构建流程 ────────────────────────────

def build_ic_report(task_id: str, output_path: str = '') -> str:
    """将 IC 管线 step_master_synthesis 输出转为行业深度研报 DOCX"""

    # 1. 查找 master_synthesis 输出
    master_md = None
    # Workspace 模式
    ws_output = JOBS / task_id / 'outputs' / 'step_master_synthesis.md'
    if ws_output.exists() and ws_output.stat().st_size > 100:
        master_md = ws_output.read_text(encoding='utf-8')
    # Legacy 模式
    if not master_md:
        legacy = TASKS / f'{task_id}-step_master_synthesis.md'
        if legacy.exists() and legacy.stat().st_size > 100:
            master_md = legacy.read_text(encoding='utf-8')

    if not master_md:
        raise FileNotFoundError(f"step_master_synthesis 输出未找到 (task_id={task_id})")

    # 2. 读取行业元数据
    entity = ''
    scope_path = TASKS / f'{task_id}-ic_scope.json'
    if scope_path.exists():
        try:
            scope = json.loads(scope_path.read_text(encoding='utf-8'))
            entity = scope.get('industry', '')
        except Exception:
            pass

    # 3. 创建文档
    doc = Document()

    # 设置默认样式
    for style_name in ('Normal', 'Heading 1', 'Heading 2', 'Heading 3'):
        try:
            style = doc.styles[style_name]
            _set_eastasia_font_on_style(style, FONT_NAME)
            style.font.name = FONT_NAME_ASCII
        except KeyError:
            pass

    # 4. 封面页
    doc.add_paragraph()
    doc.add_paragraph()
    title_para = doc.add_paragraph()
    title_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    _add_run(title_para, f'{entity}行业深度研究报告', bold=True, color=COLOR_TITLE, size=26)

    doc.add_paragraph()
    subtitle_para = doc.add_paragraph()
    subtitle_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    _add_run(subtitle_para, 'Industry Deep Dive Report', italic=True, color=COLOR_SECTION, size=14)

    doc.add_paragraph()
    date_para = doc.add_paragraph()
    date_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    _add_run(date_para, f'{datetime.now().strftime("%Y年%m月%d日")}', color=RGBColor(0x66, 0x66, 0x66), size=11)

    disclaimer_para = doc.add_paragraph()
    disclaimer_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    _add_run(disclaimer_para, '本报告由 AI 投研管线自动生成，仅供参考，不构成投资建议',
             italic=True, color=RGBColor(0x99, 0x99, 0x99), size=9)

    doc.add_page_break()

    # 5. 正文
    _parse_markdown_to_docx(doc, master_md)

    # 6. 保存
    REPORTS.mkdir(parents=True, exist_ok=True)
    if not output_path:
        output_path = str(REPORTS / f'{task_id}_行业研报_{entity}.docx')

    doc.save(output_path)

    # 7. 复制到桌面
    try:
        desktop = Path.home() / 'Desktop'
        if desktop.exists():
            desktop_copy = desktop / Path(output_path).name
            shutil.copy2(output_path, desktop_copy)
            print(f"  📋 已复制到桌面: {desktop_copy}", flush=True)
    except Exception:
        pass

    return output_path


# ─── CLI ────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IC 行业深度研报 DOCX 生成")
    parser.add_argument('task_id', help='任务 ID (如 TASK-20260529-001)')
    parser.add_argument('-o', '--output', default='', help='输出路径')
    args = parser.parse_args()

    try:
        out = build_ic_report(args.task_id, args.output)
        print(f"✅ 行业研报已生成: {out}")
    except Exception as e:
        print(f"❌ 生成失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
