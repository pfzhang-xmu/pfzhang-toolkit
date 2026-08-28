#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench PDF 导出模块 (reportlab)。
pandoc 无 PDF 引擎时的降级方案。
"""
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                 Table, TableStyle, HRFlowable)
from reportlab.lib.utils import ImageReader

_styles = getSampleStyleSheet()
S_TITLE = ParagraphStyle('SciTitle', parent=_styles['Title'],
    fontName='Helvetica-Bold', fontSize=16, leading=20, alignment=TA_CENTER,
    spaceAfter=12, textColor=HexColor('#1a1a1a'))
S_H2 = ParagraphStyle('SciH2', parent=_styles['Heading2'],
    fontName='Helvetica-Bold', fontSize=13, leading=16, spaceBefore=14,
    spaceAfter=6, textColor=HexColor('#1a1a1a'))
S_H3 = ParagraphStyle('SciH3', parent=_styles['Heading3'],
    fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=10,
    spaceAfter=4, leftIndent=8, textColor=HexColor('#1a1a1a'))
S_BODY = ParagraphStyle('SciBody', parent=_styles['Normal'],
    fontName='Helvetica', fontSize=10, leading=14, alignment=TA_JUSTIFY,
    spaceAfter=6, textColor=HexColor('#222'))
S_EM = ParagraphStyle('SciEm', parent=_styles['Normal'],
    fontName='Helvetica-Oblique', fontSize=10, leading=13, alignment=TA_LEFT,
    spaceAfter=4, textColor=HexColor('#555'))
S_CAPTION = ParagraphStyle('SciCap', parent=_styles['Normal'],
    fontName='Helvetica-Oblique', fontSize=9, leading=11, alignment=TA_CENTER,
    spaceAfter=10, textColor=HexColor('#444'))
S_TBL_H = ParagraphStyle('TblHdr', parent=_styles['Normal'],
    fontName='Helvetica-Bold', fontSize=8.5, leading=10, alignment=TA_CENTER)
S_TBL_C = ParagraphStyle('TblCell', parent=_styles['Normal'],
    fontName='Helvetica', fontSize=8.5, leading=10, alignment=TA_CENTER)


def _esc(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _md_inline(text):
    text = _esc(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def _parse_md(md_text, project_dir):
    lines = md_text.split('\n')
    flows = []
    i = 0
    table_rows, table_header, in_table = [], None, False

    def flush():
        nonlocal table_rows, table_header, in_table
        if not table_rows and not table_header:
            in_table = False
            return
        data = []
        if table_header:
            data.append([Paragraph(_md_inline(h), S_TBL_H) for h in table_header])
        for row in table_rows:
            data.append([Paragraph(_md_inline(c), S_TBL_C) for c in row])
        if not data:
            in_table = False
            return
        n = len(data[0])
        cw = [17 * cm / n] * n
        t = Table(data, colWidths=cw, hAlign='CENTER')
        t.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1.5, HexColor('#333')),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, HexColor('#333')),
            ('LINEBELOW', (0, -1), (-1, -1), 1.5, HexColor('#333')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        flows.append(t)
        flows.append(Spacer(1, 8))
        table_rows, table_header, in_table = [], None, False

    while i < len(lines):
        s = lines[i].strip()

        if s.startswith('<!--') or s.startswith('-->'):
            i += 1
            continue

        if s.startswith('# ') and not s.startswith('## '):
            if in_table: flush()
            flows.append(Paragraph(_md_inline(s[2:]), S_TITLE))
            flows.append(Spacer(1, 8))
            i += 1
            continue

        if s.startswith('## '):
            if in_table: flush()
            flows.append(Paragraph(_md_inline(s[3:]), S_H2))
            flows.append(HRFlowable(width='100%', thickness=1,
                                    color=HexColor('#333'), spaceBefore=2, spaceAfter=6))
            i += 1
            continue

        if s.startswith('### ') or s.startswith('#### '):
            if in_table: flush()
            prefix = 4 if s.startswith('#### ') else 4
            flows.append(Paragraph(_md_inline(s[prefix:]), S_H3))
            i += 1
            continue

        if s.startswith('|') and '---' not in s:
            cells = [c.strip() for c in s.strip('|').split('|')]
            if not in_table:
                in_table = True
                table_header = cells
            else:
                table_rows.append(cells)
            i += 1
            continue

        if s.startswith('|') and '---' in s:
            i += 1
            continue

        if s == '---':
            if in_table: flush()
            flows.append(Spacer(1, 6))
            flows.append(HRFlowable(width='40%', thickness=0.5,
                                    color=HexColor('#ccc'), hAlign='CENTER',
                                    spaceBefore=4, spaceAfter=8))
            i += 1
            continue

        if s.startswith('```'):
            if in_table: flush()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1
            code_text = '<br/>'.join(_esc(l) for l in code_lines)
            flows.append(Paragraph(
                f'<font face="Courier" size="8">{code_text}</font>', S_BODY))
            continue

        m = re.match(r'!\[(.*?)\]\((.*?)\)', s)
        if m:
            if in_table: flush()
            caption = m.group(1)
            img_path = m.group(2)
            full = os.path.join(project_dir, img_path) if not os.path.isabs(img_path) else img_path
            if os.path.exists(full):
                img = ImageReader(full)
                iw, ih = img.getSize()
                max_w, max_h = 14 * cm, 10 * cm
                ratio = min(max_w / iw, max_h / ih, 1.0) if iw > 0 else 1
                flows.append(Spacer(1, 6))
                flows.append(Image(full, width=iw * ratio, height=ih * ratio, hAlign='CENTER'))
                if caption:
                    flows.append(Paragraph(_md_inline(caption), S_CAPTION))
            i += 1
            continue

        if s.startswith('*') and s.endswith('*') and not s.startswith('**'):
            if in_table: flush()
            flows.append(Paragraph(_md_inline(s.strip('*')), S_EM))
            i += 1
            continue

        if s.startswith('- ') or s.startswith('* '):
            if in_table: flush()
            flows.append(Paragraph(f'\u2022 {_md_inline(s[2:])}',
                ParagraphStyle('LI', parent=S_BODY, leftIndent=15, bulletIndent=5)))
            i += 1
            continue

        if not s:
            if in_table: flush()
            i += 1
            continue

        if in_table: flush()
        flows.append(Paragraph(_md_inline(s), S_BODY))
        i += 1

    if in_table: flush()
    return flows


def _page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(HexColor('#666'))
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, str(canvas.getPageNumber()))
    canvas.restoreState()


def generate_pdf(md_path, pdf_path, project_dir=None):
    """从 Markdown 生成科研论文 PDF。"""
    if project_dir is None:
        project_dir = os.path.dirname(os.path.dirname(md_path))
    md_text = open(md_path, encoding='utf-8', errors='replace').read()
    md_text = re.sub(r'<!-- INSERT-FIG -->|<!-- /INSERT-FIG -->|<!-- INSERT-TAB -->|<!-- /INSERT-TAB -->', '', md_text)
    flows = _parse_md(md_text, project_dir)
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.5 * cm, bottomMargin=2.5 * cm)
    doc.build(flows, onFirstPage=_page_number, onLaterPages=_page_number)
    return pdf_path
