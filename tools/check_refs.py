# -*- coding: utf-8 -*-
"""全局编号与引用一致性校验。

权威来源：
  CN / DD      HD-04 登记册（xlsx）
  AS / IR      HD-03 登记册（docx）
  T            HLD 第 14 章待确认项表（docx）
  REQ          HD-01 需求（经 LLD/REF-HD-01-requirements.md）
  M            metrics/metrics.yaml
  R / D        LD-05A 规格册（xlsx）

被扫描的引用面：LLD/*.md（由各 docx/xlsx 派生，等价于全部正文）、
ddl/*.sql、metrics/*.yaml、contracts/*.yaml、infra/**、各 README。

用法：python3 tools/check_refs.py       （在仓库根目录执行）
退出码非零即有悬空引用。
"""
import re, sys, glob, io, os, collections
import openpyxl, docx, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

def xlsx_col(path, sheet, col=1, pat=None):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[sheet]; out=set()
    for row in ws.iter_rows(min_row=2, max_col=col, values_only=True):
        v = row[col-1]
        if isinstance(v,str) and (pat is None or re.fullmatch(pat, v.strip())):
            out.add(v.strip())
    return out

def docx_table_col(path, table_idx, pat):
    d = docx.Document(path); out=set()
    for r in d.tables[table_idx].rows[1:]:
        m = re.match(pat, r.cells[0].text.strip())
        if m: out.add(m.group(0))
    return out

DEFINED = {}
DEFINED['CN'] = xlsx_col('HD-04_约束与设计决策登记册.xlsx','约束登记册', pat=r'CN-\d+')
DEFINED['DD'] = xlsx_col('HD-04_约束与设计决策登记册.xlsx','设计决策记录', pat=r'DD-\d+')
DEFINED['AS'] = docx_table_col('HD-03_假设与信息需求登记册.docx', 2, r'AS-\d+')
DEFINED['IR'] = docx_table_col('HD-03_假设与信息需求登记册.docx', 4, r'IR-\d+')
DEFINED['T']  = docx_table_col('OSS911_BI数据仓库_概要设计说明书_HLD_v0.3.docx', 53, r'T-\d+')
DEFINED['REQ']= set(re.findall(r'REQ-[A-Z]+-\d+', io.open('LLD/REF-HD-01-requirements.md',encoding='utf-8').read()))
DEFINED['M']  = {m['code'] for m in yaml.safe_load(open('metrics/metrics.yaml'))['metrics']}
rd = xlsx_col('LD-05A_报表与仪表板规格册.xlsx','报表规格（40）', pat=r'R-\d+')
dd = xlsx_col('LD-05A_报表与仪表板规格册.xlsx','仪表板规格（20）', pat=r'D-\d+')
DEFINED['R'] = rd; DEFINED['D'] = dd

FILES = (glob.glob('LLD/*.md') + glob.glob('ddl/*.sql') + glob.glob('metrics/*.yaml')
         + glob.glob('contracts/*.yaml') + glob.glob('contracts/*.md')
         + glob.glob('infra/**/*.yml', recursive=True) + glob.glob('infra/**/*.md', recursive=True)
         + glob.glob('tools/*.md') + glob.glob('recon/*.sql'))

PAT = re.compile(r'\b(CN|DD|AS|IR|T|REQ|M|R|D)-([A-Z]*-?\d+)\b')
used = collections.defaultdict(set)      # token -> 出现的文件
for f in FILES:
    txt = io.open(f, encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'\b(?:CN|DD|AS|IR|T)-\d+\b', txt): used[m.group(0)].add(f)
    for m in re.finditer(r'\bREQ-[A-Z]+-\d+\b', txt): used[m.group(0)].add(f)
    for m in re.finditer(r'\bM-[A-Z]\d+\b', txt): used[m.group(0)].add(f)
    for m in re.finditer(r'(?<![A-Za-z])[RD]-\d{2}\b', txt): used[m.group(0)].add(f)

ALL = set().union(*DEFINED.values())

# 已知且有意的例外。每一条都要写明理由——这个名单一旦变成「加进去就不报了」，
# 校验器就失去意义。
ALLOW = {
    'R-41':  '工作坊新增报表的起编号，不是对现有条目的引用（LD-00 · LD-05A 说明页）',
    'D-21':  '工作坊新增仪表板的起编号，同上',
    'M-I09': '已被 LD-03 拆为 M-I06 · M-I22 · M-I23。两处提及都是「原 M-I09 已拆为…」的沿革说明，刻意保留',
}
GAP_OK = {('T', 29): '增补 T-30 及其后各条时跳号。编号一经发出不再变动，空缺记于 HLD 第 14 章'}

dangling = {k:v for k,v in used.items() if k not in ALL and k not in ALLOW}
print('=== 定义数 ===')
for k in ('CN','DD','AS','IR','T','REQ','M','R','D'):
    print(f'  {k:4s} {len(DEFINED[k]):4d}')

print('\n=== 悬空引用（引用了但登记册里没有）===')
if not dangling:
    print('  无')
for k in sorted(dangling):
    print(f'  {k}  ← {", ".join(sorted(dangling[k]))}')

UNDOC_GAPS = []
print('\n=== 编号断号 ===')
for k in ('CN','DD','AS','IR','T'):
    nums = sorted(int(x.split('-')[1]) for x in DEFINED[k])
    if not nums: continue
    miss = [n for n in range(1, max(nums)+1) if n not in nums]
    undoc = [n for n in miss if (k, n) not in GAP_OK]
    note = '无' if not miss else ', '.join(
        f'{n}{"（已记明）" if (k,n) in GAP_OK else "（未记明）"}' for n in miss)
    print(f'  {k}: 1..{max(nums)}  缺 {note}')
    if undoc: UNDOC_GAPS.append((k, undoc))

print('\n=== 从未被引用的条目（登记了但正文没提）===')
for k in ('CN','DD','IR','AS','T'):
    never = sorted(x for x in DEFINED[k] if x not in used)
    if never: print(f'  {k}: {", ".join(never)}')

print('\n=== 文档中的条数声明 vs 实际 ===')
claims=[]
for f in glob.glob('LLD/*.md'):
    txt=io.open(f,encoding='utf-8').read()
    for m in re.finditer(r'共\s*(\d+)\s*(条|项|个指标)', txt):
        claims.append((f, m.group(0), int(m.group(1))))
for f,c,n in claims:
    print(f'  {os.path.basename(f):32s} {c}')

print('\n=== 跨文件交叉校验 ===')
problems = []

ddl_text = ''.join(io.open(f, encoding='utf-8').read() for f in sorted(glob.glob('ddl/0*.sql')))
ddl_tables = set(re.findall(r'CREATE TABLE (\w+\.\w+)', ddl_text))
ddl_cols    = set(re.findall(r'^\s{4}(\w+)\s', ddl_text, re.M))

for m in yaml.safe_load(open('metrics/metrics.yaml'))['metrics']:
    for t in (m.get('source_tables') or []):
        if t not in ddl_tables:
            problems.append(f'metrics.yaml {m["code"]} 的 source_tables 指向不存在的表 {t}')

smap = yaml.safe_load(open('metrics/icp_source_map.yaml'))['metrics']
idx_ids = set()
if os.path.exists('3rd API/icp_indexes.csv'):
    import csv as _csv
    idx_ids = {r['id'] for r in _csv.DictReader(open('3rd API/icp_indexes.csv', encoding='utf-8'))}
for e in smap:
    if e['code'] not in DEFINED['M']:
        problems.append(f'icp_source_map 引用不存在的指标 {e["code"]}')
    for i in (e.get('recon_index') or []):
        if idx_ids and i not in idx_ids:
            problems.append(f'icp_source_map {e["code"]} 引用不存在的平台指标 {i}')
    if e['source'] == 'unavailable':
        pub = {m['code']: m['published'] for m in yaml.safe_load(open('metrics/metrics.yaml'))['metrics']}
        if pub.get(e['code']):
            problems.append(f'{e["code"]} 标为 unavailable 却是 published')

for f in sorted(glob.glob('contracts/*.yaml')):
    c = yaml.safe_load(open(f))
    if c['target_raw'].split('.')[0] not in ('raw',):
        problems.append(f'{f} 的 target_raw 不在 raw schema')
    for col in c.get('columns', []):
        tgt = col.get('target','')
        if '.' in tgt and tgt.split('.',1)[1] not in ddl_cols:
            problems.append(f'{f} 指向不存在的列 {tgt}')
    for e in (c.get('enums') or {}).values():
        if not os.path.exists(e['table']):
            problems.append(f'{f} 的枚举值表缺失 {e["table"]}')

if problems:
    for p in problems: print('  ' + p)
else:
    print('  无')

print()
if dangling or problems or UNDOC_GAPS:
    print('校验未通过')
    sys.exit(1)
print('校验通过')
sys.exit(0)
