# -*- coding: utf-8 -*-
"""OSS911 BI/DW HLD — shared drawing helpers.

用法:  OUT=./out python3 f3.py      (不设 OUT 则输出到当前目录)
每个脚本输出同名的 .png 与 .svg。SVG 中文字保留为文本对象，
可直接导入 Figma / Illustrator / draw.io 编辑。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['svg.fonttype'] = 'none'
import os
OUT = os.environ.get('OUT', '.')

# 调色板：键 = 层/角色，值 = (描边色, 浅底色)
P = {'ing': '#0f766e', 'sto': '#1d4ed8', 'pro': '#6d28d9', 'svc': '#b45309',
     'pre': '#be123c', 'ops': '#334155', 'ext': '#6b7280', 'ok': '#15803d',
     'bad': '#b91c1c', 'ink': '#111827', 'mid': '#4b5563', 'soft': '#94a3b8'}
BG = {'ing': '#eef7f5', 'sto': '#eef2fd', 'pro': '#f3eefc', 'svc': '#fdf4e7',
      'pre': '#fdeef1', 'ops': '#f1f3f6', 'ext': '#f4f4f5', 'ok': '#eef7f0',
      'bad': '#fdeeee'}

def newfig(W, H):
    """画布单位 = 1/10 英寸。W=178,H=104 -> 17.8 x 10.4 英寸 @160dpi"""
    fig = plt.figure(figsize=(W/10.0, H/10.0), dpi=160)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')
    ax.add_patch(Rectangle((0, 0), W, H, fc='white', ec='none', zorder=0))
    return fig, ax

def title(ax, x, y, main, sub=None, fs=13):
    ax.text(x, y, main, ha='left', va='top', fontsize=fs, fontweight='bold', color=P['ink'])
    if sub:
        ax.text(x, y-4.2, sub, ha='left', va='top', fontsize=fs*0.74, color=P['mid'])

def panel(ax, x, y, w, h, c='ext', dashed=False, lw=1.0, fill=True, z=1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=1.4',
        fc=BG.get(c, 'white') if fill else 'none', ec=P[c], lw=lw,
        ls='--' if dashed else '-', zorder=z))

def box(ax, x, y, w, h, t, s=None, c='ing', dashed=False, tfs=8.4, sfs=6.0,
        fc='white', tc=None, z=4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=1.0',
        fc=fc, ec=P[c], lw=1.1 if dashed else 1.5, ls='--' if dashed else '-', zorder=z))
    if s:
        ax.text(x+w/2, y+h*0.66, t, ha='center', va='center', fontsize=tfs,
                fontweight='bold', color=tc or P['ink'], zorder=z+1, linespacing=1.25)
        ax.text(x+w/2, y+h*0.26, s, ha='center', va='center', fontsize=sfs,
                color=P['mid'], zorder=z+1, linespacing=1.4)
    else:
        ax.text(x+w/2, y+h/2, t, ha='center', va='center', fontsize=tfs,
                fontweight='bold', color=tc or P['ink'], zorder=z+1, linespacing=1.35)

def tag(ax, x, y, w, h, t, c='sto', fs=7.2, z=5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=0.7',
        fc=BG.get(c, 'white'), ec=P[c], lw=1.1, zorder=z))
    ax.text(x+w/2, y+h/2, t, ha='center', va='center', fontsize=fs,
            fontweight='bold', color=P[c], zorder=z+1, linespacing=1.3)

def arrow(ax, x0, y0, x1, y1, c='mid', lw=1.8, ls='-', dbl=False, z=6, ms=12):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle='<|-|>' if dbl else '-|>',
        mutation_scale=ms, lw=lw, color=P.get(c, c), ls=ls, shrinkA=0, shrinkB=0, zorder=z))

def note(ax, x, y, t, fs=6.4, c='mid', ha='center', va='center', bold=False, rot=0, z=8):
    ax.text(x, y, t, ha=ha, va=va, fontsize=fs, color=P.get(c, c), zorder=z,
            fontweight='bold' if bold else 'normal', linespacing=1.4, rotation=rot)

def save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, name + '.png'), dpi=160, facecolor='white')
    fig.savefig(os.path.join(OUT, name + '.svg'), facecolor='white')
    print('saved', name, '(png + svg)')
