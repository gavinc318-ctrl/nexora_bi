# -*- coding: utf-8 -*-
"""Figure 9-1  Management Console Function Map  管理界面功能地图"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 108.0
fig, ax = newfig(W, H)
title(ax, 3, 105, 'Figure 9-1   Management Console Function Map',
      'Eleven management screens in four domains; every screen is part of the delivered system, not a database tool')

COLS = [(3, 'DATA', 'ing'), (46, 'ANALYTICS', 'pro'), (89, 'SECURITY', 'pre'), (132, 'PLATFORM', 'ops')]
for x, name, c in COLS:
    tag(ax, x, 92, 41, 6, name, c, fs=8.2)

CARDS = {
 3: [(68, 'M-01   Source Ingestion',
      'watermark, last success, lag,\nquarantined rows, contract version\n\n'
      'hot-adjustable: enable, interval,\nbatch size, rate limit  ( audited )', 'ing'),
     (44, 'M-02   Job Scheduling',
      'run history, dependency view\nfailure re-run, backfill\nSLA breach alerts', 'ing'),
     (20, 'M-03   Data Quality',
      'rule results, quarantine review\nrelease or discard a held batch\n'
      'clarification queue for unknown\ncode values, ranked by impact', 'ing')],
 46: [(68, 'M-04   Metric Dictionary',
       'definition, rule, grain, lineage\nchange history and version\n\n'
       'read-only, aimed at the business\nreader rather than the engineer', 'pro'),
      (44, 'M-05   Reports & Dashboards',
       'publish and withdraw, parameters\nvisibility by role\nsubscription and delivery', 'pro'),
      (20, 'M-10   Reference Data',
       'code tables, shifts, duty level,\nsectors, units\nbilingual dimension names where\nthe source provides only one', 'pro')],
 89: [(68, 'M-06   Users & Permissions',
       'four roles: call taker, dispatcher,\nsupervisor, administrator\n\n'
       'field-level visibility matrix\ndepartment row filter for\ndispatchers and supervisors ( on )', 'pre'),
      (44, 'M-07   Audit Query',
       'who queried or exported what,\nand when\nappend-only, retained with the\nbusiness data', 'pre'),
      (20, 'M-11   Classification & Discovery',
       'periodic scan of the databases and\nobject storage for sensitive content\nscan interval configurable\n'
       'labels drive the masking rules\nand the field visibility defaults', 'pre')],
 132: [(68, 'M-08   Runtime Monitoring',
        'availability, replication lag,\nquery latency distribution,\nresource headroom\n\n'
        'this is the SLA evidence store', 'ops'),
       (44, 'M-09   Backup & Restore',
        'backup status, recoverable point\nin time, restore drill record', 'ops'),
       (20, 'Design note',
        'Structural configuration ships\nthrough Git and a release.\nRuntime parameters can be changed\n'
        'live, with an audit entry.  The\nsplit exists because 03:00 is not\nthe time for a release.', 'ops')],
}
for x, items in CARDS.items():
    for y, t, s, c in items:
        h = 22
        box(ax, x, y, 41, h, '', None, c)
        note(ax, x+2.5, y+h-4.0, t, fs=7.8, c='ink', bold=True, ha='left', va='center')
        note(ax, x+2.5, y+h-7.5, s, fs=6.0, ha='left', va='top')

box(ax, 3, 3, 170, 12, '', None, 'sto', fc='#f8fafc')
note(ax, 88, 11.4, 'What these screens are for', fs=8.2, c='sto', bold=True)
note(ax, 88, 7.2,
     'An operator must be able to answer three questions without opening a database client:  is the data current, '
     'is it trustworthy, and who has seen it.\nM-01 and M-02 answer the first, M-03 and M-04 the second, '
     'M-06 and M-07 the third.  The rest keep the platform alive and provable.', fs=6.8)
save(fig, 'f7_admin')
