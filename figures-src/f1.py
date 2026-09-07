# -*- coding: utf-8 -*-
"""Figure 3-1  System Layered Component Diagram  系统分层组件图
本图不依赖 lib.py（早于其它图完成），自带样式定义。
v0.3: L4 增补记录检索、告警评估、投递服务与 MCP；L5 移动端转为本期交付；
      L6 增补数据分类与发现并将控制台扩至 M-11；L2 注明 PostGIS。"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
import os

plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['svg.fonttype'] = 'none'
OUT = os.environ.get('OUT', '.')

W, H = 160.0, 141.0
fig = plt.figure(figsize=(16, 14.1), dpi=160)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')
ax.add_patch(Rectangle((0, 0), W, H, fc='#ffffff', ec='none', zorder=0))

C = {'L0': ('#6b7280', '#f4f4f5'), 'L1': ('#0f766e', '#eef7f5'),
     'L2': ('#1d4ed8', '#eef2fd'), 'L3': ('#6d28d9', '#f3eefc'),
     'L4': ('#b45309', '#fdf4e7'), 'L5': ('#be123c', '#fdeef1'),
     'L6': ('#334155', '#f1f3f6')}
BX, BW = 17.0, 104.0

def band(y0, y1, key, code, name, dashed=False):
    ec, bg = C[key]
    ax.add_patch(FancyBboxPatch((BX, y0), BW, y1-y0,
        boxstyle='round,pad=0,rounding_size=1.4', fc=bg, ec=ec,
        lw=1.0, ls='--' if dashed else '-', zorder=1))
    ax.add_patch(FancyBboxPatch((3.0, y0), 12.0, y1-y0,
        boxstyle='round,pad=0,rounding_size=1.4', fc=ec, ec=ec, lw=1.0, zorder=2))
    cy = (y0+y1)/2
    ax.text(9.0, cy+2.3, code, ha='center', va='center', color='white',
            fontsize=11, fontweight='bold', zorder=3)
    ax.text(9.0, cy-2.6, name, ha='center', va='center', color='white',
            fontsize=7.6, zorder=3, linespacing=1.3)

def box(x, y, w, h, title, sub=None, key='L1', dashed=False, tfs=8.4, sfs=6.0):
    ec, _ = C[key]
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle='round,pad=0,rounding_size=1.0', fc='white', ec=ec,
        lw=1.1 if dashed else 1.5, ls='--' if dashed else '-', zorder=4))
    if sub:
        ax.text(x+w/2, y+h*0.63, title, ha='center', va='center', fontsize=tfs,
                fontweight='bold', color='#111827', zorder=5, linespacing=1.25)
        ax.text(x+w/2, y+h*0.27, sub, ha='center', va='center', fontsize=sfs,
                color='#4b5563', zorder=5, linespacing=1.35)
    else:
        ax.text(x+w/2, y+h/2, title, ha='center', va='center', fontsize=tfs,
                fontweight='bold', color='#111827', zorder=5, linespacing=1.4)

def chip(x, y, w, h, t, s=None):
    ec, bg = C['L2']
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle='round,pad=0,rounding_size=0.7', fc=bg, ec=ec, lw=1.1, zorder=5))
    if s:
        ax.text(x+w/2, y+h*0.64, t, ha='center', va='center', fontsize=7.6,
                fontweight='bold', color='#1e3a8a', zorder=6)
        ax.text(x+w/2, y+h*0.26, s, ha='center', va='center', fontsize=5.9,
                color='#475569', zorder=6)
    else:
        ax.text(x+w/2, y+h/2, t, ha='center', va='center', fontsize=7.4,
                fontweight='bold', color='#1e3a8a', zorder=6)

def arr(x0, y0, x1, y1, color='#475569', lw=2.0, ls='-', dbl=False, z=6):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1),
        arrowstyle='<|-|>' if dbl else '-|>', mutation_scale=13, lw=lw,
        color=color, ls=ls, shrinkA=0, shrinkB=0, zorder=z))

G5 = [17.9 + i*21.04 for i in range(5)]; W5 = 17.84
G4 = [17.9 + i*26.30 for i in range(4)]; W4 = 23.10
G3 = [31.1 + i*26.30 for i in range(3)]

# ---------------- L0 数据源层 ----------------
band(3, 15, 'L0', 'L0', 'Data\nSources', dashed=True)
for x, (t, s, ifc) in zip(G5, [
    ('Legacy Archive', 'Hexagon . PostgreSQL\none-time full load  (from 2016)', 'IF-01'),
    ('DS CAD\nPresentation DB', 'read-only account\nwatermark incremental', 'IF-02'),
    ('ICP Telephony', 'REST API only\nno DB replication', 'IF-03'),
    ('External Kafka', 'reserved . not enabled', 'IF-04'),
    ('File Drop / AI Package', 'SFTP . inference write-back', 'IF-05 / IF-07')]):
    box(x, 4.4, W5, 8.6, t, s, 'L0', dashed=True, tfs=8.0, sfs=5.8)
    ax.text(x+W5/2, 16.7, ifc, ha='center', va='center', fontsize=6.6,
            color='#6b7280', fontweight='bold', zorder=7)

# ---------------- L1 接入层 ----------------
band(18.5, 32.5, 'L1', 'L1', 'Ingestion')
for x, (t, s) in zip(G5, [
    ('Connector Runtime', 'pg_read . rest_api\nkafka_consume . file_drop'),
    ('Source Contract\nRegistry', 'Git authoritative\nDB copy read-only'),
    ('Watermark &\nRun State', 'run_id . from/to watermark\nrow count . outcome'),
    ('Expectations &\nQuarantine', 'batch-level validation\nreject whole batch'),
    ('Archive Migration', 'multi-version schema\nnormalisation . reconciliation')]):
    box(x, 20.0, W5, 11.0, t, s, 'L1', sfs=5.7)

# ---------------- L2 存储层 ----------------
band(36, 60, 'L2', 'L2', 'Storage')
box(17.9, 37.5, 61.0, 21.0, '', None, 'L2')
ax.text(48.4, 56.6, 'PostgreSQL Cluster  (single instance . logical layering)',
        ha='center', va='center', fontsize=8.4, fontweight='bold',
        color='#111827', zorder=6)
chip(19.8, 47.6, 10.5, 5.6, 'raw', 'landing')
chip(33.0, 47.6, 11.5, 5.6, 'staging', 'cleansing')
chip(47.2, 47.6, 10.5, 5.6, 'core', 'source of truth')
chip(61.5, 50.6, 15.0, 4.6, 'mart', 'subject . BI')
chip(61.5, 44.6, 15.0, 4.6, 'ai', 'wide tables . inference')
arr(30.3, 50.4, 32.8, 50.4, '#1d4ed8', 1.5, z=7)
arr(44.5, 50.4, 47.0, 50.4, '#1d4ed8', 1.5, z=7)
arr(57.7, 50.4, 61.3, 52.9, '#1d4ed8', 1.5, z=7)
arr(57.7, 50.4, 61.3, 46.9, '#1d4ed8', 1.5, z=7)
chip(19.8, 39.2, 18.0, 4.2, 'meta  registry . lineage')
chip(40.3, 39.2, 17.0, 4.2, 'audit  access trail')
ax.text(68.0, 41.3, 'Patroni role authority . etcd quorum\npgBouncer connection pool\n'
        'PostGIS spatial extension\n(a PG extension, not a second DB product)', ha='center',
        va='center', fontsize=5.2, color='#475569', zorder=6, linespacing=1.35)
box(81.9, 41.0, 17.5, 14.0, 'Object Storage',
    'field media . report output\nAI training export . S3 API', 'L2', sfs=5.7)
box(102.4, 41.0, 17.5, 14.0, 'Backup Repository',
    'full / incr / WAL\nstorage independent of DB', 'L2', tfs=8.0, sfs=5.7)

# ---------------- L3 加工层 ----------------
band(63.5, 78.5, 'L3', 'L3', 'Processing')
for x, (t, s) in zip(G5, [
    ('Batch Orchestration', 'DAGs generated from registry\nbackfill . rerun . SLA alerts'),
    ('Model Build', 'staging > core > mart / ai\nincremental . lineage . tests'),
    ('Near-realtime\nAggregator', 'minute-level day-bucket\nrecompute affected partitions'),
    ('Realtime State\nService', 'short-cycle "now" polling\nnot persisted, not in stats'),
    ('Data Quality &\nDiscovery Scanner', 'reconciliation . day-close\nsensitive-content scan')]):
    box(x, 65.0, W5, 12.0, t, s, 'L3', tfs=8.0, sfs=5.6)

# ---------------- L4 服务层 ----------------
band(82, 113.4, 'L4', 'L4', 'Service')
for x, (t, s) in zip(G4, [
    ('Semantic & Metric Service', 'metric definitions . rules . grain\nsingle project-wide authority'),
    ('Query API', 'metric x dimension x time\nrate limit . cache . timeout'),
    ('Record Search Service', 'configurable criteria set\nindexed . capped . replica only'),
    ('Realtime Push Gateway', 'WebSocket subscriptions\npermission-filtered . catch-up')]):
    box(x, 104.4, W4, 8.0, t, s, 'L4', tfs=8.2, sfs=5.8)
for x, (t, s) in zip(G4, [
    ('Report Rendering', 'PDF / Word / Excel\nAR-EN bilingual . RTL'),
    ('Alert Rules & Evaluation', 'thresholds . windows . scope\ndedupe . silence . events to core'),
    ('Delivery Service', 'reports + alerts\nemail . SMS . in-portal'),
    ('Media Service', 'short-lived access tokens\nstorage location never exposed')]):
    box(x, 95.0, W4, 8.0, t, s, 'L4', tfs=8.2, sfs=5.8)
for x, (t, s, dsh) in zip(G3, [
    ('AI Data Service', 'wide-table query . bulk export\nsemantic catalog . narrative call', False),
    ('MCP Server', 'read-only tools over the same\nservice layer . optional add-on', True),
    ('Identity & Authorization', 'field-level visibility matrix\nrow-level department filter', False)]):
    box(x, 85.6, W4, 8.0, t, s, 'L4', dashed=dsh, tfs=8.2, sfs=5.8)
ax.text(69.0, 83.4, 'The single egress for all external access  —  no direct database '
        'connection from the presentation layer or any external system',
        ha='center', va='center', fontsize=6.8, color='#b45309',
        fontweight='bold', zorder=7)

# ---------------- L5 展现层 ----------------
band(117.4, 128.4, 'L5', 'L5', 'Presentation')
for x, (t, s, d, ifc) in zip(G5, [
    ('BI Portal', 'custom shell\nSuperset behind it', False, 'IF-30'),
    ('Dashboards', '20 predefined + custom', False, 'IF-30'),
    ('Wall Display', 'persistent connection\nunattended self-recovery', False, 'IF-31'),
    ('Mobile App', 'aggregate-only management view\npull on open . via DMZ proxy', False, 'IF-40'),
    ('ESB / External Apps / AI', 'data services + adapter\nmandatory deliverable', False, 'IF-33 / 37-39')]):
    box(x, 118.7, W5, 8.4, t, s, 'L5', dashed=d, tfs=8.2, sfs=5.6)
    ax.text(x+W5/2, 115.9, ifc, ha='center', va='center', fontsize=6.6,
            color='#9f1239', fontweight='bold', zorder=7)

# ---------------- L6 管理与运维层（横切） ----------------
ax.add_patch(FancyBboxPatch((133.0, 18.5), 24.0, 109.9,
    boxstyle='round,pad=0,rounding_size=1.4', fc=C['L6'][1], ec=C['L6'][0],
    lw=1.2, zorder=1))
ax.text(145.0, 125.8, 'L6   Management & Operations', ha='center', va='center',
        fontsize=8.8, fontweight='bold', color='#334155', zorder=6)
ax.text(145.0, 122.4, '(cross-cutting L1 - L5)', ha='center', va='center',
        fontsize=6.8, color='#64748b', zorder=6)
for yy, hh, t, s in [
    (94.0, 24.0, 'Admin Console',
     'M-01 Source Ingestion\nM-02 Job Scheduling\nM-03 Data Quality\n'
     'M-04 Metric Dictionary\nM-05 Reports & Dashboards\nM-06 Users & Permissions\n'
     'M-07 Audit Query\nM-08 Runtime Monitoring\nM-09 Backup & Restore\n'
     'M-10 Reference Data\nM-11 Classification & Discovery'),
    (75.5, 15.0, 'Monitoring & Alerting',
     'availability . replication lag\nquery latency distribution\nSLA evidence data'),
    (58.0, 14.0, 'Audit & Traceability',
     'who queried what, when\nexport trail . append-only'),
    (40.5, 14.0, 'Classification & Discovery',
     'periodic scan . labels\ndrives masking + field visibility'),
    (23.0, 14.0, 'Configuration & Release',
     'Ansible + Podman\nthree environments\noffline mirror')]:
    box(135.0, yy, 20.0, hh, t, s, 'L6', tfs=7.8, sfs=5.5)

# ---------------- 数据流与查询路径 ----------------
arr(45.0, 15.2, 45.0, 18.3, '#475569', 2.4)
arr(45.0, 32.7, 45.0, 35.8, '#475569', 2.4)
arr(45.0, 60.2, 45.0, 63.3, '#475569', 2.4, dbl=True)
ax.text(47.3, 61.8, 'read / write', ha='left', va='center', fontsize=6.4,
        color='#475569', zorder=7)
QC = '#b45309'
ax.plot([121.0, 127.0], [52.0, 52.0], color=QC, lw=2.0, ls=(0, (5, 3)), zorder=6)
ax.plot([127.0, 127.0], [52.0, 99.0], color=QC, lw=2.0, ls=(0, (5, 3)), zorder=6)
arr(127.0, 99.0, 121.0, 99.0, QC, 2.0, ls=(0, (5, 3)))
ax.text(127.5, 106.0, 'query bypass\n(analytics replica)', ha='center', va='center',
        fontsize=7.0, color=QC, fontweight='bold', zorder=8, linespacing=1.4)
arr(45.0, 113.6, 45.0, 117.2, '#475569', 2.4)

# ---------------- 标题与图例 ----------------
ax.text(3.0, 139.6, 'OSS911 Makkah 911 Operations Centre  —  BI & Data Warehouse Subsystem',
        ha='left', va='top', fontsize=13, fontweight='bold', color='#111827')
ax.text(3.0, 135.4, 'Figure 3-1   System Layered Component Diagram', ha='left',
        va='top', fontsize=9.6, color='#475569')
lx, ly = 62.0, 133.0
ax.plot([lx, lx+5], [ly, ly], color='#475569', lw=2.2)
ax.text(lx+6.2, ly, 'data flow', ha='left', va='center', fontsize=7.2, color='#374151')
ax.plot([lx+18, lx+23], [ly, ly], color=QC, lw=2.0, ls=(0, (5, 3)))
ax.text(lx+24.2, ly, 'query path', ha='left', va='center', fontsize=7.2, color='#374151')
ax.add_patch(FancyBboxPatch((lx+38, ly-1.5), 5.0, 3.0,
    boxstyle='round,pad=0,rounding_size=0.6', fc='white', ec='#9ca3af', lw=1.1, ls='--'))
ax.text(lx+44.2, ly, 'outside the system boundary / optional', ha='left', va='center',
        fontsize=7.2, color='#374151')

os.makedirs(OUT, exist_ok=True)
fig.savefig(os.path.join(OUT, 'f1_framework.png'), dpi=160, facecolor='white')
fig.savefig(os.path.join(OUT, 'f1_framework.svg'), facecolor='white')
print('saved f1_framework (png + svg)')
