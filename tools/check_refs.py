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

本脚本查四类问题，由易到难：
  1. 悬空引用 —— 引用了登记册里没有的编号
  2. 编号断号 —— 登记册自身有缺号（已记明的除外）
  3. 章节号引用 —— 「LD-07 第 9 章」指向不存在或标题对不上的章；
     以及 docx 与 LLD/*.md 的章目录是否漂移（后者是本脚本
     「扫 md 等价于扫全部正文」这一前提的保障）
  4. 跨文件交叉校验 —— 指标、契约、DDL、平台指标之间的一致性

查不到的仍有一类，交接时要靠人：**文档里的数字与实际不符**
（如「共 45 项」而实际 48 项）。第 3 节的「条数声明」只把声明列出来，
不做判断——因为「共几条」指的是哪一组，脚本无从得知。

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

# ---------------------------------------------------------------------------
# 章节号引用校验
#
# 为什么要有这一节：编号（CN/DD/IR…）的引用由上面那几段守着，但「LD-07 第 7 章」
# 这种**章节号**引用没人守。2026-10 实测发现：LD-07 插入新的第 7 章、原 7–11 改为
# 8–12 之后，追溯矩阵里 16 处仍指着旧章节号，md 与 xlsx 各一份。这类失效引用
# 不会报错、不会被任何现有检查发现，而交接时读者翻两次翻不到就不再信任文档。
#
# 权威来源是 docx 的一级标题。LLD/*.md 是 docx 的派生物，所以这里同时校验
# 两者的章目录是否一致——它们一旦漂移，本脚本「扫 md 等价于扫全部正文」的
# 前提就不成立了，那是比个别失效引用更要紧的问题。
# ---------------------------------------------------------------------------
print('\n=== 章节号引用校验 ===')

DOCX_OF = {
    'LD-00': 'LD-00_详细设计说明与文档索引.docx',
    'LD-01': 'LD-01_数据接入详细设计.docx',
    'LD-02': 'LD-02_数据模型详细设计.docx',
    'LD-03': 'LD-03_指标与口径详细设计.docx',
    'LD-04': 'LD-04_服务层与接口详细设计.docx',
    'LD-05': 'LD-05_展现层详细设计.docx',
    'LD-06': 'LD-06_管理界面详细设计.docx',
    'LD-07': 'LD-07_安全与权限详细设计.docx',
    'LD-08': 'LD-08_高可用与部署详细设计.docx',
    'LD-09': 'LD-09_开源许可与组件合规.docx',
    'LD-10': 'LD-10_测试方案.docx',
    'HLD':   'OSS911_BI数据仓库_概要设计说明书_HLD_v0.3.docx',
}
MD_OF = {
    'LD-00': 'LLD/LD-00-overview.md',       'LD-01': 'LLD/LD-01-data-ingestion.md',
    'LD-02': 'LLD/LD-02-data-model.md',     'LD-03': 'LLD/LD-03-metrics.md',
    'LD-04': 'LLD/LD-04-service-layer.md',  'LD-05': 'LLD/LD-05-presentation.md',
    'LD-06': 'LLD/LD-06-admin-consoles.md', 'LD-07': 'LLD/LD-07-security.md',
    'LD-08': 'LLD/LD-08-ha-deployment.md',  'LD-09': 'LLD/LD-09-oss-compliance.md',
    'LD-10': 'LLD/LD-10-test-plan.md',
}
# 标题一致性只在这两处查：它们是刻意抄录章标题的地方。正文里的
# 「LD-07 第 3 章已经说过……」后面跟的是句子而不是标题，在那里做标题
# 匹配只会产出噪声——而噪声会让整条检查被忽略，那比没有检查更糟。
TITLE_SCOPE = ('LLD/REF-HD-02-traceability.md', 'LLD/LD-00-overview.md')

def _subseq(a, b):
    """a 的字符是否按原序出现在 b 中（允许中间插字）。"""
    it = iter(b)
    return all(ch in it for ch in a)

def norm(t):
    return re.sub(r'[\s·×xX*：:，,。.、/（）()「」【】\[\]—\-]+', '', t)

# 节号一并收。引用里「LD-08 9 两个机架的固有约束」给的是 9.2 的标题而不是
# 第 9 章的标题——只拿一级标题比，这种引用会被误判为指错了章。
HEAD = (('Heading 1', '标题 1'), ('Heading 2', '标题 2'), ('Heading 3', '标题 3'))

def docx_chapters(path):
    out = {}
    want = {st for grp in HEAD for st in grp}
    for para in docx.Document(path).paragraphs:
        if para.style.name in want:
            m = re.match(r'^\s*(\d+(?:\.\d+)*)\s+(.*)$', para.text.strip())
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out

def md_chapters(path):
    out = {}
    for line in io.open(path, encoding='utf-8'):
        m = re.match(r'^#{2,4}\s+(\d+(?:\.\d+)*)\s+(.*)$', line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out

def top(d):
    """只取一级章，用于「该文档共几章」这类判断。"""
    return {int(k): v for k, v in d.items() if '.' not in k}

CHAPTERS = {c: docx_chapters(p) for c, p in DOCX_OF.items() if os.path.exists(p)}

# (1) docx 与 md 的章目录是否一致。
# 这一项比个别失效引用更要紧：md 是 docx 的派生物，两者一旦漂移，
# 本脚本「扫 md 等价于扫全部正文」的前提就不成立了。
drift = []
for code, md in MD_OF.items():
    if code not in CHAPTERS or not os.path.exists(md):
        continue
    a, b = top(CHAPTERS[code]), top(md_chapters(md))
    if not b:
        continue
    for n in sorted(set(a) | set(b)):
        if n not in a:
            drift.append(f'{code}: md 有第 {n} 章「{b[n]}」而 docx 没有')
        elif n not in b:
            drift.append(f'{code}: docx 有第 {n} 章「{a[n]}」而 md 没有')
        elif norm(a[n]) != norm(b[n]):
            drift.append(f'{code} 第 {n} 章标题不一致：docx「{a[n]}」/ md「{b[n]}」')

# (2) 引用的章号是否存在；在 TITLE_SCOPE 内另比标题。
SCAN = sorted(set(glob.glob('LLD/*.md') + glob.glob('infra/**/*.md', recursive=True)
                  + glob.glob('contracts/*.yaml') + glob.glob('metrics/*.yaml')
                  + glob.glob('ddl/*.sql')))
# 文档码之后必须紧跟「第? N 章?」才算一次章节引用，否则「HLD v0.3」一类会被误读。
# 续写形式「LD-07 2 三层权限 · 3 出口路径…」靠 ITEM 在 tail 里反复匹配。
REF = re.compile(r'(LD-\d{2}|HLD)\s+(第?\s*\d+(?:\.\d+)*\s*章?'
                 r'(?:[^|｜，。；;\n/]{0,30})?(?:·\s*\d+(?:\.\d+)*\s*'
                 r'[^|｜，。；;\n/·]{0,30})*)')
# 按「·」切段后逐段解析，每段必须以「第? N 章?」开头才算一次章节引用。
# 不能对整个 tail 直接 finditer：「LD-01 6.8 · DD-57）」里的 57 会被当成章号。
ITEM = re.compile(r'^\s*第?\s*(\d+(?:\.\d+)*)\s*章?\s*(.{0,30})$', re.S)

bad_num, bad_title = [], []
for f in SCAN:
    txt = io.open(f, encoding='utf-8').read()
    check_title = f.replace(os.sep, '/') in TITLE_SCOPE
    for m in REF.finditer(txt):
        code, tail = m.group(1), m.group(2)
        if code not in CHAPTERS:
            continue
        tops = top(CHAPTERS[code])
        for seg in tail.split('·'):
            im = ITEM.match(seg)
            if not im:
                continue
            key = im.group(1)                 # 可能是「9」也可能是「9.2」
            n = int(key.split('.')[0])
            if n not in tops:
                bad_num.append(f'{os.path.basename(f)}: 引用 {code} 第 {n} 章，'
                               f'但该文档只有 1..{max(tops)} 章')
                continue
            if not check_title:
                continue
            # 标题可能抄的是本章的，也可能抄的是其中某一小节的。
            # 只要能对上任意一个就算对上——引用写「LD-08 9 两个机架的固有约束」
            # 指的是 9.2，这是合理的引法。
            cands = [v for k, v in CHAPTERS[code].items()
                     if k == key or k.startswith(key + '.')]
            ref_t = norm((im.group(2) or '').strip())
            act_t = norm(cands[0]) if cands else ''
            # 这里的难点不是比字符串，是把「写简了」和「指错了」分开。
            # 引用标题有三种合法变形，都不该报：
            #   省字    「分区主键」对「分区表主键的一个固有限制」
            #   加限定  「业务对象模型（站点维度）」对「业务对象模型」
            #   换说法  「raw 与 staging（保留与分区）」对「…的结构规范」
            # 前两种保持原字序，用双向子序列判定即可；第三种只在开头相同，
            # 用首四字兜住。两条都不满足才是真的指错了章——
            # 「两项前置门」对「脱敏与匿名化」两条都不满足。
            def _ok(a):
                a = norm(a)
                return (ref_t[:4] == a[:4]
                        or _subseq(ref_t, a) or _subseq(a, ref_t))
            if len(ref_t) >= 4 and not any(_ok(c) for c in cands):
                bad_title.append(f'{os.path.basename(f)}: {code} {key} 引作'
                                 f'「{im.group(2).strip()}」，而该处标题是'
                                 f'「{cands[0] if cands else "(无)"}」')

if drift:
    print('  [docx 与 md 的章目录漂移]')
    for x in drift: print('    ' + x)
if bad_num:
    print('  [章号不存在 —— 失效引用]')
    for x in sorted(set(bad_num)): print('    ' + x)
if bad_title:
    print('  [章号存在但标题对不上 —— 多半是改过章节号或标题没同步]')
    for x in sorted(set(bad_title)): print('    ' + x)
if not (drift or bad_num or bad_title):
    print('  无')

CHAPTER_PROBLEMS = drift + bad_num + bad_title

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
if dangling or problems or UNDOC_GAPS or CHAPTER_PROBLEMS:
    print('校验未通过')
    sys.exit(1)
print('校验通过')
sys.exit(0)
