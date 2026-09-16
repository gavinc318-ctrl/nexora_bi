# -*- coding: utf-8 -*-
"""HD-01 需求规格：转为逐条 Markdown，验收标准单列 —— 测试用例与实现都从这里派生。"""
import docx, os, re
BASE=os.path.expanduser('~/mnt/bi_datawarehouse'); OUT=os.path.join(BASE,'LLD')
d=docx.Document(os.path.join(BASE,'HD-01_需求规格说明书.docx'))
out=['---','id: HD-01','title: 需求规格说明书','layer: 参考',
     'summary: 124 条编号需求，每条含需求描述、RFP 条款、验收标准、责任组件、优先级。'
     'LD-10 的用例由验收标准派生，实现完成的判据也在这里',
     'source: HD-01_需求规格说明书.docx','format: converted from docx, content unchanged','---','',
     '# 需求规格说明书（HD-01）','',
     '> 每条需求的「验收标准」是判断实现是否完成的唯一依据。写不出可执行用例的验收标准，是需求本身的缺陷（见 LD-10 第 1 章）。','']
n=0
for t in d.tables:
    rows=t.rows
    if not rows: continue
    hdr=[c.text.strip() for c in rows[0].cells]
    if len(rows) < 2:
        continue
    for r in rows:
        v=r.cells[0].text.strip()
        m=re.match(r'(REQ-[A-Z]+-\d+)', v)
        if not m: continue
        code=m.group(1); tail=v[len(code):].strip()
        cells=[c.text.strip() for c in r.cells]
        out.append(f'## {code}' + (f'  ({tail})' if tail else ''))
        out.append('')
        labels=['需求','RFP 条款','验收标准','优先级']
        for lab, val in zip(labels, cells[1:5]):
            if val: out.append(f'- **{lab}**：' + val.replace('\n',' '))
        out.append('')
        n+=1
open(os.path.join(OUT,'REF-HD-01-requirements.md'),'w',encoding='utf-8').write('\n'.join(out))
print('REF-HD-01-requirements.md：', n, '条需求')
