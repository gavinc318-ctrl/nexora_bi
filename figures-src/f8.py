# -*- coding: utf-8 -*-
"""Figure 11-1  High Availability Topology and Failure Behaviour  高可用拓扑与故障行为"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 112.0
fig, ax = newfig(W, H)
title(ax, 3, 109, 'Figure 11-1   High Availability Topology and Failure Behaviour',
      'Patroni is the single authority for database role changes; every other mechanism must defer to it')

# ---------- A 拓扑 ----------
note(ax, 3, 100.5, 'A.  Topology', fs=9.0, c='ink', bold=True, ha='left')
box(ax, 3, 76, 24, 16, 'Applications', 'query API, ETL,\nrealtime service', 'svc', tfs=8.0, sfs=6.0)
box(ax, 33, 84, 26, 10, 'LB-1  HAProxy', 'active', 'ing', tfs=7.6, sfs=5.9)
box(ax, 33, 72, 26, 10, 'LB-2  HAProxy', 'standby', 'ing', tfs=7.6, sfs=5.9)
arrow(ax, 27.3, 84, 32.7, 89, 'svc', 1.6)
arrow(ax, 27.3, 84, 32.7, 77, 'svc', 1.6)
note(ax, 46, 69.4, 'pgBouncer pooling, read / write split', fs=5.9)

box(ax, 68, 80, 30, 14, 'PG-PRIMARY', 'writes  .  system of record', 'sto', tfs=8.6, sfs=6.0)
box(ax, 110, 80, 30, 14, 'PG-STANDBY', 'synchronous  .  RPO = 0', 'sto', tfs=8.6, sfs=6.0)
box(ax, 110, 60, 30, 14, 'PG-REPLICA', 'asynchronous  .  all reporting', 'sto', tfs=8.6, sfs=6.0)
arrow(ax, 59.3, 87, 67.7, 87, 'sto', 1.8)
arrow(ax, 98.3, 87, 109.7, 87, 'sto', 2.2)
note(ax, 104, 89.6, 'synchronous', fs=6.0, c='sto', bold=True)
arrow(ax, 98.3, 83, 109.7, 70, 'sto', 1.8, ls=(0, (5, 3)))
note(ax, 101, 74.5, 'async', fs=6.0, c='sto')
arrow(ax, 59.3, 76, 109.7, 66, 'svc', 1.4, ls=(0, (3, 3)))
note(ax, 80, 63.6, 'read traffic routed to the replica', fs=5.9, c='svc')

for i in range(3):
    box(ax, 146, 88 - i*9.5, 28, 7.5, 'ETCD-%d' % (i+1), None, 'pro', tfs=7.4, fc=BG['pro'])
note(ax, 160, 62.5, 'quorum 2 of 3\nauthorises promotion', fs=6.0, c='pro', bold=True)
arrow(ax, 145.7, 91.8, 140.3, 91.8, 'pro', 1.3, ls=(0, (3, 2)), dbl=True)
note(ax, 122, 96.5, 'Patroni  —  sole authority for role changes', fs=7.0, c='pro', bold=True)

ax.plot([3, 175], [56, 56], color=P['soft'], lw=1.0, ls=(0, (4, 3)))
note(ax, 3, 52.5, 'B.  What happens when something fails', fs=9.0, c='ink', bold=True, ha='left')

FAIL = [(3, 'Primary host fails', 'Patroni promotes the standby.\nRPO = 0, RTO under 2 minutes.\n'
         'Applications reconnect through\nHAProxy; no manual step.', 'ok'),
        (61, 'Standby fails', 'The primary keeps serving.\nSynchronous commit degrades per\n'
         'the decision tree in 11.4 rather\nthan blocking writes.', 'svc'),
        (119, 'Replica fails', 'Reporting reads fail over to the\nstandby, read-only.\n'
         'Latency rises; nothing stops.', 'svc'),
        (3, 'One etcd member fails', 'Quorum holds at 2 of 3.\nAutomatic failover remains\navailable.  Replace at leisure.', 'ok'),
        (61, 'Two etcd members fail', 'The database keeps serving, but\nautomatic failover is gone.\n'
         'Promotion becomes a manual,\ndeliberate operator action.', 'bad'),
        (119, 'Network partition', 'Patroni decides, alone.  Hypervisor\nHA must never restart a database\n'
         'VM on its own judgement — two\nauthorities produce two primaries.', 'bad')]
for i, (x, t, s, c) in enumerate(FAIL):
    y = 30 if i < 3 else 8
    box(ax, x, y, 56, 18, '', None, c, fc=BG.get(c, 'white'))
    note(ax, x+2.5, y+14.4, t, fs=8.0, c='ink', bold=True, ha='left', va='center')
    note(ax, x+2.5, y+11.2, s, fs=6.2, ha='left', va='top')
save(fig, 'f8_ha')
