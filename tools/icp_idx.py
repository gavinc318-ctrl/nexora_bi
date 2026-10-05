# -*- coding: utf-8 -*-
"""从 CloudICP 接口文档抽取 396 个平台指标目录，生成 3rd API/icp_indexes.csv。

用法：
    pdftotext -layout "3rd API/CloudICP V100R023C00 Interface Reference.pdf" /tmp/icp.txt
    python3 tools/icp_idx.py /tmp/icp.txt "3rd API/icp_indexes.csv"

CSV 是派生物，权威是 PDF。换版本的接口文档就重跑一遍，并逐行比对差异——
平台指标编号在大版本之间可能增删，而 metrics/icp_source_map.yaml 引用了这些编号。
"""
import sys
import os,re,csv,collections
SRC=sys.argv[1] if len(sys.argv)>1 else '/tmp/icp.txt'
OUT=sys.argv[2] if len(sys.argv)>2 else '3rd API/icp_indexes.csv'
pages=open(SRC,encoding='utf-8',errors='replace').read().split('\f')
OBJ={'Agent','Skill','IVR','VDN'}
recs=[]; scope='historical'; obj=None; tbl=None; cols=None; cur=None

def flush():
    global cur
    if cur:
        for k in ('desc','note'):
            cur[k]=re.sub(r'\s+',' ',cur[k]).strip().strip('-').strip()
        recs.append(cur); cur=None

for n in range(1034,1056):
    for line in pages[n].split('\n'):
        s=line.strip()
        if not s: continue
        if s.startswith('Copyright') or re.match(r'^\d+ / 1466$',s) or s.startswith('Parent topic'): continue
        if s=='Real-Time Monitoring Indexes': flush(); scope='realtime'; cols=None; continue
        if s=='Historical Monitoring Indexes': flush(); scope='historical'; cols=None; continue
        if s in OBJ and not line.startswith(' '): flush(); obj=s; cols=None; continue
        m=re.match(r'^Table \d+ (.+)$', s)
        if m: flush(); tbl=m.group(1).rstrip('.'); cols=None; continue
        if 'Index Description' in line:                     # 表头：按列标签取列起点
            flush()
            a=line.find('Index'); b=line.find('Index Description')
            c=line.find('Data Type'); dpos=line.find('Index Value Description')
            cols=(a,b,c if c>0 else dpos, dpos if dpos>0 else len(line))
            continue
        if cols and re.match(r'^\s*IDX_\d\d_\d\d_\d\d\d', line):
            flush()
            cur={'scope':scope,'object':obj,'table':tbl,
                 'id':line[cols[0]:cols[1]].strip(),
                 'desc':line[cols[1]:cols[2]],
                 'dtype':line[cols[2]:cols[3]].strip(),
                 'note':line[cols[3]:]}
            continue
        if cur is not None and cols:
            if s.startswith('{') or s.startswith('Index'): continue
            cur['desc']+=' '+line[cols[1]:cols[2]]
            if not cur['dtype']: cur['dtype']=line[cols[2]:cols[3]].strip()
            cur['note']+=' '+line[cols[3]:]
flush()
FIX={'g':'string','ing':'string','ring':'string','rray':'array','':'string'}
for r in recs:
    r['dtype']=FIX.get(r['dtype'], r['dtype'])
    r['desc']=re.sub(r'\s+(str|strin|stri|s)\b\.?$','',r['desc']).strip()
    r['desc']=re.sub(r'\s+str\s+',' ',r['desc'])
    r['desc']=re.sub(r'\s{2,}',' ',r['desc']).strip()
seen={}
for r in recs: seen.setdefault(r['id'], r)
print('records',len(recs),'unique',len(seen))
print(collections.Counter((r['scope'],r['object']) for r in seen.values()))
out=OUT
with open(out,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['id','scope','object','table','desc','dtype','note'])
    w.writeheader()
    for r in sorted(seen.values(), key=lambda x:x['id']): w.writerow(r)
print(out)
