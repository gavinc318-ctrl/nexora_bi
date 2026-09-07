# -*- coding: utf-8 -*-
"""Figure 12-1  Physical Placement and Storage Isolation  物理放置与存储隔离"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

W, H = 178.0, 112.0
fig, ax = newfig(W, H)
title(ax, 3, 109, 'Figure 12-1   Physical Placement and Storage Isolation',
      'Sizing alone does not deliver availability; where the machines sit and whose disks they use decides it')

for rx, rname in [(3, 'RACK  A'), (91, 'RACK  B')]:
    panel(ax, rx, 46, 84, 52, 'ops')
    note(ax, rx+42, 95.0, rname + '   —   independent power feed and top-of-rack switching',
         fs=7.6, c='ops', bold=True)

HOSTS = [(5, 'H1', ['PG-PRIMARY', 'ETCD-1', 'LB-1']),
         (33, 'H2', ['PG-REPLICA', 'ETCD-2', 'APP-1', 'RT-1']),
         (61, 'H3', ['OBJ-1', 'ETL-1', 'IAM-1', 'MON-1']),
         (93, 'H4', ['PG-STANDBY', 'ETCD-3', 'LB-2']),
         (121, 'H5', ['OBJ-2', 'APP-2', 'RT-2']),
         (149, 'H6', ['OBJ-3', 'ETL-2', 'IAM-2', 'MON-2', 'BKP-1'])]
for x, h, vms in HOSTS:
    box(ax, x, 49, 26, 42, '', None, 'sto')
    note(ax, x+13, 87.5, h, fs=9.0, c='sto', bold=True)
    note(ax, x+13, 84.2, '2 x 24 cores  .  256 GB', fs=5.6)
    for i, v in enumerate(vms):
        c = 'bad' if v.startswith('PG-') else ('pro' if v.startswith('ETCD') else 'ing')
        tag(ax, x+2, 78.5 - i*5.4, 22, 4.2, v, c, fs=6.4)

box(ax, 3, 30, 40, 12, 'Storage array 1', 'PG-PRIMARY data volume', 'bad', tfs=8.0, sfs=6.0, fc=BG['bad'])
box(ax, 47, 30, 40, 12, 'Storage array 2', 'PG-STANDBY data volume', 'bad', tfs=8.0, sfs=6.0, fc=BG['bad'])
box(ax, 91, 30, 40, 12, 'Capacity storage', 'object store  .  replica volume', 'sto', tfs=8.0, sfs=6.0)
box(ax, 135, 30, 40, 12, 'Backup storage', 'independent of all of the above', 'ok', tfs=8.0, sfs=6.0, fc=BG['ok'])
arrow(ax, 18, 48.8, 18, 42.3, 'bad', 1.6)
arrow(ax, 106, 48.8, 67, 42.3, 'bad', 1.6)
arrow(ax, 162, 48.8, 155, 42.3, 'ok', 1.6)

RULES = [('PL-01', 'The three PostgreSQL VMs never share a host.', 'ok'),
         ('ST-01', 'Primary and standby data volumes sit on different physical storage.  '
                   'Host anti-affinity alone is no protection when both virtual disks are carved from one LUN.', 'bad'),
         ('RS-01', 'No CPU or memory overcommitment on the database tier.  '
                   '99.999% assessed daily leaves 0.86 s; a noisy neighbour spends that in one spike.', 'bad'),
         ('OP-01', 'No automated live migration and no hypervisor snapshots of database VMs.', 'ok'),
         ('NW-02', 'Primary to standby round trip stays under 1 ms; synchronous commit waits on it.', 'ok')]
box(ax, 3, 3, 108, 24, '', None, 'sto', fc='#f8fafc')
note(ax, 6, 24.6, 'Five of the seventeen placement rules  —  the ones most often missed',
     fs=7.8, c='sto', bold=True, ha='left')
for i, (r, t, c) in enumerate(RULES):
    note(ax, 6, 20.6 - i*3.9, r, fs=6.6, c=c, bold=True, ha='left', va='top')
    note(ax, 15, 20.6 - i*3.9, t, fs=6.4, ha='left', va='top')

box(ax, 115, 3, 60, 24, '', None, 'bad', fc=BG['bad'])
note(ax, 145, 24.0, 'A limit worth stating plainly', fs=7.8, c='bad', bold=True)
note(ax, 118, 20.4,
     'With only two racks, one rack always holds two of\nthe three etcd members.  Losing that rack removes\n'
     'automatic failover, though the database keeps\nserving and can be promoted manually.\n\n'
     'Surviving a full rack loss automatically requires a\nthird rack or a witness elsewhere.  This is a fact\n'
     'about quorum, not something configuration fixes.', fs=6.2, ha='left', va='top')
save(fig, 'f9_placement')
