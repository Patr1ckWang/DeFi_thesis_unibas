"""
DiD Event Study — UNI Counter-Incentive Effect (v2, 2026-06-20)

Model specifications:
  M1: Basic DiD OLS (no FE) — reference
  M2: DiD + pool FE + day FE
  M3: DiD + user FE + day FE — MAIN specification
  M4: Cross-platform DiD (stablecoin pools: Uni vs Sushi) — SEPARATE table
  M5: Placebo test (fake event = Day 3, pre-period only)
  ES: Event study: i(rel_time, is_full, ref=6) + user FE + day FE
"""

import pandas as pd
import numpy as np
import pyfixest as pf
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import os, sys, warnings
from datetime import datetime
from scipy import stats as scipy_stats

plt.rcParams.update({
    'figure.dpi': 150, 'savefig.dpi': 300,
    'font.size': 10,
})

warnings.filterwarnings('ignore')

plt.rcParams['figure.dpi'] = 300
plt.rcParams['font.size'] = 10


DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DIR)
FIG_DIR = os.path.join(ROOT_DIR, 'results', 'figures')
TAB_DIR = os.path.join(ROOT_DIR, 'results', 'tables')
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(TAB_DIR, exist_ok=True)

UNI_DAY = 7  # Day 7 = Sep 16, 2020 = UNI announcement (per timeline.md)
REF_DAY = 6  # Reference day for event study (day before UNI)
FAKE_DAY = 3  # Placebo event day
STABLE_POOLS = ['USDC-WETH', 'USDT-WETH', 'DAI-WETH']

# 1. Load & Validate Data

panel = pd.read_csv(os.path.join(ROOT_DIR, 'cleaned_data', 'survival_panel.csv'))

# Pool-level DiD: Uniswap track only, core sample
uni = panel[panel['track'] == 'uniswap'].copy()

# Filter to full_coverage / half_coverage + core factions (no Returner, no Fence-sitter)
did = uni[uni['analysis_tier'].isin(['full_coverage', 'half_coverage']) &
          uni['faction'].isin(['Migrator', 'Stayer']) &
          (uni['cohort'] != 'Returner')].copy()

# Define treatment variables
did['is_full'] = (did['analysis_tier'] == 'full_coverage').astype(int)
did['post'] = (did['start_time'] >= UNI_DAY).astype(int)      # v2: Day 7+ = post
did['rel_time'] = np.floor(did['start_time']).astype(int)

# Drop rows with missing clustering/outcome
did = did.dropna(subset=['user_address', 'pool_name', 'rel_time', 'is_full', 'event'])


treated_pools = sorted(did[did['is_full'] == 1]['pool_name'].unique())
control_pools = sorted(did[did['is_full'] == 0]['pool_name'].unique())
n_users = did['user_address'].nunique()
n_clusters = did['pool_name'].nunique()
n_obs = len(did)
n_treated_obs = (did['is_full'] == 1).sum()
n_control_obs = (did['is_full'] == 0).sum()

print(f"  Sample: {n_obs:,} daily obs on {n_users:,} users across {n_clusters} pools")
print(f"  Treated pools (full_coverage, N={n_treated_obs:,}): {treated_pools}")
print(f"  Control pools (half_coverage, N={n_control_obs:,}): {control_pools}")
print(f"  Post cutoff: Day >= {UNI_DAY} (Sep 16, UNI announcement)")
print(f"  Event study reference: Day {REF_DAY} (day before UNI)")

# Pre-trends quick check
for d in range(0, UNI_DAY + 1):
    t = did[(did['is_full'] == 1) & (did['rel_time'] == d)]['event'].mean()
    c = did[(did['is_full'] == 0) & (did['rel_time'] == d)]['event'].mean()
    print(f"     Day {d}: Treated={t:.4f}, Control={c:.4f}, Diff={t-c:.4f}")

# 2. DiD Models (M1–M3)

# M1: Basic DiD without FE
m1 = pf.feols("event ~ is_full:post + is_full + post", data=did,
              vcov={'CRV1': 'user_address'})

# M2: DiD with pool + day FE
m2 = pf.feols("event ~ is_full:post | pool_name + rel_time", data=did,
              vcov={'CRV1': 'user_address'})

# M3: DiD with user + day FE (MAIN)
m3 = pf.feols("event ~ is_full:post | user_address + rel_time", data=did,
              vcov={'CRV1': 'user_address'})


# Extract main DiD coefficient for interpretation
# pyfixest tidy() puts coefficient names in the index
m3_tidy = m3.tidy()
did_coef_row = m3_tidy.loc['is_full:post'] if 'is_full:post' in m3_tidy.index else None
if did_coef_row is not None:
    did_coef = did_coef_row['Estimate']
    did_se = did_coef_row['Std. Error']
    did_p = did_coef_row['Pr(>|t|)']
    sign_word = "MORE death" if did_coef > 0 else "LESS death"
    ret_word = "LESS retention" if did_coef > 0 else "MORE retention"
    print(f"\n  M3 DiD: is_full:post = {did_coef:.5f} (SE={did_se:.5f}, p={did_p:.4f})")

# 3. Randomization Inference (main DiD, M3 specification)

pool_list = sorted(did['pool_name'].unique())
n_treated_actual = len(treated_pools)
obs_coef = did_coef

# Enumerate all possible treatment assignments
from itertools import combinations
all_assignments = list(combinations(pool_list, n_treated_actual))
print(f"  Total permutations: {len(all_assignments)}")

ri_coefs = []
for treated_set in all_assignments:
    did_perm = did.copy()
    did_perm['is_full_perm'] = did_perm['pool_name'].isin(treated_set).astype(int)
    try:
        m_perm = pf.feols("event ~ is_full_perm:post | user_address + rel_time",
                          data=did_perm, vcov={'CRV1': 'user_address'})
        perm_tidy = m_perm.tidy()
        if 'is_full_perm:post' in perm_tidy.index:
            ri_coefs.append(perm_tidy.loc['is_full_perm:post', 'Estimate'])
    except Exception:
        continue

ri_coefs = np.array(ri_coefs)
ri_p = np.mean(np.abs(ri_coefs) >= np.abs(obs_coef))
print(f"  Observed coefficient: {obs_coef:.5f}")
print(f"  RI coefficients: mean={ri_coefs.mean():.5f}, std={ri_coefs.std():.5f}")
print(f"  RI p-value (two-sided): {ri_p:.4f}")


# 4. Event Study

es_model = pf.feols(f"event ~ i(rel_time, is_full, ref={REF_DAY}) | user_address + rel_time",
                    data=did, vcov={'CRV1': 'user_address'})

# Extract event study coefficients (pyfixest tidy() puts coef names in index)
es_tidy = es_model.tidy()
es_tidy['term_name'] = es_tidy.index

# Parse day from term names like "rel_time::0:is_full"
es_terms = es_tidy[es_tidy['term_name'].str.contains('rel_time', na=False)].copy()
es_terms['day'] = es_terms['term_name'].str.extract(r'rel_time::(-?\d+):is_full').astype(float)
es_terms = es_terms.dropna(subset=['day']).sort_values('day')

print(f"  Event study coefficients extracted: {len(es_terms)} daily estimates")

# Pre-trends F-test: joint test that all pre-treatment (day < UNI_DAY, day != REF_DAY) coefs = 0
pre_coefs = es_terms[(es_terms['day'] < UNI_DAY) & (es_terms['day'] != REF_DAY)]
if len(pre_coefs) > 0:
    # Wald test: (Rβ - r)' * inv(R * V * R') * (Rβ - r) ~ χ²(k)
    # For testing all pre-trend coefs = 0, R = I, r = 0
    pre_est = pre_coefs['Estimate'].values
    pre_se = pre_coefs['Std. Error'].values
    # Approximate: compute chi2 stat assuming independence (conservative)
    pre_z = pre_est / pre_se
    pre_chi2 = np.sum(pre_z ** 2)
    pre_df = len(pre_coefs)
    pre_p = 1 - scipy_stats.chi2.cdf(pre_chi2, pre_df)
    print(f"\n  Pre-trends F-test (H0: all pre-treatment coefs = 0):")
    print(f"    χ²({pre_df}) = {pre_chi2:.2f}, p = {pre_p:.4f}")
    if pre_p < 0.05:
        print(f" REJECTED at 5% — evidence of differential pre-trends")
    else:
        print(f" NOT rejected — no evidence of differential pre-trends")
    # individual pre-trend coefs
    print(f"\n  Individual pre-treatment coefficients (ref=Day {REF_DAY}):")
    for _, r in pre_coefs.iterrows():
        stars = '***' if r['Pr(>|t|)'] < 0.01 else ('**' if r['Pr(>|t|)'] < 0.05 else ('*' if r['Pr(>|t|)'] < 0.10 else ''))
        print(f"    Day {int(r['day']):>3d}: {r['Estimate']:>8.5f} (SE={r['Std. Error']:.5f}){stars}")

# 5. M4: Cross-Platform DiD (Stable Coins Only) — Separate

stable = panel[panel['pool_name'].isin(STABLE_POOLS) &
               panel['faction'].isin(['Migrator', 'Stayer']) &
               (panel['cohort'] != 'Returner')].copy()
stable['is_uni'] = (stable['track'] == 'uniswap').astype(int)
stable['post'] = (stable['start_time'] >= UNI_DAY).astype(int)
stable['rel_time'] = np.floor(stable['start_time']).astype(int)
stable = stable.dropna(subset=['user_address', 'pool_name', 'rel_time', 'is_uni', 'event'])

m4 = pf.feols("event ~ is_uni:post | user_address + rel_time", data=stable,
              vcov={'CRV1': 'user_address'})

m4_tidy = m4.tidy()
if 'is_uni:post' in m4_tidy.index:
    m4_coef = m4_tidy.loc['is_uni:post', 'Estimate']
    m4_se = m4_tidy.loc['is_uni:post', 'Std. Error']
    m4_p = m4_tidy.loc['is_uni:post', 'Pr(>|t|)']
    print(f"  is_uni:post = {m4_coef:.5f} (SE={m4_se:.5f}, p={m4_p:.4f})")
    print(f"  Interpretation: Uniswap (vs SushiSwap) has {'LOWER' if m4_coef < 0 else 'HIGHER'} daily death rate post-UNI")
    print(f"  N={len(stable):,} obs, pools={stable['pool_name'].nunique()}")


# 6. Placebo Test (M5)

did_placebo = did[did['rel_time'] <= UNI_DAY].copy()
did_placebo['fake_post'] = (did_placebo['start_time'] >= FAKE_DAY).astype(int)

m5 = pf.feols("event ~ is_full:fake_post | user_address + rel_time",
              data=did_placebo, vcov={'CRV1': 'user_address'})

m5_tidy = m5.tidy()
if 'is_full:fake_post' in m5_tidy.index:
    m5_est = m5_tidy.loc['is_full:fake_post', 'Estimate']
    m5_se = m5_tidy.loc['is_full:fake_post', 'Std. Error']
    m5_p = m5_tidy.loc['is_full:fake_post', 'Pr(>|t|)']
    print(f"  is_full:fake_post = {m5_est:.5f} "
          f"(SE={m5_se:.5f}, "
          f"p={m5_p:.4f})")
    print(f"  {'Placebo passes (insignificant)' if m5_p > 0.10 else 'Placebo FAILS (significant)'}")
    m5_placebo_pass = m5_p > 0.10
else:
    m5_est, m5_se, m5_p = np.nan, np.nan, np.nan
    m5_placebo_pass = False
    print(f"  is_full:fake_post coefficient not found")

# 7. CSV Output

#DiD Main Table CSV 
models_main = {'M1': m1, 'M2': m2, 'M3': m3, 'M5_Placebo': m5}
csv_rows = []
for model_name, model in models_main.items():
    tidy = model.tidy()
    for _, row in tidy.iterrows():
        stars = '***' if row['Pr(>|t|)'] < 0.01 else ('**' if row['Pr(>|t|)'] < 0.05 else ('*' if row['Pr(>|t|)'] < 0.10 else ''))
        csv_rows.append({
            'model': model_name,
            'coefficient': str(row.name),
            'estimate': round(row['Estimate'], 6),
            'std_error': round(row['Std. Error'], 6),
            't_value': round(row['t value'], 4),
            'p_value': round(row['Pr(>|t|)'], 6),
            'ci_95_lower': round(row['2.5%'], 6),
            'ci_95_upper': round(row['97.5%'], 6),
            'significance': stars,
        })
    # Model fit
    csv_rows.append({
        'model': model_name, 'coefficient': 'N_obs', 'estimate': float(model._N),
        'std_error': '', 't_value': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '', 'significance': '',
    })
    csv_rows.append({
        'model': model_name, 'coefficient': 'R2', 'estimate': round(float(model._r2), 6) if model._r2 else '',
        'std_error': '', 't_value': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '', 'significance': '',
    })

csv_main = pd.DataFrame(csv_rows)
csv_main.to_csv(os.path.join(TAB_DIR, 'Table_DiD_EventStudy.csv'), index=False)

# Event Study Coefficients CSV 
es_csv = es_terms[['day', 'Estimate', 'Std. Error', 't value', 'Pr(>|t|)', '2.5%', '97.5%']].copy()
es_csv.columns = ['day', 'estimate', 'std_error', 't_value', 'p_value', 'ci_95_lower', 'ci_95_upper']
es_csv.to_csv(os.path.join(TAB_DIR, 'Table_DiD_EventStudy_Coefficients.csv'), index=False)

# M4 Separate CSV 
m4_csv_rows = []
m4_tidy_full = m4.tidy()
for _, row in m4_tidy_full.iterrows():
    stars = '***' if row['Pr(>|t|)'] < 0.01 else ('**' if row['Pr(>|t|)'] < 0.05 else ('*' if row['Pr(>|t|)'] < 0.10 else ''))
    m4_csv_rows.append({
        'coefficient': str(row.name),
        'estimate': round(row['Estimate'], 6),
        'std_error': round(row['Std. Error'], 6),
        't_value': round(row['t value'], 4),
        'p_value': round(row['Pr(>|t|)'], 6),
        'ci_95_lower': round(row['2.5%'], 6),
        'ci_95_upper': round(row['97.5%'], 6),
        'significance': stars,
    })
m4_csv_rows.append({'coefficient': 'N_obs', 'estimate': float(m4._N), 'std_error': '', 't_value': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '', 'significance': ''})
m4_csv_rows.append({'coefficient': 'R2', 'estimate': round(float(m4._r2), 6) if m4._r2 else '', 'std_error': '', 't_value': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '', 'significance': ''})
pd.DataFrame(m4_csv_rows).to_csv(os.path.join(TAB_DIR, 'Table_DiD_CrossPlatform.csv'), index=False)


# 8. LaTeX Tables

# Main DiD LaTeX 
table_tex = pf.etable([m1, m2, m3, m5], signif_code=[0.01, 0.05, 0.10],
                       title="Difference-in-Differences — UNI Counter-Incentive Effect",
                       notes="Dependent variable: daily death indicator (linear probability model). "
                             f"Post = Day >= {UNI_DAY} (Sep 16, UNI announcement). "
                             "Standard errors clustered at user_address level in parentheses. "
                             "M3 (user + day FE) is the preferred specification. "
                             "M5: placebo test, fake event at Day 3, pre-period only (Day 0–7). "
                             "* p<0.10, ** p<0.05, *** p<0.01."
                       ).as_latex()
table_tex = table_tex.replace('<br>', ' ').replace('<br/>', ' ')
with open(os.path.join(TAB_DIR, 'Table_DiD_EventStudy.tex'), 'w') as f:
    f.write(table_tex)

# M4 Separate LaTeX 
m4_tex = pf.etable([m4], signif_code=[0.01, 0.05, 0.10],
                    title="Cross-Platform DiD — Stablecoin Pools (Uniswap vs SushiSwap)",
                    notes="Dependent variable: daily death indicator (LPM). "
                          "Sample restricted to stablecoin pools (DAI, USDC, USDT) only. "
                          "Treatment: Uniswap track (vs. SushiSwap track). "
                          f"Post = Day >= {UNI_DAY} (Sep 16, UNI announcement). "
                          "Standard errors clustered at user_address level. "
                          "User + day fixed effects included. "
                          "This is a structurally different design from the pool-level DiD (M1–M3): "
                          "it compares across platforms rather than across pool types. "
                          "* p<0.10, ** p<0.05, *** p<0.01."
                    ).as_latex()
m4_tex = m4_tex.replace('<br>', ' ').replace('<br/>', ' ')
with open(os.path.join(TAB_DIR, 'Table_DiD_CrossPlatform.tex'), 'w') as f:
    f.write(m4_tex)

#  Event Study Pre-Trends LaTeX 
es_pre = es_terms[es_terms['day'] <= UNI_DAY].copy()
# Create a simplified dataframe for etable output
es_pre_print = es_pre[['day', 'Estimate', 'Std. Error', 't value', 'Pr(>|t|)']].copy()
es_pre_print.columns = ['Day', 'Estimate', 'Std_Error', 't_value', 'p_value']
es_pre_tex = es_pre_print.to_latex(
    index=False, float_format="%.5f",
    caption=f"Event Study Pre-Trends (Reference = Day {REF_DAY})",
    label="tab:did_pretrends",
    position="htbp"
)
with open(os.path.join(TAB_DIR, 'Table_DiD_EventStudy_PreTrends.tex'), 'w') as f:
    f.write(es_pre_tex)


# 9. Event Study Figure


ref_row = pd.DataFrame({'day': [REF_DAY], 'Estimate': [0.0], 'Std. Error': [0.0]})
plot_data = pd.concat([es_terms[['day', 'Estimate', 'Std. Error']], ref_row], ignore_index=True)
plot_data = plot_data.sort_values('day')

# Filter to display window: all pre days + selected post days
# Show days -7 to UNI_DAY (pre), UNI_DAY to UNI_DAY+21 (early post), then bin tails
display_days_pre = list(range(0, UNI_DAY + 1))
display_days_post = list(range(UNI_DAY, min(UNI_DAY + 22, int(plot_data['day'].max()) + 1)))

# For post-day 22+, bin in groups
far_post_days = [d for d in plot_data['day'].unique() if d > max(display_days_post)]
if far_post_days:
    bin_edges = [max(display_days_post) + 1]
    while bin_edges[-1] + 30 < max(far_post_days):
        bin_edges.append(bin_edges[-1] + 30)
    bin_edges.append(max(far_post_days) + 1)

# Keep all available days for now, mark post period
plot_data['is_post'] = plot_data['day'] >= UNI_DAY
plot_data['ci_lower'] = plot_data['Estimate'] - 1.96 * plot_data['Std. Error']
plot_data['ci_upper'] = plot_data['Estimate'] + 1.96 * plot_data['Std. Error']

fig, ax = plt.subplots(figsize=(12, 7))

# Pre/post shading
ymin = plot_data['ci_lower'].min() - 0.005
ymax = plot_data['ci_upper'].max() + 0.005
ax.axvspan(UNI_DAY, plot_data['day'].max() + 1, alpha=0.06, color='#D55E00', label='Post-UNI')
ax.axvspan(plot_data['day'].min(), UNI_DAY, alpha=0.04, color='#0072B2', label='Pre-UNI')

# UNI announcement line
ax.axvline(x=UNI_DAY, color='#D55E00', linestyle='--', linewidth=1.5, zorder=3)
ax.text(UNI_DAY + 0.3, ymax * 0.95, f'UNI Airdrop\n(Day {UNI_DAY}, Sep 16)',
        color='#D55E00', fontsize=9, fontweight='bold', va='top')


ax.axhline(y=0, color='black', linewidth=0.6, linestyle='-', zorder=1)

ax.fill_between(plot_data['day'], plot_data['ci_lower'], plot_data['ci_upper'],
                alpha=0.2, color='#555555', label='95% CI')


colors = ['#0072B2' if d < UNI_DAY else '#D55E00' for d in plot_data['day']]
ax.scatter(plot_data['day'], plot_data['Estimate'], c=colors, s=18, zorder=4)

# Reference day marker
ax.scatter([REF_DAY], [0], c='#009E73', s=50, marker='s', zorder=5, label=f'Reference (Day {REF_DAY})')


for _, row in plot_data.iterrows():
    if row['day'] in display_days_pre + display_days_post[:15]:
        ax.plot([row['day'], row['day']], [row['ci_lower'], row['ci_upper']],
                color='#555555', linewidth=0.8, alpha=0.6)


ax.set_xlabel('Days Since Migration (Sep 9, 2020)', fontsize=11)
ax.set_ylabel('Coefficient: Daily Exit Probability (LPM)', fontsize=11)


legend_elements = [
    mpatches.Patch(facecolor='#0072B2', alpha=0.3, label='Pre-UNI (Days 0-6)'),
    mpatches.Patch(facecolor='#D55E00', alpha=0.3, label='Post-UNI (Days 7+)'),
    plt.Line2D([0], [0], color='#555555', alpha=0.3, linewidth=3, label='95% CI'),
    plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='#009E73', markersize=8, label=f'Ref (Day {REF_DAY})'),
]
ax.legend(handles=legend_elements, loc='upper right', fontsize=8, framealpha=0.9)


ax.annotate(f'Pre-trends F-test: $\\chi^2$({pre_df})={pre_chi2:.1f}, p={pre_p:.3f}',
            xy=(0.02, 0.80), xycoords='axes fraction', fontsize=8,
            ha='left', va='top',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.85))

ax.annotate(f'DiD Coef: {did_coef:.4f} (SE={did_se:.4f})',
            xy=(0.98, 0.80), xycoords='axes fraction', fontsize=8,
            ha='right', va='top',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.85))

n_note = (f'Reference = Day {REF_DAY}. {n_obs:,} daily obs, {n_users:,} users, '
          f'SE clustered at user level.')
ax.text(0.98, 0.03, n_note, transform=ax.transAxes, fontsize=7,
        ha='right', va='bottom', style='italic', color='#555555')

plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'Fig_DiD_EventStudy_Coefficients.png'), dpi=300, bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'Fig_DiD_EventStudy_Coefficients.pdf'), bbox_inches='tight')
plt.close()

