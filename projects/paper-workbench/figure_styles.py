# -*- coding: utf-8 -*-
"""figure_styles.py — 工作台统一科研配色与绘图样式预设。

配色来源：开源科研配色方案（GitHub ggsci 的 NPG/AAAS/Lancet 期刊色板、
Okabe-Ito 色盲安全色板、Paul Tol bright 色板）。所有图默认优先 NPG。

用法：
    from figure_styles import NPG, OKABE_ITO, apply_mpl_style
    apply_mpl_style()          # matplotlib 全局预设（Arial/去顶右边框）
"""

# ggsci NPG（Nature Publishing Group）——默认
NPG = {"red": "#E64B35", "cyan": "#4DBBD5", "green": "#00A087", "navy": "#3C5488",
       "salmon": "#F39B7F", "greyblue": "#8491B4", "lightteal": "#91D1C2",
       "crimson": "#DC0000", "brown": "#7E6148", "khaki": "#B09C85"}
NPG_ORDER = ["#3C5488", "#E64B35", "#00A087", "#4DBBD5", "#F39B7F",
             "#8491B4", "#91D1C2", "#B09C85", "#7E6148", "#DC0000"]

# Okabe-Ito 色盲安全色板
OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442",
             "#0072B2", "#D55E00", "#CC79A7", "#000000"]

# Paul Tol bright（色盲友好）
TOL_BRIGHT = ["#4477AA", "#EE6677", "#228833", "#CCBB44",
              "#66CCEE", "#AA3377", "#BBBBBB"]

# ggsci AAAS（Science 系）
AAAS = ["#3B4992", "#EE0000", "#008B45", "#631879",
        "#008280", "#BB0021", "#5F559B", "#A20056"]

FONT = "Arial"


def apply_mpl_style():
    """matplotlib 出版级预设：Arial、去顶右边框、细边框。"""
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": FONT,
        "axes.edgecolor": "#444444",
        "axes.linewidth": 0.9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "text.color": "#222222",
        "axes.prop_cycle": __import__("cycler").cycler(color=NPG_ORDER),
    })
