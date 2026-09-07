# -*- coding: utf-8 -*-
"""Figure 6-1  Data Freshness Tiers and the Day-close Rule  数据新鲜度分级与日切规则"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 116.0
fig, ax = newfig(W, H)
title(ax, 3, 113, 'Figure 6-1   Data Freshness Tiers and the Day-close Rule',
      'Three paths with different latencies and different authority; the wall display and the daily report are not '
      'meant to agree to the digit')

note(ax, 3, 104.5, 'A.  Three paths', fs=9.0, c='ink', bold=True, ha='left')
LANES = [
    (88, 'REALTIME\n2 - 10 s', 'ing',
     ['ICP API\nDS presentation DB', 'Realtime State Service\nin memory, not persisted',
      'WebSocket push\nper-role filtering', 'Wall display, live tiles\nindicative, not for statistics']),
    (74, 'NEAR-REALTIME\n~ 1 min', 'pro',
     ['raw  >  staging\nwatermark increment', 'Day-bucket recompute\ntoday\'s partition only',
      'mart  ( provisional )\nflagged as current day', 'Dashboards\ncurrent-day figures, provisional']),
    (60, 'BATCH\nhourly / daily', 'sto',
     ['Full model build\nAirflow + dbt', 'core\nday-close, version stamped',
      'mart  ( authoritative )\nreconciled', 'Reports, exports, AI\nthe number of record'])]
for y, lab, c, cells in LANES:
    tag(ax, 3, y, 22, 12, lab, c, fs=7.0)
    xs = [29, 66, 103, 140]
    for i, (x, t) in enumerate(zip(xs, cells)):
        head, sub = t.split('\n')
        box(ax, x, y, 33, 12, head, sub, c, tfs=7.6, sfs=5.9)
        if i < 3:
            arrow(ax, x+33.3, y+6, x+36.7, y+6, c, 1.7)

ax.plot([3, 175], [54, 54], color=P['soft'], lw=1.0, ls=(0, (4, 3)))
note(ax, 3, 51.0, 'B.  One day on the timeline', fs=9.0, c='ink', bold=True, ha='left')

def t2x(h):
    """把小时映射到 x 坐标：0h -> 12, 30h -> 170"""
    return 12 + h*(158.0/30.0)

ax.plot([12, 170], [26, 26], color=P['ink'], lw=1.6)
for h, lab in [(0, '00:00\nday D'), (6, '06:00'), (12, '12:00'), (18, '18:00'),
               (24, '00:00\nday D+1'), (26, '02:00'), (30, '06:00')]:
    ax.plot([t2x(h), t2x(h)], [25, 27], color=P['ink'], lw=1.4)
    note(ax, t2x(h), 22.6, lab, fs=6.0, c='mid')

ax.plot([t2x(0), t2x(30)], [44, 44], color=P['ing'], lw=3.0, solid_capstyle='butt')
note(ax, 8, 44, 'realtime', fs=6.4, c='ing', bold=True, ha='right')
note(ax, t2x(15), 46.6, 'continuous, no day boundary', fs=6.0, c='ing')
for k in range(0, 61):
    x = t2x(k*0.5)
    ax.plot([x, x], [37.4, 38.6], color=P['pro'], lw=1.6)
note(ax, 8, 38, 'near-realtime', fs=6.4, c='pro', bold=True, ha='right')
note(ax, t2x(15), 40.6, 'recompute of the current day, about every minute', fs=6.0, c='pro')
for h in range(0, 31, 2):
    ax.plot([t2x(h), t2x(h)], [31, 33], color=P['sto'], lw=2.2)
note(ax, 8, 32, 'batch', fs=6.4, c='sto', bold=True, ha='right')
note(ax, t2x(15), 34.6, 'hourly build', fs=6.0, c='sto')

ax.plot([t2x(24), t2x(24)], [28, 47], color=P['mid'], lw=1.4, ls=(0, (4, 3)))
note(ax, t2x(24)-1.5, 49.5, 'day boundary  00:00 AST', fs=6.4, c='mid', bold=True, ha='right')
ax.plot([t2x(26), t2x(26)], [28, 47], color=P['bad'], lw=2.0)
note(ax, t2x(26)+1.5, 49.5, 'DAY-CLOSE', fs=7.2, c='bad', bold=True, ha='left')

box(ax, 12, 5, 52, 12, 'Before close',
    'current-day figures are labelled\nprovisional in every dashboard\nand API response',
    'pro', tfs=7.6, sfs=5.9, fc=BG['pro'])
box(ax, 66, 5, 52, 12, 'At close',
    'day D is stamped version 1 and\nbecomes authoritative; reports of\nday D are stable from here on',
    'bad', tfs=7.6, sfs=5.9, fc=BG['bad'])
box(ax, 120, 5, 52, 12, 'After close',
    'late changes go to the correction\ntable as version 2; the version-1\nsnapshot is never modified',
    'ok', tfs=7.6, sfs=5.9, fc=BG['ok'])
arrow(ax, 38, 17.3, 38, 20.5, 'pro', 1.5)
arrow(ax, 92, 17.3, 92, 20.5, 'bad', 1.5)
arrow(ax, 146, 17.3, 146, 20.5, 'ok', 1.5)
note(ax, 89, 2.0,
     'A report reprinted next year matches the one printed today.  That is the entire purpose of the day-close rule.',
     fs=6.8, c='ink', bold=True)
save(fig, 'f5_freshness')
