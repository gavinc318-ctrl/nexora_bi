# -*- coding: utf-8 -*-
"""从 CloudICP 接口文档抽取枚举值表，生成 3rd API/icp_{device_type,call_type,release_cause}.csv。

用法：
    pdftotext -layout "3rd API/CloudICP V100R023C00 Interface Reference.pdf" /tmp/icp.txt
    python3 tools/icp_enum.py /tmp/icp.txt "3rd API"

释放原因码的 release_source 来自文档中跨页合并的分组单元格，按文档顺序继承。
契约中已写明：该归类必须在联调时用真实话单核对一遍，不能只按文档归类。
"""
import os,re,csv,sys,collections
SRC=sys.argv[1] if len(sys.argv)>1 else '/tmp/icp.txt'
OUT=sys.argv[2] if len(sys.argv)>2 else '.'
pages=open(SRC,encoding='utf-8',errors='replace').read().split('\f')

def rows_by_cols(page, labels):
    lines=page.split('\n')
    hdr=None
    for l in lines:
        if all(x in l for x in labels): hdr=l; break
    if not hdr: return None,None
    pos=[hdr.find(x) for x in labels]+[10**6]
    return lines, pos

# ---------- Device Types / Call Type ----------
def simple(pagenos, labels, fields):
    out=[]
    for n in pagenos:
        lines,pos=rows_by_cols(pages[n-1], labels)
        if not lines: continue
        cur=None
        for l in lines:
            s=l.strip()
            if not s or s.startswith(('Copyright','Parent topic','Table ')) or re.match(r'^\d+ / \d+$',s): continue
            first=l[pos[0]:pos[1]].strip()
            if re.match(r'^\d+$', first):
                cur={fields[0]:first}
                for i in range(1,len(labels)): cur[fields[i]]=l[pos[i]:pos[i+1]].strip()
                out.append(cur)
            elif cur:
                for i in range(1,len(labels)):
                    seg=l[pos[i]:pos[i+1]].strip()
                    if seg: cur[fields[i]]=(cur[fields[i]]+' '+seg).strip()
    return out

dev=simple([1033], ['Value','Description'], ['value','description'])
ct =simple([1061,1062], ['ID','Call Type','Description'], ['id','name','description'])
with open(os.path.join(OUT,'icp_device_type.csv'),'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['value','description']); w.writeheader(); w.writerows(dev)
with open(os.path.join(OUT,'icp_call_type.csv'),'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['id','name','description']); w.writeheader(); w.writerows(ct)
print('device_type',len(dev),'call_type',len(ct))

# ---------- Release cause codes ----------
LABELS=['Release Source','Cause Code','Type','Description']
SRCMAP={'Agent release':'agent','User release':'caller','Process release':'system'}
rel=[]; cur_src=None; cur=None
for n in range(1063,1076):
    lines,pos=rows_by_cols(pages[n-1], LABELS)
    if not lines: continue
    for l in lines:
        s=l.strip()
        if not s or s.startswith(('Copyright','Parent topic')) or re.match(r'^\d+ / \d+$',s): continue
        seg0=l[pos[0]:pos[1]].strip()
        if seg0 in SRCMAP: cur_src=SRCMAP[seg0]
        code=l[pos[1]:pos[2]].strip()
        if re.match(r'^\d{3,5}$', code):
            cur={'release_source':cur_src or '','cause_code':code,
                 'type':l[pos[2]:pos[3]].strip(),'description':l[pos[3]:].strip()}
            rel.append(cur)
        elif cur:
            t=l[pos[2]:pos[3]].strip(); dsc=l[pos[3]:].strip()
            if t: cur['type']+=t
            if dsc: cur['description']=(cur['description']+' '+dsc).strip()
seen=set(); rel2=[]
for r in rel:
    if r['cause_code'] in seen: continue
    seen.add(r['cause_code']); rel2.append(r)
with open(os.path.join(OUT,'icp_release_cause.csv'),'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['cause_code','release_source','type','description']); w.writeheader()
    for r in rel2: w.writerow({k:r[k] for k in ['cause_code','release_source','type','description']})
print('release_cause',len(rel2), collections.Counter(r['release_source'] for r in rel2))
