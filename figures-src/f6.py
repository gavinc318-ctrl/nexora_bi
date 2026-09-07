# -*- coding: utf-8 -*-
"""Figure 7-1  Query Path and Realtime Push Path  查询路径与实时推送路径"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 108.0
fig, ax = newfig(W, H)
title(ax, 3, 105, 'Figure 7-1   Query Path and Realtime Push Path',
      'The two ways data leaves the system; they use different components and carry different guarantees')

def step(x, y, n, c):
    ax.add_patch(FancyBboxPatch((x-1.6, y-1.6), 3.2, 3.2,
        boxstyle='circle,pad=0', fc='white', ec=P[c], lw=1.4, zorder=8))
    note(ax, x, y, str(n), fs=6.4, c=c, bold=True, z=9)

# ---------- A 查询路径（同步） ----------
note(ax, 3, 96.5, 'A.  Query path   —   synchronous, request / response', fs=9.0, c='ink', bold=True, ha='left')
QB = [('BI Portal', 'browser, custom shell'),
      ('Identity & Authz', 'validate JWT, resolve role,\nfield + department scope'),
      ('Query API  /  Record Search', 'metric query | record retrieval\nrate limit, cache, timeout'),
      ('Semantic & Metric', 'metric definition, grain,\nfilters  >  SQL   (metric path)'),
      ('pgBouncer / HAProxy', 'read-only routing'),
      ('Analytics Replica', 'never the primary')]
xs = [3 + i*29.2 for i in range(6)]
for x, (t, s) in zip(xs, QB):
    box(ax, x, 78, 25.2, 13, t, s, 'svc', tfs=7.8, sfs=5.9)
for i in range(5):
    x0 = xs[i]+25.5; x1 = xs[i+1]-0.3
    arrow(ax, x0, 84.5, x1, 84.5, 'svc', 1.7)
    step((x0+x1)/2, 88.0, i+1, 'svc')
arrow(ax, 172, 76.5, 6, 76.5, 'svc', 1.5, ls=(0, (5, 3)))
step(89, 73.4, 6, 'svc')
note(ax, 89, 70.4,
     'Every response carries four metadata fields:   metric version   .   data timestamp   .   '
     'day-close state ( provisional / closed / corrected )   .   depends_on_unverified',
     fs=6.6, c='svc', bold=True)
note(ax, 89, 66.8,
     'This is what makes our figure the one that can be defended in a meeting:  the number arrives with the '
     'rule that produced it and how settled it is.', fs=6.4)
note(ax, 89, 63.0,
     'Record search takes the same route, under tighter rules:  a configurable criteria set with index coverage,  a mandatory time range,  '
     'a hard result cap,  and per-role field visibility applied before any row leaves the service layer.',
     fs=6.4, c='mid')
ax.plot([3, 175], [60, 60], color=P['soft'], lw=1.0, ls=(0, (4, 3)))

# ---------- B 实时推送路径（异步） ----------
note(ax, 3, 56.5, 'B.  Realtime push path   —   asynchronous, server initiated', fs=9.0, c='ink', bold=True, ha='left')
RB = [('ICP API\nDS presentation DB', 'polled 2 - 10 s,\nseparate connection pool'),
      ('Realtime State Service', 'holds "now" in memory,\ncomputes the diff'),
      ('LISTEN / NOTIFY', 'keeps RT-1 and RT-2\nconsistent, no new broker'),
      ('WebSocket Gateway', 'per-role filtering,\nsequence numbers'),
      ('Wall display\nPortal live tiles', 'indicative figures,\nlabelled as such')]
xs2 = [3 + i*35.2 for i in range(5)]
for x, (t, s) in zip(xs2, RB):
    box(ax, x, 36, 31.2, 14, t, s, 'ing', tfs=7.8, sfs=5.9)
for i in range(4):
    x0 = xs2[i]+31.5; x1 = xs2[i+1]-0.3
    arrow(ax, x0, 43, x1, 43, 'ing', 1.7)
    step((x0+x1)/2, 46.5, i+1, 'ing')

box(ax, 3, 18, 84, 14, 'Reconnect and catch-up',
    'The client stores the last sequence number it received.  On reconnect it sends that number and the\n'
    'gateway replies with a full state snapshot, not a replay of missed events.  A wall display that was\n'
    'dark for two hours therefore comes back correct in one round trip, with no operator action.',
    'ing', tfs=8.0, sfs=6.0)
box(ax, 91, 18, 84, 14, 'Why this path never writes to the database',
    'Realtime state is an observation of another system, not a record of our own.  Persisting it would\n'
    'create a second, faster, less reliable version of the same facts — and every argument about which\n'
    'number is right would start there.  It lives in memory and dies with the process.',
    'bad', tfs=8.0, sfs=6.0)
note(ax, 89, 11.5,
     'The two paths are deliberately allowed to disagree.  The wall shows what is happening now, sampled every few seconds from another system.',
     fs=7.0, c='ink', bold=True)
note(ax, 89, 7.5,
     'The daily report shows what was settled at day-close.  The reconciliation rule between them is defined in Chapter 6, not left to the reader.',
     fs=7.0, c='ink', bold=True)
save(fig, 'f6_paths')
