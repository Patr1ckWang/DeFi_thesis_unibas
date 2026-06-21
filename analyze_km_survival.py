"""
Kaplan-Meier Survival Analysis — Cross-Platform Visual Evidence

Output:
  results/figures/Fig3_KM_CrossPlatform.{pdf,png}
  results/figures/Fig_KM_DualTrack_Decomposition.{pdf,png}
  results/tables/Table_KM_Descriptives.csv
"""

import os, sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test
import warnings
warnings.filterwarnings('ignore')

plt.rcParams.update({
    'figure.dpi': 150, 'savefig.dpi': 300,
    'font.size': 10, 'axes.labelsize': 11,
})

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
FIG_DIR = os.path.join(ROOT, 'results', 'figures')
TBL_DIR = os.path.join(ROOT, 'results', 'tables')
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(TBL_DIR, exist_ok=True)

PANEL_PATH = os.path.join(ROOT, 'cleaned_data', 'survival_panel.csv')
if not os.path.exists(PANEL_PATH):
    print(f"ERROR: survival_panel.csv not found at {PANEL_PATH}")
    sys.exit(1)

panel = pd.read_csv(PANEL_PATH)

REQUIRED_COLS = ['user_address', 'pool_name', 'track', 'faction', 'cohort',
                 'duration_days', 'event', 'has_uni_mining']
for col in REQUIRED_COLS:
    if col not in panel.columns:
        print(f"ERROR: required column '{col}' missing from survival_panel.csv")
        sys.exit(1)

print(f"  Raw panel: {len(panel):,} obs")

# Exclude Returner cohort per empirical frame
n_ret = (panel['cohort'] == 'Returner').sum()
panel = panel[panel['cohort'] != 'Returner'].copy()
print(f"  Excluded Returner cohort: {n_ret:,} obs dropped")
print(f"  Working panel: {len(panel):,} obs")

def build_cs(df, track_filter, faction_filter):
    """Build a cross-section (one row per trajectory) from a filtered panel."""
    sub = df[(df['track'] == track_filter) & (df['faction'] == faction_filter)]
    cs = (sub.sort_values('stop_time')
          .groupby(['user_address', 'pool_name'])
          .last()
          .reset_index())
    return cs

# Core groups
cs_stayer_lp  = build_cs(panel, 'uniswap',  'Stayer')
cs_mig_all_slp = build_cs(panel, 'sushiswap', 'Migrator')

# Identify dual-track Migrators (have records on BOTH tracks)
mig_lp_addrs = set(panel[(panel['faction'] == 'Migrator') &
                         (panel['track'] == 'uniswap')]
                   .apply(lambda r: f"{r['user_address']}_{r['pool_name']}", axis=1))
cs_mig_all_slp['is_dual'] = cs_mig_all_slp.apply(
    lambda r: f"{r['user_address']}_{r['pool_name']}" in mig_lp_addrs, axis=1)

cs_mig_single_slp = cs_mig_all_slp[~cs_mig_all_slp['is_dual']]
cs_mig_dual_slp   = cs_mig_all_slp[cs_mig_all_slp['is_dual']]
cs_mig_dual_lp    = build_cs(panel[panel.apply(
    lambda r: f"{r['user_address']}_{r['pool_name']}" in mig_lp_addrs, axis=1)],
    'uniswap', 'Migrator')

# Verify N consistency with Table 1
assert len(cs_mig_all_slp) == 2604, f"Migrator N mismatch: {len(cs_mig_all_slp)} != 2604"
assert len(cs_stayer_lp) == 4059, f"Stayer N mismatch: {len(cs_stayer_lp)} != 4059"
assert len(cs_mig_single_slp) + len(cs_mig_dual_slp) == 2604

print(f"\n  Cross-Platform Panel:")
print(f"    Migrator on SLP: {len(cs_mig_all_slp):,} trajectories")
print(f"      Single-track:  {len(cs_mig_single_slp):,}")
print(f"      Dual-track:    {len(cs_mig_dual_slp):,} (also appear on LP)")
print(f"    Stayer   on LP:  {len(cs_stayer_lp):,} trajectories")

def compute_stats(cs, label):
    """Return dict of survival statistics for a trajectory cross-section."""
    T = cs['duration_days']
    E = cs['event'].astype(bool)
    # Use proper KM median survival time (handles censoring correctly)
    kmf = KaplanMeierFitter()
    kmf.fit(T, event_observed=E)
    km_median = kmf.median_survival_time_
    median_val = round(km_median, 1) if np.isfinite(km_median) else np.nan
    return {
        'Group': label,
        'N': len(cs),
        'Median_survival_days': median_val,
        'Mean_survival_days': round(T.mean(), 1),
        'Event_rate': round(E.mean(), 4),
        'Censored_pct': round((1 - E.mean()) * 100, 1),
    }

groups = [
    (cs_stayer_lp,      'Stayer (LP)'),
    (cs_mig_all_slp,    'Migrator all (SLP)'),
    (cs_mig_single_slp, 'Migrator single-track (SLP)'),
    (cs_mig_dual_slp,   'Migrator dual-track (SLP)'),
    (cs_mig_dual_lp,    'Migrator dual-track (LP)'),
]

rows = []
lr_results = []  # (comparison_label, p_value)
for cs, label in groups:
    row = compute_stats(cs, label)
    rows.append(row)
    print(f"  {label}: N={row['N']}, median={row['Median_survival_days']}d, "
          f"event_rate={row['Event_rate']:.3f}, censored={row['Censored_pct']}%")

# Log-rank tests: each Migrator group vs Stayer
stats_df = pd.DataFrame(rows)
lr_col = []
for i, (cs, label) in enumerate(groups):
    if label == 'Stayer (LP)':
        lr_col.append('—')
    else:
        lr = logrank_test(
            cs['duration_days'], cs_stayer_lp['duration_days'],
            event_observed_A=cs['event'].astype(bool),
            event_observed_B=cs_stayer_lp['event'].astype(bool))
        lr_col.append('<0.001' if lr.p_value < 0.001 else f'{lr.p_value:.4f}')
stats_df['LogRank_vs_Stayer'] = lr_col

# Within-Migrator comparison: single vs dual on SLP
lr_mig = logrank_test(
    cs_mig_single_slp['duration_days'], cs_mig_dual_slp['duration_days'],
    event_observed_A=cs_mig_single_slp['event'].astype(bool),
    event_observed_B=cs_mig_dual_slp['event'].astype(bool))
print(f"  Log-rank Single vs Dual (both on SLP): p = {lr_mig.p_value:.2e}")

# Within-subject: dual-track Migrators SLP vs LP
lr_within = logrank_test(
    cs_mig_dual_slp['duration_days'], cs_mig_dual_lp['duration_days'],
    event_observed_A=cs_mig_dual_slp['event'].astype(bool),
    event_observed_B=cs_mig_dual_lp['event'].astype(bool))
print(f"  Log-rank Dual SLP vs Dual LP (within-subject): p = {lr_within.p_value:.2e}")

# csv
stats_df.to_csv(os.path.join(TBL_DIR, 'Table_KM_Descriptives.csv'), index=False)

COLORS = {
    'stayer':    '#009E73',  # green
    'mig_all':   '#D55E00',  # orange-red
    'mig_single':'#E69F00',  # gold
    'mig_dual_s':'#CC79A7',  # purple
    'mig_dual_l':'#56B4E9',  # sky blue
}

def draw_at_risk_table(ax, fitters, labels, times=None, y_offset=-0.45):
    """Manually draw at-risk counts below the plot as a table."""
    if times is None:
        times = [0, 7, 30, 60, 120, 180, 237]
    cell_text = []
    for kmf, lbl in zip(fitters, labels):
        at_risk = []
        for t in times:
            n = (kmf.event_table['at_risk'].loc[kmf.event_table.index <= t].iloc[-1]
                 if t <= kmf.event_table.index.max() else 0)
            at_risk.append(str(int(n)))
        cell_text.append(at_risk)

    tbl = ax.table(cellText=cell_text,
                   rowLabels=[f'  {l}' for l in labels],
                   colLabels=[str(t) for t in times],
                   cellLoc='center', rowLoc='center',
                   loc='bottom', bbox=[0.0, y_offset, 1.0, 0.35])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    for key, cell in tbl.get_celld().items():
        cell.set_linewidth(0.3)


def plot_km_main(cs_mig, cs_sty):
    """Fig 3: Cross-platform KM — Migrator (SLP) vs Stayer (LP)."""
    fig, ax = plt.subplots(figsize=(10, 7))

    fitters = []
    labels_short = []

    # Stayer first (so it draws behind)
    kmf_s = KaplanMeierFitter()
    kmf_s.fit(cs_sty['duration_days'], event_observed=cs_sty['event'].astype(bool),
              label=f"Stayer on Uniswap (N={len(cs_sty):,})")
    kmf_s.plot_survival_function(ax=ax, color=COLORS['stayer'], ci_show=True, linewidth=2)
    fitters.append(kmf_s)
    labels_short.append('Stayer LP')

    kmf_m = KaplanMeierFitter()
    kmf_m.fit(cs_mig['duration_days'], event_observed=cs_mig['event'].astype(bool),
              label=f"Migrator on SushiSwap (N={len(cs_mig):,})")
    kmf_m.plot_survival_function(ax=ax, color=COLORS['mig_all'], ci_show=True, linewidth=2)
    fitters.append(kmf_m)
    labels_short.append('Migrator SLP')

    ax.set_xlabel('Days since 9 Sep 2020')
    ax.set_ylabel('Survival Probability (liquidity remaining)')
    ax.set_ylim(0, 1.04)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.legend(loc='upper right', frameon=True, fontsize=8, framealpha=0.9)

    # Log-rank
    lr = logrank_test(
        cs_mig['duration_days'], cs_sty['duration_days'],
        event_observed_A=cs_mig['event'].astype(bool),
        event_observed_B=cs_sty['event'].astype(bool))
    p_str = '< 0.001' if lr.p_value < 0.001 else f'= {lr.p_value:.3f}'
    ax.text(0.97, 0.82, f'Log-rank $p$ {p_str}', transform=ax.transAxes,
            ha='right', fontsize=10,
            bbox=dict(facecolor='white', alpha=0.85, edgecolor='gray', boxstyle='round'))

    draw_at_risk_table(ax, fitters, labels_short, y_offset=-0.50)
    fig.subplots_adjust(bottom=0.33)

    for fmt in ['png', 'pdf']:
        plt.savefig(os.path.join(FIG_DIR, f'Fig3_KM_CrossPlatform.{fmt}'),
                    dpi=300, bbox_inches='tight')
    plt.close()


def plot_km_decomposition(cs_sty, cs_single, cs_dual_s, cs_dual_l):
    """Supplementary: 4-line decomposition by track status."""
    fig, ax = plt.subplots(figsize=(12, 8))

    curves = [
        (cs_sty,     COLORS['stayer'],    f'Stayer on Uniswap (N={len(cs_sty):,})', 'Stayer LP'),
        (cs_single,  COLORS['mig_single'], f'Migrator single-track on SLP (N={len(cs_single):,})', 'Mig single'),
        (cs_dual_s,  COLORS['mig_dual_s'], f'Migrator dual-track on SLP (N={len(cs_dual_s):,})', 'Mig dual SLP'),
        (cs_dual_l,  COLORS['mig_dual_l'], f'Migrator dual-track on LP (N={len(cs_dual_l):,})', 'Mig dual LP'),
    ]

    fitters, labels_short = [], []
    for cs, color, label, short in curves:
        kmf = KaplanMeierFitter()
        kmf.fit(cs['duration_days'], event_observed=cs['event'].astype(bool),
                label=label)
        ls = '--' if 'LP' in short and 'dual' in short else '-'
        kw = {'linestyle': ls} if ls == '--' else {}
        kmf.plot_survival_function(ax=ax, color=color, ci_show=False,
                                   linewidth=1.8, **kw)
        fitters.append(kmf)
        labels_short.append(short)

    ax.set_xlabel('Days since 9 Sep 2020')
    ax.set_ylabel('Survival Probability')
    ax.set_ylim(0, 1.04)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.legend(loc='upper right', frameon=True, fontsize=7, framealpha=0.9)

    # At-risk table
    draw_at_risk_table(ax, fitters, labels_short, y_offset=-0.45)

    fig.subplots_adjust(bottom=0.34)

    for fmt in ['png', 'pdf']:
        plt.savefig(os.path.join(FIG_DIR, f'Fig_KM_DualTrack_Decomposition.{fmt}'),
                    dpi=300, bbox_inches='tight')
    plt.close()


plot_km_main(cs_mig_all_slp, cs_stayer_lp)
plot_km_decomposition(cs_stayer_lp, cs_mig_single_slp, cs_mig_dual_slp, cs_mig_dual_lp)


