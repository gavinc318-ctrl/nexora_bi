# -*- coding: utf-8 -*-
"""Figure 5-1  Data Layering and Subject Domains  数据分层与主题域"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 104.0
fig, ax = newfig(W, H)
title(ax, 3, 101, 'Figure 5-1   Data Layering and Subject Domains',
      'One database instance, seven schemas; mart and ai are parallel projections of core, never separate pipelines')

def blk(x, w, y, h, name, role, c, bullets):
    box(ax, x, y, w, h, '', None, c)
    note(ax, x+w/2, y+h-4.5, name, fs=9.6, c=c, bold=True)
    note(ax, x+w/2, y+h-8.4, role, fs=6.4, c='ink', bold=True)
    note(ax, x+3, y+h-12.0, bullets, fs=6.0, ha='left', va='top')

blk(3, 30, 56, 34, 'raw', 'Landing', 'sto',
    'source-shaped, one table per\nsource table\n\nfull original payload kept\nas JSONB\n\n'
    'run_id, contract_version,\nsource watermark on every row\n\nnever overwritten, never\ncorrected\n\n'
    'retained for the contract term')
blk(39, 32, 56, 34, 'staging', 'Cleansing and mapping', 'sto',
    'one model per source,\nno cross-source joins\n\ntype normalisation,\nde-duplication\n\n'
    'soft-delete and late-arrival\nhandling\n\nsemantic mapping applied here\n\n'
    'fully rebuildable from raw')
blk(77, 34, 56, 34, 'core', 'Single source of truth', 'sto',
    'conformed facts and dimensions\n\nslowly changing dimensions,\nType 2  ( as it was then )\n\n'
    'globally unique keys,\ndim_site on every fact\n\nday-close version and\ncorrection flags\n\n'
    'retained 8 years, partitioned by day')
arrow(ax, 33.3, 73, 38.7, 73, 'sto', 2.0)
arrow(ax, 71.3, 73, 76.7, 73, 'sto', 2.0)

box(ax, 119, 64, 56, 26, '', None, 'pro')
note(ax, 147, 86.0, 'mart', fs=9.6, c='pro', bold=True)
note(ax, 147, 82.6, 'Subject marts  ( BI )', fs=6.4, c='ink', bold=True)
for i, s in enumerate(['Call Handling', 'Incident Response', 'Resources & Duty',
                       'Performance & Quality', 'System Operations']):
    tag(ax, 121.5, 78.6 - i*3.1, 51, 2.6, s, 'pro', fs=5.8)

box(ax, 119, 40, 56, 22, '', None, 'svc')
note(ax, 147, 58.0, 'ai', fs=9.6, c='svc', bold=True)
note(ax, 147, 54.6, 'AI data layer', fs=6.4, c='ink', bold=True)
for i, s in enumerate(['incident_wide   .   call_wide', 'agent_daily_wide',
                       'media index  ( object store link )', 'export snapshots  ( Parquet )']):
    tag(ax, 121.5, 50.6 - i*3.3, 51, 2.7, s, 'svc', fs=5.8)

arrow(ax, 111.3, 80, 118.7, 80, 'pro', 2.0)
arrow(ax, 111.3, 60, 118.7, 52, 'svc', 2.0)

box(ax, 3, 28, 84, 12, 'meta', 'contract registry  .  mapping registry  .  metric registry  .  lineage\n'
    'watermarks and run state  .  value profiles  .  clarification queue', 'ops',
    tfs=8.6, sfs=6.0, fc=BG['ops'])
box(ax, 91, 28, 84, 12, 'audit', 'who queried or exported what, and when  .  append-only\n'
    'retention aligned with the business data  ( 8 years, to be confirmed )', 'ops',
    tfs=8.6, sfs=6.0, fc=BG['ops'])

box(ax, 3, 4, 172, 20, '', None, 'sto', fc='#f8fafc')
note(ax, 89, 20.6, 'Two rules that hold the model together', fs=8.2, c='sto', bold=True)
note(ax, 6, 16.4,
     '1.   core is the only source of truth.  mart and ai are both built from core.  Neither may run its own '
     'pipeline back to raw — that is how one organisation ends up with two different answers to '
     '"how many incidents last quarter".', fs=6.8, ha='left', va='top')
note(ax, 6, 10.4,
     '2.   raw is never overwritten and never corrected.  Every fix, every reinterpretation of a field, every '
     'change of business rule is made by rebuilding the layers above it.  This is what makes '
     '"understand it later" a workable strategy.', fs=6.8, ha='left', va='top')
save(fig, 'f4_layering')
