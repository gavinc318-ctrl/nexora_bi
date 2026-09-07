# -*- coding: utf-8 -*-
"""Figure 4-1  Ingestion Framework and Runtime Flow  接入框架与运行时流程"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 176.0, 116.0
fig, ax = newfig(W, H)
title(ax, 3, 113, 'Figure 4-1   Ingestion Framework and Runtime Flow',
      'Configuration drives the mechanics of fetching; code and SQL hold the semantics')

# ================= Panel A  三层切分 =================
note(ax, 3, 104.5, 'A.  Separation of concerns', fs=9.0, c='ink', bold=True, ha='left')
for y, kind, kc, t, s in [
    (90, 'CODE\nfixed set', 'ing', 'Connector Runtime',
     'pg_read  .  rest_api  .  kafka_consume  .  file_drop\n'
     'protocol, paging, rate limiting, retry, resume, batch commit  —  no business knowledge'),
    (77, 'CONFIG\nGit', 'sto', 'Source Contract Registry',
     'watermark  .  idempotency key  .  soft delete  .  late window  .  schedule  .  expectations  .  contract version\n'
     'Git is authoritative, the copy in the database is read-only; batch DAGs are generated from it'),
    (64, 'SQL\nGit', 'pro', 'Transformation  (dbt models)',
     'field mapping  .  cleansing  .  business rules  .  metric logic\n'
     'all business semantics live here and nowhere else')]:
    tag(ax, 3, y, 15, 10, kind, kc, fs=6.8)
    box(ax, 21, y, 116, 10, '', None, kc)
    note(ax, 24, y+7.0, t, fs=8.4, c='ink', bold=True, ha='left', va='center')
    note(ax, 24, y+3.2, s, fs=6.0, ha='left', va='center')
box(ax, 141, 64, 32, 36, '', None, 'ops', fc=BG['ops'])
note(ax, 157, 94, 'Why it is split this way', fs=7.6, c='ops', bold=True)
note(ax, 143, 88,
     'Adding a new source is one YAML\nblock plus one staging model.\nNo new DAG code is written.\n\n'
     'The answers still owed by DS and\nICP are values of registry fields,\nso the design does not wait\n'
     'on them.\n\nNo expression language in YAML —\nthat is how a config file quietly\nbecomes an untestable\n'
     'programming language.', fs=5.9, ha='left', va='top')
ax.plot([3, 173], [58.5, 58.5], color=P['soft'], lw=1.0, ls=(0, (4, 3)))

# ================= Panel B  运行时流程 =================
note(ax, 3, 55.5, 'B.  Runtime flow of one ingestion run', fs=9.0, c='ink', bold=True, ha='left')

def step(x, y, n, c):
    ax.add_patch(FancyBboxPatch((x-1.6, y-1.6), 3.2, 3.2,
        boxstyle='circle,pad=0', fc='white', ec=P[c], lw=1.4, zorder=8))
    note(ax, x, y, str(n), fs=6.4, c=c, bold=True, z=9)

CH = [(4, 20, 'Source', 'external system', 'ext', True),
      (28, 24, 'Connector', 'fetch by watermark\npaging . retry . resume', 'ing', False),
      (56, 24, 'Expectations', 'batch-level validation\nnot null . unique . domain', 'ing', False),
      (84, 22, 'raw', 'as-is + JSONB payload\n+ run metadata', 'sto', False),
      (110, 26, 'staging', 'semantic mapping\ncleansing . de-duplication', 'sto', False),
      (140, 24, 'core', 'conformed model\nsingle source of truth', 'sto', False)]
for x, w, t, s, c, d in CH:
    box(ax, x, 26, w, 12, t, s, c, dashed=d, tfs=8.4, sfs=5.9)
for x0, x1 in [(24, 27.7), (52, 55.7), (80, 83.7), (106, 109.7), (136, 139.7)]:
    arrow(ax, x0, 32, x1, 32, 'mid', 1.8)
note(ax, 81.8, 34.6, 'pass', fs=5.6, c='ok', bold=True)

box(ax, 28, 44, 24, 11, 'DDL Snapshot & Drift Check', 'compare source structure\nevery run',
    'pro', tfs=7.2, sfs=5.8)
arrow(ax, 40, 43.8, 40, 38.3, 'pro', 1.6)
box(ax, 84, 44, 22, 11, 'Value Profiler', 'distinct code values,\ncounts, first / last seen',
    'pro', tfs=7.4, sfs=5.8)
arrow(ax, 95, 38.2, 95, 43.7, 'pro', 1.6)
box(ax, 112, 44, 30, 11, 'Clarification Queue  ( M-03 )',
    'unknown values ranked by rows affected and metrics touched', 'pro', tfs=7.2, sfs=5.8)
arrow(ax, 106.3, 49.5, 111.7, 49.5, 'pro', 1.6)

box(ax, 28, 11, 24, 11, 'Watermark & Run State', 'run_id . from / to watermark\nrow count . outcome',
    'sto', tfs=7.2, sfs=5.8)
arrow(ax, 40, 25.8, 40, 22.3, 'sto', 1.6, dbl=True)
box(ax, 56, 11, 24, 11, 'Quarantine & Alert', 'whole batch held,\nnothing enters raw',
    'bad', tfs=7.4, sfs=5.8, fc=BG['bad'])
arrow(ax, 68, 25.8, 68, 22.3, 'bad', 1.6)
note(ax, 70.4, 24.0, 'fail', fs=5.6, c='bad', bold=True, ha='left')
box(ax, 110, 11, 26, 11, 'Mapping Registry', 'verified / mapped-unverified /\nunknown  ( three states )',
    'sto', tfs=7.4, sfs=5.8)
arrow(ax, 123, 25.8, 123, 22.3, 'sto', 1.6, dbl=True)
box(ax, 140, 11, 24, 11, 'Metric Flag', 'depends_on_unverified\ncarried to the API response',
    'svc', tfs=7.4, sfs=5.8)
arrow(ax, 136.3, 16.5, 139.7, 16.5, 'svc', 1.6)

note(ax, 3, 5.0,
     'Two properties make later clarification safe:  raw is written as-is, so a field understood three years from now '
     'can be rebuilt over full history;\nand an unverified mapping is never published silently — the metric carries a flag '
     'and the unknown code values are queued, not folded into "Other".',
     fs=6.6, c='sto', bold=True, ha='left')
save(fig, 'f3_ingestion')
