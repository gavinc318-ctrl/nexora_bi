# -*- coding: utf-8 -*-
"""docx → Markdown。保留标题层级与表格，单列表格转为引用块。"""
import docx, re, os, sys
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

def iter_block(parent):
    """按文档真实顺序遍历段落与表格。"""
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, parent)
        elif child.tag == qn('w:tbl'):
            yield Table(child, parent)

def cell_text(c):
    t = ' '.join(p.text.strip() for p in c.paragraphs if p.text.strip())
    return t.replace('|', '\\|').strip()

def table_md(t):
    rows = [[cell_text(c) for c in r.cells] for r in t.rows]
    if not rows: return ''
    ncol = len(rows[0])
    # 单列表格：文档里用作「提示框」，转为引用块
    if ncol == 1:
        body = [r[0] for r in rows if r[0]]
        if not body: return ''
        out = ['> **' + body[0] + '**'] if len(body) > 1 else ['> ' + body[0]]
        for line in body[1:]:
            out.append('>')
            out.append('> ' + line)
        return '\n'.join(out) + '\n'
    # 多列：首行为表头
    head = rows[0]
    out = ['| ' + ' | '.join(head) + ' |',
           '|' + '|'.join([' --- '] * ncol) + '|']
    for r in rows[1:]:
        r = (r + [''] * ncol)[:ncol]
        out.append('| ' + ' | '.join(x if x else ' ' for x in r) + ' |')
    return '\n'.join(out) + '\n'

def convert(path, meta):
    d = docx.Document(path)
    lines = []
    # frontmatter
    lines.append('---')
    for k, v in meta.items():
        lines.append(f'{k}: {v}')
    lines.append('---')
    lines.append('')
    cover, started = [], False
    for blk in iter_block(d):
        if isinstance(blk, Paragraph):
            style = blk.style.name
            txt = blk.text.strip()
            if not txt:
                continue
            if style == 'Heading 1':
                started = True
                lines.append('')
                lines.append('## ' + txt)
                lines.append('')
            elif style == 'Heading 2':
                started = True
                lines.append('')
                lines.append('### ' + txt)
                lines.append('')
            else:
                if not started:
                    cover.append(txt)
                else:
                    lines.append(txt)
                    lines.append('')
        else:
            md = table_md(blk)
            if md:
                if not started:
                    continue
                lines.append(md)
    # 封面块放到 frontmatter 之后
    head = []
    if cover:
        head.append('# ' + cover[2] if len(cover) > 2 else '# ' + cover[0])
        head.append('')
        head.append('> ' + ' · '.join(cover[:2] + cover[3:]))
        head.append('')
    body = '\n'.join(lines)
    body = re.sub(r'\n{3,}', '\n\n', body)
    # 把 head 插到 frontmatter 之后
    parts = body.split('---\n', 2)
    if len(parts) == 3:
        body = '---\n' + parts[1] + '---\n\n' + '\n'.join(head) + parts[2]
    return body.rstrip() + '\n'

if __name__ == '__main__':
    import json
    src, dst, meta = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    open(dst, 'w', encoding='utf-8').write(convert(src, meta))
    print('ok', dst)
