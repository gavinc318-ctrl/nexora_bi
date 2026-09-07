# -*- coding: utf-8 -*-
"""Figure 3-2  External Interface Topology  外部接口拓扑图
v0.3: 入向补 IF-07（AI 结果回流）；出向补 IF-37/38/39（AI 包）、IF-41（HudHud 地图服务）；
      移动端改为本期交付并改走 IF-40（经 DMZ）；IF-34 改为报表与告警的统一投递。"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 172.0, 128.0
fig, ax = newfig(W, H)
title(ax, 3, 125, 'Figure 3-2   External Interface Topology',
      'Protocol, direction and cadence of every interface crossing the system boundary')
note(ax, 3, 116.0, 'Inbound   IF-01 . . IF-05 . IF-07', fs=7.6, c='ext', bold=True, ha='left')
note(ax, 169, 116.0, 'Outbound   IF-30 . . IF-41', fs=7.6, c='pre', bold=True, ha='right')
note(ax, 86, 111.0,
     'Rule:  every external interface terminates in the Ingestion layer (inbound) or the Service layer (outbound).\n'
     'No external party connects to the database, and no internal component calls out directly.',
     fs=7.0, c='sto', bold=True)

# ---- 系统边界内部 ----
panel(ax, 52, 8, 62, 94, 'sto', fill=False, lw=1.6)
note(ax, 83, 104.5, 'BI & Data Warehouse Subsystem', fs=9.6, c='ink', bold=True)

box(ax, 54.5, 12, 12.5, 82, '', None, 'ing', fc=BG['ing'])
note(ax, 60.75, 90.0, 'INGESTION', fs=8.0, c='ing', bold=True)
note(ax, 60.75, 58.0, 'connector\nruntime\n\ncontract\nregistry\n\nexpectations\n& quarantine', fs=5.9)

box(ax, 70.5, 46, 19.0, 48, '', None, 'sto', fc=BG['sto'])
note(ax, 80.0, 90.0, 'STORAGE', fs=8.4, c='sto', bold=True)
note(ax, 80.0, 72.0, 'PostgreSQL cluster\nobject storage\nbackup repository', fs=6.0)

box(ax, 70.5, 12, 19.0, 22, '', None, 'pro', fc=BG['pro'])
note(ax, 80.0, 30.0, 'PROCESSING', fs=8.0, c='pro', bold=True)
note(ax, 80.0, 22.0, 'batch . near-realtime\nrealtime . quality', fs=6.0)

box(ax, 93.0, 12, 18.5, 82, '', None, 'svc', fc=BG['svc'])
note(ax, 102.25, 90.0, 'SERVICE', fs=8.4, c='svc', bold=True)
note(ax, 102.25, 58.0, 'semantic & metric\nquery API\nrecord search\nrealtime push\n'
     'alerting & delivery\nreport rendering\nmedia service\nAI data service\nidentity & authz', fs=6.0)

arrow(ax, 67.2, 62, 70.3, 62, 'sto', 2.0)
arrow(ax, 80.0, 45.8, 80.0, 34.2, 'pro', 1.8, dbl=True)
arrow(ax, 89.7, 62, 92.8, 62, 'svc', 2.0)

# ---- 入向 ----
for y, t, s, i in [
    (86.0, 'Legacy Archive', 'Hexagon . PostgreSQL . from 2016 . multi-version schema',
     'IF-01   libpq / TLS   one-time full load'),
    (70.4, 'DS CAD Presentation DB', 'read-only account on base tables',
     'IF-02   libpq / TLS   watermark incremental, 1 min'),
    (54.8, 'ICP Telephony', 'REST API only, no DB replication',
     'IF-03   HTTPS / REST-JSON   polling 2-10 s'),
    (39.2, 'External Kafka', 'reserved, not enabled at go-live',
     'IF-04   Kafka / SASL+TLS   consumer group'),
    (23.6, 'File Drop', 'manual and fallback feeds',
     'IF-05   SFTP   on arrival, checksum manifest'),
    (8.0, 'AI Package  —  inference write-back', 'model id, version, run time, confidence',
     'IF-07   REST-JSON or file drop   into raw, never into business facts')]:
    box(ax, 3, y, 44, 11.5, t, s, 'ext', dashed=True, tfs=8.2, sfs=5.7)
    note(ax, 25, y+1.9, i, fs=5.7, c='ext', bold=True)
    arrow(ax, 47.3, y+5.7, 54.2, y+5.7, 'ext', 1.7)

# ---- 出向 ----
for y, t, s, i, d in [
    (86.0, 'BI Portal & Dashboards', 'custom shell, Superset behind it',
     'IF-30   HTTPS / REST-JSON   interactive', False),
    (73.0, 'Wall Display', 'unattended, self-recovering',
     'IF-31   HTTPS + WSS   push 2-10 s', False),
    (60.0, 'Mobile App', 'aggregate-only management view, pull on open',
     'IF-40   HTTPS via DMZ reverse proxy   MDM distributed', False),
    (47.0, 'Report & Alert Delivery', 'scheduled reports, AR and EN as separate documents',
     'IF-34   e-mail gateway / SMS gateway / portal   contractor operated', False),
    (34.0, 'ESB & External Applications', 'incident and alert web services, database data services',
     'IF-33   HTTPS / REST + ESB adapter   mandatory  ( 1.6.2.29 / .31.1 / .31.2 )', False),
    (21.0, 'AI Package  —  consumption', 'wide-table supply, semantic catalogue, narrative generation',
     'IF-37 / IF-38 / IF-39   HTTPS / REST-JSON   structured queries only, no SQL', False),
    (8.0, 'HudHud Map Service', 'embedded component and tiles, deployed on premises',
     'IF-41   HTTPS on the internal network   we supply the aggregates', False)]:
    box(ax, 119, y, 50, 11.5, t, s, 'pre', dashed=d, tfs=8.2, sfs=5.7)
    note(ax, 144, y+1.9, i, fs=5.7, c='pre', bold=True)
    arrow(ax, 111.8, y+5.7, 118.7, y+5.7, 'pre', 1.7, ls=(0, (4, 2.5)) if d else '-')

# ---- 基础设施 ----
box(ax, 52, 1.0, 29.5, 5.6, 'MOI Directory  (AD / LDAP)', None, 'ops', tfs=7.4, fc=BG['ops'])
box(ax, 84.5, 1.0, 29.5, 5.6, 'NTP  (shared with CAD / ICP)', None, 'ops', tfs=7.4, fc=BG['ops'])
arrow(ax, 66.7, 7.9, 66.7, 6.8, 'ops', 1.6)
arrow(ax, 99.2, 7.9, 99.2, 6.8, 'ops', 1.6)
note(ax, 64.5, 10.4, 'IF-35  LDAPS', fs=5.9, c='ops', bold=True, ha='right')
note(ax, 101.4, 10.4, 'IF-36  NTP', fs=5.9, c='ops', bold=True, ha='left')

save(fig, 'f2_interfaces')
